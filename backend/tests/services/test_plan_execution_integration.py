from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.db.models import (
    AuthorizationProfile,
    AuthorizationRevision,
    Endpoint,
    ExecutionPlan,
    ExecutionPlanApprovalRecord,
    ExecutionPlanClaim,
    PlanAction,
    RateReservationState,
    Resource,
    SafetyDecisionRecord,
    Scope,
    Target,
    TestCase,
    TestIdentity,
    TestRun,
)
from app.db.session import SessionLocal, engine
from app.executors.http import ExecutionBlockedError, PolicyEnforcedHTTPExecutor
from app.executors.rate_limit import PostgresRateLimiter, RateLimiter
from app.policies.scope_policy import ScopePolicyEngine
from app.services.execution_plan_approval import record_plan_decision
from app.services.execution_plan_claim import (
    ExecutionClaimCoordinationError,
    ExecutionPlanClaimService,
)
from app.services.plan_execution import PlanExecutionService
from app.services.safety_audit import SafetyAuditService
from app.services.test_case_planning import create_test_case_execution_plan
from tests.services.test_test_case_planning import build_graph


class MutatingRateLimiter:
    def __init__(self, mutation=None) -> None:
        self.mutation = mutation
        self.calls = 0

    def wait(self, **kwargs) -> None:
        self.calls += 1
        if self.mutation is not None:
            self.mutation()


class RecordingGateway:
    def __init__(self) -> None:
        self.target_ids: list[int] = []

    def request(self, **kwargs):
        self.target_ids.append(kwargs["target_id"])
        return SimpleNamespace(status_code=200, body=b"{}", duration_ms=1)


@pytest.fixture
def approved_plan() -> tuple[int, int, int, int]:
    with SessionLocal() as db:
        graph = build_graph(db, actor_auth_type="anonymous")
        revision = graph["revision"]
        target = graph["target"]
        test_case = graph["test_case"]
        profile = graph["profile"]
        assert isinstance(revision, AuthorizationRevision)
        assert isinstance(target, Target)
        assert isinstance(test_case, TestCase)
        assert isinstance(profile, AuthorizationProfile)
        revision.require_human_execution_approval = True
        plan = create_test_case_execution_plan(
            db, test_case_id=test_case.id, credential_binding_id=None
        )
        record_plan_decision(db, execution_plan_id=plan.id, decision="approved")
        db.commit()
        values = plan.id, target.id, revision.id, profile.id

    try:
        yield values
    finally:
        plan_id, target_id, _, profile_id = values
        with SessionLocal() as db:
            test_case_ids = select(TestCase.id).join(Endpoint).where(
                Endpoint.target_id == target_id
            )
            db.execute(
                delete(SafetyDecisionRecord).where(
                    SafetyDecisionRecord.target_id == target_id
                )
            )
            db.execute(
                delete(RateReservationState).where(
                    RateReservationState.key == f"target:{target_id}"
                )
            )
            db.execute(
                delete(ExecutionPlanApprovalRecord).where(
                    ExecutionPlanApprovalRecord.execution_plan_id == plan_id
                )
            )
            db.execute(
                delete(ExecutionPlanClaim).where(
                    ExecutionPlanClaim.execution_plan_id == plan_id
                )
            )
            db.execute(delete(TestRun).where(TestRun.test_case_id.in_(test_case_ids)))
            db.execute(delete(PlanAction).where(PlanAction.execution_plan_id == plan_id))
            db.execute(delete(ExecutionPlan).where(ExecutionPlan.id == plan_id))
            db.execute(delete(TestCase).where(TestCase.id.in_(test_case_ids)))
            db.execute(delete(Resource).where(Resource.target_id == target_id))
            db.execute(delete(Scope).where(Scope.target_id == target_id))
            db.execute(delete(Endpoint).where(Endpoint.target_id == target_id))
            db.execute(delete(TestIdentity).where(TestIdentity.target_id == target_id))
            db.execute(delete(Target).where(Target.id == target_id))
            db.execute(
                delete(AuthorizationRevision).where(
                    AuthorizationRevision.authorization_profile_id == profile_id
                )
            )
            db.execute(
                delete(AuthorizationProfile).where(
                    AuthorizationProfile.id == profile_id
                )
            )
            db.commit()


def execute(
    plan_id: int,
    *,
    limiter: RateLimiter,
    gateway: RecordingGateway,
    claim_service: ExecutionPlanClaimService | None = None,
    claim_lease_seconds: float = 30.0,
) -> None:
    with SessionLocal() as db:
        PlanExecutionService(
            db=db,
            claim_service=claim_service,
            claim_lease_seconds=claim_lease_seconds,
            executor=PolicyEnforcedHTTPExecutor(
                policy_engine=ScopePolicyEngine({"example.test"}),
                rate_limiter=limiter,
                network_gateway=gateway,
            ),
        ).execute(execution_plan_id=plan_id)


def shared_limiter_with_wait_mutation(target_id: int, mutation) -> PostgresRateLimiter:
    limiter = PostgresRateLimiter(
        requests_per_second=100.0,
        bind=engine,
        sleep=lambda delay: mutation(),
    )
    limiter.reserve_delay(
        key=f"target:{target_id}",
        requested_requests_per_second=1.0,
    )
    return limiter


