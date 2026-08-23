#!/usr/bin/env python3
"""Generate the third-party OSS NOTICE from the installed dependency trees.

The legal policy and LGPL disposition remain PROPOSED under
ODP-PLAN-OSS-LEGAL-POLICY-001 until an authoritative external receipt is resolved.
This notice is prepared to document and reconcile third-party components from actual
installed trees and satisfy standing obligations (Apache-2.0 NOTICE retention,
caniuse-lite CC-BY-4.0 attribution, and copyleft/attribution terms).

Licences are read from the installed trees rather than the lockfiles alone, because
neither package-lock.json nor uv.lock records a licence for all ecosystems. npm licences
come from each package's own package.json; python licences come from installed
distribution metadata (including License-Expression, License, and Classifiers),
restricted to the distributions uv.lock actually declares. That restriction is
load-bearing: enumerating the interpreter's site-packages instead sweeps in the
operating system's own GPL packages, which this project does not depend on and
must not be attributed as if it did.

Reading the installed trees means the output is only as complete as the install
it was run against, so regenerate from the full tree CI installs:

    uv sync && npm ci && uv run python delivery_toolchain/security/generate_oss_notice.py

A partial install produces a notice that is short of components but still
internally consistent, so it looks fine locally and fails --check in CI.

Usage:
    generate_oss_notice.py            write NOTICE-THIRD-PARTY.md
    generate_oss_notice.py --check    exit 1 if the committed file is stale
    generate_oss_notice.py --reconcile evaluate installed components against license_policy.json
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = ROOT / "NOTICE-THIRD-PARTY.md"
POLICY_PATH = ROOT / "docs/security/license_policy.json"
EXEMPTIONS_PATH = ROOT / "docs/security/license_exemptions.json"
NODE_MODULES = ROOT / "node_modules"
UV_LOCK = ROOT / "uv.lock"

# Workspace packages are ours. They carry no licence field, and a scanner
# cannot otherwise tell them apart from a third party of unknown licence.
FIRST_PARTY_PREFIXES = ("@oday-plus/", "oday-plus")

# Licences whose terms require more than keeping a copyright line. Recorded so
# the notice states the obligation instead of leaving a reader to look it up.
OBLIGATIONS = {
    "Apache-2.0": "Retain NOTICE; state significant changes if modified.",
    "MPL-2.0": "File-level copyleft: source of any modified MPL file must be offered.",
    "CC-BY-4.0": "Attribution required. Data licence, not a code licence.",
    "LGPL-3.0-or-later": (
        "Weak copyleft. Used unmodified as a dynamically loaded library; "
        "recipients may obtain the library source from its upstream project."
    ),
    "LGPL-3.0-only": "Weak copyleft. Same handling as LGPL-3.0-or-later.",
    "LGPL-2.1": "Weak copyleft. Same handling as LGPL-3.0-or-later.",
    "LGPL-2.1-or-later": "Weak copyleft. Same handling as LGPL-3.0-or-later.",
    "LGPL with exceptions": (
        "Weak copyleft with an upstream linking exception. Used unmodified."
    ),
}

PYTHON_CLASSIFIER_MAP = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1-or-later",
    "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)": "LGPL-2.1-or-later",
}

PYTHON_KNOWN_FALLBACKS = {
    "google-crc32c": "Apache-2.0",
    "graphemeu": "Python-2.0",
    "huey": "MIT",
    "pgserver": "MIT",
    "rich-click": "MIT",
    "skops": "BSD-3-Clause",
    "universal-pathlib": "MIT",
}


@dataclass(frozen=True, order=True)
class Component:
    ecosystem: str
    name: str
    version: str
    license: str


def _normalise_license(raw: object) -> str:
    """Reduce npm's several licence shapes to one string."""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return str(raw.get("type") or raw.get("name") or "").strip()
    if isinstance(raw, list):
        parts = [
            (item.get("type") if isinstance(item, dict) else str(item)) for item in raw
        ]
        return " OR ".join(p for p in parts if p)
    return ""


