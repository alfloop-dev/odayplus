#!/usr/bin/env python3
"""Fail-closed validator for the Gate 0-6 machine-readable release registry.

``docs/evidence/gates/RELEASE_GATE_REGISTRY.json`` is the authoritative record
of the seven release gates. Prose checklists (``docs/release/RELEASE_GATE_CHECKLIST.md``)
stay the human-facing narrative; this registry is the machine-readable truth the
final gate audit reads.

Everything fails closed. A gate is *not* cleared unless the registry proves it:

* every gate carries status, owner, reviewer, status date, and the exact release
  SHA it was attested against;
* a cleared gate needs at least one evidence reference that resolves to a real
  repository path and at least one passing receipt bound to that same exact SHA;
* a receipt that names a different SHA is stale, not evidence -- a new release
  candidate re-opens every gate;
* an open gate must name at least one blocker, so a red registry says what to
  fix rather than going quiet;
* ``release.decision`` may only be ``go`` when all seven gates are cleared and a
  human sign-off is recorded.

Exit codes: ``0`` when the registry is internally consistent, ``1`` when any
integrity rule fails or when ``--require-go`` is passed and the release is not
in a GO state. A well-formed NO-GO registry is *valid* -- it exits ``0`` in the
default mode and non-zero under ``--require-go``.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json"

SUPPORTED_SCHEMA_VERSIONS = ("1.0.0",)
GATE_COUNT = 7

CLEARED_STATUSES = ("passed", "passed-with-deviation", "not-applicable")
OPEN_STATUSES = ("not-started", "in-progress", "blocked", "failed")
ALLOWED_STATUSES = CLEARED_STATUSES + OPEN_STATUSES
ALLOWED_DECISIONS = ("go", "no-go")

EVIDENCE_KINDS = ("doc", "script", "test", "ci-check", "command")
PATH_EVIDENCE_KINDS = ("doc", "script", "test")
RECEIPT_RESULTS = ("pass", "fail")

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

REQUIRED_TOP_LEVEL_KEYS = (
    "schema_version",
    "registry_id",
    "task_id",
    "generated_at",
    "release",
    "gates",
)
REQUIRED_RELEASE_KEYS = (
    "candidate_sha",
    "candidate_ref",
    "decision",
    "decision_owner",
    "decision_date",
    "decision_note",
)
REQUIRED_GATE_KEYS = (
    "id",
    "index",
    "name",
    "scope",
    "owner",
    "reviewer",
    "status",
    "status_date",
    "release_sha",
    "required_checks",
    "evidence",
    "receipts",
    "blockers",
)
REQUIRED_EVIDENCE_KEYS = ("kind", "ref", "description")
REQUIRED_RECEIPT_KEYS = (
    "receipt_id",
    "release_sha",
    "result",
    "recorded_at",
    "recorded_by",
    "artifact",
)
REQUIRED_DEVIATION_KEYS = ("description", "approver", "review_by")
REQUIRED_SIGNOFF_KEYS = ("approver", "date")


class RegistryLoadError(Exception):
    """Raised when the registry cannot be read as a JSON object."""


def load_registry(path: Path) -> dict[str, Any]:
    """Read the registry file, failing closed on anything that is not an object."""
    if not path.exists():
        raise RegistryLoadError(f"missing release gate registry: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryLoadError(f"release gate registry is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegistryLoadError("release gate registry must be a JSON object")
    return payload


def is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def is_valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def is_valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def is_cleared(gate: Any) -> bool:
    """True when the gate's recorded status no longer blocks the release."""
    return isinstance(gate, dict) and gate.get("status") in CLEARED_STATUSES


def blocking_gates(registry: dict[str, Any]) -> list[str]:
    """Gate ids that still block a GO decision, in registry order."""
    gates = registry.get("gates")
    if not isinstance(gates, list):
        return ["<gates missing>"]
    blocking: list[str] = []
    for index, gate in enumerate(gates):
        if is_cleared(gate):
            continue
        gate_id = gate.get("id") if isinstance(gate, dict) else None
        blocking.append(gate_id if is_nonempty_str(gate_id) else f"<gate #{index}>")
    return blocking


