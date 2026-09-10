"""Project isolation, including previously stored unsafe selections and lock ordering."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event, get_ident
from time import monotonic

import pytest
from sqlalchemy import delete, event, select, text

from app.db.models import AuthorizationRevision, Scope, Target
from app.db.models.research_context import ResearchContextVersion, ResearchTargetAssociation
from app.db.session import SessionLocal, engine
from app.services.research_context import (
    ResearchContextError, close_context, correct_context, create_context, read_context,
)
from tests.research_intake_fixtures import NOW, REF, intake, snapshot, two_intake_targets  # noqa: F401
from tests.services.test_research_context import create


def seed_old_foreign_selection(context_id, revision_id):
    """Reproduce a592f7a's accepted record, without migrating or repairing history."""
    with SessionLocal() as db:
        row = db.scalar(select(ResearchContextVersion).where(
            ResearchContextVersion.context_id == context_id, ResearchContextVersion.version_number == 1))
        value = deepcopy(row.intake)
        value["targets"][0]["authorization_revision_id"] = revision_id
        row.intake = value
        row.permission_snapshots = {str(value["targets"][0]["target_id"]): "old_untrusted_snapshot"}
        db.commit()


def change_rate(ids, rate):
    with SessionLocal() as db:
        db.get(AuthorizationRevision, ids["revision"]).max_requests_per_second = rate
        db.commit()


def assert_no_live_reads(statements):
    for sql in statements:
        sql = sql.lower()
        assert not any(f"from {table}" in sql for table in ("targets", "authorization_revisions", "scopes")), sql


def test_foreign_and_missing_revision_mutations_are_identical_and_atomic(two_intake_targets):
    a, b = two_intake_targets
    create(b, project=2)
    assert a["profile"] != b["profile"] and a["target"] != b["target"]
    for revision_id in (b["revision"], 2147483647):
        for rate in (.1, 10):
            change_rate(b, rate)
            payload = intake(a)
            payload["targets"][0]["authorization_revision_id"] = revision_id
            before = snapshot()
            with SessionLocal() as db:
                with pytest.raises(ResearchContextError) as error:
                    create_context(db, {"project_number": 1, "intake": payload}, now=NOW)
                assert (error.value.status, error.value.code) == (409, "intake_context_unavailable")
                db.commit()
            assert snapshot() == before
    first = create(a)
    for revision_id in (b["revision"], 2147483647):
        for rate in (.1, 10):
            change_rate(b, rate)
            payload = intake(a)
            payload["targets"][0]["authorization_revision_id"] = revision_id
            before = snapshot()
            with SessionLocal() as db:
                with pytest.raises(ResearchContextError) as error:
                    correct_context(db, 1, first["context_id"], {"expected_version": 1,
                        "correction_reference": REF, "intake": payload}, now=NOW)
                assert (error.value.status, error.value.code) == (409, "intake_context_unavailable")
                db.commit()
            assert snapshot() == before


def test_old_foreign_selection_discloses_neither_rate_nor_existence(two_intake_targets):
    a, b = two_intake_targets
    first = create(a)
    create(b, project=2)
    seed_old_foreign_selection(first["context_id"], b["revision"])
    results = []
    for rate in (.1, 10, None):
        with SessionLocal() as db:
            if rate is None:
                db.get(Target, b["target"]).authorization_revision_id = None
                db.flush()
                db.execute(delete(AuthorizationRevision).where(AuthorizationRevision.id == b["revision"]))
            else:
                db.get(AuthorizationRevision, b["revision"]).max_requests_per_second = rate
            db.commit()
        before = snapshot()
        with SessionLocal() as db:
            results.append(read_context(db, 1, first["context_id"], now=NOW))
        assert snapshot() == before
    assert results[0] == results[1] == results[2]
    assert results[0]["permissions"][0]["status"] == "unavailable"
    assert results[0]["budget_rate_exceeded"] is False
    assert "permission_missing" in results[0]["missing_inputs"]


def test_target_revision_id_alone_does_not_allow_foreign_profile_metadata(two_intake_targets):
    a, b = two_intake_targets
    first = create(a)
    create(b, project=2)
    seed_old_foreign_selection(first["context_id"], b["revision"])
    # Deliberately inconsistent legacy binding: the revision matches the selected
    # ID but belongs to a different profile. Intake must not consume its fields.
    with SessionLocal() as db:
        db.get(Target, a["target"]).authorization_revision_id = b["revision"]
        db.commit()
    try:
        results = []
        for rate in (.1, 10):
            change_rate(b, rate)
            with SessionLocal() as db:
                results.append(read_context(db, 1, first["context_id"], now=NOW))
        assert results[0] == results[1]
        assert results[0]["permissions"][0]["status"] == "unavailable"
        assert results[0]["budget_rate_exceeded"] is False
    finally:
        with SessionLocal() as db:
            db.get(Target, a["target"]).authorization_revision_id = a["revision"]
            db.commit()


def close_and_transfer(a, b, context_id):
    with SessionLocal() as db:
        close_context(db, 1, context_id, {"expected_version": 2, "closure_reference": REF}, now=NOW)
        db.commit()
    combined = intake(b)
    combined["targets"].append(intake(a)["targets"][0])
    create(b, payload=combined, project=2)


