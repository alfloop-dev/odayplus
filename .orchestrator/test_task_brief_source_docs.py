#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import common
import supervisor

SOURCE_DOCS = [
    "docs_archive/00_source_zips/operator_console/LATEST.json",
    "docs_archive/00_source_zips/operator_console/r4-20260707-package-6/extracted/Oday Plus Operator Console.dc.html",
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
            "acceptance": ["The implementation matches package 6."],
            "verification": ["unzip -t package-6.zip"],
        }

    def test_canonical_task_brief_lists_source_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            brief_path = Path(tmpdir) / "brief.md"
            with (
                mock.patch.object(common, "load_status", return_value={"tasks": [self._task()]}),
                mock.patch.object(common, "task_brief_path", return_value=brief_path),
                mock.patch.object(common, "load_json", return_value={}),
                mock.patch.object(common, "_recent_task_activity", return_value=[]),
            ):
                result = common.write_task_brief({}, "ODP-OC-R4-TEST")

            self.assertEqual(result, brief_path)
            text = brief_path.read_text(encoding="utf-8")
            self.assertIn("## Source Documents", text)
            for source_doc in SOURCE_DOCS:
                self.assertIn(f"- {source_doc}", text)
            self.assertIn("## Acceptance", text)
            self.assertIn("- The implementation matches package 6.", text)
            self.assertIn("## Verification", text)
            self.assertIn("- `unzip -t package-6.zip`", text)

    def test_fallback_worker_brief_lists_source_docs(self) -> None:
        with mock.patch.object(supervisor, "load_status", return_value={"tasks": [self._task()]}):
            text = supervisor._generated_worker_task_brief({}, "ODP-OC-R4-TEST")

        self.assertIn("## Source Documents", text)
        for source_doc in SOURCE_DOCS:
            self.assertIn(f"- {source_doc}", text)
        self.assertIn("- The implementation matches package 6.", text)
        self.assertIn("- `unzip -t package-6.zip`", text)


if __name__ == "__main__":
    unittest.main()
