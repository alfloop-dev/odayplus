"""Actual admission in a separately named engineering DB/source/bucket realm.

Upstream evidence is produced through native HTTP/SQL owners and actual
bounded synthetic storage operations. No historical rows or grants are
rewritten to make a previously captured snapshot fit the parser namespace.
"""
import json
import os
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from psycopg import sql
from psycopg.errors import ObjectInUse

from modules.external_data.application import native_manual_json_parser as provider
from modules.external_data.application.native_intake_process import NativeIORejected
from modules.external_data.application.native_parser_artifacts import (
    NativeParserArtifactDeployment,
    NativeParserArtifactRegistry,
)
from modules.listing.application.native_intake_assisted_entry import NativeIntakeAssistedEntryOwner
from modules.listing.application.native_intake_assisted_entry_policy import (
    POLICY_ENV as MANUAL_POLICY,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    SIGNING_KEY_ENV as MANUAL_KEY,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    STORAGE_ENV as MANUAL_STORAGE,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_parser import NativeIntakeParserOwner
from modules.listing.application.native_intake_parser_execution import NativeParserExecutionReads
from modules.listing.application.native_intake_parser_job_proof import parser_delivery_identity
from modules.listing.application.native_intake_parser_policy import (
    JOB_TYPE,
    POLICY_ENV,
    REALM_ENV,
    SIGNING_KEY_ENV,
    STORAGE_ENV,
    NativeParserConflict,
    NativeParserUnavailable,
)
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from modules.listing.application.native_intake_url_identity import NATIVE_URL_RULE_VERSION
from shared.audit.errors import CanonicalAuditUnavailable
from shared.auth import Role
from shared.jobs.queue import JobFenceRejectedError, JobStatus
from tests.conftest import IntakeDatabase
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import Provider
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import BODY
from tests.integration.test_native_intake_identity_postgresql import defer_audit
from tests.integration.test_native_intake_requester_authority_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_source_policy_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_source_policy_postgresql import drive_upstream, native_row
from tests.integration.test_native_intake_source_policy_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_source_policy_postgresql import native_api as native_api
from tests.integration.test_native_intake_source_policy_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_source_policy_postgresql import start_case as start_case
from tests.security.test_native_parser_artifacts import installed_bytes, write_archive

pytestmark = pytest.mark.requires_live_env
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def parser_realm():
    namespace = "p_" + uuid4().hex[:16]
    return {"version": 1, "mode": "isolated_engineering", "namespace": namespace,
        "database_name": "oday_native_parser_" + namespace}


@pytest.fixture
def intake_blank_db(intake_pg_server, parser_realm, record_property):
    """Own OID checked at creation and normal drop; never terminate sessions."""
    name = parser_realm["database_name"]
    with intake_pg_server.connect("postgres") as connection:
        assert connection.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone() is None
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        oid = connection.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone()[0]
    record_property("parser_engineering_database", json.dumps({"name": name, "oid": oid}))
    try:
        yield IntakeDatabase(server=intake_pg_server, dbname=name)
    finally:
        with intake_pg_server.connect("postgres") as connection:
            assert connection.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone() == (oid,)
            # A later stats read can observe a backend after an earlier zero.
            # Guard every normal DROP itself; a concurrent connection still
            # makes PostgreSQL refuse it. Never terminate or FORCE a session.
            started = time.monotonic()
            observations = []
            try:
                while True:
                    assert connection.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone() == (oid,)
                    sessions = connection.execute("SELECT pid,backend_type,state FROM pg_stat_activity "
                        "WHERE datname=%s ORDER BY pid", (name,)).fetchall()
                    observation = {"elapsed_seconds": time.monotonic()-started, "sessions": sessions}
                    observations.append(observation)
                    if not sessions:
                        try:
                            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                        except ObjectInUse:
                            observation["normal_drop_refused_connection_race"] = True
                        else:
                            break
                    if time.monotonic()-started >= 5:
                        raise AssertionError("Owned parser database still has sessions; normal drop refused")
                    time.sleep(.05)
            finally:
                record_property("parser_normal_close_drain_seconds", time.monotonic()-started)
                record_property("parser_normal_close_observations", json.dumps(observations))
            assert connection.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone() is None


@pytest.fixture
def parser_admission_case(source_policy_case, authority, parser_realm, monkeypatch, tmp_path, request):
    # Explicit caller fields travel through the actual HTTP request, snapshot
    # IO and parser. Default/partial fixtures retain their original BODY.
    supplied = getattr(request, "param", None)
    submission_body = BODY
    submission_scope = {}
    if supplied is not None:
        assert type(supplied) is dict and supplied and set(supplied) <= {"fields", "scope"}
        if "fields" in supplied:
            assert type(supplied["fields"]) is dict
            submission_body = {**BODY, "fields": dict(supplied["fields"])}
        if "scope" in supplied:
            assert type(supplied["scope"]) is dict
            submission_scope = dict(supplied["scope"])
    identity, source_owner = source_policy_case
    start, identity_owner = identity
    graph, ids, _, database = start[0]
    assert graph is authority[0]
    namespace = parser_realm["namespace"].replace("_", "-")
    source, bucket, host = "native-parser-" + namespace + ".manual", "native-parser-" + namespace, namespace + ".example.invalid"
    kms = "projects/synthetic/locations/asia-east1/keyRings/parser/cryptoKeys/manual/cryptoKeyVersions/3"
    with database.connect() as connection:
        connection.execute("INSERT INTO intake.source_registry (source_id,display_name,allowed_hosts,"
            "canonicalization_rule_version,retrieval_mode,legal_approval_ref,license_approval_ref,"
            "review_expires_at,policy_owner_subject_id,production_enabled,kill_switch,requests_per_minute,"
            "concurrent_requests,assisted_entry_approval_ref,assisted_entry_approved_by,"
            "assisted_entry_review_expires_at) VALUES (%s,'Isolated synthetic parser source',%s,%s,"
            "'AUTH_REQUIRED','synthetic-legal','synthetic-license',clock_timestamp()+interval '1 hour',%s,"
            "true,false,0,1,'synthetic-explicit-manual',%s,clock_timestamp()+interval '1 hour')",
            (source, [host], NATIVE_URL_RULE_VERSION, ids["actor"], ids["reviewer"]))
        connection.execute((ROOT / "infra/db/migrations/000035_native_intake_retrieval_quota.sql").read_text())
    client, caller, _ = start[1]
    caller["principal"] = replace(caller["principal"], roles=frozenset({Role.DATA_OWNER}))
    response = client.post("/api/v1/canonical-intakes", json={"intake_method": "URL", "scope": submission_scope,
        "original_url": "https://" + host + "/Listing", "reason": "Explicit isolated parser evidence"},
        headers={"Idempotency-Key": "synthetic-parser-submission-" + uuid4().hex})
    assert response.status_code == 201, response.text
    submission = response.json()
    first = start[3].admit_event(ids["tenant"], submission["outbox_event_id"])
    started = drive_upstream(source_policy_case, first, start[3])
    identified = identity_owner.admit_event(ids["tenant"], started["outbox_event_id"])
    identified = drive_upstream(source_policy_case, identified, identity_owner)
    assert identified["processing_state"] == "CHECKING_SOURCE_POLICY"
    reserved = source_owner.admit_event(ids["tenant"], identified["outbox_event_id"])
    waiting = drive_upstream(source_policy_case, reserved, source_owner)
    assert waiting["processing_state"] == "AWAITING_ASSISTED_ENTRY"
    assert native_row(start, waiting["intake_id"])["source_id"] == source
    policy = {"policy_id": "synthetic-parser-manual-capture", "version": 1,
        "tenant_ids": [ids["tenant"]], "max_attempts": 3, "deadline_seconds": 120, "backoff_seconds": [1, 3]}
    storage = {"version": 1, "global_max_concurrency": 5, "per_source_max_concurrency": 1,
        "storage_bindings": [{"tenant_id": ids["tenant"], "bucket": bucket, "residency_mode": "TW_ONLY",
            "retention_class": "STANDARD", "kms_key_name": kms}]}
    monkeypatch.setenv(MANUAL_POLICY, json.dumps(policy))
    monkeypatch.setenv(MANUAL_STORAGE, json.dumps(storage))
    monkeypatch.setenv(MANUAL_KEY, "synthetic-parser-upstream-manual-key-" * 2)
    monkeypatch.setenv("ODP_RESIDENCY_APPROVED_BUCKETS_TW_ONLY", bucket)
    response = client.post("/api/v1/canonical-intakes/" + waiting["intake_id"] + "/assisted-entry-completions",
        headers={"Idempotency-Key": "synthetic-parser-manual-request-" + uuid4().hex,
            "If-Match": f'W/"{waiting["version"]}"'}, json=submission_body)
    assert response.status_code == 202, response.text
    capture = Provider()
    manual = NativeIntakeAssistedEntryOwner(graph, snapshot_transport_factory=capture.factory)
    reserved = manual.admit_event(ids["tenant"], response.json()["request_id"])
    defer_audit(graph)
    delivery = graph.queue.claim_next(worker_id="synthetic-parser-upstream-capture")
    assert delivery.job_id == reserved["job_id"]
    result = manual.execute(delivery)
    if result is None:
        with graph.transaction(ids["tenant"]):
            failed = graph.engine.query_one("SELECT status,last_error FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                (ids["tenant"], delivery.job_id))
        error = json.loads(failed["last_error"]) if isinstance(failed["last_error"], str) else (failed["last_error"] or {})
        pytest.fail("Synthetic upstream manual capture did not complete: " + json.dumps({"status": failed["status"],
            "error_class": error.get("error_class"), "code": error.get("code")}))
    assert result["processing_state"] == "PARSING"
    assert graph.queue.get(delivery.job_id).status == JobStatus.SUCCEEDED
    release_id, uri = str(uuid4()), "gs://" + bucket + "/approved-deployment/manual-v1.zip"
    archive = tmp_path / "approved-parser.zip"
    digest = write_archive(archive, installed_bytes().items())
    with database.connect() as connection:
        connection.execute("INSERT INTO intake.parser_releases (parser_release_id,source_id,package_name,"
            "semantic_version,input_schema_version,output_schema_version,artifact_uri,artifact_sha256,"
            "test_corpus_version,validation_status,canary_percent,released_by,released_at) "
            "VALUES (%s,%s,%s,'1.0.0',%s,%s,%s,%s,%s,'PRODUCTION',100,%s,clock_timestamp())",
            (release_id, source, provider.PACKAGE_NAME, provider.INPUT_SCHEMA_VERSION,
                provider.OUTPUT_SCHEMA_VERSION, uri, digest, provider.CORPUS_VERSION, ids["reviewer"]))
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy, "policy_id": "synthetic-parser-own-purpose"}))
    monkeypatch.setenv(STORAGE_ENV, json.dumps(storage))
    monkeypatch.setenv(REALM_ENV, json.dumps(parser_realm))
    monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-parser-own-key-distinct-from-capture-" * 2)
    monkeypatch.setenv("ODP_NATIVE_INTAKE_PARSER_SNAPSHOT_GCS_TOKEN", "synthetic-no-live-GCS-token")
    registry = NativeParserArtifactRegistry((NativeParserArtifactDeployment(release_id, source, uri, archive),))
    owner = NativeIntakeParserOwner(graph, artifact_registry=registry)
    from tests.integration._native_parser_lifecycle_storage import (
        install_synthetic_storage_lifecycle,
    )

    install_synthetic_storage_lifecycle(monkeypatch, capture)
    return owner, ids, database, result, release_id, capture, start


