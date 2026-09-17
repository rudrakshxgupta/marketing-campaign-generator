"""Token-bucket limiter for the MAI image endpoints.

MAI Global Standard allows 2-12 requests per minute depending on tier, which is
low enough that it is the binding constraint on the whole product. A single
in-process bucket is provably correct while there is exactly one worker; when
that stops being true the same interface can be backed by Redis.
"""

from __future__ import annotations

import asyncio
import random
import time


class TokenBucket:
    """Async token bucket refilling at ``rate_per_minute`` tokens per minute."""

    def __init__(self, rate_per_minute: float, capacity: float | None = None) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        self.rate_per_second = rate_per_minute / 60.0
        # Burst no larger than one minute's worth, and at least one token so a
        # single request is always eventually servable.
        self.capacity = max(1.0, capacity if capacity is not None else rate_per_minute)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        self._updated = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_second)

    async def acquire(self) -> float:
        """Block until a token is available. Returns seconds spent waiting."""
        waited = 0.0
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                deficit = 1.0 - self._tokens
                sleep_for = deficit / self.rate_per_second
            await asyncio.sleep(sleep_for)
            waited += sleep_for

    async def drain(self) -> None:
        """Empty the bucket.

        Called after a 429: the service has told us our own accounting is
        optimistic, so we give up the burst allowance rather than immediately
        spending it on another rejected request.
        """
        async with self._lock:
            self._refill()
            self._tokens = 0.0

    @property
    def available(self) -> float:
        self._refill()
        return self._tokens


def backoff_delay(
    attempt: int,
    *,
    base: float = 20.0,
    cap: float = 300.0,
    retry_after: float | None = None,
) -> float:
    """Full-jitter exponential backoff, in seconds.

    ``attempt`` is 1-based. A ``Retry-After`` from the service is respected as a
    floor, since it reflects the real reset window rather than our guess.
    """
    ceiling = min(cap, base * (2 ** max(0, attempt - 1)))
    delay = random.uniform(0.0, ceiling)
    if retry_after is not None:
        delay = max(delay, retry_after)
    return min(delay, cap)
