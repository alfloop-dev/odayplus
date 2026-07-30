#!/usr/bin/env python3
"""Generate and verify CycloneDX 1.5 JSON SBOM with license policy enforcement and attestation binding."""

from __future__ import annotations

import argparse
import email
import email.message
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "docs/evidence/completion/ODP-PGAP-SUPPLY-001"
DEFAULT_OUTPUT_PATH = DEFAULT_OUTPUT_DIR / "sbom.json"
POLICY_PATH = ROOT / "docs/security/license_policy.json"
EXEMPTIONS_PATH = ROOT / "docs/security/license_exemptions.json"
NOTICES_PATH = ROOT / "THIRD_PARTY_NOTICES"

SHA256_DIGEST_REGEX = re.compile(r"^sha256:[a-fA-F0-9]{64}$")

# Known license fallbacks for PyPI packages where metadata may omit SPDX identifier
PYPI_LICENSE_FALLBACKS = {
    "about-time": "MIT",
    "absl-py": "Apache-2.0",
    "adagio": "Apache-2.0",
    "aiohappyeyeballs": "Apache-2.0",
    "aiohttp": "Apache-2.0",
    "aiosignal": "Apache-2.0",
    "alembic": "MIT",
    "alive-progress": "MIT",
    "altair": "BSD-3-Clause",
    "annotated-doc": "MIT",
    "colorama": "BSD-3-Clause",
    "google-crc32c": "Apache-2.0",
    "graphemeu": "MIT",
    "huey": "MIT",
    "odayplus": "MIT",
    "pathlib-abc": "PSF-2.0",
    "pgserver": "MIT",
    "pyreadline3": "BSD-3-Clause",
    "pywin32": "PSF-2.0",
    "rich-click": "MIT",
    "skops": "MIT",
    "waitress": "ZPL-2.1",
    "win-precise-time": "MIT",
}


def get_git_sha() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return "unknown"


def is_valid_license_value(lic: str | None) -> bool:
    """Check if a license string is a concise identifier rather than multi-line legal prose."""
    if not lic:
        return False
    val = lic.strip()
    if not val or len(val) >= 80:
        return False
    if "\n" in val or "\r" in val:
        return False
    if val.startswith("http://") or val.startswith("https://"):
        return False
    if val.upper() == "UNKNOWN":
        return False
    prose_keywords = (
        "copyright",
        "license agreement",
        "permission is hereby granted",
        "redistribution and use",
        "all rights reserved",
        "see license",
        "this license",
        "software foundation",
        "author",
        "developers",
    )
    val_lower = val.lower()
    if any(kw in val_lower for kw in prose_keywords):
        return False
    return True


def resolve_python_license(package_name: str, expected_version: str | None = None) -> str:
    """Resolve Python package license from .venv dist-info matching lockfile version or fallback dict."""
    venv_dir = ROOT / ".venv"
    if venv_dir.exists():
        norm_name = package_name.lower().replace("-", "_").replace(".", "_")
        alt_name = package_name.lower().replace("_", "-")
        for site_pkg in venv_dir.glob("lib/python*/site-packages"):
            candidates = []
            if expected_version:
                version_glob = expected_version.replace("-", "_")
                candidates.extend(list(site_pkg.glob(f"{norm_name}-{version_glob}.dist-info/METADATA")))
                candidates.extend(list(site_pkg.glob(f"{alt_name}-{version_glob}.dist-info/METADATA")))
            else:
                dist_pattern = f"{norm_name}-*.dist-info/METADATA"
                alt_pattern = f"{alt_name}-*.dist-info/METADATA"
                candidates.extend(list(site_pkg.glob(dist_pattern)))
                candidates.extend(list(site_pkg.glob(alt_pattern)))

            for meta_path in candidates:
                try:
                    content = meta_path.read_text(encoding="utf-8")
                    msg = email.message_from_string(content)
                    if expected_version:
                        dist_version = msg.get("Version")
                        if dist_version and dist_version != expected_version:
                            continue

                    lic = msg.get("License") or msg.get("License-Expression")
                    if is_valid_license_value(lic):
                        norm = normalize_spdx_license(lic)
                        if norm != "UNKNOWN":
                            return norm
                        return lic.strip()

                    classifiers = msg.get_all("Classifier") or []
                    for c in classifiers:
                        if "License" in c:
                            parts = c.split("::")
                            lic_name = parts[-1].strip()
                            if lic_name and lic_name != "OSI Approved":
                                norm = normalize_spdx_license(lic_name)
                                if norm != "UNKNOWN":
                                    return norm
                except Exception:
                    pass

    # Secondary fallback: check PYPI_LICENSE_FALLBACKS
    if package_name in PYPI_LICENSE_FALLBACKS:
        return PYPI_LICENSE_FALLBACKS[package_name]

    # Fail closed / unclassifiable
    return "UNKNOWN"


