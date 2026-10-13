"""Original promotion contract timing on actual isolated PostgreSQL.

Synthetic sources and model runtimes probe transaction boundaries only. They
do not admit a production source, model, execution budget or release policy.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest

from models.shared_ml.production_runtime import ProductionModelInputError
from modules.sitescore.tests.test_sitescore_production_runtime import RecordingRuntime
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobRequest, JobStatus
from tests.intake_registry_fixture import TENANT
from tests.integration.test_intake_registry_calendar_postgresql import _bundle
from tests.integration.test_intake_score_delivery_postgresql import (
    _approved_score,
    _assert_no_result,
    _claim,
    _physical,
    _promotion_client,
    _review,
    _reviewer_headers,
    _score_proposal,
)
from tests.integration.test_intake_score_delivery_postgresql import (
    explicit_local_signing_key as explicit_local_signing_key,
)
from tests.integration.test_operator_intake_workflow_postgresql import (
    bridge_database as bridge_database,
)

pytestmark = pytest.mark.requires_live_env


def _promotion(bundle, promotion_id):
    bundle.assisted_intake_store.refresh(TENANT)
    return bundle.assisted_intake_store.promotions[promotion_id]


def _completion_events(bundle):
    return [event for event in bundle.outbox_repository.get_unpublished_events()
            if event.event_type == "candidate.promotion_completed"]


def _hold_retry_in_future(db, job_id):
    with db.connect(autocommit=False) as conn:
        due = conn.execute("UPDATE workflow.jobs SET next_attempt_at=now()+interval '1 hour' WHERE job_id=%s RETURNING next_attempt_at", (job_id,)).fetchone()[0]
        conn.execute("UPDATE odp_runtime.durable_jobs SET available_at=%s WHERE job_id=%s", (due, job_id))


def _make_retry_due(db, job_id):
    with db.connect(autocommit=False) as conn:
        due = conn.execute("UPDATE workflow.jobs SET next_attempt_at=now()-interval '1 second' WHERE job_id=%s RETURNING next_attempt_at", (job_id,)).fetchone()[0]
        conn.execute("UPDATE odp_runtime.durable_jobs SET available_at=%s WHERE job_id=%s", (due, job_id))


def _claim_all_other_jobs(bundle, score_job_id):
    claimed = []
    for _ in range(60):
        job = bundle.job_queue.claim_next(worker_id="synthetic-other-eligible-work")
        if job is None:
            return claimed
        assert job.job_id != score_job_id, "A future retry must never consume a delivery claim"
        assert job.job_type in {"canonical-audit-delivery", "synthetic-other-ready"}
        claimed.append(job)
    raise AssertionError("Synthetic fixture unexpectedly produced unbounded other jobs")


def test_worker_acceptance_is_committed_before_inference_finishes(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    entered, release = Event(), Event()

    class BlockingRuntime(RecordingRuntime):
        def infer(self, **kwargs):
            entered.set()
            assert release.wait(30), "The test must release its synthetic model barrier"
            return super().infer(**kwargs)

    bundle = _bundle(bridge_database, monkeypatch)
    observer = None
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id, candidate_id = receipt["site_score_job_id"], receipt["candidate_site_id"]
        assert _promotion(bundle, receipt["promotion_decision_id"])["status"] == "SCORE_QUEUED"
        claimed = _claim(bundle, job_id)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(handle_intake_score, claimed, bundle, model_runtime=BlockingRuntime())
            try:
                assert entered.wait(30), "Worker never reached the inference boundary"
                # This opens another database connection while inference is
                # blocked; an in-memory mutation or uncommitted SQL cannot pass.
                observer = _bundle(bridge_database, monkeypatch)
                promotion = _promotion(observer, receipt["promotion_decision_id"])
                assert promotion["status"] == "COMPLETED"
                running = _physical(bridge_database, job_id)
                assert running["status"] == "RUNNING"
                acceptance = running["payload"]["worker_acceptance"]
                assert acceptance["tenant_id"] == TENANT
                assert acceptance["job_id"] == job_id
                assert acceptance["intake_id"] == receipt["intake_id"]
                assert acceptance["promotion_decision_id"] == promotion["promotion_decision_id"]
                assert acceptance["candidate_site_id"] == candidate_id
                assert acceptance["promotion_version"] == promotion["version"]
                assert acceptance["accepted_fence"] == running["fence_token"]
                assert datetime.fromisoformat(acceptance["accepted_at"]) <= datetime.now(UTC)
                assert observer.engine.query_one("SELECT audit_event_id FROM audit.audit_events WHERE audit_event_id=?",
                                                 (acceptance["audit_event_id"],)) is not None
                assert len(_completion_events(observer)) == 1
                _assert_no_result(observer, candidate_id)
            finally:
                release.set()
                future.result(timeout=30)
        assert _physical(bridge_database, job_id)["status"] == "SUCCEEDED"
        assert len(_completion_events(bundle)) == 1
    finally:
        release.set()
        if observer is not None:
            observer.engine.close()
        bundle.engine.close()


def test_transient_timeout_schedules_retry_without_terminal_failure(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    class TimeoutRuntime(RecordingRuntime):
        def infer(self, **kwargs):
            raise TimeoutError("Explicit synthetic transient model timeout")

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        _, _, receipt = _approved_score(bridge_database, bundle, client)
        job_id = receipt["site_score_job_id"]
        before = _physical(bridge_database, job_id)
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=TimeoutRuntime())
        retrying = _physical(bridge_database, job_id)
        assert retrying["status"] == "RETRYING"
        assert retrying["attempt"] == 1 < retrying["max_attempts"]
        assert datetime.fromisoformat(retrying["next_attempt_at"]) > datetime.now(UTC)
        assert retrying["payload"]["generation"] == before["payload"]["generation"]
        assert retrying["payload"]["prediction_origin_time"] == before["payload"]["prediction_origin_time"]
        delivery = bundle.job_queue.get(job_id)
        assert delivery.status == JobStatus.QUEUED
        assert delivery.delivery_state == JobDeliveryState.RETRYING
        assert _promotion(bundle, receipt["promotion_decision_id"])["status"] == "COMPLETED"
        candidate = bundle.listing_repository_for_tenant(TENANT).list_candidates()[0]
        assert candidate.candidate_site.site_status != "SCORING_FAILED"
        _assert_no_result(bundle, receipt["candidate_site_id"])
        public = client.get(f"/api/v1/jobs/{job_id}/receipt", headers=_reviewer_headers())
        assert public.status_code == 200, public.text
        assert public.json()["status"] == "QUEUED"
        assert public.json()["delivery_state"] == "RETRYING"
        events = bundle.outbox_repository.get_unpublished_events()
        assert not any(event.event_type in {"sitescore.failed", "job.dead_lettered", "job.replay_requested"}
                       for event in events)
    finally:
        bundle.engine.close()


def test_committed_acceptance_survives_restart_and_expired_claim_recovery(bridge_database, monkeypatch):
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        first = _claim(bundle, job_id)
        assert IntakeScoreJobRepository(bundle).begin_attempt(first) is not None
        accepted = deepcopy(_promotion(bundle, receipt["promotion_decision_id"]))
        assert accepted["status"] == "COMPLETED"
        assert len(_completion_events(bundle)) == 1
        _assert_no_result(bundle, receipt["candidate_site_id"])
        bundle.engine.close()
        bundle = _bundle(bridge_database, monkeypatch)
        assert _promotion(bundle, receipt["promotion_decision_id"]) == accepted
        with bridge_database.connect() as conn:
            conn.execute("UPDATE workflow.jobs SET lease_expires_at=now()-interval '1 second' WHERE job_id=%s", (job_id,))
        bundle.engine.execute("UPDATE durable_jobs SET lease_expires_at=? WHERE job_id=?", ("2026-01-01T00:00:00+00:00", job_id))
        reclaimed = _claim(bundle, job_id)
        assert reclaimed.fence_token > first.fence_token
        handle_intake_score(reclaimed, bundle, model_runtime=RecordingRuntime())
        assert _physical(bridge_database, job_id)["status"] == "SUCCEEDED"
        assert _physical(bridge_database, job_id)["attempt"] == 2
        assert _promotion(bundle, receipt["promotion_decision_id"]) == accepted
        assert len(_completion_events(bundle)) == 1
    finally:
        bundle.engine.close()


def test_accepted_permanent_failure_and_authorized_replay_never_reopen_promotion(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    class InvalidRuntime(RecordingRuntime):
        def infer(self, **kwargs):
            raise ProductionModelInputError("Explicit synthetic permanent failure after acceptance")

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        _, _, receipt = _approved_score(bridge_database, bundle, client)
        job_id, promotion_id = receipt["site_score_job_id"], receipt["promotion_decision_id"]
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=InvalidRuntime())
        failed = _physical(bridge_database, job_id)
        assert failed["status"] == "FAILED"
        accepted = deepcopy(_promotion(bundle, promotion_id))
        assert accepted["status"] == "COMPLETED"
        candidate = bundle.listing_repository_for_tenant(TENANT).list_candidates()[0]
        assert candidate.candidate_site.site_status == "SCORING_FAILED"
        _assert_no_result(bundle, receipt["candidate_site_id"])
        replay = client.post(
            f"/api/v1/jobs/{job_id}/retry",
            json={"checkpoint": "SCORE_QUEUED", "reason": "Manager verified the repaired synthetic model input"},
            headers={**_reviewer_headers("synthetic-accepted-replay"), "If-Match": f'W/"{failed["version"]}"'},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["job_id"] == job_id
        assert _promotion(bundle, promotion_id) == accepted
        queued = _physical(bridge_database, job_id)
        assert queued["payload"]["generation"] == failed["payload"]["generation"] + 1
        assert len(bundle.listing_repository_for_tenant(TENANT).list_candidates()) == 1
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=RecordingRuntime())
        assert _physical(bridge_database, job_id)["status"] == "SUCCEEDED"
        assert _promotion(bundle, promotion_id) == accepted
        assert len(_completion_events(bundle)) == 1
    finally:
        bundle.engine.close()


def test_retry_due_time_survives_restart_and_two_replicas_claim_only_once(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    class TimeoutRuntime(RecordingRuntime):
        def infer(self, **kwargs):
            raise TimeoutError("Explicit synthetic transient timeout")

    bundle = _bundle(bridge_database, monkeypatch)
    replica = None
    try:
        client = _promotion_client(bundle)
        _, _, receipt = _approved_score(bridge_database, bundle, client)
        job_id = receipt["site_score_job_id"]
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=TimeoutRuntime())
        retrying = _physical(bridge_database, job_id)
        assert retrying["status"] == "RETRYING"
        _hold_retry_in_future(bridge_database, job_id)
        forbidden = client.post(
            f"/api/v1/jobs/{job_id}/retry",
            json={"checkpoint": "SCORE_QUEUED", "reason": "An automatic retry must not be manually expedited"},
            headers={**_reviewer_headers("synthetic-no-expedite"), "If-Match": f'W/"{retrying["version"]}"'},
        )
        assert forbidden.status_code == 409, forbidden.text
        accepted = deepcopy(_promotion(bundle, receipt["promotion_decision_id"]))
        bundle.engine.close()
        bundle = _bundle(bridge_database, monkeypatch)
        replica = _bundle(bridge_database, monkeypatch)
        other, _ = bundle.job_queue.enqueue(JobRequest(job_type="synthetic-other-ready", payload={}), correlation_id="synthetic-other-ready")
        claimed = _claim_all_other_jobs(bundle, job_id)
        assert any(job.job_id == other.job_id for job in claimed), "A future retry must not starve eligible unrelated work"
        assert _physical(bridge_database, job_id)["attempt"] == 1
        _make_retry_due(bridge_database, job_id)
        barrier = Barrier(2)

        def compete(index, active):
            barrier.wait(timeout=10)
            return index, active.job_queue.claim_next(worker_id=f"synthetic-retry-replica-{index}")

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(compete, index, active) for index, active in enumerate((bundle, replica))]
            results = [future.result(timeout=20) for future in futures]
        winners = [(index, job) for index, job in results if job is not None]
        assert len(winners) == 1 and winners[0][1].job_id == job_id
        winner_index, winner = winners[0]
        handle_intake_score(winner, (bundle, replica)[winner_index], model_runtime=RecordingRuntime())
        finished = _physical(bridge_database, job_id)
        assert finished["status"] == "SUCCEEDED" and finished["attempt"] == 2
        assert finished["payload"]["generation"] == retrying["payload"]["generation"]
        assert _promotion(bundle, receipt["promotion_decision_id"]) == accepted
        assert len(_completion_events(bundle)) == 1
    finally:
        if replica is not None:
            replica.engine.close()
        bundle.engine.close()


def test_old_attempt_cannot_commit_after_a_transient_retry_was_scheduled(bridge_database, monkeypatch):
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        claimed = _claim(bundle, job_id)
        jobs = IntakeScoreJobRepository(bundle)
        attempt = jobs.begin_attempt(claimed)
        assert attempt is not None
        jobs.fail_attempt(claimed, attempt, "SYNTHETIC_TRANSIENT_TIMEOUT", True)
        retrying = _physical(bridge_database, job_id)
        assert retrying["status"] == "RETRYING"
        callback_called = []
        with pytest.raises(JobFenceRejectedError):
            jobs.commit_success(claimed, attempt, lambda: callback_called.append(True))
        assert callback_called == []
        assert _physical(bridge_database, job_id) == retrying
        assert _promotion(bundle, receipt["promotion_decision_id"])["status"] == "COMPLETED"
        assert len(_completion_events(bundle)) == 1
        _assert_no_result(bundle, receipt["candidate_site_id"])
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("fault", ["completion_event", "acceptance_audit", "parent_flush"])
def test_worker_acceptance_write_fault_rolls_back_the_entire_checkpoint(bridge_database, monkeypatch, fault):
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository

    bundle = _bundle(bridge_database, monkeypatch)
    observer = None
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        claimed = _claim(bundle, job_id)
        jobs = IntakeScoreJobRepository(bundle)
        before = _physical(bridge_database, job_id)
        delivery_before = deepcopy(bundle.job_queue.get(job_id))
        promotion_before = deepcopy(_promotion(bundle, receipt["promotion_decision_id"]))
        with monkeypatch.context() as patch:
            if fault == "completion_event":
                original = bundle.outbox_repository.save

                def injected(event):
                    result = original(event)
                    if event.event_type == "candidate.promotion_completed":
                        raise RuntimeError("Synthetic acceptance write fault")
                    return result

                patch.setattr(bundle.outbox_repository, "save", injected)
            elif fault == "acceptance_audit":
                from modules.listing.application import intake_score_delivery

                original = intake_score_delivery.record_workflow_change

                def injected(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if kwargs.get("action") == "candidate.promotion_completed":
                        raise RuntimeError("Synthetic acceptance write fault")
                    return result

                patch.setattr(intake_score_delivery, "record_workflow_change", injected)
            else:
                original = bundle.assisted_intake_store.flush

                def injected():
                    original()
                    raise RuntimeError("Synthetic acceptance write fault")

                patch.setattr(bundle.assisted_intake_store, "flush", injected)
            with pytest.raises(RuntimeError, match="Synthetic acceptance write fault"):
                jobs.begin_attempt(claimed)
        observer = _bundle(bridge_database, monkeypatch)
        assert _physical(bridge_database, job_id) == before
        assert observer.job_queue.get(job_id) == delivery_before
        assert _promotion(observer, receipt["promotion_decision_id"]) == promotion_before
        assert _completion_events(observer) == []
        _assert_no_result(observer, receipt["candidate_site_id"])
    finally:
        if observer is not None:
            observer.engine.close()
        bundle.engine.close()


@pytest.mark.parametrize("fault", ["delivery_write", "retry_audit", "audit_outbox_flush"])
def test_transient_retry_schedule_fault_rolls_back_to_owned_running_attempt(bridge_database, monkeypatch, fault):
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository

    bundle = _bundle(bridge_database, monkeypatch)
    observer = None
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        claimed = _claim(bundle, job_id)
        jobs = IntakeScoreJobRepository(bundle)
        attempt = jobs.begin_attempt(claimed)
        assert attempt is not None
        before = _physical(bridge_database, job_id)
        delivery_before = deepcopy(bundle.job_queue.get(job_id))
        promotion_before = deepcopy(_promotion(bundle, receipt["promotion_decision_id"]))
        events_before = deepcopy(bundle.outbox_repository.get_unpublished_events())
        with monkeypatch.context() as patch:
            if fault == "delivery_write":
                original = bundle.job_queue.update_status

                def injected(*args, **kwargs):
                    original(*args, **kwargs)
                    raise RuntimeError("Synthetic retry scheduling fault")

                patch.setattr(bundle.job_queue, "update_status", injected)
            elif fault == "retry_audit":
                original = jobs._audit

                def injected(*args, **kwargs):
                    original(*args, **kwargs)
                    raise RuntimeError("Synthetic retry scheduling fault")

                patch.setattr(jobs, "_audit", injected)
            else:
                original = bundle.assisted_intake_store.flush

                def injected():
                    original()
                    raise RuntimeError("Synthetic retry scheduling fault")

                patch.setattr(bundle.assisted_intake_store, "flush", injected)
            with pytest.raises(RuntimeError, match="Synthetic retry scheduling fault"):
                jobs.fail_attempt(claimed, attempt, "SYNTHETIC_TRANSIENT_TIMEOUT", True)
        observer = _bundle(bridge_database, monkeypatch)
        assert _physical(bridge_database, job_id) == before
        assert observer.job_queue.get(job_id) == delivery_before
        assert _promotion(observer, receipt["promotion_decision_id"]) == promotion_before
        assert observer.outbox_repository.get_unpublished_events() == events_before
        _assert_no_result(observer, receipt["candidate_site_id"])
    finally:
        if observer is not None:
            observer.engine.close()
        bundle.engine.close()


@pytest.mark.parametrize("policy", ["missing", "invalid"])
def test_review_missing_or_invalid_retry_policy_rolls_back_candidate_and_job(bridge_database, monkeypatch, policy):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        _, _, proposed = _score_proposal(bridge_database, bundle, client)
        before = deepcopy(_promotion(bundle, proposed["promotion_decision_id"]))
        if policy == "missing":
            monkeypatch.delenv("ODP_INTAKE_SCORE_RETRY_POLICY_JSON")
        else:
            monkeypatch.setenv("ODP_INTAKE_SCORE_RETRY_POLICY_JSON", json.dumps({
                "policy_id": "synthetic-invalid-zero-delay", "version": 1,
                "max_attempts": 5, "backoff_seconds": [0, 30, 120, 600],
            }))
        reviewed = _review(client, proposed)
        assert reviewed.status_code == 503, reviewed.text
        assert _promotion(bundle, proposed["promotion_decision_id"]) == before
        assert bundle.listing_repository_for_tenant(TENANT).list_candidates() == []
        assert bundle.engine.query("SELECT job_id FROM workflow.jobs WHERE job_type='intake-sitescore'") == []
        assert bundle.engine.query("SELECT job_id FROM durable_jobs WHERE job_type='intake-sitescore'") == []
        assert _completion_events(bundle) == []
    finally:
        bundle.engine.close()


def test_changed_retry_policy_discards_old_attempt_and_requires_authorized_refresh(bridge_database, monkeypatch):
    from modules.listing.application.intake_score_delivery import ScoreDeliveryDenied
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    revised = {"policy_id": "synthetic-local-score-retry-v1", "version": 2,
               "max_attempts": 5, "backoff_seconds": [20, 40, 120, 600]}

    class PolicyChangingRuntime(RecordingRuntime):
        def infer(self, **kwargs):
            result = super().infer(**kwargs)
            monkeypatch.setenv("ODP_INTAKE_SCORE_RETRY_POLICY_JSON", json.dumps(revised))
            return result

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        _, _, receipt = _approved_score(bridge_database, bundle, client)
        job_id = receipt["site_score_job_id"]
        with pytest.raises((JobFenceRejectedError, ScoreDeliveryDenied), match="POLICY"):
            handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=PolicyChangingRuntime())
        accepted = deepcopy(_promotion(bundle, receipt["promotion_decision_id"]))
        assert accepted["status"] == "COMPLETED"
        _assert_no_result(bundle, receipt["candidate_site_id"])
        with bridge_database.connect() as conn:
            conn.execute("UPDATE workflow.jobs SET lease_expires_at=now()-interval '1 second' WHERE job_id=%s", (job_id,))
        bundle.engine.execute("UPDATE durable_jobs SET lease_expires_at=? WHERE job_id=?", ("2026-01-01T00:00:00+00:00", job_id))
        refused_runtime = RecordingRuntime()
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=refused_runtime)
        assert refused_runtime.calls == []
        failed = _physical(bridge_database, job_id)
        assert failed["status"] == "FAILED"
        response = client.post(
            f"/api/v1/jobs/{job_id}/retry", json={"checkpoint": "SCORE_QUEUED", "reason": "Manager reviewed the changed synthetic retry policy"},
            headers={**_reviewer_headers("synthetic-policy-refresh"), "If-Match": f'W/"{failed["version"]}"'},
        )
        assert response.status_code == 202, response.text
        queued = _physical(bridge_database, job_id)
        assert queued["payload"]["retry_policy"] == revised
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=RecordingRuntime())
        assert _physical(bridge_database, job_id)["status"] == "SUCCEEDED"
        assert _promotion(bundle, receipt["promotion_decision_id"]) == accepted
        assert len(_completion_events(bundle)) == 1
    finally:
        bundle.engine.close()


def test_transient_retry_uses_recorded_backoff_and_stops_after_fifth_actual_failure(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    class TimeoutRuntime(RecordingRuntime):
        def infer(self, **kwargs):
            self.calls.append(kwargs)
            raise TimeoutError("Explicit synthetic repeated transient timeout")

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id, promotion_id = receipt["site_score_job_id"], receipt["promotion_decision_id"]
        runtime = TimeoutRuntime()
        acceptance = None
        for index in range(5):
            if index:
                _make_retry_due(bridge_database, job_id)
            handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=runtime)
            row = _physical(bridge_database, job_id)
            assert row["attempt"] == index + 1
            assert row["payload"]["generation"] == 1
            promotion = _promotion(bundle, promotion_id)
            assert promotion["status"] == "COMPLETED"
            if acceptance is None:
                acceptance = deepcopy(promotion)
            assert promotion == acceptance
            if index < 4:
                assert row["status"] == "RETRYING"
                delay = (datetime.fromisoformat(row["next_attempt_at"]) - datetime.fromisoformat(row["updated_at"])).total_seconds()
                assert abs(delay - [10, 30, 120, 600][index]) < 3
                assert bundle.listing_repository_for_tenant(TENANT).list_candidates()[0].candidate_site.site_status != "SCORING_FAILED"
                assert not any(event.event_type == "sitescore.failed" for event in bundle.outbox_repository.get_unpublished_events())
        assert len(runtime.calls) == 5
        assert row["status"] == "DEAD_LETTER" and row["dead_lettered_at"] is not None
        delivery = bundle.job_queue.get(job_id)
        assert delivery.status == JobStatus.FAILED and delivery.delivery_state == JobDeliveryState.DEAD_LETTER
        events = bundle.outbox_repository.get_unpublished_events()
        assert len([event for event in events if event.event_type == "sitescore.failed"]) == 1
        assert len([event for event in events if event.event_type == "job.dead_lettered"]) == 1
        assert not any(event.event_type == "job.replay_requested" for event in events)
        assert len(_completion_events(bundle)) == 1
        _assert_no_result(bundle, receipt["candidate_site_id"])
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("mutation", ["canonical_version", "canonical_lease", "delivery_fence"])
def test_stale_attempt_cannot_schedule_a_retry(bridge_database, monkeypatch, mutation):
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        claimed = _claim(bundle, job_id)
        jobs = IntakeScoreJobRepository(bundle)
        attempt = jobs.begin_attempt(claimed)
        assert attempt is not None
        if mutation == "delivery_fence":
            bundle.engine.execute("UPDATE durable_jobs SET fence_token=fence_token+1,version=version+1 WHERE job_id=?", (job_id,))
        else:
            with bridge_database.connect() as conn:
                if mutation == "canonical_version":
                    conn.execute("UPDATE workflow.jobs SET version=version+1 WHERE job_id=%s", (job_id,))
                else:
                    conn.execute("UPDATE workflow.jobs SET lease_expires_at=now()-interval '1 second' WHERE job_id=%s", (job_id,))
        before = _physical(bridge_database, job_id)
        delivery_before = deepcopy(bundle.job_queue.get(job_id))
        events_before = deepcopy(bundle.outbox_repository.get_unpublished_events())
        with pytest.raises(JobFenceRejectedError):
            jobs.fail_attempt(claimed, attempt, "SYNTHETIC_TRANSIENT_TIMEOUT", True)
        assert _physical(bridge_database, job_id) == before
        assert bundle.job_queue.get(job_id) == delivery_before
        assert bundle.outbox_repository.get_unpublished_events() == events_before
        assert _promotion(bundle, receipt["promotion_decision_id"])["status"] == "COMPLETED"
        _assert_no_result(bundle, receipt["candidate_site_id"])
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("binding", [
    "missing", "tenant_id", "job_id", "intake_id", "promotion_decision_id",
    "candidate_site_id", "promotion_version", "audit_event_id", "accepted_fence",
    "accepted_at", "accepted_at_naive", "accepted_at_future",
    "accepted_at_before_creation", "unrelated_audit",
])
def test_corrupted_committed_acceptance_is_rejected_before_result_persistence(bridge_database, monkeypatch, binding):
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        claimed = _claim(bundle, job_id)
        jobs = IntakeScoreJobRepository(bundle)
        attempt = jobs.begin_attempt(claimed)
        assert attempt is not None
        current = _physical(bridge_database, job_id)
        payload = deepcopy(current["payload"])
        expected_error = None
        if binding == "unrelated_audit":
            store = bundle.assisted_intake_store
            with store.transaction():
                store.refresh(TENANT)
                unrelated = next(audit for audit in store.workflow_audits.values()
                                 if audit["resource_type"] == "promotion_decision"
                                 and audit["action"] != "candidate.promotion_completed")
                # Reference another genuinely persisted, immutable audit. The
                # guard must validate its authority, not merely ID equality.
                store.promotions[receipt["promotion_decision_id"]]["audit_event_id"] = unrelated["audit_event_id"]
                store.flush()
            payload["worker_acceptance"]["audit_event_id"] = unrelated["audit_event_id"]
            expected_error = "SITESCORE_ACCEPTANCE_AUDIT_REJECTED"
        else:
            if binding == "missing":
                del payload["worker_acceptance"]
            elif binding == "promotion_version":
                payload["worker_acceptance"][binding] += 1
            elif binding == "accepted_fence":
                payload["worker_acceptance"][binding] = 0
            elif binding.startswith("accepted_at"):
                payload["worker_acceptance"]["accepted_at"] = {
                    "accepted_at": "not-an-iso-timestamp",
                    "accepted_at_naive": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                    "accepted_at_future": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    "accepted_at_before_creation": "2000-01-01T00:00:00+00:00",
                }[binding]
                expected_error = "SITESCORE_ACCEPTANCE_TIME_REJECTED"
            else:
                payload["worker_acceptance"][binding] = str(uuid4())
        # Intentionally do not bump the canonical version: the acceptance
        # proof itself must reject malformed persisted bindings, independently
        # of the version/fence rejection tested elsewhere.
        with bridge_database.connect() as conn:
            conn.execute("UPDATE workflow.jobs SET payload=%s::jsonb WHERE job_id=%s", (json.dumps(payload), job_id))
        before = _physical(bridge_database, job_id)

        def forbidden_persistence():
            raise AssertionError("Corrupted acceptance reached the result persistence boundary")

        with pytest.raises(JobFenceRejectedError, match=expected_error):
            jobs.commit_success(claimed, attempt, forbidden_persistence)
        assert _physical(bridge_database, job_id) == before
        assert _promotion(bundle, receipt["promotion_decision_id"])["status"] == "COMPLETED"
        assert len(_completion_events(bundle)) == 1
        _assert_no_result(bundle, receipt["candidate_site_id"])
    finally:
        bundle.engine.close()


def test_committed_acceptance_audit_is_append_only(bridge_database, monkeypatch):
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository
    from shared.infrastructure.persistence.assisted_listing_intake import (
        AssistedIntakePersistenceConflict,
    )

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        claimed = _claim(bundle, job_id)
        assert IntakeScoreJobRepository(bundle).begin_attempt(claimed) is not None
        audit_id = _physical(bridge_database, job_id)["payload"]["worker_acceptance"]["audit_event_id"]
        store = bundle.assisted_intake_store
        before = deepcopy(store.workflow_audits[audit_id])
        with pytest.raises(AssistedIntakePersistenceConflict, match="append-only"):
            with store.transaction():
                store.refresh(TENANT)
                store.workflow_audits[audit_id]["service_principal"] = "synthetic-wrong-service"
                store.flush()
        store.refresh(TENANT)
        assert store.workflow_audits[audit_id] == before
        assert _promotion(bundle, receipt["promotion_decision_id"])["status"] == "COMPLETED"
        assert len(_completion_events(bundle)) == 1
        _assert_no_result(bundle, receipt["candidate_site_id"])
    finally:
        bundle.engine.close()
