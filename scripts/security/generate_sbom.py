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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.security.exemption_validator import (
    is_valid_approver,
    validate_exemption_entry,
)

DEFAULT_OUTPUT_DIR = ROOT / "docs/evidence/completion/ODP-PGAP-SUPPLY-001"
DEFAULT_OUTPUT_PATH = DEFAULT_OUTPUT_DIR / "sbom.json"
POLICY_PATH = ROOT / "docs/security/license_policy.json"
EXEMPTIONS_PATH = ROOT / "docs/security/license_exemptions.json"
NOTICES_PATH = ROOT / "THIRD_PARTY_NOTICES"

SHA256_DIGEST_REGEX = re.compile(r"^sha256:[a-fA-F0-9]{64}$")
# Kept for backward-compat import; prefer is_valid_approver() for new checks.
AI_AGENT_PATTERN = re.compile(r"^(Antigravity|Claude|Codex|Gemini|Copilot|GPT|LLM)\d*$", re.IGNORECASE)


def _is_valid_approver(approver: str) -> bool:
    """Validate that the approver is a named human/legal authority."""
    return is_valid_approver(approver)



def is_first_party_purl(purl: str, prefixes: list[str] | tuple[str, ...] | None = None) -> bool:
    """Return True if purl matches a first-party prefix with delimiter anchoring."""
    if not purl:
        return False
    if prefixes is None:
        prefixes = [
            "pkg:generic/oday-plus@",
            "pkg:generic/oday-plus?",
            "pkg:generic/oday-plus/",
            "pkg:npm/%40oday-plus/",
        ]
    for pfx in prefixes:
        if pfx.endswith(("@", "?", "/", "#")):
            if purl.startswith(pfx):
                return True
        else:
            if purl == pfx or any(purl.startswith(pfx + d) for d in ("@", "?", "/", "#")):
                return True
    return False



def safe_rel_path(path: Path) -> Path | str:
    """Safely return relative path to ROOT, or absolute path if outside ROOT."""
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


