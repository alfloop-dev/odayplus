#!/usr/bin/env python3
"""Mandatory gate for the R4 Operator Console visual + accessibility proof.

ODP-OC-R4-011 makes the full R4 product E2E visual and accessibility gates
mandatory. The Docker-backed runner proves runtime behaviour; this static
checker blocks release earlier when the wiring or the evidence packet silently
drops a required surface. It enforces four invariants:

1. Canonical source is package 6 and its ZIP SHA-256 still matches LATEST.json.
2. The visual/a11y coverage manifest maps *exactly* the 32 archived
   ``data-screen-label`` values extracted live from the interactive HTML, each
   to a real runtime screenshot spec or an explicit non-runtime/dialog
   coverage assertion whose spec/source file exists.
3. ``run_product_e2e.sh`` still runs the operator product gate
   (``ODP_OPERATOR_PRODUCT_GATE=1``), the operator suite, the operator
   visual/a11y spec, and the map accessibility spec — so the gates are
   mandatory, not opt-in.
4. The completion evidence report exists and pins package 6 and the
   ODP-OC-PROD-014 productization gate.

Run standalone:  python3 scripts/e2e/check_operator_visual_a11y_gate.py
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PACKAGE_DIR = ROOT / "docs_archive/00_source_zips/operator_console/r4-20260707-package-6"
INTERACTIVE_HTML = PACKAGE_DIR / "extracted/Oday Plus Operator Console.dc.html"
CANONICAL_ZIP = PACKAGE_DIR / "Oday Plus 營運管理後台 (6).zip"
LATEST_JSON = ROOT / "docs_archive/00_source_zips/operator_console/LATEST.json"

COVERAGE_MANIFEST = ROOT / "docs/evidence/completion/ODP-OC-R4-011/screen_label_coverage.json"
COMPLETION_REPORT = ROOT / "docs/evidence/completion/ODP-OC-R4-011/VISUAL_A11Y_GATE_REPORT.md"
RUNNER = ROOT / "scripts/e2e/run_product_e2e.sh"

EXPECTED_LABEL_COUNT = 32
VALID_COVERAGE = {"runtime_workspace", "runtime_dialog", "non_runtime_assertion"}
REQUIRED_VIEWPORTS = {"desktop-1440x900", "constrained-1024x768"}

# Tokens that must remain in the product E2E runner so the gates stay mandatory.
REQUIRED_RUNNER_TOKENS = (
    "ODP_OPERATOR_PRODUCT_GATE=1",
    "tests/e2e/operator-visual-a11y.spec.ts",
    "tests/e2e/e2e-operator-console.spec.ts",
    "tests/e2e/e2e-map-a11y.spec.ts",
    "tests/e2e/operator-store-ops.spec.ts",
    "tests/e2e/operator-growth.spec.ts",
    "tests/e2e/operator-network-review.spec.ts",
)


def _canonical_labels() -> set[str]:
    text = INTERACTIVE_HTML.read_text(encoding="utf-8")
    return set(re.findall(r'data-screen-label="([^"]+)"', text))


def main() -> int:
    errors: list[str] = []

    # --- 1. canonical source integrity -----------------------------------
    for label, path in (
        ("interactive html", INTERACTIVE_HTML),
        ("canonical zip", CANONICAL_ZIP),
        ("LATEST.json", LATEST_JSON),
        ("coverage manifest", COVERAGE_MANIFEST),
        ("completion report", COMPLETION_REPORT),
        ("product runner", RUNNER),
    ):
        if not path.exists():
            errors.append(f"missing {label}: {path.relative_to(ROOT)}")
    if errors:
        _emit(errors)
        return 1

    labels = _canonical_labels()
    if len(labels) != EXPECTED_LABEL_COUNT:
        errors.append(
            f"archived HTML has {len(labels)} screen labels, expected {EXPECTED_LABEL_COUNT}"
        )

    latest = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
    zip_sha = hashlib.sha256(CANONICAL_ZIP.read_bytes()).hexdigest()
    if zip_sha != latest.get("zip_sha256"):
        errors.append(
            f"canonical ZIP sha256 {zip_sha} != LATEST.json {latest.get('zip_sha256')}"
        )
    if latest.get("screen_label_count") != EXPECTED_LABEL_COUNT:
        errors.append(
            f"LATEST.json screen_label_count={latest.get('screen_label_count')} "
            f"!= {EXPECTED_LABEL_COUNT}"
        )

    # --- 2. coverage manifest maps exactly the 32 canonical labels --------
    manifest = json.loads(COVERAGE_MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("canonical_source", {}).get("zip_sha256") != zip_sha:
        errors.append("coverage manifest zip_sha256 does not match canonical ZIP")

    entries = manifest.get("screen_labels", {})
    covered = set(entries)
    for missing in sorted(labels - covered):
        errors.append(f"coverage manifest missing canonical label: {missing!r}")
    for extra in sorted(covered - labels):
        errors.append(f"coverage manifest has non-canonical label: {extra!r}")

    for label in sorted(covered & labels):
        entry = entries[label]
        coverage = entry.get("coverage")
        if coverage not in VALID_COVERAGE:
            errors.append(f"{label!r}: invalid coverage {coverage!r}")
            continue
        if coverage == "runtime_workspace":
            viewports = set(entry.get("viewports", []))
            if not REQUIRED_VIEWPORTS.issubset(viewports):
                errors.append(
                    f"{label!r}: runtime_workspace must cover desktop+constrained viewports"
                )
            spec = entry.get("spec", "")
            if not spec or not (ROOT / spec).exists():
                errors.append(f"{label!r}: spec file missing: {spec!r}")
        elif coverage == "runtime_dialog":
            spec = entry.get("spec", "")
            if not spec or not (ROOT / spec).exists():
                errors.append(f"{label!r}: dialog spec file missing: {spec!r}")
        else:  # non_runtime_assertion
            source = entry.get("source", "")
            if not source or not (ROOT / source).exists():
                errors.append(f"{label!r}: assertion source file missing: {source!r}")

    # --- 3. runner keeps the gates mandatory ------------------------------
    runner_text = RUNNER.read_text(encoding="utf-8")
    for token in REQUIRED_RUNNER_TOKENS:
        if token not in runner_text:
            errors.append(f"product runner no longer wires mandatory token: {token}")

    # --- 4. completion report pins provenance + productization gate -------
    report_text = COMPLETION_REPORT.read_text(encoding="utf-8")
    for token in (zip_sha, "ODP-OC-PROD-014", "r4-20260707-package-6", "ODP_OPERATOR_PRODUCT_GATE"):
        if token not in report_text:
            errors.append(f"completion report missing required token: {token}")

    if errors:
        _emit(errors)
        return 1

    print(
        "Operator visual/a11y gate passed: "
        f"{EXPECTED_LABEL_COUNT} canonical labels covered "
        f"({manifest.get('coverage_counts')})."
    )
    return 0


def _emit(errors: list[str]) -> None:
    print("Operator visual/a11y gate failed:")
    for error in errors:
        print(f"- {error}")


if __name__ == "__main__":
    raise SystemExit(main())
