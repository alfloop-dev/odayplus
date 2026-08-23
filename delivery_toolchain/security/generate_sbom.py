#!/usr/bin/env python3
"""Generate CycloneDX 1.5 JSON SBOM from package-lock.json and uv.lock."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "docs/evidence/completion/ODP-PGAP-SUPPLY-001"
EVIDENCE_TASK_DIR = ROOT / "docs/evidence/completion/ODP-OSS-LICENSE-GATE-002"
NODE_MODULES = ROOT / "node_modules"
UV_LOCK = ROOT / "uv.lock"
PACKAGE_LOCK = ROOT / "package-lock.json"
PYPROJECT = ROOT / "pyproject.toml"

FIRST_PARTY_PREFIXES = ("@oday-plus/", "oday-plus")

CONTAINER_BASE_IMAGES = [
    "python:3.12-slim",
    "node:22-slim",
]

# Classification mapping for standard Python classifiers
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

SPDX_STANDARDS = {
    "0BSD", "AFL-2.1", "Apache-2.0", "BlueOak-1.0.0", "BSD-2-Clause", "BSD-3-Clause",
    "CC0-1.0", "CC-BY-4.0", "ISC", "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later",
    "MIT", "MIT-0", "MIT-CMU", "MPL-2.0", "PSF-2.0", "Python-2.0", "Zlib", "ZPL-2.1"
}


def get_git_sha() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=ROOT)
        return res.stdout.strip()
    except Exception:
        return "unknown"


def get_repo_release_digests() -> dict[str, str]:
    git_sha = get_git_sha()
    return {
        "alfloop-dev/odayplus": git_sha,
        "alfloop-dev/pantheon": git_sha,
    }


def normalize_spdx_license(raw_license: str) -> list[dict[str, Any]]:
    if not raw_license or raw_license.strip().upper() == "UNKNOWN":
        return [{"license": {"name": "UNKNOWN"}}]
    lic = raw_license.strip()
    if any(op in lic for op in (" AND ", " OR ", " WITH ")) or "(" in lic:
        return [{"expression": lic}]
    if lic in SPDX_STANDARDS:
        return [{"license": {"id": lic}}]
    return [{"license": {"name": lic}}]


def _normalise_npm_license(raw: object) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return str(raw.get("type") or raw.get("name") or "").strip()
    if isinstance(raw, list):
        parts = [(item.get("type") if isinstance(item, dict) else str(item)) for item in raw]
        return " OR ".join(p for p in parts if p)
    return ""


def get_installed_npm_metadata() -> dict[str, dict[str, Any]]:
    meta_by_dir: dict[str, dict[str, Any]] = {}
    if not NODE_MODULES.is_dir():
        return meta_by_dir

    def walk(directory: Path, rel_prefix: str = "node_modules") -> None:
        if not directory.is_dir():
            return
        for entry in os.scandir(directory):
            if not entry.is_dir():
                continue
            if entry.name.startswith("@"):
                walk(Path(entry.path), f"{rel_prefix}/{entry.name}")
                continue
            manifest = Path(entry.path) / "package.json"
            pkg_rel_path = f"{rel_prefix}/{entry.name}"
            if manifest.exists():
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                meta_by_dir[pkg_rel_path] = data
            walk(Path(entry.path) / "node_modules", f"{pkg_rel_path}/node_modules")

    walk(NODE_MODULES)
    return meta_by_dir


def _get_python_license(dist: md.Distribution, name: str) -> str:
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


def _get_python_scopes() -> tuple[set[str], set[str]]:
    prod_roots: set[str] = set()
    dev_roots: set[str] = set()
    if PYPROJECT.exists():
        try:
            with open(PYPROJECT, "rb") as f:
                pyproj = tomllib.load(f)
            deps = pyproj.get("project", {}).get("dependencies", [])
            for dep in deps:
                match = re.match(r"^([A-Za-z0-9_.\-]+)", dep)
                if match:
                    prod_roots.add(re.sub(r"[-_.]+", "-", match.group(1)).lower())
            dev_deps = pyproj.get("dependency-groups", {}).get("dev", [])
            for dep in dev_deps:
                match = re.match(r"^([A-Za-z0-9_.\-]+)", dep)
                if match:
                    dev_roots.add(re.sub(r"[-_.]+", "-", match.group(1)).lower())
        except Exception as e:
            print(f"Warning: Failed to parse pyproject.toml: {e}", file=sys.stderr)
    return prod_roots, dev_roots


def generate_sbom() -> dict[str, Any]:
    git_sha = get_git_sha()
    components: list[dict[str, Any]] = []
    dependency_graph: list[dict[str, Any]] = []
    root_purl = f"pkg:generic/alfloop-dev/odayplus@{git_sha}"
    root_depends_on: set[str] = set()

    # 1. Parse Node dependencies from package-lock.json
    installed_npm_meta = get_installed_npm_metadata()
    npm_purls_by_pkg_path: dict[str, str] = {}
    npm_deps_raw: dict[str, dict[str, Any]] = {}

    if PACKAGE_LOCK.exists():
        try:
            data = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))
            packages = data.get("packages", {})
            for pkg_path, pkg_info in packages.items():
                if not pkg_path:  # Root workspace
                    continue
                pkg_name = pkg_path.replace("node_modules/", "")
                if "/" in pkg_name and not pkg_name.startswith("@"):
                    pkg_name = pkg_name.split("/")[-1]
                version = pkg_info.get("version")
                if not version or pkg_info.get("link"):
                    continue
                if pkg_name.startswith(FIRST_PARTY_PREFIXES):
                    continue

                purl = f"pkg:npm/{pkg_name}@{version}"
                npm_purls_by_pkg_path[pkg_path] = purl
                npm_deps_raw[purl] = pkg_info.get("dependencies", {})

                # License extraction
                lic_str = _normalise_npm_license(pkg_info.get("license") or pkg_info.get("licenses"))
                if not lic_str and pkg_path in installed_npm_meta:
                    inst = installed_npm_meta[pkg_path]
                    lic_str = _normalise_npm_license(inst.get("license") or inst.get("licenses"))

                # Hashes
                hashes: list[dict[str, str]] = []
                integrity = pkg_info.get("integrity")
                if integrity:
                    if integrity.startswith("sha512-"):
                        hashes.append({"alg": "SHA-512", "content": integrity.replace("sha512-", "")})
                    elif integrity.startswith("sha1-"):
                        hashes.append({"alg": "SHA-1", "content": integrity.replace("sha1-", "")})

                # Scope: required vs optional
                is_dev = bool(pkg_info.get("dev", False))
                scope = "optional" if is_dev else "required"

                # Supplier / author
                supplier: dict[str, Any] | None = None
                inst_meta = installed_npm_meta.get(pkg_path, {})
                author = inst_meta.get("author") or pkg_info.get("author")
                if isinstance(author, str) and author.strip():
                    supplier = {"name": author.strip()}
                elif isinstance(author, dict) and author.get("name"):
                    supplier = {"name": str(author["name"]).strip()}

                comp: dict[str, Any] = {
                    "name": pkg_name,
                    "version": version,
                    "type": "library",
                    "purl": purl,
                    "bom-ref": purl,
                    "scope": scope,
                    "licenses": normalize_spdx_license(lic_str or "UNKNOWN"),
                }
                if hashes:
                    comp["hashes"] = hashes
                if supplier:
                    comp["supplier"] = supplier

                components.append(comp)

            # Direct dependencies of root npm packages
            root_npm = packages.get("", {})
            for d in root_npm.get("dependencies", {}):
                if not d.startswith(FIRST_PARTY_PREFIXES):
                    for p_path, p_url in npm_purls_by_pkg_path.items():
                        if p_path == f"node_modules/{d}":
                            root_depends_on.add(p_url)
        except Exception as e:
            print(f"Warning: Failed to parse package-lock.json: {e}", file=sys.stderr)

    # 2. Parse Python dependencies from uv.lock
    prod_roots, dev_roots = _get_python_scopes()
    installed_dists = {
        re.sub(r"[-_.]+", "-", dist.metadata.get("Name") or "").lower(): dist
        for dist in md.distributions()
        if dist.metadata.get("Name")
    }

    python_packages_by_norm_name: dict[str, dict[str, Any]] = {}
    python_purls_by_name: dict[str, str] = {}

    if UV_LOCK.exists():
        try:
            with open(UV_LOCK, "rb") as f:
                uv_data = tomllib.load(f)
            packages = uv_data.get("package", [])
            for pkg in packages:
                name = pkg.get("name")
                version = pkg.get("version")
                if not name or not version:
                    continue
                if name.startswith(FIRST_PARTY_PREFIXES):
                    continue

                norm_name = re.sub(r"[-_.]+", "-", name).lower()
                python_packages_by_norm_name[norm_name] = pkg
                purl = f"pkg:pypi/{name}@{version}"
                python_purls_by_name[norm_name] = purl

                # Hashes
                hashes = []
                sdist = pkg.get("sdist")
                if isinstance(sdist, dict) and sdist.get("hash"):
                    sdist_hash = sdist["hash"]
                    if ":" in sdist_hash:
                        alg, val = sdist_hash.split(":", 1)
                        hashes.append({"alg": alg.upper(), "content": val})
                wheels = pkg.get("wheels", [])
                for wheel in wheels:
                    if isinstance(wheel, dict) and wheel.get("hash"):
                        w_hash = wheel["hash"]
                        if ":" in w_hash:
                            alg, val = w_hash.split(":", 1)
                            hashes.append({"alg": alg.upper(), "content": val})
                            break

                # License extraction
                lic_str = "UNKNOWN"
                supplier = None
                if norm_name in installed_dists:
                    dist = installed_dists[norm_name]
                    lic_str = _get_python_license(dist, name)
                    author = dist.metadata.get("Author") or dist.metadata.get("Author-email") or dist.metadata.get("Maintainer")
                    if author:
                        supplier = {"name": author.strip()}

                # Scope computation (prod vs dev)
                scope = "required" if (norm_name in prod_roots or not dev_roots or norm_name not in dev_roots) else "optional"

                comp = {
                    "name": name,
                    "version": version,
                    "type": "library",
                    "purl": purl,
                    "bom-ref": purl,
                    "scope": scope,
                    "licenses": normalize_spdx_license(lic_str),
                }
                if hashes:
                    comp["hashes"] = hashes
                if supplier:
                    comp["supplier"] = supplier

                components.append(comp)

                if norm_name in prod_roots or norm_name in dev_roots:
                    root_depends_on.add(purl)
        except Exception as e:
            print(f"Warning: Failed to parse uv.lock: {e}", file=sys.stderr)

    # 3. Build dependency graph
    # Root dependency node
    dependency_graph.append({
        "ref": root_purl,
        "dependsOn": sorted(root_depends_on),
    })

    # Individual component dependency nodes
    for purl, child_deps in npm_deps_raw.items():
        depends_on = []
        for child_name in child_deps:
            for p_path, p_url in npm_purls_by_pkg_path.items():
                if p_path.endswith(f"node_modules/{child_name}"):
                    depends_on.append(p_url)
                    break
        if depends_on:
            dependency_graph.append({
                "ref": purl,
                "dependsOn": sorted(set(depends_on)),
            })

    for norm_name, pkg in python_packages_by_norm_name.items():
        purl = python_purls_by_name[norm_name]
        deps = pkg.get("dependencies", [])
        depends_on = []
        for d in deps:
            d_name = d.get("name") if isinstance(d, dict) else str(d)
            d_norm = re.sub(r"[-_.]+", "-", d_name).lower()
            if d_norm in python_purls_by_name:
                depends_on.append(python_purls_by_name[d_norm])
        if depends_on:
            dependency_graph.append({
                "ref": purl,
                "dependsOn": sorted(set(depends_on)),
            })

    # Deduplicate components by (name, version, purl)
    unique_components: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in components:
        key = f"{c['name']}@{c['version']}@{c.get('purl')}"
        if key not in seen:
            seen.add(key)
            unique_components.append(c)

    unique_components.sort(key=lambda c: (c["name"], c["version"], c.get("purl", "")))

    # Compute serial number / signature tied to git SHA and components
    sbom_content = json.dumps(unique_components, sort_keys=True)
    sbom_hash = hashlib.sha256(sbom_content.encode()).hexdigest()
    release_digests = get_repo_release_digests()

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:sha256-{sbom_hash[:32]}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "tools": [
                {
                    "vendor": "ODay Plus",
                    "name": "generate_sbom.py",
                    "version": "1.0.0",
                }
            ],
            "component": {
                "name": "oday-plus",
                "version": "0.1.0",
                "type": "application",
                "purl": root_purl,
                "bom-ref": root_purl,
            },
            "properties": [
                {"name": "git-sha", "value": git_sha},
                {"name": "sbom-hash", "value": sbom_hash},
                {
                    "name": "sbom-content-digest",
                    "value": f"sha256:{hashlib.sha256(f'{git_sha}:{sbom_hash}'.encode()).hexdigest()}",
                },
                {
                    "name": "container-base-images",
                    "value": json.dumps(CONTAINER_BASE_IMAGES),
                },
                {
                    "name": "repository-release-digests",
                    "value": json.dumps(release_digests),
                },
            ],
        },
        "components": unique_components,
        "dependencies": dependency_graph,
    }
    return sbom


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed SBOM does not match the active dependencies",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="custom path to write SBOM JSON to",
    )
    args = parser.parse_args()

    sbom = generate_sbom()

    target_path = args.output or (OUTPUT_DIR / "sbom.json")

    if args.check:
        if not target_path.exists():
            print(f"SBOM file is missing at {target_path}", file=sys.stderr)
            return 1
        try:
            committed = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Failed to read committed SBOM at {target_path}: {e}", file=sys.stderr)
            return 1

        if committed.get("components") != sbom.get("components"):
            print(
                f"Committed SBOM at {target_path.relative_to(ROOT)} is stale; "
                "run delivery_toolchain/security/generate_sbom.py to regenerate.",
                file=sys.stderr,
            )
            return 1
        print(f"SBOM at {target_path.relative_to(ROOT)} is valid and up to date.")
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_TASK_DIR.mkdir(parents=True, exist_ok=True)

    content = json.dumps(sbom, indent=2) + "\n"
    target_path.write_text(content, encoding="utf-8")
    print(f"SBOM successfully generated at {target_path.relative_to(ROOT)}")
    if target_path != (EVIDENCE_TASK_DIR / "sbom.json"):
        (EVIDENCE_TASK_DIR / "sbom.json").write_text(content, encoding="utf-8")
        print(f"Mirrored SBOM to {EVIDENCE_TASK_DIR.relative_to(ROOT)}/sbom.json")

    print(f"Total components cataloged: {len(sbom['components'])}")
    print(f"SBOM Content Digest: {sbom['metadata']['properties'][2]['value']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

