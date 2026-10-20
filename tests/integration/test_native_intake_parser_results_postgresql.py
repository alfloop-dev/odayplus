"""Actual terminal output SQL/child with explicit synthetic GCS; no release proof."""
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from modules.external_data.application.native_intake_process import NativeIORejected
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import _object
from modules.listing.application.native_intake_parser_policy import (
    SIGNING_KEY_ENV,
    NativeParserUnavailable,
)
from modules.listing.application.native_intake_parser_results import commit_parser_output
from modules.listing.application.native_intake_retrieval_policy import canonical_json
from shared.audit.errors import CanonicalAuditUnavailable
from shared.domain.events import validate_event
from shared.jobs.queue import JobFenceRejectedError, JobStatus
from tests.integration.test_native_intake_parser_io_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    parser_evidence_fields as parser_evidence_fields,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    parser_io_case as parser_io_case,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    parser_realm as parser_realm,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    persisted,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    start_case as start_case,
)

pytestmark = pytest.mark.requires_live_env


@pytest.mark.parametrize("parser_evidence_fields,status,target", [
    (None, "SUCCEEDED", "MATCHING"), ("partial", "PARTIAL", "NEEDS_REVIEW")],
    indirect=["parser_evidence_fields"])
def test_actual_parser_output_atomically_settles_run_jobs_and_exact_events(parser_io_case, status, target):
    case, io, leases, delivery = parser_io_case
    owner, ids, _, _, _, _, _ = case
    observed = io.read_and_parse()
    original = persisted(case)
    result = commit_parser_output(owner, delivery, leases, observed)
    assert result["parser_status"] == status and result["job_status"] == "SUCCEEDED"
    assert result["processing_state"] == target
    current = persisted(case)
    for table in ("intake.source_snapshots", "intake.human_corrections", "identity.match_cases",
            "expansion.listings", "storage_calls"):
        assert current[table] == original[table], table
    assert len(current["intake.parser_runs"]) == len(original["intake.parser_runs"]) == 1
    run = current["intake.parser_runs"][0]
    assert run["status"] == status and run["version"] == result["run_version"]
    assert run["started_at"] == original["intake.parser_runs"][0]["started_at"]
    assert run["source_snapshot_id"] == observed.source_snapshot_id
    assert {key: _object(run[key]) for key in ("parsed_payload", "normalized_payload", "field_confidence")} == {
        key: value for key, value in observed.output.facts.payloads().items() if key != "validation_errors"}
    errors = run["validation_errors"]
    assert (json.loads(errors) if isinstance(errors, str) else errors) == observed.output.facts.payloads()["validation_errors"]
    assert _object(run["field_confidence"]) == {}
    with owner.graph.transaction(ids["tenant"]):
        job = owner.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], delivery.job_id))
        assert job["status"] == "SUCCEEDED" and job["result"] == result
        assert job["payload"]["terminal_receipt"] == result and job["lease_expires_at"] is None
        assert job["timeout_at"] == next(row["timeout_at"] for row in original["workflow.jobs"]
            if row["job_id"] == delivery.job_id)
        assert owner.queue.get(delivery.job_id).status == JobStatus.SUCCEEDED
        intake = owner.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=?",
            (ids["tenant"], result["intake_id"]))
        assert intake["processing_state"] == target and intake["version"] == result["version"]
        transition = owner.graph._one("SELECT * FROM intake.intake_stage_transitions WHERE tenant_id=? AND transition_id=?",
            (ids["tenant"], result["transition_id"]))
        assert transition["from_state"] == "PARSING" and transition["to_state"] == target
        assert transition["actor_subject_id"] is None and transition["service_principal"] == delivery.job_type
        assert transition["job_id"] == delivery.job_id and transition["source_snapshot_id"] == observed.source_snapshot_id
        release = owner.graph._one("SELECT output_schema_version FROM intake.parser_releases WHERE parser_release_id=?",
            (result["parser_release_id"],))
    prior_events = {row["event_id"] for row in original["workflow.outbox_events"]}
    added = [row for row in current["workflow.outbox_events"] if row["event_id"] not in prior_events]
    assert len(added) == 2 and {row["event_type"] for row in added} == {"parser.run_completed", "intake.state_changed"}
    for row in added:
        envelope = _object(row["payload"])
        assert row["causation_id"] == envelope["causation_id"]
        sensitive = row["sensitive_fields"]
        assert (json.loads(sensitive) if isinstance(sensitive, str) else sensitive) == envelope["sensitive_fields"] == []
    completed = next(_object(row["payload"]) for row in added if row["event_type"] == "parser.run_completed")
    assert validate_event(completed) == []
    assert completed["producer"] == "parser_service" and completed["aggregate_type"] == "parser_run"
    assert completed["aggregate_id"] == result["parser_run_id"] and completed["sensitive_fields"] == []
    assert completed["partition_key"] == ids["tenant"] + ":" + result["intake_id"]
    assert completed["payload"] == {"parser_run_id": result["parser_run_id"], "intake_id": result["intake_id"],
        "parser_release_id": result["parser_release_id"], "status": status,
        "quality_summary": observed.output.quality_summary(), "output_schema_version": release["output_schema_version"]}
    assert owner.graph.audit_sink.writer.verify_chain(ids["tenant"]).ok
    assert leases._thread is None and leases._proof is None


