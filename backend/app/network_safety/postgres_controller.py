from __future__ import annotations

from contextlib import contextmanager
import math
import time
from typing import Iterator

from sqlalchemy import Engine, text

from app.network_safety.controller import (
    DEFAULT_CONCURRENCY_WAIT_SECONDS,
    NetworkExecutionDenied,
    _validate_target_id,
)


ADVISORY_LOCK_NAMESPACE = 4_278_163
MAXIMUM_CONCURRENCY_BOUND = 1024


class PostgresNetworkExecutionController:
    def __init__(
        self,
        *,
        bind: Engine,
        permit_wait_seconds: float = DEFAULT_CONCURRENCY_WAIT_SECONDS,
        coordination_timeout_seconds: float = 1.0,
        poll_interval_seconds: float = 0.01,
        monotonic=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        for name, value in (
            ("permit_wait_seconds", permit_wait_seconds),
            ("coordination_timeout_seconds", coordination_timeout_seconds),
            ("poll_interval_seconds", poll_interval_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be finite and positive")
        self.bind = bind
        self.permit_wait_seconds = float(permit_wait_seconds)
        configured_timeout_ms = getattr(
            bind, "_network_coordination_statement_timeout_ms", None
        )
        required_timeout_ms = max(
            1, math.ceil(coordination_timeout_seconds * 1000)
        )
        if (
            not isinstance(configured_timeout_ms, int)
            or configured_timeout_ms <= 0
            or configured_timeout_ms > required_timeout_ms
        ):
            raise ValueError(
                "bind must configure a bounded startup statement timeout"
            )
        self._poll_interval = float(poll_interval_seconds)
        self._monotonic = monotonic
        self._sleep = sleep

    def disable_global(self) -> None:
        self._update_global(False)

    def enable_global(self) -> None:
        self._update_global(True)

    def disable_target(self, target_id: int) -> None:
        _validate_target_id(target_id)
        self._execute_write(
            "INSERT INTO network_disabled_targets (target_id, disabled_at) "
            "VALUES (:target_id, clock_timestamp()) "
            "ON CONFLICT (target_id) DO NOTHING",
            {"target_id": target_id},
        )

    def enable_target(self, target_id: int) -> None:
        _validate_target_id(target_id)
        self._execute_write(
            "DELETE FROM network_disabled_targets WHERE target_id=:target_id",
            {"target_id": target_id},
        )

    def check_enabled(self, target_id: int) -> None:
        _validate_target_id(target_id)
        connection = None
        try:
            connection = self.bind.connect()
            self._read_enabled_and_limit(connection, target_id)
            connection.commit()
        except NetworkExecutionDenied:
            if connection is not None:
                connection.rollback()
            raise
        except Exception as exc:
            if connection is not None:
                connection.invalidate(exc)
            self._coordination_failed(exc)
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def admission(self, target_id: int) -> Iterator[None]:
        _validate_target_id(target_id)
        self.check_enabled(target_id)
        deadline = self._monotonic() + self.permit_wait_seconds
        connection = None
        acquired_slot: int | None = None
        discard = False
        yielded = False
        try:
            connection = self.bind.connect()
            self._raise_if_permit_deadline_expired(deadline)
            while acquired_slot is None:
                try:
                    maximum = self._read_limit(connection)
                    for slot in range(1, maximum + 1):
                        self._raise_if_permit_deadline_expired(deadline)
                        acquired = connection.scalar(
                            text("SELECT pg_try_advisory_lock(:namespace, :slot)"),
                            {"namespace": ADVISORY_LOCK_NAMESPACE, "slot": slot},
                        )
                        if acquired is True:
                            acquired_slot = slot
                        self._raise_if_permit_deadline_expired(deadline)
                        if acquired_slot is not None:
                            break
                    connection.commit()
                    if acquired_slot is not None:
                        self._raise_if_permit_deadline_expired(deadline)
                except NetworkExecutionDenied:
                    connection.rollback()
                    raise
                except Exception as exc:
                    connection.invalidate(exc)
                    discard = True
                    self._coordination_failed(exc)
                if acquired_slot is not None:
                    break
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    self._raise_concurrency_timeout()
                self._sleep(min(self._poll_interval, remaining))

            fresh_maximum = self._read_enabled_and_limit(connection, target_id)
            if acquired_slot > fresh_maximum:
                raise RuntimeError("acquired slot exceeds shared configuration")
            connection.commit()
            yielded = True
            yield
        except NetworkExecutionDenied:
            raise
        except Exception as exc:
            if not yielded:
                if connection is not None:
                    connection.invalidate(exc)
                    discard = True
                self._coordination_failed(exc)
            raise
        finally:
            if connection is not None and acquired_slot is not None:
                try:
                    unlocked = connection.scalar(
                        text("SELECT pg_advisory_unlock(:namespace, :slot)"),
                        {
                            "namespace": ADVISORY_LOCK_NAMESPACE,
                            "slot": acquired_slot,
                        },
                    )
                    connection.commit()
                    if unlocked is not True:
                        discard = True
                        connection.invalidate()
                except Exception as exc:
                    discard = True
                    connection.invalidate(exc)
            if connection is not None:
                if discard and connection.invalidated is False:
                    connection.invalidate()
                connection.close()

    def _read_enabled_and_limit(self, connection, target_id: int) -> int:
        row = connection.execute(
            text(
                "SELECT control.global_enabled, control.maximum_concurrency, "
                "disabled.target_id IS NOT NULL AS target_disabled "
                "FROM network_global_control AS control "
                "LEFT JOIN network_disabled_targets AS disabled "
                "ON disabled.target_id=:target_id WHERE control.id=1"
            ),
            {"target_id": target_id},
        ).first()
        if row is None or not isinstance(row.maximum_concurrency, int) or not (
            1 <= row.maximum_concurrency <= MAXIMUM_CONCURRENCY_BOUND
        ):
            raise RuntimeError("invalid network coordination state")
        if not row.global_enabled:
            raise NetworkExecutionDenied(
                code="network_global_disabled",
                reason="Global network execution is disabled.",
            )
        if row.target_disabled:
            raise NetworkExecutionDenied(
                code="network_target_disabled",
                reason="Network execution is disabled for this Target.",
            )
        return row.maximum_concurrency

    def _raise_if_permit_deadline_expired(self, deadline: float) -> None:
        if self._monotonic() >= deadline:
            self._raise_concurrency_timeout()

    @staticmethod
    def _raise_concurrency_timeout() -> None:
        raise NetworkExecutionDenied(
            code="network_concurrency_timeout",
            reason="Network concurrency permit wait timed out.",
        )

    def _read_limit(self, connection) -> int:
        maximum = connection.scalar(
            text(
                "SELECT maximum_concurrency FROM network_global_control "
                "WHERE id=1"
            )
        )
        if not isinstance(maximum, int) or not (
            1 <= maximum <= MAXIMUM_CONCURRENCY_BOUND
        ):
            raise RuntimeError("invalid network coordination state")
        return maximum

    def _update_global(self, enabled: bool) -> None:
        self._execute_write(
            "UPDATE network_global_control SET global_enabled=:enabled, "
            "updated_at=clock_timestamp() WHERE id=1 RETURNING id",
            {"enabled": enabled},
            require_row=True,
        )

    def _execute_write(self, statement: str, values: dict, require_row=False) -> None:
        connection = None
        try:
            connection = self.bind.connect()
            result = connection.execute(text(statement), values)
            if require_row and result.first() is None:
                raise RuntimeError("missing network coordination state")
            connection.commit()
        except NetworkExecutionDenied:
            raise
        except Exception as exc:
            if connection is not None:
                connection.invalidate(exc)
            self._coordination_failed(exc)
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _coordination_failed(exc: Exception) -> None:
        raise NetworkExecutionDenied(
            code="network_coordination_failed",
            reason="Shared network coordination failed.",
        ) from exc
