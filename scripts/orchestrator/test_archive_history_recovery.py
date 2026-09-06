#!/usr/bin/env python3
"""Focused tests for the controlled task-history recovery path.

Two halves are under test and they are deliberately asymmetric:

* the planner in ``scripts/orchestrator/backfill_task_archive_snapshots.py``,
  which grades evidence and may only ever write a plan file;
* ``ai_status.py archive_recovery_apply``, the single writer, which runs inside
  the canonical status transaction.

Every root these modules resolve is pointed at a throwaway fixture *before*
import, and again per test. That ordering is the whole safety property: both
modules bind their archive paths once at import time from the ambient
environment, so a test module that imports first and relocates second is
reading and deleting a live canonical archive. ``ImportOrderSentinelTests``
re-runs the real ordering in a separate interpreter, because no assertion
inside one module can observe which root a *different* module bound.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "orchestrator"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

_TEST_STATUS_ROOT_HANDLE = tempfile.TemporaryDirectory(
    prefix="pantheon-archive-history-recovery-"
)
_TEST_STATUS_ROOT = Path(_TEST_STATUS_ROOT_HANDLE.name).resolve()
_TEST_CONFIG = REPO_ROOT / ".orchestrator" / "config.example.json"

# Pytest imports every test module before running any of them, so `ai_status`
# may already be bound to another module's root. Only set the environment for a
# first import; setUpModule rebinds the shared modules for this module's own
# lifetime, and each test rebinds again onto its private root.
_SAVED_IMPORT_ENV = {
    name: os.environ.get(name)
    for name in (
        "PANTHEON_STATUS_ROOT",
        "ORCH_STATUS_ROOT",
        "ORCH_CONFIG_PATH",
        "PANTHEON_CONFIG_PATH",
    )
}
if "ai_status" not in sys.modules:
    os.environ["PANTHEON_STATUS_ROOT"] = str(_TEST_STATUS_ROOT)
    os.environ["ORCH_STATUS_ROOT"] = str(_TEST_STATUS_ROOT)
    os.environ["ORCH_CONFIG_PATH"] = str(_TEST_CONFIG)
    os.environ["PANTHEON_CONFIG_PATH"] = str(_TEST_CONFIG)
try:
    import ai_status
    import backfill_task_archive_snapshots as planner
    import task_archive
finally:
    for name, value in _SAVED_IMPORT_ENV.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


_AI_STATUS_ROOT_ATTRIBUTES = (
    "STATUS_ROOT",
    "STATUS_FILE",
    "LOG_FILE",
    "CURRENT_WORK_FILE",
    "DOCS_SITE_DIR",
    "STATUS_ROOT_CONFIG_LOCAL_FILE",
    "PLANNING_STATE_FILE",
    "ORCHESTRATOR_STATE_FILE",
    "APPROVAL_QUEUE_FILE",
    "DASHBOARD_BUNDLE_FILE",
    "ARCHIVE_TASKS_DIR",
)
_TASK_ARCHIVE_ROOT_ATTRIBUTES = (
    "STATUS_ROOT",
    "ARCHIVE_DIR",
    "ARCHIVE_TASKS_DIR",
    "ARCHIVE_INDEX_FILE",
)

_RUNTIME_ENV: dict[str, str | None] = {}
_ORIGINAL_AI_STATUS_PATHS: dict[str, Path] = {}
_ORIGINAL_TASK_ARCHIVE_PATHS: dict[str, Path] = {}


def _bind_roots(root: Path) -> None:
    ai_status.STATUS_ROOT = root
    ai_status.STATUS_FILE = root / "ai-status.json"
    ai_status.LOG_FILE = root / "ai-activity-log.jsonl"
    ai_status.CURRENT_WORK_FILE = root / "current-work.md"
    ai_status.DOCS_SITE_DIR = root / "docs-site"
    ai_status.STATUS_ROOT_CONFIG_LOCAL_FILE = root / ".orchestrator" / "config.local.json"
    ai_status.PLANNING_STATE_FILE = root / ".orchestrator" / "planning-state.json"
    ai_status.ORCHESTRATOR_STATE_FILE = root / ".orchestrator" / "state.json"
    ai_status.APPROVAL_QUEUE_FILE = root / ".orchestrator" / "approval-queue.json"
    ai_status.DASHBOARD_BUNDLE_FILE = root / "dashboard-bundle.json"

    task_archive.STATUS_ROOT = root
    task_archive.ARCHIVE_DIR = root / "ai-task-archive"
    task_archive.ARCHIVE_TASKS_DIR = task_archive.ARCHIVE_DIR / "tasks"
    task_archive.ARCHIVE_INDEX_FILE = task_archive.ARCHIVE_DIR / "index.json"
    ai_status.ARCHIVE_TASKS_DIR = task_archive.ARCHIVE_TASKS_DIR


def setUpModule() -> None:
    global _RUNTIME_ENV, _ORIGINAL_AI_STATUS_PATHS, _ORIGINAL_TASK_ARCHIVE_PATHS

    _RUNTIME_ENV = {
        name: os.environ.get(name)
        for name in (
            "PANTHEON_STATUS_ROOT",
            "ORCH_STATUS_ROOT",
            "ORCH_CONFIG_PATH",
            "PANTHEON_CONFIG_PATH",
        )
    }
    _ORIGINAL_AI_STATUS_PATHS = {
        name: getattr(ai_status, name) for name in _AI_STATUS_ROOT_ATTRIBUTES
    }
    _ORIGINAL_TASK_ARCHIVE_PATHS = {
        name: getattr(task_archive, name) for name in _TASK_ARCHIVE_ROOT_ATTRIBUTES
    }
    os.environ["PANTHEON_STATUS_ROOT"] = str(_TEST_STATUS_ROOT)
    os.environ["ORCH_STATUS_ROOT"] = str(_TEST_STATUS_ROOT)
    os.environ["ORCH_CONFIG_PATH"] = str(_TEST_CONFIG)
    os.environ["PANTHEON_CONFIG_PATH"] = str(_TEST_CONFIG)
    _bind_roots(_TEST_STATUS_ROOT)


def tearDownModule() -> None:
    for name, value in _ORIGINAL_AI_STATUS_PATHS.items():
        setattr(ai_status, name, value)
    for name, value in _ORIGINAL_TASK_ARCHIVE_PATHS.items():
        setattr(task_archive, name, value)
    for name, value in _RUNTIME_ENV.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    _TEST_STATUS_ROOT_HANDLE.cleanup()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(root: Path, subjects: list[str]) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    for index, subject in enumerate(subjects):
        (repo / f"f{index}.txt").write_text(str(index), encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", subject)
    return repo


def attestation_set(verifier: str = "Claude") -> dict[str, Any]:
    return {
        name: {
            "source": f"https://example.invalid/{name}",
            "verified_at": "2026-09-06T15:00:00Z",
            "verifier": verifier,
        }
        for name in planner.REQUIRED_DONE_ATTESTATIONS
    }


def inventory_entry(
    task_id: str,
    *,
    pr: int = 100,
    dependents: list[str] | None = None,
    merge_commit: str = "b" * 40,
) -> dict[str, Any]:
    return {
        "missing_id": task_id,
        "dependents": [{"id": dep, "status": "blocked"} for dep in (dependents or [])],
        "candidates": [
            {
                "headRefName": f"task/{task_id}",
                "headRefOid": "a" * 40,
                "mergeCommit": {"oid": merge_commit},
                "mergedAt": "2026-09-01T00:00:00Z",
                "number": pr,
                "state": "MERGED",
                "title": f"{task_id}: delivered",
                "url": f"https://github.com/org/repo/pull/{pr}",
            }
        ],
    }


def write_inventory(path: Path, entries: list[dict[str, Any]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "type": planner.RECOVERY_INVENTORY_TYPE,
                "observed_at": "2026-09-06T15:23:30Z",
                "authoritative": False,
                "limitations": ["candidate PR mapping is not proof of completion"],
                "entries": entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


class RecoveryFixture(unittest.TestCase):
    """A private status root, archive and git repo for every single test."""

    def setUp(self) -> None:
        handle = tempfile.TemporaryDirectory(prefix="archive-history-recovery-case-")
        self.addCleanup(handle.cleanup)
        self.root = Path(handle.name).resolve()
        self.archive_dir = self.root / "ai-task-archive" / "tasks"
        self.archive_dir.mkdir(parents=True)
        self.index_file = self.archive_dir.parent / "index.json"
        self.board_path = self.root / "ai-status.json"

        saved_ai = {name: getattr(ai_status, name) for name in _AI_STATUS_ROOT_ATTRIBUTES}
        saved_archive = {
            name: getattr(task_archive, name) for name in _TASK_ARCHIVE_ROOT_ATTRIBUTES
        }

        def restore() -> None:
            for name, value in saved_ai.items():
                setattr(ai_status, name, value)
            for name, value in saved_archive.items():
                setattr(task_archive, name, value)

        self.addCleanup(restore)
        _bind_roots(self.root)

        env = mock.patch.dict(
            os.environ,
            {
                "PANTHEON_STATUS_ROOT": str(self.root),
                "ORCH_STATUS_ROOT": str(self.root),
                "ORCH_CONFIG_PATH": str(_TEST_CONFIG),
                "PANTHEON_CONFIG_PATH": str(_TEST_CONFIG),
                "AI_NAME": "Claude",
            },
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)

    # -- fixture builders -------------------------------------------------

    def write_board(self, *, revision: str = "rev-1", tasks: list[dict[str, Any]] | None = None) -> None:
        state = ai_status.default_state()
        state["tasks"] = tasks or []
        state["handoffs"] = []
        state["blockers"] = []
        state["_status_write_revision"] = revision
        self.board_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def seed_survivor(self, task_id: str = "ODP-SURVIVOR-001") -> str:
        """Write a snapshot that pre-dates the batch, as the real six do."""

        payload = {
            "version": 1,
            "task_id": task_id,
            "archived_at": "2026-09-06T07:36:34Z",
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": {"id": task_id, "status": "done", "owner": "Claude", "reviewer": "Codex"},
            "handoffs": [],
            "blockers": [],
        }
        path = self.archive_dir / f"{task_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.index_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "updated_at": "2026-09-06T07:36:34Z",
                    "counts": {"total": 1, "completed": 1, "superseded": 0},
                    "recent_terminal_ids": [task_id],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return task_id

    def ensure_repo(self, subjects: list[str] | None = None) -> Path:
        repo = getattr(self, "_repo", None)
        if repo is None:
            repo = make_repo(self.root, subjects if subjects is not None else [])
            self._repo = repo
        return repo

    def local_merge_oid(self, task_id: str) -> str:
        """The commit local git actually credits with delivering ``task_id``."""

        evidence = planner.find_merge_evidence(self.ensure_repo(), task_id, "HEAD")
        assert evidence is not None, task_id
        return str(evidence["merge_commit"])

    def authorization_file(self) -> Path:
        """The authorization the batch is planned under, hashed into it."""

        path = self.root / "RECOVERY_AUTHORIZATION_ZH_TW.md"
        if not path.exists():
            path.write_text(
                "# 授權：依可驗證證據受控重建歷史，缺證據維持 blocked。\n",
                encoding="utf-8",
            )
        return path

    def plan_batch(
        self,
        entries: list[dict[str, Any]],
        *,
        subjects: list[str] | None = None,
        attestations: dict[str, Any] | None = None,
        owner: str = "Claude",
        reviewer: str = "Codex",
    ) -> dict[str, Any]:
        repo = self.ensure_repo(subjects)
        inventory = write_inventory(self.root / "inventory.json", entries)
        return planner.build_recovery_batch(
            repo=repo,
            archive_dir=self.archive_dir,
            board_path=self.board_path,
            inventory_path=inventory,
            ref="HEAD",
            recovery_owner=owner,
            recovery_reviewer=reviewer,
            attestations=attestations or {},
            authorization_path=self.authorization_file(),
        )

    def write_hold(self, batch_path: Path, **overrides: Any) -> Path:
        """A maintenance hold bound by hash to this exact batch file."""

        digest = hashlib.sha256(batch_path.read_bytes()).hexdigest()
        payload: dict[str, Any] = {
            "type": ai_status.RECOVERY_MAINTENANCE_HOLD_TYPE,
            "hold_id": "ORCH-ARCHIVE-HISTORY-RECOVERY-001/hold-1",
            "declared_by": "Human/Ops",
            "declared_at": "2026-09-06T15:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z",
            "scope": "任務歷史重建期間暫停派工",
            "dispatch_paused": True,
            "batch_sha256": digest,
            "batch_approval": {
                "reviewer": "Codex",
                "approved_batch_sha256": digest,
                "approved_at": "2026-09-06T15:30:00Z",
                "source": "https://github.com/org/repo/pull/1231#pullrequestreview-1",
            },
        }
        payload.update(overrides)
        self._hold_seq = getattr(self, "_hold_seq", 0) + 1
        path = self.root / f"hold-{self._hold_seq}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return path

    def write_batch(self, batch: dict[str, Any]) -> Path:
        path = self.root / "batch.json"
        path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def run_apply(self, batch_path: Path, *extra: str, hold: Path | None = None) -> int:
        """Run the real `main()` so the canonical lock is genuinely taken."""

        hold_path = hold if hold is not None else self.write_hold(batch_path)
        with (
            mock.patch.object(ai_status, "emit_status_checks_for_changed_tasks"),
            mock.patch.object(ai_status, "reconcile_status_check_outbox"),
        ):
            return ai_status.main(
                [
                    "ai_status.py",
                    "archive_recovery_apply",
                    "--batch",
                    str(batch_path),
                    "--maintenance-hold",
                    str(hold_path),
                    *extra,
                ]
            )

    def board_state(self) -> dict[str, Any]:
        return json.loads(self.board_path.read_text(encoding="utf-8"))

    def digest(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()


# --- planner: evidence grading -------------------------------------------


class EvidenceGradingTests(RecoveryFixture):
    def test_merged_pr_alone_never_reconstructs_done(self) -> None:
        """A proven merge is delivery evidence, not acceptance evidence."""

        self.write_board()
        self.ensure_repo(["Merge pull request #100 from org/task/TASK-MERGED-001"])
        batch = self.plan_batch(
            [
                inventory_entry(
                    "TASK-MERGED-001",
                    merge_commit=self.local_merge_oid("TASK-MERGED-001"),
                )
            ]
        )

        (entry,) = batch["entries"]
        self.assertEqual(planner.TIER_MERGE_VERIFIED, entry["evidence_tier"])
        self.assertEqual(planner.ACTION_BLOCKED_PLACEHOLDER, entry["action"])
        self.assertEqual(
            sorted(planner.REQUIRED_DONE_ATTESTATIONS), sorted(entry["gaps"])
        )
        self.assertEqual("blocked", entry["record"]["status"])

    def test_full_attestation_set_promotes_a_verified_merge(self) -> None:
        self.write_board()
        self.ensure_repo(["Merge pull request #100 from org/task/TASK-FULL-001"])
        batch = self.plan_batch(
            [inventory_entry("TASK-FULL-001", merge_commit=self.local_merge_oid("TASK-FULL-001"))],
            attestations={"TASK-FULL-001": attestation_set()},
        )

        (entry,) = batch["entries"]
        self.assertEqual(planner.TIER_RECONSTRUCTABLE_DONE, entry["evidence_tier"])
        self.assertEqual(planner.ACTION_ARCHIVE_DONE, entry["action"])
        self.assertEqual([], entry["gaps"])
        self.assertEqual("done", entry["record"]["status"])

    def test_one_missing_attestation_keeps_it_blocked(self) -> None:
        self.write_board()
        attestations = attestation_set()
        attestations.pop("runtime")
        self.ensure_repo(["Merge pull request #100 from org/task/TASK-PARTIAL-001"])
        batch = self.plan_batch(
            [
                inventory_entry(
                    "TASK-PARTIAL-001",
                    merge_commit=self.local_merge_oid("TASK-PARTIAL-001"),
                )
            ],
            attestations={"TASK-PARTIAL-001": attestations},
        )

        (entry,) = batch["entries"]
        self.assertEqual(planner.TIER_MERGE_VERIFIED, entry["evidence_tier"])
        self.assertEqual(planner.ACTION_BLOCKED_PLACEHOLDER, entry["action"])
        self.assertEqual(["runtime"], entry["gaps"])

    def test_attestation_missing_its_source_does_not_count(self) -> None:
        """A named attestation with no traceable source is not an attestation."""

        self.write_board()
        attestations = attestation_set()
        attestations["ci"] = {"source": "", "verified_at": "2026-09-06T15:00:00Z", "verifier": "Claude"}
        self.ensure_repo(["Merge pull request #100 from org/task/TASK-HOLLOW-001"])
        batch = self.plan_batch(
            [
                inventory_entry(
                    "TASK-HOLLOW-001",
                    merge_commit=self.local_merge_oid("TASK-HOLLOW-001"),
                )
            ],
            attestations={"TASK-HOLLOW-001": attestations},
        )

        (entry,) = batch["entries"]
        self.assertEqual(["ci"], entry["gaps"])
        self.assertEqual(planner.ACTION_BLOCKED_PLACEHOLDER, entry["action"])

    def test_source_disagreement_about_the_merge_commit_blocks_promotion(self) -> None:
        """Local git and the inventory naming different commits is not a `done`."""

        self.write_board()
        self.ensure_repo(["Merge pull request #100 from org/task/TASK-MISMATCH-001"])
        batch = self.plan_batch(
            [inventory_entry("TASK-MISMATCH-001", merge_commit="c" * 40)],
            attestations={"TASK-MISMATCH-001": attestation_set()},
        )

        (entry,) = batch["entries"]
        self.assertEqual(planner.TIER_MERGE_VERIFIED, entry["evidence_tier"])
        self.assertEqual(planner.ACTION_BLOCKED_PLACEHOLDER, entry["action"])
        self.assertEqual(["candidate_merge_commit_mismatch"], entry["gaps"])

    def test_unverifiable_merge_is_graded_claimed_not_verified(self) -> None:
        """The cross-repo candidate: a real PR this repository cannot confirm."""

        self.write_board()
        batch = self.plan_batch([inventory_entry("DPF-CROSS-001")], subjects=["unrelated"])

        (entry,) = batch["entries"]
        self.assertEqual(planner.TIER_MERGE_CLAIMED, entry["evidence_tier"])
        self.assertIn("merge_not_verifiable_on_ref", entry["gaps"])

    def test_entry_without_candidates_is_graded_no_evidence(self) -> None:
        self.write_board()
        batch = self.plan_batch(
            [{"missing_id": "TASK-EMPTY-001", "dependents": [], "candidates": []}],
            subjects=["unrelated"],
        )

        (entry,) = batch["entries"]
        self.assertEqual(planner.TIER_NO_EVIDENCE, entry["evidence_tier"])
        self.assertIn("no_candidate_delivery_evidence", entry["gaps"])
        self.assertEqual([], batch["refusals"])


# --- planner: refusals ----------------------------------------------------


class RecoveryRefusalTests(RecoveryFixture):
    def test_candidate_missing_provenance_is_refused_not_downgraded(self) -> None:
        """An untraceable claim is worse than no claim, so it fails closed."""

        self.write_board()
        entry = inventory_entry("TASK-NOPROV-001")
        entry["candidates"][0].pop("url")
        entry["candidates"][0].pop("mergeCommit")
        batch = self.plan_batch([entry], subjects=["unrelated"])

        self.assertEqual([], batch["entries"])
        (refusal,) = batch["refusals"]
        self.assertEqual("TASK-NOPROV-001", refusal["task_id"])
        self.assertIn(planner.REFUSAL_MISSING_PROVENANCE, refusal["reasons"])

    def test_attestation_supplying_a_historical_actor_is_refused(self) -> None:
        """The authorization excludes inventing owners, approvers and Human GO."""

        self.write_board()
        attestations = attestation_set()
        attestations["historical_owner"] = "Agy6"
        batch = self.plan_batch(
            [inventory_entry("TASK-FAKE-ACTOR-001")],
            subjects=["Merge pull request #100 from org/task/TASK-FAKE-ACTOR-001"],
            attestations={"TASK-FAKE-ACTOR-001": attestations},
        )

        self.assertEqual([], batch["entries"])
        (refusal,) = batch["refusals"]
        self.assertIn(planner.REFUSAL_FABRICATED_ACTOR, refusal["reasons"])

    def test_attestation_supplying_a_human_go_is_refused(self) -> None:
        self.write_board()
        attestations = attestation_set()
        attestations["human_go"] = {"source": "chat", "verified_at": "x", "verifier": "y"}
        batch = self.plan_batch(
            [inventory_entry("TASK-FAKE-GO-001")],
            subjects=["Merge pull request #100 from org/task/TASK-FAKE-GO-001"],
            attestations={"TASK-FAKE-GO-001": attestations},
        )

        (refusal,) = batch["refusals"]
        self.assertIn(planner.REFUSAL_FABRICATED_ACTOR, refusal["reasons"])

    def test_id_already_on_the_board_is_refused(self) -> None:
        self.write_board(
            tasks=[
                {
                    "id": "TASK-LIVE-001",
                    "title": "live",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "in_progress",
                    "last_update": "2026-09-06T00:00:00Z",
                }
            ]
        )
        batch = self.plan_batch(
            [inventory_entry("TASK-LIVE-001")],
            subjects=["Merge pull request #100 from org/task/TASK-LIVE-001"],
        )

        self.assertEqual([], batch["entries"])
        (refusal,) = batch["refusals"]
        self.assertIn(planner.REFUSAL_ACTIVE_CONFLICT, refusal["reasons"])

    def test_id_already_archived_is_refused(self) -> None:
        """The six snapshots that survived the incident are never written over."""

        survivor = self.seed_survivor()
        self.write_board()
        batch = self.plan_batch(
            [inventory_entry(survivor)],
            subjects=[f"Merge pull request #100 from org/task/{survivor}"],
        )

        self.assertEqual([], batch["entries"])
        (refusal,) = batch["refusals"]
        self.assertIn(planner.REFUSAL_ARCHIVE_CONFLICT, refusal["reasons"])

    def test_duplicate_inventory_ids_are_refused(self) -> None:
        self.write_board()
        batch = self.plan_batch(
            [inventory_entry("TASK-DUP-001"), inventory_entry("TASK-DUP-001", pr=101)],
            subjects=["Merge pull request #100 from org/task/TASK-DUP-001"],
        )

        self.assertEqual(1, len(batch["entries"]))
        (refusal,) = batch["refusals"]
        self.assertIn(planner.REFUSAL_DUPLICATE_ID, refusal["reasons"])

    def test_unknown_historical_actor_cannot_be_the_recovery_owner(self) -> None:
        self.write_board()
        with self.assertRaises(planner.RecoveryInputError):
            self.plan_batch(
                [inventory_entry("TASK-X-001")],
                subjects=["unrelated"],
                owner=planner.UNKNOWN_ACTOR,
            )

    def test_recovery_reviewer_cannot_equal_recovery_owner(self) -> None:
        self.write_board()
        with self.assertRaises(planner.RecoveryInputError):
            self.plan_batch(
                [inventory_entry("TASK-X-001")], subjects=["unrelated"], reviewer="Claude"
            )

    def test_board_without_a_write_revision_cannot_be_pinned(self) -> None:
        """No revision means no compare-and-swap, so there is no valid batch."""

        state = ai_status.default_state()
        state["tasks"] = []
        state.pop("_status_write_revision", None)
        self.board_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        with self.assertRaises(planner.RecoveryInputError):
            self.plan_batch([inventory_entry("TASK-X-001")], subjects=["unrelated"])

    def test_inventory_of_the_wrong_type_is_refused(self) -> None:
        self.write_board()
        path = self.root / "wrong.json"
        path.write_text(json.dumps({"type": "something_else", "entries": [1]}), encoding="utf-8")

        with self.assertRaises(planner.RecoveryInputError):
            planner.load_recovery_inventory(path)


# --- planner: reconstructed record shape ---------------------------------


class RecoveryRecordShapeTests(RecoveryFixture):
    def test_placeholder_is_blocked_non_dispatchable_and_traceable(self) -> None:
        self.write_board()
        batch = self.plan_batch(
            [inventory_entry("TASK-SHAPE-001", dependents=["ODP-DOWNSTREAM-001"])],
            subjects=["Merge pull request #100 from org/task/TASK-SHAPE-001"],
        )

        record = batch["entries"][0]["record"]
        self.assertEqual("blocked", record["status"])
        self.assertIs(True, record["non_dispatchable"])
        self.assertEqual("Human/Ops", record["waiting_for"])

        recovery = record["history_recovery"]
        self.assertIs(True, recovery["reconstructed"])
        self.assertEqual(
            {"owner": "Claude", "reviewer": "Codex"}, recovery["recovery_actors"]
        )
        self.assertEqual(
            {
                "owner": planner.UNKNOWN_ACTOR,
                "reviewer": planner.UNKNOWN_ACTOR,
                "human_go": planner.UNKNOWN_ACTOR,
            },
            recovery["historical_actors"],
        )
        self.assertEqual(
            ["ODP-DOWNSTREAM-001"], [dep["id"] for dep in recovery["known_dependents"]]
        )
        self.assertEqual(
            "https://github.com/org/repo/pull/100",
            recovery["evidence"]["candidates"][0]["url"],
        )
        self.assertEqual(batch["baseline"]["ref_commit"], recovery["baseline"]["ref_commit"])

    def test_reconstructed_done_keeps_historical_actors_unknown(self) -> None:
        """The recovery pair must never be recorded as who ran the original task."""

        self.write_board()
        self.ensure_repo(["Merge pull request #100 from org/task/TASK-DONE-SHAPE-001"])
        batch = self.plan_batch(
            [
                inventory_entry(
                    "TASK-DONE-SHAPE-001",
                    merge_commit=self.local_merge_oid("TASK-DONE-SHAPE-001"),
                )
            ],
            attestations={"TASK-DONE-SHAPE-001": attestation_set()},
        )

        record = batch["entries"][0]["record"]
        self.assertEqual(planner.UNKNOWN_ACTOR, record["owner"])
        self.assertEqual(planner.UNKNOWN_ACTOR, record["reviewer"])
        self.assertEqual(
            "Claude", record["history_recovery"]["recovery_actors"]["owner"]
        )

    def test_baseline_pins_every_input_the_batch_depends_on(self) -> None:
        survivor = self.seed_survivor()
        self.write_board(revision="rev-baseline")
        batch = self.plan_batch([inventory_entry("TASK-B-001")], subjects=["unrelated"])

        baseline = batch["baseline"]
        self.assertEqual("rev-baseline", baseline["board_revision"])
        self.assertEqual(
            self.digest(self.archive_dir / f"{survivor}.json"),
            baseline["archive_snapshot_digests"][survivor],
        )
        self.assertEqual(self.digest(self.index_file), baseline["archive_index_sha256"])
        self.assertEqual(40, len(baseline["ref_commit"]))
        self.assertEqual(self.digest(self.board_path), baseline["board_sha256"])
        self.assertEqual(
            self.digest(self.root / "inventory.json"), baseline["inventory_sha256"]
        )
        self.assertEqual(
            self.digest(self.authorization_file()), baseline["authorization_sha256"]
        )


# --- apply: the single writer --------------------------------------------


class RecoveryApplyTests(RecoveryFixture):
    def test_preview_writes_nothing_at_all(self) -> None:
        """A preview that bumped the revision would invalidate its own batch."""

        survivor = self.seed_survivor()
        self.write_board(revision="rev-preview")
        batch_path = self.write_batch(
            self.plan_batch(
                [inventory_entry("TASK-PREVIEW-001")],
                subjects=["Merge pull request #100 from org/task/TASK-PREVIEW-001"],
            )
        )
        board_before = self.digest(self.board_path)
        survivor_before = self.digest(self.archive_dir / f"{survivor}.json")

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path)

        self.assertEqual(0, caught.exception.code)
        self.assertIsInstance(caught.exception, ai_status.ArchiveRecoveryPreview)
        self.assertEqual(board_before, self.digest(self.board_path))
        self.assertEqual(survivor_before, self.digest(self.archive_dir / f"{survivor}.json"))
        self.assertEqual(
            [f"{survivor}.json"], [p.name for p in sorted(self.archive_dir.glob("*.json"))]
        )

    def test_apply_stages_placeholders_and_leaves_survivors_untouched(self) -> None:
        survivor = self.seed_survivor()
        self.write_board(revision="rev-apply")
        batch_path = self.write_batch(
            self.plan_batch(
                [inventory_entry("TASK-APPLY-001", dependents=["ODP-DOWN-001"])],
                subjects=["Merge pull request #100 from org/task/TASK-APPLY-001"],
            )
        )
        survivor_before = self.digest(self.archive_dir / f"{survivor}.json")
        index_before = self.digest(self.index_file)
        checkpoint = self.root / "checkpoint.json"

        self.assertEqual(0, self.run_apply(batch_path, "--checkpoint", str(checkpoint), "--confirm"))

        board = self.board_state()
        (recovered,) = [t for t in board["tasks"] if t["id"] == "TASK-APPLY-001"]
        self.assertEqual("blocked", recovered["status"])
        self.assertIs(True, recovered["non_dispatchable"])
        self.assertIs(True, recovered["history_recovery"]["reconstructed"])
        # A placeholder batch touches no archive file, so the survivors and the
        # shared index are byte-identical afterwards.
        self.assertEqual(survivor_before, self.digest(self.archive_dir / f"{survivor}.json"))
        self.assertEqual(index_before, self.digest(self.index_file))

        receipt = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual("applied", receipt["status"])
        self.assertEqual([], receipt["applied_archive_snapshots"])
        self.assertEqual(["TASK-APPLY-001"], receipt["board_placeholders_on_disk"])
        self.assertIs(False, receipt["rollback_performed"])
        # Rewritten after the enclosing transaction committed, so this is a
        # read-back of the board file rather than a claim about a staged row.
        self.assertIs(True, receipt["board_persistence_verified"])
        self.assertIs(True, receipt["board_readback"][0]["history_recovery_present"])

    def test_reconstructed_done_lands_in_the_archive_and_resolves(self) -> None:
        self.write_board(revision="rev-done")
        self.ensure_repo(["Merge pull request #100 from org/task/TASK-ARCHIVED-001"])
        batch_path = self.write_batch(
            self.plan_batch(
                [
                    inventory_entry(
                        "TASK-ARCHIVED-001",
                        merge_commit=self.local_merge_oid("TASK-ARCHIVED-001"),
                    )
                ],
                attestations={"TASK-ARCHIVED-001": attestation_set()},
            )
        )
        checkpoint = self.root / "checkpoint.json"

        self.assertEqual(0, self.run_apply(batch_path, "--checkpoint", str(checkpoint), "--confirm"))

        snapshot = json.loads(
            (self.archive_dir / "TASK-ARCHIVED-001.json").read_text(encoding="utf-8")
        )
        self.assertEqual("done", snapshot["terminal_status"])
        self.assertTrue(task_archive.task_satisfies_dependency(snapshot["task"]))
        self.assertIs(True, snapshot["task"]["history_recovery"]["reconstructed"])
        self.assertEqual(planner.UNKNOWN_ACTOR, snapshot["task"]["owner"])
        self.assertNotIn(
            "TASK-ARCHIVED-001", [t["id"] for t in self.board_state()["tasks"]]
        )
        index = json.loads(self.index_file.read_text(encoding="utf-8"))
        self.assertIn("TASK-ARCHIVED-001", index["recent_terminal_ids"])

    def test_board_revision_drift_refuses_the_whole_batch(self) -> None:
        """Someone else's canonical write between plan and apply must win."""

        self.write_board(revision="rev-planned")
        batch_path = self.write_batch(
            self.plan_batch(
                [inventory_entry("TASK-DRIFT-001")],
                subjects=["Merge pull request #100 from org/task/TASK-DRIFT-001"],
            )
        )
        self.write_board(revision="rev-someone-else")
        board_before = self.digest(self.board_path)
        checkpoint = self.root / "checkpoint.json"

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(checkpoint), "--confirm")

        self.assertIn("baseline drifted", str(caught.exception))
        self.assertEqual(board_before, self.digest(self.board_path))
        self.assertFalse(checkpoint.exists())
        self.assertEqual([], sorted(self.archive_dir.glob("*.json")))

    def test_archive_drift_refuses_the_whole_batch(self) -> None:
        """A snapshot appearing after planning invalidates the batch's premise."""

        self.write_board(revision="rev-archive-drift")
        batch_path = self.write_batch(
            self.plan_batch(
                [inventory_entry("TASK-ADRIFT-001")],
                subjects=["Merge pull request #100 from org/task/TASK-ADRIFT-001"],
            )
        )
        self.seed_survivor("ODP-LATE-ARRIVAL-001")

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(
                batch_path, "--checkpoint", str(self.root / "checkpoint.json"), "--confirm"
            )

        self.assertIn("archive snapshots moved", str(caught.exception))

    def test_second_apply_of_the_same_batch_refuses(self) -> None:
        """Idempotency is a refusal, not a silent merge into the live row."""

        self.write_board(revision="rev-idem")
        batch_path = self.write_batch(
            self.plan_batch(
                [inventory_entry("TASK-IDEM-001")],
                subjects=["Merge pull request #100 from org/task/TASK-IDEM-001"],
            )
        )
        checkpoint = self.root / "checkpoint.json"
        self.assertEqual(0, self.run_apply(batch_path, "--checkpoint", str(checkpoint), "--confirm"))
        board_after_first = self.digest(self.board_path)

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(checkpoint), "--confirm")

        message = str(caught.exception)
        self.assertTrue(
            "baseline drifted" in message or "already on the active board" in message,
            message,
        )
        self.assertEqual(board_after_first, self.digest(self.board_path))
        self.assertEqual(
            1, len([t for t in self.board_state()["tasks"] if t["id"] == "TASK-IDEM-001"])
        )

    def test_partial_archive_failure_checkpoints_without_claiming_rollback(self) -> None:
        self.write_board(revision="rev-partial")
        self.ensure_repo(
            [
                "Merge pull request #101 from org/task/TASK-P1-001",
                "Merge pull request #102 from org/task/TASK-P2-002",
                "Merge pull request #103 from org/task/TASK-P3-003",
            ]
        )
        batch = self.plan_batch(
            [
                inventory_entry("TASK-P1-001", pr=101, merge_commit=self.local_merge_oid("TASK-P1-001")),
                inventory_entry("TASK-P2-002", pr=102, merge_commit=self.local_merge_oid("TASK-P2-002")),
                inventory_entry("TASK-P3-003", pr=103, merge_commit=self.local_merge_oid("TASK-P3-003")),
            ],
            attestations={
                "TASK-P1-001": attestation_set(),
                "TASK-P2-002": attestation_set(),
                "TASK-P3-003": attestation_set(),
            },
        )
        self.assertEqual(3, batch["counts"][planner.ACTION_ARCHIVE_DONE])
        batch_path = self.write_batch(batch)
        checkpoint = self.root / "checkpoint.json"

        real_writer = ai_status.archive_task_snapshot
        calls: list[str] = []

        def flaky(task: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            calls.append(task["id"])
            if len(calls) == 2:
                raise OSError("disk full")
            return real_writer(task, **kwargs)

        with mock.patch.object(ai_status, "archive_task_snapshot", side_effect=flaky):
            with self.assertRaises(SystemExit) as caught:
                self.run_apply(batch_path, "--checkpoint", str(checkpoint), "--confirm")

        message = str(caught.exception)
        self.assertIn("have NOT been rolled back", message)
        self.assertIn("no board row was committed", message.lower())

        receipt = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual("partial", receipt["status"])
        self.assertEqual(["TASK-P1-001"], receipt["applied_archive_snapshots"])
        self.assertEqual([], receipt["board_placeholders_on_disk"])
        self.assertIs(False, receipt["board_persistence_verified"])
        self.assertIs(False, receipt["rollback_performed"])
        # The receipt is the truth: exactly one snapshot is on disk, and the
        # board never advanced.
        self.assertEqual(
            ["TASK-P1-001.json"], [p.name for p in sorted(self.archive_dir.glob("*.json"))]
        )
        self.assertEqual([], self.board_state()["tasks"])

    def test_confirm_without_a_checkpoint_is_refused(self) -> None:
        self.write_board(revision="rev-nockpt")
        batch_path = self.write_batch(
            self.plan_batch([inventory_entry("TASK-NOCKPT-001")], subjects=["unrelated"])
        )

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--confirm")

        self.assertIn("--checkpoint", str(caught.exception))
        self.assertEqual([], self.board_state()["tasks"])

    def test_maintenance_hold_is_mandatory(self) -> None:
        self.write_board(revision="rev-hold")
        batch_path = self.write_batch(
            self.plan_batch([inventory_entry("TASK-HOLD-001")], subjects=["unrelated"])
        )

        with (
            mock.patch.object(ai_status, "emit_status_checks_for_changed_tasks"),
            mock.patch.object(ai_status, "reconcile_status_check_outbox"),
        ):
            with self.assertRaises(SystemExit) as caught:
                ai_status.main(
                    ["ai_status.py", "archive_recovery_apply", "--batch", str(batch_path)]
                )

        self.assertIn("--maintenance-hold", str(caught.exception))

    def test_unregistered_recovery_actor_is_refused(self) -> None:
        """`ensure_agent()` would invent the roster entry; this refuses first."""

        self.write_board(revision="rev-actor")
        batch = self.plan_batch([inventory_entry("TASK-ACTOR-001")], subjects=["unrelated"])
        batch["recovery_actors"]["owner"] = "Nessie9"
        record = batch["entries"][0]["record"]
        record["owner"] = "Nessie9"
        record["history_recovery"]["recovery_actors"]["owner"] = "Nessie9"
        batch_path = self.write_batch(batch)

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(self.root / "c.json"), "--confirm")

        self.assertIn("recovery owner", str(caught.exception))
        self.assertEqual([], self.board_state()["tasks"])
        self.assertFalse((self.root / "c.json").exists())

    def test_reconstructed_archive_record_naming_a_live_actor_is_refused(self) -> None:
        """A hand-edited batch must not put a present-day name into the archive."""

        self.write_board(revision="rev-liveactor")
        self.ensure_repo(["Merge pull request #100 from org/task/TASK-LIVEACTOR-001"])
        batch = self.plan_batch(
            [
                inventory_entry(
                    "TASK-LIVEACTOR-001",
                    merge_commit=self.local_merge_oid("TASK-LIVEACTOR-001"),
                )
            ],
            attestations={"TASK-LIVEACTOR-001": attestation_set()},
        )
        self.assertEqual(planner.ACTION_ARCHIVE_DONE, batch["entries"][0]["action"])
        batch["entries"][0]["record"]["owner"] = "Claude"
        batch_path = self.write_batch(batch)

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(self.root / "c.json"), "--confirm")

        self.assertIn(planner.UNKNOWN_ACTOR, str(caught.exception))
        self.assertEqual([], sorted(self.archive_dir.glob("*.json")))

    def test_placeholder_actors_must_match_the_batch_recovery_pair(self) -> None:
        self.write_board(revision="rev-mismatch")
        batch = self.plan_batch([inventory_entry("TASK-PMATCH-001")], subjects=["unrelated"])
        batch["entries"][0]["record"]["reviewer"] = "Codex2"
        batch_path = self.write_batch(batch)

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(self.root / "c.json"), "--confirm")

        self.assertIn("do not match the batch recovery pair", str(caught.exception))
        self.assertEqual([], self.board_state()["tasks"])

    def test_foreign_document_is_not_treated_as_a_batch(self) -> None:
        self.write_board(revision="rev-foreign")
        path = self.root / "not-a-batch.json"
        path.write_text(json.dumps({"type": "read_only_recovery_inventory"}), encoding="utf-8")

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(path, "--checkpoint", str(self.root / "c.json"), "--confirm")

        self.assertIn("type must be", str(caught.exception))
        self.assertEqual([], self.board_state()["tasks"])


