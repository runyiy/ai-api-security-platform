import subprocess
import sys
import time
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db.session import engine
from app.services.execution_plan_claim import (
    ExecutionClaimCoordinationError,
    ExecutionClaimLostError,
    ExecutionClaimUnavailableError,
    ExecutionPlanClaimService,
)
from tests.services.test_plan_execution_integration import approved_plan


def service() -> ExecutionPlanClaimService:
    return ExecutionPlanClaimService(bind=engine, attempt_timeout_seconds=0.1)


def test_claim_release_reacquire_and_stale_fencing(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    claims = service()
    first = claims.acquire(plan_id, "owner-one", lease_seconds=1.0)
    assert first.fencing_generation == 1
    assert first.lease_expires_at > first.database_now

    with pytest.raises(ExecutionClaimUnavailableError):
        claims.acquire(plan_id, "owner-two", lease_seconds=1.0)
    claims.assert_current(first)
    claims.release(first)
    second = claims.acquire(plan_id, "owner-two", lease_seconds=1.0)
    assert second.fencing_generation == 2

    with pytest.raises(ExecutionClaimLostError):
        claims.release(first)
    with pytest.raises(ExecutionClaimLostError):
        claims.renew(first, lease_seconds=1.0)
    claims.assert_current(second)


def test_expiry_takeover_increments_generation_and_fences_old_owner(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    claims = service()
    first = claims.acquire(plan_id, "owner-one", lease_seconds=0.05)
    time.sleep(0.08)
    second = claims.acquire(plan_id, "owner-two", lease_seconds=1.0)

    assert second.fencing_generation == first.fencing_generation + 1
    with pytest.raises(ExecutionClaimLostError):
        claims.assert_current(first)
    with pytest.raises(ExecutionClaimLostError):
        claims.renew(first, lease_seconds=1.0)


def test_real_subprocess_claim_race_has_one_winner(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    code = (
        "from app.db.session import engine; "
        "from app.services.execution_plan_claim import *; import sys; "
        "s=ExecutionPlanClaimService(bind=engine); "
        "\ntry:\n h=s.acquire(int(sys.argv[1]),sys.argv[2],lease_seconds=2); "
        "print('won',h.fencing_generation)\n"
        "except ExecutionClaimUnavailableError: print('unavailable')"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(plan_id), f"owner-{uuid4()}"],
            cwd=".",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = [process.communicate(timeout=10)[0].strip() for process in processes]

    assert sum(output.startswith("won 1") for output in outputs) == 1
    assert outputs.count("unavailable") == 1


def test_real_subprocess_expiry_takeover_fences_original_owner(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    claims = service()
    original = claims.acquire(plan_id, "original-owner", lease_seconds=0.05)
    time.sleep(0.08)
    code = (
        "from app.db.session import engine; "
        "from app.services.execution_plan_claim import ExecutionPlanClaimService; "
        "import sys; s=ExecutionPlanClaimService(bind=engine); "
        "h=s.acquire(int(sys.argv[1]),'takeover-owner',lease_seconds=2); "
        "print(h.fencing_generation)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(plan_id)],
        cwd=".",
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )

    assert result.stdout.strip() == str(original.fencing_generation + 1)
    with pytest.raises(ExecutionClaimLostError):
        claims.assert_current(original)
    with pytest.raises(ExecutionClaimLostError):
        claims.release(original)


def test_locked_claim_attempts_are_finite_and_fail_sanitized(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    claims = service()
    current = claims.acquire(plan_id, "current-owner", lease_seconds=2)
    with engine.connect() as blocker:
        transaction = blocker.begin()
        blocker.execute(
            text(
                "SELECT execution_plan_id FROM execution_plan_claims "
                "WHERE execution_plan_id=:plan_id FOR UPDATE"
            ),
            {"plan_id": plan_id},
        )
        started = time.monotonic()
        with pytest.raises(ExecutionClaimCoordinationError) as raised:
            claims.acquire(plan_id, "waiting-owner", lease_seconds=2)
        elapsed = time.monotonic() - started
        transaction.rollback()

    assert str(raised.value) == "Execution claim coordination failed."
    assert 0.2 <= elapsed < 2
    claims.assert_current(current)
