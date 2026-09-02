from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from modules.external_data.geo import GeoFeatureSnapshot
from modules.heatzone.domain.scoring import (
    HeatZoneFeatureInput,
    HeatZoneState,
    score_heatzones,
)
from modules.heatzone.v3 import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    MODEL_VERSION,
    AbstainReasonCode,
    ExecutionMode,
    HeatZoneV3BatchResult,
    HeatZoneV3Input,
    HeatZoneV3ScoreResult,
    HeatZoneV3ShadowRunner,
    HeatZoneV3State,
    from_catchment_profile,
    from_legacy_feature_input,
    from_market_cell_profile,
    score_heatzone_v3_feature,
    score_heatzones_v3,
)
from packages.oday_data_contracts_client.models.machine_capacity import (
    CapacityEvidenceKind,
    MachineCapacityRecord,
    MachineClass,
    TimeContract,
)
from packages.oday_data_contracts_client.models.machine_capacity import (
    CoverageState as MachineCoverageState,
)
from packages.oday_data_contracts_client.models.manifests import (
    ManifestDocument,
)
from packages.oday_data_contracts_client.models.store_coverage import (
    CoverageState as StoreCoverageState,
)
from packages.oday_data_contracts_client.models.store_coverage import (
    EntityPartitionCoverage,
    QueryGeometry,
    StoreDayCoverage,
)
from packages.oday_data_product_contracts_client.models.catchment_profile import (
    CatchmentBoundary,
    CatchmentCompetitors,
    CatchmentCoverage,
    CatchmentDemographics,
    CatchmentMobility,
    CatchmentOrigin,
    CatchmentProfile,
    CatchmentProfileDocument,
    CatchmentRent,
    CatchmentTrafficAccess,
    DomainStatus,
    TravelMode,
)
from packages.oday_data_product_contracts_client.models.catchment_profile import (
    PeriodGrain as CatchmentPeriodGrain,
)
from packages.oday_data_product_contracts_client.models.catchment_profile import (
    ReadinessLevel as CatchmentReadinessLevel,
)
from packages.oday_data_product_contracts_client.models.market_cell_profile import (
    MarketCellCompetitors,
    MarketCellCoverage,
    MarketCellDemographics,
    MarketCellMobility,
    MarketCellProfile,
    MarketCellProfileDocument,
    MarketCellRent,
    ReadinessLevel,
    SourceSupportSummary,
)
from packages.oday_data_product_contracts_client.models.market_cell_profile import (
    PeriodGrain as MarketCellPeriodGrain,
)

SNAPSHOT_TIME = datetime(2026, 6, 27, 8, 0, tzinfo=UTC)


def _sample_source_support() -> SourceSupportSummary:
    return SourceSupportSummary(
        source_dataset_ids=["ds-geo-1", "ds-nlsc-1", "ds-moi-1"],
        observation_count=100,
        sample_count=100,
        first_observed_at="2026-01-01T00:00:00Z",
        last_observed_at="2026-08-14T00:00:00Z",
    )


def _sample_market_cell_profile(
    *,
    h3_index: str = "884a1072b7fffff",
    total_population: float = 6500.0,
    household_count: float = 2400.0,
    daytime_ratio: float = 1.3,
    active_competitors: int = 2,
    total_capacity: float = 40.0,
    median_rent: float = 2400.0,
    sample_count: int = 8,
    overall_readiness: ReadinessLevel = ReadinessLevel.ready,
    domain_coverage: dict[str, str] | None = None,
    has_gaps: bool = False,
    rent_confidence_pct: float | None = 90.0,
    demographics_uncertainty_pct: float | None = 5.0,
) -> MarketCellProfile:
    return MarketCellProfile(
        cell_id=f"cell:{h3_index}",
        h3_index=h3_index,
        h3_resolution=8,
        period_grain=MarketCellPeriodGrain.MONTHLY,
        period_key="2026-08",
        centroid_lat=25.04,
        centroid_lng=121.56,
        county="Taipei",
        district="Xinyi",
        admin_code="63000",
        demographics=MarketCellDemographics(
            total_population=total_population,
            household_count=household_count,
            daytime_population_ratio=daytime_ratio,
            uncertainty_pct=demographics_uncertainty_pct,
        ),
        competitors=MarketCellCompetitors(
            total_competitors=active_competitors,
            active_competitors=active_competitors,
            total_capacity=total_capacity,
            average_capacity=total_capacity / max(1, active_competitors),
            brands_present=["BrandA", "BrandB"],
            stores_by_brand={"BrandA": 1, "BrandB": 1},
            stores_by_category={"laundromat": 2, "convenience": 4},
            price_tier_distribution={"MID": 2},
        ),
        rent=MarketCellRent(
            median_rent_per_ping=median_rent,
            mean_rent_per_ping=median_rent + 100.0,
            sample_count=sample_count,
            confidence_pct=rent_confidence_pct,
        ),
        mobility=MarketCellMobility(
            activity_population=total_population * 0.8,
            resident_population=total_population * 0.6,
            is_calibrated=True,
        ),
        coverage=MarketCellCoverage(
            overall_readiness=overall_readiness,
            domain_coverage=domain_coverage or {"DEMOGRAPHICS": "complete", "COMPETITOR": "complete", "RENT": "complete"},
            has_gaps=has_gaps,
            readiness_reasons=[],
        ),
        source_support=_sample_source_support(),
    )


def _sample_machine_capacity(
    store_id: str = "store-001",
    machine_count: int = 12,
    machine_class: MachineClass = MachineClass.WASHER,
    evidence_ref: str | None = "884a1072b7fffff",
) -> MachineCapacityRecord:
    return MachineCapacityRecord(
        store_id=store_id,
        effective_business_date="2026-08-01",
        machine_class=machine_class,
        machine_count=machine_count,
        evidence_kind=CapacityEvidenceKind.DECLARED_INVENTORY,
        evidence_ref=evidence_ref,
        coverage_state=MachineCoverageState.complete,
        time_contract=TimeContract(
            business_day_boundary="00:00:00",
            store_timezone="Asia/Taipei",
            knowledge_as_of="2026-08-14T00:00:00Z",
        ),
    )


