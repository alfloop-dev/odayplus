"""Exercise score delivery on isolated PostgreSQL without production admission claims.

Synthetic property/candidate facts are explicit test inputs. Any injected
computation is only a boundary probe; the normal production runtime must still
reject missing model admission and may never fall back to a local score.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from models.shared_ml.production_runtime import ProductionModelInputError
from modules.listing.domain.models import ListingDedupKey
from modules.sitescore.tests.test_sitescore_production_runtime import RecordingRuntime
from shared.audit.canonical_delivery import JOB_TYPE as CANONICAL_AUDIT_JOB_TYPE
from shared.audit.durable_delivery import JOB_TYPE as DURABLE_AUDIT_JOB_TYPE
from shared.jobs.queue import JobFenceRejectedError, JobStatus
from tests.intake_registry_fixture import APPROVER, MANAGER, TENANT
from tests.integration.test_intake_registry_calendar_postgresql import _bundle
from tests.integration.test_operator_intake_promotion_postgresql import (
    _promotion_client,
    _ready_intake,
    _request,
    _review,
    _reviewer_headers,
)
from tests.integration.test_operator_intake_workflow_postgresql import (
    _headers,
)
from tests.integration.test_operator_intake_workflow_postgresql import (
    bridge_database as bridge_database,
)
from tests.support.listing_search_integration import execute_claimed_listing_search

pytestmark = pytest.mark.requires_live_env


@pytest.fixture(autouse=True)
def explicit_local_signing_key(monkeypatch):
    # Deliberately non-secret, local synthetic material; never production config.
    monkeypatch.setenv("ODP_INTAKE_SCORE_ATTEMPT_SIGNING_KEY", "synthetic-local-score-delivery-test-key-20261013")
    # Explicit mechanism-test policy, not approval of inference retry budgets.
    monkeypatch.setenv("ODP_INTAKE_SCORE_RETRY_POLICY_JSON", json.dumps({
        "policy_id": "synthetic-local-score-retry-v1", "version": 1,
        "max_attempts": 5, "backoff_seconds": [10, 30, 120, 600],
    }))


def _score_proposal(db, bundle, client, *, retrieved=False, available_from=None):
    intake, listing_id = _ready_intake(db, bundle, client)
    repo = bundle.listing_repository_for_tenant(TENANT)
    listing = repo.get_listing(listing_id)
    if available_from is not None:
        object.__setattr__(listing, "available_from", available_from)
    address = replace(repo.get_address(listing.address_id), latitude=25.033964, longitude=121.564468)
    # Explicit measured fixture facts exercise transport and transaction wiring.
    # No source retrieval, property admission, registry approval or UAT is claimed.
    for name, value in {
        "snapshot_id": str(uuid4()),
        "prior_90d_cell_net_revenue": 1_800_000.0,
        "prior_90d_cell_transaction_count": 720,
        "prior_90d_cell_store_count": 4,
        "feature_snapshot_time": datetime.now(UTC) - timedelta(minutes=10),
    }.items():
        object.__setattr__(listing, name, value)
    repo.save_listing(listing, address, ListingDedupKey(
        source_id=listing.source_id, source_listing_id=listing.source_listing_id,
        normalized_address=address.normalized_address,
        rent_amount=listing.rent_amount, area_ping=listing.area_ping,
    ))
    _seed_manual_admission(db, bundle, intake, listing, retrieved=retrieved)
    proposed = _request(client, intake)
    assert proposed.status_code == 202, proposed.text
    return intake, listing_id, proposed.json()


def _approved_score(db, bundle, client, *, retrieved=False, available_from=None):
    intake, listing_id, proposed = _score_proposal(db, bundle, client, retrieved=retrieved, available_from=available_from)
    reviewed = _review(client, proposed)
    assert reviewed.status_code in (200, 202), reviewed.text
    receipt = reviewed.json()
    assert receipt["candidate_site_id"]
    assert receipt["site_score_job_id"]
    return intake, listing_id, receipt



def _seed_manual_admission(db, bundle, intake, listing, *, retrieved=False):
    # These SQL records are explicit synthetic admission inputs, not records of
    # any real source approval, cloud capture or property reconciliation.
    from hashlib import sha256

    store = bundle.assisted_intake_store
    store.refresh(TENANT)
    canonical = store.intakes[intake["id"]]
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO intake.source_registry(source_id,display_name,allowed_hosts,"
            "canonicalization_rule_version,retrieval_mode,policy_owner_subject_id,"
            "legal_approval_ref,license_approval_ref,review_expires_at,kill_switch,production_enabled) "
            "VALUES(%s,'Synthetic manual score evidence',ARRAY['example.invalid'],'synthetic-v1',"
            "'ASSISTED_ENTRY_ONLY',%s,'synthetic-local-legal','synthetic-local-license',now()+interval '7 days',false,true)",
            (listing.source_id, APPROVER),
        )
        conn.execute(
            "INSERT INTO intake.intakes(intake_id,tenant_id,submitter_subject_id,intake_method,source_id,"
            "source_policy_state,processing_state,correlation_id) VALUES(%s,%s,%s,'MANUAL',%s,'ASSISTED_ENTRY_ONLY','READY',%s)",
            (intake["id"], TENANT, MANAGER, listing.source_id, canonical["correlation_id"]),
        )
        conn.execute(
            "INSERT INTO intake.source_snapshots(source_snapshot_id,tenant_id,intake_id,source_id,"
            "raw_object_uri,content_sha256,media_type,byte_length,captured_at,observed_at,capture_method,"
            "retention_class,encryption_key_ref) VALUES(%s,%s,%s,%s,%s,%s,'application/json',2,%s,%s,"
            "'MANUAL_ENTRY','BUSINESS_5Y','synthetic-local-encryption-key')",
            (listing.snapshot_id, TENANT, intake["id"], listing.source_id,
             'gs://synthetic-local-score-evidence/' + listing.snapshot_id, sha256(b'{}').hexdigest(),
             listing.feature_snapshot_time, listing.feature_snapshot_time),
        )
    parser_pins = {}
    if retrieved:
        release_id, run_id = str(uuid4()), str(uuid4())
        with db.connect() as conn:
            conn.execute("UPDATE intake.source_registry SET retrieval_mode='APPROVED_RETRIEVAL' WHERE source_id=%s", (listing.source_id,))
            conn.execute("UPDATE intake.source_snapshots SET capture_method='SERVER_RETRIEVAL' WHERE source_snapshot_id=%s", (listing.snapshot_id,))
            conn.execute("UPDATE intake.intakes SET intake_method='URL',original_url='https://example.invalid/synthetic-score',source_policy_state='APPROVED_RETRIEVAL' WHERE intake_id=%s", (intake["id"],))
            conn.execute(
                "INSERT INTO intake.parser_releases(parser_release_id,source_id,package_name,semantic_version,"
                "input_schema_version,output_schema_version,artifact_uri,artifact_sha256,test_corpus_version,"
                "validation_status,released_by,released_at) VALUES(%s,%s,'synthetic-parser','1.0.0',"
                "'synthetic-raw-v1','synthetic-normalized-v1','gs://synthetic-parser/artifact.zip',%s,"
                "'synthetic-corpus-v1','PRODUCTION',%s,%s)",
                (release_id, listing.source_id, "c" * 64, APPROVER, listing.feature_snapshot_time - timedelta(days=1)),
            )
            conn.execute(
                "INSERT INTO intake.parser_runs(parser_run_id,tenant_id,intake_id,source_snapshot_id,"
                "parser_release_id,status,normalized_payload,validation_errors,started_at,completed_at,correlation_id) "
                "VALUES(%s,%s,%s,%s,%s,'SUCCEEDED',%s::jsonb,'[]'::jsonb,%s,%s,%s)",
                (run_id, TENANT, intake["id"], listing.snapshot_id, release_id, '{"floor":"1F"}', listing.feature_snapshot_time,
                 listing.feature_snapshot_time + timedelta(minutes=1), canonical["correlation_id"]),
            )
        parser_pins = {"intake_method": "URL", "policy_state": "APPROVED_RETRIEVAL", "parser_release_id": release_id, "parser_run_id": run_id,
                       "parser_release_version": 1, "parser_input_schema_version": "synthetic-raw-v1",
                       "parser_output_schema_version": "synthetic-normalized-v1"}
    with store.transaction():
        store.refresh(TENANT)
        canonical = store.intakes[intake["id"]]
        canonical.update({"source_id": listing.source_id, "source_policy_version": 1,
                          "source_snapshot_id": listing.snapshot_id,
                          "source_snapshot_version": 1, "intake_method": "MANUAL",
                          "parser_release_id": None, "parser_run_id": None})
        canonical.update(parser_pins)
        canonical["version"] += 1
        intake["version"] = canonical["version"]
        store.flush()

def test_approved_promotion_reserves_authoritative_job_and_real_delivery(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        intake, _, receipt = _approved_score(bridge_database, bundle, client)
        job_id = receipt["site_score_job_id"]
        with bridge_database.connect() as conn:
            row = conn.execute(
                "SELECT tenant_id,status,checkpoint,payload,version,result "
                "FROM workflow.jobs WHERE job_id=%s", (job_id,),
            ).fetchone()
        assert row is not None, "A promotion receipt must reference an actual canonical workflow job"
        assert str(row[0]) == TENANT
        assert row[1] == "QUEUED" and row[2] == "SCORE_QUEUED"
        assert row[3]["intake_id"] == intake["id"]
        assert row[3]["promotion_decision_id"] == receipt["promotion_decision_id"]
        assert row[3]["candidate_site_id"] == receipt["candidate_site_id"]
        assert row[5] is None, "Accepted work must not claim a computed result"
        delivery = bundle.job_queue.get(job_id)
        assert delivery is not None, "The accepted canonical job needs an executable delivery binding"
        assert delivery.payload["tenant_id"] == TENANT
        assert set(delivery.payload) == {"job_id", "tenant_id", "attempt_token"}
        assert delivery.payload["job_id"] == job_id
        assert delivery.payload["attempt_token"]
        assert receipt["status"] == "SCORE_QUEUED"
        current = client.get(f"/api/v1/jobs/{job_id}/receipt", headers=_reviewer_headers())
        assert current.status_code == 200, current.text
        assert current.json()["status"] == "QUEUED"
        assert current.json()["version"] == row[4]
        assert len(bundle.listing_repository_for_tenant(TENANT).list_candidates()) == 1
        requested = [event for event in bundle.outbox_repository.get_unpublished_events()
                     if event.event_type == "sitescore.requested"]
        assert len(requested) == 1
        assert requested[0].payload["site_score_job_id"] == job_id
        assert requested[0].payload["candidate_site_id"] == receipt["candidate_site_id"]
    finally:
        bundle.engine.close()


def _claim(bundle, job_id):
    # Audit delivery is higher priority. Claim these local rows without sending
    # audit data anywhere, then execute only the score job under test.
    for _ in range(30):
        job = bundle.job_queue.claim_next(worker_id="isolated-score-integration")
        assert job is not None, "Canonical score delivery must actually be claimable"
        if job.job_id == job_id:
            return job
        if execute_claimed_listing_search(bundle, job):
            continue
        # Both registered audit owners may precede score work in this same SQL queue.
        # Claiming them here is not delivery, completion, or an external ACK.
        assert job.job_type in {CANONICAL_AUDIT_JOB_TYPE, DURABLE_AUDIT_JOB_TYPE}
    raise AssertionError("Score work never reached the worker queue")


def _physical(db, job_id):
    with db.connect() as conn:
        row = conn.execute("SELECT row_to_json(j) FROM workflow.jobs j WHERE job_id=%s", (job_id,)).fetchone()
    return row[0] if row else None


def _report_repository(bundle):
    return bundle._scoped_repository("sitescore_repository", TENANT)


def _assert_no_result(bundle, candidate_id):
    reports = _report_repository(bundle)
    assert reports.history(candidate_id) == []
    for collection in (reports._PREDICTION_RUNS, reports._PREDICTIONS, reports._SITESCORE_RUNS):
        assert reports._store.list_all(collection) == [], "Failed work must not leave orphan model lineage"
    candidate = bundle.listing_repository_for_tenant(TENANT).list_candidates()[0]
    assert candidate.candidate_site.candidate_site_id == candidate_id
    assert candidate.candidate_site.site_status != "scored"
    assert candidate.model_version in (None, "")
    assert candidate.score is None, "No synthetic/local score may be shown before inference commits"


def test_actual_worker_commits_one_result_and_duplicate_restart_is_read_only(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        _, _, receipt = _approved_score(bridge_database, bundle, client)
        job_id, candidate_id = receipt["site_score_job_id"], receipt["candidate_site_id"]
        _assert_no_result(bundle, candidate_id)
        claimed = _claim(bundle, job_id)
        runtime = RecordingRuntime()
        handle_intake_score(claimed, bundle, model_runtime=runtime)
        current = _physical(bridge_database, job_id)
        assert current["status"] == "SUCCEEDED"
        assert bundle.job_queue.get(job_id).status == JobStatus.SUCCEEDED
        reports = _report_repository(bundle)
        report = reports.get_report(current["result"]["report_id"])
        assert report is not None and report.candidate_site_id == candidate_id
        assert report.model_version == "sitescore:approved-2026.07.24"
        assert report.m12.p50 == 106_531.25
        assert report.payback_period is None and report.payback_p50_months is None
        assert "financial_inputs_unavailable" in report.warnings
        assert not report.has_financial_result
        assert report.sitescore_run_id == current["result"]["sitescore_run_id"]
        summary = reports.get_sitescore_run(report.sitescore_run_id)
        assert summary is not None and summary.payback_p50_months is None
        assert reports.get_prediction_run(current["result"]["prediction_run_id"]) is not None
        assert len(reports.get_predictions(current["result"]["prediction_run_id"])) == 1
        assert len(reports.history(candidate_id)) == 1
        assert len(runtime.calls) == 1
        assert runtime.calls[0]["rows"][0]["tenant_id"] == TENANT
        assert runtime.calls[0]["rows"][0]["prior_90d_cell_store_count"] == 4
        finished = client.get(f"/api/v1/promotion-decisions/{receipt['promotion_decision_id']}", headers=_reviewer_headers())
        assert finished.status_code == 200 and finished.json()["status"] == "COMPLETED", finished.text
        events = bundle.outbox_repository.get_unpublished_events()
        assert len([e for e in events if e.event_type == "candidate.promotion_completed"]) == 1
        before_events = deepcopy(events)
        before_candidate = deepcopy(bundle.listing_repository_for_tenant(TENANT).list_candidates())
        bundle.engine.close()
        bundle = _bundle(bridge_database, monkeypatch)
        duplicate_runtime = RecordingRuntime()
        handle_intake_score(claimed, bundle, model_runtime=duplicate_runtime)
        assert duplicate_runtime.calls == []
        assert _physical(bridge_database, job_id) == current
        assert bundle.outbox_repository.get_unpublished_events() == before_events
        assert bundle.listing_repository_for_tenant(TENANT).list_candidates() == before_candidate
        assert len(_report_repository(bundle).history(candidate_id)) == 1
        restored = _report_repository(bundle).get_report(report.report_id)
        assert restored is not None and restored.payback_p50_months is None
        assert restored.m12 == report.m12
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("altered", ["attempt_token", "tenant_id", "job_id"])
def test_untrusted_delivery_binding_cannot_read_features_or_write_result(bridge_database, monkeypatch, altered):
    from modules.listing.application.intake_score_delivery import (
        IntakeScoreJobRepository,
        ScoreDeliveryDenied,
    )

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        claimed = _claim(bundle, receipt["site_score_job_id"])
        payload = dict(claimed.payload)
        payload[altered] = "tampered-attempt-signature" if altered == "attempt_token" else str(uuid4())
        forged = replace(claimed, payload=payload)
        before = _physical(bridge_database, claimed.job_id)
        with pytest.raises((JobFenceRejectedError, ScoreDeliveryDenied)):
            IntakeScoreJobRepository(bundle).begin_attempt(forged)
        assert _physical(bridge_database, claimed.job_id) == before
        _assert_no_result(bundle, receipt["candidate_site_id"])
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("mutation", ["canonical_fence", "canonical_version", "delivery_fence", "canonical_lease", "delivery_lease", "cancel", "parent_version", "candidate_input"])
def test_result_is_discarded_after_authority_changes_during_inference(bridge_database, monkeypatch, mutation):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        intake, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        claimed = _claim(bundle, receipt["site_score_job_id"])
        accepted_promotion = []

        class AuthorityChangedRuntime(RecordingRuntime):
            def infer(self, **kwargs):
                accepted_promotion.append(deepcopy(bundle.assisted_intake_store.promotions[receipt["promotion_decision_id"]]))
                result = super().infer(**kwargs)
                if mutation in ("canonical_fence", "canonical_version", "delivery_fence", "canonical_lease", "delivery_lease", "cancel"):
                    with bridge_database.connect() as conn:
                        if mutation == "canonical_fence":
                            conn.execute("UPDATE workflow.jobs SET fence_token=fence_token+1,version=version+1 WHERE job_id=%s", (claimed.job_id,))
                        elif mutation == "canonical_version":
                            conn.execute("UPDATE workflow.jobs SET version=version+1 WHERE job_id=%s", (claimed.job_id,))
                        elif mutation == "delivery_fence":
                            bundle.engine.execute("UPDATE durable_jobs SET fence_token=fence_token+1,version=version+1 WHERE job_id=?", (claimed.job_id,))
                        elif mutation == "canonical_lease":
                            conn.execute("UPDATE workflow.jobs SET lease_expires_at=now()-interval '1 second' WHERE job_id=%s", (claimed.job_id,))
                        elif mutation == "delivery_lease":
                            bundle.engine.execute("UPDATE durable_jobs SET lease_expires_at=? WHERE job_id=?", ("2026-01-01T00:00:00+00:00", claimed.job_id))
                        else:
                            conn.execute("UPDATE workflow.jobs SET cancellation_requested_at=now(),version=version+1 WHERE job_id=%s", (claimed.job_id,))
                elif mutation == "parent_version":
                    store = bundle.assisted_intake_store
                    with store.transaction():
                        store.refresh(TENANT)
                        store.intakes[intake["id"]]["version"] += 1
                        store.flush()
                else:
                    repo = bundle.listing_repository_for_tenant(TENANT)
                    candidate = repo.list_candidates()[0]
                    candidate.prior_90d_cell_store_count = 9
                    repo.save_candidate(candidate)
                return result

        runtime = AuthorityChangedRuntime()
        with pytest.raises(JobFenceRejectedError):
            handle_intake_score(claimed, bundle, model_runtime=runtime)
        assert len(runtime.calls) == 1, "The mutation must happen during actual inference"
        _assert_no_result(bundle, receipt["candidate_site_id"])
        assert _physical(bridge_database, claimed.job_id)["status"] != "SUCCEEDED"
        assert len([e for e in bundle.outbox_repository.get_unpublished_events()
                    if e.event_type == "candidate.promotion_completed"]) == 1
        bundle.assisted_intake_store.refresh(TENANT)
        assert accepted_promotion[0]["status"] == "COMPLETED"
        assert bundle.assisted_intake_store.promotions[receipt["promotion_decision_id"]] == accepted_promotion[0]
    finally:
        bundle.engine.close()


def test_normal_runtime_missing_registry_cannot_produce_local_score(bridge_database, monkeypatch):
    from apps.worker.oday_worker.main import ODayWorker

    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        claimed = _claim(bundle, receipt["site_score_job_id"])
        # Abandon only this local delivery claim. Audit jobs remain leased and
        # never contact their receiver; the real worker can reclaim this score.
        bundle.engine.execute("UPDATE durable_jobs SET lease_expires_at=? WHERE job_id=?", ("2026-01-01T00:00:00+00:00", claimed.job_id))
        assert ODayWorker(persistence=bundle, worker_id="synthetic-default-registry-worker").run_once()
        current = _physical(bridge_database, claimed.job_id)
        assert current["status"] == "RETRYING"
        assert datetime.fromisoformat(current["next_attempt_at"]) > datetime.now(UTC)
        assert current["last_error"]["code"] == "PRODUCTION_MODEL_REGISTRY_UNAVAILABLE"
        _assert_no_result(bundle, receipt["candidate_site_id"])
        assert current["result"] is None
    finally:
        bundle.engine.close()


def test_score_retry_preserves_candidate_and_rechecks_authority_and_version(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    class InvalidInputRuntime(RecordingRuntime):
        def infer(self, **kwargs):
            raise ProductionModelInputError("Explicit synthetic permanent input failure")

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        intake, _, receipt = _approved_score(bridge_database, bundle, client)
        job_id = receipt["site_score_job_id"]
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=InvalidInputRuntime())
        failed = _physical(bridge_database, job_id)
        assert failed["status"] == "FAILED"
        before = deepcopy(bundle.listing_repository_for_tenant(TENANT).list_candidates())
        current = client.get(f"/api/v1/jobs/{job_id}/receipt", headers=_reviewer_headers())
        assert current.status_code == 200 and current.json()["status"] == "FAILED", current.text
        assert set(current.json()) <= {"job_id", "status", "delivery_state", "checkpoint", "attempt", "version", "correlation_id"}
        body = {"checkpoint": "SCORE_QUEUED", "reason": "The synthetic input has been repaired"}
        headers = {**_reviewer_headers("synthetic-replay-score"), "If-Match": current.headers["etag"]}
        staff = {**_headers(MANAGER, key="synthetic-staff-score-retry"), "x-roles": "expansion_user", "x-operator-role": "expansion-staff", "If-Match": current.headers["etag"]}
        denied = client.post(f"/api/v1/jobs/{job_id}/retry", json=body, headers=staff)
        assert denied.status_code == 403, denied.text
        stale = client.post(f"/api/v1/jobs/{job_id}/retry", json=body, headers={**headers, "If-Match": 'W/"1"'})
        assert stale.status_code == 409, stale.text
        override = client.post(f"/api/v1/jobs/{job_id}/retry", json={**body, "override_retry_budget": True, "risk_acknowledged": True}, headers=headers)
        assert override.status_code in (403, 422), override.text
        assert _physical(bridge_database, job_id) == failed
        replayed = client.post(f"/api/v1/jobs/{job_id}/retry", json=body, headers=headers)
        assert replayed.status_code == 202, replayed.text
        assert replayed.json()["job_id"] == job_id
        canonical = _physical(bridge_database, job_id)
        assert canonical["status"] == "QUEUED" and canonical["fence_token"] > failed["fence_token"]
        assert canonical["payload"]["intake_id"] == intake["id"]
        assert bundle.listing_repository_for_tenant(TENANT).list_candidates()[0].candidate_site.candidate_site_id == before[0].candidate_site.candidate_site_id
        assert len(bundle.listing_repository_for_tenant(TENANT).list_candidates()) == 1
        again = client.post(f"/api/v1/jobs/{job_id}/retry", json=body, headers=headers)
        assert again.status_code == 202 and again.json() == replayed.json(), again.text
        assert _physical(bridge_database, job_id) == canonical
        assert len([e for e in bundle.outbox_repository.get_unpublished_events() if e.event_type == "job.replay_requested"]) == 1
    finally:
        bundle.engine.close()


def test_result_persistence_failure_rolls_back_results_but_preserves_committed_acceptance(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import (
        _JobResultRepository,
        handle_intake_score,
    )

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        claimed = _claim(bundle, receipt["site_score_job_id"])
        original = _JobResultRepository.save_sitescore_run

        def fail_after_write(self, run):
            original(self, run)
            raise RuntimeError("Synthetic score run storage failure")

        with monkeypatch.context() as patch:
            patch.setattr(_JobResultRepository, "save_sitescore_run", fail_after_write)
            with pytest.raises(RuntimeError, match="Synthetic score run storage failure"):
                handle_intake_score(claimed, bundle, model_runtime=RecordingRuntime())
        _assert_no_result(bundle, receipt["candidate_site_id"])
        current = _physical(bridge_database, claimed.job_id)
        assert current["status"] == "RUNNING" and current["result"] is None
        assert current["payload"]["worker_acceptance"]["candidate_site_id"] == receipt["candidate_site_id"]
        assert len([e for e in bundle.outbox_repository.get_unpublished_events() if e.event_type == "candidate.promotion_completed"]) == 1
        accepted = deepcopy(bundle.assisted_intake_store.promotions[receipt["promotion_decision_id"]])
        assert accepted["status"] == "COMPLETED"
        bundle.engine.close()
        bundle = _bundle(bridge_database, monkeypatch)
        bundle.assisted_intake_store.refresh(TENANT)
        assert bundle.assisted_intake_store.promotions[receipt["promotion_decision_id"]] == accepted
        assert len([e for e in bundle.outbox_repository.get_unpublished_events() if e.event_type == "candidate.promotion_completed"]) == 1
        _assert_no_result(bundle, receipt["candidate_site_id"])
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("operation", ["receipt", "replay"])
def test_job_parent_scope_is_rechecked_before_read_or_replay(bridge_database, monkeypatch, operation):
    from modules.sitescore.application.intake_score_worker import handle_intake_score
    from shared.infrastructure.persistence.assisted_listing_intake import _STATE_COLLECTIONS

    class InvalidInputRuntime(RecordingRuntime):
        def infer(self, **kwargs):
            raise ProductionModelInputError("Explicit synthetic permanent input failure")

    bundle = _bundle(bridge_database, monkeypatch)
    second = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        intake, _, receipt = _approved_score(bridge_database, bundle, client)
        job_id = receipt["site_score_job_id"]
        if operation == "replay":
            handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=InvalidInputRuntime())
        before = _physical(bridge_database, job_id)
        original_lock = bundle.assisted_intake_store._read_locked
        revoked = False

        def revoke_before_lock(collection, key):
            nonlocal revoked
            if not revoked and collection == _STATE_COLLECTIONS["intakes"]:
                revoked = True
                other = second.assisted_intake_store
                other.refresh(TENANT)
                other.intakes[intake["id"]]["scope"]["heat_zone_id"] = "synthetic-revoked-zone"
                other.intakes[intake["id"]]["version"] += 1
                other.flush()
            return original_lock(collection, key)

        headers = {**_reviewer_headers("synthetic-scope-score-replay"),
                   "x-heat-zone-ids": "synthetic-permitted-zone", "If-Match": f'W/"{before["version"]}"'}
        with monkeypatch.context() as patch:
            patch.setattr(bundle.assisted_intake_store, "_read_locked", revoke_before_lock)
            response = (client.get(f"/api/v1/jobs/{job_id}/receipt", headers=headers)
                        if operation == "receipt" else client.post(
                            f"/api/v1/jobs/{job_id}/retry",
                            json={"checkpoint": "SCORE_QUEUED", "reason": "Synthetic scope withdrawal"}, headers=headers))
        assert revoked and response.status_code == 403, response.text
        assert _physical(bridge_database, job_id) == before
        assert len(bundle.listing_repository_for_tenant(TENANT).list_candidates()) == 1
    finally:
        bundle.engine.close()
        second.engine.close()


@pytest.mark.parametrize("mutation", ["kill_switch", "review_expiry", "source_version", "snapshot_digest"])
def test_live_source_revocation_discards_inferred_output(bridge_database, monkeypatch, mutation):
    from modules.listing.application.intake_score_delivery import ScoreDeliveryDenied
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, listing_id, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        claimed = _claim(bundle, receipt["site_score_job_id"])
        listing = bundle.listing_repository_for_tenant(TENANT).get_listing(listing_id)

        class RevokedSourceRuntime(RecordingRuntime):
            def infer(self, **kwargs):
                result = super().infer(**kwargs)
                with bridge_database.connect() as conn:
                    if mutation == "kill_switch":
                        conn.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id=%s", (listing.source_id,))
                    elif mutation == "review_expiry":
                        conn.execute("UPDATE intake.source_registry SET review_expires_at=now()-interval '1 second' WHERE source_id=%s", (listing.source_id,))
                    elif mutation == "source_version":
                        conn.execute("UPDATE intake.source_registry SET version=version+1 WHERE source_id=%s", (listing.source_id,))
                    else:
                        conn.execute("UPDATE intake.source_snapshots SET content_sha256=%s WHERE source_snapshot_id=%s", ("f" * 64, listing.snapshot_id))
                return result

        runtime = RevokedSourceRuntime()
        with pytest.raises((ScoreDeliveryDenied, JobFenceRejectedError)):
            handle_intake_score(claimed, bundle, model_runtime=runtime)
        assert len(runtime.calls) == 1
        _assert_no_result(bundle, receipt["candidate_site_id"])
        assert _physical(bridge_database, claimed.job_id)["status"] != "SUCCEEDED"
        bundle.assisted_intake_store.refresh(TENANT)
        assert bundle.assisted_intake_store.promotions[receipt["promotion_decision_id"]]["status"] == "COMPLETED"
        assert len([e for e in bundle.outbox_repository.get_unpublished_events() if e.event_type == "candidate.promotion_completed"]) == 1
    finally:
        bundle.engine.close()


def test_score_replay_rejects_current_source_revocation(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    class InvalidInputRuntime(RecordingRuntime):
        def infer(self, **kwargs):
            raise ProductionModelInputError("Explicit synthetic permanent input failure")

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        _, listing_id, receipt = _approved_score(bridge_database, bundle, client)
        job_id = receipt["site_score_job_id"]
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=InvalidInputRuntime())
        failed = _physical(bridge_database, job_id)
        listing = bundle.listing_repository_for_tenant(TENANT).get_listing(listing_id)
        with bridge_database.connect() as conn:
            conn.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id=%s", (listing.source_id,))
        response = client.post(
            f"/api/v1/jobs/{job_id}/retry", json={"checkpoint": "SCORE_QUEUED", "reason": "Synthetic revoked source replay"},
            headers={**_reviewer_headers("synthetic-source-revoked-replay"), "If-Match": f'W/"{failed["version"]}"'},
        )
        assert response.status_code == 403, response.text
        assert _physical(bridge_database, job_id) == failed
        assert len(bundle.listing_repository_for_tenant(TENANT).list_candidates()) == 1
    finally:
        bundle.engine.close()


def test_canonical_heartbeat_extends_both_leases_without_invalidating_current_result(bridge_database, monkeypatch):
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        claimed = _claim(bundle, job_id)

        class HeartbeatRuntime(RecordingRuntime):
            def infer(self, **kwargs):
                result = super().infer(**kwargs)
                delivery_before = bundle.job_queue.get(job_id)
                canonical_before = _physical(bridge_database, job_id)
                version = IntakeScoreJobRepository(bundle).heartbeat(
                    job_id, expected_version=delivery_before.version, fence_token=delivery_before.fence_token,
                )
                delivery_after = bundle.job_queue.get(job_id)
                canonical_after = _physical(bridge_database, job_id)
                assert delivery_after.version == version == delivery_before.version + 1
                assert canonical_after["version"] == canonical_before["version"] + 1
                assert datetime.fromisoformat(canonical_after["lease_expires_at"]) == delivery_after.lease_expires_at
                assert canonical_after["fence_token"] == canonical_before["fence_token"]
                return result

        runtime = HeartbeatRuntime()
        handle_intake_score(claimed, bundle, model_runtime=runtime)
        assert len(runtime.calls) == 1
        assert _physical(bridge_database, job_id)["status"] == "SUCCEEDED"
        assert bundle.job_queue.get(job_id).status == JobStatus.SUCCEEDED
        assert len(_report_repository(bundle).history(receipt["candidate_site_id"])) == 1
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("cancel_requested", [False, True])
def test_expired_worker_attempts_exhaust_into_one_canonical_failure(bridge_database, monkeypatch, cancel_requested):
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository
    from shared.jobs.queue import JobDeliveryState

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        _, _, receipt = _approved_score(bridge_database, bundle, client)
        job_id = receipt["site_score_job_id"]
        jobs = IntakeScoreJobRepository(bundle)
        for expected_attempt in range(1, 6):
            claimed = _claim(bundle, job_id)
            assert jobs.begin_attempt(claimed) is not None
            assert _physical(bridge_database, job_id)["attempt"] == expected_attempt
            with bridge_database.connect() as conn:
                conn.execute("UPDATE workflow.jobs SET lease_expires_at=now()-interval '1 second' WHERE job_id=%s", (job_id,))
            bundle.engine.execute("UPDATE durable_jobs SET lease_expires_at=? WHERE job_id=?", ("2026-01-01T00:00:00+00:00", job_id))
        if cancel_requested:
            with bridge_database.connect() as conn:
                conn.execute("UPDATE workflow.jobs SET cancellation_requested_at=now(),version=version+1 WHERE job_id=%s", (job_id,))
        assert jobs.begin_attempt(_claim(bundle, job_id)) is None
        failed = _physical(bridge_database, job_id)
        assert failed["status"] == ("CANCELLED" if cancel_requested else "DEAD_LETTER")
        assert failed["attempt"] == failed["max_attempts"] == 5
        expected_error = "SITESCORE_CANCELLED" if cancel_requested else "SITESCORE_RETRY_BUDGET_EXHAUSTED"
        assert (failed["dead_lettered_at"] is None) == cancel_requested
        assert failed["last_error"]["code"] == expected_error
        delivery = bundle.job_queue.get(job_id)
        assert delivery.status == (JobStatus.CANCELLED if cancel_requested else JobStatus.FAILED)
        assert delivery.delivery_state == (None if cancel_requested else JobDeliveryState.DEAD_LETTER)
        response = client.get(f"/api/v1/promotion-decisions/{receipt['promotion_decision_id']}", headers=_reviewer_headers())
        assert response.status_code == 200 and response.json()["status"] == "COMPLETED", response.text
        _assert_no_result(bundle, receipt["candidate_site_id"])
        events = bundle.outbox_repository.get_unpublished_events()
        assert len([event for event in events if event.event_type == "sitescore.failed"]) == (0 if cancel_requested else 1)
        dead_letters = [event for event in events if event.event_type == "job.dead_lettered"]
        if cancel_requested:
            assert dead_letters == []
            return
        assert len(dead_letters) == 1
        assert dead_letters[0].producer == "job_platform"
        assert dead_letters[0].aggregate_id == job_id
        assert dead_letters[0].payload["attempt"] == 5
        assert dead_letters[0].payload["error_code"] == "SITESCORE_RETRY_BUDGET_EXHAUSTED"
        assert datetime.fromisoformat(dead_letters[0].payload["dead_lettered_at"]) == datetime.fromisoformat(failed["dead_lettered_at"])
    finally:
        bundle.engine.close()


def test_recorded_retrieval_parser_run_can_reach_fenced_model_commit(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle), retrieved=True)
        job_id = receipt["site_score_job_id"]
        runtime = RecordingRuntime()
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=runtime)
        assert len(runtime.calls) == 1
        current = _physical(bridge_database, job_id)
        assert current["status"] == "SUCCEEDED"
        assert current["payload"]["admission"]["parser_release_version"] == 1
        assert current["payload"]["admission"]["parser_run_version"] == 1
        assert len(_report_repository(bundle).history(receipt["candidate_site_id"])) == 1
    finally:
        bundle.engine.close()


def test_parser_rollback_after_enqueue_stops_before_inference(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle), retrieved=True)
        job_id = receipt["site_score_job_id"]
        current = _physical(bridge_database, job_id)
        release_id = current["payload"]["admission"]["parser_release_id"]
        with bridge_database.connect() as conn:
            conn.execute("UPDATE intake.parser_releases SET validation_status='ROLLED_BACK' WHERE parser_release_id=%s", (release_id,))
        runtime = RecordingRuntime()
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=runtime)
        assert runtime.calls == []
        failed = _physical(bridge_database, job_id)
        assert failed["status"] == "FAILED"
        assert failed["last_error"]["code"] == "SITESCORE_PARSER_RELEASE_DENIED"
        _assert_no_result(bundle, receipt["candidate_site_id"])
    finally:
        bundle.engine.close()


def test_changed_input_recovers_after_expired_attempt_and_authorized_same_candidate_retry(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        _, _, receipt = _approved_score(bridge_database, bundle, client)
        job_id, candidate_id = receipt["site_score_job_id"], receipt["candidate_site_id"]
        claimed = _claim(bundle, job_id)

        class ChangedInputRuntime(RecordingRuntime):
            def infer(self, **kwargs):
                result = super().infer(**kwargs)
                repo = bundle.listing_repository_for_tenant(TENANT)
                candidate = repo.list_candidates()[0]
                candidate.prior_90d_cell_store_count = 9
                candidate.feature_snapshot_time = datetime.now(UTC)
                repo.save_candidate(candidate)
                return result

        stale_runtime = ChangedInputRuntime()
        with pytest.raises(JobFenceRejectedError):
            handle_intake_score(claimed, bundle, model_runtime=stale_runtime)
        assert len(stale_runtime.calls) == 1
        _assert_no_result(bundle, candidate_id)
        with bridge_database.connect() as conn:
            conn.execute("UPDATE workflow.jobs SET lease_expires_at=now()-interval '1 second' WHERE job_id=%s", (job_id,))
        bundle.engine.execute("UPDATE durable_jobs SET lease_expires_at=? WHERE job_id=?", ("2026-01-01T00:00:00+00:00", job_id))
        recovery_runtime = RecordingRuntime()
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=recovery_runtime)
        assert recovery_runtime.calls == [], "Stale input must reach a failed checkpoint before another inference"
        failed = _physical(bridge_database, job_id)
        assert failed["status"] == "FAILED"
        assert bundle.job_queue.get(job_id).status == JobStatus.FAILED
        response = client.post(
            f"/api/v1/jobs/{job_id}/retry", json={"checkpoint": "SCORE_QUEUED", "reason": "Manager reviewed corrected synthetic candidate features"},
            headers={**_reviewer_headers("synthetic-changed-input-replay"), "If-Match": f'W/"{failed["version"]}"'},
        )
        assert response.status_code == 202, response.text
        fresh_runtime = RecordingRuntime()
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=fresh_runtime)
        assert len(fresh_runtime.calls) == 1
        assert fresh_runtime.calls[0]["rows"][0]["prior_90d_cell_store_count"] == 9
        assert _physical(bridge_database, job_id)["status"] == "SUCCEEDED"
        report = _report_repository(bundle).history(candidate_id)[0]
        assert report.feature_snapshot_time <= report.prediction_origin_time
        assert report.prediction_origin_time > datetime.fromisoformat(failed["payload"]["prediction_origin_time"])
        candidates = bundle.listing_repository_for_tenant(TENANT).list_candidates()
        assert len(candidates) == 1 and candidates[0].candidate_site.candidate_site_id == candidate_id
        assert len(_report_repository(bundle).history(candidate_id)) == 1
    finally:
        bundle.engine.close()


def test_cancellation_before_worker_claim_settles_without_inference(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        bundle.assisted_intake_store.refresh(TENANT)
        promotion_before = deepcopy(bundle.assisted_intake_store.promotions[receipt["promotion_decision_id"]])
        candidate_before = deepcopy(bundle.listing_repository_for_tenant(TENANT).list_candidates())
        with bridge_database.connect() as conn:
            conn.execute("UPDATE workflow.jobs SET cancellation_requested_at=now(),version=version+1 WHERE job_id=%s", (job_id,))
        runtime = RecordingRuntime()
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=runtime)
        assert runtime.calls == []
        cancelled = _physical(bridge_database, job_id)
        assert cancelled["status"] == "CANCELLED"
        assert cancelled["attempt"] == 1
        assert cancelled["payload"].get("worker_acceptance") is None
        assert bundle.job_queue.get(job_id).status == JobStatus.CANCELLED
        events = bundle.outbox_repository.get_unpublished_events()
        failures = [event for event in events if event.event_type == "sitescore.failed"]
        assert failures == []
        assert not any(event.event_type == "job.dead_lettered" for event in events)
        bundle.assisted_intake_store.refresh(TENANT)
        assert bundle.assisted_intake_store.promotions[receipt["promotion_decision_id"]] == promotion_before
        assert bundle.listing_repository_for_tenant(TENANT).list_candidates() == candidate_before
        audits = [row for row in bundle.assisted_intake_store.workflow_audits.values() if row["action"] == "sitescore.cancelled"]
        assert len(audits) == 1 and audits[0]["after_value"]["attempt"] == cancelled["attempt"]
        _assert_no_result(bundle, receipt["candidate_site_id"])
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("changed", ["rent", "address", "snapshot", "property", "listing_status"])
def test_score_replay_cannot_bless_stale_candidate_business_inputs(bridge_database, monkeypatch, changed):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    class InvalidInputRuntime(RecordingRuntime):
        def infer(self, **kwargs):
            raise ProductionModelInputError("Explicit synthetic permanent input failure")

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        _, listing_id, receipt = _approved_score(bridge_database, bundle, client)
        job_id = receipt["site_score_job_id"]
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=InvalidInputRuntime())
        failed = _physical(bridge_database, job_id)
        repo = bundle.listing_repository_for_tenant(TENANT)
        listing = repo.get_listing(listing_id)
        if changed == "address":
            address = repo.get_address(listing.address_id)
            object.__setattr__(address, "geocode_confidence", 0.0)
            object.__setattr__(address, "h3_res_9", "")
            repo._store.put(repo._ADDRESSES, address.address_id, address)
        else:
            name, value = ({"rent": ("rent_amount", 0.0), "snapshot": ("snapshot_id", str(uuid4())),
                            "property": ("property_id", str(uuid4())), "listing_status": ("listing_status", "leased")})[changed]
            object.__setattr__(listing, name, value)
            repo._store.put(repo._LISTINGS, listing_id, listing)
        response = client.post(
            f"/api/v1/jobs/{job_id}/retry", json={"checkpoint": "SCORE_QUEUED", "reason": "Retry with current listing evidence"},
            headers={**_reviewer_headers("synthetic-stale-business-input-replay"), "If-Match": f'W/"{failed["version"]}"'},
        )
        assert response.status_code in (403, 409, 422), response.text
        assert _physical(bridge_database, job_id) == failed
        _assert_no_result(bundle, receipt["candidate_site_id"])
    finally:
        bundle.engine.close()


def test_feature_after_recorded_prediction_origin_is_rejected_before_model_call(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        candidate = bundle.listing_repository_for_tenant(TENANT).list_candidates()[0]
        # Reproduce the old replay producer's inconsistent historical origin:
        # the feature is in the past now, but later than the recorded origin.
        invalid_origin = candidate.feature_snapshot_time - timedelta(seconds=1)
        with bridge_database.connect() as conn:
            conn.execute(
                "UPDATE workflow.jobs SET payload=jsonb_set(payload,'{prediction_origin_time}',to_jsonb(%s::text)),version=version+1 WHERE job_id=%s",
                (invalid_origin.isoformat(), job_id),
            )
        runtime = RecordingRuntime()
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=runtime)
        assert runtime.calls == []
        current = _physical(bridge_database, job_id)
        assert current["status"] == "FAILED"
        assert current["result"] is None
        _assert_no_result(bundle, receipt["candidate_site_id"])
    finally:
        bundle.engine.close()


def test_failure_replay_and_success_preserve_recorded_candidate_extensions(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        _, _, receipt = _approved_score(bridge_database, bundle, client)
        job_id = receipt["site_score_job_id"]
        repo = bundle.listing_repository_for_tenant(TENANT)
        candidate = repo.list_candidates()[0]
        # Legacy CandidateSite carries tenant_id as an extension; the shared
        # feature adapter reads it. A state transition must retain this record.
        object.__setattr__(candidate.candidate_site, "tenant_id", TENANT)
        repo.save_candidate(candidate)
        runtime = RecordingRuntime()
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=runtime)
        assert runtime.calls == []
        failed = _physical(bridge_database, job_id)
        assert failed["status"] == "FAILED"
        assert repo.list_candidates()[0].candidate_site.tenant_id == TENANT
        response = client.post(
            f"/api/v1/jobs/{job_id}/retry", json={"checkpoint": "SCORE_QUEUED", "reason": "Manager reviewed recorded candidate tenant metadata"},
            headers={**_reviewer_headers("synthetic-extension-replay"), "If-Match": f'W/"{failed["version"]}"'},
        )
        assert response.status_code == 202, response.text
        assert repo.list_candidates()[0].candidate_site.tenant_id == TENANT
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=RecordingRuntime())
        assert _physical(bridge_database, job_id)["status"] == "SUCCEEDED"
        assert repo.list_candidates()[0].candidate_site.tenant_id == TENANT
    finally:
        bundle.engine.close()


def test_recorded_listing_availability_date_survives_score_and_restart(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        recorded_date = datetime.now(UTC).date()
        _, listing_id, receipt = _approved_score(
            bridge_database, bundle, _promotion_client(bundle), available_from=recorded_date,
        )
        job_id = receipt["site_score_job_id"]
        handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=RecordingRuntime())
        assert _physical(bridge_database, job_id)["status"] == "SUCCEEDED"
        bundle.engine.close()
        bundle = _bundle(bridge_database, monkeypatch)
        repo = bundle.listing_repository_for_tenant(TENANT)
        assert repo.get_listing(listing_id).available_from == recorded_date
        assert repo.list_candidates()[0].listing.available_from == recorded_date
        assert len(_report_repository(bundle).history(receipt["candidate_site_id"])) == 1
    finally:
        bundle.engine.close()


def test_actual_fifth_failed_attempt_settles_canonical_dead_letter(bridge_database, monkeypatch):
    from modules.sitescore.application.intake_score_worker import handle_intake_score
    from shared.jobs.queue import JobDeliveryState

    class InvalidInputRuntime(RecordingRuntime):
        def infer(self, **kwargs):
            raise ProductionModelInputError("Explicit synthetic repeated input failure")

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        _, _, receipt = _approved_score(bridge_database, bundle, client)
        job_id = receipt["site_score_job_id"]
        for attempt in range(1, 6):
            handle_intake_score(_claim(bundle, job_id), bundle, model_runtime=InvalidInputRuntime())
            failed = _physical(bridge_database, job_id)
            assert failed["attempt"] == attempt
            if attempt < 5:
                assert failed["status"] == "FAILED"
                response = client.post(
                    f"/api/v1/jobs/{job_id}/retry", json={"checkpoint": "SCORE_QUEUED", "reason": "Manager reviewed the synthetic retry cause"},
                    headers={**_reviewer_headers(f"synthetic-budget-replay-{attempt}"), "If-Match": f'W/"{failed["version"]}"'},
                )
                assert response.status_code == 202, response.text
        assert failed["status"] == "DEAD_LETTER"
        assert failed["dead_lettered_at"] is not None
        delivery = bundle.job_queue.get(job_id)
        assert delivery.status == JobStatus.FAILED and delivery.delivery_state == JobDeliveryState.DEAD_LETTER
        events = bundle.outbox_repository.get_unpublished_events()
        assert len([event for event in events if event.event_type == "sitescore.failed"]) == 5
        dead_letters = [event for event in events if event.event_type == "job.dead_lettered"]
        assert len(dead_letters) == 1 and dead_letters[0].payload["attempt"] == 5
        assert dead_letters[0].payload["error_code"] == "PRODUCTION_MODEL_INPUT_INVALID"
        _assert_no_result(bundle, receipt["candidate_site_id"])
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("timing", ["before_attempt", "during_inference"])
@pytest.mark.parametrize("change", ["verdict_missing", "verdict_failed", "pit_changed"])
def test_current_operator_metadata_revocation_cannot_publish_score(bridge_database, monkeypatch, timing, change):
    from modules.listing.application.intake_score_delivery import ScoreDeliveryDenied
    from modules.sitescore.application.intake_score_worker import handle_intake_score
    from shared.infrastructure.persistence.operator_network_listings import (
        DurableAssistedIntakeRepository,
    )

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        _, listing_id, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        claimed = _claim(bundle, receipt["site_score_job_id"])
        repo = bundle.listing_repository_for_tenant(TENANT)
        metadata = DurableAssistedIntakeRepository(repo._store)

        def revoke():
            values = metadata.get_listing_metadata(listing_id)
            if change == "pit_changed":
                values["prior90dCellStoreCount"] = 99
            else:
                values["hardRuleFailures"] = None if change == "verdict_missing" else ["synthetic-recorded-failure"]
            metadata.save_listing_metadata(listing_id, values)
            # The canonical snapshot remains unchanged: checking only that
            # duplicate would miss the current operator evidence withdrawal.
            assert repo.get_listing(listing_id).hard_rule_failures == []

        class RevokedMetadataRuntime(RecordingRuntime):
            def infer(self, **kwargs):
                result = super().infer(**kwargs)
                revoke()
                return result

        runtime = RevokedMetadataRuntime()
        if timing == "before_attempt":
            revoke()
            handle_intake_score(claimed, bundle, model_runtime=runtime)
            assert runtime.calls == []
        else:
            with pytest.raises((ScoreDeliveryDenied, JobFenceRejectedError)):
                handle_intake_score(claimed, bundle, model_runtime=runtime)
            assert len(runtime.calls) == 1
        _assert_no_result(bundle, receipt["candidate_site_id"])
        assert _physical(bridge_database, claimed.job_id)["status"] != "SUCCEEDED"
        bundle.assisted_intake_store.refresh(TENANT)
        expected = "SCORE_FAILED" if timing == "before_attempt" else "COMPLETED"
        assert bundle.assisted_intake_store.promotions[receipt["promotion_decision_id"]]["status"] == expected
    finally:
        bundle.engine.close()
