"""Actual manual owner roots with synthetic provider bytes; no source HTTP."""

import json
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import MethodType

import pytest

from apps.worker.oday_worker.main import ODayWorker, _LeaseHeartbeat
from modules.external_data.application.native_assisted_snapshot_transport import (
    NativeAssistedSnapshotObjectMetadata,
)
from modules.external_data.application.native_intake_process import NativeIORejected
from modules.external_data.application.native_snapshot_transport import NativeBucketMetadata
from modules.listing.application.native_intake_assisted_entry import NativeIntakeAssistedEntryOwner
from modules.listing.application.native_intake_assisted_entry_history import (
    NativeAssistedEntryHistory,
)
from modules.listing.application.native_intake_assisted_entry_io import NativeIntakeAssistedEntryIO
from modules.listing.application.native_intake_assisted_entry_leases import (
    NativeAssistedCompositeLeases,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    SIGNING_KEY_ENV,
)
from modules.listing.application.native_intake_assisted_entry_results import (
    SNAPSHOT_FIELDS,
    commit_assisted_result,
)
from modules.listing.application.native_intake_assisted_entry_terminal import (
    NativeAssistedEntryTerminal,
)
from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from modules.listing.application.native_intake_retrieval_quota import NativeIntakeRetrievalQuota
from shared.infrastructure.persistence.assisted_listing_intake import (
    NativeIntakeSnapshotMetadataRepository,
)
from shared.infrastructure.persistence.factory import PersistenceBundle
from shared.infrastructure.persistence.identity_graph_sql import CanonicalIdentityGraphRepository
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus
from tests.integration.test_native_intake_assisted_entry_history_postgresql import retire_settings
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    assisted as assisted,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    command,
    snapshot,
    withdraw,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    past_manual as past_manual,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    recheck as recheck,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    start_case as start_case,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    waiting as waiting,
)
from tests.integration.test_native_intake_identity_postgresql import defer_audit

pytestmark = pytest.mark.requires_live_env


class Provider:
    """Explicit synthetic process seam, with real byte hashing in product code."""

    def __init__(self):
        self.objects, self.calls, self.guard = {}, [], None
        self.lose_upload_response = False
        self.fail_download_once = False

    def factory(self, *, guard):
        self.guard = guard
        return self

    def check(self, operation, key, generation, deadline):
        self.guard()
        assert time.monotonic() < deadline
        self.calls.append((operation, key, generation))

    def bucket_info(self, binding, *, deadline):
        self.check("bucket", None, None, deadline)
        return NativeBucketMetadata.from_provider({"kind": "storage#bucket", "name": binding.bucket,
            "location": "ASIA-EAST1", "locationType": "region"}, binding)

    def upload_with_kms_if_absent(self, binding, key, body, media_type, *, deadline):
        self.check("upload", key, 0, deadline)
        assert key not in self.objects and media_type == "application/json"
        metadata = NativeAssistedSnapshotObjectMetadata.from_provider({"kind": "storage#object", "bucket": binding.bucket,
            "name": key, "generation": "9", "size": str(len(body)), "contentType": media_type,
            "kmsKeyName": binding.kms_key_name}, binding, key)
        self.objects[key] = (bytes(body), metadata)
        if self.lose_upload_response:
            raise NativeIORejected("SYNTHETIC_UPLOAD_RESPONSE_LOST_AFTER_CREATE")
        return metadata

    def head_version(self, binding, key, generation, *, deadline):
        self.check("head", key, generation, deadline)
        metadata = self.objects[key][1]
        assert generation is None or generation == metadata.generation
        return metadata

    def download_version_bounded(self, binding, key, generation, *, deadline):
        self.check("download", key, generation, deadline)
        if self.fail_download_once:
            self.fail_download_once = False
            raise NativeIORejected("SYNTHETIC_TEMPORARY_DOWNLOAD_REJECTED", status=503)
        body, metadata = self.objects[key]
        assert metadata.generation == generation
        return body

    def delete_exact_generation(self, binding, key, generation, *, deadline):
        self.check("delete", key, generation, deadline)
        assert self.objects[key][1].generation == generation
        del self.objects[key]


@pytest.fixture
def storage_ready(assisted):
    """Install the same real prerequisite used by native retrieval SQL tests."""
    _, graph, _, db, _, _ = assisted
    with db.connect() as connection:
        connection.execute((Path(__file__).resolve().parents[2] /
            "infra/db/migrations/000035_native_intake_retrieval_quota.sql").read_text())
    with graph.transaction(assisted[2]["tenant"]):
        NativeIntakeRetrievalQuota(graph.engine).schema(before=lambda: None)
    return assisted


