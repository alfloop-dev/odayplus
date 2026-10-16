"""Drive real native producer/worker roots with owned synthetic IO fixtures."""

import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path
from types import MethodType
from uuid import uuid4

import pytest

from apps.worker.oday_worker.main import ODayWorker
from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.external_data.application.native_snapshot_process_transport import NativeHttpHop
from modules.external_data.application.native_snapshot_transport import (
    NativeBucketMetadata,
    NativeSnapshotObjectMetadata,
)
from modules.listing.application.native_intake_retrieval import NativeIntakeRetrievalOwner
from modules.listing.application.native_intake_retrieval_policy import (
    POLICY_ENV,
    SIGNING_KEY_ENV,
    STORAGE_ENV,
)
from shared.audit.errors import CanonicalAuditUnavailable
from shared.domain.events import validate_event
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus
from tests.integration.test_native_intake_source_policy_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_source_policy_postgresql import (
    defer_audit,
    drive_upstream,
    prepare,
)
from tests.integration.test_native_intake_source_policy_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_source_policy_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_source_policy_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_source_policy_postgresql import (
    start_case as start_case,
)

pytestmark = pytest.mark.requires_live_env
RAW = b"<html><body>synthetic native content</body></html>"
BUCKET = "synthetic-native-snapshots"
KMS = "projects/test/locations/asia-east1/keyRings/native/cryptoKeys/snapshots"


class SyntheticIO:
    def __init__(self, graph, db):
        self.graph, self.db = graph, db
        self.calls, self.objects = [], {}
        self.job_id, self.hook = None, None
        self.http_responses, self.urls, self.head_generations, self.deletes = [], [], [], []
        self.upload_unknown, self.download_corrupt = False, False
        self.bucket_overrides = {}

    def admitted(self, operation):
        assert getattr(self.graph.engine._local, "connection", None) is None, "SQL root spans IO"
        with self.db.connect() as connection:
            status, payload = connection.execute("SELECT status,payload FROM workflow.jobs WHERE job_id=%s", (self.job_id,)).fetchone()
        assert status == "RUNNING" and payload["attempt_intents"][-1]["state"] == "ADMITTED"
        assert payload["attempt_intents"][-1]["operation"] == operation
        assert payload["accepted_business_fence"] == payload["attempt_intents"][-1]["business_fence"]
        self.calls.append(operation)
        if self.hook is not None:
            self.hook(operation)

    def fetch_hop(self, url, *, deadline, guard):
        guard()
        self.admitted("http_hop")
        self.urls.append(url)
        if self.http_responses:
            return self.http_responses.pop(0)
        return NativeHttpHop(200, None, "text/html", RAW, False)

    def factory(self, *, guard):
        fixture = self

        class Storage:
            def bucket_info(self, binding, *, deadline):
                guard()
                fixture.admitted("gcs_bucket")
                return NativeBucketMetadata.from_provider({"kind": "storage#bucket", "name": binding.bucket,
                    "location": "ASIA-EAST1", "locationType": "region", **fixture.bucket_overrides}, binding)

            def upload_with_kms_if_absent(self, binding, key, data, media_type, *, deadline):
                guard()
                fixture.admitted("gcs_upload")
                assert key not in fixture.objects
                fixture.objects[key] = (data, media_type)
                if fixture.upload_unknown:
                    fixture.upload_unknown = False
                    raise NativeIORejected("NATIVE_IO_CHILD_FAILED")
                return self.metadata(binding, key)

            def metadata(self, binding, key, generation=None):
                data, media_type = fixture.objects[key]
                return NativeSnapshotObjectMetadata.from_provider({"kind": "storage#object", "bucket": binding.bucket,
                    "name": key, "generation": "7", "size": str(len(data)), "contentType": media_type,
                    "kmsKeyName": KMS + "/cryptoKeyVersions/3"}, binding, key, expected_generation=generation)

            def head_version(self, binding, key, generation, *, deadline):
                guard()
                fixture.admitted("gcs_head")
                fixture.head_generations.append(generation)
                return self.metadata(binding, key, generation)

            def download_version_bounded(self, binding, key, generation, *, deadline):
                guard()
                fixture.admitted("gcs_download")
                assert generation == 7
                return fixture.objects[key][0].replace(b"synthetic", b"corrupted") if fixture.download_corrupt else fixture.objects[key][0]

            def delete_exact_generation(self, binding, key, generation, *, deadline):
                guard()
                fixture.admitted("gcs_delete")
                assert generation == 7
                fixture.deletes.append((key, generation))
                del fixture.objects[key]

        return Storage()


