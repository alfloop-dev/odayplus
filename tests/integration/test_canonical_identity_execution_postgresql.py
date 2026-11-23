"""Synthetic approved decisions; real native acceptance/rollback/failure rows."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

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
            "kill_switch,production_enabled,version) VALUES (%s,'Synthetic Geo provider','geo-v1','APPROVED_RETRIEVAL',"
            "'synthetic-geo-legal','synthetic-geo-license',%s,now()+interval '1 day',false,true,3)",
            (source_id, str(uuid4())))
        connection.commit()
    monkeypatch.setenv(GEO_POLICY_ENV, json.dumps({"policy_id": "synthetic-native-geo", "version": 1,
        "tenant_ids": [tenant], "max_attempts": 2, "deadline_seconds": 60, "backoff_seconds": [5]}))
    monkeypatch.setenv(GEO_BINDING_ENV, json.dumps({"version": 1, "global_max_concurrency": 2,
        "source": {"source_id": source_id, "source_policy_version": source_version,
                   "provider_id": "geocode.primary_api", "source_contract_id": "geocode_result_snapshot"},
        "storage_bindings": [{"tenant_id": tenant, "bucket": "synthetic-native-geocode",
            "kms_key_name": "projects/test/locations/asia-east1/keyRings/native/cryptoKeys/geocode",
            "residency_mode": "TW_ONLY", "retention_class": "STANDARD"}]}))
    monkeypatch.setenv(GEO_SIGNING_KEY_ENV, "Explicit synthetic native Geo dispatch signing material only")
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
    @pytest.mark.parametrize("geo", ["disabled", "stale_source_version", "enabled", "admitted_audit_failure"])
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
        if geo in {"disabled", "stale_source_version"}:
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
        assert payload["listing_id"] == ids["listing"] and payload["decision_id"] == receipt["decision_id"]
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
