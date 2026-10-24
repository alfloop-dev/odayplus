"""Actual SQL/default-worker identity phases, with synthetic source approvals."""

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from types import MethodType
from uuid import NAMESPACE_DNS, uuid4, uuid5

import pytest

from apps.worker.oday_worker.main import ODayWorker
from modules.listing.application.native_intake_identity import (
    NATIVE_INTAKE_IDENTITY_JOB_TYPE,
    POLICY_ENV,
    SIGNING_KEY_ENV,
    NativeIntakeIdentityConflict,
    NativeIntakeIdentityOwner,
    NativeIntakeIdentityUnavailable,
    run_native_intake_identity_delivery,
)
from modules.listing.application.native_intake_url_identity import (
    NATIVE_URL_RULE_VERSION,
    normalize_native_intake_url,
)
from shared.audit.errors import CanonicalAuditUnavailable
from shared.domain.events import validate_event
from shared.infrastructure.persistence.factory import PersistenceBundle
from shared.jobs.queue import JobFenceRejectedError, JobStatus
from tests.integration.test_assisted_listing_identity import independent_canonical_graph
from tests.integration.test_canonical_intakes_postgresql import command, snapshot
from tests.integration.test_native_intake_start_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_start_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_start_postgresql import (
    native_row,
    submitted,
)
from tests.integration.test_native_intake_start_postgresql import (
    start_case as start_case,
)
from tests.security.test_native_intake_start import policy

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def identity_case(start_case, monkeypatch):
    graph, ids, _, _ = start_case[0]
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(ids["tenant"]), "policy_id": "synthetic-native-identity"}))
    monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-native-identity-separate-key-" * 2)
    bundle = start_case[2]
    bundle.canonical_intake_identity = MethodType(PersistenceBundle.canonical_intake_identity, bundle)
    errors = []
    original_execute = NativeIntakeIdentityOwner.execute

    def observed_execute(self, delivery):
        try:
            return original_execute(self, delivery)
        except Exception as error:
            errors.append({"error_class": type(error).__name__, "error": str(error)})
            raise

    monkeypatch.setattr(NativeIntakeIdentityOwner, "execute", observed_execute)
    owner = NativeIntakeIdentityOwner(graph)
    owner.validation_errors = errors
    return start_case, owner


def defer_audit(graph):
    graph.engine.execute("UPDATE durable_jobs SET available_at=clock_timestamp()+interval '1 day' "
                         "WHERE job_type='canonical-audit-delivery'")


def prepare(case, outcome="exact", method="URL", *, scope=None, admit=True, url=None):
    start, owner = case
    graph, ids, _, db = start[0]
    if scope is None and url is None:
        submission = submitted(start, method)
    else:
        response = start[1][0].post("/api/v1/canonical-intakes",
            json=command(intake_method=method, scope=scope or {},
                         original_url=url or "https://synthetic.example/Original?private=synthetic-only"),
            headers={"Idempotency-Key": "synthetic-native-identity-" + uuid4().hex})
        assert response.status_code == 201, response.text
        submission = response.json()
    reservation = start[3].admit_event(ids["tenant"], submission["outbox_event_id"])
    defer_audit(graph)
    assert ODayWorker(persistence=start[2], heartbeat_interval_seconds=0.01).run_once() is True
    assert graph.queue.get(reservation["job_id"]).status == JobStatus.SUCCEEDED
    with graph.transaction(ids["tenant"]):
        start_result = graph.engine.query_one("SELECT result FROM workflow.jobs WHERE job_id=?", (reservation["job_id"],))["result"]
    start_result = json.loads(start_result) if isinstance(start_result, str) else start_result
    row = native_row(start, submission["intake_id"])
    identity = normalize_native_intake_url(row["original_url"]) if method == "URL" else None
    with db.connect() as conn:
        conn.execute("UPDATE intake.source_registry SET allowed_hosts=ARRAY['synthetic.example'],"
            "canonicalization_rule_version=%s,retrieval_mode='APPROVED_RETRIEVAL',legal_approval_ref='synthetic-legal',"
            "license_approval_ref='synthetic-license',review_expires_at=clock_timestamp()+interval '1 hour',"
            "kill_switch=false,production_enabled=true,version=2 WHERE source_id='synthetic-feed'", (NATIVE_URL_RULE_VERSION,))
        conn.execute("UPDATE expansion.listings SET canonical_url_sha256=%s WHERE listing_id=%s",
            (identity.canonical_url_sha256 if identity and outcome != "absent" else "a" * 64, ids["listing"]))
        patches = {
            "blocked": "retrieval_mode='SOURCE_BLOCKED'", "expired": "review_expires_at=clock_timestamp()-interval '1 hour'",
            "null_legal": "legal_approval_ref=NULL", "null_license": "license_approval_ref=NULL",
            "blank_legal": "legal_approval_ref=' '", "null_expiry": "review_expires_at=NULL",
            "kill": "kill_switch=true", "disabled": "production_enabled=false",
            "sentinel": "policy_owner_subject_id='" + str(uuid5(NAMESPACE_DNS, "system")) + "'",
            "assisted_only": "retrieval_mode='ASSISTED_ENTRY_ONLY'",
        }
        if outcome in patches:
            conn.execute("UPDATE intake.source_registry SET " + patches[outcome] + " WHERE source_id='synthetic-feed'")
    admitted = owner.admit_event(ids["tenant"], start_result["outbox_event_id"]) if admit else None
    return submission, start_result, admitted


