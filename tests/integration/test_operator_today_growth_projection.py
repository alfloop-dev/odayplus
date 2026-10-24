"""Today uses real scoped Growth HTTP owners over owned SQLite engineering data.

These controls reuse the original canonical pending fixture, synthetic masters
and explicit POC pricing algorithm. They are not PostgreSQL, member/Forecast
measurements, human approval or native browser acceptance.
"""
from datetime import UTC, datetime, timedelta

import pytest

from modules.opsboard.application import growth
from shared.infrastructure.persistence.operator_domains import DurableOperatorDomainStateRepository
from tests.integration.test_operator_shared_pending_collection import (
    BASE,
    HIDDEN_STORE,
    TENANT,
    VISIBLE_STORE,
    _growth_submission,
)
from tests.integration.test_operator_shared_pending_collection import (
    pending_context as pending_context,
)

MARKETING = {"x-operator-role": "marketing-manager", "x-roles": "marketing_manager,pricing_manager,operations_manager"}


def _cards(context, *, headers=None, path="today"):
    before = context.persisted()
    response = context.client.get(BASE + "/operator/" + path, headers={**MARKETING, **(headers or {})})
    assert response.status_code == 200, response.text
    assert context.persisted() == before
    assert response.json()["meta"]["role"]["id"] == "marketing-manager"
    return {row["label"]: row for row in response.json()["today"]["kpis"]}


def _draft(context, *, plan="visible-plan", kind="offpeak", suffix="one"):
    response = context.client.get(BASE + "/operator/growth/segments")
    assert response.status_code == 200, response.text
    segment = next(row for row in response.json()["items"] if row["planId"] == plan)
    created = context.client.post(BASE + "/operator/growth/actions", headers={"Idempotency-Key": "today-draft-" + suffix}, json={
        "name": "Declared Today draft " + suffix, "segmentId": segment["id"], "kind": kind,
        "store": segment["storeId"], "objective": "Synthetic owner control", "targetLift": 0.1,
        "rationale": "Engineering fixture only", "rollbackPlan": "Review original canonical inputs",
    })
    assert created.status_code == 200, created.text
    return created.json()["id"]


@pytest.mark.parametrize("kind", ["offpeak", "winback", "priceops"])
def test_today_and_bootstrap_count_real_growth_drafts_without_faking_candidate_sources(pending_context, kind):
    context = pending_context
    _draft(context, kind=kind)
    for path in ("today", "bootstrap"):
        cards = _cards(context, path=path)
        assert cards["促銷草稿"]["value"] == "1"
        assert cards["待核准活動"]["value"] == cards["成效待判斷"]["value"] == "0"
        assert cards["離峰機會"]["value"] is None
        assert cards["會員召回候選"]["value"] is None


@pytest.mark.parametrize("scope", [
    {"x-store-ids": VISIBLE_STORE}, {"x-brand-ids": "brand-tenant-a"}, {"x-region-ids": "north"},
])
def test_today_growth_counts_use_the_existing_current_priceops_parent_scope(pending_context, scope):
    _draft(pending_context, suffix="visible")
    _draft(pending_context, plan="excluded-plan", suffix="excluded")
    _growth_submission(pending_context, suffix="visible-pending")
    _growth_submission(pending_context, plan="excluded-plan", suffix="excluded-pending")
    wide = _cards(pending_context)
    assert wide["促銷草稿"]["value"] == wide["待核准活動"]["value"] == "2"
    scoped = _cards(pending_context, headers=scope)
    assert scoped["促銷草稿"]["value"] == scoped["待核准活動"]["value"] == "1"
    foreign = _cards(pending_context, headers={"x-tenant-id": "tenant-b"})
    assert foreign["促銷草稿"]["value"] == foreign["待核准活動"]["value"] == "0"
    excluded = _cards(pending_context, headers={"x-store-ids": HIDDEN_STORE})
    assert excluded["促銷草稿"]["value"] == excluded["待核准活動"]["value"] == "1"
    # A prior request's principal/role or counts must never be cached globally.
    again = _cards(pending_context, headers=scope)
    assert again["促銷草稿"]["value"] == again["待核准活動"]["value"] == "1"


@pytest.mark.parametrize("header", ["x-assigned-area-ids", "x-heat-zone-ids"])
def test_today_growth_requires_real_parent_evidence_for_area_and_heat_restrictions(pending_context, header):
    _draft(pending_context)
    assert _cards(pending_context)["促銷草稿"]["value"] == "1"
    # Existing PriceOps scope has no such canonical association; do not infer it.
    restricted = _cards(pending_context, headers={header: "unproven-parent"})
    assert restricted["促銷草稿"]["value"] == "0"