def admit(case, **overrides):
    owner, ids, _, result, release, _, _ = case
    values = {"tenant": ids["tenant"], "intake_id": result["intake_id"],
        "snapshot_id": result["source_snapshot_id"], "release_id": release, "expected_version": result["version"]}
    return owner.admit(**{**values, **overrides})


def persisted(case):
    owner, ids, _, _, _, capture, _ = case
    with owner.graph.transaction(ids["tenant"]):
        tables = ("intake.intakes", "intake.parser_runs", "workflow.jobs", "workflow.outbox_events",
            "audit.audit_events", "intake.source_snapshots", "intake.human_corrections",
            "identity.match_cases", "expansion.listings")
        value = {table: owner.engine.query("SELECT * FROM " + table + " WHERE tenant_id=? ORDER BY 1", (ids["tenant"],))
            for table in tables}
        value["delivery"] = owner.engine.query("SELECT * FROM durable_jobs ORDER BY job_id")
    value["storage_calls"] = list(capture.calls)
    return value


def test_parser_budget_sets_both_actual_local_timeouts_and_expiry_preserves_them(parser_admission_case):
    owner, ids, _, _, _, _, _ = parser_admission_case
    with owner.graph.transaction(ids["tenant"]):
        before = NativeParserCommandBudget(owner.clock)
        original = before.deadline, before._monotonic_deadline
        before()
        settings = owner.engine.query_one("SELECT current_setting('statement_timeout') AS statement_timeout, "
            "current_setting('lock_timeout') AS lock_timeout")
        assert settings["statement_timeout"] == settings["lock_timeout"]
        assert 1 <= int(settings["statement_timeout"][:-2]) <= 749
        with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
            owner.clock._admission_budget(owner.clock._clock() - timedelta(seconds=1))
        assert owner.engine.query_one("SELECT current_setting('statement_timeout') AS statement_timeout, "
            "current_setting('lock_timeout') AS lock_timeout") == settings
        assert (before.deadline, before._monotonic_deadline) == original


