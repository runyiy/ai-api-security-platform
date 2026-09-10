from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
import copy
import hashlib
import pytest
from sqlalchemy import event, func, select, text, update

from app.db.models import Target, Scope, AuthorizationRevision
from app.db.models.research_observation import ObservationControl, ObservationPreparation, ObservationRecord, ObservationPayload, ObservationEvent
from app.db.session import SessionLocal, engine
from app.services import research_observation as service
from app.services.research_context import close_context, correct_context, create_context
from app.schemas.research_observation import ObservationError, canonical
from tests.research_observation_fixtures import (observation_context, intake_target, two_intake_targets, zero_capabilities,
    observation, preparation, call, cleanup, NOW, REF)  # noqa: F401
from tests.research_intake_fixtures import snapshot, intake
from tests.services.test_research_context_isolation import wait_for_blocker


def accepted(ctx, value=None, **kw):
    return call(service.accept, ctx, "preparation_1", canonical(value or observation()), **kw)


def test_accept_retry_correction_history_and_no_legacy_writes(observation_context):
    ctx, _ = observation_context
    before = snapshot(legacy=True)
    a = accepted(ctx)
    b = call(service.accept, ctx, "preparation_1", canonical(observation())+b' \n')
    assert a == b
    assert a["provenance"] == "operator_import_unverified" and a["access_truth"] == "unknown"
    assert a["execution_authorized"] is False
    read = call(service.read, ctx, a["observation_id"])
    assert read["payload"] == observation()
    v = observation()
    v["batch_ref"] = "batch_2"
    v["entries"][0]["response"]["session_state"] = "expired"
    c = accepted(ctx, v, corrects_id=a["observation_id"])
    assert c["corrects_id"] == a["observation_id"] and c["digest"] != a["digest"]
    assert call(service.read, ctx, a["observation_id"])["payload"] == observation()
    assert snapshot(legacy=True) == before


@pytest.mark.parametrize("change", ["order", "content", "preparation", "correction"])
def test_changed_retry_atomic_even_caller_commits(observation_context, change):
    ctx, ids = observation_context
    v = observation()
    v["entries"].append(dict(v["entries"][0], entry_ref="entry_2", source_entry_index=1))
    a = accepted(ctx, v)
    ref, corrects = "preparation_1", None
    if change == "order":
        v["entries"].reverse()
    elif change == "content":
        v["entries"][0]["response"]["session_state"] = "expired"
    elif change == "preparation":
        p = preparation(ids)
        p.update(preparation_ref="preparation_2", corrects_preparation="preparation_1")
        call(service.prepare, ctx, p)
        v["preparation_ref"] = ref = "preparation_2"
    else:
        corrects = a["observation_id"]
    before = snapshot()
    with SessionLocal() as db:
        with pytest.raises(ObservationError, match="retry_conflict"):
            service.accept(db, 1, ctx, ref, canonical(v), corrects_id=corrects, now=NOW)
        db.commit()
    assert snapshot() == before


@pytest.mark.parametrize("field,value", [("project_ref", "project_2"), ("preparation_ref", "preparation_2"), ("batch_ref", "batch_99")])
def test_file_claims_cannot_change_trusted_context(observation_context, field, value):
    ctx, _ = observation_context
    v = observation()
    v[field] = value
    before = snapshot()
    with pytest.raises(ObservationError, match="context_unavailable"):
        accepted(ctx, v)
    assert snapshot() == before


@pytest.mark.parametrize("field,value", [("entry_ref", "entry_99"), ("actor_ref", "actor_99"),
    ("resource_labels", ["resource_99"]), ("origin", "http://127.0.0.1:58124"),
    ("query_names", ["password"]), ("path_template", "/items/{resource_id}")])
def test_unapproved_entry_registry_rejects_entire_batch(observation_context, field, value):
    ctx, _ = observation_context
    v = observation()
    v["entries"].append(copy.deepcopy(v["entries"][0]))
    v["entries"][1].update(entry_ref="entry_2", source_entry_index=1)
    v["entries"][1]["response"]["object_labels"] = []
    v["entries"][1][field] = value
    before = snapshot()
    with pytest.raises(ObservationError):
        accepted(ctx, v)
    assert snapshot() == before


