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
import hashlib
import importlib.metadata as md
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = ROOT / "NOTICE-THIRD-PARTY.md"
POLICY_PATH = ROOT / "docs/security/license_policy.json"
EXEMPTIONS_PATH = ROOT / "docs/security/license_exemptions.json"
NODE_MODULES = ROOT / "node_modules"
UV_LOCK = ROOT / "uv.lock"
PACKAGE_LOCK = ROOT / "package-lock.json"

# Workspace packages are ours: the `@oday-plus` scope plus the monorepo root
# package. They declare `UNLICENSED` so a scanner can tell them apart from a
# third party whose licence is genuinely unknown.
#
# Membership is a scope test and an exact-name test, never a bare string
# prefix. A prefix also matches an unrelated registry package called
# `oday-plus-anything`, which would drop a third party of unknown licence out
# of both this notice and the policy gate that reads from it.
FIRST_PARTY_SCOPE = "@oday-plus/"
FIRST_PARTY_ROOT_NAMES = frozenset({"oday-plus"})


def is_first_party(name: str) -> bool:
    """True only for our own packages: the @oday-plus scope or the root name."""
    return name.startswith(FIRST_PARTY_SCOPE) or name in FIRST_PARTY_ROOT_NAMES

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
    # These distributions publish only the ambiguous BSD label. Resolve the
    # package-specific licence here rather than weakening the policy with a
    # non-SPDX `BSD` allow-list entry.
    "antlr4-python3-runtime": "BSD-3-Clause",
    "google-crc32c": "Apache-2.0",
    "graphemeu": "Python-2.0",
    "huey": "MIT",
    "pgserver": "MIT",
    "pyasn1-modules": "BSD-2-Clause",
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
                if not is_first_party(name):
                    licence = _normalise_license(
                        data.get("license") or data.get("licenses")
                    )
                    version = str(data.get("version") or "")
                    found[(name, version)] = Component(
                        "npm", name, version, licence or "UNKNOWN"
                    )
            walk(Path(entry.path) / "node_modules")

    walk(base)

    if PACKAGE_LOCK.exists():
        try:
            lock_data = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(
                "Unable to read package-lock.json for install completeness check"
            ) from exc

        lock_packages = lock_data.get("packages")
        if not isinstance(lock_packages, dict):
            raise RuntimeError("package-lock.json has no valid packages map")

        found_names = {name for name, _ in found}
        missing = set()
        for pkg_path, info in lock_packages.items():
            if not pkg_path:
                continue
            if not isinstance(info, dict):
                raise RuntimeError(f"package-lock.json has invalid package entry: {pkg_path}")
            if pkg_path.startswith("node_modules/"):
                pkg_name = pkg_path.split("node_modules/")[-1]
                is_optional = info.get("optional", False)
                if not is_first_party(pkg_name):
                    if pkg_name not in found_names and not is_optional:
                        missing.add(pkg_name)
        if missing:
            raise RuntimeError(
                "Partial install detected. Missing npm packages declared in lockfile: "
                f"{missing}"
            )

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

    # Some packages publish only the ambiguous `BSD` label. Prefer a
    # package-specific, SPDX-qualified fallback for those known cases before
    # generic classifiers can collapse it to the wrong BSD variant.
    known_fallback = PYTHON_KNOWN_FALLBACKS.get(norm_name)
    raw_license = str(meta.get("License") or "").strip()
    if known_fallback and (not raw_license or raw_license.upper() in {"BSD", "UNKNOWN"}):
        return known_fallback

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
    found_names = set()
    for dist in md.distributions():
        try:
            meta = dist.metadata
            name = str(meta.get("Name") or "")
            if not name:
                continue
            norm_name = re.sub(r"[-_.]+", "-", name).lower()
            if norm_name not in declared:
                continue
            licence = _get_python_license_from_dist(dist, name)
            found[(name, dist.version or "")] = Component(
                "pypi", name, dist.version or "", licence
            )
            found_names.add(norm_name)
        except Exception:  # pragma: no cover
            continue
            
    missing = declared - found_names
    missing = {m for m in missing if m not in {"odayplus", "win-precise-time", "pyreadline3", "pywin32", "waitress", "colorama"}}
    if missing:
        raise RuntimeError(f"Partial install detected. Missing python packages declared in lockfile: {missing}")
        
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


