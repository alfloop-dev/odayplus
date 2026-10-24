"""Owned native SQLite audit readback, using the existing local Principal adapter.

These are native router/workflow/audit records, not signed-cookie BFF, PG or
browser acceptance. Refusal injections only affect newly owned test fixtures.
"""

from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.interventions import create_interventions_router
from modules.intervention.application.workflow import InterventionWorkflow
from modules.intervention.domain.lifecycle import InterventionStatus
from shared.audit.integrity import AuditIntegrityError
from shared.auth import Role
from tests.contract.test_intervention_approval_authority import (
    FOREIGN,
    REVIEWER,
    STORE,
    TENANT,
    headers,
    lifecycle_transport,
)
from tests.contract.test_intervention_approval_authority import (
    owned as inherited_owned,
)
from tests.contract.test_intervention_lifecycle_command_authority import (
    COMMANDS,
    invoke,
    ready,
    send,
    storage,
)

owned = inherited_owned


def execute(owned, command, extra=None, cid=None):
    _, client, _, _ = owned
    iid = ready(owned, command)
    body, hdr = lifecycle_transport(client, iid, command, {**COMMANDS[command], **(extra or {})})
    if cid is not None:
        hdr["x-correlation-id"] = cid
    response = send(client, iid, command, body, hdr)
    assert response.status_code == 200, response.text
    return iid, body, hdr, response.json()


def read(client, iid, cid, actor=REVIEWER, **header_values):
    response = client.get(f"/api/v1/interventions/{iid}",
        headers=headers(actor, **header_values), params={"lifecycle_correlation_id": cid})
    assert response.status_code == 200, response.text
    return response.json()


def audit(workflow, iid, cid):
    events = [event for event in workflow.audit_log.list_events(correlation_id=cid, tenant_id=TENANT)
        if event.event_type == "intervention.lifecycle.v1" and event.resource == f"intervention/{iid}"]
    assert len(events) == 1
    return events[0]


@pytest.mark.parametrize("command", list(COMMANDS))
def test_thirteen_native_target_receipts_read_back_the_exact_recorded_event(owned, command):
    engine, client, workflow, _ = owned
    iid, _, hdr, receipt = execute(owned, command)
    before = storage(engine, workflow)
    view = read(client, iid, hdr["x-correlation-id"])
    proof = view["lifecycle_audit"]
    event = audit(workflow, iid, hdr["x-correlation-id"])
    assert proof["event_id"] == receipt["lifecycle_audit_event_id"] == event.event_id
    assert proof["schema_version"] == 1 and proof["command"] == command
    assert proof["intervention_id"] == iid and proof["store_id"] == STORE and proof["tenant_id"] == TENANT
    assert proof["actor"] == proof["actor_subject_id"] == REVIEWER
    assert proof["actor_evidence"] == "authenticated_principal.v1"
    assert proof["event_type"] == event.event_type and proof["resource"] == event.resource
    assert proof["action"] == event.action and proof["outcome"] == event.outcome
    assert proof["reason"] == event.metadata["reason"]
    assert proof["policy_version"] == event.metadata["policy_version"]
    assert proof["occurred_at"] == event.occurred_at.isoformat()
    assert proof["status"] == event.metadata["status"]
    assert proof["transitions"] == [row.to_dict() for row in workflow.get(iid).history
        if row.correlation_id == hdr["x-correlation-id"]]
    assert len(proof["transitions"]) == receipt["version"] - receipt["previous_version"]
    assert not {"version", "previous_version", "metadata", "signature_key_id"} & set(proof)
    assert storage(engine, workflow) == before


