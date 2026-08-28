"""Single-process network admission controls.

These switches and permits are intentionally process-local. Multiple execution
processes remain prohibited until M8 adds shared coordination. Disabling a
switch narrows admission for new connections; it does not abort a TCP
connection that was already established.
"""

from __future__ import annotations

from contextlib import contextmanager
import math
from threading import BoundedSemaphore, Lock
from typing import Iterator, Protocol


DEFAULT_MAX_CONCURRENT_NETWORK_REQUESTS = 4
DEFAULT_CONCURRENCY_WAIT_SECONDS = 5.0


class NetworkExecutionDenied(RuntimeError):
    def __init__(self, *, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


class NetworkExecutionControllerProtocol(Protocol):
    def check_enabled(self, target_id: int) -> None: ...

    def admission(self, target_id: int): ...


class NetworkExecutionController:
    """Thread-safe, process-local network controls for single-process M6 use."""

    def __init__(
        self,
        *,
        maximum_concurrency: int = DEFAULT_MAX_CONCURRENT_NETWORK_REQUESTS,
        permit_wait_seconds: float = DEFAULT_CONCURRENCY_WAIT_SECONDS,
    ) -> None:
        if (
            isinstance(maximum_concurrency, bool)
            or not isinstance(maximum_concurrency, int)
            or maximum_concurrency <= 0
        ):
            raise ValueError("maximum_concurrency must be a positive integer")
        if (
            isinstance(permit_wait_seconds, bool)
            or not isinstance(permit_wait_seconds, (int, float))
            or not math.isfinite(permit_wait_seconds)
            or permit_wait_seconds <= 0
        ):
            raise ValueError("permit_wait_seconds must be finite and positive")
        self.maximum_concurrency = maximum_concurrency
        self.permit_wait_seconds = float(permit_wait_seconds)
        self._state_lock = Lock()
        self._global_enabled = True
        self._disabled_target_ids: set[int] = set()
        self._permits = BoundedSemaphore(maximum_concurrency)

    def disable_global(self) -> None:
        with self._state_lock:
            self._global_enabled = False

    def enable_global(self) -> None:
        with self._state_lock:
            self._global_enabled = True

    def disable_target(self, target_id: int) -> None:
        _validate_target_id(target_id)
        with self._state_lock:
            self._disabled_target_ids.add(target_id)

    def enable_target(self, target_id: int) -> None:
        _validate_target_id(target_id)
        with self._state_lock:
            self._disabled_target_ids.discard(target_id)

    def check_enabled(self, target_id: int) -> None:
        _validate_target_id(target_id)
        with self._state_lock:
            if not self._global_enabled:
                raise NetworkExecutionDenied(
                    code="network_global_disabled",
                    reason="Global network execution is disabled.",
                )
            if target_id in self._disabled_target_ids:
                raise NetworkExecutionDenied(
                    code="network_target_disabled",
                    reason="Network execution is disabled for this Target.",
                )

    @contextmanager
    def admission(self, target_id: int) -> Iterator[None]:
        self.check_enabled(target_id)
        acquired = self._permits.acquire(timeout=self.permit_wait_seconds)
        if not acquired:
            raise NetworkExecutionDenied(
                code="network_concurrency_timeout",
                reason="Network concurrency permit wait timed out.",
            )
        try:
            self.check_enabled(target_id)
            yield
        finally:
            self._permits.release()


def _validate_target_id(target_id: int) -> None:
    if isinstance(target_id, bool) or not isinstance(target_id, int) or target_id <= 0:
        raise NetworkExecutionDenied(
            code="network_target_invalid",
            reason="Network Target identifier is invalid.",
        )
