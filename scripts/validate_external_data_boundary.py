#!/usr/bin/env python3
"""Fail closed when the frozen legacy external-data surface grows or drifts.

ODP-LEGACY-INVENTORY-001 / contract ``odayplus.legacy-external-data-disposition.v2``.

The v1 policy (``delivery_toolchain/governance/emgi-consumer-boundary.json``) is
diff-scoped: it looks at the paths a pull request touched and checks them against
a handful of forbidden prefixes. That leaves two holes this validator closes:

1. A file that nobody's diff touches is never classified at all, so "is this
   producer code?" is only ever answered for known directories.
2. A provider reference that lands outside ``modules/external_data/`` — in
   ``services/provider-gateway/``, ``product_ops/``, terraform, or a workflow —
   is invisible to a prefix check.

So this validator is *whole-tree* and *exhaustive*. It reads
``docs/design/emgi/v0.4.1/LEGACY_EXTERNAL_DATA_DISPOSITION.yaml`` and runs four
checks over every tracked file:

``classification``
    Every tracked file must match at least one ordered classification rule, and
    every rule must match at least one file. An unclassified file is a
    violation: a genuinely new top-level surface has to get an explicit
    disposition, it cannot arrive unlabelled.

``freeze``
    Each frozen surface records its exact file inventory. A file appearing under
    a frozen surface's globs that is not in the inventory is a new producer
    capability. A file in the inventory that no longer exists means retirement
    happened without updating the disposition record.

``blocked capabilities``
    Path- and content-level detection for the six capability classes odayplus
    may not grow: connectors, provider credentials, source schedulers, raw
    evidence stores, canonical market writers, and direct provider calls.
    Existing occurrences are grandfathered by explicit path; anything else fails.
    Surfaces listed under ``allowed_surfaces`` (assisted intake, product review)
    are exempted per-capability so those workflows keep working.

``provider references``
    Signal regexes over the scanned tree. Every hit must be covered by a
    declaration in the disposition file that names both the matched text and a
    path glob. Undeclared references fail; declarations that match nothing fail
    too, so the inventory cannot rot.

Usage::

    uv run python scripts/validate_external_data_boundary.py
    uv run python scripts/validate_external_data_boundary.py --json
    uv run python scripts/validate_external_data_boundary.py --check classification

Exit codes: ``0`` clean, ``1`` violations found, ``2`` the policy itself is
malformed or unreadable.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "docs/design/emgi/v0.4.1/LEGACY_EXTERNAL_DATA_DISPOSITION.yaml"
EXPECTED_CONTRACT = "odayplus.legacy-external-data-disposition.v2"
EXPECTED_SCHEMA_VERSION = 2

CHECKS = ("classification", "freeze", "capabilities", "provider_references")


class PolicyError(RuntimeError):
    """The disposition document is missing, unparseable, or structurally wrong."""


# ---------------------------------------------------------------------------
# Glob matching
# ---------------------------------------------------------------------------
#
# Deliberately not ``fnmatch``: ``fnmatch`` lets ``*`` cross ``/``, which turns
# ``modules/*/providers/*.py`` into a far wider rule than it reads as, and a
# classification rule that silently over-matches is worse than no rule. The
# semantics here are the ones ``config/code-boundaries.yaml`` already assumes:
#
#   ``*``      one path segment, no separator
#   ``?``      one character, no separator
#   ``a/**/b`` zero or more intermediate segments (so it also matches ``a/b``)
#   ``a/**``   ``a`` and everything beneath it
#   ``**``     anything


def _translate_glob(pattern: str) -> str:
    if pattern == "**":
        return ".*"

    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if pattern.startswith("/**/", index):
            out.append("(?:/.*)?/")
            index += 4
        elif pattern.startswith("/**", index) and index + 3 == length:
            out.append("(?:/.*)?")
            index += 3
        elif pattern.startswith("**/", index) and index == 0:
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif char == "*":
            out.append("[^/]*")
            index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(char))
            index += 1
    return "".join(out)


@lru_cache(maxsize=4096)
def _compile_glob(pattern: str) -> re.Pattern[str]:
    return re.compile(f"^{_translate_glob(pattern)}$")


def glob_match(path: str, pattern: str) -> bool:
    """Match one repo-relative POSIX path against one glob pattern."""
    return _compile_glob(pattern).search(path) is not None


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(glob_match(path, pattern) for pattern in patterns)


def selects(path: str, include: Sequence[str], exclude: Sequence[str] = ()) -> bool:
    """Include/exclude selection, exclude winning ties."""
    return matches_any(path, include) and not matches_any(path, exclude)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    check: str
    code: str
    path: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "check": self.check,
            "code": self.code,
            "path": self.path,
            "detail": self.detail,
        }

    def render(self) -> str:
        return f"{self.path}: [{self.code}] {self.detail}"


@dataclass
class Report:
    contract: str
    checks_run: tuple[str, ...]
    tracked_file_count: int
    violations: list[Violation] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "checks_run": list(self.checks_run),
            "tracked_file_count": self.tracked_file_count,
            "ok": self.ok,
            "stats": self.stats,
            "violations": [violation.to_dict() for violation in self.violations],
        }


# ---------------------------------------------------------------------------
# Policy loading
# ---------------------------------------------------------------------------


def _require(mapping: Mapping[str, Any], key: str, kind: type, where: str) -> Any:
    if key not in mapping:
        raise PolicyError(f"{where}: missing required key {key!r}")
    value = mapping[key]
    if not isinstance(value, kind):
        raise PolicyError(
            f"{where}: key {key!r} must be {kind.__name__}, got {type(value).__name__}"
        )
    return value


def _string_list(mapping: Mapping[str, Any], key: str, where: str) -> list[str]:
    value = mapping.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PolicyError(f"{where}: key {key!r} must be a list of strings")
    return list(value)


def load_policy(path: Path | str = DEFAULT_POLICY) -> dict[str, Any]:
    """Read and structurally validate the disposition document."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PolicyError(f"cannot read disposition policy {path}: {error}") from error

    try:
        policy = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise PolicyError(f"{path}: invalid YAML: {error}") from error

    if not isinstance(policy, Mapping):
        raise PolicyError(f"{path}: top level must be a mapping")

    contract = _require(policy, "contract", str, str(path))
    if contract != EXPECTED_CONTRACT:
        raise PolicyError(f"{path}: contract must be {EXPECTED_CONTRACT!r}, got {contract!r}")
    version = _require(policy, "schema_version", int, str(path))
    if version != EXPECTED_SCHEMA_VERSION:
        raise PolicyError(f"{path}: schema_version must be {EXPECTED_SCHEMA_VERSION}, got {version}")

    validate_policy_structure(policy, source=str(path))
    return dict(policy)


