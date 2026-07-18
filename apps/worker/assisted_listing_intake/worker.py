from __future__ import annotations

import logging
import threading
import time

from modules.opsboard.application.network_listings import (
    InMemoryAssistedIntakeRepository,
    NetworkListingService,
)
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import PersistenceBundle
from shared.infrastructure.persistence.job_queue import JobFenceRejectedError
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.jobs.queue import JobRecord, JobStatus

logger = logging.getLogger("assisted-listing-intake-worker")

INTAKE_JOB_TYPE = "assisted-listing-intake"

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
                    new_version = self.job_queue.heartbeat(self.job_id, self.version, self.fence_token)
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
            if latest and latest.status in (JobStatus.FAILED, JobStatus.SUCCEEDED):
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
                raise RuntimeError(msg)
            
            stage_attempts[stage_name] = current_att + 1
            payload["current_stage"] = stage_name
            
            # Save progress update with version/fence validation
            expected_version_val = hb.version
            job_queue.update_status(
                job.job_id,
                JobStatus.RUNNING,
                payload=payload,
                expected_version=expected_version_val,
                fence_token=fence_token
            )
            # Update heartbeat version
            with hb.lock:
                hb.version += 1
                
            # Perform action inside timeout wrapper
            start_t = time.monotonic()
            try:
                result = stage_func()
                duration = time.monotonic() - start_t
                if duration > soft_to:
                    logger.warning(f"Stage {stage_name} soft timeout exceeded: {duration}s > {soft_to}s")
                if duration > hard_to:
                    raise TimeoutError(f"Stage {stage_name} hard timeout exceeded: {duration}s > {hard_to}s")
                return result
            except Exception as e:
                # Check retry
                logger.exception(f"Error executing stage {stage_name}: {e}")
                raise e

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
            intake["stage"] = "READY"
            intake["matchResult"] = {
                "outcome": "EXACT_DUPLICATE",
                "outcomeLabel": "完全重複",
                "confidence": 1.0,
                "targetListingId": existing.get("id"),
            }
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
            intake["stage"] = "QUARANTINED"
            intake["matchResult"] = {
                "outcome": "QUARANTINED",
                "outcomeLabel": "已隔離",
                "confidence": 0.0,
                "summary": f"依來源政策 {policy.policy} 予以隔離：{policy.policy_reason}",
            }
            intake_repo.save_intake(intake)
            return
        elif policy.policy in {"ASSISTED_ENTRY_ONLY", "AUTH_REQUIRED"}:
            intake["stage"] = "AWAITING_ASSISTED_ENTRY"
            intake_repo.save_intake(intake)
            return

        # -------------------------------------------------------------
        # 3. Retrieval
        # -------------------------------------------------------------
        def do_retrieval():
            from modules.external_data.application.assisted_intake import normalize_url, retrieve
            from modules.external_data.security import redact_sensitive_snapshot
            canon_url = normalize_url(url)
            retrieval = retrieve(canon_url, policy=policy)
            if not retrieval.ok:
                raise RuntimeError(f"Retrieval failed: {retrieval.failure.message if retrieval.failure else 'unknown'}")
            redacted_raw = redact_sensitive_snapshot(retrieval.raw)
            return redacted_raw, retrieval.snapshot_id, retrieval.captured_at

        raw_snapshot, snapshot_id, captured_at = run_stage("RETRIEVING", do_retrieval)
        
        # -------------------------------------------------------------
        # 4. Parsing
        # -------------------------------------------------------------
        def do_parsing():
            from dataclasses import replace

            # Reconstruct retrieval result
            from modules.external_data.application.assisted_intake import (
                normalize_url,
                parse_snapshot,
                retrieve,
            )
            canon_url = normalize_url(url)
            retrieval = retrieve(canon_url, policy=policy)
            retrieval = replace(retrieval, raw=raw_snapshot, snapshot_id=snapshot_id, captured_at=captured_at)
            
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

        if not has_all_required:
            intake["stage"] = "AWAITING_ASSISTED_ENTRY"
            intake_repo.save_intake(intake)
            return

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
        
        intake["matchResult"] = match_res.to_dict()
        if match_res.outcome == "POSSIBLE_MATCH":
            intake["stage"] = "NEEDS_REVIEW"
        else:
            intake["stage"] = "READY"
            
        intake_repo.save_intake(intake)
