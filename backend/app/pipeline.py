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
from app.foundry.image_client import ImageBackend, PromptBlocked
from app.imaging.compose import (
    Anchor,
    patch_luminance,
    composite_logo,
    composite_overlay,
    finalize_base,
    load_png,
    to_png,
)
from app.imaging.dimensions import MAI, Capabilities, plan_for
from app.imaging.fidelity import FidelityReport, compare
from app.imaging.overlay import FitReport, OverlayRenderer, OverlayStyle, TextBox
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
    #: Things the run did that the operator should know about but that are not
    #: failures -- a prompt retried in a reduced form, most of all. Silently
    #: recovering is worse than not recovering: the creative that comes back is
    #: not the one that was briefed.
    notes: list[str] = field(default_factory=list)

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

#: Above this mean luminance the backdrop is light, so the copy must be dark.
_LIGHT_BACKDROP = 0.45
#: Below this it is dark, so the copy must be light.
_DARK_BACKDROP = 0.18


def style_for(base: Image.Image, box: TextBox, brand: BrandKit) -> OverlayStyle:
    """Choose copy colour by measuring what the copy will sit on.

    The logo has always picked its knockout from the background; the *text* did
    not, and was simply always white. On a pale creative -- a cream backdrop,
    an overexposed bokeh, a white studio sweep -- white copy on white is
    unreadable, and it is unreadable in exactly the way that still renders and
    still passes every size check.

    So the text box is sampled first and the palette chosen from it, with a
    scrim in the mid-tones where neither pure choice is safe.
    """
    region = (
        round(base.width * box.left),
        round(base.height * box.top),
        round(base.width * (box.left + box.width)),
        round(base.height * (box.top + box.height)),
    )
    luminance = patch_luminance(base, region)

    if luminance > _LIGHT_BACKDROP:
        # Dark ink on a light backdrop. The shadow has to become a soft light
        # halo instead, or it reads as grime around the letters.
        return OverlayStyle(
            colour="#17130A",
            cta_background=brand.primary_hex,
            cta_foreground="#17130A",
            shadow="0 1px 10px rgba(255,255,255,0.65)",
        )

    if luminance < _DARK_BACKDROP:
        return OverlayStyle(
            colour="#FFFFFF",
            cta_background=brand.primary_hex,
            cta_foreground="#17130A",
            shadow="0 2px 12px rgba(0,0,0,0.45)",
        )

    # Mid-tone: neither pure white nor near-black is reliably legible, so keep
    # white and lean on a heavier shadow to carve it out of the background.
    return OverlayStyle(
        colour="#FFFFFF",
        cta_background=brand.primary_hex,
        cta_foreground="#17130A",
        shadow="0 2px 6px rgba(0,0,0,0.85), 0 0 22px rgba(0,0,0,0.6)",
    )


