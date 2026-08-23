#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import common
from source_document_router import SourceDocumentRoutingError, resolve_source_document


class SourceDocumentRouterTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            self.fail(proc.stderr or proc.stdout)
        return proc.stdout.strip()

    def _workspace(self, tmpdir: str) -> tuple[Path, Path, str, dict[str, object]]:
        base = Path(tmpdir)
        status_root = base / "odayplus"
        platform_root = base / "oday-data-platform"
        status_root.mkdir()
        platform_root.mkdir()

        for root, slug in (
            (status_root, "alfloop-dev/odayplus"),
            (platform_root, "alfloop-dev/oday-data-platform"),
        ):
            self._git(root, "init", "-b", "dev")
            self._git(root, "config", "user.name", "Test")
            self._git(root, "config", "user.email", "test@example.invalid")
            self._git(root, "remote", "add", "origin", f"https://github.com/{slug}.git")

        manifest = platform_root / "docs/design/emgi/v0.4.1/tasks/manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{"status":"approved_for_staged_dispatch"}\n', encoding="utf-8")
        self._git(platform_root, "add", ".")
        self._git(platform_root, "commit", "-m", "authority")
        authority_sha = self._git(platform_root, "rev-parse", "HEAD")
        self._git(platform_root, "update-ref", "refs/remotes/origin/dev", authority_sha)

        guide = status_root / "AI_COLLABORATION_GUIDE.md"
        guide.write_text("# guide\n", encoding="utf-8")
        self._git(status_root, "add", ".")
        self._git(status_root, "commit", "-m", "status")

        config: dict[str, object] = {
            "paths": {"status_file": str(status_root / "ai-status.json")},
            "coordination": {
                "repositories": {
                    "odayplus": {
                        "display_name": "odayplus",
                        "repo": "alfloop-dev/odayplus",
                        "local_path": str(status_root),
                        "default_branch": "dev",
                    },
                    "oday_data_platform": {
                        "display_name": "oday-data-platform",
                        "repo": "alfloop-dev/oday-data-platform",
                        "local_path": str(platform_root),
                        "default_branch": "dev",
                    },
                }
            },
        }
        return status_root, platform_root, authority_sha, config

    def test_relative_task_document_routes_by_task_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root, _, authority_sha, config = self._workspace(tmpdir)
            task = {
                "id": "DPF-GOV-001",
                "repository": "alfloop-dev/oday-data-platform",
                "base_branch": "dev",
                "source_commit_sha": authority_sha,
            }
            resolved = resolve_source_document(
                config,
                status_root,
                "docs/design/emgi/v0.4.1/tasks/manifest.json",
                task=task,
            )
            self.assertEqual(authority_sha, resolved.commit_sha)
            self.assertTrue(resolved.context_path.startswith(".orchestrator/source-doc-cache/"))
            self.assertEqual(
                '{"status":"approved_for_staged_dispatch"}\n',
                resolved.source_path.read_text(encoding="utf-8"),
            )
            self.assertTrue(
                any((status_root / ".orchestrator/source-doc-cache/_receipts").glob("*.json"))
            )

    def test_pinned_github_reference_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root, _, authority_sha, config = self._workspace(tmpdir)
            reference = (
                "github://alfloop-dev/oday-data-platform@"
                f"{authority_sha}/docs/design/emgi/v0.4.1/tasks/manifest.json"
            )
            first = resolve_source_document(config, status_root, reference, task={})
            second = resolve_source_document(config, status_root, reference, task={})
            self.assertEqual(first.context_path, second.context_path)
            self.assertEqual(first.sha256, second.sha256)
            self.assertEqual(reference, first.canonical_reference)

    def test_execution_context_files_returns_local_cache_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root, _, authority_sha, config = self._workspace(tmpdir)
            task = {
                "id": "DPF-GOV-001",
                "title": "Governance",
                "repository": "alfloop-dev/oday-data-platform",
                "base_branch": "dev",
                "source_commit_sha": authority_sha,
                "status": "review",
                "owner": "Codex",
                "reviewer": "Claude",
                "priority": "P0",
                "source_docs": ["docs/design/emgi/v0.4.1/tasks/manifest.json"],
            }
            (status_root / "ai-status.json").write_text(
                json.dumps({"tasks": [task]}), encoding="utf-8"
            )
            with mock.patch.object(common, "load_status", return_value={"tasks": [task]}):
                files = common.execution_context_files(config, "DPF-GOV-001")
            cached = [item for item in files if item.startswith(".orchestrator/source-doc-cache/")]
            self.assertEqual(1, len(cached))
            self.assertTrue((status_root / cached[0]).is_file())

    def test_unregistered_repository_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root, _, authority_sha, config = self._workspace(tmpdir)
            reference = (
                "github://alfloop-dev/not-registered@"
                f"{authority_sha}/docs/design/missing.md"
            )
            with self.assertRaises(SourceDocumentRoutingError) as ctx:
                resolve_source_document(config, status_root, reference, task={})
            self.assertIn("not registered", str(ctx.exception))

    def test_traversal_and_unpinned_explicit_reference_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root, _, _, config = self._workspace(tmpdir)
            with self.assertRaises(SourceDocumentRoutingError):
                resolve_source_document(
                    config,
                    status_root,
                    "github://alfloop-dev/oday-data-platform@dev/../secret",
                    task={},
                )
            with self.assertRaises(SourceDocumentRoutingError):
                resolve_source_document(
                    config,
                    status_root,
                    "../secret",
                    task={"repository": "alfloop-dev/oday-data-platform"},
                )

    def test_mutable_http_and_pull_request_urls_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root, _, _, config = self._workspace(tmpdir)
            for reference in (
                "https://github.com/alfloop-dev/oday-data-platform/blob/dev/README.md",
                "https://github.com/alfloop-dev/oday-data-platform/pull/123/files",
            ):
                with self.subTest(reference=reference):
                    with self.assertRaises(SourceDocumentRoutingError) as ctx:
                        resolve_source_document(config, status_root, reference, task={})
                    self.assertIn("mutable HTTP/PR", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