def _sample_store_coverage(
    store_id: str = "store-001",
    business_date: str = "2026-08-14",
    h3_index: str | None = "884a1072b7fffff",
) -> StoreDayCoverage:
    return StoreDayCoverage(
        store_id=store_id,
        business_date=business_date,
        window_start="2026-08-14T00:00:00Z",
        window_end="2026-08-14T23:59:59Z",
        raw_contract_fingerprint="fp-test-001",
        coverage=EntityPartitionCoverage(
            coverage_id=f"cov-{store_id}-{business_date}",
            dataset_id="ds-transactions",
            scope_principal_id="principal-001",
            state=StoreCoverageState.complete,
            is_complete=True,
            observed_count=150,
            query_geometry=QueryGeometry(h3_index=h3_index) if h3_index else None,
        ),
    )


# -----------------------------------------------------------------------------
# 1. Contract Identity and Metadata Tests
# -----------------------------------------------------------------------------

def test_heatzone_v3_contract_identity_and_version() -> None:
    assert CONTRACT_ID == "odayplus.heatzone-v3.v1"
    assert CONTRACT_VERSION == "1.0.0"
    assert MODEL_VERSION == "heatzone-v3-shadow"


# -----------------------------------------------------------------------------
# 2. Multi-Dimensional Acceptance Criteria Tests (All 9 Dimensions)
# -----------------------------------------------------------------------------

def test_heatzone_v3_scores_all_nine_required_dimensions() -> None:
    """Verify HeatZone v3 uses population, households, housing, POI, competitor capacity,

    rent, listing, own-store capacity, and coverage.
    """
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        h3_resolution=8,
        # 1. Population
        population=7200.0,
        daytime_population_ratio=1.4,
        # 2. Households
        household_count=2800.0,
        # 3. Housing
        housing_units=2600.0,
        # 4. POI
        poi_count=18,
        poi_categories={"convenience": 6, "supermarket": 2, "cafe": 10},
        # 5. Competitor capacity
        competitor_capacity=45.0,
        active_competitor_count=3,
        competitor_brands=["BrandX", "BrandY"],
        competitor_price_tiers={"MID": 2, "HIGH": 1},
        # 6. Rent
        median_rent_per_ping=2200.0,
        mean_rent_per_ping=2300.0,
        rent_sample_count=12,
        # 7. Listing
        active_listing_count=6,
        median_listing_rent=2100.0,
        # 8. Own-store capacity
        own_store_count=1,
        own_store_machine_capacity=10.0,
        # 9. Coverage
        overall_readiness=ReadinessLevel.ready,
        coverage_ratio=0.95,
        confidence=0.90,
        county="Taipei",
        district="Daan",
    )

    res = score_heatzone_v3_feature(inp)

    assert res.abstained is False
    assert res.score is not None
    assert 0.0 <= res.score <= 100.0
    assert res.is_shadow is True
    assert res.execution_mode is ExecutionMode.SHADOW
    assert res.model_version == MODEL_VERSION
    assert res.contract_version == CONTRACT_VERSION

    # Check that all sub-dimension scores are populated and within [0, 1]
    assert 0.0 <= res.demographic_vitality_score <= 1.0
    assert 0.0 <= res.housing_density_score <= 1.0
    assert 0.0 <= res.competitor_pressure_score <= 1.0
    assert 0.0 <= res.cannibalization_risk_score <= 1.0
    assert 0.0 <= res.rent_feasibility_score <= 1.0
    assert 0.0 <= res.listing_availability_score <= 1.0
    assert 0.0 <= res.unmet_demand_score <= 1.0
    assert 0.0 <= res.format_fit_score <= 1.0

    # Check input dimensions audit summary
    assert res.input_dimensions["population"] == 7200.0
    assert res.input_dimensions["household_count"] == 2800.0
    assert res.input_dimensions["housing_units"] == 2600.0
    assert res.input_dimensions["poi_count"] == 18
    assert res.input_dimensions["competitor_capacity"] == 45.0
    assert res.input_dimensions["active_competitor_count"] == 3
    assert res.input_dimensions["median_rent_per_ping"] == 2200.0
    assert res.input_dimensions["active_listing_count"] == 6
    assert res.input_dimensions["own_store_count"] == 1
    assert res.input_dimensions["own_store_machine_capacity"] == 10.0
    assert res.input_dimensions["coverage_ratio"] == 0.95


def test_heatzone_v3_sensitivity_to_own_store_cannibalization() -> None:
    """Higher own-store capacity increases cannibalization risk and lowers unmet demand."""
    base_input = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=6000.0,
        household_count=2000.0,
        poi_count=15,
        active_competitor_count=1,
        competitor_capacity=10.0,
        median_rent_per_ping=2000.0,
        active_listing_count=5,
        own_store_count=0,
        own_store_machine_capacity=0.0,
        coverage_ratio=1.0,
        confidence=1.0,
    )

    low_cannibalization = score_heatzone_v3_feature(base_input)

    high_cann_input = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=6000.0,
        household_count=2000.0,
        poi_count=15,
        active_competitor_count=1,
        competitor_capacity=10.0,
        median_rent_per_ping=2000.0,
        active_listing_count=5,
        own_store_count=3,
        own_store_machine_capacity=30.0,
        coverage_ratio=1.0,
        confidence=1.0,
    )

    high_cannibalization = score_heatzone_v3_feature(high_cann_input)

    assert high_cannibalization.cannibalization_risk_score > low_cannibalization.cannibalization_risk_score
    assert high_cannibalization.unmet_demand_score < low_cannibalization.unmet_demand_score
    assert (high_cannibalization.score or 0.0) < (low_cannibalization.score or 0.0)


