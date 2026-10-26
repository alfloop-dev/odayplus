#!/usr/bin/env python3
"""Regenerate this task's verification receipt bundle from the live mechanism.

Round 2 of the review found the committed bundle naming a head SHA that was
not an ancestor of the PR head: the bundle had been produced by hand-driving
the module, the branch was later rebuilt, and nothing could detect the drift.
So the generator is committed alongside its output. A reader who doubts the
bundle can re-run this and diff, and the provenance claim in README.md becomes
a check rather than an assertion.

The contract this script enforces:

* ``head_sha`` is the repository HEAD at generation time, and the worktree is
  clean -- otherwise the bundle would claim to measure a tree that no commit
  contains.
* The bundle is committed as the *next* commit, so ``head_sha`` is that
  commit's parent and therefore an ancestor of the PR head. Verify with::

      git merge-base --is-ancestor "$(python3 -c 'import json,sys;
        print(json.load(open(sys.argv[1]))["head_sha"])' verification_receipt.json)" HEAD

* Every receipt is produced by ``verification_evidence.verify_and_build_receipt``
  -- the same entry point ``task_verification.py run`` uses. Nothing here
  writes a receipt field by hand.

Usage::

    python3 docs/evidence/execution-control/ODP-ORCH-VERIFICATION-EVIDENCE-001/generate_receipt_bundle.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
TASK_ID = "ODP-ORCH-VERIFICATION-EVIDENCE-001"
OUTPUT = HERE / "verification_receipt.json"

sys.path.insert(0, str(REPO / ".orchestrator"))

import verification_evidence as ve  # noqa: E402

PYTEST_PREFIX = (
    "uv run --no-project --python 3.12 --with pytest --with jsonschema "
    "--with pyyaml --with cryptography pytest"
)

MEASURED_COMMANDS = (
    f"{PYTEST_PREFIX} .orchestrator/test_verification_evidence.py -q",
    f"{PYTEST_PREFIX} .orchestrator/test_verification_evidence_integration.py -q",
    f"{PYTEST_PREFIX} .orchestrator/test_task_brief_source_docs.py -q",
    f"{PYTEST_PREFIX} delivery_toolchain/git/test_task_verification.py -q",
)

REJECTED_SAMPLES = (
    f"{PYTEST_PREFIX} .orchestrator/test_verification_evidence.py -q | tail -1",
    f"{PYTEST_PREFIX} .orchestrator/test_verification_evidence.py -q || true",
    f"{PYTEST_PREFIX} .orchestrator/test_verification_evidence.py -q; echo done",
    f"set +e; {PYTEST_PREFIX} .orchestrator/test_verification_evidence.py -q",
    f"{PYTEST_PREFIX} .orchestrator/test_verification_evidence.py -q &",
)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def resolve_clean_head() -> str:
    dirt = git("status", "--porcelain", "--untracked-files=all")
    # The bundle itself is the one file allowed to be dirty: it is what this
    # run is about to rewrite.  Supervisor-seeded context files (AI_COLLABORATION_GUIDE,
    # ai-status.json, ai-task-archive/, etc.) are also filtered: they are
    # gitignored materialized context, not owner dirt that changes what HEAD means.
    _ALLOWED_PREFIXES = (
        OUTPUT.relative_to(REPO).as_posix(),
        "AI_COLLABORATION_GUIDE.md",
        "ai-status.json",
        "ai-activity-log.jsonl",
        "current-work.md",
        "ai-task-archive/",
        ".orchestrator/task-briefs/",
        ".orchestrator/reviews/",
    )
    def _porcelain_path(line: str) -> str:
        """Extract the file path from a ``git status --porcelain`` line.

        Porcelain v1 format: ``XY path`` or ``XY original -> path`` for renames.
        """
        raw = line[3:]  # skip the two-char status + space
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        return raw.strip()

    remaining = [
        line
        for line in dirt.splitlines()
        if not any(
            _porcelain_path(line) == prefix
            or _porcelain_path(line).startswith(
                prefix if prefix.endswith("/") else prefix + "/"
            )
            for prefix in _ALLOWED_PREFIXES
        )
    ]
    if remaining:
        raise SystemExit(
            "refusing to generate a bundle from a dirty worktree -- the head SHA "
            "would name a tree no commit contains:\n  " + "\n  ".join(remaining)
        )
    return git("rev-parse", "HEAD")


def measure(head_sha: str) -> tuple[list[dict], list[dict]]:
    """Run each declared command once through the real mechanism."""
    receipts: list[dict] = []
    runs: list[dict] = []
    for command in MEASURED_COMMANDS:
        decision, receipt = ve.verify_and_build_receipt(
            command,
            task_id=TASK_ID,
            head_sha=head_sha,
            receipts=receipts,
            cwd=str(REPO),
            agent="Antigravity",
            produced_by=str(Path(__file__).relative_to(REPO)),
        )
        if receipt is None:
            raise SystemExit(f"refused before running: {decision.reason}\n  {command}")
        if not receipt["passed"]:
            raise SystemExit(
                f"refusing to publish a bundle around a non-passing run "
                f"({receipt['outcome']}, exit {receipt['exit_code']}): {command}\n"
                f"{receipt.get('output_tail', '')}"
            )
        receipts.append(receipt)
        runs.append({"decision": decision.as_dict(), "receipt": receipt})
    return runs, receipts


def rerun_control(head_sha: str, receipts: list[dict]) -> dict:
    """The duplicate refusal and the explicit-retry path, both measured."""
    first = receipts[0]
    selection_id = first["selection"]["fingerprint"]

    refusal = ve.evaluate_baseline_request(
        receipts, head_sha=head_sha, selection_id=selection_id, task_id=TASK_ID
    )

    retry_reason = "evidence artifact: demonstrating the explicit retry path"
    decision, receipt = ve.verify_and_build_receipt(
        first["command"],
        task_id=TASK_ID,
        head_sha=head_sha,
        receipts=receipts,
        retry_reason=retry_reason,
        cwd=str(REPO),
        agent="Antigravity",
        produced_by=str(Path(__file__).relative_to(REPO)),
    )
    if receipt is None:
        raise SystemExit(f"explicit retry was refused: {decision.reason}")

    return {
        "duplicate_baseline_refusal": refusal.as_dict(),
        "explicit_retry": {"decision": decision.as_dict(), "receipt": receipt},
    }


def signal_interruption(head_sha: str) -> dict:
    """A SIGTERM is not a result, and does not authorize a wider rerun."""
    command = f"{sys.executable} -c 'import os, signal; os.kill(os.getpid(), signal.SIGTERM)'"
    result = ve.run_verification_command(command, cwd=str(REPO))
    receipt = ve.build_receipt(
        task_id=TASK_ID,
        head_sha=head_sha,
        command=command,
        exit_code=result["exit_code"],
        duration_seconds=result["duration_seconds"],
        started_at=result["started_at"],
        finished_at=result["finished_at"],
        audit=result["audit"],
        timed_out=result["timed_out"],
        produced_by=str(Path(__file__).relative_to(REPO)),
    )
    if receipt["outcome"] != ve.OUTCOME_INTERRUPTED:
        raise SystemExit(f"expected an interrupted run, measured {receipt['outcome']!r}")
    suite = ve.extract_selection("pytest")
    plan = ve.plan_rerun(receipt, requested_selection=suite)
    return {"receipt": receipt, "escalation_plan": plan.as_dict()}


def gate_judgements(head_sha: str, receipts: list[dict]) -> dict:
    """The finalize gate's verdict in each shape the review asked about."""
    proven = receipts[0]
    command = proven["command"]

    def gate(commands, store, requirement=None):
        return ve.evaluate_finalize_gate(
            commands=commands,
            head_sha=head_sha,
            receipts=store,
            task_id=TASK_ID,
            requirement=requirement,
        ).as_dict()

    forged_pass = json.loads(json.dumps(proven))
    forged_pass["exit_code"] = 1
    forged_pass["passed"] = True
    forged_pass["outcome"] = ve.OUTCOME_PASSED

    forged_audit = json.loads(json.dumps(proven))
    forged_audit["command"] = f"{command} | tail -1"
    forged_audit["command_audit"] = {
        "command": forged_audit["command"],
        "ok": True,
        "violations": [],
        "details": [],
        "runner": "pytest",
    }

    no_audit = json.loads(json.dumps(proven))
    no_audit.pop("command_audit", None)

    # Same tests, different command: `-q` dropped from the declaration.
    same_selection_other_command = command.replace(" -q", "", 1)

    return {
        "command": command,
        "without_receipt": gate([command], []),
        "with_passing_receipt": gate([command], [proven]),
        "with_forged_pass_flag": gate([command], [forged_pass]),
        "with_receipt_missing_its_audit": gate([command], [no_audit]),
        "with_forged_clean_audit_on_a_masked_command": gate(
            [forged_audit["command"]], [forged_audit]
        ),
        "with_receipt_for_a_different_command_over_the_same_tests": {
            "declared": same_selection_other_command,
            "receipt_command": command,
            "same_selection_fingerprint": (
                ve.extract_selection(same_selection_other_command)["fingerprint"]
                == ve.extract_selection(command)["fingerprint"]
            ),
            "result": gate([same_selection_other_command], [proven]),
        },
        "declaration_required_but_nothing_declared": gate(
            [], [], requirement=ve.declaration_requirement({"verification_required": True})
        ),
        "declaration_marker_absent_is_legacy": gate(
            [], [], requirement=ve.declaration_requirement({"id": TASK_ID})
        ),
    }