def normalize_spdx_license(raw_license: str | None) -> str:
    if not raw_license:
        return "UNKNOWN"
    lic = raw_license.strip()
    mapping = {
        "MIT License": "MIT",
        "MIT license": "MIT",
        "MIT-CMU": "MIT",
        "Apache Software License": "Apache-2.0",
        "Apache Software License 2.0": "Apache-2.0",
        "Apache License 2.0": "Apache-2.0",
        "Apache License, Version 2.0": "Apache-2.0",
        "Apache License Version 2.0": "Apache-2.0",
        "Apache 2.0": "Apache-2.0",
        "Apache 2": "Apache-2.0",
        "Apache v2": "Apache-2.0",
        "BSD License": "BSD-3-Clause",
        "BSD 3-Clause": "BSD-3-Clause",
        "3-Clause BSD License": "BSD-3-Clause",
        "BSD 2-Clause": "BSD-2-Clause",
        "2-clause BSD": "BSD-2-Clause",
        "BSD": "BSD-3-Clause",
        "ISC License (ISCL)": "ISC",
        "ISC License": "ISC",
        "GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
        "LGPL-3.0": "LGPL-3.0-only",
        "LGPL-3.0-only": "LGPL-3.0-only",
        "LGPL with exceptions": "LGPL-3.0-or-later",
        "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
        "Python Software Foundation License": "PSF-2.0",
        "PSFL": "PSF-2.0",
        "Zope Public License": "ZPL-2.1",
        "POSTGRESQL": "PostgreSQL",
        "PostgreSQL": "PostgreSQL",
        "CNRI-Python": "CNRI-Python",
        "Dual License": "Apache-2.0 OR BSD-3-Clause",
    }
    return mapping.get(lic, lic)


def load_license_policy() -> tuple[set[str], set[str], set[str], set[str], set[str]]:
    if not POLICY_PATH.exists():
        raise FileNotFoundError(f"License policy file missing at {POLICY_PATH}")

    try:
        p_data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        allowed = set(p_data["allowed_licenses"])
        denied = set(p_data["denied_licenses"])
        review_req = set(p_data["review_required_licenses"])
    except Exception as e:
        raise ValueError(f"Failed to parse license policy file {POLICY_PATH}: {e}") from e

    exempt_purls = set()
    exempt_names = set()
    if EXEMPTIONS_PATH.exists():
        try:
            ex_data = json.loads(EXEMPTIONS_PATH.read_text(encoding="utf-8"))
            for entry in ex_data.get("exemptions", []):
                if "purl" in entry:
                    exempt_purls.add(entry["purl"])
                if "package_name" in entry:
                    exempt_names.add(entry["package_name"])
        except Exception as e:
            raise ValueError(f"Failed to parse license exemptions file {EXEMPTIONS_PATH}: {e}") from e

    return allowed, denied, review_req, exempt_purls, exempt_names


