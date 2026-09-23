"""Render campaign text to a transparent PNG using headless Chromium.

Why a browser, and not a text library:

Correct Devanagari, Bengali, Tamil and Telugu need HarfBuzz shaping (matra
reordering, conjunct formation, above/below-base marks), ICU line breaking, and
per-language `locl` lookups. Pillow only does any of that when it was compiled
against libraqm, and on this machine ``PIL.features.check("raqm")`` is False --
``ImageDraw.text`` would silently emit unreordered, disconnected glyphs. The
failure is dangerous precisely because it looks plausible to a reader who does
not know the script.

Chromium already carries HarfBuzz and ICU, is continuously tested against these
scripts by a browser vendor, and gives us CSS line breaking and an in-page
auto-fit loop for free. The same template drives the frontend preview, so there
is only one renderer to keep correct.
"""

from __future__ import annotations

import asyncio
import html
import json
from dataclasses import dataclass
from pathlib import Path

from app.config import PROJECT_ROOT
from app.copy.languages import Locale, get_locale

FONTS_DIR = PROJECT_ROOT / "fonts"

TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  @font-face-placeholder {}
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body {
    width: %(width)dpx; height: %(height)dpx;
    background: transparent;
  }
  #stage {
    position: relative;
    width: %(width)dpx; height: %(height)dpx;
  }
  #block {
    position: absolute;
    left: %(left)dpx; top: %(top)dpx;
    width: %(box_w)dpx; height: %(box_h)dpx;
    display: flex; flex-direction: column;
    justify-content: %(justify)s;
    gap: %(gap)dpx;
  }
  .line {
    font-family: %(font_stack)s;
    font-weight: %(weight)d;
    color: %(colour)s;
    line-height: %(line_height).3f;
    /* Locked off: positive tracking separates shaped clusters and visibly
       breaks conjuncts in every Indic script. */
    letter-spacing: 0;
    /* Never synthesise weight for Indic -- faux-bold smears conjuncts. */
    font-synthesis: none;
    text-wrap: balance;
    /* Words must NOT break. Indic scripts produce long agglutinative words,
       and breaking one mid-cluster splits a consonant from its matra. Letting
       a long word overflow is what makes the auto-fit search able to see that
       it does not fit, so it shrinks the type instead of mangling the word. */
    overflow-wrap: normal;
    word-break: normal;
    hyphens: none;
    text-shadow: %(shadow)s;
  }
  #headline { font-weight: %(weight)d; }
  #subhead  { font-weight: 400; opacity: 0.92; }
  #cta {
    align-self: flex-start;
    font-weight: 600;
    padding: 0.45em 0.95em;
    border-radius: 999px;
    background: %(cta_bg)s;
    color: %(cta_fg)s;
    /* Small caps suit a short label and ruin a sentence, so this is only
       ever applied to the call to action, and only for Latin. */
    font-variant-caps: %(cta_caps)s;
    text-shadow: none;
  }
</style></head>
<body><div id="stage"><div id="block">
  <div class="line" id="headline" lang="%(lang)s" dir="%(dir)s"></div>
  <div class="line" id="subhead"  lang="%(lang)s" dir="%(dir)s"></div>
  <div class="line" id="cta"      lang="%(lang)s" dir="%(dir)s"></div>
