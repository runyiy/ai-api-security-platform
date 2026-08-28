from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from app.services.execution_plan_claim import ClaimHandle


class ExecutionProgressError(RuntimeError):
    pass


class ExecutionInDoubtError(ExecutionProgressError):
    pass


class ExecutionProgressLostError(ExecutionProgressError):
    pass


class ExecutionProgressCoordinationError(ExecutionProgressError):
    pass


class ExecutionProgressCancelledError(ExecutionProgressError):
    pass


@dataclass(frozen=True)
class ProgressState:
    execution_plan_id: int
    fencing_generation: int
    phase: str
    updated_at: datetime


class ExecutionPlanProgressService:
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

    def prepare_attempt(self, handle: ClaimHandle) -> ProgressState:
        row = self._run(
            text(
                """WITH current_claim AS MATERIALIZED (
                    SELECT execution_plan_id FROM execution_plan_claims
                    WHERE execution_plan_id=:plan_id AND owner_id=:owner_id
                      AND fencing_generation=:generation
                      AND lease_expires_at > clock_timestamp()
                    FOR UPDATE
                ), db_time AS MATERIALIZED (SELECT clock_timestamp() AS now),
                prepared AS (
                INSERT INTO execution_plan_progress AS progress (
                    execution_plan_id, fencing_generation, phase, updated_at
                )
                SELECT :plan_id, :generation, 'pre_network', db_time.now
                FROM current_claim CROSS JOIN db_time
                ON CONFLICT (execution_plan_id) DO UPDATE SET
                    fencing_generation=EXCLUDED.fencing_generation,
                    phase='pre_network', updated_at=EXCLUDED.updated_at
                WHERE progress.phase='pre_network'
                  AND progress.fencing_generation <= EXCLUDED.fencing_generation
                RETURNING execution_plan_id, fencing_generation, phase, updated_at
                )
                SELECT 'prepared', execution_plan_id, fencing_generation,
                       phase, updated_at
                FROM prepared
                UNION ALL
                SELECT CASE
                         WHEN NOT EXISTS (SELECT 1 FROM current_claim) THEN 'lost'
                         WHEN progress.phase IN ('network_started', 'in_doubt')
                           THEN 'in_doubt'
                         ELSE 'lost'
                       END,
                       progress.execution_plan_id, progress.fencing_generation,
                       progress.phase, progress.updated_at
                FROM (VALUES (1)) AS singleton(value)
                LEFT JOIN execution_plan_progress AS progress
                  ON progress.execution_plan_id=:plan_id
                WHERE NOT EXISTS (SELECT 1 FROM prepared)
                LIMIT 1"""
            ),
            self._values(handle),
        )
        classification = row[0]
        if classification == "prepared":
            return ProgressState(*row[1:])
        if classification == "in_doubt":
            raise ExecutionInDoubtError("ExecutionPlan execution is in doubt.")
        raise ExecutionProgressLostError("ExecutionPlan progress fencing was lost.")

    def mark_network_started(self, handle: ClaimHandle) -> ProgressState:
        values = self._values(handle)
        for _ in range(self._max_retries):
            try:
                with self._sessions.begin() as db:
                    self._set_timeouts(db)
                    db.execute(
                        text(
                            "SELECT id FROM execution_plans "
                            "WHERE id=:plan_id FOR UPDATE"
                        ),
                        values,
                    ).one()
                    current = db.execute(
                        text(
                            "SELECT execution_plan_id FROM execution_plan_claims "
                            "WHERE execution_plan_id=:plan_id AND owner_id=:owner_id "
                            "AND fencing_generation=:generation "
                            "AND lease_expires_at > clock_timestamp() FOR UPDATE"
                        ),
                        values,
                    ).first()
                    if current is None:
                        raise ExecutionProgressLostError(
                            "ExecutionPlan progress fencing was lost."
                        )
                    cancelled = db.execute(
                        text(
                            "SELECT execution_plan_id "
                            "FROM execution_plan_cancellations "
                            "WHERE execution_plan_id=:plan_id"
                        ),
                        values,
                    ).first()
                    if cancelled is not None:
                        raise ExecutionProgressCancelledError(
                            "ExecutionPlan was cancelled."
                        )
                    row = db.execute(
                        text(
                            "UPDATE execution_plan_progress SET "
                            "phase='network_started', updated_at=clock_timestamp() "
                            "WHERE execution_plan_id=:plan_id "
                            "AND fencing_generation=:generation "
                            "AND phase='pre_network' "
                            "RETURNING execution_plan_id, fencing_generation, "
                            "phase, updated_at"
                        ),
                        values,
                    ).first()
                    if row is None:
                        raise ExecutionProgressLostError(
                            "ExecutionPlan progress fencing was lost."
                        )
                    return ProgressState(*row)
            except (ExecutionProgressLostError, ExecutionProgressCancelledError):
                raise
            except Exception:
                continue
        raise ExecutionProgressCoordinationError(
            "ExecutionPlan progress coordination failed."
        )

    def _run(self, statement, values: dict[str, object]):
        for _ in range(self._max_retries):
            try:
                with self._sessions.begin() as db:
                    self._set_timeouts(db)
                    result = db.execute(statement, values).first()
                    return tuple(result) if result is not None else None
            except Exception:
                continue
        raise ExecutionProgressCoordinationError(
            "ExecutionPlan progress coordination failed."
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

    @staticmethod
    def _values(handle: ClaimHandle) -> dict[str, object]:
        return {
            "plan_id": handle.execution_plan_id,
            "owner_id": handle.owner_id,
            "generation": handle.fencing_generation,
        }
