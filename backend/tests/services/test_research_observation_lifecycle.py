"""RA-02/W2 review blockers: stable clocks, disqualification and audit recovery."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.db.models.research_observation import ObservationEvent, ObservationPayload, ObservationRecord
from app.db.session import SessionLocal
from app.schemas.research_observation import ObservationError
from app.services import research_observation as service
from tests.research_observation_fixtures import (
    observation_context, intake_target, zero_capabilities, call, NOW, REF,
)  # noqa: F401
from tests.research_intake_fixtures import snapshot
from tests.services.test_research_observation import accepted

REVIEW = {"review": REF}


def maintain(ctx, *, action="reconcile", now=NOW, **kwargs):
    return call(service.maintain, ctx, {"action": action, "review": REF, **kwargs}, now=now)


def hold(ctx, oid, *, now=NOW, end=None):
    return call(service.lifecycle, ctx, oid, "hold", {"review": REF, "reason": "synthetic_review",
        "until": (end or NOW+timedelta(days=2)).isoformat()}, now=now)


def event_rows(ctx):
    with SessionLocal() as db:
        return list(db.execute(select(ObservationEvent.__table__).where(
            ObservationEvent.context_id == ctx).order_by(ObservationEvent.id)).mappings())


def fill_audit(ctx, oid):
    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(ObservationEvent).where(ObservationEvent.context_id == ctx))
        db.execute(ObservationEvent.__table__.insert(), [dict(context_id=ctx, observation_id=oid,
            code="read", recorded_at=NOW, review=None) for _ in range(8192-count)])
        db.commit()
    assert len(event_rows(ctx)) == 8192


def test_release_without_hold_cannot_move_deleted_tombstone_deadline(observation_context):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    call(service.lifecycle, ctx, oid, "delete", REVIEW)
    assert maintain(ctx)["purged_payloads"] == 1
    before = snapshot()
    with pytest.raises(ObservationError, match="observation_hold_not_active"):
        call(service.lifecycle, ctx, oid, "release", REVIEW, now=NOW+timedelta(days=89))
    assert snapshot() == before
    assert maintain(ctx, now=NOW+timedelta(days=90))["removed_tombstones"] == 1


def test_revoked_preparation_overrides_deleted_hold(observation_context):
    ctx, _ = observation_context
    receipt = accepted(ctx)
    oid = receipt["observation_id"]
    hold(ctx, oid)
    call(service.lifecycle, ctx, oid, "delete", REVIEW)
    call(service.revoke_preparation, ctx, "preparation_1", REVIEW)
    result = maintain(ctx)
    assert result["purged_payloads"] == 1
    with SessionLocal() as db:
        row = db.get(ObservationRecord, oid)
        assert row.digest == receipt["digest"] and row.hold_until is None
        assert db.get(ObservationPayload, oid) is None


def test_explicit_audit_rotation_suspends_and_recovers_at_capacity(observation_context):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    fill_audit(ctx, oid)
    original = event_rows(ctx)
    legacy = snapshot(legacy=True)
    result = maintain(ctx, action="rotate_audit")
    assert result["status"] == "unavailable" and result["retired_audit_events"] == 1024
    rows = event_rows(ctx)
    assert rows[:-1] == original[1024:]
    assert rows[-1]["code"] == "audit_rotated_1024" and rows[-1]["review"] == REF
    assert len(rows) == 7169
    with pytest.raises(ObservationError, match="observation_recovery_required"):
        accepted(ctx)
    call(service.lifecycle, ctx, oid, "delete", REVIEW)
    call(service.lifecycle, ctx, oid, "quarantine", REVIEW)
    maintain(ctx, action="suspend")
    assert maintain(ctx)["purged_payloads"] == 1
    assert snapshot(legacy=True) == legacy


@pytest.mark.parametrize("operation", ["delete", "quarantine", "replay"])
def test_late_action_cannot_restart_already_expired_clock(observation_context, operation):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    expiry = NOW+timedelta(days=30)
    at = expiry+timedelta(days=89)
    if operation == "replay":
        maintain(ctx, now=at, deleted_observation_ids=[oid])
    else:
        call(service.lifecycle, ctx, oid, operation, REVIEW, now=at)
        maintain(ctx, now=at)
    with SessionLocal() as db:
        assert db.get(ObservationRecord, oid).unavailable_at == expiry
    before = snapshot()
    with pytest.raises(ObservationError, match="hold_not_active"):
        call(service.lifecycle, ctx, oid, "release", REVIEW, now=at)
    assert snapshot() == before
    assert maintain(ctx, now=expiry+timedelta(days=90))["removed_tombstones"] == 1


@pytest.mark.parametrize("explicit_release", [True, False])
@pytest.mark.parametrize("deleted", [True, False])
def test_only_real_hold_release_or_end_defers_clock(observation_context, explicit_release, deleted):
    from tests.research_observation_fixtures import preparation, observation
    from app.schemas.research_observation import canonical
    ctx, ids = observation_context
    p = preparation(ids)
    p.update(preparation_ref="preparation_2", retention_seconds=10, corrects_preparation="preparation_1")
    call(service.prepare, ctx, p)
    v = observation()
    v["preparation_ref"] = "preparation_2"
    receipt = call(service.accept, ctx, "preparation_2", canonical(v))
    oid = receipt["observation_id"]
    hold(ctx, oid)
    if deleted:
        call(service.lifecycle, ctx, oid, "delete", REVIEW)
    assert maintain(ctx, now=NOW+timedelta(hours=12))["purged_payloads"] == 0
    end = NOW+timedelta(days=1 if explicit_release else 2)
    if explicit_release:
        call(service.lifecycle, ctx, oid, "release", REVIEW, now=end)
    assert maintain(ctx, now=end)["purged_payloads"] == 1
    with SessionLocal() as db:
        row = db.get(ObservationRecord, oid)
        assert row.unavailable_at == end and row.digest == receipt["digest"]
    for at in (end, end+timedelta(days=89)):
        before = snapshot()
        with pytest.raises(ObservationError, match="hold_not_active"):
            call(service.lifecycle, ctx, oid, "release", REVIEW, now=at)
        assert snapshot() == before
    assert maintain(ctx, now=end+timedelta(days=90))["removed_tombstones"] == 1


def test_release_before_expiry_keeps_original_retention(observation_context):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    hold(ctx, oid)
    call(service.lifecycle, ctx, oid, "release", REVIEW, now=NOW+timedelta(days=1))
    with SessionLocal() as db:
        row = db.get(ObservationRecord, oid)
        assert row.unavailable_at is None and row.expires_at == NOW+timedelta(days=30)
    assert maintain(ctx, now=NOW+timedelta(days=120))["removed_tombstones"] == 1


@pytest.mark.parametrize("state", ["held", "deleted", "quarantined", "expired"])
def test_revocation_cleans_every_previous_state_without_postponement(observation_context, state):
    ctx, _ = observation_context
    receipt = accepted(ctx)
    oid = receipt["observation_id"]
    at = NOW+timedelta(days=1)
    if state != "expired":
        hold(ctx, oid)
    else:
        at = NOW+timedelta(days=40)
    if state in ("deleted", "quarantined"):
        call(service.lifecycle, ctx, oid, "delete" if state == "deleted" else "quarantine", REVIEW)
    call(service.revoke_preparation, ctx, "preparation_1", REVIEW, now=at)
    assert maintain(ctx, now=at)["purged_payloads"] == 1
    with SessionLocal() as db:
        row = db.get(ObservationRecord, oid)
        assert row.state == "quarantined" and row.hold_until is None
        assert row.digest == receipt["digest"] and row.entry_order == receipt["entry_order"]
        start = row.unavailable_at
        assert start == (NOW if state == "quarantined" else NOW+timedelta(days=30) if state == "expired" else at)
    assert maintain(ctx, now=start+timedelta(days=90))["removed_tombstones"] == 1


def test_still_qualified_deleted_hold_is_preserved(observation_context):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    hold(ctx, oid)
    call(service.lifecycle, ctx, oid, "delete", REVIEW)
    assert maintain(ctx, now=NOW+timedelta(days=1))["purged_payloads"] == 0
    with SessionLocal() as db:
        assert db.get(ObservationPayload, oid) is not None
        assert db.get(ObservationRecord, oid).hold_until == NOW+timedelta(days=2)


@pytest.mark.parametrize("operation", ["delete", "quarantine", "suspend", "reconcile"])
def test_saturation_denies_without_audit_until_explicit_rotation(observation_context, operation):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    fill_audit(ctx, oid)
    before = snapshot()
    with pytest.raises(ObservationError, match="capacity_exceeded"):
        if operation in ("delete", "quarantine"):
            call(service.lifecycle, ctx, oid, operation, REVIEW)
        else:
            maintain(ctx, action=operation)
    assert snapshot() == before
    maintain(ctx, action="rotate_audit")
    if operation in ("delete", "quarantine"):
        call(service.lifecycle, ctx, oid, operation, REVIEW)
    else:
        maintain(ctx, action=operation)
    assert len(event_rows(ctx)) == 7170


def test_rotation_bounds_project_scope_and_repeated_request(observation_context):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    before = snapshot()
    with pytest.raises(ObservationError, match="rotation_not_required"):
        maintain(ctx, action="rotate_audit")
    assert snapshot() == before
    fill_audit(ctx, oid)
    before = snapshot()
    with pytest.raises(ObservationError, match="context_unavailable"):
        call(service.maintain, ctx, {"action": "rotate_audit", "review": REF}, project=2)
    assert snapshot() == before
    with pytest.raises(ObservationError):
        maintain(ctx, action="rotate_audit", deleted_observation_ids=[oid])
    assert snapshot() == before
    maintain(ctx, action="rotate_audit")
    before = snapshot()
    with pytest.raises(ObservationError, match="rotation_not_required"):
        maintain(ctx, action="rotate_audit")
    assert snapshot() == before


@pytest.mark.parametrize("operation", ["rotate_audit", "reconcile", "delete", "release"])
def test_real_database_audit_error_rolls_back_even_caller_commits(observation_context, operation):
    from sqlalchemy import event
    from psycopg.errors import DivisionByZero
    from app.db.session import engine
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    if operation == "rotate_audit":
        fill_audit(ctx, oid)
    elif operation == "reconcile":
        call(service.lifecycle, ctx, oid, "delete", REVIEW)
    elif operation == "release":
        hold(ctx, oid)
        call(service.lifecycle, ctx, oid, "delete", REVIEW)
    before = snapshot()
    def fail(conn, cursor, statement, *args):
        if statement.startswith("INSERT INTO research_observation_events"):
            cursor.execute("SELECT 1/0")  # PostgreSQL aborts the audit write's transaction.
    event.listen(engine, "before_cursor_execute", fail)
    try:
        with SessionLocal() as db:
            with pytest.raises(DivisionByZero):
                if operation in ("rotate_audit", "reconcile"):
                    service.maintain(db, 1, ctx, {"action": operation, "review": REF}, now=NOW)
                else:
                    service.lifecycle(db, 1, ctx, oid, operation, REVIEW, now=NOW)
            db.commit()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert snapshot() == before


@pytest.mark.parametrize("first,second", [
    ("rotate_audit", "rotate_audit"), ("rotate_audit", "delete"), ("delete", "rotate_audit"),
    ("revoke", "reconcile"), ("reconcile", "revoke"),
    ("release", "reconcile"), ("reconcile", "release"),
])
def test_real_concurrent_lifecycle_and_maintenance(observation_context, first, second):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import text
    from tests.services.test_research_context_isolation import wait_for_blocker
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    saturation = "rotate_audit" in (first, second)
    if saturation:
        fill_audit(ctx, oid)
    else:
        hold(ctx, oid)
        call(service.lifecycle, ctx, oid, "delete", REVIEW)
    legacy = snapshot(legacy=True)
    ready, release, waiting = Event(), Event(), Event()
    pids = {}
    at = NOW if saturation else NOW+timedelta(days=1)
    def worker(name, leader):
        with SessionLocal() as db:
            pids[leader] = db.scalar(text("SELECT pg_backend_pid()"))
            if not leader:
                waiting.set()
            try:
                if name in ("rotate_audit", "reconcile"):
                    result = service.maintain(db, 1, ctx, {"action": name, "review": REF}, now=at)
                elif name == "revoke":
                    result = service.revoke_preparation(db, 1, ctx, "preparation_1", REVIEW, now=at)
                else:
                    result = service.lifecycle(db, 1, ctx, oid, name, REVIEW, now=at)
            except ObservationError as exc:
                result = exc.code
            if leader:
                ready.set()
                assert release.wait(10)
            db.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(worker, first, True)
        assert ready.wait(10)
        b = pool.submit(worker, second, False)
        assert waiting.wait(10)
        try:
            wait_for_blocker(pids[False], pids[True])
        finally:
            release.set()
        ra, rb = a.result(15), b.result(15)
    if first == second == "rotate_audit":
        assert ra["retired_audit_events"] == 1024 and rb == "observation_rotation_not_required"
        assert len(event_rows(ctx)) == 7169
    elif saturation:
        if first == "delete":
            assert ra == "observation_capacity_exceeded" and rb["retired_audit_events"] == 1024
        else:
            assert ra["retired_audit_events"] == 1024 and rb["availability"] == "deleted"
    else:
        maintain(ctx, now=at)
        with SessionLocal() as db:
            assert db.get(ObservationPayload, oid) is None
            assert db.get(ObservationRecord, oid).unavailable_at == at
    assert snapshot(legacy=True) == legacy


@pytest.mark.parametrize("revoked_first", [True, False])
@pytest.mark.parametrize("held", [True, False])
def test_closed_context_and_revocation_keep_earliest_known_deadline(observation_context, revoked_first, held):
    from sqlalchemy import event
    from app.db.session import engine
    from app.services.research_context import close_context
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    if held:
        hold(ctx, oid, end=NOW+timedelta(days=5))
    close_at = NOW+timedelta(days=2 if revoked_first else 1)
    revoked_at = NOW+timedelta(days=1 if revoked_first else 2)
    operations = [(close_at, close_context, {"expected_version": 1, "closure_reference": REF}),
                  (revoked_at, service.revoke_preparation, REVIEW)]
    for at, fn, payload in sorted(operations, key=lambda item: item[0]):
        if fn is close_context:
            call(fn, ctx, payload, now=at)
        else:
            call(fn, ctx, "preparation_1", payload, now=at)
    statements = []
    def capture(conn, cursor, statement, *args):
        statements.append(statement.lower())
    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert maintain(ctx, now=NOW+timedelta(days=3))["purged_payloads"] == 1
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not any("from targets" in s or "from scopes" in s or "from authorization_revisions" in s for s in statements)
    with SessionLocal() as db:
        assert db.get(ObservationRecord, oid).unavailable_at == (revoked_at if held else min(close_at, revoked_at))
