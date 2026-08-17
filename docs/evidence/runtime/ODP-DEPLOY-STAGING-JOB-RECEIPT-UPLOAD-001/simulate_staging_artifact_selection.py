#!/usr/bin/env python3
"""ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001: what Deploy Staging uploads.

Re-runnable reviewer proof, and the generalised successor to
`docs/evidence/runtime/ODP-DEPLOY-JOB-RECEIPT-UPLOAD-001/
simulate_artifact_selection.py`: that one was hardwired to Deploy Dev, this one
takes the workflow as an argument and evaluates either.

It builds the tree a green deploy leaves on the runner, then evaluates two
selections over it:

* OLD -- the pre-fix path list, quoted from `deploy-staging.yml` at 88dae2e1:

      .odp_data/deployment/*.json
      .odp_data/remote-staging-proof/*.json

  The first entry is the same non-recursive glob Deploy Dev shipped. GitHub's
  upload-artifact `path` uses `@actions/glob`, where a bare `*` matches within
  one directory level only; `cloud-run-jobs/` is a level below.

* NEW -- the explicit allowlist, read out of the workflow rather than restated
  here, so this script cannot drift from what CI actually does.

Three decoy files are included that do **not** exist today
(`api-env.json`, `sbom.json`, `cloud-run-jobs/worker-env-dump.json`). They
stand for the open-ended half of the defect: the old glob published whatever
JSON happened to be sitting at the top of that directory, reviewed or not.

Usage:
    python3 docs/evidence/runtime/ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001/\
simulate_staging_artifact_selection.py [--workflow deploy-dev.yml] [--pre-fix]
"""

from __future__ import annotations

import argparse
import glob
import os
import pathlib
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[4]
WORKFLOW_DIR = ROOT / ".github/workflows"

# `github.run_id` for the simulated run. Deploy Staging names the remote proof
# after the run, so the upload path has to carry the same expansion.
RUN_ID = "30445252373"

# The path list deploy-staging.yml carried before this task, at 88dae2e1.
PRE_FIX_PATHS = (
    ".odp_data/deployment/*.json",
    ".odp_data/remote-staging-proof/*.json",
)

# Written by product_ops/deployment/deploy_cloud_run_waji.sh on a green deploy, in either
# environment: the script is env-agnostic and reads ODP_DEPLOY_ENV only for
# naming and gate expectations, not for which reports it emits.
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
# Written by delivery_toolchain/e2e/check_remote_staging_proof.py in Deploy Staging only.
STAGING_PROOF = (f".odp_data/remote-staging-proof/staging-{RUN_ID}.json",)
DECOYS = (
    ".odp_data/deployment/api-env.json",
    ".odp_data/deployment/sbom.json",
    ".odp_data/deployment/cloud-run-jobs/worker-env-dump.json",
)


def workflow_allowlist(workflow: pathlib.Path) -> list[str]:
    parsed = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    steps = [
        step
        for job in parsed["jobs"].values()
        for step in job["steps"]
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert len(steps) == 1, f"{workflow.name}: expected one upload step, got {len(steps)}"
    raw = steps[0]["with"]["path"]
    return [
        line.strip().replace("${{ github.run_id }}", RUN_ID)
        for line in raw.splitlines()
        if line.strip()
    ]


def select(base: pathlib.Path, patterns: list[str]) -> list[str]:
    """Approximate `actions/upload-artifact`'s path resolution.

    `*` matches within one directory level; `**` would recurse. Neither the old
    list nor the new one uses `**`, so plain `glob.glob` without `recursive=True`
    is the same semantics.
    """

    matched: set[str] = set()
    for pattern in patterns:
        for match in glob.glob(str(base / pattern)):
            if pathlib.Path(match).is_file():
                matched.add(os.path.relpath(match, base))
    return sorted(matched)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", default="deploy-staging.yml")
    parser.add_argument(
        "--pre-fix",
        action="store_true",
        help="evaluate PRE_FIX_PATHS as NEW too, reproducing the defect on a fixed tree",
    )
    args = parser.parse_args()

    workflow = WORKFLOW_DIR / args.workflow
    staging = args.workflow == "deploy-staging.yml"

    present = VALIDATOR_REPORTS + JOB_RECEIPTS + RAW_GCLOUD_DUMPS + DECOYS
    if staging:
        present += STAGING_PROOF
    allowlist = list(PRE_FIX_PATHS) if args.pre_fix else workflow_allowlist(workflow)

    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        for rel in present:
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")

        old = select(base, list(PRE_FIX_PATHS))
        new = select(base, allowlist)

    print(f"workflow: .github/workflows/{args.workflow}")
    print(f"on the runner after a green deploy: {len(present)} files\n")
    print(f"OLD  {' + '.join(PRE_FIX_PATHS)}  -> {len(old)} uploaded")
    for name in old:
        print(f"   + {name}")
    label = "PRE-FIX (replayed)" if args.pre_fix else "NEW  explicit allowlist"
    print(f"\n{label} ({len(allowlist)} entries) -> {len(new)} uploaded")
    for name in new:
        print(f"   + {name}")
    print("\nNOT uploaded by that list:")
    for rel in sorted(set(present) - set(new)):
        print(f"   - {rel}")

    dropped_receipts = sorted(set(JOB_RECEIPTS) - set(new))
    swept_dumps = sorted(set(RAW_GCLOUD_DUMPS) & set(new))
    swept_decoys = sorted(set(DECOYS) & set(new))

    if args.pre_fix:
        # Reproduction mode: assert the defect, so this branch fails loudly if
        # someone ever "fixes" it by editing the quoted historical list.
        assert dropped_receipts == sorted(JOB_RECEIPTS), dropped_receipts
        assert swept_decoys, "the old glob was open at the top level too"
        print(
            f"\nDEFECT REPRODUCED: {len(dropped_receipts)} Cloud Run Job receipts dropped, "
            f"{len(swept_decoys)} unreviewed top-level files published."
        )
        return 0

    # 1. Every Job receipt the old glob dropped is now published.
    assert not dropped_receipts, dropped_receipts
    assert not set(JOB_RECEIPTS) & set(old), "the old glob was supposed to miss these"

    # 2. No report the old glob published was lost.
    assert set(VALIDATOR_REPORTS) <= set(new), sorted(set(VALIDATOR_REPORTS) - set(new))
    if staging:
        assert set(STAGING_PROOF) <= set(new), "the remote staging proof must survive the rewrite"
        assert set(STAGING_PROOF) <= set(old), "the old list did publish it; do not regress"

    # 3. No raw gcloud describe output, and no unreviewed newcomer.
    assert not swept_dumps, swept_dumps
    assert not swept_decoys, swept_decoys

    print(f"\nunreviewed files the OLD glob would have published: {sorted(set(DECOYS) & set(old))}")
    print(
        f"\nASSERTIONS OK: {len(JOB_RECEIPTS)} Job receipts recovered, "
        f"{len(VALIDATOR_REPORTS) + (len(STAGING_PROOF) if staging else 0)} prior reports "
        f"preserved, {len(RAW_GCLOUD_DUMPS)} raw gcloud dumps and {len(DECOYS)} decoys excluded."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
