from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from modules.notifications import ConsoleNotificationAdapter, NotificationService
from modules.opsboard.application.network_listings import (
    InMemoryAssistedIntakeRepository,
    NetworkListingService,
)
from shared.audit.events import AuditEvent
from shared.domain.events import DomainEvent
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import PersistenceBundle
from shared.infrastructure.persistence.job_queue import JobFenceRejectedError
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.jobs.queue import JobRecord, JobStatus, NonRetryableJobError
from shared.observability import AlertRouter, default_registry

logger = logging.getLogger("assisted-listing-intake-worker")

INTAKE_JOB_TYPE = "assisted-listing-intake"


def ensure_uuid(val: Any, default: str = "00000000-0000-0000-0000-000000000000") -> str:
    import uuid

    if not val:
        return default
    try:
        return str(uuid.UUID(str(val)))
    except ValueError:
        try:
            return str(uuid.uuid5(uuid.NAMESPACE_DNS, str(val)))
        except Exception:
            return default


class HeartbeatScope:
    def __init__(self, job_queue, job_id, fence_token, start_version, interval=15):
        self.job_queue = job_queue
        self.job_id = job_id
        self.fence_token = fence_token
        self.version = start_version
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.error = None

    def __enter__(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1.0)

    def _run(self):
        while not self.stop_event.wait(self.interval):
            with self.lock:
                try:
                    new_version = self.job_queue.heartbeat(
                        self.job_id, self.version, self.fence_token
                    )
                    self.version = new_version
                except Exception as exc:
                    self.error = exc
                    break