# Known license fallbacks for PyPI packages where metadata may omit SPDX identifier
PYPI_LICENSE_FALLBACKS = {
    "about-time": "MIT",
    "absl-py": "Apache-2.0",
    "adagio": "Apache-2.0",
    "aiohappyeyeballs": "PSF-2.0",
    "aiohttp": "Apache-2.0 AND MIT",
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

# Complete, deterministic locked license registry for clean environments without .venv
PYPI_LOCKED_LICENSES = {
    "about-time": "MIT",
    "absl-py": "Apache-2.0",
    "adagio": "Apache-2.0",
    "aiohappyeyeballs": "PSF-2.0",
    "aiohttp": "Apache-2.0 AND MIT",
    "aiosignal": "Apache-2.0",
    "alembic": "MIT",
    "alive-progress": "MIT",
    "altair": "BSD-3-Clause",
    "annotated-doc": "MIT",
    "annotated-types": "MIT",
    "antlr4-python3-runtime": "BSD-3-Clause",
    "anyio": "MIT",
    "appdirs": "MIT",
    "attrs": "MIT",
    "autograd": "MIT",
    "autograd-gamma": "MIT",
    "blinker": "MIT",
    "cachetools": "MIT",
    "catboost": "Apache-2.0",
    "certifi": "MPL-2.0",
    "cffi": "MIT-0",
    "charset-normalizer": "MIT",
    "clarabel": "Apache-2.0",
    "click": "BSD-3-Clause",
    "cloudpickle": "BSD-3-Clause",
    "cma": "BSD-3-Clause",
    "colorama": "BSD-3-Clause",
    "coloredlogs": "MIT",
    "colorlog": "MIT",
    "contourpy": "BSD-3-Clause",
    "coreforecast": "Apache-2.0",
    "cryptography": "Apache-2.0 OR BSD-3-Clause",
    "cvxpy": "Apache-2.0",
    "cycler": "BSD-3-Clause",
    "dagster": "Apache-2.0",
    "dagster-pipes": "Apache-2.0",
    "dagster-shared": "Apache-2.0",
    "databricks-sdk": "Apache-2.0",
    "defusedxml": "PSF-2.0",
    "deprecated": "MIT",
    "deprecation": "Apache-2.0",
    "distro": "Apache-2.0",
    "dlt": "Apache-2.0",
    "docker": "Apache-2.0",
    "docstring-parser": "MIT",
    "duckdb": "MIT",
    "dynaconf": "MIT",
    "evidently": "Apache-2.0",
    "faker": "MIT",
    "fastapi": "MIT",
    "fasteners": "Apache-2.0",
    "filelock": "MIT",
    "flask": "BSD-3-Clause",
    "flask-cors": "MIT",
    "fonttools": "MIT",
    "formulaic": "MIT",
    "frozenlist": "Apache-2.0",
    "fsspec": "BSD-3-Clause",
    "fugue": "Apache-2.0",
    "gitdb": "BSD-3-Clause",
    "gitpython": "BSD-3-Clause",
    "giturlparse": "Apache-2.0",
    "google-api-core": "Apache-2.0",
    "google-auth": "Apache-2.0",
    "google-cloud-core": "Apache-2.0",
    "google-cloud-storage": "Apache-2.0",
    "google-crc32c": "Apache-2.0",
    "google-resumable-media": "Apache-2.0",
    "googleapis-common-protos": "Apache-2.0",
    "graphemeu": "MIT",
    "graphene": "MIT",
    "graphql-core": "MIT",
    "graphql-relay": "MIT",
    "graphviz": "MIT",
    "great-expectations": "Apache-2.0",
    "greenlet": "MIT AND PSF-2.0",
    "grpcio": "Apache-2.0",
    "grpcio-health-checking": "Apache-2.0",
    "gunicorn": "MIT",
    "h11": "MIT",
    "h3": "Apache-2.0",
    "highspy": "MIT",
    "httpcore": "BSD-3-Clause",
    "httptools": "MIT",
    "httpx": "BSD-3-Clause",
    "huey": "MIT",
    "humanfriendly": "MIT",
    "humanize": "MIT",
    "idna": "BSD-3-Clause",
    "immutabledict": "MIT",
    "importlib-metadata": "Apache-2.0",
    "iniconfig": "MIT",
    "interface-meta": "MIT",
    "iterative-telemetry": "Apache-2.0",
    "itsdangerous": "BSD-3-Clause",
    "jinja2": "BSD-3-Clause",
    "joblib": "BSD-3-Clause",
    "jsonpath-ng": "Apache-2.0",
    "jsonschema": "MIT",
    "jsonschema-path": "Apache-2.0",
    "jsonschema-specifications": "MIT",
    "kiwisolver": "BSD-3-Clause",
    "lazy-object-proxy": "BSD-2-Clause",
    "lifelines": "MIT",
    "lightgbm": "MIT",
    "litestar": "MIT",
    "litestar-htmx": "MIT",
    "mako": "MIT",
    "markdown-it-py": "MIT",
    "markupsafe": "BSD-3-Clause",
    "marshmallow": "MIT",
    "matplotlib": "PSF-2.0",
    "mdurl": "MIT",
    "mistune": "BSD-3-Clause",
    "mlflow": "Apache-2.0",
    "mlflow-skinny": "Apache-2.0",
    "mlflow-tracing": "Apache-2.0",
    "mlforecast": "Apache-2.0",
    "moocore": "LGPL-2.1-or-later",
    "msgspec": "BSD-3-Clause",
    "multidict": "Apache-2.0",
    "multipart": "MIT",
    "mypy-extensions": "MIT",
    "narwhals": "MIT",
    "nltk": "Apache-2.0",
    "numpy": "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
    "odayplus": "MIT",
    "openapi-schema-validator": "BSD-3-Clause",
    "openapi-spec-validator": "Apache-2.0",
    "opentelemetry-api": "Apache-2.0",
    "opentelemetry-proto": "Apache-2.0",
    "opentelemetry-sdk": "Apache-2.0",
    "opentelemetry-semantic-conventions": "Apache-2.0",
    "optuna": "MIT",
    "orjson": "MPL-2.0 AND (Apache-2.0 OR MIT)",
    "ortools": "Apache-2.0",
    "osqp": "Apache-2.0",
    "packaging": "Apache-2.0 OR BSD-2-Clause",
    "pandas": "BSD-3-Clause",
    "pathable": "Apache-2.0",
    "pathlib-abc": "PSF-2.0",
    "pathvalidate": "MIT",
    "patsy": "BSD-2-Clause",
    "pendulum": "MIT",
    "pgserver": "MIT",
    "pillow": "MIT",
    "platformdirs": "MIT",
    "plotly": "MIT",
    "pluggy": "MIT",
    "polyfactory": "MIT",
    "prettytable": "BSD-3-Clause",
    "propcache": "Apache-2.0",
    "proto-plus": "Apache-2.0",
    "protobuf": "BSD-3-Clause",
    "psutil": "BSD-3-Clause",
    "psycopg": "LGPL-3.0-only",
    "psycopg-binary": "LGPL-3.0-only",
    "psycopg-pool": "LGPL-3.0-only",
    "psycopg2-binary": "LGPL-3.0-or-later",
    "pyarrow": "Apache-2.0",
    "pyasn1": "BSD-2-Clause",
    "pyasn1-modules": "BSD-3-Clause",
    "pycparser": "BSD-3-Clause",
    "pydantic": "MIT",
    "pydantic-core": "MIT",
    "pydantic-settings": "MIT",
    "pygments": "BSD-2-Clause",
    "pymoo": "Apache-2.0",
    "pyomo": "BSD-3-Clause",
    "pyparsing": "MIT",
    "pyreadline3": "BSD-3-Clause",
    "pytest": "MIT",
    "python-dateutil": "Apache-2.0 OR BSD-3-Clause",
    "python-dotenv": "BSD-3-Clause",
    "pytz": "MIT",
    "pywin32": "PSF-2.0",
    "pyyaml": "MIT",
    "qdldl": "Apache-2.0",
    "referencing": "MIT",
    "regex": "Apache-2.0 AND CNRI-Python",
    "requests": "Apache-2.0",
    "requirements-parser": "Apache-2.0",
    "rfc3339-validator": "MIT",
    "rich": "MIT",
    "rich-argparse": "MIT",
    "rich-click": "MIT",
    "rpds-py": "MIT",
    "ruamel-yaml": "MIT",
    "ruff": "MIT",
    "scikit-learn": "BSD-3-Clause",
    "scipy": "BSD-3-Clause",
    "scs": "MIT",
    "semver": "BSD-3-Clause",
    "setuptools": "MIT",
    "shellingham": "ISC",
    "simplejson": "MIT OR AFL-2.1",
    "six": "MIT",
    "skops": "MIT",
    "smmap": "BSD-3-Clause",
    "sniffio": "MIT OR Apache-2.0",
    "sparsediffpy": "Apache-2.0",
    "sqlalchemy": "MIT",
    "sqlglot": "MIT",
    "sqlparse": "BSD-3-Clause",
    "starlette": "BSD-3-Clause",
    "statsforecast": "Apache-2.0",
    "statsmodels": "BSD-3-Clause",
    "structlog": "MIT OR Apache-2.0",
    "tabulate": "MIT",
    "tenacity": "Apache-2.0",
    "threadpoolctl": "BSD-3-Clause",
    "tomli": "MIT",
    "tomlkit": "MIT",
    "toposort": "Apache-2.0",
    "tqdm": "MPL-2.0 AND MIT",
    "triad": "Apache-2.0",
    "typer": "MIT",
    "typing-extensions": "PSF-2.0",
    "typing-inspect": "MIT",
    "typing-inspection": "MIT",
    "tzdata": "Apache-2.0",
    "tzlocal": "MIT",
    "ujson": "BSD-3-Clause AND TCL",
    "universal-pathlib": "MIT",
    "urllib3": "MIT",
    "utilsforecast": "Apache-2.0",
    "uuid6": "MIT",
    "uvicorn": "BSD-3-Clause",
    "uvloop": "MIT",
    "waitress": "ZPL-2.1",
    "watchdog": "Apache-2.0",
    "watchfiles": "MIT",
    "wcwidth": "MIT",
    "websockets": "BSD-3-Clause",
    "werkzeug": "BSD-3-Clause",
    "win-precise-time": "MIT",
    "wrapt": "BSD-2-Clause",
    "yarl": "Apache-2.0",
    "zipp": "MIT",
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
    """Resolve Python package license using locked registry, dist-info metadata, or fallbacks."""
    # 1. Primary check: locked license registry (reproducible anywhere without .venv)
    norm_key = package_name.lower().replace("_", "-")
    if norm_key in PYPI_LOCKED_LICENSES:
        return PYPI_LOCKED_LICENSES[norm_key]
    if package_name in PYPI_LOCKED_LICENSES:
        return PYPI_LOCKED_LICENSES[package_name]

    # 2. Secondary check: .venv dist-info if present
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
                candidates.extend(list(site_pkg.glob(f"{norm_name}-*.dist-info/METADATA")))
                candidates.extend(list(site_pkg.glob(f"{alt_name}-*.dist-info/METADATA")))

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

    # 3. Fallback dict check
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


REQUIRED_LICENSE_EXEMPTION_FIELDS = {
    "status",
    "issued_at",
    "expires_at",
    "approved_by",
    "approval_reference",
    "scope",
    "reason",
}


def load_license_policy(target_scope: str = "prod") -> tuple[set[str], set[str], set[str], set[str], set[str], list[str]]:
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
    ex_violations = []
    if EXEMPTIONS_PATH.exists():
        try:
            ex_data = json.loads(EXEMPTIONS_PATH.read_text(encoding="utf-8"))
            for entry in ex_data.get("exemptions", []):
                status = entry.get("status")
                entry_valid, entry_violations = validate_exemption_entry(entry, exemption_type="license", base_dir=EXEMPTIONS_PATH.parent)
                if entry_violations:
                    ex_violations.extend(entry_violations)

                if status == "active" and entry_valid:
                    scope = entry.get("scope", "all")
                    # Enforce scope: if target_scope is release/prod, dev-scoped exemptions must not suppress findings
                    if target_scope in {"prod", "production", "release"} and scope not in {"prod", "production", "all"}:
                        continue
                    if "purl" in entry:
                        exempt_purls.add(entry["purl"])
                    if "package_name" in entry:
                        exempt_names.add(entry["package_name"])
        except Exception as e:
            raise ValueError(f"Failed to parse license exemptions file {EXEMPTIONS_PATH}: {e}") from e

    return allowed, denied, review_req, exempt_purls, exempt_names, ex_violations



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


def compute_lockfile_hashes() -> tuple[str, str, str, str]:
    """Compute sha256 hashes of package-lock.json, uv.lock, license_policy.json, and vulnerability_exemptions.json."""
    pkg_lock = ROOT / "package-lock.json"
    uv_lock = ROOT / "uv.lock"
    policy_file = ROOT / "docs/security/license_policy.json"
    evidence_file = ROOT / "docs/security/vulnerability_exemptions.json"

    pkg_hash = hashlib.sha256(pkg_lock.read_bytes()).hexdigest() if pkg_lock.exists() else "MISSING"
    uv_hash = hashlib.sha256(uv_lock.read_bytes()).hexdigest() if uv_lock.exists() else "MISSING"
    pol_hash = hashlib.sha256(policy_file.read_bytes()).hexdigest() if policy_file.exists() else "MISSING"
    ev_hash = hashlib.sha256(evidence_file.read_bytes()).hexdigest() if evidence_file.exists() else "MISSING"

    return pkg_hash, uv_hash, pol_hash, ev_hash


def compute_sbom_digest(
    components: list[dict],
    dependencies: list[dict],
    git_sha: str = "unknown",
    package_lock_hash: str = "MISSING",
    uv_lock_hash: str = "MISSING",
    policy_hash: str = "MISSING",
    evidence_report_hash: str = "MISSING",
    image_digest: str = "UNBOUND",
    release_digest: str = "UNBOUND",
) -> tuple[str, str, str]:
    """Compute content_hash, sbom_hash, and sbom_content_digest deterministically."""
    comp_json = json.dumps(components, sort_keys=True)
    dep_json = json.dumps(dependencies, sort_keys=True)
    content_hash = hashlib.sha256(
        f"{comp_json}:{dep_json}:{package_lock_hash}:{uv_lock_hash}:{policy_hash}:{evidence_report_hash}".encode()
    ).hexdigest()
    digest_input = f"{content_hash}:{image_digest}:{release_digest}"
    sbom_hash = hashlib.sha256(digest_input.encode()).hexdigest()
    sbom_digest = f"sha256:{sbom_hash}"
    return content_hash, sbom_hash, sbom_digest


def generate_sbom(image_digest: str | None = None, release_digest: str | None = None) -> dict:
    components = []
    dependencies = []

    # Add Root Component
    # C2: No authentic artifact bytes exist for the root placeholder; omit hashes
    # rather than emit a coordinate-derived digest that falsely claims integrity.
    root_purl = "pkg:generic/oday-plus@0.1.0"
    components.append({
        "name": "oday-plus",
        "version": "0.1.0",
        "type": "application",
        "purl": root_purl,
        "bom-ref": root_purl,
        "supplier": {"name": "oday-plus"},
        "licenses": [{"license": {"id": "MIT"}}],
    })

    root_deps = []
    npm_installed_versions: dict[str, str] = {}
    python_installed_versions: dict[str, str] = {}

    # 1. Parse Node dependencies from package-lock.json and package.json
    lockfile_path = ROOT / "package-lock.json"
    raw_npm_sub_deps = []
    if not lockfile_path.exists():
        raise ValueError(f"Required dependency inventory missing: {safe_rel_path(lockfile_path)}")
    try:
        data = json.loads(lockfile_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to parse package-lock.json: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("package-lock.json missing valid dictionary schema")

    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("package-lock.json missing valid 'packages' object schema")
    non_root_packages = {k: v for k, v in packages.items() if k != "" and isinstance(v, dict)}
    if not non_root_packages:
        raise ValueError("package-lock.json missing required non-root dependency inventory")

    manifest_path = ROOT / "package.json"
    if not manifest_path.exists():
        raise ValueError(f"Required manifest missing: {safe_rel_path(manifest_path)}")
    try:
        mdata = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"package.json missing valid JSON schema: {e}") from e

    if not isinstance(mdata, dict):
        raise ValueError("package.json missing valid dictionary schema")

    deps_dict = mdata.get("dependencies", {})
    dev_deps_dict = mdata.get("devDependencies", {})
    if deps_dict and not isinstance(deps_dict, dict):
        raise ValueError("package.json 'dependencies' field is not an object schema")
    if dev_deps_dict and not isinstance(dev_deps_dict, dict):
        raise ValueError("package.json 'devDependencies' field is not an object schema")

    declared_node_deps = set((deps_dict or {}).keys()) | set((dev_deps_dict or {}).keys())

    if declared_node_deps:
        installed_pkg_names = {
            v.get("name") or k.replace("node_modules/", "")
            for k, v in non_root_packages.items()
        }
        missing_node = declared_node_deps - installed_pkg_names
        if missing_node:
            raise ValueError(f"package-lock.json missing declared dependencies: {sorted(missing_node)}")

    for pkg_path, pkg_info in packages.items():
        if not pkg_path:  # Root workspace
            continue
        if not isinstance(pkg_info, dict):
            raise ValueError(f"package-lock.json entry for '{pkg_path}' is not an object schema")
        pkg_name = pkg_info.get("name") or pkg_path.replace("node_modules/", "")
        version = pkg_info.get("version", "0.1.0")
        npm_installed_versions[pkg_name] = version

        if pkg_info.get("link"):
            # Local workspace package: no published artifact bytes; omit hashes (C2)
            purl = f"pkg:npm/{pkg_name.replace('@', '%40')}@{version}"
            components.append({
                "name": pkg_name,
                "version": version,
                "type": "library",
                "purl": purl,
                "bom-ref": purl,
                "supplier": {"name": "npm"},
                "licenses": [{"license": {"id": "MIT"}}],
            })
            root_deps.append(purl)
            continue

        raw_lic = pkg_info.get("license") or "UNKNOWN"
        spdx_lic = normalize_spdx_license(raw_lic)

        purl = f"pkg:npm/{pkg_name.replace('@', '%40')}@{version}"

        integrity = pkg_info.get("integrity", "")
        hashes = []
        if isinstance(integrity, str):
            if integrity.startswith("sha512-"):
                hashes.append({"alg": "SHA-512", "content": integrity.replace("sha512-", "")})
            elif integrity.startswith("sha256-"):
                hashes.append({"alg": "SHA-256", "content": integrity.replace("sha256-", "")})

        component_obj: dict = {
            "name": pkg_name,
            "version": version,
            "type": "library",
            "purl": purl,
            "bom-ref": purl,
            "supplier": {"name": "npm"},
            "licenses": make_license_entry(spdx_lic),
        }
        if hashes:
            component_obj["hashes"] = hashes
        components.append(component_obj)
        root_deps.append(purl)

        sub_deps = pkg_info.get("dependencies", {})
        if sub_deps and isinstance(sub_deps, dict):
            raw_npm_sub_deps.append((purl, sub_deps))

    # Resolve npm sub-dependencies graph purls using exact lockfile versions
    for purl, sub_deps in raw_npm_sub_deps:
        dep_purls = []
        for dep_k, dep_v in sub_deps.items():
            if not isinstance(dep_v, str):
                continue
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

    # 2. Parse Python dependencies from uv.lock and pyproject.toml
    uv_lock_path = ROOT / "uv.lock"
    raw_py_sub_deps = []
    if not uv_lock_path.exists():
        raise ValueError(f"Required dependency inventory missing: {safe_rel_path(uv_lock_path)}")
    try:
        with open(uv_lock_path, "rb") as f:
            uv_data = tomllib.load(f)
    except Exception as e:
        raise ValueError(f"Failed to parse uv.lock: {e}") from e

    if not isinstance(uv_data, dict):
        raise ValueError("uv.lock missing valid dictionary schema")

    packages = uv_data.get("package")
    if not isinstance(packages, list):
        raise ValueError("uv.lock missing valid 'package' list schema")
    if not packages:
        raise ValueError("uv.lock missing required non-root dependency inventory")

    pyproject_path = ROOT / "pyproject.toml"
    if not pyproject_path.exists():
        raise ValueError(f"Required manifest missing: {safe_rel_path(pyproject_path)}")
    try:
        with open(pyproject_path, "rb") as f:
            pdata = tomllib.load(f)
    except Exception as e:
        raise ValueError(f"pyproject.toml missing valid TOML schema: {e}") from e

    if not isinstance(pdata, dict):
        raise ValueError("pyproject.toml missing valid dictionary schema")

    declared_py = set()
    proj_section = pdata.get("project")
    if proj_section is not None:
        if not isinstance(proj_section, dict):
            raise ValueError("pyproject.toml 'project' section is not a table")
        p_deps = proj_section.get("dependencies", [])
        if p_deps:
            if not isinstance(p_deps, list):
                raise ValueError("pyproject.toml 'project.dependencies' is not an array")
            for dep in p_deps:
                if not isinstance(dep, str):
                    raise ValueError("pyproject.toml dependency item is not a string")
                m = re.match(r"^([a-zA-Z0-9_\-\.]+)", dep)
                if m:
                    declared_py.add(m.group(1).lower().replace("_", "-"))

        opt_deps = proj_section.get("optional-dependencies", {})
        if opt_deps:
            if not isinstance(opt_deps, dict):
                raise ValueError("pyproject.toml 'project.optional-dependencies' is not a table")
            for group_name, group_list in opt_deps.items():
                if not isinstance(group_list, list):
                    raise ValueError(f"pyproject.toml 'optional-dependencies.{group_name}' is not an array")
                for dep in group_list:
                    if not isinstance(dep, str):
                        raise ValueError("pyproject.toml dependency item is not a string")
                    m = re.match(r"^([a-zA-Z0-9_\-\.]+)", dep)
                    if m:
                        declared_py.add(m.group(1).lower().replace("_", "-"))

    dep_groups = pdata.get("dependency-groups", {})
    if dep_groups:
        if not isinstance(dep_groups, dict):
            raise ValueError("pyproject.toml 'dependency-groups' is not a table")
        for group_name, group_list in dep_groups.items():
            if not isinstance(group_list, list):
                raise ValueError(f"pyproject.toml 'dependency-groups.{group_name}' is not an array")
            for dep in group_list:
                if not isinstance(dep, str):
                    raise ValueError("pyproject.toml dependency item is not a string")
                m = re.match(r"^([a-zA-Z0-9_\-\.]+)", dep)
                if m:
                    declared_py.add(m.group(1).lower().replace("_", "-"))

    if declared_py:
        installed_py = set()
        for p in packages:
            if not isinstance(p, dict):
                raise ValueError("uv.lock package item is not a table")
            pname = p.get("name")
            if pname and isinstance(pname, str):
                installed_py.add(pname.lower().replace("_", "-"))
        missing_py = declared_py - installed_py
        if missing_py:
            raise ValueError(f"uv.lock missing declared dependencies: {sorted(missing_py)}")

    for pkg in packages:
        if not isinstance(pkg, dict):
            raise ValueError("uv.lock package entry is not a table schema")
        name = pkg.get("name")
        version = pkg.get("version")
        if name and version and isinstance(name, str) and isinstance(version, str):
            python_installed_versions[name] = version

    for pkg in packages:
        name = pkg.get("name")
        version = pkg.get("version")
        if name and version and isinstance(name, str) and isinstance(version, str):
            purl = f"pkg:pypi/{name}@{version}"
            raw_lic = resolve_python_license(name, expected_version=version)
            spdx_lic = normalize_spdx_license(raw_lic)

            hashes = []
            sdist_info = pkg.get("sdist")
            sdist_hash = (sdist_info.get("hash", "") if isinstance(sdist_info, dict) else "")
            wheels = pkg.get("wheels")
            wheel_hash = (wheels[0].get("hash", "") if isinstance(wheels, list) and wheels and isinstance(wheels[0], dict) else "")

            target_hash = sdist_hash or wheel_hash
            if isinstance(target_hash, str):
                if target_hash.startswith("sha256:"):
                    hashes.append({"alg": "SHA-256", "content": target_hash.replace("sha256:", "")})
                elif target_hash.startswith("sha512:"):
                    hashes.append({"alg": "SHA-512", "content": target_hash.replace("sha512:", "")})

            py_component: dict = {
                "name": name,
                "version": version,
                "type": "library",
                "purl": purl,
                "bom-ref": purl,
                "supplier": {"name": "pypi"},
                "licenses": make_license_entry(spdx_lic),
            }
            if hashes:
                py_component["hashes"] = hashes
            components.append(py_component)
            root_deps.append(purl)

            pkg_deps = pkg.get("dependencies", [])
            if pkg_deps and isinstance(pkg_deps, list):
                raw_py_sub_deps.append((purl, pkg_deps))

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

    non_root_components = [c for c in components if c.get("bom-ref") != root_purl]
    if not non_root_components:
        raise ValueError("Generated SBOM contains no non-root dependency components; inventory incomplete")

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
    pkg_lock_hash, uv_lock_hash, policy_hash, evidence_hash = compute_lockfile_hashes()
    resolved_image_digest = image_digest if (image_digest and SHA256_DIGEST_REGEX.match(image_digest)) else "UNBOUND"
    resolved_release_digest = release_digest if (release_digest and SHA256_DIGEST_REGEX.match(release_digest)) else "UNBOUND"

    content_hash, sbom_hash, sbom_digest = compute_sbom_digest(
        components,
        filtered_dependencies,
        git_sha=git_sha,
        package_lock_hash=pkg_lock_hash,
        uv_lock_hash=uv_lock_hash,
        policy_hash=policy_hash,
        evidence_report_hash=evidence_hash,
        image_digest=resolved_image_digest,
        release_digest=resolved_release_digest,
    )

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
                {"name": "package-lock-hash", "value": pkg_lock_hash},
                {"name": "uv-lock-hash", "value": uv_lock_hash},
                {"name": "policy-hash", "value": policy_hash},
                {"name": "evidence-report-hash", "value": evidence_hash},
                {"name": "sbom-hash", "value": sbom_hash},
                {"name": "sbom-content-digest", "value": sbom_digest},
                {"name": "image-digest", "value": resolved_image_digest},
                {"name": "release-digest", "value": resolved_release_digest},
                {"name": "policy-status", "value": "PASSED"},
            ]
        },
        "components": components,
        "dependencies": filtered_dependencies,
    }
    is_passed, _ = check_license_policy(sbom, require_digests=False)
    for p in sbom["metadata"]["properties"]:
        if p["name"] == "policy-status":
            p["value"] = "PASSED" if is_passed else "FAILED"
    return sbom