class CampaignPipeline:
    def __init__(
        self,
        images: ImageBackend,
        *,
        renderer: OverlayRenderer | None = None,
        writer: CopyWriter | None = None,
        storage: Path | None = None,
        capabilities: Capabilities = MAI,
    ) -> None:
        self._images = images
        self._renderer = renderer or OverlayRenderer()
        self._writer = writer or StubCopyWriter()
        self._last_fidelity: FidelityReport | None = None
        self._notes: list[str] = []
        #: The prompt most recently built, kept outside the result so a run
        #: that fails still has something to show. A refused prompt is exactly
        #: the one worth reading, and it is the one the old code threw away.
        self.last_prompt: str = ""
        # What the chosen image model will accept. MAI's 1 MP forces a
        # crop-and-upscale on every Instagram format; FLUX's 4 MP does not.
        self._caps = capabilities
        self._storage = storage or Path("storage")
        self._owns_renderer = renderer is None

    async def aclose(self) -> None:
        if self._owns_renderer:
            await self._renderer.aclose()

    def note(self, message: str) -> None:
        """Record something the operator should see in the job log."""
        self._notes.append(message)

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
        # Per run, not per instance: the pipeline outlives a single campaign.
        self._notes = []

        # In economy mode the master is cropped down to every other format, so
        # the subject has to be briefed to survive losing its top and bottom.
        crop_safe = economy and len(formats) > 1
        prompt = render_prompt(brief, crop_safe=crop_safe)
        self.last_prompt = prompt
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
            master_plan = plan_for(master_key, self._caps)

            master_png = await self._generate_base(
                prompt, brief, master_plan, reference, reference_mime,
                edit_instruction, crop_safe=crop_safe,
            )
            result.image_calls += 1
            master = finalize_base(master_png, master_plan)
            bases[master_key] = master

            for format_key in formats:
                if format_key == master_key:
                    continue
                plan = plan_for(format_key, self._caps)
                # Cover-crop the master down. Only ever taller -> wider, which
                # discards the top and bottom rather than inventing pixels.
                bases[format_key] = _cover_crop(master, plan.target_w, plan.target_h)

            logger.info(
                "economy: generated %s once, cropped %d other format(s) from it",
                master_key, len(formats) - 1,
            )
        else:
            for format_key in formats:
                plan = plan_for(format_key, self._caps)
                base_png = await self._generate_base(
                    prompt, brief, plan, reference, reference_mime,
                    edit_instruction, crop_safe=crop_safe,
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
                    brand=brand,
                )
                result.variants.append(variant)

        result.fidelity = self._last_fidelity
        result.notes = list(self._notes)
        logger.info(
            "campaign %s: %d image call(s) -> %d deliverables (%d call(s) saved)",
            campaign_id, result.image_calls, len(result.variants), result.calls_saved,
        )
        return result

    async def recompose(
        self,
        result: CampaignResult,
        *,
        locale_key: str,
        brand: BrandKit,
        logo: Image.Image | None = None,
    ) -> list[Variant]:
        """Re-render one locale from the base image already on disk.

        Costs **no image quota**. The base is text-free by design, so changing
        copy is a typesetting job rather than a regeneration -- which is what
        makes human review practical at all. If every correction cost a
        rate-limited call, nobody would correct anything.
        """
        if result.copy_pack is None:
            raise ValueError("campaign has no copy to recompose")

        out_dir = self._storage / "renders" / result.campaign_id
        replaced: list[Variant] = []

        for format_key, base_path in result.base_paths.items():
            with Image.open(base_path) as stored:
                base = stored.convert("RGB")
            variant = await self._compose_variant(
                base=base,
                logo=logo,
                copy_pack=result.copy_pack,
                locale_key=locale_key,
                format_key=format_key,
                out_dir=out_dir,
                brand=brand,
            )
            replaced.append(variant)

        # Swap the new variants in for the old ones.
        result.variants = [
            v for v in result.variants if v.locale != locale_key
        ] + replaced
        logger.info(
            "recomposed %s for %s (%d format(s), no image call)",
            locale_key, result.campaign_id, len(replaced),
        )
        return replaced

    async def _generate_base(
        self,
        prompt: str,
        brief: CreativeBrief,
        plan,
        reference: bytes | None,
        reference_mime: str,
        edit_instruction: str,
        *,
        crop_safe: bool = False,
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
        try:
            generated = await self._images.generate(
                prompt=prompt, width=plan.gen_w, height=plan.gen_h
            )
        except PromptBlocked:
            # A blocklist refusal is not billed, so one retry is free -- and
            # the clause that most often causes it is the one the brief wrote
            # to *prevent* a problem. See render_prompt's docstring.
            if not brief.must_not_depict:
                raise
            retry = render_prompt(
                brief, crop_safe=crop_safe, omit_authored_prohibitions=True
            )
            if retry == prompt:
                raise
            logger.warning(
                "prompt refused; retrying without %d authored prohibition(s)",
                len(brief.must_not_depict),
            )
            self.last_prompt = retry
            self.note(
                "the service refused the first prompt; retried without the "
                "brief's own 'do not show' list (no image was billed)"
            )
            generated = await self._images.generate(
                prompt=retry, width=plan.gen_w, height=plan.gen_h
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
        brand: BrandKit,
    ) -> Variant:
        copy = copy_pack[locale_key]
        locale = get_locale(locale_key)
        reasons: list[str] = []

        box = TEXT_BOXES.get(format_key, TEXT_BOXES["portrait"])
        overlay_png, fit = await self._renderer.render(
            width=base.width,
            height=base.height,
            locale=locale,
            headline=copy.headline,
            subhead=copy.subhead,
            cta=copy.cta,
            headline_alternates=copy.headline_alternates,
            box=box,
            # Measured against this image, not assumed. White-on-white renders
            # perfectly and reads as nothing at all.
            style=style_for(base, box, brand),
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
