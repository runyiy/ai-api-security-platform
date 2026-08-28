from types import SimpleNamespace
import time
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text

from app.db.models import (
    AuthorizationProfile,
    AuthorizationRevision,
    Endpoint,
    ExecutionPlan,
    ExecutionPlanApprovalRecord,
    ExecutionPlanCancellation,
    ExecutionPlanClaim,
    ExecutionPlanProgress,
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
from app.network_safety.gateway import NetworkGatewayError
from app.policies.scope_policy import ScopePolicyEngine
from app.services.execution_plan_approval import record_plan_decision
from app.services.execution_plan_claim import (
    ExecutionClaimCoordinationError,
    ExecutionPlanClaimService,
)
from app.services.execution_plan_cancellation import ExecutionPlanCancellationService
from app.services.execution_plan_progress import (
    ExecutionPlanProgressService,
    ExecutionProgressCoordinationError,
    ExecutionProgressLostError,
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


class FailingGateway(RecordingGateway):
    def request(self, **kwargs):
        self.target_ids.append(kwargs["target_id"])
        raise NetworkGatewayError(code="synthetic_network_failure", reason="failed")


class ReleaseCoordinationFailureClaims:
    def __init__(self) -> None:
        self.delegate = ExecutionPlanClaimService(
            bind=engine, attempt_timeout_seconds=0.1
        )
        self.acquired = []
        self.renewed = []
        self.release_attempts = []

    def acquire(self, *args, **kwargs):
        handle = self.delegate.acquire(*args, **kwargs)
        self.acquired.append(handle)
        return handle

    def renew(self, *args, **kwargs):
        handle = self.delegate.renew(*args, **kwargs)
        self.renewed.append(handle)
        return handle

    def assert_current(self, handle, **kwargs) -> None:
        self.delegate.assert_current(handle, **kwargs)

    def release(self, handle) -> None:
        self.release_attempts.append(handle)
        raise ExecutionClaimCoordinationError("private owner and database details")


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
                delete(ExecutionPlanCancellation).where(
                    ExecutionPlanCancellation.execution_plan_id == plan_id
                )
            )
            db.execute(
                delete(ExecutionPlanProgress).where(
                    ExecutionPlanProgress.execution_plan_id == plan_id
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
    progress_service: ExecutionPlanProgressService | None = None,
    cancellation_service: ExecutionPlanCancellationService | None = None,
) -> TestRun:
    with SessionLocal() as db:
        return PlanExecutionService(
            db=db,
            claim_service=claim_service,
            claim_lease_seconds=claim_lease_seconds,
            progress_service=progress_service,
            cancellation_service=cancellation_service,
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
        requested_requests_per_second=0.1,
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
    with SessionLocal() as db:
        assert db.scalar(select(TestRun).where(TestRun.execution_plan_id == plan_id)) is None
        assert db.get(ExecutionPlanProgress, plan_id).phase == "pre_network"


def test_blocked_pre_network_approval_is_retryable_by_higher_generation(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, target_id, _, _ = approved_plan

    def revoke() -> None:
        with SessionLocal() as db:
            record_plan_decision(db, execution_plan_id=plan_id, decision="revoked")
            db.commit()

    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError):
        execute(
            plan_id,
            limiter=MutatingRateLimiter(revoke),
            gateway=gateway,
        )

    with SessionLocal() as db:
        progress = db.get(ExecutionPlanProgress, plan_id)
        assert progress is not None
        assert progress.phase == "pre_network"
        assert progress.fencing_generation == 1
        record_plan_decision(db, execution_plan_id=plan_id, decision="approved")
        db.commit()

    result = execute(
        plan_id,
        limiter=MutatingRateLimiter(),
        gateway=gateway,
    )
    assert result.response_status == 200
    assert gateway.target_ids == [target_id]
    with SessionLocal() as db:
        progress = db.get(ExecutionPlanProgress, plan_id)
        assert progress is not None
        assert progress.fencing_generation == 2
        assert progress.phase == "network_started"
        assert db.scalars(
            select(TestRun).where(TestRun.execution_plan_id == plan_id)
        ).all() == [db.get(TestRun, result.id)]


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
    with SessionLocal() as db:
        assert db.scalar(select(TestRun).where(TestRun.execution_plan_id == plan_id)) is None
        assert db.get(ExecutionPlanProgress, plan_id).phase == "pre_network"


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
    with SessionLocal() as db:
        assert db.scalar(select(TestRun).where(TestRun.execution_plan_id == plan_id)) is None
        assert db.get(ExecutionPlanProgress, plan_id).phase == "pre_network"


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
    with SessionLocal() as db:
        assert db.scalar(select(TestRun).where(TestRun.execution_plan_id == plan_id)) is None
        progress = db.get(ExecutionPlanProgress, plan_id)
        assert progress is not None
        assert progress.phase == "pre_network"


def test_network_marker_and_final_audit_commit_before_unlocked_gateway(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan

    events: list[str] = []
    delegate = ExecutionPlanProgressService(bind=engine)

    class TrackingProgress:
        def prepare_attempt(self, handle):
            return delegate.prepare_attempt(handle)

        def mark_network_started(self, handle):
            with SessionLocal() as db:
                final_audit = db.scalar(
                    select(SafetyDecisionRecord).where(
                        SafetyDecisionRecord.execution_plan_id == plan_id,
                        SafetyDecisionRecord.stage == "policy",
                        SafetyDecisionRecord.outcome == "allowed",
                    )
                )
                assert final_audit is not None
            events.append("audit_visible_inside_marker")
            state = delegate.mark_network_started(handle)
            with SessionLocal() as db:
                assert db.get(ExecutionPlanProgress, plan_id).phase == "network_started"
            events.append("marker_committed")
            return state

    class InspectingGateway(RecordingGateway):
        def request(self, **kwargs):
            with SessionLocal() as db:
                progress = db.execute(
                    text(
                        "SELECT phase FROM execution_plan_progress "
                        "WHERE execution_plan_id=:plan_id FOR UPDATE NOWAIT"
                    ),
                    {"plan_id": plan_id},
                ).one()
                assert progress.phase == "network_started"
                final_audit = db.scalar(
                    select(SafetyDecisionRecord).where(
                        SafetyDecisionRecord.execution_plan_id == plan_id,
                        SafetyDecisionRecord.stage == "policy",
                        SafetyDecisionRecord.outcome == "allowed",
                    )
                )
                assert final_audit is not None
                db.commit()
            events.append("gateway")
            return super().request(**kwargs)

    gateway = InspectingGateway()
    result = execute(
        plan_id,
        limiter=MutatingRateLimiter(),
        gateway=gateway,
        progress_service=TrackingProgress(),
    )
    assert result.response_status == 200
    assert len(gateway.target_ids) == 1
    assert events == ["audit_visible_inside_marker", "marker_committed", "gateway"]


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            ExecutionProgressLostError("private owner fencing details"),
            "execution_plan_progress_lost",
        ),
        (
            ExecutionProgressCoordinationError("private database details"),
            "execution_plan_progress_coordination_failed",
        ),
    ],
)
def test_prepare_progress_failure_is_sanitized_distinct_and_releases_claim(
    approved_plan: tuple[int, int, int, int],
    error: Exception,
    expected_code: str,
) -> None:
    plan_id, _, _, _ = approved_plan

    class FailingPrepare:
        def prepare_attempt(self, handle):
            raise error

        def mark_network_started(self, handle):
            raise AssertionError("marker must not run")

    limiter = MutatingRateLimiter()
    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id,
            limiter=limiter,
            gateway=gateway,
            progress_service=FailingPrepare(),
        )
    assert raised.value.code == expected_code
    assert "private" not in raised.value.reason
    assert limiter.calls == 0
    assert gateway.target_ids == []
    with SessionLocal() as db:
        claim = db.get(ExecutionPlanClaim, plan_id)
        assert claim is not None
        assert claim.owner_id is None