def test_missing_permission_draft_can_import_without_execution(observation_context):
    ctx, ids = observation_context
    v = intake(ids)
    v["targets"][0].update(authorization_revision_id=None, permission_source=None)
    with SessionLocal() as db:
        result = correct_context(db, 1, ctx, {"expected_version": 1, "correction_reference": REF, "intake": v}, now=NOW)
        db.commit()
    assert "permission_missing" in result["missing_inputs"] and not result["execution_preparation_allowed"]
    with pytest.raises(ObservationError):
        accepted(ctx)
    p = preparation(ids)
    p.update(context_version=2, preparation_ref="preparation_2", corrects_preparation="preparation_1")
    call(service.prepare, ctx, p)
    v = observation()
    v["preparation_ref"] = "preparation_2"
    assert not call(service.accept, ctx, "preparation_2", canonical(v))["execution_authorized"]


def test_foreign_project_references_have_uniform_failure(two_intake_targets):
    a, b = two_intake_targets
    contexts = []
    try:
        for project, ids in ((1, a), (2, b)):
            with SessionLocal() as db:
                ctx = create_context(db, {"project_number": project, "intake": intake(ids)}, now=NOW)["context_id"]
                db.commit()
            contexts.append(ctx)
            call(service.maintain, ctx, {"action": "reconcile", "review": REF}, project=project)
            call(service.prepare, ctx, preparation(ids), project=project)
        rb = accepted(contexts[1], observation(2, 58124), project=2)
        before = snapshot()
        for ctx, oid in ((contexts[1], rb["observation_id"]), (9999999, rb["observation_id"]), (contexts[0], rb["observation_id"])):
            with pytest.raises(ObservationError, match="context_unavailable"):
                call(service.read, ctx, oid)
        p = preparation(b)
        p["preparation_ref"] = "preparation_2"
        with pytest.raises(ObservationError, match="context_unavailable"):
            call(service.prepare, contexts[0], p)
        assert snapshot() == before
    finally:
        for ctx in contexts:
            cleanup(ctx)


def test_close_transfer_old_reads_do_not_consume_live_metadata(observation_context):
    # The second fixture graph need not own the transferred Target beforehand.
    ctx, ids = observation_context
    a = accepted(ctx)
    call(close_context, ctx, {"expected_version": 1, "closure_reference": REF})
    with SessionLocal() as db:
        other = create_context(db, {"project_number": 2, "intake": intake(ids)}, now=NOW)["context_id"]
        db.commit()
    before = call(service.read, ctx, a["observation_id"])
    statements = []
    def capture(conn, cursor, statement, *args):
        statements.append(statement.lower())
    with SessionLocal() as db:
        db.get(Target, ids["target"]).base_url = "http://127.0.0.1:59000"
        db.get(AuthorizationRevision, ids["revision"]).max_requests_per_second = .001
        db.get(Scope, ids["scope"]).is_active = False
        db.commit()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        after = call(service.read, ctx, a["observation_id"])
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert before == after and after["availability"] == "quarantined" and "payload" not in after
    assert not any("from targets" in s or "from scopes" in s or "from authorization_revisions" in s for s in statements)
    with pytest.raises(ObservationError):
        accepted(ctx)


