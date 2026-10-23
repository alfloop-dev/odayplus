"""Real PostgreSQL native start; all users, signing keys and policies synthetic."""

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from types import MethodType
from uuid import uuid4

import pytest

from apps.worker.oday_worker.main import ODayWorker
from modules.listing.application.native_intake_start import (
    NATIVE_INTAKE_START_JOB_TYPE,
    POLICY_ENV,
    SIGNING_KEY_ENV,
    NativeIntakeStartConflict,
    NativeIntakeStartOwner,
    run_native_intake_start_delivery,
)
from shared.audit.errors import CanonicalAuditUnavailable
from shared.domain.events import validate_event
from shared.infrastructure.persistence.factory import PersistenceBundle
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.jobs.queue import JobFenceRejectedError, JobStatus
from tests.integration.test_canonical_intakes_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_canonical_intakes_postgresql import (
    command,
)
from tests.integration.test_canonical_intakes_postgresql import (
    native_api as native_api,
)
from tests.security.test_native_intake_start import policy

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def start_case(canonical_graph, native_api, monkeypatch):
    graph, ids, _, _ = canonical_graph
    client, _, _ = native_api
    monkeypatch.setenv(POLICY_ENV, json.dumps(policy(ids["tenant"])))
    monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-native-start-independent-key-" * 2)
    bundle = client.app.state.persistence_bundle
    bundle.audit_log = client.app.state.audit_log
    bundle.canonical_intake_start = MethodType(PersistenceBundle.canonical_intake_start, bundle)
    return canonical_graph, native_api, bundle, NativeIntakeStartOwner(graph)


def submitted(case, method="MANUAL"):
    _, api, _, _ = case
    body = command(intake_method=method)
    if method == "URL":
        body["original_url"] = "https://synthetic.example/Original?private=synthetic-only"
    response = api[0].post("/api/v1/canonical-intakes", json=body,
        headers={"Idempotency-Key": "synthetic-native-start-" + uuid4().hex})
    assert response.status_code == 201, response.text
    assert response.json()["processing_state"] == "SUBMITTED" and "job_id" not in response.json()
    return response.json()


def claim(case, receipt):
    (graph, _, _, _), _, _, _ = case
    # Audit delivery remains durably pending. Defer these separate synthetic
    # fixture jobs so this test claims the native start via the actual queue.
    graph.engine.execute("UPDATE durable_jobs SET available_at=clock_timestamp()+interval '1 day' "
                         "WHERE job_type='canonical-audit-delivery'")
    current = graph.queue.claim_next(worker_id="synthetic-native-start-worker")
    assert current.job_id == receipt["job_id"] and current.job_type == NATIVE_INTAKE_START_JOB_TYPE
    return current


def native_row(case, intake_id):
    (graph, ids, _, _), _, _, _ = case
    with graph.transaction(ids["tenant"]):
        return graph.engine.query_one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=?",
                                       (ids["tenant"], intake_id))


def admitted(case, method="MANUAL"):
    submission = submitted(case, method)
    owner, ids = case[3], case[0][1]
    reservation = owner.admit_event(ids["tenant"], submission["outbox_event_id"])
    return submission, reservation, claim(case, reservation)


