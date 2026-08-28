import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import time

import pytest
from sqlalchemy import delete, select, text

from app.db.models import (
    AuthorizationProfile,
    AuthorizationRevision,
    CredentialBinding,
    Endpoint,
    ExecutionPlan,
    ExecutionPlanApprovalRecord,
    ExecutionPlanCancellation,
    ExecutionPlanClaim,
    ExecutionPlanProgress,
    PlanAction,
    Resource,
    SafetyDecisionRecord,
    Scope,
    Target,
    TestCase,
    TestIdentity,
    TestRun,
)
from app.db.session import SessionLocal, engine
from app.credentials.bearer import BearerCredentialService
from app.executors.http import ExecutionBlockedError
from app.services.execution_plan_cancellation import (
    ExecutionPlanAlreadyCompletedError,
    ExecutionPlanCancellationCoordinationError,
    ExecutionPlanCancellationInDoubtError,
    ExecutionPlanCancellationNotFoundError,
    ExecutionPlanCancellationService,
)
from app.services.execution_plan_approval import record_plan_decision
from app.services.execution_plan_claim import ExecutionPlanClaimService
from app.services.execution_plan_progress import (
    ExecutionPlanProgressService,
    ExecutionProgressCancelledError,
    ExecutionProgressCoordinationError,
    ExecutionProgressLostError,
)
from app.services.test_case_planning import create_test_case_execution_plan
from tests.services.test_test_case_planning import build_graph
from tests.services.test_plan_execution_integration import (
    FailingGateway,
    MutatingRateLimiter,
    RecordingGateway,
    approved_plan,
    execute,
)


def cancellation_service(**kwargs):
    return ExecutionPlanCancellationService(
        bind=engine, attempt_timeout_seconds=0.05, **kwargs
    )


@pytest.fixture
def cancelled_bearer_plan() -> int:
    with SessionLocal() as db:
        graph = build_graph(db, actor_auth_type="bearer")
        actor = graph["actor"]
        revision = graph["revision"]
        target = graph["target"]
        test_case = graph["test_case"]
        profile = graph["profile"]
        revision.require_human_execution_approval = True
        binding = CredentialBinding(
            test_identity_id=actor.id,
            auth_type="bearer",
            source_type="stored_secret",
            is_active=True,
        )
        db.add(binding)
        db.flush()
        plan = create_test_case_execution_plan(
            db,
            test_case_id=test_case.id,
            credential_binding_id=binding.id,
        )
        record_plan_decision(db, execution_plan_id=plan.id, decision="approved")
        db.commit()
        values = plan.id, target.id, profile.id, binding.id
    cancellation_service().request_cancel(values[0])
    try:
        yield values[0]
    finally:
        plan_id, target_id, profile_id, binding_id = values
        with SessionLocal() as db:
            test_case_ids = select(TestCase.id).join(Endpoint).where(
                Endpoint.target_id == target_id
            )
            db.execute(delete(SafetyDecisionRecord).where(
                SafetyDecisionRecord.target_id == target_id
            ))
            db.execute(delete(ExecutionPlanApprovalRecord).where(
                ExecutionPlanApprovalRecord.execution_plan_id == plan_id
            ))
            db.execute(delete(ExecutionPlanCancellation).where(
                ExecutionPlanCancellation.execution_plan_id == plan_id
            ))
            db.execute(delete(ExecutionPlanProgress).where(
                ExecutionPlanProgress.execution_plan_id == plan_id
            ))
            db.execute(delete(ExecutionPlanClaim).where(
                ExecutionPlanClaim.execution_plan_id == plan_id
            ))
            db.execute(delete(TestRun).where(TestRun.test_case_id.in_(test_case_ids)))
            db.execute(delete(PlanAction).where(PlanAction.execution_plan_id == plan_id))
            db.execute(delete(ExecutionPlan).where(ExecutionPlan.id == plan_id))
            db.execute(delete(CredentialBinding).where(CredentialBinding.id == binding_id))
            db.execute(delete(TestCase).where(TestCase.id.in_(test_case_ids)))
            db.execute(delete(Resource).where(Resource.target_id == target_id))
            db.execute(delete(Scope).where(Scope.target_id == target_id))
            db.execute(delete(Endpoint).where(Endpoint.target_id == target_id))
            db.execute(delete(TestIdentity).where(TestIdentity.target_id == target_id))
            db.execute(delete(Target).where(Target.id == target_id))
            db.execute(delete(AuthorizationRevision).where(
                AuthorizationRevision.authorization_profile_id == profile_id
            ))
            db.execute(delete(AuthorizationProfile).where(
                AuthorizationProfile.id == profile_id
            ))
            db.commit()


