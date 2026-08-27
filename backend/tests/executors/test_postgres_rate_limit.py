from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.db.models.rate_reservation_state import RateReservationState
from app.db.session import SessionLocal, engine
from app.executors.rate_limit import (
    PostgresRateLimiter,
    RateLimitConfigurationError,
    RateLimitCoordinationError,
)


@pytest.fixture
def rate_key() -> str:
    key = f"target:test-{uuid4()}"
    yield key
    with SessionLocal.begin() as db:
        db.execute(
            delete(RateReservationState).where(RateReservationState.key == key)
        )


def limiter(
    *,
    sleep=lambda delay: None,
    retries: int = 3,
    attempt_timeout_seconds: float = 1.0,
) -> PostgresRateLimiter:
    return PostgresRateLimiter(
        requests_per_second=2.0,
        bind=engine,
        sleep=sleep,
        max_retries=retries,
        attempt_timeout_seconds=attempt_timeout_seconds,
    )


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_platform_and_requested_rates_fail_closed(
    rate_key: str, value: float
) -> None:
    with pytest.raises(RateLimitConfigurationError):
        PostgresRateLimiter(requests_per_second=value, bind=engine)
    with pytest.raises(RateLimitConfigurationError):
        limiter().reserve_delay(
            key=rate_key, requested_requests_per_second=value
        )


@pytest.mark.parametrize(
    ("requested_rate", "minimum_delay"),
    [(100.0, 0.4), (0.5, 1.8)],
)
def test_first_and_sequential_reservations_use_effective_minimum_rate(
    rate_key: str, requested_rate: float, minimum_delay: float
) -> None:
    delays: list[float] = []
    shared = limiter(sleep=delays.append)

    shared.wait(key=rate_key, requested_requests_per_second=requested_rate)
    shared.wait(key=rate_key, requested_requests_per_second=requested_rate)

    assert len(delays) == 1
    assert minimum_delay <= delays[0] <= 2.0


def test_reservation_is_committed_and_lock_released_before_sleep(
    rate_key: str,
) -> None:
    observed: list[bool] = []

    def inspect_during_sleep(delay: float) -> None:
        with SessionLocal() as db:
            state = db.get(RateReservationState, rate_key)
            assert state is not None
            state.next_allowed_at = state.next_allowed_at
            db.flush()
            observed.append(True)

    shared = limiter(sleep=inspect_during_sleep)
    shared.wait(key=rate_key, requested_requests_per_second=2.0)
    shared.wait(key=rate_key, requested_requests_per_second=2.0)

    assert observed == [True]


def test_independent_sessions_serialize_same_new_key(rate_key: str) -> None:
    def reserve(_: int) -> float:
        return limiter().reserve_delay(
            key=rate_key, requested_requests_per_second=2.0
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        delays = sorted(pool.map(reserve, range(4)))

    assert delays[0] < 0.1
    assert all(later > earlier for earlier, later in zip(delays, delays[1:]))
    assert delays[-1] >= 1.3


def test_different_keys_reserve_without_shared_schedule() -> None:
    keys = [f"target:test-{uuid4()}" for _ in range(2)]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            delays = list(
                pool.map(
                    lambda key: limiter().reserve_delay(
                        key=key, requested_requests_per_second=2.0
                    ),
                    keys,
                )
            )
        assert all(delay < 0.1 for delay in delays)
    finally:
        with SessionLocal.begin() as db:
            db.execute(
                delete(RateReservationState).where(
                    RateReservationState.key.in_(keys)
                )
            )


def test_subprocess_observes_committed_postgres_reservation(rate_key: str) -> None:
    first = limiter().reserve_delay(
        key=rate_key, requested_requests_per_second=0.1
    )
    code = (
        "from app.db.session import engine; "
        "from app.executors.rate_limit import PostgresRateLimiter; "
        "import sys; "
        "value=PostgresRateLimiter(requests_per_second=2.0,bind=engine)"
        ".reserve_delay(key=sys.argv[1],requested_requests_per_second=0.1); "
        "print(value)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, rate_key],
        cwd=os.getcwd(),
        check=True,
        capture_output=True,
        text=True,
    )

    assert first < 0.1
    assert float(result.stdout.strip()) >= 5.0


def test_bounded_coordination_failure_is_sanitized(
    rate_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = limiter(retries=2)
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("database details must not escape")

    monkeypatch.setattr(shared, "_reserve_once", fail)
    with pytest.raises(RateLimitCoordinationError) as raised:
        shared.reserve_delay(key=rate_key, requested_requests_per_second=2.0)

    assert calls == 2
    assert str(raised.value) == "Shared rate coordination failed."
    assert "database details" not in str(raised.value)


def test_locked_key_attempts_time_out_and_exhaust_bounded_retries(
    rate_key: str,
) -> None:
    shared = limiter(retries=2, attempt_timeout_seconds=0.05)
    shared.reserve_delay(key=rate_key, requested_requests_per_second=2.0)

    with SessionLocal() as holder:
        holder.begin()
        locked = holder.scalar(
            select(RateReservationState)
            .where(RateReservationState.key == rate_key)
            .with_for_update()
        )
        assert locked is not None
        original_next_allowed_at = locked.next_allowed_at

        started = time.monotonic()
        with pytest.raises(RateLimitCoordinationError) as raised:
            shared.reserve_delay(
                key=rate_key,
                requested_requests_per_second=2.0,
            )
        elapsed = time.monotonic() - started

        assert 0.08 <= elapsed < 1.0
        assert str(raised.value) == "Shared rate coordination failed."
        holder.rollback()

    with SessionLocal() as db:
        persisted = db.get(RateReservationState, rate_key)
        assert persisted is not None
        assert persisted.next_allowed_at == original_next_allowed_at


def test_coordination_table_contains_only_bounded_schedule_state() -> None:
    assert set(RateReservationState.__table__.columns.keys()) == {
        "key",
        "next_allowed_at",
    }