def graph_rows(case):
    graph, ids, _, _ = case[0][0]
    with graph.transaction(ids["tenant"]):
        tables = ("identity.properties", "identity.source_identity_edges", "identity.property_redirects",
                  "expansion.listings", "identity.match_cases", "identity.match_decisions")
        return {table: graph.engine.query("SELECT * FROM " + table + " WHERE tenant_id=?", (ids["tenant"],)) for table in tables}


@pytest.mark.parametrize("outcome", ["exact", "absent", "blocked", "expired", "null_legal", "null_license",
                                    "blank_legal", "null_expiry", "kill", "disabled", "sentinel", "assisted_only"])
def test_real_http_start_and_default_identity_worker_preserve_graph_and_readback(identity_case, outcome):
    start, owner = identity_case
    graph, ids, _, db = start[0]
    submission, source_result, admission = prepare(identity_case, outcome)
    before = graph_rows(identity_case)
    before_intake = native_row(start, submission["intake_id"])
    assert owner.admit_event(ids["tenant"], source_result["outbox_event_id"]) == admission
    defer_audit(graph)
    assert ODayWorker(persistence=start[2], heartbeat_interval_seconds=0.01).run_once() is True
    delivery = graph.queue.get(admission["job_id"])
    assert delivery.status == JobStatus.SUCCEEDED, owner.validation_errors
    result = owner.execute(delivery)
    assert owner.execute(delivery) == result
    expected = "READY" if outcome == "exact" else "CHECKING_SOURCE_POLICY"
    assert result["processing_state"] == expected and result["version"] == 3
    after = native_row(start, submission["intake_id"])
    assert after["processing_state"] == expected and after["version"] == 3
    assert after["original_url"] == before_intake["original_url"]
    assert after["source_id"] == "synthetic-feed" and after["source_policy_state"] is None
    assert after["resolved_listing_id"] == (ids["listing"] if outcome == "exact" else None)
    assert graph_rows(identity_case) == before
    assert list(owner.admit_partition(ids["tenant"], limit=5)) == []
    with db.connect() as conn:
        transition = conn.execute("SELECT permission,service_principal,actor_subject_id,source_policy_version,"
            "expected_version,resulting_version FROM intake.intake_stage_transitions WHERE transition_id=%s",
            (result["transition_id"],)).fetchone()
        assert transition == ("identity.resolve" if outcome == "exact" else "source_policy.evaluate",
                              NATIVE_INTAKE_IDENTITY_JOB_TYPE, None, 2, 2, 3)
        audit = conn.execute("SELECT action,service_principal,actor_subject_id FROM audit.audit_events "
            "WHERE audit_event_id=%s", (result["audit_event_id"],)).fetchone()
        assert audit == ("intake.resolved.v1" if outcome == "exact" else "intake.source_policy_started.v1",
                         NATIVE_INTAKE_IDENTITY_JOB_TYPE, None)
        event = conn.execute("SELECT payload FROM workflow.outbox_events WHERE event_id=%s", (result["outbox_event_id"],)).fetchone()[0]
        assert validate_event(event) == []
        assert set(event["payload"]) == {"intake_id", "from_state", "to_state", "transition_id", "version", "occurred_at"}
        assert event["payload"]["to_state"] == expected and event["causation_id"] == source_result["outbox_event_id"]
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok


def claim_identity(case, admission):
    graph = case[0][0][0]
    defer_audit(graph)
    delivery = graph.queue.claim_next(worker_id="synthetic-native-identity-worker")
    assert delivery.job_id == admission["job_id"] and delivery.job_type == NATIVE_INTAKE_IDENTITY_JOB_TYPE
    return delivery


def phase_snapshot(case):
    return {"intake_and_delivery": snapshot(case[0][0]), "graph": graph_rows(case)}


@pytest.mark.parametrize("change", ["worker", "fence", "payload", "policy", "url", "scope", "version",
                                   "legacy_rule", "ambiguous_source", "inactive_listing", "contradictory_edge",
                                   "inactive_property", "premature_resolution", "cancel", "expired_lease", "foreign_root", "rule_code_drift"])
