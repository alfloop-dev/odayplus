"""Actual tenant NetPlan collection tasks and descriptive manual labels.

User rule confirmed 2026-10-16: approval registers 90/180/365-day tasks;
actual execution starts their calendar-day windows. Due is not measured or
mature. No POS margin, settlement rule or training eligibility is inferred.
"""
from __future__ import annotations

import hashlib
import json
import math
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException

from modules.netplan.domain.planning import ApprovalRecord, ExecutionRecord, OutcomeRecord
from shared.audit.events import AuditEvent
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.jobs.queue import JobRequest

HORIZON_DAYS = (90, 180, 365)
TASK_COLLECTION = "netplan.outcome_tracking"
LABEL_COLLECTION = "netplan.observed_labels"
RECEIPT_COLLECTION = "netplan.outcome_tracking_receipts"
PROGRESS_COLLECTION = "netplan.outcome_tracking_progress"
DUE_JOB_TYPE = "netplan-outcome-due"


def content_hash(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(503, "NETPLAN_OUTCOME_SOURCE_INVALID") from exc
    return hashlib.sha256(raw).hexdigest()


def aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(503, "NETPLAN_OUTCOME_SOURCE_TIME_UNAVAILABLE")
    return value


def retained_collection_progress(documents, audit, task):
    """Read immutable source-bound progress without rewriting later facts."""
    value = documents.get(PROGRESS_COLLECTION, task["task_id"])
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get("task_source_sha256") != content_hash(task)
            or any(value.get(key) != task[key] for key in ("tenant_id", "scenario_id", "task_id"))
            or value.get("job_id") != task.get("collection_job_id")
            or type(value.get("audit_event_id")) is not str):
        raise HTTPException(503, "NETPLAN_OUTCOME_PROGRESS_SOURCE_CHANGED")
    event = audit.get_event(value["audit_event_id"], tenant_id=task["tenant_id"])
    source = {key: item for key, item in value.items() if key != "audit_event_id"}
    if (event is None or event.event_type != "netplan.outcome_collection_due.v1"
            or event.actor != "netplan-outcome-worker" or event.action != "mark_collection_due"
            or event.resource != "netplan/scenarios/" + task["scenario_id"]
            or event.metadata.get("task_id") != task["task_id"]
            or event.metadata.get("progress_sha256") != content_hash(source)):
        raise HTTPException(503, "NETPLAN_OUTCOME_PROGRESS_AUDIT_UNAVAILABLE")
    return value