@pytest.mark.parametrize("method", ["MANUAL", "URL"])
def test_real_submission_partition_claim_registered_worker_and_restart_are_durable(start_case, method):
    case, api, bundle, owner = start_case
    graph, ids, _, db = case
    submission = submitted(start_case, method)
    original = native_row(start_case, submission["intake_id"])
    receipts = list(owner.admit_partition(ids["tenant"], limit=5))
    assert len(receipts) == 1
    reservation = receipts[0]
    assert owner.admit_event(ids["tenant"], submission["outbox_event_id"]) == reservation
    assert list(owner.admit_partition(ids["tenant"], limit=5)) == []
    graph.engine.execute("UPDATE durable_jobs SET available_at=clock_timestamp()+interval '1 day' "
                         "WHERE job_type='canonical-audit-delivery'")
    assert ODayWorker(persistence=bundle, heartbeat_interval_seconds=1).run_once() is True
    assert graph.queue.get(reservation["job_id"]).status == JobStatus.SUCCEEDED
    after = native_row(start_case, submission["intake_id"])
    assert after["processing_state"] == "CHECKING_IDENTITY" and after["version"] == 2
    assert {k: v for k, v in after.items() if k not in {"version", "processing_state", "last_transition_at"}} == {
        k: v for k, v in original.items() if k not in {"version", "processing_state", "last_transition_at"}}
    with db.connect() as conn:
        transition = conn.execute("SELECT * FROM intake.intake_stage_transitions WHERE job_id=%s",
                                  (reservation["job_id"],)).fetchone()
        columns = [c.name for c in conn.execute("SELECT * FROM intake.intake_stage_transitions LIMIT 0").description]
        transition = dict(zip(columns, transition, strict=True))
        assert transition["actor_subject_id"] is None
        assert transition["service_principal"] == NATIVE_INTAKE_START_JOB_TYPE
        assert str(transition["causation_id"]) == submission["outbox_event_id"]
        assert transition["sequence_no"] == 2 and transition["expected_version"] == 1 and transition["resulting_version"] == 2
        job = conn.execute("SELECT status,checkpoint,result FROM workflow.jobs WHERE job_id=%s",
                           (reservation["job_id"],)).fetchone()
        assert job[:2] == ("SUCCEEDED", "CHECKING_IDENTITY")
        result = job[2]
        event = conn.execute("SELECT payload FROM workflow.outbox_events WHERE event_id=%s", (result["outbox_event_id"],)).fetchone()[0]
        assert validate_event(event) == [] and event["event_type"] == "intake.state_changed"
        assert set(event["payload"]) == {"intake_id", "from_state", "to_state", "transition_id", "version", "occurred_at"}
        assert event["actor_ref"] == NATIVE_INTAKE_START_JOB_TYPE and event["causation_id"] == submission["outbox_event_id"]
        audit = conn.execute("SELECT actor_subject_id,service_principal,action,causation_id FROM audit.audit_events "
                             "WHERE audit_event_id=%s", (result["audit_event_id"],)).fetchone()
        assert audit[:3] == (None, NATIVE_INTAKE_START_JOB_TYPE, "intake.identity_check_started.v1")
        assert str(audit[3]) == submission["outbox_event_id"]
        for table in ("intake.source_snapshots", "intake.parser_runs"):
            assert conn.execute("SELECT count(*) FROM " + table + " WHERE intake_id=%s", (submission["intake_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM identity.match_cases WHERE intake_id=%s", (submission["intake_id"],)).fetchone()[0] == 0
    reopened = PostgresEngine(db.url(), bootstrap=False, validate_schema=False)
    try:
        with reopened.transaction():
            reopened.query_one("SELECT set_config('app.tenant_id',?,true)", (ids["tenant"],))
            assert reopened.query_one("SELECT processing_state,version FROM intake.intakes WHERE intake_id=?",
                                      (submission["intake_id"],)) == {"processing_state": "CHECKING_IDENTITY", "version": 2}
    finally:
        reopened.close()
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok


@pytest.mark.parametrize("change", ["worker", "fence", "payload", "foreign_root", "terminal_business", "cancel", "version"])
def test_current_authority_and_versions_precede_any_state_write(start_case, change):
    case, _, _, owner = start_case
    graph, ids, _, _ = case
    submission, _, delivery = admitted(start_case)
    if change == "worker":
        delivery = replace(delivery, locked_by="another-worker")
    elif change == "fence":
        delivery = replace(delivery, fence_token=delivery.fence_token + 1)
    elif change == "payload":
        delivery = replace(delivery, payload={**delivery.payload, "actor_role": "SVC_INTAKE"})
    elif change == "terminal_business":
        with graph.transaction(ids["tenant"]):
            graph.engine.execute("UPDATE workflow.jobs SET status='SUCCEEDED' WHERE job_id=?", (delivery.job_id,))
    elif change == "cancel":
        with graph.transaction(ids["tenant"]):
            graph.engine.execute("UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp() WHERE job_id=?", (delivery.job_id,))
    elif change == "version":
        with graph.transaction(ids["tenant"]):
            graph.engine.execute("UPDATE intake.intakes SET version=2 WHERE intake_id=?", (submission["intake_id"],))
    before = native_row(start_case, submission["intake_id"])
    if change == "foreign_root":
        with graph.transaction(str(uuid4())):
            with pytest.raises(PermissionError):
                owner.execute(delivery)
    else:
        with pytest.raises((JobFenceRejectedError, NativeIntakeStartConflict)):
            owner.execute(delivery)
    assert native_row(start_case, submission["intake_id"]) == before


@pytest.mark.parametrize("failure", ["audit", "outbox", "result", "transport"])
def test_any_metadata_failure_rolls_back_transition_and_business_result(start_case, monkeypatch, failure):
    case, _, _, owner = start_case
    graph, ids, _, _ = case
    submission, _, delivery = admitted(start_case)
    before = native_row(start_case, submission["intake_id"])
    if failure == "audit":
        original = graph.audit_sink.persist

        def fail(audit, scope):
            if audit["action"] == "intake.identity_check_started.v1":
                raise CanonicalAuditUnavailable("Synthetic start audit outage")
            return original(audit, scope)

        monkeypatch.setattr(graph.audit_sink, "persist", fail)
    elif failure == "outbox":
        monkeypatch.setattr(graph, "_outbox", lambda _: (_ for _ in ()).throw(ValueError("Synthetic outbox outage")))
    elif failure == "transport":
        monkeypatch.setattr(graph.queue, "update_status", lambda *a, **k: (_ for _ in ()).throw(ValueError("Synthetic settlement outage")))
    else:
        original = graph.engine.execute

        def fail(sql, params=()):
            if "UPDATE workflow.jobs SET status='SUCCEEDED'" in sql:
                raise ValueError("Synthetic result write outage")
            return original(sql, params)

        monkeypatch.setattr(graph.engine, "execute", fail)
    with pytest.raises((ValueError, CanonicalAuditUnavailable)):
        owner.execute(delivery)
    assert native_row(start_case, submission["intake_id"]) == before
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT status,attempt,result FROM workflow.jobs WHERE job_id=?",
            (delivery.job_id,)) == {"status": "QUEUED", "attempt": 0, "result": None}
        assert not graph.engine.query("SELECT transition_id FROM intake.intake_stage_transitions WHERE job_id=?", (delivery.job_id,))
    assert graph.queue.get(delivery.job_id).status == JobStatus.RUNNING


def test_successful_nested_start_is_rolled_back_with_its_caller_root(start_case):
    case, _, _, owner = start_case
    graph, ids, _, _ = case
    submission, _, delivery = admitted(start_case)
    before = native_row(start_case, submission["intake_id"])
    with pytest.raises(RuntimeError, match="Synthetic outer rollback"):
        with graph.transaction(ids["tenant"]):
            assert owner.execute(delivery)["processing_state"] == "CHECKING_IDENTITY"
            raise RuntimeError("Synthetic outer rollback")
    assert native_row(start_case, submission["intake_id"]) == before
    assert graph.queue.get(delivery.job_id).status == JobStatus.RUNNING


def test_failure_after_actual_commit_reads_result_before_any_failure_settlement(start_case, monkeypatch):
    case, _, _, owner = start_case
    graph, ids, _, _ = case
    submission, _, delivery = admitted(start_case)
    original, remaining = graph.transaction, {"raise": True}

    @contextmanager
    def commit_then_fail(tenant):
        with original(tenant):
            yield
        if remaining["raise"]:
            remaining["raise"] = False
            raise OSError("Synthetic lost reply after actual PostgreSQL commit")

    monkeypatch.setattr(graph, "transaction", commit_then_fail)
    result = run_native_intake_start_delivery(delivery, owner)
    assert result["processing_state"] == "CHECKING_IDENTITY"
    assert owner.execute(delivery) == result
    assert native_row(start_case, submission["intake_id"])["version"] == 2
    with original(ids["tenant"]):
        assert len(graph.engine.query("SELECT transition_id FROM intake.intake_stage_transitions WHERE job_id=?", (delivery.job_id,))) == 1
        assert graph.engine.query_one("SELECT status,last_error FROM workflow.jobs WHERE job_id=?",
                                      (delivery.job_id,)) == {"status": "SUCCEEDED", "last_error": None}


def test_expired_business_work_is_failed_by_live_owner_without_advancing_intake(start_case, monkeypatch):
    case, _, _, owner = start_case
    graph, ids, _, _ = case
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(ids["tenant"]), "deadline_seconds": 1}))
    submission, _, delivery = admitted(start_case)
    with graph.transaction(ids["tenant"]):
        graph.engine.query_one("SELECT pg_sleep(1.05)")
    assert run_native_intake_start_delivery(delivery, owner) is None
    assert native_row(start_case, submission["intake_id"])["processing_state"] == "SUBMITTED"
    assert graph.queue.get(delivery.job_id).status == JobStatus.FAILED
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT status FROM workflow.jobs WHERE job_id=?",
                                      (delivery.job_id,))["status"] == "FAILED"