def test_admission_reserves_one_run_and_two_jobs_without_mutating_input_or_intake(parser_admission_case):
    case = parser_admission_case
    owner, ids, _, result, release, _, _ = case
    original = persisted(case)
    receipt = admit(case)
    current = persisted(case)
    assert len(current["intake.parser_runs"]) == len(original["intake.parser_runs"]) + 1
    assert len(current["workflow.jobs"]) == len(original["workflow.jobs"]) + 1
    assert len([row for row in current["delivery"] if row["job_type"] == JOB_TYPE]) == 1
    for table in ("intake.intakes", "intake.source_snapshots", "intake.human_corrections",
            "identity.match_cases", "expansion.listings", "workflow.outbox_events", "storage_calls"):
        assert current[table] == original[table], table
    assert admit(case) == receipt and persisted(case) == current
    with owner.graph.transaction(ids["tenant"]):
        before = NativeParserCommandBudget(owner.clock)
        run = owner.runs.get(ids["tenant"], receipt["parser_run_id"], before=before)
        job = owner.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], receipt["job_id"]))
        assert run["status"] == "QUEUED" and run["parser_release_id"] == release
        assert job["status"] == "QUEUED" and job["attempt"] == 0 and job["max_attempts"] == 3
        assert owner.jobs.admission(job, run, before=before) == receipt
        delivery = owner.queue.get(receipt["job_id"])
        assert parser_delivery_identity(delivery) == (ids["tenant"], 1)
        assert owner.auth.decode(delivery) == (ids["tenant"], 1)
    assert result["version"] == receipt["admitted_intake_version"]
    assert owner.graph.audit_sink.writer.verify_chain(ids["tenant"]).ok