def test_expiry_hold_delete_tombstone_and_no_revival(observation_context):
    ctx, ids = observation_context
    p = preparation(ids)
    p.update(preparation_ref="preparation_2", retention_seconds=10, corrects_preparation="preparation_1")
    call(service.prepare, ctx, p)
    v = observation()
    v["preparation_ref"] = "preparation_2"
    a = call(service.accept, ctx, "preparation_2", canonical(v))
    oid = a["observation_id"]
    assert call(service.read, ctx, oid, now=NOW+timedelta(seconds=9))["availability"] == "available"
    hold = {"review": REF, "reason": "synthetic_review", "until": (NOW+timedelta(seconds=20)).isoformat()}
    call(service.lifecycle, ctx, oid, "hold", hold)
    assert "payload" not in call(service.read, ctx, oid)
    assert "payload" in call(service.read, ctx, oid, review={"review": REF})
    m = call(service.maintain, ctx, {"action": "reconcile", "review": REF}, now=NOW+timedelta(seconds=10))
    assert m["purged_payloads"] == 0
    call(service.lifecycle, ctx, oid, "release", {"review": REF}, now=NOW+timedelta(seconds=11))
    assert call(service.read, ctx, oid, now=NOW+timedelta(seconds=11))["availability"] == "expired"
    assert call(service.maintain, ctx, {"action": "reconcile", "review": REF}, now=NOW+timedelta(seconds=11))["purged_payloads"] == 1
    with pytest.raises(ObservationError):
        call(service.lifecycle, ctx, oid, "hold", hold, now=NOW+timedelta(seconds=12))
    assert call(service.read, ctx, oid, now=NOW+timedelta(seconds=12))["digest"] == a["digest"]
    assert "payload" not in call(service.read, ctx, oid, now=NOW+timedelta(seconds=12))
    later = NOW+timedelta(days=90, seconds=11)
    assert call(service.maintain, ctx, {"action": "reconcile", "review": REF}, now=later)["removed_tombstones"] == 1
    with pytest.raises(ObservationError):
        call(service.read, ctx, oid, now=later)


@pytest.mark.parametrize("seconds,allowed", [(1, True), (2592000, True), (0, False), (2592001, False)])
def test_hold_exact_boundary(observation_context, seconds, allowed):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    hold = {"review": REF, "reason": "synthetic_review", "until": (NOW+timedelta(seconds=seconds)).isoformat()}
    if allowed:
        assert call(service.lifecycle, ctx, oid, "hold", hold)["availability"] == "held"
    else:
        before = snapshot()
        with pytest.raises(ObservationError):
            call(service.lifecycle, ctx, oid, "hold", hold)
        assert snapshot() == before


def test_recovery_gate_replay_and_revoked_preparation(observation_context, monkeypatch):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    monkeypatch.setattr(service, "BOOT_TOKEN", "different-process")
    assert "payload" not in call(service.read, ctx, oid)
    with pytest.raises(ObservationError, match="recovery_required"):
        accepted(ctx)
    result = call(service.maintain, ctx, {"action": "reconcile", "review": REF, "deleted_observation_ids": [oid]})
    assert result["purged_payloads"] == 1 and result["private_data_admitted"] is False
    assert call(service.read, ctx, oid)["availability"] == "deleted"
    retry = accepted(ctx)
    assert retry["availability"] == "deleted"  # Receipt only, no recreation.
    call(service.revoke_preparation, ctx, "preparation_1", {"review": REF})
    with pytest.raises(ObservationError):
        accepted(ctx)
    assert "payload" not in call(service.read, ctx, oid)


@pytest.mark.parametrize("operation", ["accept", "read", "delete", "maintain"])
def test_audit_failure_rolls_back_even_caught_then_commit(observation_context, monkeypatch, operation):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"] if operation != "accept" else None
    before = snapshot()
    def fail(*a, **kw):
        raise RuntimeError("synthetic audit failure")
    monkeypatch.setattr(service, "_event", fail)
    with SessionLocal() as db:
        with pytest.raises(RuntimeError):
            if operation == "accept":
                service.accept(db, 1, ctx, "preparation_1", canonical(observation()), now=NOW)
            elif operation == "read":
                service.read(db, 1, ctx, oid, now=NOW)
            elif operation == "delete":
                service.lifecycle(db, 1, ctx, oid, "delete", {"review": REF}, now=NOW)
            else:
                service.maintain(db, 1, ctx, {"action": "suspend", "review": REF}, now=NOW)
        db.commit()
    assert snapshot() == before


def test_corrupt_payload_is_never_returned(observation_context):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    with SessionLocal() as db:
        db.get(ObservationPayload, oid).canonical_payload = '{}'
        db.commit()
    with pytest.raises(ObservationError, match="source_unavailable"):
        call(service.read, ctx, oid)
    with pytest.raises(ObservationError):
        accepted(ctx)


