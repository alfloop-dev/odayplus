"""Synthetic approved decisions; real native acceptance/rollback/failure rows."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
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
    from urllib.parse import urlsplit

    from modules.listing.application.native_intake_retrieval_policy import (
        GEO_BINDING_ENV,
        GEO_POLICY_ENV,
        GEO_SIGNING_KEY_ENV,
    )
    from modules.listing.application.native_intake_url_identity import NATIVE_URL_RULE_VERSION

    source_id = "geo.synthetic-registered-" + uuid4().hex[:8]
    with database.connect() as connection:
        connection.execute("INSERT INTO intake.source_registry (source_id,display_name,canonicalization_rule_version,"
            "retrieval_mode,legal_approval_ref,license_approval_ref,policy_owner_subject_id,review_expires_at,"
            "kill_switch,production_enabled,requests_per_minute,concurrent_requests,version,allowed_hosts) "
            "VALUES (%s,'Synthetic Geo provider',%s,'APPROVED_RETRIEVAL',"
            "'synthetic-geo-legal','synthetic-geo-license',%s,now()+interval '1 day',false,true,10,2,3,ARRAY[%s])",
            (source_id, NATIVE_URL_RULE_VERSION, str(uuid4()), urlsplit(GEO_ENDPOINT).hostname))
        connection.commit()
    monkeypatch.setenv(GEO_POLICY_ENV, json.dumps({"policy_id": "synthetic-native-geo", "version": 1,
        "tenant_ids": [tenant], "max_attempts": 2, "deadline_seconds": 120, "backoff_seconds": [5]}))
    monkeypatch.setenv(GEO_BINDING_ENV, json.dumps({"version": 1, "global_max_concurrency": 2,
        "source": {"source_id": source_id, "source_policy_version": source_version,
                   "provider_id": "geocode.primary_api", "source_contract_id": "geocode_result_snapshot"},
        "storage_bindings": [{"tenant_id": tenant, "bucket": GEO_BUCKET, "kms_key_name": GEO_KMS,
            "residency_mode": "TW_ONLY", "retention_class": "STANDARD"}]}))
    monkeypatch.setenv(GEO_SIGNING_KEY_ENV, "Explicit synthetic native Geo dispatch signing material only")
    # Only an explicit LIVE provider with an HTTPS endpoint is admitted.
    monkeypatch.setenv("ODP_EXTERNAL_PROVIDER_MODE", "live")
    monkeypatch.setenv("ODP_PRODUCTION_PROVIDER_IDS", "geocode.primary_api")
    monkeypatch.setenv("ODP_GEOCODE_PROVIDER_URL", GEO_ENDPOINT)
    monkeypatch.setenv("ODP_GEOCODE_PROVIDER_API_KEY", GEO_API_KEY)
    monkeypatch.delenv("ODP_GEOCODE_PROVIDER_AUTH_STATUS", raising=False)
    monkeypatch.setenv("ODP_RESIDENCY_APPROVED_BUCKETS_TW_ONLY", GEO_BUCKET)
    return source_id


def _geo_provider_license(monkeypatch, **changes):
    """The unchanged original validation over a changed registered Geo licence."""
    from dataclasses import replace
    from importlib import import_module

    registry = import_module("modules.external_data.connectors.provider_registry")
    monkeypatch.setattr(registry, "PROVIDER_REGISTRY", tuple(
        replace(entry, license=replace(entry.license, **changes)) if entry.provider_id == "geocode.primary_api" else entry
        for entry in registry.PROVIDER_REGISTRY))


GEO_SOURCE_REFUSALS = {
    "zero_rate_limit": "requests_per_minute=0", "zero_concurrency": "concurrent_requests=0",
    "unassociated_endpoint_host": "allowed_hosts=ARRAY['other.synthetic.invalid']",
    "source_expired": "review_expires_at=now()-interval '1 minute'", "source_killed": "kill_switch=true"}


def _refuse_geo(monkeypatch, database, source_id, refusal):
    """Withdraw exactly one Geo provider/source admission input."""
    from datetime import date

    if refusal in GEO_SOURCE_REFUSALS:
        with database.connect() as connection:
            connection.execute("UPDATE intake.source_registry SET " + GEO_SOURCE_REFUSALS[refusal] + " WHERE source_id=%s", (source_id,))
    elif refusal == "fixture_mode":
        # Fixture/disabled/unknown provider modes never forge READY geodata.
        monkeypatch.setenv("ODP_EXTERNAL_PROVIDER_MODE", "fixture")
    elif refusal == "provider_not_selected":
        monkeypatch.setenv("ODP_PRODUCTION_PROVIDER_IDS", "poi.commercial_api")
        monkeypatch.setenv("ODP_POI_PROVIDER_API_KEY", "-".join(["synthetic", "other", "provider", "value"]))
    elif refusal == "credential_revoked":
        monkeypatch.setenv("ODP_GEOCODE_PROVIDER_AUTH_STATUS", "revoked")
    elif refusal == "credential_missing":
        monkeypatch.setenv("ODP_GEOCODE_PROVIDER_API_KEY", "changeme")
    elif refusal == "license_expired":
        _geo_provider_license(monkeypatch, expires_on=date(2026, 1, 1))
    else:
        raise AssertionError(refusal)


GEO_ADMISSION_REFUSALS = ["fixture_mode", "provider_not_selected", "credential_revoked", "credential_missing",
                          "license_expired", *GEO_SOURCE_REFUSALS]


class TestNativeGeoReservation:
    """The exact-existing flow needs parser-realm databases; scope them here only."""

    @pytest.fixture
    def intake_blank_db(self, intake_pg_server, parser_realm, record_property):
        yield from _parser_intake_blank_db._get_wrapped_function()(intake_pg_server, parser_realm, record_property)

    @pytest.fixture
    def intake_db(self, intake_blank_db):
        return _matching_intake_db._get_wrapped_function()(intake_blank_db)

    @EXACT_SERVICE
    @pytest.mark.parametrize("geo", ["disabled", "stale_source_version", "fixture_mode", "credential_revoked",
                                     "zero_rate_limit", "unassociated_endpoint_host", "enabled",
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
            source_id = _enable_native_geo(monkeypatch, database, ids["tenant"],
                                           source_version=2 if geo == "stale_source_version" else 3)
        if geo in GEO_ADMISSION_REFUSALS:
            _refuse_geo(monkeypatch, database, source_id, geo)
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
        if geo in {"disabled", "stale_source_version", *GEO_ADMISSION_REFUSALS}:
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
        # The consuming parser phase and the existing revision stay separate.
        lineage, binding = payload["input_lineage"], payload["property_binding"]
        assert payload["parser_run_id"] in {run["parser_run_id"] for run in lineage["parser_runs"]}
        assert lineage["revision"]["parser_run_id"] != payload["parser_run_id"]
        assert lineage["corrections"] and {row["status"] for row in lineage["corrections"]} == {"PROPOSED"}
        assert binding["redirect_path"][-1] == payload["property_id"]
        assert lineage["revision"]["material_fingerprint"] and len(lineage["revision"]["effective_values_sha256"]) == 64
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
                           "provider_id": "vendor-claimed-label", "city": "台北市", "district": "信義區"}}
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
        self.upload_failure, self.after_exchange = None, None

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
        if self.after_exchange is not None:
            self.after_exchange()
        return {"status": 200, "media_type": "application/json", "body_base64": base64.b64encode(body).decode()}

    def factory(self, *, guard):
        from modules.external_data.application.native_assisted_snapshot_transport import (
            GEOCODE_JSON_PURPOSE,
            NativeAssistedSnapshotObjectMetadata,
        )
        from modules.external_data.application.native_intake_process import NativeIORejected
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
                if fixture.upload_failure is not None:
                    raise NativeIORejected(fixture.upload_failure[0], status=fixture.upload_failure[1])
                fixture.objects[key] = data
                return self.metadata(binding, key)

            def head_version(self, binding, key, generation, *, deadline):
                guard()
                fixture.admitted("gcs_geocode_head")
                if key not in fixture.objects:
                    raise NativeIORejected("NATIVE_GCS_HTTP_REJECTED", status=404)
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


def _prepare_geo_database(database, tenant):
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    with database.connect() as connection:
        connection.execute(ADDRESS_DDL)
        connection.execute((root / "infra/db/migrations/000035_native_intake_retrieval_quota.sql").read_text())
        columns = {row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='core' AND table_name='tenants'").fetchall()}
        if "tenant_name" in columns:
            connection.execute("INSERT INTO core.tenants (tenant_id,tenant_name,status,created_at,updated_at) "
                "VALUES (%s,'Synthetic Geo tenant','active',now(),now()) ON CONFLICT DO NOTHING", (tenant,))
        else:
            connection.execute("INSERT INTO core.tenants (tenant_id) VALUES (%s) ON CONFLICT DO NOTHING", (tenant,))


def _geo_worker_case(monkeypatch, graph, ids, database, runtime, origin_id, parsed, source_id):
    """Compose the normal default worker; only child and storage are synthetic.

    Same composition as native_intake_geo_owner(persistence): the runtime
    factory graph carries the injected LIVE adapter.
    """
    from apps.worker.oday_worker import main as worker_main
    from modules.listing.application import pipeline

    io = SyntheticGeoIO(graph, database)
    io.job_id = pipeline.NativeIntakeGeoOwner.job_id(origin_id)
    geo_owner = pipeline.NativeIntakeGeoOwner(runtime.canonical_identity_graph(), geocode_runner=io,
                                               snapshot_transport_factory=io.factory)
    monkeypatch.setattr(pipeline, "native_intake_geo_owner", lambda persistence: geo_owner)
    original_start = worker_main._LeaseHeartbeat.start

    def heartbeat_start(heartbeat):
        if heartbeat._job_id == io.job_id:
            pytest.fail("native Geo must own both leases")
        return original_start(heartbeat)

    monkeypatch.setattr(worker_main._LeaseHeartbeat, "start", heartbeat_start)
    return SimpleGeoCase(graph, ids, database, runtime, geo_owner, io, {"decision_id": origin_id}, parsed, source_id)


def _run_geo_worker(case):
    from apps.worker.oday_worker.main import ODayWorker
    from shared.jobs.queue import JobStatus

    worker = ODayWorker(persistence=case.runtime, heartbeat_interval_seconds=0.01)
    # The normal loop also drains the preexisting canonical audit deliveries.
    for _ in range(60):
        physical = case.graph.queue.get(case.io.job_id)
        if physical.status != JobStatus.QUEUED or physical.attempts:
            break
        assert worker.run_once() is True
    return case.graph.queue.get(case.io.job_id)


def _geo_rows(case, sql, params=()):
    with case.database.connect() as connection:
        cursor = connection.execute(sql, params)
        names = [column.name for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _geo_job(case):
    return _geo_rows(case, "SELECT * FROM workflow.jobs WHERE job_id=%s", (case.io.job_id,))[0]


def _assert_committed_geo_evidence(case):
    """One exchange, one snapshot of the Geo source, one Address, one replay."""
    from shared.jobs.queue import JobStatus

    physical = _run_geo_worker(case)
    job = _geo_job(case)
    assert physical.status == JobStatus.SUCCEEDED, json.dumps(job["last_error"], default=str)
    assert case.io.calls == ["gcs_geocode_bucket", "geocode_post", "gcs_geocode_upload", "gcs_geocode_head",
                             "gcs_geocode_download"]
    result, payload = job["result"], job["payload"]
    for key in ("origin_kind", "origin_id", "listing_id", "listing_revision_id", "property_id"):
        assert result[key] == payload[key]
    address = _geo_rows(case, "SELECT * FROM core.address_locations")
    assert [str(row["address_id"]) for row in address] == [result["address_id"]]
    assert str(address[0]["tenant_id"]) == case.ids["tenant"] and address[0]["revision"] == 1
    snapshots = _geo_rows(case, "SELECT * FROM intake.source_snapshots WHERE source_id=%s", (case.source_id,))
    assert [str(row["source_snapshot_id"]) for row in snapshots] == [result["source_snapshot_id"]]
    calls = list(case.io.calls)
    assert case.owner.execute(case.graph.queue.get(case.io.job_id)) == result and case.io.calls == calls
    return job


def _assert_no_geo_evidence(case):
    assert _geo_job(case)["result"] is None
    assert _geo_rows(case, "SELECT 1 FROM core.address_locations") == []
    assert _geo_rows(case, "SELECT 1 FROM intake.source_snapshots WHERE source_id=%s", (case.source_id,)) == []


def _geo_parent_changes(ids, payload):
    """Each single change that must stop the Geo parent: (expected code, statements)."""
    correction = payload["input_lineage"]["corrections"][0]["correction_id"]
    receipt, graph, lineage = ("NATIVE_GEO_PARENT_RECEIPT_REJECTED", "NATIVE_GEO_CURRENT_IDENTITY_GRAPH_CHANGED",
                               "NATIVE_GEO_CURRENT_LINEAGE_CHANGED")
    return {
        "listing_version": ("NATIVE_GEO_CURRENT_LISTING_CHANGED", [
            ("UPDATE expansion.listings SET version=version+1 WHERE listing_id=%s", (ids["listing"],))]),
        # A rolled back, unfinished or rewritten identity unit is no parent.
        "identity_result": (receipt, [("UPDATE workflow.jobs SET result=jsonb_set(result,'{audit_event_id}',"
            "to_jsonb(%s::text)) WHERE job_id=%s", (str(uuid4()), payload["identity_job_id"]))]),
        "identity_uncommitted": (receipt, [("UPDATE workflow.jobs SET status='RUNNING',result=NULL WHERE job_id=%s",
                                            (payload["identity_job_id"],))]),
        "parser_version": (lineage, [("UPDATE intake.parser_runs SET version=version+1 WHERE parser_run_id=%s",
                                      (payload["parser_run_id"],))]),
        "parser_status": (lineage, [("UPDATE intake.parser_runs SET status='PARTIAL' WHERE parser_run_id=%s",
                                     (payload["parser_run_id"],))]),
        "correction_version": (lineage, [("UPDATE intake.human_corrections SET version=version+1 WHERE correction_id=%s",
                                          (correction,))]),
        "correction_value": (lineage, [("UPDATE intake.human_corrections SET after_effective_value='\"changed\"'::jsonb "
                                        "WHERE correction_id=%s", (correction,))]),
        "correction_added": (lineage, [("INSERT INTO intake.human_corrections (tenant_id,intake_id,field_path,"
            "field_classification,corrected_value,after_effective_value,reason,proposed_by,status) SELECT tenant_id,"
            "intake_id,'lateField',field_classification,corrected_value,after_effective_value,reason,proposed_by,"
            "'PROPOSED' FROM intake.human_corrections WHERE correction_id=%s", (correction,))]),
        "revision_fingerprint": (lineage, [("UPDATE expansion.listing_revisions SET material_fingerprint=repeat('e',64) "
                                            "WHERE listing_revision_id=%s", (payload["listing_revision_id"],))]),
        "property_version": ("NATIVE_GEO_CURRENT_PROPERTY_CHANGED", [
            ("UPDATE identity.properties SET version=version+1 WHERE property_id=%s", (payload["property_id"],))]),
        "property_merged": ("NATIVE_GEO_CURRENT_PROPERTY_CHANGED", [
            ("UPDATE identity.properties SET status='MERGED',current_redirect_property_id=%s,version=version+1 "
             "WHERE property_id=%s", (ids["target"], payload["property_id"])),
            ("INSERT INTO identity.property_redirects (tenant_id,from_property_id,to_property_id,decision_id) "
             "VALUES (%s,%s,%s,%s)", (ids["tenant"], payload["property_id"], ids["target"], payload["origin_id"]))]),
        "source_edge_moved": (graph, [("UPDATE identity.source_identity_edges SET property_id=%s WHERE edge_id=%s",
                                       (ids["target"], payload["property_binding"]["source_edge_id"]))]),
        "source_edge_ended": (graph, [("UPDATE identity.source_identity_edges SET effective_to=clock_timestamp() "
                                       "WHERE edge_id=%s", (payload["property_binding"]["source_edge_id"],))]),
    }


class TestNativeGeoExecution(TestNativeGeoReservation):
    """The reserved Geo job runs through the normal default ODayWorker."""

    test_exact_existing_ready_reserves_native_geo_in_the_same_identity_unit = None

    @pytest.fixture
    def geo_case(self, exact_service_case, monkeypatch):
        from tests.integration.test_canonical_identity_admission_postgresql import _claim

        case, parsed, owner, proposal, policy_id, store = exact_service_case
        ids, database = case[1], case[2]
        _prepare_geo_database(database, ids["tenant"])
        source_id = _enable_native_geo(monkeypatch, database, ids["tenant"])
        receipt = owner.confirm_exact_case(ids["tenant"], proposal["match_case_id"],
            expected_case_version=proposal["version"], policy_version_id=policy_id, replay_store=store)
        delivery, registry, runtime = _claim((owner.graph, ids, None, database), receipt["job_id"])
        registry.handle(delivery, runtime)
        geo = _geo_worker_case(monkeypatch, owner.graph, ids, database, runtime, receipt["decision_id"], parsed, source_id)
        geo.receipt = receipt
        return geo

    run_default_worker = staticmethod(_run_geo_worker)
    rows = staticmethod(_geo_rows)
    job = staticmethod(_geo_job)

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
        # The selected registered provider/source is never the vendor's label,
        # and each recorded time names whose clock it is.
        assert result["provider"] == {"provider_id": "geocode.primary_api", "source_id": case.source_id,
            "source_contract_id": "geocode_result_snapshot", "source_policy_version": 3,
            "vendor_provider_claim": "vendor-claimed-label"}
        times = result["timestamps"]
        assert times["provider_observed_at"] == "2026-11-20T08:00:00+00:00"
        assert times["provider_observed_at_basis"] == "PROVIDER_SUPPLIED"
        assert times["fetched_at_basis"] == "WORKER_CLOCK_RESPONSE_RECEIVED" and times["captured_at_basis"] == "FETCHED_AT"
        assert times["fetched_at"] not in {times["exchange_admitted_at"], times["provider_observed_at"]}
        assert len(result["address_record_sha256"]) == 64
        assert geocode["precision"] == "rooftop" and geocode["confidence"] == 0.93 and geocode["h3_res_9"]
        address = self.rows(case, "SELECT * FROM core.address_locations WHERE address_id=%s", (result["address_id"],))
        assert len(address) == 1 and str(address[0]["tenant_id"]) == case.ids["tenant"]
        assert address[0]["revision"] == 1 and float(address[0]["latitude"]) == 25.0339
        assert address[0]["h3_res_9"] == geocode["h3_res_9"]
        snapshot = self.rows(case, "SELECT * FROM intake.source_snapshots WHERE source_snapshot_id=%s",
                             (result["source_snapshot_id"],))[0]
        assert snapshot["source_id"] == case.source_id and snapshot["capture_method"] == "SERVER_RETRIEVAL"
        assert snapshot["media_type"] == "application/json" and snapshot["original_url"] is None
        assert snapshot["captured_at"] == snapshot["observed_at"] == datetime.fromisoformat(times["fetched_at"])
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
    def test_unknown_provider_time_and_label_stay_unknown(self, geo_case):
        case = geo_case
        case.io.response = {"request_id": "synthetic-provider-request-2", "result": {
            key: value for key, value in GEO_RESPONSE["result"].items() if key != "provider_id"}}
        result = _assert_committed_geo_evidence(case)["result"]
        assert result["provider"]["vendor_provider_claim"] is None
        assert result["provider"]["provider_id"] == "geocode.primary_api"
        assert result["timestamps"]["provider_observed_at"] is None
        assert result["timestamps"]["provider_observed_at_basis"] == "UNKNOWN"

    @EXACT_SERVICE
    @pytest.mark.parametrize("change", [{"latitude": 0.0, "longitude": 0.0}, {"precision": "guess"}],
                             ids=["out_of_market", "precision"])
    def test_invalid_provider_result_is_never_geo_evidence(self, geo_case, change):
        from shared.jobs.queue import JobStatus

        case = geo_case
        case.io.response = {**GEO_RESPONSE, "result": {**GEO_RESPONSE["result"], **change}}
        assert self.run_default_worker(case).status == JobStatus.FAILED
        assert self.job(case)["last_error"]["code"] == "NATIVE_GEO_RESULT_INVALID"
        assert case.io.calls == ["gcs_geocode_bucket", "geocode_post"]
        _assert_no_geo_evidence(case)

    @EXACT_SERVICE
    def test_every_parent_source_and_address_change_is_refused_by_the_fresh_guards(self, geo_case, monkeypatch):
        """One committed READY; each change is applied and rolled back alone."""
        from modules.listing.application.native_intake_retrieval_policy import (
            NativeIntakeGeoUnadmitted,
        )
        from shared.jobs.queue import JobFenceRejectedError

        case = geo_case
        payload, engine, tenant = self.job(case)["payload"], case.owner.engine, case.ids["tenant"]

        class RolledBack(Exception):
            pass

        def intake():
            return case.owner.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=?",
                                         (tenant, case.parsed["intake_id"]))

        def admission():
            return case.owner._admission(tenant, intake(), payload["listing_id"], payload["listing_revision_id"],
                                         case.io.job_id)

        with case.owner.graph.transaction(tenant):
            case.owner._parent(tenant, intake(), payload, lambda: None)
            assert case.owner._current_source(payload, lambda: None)[1].target_state == "GEOCODING"
            assert admission()[0].sha256 == payload["policy_sha256"]
        changes = _geo_parent_changes(case.ids, payload)
        for change, (code, statements) in changes.items():
            with pytest.raises(RolledBack), case.owner.graph.transaction(tenant):
                for sql, params in statements:
                    assert engine.execute(sql.replace("%s", "?"), params).rowcount == 1, change
                with pytest.raises(JobFenceRejectedError, match=code):
                    case.owner._parent(tenant, intake(), payload, lambda: None)
                raise RolledBack
        with monkeypatch.context() as patch, case.owner.graph.transaction(tenant):
            writer = case.owner.graph.audit_sink.writer
            original = writer.get_event

            def unavailable(scope, audit_id):
                event = original(scope, audit_id)
                return None if event and event["action"] == "identity.executed.v1" else event

            patch.setattr(writer, "get_event", unavailable)
            with pytest.raises(JobFenceRejectedError, match="NATIVE_GEO_PARENT_RECEIPT_REJECTED"):
                case.owner._parent(tenant, intake(), payload, lambda: None)
        for refusal in GEO_ADMISSION_REFUSALS:
            with (pytest.raises(RolledBack), monkeypatch.context() as patch,
                    case.owner.graph.transaction(tenant)):
                if refusal in GEO_SOURCE_REFUSALS:
                    assert engine.execute("UPDATE intake.source_registry SET " + GEO_SOURCE_REFUSALS[refusal]
                                          + " WHERE source_id=?", (case.source_id,)).rowcount == 1
                else:
                    _refuse_geo(patch, None, case.source_id, refusal)
                assert case.owner._current_source(payload, lambda: None)[1].target_state == "DENIED", refusal
                with pytest.raises(NativeIntakeGeoUnadmitted):
                    admission()
                raise RolledBack
        # The unchanged READY is still admitted and commits through the worker;
        # then no committed Address column can change under its replay.
        _assert_committed_geo_evidence(case)
        delivery = case.graph.queue.get(case.io.job_id)
        for change in ("geocode_confidence=0.10", "raw_address='changed'", "h3_res_9='changed'", "revision=2",
                       "latitude=latitude+0.001", "manual_override_flag=true"):
            with pytest.raises(RolledBack), case.owner.graph.transaction(tenant):
                assert engine.execute("UPDATE address_locations SET " + change).rowcount == 1
                with pytest.raises(JobFenceRejectedError, match="NATIVE_GEO_TERMINAL_ADDRESS_CHANGED"):
                    case.owner.committed_result(delivery, tenant, 1)
                raise RolledBack

    @EXACT_SERVICE
    @pytest.mark.parametrize("change", ["listing_version", "identity_uncommitted", "parser_version", "correction_added",
                                        "source_edge_moved"])
    def test_changed_parent_closes_job_without_io_or_evidence(self, geo_case, change):
        from shared.jobs.queue import JobStatus

        case = geo_case
        code, statements = _geo_parent_changes(case.ids, self.job(case)["payload"])[change]
        with case.database.connect() as connection:
            for sql, params in statements:
                assert connection.execute(sql, params).rowcount == 1
        physical = self.run_default_worker(case)
        error = self.job(case)["last_error"]
        assert physical.status == JobStatus.FAILED and case.io.calls == [], error
        assert error["code"] == "NATIVE_GEO_ACCEPTANCE_REJECTED" and error["causes"][0]["code"] == code, error
        _assert_no_geo_evidence(case)

    @EXACT_SERVICE
    @pytest.mark.parametrize("refusal", ["provider_not_selected", "credential_revoked", "license_expired",
                                         "zero_concurrency"])
    def test_provider_or_source_withdrawn_after_reservation_admits_no_io(self, geo_case, monkeypatch, refusal):
        from shared.jobs.queue import JobStatus

        case = geo_case
        _refuse_geo(monkeypatch, case.database, case.source_id, refusal)
        assert self.run_default_worker(case).status == JobStatus.FAILED
        error = self.job(case)["last_error"]
        assert error["code"] == "NATIVE_GEO_ACCEPTANCE_REJECTED" and case.io.calls == []
        assert error["causes"][0]["code"] == "NATIVE_GEO_CURRENT_SOURCE_DENIED"
        _assert_no_geo_evidence(case)

    @EXACT_SERVICE
    @pytest.mark.parametrize("change", ["credential_revoked", "identity"])
    def test_authority_lost_during_exchange_commits_nothing(self, geo_case, monkeypatch, change):
        from shared.jobs.queue import JobStatus

        case = geo_case
        payload = self.job(case)["payload"]

        def lose():
            if change in GEO_ADMISSION_REFUSALS:
                return _refuse_geo(monkeypatch, case.database, case.source_id, change)
            with case.database.connect() as connection:
                connection.execute("UPDATE workflow.jobs SET result=NULL,status='RUNNING' WHERE job_id=%s",
                                   (payload["identity_job_id"],))

        case.io.after_exchange = lose
        assert self.run_default_worker(case).status == JobStatus.FAILED
        assert case.io.calls == ["gcs_geocode_bucket", "geocode_post"]
        assert self.job(case)["last_error"]["code"] == (
            "NATIVE_GEO_CURRENT_SOURCE_DENIED" if change in GEO_ADMISSION_REFUSALS else "NATIVE_GEO_CURRENT_AUTHORITY_LOST")
        _assert_no_geo_evidence(case)

    @EXACT_SERVICE
    def test_existing_address_is_never_overwritten(self, geo_case, monkeypatch):
        from modules.listing.application.pipeline import NativeIntakeGeoOwner
        from shared.jobs.queue import JobStatus

        case, existing = geo_case, str(uuid4())
        with case.database.connect() as connection:
            connection.execute("INSERT INTO core.address_locations (address_id,raw_address,tenant_id,latitude,longitude) "
                               "VALUES (%s,'Synthetic earlier address',%s,24.5,120.5)", (existing, case.ids["tenant"]))
        before = self.rows(case, "SELECT * FROM core.address_locations")
        monkeypatch.setattr(NativeIntakeGeoOwner, "_mint_address_id", staticmethod(lambda: existing))
        assert self.run_default_worker(case).status == JobStatus.FAILED
        job = self.job(case)
        assert job["result"] is None and job["last_error"]["causes"][0]["code"] == "NATIVE_GEO_ADDRESS_COLLISION", job["last_error"]
        assert self.rows(case, "SELECT * FROM core.address_locations") == before
        assert self.rows(case, "SELECT 1 FROM intake.source_snapshots WHERE source_id=%s", (case.source_id,)) == []

    @EXACT_SERVICE
    @pytest.mark.parametrize(("failure", "retried"), [
        (("NATIVE_GCS_HTTP_REJECTED", 503), True), (("NATIVE_GCS_HTTP_REJECTED", 403), False)])
    def test_upload_failure_with_proven_absent_object_keeps_its_own_retry_class(self, geo_case, failure, retried):
        from shared.jobs.queue import JobStatus

        case = geo_case
        case.io.upload_failure = failure
        physical = self.run_default_worker(case)
        job = self.job(case)
        # The reconciliation 404 never masks the original upload failure.
        assert case.io.calls == ["gcs_geocode_bucket", "geocode_post", "gcs_geocode_upload", "gcs_geocode_head"]
        assert physical.status == (JobStatus.QUEUED if retried else JobStatus.FAILED), job["last_error"]
        assert job["status"] == ("RETRYING" if retried else "FAILED")
        _assert_no_geo_evidence(case)
        if retried:
            case.io.upload_failure, case.io.calls = None, []
            with case.database.connect() as connection:
                connection.execute("UPDATE workflow.jobs SET next_attempt_at=clock_timestamp() WHERE job_id=%s", (case.io.job_id,))
                connection.execute("UPDATE odp_runtime.durable_jobs SET available_at=clock_timestamp() WHERE job_id=%s",
                                   (case.io.job_id,))
            from apps.worker.oday_worker.main import ODayWorker

            worker = ODayWorker(persistence=case.runtime, heartbeat_interval_seconds=0.01)
            for _ in range(60):
                if case.graph.queue.get(case.io.job_id).status != JobStatus.QUEUED:
                    break
                assert worker.run_once() is True
            assert _assert_committed_geo_evidence(case)["attempt"] == 2

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
    def test_fresh_create_and_revise_ready_commit_native_geo_by_default_worker(self, matching_case, case_api, monkeypatch):
        import tests.integration.test_native_matching_case_command_postgresql as matching
        from modules.listing.application.native_intake_retrieval_policy import GEO_JOB_TYPE
        case, parsed, repo, _, _, _, mode = matching_case
        ids, database = case[1], case[2]
        _prepare_geo_database(database, ids["tenant"])
        source_id = _enable_native_geo(monkeypatch, database, ids["tenant"])
        from tests.integration import test_canonical_identity_admission_postgresql as admission

        committed, claim = {}, admission._claim

        def execute_then_geo(canonical, target):
            delivery, registry, bundle = claim(canonical, target)

            class Executor:
                def handle(self, job, persistence):
                    registry.handle(job, persistence)
                    # Immediately after READY the default worker finds the job.
                    committed.update(self.geo(persistence))

                def geo(self, runtime):
                    with database.connect() as connection:
                        jobs = connection.execute("SELECT job_id,status,checkpoint,payload FROM workflow.jobs "
                            "WHERE job_type=%s AND aggregate_id=%s", (GEO_JOB_TYPE, parsed["intake_id"])).fetchall()
                    assert len(jobs) == 1, jobs
                    job_id, status, checkpoint, payload = jobs[0]
                    assert (status, checkpoint) == ("QUEUED", "GEOCODING")
                    geo = _geo_worker_case(monkeypatch, repo.graph, ids, database, runtime, payload["origin_id"],
                                           parsed, source_id)
                    assert geo.io.job_id == str(job_id)
                    return {"job": _assert_committed_geo_evidence(geo), "case": geo}

            return delivery, Executor(), bundle

        monkeypatch.setattr(admission, "_claim", execute_then_geo)
        candidates = "SELECT count(*) FROM expansion.candidate_sites"
        with database.connect() as connection:
            candidates_before = connection.execute(candidates).fetchone()
        # The unchanged original HTTP confirmation + registered executor flow.
        matching.test_service_match_case_human_confirmation_and_registered_executor_share_real_native_flow(
            matching_case, case_api, monkeypatch, None)
        payload, result = committed["job"]["payload"], committed["job"]["result"]
        with database.connect() as connection:
            intake = connection.execute("SELECT processing_state,resolved_listing_id,version FROM intake.intakes "
                                        "WHERE intake_id=%s", (parsed["intake_id"],)).fetchone()
            listing = connection.execute("SELECT version,current_revision_id,property_id FROM expansion.listings "
                                         "WHERE listing_id=%s", (str(intake[1]),)).fetchone()
            revision = connection.execute("SELECT revision_kind,source_snapshot_id FROM expansion.listing_revisions "
                                          "WHERE listing_revision_id=%s", (str(listing[1]),)).fetchone()
            transition = connection.execute("SELECT to_state,job_id FROM intake.intake_stage_transitions "
                                            "WHERE transition_id=%s", (payload["ready_transition_id"],)).fetchone()
            # Geo evidence has no candidate, promotion, report or score side effect.
            assert connection.execute(candidates).fetchone() == candidates_before
        assert intake[0] == "READY" and payload["origin_kind"] == result["origin_kind"] == "IDENTITY_DECISION"
        assert committed["job"]["job_id"] == uuid5(UUID(payload["origin_id"]), GEO_JOB_TYPE)
        assert payload["listing_id"] == result["listing_id"] == str(intake[1]) and payload["intake_version"] == intake[2]
        assert payload["listing_version"] == listing[0] and result["listing_revision_id"] == str(listing[1])
        assert payload["property_id"] == str(listing[2])
        assert revision[0] == ("CREATED" if mode == "NEW" else "CONTENT_CHANGED")
        assert payload["source_snapshot_id"] == str(revision[1])
        assert transition[0] == "READY" and str(transition[1]) == payload["identity_job_id"]
        lineage = payload["input_lineage"]
        assert lineage["revision"]["parser_run_id"] == payload["parser_run_id"]
        assert lineage["corrections"] and {row["status"] for row in lineage["corrections"]} == {"APPLIED"}