@pytest.fixture
def accepted(storage_ready, monkeypatch):
    assisted = storage_ready
    _, graph, ids, _, _, _ = assisted
    monkeypatch.setenv("ODP_RESIDENCY_APPROVED_BUCKETS_TW_ONLY", "native-synthetic-manual")
    provider = Provider()
    owner = NativeIntakeAssistedEntryOwner(graph, snapshot_transport_factory=provider.factory)
    response = command(assisted)
    assert response.status_code == 202, response.text
    admitted = owner.admit_event(ids["tenant"], response.json()["request_id"])
    defer_audit(graph)
    delivery = graph.queue.claim_next(worker_id="synthetic-native-human-evidence-worker")
    assert delivery.job_id == admitted["job_id"]
    fence, policy = owner._accept(delivery, ids["tenant"], 1)
    leases = NativeAssistedCompositeLeases(graph, delivery, business_fence=fence, policy_sha256=policy.sha256)
    io = NativeIntakeAssistedEntryIO(owner, delivery, leases, policy)
    yield assisted, owner, delivery, io, provider
    leases.pause()


def replay(case):
    assisted, owner, delivery, _, _ = case
    with owner.graph.transaction(assisted[2]["tenant"]):
        return NativeAssistedEntryHistory(owner.graph).committed_result(delivery, assisted[2]["tenant"], 1,
            before=NativeIntakeCommandBudget(owner.source.source))


def complete(case):
    _, owner, delivery, io, _ = case
    verified, upload, bucket = io.capture()
    io.terminal_attempted = True
    return commit_assisted_result(owner, delivery, io.leases, verified=verified, upload=upload, bucket=bucket)


def test_actual_manual_json_snapshot_proposal_links_human_transition_and_both_events(accepted, monkeypatch):
    assisted, owner, delivery, io, provider = accepted
    result = complete(accepted)
    assert result["processing_state"] == "PARSING" and result["requested_by"] == assisted[2]["actor"]
    assert owner.queue.get(delivery.job_id).status == JobStatus.SUCCEEDED
    key, (body, metadata) = next(iter(provider.objects.items()))
    document = json.loads(body)
    assert document["capture_method"] == "MANUAL_ENTRY" and document["request_id"] == result["request_id"]
    assert [x[0] for x in provider.calls] == ["bucket", "upload", "head", "download"]
    with owner.graph.transaction(assisted[2]["tenant"]):
        actual = NativeIntakeSnapshotMetadataRepository(owner.graph).required_for_intake(
            assisted[2]["tenant"], result["source_snapshot_id"], result["intake_id"])
        assert {key: actual[key] for key in SNAPSHOT_FIELDS} == result["native_snapshot_proof"]["snapshot_fields"]
        assert actual["observed_at"] == actual["captured_at"] and actual["observed_at"] is not None
        rows = owner.engine.query("SELECT * FROM intake.human_corrections WHERE tenant_id=? AND intake_id=?",
            (assisted[2]["tenant"], result["intake_id"]))
        assert len(rows) == 3 and all(r["status"] == "PROPOSED" and r["reviewed_by"] is None
            and r["source_snapshot_id"] == result["source_snapshot_id"] and r["version"] == 2 for r in rows)
        assert all(r["before_effective_value"] is None and r["parsed_value"] is None and r["normalized_value"] is None for r in rows)
    assert replay(accepted) == result
    retire_settings(monkeypatch)
    with assisted[3].connect() as conn:
        conn.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (assisted[2]["actor"],))
        conn.execute("UPDATE intake.source_registry SET kill_switch=true,assisted_entry_approval_ref=NULL")
    frozen = snapshot(assisted)
    assert replay(accepted) == result and snapshot(assisted) == frozen
    io.cleanup_known_unreferenced()
    assert key in provider.objects and metadata.generation == 9 and len(provider.calls) == 4


def test_unknown_upload_response_reconciles_only_its_journaled_key(accepted):
    provider = accepted[4]
    provider.lose_upload_response = True
    result = complete(accepted)
    assert replay(accepted) == result
    assert [x[0] for x in provider.calls] == ["bucket", "upload", "head", "download"]
    assert provider.calls[2][2] is None and provider.calls[3][2] == 9
    assert len({x[1] for x in provider.calls[1:]}) == 1 and len(provider.objects) == 1