@pytest.mark.parametrize("attempt_cap", [1, 2, 5])
def test_explicit_parser_attempt_cap_is_written_to_actual_canonical_physical_queue(parser_admission_case, monkeypatch, attempt_cap):
    case = parser_admission_case
    owner, ids, _, _, _, _, _ = case
    policy = json.loads(os.environ[POLICY_ENV])
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy, "max_attempts": attempt_cap,
        "backoff_seconds": [1] * (attempt_cap - 1)}))
    receipt = admit(case)
    with owner.graph.transaction(ids["tenant"]):
        before = NativeParserCommandBudget(owner.clock)
        before()
        physical = owner.engine.query_one("SELECT status,max_retries,attempts,fence_token FROM durable_jobs WHERE job_id=?",
            (receipt["job_id"],))
        before()
        business = owner.graph._one("SELECT status,max_attempts,attempt,timeout_at FROM workflow.jobs "
            "WHERE tenant_id=? AND job_id=?", (ids["tenant"], receipt["job_id"]))
        assert physical == {"status": JobStatus.QUEUED.value, "max_retries": attempt_cap, "attempts": 0, "fence_token": 0}
        assert business["status"] == "QUEUED" and business["max_attempts"] == attempt_cap and business["attempt"] == 0
        assert business["timeout_at"] == receipt["timeout_at"]
    original = persisted(case)
    assert admit(case) == receipt and persisted(case) == original


