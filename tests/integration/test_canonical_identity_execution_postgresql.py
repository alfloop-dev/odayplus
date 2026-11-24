"""Synthetic approved decisions; real native acceptance/rollback/failure rows."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4, uuid5

import pytest

from modules.listing.application.identity_execution import IdentityExecutionService
from modules.listing.domain.identity_graph import IdentityConflictError
from shared.jobs.queue import JobFenceRejectedError
from tests.integration.test_assisted_listing_identity import (
    _native_snapshot,
    _replace_native_plan,
)
from tests.integration.test_assisted_listing_identity import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_admission_postgresql import (
    intake_blank_db as _parser_intake_blank_db,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    EXACT_SERVICE,
    _snapshot,
    persisted,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    authority as authority,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    case_api as case_api,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    completed_parser as completed_parser,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    exact_service_case as exact_service_case,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    intake_db as _matching_intake_db,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    matching_case as matching_case,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    parser_evidence_fields as parser_evidence_fields,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    parser_io_case as parser_io_case,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    parser_realm as parser_realm,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_matching_case_command_postgresql import (
    start_case as start_case,
)

pytestmark = pytest.mark.requires_live_env


def _queued(case):
    repo, _, _, db = case
    with db.connect() as conn:
        conn.execute("UPDATE identity.match_decisions SET status='APPROVED'")
        conn.execute("UPDATE identity.match_cases SET status='APPROVED'")
        conn.execute("UPDATE workflow.jobs SET status='QUEUED',checkpoint='APPROVED',attempt=0,lease_expires_at=NULL")
    return IdentityExecutionService(repo)


def test_native_approved_acceptance_execution_and_replay(canonical_graph):
    service = _queued(canonical_graph)
    repo, _, delivery, db = canonical_graph
    receipt = service.run(delivery)
    assert receipt["status"] == "EXECUTED"
    before = _native_snapshot(canonical_graph)
    assert service.run(delivery) == receipt
    assert _native_snapshot(canonical_graph) == before
    with db.connect() as conn:
        assert conn.execute("SELECT status,version FROM identity.match_decisions").fetchone() == ("EXECUTED", 31)
        assert conn.execute("SELECT status,attempt,fence_token FROM workflow.jobs").fetchone() == ("SUCCEEDED", 1, 32)
        assert conn.execute("SELECT event_type FROM workflow.outbox_events ORDER BY occurred_at,event_type").fetchall() == [
            ("match.decided",), ("identity.resolution_changed",), ("match.decided",),
        ]
    assert repo.audit_sink.writer.verify_chain(canonical_graph[1]["tenant"]).ok


def test_native_preaccept_version_conflict_leaves_approved_and_no_attempt(canonical_graph):
    service = _queued(canonical_graph)
    _, _, delivery, db = canonical_graph
    with db.connect() as conn:
        conn.execute("UPDATE identity.properties SET version=version+1")
    before = _native_snapshot(canonical_graph)
    with pytest.raises(IdentityConflictError, match="VERSION_CONFLICT"):
        service.run(delivery)
    assert _native_snapshot(canonical_graph) == before
    with db.connect() as conn:
        assert conn.execute("SELECT status,attempt FROM workflow.jobs").fetchone() == ("QUEUED", 0)
        assert conn.execute("SELECT status FROM identity.match_decisions").fetchone()[0] == "APPROVED"


@pytest.mark.parametrize("settlement", ["succeeds", "stale_physical", "expired_business"])
def test_native_executing_failure_records_failed_after_full_graph_rollback(canonical_graph, monkeypatch, settlement):
    service = _queued(canonical_graph)
    repo, ids, delivery, db = canonical_graph
    before = _native_snapshot(canonical_graph)

    graph_error = RuntimeError("Synthetic graph failure after source edges were written")

    def fail_after_edge_writes(*args):
        raise graph_error

    if settlement != "succeeds":
        original_fail = service.fail

        def lose_authority_before_failure_settlement(*args, **kwargs):
            # The execution transaction has rolled back before settlement.
            # Change actual SQL authority; do not manufacture a fail receipt.
            with db.connect() as conn:
                if settlement == "stale_physical":
                    conn.execute("UPDATE odp_runtime.durable_jobs SET fence_token=fence_token+1")
                else:
                    conn.execute("UPDATE workflow.jobs SET lease_expires_at=clock_timestamp()-interval '1 second'")
            return original_fail(*args, **kwargs)

        monkeypatch.setattr(service, "fail", lose_authority_before_failure_settlement)

    monkeypatch.setattr(repo, "_redirect", fail_after_edge_writes)
    with pytest.raises(RuntimeError, match="Synthetic graph failure") as observed:
        service.run(delivery)
    assert observed.value is graph_error
    after = _native_snapshot(canonical_graph)
    for table in ("identity.properties", "identity.source_identity_edges", "identity.property_redirects", "expansion.candidate_sites"):
        assert after[table] == before[table]
    with db.connect() as conn:
        if settlement != "succeeds":
            assert conn.execute("SELECT status FROM identity.match_decisions").fetchone()[0] == "EXECUTING"
            assert conn.execute("SELECT status FROM identity.match_cases").fetchone()[0] == "EXECUTING"
            assert conn.execute("SELECT status,attempt,last_error->>'reason_code' FROM workflow.jobs").fetchone() == (
                "RUNNING", 1, None,
            )
            assert conn.execute("SELECT event_type FROM workflow.outbox_events ORDER BY aggregate_version").fetchall() == [
                ("match.decided",),
            ]
            assert any("reconciliation" in note for note in graph_error.__notes__)
            assert repo.audit_sink.writer.verify_chain(ids["tenant"]).ok
            return
        assert conn.execute("SELECT status FROM identity.match_decisions").fetchone()[0] == "FAILED"
        assert conn.execute("SELECT status FROM identity.match_cases").fetchone()[0] == "FAILED"
        assert conn.execute("SELECT status,attempt,last_error->>'reason_code' FROM workflow.jobs").fetchone() == (
            "FAILED", 1, "IDENTITY_GRAPH_TRANSACTION_FAILED",
        )
        assert conn.execute("SELECT event_type FROM workflow.outbox_events ORDER BY aggregate_version").fetchall() == [
            ("match.decided",), ("match.decided",),
        ]
    assert repo.audit_sink.writer.verify_chain(ids["tenant"]).ok


def test_native_stale_worker_cannot_settle_a_failed_attempt(canonical_graph):
    service = _queued(canonical_graph)
    repo, ids, delivery, db = canonical_graph
    fence = service.begin(ids["tenant"], ids["decision"], delivery)
    with db.connect() as conn:
        conn.execute("UPDATE odp_runtime.durable_jobs SET fence_token=fence_token+1")
    before = _native_snapshot(canonical_graph)
    with pytest.raises(JobFenceRejectedError, match="IDENTITY_DELIVERY_FENCE_REJECTED"):
        service.fail(ids["tenant"], ids["decision"], delivery, business_fence=fence)
    assert _native_snapshot(canonical_graph) == before
    assert repo.case_context(ids["tenant"], ids["case"])["match_case"]["status"] == "EXECUTING"


@pytest.mark.parametrize("mutation", [
    "UPDATE workflow.jobs SET next_attempt_at=clock_timestamp()+interval '1 hour'",
    "UPDATE workflow.jobs SET attempt=max_attempts",
    "UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp()",
])
def test_native_job_schedule_attempt_and_cancel_authority_precede_acceptance(canonical_graph, mutation):
    service = _queued(canonical_graph)
    _, _, delivery, db = canonical_graph
    with db.connect() as conn:
        conn.execute(mutation)
    before = _native_snapshot(canonical_graph)
    with pytest.raises(JobFenceRejectedError, match="IDENTITY_ADMISSION_FENCE_REJECTED"):
        service.run(delivery)
    assert _native_snapshot(canonical_graph) == before


@pytest.mark.parametrize("command", ["begin", "fail"])
def test_native_attempt_checkpoints_require_separate_commits(canonical_graph, command):
    service = IdentityExecutionService(canonical_graph[0])
    repo, ids, delivery, _ = canonical_graph
    before = _native_snapshot(canonical_graph)
    with repo.transaction(ids["tenant"]):
        with pytest.raises(IdentityConflictError, match="MUST_OWN_TRANSACTION"):
            if command == "begin":
                service.begin(ids["tenant"], ids["decision"], delivery)
            else:
                service.fail(ids["tenant"], ids["decision"], delivery, business_fence=31)
    assert _native_snapshot(canonical_graph) == before


def _historical_listing(case, *, omit_version=False, held=False):
    _, ids, _, db = case
    with db.connect() as conn:
        conn.execute(
            "UPDATE expansion.listings SET property_id=%s,legal_hold=%s", (ids["split_target"], held),
        )
        conn.execute(
            "UPDATE identity.properties SET status='MERGED',current_redirect_property_id=%s WHERE property_id=%s",
            (ids["source"], ids["split_target"]),
        )
    if omit_version:
        _replace_native_plan(case, lambda plan: plan.update(expected_listing_versions={}))


def test_native_execution_requires_versions_for_historical_edge_listing(canonical_graph):
    repo, ids, delivery, _ = canonical_graph
    _historical_listing(canonical_graph, omit_version=True, held=True)
    before = _native_snapshot(canonical_graph)
    with pytest.raises(IdentityConflictError, match="LISTING_VERSION_CONFLICT"):
        repo.execute(ids["tenant"], ids["decision"], delivery, business_fence=31)
    assert _native_snapshot(canonical_graph) == before


def test_native_historical_listing_can_merge_with_real_versions(canonical_graph):
    repo, ids, delivery, _ = canonical_graph
    _historical_listing(canonical_graph)
    assert repo.execute(ids["tenant"], ids["decision"], delivery, business_fence=31)["status"] == "EXECUTED"
    resolved = repo.resolve_listing(ids["tenant"], ids["listing"])
    assert resolved["property_id_at_creation"] == ids["split_target"]
    assert resolved["effective_property_id"] == ids["target"]


def test_native_execution_replay_survives_later_case_work(canonical_graph):
    repo, ids, delivery, db = canonical_graph
    receipt = repo.execute(ids["tenant"], ids["decision"], delivery, business_fence=31)
    with db.connect() as conn:
        conn.execute("UPDATE identity.match_cases SET status='PENDING_REVIEW',version=version+2")
    before = _native_snapshot(canonical_graph)
    assert repo.execute(ids["tenant"], ids["decision"], delivery, business_fence=31) == receipt
    assert _native_snapshot(canonical_graph) == before


def test_native_execution_requires_accepted_transport_generation(canonical_graph):
    from psycopg.types.json import Jsonb

    repo, ids, delivery, db = canonical_graph
    with db.connect() as conn:
        conn.execute("UPDATE workflow.jobs SET payload=payload||%s", (Jsonb({
            "delivery_fence": delivery.fence_token, "delivery_worker": delivery.locked_by,
        }),))
        conn.execute("UPDATE odp_runtime.durable_jobs SET fence_token=fence_token+1,locked_by='later-worker'")
    later = repo.queue.get(delivery.job_id)
    before = _native_snapshot(canonical_graph)
    with pytest.raises(JobFenceRejectedError, match="IDENTITY_ACCEPTANCE_BINDING_REJECTED"):
        repo.execute(ids["tenant"], ids["decision"], later, business_fence=31)
    assert _native_snapshot(canonical_graph) == before


def test_concurrent_native_execution_does_not_duplicate_graph(canonical_graph):
    from modules.listing.application.intake_audit import CanonicalIntakeAuditSink
    from shared.audit.canonical_delivery import CanonicalAuditDelivery
    from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
    from shared.infrastructure.persistence.identity_graph_sql import (
        CanonicalIdentityGraphRepository,
    )
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    repo, ids, delivery, db = canonical_graph
    other_engine = PostgresEngine(db.url(), bootstrap=False, validate_schema=False)
    other_queue = DurableJobQueue(other_engine)
    writer = CanonicalPostgresAuditLog(other_engine)
    other = CanonicalIdentityGraphRepository(other_engine, other_queue,
        CanonicalIntakeAuditSink(writer, repo.audit_sink.retention,
            CanonicalAuditDelivery(writer, other_queue, repo.audit_sink.delivery.sink)))
    barrier = Barrier(2)

    def execute(graph):
        barrier.wait(timeout=10)
        try:
            return graph.execute(ids["tenant"], ids["decision"], delivery, business_fence=31)
        except Exception as exc:
            assert getattr(exc, "sqlstate", None) == "40001"
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, [repo, other]))
        committed = [result for result in results if result is not None]
        assert committed and all(result == committed[0] for result in committed)
        # An explicit caller retry after serialization reads the original
        # receipt; the repository never silently replays a write transaction.
        assert repo.execute(ids["tenant"], ids["decision"], delivery, business_fence=31) == committed[0]
        with db.connect() as conn:
            assert conn.execute("SELECT count(*) FROM identity.source_identity_edges").fetchone()[0] == 2
            assert conn.execute("SELECT count(*) FROM audit.audit_events").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM workflow.outbox_events").fetchone()[0] == 2
    finally:
        other_engine.close()


GEO_ENDPOINT = "https://geocode.synthetic.invalid/v1/geocode"
GEO_API_KEY = "-".join(["synthetic", "native", "geo", "provider", "value", "not", "a", "secret"])
GEO_BUCKET = "synthetic-native-geocode"
GEO_KMS = "projects/test/locations/asia-east1/keyRings/native/cryptoKeys/geocode"


def _enable_native_geo(monkeypatch, database, tenant, *, source_version=3):
    from modules.listing.application.native_intake_retrieval_policy import (
        GEO_BINDING_ENV,
        GEO_POLICY_ENV,
        GEO_SIGNING_KEY_ENV,
    )

    source_id = "geo.synthetic-registered-" + uuid4().hex[:8]
    with database.connect() as connection:
        connection.execute("INSERT INTO intake.source_registry (source_id,display_name,canonicalization_rule_version,"
            "retrieval_mode,legal_approval_ref,license_approval_ref,policy_owner_subject_id,review_expires_at,"
            "kill_switch,production_enabled,requests_per_minute,concurrent_requests,version) "
            "VALUES (%s,'Synthetic Geo provider','geo-v1','APPROVED_RETRIEVAL',"
            "'synthetic-geo-legal','synthetic-geo-license',%s,now()+interval '1 day',false,true,10,2,3)",
            (source_id, str(uuid4())))
        connection.commit()
    monkeypatch.setenv(GEO_POLICY_ENV, json.dumps({"policy_id": "synthetic-native-geo", "version": 1,
        "tenant_ids": [tenant], "max_attempts": 2, "deadline_seconds": 60, "backoff_seconds": [5]}))
    monkeypatch.setenv(GEO_BINDING_ENV, json.dumps({"version": 1, "global_max_concurrency": 2,
        "source": {"source_id": source_id, "source_policy_version": source_version,
                   "provider_id": "geocode.primary_api", "source_contract_id": "geocode_result_snapshot"},
        "storage_bindings": [{"tenant_id": tenant, "bucket": GEO_BUCKET, "kms_key_name": GEO_KMS,
            "residency_mode": "TW_ONLY", "retention_class": "STANDARD"}]}))
    monkeypatch.setenv(GEO_SIGNING_KEY_ENV, "Explicit synthetic native Geo dispatch signing material only")
    # Only an explicit LIVE provider with an HTTPS endpoint is admitted.
    monkeypatch.setenv("ODP_EXTERNAL_PROVIDER_MODE", "live")
    monkeypatch.setenv("ODP_GEOCODE_PROVIDER_URL", GEO_ENDPOINT)
    monkeypatch.setenv("ODP_GEOCODE_PROVIDER_API_KEY", GEO_API_KEY)
    monkeypatch.delenv("ODP_GEOCODE_PROVIDER_AUTH_STATUS", raising=False)
    monkeypatch.setenv("ODP_RESIDENCY_APPROVED_BUCKETS_TW_ONLY", GEO_BUCKET)
    return source_id


class TestNativeGeoReservation:
    """The exact-existing flow needs parser-realm databases; scope them here only."""

    @pytest.fixture
    def intake_blank_db(self, intake_pg_server, parser_realm, record_property):
        yield from _parser_intake_blank_db._get_wrapped_function()(intake_pg_server, parser_realm, record_property)

    @pytest.fixture
    def intake_db(self, intake_blank_db):
        return _matching_intake_db._get_wrapped_function()(intake_blank_db)

    @EXACT_SERVICE
    @pytest.mark.parametrize("geo", ["disabled", "fixture_mode", "stale_source_version", "enabled",
                                     "admitted_audit_failure"])
    def test_exact_existing_ready_reserves_native_geo_in_the_same_identity_unit(self, exact_service_case, monkeypatch, geo):
        from apps.api.oday_api.security.dependencies import default_boundary
        from modules.listing.application.identity_execution import IdentityExecutionService
        from modules.listing.application.native_intake_retrieval_policy import GEO_JOB_TYPE
        from modules.listing.application.pipeline import (
            NATIVE_GEO_REQUESTED_ACTION,
            NativeIntakeGeoOwner,
        )
        from shared.audit.errors import CanonicalAuditUnavailable
        from shared.jobs.queue import JobStatus
        from tests.integration.test_canonical_identity_admission_postgresql import _claim

        case, parsed, owner, proposal, policy_id, store = exact_service_case
        ids, database = case[1], case[2]
        if geo != "disabled":
            _enable_native_geo(monkeypatch, database, ids["tenant"],
                               source_version=2 if geo == "stale_source_version" else 3)
        if geo == "fixture_mode":
            # Fixture/disabled/unknown provider modes never forge READY geodata.
            monkeypatch.setenv("ODP_EXTERNAL_PROVIDER_MODE", "fixture")
        receipt = owner.confirm_exact_case(ids["tenant"], proposal["match_case_id"], expected_case_version=proposal["version"],
            policy_version_id=policy_id, replay_store=store)
        canonical = owner.graph, ids, None, database
        geo_job_id = NativeIntakeGeoOwner.job_id(receipt["decision_id"])

        def geo_rows():
            with owner.graph.transaction(ids["tenant"]):
                return (owner.graph.engine.query("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_type=?",
                                                 (ids["tenant"], GEO_JOB_TYPE)),
                        owner.graph.engine.query("SELECT * FROM durable_jobs WHERE job_type=?", (GEO_JOB_TYPE,)))

        delivery, registry, runtime = _claim(canonical, receipt["job_id"])
        if geo == "admitted_audit_failure":
            graph = runtime.canonical_identity_graph(identity_service_boundary=default_boundary)
            fence = IdentityExecutionService(graph).begin(ids["tenant"], receipt["decision_id"], delivery)
            before = _snapshot(canonical), persisted(case)
            original = graph.audit_sink.persist

            def unavailable(record, scope):
                if record["action"] == NATIVE_GEO_REQUESTED_ACTION:
                    raise CanonicalAuditUnavailable("Synthetic admitted Geo reservation audit unavailable")
                return original(record, scope)

            monkeypatch.setattr(graph.audit_sink, "persist", unavailable)
            # An admitted reservation failure is not NO_JOB_UNADMITTED: the whole
            # identity unit rolls back instead of committing READY without Geo.
            with pytest.raises(CanonicalAuditUnavailable):
                graph.execute(ids["tenant"], receipt["decision_id"], delivery, business_fence=fence)
            assert (_snapshot(canonical), persisted(case)) == before
            assert geo_rows() == ([], [])
            return
        registry.handle(delivery, runtime)
        assert owner.graph.queue.get(delivery.job_id).status == JobStatus.SUCCEEDED
        with owner.graph.transaction(ids["tenant"]):
            intake = owner.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=?",
                                      (ids["tenant"], parsed["intake_id"]))
        assert intake["processing_state"] == "READY" and intake["resolved_listing_id"] == ids["listing"]
        jobs, deliveries = geo_rows()
        if geo in {"disabled", "fixture_mode", "stale_source_version"}:
            # NO_JOB_UNADMITTED: truthful identity READY, no Geo work or evidence.
            assert jobs == [] and deliveries == []
            with owner.graph.transaction(ids["tenant"]):
                assert owner.graph.engine.query("SELECT 1 FROM audit.audit_events WHERE tenant_id=? AND action=?",
                                                (ids["tenant"], NATIVE_GEO_REQUESTED_ACTION)) == []
            return
        assert len(jobs) == 1 and len(deliveries) == 1
        job, physical = jobs[0], deliveries[0]
        payload = json.loads(job["payload"]) if isinstance(job["payload"], str) else job["payload"]
        assert job["job_id"] == geo_job_id == physical["job_id"] and job["status"] == "QUEUED"
        assert job["checkpoint"] == "GEOCODING" and job["aggregate_type"] == "intake"
        assert job["aggregate_id"] == parsed["intake_id"] and job["max_attempts"] == 2
        assert job["idempotency_key"] == parsed["intake_id"] + ":" + receipt["decision_id"]
        assert payload["listing_id"] == ids["listing"] and payload["origin_id"] == receipt["decision_id"]
        assert payload["origin_kind"] == "IDENTITY_DECISION"
        assert payload["ready_transition_id"] == str(uuid5(UUID(receipt["decision_id"]), "intake-ready-transition"))
        assert payload["identity_job_id"] == receipt["job_id"] and payload["intake_version"] == intake["version"]
        assert payload["geo_source_version"] == 3 and payload["delivery_generation"] == 1
        assert "attempt_token" not in payload and len(payload["address_sha256"]) == 64
        assert physical["status"] == "queued" and physical["max_retries"] == 2
        with owner.graph.transaction(ids["tenant"]):
            audits = owner.graph.engine.query("SELECT * FROM audit.audit_events WHERE tenant_id=? AND action=?",
                                              (ids["tenant"], NATIVE_GEO_REQUESTED_ACTION))
        assert len(audits) == 1 and audits[0]["causation_id"] == receipt["decision_id"]
        assert audits[0]["service_principal"] == GEO_JOB_TYPE
        # Replaying historical identity success adds no Geo work.
        after = _snapshot(canonical)
        replay = owner.confirm_exact_case(ids["tenant"], proposal["match_case_id"], expected_case_version=proposal["version"],
            policy_version_id=policy_id, replay_store=store)
        assert replay == receipt and _snapshot(canonical) == after and geo_rows() == (jobs, deliveries)


GEO_RESPONSE = {"request_id": "synthetic-provider-request-1", "observed_at": "2026-11-20T08:00:00+00:00",
                "result": {"latitude": 25.0339, "longitude": 121.5645, "precision": "rooftop", "confidence": 0.93,
                           "city": "台北市", "district": "信義區"}}
ADDRESS_DDL = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS core;
CREATE TABLE IF NOT EXISTS core.tenants (tenant_id UUID PRIMARY KEY);
CREATE TABLE IF NOT EXISTS core.address_locations (address_id UUID PRIMARY KEY);
-- Grow a relation-only boot stub to the canonical 000001 + 000021 columns.
ALTER TABLE core.address_locations
    ADD COLUMN IF NOT EXISTS raw_address TEXT NOT NULL, ADD COLUMN IF NOT EXISTS normalized_address TEXT,
    ADD COLUMN IF NOT EXISTS city VARCHAR(100), ADD COLUMN IF NOT EXISTS district VARCHAR(100),
    ADD COLUMN IF NOT EXISTS village VARCHAR(100), ADD COLUMN IF NOT EXISTS road VARCHAR(255),
    ADD COLUMN IF NOT EXISTS latitude NUMERIC(10, 7), ADD COLUMN IF NOT EXISTS longitude NUMERIC(10, 7),
    ADD COLUMN IF NOT EXISTS geom GEOMETRY(Point, 4326),
    ADD COLUMN IF NOT EXISTS geocode_precision VARCHAR(50) DEFAULT 'manual',
    ADD COLUMN IF NOT EXISTS geocode_confidence NUMERIC(3, 2), ADD COLUMN IF NOT EXISTS h3_res_8 VARCHAR(15),
    ADD COLUMN IF NOT EXISTS h3_res_9 VARCHAR(15), ADD COLUMN IF NOT EXISTS h3_res_10 VARCHAR(15),
    ADD COLUMN IF NOT EXISTS manual_override_flag BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES core.tenants(tenant_id),
    ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1;
"""


