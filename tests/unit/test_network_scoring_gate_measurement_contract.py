"""Finite recorded measurements in the existing six-field scoring Gate.

Inputs are synthetic in-memory records. No SQL, model inference or production
approval is exercised. A completed historical report is not eligibility.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from modules.listing.domain.models import CandidateSiteDraft
from modules.opsboard.application.network_scoring import (
    NetworkScoringGateError,
    NetworkScoringService,
)
from modules.sitescore.domain.scoring import SiteScoreReport
from shared.domain import AddressLocation, CandidateSite, Listing

TENANT = "synthetic-measurement-gate-tenant"
AT = datetime(2026, 10, 13, tzinfo=UTC)
HUGE_INTEGER = 10**400


class NeverInfer:
    calls = 0

    def infer(self, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("This measurement proof must never infer")


class ReadOnlyListings:
    tenant_id = TENANT

    def __init__(self, draft: CandidateSiteDraft) -> None:
        self.draft = draft

    def list_candidates(self) -> list[CandidateSiteDraft]:
        return [self.draft]


class ReadOnlyReports:
    tenant_id = TENANT

    def list_latest(self) -> list[SiteScoreReport]:
        return []


def draft() -> CandidateSiteDraft:
    listing = Listing(
        listing_id="synthetic-measurement-listing", address_id="synthetic-measurement-address",
        rent_amount=54321.5, area_ping=23.75, floor="1F", confidence=0.0,
        snapshot_id="synthetic-measurement-snapshot", tenant_id=TENANT,
    )
    object.__setattr__(listing, "hard_rule_failures", [])
    return CandidateSiteDraft(
        listing=listing,
        address=AddressLocation(
            address_id=listing.address_id, normalized_address="Synthetic recorded location",
            latitude=25.0, longitude=121.0, geocode_confidence=0.93,
            h3_res_9="synthetic-measurement-cell", tenant_id=TENANT,
        ),
        candidate_site=CandidateSite(
            candidate_site_id="synthetic-measurement-candidate", listing_id=listing.listing_id,
            address_id=listing.address_id, target_format_code="synthetic-format", created_at=AT,
        ),
        heat_zone_id="synthetic-measurement-zone", feature_snapshot_time=AT,
        prior_90d_cell_net_revenue=0.0, prior_90d_cell_transaction_count=0, prior_90d_cell_store_count=0,
    )


def service(record: CandidateSiteDraft) -> NetworkScoringService:
    return NetworkScoringService(
        seed_fixtures=False, require_canonical=True, tenant_id=TENANT,
        listing_repository=ReadOnlyListings(record), sitescore_repository=ReadOnlyReports(),
        model_runtime=NeverInfer(),
    )


BAD_GEOCODE = [
    pytest.param(None, "unmeasured", id="none"),
    pytest.param(float("nan"), "invalid", id="nan"),
    pytest.param(float("inf"), "invalid", id="positive-infinity"),
    pytest.param(float("-inf"), "invalid", id="negative-infinity"),
    pytest.param(True, "invalid", id="boolean-true"),
    pytest.param(False, "invalid", id="boolean-false"),
    pytest.param("0.93", "invalid", id="numeric-string"),
    pytest.param({}, "invalid", id="object"),
    pytest.param([], "invalid", id="array"),
    pytest.param(HUGE_INTEGER, "invalid", id="integer-overflow"),
    pytest.param(1.00001, "invalid", id="above-ratio-range"),
    pytest.param(-0.1, "invalid", id="below-ratio-range"),
]


@pytest.mark.parametrize(("value", "availability"), BAD_GEOCODE)
def test_unavailable_geocode_blocks_without_exception_or_substituted_zero(value: Any, availability: str) -> None:
    record = draft()
    record.address = replace(record.address, geocode_confidence=value)
    scorer = service(record)
    row = scorer.snapshot()["candidates"][0]
    evidence = row["readEvidence"]["fields"]["geocode"]
    assert evidence["availability"] == availability
    assert evidence["value"] is None
    assert row["gate"]["passed"] is False
    assert next(check for check in row["gate"]["checks"] if check["key"] == "geocode")["state"] == "fail"
    with pytest.raises(NetworkScoringGateError):
        scorer.score_candidate(
            candidate_id=row["id"], actor_role_id="expansionManager", actor_name=None,
            idempotency_key=None, correlation_id=None,
        )
    assert scorer._model_runtime.calls == 0


BAD_AMOUNT = [
    pytest.param(None, "unmeasured", id="none"),
    pytest.param(0.0, "unmeasured", id="unmarked-legacy-zero"),
    pytest.param(-1.0, "invalid", id="negative"),
    pytest.param(float("nan"), "invalid", id="nan"),
    pytest.param(float("inf"), "invalid", id="positive-infinity"),
    pytest.param(float("-inf"), "invalid", id="negative-infinity"),
    pytest.param(True, "invalid", id="boolean-true"),
    pytest.param(False, "invalid", id="boolean-false"),
    pytest.param("54321.5", "invalid", id="numeric-string"),
    pytest.param({}, "invalid", id="object"),
    pytest.param([], "invalid", id="array"),
    pytest.param(HUGE_INTEGER, "invalid", id="integer-overflow"),
]


@pytest.mark.parametrize("dimension", ["rent", "area"])
@pytest.mark.parametrize(("value", "availability"), BAD_AMOUNT)
def test_unavailable_amount_blocks_the_same_existing_dimension(
    dimension: str, value: Any, availability: str,
) -> None:
    record = draft()
    attribute = "rent_amount" if dimension == "rent" else "area_ping"
    record.listing = replace(record.listing, **{attribute: value})
    scorer = service(record)
    row = scorer.snapshot()["candidates"][0]
    assert row["readEvidence"]["fields"][dimension]["availability"] == availability
    assert row["readEvidence"]["fields"][dimension]["value"] is None
    assert row["gate"]["passed"] is False
    assert next(check for check in row["gate"]["checks"] if check["key"] == dimension)["state"] == "fail"
    assert scorer._model_runtime.calls == 0


@pytest.mark.parametrize("confidence", [0.80, 0.93, 1.0])
def test_valid_finite_ratio_and_positive_amounts_keep_the_qualified_six_gate(confidence: float) -> None:
    record = draft()
    record.address = replace(record.address, geocode_confidence=confidence)
    scorer = service(record)
    row = scorer.snapshot()["candidates"][0]
    assert row["gate"]["passed"] is True
    assert (row["gate"]["okCount"], row["gate"]["totalCount"]) == (6, 6)
    assert row["readEvidence"]["fields"]["geocode"]["value"] == confidence
    assert row["readEvidence"]["fields"]["sourceConfidence"]["value"] == 0.0
    assert row["readEvidence"]["fields"]["sourceConfidence"]["availability"] == "recorded"
    assert scorer._model_runtime.calls == 0


@pytest.mark.parametrize("confidence", [0.0, 0.799999])
def test_recorded_finite_low_ratio_is_not_unmeasured_or_a_new_threshold(confidence: float) -> None:
    record = draft()
    record.address = replace(record.address, geocode_confidence=confidence)
    row = service(record).snapshot()["candidates"][0]
    assert row["readEvidence"]["fields"]["geocode"]["availability"] == "recorded"
    assert row["readEvidence"]["fields"]["geocode"]["value"] == confidence
    assert row["gate"]["passed"] is False


@pytest.mark.parametrize("latitude,longitude", [(0.0, 121.0), (25.0, 0.0), (0.0, 0.0)])
def test_zero_coordinate_is_present_in_the_data_gate(latitude: float, longitude: float) -> None:
    record = draft()
    record.address = replace(record.address, latitude=latitude, longitude=longitude)
    scorer = service(record)
    assert scorer.snapshot()["candidates"][0]["gate"]["passed"] is True
    assert scorer._model_runtime.calls == 0
    # The existing production model input contract separately rejects zero-axis
    # locations; this test makes no claim that zero is qualified for inference.


def test_valid_score_still_reaches_existing_exact_executor_without_real_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    record = draft()
    received = []
    # The qualified measurement gate stays positive; fresh execution is now
    # admitted by its explicit owner rather than a synchronous report service.
    class StubOwner:
        def create(self, command: Any, *, principal: Any) -> Any:
            received.append(command)
            return {"status": "QUEUED", "result": None, "candidateSiteId": command.candidate_site_id}

    from modules.sitescore.domain.execution import ScoreExecutionCommand
    from shared.auth import Principal, Role, Scope
    scorer = service(record)
    scorer._execution_service = StubOwner()
    command = ScoreExecutionCommand(record.candidate_site.candidate_site_id, "synthetic-saved-report", 1, 7,
                                    "Synthetic qualified boundary", "synthetic-key", "synthetic-correlation")
    result = scorer.score_candidate(
        candidate_id=record.candidate_site.candidate_site_id, actor_role_id="expansionManager", actor_name=None,
        idempotency_key=None, correlation_id=None, execution_command=command,
        principal=Principal("synthetic-actor", frozenset({Role.EXPANSION_USER}), Scope(tenant_id=TENANT)),
    )
    assert scorer.snapshot()["candidates"][0]["gate"]["passed"] is True
    assert len(received) == 1
    assert received[0].candidate_site_id == record.candidate_site.candidate_site_id
    fields = scorer.snapshot()["candidates"][0]["readEvidence"]["fields"]
    assert fields["rent"]["value"] == record.listing.rent_amount
    assert fields["area"]["value"] == record.listing.area_ping
    assert fields["geocode"]["value"] == record.address.geocode_confidence
    assert result["executionRequest"]["status"] == "QUEUED"
    assert result["executionRequest"]["result"] is None
    assert scorer._model_runtime.calls == 0
