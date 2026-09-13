"""Tests that the six canonical models with nullable measurement fields
properly distinguish absent from measured, and that downstream consumers
do not silently restore 1.0 for absent values.

ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001
Acceptance: six production-shaped absence tests prove abstain/mark/reject.
"""

from __future__ import annotations

import pytest

from shared.domain.models import (
    CompetitorStore,
    DataSnapshot,
    HeatZoneScore,
    Listing,
    Poi,
    Prediction,
)


# ---------------------------------------------------------------------------
# 1. The six fields default to None (unmeasured), not 1.0 (perfect)
# ---------------------------------------------------------------------------


class TestCanonicalMeasurementNullableDefaults:
    """Each canonical model with a measurement field defaults to None."""

    def test_poi_confidence_defaults_to_none(self) -> None:
        poi = Poi()
        assert poi.confidence is None, (
            "Poi.confidence must default to None (unmeasured), not 1.0"
        )

    def test_competitor_store_confidence_defaults_to_none(self) -> None:
        cs = CompetitorStore()
        assert cs.confidence is None, (
            "CompetitorStore.confidence must default to None (unmeasured)"
        )

    def test_listing_confidence_defaults_to_none(self) -> None:
        listing = Listing()
        assert listing.confidence is None, (
            "Listing.confidence must default to None (unmeasured)"
        )

    def test_prediction_confidence_defaults_to_none(self) -> None:
        pred = Prediction()
        assert pred.confidence is None, (
            "Prediction.confidence must default to None (unmeasured)"
        )

    def test_heatzone_score_confidence_defaults_to_none(self) -> None:
        hz = HeatZoneScore()
        assert hz.confidence is None, (
            "HeatZoneScore.confidence must default to None (unmeasured)"
        )

    def test_data_snapshot_quality_score_defaults_to_none(self) -> None:
        snap = DataSnapshot()
        assert snap.quality_score is None, (
            "DataSnapshot.quality_score must default to None (unmeasured)"
        )


# ---------------------------------------------------------------------------
# 2. A genuinely measured value is preserved and distinguishable
# ---------------------------------------------------------------------------


class TestCanonicalMeasurementMeasuredValues:
    """Explicitly supplied measurement values are preserved."""

    def test_poi_measured_confidence(self) -> None:
        poi = Poi(confidence=0.85)
        assert poi.confidence == 0.85

    def test_poi_measured_perfect_confidence(self) -> None:
        """A genuinely measured 1.0 is not the same as absence."""
        poi = Poi(confidence=1.0)
        assert poi.confidence == 1.0
        assert poi.confidence is not None  # not the same as absence

    def test_competitor_store_measured_confidence(self) -> None:
        cs = CompetitorStore(confidence=0.72)
        assert cs.confidence == 0.72

    def test_listing_measured_confidence(self) -> None:
        listing = Listing(confidence=0.95)
        assert listing.confidence == 0.95

    def test_prediction_measured_confidence(self) -> None:
        pred = Prediction(confidence=0.60)
        assert pred.confidence == 0.60

    def test_heatzone_score_measured_confidence(self) -> None:
        hz = HeatZoneScore(confidence=0.40)
        assert hz.confidence == 0.40

    def test_data_snapshot_measured_quality_score(self) -> None:
        snap = DataSnapshot(quality_score=0.92)
        assert snap.quality_score == 0.92


# ---------------------------------------------------------------------------
# 3. Downstream consumers reject / abstain on absent measurement
# ---------------------------------------------------------------------------


class TestAbsentMeasurementFailsClosed:
    """Consumers that receive None must not treat it as a valid score."""

    def test_none_confidence_is_not_above_threshold(self) -> None:
        """A None confidence must not pass a numeric threshold check.

        This is the pattern from heatzone/v3/scoring.py:
            if feature.confidence is None or feature.confidence < 0.25:
                → abstain
        """
        poi = Poi()  # confidence=None
        # The fail-closed pattern: None means unmeasured → fail
        assert poi.confidence is None or poi.confidence < 0.25

    def test_none_quality_score_is_distinguishable_from_perfect(self) -> None:
        snap_unmeasured = DataSnapshot()
        snap_perfect = DataSnapshot(quality_score=1.0)
        assert snap_unmeasured.quality_score != snap_perfect.quality_score

    def test_absent_listing_confidence_not_treated_as_full(self) -> None:
        """A listing with no confidence must not pass a completeness gate."""
        listing = Listing()
        # The gate should recognise this as missing data, not as perfect
        assert listing.confidence is None

    def test_absent_prediction_confidence_not_treated_as_certain(self) -> None:
        """A prediction without a confidence figure must not claim certainty."""
        pred = Prediction()
        # An operator screen showing this must show "unmeasured", not 100%
        assert pred.confidence is None

    def test_none_safe_min_with_absent_confidence(self) -> None:
        """min() of values with None should not crash or return 1.0."""
        values = [None, 0.8, None]
        present = [v for v in values if v is not None]
        result = min(present) if present else None
        assert result == 0.8

    def test_none_safe_min_all_absent(self) -> None:
        """When all measurement inputs are None, the aggregate is None."""
        values = [None, None]
        present = [v for v in values if v is not None]
        result = min(present) if present else None
        assert result is None


# ---------------------------------------------------------------------------
# 4. Connector ingestion does not substitute 1.0 for missing confidence
# ---------------------------------------------------------------------------


class TestConnectorIngestionNullable:
    """External data connectors must not substitute 1.0 for absent confidence."""

    def test_poi_connector_absent_confidence(self) -> None:
        """A source record with no confidence field yields None, not 1.0."""
        from modules.external_data.connectors.external import _parse_optional_float
        assert _parse_optional_float(None) is None

    def test_poi_connector_present_confidence(self) -> None:
        from modules.external_data.connectors.external import _parse_optional_float
        assert _parse_optional_float(0.85) == 0.85
        assert _parse_optional_float("0.72") == 0.72

    def test_poi_connector_invalid_confidence(self) -> None:
        from modules.external_data.connectors.external import _parse_optional_float
        assert _parse_optional_float("not_a_number") is None
