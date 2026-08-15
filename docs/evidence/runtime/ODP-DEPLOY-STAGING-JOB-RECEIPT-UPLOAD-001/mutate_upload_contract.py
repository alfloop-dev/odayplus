#!/usr/bin/env python3
"""ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001: does the contract test bite?

`tests/ops/test_deploy_workflow_contract.py` passing proves the workflows are
correct today. It does not prove the tests would notice if they stopped being
correct. This driver reintroduces eight forms of the defect — including the
literal pre-fix path list — into a throwaway git worktree, and asserts each one
turns the suite red, naming the tests that caught it.

The worktree is created from the current HEAD and removed afterwards, so this
never touches the checkout it runs from.

Usage:  python3 docs/evidence/runtime/ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001/\
mutate_upload_contract.py
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[4]
TEST_TARGET = "tests/ops/test_deploy_workflow_contract.py"

RECEIPTS = """            .odp_data/deployment/cloud-run-jobs/migration-validation.json
            .odp_data/deployment/cloud-run-jobs/scheduler-validation.json
            .odp_data/deployment/cloud-run-jobs/worker-validation.json
"""
PRE_FIX_STAGING_PATHS = (
    "          path: |\n"
    "            .odp_data/deployment/*.json\n"
    "            .odp_data/remote-staging-proof/*.json\n"
    "          if-no-files-found"
)
PRE_FIX_DEV_PATH = "          path: .odp_data/deployment/*.json\n          if-no-files-found"
_PATH_BLOCK = r"(?s)          path: \|\n.*?          if-no-files-found"

STAGING = ".github/workflows/deploy-staging.yml"
DEV = ".github/workflows/deploy-dev.yml"

MUTATIONS = (
    (
        "staging reverts to the original non-recursive glob pair",
        STAGING,
        lambda t: re.sub(_PATH_BLOCK, PRE_FIX_STAGING_PATHS, t),
    ),
    (
        "staging drops the worker Job receipt",
        STAGING,
        lambda t: t.replace(
            "            .odp_data/deployment/cloud-run-jobs/worker-validation.json\n", ""
        ),
    ),
    (
        "staging drops the remote staging proof",
        STAGING,
        lambda t: t.replace(
            "            .odp_data/remote-staging-proof/staging-${{ github.run_id }}.json\n", ""
        ),
    ),
    (
        "staging uploads a raw gcloud describe dump",
        STAGING,
        lambda t: t.replace(
            RECEIPTS, RECEIPTS + "            .odp_data/deployment/cloud-run-jobs/worker-job.json\n"
        ),
    ),
    (
        "staging re-adds a recursive wildcard alongside the allowlist",
        STAGING,
        lambda t: t.replace(RECEIPTS, RECEIPTS + "            .odp_data/deployment/**/*.json\n"),
    ),
    (
        "staging proof path drifts from the step that writes it",
        STAGING,
        lambda t: t.replace(
            "staging-${{ github.run_id }}.json", "staging-${{ github.run_number }}.json"
        ),
    ),
    (
        "staging publishes an unjustified file from the proof directory",
        STAGING,
        lambda t: t.replace(
            "            .odp_data/remote-staging-proof/staging-${{ github.run_id }}.json\n",
            "            .odp_data/remote-staging-proof/staging-${{ github.run_id }}.json\n"
            "            .odp_data/remote-staging-proof/operator-token.json\n",
        ),
    ),
    (
        "dev regresses to the glob the sweep must also catch",
        DEV,
        lambda t: re.sub(_PATH_BLOCK, PRE_FIX_DEV_PATH, t),
    ),
)


def run_suite(worktree: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            TEST_TARGET,
            "-q",
            "--no-header",
            "--tb=no",
            "-rf",
            "-p",
            "no:cacheprovider",
        ],
        cwd=worktree,
        capture_output=True,
        text=True,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        worktree = pathlib.Path(tmp) / "mutation"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        try:
            # The worktree is at HEAD; overlay the checkout's current state so
            # this can be run before the work is committed as well as after.
            for rel in (STAGING, DEV, TEST_TARGET):
                (worktree / rel).write_text((ROOT / rel).read_text(encoding="utf-8"), "utf-8")
            original = {rel: (worktree / rel).read_text(encoding="utf-8") for rel in (STAGING, DEV)}

            baseline = run_suite(worktree)
            print("BASELINE (unmutated):", baseline.stdout.strip().splitlines()[-1])
            assert baseline.returncode == 0, baseline.stdout

            for label, target, mutate in MUTATIONS:
                for rel, text in original.items():
                    (worktree / rel).write_text(text, encoding="utf-8")
                path = worktree / target
                mutated = mutate(path.read_text(encoding="utf-8"))
                assert mutated != path.read_text(encoding="utf-8"), f"{label}: changed nothing"
                path.write_text(mutated, encoding="utf-8")

                result = run_suite(worktree)
                caught = sorted(set(re.findall(r"^FAILED [^:]+::(\S+)", result.stdout, flags=re.M)))
                status = "CAUGHT" if result.returncode != 0 else "*** MISSED ***"
                print(f"\n{status}: {label}")
                print(f"  target: {pathlib.Path(target).name}")
                print("  failing tests:\n    " + ("\n    ".join(caught) or "-"))
                assert result.returncode != 0, f"MUTATION SURVIVED: {label}\n{result.stdout}"

            for rel, text in original.items():
                (worktree / rel).write_text(text, encoding="utf-8")
            restored = run_suite(worktree)
            print(
                f"\nAll {len(MUTATIONS)} mutations caught; worktree restored:",
                restored.stdout.strip().splitlines()[-1],
            )
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
