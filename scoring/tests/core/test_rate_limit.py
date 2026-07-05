import pytest

from core.rate_limit import SlidingWindowRateLimiter


def test_rate_limiter_waits_until_window_has_free_slot() -> None:
    current_time = 0.0
    sleep_calls: list[float] = []

    def clock() -> float:
        return current_time

    def sleeper(seconds: float) -> None:
        nonlocal current_time
        sleep_calls.append(seconds)
        current_time += seconds

    limiter = SlidingWindowRateLimiter(
        max_calls=1,
        period_seconds=1,
        clock=clock,
        sleeper=sleeper,
    )

    limiter.wait()
    limiter.wait()

    assert sleep_calls == [1.0]


def test_rate_limiter_rejects_invalid_limit() -> None:
    with pytest.raises(ValueError, match="max_calls"):
        SlidingWindowRateLimiter(max_calls=0)
