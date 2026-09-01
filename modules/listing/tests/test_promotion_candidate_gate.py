"""Candidate-gate regression tests for listing promotion.

ODP-BR-LST-001 is a Hard Constraint: a listing with no address or a failed
geocode must not enter SiteScore. Three separate defects let one through
anyway, and this module pins all three shut.

1. `review_promotion` never re-ran the gate, and every accessor it used
   supplied a default for a missing value ("HZ-01", "", 1.0, 75.0), so the
   SiteScore input was fully populated regardless of what the listing actually
   contained. A listing with no address reached score_site() carrying full
   confidence and a passing demand signal.

2. The gate read geocode confidence as
   `listing.get("geocodeConfidence") or listing.get("confidence")`. A
   listing's `confidence` is *extraction* confidence -- how sure the parser is
   about the rent and area it read -- and says nothing about whether the
   address was geocoded. V1ListingRepositoryAdapter emits the address value
   under `geocode_confidence` and the listing value under `confidence`, so a
   listing whose geocode had failed (0.0) was waved through on an unrelated
   1.0 sitting one key away. Adding the gate in (1) did not close this: the
   gate itself was reading the wrong field.

3. The gate carried a dead coordinate check -- `lat` defaulted to 25.0339
   (Taipei Main Station), making `lat is None` unreachable. That was removed to
   state the real rule. Coordinates are deliberately not a gate field:
   AddressLocation defaults latitude/longitude to 0.0, the promotion payload
   does not carry them, and SiteScoreFeatureInput consumes heat_zone_id rather
   than a coordinate pair. Geocode completeness is carried by the H3 cell and
   the confidence value.

There were no tests in this package before this file, which is why none of the
three surfaced. The end-to-end classes below drive the real
`request_promotion` / `review_promotion` pair through the real adapter, so a
gate that passes only because it is being called with a hand-built dictionary
cannot look green here.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from apps.api.app.routes.listings import V1ListingRepositoryAdapter
from modules.listing.application.promotion import PromotionService
from modules.listing.domain.intake_states import (
    Actor,
    DenialCode,
    DomainValidationError,
    PrincipalRole,
    TransitionContext,
)
from modules.listing.domain.models import ListingDedupKey
from modules.listing.infrastructure.repositories import InMemoryListingRepository
from shared.domain import AddressLocation, Listing

TENANT = "tenant-a"
LISTING_ID = "LST-GATE-001"
ADDRESS_ID = "ADDR-GATE-001"
INTAKE_ID = "IN-GATE-001"


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
            "geocode_confidence": 0.92,
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


class TestGeocodeConfidenceIsReadFromTheAddress:
    """Defect 2: the gate must not read the listing's extraction confidence.

    These are the assertions the adapter-path escape would have failed. They
    are written against the field names rather than a repository so a future
    reader can see exactly which key means what.
    """

    def test_listing_confidence_does_not_satisfy_the_geocode_gate(self) -> None:
        """`confidence` is extraction confidence, not geocode confidence.

        This is the exact escape route: V1ListingRepositoryAdapter emits
        `confidence: 1.0` from the listing record while the address it belongs
        to has `geocode_confidence: 0.0`. Reading the former as a fallback for
        the latter reported a fully-confident geocode for an address that was
        never resolved.
        """
        listing = _complete_listing()
        del listing["geocodeConfidence"]
        listing["confidence"] = 1.0
        assert "geocode" in _service()._validate_listing_fields(listing)

    def test_zero_geocode_confidence_is_rejected(self) -> None:
        """0.0 is absence, not a low-but-real reading.

        AddressLocation defaults geocode_confidence to 0.0 and the persistence
        layer coerces NULL to 0.0, so nothing distinguishes "the geocoder
        returned 0.0" from "never geocoded". `to_sitescore_model_row()` already
        rejects the row on the same test, so a gate that let 0.0 through would
        only be handing SiteScore something SiteScore refuses.
        """
        listing = _complete_listing()
        listing["geocodeConfidence"] = 0.0
        assert "geocode" in _service()._validate_listing_fields(listing)

    def test_snake_case_zero_is_rejected_even_with_listing_confidence_present(
        self,
    ) -> None:
        """The adapter's exact key shape, asserted directly."""
        listing = _complete_listing()
        del listing["geocodeConfidence"]
        listing["geocode_confidence"] = 0.0
        listing["confidence"] = 1.0
        assert "geocode" in _service()._validate_listing_fields(listing)

    def test_non_numeric_geocode_confidence_is_rejected(self) -> None:
        """An unparseable value is not evidence of a geocode."""
        listing = _complete_listing()
        listing["geocodeConfidence"] = "high"
        assert "geocode" in _service()._validate_listing_fields(listing)


