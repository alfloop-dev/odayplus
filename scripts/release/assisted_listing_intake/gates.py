"""Release-authority, feature-flag, and production-readiness gate checks.

Every check here is fail-closed and produces evidence rows: a gate either
proves the safe state at runtime (flags off, approvals pending ⇒ cutover
blocked, surrogates rejected as production) or reports drift and fails.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any

from scripts.release.assisted_listing_intake.config import (
    EXPECTED_FLAG_KEYS,
    REQUIRED_LIVE_TARGETS,
    ReleaseConfig,
)
from shared.auth.feature_flags import (
    DUAL_APPROVAL_MINIMUM,
    FeatureFlag,
    FeatureFlagRegistry,
    Readiness,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def build_intake_flag_registry(config: ReleaseConfig) -> FeatureFlagRegistry:
    """Load the manifest flags through the production governance engine.

    ``FeatureFlag.__post_init__`` rejects enabled-without-dual-approval at
    load time, so a tampered manifest (flag flipped on without approvals)
    raises here instead of silently propagating.
    """

    registry = FeatureFlagRegistry()
    for entry in config.feature_flags["flags"]:
        registry.register(
            FeatureFlag(
                key=entry["key"],
                owner=entry["owner"],
                enabled=bool(entry["enabled"]),
                readiness=Readiness(entry.get("readiness", "experimental")),
                high_risk=bool(entry.get("high_risk", True)),
                description=entry.get("description", ""),
                approved_by=frozenset(entry.get("approved_by", ())),
            )
        )
    return registry


def check_release_authority(config: ReleaseConfig) -> dict[str, Any]:
    """Evaluate the §12 approval register. Pending rows keep cutover blocked."""

    gates = config.release_authority["gates"]
    pending = [g["owner"] for g in gates if g["status"] != "approved"]
    rows = [
        {
            "owner": g["owner"],
            "status": g["status"],
            "approval_scope": g.get("approval_scope"),
            "fail_closed_effect": g.get("fail_closed_effect"),
            "approver": g.get("approver"),
            "approved_at": g.get("approved_at"),
            "evidence_ref": g.get("evidence_ref"),
        }
        for g in gates
    ]
    return {
        "checked_at": _now(),
        "register": str(config.infra_dir / "release_authority.yaml"),
        "gates": rows,
        "pending_owners": pending,
        "all_approved": not pending,
        "cutover_blocked": bool(pending),
        "fail_closed_effects_active": [
            g["fail_closed_effect"] for g in rows if g["status"] != "approved"
        ],
    }


def check_live_runtime_evidence(config: ReleaseConfig) -> dict[str, Any]:
    """Evaluate the governed live runtime evidence register.

    The register is already schema-validated fail-closed at load time
    (``_validate_live_evidence``): this check surfaces its state as gate
    facts. ``recorded`` is true only when a human release authority
    attested every required live target with its own evidence reference.
    Production canary units may pass only via a recorded live result here.
    """

    record = config.live_evidence
    recorded = bool(record.get("recorded"))
    targets = {t.get("target"): t for t in record.get("targets", [])}
    canary_units = {
        int(u["unit"]): {
            "passed": bool(u.get("passed")),
            "completed_at": u.get("completed_at"),
            "evidence_ref": u.get("evidence_ref"),
        }
        for u in (record.get("canary_units") or [])
    }
    return {
        "checked_at": _now(),
        "register": str(config.infra_dir / "live_runtime_evidence.yaml"),
        "recorded": recorded,
        "recorded_by": record.get("recorded_by"),
        "recorded_at": record.get("recorded_at"),
        "evidence_ref": record.get("evidence_ref"),
        "error_budget_intact": bool(record.get("error_budget_intact"))
        if recorded
        else None,
        "required_targets": list(REQUIRED_LIVE_TARGETS),
        "targets": {
            name: {
                "status": entry.get("status"),
                "completed_at": entry.get("completed_at"),
                "evidence_ref": entry.get("evidence_ref"),
            }
            for name, entry in targets.items()
        },
        "canary_units": canary_units,
        "missing_targets": [
            t
            for t in REQUIRED_LIVE_TARGETS
            if targets.get(t, {}).get("status") != "completed"
        ],
    }


def check_feature_flags(config: ReleaseConfig) -> dict[str, Any]:
    """Prove at runtime that every production flag is off and governed.

    Three live proofs against ``shared/auth/feature_flags.py``:

    1. the manifest loads with every flag disabled;
    2. constructing an enabled high-risk flag without dual approval raises;
    3. ``FeatureFlagRegistry.enable`` without >= 2 approvals raises
       ``PermissionError`` for every intake flag.
    """

    registry = build_intake_flag_registry(config)
    today = date.today()

    flag_rows = []
    enabled_flags = []
    for key in EXPECTED_FLAG_KEYS:
        flag = registry.get(key)
        active = registry.is_enabled(key, on=today)
        flag_rows.append(
            {
                "key": key,
                "owner": flag.owner if flag else None,
                "enabled": bool(flag.enabled) if flag else None,
                "active": active,
                "high_risk": bool(flag.high_risk) if flag else None,
            }
        )
        if active:
            enabled_flags.append(key)

    # Proof 2: enabled-without-dual-approval must be rejected at load time.
    try:
        FeatureFlag(key="assisted_intake_v1_probe", owner="release-harness", enabled=True, high_risk=True)
        load_time_rejection = False
    except ValueError:
        load_time_rejection = True

    # Proof 3: runtime enable without dual approval must be rejected per flag.
    enable_rejections = {}
    for key in EXPECTED_FLAG_KEYS:
        try:
            registry.enable(key, approvals=frozenset({"solo-approver"}))
            enable_rejections[key] = False
        except PermissionError:
            enable_rejections[key] = True
    # The probe registry is discarded; the manifest on disk is never mutated.

    passed = (
        not enabled_flags
        and load_time_rejection
        and all(enable_rejections.values())
    )
    return {
        "checked_at": _now(),
        "manifest": str(config.infra_dir / "feature_flags.yaml"),
        "governance_engine": "shared/auth/feature_flags.py",
        "dual_approval_minimum": DUAL_APPROVAL_MINIMUM,
        "flags": flag_rows,
        "enabled_production_flags": enabled_flags,
        "load_time_rejection_proved": load_time_rejection,
        "runtime_enable_rejected_without_dual_approval": enable_rejections,
        "passed": passed,
    }


def check_production_readiness() -> dict[str, Any]:
    """Prove that staging/CI surrogates cannot masquerade as production.

    Acceptance: "Verify no legacy, SQLite, memory, fixture, or silent
    fallback path is presented as production-ready." Each proof below is a
    live runtime check, and the phase report explicitly marks every
    surrogate as NOT production.
    """

    from shared.infrastructure.object_store.client import GcsObjectStore
    from shared.infrastructure.persistence.factory import build_persistence

    proofs: list[dict[str, Any]] = []

    # Proof: memory persistence is a surrogate (default fallback), never durable.
    memory_bundle = build_persistence(mode="memory")
    proofs.append(
        {
            "name": "memory_persistence_is_surrogate",
            "observed_mode": memory_bundle.mode,
            "is_durable": memory_bundle.is_durable,
            "production_ready": False,
            "passed": memory_bundle.mode == "memory" and not memory_bundle.is_durable,
            "note": "ODP_PERSISTENCE unset falls back to memory; production config must set a Cloud SQL DSN. Memory mode is CI/staging only.",
        }
    )

    # Proof: the unset-env default silently falls back to memory. The release
    # gate records this fallback hazard so it can never be presented as
    # production-ready.
    previous = os.environ.pop("ODP_PERSISTENCE", None)
    try:
        default_bundle = build_persistence()
        proofs.append(
            {
                "name": "unset_persistence_env_falls_back_to_memory",
                "observed_mode": default_bundle.mode,
                "production_ready": False,
                "passed": default_bundle.mode == "memory",
                "note": "Silent fallback exists at runtime; deployment gate must therefore verify ODP_PERSISTENCE explicitly before any production flag is enabled.",
            }
        )
    finally:
        if previous is not None:
            os.environ["ODP_PERSISTENCE"] = previous

    # Proof: SQLite durable mode is a staging surrogate, not Cloud SQL.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="intake-release-readiness-") as tmp:
        durable_bundle = build_persistence(mode="durable", db_path=f"{tmp}/probe.sqlite3")
        try:
            proofs.append(
                {
                    "name": "sqlite_durable_mode_is_staging_surrogate",
                    "observed_mode": durable_bundle.mode,
                    "is_durable": durable_bundle.is_durable,
                    "production_ready": False,
                    "passed": durable_bundle.is_durable,
                    "note": "Durable SQLite exercises the production adapters (queue/outbox/audit) but is not Cloud SQL PostgreSQL 16; production requires a DSN-backed deployment.",
                }
            )
        finally:
            durable_bundle.engine.close()

    # Proof: GCS object store without credentials fails closed on use.
    saved_env = {
        key: os.environ.pop(key, None)
        for key in ("GOOGLE_OAUTH_ACCESS_TOKEN", "ODP_AUDIT_WORM_GCS_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS")
    }
    try:
        store = GcsObjectStore()
        try:
            store.upload_object(
                tenant_id="00000000-0000-0000-0000-000000000001",
                bucket="taiwan-snapshots",
                key="tenants/00000000-0000-0000-0000-000000000001/probe",
                data=b"probe",
                content_type="text/plain",
            )
            gcs_fail_closed = False
        except Exception:
            gcs_fail_closed = True
    finally:
        for key, value in saved_env.items():
            if value is not None:
                os.environ[key] = value
    proofs.append(
        {
            "name": "gcs_without_credentials_fails_closed",
            "production_ready": False,
            "passed": gcs_fail_closed,
            "note": "GcsObjectStore raises without runtime credentials; WORM/high-risk mutations stay disabled (§12 Security fail-closed effect).",
        }
    )

    return {
        "checked_at": _now(),
        "proofs": proofs,
        "surrogates_marked_not_production": True,
        "passed": all(p["passed"] for p in proofs),
    }