def test_current_authority_and_native_input_changes_cannot_advance(identity_case, monkeypatch, change):
    start, owner = identity_case
    graph, ids, _, _ = start[0]
    submission, _, admission = prepare(identity_case)
    delivery = claim_identity(identity_case, admission)
    if change == "worker":
        delivery = replace(delivery, locked_by="another-worker")
    elif change == "fence":
        delivery = replace(delivery, fence_token=delivery.fence_token + 1)
    elif change == "payload":
        delivery = replace(delivery, payload={**delivery.payload, "actor_role": "SVC_INTAKE"})
    elif change == "policy":
        monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(ids["tenant"]), "version": 2}))
    elif change == "rule_code_drift":
        from modules.listing.application import native_intake_identity as module

        monkeypatch.setattr(module, "NATIVE_URL_RULE_SOURCE_SHA256", "0" * 64)
    else:
        with graph.transaction(ids["tenant"]):
            changes = {
                "url": ("UPDATE intake.intakes SET original_url='https://synthetic.example/Changed' WHERE intake_id=?", (submission["intake_id"],)),
                "scope": ("UPDATE intake.intakes SET region_id=? WHERE intake_id=?", (str(uuid4()), submission["intake_id"])),
                "version": ("UPDATE intake.intakes SET version=version+1 WHERE intake_id=?", (submission["intake_id"],)),
                "legacy_rule": ("UPDATE intake.source_registry SET canonicalization_rule_version='v1.0' WHERE source_id='synthetic-feed'", ()),
                "inactive_listing": ("UPDATE expansion.listings SET lifecycle_state='EXPIRED' WHERE listing_id=?", (ids["listing"],)),
                "contradictory_edge": ("UPDATE identity.source_identity_edges SET property_id=? WHERE edge_id=?", (ids["target"], ids["edge"])),
                "inactive_property": ("UPDATE identity.properties SET status='QUARANTINED' WHERE property_id=?", (ids["source"],)),
                "premature_resolution": ("UPDATE intake.intakes SET source_id='synthetic-feed' WHERE intake_id=?", (submission["intake_id"],)),
                "cancel": ("UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp() WHERE job_id=?", (delivery.job_id,)),
                "expired_lease": ("UPDATE durable_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=?", (delivery.job_id,)),
            }
            if change in changes:
                graph.engine.execute(*changes[change])
            elif change == "ambiguous_source":
                graph.engine.execute("INSERT INTO intake.source_registry (source_id,display_name,allowed_hosts,"
                    "canonicalization_rule_version,retrieval_mode,policy_owner_subject_id) "
                    "VALUES ('synthetic-other','Synthetic other',ARRAY['synthetic.example'],'v1.0','POLICY_UNKNOWN',?)",
                    (ids["actor"],))
    before = phase_snapshot(identity_case)
    if change == "foreign_root":
        with graph.transaction(str(uuid4())):
            with pytest.raises(PermissionError):
                owner.execute(delivery)
    else:
        with pytest.raises((NativeIntakeIdentityConflict, JobFenceRejectedError)):
            owner.execute(delivery)
    assert phase_snapshot(identity_case) == before
    assert graph.queue.get(delivery.job_id).status == JobStatus.RUNNING


@pytest.mark.parametrize("outcome", ["exact", "absent"])
@pytest.mark.parametrize("failure", ["audit", "outbox", "result", "transport"])
def test_every_phase_metadata_failure_rolls_back_all_native_and_transport_writes(identity_case, monkeypatch, outcome, failure):
    start, owner = identity_case
    graph = start[0][0]
    _, _, admission = prepare(identity_case, outcome)
    delivery = claim_identity(identity_case, admission)
    before = phase_snapshot(identity_case)

    def fail(*_args, **_kwargs):
        raise CanonicalAuditUnavailable("Synthetic identity metadata outage")

    if failure == "audit":
        original = graph.audit_sink.persist

        def audit(record, scope):
            if record["action"] in {"intake.resolved.v1", "intake.source_policy_started.v1"}:
                return fail()
            return original(record, scope)

        monkeypatch.setattr(graph.audit_sink, "persist", audit)
    elif failure == "outbox":
        monkeypatch.setattr(graph, "_outbox", fail)
    elif failure == "transport":
        monkeypatch.setattr(graph.queue, "update_status", fail)
    else:
        original = graph.engine.execute

        def execute(sql, params=()):
            if "UPDATE workflow.jobs SET status='SUCCEEDED'" in sql:
                return fail()
            return original(sql, params)

        monkeypatch.setattr(graph.engine, "execute", execute)
    with pytest.raises(CanonicalAuditUnavailable):
        owner.execute(delivery)
    assert phase_snapshot(identity_case) == before


@pytest.mark.parametrize("outcome", ["exact", "absent"])
def test_actual_current_catalog_allows_two_same_url_intakes(identity_case, outcome):
    start, owner = identity_case
    graph, ids, _, db = start[0]
    receipts = []
    for _ in range(2):
        submission, _, admission = prepare(identity_case, outcome)
        receipts.append((submission, owner.execute(claim_identity(identity_case, admission))))
    assert receipts[0][0]["intake_id"] != receipts[1][0]["intake_id"]
    assert [result["processing_state"] for _, result in receipts] == ["READY" if outcome == "exact" else "CHECKING_SOURCE_POLICY"] * 2
    with db.connect() as conn:
        assert conn.execute("SELECT to_regclass('intake.ux_intakes_exact_url_active')").fetchone()[0] is None
        assert conn.execute("SELECT indisunique FROM pg_index WHERE indexrelid='intake.ix_intakes_canonical_url_history'::regclass").fetchone() == (False,)
        assert conn.execute("SELECT count(*) FROM intake.intakes WHERE tenant_id=%s AND canonical_url_sha256=%s",
            (ids["tenant"], receipts[0][1]["identity_evidence"]["canonical_url_sha256"])).fetchone()[0] == 2
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok


@pytest.mark.parametrize("schema", ["old_unique", "missing_history"])
@pytest.mark.parametrize("operation", ["admit", "execute"])
def test_unsupported_schema_cannot_admit_or_mutate_a_phase(identity_case, schema, operation):
    start, owner = identity_case
    graph, ids, _, db = start[0]
    _, source, admission = prepare(identity_case, admit=operation == "execute")
    delivery = claim_identity(identity_case, admission) if operation == "execute" else None
    with db.connect() as conn:
        if schema == "old_unique":
            conn.execute("CREATE UNIQUE INDEX ux_intakes_exact_url_active ON intake.intakes "
                         "(tenant_id,source_id,canonical_url_sha256) WHERE canonical_url_sha256 IS NOT NULL")
        else:
            conn.execute("DROP INDEX intake.ix_intakes_canonical_url_history")
    before = phase_snapshot(identity_case)
    with pytest.raises(NativeIntakeIdentityUnavailable, match="CURRENT_SCHEMA_REQUIRED"):
        if operation == "admit":
            owner.admit_event(ids["tenant"], source["outbox_event_id"])
        else:
            owner.execute(delivery)
    assert phase_snapshot(identity_case) == before