@pytest.fixture
def retrieval_case(source_policy_case, monkeypatch, record_property):
    import traceback

    from modules.listing.application.native_intake_retrieval_runtime import (
        NativeIntakeRetrievalRuntime,
    )

    settle = NativeIntakeRetrievalRuntime._settle

    def observe_settlement(self, delivery, tenant, generation, leases, error, **kwargs):
        # Retain typed cause evidence without driver text, URL, raw content,
        # DSN or credentials. Assertions and actual settlement stay unchanged.
        causes, seen = [], set()
        current = error
        while current is not None and id(current) not in seen and len(causes) < 8:
            seen.add(id(current))
            code = getattr(current, "code", None)
            reason = str(current)
            safe_reason = reason if (reason.startswith("NATIVE_") and len(reason) <= 96
                and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in reason)) else None
            causes.append({"error_class": type(current).__name__, "sqlstate": getattr(current, "sqlstate", None),
                "code": code if isinstance(code, str) and code.startswith("NATIVE_") else safe_reason,
                "frames": [{"path": frame.filename, "line": frame.lineno, "function": frame.name}
                           for frame in traceback.extract_tb(current.__traceback__)[-6:]]})
            current = current.__cause__
        record_property("actual_native_retrieval_settlement_causes", json.dumps(causes))
        return settle(self, delivery, tenant, generation, leases, error, **kwargs)

    monkeypatch.setattr(NativeIntakeRetrievalRuntime, "_settle", observe_settlement)
    identity, _ = source_policy_case
    start, _ = identity
    graph, ids, _, db = start[0]
    for path in ["infra/db/migrations/000011_identity_schema.sql", "infra/db/migrations/000035_native_intake_retrieval_quota.sql"]:
        with db.connect() as connection:
            connection.execute((Path(__file__).resolve().parents[2] / path).read_text())
    with db.connect() as connection:
        connection.execute("INSERT INTO identity.accounts (account_id,tenant_id,username,email,status,created_by) "
            "VALUES (%s,%s,'native-submitter','synthetic-native@example.invalid','active','synthetic-fixture')", (ids["actor"], ids["tenant"]))
        connection.execute("INSERT INTO identity.account_roles (account_id,role,granted_by) VALUES (%s,'expansion_user','synthetic-fixture')", (ids["actor"],))
        connection.execute("INSERT INTO identity.account_scopes (account_id,modules,clearance) VALUES (%s,'[\"listing\"]'::jsonb,'PUBLIC')", (ids["actor"],))
    monkeypatch.setenv(POLICY_ENV, json.dumps({"policy_id": "synthetic-native-retrieval", "version": 1,
        "tenant_ids": [ids["tenant"]], "max_attempts": 2, "deadline_seconds": 120, "backoff_seconds": [0.05]}))
    monkeypatch.setenv(STORAGE_ENV, json.dumps({"version": 1, "global_max_concurrency": 2, "storage_bindings": [
        {"tenant_id": ids["tenant"], "bucket": BUCKET, "kms_key_name": KMS, "residency_mode": "TW_ONLY", "retention_class": "STANDARD"}]}))
    monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-native-retrieval-key-separate-" * 2)
    monkeypatch.setenv("ODP_RESIDENCY_APPROVED_BUCKETS_TW_ONLY", BUCKET)
    io = SyntheticIO(graph, db)
    owner = NativeIntakeRetrievalOwner(graph, http_transport=io, snapshot_transport_factory=io.factory)
    start[2].canonical_intake_retrieval = MethodType(lambda bundle: owner, start[2])
    return source_policy_case, owner, io


def admitted(case):
    source_case, owner, io = case
    _, _, receipt = prepare(source_case)
    result = drive_upstream(source_case, receipt, source_case[1])
    assert result["processing_state"] == "RETRIEVING" and result["version"] == 4
    start = source_case[0][0]
    ids = start[0][1]
    reservation = owner.admit_event(ids["tenant"], result["outbox_event_id"])
    io.job_id = reservation["job_id"]
    return result, reservation