def validate_release(release: Any, errors: list[str]) -> str | None:
    """Validate the release block; return the candidate SHA when it is usable."""
    if not isinstance(release, dict):
        errors.append("release must be an object")
        return None

    for key in REQUIRED_RELEASE_KEYS:
        if key not in release:
            errors.append(f"release missing required field: {key}")

    candidate_sha = release.get("candidate_sha")
    if not isinstance(candidate_sha, str) or not SHA_PATTERN.fullmatch(candidate_sha):
        errors.append(
            "release.candidate_sha must be an exact 40-character lowercase git SHA, "
            f"got: {candidate_sha!r}"
        )
        candidate_sha = None

    if not is_nonempty_str(release.get("candidate_ref")):
        errors.append("release.candidate_ref must be a non-empty string")

    decision = release.get("decision")
    if decision not in ALLOWED_DECISIONS:
        errors.append(
            f"release.decision must be one of {list(ALLOWED_DECISIONS)}, got: {decision!r}"
        )

    if not is_nonempty_str(release.get("decision_owner")):
        errors.append("release.decision_owner must be a non-empty string")
    if not is_valid_date(release.get("decision_date")):
        errors.append(
            f"release.decision_date must be an ISO date, got: {release.get('decision_date')!r}"
        )
    if not is_nonempty_str(release.get("decision_note")):
        errors.append("release.decision_note must be a non-empty string")

    if decision == "go":
        signoff = release.get("human_signoff")
        if not isinstance(signoff, dict):
            errors.append("release.decision 'go' requires a release.human_signoff object")
        else:
            for key in REQUIRED_SIGNOFF_KEYS:
                if key not in signoff:
                    errors.append(f"release.human_signoff missing required field: {key}")
            if not is_nonempty_str(signoff.get("approver")):
                errors.append("release.human_signoff.approver must be a non-empty string")
            if not is_valid_date(signoff.get("date")):
                errors.append("release.human_signoff.date must be an ISO date")

    return candidate_sha


def validate_evidence(
    gate_id: str, evidence: Any, root: Path, errors: list[str]
) -> None:
    if not isinstance(evidence, list):
        errors.append(f"{gate_id}.evidence must be a list")
        return
    for position, entry in enumerate(evidence):
        label = f"{gate_id}.evidence[{position}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        for key in REQUIRED_EVIDENCE_KEYS:
            if key not in entry:
                errors.append(f"{label} missing required field: {key}")
        kind = entry.get("kind")
        if kind not in EVIDENCE_KINDS:
            errors.append(f"{label}.kind must be one of {list(EVIDENCE_KINDS)}, got: {kind!r}")
        ref = entry.get("ref")
        if not is_nonempty_str(ref):
            errors.append(f"{label}.ref must be a non-empty string")
        elif kind in PATH_EVIDENCE_KINDS and not (root / ref).exists():
            errors.append(f"{label} references a path that does not exist: {ref}")
        if not is_nonempty_str(entry.get("description")):
            errors.append(f"{label}.description must be a non-empty string")