def collect_npm(base: Path | None = None) -> list[Component]:
    """Walk node_modules, including nested trees, reading each package.json."""
    base = NODE_MODULES if base is None else base
    found: dict[tuple[str, str], Component] = {}

    def walk(directory: Path) -> None:
        if not directory.is_dir():
            return
        for entry in os.scandir(directory):
            if not entry.is_dir():
                continue
            if entry.name.startswith("@"):  # scope directory, not a package
                walk(Path(entry.path))
                continue
            manifest = Path(entry.path) / "package.json"
            if manifest.exists():
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    data = {}
                name = str(data.get("name") or entry.name)
                if not name.startswith(FIRST_PARTY_PREFIXES):
                    licence = _normalise_license(
                        data.get("license") or data.get("licenses")
                    )
                    version = str(data.get("version") or "")
                    found[(name, version)] = Component(
                        "npm", name, version, licence or "UNKNOWN"
                    )
            walk(Path(entry.path) / "node_modules")

    walk(base)
    return sorted(found.values())


def _declared_python_names() -> set[str]:
    """Names uv.lock declares, normalised per PEP 503."""
    if not UV_LOCK.exists():
        return set()
    text = UV_LOCK.read_text(encoding="utf-8")
    return {
        re.sub(r"[-_.]+", "-", name).lower()
        for name in re.findall(r'^name = "([^"]+)"', text, re.MULTILINE)
    }


def _get_python_license_from_dist(dist: md.Distribution, name: str) -> str:
    norm_name = re.sub(r"[-_.]+", "-", name).lower()
    meta = dist.metadata

    # 1. License-Expression (PEP 639)
    lic_expr = meta.get("License-Expression")
    if not lic_expr and hasattr(meta, "json") and isinstance(meta.json, dict):
        lic_expr = meta.json.get("license_expression")
    if lic_expr and lic_expr.strip():
        return lic_expr.strip()

    # 2. Short License header
    lic = str(meta.get("License") or "").strip()
    if lic and lic.lower() != "unknown" and "\n" not in lic and len(lic) <= 50:
        if lic in ("MIT License", "MIT license", "MIT"):
            return "MIT"
        if lic in (
            "Apache 2.0", "Apache License 2.0", "Apache License, Version 2.0",
            "Apache 2", "Apache v2", "Apache License Version 2.0",
            "Apache Software License", "Apache Software License 2.0"
        ):
            return "Apache-2.0"
        if lic in ("BSD License", "3-Clause BSD License", "BSD 3-Clause"):
            return "BSD-3-Clause"
        if lic in ("2-clause BSD", "BSD-2-Clause"):
            return "BSD-2-Clause"
        if lic in ("ISC License", "ISC"):
            return "ISC"
        if lic in ("Python Software Foundation License", "PSFL"):
            return "PSF-2.0"
        if lic == "Dual License" and norm_name == "python-dateutil":
            return "Apache-2.0 OR BSD-3-Clause"
        return lic

    # 3. Classifiers
    classifiers = [c for c in meta.get_all("Classifier") or [] if "License" in c]
    for c in classifiers:
        if c in PYTHON_CLASSIFIER_MAP:
            return PYTHON_CLASSIFIER_MAP[c]

    # 4. Known fallbacks
    if norm_name in PYTHON_KNOWN_FALLBACKS:
        return PYTHON_KNOWN_FALLBACKS[norm_name]

    # 5. Multi-line license text heuristics
    if lic:
        if "Apache License" in lic and "Version 2.0" in lic:
            return "Apache-2.0"
        if "MIT License" in lic or "Permission is hereby granted, free of charge" in lic:
            return "MIT"
        if "BSD 3-Clause" in lic or "Redistribution and use in source and binary forms" in lic:
            return "BSD-3-Clause"

    return "UNKNOWN"


def collect_python() -> list[Component]:
    declared = _declared_python_names()
    if not declared:
        return []

    found: dict[tuple[str, str], Component] = {}
    for dist in md.distributions():
        try:
            meta = dist.metadata
            name = str(meta.get("Name") or "")
            if not name:
                continue
            if re.sub(r"[-_.]+", "-", name).lower() not in declared:
                continue
            licence = _get_python_license_from_dist(dist, name)
            found[(name, dist.version or "")] = Component(
                "pypi", name, dist.version or "", licence
            )
        except Exception:  # pragma: no cover
            continue
    return sorted(found.values())


