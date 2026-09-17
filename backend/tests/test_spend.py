"""Spend controls.

This logic protects real credits, so it is tested harder than its size
suggests. The failure this exists to prevent is not a user generating too
much -- it is a retry loop or a runaway test quietly spending a month of
quota in an afternoon.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.foundry.budget import (
    BudgetedImageBackend,
    BudgetExceeded,
    Usage,
)
from app.foundry.cache import CachingImageBackend
from app.foundry.mock_client import MockImageClient


class CountingBackend:
    """Mock that records how many times it was actually called."""

    def __init__(self) -> None:
        self.calls = 0
        self._inner = MockImageClient()

    async def generate(self, **kwargs):
        self.calls += 1
        return await self._inner.generate(**kwargs)

    async def edit(self, **kwargs):
        self.calls += 1
        return await self._inner.edit(**kwargs)


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

async def test_identical_request_is_served_from_cache(tmp_path: Path) -> None:
    counter = CountingBackend()
    cache = CachingImageBackend(counter, root=tmp_path / "cache")

    args = {"prompt": "a bottle on wood", "width": 1024, "height": 1024}
    first = await cache.generate(**args)
    second = await cache.generate(**args)

    # The whole point: the user re-clicking Generate costs nothing.
    assert counter.calls == 1, "second identical request hit the service"
    assert first.png == second.png
    assert cache.stats.hits == 1
    assert cache.stats.calls_saved == 1


async def test_a_different_prompt_is_a_different_image(tmp_path: Path) -> None:
    counter = CountingBackend()
    cache = CachingImageBackend(counter, root=tmp_path / "cache")

    await cache.generate(prompt="a bottle", width=1024, height=1024)
    await cache.generate(prompt="a candle", width=1024, height=1024)
    assert counter.calls == 2


async def test_same_prompt_at_a_different_size_is_a_different_image(
    tmp_path: Path,
) -> None:
    # Cropping a square into a story does not work, so these must not share a
    # cache entry.
    counter = CountingBackend()
    cache = CachingImageBackend(counter, root=tmp_path / "cache")

    await cache.generate(prompt="a bottle", width=1024, height=1024)
    await cache.generate(prompt="a bottle", width=768, height=1365)
    assert counter.calls == 2


async def test_draft_and_final_do_not_share_an_entry(tmp_path: Path) -> None:
    counter = CountingBackend()
    cache = CachingImageBackend(counter, root=tmp_path / "cache")

    await cache.generate(prompt="a bottle", width=1024, height=1024, draft=True)
    await cache.generate(prompt="a bottle", width=1024, height=1024, draft=False)
    assert counter.calls == 2, "Flash output must not be served as a final render"


async def test_bypass_forces_a_fresh_generation(tmp_path: Path) -> None:
    # Wanting a different result must be deliberate, never an accident of
    # impatience -- which is why it is a flag rather than the default.
    counter = CountingBackend()
    cache = CachingImageBackend(counter, root=tmp_path / "cache")

    args = {"prompt": "a bottle", "width": 1024, "height": 1024}
    await cache.generate(**args)
    await cache.generate(**args, bypass_cache=True)
    assert counter.calls == 2


async def test_cache_survives_a_restart(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    args = {"prompt": "a bottle", "width": 1024, "height": 1024}

    first_counter = CountingBackend()
    await CachingImageBackend(first_counter, root=root).generate(**args)

    # A fresh process, same disk.
    second_counter = CountingBackend()
    await CachingImageBackend(second_counter, root=root).generate(**args)

    assert second_counter.calls == 0, "cache did not survive a restart"


async def test_edits_are_keyed_on_the_input_image(tmp_path: Path) -> None:
    counter = CountingBackend()
    cache = CachingImageBackend(counter, root=tmp_path / "cache")

    seed_a = (await cache.generate(prompt="a", width=1024, height=1024)).png
    seed_b = (await cache.generate(prompt="b", width=1024, height=1024)).png
    before = counter.calls

    await cache.edit(prompt="restage", image=seed_a)
    await cache.edit(prompt="restage", image=seed_a)   # repeat -> cached
    await cache.edit(prompt="restage", image=seed_b)   # different input -> not

    assert counter.calls - before == 2


async def test_disabling_the_cache_always_calls_through(tmp_path: Path) -> None:
    counter = CountingBackend()
    cache = CachingImageBackend(counter, root=tmp_path / "cache", enabled=False)

    args = {"prompt": "a bottle", "width": 1024, "height": 1024}
    await cache.generate(**args)
    await cache.generate(**args)
    assert counter.calls == 2


# --------------------------------------------------------------------------
# Budget
# --------------------------------------------------------------------------

async def test_daily_limit_refuses_before_billing(tmp_path: Path) -> None:
    counter = CountingBackend()
    budget = BudgetedImageBackend(
        counter, storage_root=tmp_path, daily_limit=3, total_limit=100
    )

    for _ in range(3):
        await budget.generate(prompt="x", width=1024, height=1024)

    with pytest.raises(BudgetExceeded, match="daily"):
        await budget.generate(prompt="x", width=1024, height=1024)

    # The refused call must not have reached the service.
    assert counter.calls == 3


async def test_total_limit_is_enforced_independently(tmp_path: Path) -> None:
    counter = CountingBackend()
    budget = BudgetedImageBackend(
        counter, storage_root=tmp_path, daily_limit=100, total_limit=2
    )

    await budget.generate(prompt="x", width=1024, height=1024)
    await budget.generate(prompt="x", width=1024, height=1024)
    with pytest.raises(BudgetExceeded, match="total"):
        await budget.generate(prompt="x", width=1024, height=1024)


async def test_spend_survives_a_restart(tmp_path: Path) -> None:
    # A process restart must not hand out a fresh allowance, or a crash loop
    # becomes an unlimited budget.
    first = BudgetedImageBackend(
        CountingBackend(), storage_root=tmp_path, daily_limit=2, total_limit=100
    )
    await first.generate(prompt="x", width=1024, height=1024)

    second = BudgetedImageBackend(
        CountingBackend(), storage_root=tmp_path, daily_limit=2, total_limit=100
    )
    assert second.usage.today == 1
    assert second.remaining_today == 1

    await second.generate(prompt="x", width=1024, height=1024)
    with pytest.raises(BudgetExceeded):
        await second.generate(prompt="x", width=1024, height=1024)


async def test_a_failed_call_is_not_charged(tmp_path: Path) -> None:
    """A 429 or a network error costs nothing, so the ledger must not count it."""

    class FailingBackend:
        async def generate(self, **kwargs):
            raise RuntimeError("network died")

        async def edit(self, **kwargs):
            raise RuntimeError("network died")

    budget = BudgetedImageBackend(
        FailingBackend(), storage_root=tmp_path, daily_limit=5, total_limit=5
    )
    with pytest.raises(RuntimeError):
        await budget.generate(prompt="x", width=1024, height=1024)

    assert budget.usage.total == 0, "a failed call was billed to the ledger"


async def test_daily_counter_rolls_over_but_total_does_not(tmp_path: Path) -> None:
    ledger = tmp_path / "usage.json"
    Usage(total=17, today=9, day="2020-01-01").save(ledger)

    usage = Usage.load(ledger)
    assert usage.today == 0, "yesterday's count should not limit today"
    assert usage.total == 17, "the running total must persist across days"


async def test_a_corrupt_ledger_does_not_crash_the_service(tmp_path: Path) -> None:
    (tmp_path / "usage.json").write_text("{ not json", encoding="utf-8")
    budget = BudgetedImageBackend(
        CountingBackend(), storage_root=tmp_path, daily_limit=5, total_limit=5
    )
    assert budget.usage.total == 0
    await budget.generate(prompt="x", width=1024, height=1024)


# --------------------------------------------------------------------------
# The two together, in the order the app composes them
# --------------------------------------------------------------------------

async def test_a_cache_hit_does_not_consume_budget(tmp_path: Path) -> None:
    """The ordering that makes the whole thing worthwhile.

    cache -> budget -> client, so a repeat request is free in every sense:
    no network call, and no credit deducted.
    """
    counter = CountingBackend()
    budget = BudgetedImageBackend(
        counter, storage_root=tmp_path, daily_limit=2, total_limit=100
    )
    cache = CachingImageBackend(budget, root=tmp_path / "cache")

    args = {"prompt": "a bottle", "width": 1024, "height": 1024}
    for _ in range(5):
        await cache.generate(**args)

    assert counter.calls == 1
    assert budget.usage.today == 1, "cache hits were charged against the budget"
    assert cache.stats.calls_saved == 4


# --------------------------------------------------------------------------
# Economy mode -- the largest saving available
# --------------------------------------------------------------------------

async def test_master_format_is_the_tallest_requested() -> None:
    """Cropping only goes taller -> wider, never the reverse.

    Pick the wrong master and the other formats cannot be derived at all
    without inventing pixels.
    """
    from app.pipeline import master_format_for

    assert master_format_for(("portrait", "grid", "square", "story")) == "story"
    assert master_format_for(("portrait", "square")) == "portrait"
    assert master_format_for(("square", "landscape")) == "square"


async def test_economy_mode_collapses_every_format_into_one_call(
    tmp_path: Path,
) -> None:
    from app.copy.strategy import BrandKit, CreativeBrief, CreativeStrategy
    from app.pipeline import CampaignPipeline

    counter = CountingBackend()
    pipeline = CampaignPipeline(counter, storage=tmp_path)
    formats = ("portrait", "grid", "square", "story")
    try:
        result = await pipeline.run(
            campaign_id="econ",
            brief=CreativeBrief(subject="a bottle"),
            strategy=CreativeStrategy(proposition="p", benefit="b"),
            brand=BrandKit(name="ACME"),
            formats=formats,
            locales=("en", "hi"),
            economy=True,
        )
    finally:
        await pipeline.aclose()

    assert counter.calls == 1, "economy mode still generated per format"
    assert result.image_calls == 1
    assert result.calls_saved == 3
    assert result.master_format == "story"
    # Same output either way -- 4 formats x 2 locales.
    assert len(result.variants) == 8
    for format_key in formats:
        assert format_key in result.base_paths


async def test_economy_bases_are_still_the_exact_delivery_size(
    tmp_path: Path,
) -> None:
    from PIL import Image as PILImage

    from app.copy.strategy import BrandKit, CreativeBrief, CreativeStrategy
    from app.imaging.dimensions import plan_for
    from app.pipeline import CampaignPipeline

    pipeline = CampaignPipeline(MockImageClient(), storage=tmp_path)
    try:
        result = await pipeline.run(
            campaign_id="econ2",
            brief=CreativeBrief(subject="a bottle"),
            strategy=CreativeStrategy(proposition="p", benefit="b"),
            brand=BrandKit(name="ACME"),
            formats=("portrait", "square", "story"),
            locales=("en",),
            economy=True,
        )
    finally:
        await pipeline.aclose()

    # Cropping down must not leave an off-size canvas.
    for format_key, path in result.base_paths.items():
        plan = plan_for(format_key)
        with PILImage.open(path) as image:
            assert image.size == (plan.target_w, plan.target_h), format_key


async def test_economy_prompt_asks_the_subject_to_survive_cropping(
    tmp_path: Path,
) -> None:
    from app.copy.strategy import BrandKit, CreativeBrief, CreativeStrategy
    from app.pipeline import CampaignPipeline

    pipeline = CampaignPipeline(MockImageClient(), storage=tmp_path)
    try:
        economy = await pipeline.run(
            campaign_id="e1",
            brief=CreativeBrief(subject="a bottle"),
            strategy=CreativeStrategy(proposition="p", benefit="b"),
            brand=BrandKit(name="ACME"),
            formats=("portrait", "square"),
            locales=("en",),
            economy=True,
        )
        normal = await pipeline.run(
            campaign_id="e2",
            brief=CreativeBrief(subject="a bottle"),
            strategy=CreativeStrategy(proposition="p", benefit="b"),
            brand=BrandKit(name="ACME"),
            formats=("portrait", "square"),
            locales=("en",),
            economy=False,
        )
    finally:
        await pipeline.aclose()

    # Without this clause the square crop decapitates the subject.
    assert "cropped to a shorter shape" in economy.prompt
    assert "cropped to a shorter shape" not in normal.prompt


async def test_economy_is_a_no_op_for_a_single_format(tmp_path: Path) -> None:
    from app.copy.strategy import BrandKit, CreativeBrief, CreativeStrategy
    from app.pipeline import CampaignPipeline

    counter = CountingBackend()
    pipeline = CampaignPipeline(counter, storage=tmp_path)
    try:
        result = await pipeline.run(
            campaign_id="econ3",
            brief=CreativeBrief(subject="a bottle"),
            strategy=CreativeStrategy(proposition="p", benefit="b"),
            brand=BrandKit(name="ACME"),
            formats=("portrait",),
            locales=("en",),
            economy=True,
        )
    finally:
        await pipeline.aclose()

    # Nothing to save, so no crop-safe compromise should be imposed.
    assert counter.calls == 1
    assert result.master_format is None
    assert "cropped to a shorter shape" not in result.prompt