def test_conflicting_outbox_cause_rolls_back_run_jobs_events_audits_and_intake(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    owner, ids, _, _, _, _, _ = case
    observed = io.read_and_parse()
    previous = persisted(case)
    original = owner.graph._outbox

    def conflict(event):
        original(event)
        if event.event_type == "parser.run_completed":
            assert owner.engine.execute("UPDATE workflow.outbox_events SET causation_id=? "
                "WHERE tenant_id=? AND event_id=?", (str(uuid4()), ids["tenant"], event.event_id)).rowcount == 1

    monkeypatch.setattr(owner.graph, "_outbox", conflict)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_OUTPUT_OUTBOX_METADATA_REJECTED$"):
        commit_parser_output(owner, delivery, leases, observed)
    assert persisted(case) == previous


@pytest.mark.parametrize("damage", ["unresolved", "child_fence", "download_digest", "observation_ids"])
def test_unresolved_or_substituted_output_journal_cannot_commit_positive_facts(parser_io_case, damage):
    case, io, leases, delivery = parser_io_case
    owner, ids, _, _, _, _, _ = case
    observed = io.read_and_parse()
    if damage == "observation_ids":
        observed = replace(observed, intent_ids=(observed.intent_ids[0],) * 4)
    else:
        with owner.graph.transaction(ids["tenant"]):
            job = owner.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                (ids["tenant"], delivery.job_id))
            payload = job["payload"]
            if damage == "unresolved":
                payload["attempt_intents"][-1]["state"] = "ADMITTED"
            elif damage == "child_fence":
                payload["attempt_intents"][-1]["physical_fence"] += 1
            else:
                payload["attempt_intents"][-2]["observed"]["sha256"] = "f" * 64
            owner.engine.execute("UPDATE workflow.jobs SET payload=?::jsonb WHERE tenant_id=? AND job_id=?",
                (canonical_json(payload), ids["tenant"], delivery.job_id))
    original = persisted(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_OUTPUT_JOURNAL_REJECTED$"):
        commit_parser_output(owner, delivery, leases, observed)
    assert persisted(case) == original


@pytest.mark.parametrize("loss", ["key", "human", "source", "registry"])
def test_authority_loss_after_real_child_does_not_commit_output_or_intake_change(parser_io_case, monkeypatch, loss):
    case, io, leases, delivery = parser_io_case
    owner, ids, database, _, _, _, _ = case
    observed = io.read_and_parse()
    if loss == "key":
        monkeypatch.delenv(SIGNING_KEY_ENV)
    elif loss == "registry":
        owner.artifact_registry = None
    else:
        with database.connect() as connection:
            if loss == "human":
                connection.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (ids["actor"],))
            else:
                connection.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id LIKE 'native-parser-%'")
    original = persisted(case)
    with pytest.raises((NativeParserUnavailable, NativeIORejected, NativeAssistedEntryConflict, PermissionError, HTTPException)):
        commit_parser_output(owner, delivery, leases, observed)
    assert persisted(case) == original


@pytest.mark.parametrize("fault", ["audit_failure", "key_loss_after_first_audit"])
def test_terminal_audit_or_final_authority_failure_rolls_back_every_positive_write(parser_io_case, monkeypatch, fault):
    case, io, leases, delivery = parser_io_case
    owner = case[0]
    observed = io.read_and_parse()
    original = persisted(case)
    persist, calls = owner.graph.audit_sink.persist, []

    def broken(*args, **kwargs):
        if fault == "audit_failure":
            raise CanonicalAuditUnavailable("Synthetic terminal audit unavailable")
        result = persist(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            monkeypatch.delenv(SIGNING_KEY_ENV)
        return result

    monkeypatch.setattr(owner.graph.audit_sink, "persist", broken)
    expected = CanonicalAuditUnavailable if fault == "audit_failure" else NativeParserUnavailable
    with pytest.raises(expected):
        commit_parser_output(owner, delivery, leases, observed)
    assert persisted(case) == original


@pytest.mark.parametrize("loss", ["fence", "lease"])
def test_physical_owner_loss_after_reaped_child_cannot_commit_any_terminal_fact(parser_io_case, loss):
    case, io, leases, delivery = parser_io_case
    owner, ids, _, _, _, _, _ = case
    observed = io.read_and_parse()
    with owner.graph.transaction(ids["tenant"]):
        if loss == "fence":
            owner.engine.execute("UPDATE durable_jobs SET fence_token=fence_token+1 WHERE job_id=?", (delivery.job_id,))
        else:
            owner.engine.execute("UPDATE durable_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                "WHERE job_id=?", (delivery.job_id,))
    original = persisted(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_PHYSICAL_NOT_OWNED$"):
        commit_parser_output(owner, delivery, leases, observed)
    assert persisted(case) == original
