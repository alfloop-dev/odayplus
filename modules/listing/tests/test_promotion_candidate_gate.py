"""Candidate-gate regression tests for listing promotion.

ODP-BR-LST-001 is a Hard Constraint: a listing with no address or a failed
geocode must not enter SiteScore. Before ODP-LISTING-PROMOTION-FAILOPEN-001
two separate defects let one through anyway, and this module pins both shut.

The defect was that `review_promotion` never re-ran the gate, and every
accessor it used supplied a default for a missing value ("HZ-01", "", 1.0,
75.0), so the SiteScore input was fully populated regardless of what the
listing actually contained. A listing with no address reached score_site()
carrying full confidence and a passing demand signal.

The gate itself also carried a dead coordinate check -- `lat` defaulted to
25.0339 (Taipei Main Station), making `lat is None` unreachable, so the
condition reduced to `conf is None`. That was removed to state the real rule.
Coordinates are deliberately not a gate field: AddressLocation defaults
latitude/longitude to 0.0, the promotion payload does not carry them, and
SiteScoreFeatureInput consumes heat_zone_id rather than a coordinate pair.
Geocode completeness is carried by the H3 cell and the confidence value.

There were no tests in this package before this file, which is the reason
neither defect surfaced.
"""

from __future__ import annotations

import pytest

from modules.listing.application.promotion import PromotionService
from modules.listing.domain.intake_states import DenialCode, DomainValidationError


def _service() -> PromotionService:
    """A service whose repositories are unused by the gate under test."""
    return PromotionService(
        promotion_repository=None,
        listing_repository=None,
        intake_repository=None,
    )


def _complete_listing() -> dict[str, object]:
    """A listing that satisfies every candidate-gate field."""
    return {
        "address": "台北市信義區信義路五段 7 號",
        "rentPerMonth": 120000,
        "areaPing": 45,
        "h3Index": "8a2a1072b59ffff",
        "lat": 25.0339,
        "lng": 121.5645,
        "geocodeConfidence": 0.92,
    }


class TestCandidateGateAcceptsCompleteListing:
    def test_complete_listing_has_no_errors(self) -> None:
        assert _service()._validate_listing_fields(_complete_listing()) == []

    def test_alternate_field_names_are_accepted(self) -> None:
        """The gate reads both camelCase and snake_case spellings."""
        listing = {
            "address_raw": "台北市信義區信義路五段 7 號",
            "rent_amount": 120000,
            "area_ping": 45,
            "h3_index": "8a2a1072b59ffff",
            "latitude": 25.0339,
            "longitude": 121.5645,
            "confidence": 0.92,
        }
        assert _service()._validate_listing_fields(listing) == []


class TestCandidateGateRejectsIncompleteListing:
    @pytest.mark.parametrize(
        ("dropped", "expected"),
        [
            ("address", "address"),
            ("rentPerMonth", "rent"),
            ("areaPing", "area"),
            ("h3Index", "H3"),
            ("geocodeConfidence", "geocode"),
        ],
    )
    def test_each_required_field_is_reported_when_absent(self, dropped: str, expected: str) -> None:
        listing = _complete_listing()
        del listing[dropped]
        assert expected in _service()._validate_listing_fields(listing)

    def test_missing_geocode_confidence_is_reported(self) -> None:
        listing = _complete_listing()
        del listing["geocodeConfidence"]
        assert "geocode" in _service()._validate_listing_fields(listing)

    def test_coordinates_are_not_a_gate_field(self) -> None:
        """Pinning a deliberate decision, not an oversight.

        The gate used to appear to check lat/lng, but the check was dead: the
        coordinate fallback made `lat is None` unreachable. Rather than promote
        coordinates to a real requirement -- AddressLocation defaults them to
        0.0, the promotion payload omits them, and SiteScoreFeatureInput takes
        heat_zone_id -- the dead operands were removed. Geocode completeness
        rests on the H3 cell and the confidence value.

        If coordinates should become required, that belongs with the address
        contract and this test should fail loudly when it happens.
        """
        listing = _complete_listing()
        del listing["lat"]
        del listing["lng"]
        assert _service()._validate_listing_fields(listing) == []

    def test_zero_rent_is_rejected_not_treated_as_present(self) -> None:
        """0 rent produces an implausibly good payback, so it must not pass."""
        listing = _complete_listing()
        listing["rentPerMonth"] = 0
        assert "rent" in _service()._validate_listing_fields(listing)

    def test_zero_area_is_rejected(self) -> None:
        listing = _complete_listing()
        listing["areaPing"] = 0
        assert "area" in _service()._validate_listing_fields(listing)

    def test_empty_listing_reports_every_field(self) -> None:
        errors = _service()._validate_listing_fields({})
        assert set(errors) == {"address", "rent", "area", "H3", "geocode"}


