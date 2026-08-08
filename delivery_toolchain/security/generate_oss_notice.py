#!/usr/bin/env python3
"""Generate the third-party OSS NOTICE from the installed dependency trees.

Human/Ops decided ODP-PLAN-OSS-LEGAL-POLICY-001's LGPL question as
allow-with-obligations: LGPL components stay, on condition that the product
ships an attribution notice naming every third-party package and its licence.
That one notice also discharges two obligations we were already carrying and
not meeting -- Apache-2.0 NOTICE retention and caniuse-lite's CC-BY-4.0
attribution.

Licences are read from the installed trees rather than the lockfiles, because
neither package-lock.json nor uv.lock records a licence. npm licences come from
each package's own package.json; python licences come from installed
distribution metadata, restricted to the distributions uv.lock actually
declares. That restriction is load-bearing: enumerating the interpreter's
site-packages instead sweeps in the operating system's own GPL packages, which
this project does not depend on and must not be attributed as if it did.

Usage:
    generate_oss_notice.py            write NOTICE-THIRD-PARTY.md
    generate_oss_notice.py --check    exit 1 if the committed file is stale
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = ROOT / "NOTICE-THIRD-PARTY.md"
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
        return str(raw.get("type") or "").strip()
    if isinstance(raw, list):
        parts = [
            (item.get("type") if isinstance(item, dict) else str(item)) for item in raw
        ]
        return " OR ".join(p for p in parts if p)
    return ""


def collect_npm(base: Path | None = None) -> list[Component]:
    """Walk node_modules, including nested trees, reading each package.json.

    Nested node_modules matter: npm installs a conflicting transitive version
    beside its dependent rather than at the top level, and a top-level-only
    walk silently omits those copies.
    """
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


def collect_python() -> list[Component]:
    import importlib.metadata as md

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
            licence = str(meta.get("License") or "").strip()
            if not licence or licence.lower() == "unknown" or len(licence) > 60:
                classifiers = [
                    value
                    for key, value in meta.items()
                    if key == "Classifier" and value.startswith("License ::")
                ]
                licence = (
                    classifiers[0].split("::")[-1].strip() if classifiers else "UNKNOWN"
                )
            found[(name, dist.version or "")] = Component(
                "pypi", name, dist.version or "", licence
            )
        except Exception:  # pragma: no cover - metadata is third-party data
            continue
    return sorted(found.values())


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
        "`scripts/security/generate_oss_notice.py`; edit that script, not this file.",
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
    args = parser.parse_args()

    content = build()

    if args.check:
        if not OUTPUT_PATH.exists():
            print(f"{OUTPUT_PATH.name} is missing; run this script.", file=sys.stderr)
            return 1
        if OUTPUT_PATH.read_text(encoding="utf-8") != content:
            print(
                f"{OUTPUT_PATH.name} is stale; run scripts/security/generate_oss_notice.py",
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
