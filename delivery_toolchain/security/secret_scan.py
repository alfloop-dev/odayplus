#!/usr/bin/env python3
"""Secret scanner to detect leaked secrets, private keys, and API tokens."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# High-risk patterns to block
PATTERNS = {
    "Private Key": re.compile(r"-----BEGIN[ A-Z0-9_-]+PRIVATE KEY-----"),
    "Generic API Key / Token": re.compile(r"(?i)(api[_-]key|auth[_-]token|gh[_-]token|access[_-]token|client[_-]secret)\s*[:=]\s*['\"][a-zA-Z0-9_\-\.]{16,}['\"]"),
    "AWS Access Key ID": re.compile(r"AKIA[0-9A-Z]{16}"),
    "AWS Secret Access Key": re.compile(r"(?i)aws[_-]secret[_-]access[_-]key\s*[:=]\s*['\"][a-zA-Z0-9/+=]{40}['\"]"),
    "Google OAuth Client Secret": re.compile(r"AIza[0-9A-Za-z-_]{35}"),
}

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    ".next",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".odp_data",
    "docs",
}

EXCLUDE_FILES = {
    "package-lock.json",
    "uv.lock",
    "secret_scan.py",
}


def scan_file(path: Path) -> list[str]:
    violations = []
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Skip binary files
        return []

    for name, regex in PATTERNS.items():
        for line_num, line in enumerate(content.splitlines(), 1):
            if regex.search(line):
                # Only allow specific bypass using pragma in test/fixture/mock paths
                if any(term in str(path).lower() for term in ["test", "fixture", "mock"]):
                    if "# pragma: allowlist-secret" in line:
                        continue
                try:
                    rel_path = path.relative_to(ROOT)
                except ValueError:
                    rel_path = path
                violations.append(f"{rel_path}:{line_num}: Found {name}")
    return violations


def main() -> int:
    print("Starting secret scanning...")
    violations = []
    for p in ROOT.rglob("*"):
        if p.is_file():
            # Check exclusions
            parts = p.relative_to(ROOT).parts
            if any(d in parts for d in EXCLUDE_DIRS):
                continue
            if p.name in EXCLUDE_FILES:
                continue

            violations.extend(scan_file(p))

    if violations:
        print("\n[SECURITY FAILURE] Secrets scan failed. The following violations were found:")
        for v in violations:
            print(f"  - {v}")
        return 1

    print("Secrets scan passed successfully. No violations found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