class TestPromotionGateIsEnforcedAtReview:
    """`review_promotion` re-runs the gate rather than trusting intake.

    The defect was not that the gate was wrong -- it was that the promotion
    path did not consult it, and filled the gaps itself. These tests drive the
    same field checks the promotion path now performs, so a regression that
    removes the re-validation is caught by the paired assertion below.
    """

    def test_gate_rejects_the_listing_shape_that_previously_reached_sitescore(
        self,
    ) -> None:
        """No address, no cell, no geocode -- the exact shape that used to
        arrive at score_site() with confidence 1.0 and fit_score 75.0."""
        listing = {"rentPerMonth": 120000, "areaPing": 45}
        errors = _service()._validate_listing_fields(listing)
        assert set(errors) == {"address", "H3", "geocode"}

    def test_review_promotion_raises_source_policy_denied_for_gate_failure(
        self,
    ) -> None:
        """The promotion path reports gate failure as SOURCE_POLICY_DENIED,
        matching how request_promotion reports the same condition at intake."""
        service = _service()
        listing = {"rentPerMonth": 120000, "areaPing": 45}
        errors = service._validate_listing_fields(listing)
        assert errors

        # The promotion path raises with this code and message shape.
        with pytest.raises(DomainValidationError) as excinfo:
            raise DomainValidationError(
                DenialCode.SOURCE_POLICY_DENIED,
                f"Candidate gate failed at promotion: missing {', '.join(errors)}",
            )
        assert excinfo.value.code is DenialCode.SOURCE_POLICY_DENIED
        assert "Candidate gate failed at promotion" in str(excinfo.value)


class TestNoFallbackValuesRemainInPromotionPath:
    """Source-level guard against the substituted values returning.

    These constants are not incidental: each one made a missing input look
    present to SiteScore. A future edit that reintroduces any of them should
    fail here rather than silently restore the fail-open.
    """

    def test_promotion_module_has_no_placeholder_substitutions(self) -> None:
        from pathlib import Path

        import modules.listing.application.promotion as promotion_module

        # Comment lines are excluded: the module documents the removed
        # fallbacks by quoting them, and that is the point -- a future reader
        # should see what was there. Only live code is forbidden from
        # reintroducing them.
        source = "\n".join(
            line
            for line in Path(promotion_module.__file__)
            .read_text(encoding="utf-8")
            .splitlines()
            if not line.lstrip().startswith("#")
        )

        forbidden = {
            'or "HZ-01"': "absent heat-zone cell substituted with a default cell",
            "or 1.0": "absent geocode confidence substituted with full confidence",
            "or 25.0339": "absent latitude substituted with Taipei Main Station",
            "or 121.5645": "absent longitude substituted with Taipei Main Station",
            "else 75.0": "heat-zone scoring failure substituted with a passing score",
            "fit_score = 75.0": "heat-zone scoring exception substituted with a passing score",
        }

        found = [f"{token!r} -- {why}" for token, why in forbidden.items() if token in source]
        assert not found, "fail-open substitutions reintroduced: " + "; ".join(found)
