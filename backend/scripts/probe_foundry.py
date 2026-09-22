"""Answer the open questions in issue #29 against a live MAI deployment.

Six things cannot be determined from the documentation. This script answers
them in one run and prints a report you can paste into the issue.

    ../.venv/Scripts/python scripts/probe_foundry.py

Quota-aware by design. The default run costs **3 image calls**; at tier 1
(2 RPM) that is roughly 90 seconds. The expensive probes are opt-in:

    --rpm-test     +4 calls, tests whether deployments have separate buckets
    --skip-edit    -1 call

Requires MAI_MOCK=0 and FOUNDRY_ENDPOINT set. Authentication is whatever
`DefaultAzureCredential` finds - normally your `az login`.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from app.config import Settings  # noqa: E402
from app.foundry.image_client import MaiError, MaiImageClient  # noqa: E402
from app.imaging.dimensions import plan_for  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "storage" / "probe"

PROMPT = (
    "Commercial product photography. A clear glass bottle of cold-pressed "
    "coconut oil with a matte gold cap on dark walnut, warm bokeh behind. "
    "Leave the lower-left 32% of the frame as clean, uncluttered smooth soft "
    "gradient with no objects, no detail and no texture. "
    "There is no text, no lettering, no words, no numbers, no letterforms, "
    "no signage, no watermark, no logo and no typography anywhere in this image."
)


class _MinimalStop(Exception):
    """Raised to jump to the report after --minimal's single image."""


@dataclass
class Findings:
    lines: list[str] = field(default_factory=list)

    def record(self, question: str, answer: str, detail: str = "") -> None:
        self.lines.append(f"**{question}**\n  {answer}" + (f"\n  {detail}" if detail else ""))

    def report(self) -> str:
        return "\n\n".join(self.lines)


