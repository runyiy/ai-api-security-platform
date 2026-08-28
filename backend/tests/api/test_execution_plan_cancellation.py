from fastapi.testclient import TestClient

from app.main import app
from app.services.execution_plan_claim import ExecutionPlanClaimService
from app.services.execution_plan_progress import ExecutionPlanProgressService
from app.db.session import engine
from tests.services.test_plan_execution_integration import approved_plan
from tests.services.test_plan_execution_integration import (
    MutatingRateLimiter,
    RecordingGateway,
    execute,
)


client = TestClient(app)


def test_cancel_route_is_idempotent_and_non_secret(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    first = client.post(f"/api/execution-plans/{plan_id}/cancel")
    second = client.post(f"/api/execution-plans/{plan_id}/cancel")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert set(first.json()) == {"execution_plan_id", "requested_at"}


def test_cancel_route_unknown_plan_is_404() -> None:
    response = client.post("/api/execution-plans/2147483647/cancel")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "execution_plan_not_found"


def test_cancel_route_network_started_is_conflict(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    claims = ExecutionPlanClaimService(bind=engine)
    progress = ExecutionPlanProgressService(bind=engine)
    handle = claims.acquire(plan_id, "owner", lease_seconds=5)
    progress.prepare_attempt(handle)
    progress.mark_network_started(handle)
    response = client.post(f"/api/execution-plans/{plan_id}/cancel")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "execution_plan_cancellation_in_doubt"


def test_cancel_route_completed_is_conflict_and_canonical_survives(approved_plan) -> None:
    plan_id, _, _, _ = approved_plan
    canonical = execute(
        plan_id, limiter=MutatingRateLimiter(), gateway=RecordingGateway()
    )
    response = client.post(f"/api/execution-plans/{plan_id}/cancel")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "execution_plan_already_completed"
    replay = execute(
        plan_id, limiter=MutatingRateLimiter(), gateway=RecordingGateway()
    )
    assert replay.id == canonical.id