def handle_assisted_listing_intake(job: JobRecord, persistence: PersistenceBundle) -> None:
    """Run the intake pipeline stages asynchronously with leasing, heartbeats, and fencing."""

    # Load or initialize intake repository
    if persistence.is_durable:
        doc_store = SqliteDocumentStore(persistence.engine)
        intake_repo = DurableAssistedIntakeRepository(doc_store)
    else:
        intake_repo = InMemoryAssistedIntakeRepository()

    service = NetworkListingService(
        listing_repository=persistence.listing_repository,
        intake_repository=intake_repo,
    )

    # Load parameters
    payload = dict(job.payload)
    intake_id = payload.get("intake_id")
    url = payload.get("url")
    payload.get("heat_zone_id")
    payload.get("actor_role_id", "system")
    payload.get("actor_name", "Ingestion Job")

    if not intake_id or not url:
        raise ValueError("Intake job payload missing intake_id or url")

    # Fencing and version tracking
    expected_version = job.version
    fence_token = job.fence_token
    job_queue = persistence.job_queue

    # Define stage details: (soft timeout, hard timeout, max attempts)
    stage_rules = {
        "CHECKING_IDENTITY": (10, 30, 3),
        "CHECKING_SOURCE_POLICY": (5, 15, 3),
        "RETRIEVING": (30, 120, 5),
        "PARSING": (60, 300, 4),
        "MATCHING": (30, 120, 3),
        "CANDIDATE_CREATING": (10, 30, 3),
        "SCORE_QUEUED": (10, 30, 5),
        "OUTBOX_PUBLISH": (10, 60, 10),
    }

    # Start heartbeat thread background
    with HeartbeatScope(job_queue, job.job_id, fence_token, expected_version) as hb:
        # Check cancellation
        def check_cancellation_and_fence():
            if hb.error:
                raise hb.error
            # Verify latest state in database is not cancelled
            latest = job_queue.get(job.job_id)
            if latest and latest.status in (
                JobStatus.FAILED,
                JobStatus.SUCCEEDED,
                JobStatus.CANCELLED,
            ):
                raise JobFenceRejectedError("Job was cancelled or claimed by another worker")

        # Helper to execute a stage with soft/hard timeout and retry/backoff
        def run_stage(stage_name: str, stage_func):
            check_cancellation_and_fence()
            soft_to, hard_to, max_att = stage_rules.get(stage_name, (10, 30, 3))

            # Check attempts
            stage_attempts = payload.setdefault("stage_attempts", {})
            current_att = stage_attempts.get(stage_name, 0)
            if current_att >= max_att:
                # DLQ poison isolation
                msg = f"Stage {stage_name} exceeded max attempts ({max_att})"
                logger.error(msg)

                # Set DLQ count metric
                default_registry().set(
                    "dlq_message_count", 1.0, labels={"topic": "assisted-listing-intake.dlq"}
                )

                # Route & trigger alert
                notification_repo = persistence.notification_repository
                if notification_repo:
                    ns = NotificationService(
                        repository=notification_repo, adapter=ConsoleNotificationAdapter()
                    )
                    ar = AlertRouter(notification_service=ns)
                    ar.trigger_alert(
                        "dlq-spike", f"Job {job.job_id} stage {stage_name} exceeded max attempts"
                    )

                # Write outbox event
                try:
                    tenant_id_val = ensure_uuid(
                        payload.get("tenant_id", "00000000-0000-0000-0000-000000000000")
                    )
                    correlation_id_val = ensure_uuid(job.correlation_id)
                    dlq_event = DomainEvent(
                        event_type="job.dead_lettered",
                        payload={
                            "job_id": job.job_id,
                            "job_type": job.job_type,
                            "checkpoint": stage_name,
                            "attempt": current_att,
                            "error_code": "MAX_ATTEMPTS_EXCEEDED",
                            "dead_lettered_at": datetime.now(UTC).isoformat(),
                            "last_error_details": {"message": msg},
                        },
                        tenant_id=tenant_id_val,
                        aggregate_type="job",
                        aggregate_id=job.job_id,
                        aggregate_version=hb.version,
                        partition_key=f"{tenant_id_val}:{job.job_id}",
                        correlation_id=correlation_id_val,
                        producer="job_platform",
                        schema_ref="#/payloads/JobDeadLetteredV1",
                        sensitive_fields=["payload.last_error_details"],
                    )
                    persistence.outbox_repository.save(dlq_event)
                except Exception as outbox_exc:
                    logger.exception(
                        "Failed to write job.dead_lettered event to outbox: %s", outbox_exc
                    )

                raise NonRetryableJobError(msg)

            stage_attempts[stage_name] = current_att + 1
            payload["current_stage"] = stage_name

            # Save progress update with version/fence validation
            expected_version_val = hb.version
            job_queue.update_status(
                job.job_id,
                JobStatus.RUNNING,
                payload=payload,
                expected_version=expected_version_val,
                fence_token=fence_token,
            )
            # Update heartbeat version
            with hb.lock:
                hb.version += 1

            # Perform action inside timeout wrapper with local retry/backoff
            local_att = 0
            max_local_att = 3
            backoff_base = 1.0

            while True:
                local_att += 1
                stage_intake = service.get_intake(intake_id)
                service._append_processing_transition(
                    stage_intake,
                    to_stage=stage_name,
                    actor=payload.get("actor_name", "Assisted Intake Worker"),
                    correlation_id=job.correlation_id,
                    checkpoint=stage_name,
                    attempt=current_att + local_att,
                    timeout_seconds=hard_to,
                )
                intake_repo.save_intake(stage_intake)
                start_t = time.monotonic()
                result_container = []
                exception_container = []

                def thread_target(res_c=result_container, exc_c=exception_container):
                    try:
                        res = stage_func()
                        res_c.append(res)
                    except Exception as e:
                        exc_c.append(e)

                stage_thread = threading.Thread(target=thread_target, daemon=True)
                stage_thread.start()
                stage_thread.join(timeout=hard_to)

                duration = time.monotonic() - start_t

                if stage_thread.is_alive():
                    # Hung stage timeout
                    msg = f"Stage {stage_name} hard timeout exceeded: hung stage interrupted after {duration:.2f}s > {hard_to}s"
                    logger.error(msg)
                    exc = TimeoutError(msg)
                elif exception_container:
                    exc = exception_container[0]
                else:
                    # Success
                    if duration > soft_to:
                        logger.warning(
                            f"Stage {stage_name} soft timeout exceeded: {duration:.2f}s > {soft_to}s"
                        )
                    return result_container[0] if result_container else None

                # Handle failure
                logger.warning(
                    f"Stage {stage_name} failed (local attempt {local_att}/{max_local_att}): {exc}"
                )

                if local_att < max_local_att:
                    sleep_time = backoff_base * (2 ** (local_att - 1))
                    logger.info(f"Retrying stage {stage_name} in {sleep_time}s...")
                    time.sleep(sleep_time)
                    check_cancellation_and_fence()
                else:
                    # Emit failure metrics & audit events
                    try:
                        if stage_name == "RETRIEVING":
                            policy_id = policy.source_id if "policy" in locals() else "unknown"
                            default_registry().increment(
                                "external_connector_failure_count", labels={"source": policy_id}
                            )

                            persistence.audit_log.record(
                                AuditEvent(
                                    event_type="intake.retrieval_failed",
                                    actor=payload.get("actor_name", "system"),
                                    action="retrieve",
                                    resource=f"intake/{intake_id}",
                                    outcome="failure",
                                    correlation_id=job.correlation_id,
                                    job_id=job.job_id,
                                    metadata={"url": url, "error": str(exc)},
                                )
                            )
                        elif stage_name == "PARSING":
                            persistence.audit_log.record(
                                AuditEvent(
                                    event_type="intake.parsing_failed",
                                    actor=payload.get("actor_name", "system"),
                                    action="parse",
                                    resource=f"intake/{intake_id}",
                                    outcome="failure",
                                    correlation_id=job.correlation_id,
                                    job_id=job.job_id,
                                    metadata={"url": url, "error": str(exc)},
                                )
                            )
                        elif stage_name == "MATCHING":
                            persistence.audit_log.record(
                                AuditEvent(
                                    event_type="intake.matching_failed",
                                    actor=payload.get("actor_name", "system"),
                                    action="match",
                                    resource=f"intake/{intake_id}",
                                    outcome="failure",
                                    correlation_id=job.correlation_id,
                                    job_id=job.job_id,
                                    metadata={"url": url, "error": str(exc)},
                                )
                            )
                    except Exception as audit_exc:
                        logger.exception("Failed to write failure audit log: %s", audit_exc)

                    raise exc

        # -------------------------------------------------------------
        # 1. Identity Check Stage
        # -------------------------------------------------------------
        def do_identity_check():
            from modules.external_data.application.assisted_intake import normalize_url

            canon_url = normalize_url(url)

            # Look for existing intake that has exact match or READY state
            existing_ready = None
            intakes = service.list_intakes()
            for item in intakes:
                if item.get("canonicalUrl") == canon_url and item.get("stage") == "READY":
                    existing_ready = item
                    break

            if existing_ready:
                return "READY", existing_ready
            return "NEXT", None

        outcome, existing = run_stage("CHECKING_IDENTITY", do_identity_check)
        if outcome == "READY":
            intake = service.get_intake(intake_id)
            intake["matchResult"] = {
                "outcome": "EXACT_DUPLICATE",
                "outcomeLabel": "完全重複",
                "confidence": 1.0,
                "targetListingId": existing.get("id"),
            }
            service._record_match_case(
                intake=intake,
                match_result=intake["matchResult"],
            )
            service._append_processing_transition(
                intake,
                to_stage="READY",
                actor=payload.get("actor_name", "Assisted Intake Worker"),
                correlation_id=job.correlation_id,
                checkpoint="CHECKING_IDENTITY",
                attempt=job.attempts,
                timeout_seconds=30,
                reason_code="EXACT_SOURCE_IDENTITY",
            )
            intake_repo.save_intake(intake)
            return

        # -------------------------------------------------------------
        # 2. Source Policy Evaluation
        # -------------------------------------------------------------
        def do_policy_eval():
            from modules.external_data.application.assisted_intake import resolve_source_policy

            policy_val = resolve_source_policy(url)
            return policy_val

        policy = run_stage("CHECKING_SOURCE_POLICY", do_policy_eval)

        # Apply policy
        intake = service.get_intake(intake_id)
        if policy.quarantines or policy.policy in {"POLICY_UNKNOWN", "SOURCE_BLOCKED"}:
            intake["sourceId"] = policy.source_id
            intake["policy"] = policy.policy
            intake["policyLabel"] = policy.policy_label
            intake["policyReason"] = policy.policy_reason
            intake["matchResult"] = {
                "outcome": "QUARANTINED",
                "outcomeLabel": "已隔離",
                "confidence": 0.0,
                "summary": f"依來源政策 {policy.policy} 予以隔離：{policy.policy_reason}",
            }
            service._record_match_case(
                intake=intake,
                match_result=intake["matchResult"],
            )
            service._append_processing_transition(
                intake,
                to_stage="QUARANTINED",
                actor=payload.get("actor_name", "Assisted Intake Worker"),
                correlation_id=job.correlation_id,
                checkpoint="CHECKING_SOURCE_POLICY",
                attempt=job.attempts,
                timeout_seconds=15,
                reason_code=policy.policy,
            )
            intake_repo.save_intake(intake)

            try:
                persistence.audit_log.record(
                    AuditEvent(
                        event_type="intake.quarantined",
                        actor=payload.get("actor_name", "system"),
                        action="quarantine",
                        resource=f"intake/{intake_id}",
                        outcome="failure",
                        correlation_id=job.correlation_id,
                        job_id=job.job_id,
                        metadata={
                            "policy": policy.policy,
                            "reason": policy.policy_reason,
                        },
                    )
                )
            except Exception as audit_exc:
                logger.exception("Failed to record quarantine audit event: %s", audit_exc)
            return
        elif policy.policy in {"ASSISTED_ENTRY_ONLY", "AUTH_REQUIRED"}:
            intake["sourceId"] = policy.source_id
            intake["policy"] = policy.policy
            intake["policyLabel"] = policy.policy_label
            intake["policyReason"] = policy.policy_reason
            required_cells = {
                "address": "地址",
                "rent": "租金",
                "areaPing": "坪數",
            }
            for field_key, label in required_cells.items():
                intake.setdefault("parsedFields", {}).setdefault(
                    field_key,
                    {
                        "key": field_key,
                        "label": label,
                        "sourceValue": None,
                        "normalizedValue": None,
                        "correctedValue": None,
                        "correctionReason": None,
                        "identity": True,
                        "lowConfidence": True,
                        "sourceSnapshotId": None,
                        "parserVersion": None,
                    },
                )
            service._append_processing_transition(
                intake,
                to_stage="AWAITING_ASSISTED_ENTRY",
                actor=payload.get("actor_name", "Assisted Intake Worker"),
                correlation_id=job.correlation_id,
                checkpoint="CHECKING_SOURCE_POLICY",
                attempt=job.attempts,
                timeout_seconds=15,
                reason_code=policy.policy,
            )
            intake_repo.save_intake(intake)
            return

        # -------------------------------------------------------------
        # 3. Retrieval
        # -------------------------------------------------------------
        def do_retrieval():
            import json
            import os

            from modules.external_data.application.assisted_intake import normalize_url
            from modules.external_data.application.source_snapshots import (
                build_source_snapshot_service,
            )
            from modules.external_data.security import redact_sensitive_snapshot
            from modules.external_data.security.assisted_listing_retrieval import (
                RetrievalSecurityGate,
            )

            snapshot_service = build_source_snapshot_service(persistence, doc_store)
            gate = RetrievalSecurityGate(source_snapshot_service=snapshot_service)

            canon_url = normalize_url(url)
            resolved_tenant_id = payload.get("tenant_id") or "00000000-0000-0000-0000-000000000000"
            
            # Fetch page through security gate
            retrieval_res = gate.fetch(
                canon_url,
                tenant_id=resolved_tenant_id,
                source_id=policy.source_id,
                policy=policy.policy,
                retrieval_method="server_http",
            )
            if not retrieval_res.ok:
                raise RuntimeError(
                    f"Retrieval failed: {retrieval_res.failure.summary if retrieval_res.failure else 'unknown'}"
                )
            
            raw_dict = json.loads(retrieval_res.body.decode("utf-8"))
            redacted_raw = redact_sensitive_snapshot(raw_dict)
            redacted_data = json.dumps(redacted_raw).encode("utf-8")
            
            snapshot_bucket = os.environ.get("ODP_SNAPSHOT_BUCKET", "").strip() or "taiwan-snapshots"
            snapshot_id = snapshot_service.create_snapshot(
                tenant_id=resolved_tenant_id,
                intake_id=intake_id,
                source_id=policy.source_id,
                raw_data=retrieval_res.body,
                original_url=url,
                canonical_url=canon_url,
                media_type="application/json",
                capture_method="SERVER_RETRIEVAL",
                retention_class="STANDARD",
                encryption_key_ref="kms://default-key",
                observed_at=datetime.now(UTC),
                captured_at=datetime.now(UTC),
                bucket=snapshot_bucket,
                redacted_data=redacted_data,
            )
            
            return redacted_raw, snapshot_id, datetime.now(UTC).isoformat().replace("+00:00", "Z")

        raw_snapshot, snapshot_id, captured_at = run_stage("RETRIEVING", do_retrieval)

        # -------------------------------------------------------------
        # 4. Parsing
        # -------------------------------------------------------------
        def do_parsing():
            from modules.external_data.application.assisted_intake import (
                RetrievalResult,
                parse_snapshot,
            )

            retrieval = RetrievalResult(
                snapshot_id=snapshot_id,
                captured_at=captured_at,
                raw=raw_snapshot,
            )

            parsed_fields_val = parse_snapshot(retrieval)
            return parsed_fields_val

        parsed_fields = run_stage("PARSING", do_parsing)

        # Check required fields
        from modules.external_data.application.assisted_intake import (
            ASSISTED_ENTRY_REQUIRED_FIELDS,
            effective_fields,
        )

        effective_vals = effective_fields(parsed_fields)
        has_all_required = True
        for rf in ASSISTED_ENTRY_REQUIRED_FIELDS:
            val = effective_vals.get(rf)
            if val in (None, ""):
                has_all_required = False
                break
            if rf in ("rent", "areaPing"):
                try:
                    if float(val) <= 0:
                        has_all_required = False
                        break
                except (ValueError, TypeError):
                    has_all_required = False
                    break

        intake = service.get_intake(intake_id)
        intake["rawSnapshot"] = raw_snapshot
        intake["snapshotId"] = snapshot_id
        intake["capturedAt"] = captured_at
        intake["parsedFields"] = parsed_fields
        intake["sourceId"] = policy.source_id
        intake["policy"] = policy.policy
        intake["policyLabel"] = policy.policy_label
        intake["policyReason"] = policy.policy_reason
        from modules.external_data.application.assisted_intake import PARSER_VERSION
        intake["parserVersion"] = PARSER_VERSION

        if not has_all_required:
            service._append_processing_transition(
                intake,
                to_stage="AWAITING_ASSISTED_ENTRY",
                actor=payload.get("actor_name", "Assisted Intake Worker"),
                correlation_id=job.correlation_id,
                checkpoint="PARSING",
                attempt=job.attempts,
                timeout_seconds=300,
                reason_code="PARSER_PARTIAL",
            )
            intake_repo.save_intake(intake)
            return
        intake_repo.save_intake(intake)

        # -------------------------------------------------------------
        # 5. Matching
        # -------------------------------------------------------------
        def do_matching():
            from modules.external_data.application.assisted_intake import (
                content_fingerprint,
                match_listing,
                normalize_url,
            )

            canon_url = normalize_url(url)
            fingerprint = content_fingerprint(effective_vals)

            listings = service._get_match_listings()
            match_res_val = match_listing(
                values=effective_vals,
                canonical_url=canon_url,
                source_id=policy.source_id,
                fingerprint=fingerprint,
                listings=listings,
            )
            return match_res_val

        match_res = run_stage("MATCHING", do_matching)

        intake = service.get_intake(intake_id)
        intake["matchResult"] = match_res.to_dict()
        service._record_match_case(
            intake=intake,
            match_result=intake["matchResult"],
            submitted_values=effective_vals,
        )
        final_stage = (
            "NEEDS_REVIEW" if match_res.outcome == "POSSIBLE_MATCH" else "READY"
        )
        service._append_processing_transition(
            intake,
            to_stage=final_stage,
            actor=payload.get("actor_name", "Assisted Intake Worker"),
            correlation_id=job.correlation_id,
            checkpoint="MATCHING",
            attempt=job.attempts,
            timeout_seconds=120,
        )

        intake_repo.save_intake(intake)
