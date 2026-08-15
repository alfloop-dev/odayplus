"""Fail-closed loading of the release governance configs.

The four YAML files under ``infra/assisted-listing-intake/`` are the
machine-readable release record (feature flags, §12 approval register,
canary ladder, rollback triggers). The harness refuses to run when any
file is missing, malformed, or drifted from the shapes the drills rely
on — a broken governance record must block the release, not be skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
INFRA_DIR = REPO_ROOT / "infra" / "assisted-listing-intake"

EXPECTED_FLAG_KEYS = (
    "assisted_intake_v1_read",
    "assisted_intake_v1_shadow",
    "assisted_intake_v1_write",
    "assisted_intake_v1_events",
    "assisted_intake_v1_promotion",
)

# §12 owner rows that must exist in release_authority.yaml.
EXPECTED_AUTHORITY_OWNERS = (
    "Product / Expansion Ops",
    "Security",
    "Privacy",
    "Legal / Commercial",
    "Data owner",
    "Platform / SRE",
    "Expansion Engineering",
    "QA",
    "Release authority",
)

# §5.2 rollback mechanism must retain exactly this many ordered steps.
EXPECTED_MECHANISM_STEPS = 8

# Live runtime targets that must be completed (each with its own evidence
# reference) before the live-evidence register may claim `recorded: true`.
# These mirror the not_executed_targets the staging-surrogate drills declare.
REQUIRED_LIVE_TARGETS = (
    "production_shadow_window_7d_or_10k_rows",
    "live_staging_e2e_gcs_cloud_tasks_pubsub",
    "cloud_sql_pitr_and_regional_failover",
)

# Canary units that run in production and may only pass via a recorded live
# result in the live-evidence register (never via a surrogate execution).
PRODUCTION_CANARY_UNITS = (3, 4, 5, 6, 7)


class ReleaseConfigError(ValueError):
    """A governance config is missing or drifted; the release fails closed."""


@dataclass(frozen=True)
class ReleaseConfig:
    feature_flags: dict[str, Any]
    release_authority: dict[str, Any]
    canary_plan: dict[str, Any]
    rollback_triggers: dict[str, Any]
    live_evidence: dict[str, Any]
    infra_dir: Path


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReleaseConfigError(f"missing governance config: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - malformed yaml is exercised via tests
        raise ReleaseConfigError(f"malformed governance config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReleaseConfigError(f"governance config {path} must be a mapping")
    return data


def _validate_flags(manifest: dict[str, Any], path: Path) -> None:
    flags = manifest.get("flags")
    if not isinstance(flags, list) or not flags:
        raise ReleaseConfigError(f"{path}: 'flags' must be a non-empty list")
    keys = [f.get("key") for f in flags]
    missing = [k for k in EXPECTED_FLAG_KEYS if k not in keys]
    if missing:
        raise ReleaseConfigError(f"{path}: missing production flags {missing}")
    for flag in flags:
        for field in ("key", "owner", "enabled", "high_risk", "scope"):
            if field not in flag:
                raise ReleaseConfigError(f"{path}: flag {flag.get('key')!r} missing field {field!r}")


def _validate_authority(register: dict[str, Any], path: Path) -> None:
    gates = register.get("gates")
    if not isinstance(gates, list) or not gates:
        raise ReleaseConfigError(f"{path}: 'gates' must be a non-empty list")
    owners = [g.get("owner") for g in gates]
    missing = [o for o in EXPECTED_AUTHORITY_OWNERS if o not in owners]
    if missing:
        raise ReleaseConfigError(f"{path}: missing §12 owner rows {missing}")
    for gate in gates:
        status = gate.get("status")
        if status not in ("pending", "approved"):
            raise ReleaseConfigError(
                f"{path}: gate {gate.get('owner')!r} has invalid status {status!r}"
            )
        if status == "approved" and not (
            gate.get("approver") and gate.get("approved_at") and gate.get("evidence_ref")
        ):
            # An "approved" row without a human approver + timestamp + evidence
            # is automation drift — treat as a broken record, not an approval.
            raise ReleaseConfigError(
                f"{path}: gate {gate.get('owner')!r} approved without "
                "approver/approved_at/evidence_ref (automation drift)"
            )


def _validate_canary(plan: dict[str, Any], path: Path) -> None:
    if not isinstance(plan.get("shadow_acceptance"), dict):
        raise ReleaseConfigError(f"{path}: missing shadow_acceptance metrics")
    units = plan.get("write_canary_units")
    if not isinstance(units, list) or len(units) < 7:
        raise ReleaseConfigError(f"{path}: write_canary_units must list the 7-unit ladder")
    numbers = [u.get("unit") for u in units]
    if numbers != sorted(numbers) or len(set(numbers)) != len(numbers):
        raise ReleaseConfigError(f"{path}: canary units must be strictly ordered")


def _validate_triggers(register: dict[str, Any], path: Path) -> None:
    triggers = register.get("triggers")
    if not isinstance(triggers, list) or not triggers:
        raise ReleaseConfigError(f"{path}: 'triggers' must be a non-empty list")
    steps = register.get("mechanism_order")
    if not isinstance(steps, list) or len(steps) != EXPECTED_MECHANISM_STEPS:
        raise ReleaseConfigError(
            f"{path}: mechanism_order must retain the {EXPECTED_MECHANISM_STEPS} §5.2 steps"
        )
    ordering = [s.get("step") for s in steps]
    if ordering != list(range(1, EXPECTED_MECHANISM_STEPS + 1)):
        raise ReleaseConfigError(f"{path}: mechanism_order steps must be 1..{EXPECTED_MECHANISM_STEPS} in order")
    packet = register.get("evidence_packet", {}).get("required_fields")
    if not isinstance(packet, list) or not packet:
        raise ReleaseConfigError(f"{path}: evidence_packet.required_fields missing")


def _validate_live_evidence(record: dict[str, Any], path: Path) -> None:
    """Fail-closed validation of the live runtime evidence register.

    Mirrors the release-authority drift rules: a record that claims live
    evidence without a human attestation (recorded_by/recorded_at/
    evidence_ref), a completed target without its own evidence, or a
    recorded canary-unit result missing its fields is a broken governance
    record and must block the release rather than be skipped.
    """

    recorded = record.get("recorded")
    if not isinstance(recorded, bool):
        raise ReleaseConfigError(f"{path}: 'recorded' must be a boolean")

    targets = record.get("targets")
    if not isinstance(targets, list):
        raise ReleaseConfigError(f"{path}: 'targets' must be a list")
    by_target: dict[str, dict[str, Any]] = {}
    for entry in targets:
        name = entry.get("target")
        status = entry.get("status")
        if status not in ("pending", "completed"):
            raise ReleaseConfigError(
                f"{path}: target {name!r} has invalid status {status!r}"
            )
        if status == "completed" and not (entry.get("completed_at") and entry.get("evidence_ref")):
            raise ReleaseConfigError(
                f"{path}: target {name!r} completed without completed_at/evidence_ref "
                "(automation drift)"
            )
        by_target[name] = entry

    canary_units = record.get("canary_units")
    if canary_units is None:
        canary_units = []
    if not isinstance(canary_units, list):
        raise ReleaseConfigError(f"{path}: 'canary_units' must be a list")
    seen_units: set[int] = set()
    for entry in canary_units:
        unit = entry.get("unit")
        if unit not in PRODUCTION_CANARY_UNITS:
            raise ReleaseConfigError(
                f"{path}: canary_units entry {unit!r} is not a production unit "
                f"{PRODUCTION_CANARY_UNITS}"
            )
        if unit in seen_units:
            raise ReleaseConfigError(f"{path}: duplicate canary_units entry for unit {unit}")
        seen_units.add(unit)
        if not isinstance(entry.get("passed"), bool):
            raise ReleaseConfigError(f"{path}: canary unit {unit} missing boolean 'passed'")
        if not (entry.get("completed_at") and entry.get("evidence_ref")):
            raise ReleaseConfigError(
                f"{path}: canary unit {unit} recorded without completed_at/evidence_ref "
                "(automation drift)"
            )

    if not recorded:
        # A not-recorded register must not smuggle in live claims.
        completed = [n for n, e in by_target.items() if e.get("status") == "completed"]
        if completed:
            raise ReleaseConfigError(
                f"{path}: recorded is false but targets claim completed: {completed} "
                "(automation drift)"
            )
        if canary_units:
            raise ReleaseConfigError(
                f"{path}: recorded is false but canary_units results are present "
                "(automation drift)"
            )
        return

    # recorded: true — require the full human attestation.
    for field in ("recorded_by", "recorded_at", "evidence_ref"):
        if not record.get(field):
            raise ReleaseConfigError(
                f"{path}: recorded is true without {field!r} (automation drift)"
            )
    if not isinstance(record.get("error_budget_intact"), bool):
        raise ReleaseConfigError(
            f"{path}: recorded is true without boolean 'error_budget_intact'"
        )
    missing = [t for t in REQUIRED_LIVE_TARGETS if t not in by_target]
    if missing:
        raise ReleaseConfigError(f"{path}: missing required live targets {missing}")
    incomplete = [
        t for t in REQUIRED_LIVE_TARGETS if by_target[t].get("status") != "completed"
    ]
    if incomplete:
        raise ReleaseConfigError(
            f"{path}: recorded is true but required live targets are not completed: "
            f"{incomplete}"
        )


def load_release_config(infra_dir: Path | None = None) -> ReleaseConfig:
    base = infra_dir or INFRA_DIR
    flags_path = base / "feature_flags.yaml"
    authority_path = base / "release_authority.yaml"
    canary_path = base / "canary_plan.yaml"
    triggers_path = base / "rollback_triggers.yaml"
    live_evidence_path = base / "live_runtime_evidence.yaml"

    feature_flags = _load_yaml(flags_path)
    release_authority = _load_yaml(authority_path)
    canary_plan = _load_yaml(canary_path)
    rollback_triggers = _load_yaml(triggers_path)
    live_evidence = _load_yaml(live_evidence_path)

    _validate_flags(feature_flags, flags_path)
    _validate_authority(release_authority, authority_path)
    _validate_canary(canary_plan, canary_path)
    _validate_triggers(rollback_triggers, triggers_path)
    _validate_live_evidence(live_evidence, live_evidence_path)

    return ReleaseConfig(
        feature_flags=feature_flags,
        release_authority=release_authority,
        canary_plan=canary_plan,
        rollback_triggers=rollback_triggers,
        live_evidence=live_evidence,
        infra_dir=base,
    )
