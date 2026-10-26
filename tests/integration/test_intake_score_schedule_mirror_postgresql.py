"""Fenced delivery scheduling is a hint; tenant-owned canonical state authorizes work."""
from __future__ import annotations

from copy import deepcopy

import pytest

from modules.listing.application.intake_score_delivery import (
    IntakeScoreJobRepository,
    ScoreDeliveryUnavailable,
)
from shared.jobs.queue import JobFenceRejectedError
from tests.intake_registry_fixture import TENANT
from tests.integration.test_intake_registry_calendar_postgresql import _bundle
from tests.integration.test_intake_score_delivery_postgresql import (
    _approved_score,
    _assert_no_result,
    _claim,
    _physical,
    _promotion_client,
    _review,
    _score_proposal,
)
from tests.integration.test_intake_score_delivery_postgresql import (
    explicit_local_signing_key as explicit_local_signing_key,
)
from tests.integration.test_intake_score_lifecycle_postgresql import _promotion
from tests.integration.test_operator_intake_workflow_postgresql import (
    bridge_database as bridge_database,
)

pytestmark = pytest.mark.requires_live_env


def _delivery_row(bundle, job_id):
    return bundle.engine.query_one("SELECT * FROM durable_jobs WHERE job_id=?", (job_id,))


def test_delivery_schedule_reservation_fault_rolls_back_review(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        _, _, proposed = _score_proposal(bridge_database, bundle, client)
        promotion_before = deepcopy(_promotion(bundle, proposed["promotion_decision_id"]))
        audits_before = deepcopy(bundle.assisted_intake_store.workflow_audits)
        events_before = deepcopy(bundle.outbox_repository.get_unpublished_events())
        original = IntakeScoreJobRepository._schedule_delivery
        writes = []

        def fault(self, *args, **kwargs):
            original(self, *args, **kwargs)
            writes.append(True)
            raise ScoreDeliveryUnavailable("SYNTHETIC_SCHEDULE_RESERVATION_FAULT")

        with monkeypatch.context() as patch:
            patch.setattr(IntakeScoreJobRepository, "_schedule_delivery", fault)
            response = _review(client, proposed)
        assert response.status_code == 503, response.text
        assert writes == [True], "Fault must occur after the real mirrored schedule write"
        assert _promotion(bundle, proposed["promotion_decision_id"]) == promotion_before
        assert bundle.assisted_intake_store.workflow_audits == audits_before
        assert bundle.outbox_repository.get_unpublished_events() == events_before
        assert bundle.listing_repository_for_tenant(TENANT).list_candidates() == []
        assert bundle.engine.query("SELECT job_id FROM workflow.jobs WHERE job_type='intake-sitescore'") == []
        assert bundle.engine.query("SELECT job_id FROM durable_jobs WHERE job_type='intake-sitescore'") == []
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("transition", ["transient_retry", "permanent_failure", "preaccept_cancel"])
def test_delivery_schedule_write_fault_rolls_back_the_entire_transition(bridge_database, monkeypatch, transition):
    bundle = _bundle(bridge_database, monkeypatch)
    observer = None
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        claimed = _claim(bundle, job_id)
        jobs = IntakeScoreJobRepository(bundle)
        if transition == "preaccept_cancel":
            with bridge_database.connect() as conn:
                conn.execute("UPDATE workflow.jobs SET cancellation_requested_at=now(),version=version+1 WHERE job_id=%s", (job_id,))
            attempt = None
        else:
            attempt = jobs.begin_attempt(claimed)
            assert attempt is not None
        before = _physical(bridge_database, job_id)
        delivery_before = deepcopy(_delivery_row(bundle, job_id))
        promotion_before = deepcopy(_promotion(bundle, receipt["promotion_decision_id"]))
        candidates_before = deepcopy(bundle.listing_repository_for_tenant(TENANT).list_candidates())
        audits_before = deepcopy(bundle.assisted_intake_store.workflow_audits)
        events_before = deepcopy(bundle.outbox_repository.get_unpublished_events())
        original = jobs._schedule_delivery
        writes = []

        def fault(*args, **kwargs):
            original(*args, **kwargs)
            writes.append(True)
            raise RuntimeError("Synthetic mirrored schedule write fault")

        with monkeypatch.context() as patch:
            patch.setattr(jobs, "_schedule_delivery", fault)
            with pytest.raises(RuntimeError, match="Synthetic mirrored schedule write fault"):
                if transition == "preaccept_cancel":
                    jobs.begin_attempt(claimed)
                else:
                    jobs.fail_attempt(claimed, attempt, "SYNTHETIC_FAILURE", transition == "transient_retry")
        assert writes == [True]
        observer = _bundle(bridge_database, monkeypatch)
        assert _physical(bridge_database, job_id) == before
        assert _delivery_row(observer, job_id) == delivery_before
        assert _promotion(observer, receipt["promotion_decision_id"]) == promotion_before
        assert observer.listing_repository_for_tenant(TENANT).list_candidates() == candidates_before
        assert observer.assisted_intake_store.workflow_audits == audits_before
        assert observer.outbox_repository.get_unpublished_events() == events_before
        _assert_no_result(observer, receipt["candidate_site_id"])
    finally:
        if observer is not None:
            observer.engine.close()
        bundle.engine.close()


def test_due_delivery_hint_cannot_override_future_canonical_retry(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        jobs = IntakeScoreJobRepository(bundle)
        claimed = _claim(bundle, job_id)
        attempt = jobs.begin_attempt(claimed)
        assert attempt is not None
        jobs.fail_attempt(claimed, attempt, "SYNTHETIC_TRANSIENT_TIMEOUT", True)
        with bridge_database.connect(autocommit=False) as conn:
            # Deliberately corrupt only the scheduling agreement. Delivery is
            # due, but tenant-owned canonical authority explicitly says future.
            conn.execute("UPDATE odp_runtime.durable_jobs SET available_at=now()-interval '1 second' WHERE job_id=%s", (job_id,))
            conn.execute("UPDATE workflow.jobs SET next_attempt_at=now()+interval '1 hour' WHERE job_id=%s", (job_id,))
        before = _physical(bridge_database, job_id)
        next_delivery = _claim(bundle, job_id)
        with pytest.raises(JobFenceRejectedError, match="SITESCORE_RETRY_NOT_DUE"):
            jobs.begin_attempt(next_delivery)
        assert _physical(bridge_database, job_id) == before
        assert before["attempt"] == 1
        assert _promotion(bundle, receipt["promotion_decision_id"])["status"] == "COMPLETED"
        _assert_no_result(bundle, receipt["candidate_site_id"])
    finally:
        bundle.engine.close()


def test_success_schedule_clear_fault_preserves_acceptance_and_rolls_back_results(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score
    from modules.sitescore.tests.test_sitescore_production_runtime import RecordingRuntime

    bundle = _bundle(bridge_database, monkeypatch)
    observer = None
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        claimed = _claim(bundle, job_id)
        checkpoint = {}

        class CaptureAcceptedRuntime(RecordingRuntime):
            def infer(self, **kwargs):
                checkpoint["physical"] = _physical(bridge_database, job_id)
                checkpoint["delivery"] = deepcopy(_delivery_row(bundle, job_id))
                checkpoint["promotion"] = deepcopy(_promotion(bundle, receipt["promotion_decision_id"]))
                checkpoint["candidates"] = deepcopy(bundle.listing_repository_for_tenant(TENANT).list_candidates())
                checkpoint["audits"] = deepcopy(bundle.assisted_intake_store.workflow_audits)
                checkpoint["events"] = deepcopy(bundle.outbox_repository.get_unpublished_events())
                return super().infer(**kwargs)

        original = IntakeScoreJobRepository._schedule_delivery
        writes = []

        def fault(self, current, available_at, **kwargs):
            original(self, current, available_at, **kwargs)
            writes.append(available_at)
            raise RuntimeError("Synthetic success schedule clear fault")

        runtime = CaptureAcceptedRuntime()
        with monkeypatch.context() as patch:
            patch.setattr(IntakeScoreJobRepository, "_schedule_delivery", fault)
            with pytest.raises(RuntimeError, match="Synthetic success schedule clear fault"):
                handle_intake_score(claimed, bundle, model_runtime=runtime)
        assert len(runtime.calls) == 1
        assert writes == [None], "Fail only after terminal delivery eligibility was actually cleared"
        observer = _bundle(bridge_database, monkeypatch)
        assert _physical(bridge_database, job_id) == checkpoint["physical"]
        assert _delivery_row(observer, job_id) == checkpoint["delivery"]
        assert _promotion(observer, receipt["promotion_decision_id"]) == checkpoint["promotion"]
        assert checkpoint["promotion"]["status"] == "COMPLETED"
        assert observer.listing_repository_for_tenant(TENANT).list_candidates() == checkpoint["candidates"]
        assert observer.assisted_intake_store.workflow_audits == checkpoint["audits"]
        assert observer.outbox_repository.get_unpublished_events() == checkpoint["events"]
        _assert_no_result(observer, receipt["candidate_site_id"])
    finally:
        if observer is not None:
            observer.engine.close()
        bundle.engine.close()
