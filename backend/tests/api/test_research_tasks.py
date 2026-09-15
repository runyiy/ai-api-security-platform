from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.routes import research_tasks as route
from app.services import research_verification_clock as clock
from tests.research_task_fixtures import (task_graph, intent_graph, subject_pair, two_intake_targets,
    original_targets, zero_capabilities, creation, decision, transition, NOW)
from tests.research_intake_fixtures import snapshot


def url(g, suffix):
    return f"/api/research-projects/{g['project']}/contexts/{g['ctx']}/tasks/{suffix}"


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(clock, 'utcnow', lambda: NOW)
    with TestClient(app) as client:
        yield client


def test_local_api_workflow_and_no_start(api, task_graph):
    g = task_graph
    response = api.post(url(g, 'versions/1'), json=creation(g))
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    value = response.json()
    response = api.post(url(g, 'read'), json=value['reference'])
    assert response.status_code == 200 and response.json()['core'] == value['core']
    response = api.post(url(g, 'budget-decisions'), json=decision(value))
    assert response.status_code == 200 and response.json()['held_requests'] == 2
    approved = response.json()
    before = snapshot()
    response = api.post(url(g, 'transitions'), json=transition(approved, 'start'))
    assert response.status_code == 409 and response.json()['code'] == 'task_execution_disabled'
    assert snapshot() == before
    response = api.post(url(g, 'budget-decisions'), json=decision(approved, 'revoked'))
    assert response.status_code == 200 and not response.json()['budget_current']
    assert response.json()['held_requests'] == 2
    assert api.post(url(g, 'budget-decisions'), json=decision(approved)).status_code == 409


@pytest.mark.parametrize('raw,status', [(b'{}', 422), (b'{"previous":null,"previous":null}', 422),
    (b'\xff', 422), (b'"'+b'x'*32768+b'"', 413)])
def test_bounded_invalid_transport_is_sanitized(api, task_graph, raw, status):
    before = snapshot()
    response = api.post(url(task_graph, 'versions/1'), content=raw, headers={'content-type': 'application/json'})
    assert response.status_code == status and response.headers['cache-control'] == 'no-store'
    assert len(response.content) < 256 and snapshot() == before


@pytest.mark.parametrize('change', ['query', 'unknown', 'project', 'media', 'encoding'])
def test_closed_input_and_project_boundaries(api, task_graph, change):
    g = task_graph
    p, address = creation(g), url(g, 'versions/1')
    headers = {}
    if change == 'query': address += '?start=true'
    if change == 'unknown': p['approval_reference'] = 'synthetic-sensitive-error'
    if change == 'project': address = address.replace('/research-projects/1/', '/research-projects/2/')
    if change == 'media': headers['content-type'] = 'text/plain'
    if change == 'encoding': headers['content-encoding'] = 'gzip'
    before = snapshot()
    response = api.post(address, json=p, headers=headers)
    assert response.status_code in (409, 415, 422)
    assert 'synthetic-sensitive-error' not in response.text and snapshot() == before


def test_complete_api_encoding_precedes_approval_commit(api, task_graph, monkeypatch):
    g = task_graph
    value = api.post(url(g, 'versions/1'), json=creation(g)).json()
    before = snapshot()
    monkeypatch.setattr(route, 'encoded', Mock(side_effect=RuntimeError('synthetic-sensitive-error')))
    response = api.post(url(g, 'budget-decisions'), json=decision(value))
    assert response.status_code == 409 and response.json()['code'] == 'task_unavailable'
    assert snapshot() == before
