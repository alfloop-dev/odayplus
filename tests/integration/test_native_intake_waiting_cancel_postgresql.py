"""Cancel actual default-worker authorization waits with synthetic grants only."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

import pytest

from modules.listing.domain.intake_states import IntakeStage
from shared.audit.errors import CanonicalAuditUnavailable
from shared.auth import Role, Scope
from shared.domain.events import validate_event
from shared.infrastructure.persistence import PostgresDocumentStore
from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.jobs.queue import JobDeliveryState, JobStatus
from tests.integration.test_canonical_intakes_postgresql import snapshot
from tests.integration.test_native_intake_source_policy_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_source_policy_postgresql import (
    drive_upstream,
    native_row,
    prepare,
    sql_patch,
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


@pytest.fixture
def waiting(source_policy_case):
    identity, owner = source_policy_case
    start, _ = identity
    submission, _, admitted = prepare(source_policy_case)
    sql_patch(source_policy_case, "retrieval_mode='AUTH_REQUIRED'")
    result = drive_upstream(source_policy_case, admitted, owner)
    assert result["processing_state"] == "AWAITING_SOURCE_AUTHORIZATION"
    assert native_row(start, submission["intake_id"])["source_policy_state"] == "AUTH_REQUIRED"
    return start, result


def command(case, *, key="synthetic-native-auth-wait-cancel-001", version=None, reason="Stop this synthetic waiting request"):
    start, result = case
    version = result["version"] if version is None else version
    return (start[1][0], f'/api/v1/canonical-intakes/{result["intake_id"]}/cancel',
            {"Idempotency-Key": key, "If-Match": f'W/"{version}"'}, {"reason": reason})


def state(case):
    start, _ = case
    graph, ids, _, _ = start[0]
    result = snapshot(start[0])
    with graph.transaction(ids["tenant"]):
        result["source_registry"] = graph.engine.query("SELECT * FROM intake.source_registry ORDER BY source_id")
        for table in ("intake.source_snapshots", "intake.parser_runs", "intake.human_corrections", "expansion.promotion_decisions"):
            result[table] = sorted(graph.engine.query("SELECT * FROM " + table + " WHERE tenant_id=?", (ids["tenant"],)),
                                   key=lambda row: json.dumps(row, sort_keys=True))
    return result


@pytest.mark.parametrize("role", [Role.EXPANSION_USER, Role.SITE_REVIEWER, Role.DATA_OWNER])
def test_actual_wait_cancellation_is_once_only_durable_scoped_and_preserves_evidence(waiting, role):
    start, prior = waiting
    graph, ids, _, db = start[0]
    client, caller, _ = start[1]
    if role != Role.EXPANSION_USER:
        caller["principal"] = replace(caller["principal"], subject_id=ids["reviewer"], roles=frozenset({role}))
    _, path, headers, body = command(waiting)
    before_row, before = native_row(start, prior["intake_id"]), state(waiting)
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["processing_state"] == "CANCELLED" and receipt["version"] == prior["version"] + 1
    assert response.headers["etag"] == f'W/"{receipt["version"]}"'
    assert response.headers["Idempotency-Replayed"] == "false"
    assert not any(value in response.text for value in (body["reason"], "synthetic.example", "source_snapshot"))
    row, after = native_row(start, prior["intake_id"]), state(waiting)
    assert {k: v for k, v in row.items() if k not in {"version", "processing_state", "last_transition_at"}} == {
        k: v for k, v in before_row.items() if k not in {"version", "processing_state", "last_transition_at"}}
    for table in ("source_registry", "workflow.jobs", "identity.match_cases", "expansion.listings",
                  "intake.source_snapshots", "intake.parser_runs", "intake.human_corrections", "expansion.promotion_decisions"):
        assert after[table] == before[table], table
    assert [r for r in after["delivery"] if r["job_type"] != "canonical-audit-delivery"] == [
        r for r in before["delivery"] if r["job_type"] != "canonical-audit-delivery"]
    for table in ("intake.intake_stage_transitions", "audit.audit_events", "workflow.outbox_events"):
        assert len(after[table]) == len(before[table]) + 1, table
    transition = next(r for r in after["intake.intake_stage_transitions"] if r["transition_id"] == receipt["transition_id"])
    assert transition["actor_subject_id"] == caller["principal"].subject_id and transition["service_principal"] is None
    assert transition["permission"] == "intake.cancel" and transition["reason_text"] == body["reason"]
    assert transition["expected_version"] == prior["version"] and transition["resulting_version"] == receipt["version"]
    event = next(r for r in after["workflow.outbox_events"] if r["event_id"] == receipt["outbox_event_id"])["payload"]
    event = json.loads(event) if isinstance(event, str) else event
    assert validate_event(event) == [] and event["event_type"] == "intake.state_changed"
    assert event["actor_ref"] == caller["principal"].subject_id and event["aggregate_version"] == receipt["version"]
    assert event["payload"] == {"intake_id": prior["intake_id"], "from_state": "AWAITING_SOURCE_AUTHORIZATION",
        "to_state": "CANCELLED", "transition_id": receipt["transition_id"], "version": receipt["version"],
        "occurred_at": receipt["cancelled_at"]}
    audit = next(r for r in after["audit.audit_events"] if r["audit_event_id"] == receipt["audit_event_id"])
    audited_receipt = json.loads(audit["after_value"]) if isinstance(audit["after_value"], str) else audit["after_value"]
    assert audit["action"] == "intake.cancelled.v1"
    assert set(audited_receipt) == set(receipt) | {"audit_retention_policy"}
    assert {key: audited_receipt[key] for key in receipt} == receipt
    retention = audited_receipt["audit_retention_policy"]
    assert retention["policy_version_id"] == "synthetic-audit-v1:" + ids["tenant"]
    assert datetime.fromisoformat(retention["retained_until"]) > datetime.fromisoformat(receipt["cancelled_at"])
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok
    replay = client.post(path, headers=headers, json=body)
    assert replay.status_code == 200 and replay.json() == receipt and replay.headers["Idempotency-Replayed"] == "true"
    assert state(waiting) == after
    assert client.post(path, headers=headers, json={"reason": "Different synthetic intent"}).status_code == 409
    assert client.post(path, headers={**headers, "If-Match": 'W/"999"'}, json=body).status_code == 409
    assert client.post(path, headers={**headers, "Idempotency-Key": "synthetic-second-auth-wait-cancel"}, json=body).status_code == 409
    assert state(waiting) == after
    reopened = PostgresEngine(db.url(), bootstrap=False, validate_schema=False)
    try:
        store = DurableAssistedIntakeStore(PostgresDocumentStore(reopened))
        store.refresh(ids["tenant"])
        key = f'{ids["tenant"]}:{caller["principal"].subject_id}:cancelCanonicalIntakeAuthorizationWait:{prior["intake_id"]}:{headers["Idempotency-Key"]}'
        assert store.replays[key][1] == receipt
    finally:
        reopened.close()


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("revocation", ["anonymous", "role", "tenant", "scope", "ownership", "viewer"])
def test_current_authority_precedes_new_cancellation_and_lost_response_replay(waiting, replay, revocation):
    start, receipt = waiting
    graph, ids, _, _ = start[0]
    client, path, headers, body = command(waiting)
    if replay:
        assert client.post(path, headers=headers, json=body).status_code == 200
    caller = start[1][1]
    principal = caller["principal"]
    if revocation == "anonymous":
        caller["principal"] = replace(principal, authenticated=False)
    elif revocation in {"role", "viewer"}:
        caller["principal"] = replace(principal, roles=frozenset({Role.PLATFORM_ADMIN if revocation == "role" else Role.OPERATOR_VIEWER}))
    elif revocation == "tenant":
        caller["principal"] = replace(principal, scope=Scope(tenant_id=str(uuid4())))
    elif revocation == "scope":
        caller["principal"] = replace(principal, scope=Scope(tenant_id=ids["tenant"], region_ids=frozenset({str(uuid4())})))
    else:
        with graph.transaction(ids["tenant"]):
            graph.engine.execute("UPDATE intake.intakes SET submitter_subject_id=? WHERE intake_id=?",
                                 (str(uuid4()), receipt["intake_id"]))
    before = state(waiting)
    response = client.post(path, headers={**headers, "X-Operator-Role": "expansion-manager"}, json=body)
    assert response.status_code in {401, 403, 404}, response.text
    assert state(waiting) == before


@pytest.mark.parametrize("invalid", ["missing_key", "missing_version", "unquoted_version", "zero_version", "blank_reason", "credentials", "actor_override"])
def test_invalid_commands_are_redacted_and_write_nothing(waiting, invalid):
    client, path, headers, body = command(waiting)
    if invalid == "missing_key":
        headers.pop("Idempotency-Key")
    elif invalid == "missing_version":
        headers.pop("If-Match")
    elif invalid == "unquoted_version":
        headers["If-Match"] = "4"
    elif invalid == "zero_version":
        headers["If-Match"] = 'W/"0"'
    elif invalid == "blank_reason":
        body["reason"] = "   "
    elif invalid == "credentials":
        body["reason"] = "Cancel Authorization: Bearer synthetic-secret-credential"
    else:
        body["actor_subject_id"] = str(uuid4())
    before = state(waiting)
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 422, response.text
    assert "synthetic-secret-credential" not in response.text and "actor_subject_id" not in response.text
    assert state(waiting) == before


@pytest.mark.parametrize("stage", [s.value for s in IntakeStage if s != IntakeStage.AWAITING_SOURCE_AUTHORIZATION])
def test_command_cannot_cancel_another_native_phase(waiting, stage):
    start, prior = waiting
    graph, ids, _, _ = start[0]
    with graph.transaction(ids["tenant"]):
        graph.engine.execute("UPDATE intake.intakes SET processing_state=? WHERE intake_id=?", (stage, prior["intake_id"]))
    before = state(waiting)
    client, path, headers, body = command(waiting)
    assert client.post(path, headers=headers, json=body).status_code == 409
    assert state(waiting) == before


@pytest.mark.parametrize("defect", ["stale_version", "policy_state", "missing_delivery", "unbound_policy_job", "business_active", "business_failed", "physical_active", "physical_failed", "delivery_retry", "case", "promotion"])
def test_inconsistent_wait_or_downstream_work_blocks_cancellation(waiting, defect):
    start, prior = waiting
    graph, ids, _, _ = start[0]
    client, path, headers, body = command(waiting)
    with graph.transaction(ids["tenant"]):
        if defect == "stale_version":
            headers["If-Match"] = 'W/"999"'
        elif defect == "policy_state":
            graph.engine.execute("UPDATE intake.intakes SET source_policy_state='APPROVED_RETRIEVAL' WHERE intake_id=?", (prior["intake_id"],))
        elif defect == "missing_delivery":
            graph.engine.execute("DELETE FROM durable_jobs WHERE job_id=?", (prior["job_id"],))
        elif defect == "unbound_policy_job":
            graph.engine.execute("UPDATE workflow.jobs SET aggregate_id=? WHERE job_id=?", (str(uuid4()), prior["job_id"]))
        elif defect in {"business_active", "business_failed"}:
            graph.engine.execute("UPDATE workflow.jobs SET status=? WHERE job_id=?",
                                 ("RUNNING" if defect == "business_active" else "FAILED", prior["job_id"]))
        elif defect in {"physical_active", "physical_failed"}:
            graph.engine.execute("UPDATE durable_jobs SET status=? WHERE job_id=?",
                                 (JobStatus.RUNNING.value if defect == "physical_active" else JobStatus.FAILED.value, prior["job_id"]))
        elif defect == "delivery_retry":
            graph.engine.execute("UPDATE durable_jobs SET delivery_state=? WHERE job_id=?", (JobDeliveryState.RETRYING.value, prior["job_id"]))
        elif defect == "case":
            graph.engine.execute("UPDATE identity.match_cases SET intake_id=? WHERE match_case_id=?", (prior["intake_id"], ids["case"]))
        else:
            graph.engine.execute("INSERT INTO expansion.promotion_decisions (tenant_id,intake_id,listing_id,property_id,"
                "target_format_code,status,proposer_subject_id,reason,gate_snapshot) VALUES (?,?,?,?,'synthetic-format',"
                "'REQUESTED',?,'Synthetic conflicting promotion','{}')", (ids["tenant"], prior["intake_id"], ids["listing"], ids["source"], ids["actor"]))
    before = state(waiting)
    assert client.post(path, headers=headers, json=body).status_code == 409
    assert state(waiting) == before


@pytest.mark.parametrize("failure", ["audit", "outbox", "receipt"])
def test_failure_rolls_back_cancellation_transition_audit_event_and_receipt(waiting, monkeypatch, failure):
    start, _ = waiting
    graph = start[0][0]
    before = state(waiting)

    def unavailable(*args, **kwargs):
        raise CanonicalAuditUnavailable("Synthetic cancellation commit failure")

    if failure == "audit":
        monkeypatch.setattr(graph.audit_sink, "persist", unavailable)
    elif failure == "outbox":
        monkeypatch.setattr(type(graph), "_outbox", unavailable)
    else:
        monkeypatch.setattr(start[1][2], "flush", unavailable)
    client, path, headers, body = command(waiting)
    assert client.post(path, headers=headers, json=body).status_code == 503
    assert state(waiting) == before


def test_two_current_version_commands_commit_exactly_once(waiting):
    start, prior = waiting
    client, path, headers, body = command(waiting)
    before = state(waiting)

    def cancel(n):
        return client.post(path, headers={**headers, "Idempotency-Key": f"synthetic-concurrent-wait-cancel-{n}"}, json=body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(cancel, [1, 2]))
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    assert native_row(start, prior["intake_id"])["version"] == prior["version"] + 1
    after = state(waiting)
    for table in ("intake.intake_stage_transitions", "audit.audit_events", "workflow.outbox_events"):
        assert len(after[table]) == len(before[table]) + 1
