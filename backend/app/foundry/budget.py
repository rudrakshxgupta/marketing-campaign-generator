"""A hard ceiling on how many images can ever be generated.

Image generation is billed per call and the quota is small. The failure mode
this exists to prevent is not a user generating too much -- it is a retry loop,
a runaway test, or a bug in a queue quietly spending a month of credits in an
afternoon.

So the limit is enforced *before* the call, counted on disk so it survives a
restart, and refuses rather than warns. Every call is logged with what it was
for, which also gives an honest per-campaign cost figure without waiting for
the Azure bill.

Deliberately conservative defaults: it is easier to raise a limit you hit than
to un-spend a credit.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from app.foundry.image_client import ImageBackend, ImageResult

logger = logging.getLogger(__name__)

DEFAULT_DAILY_LIMIT = 25
DEFAULT_TOTAL_LIMIT = 200


class BudgetExceeded(RuntimeError):
    """The call was refused before it could be billed."""


@dataclass
class Usage:
    total: int = 0
    today: int = 0
    day: str = ""

    @classmethod
    def load(cls, path: Path) -> Usage:
        if not path.is_file():
            return cls(day=date.today().isoformat())
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("usage ledger unreadable, starting a new one")
            return cls(day=date.today().isoformat())

        usage = cls(
            total=int(raw.get("total", 0)),
            today=int(raw.get("today", 0)),
            day=raw.get("day", ""),
        )
        # Roll the daily counter over at midnight, keeping the running total.
        if usage.day != date.today().isoformat():
            usage.today = 0
            usage.day = date.today().isoformat()
        return usage

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"total": self.total, "today": self.today, "day": self.day}),
            encoding="utf-8",
        )


class BudgetedImageBackend:
    """Wraps an :class:`ImageBackend` with a hard, persistent spend cap."""

    def __init__(
        self,
        inner: ImageBackend,
        *,
        storage_root: Path,
        daily_limit: int | None = None,
        total_limit: int | None = None,
    ) -> None:
        self._inner = inner
        self._ledger = storage_root / "usage.json"
        self._log = storage_root / "usage.log"
        self.daily_limit = (
            daily_limit
            if daily_limit is not None
            else int(os.environ.get("MAI_DAILY_LIMIT", DEFAULT_DAILY_LIMIT))
        )
        self.total_limit = (
            total_limit
            if total_limit is not None
            else int(os.environ.get("MAI_TOTAL_LIMIT", DEFAULT_TOTAL_LIMIT))
        )
        self.usage = Usage.load(self._ledger)

    @property
    def remaining_today(self) -> int:
        return max(0, self.daily_limit - self.usage.today)

    @property
    def remaining_total(self) -> int:
        return max(0, self.total_limit - self.usage.total)

    def _check(self) -> None:
        # Re-read so a limit raised in another process is picked up, and so a
        # long-running server rolls over at midnight.
        self.usage = Usage.load(self._ledger)

        if self.usage.today >= self.daily_limit:
            raise BudgetExceeded(
                f"daily image limit reached ({self.usage.today}/{self.daily_limit}). "
                f"Raise MAI_DAILY_LIMIT, or wait until tomorrow. "
                f"{self.usage.total} generated in total."
            )
        if self.usage.total >= self.total_limit:
            raise BudgetExceeded(
                f"total image limit reached ({self.usage.total}/{self.total_limit}). "
                f"Raise MAI_TOTAL_LIMIT once you are sure the spend is intended."
            )

    def _record(self, what: str) -> None:
        self.usage.total += 1
        self.usage.today += 1
        self.usage.save(self._ledger)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._log.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}\t{what}\ttoday={self.usage.today}\ttotal={self.usage.total}\n")
        logger.info(
            "image call %d today / %d total (%d left today)",
            self.usage.today, self.usage.total, self.remaining_today,
        )

    async def generate(
        self,
        *,
        prompt: str,
        width: int,
        height: int,
        draft: bool = False,
        auto_aspect_ratio: bool = False,
        web_grounding: bool = False,
    ) -> ImageResult:
        self._check()
        result = await self._inner.generate(
            prompt=prompt,
            width=width,
            height=height,
            draft=draft,
            auto_aspect_ratio=auto_aspect_ratio,
            web_grounding=web_grounding,
        )
        # Counted only on success: a 429 or a network failure costs nothing, so
        # charging for it would make the ledger lie.
        self._record(f"generate {width}x{height} draft={draft}")
        return result

    async def edit(
        self,
        *,
        prompt: str,
        image: bytes,
        mime: str = "image/png",
        draft: bool = False,
        #: Further reference images. The first image is the subject; these
        #: are context. Ignored by backends that take a single input.
        extras: tuple[bytes, ...] = (),
    ) -> ImageResult:
        self._check()
        result = await self._inner.edit(
            prompt=prompt, image=image, mime=mime, draft=draft, extras=extras
        )
        self._record(f"edit draft={draft}")
        return result

    async def aclose(self) -> None:
        if hasattr(self._inner, "aclose"):
            await self._inner.aclose()
