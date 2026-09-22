"""Measure whether an edit left the subject alone.

The preservation clause in :mod:`app.copy.fidelity` asks the model not to
change the subject. This checks whether it listened.

That check has to exist because the failure is invisible to the person running
the tool. A building with one extra floor, a bottle with a redrawn label, a
necklace with an added stone -- each renders beautifully, and each
misrepresents something the customer is actually selling.

**Why this cannot be whole-frame similarity.** Restaging is supposed to change
the background. A plain-sky-to-studio-backdrop swap moves most of the pixels in
the frame while leaving the subject untouched, and an added storey moves very
few while destroying the listing's accuracy. Compared across the whole frame,
the harmless edit scores *worse* than the dangerous one.

So the subject is located first -- it is the structured part of the image; sky,
seamless backdrops and plain walls are flat -- and three things are measured
about it:

1. **Similarity inside the subject** -- catches redrawing, relabelling,
   material and colour changes.
2. **Change in the subject's extent** -- catches a building gaining a storey,
   which grows upward into what used to be sky.
3. **Change in detail density inside that extent** -- catches windows,
   balconies or stones added without the outline moving.

Pure Pillow, no numpy, on a downsampled frame. Milliseconds, and no dependency
added for one call.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageFilter

from app.copy.fidelity import FIDELITY_FLOOR, SubjectKind

#: Work at this size: large enough that an added window survives downsampling,
#: small enough that pure-Python SSIM is instant.
_ANALYSIS = 256
_WINDOW = 8
_GRID = _ANALYSIS // _WINDOW

# Standard SSIM stabilisers for 8-bit data.
_C1 = (0.01 * 255) ** 2
_C2 = (0.03 * 255) ** 2

#: A window counts as "subject" when its edge energy clears a threshold taken
#: from the ORIGINAL image and then applied, unchanged, to both frames.
#:
#: Deriving it separately per image would be a bug: recolouring the sky moves
#: that frame's mean, which moves its threshold, which silently reclassifies
#: the building -- and the subject would appear to have moved when nothing
#: about it changed.
#:
#: Set at mean + k*sigma rather than a percentile. In a typical photograph the
#: subject is a *minority* of the frame -- sky, backdrop and ground are flat --
#: so any percentile below about 0.9 lands inside the flat majority and the
#: mask balloons to include sensor noise. Standard deviations track the real
#: structure regardless of how much of the frame it occupies.
#:
#: High enough that soft additions (bokeh, gradients, grain) stay below it,
#: while hard structure -- window frames, rooflines, railings, labels -- stays
#: above.
_STRUCTURE_SIGMAS = 2.0

#: Absolute floor on a 0-255 edge scale, so a near-empty frame yields no mask
#: rather than a mask made of noise.
_STRUCTURE_MIN_ENERGY = 8.0

#: The subject's outline may shift this much before it counts as a change.
#: Architecture is the reason this is tight -- a storey is a small fraction of
#: the frame and a large fraction of the truth.
_MIN_EXTENT_IOU = 0.90

#: Detail density inside the subject may move this much. Above it, something
#: was added or removed.
_MAX_DENSITY_DELTA = 0.15


@dataclass(frozen=True)
class FidelityReport:
    kind: SubjectKind
    similarity: float
    extent_iou: float
    density_delta: float
    floor: float
    passed: bool
    reason: str = ""

    def describe(self) -> str:
        return (
            f"{self.kind.value}: subject similarity {self.similarity:.3f} "
            f"(floor {self.floor:.2f}), extent {self.extent_iou:.2f}, "
            f"detail {self.density_delta:+.1%}"
        )


def _grey(image: Image.Image) -> Image.Image:
    return image.convert("L").resize((_ANALYSIS, _ANALYSIS), Image.LANCZOS)


def _window_edge_energy(grey: Image.Image) -> list[float]:
    """Mean edge magnitude per 8x8 window, row-major."""
    raw = grey.filter(ImageFilter.FIND_EDGES).tobytes()
    energies: list[float] = []
    for top in range(0, _ANALYSIS, _WINDOW):
        for left in range(0, _ANALYSIS, _WINDOW):
            total = 0
            for row in range(top, top + _WINDOW):
                base = row * _ANALYSIS
                total += sum(raw[base + left : base + left + _WINDOW])
            energies.append(total / (_WINDOW * _WINDOW))
    return energies


def _interior(count: int) -> list[int]:
    """Window indices away from the frame edge.

    FIND_EDGES leaves a bright rim at the border, which would otherwise read as
    structure all the way round.
    """
    return [
        i for i in range(count)
        if 0 < i // _GRID < _GRID - 1 and 0 < i % _GRID < _GRID - 1
    ]


def structure_threshold(energies: list[float]) -> float:
    """The energy above which a window counts as subject, from one image."""
    interior = _interior(len(energies))
    if not interior:
        return float("inf")
    values = [energies[i] for i in interior]
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    sigma = variance ** 0.5
    return max(mean + _STRUCTURE_SIGMAS * sigma, _STRUCTURE_MIN_ENERGY)


def structure_mask(grey: Image.Image, threshold: float | None = None) -> tuple[set[int], list[float], float]:
    """Window indices carrying real structure -- i.e. the subject.

    Pass ``threshold`` to score a second image against the first one's
    definition of structure.
    """
    energies = _window_edge_energy(grey)
    if threshold is None:
        threshold = structure_threshold(energies)
    mask = {i for i in _interior(len(energies)) if energies[i] > threshold}
    return mask, energies, threshold


def _extent(mask: set[int]) -> tuple[int, int, int, int] | None:
    """Bounding box of the mask in window coordinates."""
    if not mask:
        return None
    rows = [i // _GRID for i in mask]
    cols = [i % _GRID for i in mask]
    return min(cols), min(rows), max(cols) + 1, max(rows) + 1


def _iou(a: tuple[int, int, int, int] | None, b: tuple[int, int, int, int] | None) -> float:
    if a is None or b is None:
        return 1.0 if a == b else 0.0
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (area_a + area_b - intersection)


def _ssim_over(a: bytes, b: bytes, windows: set[int]) -> float:
    """Mean SSIM restricted to the given windows."""
    if not windows:
        return 1.0
    scores: list[float] = []
    for index in windows:
        top = (index // _GRID) * _WINDOW
        left = (index % _GRID) * _WINDOW
        sum_a = sum_b = sum_aa = sum_bb = sum_ab = 0
        for row in range(top, top + _WINDOW):
            base = row * _ANALYSIS
            for col in range(left, left + _WINDOW):
                pa, pb = a[base + col], b[base + col]
                sum_a += pa
                sum_b += pb
                sum_aa += pa * pa
                sum_bb += pb * pb
                sum_ab += pa * pb

        n = _WINDOW * _WINDOW
        mean_a, mean_b = sum_a / n, sum_b / n
        var_a = sum_aa / n - mean_a * mean_a
        var_b = sum_bb / n - mean_b * mean_b
        cov = sum_ab / n - mean_a * mean_b

        numerator = (2 * mean_a * mean_b + _C1) * (2 * cov + _C2)
        denominator = (mean_a**2 + mean_b**2 + _C1) * (var_a + var_b + _C2)
        scores.append(numerator / denominator if denominator else 1.0)
    return sum(scores) / len(scores)


def _density(energies: list[float], box: tuple[int, int, int, int] | None) -> float:
    """Mean edge energy inside a window-coordinate box."""
    if box is None:
        return 0.0
    x0, y0, x1, y1 = box
    values = [
        energies[row * _GRID + col]
        for row in range(y0, y1)
        for col in range(x0, x1)
    ]
    return sum(values) / len(values) if values else 0.0


def compare(
    before: Image.Image,
    after: Image.Image,
    kind: SubjectKind = SubjectKind.GENERIC,
    *,
    floor: float | None = None,
) -> FidelityReport:
    """Did the edit preserve the subject?

    ``floor`` overrides the per-kind similarity threshold. Architecture is
    strictest, because misstating a property is a legal exposure rather than a
    design flaw.
    """
    threshold = floor if floor is not None else FIDELITY_FLOOR[kind]

    grey_before, grey_after = _grey(before), _grey(after)

    # The original defines what counts as structure; the result is measured
    # against that same bar rather than against its own.
    mask_before, energies_before, threshold_used = structure_mask(grey_before)
    mask_after, energies_after, _ = structure_mask(grey_after, threshold_used)

    extent_before = _extent(mask_before)
    extent_after = _extent(mask_after)

    # 1. Did the subject itself survive? Measured only where the original had
    #    structure, so recolouring the sky does not count against the edit.
    similarity = _ssim_over(grey_before.tobytes(), grey_after.tobytes(), mask_before)

    # 2. Did the subject's outline move? A building that gains a storey grows
    #    upward into what used to be sky.
    extent_iou = _iou(extent_before, extent_after)

    # 3. Did detail appear inside that outline? Extra windows or stones without
    #    the outline moving.
    density_before = _density(energies_before, extent_before)
    density_after = _density(energies_after, extent_before)
    density_delta = (
        (density_after - density_before) / density_before if density_before else 0.0
    )

    reasons: list[str] = []
    if similarity < threshold:
        reasons.append(
            f"the subject itself changed (similarity {similarity:.3f}, "
            f"needs {threshold:.2f})"
        )
    if extent_iou < _MIN_EXTENT_IOU:
        reasons.append(
            f"the subject's outline moved or resized (overlap {extent_iou:.2f})"
        )
    if abs(density_delta) > _MAX_DENSITY_DELTA:
        direction = "added to" if density_delta > 0 else "removed from"
        reasons.append(
            f"detail was {direction} the subject ({density_delta:+.1%})"
        )

    return FidelityReport(
        kind=kind,
        similarity=similarity,
        extent_iou=extent_iou,
        density_delta=density_delta,
        floor=threshold,
        passed=not reasons,
        reason="; ".join(reasons),
    )
