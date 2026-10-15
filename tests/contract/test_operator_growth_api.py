"""Contract tests for the Growth workspace R4 API (ODP-OC-R4-004).

Proves the four task acceptance criteria at the HTTP contract layer:

  1. All three entry cards (offpeak / winback / priceops) prefill and persist
     the correct draft type.
  2. Blocked conflict states cannot submit and return actionable server reasons.
  3. Approval creates a Govern item and the approval result advances the
     Growth state.
  4. Effective, ineffective and inconclusive outcomes persist and write a
     Decision Log entry plus an Audit Trail event.

Plus the cross-cutting R4 contract: Idempotency-Key de-duplication,
X-Correlation-Id round-trip, and fail-closed authorization on write routes.

Verification command (per task brief):
  uv run pytest tests/contract -k growth

Owner: Claude (ODP-OC-R4-004)
Reviewer: Antigravity6
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app

BASE = "/api/v1/operator/growth"

# operations_manager carries intervention.CREATE + intervention.APPROVE.
WRITE_HEADERS = {
    "X-Subject-Id": "test-growth-lead",
    "X-Roles": "operations_manager",
    "X-Tenant-Id": "tenant-a",
}


def _client() -> TestClient:
    """Fresh app (hence fresh GrowthService) per test for isolation."""
    return TestClient(create_app(external_provider_validation=lambda: None))


def _headers(idem: str | None = None, correlation: str = "corr-growth-test") -> dict[str, str]:
    hdr = {**WRITE_HEADERS, "X-Correlation-Id": correlation}
    if idem is not None:
        hdr["Idempotency-Key"] = idem
    return hdr


def _execution_headers(idem: str | None = None) -> dict[str, str]:
    return {**_headers(idem=idem), "X-Roles": "regional_supervisor", "X-Subject-Id": "synthetic-growth-executor"}


@pytest.mark.parametrize("rationale", ["", "   ", None])
def test_growth_outcome_requires_human_reason_without_mutating_record(rationale):
    client = _client()
    path = f"{BASE}/actions/growth-7001"
    read_headers = _headers(correlation="synthetic-fixed-read")
    before = client.get(path, headers=read_headers).json()
    decisions = client.get(f"{BASE}/decisions", headers=read_headers).json()
    body = {"outcome": "EFFECTIVE", "requiredAction": "CLOSE"}
    if rationale is not None:
        body["rationale"] = rationale
    response = client.post(path + "/outcome", json=body, headers=_headers(idem="synthetic-empty-reason"))
    assert response.status_code == 422, response.text
    assert client.get(path, headers=read_headers).json() == before
    assert client.get(f"{BASE}/decisions", headers=read_headers).json() == decisions


def test_growth_outcome_replay_is_actor_and_request_bound_and_ignores_body_actor():
    client = _client()
    path = f"{BASE}/actions/growth-7001/outcome"
    body = {"outcome": "EFFECTIVE", "requiredAction": "CLOSE", "rationale": "Synthetic independent review", "actorName": "forged-other", "actorRoleId": "forged-role"}
    headers = _headers(idem="synthetic-bound-outcome")
    first = client.post(path, json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["auditEvent"]["actorName"] == WRITE_HEADERS["X-Subject-Id"]
    assert first.json()["auditEvent"]["actorRoleId"] != "forged-role"
    assert first.json()["decision"]["reason"] == body["rationale"]
    replay = client.post(path, json=body, headers=headers)
    assert replay.status_code == 200 and replay.json()["idempotentReplay"]
    assert replay.json()["auditEvent"]["id"] == first.json()["auditEvent"]["id"]
    changed = client.post(path, json={**body, "rationale": "Changed review"}, headers=headers)
    assert changed.status_code == 409, changed.text
    different_record = client.post(f"{BASE}/actions/growth-7003/outcome", json=body, headers=headers)
    assert different_record.status_code == 409, different_record.text
    other = client.post(path, json=body, headers={**headers, "X-Subject-Id": "synthetic-other-reviewer"})
    assert other.status_code == 200, other.text
    assert other.json()["idempotentReplay"] is False
    assert other.json()["auditEvent"]["actorName"] == "synthetic-other-reviewer"
    denied = client.post(path, json=body, headers={**headers, "X-Roles": "auditor"})
    assert denied.status_code == 403, denied.text


def test_growth_outcome_does_not_replay_an_unbound_legacy_receipt():
    from copy import deepcopy

    from modules.opsboard.application.growth import GrowthConflict, GrowthService

    service = GrowthService()
    service._state["idempotencyResults"]["synthetic-legacy"] = {"id": "unattributable-receipt"}
    before = deepcopy(service.export_state())
    with pytest.raises(GrowthConflict, match="UNBOUND_LEGACY_REPLAY"):
        service.write_outcome(action_id="growth-7001", outcome="EFFECTIVE", required_action="CLOSE", rationale="Synthetic review", actor_name="synthetic-reviewer", tenant_id="tenant-a", idempotency_key="synthetic-legacy")
    assert service.export_state() == before


def _create_draft(
    client: TestClient,
    *,
    kind: str,
    name: str = "測試活動",
    store: str = "Oday 松仁店",
    window: str = "平日 10:00-14:00",
    channel: str = "App 首頁",
    budget: float = 5000,
    idem: str | None = None,
) -> dict:
    resp = client.post(
        f"{BASE}/actions",
        json={
            "name": name,
            "segmentId": "seg-metro-dinner",
            "objective": "提升離峰利用率",
            "targetLift": 2.0,
            "kind": kind,
            "store": store,
            "observationWindow": window,
            "channel": channel,
            "budget": budget,
        },
        headers=_headers(idem=idem),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Read surface
# ---------------------------------------------------------------------------


def test_growth_read_endpoints_return_envelopes() -> None:
    client = _client()
    for path in ("/freshness", "/segments", "/recommendations", "/actions", "/summary"):
        resp = client.get(f"{BASE}{path}", headers=WRITE_HEADERS)
        assert resp.status_code == 200, (path, resp.text)
    actions = client.get(f"{BASE}/actions", headers=WRITE_HEADERS).json()
    assert actions["count"] >= 5
    # Seed actions expose their draft kind and a derived closeout gate.
    first = actions["items"][0]
    assert "kind" in first
    assert "closeoutGate" in first


# ---------------------------------------------------------------------------
# Criterion 1 — three entry cards persist the correct draft type
# ---------------------------------------------------------------------------


def test_three_entry_cards_persist_correct_draft_type() -> None:
    client = _client()
    for kind in ("offpeak", "winback", "priceops"):
        created = _create_draft(client, kind=kind, name=f"{kind}-campaign", idem=f"idem-{kind}")
        assert created["kind"] == kind
        assert created["status"] == "DRAFT"
        # Persisted: a follow-up GET round-trips the same draft type.
        fetched = client.get(f"{BASE}/actions/{created['id']}", headers=WRITE_HEADERS).json()
        assert fetched["kind"] == kind, (kind, fetched["kind"])


def test_create_action_idempotency_replays_same_draft() -> None:
    client = _client()
    first = _create_draft(client, kind="offpeak", idem="idem-dup")
    second = _create_draft(client, kind="offpeak", idem="idem-dup")
    assert first["id"] == second["id"]
    # Only one action was actually appended for the duplicated key.
    ids = [a["id"] for a in client.get(f"{BASE}/actions", headers=WRITE_HEADERS).json()["items"]]
    assert ids.count(first["id"]) == 1


def test_create_action_round_trips_correlation_id() -> None:
    client = _client()
    resp = client.post(
        f"{BASE}/actions",
        json={
            "name": "corr-campaign",
            "segmentId": "seg-metro-dinner",
            "objective": "obj",
            "targetLift": 1.5,
            "kind": "offpeak",
        },
        headers=_headers(correlation="corr-abc-123"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["correlation_id"] == "corr-abc-123"


# ---------------------------------------------------------------------------
# Criterion 2 — blocked conflict states cannot submit; server returns reasons
# ---------------------------------------------------------------------------


def test_conflict_check_returns_five_named_checks() -> None:
    client = _client()
    resp = client.post(
        f"{BASE}/conflicts/check",
        json={
            "kind": "priceops",
            "store": "Oday 松仁店",
            "observationWindow": "平日 10:00-14:00",
            "channel": "LINE 推播",
            "budget": 80000,
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {c["id"] for c in body["checks"]}
    assert ids == {"overlap", "priceops", "budget", "fatigue", "approval"}
    # Over-budget priceops surfaces warnings (actionable, non-blocking here).
    budget_check = next(c for c in body["checks"] if c["id"] == "budget")
    assert budget_check["level"] == "warn"


def test_blocked_conflict_cannot_submit_and_returns_actionable_reason() -> None:
    client = _client()
    # First draft occupies a store + window and is submitted (becomes active).
    first = _create_draft(client, kind="offpeak", store="Oday 內湖店", window="平日 09:00-12:00")
    submit_first = client.post(f"{BASE}/actions/{first['id']}/submit", json={}, headers=_headers())
    assert submit_first.status_code == 200, submit_first.text

    # Second draft collides on the same store + window -> hard overlap.
    second = _create_draft(client, kind="offpeak", store="Oday 內湖店", window="平日 09:00-12:00")
    gate = client.post(
        f"{BASE}/conflicts/check",
        json={
            "kind": "offpeak",
            "store": "Oday 內湖店",
            "observationWindow": "平日 09:00-12:00",
            "channel": "App 首頁",
            "excludeActionId": second["id"],
        },
        headers=_headers(),
    ).json()
    assert gate["blocked"] is True
    assert gate["reasons"], "blocked gate must carry actionable reasons"

    submit_second = client.post(
        f"{BASE}/actions/{second['id']}/submit", json={}, headers=_headers()
    )
    assert submit_second.status_code == 422, submit_second.text
    detail = submit_second.json()["detail"]
    assert "conflict gate" in detail
    # Server reason is actionable (names the conflicting live campaign).
    assert first["id"] in detail

    # The blocked draft stays DRAFT — no approval item was created.
    assert (
        client.get(f"{BASE}/actions/{second['id']}", headers=WRITE_HEADERS).json()["status"]
        == "DRAFT"
    )


# ---------------------------------------------------------------------------
# Criterion 3 — approval creates a Govern item and advances the Growth state
# ---------------------------------------------------------------------------


def test_submit_creates_govern_item_and_approval_advances_state() -> None:
    client = _client()
    draft = _create_draft(client, kind="offpeak", store="Oday 南港店", window="平日 14:00-17:00")

    submit = client.post(
        f"{BASE}/actions/{draft['id']}/submit", json={}, headers=_headers(idem="idem-submit")
    )
    assert submit.status_code == 200, submit.text
    approval = submit.json()["approval"]
    assert approval["module"] == "Growth"
    assert approval["ref"] == draft["id"]
    assert approval["status"] == "pending"
    assert submit.json()["status"] == "PENDING_APPROVAL"

    assert approval["risk"] == "低"
    governed = client.get("/api/v1/operator/governance/snapshot", headers=_headers())
    assert governed.status_code == 200, governed.text
    governed_approval = next(row for row in governed.json()["approvals"] if row["id"] == approval["id"])
    assert governed_approval["priority"] == "low"

    # The Govern item is listed on the growth approvals surface.
    approvals = client.get(f"{BASE}/approvals", headers=WRITE_HEADERS).json()["items"]
    assert any(a["id"] == approval["id"] and a["module"] == "Growth" for a in approvals)

    # Idempotent re-submit does not create a second approval.
    replay = client.post(
        f"{BASE}/actions/{draft['id']}/submit", json={}, headers=_headers(idem="idem-submit")
    )
    assert replay.json()["idempotentReplay"] is True
    assert len(client.get(f"{BASE}/approvals", headers=WRITE_HEADERS).json()["items"]) == 1

    # Approve advances the Growth state to APPROVED and writes a Decision Log.
    decided = client.post(
        f"{BASE}/approvals/{approval['id']}/decision",
        json={"decision": "approved", "reason": "符合政策"},
        headers=_headers(),
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["growthStatus"] == "APPROVED"
    assert (
        client.get(f"{BASE}/actions/{draft['id']}", headers=WRITE_HEADERS).json()["status"]
        == "APPROVED"
    )
    decisions = client.get(f"{BASE}/decisions", headers=WRITE_HEADERS).json()["items"]
    assert any(d["ref"] == draft["id"] and d["verdict"] == "核准" for d in decisions)


def test_rejected_approval_returns_action_to_draft() -> None:
    client = _client()
    draft = _create_draft(client, kind="winback", store="全品牌", window="核准後 3 日內")
    approval = client.post(
        f"{BASE}/actions/{draft['id']}/submit", json={}, headers=_headers()
    ).json()["approval"]

    rejected = client.post(
        f"{BASE}/approvals/{approval['id']}/decision",
        json={"decision": "rejected", "reason": "需補資料"},
        headers=_headers(),
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["growthStatus"] == "DRAFT"
    assert (
        client.get(f"{BASE}/actions/{draft['id']}", headers=WRITE_HEADERS).json()["status"]
        == "DRAFT"
    )


def test_full_lifecycle_pending_to_outcome_ready() -> None:
    client = _client()
    draft = _create_draft(client, kind="offpeak", store="Oday 三重店", window="平日 15:00-18:00")
    approval = client.post(
        f"{BASE}/actions/{draft['id']}/submit", json={}, headers=_headers()
    ).json()["approval"]
    client.post(
        f"{BASE}/approvals/{approval['id']}/decision",
        json={"decision": "approved"},
        headers=_headers(),
    )
    # APPROVED -> SCHEDULED -> RUNNING -> OBSERVING -> OUTCOME_READY
    for target in ("SCHEDULED", "RUNNING", "OBSERVING", "OUTCOME_READY"):
        resp = client.post(
            f"{BASE}/actions/{draft['id']}/transition",
            json={"targetStatus": target},
            headers=_execution_headers(),
        )
        assert resp.status_code == 200, (target, resp.text)
        assert resp.json()["status"] == target


# ---------------------------------------------------------------------------
# Criterion 4 — effective / ineffective / inconclusive outcomes persist
#               and write Decision Log + Audit Trail
# ---------------------------------------------------------------------------


def test_outcomes_persist_and_write_decision_log() -> None:
    client = _client()
    cases = [
        ("growth-7001", "EFFECTIVE", "CLOSE", "CLOSED", "判定有效"),
        ("growth-7002", "INEFFECTIVE", "ROLLBACK", "INEFFECTIVE", "判定無效"),
        ("growth-7003", "INCONCLUSIVE", "STRENGTHEN_EVIDENCE", "OUTCOME_READY", "判定待判定"),
    ]
    for action_id, outcome, required, expected_status, verdict in cases:
        resp = client.post(
            f"{BASE}/actions/{action_id}/outcome",
            json={"outcome": outcome, "requiredAction": required, "rationale": "Synthetic operator reviewed the outcome evidence"},
            headers=_headers(idem=f"idem-out-{action_id}"),
        )
        assert resp.status_code == 200, (action_id, resp.text)
        body = resp.json()
        assert body["growth_outcome"] == outcome
        assert body["status"] == expected_status
        assert body["decision"]["verdict"] == verdict
        # Outcome persists on the action record.
        assert (
            client.get(f"{BASE}/actions/{action_id}", headers=WRITE_HEADERS).json()["growthOutcome"]
            == outcome
        )

    decisions = client.get(f"{BASE}/decisions", headers=WRITE_HEADERS).json()["items"]
    assert len(decisions) == 3
    assert {d["verdict"] for d in decisions} == {"判定有效", "判定無效", "判定待判定"}


def test_ineffective_action_cannot_close_directly() -> None:
    client = _client()
    # growth-7002 is ineffective (negative observed lift); direct CLOSE blocked.
    resp = client.post(
        f"{BASE}/actions/growth-7002/transition",
        json={"targetStatus": "CLOSED"},
        headers=_execution_headers(),
    )
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# Cross-cutting — fail-closed authorization on write routes
# ---------------------------------------------------------------------------


def test_create_action_without_role_is_denied() -> None:
    client = _client()
    resp = client.post(
        f"{BASE}/actions",
        json={
            "name": "no-auth",
            "segmentId": "seg-metro-dinner",
            "objective": "obj",
            "targetLift": 1.0,
            "kind": "offpeak",
        },
    )
    assert resp.status_code in (401, 403), resp.text


def test_growth_action_evidence_level_canonical_alignment() -> None:
    client = _client()
    # 1. Newly created draft has unrated evidenceLevel (None), not "low" or "medium"
    draft = _create_draft(client, kind="offpeak", name="evidence-test-campaign")
    fetched = client.get(f"{BASE}/actions/{draft['id']}", headers=WRITE_HEADERS).json()
    assert fetched["evidenceLevel"] is None

    # 2. Transition through lifecycle to OUTCOME_READY
    _drive_to_outcome_ready(client, draft["id"])

    # 3. Write outcome with canonical EvidenceLevel (e.g. L3)
    resp = client.post(
        f"{BASE}/actions/{draft['id']}/outcome",
        json={
            "outcome": "EFFECTIVE",
            "requiredAction": "CLOSE",
            "observedLift": 2.5,
            "evidenceLevel": "L3",
            "rationale": "Synthetic operator reviewed L3 evidence",
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "CLOSED"

    fetched_closed = client.get(f"{BASE}/actions/{draft['id']}", headers=WRITE_HEADERS).json()
    assert fetched_closed["evidenceLevel"] == "L3"


def _drive_to_outcome_ready(client: TestClient, action_id: str) -> None:
    """Advance an approved draft through its lifecycle to OUTCOME_READY."""
    approval = client.post(
        f"{BASE}/actions/{action_id}/submit", json={}, headers=_headers()
    ).json()["approval"]
    client.post(
        f"{BASE}/approvals/{approval['id']}/decision",
        json={"decision": "approved"},
        headers=_headers(),
    )
    for target in ("SCHEDULED", "RUNNING", "OBSERVING", "OUTCOME_READY"):
        client.post(
            f"{BASE}/actions/{action_id}/transition",
            json={"targetStatus": target},
            headers=_execution_headers(),
        )


def test_outcome_rejects_evidence_that_names_no_rung() -> None:
    """The pre-ADR-0004 vocabulary is refused at the boundary, not stored.

    "medium" is the value the old default wrote, so a caller still sending it
    explicitly would reproduce the exact record the ADR removed. The ladder is
    the request contract now, so this is a 422 rather than a silent write.
    """
    client = _client()
    draft = _create_draft(client, kind="offpeak", name="evidence-reject-campaign")
    _drive_to_outcome_ready(client, draft["id"])

    for value in ("medium", "high", "low", "L6"):
        resp = client.post(
            f"{BASE}/actions/{draft['id']}/outcome",
            json={
                "outcome": "EFFECTIVE",
                "requiredAction": "CLOSE",
                "observedLift": 2.5,
                "evidenceLevel": value,
                "rationale": "Synthetic evidence validation",
            },
            headers=_headers(),
        )
        assert resp.status_code == 422, f"{value!r} -> {resp.status_code}: {resp.text}"

    # Nothing was written: the action neither closed nor acquired a claim.
    fetched = client.get(f"{BASE}/actions/{draft['id']}", headers=WRITE_HEADERS).json()
    assert fetched["evidenceLevel"] is None
    assert fetched["status"] == "OUTCOME_READY"


def test_unrated_evidence_cannot_close_an_action() -> None:
    """Unrated is inconclusive, which is the behaviour the "medium" default hid.

    An action that met its target lift with nobody having assessed the evidence
    used to arrive at the gate carrying a medium claim and close. With the
    default gone it stays open.
    """
    client = _client()
    draft = _create_draft(client, kind="offpeak", name="evidence-unrated-campaign")
    _drive_to_outcome_ready(client, draft["id"])

    resp = client.post(
        f"{BASE}/actions/{draft['id']}/outcome",
        json={
            "outcome": "EFFECTIVE",
            "requiredAction": "CLOSE",
            "observedLift": 99.0,
            "evidenceLevel": None,
            "rationale": "Synthetic unrated evidence review",
        },
        headers=_headers(),
    )
    assert resp.status_code in (200, 409), resp.text

    fetched = client.get(f"{BASE}/actions/{draft['id']}", headers=WRITE_HEADERS).json()
    assert fetched["status"] != "CLOSED"
    assert fetched["evidenceLevel"] is None


def test_evidence_below_the_causal_threshold_cannot_close_an_action() -> None:
    """L2 is a real reading, and still not enough to claim the lift was caused.

    ODP-BR-AD-001 puts the causal threshold at L3; the backend gate and the
    browser gate must agree on it, so the rung one step below must not close.
    """
    client = _client()
    draft = _create_draft(client, kind="offpeak", name="evidence-l2-campaign")
    _drive_to_outcome_ready(client, draft["id"])

    resp = client.post(
        f"{BASE}/actions/{draft['id']}/outcome",
        json={
            "outcome": "EFFECTIVE",
            "requiredAction": "CLOSE",
            "observedLift": 99.0,
            "evidenceLevel": "L2",
            "rationale": "Synthetic limited evidence review",
        },
        headers=_headers(),
    )
    assert resp.status_code in (200, 409), resp.text

    fetched = client.get(f"{BASE}/actions/{draft['id']}", headers=WRITE_HEADERS).json()
    assert fetched["status"] != "CLOSED"
    assert fetched["evidenceLevel"] == "L2"


@pytest.mark.parametrize("operation", ["create", "submit", "transition", "decision"])
def test_growth_draft_commands_refuse_unbound_legacy_replay(operation):
    from modules.opsboard.application.growth import GrowthConflict, GrowthService

    service = GrowthService()
    draft = service.create_action(name="Synthetic", segment_id="seg-metro-dinner", objective="Synthetic", target_lift=1)
    approval = service.submit_for_approval(action_id=draft["id"])["approval"] if operation == "decision" else None
    service._state["idempotencyResults"]["synthetic-legacy-command"] = {"id": "unattributable"}
    before = service.export_state()
    with pytest.raises(GrowthConflict, match="UNBOUND_LEGACY_REPLAY"):
        if operation == "create":
            service.create_action(name="Synthetic", segment_id="seg-metro-dinner", objective="Synthetic", target_lift=1, idempotency_key="synthetic-legacy-command")
        elif operation == "submit":
            service.submit_for_approval(action_id=draft["id"], idempotency_key="synthetic-legacy-command")
        elif operation == "transition":
            service.transition_action(action_id=draft["id"], target_status="SCHEDULED", idempotency_key="synthetic-legacy-command")
        else:
            service.resolve_approval(approval_id=approval["id"], decision="approved", idempotency_key="synthetic-legacy-command")
    assert service.export_state() == before


def test_growth_create_request_actor_and_operation_bound_replay():
    client = _client()
    body = {"name": "Synthetic retry", "segmentId": "seg-metro-dinner", "objective": "Synthetic", "targetLift": 1, "store": "Synthetic Store", "observationWindow": "Synthetic Window"}
    headers = _headers(idem="synthetic-create-bound")
    first = client.post(f"{BASE}/actions", json=body, headers=headers)
    assert first.status_code == 200, first.text
    replay = client.post(f"{BASE}/actions", json={**body, "actorName": "forged", "actorRoleId": "forged"}, headers=headers)
    assert replay.status_code == 200 and replay.json()["idempotentReplay"]
    assert replay.json()["id"] == first.json()["id"]
    before = client.get(f"{BASE}/actions", headers=headers).json()["items"]
    changed = client.post(f"{BASE}/actions", json={**body, "name": "Changed"}, headers=headers)
    assert changed.status_code == 409 and "IDEMPOTENCY_CONFLICT" in changed.text
    other_operation = client.post(f"{BASE}/actions/{first.json()['id']}/submit", json={}, headers=headers)
    assert other_operation.status_code == 409
    denied = client.post(f"{BASE}/actions", json=body, headers={**headers, "X-Roles": "auditor"})
    assert denied.status_code == 403
    assert client.get(f"{BASE}/actions", headers=headers).json()["items"] == before
    other_actor = client.post(f"{BASE}/actions", json=body, headers={**headers, "X-Subject-Id": "synthetic-other"})
    assert other_actor.status_code == 200 and not other_actor.json()["idempotentReplay"]
    assert other_actor.json()["id"] != first.json()["id"]


def test_growth_submit_record_and_actor_bound_replay():
    client = _client()
    first = _create_draft(client, kind="offpeak", store="Synthetic Store One", window="Synthetic Window")
    second = _create_draft(client, kind="offpeak", store="Synthetic Store Two", window="Synthetic Window")
    headers = _headers(idem="synthetic-submit-bound")
    accepted = client.post(f"{BASE}/actions/{first['id']}/submit", json={}, headers=headers)
    assert accepted.status_code == 200, accepted.text
    replay = client.post(f"{BASE}/actions/{first['id']}/submit", json={}, headers=headers)
    assert replay.status_code == 200 and replay.json()["idempotentReplay"]
    assert replay.json()["approval"] == accepted.json()["approval"]
    other_record = client.post(f"{BASE}/actions/{second['id']}/submit", json={}, headers=headers)
    assert other_record.status_code == 409
    other_actor = client.post(f"{BASE}/actions/{first['id']}/submit", json={}, headers={**headers, "X-Subject-Id": "synthetic-other"})
    assert other_actor.status_code == 409 and "only DRAFT" in other_actor.text
    denied = client.post(f"{BASE}/actions/{first['id']}/submit", json={}, headers={**headers, "X-Roles": "auditor"})
    assert denied.status_code == 403
    assert len(client.get(f"{BASE}/approvals", headers=headers).json()["items"]) == 1


def test_growth_create_and_submit_audit_use_verified_principal(monkeypatch):
    from modules.opsboard.application.growth import GrowthService

    events = []
    original = GrowthService._append_audit_event

    def record(self, **kwargs):
        event = original(self, **kwargs)
        events.append(event)
        return event

    monkeypatch.setattr(GrowthService, "_append_audit_event", record)
    client = _client()
    body = {"name": "Synthetic attribution", "segmentId": "seg-metro-dinner", "objective": "Synthetic", "targetLift": 1, "store": "Synthetic Store", "observationWindow": "Synthetic Window", "actorName": "forged", "actorRoleId": "forged"}
    created = client.post(f"{BASE}/actions", json=body, headers=_headers())
    assert created.status_code == 200, created.text
    submitted = client.post(f"{BASE}/actions/{created.json()['id']}/submit", json={"actorName": "forged", "actorRoleId": "forged"}, headers=_headers())
    assert submitted.status_code == 200, submitted.text
    assert [e["action"] for e in events] == ["growth.action.created", "growth.action.submitted"]
    assert all(e["actorName"] == WRITE_HEADERS["X-Subject-Id"] and e["actorRoleId"] != "forged" for e in events)
    assert submitted.json()["approval"]["requester"] == WRITE_HEADERS["X-Subject-Id"]


def test_growth_command_receipts_survive_reload_and_bind_tenant():
    from modules.opsboard.application.growth import GrowthConflict, GrowthPolicyError, GrowthService

    service = GrowthService(tenant_id="synthetic-tenant")
    kwargs = dict(name="Synthetic", segment_id="seg-metro-dinner", objective="Synthetic", target_lift=1, store="Synthetic Store", observation_window="Synthetic Window", actor_name="synthetic-actor", tenant_id="synthetic-tenant", idempotency_key="synthetic-durable-create")
    created = service.create_action(**kwargs)
    restored = GrowthService(initial_state=service.export_state(), seed_fixtures=False, tenant_id="synthetic-tenant")
    assert restored.create_action(**kwargs)["id"] == created["id"]
    with pytest.raises(GrowthPolicyError, match="TENANT_SCOPE_DENIED"):
        restored.create_action(**{**kwargs, "tenant_id": "other"})
    submitted = restored.submit_for_approval(action_id=created["id"], actor_name="synthetic-actor", tenant_id="synthetic-tenant", idempotency_key="synthetic-durable-submit")
    reloaded = GrowthService(initial_state=restored.export_state(), seed_fixtures=False, tenant_id="synthetic-tenant")
    replay = reloaded.submit_for_approval(action_id=created["id"], actor_name="synthetic-actor", tenant_id="synthetic-tenant", idempotency_key="synthetic-durable-submit")
    assert replay["idempotentReplay"] and replay["approval"] == submitted["approval"]
    with pytest.raises(GrowthConflict, match="IDEMPOTENCY_CONFLICT"):
        reloaded.create_action(**{**kwargs, "budget": 5})


@pytest.mark.parametrize("role,can_decide,can_execute", [
    ("operations_manager", True, False),
    ("regional_supervisor", False, True),
    ("auditor", False, False),
])
def test_growth_decision_and_execution_use_separate_rbac_permissions(role, can_decide, can_execute):
    client = _client()
    draft = _create_draft(client, kind="offpeak", store="Synthetic Authority Store", window="Synthetic Authority Window")
    approval = client.post(f"{BASE}/actions/{draft['id']}/submit", json={}, headers=_headers()).json()["approval"]
    caller = {**_headers(), "X-Roles": role, "X-Subject-Id": "synthetic-role-probe"}
    decided = client.post(f"{BASE}/approvals/{approval['id']}/decision", json={"decision": "approved", "reason": "Synthetic independent review"}, headers=caller)
    assert decided.status_code == (200 if can_decide else 403), decided.text
    if not can_decide:
        authorized = client.post(f"{BASE}/approvals/{approval['id']}/decision", json={"decision": "approved", "reason": "Synthetic authorized review"}, headers=_headers())
        assert authorized.status_code == 200
    before = client.get(f"{BASE}/actions/{draft['id']}", headers=_headers()).json()
    executed = client.post(f"{BASE}/actions/{draft['id']}/transition", json={"targetStatus": "SCHEDULED"}, headers=caller)
    assert executed.status_code == (200 if can_execute else 403), executed.text
    if not can_execute:
        assert client.get(f"{BASE}/actions/{draft['id']}", headers=_headers()).json() == before


def test_growth_transition_replay_is_actor_request_bound_and_ignores_body_actor():
    client = _client()
    path = f"{BASE}/actions/growth-7004/transition"
    body = {"targetStatus": "OUTCOME_READY", "actorName": "forged", "actorRoleId": "forged"}
    headers = _execution_headers(idem="synthetic-transition-bound")
    first = client.post(path, json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["auditEvent"]["actorName"] == headers["X-Subject-Id"]
    assert first.json()["auditEvent"]["actorRoleId"] != "forged"
    replay = client.post(path, json=body, headers=headers)
    assert replay.status_code == 200 and replay.json()["idempotentReplay"]
    assert replay.json()["auditEvent"] == first.json()["auditEvent"]
    changed = client.post(path, json={"targetStatus": "OBSERVING"}, headers=headers)
    assert changed.status_code == 409
    other_record = client.post(f"{BASE}/actions/growth-7001/transition", json=body, headers=headers)
    assert other_record.status_code == 409
    other_actor = client.post(path, json=body, headers={**headers, "X-Subject-Id": "synthetic-other-executor"})
    assert other_actor.status_code == 409 and not other_actor.json().get("idempotentReplay")
    denied = client.post(path, json=body, headers={**headers, "X-Roles": "operations_manager"})
    assert denied.status_code == 403


def test_growth_decision_replay_is_bound_to_verified_actor_record_and_request(monkeypatch):
    from modules.opsboard.application.growth import GrowthService

    events = []
    original = GrowthService._append_audit_event
    def record(self, **kwargs):
        result = original(self, **kwargs)
        events.append(result)
        return result
    monkeypatch.setattr(GrowthService, "_append_audit_event", record)
    client = _client()
    draft = _create_draft(client, kind="offpeak", store="Synthetic Decision Store", window="Synthetic Decision Window")
    approval = client.post(f"{BASE}/actions/{draft['id']}/submit", json={}, headers=_headers()).json()["approval"]
    other = _create_draft(client, kind="offpeak", store="Synthetic Other Store", window="Synthetic Other Window")
    other_approval = client.post(f"{BASE}/actions/{other['id']}/submit", json={}, headers=_headers()).json()["approval"]
    path = f"{BASE}/approvals/{approval['id']}/decision"
    headers = {**_headers(idem="synthetic-decision-bound"), "X-Subject-Id": "synthetic-independent-reviewer"}
    body = {"decision": "approved", "reason": "Synthetic review", "actorName": "forged", "actorRoleId": "forged"}
    accepted = client.post(path, json=body, headers=headers)
    assert accepted.status_code == 200, accepted.text
    assert events[-1]["actorName"] == headers["X-Subject-Id"] and events[-1]["actorRoleId"] != "forged"
    count = len(events)
    replay = client.post(path, json=body, headers=headers)
    assert replay.status_code == 200 and replay.json()["idempotentReplay"]
    assert replay.json()["decision"] == accepted.json()["decision"] and len(events) == count
    for altered in [{**body, "reason": "Changed reason"}, {**body, "decision": "rejected"}]:
        assert client.post(path, json=altered, headers=headers).status_code == 409
    assert client.post(f"{BASE}/approvals/{other_approval['id']}/decision", json=body, headers=headers).status_code == 409
    different_actor = client.post(path, json=body, headers={**headers, "X-Subject-Id": "synthetic-other-reviewer"})
    assert different_actor.status_code == 409 and "already decided" in different_actor.text
    assert client.post(path, json=body, headers={**headers, "X-Roles": "regional_supervisor"}).status_code == 403
    assert len(events) == count


def test_growth_approval_and_execution_authority_fail_closed_without_wiring():
    from fastapi import FastAPI

    from apps.api.app.routes.operator_modules.growth import create_growth_sub_router
    from modules.opsboard.application.growth import GrowthService

    service = GrowthService()
    before = service.export_state()
    app = FastAPI()
    app.include_router(create_growth_sub_router(service))
    client = TestClient(app)
    for path, body in [("/growth/actions/growth-7004/transition", {"targetStatus": "OBSERVING"}), ("/growth/approvals/synthetic/decision", {"decision": "approved"})]:
        response = client.post(path, json=body)
        assert response.status_code == 403 and "GROWTH_COMMAND_AUTHORITY_UNCONFIGURED" in response.text
    assert service.export_state() == before


def test_growth_transition_and_decision_receipts_survive_reload_and_bind_tenant():
    from modules.opsboard.application.growth import GrowthPolicyError, GrowthService

    service = GrowthService(tenant_id="synthetic-tenant")
    draft = service.create_action(name="Synthetic", segment_id="seg-metro-dinner", objective="Synthetic", target_lift=1, store="Synthetic Store", observation_window="Synthetic Window")
    approval = service.submit_for_approval(action_id=draft["id"])["approval"]
    decision_args = dict(approval_id=approval["id"], decision="approved", reason="Synthetic review", actor_name="synthetic-reviewer", tenant_id="synthetic-tenant", idempotency_key="synthetic-durable-decision")
    decided = service.resolve_approval(**decision_args)
    restored = GrowthService(initial_state=service.export_state(), seed_fixtures=False, tenant_id="synthetic-tenant")
    assert restored.resolve_approval(**decision_args)["decision"] == decided["decision"]
    with pytest.raises(GrowthPolicyError, match="TENANT_SCOPE_DENIED"):
        restored.resolve_approval(**{**decision_args, "tenant_id": "other"})
    transition_args = dict(action_id=draft["id"], target_status="SCHEDULED", actor_name="synthetic-executor", tenant_id="synthetic-tenant", idempotency_key="synthetic-durable-transition")
    transitioned = restored.transition_action(**transition_args)
    reloaded = GrowthService(initial_state=restored.export_state(), seed_fixtures=False, tenant_id="synthetic-tenant")
    replay = reloaded.transition_action(**transition_args)
    assert replay["idempotentReplay"] and replay["auditEvent"] == transitioned["auditEvent"]
    with pytest.raises(GrowthPolicyError, match="TENANT_SCOPE_DENIED"):
        reloaded.transition_action(**{**transition_args, "tenant_id": "other"})


@pytest.mark.parametrize("target", ["PENDING_APPROVAL", "APPROVED"])
def test_growth_generic_execution_cannot_bypass_approval_workflow(target):
    client = _client()
    draft = _create_draft(client, kind="offpeak", store="Synthetic Boundary Store", window="Synthetic Boundary Window")
    before = client.get(f"{BASE}/actions/{draft['id']}", headers=_headers()).json()
    denied = client.post(f"{BASE}/actions/{draft['id']}/transition", json={"targetStatus": target}, headers=_execution_headers())
    assert denied.status_code == 422 and "APPROVAL_WORKFLOW_REQUIRED" in denied.text
    assert client.get(f"{BASE}/actions/{draft['id']}", headers=_headers()).json() == before
    assert client.get(f"{BASE}/approvals", headers=_headers()).json()["items"] == []


def test_growth_generic_execution_cannot_replace_a_pending_decision_or_outcome():
    client = _client()
    draft = _create_draft(client, kind="offpeak", store="Synthetic Pending Store", window="Synthetic Pending Window")
    client.post(f"{BASE}/actions/{draft['id']}/submit", json={}, headers=_headers())
    before = client.get(f"{BASE}/actions/{draft['id']}", headers=_headers()).json()
    for target in ["APPROVED", "DRAFT"]:
        denied = client.post(f"{BASE}/actions/{draft['id']}/transition", json={"targetStatus": target}, headers=_execution_headers())
        assert denied.status_code == 422 and "APPROVAL_WORKFLOW_REQUIRED" in denied.text
    assert client.get(f"{BASE}/actions/{draft['id']}", headers=_headers()).json() == before
    outcome_before = client.get(f"{BASE}/actions/growth-7001", headers=_headers()).json()
    denied = client.post(f"{BASE}/actions/growth-7001/transition", json={"targetStatus": "INEFFECTIVE"}, headers=_execution_headers())
    assert denied.status_code == 422 and "OUTCOME_WORKFLOW_REQUIRED" in denied.text
    assert client.get(f"{BASE}/actions/growth-7001", headers=_headers()).json() == outcome_before
    before = client.get(f"{BASE}/actions/growth-7003", headers=_headers()).json()
    denied = client.post(f"{BASE}/actions/growth-7003/transition", json={"targetStatus": "INEFFECTIVE"}, headers=_execution_headers())
    assert denied.status_code == 422 and "OUTCOME_WORKFLOW_REQUIRED" in denied.text
    assert client.get(f"{BASE}/actions/growth-7003", headers=_headers()).json() == before


def test_growth_decision_does_not_revive_a_cancelled_linked_action():
    client = _client()
    draft = _create_draft(client, kind="offpeak", store="Synthetic Cancelled Store", window="Synthetic Cancelled Window")
    approval = client.post(f"{BASE}/actions/{draft['id']}/submit", json={}, headers=_headers()).json()["approval"]
    cancelled = client.post(f"{BASE}/actions/{draft['id']}/transition", json={"targetStatus": "CANCELLED"}, headers=_execution_headers())
    assert cancelled.status_code == 200, cancelled.text
    denied = client.post(f"{BASE}/approvals/{approval['id']}/decision", json={"decision": "approved", "reason": "Synthetic review"}, headers=_headers())
    assert denied.status_code == 409 and "not PENDING_APPROVAL" in denied.text
    assert client.get(f"{BASE}/actions/{draft['id']}", headers=_headers()).json()["status"] == "CANCELLED"
    assert client.get(f"{BASE}/decisions", headers=_headers()).json()["items"] == []


@pytest.mark.parametrize("target", ["CLOSED", "INEFFECTIVE"])
def test_growth_generic_execution_cannot_bypass_a_mature_outcome_decision(target):
    from modules.opsboard.application.growth import GrowthPolicyError, GrowthService

    service = GrowthService()
    # An isolated, mature, effective fixture proves the positive closeout gate
    # cannot substitute for the dedicated human outcome command.
    service._find_action("growth-7001")["status"] = "OUTCOME_READY"
    assert service.get_action("growth-7001")["closeoutGate"]["canClose"]
    before = service.export_state()
    with pytest.raises(GrowthPolicyError, match="OUTCOME_WORKFLOW_REQUIRED"):
        service.transition_action(action_id="growth-7001", target_status=target)
    assert service.export_state() == before
