"""Operator API regressions for the three W2 lifecycle review blockers."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.main import app
from app.db.session import engine
from app.services import research_observation as service
from tests.research_observation_fixtures import (
    observation_context, intake_target, zero_capabilities, call, NOW, REF,
)  # noqa: F401
from tests.research_intake_fixtures import snapshot
from tests.services.test_research_observation import accepted
from tests.services.test_research_observation_lifecycle import fill_audit, event_rows

client = TestClient(app)
REVIEW = {"review": REF}


@pytest.fixture
def api(observation_context, monkeypatch):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    clock = {"now": NOW}
    monkeypatch.setattr(service, "_time", lambda value: clock["now"])
    yield f"/api/research-projects/1/contexts/{ctx}/observations", ctx, oid, clock


def post(root, operation, oid=None, **kw):
    return client.post(root+(f"/{oid}/{operation}" if oid else "/maintenance"),
                       json={"review": REF, **({} if oid else {"action": operation}), **kw})


def test_api_release_cannot_extend_deleted_history(api):
    root, ctx, oid, clock = api
    assert post(root, "delete", oid).status_code == 200
    assert post(root, "reconcile").json()["purged_payloads"] == 1
    clock["now"] += timedelta(days=89)
    before = snapshot()
    for _ in range(2):
        response = post(root, "release", oid)
        assert response.status_code == 409
        assert response.json() == {"status": "rejected", "code": "observation_hold_not_active"}
    assert snapshot() == before
    clock["now"] += timedelta(days=1)
    assert post(root, "reconcile").json()["removed_tombstones"] == 1


def test_api_revocation_overrides_deleted_hold(api):
    root, ctx, oid, clock = api
    assert post(root, "hold", oid, reason="synthetic_review", until=(NOW+timedelta(days=2)).isoformat()).status_code == 200
    assert post(root, "delete", oid).status_code == 200
    assert client.post(root+"/preparations/preparation_1/revoke", json=REVIEW).status_code == 200
    result = post(root, "reconcile")
    assert result.status_code == 200 and result.json()["purged_payloads"] == 1
    read = client.get(root+f"/{oid}").json()
    assert read["availability"] == "quarantined" and "payload" not in read


def test_api_capacity_rotation_is_audited_suspension_before_cleanup(api):
    root, ctx, oid, clock = api
    fill_audit(ctx, oid)
    before = snapshot(legacy=True)
    assert post(root, "delete", oid).status_code == 409
    response = post(root, "rotate_audit")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["status"] == "unavailable" and response.json()["retired_audit_events"] == 1024
    assert event_rows(ctx)[-1]["code"] == "audit_rotated_1024"
    read = client.get(root+f"/{oid}").json()
    assert "payload" not in read
    assert post(root, "delete", oid).status_code == 200
    assert post(root, "quarantine", oid).status_code == 200
    assert post(root, "suspend").status_code == 200
    assert post(root, "reconcile").json()["purged_payloads"] == 1
    assert len(event_rows(ctx)) <= 8192
    assert snapshot(legacy=True) == before


@pytest.mark.parametrize("failure", ["audit", "serialization"])
@pytest.mark.parametrize("operation", ["rotate_audit", "reconcile", "release"])
def test_api_failure_never_commits_rotation_cleanup_or_release(api, monkeypatch, failure, operation):
    from app.api.routes import research_observations as routes
    root, ctx, oid, clock = api
    if operation == "rotate_audit":
        fill_audit(ctx, oid)
    elif operation == "release":
        assert post(root, "hold", oid, reason="synthetic_review", until=(NOW+timedelta(days=2)).isoformat()).status_code == 200
        assert post(root, "delete", oid).status_code == 200
    else:
        assert post(root, "delete", oid).status_code == 200
    before = snapshot()
    def fail_audit(conn, cursor, statement, *args):
        if statement.startswith("INSERT INTO research_observation_events"):
            cursor.execute("SELECT 1/0")
    def fail_response(value):
        raise RuntimeError("synthetic failure must not be echoed")
    if failure == "audit":
        event.listen(engine, "before_cursor_execute", fail_audit)
    else:
        monkeypatch.setattr(routes, "encoded", fail_response)
    try:
        response = post(root, operation, oid if operation == "release" else None)
    finally:
        if failure == "audit":
            event.remove(engine, "before_cursor_execute", fail_audit)
    assert response.status_code == 500 and len(response.content) <= 256
    assert response.json() == {"status": "rejected", "code": "observation_failed"}
    assert snapshot() == before
