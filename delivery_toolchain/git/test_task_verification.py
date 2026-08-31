#!/usr/bin/env python3
"""Regression tests for the declared-verification finalize gate.

These drive the CLI as a subprocess against a throwaway git repository, which
is how task_finalize.sh calls it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "delivery_toolchain" / "git" / "task_verification.py"

if str(REPO_ROOT / ".orchestrator") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / ".orchestrator"))

import verification_evidence as ve  # noqa: E402

TASK_ID = "ODP-GATE-TEST-001"


class TaskVerificationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.repo = Path(self.tmpdir.name) / "repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("gate test\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git(
            "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-q", "-m", "seed",
        )
        self.head = self._git("rev-parse", "HEAD")
        self.store = self.repo / ".orchestrator" / "evidence"
        self.status_file = Path(self.tmpdir.name) / "ai-status.json"

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, text=True, capture_output=True, check=True
        )
        return result.stdout.strip()

    def _write_status(self, verification: list[str], *, task_id: str = TASK_ID) -> None:
        self.status_file.write_text(
            json.dumps({"tasks": [{"id": task_id, "verification": verification}]}),
            encoding="utf-8",
        )

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable, str(CLI), *args,
                "--task-id", TASK_ID,
                "--repo", str(self.repo),
                "--status-file", str(self.status_file),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=600,
        )

    def _receipt(self, command: str, *, exit_code: int = 0, head: str | None = None) -> dict:
        return ve.build_receipt(
            task_id=TASK_ID,
            head_sha=head or self.head,
            command=command,
            exit_code=exit_code,
            duration_seconds=1.0,
            produced_by="test",
        )

    # --- check ---------------------------------------------------------------

    def test_no_declared_verification_passes(self) -> None:
        self._write_status([])
        result = self._cli("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing to prove", result.stdout)

    def test_task_absent_from_the_board_passes(self) -> None:
        self._write_status(["pytest -q"], task_id="SOME-OTHER-TASK")
        result = self._cli("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not on the board", result.stdout)

    def test_declared_command_without_a_receipt_cannot_finalize(self) -> None:
        self._write_status(["pytest -q tests/unit"])
        result = self._cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no verification receipt", result.stderr)
        self.assertIn("refusing to publish", result.stderr)

    def test_passing_receipt_at_head_allows_finalize(self) -> None:
        command = "pytest -q tests/unit"
        self._write_status([command])
        ve.write_receipt(self.store, self._receipt(command))
        result = self._cli("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("proven at", result.stdout)

    def test_receipt_from_another_head_cannot_finalize(self) -> None:
        command = "pytest -q tests/unit"
        self._write_status([command])
        ve.write_receipt(self.store, self._receipt(command, head="9" * 40))
        result = self._cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no verification receipt", result.stderr)

    def test_nonzero_receipt_cannot_finalize(self) -> None:
        command = "pytest -q tests/unit"
        self._write_status([command])
        ve.write_receipt(self.store, self._receipt(command, exit_code=1))
        result = self._cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not prove", result.stderr)

    def test_interrupted_receipt_cannot_finalize(self) -> None:
        command = "pytest -q tests/unit"
        self._write_status([command])
        ve.write_receipt(self.store, self._receipt(command, exit_code=-15))
        result = self._cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("interrupted", result.stderr)

    def test_masked_declared_command_cannot_finalize(self) -> None:
        self._write_status(["pytest -q tests/unit | tail -1"])
        result = self._cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("masking policy", result.stderr)

    def test_missing_status_file_fails_closed(self) -> None:
        # setUp never writes the status file: without it, whether this task
        # declares verification commands is unknowable, so publishing is refused.
        result = self._cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no status file found", result.stderr)

    # --- run -----------------------------------------------------------------

    def test_run_records_a_receipt_that_unlocks_the_gate(self) -> None:
        command = f"{sys.executable} -c 'pass'"
        self._write_status([command])

        self.assertEqual(self._cli("check").returncode, 1)

        run_result = self._cli("run")
        self.assertEqual(run_result.returncode, 0, run_result.stderr)
        self.assertIn("passed", run_result.stdout)

        receipts = ve.load_receipts(self.store, task_id=TASK_ID)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["head_sha"], self.head)
        self.assertEqual(receipts[0]["exit_code"], 0)
        self.assertEqual(receipts[0]["produced_by"], "delivery_toolchain/git/task_verification.py")
        self.assertGreaterEqual(receipts[0]["duration_seconds"], 0.0)

        self.assertEqual(self._cli("check").returncode, 0)

    def test_run_refuses_a_duplicate_baseline_without_a_retry_reason(self) -> None:
        command = f"{sys.executable} -c 'pass'"
        self._write_status([command])
        self.assertEqual(self._cli("run").returncode, 0)

        again = self._cli("run")
        self.assertEqual(again.returncode, 1)
        self.assertIn("duplicate", again.stderr)
        self.assertEqual(len(ve.load_receipts(self.store, task_id=TASK_ID)), 1)

    def test_run_accepts_an_explicit_retry_reason(self) -> None:
        command = f"{sys.executable} -c 'pass'"
        self._write_status([command])
        self.assertEqual(self._cli("run").returncode, 0)

        retried = self._cli("run", "--retry-reason", "re-measuring after a runner image change")
        self.assertEqual(retried.returncode, 0, retried.stderr)
        receipts = ve.load_receipts(self.store, task_id=TASK_ID)
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[-1]["attempt"], 2)

    def test_run_reports_a_placeholder_retry_reason_as_a_duplicate(self) -> None:
        command = f"{sys.executable} -c 'pass'"
        self._write_status([command])
        self.assertEqual(self._cli("run").returncode, 0)
        self.assertEqual(self._cli("run", "--retry-reason", "flaky").returncode, 1)

    def test_run_records_a_real_failure_and_the_gate_stays_shut(self) -> None:
        command = f"{sys.executable} -c 'raise SystemExit(1)'"
        self._write_status([command])

        run_result = self._cli("run")
        self.assertEqual(run_result.returncode, 1)

        receipts = ve.load_receipts(self.store, task_id=TASK_ID)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["exit_code"], 1)
        self.assertFalse(receipts[0]["passed"])
        self.assertEqual(self._cli("check").returncode, 1)

    def test_run_does_not_execute_a_masked_command(self) -> None:
        sentinel = self.repo / "ran.txt"
        script = self.repo / "touch_sentinel.py"
        script.write_text(f"open({str(sentinel)!r}, 'w').write('x')\n", encoding="utf-8")
        self._write_status([f"{sys.executable} {script} | cat"])

        run_result = self._cli("run")
        self.assertEqual(run_result.returncode, 1)
        self.assertIn("masked_pipeline", run_result.stderr)
        self.assertFalse(sentinel.exists())
        self.assertEqual(ve.load_receipts(self.store, task_id=TASK_ID), [])


if __name__ == "__main__":
    unittest.main()