def test_unknown_commit_reply_reads_the_actual_committed_receipt(identity_case, monkeypatch):
    start, owner = identity_case
    graph = start[0][0]
    _, _, admission = prepare(identity_case)
    delivery = claim_identity(identity_case, admission)
    original, lose_once = graph.transaction, [True]

    @contextmanager
    def lost_reply(tenant):
        with original(tenant):
            yield
        if lose_once[0]:
            lose_once[0] = False
            raise OSError("Synthetic lost commit reply after actual commit")

    monkeypatch.setattr(graph, "transaction", lost_reply)
    result = run_native_intake_identity_delivery(delivery, owner)
    assert result["processing_state"] == "READY"
    assert graph.queue.get(delivery.job_id).status == JobStatus.SUCCEEDED
    before = phase_snapshot(identity_case)
    assert owner.execute(delivery) == result
    assert phase_snapshot(identity_case) == before


def test_outer_serializable_rollback_retains_ownership_of_all_phase_writes(identity_case):
    start, owner = identity_case
    graph, ids, _, _ = start[0]
    _, _, admission = prepare(identity_case)
    delivery = claim_identity(identity_case, admission)
    before = phase_snapshot(identity_case)
    with pytest.raises(RuntimeError, match="Synthetic outer rollback"):
        with graph.transaction(ids["tenant"]):
            assert owner.execute(delivery)["processing_state"] == "READY"
            raise RuntimeError("Synthetic outer rollback")
    assert phase_snapshot(identity_case) == before


@pytest.mark.parametrize("field", ["accepted_worker", "accepted_delivery_fence", "fence_token"])
def test_terminal_receipt_cannot_adopt_changed_worker_or_fences(identity_case, field):
    start, owner = identity_case
    graph, ids, _, _ = start[0]
    _, _, admission = prepare(identity_case)
    owner.execute(claim_identity(identity_case, admission))
    with graph.transaction(ids["tenant"]):
        if field == "fence_token":
            graph.engine.execute("UPDATE workflow.jobs SET fence_token=fence_token+1 WHERE job_id=?", (admission["job_id"],))
        else:
            row = graph.engine.query_one("SELECT payload FROM workflow.jobs WHERE job_id=?", (admission["job_id"],))
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            payload[field] = "tampered-worker" if field == "accepted_worker" else payload[field] + 1
            graph.engine.execute("UPDATE workflow.jobs SET payload=?::jsonb WHERE job_id=?", (json.dumps(payload), admission["job_id"]))
    before = phase_snapshot(identity_case)
    with pytest.raises(JobFenceRejectedError):
        owner.execute(graph.queue.get(admission["job_id"]))
    assert phase_snapshot(identity_case) == before


def test_native_worker_renews_before_SQL_without_racing_native_audit_enqueue(identity_case, monkeypatch):
    start, owner = identity_case
    graph = start[0][0]
    _, _, admission = prepare(identity_case)
    renewals = []
    original_heartbeat, original_audit = graph.queue.heartbeat, graph.audit_sink.persist

    def heartbeat(*args, **kwargs):
        version = original_heartbeat(*args, **kwargs)
        renewals.append((threading.get_ident(), graph.queue.get(admission["job_id"])))
        return version

    def audit(record, scope):
        if record["action"] == "intake.resolved.v1":
            assert len(renewals) == 1 and renewals[0][0] == threading.get_ident()
            physical = graph.queue.get(admission["job_id"])
            assert physical.lease_expires_at == renewals[0][1].lease_expires_at
            assert physical.version == renewals[0][1].version
            time.sleep(0.15)  # Fifteen configured heartbeat intervals inside the real SQL root.
            assert len(renewals) == 1
        return original_audit(record, scope)

    monkeypatch.setattr(graph.queue, "heartbeat", heartbeat)
    monkeypatch.setattr(graph.audit_sink, "persist", audit)
    defer_audit(graph)
    assert ODayWorker(persistence=start[2], heartbeat_interval_seconds=0.01).run_once() is True
    assert len(renewals) == 1
    assert graph.queue.get(admission["job_id"]).status == JobStatus.SUCCEEDED
    assert owner.execute(graph.queue.get(admission["job_id"]))["processing_state"] == "READY"


@pytest.mark.parametrize("axis", ["brand_id", "assigned_area_id", "heat_zone_id"])
def test_finer_scope_is_not_inferred_from_a_tenant_or_region(identity_case, axis):
    start, owner = identity_case
    submission, _, admission = prepare(identity_case, scope={axis: str(uuid4())})
    before = native_row(start, submission["intake_id"])
    before_graph = graph_rows(identity_case)
    assert run_native_intake_identity_delivery(claim_identity(identity_case, admission), owner) is None
    assert native_row(start, submission["intake_id"]) == before
    assert graph_rows(identity_case) == before_graph
    graph = start[0][0]
    with graph.transaction(start[0][1]["tenant"]):
        row = graph.engine.query_one("SELECT status,last_error FROM workflow.jobs WHERE job_id=?", (admission["job_id"],))
    assert row["status"] == "FAILED"
    assert json.loads(row["last_error"])["code"] == "NATIVE_IDENTITY_SCOPE_UNREPRESENTABLE"
    assert graph.queue.get(admission["job_id"]).status == JobStatus.FAILED


