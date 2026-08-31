#!/usr/bin/env python3
"""Integration regression tests for verification evidence.

These drive real subprocesses, the real receipt store under
``.orchestrator/evidence``, and the real worker prompt rendering, so a
regression that only shows up once the pieces are wired together is caught
here rather than in production dispatch.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import common
import supervisor
import verification_evidence as ve

HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"


def _pytest_available() -> bool:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


class RealExitCodeTests(unittest.TestCase):
    """The recorded exit code must be the process's own, not the shell's."""

    def test_nonzero_exit_code_survives(self) -> None:
        result = ve.run_verification_command(f"{sys.executable} -c 'raise SystemExit(3)'")
        self.assertTrue(result["executed"])
        self.assertEqual(result["exit_code"], 3)
        self.assertGreaterEqual(result["duration_seconds"], 0.0)

    def test_zero_exit_code_survives(self) -> None:
        result = ve.run_verification_command(f"{sys.executable} -c 'pass'")
        self.assertEqual(result["exit_code"], 0)

    def test_masked_command_is_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sentinel = Path(tmpdir) / "ran.txt"
            script = Path(tmpdir) / "touch_sentinel.py"
            script.write_text(f"open({str(sentinel)!r}, 'w').write('x')\n", encoding="utf-8")
            command = f"{sys.executable} {script} | cat"
            result = ve.run_verification_command(command)

            self.assertFalse(result["executed"])
            self.assertIsNone(result["exit_code"])
            self.assertFalse(result["audit"].ok)
            self.assertIn(ve.V_MASKED_PIPELINE, result["audit"].violations)
            self.assertFalse(sentinel.exists(), "a masked command must not run at all")

    def test_redirection_actually_redirects(self) -> None:
        # The audit passes redirections on the grounds that they do not touch
        # the exit code. That only holds if a shell interprets them: run as
        # argv, `>` and the log path become arguments to the program itself.
        if shutil.which("bash") is None:
            self.skipTest("bash is required to honour a redirection")
        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "run.log"
            script = Path(tmpdir) / "emit.py"
            script.write_text(
                "import sys\n"
                "if len(sys.argv) > 1:\n"
                "    raise SystemExit('unexpected argv: ' + repr(sys.argv[1:]))\n"
                "print('stdout line')\n"
                "print('stderr line', file=sys.stderr)\n",
                encoding="utf-8",
            )
            result = ve.run_verification_command(f"{sys.executable} {script} > {log} 2>&1")

            self.assertTrue(result["executed"])
            self.assertTrue(result["shell"], "a redirection must be handed to a shell")
            self.assertEqual(result["exit_code"], 0)
            self.assertTrue(log.exists(), "the redirect target was never written")
            written = log.read_text(encoding="utf-8")
            self.assertIn("stdout line", written)
            self.assertIn("stderr line", written, "2>&1 must fold stderr into the same file")

    def test_redirection_does_not_hide_a_failing_exit_code(self) -> None:
        if shutil.which("bash") is None:
            self.skipTest("bash is required to honour a redirection")
        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "run.log"
            result = ve.run_verification_command(
                f"{sys.executable} -c 'raise SystemExit(7)' > {log} 2>&1"
            )
            self.assertEqual(result["exit_code"], 7)
            self.assertEqual(ve.classify_outcome(result["exit_code"]), ve.OUTCOME_FAILED)

    def test_command_without_shell_metacharacters_stays_on_argv(self) -> None:
        result = ve.run_verification_command(f"{sys.executable} -c 'pass'")
        self.assertTrue(result["executed"])
        self.assertFalse(result["shell"], "a plain command must not gain a shell it did not ask for")

    def test_pipefail_pipeline_reports_the_failing_stage(self) -> None:
        if shutil.which("bash") is None:
            self.skipTest("bash is required for a pipefail pipeline")
        command = f"set -o pipefail; {sys.executable} -c 'raise SystemExit(4)' | cat"
        result = ve.run_verification_command(command)
        self.assertTrue(result["executed"])
        self.assertEqual(result["exit_code"], 4)

    def test_signal_termination_is_interrupted_not_passed(self) -> None:
        command = (
            f"{sys.executable} -c "
            "'import os, signal; os.kill(os.getpid(), signal.SIGTERM)'"
        )
        result = ve.run_verification_command(command)
        receipt = ve.build_receipt(
            task_id="T-SIGNAL",
            head_sha=HEAD_SHA,
            command=command,
            exit_code=result["exit_code"],
            duration_seconds=result["duration_seconds"],
            timed_out=result["timed_out"],
        )
        self.assertEqual(receipt["outcome"], ve.OUTCOME_INTERRUPTED)
        self.assertEqual(receipt["signal"], "SIGTERM")
        self.assertFalse(receipt["passed"])

    def test_timeout_is_interrupted_not_passed(self) -> None:
        command = f"{sys.executable} -c 'import time; time.sleep(30)'"
        result = ve.run_verification_command(command, timeout=1.0)
        self.assertTrue(result["timed_out"])
        receipt = ve.build_receipt(
            task_id="T-TIMEOUT",
            head_sha=HEAD_SHA,
            command=command,
            exit_code=result["exit_code"],
            duration_seconds=result["duration_seconds"],
            timed_out=True,
        )
        self.assertEqual(receipt["outcome"], ve.OUTCOME_INTERRUPTED)
        self.assertFalse(receipt["passed"])

    def test_missing_binary_does_not_look_like_a_pass(self) -> None:
        result = ve.run_verification_command("odp-not-a-real-binary --version")
        self.assertFalse(result["executed"])
        self.assertEqual(result["exit_code"], 127)
        receipt = ve.build_receipt(
            task_id="T-MISSING",
            head_sha=HEAD_SHA,
            command="odp-not-a-real-binary --version",
            exit_code=result["exit_code"],
            duration_seconds=result["duration_seconds"],
        )
        self.assertEqual(receipt["outcome"], ve.OUTCOME_FAILED)
        self.assertFalse(receipt["passed"])


@unittest.skipUnless(_pytest_available(), "pytest must be runnable as a subprocess")
class RealPytestExitCodeTests(unittest.TestCase):
    """pytest's own exit codes must be recorded, including 1, 2 and 5."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.suite = Path(self.tmpdir.name) / "test_sample.py"
        self.suite.write_text(
            textwrap.dedent(
                """
                def test_ok():
                    assert True


                def test_broken():
                    assert False
                """
            ),
            encoding="utf-8",
        )

    def _command(self, *extra: str) -> str:
        parts = [sys.executable, "-m", "pytest", str(self.suite), "-q", "-p", "no:cacheprovider", *extra]
        return " ".join(parts)

    def _receipt(self, command: str) -> dict:
        result = ve.run_verification_command(command, timeout=300)
        return ve.build_receipt(
            task_id="T-PYTEST",
            head_sha=HEAD_SHA,
            command=command,
            exit_code=result["exit_code"],
            duration_seconds=result["duration_seconds"],
            timed_out=result["timed_out"],
        )

    def test_failing_suite_records_exit_code_one(self) -> None:
        receipt = self._receipt(self._command())
        self.assertEqual(receipt["exit_code"], 1)
        self.assertEqual(receipt["outcome"], ve.OUTCOME_FAILED)
        self.assertFalse(receipt["passed"])

    def test_passing_selection_records_exit_code_zero(self) -> None:
        receipt = self._receipt(self._command("-k", "test_ok"))
        self.assertEqual(receipt["exit_code"], 0)
        self.assertTrue(receipt["passed"])

    def test_empty_selection_is_not_a_pass(self) -> None:
        receipt = self._receipt(self._command("-k", "no_such_test_name"))
        self.assertEqual(receipt["exit_code"], ve.PYTEST_NO_TESTS_COLLECTED)
        self.assertEqual(receipt["outcome"], ve.OUTCOME_NO_TESTS)
        self.assertFalse(receipt["passed"])

    def test_piped_failing_suite_would_have_reported_success(self) -> None:
        """The masking this policy exists to stop, demonstrated end to end."""
        if shutil.which("bash") is None:
            self.skipTest("bash is required to demonstrate pipeline masking")
        masked = f"{self._command()} | tail -1"
        self.assertFalse(ve.audit_command(masked).ok)

        # Without the audit the shell reports the tail's status, not pytest's.
        completed = subprocess.run(["bash", "-c", masked], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, "pipeline masking is what the audit prevents")


