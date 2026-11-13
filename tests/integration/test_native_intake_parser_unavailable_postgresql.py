"""Real owned unavailable closure: immutable input, no parser output grant."""
import copy
import json
import os
import time
from contextlib import contextmanager
from uuid import uuid4

import pytest

from modules.external_data.application.native_intake_process import NativeIORejected
from modules.listing.application.native_intake_assisted_entry_request_proof import _time
from modules.listing.application.native_intake_parser_leases import NativeParserCompositeLeases
from modules.listing.application.native_intake_parser_policy import (
    POLICY_ENV,
    REALM_ENV,
    SIGNING_KEY_ENV,
    STORAGE_ENV,
    NativeParserConflict,
)
from modules.listing.application.native_intake_retrieval_policy import canonical_json
from shared.audit.errors import CanonicalAuditUnavailable
from shared.domain.events import validate_event
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus
from tests.integration.test_native_intake_parser_admission_postgresql import claimed
from tests.integration.test_native_intake_parser_history_postgresql import (
    retire_all_positive_settings,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    parser_evidence_fields as parser_evidence_fields,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    parser_io_case as parser_io_case,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    parser_realm as parser_realm,
)
from tests.integration.test_native_intake_parser_results_postgresql import persisted
from tests.integration.test_native_intake_parser_results_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    start_case as start_case,
)

pytestmark = pytest.mark.requires_live_env
TOKEN_ENV = "ODP_NATIVE_INTAKE_PARSER_SNAPSHOT_GCS_TOKEN"


def snapshot(case):
    owner, ids, _, result, _, _, _ = case
    value = persisted(case)
    with owner.graph.transaction(ids["tenant"]):
        value["control_transitions"] = owner.engine.query("SELECT * FROM intake.intake_stage_transitions "
            "WHERE tenant_id=? AND intake_id=? ORDER BY sequence_no", (ids["tenant"], result["intake_id"]))
        value["slots"] = owner.engine.query("SELECT * FROM intake.native_retrieval_inflight_slots ORDER BY reservation_id")
        value["rates"] = owner.engine.query("SELECT * FROM intake.native_retrieval_rate_reservations ORDER BY reservation_id")
    return value


def withdraw(case, loss, monkeypatch):
    owner, ids, database, original, release, _, _ = case
    envs = {"key": SIGNING_KEY_ENV, "policy": POLICY_ENV, "storage": STORAGE_ENV, "realm": REALM_ENV, "token": TOKEN_ENV}
    if loss in envs:
        monkeypatch.delenv(envs[loss])
    elif loss == "rotated_key":
        monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-distinct-rotated-parser-key-" * 2)
    elif loss == "registry":
        owner.artifact_registry = None
    else:
        with database.connect() as connection:
            if loss == "human":
                assert connection.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (ids["actor"],)).rowcount > 0
            elif loss == "source":
                source = connection.execute("SELECT source_id FROM intake.intakes WHERE tenant_id=%s AND intake_id=%s",
                    (ids["tenant"], original["intake_id"])).fetchone()[0]
                assert connection.execute("UPDATE intake.source_registry SET kill_switch=true,version=version+1 "
                    "WHERE source_id=%s", (source,)).rowcount == 1
            else:
                assert loss == "release"
                assert connection.execute("UPDATE intake.parser_releases SET validation_status='DEPRECATED',"
                    "deprecated_at=clock_timestamp(),version=version+1 WHERE parser_release_id=%s", (release,)).rowcount == 1


def settle(case, leases, delivery):
    return case[0].settle_unavailable(delivery, business_fence=leases.business_fence, leases=leases)


def assert_preserved(previous, current):
    for key in ("intake.intakes", "intake.source_snapshots", "intake.human_corrections", "identity.match_cases",
            "expansion.listings", "storage_calls", "control_transitions", "slots", "rates"):
        assert current[key] == previous[key], key