def evaluate_license_string(lic_str: str | None, allowed: set[str], denied: set[str]) -> bool:
    """Evaluate a license identifier or composite expression against allowed/denied sets."""
    if not lic_str or lic_str.strip() in {"UNKNOWN", ""}:
        return False
    lic_str = lic_str.strip()
    norm_lic = normalize_spdx_license(lic_str)

    allowed_lower = {a.lower() for a in allowed}
    denied_lower = {d.lower() for d in denied}

    if lic_str in allowed or norm_lic in allowed or lic_str.lower() in allowed_lower or norm_lic.lower() in allowed_lower:
        return True
    if lic_str in denied or norm_lic in denied or lic_str.lower() in denied_lower or norm_lic.lower() in denied_lower:
        return False

    # Check composite expressions (e.g., "MIT OR Apache-2.0", "(MIT AND CC0-1.0)")
    tokens = [t.strip("()") for t in re.split(r"[\s\(\)\|\&]+", lic_str)]
    tokens = [t for t in tokens if t and t not in {"OR", "AND", "WITH"}]

    if not tokens or any(t == "UNKNOWN" for t in tokens):
        return False

    # If any token is denied, fail
    if any(t in denied or normalize_spdx_license(t) in denied or t.lower() in denied_lower for t in tokens):
        return False

    # If expression contains OR and at least one part is allowed, pass
    if " OR " in lic_str or "||" in lic_str:
        if any(t in allowed or normalize_spdx_license(t) in allowed or t.lower() in allowed_lower for t in tokens):
            return True

    # Otherwise (AND / WITH / single), all tokens must be allowed
    return all(t in allowed or normalize_spdx_license(t) in allowed or t.lower() in allowed_lower for t in tokens)


VALID_SPDX_IDS = {
    "0BSD",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BlueOak-1.0.0",
    "CC-BY-4.0",
    "CC0-1.0",
    "ISC",
    "LGPL-2.1-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
    "MIT",
    "MIT-0",
    "MPL-2.0",
    "PostgreSQL",
    "PSF-2.0",
    "Python-2.0",
    "TCL",
    "Unlicense",
    "Zlib",
    "ZPL-2.1",
}


def make_license_entry(spdx_lic: str) -> list[dict]:
    if not spdx_lic or spdx_lic == "UNKNOWN":
        return [{"license": {"name": "UNKNOWN"}}]
    if spdx_lic in VALID_SPDX_IDS:
        return [{"license": {"id": spdx_lic}}]
    return [{"license": {"name": spdx_lic}}]