def test_heatzone_v3_sensitivity_to_rent_and_listing_availability() -> None:
    """Extreme rent lowers rent feasibility score; active listing supply increases availability."""
    low_rent_input = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        household_count=2000.0,
        median_rent_per_ping=1500.0,
        active_listing_count=8,
        coverage_ratio=1.0,
        confidence=1.0,
    )
    high_rent_input = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        household_count=2000.0,
        median_rent_per_ping=4800.0,
        active_listing_count=1,
        coverage_ratio=1.0,
        confidence=1.0,
    )

    res_low = score_heatzone_v3_feature(low_rent_input)
    res_high = score_heatzone_v3_feature(high_rent_input)

    assert res_low.rent_feasibility_score > res_high.rent_feasibility_score
    assert res_low.listing_availability_score > res_high.listing_availability_score


# -----------------------------------------------------------------------------
# 3. Abstention Outside Platform Support Tests (Fail Closed)
# -----------------------------------------------------------------------------

def test_heatzone_v3_abstains_when_readiness_is_blocked() -> None:
    """Fail-closed: abstain when platform readiness level is blocked."""
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        overall_readiness=ReadinessLevel.blocked,
        readiness_reasons=["PLATFORM_UPSTREAM_UNAVAILABLE"],
    )

    res = score_heatzone_v3_feature(inp)

    assert res.abstained is True
    assert res.score is None
    assert res.state is HeatZoneV3State.ABSTAINED
    assert AbstainReasonCode.READINESS_BLOCKED.value in res.abstain_reasons
    assert "evaluation_abstained_outside_support" in res.warnings


def test_heatzone_v3_abstains_when_readiness_is_unknown() -> None:
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        overall_readiness=ReadinessLevel.unknown,
    )

    res = score_heatzone_v3_feature(inp)

    assert res.abstained is True
    assert res.score is None
    assert res.state is HeatZoneV3State.ABSTAINED
    assert AbstainReasonCode.READINESS_UNKNOWN.value in res.abstain_reasons


def test_heatzone_v3_abstains_when_source_is_quarantined() -> None:
    """Fail-closed: abstain when underlying data source is quarantined."""
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        is_quarantined=True,
    )

    res = score_heatzone_v3_feature(inp)

    assert res.abstained is True
    assert res.score is None
    assert res.state is HeatZoneV3State.ABSTAINED
    assert AbstainReasonCode.SOURCE_QUARANTINED.value in res.abstain_reasons


def test_heatzone_v3_abstains_when_coverage_ratio_below_threshold() -> None:
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        coverage_ratio=0.35,  # Below 0.50 threshold
    )

    res = score_heatzone_v3_feature(inp)

    assert res.abstained is True
    assert res.score is None
    assert res.state is HeatZoneV3State.ABSTAINED
    assert AbstainReasonCode.INSUFFICIENT_COVERAGE.value in res.abstain_reasons


def test_heatzone_v3_abstains_when_support_level_outside_bounds() -> None:
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        support_level="unsupported",
    )

    res = score_heatzone_v3_feature(inp)

    assert res.abstained is True
    assert res.score is None
    assert res.state is HeatZoneV3State.ABSTAINED
    assert AbstainReasonCode.OUT_OF_SUPPORT_BOUNDS.value in res.abstain_reasons


def test_heatzone_v3_abstains_when_critical_domain_quarantined_or_missing() -> None:
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        domain_coverage={"DEMOGRAPHICS": "quarantined", "COMPETITOR": "complete"},
    )

    res = score_heatzone_v3_feature(inp)

    assert res.abstained is True
    assert res.score is None
    assert any(AbstainReasonCode.MISSING_REQUIRED_DOMAINS.value in r for r in res.abstain_reasons)


def test_heatzone_v3_abstains_when_coverage_ratio_is_none() -> None:
    """Fail-closed: unmeasured coverage (None) must reach abstention gate with INSUFFICIENT_COVERAGE."""
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        coverage_ratio=None,
        confidence=1.0,
    )

    res = score_heatzone_v3_feature(inp)

    assert res.abstained is True
    assert res.score is None
    assert res.state is HeatZoneV3State.ABSTAINED
    assert AbstainReasonCode.INSUFFICIENT_COVERAGE.value in res.abstain_reasons
    assert res.input_dimensions["coverage_ratio"] is None


def test_heatzone_v3_abstains_when_confidence_is_none() -> None:
    """Fail-closed: unmeasured confidence (None) must reach abstention gate with DATA_QUALITY_UNACCEPTABLE."""
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        coverage_ratio=1.0,
        confidence=None,
    )

    res = score_heatzone_v3_feature(inp)

    assert res.abstained is True
    assert res.score is None
    assert res.state is HeatZoneV3State.ABSTAINED
    assert AbstainReasonCode.DATA_QUALITY_UNACCEPTABLE.value in res.abstain_reasons


def test_heatzone_v3_abstains_when_both_coverage_and_confidence_are_unmeasured() -> None:
    """Fail-closed: default constructed HeatZoneV3Input (no coverage or confidence) abstains."""
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
    )

    res = score_heatzone_v3_feature(inp)

    assert res.abstained is True
    assert res.score is None
    assert res.state is HeatZoneV3State.ABSTAINED
    assert AbstainReasonCode.INSUFFICIENT_COVERAGE.value in res.abstain_reasons
    assert AbstainReasonCode.DATA_QUALITY_UNACCEPTABLE.value in res.abstain_reasons


def test_heatzone_v2_suppresses_when_confidence_or_quality_unmeasured() -> None:
    """Fail-closed in v2: missing confidence/quality defaults to None and suppresses."""
    inp_none = HeatZoneFeatureInput(
        h3_index="884a1072b7fffff",
        poi_count=10,
    )
    scores = score_heatzones([inp_none])
    assert len(scores) == 1
    assert scores[0].confidence == 0.0
    assert scores[0].state is HeatZoneState.SUPPRESSED_LOW_CONFIDENCE
    assert "low_confidence" in scores[0].warnings


