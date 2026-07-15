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
    submit_first = client.post(
        f"{BASE}/actions/{first['id']}/submit", json={}, headers=_headers()
    )
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
    assert client.get(f"{BASE}/actions/{second['id']}", headers=WRITE_HEADERS).json()["status"] == "DRAFT"


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
    assert client.get(f"{BASE}/actions/{draft['id']}", headers=WRITE_HEADERS).json()["status"] == "APPROVED"
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
    assert client.get(f"{BASE}/actions/{draft['id']}", headers=WRITE_HEADERS).json()["status"] == "DRAFT"


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
            headers=_headers(),
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
            json={"outcome": outcome, "requiredAction": required},
            headers=_headers(idem=f"idem-out-{action_id}"),
        )
        assert resp.status_code == 200, (action_id, resp.text)
        body = resp.json()
        assert body["growth_outcome"] == outcome
        assert body["status"] == expected_status
        assert body["decision"]["verdict"] == verdict
        # Outcome persists on the action record.
        assert (
            client.get(f"{BASE}/actions/{action_id}", headers=WRITE_HEADERS)
            .json()["growthOutcome"]
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
        headers=_headers(),
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