def test_immature_then_mature_evaluation_each_retains_its_native_history_and_event(owned):
    _, client, workflow, _ = owned
    iid = ready(owned, "evaluate")
    records = []
    for now in ["2025-01-02T00:00:00+00:00", "2025-04-01T00:00:00+00:00"]:
        body, hdr = lifecycle_transport(client, iid, "evaluate", {**COMMANDS["evaluate"], "now": now})
        response = send(client, iid, "evaluate", body, hdr)
        assert response.status_code == 200, response.text
        records.append((hdr["x-correlation-id"], response.json()))
    immature = read(client, iid, records[0][0])["lifecycle_audit"]
    mature = read(client, iid, records[1][0])["lifecycle_audit"]
    assert immature["event_id"] == records[0][1]["audit_event_id"]
    assert mature["event_id"] == records[1][1]["audit_event_id"]
    assert [row["action"] for row in immature["transitions"]] == ["evaluate_effect"]
    assert [row["action"] for row in mature["transitions"]] == ["evaluate_effect", "complete"]
    assert immature["status"] == "EVALUATING" and mature["status"] == "COMPLETED"
    assert workflow.get(iid).status == InterventionStatus.COMPLETED


@pytest.mark.parametrize("command", ["close", "adjust"])
def test_linked_child_creation_is_separately_owned_and_does_not_replace_target_audit(owned, command):
    _, client, workflow, _ = owned
    iid, _, hdr, receipt = execute(owned, command, {"follow_up": True} if command == "close" else None)
    child_id = receipt["replacement"]["intervention_id"] if command == "adjust" else receipt["close"]["follow_up_intervention_id"]
    target = read(client, iid, hdr["x-correlation-id"])["lifecycle_audit"]
    child = read(client, child_id, hdr["x-correlation-id"])["lifecycle_audit"]
    assert target["event_id"] == receipt["lifecycle_audit_event_id"]
    assert child["event_id"] == audit(workflow, child_id, hdr["x-correlation-id"]).event_id
    assert target["event_id"] != child["event_id"]
    assert child["action"] == child["command"] == "create" and child["transitions"] == []
    assert child["store_id"] == target["store_id"] == STORE and child["actor"] == target["actor"]
    if command == "adjust":
        assert child["event_id"] == receipt["audit_event_id"]
        assert workflow.get(child_id).predecessor_id == iid
    else:
        assert workflow.get(child_id).trigger_ref == f"follow-up:{iid}"


@pytest.mark.parametrize("command", ["close", "adjust"])
def test_unpaired_child_creation_cannot_be_projected_as_verified_lineage(owned, command):
    _, client, workflow, _ = owned
    iid, _, hdr, receipt = execute(owned, command, {"follow_up": True} if command == "close" else None)
    child_id = receipt["replacement"]["intervention_id"] if command == "adjust" else receipt["close"]["follow_up_intervention_id"]
    parent = workflow.get(iid)
    changed = replace(parent, replacement_id="different-child") if command == "adjust" else replace(
        parent, close=replace(parent.close, follow_up_intervention_id="different-child"))
    workflow.repository._store.put(workflow.repository._C, iid, changed, group_key=STORE)
    assert read(client, child_id, hdr["x-correlation-id"])["lifecycle_audit"] is None


@pytest.mark.parametrize("command,extra", [
    ("assign", {"role": "execution label"}),
    ("eligibility", {"reasons": ["native eligibility A", "native eligibility B"]}),
    ("conflict-check", {"reason": "native reviewed coexistence"}),
    ("evaluate", {}),
    ("adjust", {"reason": "native adjust rationale"}),
])
def test_audit_reason_and_history_reason_keep_their_distinct_native_values(owned, command, extra):
    _, client, workflow, _ = owned
    iid, _, hdr, _ = execute(owned, command, extra)
    proof = read(client, iid, hdr["x-correlation-id"])["lifecycle_audit"]
    event = audit(workflow, iid, hdr["x-correlation-id"])
    assert proof["reason"] == event.metadata["reason"]
    assert proof["reason"] != proof["transitions"][0]["reason"]