def test_heatzone_v2_suppresses_when_only_average_confidence_is_measured() -> None:
    """One measured side is not a measured composite: data quality stays unknown."""
    inp = HeatZoneFeatureInput(
        h3_index="884a1072b7fffff",
        poi_count=10,
        competitor_count=2,
        average_confidence=0.9,
        data_quality_score=None,
    )

    score = score_heatzones([inp])[0]

    assert score.confidence == 0.0
    assert score.state is HeatZoneState.SUPPRESSED_LOW_CONFIDENCE
    assert "low_confidence" in score.warnings


def test_heatzone_v2_suppresses_when_only_data_quality_is_measured() -> None:
    """Mirror of the above: a measured data quality does not stand in for confidence."""
    inp = HeatZoneFeatureInput(
        h3_index="884a1072b7fffff",
        poi_count=10,
        competitor_count=2,
        average_confidence=None,
        data_quality_score=0.9,
    )

    score = score_heatzones([inp])[0]

    assert score.confidence == 0.0
    assert score.state is HeatZoneState.SUPPRESSED_LOW_CONFIDENCE
    assert "low_confidence" in score.warnings


def test_heatzone_v2_unmeasured_quality_never_outranks_measured_quality() -> None:
    """Absence must never buy a higher confidence than an actual measurement."""
    measured = HeatZoneFeatureInput(
        h3_index="884a1072b7fffff",
        poi_count=10,
        competitor_count=2,
        average_confidence=0.9,
        data_quality_score=0.9,
    )
    partially_measured = HeatZoneFeatureInput(
        h3_index="884a1072b1fffff",
        poi_count=10,
        competitor_count=2,
        average_confidence=0.9,
        data_quality_score=None,
    )

    by_h3 = {score.h3_index: score for score in score_heatzones([measured, partially_measured])}

    assert by_h3["884a1072b7fffff"].confidence == 0.81
    assert by_h3["884a1072b7fffff"].state is not HeatZoneState.SUPPRESSED_LOW_CONFIDENCE
    assert by_h3["884a1072b1fffff"].confidence < by_h3["884a1072b7fffff"].confidence


def test_heatzone_v2_from_mapping_leaves_absent_quality_unmeasured() -> None:
    """from_mapping is the realistic ingress: an absent quality key stays None."""
    without_quality = HeatZoneFeatureInput.from_mapping(
        {"h3_index": "884a1072b7fffff", "poi_count": 10, "average_confidence": 0.9}
    )
    with_quality = HeatZoneFeatureInput.from_mapping(
        {
            "h3_index": "884a1072b7fffff",
            "poi_count": 10,
            "average_confidence": 0.9,
            "data_quality_score": 0.9,
        }
    )

    assert without_quality.average_confidence == 0.9
    assert without_quality.data_quality_score is None
    assert with_quality.data_quality_score == 0.9

    assert score_heatzones([without_quality])[0].confidence == 0.0
    assert score_heatzones([with_quality])[0].confidence == 0.81


def test_heatzone_v2_geo_snapshot_conversion_carries_competitor_capacity() -> None:
    """competitor_capacity is measured per cell by the geo pipeline and must survive."""
    snapshot = GeoFeatureSnapshot(
        h3_index="884a1072b7fffff",
        h3_resolution=9,
        feature_snapshot_time=SNAPSHOT_TIME,
        view_version="geo-grid-view-v1",
        poi_count=30,
        competitor_count=2,
        competitor_capacity=200.0,
        average_confidence=0.9,
        source_snapshot_ids=("geo-1",),
    )

    feature = HeatZoneFeatureInput.from_geo_feature_snapshot(
        snapshot, data_quality_score=0.9
    )
    assert feature.competitor_capacity == 200.0

    saturated = score_heatzones([feature])[0]
    uncontested = score_heatzones(
        [
            HeatZoneFeatureInput.from_geo_feature_snapshot(
                replace(snapshot, competitor_capacity=0.0), data_quality_score=0.9
            )
        ]
    )[0]

    assert saturated.unmet_demand_score < uncontested.unmet_demand_score
    assert saturated.score < uncontested.score


# -----------------------------------------------------------------------------
# 4. Adaptation of Canonical Data Products
# -----------------------------------------------------------------------------

def test_adapt_market_cell_profile_document_and_own_store_capacity() -> None:
    """Adapt MarketCellProfileDocument into HeatZone v3 inputs with store capacity records."""
    cell1 = _sample_market_cell_profile(
        h3_index="884a1072b7fffff",
        total_population=6000.0,
        active_competitors=2,
        total_capacity=30.0,
    )
    cell2 = _sample_market_cell_profile(
        h3_index="884a1072b1fffff",
        total_population=2000.0,
        overall_readiness=ReadinessLevel.blocked,
    )

    doc = MarketCellProfileDocument(
        profile_id="mcp-doc-001",
        cells=[cell1, cell2],
        h3_resolution=8,
        period_grain=MarketCellPeriodGrain.MONTHLY,
        period_key="2026-08",
        generated_at="2026-08-14T14:40:00Z",
        source_support=_sample_source_support(),
    )

    machine_cap = _sample_machine_capacity(store_id="store-001", machine_count=14)
    store_cov = _sample_store_coverage(store_id="store-001")

    runner = HeatZoneV3ShadowRunner()
    batch_result = runner.evaluate_market_cells(
        doc,
        own_store_capacities=[machine_cap],
        store_coverage_records=[store_cov],
    )

    assert batch_result.total_evaluated == 2
    assert batch_result.scored_count == 1
    assert batch_result.abstained_count == 1
    assert batch_result.is_shadow is True

    scored_res = next(s for s in batch_result.scores if s.h3_index == "884a1072b7fffff")
    abstained_res = next(s for s in batch_result.scores if s.h3_index == "884a1072b1fffff")

    assert scored_res.abstained is False
    assert scored_res.score is not None
    assert abstained_res.abstained is True
    assert abstained_res.score is None


