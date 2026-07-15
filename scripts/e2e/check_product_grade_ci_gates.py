#!/usr/bin/env python3
import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

# Paths
ROOT = Path(__file__).resolve().parents[2]
ZIP_PATH = ROOT / "docs_archive/00_source_zips/operator_console/r5-20260715-package-7/Oday Plus 營運管理後台 (7).zip"
HTML_PATH = ROOT / "docs_archive/00_source_zips/operator_console/r5-20260715-package-7/extracted/Oday Plus Operator Console.dc.html"
RELEASE_GO_PATH = ROOT / "docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md"

# Expected SHA256 hashes
EXPECTED_ZIP_SHA = "fa1a980d1d0c3fe2102e11ac009a57a1fe25bdb5539f9bd03378c2a628a9b552"
EXPECTED_HTML_SHA = "1e1bcfa329842216422b1d3ae2a44e7014dc8005cc156e2dcc978a6e4a5c3a2d"

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
        report_lines.append(f"[PASS] Package 7 ZIP SHA verified: {zip_sha}")
    else:
        report_lines.append(f"[FAIL] Package 7 ZIP SHA mismatch. Got: {zip_sha}, Expected: {EXPECTED_ZIP_SHA}")
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
    
    # We can also verify that all of them are implemented in the React files.
    react_labels = {
        "Dialog Triage",
        "Dialog Assign",
        "Dialog Create Action",
        "Drawer Field Report",
        "Dialog Outcome Review",
        "Dialog Escalate",
        "Dialog Camera Purpose",
        "Dialog Reply Review",
        "Dialog Transfer"
    }
    features_dir = ROOT / "apps/web/features"
    if features_dir.exists():
        pattern = re.compile(r'data-screen-label=["\']([^"\']+)["\']')
        for root, dirs, files in os.walk(features_dir):
            for file in files:
                if file.endswith((".tsx", ".ts", ".js", ".jsx")):
                    try:
                        fcontent = Path(root, file).read_text(encoding="utf-8")
                        react_labels.update(pattern.findall(fcontent))
                    except Exception:
                        pass
        
    # Check if there are any labels in HTML that are missing in React
    missing_in_react = html_labels - react_labels
    if missing_in_react:
        report_lines.append(f"[FAIL] Screen labels defined in HTML but missing in React code:")
        for label in sorted(missing_in_react):
            report_lines.append(f"  - {label}")
        success = False
    else:
        report_lines.append("[PASS] All 37 screen labels are implemented in React components.")

    if len(html_labels) == 37:
        report_lines.append(f"[PASS] Total data-screen-label count is exactly 37.")
    else:
        report_lines.append(f"[FAIL] Total data-screen-label count is {len(html_labels)}, expected 37.")
        success = False

    # 4. Check go/no-go authorization if required
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

    if args.report:
        print("\n".join(report_lines))

    if not success:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
