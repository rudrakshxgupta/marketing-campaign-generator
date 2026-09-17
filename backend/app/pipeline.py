"""Campaign generation, end to end.

The shape of this module is the whole architectural argument:

    ONE image generation per format  ->  N language variants, composited.

Step 3 is the only call that touches MAI's 2-12 RPM budget. Everything after it
is CPU work that scales freely, which is what makes the seventh language cost
nothing and the seventh *format* cost a generation.
"""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.copy.languages import DEFAULT_LOCALES, get_locale
from app.copy.strategy import (
    BrandKit,
    CreativeBrief,
    CreativeStrategy,
    render_edit_prompt,
    render_prompt,
)
from app.copy.transcreate import CopyPack, CopyWriter, StubCopyWriter
from app.foundry.image_client import ImageBackend
from app.imaging.compose import (
    Anchor,
    composite_logo,
    composite_overlay,
    finalize_base,
    load_png,
    to_png,
)
from app.imaging.dimensions import plan_for
from app.imaging.overlay import FitReport, OverlayRenderer, TextBox
from app.imaging.safezones import SafeZoneViolation

logger = logging.getLogger(__name__)


@dataclass
class Variant:
    locale: str
    format_key: str
    path: Path
    fit: FitReport
    logo_variant: str
    logo_contrast: float
    needs_review: bool
    review_reasons: tuple[str, ...] = ()


@dataclass
class CampaignResult:
    campaign_id: str
    prompt: str
    base_paths: dict[str, Path] = field(default_factory=dict)
    variants: list[Variant] = field(default_factory=list)
    copy_pack: CopyPack | None = None
    image_calls: int = 0

    @property
    def review_queue(self) -> list[Variant]:
        return [v for v in self.variants if v.needs_review]


#: Where the copy block sits, per format. Story keeps well clear of the Reels
#: caption tray, which eats the bottom 35% of the canvas.
TEXT_BOXES: dict[str, TextBox] = {
    "portrait": TextBox(left=0.07, top=0.52, width=0.62, height=0.34),
    "grid": TextBox(left=0.07, top=0.50, width=0.62, height=0.32),
    "square": TextBox(left=0.07, top=0.46, width=0.64, height=0.38),
    "story": TextBox(left=0.08, top=0.30, width=0.70, height=0.30),
    "landscape": TextBox(left=0.06, top=0.30, width=0.52, height=0.50),
}

#: Story UI occupies the top, so the mark goes high-left where the safe rect
#: still has room; feed formats read better with it bottom-right.
LOGO_ANCHORS: dict[str, Anchor] = {
    "story": "top_left",
}