def test_adapt_catchment_profile_document() -> None:
    """Adapt CatchmentProfileDocument into HeatZone v3 inputs."""
    prof = CatchmentProfile(
        profile_id="prof-catch-001",
        period_grain=CatchmentPeriodGrain.MONTHLY,
        period_key="2026-08",
        origin=CatchmentOrigin(
            origin_id="orig-001",
            origin_h3="884a1072b7fffff",
            latitude=25.04,
            longitude=121.56,
            origin_geom={"type": "Point", "coordinates": [121.56, 25.04]},
            county="Taipei",
            district="Zhongshan",
        ),
        boundary=CatchmentBoundary(
            catchment_id="boundary-001",
            travel_mode=TravelMode.motorcycle,
            cutoff_seconds=600,
            routing_engine="valhalla",
            graph_version="2026.08",
            h3_cells=["884a1072b7fffff"],
            h3_resolution=9,
            total_cells_count=1,
            geom={"type": "Polygon", "coordinates": [[[121.5, 25.0], [121.6, 25.0], [121.6, 25.1], [121.5, 25.0]]]},
        ),
        demographics=CatchmentDemographics(
            status=DomainStatus.available,
            total_population=8500.0,
            household_count=3200.0,
            daytime_population_ratio=1.5,
        ),
        competitors=CatchmentCompetitors(
            status=DomainStatus.available,
            active_competitors=3,
            total_capacity=50.0,
            stores_by_category={"laundromat": 3, "convenience": 5},
        ),
        rent=CatchmentRent(
            status=DomainStatus.available,
            median_rent_per_ping=2500.0,
            sample_count=15,
        ),
        mobility=CatchmentMobility(
            status=DomainStatus.available,
            activity_population=7000.0,
            resident_population=5000.0,
        ),
        traffic=CatchmentTrafficAccess(status=DomainStatus.available),
        coverage=CatchmentCoverage(
            overall_readiness=CatchmentReadinessLevel.ready,
            domain_coverage={"DEMOGRAPHICS": "complete", "COMPETITOR": "complete", "RENT": "complete"},
            has_gaps=False,
        ),
        source_support=_sample_source_support(),
    )

    doc = CatchmentProfileDocument(
        document_id="cat-doc-001",
        generated_at="2026-08-14T14:40:00Z",
        period_grain=CatchmentPeriodGrain.MONTHLY,
        period_key="2026-08",
        profiles=[prof],
        source_support=_sample_source_support(),
    )

    runner = HeatZoneV3ShadowRunner()
    batch_result = runner.evaluate_catchment_profiles(doc)

    assert batch_result.total_evaluated == 1
    assert batch_result.scored_count == 1
    assert batch_result.abstained_count == 0
    res = batch_result.scores[0]
    assert res.score is not None
    assert res.district == "Zhongshan"


def test_adapt_legacy_feature_input_bridge() -> None:
    """Bridge legacy v1 HeatZoneFeatureInput into HeatZoneV3Input cleanly."""
    legacy = HeatZoneFeatureInput(
        h3_index="884a1072b7fffff",
        h3_resolution=9,
        poi_count=12,
        competitor_count=2,
        competitor_capacity=25.0,
        median_listing_rent=2200.0,
        active_listing_count=5,
        existing_store_count=1,
        average_confidence=0.85,
        admin_city="Taipei",
        admin_district="Neihu",
    )

    v3_inp = from_legacy_feature_input(
        legacy,
        population_override=4500.0,
        coverage_ratio=1.0,
    )

    assert v3_inp.h3_index == "884a1072b7fffff"
    assert v3_inp.population == 4500.0
    assert v3_inp.poi_count == 12
    assert v3_inp.county == "Taipei"
    assert v3_inp.district == "Neihu"
    assert v3_inp.coverage_ratio == 1.0
    assert v3_inp.confidence == 0.85

    res = score_heatzone_v3_feature(v3_inp)
    assert res.score is not None
    assert res.abstained is False


def test_from_market_cell_profile_missing_confidence_abstains() -> None:
    """When market cell profile has neither rent confidence nor demographics uncertainty, confidence is None and evaluation abstains."""
    cell = _sample_market_cell_profile(
        h3_index="884a1072b7fffff",
        rent_confidence_pct=None,
        demographics_uncertainty_pct=None,
    )

    v3_inp = from_market_cell_profile(cell)
    assert v3_inp.confidence is None

    res = score_heatzone_v3_feature(v3_inp)
    assert res.abstained is True
    assert res.score is None
    assert AbstainReasonCode.DATA_QUALITY_UNACCEPTABLE.value in res.abstain_reasons


def test_from_market_cell_profile_confidence_from_single_and_multiple_signals() -> None:
    """Validate confidence derivation when one or both observation signals are present."""
    # Only rent confidence present (80%)
    cell1 = _sample_market_cell_profile(
        h3_index="884a1072b7fffff",
        rent_confidence_pct=80.0,
        demographics_uncertainty_pct=None,
    )
    v3_inp1 = from_market_cell_profile(cell1)
    assert v3_inp1.confidence == 0.80

    # Only demographics uncertainty present (15% -> 85% confidence)
    cell2 = _sample_market_cell_profile(
        h3_index="884a1072b7fffff",
        rent_confidence_pct=None,
        demographics_uncertainty_pct=15.0,
    )
    v3_inp2 = from_market_cell_profile(cell2)
    assert v3_inp2.confidence == 0.85

    # Both present: takes min (e.g., min(0.90, 0.80) == 0.80)
    cell3 = _sample_market_cell_profile(
        h3_index="884a1072b7fffff",
        rent_confidence_pct=90.0,
        demographics_uncertainty_pct=20.0,
    )
    v3_inp3 = from_market_cell_profile(cell3)
    assert v3_inp3.confidence == 0.80


def test_from_legacy_feature_input_missing_coverage_ratio_abstains() -> None:
    """A legacy feature with measured confidence but unmeasured coverage defaults to coverage_ratio=None and abstains with INSUFFICIENT_COVERAGE."""
    legacy = HeatZoneFeatureInput(
        h3_index="884a1072b7fffff",
        h3_resolution=9,
        poi_count=10,
        competitor_count=2,
        average_confidence=0.90,
    )

    v3_inp = from_legacy_feature_input(legacy)
    assert v3_inp.coverage_ratio is None
    assert v3_inp.confidence == 0.90

    res = score_heatzone_v3_feature(v3_inp)
    assert res.abstained is True
    assert res.score is None
    assert AbstainReasonCode.INSUFFICIENT_COVERAGE.value in res.abstain_reasons
    assert AbstainReasonCode.DATA_QUALITY_UNACCEPTABLE.value not in res.abstain_reasons


