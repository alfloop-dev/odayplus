from datetime import UTC, datetime

import pytest

from modules.opsboard.application.today_metrics import (
    ROLE_CARD_LABELS,
    TODAY_CARD_LABELS,
    build_today_metrics,
)

NOW = datetime(2026, 10, 12, 10, tzinfo=UTC)


def _issue(**changes):
    return {
        "storeId": "test-store",
        "status": "new",
        "severity": "critical",
        "ownerName": "",
        "slaDueAt": "2026-10-12T09:00:00+00:00",
        **changes,
    }


def _cards(issues, approvals=2):
    cards = build_today_metrics(issues=issues, approvals_count=approvals, now=NOW)
    assert tuple(card["label"] for card in cards) == TODAY_CARD_LABELS
    return {card["label"]: card for card in cards}


def test_issue_facts_do_not_count_closed_or_observing_as_outcome_ready():
    cards = _cards([
        _issue(),
        _issue(status="closed"),
        _issue(status="observing", ownerName="Operator", severity="medium"),
        _issue(status="outcomeready", ownerName="Operator", slaDueAt="2026-10-12T12:00:00+00:00"),
    ])
    assert cards["高風險未指派"]["value"] == "1"
    assert cards["已逾期 Issue"]["value"] == "2"
    assert cards["成效待判斷"]["value"] == "1"
    assert cards["待我核准"]["value"] == "2"
    assert cards["即將逾期"]["value"] is None
    assert cards["需升級門市"]["value"] == "0"


def test_unavailable_issue_source_is_distinct_from_successful_empty_read():
    missing = _cards(None, approvals=None)
    assert all(card["value"] is None and card["availability"] == "unavailable" for card in missing.values())
    empty = _cards([], approvals=0)
    assert all(card["value"] == "0" and card["availability"] == "available" for card in empty.values())


def test_unknown_issue_lifecycle_does_not_create_plausible_zero_counts():
    cards = _cards([_issue(status="not-a-real-issue-status")])
    assert all(cards[label]["value"] is None for label in TODAY_CARD_LABELS if label != "待我核准")


@pytest.mark.parametrize("deadline", [None, "bad-date", "2026-10-12T09:00:00"])
def test_missing_or_naive_deadline_does_not_become_a_zero_overdue_count(deadline):
    assert _cards([_issue(slaDueAt=deadline)])["已逾期 Issue"]["value"] is None


def test_unassigned_requires_explicit_owner_and_high_risk_facts():
    assert _cards([_issue(ownerName=None)])["高風險未指派"]["value"] is None
    assert _cards([_issue(severity="unknown")])["高風險未指派"]["value"] is None


def test_store_escalation_uses_committed_facts_and_deduplicates_stores():
    assert _cards([_issue(status="escalated"), _issue(status="escalated")])["需升級門市"]["value"] == "1"


def test_high_severity_is_not_the_prototype_critical_unassigned_metric():
    assert _cards([_issue(severity="high")])["高風險未指派"]["value"] == "0"


@pytest.mark.parametrize("role_id", [role for role in ROLE_CARD_LABELS if role != "ops-lead"])
def test_other_personas_do_not_inherit_operations_cards_or_example_numbers(role_id):
    cards = build_today_metrics(issues=[_issue()], approvals_count=2, now=NOW, role_id=role_id)
    assert tuple(card["label"] for card in cards) == ROLE_CARD_LABELS[role_id]
    assert all(card["value"] is None for card in cards if card["label"] != "待核准項")


def test_due_soon_does_not_invent_a_two_hour_or_utc_business_day_policy():
    assert _cards([_issue(slaDueAt="2026-10-12T11:00:00+00:00")])["即將逾期"]["value"] is None


def _growth_cards(actions, approvals=()):
    facts = None if actions is None else {"actions": actions, "approvals": list(approvals)}
    return {row["label"]: row for row in build_today_metrics(
        issues=[_issue()], approvals_count=99, now=NOW,
        role_id="marketing-manager", growth_facts=facts,
    )}


def _action(identity, status="DRAFT", kind="offpeak", **changes):
    return {"id": identity, "status": status, "kind": kind, **changes}


def test_marketing_uses_actual_action_lifecycle_and_mature_review_not_other_shell_counts():
    cards = _growth_cards([
        _action("draft-offpeak"), _action("draft-winback", kind="winback"),
        _action("draft-priceops", kind="priceops"),
        _action("pending", status="PENDING_APPROVAL", approvalId="own-approval"),
        _action("observing", status="OBSERVING", observation={"status": "OBSERVING"}),
        _action("ready", status="OUTCOME_READY", observation={"status": "MATURE"}),
        _action("closed", status="CLOSED", observation={"status": "MATURE"}),
    ], [{"id": "own-approval", "ref": "pending", "status": "pending"}])
    assert cards["促銷草稿"]["value"] == "3"
    assert cards["待核准活動"]["value"] == "1"
    assert cards["成效待判斷"]["value"] == "1"
    # A draft kind is not a measured Forecast candidate or lapsed-member feed.
    assert cards["離峰機會"]["value"] is None
    assert cards["會員召回候選"]["value"] is None


def test_marketing_missing_owner_is_distinct_from_successful_empty_read():
    assert all(row["value"] is None for row in _growth_cards(None).values())
    cards = _growth_cards([])
    for label in ("促銷草稿", "待核准活動", "成效待判斷"):
        assert cards[label]["value"] == "0" and cards[label]["availability"] == "available"
    for label in ("離峰機會", "會員召回候選"):
        assert cards[label]["value"] is None and cards[label]["availability"] == "unavailable"


@pytest.mark.parametrize("damage", [
    {"id": None}, {"status": "candidate"}, {"status": []}, {"kind": None}, {"kind": "unknown"},
])
def test_marketing_malformed_action_does_not_create_plausible_counts(damage):
    cards = _growth_cards([_action("declared-unit", **damage)])
    assert all(row["value"] is None for row in cards.values())


def test_marketing_duplicate_action_identity_is_not_double_counted():
    assert all(row["value"] is None for row in _growth_cards([_action("one"), _action("one")]).values())


@pytest.mark.parametrize("approvals", [[], [{"id": "approval", "ref": "foreign", "status": "pending"}],
    [{"id": "approval", "ref": "own", "status": "approved"}],
    [{"id": "approval", "ref": "own", "status": "pending"}] * 2,
    [{"id": "approval", "ref": "own", "status": "pending"},
     {"id": "approval", "ref": "own", "status": "approved"}],
    [{"id": "approval", "ref": "own", "status": "pending"},
     {"id": "approval", "ref": "foreign", "status": "pending"}],
    [{"id": "approval", "ref": "own", "status": "pending"},
     {"id": "approval", "ref": "foreign", "status": "approved"}],
])
def test_marketing_pending_requires_the_actual_unique_pending_approval_parent(approvals):
    cards = _growth_cards([_action("own", status="PENDING_APPROVAL", approvalId="approval")], approvals)
    assert cards["待核准活動"]["value"] is None
    assert cards["促銷草稿"]["value"] == "0"


@pytest.mark.parametrize("observation", [None, {}, {"status": "UNAVAILABLE"}, {"status": "OBSERVING"}])
def test_marketing_legacy_ready_without_mature_recorded_window_is_unknown(observation):
    cards = _growth_cards([_action("legacy", status="OUTCOME_READY", observation=observation)])
    assert cards["成效待判斷"]["value"] is None
    assert cards["促銷草稿"]["value"] == "0"
