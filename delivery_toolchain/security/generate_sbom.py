#!/usr/bin/env python3
"""Generate CycloneDX 1.5 JSON SBOM from package-lock.json and uv.lock."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "docs/evidence/completion/ODP-PGAP-SUPPLY-001"


def get_git_sha() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return "unknown"


def generate_sbom() -> dict:
    components = []

    # 1. Parse Node dependencies from package-lock.json
    lockfile_path = ROOT / "package-lock.json"
    if lockfile_path.exists():
        try:
            data = json.loads(lockfile_path.read_text(encoding="utf-8"))
            packages = data.get("packages", {})
            for pkg_path, pkg_info in packages.items():
                if not pkg_path:  # Root workspace
                    continue
                pkg_name = pkg_path.replace("node_modules/", "")
                version = pkg_info.get("version")
                if not version:
                    continue
                # Skip workspace links (they have no version or start with 'file:')
                if pkg_info.get("link"):
                    continue

                # Deduplicate
                purl = f"pkg:npm/{pkg_name}@{version}"
                components.append({
                    "name": pkg_name,
                    "version": version,
                    "type": "library",
                    "purl": purl,
                    "bom-ref": purl,
                })
        except Exception as e:
            print(f"Warning: Failed to parse package-lock.json: {e}", file=sys.stderr)

    # 2. Parse Python dependencies from uv.lock
    uv_lock_path = ROOT / "uv.lock"
    if uv_lock_path.exists():
        try:
            with open(uv_lock_path, "rb") as f:
                uv_data = tomllib.load(f)
            packages = uv_data.get("package", [])
            for pkg in packages:
                name = pkg.get("name")
                version = pkg.get("version")
                if name and version:
                    purl = f"pkg:pypi/{name}@{version}"
                    components.append({
                        "name": name,
                        "version": version,
                        "type": "library",
                        "purl": purl,
                        "bom-ref": purl,
                    })
        except Exception as e:
            print(f"Warning: Failed to parse uv.lock: {e}", file=sys.stderr)

    # Compute a unique serial number / signature tied to git SHA and components
    git_sha = get_git_sha()
    sbom_content = json.dumps(components, sort_keys=True)
    sbom_hash = hashlib.sha256(sbom_content.encode()).hexdigest()

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:sha256-{sbom_hash[:32]}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": {
                "name": "oday-plus",
                "version": "0.1.0",
                "type": "application",
            },
            "properties": [
                {"name": "git-sha", "value": git_sha},
                {"name": "sbom-hash", "value": sbom_hash},
                {"name": "sbom-content-digest", "value": f"sha256:{hashlib.sha256(f'{git_sha}:{sbom_hash}'.encode()).hexdigest()}"}
            ]
        },
        "components": components,
    }
    return sbom


def main() -> int:
    print("Generating Software Bill of Materials (SBOM)...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sbom = generate_sbom()
    output_path = OUTPUT_DIR / "sbom.json"
    output_path.write_text(json.dumps(sbom, indent=2), encoding="utf-8")
    print(f"SBOM successfully generated at {output_path.relative_to(ROOT)}")
    print(f"Total components cataloged: {len(sbom['components'])}")
    print(f"SBOM Content Digest: {sbom['metadata']['properties'][2]['value']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