def test_actual_default_worker_commits_snapshot_two_events_and_replays_without_io(retrieval_case, monkeypatch):
    source_case, owner, io = retrieval_case
    parent, receipt = admitted(retrieval_case)
    start = source_case[0][0]
    graph, ids, _, db = start[0]
    monkeypatch.setattr("apps.worker.oday_worker.main._LeaseHeartbeat.start",
                        lambda self: pytest.fail("native retrieval must own both leases"))
    defer_audit(graph)
    assert ODayWorker(persistence=start[2], heartbeat_interval_seconds=0.01).run_once() is True
    delivery = graph.queue.get(receipt["job_id"])
    with graph.transaction(ids["tenant"]):
        job = graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (ids["tenant"], receipt["job_id"]))
    assert delivery.status == JobStatus.SUCCEEDED, job
    result = owner.execute(delivery)
    assert result["processing_state"] == "PARSING" and result["version"] == 5
    assert io.calls == ["gcs_bucket", "http_hop", "gcs_upload", "gcs_head", "gcs_download"]
    with db.connect() as connection:
        snapshot = connection.execute("SELECT intake_id,source_id,object_generation,encryption_key_ref,canonical_url "
            "FROM intake.source_snapshots WHERE source_snapshot_id=%s", (result["source_snapshot_id"],)).fetchone()
        state = connection.execute("SELECT processing_state,version FROM intake.intakes WHERE intake_id=%s", (receipt["intake_id"],)).fetchone()
        linked_snapshot = connection.execute("SELECT source_snapshot_id FROM intake.intake_stage_transitions WHERE transition_id=%s",
                                             (result["transition_id"],)).fetchone()[0]
        events = [r[0] for r in connection.execute("SELECT payload FROM workflow.outbox_events WHERE event_id IN (%s,%s)",
            (result["outbox_event_id"], result["snapshot_created_event_id"])).fetchall()]
    assert str(snapshot[0]) == receipt["intake_id"] and snapshot[1:4] == ("synthetic-feed", 7, KMS + "/cryptoKeyVersions/3")
    assert state == ("PARSING", 5) and str(linked_snapshot) == result["source_snapshot_id"]
    assert len(events) == 2 and all(not validate_event(e) for e in events)
    created = next(e for e in events if e["event_type"] == "snapshot.created")
    assert created["aggregate_id"] == result["source_snapshot_id"] and created["aggregate_type"] == "source_snapshot"
    assert created["causation_id"] == parent["outbox_event_id"] and created["producer"] == "retrieval_service"
    assert created["sensitive_fields"] == ["payload.object_uri"] and "object_uri" not in created["payload"]
    calls = list(io.calls)
    assert owner.execute(delivery) == result and io.calls == calls
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok


def run_worker(case):
    source_case, _, io = case
    start = source_case[0][0]
    graph, ids, _, db = start[0]
    defer_audit(graph)
    assert ODayWorker(persistence=start[2], heartbeat_interval_seconds=0.01).run_once() is True
    physical = graph.queue.get(io.job_id)
    with graph.transaction(ids["tenant"]):
        job = graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (ids["tenant"], io.job_id))
        intake = graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=?", (ids["tenant"], job["aggregate_id"]))
        snapshots = graph.engine.query("SELECT source_snapshot_id FROM intake.source_snapshots WHERE tenant_id=? AND intake_id=?",
                                       (ids["tenant"], job["aggregate_id"]))
    return physical, job, intake, snapshots


@pytest.mark.parametrize("status", [200, 401, 403])
def test_auth_wall_quarantines_without_false_source_block_or_parser_grant(retrieval_case, status):
    _, owner, io = retrieval_case
    admitted(retrieval_case)
    io.http_responses = [NativeHttpHop(status, None, "text/html" if status == 200 else None, b"", True)]
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.SUCCEEDED and job["status"] == "SUCCEEDED"
    assert intake["processing_state"] == "QUARANTINED" and intake["version"] == 5 and not snapshots
    assert intake["source_policy_state"] == "APPROVED_RETRIEVAL"
    assert job["result"]["error_code"] == "NATIVE_RETRIEVAL_AUTH_WALL"
    assert io.calls == ["gcs_bucket", "http_hop"]
    assert owner.execute(physical) == job["result"]


