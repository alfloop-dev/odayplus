"""Actual permanent-failure SQL and fixed child; explicit synthetic GCS seam."""
import json
import time
from contextlib import contextmanager
from dataclasses import replace

import pytest
from fastapi import HTTPException

from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.external_data.application.native_parser_output import NativeParserOutputRejected
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_parser_policy import (
    NativeParserConflict,
    NativeParserUnavailable,
)
from modules.listing.application.native_intake_retrieval_policy import canonical_json
from shared.audit.errors import CanonicalAuditUnavailable
from shared.domain.events import validate_event
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus
from tests.integration.test_native_intake_parser_history_postgresql import (
    retire_all_positive_settings,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import (
    parser_evidence_fields as parser_evidence_fields,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import (
    parser_io_case as parser_io_case,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import (
    parser_realm as parser_realm,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import snapshot, withdraw
from tests.integration.test_native_intake_parser_unavailable_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import (
    start_case as start_case,
)

pytestmark = pytest.mark.requires_live_env


def observe_error(io, monkeypatch, kind="metadata"):
    if kind == "output":
        original = io.parser.parse
        def corrupt(*args, **kwargs):
            value = original(*args, **kwargs)
            return replace(value, facts=replace(value.facts, parsed_payload_json="{}"))
        monkeypatch.setattr(io.parser, "parse", corrupt)
    elif kind == "metadata":
        original = io.storage.head_version
        def corrupt(*args, **kwargs):
            return replace(original(*args, **kwargs), media_type="text/html")
        monkeypatch.setattr(io.storage, "head_version", corrupt)
    else:
        error = {"transient": NativeIORejected("NATIVE_IO_CONNECTION_FAILED"),
            "auth": NativeIORejected("NATIVE_GCS_PROVIDER_REJECTED", status=403),
            "unknown": RuntimeError("Synthetic private input"),
            "unreaped": NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED")}[kind]
        def corrupt(*args, **kwargs):
            raise error
        monkeypatch.setattr(io.storage, "bucket_info", corrupt)
    with pytest.raises((NativeIORejected, NativeParserOutputRejected, RuntimeError)):
        io.read_and_parse()


def settle(case, leases, delivery):
    return case[0].settle_permanent(delivery, leases=leases)


def assert_failure(case, delivery, previous, *, operations):
    owner, ids, _, _, _, _, _ = case
    current = snapshot(case)
    for table in ("intake.source_snapshots", "intake.human_corrections", "identity.match_cases",
            "expansion.listings", "storage_calls", "slots", "rates"):
        assert current[table] == previous[table], table
    target = current["intake.parser_runs"][0]["intake_id"]
    old = next(row for row in previous["intake.intakes"] if row["intake_id"] == target)
    new = next(row for row in current["intake.intakes"] if row["intake_id"] == target)
    assert [row for row in current["intake.intakes"] if row["intake_id"] != target] == [
        row for row in previous["intake.intakes"] if row["intake_id"] != target]
    assert new["processing_state"] == "FAILED" and new["version"] == old["version"]+1
    assert {k:v for k,v in new.items() if k not in {"processing_state","version","last_transition_at"}} == {
        k:v for k,v in old.items() if k not in {"processing_state","version","last_transition_at"}}
    run = current["intake.parser_runs"][0]
    assert len(current["intake.parser_runs"]) == len(previous["intake.parser_runs"]) == 1
    assert run["status"] == "FAILED" and run["parsed_payload"] is None and run["normalized_payload"] is None
    assert run["started_at"] == previous["intake.parser_runs"][0]["started_at"]
    assert json.loads(run["field_confidence"]) == {} and json.loads(run["validation_errors"]) == []
    with owner.graph.transaction(ids["tenant"]):
        job = owner.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], delivery.job_id))
        receipt = job["payload"]["permanent_failure_receipt"]
        assert job["status"] == "FAILED" and job["result"] is None
        assert job["lease_expires_at"] is None and job["next_attempt_at"] is None
        assert job["version"] == receipt["business_version"] and run["version"] == receipt["run_version"]
        physical = owner.queue.get(delivery.job_id)
        assert physical.status == JobStatus.FAILED and physical.delivery_state == JobDeliveryState.DEAD_LETTER
        assert physical.version == receipt["physical_version"] and physical.attempts == receipt["physical_attempts"]
        assert physical.locked_by is None and physical.lease_expires_at is None
    assert receipt["quality_summary"] == {"recorded_operations": operations, "finished_operations": operations-1,
        "error_operations": 1, "reaped_operations": operations, "unresolved_operations": 0, "output_committed": False}
    transitions = current["control_transitions"]
    assert len(transitions) == len(previous["control_transitions"])+1
    assert transitions[-1]["from_state"] == "PARSING" and transitions[-1]["to_state"] == "FAILED"
    assert transitions[-1]["transition_id"] == receipt["transition_id"]
    added = [r for r in current["workflow.outbox_events"]
        if r["event_id"] not in {p["event_id"] for p in previous["workflow.outbox_events"]}]
    assert len(added) == 3 and {r["event_type"] for r in added} == {
        "parser.run_completed", "intake.state_changed", "job.dead_lettered"}
    for row in added:
        event = json.loads(row["payload"])
        assert validate_event(event) == [] and row["causation_id"] == event["causation_id"]
        assert json.loads(row["sensitive_fields"]) == event["sensitive_fields"]
    assert owner.graph.audit_sink.writer.verify_chain(ids["tenant"]).ok
    return receipt


@pytest.mark.parametrize("kind,operations", [("metadata", 2), ("output", 4)])
def test_actual_reaped_permanent_defect_atomically_fails_intake_run_and_both_jobs(parser_io_case, monkeypatch, kind, operations):
    case, io, leases, delivery = parser_io_case
    observe_error(io, monkeypatch, kind)
    previous = snapshot(case)
    assert settle(case, leases, delivery) is None
    receipt = assert_failure(case, delivery, previous, operations=operations)
    assert receipt["failure_observation"]["family"] == (
        "NativeParserOutputRejected" if kind == "output" else "NativeIORejected")
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    current = snapshot(case)
    assert case[0].replay(delivery) == {"terminal": True, "result": None}
    assert settle(case, leases, delivery) is None and snapshot(case) == current


@pytest.mark.parametrize("kind", ["transient", "auth", "unknown"])
def test_other_error_families_cannot_be_promoted_to_permanent_failure(parser_io_case, monkeypatch, kind):
    case, io, leases, delivery = parser_io_case
    observe_error(io, monkeypatch, kind)
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_OBSERVED_PERMANENT_FAILURE_REQUIRED$"):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("stage", ["empty", "healthy", "admitted", "unreaped"])
def test_missing_or_unresolved_failure_cannot_change_intake(parser_io_case, monkeypatch, stage):
    case, io, leases, delivery = parser_io_case
    if stage == "healthy":
        io.read_and_parse()
    elif stage == "admitted":
        io._intent("gcs_bucket", data=None)
    elif stage == "unreaped":
        observe_error(io, monkeypatch, "unreaped")
    previous = snapshot(case)
    with pytest.raises((NativeParserConflict, JobFenceRejectedError)):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("loss", ["key", "human", "source", "registry"])
def test_permanent_error_does_not_authorize_an_intake_write_after_authority_loss(parser_io_case, monkeypatch, loss):
    case, io, leases, delivery = parser_io_case
    observe_error(io, monkeypatch)
    withdraw(case, loss, monkeypatch)
    previous = snapshot(case)
    with pytest.raises((NativeParserUnavailable, HTTPException, NativeAssistedEntryConflict)):
        settle(case, leases, delivery)
    assert snapshot(case) == previous
    assert case[0].settle_unavailable(delivery, business_fence=leases.business_fence, leases=leases) is None
    assert snapshot(case)["intake.intakes"] == previous["intake.intakes"]
    assert case[0].replay(delivery) == {"terminal": True, "result": None}


def test_late_authority_loss_rolls_back_the_permanent_failure_atomically(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    observe_error(io, monkeypatch)
    previous = snapshot(case)
    original = case[0]._confirm
    def revoke(*args):
        withdraw(case, "key", monkeypatch)
        return original(*args)
    monkeypatch.setattr(case[0], "_confirm", revoke)
    with pytest.raises(NativeParserUnavailable):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


def test_audit_failure_rolls_back_permanent_stage_run_jobs_and_events(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    observe_error(io, monkeypatch)
    previous = snapshot(case)
    original = case[0].graph.audit_sink.persist
    def fail(event, scope):
        if event["action"] == "intake.processing_failed.v1":
            raise CanonicalAuditUnavailable("Synthetic scoped audit unavailable")
        return original(event, scope)
    monkeypatch.setattr(case[0].graph.audit_sink, "persist", fail)
    with pytest.raises(CanonicalAuditUnavailable):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


def test_expired_original_root_cannot_be_reclassified_as_a_permanent_defect(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    observe_error(io, monkeypatch)
    previous = snapshot(case)
    original = case[0]._confirm
    def expire(*args):
        args[-1]._monotonic_deadline = time.monotonic()-1
        return original(*args)
    monkeypatch.setattr(case[0], "_confirm", expire)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


def test_unknown_successful_permanent_commit_is_proved_without_duplicate_effects(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    owner = case[0]
    observe_error(io, monkeypatch)
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
    assert_failure(case, delivery, previous, operations=2)
    current = snapshot(case)
    assert settle(case, leases, delivery) is None and snapshot(case) == current


@pytest.fixture
def completed_permanent(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    observe_error(io, monkeypatch)
    assert settle(case, leases, delivery) is None
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    assert case[0].replay(delivery) == {"terminal": True, "result": None}
    return case, leases, delivery


@pytest.mark.parametrize("damage", ["physical_status", "business_version", "run_started", "run_payload",
    "receipt_quality_type", "failure_code", "journal_code", "failure_version_type", "audit_intake_flag",
    "transition_reason", "state_event", "parser_event", "deadletter_metadata"])
def test_permanent_labels_cannot_replace_complete_historical_proof(completed_permanent, damage):
    case, _, delivery = completed_permanent
    owner, ids, database, _, _, _, _ = case
    with database.connect() as connection:
        run_id, payload = connection.execute("SELECT aggregate_id,payload FROM workflow.jobs WHERE tenant_id=%s AND job_id=%s",
            (ids["tenant"], delivery.job_id)).fetchone()
        receipt = payload["permanent_failure_receipt"]
        if damage == "physical_status":
            changed = connection.execute("UPDATE odp_runtime.durable_jobs SET status='succeeded' WHERE job_id=%s", (delivery.job_id,))
        elif damage == "business_version":
            changed = connection.execute("UPDATE workflow.jobs SET version=version+1 WHERE tenant_id=%s AND job_id=%s",
                (ids["tenant"], delivery.job_id))
        elif damage == "run_started":
            changed = connection.execute("UPDATE intake.parser_runs SET started_at=started_at-interval '1 second' "
                "WHERE parser_run_id=%s", (run_id,))
        elif damage == "run_payload":
            changed = connection.execute("UPDATE intake.parser_runs SET parsed_payload='{}'::jsonb WHERE parser_run_id=%s", (run_id,))
        elif damage in {"receipt_quality_type", "failure_code", "journal_code", "failure_version_type"}:
            if damage == "receipt_quality_type":
                receipt["quality_summary"]["output_committed"] = 0
            elif damage == "failure_code":
                receipt["failure_observation"]["code"] = "NATIVE_IO_CONNECTION_FAILED"
            elif damage == "journal_code":
                payload["attempt_intents"][-1]["failure_observation"]["code"] = "NATIVE_IO_CONNECTION_FAILED"
            else:
                receipt["failure_observation"]["version"] = True
            changed = connection.execute("UPDATE workflow.jobs SET payload=%s::jsonb WHERE tenant_id=%s AND job_id=%s",
                (canonical_json(payload), ids["tenant"], delivery.job_id))
        elif damage == "audit_intake_flag":
            changed = connection.execute("UPDATE audit.audit_events SET after_value=jsonb_set(after_value,"
                "'{intake_mutated}','1'::jsonb) WHERE tenant_id=%s AND audit_event_id=%s",
                (ids["tenant"], receipt["intake_audit_event_id"]))
        elif damage == "transition_reason":
            changed = connection.execute("UPDATE intake.intake_stage_transitions SET reason_code='UNPROVED' WHERE transition_id=%s",
                (receipt["transition_id"],))
        elif damage == "state_event":
            changed = connection.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,"
                "'{payload,to_state}','\"PARSING\"'::jsonb) WHERE tenant_id=%s AND event_id=%s",
                (ids["tenant"], receipt["outbox_event_id"]))
        elif damage == "parser_event":
            changed = connection.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,"
                "'{payload,quality_summary,output_committed}','0'::jsonb) WHERE tenant_id=%s AND event_id=%s",
                (ids["tenant"], receipt["parser_completed_event_id"]))
        else:
            changed = connection.execute("UPDATE workflow.outbox_events SET sensitive_fields='{}'::text[] WHERE tenant_id=%s AND event_id=%s",
                (ids["tenant"], receipt["dead_letter_event_id"]))
        assert changed.rowcount == 1
    previous = snapshot(case)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_TERMINAL_PROOF_REJECTED$"):
        owner.replay(delivery)
    assert snapshot(case) == previous


def test_later_independent_correction_review_preserves_permanent_history(completed_permanent):
    case, _, delivery = completed_permanent
    owner, ids, database, original, _, _, _ = case
    with database.connect() as connection:
        assert connection.execute("UPDATE intake.human_corrections SET status='REJECTED',reviewed_by=%s,"
            "reviewed_at=clock_timestamp(),version=version+1 WHERE tenant_id=%s AND intake_id=%s",
            (ids["reviewer"], ids["tenant"], original["intake_id"])).rowcount > 0
    previous = snapshot(case)
    assert owner.replay(delivery) == {"terminal": True, "result": None}
    assert snapshot(case) == previous