def test_from_legacy_feature_input_missing_confidence_abstains() -> None:
    """A legacy feature with explicit coverage but unmeasured confidence defaults to confidence=None and abstains with DATA_QUALITY_UNACCEPTABLE."""
    legacy = HeatZoneFeatureInput(
        h3_index="884a1072b7fffff",
        h3_resolution=9,
        poi_count=10,
        competitor_count=2,
        average_confidence=None,
    )

    v3_inp = from_legacy_feature_input(legacy, coverage_ratio=1.0)
    assert v3_inp.coverage_ratio == 1.0
    assert v3_inp.confidence is None

    res = score_heatzone_v3_feature(v3_inp)
    assert res.abstained is True
    assert res.score is None
    assert AbstainReasonCode.DATA_QUALITY_UNACCEPTABLE.value in res.abstain_reasons
    assert AbstainReasonCode.INSUFFICIENT_COVERAGE.value not in res.abstain_reasons


def test_from_legacy_feature_input_all_missing_abstains_with_both_reasons() -> None:
    """A legacy mapping with neither confidence nor coverage ratio produces both abstention reasons."""
    data = {"h3_index": "884a1072b7fffff", "poi_count": 10}
    v3_inp = from_legacy_feature_input(data)
    assert v3_inp.coverage_ratio is None
    assert v3_inp.confidence is None

    res = score_heatzone_v3_feature(v3_inp)
    assert res.abstained is True
    assert res.score is None
    assert AbstainReasonCode.INSUFFICIENT_COVERAGE.value in res.abstain_reasons
    assert AbstainReasonCode.DATA_QUALITY_UNACCEPTABLE.value in res.abstain_reasons


def test_from_legacy_feature_input_mapping_coverage_ratio() -> None:
    """A legacy mapping can specify coverage_ratio in dict or via keyword argument."""
    data = {"h3_index": "884a1072b7fffff", "average_confidence": 0.88, "coverage_ratio": 0.95}
    v3_inp = from_legacy_feature_input(data)
    assert v3_inp.coverage_ratio == 0.95
    assert v3_inp.confidence == 0.88

    res = score_heatzone_v3_feature(v3_inp)
    assert res.abstained is False
    assert res.score is not None


def test_manifest_document_linkage() -> None:
    """Verify manifest document component linkage in shadow batch evaluations."""
    manifest = ManifestDocument(
        contract_id="emgi.manifests.v4.1",
        manifest={
            "manifest_id": "manifest-product-emgi-v0.4.1",
            "manifest_kind": "product",
            "product_contract_id": "emgi.market-cell-profile.v1",
            "product_version": "0.4.1",
        },
    )

    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        household_count=2000.0,
        poi_count=10,
        coverage_ratio=1.0,
        confidence=1.0,
    )

    runner = HeatZoneV3ShadowRunner()
    batch = runner.evaluate_inputs([inp], manifest_document=manifest)

    assert batch.manifest_id == "manifest-product-emgi-v0.4.1"
    assert batch.contract_version == CONTRACT_VERSION


# -----------------------------------------------------------------------------
# 5. Shadow Mode & Side-by-Side Comparison
# -----------------------------------------------------------------------------

def test_shadow_runner_generates_side_by_side_comparison_with_baseline() -> None:
    """Verify shadow execution computes delta against legacy heuristic without overwriting."""
    inp1 = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=8000.0,
        household_count=3000.0,
        poi_count=20,
        competitor_capacity=20.0,
        active_competitor_count=1,
        median_rent_per_ping=1800.0,
        active_listing_count=7,
        own_store_count=0,
        coverage_ratio=1.0,
        confidence=1.0,
    )
    inp2 = HeatZoneV3Input(
        h3_index="884a1072b1fffff",
        population=3000.0,
        household_count=1000.0,
        poi_count=5,
        competitor_capacity=40.0,
        active_competitor_count=3,
        median_rent_per_ping=3500.0,
        active_listing_count=2,
        own_store_count=1,
        coverage_ratio=1.0,
        confidence=1.0,
    )

    runner = HeatZoneV3ShadowRunner()
    batch_result = runner.evaluate_inputs([inp1, inp2])

    assert batch_result.is_shadow is True
    assert batch_result.execution_mode is ExecutionMode.SHADOW
    assert len(batch_result.comparisons) == 2

    comp1 = next(c for c in batch_result.comparisons if c.h3_index == "884a1072b7fffff")
    comp2 = next(c for c in batch_result.comparisons if c.h3_index == "884a1072b1fffff")

    assert comp1.v3_score is not None
    assert comp2.v3_score is not None
    assert comp1.baseline_score is not None
    assert comp1.score_delta is not None
    assert comp1.rank_delta is not None
    assert isinstance(comp1.agreement, bool)

    # Check metrics
    metrics = batch_result.shadow_metrics
    assert "total_evaluated" in metrics
    assert "scored_count" in metrics
    assert "abstained_count" in metrics
    assert "mean_score_delta" in metrics
    assert "agreement_rate" in metrics
    assert "top_k_overlap_rate" in metrics


# -----------------------------------------------------------------------------
# 6. Serialization, Round-Trip, and Map Feature Output
# -----------------------------------------------------------------------------

def test_heatzone_v3_score_result_round_trips() -> None:
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        household_count=2000.0,
        poi_count=10,
        competitor_capacity=20.0,
        active_competitor_count=1,
        median_rent_per_ping=2000.0,
        active_listing_count=4,
        coverage_ratio=1.0,
        confidence=1.0,
        county="Taipei",
        district="Xinyi",
    )

    scored = score_heatzone_v3_feature(inp)
    wire = scored.to_dict()

    restored = HeatZoneV3ScoreResult.from_dict(wire)
    assert restored.h3_index == scored.h3_index
    assert restored.score == scored.score
    assert restored.state == scored.state
    assert restored.is_shadow == scored.is_shadow
    assert restored.to_dict() == wire

    map_feat = scored.to_map_feature()
    assert map_feat["type"] == "Feature"
    assert map_feat["properties"]["h3_index"] == scored.h3_index
    assert map_feat["properties"]["score"] == scored.score