# --- apply: admission of a batch that was edited after planning -----------


class RecoveryAdmissionTests(RecoveryFixture):
    """The batch is a file between planning and applying, so it is re-derived.

    Every case here starts from a batch the planner really produced and then
    makes the single edit that would turn it into something the planner would
    never emit. The writer has to refuse each one on its own, without help from
    the grading that produced the file.
    """

    def forged(self, task_id: str, **_: Any) -> dict[str, Any]:
        self.write_board(revision=f"rev-{task_id.lower()}")
        return self.plan_batch([inventory_entry(task_id)], subjects=["unrelated"])

    def refuse(self, batch: dict[str, Any]) -> str:
        batch_path = self.write_batch(batch)
        checkpoint = self.root / "c.json"
        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(checkpoint), "--confirm")
        # Nothing may land, in either store, for any of these.
        self.assertEqual([], self.board_state()["tasks"])
        self.assertEqual([], sorted(self.archive_dir.glob("*.json")))
        self.assertFalse(checkpoint.exists())
        return str(caught.exception)

    def test_evidence_free_entry_relabelled_as_reconstructed_done_is_refused(self) -> None:
        """Relabelling a placeholder is the cheapest way to forge history."""

        batch = self.forged("TASK-FORGE-001")
        entry = batch["entries"][0]
        self.assertEqual(planner.ACTION_BLOCKED_PLACEHOLDER, entry["action"])
        entry["action"] = planner.ACTION_ARCHIVE_DONE
        entry["archived_at"] = "2026-09-01T00:00:00Z"
        record = entry["record"]
        record["status"] = "done"
        record["terminal_outcome"] = "completed"
        record["owner"] = planner.UNKNOWN_ACTOR
        record["reviewer"] = planner.UNKNOWN_ACTOR
        record["history_recovery"]["record_kind"] = planner.RECORD_KIND_DONE

        message = self.refuse(batch)
        self.assertIn(planner.TIER_RECONSTRUCTABLE_DONE, message)
        self.assertIn("open gaps", message)
        self.assertIn("no complete attestation", message)

    def test_evidence_tier_raised_without_the_evidence_is_refused(self) -> None:
        """A tier is a claim about evidence, so the evidence is re-checked."""

        batch = self.forged("TASK-TIER-001")
        entry = batch["entries"][0]
        entry["action"] = planner.ACTION_ARCHIVE_DONE
        entry["evidence_tier"] = planner.TIER_RECONSTRUCTABLE_DONE
        entry["gaps"] = []
        entry["archived_at"] = "2026-09-01T00:00:00Z"
        record = entry["record"]
        record["status"] = "done"
        record["terminal_outcome"] = "completed"
        record["owner"] = planner.UNKNOWN_ACTOR
        record["reviewer"] = planner.UNKNOWN_ACTOR
        history = record["history_recovery"]
        history["record_kind"] = planner.RECORD_KIND_DONE
        history["evidence_tier"] = planner.TIER_RECONSTRUCTABLE_DONE
        history["gaps"] = []

        message = self.refuse(batch)
        self.assertIn("no complete attestation", message)
        self.assertIn("no verified local merge commit", message)

    def test_placeholder_flipped_into_dispatchable_work_is_refused(self) -> None:
        """A todo, dispatchable placeholder is just an invented task."""

        batch = self.forged("TASK-DISPATCH-001")
        record = batch["entries"][0]["record"]
        record["status"] = "todo"
        record["non_dispatchable"] = False

        message = self.refuse(batch)
        self.assertIn(f"status must be {planner.PLACEHOLDER_STATUS!r}", message)
        self.assertIn("must be non_dispatchable", message)

    def test_record_id_that_does_not_match_the_entry_is_refused(self) -> None:
        """The id decides both conflict checks; the record decides what lands."""

        batch = self.forged("TASK-IDSWAP-001")
        batch["entries"][0]["record"]["id"] = "TASK-SOMETHING-ELSE-999"

        message = self.refuse(batch)
        self.assertIn("does not match the entry task_id", message)

    def test_duplicate_ids_inside_one_batch_are_refused(self) -> None:
        batch = self.forged("TASK-DUP-001")
        batch["entries"].append(deepcopy(batch["entries"][0]))

        self.assertIn("repeats task id(s)", self.refuse(batch))

    def test_record_stripped_of_its_provenance_is_refused(self) -> None:
        batch = self.forged("TASK-NOPROV-001")
        del batch["entries"][0]["record"]["history_recovery"]

        self.assertIn("no history_recovery provenance", self.refuse(batch))

    def test_record_naming_a_historical_actor_is_refused(self) -> None:
        """Provenance says the original actors are unrecoverable; keep it so."""

        batch = self.forged("TASK-HISTACTOR-001")
        batch["entries"][0]["record"]["history_recovery"]["historical_actors"][
            "owner"
        ] = "Claude"

        self.assertIn("historical_actors must stay", self.refuse(batch))

    def test_record_carrying_a_fabricated_human_go_is_refused(self) -> None:
        batch = self.forged("TASK-HUMANGO-001")
        batch["entries"][0]["record"]["history_recovery"]["evidence"]["attestations"] = {
            "human_go": "Human/Ops approved on 2026-01-01"
        }

        self.assertIn("may not supply", self.refuse(batch))

    def test_record_baseline_rewritten_to_a_different_origin_is_refused(self) -> None:
        batch = self.forged("TASK-ORIGIN-001")
        batch["entries"][0]["record"]["history_recovery"]["baseline"][
            "ref_commit"
        ] = "f" * 40

        self.assertIn("does not match the batch baseline", self.refuse(batch))


