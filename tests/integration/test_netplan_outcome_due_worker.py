"""Real owned SQL/queue clock controls, not elapsed business observation.

Clock movement only exercises available_at and durable due notifications.
No POS measurement, label maturity or external archive completion is inferred.
"""
from dataclasses import replace
from datetime import datetime, timedelta
from types import MethodType, SimpleNamespace

import pytest

from apps.worker.oday_worker.handlers import build_default_registry
from modules.netplan.workers import outcome_worker
from shared.infrastructure.persistence import job_queue as queue_module
from shared.infrastructure.persistence.factory import PersistenceBundle
from shared.infrastructure.persistence.netplan_outcomes import (
    DUE_JOB_TYPE,
    PROGRESS_COLLECTION,
    TASK_COLLECTION,
)
from tests.integration.test_netplan_constraint_disclosure_approval import SCENARIO_ID
from tests.integration.test_netplan_outcome_tracking import approve, owner


def persistence(engine, queue, audit, repository):
    # Small engineering container uses the real bundle's original scoped
    # provider method and real owners, not a replacement repository/queue.
    value = SimpleNamespace(engine=engine, job_queue=queue, audit_log=audit, netplan_repository=repository)
    value._scoped_repository = MethodType(PersistenceBundle._scoped_repository, value)
    return value


def move_queue_clock(monkeypatch, moment):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is not None else moment.replace(tzinfo=None)
    monkeypatch.setattr(queue_module, "datetime", Clock)
    monkeypatch.setattr(outcome_worker, "utc_now", lambda: moment)


def due_events(audit):
    return [event for event in audit.list_events() if event.event_type == "netplan.outcome_collection_due.v1"]


def test_three_future_jobs_use_real_available_at_and_due_read_replay_is_monotonic(tmp_path, monkeypatch):
    with owner(tmp_path / "due.sqlite", monkeypatch) as (engine, documents, repository, native, tracking, audit, _):
        approve(native)
        assert engine.query("SELECT job_id FROM durable_jobs WHERE job_type = ?", (DUE_JOB_TYPE,)) == []
        execution = native.execute(SCENARIO_ID)
        initial = tracking.reconcile(SCENARIO_ID)
        stored = engine.query("SELECT job_id, available_at FROM durable_jobs WHERE job_type = ?", (DUE_JOB_TYPE,))
        assert len(stored) == 3
        assert {row["job_id"] for row in stored} == {row["collection_job_id"] for row in initial["tasks"]}
        assert {row["available_at"] for row in stored} == {row["due_at"] for row in initial["tasks"]}
        assert tracking.queue.claim_next("original-due-worker") is None
        move_queue_clock(monkeypatch, execution.executed_at + timedelta(days=366))
        claimed = tracking.queue.claim_next("original-due-worker")
        assert claimed is not None and claimed.job_type == DUE_JOB_TYPE
        bundle = persistence(engine, tracking.queue, audit, repository)
        build_default_registry().handle(claimed, bundle)
        progress, version = documents.get_versioned(PROGRESS_COLLECTION, claimed.payload["task_id"])
        assert progress["state"] == "DUE_SOURCE_UNAVAILABLE" and progress["measured_value"] is None
        assert progress["collected_outcome_id"] is None and not progress["mature"] and not progress["training_eligible"]
        assert len(due_events(audit)) == 1
        descriptor = documents.get(TASK_COLLECTION, claimed.payload["task_id"])
        reread = tracking.reconcile(SCENARIO_ID)
        assert reread["collectionProgress"] == [progress]
        assert documents.get_versioned(PROGRESS_COLLECTION, claimed.payload["task_id"]) == (progress, version)
        assert documents.get(TASK_COLLECTION, claimed.payload["task_id"]) == descriptor
        build_default_registry().handle(claimed, bundle)
        assert documents.get_versioned(PROGRESS_COLLECTION, claimed.payload["task_id"]) == (progress, version)
        assert len(due_events(audit)) == 1


def test_due_audit_failure_rolls_back_then_actual_retry_keeps_single_progress(tmp_path, monkeypatch):
    with owner(tmp_path / "audit.sqlite", monkeypatch) as (engine, documents, repository, native, tracking, audit, _):
        approve(native)
        execution = native.execute(SCENARIO_ID)
        move_queue_clock(monkeypatch, execution.executed_at + timedelta(days=366))
        claimed = tracking.queue.claim_next("audit-failure-worker")
        assert claimed is not None
        bundle = persistence(engine, tracking.queue, audit, repository)
        original = audit.record
        def fail(event):
            if event.event_type == "netplan.outcome_collection_due.v1":
                raise RuntimeError("declared due audit failure")
            return original(event)
        monkeypatch.setattr(audit, "record", fail)
        with pytest.raises(RuntimeError, match="declared due audit failure"):
            build_default_registry().handle(claimed, bundle)
        assert documents.list_all(PROGRESS_COLLECTION) == [] and due_events(audit) == []
        monkeypatch.setattr(audit, "record", original)
        build_default_registry().handle(claimed, bundle)
        assert len(documents.list_all(PROGRESS_COLLECTION)) == len(due_events(audit)) == 1


def test_changed_job_payload_and_stale_fence_cannot_create_due_or_measured_facts(tmp_path, monkeypatch):
    with owner(tmp_path / "fence.sqlite", monkeypatch) as (engine, documents, repository, native, tracking, audit, _):
        approve(native)
        execution = native.execute(SCENARIO_ID)
        move_queue_clock(monkeypatch, execution.executed_at + timedelta(days=366))
        claimed = tracking.queue.claim_next("fenced-due-worker")
        assert claimed is not None
        bundle = persistence(engine, tracking.queue, audit, repository)
        with pytest.raises(RuntimeError, match="lease ownership changed"):
            build_default_registry().handle(replace(claimed, fence_token=claimed.fence_token + 1), bundle)
        with pytest.raises(RuntimeError, match="lease ownership changed"):
            build_default_registry().handle(replace(claimed, payload={**claimed.payload, "tenant_id": "foreign-tenant"}), bundle)
        assert documents.list_all(PROGRESS_COLLECTION) == [] and due_events(audit) == []
        assert all(row["measured_value"] is None and not row["mature"] for row in documents.list_all(TASK_COLLECTION))