@pytest.mark.parametrize("matches", [False, True])
def test_ready_requires_actual_native_property_region_scope(identity_case, matches):
    start, owner = identity_case
    graph, ids, _, db = start[0]
    region = str(uuid4())
    submission, _, admission = prepare(identity_case, scope={"region_id": region})
    if matches:
        with db.connect() as conn:
            conn.execute("UPDATE identity.properties SET region_id=%s,version=version+1 WHERE property_id=%s",
                         (region, ids["source"]))
    before = native_row(start, submission["intake_id"])
    result = run_native_intake_identity_delivery(claim_identity(identity_case, admission), owner)
    if matches:
        assert result["processing_state"] == "READY"
        assert result["identity_evidence"]["property_versions"] == {ids["source"]: 8}
    else:
        assert result is None and native_row(start, submission["intake_id"]) == before
        with graph.transaction(ids["tenant"]):
            error = graph.engine.query_one("SELECT last_error FROM workflow.jobs WHERE job_id=?", (admission["job_id"],))["last_error"]
        assert json.loads(error)["code"] == "NATIVE_IDENTITY_REGION_DENIED"
        assert graph.queue.get(admission["job_id"]).status == JobStatus.FAILED


def test_blocked_source_with_finer_intake_scope_reaches_policy_without_graph_reads(identity_case, monkeypatch):
    start, owner = identity_case
    graph = start[0][0]
    submission, _, admission = prepare(identity_case, "blocked", scope={"brand_id": str(uuid4())})
    queries, original = [], graph.engine.query

    def query(sql, params=()):
        queries.append(sql)
        return original(sql, params)

    monkeypatch.setattr(graph.engine, "query", query)
    result = owner.execute(claim_identity(identity_case, admission))
    assert result["processing_state"] == "CHECKING_SOURCE_POLICY"
    assert not any("FROM identity.properties" in sql or "FROM expansion.listings" in sql for sql in queries)
    assert native_row(start, submission["intake_id"])["source_policy_state"] is None


@pytest.mark.parametrize("kind", ["manual", "unknown_host", "contradictory_identity"])
def test_unsupported_and_contradictory_commands_have_visible_safe_failures(identity_case, kind):
    start, owner = identity_case
    graph, ids, _, db = start[0]
    submission, _, admission = prepare(identity_case, method="MANUAL" if kind == "manual" else "URL",
        url="https://unknown.synthetic.example/item" if kind == "unknown_host" else None)
    if kind == "contradictory_identity":
        with db.connect() as conn:
            conn.execute("UPDATE identity.source_identity_edges SET property_id=%s WHERE edge_id=%s",
                         (ids["target"], ids["edge"]))
    before = native_row(start, submission["intake_id"])
    before_graph = graph_rows(identity_case)
    assert run_native_intake_identity_delivery(claim_identity(identity_case, admission), owner) is None
    assert native_row(start, submission["intake_id"]) == before
    assert graph_rows(identity_case) == before_graph
    with graph.transaction(ids["tenant"]):
        row = graph.engine.query_one("SELECT status,last_error FROM workflow.jobs WHERE job_id=?", (admission["job_id"],))
    assert row["status"] == "FAILED"
    expected = {"manual": "METHOD_UNSUPPORTED", "unknown_host": "SOURCE_UNSUPPORTED_OR_AMBIGUOUS",
                "contradictory_identity": "CONTRADICTORY_OPEN_CONTRACT_GAP"}[kind]
    assert json.loads(row["last_error"])["code"] == "NATIVE_IDENTITY_" + expected
    assert graph.queue.get(admission["job_id"]).status == JobStatus.FAILED


def test_same_normalized_host_drives_actual_registry_match_and_canonical_hash(identity_case):
    start, owner = identity_case
    _, ids, _, db = start[0]
    original = "HTTPS://SYNTHETIC.EXAMPLE:443/Original?private=synthetic-only"
    submission, _, admission = prepare(identity_case, url=original)
    with db.connect() as conn:
        conn.execute("UPDATE intake.source_registry SET allowed_hosts=ARRAY['SYNTHETIC.EXAMPLE'] WHERE source_id='synthetic-feed'")
    result = owner.execute(claim_identity(identity_case, admission))
    assert result["processing_state"] == "READY" and result["identity_evidence"]["source_host"] == "synthetic.example"
    after = native_row(start, submission["intake_id"])
    assert after["original_url"] == original
    assert after["canonical_url"] == "https://synthetic.example/Original?private=synthetic-only"
    assert after["resolved_listing_id"] == ids["listing"]


