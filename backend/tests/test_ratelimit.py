"""The token bucket is what stands between us and a 429 storm.

MAI allows 2-12 requests per minute, so the limiter is not a nicety -- it is
the component that decides whether the product works under load.
"""

from __future__ import annotations

import asyncio

import pytest

from app.foundry.ratelimit import TokenBucket, backoff_delay


async def test_burst_is_capped_at_the_minute_rate() -> None:
    bucket = TokenBucket(rate_per_minute=4)
    # Starts full, so four immediate acquires cost nothing.
    for _ in range(4):
        assert await bucket.acquire() == 0.0
    assert bucket.available < 1.0


async def test_fifth_request_waits_for_a_refill() -> None:
    # 120/min = 2 per second, so the wait is short enough to actually test.
    bucket = TokenBucket(rate_per_minute=120)
    for _ in range(int(bucket.capacity)):
        await bucket.acquire()

    start = asyncio.get_event_loop().time()
    waited = await bucket.acquire()
    elapsed = asyncio.get_event_loop().time() - start

    assert waited > 0, "bucket handed out a token it did not have"
    assert elapsed >= 0.4, "acquire returned before the refill could happen"


async def test_drain_surrenders_the_burst_allowance() -> None:
    # After a 429 the service has told us our accounting is optimistic, so the
    # remaining burst must not be spent on further rejected requests.
    bucket = TokenBucket(rate_per_minute=60)
    assert bucket.available > 1.0
    await bucket.drain()
    assert bucket.available < 1.0


async def test_concurrent_acquires_never_exceed_the_budget() -> None:
    bucket = TokenBucket(rate_per_minute=60)  # 1/sec, capacity 60
    bucket._tokens = 3.0  # start with exactly three

    granted = 0

    async def take() -> None:
        nonlocal granted
        await bucket.acquire()
        granted += 1

    # Ten racers, three tokens. Only three may pass immediately.
    tasks = [asyncio.create_task(take()) for _ in range(10)]
    await asyncio.sleep(0.05)
    assert granted == 3, f"{granted} requests got through on a 3-token budget"

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def test_rate_must_be_positive() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate_per_minute=0)


def test_backoff_grows_and_is_capped() -> None:
    # Full jitter means each delay is a random draw below the ceiling, so
    # assert the bound rather than the value.
    for attempt in range(1, 10):
        delay = backoff_delay(attempt, base=20, cap=300)
        assert 0.0 <= delay <= 300.0


def test_backoff_respects_retry_after_as_a_floor() -> None:
    # The service knows its own reset window better than our guess does.
    delay = backoff_delay(1, base=1, cap=300, retry_after=45.0)
    assert delay >= 45.0


def test_backoff_never_exceeds_the_cap_even_with_retry_after() -> None:
    delay = backoff_delay(1, base=20, cap=120, retry_after=600.0)
    assert delay <= 120.0