class DurableNetPlanOutcomeRegistry:
    """A request-bound registry, never an InterventionOps label adapter.

    Authorize is the existing canonical builder's current object authorization,
    called again with the exact scenario reloaded after its real owner lock.
    The supplied principal/audit/queue are server owners, not body fields.
    """

    def __init__(self, *, repository, principal, authorize, audit_log, queue, correlation_id):
        self.repository = repository
        self.documents = getattr(repository, "_store", None)
        self.principal = principal
        self.authorize = authorize
        self.audit = audit_log
        self.queue = queue
        self.correlation_id = correlation_id
        self.engine = getattr(self.documents, "engine", None)
        if (not principal.authenticated or not principal.tenant_id
                or not isinstance(self.documents, TenantScopedDocumentStore)
                or self.documents.tenant_id != principal.tenant_id or not callable(authorize)
                or not isinstance(audit_log, DurableAuditLog) or audit_log._engine is not self.engine
                or not isinstance(queue, DurableJobQueue) or queue._engine is not self.engine):
            raise HTTPException(503, "NETPLAN_OUTCOME_OWNER_UNAVAILABLE")

    @contextmanager
    def _transaction(self, scenario_id):
        with self.repository.scenario_transaction(scenario_id):
            sink = getattr(self.audit, "_worm_sink", None)
            if sink is None:
                yield
            else:
                from shared.audit.durable_delivery import DurableAuditDelivery
                with self.audit.transactional_worm_sink(DurableAuditDelivery(
                        self.engine, self.principal.tenant_id, self.queue, sink)):
                    yield

    def _row(self, collection, identity, value, group):
        snapshot = self.documents.get_versioned(collection, identity)
        previous, version = snapshot if snapshot is not None else (None, None)
        if previous == value:
            return False
        if previous is None:
            if not self.documents.put_if_absent(collection, identity, value, group_key=group):
                raise HTTPException(409, "NETPLAN_OUTCOME_REGISTRY_VERSION_CONFLICT")
        elif self.documents.compare_and_put(collection, identity, value,
                expected_version=version) is None:
            raise HTTPException(409, "NETPLAN_OUTCOME_REGISTRY_VERSION_CONFLICT")
        return True

    def _schedule(self, row):
        """Reserve the actual due job inside the descriptor's SQL transaction.

        The existing queue's available_at predicates govern lease and claim.
        This schedules a collection-due notice, never a measured label.
        """
        payload = {key: row[key] for key in (
            "tenant_id", "scenario_id", "task_id", "execution_id", "horizon_days", "due_at")}
        payload["task_source_sha256"] = content_hash(row)
        record, created = self.queue.enqueue(JobRequest(job_type=DUE_JOB_TYPE, payload=payload,
            idempotency_key="netplan-outcome-due:" + row["collection_job_id"],
            job_id=row["collection_job_id"]), correlation_id=self.correlation_id)
        if (record.job_id != row["collection_job_id"] or record.job_type != DUE_JOB_TYPE
                or record.payload != payload):
            raise HTTPException(503, "NETPLAN_OUTCOME_JOB_SOURCE_CHANGED")
        if created:
            updated = self.engine.execute(
                "UPDATE durable_jobs SET available_at = ? WHERE job_id = ? "
                "AND job_type = ? AND available_at IS NULL",
                (row["due_at"], record.job_id, DUE_JOB_TYPE))
            if updated.rowcount != 1:
                raise HTTPException(503, "NETPLAN_OUTCOME_JOB_SCHEDULE_UNAVAILABLE")
        stored = self.engine.query_one("SELECT available_at FROM durable_jobs WHERE job_id = ?", (record.job_id,))
        if (stored is None or stored["available_at"] is None
                or aware(datetime.fromisoformat(str(stored["available_at"]).replace("Z", "+00:00")))
                != aware(datetime.fromisoformat(row["due_at"]))):
            raise HTTPException(503, "NETPLAN_OUTCOME_JOB_SCHEDULE_CHANGED")

    def reconcile(self, scenario_id, *, expected_scenario=None):
        """Idempotently retain only actual verified approval/execution facts.

        Also supports the explicitly authorized immutable backfill of an older
        canonical approval. No history, approval, execution or outcome is made.
        The single native manual outcome is never assigned to all three tasks.
        """
        with self._transaction(scenario_id):
            scenario = self.repository.get_scenario(scenario_id)
            if scenario is None or scenario.tenant_id != self.principal.tenant_id:
                raise HTTPException(404, "NETPLAN_SCENARIO_NOT_FOUND")
            # Existing scope/ABAC includes actual masters/candidates. This runs
            # before any registry projection or reconciliation write.
            self.authorize(scenario)
            if expected_scenario is not None and scenario != expected_scenario:
                raise HTTPException(409, "NETPLAN_OUTCOME_SCENARIO_CHANGED")
            approvals = [row for row in self.repository.list_approvals(scenario_id)
                         if isinstance(row, ApprovalRecord) and row.authentic_approval_verified
                         and row.scenario_id == scenario_id]
            if not approvals:
                return {"state": "unavailable", "reasonCode": "NETPLAN_VERIFIED_APPROVAL_NOT_RECORDED",
                        "tasks": [], "labels": [], "receipt": None}
            if len(approvals) != 1:
                raise HTTPException(503, "NETPLAN_OUTCOME_APPROVAL_AMBIGUOUS")
            approval = approvals[0]
            solve = self.repository.get_solve(scenario_id)
            signature = tuple(sorted(approval.selected_action_signature))
            if (not signature or not approval.selected_candidate_id or not approval.solver_problem_hash
                    or scenario.selected_candidate_id != approval.selected_candidate_id
                    or solve is None or solve.problem_hash != approval.solver_problem_hash
                    or solve.is_stale(scenario)):
                return {"state": "unavailable", "reasonCode": "NETPLAN_OUTCOME_APPROVAL_BINDING_UNAVAILABLE",
                        "tasks": [], "labels": [], "receipt": None}
            from modules.netplan.application.planning import NetPlanService
            subject = NetPlanService._selected_approval_subject(scenario, solve)
            if subject.candidate_id != approval.selected_candidate_id or subject.action_signature != signature:
                raise HTTPException(503, "NETPLAN_OUTCOME_SELECTED_SUBJECT_CHANGED")
            execution = self.repository.get_execution(scenario_id)
            if execution is not None and (not isinstance(execution, ExecutionRecord)
                    or execution.scenario_id != scenario_id
                    or tuple(execution.actions) != subject.actions):
                raise HTTPException(503, "NETPLAN_OUTCOME_EXECUTION_BINDING_UNAVAILABLE")
            outcome = self.repository.get_outcome(scenario_id)
            if outcome is not None and (not isinstance(outcome, OutcomeRecord)
                    or execution is None or outcome.scenario_id != scenario_id):
                raise HTTPException(503, "NETPLAN_OUTCOME_LABEL_BINDING_UNAVAILABLE")
            basis = {"tenant_id": self.principal.tenant_id, "scenario_id": scenario_id,
                     "approval_id": approval.approval_id, "approval_source_sha256": content_hash(approval.to_dict()),
                     "selected_candidate_id": approval.selected_candidate_id,
                     "selected_action_signature": [list(item) for item in signature],
                     "solver_problem_hash": approval.solver_problem_hash,
                     "approved_at": aware(approval.decided_at).isoformat()}
            tasks = []
            changed = False
            for days in HORIZON_DAYS:
                identity = str(uuid5(NAMESPACE_URL, f"netplan-outcome:{basis['tenant_id']}:{scenario_id}:{approval.approval_id}:{days}"))
                row = {**basis, "task_id": identity, "horizon_days": days,
                       "anchor_rule": "actual_execution_calendar_days", "execution_id": None,
                       "executed_at": None, "due_at": None, "status": "PENDING_EXECUTION",
                       "collection_job_id": None,
                       "collected_outcome_id": None, "measured_value": None,
                       "mature": False, "training_eligible": False}
                if execution is not None:
                    anchor = aware(execution.executed_at)
                    row.update(execution_id=execution.execution_id, executed_at=anchor.isoformat(),
                               due_at=(anchor + timedelta(days=days)).isoformat(), status="SCHEDULED",
                               collection_job_id=str(uuid5(NAMESPACE_URL, f"netplan-outcome-due:{identity}:{execution.execution_id}")))
                previous = self.documents.get(TASK_COLLECTION, identity)
                if previous is not None:
                    immutable = [*basis, "task_id", "horizon_days", "anchor_rule"]
                    if any(previous.get(key) != row[key] for key in immutable):
                        raise HTTPException(503, "NETPLAN_OUTCOME_TASK_SOURCE_CHANGED")
                    if previous.get("execution_id") is not None and any(previous.get(key) != row[key]
                            for key in ("execution_id", "executed_at", "due_at")):
                        raise HTTPException(503, "NETPLAN_OUTCOME_TASK_EXECUTION_CHANGED")
                changed = self._row(TASK_COLLECTION, identity, row, scenario_id) or changed
                if execution is not None:
                    self._schedule(row)
                tasks.append(row)
            labels = []
            if outcome is not None:
                numbers = (outcome.expected_gross_margin, outcome.actual_gross_margin, outcome.variance, outcome.variance_pct)
                try:
                    valid_numbers = all(type(value) in (int, float) and math.isfinite(value) for value in numbers)
                except OverflowError:
                    valid_numbers = False
                if not valid_numbers:
                    raise HTTPException(503, "NETPLAN_OUTCOME_LABEL_SOURCE_INVALID")
                selected_expected = round(sum(action.expected_gross_margin for action in execution.actions), 4)
                payload = outcome.label_registry_payload
                if (outcome.expected_gross_margin != selected_expected or not isinstance(payload, dict)
                        or payload.get("entity_id") != scenario_id
                        or payload.get("actual_gross_margin") != outcome.actual_gross_margin
                        or payload.get("expected_gross_margin") != selected_expected
                        or payload.get("label_type") != "netplan_realized_gross_margin"
                        or payload.get("solver_version") != solve.result.solver_version
                        or outcome.variance != round(outcome.actual_gross_margin - selected_expected, 4)
                        or outcome.variance_pct != (round(outcome.variance / selected_expected, 6) if selected_expected else 0.0)):
                    raise HTTPException(503, "NETPLAN_OUTCOME_LABEL_BINDING_UNAVAILABLE")
                label = {**basis, "label_id": str(uuid5(NAMESPACE_URL, f"netplan-manual-label:{basis['tenant_id']}:{outcome.outcome_id}")),
                         "outcome_id": outcome.outcome_id, "execution_id": execution.execution_id,
                         "observed_at": aware(outcome.observed_at).isoformat(),
                         "actual_gross_margin": outcome.actual_gross_margin,
                         "expected_gross_margin": outcome.expected_gross_margin,
                         "variance": outcome.variance, "variance_pct": outcome.variance_pct,
                         "source_snapshot_ids": list(outcome.source_snapshot_ids),
                         "native_label_registry_payload": payload, "source_sha256": content_hash(outcome.to_dict()),
                         "measurement_method": "manual_recorded", "status": "OBSERVED", "horizon_days": None,
                         "outcome_window": None, "label_maturity_time": None,
                         "mature": False, "training_eligible": False, "evidence_level": None}
                previous = self.documents.get(LABEL_COLLECTION, label["label_id"])
                if previous is not None and previous != label:
                    raise HTTPException(503, "NETPLAN_OBSERVED_LABEL_IMMUTABLE_SOURCE_CHANGED")
                changed = self._row(LABEL_COLLECTION, label["label_id"], label, scenario_id) or changed
                labels.append(label)
            fingerprint = content_hash({"tasks": tasks, "labels": labels})
            receipt_id = str(uuid5(NAMESPACE_URL, f"netplan-tracking-receipt:{basis['tenant_id']}:{fingerprint}"))
            receipt = self.documents.get(RECEIPT_COLLECTION, receipt_id)
            if changed:
                event = self.audit.record(AuditEvent(
                    event_type="netplan.outcome_tracking_recorded.v1", actor=self.principal.subject_id,
                    action="record_outcome_tracking", resource=f"netplan/scenarios/{scenario_id}",
                    outcome="recorded", correlation_id=self.correlation_id,
                    metadata={"tenant_id": self.principal.tenant_id, "approval_id": approval.approval_id,
                              "execution_id": execution.execution_id if execution else None,
                              "outcome_id": outcome.outcome_id if outcome else None,
                              "task_ids": [row["task_id"] for row in tasks], "label_ids": [row["label_id"] for row in labels],
                              "source_sha256": fingerprint, "anchor_rule": "actual_execution_calendar_days"}))
                receipt = {"receipt_id": receipt_id, "tenant_id": self.principal.tenant_id,
                           "actor": self.principal.subject_id, "correlation_id": self.correlation_id,
                           "audit_event_id": event.event_id, "source_sha256": fingerprint,
                           "delivery_status": "pending" if getattr(self.audit, "_worm_sink", None) is not None else "not_configured"}
                if not self.documents.put_if_absent(RECEIPT_COLLECTION, receipt_id, receipt, group_key=scenario_id):
                    raise HTTPException(409, "NETPLAN_OUTCOME_REGISTRY_RECEIPT_CONFLICT")
            if receipt is None:
                raise HTTPException(503, "NETPLAN_OUTCOME_REGISTRY_RECEIPT_UNAVAILABLE")
            progress = []
            for task in tasks:
                value = retained_collection_progress(self.documents, self.audit, task)
                if value is not None:
                    # Independent immutable progress is joined, never rebuilt
                    # from the descriptor or overwritten by a later read.
                    progress.append(value)
            return {"state": "recorded", "reasonCode": None, "tasks": tasks, "labels": labels, "receipt": receipt,
                    "collectionProgress": progress,
                    "automatic_collection": {"available": False, "reasonCode": "NETPLAN_REALIZED_MARGIN_PROVIDER_NOT_BOUND"},
                    "maturity": {"available": False, "reasonCode": "NETPLAN_LABEL_MATURITY_DEFINITION_NOT_BOUND"}}
