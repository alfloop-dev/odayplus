#!/usr/bin/env python3
"""Auto-merge green task PRs into dev.

SAFETY (all must hold, else skip):
  - head branch matches an allowed task prefix (task/ODP-...)
  - base branch is `dev`
  - mergeable (no conflicts)
  - check_merge_eligibility passes:
    - task status is review_approved
    - assigned reviewer approval is present on GitHub (or fallback to canonical task status)
    - all required CI checks in policy.json are SUCCESS (completed/success)
    - fails closed on any errors or unresolved metadata.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add repo root to path to allow importing scripts
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".orchestrator"))

from scripts.check_pr_merge_eligibility import check_merge_eligibility, get_gh_executable

REPO = "alfloop-dev/odayplus"
BASE = "dev"
ALLOWED_HEAD = re.compile(r"^task/ODP-[A-Z0-9-]+", re.IGNORECASE)


def _gh(*args: str, timeout: int = 60) -> tuple[int, str, str]:
    gh_bin = get_gh_executable()
    p = subprocess.run([gh_bin, *args], capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_task_prs() -> list[dict]:
    rc, out, err = _gh(
        "pr", "list", "--repo", REPO, "--state", "open", "--limit", "50",
        "--json", "number,headRefName,baseRefName,isDraft,mergeable",
    )
    if rc != 0:
        print(f"[{_now()}] ERROR listing PRs: {err}", flush=True)
        return []
    try:
        prs = json.loads(out or "[]")
    except ValueError:
        return []
    return [
        p for p in prs
        if ALLOWED_HEAD.match(str(p.get("headRefName", "")))
        and str(p.get("baseRefName")) == BASE
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=5, help="max merges per run")
    args = ap.parse_args()

    prs = open_task_prs()
    if not prs:
        print(f"[{_now()}] no eligible open task PRs", flush=True)
        return 0

    status_path = ROOT / "ai-status.json"
    config_path = ROOT / ".orchestrator/config.json"
    policy_path = ROOT / ".github/branch-protection/policy.json"

    merged = 0
    for p in sorted(prs, key=lambda x: x["number"]):
        if merged >= args.max:
            break
        n = p["number"]
        head = p["headRefName"]
        if p.get("mergeable") == "CONFLICTING":
            print(f"[{_now()}] skip #{n} {head}: CONFLICTING (needs rebase)", flush=True)
            continue

        # Run the fail-closed merge eligibility check
        try:
            eligible, errors = check_merge_eligibility(
                pr_number=n,
                branch_name=head,
                repo_slug=REPO,
                status_path=status_path,
                config_path=config_path,
                policy_path=policy_path,
            )
        except Exception as exc:
            eligible = False
            errors = [f"Exception during merge eligibility check: {exc}"]

        if not eligible:
            print(f"[{_now()}] skip #{n} {head}: NOT ELIGIBLE. Errors:", flush=True)
            for err in errors:
                print(f"  - {err}", flush=True)
            continue

        if args.dry_run:
            draft = " (draft->ready)" if p.get("isDraft") else ""
            print(f"[{_now()}] DRY would merge #{n} {head}{draft}", flush=True)
            merged += 1
            continue

        if p.get("isDraft"):
            _gh("pr", "ready", str(n), "--repo", REPO)
            print(f"[{_now()}] marked #{n} {head} ready (was draft, gates green)", flush=True)

        rc, out, err = _gh("pr", "merge", str(n), "--merge", "--repo", REPO)
        if rc == 0:
            print(f"[{_now()}] MERGED #{n} {head}", flush=True)
            merged += 1
        else:
            print(f"[{_now()}] merge #{n} FAILED: {(err or out)[:120]}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
