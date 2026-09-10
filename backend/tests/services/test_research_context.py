from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import event, select

from app.db.models import AuthorizationRevision, Scope, Target
from app.db.models.research_context import ResearchContextVersion, ResearchTargetAssociation
from app.db.session import SessionLocal, engine
from app.services.research_context import (
    ResearchContextError, create_context, correct_context, close_context, read_context,
)
from tests.research_intake_fixtures import NOW, REF, intake, intake_target, snapshot  # noqa: F401


def create(ids, payload=None, project=1):
    with SessionLocal() as db:
        result = create_context(db, {"project_number": project, "intake": payload or intake(ids)}, now=NOW)
        db.commit()
        return result


def test_current_permission_is_not_execution_or_budget_approval(intake_target):
    before = snapshot(legacy=True)
    result = create(intake_target)
    assert result["permissions"][0]["status"] == "referenced_current"
    assert result["missing_inputs"] == ["budget_unapproved", "facts_missing"]
    assert result["budget_missing_fields"] == ["model_tokens", "model_cost_microusd", "approval_reference"]
    assert result["intake"]["budget"]["model_tokens"] is None
    assert result["execution_preparation_allowed"] is result["execution_authorized"] is False
    assert result["activity_limits"] == {"target_requests": 0, "provider_calls": 0, "provider_cost_microusd": 0}
    assert snapshot(legacy=True) == before


@pytest.mark.parametrize("change,expected", [
    ("missing", "missing"), ("reference_missing", "missing"), ("revoked", "revoked"),
    ("draft", "draft"), ("superseded", "superseded"), ("expired", "expired"),
    ("not_yet_valid", "not_yet_valid"), ("mismatched", "unavailable"),
    ("disabled", "target_unavailable"), ("get", "get_not_permitted"),
    ("automation", "get_not_permitted"), ("scope", "scope_missing"),
    ("scope_changed", "changed"), ("origin_changed", "changed"),
])
def test_permission_changes_visible_without_cached_authority(intake_target, change, expected):
    ids = intake_target
    payload = intake(ids)
    if change == "missing":
        payload["targets"][0].update(authorization_revision_id=None, permission_source=None)
    elif change == "reference_missing":
        payload["targets"][0]["permission_source"] = None
    result = create(ids, payload)
    with SessionLocal() as db:
        r, t, s = db.get(AuthorizationRevision, ids["revision"]), db.get(Target, ids["target"]), db.get(Scope, ids["scope"])
        if change in {"revoked", "draft", "superseded"}:
            r.lifecycle_state = change
        elif change == "expired":
            r.valid_until = NOW
        elif change == "not_yet_valid":
            r.valid_from = NOW + timedelta(microseconds=1)
        elif change == "mismatched":
            t.authorization_revision_id = None
        elif change == "disabled":
            t.is_enabled = False
        elif change == "get":
            r.allow_get = False
        elif change == "automation":
            r.automation_allowed = False
        elif change == "scope":
            s.is_active = False
        elif change == "scope_changed":
            s.path_pattern = "/other/*"
        elif change == "origin_changed":
            t.base_url = "http://127.0.0.1:58124"
        db.commit()
    before = snapshot()
    with SessionLocal() as db:
        loaded = read_context(db, 1, result["context_id"], now=NOW)
    assert loaded["permissions"][0]["status"] == expected
    assert "permission_missing" in loaded["missing_inputs"]
    assert snapshot() == before


def test_independent_revision_never_unions_and_budget_rate_remains_visible(intake_target):
    ids = intake_target
    with SessionLocal() as db:
        db.get(AuthorizationRevision, ids["revision"]).max_requests_per_second = .25
        db.commit()
    result = create(ids)
    assert result["budget_rate_exceeded"]
    assert "budget_unapproved" in result["missing_inputs"]
    payload = intake(ids)
    payload["targets"][0]["authorization_revision_id"] = 2147483647
    correction = {"expected_version": 1, "correction_reference": REF, "intake": payload}
    before = snapshot()
    with SessionLocal() as db:
        with pytest.raises(ResearchContextError) as rejected:
            correct_context(db, 1, result["context_id"], correction, now=NOW)
        assert rejected.value.code == "intake_context_unavailable"
        db.commit()
    assert snapshot() == before
    # An explicit missing-permission draft remains supported; no fallback grant.
    payload["targets"][0].update(authorization_revision_id=None, permission_source=None)
    with SessionLocal() as db:
        changed = correct_context(db, 1, result["context_id"], correction, now=NOW)
        db.commit()
    assert changed["permissions"][0]["status"] == "missing"
    assert changed["context_version"] == 2