@pytest.mark.parametrize("event_type", ["intake.state_changed", "snapshot.manual_entry_created"])
def test_outbox_failure_rolls_back_snapshot_proposals_transition_audit_and_both_jobs(accepted, monkeypatch, event_type):
    assisted, owner, delivery, io, _ = accepted
    verified, upload, bucket = io.capture()
    frozen = snapshot(assisted)
    original = owner.graph._outbox

    def fail(event):
        if event.event_type == event_type:
            raise RuntimeError("SYNTHETIC_FINAL_OUTBOX_FAILURE")
        return original(event)

    monkeypatch.setattr(owner.graph, "_outbox", fail)
    io.terminal_attempted = True
    with pytest.raises(RuntimeError, match="SYNTHETIC_FINAL_OUTBOX_FAILURE"):
        commit_assisted_result(owner, delivery, io.leases, verified=verified, upload=upload, bucket=bucket)
    assert snapshot(assisted) == frozen and replay(accepted) is None


@pytest.mark.parametrize("mutation", ["human", "source", "manual_expiry", "proposal", "physical_fence", "business_fence"])
def test_fresh_final_root_refuses_changed_authority_input_or_fences(accepted, mutation):
    assisted, owner, delivery, io, _ = accepted
    verified, upload, bucket = io.capture()
    tenant = assisted[2]["tenant"]
    with owner.graph.transaction(tenant):
        if mutation == "human":
            owner.engine.execute("DELETE FROM identity.account_roles WHERE account_id=?", (assisted[2]["actor"],))
        elif mutation == "source":
            owner.engine.execute("UPDATE intake.source_registry SET assisted_entry_approval_ref=NULL")
        elif mutation == "manual_expiry":
            owner.engine.execute("UPDATE intake.source_registry SET assisted_entry_review_expires_at=clock_timestamp()-interval '1 second'")
        elif mutation == "proposal":
            owner.engine.execute("UPDATE intake.human_corrections SET corrected_value='999'::jsonb WHERE field_path='rent'")
        elif mutation == "physical_fence":
            owner.engine.execute("UPDATE durable_jobs SET fence_token=fence_token+1 WHERE job_id=?", (delivery.job_id,))
        else:
            owner.engine.execute("UPDATE workflow.jobs SET fence_token=fence_token+1 WHERE tenant_id=? AND job_id=?", (tenant, delivery.job_id))
    frozen = snapshot(assisted)
    io.terminal_attempted = True
    from fastapi import HTTPException

    with pytest.raises((ValueError, PermissionError, JobFenceRejectedError, HTTPException)) as error:
        commit_assisted_result(owner, delivery, io.leases, verified=verified, upload=upload, bucket=bucket)
    if mutation == "human":
        assert isinstance(error.value, HTTPException) and error.value.status_code in {401, 403}
    else:
        expected = {"source": "NATIVE_ASSISTED_CURRENT_MANUAL_APPROVAL_REQUIRED",
            "manual_expiry": "NATIVE_ASSISTED_CURRENT_MANUAL_APPROVAL_REQUIRED",
            "proposal": "NATIVE_ASSISTED_HUMAN_REQUEST_PROOF_REJECTED", "physical_fence": "NATIVE_ASSISTED_PHYSICAL_NOT_OWNED",
            "business_fence": "NATIVE_ASSISTED_BUSINESS_NOT_OWNED"}[mutation]
        assert str(error.value) == expected
    assert snapshot(assisted) == frozen


def test_actual_http_cancellation_after_acceptance_prevents_final_commit(accepted):
    assisted, owner, delivery, io, _ = accepted
    verified, upload, bucket = io.capture()
    response = withdraw(assisted, {"request_id": upload["request_id"]})
    assert response.status_code == 200 and response.json()["status"] == "CANCELLATION_REQUESTED", response.text
    frozen = snapshot(assisted)
    io.terminal_attempted = True
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_ASSISTED_CANCELLATION_REQUESTED$"):
        commit_assisted_result(owner, delivery, io.leases, verified=verified, upload=upload, bucket=bucket)
    assert snapshot(assisted) == frozen