@pytest.mark.parametrize("mutation", ["source", "actor", "cancel", "input", "fence", "config"])
def test_committed_change_during_http_discards_content_and_stops_later_io(retrieval_case, monkeypatch, mutation):
    source_case, _, io = retrieval_case
    admitted(retrieval_case)
    graph, _, _, db = source_case[0][0][0]

    def change(operation):
        if operation != "http_hop":
            return
        io.hook = None
        if mutation == "config":
            import os

            policy = json.loads(os.environ[POLICY_ENV])
            monkeypatch.setenv(POLICY_ENV, json.dumps({**policy, "version": 2}))
            return
        with db.connect() as connection:
            if mutation == "source":
                connection.execute("UPDATE intake.source_registry SET kill_switch=true,version=version+1 WHERE source_id='synthetic-feed'")
            elif mutation == "actor":
                connection.execute("UPDATE identity.accounts SET status='disabled'")
            elif mutation == "cancel":
                connection.execute("UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp() WHERE job_id=%s", (io.job_id,))
            elif mutation == "input":
                connection.execute("UPDATE intake.intakes SET original_url='https://synthetic.example/changed' "
                    "WHERE intake_id=(SELECT aggregate_id FROM workflow.jobs WHERE job_id=%s)", (io.job_id,))
            elif mutation == "fence":
                connection.execute("UPDATE odp_runtime.durable_jobs SET fence_token=fence_token+1,locked_by='foreign-worker' WHERE job_id=%s", (io.job_id,))

    io.hook = change
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert not snapshots and io.calls == ["gcs_bucket", "http_hop"] and not io.objects
    if mutation == "source":
        assert intake["processing_state"] == "QUARANTINED" and intake["version"] == 5
        assert intake["source_policy_state"] == "SOURCE_BLOCKED" and physical.status == JobStatus.SUCCEEDED
    else:
        assert intake["processing_state"] == "RETRIEVING" and intake["version"] == 4
        if mutation == "cancel":
            assert job["status"] == "CANCELLED" and physical.status == JobStatus.CANCELLED
        elif mutation in {"actor", "input"}:
            assert job["status"] == "FAILED" and physical.status == JobStatus.FAILED
        else:
            # Lost fence/config does not authorize settlement or a new Intake
            # state. Its unresolved accepted checkpoint remains for recovery.
            assert job["status"] == "RUNNING" and physical.status == JobStatus.RUNNING
    assert graph.audit_sink.writer.verify_chain(intake["tenant_id"]).ok


def test_prohibited_redirect_gets_no_second_http_intent(retrieval_case):
    _, _, io = retrieval_case
    admitted(retrieval_case)
    io.http_responses = [NativeHttpHop(302, "http://127.0.0.1/private", None, b"", False)]
    physical, _, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.SUCCEEDED and intake["processing_state"] == "QUARANTINED"
    assert io.calls == ["gcs_bucket", "http_hop"] and not snapshots


def test_redirects_each_charge_fresh_intent_but_keep_original_snapshot_input(retrieval_case):
    _, owner, io = retrieval_case
    admitted(retrieval_case)
    io.http_responses = [NativeHttpHop(302, "/next", None, b"", False)]
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.SUCCEEDED and len(snapshots) == 1
    assert len(io.urls) == 2 and io.urls[1] == "https://synthetic.example/next"
    proof = job["result"]["native_snapshot_proof"]["snapshot_fields"]
    assert proof["canonical_url"] == intake["canonical_url"] == io.urls[0]
    intents = [i for i in job["payload"]["attempt_intents"] if i["operation"] == "http_hop"]
    assert len(intents) == 2 and len({i["reservation"]["rate_reservation_id"] for i in intents}) == 2
    assert all(i["input_sha256"] == job["payload"]["input_sha256"] for i in intents)
    assert owner.execute(physical) == job["result"]


def test_unknown_upload_response_reconciles_only_its_committed_key_and_generation(retrieval_case):
    _, owner, io = retrieval_case
    admitted(retrieval_case)
    io.upload_unknown = True
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.SUCCEEDED and intake["processing_state"] == "PARSING" and len(snapshots) == 1
    assert io.calls.count("gcs_upload") == 1 and io.head_generations == [None]
    uploads = [i for i in job["payload"]["attempt_intents"] if i["operation"] == "gcs_upload"]
    assert len(uploads) == 1 and uploads[0]["state"] == "ERROR"
    assert set(io.objects) == {uploads[0]["arguments"]["key"]} and not io.deletes
    assert owner.execute(physical) == job["result"]


def test_bad_download_hash_fails_and_cleans_only_owned_unreferenced_generation(retrieval_case):
    _, _, io = retrieval_case
    admitted(retrieval_case)
    io.download_corrupt = True
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.FAILED and job["status"] == "FAILED"
    assert intake["processing_state"] == "FAILED" and intake["version"] == 5 and not snapshots
    assert len(io.deletes) == 1 and io.deletes[0][1] == 7 and not io.objects
    assert io.calls[-1] == "gcs_delete"


def test_actual_provider_region_rejection_stops_before_http(retrieval_case):
    _, _, io = retrieval_case
    admitted(retrieval_case)
    io.bucket_overrides = {"location": "US-CENTRAL1"}
    physical, _, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.FAILED and intake["processing_state"] == "FAILED" and not snapshots
    assert io.calls == ["gcs_bucket"]