def validate_receipts(
    gate_id: str,
    receipts: Any,
    candidate_sha: str | None,
    root: Path,
    errors: list[str],
) -> int:
    """Validate receipts; return the count of passing receipts bound to the SHA."""
    if not isinstance(receipts, list):
        errors.append(f"{gate_id}.receipts must be a list")
        return 0

    passing = 0
    for position, receipt in enumerate(receipts):
        label = f"{gate_id}.receipts[{position}]"
        if not isinstance(receipt, dict):
            errors.append(f"{label} must be an object")
            continue
        for key in REQUIRED_RECEIPT_KEYS:
            if key not in receipt:
                errors.append(f"{label} missing required field: {key}")

        receipt_sha = receipt.get("release_sha")
        sha_ok = isinstance(receipt_sha, str) and SHA_PATTERN.fullmatch(receipt_sha)
        if not sha_ok:
            errors.append(
                f"{label}.release_sha must be an exact 40-character lowercase git SHA, "
                f"got: {receipt_sha!r}"
            )
        elif candidate_sha is not None and receipt_sha != candidate_sha:
            errors.append(
                f"{label} is stale: bound to {receipt_sha} but the release candidate is "
                f"{candidate_sha}"
            )
            sha_ok = False

        if not is_nonempty_str(receipt.get("receipt_id")):
            errors.append(f"{label}.receipt_id must be a non-empty string")
        if not is_nonempty_str(receipt.get("recorded_by")):
            errors.append(f"{label}.recorded_by must be a non-empty string")
        if not is_valid_timestamp(receipt.get("recorded_at")):
            errors.append(
                f"{label}.recorded_at must be an ISO 8601 timestamp, "
                f"got: {receipt.get('recorded_at')!r}"
            )

        artifact = receipt.get("artifact")
        if not is_nonempty_str(artifact):
            errors.append(f"{label}.artifact must be a non-empty string")
        elif not (root / artifact).exists():
            errors.append(f"{label}.artifact does not exist: {artifact}")

        result = receipt.get("result")
        if result not in RECEIPT_RESULTS:
            errors.append(
                f"{label}.result must be one of {list(RECEIPT_RESULTS)}, got: {result!r}"
            )
        elif result == "pass" and sha_ok:
            passing += 1

    return passing