def test_actual_claim_beyond_physical_attempt_budget_cannot_accept_or_upload(assisted):
    _, graph, ids, _, _, _ = assisted
    owner = NativeIntakeAssistedEntryOwner(graph)
    response = command(assisted)
    assert response.status_code == 202, response.text
    admitted = owner.admit_event(ids["tenant"], response.json()["request_id"])
    graph.engine.execute("UPDATE durable_jobs SET attempts=max_retries WHERE job_id=?", (admitted["job_id"],))
    defer_audit(graph)
    delivery = graph.queue.claim_next(worker_id="synthetic-exhausted-human-evidence-worker")
    assert delivery.job_id == admitted["job_id"] and delivery.attempts > delivery.max_retries
    frozen = snapshot(assisted)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_ASSISTED_PHYSICAL_ATTEMPTS_EXHAUSTED$"):
        owner._accept(delivery, ids["tenant"], 1)
    assert snapshot(assisted) == frozen


def test_historical_success_allows_later_independent_review_but_rejects_changed_original_value(accepted):
    assisted, owner, _, _, _ = accepted
    result = complete(accepted)
    with assisted[3].connect() as conn:
        # A simulated subsequent lifecycle state, not an implementation of
        # review/application authority. Immutable original evidence survives.
        conn.execute("UPDATE intake.human_corrections SET status='REJECTED',reviewed_by=%s,reviewed_at=clock_timestamp(),"
            "version=version+1 WHERE intake_id=%s", (assisted[2]["reviewer"], result["intake_id"]))
    assert replay(accepted) == result
    with owner.graph.transaction(assisted[2]["tenant"]):
        owner.engine.execute("UPDATE intake.human_corrections SET corrected_value='999'::jsonb WHERE field_path='rent'")
    with pytest.raises(JobFenceRejectedError, match="NATIVE_ASSISTED_TERMINAL_PROOF_REJECTED"):
        replay(accepted)


@pytest.fixture
def worker_ready(storage_ready, monkeypatch):
    """Real default factory and registry; only the explicit provider seam varies."""
    assisted = storage_ready
    _, graph, ids, _, _, _ = assisted
    bundle = assisted[0][0][0][2]
    provider, constructed = Provider(), []
    monkeypatch.setenv("ODP_RESIDENCY_APPROVED_BUCKETS_TW_ONLY", "native-synthetic-manual")
    original = NativeIntakeAssistedEntryOwner.__init__

    def construct(self, current_graph, *, snapshot_transport_factory=None):
        assert current_graph.engine is graph.engine and snapshot_transport_factory is None
        original(self, current_graph, snapshot_transport_factory=provider.factory)
        constructed.append(self)

    monkeypatch.setattr(NativeIntakeAssistedEntryOwner, "__init__", construct)
    bundle.canonical_intake_assisted_entry = MethodType(PersistenceBundle.canonical_intake_assisted_entry, bundle)
    owner = bundle.canonical_intake_assisted_entry()
    response = command(assisted)
    assert response.status_code == 202, response.text
    admitted = list(owner.admit_partition(ids["tenant"], limit=1))
    assert len(admitted) == 1, owner.last_partition_progress
    assert owner.last_partition_progress["admitted"] == 1 and owner.last_partition_progress["conflicts"] == 0
    assert admitted[0]["request_id"] == response.json()["request_id"] and provider.calls == []
    return assisted, owner, admitted[0], provider, bundle, constructed


def worker_once(case):
    assisted, owner, receipt, _, bundle, _ = case
    defer_audit(owner.graph)
    assert ODayWorker(persistence=bundle, heartbeat_interval_seconds=0.01).run_once() is True
    with owner.graph.transaction(assisted[2]["tenant"]):
        row = owner.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (assisted[2]["tenant"], receipt["job_id"]))
    # PostgreSQL's persistence adapter returns jsonb as JSON text. Decode only
    # these object columns once for assertions against the owner read model.
    for field in ("payload", "result", "last_error"):
        if row[field] is not None:
            row[field] = json.loads(row[field]) if isinstance(row[field], str) else row[field]
            assert isinstance(row[field], dict)
    return row


def worker_terminal(case):
    assisted, owner, receipt, _, _, _ = case
    delivery = owner.queue.get(receipt["job_id"])
    with owner.graph.transaction(assisted[2]["tenant"]):
        before = NativeIntakeCommandBudget(owner.source.source)
        outcome = NativeAssistedEntryTerminal(owner.graph).replay(delivery, assisted[2]["tenant"], 1, before=before)
        assert outcome["terminal"]
        intake = owner.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=?",
            (assisted[2]["tenant"], receipt["intake_id"]))
        from modules.listing.application.native_intake_pending_commands import (
            NativeIntakePendingCommands,
        )

        NativeIntakePendingCommands(owner.graph).require_clear(intake, before=before)
        return outcome


