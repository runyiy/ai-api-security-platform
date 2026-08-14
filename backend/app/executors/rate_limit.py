import threading
import time


class InMemoryRateLimiter:
    def __init__(
        self,
        requests_per_second: float,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError(
                "requests_per_second must be > 0"
            )

        self._minimum_interval = (
            1.0 / requests_per_second
        )

        self._next_allowed_at: dict[str, float] = {}

        self._lock = threading.Lock()

    def wait(
        self,
        *,
        key: str,
    ) -> None:
        with self._lock:
            now = time.monotonic()

            next_allowed = (
                self._next_allowed_at.get(
                    key,
                    now,
                )
            )

            delay = max(
                0.0,
                next_allowed - now,
            )

            reserved_time = max(
                now,
                next_allowed,
            )

            self._next_allowed_at[key] = (
                reserved_time
                + self._minimum_interval
            )

        if delay > 0:
            time.sleep(delay)