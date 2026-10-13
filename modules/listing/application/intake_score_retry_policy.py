"""Explicit retry configuration, distinct from the enqueue deadline.

The reliability contract specifies an enqueue budget. It does not approve an
inference backoff. Operators must bind a separate policy before reserving score
work. Parsing this configuration is not evidence of policy or model approval.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

RETRY_POLICY_ENV = "ODP_INTAKE_SCORE_RETRY_POLICY_JSON"


def score_retry_policy() -> dict[str, Any]:
    from modules.listing.application.intake_score_delivery import ScoreDeliveryUnavailable

    try:
        policy = json.loads(os.environ.get(RETRY_POLICY_ENV, ""))
        if not isinstance(policy, dict) or set(policy) != {
            "policy_id", "version", "max_attempts", "backoff_seconds",
        }:
            raise ValueError
        if not isinstance(policy["policy_id"], str) or not policy["policy_id"].strip():
            raise ValueError
        if type(policy["version"]) is not int or policy["version"] < 1:
            raise ValueError
        attempts = policy["max_attempts"]
        # The existing canonical workflow has a five-attempt ceiling; a policy
        # may reduce it, but may not silently introduce a budget override.
        if type(attempts) is not int or not 1 <= attempts <= 5:
            raise ValueError
        delays = policy["backoff_seconds"]
        if not isinstance(delays, list) or len(delays) != attempts - 1:
            raise ValueError
        if any(type(delay) not in (int, float) or not math.isfinite(delay)
               or delay <= 0 or delay > 86400 for delay in delays):
            raise ValueError
    except (ValueError, TypeError, OverflowError) as exc:
        raise ScoreDeliveryUnavailable("SITESCORE_RETRY_POLICY_UNAVAILABLE") from exc
    return policy
