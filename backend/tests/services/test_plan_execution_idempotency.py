import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    AuthorizationRevision,
    ExecutionPlan,
    ExecutionPlanApprovalRecord,
    ExecutionPlanClaim,
    PlanAction,
    SafetyDecisionRecord,
    Scope,
    TestCase,
    TestRun,
)
from app.db.session import SessionLocal, engine
from app.credentials.bearer import BearerCredentialService
from app.executors.http import ExecutionBlockedError
from app.services.execution_plan import PlanActionInput, create_execution_plan
from app.services.execution_plan_approval import record_plan_decision
from app.services.execution_plan_claim import (
    ExecutionClaimCoordinationError,
    ExecutionClaimUnavailableError,
    ExecutionPlanClaimService,
)
from app.services.plan_execution import (
    CanonicalResultLookupError,
    PlanExecutionService,
)
from app.services.safety_audit import SafetyAuditService
from tests.services.test_plan_execution_integration import (
    FailingGateway,
    MutatingRateLimiter,
    RecordingGateway,
    approved_plan,
    execute,
)


def test_success_replay_returns_exact_result_without_execution_boundaries(
    approved_plan: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id, _, _, _ = approved_plan
    first = execute(plan_id, limiter=MutatingRateLimiter(), gateway=RecordingGateway())
    replay_limiter = MutatingRateLimiter()
    replay_gateway = RecordingGateway()
    claims = ExecutionPlanClaimService(bind=engine)
    claims.acquire = lambda *args, **kwargs: pytest.fail("claim acquired on replay")
    monkeypatch.setattr(
        BearerCredentialService,
        "resolve_binding",
        lambda *args, **kwargs: pytest.fail("credential resolved on replay"),
    )

    replay = execute(
        plan_id,
        limiter=replay_limiter,
        gateway=replay_gateway,
        claim_service=claims,
    )

    assert replay.id == first.id
    assert replay_limiter.calls == 0
    assert replay_gateway.target_ids == []
    with SessionLocal() as db:
        action_id = db.scalar(
            select(PlanAction.id).where(PlanAction.execution_plan_id == plan_id)
        )
        outcome = db.scalar(
            select(SafetyDecisionRecord).where(
                SafetyDecisionRecord.execution_plan_id == plan_id,
                SafetyDecisionRecord.test_run_id == first.id,
            )
        )
        assert outcome is not None
        assert outcome.plan_action_id == action_id
        assert outcome.outcome == "succeeded"


def test_http_failed_replay_returns_same_result_without_outbound_execution(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    first = execute(plan_id, limiter=MutatingRateLimiter(), gateway=FailingGateway())
    limiter = MutatingRateLimiter()
    gateway = RecordingGateway()
    claims = ExecutionPlanClaimService(bind=engine)
    claims.acquire = lambda *args, **kwargs: pytest.fail("claim acquired on replay")

    replay = execute(
        plan_id, limiter=limiter, gateway=gateway, claim_service=claims
    )

    assert replay.id == first.id
    assert replay.error_message == "synthetic_network_failure: failed"
    assert limiter.calls == 0
    assert gateway.target_ids == []


def test_claim_unavailable_race_returns_fresh_canonical_result(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, target_id, revision_id, _ = approved_plan
    delegate = ExecutionPlanClaimService(bind=engine, attempt_timeout_seconds=0.1)
    active = delegate.acquire(plan_id, "active-writer", lease_seconds=10)
    committed_run_id: list[int] = []
    acquire_calls = 0

    class UnavailableAfterCommit:
        def acquire(self, *args, **kwargs):
            nonlocal acquire_calls
            acquire_calls += 1
            with SessionLocal() as db:
                action = db.scalar(
                    select(PlanAction).where(PlanAction.execution_plan_id == plan_id)
                )
                assert action is not None
                run = TestRun(
                    test_case_id=action.test_case_id,
                    authorization_revision_id=revision_id,
                    execution_plan_id=plan_id,
                    request_data={"race": "active-owner"},
                    response_status=200,
                )
                db.add(run)
                db.flush()
                SafetyAuditService(db).append_execution_outcome(
                    outcome="succeeded",
                    target_id=target_id,
                    authorization_revision_id=revision_id,
                    test_case_id=action.test_case_id,
                    execution_plan_id=plan_id,
                    plan_action_id=action.id,
                    test_run=run,
                    code="http_execution_succeeded",
                    reason="HTTP execution completed.",
                )
                db.commit()
                committed_run_id.append(run.id)
            raise ExecutionClaimUnavailableError("private owner details")

        def release(self, handle):
            pytest.fail("unavailable contender attempted claim release")

    with SessionLocal() as db:
        action = db.scalar(
            select(PlanAction).where(PlanAction.execution_plan_id == plan_id)
        )
        assert action is not None
        test_case = db.get(TestCase, action.test_case_id)
        assert test_case is not None
        test_case.status = "pending"
        db.commit()
        assert db.scalar(
            select(TestRun).where(TestRun.execution_plan_id == plan_id)
        ) is None

    limiter = MutatingRateLimiter()
    gateway = RecordingGateway()
    result = execute(
        plan_id,
        limiter=limiter,
        gateway=gateway,
        claim_service=UnavailableAfterCommit(),
    )

    assert result.id == committed_run_id[0]
    assert acquire_calls == 1
    assert result.request_data == {"race": "active-owner"}
    assert limiter.calls == 0
    assert gateway.target_ids == []
    with SessionLocal() as db:
        assert db.get(TestCase, result.test_case_id).status == "pending"
        assert db.scalar(
            select(func.count(TestRun.id)).where(TestRun.execution_plan_id == plan_id)
        ) == 1
        assert db.scalar(
            select(func.count(SafetyDecisionRecord.id)).where(
                SafetyDecisionRecord.execution_plan_id == plan_id,
                SafetyDecisionRecord.test_run_id.is_not(None),
            )
        ) == 1
        claim = db.get(ExecutionPlanClaim, plan_id)
        assert claim is not None
        assert claim.owner_id == active.owner_id
        assert claim.fencing_generation == active.fencing_generation


def test_post_claim_canonical_lookup_failure_releases_claim_and_fails_closed(
    approved_plan: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id, _, _, _ = approved_plan
    delegate = ExecutionPlanClaimService(bind=engine, attempt_timeout_seconds=0.1)

    class TrackingClaims:
        def __init__(self) -> None:
            self.acquired = None
            self.released = []

        def acquire(self, *args, **kwargs):
            self.acquired = delegate.acquire(*args, **kwargs)
            return self.acquired

        def release(self, handle):
            delegate.release(handle)
            self.released.append(handle)

    claims = TrackingClaims()
    monkeypatch.setattr(
        PlanExecutionService,
        "_load_fresh_canonical",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            CanonicalResultLookupError("private database details")
        ),
    )
    limiter = MutatingRateLimiter()
    gateway = RecordingGateway()

    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id,
            limiter=limiter,
            gateway=gateway,
            claim_service=claims,
        )

    assert raised.value.code == "execution_plan_result_lookup_failed"
    assert "private" not in raised.value.reason
    assert limiter.calls == 0
    assert gateway.target_ids == []
    assert claims.released == [claims.acquired]
    with SessionLocal() as db:
        assert db.scalar(
            select(TestRun).where(TestRun.execution_plan_id == plan_id)
        ) is None
        claim = db.get(ExecutionPlanClaim, plan_id)
        assert claim is not None
        assert claim.owner_id is None


def test_claim_unavailable_canonical_lookup_failure_fails_closed(
    approved_plan: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id, _, _, _ = approved_plan

    class UnavailableClaims:
        def acquire(self, *args, **kwargs):
            raise ExecutionClaimUnavailableError("private owner details")

    monkeypatch.setattr(
        PlanExecutionService,
        "_load_fresh_canonical",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            CanonicalResultLookupError("private database details")
        ),
    )
    limiter = MutatingRateLimiter()
    gateway = RecordingGateway()

    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id,
            limiter=limiter,
            gateway=gateway,
            claim_service=UnavailableClaims(),
        )

    assert raised.value.code == "execution_plan_result_lookup_failed"
    assert "private" not in raised.value.reason
    assert limiter.calls == 0
    assert gateway.target_ids == []
    with SessionLocal() as db:
        assert db.scalar(
            select(TestRun).where(TestRun.execution_plan_id == plan_id)
        ) is None


def test_replay_does_not_mutate_status_or_duplicate_outcome_audit(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, target_id, revision_id, _ = approved_plan
    first = execute(plan_id, limiter=MutatingRateLimiter(), gateway=RecordingGateway())
    with SessionLocal() as db:
        test_case = db.get(TestCase, first.test_case_id)
        assert test_case is not None
        test_case.status = "pending"
        record_plan_decision(db, execution_plan_id=plan_id, decision="revoked")
        revision = db.get(AuthorizationRevision, revision_id)
        assert revision is not None
        revision.lifecycle_state = "revoked"
        scope = db.scalar(select(Scope).where(Scope.target_id == target_id))
        assert scope is not None
        scope.is_active = False
        db.commit()
        before = db.scalar(
            select(func.count(SafetyDecisionRecord.id)).where(
                SafetyDecisionRecord.execution_plan_id == plan_id,
                SafetyDecisionRecord.stage == "execution",
                SafetyDecisionRecord.test_run_id.is_not(None),
            )
        )

    replay = execute(plan_id, limiter=MutatingRateLimiter(), gateway=RecordingGateway())

    with SessionLocal() as db:
        assert db.get(TestCase, first.test_case_id).status == "pending"
        after = db.scalar(
            select(func.count(SafetyDecisionRecord.id)).where(
                SafetyDecisionRecord.execution_plan_id == plan_id,
                SafetyDecisionRecord.stage == "execution",
                SafetyDecisionRecord.test_run_id.is_not(None),
            )
        )
    assert replay.id == first.id
    assert before == after == 1


def test_two_plans_for_same_reusable_test_case_have_independent_results(
    approved_plan: tuple[int, int, int, int],
) -> None:
    first_plan_id, _, _, _ = approved_plan
    with SessionLocal() as db:
        first_plan = db.get(ExecutionPlan, first_plan_id)
        action = db.scalar(
            select(PlanAction).where(PlanAction.execution_plan_id == first_plan_id)
        )
        assert first_plan is not None and action is not None
        second = create_execution_plan(
            db,
            target_id=first_plan.target_id,
            authorization_revision_id=first_plan.authorization_revision_id,
            actor_identity_id=first_plan.actor_identity_id,
            credential_binding_id=first_plan.credential_binding_id,
            actions=[
                PlanActionInput(
                    action.method,
                    action.url,
                    action.test_case_id,
                    action.resource_id,
                )
            ],
            policy_context=first_plan.policy_context,
        )
        record_plan_decision(db, execution_plan_id=second.id, decision="approved")
        db.commit()
        second_plan_id = second.id

    try:
        first_run = execute(
            first_plan_id,
            limiter=MutatingRateLimiter(),
            gateway=RecordingGateway(),
        )
        second_run = execute(
            second_plan_id,
            limiter=MutatingRateLimiter(),
            gateway=RecordingGateway(),
        )
        assert first_run.id != second_run.id
        assert first_run.execution_plan_id == first_plan_id
        assert second_run.execution_plan_id == second_plan_id
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(SafetyDecisionRecord).where(
                    SafetyDecisionRecord.execution_plan_id == second_plan_id
                )
            )
            db.execute(delete(TestRun).where(TestRun.execution_plan_id == second_plan_id))
            db.execute(
                delete(ExecutionPlanApprovalRecord).where(
                    ExecutionPlanApprovalRecord.execution_plan_id == second_plan_id
                )
            )
            db.execute(
                delete(ExecutionPlanClaim).where(
                    ExecutionPlanClaim.execution_plan_id == second_plan_id
                )
            )
            db.execute(delete(PlanAction).where(PlanAction.execution_plan_id == second_plan_id))
            db.execute(delete(ExecutionPlan).where(ExecutionPlan.id == second_plan_id))
            db.commit()


def test_post_claim_recheck_returns_result_committed_after_initial_miss(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, revision_id, _ = approved_plan
    delegate = ExecutionPlanClaimService(bind=engine)

    class RacingClaims:
        def acquire(self, *args, **kwargs):
            handle = delegate.acquire(*args, **kwargs)
            with SessionLocal() as db:
                action = db.scalar(
                    select(PlanAction).where(PlanAction.execution_plan_id == plan_id)
                )
                assert action is not None
                db.add(
                    TestRun(
                        test_case_id=action.test_case_id,
                        authorization_revision_id=revision_id,
                        execution_plan_id=plan_id,
                        request_data={"race": "winner"},
                        response_status=200,
                    )
                )
                db.commit()
            return handle

        def release(self, handle):
            delegate.release(handle)

    limiter = MutatingRateLimiter()
    gateway = RecordingGateway()
    result = execute(
        plan_id,
        limiter=limiter,
        gateway=gateway,
        claim_service=RacingClaims(),
    )

    assert result.execution_plan_id == plan_id
    assert result.request_data == {"race": "winner"}
    assert limiter.calls == 0
    assert gateway.target_ids == []


def test_legacy_null_runs_remain_multiple_and_duplicate_plan_run_is_rejected(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, revision_id, _ = approved_plan
    with SessionLocal() as db:
        action = db.scalar(select(PlanAction).where(PlanAction.execution_plan_id == plan_id))
        assert action is not None
        values = dict(
            test_case_id=action.test_case_id,
            authorization_revision_id=revision_id,
            request_data={},
        )
        db.add_all([TestRun(**values), TestRun(**values)])
        db.commit()
        assert db.scalar(
            select(func.count(TestRun.id)).where(
                TestRun.test_case_id == action.test_case_id,
                TestRun.execution_plan_id.is_(None),
            )
        ) == 2
        db.add(TestRun(**values, execution_plan_id=plan_id))
        db.commit()
        db.add(TestRun(**values, execution_plan_id=plan_id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.scalar(
            select(func.count(TestRun.id)).where(TestRun.execution_plan_id == plan_id)
        ) == 1


def test_stale_generation_after_network_cannot_persist_result(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine, attempt_timeout_seconds=0.1)

    class TakeoverGateway(RecordingGateway):
        def request(self, **kwargs):
            result = super().request(**kwargs)
            time.sleep(0.08)
            claims.acquire(plan_id, "new-owner", lease_seconds=2)
            return result

    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id,
            limiter=MutatingRateLimiter(),
            gateway=TakeoverGateway(),
            claim_service=claims,
            claim_lease_seconds=0.05,
        )

    assert raised.value.code == "execution_plan_result_fencing_lost"
    with SessionLocal() as db:
        assert db.scalar(select(TestRun).where(TestRun.execution_plan_id == plan_id)) is None
        audit = db.scalar(
            select(SafetyDecisionRecord).where(
                SafetyDecisionRecord.execution_plan_id == plan_id,
                SafetyDecisionRecord.code == "execution_plan_result_fencing_lost",
            )
        )
        assert audit is not None
        assert audit.outcome == "failed"


def test_result_fencing_coordination_failure_is_sanitized_and_writes_no_result(
    approved_plan: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine)
    monkeypatch.setattr(
        claims,
        "assert_current",
        lambda handle, **kwargs: (_ for _ in ()).throw(
            ExecutionClaimCoordinationError("private database details")
        ),
    )

    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id,
            limiter=MutatingRateLimiter(),
            gateway=RecordingGateway(),
            claim_service=claims,
        )

    assert raised.value.code == "execution_plan_result_fencing_failed"
    assert "private" not in raised.value.reason
    with SessionLocal() as db:
        assert db.scalar(select(TestRun).where(TestRun.execution_plan_id == plan_id)) is None
        audit = db.scalar(
            select(SafetyDecisionRecord).where(
                SafetyDecisionRecord.execution_plan_id == plan_id,
                SafetyDecisionRecord.code == "execution_plan_result_fencing_failed",
            )
        )
        assert audit is not None
        assert audit.outcome == "failed"


def _synthetic_integrity_error(constraint_name: str) -> IntegrityError:
    original = RuntimeError("private PostgreSQL details")
    original.sqlstate = "23505"
    original.diag = SimpleNamespace(constraint_name=constraint_name)
    return IntegrityError("private SQL", {}, original)


@pytest.mark.parametrize(
    ("constraint_name", "returns_canonical"),
    [
        ("uq_test_runs_execution_plan_id", True),
        ("some_unrelated_constraint", False),
    ],
)
def test_only_exact_plan_unique_conflict_is_recovered_as_replay(
    approved_plan: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
    constraint_name: str,
    returns_canonical: bool,
) -> None:
    plan_id, target_id, revision_id, _ = approved_plan
    with SessionLocal() as db:
        action = db.scalar(
            select(PlanAction).where(PlanAction.execution_plan_id == plan_id)
        )
        assert action is not None
        test_case = db.get(TestCase, action.test_case_id)
        assert test_case is not None
        canonical = TestRun(
            test_case_id=test_case.id,
            authorization_revision_id=revision_id,
            execution_plan_id=plan_id,
            request_data={"canonical": True},
            response_status=200,
        )
        db.add(canonical)
        db.commit()
        canonical_id = canonical.id
        claims = SimpleNamespace(assert_current=lambda *args, **kwargs: None)
        service = PlanExecutionService(
            db=db, executor=Mock(), claim_service=claims
        )
        monkeypatch.setattr(
            db,
            "flush",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                _synthetic_integrity_error(constraint_name)
            ),
        )

        if returns_canonical:
            result = service._finish(
                claim_handle=object(),
                test_case=test_case,
                request_data={},
                target_id=target_id,
                revision_id=revision_id,
                plan_id=plan_id,
                action_id=action.id,
                outcome="succeeded",
                response_status=201,
            )
            assert result.id == canonical_id
        else:
            with pytest.raises(ExecutionBlockedError) as raised:
                service._finish(
                    claim_handle=object(),
                    test_case=test_case,
                    request_data={},
                    target_id=target_id,
                    revision_id=revision_id,
                    plan_id=plan_id,
                    action_id=action.id,
                    outcome="succeeded",
                    response_status=201,
                )
            assert raised.value.code == "execution_plan_result_persistence_failed"
            assert "private" not in raised.value.reason
        assert db.scalar(
            select(func.count(TestRun.id)).where(TestRun.execution_plan_id == plan_id)
        ) == 1


def test_outcome_audit_failure_rolls_back_result_before_exact_claim_release(
    approved_plan: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id, _, _, _ = approved_plan
    delegate = ExecutionPlanClaimService(bind=engine, attempt_timeout_seconds=0.1)

    class TrackingClaims:
        def __init__(self) -> None:
            self.handle = None
            self.released = []

        def acquire(self, *args, **kwargs):
            self.handle = delegate.acquire(*args, **kwargs)
            return self.handle

        def renew(self, *args, **kwargs):
            self.handle = delegate.renew(*args, **kwargs)
            return self.handle

        def assert_current(self, handle, **kwargs):
            return delegate.assert_current(handle, **kwargs)

        def release(self, handle):
            delegate.release(handle)
            self.released.append(handle)

    claims = TrackingClaims()
    original = SafetyAuditService.append_execution_outcome

    def fail_terminal_outcome(self, **kwargs):
        if kwargs.get("test_run") is not None:
            raise RuntimeError("private audit persistence details")
        return original(self, **kwargs)

    monkeypatch.setattr(
        SafetyAuditService, "append_execution_outcome", fail_terminal_outcome
    )
    gateway = RecordingGateway()

    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id,
            limiter=MutatingRateLimiter(),
            gateway=gateway,
            claim_service=claims,
        )

    assert raised.value.code == "execution_plan_result_persistence_failed"
    assert "private" not in raised.value.reason
    assert len(gateway.target_ids) == 1
    assert claims.released == [claims.handle]
    with SessionLocal() as db:
        assert db.scalar(select(TestRun).where(TestRun.execution_plan_id == plan_id)) is None
        claim = db.get(ExecutionPlanClaim, plan_id)
        assert claim is not None
        assert claim.owner_id is None
        assert not db.scalars(
            select(SafetyDecisionRecord).where(
                SafetyDecisionRecord.execution_plan_id == plan_id,
                SafetyDecisionRecord.code.in_(
                    ["http_execution_succeeded", "http_execution_failed"]
                ),
            )
        ).all()
        failure = db.scalar(
            select(SafetyDecisionRecord).where(
                SafetyDecisionRecord.execution_plan_id == plan_id,
                SafetyDecisionRecord.code
                == "execution_plan_result_persistence_failed",
            )
        )
        assert failure is not None
        assert failure.outcome == "failed"
