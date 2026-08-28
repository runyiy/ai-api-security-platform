import subprocess
import sys
import time

import pytest
from sqlalchemy import text

from app.db.session import engine
from app.executors.http import ExecutionBlockedError
from app.services.execution_plan_claim import ExecutionPlanClaimService
from app.services.execution_plan_progress import (
    ExecutionInDoubtError,
    ExecutionPlanProgressService,
    ExecutionProgressCoordinationError,
    ExecutionProgressLostError,
)
from tests.services.test_plan_execution_integration import (
    MutatingRateLimiter,
    RecordingGateway,
    approved_plan,
    execute,
)


def services():
    return (
        ExecutionPlanClaimService(bind=engine, attempt_timeout_seconds=0.1),
        ExecutionPlanProgressService(bind=engine, attempt_timeout_seconds=0.1),
    )


def test_prepare_is_idempotent_and_higher_generation_takes_over_pre_network(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    claims, progress = services()
    first = claims.acquire(plan_id, "first", lease_seconds=1)
    created = progress.prepare_attempt(first)
    same = progress.prepare_attempt(first)
    assert created.phase == same.phase == "pre_network"
    assert created.fencing_generation == same.fencing_generation == 1
    claims.release(first)
    second = claims.acquire(plan_id, "second", lease_seconds=1)
    taken_over = progress.prepare_attempt(second)
    assert taken_over.fencing_generation == 2
    with pytest.raises(ExecutionProgressLostError):
        progress.mark_network_started(first)


def test_only_exact_generation_marks_network_started_and_then_is_in_doubt(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    claims, progress = services()
    first = claims.acquire(plan_id, "first", lease_seconds=1)
    progress.prepare_attempt(first)
    marked = progress.mark_network_started(first)
    assert marked.phase == "network_started"
    claims.release(first)
    second = claims.acquire(plan_id, "second", lease_seconds=1)
    with pytest.raises(ExecutionInDoubtError):
        progress.prepare_attempt(second)


def test_progress_lock_contention_exhausts_bounded_retries_sanitized(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    claims, progress = services()
    handle = claims.acquire(plan_id, "owner", lease_seconds=10)
    progress.prepare_attempt(handle)
    with engine.connect() as blocker:
        tx = blocker.begin()
        blocker.execute(
            text(
                "SELECT execution_plan_id FROM execution_plan_progress "
                "WHERE execution_plan_id=:plan_id FOR UPDATE"
            ),
            {"plan_id": plan_id},
        )
        with pytest.raises(ExecutionProgressCoordinationError) as raised:
            progress.mark_network_started(handle)
        tx.rollback()
    assert str(raised.value) == "ExecutionPlan progress coordination failed."


def test_real_subprocess_recovers_released_pre_network_generation(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    claims, progress = services()
    first = claims.acquire(plan_id, "first", lease_seconds=1)
    progress.prepare_attempt(first)
    claims.release(first)
    code = (
        "from app.db.session import engine; "
        "from app.services.execution_plan_claim import ExecutionPlanClaimService; "
        "from app.services.execution_plan_progress import ExecutionPlanProgressService; "
        "import sys; c=ExecutionPlanClaimService(bind=engine); "
        "p=ExecutionPlanProgressService(bind=engine); "
        "h=c.acquire(int(sys.argv[1]),'subprocess',lease_seconds=2); "
        "s=p.prepare_attempt(h); print(h.fencing_generation,s.phase)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(plan_id)],
        cwd=".", capture_output=True, text=True, check=True, timeout=10,
    )
    assert result.stdout.strip() == "2 pre_network"


def test_real_subprocess_network_started_then_retry_is_in_doubt(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    code = (
        "from app.db.session import engine; "
        "from app.services.execution_plan_claim import ExecutionPlanClaimService; "
        "from app.services.execution_plan_progress import ExecutionPlanProgressService; "
        "import sys; c=ExecutionPlanClaimService(bind=engine); "
        "p=ExecutionPlanProgressService(bind=engine); "
        "h=c.acquire(int(sys.argv[1]),'subprocess',lease_seconds=.05); "
        "p.prepare_attempt(h); p.mark_network_started(h); print('marked')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(plan_id)],
        cwd=".", capture_output=True, text=True, check=True, timeout=10,
    )
    assert result.stdout.strip() == "marked"
    time.sleep(0.08)
    limiter = MutatingRateLimiter()
    gateway = RecordingGateway()
    with pytest.raises(ExecutionBlockedError) as raised:
        execute(plan_id, limiter=limiter, gateway=gateway)
    assert raised.value.code == "execution_plan_in_doubt"
    assert limiter.calls == 0
    assert gateway.target_ids == []