@pytest.mark.parametrize("command", ["adjust", "close"])
def test_recorded_policy_is_not_fabricated_from_immutable_case_or_close_record(owned, command):
    _, client, workflow, _ = owned
    iid = ready(owned, command)
    original_policy = workflow.get(iid).policy_version
    workflow.policy_version = "synthetic-native-workflow-policy-v2"
    body, hdr = lifecycle_transport(client, iid, command, COMMANDS[command])
    response = send(client, iid, command, body, hdr)
    assert response.status_code == 200, response.text
    view = read(client, iid, hdr["x-correlation-id"])
    proof = view["lifecycle_audit"]
    assert view["policy_version"] == original_policy
    assert proof["policy_version"] == audit(workflow, iid, hdr["x-correlation-id"]).metadata["policy_version"]
    if command == "adjust":
        assert proof["policy_version"] == workflow.policy_version != view["policy_version"]
    else:
        assert proof["policy_version"] == original_policy != view["close"]["policy_version"]


def test_authorized_reader_may_differ_from_recorded_writer(owned):
    _, client, _, _ = owned
    iid, _, hdr, _ = execute(owned, "assign")
    view = read(client, iid, hdr["x-correlation-id"], actor="synthetic-distinct-reader",
        **{"x-roles": Role.REGIONAL_SUPERVISOR.value})
    assert view["review_context"]["actor_subject_id"] == "synthetic-distinct-reader"
    assert view["lifecycle_audit"]["actor"] == REVIEWER


@pytest.mark.parametrize("restriction", ["tenant", "store", "module", "role"])
def test_current_authority_denial_precedes_case_decode_and_audit_lookup(owned, monkeypatch, restriction):
    _, client, workflow, _ = owned
    iid, _, hdr, _ = execute(owned, "assign")
    denied = headers()
    denied[{"tenant": "x-tenant-id", "store": "x-store-ids", "module": "x-test-modules", "role": "x-roles"}[restriction]] = (
        FOREIGN if restriction == "tenant" else Role.MARKETING_MANAGER.value if restriction == "role" else "synthetic-outside-scope")
    decoded, lookups = [], []
    original_get, original_events = workflow.get, workflow.audit_log.list_events

    def observe_get(target):
        decoded.append(target)
        return original_get(target)

    def observe_events(**kw):
        lookups.append(kw)
        return original_events(**kw)

    monkeypatch.setattr(workflow, "get", observe_get)
    monkeypatch.setattr(workflow.audit_log, "list_events", observe_events)
    response = client.get(f"/api/v1/interventions/{iid}", headers=denied,
        params={"lifecycle_correlation_id": hdr["x-correlation-id"]})
    assert response.status_code == 403, response.text
    assert decoded == [] and lookups == []


@pytest.mark.parametrize("fault", [
    "missing", "duplicate", "event_type", "resource", "cid", "actor", "tenant", "store", "kind",
    "actor_subject", "legacy", "status", "reason_type", "policy_type", "policy_blank", "metadata_type", "time_type",
])
def test_missing_ambiguous_or_inconsistent_audit_cannot_confirm(owned, monkeypatch, fault):
    _, client, workflow, _ = owned
    iid, _, hdr, _ = execute(owned, "assign")
    cid = hdr["x-correlation-id"]
    event = audit(workflow, iid, cid)
    changed = event
    fields = {"event_type": "other.event", "resource": "intervention/other", "cid": "other-CID",
        "actor": "different-writer", "time_type": None}
    if fault in fields:
        changed = replace(event, **{{"cid": "correlation_id", "time_type": "occurred_at"}.get(fault, fault): fields[fault]})
    elif fault == "metadata_type":
        changed = replace(event, metadata=[])
    elif fault not in {"missing", "duplicate"}:
        key, value = {
            "tenant": ("tenant_id", FOREIGN), "store": ("store_id", "other-store"), "kind": ("kind", "CLEANING"),
            "actor_subject": ("actor_subject_id", "different-writer"), "legacy": ("actor_evidence", "unverified"),
            "status": ("status", "CLOSED"), "reason_type": ("reason", False),
            "policy_type": ("policy_version", []), "policy_blank": ("policy_version", " "),
        }[fault]
        changed = replace(event, metadata={**event.metadata, key: value})
    original = workflow.audit_log.list_events

    def selected(**kw):
        if kw.get("correlation_id") == cid:
            assert kw["tenant_id"] == TENANT
            return [] if fault == "missing" else [changed, changed] if fault == "duplicate" else [changed]
        return original(**kw)

    monkeypatch.setattr(workflow.audit_log, "list_events", selected)
    assert read(client, iid, cid)["lifecycle_audit"] is None