def assert_failure(case, delivery, previous, *, operations, errors=0):
    owner, ids, _, _, _, _, _ = case
    current = snapshot(case)
    assert_preserved(previous, current)
    assert len(current["intake.parser_runs"]) == len(previous["intake.parser_runs"]) == 1
    run = current["intake.parser_runs"][0]
    assert run["status"] == "FAILED" and run["parsed_payload"] is None and run["normalized_payload"] is None
    assert json.loads(run["field_confidence"]) == {} and json.loads(run["validation_errors"]) == []
    with owner.graph.transaction(ids["tenant"]):
        job = owner.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (ids["tenant"], delivery.job_id))
        receipt = job["payload"]["negative_receipt"]
        assert job["aggregate_id"] == run["parser_run_id"]
        assert job["status"] == "FAILED" and job["result"] is None
        assert job["lease_expires_at"] is None and job["next_attempt_at"] is None
        assert job["version"] == receipt["business_version"] and run["version"] == receipt["run_version"]
        physical = owner.queue.get(delivery.job_id)
        assert physical.status == JobStatus.FAILED and physical.delivery_state == JobDeliveryState.DEAD_LETTER
        assert physical.version == receipt["physical_version"] and physical.attempts == receipt["physical_attempts"]
        assert physical.locked_by is None and physical.lease_expires_at is None
    events = [row for row in current["workflow.outbox_events"]
        if row["event_id"] not in {p["event_id"] for p in previous["workflow.outbox_events"]}]
    assert len(events) == 2 and {row["event_type"] for row in events} == {"parser.run_completed", "job.dead_lettered"}
    quality = {"recorded_operations": operations, "finished_operations": operations-errors, "error_operations": errors,
        "reaped_operations": operations, "unresolved_operations": 0, "output_committed": False}
    assert receipt["quality_summary"] == quality
    for row in events:
        event = json.loads(row["payload"])
        assert validate_event(event) == [] and row["causation_id"] == event["causation_id"]
        assert json.loads(row["sensitive_fields"]) == event["sensitive_fields"]
        if event["event_type"] == "parser.run_completed":
            assert event["payload"] == {"parser_run_id": run["parser_run_id"], "intake_id": run["intake_id"],
                "parser_release_id": run["parser_release_id"], "status": "FAILED", "quality_summary": quality}
    assert owner.graph.audit_sink.writer.verify_chain(ids["tenant"]).ok
    return receipt


@pytest.mark.parametrize("loss", ["key", "rotated_key", "policy", "storage", "realm", "token", "registry", "human", "source", "release"])
def test_current_capability_loss_settles_only_owned_run_jobs_and_exact_failure_evidence(parser_io_case, monkeypatch, loss):
    case, _, leases, delivery = parser_io_case
    withdraw(case, loss, monkeypatch)
    previous = snapshot(case)
    assert settle(case, leases, delivery) is None
    assert_failure(case, delivery, previous, operations=0)
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    current = snapshot(case)
    assert case[0].replay(delivery) == {"terminal": True, "result": None}
    assert settle(case, leases, delivery) is None
    assert snapshot(case) == current


@pytest.mark.parametrize("loss,parser_evidence_fields", [("key", None), ("key", "partial"),
    ("human", None), ("source", None), ("registry", None)], indirect=["parser_evidence_fields"])
def test_capability_loss_after_actual_reaped_child_never_publishes_its_output(parser_io_case, monkeypatch, loss):
    case, io, leases, delivery = parser_io_case
    io.read_and_parse()
    withdraw(case, loss, monkeypatch)
    previous = snapshot(case)
    assert settle(case, leases, delivery) is None
    assert_failure(case, delivery, previous, operations=4)


