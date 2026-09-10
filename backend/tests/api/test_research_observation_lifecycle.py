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


@pytest.mark.parametrize("cause", ["revoke", "close"])
@pytest.mark.parametrize("operation", ["delete", "quarantine", "replay"])
def test_api_recorded_cause_sets_exact_tombstone_boundary(api, monkeypatch, cause, operation):
    from app.services import research_context
    from tests.services.test_research_observation_lifecycle import assert_clock
    root, ctx, oid, clock = api
    monkeypatch.setattr(research_context, "_now", lambda value: clock["now"])
    legacy = snapshot(legacy=True)
    first = NOW+timedelta(days=1)
    clock["now"] = first
    if cause == "close":
        response = client.post(root.removesuffix("/observations")+"/close",
                               json={"expected_version": 1, "closure_reference": REF})
    else:
        response = client.post(root+"/preparations/preparation_1/revoke", json=REVIEW)
    assert response.status_code == 200
    clock["now"] = NOW+timedelta(days=2)
    response = (post(root, "reconcile", deleted_observation_ids=[oid]) if operation == "replay"
                else post(root, operation, oid))
    assert response.status_code == 200
    clock["now"] = NOW+timedelta(days=3)
    for _ in range(2):
        response = post(root, "reconcile")
        assert response.status_code == 200
        assert_clock(oid, first)
    clock["now"] = first+timedelta(days=90)-timedelta(microseconds=1)
    assert post(root, "reconcile").json()["removed_tombstones"] == 0
    clock["now"] += timedelta(microseconds=1)
    assert post(root, "reconcile").json()["removed_tombstones"] == 1
    assert post(root, "reconcile").json()["removed_tombstones"] == 0
    assert client.get(root+f"/{oid}").status_code == 409
    assert snapshot(legacy=True) == legacy


@pytest.mark.parametrize("explicit_release", [True, False])
def test_api_closed_context_preserves_real_hold_end(api, monkeypatch, explicit_release):
    from app.services import research_context
    from tests.services.test_research_observation_lifecycle import assert_clock
    root, ctx, oid, clock = api
    monkeypatch.setattr(research_context, "_now", lambda value: clock["now"])
    assert post(root, "hold", oid, reason="synthetic_review", until=(NOW+timedelta(days=4)).isoformat()).status_code == 200
    clock["now"] = NOW+timedelta(days=1)
    assert client.post(root.removesuffix("/observations")+"/close",
                       json={"expected_version": 1, "closure_reference": REF}).status_code == 200
    clock["now"] = NOW+timedelta(days=2)
    assert post(root, "delete", oid).status_code == 200
    assert post(root, "reconcile").json()["purged_payloads"] == 0
    end = NOW+timedelta(days=3 if explicit_release else 4)
    clock["now"] = end
    if explicit_release:
        assert post(root, "release", oid).status_code == 200
    assert post(root, "reconcile").json()["purged_payloads"] == 1
    for _ in range(2):
        assert post(root, "reconcile").json()["removed_tombstones"] == 0
        assert_clock(oid, end)
    clock["now"] = end+timedelta(days=90)
    assert post(root, "reconcile").json()["removed_tombstones"] == 1


@pytest.mark.parametrize("failure", ["audit", "serialization"])
def test_api_failed_reconciliation_rolls_back_earlier_clock(api, monkeypatch, failure):
    from app.api.routes import research_observations as routes
    from app.db.models.research_observation import ObservationRecord
    from app.db.session import SessionLocal
    root, ctx, oid, clock = api
    clock["now"] = NOW+timedelta(days=1)
    assert client.post(root+"/preparations/preparation_1/revoke", json=REVIEW).status_code == 200
    clock["now"] = NOW+timedelta(days=2)
    assert post(root, "delete", oid).status_code == 200
    with SessionLocal() as db:
        db.get(ObservationRecord, oid).unavailable_at = clock["now"]  # Reviewed no-hold state.
        db.commit()
    before = snapshot()
    clock["now"] = NOW+timedelta(days=3)
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
        response = post(root, "reconcile")
    finally:
        if failure == "audit":
            event.remove(engine, "before_cursor_execute", fail_audit)
    assert response.status_code == 500
    assert response.json() == {"status": "rejected", "code": "observation_failed"}
    assert snapshot() == before