@pytest.mark.parametrize("missing", [POLICY_ENV, STORAGE_ENV, REALM_ENV, SIGNING_KEY_ENV,
    "ODP_NATIVE_INTAKE_PARSER_SNAPSHOT_GCS_TOKEN", "registry", "release"])
def test_missing_prerequisite_creates_no_run_job_or_positive_event(parser_admission_case, monkeypatch, missing):
    case = parser_admission_case
    if missing == "registry":
        case[0].artifact_registry = None
    elif missing != "release":
        monkeypatch.delenv(missing)
    original = persisted(case)
    expected = NativeIORejected if missing == "ODP_NATIVE_INTAKE_PARSER_SNAPSHOT_GCS_TOKEN" else NativeParserUnavailable
    codes = {"registry": "NATIVE_PARSER_ARTIFACT_REGISTRY_REQUIRED", "release": "NATIVE_PARSER_RELEASE_UNAVAILABLE",
        SIGNING_KEY_ENV: "NATIVE_PARSER_SIGNING_KEY_UNAVAILABLE",
        "ODP_NATIVE_INTAKE_PARSER_SNAPSHOT_GCS_TOKEN": "NATIVE_GCS_PARSER_CREDENTIAL_UNAVAILABLE"}
    with pytest.raises(expected, match="^" + codes.get(missing, "NATIVE_PARSER_CONFIGURATION_UNAVAILABLE") + "$"):
        admit(case, **({"release_id": str(uuid4())} if missing == "release" else {}))
    assert persisted(case) == original


@pytest.mark.parametrize("fault", ["audit", "key_after_enqueue"])
def test_admission_writes_roll_back_together_when_final_authority_or_audit_is_lost(parser_admission_case, monkeypatch, fault):
    case = parser_admission_case
    owner = case[0]
    original = persisted(case)
    if fault == "audit":
        def unavailable(*args, **kwargs):
            raise CanonicalAuditUnavailable("Synthetic admission audit unavailable")
        monkeypatch.setattr(owner.graph.audit_sink, "persist", unavailable)
    else:
        enqueue = owner.queue.enqueue

        def revoke(*args, **kwargs):
            value = enqueue(*args, **kwargs)
            if args and args[0].job_type == JOB_TYPE:
                monkeypatch.delenv(SIGNING_KEY_ENV)
            return value
        monkeypatch.setattr(owner.queue, "enqueue", revoke)
    with pytest.raises((CanonicalAuditUnavailable, NativeParserUnavailable)):
        admit(case)
    assert persisted(case) == original


def test_original_admission_remains_readable_after_all_positive_parser_settings_are_removed(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids, _, _, _, _, _ = case
    receipt = admit(case)
    for env in (POLICY_ENV, STORAGE_ENV, REALM_ENV, SIGNING_KEY_ENV, "ODP_NATIVE_INTAKE_PARSER_SNAPSHOT_GCS_TOKEN"):
        monkeypatch.delenv(env)
    reopened = NativeIntakeParserOwner(owner.graph)
    original = persisted(case)
    with reopened.graph.transaction(ids["tenant"]):
        before = NativeParserCommandBudget(reopened.clock)
        run = reopened.runs.get(ids["tenant"], receipt["parser_run_id"], before=before)
        job = reopened.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], receipt["job_id"]))
        assert reopened.jobs.admission(job, run, before=before) == receipt
    assert persisted(case) == original
    with pytest.raises(NativeParserUnavailable):
        admit(case)
    assert persisted(case) == original


