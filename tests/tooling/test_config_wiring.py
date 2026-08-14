#!/usr/bin/env python3
from __future__ import annotations

import unittest

from delivery_toolchain.governance import check_config_wiring as guard


class ConfigWiringAuditTests(unittest.TestCase):
    def test_key_no_code_reads_is_reported(self) -> None:
        config = {"branch_workflow": {"task_pr": {"target_branch": "dev"}}}
        sources = {"a.py": "base = default_branch(config)\n"}

        unexpected = guard.audit(config, sources)

        self.assertIn("branch_workflow.task_pr.target_branch", unexpected)

    def test_wired_key_is_accepted(self) -> None:
        config = {"branch_workflow": {"task_pr": {"target_branch": "dev"}}}
        sources = {
            "github_bus.py": (
                'workflow = config.get("branch_workflow") or {}\n'
                'task_pr = workflow.get("task_pr") or {}\n'
                'target = task_pr.get("target_branch")\n'
            )
        }

        unexpected = guard.audit(config, sources)

        self.assertEqual(unexpected, [])

    def test_settings_sharing_a_leaf_name_are_judged_separately(self) -> None:
        # The bug this pins: wiring task_pr.target_branch used to mark
        # promote.target_branch as read too, so a dead key sailed through.
        config = {
            "branch_workflow": {
                "task_pr": {"target_branch": "dev"},
                "promote": {"target_branch": "main"},
            }
        }
        sources = {
            "github_bus.py": (
                'workflow = config.get("branch_workflow") or {}\n'
                'task_pr = workflow.get("task_pr") or {}\n'
                'target = task_pr.get("target_branch")\n'
            )
        }

        unexpected = guard.audit(config, sources)

        self.assertIn("branch_workflow.promote.target_branch", unexpected)
        self.assertNotIn("branch_workflow.task_pr.target_branch", unexpected)

    def test_parent_bound_to_a_variable_still_counts_as_wired(self) -> None:
        # The opposite failure: `schema["status_field"]` never quotes `schema`,
        # so demanding a quoted parent reported live settings as dead.
        config = {"schema": {"status_field": "status"}}
        sources = {"common.py": 'value = schema["status_field"]\n'}

        unexpected = guard.audit(config, sources)

        self.assertNotIn("schema.status_field", unexpected)

    def test_data_container_children_are_not_settings(self) -> None:
        # Agent ids are data. Requiring code to mention every deployed agent by
        # name would make the guard fire on every roster change.
        config = {
            "agents": {"antigravity4": {"model": "x"}},
            "providers": {"codex": {}},
            "account_pools": {"codex_primary": {"state": "healthy", "max_concurrent": 2}},
        }

        paths = {".".join(p) for p in guard.iter_setting_paths(config)}

        self.assertIn("agents", paths)
        self.assertNotIn("agents.antigravity4", paths)
        self.assertNotIn("providers.codex", paths)
        self.assertIn("account_pools", paths)
        self.assertNotIn("account_pools.codex_primary", paths)


class RepositoryConfigIsWiredTests(unittest.TestCase):
    def test_committed_config_passes_the_guard(self) -> None:
        schema = guard.json.loads(guard.CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"))
        config = guard.schema_config_shape(schema)
        unexpected = guard.audit(config, guard.load_sources())

        self.assertEqual(unexpected, [], "new config keys must be wired before they are declared")


if __name__ == "__main__":
    unittest.main()