def _classify_single_term(
    term: str,
    allowed_ids: set[str],
    allowed_with_obligations_ids: set[str],
    review_case_licenses: set[str],
    deny_ids: set[str],
) -> str:
    t = term.strip().strip("()")
    if t in deny_ids:
        return "deny"
    if t == "UNKNOWN" or not t:
        return "unknown"
    if t in review_case_licenses:
        return "review_required"
    if t in allowed_with_obligations_ids:
        return "allow_with_obligations"
    if t in allowed_ids:
        return "allow"
    return "unknown"


def evaluate_compound_expression(
    lic: str,
    allowed_ids: set[str],
    allowed_with_obligations_ids: set[str],
    review_case_licenses: set[str],
    deny_ids: set[str],
) -> str:
    """Evaluate compound SPDX expression using policy precedence order."""
    lic = lic.strip()
    if " OR " in lic:
        terms = [t.strip().strip("()") for t in lic.split(" OR ")]
        classes = [
            _classify_single_term(t, allowed_ids, allowed_with_obligations_ids, review_case_licenses, deny_ids)
            for t in terms
        ]
        if "allow" in classes:
            return "allow"
        if "allow_with_obligations" in classes:
            return "allow_with_obligations"
        if "review_required" in classes:
            return "review_required"
        if all(c == "deny" for c in classes):
            return "deny"
        return "unknown"

    if " AND " in lic:
        terms = [t.strip().strip("()") for t in lic.split(" AND ")]
        classes = [
            _classify_single_term(t, allowed_ids, allowed_with_obligations_ids, review_case_licenses, deny_ids)
            for t in terms
        ]
        # Most restrictive term governs: deny > review_required > unknown > allow_with_obligations > allow
        if "deny" in classes:
            return "deny"
        if "review_required" in classes:
            return "review_required"
        if "unknown" in classes:
            return "unknown"
        if "allow_with_obligations" in classes:
            return "allow_with_obligations"
        return "allow"

    return _classify_single_term(lic, allowed_ids, allowed_with_obligations_ids, review_case_licenses, deny_ids)


def evaluate_policy(
    policy_path: Path | None = None,
    components: list[Component] | None = None,
    exemptions_path: Path | None = None,
) -> dict[str, Any]:
    """Evaluate components against license_policy.json with fail-closed rules."""
    policy_file = policy_path or POLICY_PATH
    if not policy_file.exists():
        raise FileNotFoundError(f"License policy not found: {policy_file}")

    policy = json.loads(policy_file.read_text(encoding="utf-8"))

    if components is None:
        components = collect_npm() + collect_python()

    allowed_ids = {entry["id"] for entry in policy.get("allow", {}).get("licenses", [])}
    allowed_with_obligations_ids = {
        entry["id"] for entry in policy.get("allow_with_obligations", {}).get("licenses", [])
    }
    deny_ids = set(policy.get("deny", {}).get("licenses", []))
    review_required_cases = policy.get("review_required", {}).get("cases", [])
    review_case_licenses = {case["license"] for case in review_required_cases}

    results = {
        "status": "PASS",
        "violations": [],
        "review_required": [],
        "allowed": [],
        "allowed_with_obligations": [],
    }

    exemptions_file = exemptions_path or (ROOT / "docs" / "security" / "license_exemptions.json")
    exemptions = []
    if exemptions_file.exists():
        try:
            ex_data = json.loads(exemptions_file.read_text(encoding="utf-8"))
            exemptions = ex_data.get("exemptions", [])
        except Exception:
            pass

    def is_valid_exemption(ex: dict[str, Any], comp_name: str, lic: str) -> bool:
        from datetime import datetime, timezone
        required = ["package", "purl", "license_or_finding", "scope", "applicable_releases", "rationale"]
        if not all(k in ex for k in required):
            return False
        if ex.get("package") != comp_name:
            return False
        if ex.get("license_or_finding") != lic:
            return False
        expires_str = ex.get("expires_at")
        if not expires_str:
            return False
        try:
            expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
        except ValueError:
            return False
        if expires_at < datetime.now(timezone.utc):
            return False
        approver = ex.get("approved_by", {})
        principal = approver.get("principal_id")
        name = approver.get("display_name", "")
        role = approver.get("role", "")
        invalid_names = {"Antigravity", "Antigravity2", "Antigravity3", "Claude", "Claude2", "Codex", "Gemini", "Copilot", "Human/Ops", "Legal", "Jane Doe", "John Doe"}
        if not principal or name in invalid_names or "AI" in role:
            return False
        return True

    for comp in components:
        lic = comp.license.strip()
        classification = evaluate_compound_expression(
            lic, allowed_ids, allowed_with_obligations_ids, review_case_licenses, deny_ids
        )

        if classification == "deny":
            results["violations"].append(
                {"component": comp, "reason": f"Denied license: {lic}"}
            )
            results["status"] = "FAIL"
        elif classification == "unknown":
            results["violations"].append(
                {"component": comp, "reason": f"Unknown or unclassified license: {lic}"}
            )
            results["status"] = "FAIL"
        elif classification == "review_required":
            valid_ex = any(is_valid_exemption(ex, comp.name, lic) for ex in exemptions)
            if valid_ex:
                results["allowed_with_obligations"].append(comp)
            else:
                results["review_required"].append(
                    {"component": comp, "reason": f"Review required license: {lic}"}
                )
                results["status"] = "FAIL"
        elif classification == "allow_with_obligations":
            results["allowed_with_obligations"].append(comp)
        elif classification == "allow":
            results["allowed"].append(comp)

    return results


