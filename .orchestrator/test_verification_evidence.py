#!/usr/bin/env python3
"""Unit regression tests for the verification evidence policy."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import verification_evidence as ve


class CommandAuditTests(unittest.TestCase):
    """A verification command is only trustworthy if its exit code survives."""

    def test_plain_runner_command_is_accepted(self) -> None:
        audit = ve.audit_command("uv run pytest .orchestrator/test_common.py -q")
        self.assertTrue(audit.ok, audit.details)
        self.assertEqual(audit.violations, ())
        self.assertEqual(audit.runner, "pytest")

    def test_pipe_without_pipefail_is_rejected(self) -> None:
        audit = ve.audit_command("pytest -q tests/unit | tee /tmp/log")
        self.assertFalse(audit.ok)
        self.assertIn(ve.V_MASKED_PIPELINE, audit.violations)

    def test_pipe_with_pipefail_is_accepted(self) -> None:
        audit = ve.audit_command("set -o pipefail; pytest -q tests/unit | tee /tmp/log")
        self.assertTrue(audit.ok, audit.details)

    def test_or_true_tail_is_rejected(self) -> None:
        for command in ("pytest -q || true", "pytest -q || :", "pytest -q || echo skipped", "pytest -q || exit 0"):
            with self.subTest(command=command):
                audit = ve.audit_command(command)
                self.assertFalse(audit.ok)
                self.assertIn(ve.V_FORCED_SUCCESS, audit.violations)

    def test_or_exit_nonzero_is_allowed(self) -> None:
        audit = ve.audit_command("pytest -q || exit 1")
        self.assertTrue(audit.ok, audit.details)

    def test_semicolon_tail_after_runner_is_rejected(self) -> None:
        audit = ve.audit_command("pytest -q; echo done")
        self.assertFalse(audit.ok)
        self.assertIn(ve.V_TRAILING_COMMAND, audit.violations)

    def test_semicolon_tail_that_reraises_status_is_allowed(self) -> None:
        audit = ve.audit_command("pytest -q; exit $?")
        self.assertTrue(audit.ok, audit.details)

    def test_setup_before_runner_is_allowed(self) -> None:
        audit = ve.audit_command("export CI=1; pytest -q tests/unit")
        self.assertTrue(audit.ok, audit.details)

    def test_and_chain_is_allowed(self) -> None:
        audit = ve.audit_command("pytest -q && ruff check .")
        self.assertTrue(audit.ok, audit.details)

    def test_redirection_is_allowed(self) -> None:
        audit = ve.audit_command("pytest -q > /tmp/log 2>&1")
        self.assertTrue(audit.ok, audit.details)

    def test_set_plus_e_is_rejected(self) -> None:
        audit = ve.audit_command("set +e; pytest -q")
        self.assertFalse(audit.ok)
        self.assertIn(ve.V_DISABLED_ERREXIT, audit.violations)

    def test_backgrounded_runner_is_rejected(self) -> None:
        audit = ve.audit_command("pytest -q &")
        self.assertFalse(audit.ok)
        self.assertIn(ve.V_BACKGROUNDED, audit.violations)

    def test_quoted_pipe_is_not_a_pipeline(self) -> None:
        audit = ve.audit_command("pytest -q -k 'alpha|beta'")
        self.assertTrue(audit.ok, audit.details)

    def test_unbalanced_quotes_are_rejected(self) -> None:
        audit = ve.audit_command("pytest -k 'unclosed")
        self.assertFalse(audit.ok)
        self.assertIn(ve.V_UNPARSABLE, audit.violations)

    def test_empty_command_is_rejected(self) -> None:
        audit = ve.audit_command("   ")
        self.assertFalse(audit.ok)
        self.assertIn(ve.V_EMPTY, audit.violations)

    def test_audit_commands_maps_every_entry(self) -> None:
        audits = ve.audit_commands(["pytest -q", "pytest -q | head -1"])
        self.assertEqual([a.ok for a in audits], [True, False])

    def test_audit_reports_actionable_detail(self) -> None:
        audit = ve.audit_command("pytest -q | head -1")
        self.assertTrue(any("pipefail" in detail for detail in audit.details))


class SelectionTests(unittest.TestCase):
    def test_selection_reads_tokens_after_the_runner(self) -> None:
        selection = ve.extract_selection(
            "uv run --no-project --with pytest pytest .orchestrator/test_common.py -q -k 'brief'"
        )
        self.assertEqual(selection["scope"], "targeted")
        self.assertIn(".orchestrator/test_common.py", selection["items"])
        self.assertIn("-k=brief", selection["items"])

    def test_bare_runner_is_suite_scope(self) -> None:
        selection = ve.extract_selection("pytest -q")
        self.assertEqual(selection["scope"], "suite")
        self.assertEqual(selection["items"], [])

    def test_fingerprint_is_order_insensitive(self) -> None:
        first = ve.extract_selection("pytest tests/a.py tests/b.py")
        second = ve.extract_selection("pytest tests/b.py tests/a.py")
        self.assertEqual(first["fingerprint"], second["fingerprint"])

    def test_fingerprint_differs_by_selection(self) -> None:
        self.assertNotEqual(
            ve.extract_selection("pytest tests/a.py")["fingerprint"],
            ve.extract_selection("pytest tests/b.py")["fingerprint"],
        )

    def test_suite_run_is_broader_than_targeted(self) -> None:
        targeted = ve.extract_selection("pytest tests/a.py")
        suite = ve.extract_selection("pytest")
        self.assertTrue(ve.selection_is_broader(suite, targeted))
        self.assertFalse(ve.selection_is_broader(targeted, suite))

    def test_superset_selection_is_broader(self) -> None:
        narrow = ve.extract_selection("pytest tests/a.py")
        wide = ve.extract_selection("pytest tests/a.py tests/b.py")
        self.assertTrue(ve.selection_is_broader(wide, narrow))
        self.assertFalse(ve.selection_is_broader(narrow, narrow))


class OutcomeClassificationTests(unittest.TestCase):
    def test_zero_is_the_only_pass(self) -> None:
        self.assertEqual(ve.classify_outcome(0, runner="pytest"), ve.OUTCOME_PASSED)
        self.assertTrue(ve.outcome_is_pass(ve.OUTCOME_PASSED))
        for outcome in (ve.OUTCOME_FAILED, ve.OUTCOME_INTERRUPTED, ve.OUTCOME_NO_TESTS, ve.OUTCOME_REJECTED):
            self.assertFalse(ve.outcome_is_pass(outcome))

    def test_pytest_failure_code(self) -> None:
        self.assertEqual(ve.classify_outcome(1, runner="pytest"), ve.OUTCOME_FAILED)

    def test_pytest_interrupt_code_is_not_a_failure_or_pass(self) -> None:
        self.assertEqual(ve.classify_outcome(2, runner="pytest"), ve.OUTCOME_INTERRUPTED)

    def test_pytest_no_tests_collected_is_not_a_pass(self) -> None:
        self.assertEqual(ve.classify_outcome(5, runner="pytest"), ve.OUTCOME_NO_TESTS)

    def test_negative_return_code_is_a_signal(self) -> None:
        self.assertEqual(ve.classify_outcome(-15), ve.OUTCOME_INTERRUPTED)
        self.assertEqual(ve.signal_from_exit_code(-15), "SIGTERM")

    def test_shell_signal_code_is_a_signal(self) -> None:
        self.assertEqual(ve.classify_outcome(130), ve.OUTCOME_INTERRUPTED)
        self.assertEqual(ve.signal_from_exit_code(130), "SIGINT")

    def test_missing_exit_code_and_timeout_are_interrupted(self) -> None:
        self.assertEqual(ve.classify_outcome(None), ve.OUTCOME_INTERRUPTED)
        self.assertEqual(ve.classify_outcome(0, timed_out=True), ve.OUTCOME_INTERRUPTED)

    def test_ordinary_exit_codes_are_not_signals(self) -> None:
        for code in (0, 1, 2, 5, 127):
            self.assertIsNone(ve.signal_from_exit_code(code))


class ReceiptTests(unittest.TestCase):
    def _receipt(self, **overrides):
        payload = {
            "task_id": "ODP-ORCH-VERIFICATION-EVIDENCE-001",
            "head_sha": "a" * 40,
            "command": "pytest -q tests/unit",
            "exit_code": 0,
            "duration_seconds": 12.5,
        }
        payload.update(overrides)
        return ve.build_receipt(**payload)

    def test_receipt_binds_the_five_required_facts(self) -> None:
        receipt = self._receipt()
        self.assertEqual(receipt["head_sha"], "a" * 40)
        self.assertEqual(receipt["command"], "pytest -q tests/unit")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["duration_seconds"], 12.5)
        self.assertEqual(receipt["selection"]["items"], ["tests/unit"])
        self.assertTrue(receipt["selection"]["fingerprint"])
        self.assertTrue(receipt["passed"])
        self.assertEqual(ve.validate_receipt(receipt), [])

    def test_receipt_requires_a_head_sha(self) -> None:
        with self.assertRaises(ValueError):
            self._receipt(head_sha="")
        with self.assertRaises(ValueError):
            self._receipt(head_sha="not-a-sha")

    def test_receipt_requires_task_command_and_duration(self) -> None:
        with self.assertRaises(ValueError):
            self._receipt(task_id="")
        with self.assertRaises(ValueError):
            self._receipt(command="  ")
        with self.assertRaises(ValueError):
            self._receipt(duration_seconds=None)

    def test_masked_command_receipt_is_rejected_not_passed(self) -> None:
        receipt = self._receipt(command="pytest -q tests/unit | tail -1", exit_code=0)
        self.assertEqual(receipt["outcome"], ve.OUTCOME_REJECTED)
        self.assertFalse(receipt["passed"])
        self.assertTrue(ve.validate_receipt(receipt))

    def test_signalled_receipt_is_interrupted(self) -> None:
        receipt = self._receipt(exit_code=-15)
        self.assertEqual(receipt["outcome"], ve.OUTCOME_INTERRUPTED)
        self.assertEqual(receipt["signal"], "SIGTERM")
        self.assertFalse(receipt["passed"])

    def test_validate_receipt_flags_missing_fields(self) -> None:
        problems = ve.validate_receipt({"task_id": "T", "outcome": ve.OUTCOME_PASSED})
        self.assertTrue(any("missing field: head_sha" in item for item in problems))

    def test_validate_receipt_rejects_pass_claim_with_nonzero_exit(self) -> None:
        receipt = self._receipt()
        receipt["exit_code"] = 1
        self.assertTrue(any("requires exit_code 0" in item for item in ve.validate_receipt(receipt)))

    def test_validate_receipt_rejects_pass_claim_on_interrupted_outcome(self) -> None:
        receipt = self._receipt(exit_code=-15)
        receipt["passed"] = True
        self.assertTrue(any("claims passed" in item for item in ve.validate_receipt(receipt)))

    def test_receipt_id_is_stable_for_the_same_run(self) -> None:
        first = self._receipt(started_at="2026-08-31T00:00:00Z")
        second = self._receipt(started_at="2026-08-31T00:00:00Z")
        self.assertEqual(first["receipt_id"], second["receipt_id"])


class BaselineDedupeTests(unittest.TestCase):
    HEAD = "b" * 40

    def _receipt(self, *, outcome=ve.OUTCOME_PASSED, exit_code=0, head=None, command="pytest -q tests/unit"):
        receipt = ve.build_receipt(
            task_id="T-1",
            head_sha=head or self.HEAD,
            command=command,
            exit_code=exit_code,
            duration_seconds=1.0,
        )
        receipt["outcome"] = outcome
        receipt["passed"] = ve.outcome_is_pass(outcome)
        return receipt

    def _selection_id(self, command="pytest -q tests/unit"):
        return ve.extract_selection(command)["fingerprint"]

    def test_first_run_is_a_baseline(self) -> None:
        decision = ve.evaluate_baseline_request([], head_sha=self.HEAD, selection_id=self._selection_id())
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.kind, ve.KIND_BASELINE)
        self.assertEqual(decision.attempt, 1)

    def test_duplicate_baseline_for_same_sha_is_refused(self) -> None:
        decision = ve.evaluate_baseline_request(
            [self._receipt()], head_sha=self.HEAD, selection_id=self._selection_id()
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.kind, ve.KIND_DUPLICATE)
        self.assertIn("retry reason", decision.reason)

    def test_duplicate_after_failure_is_also_refused(self) -> None:
        decision = ve.evaluate_baseline_request(
            [self._receipt(outcome=ve.OUTCOME_FAILED, exit_code=1)],
            head_sha=self.HEAD,
            selection_id=self._selection_id(),
        )
        self.assertFalse(decision.allowed)

    def test_explicit_retry_reason_unlocks_the_rerun(self) -> None:
        decision = ve.evaluate_baseline_request(
            [self._receipt()],
            head_sha=self.HEAD,
            selection_id=self._selection_id(),
            retry_reason="reproducing an ordering-dependent failure reported in review",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.kind, ve.KIND_RETRY)
        self.assertEqual(decision.attempt, 2)

    def test_placeholder_retry_reason_is_not_explicit(self) -> None:
        for reason in ("", "retry", "rerun", "flaky", "n/a", "again"):
            with self.subTest(reason=reason):
                self.assertFalse(ve.retry_reason_is_explicit(reason))
                decision = ve.evaluate_baseline_request(
                    [self._receipt()],
                    head_sha=self.HEAD,
                    selection_id=self._selection_id(),
                    retry_reason=reason,
                )
                self.assertFalse(decision.allowed)

    def test_interrupted_prior_run_is_a_resume_not_a_duplicate(self) -> None:
        decision = ve.evaluate_baseline_request(
            [self._receipt(outcome=ve.OUTCOME_INTERRUPTED, exit_code=-15)],
            head_sha=self.HEAD,
            selection_id=self._selection_id(),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.kind, ve.KIND_RESUME)

    def test_different_head_sha_is_a_fresh_baseline(self) -> None:
        decision = ve.evaluate_baseline_request(
            [self._receipt()], head_sha="c" * 40, selection_id=self._selection_id()
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.kind, ve.KIND_BASELINE)

    def test_different_selection_on_same_sha_is_a_fresh_baseline(self) -> None:
        decision = ve.evaluate_baseline_request(
            [self._receipt()],
            head_sha=self.HEAD,
            selection_id=self._selection_id("pytest -q tests/integration"),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.kind, ve.KIND_BASELINE)

    def test_task_id_scopes_the_lookup(self) -> None:
        decision = ve.evaluate_baseline_request(
            [self._receipt()],
            head_sha=self.HEAD,
            selection_id=self._selection_id(),
            task_id="T-2",
        )
        self.assertTrue(decision.allowed)


class RerunScopeTests(unittest.TestCase):
    def _interrupted(self):
        return ve.build_receipt(
            task_id="T-1",
            head_sha="d" * 40,
            command="pytest -q tests/unit/test_alpha.py",
            exit_code=-15,
            duration_seconds=3.0,
        )

    def test_interrupted_run_repeats_the_same_selection(self) -> None:
        prior = self._interrupted()
        plan = ve.plan_rerun(prior, requested_selection=prior["selection"])
        self.assertTrue(plan.allowed)
        self.assertFalse(plan.escalated)
        self.assertEqual(plan.selection["items"], prior["selection"]["items"])

    def test_interrupted_run_does_not_authorize_a_wider_rerun(self) -> None:
        prior = self._interrupted()
        plan = ve.plan_rerun(prior, requested_selection=ve.extract_selection("pytest -q"))
        self.assertFalse(plan.allowed)
        self.assertTrue(plan.escalated)
        self.assertIn("same selection", plan.reason)
        self.assertEqual(plan.selection["items"], prior["selection"]["items"])

    def test_interrupted_run_does_not_authorize_a_superset_rerun(self) -> None:
        prior = self._interrupted()
        wider = ve.extract_selection("pytest -q tests/unit/test_alpha.py tests/unit/test_beta.py")
        plan = ve.plan_rerun(prior, requested_selection=wider)
        self.assertFalse(plan.allowed)
        self.assertTrue(plan.escalated)

    def test_settled_run_needs_an_explicit_retry_reason(self) -> None:
        settled = ve.build_receipt(
            task_id="T-1",
            head_sha="d" * 40,
            command="pytest -q tests/unit/test_alpha.py",
            exit_code=1,
            duration_seconds=3.0,
        )
        self.assertFalse(ve.plan_rerun(settled).allowed)
        plan = ve.plan_rerun(settled, retry_reason="re-measuring after a dependency pin changed")
        self.assertTrue(plan.allowed)

    def test_no_prior_receipt_is_unconstrained(self) -> None:
        plan = ve.plan_rerun(None, requested_selection=ve.extract_selection("pytest -q"))
        self.assertTrue(plan.allowed)


class ReceiptStoreTests(unittest.TestCase):
    """The store layout is shared by the writer and the finalize gate."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.store = Path(self.tmpdir.name) / "evidence"

    def _receipt(self, *, task="T-1", command="pytest -q tests/unit", exit_code=0):
        return ve.build_receipt(
            task_id=task,
            head_sha="a" * 40,
            command=command,
            exit_code=exit_code,
            duration_seconds=1.0,
        )

    def test_write_then_load(self) -> None:
        path = ve.write_receipt(self.store, self._receipt())
        self.assertTrue(path.exists())
        loaded = ve.load_receipts(self.store, task_id="T-1")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["command"], "pytest -q tests/unit")

    def test_load_is_scoped_by_task(self) -> None:
        ve.write_receipt(self.store, self._receipt(task="T-1"))
        ve.write_receipt(self.store, self._receipt(task="T-2"))
        self.assertEqual(len(ve.load_receipts(self.store, task_id="T-1")), 1)
        self.assertEqual(len(ve.load_receipts(self.store)), 2)

    def test_invalid_receipt_is_not_persisted(self) -> None:
        with self.assertRaises(ValueError):
            ve.write_receipt(self.store, self._receipt(command="pytest -q | tail -1"))
        self.assertEqual(ve.load_receipts(self.store), [])

    def test_missing_store_reads_as_empty(self) -> None:
        self.assertEqual(ve.load_receipts(self.store, task_id="T-1"), [])

    def test_unreadable_file_is_skipped_not_trusted(self) -> None:
        ve.write_receipt(self.store, self._receipt())
        (self.store / "verification-t_1-corrupt.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(len(ve.load_receipts(self.store, task_id="T-1")), 1)

    def test_foreign_json_in_the_store_is_ignored(self) -> None:
        self.store.mkdir(parents=True, exist_ok=True)
        (self.store / "verification-t_1-foreign.json").write_text('{"kind": "something-else"}', encoding="utf-8")
        self.assertEqual(ve.load_receipts(self.store, task_id="T-1"), [])


class FinalizeGateTests(unittest.TestCase):
    """Declared verification must be proven at the exact head being published."""

    HEAD = "f" * 40
    COMMAND = "pytest -q tests/unit"

    def _receipt(self, *, exit_code=0, head=None, command=None, outcome=None, task="T-GATE"):
        receipt = ve.build_receipt(
            task_id=task,
            head_sha=head or self.HEAD,
            command=command or self.COMMAND,
            exit_code=exit_code,
            duration_seconds=1.0,
        )
        if outcome is not None:
            receipt["outcome"] = outcome
            receipt["passed"] = ve.outcome_is_pass(outcome)
        return receipt

    def _gate(self, receipts, commands=None):
        return ve.evaluate_finalize_gate(
            commands=commands if commands is not None else [self.COMMAND],
            head_sha=self.HEAD,
            receipts=receipts,
            task_id="T-GATE",
        )

    def test_no_declared_commands_passes(self) -> None:
        self.assertTrue(self._gate([], commands=[]).ok)

    def test_passing_receipt_at_head_satisfies_the_gate(self) -> None:
        result = self._gate([self._receipt()])
        self.assertTrue(result.ok, result.problems)
        self.assertEqual(result.satisfied, (self.COMMAND,))

    def test_no_receipt_fails_closed(self) -> None:
        result = self._gate([])
        self.assertFalse(result.ok)
        self.assertTrue(any("no verification receipt" in item for item in result.problems))

    def test_receipt_for_another_head_does_not_count(self) -> None:
        result = self._gate([self._receipt(head="0" * 40)])
        self.assertFalse(result.ok)
        self.assertTrue(any("no verification receipt" in item for item in result.problems))

    def test_receipt_for_another_selection_does_not_count(self) -> None:
        result = self._gate([self._receipt(command="pytest -q tests/integration")])
        self.assertFalse(result.ok)

    def test_nonzero_exit_receipt_fails_closed(self) -> None:
        result = self._gate([self._receipt(exit_code=1)])
        self.assertFalse(result.ok)
        self.assertTrue(any("does not prove" in item for item in result.problems))

    def test_interrupted_receipt_fails_closed(self) -> None:
        result = self._gate([self._receipt(exit_code=-15)])
        self.assertFalse(result.ok)
        self.assertTrue(any("interrupted" in item for item in result.problems))

    def test_no_tests_collected_receipt_fails_closed(self) -> None:
        result = self._gate([self._receipt(exit_code=5, command=self.COMMAND)])
        self.assertFalse(result.ok)

    def test_rejected_receipt_fails_closed(self) -> None:
        masked = "pytest -q tests/unit | tail -1"
        receipt = self._receipt(command=masked)
        self.assertEqual(receipt["outcome"], ve.OUTCOME_REJECTED)
        result = self._gate([receipt], commands=[masked])
        self.assertFalse(result.ok)
        self.assertTrue(any("masking policy" in item for item in result.problems))

    def test_forged_pass_flag_does_not_satisfy_the_gate(self) -> None:
        receipt = self._receipt(exit_code=1)
        receipt["passed"] = True
        receipt["outcome"] = ve.OUTCOME_PASSED
        self.assertFalse(ve.receipt_proves(receipt))
        self.assertFalse(self._gate([receipt]).ok)

    def test_a_later_passing_receipt_satisfies_an_earlier_failure(self) -> None:
        failed = self._receipt(exit_code=1)
        passed = self._receipt()
        self.assertTrue(self._gate([failed, passed]).ok)

    def test_every_declared_command_must_be_proven(self) -> None:
        second = "pytest -q tests/integration"
        result = self._gate([self._receipt()], commands=[self.COMMAND, second])
        self.assertFalse(result.ok)
        self.assertEqual(result.satisfied, (self.COMMAND,))
        self.assertTrue(any(second in item for item in result.problems))

    def test_unresolvable_head_fails_closed(self) -> None:
        result = ve.evaluate_finalize_gate(commands=[self.COMMAND], head_sha="", receipts=[])
        self.assertFalse(result.ok)
        self.assertTrue(any("head SHA" in item for item in result.problems))


if __name__ == "__main__":
    unittest.main()