def _combine_or(classes: list[str]) -> str:
    """Apply the policy's any-allowed-disjunct rule to an OR expression."""
    if "allow" in classes:
        return "allow"
    if "allow_with_obligations" in classes:
        return "allow_with_obligations"
    if "deny" in classes:
        return "deny"
    if "review_required" in classes:
        return "review_required"
    if "unknown" in classes:
        return "unknown"
    return "deny"


def _combine_and(classes: list[str]) -> str:
    """Apply the policy's most-restrictive-conjunct rule to an AND expression."""
    precedence = {
        "allow": 0,
        "allow_with_obligations": 1,
        "unknown": 2,
        "review_required": 3,
        "deny": 4,
    }
    return max(classes, key=precedence.__getitem__)


class _SpdxExpressionParser:
    """Small fail-closed parser for the AND/OR/parenthesis subset we use."""

    def __init__(self, expression: str, classify: Any) -> None:
        self.tokens = re.findall(r"\(|\)|\bAND\b|\bOR\b|[^()\s]+", expression)
        self.position = 0
        self.classify = classify

    def parse(self) -> str:
        if not self.tokens:
            raise ValueError("empty expression")
        result = self._parse_or()
        if self.position != len(self.tokens):
            raise ValueError("trailing tokens")
        return result

    def _accept(self, token: str) -> bool:
        if self.position < len(self.tokens) and self.tokens[self.position] == token:
            self.position += 1
            return True
        return False

    def _parse_or(self) -> str:
        classes = [self._parse_and()]
        while self._accept("OR"):
            classes.append(self._parse_and())
        return _combine_or(classes) if len(classes) > 1 else classes[0]

    def _parse_and(self) -> str:
        classes = [self._parse_primary()]
        while self._accept("AND"):
            classes.append(self._parse_primary())
        return _combine_and(classes) if len(classes) > 1 else classes[0]

    def _parse_primary(self) -> str:
        if self._accept("("):
            result = self._parse_or()
            if not self._accept(")"):
                raise ValueError("unclosed parenthesis")
            return result

        if self.position >= len(self.tokens):
            raise ValueError("missing term")
        token = self.tokens[self.position]
        if token in {"AND", "OR", ")"}:
            raise ValueError("unexpected operator")
        self.position += 1
        return self.classify(token)


def evaluate_compound_expression(
    lic: str,
    allowed_ids: set[str],
    allowed_with_obligations_ids: set[str],
    review_case_licenses: set[str],
    deny_ids: set[str],
) -> str:
    """Evaluate compound SPDX expression using policy precedence order."""
    lic = lic.strip()

    def classify(term: str) -> str:
        return _classify_single_term(
            term,
            allowed_ids,
            allowed_with_obligations_ids,
            review_case_licenses,
            deny_ids,
        )

    # Preserve non-SPDX descriptive labels such as "LGPL with exceptions" as
    # one policy term. Compound expressions use the parser so parentheses are
    # never discarded before classification.
    if not re.search(r"\b(?:AND|OR)\b|[()]", lic):
        return classify(lic)

    try:
        return _SpdxExpressionParser(lic, classify).parse()
    except ValueError:
        return "unknown"


RELEASE_BINDINGS_PATH = ROOT / "docs/security/release_bindings.json"

