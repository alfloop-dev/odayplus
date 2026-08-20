#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("register_emgi_v041_catalog.py")
spec = importlib.util.spec_from_file_location("register_emgi_v041_catalog", SCRIPT)
assert spec and spec.loader
catalog = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = catalog
spec.loader.exec_module(catalog)


def synthetic_catalog() -> dict[str, object]:
    initial = [
        ("DPF-GOV-001", []),
        ("DPF-KRN-MEAS-001", ["DPF-GOV-001"]),
        ("DPF-KRN-DATASET-001", ["DPF-GOV-001"]),
        ("DPF-KRN-TIME-001", ["DPF-GOV-001"]),
    ]
    definitions: list[dict[str, object]] = []
    for task_id, dependencies in initial:
        definitions.append(
            {
                "id": task_id,
                "title": task_id,
                "repository": "alfloop-dev/oday-data-platform",
                "base_branch": "dev",
                "group": "Kernel",
                "priority": "P0",
                "owner": "AUTO_ASSIGN",
                "reviewer": "DIFFERENT_AGENT_REQUIRED",
                "depends_on": dependencies,
                "owned_paths": [f"src/{task_id.lower()}.py"],
                "forbidden_paths": [],
                "requires_contracts": [],
                "provides_contracts": [f"contract.{task_id.lower()}"],
                "acceptance": ["accepted"],
                "verification": ["pytest"],
            }
        )
    for index in range(46):
        task_id = f"EMGI-DUMMY-{index:03d}"
        definitions.append(
            {
                "id": task_id,
                "title": task_id,
                "repository": "alfloop-dev/odayplus" if index % 2 else "alfloop-dev/oday-data-platform",
                "base_branch": "dev",
                "group": "Deferred",
                "priority": "P0",
                "owner": "AUTO_ASSIGN",
                "reviewer": "DIFFERENT_AGENT_REQUIRED",
                "depends_on": ["DPF-KRN-MEAS-001"],
                "owned_paths": [f"modules/{task_id.lower()}/"],
                "forbidden_paths": [],
                "requires_contracts": [],
                "provides_contracts": [f"contract.{task_id.lower()}"],
                "acceptance": ["accepted"],
                "verification": ["pytest"],
            }
        )
    return {
        "manifest": {},
        "manifest_path": "docs/design/emgi/v0.4.1/tasks/manifest.json",
        "authority_sha": "7" * 40,
        "definitions": definitions,
        "definition_by_task": {
            str(task["id"]): "definitions/synthetic.json" for task in definitions
        },
    }