def two_versions(a):
    first = create(a)
    revised = intake(a)
    revised["rules"][0]["source"]["version"] = 2
    with SessionLocal() as db:
        second = correct_context(db, 1, first["context_id"], {"expected_version": 1,
            "correction_reference": REF, "intake": revised}, now=NOW)
        db.commit()
    return first, second


@pytest.mark.parametrize("released_only", [False, True])
def test_old_context_has_no_live_reads_and_keeps_exact_history(two_intake_targets, released_only):
    a, b = two_intake_targets
    first, second = two_versions(a)
    if released_only:
        with SessionLocal() as db:
            association = db.scalar(select(ResearchTargetAssociation).where(
                ResearchTargetAssociation.context_id == first["context_id"]))
            association.released_at = NOW
            db.commit()
        combined = intake(b)
        combined["targets"].append(intake(a)["targets"][0])
        create(b, payload=combined, project=2)
    else:
        close_and_transfer(a, b, first["context_id"])
    initial = None
    for changed in (False, True):
        if changed:
            with SessionLocal() as db:
                r = db.get(AuthorizationRevision, a["revision"])
                r.max_requests_per_second, r.lifecycle_state, r.allow_get = .01, "revoked", False
                db.get(Scope, a["scope"]).path_pattern = "/new-project/*"
                db.get(Scope, a["scope"]).is_active = False
                db.get(Target, a["target"]).is_enabled = False
                db.commit()
        before = snapshot()
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(engine, "before_cursor_execute", capture)
        try:
            with SessionLocal() as db:
                output = [read_context(db, 1, first["context_id"], version=v, now=NOW) for v in (None, 1, 2)]
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert_no_live_reads(statements)
        assert snapshot() == before
        assert [r["intake"] for r in output] == [second["intake"], first["intake"], second["intake"]]
        assert all(r["permissions"][0]["status"] == "unavailable" and not r["budget_rate_exceeded"] for r in output)
        assert all(not r["execution_preparation_allowed"] and "permission_missing" in r["missing_inputs"] for r in output)
        if initial is None:
            initial = output
        else:
            assert output == initial
    before = snapshot()
    with SessionLocal() as db:
        with pytest.raises(ResearchContextError):
            correct_context(db, 1, first["context_id"], {"expected_version": 2,
                "correction_reference": REF, "intake": intake(a)}, now=NOW)
        db.commit()
    assert snapshot() == before


def wait_for_blocker(waiter, blocker):
    """Observe a real PostgreSQL row-lock wait; do not infer ordering from a sleep."""
    deadline = monotonic() + 10
    pause = Event()
    while monotonic() < deadline:
        with engine.connect() as db:
            pids = db.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter})
        if blocker in pids:
            return
        pause.wait(.01)
    pytest.fail("Expected ownership row-lock conflict was not observed")


@pytest.mark.parametrize("close_first", [False, True])
@pytest.mark.parametrize("read_version", [None, 1])
def test_read_close_transfer_are_serialized_at_ownership_boundary(two_intake_targets, close_first, read_version):
    a, b = two_intake_targets
    first, second = two_versions(a)
    reached, release, reader_started, closer_started = Event(), Event(), Event(), Event()
    pids = {}
    reader_thread = {}

    def pause_before_live_metadata(conn, cursor, statement, parameters, context, executemany):
        if (not close_first and get_ident() == reader_thread.get("id")
                and "FROM targets" in statement and not reached.is_set()):
            # Association validation has completed but no Target/revision/Scope
            # data has been fetched. Close/transfer must already be excluded.
            reached.set()
            assert release.wait(10)

    def reader():
        with SessionLocal() as db:
            pids["reader"] = db.scalar(text("SELECT pg_backend_pid()"))
            reader_thread["id"] = get_ident()
            reader_started.set()
            result = read_context(db, 1, first["context_id"], version=read_version, now=NOW)
            db.commit()
            return result

    def closer():
        with SessionLocal() as db:
            pids["closer"] = db.scalar(text("SELECT pg_backend_pid()"))
            closer_started.set()
            result = close_context(db, 1, first["context_id"], {"expected_version": 2,
                "closure_reference": REF}, now=NOW)
            if close_first:
                reached.set()  # Closed but uncommitted; the reader must wait and then see closed.
                assert release.wait(10)
            db.commit()
        combined = intake(b)
        combined["targets"].append(intake(a)["targets"][0])
        create(b, payload=combined, project=2)
        change_rate(a, .01)
        return result

    event.listen(engine, "before_cursor_execute", pause_before_live_metadata)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            leading = pool.submit(closer if close_first else reader)
            try:
                assert reached.wait(10)
                following = pool.submit(reader if close_first else closer)
                assert (reader_started if close_first else closer_started).wait(10)
                waiter, blocker = ("reader", "closer") if close_first else ("closer", "reader")
                wait_for_blocker(pids[waiter], pids[blocker])
            finally:
                release.set()
            lead_result, follow_result = leading.result(timeout=15), following.result(timeout=15)
    finally:
        event.remove(engine, "before_cursor_execute", pause_before_live_metadata)
    read = follow_result if close_first else lead_result
    assert read["permissions"][0]["status"] == ("unavailable" if close_first else "referenced_current")
    with SessionLocal() as db:
        after = read_context(db, 1, first["context_id"], now=NOW)
    assert after["permissions"][0]["status"] == "unavailable"
    assert after["budget_rate_exceeded"] is False
    assert after["intake"] == second["intake"]