@pytest.mark.parametrize("first,second", [("accept", "close"), ("close", "accept"), ("hold", "cleanup"),
    ("cleanup", "hold"), ("accept", "accept"), ("suspend", "accept"), ("delete", "hold"), ("read", "close"), ("close", "read"), ("cleanup", "delete"), ("delete", "cleanup"), ("cleanup", "suspend")])
def test_real_transaction_serialization(observation_context, first, second):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"] if first in ("hold", "cleanup", "delete", "read") or second == "read" else None
    ready, release, waiting = Event(), Event(), Event()
    pids = {}
    def operation(db, name):
        if name == "read":
            return service.read(db, 1, ctx, oid, now=NOW)
        if name == "accept":
            return service.accept(db, 1, ctx, "preparation_1", canonical(observation()), now=NOW)
        if name == "close":
            return close_context(db, 1, ctx, {"expected_version": 1, "closure_reference": REF}, now=NOW)
        if name == "hold":
            return service.lifecycle(db, 1, ctx, oid, "hold", {"review": REF, "reason": "synthetic_review", "until": (NOW+timedelta(days=1)).isoformat()}, now=NOW)
        if name == "delete":
            return service.lifecycle(db, 1, ctx, oid, "delete", {"review": REF}, now=NOW)
        return service.maintain(db, 1, ctx, {"action": "suspend" if name == "suspend" else "reconcile", "review": REF}, now=NOW)
    def worker(name, leading):
        with SessionLocal() as db:
            pids[name + str(leading)] = db.scalar(text("SELECT pg_backend_pid()"))
            if not leading:
                waiting.set()
            try:
                result = operation(db, name)
                if leading:
                    ready.set()
                    assert release.wait(10)
                db.commit()
                return result
            except ObservationError as exc:
                db.rollback()
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(worker, first, True)
        assert ready.wait(10)
        b = pool.submit(worker, second, False)
        assert waiting.wait(10)
        try:
            wait_for_blocker(pids[second+"False"], pids[first+"True"])
        finally:
            release.set()
        ra, rb = a.result(15), b.result(15)
    if (first in ("close", "suspend") and second == "accept") or (first == "delete" and second == "hold"):
        assert isinstance(rb, str)
    if first == "close" and second == "read":
        assert rb["availability"] == "quarantined" and "payload" not in rb
    if first == second == "accept":
        assert ra == rb


def test_correction_required_for_reused_entry_identity(observation_context):
    ctx, _ = observation_context
    a = accepted(ctx)
    v = observation()
    v["batch_ref"] = "batch_2"
    before = snapshot()
    with pytest.raises(ObservationError, match="correction_required"):
        accepted(ctx, v)
    assert snapshot() == before
    assert accepted(ctx, v, corrects_id=a["observation_id"])["corrects_id"] == a["observation_id"]


def test_maintenance_backlog_and_delete_hold_quarantine_boundaries(observation_context):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    call(service.lifecycle, ctx, oid, "hold", {"review": REF, "reason": "synthetic_review", "until": (NOW+timedelta(days=2)).isoformat()})
    call(service.lifecycle, ctx, oid, "delete", {"review": REF})
    assert call(service.maintain, ctx, {"action": "reconcile", "review": REF})["purged_payloads"] == 0
    # Disqualification cancels hold: unsafe content must not be retained under review.
    call(service.lifecycle, ctx, oid, "quarantine", {"review": REF})
    with pytest.raises(ObservationError, match="maintenance_required"):
        accepted(ctx, now=NOW+timedelta(days=1))
    m = call(service.maintain, ctx, {"action": "reconcile", "review": REF}, now=NOW+timedelta(days=1))
    assert m["purged_payloads"] == 1
    with pytest.raises(ObservationError):
        call(service.lifecycle, ctx, oid, "hold", {"review": REF, "reason": "synthetic_review", "until": (NOW+timedelta(days=2)).isoformat()})


def test_hold_expiration_defers_tombstone_clock(observation_context):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    call(service.lifecycle, ctx, oid, "hold", {"review": REF, "reason": "synthetic_review", "until": (NOW+timedelta(days=2)).isoformat()})
    call(service.lifecycle, ctx, oid, "delete", {"review": REF})
    assert call(service.maintain, ctx, {"action": "reconcile", "review": REF}, now=NOW+timedelta(days=2))["purged_payloads"] == 1
    assert call(service.maintain, ctx, {"action": "reconcile", "review": REF}, now=NOW+timedelta(days=90))["removed_tombstones"] == 0
    assert call(service.maintain, ctx, {"action": "reconcile", "review": REF}, now=NOW+timedelta(days=92))["removed_tombstones"] == 1


