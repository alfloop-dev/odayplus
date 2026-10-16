"""Current scoped diagnostic reader over recorded Forecast facts only.

No request supplies values, policy versions, source snapshots or a model.
Original source IDs are references, not independently verified raw bytes or
observations. All eleven cause families remain unknown until Stage C exists.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

from modules.forecastops.domain.root_cause import (
    DiagnosticEvidenceDenied,
    DiagnosticEvidenceEnvelope,
    DiagnosticEvidenceUnavailable,
    RecordedDiagnosticFact,
)
from modules.forecastops.infrastructure.root_cause_repository import (
    DurableDiagnosticEvidenceRepository,
)
from modules.opsboard.application.store_ops import DurableStoreOpsRepository
from modules.opsboard.application.store_ops_forecast import current_issue_forecast
from shared.audit import AuditEvent
from shared.audit.policy import build_security_event
from shared.auth import (
    AccessRequest,
    Action,
    AuthorizationEngine,
    Decision,
    Environment,
    Principal,
    ResourceDescriptor,
)
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.repositories import (
    DurableForecastOpsRepository,
    DurableStoreRepository,
)


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise DiagnosticEvidenceUnavailable("Recorded diagnostic timestamp must be aware")
    return result


class ScopedDiagnosticEvidenceReader:
    def __init__(self, *, engine, forecasts, issues, stores, evidence, audit) -> None:
        if (engine is None or not isinstance(forecasts, DurableForecastOpsRepository)
                or not isinstance(issues, DurableStoreOpsRepository)
                or not isinstance(stores, DurableStoreRepository)
                or not isinstance(evidence, DurableDiagnosticEvidenceRepository)
                or not isinstance(audit, DurableAuditLog)
                or any(owner is not engine for owner in (
                    forecasts._store.engine, issues._store.engine, stores._engine,
                    evidence.engine, audit._engine,
                ))):
            raise DiagnosticEvidenceUnavailable("Diagnostic repositories require the same SQL owner")
        self.engine, self.forecasts, self.issues = engine, forecasts, issues
        self.stores, self.evidence, self.audit = stores, evidence, audit

    @contextmanager
    def _transaction(self, principal, resource_id: str, correlation_id: str):
        try:
            with self.engine.transaction():
                yield
        except DiagnosticEvidenceDenied:
            # AuthorizationEngine's in-transaction denial was rolled back.
            # Retain the real denied attempt after rollback, with no source data.
            if isinstance(principal, Principal):
                self.audit.record(build_security_event(AccessRequest(
                    principal=principal, action=Action.VIEW,
                    resource=ResourceDescriptor(type="operator_console", resource_id=resource_id,
                                                tenant_id=principal.tenant_id, module="operator_console"),
                    environment=Environment(attributes={"correlation_id": correlation_id}),
                ), Decision.deny("Current diagnostic scope is denied")))
            raise

    def _issue(self, principal: Principal, issue_id: str, correlation_id: str):
        if (not isinstance(principal, Principal) or not principal.authenticated
                or not principal.subject_id.strip() or not principal.tenant_id
                or not isinstance(issue_id, str) or not issue_id.strip()
                or not isinstance(correlation_id, str) or not correlation_id.strip()):
            raise DiagnosticEvidenceDenied("Authenticated diagnostic scope is required")
        # No canonical Store-to-area/zone owner is available to prove these
        # explicit restrictions. Default ABAC does not inspect either axis.
        if principal.scope.assigned_area_ids or principal.scope.heat_zone_ids:
            raise DiagnosticEvidenceDenied("Diagnostic area or heat-zone scope cannot be established")
        # The public versioned read does not seed the legacy POC aggregate.
        state, version = self.issues.for_tenant(principal.tenant_id).read_versioned_state()
        if not isinstance(state, dict) or type(version) is not int or version < 1:
            raise DiagnosticEvidenceUnavailable("Diagnostic workflow source is not materialized")
        rows = state.get("issues")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise DiagnosticEvidenceUnavailable("Diagnostic workflow source is invalid")
        matches = [row for row in rows if row.get("id") == issue_id]
        if len(matches) != 1 or not isinstance(matches[0].get("storeId"), str):
            raise DiagnosticEvidenceDenied("Diagnostic issue is not available")
        issue = matches[0]
        store = self.stores.get_store(issue["storeId"])
        if store is None or store.tenant_id != principal.tenant_id or not store.is_current:
            raise DiagnosticEvidenceDenied("Diagnostic issue is not available")
        access = AccessRequest(
            principal=principal, action=Action.VIEW,
            resource=ResourceDescriptor(
                type="operator_console", resource_id=issue_id, module="operator_console",
                tenant_id=store.tenant_id, store_id=store.store_id,
                brand_id=store.brand_id, region_id=store.region_code,
            ),
            environment=Environment(attributes={"correlation_id": correlation_id}),
        )
        if not AuthorizationEngine(audit_log=self.audit).authorize(access).allowed:
            raise DiagnosticEvidenceDenied("Current diagnostic issue scope is denied")
        return issue, version

    def _recorded(self, principal: Principal, issue_id: str, correlation_id: str):
        issue, version = self._issue(principal, issue_id, correlation_id)
        projection = current_issue_forecast(
            issue=issue, tenant_id=principal.tenant_id,
            repository=self.forecasts, engine=self.engine,
        )
        data = projection.get("data")
        if projection.get("status") != "recorded" or not isinstance(data, dict):
            raise DiagnosticEvidenceUnavailable("Exact recorded Forecast source is unavailable")
        policy, model = data["policy"], data["modelBinding"]
        versions = tuple((key, value) for key, value in (
            ("forecast_feature", data["featureVersion"]),
            ("policy_id", policy["id"]), ("policy_version", policy["version"]),
            ("policy_version_id", policy["versionId"]), ("model_id", model["model_id"]),
            ("model_version", model["model_version"]),
            ("model_name", model["model_name"]), ("model_stage", model["model_stage"]),
            ("model_dataset_snapshot", model["dataset_snapshot_id"]),
            ("model_label_version", model["label_version"]),
            ("model_artifact_sha256", model["artifact_sha256"]),
            ("model_feature_schema", model["feature_schema_version"]),
        ))
        versions += tuple(("model_" + key, model[key]) for key in (
            "registry_version", "source_run_id", "mlflow_run_id", "binding_resolved_at",
        ) if model[key] is not None)
        scored_at = _time(data["scoredAt"])
        facts = tuple(RecordedDiagnosticFact(
            name=name, value=data[name], source_kind="recorded_forecast_result",
            source_id=data["forecastOutputId"], source_version=str(data["forecastVersion"]),
            recorded_at=scored_at,
        ) for name in (
            "actualRevenue", "siteScoreBaselineP50", "siteScoreGapRatio",
            "trajectoryClass", "turningPointProbability", "dataQualityScore", "dataStalenessDays",
        ))
        facts += tuple(RecordedDiagnosticFact(
            name=f"forecast.w{row['horizonWeeks']}.{quantile}", value=row[quantile],
            source_kind="recorded_forecast_result", source_id=data["forecastOutputId"],
            source_version=str(data["forecastVersion"]), recorded_at=scored_at,
        ) for row in data["bands"] for quantile in ("p10", "p50", "p90"))
        alert = data["alertAtRun"]
        if alert["state"] == "recorded":
            facts += tuple(RecordedDiagnosticFact(
                name="alert." + key, value=alert["data"][key], source_kind="recorded_forecast_alert",
                source_id=data["alertId"], source_version=policy["versionId"],
                recorded_at=_time(alert["data"]["openedAt"]),
            ) for key in ("level", "reasonCode"))
        return DiagnosticEvidenceEnvelope(
            tenant_id=principal.tenant_id, store_id=issue["storeId"], issue_id=issue_id,
            workflow_version=version, alert_id=data["alertId"],
            forecast_output_id=data["forecastOutputId"], prediction_run_id=data["predictionRunId"],
            forecast_version=data["forecastVersion"],
            prediction_origin_time=_time(data["predictionOriginTime"]),
            feature_snapshot_time=_time(data["featureSnapshotTime"]), scored_at=scored_at,
            source_snapshot_ids=tuple(data["sourceSnapshotIds"]), versions=versions, facts=facts,
        )

    def _read(self, principal: Principal, issue_id: str, correlation_id: str):
        try:
            return self._recorded(principal, issue_id, correlation_id)
        except (DiagnosticEvidenceDenied, DiagnosticEvidenceUnavailable):
            raise
        except Exception as exc:
            # SQL/shape failures must escape the transaction for rollback.
            raise DiagnosticEvidenceUnavailable("Diagnostic source read unavailable") from exc

    def read_current(self, *, principal: Principal, issue_id: str, correlation_id: str):
        """No diagnostic/business rows are written by this read; Auth005 is real."""
        with self._transaction(principal, issue_id, correlation_id):
            return self._read(principal, issue_id, correlation_id)

    def capture(self, *, principal: Principal, issue_id: str, correlation_id: str):
        """Derive and retain actual sources, never accept a caller fact envelope."""
        with self._transaction(principal, issue_id, correlation_id):
            value = self._read(principal, issue_id, correlation_id)
            return self._retain(value, principal=principal, correlation_id=correlation_id)

    def _retain(self, value: DiagnosticEvidenceEnvelope, *, principal: Principal, correlation_id: str):
        """Write boundary after actual current-source admission, in its SQL tx.

        The atomic INSERT outcome, not an earlier existence read, owns the
        domain audit. A losing PG writer may receive the winner's same value.
        """
        with self.engine.transaction():
            receipt = self.evidence.save_with_created(value)
            if receipt.created:
                self.audit.record(AuditEvent(
                    event_type="forecastops.diagnostic_evidence.recorded",
                    actor=principal.subject_id, action="record_diagnostic_evidence",
                    resource=receipt.evidence.evidence_id, outcome="recorded", correlation_id=correlation_id,
                    metadata={"tenant_id": receipt.evidence.tenant_id, "store_id": receipt.evidence.store_id,
                              "issue_id": receipt.evidence.issue_id, "source_sha256": receipt.evidence.source_sha256,
                              "independent_observation_verified": False},
                ))
            return receipt.evidence

    def read_retained(self, *, principal: Principal, evidence_id: str, correlation_id: str):
        with self._transaction(principal, evidence_id, correlation_id):
            if (not isinstance(principal, Principal) or not principal.authenticated
                    or not principal.subject_id.strip() or not principal.tenant_id):
                raise DiagnosticEvidenceDenied("Authenticated diagnostic scope is required")
            value = self.evidence.get(principal.tenant_id, evidence_id)
            if value is None:
                raise DiagnosticEvidenceDenied("Diagnostic evidence is not available")
            current = self._read(principal, value.issue_id, correlation_id)
            if current.source_sha256 != value.source_sha256:
                raise DiagnosticEvidenceUnavailable("Retained diagnostic source no longer matches")
            return value
