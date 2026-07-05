from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic, sleep


Clock = Callable[[], float]
Sleeper = Callable[[float], None]


@dataclass(slots=True)
class SlidingWindowRateLimiter:
    """Ограничивает количество операций в скользящем временном окне."""

    max_calls: int
    period_seconds: float = 1.0
    clock: Clock = monotonic
    sleeper: Sleeper = sleep
    _timestamps: deque[float] = field(default_factory=deque, init=False)
    _lock: Lock = field(default_factory=Lock, init=False)

    def __post_init__(self) -> None:
        if self.max_calls < 1:
            raise ValueError("max_calls должен быть больше нуля")
        if self.period_seconds <= 0:
            raise ValueError("period_seconds должен быть больше нуля")

    def wait(self) -> None:
        """Ждёт, пока операция попадёт в разрешённый лимит."""

        while True:
            wait_seconds = self._reserve_or_get_wait()
            if wait_seconds <= 0:
                return

            self.sleeper(wait_seconds)

    def _reserve_or_get_wait(self) -> float:
        with self._lock:
            now = self.clock()
            self._drop_expired(now)

            if len(self._timestamps) < self.max_calls:
                self._timestamps.append(now)
                return 0

            return max(self.period_seconds - (now - self._timestamps[0]), 0)

    def _drop_expired(self, now: float) -> None:
        min_allowed_time = now - self.period_seconds
        while self._timestamps and self._timestamps[0] <= min_allowed_time:
            self._timestamps.popleft()
