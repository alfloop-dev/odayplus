#!/usr/bin/env python3
"""Assisted Listing Intake v1 release drill / gate harness (ODP-INTAKE-RELEASE-001).

Executes the governed release phases in the required order and emits JSON
evidence per phase plus a summary report:

    readiness   §12 fail-closed gates, flag governance, surrogate rejection
    migration   staging backfill → reconciliation → scoped rollback proof
    shadow      shadow processing canary metrics (runbook §4 Phase 4)
    killswitch  rollback trigger + §5.2 mechanism order drill
    restore     reliability contract §4 restore order (runs after rollback)
    canary      tenant/source write canary ladder (units 3+ BLOCKED until
                their exact entry gates — §12 approvals + live evidence —
                pass; passed only via recorded live results)
    uat         role-based operator UAT (Playwright report ingestion)
    cutover     governed cutover gate — BLOCKED while any §12 row is
                pending, live runtime evidence is unrecorded, or production
                canary units 3-7 lack current passing evidence; AUTHORIZED
                only after every approval and the complete live ladder pass

The harness fails closed: any pending approval, enabled production flag,
governance-config drift, missing live runtime evidence, or an incomplete
production canary ladder blocks the cutover phase and exits nonzero on
drift. Live evidence is supplied only through the governed, schema-validated register
infra/assisted-listing-intake/live_runtime_evidence.yaml (human-recorded
via task PR — there is no CLI override).

Usage:
    python3 scripts/release/assisted_listing_intake/run.py --phase all \
        --output-dir docs/evidence/completion/ODP-INTAKE-RELEASE-001
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.release.assisted_listing_intake.config import (  # noqa: E402
    ReleaseConfigError,
    load_release_config,
)

PHASE_ORDER = (
    "readiness",
    "migration",
    "shadow",
    "killswitch",
    "restore",
    "canary",
    "uat",
    "cutover",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write(output_dir: Path, name: str, payload: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{name}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _load_existing(output_dir: Path, name: str) -> dict[str, Any] | None:
    path = output_dir / f"{name}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def run_readiness(config, output_dir: Path) -> dict[str, Any]:
    from scripts.release.assisted_listing_intake.gates import (
        check_feature_flags,
        check_production_readiness,
        check_release_authority,
    )

    authority = check_release_authority(config)
    flags = check_feature_flags(config)
    production = check_production_readiness()
    result = {
        "phase": "readiness",
        "checked_at": _now(),
        "release_authority": authority,
        "feature_flags": flags,
        "production_readiness": production,
        # Readiness passes when the fail-closed state is intact: flags off
        # and governed, surrogates provably rejected, cutover blocked while
        # any §12 row is pending.
        "passed": flags["passed"] and production["passed"] and authority["cutover_blocked"] == bool(authority["pending_owners"]),
    }
    _write(output_dir, "readiness", result)
    return result


def run_cutover_gate(config, output_dir: Path, phase_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    from scripts.release.assisted_listing_intake.gates import (
        check_feature_flags,
        check_live_runtime_evidence,
        check_release_authority,
    )

    authority = check_release_authority(config)
    flags = check_feature_flags(config)
    live = check_live_runtime_evidence(config)

    drill_gates = {
        name: bool(result.get("passed"))
        for name, result in phase_results.items()
        if name not in ("cutover",)
    }
    failed_drills = [name for name, ok in drill_gates.items() if not ok]
    canary = phase_results.get("canary") or {}
    canary_drill_complete = canary.get("production_ladder_complete") is True
    live_register_complete = live["production_canary_complete"] is True
    production_ladder_complete = canary_drill_complete and live_register_complete
    canary_evidence_current = (
        canary.get("live_evidence_digest") == live["evidence_digest"]
    )

    # Runbook §4: cutover unblocks only when every §12 row is approved AND
    # live staging runtime evidence is recorded in the governed register AND
    # the current evidence proves every production canary unit through unit 7.
    # Each blocker below is a real unmet gate — none is unconditional.
    blocked_reasons = []
    if authority["pending_owners"]:
        blocked_reasons.append(f"§12 approvals pending: {authority['pending_owners']}")
    if not live["recorded"]:
        blocked_reasons.append(
            "no live staging runtime evidence recorded in "
            "infra/assisted-listing-intake/live_runtime_evidence.yaml "
            f"(missing targets: {live['missing_targets']})"
        )
    if live["recorded"] and not live_register_complete:
        blocked_reasons.append(
            "production canary register incomplete: current governed evidence does "
            "not prove an intact error budget and recorded passing results for "
            f"units 3-7 (missing={live['missing_canary_units']}, "
            f"failed={live['failed_canary_units']})"
        )
    if live["recorded"] and live_register_complete and not canary_drill_complete:
        blocked_reasons.append(
            "production canary drill incomplete: rerun --phase canary through unit 7"
        )
    if live["recorded"] and live_register_complete and not canary_evidence_current:
        blocked_reasons.append(
            "production canary evidence is stale: rerun --phase canary against "
            "the current live_runtime_evidence.yaml register"
        )
    # Flags may only be enabled (via dual approval) once every §12 row is
    # approved and live evidence exists; earlier than that is drift.
    release_prerequisites_incomplete = (
        bool(authority["pending_owners"])
        or not live["recorded"]
        or not production_ladder_complete
        or not canary_evidence_current
    )
    premature_flags = (
        flags["enabled_production_flags"] if release_prerequisites_incomplete else []
    )
    if premature_flags:
        blocked_reasons.append(f"production flags enabled prematurely: {premature_flags}")
    if failed_drills:
        blocked_reasons.append(f"failed drill phases: {failed_drills}")

    cutover_blocked = bool(blocked_reasons)
    cutover_authorized = not cutover_blocked

    # Two valid passing states: (a) BLOCKED fail-closed with flags off and
    # every drill green while gates are pending; (b) AUTHORIZED when every
    # §12 row is approved, live runtime evidence is recorded, and every
    # drill is green. Drift (a premature flag, an "approved" row without
    # evidence, a failed drill) fails the phase in either state.
    result = {
        "phase": "cutover",
        "checked_at": _now(),
        "cutover_blocked": cutover_blocked,
        "cutover_authorized": cutover_authorized,
        "blocked_reasons": blocked_reasons,
        "drill_gates": drill_gates,
        "failed_drills": failed_drills,
        "release_authority": {
            "pending_owners": authority["pending_owners"],
            "all_approved": authority["all_approved"],
            "fail_closed_effects_active": authority["fail_closed_effects_active"],
        },
        "live_runtime_evidence": {
            "register": live["register"],
            "recorded": live["recorded"],
            "recorded_by": live["recorded_by"],
            "evidence_ref": live["evidence_ref"],
            "missing_targets": live["missing_targets"],
        },
        "production_canary": {
            "ladder_complete": production_ladder_complete,
            "drill_complete": canary_drill_complete,
            "register_complete": live_register_complete,
            "evidence_current": canary_evidence_current,
            "evidence_digest": canary.get("live_evidence_digest"),
            "missing_units": live["missing_canary_units"],
            "failed_units": live["failed_canary_units"],
            "error_budget_intact": live["error_budget_intact"],
        },
        "production_flags_disabled": not flags["enabled_production_flags"],
        "rule": config.release_authority.get("cutover_rule"),
        "passed": not premature_flags and not failed_drills,
    }
    _write(output_dir, "cutover", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phase", choices=(*PHASE_ORDER, "all"), default="all")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "docs/evidence/completion/ODP-INTAKE-RELEASE-001",
        help="Evidence output directory",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Scratch directory for drill databases (default: temp dir)",
    )
    parser.add_argument(
        "--uat-report",
        type=Path,
        default=None,
        help="Playwright JSON report for the UAT phase",
    )
    parser.add_argument(
        "--infra-dir",
        type=Path,
        default=None,
        help="Override the release governance config directory (tests only)",
    )
    args = parser.parse_args(argv)

    try:
        config = load_release_config(args.infra_dir)
    except ReleaseConfigError as exc:
        print(f"RELEASE CONFIG DRIFT (fail closed): {exc}", file=sys.stderr)
        return 2

    output_dir = args.output_dir
    phases = PHASE_ORDER if args.phase == "all" else (args.phase,)

    scratch_ctx = None
    if args.work_dir is None:
        scratch_ctx = tempfile.TemporaryDirectory(prefix="intake-release-drill-")
        work_dir = Path(scratch_ctx.name)
    else:
        work_dir = args.work_dir
        work_dir.mkdir(parents=True, exist_ok=True)

    from scripts.release.assisted_listing_intake import drills

    results: dict[str, dict[str, Any]] = {}
    exit_code = 0
    try:
        for phase in phases:
            if phase == "readiness":
                results["readiness"] = run_readiness(config, output_dir)
            elif phase == "migration":
                results["migration"] = drills.run_migration_reconciliation(work_dir / "migration")
                _write(output_dir, "migration", results["migration"])
            elif phase == "shadow":
                results["shadow"] = drills.run_shadow_drill(config, work_dir / "shadow")
                _write(output_dir, "shadow", results["shadow"])
            elif phase == "killswitch":
                results["killswitch"] = drills.run_killswitch_rollback(config, work_dir / "killswitch")
                _write(output_dir, "killswitch", results["killswitch"])
            elif phase == "restore":
                killswitch = results.get("killswitch") or _load_existing(output_dir, "killswitch")
                migration = results.get("migration") or _load_existing(output_dir, "migration")
                if not killswitch or not migration:
                    print(
                        "restore drill requires killswitch + migration results (run those phases first)",
                        file=sys.stderr,
                    )
                    return 2
                results["restore"] = drills.run_restore_drill(
                    work_dir / "restore", killswitch_result=killswitch, migration_result=migration
                )
                _write(output_dir, "restore", results["restore"])
            elif phase == "canary":
                from scripts.release.assisted_listing_intake.gates import (
                    check_live_runtime_evidence,
                    check_release_authority,
                )

                shadow = results.get("shadow") or _load_existing(output_dir, "shadow") or {}
                migration = results.get("migration") or _load_existing(output_dir, "migration") or {}
                killswitch = results.get("killswitch") or _load_existing(output_dir, "killswitch") or {}
                results["canary"] = drills.run_write_canary(
                    config,
                    work_dir / "canary",
                    shadow_result=shadow,
                    migration_result=migration,
                    killswitch_result=killswitch,
                    authority_report=check_release_authority(config),
                    live_evidence_report=check_live_runtime_evidence(config),
                )
                _write(output_dir, "canary", results["canary"])
            elif phase == "uat":
                results["uat"] = drills.run_uat(work_dir / "uat", report_path=args.uat_report)
                _write(output_dir, "uat", results["uat"])
            elif phase == "cutover":
                prior = {
                    name: (results.get(name) or _load_existing(output_dir, name) or {"passed": False})
                    for name in PHASE_ORDER
                    if name != "cutover"
                }
                results["cutover"] = run_cutover_gate(config, output_dir, prior)
    finally:
        if scratch_ctx is not None:
            scratch_ctx.cleanup()

    from scripts.release.assisted_listing_intake.gates import check_live_runtime_evidence

    live = check_live_runtime_evidence(config)
    failed = [name for name, result in results.items() if not result.get("passed")]
    not_executed = []
    if not live["recorded"]:
        not_executed.append(
            {
                "target": "live_staging_and_production_canary",
                "reason": (
                    "Live staging runtime evidence is not recorded in "
                    "infra/assisted-listing-intake/live_runtime_evidence.yaml "
                    "(Human/Ops gate); production units of the canary ladder stay "
                    "BLOCKED until §12 approvals plus live evidence are recorded."
                ),
                "release_gate": True,
            }
        )
    summary = {
        "task": "ODP-INTAKE-RELEASE-001",
        "design_ref": "ODP-SD-INTAKE-001 v0.2.1",
        "generated_at": _now(),
        "phases_run": list(results),
        "phase_status": {name: bool(result.get("passed")) for name, result in results.items()},
        "failed_phases": failed,
        "cutover_blocked": results.get("cutover", {}).get("cutover_blocked"),
        "cutover_authorized": results.get("cutover", {}).get("cutover_authorized"),
        "live_runtime_evidence_recorded": live["recorded"],
        "production_ready": bool(results.get("cutover", {}).get("cutover_authorized")) and not failed,
        "not_executed_targets": not_executed,
        "passed": not failed,
    }
    if args.phase == "all":
        _write(output_dir, "release-drill-report", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if failed:
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