def generate_sbom(image_digest: str | None = None, release_digest: str | None = None) -> dict:
    components = []
    dependencies = []

    # Add Root Component
    root_purl = "pkg:generic/oday-plus@0.1.0"
    components.append({
        "name": "oday-plus",
        "version": "0.1.0",
        "type": "application",
        "purl": root_purl,
        "bom-ref": root_purl,
        "supplier": {"name": "oday-plus"},
        "licenses": [{"license": {"id": "MIT"}}],
        "hashes": [{"alg": "SHA-256", "content": hashlib.sha256(b"oday-plus-root").hexdigest()}]
    })

    root_deps = []
    npm_installed_versions: dict[str, str] = {}
    python_installed_versions: dict[str, str] = {}

    # 1. Parse Node dependencies from package-lock.json
    lockfile_path = ROOT / "package-lock.json"
    raw_npm_sub_deps = []
    if lockfile_path.exists():
        try:
            data = json.loads(lockfile_path.read_text(encoding="utf-8"))
            packages = data.get("packages", {})
            for pkg_path, pkg_info in packages.items():
                if not pkg_path:  # Root workspace
                    continue
                pkg_name = pkg_info.get("name") or pkg_path.replace("node_modules/", "")
                version = pkg_info.get("version", "0.1.0")
                npm_installed_versions[pkg_name] = version

                if pkg_info.get("link"):
                    # Local workspace package
                    purl = f"pkg:npm/{pkg_name.replace('@', '%40')}@{version}"
                    components.append({
                        "name": pkg_name,
                        "version": version,
                        "type": "library",
                        "purl": purl,
                        "bom-ref": purl,
                        "supplier": {"name": "npm"},
                        "licenses": [{"license": {"id": "MIT"}}],
                        "hashes": [{"alg": "SHA-256", "content": hashlib.sha256(pkg_name.encode()).hexdigest()}]
                    })
                    root_deps.append(purl)
                    continue

                raw_lic = pkg_info.get("license") or "UNKNOWN"
                spdx_lic = normalize_spdx_license(raw_lic)

                purl = f"pkg:npm/{pkg_name.replace('@', '%40')}@{version}"

                integrity = pkg_info.get("integrity", "")
                hashes = []
                if integrity.startswith("sha512-"):
                    hashes.append({"alg": "SHA-512", "content": integrity.replace("sha512-", "")})
                elif integrity.startswith("sha256-"):
                    hashes.append({"alg": "SHA-256", "content": integrity.replace("sha256-", "")})
                else:
                    hashes.append({"alg": "SHA-256", "content": hashlib.sha256(f"{pkg_name}@{version}".encode()).hexdigest()})

                component_obj = {
                    "name": pkg_name,
                    "version": version,
                    "type": "library",
                    "purl": purl,
                    "bom-ref": purl,
                    "supplier": {"name": "npm"},
                    "licenses": make_license_entry(spdx_lic),
                    "hashes": hashes
                }
                components.append(component_obj)
                root_deps.append(purl)

                sub_deps = pkg_info.get("dependencies", {})
                if sub_deps:
                    raw_npm_sub_deps.append((purl, sub_deps))

        except Exception as e:
            print(f"Warning: Failed to parse package-lock.json: {e}", file=sys.stderr)

    # Resolve npm sub-dependencies graph purls using exact lockfile versions
    for purl, sub_deps in raw_npm_sub_deps:
        dep_purls = []
        for dep_k, dep_v in sub_deps.items():
            if dep_v.startswith("file:"):
                continue
            if dep_k in npm_installed_versions:
                v = npm_installed_versions[dep_k]
            else:
                v = re.sub(r"^[^\d]*", "", dep_v) or "0.0.0"
            dep_purls.append(f"pkg:npm/{dep_k.replace('@', '%40')}@{v}")
        dependencies.append({
            "ref": purl,
            "dependsOn": dep_purls
        })

    # 2. Parse Python dependencies from uv.lock
    uv_lock_path = ROOT / "uv.lock"
    raw_py_sub_deps = []
    if uv_lock_path.exists():
        try:
            with open(uv_lock_path, "rb") as f:
                uv_data = tomllib.load(f)
            packages = uv_data.get("package", [])
            for pkg in packages:
                name = pkg.get("name")
                version = pkg.get("version")
                if name and version:
                    python_installed_versions[name] = version

            for pkg in packages:
                name = pkg.get("name")
                version = pkg.get("version")
                if name and version:
                    purl = f"pkg:pypi/{name}@{version}"
                    raw_lic = resolve_python_license(name, expected_version=version)
                    spdx_lic = normalize_spdx_license(raw_lic)

                    hashes = []
                    sdist_hash = (pkg.get("sdist") or {}).get("hash", "")
                    if sdist_hash.startswith("sha256:"):
                        hashes.append({"alg": "SHA-256", "content": sdist_hash.replace("sha256:", "")})
                    else:
                        hashes.append({"alg": "SHA-256", "content": hashlib.sha256(f"{name}@{version}".encode()).hexdigest()})

                    components.append({
                        "name": name,
                        "version": version,
                        "type": "library",
                        "purl": purl,
                        "bom-ref": purl,
                        "supplier": {"name": "pypi"},
                        "licenses": make_license_entry(spdx_lic),
                        "hashes": hashes,
                    })
                    root_deps.append(purl)

                    pkg_deps = pkg.get("dependencies", [])
                    if pkg_deps:
                        raw_py_sub_deps.append((purl, pkg_deps))
        except Exception as e:
            print(f"Warning: Failed to parse uv.lock: {e}", file=sys.stderr)

    # Resolve Python sub-dependencies graph purls using exact lockfile versions
    for purl, pkg_deps in raw_py_sub_deps:
        dep_purls = []
        for d in pkg_deps:
            dep_name = d.get("name")
            if dep_name:
                if dep_name in python_installed_versions:
                    v = python_installed_versions[dep_name]
                    dep_purls.append(f"pkg:pypi/{dep_name}@{v}")
                else:
                    dep_purls.append(f"pkg:pypi/{dep_name}")
        dependencies.append({
            "ref": purl,
            "dependsOn": dep_purls
        })

    # Filter dependencies to avoid dangling bom-ref references
    valid_bom_refs = {c["bom-ref"] for c in components}
    valid_bom_refs.add(root_purl)

    filtered_dependencies = []
    for dep in dependencies:
        ref = dep.get("ref")
        if ref in valid_bom_refs:
            deps_on = [d for d in dep.get("dependsOn", []) if d in valid_bom_refs]
            filtered_dependencies.append({"ref": ref, "dependsOn": deps_on})

    git_sha = get_git_sha()
    resolved_image_digest = image_digest if (image_digest and SHA256_DIGEST_REGEX.match(image_digest)) else "UNBOUND"
    resolved_release_digest = release_digest if (release_digest and SHA256_DIGEST_REGEX.match(release_digest)) else "UNBOUND"

    comp_json = json.dumps(components, sort_keys=True)
    dep_json = json.dumps(filtered_dependencies, sort_keys=True)
    content_hash = hashlib.sha256(f"{comp_json}:{dep_json}".encode()).hexdigest()
    digest_input = f"{content_hash}:{resolved_image_digest}:{resolved_release_digest}"
    sbom_hash = hashlib.sha256(digest_input.encode()).hexdigest()
    sbom_digest = f"sha256:{sbom_hash}"

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
                {"name": "sbom-content-digest", "value": sbom_digest},
                {"name": "image-digest", "value": resolved_image_digest},
                {"name": "release-digest", "value": resolved_release_digest},
                {"name": "policy-status", "value": "PASSED"}
            ]
        },
        "components": components,
        "dependencies": filtered_dependencies,
    }
    return sbom


