#!/usr/bin/env python3
"""CI gate: block API drift and unapproved breaking changes.

Three checks, run in order. Each is independent, and all run even when an
earlier one fails, so a contributor sees every problem in one CI cycle rather
than rediscovering them one push at a time.

1. **Artifact freshness** — ``packages/openapi-client/openapi.json`` matches the
   schema the live app serves. Catches an endpoint added or changed without
   regenerating the contract.
2. **Client freshness** — ``src/generated/types.ts`` matches the artifact.
   Catches a contract change that never reached the TypeScript client.
3. **Breaking changes** — the artifact is diffed against its merge-base
   revision. A breaking change fails the build unless its signature is listed
   in ``scripts/openapi/approved_breaking_changes.json``.

The approval file is the deliberate escape hatch: breaking changes are
sometimes correct, and a gate with no path forward gets disabled. Adding a
signature is a reviewable diff that names what breaks and why, which is
precisely the conversation the gate exists to force.

Usage::

    python3 scripts/openapi/check_drift.py                 # all checks
    python3 scripts/openapi/check_drift.py --base-ref dev  # diff against a ref
    python3 scripts/openapi/check_drift.py --skip-diff     # freshness only
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.openapi.openapi_diff import diff_openapi  # noqa: E402

ARTIFACT_REL = "packages/openapi-client/openapi.json"
APPROVALS_PATH = REPO_ROOT / "scripts" / "openapi" / "approved_breaking_changes.json"


def _run(*args: str) -> tuple[int, str]:
    result = subprocess.run(
        args, cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    return result.returncode, result.stdout


def _load_approvals() -> set[str]:
    if not APPROVALS_PATH.exists():
        return set()
    payload = json.loads(APPROVALS_PATH.read_text(encoding="utf-8"))
    return {
        entry["signature"]
        for entry in payload.get("approved", [])
        if isinstance(entry, dict) and "signature" in entry
    }


def _base_artifact(base_ref: str) -> dict[str, Any] | None:
    """The artifact as of the merge base with ``base_ref``.

    The merge base -- not the ref tip -- is the right comparison point: diffing
    against the tip would attribute someone else's concurrently-merged change to
    this branch.
    """
    code, merge_base = _run("git", "merge-base", base_ref, "HEAD")
    if code != 0:
        code, merge_base = _run("git", "rev-parse", base_ref)
        if code != 0:
            return None
    revision = merge_base.strip()
    code, blob = _run("git", "show", f"{revision}:{ARTIFACT_REL}")
    if code != 0:
        # The artifact did not exist at the base (the first run, including this
        # task's own PR). Nothing to compare, so nothing can break.
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


def check_artifact_fresh() -> bool:
    from scripts.openapi.export_openapi import main as export_main

    print("[1/3] OpenAPI artifact freshness")
    return export_main(["--check"]) == 0


def check_client_fresh() -> bool:
    from scripts.openapi.generate_client import main as generate_main

    print("[2/3] Generated client freshness")
    return generate_main(["--check"]) == 0


def check_breaking(base_ref: str) -> bool:
    print(f"[3/3] Breaking-change diff against {base_ref}")
    base = _base_artifact(base_ref)
    if base is None:
        print("      No base artifact to compare against; skipping.")
        return True

    head = json.loads((REPO_ROOT / ARTIFACT_REL).read_text(encoding="utf-8"))
    changes = diff_openapi(base, head)
    approved = _load_approvals()

    breaking = [c for c in changes if c.is_breaking and c.signature not in approved]
    waived = [c for c in changes if c.is_breaking and c.signature in approved]
    additive = [c for c in changes if not c.is_breaking]

    for change in additive:
        print(f"      + {change.description}")
    for change in waived:
        print(f"      ~ APPROVED BREAKING: {change.description}")

    if not breaking:
        print(
            f"      OK: {len(additive)} additive, {len(waived)} approved breaking, "
            "0 unapproved breaking."
        )
        return True

    print("", file=sys.stderr)
    print("ERROR: unapproved breaking API changes:", file=sys.stderr)
    for change in breaking:
        print(f"  - {change.description}", file=sys.stderr)
        print(f"    signature: {change.signature}", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "If these breaks are intended, add each signature to\n"
        f"  {APPROVALS_PATH.relative_to(REPO_ROOT)}\n"
        "with a reason and the migration for existing callers.",
        file=sys.stderr,
    )
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        default="origin/dev",
        help="Git ref to diff the artifact against (default: origin/dev).",
    )
    parser.add_argument("--skip-diff", action="store_true", help="Run freshness checks only.")
    args = parser.parse_args(argv)

    # Every check runs regardless of earlier failures: surfacing all problems in
    # one run beats one-per-push discovery.
    results = [check_artifact_fresh(), check_client_fresh()]
    if not args.skip_diff:
        results.append(check_breaking(args.base_ref))

    if all(results):
        print("\nAPI contract gate: PASS")
        return 0
    print("\nAPI contract gate: FAIL", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
