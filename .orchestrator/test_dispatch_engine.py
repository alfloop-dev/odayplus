from __future__ import annotations

import contextlib
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import dispatch_engine
import supervisor


class PausedOwnerFailoverPreferenceTests(unittest.TestCase):
    """The paused-owner failover picks the first candidate that passes, so its
    ordering *is* its owner preference.

    That ordering came from `agent_dispatch_preference_rank`, which reads the
    provider *name*: anything not spelled antigravity/claude/codex sorts last,
    however it is actually delivered. This path therefore had a second, weaker,
    unconfigurable opinion about which lane should implement -- and it could
    hand a task to a lane with no free slot, because reserving is checked per
    dispatch slot while candidates are logical agents. Both now go through the
    same policy the reassignment selector uses.
    """

    PREFERENCE = {
        "enabled": True,
        "preferred_providers": ["antigravity"],
        "task_classes": ["implementation", "remediation", "documentation"],
    }

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        (self.root / "event_queue.jsonl").write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _config(self, preference: dict[str, Any] | None = "default") -> dict[str, Any]:
        config: dict[str, Any] = {
            "paths": {
                "event_queue": str(self.root / "event_queue.jsonl"),
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "activity.jsonl"),
            },
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "finalize_statuses": ["review_approved"],
                "owned_statuses": ["in_progress", "todo"],
                "active_worker_statuses": ["running"],
            },
            "providers": {
                "claude": {"delivery_mode": "claude_cli"},
                "codex": {
                    "delivery_mode": "codex",
                    "codex": {"codex_home": str(self.root / "codex-home")},
                },
                "codex_lupin": {
                    "delivery_mode": "codex",
                    "codex": {"codex_home": str(self.root / "codex-home")},
                },
                # An agy lane whose provider key is not spelled "antigravity".
                # Only the configured delivery mode says what runs the work.
                "agy_pool_b": {"delivery_mode": "antigravity"},
            },
            "agents": {
                "claude": {
                    "display_name": "Claude",
                    "provider": "claude",
                    "adapter": "claude_cli",
                    "account_pool": "claude_main",
                },
                "codex": {
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                    "account_pool": "codex_bjoe",
                },
                "codex2": {
                    "display_name": "Codex2",
                    "provider": "codex_lupin",
                    "adapter": "codex",
                    "account_pool": "codex_lupin",
                },
                "agy_b": {
                    "display_name": "AgyB",
                    "provider": "agy_pool_b",
                    "adapter": "antigravity",
                    "account_pool": "agy_b_pool",
                },
                "agy_b_slot_1": {
                    "display_name": "agy_b_slot_1",
                    "provider": "agy_pool_b",
                    "adapter": "antigravity",
                    "account_pool": "agy_b_pool",
                    "dispatch_slot_for_pool": "agy_b_pool",
                },
            },
            "account_pools": {
                "claude_main": {"enabled": True, "max_concurrent": 1},
                "codex_bjoe": {"enabled": True, "max_concurrent": 1},
                "codex_lupin": {"enabled": True, "max_concurrent": 1},
                "agy_b_pool": {"enabled": True, "max_concurrent": 1},
            },
        }
        if preference == "default":
            preference = dict(self.PREFERENCE)
        if preference is not None:
            config["ready_dispatcher"]["owner_provider_preference"] = preference
        return config

    @staticmethod
    def _paused_codex_state() -> dict[str, Any]:
        return {
            "workers": {},
            "queue": {"events": {}},
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "blocked_until": "2099-01-01T00:00:00Z",
                        "reason": "Codex usage limit reached",
                    }
                }
            },
        }

    @staticmethod
    def _owned_task(**overrides: Any) -> dict[str, Any]:
        """Owned implementation work whose owner is still a live choice.

        This used to be `review_approved`, which is the one owner state the
        preference must never touch: that status pins an exact reviewed head
        and closeout from it is read-only, so "which lane should implement
        this" is already answered. The preference is about work that is still
        to be written, so the fixture states work that is still to be written.
        """
        task = {
            "id": "T-1",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "task_class": "implementation",
        }
        task.update(overrides)
        return {"tasks": [task]}

    def _run(self, config: dict[str, Any], state: dict[str, Any], status: dict[str, Any]) -> dict[str, Any] | None:
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "console_log"),
        ):
            dispatch_engine.reassign_unavailable_reviewers(
                config, copy.deepcopy(state), copy.deepcopy(status)
            )
        return persist.call_args.kwargs if persist.call_args else None

    def test_candidate_order_without_the_preference_puts_codex_first(self) -> None:
        """Baseline: the dispatch loop ranks the agy lane last by provider name."""
        self.assertEqual(
            supervisor.dispatch_loop_agent_ids(self._config()),
            ["claude", "codex", "codex2", "agy_b"],
        )
        reassignment = self._run(
            self._config(preference=None), self._paused_codex_state(), self._owned_task()
        )
        self.assertEqual((reassignment or {}).get("new_owner"), "Codex2")

    def test_paused_owner_fails_over_to_the_preferred_provider(self) -> None:
        reassignment = self._run(self._config(), self._paused_codex_state(), self._owned_task())
        self.assertIsNotNone(reassignment)
        self.assertEqual(reassignment["new_owner"], "AgyB")
        self.assertEqual(reassignment["new_reviewer"], "Claude")
        self.assertEqual(reassignment["handoff_from"], "Codex")

    def test_saturated_preferred_lane_falls_back_to_the_existing_order(self) -> None:
        """Its one slot is running, so preferring it would only queue the task."""
        state = self._paused_codex_state()
        state["workers"]["run-1"] = {
            "status": "running",
            "agent_id": "agy_b_slot_1",
            "logical_agent_id": "agy_b",
            "quota_group": "agy_b_pool",
            "task_id": "T-OTHER",
            "request_snapshot": {"reason": "owned_ready_dispatch"},
        }
        reassignment = self._run(self._config(), state, self._owned_task())
        self.assertEqual((reassignment or {}).get("new_owner"), "Codex2")

    def test_pending_delivery_on_the_shared_pool_also_saturates_the_lane(self) -> None:
        """A queued-but-undelivered event holds the pool's only slot too.

        Nothing is running, so the preference's old per-agent load reads zero
        and the active-only quota guard lets the lane through -- yet the one
        slot this account owns is already spoken for.
        """
        queue_path = self.root / "event_queue.jsonl"
        queue_path.write_text(
            json.dumps(
                {
                    "event_id": "evt-1",
                    "target_agent": "AgyB",
                    "target_display_name": "AgyB",
                    "provider": "agy_pool_b",
                    "reason": "owned_ready_dispatch",
                    "message": "queued but not delivered",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        reassignment = self._run(self._config(), self._paused_codex_state(), self._owned_task())
        self.assertEqual((reassignment or {}).get("new_owner"), "Codex2")

    def test_frozen_closeout_states_keep_the_existing_candidate_order(self) -> None:
        """The preferred lane is free and the class is eligible; it still loses.

        A `review_approved` head, a recorded `approved_head`, or a task already
        on a merge route is owned by whoever wrote the reviewed commit. Failing
        an unavailable owner over is a control-plane repair either way, but it
        must land wherever the pre-preference order put it, not on the lane the
        implementation policy would rather use.
        """
        for label, overrides in (
            ("review_approved", {"status": "review_approved"}),
            ("approved_head", {"approved_head": "a" * 40}),
            ("merge_route", {"merge_route": {"route": "queued", "head": "a" * 40}}),
        ):
            with self.subTest(label=label):
                status = self._owned_task(id=f"T-{label}", **overrides)
                with_preference = self._run(self._config(), self._paused_codex_state(), status)
                without_preference = self._run(
                    self._config(preference=None), self._paused_codex_state(), status
                )
                self.assertEqual(
                    (with_preference or {}).get("new_owner"),
                    (without_preference or {}).get("new_owner"),
                )
                self.assertEqual((with_preference or {}).get("new_owner"), "Codex2")

    def test_deployment_task_classes_keep_the_existing_order(self) -> None:
        reassignment = self._run(
            self._config(),
            self._paused_codex_state(),
            self._owned_task(id="T-3", task_class="runtime_release"),
        )
        self.assertEqual((reassignment or {}).get("new_owner"), "Codex2")

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=self.root, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def _submitted_review(self, *authors: str, submitted_by: str = "Claude", **overrides: Any) -> dict[str, Any]:
        """A review over a real synthetic range, with the provenance `submit_review` records.

        The config's status root is this checkout, so the registry's own
        `pantheon` repository is the repository the range is written to: each
        of `authors` commits on the task branch, then `submitted_by` merges an
        advanced `dev` back in as the finalizer's base advance.
        """
        if not (self.root / ".git").exists():
            self._git("init", "-q", "-b", "dev")
            self._git("config", "user.email", "worker@pantheon.local")
            self._git("config", "user.name", "Worker")
            self._git("commit", "-q", "--allow-empty", "-m", "bootstrap")
        self._git("checkout", "-q", "-B", "task/T-2", "dev")
        for index, author in enumerate(authors):
            reviewer = "Claude" if author != "Claude" else "Codex2"
            self._git(
                "commit", "-q", "--allow-empty", "-m",
                f"T-2: change {index}\n\nLLM-Agent: {author}\nTask-ID: T-2\nReviewer: {reviewer}\n",
            )
        self._git("checkout", "-q", "dev")
        self._git("commit", "-q", "--allow-empty", "-m", "OTHER-1: unrelated dev work")
        self._git("update-ref", "refs/remotes/origin/dev", "dev")
        self._git("checkout", "-q", "task/T-2")
        self._git("merge", "--no-ff", "-q", "-m", f"Merge dev into task/T-2\n\nLLM-Agent: {submitted_by}\n", "dev")
        head = self._git("rev-parse", "HEAD")
        task = {
            "id": "T-2",
            "status": "review",
            "owner": "Claude",
            "reviewer": "Codex",
            "task_class": "implementation",
            "review_submission": {
                "pr_number": 12,
                "branch": "task/T-2",
                "base_branch": "dev",
                "remote_sha": head,
                "source_head": head,
                "source_base": self._git("rev-parse", "dev"),
                "source_authors": list(authors),
                "submitted_by": submitted_by,
            },
        }
        task.update(overrides)
        return {"tasks": [task]}

    def test_reviewer_failover_order_is_unchanged(self) -> None:
        """The reviewer branch is not an implementation-owner decision."""
        reassignment = self._run(self._config(), self._paused_codex_state(), self._submitted_review("Claude"))
        self.assertIsNotNone(reassignment)
        self.assertEqual(reassignment["new_reviewer"], "Codex2")
        self.assertEqual(reassignment["new_owner"], "Claude")

    def test_reviewer_failover_excludes_every_source_pool(self) -> None:
        """Codex2 is next in order, but it wrote part of the range."""
        reassignment = self._run(
            self._config(), self._paused_codex_state(), self._submitted_review("Claude", "Codex2")
        )
        self.assertIsNotNone(reassignment)
        self.assertEqual(reassignment["new_reviewer"], "AgyB")
        self.assertEqual(reassignment["new_owner"], "Claude")

    def test_reviewer_failover_refuses_missing_or_unknown_source(self) -> None:
        """Without validated source evidence no replacement is independent."""
        missing = self._submitted_review("Claude")
        missing["tasks"][0].pop("review_submission")
        unknown = self._submitted_review("Codex-Root")
        stale = self._submitted_review("Claude")
        stale["tasks"][0]["review_submission"]["source_head"] = "c" * 40
        # Same real head, base and remote; Codex2 wrote part of the range, but
        # the record is rewritten to leave it out. Read from the record, Codex2
        # would be the next independent reviewer.
        rewritten = self._submitted_review("Claude", "Codex2")
        rewritten["tasks"][0]["review_submission"]["source_authors"] = ["Claude"]
        for label, status in (
            ("missing", missing),
            ("unknown", unknown),
            ("stale", stale),
            ("rewritten", rewritten),
        ):
            with self.subTest(label=label):
                self.assertIsNone(self._run(self._config(), self._paused_codex_state(), status))


class ReviewDispatchStateReloadTests(unittest.TestCase):
    """One dispatch tick reads the board once, not once per review task.

    Every review-eligibility check asked `resolve_task_sha` and
    `task_pr_ci_status` for a task whose record the dispatcher already held,
    and each of them reloaded and re-normalized the whole ai-status state just
    to find that record's branch and repository again. On the live board that
    is ~1.5 s per call, so a tick with twenty review tasks spent minutes there.
    The record is passed now; the origin read it guards is not cached by it.
    """

    HEADS = {"R-1": "1" * 40, "R-2": "2" * 40, "R-3": "3" * 40, "R-4": "4" * 40}

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        (self.root / "event_queue.jsonl").write_text("", encoding="utf-8")
        supervisor.runtime_ai_status.clear_ai_status_caches()
        self.addCleanup(supervisor.runtime_ai_status.clear_ai_status_caches)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _config(self) -> dict[str, Any]:
        agents = {
            "claude": ("Claude", "claude", "claude_main"),
            "codex": ("Codex", "codex", "codex_bjoe"),
            "codex2": ("Codex2", "codex_lupin", "codex_lupin"),
        }
        return {
            "paths": {
                "event_queue": str(self.root / "event_queue.jsonl"),
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "activity.jsonl"),
            },
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "finalize_statuses": ["review_approved"],
                "owned_statuses": ["in_progress", "todo"],
                "active_worker_statuses": ["running"],
                "max_dispatches_per_tick": 10,
            },
            "providers": {
                "claude": {"delivery_mode": "claude_cli"},
                "codex": {"delivery_mode": "codex", "codex": {"codex_home": str(self.root / "codex-home")}},
                "codex_lupin": {"delivery_mode": "codex", "codex": {"codex_home": str(self.root / "codex-home")}},
            },
            "agents": {
                agent_id: {
                    "display_name": display,
                    "provider": provider,
                    "adapter": "claude_cli" if provider == "claude" else "codex",
                    "account_pool": pool,
                }
                for agent_id, (display, provider, pool) in agents.items()
            },
            "account_pools": {
                pool: {"enabled": True, "max_concurrent": 1}
                for _display, _provider, pool in agents.values()
            },
        }

    def _status(self) -> dict[str, Any]:
        def review(task_id: str, owner: str, reviewer: str, **extra: Any) -> dict[str, Any]:
            task = {
                "id": task_id,
                "status": "review",
                "owner": owner,
                "reviewer": reviewer,
                "task_class": "implementation",
                "review_submission": {
                    "pr_number": int(task_id[-1]),
                    "branch": f"task/{task_id}",
                    "remote_sha": self.HEADS[task_id],
                    "submitted_by": owner,
                },
            }
            task.update(extra)
            return task

        return {
            "tasks": [
                review("R-1", "Claude", "Codex"),
                review("R-2", "Claude", "Codex2"),
                # Reimported from an existing PR: only the record names its branch.
                review("R-3", "Codex", "Claude", branch="imported/r3-from-pr"),
                # Origin moved past the submitted head: never review-ready.
                review("R-4", "Codex", "Codex2"),
            ]
        }

    def _tick(self, *, pass_records: bool) -> dict[str, Any]:
        """Run one dispatch tick against faked origin and GitHub reads."""
        status = self._status()
        on_disk = copy.deepcopy(status)
        origin = {
            "refs/heads/task/R-1": self.HEADS["R-1"],
            "refs/heads/task/R-2": self.HEADS["R-2"],
            "refs/heads/imported/r3-from-pr": self.HEADS["R-3"],
            "refs/heads/task/R-4": "f" * 40,
        }
        ls_remote_refs: list[tuple[str, ...]] = []
        loads: list[int] = []
        queued: list[dict[str, Any]] = []
        real_run = subprocess.run
        real_resolve = supervisor.runtime_ai_status.resolve_task_sha
        real_ci = supervisor.runtime_ai_status.task_pr_ci_status

        def fake_run(cmd: Any, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            argv = [str(part) for part in cmd] if isinstance(cmd, (list, tuple)) else [str(cmd)]
            if "ls-remote" not in argv:
                return real_run(cmd, *args, **kwargs)
            refs = tuple(a for a in argv if a.startswith("refs/heads/"))
            ls_remote_refs.append(refs)
            out = "".join(f"{origin[ref]}\t{ref}\n" for ref in refs if ref in origin)
            return subprocess.CompletedProcess(argv, 0, out, "")

        def load_state() -> dict[str, Any]:
            loads.append(1)
            return copy.deepcopy(on_disk)

        def gh_pr(args: list[str], *, cwd: Any = None) -> dict[str, Any]:
            return {
                "state": "OPEN",
                "statusCheckRollup": [
                    {"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}
                ],
            }

        def resolve_from_disk(task_id: str, *args: Any, task: Any = None, **kwargs: Any) -> Any:
            return real_resolve(task_id, *args, **kwargs)

        def ci_from_disk(task_id: str, *args: Any, task: Any = None, **kwargs: Any) -> Any:
            return real_ci(task_id, *args, **kwargs)

        config = self._config()
        runtime = supervisor.runtime_ai_status
        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(mock.patch.object(subprocess, "run", side_effect=fake_run))
            enter(mock.patch.object(runtime, "load_state", side_effect=load_state))
            enter(mock.patch.object(runtime, "status_runtime_config", return_value=config))
            enter(mock.patch.object(runtime, "run_gh_json_command", side_effect=gh_pr))
            if not pass_records:
                # The pre-change shape: every check finds its record on disk again.
                enter(mock.patch.object(runtime, "resolve_task_sha", side_effect=resolve_from_disk))
                enter(mock.patch.object(runtime, "task_pr_ci_status", side_effect=ci_from_disk))
            enter(mock.patch.object(supervisor, "load_status", return_value=status))
            enter(mock.patch.object(supervisor, "load_event_queue", return_value=[]))
            enter(mock.patch.object(supervisor, "queue_delivery_event", side_effect=lambda _c, e: queued.append(e) or True))
            # Source provenance is read from Git commits and is not what this measures.
            enter(mock.patch.object(supervisor, "review_source_block_reason", return_value=None))
            # Board repairs ahead of candidate selection have their own tests;
            # here they would only rewrite the fixture before the checks run.
            for name, value in (
                ("repair_open_task_metadata", False),
                ("repair_unsubmitted_review_tasks", False),
                ("release_dead_helper_claims", (False, True)),
                ("reassign_tasks_after_review_churn", False),
                ("normalize_task_assignment_integrity", False),
                ("normalize_mainline_task_assignment", False),
            ):
                enter(mock.patch.object(supervisor, name, return_value=value))
            enter(mock.patch.object(dispatch_engine, "reassign_unavailable_reviewers", return_value=False))
            enter(mock.patch.object(dispatch_engine, "task_reality_reconcile_is_due", return_value=False))
            enter(mock.patch.object(supervisor, "write_activity_log"))
            enter(mock.patch.object(supervisor, "console_log"))
            enter(mock.patch.object(dispatch_engine, "_commit_advisory_status_transition", return_value=True))
            supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}}, provider_report={})

        return {
            "loads": len(loads),
            "ls_remote_refs": ls_remote_refs,
            "review_dispatches": sorted(
                (event["task_id"], event["target_agent"])
                for event in queued
                if event.get("reason") == "review_ready_dispatch"
            ),
            "next": {task["id"]: task.get("next") for task in status["tasks"]},
        }

    def test_one_tick_does_not_reload_state_per_review_task(self) -> None:
        before = self._tick(pass_records=False)
        after = self._tick(pass_records=True)

        self.assertGreaterEqual(before["loads"], len(self.HEADS))
        self.assertEqual(after["loads"], 0)
        self.assertEqual(
            after["review_dispatches"],
            [("R-1", "Codex"), ("R-2", "Codex2"), ("R-3", "Claude")],
        )
        self.assertEqual(after["review_dispatches"], before["review_dispatches"])
        self.assertEqual(after["next"], before["next"])
        self.assertIn("drifted", str(after["next"]["R-4"]))

    def test_origin_is_still_asked_for_every_review_head(self) -> None:
        """Passing the record replaces the board read, not the forced origin read."""
        after = self._tick(pass_records=True)
        asked = {ref for refs in after["ls_remote_refs"] for ref in refs}
        self.assertLessEqual(
            {
                "refs/heads/task/R-1",
                "refs/heads/task/R-2",
                "refs/heads/imported/r3-from-pr",
                "refs/heads/task/R-4",
            },
            asked,
        )
        # The recorded branch is authoritative; the conventional name is not probed.
        self.assertNotIn("refs/heads/task/R-3", asked)

    def test_passed_record_binds_branch_and_repository(self) -> None:
        runtime = supervisor.runtime_ai_status
        record = {"id": "R-3", "branch": "imported/r3-from-pr", "repository": "pantheon"}
        disk = {"tasks": [{"id": "R-3", "branch": "task/R-3-stale"}]}
        refs: list[str] = []

        def fake_run(cmd: Any, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            asked = [str(a) for a in cmd if str(a).startswith("refs/heads/")]
            refs.extend(asked)
            return subprocess.CompletedProcess(cmd, 0, f"{'3' * 40}\t{asked[0]}\n", "")

        binding = mock.Mock(resolved=True, root=self.root)
        with (
            mock.patch.object(runtime, "status_runtime_config", return_value=self._config()),
            mock.patch.object(runtime, "load_state", return_value=disk) as load_state,
            mock.patch.object(runtime, "resolve_task_repository", return_value=binding) as resolve_repo,
            mock.patch.object(runtime.subprocess, "run", side_effect=fake_run) as run,
        ):
            self.assertEqual(runtime.resolve_task_sha("R-3", force_refresh=True, task=record), "3" * 40)
            load_state.assert_not_called()
            self.assertIs(resolve_repo.call_args.args[1], record)
            self.assertEqual(run.call_args.kwargs["cwd"], self.root)
            self.assertEqual(refs, ["refs/heads/imported/r3-from-pr"])

            # A record for some other task is not this task's binding: the
            # canonical lookup decides, exactly as when nothing is passed.
            refs.clear()
            runtime.resolve_task_sha("R-3", force_refresh=True, task={"id": "R-9", "branch": "other"})
            load_state.assert_called_once()
            self.assertEqual(refs, ["refs/heads/task/R-3-stale"])

if __name__ == "__main__":
    unittest.main()