@pytest.mark.parametrize("fault", ["unavailable", "integrity"])
def test_requested_audit_storage_or_integrity_failure_fails_closed(owned, monkeypatch, fault):
    _, client, workflow, _ = owned
    iid, _, hdr, _ = execute(owned, "assign")
    original = workflow.audit_log.list_events

    def selected(**kw):
        if kw.get("correlation_id") == hdr["x-correlation-id"]:
            raise AuditIntegrityError("owned integrity failure") if fault == "integrity" else RuntimeError("owned store unavailable")
        return original(**kw)

    monkeypatch.setattr(workflow.audit_log, "list_events", selected)
    response = client.get(f"/api/v1/interventions/{iid}", headers=headers(),
        params={"lifecycle_correlation_id": hdr["x-correlation-id"]})
    assert response.status_code == 503 and response.json()["detail"] == "LIFECYCLE_AUDIT_READBACK_UNAVAILABLE"


@pytest.mark.parametrize("fault", ["action", "actor", "illegal_source", "broken_chain"])
def test_inconsistent_owned_history_does_not_become_audit_proof(owned, fault):
    _, client, workflow, _ = owned
    command = "execute" if fault == "broken_chain" else "eligibility"
    iid, _, hdr, _ = execute(owned, command)
    case = workflow.get(iid)
    rows = list(case.history)
    if fault == "action":
        rows[-1] = replace(rows[-1], action="assign")
    elif fault == "actor":
        rows[-1] = replace(rows[-1], actor="different-writer")
    elif fault == "illegal_source":
        rows[-1] = replace(rows[-1], from_status=InterventionStatus.PENDING_APPROVAL)
    else:
        rows[-1] = replace(rows[-1], from_status=InterventionStatus.APPROVED)
    workflow.repository._store.put(workflow.repository._C, iid, replace(case, history=tuple(rows)), group_key=STORE)
    assert read(client, iid, hdr["x-correlation-id"])["lifecycle_audit"] is None


def test_recorded_replay_readback_keeps_original_transport_and_no_new_business_effects(owned):
    engine, client, workflow, _ = owned
    iid, body, hdr, receipt = execute(owned, "assign")
    frozen_body, frozen_headers = dict(body), dict(hdr)
    before = storage(engine, workflow)
    replay = send(client, iid, "assign", body, hdr)
    assert replay.status_code == 200 and replay.json() == {**receipt, "idempotent_replay": True}
    proof = read(client, iid, hdr["x-correlation-id"])["lifecycle_audit"]
    assert proof["event_id"] == receipt["lifecycle_audit_event_id"]
    assert body == frozen_body and hdr == frozen_headers and storage(engine, workflow) == before


def test_audit_projection_keeps_historical_status_after_later_native_command(owned):
    _, client, _, _ = owned
    iid, _, hdr, receipt = execute(owned, "eligibility")
    invoke(client, iid, "action")
    view = read(client, iid, hdr["x-correlation-id"])
    assert view["status"] == "ACTION_PROPOSED" and view["version"] > receipt["version"]
    assert view["lifecycle_audit"]["status"] == "ELIGIBLE"
    assert view["lifecycle_audit"]["event_id"] == receipt["lifecycle_audit_event_id"]
    # The projection is historical proof, not a receipt/current-version match.