@pytest.mark.parametrize("field", ["producer", "schema_ref", "event_version", "scope", "actor", "method", "link", "correlation"])
def test_tampered_submission_cannot_mint_native_service_authority(start_case, field):
    case, _, _, owner = start_case
    graph, ids, _, _ = case
    submission = submitted(start_case)
    with graph.transaction(ids["tenant"]):
        raw = graph.engine.query_one("SELECT payload FROM workflow.outbox_events WHERE event_id=?",
                                     (submission["outbox_event_id"],))["payload"]
        event = json.loads(raw) if isinstance(raw, str) else raw
        if field == "producer":
            event["producer"] = "unaccepted-producer"
        elif field == "schema_ref":
            event["schema_ref"] = "#/payloads/IntakeStateChangedV1"
        elif field == "event_version":
            event["event_version"] = True
        elif field == "scope":
            event["payload"]["scope"]["region_id"] = str(uuid4())
        elif field == "actor":
            event["actor_ref"] = str(uuid4())
        elif field == "method":
            event["payload"]["intake_method"] = "URL"
        elif field == "link":
            event["payload"]["intake_id"] = str(uuid4())
        else:
            event["correlation_id"] = str(uuid4())
        graph.engine.execute("UPDATE workflow.outbox_events SET payload=?::jsonb WHERE event_id=?",
                              (json.dumps(event), submission["outbox_event_id"]))
    with pytest.raises(NativeIntakeStartConflict):
        owner.admit_event(ids["tenant"], submission["outbox_event_id"])
    with graph.transaction(ids["tenant"]):
        assert not graph.engine.query("SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND job_type=?",
                                       (ids["tenant"], NATIVE_INTAKE_START_JOB_TYPE))


