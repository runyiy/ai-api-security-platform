from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker


class ExecutionClaimError(RuntimeError):
    pass


class ExecutionClaimUnavailableError(ExecutionClaimError):
    pass


class ExecutionClaimLostError(ExecutionClaimError):
    pass


class ExecutionClaimCoordinationError(ExecutionClaimError):
    pass


@dataclass(frozen=True)
class ClaimHandle:
    execution_plan_id: int
    owner_id: str
    fencing_generation: int
    lease_expires_at: datetime
    database_now: datetime


class ExecutionPlanClaimService:
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
        self._sessions = sessionmaker(
            bind=bind, autoflush=False, expire_on_commit=False
        )
        self._max_retries = max_retries
        self._timeout = f"{max(1, math.ceil(attempt_timeout_seconds * 1000))}ms"

    def acquire(
        self, execution_plan_id: int, owner_id: str, *, lease_seconds: float
    ) -> ClaimHandle:
        owner_id = self._validate(owner_id, lease_seconds)
        statement = text(
            """
            WITH db_time AS (SELECT clock_timestamp() AS now)
            INSERT INTO execution_plan_claims AS claim (
                execution_plan_id, owner_id, fencing_generation, lease_expires_at
            ) VALUES (
                :plan_id, :owner_id, 1,
                (SELECT now FROM db_time)
                    + make_interval(secs => CAST(:lease AS double precision))
            )
            ON CONFLICT (execution_plan_id) DO UPDATE SET
                owner_id = EXCLUDED.owner_id,
                fencing_generation = claim.fencing_generation + 1,
                lease_expires_at = (SELECT now FROM db_time)
                    + make_interval(secs => CAST(:lease AS double precision))
            WHERE claim.owner_id IS NULL
               OR claim.lease_expires_at <= (SELECT now FROM db_time)
            RETURNING execution_plan_id, owner_id, fencing_generation,
                      lease_expires_at, (SELECT now FROM db_time) AS database_now
            """
        )
        row = self._run(statement, {
            "plan_id": execution_plan_id,
            "owner_id": owner_id,
            "lease": lease_seconds,
        })
        if row is None:
            raise ExecutionClaimUnavailableError("ExecutionPlan claim is unavailable.")
        return ClaimHandle(*row)

    def assert_current(
        self, handle: ClaimHandle, *, db: Session | None = None
    ) -> None:
        statement = text(
            """SELECT execution_plan_id FROM execution_plan_claims
            WHERE execution_plan_id=:plan_id AND owner_id=:owner_id
              AND fencing_generation=:generation
              AND lease_expires_at > clock_timestamp()
            FOR UPDATE"""
        )
        if db is None:
            row = self._run(statement, self._handle_values(handle))
        else:
            try:
                db.execute(
                    text("SELECT set_config('lock_timeout', :value, true)"),
                    {"value": self._timeout},
                )
                db.execute(
                    text("SELECT set_config('statement_timeout', :value, true)"),
                    {"value": self._timeout},
                )
                result = db.execute(statement, self._handle_values(handle)).first()
                row = tuple(result) if result is not None else None
            except Exception as exc:
                raise ExecutionClaimCoordinationError(
                    "Execution claim coordination failed."
                ) from exc
        if row is None:
            raise ExecutionClaimLostError("ExecutionPlan claim was lost.")

    def renew(self, handle: ClaimHandle, *, lease_seconds: float) -> ClaimHandle:
        self._validate(handle.owner_id, lease_seconds)
        row = self._run(
            text(
                """WITH db_time AS (SELECT clock_timestamp() AS now)
                UPDATE execution_plan_claims SET lease_expires_at =
                  (SELECT now FROM db_time)
                    + make_interval(secs => CAST(:lease AS double precision))
                WHERE execution_plan_id=:plan_id AND owner_id=:owner_id
                  AND fencing_generation=:generation
                  AND lease_expires_at > (SELECT now FROM db_time)
                RETURNING execution_plan_id, owner_id, fencing_generation,
                  lease_expires_at, (SELECT now FROM db_time) AS database_now"""
            ),
            {**self._handle_values(handle), "lease": lease_seconds},
        )
        if row is None:
            raise ExecutionClaimLostError("ExecutionPlan claim was lost.")
        return ClaimHandle(*row)

    def release(self, handle: ClaimHandle) -> None:
        row = self._run(
            text(
                """UPDATE execution_plan_claims SET owner_id=NULL,
                     lease_expires_at=clock_timestamp()
                WHERE execution_plan_id=:plan_id AND owner_id=:owner_id
                  AND fencing_generation=:generation
                RETURNING execution_plan_id"""
            ),
            self._handle_values(handle),
        )
        if row is not None:
            return
        state = self._run(
            text(
                """SELECT execution_plan_id FROM execution_plan_claims
                WHERE execution_plan_id=:plan_id AND owner_id IS NULL
                  AND fencing_generation=:generation"""
            ),
            self._handle_values(handle),
        )
        if state is None:
            raise ExecutionClaimLostError("ExecutionPlan claim was lost.")

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
        raise ExecutionClaimCoordinationError("Execution claim coordination failed.")

    @staticmethod
    def _validate(owner_id: str, lease_seconds: float) -> str:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128:
            raise ValueError("owner_id must be non-empty and bounded")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, (int, float))
            or not math.isfinite(lease_seconds)
            or not 0 < lease_seconds <= 300
        ):
            raise ValueError("lease_seconds must be finite and bounded")
        return owner_id

    @staticmethod
    def _handle_values(handle: ClaimHandle) -> dict[str, object]:
        return {
            "plan_id": handle.execution_plan_id,
            "owner_id": handle.owner_id,
            "generation": handle.fencing_generation,
        }
