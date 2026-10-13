"""Current readiness is separate from historical reports and execution authority.

All records and ports in this file are local synthetic doubles; reads never
call inference or an external source/model registry.
"""
from typing import Any

import pytest

from modules.opsboard.application.network_scoring import (
    NetworkScoringGateError,
    NetworkScoringService,
)
from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository
from tests.unit.test_candidate_score_context import context as context
from tests.unit.test_intake_score_admission import admission as admission
from tests.unit.test_network_candidate_report_projection import _client, _report
from tests.unit.test_network_scoring_gate_measurement_contract import (
    TENANT,
    NeverInfer,
    ReadOnlyListings,
    ReadOnlyReports,
    draft,
)


@pytest.mark.parametrize("verdict, passed", [
    (None, False), ([], True), (["recorded-floor-failure"], False),
    ("", False), ((), False), ({}, False), ([None], False), ([False], False),
])
def test_canonical_data_gate_uses_recorded_verdict_instead_of_default_flags(
    verdict: Any, passed: bool,
) -> None:
    record = draft()
    object.__setattr__(record.listing, "hard_rule_failures", verdict)
    runtime = NeverInfer()
    service = NetworkScoringService(
        seed_fixtures=False, require_canonical=True, tenant_id=TENANT,
        listing_repository=ReadOnlyListings(record), sitescore_repository=ReadOnlyReports(),
        model_runtime=runtime,
    )
    row = service.snapshot()["candidates"][0]
    assert row["gate"]["passed"] is passed
    assert (row["gate"]["okCount"], row["gate"]["totalCount"]) == (6 if passed else 5, 6)
    assert runtime.calls == 0


def test_missing_recorded_verdict_is_unmeasured_and_blocks_data_gate() -> None:
    record = draft()
    object.__delattr__(record.listing, "hard_rule_failures")
    row = NetworkScoringService(
        seed_fixtures=False, require_canonical=True, tenant_id=TENANT,
        listing_repository=ReadOnlyListings(record), sitescore_repository=ReadOnlyReports(),
        model_runtime=NeverInfer(),
    ).snapshot()["candidates"][0]
    assert row["readEvidence"]["fields"]["hardRule"]["availability"] == "unmeasured"
    assert row["gate"]["passed"] is False
    assert row["gate"]["okCount"] == 5


def test_qualified_data_does_not_claim_current_source_pit_or_model_approval_without_port() -> None:
    record = draft()
    record.model_version = "historical-approved-looking-model"
    runtime = NeverInfer()
    service = NetworkScoringService(
        seed_fixtures=False, require_canonical=True, tenant_id=TENANT,
        listing_repository=ReadOnlyListings(record), sitescore_repository=ReadOnlyReports(),
        model_runtime=runtime,
    )
    snapshot = service.snapshot()
    row = snapshot["candidates"][0]
    assert row["gate"]["passed"] is True
    assert row["currentAdmission"]["currentInputs"]["state"] == "unverified"
    assert row["currentAdmission"]["source"]["state"] == "unverified"
    assert row["currentAdmission"]["pit"]["state"] == "unverified"
    assert row["currentAdmission"]["model"] == {
        "state": "unverified", "reasonCode": "SITESCORE_CURRENT_MODEL_ADMISSION_UNVERIFIED",
    }
    assert row["modelVersion"] is None
    assert snapshot["modelVersion"] is None
    assert runtime.calls == 0


def test_explicit_fixture_branch_keeps_its_original_gate_and_model_contract() -> None:
    snapshot = NetworkScoringService().snapshot()
    row = next(item for item in snapshot["candidates"] if item["id"] == "CS-1001")
    assert row["gate"]["passed"] is True
    assert row["modelVersion"] == "SiteScore v2.3"
    assert "currentAdmission" not in row


@pytest.mark.parametrize("verdict,passed", [(None, False), ([], True), (["current-recorded-failure"], False)])
def test_actual_scoring_snapshot_uses_current_metadata_verdict_not_historical_draft(context, verdict, passed):
    context.store.records[context.metadata_key] = {"hardRuleFailures": verdict}
    runtime = NeverInfer()
    scorer = NetworkScoringService(
        seed_fixtures=False, require_canonical=True, tenant_id=context.tenant,
        listing_repository=context.repository, sitescore_repository=ReadOnlyReports(),
        model_runtime=runtime, current_context_reader=context.reader,
    )
    with _client(lambda: scorer) as client:
        response = client.get("/network-scoring")
    assert response.status_code == 200
    row = response.json()["candidates"][0]
    assert row["gate"]["passed"] is passed
    assert row["readEvidence"]["fields"]["hardRule"]["value"] == verdict
    assert row["currentAdmission"]["currentInputs"]["state"] == ("verified" if passed else "blocked")
    assert row["currentAdmission"]["model"]["state"] == "unverified"
    assert context.record.listing.hard_rule_failures == []
    if not passed:
        with pytest.raises(NetworkScoringGateError):
            scorer.score_candidate(
                candidate_id=row["id"], actor_role_id="expansionManager", actor_name=None,
                idempotency_key=None, correlation_id=None,
            )
    assert runtime.calls == 0


def test_historical_own_report_stays_readable_when_current_verdict_is_withdrawn(context):
    context.store.records[context.metadata_key] = {"hardRuleFailures": None}
    candidate_id = context.record.candidate_site.candidate_site_id
    reports = InMemorySiteScoreRepository()
    report = reports.save_report(_report(candidate_id, "historical-own-report-model"))
    runtime = NeverInfer()
    scorer = NetworkScoringService(
        seed_fixtures=False, require_canonical=True, tenant_id=context.tenant,
        listing_repository=context.repository, sitescore_repository=reports,
        model_runtime=runtime, current_context_reader=context.reader,
    )
    before = reports.history(candidate_id)
    with _client(lambda: scorer) as client:
        response = client.get("/network-scoring")
    assert response.status_code == 200
    snapshot = response.json()
    row = snapshot["candidates"][0]
    assert row["gate"]["passed"] is False
    assert row["modelVersion"] == snapshot["modelVersion"] == "historical-own-report-model"
    assert row["scored"] is True and row["score"] is None
    assert row["currentAdmission"]["model"] == {
        "state": "unverified", "reasonCode": "SITESCORE_CURRENT_MODEL_ADMISSION_UNVERIFIED",
    }
    assert row["currentAdmission"]["currentInputs"]["reasonCode"] == "SITESCORE_CURRENT_HARD_RULE_GATE_DENIED"
    assert snapshot["scorecards"][0]["reportId"] == report.report_id
    assert reports.history(candidate_id) == before
    assert runtime.calls == 0


def test_current_amount_change_is_visible_but_does_not_refresh_immutable_candidate(context):
    from tests.unit.test_candidate_score_context import _listing_with

    context.repository.listing = _listing_with(context.repository.listing, rent_amount=43210.25)
    scorer = NetworkScoringService(
        seed_fixtures=False, require_canonical=True, tenant_id=context.tenant,
        listing_repository=context.repository, sitescore_repository=ReadOnlyReports(),
        model_runtime=NeverInfer(), current_context_reader=context.reader,
    )
    row = scorer.snapshot()["candidates"][0]
    assert row["gate"]["passed"] is True  # complete current data is not matching frozen input
    assert row["readEvidence"]["fields"]["rent"]["value"] == 43210.25
    assert row["currentAdmission"]["currentInputs"]["reasonCode"] == "SITESCORE_CANDIDATE_INPUT_STALE"
    assert context.record.listing.rent_amount == 54321.5
    assert row["currentAdmission"]["model"]["state"] == "unverified"
