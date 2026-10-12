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
