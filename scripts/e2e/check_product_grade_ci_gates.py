#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# Paths
ROOT = Path(__file__).resolve().parents[2]
ZIP_PATH = ROOT / "docs_archive/00_source_zips/operator_console/r7-20260720-package-10/Oday Plus 營運管理後台 (10).zip"
HTML_PATH = ROOT / "docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/Oday Plus Operator Console.dc.html"
RELEASE_GO_PATH = ROOT / "docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md"
REMOTE_VISUAL_APPROVAL_PATH = (
    ROOT / "docs/evidence/operator_console_r7_remote_visual_approval.json"
)

# Expected SHA256 hashes
EXPECTED_ZIP_SHA = "d1583a00496f928b0765c1756c9671fedf615f12c84c00494d454c983645d7f8"
EXPECTED_HTML_SHA = "cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d"
EXPECTED_SCREEN_LABEL_COUNT = 40
REQUIRED_VISUAL_VIEWPORTS = {390, 1024, 1440}
REQUIRED_VISUAL_ROUTES = {
    "/operator",
    "/operator?ws=store",
    "/operator?ws=growth",
    "/operator?ws=network&tab=radar",
    "/operator?ws=govern",
    "/w/expansion/listings",
    "/intake/:intakeId",
}

def get_sha256(filepath):
    if not filepath.exists():
        return None
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

def extract_labels_from_html(html_path):
    if not html_path.exists():
        return set()
    content = html_path.read_text(encoding="utf-8")
    # match data-screen-label="xxx" or data-screen-label='xxx'
    pattern = re.compile(r'data-screen-label=["\']([^"\']+)["\']')
    return set(pattern.findall(content))


def validate_remote_visual_approval(path):
    if not path.exists():
        return ["remote visual approval artifact is missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"remote visual approval artifact is invalid: {exc}"]

    errors = []
    if payload.get("status") != "approved":
        errors.append("remote visual approval status must be approved")
    if payload.get("authenticated") is not True:
        errors.append("remote visual run must be authenticated")
    if payload.get("canonical_html_sha256") != EXPECTED_HTML_SHA:
        errors.append("remote visual run targets the wrong Package 10 HTML")
    release_sha = payload.get("release_sha")
    if not isinstance(release_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        errors.append("remote visual release_sha must be a full 40-character SHA")
    if payload.get("web_release_sha") != release_sha:
        errors.append("remote visual web_release_sha must match release_sha")
    if payload.get("api_release_sha") != release_sha:
        errors.append("remote visual api_release_sha must match release_sha")
    if payload.get("production_fixture_count") != 0:
        errors.append("remote visual run must prove zero production fixtures")

    viewports = {
        value for value in payload.get("viewports", []) if isinstance(value, int)
    }
    if not REQUIRED_VISUAL_VIEWPORTS.issubset(viewports):
        errors.append("remote visual run is missing a required viewport")
    routes = {
        value for value in payload.get("routes", []) if isinstance(value, str)
    }
    if not REQUIRED_VISUAL_ROUTES.issubset(routes):
        errors.append("remote visual run is missing a required route")
    return errors


def main():
    parser = argparse.ArgumentParser(description="Product-grade CI release gate validator.")
    parser.add_argument("--require-go", action="store_true", help="Enforce go/no-go release authorization presence.")
    parser.add_argument("--report", action="store_true", help="Print verification report.")
    args = parser.parse_args()

    success = True
    report_lines = []

    report_lines.append("=== Oday Plus Product-Grade CI Gate Validation ===")
    
    # 1. Verify ZIP SHA
    zip_sha = get_sha256(ZIP_PATH)
    if zip_sha == EXPECTED_ZIP_SHA:
        report_lines.append(f"[PASS] Package 10 ZIP SHA verified: {zip_sha}")
    else:
        report_lines.append(f"[FAIL] Package 10 ZIP SHA mismatch. Got: {zip_sha}, Expected: {EXPECTED_ZIP_SHA}")
        success = False

    # 2. Verify HTML SHA
    html_sha = get_sha256(HTML_PATH)
    if html_sha == EXPECTED_HTML_SHA:
        report_lines.append(f"[PASS] Interactive HTML SHA verified: {html_sha}")
    else:
        report_lines.append(f"[FAIL] Interactive HTML SHA mismatch. Got: {html_sha}, Expected: {EXPECTED_HTML_SHA}")
        success = False

    # 3. Verify screen labels
    html_labels = extract_labels_from_html(HTML_PATH)
    report_lines.append(f"Found {len(html_labels)} unique data-screen-labels in interactive HTML.")
    
    # Dynamic screen-label maps are valid, but every canonical label must exist
    # as an exact source string. Do not pre-seed labels as implementation proof.
    react_source = []
    features_dir = ROOT / "apps/web/features"
    if features_dir.exists():
        for root, _, files in os.walk(features_dir):
            for file in files:
                if file.endswith((".tsx", ".ts", ".js", ".jsx")):
                    try:
                        react_source.append(Path(root, file).read_text(encoding="utf-8"))
                    except Exception:
                        pass
    source_text = "\n".join(react_source)
    react_labels = {label for label in html_labels if label in source_text}

    # Check if there are any labels in HTML that are missing in React
    missing_in_react = html_labels - react_labels
    if missing_in_react:
        report_lines.append("[FAIL] Screen labels defined in HTML but missing in React code:")
        for label in sorted(missing_in_react):
            report_lines.append(f"  - {label}")
        success = False
    else:
        report_lines.append(
            f"[PASS] All {EXPECTED_SCREEN_LABEL_COUNT} Package 10 screen labels exist in React source."
        )

    if len(html_labels) == EXPECTED_SCREEN_LABEL_COUNT:
        report_lines.append(
            f"[PASS] Total Package 10 data-screen-label count is exactly {EXPECTED_SCREEN_LABEL_COUNT}."
        )
    else:
        report_lines.append(
            f"[FAIL] Total data-screen-label count is {len(html_labels)}, "
            f"expected {EXPECTED_SCREEN_LABEL_COUNT}."
        )
        success = False

    # 4. Check go/no-go authorization and authenticated remote visual evidence.
    if args.require_go:
        if RELEASE_GO_PATH.exists():
            content = RELEASE_GO_PATH.read_text(encoding="utf-8").lower()
            if "go" in content:
                report_lines.append("[PASS] PRODUCT_RELEASE_GO_NO_GO.md authorizes release.")
            else:
                report_lines.append("[FAIL] PRODUCT_RELEASE_GO_NO_GO.md exists but does not authorize release.")
                success = False
        else:
            report_lines.append("[FAIL] --require-go specified but PRODUCT_RELEASE_GO_NO_GO.md is missing.")
            success = False
        visual_errors = validate_remote_visual_approval(REMOTE_VISUAL_APPROVAL_PATH)
        if visual_errors:
            report_lines.append("[FAIL] Package 10 authenticated remote visual approval is incomplete:")
            report_lines.extend(f"  - {error}" for error in visual_errors)
            success = False
        else:
            report_lines.append(
                "[PASS] Package 10 authenticated Cloud Run visual approval is complete."
            )

    if args.report:
        print("\n".join(report_lines))

    if not success:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
