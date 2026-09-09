#!/usr/bin/env python3
"""Focused tests for archive recovery invalidation and resolver semantics.

Verifies:
- Authorization rules (coordination task reviewer only, independent reviewer/owner)
- Target validation (archive only, reconstructed recovery only, hash check)
- Dry run (zero side effects) vs. Confirm (append-only correction, audit log)
- Immutability of original snapshots and index
- Fail-closed behavior on corrupt/invalid correction files
- TaskResolver and ai-status show effective blocked semantics
- Downstream task admission blocking for ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001
  and ODP-MODELREADY-QUALITY-NULLABLE-001
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

_TEST_CONFIG = THIS_DIR / "config.example.json"

import ai_status
import supervisor
import task_archive


def canonical_test_environment(tmp_status_root: Path):
    return mock.patch.dict(
        os.environ,
        {
            "ORCH_CONFIG_PATH": str(_TEST_CONFIG),
            "PANTHEON_CONFIG_PATH": str(_TEST_CONFIG),
            "ORCH_STATUS_ROOT": str(tmp_status_root),
            "PANTHEON_STATUS_ROOT": str(tmp_status_root),
        },
        clear=False,
    )


class ArchiveRecoveryInvalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir_handle = tempfile.TemporaryDirectory(prefix="orch-invalidation-test-")
        self.status_root = Path(self.tmpdir_handle.name).resolve()

        self.archive_dir = self.status_root / "ai-task-archive"
        self.tasks_dir = self.archive_dir / "tasks"
        self.corrections_dir = self.archive_dir / "corrections"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.corrections_dir.mkdir(parents=True, exist_ok=True)

        self.index_file = self.archive_dir / "index.json"
        self.log_file = self.status_root / "ai-activity-log.jsonl"
        self.status_file = self.status_root / "ai-status.json"

        # Coordination task on active board: Owner=Antigravity3, Reviewer=Codex
        self.coord_task = {
            "id": "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
            "title": "Coordination task for recovery execution",
            "status": "in_progress",
            "owner": "Antigravity3",
            "reviewer": "Codex",
            "depends_on": [],
        }

        # Downstream task 1
        self.downstream_task_1 = {
            "id": "ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001",
            "title": "Downstream closeout task 1",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Codex2",
            "depends_on": ["ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001"],
        }

        # Downstream task 2
        self.downstream_task_2 = {
            "id": "ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001",
            "title": "Downstream cutover task 2",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": ["ODP-MODELREADY-QUALITY-NULLABLE-001"],
        }

        self.state = {
            "tasks": [
                deepcopy(self.coord_task),
                deepcopy(self.downstream_task_1),
                deepcopy(self.downstream_task_2),
            ],
            "agents": [
                {"name": "Codex", "status": "idle"},
                {"name": "Codex2", "status": "idle"},
                {"name": "Antigravity3", "status": "working"},
                {"name": "Claude", "status": "idle"},
            ],
            "handoffs": [],
            "blockers": [],
        }

        # Reconstructed archive snapshot 1
        self.snap_1 = {
            "version": 1,
            "task_id": "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
            "archived_at": "2026-09-03T17:48:49Z",
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": {
                "id": "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
                "title": "Audit merge queue disposition",
                "status": "done",
                "terminal_outcome": "completed",
                "depends_on": [],
                "history_recovery": {
                    "reconstructed": True,
                    "record_kind": "reconstructed_done",
                    "evidence_tier": "reconstructable_done",
                    "gaps": [],
                },
            },
        }

        # Reconstructed archive snapshot 2
        self.snap_2 = {
            "version": 1,
            "task_id": "ODP-MODELREADY-QUALITY-NULLABLE-001",
            "archived_at": "2026-09-03T17:48:49Z",
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": {
                "id": "ODP-MODELREADY-QUALITY-NULLABLE-001",
                "title": "Quality nullable check",
                "status": "done",
                "terminal_outcome": "completed",
                "depends_on": [],
                "history_recovery": {
                    "reconstructed": True,
                    "record_kind": "reconstructed_done",
                    "evidence_tier": "reconstructable_done",
                    "gaps": [],
                },
            },
        }

        # Standard non-reconstructed archive snapshot (historical)
        self.snap_normal = {
            "version": 1,
            "task_id": "NORMAL-TASK-001",
            "archived_at": "2026-09-01T12:00:00Z",
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": {
                "id": "NORMAL-TASK-001",
                "title": "Normal completed historical task",
                "status": "done",
                "terminal_outcome": "completed",
                "depends_on": [],
            },
        }

        # Write initial snapshot files
        self.snap_1_path = self.tasks_dir / "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001.json"
        self.snap_2_path = self.tasks_dir / "ODP-MODELREADY-QUALITY-NULLABLE-001.json"
        self.snap_normal_path = self.tasks_dir / "NORMAL-TASK-001.json"

        self.snap_1_path.write_text(json.dumps(self.snap_1, indent=2), encoding="utf-8")
        self.snap_2_path.write_text(json.dumps(self.snap_2, indent=2), encoding="utf-8")
        self.snap_normal_path.write_text(json.dumps(self.snap_normal, indent=2), encoding="utf-8")

        self.snap_1_bytes = self.snap_1_path.read_bytes()
        self.snap_1_sha256 = hashlib.sha256(self.snap_1_bytes).hexdigest()

        self.snap_2_bytes = self.snap_2_path.read_bytes()
        self.snap_2_sha256 = hashlib.sha256(self.snap_2_bytes).hexdigest()

        # Write index
        self.initial_index = {
            "version": 1,
            "updated_at": "2026-09-03T17:48:49Z",
            "counts": {"total": 3, "completed": 3, "superseded": 0},
            "recent_terminal_ids": [
                "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
                "ODP-MODELREADY-QUALITY-NULLABLE-001",
                "NORMAL-TASK-001",
            ],
        }
        self.index_file.write_text(json.dumps(self.initial_index, indent=2), encoding="utf-8")
        self.initial_index_bytes = self.index_file.read_bytes()

    def tearDown(self) -> None:
        self.tmpdir_handle.cleanup()

    def _run_invalidate(
        self,
        args: list[str],
        actor: str = "Codex",
        state: dict | None = None,
    ) -> tuple[int, str]:
        current_state = state if state is not None else self.state
        out = io.StringIO()
        with (
            canonical_test_environment(self.status_root),
            mock.patch.dict(os.environ, {"AI_NAME": actor}, clear=False),
            mock.patch.object(task_archive, "STATUS_ROOT", self.status_root),
            mock.patch.object(task_archive, "ARCHIVE_DIR", self.archive_dir),
            mock.patch.object(task_archive, "ARCHIVE_TASKS_DIR", self.tasks_dir),
            mock.patch.object(task_archive, "ARCHIVE_CORRECTIONS_DIR", self.corrections_dir),
            mock.patch.object(task_archive, "ARCHIVE_INDEX_FILE", self.index_file),
            mock.patch.object(ai_status, "STATUS_ROOT", self.status_root),
            mock.patch.object(ai_status, "LOG_FILE", self.log_file),
            mock.patch("sys.stdout", out),
        ):
            try:
                ai_status.command_archive_recovery_invalidate(current_state, args)
                return 0, out.getvalue()
            except SystemExit as exc:
                return (exc.code if isinstance(exc.code, int) else 1), str(exc)

    def test_authorization_requires_coordination_task_reviewer(self) -> None:
        # Actor is Antigravity3 (owner, not reviewer) -> must be rejected
        code, err = self._run_invalidate(
            [
                "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
                "--coordination-task",
                "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
                "--reason",
                "Review gap identified",
                "--evidence-ref",
                "docs/evidence/gap.json",
            ],
            actor="Antigravity3",
        )
        self.assertNotEqual(code, 0)
        self.assertIn("Unauthorized", err)

        # Actor is unrelated agent (Claude) -> must be rejected
        code, err = self._run_invalidate(
            [
                "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
                "--coordination-task",
                "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
                "--reason",
                "Review gap identified",
                "--evidence-ref",
                "docs/evidence/gap.json",
            ],
            actor="Claude",
        )
        self.assertNotEqual(code, 0)
        self.assertIn("Unauthorized", err)

    def test_authorization_rejects_non_independent_reviewer_and_owner(self) -> None:
        bad_state = deepcopy(self.state)
        bad_state["tasks"][0]["owner"] = "Codex"
        bad_state["tasks"][0]["reviewer"] = "Codex"

        code, err = self._run_invalidate(
            [
                "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
                "--coordination-task",
                "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
                "--reason",
                "Review gap",
                "--evidence-ref",
                "docs/evidence/gap.json",
            ],
            actor="Codex",
            state=bad_state,
        )
        self.assertNotEqual(code, 0)
        self.assertIn("must be independent", err)

    def test_target_must_be_reconstructed_recovery_snapshot(self) -> None:
        # Target is normal non-reconstructed archive task -> must be rejected
        code, err = self._run_invalidate(
            [
                "NORMAL-TASK-001",
                "--coordination-task",
                "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
                "--reason",
                "Try to invalidate normal archive",
                "--evidence-ref",
                "docs/evidence/gap.json",
            ],
            actor="Codex",
        )
        self.assertNotEqual(code, 0)
        self.assertIn("not a reconstructed history recovery", err)

    def test_target_on_active_board_is_rejected(self) -> None:
        code, err = self._run_invalidate(
            [
                "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
                "--coordination-task",
                "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
                "--reason",
                "Target is active",
                "--evidence-ref",
                "docs/evidence/gap.json",
            ],
            actor="Codex",
        )
        self.assertNotEqual(code, 0)
        self.assertIn("currently active on the board", err)

    def test_stale_expected_sha256_is_rejected(self) -> None:
        code, err = self._run_invalidate(
            [
                "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
                "--coordination-task",
                "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
                "--reason",
                "Review gap",
                "--evidence-ref",
                "docs/evidence/gap.json",
                "--expected-sha256",
                "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            ],
            actor="Codex",
        )
        self.assertNotEqual(code, 0)
        self.assertIn("Stale snapshot hash", err)

    def test_dry_run_leaves_all_state_completely_unmutated(self) -> None:
        code, output = self._run_invalidate(
            [
                "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
                "--coordination-task",
                "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
                "--reason",
                "Dry run test",
                "--evidence-ref",
                "docs/evidence/gap.json",
                "--expected-sha256",
                self.snap_1_sha256,
            ],
            actor="Codex",
        )
        self.assertEqual(code, 0)
        parsed = json.loads(output)
        self.assertEqual(parsed["status"], "dry_run")
        self.assertEqual(parsed["effective_status"], "blocked")
        self.assertFalse(parsed["dependency_satisfied"])

        # Confirm zero side effects
        corr_file = self.corrections_dir / "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001.json"
        self.assertFalse(corr_file.exists())
        self.assertFalse(self.log_file.exists())
        self.assertEqual(self.snap_1_path.read_bytes(), self.snap_1_bytes)
        self.assertEqual(self.index_file.read_bytes(), self.initial_index_bytes)

    def test_confirm_writes_correction_and_audit_log_without_touching_snapshot_or_index(self) -> None:
        code, output = self._run_invalidate(
            [
                "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
                "--coordination-task",
                "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
                "--reason",
                "Human/Ops decision missing for merge queue audit",
                "--evidence-ref",
                "docs/evidence/execution-control/blocker.json",
                "--expected-sha256",
                self.snap_1_sha256,
                "--confirm",
            ],
            actor="Codex",
        )
        self.assertEqual(code, 0)
        receipt = json.loads(output)
        self.assertEqual(receipt["status"], "applied")
        self.assertEqual(receipt["effective_status"], "blocked")

        # Check correction file written
        corr_file = self.corrections_dir / "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001.json"
        self.assertTrue(corr_file.exists())
        corr_data = json.loads(corr_file.read_text(encoding="utf-8"))
        self.assertEqual(corr_data["schema_version"], 1)
        self.assertEqual(corr_data["type"], "archive_recovery_invalidation")
        self.assertEqual(corr_data["task_id"], "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001")
        self.assertEqual(corr_data["snapshot_sha256"], self.snap_1_sha256)
        self.assertEqual(corr_data["actor"], "Codex")
        self.assertEqual(corr_data["coordination_task_id"], "ORCH-ARCHIVE-HISTORY-EXECUTE-003")
        self.assertEqual(corr_data["reason"], "Human/Ops decision missing for merge queue audit")
        self.assertEqual(corr_data["evidence_ref"], "docs/evidence/execution-control/blocker.json")
        self.assertEqual(corr_data["effective_status"], "blocked")
        self.assertFalse(corr_data["dependency_satisfied"])

        # Check audit log written
        self.assertTrue(self.log_file.exists())
        log_lines = self.log_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(log_lines), 1)
        log_entry = json.loads(log_lines[0])
        self.assertEqual(log_entry["type"], "archive_recovery_invalidate")
        self.assertEqual(log_entry["agent"], "Codex")
        self.assertEqual(log_entry["task_id"], "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001")

        # Crucial immutability checks
        self.assertEqual(self.snap_1_path.read_bytes(), self.snap_1_bytes)
        self.assertEqual(self.index_file.read_bytes(), self.initial_index_bytes)

    def test_idempotent_retry_and_conflict_rejection(self) -> None:
        args = [
            "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
            "--coordination-task",
            "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
            "--reason",
            "Human/Ops decision missing",
            "--evidence-ref",
            "docs/evidence/blocker.json",
            "--confirm",
        ]
        # 1st run: applied
        code1, out1 = self._run_invalidate(args, actor="Codex")
        self.assertEqual(code1, 0)
        self.assertEqual(json.loads(out1)["status"], "applied")

        # 2nd run with same args: already_invalidated
        code2, out2 = self._run_invalidate(args, actor="Codex")
        self.assertEqual(code2, 0)
        self.assertEqual(json.loads(out2)["status"], "already_invalidated")

        # 3rd run with conflicting reason: rejected
        conflict_args = [
            "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
            "--coordination-task",
            "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
            "--reason",
            "Different conflicting reason",
            "--evidence-ref",
            "docs/evidence/blocker.json",
            "--confirm",
        ]
        code3, err3 = self._run_invalidate(conflict_args, actor="Codex")
        self.assertNotEqual(code3, 0)
        self.assertIn("cannot overwrite with conflicting invalidation", err3)

    def test_resolver_and_show_semantics_for_corrected_and_uncorrected_tasks(self) -> None:
        with (
            canonical_test_environment(self.status_root),
            mock.patch.object(task_archive, "STATUS_ROOT", self.status_root),
            mock.patch.object(task_archive, "ARCHIVE_DIR", self.archive_dir),
            mock.patch.object(task_archive, "ARCHIVE_TASKS_DIR", self.tasks_dir),
            mock.patch.object(task_archive, "ARCHIVE_CORRECTIONS_DIR", self.corrections_dir),
            mock.patch.object(task_archive, "ARCHIVE_INDEX_FILE", self.index_file),
            mock.patch.object(ai_status, "STATUS_ROOT", self.status_root),
        ):
            resolver = task_archive.TaskResolver(self.state["tasks"])

            # 1. Before correction: NORMAL-TASK-001 and ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001 are satisfied
            self.assertTrue(resolver.dependency_satisfied("NORMAL-TASK-001"))
            self.assertEqual(resolver.dependency_status("NORMAL-TASK-001"), "done")
            self.assertTrue(resolver.dependency_satisfied("ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001"))
            self.assertEqual(resolver.dependency_status("ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001"), "done")

            # 2. Apply correction to ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001
            code, _ = self._run_invalidate(
                [
                    "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
                    "--coordination-task",
                    "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
                    "--reason",
                    "Invalidating audit completion",
                    "--evidence-ref",
                    "docs/evidence/blocker.json",
                    "--confirm",
                ],
                actor="Codex",
            )
            self.assertEqual(code, 0)

            # 3. After correction: Fresh resolver sees effective blocked
            fresh_resolver = task_archive.TaskResolver(self.state["tasks"])
            self.assertFalse(fresh_resolver.dependency_satisfied("ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001"))
            self.assertEqual(fresh_resolver.dependency_status("ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001"), "blocked")

            # Effective task dict has status=blocked and blocked_by_invalidation=True
            effective_task = fresh_resolver.get("ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001")
            self.assertIsNotNone(effective_task)
            self.assertEqual(effective_task["status"], "blocked")
            self.assertIsNone(effective_task.get("terminal_outcome"))
            self.assertTrue(effective_task.get("blocked_by_invalidation"))

            # Uncorrected normal task is completely unaffected
            self.assertTrue(fresh_resolver.dependency_satisfied("NORMAL-TASK-001"))
            self.assertEqual(fresh_resolver.dependency_status("NORMAL-TASK-001"), "done")

            # load_archived_snapshot still returns exact original historical bytes
            raw_snap = task_archive.load_archived_snapshot("ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001")
            self.assertEqual(raw_snap["task"]["status"], "done")
            self.assertEqual(raw_snap["terminal_outcome"], "completed")

            # Test ai-status show output
            out_show = io.StringIO()
            with mock.patch("sys.stdout", out_show):
                ai_status.command_show(self.state, ["ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001"])
            show_data = json.loads(out_show.getvalue())
            self.assertEqual(show_data["source"], "archive")
            self.assertEqual(show_data["effective_status"], "blocked")
            self.assertEqual(show_data["effective_task"]["status"], "blocked")
            self.assertEqual(show_data["correction"]["task_id"], "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001")
            self.assertEqual(show_data["snapshot"]["task"]["status"], "done")

    def test_fail_closed_on_corrupt_correction_record(self) -> None:
        corr_path = self.corrections_dir / "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001.json"

        # Scenario A: Malformed JSON file
        corr_path.write_text("{ broken json", encoding="utf-8")

        with (
            canonical_test_environment(self.status_root),
            mock.patch.object(task_archive, "STATUS_ROOT", self.status_root),
            mock.patch.object(task_archive, "ARCHIVE_DIR", self.archive_dir),
            mock.patch.object(task_archive, "ARCHIVE_TASKS_DIR", self.tasks_dir),
            mock.patch.object(task_archive, "ARCHIVE_CORRECTIONS_DIR", self.corrections_dir),
            mock.patch.object(task_archive, "ARCHIVE_INDEX_FILE", self.index_file),
        ):
            resolver = task_archive.TaskResolver(self.state["tasks"])
            self.assertFalse(resolver.dependency_satisfied("ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001"))
            self.assertEqual(resolver.dependency_status("ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001"), "blocked")
            task = resolver.get("ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001")
            self.assertEqual(task["status"], "blocked")
            self.assertIn("corrupt correction", task["next"])

        # Scenario B: Hash mismatch in correction record
        bad_hash_corr = {
            "schema_version": 1,
            "type": "archive_recovery_invalidation",
            "task_id": "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
            "snapshot_sha256": "badhash0000000000000000000000000000000000000000000000000000000000",
            "invalidated_at": "2026-09-09T00:00:00Z",
            "actor": "Codex",
            "coordination_task_id": "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
            "reason": "Test",
            "evidence_ref": "Test",
        }
        corr_path.write_text(json.dumps(bad_hash_corr), encoding="utf-8")

        with (
            canonical_test_environment(self.status_root),
            mock.patch.object(task_archive, "STATUS_ROOT", self.status_root),
            mock.patch.object(task_archive, "ARCHIVE_DIR", self.archive_dir),
            mock.patch.object(task_archive, "ARCHIVE_TASKS_DIR", self.tasks_dir),
            mock.patch.object(task_archive, "ARCHIVE_CORRECTIONS_DIR", self.corrections_dir),
            mock.patch.object(task_archive, "ARCHIVE_INDEX_FILE", self.index_file),
        ):
            resolver = task_archive.TaskResolver(self.state["tasks"])
            self.assertFalse(resolver.dependency_satisfied("ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001"))
            self.assertEqual(resolver.dependency_status("ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001"), "blocked")
            task = resolver.get("ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001")
            self.assertEqual(task["status"], "blocked")
            self.assertIn("snapshot_sha256 mismatch", task["next"])

    def test_downstream_task_admission_rejection_for_both_affected_tasks(self) -> None:
        """Verify supervisor dependencies_satisfied blocks downstream admission after invalidation."""
        with (
            canonical_test_environment(self.status_root),
            mock.patch.object(task_archive, "STATUS_ROOT", self.status_root),
            mock.patch.object(task_archive, "ARCHIVE_DIR", self.archive_dir),
            mock.patch.object(task_archive, "ARCHIVE_TASKS_DIR", self.tasks_dir),
            mock.patch.object(task_archive, "ARCHIVE_CORRECTIONS_DIR", self.corrections_dir),
            mock.patch.object(task_archive, "ARCHIVE_INDEX_FILE", self.index_file),
            mock.patch.object(ai_status, "STATUS_ROOT", self.status_root),
        ):
            done_statuses = {"done", "completed"}

            # Before invalidation: both downstream tasks are satisfied
            resolver = task_archive.TaskResolver(self.state["tasks"])
            self.assertTrue(
                supervisor.dependencies_satisfied(
                    self.downstream_task_1, resolver, done_statuses
                )
            )
            self.assertTrue(
                supervisor.dependencies_satisfied(
                    self.downstream_task_2, resolver, done_statuses
                )
            )

            # Invalidate Task 1 (ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001)
            code1, _ = self._run_invalidate(
                [
                    "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
                    "--coordination-task",
                    "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
                    "--reason",
                    "Human/Ops authority missing",
                    "--evidence-ref",
                    "docs/evidence/gap1.json",
                    "--confirm",
                ],
                actor="Codex",
            )
            self.assertEqual(code1, 0)

            # Invalidate Task 2 (ODP-MODELREADY-QUALITY-NULLABLE-001)
            code2, _ = self._run_invalidate(
                [
                    "ODP-MODELREADY-QUALITY-NULLABLE-001",
                    "--coordination-task",
                    "ORCH-ARCHIVE-HISTORY-EXECUTE-003",
                    "--reason",
                    "ModelReadyRecord nullable test unverified",
                    "--evidence-ref",
                    "docs/evidence/gap2.json",
                    "--confirm",
                ],
                actor="Codex",
            )
            self.assertEqual(code2, 0)

            # After invalidation: both downstream admissions MUST be False (blocked)
            fresh_resolver = task_archive.TaskResolver(self.state["tasks"])
            self.assertFalse(
                supervisor.dependencies_satisfied(
                    self.downstream_task_1, fresh_resolver, done_statuses
                )
            )
            self.assertFalse(
                supervisor.dependencies_satisfied(
                    self.downstream_task_2, fresh_resolver, done_statuses
                )
            )


if __name__ == "__main__":
    unittest.main()
