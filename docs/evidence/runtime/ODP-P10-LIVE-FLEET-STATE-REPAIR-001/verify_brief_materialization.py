#!/usr/bin/env python3
"""Prove that all 11 Package 10 live-closure tasks materialize an execution brief.

`common.execution_context_files()` fail-closes any P0 or `mutates_canonical`
task whose `source_docs` contain a reference that does not resolve *inside the
live status root checkout*. Owner and reviewer materialize from the same call,
so a task that raises here has no usable brief for either side and cannot be
dispatched. This is the operative proof for acceptance criterion 4, "owner and
reviewer source manifests match".

Note the resolution root: `validate_source_doc_path()` resolves against the
status root, which lags `dev`. A document merged to `dev` today is therefore not
citable as a `source_doc` until the status root advances; the Package 10
dispatch-authority documents dated 2026-08-09 are carried in
`target_context_paths` for exactly this reason.

Read-only. Usage:

    python3 verify_brief_materialization.py \
        --supervisor-root /home/lupin/oday-plus-supervisor-runtime-current \
        --status-root /home/lupin/oday-plus-supervisor-live
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

TASK_IDS = [
    ("T00", "ODP-P10-LIVE-FLEET-STATE-REPAIR-001"),
    ("T10", "ODP-P10-LIVE-EXTDATA-DIAG-001"),
    ("T11", "ODP-P10-LIVE-EXTDATA-REMEDIATE-001"),
    ("T20", "ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001"),
    ("T21", "ODP-PRODUCTION-MODEL-REGISTRY-001"),
    ("T30", "ODP-P10-DEV-REDEPLOY-VERIFY-001"),
    ("T40", "ODP-P10-LIVE-VISUAL-PARITY-001"),
    ("T41", "ODP-P10-LIVE-LEGACY-RETIREMENT-001"),
    ("T42", "ODP-PLAN-LIVE-STAGING-PROOF-001"),
    ("T50", "ODP-PLAN-UAT-SIGNOFF-001"),
    ("T60", "ODP-PLAN-FINAL-GATE-AUDIT-001"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--supervisor-root", required=True, type=Path)
    parser.add_argument("--status-root", required=True, type=Path)
    args = parser.parse_args()

    sys.path.insert(0, str(args.supervisor_root / ".orchestrator"))
    import common  # noqa: E402  -- resolved from --supervisor-root

    config = common.load_config_for_status_root(args.status_root)

    print(f"supervisor root: {args.supervisor_root}")
    print(f"status root:     {args.status_root}\n")

    passed = 0
    for order, task_id in TASK_IDS:
        try:
            files = common.execution_context_files(config, task_id)
        except Exception as exc:  # fail-closed materialization
            print(f"[FAIL] {order} {task_id}: {exc}")
            continue
        print(f"[PASS] {order} {task_id}: {len(files)} context files")
        passed += 1

    print(f"\n{passed}/{len(TASK_IDS)} tasks materialize without fail-closed")
    return 0 if passed == len(TASK_IDS) else 1


if __name__ == "__main__":
    sys.exit(main())
