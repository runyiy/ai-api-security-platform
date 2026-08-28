from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models.execution_plan_cancellation import ExecutionPlanCancellation


class ExecutionPlanCancellationError(RuntimeError):
    code = "execution_plan_cancellation_coordination_failed"


class ExecutionPlanCancellationNotFoundError(ExecutionPlanCancellationError):
    code = "execution_plan_not_found"


class ExecutionPlanAlreadyCompletedError(ExecutionPlanCancellationError):
    code = "execution_plan_already_completed"


class ExecutionPlanCancellationInDoubtError(ExecutionPlanCancellationError):
    code = "execution_plan_cancellation_in_doubt"


class ExecutionPlanCancellationCoordinationError(ExecutionPlanCancellationError):
    code = "execution_plan_cancellation_coordination_failed"


@dataclass(frozen=True)
class CancellationState:
    execution_plan_id: int
    requested_at: datetime


class ExecutionPlanCancellationService:
    def __init__(
        self,
        *,
        bind: Engine,
        max_retries: int = 3,
        attempt_timeout_seconds: float = 1.0,
    ) -> None:
        if max_retries <= 0:
            raise ValueError("max_retries must be positive")
        if not math.isfinite(attempt_timeout_seconds) or attempt_timeout_seconds <= 0:
            raise ValueError("attempt timeout must be finite and positive")
        self._sessions = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
        self._max_retries = max_retries
        self._timeout = f"{max(1, math.ceil(attempt_timeout_seconds * 1000))}ms"

    def get_cancellation(self, execution_plan_id: int) -> CancellationState | None:
        row = self._run_read(execution_plan_id)
        return CancellationState(*row) if row is not None else None

    def request_cancel(self, execution_plan_id: int) -> CancellationState:
        for _ in range(self._max_retries):
            try:
                with self._sessions.begin() as db:
                    self._set_timeouts(db)
                    plan = db.execute(
                        text("SELECT id FROM execution_plans WHERE id=:plan_id FOR UPDATE"),
                        {"plan_id": execution_plan_id},
                    ).first()
                    if plan is None:
                        raise ExecutionPlanCancellationNotFoundError(
                            "ExecutionPlan not found."
                        )
                    canonical = db.execute(
                        text("SELECT id FROM test_runs WHERE execution_plan_id=:plan_id"),
                        {"plan_id": execution_plan_id},
                    ).first()
                    if canonical is not None:
                        raise ExecutionPlanAlreadyCompletedError(
                            "ExecutionPlan already has a canonical result."
                        )
                    phase = db.execute(
                        text("SELECT phase FROM execution_plan_progress WHERE execution_plan_id=:plan_id"),
                        {"plan_id": execution_plan_id},
                    ).scalar_one_or_none()
                    if phase in {"network_started", "in_doubt"}:
                        raise ExecutionPlanCancellationInDoubtError(
                            "ExecutionPlan may have crossed the network boundary."
                        )
                    db.execute(
                        text(
                            "INSERT INTO execution_plan_cancellations "
                            "(execution_plan_id, requested_at) "
                            "VALUES (:plan_id, clock_timestamp()) "
                            "ON CONFLICT (execution_plan_id) DO NOTHING"
                        ),
                        {"plan_id": execution_plan_id},
                    )
                    row = db.execute(
                        select(
                            ExecutionPlanCancellation.execution_plan_id,
                            ExecutionPlanCancellation.requested_at,
                        ).where(
                            ExecutionPlanCancellation.execution_plan_id
                            == execution_plan_id
                        )
                    ).one()
                    return CancellationState(*row)
            except (
                ExecutionPlanCancellationNotFoundError,
                ExecutionPlanAlreadyCompletedError,
                ExecutionPlanCancellationInDoubtError,
            ):
                raise
            except Exception:
                continue
        raise ExecutionPlanCancellationCoordinationError(
            "ExecutionPlan cancellation coordination failed."
        )

    def _run_read(self, execution_plan_id: int):
        for _ in range(self._max_retries):
            try:
                with self._sessions.begin() as db:
                    self._set_timeouts(db)
                    row = db.execute(
                        select(
                            ExecutionPlanCancellation.execution_plan_id,
                            ExecutionPlanCancellation.requested_at,
                        ).where(
                            ExecutionPlanCancellation.execution_plan_id
                            == execution_plan_id
                        )
                    ).first()
                    return tuple(row) if row is not None else None
            except Exception:
                continue
        raise ExecutionPlanCancellationCoordinationError(
            "ExecutionPlan cancellation coordination failed."
        )

    def _set_timeouts(self, db) -> None:
        db.execute(
            text("SELECT set_config('lock_timeout', :value, true)"),
            {"value": self._timeout},
        )
        db.execute(
            text("SELECT set_config('statement_timeout', :value, true)"),
            {"value": self._timeout},
        )