def preserved_business(case):
    state = snapshot(case[0])
    return {key: state[key] for key in ("intake.intakes", "intake.intake_stage_transitions", "identity.match_cases",
        "expansion.listings", "intake.source_snapshots", "intake.parser_runs", "intake.human_corrections",
        "expansion.promotion_decisions")}


def test_default_worker_missing_storage_controls_fails_closed_without_IO(worker_ready):
    case = worker_ready
    assisted = case[0]
    _, owner, receipt, provider, _, _ = case
    with assisted[3].connect() as connection:
        connection.execute("DROP TABLE intake.native_retrieval_rate_reservations")
        connection.execute("DROP TABLE intake.native_retrieval_inflight_slots")
    with owner.graph.transaction(assisted[2]["tenant"]):
        assert owner.engine.query_one("SELECT to_regclass('intake.native_retrieval_inflight_slots') AS table_name")["table_name"] is None
    before = preserved_business(case)
    job = worker_once(case)
    assert job["status"] == "FAILED" and owner.queue.get(receipt["job_id"]).status == JobStatus.FAILED
    assert job["last_error"]["error_class"] == "NativeIntakeRetrievalConflict"
    assert provider.calls == [] and preserved_business(case) == before
    assert worker_terminal(case) == {"terminal": True, "result": None}


def test_real_default_factory_partition_worker_supports_zero_HTTP_budget_manual_source(worker_ready, monkeypatch):
    assisted, owner, receipt, provider, _, constructed = worker_ready
    with assisted[3].connect() as conn:
        conn.execute("UPDATE intake.source_registry SET retrieval_mode='ASSISTED_ENTRY_ONLY',requests_per_minute=0,"
            "concurrent_requests=0,version=version+1 WHERE source_id='synthetic-feed'")
    original = _LeaseHeartbeat.start

    def no_generic(self):
        if self._job_id == receipt["job_id"]:
            raise AssertionError("The manual owner must own both leases")
        return original(self)

    monkeypatch.setattr(_LeaseHeartbeat, "start", no_generic)
    job = worker_once(worker_ready)
    assert job["status"] == "SUCCEEDED" and job["checkpoint"] == "PARSING" and len(constructed) >= 2
    assert worker_terminal(worker_ready)["result"] == job["result"]
    assert len(provider.objects) == 1 and len([c for c in provider.calls if c[0] == "upload"]) == 1
    with owner.graph.transaction(assisted[2]["tenant"]):
        assert owner.engine.query_one("SELECT count(*) AS n FROM intake.native_retrieval_rate_reservations")["n"] == 0


@pytest.mark.parametrize("stage", ["before_acceptance", "after_acceptance"])
@pytest.mark.parametrize("loss", ["all_settings", "rotated_key", "human", "source"])
def test_default_worker_settles_actual_owned_job_after_authority_loss_without_changing_evidence(
        worker_ready, monkeypatch, stage, loss):
    assisted, owner, receipt, provider, _, _ = worker_ready

    def revoke():
        if loss == "all_settings":
            retire_settings(monkeypatch)
        elif loss == "rotated_key":
            monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-new-unrelated-manual-signing-key-" * 2)
        else:
            with assisted[3].connect() as conn:
                if loss == "human":
                    conn.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (assisted[2]["actor"],))
                else:
                    conn.execute("UPDATE intake.source_registry SET assisted_entry_approval_ref=NULL,version=version+1")

    frozen = preserved_business(worker_ready)
    if stage == "before_acceptance":
        revoke()
    else:
        original = NativeIntakeAssistedEntryOwner._accept

        def revoke_after(self, *args):
            accepted = original(self, *args)
            revoke()
            return accepted

        monkeypatch.setattr(NativeIntakeAssistedEntryOwner, "_accept", revoke_after)
    job = worker_once(worker_ready)
    assert job["status"] == "FAILED" and job["result"] is None and job["checkpoint"] == "AWAITING_ASSISTED_ENTRY"
    assert job["attempt"] == (0 if stage == "before_acceptance" else 1) and provider.calls == []
    assert owner.queue.get(receipt["job_id"]).status == JobStatus.FAILED
    assert preserved_business(worker_ready) == frozen
    assert worker_terminal(worker_ready) == {"terminal": True, "result": None}