@pytest.mark.parametrize("tamper", ["audit", "policy", "physical_token", "timeout"])
def test_actual_admission_rows_cannot_substitute_labels_for_original_provenance(parser_admission_case, tamper):
    case = parser_admission_case
    owner, ids, database, _, _, _, _ = case
    receipt = admit(case)
    with database.connect() as connection:
        if tamper == "audit":
            connection.execute("UPDATE audit.audit_events SET after_value='{}'::jsonb WHERE audit_event_id=%s",
                (receipt["audit_event_id"],))
        elif tamper == "policy":
            connection.execute("UPDATE workflow.jobs SET payload=jsonb_set(payload,"
                "'{admission,policy,max_attempts}','4'::jsonb) WHERE job_id=%s", (receipt["job_id"],))
        elif tamper == "physical_token":
            changed = connection.execute("UPDATE odp_runtime.durable_jobs SET payload_json=%s WHERE job_id=%s",
                (json.dumps({"job_id": receipt["job_id"], "tenant_id": ids["tenant"],
                    "attempt_token": "1." + "f" * 64}), receipt["job_id"]))
            assert changed.rowcount == 1
            assert connection.execute("SELECT payload_json->>'attempt_token' FROM odp_runtime.durable_jobs "
                "WHERE job_id=%s", (receipt["job_id"],)).fetchone() == ("1." + "f" * 64,)
        else:
            connection.execute("UPDATE workflow.jobs SET timeout_at=timeout_at+interval '1 second' WHERE job_id=%s",
                (receipt["job_id"],))
    original = persisted(case)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ADMISSION_PROOF_REJECTED$"):
        admit(case)
    assert persisted(case) == original


def claimed(case):
    receipt = admit(case)
    owner = case[0]
    defer_audit(owner.graph)
    delivery = owner.queue.claim_next(worker_id="synthetic-native-parser-worker")
    assert delivery.job_type == JOB_TYPE and delivery.job_id == receipt["job_id"]
    assert delivery.lease_expires_at is not None and delivery.leased_until is None
    return receipt, delivery


def test_initial_acceptance_couples_existing_run_and_attempt_without_output_or_intake_change(parser_admission_case):
    case = parser_admission_case
    owner, ids, _, _, _, _, _ = case
    receipt, delivery = claimed(case)
    original = persisted(case)
    fence, policy = owner._accept(delivery, ids["tenant"], 1)
    assert fence == 2 and policy.max_attempts == 3
    current = persisted(case)
    for table in ("intake.intakes", "intake.source_snapshots", "intake.human_corrections",
            "identity.match_cases", "expansion.listings", "workflow.outbox_events", "storage_calls"):
        assert current[table] == original[table], table
    original_deliveries = {row["job_id"]: row for row in original["delivery"]}
    current_deliveries = {row["job_id"]: row for row in current["delivery"]}
    assert all(current_deliveries.get(ident) == row for ident, row in original_deliveries.items())
    added_deliveries = [row for ident, row in current_deliveries.items() if ident not in original_deliveries]
    assert len(added_deliveries) == 1
    added = added_deliveries[0]
    with owner.graph.transaction(ids["tenant"]):
        state = NativeParserExecutionReads(owner).fresh(delivery, ids["tenant"], 1, business_fence=fence)
        assert state.run["status"] == state.job["status"] == "RUNNING"
        assert state.run["version"] == 2 and state.job["attempt"] == 1
        assert state.run["parsed_payload"] is None and state.run["normalized_payload"] is None
        assert state.job["timeout_at"] == receipt["timeout_at"]
        assert state.payload["admission"]["policy"]["max_attempts"] == state.job["max_attempts"] == 3
        acceptance_id = state.payload["acceptance_receipt"]["audit_event_id"]
        acceptance = [row for row in current["audit.audit_events"] if row["audit_event_id"] == acceptance_id]
        assert len(acceptance) == 1 and acceptance[0]["action"] == "intake.parser_attempt_accepted.v1"
        sinks = {json.loads(row["payload_json"])["sink_id"] for row in original["delivery"]
            if row["job_type"] == "canonical-audit-delivery"}
        assert len(sinks) == 1
        assert added["job_type"] == "canonical-audit-delivery" and added["status"] == JobStatus.QUEUED.value
        assert added["attempts"] == added["fence_token"] == 0
        assert added["correlation_id"] == state.job["correlation_id"]
        assert added["idempotency_key"] == "canonical-audit:" + ids["tenant"] + ":" + acceptance_id
        assert json.loads(added["payload_json"]) == {"tenant_id": ids["tenant"], "audit_event_id": acceptance_id,
            "event_sha256": acceptance[0]["event_sha256"], "sink_id": next(iter(sinks))}
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_ACCEPTANCE_NOT_CURRENT$"):
        owner._accept(delivery, ids["tenant"], 1)
    assert persisted(case) == current
    assert owner.graph.audit_sink.writer.verify_chain(ids["tenant"]).ok