def test_current_healthy_capability_cannot_be_labeled_an_unavailable_failure(parser_io_case):
    case, _, leases, delivery = parser_io_case
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_CAPABILITY_STILL_CURRENT$"):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("deadline", ["command", "source"])
def test_capability_loss_cannot_settle_after_original_deadline(
        parser_admission_case, monkeypatch, record_property, deadline):
    from modules.listing.application.native_intake_parser import run_native_intake_parser_delivery
    from tests.integration.test_native_intake_identity_postgresql import defer_audit
    from tests.integration.test_native_intake_parser_budget_postgresql import wait_outside_sql
    from tests.integration.test_native_intake_parser_recovery_postgresql import (
        assert_recovered,
        settle_seed,
    )

    case = parser_admission_case
    owner, ids, database, intake, _, _, _ = case
    settle_seed(case)
    if deadline == "command":
        policy = json.loads(os.environ[POLICY_ENV])
        monkeypatch.setenv(POLICY_ENV, json.dumps({**policy, "max_attempts": 2,
            "deadline_seconds": 6, "backoff_seconds": [1]}))
    else:
        with database.connect() as connection:
            assert connection.execute("UPDATE intake.source_registry SET "
                "review_expires_at=clock_timestamp()+interval '6 seconds',"
                "assisted_entry_review_expires_at=clock_timestamp()+interval '6 seconds',version=version+1 "
                "WHERE source_id=(SELECT source_id FROM intake.intakes WHERE tenant_id=%s AND intake_id=%s)",
                (ids["tenant"], intake["intake_id"])).rowcount == 1
    _, delivery = claimed(case)
    fence, policy = owner._accept(delivery, ids["tenant"], 1)
    leases = NativeParserCompositeLeases(owner, delivery, business_fence=fence, policy_sha256=policy.sha256)
    try:
        with owner.graph.transaction(ids["tenant"]):
            job = owner.graph._one("SELECT timeout_at,payload,lease_expires_at FROM workflow.jobs "
                "WHERE tenant_id=? AND job_id=?", (ids["tenant"], delivery.job_id))
        expires = _time(job["timeout_at"] if deadline == "command"
            else job["payload"]["admission"]["binding"]["source_valid_until"])
        assert expires < _time(job["lease_expires_at"]) <= delivery.lease_expires_at
        record_property("actual_original_deadline_wait_seconds", wait_outside_sql(case, expires))
        withdraw(case, "key", monkeypatch)
        previous = snapshot(case)
        with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_NEGATIVE_LEASE_WITNESS_REJECTED$"):
            settle(case, leases, delivery)
        assert snapshot(case) == previous
        leases.pause()
        record_property("actual_original_physical_lease_wait_seconds",
            wait_outside_sql(case, delivery.lease_expires_at))
        defer_audit(owner.graph)
        reclaimed = owner.queue.claim_next(worker_id="synthetic-native-parser-original-expiry-recovery")
        assert reclaimed.job_id == delivery.job_id and reclaimed.fence_token > delivery.fence_token
        assert reclaimed.attempts == delivery.attempts + 1
        previous = snapshot(case)
        assert run_native_intake_parser_delivery(reclaimed, owner) is None
        with owner.graph.transaction(ids["tenant"]):
            job = owner.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                (ids["tenant"], delivery.job_id))
        receipt = job["payload"]["cancellation_receipt"]
        assert receipt["cause_code"] == ("ORIGINAL_COMMAND_EXPIRED" if deadline == "command"
            else "ORIGINAL_SOURCE_AUTHORITY_EXPIRED")
        assert_recovered(case, reclaimed, previous, receipt, mode="RECLAIMED_PHYSICAL", operations=0)
    finally:
        leases.pause()