class ReceiptStoreTests(unittest.TestCase):
    """Receipts round-trip through the supervisor's existing evidence dir."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.evidence = Path(self.tmpdir.name) / "evidence"
        self.config = {"paths": {"evidence_dir": str(self.evidence)}}

    def _receipt(self, *, command="pytest -q tests/unit", exit_code=0, head=HEAD_SHA, task="T-STORE", **kwargs):
        return ve.build_receipt(
            task_id=task,
            head_sha=head,
            command=command,
            exit_code=exit_code,
            duration_seconds=2.5,
            **kwargs,
        )

    def test_receipt_round_trips(self) -> None:
        written = common.write_verification_receipt(self.config, receipt=self._receipt())
        self.assertTrue(written)

        loaded = common.load_verification_receipts(self.config, task_id="T-STORE")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["head_sha"], HEAD_SHA)
        self.assertEqual(loaded[0]["command"], "pytest -q tests/unit")
        self.assertEqual(loaded[0]["exit_code"], 0)
        self.assertEqual(loaded[0]["duration_seconds"], 2.5)
        self.assertTrue(loaded[0]["selection"]["fingerprint"])

    def test_receipts_are_scoped_by_task(self) -> None:
        common.write_verification_receipt(self.config, receipt=self._receipt(task="T-STORE"))
        common.write_verification_receipt(self.config, receipt=self._receipt(task="T-OTHER"))

        self.assertEqual(len(common.load_verification_receipts(self.config, task_id="T-STORE")), 1)
        self.assertEqual(len(common.load_verification_receipts(self.config)), 2)

    def test_invalid_receipt_is_refused(self) -> None:
        receipt = self._receipt(command="pytest -q tests/unit | tail -1")
        with self.assertRaises(ValueError):
            common.write_verification_receipt(self.config, receipt=receipt)
        self.assertEqual(common.load_verification_receipts(self.config), [])

    def test_pass_claim_with_nonzero_exit_is_refused(self) -> None:
        receipt = self._receipt()
        receipt["exit_code"] = 1
        with self.assertRaises(ValueError):
            common.write_verification_receipt(self.config, receipt=receipt)

    def test_missing_evidence_dir_reads_as_empty(self) -> None:
        self.assertEqual(common.load_verification_receipts(self.config, task_id="T-STORE"), [])


class DuplicateBaselineFlowTests(unittest.TestCase):
    """A settled head SHA is not re-measured without an explicit reason."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.config = {"paths": {"evidence_dir": str(Path(self.tmpdir.name) / "evidence")}}
        self.command = f"{sys.executable} -c 'pass'"

    def _run(self, *, head=HEAD_SHA, retry_reason=None):
        receipts = common.load_verification_receipts(self.config, task_id="T-FLOW")
        decision, receipt = ve.verify_and_build_receipt(
            self.command,
            task_id="T-FLOW",
            head_sha=head,
            receipts=receipts,
            retry_reason=retry_reason,
            agent="Claude",
        )
        if receipt is not None:
            common.write_verification_receipt(self.config, receipt=receipt)
        return decision, receipt

    def test_second_run_on_the_same_sha_is_refused(self) -> None:
        first_decision, first_receipt = self._run()
        self.assertEqual(first_decision.kind, ve.KIND_BASELINE)
        self.assertTrue(first_receipt["passed"])

        second_decision, second_receipt = self._run()
        self.assertFalse(second_decision.allowed)
        self.assertEqual(second_decision.kind, ve.KIND_DUPLICATE)
        self.assertIsNone(second_receipt)
        self.assertEqual(len(common.load_verification_receipts(self.config, task_id="T-FLOW")), 1)

    def test_explicit_retry_reason_allows_a_second_run(self) -> None:
        self._run()
        decision, receipt = self._run(retry_reason="re-measuring after the CI runner image changed")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.kind, ve.KIND_RETRY)
        self.assertEqual(receipt["attempt"], 2)
        self.assertEqual(receipt["retry_reason"], "re-measuring after the CI runner image changed")
        self.assertEqual(len(common.load_verification_receipts(self.config, task_id="T-FLOW")), 2)

    def test_a_new_head_sha_is_measured_again(self) -> None:
        self._run()
        decision, receipt = self._run(head=OTHER_SHA)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.kind, ve.KIND_BASELINE)
        self.assertEqual(receipt["head_sha"], OTHER_SHA)

    def test_interrupted_run_is_resumable_and_not_escalated(self) -> None:
        interrupted = ve.build_receipt(
            task_id="T-FLOW",
            head_sha=HEAD_SHA,
            command=self.command,
            exit_code=-15,
            duration_seconds=0.5,
        )
        common.write_verification_receipt(self.config, receipt=interrupted)

        decision, receipt = self._run()
        self.assertTrue(decision.allowed, decision.reason)
        self.assertEqual(decision.kind, ve.KIND_RESUME)
        self.assertTrue(receipt["passed"])

        plan = ve.plan_rerun(interrupted, requested_selection=ve.extract_selection("pytest"))
        self.assertFalse(plan.allowed)
        self.assertTrue(plan.escalated)