def test_no_query_response_and_hash_are_preserved_and_wrong_selector_is_not_authority(owned):
    _, client, _, _ = owned
    iid, _, hdr, _ = execute(owned, "assign")
    ordinary = client.get(f"/api/v1/interventions/{iid}", headers=headers())
    exact = client.get(f"/api/v1/interventions/{iid}", headers=headers(),
        params={"lifecycle_correlation_id": hdr["x-correlation-id"]})
    assert ordinary.status_code == exact.status_code == 200
    before, after = ordinary.json(), exact.json()
    assert "lifecycle_audit" not in before and after.pop("lifecycle_audit") is not None
    before.pop("read_at")
    after.pop("read_at")
    assert before == after and ordinary.headers["etag"] == exact.headers["etag"]
    assert read(client, iid, str(uuid4()))["lifecycle_audit"] is None


@pytest.mark.parametrize("cid", ["", " "])
def test_blank_selector_is_rejected(owned, cid):
    _, client, _, _ = owned
    iid = ready(owned, "assign")
    response = client.get(f"/api/v1/interventions/{iid}", headers=headers(), params={"lifecycle_correlation_id": cid})
    assert response.status_code == 422


def test_exact_nonblank_cid_keeps_spaces_and_reserved_characters(owned):
    _, client, _, _ = owned
    cid = "  owned CID /?&=%+ exact tail  "
    iid, _, _, receipt = execute(owned, "assign", cid=cid)
    assert read(client, iid, cid)["lifecycle_audit"]["event_id"] == receipt["lifecycle_audit_event_id"]
    assert read(client, iid, cid.strip())["lifecycle_audit"] is None


def test_exact_unicode_native_domain_cid_is_not_normalized_by_read_selector(owned):
    _, client, workflow, _ = owned
    iid = ready(owned, "assign")
    cid = "  原生é e\u0301 /?&=精確  "
    # Genuine native workflow+durable audit projection, with synthetic local
    # verified context; this is not a Unicode HTTP-header acceptance claim.
    with workflow.verified_command_context(tenant_id=TENANT, actor_subject_id=REVIEWER):
        workflow.assign_case(iid, assignee=REVIEWER, actor=REVIEWER, correlation_id=cid)
    proof = read(client, iid, cid)["lifecycle_audit"]
    assert proof["event_id"] == audit(workflow, iid, cid).event_id and proof["correlation_id"] == cid
    assert read(client, iid, cid.replace("e\u0301", "é"))["lifecycle_audit"] is None


def test_ordinary_unverified_case_never_claims_native_principal_audit_proof(owned):
    _, _, _, _ = owned
    from datetime import UTC, datetime, timedelta

    workflow = InterventionWorkflow()
    case = workflow.open_case(store_id=STORE, kind="PRICE_CHANGE", trigger_ref="ordinary", expected_outcome="ordinary",
        planned_start=datetime.now(UTC), planned_end=datetime.now(UTC) + timedelta(days=1), created_by="ordinary-body")
    workflow.assign_case(case.intervention_id, assignee="ordinary-body", actor="ordinary-body", correlation_id="ordinary-CID")
    app = FastAPI()

    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = "ordinary-read"
        return await call_next(request)

    app.include_router(create_interventions_router(workflow=workflow), prefix="/api/v1")
    with TestClient(app, raise_server_exceptions=False) as client:
        old = client.get(f"/api/v1/interventions/{case.intervention_id}", headers=headers())
        proof = client.get(f"/api/v1/interventions/{case.intervention_id}", headers=headers(),
            params={"lifecycle_correlation_id": "ordinary-CID"})
    assert old.status_code == proof.status_code == 200
    assert "lifecycle_audit" not in old.json() and proof.json()["lifecycle_audit"] is None
