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
from app.copy.fidelity import SubjectKind
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
from app.imaging.fidelity import FidelityReport, compare
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
    economy: bool = False
    #: Present when an edit ran with subject preservation on.
    fidelity: FidelityReport | None = None
    #: Which format was generated and cropped down from, in economy mode.
    master_format: str | None = None

    @property
    def review_queue(self) -> list[Variant]:
        return [v for v in self.variants if v.needs_review]

    @property
    def calls_saved(self) -> int:
        """Generations avoided versus one call per format."""
        formats = len(self.base_paths)
        return max(0, formats - self.image_calls)


def master_format_for(formats: tuple[str, ...]) -> str:
    """The format every other requested format can be cropped out of.

    Cropping only ever goes from taller to wider -- you can cut the top and
    bottom off a 9:16 frame to get 4:5, but you cannot invent pixels to go the
    other way. So the master is the *tallest* requested format, i.e. the one
    with the smallest width/height ratio.
    """
    return min(formats, key=lambda key: plan_for(key).target_aspect)


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
        self._last_fidelity: FidelityReport | None = None
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
        economy: bool = False,
    ) -> CampaignResult:
        out_dir = self._storage / "renders" / campaign_id
        out_dir.mkdir(parents=True, exist_ok=True)

        # In economy mode the master is cropped down to every other format, so
        # the subject has to be briefed to survive losing its top and bottom.
        crop_safe = economy and len(formats) > 1
        prompt = render_prompt(brief, crop_safe=crop_safe)
        result = CampaignResult(
            campaign_id=campaign_id, prompt=prompt, economy=economy
        )

        # 1. Copy first. It costs no image quota, so a failure here should not
        #    burn a generation, and having it ready lets the UI show progress
        #    while the image is still queued.
        result.copy_pack = await self._writer.write(strategy, brand, locales)

        # 2. Generate. Economy mode produces one master and crops every other
        #    format out of it, turning N generations into one.
        bases: dict[str, Image.Image] = {}

        if economy and len(formats) > 1:
            master_key = master_format_for(formats)
            result.master_format = master_key
            master_plan = plan_for(master_key)

            master_png = await self._generate_base(
                prompt, brief, master_plan, reference, reference_mime, edit_instruction
            )
            result.image_calls += 1
            master = finalize_base(master_png, master_plan)
            bases[master_key] = master

            for format_key in formats:
                if format_key == master_key:
                    continue
                plan = plan_for(format_key)
                # Cover-crop the master down. Only ever taller -> wider, which
                # discards the top and bottom rather than inventing pixels.
                bases[format_key] = _cover_crop(master, plan.target_w, plan.target_h)

            logger.info(
                "economy: generated %s once, cropped %d other format(s) from it",
                master_key, len(formats) - 1,
            )
        else:
            for format_key in formats:
                plan = plan_for(format_key)
                base_png = await self._generate_base(
                    prompt, brief, plan, reference, reference_mime, edit_instruction
                )
                result.image_calls += 1
                bases[format_key] = finalize_base(base_png, plan)

        # 3. Every locale reuses the base for its format.
        for format_key in formats:
            base = bases[format_key]
            base_path = out_dir / f"base_{format_key}.png"
            base_path.write_bytes(to_png(base))
            result.base_paths[format_key] = base_path

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

        result.fidelity = self._last_fidelity
        logger.info(
            "campaign %s: %d image call(s) -> %d deliverables (%d call(s) saved)",
            campaign_id, result.image_calls, len(result.variants), result.calls_saved,
        )
        return result

    async def _generate_base(
        self,
        prompt: str,
        brief: CreativeBrief,
        plan,
        reference: bytes | None,
        reference_mime: str,
        edit_instruction: str,
    ) -> bytes:
        if reference is not None:
            png, report = await self._generate_from_reference(
                reference,
                reference_mime,
                edit_instruction,
                plan,
                subject_kind=brief.subject_kind,
                preserve_subject=brief.preserve_subject,
            )
            self._last_fidelity = report
            return png
        generated = await self._images.generate(
            prompt=prompt, width=plan.gen_w, height=plan.gen_h
        )
        return generated.png

    async def _generate_from_reference(
        self,
        reference: bytes,
        mime: str,
        instruction: str,
        plan,
        *,
        subject_kind: SubjectKind = SubjectKind.GENERIC,
        preserve_subject: bool = True,
    ) -> tuple[bytes, FidelityReport | None]:
        """Image-to-image path.

        The edits endpoint accepts no width/height, so geometry is handled on
        both sides of the call: cover-crop the reference to the target aspect
        going in, and crop whatever comes back on the way out.

        When preservation is on, the result is measured against the input. The
        prompt asks the model to leave the subject alone; this is what checks
        whether it did. A building with an extra storey renders beautifully and
        misrepresents the property, so it cannot be left to the prompt alone.
        """
        prepared = _cover_crop(load_png(reference), plan.gen_w, plan.gen_h)
        edited = await self._images.edit(
            prompt=render_edit_prompt(
                instruction or "Restage this on a clean studio backdrop",
                preserve_subject=preserve_subject,
                subject_kind=subject_kind,
            ),
            image=to_png(prepared),
            mime="image/png",
        )
        with load_png(edited.png) as returned:
            adjusted = _cover_crop(returned.convert("RGB"), plan.gen_w, plan.gen_h)

        report = compare(prepared, adjusted, subject_kind) if preserve_subject else None
        if report is not None and not report.passed:
            logger.warning("subject fidelity failed -- %s", report.reason)
        return to_png(adjusted), report

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