def check_license_policy(sbom: dict, require_digests: bool = False, scope: str = "prod") -> tuple[bool, list[str]]:
    allowed, denied, review_req, exempt_purls, exempt_names, ex_violations = load_license_policy(target_scope=scope)

    violations = list(ex_violations)

    # Attestation digests check
    metadata_props = {p["name"]: p["value"] for p in sbom.get("metadata", {}).get("properties", [])}
    img_dig = metadata_props.get("image-digest", "")
    rel_dig = metadata_props.get("release-digest", "")

    if require_digests:
        if not img_dig or img_dig == "UNBOUND" or not SHA256_DIGEST_REGEX.match(img_dig):
            violations.append(f"Image digest is missing, unbound, or invalid format (expected sha256:<64-hex>): '{img_dig}'")
        if not rel_dig or rel_dig == "UNBOUND" or not SHA256_DIGEST_REGEX.match(rel_dig):
            violations.append(f"Release digest is missing, unbound, or invalid format (expected sha256:<64-hex>): '{rel_dig}'")

    policy_data = {}
    if POLICY_PATH.exists():
        try:
            policy_data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    first_party_prefixes = policy_data.get("first_party_purl_prefixes") or [
        "pkg:generic/oday-plus@",
        "pkg:generic/oday-plus?",
        "pkg:generic/oday-plus/",
        "pkg:npm/%40oday-plus/",
    ]

    for comp in sbom.get("components", []):
        name = comp.get("name", "")
        purl = comp.get("purl", "")

        # First-party packages are recognized directly via policy rule
        if is_first_party_purl(purl, first_party_prefixes):
            continue

        purl_exempted = purl in exempt_purls
        name_exempted = name in exempt_names
        if purl_exempted or name_exempted:
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