def _seed_repository(geocode_confidence: float) -> InMemoryListingRepository:
    """A repository holding one active, otherwise-promotable listing."""
    repo = InMemoryListingRepository()
    listing = Listing(
        listing_id=LISTING_ID,
        source_listing_id="SRC-GATE-001",
        source_id="approved-provider",
        listing_status="active",
        address_id=ADDRESS_ID,
        rent_amount=50000.0,
        area_ping=25.0,
        floor="1F",
        # Full extraction confidence, deliberately: this is the value that used
        # to stand in for the geocode confidence below.
        confidence=1.0,
    )
    address = AddressLocation(
        address_id=ADDRESS_ID,
        raw_address="台北市信義區信義路 1 號",
        normalized_address="台北市信義區信義路1號",
        geocode_confidence=geocode_confidence,
        h3_res_9="892a100d2d7ffff",
    )
    repo.save_listing(
        listing,
        address,
        ListingDedupKey(
            source_id=listing.source_id,
            source_listing_id=listing.source_listing_id,
            normalized_address=address.normalized_address,
            rent_amount=listing.rent_amount,
            area_ping=listing.area_ping,
        ),
    )
    return repo


class _PromotionRepository:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    def get_promotion(self, promotion_decision_id: str) -> dict[str, Any] | None:
        return self.records.get(promotion_decision_id)

    def list_promotions(self) -> list[dict[str, Any]]:
        return list(self.records.values())

    def save_promotion(self, promotion: dict[str, Any]) -> None:
        self.records[promotion["promotion_decision_id"]] = dict(promotion)


class _IntakeRepository:
    def __init__(self) -> None:
        self.intake = {
            "id": INTAKE_ID,
            "tenantId": TENANT,
            "matchResult": {"targetListingId": LISTING_ID},
        }

    def get_listing_intake(self, intake_id: str) -> dict[str, Any] | None:
        return self.intake if intake_id == INTAKE_ID else None


def _promotion_service(repo: InMemoryListingRepository) -> PromotionService:
    """The service wired exactly as the v1 API wires it."""
    return PromotionService(
        promotion_repository=_PromotionRepository(),
        listing_repository=V1ListingRepositoryAdapter(repo),
        intake_repository=_IntakeRepository(),
    )


def _request(service: PromotionService, key: str = "request-001") -> dict[str, Any]:
    return service.request_promotion(
        intake_id=INTAKE_ID,
        target_format_code="FORMAT-A",
        reason="符合 G2 標準店型",
        gate_snapshot_sha256="a" * 64,
        context=TransitionContext(
            actor=Actor(
                actor_id="user-proposer",
                role=PrincipalRole.EXPANSION_STAFF,
                tenant_id=TENANT,
            ),
            idempotency_key=key,
        ),
    )


def _review(
    service: PromotionService, promotion_decision_id: str, key: str = "review-001"
) -> dict[str, Any]:
    return service.review_promotion(
        promotion_decision_id=promotion_decision_id,
        decision="APPROVE",
        reason="independent review",
        risk_acknowledged=True,
        context=TransitionContext(
            actor=Actor(
                actor_id="user-reviewer",
                role=PrincipalRole.EXPANSION_MANAGER,
                tenant_id=TENANT,
            ),
            idempotency_key=key,
        ),
    )