def validate_gate(
    gate: Any,
    expected_index: int,
    candidate_sha: str | None,
    root: Path,
    errors: list[str],
) -> None:
    gate_id = f"gate-{expected_index}"
    if not isinstance(gate, dict):
        errors.append(f"{gate_id} must be an object")
        return

    actual_id = gate.get("id")
    if actual_id != gate_id:
        errors.append(f"gates[{expected_index}].id must be {gate_id!r}, got: {actual_id!r}")
    if gate.get("index") != expected_index:
        errors.append(
            f"{gate_id}.index must be {expected_index}, got: {gate.get('index')!r}"
        )

    for key in REQUIRED_GATE_KEYS:
        if key not in gate:
            errors.append(f"{gate_id} missing required field: {key}")

    for key in ("name", "scope", "owner", "reviewer"):
        if not is_nonempty_str(gate.get(key)):
            errors.append(f"{gate_id}.{key} must be a non-empty string")

    owner = gate.get("owner")
    reviewer = gate.get("reviewer")
    if is_nonempty_str(owner) and owner == reviewer:
        errors.append(f"{gate_id}.reviewer must differ from owner ({owner})")

    status = gate.get("status")
    if status not in ALLOWED_STATUSES:
        errors.append(
            f"{gate_id}.status must be one of {list(ALLOWED_STATUSES)}, got: {status!r}"
        )

    if not is_valid_date(gate.get("status_date")):
        errors.append(
            f"{gate_id}.status_date must be an ISO date, got: {gate.get('status_date')!r}"
        )

    gate_sha = gate.get("release_sha")
    if not isinstance(gate_sha, str) or not SHA_PATTERN.fullmatch(gate_sha):
        errors.append(
            f"{gate_id}.release_sha must be an exact 40-character lowercase git SHA, "
            f"got: {gate_sha!r}"
        )
    elif candidate_sha is not None and gate_sha != candidate_sha:
        errors.append(
            f"{gate_id}.release_sha {gate_sha} does not match the release candidate "
            f"{candidate_sha}; re-attest the gate against the current candidate"
        )

    required_checks = gate.get("required_checks")
    if not isinstance(required_checks, list) or not required_checks:
        errors.append(f"{gate_id}.required_checks must be a non-empty list")
    elif not all(is_nonempty_str(check) for check in required_checks):
        errors.append(f"{gate_id}.required_checks entries must be non-empty strings")

    blockers = gate.get("blockers")
    if not isinstance(blockers, list):
        errors.append(f"{gate_id}.blockers must be a list")
        blockers = []
    elif not all(is_nonempty_str(blocker) for blocker in blockers):
        errors.append(f"{gate_id}.blockers entries must be non-empty strings")

    validate_evidence(gate_id, gate.get("evidence"), root, errors)
    passing_receipts = validate_receipts(
        gate_id, gate.get("receipts"), candidate_sha, root, errors
    )
    receipts = gate.get("receipts") if isinstance(gate.get("receipts"), list) else []
    failed_receipts = [
        receipt
        for receipt in receipts
        if isinstance(receipt, dict) and receipt.get("result") == "fail"
    ]
    evidence = gate.get("evidence") if isinstance(gate.get("evidence"), list) else []

    if status in OPEN_STATUSES and not blockers:
        errors.append(
            f"{gate_id} is {status} and must name at least one blocker so the registry "
            "says what to fix"
        )

    if status == "not-applicable":
        if not is_nonempty_str(gate.get("justification")):
            errors.append(
                f"{gate_id} is not-applicable and requires a non-empty justification"
            )
        if blockers:
            errors.append(f"{gate_id} is not-applicable and must not carry blockers")

    if status in ("passed", "passed-with-deviation"):
        if not evidence:
            errors.append(f"{gate_id} is {status} and requires at least one evidence entry")
        if passing_receipts == 0:
            errors.append(
                f"{gate_id} is {status} but has no passing receipt bound to release SHA "
                f"{candidate_sha}"
            )
        if failed_receipts:
            errors.append(f"{gate_id} is {status} but carries a failing receipt")
        if blockers:
            errors.append(f"{gate_id} is {status} and must not carry open blockers")

    if status == "passed-with-deviation":
        deviation = gate.get("deviation")
        if not isinstance(deviation, dict):
            errors.append(
                f"{gate_id} is passed-with-deviation and requires a deviation object"
            )
        else:
            for key in REQUIRED_DEVIATION_KEYS:
                if key not in deviation:
                    errors.append(f"{gate_id}.deviation missing required field: {key}")
            if not is_nonempty_str(deviation.get("description")):
                errors.append(f"{gate_id}.deviation.description must be a non-empty string")
            if not is_nonempty_str(deviation.get("approver")):
                errors.append(f"{gate_id}.deviation.approver must be a non-empty string")
            if not is_valid_date(deviation.get("review_by")):
                errors.append(f"{gate_id}.deviation.review_by must be an ISO date")


def validate_registry(registry: dict[str, Any], root: Path = ROOT) -> list[str]:
    """Return every integrity error in the registry. Empty list means consistent."""
    errors: list[str] = []

    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in registry:
            errors.append(f"registry missing required field: {key}")

    schema_version = registry.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            f"registry.schema_version must be one of {list(SUPPORTED_SCHEMA_VERSIONS)}, "
            f"got: {schema_version!r}"
        )
    if not is_nonempty_str(registry.get("registry_id")):
        errors.append("registry.registry_id must be a non-empty string")
    if not is_nonempty_str(registry.get("task_id")):
        errors.append("registry.task_id must be a non-empty string")
    if not is_valid_date(registry.get("generated_at")):
        errors.append(
            f"registry.generated_at must be an ISO date, got: {registry.get('generated_at')!r}"
        )

    candidate_sha = validate_release(registry.get("release"), errors)

    gates = registry.get("gates")
    if not isinstance(gates, list):
        errors.append("registry.gates must be a list")
        return errors

    if len(gates) != GATE_COUNT:
        errors.append(
            f"registry.gates must declare exactly {GATE_COUNT} gates (gate-0 through "
            f"gate-{GATE_COUNT - 1}), got: {len(gates)}"
        )

    seen_ids: set[str] = set()
    for position, gate in enumerate(gates[:GATE_COUNT]):
        validate_gate(gate, position, candidate_sha, root, errors)
        if isinstance(gate, dict) and is_nonempty_str(gate.get("id")):
            if gate["id"] in seen_ids:
                errors.append(f"duplicate gate id: {gate['id']}")
            seen_ids.add(gate["id"])

    for position, gate in enumerate(gates[GATE_COUNT:], start=GATE_COUNT):
        gate_id = gate.get("id") if isinstance(gate, dict) else None
        errors.append(f"unexpected extra gate at index {position}: {gate_id!r}")

    release = registry.get("release")
    if isinstance(release, dict) and release.get("decision") == "go":
        open_gates = blocking_gates(registry)
        if open_gates:
            errors.append(
                "release.decision is 'go' but these gates are not cleared: "
                f"{open_gates}"
            )

    return errors


