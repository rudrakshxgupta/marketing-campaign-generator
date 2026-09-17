"""Content-addressed cache for generated images.

MAI image generation is the dominant cost in the system and the quota is
small, so the same request must never be paid for twice.

Users re-click "Generate" constantly -- while waiting, after a page refresh,
after a restart. Without a cache each of those is a fresh charge for a
byte-identical result.

The key covers everything that changes the output: prompt, model, dimensions,
and the endpoint used. It deliberately does **not** include a timestamp or a
random seed, so a repeated request is a cache hit rather than a new variation.
Wanting a different result is an explicit action (``bypass=True``), not an
accident of impatience.

Deliberately *not* a semantic cache. Two briefs being 95% similar does not mean
the user wants the same picture -- that is a correctness bug dressed up as an
optimisation.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.foundry.image_client import ImageBackend, ImageResult

logger = logging.getLogger(__name__)


def _digest(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0

    @property
    def calls_saved(self) -> int:
        return self.hits

    def __str__(self) -> str:
        total = self.hits + self.misses
        rate = (self.hits / total * 100) if total else 0.0
        return f"{self.hits} hit / {self.misses} miss ({rate:.0f}% saved)"


class CachingImageBackend:
    """Wraps an :class:`ImageBackend`, serving repeats from disk.

    Implements the same protocol, so the pipeline neither knows nor cares.
    """

    def __init__(
        self,
        inner: ImageBackend,
        *,
        root: Path,
        enabled: bool = True,
    ) -> None:
        self._inner = inner
        self._root = root
        self._enabled = enabled
        self.stats = CacheStats()
        if enabled:
            self._root.mkdir(parents=True, exist_ok=True)

    # -- key construction -------------------------------------------------

    def _generate_key(
        self, prompt: str, width: int, height: int, draft: bool, **flags: bool
    ) -> str:
        return _digest(
            {
                "op": "generate",
                "prompt": prompt,
                "width": width,
                "height": height,
                "draft": draft,
                **{k: v for k, v in flags.items() if v},
            }
        )

    def _edit_key(self, prompt: str, image: bytes, draft: bool) -> str:
        return _digest(
            {
                "op": "edit",
                "prompt": prompt,
                # Hash the bytes rather than embedding them.
                "image": hashlib.sha256(image).hexdigest(),
                "draft": draft,
            }
        )

    def _path(self, key: str) -> Path:
        return self._root / f"{key}.png"

    # -- protocol ---------------------------------------------------------

    async def generate(
        self,
        *,
        prompt: str,
        width: int,
        height: int,
        draft: bool = False,
        auto_aspect_ratio: bool = False,
        web_grounding: bool = False,
        bypass_cache: bool = False,
    ) -> ImageResult:
        key = self._generate_key(
            prompt, width, height, draft,
            auto_aspect_ratio=auto_aspect_ratio,
            web_grounding=web_grounding,
        )
        cached = self._read(key, width, height)
        if cached is not None and not bypass_cache:
            return cached

        result = await self._inner.generate(
            prompt=prompt,
            width=width,
            height=height,
            draft=draft,
            auto_aspect_ratio=auto_aspect_ratio,
            web_grounding=web_grounding,
        )
        self._write(key, result.png)
        self.stats.misses += 1
        return result

    async def edit(
        self,
        *,
        prompt: str,
        image: bytes,
        mime: str = "image/png",
        draft: bool = False,
        bypass_cache: bool = False,
    ) -> ImageResult:
        key = self._edit_key(prompt, image, draft)
        cached = self._read(key, 0, 0)
        if cached is not None and not bypass_cache:
            return cached

        result = await self._inner.edit(
            prompt=prompt, image=image, mime=mime, draft=draft
        )
        self._write(key, result.png)
        self.stats.misses += 1
        return result

    # -- storage ----------------------------------------------------------

    def _read(self, key: str, width: int, height: int) -> ImageResult | None:
        if not self._enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            return None
        self.stats.hits += 1
        logger.info("image cache hit %s (%s)", key[:8], self.stats)
        return ImageResult(
            png=path.read_bytes(),
            width=width,
            height=height,
            deployment="cache",
            attempts=0,
            waited_s=0.0,
        )

    def _write(self, key: str, png: bytes) -> None:
        if not self._enabled:
            return
        # Write to a temp name and move, so a crash mid-write cannot leave a
        # truncated PNG that later reads as a valid cache hit.
        path = self._path(key)
        temp = path.with_suffix(".part")
        temp.write_bytes(png)
        temp.replace(path)

    async def aclose(self) -> None:
        if hasattr(self._inner, "aclose"):
            await self._inner.aclose()