def test_boolean_delivery_generation_is_not_an_integer_admission(start_case):
    case, _, _, owner = start_case
    graph, ids, _, _ = case
    submission, _, delivery = admitted(start_case)
    with graph.transaction(ids["tenant"]):
        graph.engine.execute("UPDATE workflow.jobs SET payload=jsonb_set(payload,'{delivery_generation}','true') "
                              "WHERE job_id=?", (delivery.job_id,))
    with pytest.raises(JobFenceRejectedError):
        owner.execute(delivery)
    assert native_row(start_case, submission["intake_id"])["processing_state"] == "SUBMITTED"


@pytest.mark.parametrize("field", ["service", "expected_version", "causation", "audit_link", "event_link", "job_link"])
def test_corrupt_terminal_receipt_is_not_returned_as_verified_success(start_case, field):
    case, _, _, owner = start_case
    graph, ids, _, _ = case
    submission, _, delivery = admitted(start_case)
    result = owner.execute(delivery)
    with graph.transaction(ids["tenant"]):
        if field == "service":
            graph.engine.execute("UPDATE intake.intake_stage_transitions SET service_principal='wrong-service' "
                                  "WHERE transition_id=?", (result["transition_id"],))
        elif field == "expected_version":
            graph.engine.execute("UPDATE intake.intake_stage_transitions SET expected_version=2 WHERE transition_id=?",
                                  (result["transition_id"],))
        elif field == "causation":
            graph.engine.execute("UPDATE intake.intake_stage_transitions SET causation_id=? WHERE transition_id=?",
                                  (str(uuid4()), result["transition_id"]))
        else:
            key = {"audit_link": "audit_event_id", "event_link": "outbox_event_id", "job_link": "job_id"}[field]
            graph.engine.execute("UPDATE workflow.jobs SET result=?::jsonb WHERE job_id=?",
                                  (json.dumps({**result, key: str(uuid4())}), delivery.job_id))
    with pytest.raises(JobFenceRejectedError):
        owner.execute(delivery)
    assert native_row(start_case, submission["intake_id"])["version"] == 2