def test_today_outcome_card_waits_for_the_original_recorded_execution_window(pending_context, monkeypatch):
    now = [datetime(2026, 10, 1, tzinfo=UTC)]
    monkeypatch.setattr(growth, "_now_iso", lambda: now[0].isoformat())
    action, approval = _growth_submission(pending_context, suffix="maturity")
    approved = pending_context.client.post(BASE + "/operator/growth/approvals/" + approval + "/decision",
        headers={"Idempotency-Key": "today-independent-approval", "x-subject-id": "synthetic-independent-reviewer"},
        json={"decision": "approved", "reason": "Explicit synthetic workflow prerequisite"})
    assert approved.status_code == 200, approved.text
    for target in ("EXECUTED", "OBSERVING"):
        transitioned = pending_context.client.post(BASE + "/operator/growth/actions/" + action + "/transition",
            headers={"Idempotency-Key": "today-" + target, "x-roles": "operations_manager,regional_supervisor"},
            json={"targetStatus": target})
        assert transitioned.status_code == 200, transitioned.text
    assert _cards(pending_context)["成效待判斷"]["value"] == "0"
    now[0] += timedelta(days=14) - timedelta(microseconds=1)
    premature = pending_context.client.post(BASE + "/operator/growth/actions/" + action + "/transition",
        headers={"Idempotency-Key": "today-premature", "x-roles": "operations_manager,regional_supervisor"},
        json={"targetStatus": "OUTCOME_READY"})
    assert premature.status_code == 409 and "OBSERVATION_WINDOW_NOT_MATURE" in premature.text
    assert _cards(pending_context)["成效待判斷"]["value"] == "0"
    now[0] += timedelta(microseconds=1)
    ready = pending_context.client.post(BASE + "/operator/growth/actions/" + action + "/transition",
        headers={"Idempotency-Key": "today-ready", "x-roles": "operations_manager,regional_supervisor"},
        json={"targetStatus": "OUTCOME_READY"})
    assert ready.status_code == 200, ready.text
    assert _cards(pending_context)["成效待判斷"]["value"] == "1"


def test_today_legacy_ready_without_recorded_window_remains_unknown(pending_context):
    identity = _draft(pending_context)
    owner = DurableOperatorDomainStateRepository(pending_context.documents, "growth")
    state = owner.load(TENANT)
    legacy = next(row for row in state["actions"] if row["id"] == identity)
    # Explicit historical engineering input; positive cases above use commands.
    legacy["status"] = "OUTCOME_READY"
    owner.save(TENANT, state)
    cards = _cards(pending_context)
    assert cards["成效待判斷"]["value"] is None
    assert cards["促銷草稿"]["value"] == "0"


def test_today_missing_growth_read_source_is_unknown_without_storing_a_fallback(pending_context, monkeypatch):
    _draft(pending_context)

    def unavailable(*_args, **_kwargs):
        raise growth.GrowthPolicyError("Explicit missing canonical Growth read owner")

    monkeypatch.setattr(growth.GrowthService, "list_actions", unavailable)
    assert all(row["value"] is None for row in _cards(pending_context).values())


@pytest.mark.parametrize("damage", ["status", "other-owned-ref", "both"])
def test_today_ambiguous_approval_id_is_unknown_even_with_one_matching_pending_row(pending_context, damage):
    _, approval = _growth_submission(pending_context, suffix="duplicate-parent")
    other = _draft(pending_context, suffix="other-owned-parent")
    assert _cards(pending_context)["待核准活動"]["value"] == "1"
    owner = DurableOperatorDomainStateRepository(pending_context.documents, "growth")
    state = owner.load(TENANT)
    original = next(row for row in state["approvals"] if row["id"] == approval)
    duplicate = dict(original)
    if damage in {"status", "both"}:
        duplicate["status"] = "approved"
    if damage in {"other-owned-ref", "both"}:
        # This second real parent also survives project_growth_read_state;
        # the duplicate must not be hidden by using an out-of-scope fake ref.
        duplicate["ref"] = other
    # Deliberate historical corruption in the owned test aggregate only.
    state["approvals"].append(duplicate)
    owner.save(TENANT, state)
    cards = _cards(pending_context)
    assert cards["待核准活動"]["value"] is None
    assert cards["待核准活動"]["availability"] == "unavailable"