class CampaignPipeline:
    def __init__(
        self,
        images: ImageBackend,
        *,
        renderer: OverlayRenderer | None = None,
        writer: CopyWriter | None = None,
        storage: Path | None = None,
    ) -> None:
        self._images = images
        self._renderer = renderer or OverlayRenderer()
        self._writer = writer or StubCopyWriter()
        self._storage = storage or Path("storage")
        self._owns_renderer = renderer is None

    async def aclose(self) -> None:
        if self._owns_renderer:
            await self._renderer.aclose()

    async def run(
        self,
        *,
        campaign_id: str,
        brief: CreativeBrief,
        strategy: CreativeStrategy,
        brand: BrandKit,
        logo: Image.Image | None = None,
        formats: tuple[str, ...] = ("portrait",),
        locales: tuple[str, ...] = DEFAULT_LOCALES,
        reference: bytes | None = None,
        reference_mime: str = "image/png",
        edit_instruction: str = "",
    ) -> CampaignResult:
        out_dir = self._storage / "renders" / campaign_id
        out_dir.mkdir(parents=True, exist_ok=True)

        prompt = render_prompt(brief)
        result = CampaignResult(campaign_id=campaign_id, prompt=prompt)

        # 1. Copy first. It costs no image quota, so a failure here should not
        #    burn a generation, and having it ready lets the UI show progress
        #    while the image is still queued.
        result.copy_pack = await self._writer.write(strategy, brand, locales)

        for format_key in formats:
            plan = plan_for(format_key)

            # 2. The single rate-limited call.
            if reference is not None:
                base_png = await self._generate_from_reference(
                    reference, reference_mime, edit_instruction, plan
                )
            else:
                generated = await self._images.generate(
                    prompt=prompt, width=plan.gen_w, height=plan.gen_h
                )
                base_png = generated.png
            result.image_calls += 1

            base = finalize_base(base_png, plan)
            base_path = out_dir / f"base_{format_key}.png"
            base_path.write_bytes(to_png(base))
            result.base_paths[format_key] = base_path

            # 3. Every locale reuses that one base.
            for locale_key in locales:
                variant = await self._compose_variant(
                    base=base,
                    logo=logo,
                    copy_pack=result.copy_pack,
                    locale_key=locale_key,
                    format_key=format_key,
                    out_dir=out_dir,
                )
                result.variants.append(variant)

        logger.info(
            "campaign %s: %d image call(s) -> %d deliverables",
            campaign_id, result.image_calls, len(result.variants),
        )
        return result

    async def _generate_from_reference(
        self, reference: bytes, mime: str, instruction: str, plan
    ) -> bytes:
        """Image-to-image path.

        The edits endpoint accepts no width/height, so geometry is handled on
        both sides of the call: cover-crop the reference to the target aspect
        going in, and crop whatever comes back on the way out.
        """
        prepared = _cover_crop(load_png(reference), plan.gen_w, plan.gen_h)
        edited = await self._images.edit(
            prompt=render_edit_prompt(instruction or "Restage this on a clean studio backdrop"),
            image=to_png(prepared),
            mime="image/png",
        )
        with load_png(edited.png) as returned:
            adjusted = _cover_crop(returned.convert("RGB"), plan.gen_w, plan.gen_h)
        return to_png(adjusted)

    async def _compose_variant(
        self,
        *,
        base: Image.Image,
        logo: Image.Image | None,
        copy_pack: CopyPack,
        locale_key: str,
        format_key: str,
        out_dir: Path,
    ) -> Variant:
        copy = copy_pack[locale_key]
        locale = get_locale(locale_key)
        reasons: list[str] = []

        overlay_png, fit = await self._renderer.render(
            width=base.width,
            height=base.height,
            locale=locale,
            headline=copy.headline,
            subhead=copy.subhead,
            cta=copy.cta,
            headline_alternates=copy.headline_alternates,
            box=TEXT_BOXES.get(format_key, TEXT_BOXES["portrait"]),
        )
        composed = composite_overlay(base, overlay_png)

        logo_variant, logo_contrast = "none", 0.0
        if logo is not None:
            anchor = LOGO_ANCHORS.get(format_key, "bottom_right")
            try:
                composed, placement = composite_logo(
                    composed.convert("RGB"), logo, format_key=format_key, anchor=anchor
                )
                logo_variant = placement.variant
                logo_contrast = placement.contrast
            except SafeZoneViolation as exc:
                # A hard gate, not a warning: a mark under the platform's own
                # UI is invisible, and shipping it is worse than shipping none.
                reasons.append(f"logo placement rejected: {exc}")

        if fit.did_break:
            reasons.append("a word was broken mid-cluster")
        if fit.below_minimum:
            reasons.append("type fell below the legible minimum for this script")
        if copy.needs_review:
            reasons.append("machine-written copy has not been reviewed")

        path = out_dir / f"{format_key}_{locale_key}.png"
        path.write_bytes(to_png(composed.convert("RGB")))

        return Variant(
            locale=locale_key,
            format_key=format_key,
            path=path,
            fit=fit,
            logo_variant=logo_variant,
            logo_contrast=logo_contrast,
            needs_review=bool(reasons),
            review_reasons=tuple(reasons),
        )


def _cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    """Centre cover-crop to an exact size, preserving aspect."""
    image = image.convert("RGB")
    target = width / height
    source = image.width / image.height
    if source > target:
        new_w = round(image.height * target)
        offset = (image.width - new_w) // 2
        image = image.crop((offset, 0, offset + new_w, image.height))
    elif source < target:
        new_h = round(image.width / target)
        offset = (image.height - new_h) // 2
        image = image.crop((0, offset, image.width, offset + new_h))
    if image.size != (width, height):
        image = image.resize((width, height), Image.LANCZOS)
    return image


def export_bundle(result: CampaignResult, destination: Path) -> Path:
    """Zip the deliverables plus the copy, ready to post.

    The unit a marketer needs is a campaign bundle, not an image: the creative,
    the caption, the hashtags, the CTA and the alt text, per language.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for variant in result.variants:
            archive.write(variant.path, f"images/{variant.path.name}")

        if result.copy_pack is not None:
            for locale_key, copy in result.copy_pack.by_locale.items():
                lines = [
                    f"# {get_locale(locale_key).label}",
                    "",
                    f"Headline: {copy.headline}",
                    f"Subhead:  {copy.subhead}",
                    f"CTA:      {copy.cta}",
                    "",
                    "Caption:",
                    copy.caption,
                    "",
                    "Hashtags: " + " ".join(copy.hashtags),
                    f"Alt text: {copy.alt_text}",
                    "",
                    f"English back-translation: {copy.back_translation}",
                ]
                archive.writestr(f"copy/{locale_key}.txt", "\n".join(lines))

        if result.review_queue:
            review = ["Items needing review before publishing:", ""]
            for variant in result.review_queue:
                review.append(f"- {variant.format_key}/{variant.locale}")
                review.extend(f"    * {reason}" for reason in variant.review_reasons)
            archive.writestr("REVIEW.txt", "\n".join(review))

    return destination


def bundle_bytes(result: CampaignResult) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for variant in result.variants:
            archive.write(variant.path, f"images/{variant.path.name}")
    return buffer.getvalue()
