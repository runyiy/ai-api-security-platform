import time

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
    ExecutionPlanClaimService,
)
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

    replay = execute(plan_id, limiter=limiter, gateway=gateway)

    assert replay.id == first.id
    assert replay.error_message == "synthetic_network_failure: failed"
    assert limiter.calls == 0
    assert gateway.target_ids == []


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