def test_history_correction_and_cross_project_failure(intake_target):
    first = create(intake_target)
    before = snapshot()
    with SessionLocal() as db:
        with pytest.raises(ResearchContextError) as wrong:
            read_context(db, 2, first["context_id"], now=NOW)
        assert wrong.value.code == "intake_context_unavailable"
        with pytest.raises(ResearchContextError):
            correct_context(db, 2, first["context_id"], {"expected_version": 1,
                "correction_reference": REF, "intake": intake(intake_target)}, now=NOW)
        db.commit()
    assert snapshot() == before
    payload = intake(intake_target)
    payload["rules"][0]["source"]["version"] = 2
    payload["data_eligibility"] = "unknown"
    payload["budget"]["approval_reference"] = REF
    with SessionLocal() as db:
        second = correct_context(db, 1, first["context_id"], {"expected_version": 1,
            "correction_reference": REF, "intake": payload}, now=NOW+timedelta(seconds=1))
        db.commit()
        old = read_context(db, 1, first["context_id"], version=1, now=NOW)
    assert old["intake"] == first["intake"] and old["is_current"] is False
    assert second["correction_reference"] == REF
    assert second["missing_inputs"] == ["data_ineligible", "budget_unapproved", "facts_missing"]
    assert second["budget_approval"] == "unverified"


def test_atomic_failure_even_when_caller_catches_and_commits(intake_target):
    before = snapshot()
    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO research_context_versions"):
            raise RuntimeError("synthetic failure")
    event.listen(engine, "before_cursor_execute", fail)
    try:
        with SessionLocal() as db:
            with pytest.raises(RuntimeError):
                create_context(db, {"project_number": 1, "intake": intake(intake_target)}, now=NOW)
            db.commit()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert snapshot() == before


def test_concurrent_target_ownership_and_close_transfer(intake_target):
    barrier = Barrier(2)
    def claim(project):
        barrier.wait(timeout=10)
        try:
            return create(intake_target, project=project)
        except ResearchContextError as e:
            return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, [1, 2]))
    winner, = [r for r in results if isinstance(r, dict)]
    assert results.count("intake_context_unavailable") == 1
    with SessionLocal() as db:
        closed = close_context(db, winner["project_number"], winner["context_id"], {
            "expected_version": 1, "closure_reference": REF}, now=NOW)
        db.commit()
    assert closed["state"] == "closed"
    transferred = create(intake_target, project=3)
    assert transferred["project_number"] == 3
    with SessionLocal() as db:
        rows = list(db.scalars(select(ResearchTargetAssociation).where(
            ResearchTargetAssociation.target_id == intake_target["target"])))
    assert len(rows) == 2 and sum(r.released_at is None for r in rows) == 1


def test_concurrent_corrections_have_one_success_and_immutable_history(intake_target):
    first = create(intake_target)
    barrier = Barrier(2)
    def revise(number):
        payload = intake(intake_target)
        payload["rules"][0]["source"]["version"] = number
        barrier.wait(timeout=10)
        with SessionLocal() as db:
            try:
                result = correct_context(db, 1, first["context_id"], {"expected_version": 1,
                    "correction_reference": REF, "intake": payload}, now=NOW)
                db.commit()
                return result
            except ResearchContextError as e:
                return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(revise, [2, 3]))
    assert len([r for r in results if isinstance(r, dict)]) == 1
    assert results.count("intake_version_conflict") == 1
    with SessionLocal() as db:
        rows = list(db.scalars(select(ResearchContextVersion).where(
            ResearchContextVersion.context_id == first["context_id"]).order_by(ResearchContextVersion.version_number)))
    assert [r.version_number for r in rows] == [1, 2]
    assert rows[0].intake == first["intake"]