def test_negative_receipt_remains_readable_after_original_command_deadline(
        parser_admission_case, monkeypatch, record_property):
    from tests.integration.test_native_intake_parser_budget_postgresql import wait_outside_sql

    case = parser_admission_case
    owner, ids = case[:2]
    policy = json.loads(os.environ[POLICY_ENV])
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy, "max_attempts": 2,
        "deadline_seconds": 6, "backoff_seconds": [1]}))
    _, delivery = claimed(case)
    fence, policy = owner._accept(delivery, ids["tenant"], 1)
    leases = NativeParserCompositeLeases(owner, delivery, business_fence=fence, policy_sha256=policy.sha256)
    try:
        with owner.graph.transaction(ids["tenant"]):
            job = owner.graph._one("SELECT timeout_at FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                (ids["tenant"], delivery.job_id))
        withdraw(case, "key", monkeypatch)
        previous = snapshot(case)
        assert settle(case, leases, delivery) is None
        receipt = assert_failure(case, delivery, previous, operations=0)
        assert _time(receipt["settled_at"]) < _time(job["timeout_at"])
        record_property("actual_original_deadline_wait_seconds", wait_outside_sql(case, _time(job["timeout_at"])))
        retire_all_positive_settings(monkeypatch)
        owner.artifact_registry = None
        current = snapshot(case)
        assert owner.replay(delivery) == {"terminal": True, "result": None}
        assert snapshot(case) == current
    finally:
        leases.pause()


def test_admitted_unresolved_operation_retains_its_real_quota_and_unsettled_work(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    io._intent("gcs_bucket", data=None)
    withdraw(case, "key", monkeypatch)
    previous = snapshot(case)
    assert len(previous["slots"]) == 1
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_NEGATIVE_JOURNAL_REJECTED$"):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("damage", ["admitted", "error_unreaped", "extra_prior"])
def test_unproved_or_unreaped_journal_never_becomes_a_terminal_failure(parser_io_case, monkeypatch, damage):
    case, io, leases, delivery = parser_io_case
    owner, ids, _, _, _, _, _ = case
    io.read_and_parse()
    with owner.graph.transaction(ids["tenant"]):
        payload = owner.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], delivery.job_id))["payload"]
        if damage == "extra_prior":
            prior = copy.deepcopy(payload["attempt_intents"][0])
            prior.update(intent_id=str(uuid4()), delivery_generation=0, attempt=0, state="ERROR", process_state="UNKNOWN")
            payload["attempt_intents"].insert(0, prior)
        elif damage == "admitted":
            payload["attempt_intents"][-1]["state"] = "ADMITTED"
        else:
            payload["attempt_intents"][-1].update(state="ERROR", process_state="UNKNOWN", observed=None,
                error_class="NativeIORejected")
        assert owner.engine.execute("UPDATE workflow.jobs SET payload=?::jsonb WHERE tenant_id=? AND job_id=?",
            (canonical_json(payload), ids["tenant"], delivery.job_id)).rowcount == 1
    withdraw(case, "key", monkeypatch)
    previous = snapshot(case)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_NEGATIVE_JOURNAL_REJECTED$"):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("fence", [1, 2])
def test_queued_run_cannot_invent_an_accepted_unavailable_failure(parser_admission_case, monkeypatch, fence):
    case = parser_admission_case
    owner = case[0]
    _, delivery = claimed(case)
    withdraw(case, "key", monkeypatch)
    previous = snapshot(case)
    expected = "NATIVE_PARSER_NEGATIVE_ACCEPTED_OWNER_REQUIRED" if fence == 1 else "NATIVE_PARSER_BUSINESS_NOT_OWNED"
    with pytest.raises(JobFenceRejectedError, match="^" + expected + "$"):
        owner.settle_unavailable(delivery, business_fence=fence)
    assert snapshot(case) == previous


@pytest.mark.parametrize("damage,error_type,code", [
    ("fence", NativeParserConflict, "NATIVE_PARSER_TERMINAL_PROOF_REJECTED"),
    ("lease", JobFenceRejectedError, "NATIVE_PARSER_PHYSICAL_NOT_OWNED")])
def test_stale_physical_owner_cannot_close_an_unavailable_run(parser_io_case, monkeypatch, damage, error_type, code):
    case, _, leases, delivery = parser_io_case
    with case[2].connect() as connection:
        statement = ("UPDATE odp_runtime.durable_jobs SET fence_token=fence_token+1 WHERE job_id=%s" if damage == "fence"
            else "UPDATE odp_runtime.durable_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s")
        assert connection.execute(statement, (delivery.job_id,)).rowcount == 1
    withdraw(case, "key", monkeypatch)
    previous = snapshot(case)
    with pytest.raises(error_type, match="^" + code + "$"):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


def test_audit_failure_rolls_back_negative_run_jobs_events_and_receipt(parser_io_case, monkeypatch):
    case, _, leases, delivery = parser_io_case
    withdraw(case, "key", monkeypatch)
    previous = snapshot(case)
    def unavailable(*args, **kwargs):
        raise CanonicalAuditUnavailable("Synthetic negative parser audit unavailable")
    monkeypatch.setattr(case[0].graph.audit_sink, "persist", unavailable)
    with pytest.raises(CanonicalAuditUnavailable):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