@pytest.mark.parametrize("graph_kind", ["valid_redirect", "cycle", "over_depth"])
def test_redirect_proof_is_bounded_current_and_consistent(identity_case, graph_kind):
    start, owner = identity_case
    graph, ids, _, db = start[0]
    submission, _, admission = prepare(identity_case)
    with db.connect() as conn:
        path = [ids["source"], ids["target"]]
        if graph_kind == "over_depth":
            path += [str(uuid4()) for _ in range(8)]
            for property_id in path[2:]:
                conn.execute("INSERT INTO identity.properties (property_id,tenant_id,normalized_address,address_fingerprint) "
                    "VALUES (%s,%s,'Synthetic depth property',repeat('a',64))", (property_id, ids["tenant"]))
        if graph_kind == "cycle":
            path.append(ids["source"])
        for source_id, target_id in zip(path[:-1], path[1:], strict=True):
            conn.execute("UPDATE identity.properties SET status='MERGED',current_redirect_property_id=%s,version=version+1 "
                         "WHERE property_id=%s", (target_id, source_id))
            conn.execute("INSERT INTO identity.property_redirects (tenant_id,from_property_id,to_property_id,decision_id) "
                         "VALUES (%s,%s,%s,%s)", (ids["tenant"], source_id, target_id, ids["decision"]))
    before, before_graph = native_row(start, submission["intake_id"]), graph_rows(identity_case)
    delivery = claim_identity(identity_case, admission)
    if graph_kind == "valid_redirect":
        result = owner.execute(delivery)
        assert result["processing_state"] == "READY"
        assert result["identity_evidence"]["effective_property_id"] == ids["target"]
        assert len(result["identity_evidence"]["redirect_versions"]) == 1
    else:
        with pytest.raises(NativeIntakeIdentityConflict, match="REDIRECT"):
            owner.execute(delivery)
        assert native_row(start, submission["intake_id"]) == before
    assert graph_rows(identity_case) == before_graph


def test_two_actual_connections_reserve_one_identity_phase(identity_case, record_property):
    start, owner = identity_case
    graph, ids, _, db = start[0]
    _, source, _ = prepare(identity_case, admit=False)
    barrier = threading.Barrier(2)

    def attempt(active_graph, active_owner):
        pid = None
        try:
            with active_graph.transaction(ids["tenant"]):
                active_graph.engine.query_one("SELECT set_config('statement_timeout','5000ms',true)")
                pid = active_graph.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"]
                barrier.wait(timeout=5)
                result = active_owner.admit_event(ids["tenant"], source["outbox_event_id"])
            return pid, result, None, None
        except Exception as error:
            return pid, None, getattr(error, "sqlstate", type(error).__name__), getattr(getattr(error, "diag", None), "constraint_name", None)

    # An engine holds its root lock through commit. A second engine lets
    # both transactions reach the existing SQL barrier.
    with independent_canonical_graph(graph, db) as fresh_graph:
        fresh_owner = NativeIntakeIdentityOwner(fresh_graph)
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = [future.result(timeout=12) for future in [
                executor.submit(attempt, graph, owner), executor.submit(attempt, fresh_graph, fresh_owner)]]
    assert len({row[0] for row in outcomes}) == 2 and all(row[0] for row in outcomes)
    record_property("actual_postgresql_backend_pids", ",".join(str(row[0]) for row in outcomes))
    accepted = [row[1] for row in outcomes if row[1] is not None]
    assert accepted and all(row == accepted[0] for row in accepted), outcomes
    assert all(row[2] in {None, "40001", "40P01", "23505"} for row in outcomes), outcomes
    for row in outcomes:
        if row[2] == "23505":
            assert row[3] in {"jobs_pkey", "jobs_tenant_id_job_type_idempotency_key_key"}
    record_property("reservation_conflicts", json.dumps([{"sqlstate": row[2], "constraint": row[3]} for row in outcomes]))
    assert owner.admit_event(ids["tenant"], source["outbox_event_id"]) == accepted[0]
    with graph.transaction(ids["tenant"]):
        assert len(graph.engine.query("SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND job_type=?",
                                      (ids["tenant"], NATIVE_INTAKE_IDENTITY_JOB_TYPE))) == 1


def test_actual_source_row_lock_wait_uses_remaining_db_clock_budget(identity_case, monkeypatch, record_property):
    start, owner = identity_case
    graph, ids, _, db = start[0]
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(ids["tenant"]), "deadline_seconds": 2}))
    submission, _, admission = prepare(identity_case)
    delivery = claim_identity(identity_case, admission)

    def execute():
        began = time.monotonic()
        pid = None
        try:
            with graph.transaction(ids["tenant"]):
                pid = graph.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"]
                owner.execute(delivery)
            return None, time.monotonic() - began, pid
        except Exception as error:
            return error, time.monotonic() - began, pid

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with db.connect() as blocker, blocker.transaction():
            blocking_pid = blocker.execute("SELECT pg_backend_pid()").fetchone()[0]
            blocker.execute("SELECT source_id FROM intake.source_registry WHERE source_id='synthetic-feed' FOR UPDATE")
            error, elapsed, worker_pid = executor.submit(execute).result(timeout=5)
    finally:
        executor.shutdown(wait=True)
    assert worker_pid and worker_pid != blocking_pid
    assert getattr(error, "sqlstate", None) in {"55P03", "57014"}, error
    assert elapsed < 5
    record_property("actual_postgresql_backend_pids", f"{blocking_pid},{worker_pid}")
    record_property("blocked_statement_elapsed_seconds", elapsed)
    assert owner.settle_failure(delivery, error) is None
    assert native_row(start, submission["intake_id"])["processing_state"] == "CHECKING_IDENTITY"
    assert graph.queue.get(delivery.job_id).status == JobStatus.FAILED


