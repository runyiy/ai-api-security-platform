from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.routes import research_knowledge as route
from app.services import research_observation as observation, research_knowledge as knowledge
from app.schemas import research_knowledge as s
from tests.research_rule_fixtures import (  # noqa: F401
    rule_pair, knowledge_pair, subject_pair, two_intake_targets, rule, validate, feedback,
    NOW, REF, zero_capabilities,
)
from tests.research_intake_fixtures import snapshot

client = TestClient(app)


@pytest.fixture
def api(rule_pair, monkeypatch):
    monkeypatch.setattr(observation, '_time', lambda _: NOW)
    g, other = rule_pair
    return f"/api/research-projects/{g['project']}/contexts/{g['ctx']}/knowledge", g, other


def test_operator_validation_feedback_and_human_review(api):
    root, g, _ = api
    ref = rule(g)
    result = client.post(root+'/validations', json={'reference': ref, 'valid_until': (NOW+timedelta(hours=1)).isoformat()})
    assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
    proof = result.json()['validation_ref']
    result = client.post(root+'/validations/read', json={'reference': ref, 'validation_ref': proof})
    assert result.status_code == 200 and result.json()['evidence']['report']['passed']
    result = client.post(root+'/feedback', json={'reference': ref, 'validation_ref': proof,
        'proposal': 'promotion', 'reason': 'validation_passed', 'correction': None, 'review': REF})
    assert result.status_code == 200 and result.json()['status'] == 'pending'
    fid = result.json()['feedback_id']
    result = client.post(root+'/feedback/read', json={'reference': ref, 'feedback_id': fid})
    assert result.status_code == 200 and result.json()['eligible_for_review']
    assert result.json()['proposal']['validation_ref'] == proof
    assert result.json()['proposal']['reason'] == 'validation_passed'
    result = client.post(root+'/feedback/reviews', json={'reference': ref, 'feedback_id': fid,
        'expected_sequence': 0, 'decision': 'accept', 'review': REF})
    assert result.status_code == 200 and result.json()['status'] == 'accept'
    result = client.post(root+'/feedback/read', json={'reference': ref, 'feedback_id': fid})
    assert result.status_code == 200 and not result.json()['eligible_for_review']
    assert result.json()['review']['actor'] == 'local_operator'
    from tests.research_knowledge_fixtures import query
    result = client.post(root+'/query', json=query())
    assert result.status_code == 200 and result.json()['matches'] == []


@pytest.mark.parametrize('failure', ['audit', 'encoding', 'output_limit'])
@pytest.mark.parametrize('operation', ['validation', 'feedback', 'review'])
def test_all_new_writes_rollback_on_audit_or_serialization_failure(api, monkeypatch, failure, operation):
    root, g, _ = api
    ref = rule(g)
    payload = {'reference': ref, 'valid_until': (NOW+timedelta(hours=1)).isoformat()}
    path = '/validations'
    if operation != 'validation':
        proof = validate(g, ref)['validation_ref']
        payload = {'reference': ref, 'validation_ref': proof, 'proposal': 'promotion',
            'reason': 'validation_passed', 'correction': None, 'review': REF}
        path = '/feedback'
        if operation == 'review':
            fid = feedback(g, ref, proof)['feedback_id']
            payload = {'reference': ref, 'feedback_id': fid, 'expected_sequence': 0, 'decision': 'accept', 'review': REF}
            path = '/feedback/reviews'
    if failure == 'audit':
        def fail(*a, **k):raise RuntimeError('test audit failure')
        monkeypatch.setattr(knowledge, '_audit', fail)
    elif failure == 'encoding':
        def fail(value):raise ValueError('test encoding failure')
        monkeypatch.setattr(route, 'encoded', fail)
    else:monkeypatch.setattr(s, 'MAX_RESPONSE', 1)
    before = snapshot()
    result = client.post(root+path, json=payload)
    assert result.status_code == 500 and snapshot() == before


@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_api_encoding_checks_exact_validation_expiry(api, monkeypatch, offset):
    root, g, _ = api
    ref = rule(g)
    end = NOW+timedelta(seconds=1)
    clock = [NOW]
    monkeypatch.setattr(observation, '_time', lambda _: clock[0])
    encoded = route.encoded
    def advancing(value):
        clock[0] = end+timedelta(microseconds=offset)
        return encoded(value)
    monkeypatch.setattr(route, 'encoded', advancing)
    before = snapshot()
    result = client.post(root+'/validations', json={'reference': ref, 'valid_until': end.isoformat()})
    assert result.status_code == (200 if offset < 0 else 409)
    if offset >= 0:assert snapshot() == before


@pytest.mark.parametrize('value', [True, 'NOT_RUN', 'synthetic_test_only', {'passed': True}])
def test_caller_cannot_supply_pass_or_placeholder(api, value):
    root, g, _ = api
    ref = rule(g)
    before = snapshot()
    result = client.post(root+'/validations', json={'reference': ref,
        'valid_until': (NOW+timedelta(hours=1)).isoformat(), 'result': value})
    assert result.status_code == 422 and snapshot() == before


def test_foreign_and_missing_validation_refs_are_indistinguishable(api):
    root, g, other = api
    ref = rule(g)
    foreign = validate(other, rule(other))['validation_ref']
    values = []
    for proof in (foreign, {**foreign, 'validation_id': 2147483647}):
        result = client.post(root+'/validations/read', json={'reference': ref, 'validation_ref': proof})
        values.append((result.status_code, result.json()))
    assert values[0] == values[1] == (409, {'status': 'rejected', 'code': 'knowledge_validation_unavailable'})


@pytest.mark.parametrize('action', ['review', 'publish'])
def test_human_decision_encoding_cannot_cross_proof_expiry(api, monkeypatch, action):
    from tests.research_knowledge_fixtures import call
    root, g, _ = api
    ref = rule(g)
    end = NOW+timedelta(seconds=1)
    proof = validate(g, ref, end)['validation_ref']
    payload = {'reference': ref, 'expected_sequence': 0, 'action': action, 'review': REF,
        'valid_from': NOW.isoformat(), 'valid_until': (NOW+timedelta(hours=1)).isoformat(), 'validation_ref': proof}
    if action == 'publish':
        r = call(knowledge.decide, g['ctx'], {**payload, 'action': 'review'})
        payload.update(expected_sequence=1, review_event_id=r['event_id'])
    clock = [NOW]
    monkeypatch.setattr(observation, '_time', lambda _: clock[0])
    encoded = route.encoded
    def advancing(value):
        clock[0] = end
        return encoded(value)
    monkeypatch.setattr(route, 'encoded', advancing)
    before = snapshot()
    result = client.post(root+'/decisions', json=payload)
    assert result.status_code == 409 and snapshot() == before
