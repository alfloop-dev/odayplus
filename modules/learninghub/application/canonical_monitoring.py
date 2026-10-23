"""Selected canonical Forecast observations -> native drift -> atomic receipt.

This lane measures prediction-distribution drift, not mature forecast error or
release success. Sources, policy and the active release are re-read under the
existing release writer lock. Only saved selectors enter from HTTP. Generated
record-set identities name the manifests stored in source_provenance, not a
training DatasetSnapshot or a claimed external WORM acknowledgement.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from math import isfinite
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from modules.learninghub.application.forecast_monitoring_source import (
    CanonicalForecastMonitoringSource,
    CanonicalMonitoringUnavailable,
    ForecastMonitoringSources,
    _aware,
    _digest,
    _ids,
)
from modules.learninghub.domain.monitoring import (
    MonitoringBreach,
    MonitoringEvaluation,
    MonitoringSignalType,
)
from modules.learninghub.infrastructure.evidently_monitor import EvidentlyDriftMonitor
from shared.audit import AuditEvent
from shared.audit.durable_delivery import JOB_TYPE, DurableAuditDelivery
from shared.audit.integrity import verify_audit_event_integrity
from shared.audit.policy import build_security_event
from shared.auth import Action
from shared.auth.abac import AccessRequest, Decision, ResourceDescriptor
from shared.auth.identity import Principal
from shared.auth.rbac import rbac_allows
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.job_queue import DurableJobQueue

SCHEMA = "forecast.canonical-monitoring.v1"
EVENT_TYPE = "learninghub.canonical_prediction_drift.v1"


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _policy_facts(policy: Any) -> dict[str, Any]:
    # effective_to can be closed by legitimate later supersession. The version,
    # parameters and original approval are immutable; resolve again at assessed_at.
    return {key: _plain(value) for key, value in policy.to_dict().items() if key != "effective_to"}


def _provenance(sources: ForecastMonitoringSources, as_of: datetime) -> dict[str, Any]:
    manifest = _plain(sources.source_manifest)
    reference_ids = [row["forecast_output_id"] for row in manifest[:len(sources.reference)]]
    current_ids = [row["forecast_output_id"] for row in manifest[len(sources.reference):]]
    reference = manifest[:len(sources.reference)]
    current = manifest[len(sources.reference):]
    # These content-addressed manifests are stored in this same durable receipt.
    # They are explicitly not DatasetSnapshot registry identities.
    return {
        "schema": SCHEMA, "tenant_id": sources.tenant_id, "store_id": sources.store_id,
        "release_id": sources.release_id, "release_revision": sources.release_revision,
        "model_name": sources.model_name, "model_version": sources.model_version,
        "model_dataset_snapshot_id": sources.model_dataset_snapshot_id,
        "horizon_days": sources.horizon_days, "monitoring_window": sources.monitoring_window,
        "release_executed_at": sources.release_executed_at.isoformat(),
        "release_window_end": sources.release_window_end.isoformat(),
        "assessed_at": as_of.isoformat(), "reference_ids": reference_ids, "current_ids": current_ids,
        "reference_coverage": [value.isoformat() for value in sources.reference_coverage],
        "current_coverage": [value.isoformat() for value in sources.current_coverage],
        "reference_record_set_id": "forecast-record-set:" + _digest(reference),
        "current_record_set_id": "forecast-record-set:" + _digest(current),
        "source_manifest": manifest, "source_digest": sources.source_digest,
        "policy": _policy_facts(sources.decision_policy),
        "policy_digest": _digest(_policy_facts(sources.decision_policy)),
        "signal": "prediction_distribution_drift", "mature_error_available": False,
        "full_window_observed": False,
    }


class CanonicalForecastMonitoring:
    def __init__(self, *, source: CanonicalForecastMonitoringSource,
                 audit: DurableAuditLog, queue: DurableJobQueue,
                 clock: Callable[[], datetime] | None = None) -> None:
        if (
            not isinstance(source, CanonicalForecastMonitoringSource)
            or not isinstance(audit, DurableAuditLog)
            or not isinstance(queue, DurableJobQueue)
            or audit._engine is not source.engine or queue._engine is not source.engine
            or audit._worm_sink is None
        ):
            raise CanonicalMonitoringUnavailable("MONITORING_ATOMIC_OWNER_UNAVAILABLE")
        self.source, self.audit, self.queue, self.engine = source, audit, queue, source.engine
        self.clock = clock or (lambda: datetime.now(UTC))

    def _principal(self, principal: Principal, action: Action, release_id: str) -> None:
        if not isinstance(principal, Principal) or not principal.authenticated or not principal.tenant_id:
            raise CanonicalMonitoringUnavailable("MONITORING_CURRENT_PRINCIPAL_REQUIRED")
        if not rbac_allows(principal, "model", action):
            self.audit.record(build_security_event(AccessRequest(principal=principal, action=action,
                resource=ResourceDescriptor(type="model", resource_id=release_id,
                    tenant_id=principal.tenant_id)), Decision.deny("model action not granted", policy_id="rbac")))
            raise CanonicalMonitoringUnavailable("MONITORING_PUBLISH_FORBIDDEN" if action is Action.PUBLISH else "MONITORING_SOURCE_NOT_FOUND")

    @contextmanager
    def _transaction(self, model_name: str, tenant_id: str):
        with self.source.learning.release_recovery_guard(model_name):
            if getattr(self.engine, "dialect", None) != "postgresql" and not self.engine._conn.in_transaction:
                self.engine.execute("BEGIN IMMEDIATE")
            delivery = DurableAuditDelivery(self.engine, tenant_id, self.queue, self.audit._worm_sink)
            with self.audit.transactional_worm_sink(delivery):
                yield

    def evaluate(self, *, principal: Principal, release_id: str, store_id: str,
                 horizon_days: int, reference_ids: tuple[str, ...], current_ids: tuple[str, ...],
                 correlation_id: str) -> dict[str, Any]:
        self._principal(principal, Action.PUBLISH, release_id)
        if not isinstance(correlation_id, str) or not correlation_id.strip():
            raise CanonicalMonitoringUnavailable("MONITORING_CORRELATION_REQUIRED")
        at = self.clock()
        # Initial actual tenant/object admission supplies the lock key; all facts
        # are loaded again inside that existing native release transaction.
        initial = self.source.load(principal=principal, release_id=release_id, store_id=store_id,
            horizon_days=horizon_days, reference_ids=reference_ids, current_ids=current_ids, as_of=at)
        with self._transaction(initial.model_name, initial.tenant_id):
            sources = self.source.load(principal=principal, release_id=release_id, store_id=store_id,
                horizon_days=horizon_days, reference_ids=reference_ids, current_ids=current_ids, as_of=at)
            if _provenance(initial, at) != _provenance(sources, at):
                raise CanonicalMonitoringUnavailable("MONITORING_SOURCE_CHANGED")
            provenance = _provenance(sources, at)
            identity = _digest({"tenant_id": sources.tenant_id, "subject_id": principal.subject_id,
                "release_id": sources.release_id, "release_revision": sources.release_revision,
                "store_id": sources.store_id, "horizon_days": sources.horizon_days,
                "reference_ids": provenance["reference_ids"], "current_ids": provenance["current_ids"],
                "source_digest": sources.source_digest, "policy_digest": provenance["policy_digest"]})
            evaluation_id = str(uuid5(NAMESPACE_URL, "odp:canonical-forecast-monitoring:" + identity))
            existing = self.source.learning.get_monitoring_evaluation(evaluation_id)
            if existing is not None:
                # Same actual sources/policy/release/actor recover a lost response
                # without a second computation, AuditEvent or delivery intent.
                if not isinstance(existing, MonitoringEvaluation) or existing.requested_by != principal.subject_id:
                    raise CanonicalMonitoringUnavailable("MONITORING_RECEIPT_CONFLICT")
                return self._verified(existing, principal=principal, release_id=release_id)
            result = EvidentlyDriftMonitor().run_prediction(
                reference_rows=sources.reference, current_rows=sources.current,
                model_name=sources.model_name, model_version=sources.model_version,
                cohort_key=sources.cohort_key, prediction_columns=("prediction",),
                output_types={"prediction": "numeric"},
                reference_snapshot_id=provenance["reference_record_set_id"],
                current_snapshot_id=provenance["current_record_set_id"], policy=sources.decision_policy)
            if (type(result.drift_share) not in (float, int) or not isfinite(result.drift_share)
                or not 0 <= result.drift_share <= 1 or type(result.drift_detected) is not bool
                or result.reference_snapshot_id != provenance["reference_record_set_id"]
                or result.current_snapshot_id != provenance["current_record_set_id"]
                or result.model_name != sources.model_name or result.model_version != sources.model_version
                or result.cohort_key != sources.cohort_key
                or result.decision_policy_version_id != sources.decision_policy.policy_version_id):
                raise CanonicalMonitoringUnavailable("MONITORING_NATIVE_RESULT_INVALID")
            fresh = self.source.load(principal=principal, release_id=release_id, store_id=store_id,
                horizon_days=horizon_days, reference_ids=reference_ids, current_ids=current_ids, as_of=at)
            if _provenance(fresh, at) != provenance:
                raise CanonicalMonitoringUnavailable("MONITORING_SOURCE_CHANGED")
            audit_id = str(uuid4())
            evaluation = MonitoringEvaluation(
                evaluation_id=evaluation_id, model_name=sources.model_name,
                model_version=sources.model_version, dataset_snapshot_id=sources.model_dataset_snapshot_id,
                signal_type=MonitoringSignalType.PREDICTION_DRIFT,
                observed_metrics={"prediction_drift_share": result.drift_share}, baseline_metrics={},
                breaches=(MonitoringBreach("prediction_drift_share", result.drift_share,
                    "Prediction distribution drift exceeded the recorded DecisionPolicy threshold", "FAILED"),)
                    if result.drift_detected else (),
                requested_by=principal.subject_id, created_at=at,
                decision_policy_version_id=sources.decision_policy.policy_version_id,
                reference_snapshot_id=result.reference_snapshot_id, current_snapshot_id=result.current_snapshot_id,
                cohort_key=result.cohort_key, prediction_columns=result.prediction_columns,
                prediction_output_types=result.prediction_output_types or {}, drift_detected=result.drift_detected,
                drifted_columns=result.drifted_column_names, drift_report_json=result.report_json,
                drift_engine=result.engine, audit_event_id=audit_id, source_provenance=provenance)
            self.source.learning.save_monitoring_evaluation(evaluation)
            self.audit.record(AuditEvent(event_id=audit_id, occurred_at=at, event_type=EVENT_TYPE,
                actor=principal.subject_id, action="monitor_prediction_drift",
                resource=f"model/{sources.model_name}:{sources.model_version}",
                outcome="drift_detected" if result.drift_detected else "drift_not_detected",
                correlation_id=correlation_id, metadata={"tenant_id": sources.tenant_id,
                    "evaluation_id": evaluation_id, "release_id": release_id,
                    "release_revision": sources.release_revision,
                    "evaluation_sha256": _digest(evaluation.to_dict())}))
            return self._public(evaluation)

    def _public(self, evaluation: MonitoringEvaluation) -> dict[str, Any]:
        data = evaluation.to_dict()
        data["recommended_action"] = "REVIEW_ROLLBACK" if evaluation.drift_detected else "CONTINUE_OBSERVING"
        data["audit_delivery"] = "pending_or_delivered_separately"
        return data

    def _verified(self, evaluation: MonitoringEvaluation, *, principal: Principal, release_id: str) -> dict[str, Any]:
        provenance = getattr(evaluation, "source_provenance", None)
        if not isinstance(provenance, dict) or provenance.get("schema") != SCHEMA:
            raise CanonicalMonitoringUnavailable("MONITORING_PROVENANCE_UNAVAILABLE")
        # Tenant ownership precedes source/policy/audit reads; a global model
        # repository must not expose another tenant's evaluation as unavailable.
        if provenance.get("tenant_id") != principal.tenant_id or provenance.get("release_id") != release_id:
            raise CanonicalMonitoringUnavailable("MONITORING_SOURCE_NOT_FOUND")
        try:
            at = datetime.fromisoformat(provenance["assessed_at"])
            reference_ids, current_ids = _ids(provenance["reference_ids"]), _ids(provenance["current_ids"])
            sources = self.source.load(principal=principal, release_id=release_id,
                store_id=provenance["store_id"], horizon_days=provenance["horizon_days"],
                reference_ids=reference_ids, current_ids=current_ids, as_of=at)
            if not _aware(at) or _provenance(sources, at) != provenance:
                raise CanonicalMonitoringUnavailable("MONITORING_PROVENANCE_CHANGED")
            event = self.audit.get_event(evaluation.audit_event_id, tenant_id=principal.tenant_id)
            expected = {"tenant_id": principal.tenant_id, "evaluation_id": evaluation.evaluation_id,
                "audit_delivery_job_type": JOB_TYPE,
                "release_id": release_id, "release_revision": sources.release_revision,
                "evaluation_sha256": _digest(evaluation.to_dict())}
            if (evaluation.signal_type is not MonitoringSignalType.PREDICTION_DRIFT
                or evaluation.model_name != sources.model_name or evaluation.model_version != sources.model_version
                or evaluation.dataset_snapshot_id != sources.model_dataset_snapshot_id
                or evaluation.reference_snapshot_id != provenance["reference_record_set_id"]
                or evaluation.current_snapshot_id != provenance["current_record_set_id"]
                or evaluation.cohort_key != sources.cohort_key
                or evaluation.decision_policy_version_id != sources.decision_policy.policy_version_id
                or evaluation.created_at != at or event is None or not verify_audit_event_integrity(event)
                or event.event_id != evaluation.audit_event_id or event.event_type != EVENT_TYPE
                or event.metadata != expected or event.actor != evaluation.requested_by
                or event.action != "monitor_prediction_drift"
                or event.resource != f"model/{sources.model_name}:{sources.model_version}"
                or event.outcome != ("drift_detected" if evaluation.drift_detected else "drift_not_detected")
                or event.occurred_at != at):
                raise CanonicalMonitoringUnavailable("MONITORING_EVALUATION_INTEGRITY_INVALID")
            json.loads(evaluation.drift_report_json)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            if isinstance(exc, CanonicalMonitoringUnavailable):
                raise
            raise CanonicalMonitoringUnavailable("MONITORING_EVALUATION_INVALID") from exc
        return self._public(evaluation)

    def read(self, *, principal: Principal, release_id: str) -> dict[str, Any]:
        self._principal(principal, Action.VIEW, release_id)
        release, _ = self.source._release(release_id)
        items, unavailable = [], 0
        for evaluation in self.source.learning.list_monitoring_evaluations(release.model_name):
            provenance = getattr(evaluation, "source_provenance", None)
            if (not isinstance(provenance, Mapping) or provenance.get("tenant_id") != principal.tenant_id
                or provenance.get("release_id") != release_id):
                continue
            try:
                items.append(self._verified(evaluation, principal=principal, release_id=release_id))
            except CanonicalMonitoringUnavailable:
                unavailable += 1
        return {"release_id": release_id, "state": "available" if items else "unavailable", "items": items,
            "unavailable_count": unavailable,
            "reason": None if items else "CANONICAL_MONITORING_EVALUATION_UNAVAILABLE"}