@pytest.mark.parametrize("phase", ["acceptance", "final"])
def test_default_worker_recovers_real_SQL_commit_response_loss_without_duplicate_snapshot_or_events(
        worker_ready, monkeypatch, phase):
    assisted, owner, receipt, provider, _, _ = worker_ready
    original, lost = CanonicalIdentityGraphRepository.transaction, {"done": False}

    @contextmanager
    def response_loss(self, tenant):
        committed_target = False
        with original(self, tenant):
            yield
            if not lost["done"] and tenant == assisted[2]["tenant"]:
                row = self.engine.query_one("SELECT status FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                    (tenant, receipt["job_id"]))
                committed_target = row is not None and row["status"] == ("RUNNING" if phase == "acceptance" else "SUCCEEDED")
        if committed_target and not lost["done"]:
            lost["done"] = True
            raise ConnectionError("SYNTHETIC_RESPONSE_LOST_AFTER_ACTUAL_SQL_COMMIT")

    monkeypatch.setattr(CanonicalIdentityGraphRepository, "transaction", response_loss)
    job = worker_once(worker_ready)
    assert lost["done"] and job["status"] == "SUCCEEDED"
    outcome = worker_terminal(worker_ready)
    assert outcome["result"] == job["result"] and len(provider.objects) == 1
    assert len([x for x in provider.calls if x[0] == "upload"]) == 1
    with owner.graph.transaction(assisted[2]["tenant"]):
        rows = owner.engine.query("SELECT event_type FROM workflow.outbox_events WHERE tenant_id=? "
            "AND payload->>'causation_id'=? AND event_type IN ('intake.state_changed','snapshot.manual_entry_created')",
            (assisted[2]["tenant"], receipt["request_id"]))
        assert sorted(r["event_type"] for r in rows) == ["intake.state_changed", "snapshot.manual_entry_created"]


def test_default_worker_retry_keeps_original_deadline_generation_and_new_attempt_key(worker_ready):
    assisted, owner, receipt, provider, _, _ = worker_ready
    provider.fail_download_once = True
    job = worker_once(worker_ready)
    physical = owner.queue.get(receipt["job_id"])
    assert job["status"] == "RETRYING" and job["attempt"] == 1 and job["timeout_at"] == receipt["timeout_at"]
    assert job["payload"]["delivery_generation"] == 1 and physical.status == JobStatus.QUEUED
    assert physical.delivery_state == JobDeliveryState.RETRYING and len(provider.objects) == 0
    with owner.graph.transaction(assisted[2]["tenant"]):
        before = NativeIntakeCommandBudget(owner.source.source)
        owner.jobs.admission(job, before=before)
        owner.jobs.retry(job, before=before)
        now = datetime.fromisoformat(owner.engine.query_one("SELECT clock_timestamp() AS now")["now"])
    time.sleep(max(0, (datetime.fromisoformat(job["next_attempt_at"]) - now).total_seconds()) + 0.03)
    completed = worker_once(worker_ready)
    assert completed["status"] == "SUCCEEDED" and completed["attempt"] == 2 and completed["timeout_at"] == receipt["timeout_at"]
    assert completed["payload"]["delivery_generation"] == 1 and worker_terminal(worker_ready)["result"] == completed["result"]
    keys = [x[1] for x in provider.calls if x[0] == "upload"]
    assert len(keys) == 2 and keys[0] != keys[1] and len(provider.objects) == 1


@pytest.mark.parametrize("loss", ["all_settings", "inactive_account", "roles"])
def test_default_worker_authority_loss_at_cleanup_still_settles_without_deleting_evidence(
        worker_ready, monkeypatch, loss):
    assisted, owner, receipt, provider, _, _ = worker_ready
    provider.fail_download_once = True
    frozen = preserved_business(worker_ready)
    original = NativeIntakeAssistedEntryIO.cleanup_known_unreferenced
    changed = {"done": False}

    def revoke_before_cleanup(self):
        changed["done"] = True
        if loss == "all_settings":
            retire_settings(monkeypatch)
        else:
            with assisted[3].connect() as conn:
                if loss == "inactive_account":
                    conn.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (assisted[2]["actor"],))
                else:
                    conn.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (assisted[2]["actor"],))
        return original(self)

    monkeypatch.setattr(NativeIntakeAssistedEntryIO, "cleanup_known_unreferenced", revoke_before_cleanup)
    job = worker_once(worker_ready)
    assert changed["done"] and job["status"] == "FAILED" and job["attempt"] == 1 and job["result"] is None
    assert owner.queue.get(receipt["job_id"]).status == JobStatus.FAILED
    assert preserved_business(worker_ready) == frozen and worker_terminal(worker_ready) == {"terminal": True, "result": None}
    assert len(provider.objects) == 1 and all(call[0] != "delete" for call in provider.calls)
