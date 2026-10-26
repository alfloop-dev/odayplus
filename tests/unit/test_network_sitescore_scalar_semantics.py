"""A confidence measurement cannot silently become a suitability ranking."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from modules.opsboard.application.network_scoring import NetworkScoringService
from modules.sitescore.domain.scoring import Interval, SiteScoreRecommendation, SiteScoreReport
from shared.domain.models import MEASUREMENT_MEASURED, MEASUREMENT_UNMEASURED


def _report(candidate_id, confidence, recommendation):
    at = datetime(2026, 10, 9, tzinfo=UTC)
    band = Interval(100000, 150000, 200000)
    return SiteScoreReport(
        sitescore_run_id=f"synthetic-run-{candidate_id}", candidate_site_id=candidate_id,
        target_format_code="synthetic-format", recommendation=recommendation,
        m1=band, m3=band, m6=band, m12=band, payback_period=Interval(24, 36, 48),
        payback_p50_months=36, rent_reasonableness=0.6, cannibalization_risk=0.2,
        comparable_stores=3, key_positive_factors=(), key_negative_factors=(), confidence=confidence,
        model_version="synthetic-model", feature_version="synthetic-view",
        feature_snapshot_time=at, prediction_origin_time=at, scored_at=at,
    )


def test_canonical_report_preserves_confidence_without_inventing_scalar_score():
    report = _report("CS-SYNTHETIC-A", 0.99, SiteScoreRecommendation.REJECT)
    card = NetworkScoringService._scorecard_from_report(report)
    assert card["score"] is None
    assert card["confidence"] == 0.99
    assert card["confidenceStatus"] == MEASUREMENT_MEASURED
    assert card["recommendation"] == "REJECT"
    assert card["band"] == report.m12.to_dict()


@pytest.mark.parametrize("confidence", [0.0, 0.73])
def test_confidence_measurement_status_distinguishes_absence_from_measured_zero(confidence):
    measured = _report("CS-SYNTHETIC-CONFIDENCE", confidence, SiteScoreRecommendation.WAIT)
    unmeasured = replace(
        measured, candidate_site_id="CS-SYNTHETIC-UNMEASURED",
        confidence_status=MEASUREMENT_UNMEASURED,
    )
    measured_card = NetworkScoringService._scorecard_from_report(measured)
    unmeasured_card = NetworkScoringService._scorecard_from_report(unmeasured)

    assert measured_card["confidence"] == confidence
    assert measured_card["confidenceStatus"] == MEASUREMENT_MEASURED
    assert unmeasured_card["confidence"] is None
    assert unmeasured_card["confidenceStatus"] == MEASUREMENT_UNMEASURED
    assert measured_card["score"] is unmeasured_card["score"] is None
    assert measured_card["recommendation"] == unmeasured_card["recommendation"] == "WAIT"
    cards = [measured_card, unmeasured_card]
    service = NetworkScoringService(initial_state={
        "scores": {card["id"]: card for card in cards},
        "compareSet": [card["id"] for card in cards],
    }, seed_fixtures=False)
    compared = service._compare(cards)
    confidence_values = next(row for row in compared["metrics"] if row["key"] == "confidence")["values"]
    assert [value["text"] for value in confidence_values] == [str(confidence), "—"]


def test_missing_scalar_score_does_not_assign_rank_priority_or_primary():
    cards = [NetworkScoringService._scorecard_from_report(_report("CS-SYNTHETIC-B", 0.99, SiteScoreRecommendation.REJECT)),
             NetworkScoringService._scorecard_from_report(_report("CS-SYNTHETIC-A", 0.6, SiteScoreRecommendation.GO))]
    service = NetworkScoringService(initial_state={"scores": {card["id"]: card for card in cards},
                                                    "compareSet": [card["id"] for card in cards]}, seed_fixtures=False)
    ordered = service._sorted_scorecards()
    assert [card["id"] for card in ordered] == ["CS-SYNTHETIC-A", "CS-SYNTHETIC-B"]
    assert all(row["rank"] is None and row["priority"] is None for row in service._batch_results(ordered))
    compared = service._compare(ordered)
    assert not compared["empty"]
    assert compared["recommendation"] is None
    assert all(column["priority"] is None and not column["isBest"] for column in compared["columns"])
    assert {column["recommendation"] for column in compared["columns"]} == {"GO", "REJECT"}
    values = next(row for row in compared["metrics"] if row["key"] == "sitescore")["values"]
    assert [row["text"] for row in values] == ["— GO", "— REJECT"]