class TestPromotionSagaFailsClosedOnGeocodeFailure:
    """End-to-end, through the adapter the v1 API actually uses.

    Everything above this point tests the gate as a function. These tests run
    the saga, because the escape was not in the gate's logic -- it was in which
    field the gate was handed. A test that builds its own dictionary cannot see
    that; only the real `V1ListingRepositoryAdapter` shape can.
    """

    def test_request_promotion_rejects_a_listing_whose_geocode_failed(self) -> None:
        """Fail closed at the earliest point: intake."""
        service = _promotion_service(_seed_repository(geocode_confidence=0.0))

        with pytest.raises(DomainValidationError) as excinfo:
            _request(service)

        assert excinfo.value.code is DenialCode.SOURCE_POLICY_DENIED
        assert "geocode" in str(excinfo.value)

    def test_review_promotion_rejects_a_geocode_that_failed_after_intake(self) -> None:
        """The whole point of re-running the gate at review.

        Intake passes against a good address, then the address degrades -- a
        re-geocode lands, a backfill clears a bad value -- before the reviewer
        approves. Trusting the intake decision here is what let an ungeocoded
        listing into SiteScore.
        """
        repo = _seed_repository(geocode_confidence=0.95)
        service = _promotion_service(repo)
        requested = _request(service)
        assert requested["status"] == "PENDING_REVIEW"

        repo.addresses[0] = replace(repo.addresses[0], geocode_confidence=0.0)

        with pytest.raises(DomainValidationError) as excinfo:
            _review(service, requested["promotion_decision_id"])

        assert excinfo.value.code is DenialCode.SOURCE_POLICY_DENIED
        assert "Candidate gate failed at promotion: missing geocode" in str(excinfo.value)
        # Defect 4: Review gate failure must not mutate approval state in-place.
        # Stored promotion record must remain PENDING_REVIEW at its original version.
        stored = service.promotion_repository.get_promotion(requested["promotion_decision_id"])
        assert stored is not None
        assert stored["status"] == "PENDING_REVIEW"
        assert stored["version"] == requested["version"]
        assert stored.get("reviewer") is None

    def test_no_candidate_is_created_when_the_gate_fails_at_review(self) -> None:
        """The observable Hard Constraint: nothing reaches SiteScore.

        `candidate count: 1` was the reviewer-reported symptom, so it is
        asserted directly rather than inferred from the raised error.
        """
        repo = _seed_repository(geocode_confidence=0.95)
        service = _promotion_service(repo)
        requested = _request(service)
        repo.addresses[0] = replace(repo.addresses[0], geocode_confidence=0.0)

        with pytest.raises(DomainValidationError):
            _review(service, requested["promotion_decision_id"])

        assert repo.list_candidates() == []
        assert repo.get_listing(LISTING_ID).listing_status == "active"
        stored = service.promotion_repository.get_promotion(requested["promotion_decision_id"])
        assert stored is not None
        assert stored["status"] == "PENDING_REVIEW"
        assert stored["version"] == requested["version"]

    def test_gate_failure_at_review_leaves_promotion_retryable(self) -> None:
        """State integrity: after a review-time gate rejection, the pending
        promotion record remains in PENDING_REVIEW and can be approved once the
        address issue is resolved."""
        repo = _seed_repository(geocode_confidence=0.95)
        service = _promotion_service(repo)
        requested = _request(service)

        # Address degrades before review
        repo.addresses[0] = replace(repo.addresses[0], geocode_confidence=0.0)
        with pytest.raises(DomainValidationError):
            _review(service, requested["promotion_decision_id"], key="review-attempt-1")

        stored = service.promotion_repository.get_promotion(requested["promotion_decision_id"])
        assert stored is not None
        assert stored["status"] == "PENDING_REVIEW"
        assert stored["version"] == requested["version"]
        assert repo.list_candidates() == []

        # Address is fixed / re-geocoded
        repo.addresses[0] = replace(repo.addresses[0], geocode_confidence=0.95)

        # Retrying review now successfully transitions through the saga
        completed = _review(service, requested["promotion_decision_id"], key="review-attempt-2")
        assert completed["status"] == "COMPLETED"
        assert len(repo.list_candidates()) == 1

    def test_missing_address_record_fails_closed_at_review(self) -> None:
        """No address at all, rather than a bad geocode.

        The adapter simply omits every address key when the record is gone, so
        this exercises absence rather than a zero value.
        """
        repo = _seed_repository(geocode_confidence=0.95)
        service = _promotion_service(repo)
        requested = _request(service)
        repo.addresses.clear()

        with pytest.raises(DomainValidationError) as excinfo:
            _review(service, requested["promotion_decision_id"])

        assert excinfo.value.code is DenialCode.SOURCE_POLICY_DENIED
        assert repo.list_candidates() == []
        stored = service.promotion_repository.get_promotion(requested["promotion_decision_id"])
        assert stored is not None
        assert stored["status"] == "PENDING_REVIEW"
        assert stored["version"] == requested["version"]