def build_report(registry: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    release = registry.get("release") if isinstance(registry.get("release"), dict) else {}
    gates = registry.get("gates") if isinstance(registry.get("gates"), list) else []
    open_gates = blocking_gates(registry)
    return {
        "registry_id": registry.get("registry_id"),
        "candidate_sha": release.get("candidate_sha"),
        "decision": release.get("decision"),
        "gate_count": len(gates),
        "cleared_gates": [
            gate.get("id") for gate in gates if is_cleared(gate)
        ],
        "blocking_gates": open_gates,
        "integrity_errors": errors,
        "release_state": "GO" if release.get("decision") == "go" and not open_gates else "NO-GO",
    }


def print_report(report: dict[str, Any], registry: dict[str, Any]) -> None:
    print(f"Release gate registry: {report['registry_id']}")
    print(f"Release candidate SHA: {report['candidate_sha']}")
    print(f"Recorded decision: {report['decision']}")
    print(f"Gates cleared: {len(report['cleared_gates'])}/{report['gate_count']}")

    gates = registry.get("gates") if isinstance(registry.get("gates"), list) else []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        marker = "PASS" if is_cleared(gate) else "OPEN"
        print(f"- [{marker}] {gate.get('id')} {gate.get('name')}: {gate.get('status')}")
        for blocker in gate.get("blockers") or []:
            print(f"    blocker: {blocker}")

    print(f"RELEASE STATE: {report['release_state']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the Gate 0-6 machine-readable release registry."
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=REGISTRY_PATH,
        help="path to the registry JSON (default: docs/evidence/gates/RELEASE_GATE_REGISTRY.json)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root used to resolve evidence and receipt paths",
    )
    parser.add_argument(
        "--expected-sha",
        help="fail unless release.candidate_sha equals this exact SHA",
    )
    parser.add_argument(
        "--require-go",
        action="store_true",
        help="fail unless every gate is cleared and the recorded decision is 'go'",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the machine-readable report instead of the human summary",
    )
    args = parser.parse_args(argv)

    try:
        registry = load_registry(args.registry)
    except RegistryLoadError as exc:
        print("Release gate registry check failed:")
        print(f"- {exc}")
        return 1

    errors = validate_registry(registry, args.root)

    if args.expected_sha:
        release = registry.get("release") if isinstance(registry.get("release"), dict) else {}
        actual_sha = release.get("candidate_sha")
        if actual_sha != args.expected_sha:
            errors.append(
                f"release.candidate_sha {actual_sha!r} does not match --expected-sha "
                f"{args.expected_sha!r}"
            )

    report = build_report(registry, errors)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(report, registry)
        if errors:
            print("Release gate registry integrity errors:")
            for error in errors:
                print(f"- {error}")

    if errors:
        return 1

    if args.require_go and report["release_state"] != "GO":
        if not args.json:
            print(
                "Release is NO-GO: --require-go was requested but the registry does not "
                "record a cleared GO state."
            )
        return 1

    if not args.json:
        print("Release gate registry checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
