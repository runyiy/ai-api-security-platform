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
                ), db_time AS MATERIALIZED (SELECT clock_timestamp() AS now)
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
                RETURNING execution_plan_id, fencing_generation, phase, updated_at"""
            ),
            self._values(handle),
        )
        if row is not None:
            return ProgressState(*row)
        phase = self._read_phase(handle.execution_plan_id)
        if phase in {"network_started", "in_doubt"}:
            raise ExecutionInDoubtError("ExecutionPlan execution is in doubt.")
        raise ExecutionProgressLostError("ExecutionPlan progress fencing was lost.")

    def mark_network_started(self, handle: ClaimHandle) -> ProgressState:
        row = self._run(
            text(
                """WITH current_claim AS MATERIALIZED (
                    SELECT execution_plan_id FROM execution_plan_claims
                    WHERE execution_plan_id=:plan_id AND owner_id=:owner_id
                      AND fencing_generation=:generation
                      AND lease_expires_at > clock_timestamp()
                    FOR UPDATE
                )
                UPDATE execution_plan_progress AS progress SET
                    phase='network_started', updated_at=clock_timestamp()
                FROM current_claim
                WHERE progress.execution_plan_id=:plan_id
                  AND progress.fencing_generation=:generation
                  AND progress.phase='pre_network'
                RETURNING progress.execution_plan_id,
                          progress.fencing_generation, progress.phase,
                          progress.updated_at"""
            ),
            self._values(handle),
        )
        if row is None:
            raise ExecutionProgressLostError("ExecutionPlan progress fencing was lost.")
        return ProgressState(*row)

    def _read_phase(self, plan_id: int) -> str | None:
        row = self._run(
            text("SELECT phase FROM execution_plan_progress WHERE execution_plan_id=:plan_id"),
            {"plan_id": plan_id},
        )
        return row[0] if row is not None else None

    def _run(self, statement, values: dict[str, object]):
        for _ in range(self._max_retries):
            try:
                with self._sessions.begin() as db:
                    db.execute(
                        text("SELECT set_config('lock_timeout', :value, true)"),
                        {"value": self._timeout},
                    )
                    db.execute(
                        text("SELECT set_config('statement_timeout', :value, true)"),
                        {"value": self._timeout},
                    )
                    result = db.execute(statement, values).first()
                    return tuple(result) if result is not None else None
            except Exception:
                continue
        raise ExecutionProgressCoordinationError(
            "ExecutionPlan progress coordination failed."
        )

    @staticmethod
    def _values(handle: ClaimHandle) -> dict[str, object]:
        return {
            "plan_id": handle.execution_plan_id,
            "owner_id": handle.owner_id,
            "generation": handle.fencing_generation,
        }