@pytest.mark.parametrize("field", ["intake_id", "audit_event_id", "admitted_intake_version"])
def test_corrupt_admission_receipt_is_not_replayed(start_case, field):
    case, _, _, owner = start_case
    graph, ids, _, _ = case
    submission = submitted(start_case)
    receipt = owner.admit_event(ids["tenant"], submission["outbox_event_id"])
    with graph.transaction(ids["tenant"]):
        raw = graph.engine.query_one("SELECT payload FROM workflow.jobs WHERE job_id=?", (receipt["job_id"],))["payload"]
        payload = json.loads(raw) if isinstance(raw, str) else raw
        payload["admission_receipt"][field] = True if field == "admitted_intake_version" else str(uuid4())
        graph.engine.execute("UPDATE workflow.jobs SET payload=?::jsonb WHERE job_id=?", (json.dumps(payload), receipt["job_id"]))
    with pytest.raises((NativeIntakeStartConflict, JobFenceRejectedError)):
        owner.admit_event(ids["tenant"], submission["outbox_event_id"])


def test_two_real_connections_cannot_admit_the_same_submission_twice(start_case, record_property):
    case, _, _, owner = start_case
    graph, ids, _, _ = case
    submission = submitted(start_case)
    barrier = threading.Barrier(2)

    def attempt():
        pid = None
        try:
            with graph.transaction(ids["tenant"]):
                graph.engine.query_one("SELECT set_config('statement_timeout','5000ms',true)")
                pid = graph.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"]
                barrier.wait(timeout=5)
                receipt = owner.admit_event(ids["tenant"], submission["outbox_event_id"])
            return pid, receipt, None, None
        except Exception as error:
            return (pid, None, getattr(error, "sqlstate", type(error).__name__),
                    getattr(getattr(error, "diag", None), "constraint_name", None))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(attempt) for _ in range(2)]
        outcomes = [future.result(timeout=12) for future in futures]
    assert len({row[0] for row in outcomes}) == 2 and all(row[0] for row in outcomes)
    record_property("actual_postgresql_backend_pids", ",".join(str(row[0]) for row in outcomes))
    receipts = [row[1] for row in outcomes if row[1] is not None]
    assert receipts and all(receipt == receipts[0] for receipt in receipts)
    assert all(row[2] in {None, "40001", "40P01", "23505"} for row in outcomes), outcomes
    for row in outcomes:
        if row[2] == "23505":
            assert row[3] in {"jobs_pkey", "jobs_tenant_id_job_type_idempotency_key_key"}
    record_property("reservation_conflicts", json.dumps([{"sqlstate": row[2], "constraint": row[3]} for row in outcomes]))
    with graph.transaction(ids["tenant"]):
        assert len(graph.engine.query("SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND job_type=?",
                                       (ids["tenant"], NATIVE_INTAKE_START_JOB_TYPE))) == 1