def render(npm: list[Component], python: list[Component]) -> str:
    everything = npm + python
    by_licence: dict[str, list[Component]] = {}
    for component in everything:
        by_licence.setdefault(component.license, []).append(component)

    lines: list[str] = [
        "# Third-Party Software Notices",
        "",
        "Oday Plus incorporates the open-source components listed below. Each is",
        "used under the licence shown against it. This file is generated by",
        "`delivery_toolchain/security/generate_oss_notice.py`; edit that script, not this file.",
        "",
        f"Components: {len(everything)} ({len(npm)} npm, {len(python)} python).",
        "",
        "## Components carrying obligations beyond attribution",
        "",
    ]

    flagged = sorted(
        (licence for licence in by_licence if licence in OBLIGATIONS),
        key=str,
    )
    if flagged:
        for licence in flagged:
            lines.append(f"### {licence}")
            lines.append("")
            lines.append(OBLIGATIONS[licence])
            lines.append("")
            for component in sorted(by_licence[licence]):
                lines.append(
                    f"- `{component.name}` {component.version} ({component.ecosystem})"
                )
            lines.append("")
    else:
        lines.extend(["None.", ""])

    lines.extend(["## All components by licence", ""])
    for licence in sorted(by_licence, key=str):
        components = sorted(by_licence[licence])
        lines.append(f"### {licence} ({len(components)})")
        lines.append("")
        for component in components:
            lines.append(
                f"- `{component.name}` {component.version} ({component.ecosystem})"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build() -> str:
    return render(collect_npm(), collect_python())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed notice does not match the installed trees",
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="evaluate installed components against license_policy.json and exit 1 on policy violation",
    )
    args = parser.parse_args()

    content = build()

    if args.reconcile:
        eval_result = evaluate_policy()
        if eval_result["status"] != "PASS" or eval_result["violations"]:
            print(f"Policy evaluation FAILED: {len(eval_result['violations'])} violations found:", file=sys.stderr)
            for v in eval_result["violations"]:
                print(f"  - {v['component'].name} ({v['component'].version}): {v['reason']}", file=sys.stderr)
            return 1
        print("Policy evaluation PASSED: all components reconcile against license_policy.json.")

    if args.check:
        if not OUTPUT_PATH.exists():
            print(f"{OUTPUT_PATH.name} is missing; run this script.", file=sys.stderr)
            return 1
        if OUTPUT_PATH.read_text(encoding="utf-8") != content:
            print(
                f"{OUTPUT_PATH.name} is stale; run delivery_toolchain/security/generate_oss_notice.py",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT_PATH.name} matches the installed dependency trees.")
        return 0

    OUTPUT_PATH.write_text(content, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

