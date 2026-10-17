"""A retry configuration is explicit input, never inferred production approval."""

import json

import pytest

from modules.listing.application.intake_score_delivery import ScoreDeliveryUnavailable
from modules.listing.application.intake_score_retry_policy import (
    RETRY_POLICY_ENV,
    score_retry_policy,
)

POLICY = {"policy_id": "synthetic-retry-policy", "version": 1,
          "max_attempts": 5, "backoff_seconds": [10, 30, 120, 600]}


def test_missing_retry_policy_fails_closed(monkeypatch):
    monkeypatch.delenv(RETRY_POLICY_ENV, raising=False)
    with pytest.raises(ScoreDeliveryUnavailable, match="SITESCORE_RETRY_POLICY_UNAVAILABLE"):
        score_retry_policy()


@pytest.mark.parametrize("replacement", [
    {"policy_id": " "}, {"version": True}, {"version": 0},
    {"max_attempts": True}, {"max_attempts": 0}, {"max_attempts": 6},
    {"max_attempts": 5.0}, {"backoff_seconds": [10, 30, 120, 600, 1800]},
    {"backoff_seconds": [10, 30, 120]}, {"backoff_seconds": [0, 30, 120, 600]},
    {"backoff_seconds": [-1, 30, 120, 600]}, {"backoff_seconds": [True, 30, 120, 600]},
    {"backoff_seconds": [float("nan"), 30, 120, 600]},
    {"backoff_seconds": [float("inf"), 30, 120, 600]},
    {"backoff_seconds": [86401, 30, 120, 600]}, {"unexpected_approval": "not-authority"},
])
def test_invalid_policy_cannot_create_a_hidden_attempt_or_unbounded_delay(monkeypatch, replacement):
    monkeypatch.setenv(RETRY_POLICY_ENV, json.dumps({**POLICY, **replacement}))
    with pytest.raises(ScoreDeliveryUnavailable, match="SITESCORE_RETRY_POLICY_UNAVAILABLE"):
        score_retry_policy()


@pytest.mark.parametrize("raw", ["null", "[]", "{}", "not-json"])
def test_non_policy_json_is_rejected(monkeypatch, raw):
    monkeypatch.setenv(RETRY_POLICY_ENV, raw)
    with pytest.raises(ScoreDeliveryUnavailable, match="SITESCORE_RETRY_POLICY_UNAVAILABLE"):
        score_retry_policy()


@pytest.mark.parametrize("attempts,delays", [(1, []), (2, [0.5]), (5, [10, 30, 120, 600])])
def test_explicit_budget_has_one_fewer_delays_than_attempts(monkeypatch, attempts, delays):
    policy = {**POLICY, "max_attempts": attempts, "backoff_seconds": delays}
    monkeypatch.setenv(RETRY_POLICY_ENV, json.dumps(policy))
    assert score_retry_policy() == policy