class WorkerPromptTests(unittest.TestCase):
    """The worker prompt must show which declared commands are unusable."""

    MASKED = "pytest -q tests/unit | tee /tmp/out.log"
    CLEAN = "pytest -q tests/unit"

    def _task(self, verification: list[str]) -> dict:
        return {
            "id": "ODP-VERIF-BRIEF-TEST",
            "title": "Verification brief",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Codex2",
            "summary_zh": "驗證證據測試",
            "depends_on": [],
            "artifacts": [],
            "source_docs": [],
            "acceptance": ["receipts bind head sha"],
            "verification": verification,
            "priority": "P0",
            "last_update": "2026-08-31T02:19:29Z",
        }

    def _render(self, verification: list[str]) -> str:
        with tempfile.TemporaryDirectory() as tmpdir:
            brief_path = Path(tmpdir) / "brief.md"
            config = {"paths": {"status_file": str(Path(tmpdir) / "ai-status.json")}}
            with (
                mock.patch.object(common, "load_status", return_value={"tasks": [self._task(verification)]}),
                mock.patch.object(common, "task_brief_path", return_value=brief_path),
                mock.patch.object(common, "load_json", return_value={}),
                mock.patch.object(common, "_recent_task_activity", return_value=[]),
            ):
                common.write_task_brief(config, "ODP-VERIF-BRIEF-TEST")
            return brief_path.read_text(encoding="utf-8")

    def test_clean_command_renders_unchanged(self) -> None:
        text = self._render([self.CLEAN])
        self.assertIn(f"- `{self.CLEAN}`", text)
        self.assertNotIn("REJECTED", text)

    def test_masked_command_is_marked_rejected(self) -> None:
        text = self._render([self.MASKED])
        self.assertIn("REJECTED", text)
        self.assertIn(ve.V_MASKED_PIPELINE, text)
        self.assertIn("1 declared command(s) above are rejected", text)

    def test_policy_block_states_the_receipt_contract(self) -> None:
        text = self._render([self.CLEAN])
        self.assertIn("### Verification Evidence Policy", text)
        self.assertIn("head SHA", text)
        self.assertIn("test selection", text)
        self.assertIn("never a pass", text)
        self.assertIn("explicit retry reason", text)

    def test_no_policy_block_without_declared_commands(self) -> None:
        text = self._render([])
        self.assertIn("## Verification\n- none", text)
        self.assertNotIn("### Verification Evidence Policy", text)

    def test_fallback_worker_brief_also_marks_rejected(self) -> None:
        task = self._task([self.MASKED])
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(
                supervisor,
                "generate_task_brief_content",
                side_effect=ValueError("no brief on the supervisor root"),
            ),
        ):
            text = supervisor._generated_worker_task_brief({}, "ODP-VERIF-BRIEF-TEST")

        self.assertIn("REJECTED", text)
        self.assertIn(ve.V_MASKED_PIPELINE, text)


if __name__ == "__main__":
    unittest.main()
