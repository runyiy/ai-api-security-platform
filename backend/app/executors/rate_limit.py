from collections.abc import Callable
import math
import threading
import time


class RateLimitConfigurationError(ValueError):
    pass


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