def test_recovery_corruption_quarantines_before_exposure(observation_context, monkeypatch):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    with SessionLocal() as db:
        db.get(ObservationPayload, oid).canonical_payload = '{}'
        db.commit()
    monkeypatch.setattr(service, "BOOT_TOKEN", "recovery-test")
    result = call(service.maintain, ctx, {"action": "reconcile", "review": REF})
    assert result["purged_payloads"] == 1
    result = call(service.read, ctx, oid)
    assert result["availability"] == "quarantined" and "payload" not in result


def test_replay_foreign_or_unknown_id_atomic(observation_context):
    ctx, _ = observation_context
    accepted(ctx)
    before = snapshot()
    with pytest.raises(ObservationError):
        call(service.maintain, ctx, {"action": "reconcile", "review": REF, "deleted_observation_ids": [9999999]})
    assert snapshot() == before


def test_log_expiry_and_preparation_revoke_cleanup(observation_context):
    ctx, _ = observation_context
    oid = accepted(ctx)["observation_id"]
    call(service.revoke_preparation, ctx, "preparation_1", {"review": REF})
    assert "payload" not in call(service.read, ctx, oid)
    assert call(service.maintain, ctx, {"action": "reconcile", "review": REF})["purged_payloads"] == 1
    call(service.maintain, ctx, {"action": "reconcile", "review": REF}, now=NOW+timedelta(days=90))
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ObservationRecord).where(ObservationRecord.context_id == ctx)) == 0
        assert db.scalar(select(func.count()).select_from(ObservationPreparation).where(ObservationPreparation.context_id == ctx)) == 0
        events = list(db.scalars(select(ObservationEvent).where(ObservationEvent.context_id == ctx)))
        assert len(events) == 1 and events[0].code == "reconcile"


@pytest.mark.parametrize("url", ["http://private-company.invalid:80", "http://10.0.0.1:80", "http://192.168.1.1:80"])
def test_synthetic_qualification_does_not_admit_private_origins(observation_context, url):
    ctx, ids = observation_context
    with SessionLocal() as db:
        db.get(Target, ids["target"]).base_url = url
        db.commit()
    before = snapshot()
    p = preparation(ids)
    p.update(preparation_ref="preparation_2", corrects_preparation="preparation_1")
    with pytest.raises(ObservationError):
        call(service.prepare, ctx, p)
    assert snapshot() == before


def test_unrelated_correction_cannot_hide_reused_entry_provenance(observation_context):
    ctx, ids = observation_context
    a = accepted(ctx)
    v = observation()
    v["batch_ref"] = "batch_2"
    v["entries"][0]["entry_ref"] = "entry_2"
    b = accepted(ctx, v)
    p = preparation(ids)
    p.update(preparation_ref="preparation_2", batch_refs=["batch_3"], corrects_preparation="preparation_1")
    call(service.prepare, ctx, p)
    v = observation()
    v.update(batch_ref="batch_3", preparation_ref="preparation_2")
    with pytest.raises(ObservationError, match="correction_required"):
        call(service.accept, ctx, "preparation_2", canonical(v), corrects_id=b["observation_id"])
    assert call(service.accept, ctx, "preparation_2", canonical(v), corrects_id=a["observation_id"])["corrects_id"] == a["observation_id"]