@pytest.mark.parametrize("loss", ["all_settings", "human_role", "source_permission", "storage_credential", "registry"])
def test_owned_accepted_history_survives_current_authority_loss_without_authorizing_more_work(parser_admission_case, monkeypatch, loss):
    case = parser_admission_case
    owner, ids, database, _, _, _, _ = case
    _, delivery = claimed(case)
    fence, _ = owner._accept(delivery, ids["tenant"], 1)
    if loss == "all_settings":
        for env in (POLICY_ENV, STORAGE_ENV, REALM_ENV, SIGNING_KEY_ENV):
            monkeypatch.delenv(env)
    elif loss == "human_role":
        with database.connect() as connection:
            connection.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (ids["actor"],))
    elif loss == "source_permission":
        with database.connect() as connection:
            connection.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id LIKE 'native-parser-%'")
    elif loss == "storage_credential":
        monkeypatch.delenv("ODP_NATIVE_INTAKE_PARSER_SNAPSHOT_GCS_TOKEN")
    else:
        owner.artifact_registry = None
    original = persisted(case)
    with owner.graph.transaction(ids["tenant"]):
        physical, job, payload, run, before = NativeParserExecutionReads(owner).historical_locked(
            delivery, ids["tenant"], 1, business_fence=fence)
        assert job["status"] == run["status"] == "RUNNING"
        assert payload["acceptance_receipt"]["physical_fence"] == physical.fence_token
        before()
    with pytest.raises((NativeParserUnavailable, NativeIORejected, PermissionError, HTTPException, NativeAssistedEntryConflict)):
        with owner.graph.transaction(ids["tenant"]):
            NativeParserExecutionReads(owner).fresh(delivery, ids["tenant"], 1, business_fence=fence)
    assert persisted(case) == original


def test_acceptance_audit_failure_rolls_back_run_and_business_attempt_together(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner, ids, _, _, _, _, _ = case
    _, delivery = claimed(case)
    original = persisted(case)

    def unavailable(*args, **kwargs):
        raise CanonicalAuditUnavailable("Synthetic parser acceptance audit unavailable")

    monkeypatch.setattr(owner.graph.audit_sink, "persist", unavailable)
    with pytest.raises(CanonicalAuditUnavailable):
        owner._accept(delivery, ids["tenant"], 1)
    assert persisted(case) == original


@pytest.mark.parametrize("wrong", ["worker", "fence"])
def test_acceptance_cannot_claim_a_different_physical_worker_or_fence(parser_admission_case, wrong):
    case = parser_admission_case
    owner, ids, _, _, _, _, _ = case
    _, delivery = claimed(case)
    original = persisted(case)
    invalid = replace(delivery, **({"locked_by": "other-worker"} if wrong == "worker"
        else {"fence_token": delivery.fence_token + 1}))
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_PHYSICAL_NOT_OWNED$"):
        owner._accept(invalid, ids["tenant"], 1)
    assert persisted(case) == original
