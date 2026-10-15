"""Fresh execution request owner using the existing dual-lease score protocol.

Original promotion acceptance and job are read-only prerequisites. This owner
never reopens them, changes human Candidate status, or treats a failed retry as
a new successful execution. All writes share the PostgreSQL transaction.
"""
from __future__ import annotations

import hashlib
import json
import math
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

from modules.listing.application.intake_score_admission import validate_score_admission
from modules.listing.application.intake_score_delivery import (
    INTAKE_SITESCORE_JOB_TYPE,
    IntakeScoreJobRepository,
    ScoreDeliveryConflict,
    ScoreDeliveryDenied,
    ScoreDeliveryUnavailable,
    _digest,
    _jsonable,
    _time,
)
from modules.listing.application.intake_score_retry_policy import score_retry_policy
from modules.opsboard.application.network_read_scope import record_in_scope
from modules.sitescore.domain.scoring import SITESCORE_FEATURE_VERSION
from shared.infrastructure.persistence.sitescore_execution import ScoreExecutionDocuments
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobRequest, JobStatus

SITESCORE_EXECUTION_JOB_TYPE = "sitescore-execution"


class ScoreExecutionJobRepository(IntakeScoreJobRepository):
    job_type = SITESCORE_EXECUTION_JOB_TYPE

    def __init__(self, persistence: Any, *, model_admission: Any = None):
        super().__init__(persistence)
        self.model_admission = model_admission or getattr(persistence, "current_model_admission", None)

    def enqueue(self, promotion: dict, *, context: Any) -> dict:
        raise ScoreDeliveryDenied("SITESCORE_EXECUTION_REQUEST_OWNER_REQUIRED")

    def seal_enqueue(self, job_id: str, tenant_id: str) -> None:
        raise ScoreDeliveryDenied("SITESCORE_EXECUTION_REQUEST_OWNER_REQUIRED")

    def retry_score(self, *args: Any, **kwargs: Any) -> dict:
        raise ScoreDeliveryDenied("SITESCORE_EXECUTION_REQUEST_VERSION_REQUIRED")

    def _documents(self, tenant: str) -> ScoreExecutionDocuments:
        return ScoreExecutionDocuments(self.store, tenant)

    def _reports(self, tenant: str):
        from shared.infrastructure.persistence.repositories import DurableSiteScoreRepository
        return DurableSiteScoreRepository(self._documents(tenant).store)

    def _serialize(self, tenant: str):
        self._tenant(tenant)
        self.engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?,0))", (f"operator-intake-submission:{tenant}",))
        self.store.refresh(tenant)

    def _original(self, tenant: str, candidate_id: str):
        rows = self.engine.query("SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND job_type=? AND aggregate_id=?",
                                 (tenant, INTAKE_SITESCORE_JOB_TYPE, candidate_id))
        if len(rows) != 1:
            raise ScoreDeliveryDenied("SITESCORE_ORIGINAL_PARENT_UNVERIFIED")
        original = IntakeScoreJobRepository(self.persistence)
        row = original._row(str(rows[0]["job_id"]), tenant)
        intake, promotion, candidate = original._parents(row)
        row = original._row(str(row["job_id"]), tenant, lock=True)
        if row["status"] != "SUCCEEDED" or promotion["status"] != "COMPLETED" or not original._accepted(row, promotion):
            raise ScoreDeliveryDenied("SITESCORE_ORIGINAL_SUCCESS_REQUIRED")
        self._verify_result(row, tenant, candidate_id)
        return row, intake, promotion, candidate

    def _verify_result(self, row: dict, tenant: str, candidate_id: str):
        result = row.get("result")
        if not isinstance(result, dict) or not all(result.get(k) for k in (
                "report_id", "report_version", "sitescore_run_id", "prediction_run_id", "model_version", "model_lineage")):
            raise ScoreDeliveryDenied("SITESCORE_PREDECESSOR_RESULT_UNVERIFIED")
        reports = self._reports(tenant)
        report = reports.get_report(result["report_id"])
        run = reports.get_sitescore_run(result["sitescore_run_id"])
        prediction = reports.get_prediction_run(result["prediction_run_id"])
        lineage = result["model_lineage"]
        if (report is None or run is None or prediction is None or not isinstance(lineage, dict)
                or report.candidate_site_id != candidate_id or report.report_version != result["report_version"]
                or report.sitescore_run_id != result["sitescore_run_id"]
                or run.candidate_site_id != candidate_id or run.prediction_run_id != result["prediction_run_id"]
                or report.model_version != result["model_version"] or prediction.model_version_id != report.model_version
                or lineage.get("model_id") != report.model_version or not lineage.get("artifact_sha256")):
            raise ScoreDeliveryDenied("SITESCORE_PREDECESSOR_RESULT_UNVERIFIED")
        if (type(result["report_version"]) is not int or result["report_version"] < 1
                or prediction.run_status != "succeeded"
                or prediction.feature_snapshot_time != report.feature_snapshot_time
                or prediction.prediction_origin_time != report.prediction_origin_time
                or report.prediction_origin_time != _time(row["payload"]["prediction_origin_time"])
                or run.target_format_code != report.target_format_code
                or run.payback_p50_months != report.payback_p50_months
                or run.decision_recommendation != report.recommendation.value.lower()
                or any(getattr(run, f"{horizon}_{quantile}") != getattr(getattr(report, horizon), quantile)
                       for horizon in ("m1", "m3", "m6", "m12") for quantile in ("p10", "p50", "p90"))):
            raise ScoreDeliveryDenied("SITESCORE_PREDECESSOR_TRACE_UNVERIFIED")
        return report

    def _base(self, original: dict, tenant: str, candidate_id: str):
        latest = self._reports(tenant).latest(candidate_id)
        if latest is None:
            raise ScoreDeliveryDenied("SITESCORE_PREDECESSOR_RESULT_UNVERIFIED")
        if latest.report_id == original["result"]["report_id"]:
            row = original
        else:
            rows = self.engine.query("SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND job_type=? AND aggregate_id=? AND status='SUCCEEDED'",
                                     (tenant, self.job_type, candidate_id))
            matches = [self._row(str(r["job_id"]), tenant) for r in rows]
            matches = [r for r in matches if isinstance(r["result"], dict) and r["result"].get("report_id") == latest.report_id]
            if len(matches) != 1:
                raise ScoreDeliveryDenied("SITESCORE_PREDECESSOR_RESULT_UNVERIFIED")
            row = self._row(str(matches[0]["job_id"]), tenant, lock=True)
            owner = self._documents(tenant).locked(ScoreExecutionDocuments.REQUESTS, row["payload"]["execution_request_id"])
            if not owner or owner.get("job_id") != str(row["job_id"]) or owner.get("status") != "SUCCEEDED":
                raise ScoreDeliveryDenied("SITESCORE_PREDECESSOR_REQUEST_UNVERIFIED")
        report = self._verify_result(row, tenant, candidate_id)
        return row, report

    @staticmethod
    def _authorize(principal: Any, intake: dict, candidate: Any) -> None:
        evidence = {"tenant_id": intake["scope"]["tenant_id"], "scope": intake["scope"],
                    "heatZoneId": candidate.heat_zone_id}
        if not record_in_scope(principal, evidence):
            raise ScoreDeliveryDenied("SITESCORE_EXECUTION_SCOPE_DENIED")
        try:
            UUID(principal.subject_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ScoreDeliveryDenied("SITESCORE_EXECUTION_PRINCIPAL_DENIED") from exc

    def _current_inputs(self, tenant: str, candidate: Any) -> None:
        super()._current_inputs(tenant, candidate)
        # Reuse the existing Network six-field gate. It is independent of
        # current source/PIT/model admission and does not infer hard rules.
        from modules.opsboard.application.network_scoring import NetworkScoringService
        gate_reader = NetworkScoringService(seed_fixtures=False, tenant_id=tenant)
        view = gate_reader._candidate_from_draft(candidate)
        if not gate_reader._gate(view)["passed"]:
            raise ScoreDeliveryDenied("SITESCORE_CURRENT_DATA_GATE_BLOCKED")

    @staticmethod
    def _validate_pit(candidate: Any, tenant: str, now: datetime) -> None:
        from models.model_ready.contracts import MODEL_SPECS
        from models.shared_ml.production_runtime import (
            ProductionModelInputError,
            _validate_live_rows,
        )
        from modules.sitescore.domain.scoring import SITESCORE_FEATURE_MAX_AGE
        snapshot = candidate.feature_snapshot_time
        if not isinstance(snapshot, datetime) or snapshot.tzinfo is None or snapshot.utcoffset() is None:
            raise ProductionModelInputError("SITESCORE_FEATURE_TIMEZONE_REQUIRED")
        if snapshot > now or now - snapshot > SITESCORE_FEATURE_MAX_AGE:
            raise ProductionModelInputError("SITESCORE_FEATURE_TIME_INVALID")
        address, site = candidate.address, candidate.candidate_site
        if not site.target_format_code or not address.h3_res_9 or not candidate.listing.snapshot_id:
            raise ProductionModelInputError("SITESCORE_FEATURE_CONTRACT_INCOMPLETE")
        values = {"tenant_id": tenant, "target_format_code": site.target_format_code, "h3_index": address.h3_res_9,
            "latitude": address.latitude, "longitude": address.longitude, "geocode_confidence": address.geocode_confidence,
            "prior_90d_cell_net_revenue": candidate.prior_90d_cell_net_revenue,
            "prior_90d_cell_transaction_count": candidate.prior_90d_cell_transaction_count,
            "prior_90d_cell_store_count": candidate.prior_90d_cell_store_count,
            "feature_snapshot_time": snapshot, "view_version": SITESCORE_FEATURE_VERSION,
            "source_snapshot_ids": (candidate.listing.snapshot_id,)}
        for key in ("latitude", "longitude", "geocode_confidence", "prior_90d_cell_net_revenue", "prior_90d_cell_transaction_count", "prior_90d_cell_store_count"):
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise ProductionModelInputError("SITESCORE_FEATURE_CONTRACT_INCOMPLETE")
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ProductionModelInputError("SITESCORE_FEATURE_CONTRACT_INCOMPLETE")
        if address.latitude == 0 or address.longitude == 0:
            raise ProductionModelInputError("SITESCORE_LOCATION_INVALID")
        _validate_live_rows([values], service="sitescore", expected_feature_schema_version=SITESCORE_FEATURE_VERSION,
                           required_features=MODEL_SPECS["sitescore"].feature_columns)

    def _read_model(self) -> dict:
        if self.model_admission is None or not callable(getattr(self.model_admission, "guard", None)):
            raise ScoreDeliveryUnavailable("SITESCORE_CURRENT_MODEL_UNVERIFIED")
        return self.model_admission.read(service="sitescore", expected_feature_schema_version=SITESCORE_FEATURE_VERSION)

    def execution_context(self, candidate_id: str, *, principal: Any) -> dict:
        with self.engine.lock:
            self._serialize(principal.tenant_id)
            original, intake, _, candidate = self._original(principal.tenant_id, candidate_id)
            self._authorize(principal, intake, candidate)
            with self._reports(principal.tenant_id).report_guard(candidate_id):
                base, report = self._base(original, principal.tenant_id, candidate_id)
                return {"candidateSiteId": candidate_id, "reportId": report.report_id,
                        "reportVersion": report.report_version, "jobVersion": base["version"],
                        "originalJobId": str(original["job_id"]), "predecessorJobId": str(base["job_id"]),
                        "activeExecutionRequest": self._active_receipt(principal.tenant_id, candidate_id)}

    def _active_receipt(self, tenant: str, candidate_id: str) -> dict | None:
        documents = self._documents(tenant)
        head = documents.locked(documents.HEADS, candidate_id)
        if not head or head.get("active_request_id") is None:
            return None
        request = documents.locked(documents.REQUESTS, head["active_request_id"])
        if not request or request.get("candidate_site_id") != candidate_id:
            raise ScoreDeliveryUnavailable("SITESCORE_EXECUTION_RECEIPT_UNAVAILABLE")
        return self._request_receipt(request)

    def create_execution(self, command: Any, *, principal: Any) -> dict:
        command.validate()
        UUID(command.correlation_id)
        self._key()
        tenant, candidate_id = principal.tenant_id, command.candidate_site_id
        with self.engine.lock:
            self._serialize(tenant)
            original, intake, promotion, candidate = self._original(tenant, candidate_id)
            self._authorize(principal, intake, candidate)
            documents = self._documents(tenant)
            command_sha = _digest(vars(command) | {"correlation_id": None, "actor_id": principal.subject_id, "tenant_id": tenant})
            key_id = _digest([tenant, principal.subject_id, "sitescore.fresh", command.idempotency_key])
            key = documents.locked(documents.KEYS, key_id)
            if key:
                if key.get("command_sha256") != command_sha:
                    raise ScoreDeliveryConflict("SITESCORE_EXECUTION_IDEMPOTENCY_CONFLICT")
                request = documents.locked(documents.REQUESTS, key["execution_request_id"])
                if not request:
                    raise ScoreDeliveryUnavailable("SITESCORE_EXECUTION_RECEIPT_UNAVAILABLE")
                return self._request_receipt(request, created=False)
            head = documents.locked(documents.HEADS, candidate_id)
            if head and head.get("active_request_id"):
                active = documents.locked(documents.REQUESTS, head["active_request_id"])
                if not active or active["status"] != "SUCCEEDED":
                    raise ScoreDeliveryConflict("SITESCORE_EXECUTION_ACTIVE_OR_FAILED_REQUEST")
            with self._reports(tenant).report_guard(candidate_id):
                predecessor, report = self._base(original, tenant, candidate_id)
                if (report.report_id != command.expected_report_id or report.report_version != command.expected_report_version
                        or predecessor["version"] != command.expected_job_version):
                    raise ScoreDeliveryConflict("SITESCORE_EXECUTION_BASE_VERSION_CONFLICT")
                self._current_inputs(tenant, candidate)
                admission = validate_score_admission(self.engine, intake, candidate)
                now = self._now()
                self._validate_pit(candidate, tenant, now)
                model = self._read_model()
                request_id = str(uuid4())
                job_id = str(uuid5(UUID(request_id), "sitescore"))
                retry_policy = score_retry_policy()
                request = {"execution_request_id": request_id, "tenant_id": tenant, "candidate_site_id": candidate_id,
                           "listing_id": candidate.candidate_site.listing_id, "actor_id": principal.subject_id,
                           "reason": command.reason, "correlation_id": command.correlation_id,
                           "idempotency_key": command.idempotency_key, "command_sha256": command_sha,
                           "version": 1, "status": "QUEUED", "job_version": 1, "job_id": job_id, "created_at": now.isoformat(),
                           "original_job_id": str(original["job_id"]), "original_job_version": original["version"],
                           "original_result": deepcopy(original["result"]), "predecessor_job_id": str(predecessor["job_id"]),
                           "predecessor_job_version": predecessor["version"], "base_report_id": report.report_id,
                           "base_report_version": report.report_version, "input_sha256": self._fingerprint(intake, promotion, candidate),
                           "model_admission": model, "admission": admission, "retry_policy": retry_policy,
                           "result": None, "last_error": None}
                with self.model_admission.guard(model, tenant_id=tenant):
                    documents.insert(documents.REQUESTS, request_id, request)
                    documents.insert(documents.KEYS, key_id, {"execution_request_id": request_id, "command_sha256": command_sha})
                    new_head = {"tenant_id": tenant, "candidate_site_id": candidate_id, "active_request_id": request_id,
                                "latest_succeeded_request_id": head.get("latest_succeeded_request_id") if head else None,
                                "version": 1 if head is None else head["version"] + 1}
                    if head is None:
                        documents.insert(documents.HEADS, candidate_id, new_head)
                    else:
                        documents.cas(documents.HEADS, candidate_id, head["version"], new_head)
                    payload = {"execution_request_id": request_id, "intake_id": intake["intake_id"],
                               "promotion_decision_id": promotion["promotion_decision_id"], "candidate_site_id": candidate_id,
                               "original_job_id": str(original["job_id"]), "actor_id": principal.subject_id,
                               "generation": 1, "parent_fingerprint": request["input_sha256"],
                               "prediction_origin_time": now.isoformat(), "admission": admission, "retry_policy": retry_policy}
                    token = self._signed(job_id, tenant, 1)
                    self.engine.execute("INSERT INTO workflow.jobs(job_id,tenant_id,job_type,aggregate_type,aggregate_id,status,checkpoint,max_attempts,timeout_at,payload,correlation_id,idempotency_key,attempt_token_sha256) VALUES(?,?,?,'candidate_site',?,'QUEUED','SCORE_QUEUED',?,?,?::jsonb,?,?,?)",
                                        (job_id, tenant, self.job_type, candidate_id, retry_policy["max_attempts"], (now + timedelta(seconds=30)).isoformat(), json.dumps(payload), command.correlation_id, f"sitescore-execution:{tenant}:{request_id}", hashlib.sha256(token.encode()).hexdigest()))
                    delivery, created = self.queue.enqueue(JobRequest(job_type=self.job_type, job_id=job_id,
                        idempotency_key=f"sitescore-execution:{tenant}:{request_id}", payload={"job_id": job_id, "tenant_id": tenant, "attempt_token": token}), correlation_id=command.correlation_id)
                    if not created or delivery.job_id != job_id:
                        raise ScoreDeliveryConflict("SITESCORE_DELIVERY_RESERVATION_CONFLICT")
                    self.engine.execute("UPDATE durable_jobs SET max_retries=? WHERE job_id=?", (retry_policy["max_attempts"], job_id))
                    self._schedule_delivery(delivery, now)
                    row = self._row(job_id, tenant)
                    self.store.refresh(tenant)
                    with self.store.transaction():
                        self._audit(row, intake, "sitescore.execution_requested", principal.subject_id)
                        self._event(row, "sitescore.requested", "sitescore_execution_service", "SiteScoreRequestedV1", {"candidate_site_id": candidate_id, "site_score_job_id": job_id, "promotion_decision_id": promotion["promotion_decision_id"], "gate_snapshot_sha256": promotion["gate_snapshot_sha256"]})
                        self.store.flush()
                return self._request_receipt(request, created=True)

    def _parents(self, row: dict, *, live: bool = False):
        original = IntakeScoreJobRepository(self.persistence)
        parent = original._row(row["payload"]["original_job_id"], str(row["tenant_id"]))
        if parent is None:
            raise ScoreDeliveryDenied("SITESCORE_ORIGINAL_PARENT_UNVERIFIED")
        intake, promotion, candidate = original._parents(parent, live=live)
        if parent["status"] != "SUCCEEDED" or promotion["status"] != "COMPLETED" or row["payload"]["candidate_site_id"] != parent["payload"]["candidate_site_id"]:
            raise ScoreDeliveryDenied("SITESCORE_ORIGINAL_SUCCESS_REQUIRED")
        parent = original._row(str(parent["job_id"]), str(row["tenant_id"]), lock=True)
        request = self._documents(str(row["tenant_id"])).store.get(ScoreExecutionDocuments.REQUESTS, row["payload"]["execution_request_id"])
        if (not request or parent["version"] != request["original_job_version"] or parent["result"] != request["original_result"]
                or not original._accepted(parent, promotion)):
            raise ScoreDeliveryDenied("SITESCORE_ORIGINAL_SUCCESS_CHANGED")
        return intake, promotion, candidate

    @contextmanager
    def _unit(self, job_id: str, tenant: str):
        with self.engine.lock:
            self._serialize(tenant)
            with super()._unit(job_id, tenant) as unit:
                row, _, _, _ = unit
                documents = self._documents(tenant)
                request = documents.locked(documents.REQUESTS, row["payload"]["execution_request_id"])
                head = documents.locked(documents.HEADS, row["payload"]["candidate_site_id"])
                if (not request or not head or request["job_id"] != job_id
                        or (row["status"] != "SUCCEEDED" and head["active_request_id"] != request["execution_request_id"])):
                    raise JobFenceRejectedError("SITESCORE_EXECUTION_OWNER_REJECTED")
                yield unit


    def _parent_ready(self, row: dict, promotion: dict, accepted: bool) -> bool:
        return promotion["status"] == "COMPLETED"

    def _accepted(self, row: dict, promotion: dict) -> bool:
        acceptance = row["payload"].get("worker_acceptance")
        if acceptance is None:
            return False
        return (acceptance.get("execution_request_id") == row["payload"]["execution_request_id"]
                and acceptance.get("job_id") == str(row["job_id"])
                and acceptance.get("generation") == row["payload"]["generation"]
                and type(acceptance.get("accepted_fence")) is int
                and 0 < acceptance["accepted_fence"] <= row["fence_token"])

    def _accept(self, row: dict, intake: dict, promotion: dict, candidate: Any) -> None:
        payload = deepcopy(row["payload"])
        payload["worker_acceptance"] = {"execution_request_id": payload["execution_request_id"],
                                       "job_id": str(row["job_id"]), "generation": payload["generation"],
                                       "accepted_fence": row["fence_token"], "accepted_at": self._now().isoformat()}
        self._update(row, payload=payload)
        self._audit(row, intake, "sitescore.execution_accepted", payload["actor_id"])

    def _update(self, row: dict, **changes: Any) -> None:
        super()._update(row, **changes)
        documents = self._documents(str(row["tenant_id"]))
        request_id = row["payload"]["execution_request_id"]
        request = documents.locked(documents.REQUESTS, request_id)
        if not request:
            raise JobFenceRejectedError("SITESCORE_EXECUTION_OWNER_REJECTED")
        saved = {**request, "status": row["status"], "job_version": row["version"],
                 "result": deepcopy(row.get("result")), "last_error": deepcopy(row.get("last_error"))}
        documents.cas(documents.REQUESTS, request_id, request["version"], saved)
        if row["status"] == "SUCCEEDED":
            head = documents.locked(documents.HEADS, row["payload"]["candidate_site_id"])
            documents.cas(documents.HEADS, row["payload"]["candidate_site_id"], head["version"],
                          {**head, "active_request_id": None, "latest_succeeded_request_id": request_id})

    def _audit(self, row: dict, intake: dict, action: str, actor: str, *, result: str = "SUCCEEDED") -> None:
        audit_id = str(uuid4())
        self.store.workflow_audits[audit_id] = {
            "audit_event_id": audit_id, "tenant_id": str(row["tenant_id"]), "intake_id": intake["intake_id"],
            "actor_subject_id": actor, "service_principal": "sitescore_execution_service" if action.endswith("requested") else "sitescore_worker",
            "action": action, "resource_type": "sitescore_execution_request", "resource_id": row["payload"]["execution_request_id"],
            "before_value": row.pop("_previous_receipt", None), "after_value": self._audit_snapshot(row),
            "result": result, "correlation_id": str(row["correlation_id"]), "occurred_at": self._now().isoformat(), "worm_state": "worm_pending"}

    def model_receipt(self, attempt: Any) -> dict:
        request_id = attempt.token["execution_request_id"]
        request = self._documents(attempt.tenant_id).store.get(ScoreExecutionDocuments.REQUESTS, request_id)
        return deepcopy(request["model_admission"])

    def begin_attempt(self, job: Any):
        attempt = super().begin_attempt(job)
        if attempt is None:
            return None
        request_id = self._row(job.job_id, attempt.tenant_id)["payload"]["execution_request_id"]
        attempt.token["execution_request_id"] = request_id
        from models.shared_ml.production_runtime import (
            ProductionModelApprovalError,
            ProductionModelInputError,
            ProductionModelLineageError,
            ProductionModelRuntimeError,
        )
        try:
            with self._unit(job.job_id, attempt.tenant_id) as (row, intake, promotion, candidate):
                self._check_attempt(job, attempt, row, intake, promotion, candidate)
                request = self._documents(attempt.tenant_id).locked(ScoreExecutionDocuments.REQUESTS, request_id)
                with self._reports(attempt.tenant_id).report_guard(attempt.candidate_site_id):
                    latest = self._reports(attempt.tenant_id).latest(attempt.candidate_site_id)
                    if latest is None or latest.report_id != request["base_report_id"] or latest.report_version != request["base_report_version"]:
                        raise ScoreDeliveryDenied("SITESCORE_EXECUTION_BASE_CHANGED")
                    with self.model_admission.guard(request["model_admission"], tenant_id=attempt.tenant_id):
                        self._check_attempt(job, attempt, row, intake, promotion, candidate)
        except (ProductionModelRuntimeError, ScoreDeliveryDenied) as exc:
            self.fail_attempt(job, attempt, error_code=getattr(exc, "code", str(exc)), retryable=not isinstance(exc, (ProductionModelApprovalError, ProductionModelLineageError, ProductionModelInputError, ScoreDeliveryDenied)))
            return None
        return attempt

    def commit_success(self, job: Any, attempt: Any, persist_callback: Any):
        with self._unit(job.job_id, attempt.tenant_id) as (row, intake, promotion, candidate):
            self._check_attempt(job, attempt, row, intake, promotion, candidate)
            documents = self._documents(attempt.tenant_id)
            request = documents.locked(documents.REQUESTS, row["payload"]["execution_request_id"])
            with self._reports(attempt.tenant_id).report_guard(attempt.candidate_site_id):
                latest = self._reports(attempt.tenant_id).latest(attempt.candidate_site_id)
                if latest is None or latest.report_id != request["base_report_id"] or latest.report_version != request["base_report_version"]:
                    raise JobFenceRejectedError("SITESCORE_EXECUTION_BASE_CHANGED")
                with self.model_admission.guard(request["model_admission"], tenant_id=attempt.tenant_id):
                    # Guard may block past lease deadline. Shared commit checks both again.
                    receipt = super().commit_success(job, attempt, persist_callback)
                    self._event(row, "sitescore.execution_completed", "sitescore_worker", "SiteScoreExecutionChangedV1", {})
                    return receipt

    def settle_cancelled_attempt(self, job: Any, attempt: Any) -> bool:
        if self._authenticate_reference(job) != attempt.tenant_id:
            raise ScoreDeliveryDenied("SITESCORE_ATTEMPT_TENANT_DENIED")
        with self._unit(job.job_id, attempt.tenant_id) as (row, intake, _, _):
            current = self._delivery(job, row)
            if (row["status"] != "RUNNING" or not row["cancellation_requested_at"]
                    or row["fence_token"] != attempt.token["fence"]
                    or row["version"] != attempt.token["version"] + row["payload"].get("heartbeat_count", 0)
                    or row["payload"]["generation"] != attempt.token["generation"]
                    or not row["lease_expires_at"] or _time(row["lease_expires_at"]) <= self._now()):
                return False
            self._settle_cancelled(job, row, intake, current)
            return True

    def _failure_fence(self, job: Any, attempt: Any, row: dict):
        current = self._delivery(job, row)
        if (row["status"] != "RUNNING" or row["cancellation_requested_at"]
                or row["fence_token"] != attempt.token["fence"]
                or row["version"] != attempt.token["version"] + row["payload"].get("heartbeat_count", 0)
                or row["payload"]["generation"] != attempt.token["generation"]
                or not row["lease_expires_at"] or _time(row["lease_expires_at"]) <= self._now()):
            raise JobFenceRejectedError("SITESCORE_RESULT_FENCE_REJECTED")
        return current

    def fail_attempt(self, job: Any, attempt: Any, error_code: str, retryable: bool) -> dict:
        with self._unit(job.job_id, attempt.tenant_id) as (row, intake, promotion, candidate):
            current = self._failure_fence(job, attempt, row)
            if retryable and row["attempt"] < row["max_attempts"]:
                due = self._now() + timedelta(seconds=row["payload"]["retry_policy"]["backoff_seconds"][row["attempt"] - 1])
                self._update(row, status="RETRYING", next_attempt_at=due, last_error={"code": error_code, "retryable": True}, lease_expires_at=None, lease_owner=None)
                self.queue.update_status(job.job_id, JobStatus.QUEUED, expected_version=current.version, fence_token=current.fence_token, error_message=error_code, delivery_state=JobDeliveryState.RETRYING)
                self._schedule_delivery(current, due, after_status_update=True)
                self._audit(row, intake, "sitescore.retry_scheduled", row["payload"]["actor_id"])
            else:
                self._settle_failed(job, row, intake, promotion, candidate, current, error_code, retryable)
            return self._receipt(row)

    def retry_execution(self, request_id: str, *, principal: Any, expected_version: int,
                        expected_job_version: int, reason: str, idempotency_key: str, correlation_id: str) -> dict:
        if (type(expected_version) is not int or expected_version < 1 or type(expected_job_version) is not int
                or expected_job_version < 1 or not isinstance(reason, str) or not reason.strip() or not idempotency_key):
            from modules.listing.application.intake_score_delivery import (
                ScoreDeliveryValidationError,
            )
            raise ScoreDeliveryValidationError("SITESCORE_REPLAY_CHECKPOINT_REQUIRED")
        UUID(correlation_id)
        tenant = principal.tenant_id
        with self.engine.lock:
            self._serialize(tenant)
            documents = self._documents(tenant)
            discovered = documents.store.get(documents.REQUESTS, request_id)
            if not discovered:
                raise ScoreDeliveryDenied("SITESCORE_EXECUTION_OWNER_UNAVAILABLE")
            with self._unit(discovered["job_id"], tenant) as (row, intake, promotion, candidate):
                self._authorize(principal, intake, candidate)
                request = documents.locked(documents.REQUESTS, request_id)
                key_id = _digest([tenant, principal.subject_id, "sitescore.retry", idempotency_key])
                command_sha = _digest([request_id, expected_version, expected_job_version, reason])
                key = documents.locked(documents.KEYS, key_id)
                if key:
                    if key.get("command_sha256") != command_sha or key.get("execution_request_id") != request_id:
                        raise ScoreDeliveryConflict("SITESCORE_EXECUTION_IDEMPOTENCY_CONFLICT")
                    return self._request_receipt(request)
                if (request["version"] != expected_version or row["version"] != expected_job_version
                        or row["status"] not in {"FAILED", "CANCELLED"} or row["attempt"] >= row["max_attempts"]):
                    raise ScoreDeliveryConflict("SITESCORE_REPLAY_VERSION_OR_STATE_CONFLICT")
                self._current_inputs(tenant, candidate)
                self._validate_pit(candidate, tenant, self._now())
                if (self._fingerprint(intake, promotion, candidate) != request["input_sha256"]
                        or validate_score_admission(self.engine, intake, candidate) != request["admission"]
                        or score_retry_policy() != request["retry_policy"]):
                    raise ScoreDeliveryDenied("SITESCORE_REPLAY_PINNED_INPUT_CHANGED")
                with self._reports(tenant).report_guard(candidate.candidate_site.candidate_site_id):
                    latest = self._reports(tenant).latest(candidate.candidate_site.candidate_site_id)
                    if latest is None or latest.report_id != request["base_report_id"] or latest.report_version != request["base_report_version"]:
                        raise ScoreDeliveryConflict("SITESCORE_EXECUTION_BASE_VERSION_CONFLICT")
                    with self.model_admission.guard(request["model_admission"], tenant_id=tenant):
                        payload = deepcopy(row["payload"])
                        payload["generation"] += 1
                        payload["actor_id"] = principal.subject_id
                        payload.pop("worker_acceptance", None)
                        payload["prediction_origin_time"] = self._now().isoformat()
                        token = self._signed(str(row["job_id"]), tenant, payload["generation"])
                        self._update(row, status="QUEUED", payload=payload, fence_token=row["fence_token"] + 1,
                            cancellation_requested_at=None, last_error=None, next_attempt_at=None,
                            attempt_token_sha256=hashlib.sha256(token.encode()).hexdigest())
                        current = self.queue.get(str(row["job_id"]))
                        if current is None or current.status not in {JobStatus.FAILED, JobStatus.CANCELLED}:
                            raise ScoreDeliveryConflict("SITESCORE_REPLAY_DELIVERY_CONFLICT")
                        update = self.engine.execute("UPDATE durable_jobs SET status=?,delivery_state=NULL,payload_json=?,version=version+1,fence_token=fence_token+1,locked_by=NULL,lease_expires_at=NULL,heartbeat_at=NULL,error_message=NULL,available_at=? WHERE job_id=? AND version=? AND fence_token=?",
                            (JobStatus.QUEUED.value, json.dumps({"job_id": str(row["job_id"]), "tenant_id": tenant, "attempt_token": token}), self._now().isoformat(), str(row["job_id"]), current.version, current.fence_token))
                        if update.rowcount != 1:
                            raise JobFenceRejectedError("SITESCORE_REPLAY_DELIVERY_CAS_REJECTED")
                        documents.insert(documents.KEYS, key_id, {"execution_request_id": request_id, "command_sha256": command_sha})
                        self._event(row, "job.replay_requested", "job_platform", "JobReplayRequestedV1", {
                            "job_id": str(row["job_id"]), "checkpoint": "SCORE_QUEUED", "requested_by": principal.subject_id,
                            "reason": reason, "override_retry_budget": False}, job_event=True)
                        self._audit(row, intake, "sitescore.replay_requested", principal.subject_id)
                        return self._request_receipt(documents.store.get(documents.REQUESTS, request_id))

    def _settle_failed(self, job: Any, row: dict, intake: dict, promotion: dict, candidate: Any, current: Any,
                       error_code: str, retryable: bool, *, exhausted: bool = False) -> None:
        exhausted = exhausted or row["attempt"] >= row["max_attempts"]
        self._update(row, status="DEAD_LETTER" if exhausted else "FAILED", last_error={"code": error_code, "retryable": retryable}, lease_expires_at=None, lease_owner=None,
                     dead_lettered_at=self._now() if exhausted else None, next_attempt_at=None)
        self.queue.update_status(job.job_id, JobStatus.FAILED, expected_version=current.version, fence_token=current.fence_token, error_message=error_code,
                                 delivery_state=JobDeliveryState.DEAD_LETTER if exhausted else None)
        self._schedule_delivery(current, None, after_status_update=True)
        self._event(row, "sitescore.failed", "sitescore_worker", "SiteScoreFailedV1", {"candidate_site_id": row["payload"]["candidate_site_id"], "site_score_job_id": job.job_id, "error_code": error_code, "retryable": retryable, "attempt": row["attempt"]})
        self._audit(row, intake, "sitescore.failed", row["payload"]["actor_id"], result="FAILED")

    def _event(self, row: dict, event: str, producer: str, schema: str, payload: dict,
               *, promotion: bool = False, job_event: bool = False) -> None:
        if event not in {"sitescore.requested", "sitescore.failed", "sitescore.execution_completed"}:
            return super()._event(row, event, producer, schema, payload, promotion=promotion, job_event=job_event)
        from shared.domain.events import DomainEvent
        request = self._documents(str(row["tenant_id"])).store.get(ScoreExecutionDocuments.REQUESTS, row["payload"]["execution_request_id"])
        event_type = {"sitescore.requested": "sitescore.execution_requested", "sitescore.failed": "sitescore.execution_failed"}.get(event, event)
        result = request.get("result") or {}
        own_id = request["execution_request_id"]
        self.outbox.save(DomainEvent(event_type=event_type, tenant_id=str(row["tenant_id"]),
            aggregate_type="sitescore_execution_request", aggregate_id=own_id, aggregate_version=request["version"],
            partition_key=f"{row['tenant_id']}:{own_id}", correlation_id=str(row["correlation_id"]),
            producer=producer, schema_ref="#/payloads/SiteScoreExecutionChangedV1", payload={
                "execution_request_id": own_id, "candidate_site_id": request["candidate_site_id"], "job_id": request["job_id"],
                "request_version": request["version"], "status": request["status"], "base_report_id": request["base_report_id"],
                "base_report_version": request["base_report_version"], "result_report_id": result.get("report_id"),
                "result_report_version": result.get("report_version")}))

    @staticmethod
    def _request_receipt(request: dict, *, created: bool = False) -> dict:
        return {"source": "canonical", "created": created, "executionRequestId": request["execution_request_id"],
                "candidateSiteId": request["candidate_site_id"], "jobId": request["job_id"], "status": request["status"],
                "actualVersion": request["version"], "jobVersion": request.get("job_version"),
                "baseReportId": request["base_report_id"], "baseReportVersion": request["base_report_version"],
                "result": None if request.get("result") is None else {key: _jsonable(request["result"][key]) for key in ("report_id", "report_version", "sitescore_run_id", "prediction_run_id", "model_version")}, "lastError": deepcopy(request.get("last_error")), "correlationId": request["correlation_id"]}

    def get_execution(self, request_id: str, *, principal: Any) -> dict | None:
        with self.engine.lock:
            self._serialize(principal.tenant_id)
            request = self._documents(principal.tenant_id).store.get(ScoreExecutionDocuments.REQUESTS, request_id)
            if not request:
                return None
            _, intake, _, candidate = self._original(principal.tenant_id, request["candidate_site_id"])
            self._authorize(principal, intake, candidate)
            return self._request_receipt(request)
