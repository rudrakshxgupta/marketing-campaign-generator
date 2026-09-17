"""Deterministic stand-in for MAI-Image-2.6.

There is no local emulator for MAI, and the real deployment allows 2-12
requests per minute. Without this the pipeline could not be built or tested
before Azure exists, and the test suite would inherit a rate limit.

The mock honours the parts of the contract the rest of the pipeline depends on:
it returns a PNG, at exactly the requested dimensions, and it refuses
dimensions MAI would reject. Output is keyed off the prompt, so the same brief
always renders the same placeholder and visual diffs stay meaningful.
"""

from __future__ import annotations

import asyncio
import colorsys
import hashlib
import io

from PIL import Image, ImageDraw

from app.foundry.image_client import ImageResult, MaiError
from app.imaging.dimensions import is_legal


def _palette(seed: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    hue = digest[0] / 255.0
    top = colorsys.hsv_to_rgb(hue, 0.35, 0.92)
    bottom = colorsys.hsv_to_rgb((hue + 0.08) % 1.0, 0.55, 0.42)
    to_rgb = lambda c: tuple(int(v * 255) for v in c)  # noqa: E731
    return to_rgb(top), to_rgb(bottom)


def _render(prompt: str, width: int, height: int, label: str) -> bytes:
    top, bottom = _palette(prompt)
    image = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(image)

    # Vertical gradient, so contrast-aware logo placement has something real to
    # measure rather than a flat fill.
    for y in range(height):
        t = y / max(1, height - 1)
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )

    # Centre marker, so crops and upscales are visually verifiable.
    cx, cy = width // 2, height // 2
    arm = min(width, height) // 12
    draw.line([(cx - arm, cy), (cx + arm, cy)], fill=(255, 255, 255), width=3)
    draw.line([(cx, cy - arm), (cx, cy + arm)], fill=(255, 255, 255), width=3)

    # Latin-only caption. Never render Indic text here -- Pillow has no RAQM on
    # this machine and would produce broken glyph order, which is exactly the
    # failure the real pipeline exists to avoid.
    lines = [
        "MOCK - not a real generation",
        f"{width}x{height}  ({width * height:,} px)",
        label,
        "",
        *_wrap(prompt, 52)[:6],
    ]
    y = 24
    for line in lines:
        draw.text((24, y), line, fill=(255, 255, 255))
        y += 16

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


class MockImageClient:
    """Implements :class:`~app.foundry.image_client.ImageBackend`."""

    def __init__(self, *, latency_s: float = 0.0) -> None:
        self._latency_s = latency_s

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
        # Enforce the same limits the service does, so a dimension bug fails in
        # tests instead of in production.
        if not is_legal(width, height):
            raise MaiError(
                f"{width}x{height} violates MAI limits "
                f"(sides >= 768, product <= 1048576)"
            )
        if self._latency_s:
            await asyncio.sleep(self._latency_s)

        deployment = "mock-flash" if draft else "mock-2.6"
        png = _render(prompt, width, height, deployment)
        return ImageResult(
            png=png,
            width=width,
            height=height,
            deployment=deployment,
            attempts=1,
            waited_s=0.0,
        )

    async def edit(
        self,
        *,
        prompt: str,
        image: bytes,
        mime: str = "image/png",
        draft: bool = False,
    ) -> ImageResult:
        if mime not in ("image/png", "image/jpeg"):
            raise MaiError(f"edits accepts PNG or JPEG, got {mime}")
        if self._latency_s:
            await asyncio.sleep(self._latency_s)

        # The real edits endpoint takes no width/height -- output geometry
        # follows the input. Mirror that so callers cannot accidentally depend
        # on requesting a size here.
        with Image.open(io.BytesIO(image)) as source:
            width, height = source.size

        deployment = "mock-flash" if draft else "mock-2.6"
        png = _render(f"[edit] {prompt}", width, height, deployment)
        return ImageResult(
            png=png,
            width=width,
            height=height,
            deployment=deployment,
            attempts=1,
            waited_s=0.0,
        )
