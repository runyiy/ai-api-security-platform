import subprocess
import sys
import time
from uuid import uuid4

import pytest
from sqlalchemy import delete, inspect, text

from app.db.models import ExecutionPlan, ExecutionPlanClaim, PlanAction
from app.db.session import SessionLocal, engine
from app.services.execution_plan import PlanActionInput, create_execution_plan
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
    current = claims.acquire(plan_id, "current-owner", lease_seconds=10)
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
            claims.acquire(plan_id, "waiting-owner", lease_seconds=10)
        elapsed = time.monotonic() - started
        transaction.rollback()

    assert str(raised.value) == "Execution claim coordination failed."
    assert 0.2 <= elapsed < 2
    claims.assert_current(current)


def test_different_execution_plans_hold_active_claims_independently(
    approved_plan: tuple[int, int, int, int],
) -> None:
    first_plan_id, _, _, _ = approved_plan
    with SessionLocal() as db:
        first_plan = db.get(ExecutionPlan, first_plan_id)
        assert first_plan is not None
        first_action = db.scalar(
            text(
                "SELECT id FROM plan_actions "
                "WHERE execution_plan_id=:plan_id ORDER BY ordinal"
            ),
            {"plan_id": first_plan_id},
        )
        assert first_action is not None
        action = db.get(PlanAction, first_action)
        assert action is not None
        second_plan = create_execution_plan(
            db,
            target_id=first_plan.target_id,
            authorization_revision_id=first_plan.authorization_revision_id,
            actor_identity_id=first_plan.actor_identity_id,
            credential_binding_id=first_plan.credential_binding_id,
            actions=[
                PlanActionInput(
                    method=action.method,
                    url=action.url,
                    test_case_id=action.test_case_id,
                    resource_id=action.resource_id,
                )
            ],
            policy_context=first_plan.policy_context,
        )
        db.commit()
        second_plan_id = second_plan.id

    claims = service()
    try:
        first = claims.acquire(first_plan_id, "first-owner", lease_seconds=2)
        second = claims.acquire(second_plan_id, "second-owner", lease_seconds=2)

        claims.assert_current(first)
        claims.assert_current(second)
        assert first.execution_plan_id != second.execution_plan_id
        assert first.fencing_generation == second.fencing_generation == 1
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(ExecutionPlanClaim).where(
                    ExecutionPlanClaim.execution_plan_id == second_plan_id
                )
            )
            db.execute(
                delete(PlanAction).where(
                    PlanAction.execution_plan_id == second_plan_id
                )
            )
            db.execute(
                delete(ExecutionPlan).where(ExecutionPlan.id == second_plan_id)
            )
            db.commit()


def test_expired_handle_cannot_renew_before_any_takeover(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    claims = service()
    expired = claims.acquire(plan_id, "expired-owner", lease_seconds=0.05)
    time.sleep(0.08)

    with pytest.raises(ExecutionClaimLostError):
        claims.renew(expired, lease_seconds=1)


def test_released_handle_immediately_fails_assert_current(
    approved_plan: tuple[int, int, int, int],
) -> None:
    plan_id, _, _, _ = approved_plan
    claims = service()
    released = claims.acquire(plan_id, "released-owner", lease_seconds=2)
    claims.release(released)

    with pytest.raises(ExecutionClaimLostError):
        claims.assert_current(released)


def test_claim_model_and_table_have_only_required_columns() -> None:
    expected = {
        "execution_plan_id",
        "owner_id",
        "fencing_generation",
        "lease_expires_at",
    }

    assert set(ExecutionPlanClaim.__table__.columns.keys()) == expected
    assert {
        column["name"]
        for column in inspect(engine).get_columns("execution_plan_claims")
    } == expected