def test_late_io_result_after_total_deadline_uses_only_live_dual_lease_protective_failure(retrieval_case, monkeypatch):
    import os

    _, _, io = retrieval_case
    policy = json.loads(os.environ[POLICY_ENV])
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy, "deadline_seconds": 2}))
    admitted(retrieval_case)

    def late(operation):
        if operation == "http_hop":
            time.sleep(2.05)

    io.hook = late
    physical, _, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.FAILED and intake["processing_state"] == "FAILED" and not snapshots
    assert io.calls == ["gcs_bucket", "http_hop"]


def test_snapshot_and_state_events_roll_back_together_then_retry_keeps_total_deadline(retrieval_case, monkeypatch):
    source_case, owner, io = retrieval_case
    admitted(retrieval_case)
    graph, ids, _, _ = source_case[0][0][0]
    outbox = graph._outbox

    def reject_second_event(event):
        if event.event_type == "intake.state_changed" and event.payload["to_state"] == "PARSING":
            raise CanonicalAuditUnavailable("synthetic second-event failure")
        return outbox(event)

    monkeypatch.setattr(graph, "_outbox", reject_second_event)
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.QUEUED and physical.delivery_state == JobDeliveryState.RETRYING
    assert job["status"] == "RETRYING" and job["attempt"] == 1 and intake["processing_state"] == "RETRIEVING" and not snapshots
    timeout = job["timeout_at"]
    with graph.transaction(ids["tenant"]):
        assert not graph.engine.query("SELECT event_id FROM workflow.outbox_events WHERE tenant_id=? "
            "AND event_type='snapshot.created' AND payload->'payload'->>'intake_id'=?", (ids["tenant"], intake["intake_id"]))
        graph.engine.query_one("SELECT pg_sleep(GREATEST(0.0,EXTRACT(EPOCH FROM (available_at-clock_timestamp())))) FROM durable_jobs WHERE job_id=?", (io.job_id,))
    monkeypatch.setattr(graph, "_outbox", outbox)
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.SUCCEEDED and job["attempt"] == 2 and job["timeout_at"] == timeout
    assert intake["processing_state"] == "PARSING" and len(snapshots) == 1
    assert job["payload"]["delivery_generation"] == 2 and io.calls.count("gcs_upload") == 2
    assert len(io.objects) == 2 and not io.deletes  # no delete after uncertain SQL outcome
    assert owner.execute(physical) == job["result"]


def test_unknown_success_commit_replays_evidence_without_negative_settlement_or_delete(retrieval_case, monkeypatch):
    source_case, owner, io = retrieval_case
    admitted(retrieval_case)
    graph, _, _, _ = source_case[0][0][0]
    transaction, armed = graph.transaction, True

    @contextmanager
    def commit_then_disconnect(tenant):
        nonlocal armed
        trigger = False
        with transaction(tenant):
            yield
            row = graph.engine.query_one("SELECT status FROM workflow.jobs WHERE job_id=?", (io.job_id,))
            trigger = armed and row and row["status"] == "SUCCEEDED"
        if trigger:
            armed = False
            raise RuntimeError("synthetic connection loss after actual commit")

    monkeypatch.setattr(graph, "transaction", commit_then_disconnect)
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.SUCCEEDED and intake["processing_state"] == "PARSING" and len(snapshots) == 1
    assert io.calls.count("gcs_upload") == 1 and not io.deletes and not armed
    assert owner.execute(physical) == job["result"]


def test_two_intakes_with_identical_content_create_separate_owned_snapshots_objects_and_events(retrieval_case):
    source_case, owner, io = retrieval_case
    first_parent, first_receipt = admitted(retrieval_case)
    first_physical, first_job, first_intake, first_snapshots = run_worker(retrieval_case)
    _, second_receipt = admitted(retrieval_case)
    second_physical, second_job, second_intake, second_snapshots = run_worker(retrieval_case)
    assert first_intake["intake_id"] != second_intake["intake_id"]
    assert first_physical.status == second_physical.status == JobStatus.SUCCEEDED
    assert len(first_snapshots) == len(second_snapshots) == 1
    one, two = first_job["result"], second_job["result"]
    assert one["source_snapshot_id"] != two["source_snapshot_id"]
    assert one["snapshot_created_event_id"] != two["snapshot_created_event_id"]
    p1, p2 = one["native_snapshot_proof"]["snapshot_fields"], two["native_snapshot_proof"]["snapshot_fields"]
    assert p1["content_sha256"] == p2["content_sha256"] and p1["raw_object_uri"] != p2["raw_object_uri"]
    assert p1["intake_id"] == first_receipt["intake_id"] and p2["intake_id"] == second_receipt["intake_id"]
    assert len(io.objects) == 2 and not io.deletes and io.calls.count("gcs_upload") == 2
    assert owner.execute(first_physical) == one and owner.execute(second_physical) == two
    assert first_parent["outbox_event_id"] != second_job["payload"]["event_id"]
    assert source_case[0][0][0][0].audit_sink.writer.verify_chain(first_intake["tenant_id"]).ok