# --- apply: a batch is one reviewed unit ----------------------------------


class RecoveryAllOrNothingTests(RecoveryFixture):
    def test_one_planning_refusal_refuses_every_other_entry_too(self) -> None:
        """A refusal disqualifies the document, not just the id it names.

        The planner reports refusals alongside the entries it could still
        grade. Applying "the rest" would silently narrow a batch a reviewer
        approved as a whole, and the narrowing would happen after the review.
        """

        self.seed_survivor("TASK-CONFLICT-001")
        self.write_board(revision="rev-mixed")
        batch = self.plan_batch(
            [inventory_entry("TASK-VALID-001"), inventory_entry("TASK-CONFLICT-001")],
            subjects=["unrelated"],
        )
        self.assertEqual(["TASK-VALID-001"], [e["task_id"] for e in batch["entries"]])
        self.assertEqual(["TASK-CONFLICT-001"], [r["task_id"] for r in batch["refusals"]])
        self.assertIs(False, batch["applicable"])
        batch_path = self.write_batch(batch)
        checkpoint = self.root / "c.json"

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(checkpoint), "--confirm")

        message = str(caught.exception)
        self.assertIn("planning refusal", message)
        self.assertIn("TASK-CONFLICT-001", message)
        # The entry that graded cleanly must not have landed on its own.
        self.assertEqual([], self.board_state()["tasks"])
        self.assertFalse(checkpoint.exists())

    def test_a_refusal_blocks_the_batch_even_in_preview(self) -> None:
        self.seed_survivor("TASK-PREFUSE-001")
        self.write_board(revision="rev-prefuse")
        batch_path = self.write_batch(
            self.plan_batch(
                [inventory_entry("TASK-PVALID-001"), inventory_entry("TASK-PREFUSE-001")],
                subjects=["unrelated"],
            )
        )

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path)

        self.assertNotIsInstance(caught.exception, ai_status.ArchiveRecoveryPreview)
        self.assertIn("planning refusal", str(caught.exception))