def test_safe_cancellation_uses_db_time_and_is_idempotent(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    service = cancellation_service()
    with engine.connect() as db:
        before = db.scalar(select(text("clock_timestamp()")))
    first = service.request_cancel(plan_id)
    second = service.request_cancel(plan_id)
    with engine.connect() as db:
        after = db.scalar(select(text("clock_timestamp()")))
    assert first == second
    assert before <= first.requested_at <= after
    with SessionLocal() as db:
        assert len(db.scalars(
            select(ExecutionPlanCancellation).where(
                ExecutionPlanCancellation.execution_plan_id == plan_id
            )
        ).all()) == 1


def test_unknown_plan_is_not_found() -> None:
    unknown_id = 2_147_483_647
    with pytest.raises(ExecutionPlanCancellationNotFoundError):
        cancellation_service().request_cancel(unknown_id)
    with SessionLocal() as db:
        assert db.get(ExecutionPlanCancellation, unknown_id) is None


@pytest.mark.parametrize("gateway", [RecordingGateway(), FailingGateway()])
def test_completed_plan_cannot_be_cancelled(approved_plan, gateway) -> None:
    plan_id, _, _, _ = approved_plan
    canonical = execute(
        plan_id, limiter=MutatingRateLimiter(), gateway=gateway
    )
    with pytest.raises(ExecutionPlanAlreadyCompletedError):
        cancellation_service().request_cancel(plan_id)
    with SessionLocal() as db:
        assert db.get(TestRun, canonical.id).id == canonical.id
        assert db.get(ExecutionPlanCancellation, plan_id) is None


def test_network_started_cannot_become_cancelled(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine)
    progress = ExecutionPlanProgressService(bind=engine)
    handle = claims.acquire(plan_id, "owner", lease_seconds=5)
    progress.prepare_attempt(handle)
    progress.mark_network_started(handle)
    with pytest.raises(ExecutionPlanCancellationInDoubtError):
        cancellation_service().request_cancel(plan_id)
    with SessionLocal() as db:
        assert db.get(ExecutionPlanCancellation, plan_id) is None


def test_in_doubt_cancellation_preserves_exact_progress_state(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine)
    progress = ExecutionPlanProgressService(bind=engine)
    handle = claims.acquire(plan_id, "owner", lease_seconds=5)
    progress.prepare_attempt(handle)
    with SessionLocal() as db:
        row = db.get(ExecutionPlanProgress, plan_id)
        row.phase = "in_doubt"
        db.commit()
        expected = row.fencing_generation, row.phase, row.updated_at

    with pytest.raises(ExecutionPlanCancellationInDoubtError) as raised:
        cancellation_service().request_cancel(plan_id)
    assert raised.value.code == "execution_plan_cancellation_in_doubt"
    with SessionLocal() as db:
        row = db.get(ExecutionPlanProgress, plan_id)
        assert (row.fencing_generation, row.phase, row.updated_at) == expected
        assert db.get(ExecutionPlanCancellation, plan_id) is None


def test_cancel_first_serializes_against_marker(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine)
    progress = ExecutionPlanProgressService(bind=engine)
    handle = claims.acquire(plan_id, "owner", lease_seconds=5)
    progress.prepare_attempt(handle)
    cancellation_service().request_cancel(plan_id)
    with pytest.raises(ExecutionProgressCancelledError):
        progress.mark_network_started(handle)


def _wait_for_plan_lock_waiters(expected: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with engine.connect() as db:
            waiters = db.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname=current_database() AND wait_event_type='Lock' "
                    "AND query LIKE '%execution_plans%' "
                    "AND query LIKE '%FOR UPDATE%'"
                )
            )
        if waiters >= expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"expected {expected} PostgreSQL plan-lock waiters")


def test_real_postgres_race_cancellation_commits_before_marker(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine)
    progress = ExecutionPlanProgressService(bind=engine, attempt_timeout_seconds=2)
    cancellation = ExecutionPlanCancellationService(
        bind=engine, attempt_timeout_seconds=2
    )
    handle = claims.acquire(plan_id, "owner", lease_seconds=5)
    progress.prepare_attempt(handle)
    with engine.connect() as blocker, ThreadPoolExecutor(max_workers=2) as pool:
        tx = blocker.begin()
        blocker.execute(
            text("SELECT id FROM execution_plans WHERE id=:plan_id FOR UPDATE"),
            {"plan_id": plan_id},
        )
        cancel_future = pool.submit(cancellation.request_cancel, plan_id)
        _wait_for_plan_lock_waiters(1)
        marker_future = pool.submit(progress.mark_network_started, handle)
        _wait_for_plan_lock_waiters(2)
        tx.commit()
        assert cancel_future.result(timeout=3).execution_plan_id == plan_id
        with pytest.raises(ExecutionProgressCancelledError):
            marker_future.result(timeout=3)