def test_unreaped_io_never_releases_quota_or_claims_terminal_closure(retrieval_case):
    source_case, _, io = retrieval_case
    admitted(retrieval_case)

    def unreaped(operation):
        if operation == "http_hop":
            raise NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED")

    io.hook = unreaped
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.RUNNING and job["status"] == "RUNNING" and job["result"] is None
    assert intake["processing_state"] == "RETRIEVING" and intake["version"] == 4 and not snapshots
    assert io.calls == ["gcs_bucket", "http_hop"] and not io.deletes
    graph, ids, _, _ = source_case[0][0][0]
    with graph.transaction(ids["tenant"]):
        slots = graph.engine.query("SELECT reservation_id FROM intake.native_retrieval_inflight_slots")
    assert len(slots) == 1 and slots[0]["reservation_id"] == job["payload"]["attempt_intents"][-1]["reservation"]["reservation_id"]


def test_http_quota_reservation_rolls_back_when_intent_write_fails_before_io(retrieval_case, monkeypatch):
    source_case, _, io = retrieval_case
    admitted(retrieval_case)
    graph, ids, _, _ = source_case[0][0][0]
    execute = graph.engine.execute

    def reject_http_intent(sql, params=()):
        if (sql.startswith("UPDATE workflow.jobs SET payload=")
                and json.loads(params[0])["attempt_intents"][-1]["operation"] == "http_hop"):
            raise RuntimeError("synthetic HTTP intent write rejection")
        return execute(sql, params)

    monkeypatch.setattr(graph.engine, "execute", reject_http_intent)
    physical, _, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.FAILED and intake["processing_state"] == "FAILED" and not snapshots
    assert io.calls == ["gcs_bucket"] and not io.objects
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT count(*) AS count FROM intake.native_retrieval_rate_reservations")["count"] == 0
        assert graph.engine.query_one("SELECT count(*) AS count FROM intake.native_retrieval_inflight_slots")["count"] == 0


def test_actual_default_factory_process_transports_private_token_and_generation_proof(retrieval_case, monkeypatch, tmp_path):
    import sys

    from modules.external_data.application.native_intake_process import NativeIntakeProcessRunner
    from modules.listing.application.native_intake_retrieval_policy import STORAGE_CREDENTIAL_ENV
    from shared.infrastructure.persistence.factory import PersistenceBundle

    source_case, _, io = retrieval_case
    _, receipt = admitted(retrieval_case)
    start = source_case[0][0]
    graph, ids, _, _ = start[0]
    # Actual default factory/handler/transport/parent runner. Only this test's
    # private construction hook substitutes an owned stdlib protocol child.
    # Production fixed child remains unchanged and has separate SSRF/kill tests.
    start[2].canonical_intake_retrieval = MethodType(PersistenceBundle.canonical_intake_retrieval, start[2])
    object_file, calls_file, script = tmp_path / "own-object.json", tmp_path / "operations", tmp_path / "synthetic_child.py"
    token = "synthetic-private-snapshot-token"
    monkeypatch.setenv(STORAGE_CREDENTIAL_ENV, token)
    script.write_text('''import base64,json,os,pathlib,sys
request=json.loads(sys.stdin.readline()); operation=request['operation']
with pathlib.Path(SYNTH_CALLS).open('a') as saved: saved.write(operation+'\\n')
if operation.startswith('gcs_'):
 assert len(sys.argv)==3 and sys.argv[1]=='--credential-fd'
 fd=int(sys.argv[2]); data=bytearray()
 while True:
  block=os.read(fd,1024)
  if not block: break
  data.extend(block)
 os.close(fd); assert data.decode()==SYNTH_TOKEN
store=pathlib.Path(SYNTH_OBJECT)
if operation=='http_hop':
 result={'status':200,'location':None,'media_type':'text/html','body_base64':base64.b64encode(SYNTH_RAW).decode(),'auth_wall':False}
elif operation=='gcs_bucket':
 result={'kind':'storage#bucket','name':request['bucket'],'location':'ASIA-EAST1','locationType':'region'}
else:
 if operation=='gcs_upload':
  assert not store.exists()
  store.write_text(json.dumps({'key':request['key'],'body':request['body_base64'],'media':request['media_type']}))
 state=json.loads(store.read_text()); assert state['key']==request['key']
 if operation=='gcs_download':
  assert request['generation']==7; result={'body_base64':state['body']}
 elif operation=='gcs_delete':
  assert request['generation']==7; store.unlink(); result={}
 else:
  assert operation in {'gcs_upload','gcs_head'}
  result={'kind':'storage#object','bucket':request['bucket'],'name':state['key'],'generation':'7',
   'size':str(len(base64.b64decode(state['body']))),'contentType':state['media'],'kmsKeyName':SYNTH_KMS+'/cryptoKeyVersions/3'}
print(json.dumps({'ok':True,'result':result}))
'''.replace("SYNTH_CALLS", repr(str(calls_file))).replace("SYNTH_OBJECT", repr(str(object_file)))
        .replace("SYNTH_TOKEN", repr(token)).replace("SYNTH_RAW", repr(RAW)).replace("SYNTH_KMS", repr(KMS)))
    commands = []
    original = NativeIntakeProcessRunner._command

    def owned_test_child(self, credential_fd):
        command = original(self, credential_fd)
        assert command[:3] == [sys.executable, "-I", "-S"] and command[3].endswith("/native_intake_io_child.py")
        command[3] = str(script)
        commands.append(command)
        return command

    monkeypatch.setattr(NativeIntakeProcessRunner, "_command", owned_test_child)
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.SUCCEEDED and intake["processing_state"] == "PARSING" and len(snapshots) == 1, job
    assert calls_file.read_text().splitlines() == ["gcs_bucket", "http_hop", "gcs_upload", "gcs_head", "gcs_download"]
    assert not io.calls and len(commands) == 5 and all(token not in str(c) for c in commands)
    assert token not in json.dumps(job)
    actual_owner = start[2].canonical_intake_retrieval()
    assert actual_owner.http_transport is None and actual_owner.snapshot_transport_factory is None
    assert actual_owner.execute(physical) == job["result"] and job["job_id"] == receipt["job_id"]
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok


def test_source_proof_change_after_head_retries_with_new_attempt_without_adopting_old_object(retrieval_case):
    source_case, _, io = retrieval_case
    admitted(retrieval_case)
    graph, ids, _, db = source_case[0][0][0]

    def change_after_head(operation):
        if operation == "gcs_download":
            io.hook = None
            with db.connect() as connection:
                connection.execute("UPDATE intake.source_registry SET version=version+1 WHERE source_id='synthetic-feed'")

    io.hook = change_after_head
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.QUEUED and physical.delivery_state == JobDeliveryState.RETRYING
    assert job["status"] == "RETRYING" and intake["processing_state"] == "RETRIEVING" and not snapshots
    first_objects, deadline = set(io.objects), job["timeout_at"]
    assert len(first_objects) == 1 and not io.deletes
    with graph.transaction(ids["tenant"]):
        graph.engine.query_one("SELECT pg_sleep(GREATEST(0.0,EXTRACT(EPOCH FROM (available_at-clock_timestamp())))) "
            "FROM durable_jobs WHERE job_id=?", (io.job_id,))
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.SUCCEEDED and intake["processing_state"] == "PARSING" and len(snapshots) == 1
    assert job["attempt"] == 2 and job["timeout_at"] == deadline and job["payload"]["delivery_generation"] == 2
    assert len(io.objects) == 2 and io.calls.count("gcs_upload") == 2 and not io.deletes
    snapshot = job["result"]["native_snapshot_proof"]["snapshot_fields"]
    assert snapshot["raw_object_uri"].removeprefix("gs://" + BUCKET + "/") not in first_objects


@pytest.mark.parametrize("field,value", [("attempt_token_sha256", "0" * 64), ("delivery_generation", True)])
def test_terminal_replay_rejects_changed_signed_attempt_binding_without_io(retrieval_case, field, value):
    source_case, owner, io = retrieval_case
    admitted(retrieval_case)
    physical, job, _, _ = run_worker(retrieval_case)
    assert physical.status == JobStatus.SUCCEEDED
    calls = list(io.calls)
    _, _, _, db = source_case[0][0][0]
    with db.connect() as connection:
        connection.execute("UPDATE workflow.jobs SET payload=jsonb_set(payload,%s,%s::jsonb) WHERE job_id=%s",
                           ([field], json.dumps(value), job["job_id"]))
    with pytest.raises(JobFenceRejectedError, match="TERMINAL_RECEIPT_REJECTED"):
        owner.execute(physical)
    assert io.calls == calls and not io.deletes