class SyntheticGeoIO:
    """Owned synthetic child runner and GEOCODE storage at the IO boundary only.

    The real HttpGeocodeClient, NativeGeocodeProcessTransport, shared intent
    journal, quota and dual leases run unchanged around these doubles.
    """

    def __init__(self, graph, database):
        self.graph, self.database = graph, database
        self.calls, self.objects, self.requests, self.credentials, self.deletes = [], {}, [], [], []
        self.job_id, self.response, self.failure = None, GEO_RESPONSE, None

    def admitted(self, operation):
        assert getattr(self.graph.engine._local, "connection", None) is None, "SQL root spans Geo IO"
        with self.database.connect() as connection:
            status, payload = connection.execute("SELECT status,payload FROM workflow.jobs WHERE job_id=%s",
                                                 (self.job_id,)).fetchone()
        assert status == "RUNNING" and payload["attempt_intents"][-1]["state"] == "ADMITTED"
        assert payload["attempt_intents"][-1]["operation"] == operation
        self.calls.append(operation)

    def run(self, request, *, deadline, guard, credential=None):
        import base64

        from modules.external_data.application.native_intake_process import NativeIORejected

        guard()
        self.admitted("geocode_post")
        assert request["operation"] == "geocode_post" and request["url"] == GEO_ENDPOINT
        assert GEO_API_KEY not in json.dumps(request)
        self.requests.append(json.loads(base64.b64decode(request["body_base64"])))
        self.credentials.append(credential)
        if self.failure is not None:
            raise NativeIORejected(self.failure)
        body = json.dumps(self.response).encode()
        return {"status": 200, "media_type": "application/json", "body_base64": base64.b64encode(body).decode()}

    def factory(self, *, guard):
        from modules.external_data.application.native_assisted_snapshot_transport import (
            GEOCODE_JSON_PURPOSE,
            NativeAssistedSnapshotObjectMetadata,
        )
        from modules.external_data.application.native_snapshot_transport import NativeBucketMetadata

        fixture = self

        class Storage:
            def bucket_info(self, binding, *, deadline):
                guard()
                fixture.admitted("gcs_geocode_bucket")
                return NativeBucketMetadata.from_provider({"kind": "storage#bucket", "name": binding.bucket,
                    "location": "ASIA-EAST1", "locationType": "region"}, binding)

            def metadata(self, binding, key, generation=None):
                data = fixture.objects[key]
                return NativeAssistedSnapshotObjectMetadata.from_provider({"kind": "storage#object",
                    "bucket": binding.bucket, "name": key, "generation": "11", "size": str(len(data)),
                    "contentType": "application/json", "kmsKeyName": GEO_KMS + "/cryptoKeyVersions/2"},
                    binding, key, expected_generation=generation, purpose=GEOCODE_JSON_PURPOSE)

            def upload_with_kms_if_absent(self, binding, key, data, media_type, *, deadline):
                guard()
                fixture.admitted("gcs_geocode_upload")
                assert "/native-intake-geocode/" in key and media_type == "application/json"
                assert key not in fixture.objects
                fixture.objects[key] = data
                return self.metadata(binding, key)

            def head_version(self, binding, key, generation, *, deadline):
                guard()
                fixture.admitted("gcs_geocode_head")
                return self.metadata(binding, key, generation)

            def download_version_bounded(self, binding, key, generation, *, deadline):
                guard()
                fixture.admitted("gcs_geocode_download")
                return fixture.objects[key]

            def delete_exact_generation(self, binding, key, generation, *, deadline):
                guard()
                fixture.admitted("gcs_geocode_delete")
                fixture.deletes.append((key, generation))
                del fixture.objects[key]

        return Storage()