def test_missing_key_does_not_turn_an_expired_root_into_negative_authority(parser_io_case, monkeypatch):
    case, _, leases, delivery = parser_io_case
    withdraw(case, "key", monkeypatch)
    previous = snapshot(case)
    original, entered = case[0]._confirm, []
    def expire(*args):
        before = args[-1]
        entered.append(before)
        before._monotonic_deadline = time.monotonic() - 1
        return original(*args)
    monkeypatch.setattr(case[0], "_confirm", expire)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
        settle(case, leases, delivery)
    assert len(entered) == 1 and snapshot(case) == previous


def test_unknown_successful_negative_commit_is_proved_without_duplicate_effects(parser_io_case, monkeypatch):
    case, _, leases, delivery = parser_io_case
    owner = case[0]
    withdraw(case, "key", monkeypatch)
    previous = snapshot(case)
    original, injected = owner.graph.transaction, []
    @contextmanager
    def uncertain(tenant):
        with original(tenant):
            yield
        if not injected and owner.queue.get(delivery.job_id).status == JobStatus.FAILED:
            injected.append(True)
            raise RuntimeError("Synthetic lost commit response")
    monkeypatch.setattr(owner.graph, "transaction", uncertain)
    assert settle(case, leases, delivery) is None and len(injected) == 1
    assert_failure(case, delivery, previous, operations=0)
    current = snapshot(case)
    assert settle(case, leases, delivery) is None and snapshot(case) == current


def test_actual_reaped_error_can_close_after_capability_loss_without_invented_output(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    original = io.storage.bucket_info
    def denied(*args, **kwargs):
        original(*args, **kwargs)
        withdraw(case, "key", monkeypatch)
        raise NativeIORejected("SYNTHETIC_OBSERVED_PARSER_READ_ERROR")
    monkeypatch.setattr(io.storage, "bucket_info", denied)
    with pytest.raises(NativeIORejected, match="^SYNTHETIC_OBSERVED_PARSER_READ_ERROR$"):
        io.read_and_parse()
    previous = snapshot(case)
    assert not previous["slots"]
    assert settle(case, leases, delivery) is None
    assert_failure(case, delivery, previous, operations=1, errors=1)


@pytest.fixture
def completed_unavailable(parser_io_case, monkeypatch):
    case, _, leases, delivery = parser_io_case
    withdraw(case, "key", monkeypatch)
    assert settle(case, leases, delivery) is None
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    assert case[0].replay(delivery) == {"terminal": True, "result": None}
    return case, leases, delivery


@pytest.mark.parametrize("damage", ["physical_status", "physical_version", "physical_fence", "physical_token",
    "business_version", "last_error", "lease_witness", "run_started", "run_payload", "receipt_cause",
    "receipt_quality_type", "audit_intake_flag", "parser_event", "deadletter_metadata"])
def test_negative_labels_cannot_replace_complete_historical_proof(completed_unavailable, damage):
    case, _, delivery = completed_unavailable
    owner, ids, database, _, _, _, _ = case
    with database.connect() as connection:
        job = connection.execute("SELECT aggregate_id,payload FROM workflow.jobs WHERE tenant_id=%s AND job_id=%s",
            (ids["tenant"], delivery.job_id)).fetchone()
        run_id, payload = job
        receipt = payload["negative_receipt"]
        if damage == "physical_status":
            changed = connection.execute("UPDATE odp_runtime.durable_jobs SET status='succeeded' WHERE job_id=%s", (delivery.job_id,))
        elif damage == "physical_version":
            changed = connection.execute("UPDATE odp_runtime.durable_jobs SET version=version+1 WHERE job_id=%s", (delivery.job_id,))
        elif damage == "physical_fence":
            changed = connection.execute("UPDATE odp_runtime.durable_jobs SET fence_token=fence_token+1 WHERE job_id=%s", (delivery.job_id,))
        elif damage == "physical_token":
            changed = connection.execute("UPDATE odp_runtime.durable_jobs SET payload_json=jsonb_set(payload_json,"
                "'{attempt_token}',to_jsonb(%s::text)) WHERE job_id=%s", ("1."+"f"*64, delivery.job_id))
        elif damage == "business_version":
            changed = connection.execute("UPDATE workflow.jobs SET version=version+1 WHERE tenant_id=%s AND job_id=%s",
                (ids["tenant"], delivery.job_id))
        elif damage == "last_error":
            changed = connection.execute("UPDATE workflow.jobs SET last_error='{}'::jsonb WHERE tenant_id=%s AND job_id=%s",
                (ids["tenant"], delivery.job_id))
        elif damage == "run_started":
            changed = connection.execute("UPDATE intake.parser_runs SET started_at=started_at-interval '1 second' "
                "WHERE parser_run_id=%s", (run_id,))
        elif damage == "run_payload":
            changed = connection.execute("UPDATE intake.parser_runs SET parsed_payload='{}'::jsonb WHERE parser_run_id=%s", (run_id,))
        elif damage in {"lease_witness", "receipt_cause", "receipt_quality_type"}:
            if damage == "lease_witness":
                receipt["business_lease_expires_at"] = receipt["settled_at"]
            elif damage == "receipt_cause":
                receipt["cause_code"] = "UNPROVED"
            else:
                receipt["quality_summary"]["output_committed"] = 0
            changed = connection.execute("UPDATE workflow.jobs SET payload=%s::jsonb WHERE tenant_id=%s AND job_id=%s",
                (canonical_json(payload), ids["tenant"], delivery.job_id))
        elif damage == "audit_intake_flag":
            changed = connection.execute("UPDATE audit.audit_events SET after_value=jsonb_set(after_value,"
                "'{intake_mutated}','0'::jsonb) WHERE tenant_id=%s AND audit_event_id=%s",
                (ids["tenant"], receipt["audit_event_id"]))
        elif damage == "parser_event":
            changed = connection.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,"
                "'{payload,quality_summary,output_committed}','0'::jsonb) WHERE tenant_id=%s AND event_id=%s",
                (ids["tenant"], receipt["parser_completed_event_id"]))
        else:
            changed = connection.execute("UPDATE workflow.outbox_events SET sensitive_fields='{}'::text[] "
                "WHERE tenant_id=%s AND event_id=%s", (ids["tenant"], receipt["dead_letter_event_id"]))
        assert changed.rowcount == 1
    previous = snapshot(case)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_TERMINAL_PROOF_REJECTED$"):
        owner.replay(delivery)
    assert snapshot(case) == previous