class EmgiCatalogRegistrationTests(unittest.TestCase):
    def existing_status(self) -> dict[str, object]:
        return {
            "tasks": [
                {
                    "id": "DPF-GOV-001",
                    "title": "GOV",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "pr_number": 6,
                    "branch": "task/DPF-GOV-001",
                },
                {"id": "DPF-KRN-MEAS-001", "status": "blocked", "owner": "Codex2"},
                {"id": "DPF-KRN-DATASET-001", "status": "blocked", "owner": "Claude2"},
                {"id": "DPF-KRN-TIME-001", "status": "blocked", "owner": "Antigravity"},
            ]
        }

    def test_registers_all_fifty_without_dispatching_blocked_tasks(self) -> None:
        updated, receipt = catalog.register_catalog(self.existing_status(), synthetic_catalog())
        tasks = updated["tasks"]
        self.assertEqual(50, len([task for task in tasks if task.get("catalog_managed")]))
        self.assertEqual(46, receipt["newly_registered"])
        self.assertEqual(4, receipt["existing_preserved"])
        self.assertEqual({"review": 1, "blocked": 49}, receipt["status_counts"])
        self.assertFalse(any(task.get("status") == "in_progress" for task in tasks))

        gov = next(task for task in tasks if task["id"] == "DPF-GOV-001")
        self.assertEqual("review", gov["status"])
        self.assertEqual(6, gov["pr_number"])
        self.assertEqual("Codex", gov["owner"])
        self.assertEqual("alfloop-dev/oday-data-platform", gov["repository"])
        self.assertTrue(gov["source_docs"][0].startswith("github://alfloop-dev/oday-data-platform@"))

    def test_registration_is_idempotent_and_preserves_lifecycle(self) -> None:
        first, _ = catalog.register_catalog(self.existing_status(), synthetic_catalog())
        second, receipt = catalog.register_catalog(first, synthetic_catalog())
        self.assertEqual(0, receipt["newly_registered"])
        self.assertEqual(50, receipt["existing_preserved"])
        self.assertEqual(50, len([task for task in second["tasks"] if task.get("catalog_managed")]))
        gov = next(task for task in second["tasks"] if task["id"] == "DPF-GOV-001")
        self.assertEqual("review", gov["status"])
        self.assertEqual("task/DPF-GOV-001", gov["branch"])

    def test_wave_one_unlock_fails_before_governance_done(self) -> None:
        with self.assertRaises(catalog.CatalogRegistrationError) as ctx:
            catalog.register_catalog(
                self.existing_status(), synthetic_catalog(), unlock_wave_1=True
            )
        self.assertIn("cannot be unlocked", str(ctx.exception))

    def test_wave_one_unlocks_exactly_three_after_governance_done(self) -> None:
        status = self.existing_status()
        status["tasks"][0]["status"] = "done"
        updated, receipt = catalog.register_catalog(
            status, synthetic_catalog(), unlock_wave_1=True
        )
        states = {task["id"]: task["status"] for task in updated["tasks"] if task.get("catalog_managed")}
        self.assertEqual("done", states["DPF-GOV-001"])
        for task_id in catalog.WAVE_1:
            self.assertEqual("todo", states[task_id])
        remaining = [
            status_value
            for task_id, status_value in states.items()
            if task_id not in {"DPF-GOV-001", *catalog.WAVE_1}
        ]
        self.assertEqual({"blocked"}, set(remaining))
        self.assertTrue(receipt["wave_1_unlocked"])

    def test_repository_artifacts_are_explicitly_prefixed(self) -> None:
        updated, _ = catalog.register_catalog(self.existing_status(), synthetic_catalog())
        platform = next(task for task in updated["tasks"] if task["id"] == "EMGI-DUMMY-000")
        consumer = next(task for task in updated["tasks"] if task["id"] == "EMGI-DUMMY-001")
        self.assertTrue(all(path.startswith("oday-data-platform/") for path in platform["artifacts"]))
        self.assertTrue(all(path.startswith("odayplus/") for path in consumer["artifacts"]))

    def test_archived_governance_restores_lifecycle_and_unlocks_wave_one(self) -> None:
        status = {"tasks": []}
        with tempfile.TemporaryDirectory() as directory:
            archive_dir = Path(directory)
            (archive_dir / "DPF-GOV-001.json").write_text(
                json.dumps(
                    {
                        "terminal_status": "done",
                        "terminal_outcome": "completed",
                        "task": {
                            "id": "DPF-GOV-001",
                            "status": "done",
                            "owner": "Claude",
                            "reviewer": "Antigravity",
                            "pr_number": 6,
                        },
                    }
                ),
                encoding="utf-8",
            )
            restored = catalog.hydrate_archived_catalog_tasks(
                status, synthetic_catalog(), archive_dir
            )

        self.assertEqual(1, restored)
        updated, receipt = catalog.register_catalog(
            status, synthetic_catalog(), unlock_wave_1=True
        )
        states = {task["id"]: task["status"] for task in updated["tasks"]}
        self.assertEqual("done", states["DPF-GOV-001"])
        for task_id in catalog.WAVE_1:
            self.assertEqual("todo", states[task_id])
        self.assertTrue(receipt["wave_1_unlocked"])


if __name__ == "__main__":
    unittest.main()
