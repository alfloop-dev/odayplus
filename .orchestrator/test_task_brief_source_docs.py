#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import common
import supervisor

SOURCE_DOCS = [
    "docs_archive/00_source_zips/operator_console/LATEST.json",
    "docs_archive/00_source_zips/operator_console/r5-20260715-package-7/extracted/Oday Plus Operator Console.dc.html",
]


class TaskBriefSourceDocsTests(unittest.TestCase):
    def _task(self) -> dict[str, object]:
        return {
            "id": "ODP-OC-R4-TEST",
            "title": "Use the canonical design",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
            "summary_zh": "Read the exact design source.",
            "depends_on": [],
            "artifacts": ["apps/web/example.tsx"],
            "source_docs": SOURCE_DOCS,
            "acceptance": ["The implementation matches package 7 / R5."],
            "verification": ["unzip -t package-7.zip"],
            "priority": "P0",
            "mutates_canonical": True,
            "last_update": "2026-08-02T10:00:00Z",
        }

    def test_canonical_task_brief_lists_source_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            brief_path = tmp_path / "brief.md"
            status_root = tmp_path
            # Create fake source docs
            for doc in SOURCE_DOCS:
                p = status_root / doc
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("content", encoding="utf-8")

            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            with (
                mock.patch.object(common, "load_status", return_value={"tasks": [self._task()]}),
                mock.patch.object(common, "task_brief_path", return_value=brief_path),
                mock.patch.object(common, "load_json", return_value={}),
                mock.patch.object(common, "_recent_task_activity", return_value=[]),
            ):
                result = common.write_task_brief(config, "ODP-OC-R4-TEST")

            self.assertEqual(result, brief_path)
            text = brief_path.read_text(encoding="utf-8")
            self.assertIn("## Source Documents", text)
            for source_doc in SOURCE_DOCS:
                self.assertIn(f"- {source_doc}", text)
            self.assertIn("## Acceptance", text)
            self.assertIn("- The implementation matches package 7 / R5.", text)
            self.assertIn("## Verification", text)
            self.assertIn("- `unzip -t package-7.zip`", text)
            self.assertIn("- SHA256:", text)

    def test_fallback_worker_brief_lists_source_docs(self) -> None:
        with mock.patch.object(supervisor, "load_status", return_value={"tasks": [self._task()]}):
            text = supervisor._generated_worker_task_brief({}, "ODP-OC-R4-TEST")

        self.assertIn("## Source Documents", text)
        for source_doc in SOURCE_DOCS:
            self.assertIn(f"- {source_doc}", text)
        self.assertIn("- The implementation matches package 7 / R5.", text)
        self.assertIn("- `unzip -t package-7.zip`", text)

    def test_package_10_canonical_design_doc_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "pantheon"
            status_root.mkdir()
            pkg10_doc = status_root / "docs" / "design" / "PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.md"
            pkg10_doc.parent.mkdir(parents=True)
            pkg10_doc.write_text("# Package 10 Canonical Design\n", encoding="utf-8")

            task = {
                "id": "ODP-P10-TEST-001",
                "title": "Package 10 Task",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "priority": "P0",
                "source_docs": ["docs/design/PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.md"],
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            with mock.patch.object(common, "load_status", return_value={"tasks": [task]}):
                context_files = common.execution_context_files(config, "ODP-P10-TEST-001")

            self.assertIn("docs/design/PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.md", context_files)

    def test_model_registry_blocker_stale_brief_rejection(self) -> None:
        task = {
            "id": "ODP-PRODUCTION-MODEL-REGISTRY-001",
            "title": "Model Registry Task",
            "status": "blocked",
            "owner": "Codex5",
            "reviewer": "Codex8",
            "last_update": "2026-07-29T05:36:49Z",
        }
        stale_text = (
            "# Task Brief: ODP-PRODUCTION-MODEL-REGISTRY-001\n"
            "- Status: in_progress\n"
            "- Owner: Claude3\n"
            "- Reviewer: Codex8\n"
            "- Last update: 2026-07-20T00:00:00Z\n"
        )
        self.assertTrue(common.is_task_brief_stale(stale_text, task))

        sha_val = common.task_brief_canonical_hash(task)
        fresh_text = (
            "# Task Brief: ODP-PRODUCTION-MODEL-REGISTRY-001\n"
            "- Status: blocked\n"
            "- Owner: Codex5\n"
            "- Reviewer: Codex8\n"
            "- Last update: 2026-07-29T05:36:49Z\n"
            f"- SHA256: {sha_val}\n"
            "\n"
            "## Source Documents\n"
            "- none\n"
        )
        self.assertFalse(common.is_task_brief_stale(fresh_text, task))

    def test_missing_sha256_or_source_docs_is_stale(self) -> None:
        task = {
            "id": "ODP-LEGACY-001",
            "title": "Legacy Brief Task",
            "status": "in_progress",
            "owner": "Antigravity",
            "reviewer": "Codex5",
            "last_update": "2026-08-02T11:00:00Z",
            "source_docs": ["docs/new.md"],
        }
        legacy_text = (
            "# Task Brief: ODP-LEGACY-001\n"
            "- Status: in_progress\n"
            "- Owner: Antigravity\n"
            "- Reviewer: Codex5\n"
            "- Last update: 2026-08-02T11:00:00Z\n"
        )
        self.assertTrue(common.is_task_brief_stale(legacy_text, task))

        sha_val = common.task_brief_canonical_hash(task)
        missing_docs_text = (
            "# Task Brief: ODP-LEGACY-001\n"
            "- Status: in_progress\n"
            "- Owner: Antigravity\n"
            "- Reviewer: Codex5\n"
            "- Last update: 2026-08-02T11:00:00Z\n"
            f"- SHA256: {sha_val}\n"
        )
        self.assertTrue(common.is_task_brief_stale(missing_docs_text, task))

    def test_source_docs_change_stale_brief_rejection(self) -> None:
        task = {
            "id": "ODP-SRC-CHANGE-001",
            "title": "Source Doc Change Test",
            "status": "in_progress",
            "owner": "Antigravity",
            "reviewer": "Codex5",
            "last_update": "2026-08-02T11:00:00Z",
            "source_docs": ["docs/new.md"],
        }
        sha_val = common.task_brief_canonical_hash(task)
        old_brief_text = (
            "# Task Brief: ODP-SRC-CHANGE-001\n"
            "- Status: in_progress\n"
            "- Owner: Antigravity\n"
            "- Reviewer: Codex5\n"
            "- Last update: 2026-08-02T11:00:00Z\n"
            f"- SHA256: {sha_val}\n"
            "\n"
            "## Source Documents\n"
            "- docs/old.md\n"
        )
        self.assertTrue(common.is_task_brief_stale(old_brief_text, task))

    def test_execution_context_files_without_status_file_config(self) -> None:
        empty_config: dict[str, object] = {}
        with mock.patch.object(common, "load_status", return_value={"tasks": []}):
            files = common.execution_context_files(empty_config, None)
        self.assertIn("AI_COLLABORATION_GUIDE.md", files)
        self.assertIn("ai-status.json", files)

    def test_archived_readiness_task_brief_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "pantheon"
            status_root.mkdir()
            archive_dir = status_root / "ai-task-archive" / "tasks"
            archive_dir.mkdir(parents=True)

            archived_task = {
                "id": "ODP-READINESS-ARCHIVED-001",
                "title": "Archived Readiness Task",
                "status": "done",
                "owner": "Claude",
                "reviewer": "Codex",
                "terminal_outcome": "completed",
                "source_docs": [],
            }
            snapshot = {
                "version": 1,
                "task_id": "ODP-READINESS-ARCHIVED-001",
                "archived_at": "2026-07-01T00:00:00Z",
                "terminal_status": "done",
                "terminal_outcome": "completed",
                "task": archived_task,
            }
            (archive_dir / "ODP-READINESS-ARCHIVED-001.json").write_text(json.dumps(snapshot), encoding="utf-8")

            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            with (
                mock.patch("task_archive.STATUS_ROOT", status_root),
                mock.patch("task_archive.ARCHIVE_DIR", status_root / "ai-task-archive"),
                mock.patch("task_archive.ARCHIVE_TASKS_DIR", archive_dir),
                mock.patch.object(common, "load_status", return_value={"tasks": []}),
            ):
                text, sha256_val, t_obj = common.generate_task_brief_content(config, "ODP-READINESS-ARCHIVED-001")

            self.assertEqual(t_obj["id"], "ODP-READINESS-ARCHIVED-001")
            self.assertIn("Status: done", text)
            self.assertTrue(bool(sha256_val))

    def test_owner_reviewer_hash_equality(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "pantheon"
            status_root.mkdir()

            task = {
                "id": "ODP-HASH-EQ-001",
                "title": "Hash Equality Test",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "last_update": "2026-08-02T11:00:00Z",
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            with mock.patch.object(common, "load_status", return_value={"tasks": [task]}):
                text_owner, hash_owner, _ = common.generate_task_brief_content(config, "ODP-HASH-EQ-001", generated_at="2026-08-02T11:05:00Z")
                text_reviewer, hash_reviewer, _ = common.generate_task_brief_content(config, "ODP-HASH-EQ-001", generated_at="2026-08-02T11:05:00Z")

            self.assertEqual(text_owner, text_reviewer)
            self.assertEqual(hash_owner, hash_reviewer)

    def test_stale_brief_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "pantheon"
            status_root.mkdir()
            brief_dir = status_root / ".orchestrator" / "task-briefs"
            brief_dir.mkdir(parents=True)

            stale_brief = brief_dir / "odp_stale_001.md"
            stale_brief.write_text("# Task Brief: ODP-STALE-001\n- Status: in_progress\n- Owner: OldOwner\n", encoding="utf-8")

            task = {
                "id": "ODP-STALE-001",
                "title": "Stale Task",
                "status": "blocked",
                "owner": "NewOwner",
                "reviewer": "Reviewer1",
                "last_update": "2026-08-02T11:00:00Z",
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            worktree_path = Path(tmpdir) / "worktree"
            worktree_path.mkdir()

            req = supervisor.DeliveryRequest(
                agent_id="Antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="ODP-STALE-001",
                reason="owned_ready_dispatch",
                context_files=[".orchestrator/task-briefs/odp_stale_001.md"],
            )

            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(common, "load_status", return_value={"tasks": [task]}),
            ):
                _ = supervisor.materialize_worker_context_files(config, req, worktree_path)

            dest_brief = worktree_path / ".orchestrator" / "task-briefs" / "odp_stale_001.md"
            self.assertTrue(dest_brief.exists())
            text = dest_brief.read_text(encoding="utf-8")
            self.assertIn("Status: blocked", text)
            self.assertIn("Owner: NewOwner", text)

    def test_file_or_dir_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            f1 = tmp_path / "f1.txt"
            f2 = tmp_path / "f2.txt"
            f1.write_text("hello world", encoding="utf-8")
            f2.write_text("hello world", encoding="utf-8")

            h1 = supervisor._file_or_dir_hash(f1)
            h2 = supervisor._file_or_dir_hash(f2)
            self.assertIsNotNone(h1)
            self.assertEqual(len(h1), 64)
            self.assertEqual(h1, h2)

            f2.write_text("different content", encoding="utf-8")
            h2_diff = supervisor._file_or_dir_hash(f2)
            self.assertNotEqual(h1, h2_diff)

            non_existent = tmp_path / "non_existent.txt"
            self.assertIsNone(supervisor._file_or_dir_hash(non_existent))

            d1 = tmp_path / "dir1"
            d1.mkdir()
            (d1 / "a.txt").write_text("file a", encoding="utf-8")
            dh1 = supervisor._file_or_dir_hash(d1)
            self.assertIsNotNone(dh1)
            self.assertEqual(len(dh1), 64)

    def test_tracked_worktree_file_no_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "pantheon"
            status_root.mkdir()
            src_doc = status_root / "docs" / "evidence" / "source.txt"
            src_doc.parent.mkdir(parents=True)
            src_doc.write_text("version 1 (supervisor tip)", encoding="utf-8")

            worktree_path = Path(tmpdir) / "worktree"
            worktree_path.mkdir()
            dest_doc = worktree_path / "docs" / "evidence" / "source.txt"
            dest_doc.parent.mkdir(parents=True)
            dest_doc.write_text("version 0 (worker task-owned edits)", encoding="utf-8")

            task = {
                "id": "ODP-NOCLOBBER-001",
                "title": "No Clobber Test",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "source_docs": ["docs/evidence/source.txt"],
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            req = supervisor.DeliveryRequest(
                agent_id="Antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="ODP-NOCLOBBER-001",
                reason="owned_ready_dispatch",
                context_files=["docs/evidence/source.txt"],
            )

            # When destination is tracked in worktree (_is_tracked_in_worktree=True),
            # task-owned edits in worktree MUST NOT be overwritten!
            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(common, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(supervisor, "_is_tracked_in_worktree", return_value=True),
            ):
                supervisor.materialize_worker_context_files(config, req, worktree_path)

            self.assertEqual(dest_doc.read_text(encoding="utf-8"), "version 0 (worker task-owned edits)")

            # When destination is untracked in worktree (_is_tracked_in_worktree=False),
            # differing untracked destination IS updated to match supervisor root.
            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(common, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(supervisor, "_is_tracked_in_worktree", return_value=False),
            ):
                supervisor.materialize_worker_context_files(config, req, worktree_path)

            self.assertEqual(dest_doc.read_text(encoding="utf-8"), "version 1 (supervisor tip)")

    def test_fail_closed_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "pantheon"
            status_root.mkdir()

            task_missing_doc = {
                "id": "ODP-FAIL-001",
                "title": "P0 Task Missing Doc",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "priority": "P0",
                "source_docs": ["non_existent_file.txt"],
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            with mock.patch.object(common, "load_status", return_value={"tasks": [task_missing_doc]}):
                with self.assertRaises(ValueError) as ctx:
                    common.execution_context_files(config, "ODP-FAIL-001")
                self.assertIn("missing source document", str(ctx.exception))

            task_traversal = {
                "id": "ODP-FAIL-002",
                "title": "P0 Task Traversal Doc",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "priority": "P0",
                "source_docs": ["../secret.txt"],
            }
            with mock.patch.object(common, "load_status", return_value={"tasks": [task_traversal]}):
                with self.assertRaises(ValueError) as ctx:
                    common.execution_context_files(config, "ODP-FAIL-002")
                self.assertIn("traversal path rejected", str(ctx.exception))

            empty_dir = status_root / "empty_dir"
            empty_dir.mkdir()
            task_empty_dir = {
                "id": "ODP-FAIL-003",
                "title": "P0 Task Empty Dir Doc",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "priority": "P0",
                "source_docs": ["empty_dir"],
            }
            with mock.patch.object(common, "load_status", return_value={"tasks": [task_empty_dir]}):
                with self.assertRaises(ValueError) as ctx:
                    common.execution_context_files(config, "ODP-FAIL-003")
                self.assertIn("directory without inventory manifest", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