def test_real_postgres_race_marker_commits_before_cancellation(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine)
    progress = ExecutionPlanProgressService(bind=engine, attempt_timeout_seconds=2)
    cancellation = ExecutionPlanCancellationService(
        bind=engine, attempt_timeout_seconds=2
    )
    handle = claims.acquire(plan_id, "owner", lease_seconds=5)
    progress.prepare_attempt(handle)
    with engine.connect() as blocker, ThreadPoolExecutor(max_workers=2) as pool:
        tx = blocker.begin()
        blocker.execute(
            text("SELECT id FROM execution_plans WHERE id=:plan_id FOR UPDATE"),
            {"plan_id": plan_id},
        )
        marker_future = pool.submit(progress.mark_network_started, handle)
        _wait_for_plan_lock_waiters(1)
        cancel_future = pool.submit(cancellation.request_cancel, plan_id)
        _wait_for_plan_lock_waiters(2)
        tx.commit()
        assert marker_future.result(timeout=3).phase == "network_started"
        with pytest.raises(ExecutionPlanCancellationInDoubtError):
            cancel_future.result(timeout=3)
    with SessionLocal() as db:
        progress_row = db.get(ExecutionPlanProgress, plan_id)
        assert progress_row.phase == "network_started"
        assert db.get(ExecutionPlanCancellation, plan_id) is None


def test_stale_generation_cannot_bypass_cancellation(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine)
    progress = ExecutionPlanProgressService(bind=engine)
    stale = claims.acquire(plan_id, "stale", lease_seconds=5)
    progress.prepare_attempt(stale)
    claims.release(stale)
    current = claims.acquire(plan_id, "current", lease_seconds=5)
    progress.prepare_attempt(current)
    cancellation_service().request_cancel(plan_id)
    with pytest.raises(ExecutionProgressLostError):
        progress.mark_network_started(stale)
    with pytest.raises(ExecutionProgressCancelledError):
        progress.mark_network_started(current)


