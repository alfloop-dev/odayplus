"""Release harness tests for ODP-INTAKE-RELEASE-001.

Covers the fail-closed governance gates and the runtime drills the release
harness executes: config drift detection, feature-flag governance proofs,
production-surrogate rejection, migration reconciliation + scoped rollback,
shadow acceptance metrics, §5.2 kill-switch mechanism, §4 restore order,
canary ladder blocking, and the governed cutover gate.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from scripts.release.assisted_listing_intake import drills
from scripts.release.assisted_listing_intake.config import (
    INFRA_DIR,
    ReleaseConfigError,
    load_release_config,
)
from scripts.release.assisted_listing_intake.gates import (
    check_feature_flags,
    check_production_readiness,
    check_release_authority,
)


@pytest.fixture(scope="module")
def config():
    return load_release_config()


@pytest.fixture()
def infra_copy(tmp_path: Path) -> Path:
    target = tmp_path / "infra"
    shutil.copytree(INFRA_DIR, target)
    return target


# ---------------------------------------------------------------------------
# Config fail-closed
# ---------------------------------------------------------------------------

def test_release_config_loads_and_validates(config) -> None:
    assert len(config.release_authority["gates"]) == 9
    assert len(config.canary_plan["write_canary_units"]) == 7
    assert len(config.rollback_triggers["mechanism_order"]) == 8


def test_missing_config_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ReleaseConfigError, match="missing governance config"):
        load_release_config(tmp_path)


def test_tampered_enabled_flag_fails_closed(infra_copy: Path) -> None:
    """A flag flipped on without dual approval must fail at load time."""
    flags_path = infra_copy / "feature_flags.yaml"
    manifest = yaml.safe_load(flags_path.read_text())
    manifest["flags"][0]["enabled"] = True
    flags_path.write_text(yaml.safe_dump(manifest))
    config = load_release_config(infra_copy)
    with pytest.raises(ValueError, match="cannot be enabled without"):
        check_feature_flags(config)


def test_approved_row_without_evidence_is_drift(infra_copy: Path) -> None:
    """An 'approved' §12 row missing approver/evidence is automation drift."""
    authority_path = infra_copy / "release_authority.yaml"
    register = yaml.safe_load(authority_path.read_text())
    register["gates"][0]["status"] = "approved"  # no approver/evidence recorded
    authority_path.write_text(yaml.safe_dump(register))
    with pytest.raises(ReleaseConfigError, match="automation drift"):
        load_release_config(infra_copy)


def test_mechanism_order_cannot_be_truncated(infra_copy: Path) -> None:
    triggers_path = infra_copy / "rollback_triggers.yaml"
    register = yaml.safe_load(triggers_path.read_text())
    register["mechanism_order"] = register["mechanism_order"][:-1]
    triggers_path.write_text(yaml.safe_dump(register))
    with pytest.raises(ReleaseConfigError, match="mechanism_order"):
        load_release_config(infra_copy)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def test_release_authority_all_pending_blocks_cutover(config) -> None:
    report = check_release_authority(config)
    assert report["cutover_blocked"] is True
    assert len(report["pending_owners"]) == 9
    assert report["all_approved"] is False
    assert "All production flags off" in report["fail_closed_effects_active"]


def test_feature_flags_disabled_and_governed(config) -> None:
    report = check_feature_flags(config)
    assert report["passed"] is True
    assert report["enabled_production_flags"] == []
    assert report["load_time_rejection_proved"] is True
    assert all(report["runtime_enable_rejected_without_dual_approval"].values())


def test_production_readiness_rejects_surrogates() -> None:
    report = check_production_readiness()
    assert report["passed"] is True
    by_name = {p["name"]: p for p in report["proofs"]}
    assert by_name["memory_persistence_is_surrogate"]["production_ready"] is False
    assert by_name["sqlite_durable_mode_is_staging_surrogate"]["production_ready"] is False
    assert by_name["unset_persistence_env_falls_back_to_memory"]["passed"] is True
    assert by_name["gcs_without_credentials_fails_closed"]["passed"] is True


# ---------------------------------------------------------------------------
# Drills
# ---------------------------------------------------------------------------

def test_migration_reconciliation_drill(tmp_path: Path) -> None:
    result = drills.run_migration_reconciliation(tmp_path / "migration", rows_per_tenant=3)
    assert result["passed"] is True
    assert result["blocking_findings"] == 0
    assert result["tenant_isolation"]["cross_tenant_id_overlap"] == 0
    assert result["rollback_proof"]["rows_after"] == 0
    assert result["rollback_proof"]["other_tenant_rows_untouched"] == 3
    # rollback ran on an isolated copy; the staging record stays intact
    assert result["rollback_proof"]["staging_record_untouched"] == 3


def test_shadow_drill_meets_acceptance(config, tmp_path: Path) -> None:
    result = drills.run_shadow_drill(config, tmp_path / "shadow", volume=12)
    assert result["passed"] is True, result["checks"]
    metrics = result["metrics"]
    assert metrics["ambiguous_auto_merges"] == 0
    assert metrics["automatic_candidate_promotions"] == 0
    assert metrics["tenant_scope_isolation_pass_rate"] == 1.0
    assert metrics["unknown_blocked_sources_fail_closed_rate"] == 1.0
    assert metrics["snapshot_checksum_reconciliation_rate"] == 1.0
    assert metrics["audit_outbox_loss"] == 0
    assert result["audit_chain_valid"] is True
    # honesty marker: production 7d/10k window remains a release gate
    assert result["not_executed_targets"][0]["release_gate"] is True


def test_killswitch_and_restore_drills(config, tmp_path: Path) -> None:
    killswitch = drills.run_killswitch_rollback(config, tmp_path / "killswitch")
    assert killswitch["passed"] is True
    assert killswitch["trigger_detected"] is True  # real checksum mismatch
    assert killswitch["evidence_packet_missing_fields"] == []
    steps = {s["step"]: s for s in killswitch["mechanism_steps"]}
    assert len(steps) == 8
    assert steps[1]["new_enqueue_refused"] is True
    assert steps[3]["stale_fence_rejected"] is True
    assert steps[4]["unpublished_rows_retained"] == 2

    migration = drills.run_migration_reconciliation(tmp_path / "migration", rows_per_tenant=3)
    restore = drills.run_restore_drill(
        tmp_path / "restore", killswitch_result=killswitch, migration_result=migration
    )
    assert restore["passed"] is True, restore["steps"]
    assert restore["unresolved_differences"] == 0
    restore_steps = {s["step"]: s for s in restore["steps"]}
    assert len(restore_steps) == 9  # full §4 order
    assert restore_steps[2]["table_checksums_match"] is True
    assert restore_steps[3]["audit_chain_valid"] is True
    assert restore_steps[5]["redirect_cycles"] == 0
    assert restore_steps[6]["duplicate_active_candidates"] == 0
    assert restore_steps[7]["duplicate_idempotency_keys"] == 0


def test_restore_detects_tampered_copy(config, tmp_path: Path) -> None:
    """A corrupted staging record must surface as unresolved differences."""
    killswitch = drills.run_killswitch_rollback(config, tmp_path / "killswitch")
    migration = drills.run_migration_reconciliation(tmp_path / "migration", rows_per_tenant=3)
    # Tamper the staging record after the drill captured its checksums
    import sqlite3

    conn = sqlite3.connect(migration["staging_db"])
    try:
        conn.execute("DELETE FROM listing_revisions")
        conn.commit()
    finally:
        conn.close()
    # Recompute a stale checksum record so restore compares source-vs-copy: the
    # copy equals the tampered source, so step 2 passes, but step 6 (revision
    # pointers) must fail because listings now lack revisions.
    restore = drills.run_restore_drill(
        tmp_path / "restore", killswitch_result=killswitch, migration_result=migration
    )
    assert restore["passed"] is False
    assert restore["unresolved_differences"] >= 1


def test_write_canary_ladder_blocks_production_units(config, tmp_path: Path) -> None:
    shadow = {"passed": True}
    migration = {"blocking_findings": 0}
    killswitch = {"kill_switch_verified": True}
    authority = check_release_authority(config)
    result = drills.run_write_canary(
        config,
        tmp_path / "canary",
        shadow_result=shadow,
        migration_result=migration,
        killswitch_result=killswitch,
        authority_report=authority,
    )
    assert result["passed"] is True
    executed = [u for u in result["units"] if u.get("executed")]
    blocked = [u for u in result["units"] if u.get("blocked")]
    assert [u["unit"] for u in executed] == [1, 2]
    assert all(u["passed"] for u in executed)
    assert [u["unit"] for u in blocked] == [3, 4, 5, 6, 7]


def test_write_canary_halts_without_killswitch_proof(config, tmp_path: Path) -> None:
    """Unit 2's entry gate requires the kill-switch drill to have passed."""
    authority = check_release_authority(config)
    result = drills.run_write_canary(
        config,
        tmp_path / "canary",
        shadow_result={"passed": True},
        migration_result={"blocking_findings": 0},
        killswitch_result={"kill_switch_verified": False},
        authority_report=authority,
    )
    executed = [u["unit"] for u in result["units"] if u.get("executed")]
    assert executed == [1]
    assert result["passed"] is False