class TestPromotionSagaStillCompletesForAGeocodedListing:
    """The gate has to stay passable, or it is just an outage.

    Paired with the class above: together they show the change rejects the
    ungeocoded listing specifically, not every listing.
    """

    def test_geocoded_listing_promotes_to_a_candidate(self) -> None:
        repo = _seed_repository(geocode_confidence=0.95)
        service = _promotion_service(repo)

        requested = _request(service)
        completed = _review(service, requested["promotion_decision_id"])

        assert completed["status"] == "COMPLETED"
        assert len(repo.list_candidates()) == 1

    def test_candidate_carries_the_address_confidence_not_the_listing_one(self) -> None:
        """0.95 from the address, never 1.0 from the listing record.

        If this ever reads 1.0, the extraction-confidence fallback is back and
        the SiteScore input is being told the geocode is perfect.
        """
        repo = _seed_repository(geocode_confidence=0.95)
        service = _promotion_service(repo)

        requested = _request(service)
        _review(service, requested["promotion_decision_id"])

        candidate = repo.list_candidates()[0]
        assert candidate.address.geocode_confidence == 0.95


class TestAdapterKeepsGeocodeConfidenceDistinct:
    """The adapter's own field mapping, asserted without the saga."""

    def test_adapter_emits_both_spellings_of_the_address_confidence(self) -> None:
        repo = _seed_repository(geocode_confidence=0.0)
        adapted = V1ListingRepositoryAdapter(repo).get_listing(LISTING_ID)

        assert adapted.get("geocode_confidence") == 0.0
        assert adapted.get("geocodeConfidence") == 0.0

    def test_adapter_keeps_listing_confidence_under_its_own_key(self) -> None:
        """Both values survive the mapping; they just stop being interchangeable."""
        repo = _seed_repository(geocode_confidence=0.0)
        adapted = V1ListingRepositoryAdapter(repo).get_listing(LISTING_ID)

        assert adapted.get("confidence") == 1.0
        assert adapted.get("geocodeConfidence") == 0.0


class TestDomainListingBranchAppliesTheSameGate:
    """`review_promotion` also accepts a domain `Listing` rather than a mapping.

    That branch reads the address off a separate record, so it cannot reuse
    `_validate_listing_fields`. It has to ask the same questions anyway.
    """

    def test_domain_listing_with_failed_geocode_is_rejected(self) -> None:
        repo = _seed_repository(geocode_confidence=0.0)
        service = PromotionService(
            promotion_repository=_PromotionRepository(),
            listing_repository=repo,
            intake_repository=_IntakeRepository(),
        )

        with pytest.raises(DomainValidationError) as excinfo:
            service._assert_promotable(repo.get_listing(LISTING_ID))

        assert excinfo.value.code is DenialCode.SOURCE_POLICY_DENIED
        assert "geocode" in str(excinfo.value)

    def test_domain_listing_without_address_reports_every_address_field(self) -> None:
        repo = _seed_repository(geocode_confidence=0.95)
        listing = repo.get_listing(LISTING_ID)
        repo.addresses.clear()
        service = PromotionService(
            promotion_repository=_PromotionRepository(),
            listing_repository=repo,
            intake_repository=_IntakeRepository(),
        )

        with pytest.raises(DomainValidationError) as excinfo:
            service._assert_promotable(listing)

        message = str(excinfo.value)
        assert "address" in message
        assert "H3" in message
        assert "geocode" in message

    def test_geocoded_domain_listing_returns_its_address(self) -> None:
        """The gate hands the checked address back so the derivation below it
        cannot re-fetch a different one."""
        repo = _seed_repository(geocode_confidence=0.95)
        service = PromotionService(
            promotion_repository=_PromotionRepository(),
            listing_repository=repo,
            intake_repository=_IntakeRepository(),
        )

        address = service._assert_promotable(repo.get_listing(LISTING_ID))

        assert address is not None
        assert address.geocode_confidence == 0.95


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
            'or listing.get("confidence")': (
                "listing extraction confidence read as address geocode confidence"
            ),
        }

        found = [f"{token!r} -- {why}" for token, why in forbidden.items() if token in source]
        assert not found, "fail-open substitutions reintroduced: " + "; ".join(found)
