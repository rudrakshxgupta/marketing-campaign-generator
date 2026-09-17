"""Seed GitHub labels, milestones and issues from .github/backlog.json.

Idempotent enough to re-run: labels use --force, milestones are skipped if the
title already exists, and issues are matched by title so a second run does not
create duplicates.

    python .github/seed_issues.py [--dry-run] [--gh "C:/Program Files/GitHub CLI/gh.exe"]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKLOG = ROOT / "backlog.json"


def run(gh: str, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [gh, *args], capture_output=True, text=True, encoding="utf-8"
    )
    if check and result.returncode != 0:
        sys.stderr.write(f"! gh {' '.join(args)}\n  {result.stderr.strip()}\n")
    return result


def existing_issue_titles(gh: str) -> set[str]:
    result = run(
        gh,
        ["issue", "list", "--state", "all", "--limit", "500", "--json", "title"],
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return set()
    return {row["title"] for row in json.loads(result.stdout)}


def existing_milestones(gh: str) -> set[str]:
    result = run(
        gh,
        ["api", "repos/{owner}/{repo}/milestones?state=all", "--jq", ".[].title"],
        check=False,
    )
    if result.returncode != 0:
        return set()
    return {line for line in result.stdout.splitlines() if line.strip()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--gh", default=shutil.which("gh") or "gh")
    args = parser.parse_args()

    gh = args.gh
    if not args.dry_run and not (shutil.which(gh) or Path(gh).exists()):
        sys.stderr.write(f"gh not found at {gh!r}\n")
        return 1

    data = json.loads(BACKLOG.read_text(encoding="utf-8"))

    # --- labels ---------------------------------------------------------
    print(f"labels ({len(data['labels'])})")
    for label in data["labels"]:
        if args.dry_run:
            print(f"  would create {label['name']}")
            continue
        run(
            gh,
            [
                "label", "create", label["name"],
                "--color", label["color"],
                "--description", label["description"],
                "--force",
            ],
            check=False,
        )
        print(f"  {label['name']}")

    # --- milestones -----------------------------------------------------
    print(f"\nmilestones ({len(data['milestones'])})")
    present = set() if args.dry_run else existing_milestones(gh)
    for milestone in data["milestones"]:
        if milestone["title"] in present:
            print(f"  = {milestone['title']} (exists)")
            continue
        if args.dry_run:
            print(f"  would create {milestone['title']}")
            continue
        run(
            gh,
            [
                "api", "repos/{owner}/{repo}/milestones",
                "-f", f"title={milestone['title']}",
                "-f", f"description={milestone['description']}",
            ],
            check=False,
        )
        print(f"  {milestone['title']}")

    # --- issues ---------------------------------------------------------
    issues = data["issues"]
    done = sum(1 for i in issues if i["state"] == "closed")
    print(f"\nissues ({len(issues)}: {done} built, {len(issues) - done} pending)")

    already = set() if args.dry_run else existing_issue_titles(gh)
    created = 0
    for issue in issues:
        if issue["title"] in already:
            print(f"  = {issue['title'][:62]} (exists)")
            continue
        if args.dry_run:
            state = "closed" if issue["state"] == "closed" else "open"
            print(f"  would create [{state}] {issue['title'][:58]}")
            continue

        # Bodies are long markdown; a temp file avoids every shell quoting
        # problem, and keeps non-ASCII intact on Windows.
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(issue["body"])
            body_path = handle.name

        command = [
            "issue", "create",
            "--title", issue["title"],
            "--body-file", body_path,
        ]
        for label in issue["labels"]:
            command += ["--label", label]
        if issue.get("milestone"):
            command += ["--milestone", issue["milestone"]]

        result = run(gh, command, check=False)
        Path(body_path).unlink(missing_ok=True)

        if result.returncode != 0:
            print(f"  ! failed: {issue['title'][:58]}")
            continue

        url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        created += 1

        # Features that are already built are filed as closed, so the repo
        # documents what exists as well as what is outstanding.
        if issue["state"] == "closed" and url:
            number = url.rstrip("/").split("/")[-1]
            run(
                gh,
                ["issue", "close", number, "--reason", "completed",
                 "--comment", "Built and tested in the Phase 1 commit."],
                check=False,
            )
            print(f"  [done]    #{number} {issue['title'][:55]}")
        elif url:
            number = url.rstrip("/").split("/")[-1]
            print(f"  [pending] #{number} {issue['title'][:55]}")

    print(f"\ncreated {created} issue(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