def test_accept_ownership_guard_blocks_close_before_target_metadata(observation_context):
    ctx, _ = observation_context
    at_target, release, waiting = Event(), Event(), Event()
    from threading import get_ident
    identifiers = {}
    def capture(conn, cursor, statement, *args):
        if get_ident() == identifiers.get("thread") and "FROM targets" in statement:
            at_target.set()
            assert release.wait(10)
    def importer():
        with SessionLocal() as db:
            identifiers["thread"] = get_ident()
            identifiers["importer"] = db.scalar(text("SELECT pg_backend_pid()"))
            result = service.accept(db, 1, ctx, "preparation_1", canonical(observation()), now=NOW)
            db.commit()
            return result
    def closer():
        with SessionLocal() as db:
            identifiers["closer"] = db.scalar(text("SELECT pg_backend_pid()"))
            waiting.set()
            result = close_context(db, 1, ctx, {"expected_version": 1, "closure_reference": REF}, now=NOW)
            db.commit()
            return result
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(importer)
            assert at_target.wait(10)
            b = pool.submit(closer)
            assert waiting.wait(10)
            try:
                wait_for_blocker(identifiers["closer"], identifiers["importer"])
            finally:
                release.set()
            result = a.result(15)
            assert b.result(15)["state"] == "closed"
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", capture)
    assert "payload" not in call(service.read, ctx, result["observation_id"])


def test_retention_seconds_do_not_depend_on_database_dst(observation_context):
    from datetime import datetime, timezone
    ctx, ids = observation_context
    instant = datetime(2032, 3, 1, 12, tzinfo=timezone.utc)
    p = preparation(ids)
    p.update(preparation_ref="preparation_2", corrects_preparation="preparation_1", valid_until=(instant+timedelta(days=30)).isoformat())
    call(service.prepare, ctx, p, now=instant)
    v = observation()
    v["preparation_ref"] = "preparation_2"
    with SessionLocal() as db:
        db.execute(text("SET LOCAL TIME ZONE 'America/Los_Angeles'"))
        a = service.accept(db, 1, ctx, "preparation_2", canonical(v), now=instant)
        db.commit()
    assert a["expires_at"] == (instant+timedelta(seconds=2592000)).isoformat()
    hold = {"review": REF, "reason": "synthetic_review", "until": (instant+timedelta(seconds=2592000)).isoformat()}
    assert call(service.lifecycle, ctx, a["observation_id"], "hold", hold, now=instant)["availability"] == "held"


@pytest.mark.parametrize("lock_domain", ["context", "target"])
@pytest.mark.parametrize("action", ["read", "hold"])
def test_lock_wait_cannot_extend_payload_availability(observation_context, monkeypatch, lock_domain, action):
    ctx, ids = observation_context
    p = preparation(ids)
    p.update(preparation_ref="preparation_2", corrects_preparation="preparation_1", retention_seconds=10)
    call(service.prepare, ctx, p)
    v = observation()
    v["preparation_ref"] = "preparation_2"
    oid = call(service.accept, ctx, "preparation_2", canonical(v))["observation_id"]
    current = {"now": NOW}
    original_time = service._time
    monkeypatch.setattr(service, "_time", lambda value: current["now"] if value is None else original_time(value))
    waiting, locked, release = Event(), Event(), Event()
    pids = {}
    def blocker():
        with SessionLocal() as db:
            pids["blocker"] = db.scalar(text("SELECT pg_backend_pid()"))
            if lock_domain == "context":
                service._locked(db, 1, ctx)
            else:
                db.scalar(select(Target.id).where(Target.id == ids["target"]).with_for_update())
            locked.set()
            assert release.wait(10)
            db.commit()
    def consumer():
        with SessionLocal() as db:
            pids["consumer"] = db.scalar(text("SELECT pg_backend_pid()"))
            waiting.set()
            try:
                if action == "read":
                    result = service.read(db, 1, ctx, oid)  # Runtime clock, sampled after blocking locks.
                else:
                    result = service.lifecycle(db, 1, ctx, oid, "hold", {"review": REF,
                        "reason": "synthetic_review", "until": (NOW+timedelta(days=1)).isoformat()})
                db.commit()
                return result
            except ObservationError as exc:
                db.rollback()
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(blocker)
        assert locked.wait(10)
        b = pool.submit(consumer)
        assert waiting.wait(10)
        try:
            wait_for_blocker(pids["consumer"], pids["blocker"])
            current["now"] = NOW+timedelta(seconds=10)
        finally:
            release.set()
        a.result(15)
        result = b.result(15)
    if action == "read":
        assert result["availability"] == "expired" and "payload" not in result
    else:
        assert result == "observation_source_unavailable"