def test_aware_time_and_dirty_session(intake_target):
    with SessionLocal() as db:
        with pytest.raises(ResearchContextError):
            create_context(db, {"project_number": 1, "intake": intake(intake_target)}, now=NOW.replace(tzinfo=None))
        t = db.get(Target, intake_target["target"])
        t.name = "uncommitted"
        with pytest.raises(ResearchContextError) as dirty:
            create_context(db, {"project_number": 1, "intake": intake(intake_target)}, now=NOW)
        assert dirty.value.code == "intake_session_not_clean"
        db.rollback()
    result = create(intake_target)
    with SessionLocal() as db:
        shifted = read_context(db, 1, result["context_id"], now=NOW.astimezone(timezone(timedelta(hours=-7))))
    assert shifted["evaluated_at"] == result["evaluated_at"]


def test_scope_scan_bound_keeps_uncertainty_visible(intake_target):
    ids = intake_target
    with SessionLocal() as db:
        db.add_all([Scope(target_id=ids["target"], hostname="127.0.0.1", path_pattern=f"/s{i}",
                          allowed_methods=["GET"], is_active=True) for i in range(255)])
        db.commit()
    result = create(ids)
    assert result["permissions"][0]["status"] == "referenced_current"
    with SessionLocal() as db:
        db.add(Scope(target_id=ids["target"], hostname="127.0.0.1", path_pattern="/overflow", allowed_methods=["GET"]))
        db.commit()
        reread = read_context(db, 1, result["context_id"], now=NOW)
    assert reread["permissions"][0]["status"] == "scope_limit_exceeded"
    assert "permission_missing" in reread["missing_inputs"]


def test_no_partial_association_on_missing_second_target(intake_target):
    payload = intake(intake_target)
    payload["targets"].append({**deepcopy(payload["targets"][0]), "target_id": 2147483647})
    before = snapshot()
    with SessionLocal() as db:
        with pytest.raises(ResearchContextError):
            create_context(db, {"project_number": 1, "intake": payload}, now=NOW)
        db.commit()
    assert snapshot() == before


def test_constructed_schema_cannot_bypass_validation(intake_target):
    from app.schemas.research_context import ResearchContextCreate
    malicious = ResearchContextCreate.model_construct(project_number=True, intake=intake(intake_target))
    before = snapshot()
    with SessionLocal() as db:
        with pytest.raises(ResearchContextError) as error:
            create_context(db, malicious, now=NOW)
        assert error.value.code == "intake_invalid_request"
        db.commit()
    assert snapshot() == before


def test_closed_context_and_association_changes_require_explicit_new_context(intake_target):
    result = create(intake_target)
    payload = intake(intake_target)
    payload["targets"][0]["association_review"]["version"] = 2
    with SessionLocal() as db:
        with pytest.raises(ResearchContextError):
            correct_context(db, 1, result["context_id"], {"expected_version": 1,
                "correction_reference": REF, "intake": payload}, now=NOW)
        close_context(db, 1, result["context_id"], {"expected_version": 1, "closure_reference": REF}, now=NOW)
        db.commit()
        with pytest.raises(ResearchContextError):
            correct_context(db, 1, result["context_id"], {"expected_version": 1,
                "correction_reference": REF, "intake": intake(intake_target)}, now=NOW)
        db.commit()


def test_service_rejection_never_warns_with_constructed_sensitive_values():
    import warnings
    from app.schemas.research_context import ResearchContextCreate
    value = ResearchContextCreate.model_construct(project_number=True, intake={"secret": "synthetic-canary"})
    with warnings.catch_warnings(record=True) as captured, SessionLocal() as db:
        warnings.simplefilter("always")
        with pytest.raises(ResearchContextError) as rejected:
            create_context(db, value, now=NOW)
        assert rejected.value.code == "intake_invalid_request"
    assert captured == []