# --- apply: checkpoints describe disk, not intentions ----------------------


class RecoveryCheckpointReadbackTests(RecoveryFixture):
    """Failure receipts are read back from the files, at every failure point."""

    def done_batch(self, task_id: str) -> Path:
        self.write_board(revision=f"rev-{task_id.lower()}")
        self.ensure_repo([f"Merge pull request #100 from org/task/{task_id}"])
        return self.write_batch(
            self.plan_batch(
                [inventory_entry(task_id, merge_commit=self.local_merge_oid(task_id))],
                attestations={task_id: attestation_set()},
            )
        )

    def test_index_save_failure_still_reports_the_snapshot_on_disk(self) -> None:
        """`archive_task_snapshot()` writes the snapshot, then the index.

        A receipt built from this command's own bookkeeping would omit the
        snapshot, because the writer raised before returning it -- and the
        operator would go looking for a file the receipt says is not there.
        """

        batch_path = self.done_batch("TASK-IDXSAVE-001")
        checkpoint = self.root / "checkpoint.json"

        with mock.patch.object(
            task_archive, "save_archive_index", side_effect=OSError("index volume gone")
        ):
            with self.assertRaises(SystemExit) as caught:
                self.run_apply(batch_path, "--checkpoint", str(checkpoint), "--confirm")

        self.assertIn("have NOT been rolled back", str(caught.exception))
        self.assertTrue((self.archive_dir / "TASK-IDXSAVE-001.json").is_file())

        receipt = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual("partial", receipt["status"])
        self.assertEqual(["TASK-IDXSAVE-001"], receipt["applied_archive_snapshots"])
        (row,) = receipt["archive_readback"]
        self.assertIs(True, row["snapshot_on_disk"])
        self.assertIs(False, row["listed_in_archive_index"])
        self.assertIs(False, receipt["rollback_performed"])
        self.assertEqual([], self.board_state()["tasks"])

    def test_index_rebuild_failure_is_checkpointed(self) -> None:
        """The rebuild used to sit outside every try/except."""

        batch_path = self.done_batch("TASK-IDXBUILD-001")
        checkpoint = self.root / "checkpoint.json"

        with mock.patch.object(
            ai_status, "rebuild_archive_index", side_effect=OSError("index unwritable")
        ):
            with self.assertRaises(SystemExit) as caught:
                self.run_apply(batch_path, "--checkpoint", str(checkpoint), "--confirm")

        self.assertIn("could not rebuild the archive index", str(caught.exception))
        receipt = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual("partial", receipt["status"])
        self.assertEqual(["TASK-IDXBUILD-001"], receipt["applied_archive_snapshots"])
        self.assertIs(False, receipt["board_persistence_verified"])
        self.assertEqual([], self.board_state()["tasks"])

    def test_snapshot_that_cannot_be_read_back_stops_the_apply(self) -> None:
        """A writer that returns is not the same as a snapshot on disk."""

        batch_path = self.done_batch("TASK-NOREAD-001")
        checkpoint = self.root / "checkpoint.json"
        real_writer = ai_status.archive_task_snapshot

        def write_then_vanish(task: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            snapshot = real_writer(task, **kwargs)
            (self.archive_dir / f"{task['id']}.json").unlink()
            return snapshot

        with mock.patch.object(
            ai_status, "archive_task_snapshot", side_effect=write_then_vanish
        ):
            with self.assertRaises(SystemExit) as caught:
                self.run_apply(batch_path, "--checkpoint", str(checkpoint), "--confirm")

        self.assertIn("could not read back terminal snapshots", str(caught.exception))
        receipt = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual("partial", receipt["status"])
        self.assertEqual([], receipt["applied_archive_snapshots"])
        self.assertEqual([], self.board_state()["tasks"])

    def test_board_that_never_reaches_disk_is_reported_after_the_transaction(self) -> None:
        """`sync_all()` writes the board after this command returns.

        Until then the placeholders exist only in the state dict, so the
        command cannot honestly call them applied. The post-commit read-back
        is what turns the receipt from staged into persisted -- or reports that
        it never happened.
        """

        self.write_board(revision="rev-noboard")
        batch_path = self.write_batch(
            self.plan_batch([inventory_entry("TASK-NOBOARD-001")], subjects=["unrelated"])
        )
        checkpoint = self.root / "checkpoint.json"

        with mock.patch.object(ai_status, "save_state"):
            with self.assertRaises(SystemExit) as caught:
                self.run_apply(batch_path, "--checkpoint", str(checkpoint), "--confirm")

        message = str(caught.exception)
        self.assertIn("TASK-NOBOARD-001", message)
        self.assertIn("nothing was rolled back", message)

        receipt = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual("partial", receipt["status"])
        self.assertEqual([], receipt["board_placeholders_on_disk"])
        self.assertEqual(["TASK-NOBOARD-001"], receipt["board_placeholders_intended"])
        self.assertIs(False, receipt["board_persistence_verified"])
        self.assertIs(False, receipt["rollback_performed"])

    def test_successful_apply_reports_both_stores_read_back(self) -> None:
        batch_path = self.done_batch("TASK-BOTH-001")
        checkpoint = self.root / "checkpoint.json"

        self.assertEqual(
            0, self.run_apply(batch_path, "--checkpoint", str(checkpoint), "--confirm")
        )

        receipt = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual("applied", receipt["status"])
        self.assertEqual(["TASK-BOTH-001"], receipt["applied_archive_snapshots"])
        (row,) = receipt["archive_readback"]
        self.assertIs(True, row["snapshot_on_disk"])
        self.assertIs(True, row["listed_in_archive_index"])
        self.assertEqual("done", row["terminal_status"])
        self.assertIs(True, receipt["board_persistence_verified"])
        self.assertEqual(receipt["batch_sha256"], self.digest(batch_path))


# --- apply: every pinned input is re-checked in the lock -------------------


class RecoveryBaselineProvenanceTests(RecoveryFixture):
    def test_board_bytes_that_moved_at_the_same_revision_are_refused(self) -> None:
        """Revision equality is not board equality."""

        self.write_board(revision="rev-bytes")
        batch_path = self.write_batch(
            self.plan_batch([inventory_entry("TASK-BYTES-001")], subjects=["unrelated"])
        )
        board = self.board_state()
        board["tasks"].append(
            {"id": "ODP-SNEAKED-IN-001", "status": "todo", "owner": "Claude", "reviewer": "Codex"}
        )
        self.board_path.write_text(
            json.dumps(board, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self.assertEqual("rev-bytes", self.board_state()["_status_write_revision"])

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(self.root / "c.json"), "--confirm")

        self.assertIn("board bytes moved", str(caught.exception))
        self.assertEqual(
            ["ODP-SNEAKED-IN-001"], [t["id"] for t in self.board_state()["tasks"]]
        )

    def test_evidence_ref_that_moved_after_planning_is_refused(self) -> None:
        """The batch is only valid against the commit it was graded on."""

        self.write_board(revision="rev-ref")
        repo = self.ensure_repo(["Merge pull request #100 from org/task/TASK-REFMOVE-001"])
        batch_path = self.write_batch(
            self.plan_batch(
                [
                    inventory_entry(
                        "TASK-REFMOVE-001",
                        merge_commit=self.local_merge_oid("TASK-REFMOVE-001"),
                    )
                ],
                attestations={"TASK-REFMOVE-001": attestation_set()},
            )
        )
        (repo / "later.txt").write_text("later", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "later work on the same ref")

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(self.root / "c.json"), "--confirm")

        self.assertIn("evidence ref moved", str(caught.exception))
        self.assertEqual([], sorted(self.archive_dir.glob("*.json")))

    def test_authorization_document_edited_after_planning_is_refused(self) -> None:
        """The scope the batch was approved under has to still say that."""

        self.write_board(revision="rev-auth")
        batch_path = self.write_batch(
            self.plan_batch([inventory_entry("TASK-AUTH-001")], subjects=["unrelated"])
        )
        self.authorization_file().write_text(
            "# 授權：改為允許無證據直接重建 done\n", encoding="utf-8"
        )

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(self.root / "c.json"), "--confirm")

        self.assertIn("authorization source drifted", str(caught.exception))
        self.assertEqual([], self.board_state()["tasks"])

    def test_inventory_edited_after_planning_is_refused(self) -> None:
        self.write_board(revision="rev-inv")
        batch_path = self.write_batch(
            self.plan_batch([inventory_entry("TASK-INV-001")], subjects=["unrelated"])
        )
        write_inventory(self.root / "inventory.json", [inventory_entry("TASK-INV-002")])

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(self.root / "c.json"), "--confirm")

        self.assertIn("inventory source drifted", str(caught.exception))

    def test_source_document_that_disappeared_is_refused(self) -> None:
        self.write_board(revision="rev-gone")
        batch_path = self.write_batch(
            self.plan_batch([inventory_entry("TASK-GONE-001")], subjects=["unrelated"])
        )
        self.authorization_file().unlink()

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(self.root / "c.json"), "--confirm")

        self.assertIn("authorization source is gone", str(caught.exception))

    def test_baseline_stripped_of_a_required_hash_is_refused(self) -> None:
        """Omitting a pinned input must not buy an unchecked baseline."""

        self.write_board(revision="rev-strip")
        batch = self.plan_batch([inventory_entry("TASK-STRIP-001")], subjects=["unrelated"])
        batch["baseline"].pop("board_sha256")
        batch_path = self.write_batch(batch)

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(self.root / "c.json"), "--confirm")

        self.assertIn("missing required provenance", str(caught.exception))
        self.assertIn("board_sha256", str(caught.exception))

    def test_reconstructed_done_whose_merge_is_not_on_the_ref_is_refused(self) -> None:
        """The one claim that creates terminal history is re-proved in the lock."""

        self.write_board(revision="rev-merge")
        self.ensure_repo(["Merge pull request #100 from org/task/TASK-MERGEPROOF-001"])
        batch = self.plan_batch(
            [
                inventory_entry(
                    "TASK-MERGEPROOF-001",
                    merge_commit=self.local_merge_oid("TASK-MERGEPROOF-001"),
                )
            ],
            attestations={"TASK-MERGEPROOF-001": attestation_set()},
        )
        self.assertEqual(planner.ACTION_ARCHIVE_DONE, batch["entries"][0]["action"])
        batch["entries"][0]["record"]["history_recovery"]["evidence"]["local_merge"][
            "merge_commit"
        ] = "0" * 40
        batch_path = self.write_batch(batch)

        with self.assertRaises(SystemExit) as caught:
            self.run_apply(batch_path, "--checkpoint", str(self.root / "c.json"), "--confirm")

        self.assertIn("is not contained in the pinned ref", str(caught.exception))
        self.assertEqual([], sorted(self.archive_dir.glob("*.json")))