def looks_like_c2pa(png: bytes) -> tuple[bool, str]:
    """Scan PNG chunks for a Content Credentials manifest.

    C2PA rides in a `caBX` chunk in PNG. JUMBF/`c2pa` markers are a weaker
    signal but worth reporting.
    """
    markers = {
        b"caBX": "caBX chunk (the C2PA PNG container)",
        b"c2pa": "c2pa marker",
        b"jumb": "JUMBF box",
    }
    hits = [label for marker, label in markers.items() if marker in png]
    return bool(hits), ", ".join(hits) if hits else "none of caBX / c2pa / jumb present"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimal", action="store_true",
                        help="ONE image only. Still answers connectivity, dimensions, "
                             "latency and C2PA -- everything that changes the design.")
    parser.add_argument("--rpm-test", action="store_true",
                        help="test whether deployments have separate RPM buckets (+4 calls)")
    parser.add_argument("--skip-edit", action="store_true")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)

    if os.environ.get("MAI_MOCK", "1").lower() in {"1", "true", "yes", "on"}:
        print("MAI_MOCK is on. Set MAI_MOCK=0 to probe the real service.")
        return 1

    try:
        settings = Settings()
    except RuntimeError as exc:
        print(f"{exc}\n")
        print("Set it in .env, or run scripts/setup_foundry.ps1 -WriteEnv to")
        print("create the resource and write the settings for you.")
        return 1

    print(f"endpoint   {settings.foundry_endpoint}")
    print(f"deployment {settings.image_deployment}")
    print(f"draft      {settings.draft_deployment}")
    print(f"auth       {'API key' if settings.foundry_api_key else 'Entra ID (az login)'}")
    print()

    client = MaiImageClient(settings)
    findings = Findings()
    calls = 0

    try:
      try:
        # -- 1. connectivity, auth, and geometry --------------------------
        print("[1/6] one real generation at portrait size ...")
        plan = plan_for("portrait")
        started = time.monotonic()
        try:
            result = await client.generate(
                prompt=PROMPT, width=plan.gen_w, height=plan.gen_h
            )
        except MaiError as exc:
            print(f"      FAILED: {exc}")
            findings.record(
                "Connectivity", f"FAILED ({exc.status}): {exc}",
                "Check FOUNDRY_ENDPOINT, the deployment name, and that az login has "
                "Cognitive Services User on the resource.",
            )
            print("\n" + findings.report())
            return 1

        elapsed = time.monotonic() - started
        calls += 1
        with Image.open(io.BytesIO(result.png)) as image:
            size = image.size
            mode = image.mode
        (OUT / "probe_portrait.png").write_bytes(result.png)

        ok = size == (plan.gen_w, plan.gen_h)
        print(f"      {elapsed:.1f}s, {size[0]}x{size[1]}, {mode}, {len(result.png)//1024} KB")
        findings.record(
            "Does a real generation work, and are dimensions honoured?",
            f"YES - requested {plan.gen_w}x{plan.gen_h}, got {size[0]}x{size[1]} "
            f"({'match' if ok else 'MISMATCH'})",
            f"{len(result.png)//1024} KB PNG, mode {mode}. Saved to storage/probe/.",
        )

        # -- 2. latency, 2.6 vs Flash -------------------------------------
        if args.minimal:
            print("[2/6] skipped (--minimal): no Flash comparison")
            flash_elapsed = None
            findings.record(
                "What is real generation latency?",
                f"MAI-Image-2.6: **{elapsed:.1f}s** at {plan.gen_w}x{plan.gen_h}",
                "Flash not compared (--minimal). At 2 RPM the limiter allows one "
                f"call every 30s, so latency "
                f"{'is not' if elapsed < 30 else 'IS'} the binding constraint.",
            )
            raise _MinimalStop
        print("[2/6] latency of MAI-Image-2.6-Flash for comparison ...")
        flash_note = ""
        try:
            started = time.monotonic()
            flash = await client.generate(
                prompt=PROMPT, width=plan.gen_w, height=plan.gen_h, draft=True
            )
            flash_elapsed = time.monotonic() - started
            calls += 1
            (OUT / "probe_flash.png").write_bytes(flash.png)
            speedup = elapsed / flash_elapsed if flash_elapsed else 0
            print(f"      {flash_elapsed:.1f}s  ({speedup:.1f}x faster)")
            flash_note = f"Flash {flash_elapsed:.1f}s, {speedup:.1f}x faster than 2.6."
        except MaiError as exc:
            flash_elapsed = None
            print(f"      unavailable: {exc}")
            flash_note = f"Flash deployment unavailable ({exc.status}). Drafts fall back to 2.6."

        findings.record(
            "What is real generation latency? (the interactive UX budget)",
            f"MAI-Image-2.6: **{elapsed:.1f}s** at {plan.gen_w}x{plan.gen_h}",
            flash_note
            + f"  At 2 RPM the limiter allows one call every 30s, so latency "
            f"{'is not' if elapsed < 30 else 'IS'} the binding constraint at tier 1.",
        )

        # -- 3. C2PA ------------------------------------------------------
        print("[3/6] checking output for Content Credentials ...")
        has_c2pa, detail = looks_like_c2pa(result.png)
        print(f"      {'found' if has_c2pa else 'not found'}: {detail}")
        findings.record(
            "Does MAI output carry C2PA Content Credentials?",
            "YES" if has_c2pa else "**NO**",
            detail
            + (
                ""
                if has_c2pa
                else "  We must sign our own manifest at the compositing step (#15). "
                "Compositing modifies pixels anyway, which invalidates any inherited manifest."
            ),
        )

        # -- 4. dimension enforcement -------------------------------------
        print("[4/6] confirming the service rejects an illegal size ...")
        # Costs no successful generation - we expect a 400 before any work.
        try:
            await client.generate(prompt="test", width=700, height=1000)
            print("      UNEXPECTED: 700x1000 was accepted")
            findings.record(
                "Are the documented dimension limits enforced?",
                "**NO** - 700x1000 was accepted despite the 768 floor",
                "Our client pre-checks locally, so this does not affect us, but the "
                "documented limit may be wrong.",
            )
        except MaiError as exc:
            # Our own client blocks this before the network, which is the point.
            print(f"      rejected locally: {exc}")
            findings.record(
                "Are the documented dimension limits enforced?",
                "Our client rejects it locally before spending a request",
                "A 768-floor violation never reaches the service, so it cannot cost quota.",
            )

        # -- 5. edits geometry --------------------------------------------
        if args.skip_edit or args.minimal:
            print("[5/6] skipped (costs one image)")
        else:
            print("[5/6] edits endpoint: does output geometry follow the input? ...")
            try:
                edited = await client.edit(
                    prompt=(
                        "Replace the background with a seamless neutral grey studio "
                        "backdrop, soft light from the upper left. Keep the product "
                        "completely unchanged. Do not add any text, logo or watermark."
                    ),
                    image=result.png,
                    mime="image/png",
                )
                calls += 1
                with Image.open(io.BytesIO(edited.png)) as image:
                    edit_size = image.size
                (OUT / "probe_edit.png").write_bytes(edited.png)
                follows = edit_size == size
                print(f"      in {size[0]}x{size[1]} -> out {edit_size[0]}x{edit_size[1]}")
                findings.record(
                    "The edits endpoint takes no width/height - what sets output size?",
                    f"Input {size[0]}x{size[1]} produced {edit_size[0]}x{edit_size[1]} - "
                    f"{'output FOLLOWS the input' if follows else '**output does NOT follow the input**'}",
                    "Our mock assumes it follows. "
                    + ("Assumption confirmed." if follows else "**Mock and pipeline need updating.**"),
                )
            except MaiError as exc:
                print(f"      failed: {exc}")
                findings.record(
                    "The edits endpoint takes no width/height - what sets output size?",
                    f"Call failed ({exc.status}): {exc}",
                )

        # -- 6. RPM buckets -----------------------------------------------
        if not args.rpm_test:
            print("[6/6] skipped (pass --rpm-test to check RPM bucket independence, +4 calls)")
            findings.record(
                "Do separate deployments get independent RPM buckets?",
                "NOT TESTED - re-run with --rpm-test",
                "Issue #23 (routing drafts to Flash) depends entirely on this answer.",
            )
        else:
            print("[6/6] firing at both deployments to see if buckets are independent ...")
            # Bypass our own limiter: we want the *service's* answer, not ours.
            client._bucket.capacity = 100
            client._bucket._tokens = 100

            async def hammer(draft: bool) -> tuple[int, int]:
                ok_count = limited = 0
                for _ in range(2):
                    try:
                        await client.generate(
                            prompt=PROMPT, width=768, height=768, draft=draft
                        )
                        ok_count += 1
                    except MaiError as exc:
                        if exc.status == 429:
                            limited += 1
                        else:
                            raise
                return ok_count, limited

            main_ok, main_429 = await hammer(False)
            flash_ok, flash_429 = await hammer(True)
            calls += 4
            print(f"      2.6: {main_ok} ok / {main_429} rate-limited")
            print(f"      Flash: {flash_ok} ok / {flash_429} rate-limited")
            independent = flash_ok > 0 and main_429 > 0
            findings.record(
                "Do separate deployments get independent RPM buckets?",
                f"{'YES - likely independent' if independent else 'INCONCLUSIVE'}",
                f"2.6: {main_ok} ok/{main_429} limited. Flash: {flash_ok} ok/{flash_429} limited. "
                f"Flash succeeding while 2.6 is limited indicates separate buckets.",
            )

      except _MinimalStop:
        # --minimal: the C2PA check below still runs on the image we have.
        has_c2pa, detail = looks_like_c2pa(result.png)
        print(f"[3/6] Content Credentials: {'found' if has_c2pa else 'not found'}")
        findings.record(
            "Does MAI output carry C2PA Content Credentials?",
            "YES" if has_c2pa else "**NO**",
            detail + ("" if has_c2pa else "  We must sign our own manifest at "
                      "the compositing step (#15)."),
        )
    finally:
        await client.aclose()

    print("\n" + "=" * 70)
    print(f"{calls} image call(s) used")
    print("=" * 70 + "\n")
    print(findings.report())
    print(
        "\n\nStill to check by hand:\n"
        "  - Open storage/probe/probe_portrait.png and confirm it contains NO text.\n"
        "    That is the assumption the whole overlay architecture rests on (#13).\n"
        "  - Whether a custom content-filter config attaches to a Microsoft-format\n"
        "    deployment: check the deployment blade in the Foundry portal (#14).\n"
        "  - Azure Translator transliterate coverage for ta/te:\n"
        "    GET /languages?api-version=3.0&scope=transliteration\n"
    )

    report_path = OUT / "findings.md"
    report_path.write_text(findings.report(), encoding="utf-8")
    print(f"Report written to {report_path}")
    print("Paste it into issue #29.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