@pytest.mark.parametrize("mode", ["retry", "exhausted", "revoked", "third_attempt", "three_exhausted"])
def test_actual_bounded_retry_and_revocation_settle_both_job_axes(identity_case, monkeypatch, mode):
    start, owner = identity_case
    graph, ids, _, _ = start[0]
    p = {**policy(ids["tenant"]), "backoff_seconds": [0.001]}
    if mode == "exhausted":
        p.update(max_attempts=1, backoff_seconds=[])
    elif mode in {"third_attempt", "three_exhausted"}:
        p.update(max_attempts=3, backoff_seconds=[0.001, 0.001])
    monkeypatch.setenv(POLICY_ENV, json.dumps(p))
    submission, _, admission = prepare(identity_case)
    delivery = claim_identity(identity_case, admission)
    original, remaining = graph.audit_sink.persist, [3 if mode == "three_exhausted" else 2 if mode == "third_attempt" else 1]

    def fail_audit(record, scope):
        if record["action"] == "intake.resolved.v1" and remaining[0]:
            remaining[0] -= 1
            raise CanonicalAuditUnavailable("Synthetic transient identity audit outage")
        return original(record, scope)

    monkeypatch.setattr(graph.audit_sink, "persist", fail_audit)
    if mode == "revoked":
        monkeypatch.setenv(POLICY_ENV, json.dumps({**p, "tenant_ids": [str(uuid4())]}))
    assert run_native_intake_identity_delivery(delivery, owner) is None
    assert native_row(start, submission["intake_id"])["processing_state"] == "CHECKING_IDENTITY"
    if mode in {"exhausted", "revoked"}:
        assert graph.queue.get(delivery.job_id).status == JobStatus.FAILED
        with graph.transaction(ids["tenant"]):
            row = graph.engine.query_one("SELECT status,attempt,timeout_at FROM workflow.jobs WHERE job_id=?", (delivery.job_id,))
        assert row == {"status": "FAILED", "attempt": 1, "timeout_at": admission["timeout_at"]}
        return
    original_fence = delivery.fence_token
    for attempt in range(2, p["max_attempts"] + 1):
        delivery = claim_identity(identity_case, admission)
        assert delivery.fence_token > original_fence
        original_fence = delivery.fence_token
        result = run_native_intake_identity_delivery(delivery, owner)
        if result is not None:
            assert result["processing_state"] == "READY" and result["identity_evidence"]["accepted_attempt"] == attempt
            assert owner.execute(delivery) == result
            break
    if mode == "three_exhausted":
        assert result is None and graph.queue.get(delivery.job_id).status == JobStatus.FAILED
        assert native_row(start, submission["intake_id"])["processing_state"] == "CHECKING_IDENTITY"
    else:
        assert graph.queue.get(delivery.job_id).status == JobStatus.SUCCEEDED
    with graph.transaction(ids["tenant"]):
        row = graph.engine.query_one("SELECT status,attempt,timeout_at FROM workflow.jobs WHERE job_id=?", (delivery.job_id,))
        assert row["status"] == ("FAILED" if mode == "three_exhausted" else "SUCCEEDED")
        assert row["attempt"] == p["max_attempts"]
        assert row["timeout_at"] == admission["timeout_at"]


@pytest.mark.parametrize("field", ["event_version", "aggregate_version", "payload_version", "actor_ref", "causation_id"])
def test_original_native_start_event_is_not_replaced_by_typed_or_actor_claims(identity_case, field):
    start, owner = identity_case
    graph, ids, _, _ = start[0]
    _, source, admission = prepare(identity_case)
    delivery = claim_identity(identity_case, admission)
    with graph.transaction(ids["tenant"]):
        row = graph.engine.query_one("SELECT payload FROM workflow.outbox_events WHERE event_id=?", (source["outbox_event_id"],))
        event = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        if field == "payload_version":
            event["payload"]["version"] = True
        else:
            event[field] = True if field in {"event_version", "aggregate_version"} else str(uuid4())
        graph.engine.execute("UPDATE workflow.outbox_events SET payload=?::jsonb WHERE event_id=?",
                             (json.dumps(event), source["outbox_event_id"]))
    before = phase_snapshot(identity_case)
    with pytest.raises((ValueError, JobFenceRejectedError)):
        owner.execute(delivery)
    assert phase_snapshot(identity_case) == before


@pytest.mark.parametrize("outcome", ["exact", "absent"])
def test_terminal_receipt_checks_the_branch_specific_permission(identity_case, outcome):
    start, owner = identity_case
    graph, ids, _, _ = start[0]
    _, _, admission = prepare(identity_case, outcome)
    result = owner.execute(claim_identity(identity_case, admission))
    with graph.transaction(ids["tenant"]):
        graph.engine.execute("UPDATE intake.intake_stage_transitions SET permission='intake.process' WHERE transition_id=?",
                             (result["transition_id"],))
    before = phase_snapshot(identity_case)
    with pytest.raises(JobFenceRejectedError, match="TERMINAL_TRANSITION_CONFLICT"):
        owner.execute(graph.queue.get(admission["job_id"]))
    assert phase_snapshot(identity_case) == before


def test_partition_cli_uses_injected_native_bootstrap_and_prints_only_durable_receipt(identity_case, monkeypatch, capsys):
    from apps.api import server
    from apps.worker.oday_worker import native_intake_identity as cli

    start, _ = identity_case
    _, ids, _, db = start[0]
    submission, _, _ = prepare(identity_case, admit=False)
    monkeypatch.setattr(server, "bootstrap_runtime", lambda: start[2])
    monkeypatch.setattr(sys, "argv", ["native_intake_identity", "--tenant-id", ids["tenant"], "--limit", "1"])
    capsys.readouterr()
    cli.main()
    output = capsys.readouterr().out
    assert "synthetic.example" not in output and "private" not in output and "attempt_token" not in output
    printed = json.loads(output)
    assert printed["phase"] == "native_url_identity_only"
    receipt = printed["admission_receipt"]
    assert receipt["intake_id"] == submission["intake_id"]
    with db.connect() as conn:
        assert conn.execute("SELECT status FROM workflow.jobs WHERE job_id=%s", (receipt["job_id"],)).fetchone() == ("QUEUED",)
        assert conn.execute("SELECT processing_state FROM intake.intakes WHERE intake_id=%s", (submission["intake_id"],)).fetchone() == ("CHECKING_IDENTITY",)


