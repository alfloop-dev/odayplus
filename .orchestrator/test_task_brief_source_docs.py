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

            # When destination is tracked in worktree (_is_tracked_in_worktree=True) and differs,
            # it MUST fail closed with ValueError and MUST NOT overwrite task-owned edits!
            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(common, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(supervisor, "_is_tracked_in_worktree", return_value=True),
            ):
                with self.assertRaises(ValueError) as ctx:
                    supervisor.materialize_worker_context_files(config, req, worktree_path)
                self.assertIn("tracked document 'docs/evidence/source.txt' hash mismatch", str(ctx.exception))

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

    def test_destination_escape_file_symlink_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            status_root = tmp_path / "pantheon"
            status_root.mkdir()
            src_doc = status_root / "docs" / "source.txt"
            src_doc.parent.mkdir(parents=True)
            src_doc.write_text("CANONICAL", encoding="utf-8")

            outside_dir = tmp_path / "outside"
            outside_dir.mkdir()
            outside_file = outside_dir / "source.txt"
            outside_file.write_text("DO NOT TOUCH", encoding="utf-8")

            worktree_path = tmp_path / "worktree"
            worktree_path.mkdir()
            (worktree_path / "docs").symlink_to(outside_dir)

            task = {
                "id": "ODP-ESCAPE-001",
                "title": "Escape Test 1",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "priority": "P0",
                "source_docs": ["docs/source.txt"],
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}
            req = supervisor.DeliveryRequest(
                agent_id="Antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="ODP-ESCAPE-001",
                reason="owned_ready_dispatch",
                context_files=["docs/source.txt"],
            )

            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(common, "load_status", return_value={"tasks": [task]}),
            ):
                with self.assertRaises(ValueError) as ctx:
                    supervisor.materialize_worker_context_files(config, req, worktree_path)
                self.assertIn("escapes workspace root", str(ctx.exception))

            self.assertEqual(outside_file.read_text(encoding="utf-8"), "DO NOT TOUCH")

    def test_destination_escape_dir_symlink_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            status_root = tmp_path / "pantheon"
            status_root.mkdir()
            src_dir = status_root / "docs"
            src_dir.mkdir(parents=True)
            (src_dir / "manifest.json").write_text("{}", encoding="utf-8")
            (src_dir / "source.txt").write_text("CANONICAL", encoding="utf-8")

            outside_dir = tmp_path / "outside"
            outside_dir.mkdir()
            outside_file = outside_dir / "source.txt"
            outside_file.write_text("DO NOT TOUCH", encoding="utf-8")

            worktree_path = tmp_path / "worktree"
            worktree_path.mkdir()
            (worktree_path / "docs").symlink_to(outside_dir)

            task = {
                "id": "ODP-ESCAPE-002",
                "title": "Escape Test 2",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "priority": "P0",
                "source_docs": ["docs"],
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}
            req = supervisor.DeliveryRequest(
                agent_id="Antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="ODP-ESCAPE-002",
                reason="owned_ready_dispatch",
                context_files=["docs"],
            )

            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(common, "load_status", return_value={"tasks": [task]}),
            ):
                with self.assertRaises(ValueError) as ctx:
                    supervisor.materialize_worker_context_files(config, req, worktree_path)
                self.assertIn("escapes workspace root", str(ctx.exception))

            self.assertEqual(outside_file.read_text(encoding="utf-8"), "DO NOT TOUCH")

    def test_destination_escape_direct_file_symlink_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            status_root = tmp_path / "pantheon"
            status_root.mkdir()
            src_doc = status_root / "docs" / "source.txt"
            src_doc.parent.mkdir(parents=True)
            src_doc.write_text("CANONICAL", encoding="utf-8")

            outside_dir = tmp_path / "outside"
            outside_dir.mkdir()
            outside_file = outside_dir / "source.txt"
            outside_file.write_text("DO NOT TOUCH", encoding="utf-8")

            worktree_path = tmp_path / "worktree"
            dest_dir = worktree_path / "docs"
            dest_dir.mkdir(parents=True)
            (dest_dir / "source.txt").symlink_to(outside_file)

            task = {
                "id": "ODP-ESCAPE-003",
                "title": "Escape Test 3",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "priority": "P0",
                "source_docs": ["docs/source.txt"],
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}
            req = supervisor.DeliveryRequest(
                agent_id="Antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="ODP-ESCAPE-003",
                reason="owned_ready_dispatch",
                context_files=["docs/source.txt"],
            )

            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(common, "load_status", return_value={"tasks": [task]}),
            ):
                with self.assertRaises(ValueError) as ctx:
                    supervisor.materialize_worker_context_files(config, req, worktree_path)
                self.assertIn("escapes workspace root", str(ctx.exception))

            self.assertEqual(outside_file.read_text(encoding="utf-8"), "DO NOT TOUCH")

    def test_tracked_source_hash_mismatch_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            status_root = tmp_path / "pantheon"
            status_root.mkdir()
            src_doc = status_root / "docs" / "source.md"
            src_doc.parent.mkdir(parents=True)
            src_doc.write_text("CANONICAL-V2", encoding="utf-8")

            worktree_path = tmp_path / "worktree"
            worktree_path.mkdir()
            dest_doc = worktree_path / "docs" / "source.md"
            dest_doc.parent.mkdir(parents=True)
            dest_doc.write_text("STALE-V1", encoding="utf-8")

            task = {
                "id": "ODP-B7-TEST-001",
                "title": "Tracked Freshness Test",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "priority": "P0",
                "source_docs": ["docs/source.md"],
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            req = supervisor.DeliveryRequest(
                agent_id="Antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="ODP-B7-TEST-001",
                reason="owned_ready_dispatch",
                context_files=["docs/source.md"],
            )

            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(common, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(supervisor, "_is_tracked_in_worktree", return_value=True),
            ):
                with self.assertRaises(ValueError) as ctx:
                    supervisor.materialize_worker_context_files(config, req, worktree_path)
                self.assertIn("tracked document 'docs/source.md' hash mismatch", str(ctx.exception))

            self.assertEqual(dest_doc.read_text(encoding="utf-8"), "STALE-V1")

    def test_immutable_source_manifest_in_metadata_and_owner_reviewer_equality(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            status_root = tmp_path / "pantheon"
            status_root.mkdir()
            src_doc = status_root / "docs" / "source.md"
            src_doc.parent.mkdir(parents=True)
            src_doc.write_text("CANONICAL CONTENT", encoding="utf-8")

            worktree_owner = tmp_path / "worktree_owner"
            worktree_owner.mkdir()

            worktree_reviewer = tmp_path / "worktree_reviewer"
            worktree_reviewer.mkdir()

            task = {
                "id": "ODP-B8-TEST-001",
                "title": "Manifest Test",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "priority": "P0",
                "source_docs": ["docs/source.md"],
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            req_owner = supervisor.DeliveryRequest(
                agent_id="Antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="ODP-B8-TEST-001",
                reason="owned_ready_dispatch",
                context_files=["docs/source.md"],
            )

            req_reviewer = supervisor.DeliveryRequest(
                agent_id="Codex5",
                provider="codex",
                delivery_mode="codex",
                message="review",
                task_id="ODP-B8-TEST-001",
                reason="review_dispatch",
                context_files=["docs/source.md"],
            )

            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(common, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(supervisor, "_is_tracked_in_worktree", return_value=False),
            ):
                supervisor.materialize_worker_context_files(config, req_owner, worktree_owner)
                supervisor.materialize_worker_context_files(config, req_reviewer, worktree_reviewer)

            manifest_owner = req_owner.metadata.get("materialized_source_manifest")
            manifest_reviewer = req_reviewer.metadata.get("materialized_source_manifest")

            self.assertIsNotNone(manifest_owner)
            self.assertIsNotNone(manifest_reviewer)
            self.assertEqual(len(manifest_owner), 1)
            self.assertEqual(manifest_owner[0]["relative_path"], "docs/source.md")
            self.assertEqual(manifest_owner[0]["canonical_source_path"], str(src_doc.resolve()))
            self.assertEqual(len(manifest_owner[0]["sha256"]), 64)

            self.assertEqual(manifest_owner[0]["sha256"], manifest_reviewer[0]["sha256"])

    def test_read_copy_failure_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            status_root = tmp_path / "pantheon"
            status_root.mkdir()
            src_doc = status_root / "docs" / "source.md"
            src_doc.parent.mkdir(parents=True)
            src_doc.write_text("CANONICAL", encoding="utf-8")

            worktree_path = tmp_path / "worktree"
            worktree_path.mkdir()

            task = {
                "id": "ODP-B9-TEST-001",
                "title": "Copy Failure Test",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "priority": "P0",
                "source_docs": ["docs/source.md"],
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            req = supervisor.DeliveryRequest(
                agent_id="Antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="ODP-B9-TEST-001",
                reason="owned_ready_dispatch",
                context_files=["docs/source.md"],
            )

            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(common, "load_status", return_value={"tasks": [task]}),
                mock.patch("shutil.copy2", side_effect=OSError("Disk read failure")),
            ):
                with self.assertRaises(ValueError) as ctx:
                    supervisor.materialize_worker_context_files(config, req, worktree_path)
                self.assertIn("failed to copy source document", str(ctx.exception))

    def test_raw_absolute_path_and_directory_child_symlink_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            status_root = tmp_path / "pantheon"
            status_root.mkdir()

            # Test raw absolute path rejection
            valid, norm, err = common.validate_source_doc_path("/docs/source.md", status_root)
            self.assertFalse(valid)
            self.assertIn("raw absolute path rejected", err or "")

            # Test directory child symlink pointing outside status_root
            outside_dir = tmp_path / "outside"
            outside_dir.mkdir()
            outside_file = outside_dir / "secret.txt"
            outside_file.write_text("SECRET", encoding="utf-8")

            dir_source = status_root / "docs_dir"
            dir_source.mkdir()
            (dir_source / "manifest.json").write_text("{}", encoding="utf-8")
            (dir_source / "child_symlink").symlink_to(outside_file)

            valid_dir, norm_dir, err_dir = common.validate_source_doc_path("docs_dir", status_root)
            self.assertFalse(valid_dir)
            self.assertIn("external directory child symlink rejected", err_dir or "")

            task = {
                "id": "ODP-B10-TEST-001",
                "title": "Directory Symlink Test",
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "priority": "P0",
                "source_docs": ["docs_dir"],
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            req = supervisor.DeliveryRequest(
                agent_id="Antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="ODP-B10-TEST-001",
                reason="owned_ready_dispatch",
                context_files=["docs_dir"],
            )

            worktree_path = tmp_path / "worktree"
            worktree_path.mkdir()

            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(common, "load_status", return_value={"tasks": [task]}),
            ):
                with self.assertRaises(ValueError) as ctx:
                    supervisor.materialize_worker_context_files(config, req, worktree_path)
                self.assertIn("external directory child symlink rejected", str(ctx.exception))

    def test_canonical_hash_binds_rendered_metadata_and_archived_ambiguity(self) -> None:
        task1 = {
            "id": "ODP-B11-TEST-001",
            "title": "Hash Metadata Test",
            "status": "todo",
            "owner": "Antigravity",
            "reviewer": "Codex5",
            "last_update": "2026-08-02T11:00:00Z",
            "artifacts": ["apps/web/src/app.tsx"],
            "depends_on": ["ODP-DEP-001"],
            "phase": "Phase 1",
            "summary_zh": "Summary Zh 1",
        }
        h1 = common.task_brief_canonical_hash(task1)

        task2 = dict(task1)
        task2["artifacts"] = ["apps/web/src/app.tsx", "apps/api/src/server.ts"]
        h2 = common.task_brief_canonical_hash(task2)

        self.assertNotEqual(h1, h2)

        brief_text = (
            "# Task Brief: ODP-B11-TEST-001\n"
            "- Status: todo\n"
            "- Owner: Antigravity\n"
            "- Reviewer: Codex5\n"
            "- Last update: 2026-08-02T11:00:00Z\n"
            f"- SHA256: {h1}\n"
            "\n"
            "## Source Documents\n"
            "- none\n"
        )
        self.assertTrue(common.is_task_brief_stale(brief_text, task2))

        # Test archived ambiguity check
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "pantheon"
            status_root.mkdir()
            archive_dir = status_root / "ai-task-archive" / "tasks"
            archive_dir.mkdir(parents=True)

            archived_task = {
                "id": "ODP-AMBIG-001",
                "title": "Ambiguity Test Task",
                "status": "done",
                "owner": "Claude",
                "reviewer": "Codex5",
                "last_update": "2026-08-01T00:00:00Z",
            }
            snapshot = {
                "version": 1,
                "task_id": "ODP-AMBIG-001",
                "archived_at": "2026-08-01T00:00:00Z",
                "terminal_status": "done",
                "terminal_outcome": "completed",
                "task": archived_task,
            }
            (archive_dir / "ODP-AMBIG-001.json").write_text(json.dumps(snapshot), encoding="utf-8")

            active_task = {
                "id": "ODP-AMBIG-001",
                "title": "Ambiguity Test Task",
                "status": "in_progress",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "last_update": "2026-08-02T11:00:00Z",
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            with (
                mock.patch.object(common, "load_status", return_value={"tasks": [active_task]}),
            ):
                with self.assertRaises(ValueError) as ctx:
                    common.generate_task_brief_content(config, "ODP-AMBIG-001")
                self.assertIn("Archived-task ambiguity for task ODP-AMBIG-001", str(ctx.exception))

    def test_b12_directory_source_doc_materialization_tree_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            status_root = tmp_path / "pantheon"
            status_root.mkdir()
            status_file = status_root / "ai-status.json"

            inv_dir = status_root / "docs" / "evidence" / "inventory"
            inv_dir.mkdir(parents=True)
            (inv_dir / "manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
            (inv_dir / "fileA.txt").write_text("content A\n", encoding="utf-8")
            (inv_dir / "fileB.txt").write_text("content B\n", encoding="utf-8")

            task = {
                "id": "ODP-B12-DIR-001",
                "title": "Directory Materialization B12 Task",
                "priority": "P0",
                "mutates_canonical": True,
                "status": "in_progress",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "source_docs": ["docs/evidence/inventory"],
            }
            status_data = {"tasks": [task]}
            status_file.write_text(json.dumps(status_data), encoding="utf-8")
            config = {"paths": {"status_file": str(status_file)}}

            # Owner worktree: clean materialization
            worktree_owner = tmp_path / "worktree_owner"
            worktree_owner.mkdir()
            req_owner = supervisor.DeliveryRequest(
                agent_id="Antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="ODP-B12-DIR-001",
                context_files=["docs/evidence/inventory"],
            )
            mat_owner = supervisor.materialize_worker_context_files(config, req_owner, worktree_owner)
            self.assertEqual(mat_owner, ["docs/evidence/inventory"])
            manifest_owner = req_owner.metadata.get("materialized_source_manifest")
            self.assertIsNotNone(manifest_owner)
            sha_owner = manifest_owner[0]["sha256"]
            self.assertTrue(bool(sha_owner))

            # Reviewer worktree: pre-existing reviewer-only extra file
            worktree_reviewer = tmp_path / "worktree_reviewer"
            worktree_reviewer.mkdir()
            dest_inv_rev = worktree_reviewer / "docs" / "evidence" / "inventory"
            dest_inv_rev.mkdir(parents=True)
            extra_file = dest_inv_rev / "reviewer_extra.txt"
            extra_file.write_text("reviewer extra data\n", encoding="utf-8")

            req_reviewer = supervisor.DeliveryRequest(
                agent_id="Codex5",
                provider="codex",
                delivery_mode="codex",
                message="review",
                task_id="ODP-B12-DIR-001",
                context_files=["docs/evidence/inventory"],
            )
            with self.assertRaises(ValueError) as ctx:
                supervisor.materialize_worker_context_files(config, req_reviewer, worktree_reviewer)
            self.assertIn("final source and destination tree mismatch", str(ctx.exception))
            self.assertTrue(extra_file.exists())

    def test_b13_archived_ambiguity_canonical_metadata_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "pantheon"
            status_root.mkdir()
            archive_dir = status_root / "ai-task-archive" / "tasks"
            archive_dir.mkdir(parents=True)

            archived_task = {
                "id": "ODP-B13-AMBIG-001",
                "title": "Archived Title",
                "phase": "Archived Phase",
                "summary_zh": "Archived Summary",
                "status": "in_progress",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "last_update": "2026-08-02T10:00:00Z",
            }
            snapshot = {
                "version": 1,
                "task_id": "ODP-B13-AMBIG-001",
                "task": archived_task,
            }
            (archive_dir / "ODP-B13-AMBIG-001.json").write_text(json.dumps(snapshot), encoding="utf-8")

            active_task = {
                "id": "ODP-B13-AMBIG-001",
                "title": "Active Title Conflict",
                "phase": "Archived Phase",
                "summary_zh": "Archived Summary",
                "status": "in_progress",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "last_update": "2026-08-02T10:00:00Z",
            }
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            with mock.patch.object(common, "load_status", return_value={"tasks": [active_task]}):
                with self.assertRaises(ValueError) as ctx:
                    common.validate_task_archive_ambiguity(config, "ODP-B13-AMBIG-001")
                self.assertIn("Archived-task ambiguity for task ODP-B13-AMBIG-001: active title=", str(ctx.exception))

                with self.assertRaises(ValueError) as ctx:
                    common.generate_task_brief_content(config, "ODP-B13-AMBIG-001")
                self.assertIn("Archived-task ambiguity", str(ctx.exception))

    def test_b13_write_task_brief_bypasses_cache_on_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "pantheon"
            status_root.mkdir()
            archive_dir = status_root / "ai-task-archive" / "tasks"
            archive_dir.mkdir(parents=True)

            archived_task = {
                "id": "ODP-B13-CACHE-001",
                "title": "Archived Title",
                "summary_zh": "Archived Summary",
                "status": "in_progress",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "last_update": "2026-08-02T10:00:00Z",
            }
            (archive_dir / "ODP-B13-CACHE-001.json").write_text(
                json.dumps({"version": 1, "task_id": "ODP-B13-CACHE-001", "task": archived_task}),
                encoding="utf-8",
            )

            active_task = dict(archived_task)
            active_task["summary_zh"] = "Conflicting Active Summary"

            brief_path = status_root / ".orchestrator" / "task-briefs" / "odp_b13_cache_001.md"
            brief_path.parent.mkdir(parents=True)
            brief_path.write_text("Fresh cached brief content\n", encoding="utf-8")

            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}
            with mock.patch.object(common, "load_status", return_value={"tasks": [active_task]}):
                with self.assertRaises(ValueError) as ctx:
                    common.write_task_brief(config, "ODP-B13-CACHE-001")
                self.assertIn("Archived-task ambiguity", str(ctx.exception))

    def test_b13_materialize_worker_context_files_fails_closed_on_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            status_root = tmp_path / "pantheon"
            status_root.mkdir()
            archive_dir = status_root / "ai-task-archive" / "tasks"
            archive_dir.mkdir(parents=True)

            archived_task = {
                "id": "ODP-B13-DISPATCH-001",
                "title": "Archived Title",
                "priority": "P0",
                "mutates_canonical": True,
                "status": "in_progress",
                "owner": "Antigravity",
                "reviewer": "Codex5",
                "last_update": "2026-08-02T10:00:00Z",
            }
            (archive_dir / "ODP-B13-DISPATCH-001.json").write_text(
                json.dumps({"version": 1, "task_id": "ODP-B13-DISPATCH-001", "task": archived_task}),
                encoding="utf-8",
            )

            active_task = dict(archived_task)
            active_task["phase"] = "Conflicting Phase"
            (status_root / "ai-status.json").write_text(json.dumps({"tasks": [active_task]}), encoding="utf-8")

            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}
            worktree = tmp_path / "worktree"
            worktree.mkdir()
            req = supervisor.DeliveryRequest(
                agent_id="Antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="ODP-B13-DISPATCH-001",
                context_files=[".orchestrator/task-briefs/odp_b13_dispatch_001.md"],
            )

            with self.assertRaises(ValueError) as ctx:
                supervisor.materialize_worker_context_files(config, req, worktree)
            self.assertIn("Archived-task ambiguity", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