def test_uat_fails_closed_without_report(tmp_path: Path) -> None:
    result = drills.run_uat(tmp_path / "uat", report_path=None)
    assert result["passed"] is False


def test_uat_rejects_report_with_failures(tmp_path: Path) -> None:
    report = {
        "stats": {"unexpected": 1},
        "suites": [
            {
                "specs": [
                    {"title": "case", "ok": False, "file": "operator-assisted-listing-intake.spec.ts"}
                ]
            }
        ],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    result = drills.run_uat(tmp_path / "uat", report_path=path)
    assert result["passed"] is False
    assert result["failed_cases"] == ["case"]


def test_cutover_gate_blocks_while_pending(config, tmp_path: Path) -> None:
    from scripts.release.assisted_listing_intake.run import run_cutover_gate

    prior = {name: {"passed": True} for name in ("readiness", "migration", "shadow", "killswitch", "restore", "canary", "uat")}
    result = run_cutover_gate(config, tmp_path, prior)
    assert result["cutover_blocked"] is True
    assert result["production_flags_disabled"] is True
    assert result["passed"] is True  # correctly blocked == passing state

    # A failed drill must fail the gate even while blocked.
    prior["restore"] = {"passed": False}
    result = run_cutover_gate(config, tmp_path, prior)
    assert result["passed"] is False
    assert "restore" in result["failed_drills"]