def readback_sbom(
    sbom_path: Path,
    expected_image_digest: str | None = None,
    expected_release_digest: str | None = None,
    expected_git_sha: str | None = None,
    expected_package_lock_hash: str | None = None,
    expected_uv_lock_hash: str | None = None,
    expected_policy_hash: str | None = None,
    expected_evidence_report_hash: str | None = None,
) -> int:
    if not sbom_path.exists():
        print(f"Error: SBOM file does not exist at {safe_rel_path(sbom_path)}", file=sys.stderr)
        return 1
    data = json.loads(sbom_path.read_text(encoding="utf-8"))
    metadata = data.get("metadata", {})
    props = {p["name"]: p["value"] for p in metadata.get("properties", [])}

    print("=== CycloneDX SBOM Readback ===")
    print(f"Format: {data.get('bomFormat')} v{data.get('specVersion')}")
    print(f"Serial Number: {data.get('serialNumber')}")
    print(f"Timestamp: {metadata.get('timestamp')}")
    print(f"Git SHA: {props.get('git-sha', 'N/A')}")
    print(f"Package Lock Hash: {props.get('package-lock-hash', 'N/A')}")
    print(f"UV Lock Hash: {props.get('uv-lock-hash', 'N/A')}")
    print(f"Policy Hash: {props.get('policy-hash', 'N/A')}")
    print(f"Evidence Report Hash: {props.get('evidence-report-hash', 'N/A')}")
    print(f"SBOM Content Digest: {props.get('sbom-content-digest', 'N/A')}")
    print(f"Image Digest: {props.get('image-digest', 'N/A')}")
    print(f"Release Digest: {props.get('release-digest', 'N/A')}")
    print(f"Policy Status: {props.get('policy-status', 'N/A')}")
    print(f"Technical Inventory Status: {props.get('policy-status', 'N/A')}")
    img_d = props.get("image-digest", "")
    rel_d = props.get("release-digest", "")
    rel_att_status = "PASSED" if (img_d.startswith("sha256:") and rel_d.startswith("sha256:") and props.get("policy-status") == "PASSED") else "NO-GO (digests UNBOUND or policy FAILED)"
    print(f"Release Attestation Status: {rel_att_status}")
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

    mismatches = []
    checks = [
        ("git-sha", expected_git_sha),
        ("package-lock-hash", expected_package_lock_hash),
        ("uv-lock-hash", expected_uv_lock_hash),
        ("policy-hash", expected_policy_hash),
        ("evidence-report-hash", expected_evidence_report_hash),
        ("image-digest", expected_image_digest),
        ("release-digest", expected_release_digest),
    ]
    for prop_key, expected in checks:
        if expected:
            actual = props.get(prop_key, "")
            if actual != expected:
                mismatches.append(f"Readback {prop_key} mismatch: actual='{actual}', expected='{expected}'")

    if mismatches:
        for m in mismatches:
            print(f"❌ {m}", file=sys.stderr)
        return 1

    return 0