# --- apply: the maintenance hold is evidence, not a string ----------------


class MaintenanceHoldTests(RecoveryFixture):
    def planned(self, task_id: str, **kwargs: Any) -> Path:
        self.write_board(revision=f"rev-{task_id.lower()}")
        return self.write_batch(
            self.plan_batch([inventory_entry(task_id)], subjects=["unrelated"], **kwargs)
        )

    def refuse_with(self, batch_path: Path, hold: Path) -> str:
        checkpoint = self.root / "c.json"
        with self.assertRaises(SystemExit) as caught:
            self.run_apply(
                batch_path, "--checkpoint", str(checkpoint), "--confirm", hold=hold
            )
        self.assertEqual([], self.board_state()["tasks"])
        self.assertFalse(checkpoint.exists())
        return str(caught.exception)

    def test_free_text_hold_reference_is_no_longer_accepted(self) -> None:
        """A string the applying worker types proves nothing about dispatch."""

        batch_path = self.planned("TASK-FREETEXT-001")
        message = self.refuse_with(
            batch_path, self.root / "ORCH-ARCHIVE-HISTORY-RECOVERY-001-hold-1"
        )
        self.assertIn("Maintenance hold unreadable", message)

    def test_hold_bound_to_a_different_batch_is_refused(self) -> None:
        """Otherwise a hold approved for a safer batch authorises this one."""

        batch_path = self.planned("TASK-OTHERHOLD-001")
        other = self.root / "other-batch.json"
        other.write_text("{}\n", encoding="utf-8")

        message = self.refuse_with(batch_path, self.write_hold(other))
        self.assertIn("is not this batch", message)

    def test_hold_that_has_expired_is_refused(self) -> None:
        batch_path = self.planned("TASK-EXPIRED-001")
        hold = self.write_hold(batch_path, expires_at="2026-09-06T00:00:00Z")

        self.assertIn("no longer held", self.refuse_with(batch_path, hold))

    def test_hold_that_does_not_assert_a_dispatch_pause_is_refused(self) -> None:
        batch_path = self.planned("TASK-NOPAUSE-001")
        hold = self.write_hold(batch_path, dispatch_paused=False)

        self.assertIn("dispatch_paused must be true", self.refuse_with(batch_path, hold))

    def test_hold_without_the_reviewer_approval_is_refused(self) -> None:
        batch_path = self.planned("TASK-NOAPPROVE-001")
        hold = self.write_hold(batch_path, batch_approval={})

        self.assertIn("carries no batch_approval", self.refuse_with(batch_path, hold))

    def test_approval_of_a_different_batch_hash_is_refused(self) -> None:
        batch_path = self.planned("TASK-OTHERHASH-001")
        digest = hashlib.sha256(batch_path.read_bytes()).hexdigest()
        hold = self.write_hold(
            batch_path,
            batch_approval={
                "reviewer": "Codex",
                "approved_batch_sha256": "e" * 64,
                "approved_at": "2026-09-06T15:30:00Z",
                "source": "https://example.invalid/review",
            },
        )
        self.assertNotEqual("e" * 64, digest)

        self.assertIn("approves a different batch", self.refuse_with(batch_path, hold))

    def test_approval_by_someone_other_than_the_batch_reviewer_is_refused(self) -> None:
        batch_path = self.planned("TASK-WRONGREVIEWER-001")
        hold = self.write_hold(
            batch_path,
            batch_approval={
                "reviewer": "Codex2",
                "approved_batch_sha256": hashlib.sha256(
                    batch_path.read_bytes()
                ).hexdigest(),
                "approved_at": "2026-09-06T15:30:00Z",
                "source": "https://example.invalid/review",
            },
        )

        self.assertIn("is not the batch reviewer", self.refuse_with(batch_path, hold))

    def test_the_applying_actor_cannot_also_be_the_approver(self) -> None:
        """Two roles, two people: the writer must not approve its own batch."""

        batch_path = self.planned(
            "TASK-SELFAPPROVE-001", owner="Codex", reviewer="Claude"
        )
        hold = self.write_hold(
            batch_path,
            batch_approval={
                "reviewer": "Claude",
                "approved_batch_sha256": hashlib.sha256(
                    batch_path.read_bytes()
                ).hexdigest(),
                "approved_at": "2026-09-06T15:30:00Z",
                "source": "https://example.invalid/review",
            },
        )

        self.assertIn(
            "cannot both approve and apply", self.refuse_with(batch_path, hold)
        )

    def test_the_hold_is_recorded_in_the_checkpoint(self) -> None:
        """The receipt has to name what authorised the write."""

        self.write_board(revision="rev-holdrecord")
        batch_path = self.write_batch(
            self.plan_batch([inventory_entry("TASK-HOLDREC-001")], subjects=["unrelated"])
        )
        hold = self.write_hold(batch_path)
        checkpoint = self.root / "checkpoint.json"

        self.assertEqual(
            0,
            self.run_apply(
                batch_path, "--checkpoint", str(checkpoint), "--confirm", hold=hold
            ),
        )

        receipt = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(str(hold), receipt["maintenance_hold"]["path"])
        self.assertEqual(self.digest(hold), receipt["maintenance_hold"]["sha256"])
        self.assertEqual("Codex", receipt["maintenance_hold"]["approved_by"])
        self.assertEqual(
            "ORCH-ARCHIVE-HISTORY-RECOVERY-001/hold-1",
            receipt["maintenance_hold"]["hold_id"],
        )


