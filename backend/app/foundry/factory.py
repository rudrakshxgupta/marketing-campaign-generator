"""The one sanctioned way to build an image backend.

The cache and the spend ceiling are not decorations -- they are what stops a
retry loop, a runaway test or an impatient user from draining a quota. But they
only protect what goes through them, and a client constructed directly bypasses
both silently.

That is not hypothetical. A throwaway script built ``FluxImageClient(settings)``
by hand to re-render a campaign, and every call went straight to the service:
no cache hit on an identical prompt, and nothing written to the ledger. The
guards were present, configured, and completely absent from the call path.

So construction lives here, and callers ask for a backend rather than choosing
a client. Anything that wants the raw client has to reach past this module,
which is visible in review in a way ``FluxImageClient(settings)`` is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings
from app.foundry.budget import BudgetedImageBackend
from app.foundry.cache import CachingImageBackend
from app.foundry.flux_client import FluxImageClient
from app.foundry.image_client import MaiImageClient
from app.foundry.mock_client import MockImageClient

logger = logging.getLogger(__name__)


@dataclass
class Backend:
    """A guarded image backend, plus the guards themselves for reporting."""

    images: object
    budget: BudgetedImageBackend | None = None
    cache: CachingImageBackend | None = None

    async def aclose(self) -> None:
        if hasattr(self.images, "aclose"):
            await self.images.aclose()


def build_image_backend(settings: Settings) -> Backend:
    """Compose the stack.

    Ordering is the whole point:

        cache -> budget -> client

    The cache sits outermost so a repeat request is served from disk *without*
    consuming budget. The budget guard sits above the client so nothing can
    spend past the ceiling, whatever calls it.
    """
    if settings.mock:
        # Nothing to guard: placeholders are free and caching them saves
        # nothing worth the indirection.
        return Backend(images=MockImageClient())

    if settings.image_backend == "flux":
        base = FluxImageClient(settings)
        logger.info("images: FLUX (%s)", settings.flux_model)
    else:
        base = MaiImageClient(settings)
        logger.info("images: MAI (%s)", settings.image_deployment)

    budget = BudgetedImageBackend(
        base,
        storage_root=settings.storage_root,
        daily_limit=settings.daily_limit,
        total_limit=settings.total_limit,
    )
    cache = CachingImageBackend(
        budget, root=settings.cache_root, enabled=settings.cache_enabled
    )
    logger.info(
        "spend guard: %d/%d today, %d/%d total, cache %s",
        budget.usage.today, budget.daily_limit,
        budget.usage.total, budget.total_limit,
        "on" if settings.cache_enabled else "off",
    )
    return Backend(images=cache, budget=budget, cache=cache)