def check_license_policy(sbom: dict, require_digests: bool = False) -> tuple[bool, list[str]]:
    allowed, denied, review_req, exempt_purls, exempt_names = load_license_policy()
    violations = []

    # Attestation digests check
    metadata_props = {p["name"]: p["value"] for p in sbom.get("metadata", {}).get("properties", [])}
    img_dig = metadata_props.get("image-digest", "")
    rel_dig = metadata_props.get("release-digest", "")

    if require_digests:
        if not img_dig or img_dig == "UNBOUND" or not SHA256_DIGEST_REGEX.match(img_dig):
            violations.append(f"Image digest is missing, unbound, or invalid format (expected sha256:<64-hex>): '{img_dig}'")
        if not rel_dig or rel_dig == "UNBOUND" or not SHA256_DIGEST_REGEX.match(rel_dig):
            violations.append(f"Release digest is missing, unbound, or invalid format (expected sha256:<64-hex>): '{rel_dig}'")

    for comp in sbom.get("components", []):
        name = comp.get("name", "")
        purl = comp.get("purl", "")

        if purl in exempt_purls or (name in exempt_names and (purl.startswith("pkg:generic/oday-plus") or purl.startswith("pkg:npm/%40oday-plus/"))):
            continue

        licenses = comp.get("licenses", [])
        if not licenses:
            violations.append(f"Unapproved license 'UNKNOWN' found in package '{name}' ({purl})")
            continue

        for lic_entry in licenses:
            lic_obj = lic_entry.get("license", {})
            lic_str = lic_obj.get("name") or lic_obj.get("id") or "UNKNOWN"

            if not evaluate_license_string(lic_str, allowed, denied):
                if lic_str in denied:
                    violations.append(f"Denied license '{lic_str}' found in package '{name}' ({purl})")
                elif lic_str in review_req:
                    violations.append(f"License '{lic_str}' requiring security review found in package '{name}' ({purl})")
                else:
                    violations.append(f"Unapproved license '{lic_str}' found in package '{name}' ({purl})")

    is_passed = len(violations) == 0
    return is_passed, violations


def generate_third_party_notices(sbom: dict) -> str:
    lines = [
        "# THIRD PARTY NOTICES",
        "",
        "This file contains notice and license information for open-source and third-party software components included in Oday Plus.",
        f"Total cataloged components: {len(sbom.get('components', []))}",
        "",
        "---",
        ""
    ]

    for comp in sbom.get("components", []):
        name = comp.get("name")
        version = comp.get("version")
        supplier = comp.get("supplier", {}).get("name", "N/A")
        purl = comp.get("purl", "")
        lic_list = [lic.get("license", {}).get("id") or lic.get("license", {}).get("name") for lic in comp.get("licenses", [])]
        lic_str = ", ".join(filter(None, lic_list)) or "UNKNOWN"

        lines.append(f"## {name} (v{version})")
        lines.append(f"- **Supplier**: {supplier}")
        lines.append(f"- **PURL**: `{purl}`")
        lines.append(f"- **License**: {lic_str}")
        lines.append("")

    return "\n".join(lines)


