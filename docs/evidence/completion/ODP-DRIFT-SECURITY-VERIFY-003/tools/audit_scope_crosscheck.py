#!/usr/bin/env python3
"""Reconcile the audited package set against installed dists, uv.lock and the SBOM.

Reviewer requirement (reopen #3, item 1): "核對每個 audited name/version 與
installed/lock/SBOM" -- every package the audit actually reported on must be
the same package, at the same version, that is installed, locked and listed in
the SBOM. A clean audit of the wrong set of packages is not a clean audit of
this candidate.

This reads the raw ``pip-audit --format json`` payload captured from the gate's
own child process and answers four questions, per package:

* was it audited with advisory data, or carried a ``skip_reason`` (which means
  it was seen but never scanned)?
* does the audited version match the version actually installed in the audited
  site-packages directory?
* does it appear in ``uv.lock`` at that version? (The lock is a superset: it
  resolves every platform and extra, so lock-only entries are expected and are
  reported separately rather than as failures.)
* does it appear in the task SBOM's PyPI components at that version?

It also checks the reverse direction -- an installed distribution the audit
never mentioned would be an unscanned package inside the audited scope -- and
asserts the banned monitoring stack is absent from all four views.

This tool renders no security verdict. The gate's exit code is the verdict; a
mismatch here means the *evidence* does not line up and must be explained.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from datetime import UTC, datetime
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
BANNED = ("evidently", "nltk")


def canonical(name: str) -> str:
    """PEP 503 name normalisation, so `Foo.Bar_baz` and `foo-bar-baz` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_audited(payload_path: Path) -> tuple[dict[str, dict], list[dict], list[dict]]:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    audited: dict[str, dict] = {}
    skipped: list[dict] = []
    findings: list[dict] = []
    for entry in payload.get("dependencies", []):
        name = str(entry.get("name", "")).strip()
        version = str(entry.get("version", "")).strip()
        key = canonical(name)
        if entry.get("skip_reason"):
            skipped.append({"name": name, "skip_reason": entry["skip_reason"]})
            continue
        vulns = entry.get("vulns")
        audited[key] = {"name": name, "version": version, "vuln_count": len(vulns or [])}
        for vuln in vulns or []:
            findings.append({"name": name, "version": version, "vuln": vuln})
    return audited, skipped, findings


def load_installed() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for dist in distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        out[canonical(name)] = {"name": name, "version": dist.version}
    return out


def load_lock(lock_path: Path) -> dict[str, dict]:
    data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for package in data.get("package", []):
        name = package.get("name")
        if not name:
            continue
        out[canonical(name)] = {"name": name, "version": package.get("version", "")}
    return out