</div></div></body></html>
"""


@dataclass(frozen=True)
class TextBox:
    """Where the copy sits, as fractions of the canvas."""

    left: float = 0.06
    top: float = 0.55
    width: float = 0.60
    height: float = 0.30
    justify: str = "flex-end"


@dataclass(frozen=True)
class OverlayStyle:
    colour: str = "#FFFFFF"
    #: Latin display family for this campaign, chosen from what is being
    #: sold. Empty keeps the locale's own stack.
    #:
    #: Applied to Latin scripts ONLY. Substituting a display family for
    #: Devanagari or Tamil is how shaping breaks: the fallback either lacks
    #: the conjunct glyphs or has untested metrics, and the result renders
    #: convincingly while reading as illiterate to the audience.
    family: tuple[str, ...] = ()
    weight: int = 700
    cta_small_caps: bool = False
    cta_background: str = "#D4A03C"
    cta_foreground: str = "#1A0E08"
    #: A soft shadow keeps text legible over photographic mid-tones without
    #: needing a scrim over the whole block.
    shadow: str = "0 2px 12px rgba(0,0,0,0.45)"
    headline_max_lines: int = 3
    subhead_max_lines: int = 2


@dataclass(frozen=True)
class FitReport:
    headline_px: int
    subhead_px: int
    cta_px: int
    headline_used: str
    #: Type had to go below the script's legible minimum. Never ship silently.
    below_minimum: bool
    #: A word had to be broken mid-cluster. Always a defect for Indic.
    did_break: bool
    headline_lines: int
    #: Painted bounds of the copy, relative to the canvas.
    bounds: tuple[int, int, int, int]

    @property
    def needs_review(self) -> bool:
        return self.below_minimum or self.did_break


class OverlayRenderer:
    """Renders text overlays. Reuses one browser across calls.

    Chromium launch is the expensive part (hundreds of ms); rendering a single
    overlay once the browser is warm is tens of ms, which is what makes "one
    base image, many languages" cheap.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self):
        if self._browser is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch()
        return self._browser

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def measure(
        self, text: str, *, locale: Locale | str, size_px: int = 96
    ) -> float:
        """Advance width of ``text`` in pixels, as the browser would paint it.

        Used to prove the shaping engine is actually running: a script that is
        being shaped produces a different width for a ligated conjunct than for
        the same characters forced apart with a zero-width non-joiner. Without
        GSUB both render as the same glyph run and the widths match.
        """
        if isinstance(locale, str):
            locale = get_locale(locale)
        stack = ", ".join(f'"{f}"' for f in locale.metrics.font_stack)

        browser = await self._ensure_browser()
        async with self._lock:
            page = await browser.new_page(viewport={"width": 800, "height": 400})
            try:
                await page.set_content(
                    "<!doctype html><meta charset='utf-8'>"
                    f"<span id='m' lang='{html.escape(locale.bcp47)}' "
                    f"style='font-family:{stack};font-size:{size_px}px;"
                    "white-space:pre;letter-spacing:0'></span>"
                )
                await _load_local_fonts(page)
                return await page.evaluate(
                    "async (t) => { const el = document.getElementById('m');"
                    " el.textContent = t; await document.fonts.ready;"
                    " return el.getBoundingClientRect().width; }",
                    text,
                )
            finally:
                await page.close()

    async def wordmark(self, text: str, *, height_px: int = 120) -> bytes:
        """Set a brand name as a mark, on transparent ground.

        For the common case where a small seller has a name but no logo file.
        Leaving the corner empty makes the creative look unfinished, and
        asking a diffusion model to letter the name produces a *plausible*
        wordmark, which is to say the wrong one -- so it is typeset here, the
        same way the copy is, and composited from real glyphs.

        Deliberately not the body face. A wordmark that matches the headline
        reads as a stray line of copy rather than as a mark, so this is set in
        a high-contrast serif with tight tracking and small caps -- the
        conventions that make a name read as an identity.

        The PNG is cropped to the glyphs and returned with alpha, so it drops
        straight into :func:`composite_logo` and inherits everything that
        already works there: safe-zone placement, the light/dark knockout
        chosen from the pixels behind it, and a scrim when the background
        would swallow it.
        """
        browser = await self._ensure_browser()
        # Generous canvas: the text is measured and cropped afterwards, so
        # this only has to be big enough not to clip a long name.
        canvas_w = max(400, len(text) * height_px)

        async with self._lock:
            page = await browser.new_page(
                viewport={"width": canvas_w, "height": height_px * 3}
            )
            try:
                await page.set_content(
                    "<!doctype html><meta charset='utf-8'>"
                    "<style>html,body{margin:0;background:transparent}"
                    "#w{display:inline-block;color:#fff;"
                    "font-family:Georgia,'Iowan Old Style','Times New Roman',serif;"
                    f"font-size:{height_px}px;font-weight:600;"
                    "letter-spacing:0.02em;line-height:1.25;white-space:pre;"
                    "font-variant-caps:all-small-caps;"
                    # A wordmark is never synthesised bold or italic: faux
                    # styling smears the very shapes that make it recognisable.
                    "font-synthesis:none}</style>"
                    f"<span id='w'>{html.escape(text)}</span>"
                )
                await page.evaluate("async () => { await document.fonts.ready; }")
                element = await page.query_selector("#w")
                # Screenshot the element rather than the page, so the result
                # is already tight to the glyphs.
                return await element.screenshot(omit_background=True, type="png")
            finally:
                await page.close()

    async def render(
        self,
        *,
        width: int,
        height: int,
        locale: Locale | str,
        headline: str,
        subhead: str = "",
        cta: str = "",
        headline_alternates: tuple[str, ...] = (),
        box: TextBox | None = None,
        style: OverlayStyle | None = None,
    ) -> tuple[bytes, FitReport]:
        """Return (transparent PNG at canvas size, fit report)."""
        if isinstance(locale, str):
            locale = get_locale(locale)
        box = box or TextBox()
        style = style or OverlayStyle()
        metrics = locale.metrics

        page_html = TEMPLATE % {
            "width": width,
            "height": height,
            "left": round(width * box.left),
            "top": round(height * box.top),
            "box_w": round(width * box.width),
            "box_h": round(height * box.height),
            "justify": box.justify,
            "gap": max(8, round(height * 0.012)),
            # Latin only. Indic locales keep their Noto faces whatever the
            # campaign's chosen display family is.
            "font_stack": ", ".join(
                f'"{f}"'
                for f in (
                    style.family
                    if style.family and metrics.code == "Latn"
                    else metrics.font_stack
                )
            ),
            "weight": style.weight,
            "cta_caps": "all-small-caps" if style.cta_small_caps else "normal",
            "colour": style.colour,
            "line_height": metrics.line_height,
            "shadow": style.shadow,
            "cta_bg": style.cta_background,
            "cta_fg": style.cta_foreground,
            "lang": html.escape(locale.bcp47),
            "dir": "rtl" if metrics.rtl else "ltr",
        }

        browser = await self._ensure_browser()
        async with self._lock:
            page = await browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
            )
            try:
                await page.set_content(page_html, wait_until="load")
                await _load_local_fonts(page)
                report = await page.evaluate(
                    _FIT_SCRIPT,
                    {
                        "headline": headline,
                        "alternates": list(headline_alternates),
                        "subhead": subhead,
                        "cta": cta,
                        "minSize": metrics.min_size_px,
                        "maxSize": round(height * 0.085),
                        "headlineMaxLines": style.headline_max_lines,
                        "subheadMaxLines": style.subhead_max_lines,
                        "canvasWidth": width,
                    },
                )
                png = await page.screenshot(omit_background=True, type="png")
            finally:
                await page.close()

        return png, FitReport(
            headline_px=round(report["headlinePx"]),
            subhead_px=round(report["subheadPx"]),
            cta_px=round(report["ctaPx"]),
            headline_used=report["headlineUsed"],
            below_minimum=report["belowMinimum"],
            did_break=report["didBreak"],
            headline_lines=report["lines"],
            bounds=tuple(report["bounds"]),  # type: ignore[arg-type]
        )