def test_actual_postgresql_lock_wait_respects_remaining_command_deadline(start_case, monkeypatch, record_property):
    case, _, _, owner = start_case
    graph, ids, _, db = case
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(ids["tenant"]), "deadline_seconds": 1}))
    submission, _, delivery = admitted(start_case)

    def execute():
        started = time.monotonic()
        try:
            owner.execute(delivery)
            return None, time.monotonic() - started
        except Exception as error:
            return error, time.monotonic() - started

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with db.connect() as blocker, blocker.transaction():
            blocker.execute("SELECT intake_id FROM intake.intakes WHERE intake_id=%s FOR UPDATE", (submission["intake_id"],))
            error, elapsed = executor.submit(execute).result(timeout=5)
    finally:
        # Leaving the blocker transaction precedes joining on every failure,
        # so a failed timeout assertion cannot leave a blocked test thread.
        executor.shutdown(wait=True)
    assert getattr(error, "sqlstate", None) in {"55P03", "57014"}
    assert elapsed < 5
    record_property("blocked_statement_elapsed_seconds", elapsed)
    assert owner.settle_failure(delivery, error) is None
    assert native_row(start_case, submission["intake_id"])["processing_state"] == "SUBMITTED"
    assert graph.queue.get(delivery.job_id).status == JobStatus.FAILED


@pytest.mark.parametrize("mode", ["retry", "exhausted", "revoked"])
def test_current_policy_and_bounded_retry_settle_both_job_axes(start_case, monkeypatch, mode):
    case, _, _, owner = start_case
    graph, ids, _, _ = case
    p = {**policy(ids["tenant"]), "backoff_seconds": [0.001]}
    if mode == "exhausted":
        p.update(max_attempts=1, backoff_seconds=[])
    monkeypatch.setenv(POLICY_ENV, json.dumps(p))
    submission, _, delivery = admitted(start_case)
    original, remaining = graph.audit_sink.persist, {"fail": True}

    def fail_once(audit, scope):
        if audit["action"] == "intake.identity_check_started.v1" and remaining["fail"]:
            remaining["fail"] = False
            raise CanonicalAuditUnavailable("Synthetic transient audit outage")
        return original(audit, scope)

    monkeypatch.setattr(graph.audit_sink, "persist", fail_once)
    if mode == "revoked":
        monkeypatch.setenv(POLICY_ENV, json.dumps({**p, "tenant_ids": [str(uuid4())]}))
    assert run_native_intake_start_delivery(delivery, owner) is None
    assert native_row(start_case, submission["intake_id"])["processing_state"] == "SUBMITTED"
    with graph.transaction(ids["tenant"]):
        row = graph.engine.query_one("SELECT status,attempt,next_attempt_at FROM workflow.jobs WHERE job_id=?", (delivery.job_id,))
        assert row["attempt"] == 1
        if mode == "retry":
            assert row["status"] == "RETRYING" and row["next_attempt_at"]
        else:
            assert row["status"] == "FAILED" and row["next_attempt_at"] is None
    if mode == "retry":
        replacement = claim(start_case, {"job_id": delivery.job_id})
        assert replacement.fence_token > delivery.fence_token
        assert run_native_intake_start_delivery(replacement, owner)["processing_state"] == "CHECKING_IDENTITY"
        assert native_row(start_case, submission["intake_id"])["version"] == 2
    else:
        assert graph.queue.get(delivery.job_id).status == JobStatus.FAILED


def test_partition_cli_prints_durable_opaque_receipt_with_injected_native_runtime(start_case, monkeypatch, capsys):
    from apps.api import server
    from apps.worker.oday_worker import native_intake_start as cli

    case, _, bundle, _ = start_case
    _, ids, _, db = case
    submission = submitted(start_case, "URL")
    monkeypatch.setattr(server, "bootstrap_runtime", lambda: bundle)
    monkeypatch.setattr(sys, "argv", ["native_intake_start", "--tenant-id", ids["tenant"], "--limit", "1"])
    capsys.readouterr()
    cli.main()
    output = capsys.readouterr().out
    assert "synthetic.example" not in output and "private" not in output and "attempt_token" not in output
    receipt = json.loads(output)["admission_receipt"]
    assert receipt["intake_id"] == submission["intake_id"]
    with db.connect() as conn:
        assert conn.execute("SELECT status FROM workflow.jobs WHERE job_id=%s", (receipt["job_id"],)).fetchone()[0] == "QUEUED"
        assert conn.execute("SELECT processing_state FROM intake.intakes WHERE intake_id=%s", (submission["intake_id"],)).fetchone()[0] == "SUBMITTED"