def test_progress_row_is_unlocked_during_rate_wait(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    observed: list[str] = []

    def lock_progress() -> None:
        with SessionLocal() as db:
            row = db.execute(
                text(
                    "SELECT phase FROM execution_plan_progress "
                    "WHERE execution_plan_id=:plan_id FOR UPDATE NOWAIT"
                ),
                {"plan_id": plan_id},
            ).one()
            observed.append(row.phase)
            db.commit()

    result = execute(
        plan_id,
        limiter=MutatingRateLimiter(lock_progress),
        gateway=RecordingGateway(),
    )
    assert result.response_status == 200
    assert observed == ["pre_network"]


def test_network_marker_coordination_failure_calls_zero_gateway(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    delegate = ExecutionPlanProgressService(bind=engine)

    class FailingMarker:
        def prepare_attempt(self, handle):
            return delegate.prepare_attempt(handle)

        def mark_network_started(self, handle):
            raise ExecutionProgressCoordinationError("private database details")

    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id,
            limiter=MutatingRateLimiter(),
            gateway=gateway,
            progress_service=FailingMarker(),
        )
    assert raised.value.code == "execution_plan_progress_coordination_failed"
    assert "private" not in raised.value.reason
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


def test_expired_old_owner_is_not_blocked_by_stale_running_test_case(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine, attempt_timeout_seconds=0.1)
    first = claims.acquire(plan_id, "dead-worker", lease_seconds=0.05)
    with SessionLocal() as db:
        action = db.scalar(
            select(PlanAction).where(PlanAction.execution_plan_id == plan_id)
        )
        assert action is not None
        test_case = db.get(TestCase, action.test_case_id)
        assert test_case is not None
        test_case.status = "running"
        db.commit()
    time.sleep(0.08)

    result = execute(
        plan_id,
        limiter=MutatingRateLimiter(),
        gateway=RecordingGateway(),
        claim_service=claims,
    )

    assert result.response_status == 200
    with SessionLocal() as db:
        claim = db.get(ExecutionPlanClaim, plan_id)
        assert claim is not None
        assert claim.fencing_generation == first.fencing_generation + 1
        assert claim.owner_id is None


def test_claim_is_committed_and_unlocked_before_rate_wait(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    observed: list[tuple[str | None, int]] = []

    def inspect_claim_during_wait() -> None:
        with SessionLocal() as db:
            row = db.execute(
                text(
                    "SELECT owner_id, fencing_generation "
                    "FROM execution_plan_claims "
                    "WHERE execution_plan_id=:plan_id FOR UPDATE NOWAIT"
                ),
                {"plan_id": plan_id},
            ).one()
            observed.append((row.owner_id, row.fencing_generation))
            db.commit()

    result = execute(
        plan_id,
        limiter=MutatingRateLimiter(inspect_claim_during_wait),
        gateway=RecordingGateway(),
    )

    assert result.response_status == 200
    assert len(observed) == 1
    assert observed[0][0]
    assert observed[0][1] == 1


@pytest.mark.parametrize(
    ("path", "expected_status", "expected_block_code"),
    [
        ("success", 200, None),
        ("http_failed", None, None),
        ("blocked", None, "external_network_mode_not_ready"),
    ],
)
def test_release_coordination_failure_preserves_original_outcome_and_is_audited(
    approved_plan: tuple[int, int, int, int],
    path: str,
    expected_status: int | None,
    expected_block_code: str | None,
) -> None:
    plan_id, target_id, _, _ = approved_plan
    claims = ReleaseCoordinationFailureClaims()
    gateway: RecordingGateway = (
        FailingGateway() if path == "http_failed" else RecordingGateway()
    )
    if path == "blocked":
        with SessionLocal() as db:
            target = db.get(Target, target_id)
            assert target is not None
            target.network_mode = "external_public_authorized"
            db.commit()

    if expected_block_code is not None:
        with pytest.raises(ExecutionBlockedError) as raised:
            execute(
                plan_id,
                limiter=MutatingRateLimiter(),
                gateway=gateway,
                claim_service=claims,
            )
        assert raised.value.code == expected_block_code
    else:
        result = execute(
            plan_id,
            limiter=MutatingRateLimiter(),
            gateway=gateway,
            claim_service=claims,
        )
        assert result.response_status == expected_status
        if path == "http_failed":
            assert result.error_message == "synthetic_network_failure: failed"

    assert len(claims.release_attempts) == 1
    assert len(claims.renewed) == 1
    assert claims.release_attempts[0] == claims.renewed[0]
    assert (
        claims.release_attempts[0].execution_plan_id,
        claims.release_attempts[0].owner_id,
        claims.release_attempts[0].fencing_generation,
    ) == (
        claims.acquired[0].execution_plan_id,
        claims.acquired[0].owner_id,
        claims.acquired[0].fencing_generation,
    )
    with SessionLocal() as db:
        action = db.scalar(
            select(PlanAction).where(PlanAction.execution_plan_id == plan_id)
        )
        assert action is not None
        cleanup = db.scalar(
            select(SafetyDecisionRecord)
            .where(
                SafetyDecisionRecord.execution_plan_id == plan_id,
                SafetyDecisionRecord.code
                == "execution_plan_claim_cleanup_failed",
            )
            .order_by(SafetyDecisionRecord.id.desc())
        )
        assert cleanup is not None
        assert cleanup.reason == "ExecutionPlan claim cleanup could not be completed."
        assert "private" not in cleanup.reason
        claim = db.get(ExecutionPlanClaim, plan_id)
        assert claim is not None
        assert claim.owner_id == claims.acquired[0].owner_id
        assert claim.fencing_generation == claims.acquired[0].fencing_generation
        if path != "blocked":
            persisted_run = db.scalar(
                select(TestRun)
                .where(TestRun.test_case_id == action.test_case_id)
                .order_by(TestRun.id.desc())
            )
            assert persisted_run is not None
            assert persisted_run.response_status == expected_status
            assert persisted_run.error_message == (
                "synthetic_network_failure: failed"
                if path == "http_failed"
                else None
            )