@pytest.mark.parametrize("generation", [7, None])
def test_cleanup_never_deletes_committed_snapshot_reference_even_when_legacy_generation_is_null(retrieval_case, generation):
    source_case, _, io = retrieval_case
    admitted(retrieval_case)
    graph, ids, _, db = source_case[0][0][0]
    snapshot_id = str(uuid4())

    def reference_before_failed_download(operation):
        if operation != "gcs_download":
            return
        io.hook = None
        key = next(iter(io.objects))
        with db.connect() as connection:
            connection.execute("INSERT INTO intake.source_snapshots (source_snapshot_id,tenant_id,intake_id,source_id,"
                "raw_object_uri,content_sha256,media_type,byte_length,captured_at,observed_at,capture_method,retention_class,"
                "encryption_key_ref,object_generation,residency_mode,legal_hold) "
                "SELECT %s,%s,aggregate_id,'synthetic-feed',%s,%s,'text/html',%s,clock_timestamp(),clock_timestamp(),"
                "'SERVER_RETRIEVAL','STANDARD',%s,%s,'TW_ONLY',true FROM workflow.jobs WHERE job_id=%s",
                (snapshot_id, ids["tenant"], "gs://" + BUCKET + "/" + key, hashlib.sha256(RAW).hexdigest(), len(RAW),
                 KMS + "/cryptoKeyVersions/3", generation, io.job_id))

    io.hook, io.download_corrupt = reference_before_failed_download, True
    physical, _, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.FAILED and intake["processing_state"] == "FAILED"
    assert snapshots == [{"source_snapshot_id": snapshot_id}] and len(io.objects) == 1
    assert not io.deletes and "gcs_delete" not in io.calls
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok


@pytest.mark.parametrize("failure", ["statement_timeout", "root_budget"])
def test_transient_sql_root_timeout_retries_only_after_fresh_ownership_without_extending_total_budget(retrieval_case, monkeypatch, failure):
    source_case, owner, io = retrieval_case
    admitted(retrieval_case)
    graph, ids, _, _ = source_case[0][0][0]
    current_source = owner._current_source
    injected = False

    def expire_one_accepted_root(intake, url, before):
        nonlocal injected
        result = current_source(intake, url, before)
        job = graph.engine.query_one("SELECT status FROM workflow.jobs WHERE job_id=?", (io.job_id,))
        if job["status"] == "RUNNING" and not injected:
            injected = True
            if failure == "statement_timeout":
                graph.engine.query_one("SELECT pg_sleep(1)")
            else:
                time.sleep(0.8)
                before()
        return result

    monkeypatch.setattr(owner, "_current_source", expire_one_accepted_root)
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert injected and physical.status == JobStatus.QUEUED and physical.delivery_state == JobDeliveryState.RETRYING
    assert job["status"] == "RETRYING" and job["attempt"] == 1 and intake["processing_state"] == "RETRIEVING" and not snapshots
    assert not io.calls
    details = json.loads(job["last_error"]) if isinstance(job["last_error"], str) else job["last_error"]
    assert details["causes"][0]["error_class"] in {"QueryCanceled", "JobFenceRejectedError"}
    deadline = job["timeout_at"]
    with graph.transaction(ids["tenant"]):
        graph.engine.query_one("SELECT pg_sleep(GREATEST(0.0,EXTRACT(EPOCH FROM (available_at-clock_timestamp())))) "
            "FROM durable_jobs WHERE job_id=?", (io.job_id,))
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.SUCCEEDED and intake["processing_state"] == "PARSING" and len(snapshots) == 1
    assert job["attempt"] == 2 and job["timeout_at"] == deadline


@pytest.mark.parametrize("code", ["NATIVE_HTTP_URL_REJECTED", "NATIVE_HTTP_ADDRESS_REJECTED",
                                "NATIVE_HTTP_REDIRECT_REJECTED", "NATIVE_HTTP_MEDIA_TYPE_REJECTED"])
def test_fixed_child_prohibited_http_errors_quarantine_without_rewriting_source_policy(retrieval_case, code):
    _, owner, io = retrieval_case
    admitted(retrieval_case)

    def reject_http(operation):
        if operation == "http_hop":
            raise NativeIORejected(code)

    io.hook = reject_http
    physical, job, intake, snapshots = run_worker(retrieval_case)
    assert physical.status == JobStatus.SUCCEEDED and job["status"] == "SUCCEEDED"
    assert intake["processing_state"] == "QUARANTINED" and intake["version"] == 5 and not snapshots
    assert intake["source_policy_state"] == "APPROVED_RETRIEVAL"
    assert io.calls == ["gcs_bucket", "http_hop"] and not io.objects
    assert owner.execute(physical) == job["result"]
