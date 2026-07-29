#!/usr/bin/env python3
"""ODP-DEPLOY-JOB-RECEIPT-UPLOAD-001: what Deploy Dev uploads, before and after.

Re-runnable reviewer proof. Builds the `.odp_data/deployment` tree a green
deploy leaves on the runner, then evaluates two selections over it:

* OLD — the single non-recursive glob `.odp_data/deployment/*.json`
* NEW — the explicit allowlist, read out of `.github/workflows/deploy-dev.yml`
  rather than restated here, so this script cannot drift from the workflow

Three decoy files are included that do **not** exist today
(`api-env.json`, `sbom.json`, `cloud-run-jobs/worker-env-dump.json`). They
stand for the open-ended half of the defect: the old glob published whatever
JSON happened to be sitting at the top of that directory, reviewed or not.

Usage:  python3 docs/evidence/runtime/ODP-DEPLOY-JOB-RECEIPT-UPLOAD-001/\
simulate_artifact_selection.py
"""

from __future__ import annotations

import glob
import os
import pathlib
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[4]
WORKFLOW = ROOT / ".github/workflows/deploy-dev.yml"

# Written by scripts/deploy_cloud_run_waji.sh on a green deploy.
VALIDATOR_REPORTS = (
    ".odp_data/deployment/cloud-run-preflight.json",
    ".odp_data/deployment/cloud-run-smoke.json",
    ".odp_data/deployment/cloud-run-migration-compatibility.json",
    ".odp_data/deployment/live-e2e-gate.json",
)
JOB_RECEIPTS = tuple(
    f".odp_data/deployment/cloud-run-jobs/{kind}-validation.json"
    for kind in ("migration", "scheduler", "worker")
)
RAW_GCLOUD_DUMPS = tuple(
    f".odp_data/deployment/cloud-run-jobs/{kind}-{dump}.json"
    for kind in ("migration", "scheduler", "worker")
    for dump in ("job", "execution", "execution-list")
)
DECOYS = (
    ".odp_data/deployment/api-env.json",
    ".odp_data/deployment/sbom.json",
    ".odp_data/deployment/cloud-run-jobs/worker-env-dump.json",
)


def workflow_allowlist() -> list[str]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["deploy"]["steps"]
    step = next(
        s
        for s in steps
        if isinstance(s, dict) and str(s.get("uses", "")).startswith("actions/upload-artifact@")
    )
    return [line.strip() for line in step["with"]["path"].splitlines() if line.strip()]


def main() -> int:
    present = VALIDATOR_REPORTS + JOB_RECEIPTS + RAW_GCLOUD_DUMPS + DECOYS
    allowlist = workflow_allowlist()

    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        for rel in present:
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")

        old = sorted(
            os.path.relpath(match, base)
            for match in glob.glob(str(base / ".odp_data/deployment/*.json"))
        )
        new = sorted(rel for rel in allowlist if (base / rel).is_file())

    print(f"on the runner after a green deploy: {len(present)} files\n")
    print(f"OLD  path: .odp_data/deployment/*.json  -> {len(old)} uploaded")
    for name in old:
        print(f"   + {name}")
    print(f"\nNEW  explicit allowlist ({len(allowlist)} entries) -> {len(new)} uploaded")
    for name in new:
        print(f"   + {name}")
    print("\nNEVER uploaded by the new allowlist:")
    for rel in sorted(set(present) - set(new)):
        print(f"   - {rel}")

    # 1. Every Job receipt the old glob dropped is now published. This is the
    #    ODP-DEPLOY-JOB-RECEIPT-UPLOAD-001 defect.
    missing_receipts = sorted(set(JOB_RECEIPTS) - set(new))
    assert not missing_receipts, missing_receipts
    assert not set(JOB_RECEIPTS) & set(old), "the old glob was supposed to miss these"

    # 2. No validator report the old glob published was lost.
    assert set(VALIDATOR_REPORTS) <= set(new), sorted(set(VALIDATOR_REPORTS) - set(new))

    # 3. No raw gcloud describe output, and no unreviewed newcomer.
    assert not set(RAW_GCLOUD_DUMPS) & set(new), sorted(set(RAW_GCLOUD_DUMPS) & set(new))
    assert not set(DECOYS) & set(new), sorted(set(DECOYS) & set(new))

    # 4. The decoys show the old glob's open end: it published two files nobody
    #    reviewed. The allowlist is closed by construction.
    swept_by_old = sorted(set(DECOYS) & set(old))
    print(f"\nunreviewed files the OLD glob would have published: {swept_by_old}")

    print(
        "\nASSERTIONS OK: 3 Job receipts recovered, 4 validator reports preserved, "
        "9 raw gcloud dumps and 3 decoys excluded."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