def test_approval_revoked_during_rate_wait_blocks_gateway(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, target_id, _, _ = approved_plan

    def revoke() -> None:
        with SessionLocal() as db:
            record_plan_decision(
                db, execution_plan_id=plan_id, decision="revoked"
            )
            db.commit()

    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id,
            limiter=shared_limiter_with_wait_mutation(target_id, revoke),
            gateway=gateway,
        )

    assert raised.value.code == "execution_plan_approval_changed"
    assert gateway.target_ids == []


@pytest.mark.parametrize("change", ["rebind", "revision_inactive"])
def test_authorization_change_during_rate_wait_blocks_gateway(
    approved_plan: tuple[int, int, int, int], change: str
) -> None:
    plan_id, target_id, revision_id, profile_id = approved_plan

    def mutate() -> None:
        with SessionLocal() as db:
            revision = db.get(AuthorizationRevision, revision_id)
            assert revision is not None
            revision.lifecycle_state = "superseded"
            if change == "rebind":
                replacement = AuthorizationRevision(
                    authorization_profile_id=profile_id,
                    revision_number=2,
                    lifecycle_state="active",
                    name=f"replacement-{uuid4()}",
                    program_name=revision.program_name,
                    authorization_type=revision.authorization_type,
                    automation_allowed=True,
                    max_requests_per_second=1.0,
                    allow_get=True,
                    require_human_execution_approval=True,
                )
                db.add(replacement)
                db.flush()
                target = db.get(Target, target_id)
                assert target is not None
                target.authorization_revision_id = replacement.id
            db.commit()

    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError):
        execute(
            plan_id,
            limiter=shared_limiter_with_wait_mutation(target_id, mutate),
            gateway=gateway,
        )

    assert gateway.target_ids == []


def test_scope_narrowing_during_rate_wait_blocks_gateway(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, target_id, _, _ = approved_plan

    def narrow_scope() -> None:
        with SessionLocal() as db:
            scope = db.scalar(select(Scope).where(Scope.target_id == target_id))
            assert scope is not None
            scope.is_active = False
            db.commit()

    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id,
            limiter=shared_limiter_with_wait_mutation(target_id, narrow_scope),
            gateway=gateway,
        )

    assert raised.value.code == "no_matching_scope"
    assert gateway.target_ids == []


def test_success_audits_exact_plan_action_and_uses_refreshed_target(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, target_id, _, _ = approved_plan
    gateway = RecordingGateway()

    execute(plan_id, limiter=MutatingRateLimiter(), gateway=gateway)

    with SessionLocal() as db:
        action_id = db.scalar(
            select(PlanAction.id).where(PlanAction.execution_plan_id == plan_id)
        )
        final_policy = db.scalar(
            select(SafetyDecisionRecord)
            .where(
                SafetyDecisionRecord.stage == "policy",
                SafetyDecisionRecord.execution_plan_id == plan_id,
            )
            .order_by(SafetyDecisionRecord.id.desc())
        )
        assert final_policy is not None
        assert final_policy.plan_action_id == action_id
        assert final_policy.outcome == "allowed"
    assert gateway.target_ids == [target_id]


def test_audit_failure_blocks_gateway(
    approved_plan: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id, _, _, _ = approved_plan

    def fail_audit(*args, **kwargs):
        raise RuntimeError("synthetic policy audit failure")

    monkeypatch.setattr(SafetyAuditService, "append_policy_decision", fail_audit)
    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(plan_id, limiter=MutatingRateLimiter(), gateway=gateway)

    assert raised.value.code == "safety_audit_persistence_failed"
    assert gateway.target_ids == []


def test_external_public_mode_remains_blocked_before_gateway(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, target_id, _, _ = approved_plan
    with SessionLocal() as db:
        target = db.get(Target, target_id)
        assert target is not None
        target.network_mode = "external_public_authorized"
        db.commit()
    gateway = RecordingGateway()

    with pytest.raises(ExecutionBlockedError) as raised:
        execute(plan_id, limiter=MutatingRateLimiter(), gateway=gateway)

    assert raised.value.code == "external_network_mode_not_ready"
    assert gateway.target_ids == []


def test_expired_claim_taken_over_during_rate_wait_fences_stale_worker(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine, attempt_timeout_seconds=0.1)

    def take_over() -> None:
        import time

        time.sleep(0.08)
        claims.acquire(plan_id, "takeover-owner", lease_seconds=2)

    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id,
            limiter=MutatingRateLimiter(take_over),
            gateway=gateway,
            claim_service=claims,
            claim_lease_seconds=0.05,
        )

    assert raised.value.code == "execution_plan_claim_lost"
    assert gateway.target_ids == []
    with SessionLocal() as db:
        claim = db.get(ExecutionPlanClaim, plan_id)
        assert claim is not None
        assert claim.owner_id == "takeover-owner"
        assert claim.fencing_generation == 2


def test_claim_renew_coordination_failure_blocks_gateway(
    approved_plan: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine, attempt_timeout_seconds=0.1)

    def fail_renew(*args, **kwargs):
        raise ExecutionClaimCoordinationError("private database details")

    monkeypatch.setattr(claims, "renew", fail_renew)
    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id,
            limiter=MutatingRateLimiter(),
            gateway=gateway,
            claim_service=claims,
        )

    assert raised.value.code == "execution_plan_claim_coordination_failed"
    assert "private database details" not in raised.value.reason
    assert gateway.target_ids == []