def test_heatzone_v3_batch_result_round_trips() -> None:
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        coverage_ratio=1.0,
        confidence=1.0,
    )
    runner = HeatZoneV3ShadowRunner()
    batch = runner.evaluate_inputs([inp])

    wire = batch.to_dict()
    restored = HeatZoneV3BatchResult.from_dict(wire)

    assert restored.document_id == batch.document_id
    assert restored.total_evaluated == batch.total_evaluated
    assert restored.scored_count == batch.scored_count
    assert restored.abstained_count == batch.abstained_count
    assert restored.contract_version == CONTRACT_VERSION
    assert restored.is_shadow is True


def test_heatzone_v3_deterministic_ranking_order() -> None:
    """Verify features are deterministically ranked by score descending with abstained at the end."""
    inps = [
        HeatZoneV3Input(h3_index="cell_low", population=1500.0, poi_count=2, active_competitor_count=4, competitor_capacity=50.0, coverage_ratio=1.0, confidence=1.0),
        HeatZoneV3Input(h3_index="cell_abstained", population=8000.0, overall_readiness=ReadinessLevel.blocked, coverage_ratio=1.0, confidence=1.0),
        HeatZoneV3Input(h3_index="cell_high", population=9000.0, poi_count=25, active_competitor_count=1, competitor_capacity=10.0, coverage_ratio=1.0, confidence=1.0),
        HeatZoneV3Input(h3_index="cell_mid", population=5000.0, poi_count=10, active_competitor_count=2, competitor_capacity=20.0, coverage_ratio=1.0, confidence=1.0),
    ]

    results = score_heatzones_v3(inps)

    assert len(results) == 4
    # cell_high should be rank 1
    assert results[0].h3_index == "cell_high"
    assert results[0].priority_rank == 1
    assert results[0].abstained is False

    # cell_mid should be rank 2
    assert results[1].h3_index == "cell_mid"
    assert results[1].priority_rank == 2
    assert results[1].abstained is False

    # cell_low should be rank 3
    assert results[2].h3_index == "cell_low"
    assert results[2].priority_rank == 3
    assert results[2].abstained is False

    # cell_abstained should be rank 4 (at the end)
    assert results[3].h3_index == "cell_abstained"
    assert results[3].priority_rank == 4
    assert results[3].abstained is True
    assert results[3].score is None


def test_heatzone_v3_abstains_when_critical_domain_empty_even_if_ready_and_no_gaps() -> None:
    """Acceptance B2: critical domain empty/missing/unobserved must fail closed and abstain."""
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=5000.0,
        overall_readiness=ReadinessLevel.ready,
        has_coverage_gaps=False,
        domain_coverage={"DEMOGRAPHICS": "empty", "COMPETITOR": "complete", "RENT": "complete"},
        coverage_ratio=1.0,
        confidence=1.0,
    )
    res = score_heatzone_v3_feature(inp)
    assert res.abstained is True
    assert res.score is None
    assert any("MISSING_REQUIRED_DOMAINS:demographics_empty" in r for r in res.abstain_reasons)


def test_heatzone_v3_rent_feasibility_monotonic_without_rent_data() -> None:
    """C1: rent_feasibility must be monotonic when effective_rent <= 0 (0 listings=0.0 <= 1 listing=0.08 <= 5 listings=0.40)."""
    inp_0 = HeatZoneV3Input(h3_index="884a1072b7fffff", population=5000.0, median_rent_per_ping=0.0, active_listing_count=0, coverage_ratio=1.0, confidence=1.0)
    inp_1 = HeatZoneV3Input(h3_index="884a1072b7fffff", population=5000.0, median_rent_per_ping=0.0, active_listing_count=1, coverage_ratio=1.0, confidence=1.0)
    inp_5 = HeatZoneV3Input(h3_index="884a1072b7fffff", population=5000.0, median_rent_per_ping=0.0, active_listing_count=5, coverage_ratio=1.0, confidence=1.0)

    res_0 = score_heatzone_v3_feature(inp_0)
    res_1 = score_heatzone_v3_feature(inp_1)
    res_5 = score_heatzone_v3_feature(inp_5)

    assert res_0.rent_feasibility_score == 0.0
    assert res_1.rent_feasibility_score == 0.08
    assert res_5.rent_feasibility_score == 0.40
    assert res_0.rent_feasibility_score <= res_1.rent_feasibility_score <= res_5.rent_feasibility_score


def test_heatzone_v3_saturated_state_when_competitor_saturated_and_zero_own_stores() -> None:
    """C3: SATURATED is reached when competitor capacity is saturated even if own_store_count == 0."""
    inp = HeatZoneV3Input(
        h3_index="884a1072b7fffff",
        population=4000.0,
        household_count=1500.0,
        poi_count=5,
        active_competitor_count=8,
        competitor_capacity=60.0,
        own_store_count=0,
        own_store_machine_capacity=0.0,
        coverage_ratio=1.0,
        confidence=1.0,
    )
    res = score_heatzone_v3_feature(inp)
    assert res.state is HeatZoneV3State.SATURATED
    assert res.unmet_demand_score < 0.25