def load_sbom_pypi(sbom_path: Path) -> dict[str, dict]:
    data = json.loads(sbom_path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for component in data.get("components", []):
        purl = component.get("purl", "")
        if not purl.startswith("pkg:pypi/"):
            continue
        out[canonical(str(component.get("name", "")))] = {
            "name": component.get("name"),
            "version": component.get("version", ""),
            "purl": purl,
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-payload", required=True)
    parser.add_argument("--lock", default="uv.lock")
    parser.add_argument(
        "--sbom", default="docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/sbom.json"
    )
    parser.add_argument(
        "--installed-inventory",
        default=(
            "docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/receipts/"
            "installed_inventory.json"
        ),
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    payload_path = (ROOT / args.audit_payload).resolve()
    lock_path = (ROOT / args.lock).resolve()
    sbom_path = (ROOT / args.sbom).resolve()
    inventory_path = (ROOT / args.installed_inventory).resolve()

    audited, skipped, findings = load_audited(payload_path)
    installed = load_installed()
    lock = load_lock(lock_path)
    sbom = load_sbom_pypi(sbom_path)

    recorded_inventory = {
        canonical(p["name"]): p
        for p in json.loads(inventory_path.read_text(encoding="utf-8"))["packages"]
    }

    rows: list[dict] = []
    mismatches: list[dict] = []
    for key in sorted(audited):
        item = audited[key]
        row = {
            "audited_name": item["name"],
            "audited_version": item["version"],
            "vuln_count": item["vuln_count"],
            "installed_version": installed.get(key, {}).get("version"),
            "recorded_inventory_version": recorded_inventory.get(key, {}).get("version"),
            "lock_version": lock.get(key, {}).get("version"),
            "sbom_version": sbom.get(key, {}).get("version"),
        }
        problems = []
        if row["installed_version"] is None:
            problems.append("not installed in the audited site-packages")
        elif row["installed_version"] != row["audited_version"]:
            problems.append(
                f"installed {row['installed_version']} != audited {row['audited_version']}"
            )
        if row["recorded_inventory_version"] is None:
            problems.append("absent from the committed installed inventory")
        elif row["recorded_inventory_version"] != row["audited_version"]:
            problems.append(
                f"inventory {row['recorded_inventory_version']} != audited {row['audited_version']}"
            )
        if row["lock_version"] is None:
            problems.append("absent from uv.lock")
        elif row["lock_version"] != row["audited_version"]:
            problems.append(f"lock {row['lock_version']} != audited {row['audited_version']}")
        if row["sbom_version"] is None:
            problems.append("absent from the SBOM PyPI components")
        elif row["sbom_version"] != row["audited_version"]:
            problems.append(f"sbom {row['sbom_version']} != audited {row['audited_version']}")
        row["problems"] = problems
        if problems:
            mismatches.append(row)
        rows.append(row)

    installed_not_audited = sorted(set(installed) - set(audited))
    audited_not_installed = sorted(set(audited) - set(installed))
    lock_only = sorted(set(lock) - set(audited))

    banned_presence = {
        pkg: {
            "audited": pkg in audited,
            "installed": pkg in installed,
            "committed_inventory": pkg in recorded_inventory,
            "uv_lock": pkg in lock,
            "sbom_pypi": pkg in sbom,
        }
        for pkg in BANNED
    }
    banned_anywhere = {
        pkg: [view for view, present in views.items() if present]
        for pkg, views in banned_presence.items()
    }

    reconciled = (
        not mismatches
        and not skipped
        and not installed_not_audited
        and not audited_not_installed
        and not findings
        and not any(banned_anywhere.values())
    )

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "generated_by": (
            "docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/tools/"
            "audit_scope_crosscheck.py"
        ),
        "inputs": {
            "audit_payload": {
                "path": str(payload_path.relative_to(ROOT)),
                "sha256": _sha256_file(payload_path),
            },
            "uv_lock": {"path": str(lock_path.relative_to(ROOT)), "sha256": _sha256_file(lock_path)},
            "sbom": {"path": str(sbom_path.relative_to(ROOT)), "sha256": _sha256_file(sbom_path)},
            "installed_inventory": {
                "path": str(inventory_path.relative_to(ROOT)),
                "sha256": _sha256_file(inventory_path),
            },
        },
        "counts": {
            "audited_with_advisory_data": len(audited),
            "skipped_by_pip_audit": len(skipped),
            "vulnerability_findings": len(findings),
            "installed_distributions": len(installed),
            "committed_inventory_packages": len(recorded_inventory),
            "uv_lock_packages": len(lock),
            "sbom_pypi_components": len(sbom),
            "lock_entries_not_installed_on_this_platform": len(lock_only),
        },
        "reconciled": reconciled,
        "mismatches": mismatches,
        "skipped_entries": skipped,
        "vulnerability_findings": findings,
        "installed_but_not_audited": installed_not_audited,
        "audited_but_not_installed": audited_not_installed,
        "banned_package_presence": banned_presence,
        "banned_packages_found_in": {k: v for k, v in banned_anywhere.items() if v},
        "per_package": rows,
        "python_executable": sys.executable,
    }

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        f"crosscheck: audited={len(audited)} installed={len(installed)} "
        f"lock={len(lock)} sbom_pypi={len(sbom)} findings={len(findings)} "
        f"skipped={len(skipped)} mismatches={len(mismatches)} reconciled={reconciled}"
    )
    if not reconciled:
        print("crosscheck: the evidence views do not reconcile; see the report", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