def redirection_execution() -> dict:
    """A documented redirection must actually redirect, not become argv."""
    log = HERE / ".redirect-probe.log"
    try:
        command = f"{sys.executable} -c 'print(\"redirected\")' > {log} 2>&1"
        result = ve.run_verification_command(command, cwd=str(REPO))
        return {
            "command": command.replace(str(log), "<tmp>/redirect-probe.log"),
            "audit_ok": result["audit"].ok,
            "ran_under_a_shell": result.get("shell"),
            "exit_code": result["exit_code"],
            "target_written": log.is_file(),
            "target_contents": log.read_text(encoding="utf-8").strip() if log.is_file() else "",
            "selection_ignores_the_redirect_target": (
                ve.extract_selection(f"pytest -q tests/unit > {log} 2>&1")["items"] == ["tests/unit"]
            ),
        }
    finally:
        log.unlink(missing_ok=True)


def main() -> int:
    head_sha = resolve_clean_head()
    runs, receipts = measure(head_sha)

    bundle = {
        "task_id": TASK_ID,
        "owner": "Antigravity",
        "reviewer": "Codex2",
        "head_sha": head_sha,
        "generated_by": str(Path(__file__).relative_to(REPO)),
        "note": (
            "Produced by the mechanism under review, not written by hand. head_sha is the "
            "repository HEAD at generation time and the parent of the commit that records "
            "this file, so `git merge-base --is-ancestor <head_sha> HEAD` holds."
        ),
        "runs": runs,
    }
    bundle.update(rerun_control(head_sha, receipts))
    bundle["signal_interruption"] = signal_interruption(head_sha)
    bundle["finalize_gate"] = gate_judgements(head_sha, receipts)
    bundle["redirection_execution"] = redirection_execution()
    bundle["rejected_command_samples"] = [
        ve.audit_command(sample).as_dict() for sample in REJECTED_SAMPLES
    ]

    OUTPUT.write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUTPUT.relative_to(REPO)} at head {head_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