class NestedLockTests(unittest.TestCase):
    def test_apply_never_re_enters_the_canonical_status_lock(self) -> None:
        """`main()` already holds it; taking it again would deadlock on flock.

        The functional tests would hang rather than fail if this regressed, so
        the property is also asserted statically.
        """

        tree = ast.parse(Path(ai_status.__file__).read_text(encoding="utf-8"))
        target = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "command_archive_recovery_apply"
        )
        referenced = {node.id for node in ast.walk(target) if isinstance(node, ast.Name)}
        referenced |= {node.attr for node in ast.walk(target) if isinstance(node, ast.Attribute)}

        self.assertNotIn("status_write_transaction", referenced)
        self.assertNotIn("save_state", referenced)
        self.assertIn(
            "archive_recovery_apply", ai_status.MUTATING_COMMANDS
        )


class ImportOrderSentinelTests(unittest.TestCase):
    """Which root the modules bind is an import-order property.

    No assertion inside this module can observe it, because this module is not
    necessarily the first importer. The real ordering is therefore re-run in a
    separate interpreter against a throwaway root, and a pre-existing snapshot
    there has to come back byte-identical.
    """

    SENTINEL = """
import hashlib, json, os, sys
from pathlib import Path

root = Path(os.environ["ORCH_STATUS_ROOT"]).resolve()
snapshot = root / "ai-task-archive" / "tasks" / "SENTINEL-001.json"
before = hashlib.sha256(snapshot.read_bytes()).hexdigest()

sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))
sys.path.insert(0, str(Path(sys.argv[1]) / "scripts" / "orchestrator"))
import ai_status
import task_archive
import backfill_task_archive_snapshots as planner

result = {
    "ai_status_archive": str(ai_status.ARCHIVE_TASKS_DIR),
    "task_archive_archive": str(task_archive.ARCHIVE_TASKS_DIR),
    "planner_batch_type": planner.RECOVERY_BATCH_TYPE,
    "snapshot_unchanged": hashlib.sha256(snapshot.read_bytes()).hexdigest() == before,
}
print(json.dumps(result))
"""

    def test_status_roots_come_from_the_environment_at_import(self) -> None:
        with tempfile.TemporaryDirectory(prefix="recovery-sentinel-") as temp_dir:
            root = Path(temp_dir).resolve()
            tasks = root / "ai-task-archive" / "tasks"
            tasks.mkdir(parents=True)
            snapshot = tasks / "SENTINEL-001.json"
            snapshot.write_text('{"task_id": "SENTINEL-001"}\n', encoding="utf-8")
            script = root / "sentinel.py"
            script.write_text(self.SENTINEL, encoding="utf-8")

            env = dict(os.environ)
            env["ORCH_STATUS_ROOT"] = str(root)
            env["PANTHEON_STATUS_ROOT"] = str(root)
            env["ORCH_CONFIG_PATH"] = str(_TEST_CONFIG)
            env["PANTHEON_CONFIG_PATH"] = str(_TEST_CONFIG)
            completed = subprocess.run(
                [sys.executable, str(script), str(REPO_ROOT)],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertTrue(payload["snapshot_unchanged"])
            self.assertEqual(str(tasks), payload["ai_status_archive"])
            self.assertEqual(str(tasks), payload["task_archive_archive"])
            # A root under the checkout would mean the environment was ignored
            # and a real coordination tree was bound instead.
            self.assertFalse(payload["ai_status_archive"].startswith(str(REPO_ROOT)))
            self.assertEqual("task_history_recovery_batch", payload["planner_batch_type"])


if __name__ == "__main__":
    unittest.main()
