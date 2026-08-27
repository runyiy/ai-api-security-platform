from collections.abc import Callable
import math
import threading
import time
from typing import Protocol

from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker


class RateLimitConfigurationError(ValueError):
    pass


class RateLimitCoordinationError(RuntimeError):
    pass


class RateLimiter(Protocol):
    def wait(
        self, *, key: str, requested_requests_per_second: float
    ) -> None: ...


def validate_requests_per_second(
    value: float,
    *,
    field_name: str,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise RateLimitConfigurationError(
            f"{field_name} must be finite and greater than zero"
        )

    return float(value)


class InMemoryRateLimiter:
    def __init__(
        self,
        requests_per_second: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._platform_requests_per_second = (
            validate_requests_per_second(
                requests_per_second,
                field_name="requests_per_second",
            )
        )

        self._last_reserved_at: dict[str, float] = {}

        self._lock = threading.Lock()
        self._monotonic = monotonic
        self._sleep = sleep

    def wait(
        self,
        *,
        key: str,
        requested_requests_per_second: float,
    ) -> None:
        requested_rate = validate_requests_per_second(
            requested_requests_per_second,
            field_name="requested_requests_per_second",
        )
        effective_rate = min(
            self._platform_requests_per_second,
            requested_rate,
        )
        minimum_interval = 1.0 / effective_rate

        with self._lock:
            now = self._monotonic()

            last_reserved = (
                self._last_reserved_at.get(
                    key,
                    now - minimum_interval,
                )
            )

            next_allowed = last_reserved + minimum_interval

            delay = max(
                0.0,
                next_allowed - now,
            )

            reserved_time = max(
                now,
                next_allowed,
            )

            self._last_reserved_at[key] = reserved_time

        if delay > 0:
            self._sleep(delay)


class PostgresRateLimiter:
    def __init__(
        self,
        requests_per_second: float,
        *,
        bind: Engine,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
        attempt_timeout_seconds: float = 1.0,
    ) -> None:
        self._platform_requests_per_second = validate_requests_per_second(
            requests_per_second,
            field_name="requests_per_second",
        )
        if isinstance(max_retries, bool) or not isinstance(max_retries, int):
            raise ValueError("max_retries must be a positive integer")
        if max_retries <= 0:
            raise ValueError("max_retries must be a positive integer")
        if (
            isinstance(attempt_timeout_seconds, bool)
            or not isinstance(attempt_timeout_seconds, (int, float))
            or not math.isfinite(attempt_timeout_seconds)
            or attempt_timeout_seconds <= 0
        ):
            raise ValueError("attempt_timeout_seconds must be finite and positive")
        self._session_factory = sessionmaker(
            bind=bind, autoflush=False, expire_on_commit=False
        )
        self._sleep = sleep
        self._max_retries = max_retries
        self._attempt_timeout_milliseconds = max(
            1, math.ceil(float(attempt_timeout_seconds) * 1000)
        )

    def wait(
        self, *, key: str, requested_requests_per_second: float
    ) -> None:
        delay = self.reserve_delay(
            key=key,
            requested_requests_per_second=requested_requests_per_second,
        )
        if delay > 0:
            self._sleep(delay)

    def reserve_delay(
        self, *, key: str, requested_requests_per_second: float
    ) -> float:
        requested_rate = validate_requests_per_second(
            requested_requests_per_second,
            field_name="requested_requests_per_second",
        )
        effective_rate = min(
            self._platform_requests_per_second,
            requested_rate,
        )
        minimum_interval = 1.0 / effective_rate
        for _ in range(self._max_retries):
            try:
                return self._reserve_once(
                    key=key,
                    minimum_interval=minimum_interval,
                )
            except Exception:
                continue
        raise RateLimitCoordinationError("Shared rate coordination failed.")

    def _reserve_once(self, *, key: str, minimum_interval: float) -> float:
        statement = text(
            """
            INSERT INTO rate_reservation_states AS state (key, next_allowed_at)
            VALUES (
                :key,
                clock_timestamp()
                    + make_interval(secs => CAST(:minimum_interval AS double precision))
            )
            ON CONFLICT (key) DO UPDATE
            SET next_allowed_at = GREATEST(
                    state.next_allowed_at,
                    clock_timestamp()
                ) + make_interval(
                    secs => CAST(:minimum_interval AS double precision)
                )
            RETURNING GREATEST(
                0.0,
                EXTRACT(EPOCH FROM (
                    next_allowed_at
                    - make_interval(
                        secs => CAST(:minimum_interval AS double precision)
                    )
                    - clock_timestamp()
                ))
            )
            """
        )
        with self._session_factory.begin() as db:
            timeout = f"{self._attempt_timeout_milliseconds}ms"
            db.execute(
                text("SELECT set_config('lock_timeout', :timeout, true)"),
                {"timeout": timeout},
            )
            db.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": timeout},
            )
            delay = db.scalar(
                statement,
                {"key": key, "minimum_interval": minimum_interval},
            )
            if delay is None:
                raise RuntimeError("rate reservation returned no delay")
            return max(0.0, float(delay))