def test_heatzone_v3_r1_store_capacity_isolation_across_cells_and_catchments() -> None:
    """R1: Store capacities must be spatially isolated to matching cells/catchments, not attributed to every cell."""
    cell_a = _sample_market_cell_profile(h3_index="884a1072b7fffff", total_population=6000.0, active_competitors=1, total_capacity=10.0)
    cell_b = _sample_market_cell_profile(h3_index="884a1072b1fffff", total_population=6000.0, active_competitors=1, total_capacity=10.0)
    cell_c = _sample_market_cell_profile(h3_index="884a1072b3fffff", total_population=6000.0, active_competitors=1, total_capacity=10.0)

    doc = MarketCellProfileDocument(
        profile_id="mcp-doc-multi",
        cells=[cell_a, cell_b, cell_c],
        h3_resolution=8,
        period_grain=MarketCellPeriodGrain.MONTHLY,
        period_key="2026-08",
        generated_at="2026-08-14T14:40:00Z",
        source_support=_sample_source_support(),
    )

    cap_a = _sample_machine_capacity(store_id="store-a", machine_count=10, evidence_ref="884a1072b7fffff")
    cap_b = _sample_machine_capacity(store_id="store-b", machine_count=10, evidence_ref="884a1072b1fffff")
    cap_c = _sample_machine_capacity(store_id="store-c", machine_count=10, evidence_ref="884a1072b3fffff")

    runner = HeatZoneV3ShadowRunner()
    batch = runner.evaluate_market_cells(
        doc,
        own_store_capacities=[cap_a, cap_b, cap_c],
    )

    assert batch.total_evaluated == 3
    assert batch.scored_count == 3
    assert batch.abstained_count == 0

    for score_res in batch.scores:
        assert score_res.input_dimensions["own_store_count"] == 1
        assert score_res.input_dimensions["own_store_machine_capacity"] == 10.0
        assert score_res.cannibalization_risk_score < 0.50
        assert score_res.state != HeatZoneV3State.SATURATED
        assert score_res.score is not None and score_res.score > 55.0


def test_heatzone_v3_r2_distinct_store_count_with_multiple_machine_classes() -> None:
    """R2: own_store_count counts distinct store IDs, not total machine capacity records (1 store with 2 classes == 1 store)."""
    cell = _sample_market_cell_profile(h3_index="884a1072b7fffff")
    cap_washer = _sample_machine_capacity(
        store_id="store-multi-001",
        machine_class=MachineClass.WASHER,
        machine_count=8,
        evidence_ref="884a1072b7fffff",
    )
    cap_dryer = _sample_machine_capacity(
        store_id="store-multi-001",
        machine_class=MachineClass.DRYER,
        machine_count=6,
        evidence_ref="884a1072b7fffff",
    )

    adapted = from_market_cell_profile(
        cell,
        own_store_capacities=[cap_washer, cap_dryer],
    )

    assert adapted.own_store_count == 1
    assert adapted.own_store_machine_capacity == 14.0
    assert len(adapted.own_store_capacities) == 2

    # Score result check
    res = score_heatzone_v3_feature(adapted)
    assert res.input_dimensions["own_store_count"] == 1
    assert res.input_dimensions["own_store_machine_capacity"] == 14.0


def test_heatzone_v3_r3_readiness_usable_with_gaps_is_supported() -> None:
    """R3: ReadinessLevel.usable_with_gaps is supported and does not collapse to unsupported or OUT_OF_SUPPORT_BOUNDS."""
    cell = _sample_market_cell_profile(
        h3_index="884a1072b7fffff",
        overall_readiness=ReadinessLevel.usable_with_gaps,
        has_gaps=True,
    )
    adapted = from_market_cell_profile(cell)

    assert adapted.support_level == "supported"
    assert adapted.overall_readiness == ReadinessLevel.usable_with_gaps
    assert adapted.has_coverage_gaps is True

    res = score_heatzone_v3_feature(adapted)
    assert res.abstained is False
    assert res.score is not None
    assert AbstainReasonCode.OUT_OF_SUPPORT_BOUNDS.value not in res.abstain_reasons
    assert "domain_coverage_gaps_present" in res.warnings

    # Also verify from_catchment_profile
    prof = CatchmentProfile(
        profile_id="prof-gaps-001",
        period_grain=CatchmentPeriodGrain.MONTHLY,
        period_key="2026-08",
        origin=CatchmentOrigin(
            origin_id="orig-001",
            origin_h3="884a1072b7fffff",
            latitude=25.04,
            longitude=121.56,
            origin_geom={"type": "Point", "coordinates": [121.56, 25.04]},
            county="Taipei",
            district="Zhongshan",
        ),
        boundary=CatchmentBoundary(
            catchment_id="boundary-001",
            travel_mode=TravelMode.motorcycle,
            cutoff_seconds=600,
            routing_engine="valhalla",
            graph_version="2026.08",
            h3_cells=["884a1072b7fffff"],
            h3_resolution=9,
            total_cells_count=1,
            geom={"type": "Polygon", "coordinates": [[[121.5, 25.0], [121.6, 25.0], [121.6, 25.1], [121.5, 25.0]]]},
        ),
        demographics=CatchmentDemographics(
            status=DomainStatus.available,
            total_population=8500.0,
            household_count=3200.0,
            daytime_population_ratio=1.5,
        ),
        competitors=CatchmentCompetitors(
            status=DomainStatus.available,
            active_competitors=3,
            total_capacity=50.0,
            stores_by_category={"laundromat": 3, "convenience": 5},
        ),
        rent=CatchmentRent(
            status=DomainStatus.available,
            median_rent_per_ping=2500.0,
            sample_count=15,
        ),
        mobility=CatchmentMobility(
            status=DomainStatus.available,
            activity_population=7000.0,
            resident_population=5000.0,
        ),
        traffic=CatchmentTrafficAccess(status=DomainStatus.available),
        coverage=CatchmentCoverage(
            overall_readiness=CatchmentReadinessLevel.usable_with_gaps,
            domain_coverage={"DEMOGRAPHICS": "complete", "COMPETITOR": "complete", "RENT": "complete"},
            has_gaps=True,
        ),
        source_support=_sample_source_support(),
    )
    adapted_catch = from_catchment_profile(prof)
    assert adapted_catch.support_level == "supported"
    res_catch = score_heatzone_v3_feature(adapted_catch)
    assert res_catch.abstained is False
    assert res_catch.score is not None
    assert AbstainReasonCode.OUT_OF_SUPPORT_BOUNDS.value not in res_catch.abstain_reasons