def validate_policy_structure(policy: Mapping[str, Any], *, source: str = "<policy>") -> None:
    """Reject a policy that cannot be enforced, before any file is scanned."""
    dispositions = _require(policy, "dispositions", list, source)
    disposition_ids: set[str] = set()
    for entry in dispositions:
        if not isinstance(entry, Mapping):
            raise PolicyError(f"{source}: dispositions entries must be mappings")
        entry_id = _require(entry, "id", str, f"{source}: disposition")
        if entry_id in disposition_ids:
            raise PolicyError(f"{source}: duplicate disposition id {entry_id!r}")
        disposition_ids.add(entry_id)
        _require(entry, "intent", str, f"{source}: disposition {entry_id}")
        _require(entry, "description", str, f"{source}: disposition {entry_id}")

    classification = _require(policy, "classification", dict, source)
    rules = _require(classification, "rules", list, f"{source}: classification")
    if not rules:
        raise PolicyError(f"{source}: classification.rules must not be empty")
    rule_ids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, Mapping):
            raise PolicyError(f"{source}: classification rules must be mappings")
        rule_id = _require(rule, "id", str, f"{source}: classification rule")
        if rule_id in rule_ids:
            raise PolicyError(f"{source}: duplicate classification rule id {rule_id!r}")
        rule_ids.add(rule_id)
        disposition = _require(rule, "disposition", str, f"{source}: rule {rule_id}")
        if disposition not in disposition_ids:
            raise PolicyError(
                f"{source}: rule {rule_id!r} uses unknown disposition {disposition!r}"
            )
        if not _string_list(rule, "include", f"{source}: rule {rule_id}"):
            raise PolicyError(f"{source}: rule {rule_id!r} must declare a non-empty include list")
        _string_list(rule, "exclude", f"{source}: rule {rule_id}")
        _require(rule, "rationale", str, f"{source}: rule {rule_id}")

    for surface in _require(policy, "frozen_surfaces", list, source):
        if not isinstance(surface, Mapping):
            raise PolicyError(f"{source}: frozen_surfaces entries must be mappings")
        surface_id = _require(surface, "id", str, f"{source}: frozen surface")
        if not _string_list(surface, "include", f"{source}: frozen surface {surface_id}"):
            raise PolicyError(f"{source}: frozen surface {surface_id!r} needs an include list")
        _string_list(surface, "exclude", f"{source}: frozen surface {surface_id}")
        _string_list(surface, "inventory", f"{source}: frozen surface {surface_id}")
        _require(surface, "description", str, f"{source}: frozen surface {surface_id}")

    capability_ids: set[str] = set()
    for capability in _require(policy, "blocked_capabilities", list, source):
        if not isinstance(capability, Mapping):
            raise PolicyError(f"{source}: blocked_capabilities entries must be mappings")
        capability_id = _require(capability, "id", str, f"{source}: blocked capability")
        capability_ids.add(capability_id)
        _require(capability, "description", str, f"{source}: capability {capability_id}")
        scope = _require(capability, "scope", dict, f"{source}: capability {capability_id}")
        if not _string_list(scope, "include", f"{source}: capability {capability_id} scope"):
            raise PolicyError(f"{source}: capability {capability_id!r} needs a scope include list")
        _string_list(scope, "exclude", f"{source}: capability {capability_id} scope")
        has_signal = bool(
            _string_list(capability, "filename_tokens", f"{source}: capability {capability_id}")
            or _string_list(capability, "path_globs", f"{source}: capability {capability_id}")
            or _string_list(capability, "content_patterns", f"{source}: capability {capability_id}")
        )
        if not has_signal:
            raise PolicyError(
                f"{source}: capability {capability_id!r} declares no detection signal"
            )
        for pattern in _string_list(
            capability, "content_patterns", f"{source}: capability {capability_id}"
        ):
            _compile_regex(pattern, f"{source}: capability {capability_id}")

    for surface in _require(policy, "allowed_surfaces", list, source):
        if not isinstance(surface, Mapping):
            raise PolicyError(f"{source}: allowed_surfaces entries must be mappings")
        surface_id = _require(surface, "id", str, f"{source}: allowed surface")
        _require(surface, "description", str, f"{source}: allowed surface {surface_id}")
        if not _string_list(surface, "include", f"{source}: allowed surface {surface_id}"):
            raise PolicyError(f"{source}: allowed surface {surface_id!r} needs an include list")
        for capability_id in _string_list(
            surface, "capability_exemptions", f"{source}: allowed surface {surface_id}"
        ):
            if capability_id not in capability_ids:
                raise PolicyError(
                    f"{source}: allowed surface {surface_id!r} exempts unknown "
                    f"capability {capability_id!r}"
                )

    references = _require(policy, "provider_references", dict, source)
    scan = _require(references, "scan", dict, f"{source}: provider_references")
    if not _string_list(scan, "include", f"{source}: provider_references.scan"):
        raise PolicyError(f"{source}: provider_references.scan needs an include list")
    _string_list(scan, "exclude", f"{source}: provider_references.scan")

    signal_ids: set[str] = set()
    signals = _require(references, "signals", list, f"{source}: provider_references")
    if not signals:
        raise PolicyError(f"{source}: provider_references.signals must not be empty")
    for signal in signals:
        if not isinstance(signal, Mapping):
            raise PolicyError(f"{source}: provider_references.signals entries must be mappings")
        signal_id = _require(signal, "id", str, f"{source}: provider reference signal")
        if signal_id in signal_ids:
            raise PolicyError(f"{source}: duplicate provider reference signal {signal_id!r}")
        signal_ids.add(signal_id)
        _compile_regex(
            _require(signal, "pattern", str, f"{source}: signal {signal_id}"),
            f"{source}: signal {signal_id}",
        )

    declared_ids: set[str] = set()
    for declaration in _require(references, "declared", list, f"{source}: provider_references"):
        if not isinstance(declaration, Mapping):
            raise PolicyError(f"{source}: provider_references.declared entries must be mappings")
        declaration_id = _require(declaration, "id", str, f"{source}: provider reference")
        if declaration_id in declared_ids:
            raise PolicyError(f"{source}: duplicate provider reference id {declaration_id!r}")
        declared_ids.add(declaration_id)
        signal_id = _require(declaration, "signal", str, f"{source}: reference {declaration_id}")
        if signal_id not in signal_ids:
            raise PolicyError(
                f"{source}: reference {declaration_id!r} uses unknown signal {signal_id!r}"
            )
        disposition = _require(
            declaration, "disposition", str, f"{source}: reference {declaration_id}"
        )
        if disposition not in disposition_ids:
            raise PolicyError(
                f"{source}: reference {declaration_id!r} uses unknown "
                f"disposition {disposition!r}"
            )
        if not _string_list(declaration, "matches", f"{source}: reference {declaration_id}"):
            raise PolicyError(f"{source}: reference {declaration_id!r} needs a matches list")
        if not _string_list(declaration, "paths", f"{source}: reference {declaration_id}"):
            raise PolicyError(f"{source}: reference {declaration_id!r} needs a paths list")
        _require(declaration, "rationale", str, f"{source}: reference {declaration_id}")

    runtime_gates = policy.get("runtime_gate_invariants")
    if runtime_gates is not None:
        if not isinstance(runtime_gates, Mapping):
            raise PolicyError(f"{source}: runtime_gate_invariants must be a mapping")
        version = _require(
            runtime_gates, "schema_version", int, f"{source}: runtime_gate_invariants"
        )
        if version != 1:
            raise PolicyError(
                f"{source}: runtime_gate_invariants schema_version must be 1, got {version}"
            )
        entries = _require(runtime_gates, "entries", list, f"{source}: runtime_gate_invariants")
        if not entries:
            raise PolicyError(f"{source}: runtime_gate_invariants.entries must not be empty")

        dispositioned_paths = {
            path
            for surface in policy.get("frozen_surfaces", [])
            for path in surface.get("inventory", [])
        }
        dispositioned_paths |= {
            path
            for capability in policy.get("blocked_capabilities", [])
            for path in capability.get("grandfathered_paths", [])
        }

        gate_ids: set[str] = set()
        allowed_assertion_types = {"contains", "ordered_tokens", "constant_equals"}
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise PolicyError(f"{source}: runtime_gate_invariants entries must be mappings")
            entry_id = _require(entry, "id", str, f"{source}: runtime gate invariant")
            if entry_id in gate_ids:
                raise PolicyError(
                    f"{source}: duplicate runtime gate invariant id {entry_id!r}"
                )
            gate_ids.add(entry_id)

            paths = _string_list(entry, "paths", f"{source}: runtime gate invariant {entry_id}")
            if not paths:
                raise PolicyError(
                    f"{source}: runtime gate invariant {entry_id!r} must declare a non-empty paths list"
                )
            for path in paths:
                if path not in dispositioned_paths:
                    raise PolicyError(
                        f"{source}: runtime gate invariant {entry_id!r} path {path!r} "
                        "is not dispositioned in inventory or grandfathered_paths"
                    )

            assertions = _require(
                entry, "assertions", list, f"{source}: runtime gate invariant {entry_id}"
            )
            if not assertions:
                raise PolicyError(
                    f"{source}: runtime gate invariant {entry_id!r} assertions must not be empty"
                )
            for assertion in assertions:
                if not isinstance(assertion, Mapping):
                    raise PolicyError(
                        f"{source}: runtime gate invariant {entry_id!r} assertions must be mappings"
                    )
                atype = _require(
                    assertion, "type", str, f"{source}: runtime gate invariant {entry_id} assertion"
                )
                if atype not in allowed_assertion_types:
                    raise PolicyError(
                        f"{source}: runtime gate invariant {entry_id!r} uses unknown assertion type {atype!r}"
                    )
                if atype == "contains":
                    _require(
                        assertion, "text", str, f"{source}: runtime gate invariant {entry_id} assertion"
                    )
                elif atype == "ordered_tokens":
                    _require(
                        assertion, "before", str, f"{source}: runtime gate invariant {entry_id} assertion"
                    )
                    _require(
                        assertion, "after", str, f"{source}: runtime gate invariant {entry_id} assertion"
                    )
                elif atype == "constant_equals":
                    _require(
                        assertion, "name", str, f"{source}: runtime gate invariant {entry_id} assertion"
                    )
                    if "value" not in assertion:
                        raise PolicyError(
                            f"{source}: runtime gate invariant {entry_id} assertion: missing required key 'value'"
                        )