def test_later_independent_review_does_not_invalidate_a_negative_receipt(completed_unavailable):
    case, _, delivery = completed_unavailable
    owner, ids, database, result, _, _, _ = case
    with database.connect() as connection:
        assert connection.execute("UPDATE intake.human_corrections SET status='REJECTED',reviewed_by=%s,"
            "reviewed_at=clock_timestamp(),version=version+1 WHERE tenant_id=%s AND intake_id=%s",
            (ids["reviewer"], ids["tenant"], result["intake_id"])).rowcount > 0
    previous = snapshot(case)
    assert owner.replay(delivery) == {"terminal": True, "result": None}
    assert snapshot(case) == previous


def test_reintroduced_owned_reservation_blocks_negative_historical_discharge(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    owner, ids, database, _, _, _, _ = case
    io.read_and_parse()
    withdraw(case, "key", monkeypatch)
    assert settle(case, leases, delivery) is None
    with database.connect() as connection:
        payload = connection.execute("SELECT payload FROM workflow.jobs WHERE tenant_id=%s AND job_id=%s",
            (ids["tenant"], delivery.job_id)).fetchone()[0]
        reservation = payload["attempt_intents"][0]["reservation"]
        assert connection.execute("INSERT INTO intake.native_retrieval_inflight_slots "
            "(reservation_id,source_id,admitted_at,expires_at) VALUES (%s,%s,clock_timestamp(),"
            "clock_timestamp()+interval '1 hour')", (reservation["reservation_id"], reservation["source_id"])).rowcount == 1
    previous = snapshot(case)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_TERMINAL_PROOF_REJECTED$"):
        owner.replay(delivery)
    assert snapshot(case) == previous
