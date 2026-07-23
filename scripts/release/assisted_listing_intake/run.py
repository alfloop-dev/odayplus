#!/usr/bin/env python3
"""Assisted Listing Intake v1 release drill / gate harness (ODP-INTAKE-RELEASE-001).

Executes the governed release phases in the required order and emits JSON
evidence per phase plus a summary report:

    readiness   §12 fail-closed gates, flag governance, surrogate rejection
    migration   staging backfill → reconciliation → scoped rollback proof
    shadow      shadow processing canary metrics (runbook §4 Phase 4)
    killswitch  rollback trigger + §5.2 mechanism order drill
    restore     reliability contract §4 restore order (runs after rollback)
    canary      tenant/source write canary ladder (units 3+ must be BLOCKED)
    uat         role-based operator UAT (Playwright report ingestion)
    cutover     governed cutover gate — must be BLOCKED while §12 is pending

The harness fails closed: any pending approval, enabled production flag,
governance-config drift, or missing runtime evidence blocks the cutover
phase and exits nonzero on drift.

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
        check_release_authority,
    )

    authority = check_release_authority(config)
    flags = check_feature_flags(config)

    drill_gates = {
        name: bool(result.get("passed"))
        for name, result in phase_results.items()
        if name not in ("cutover",)
    }
    blocked_reasons = []
    if authority["pending_owners"]:
        blocked_reasons.append(f"§12 approvals pending: {authority['pending_owners']}")
    if flags["enabled_production_flags"]:
        blocked_reasons.append(f"production flags enabled prematurely: {flags['enabled_production_flags']}")
    blocked_reasons.append("no live staging runtime evidence recorded (staging environment not provisioned)")

    failed_drills = [name for name, ok in drill_gates.items() if not ok]
    cutover_blocked = bool(blocked_reasons)

    # The correct, expected state today is BLOCKED with flags off and all
    # drill evidence recorded. Drift (an enabled flag, an "approved" row
    # without evidence, a failed drill) fails the phase.
    result = {
        "phase": "cutover",
        "checked_at": _now(),
        "cutover_blocked": cutover_blocked,
        "blocked_reasons": blocked_reasons,
        "drill_gates": drill_gates,
        "failed_drills": failed_drills,
        "release_authority": {
            "pending_owners": authority["pending_owners"],
            "fail_closed_effects_active": authority["fail_closed_effects_active"],
        },
        "production_flags_disabled": not flags["enabled_production_flags"],
        "rule": config.release_authority.get("cutover_rule"),
        "passed": cutover_blocked and not flags["enabled_production_flags"] and not failed_drills,
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
                from scripts.release.assisted_listing_intake.gates import check_release_authority

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

    failed = [name for name, result in results.items() if not result.get("passed")]
    summary = {
        "task": "ODP-INTAKE-RELEASE-001",
        "design_ref": "ODP-SD-INTAKE-001 v0.2.1",
        "generated_at": _now(),
        "phases_run": list(results),
        "phase_status": {name: bool(result.get("passed")) for name, result in results.items()},
        "failed_phases": failed,
        "cutover_blocked": results.get("cutover", {}).get("cutover_blocked"),
        "production_ready": False,
        "not_executed_targets": [
            {
                "target": "live_staging_and_production_canary",
                "reason": "No live staging environment is provisioned (Human/Ops gate); production units of the canary ladder stay BLOCKED by §12 pending approvals.",
                "release_gate": True,
            }
        ],
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