def verify_sbom(
    output_path: Path,
    image_digest: str | None = None,
    release_digest: str | None = None,
    expected_git_sha: str | None = None,
) -> int:
    """Verify committed sbom.json matches active lockfiles without mutating any files on disk."""
    if not output_path.exists():
        print(f"Error: SBOM file does not exist at {safe_rel_path(output_path)}", file=sys.stderr)
        return 1

    try:
        committed_data = json.loads(output_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Error reading committed SBOM {safe_rel_path(output_path)}: {e}", file=sys.stderr)
        return 1

    diff_reasons = []
    if committed_data.get("bomFormat") != "CycloneDX" or committed_data.get("specVersion") != "1.5":
        diff_reasons.append(f"Format mismatch: bomFormat={committed_data.get('bomFormat')}, specVersion={committed_data.get('specVersion')}")

    committed_components = committed_data.get("components", [])
    committed_deps = committed_data.get("dependencies", [])

    # Generate active SBOM in memory
    current_sbom = generate_sbom(image_digest=image_digest, release_digest=release_digest)
    current_components = current_sbom.get("components", [])
    current_deps = current_sbom.get("dependencies", [])

    if len(committed_components) != len(current_components):
        diff_reasons.append(f"Component count mismatch: committed={len(committed_components)}, active={len(current_components)}")
    else:
        for c_comm, c_curr in zip(committed_components, current_components, strict=False):
            if c_comm.get("purl") != c_curr.get("purl"):
                diff_reasons.append(f"Component mismatch: committed={c_comm.get('purl')}, active={c_curr.get('purl')}")
                break
            if c_comm.get("supplier") != c_curr.get("supplier"):
                diff_reasons.append(f"Supplier mismatch for {c_comm.get('name')}: committed={c_comm.get('supplier')}, active={c_curr.get('supplier')}")
                break
            if c_comm.get("licenses") != c_curr.get("licenses"):
                diff_reasons.append(f"License mismatch for {c_comm.get('name')}: committed={c_comm.get('licenses')}, active={c_curr.get('licenses')}")
                break
            if c_comm.get("hashes") != c_curr.get("hashes"):
                diff_reasons.append(f"Package hash mismatch for {c_comm.get('name')}: committed={c_comm.get('hashes')}, active={c_curr.get('hashes')}")
                break

    if len(committed_deps) != len(current_deps):
        diff_reasons.append(f"Dependency graph node count mismatch: committed={len(committed_deps)}, active={len(current_deps)}")
    else:
        for d_comm, d_curr in zip(committed_deps, current_deps, strict=False):
            if d_comm.get("ref") != d_curr.get("ref") or d_comm.get("dependsOn") != d_curr.get("dependsOn"):
                diff_reasons.append(f"Dependency graph tampering detected at node '{d_comm.get('ref')}'")
                break

    # Verify content digest integrity and exact bound properties
    comm_props = {p["name"]: p["value"] for p in committed_data.get("metadata", {}).get("properties", [])}
    curr_props = {p["name"]: p["value"] for p in current_sbom.get("metadata", {}).get("properties", [])}

    for prop_key in [
        "git-sha",
        "package-lock-hash",
        "uv-lock-hash",
        "policy-hash",
        "evidence-report-hash",
        "sbom-content-digest",
        "image-digest",
        "release-digest",
        "policy-status",
    ]:
        comm_val = comm_props.get(prop_key, "")
        curr_val = curr_props.get(prop_key, "")
        if comm_val != curr_val:
            if prop_key == "git-sha" and comm_val and curr_val and comm_val != "unknown" and curr_val != "unknown":
                is_ancestor = False
                try:
                    res = subprocess.run(["git", "merge-base", "--is-ancestor", comm_val, curr_val], capture_output=True)
                    if res.returncode == 0:
                        is_ancestor = True
                except Exception:
                    pass
                if is_ancestor:
                    continue
            diff_reasons.append(f"Property binding mismatch for '{prop_key}': committed='{comm_val}', active='{curr_val}'")

    if expected_git_sha and comm_props.get("git-sha") != expected_git_sha:
        diff_reasons.append(f"Expected git-sha mismatch: committed='{comm_props.get('git-sha')}', expected='{expected_git_sha}'")

    notices_ok, notices_err = check_third_party_notices(current_sbom)
    if not notices_ok and notices_err:
        diff_reasons.append(notices_err)

    if not diff_reasons:
        print(f"✅ SBOM verification PASSED: {safe_rel_path(output_path)} matches active lockfiles and policy.")
        return 0
    else:
        print(f"❌ SBOM verification FAILED: {safe_rel_path(output_path)} is stale or invalid.", file=sys.stderr)
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
    parser.add_argument("--expected-git-sha", type=str, help="Expected git SHA during readback verification gate")
    parser.add_argument("--expected-package-lock-hash", type=str, help="Expected package-lock.json hash during readback verification gate")
    parser.add_argument("--expected-uv-lock-hash", type=str, help="Expected uv.lock hash during readback verification gate")
    parser.add_argument("--expected-policy-hash", type=str, help="Expected license_policy.json hash during readback verification gate")
    parser.add_argument("--expected-evidence-report-hash", type=str, help="Expected vulnerability_exemptions.json hash during readback verification gate")
    parser.add_argument("--check-notices", action="store_true", help="Check THIRD_PARTY_NOTICES is up to date fail closed")
    parser.add_argument("--update-notices", action="store_true", help="Generate/update THIRD_PARTY_NOTICES file")

    args = parser.parse_args()

    if args.readback:
        return readback_sbom(
            args.output,
            expected_image_digest=args.expected_image_digest,
            expected_release_digest=args.expected_release_digest,
            expected_git_sha=args.expected_git_sha,
            expected_package_lock_hash=args.expected_package_lock_hash,
            expected_uv_lock_hash=args.expected_uv_lock_hash,
            expected_policy_hash=args.expected_policy_hash,
            expected_evidence_report_hash=args.expected_evidence_report_hash,
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
    print(f"SBOM successfully generated at {safe_rel_path(args.output)}")
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
        print(f"THIRD_PARTY_NOTICES updated at {safe_rel_path(NOTICES_PATH)}")

    if args.check_policy and not is_passed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
