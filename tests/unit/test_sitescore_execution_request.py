"""Fresh execution owner must replace canonical inline inference."""
from typing import Any

import pytest

from modules.opsboard.application.network_scoring import (
    NetworkScoringRuntimeUnavailable,
    NetworkScoringService,
)
from tests.unit.test_network_scoring_gate_measurement_contract import (
    NeverInfer,
    ReadOnlyListings,
    ReadOnlyReports,
    draft,
)


def test_canonical_execute_never_invokes_inline_report_executor_without_request_owner(monkeypatch):
    record = draft()
    scorer = NetworkScoringService(
        seed_fixtures=False, require_canonical=True,
        listing_repository=ReadOnlyListings(record), sitescore_repository=ReadOnlyReports(),
        model_runtime=NeverInfer(),
    )
    calls: list[Any] = []

    def inline(candidate):
        calls.append(candidate)
        raise AssertionError("canonical execution bypassed authoritative request/job owner")

    monkeypatch.setattr(scorer, "_run_canonical_score", inline)
    with pytest.raises(NetworkScoringRuntimeUnavailable):
        scorer.score_candidate(
            candidate_id=record.candidate_site.candidate_site_id,
            actor_role_id="expansionManager", actor_name=None,
            idempotency_key="synthetic-no-authority", correlation_id=None,
        )
    assert calls == []
