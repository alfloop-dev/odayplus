"""Actual governed rechecks from native assisted waits, without source IO."""

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    assisted as assisted,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    command,
    snapshot,
    withdraw,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    past_manual as past_manual,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    recheck as recheck,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    start_case as start_case,
)
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import (
    waiting as waiting,
)
from tests.integration.test_native_intake_source_policy_postgresql import (
    drive_upstream,
    native_row,
    sql_patch,
)
from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
    drive,
)
from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
    request as authorization_request,
)

pytestmark = pytest.mark.requires_live_env


def request(case, version, key="synthetic-assisted-wait-governed-recheck-001"):
    recheck, _, _, _, result, _ = case
    start = recheck[0][0]
    return start[1][0].post(f'/api/v1/canonical-intakes/{result["intake_id"]}/source-policy-rechecks',
        headers={"Idempotency-Key": key, "If-Match": f'W/"{version}"'},
        json={"reason": "Re-evaluate the current synthetic manual-source permission"})


def complete(case, response):
    recheck, _, ids, _, _, _ = case
    owner, upstream = recheck[1], recheck[2]
    checked = drive(recheck, response)
    source = upstream[1]
    pending = list(source.admit_partition(ids["tenant"], limit=1))
    assert len(pending) == 1
    result = drive_upstream(upstream, pending[0], source)
    graph = recheck[0][0][0][0]
    assert owner.execute(graph.queue.get(checked["job_id"])) == checked
    return result, checked


@pytest.mark.parametrize("origin", ["AUTH_REQUIRED", "ASSISTED_ENTRY_ONLY"])
@pytest.mark.parametrize("outcome,target", [("manual", "AWAITING_ASSISTED_ENTRY"),
    ("account", "AWAITING_SOURCE_AUTHORIZATION"), ("retrieval", "RETRIEVING"), ("blocked", "QUARANTINED")])
def test_actual_partition_and_default_workers_reclassify_both_manual_wait_labels(assisted, origin, outcome, target):
    recheck, graph, ids, db, prior, _ = assisted
    upstream = recheck[2]
    version = prior["version"]
    if origin == "ASSISTED_ENTRY_ONLY":
        sql_patch(upstream, "retrieval_mode='ASSISTED_ENTRY_ONLY'")
        first = request(assisted, version, key="synthetic-enter-manual-only-wait-000")
        assert first.status_code == 202, first.text
        current, _ = complete(assisted, first)
        version = current["version"]
        assert current["policy_decision"]["effective_policy"] == origin
    if outcome == "account":
        with db.connect() as connection:
            connection.execute("UPDATE intake.source_registry SET retrieval_mode='AUTH_REQUIRED',assisted_entry_approval_ref=NULL,"
                "assisted_entry_approved_by=NULL,assisted_entry_review_expires_at=NULL,version=version+1 WHERE source_id='synthetic-feed'")
    elif outcome in {"retrieval", "blocked"}:
        sql_patch(upstream, "retrieval_mode='APPROVED_RETRIEVAL'" if outcome == "retrieval" else "kill_switch=true")
    response = request(assisted, version)
    assert response.status_code == 202, response.text
    with graph.transaction(ids["tenant"]):
        event = graph._one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?", (ids["tenant"], response.json()["request_id"]))["payload"]
        assert event["payload"]["waiting_state"] == "AWAITING_ASSISTED_ENTRY"
        assert event["payload"]["waiting_source_policy"] == origin
    assert native_row(recheck[0][0], prior["intake_id"])["version"] == version
    admitted = list(recheck[1].admit_partition(ids["tenant"], limit=1))
    assert len(admitted) == 1
    final, checked = complete(assisted, response)
    assert checked["version"] == version + 1 and final["version"] == version + 2 and final["processing_state"] == target


def test_pending_manual_request_blocks_recheck_then_exact_withdrawal_allows_account_wait(assisted):
    recheck, graph, _, db, prior, _ = assisted
    human = command(assisted)
    assert human.status_code == 202, human.text
    before = snapshot(assisted)
    assert request(assisted, prior["version"]).status_code == 409
    assert snapshot(assisted) == before
    cancelled = withdraw(assisted, human.json())
    assert cancelled.status_code == 200, cancelled.text
    with db.connect() as connection:
        connection.execute("UPDATE intake.source_registry SET assisted_entry_approval_ref=NULL,assisted_entry_approved_by=NULL,"
            "assisted_entry_review_expires_at=NULL,version=version+1 WHERE source_id='synthetic-feed'")
    response = request(assisted, prior["version"], key="synthetic-governed-after-manual-withdrawal-002")
    assert response.status_code == 202, response.text
    final, _ = complete(assisted, response)
    assert final["processing_state"] == "AWAITING_SOURCE_AUTHORIZATION"
    assert snapshot(assisted)["intake.human_corrections"] == before["intake.human_corrections"]