def test_cancellation_lock_timeout_is_bounded_and_sanitized(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    with engine.connect() as blocker:
        tx = blocker.begin()
        blocker.execute(
            text("SELECT id FROM execution_plans WHERE id=:plan_id FOR UPDATE"),
            {"plan_id": plan_id},
        )
        with pytest.raises(ExecutionPlanCancellationCoordinationError) as raised:
            cancellation_service(max_retries=2).request_cancel(plan_id)
        tx.rollback()
    assert str(raised.value) == "ExecutionPlan cancellation coordination failed."


def test_marker_plan_lock_timeout_is_bounded_and_sanitized(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine)
    progress = ExecutionPlanProgressService(
        bind=engine, max_retries=2, attempt_timeout_seconds=0.05
    )
    handle = claims.acquire(plan_id, "owner", lease_seconds=5)
    progress.prepare_attempt(handle)
    with engine.connect() as blocker:
        tx = blocker.begin()
        blocker.execute(
            text("SELECT id FROM execution_plans WHERE id=:plan_id FOR UPDATE"),
            {"plan_id": plan_id},
        )
        with pytest.raises(ExecutionProgressCoordinationError) as raised:
            progress.mark_network_started(handle)
        tx.rollback()
    assert str(raised.value) == "ExecutionPlan progress coordination failed."


def test_preexisting_cancellation_does_no_claim_rate_or_gateway(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    cancellation_service().request_cancel(plan_id)

    class NoClaim:
        def acquire(self, *args, **kwargs):
            raise AssertionError("claim acquisition must not run")

    limiter = MutatingRateLimiter()
    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(plan_id, limiter=limiter, gateway=gateway, claim_service=NoClaim())
    assert raised.value.code == "execution_plan_cancelled"
    assert limiter.calls == 0
    assert gateway.target_ids == []


def test_preexisting_bearer_plan_cancellation_skips_credential_and_execution(
    cancelled_bearer_plan: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id = cancelled_bearer_plan
    monkeypatch.setattr(
        BearerCredentialService,
        "resolve_binding",
        lambda *args, **kwargs: pytest.fail("credential resolution must not run"),
    )

    class NoClaim:
        def acquire(self, *args, **kwargs):
            raise AssertionError("claim acquisition must not run")

    limiter = MutatingRateLimiter()
    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(plan_id, limiter=limiter, gateway=gateway, claim_service=NoClaim())
    assert raised.value.code == "execution_plan_cancelled"
    assert limiter.calls == 0
    assert gateway.target_ids == []
    with SessionLocal() as db:
        assert db.scalar(
            select(TestRun).where(TestRun.execution_plan_id == plan_id)
        ) is None


def test_cancellation_lookup_failure_before_claim_fails_closed(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan

    class FailedLookup:
        def get_cancellation(self, plan_id):
            raise ExecutionPlanCancellationCoordinationError("private database text")

    class NoClaim:
        def acquire(self, *args, **kwargs):
            raise AssertionError("claim acquisition must not run")

    limiter = MutatingRateLimiter()
    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id, limiter=limiter, gateway=gateway,
            claim_service=NoClaim(), cancellation_service=FailedLookup(),
        )
    assert raised.value.code == "execution_plan_cancellation_coordination_failed"
    assert "private" not in raised.value.reason
    assert limiter.calls == 0
    assert gateway.target_ids == []


def test_post_claim_cancellation_recheck_releases_exact_claim(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    cancellation = cancellation_service()
    progress = ExecutionPlanProgressService(bind=engine)
    delegate = ExecutionPlanClaimService(bind=engine)

    class PrepareAndCancelAfterClaim:
        def acquire(self, *args, **kwargs):
            handle = delegate.acquire(*args, **kwargs)
            prepared = progress.prepare_attempt(handle)
            assert prepared.phase == "pre_network"
            cancellation.request_cancel(handle.execution_plan_id)
            return handle

        def release(self, handle):
            return delegate.release(handle)

    limiter = MutatingRateLimiter()
    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id, limiter=limiter, gateway=gateway,
            claim_service=PrepareAndCancelAfterClaim(),
        )
    assert raised.value.code == "execution_plan_cancelled"
    assert limiter.calls == 0
    assert gateway.target_ids == []
    with SessionLocal() as db:
        claim = db.get(ExecutionPlanClaim, plan_id)
        assert claim.owner_id is None
        progress_row = db.get(ExecutionPlanProgress, plan_id)
        assert progress_row.phase == "pre_network"
        assert db.scalar(
            select(TestRun).where(TestRun.execution_plan_id == plan_id)
        ) is None


def test_post_claim_lookup_failure_releases_exact_claim(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan

    class FailSecondLookup:
        calls = 0

        def get_cancellation(self, plan_id):
            self.calls += 1
            if self.calls == 2:
                raise ExecutionPlanCancellationCoordinationError("private db details")
            return None

    limiter = MutatingRateLimiter()
    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(
            plan_id, limiter=limiter, gateway=gateway,
            cancellation_service=FailSecondLookup(),
        )
    assert raised.value.code == "execution_plan_cancellation_coordination_failed"
    assert limiter.calls == 0
    assert gateway.target_ids == []
    with SessionLocal() as db:
        claim = db.get(ExecutionPlanClaim, plan_id)
        assert claim.owner_id is None


def test_cancellation_during_rate_wait_calls_zero_gateway(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    service = cancellation_service()
    limiter = MutatingRateLimiter(lambda: service.request_cancel(plan_id))
    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(plan_id, limiter=limiter, gateway=gateway)
    assert raised.value.code == "execution_plan_cancelled"
    assert gateway.target_ids == []
    with SessionLocal() as db:
        assert db.scalar(select(TestRun).where(TestRun.execution_plan_id == plan_id)) is None


def test_plan_serialization_lock_is_not_held_during_rate_or_network(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    observations: list[str] = []

    def lock_plan(stage: str) -> None:
        with engine.begin() as db:
            db.execute(
                text("SELECT id FROM execution_plans WHERE id=:plan_id FOR UPDATE NOWAIT"),
                {"plan_id": plan_id},
            )
        observations.append(stage)

    class LockingGateway(RecordingGateway):
        def request(self, **kwargs):
            lock_plan("network")
            return super().request(**kwargs)

    result = execute(
        plan_id,
        limiter=MutatingRateLimiter(lambda: lock_plan("rate")),
        gateway=LockingGateway(),
    )
    assert result.response_status == 200
    assert observations == ["rate", "network"]


def test_canonical_replay_bypasses_cancellation_lookup(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    canonical = execute(
        plan_id, limiter=MutatingRateLimiter(), gateway=RecordingGateway()
    )

    class NoCancellationLookup:
        def get_cancellation(self, plan_id):
            raise AssertionError("canonical replay must win")

    replay = execute(
        plan_id, limiter=MutatingRateLimiter(), gateway=RecordingGateway(),
        cancellation_service=NoCancellationLookup(),
    )
    assert replay.id == canonical.id


@pytest.mark.parametrize("stale_status", ["running", "succeeded", "failed"])
def test_cancellation_is_independent_of_test_case_status(
    approved_plan, stale_status: str
) -> None:
    plan_id, _, _, _ = approved_plan
    with SessionLocal() as db:
        test_case_id = db.scalar(select(PlanAction.test_case_id).where(
            PlanAction.execution_plan_id == plan_id
        ))
        test_case = db.get(TestCase, test_case_id)
        test_case.status = stale_status
        db.commit()
    cancellation_service().request_cancel(plan_id)
    limiter = MutatingRateLimiter()
    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(plan_id, limiter=limiter, gateway=gateway)
    assert raised.value.code == "execution_plan_cancelled"
    assert limiter.calls == 0
    assert gateway.target_ids == []
    with SessionLocal() as db:
        assert db.get(ExecutionPlanCancellation, plan_id) is not None
        assert db.scalar(
            select(TestRun).where(TestRun.execution_plan_id == plan_id)
        ) is None


def test_cancellation_is_exact_plan_not_reusable_test_case(approved_plan) -> None:
    plan_id, target_id, _, _ = approved_plan
    cancellation_service().request_cancel(plan_id)
    with SessionLocal() as db:
        test_case_id = db.scalar(
            select(PlanAction.test_case_id).where(
                PlanAction.execution_plan_id == plan_id
            )
        )
        second = create_test_case_execution_plan(
            db, test_case_id=test_case_id, credential_binding_id=None
        )
        record_plan_decision(db, execution_plan_id=second.id, decision="approved")
        db.commit()
        second_plan_id = second.id
    try:
        gateway = RecordingGateway()
        result = execute(
            second_plan_id, limiter=MutatingRateLimiter(), gateway=gateway
        )
        assert result.execution_plan_id == second_plan_id
        assert gateway.target_ids == [target_id]
    finally:
        with SessionLocal() as db:
            db.execute(delete(SafetyDecisionRecord).where(
                SafetyDecisionRecord.execution_plan_id == second_plan_id
            ))
            db.execute(delete(TestRun).where(TestRun.execution_plan_id == second_plan_id))
            db.execute(delete(ExecutionPlanApprovalRecord).where(
                ExecutionPlanApprovalRecord.execution_plan_id == second_plan_id
            ))
            db.execute(delete(ExecutionPlanCancellation).where(
                ExecutionPlanCancellation.execution_plan_id == second_plan_id
            ))
            db.execute(delete(ExecutionPlanProgress).where(
                ExecutionPlanProgress.execution_plan_id == second_plan_id
            ))
            db.execute(delete(ExecutionPlanClaim).where(
                ExecutionPlanClaim.execution_plan_id == second_plan_id
            ))
            db.execute(delete(PlanAction).where(PlanAction.execution_plan_id == second_plan_id))
            db.execute(delete(ExecutionPlan).where(ExecutionPlan.id == second_plan_id))
            db.commit()


def test_real_subprocess_cancels_pre_network_attempt(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine)
    progress = ExecutionPlanProgressService(bind=engine)
    handle = claims.acquire(plan_id, "worker", lease_seconds=5)
    progress.prepare_attempt(handle)
    code = (
        "from app.db.session import engine; "
        "from app.services.execution_plan_cancellation import ExecutionPlanCancellationService; "
        "import sys; s=ExecutionPlanCancellationService(bind=engine); "
        "print(s.request_cancel(int(sys.argv[1])).execution_plan_id)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(plan_id)], cwd=".",
        capture_output=True, text=True, check=True, timeout=10,
    )
    assert result.stdout.strip() == str(plan_id)
    with pytest.raises(ExecutionProgressCancelledError):
        progress.mark_network_started(handle)

    class NoClaim:
        def acquire(self, *args, **kwargs):
            raise AssertionError("cancelled retry must not acquire a claim")

    for _ in range(2):
        limiter = MutatingRateLimiter()
        gateway = RecordingGateway()
        with pytest.raises(ExecutionBlockedError) as raised:
            execute(
                plan_id,
                limiter=limiter,
                gateway=gateway,
                claim_service=NoClaim(),
            )
        assert raised.value.code == "execution_plan_cancelled"
        assert limiter.calls == 0
        assert gateway.target_ids == []
    with SessionLocal() as db:
        assert db.get(ExecutionPlanCancellation, plan_id) is not None
        assert db.scalar(
            select(TestRun).where(TestRun.execution_plan_id == plan_id)
        ) is None