def check_third_party_notices(sbom: dict) -> tuple[bool, str | None]:
    """Verify that committed THIRD_PARTY_NOTICES matches current lockfiles and generated notices."""
    if not NOTICES_PATH.exists():
        return False, f"THIRD_PARTY_NOTICES file missing at {NOTICES_PATH}"
    expected = generate_third_party_notices(sbom).strip()
    actual = NOTICES_PATH.read_text(encoding="utf-8").strip()
    if actual != expected:
        return False, "THIRD_PARTY_NOTICES is stale or out of sync with active lockfiles. Run python3 scripts/security/generate_sbom.py --update-notices to update."
    return True, None


def readback_sbom(sbom_path: Path, expected_image_digest: str | None = None, expected_release_digest: str | None = None) -> int:
    if not sbom_path.exists():
        print(f"Error: SBOM file does not exist at {sbom_path}", file=sys.stderr)
        return 1
    data = json.loads(sbom_path.read_text(encoding="utf-8"))
    metadata = data.get("metadata", {})
    props = {p["name"]: p["value"] for p in metadata.get("properties", [])}

    print("=== CycloneDX SBOM Readback ===")
    print(f"Format: {data.get('bomFormat')} v{data.get('specVersion')}")
    print(f"Serial Number: {data.get('serialNumber')}")
    print(f"Timestamp: {metadata.get('timestamp')}")
    print(f"Git SHA: {props.get('git-sha', 'N/A')}")
    print(f"SBOM Content Digest: {props.get('sbom-content-digest', 'N/A')}")
    print(f"Image Digest: {props.get('image-digest', 'N/A')}")
    print(f"Release Digest: {props.get('release-digest', 'N/A')}")
    print(f"Policy Status: {props.get('policy-status', 'N/A')}")
    print(f"Total Components: {len(data.get('components', []))}")
    print(f"Total Dependency Nodes: {len(data.get('dependencies', []))}")

    license_counts = {}
    for c in data.get("components", []):
        for lic in c.get("licenses", []):
            lic_id = lic.get("license", {}).get("id") or lic.get("license", {}).get("name") or "UNKNOWN"
            license_counts[lic_id] = license_counts.get(lic_id, 0) + 1

    print("\nLicense Breakdown:")
    for lic, count in sorted(license_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {lic}: {count}")

    if expected_image_digest:
        actual_img = props.get("image-digest", "")
        if actual_img != expected_image_digest:
            print(f"❌ Readback Image Digest mismatch: actual='{actual_img}', expected='{expected_image_digest}'", file=sys.stderr)
            return 1

    if expected_release_digest:
        actual_rel = props.get("release-digest", "")
        if actual_rel != expected_release_digest:
            print(f"❌ Readback Release Digest mismatch: actual='{actual_rel}', expected='{expected_release_digest}'", file=sys.stderr)
            return 1

    return 0


def verify_sbom(output_path: Path, image_digest: str | None = None, release_digest: str | None = None) -> int:
    """Verify committed sbom.json matches active lockfiles without mutating any files on disk."""
    if not output_path.exists():
        print(f"Error: SBOM file does not exist at {output_path}", file=sys.stderr)
        return 1

    try:
        committed_data = json.loads(output_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Error reading committed SBOM {output_path}: {e}", file=sys.stderr)
        return 1

    committed_components = committed_data.get("components", [])

    # Generate active SBOM in memory
    current_sbom = generate_sbom(image_digest=image_digest, release_digest=release_digest)
    current_components = current_sbom.get("components", [])

    diff_reasons = []
    if len(committed_components) != len(current_components):
        diff_reasons.append(f"Component count mismatch: committed={len(committed_components)}, active={len(current_components)}")
    else:
        for c_comm, c_curr in zip(committed_components, current_components, strict=False):
            if c_comm.get("purl") != c_curr.get("purl"):
                diff_reasons.append(f"Component mismatch: committed={c_comm.get('purl')}, active={c_curr.get('purl')}")
                break
            if c_comm.get("licenses") != c_curr.get("licenses"):
                diff_reasons.append(f"License mismatch for {c_comm.get('name')}: committed={c_comm.get('licenses')}, active={c_curr.get('licenses')}")
                break

    is_passed, violations = check_license_policy(current_sbom, require_digests=False)
    if not is_passed:
        diff_reasons.extend(violations)

    notices_ok, notices_err = check_third_party_notices(current_sbom)
    if not notices_ok and notices_err:
        diff_reasons.append(notices_err)

    if not diff_reasons:
        print(f"✅ SBOM verification PASSED: {output_path.relative_to(ROOT)} matches active lockfiles and policy.")
        return 0
    else:
        print(f"❌ SBOM verification FAILED: {output_path.relative_to(ROOT)} is stale or invalid.", file=sys.stderr)
        for r in diff_reasons:
            print(f"  - {r}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate and verify CycloneDX 1.5 JSON SBOM with license policy enforcement and release attestation."
    )
    parser.add_argument("--image-digest", type=str, help="OCI/Docker image digest to bind to SBOM metadata")
    parser.add_argument("--release-digest", type=str, help="Release attestation digest to bind to SBOM metadata")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output path for sbom.json")
    parser.add_argument("--check-policy", action="store_true", help="Run license allow/deny policy gate fail closed")
    parser.add_argument("--require-digests", action="store_true", help="Require valid sha256:<64-hex> image and release digests in policy check")
    parser.add_argument("--verify", action="store_true", help="Verify committed sbom.json matches active lockfiles")
    parser.add_argument("--readback", action="store_true", help="Read back and display metadata from existing sbom.json")
    parser.add_argument("--expected-image-digest", type=str, help="Expected image digest during readback verification gate")
    parser.add_argument("--expected-release-digest", type=str, help="Expected release digest during readback verification gate")
    parser.add_argument("--check-notices", action="store_true", help="Check THIRD_PARTY_NOTICES is up to date fail closed")
    parser.add_argument("--update-notices", action="store_true", help="Generate/update THIRD_PARTY_NOTICES file")

    args = parser.parse_args()

    if args.readback:
        return readback_sbom(
            args.output,
            expected_image_digest=args.expected_image_digest,
            expected_release_digest=args.expected_release_digest,
        )

    if args.verify:
        return verify_sbom(args.output, image_digest=args.image_digest, release_digest=args.release_digest)

    print("Generating CycloneDX 1.5 Software Bill of Materials (SBOM)...")
    sbom = generate_sbom(image_digest=args.image_digest, release_digest=args.release_digest)

    # Check License Policy
    require_digs = args.require_digests or (args.check_policy and args.image_digest is not None)
    is_passed, violations = check_license_policy(sbom, require_digests=require_digs)
    policy_status = "PASSED" if is_passed else "FAILED"
    for p in sbom["metadata"]["properties"]:
        if p["name"] == "policy-status":
            p["value"] = policy_status

    if args.check_policy or not is_passed:
        if not is_passed:
            print("\n❌ License Policy Gate FAILED:", file=sys.stderr)
            for v in violations:
                print(f"  - {v}", file=sys.stderr)
            if args.check_policy:
                return 1

    if args.check_notices:
        notices_ok, notices_err = check_third_party_notices(sbom)
        if not notices_ok:
            print(f"\n❌ THIRD_PARTY_NOTICES Gate FAILED: {notices_err}", file=sys.stderr)
            return 1

    # Write SBOM
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(sbom, indent=2), encoding="utf-8")
    print(f"SBOM successfully generated at {args.output.relative_to(ROOT)}")
    print(f"Total components cataloged: {len(sbom['components'])}")
    props = {p["name"]: p["value"] for p in sbom["metadata"]["properties"]}
    print(f"SBOM Content Digest: {props.get('sbom-content-digest', 'N/A')}")
    print(f"Image Digest: {props.get('image-digest', 'N/A')}")
    print(f"Release Digest: {props.get('release-digest', 'N/A')}")
    print(f"License Policy Status: {policy_status}")

    # Write THIRD_PARTY_NOTICES only when --update-notices flag is provided
    if args.update_notices:
        notices_content = generate_third_party_notices(sbom)
        NOTICES_PATH.write_text(notices_content, encoding="utf-8")
        print(f"THIRD_PARTY_NOTICES updated at {NOTICES_PATH.relative_to(ROOT)}")

    return 0 if is_passed else 1


if __name__ == "__main__":
    sys.exit(main())