def evaluate_runtime_gate_assertion(
    assertion: Mapping[str, Any],
    content: str,
) -> bool:
    """Evaluate one runtime gate closure invariant assertion against file content."""
    atype = assertion.get("type")
    if atype == "contains":
        return str(assertion.get("text", "")) in content
    if atype == "ordered_tokens":
        before = str(assertion.get("before", ""))
        after = str(assertion.get("after", ""))
        if before not in content or after not in content:
            return False
        return content.index(before) < content.index(after)
    if atype == "constant_equals":
        name = str(assertion.get("name", ""))
        value = str(assertion.get("value", ""))
        pattern = rf"^\s*{re.escape(name)}\s*=\s*{re.escape(value)}\b"
        return re.search(pattern, content, re.MULTILINE) is not None
    return False


def _compile_regex(pattern: str, where: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as error:
        raise PolicyError(f"{where}: invalid regex {pattern!r}: {error}") from error


# ---------------------------------------------------------------------------
# Repository access
# ---------------------------------------------------------------------------


def tracked_files(root: Path | str = ROOT) -> list[str]:
    """Every path git tracks, repo-relative and POSIX-separated."""
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    return sorted(
        entry.decode("utf-8", "surrogateescape")
        for entry in completed.stdout.split(b"\0")
        if entry
    )


def make_reader(root: Path | str = ROOT) -> Callable[[str], str]:
    """Text reader that degrades to empty string for binary/unreadable files."""
    base = Path(root)

    def read(path: str) -> str:
        try:
            return (base / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    return read


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def classify(files: Sequence[str], policy: Mapping[str, Any]) -> dict[str, str]:
    """Map each file to the id of the first classification rule that selects it.

    Files no rule selects are absent from the result; ``check_classification``
    turns those into violations.
    """
    rules = policy["classification"]["rules"]
    assigned: dict[str, str] = {}
    for path in files:
        for rule in rules:
            if selects(path, rule["include"], rule.get("exclude") or ()):
                assigned[path] = rule["id"]
                break
    return assigned


def check_classification(
    files: Sequence[str], policy: Mapping[str, Any]
) -> tuple[list[Violation], dict[str, Any]]:
    assigned = classify(files, policy)
    violations = [
        Violation(
            check="classification",
            code="unclassified_file",
            path=path,
            detail=(
                "no classification rule in the disposition record covers this path; "
                "a new surface needs an explicit disposition before it can land"
            ),
        )
        for path in files
        if path not in assigned
    ]

    used = set(assigned.values())
    for rule in policy["classification"]["rules"]:
        if rule["id"] in used or rule.get("allow_absent"):
            continue
        violations.append(
            Violation(
                check="classification",
                code="dead_classification_rule",
                path=str(rule["id"]),
                detail=(
                    "classification rule matches no tracked file; delete it or set "
                    "allow_absent: true so the inventory stays truthful"
                ),
            )
        )

    rule_dispositions = {
        rule["id"]: rule["disposition"] for rule in policy["classification"]["rules"]
    }
    counts: dict[str, int] = {}
    for rule_id in assigned.values():
        counts[rule_dispositions[rule_id]] = counts.get(rule_dispositions[rule_id], 0) + 1

    stats = {
        "classified": len(assigned),
        "unclassified": len(files) - len(assigned),
        "by_disposition": dict(sorted(counts.items())),
    }
    return violations, stats


def check_freeze(
    files: Sequence[str], policy: Mapping[str, Any]
) -> tuple[list[Violation], dict[str, Any]]:
    violations: list[Violation] = []
    tracked = set(files)
    frozen_total = 0

    for surface in policy["frozen_surfaces"]:
        include = surface["include"]
        exclude = surface.get("exclude") or ()
        inventory = set(surface.get("inventory") or ())
        present = {path for path in tracked if selects(path, include, exclude)}
        frozen_total += len(present)

        for path in sorted(present - inventory):
            violations.append(
                Violation(
                    check="freeze",
                    code="frozen_surface_addition",
                    path=path,
                    detail=(
                        f"new file under frozen surface {surface['id']!r}; "
                        f"{surface['description']} "
                        "New producer capability belongs in alfloop-dev/oday-data-platform"
                    ),
                )
            )
        for path in sorted(inventory - present):
            violations.append(
                Violation(
                    check="freeze",
                    code="frozen_surface_inventory_stale",
                    path=path,
                    detail=(
                        f"frozen surface {surface['id']!r} lists a file that is no longer "
                        "tracked; retiring legacy code must also drop it from the "
                        "disposition inventory in the same change"
                    ),
                )
            )

    return violations, {"frozen_files": frozen_total}


def _exempt_capabilities(path: str, policy: Mapping[str, Any]) -> set[str]:
    """Capability ids the allowed-surface list waives for this path."""
    exempt: set[str] = set()
    for surface in policy["allowed_surfaces"]:
        if selects(path, surface["include"], surface.get("exclude") or ()):
            exempt.update(surface.get("capability_exemptions") or ())
    return exempt


def check_capabilities(
    files: Sequence[str],
    policy: Mapping[str, Any],
    read_text: Callable[[str], str],
) -> tuple[list[Violation], dict[str, Any]]:
    violations: list[Violation] = []
    detections = 0

    for capability in policy["blocked_capabilities"]:
        capability_id = capability["id"]
        scope = capability["scope"]
        grandfathered = set(capability.get("grandfathered_paths") or ())
        tokens = [token.lower() for token in capability.get("filename_tokens") or ()]
        path_globs = capability.get("path_globs") or ()
        content_patterns = [
            _compile_regex(pattern, f"capability {capability_id}")
            for pattern in capability.get("content_patterns") or ()
        ]

        for path in files:
            if not selects(path, scope["include"], scope.get("exclude") or ()):
                continue
            if capability_id in _exempt_capabilities(path, policy):
                continue

            reasons: list[str] = []
            if path_globs and matches_any(path, path_globs):
                reasons.append("path is inside a producer-owned location")
            if tokens:
                filename = path.rsplit("/", 1)[-1].lower()
                hit = [token for token in tokens if token in filename]
                if hit:
                    reasons.append(f"filename carries producer token(s) {', '.join(sorted(hit))}")
            if content_patterns:
                matched = sorted(
                    {
                        match.group(0)
                        for pattern in content_patterns
                        for match in pattern.finditer(read_text(path))
                    }
                )
                if matched:
                    preview = ", ".join(matched[:4])
                    if len(matched) > 4:
                        preview += f", … (+{len(matched) - 4} more)"
                    reasons.append(f"content matches {preview}")

            if not reasons:
                continue
            detections += 1
            if path in grandfathered:
                continue
            violations.append(
                Violation(
                    check="capabilities",
                    code=f"blocked_capability:{capability_id}",
                    path=path,
                    detail=f"{capability['description']} Detected because {'; '.join(reasons)}.",
                )
            )

        for path in sorted(grandfathered):
            if path not in set(files):
                violations.append(
                    Violation(
                        check="capabilities",
                        code="stale_grandfathered_path",
                        path=path,
                        detail=(
                            f"capability {capability_id!r} grandfathers a path that is no "
                            "longer tracked; drop the entry so the freeze list stays exact"
                        ),
                    )
                )

    return violations, {"capability_detections": detections}


def check_provider_references(
    files: Sequence[str],
    policy: Mapping[str, Any],
    read_text: Callable[[str], str],
) -> tuple[list[Violation], dict[str, Any]]:
    references = policy["provider_references"]
    scan = references["scan"]
    signals = [
        (signal["id"], _compile_regex(signal["pattern"], f"signal {signal['id']}"))
        for signal in references["signals"]
    ]
    declared = references["declared"]

    violations: list[Violation] = []
    used_declarations: set[str] = set()
    hit_count = 0

    for path in files:
        if not selects(path, scan["include"], scan.get("exclude") or ()):
            continue
        text = read_text(path)
        if not text:
            continue
        for signal_id, pattern in signals:
            for matched in sorted({match.group(0) for match in pattern.finditer(text)}):
                hit_count += 1
                covering = [
                    declaration
                    for declaration in declared
                    if declaration["signal"] == signal_id
                    and matched in declaration["matches"]
                    and matches_any(path, declaration["paths"])
                ]
                if covering:
                    used_declarations.update(
                        declaration["id"] for declaration in covering
                    )
                    continue
                violations.append(
                    Violation(
                        check="provider_references",
                        code="undeclared_provider_reference",
                        path=path,
                        detail=(
                            f"signal {signal_id!r} matched {matched!r} but no entry in "
                            "provider_references.declared covers this text at this path; "
                            "classify it in the disposition record or route it through "
                            "the data-platform client"
                        ),
                    )
                )

    for declaration in declared:
        if declaration["id"] in used_declarations or declaration.get("allow_absent"):
            continue
        violations.append(
            Violation(
                check="provider_references",
                code="dead_provider_declaration",
                path=str(declaration["id"]),
                detail=(
                    "declared provider reference matches nothing in the tree; drop it or "
                    "set allow_absent: true if it is a forward-looking prohibition"
                ),
            )
        )

    return violations, {"provider_reference_hits": hit_count}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def evaluate(
    policy: Mapping[str, Any],
    files: Sequence[str],
    read_text: Callable[[str], str],
    checks: Sequence[str] = CHECKS,
) -> Report:
    """Run the requested checks and fold them into one report."""
    unknown = [check for check in checks if check not in CHECKS]
    if unknown:
        raise PolicyError(f"unknown check(s): {', '.join(sorted(unknown))}")

    report = Report(
        contract=str(policy.get("contract", EXPECTED_CONTRACT)),
        checks_run=tuple(checks),
        tracked_file_count=len(files),
    )

    if "classification" in checks:
        violations, stats = check_classification(files, policy)
        report.violations.extend(violations)
        report.stats.update(stats)
    if "freeze" in checks:
        violations, stats = check_freeze(files, policy)
        report.violations.extend(violations)
        report.stats.update(stats)
    if "capabilities" in checks:
        violations, stats = check_capabilities(files, policy, read_text)
        report.violations.extend(violations)
        report.stats.update(stats)
    if "provider_references" in checks:
        violations, stats = check_provider_references(files, policy, read_text)
        report.violations.extend(violations)
        report.stats.update(stats)

    report.violations.sort(key=lambda violation: (violation.check, violation.path, violation.code))
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--policy", default=str(DEFAULT_POLICY), help="disposition YAML path")
    parser.add_argument("--root", default=str(ROOT), help="repository root to scan")
    parser.add_argument(
        "--check",
        action="append",
        choices=CHECKS,
        help="run only the named check (repeatable); defaults to all checks",
    )
    parser.add_argument("--json", action="store_true", help="emit the machine-readable report")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        policy = load_policy(args.policy)
        files = tracked_files(args.root)
    except PolicyError as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as error:
        stderr = error.stderr.decode("utf-8", "ignore") if error.stderr else ""
        print(f"git ls-files failed in {args.root}: {stderr.strip()}", file=sys.stderr)
        return 2

    report = evaluate(policy, files, make_reader(args.root), tuple(args.check or CHECKS))

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=False))
    else:
        print(f"contract: {report.contract}")
        print(f"tracked files: {report.tracked_file_count}")
        for key, value in report.stats.items():
            print(f"  {key}: {json.dumps(value) if isinstance(value, dict) else value}")
        if report.ok:
            print("external-data boundary: OK")
        else:
            print(f"external-data boundary: {len(report.violations)} violation(s)")
            for violation in report.violations:
                print(f"  - {violation.render()}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