# The binding an exemption must carry, per docs/security/license_exemptions.json
# rules.required_binding. A missing field fails closed before anything else is
# looked at.
EXEMPTION_REQUIRED_FIELDS = (
    "package",
    "purl",
    "license_or_finding",
    "scope",
    "applicable_releases",
    "rationale",
)
EXEMPTION_SCOPE_VALUES = frozenset({"prod", "dev"})
EXEMPTION_INTEGRITY_ALGORITHMS = frozenset({"sha256", "SHA-256"})
EXEMPTION_INVALID_APPROVER_NAMES = frozenset(
    {
        "Antigravity",
        "Antigravity2",
        "Antigravity3",
        "Claude",
        "Claude2",
        "Codex",
        "Gemini",
        "Copilot",
        "Human/Ops",
        "Legal",
        "Jane Doe",
        "John Doe",
    }
)
# A source system that is the repository itself offers no external readback:
# the register would be vouching for itself, and a self-calculated hash over
# repository-local JSON proves integrity, never authority
# (receipt_requirements.never_acceptable). Names of that shape are rejected
# before any hash is examined.
_REPOSITORY_LOCAL_SOURCE = re.compile(
    r"repo(?:sitory)?[-_ ]?local|^(?:local|repo|repository|git|filesystem|none|n/a)$",
    re.IGNORECASE,
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def component_purl(component: Component) -> str:
    """The purl the SBOM generator mints for a component: pkg:<ecosystem>/<name>@<version>."""
    return f"pkg:{component.ecosystem}/{component.name}@{component.version}"


def exemption_content_sha256(entry: dict[str, Any]) -> str:
    """Canonical content digest of an exemption receipt, excluding its integrity block.

    Mirrors attestation.py so an exemption is sealed and read back the same way
    the gate attestation is: sort_keys JSON over every field except ``integrity``.
    """
    payload = {key: value for key, value in entry.items() if key != "integrity"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def load_release_digest(path: Path | None = None) -> str | None:
    """The pinned odayplus release digest from docs/security/release_bindings.json.

    This is the same release the SBOM and the attestation bind to; a task
    checkout's HEAD is not a release. Returns None when the binding cannot be
    read so that callers fail closed instead of matching against nothing.
    """
    bindings_path = path or RELEASE_BINDINGS_PATH
    try:
        bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    repositories = bindings.get("repositories") if isinstance(bindings, dict) else None
    record = repositories.get("alfloop-dev/odayplus") if isinstance(repositories, dict) else None
    digest = str(record.get("digest") or "").strip().lower() if isinstance(record, dict) else ""
    return digest if re.fullmatch(r"[0-9a-f]{40}", digest) else None


def _parse_utc_timestamp(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp; None unless it is timezone-aware and UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed


def validate_exemption(
    exemption: dict[str, Any],
    component: Component,
    lic: str,
    *,
    review_cases: dict[str, dict[str, Any]],
    release_digest: str | None,
    now: datetime | None = None,
) -> str | None:
    """Return None when the exemption discharges this component, else why it does not.

    Every check applies one of docs/security/license_exemptions.json
    ``rules.fail_closed_on`` or ``receipt_requirements.never_acceptable`` items
    to the exact installed component:

    - binding: package, license, and the purl the SBOM mints for the installed
      version (an exemption for psycopg@3.3.4 does not cover psycopg@3.3.5);
    - adjudication: ``policy_case_id`` must name a review_required case in
      license_policy.json that carries this license and scope and lists this
      package -- a package nobody adjudicated cannot be exempted by analogy;
    - release: ``applicable_releases`` must contain the pinned release digest;
    - time: ``issued_at`` UTC and not in the future, ``expires_at`` later and
      not yet passed;
    - approver: a named principal, never an AI agent or a bare role;
    - receipt: an external ``source_system`` and non-empty
      ``approval_reference``, ``evidence_hashes`` as sha256 digests, and an
      ``integrity.content_sha256`` that matches the entry it seals.

    Reading the approval back from ``source_system`` cannot be done offline and
    stays a human/ops gate. Everything that can be checked without it is
    checked here, so an entry that fails here is never honoured by the gate.
    """
    now = now or datetime.now(UTC)
    if not isinstance(exemption, dict):
        return "exemption is not an object"
    missing = [field for field in EXEMPTION_REQUIRED_FIELDS if field not in exemption]
    if missing:
        return f"missing required binding field(s): {', '.join(missing)}"
    if exemption.get("package") != component.name:
        return (
            f"package mismatch: bound to {exemption.get('package')!r}, "
            f"component is {component.name!r}"
        )
    if exemption.get("license_or_finding") != lic:
        return (
            f"license mismatch: bound to {exemption.get('license_or_finding')!r}, "
            f"component declares {lic!r}"
        )
    expected_purl = component_purl(component)
    if exemption.get("purl") != expected_purl:
        return (
            f"purl mismatch: bound to {exemption.get('purl')!r}, "
            f"installed component is {expected_purl!r}"
        )
    scope = exemption.get("scope")
    if scope not in EXEMPTION_SCOPE_VALUES:
        return f"scope {scope!r} is not one of {sorted(EXEMPTION_SCOPE_VALUES)}"

    case_id = exemption.get("policy_case_id")
    case = review_cases.get(str(case_id or ""))
    if case is None:
        return (
            f"policy_case_id {case_id!r} does not name a review_required case "
            "in license_policy.json"
        )
    if case.get("license") != lic:
        return f"case {case_id} adjudicates {case.get('license')!r}, not {lic!r}"
    if case.get("scope") != scope:
        return (
            f"scope mismatch: case {case_id} is scoped {case.get('scope')!r}, "
            f"exemption claims {scope!r}"
        )
    case_packages = {
        str(entry.get("package"))
        for entry in case.get("packages", [])
        if isinstance(entry, dict)
    }
    if component.name not in case_packages:
        return f"{component.name} is not among the packages adjudicated under case {case_id}"

    releases = exemption.get("applicable_releases")
    if (
        not isinstance(releases, list)
        or not releases
        or not all(isinstance(item, str) and item.strip() for item in releases)
    ):
        return "applicable_releases must be a non-empty list of release digests"
    if release_digest is None:
        return "release digest unresolvable from docs/security/release_bindings.json"
    if release_digest not in {item.strip().lower() for item in releases}:
        return f"release mismatch: pinned release {release_digest} is not in applicable_releases"

    issued_at = _parse_utc_timestamp(exemption.get("issued_at"))
    if issued_at is None:
        return "issued_at must be a UTC timestamp"
    if issued_at > now:
        return "issued_at is in the future"
    expires_at = _parse_utc_timestamp(exemption.get("expires_at"))
    if expires_at is None:
        return "expires_at must be a UTC timestamp"
    if expires_at <= issued_at:
        return "expires_at must be later than issued_at"
    if expires_at < now:
        return f"exemption expired at {exemption.get('expires_at')}"

    approver = exemption.get("approved_by")
    if not isinstance(approver, dict):
        return "approved_by must name a principal"
    principal = str(approver.get("principal_id") or "").strip()
    name = str(approver.get("display_name") or "").strip()
    role = str(approver.get("role") or "")
    if not principal or name in EXEMPTION_INVALID_APPROVER_NAMES or "AI" in role:
        return "approved_by is not a named human principal"

    source_system = str(exemption.get("source_system") or "").strip()
    if not source_system or _REPOSITORY_LOCAL_SOURCE.search(source_system):
        return f"source_system {source_system!r} offers no external authoritative readback"
    if not str(exemption.get("approval_reference") or "").strip():
        return "approval_reference is empty"
    evidence_hashes = exemption.get("evidence_hashes")
    if not isinstance(evidence_hashes, list) or not evidence_hashes:
        return "evidence_hashes is empty"
    if not all(isinstance(item, str) and _SHA256_HEX.fullmatch(item) for item in evidence_hashes):
        return "evidence_hashes must all be lowercase sha256 hex digests"
    integrity = exemption.get("integrity")
    if (
        not isinstance(integrity, dict)
        or integrity.get("algorithm") not in EXEMPTION_INTEGRITY_ALGORITHMS
    ):
        return "integrity.algorithm must be sha256"
    recorded = str(integrity.get("content_sha256") or "").strip().lower()
    if not recorded:
        return "integrity.content_sha256 is empty"
    actual = exemption_content_sha256(exemption)
    if recorded != actual:
        return f"receipt integrity check failed: recorded {recorded} != actual {actual}"
    return None


def is_valid_exemption(
    exemption: dict[str, Any],
    component: Component,
    lic: str,
    *,
    review_cases: dict[str, dict[str, Any]],
    release_digest: str | None,
) -> bool:
    """Boolean form of validate_exemption()."""
    return (
        validate_exemption(
            exemption,
            component,
            lic,
            review_cases=review_cases,
            release_digest=release_digest,
        )
        is None
    )


def evaluate_policy(
    policy_path: Path | None = None,
    components: list[Component] | None = None,
    exemptions_path: Path | None = None,
    *,
    release_digest: str | None = None,
) -> dict[str, Any]:
    """Evaluate components against license_policy.json with fail-closed rules.

    ``release_digest`` defaults to the odayplus pin in
    docs/security/release_bindings.json, the release the SBOM and attestation
    bind to. An exemption only moves a review_required component into
    allowed_with_obligations when validate_exemption() accepts it for the exact
    installed purl, the adjudicated policy case, this release and a complete
    receipt; every candidate that is refused is recorded on the review_required
    item under ``exemption_rejections`` so the gate says why it stayed closed.
    """
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
    review_cases = {
        str(case.get("id")): case for case in review_required_cases if isinstance(case, dict)
    }
    if release_digest is None:
        release_digest = load_release_digest()

    results = {
        "status": "PASS",
        "violations": [],
        "review_required": [],
        "allowed": [],
        "allowed_with_obligations": [],
    }

    exemptions_file = exemptions_path or EXEMPTIONS_PATH
    exemptions: list[Any] = []
    if exemptions_file.exists():
        try:
            ex_data = json.loads(exemptions_file.read_text(encoding="utf-8"))
            exemptions = ex_data.get("exemptions", [])
        except Exception:
            pass

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
            rejections: list[dict[str, str]] = []
            honoured = False
            for exemption in exemptions:
                if not isinstance(exemption, dict) or exemption.get("package") != comp.name:
                    continue
                reason = validate_exemption(
                    exemption,
                    comp,
                    lic,
                    review_cases=review_cases,
                    release_digest=release_digest,
                )
                if reason is None:
                    honoured = True
                    break
                rejections.append(
                    {"exemption_id": str(exemption.get("exemption_id") or ""), "reason": reason}
                )
            if honoured:
                results["allowed_with_obligations"].append(comp)
            else:
                results["review_required"].append(
                    {
                        "component": comp,
                        "reason": f"Review required license: {lic}",
                        "exemption_rejections": rejections,
                    }
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
            print(
                f"Policy evaluation FAILED: {len(eval_result['violations'])} violations, "
                f"{len(eval_result['review_required'])} components awaiting adjudication:",
                file=sys.stderr,
            )
            for v in eval_result["violations"]:
                print(f"  - {v['component'].name} ({v['component'].version}): {v['reason']}", file=sys.stderr)
            for item in eval_result["review_required"]:
                comp = item["component"]
                print(f"  - {comp.name} ({comp.version}): {item['reason']}", file=sys.stderr)
                for rejection in item.get("exemption_rejections", []):
                    print(
                        f"      exemption {rejection['exemption_id']} refused: {rejection['reason']}",
                        file=sys.stderr,
                    )
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