@pytest.mark.parametrize("field,value", [("waiting_state", "AWAITING_SOURCE_AUTHORIZATION"),
    ("waiting_source_policy", "ASSISTED_ENTRY_ONLY")])
def test_changed_waiting_context_is_not_a_signed_or_audited_recheck(assisted, field, value):
    recheck, graph, ids, _, prior, _ = assisted
    response = request(assisted, prior["version"])
    assert response.status_code == 202, response.text
    with graph.transaction(ids["tenant"]):
        graph.engine.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,?::text[],to_jsonb(?::text)) "
            "WHERE tenant_id=? AND event_id=?", ("{payload," + field + "}", value, ids["tenant"], response.json()["request_id"]))
    before = snapshot(assisted)
    with pytest.raises(ValueError):
        recheck[1].admit_event(ids["tenant"], response.json()["request_id"])
    assert snapshot(assisted) == before


def test_removing_both_modern_context_fields_cannot_downgrade_a_request_to_legacy(recheck):
    (start, prior), owner, _ = recheck
    graph, ids, _, _ = start[0]
    response = authorization_request(recheck)
    assert response.status_code == 202, response.text
    with graph.transaction(ids["tenant"]):
        graph.engine.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,'{payload}',"
            "(payload->'payload')-'waiting_state'-'waiting_source_policy') WHERE tenant_id=? AND event_id=?",
            (ids["tenant"], response.json()["request_id"]))
    before = native_row(start, prior["intake_id"])
    with pytest.raises(ValueError, match="NATIVE_RECHECK_REQUEST_WAITING_CONTEXT_NOT_AUDITED"):
        owner.admit_event(ids["tenant"], response.json()["request_id"])
    assert native_row(start, prior["intake_id"]) == before
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND aggregate_type='intake_policy_recheck'",
            (ids["tenant"],)) is None


def test_genuine_published_v1_request_and_audit_complete_with_the_current_owner(recheck, tmp_path):
    (start, prior), owner, upstream = recheck
    graph, ids, _, _ = start[0]
    published = "6e90cf17728d9bb29cfea6ee0a24fbd5df0e1ebd"
    path = "modules/listing/application/native_intake_source_policy_recheck.py"
    source = subprocess.check_output(["git", "show", published + ":" + path], cwd=Path(__file__).resolve().parents[2])
    module_path = tmp_path / "genuine_published_recheck_v1.py"
    module_path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("genuine_published_recheck_v1", module_path)
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    receipt = legacy.NativeIntakeSourcePolicyRecheckOwner(graph).request(start[1][1]["principal"], prior["intake_id"],
        expected_version=prior["version"], reason="Recheck the current synthetic legacy source permission",
        idempotency_key="synthetic-genuine-published-request-001")
    with graph.transaction(ids["tenant"]):
        event = graph._one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?",
            (ids["tenant"], receipt["request_id"]))["payload"]
        audit = graph._one("SELECT before_value,after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=?",
            (ids["tenant"], receipt["audit_event_id"]))
    assert "waiting_state" not in event["payload"] and "waiting_source_policy" not in event["payload"]
    after = json.loads(audit["after_value"]) if isinstance(audit["after_value"], str) else audit["after_value"]
    before = json.loads(audit["before_value"]) if isinstance(audit["before_value"], str) else audit["before_value"]
    assert "native_recheck_request_provenance" not in after
    assert before == {"processing_state": "AWAITING_SOURCE_AUTHORIZATION", "version": prior["version"]}
    checked = drive(recheck, SimpleNamespace(json=lambda: receipt))
    child = upstream[1].admit_event(ids["tenant"], checked["outbox_event_id"])
    final = drive_upstream(upstream, child, upstream[1])
    assert final["processing_state"] == "AWAITING_SOURCE_AUTHORIZATION" and final["version"] == prior["version"] + 2
    assert owner.execute(graph.queue.get(checked["job_id"])) == checked
