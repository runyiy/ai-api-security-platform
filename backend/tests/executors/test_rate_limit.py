import pytest

from app.executors.rate_limit import (
    InMemoryRateLimiter,
    RateLimitConfigurationError,
)


class FakeTime:
    def __init__(self) -> None:
        self.current = 100.0
        self.delays: list[float] = []

    def monotonic(self) -> float:
        return self.current

    def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        self.current += delay


def build_limiter(
    *,
    platform_requests_per_second: float,
    fake_time: FakeTime,
) -> InMemoryRateLimiter:
    return InMemoryRateLimiter(
        requests_per_second=platform_requests_per_second,
        monotonic=fake_time.monotonic,
        sleep=fake_time.sleep,
    )


def test_profile_limit_tightens_platform_ceiling() -> None:
    fake_time = FakeTime()
    limiter = build_limiter(
        platform_requests_per_second=2.0,
        fake_time=fake_time,
    )

    limiter.wait(
        key="target:1",
        requested_requests_per_second=0.5,
    )
    limiter.wait(
        key="target:1",
        requested_requests_per_second=0.5,
    )

    assert fake_time.delays == [2.0]


def test_profile_cannot_widen_platform_ceiling() -> None:
    fake_time = FakeTime()
    limiter = build_limiter(
        platform_requests_per_second=2.0,
        fake_time=fake_time,
    )

    limiter.wait(
        key="target:1",
        requested_requests_per_second=100.0,
    )
    limiter.wait(
        key="target:1",
        requested_requests_per_second=100.0,
    )

    assert fake_time.delays == [0.5]


def test_separate_target_schedules_are_independent() -> None:
    fake_time = FakeTime()
    limiter = build_limiter(
        platform_requests_per_second=2.0,
        fake_time=fake_time,
    )

    limiter.wait(
        key="target:1",
        requested_requests_per_second=2.0,
    )
    limiter.wait(
        key="target:2",
        requested_requests_per_second=2.0,
    )

    assert fake_time.delays == []

    limiter.wait(
        key="target:1",
        requested_requests_per_second=2.0,
    )

    assert fake_time.delays == [0.5]


def test_state_persists_across_repeated_waits() -> None:
    fake_time = FakeTime()
    limiter = build_limiter(
        platform_requests_per_second=4.0,
        fake_time=fake_time,
    )

    for _ in range(3):
        limiter.wait(
            key="target:1",
            requested_requests_per_second=4.0,
        )

    assert fake_time.delays == [0.25, 0.25]


@pytest.mark.parametrize(
    "invalid_rate",
    [0.0, -1.0, float("nan"), float("inf")],
)
def test_invalid_requested_rate_is_rejected(
    invalid_rate: float,
) -> None:
    fake_time = FakeTime()
    limiter = build_limiter(
        platform_requests_per_second=2.0,
        fake_time=fake_time,
    )

    with pytest.raises(RateLimitConfigurationError):
        limiter.wait(
            key="target:1",
            requested_requests_per_second=invalid_rate,
        )

    assert fake_time.delays == []