class SimpleGeoCase:
    def __init__(self, graph, ids, database, runtime, owner, io, receipt, parsed, source_id):
        self.graph, self.ids, self.database, self.runtime = graph, ids, database, runtime
        self.owner, self.io, self.receipt, self.parsed, self.source_id = owner, io, receipt, parsed, source_id


class TestNativeGeoExecution(TestNativeGeoReservation):
    """The reserved Geo job runs through the normal default ODayWorker."""

    test_exact_existing_ready_reserves_native_geo_in_the_same_identity_unit = None

    @pytest.fixture
    def geo_case(self, exact_service_case, monkeypatch):
        from pathlib import Path

        from modules.listing.application import pipeline
        from tests.integration.test_canonical_identity_admission_postgresql import _claim

        case, parsed, owner, proposal, policy_id, store = exact_service_case
        ids, database = case[1], case[2]
        root = Path(__file__).resolve().parents[2]
        with database.connect() as connection:
            connection.execute(ADDRESS_DDL)
            connection.execute((root / "infra/db/migrations/000035_native_intake_retrieval_quota.sql").read_text())
            columns = {row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='core' AND table_name='tenants'").fetchall()}
            if "tenant_name" in columns:
                connection.execute("INSERT INTO core.tenants (tenant_id,tenant_name,status,created_at,updated_at) "
                    "VALUES (%s,'Synthetic Geo tenant','active',now(),now()) ON CONFLICT DO NOTHING", (ids["tenant"],))
            else:
                connection.execute("INSERT INTO core.tenants (tenant_id) VALUES (%s) ON CONFLICT DO NOTHING",
                                   (ids["tenant"],))
        source_id = _enable_native_geo(monkeypatch, database, ids["tenant"])
        receipt = owner.confirm_exact_case(ids["tenant"], proposal["match_case_id"],
            expected_case_version=proposal["version"], policy_version_id=policy_id, replay_store=store)
        canonical = owner.graph, ids, None, database
        delivery, registry, runtime = _claim(canonical, receipt["job_id"])
        registry.handle(delivery, runtime)
        io = SyntheticGeoIO(owner.graph, database)
        io.job_id = pipeline.NativeIntakeGeoOwner.job_id(receipt["decision_id"])
        geo_owner = pipeline.NativeIntakeGeoOwner(owner.graph, geocode_runner=io,
                                                   snapshot_transport_factory=io.factory)
        monkeypatch.setattr(pipeline, "native_intake_geo_owner", lambda persistence: geo_owner)
        from apps.worker.oday_worker import main as worker_main

        original_start = worker_main._LeaseHeartbeat.start

        def heartbeat_start(heartbeat):
            if heartbeat._job_id == io.job_id:
                pytest.fail("native Geo must own both leases")
            return original_start(heartbeat)

        monkeypatch.setattr(worker_main._LeaseHeartbeat, "start", heartbeat_start)
        return SimpleGeoCase(owner.graph, ids, database, runtime, geo_owner, io, receipt, parsed, source_id)

    @staticmethod
    def run_default_worker(case):
        from apps.worker.oday_worker.main import ODayWorker
        from shared.jobs.queue import JobStatus

        worker = ODayWorker(persistence=case.runtime, heartbeat_interval_seconds=0.01)
        # The normal loop also drains the preexisting canonical audit deliveries.
        for _ in range(30):
            physical = case.graph.queue.get(case.io.job_id)
            if physical.status != JobStatus.QUEUED or physical.attempts:
                break
            assert worker.run_once() is True
        return case.graph.queue.get(case.io.job_id)

    @staticmethod
    def rows(case, sql, params=()):
        with case.database.connect() as connection:
            cursor = connection.execute(sql, params)
            names = [column.name for column in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def job(self, case):
        return self.rows(case, "SELECT * FROM workflow.jobs WHERE job_id=%s", (case.io.job_id,))[0]

    @EXACT_SERVICE
    def test_default_worker_commits_one_live_geo_evidence_unit_and_replays_cold(self, geo_case):
        from modules.listing.application.intake_audit import CanonicalIntakeAuditSink
        from modules.listing.application.pipeline import (
            NATIVE_GEO_COMPLETED_ACTION,
            NativeIntakeGeoOwner,
        )
        from shared.audit.canonical_delivery import CanonicalAuditDelivery
        from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
        from shared.domain.events import validate_event
        from shared.infrastructure.persistence.factory import native_intake_geo_provider
        from shared.infrastructure.persistence.identity_graph_sql import (
            CanonicalIdentityGraphRepository,
        )
        from shared.infrastructure.persistence.job_queue import DurableJobQueue
        from shared.infrastructure.persistence.postgresql import PostgresEngine
        from shared.jobs.queue import JobStatus

        case = geo_case
        intake_sql = "SELECT processing_state,version,resolved_listing_id FROM intake.intakes WHERE intake_id=%s"
        intake_before = self.rows(case, intake_sql, (case.parsed["intake_id"],))
        physical = self.run_default_worker(case)
        assert physical.status == JobStatus.SUCCEEDED, json.dumps(self.job(case)["last_error"], default=str)
        assert case.io.calls == ["gcs_geocode_bucket", "geocode_post", "gcs_geocode_upload", "gcs_geocode_head",
                                 "gcs_geocode_download"]
        # One request from the original client; the key travels only on the
        # private credential channel, never in the child request or journal.
        assert len(case.io.requests) == 1 and set(case.io.requests[0]) == {"address"}
        assert case.io.credentials == [GEO_API_KEY]
        job = self.job(case)
        assert job["status"] == "SUCCEEDED" and job["checkpoint"] == "GEOCODED" and job["attempt"] == 1
        result = job["result"]
        assert GEO_API_KEY not in json.dumps(job["payload"]) + json.dumps(result)
        assert result["origin_id"] == case.receipt["decision_id"] and result["listing_id"] == case.ids["listing"]
        assert result["origin_kind"] == "IDENTITY_DECISION"
        geocode = result["geocode"]
        assert geocode["provider_request_id"] == "synthetic-provider-request-1"
        assert geocode["provider_observed_at"] == "2026-11-20T08:00:00+00:00"
        assert geocode["precision"] == "rooftop" and geocode["confidence"] == 0.93 and geocode["h3_res_9"]
        address = self.rows(case, "SELECT * FROM core.address_locations WHERE address_id=%s", (result["address_id"],))
        assert len(address) == 1 and str(address[0]["tenant_id"]) == case.ids["tenant"]
        assert address[0]["revision"] == 1 and float(address[0]["latitude"]) == 25.0339
        assert address[0]["h3_res_9"] == geocode["h3_res_9"]
        snapshot = self.rows(case, "SELECT * FROM intake.source_snapshots WHERE source_snapshot_id=%s",
                             (result["source_snapshot_id"],))[0]
        assert snapshot["source_id"] == case.source_id and snapshot["capture_method"] == "SERVER_RETRIEVAL"
        assert snapshot["media_type"] == "application/json" and snapshot["original_url"] is None
        assert "/native-intake-geocode/" in snapshot["raw_object_uri"]
        assert [json.loads(value) for value in case.io.objects.values()] == [GEO_RESPONSE]
        event = self.rows(case, "SELECT payload FROM workflow.outbox_events WHERE event_id=%s",
                          (result["snapshot_created_event_id"],))[0]["payload"]
        assert not validate_event(event) and event["payload"]["source_id"] == case.source_id
        audits = self.rows(case, "SELECT audit_event_id FROM audit.audit_events WHERE action=%s",
                           (NATIVE_GEO_COMPLETED_ACTION,))
        assert [str(row["audit_event_id"]) for row in audits] == [result["audit_event_id"]]
        # Geo evidence never changes identity or promotes anything.
        assert self.rows(case, intake_sql, (case.parsed["intake_id"],)) == intake_before
        # Replay through the same owner adds no IO; a cold engine re-proves it.
        calls = list(case.io.calls)
        delivery = case.graph.queue.get(case.io.job_id)
        assert case.owner.execute(delivery) == result and case.io.calls == calls
        engine = PostgresEngine(case.database.url(), bootstrap=False, validate_schema=False)
        try:
            queue = DurableJobQueue(engine)
            writer = CanonicalPostgresAuditLog(engine)
            # A cold runtime graph carries the same factory-composed LIVE adapter.
            cold = CanonicalIdentityGraphRepository(engine, queue, CanonicalIntakeAuditSink(writer,
                case.graph.audit_sink.retention, CanonicalAuditDelivery(writer, queue, case.graph.audit_sink.delivery.sink)),
                geo_provider=native_intake_geo_provider())
            assert NativeIntakeGeoOwner(cold).execute(queue.get(case.io.job_id)) == result
        finally:
            engine.close()
        assert case.io.calls == calls

    @EXACT_SERVICE
    def test_missing_provider_result_fails_without_address_or_snapshot(self, geo_case):
        from shared.jobs.queue import JobStatus

        case = geo_case
        case.io.response = {}
        physical = self.run_default_worker(case)
        assert physical.status == JobStatus.FAILED, json.dumps(self.job(case)["last_error"], default=str)
        job = self.job(case)
        assert job["status"] == "FAILED" and job["result"] is None
        assert job["last_error"]["code"] == "NATIVE_GEO_RESULT_MISSING"
        assert case.io.calls == ["gcs_geocode_bucket", "geocode_post"]
        assert self.rows(case, "SELECT 1 FROM core.address_locations") == []
        assert self.rows(case, "SELECT 1 FROM intake.source_snapshots WHERE source_id=%s", (case.source_id,)) == []
        dead = self.rows(case, "SELECT payload FROM workflow.outbox_events WHERE event_type='job.dead_lettered' "
                               "AND aggregate_id=%s", (case.io.job_id,))
        assert len(dead) == 1 and dead[0]["payload"]["payload"]["job_type"] == "native-intake-geocode"

    @EXACT_SERVICE
    def test_changed_listing_version_closes_job_without_io_or_evidence(self, geo_case):
        from shared.jobs.queue import JobStatus

        case = geo_case
        with case.database.connect() as connection:
            connection.execute("UPDATE expansion.listings SET version=version+1 WHERE listing_id=%s",
                               (case.ids["listing"],))
        physical = self.run_default_worker(case)
        assert physical.status == JobStatus.FAILED and case.io.calls == []
        job = self.job(case)
        assert job["last_error"]["code"] == "NATIVE_GEO_ACCEPTANCE_REJECTED" and job["result"] is None
        assert self.rows(case, "SELECT 1 FROM core.address_locations") == []

    @EXACT_SERVICE
    def test_provider_timeout_schedules_bounded_retry_with_new_generation(self, geo_case):
        from shared.jobs.queue import JobDeliveryState, JobStatus

        case = geo_case
        case.io.failure = "NATIVE_IO_DEADLINE_EXPIRED"
        physical = self.run_default_worker(case)
        assert physical.status == JobStatus.QUEUED, json.dumps(self.job(case)["last_error"], default=str)
        assert physical.delivery_state == JobDeliveryState.RETRYING
        job = self.job(case)
        assert job["status"] == "RETRYING" and job["payload"]["delivery_generation"] == 2 and job["attempt"] == 1
        assert physical.payload["attempt_token"].startswith("2.")
        assert self.rows(case, "SELECT 1 FROM core.address_locations") == []
        assert [i["state"] for i in job["payload"]["attempt_intents"]] == ["FINISHED", "ERROR"]

    @EXACT_SERVICE
    def test_address_write_failure_rolls_back_snapshot_event_and_result(self, geo_case, monkeypatch):
        from shared.infrastructure.persistence.repositories import DurableAddressLocationRepository
        from shared.jobs.queue import JobStatus

        case = geo_case

        def unavailable(self, address):
            raise RuntimeError("synthetic Address persistence unavailable")

        monkeypatch.setattr(DurableAddressLocationRepository, "save_address", unavailable)
        physical = self.run_default_worker(case)
        assert physical.status == JobStatus.FAILED, json.dumps(self.job(case)["last_error"], default=str)
        job = self.job(case)
        assert job["result"] is None and job["status"] == "FAILED"
        assert self.rows(case, "SELECT 1 FROM intake.source_snapshots WHERE source_id=%s", (case.source_id,)) == []
        assert self.rows(case, "SELECT 1 FROM workflow.outbox_events WHERE event_type='snapshot.created' "
                               "AND payload->>'causation_id'=%s", (case.receipt["decision_id"],)) == []
        assert self.rows(case, "SELECT 1 FROM core.address_locations") == []


class TestNativeGeoFreshIdentity(TestNativeGeoReservation):
    """Fresh CREATE/REVISE READY through the original human-confirmed flow."""

    test_exact_existing_ready_reserves_native_geo_in_the_same_identity_unit = None

    @pytest.mark.parametrize(("matching_case", "parser_admission_case"), [
        pytest.param("NEW", None, id="CREATE"), pytest.param("REVISION", None, id="REVISE"),
    ], indirect=["matching_case", "parser_admission_case"])
    def test_fresh_create_and_revise_ready_reserve_native_geo(self, matching_case, case_api, monkeypatch):
        import tests.integration.test_native_matching_case_command_postgresql as matching
        from modules.listing.application.native_intake_retrieval_policy import GEO_JOB_TYPE

        case, parsed, _, _, _, _, mode = matching_case
        ids, database = case[1], case[2]
        _enable_native_geo(monkeypatch, database, ids["tenant"])
        # The unchanged original HTTP confirmation + registered executor flow.
        matching.test_service_match_case_human_confirmation_and_registered_executor_share_real_native_flow(
            matching_case, case_api, monkeypatch, None)
        with database.connect() as connection:
            intake = connection.execute("SELECT processing_state,resolved_listing_id,version FROM intake.intakes "
                                        "WHERE intake_id=%s", (parsed["intake_id"],)).fetchone()
            jobs = connection.execute("SELECT job_id,status,checkpoint,payload FROM workflow.jobs WHERE job_type=%s "
                                      "AND aggregate_id=%s", (GEO_JOB_TYPE, parsed["intake_id"])).fetchall()
            assert intake[0] == "READY" and len(jobs) == 1, (intake, jobs)
            job_id, status, checkpoint, payload = jobs[0]
            listing = connection.execute("SELECT version,current_revision_id,property_id FROM expansion.listings "
                                         "WHERE listing_id=%s", (str(intake[1]),)).fetchone()
            revision = connection.execute("SELECT revision_kind,source_snapshot_id FROM expansion.listing_revisions "
                                          "WHERE listing_revision_id=%s", (str(listing[1]),)).fetchone()
            transition = connection.execute("SELECT to_state,job_id FROM intake.intake_stage_transitions "
                                            "WHERE transition_id=%s", (payload["ready_transition_id"],)).fetchone()
        assert (status, checkpoint) == ("QUEUED", "GEOCODING") and payload["origin_kind"] == "IDENTITY_DECISION"
        assert str(job_id) == str(uuid5(UUID(payload["origin_id"]), GEO_JOB_TYPE))
        assert payload["listing_id"] == str(intake[1]) and payload["intake_version"] == intake[2]
        assert payload["listing_version"] == listing[0] and payload["listing_revision_id"] == str(listing[1])
        assert payload["property_id"] == str(listing[2])
        assert revision[0] == ("CREATED" if mode == "NEW" else "CONTENT_CHANGED")
        assert payload["source_snapshot_id"] == str(revision[1])
        assert transition[0] == "READY" and str(transition[1]) == payload["identity_job_id"]