def test_fresh_engine_queue_and_owner_read_the_original_committed_receipt(identity_case):
    from modules.listing.application.intake_audit import CanonicalIntakeAuditSink
    from shared.audit.canonical_delivery import CanonicalAuditDelivery
    from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
    from shared.infrastructure.persistence.identity_graph_sql import (
        CanonicalIdentityGraphRepository,
    )
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    start, owner = identity_case
    graph, ids, old_delivery, db = start[0]
    _, _, admission = prepare(identity_case)
    result = owner.execute(claim_identity(identity_case, admission))
    before = snapshot(start[0])
    graph.engine.close()
    reopened = PostgresEngine(db.url(), bootstrap=False, validate_schema=False)
    try:
        queue = DurableJobQueue(reopened)
        writer = CanonicalPostgresAuditLog(reopened)
        sink = CanonicalIntakeAuditSink(writer, graph.audit_sink.retention,
            CanonicalAuditDelivery(writer, queue, graph.audit_sink.delivery.sink))
        fresh_graph = CanonicalIdentityGraphRepository(reopened, queue, sink)
        fresh_owner = NativeIntakeIdentityOwner(fresh_graph)
        assert fresh_owner.execute(queue.get(admission["job_id"])) == result
        assert snapshot((fresh_graph, ids, old_delivery, db)) == before
        assert writer.verify_chain(ids["tenant"]).ok
    finally:
        reopened.close()


@pytest.mark.parametrize("resource", ["source_registry", "property"])
def test_concurrent_source_or_property_change_is_serialized_before_any_identity_acceptance(identity_case, monkeypatch, record_property, resource):
    start, owner = identity_case
    graph, ids, _, db = start[0]
    submission, _, admission = prepare(identity_case)
    delivery = claim_identity(identity_case, admission)
    before = native_row(start, submission["intake_id"])
    reached = threading.Event()
    original = graph.engine.query

    def query(sql, params=()):
        target = "FROM intake.source_registry" if resource == "source_registry" else "FROM identity.properties"
        if target in sql and "FOR SHARE" in sql:
            reached.set()
        return original(sql, params)

    monkeypatch.setattr(graph.engine, "query", query)

    def execute():
        pid = None
        try:
            with graph.transaction(ids["tenant"]):
                pid = graph.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"]
                owner.execute(delivery)
            return None, pid
        except Exception as error:
            return error, pid

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with db.connect() as blocker, blocker.transaction():
            blocking_pid = blocker.execute("SELECT pg_backend_pid()").fetchone()[0]
            if resource == "source_registry":
                blocker.execute("UPDATE intake.source_registry SET kill_switch=true,version=version+1 WHERE source_id='synthetic-feed'")
            else:
                blocker.execute("UPDATE identity.properties SET status='QUARANTINED',version=version+1 WHERE property_id=%s", (ids["source"],))
            future = executor.submit(execute)
            assert reached.wait(5), "The phase must reach the actual current FOR SHARE boundary"
        error, worker_pid = future.result(timeout=5)
    finally:
        executor.shutdown(wait=True)
    assert worker_pid and worker_pid != blocking_pid
    assert getattr(error, "sqlstate", None) == "40001", error
    record_property("actual_postgresql_backend_pids", f"{blocking_pid},{worker_pid}")
    record_property("current_resource_conflict_sqlstate", error.sqlstate)
    assert native_row(start, submission["intake_id"]) == before
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT status,attempt,result FROM workflow.jobs WHERE job_id=?", (delivery.job_id,)) == {
            "status": "QUEUED", "attempt": 0, "result": None}


@pytest.mark.parametrize("field", ["submitter", "scope"])
def test_original_submission_proof_cannot_adopt_coordinated_actor_or_scope_rewrites(identity_case, record_property, field):
    start, owner = identity_case
    graph, ids, _, _ = start[0]
    submission, source, _ = prepare(identity_case, admit=False)
    replacement = str(uuid4())
    with graph.transaction(ids["tenant"]):
        row = graph.engine.query_one("SELECT payload FROM workflow.outbox_events WHERE event_id=?", (submission["outbox_event_id"],))
        event = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        if field == "submitter":
            event["actor_ref"] = event["payload"]["submitter_subject_id"] = replacement
            graph.engine.execute("UPDATE intake.intakes SET submitter_subject_id=? WHERE intake_id=?", (replacement, submission["intake_id"]))
        else:
            event["payload"]["scope"]["region_id"] = replacement
            graph.engine.execute("UPDATE intake.intakes SET region_id=? WHERE intake_id=?", (replacement, submission["intake_id"]))
        graph.engine.execute("UPDATE workflow.outbox_events SET payload=?::jsonb WHERE event_id=?", (json.dumps(event), submission["outbox_event_id"]))
    before = phase_snapshot(identity_case)
    with pytest.raises(NativeIntakeIdentityConflict, match="ORIGINAL_SUBMISSION_PROOF"):
        receipt = owner.admit_event(ids["tenant"], source["outbox_event_id"])
        record_property("unexpected_unverified_admission_created", json.dumps(receipt))
    assert phase_snapshot(identity_case) == before