async def _load_local_fonts(page) -> None:
    """Register bundled Noto faces, if present.

    Windows ships Nirmala UI, which covers every v1 script and is fine for
    development. It is not redistributable, so production bundles Noto and
    pins the versions -- a silent font upgrade changes line metrics and
    reflows every historical creative.
    """
    if not FONTS_DIR.is_dir():
        return
    faces = []
    for path in sorted(FONTS_DIR.glob("*.ttf")) + sorted(FONTS_DIR.glob("*.otf")):
        family = path.stem.replace("-Regular", "").replace("-Bold", "")
        weight = "700" if "Bold" in path.stem else "400"
        faces.append(
            {
                "family": family.replace("_", " "),
                "weight": weight,
                "url": path.resolve().as_uri(),
            }
        )
    if faces:
        await page.evaluate(_FONT_SCRIPT, faces)


_FONT_SCRIPT = """
async (faces) => {
  for (const f of faces) {
    try {
      const ff = new FontFace(f.family, `url(${f.url})`, { weight: f.weight });
      await ff.load();
      document.fonts.add(ff);
    } catch (e) { /* fall back to the system stack */ }
  }
  await document.fonts.ready;
}
"""

# Binary-searches the largest size that fits the box within the line budget.
# Running it in-page means the browser's own shaping and line breaking decide
# what "fits" -- which is the only measurement that matches what gets painted.
_FIT_SCRIPT = """
async (cfg) => {
  const headline = document.getElementById('headline');
  const subhead  = document.getElementById('subhead');
  const cta      = document.getElementById('cta');
  const block    = document.getElementById('block');

  subhead.textContent = cfg.subhead || '';
  cta.textContent     = cfg.cta || '';
  subhead.style.display = cfg.subhead ? '' : 'none';
  cta.style.display     = cfg.cta ? '' : 'none';

  const lineCount = (el) => {
    const lh = parseFloat(getComputedStyle(el).lineHeight);
    if (!isFinite(lh) || lh <= 0) return 1;
    return Math.max(1, Math.round(el.getBoundingClientRect().height / lh));
  };

  // Measure the real stacked height of the visible slots. `block.scrollHeight`
  // cannot be used: glyphs overshoot their line box by a pixel or two, so it
  // always exceeds clientHeight and every candidate size looks like a failure.
  const contentHeight = () => {
    const items = [headline, subhead, cta].filter(
      el => el.style.display !== 'none' && el.textContent
    );
    if (!items.length) return 0;
    const gap = parseFloat(getComputedStyle(block).rowGap) || 0;
    const sum = items.reduce((t, el) => t + el.getBoundingClientRect().height, 0);
    return sum + gap * (items.length - 1);
  };

  // Subhead and CTA scale with the headline, so the slots have to be sized
  // together -- fitting the headline alone then adding a subhead underneath is
  // how a layout ends up overflowing.
  const subheadFor = (h) =>
    Math.max(Math.round(cfg.minSize * 0.8), Math.round(h * 0.55));
  const ctaFor = (h) =>
    Math.max(Math.round(cfg.minSize * 0.85), Math.round(h * 0.42));

  const applySizes = (h) => {
    headline.style.fontSize = h + 'px';
    if (cfg.subhead) subhead.style.fontSize = subheadFor(h) + 'px';
    if (cfg.cta) cta.style.fontSize = ctaFor(h) + 'px';
  };

  const overflowsWidth = (el) => el.scrollWidth > el.clientWidth + 1;

  const fits = (h) => {
    applySizes(h);
    if (lineCount(headline) > cfg.headlineMaxLines) return false;
    if (overflowsWidth(headline)) return false;
    if (cfg.subhead) {
      if (lineCount(subhead) > cfg.subheadMaxLines) return false;
      if (overflowsWidth(subhead)) return false;
    }
    if (cfg.cta && lineCount(cta) > 2) return false;
    return contentHeight() <= block.clientHeight + 1;
  };

  const search = () => {
    let lo = cfg.minSize, hi = cfg.maxSize, best = 0;
    while (lo <= hi) {
      const mid = Math.floor((lo + hi) / 2);
      if (fits(mid)) { best = mid; lo = mid + 1; }
      else { hi = mid - 1; }
    }
    return best;
  };

  // Try the preferred headline first, then progressively shorter alternates.
  // Selecting a shorter line beats shrinking type below legibility.
  const candidates = [cfg.headline, ...(cfg.alternates || [])];
  let headlinePx = 0, headlineUsed = cfg.headline;
  for (const candidate of candidates) {
    headline.textContent = candidate;
    const size = search();
    if (size >= cfg.minSize) { headlinePx = size; headlineUsed = candidate; break; }
    if (size > headlinePx) { headlinePx = size; headlineUsed = candidate; }
  }
  headline.textContent = headlineUsed;
  applySizes(Math.max(headlinePx, cfg.minSize));

  // Last resort only: if even the minimum legible size cannot fit an unbroken
  // word, allow breaking so the export is at least complete, and flag it for
  // review rather than shipping silently.
  let didBreak = false;
  if (overflowsWidth(headline)) {
    headline.style.overflowWrap = 'break-word';
    didBreak = true;
  }

  const finalH = parseFloat(headline.style.fontSize);
  const subheadPx = cfg.subhead ? subheadFor(finalH) : 0;
  const ctaPx = cfg.cta ? ctaFor(finalH) : 0;

  await document.fonts.ready;

  // Report the painted bounds so the caller can assert the copy actually
  // stayed inside the box it was given.
  const stage = document.getElementById('stage').getBoundingClientRect();
  const parts = [headline, subhead, cta].filter(el => el.textContent);
  let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
  for (const el of parts) {
    const r = el.getBoundingClientRect();
    left = Math.min(left, r.left - stage.left);
    top = Math.min(top, r.top - stage.top);
    right = Math.max(right, r.right - stage.left);
    bottom = Math.max(bottom, r.bottom - stage.top);
  }

  return {
    headlinePx: parseFloat(headline.style.fontSize),
    subheadPx, ctaPx, headlineUsed,
    belowMinimum: headlinePx < cfg.minSize,
    didBreak,
    lines: lineCount(headline),
    bounds: [Math.round(left), Math.round(top), Math.round(right), Math.round(bottom)]
  };
}
"""
