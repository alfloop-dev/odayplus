"""Transaction-owned Intake SiteScore jobs.

workflow.jobs owns business state. The durable queue carries only a signed
reference; it is a local delivery binding, not evidence of Cloud Tasks admission.
Inference runs outside SQL. Results may be written only under a fresh parent,
job and delivery fence in the same transaction as the result and audit.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, is_dataclass, replace
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID, uuid5

from modules.listing.application.intake_score_admission import validate_score_admission
from modules.listing.application.intake_score_retry_policy import score_retry_policy
from modules.listing.application.intake_workflow_bootstrap import record_workflow_change
from modules.sitescore.application.candidate_features import feature_from_candidate
from shared.domain.events import DomainEvent
from shared.infrastructure.persistence.assisted_listing_intake import _STATE_COLLECTIONS
from shared.jobs.queue import (
    JobDeliveryState,
    JobFenceRejectedError,
    JobRecord,
    JobRequest,
    JobStatus,
)

INTAKE_SITESCORE_JOB_TYPE = "intake-sitescore"
_SIGNING_KEY = "ODP_INTAKE_SCORE_ATTEMPT_SIGNING_KEY"


class ScoreDeliveryUnavailable(RuntimeError):
    pass


class ScoreDeliveryConflict(RuntimeError):
    pass


class ScoreDeliveryDenied(RuntimeError):
    pass


class ScoreDeliveryValidationError(ValueError):
    pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, UUID)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(vars(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _time(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _with_candidate_status(candidate: Any, status: str) -> Any:
    updated = deepcopy(candidate)
    updated.candidate_site = replace(updated.candidate_site, site_status=status)
    for name, value in vars(candidate.candidate_site).items():
        if name not in vars(updated.candidate_site):
            object.__setattr__(updated.candidate_site, name, deepcopy(value))
    return updated


@dataclass(frozen=True)
class ScoreAttempt:
    tenant_id: str
    candidate_site_id: str
    prediction_origin_time: datetime
    scored_at: datetime
    token: dict[str, Any]
    _candidate: Any

    @property
    def features(self) -> list[Any]:
        # Hydration is inside the handler's inference error boundary, so invalid
        # evidence is a fenced permanent failure, not an endless lease reclaim.
        feature = feature_from_candidate(self._candidate, tenant_id=self.tenant_id)
        if feature.feature_snapshot_time is not None and feature.feature_snapshot_time > self.prediction_origin_time:
            from models.shared_ml.production_runtime import ProductionModelInputError

            raise ProductionModelInputError("SITESCORE_FEATURE_AFTER_PREDICTION_ORIGIN")
        return [feature]


class IntakeScoreJobRepository:
    job_type = INTAKE_SITESCORE_JOB_TYPE
    def __init__(self, persistence: Any):
        self.persistence = persistence
        self.engine = persistence.engine
        self.store = persistence.assisted_intake_store
        self.queue = persistence.job_queue
        self.outbox = persistence.outbox_repository
        if (getattr(self.engine, "dialect", None) != "postgresql"
                or getattr(self.store, "_engine", None) is not self.engine
                or getattr(self.queue, "_engine", None) is not self.engine
                or getattr(self.outbox, "_engine", None) is not self.engine):
            raise ScoreDeliveryUnavailable("SITESCORE_TRANSACTION_BINDING_UNAVAILABLE")

    def _key(self) -> bytes:
        key = os.environ.get(_SIGNING_KEY, "").encode()
        if len(key) < 32:
            raise ScoreDeliveryUnavailable("SITESCORE_ATTEMPT_SIGNING_UNAVAILABLE")
        return key

    def _signed(self, job_id: str, tenant: str, generation: int) -> str:
        body = f"{job_id}:{tenant}:{generation}"
        return f"{generation}.{hmac.new(self._key(), body.encode(), hashlib.sha256).hexdigest()}"

    def _authenticate_reference(self, job: JobRecord) -> str:
        """Authenticate opaque delivery metadata before loading business input."""
        payload = job.payload
        if (job.job_type != self.job_type
                or set(payload) != {"job_id", "tenant_id", "attempt_token"}
                or payload.get("job_id") != job.job_id):
            raise ScoreDeliveryDenied("SITESCORE_ATTEMPT_TOKEN_DENIED")
        try:
            UUID(job.job_id)
            tenant = str(UUID(payload["tenant_id"]))
            generation = int(str(payload["attempt_token"]).split(".", 1)[0])
        except (TypeError, ValueError, AttributeError) as exc:
            raise ScoreDeliveryDenied("SITESCORE_ATTEMPT_TOKEN_DENIED") from exc
        if generation < 1 or not hmac.compare_digest(str(payload["attempt_token"]), self._signed(job.job_id, tenant, generation)):
            raise ScoreDeliveryDenied("SITESCORE_ATTEMPT_TOKEN_DENIED")
        return tenant

    def _tenant(self, tenant: str) -> None:
        UUID(tenant)
        self.engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (tenant,))

    def _row(self, job_id: str, tenant: str, *, lock: bool = False) -> dict[str, Any] | None:
        row = self.engine.query_one(
            ("SELECT * FROM workflow.jobs WHERE job_id=? AND tenant_id=? AND job_type=? FOR UPDATE"
             if lock else "SELECT * FROM workflow.jobs WHERE job_id=? AND tenant_id=? AND job_type=?"),
            (job_id, tenant, self.job_type),
        )
        if row is not None:
            for key in ("payload", "result", "last_error"):
                if isinstance(row[key], str):
                    row[key] = json.loads(row[key])
        return row

    def _now(self) -> datetime:
        # Eligibility and lease checks must use the same clock as queue claims.
        return _time(self.engine.query_one("SELECT statement_timestamp() AS now")["now"])

    def _repository(self, tenant: str) -> Any:
        repo = self.persistence.listing_repository_for_tenant(tenant)
        if getattr(getattr(repo, "_store", None), "engine", None) is not self.engine:
            raise ScoreDeliveryUnavailable("SITESCORE_CANDIDATE_BINDING_UNAVAILABLE")
        return repo

    def _candidate(self, tenant: str, candidate_id: str) -> Any:
        return next((item for item in self._repository(tenant).list_candidates()
                     if item.candidate_site.candidate_site_id == candidate_id), None)

    def _current_inputs(self, tenant: str, candidate: Any) -> None:
        from modules.listing.application.candidate_score_context import (
            read_current_candidate_inputs,
            validate_current_candidate_inputs,
        )

        current = read_current_candidate_inputs(self._repository(tenant), self.store, candidate)
        validate_current_candidate_inputs(candidate, current)

    def _parents(self, row: dict[str, Any], *, live: bool = False) -> tuple[dict, dict, Any]:
        payload, tenant = row["payload"], str(row["tenant_id"])
        intake_id, promotion_id = payload["intake_id"], payload["promotion_decision_id"]
        if live:
            intake, promotion = self.store.intakes.get(intake_id), self.store.promotions.get(promotion_id)
        else:
            intake = self.store._read_locked(_STATE_COLLECTIONS["intakes"], f"{tenant}:{intake_id}")
            promotion = self.store._read_locked(_STATE_COLLECTIONS["promotions"], f"{tenant}:{promotion_id}")
        candidate = self._candidate(tenant, payload["candidate_site_id"])
        if (not intake or not promotion or candidate is None
                or intake.get("scope", {}).get("tenant_id") != tenant
                or promotion.get("tenant_id") != tenant
                or promotion.get("intake_id") != intake_id
                or promotion.get("site_score_job_id") != str(row["job_id"])
                or promotion.get("candidate_site_id") != payload["candidate_site_id"]
                or promotion.get("listing_id") != candidate.candidate_site.listing_id
                or str(row["aggregate_id"]) != payload["candidate_site_id"]):
            raise ScoreDeliveryDenied("SITESCORE_PARENT_BINDING_DENIED")
        repo = self._repository(tenant)
        for collection, key in ((repo._CANDIDATES, payload["candidate_site_id"]),
                                (repo._LISTINGS, candidate.candidate_site.listing_id),
                                (repo._ADDRESSES, candidate.candidate_site.address_id)):
            self.store._read_locked(repo._store._collection(collection), key)
        candidate = self._candidate(tenant, payload["candidate_site_id"])
        return intake, promotion, candidate

    def _fingerprint(self, intake: dict, promotion: dict, candidate: Any) -> str:
        repo = self._repository(intake["scope"]["tenant_id"])
        return _digest({
            "intake": {k: intake.get(k) for k in ("version", "status", "stage", "scope", "match_result", "resolved_listing_id", "source_policy_version", "parser_release_id")},
            "promotion": {k: promotion.get(k) for k in ("version", "status", "gate_snapshot_sha256", "listing_id", "candidate_site_id", "site_score_job_id")},
            "candidate": candidate,
            "listing": repo.get_listing(candidate.candidate_site.listing_id),
            "address": repo.get_address(candidate.candidate_site.address_id),
        })

    def _receipt(self, row: dict[str, Any]) -> dict[str, Any]:
        status = row["status"]
        return {
            "job_id": str(row["job_id"]), "tenant_id": str(row["tenant_id"]),
            **{k: row["payload"][k] for k in ("intake_id", "promotion_decision_id", "candidate_site_id")},
            "status": "FAILED" if status == "DEAD_LETTER" else "QUEUED" if status == "RETRYING" else status,
            "delivery_state": status if status in {"DEAD_LETTER", "RETRYING"} else None,
            "checkpoint": row["checkpoint"], "attempt": row["attempt"],
            "version": row["version"], "correlation_id": str(row["correlation_id"]),
        }

    def _audit_snapshot(self, row: dict) -> dict:
        return {**self._receipt(row), "canonical_status": row["status"],
                "next_attempt_at": _jsonable(row.get("next_attempt_at")),
                "retry_policy": deepcopy(row["payload"].get("retry_policy")),
                "worker_acceptance": deepcopy(row["payload"].get("worker_acceptance"))}

    def get_job(self, job_id: str, tenant_id: str) -> dict[str, Any] | None:
        try:
            UUID(job_id)
        except (TypeError, ValueError, AttributeError):
            return None
        with self.engine.lock:
            self._tenant(tenant_id)
            row = self._row(job_id, tenant_id)
            if row is None:
                return None
            self._parents(row)
            return self._receipt(row)

    def enqueue(self, promotion: dict[str, Any], *, context: Any) -> dict[str, Any]:
        tenant = context.actor.tenant_id
        self._key()
        retry_policy = score_retry_policy()
        with self.engine.lock:
            self._tenant(tenant)
            job_id = str(uuid5(UUID(promotion["promotion_decision_id"]), "sitescore"))
            promotion["site_score_job_id"] = job_id
            candidate = self._candidate(tenant, promotion["candidate_site_id"])
            intake = self.store.intakes.get(promotion["intake_id"])
            if candidate is None or intake is None or intake["scope"]["tenant_id"] != tenant:
                raise ScoreDeliveryDenied("SITESCORE_PARENT_BINDING_DENIED")
            self._current_inputs(tenant, candidate)
            admission = validate_score_admission(self.engine, intake, candidate)
            now = self._now()
            correlation = context.correlation_id or intake["correlation_id"]
            UUID(correlation)
            payload = {
                "intake_id": promotion["intake_id"],
                "promotion_decision_id": promotion["promotion_decision_id"],
                "candidate_site_id": promotion["candidate_site_id"],
                "actor_id": context.actor.actor_id,
                "generation": 1,
                "parent_fingerprint": self._fingerprint(intake, promotion, candidate),
                "prediction_origin_time": now.isoformat(),
                "admission": admission,
                "retry_policy": retry_policy,
            }
            token = self._signed(job_id, tenant, 1)
            self.engine.execute(
                "INSERT INTO workflow.jobs(job_id,tenant_id,job_type,aggregate_type,aggregate_id,"
                "status,checkpoint,max_attempts,timeout_at,payload,correlation_id,idempotency_key,attempt_token_sha256) "
                "VALUES(?,?,?,'candidate_site',?,'QUEUED','SCORE_QUEUED',?,?,?::jsonb,?,?,?)",
                (job_id, tenant, self.job_type, promotion["candidate_site_id"],
                 retry_policy["max_attempts"],
                 # This is the existing enqueue deadline, not an inference SLA.
                 (now + timedelta(seconds=30)).isoformat(), json.dumps(payload), correlation,
                 f"intake-sitescore:{tenant}:{promotion['promotion_decision_id']}", hashlib.sha256(token.encode()).hexdigest()),
            )
            delivery, created = self.queue.enqueue(JobRequest(
                job_type=self.job_type, job_id=job_id,
                idempotency_key=f"intake-sitescore:{tenant}:{promotion['promotion_decision_id']}",
                payload={"job_id": job_id, "tenant_id": tenant, "attempt_token": token},
            ), correlation_id=correlation)
            if not created or delivery.job_id != job_id:
                raise ScoreDeliveryConflict("SITESCORE_DELIVERY_RESERVATION_CONFLICT")
            self.engine.execute("UPDATE durable_jobs SET max_retries=? WHERE job_id=?", (retry_policy["max_attempts"], job_id))
            self._schedule_delivery(delivery, now)
            row = self._row(job_id, tenant)
            self._event(row, "sitescore.requested", "candidate_promotion_service", "SiteScoreRequestedV1", {
                "candidate_site_id": payload["candidate_site_id"],
                "promotion_decision_id": payload["promotion_decision_id"],
                "site_score_job_id": job_id, "gate_snapshot_sha256": promotion["gate_snapshot_sha256"],
            })
            self._audit(row, intake, "sitescore.enqueue", context.actor.actor_id)
            return self._receipt(row)

    def seal_enqueue(self, job_id: str, tenant_id: str) -> None:
        """Bind the accepted parent version after the enclosing review audit.

        Caller still owns the original promotion transaction and parent locks;
        no delivery can be claimed until that transaction commits.
        """
        with self.engine.lock:
            self._tenant(tenant_id)
            row = self._row(job_id, tenant_id, lock=True)
            if row is None or row["status"] != "QUEUED" or row["attempt"] != 0:
                raise ScoreDeliveryConflict("SITESCORE_ENQUEUE_SEAL_CONFLICT")
            intake, promotion, candidate = self._parents(row, live=True)
            payload = deepcopy(row["payload"])
            payload["parent_fingerprint"] = self._fingerprint(intake, promotion, candidate)
            self._update(row, payload=payload)

    @contextmanager
    def _unit(self, job_id: str, tenant: str):
        with self.engine.lock:
            self._tenant(tenant)
            self.engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?,0))", (f"operator-intake-submission:{tenant}",))
            initial = self._row(job_id, tenant)
            if initial is None:
                raise ScoreDeliveryDenied("SITESCORE_JOB_UNAVAILABLE")
            self._parents(initial)  # parent locks precede job and delivery locks
            self.store.refresh(tenant)
            with self.store.transaction():
                row = self._row(job_id, tenant, lock=True)
                self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (job_id,))
                intake, promotion, candidate = self._parents(row, live=True)
                yield row, intake, promotion, candidate
                self.store.flush()

    def _delivery(self, job: JobRecord, row: dict[str, Any]) -> JobRecord:
        payload = job.payload
        tenant = str(row["tenant_id"])
        token = self._signed(job.job_id, tenant, row["payload"]["generation"])
        if (set(payload) != {"job_id", "tenant_id", "attempt_token"}
                or payload["job_id"] != job.job_id or payload["tenant_id"] != tenant
                or not hmac.compare_digest(str(payload["attempt_token"]), token)
                or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), row["attempt_token_sha256"])):
            raise ScoreDeliveryDenied("SITESCORE_ATTEMPT_TOKEN_DENIED")
        current = self.queue.get(job.job_id)
        if (current is None or current.job_type != self.job_type
                or current.payload != payload or current.status != JobStatus.RUNNING
                or current.fence_token != job.fence_token or current.locked_by != job.locked_by
                or current.lease_expires_at is None or current.lease_expires_at <= self._now()):
            raise JobFenceRejectedError("SITESCORE_DELIVERY_FENCE_REJECTED")
        return current

    def _schedule_delivery(self, current: JobRecord, available_at: datetime | None,
                           *, after_status_update: bool = False) -> None:
        """Mirror canonical eligibility in the same fenced transaction.

        The global delivery poller must not read tenant-protected business rows.
        This scheduling hint never authorizes execution: begin_attempt checks
        the signed reference, tenant, canonical checkpoint and due time again.
        """
        result = self.engine.execute(
            "UPDATE durable_jobs SET available_at=? WHERE job_id=? AND version=? AND fence_token=?",
            (_jsonable(available_at), current.job_id,
             current.version + int(after_status_update), current.fence_token),
        )
        if result.rowcount != 1:
            raise JobFenceRejectedError("SITESCORE_DELIVERY_SCHEDULE_CAS_REJECTED")

    def _accepted(self, row: dict, promotion: dict) -> bool:
        acceptance = row["payload"].get("worker_acceptance")
        if acceptance is None:
            return False
        expected = {
            "tenant_id": str(row["tenant_id"]), "job_id": str(row["job_id"]),
            "intake_id": row["payload"]["intake_id"],
            "promotion_decision_id": row["payload"]["promotion_decision_id"],
            "candidate_site_id": row["payload"]["candidate_site_id"],
            "promotion_version": promotion["version"],
            "audit_event_id": promotion.get("audit_event_id"),
        }
        if (not isinstance(acceptance, dict) or promotion["status"] != "COMPLETED"
                or any(acceptance.get(key) != value for key, value in expected.items())
                or type(acceptance.get("accepted_fence")) is not int
                or not 0 < acceptance["accepted_fence"] <= row["fence_token"]
                or not acceptance.get("accepted_at")):
            raise JobFenceRejectedError("SITESCORE_ACCEPTANCE_BINDING_REJECTED")
        try:
            accepted_at = _time(acceptance["accepted_at"])
            if (accepted_at.tzinfo is None or accepted_at.utcoffset() is None
                    or not _time(row["created_at"]) <= accepted_at <= self._now()):
                raise ValueError("Acceptance timestamp outside job lifetime")
        except (ValueError, TypeError, OverflowError) as exc:
            raise JobFenceRejectedError("SITESCORE_ACCEPTANCE_TIME_REJECTED") from exc
        audit = self.store.workflow_audits.get(acceptance["audit_event_id"])
        audit_parent = {
            "tenant_id": str(row["tenant_id"]),
            "promotion_decision_id": row["payload"]["promotion_decision_id"],
            "intake_id": row["payload"]["intake_id"],
            "candidate_site_id": row["payload"]["candidate_site_id"],
            "site_score_job_id": str(row["job_id"]),
            "version": promotion["version"], "status": "COMPLETED",
        }
        if (not audit or audit["resource_type"] != "promotion_decision"
                or audit["resource_id"] != promotion["promotion_decision_id"]
                or audit["action"] != "candidate.promotion_completed"
                or audit["service_principal"] != "sitescore_worker"
                or audit["result"] != "SUCCEEDED"
                or audit["tenant_id"] != str(row["tenant_id"])
                or audit["correlation_id"] != str(row["correlation_id"])
                or any(audit["after_value"].get(key) != value for key, value in audit_parent.items())):
            raise JobFenceRejectedError("SITESCORE_ACCEPTANCE_AUDIT_REJECTED")
        return True

    def _accept(self, row: dict, intake: dict, promotion: dict, candidate: Any) -> None:
        before = deepcopy(promotion)
        self._promotion(promotion, "COMPLETED")
        audit_id = record_workflow_change(
            self.store, intake, resource_type="promotion_decision", before=before, after=promotion,
            action="candidate.promotion_completed", actor_id=row["payload"]["actor_id"],
            service_principal="sitescore_worker", correlation_id=str(row["correlation_id"]),
        )
        self.store.promotions[promotion["promotion_decision_id"]] = promotion
        payload = deepcopy(row["payload"])
        payload["worker_acceptance"] = {
            "tenant_id": str(row["tenant_id"]), "job_id": str(row["job_id"]),
            "intake_id": payload["intake_id"], "promotion_decision_id": payload["promotion_decision_id"],
            "candidate_site_id": payload["candidate_site_id"], "promotion_version": promotion["version"],
            "accepted_at": self._now().isoformat(), "audit_event_id": audit_id,
            "accepted_fence": row["fence_token"],
        }
        payload["parent_fingerprint"] = self._fingerprint(intake, promotion, candidate)
        self._update(row, payload=payload)
        self._event(row, "candidate.promotion_completed", "candidate_promotion_service", "PromotionChangedV1", {
            key: promotion[key] for key in ("promotion_decision_id", "intake_id", "listing_id", "status", "decision_type", "candidate_site_id", "site_score_job_id", "version")
        }, promotion=True)

    def _parent_ready(self, row: dict, promotion: dict, accepted: bool) -> bool:
        return accepted or promotion["status"] == "SCORE_QUEUED"

    def begin_attempt(self, job: JobRecord) -> ScoreAttempt | None:
        tenant = self._authenticate_reference(job)
        with self._unit(job.job_id, tenant) as (row, intake, promotion, candidate):
            if row["status"] == "SUCCEEDED":
                # A completed job must still authenticate its original delivery.
                expected = self._signed(job.job_id, tenant, row["payload"]["generation"])
                if not hmac.compare_digest(str(job.payload.get("attempt_token", "")), expected):
                    raise ScoreDeliveryDenied("SITESCORE_ATTEMPT_TOKEN_DENIED")
                return None
            current = self._delivery(job, row)
            accepted = self._accepted(row, promotion)
            if (row["status"] not in {"QUEUED", "RETRYING", "RUNNING"}
                    or not self._parent_ready(row, promotion, accepted)):
                raise JobFenceRejectedError("SITESCORE_PARENT_OR_STATE_CHANGED")
            now = self._now()
            if row["status"] == "RETRYING" and (not row["next_attempt_at"] or _time(row["next_attempt_at"]) > now):
                raise JobFenceRejectedError("SITESCORE_RETRY_NOT_DUE")
            if row["status"] == "RUNNING" and row["lease_expires_at"] and _time(row["lease_expires_at"]) > now:
                raise JobFenceRejectedError("SITESCORE_ATTEMPT_ALREADY_RUNNING")
            if row["cancellation_requested_at"]:
                # This authenticated worker claim is an attempt even though it
                # stops before inference. Keep the physical receipt and event
                # consistent without exceeding an already exhausted budget.
                if row["attempt"] < row["max_attempts"]:
                    self._update(row, attempt=row["attempt"] + 1, fence_token=int(row["fence_token"]) + 1)
                self._settle_cancelled(job, row, intake, current)
                return None
            if row["attempt"] >= row["max_attempts"]:
                self._settle_failed(job, row, intake, promotion, candidate, current,
                                    "SITESCORE_RETRY_BUDGET_EXHAUSTED", False, exhausted=True)
                return None
            fence = int(row["fence_token"]) + 1
            payload = deepcopy(row["payload"])
            payload["heartbeat_count"] = 0
            self._update(row, status="RUNNING", attempt=row["attempt"] + 1, fence_token=fence, payload=payload,
                         heartbeat_at=current.heartbeat_at, lease_expires_at=current.lease_expires_at,
                         lease_owner=current.locked_by, next_attempt_at=None)
            if self._fingerprint(intake, promotion, candidate) != row["payload"]["parent_fingerprint"]:
                # The old attempt cannot commit. A newly claimed delivery may
                # settle its stale checkpoint without overwriting changed input,
                # then an authorized manager can replay after fixing the cause.
                self._settle_failed(job, row, intake, promotion, candidate, current,
                                    "SITESCORE_PARENT_CHANGED", False)
                return None
            self._audit(row, intake, "sitescore.attempt_started", row["payload"]["actor_id"])
            try:
                self._current_inputs(tenant, candidate)
                if validate_score_admission(self.engine, intake, candidate) != row["payload"]["admission"]:
                    raise ScoreDeliveryDenied("SITESCORE_ADMISSION_CHANGED")
                if score_retry_policy() != row["payload"].get("retry_policy"):
                    raise ScoreDeliveryDenied("SITESCORE_RETRY_POLICY_CHANGED")
            except ScoreDeliveryDenied as exc:
                self._settle_failed(job, row, intake, promotion, candidate, current, str(exc), False)
                return None
            if not accepted:
                self._accept(row, intake, promotion, candidate)
            # The unit commits this acceptance receipt, audit and event before
            # the handler can enter external inference. Result state is separate.
            return ScoreAttempt(tenant, row["payload"]["candidate_site_id"],
                                _time(row["payload"]["prediction_origin_time"]), now,
                                {"fence": fence, "version": row["version"], "generation": row["payload"]["generation"],
                                 "fingerprint": row["payload"]["parent_fingerprint"]}, deepcopy(candidate))

    def _check_attempt(self, job: JobRecord, attempt: ScoreAttempt, row: dict, intake: dict, promotion: dict, candidate: Any):
        current = self._delivery(job, row)
        if (row["status"] != "RUNNING" or row["cancellation_requested_at"]
                or row["fence_token"] != attempt.token["fence"]
                or row["version"] != attempt.token["version"] + row["payload"].get("heartbeat_count", 0)
                or row["payload"]["generation"] != attempt.token["generation"]
                or not row["lease_expires_at"] or _time(row["lease_expires_at"]) <= self._now()
                or not self._accepted(row, promotion)
                or self._fingerprint(intake, promotion, candidate) != attempt.token["fingerprint"]):
            raise JobFenceRejectedError("SITESCORE_RESULT_FENCE_REJECTED")
        self._current_inputs(attempt.tenant_id, candidate)
        if validate_score_admission(self.engine, intake, candidate) != row["payload"]["admission"]:
            raise JobFenceRejectedError("SITESCORE_ADMISSION_CHANGED")
        if score_retry_policy() != row["payload"].get("retry_policy"):
            raise JobFenceRejectedError("SITESCORE_RETRY_POLICY_CHANGED")
        # Evidence locks may have blocked past the original lease deadline.
        # Recheck both leases against the DB clock before publishing anything.
        current = self._delivery(job, row)
        if _time(row["lease_expires_at"]) <= self._now():
            raise JobFenceRejectedError("SITESCORE_RESULT_FENCE_REJECTED")
        return current

    def pin_model_admission(self, job: JobRecord, attempt: ScoreAttempt, receipt: dict) -> None:
        """Persist current production model facts under the actual job fence."""
        if self._authenticate_reference(job) != attempt.tenant_id:
            raise ScoreDeliveryDenied("SITESCORE_ATTEMPT_TENANT_DENIED")
        with self._unit(job.job_id, attempt.tenant_id) as (row, intake, promotion, candidate):
            self._check_attempt(job, attempt, row, intake, promotion, candidate)
            existing = row["payload"].get("model_admission")
            if existing is not None and existing != receipt:
                raise ScoreDeliveryDenied("SITESCORE_CURRENT_MODEL_CHANGED")
            if existing is None:
                payload = deepcopy(row["payload"])
                payload["model_admission"] = deepcopy(receipt)
                self._update(row, payload=payload)
                attempt.token["version"] = row["version"] - payload.get("heartbeat_count", 0)
                self._audit(row, intake, "sitescore.model_admission_pinned", row["payload"]["actor_id"])

    def check_publication(self, job: JobRecord, attempt: ScoreAttempt) -> None:
        # Reentered only inside commit_success, after any held model guard may
        # have waited; fresh DB clock checks precede all result writes.
        with self._unit(job.job_id, attempt.tenant_id) as (row, intake, promotion, candidate):
            self._check_attempt(job, attempt, row, intake, promotion, candidate)

    def commit_success(self, job: JobRecord, attempt: ScoreAttempt, persist_callback: Callable[[], dict]) -> dict:
        if self._authenticate_reference(job) != attempt.tenant_id:
            raise ScoreDeliveryDenied("SITESCORE_ATTEMPT_TENANT_DENIED")
        with self._unit(job.job_id, attempt.tenant_id) as (row, intake, promotion, candidate):
            current = self._check_attempt(job, attempt, row, intake, promotion, candidate)
            result = persist_callback()
            if not all(result.get(key) for key in ("report_id", "report_version", "sitescore_run_id", "prediction_run_id", "model_version")):
                raise ScoreDeliveryValidationError("SITESCORE_RESULT_RECEIPT_INCOMPLETE")
            self._update(row, status="SUCCEEDED", result=result, acknowledged_at=self._now(),
                         lease_expires_at=None, lease_owner=None, last_error=None, next_attempt_at=None)
            self.queue.update_status(job.job_id, JobStatus.SUCCEEDED, expected_version=current.version, fence_token=current.fence_token)
            self._schedule_delivery(current, None, after_status_update=True)
            self._audit(row, intake, "sitescore.completed", row["payload"]["actor_id"])
            return self._receipt(row)

    def _failure_fence(self, job: JobRecord, attempt: ScoreAttempt, row: dict) -> JobRecord:
        """An owned failure has no inference/publication authority.

        Source/model withdrawal must stop results, while the authenticated
        attempt can still settle its own status under both current leases.
        """
        current = self._delivery(job, row)
        intake, promotion, candidate = self._parents(row, live=True)
        if (row["status"] != "RUNNING" or row["cancellation_requested_at"]
                or row["fence_token"] != attempt.token["fence"]
                or row["version"] != attempt.token["version"] + row["payload"].get("heartbeat_count", 0)
                or row["payload"]["generation"] != attempt.token["generation"]
                or not row["lease_expires_at"] or _time(row["lease_expires_at"]) <= self._now()
                or not self._accepted(row, promotion)
                or self._fingerprint(intake, promotion, candidate) != attempt.token["fingerprint"]):
            raise JobFenceRejectedError("SITESCORE_RESULT_FENCE_REJECTED")
        return current

    def fail_attempt(self, job: JobRecord, attempt: ScoreAttempt, error_code: str, retryable: bool) -> dict:
        if self._authenticate_reference(job) != attempt.tenant_id:
            raise ScoreDeliveryDenied("SITESCORE_ATTEMPT_TENANT_DENIED")
        with self._unit(job.job_id, attempt.tenant_id) as (row, intake, promotion, candidate):
            current = self._failure_fence(job, attempt, row)
            if retryable:
                try:
                    self._check_attempt(job, attempt, row, intake, promotion, candidate)
                except ScoreDeliveryDenied as exc:
                    error_code, retryable = str(exc), False
            if retryable and row["attempt"] < row["max_attempts"]:
                policy = row["payload"]["retry_policy"]
                due = self._now() + timedelta(seconds=policy["backoff_seconds"][row["attempt"] - 1])
                self._update(row, status="RETRYING", next_attempt_at=due,
                             last_error={"code": error_code, "retryable": True},
                             lease_expires_at=None, lease_owner=None)
                self.queue.update_status(job.job_id, JobStatus.QUEUED, expected_version=current.version,
                                         fence_token=current.fence_token, error_message=error_code,
                                         delivery_state=JobDeliveryState.RETRYING)
                self._schedule_delivery(current, due, after_status_update=True)
                self._audit(row, intake, "sitescore.retry_scheduled", row["payload"]["actor_id"])
            else:
                self._settle_failed(job, row, intake, promotion, candidate, current, error_code, retryable)
            return self._receipt(row)

    def _settle_cancelled(self, job: JobRecord, row: dict, intake: dict, current: JobRecord) -> None:
        self._update(row, status="CANCELLED", last_error={"code": "SITESCORE_CANCELLED", "retryable": False},
                     lease_expires_at=None, lease_owner=None, next_attempt_at=None)
        self.queue.update_status(job.job_id, JobStatus.CANCELLED, expected_version=current.version,
                                 fence_token=current.fence_token, error_message="SITESCORE_CANCELLED")
        self._schedule_delivery(current, None, after_status_update=True)
        # Cancellation is not a permanent validation error. The contract has no
        # promotion cancellation edge; preserve its checkpoint and candidate.
        self._audit(row, intake, "sitescore.cancelled", row["payload"]["actor_id"])

    def _settle_failed(self, job: JobRecord, row: dict, intake: dict, promotion: dict,
                       candidate: Any, current: JobRecord, error_code: str, retryable: bool,
                       *, exhausted: bool = False) -> None:
        exhausted = exhausted or row["attempt"] >= row["max_attempts"]
        dead_lettered_at = self._now() if exhausted else None
        self._update(row, status="DEAD_LETTER" if exhausted else "FAILED",
                     last_error={"code": error_code, "retryable": retryable},
                     lease_expires_at=None, lease_owner=None, dead_lettered_at=dead_lettered_at, next_attempt_at=None)
        self.queue.update_status(job.job_id, JobStatus.FAILED, expected_version=current.version,
                                 fence_token=current.fence_token, error_message=error_code,
                                 delivery_state=JobDeliveryState.DEAD_LETTER if exhausted else None)
        self._schedule_delivery(current, None, after_status_update=True)
        if not self._accepted(row, promotion):
            self._promotion(promotion, "SCORE_FAILED")
        failed = _with_candidate_status(candidate, "SCORING_FAILED")
        if candidate.candidate_site.site_status not in {"approved", "opened"}:
            self._repository(str(row["tenant_id"])).save_candidate(failed)
        self._event(row, "sitescore.failed", "sitescore_worker", "SiteScoreFailedV1", {
            "candidate_site_id": row["payload"]["candidate_site_id"], "site_score_job_id": job.job_id,
            "error_code": error_code, "retryable": retryable, "attempt": row["attempt"],
        })
        if exhausted:
            self._event(row, "job.dead_lettered", "job_platform", "JobDeadLetteredV1", {
                "job_id": job.job_id, "job_type": self.job_type,
                "checkpoint": row["checkpoint"], "attempt": row["attempt"],
                "error_code": error_code, "dead_lettered_at": dead_lettered_at.isoformat(),
            }, job_event=True)
        self._audit(row, intake, "sitescore.failed", row["payload"]["actor_id"], result="FAILED")

    def retry_score(self, job_id: str, *, tenant_id: str, actor_id: str, expected_version: int,
                    checkpoint: str, reason: str, override_retry_budget: bool, risk_acknowledged: bool,
                    correlation_id: str, request_key: str) -> dict:
        if checkpoint != "SCORE_QUEUED" or not reason.strip() or not request_key:
            raise ScoreDeliveryValidationError("SITESCORE_REPLAY_CHECKPOINT_REQUIRED")
        if override_retry_budget:
            raise ScoreDeliveryDenied("SITESCORE_REPLAY_INCIDENT_REFERENCE_REQUIRED")
        self._key()
        with self._unit(job_id, tenant_id) as (row, intake, promotion, candidate):
            accepted = self._accepted(row, promotion)
            if (row["version"] != expected_version or row["status"] != "FAILED"
                    or (not accepted and promotion["status"] != "SCORE_FAILED")):
                raise ScoreDeliveryConflict("SITESCORE_REPLAY_VERSION_OR_STATE_CONFLICT")
            if row["attempt"] >= row["max_attempts"]:
                raise ScoreDeliveryConflict("SITESCORE_RETRY_BUDGET_EXHAUSTED")
            self._current_inputs(tenant_id, candidate)
            admission = validate_score_admission(self.engine, intake, candidate)
            retry_policy = score_retry_policy()
            if retry_policy["max_attempts"] != row["max_attempts"]:
                raise ScoreDeliveryDenied("SITESCORE_REPLAY_BUDGET_CHANGE_DENIED")
            if not accepted:
                self._promotion(promotion, "SCORE_QUEUED")
            candidate = _with_candidate_status(candidate, "new")
            self._repository(tenant_id).save_candidate(candidate)
            payload = deepcopy(row["payload"])
            payload["generation"] += 1
            payload.pop("model_admission", None)
            payload["actor_id"] = actor_id
            payload["prediction_origin_time"] = self._now().isoformat()
            payload["admission"] = admission
            payload["retry_policy"] = retry_policy
            payload["parent_fingerprint"] = self._fingerprint(intake, promotion, candidate)
            token = self._signed(job_id, tenant_id, payload["generation"])
            self._update(row, status="QUEUED", payload=payload, fence_token=row["fence_token"] + 1,
                         cancellation_requested_at=None, last_error=None, next_attempt_at=None,
                         attempt_token_sha256=hashlib.sha256(token.encode()).hexdigest())
            current = self.queue.get(job_id)
            if current is None or current.status != JobStatus.FAILED:
                raise ScoreDeliveryConflict("SITESCORE_REPLAY_DELIVERY_CONFLICT")
            delivery_update = self.engine.execute(
                "UPDATE durable_jobs SET status=?,delivery_state=NULL,payload_json=?,version=version+1,"
                "fence_token=fence_token+1,locked_by=NULL,lease_expires_at=NULL,heartbeat_at=NULL,error_message=NULL,available_at=? "
                "WHERE job_id=? AND version=? AND fence_token=?",
                (JobStatus.QUEUED.value, json.dumps({"job_id": job_id, "tenant_id": tenant_id, "attempt_token": token}),
                 self._now().isoformat(), job_id, current.version, current.fence_token),
            )
            if delivery_update.rowcount != 1:
                raise JobFenceRejectedError("SITESCORE_REPLAY_DELIVERY_CAS_REJECTED")
            self._event(row, "job.replay_requested", "job_platform", "JobReplayRequestedV1", {
                "job_id": job_id, "checkpoint": checkpoint, "requested_by": actor_id,
                "reason": reason, "override_retry_budget": False,
            }, job_event=True)
            self._audit(row, intake, "sitescore.replay_requested", actor_id)
            return self._receipt(row)

    def heartbeat(self, job_id: str, expected_version: int, fence_token: int) -> int:
        current = self.queue.get(job_id)
        if current is None:
            raise JobFenceRejectedError("SITESCORE_JOB_UNAVAILABLE")
        tenant = self._authenticate_reference(current)
        with self._unit(job_id, tenant) as (row, intake, promotion, candidate):
            if row["status"] not in {"QUEUED", "RETRYING", "RUNNING"} or row["cancellation_requested_at"]:
                raise JobFenceRejectedError("SITESCORE_HEARTBEAT_STATE_REJECTED")
            if row["status"] == "RETRYING" and (not row["next_attempt_at"]
                    or _time(row["next_attempt_at"]) > self._now()):
                raise JobFenceRejectedError("SITESCORE_RETRY_NOT_DUE")
            if row["status"] == "RUNNING" and (not row["lease_expires_at"]
                    or _time(row["lease_expires_at"]) <= self._now()):
                raise JobFenceRejectedError("SITESCORE_HEARTBEAT_LEASE_EXPIRED")
            self._delivery(current, row)
            version = self.queue.heartbeat(job_id, expected_version, fence_token)
            updated = self.queue.get(job_id)
            if row["status"] == "RUNNING":
                payload = deepcopy(row["payload"])
                payload["heartbeat_count"] = payload.get("heartbeat_count", 0) + 1
                self._update(row, heartbeat_at=updated.heartbeat_at, lease_expires_at=updated.lease_expires_at,
                             payload=payload)
            return version

    def _update(self, row: dict, **changes: Any) -> None:
        allowed = {"status", "attempt", "fence_token", "heartbeat_at", "lease_expires_at", "lease_owner",
                   "payload", "result", "last_error", "acknowledged_at", "dead_lettered_at", "cancellation_requested_at", "attempt_token_sha256", "next_attempt_at"}
        if not set(changes) <= allowed:
            raise ValueError("Unsupported workflow job mutation")
        columns, values = [], []
        for key, value in changes.items():
            is_json = key in {"payload", "result", "last_error"}
            columns.append(f"{key}=?" + ("::jsonb" if is_json else ""))
            values.append(json.dumps(_jsonable(value)) if is_json and value is not None else _jsonable(value))
        result = self.engine.execute(
            "UPDATE workflow.jobs SET " + ",".join(columns) + ",version=version+1,updated_at=now() "  # nosec B608: identifiers restricted to the fixed allowlist above; every value is bound
            "WHERE job_id=? AND tenant_id=? AND version=? AND fence_token=?",
            (*values, str(row["job_id"]), str(row["tenant_id"]), row["version"], row["fence_token"]),
        )
        if result.rowcount != 1:
            raise JobFenceRejectedError("SITESCORE_JOB_CAS_REJECTED")
        row["_previous_receipt"] = self._audit_snapshot(row)
        row.update(changes)
        row["version"] += 1

    def _promotion(self, promotion: dict, status: str) -> None:
        allowed = {("SCORE_QUEUED", "COMPLETED"), ("SCORE_QUEUED", "SCORE_FAILED"), ("SCORE_FAILED", "SCORE_QUEUED")}
        if (promotion["status"], status) not in allowed:
            raise ScoreDeliveryConflict("SITESCORE_PROMOTION_STATE_CONFLICT")
        promotion["status"] = status
        promotion["version"] += 1
        self.store.promotions[promotion["promotion_decision_id"]] = promotion

    def _audit(self, row: dict, intake: dict, action: str, actor: str, *, result: str = "SUCCEEDED") -> None:
        service = ("candidate_promotion_service" if action == "sitescore.enqueue"
                   else "job_platform" if action == "sitescore.replay_requested" else "sitescore_worker")
        record_workflow_change(self.store, intake, resource_type="job", before=row.pop("_previous_receipt", None),
                               after=self._audit_snapshot(row), action=action, actor_id=actor, result=result,
                               service_principal=service, correlation_id=str(row["correlation_id"]))

    def _event(self, row: dict, event: str, producer: str, schema: str, payload: dict,
               *, promotion: bool = False, job_event: bool = False) -> None:
        aggregate = ("promotion_decision" if promotion else "job" if job_event else "candidate_site")
        aggregate_id = (row["payload"]["promotion_decision_id"] if promotion else str(row["job_id"]) if job_event
                        else row["payload"]["candidate_site_id"])
        self.outbox.save(DomainEvent(
            event_type=event, payload=payload, tenant_id=str(row["tenant_id"]),
            aggregate_type=aggregate, aggregate_id=aggregate_id,
            aggregate_version=payload["version"] if promotion else row["version"],
            partition_key=f"{row['tenant_id']}:{aggregate_id}", correlation_id=str(row["correlation_id"]),
            producer=producer, schema_ref=f"#/payloads/{schema}",
            sensitive_fields=({"sitescore.failed": ["payload.error_details"],
                               "job.dead_lettered": ["payload.last_error_details"]}.get(event, [])),
        ))
