#!/usr/bin/env python3
"""Fail-closed prerequisites probe for Chromium and Playwright E2E execution.

Validates that Node, npm, @playwright/test, and the Chromium browser binary
together with all host OS shared libraries (e.g., libnspr4, libnss3, libgbm)
are installed and capable of headless execution before test suites or Docker
services are started.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REMEDIATION_MESSAGE = """
Remediation:
  Run the unified Product E2E bootstrap contract:
    make product-e2e-bootstrap
  Or directly:
    delivery_toolchain/e2e/bootstrap_product_e2e.sh
  To install OS system libraries and Chromium browser explicitly:
    npx playwright install --with-deps chromium
"""

NODE_CHROMIUM_PROBE = """
const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setContent('<html><body><h1>e2e preflight ok</h1></body></html>');
  await browser.close();
})().catch((err) => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
"""


def check_chromium_prerequisites(root_dir: Path | None = None) -> tuple[bool, str]:
    """Probe system and browser readiness for Product E2E tests.

    Returns (is_ready, diagnostic_message).
    """
    repo_root = root_dir or ROOT

    # 1. Check Node.js CLI
    node_path = shutil.which("node")
    if not node_path:
        return False, (
            "Node.js ('node') executable is not installed or not in PATH.\n"
            + REMEDIATION_MESSAGE
        )

    # 2. Check npm CLI
    npm_path = shutil.which("npm")
    if not npm_path:
        return False, (
            "npm executable is not installed or not in PATH.\n"
            + REMEDIATION_MESSAGE
        )

    # 3. Probe @playwright/test module resolution and Chromium headless launch
    probe_result = subprocess.run(
        [node_path, "-e", NODE_CHROMIUM_PROBE],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )

    if probe_result.returncode != 0:
        error_output = (probe_result.stderr or probe_result.stdout).strip()
        return False, (
            "Chromium browser or host system dependencies check failed:\n"
            f"{error_output}\n"
            + REMEDIATION_MESSAGE
        )

    return True, "Chromium browser and Playwright dependencies verified successfully."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress output on success; only print errors on failure.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ok, message = check_chromium_prerequisites(ROOT)
    if not ok:
        print("[FAIL-CLOSED] Product E2E Chromium prerequisites check failed:", file=sys.stderr)
        print(message, file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"[OK] {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
