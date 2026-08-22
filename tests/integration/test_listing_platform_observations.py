"""Integration verification test suite for ODP-LISTING-001.

Contract: `odayplus.assisted-listing-platform-bridge.v2`
Requires: `odayplus.market-data-facade.v2`, `emgi.property-observation.v1`

Acceptance criteria:
1. Keep Intake, Property, Listing, Revision, Correction, Identity, Review and Candidate Promotion in odayplus.
2. Import platform observations without creating a second listing authority.
3. Strict field precedence: Manual Correction > Normalized Intake > Platform Observation Raw.
4. Governed XLSX import matches and binds platform PropertyEntity identities (confidence >= 0.85).
5. Candidate promotion saga incorporates platform property observations and rent benchmarks with tenant isolation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from modules.external_data.application.assisted_intake import (
    RetrievalResult,
    effective_fields,
    match_listing,
    parse_snapshot,
)
from modules.external_data.application.market_data_facade import (
    MarketDataAuthorizationError,
    MarketDataFacade,
)
from modules.external_data.application.xlsx_import import (
    map_and_validate_rows,
)
from modules.external_data.infrastructure.data_platform_client import (
    DataPlatformClient,
    InMemoryDataPlatformTransport,
)
from modules.listing.application.platform_bridge import (
    BRIDGE_CONTRACT,
    BRIDGE_VERSION,
    AssistedListingPlatformBridge,
)
from modules.listing.application.promotion import PromotionService
from modules.listing.domain.identity_graph import IdentityGraph, SourceIdentity
from modules.listing.domain.intake_states import (
    Actor,
    DenialCode,
    DomainValidationError,
    PrincipalRole,
    TransitionContext,
)
from modules.listing.domain.models import (
    CandidateSiteDraft,
)
from modules.listing.infrastructure.repositories import InMemoryListingRepository
from packages.oday_data_product_contracts_client.models.property_observation import (
    BenchmarkFallbackLevel,
    ListingStatus,
    ListingStatusHistory,
    PropertyEntity,
    PropertyListingObservation,
    PropertyObservationDocument,
    PropertyTier,
    RentBenchmark,
)
from shared.auth import Principal, Role, Scope
from shared.domain.models import AddressLocation, Listing

# ---------------------------------------------------------------------------
# Fixtures & Sample Platform Data
# ---------------------------------------------------------------------------

SAMPLE_PROPERTY_ENTITY = PropertyEntity(
    property_id="PROP-TPE-DAAN-001",
    county="台北市",
    district="大安區",
    normalized_address="台北市大安區復興南路二段100號",
    created_at="2026-07-01T00:00:00Z",
    updated_at="2026-07-15T00:00:00Z",
    h3_index="8928308281bffff",
    latitude=25.026,
    longitude=121.543,
    total_floors=7,
)

SAMPLE_LISTING_OBSERVATION = PropertyListingObservation(
    listing_obs_id="OBS-591-99881122",
    property_id="PROP-TPE-DAAN-001",
    source_listing_id="s591-99881122",
    channel="591",
    monthly_rent=48000.0,
    floor_area_ping=25.0,
    target_floor="1F",
    listing_status=ListingStatus.ACTIVE,
    observed_at="2026-07-15T08:00:00Z",
    first_seen_at="2026-07-01T00:00:00Z",
    last_seen_at="2026-07-15T08:00:00Z",
)

SAMPLE_RENT_BENCHMARK = RentBenchmark(
    benchmark_id="BM-DAAN-RETAIL-202607",
    period_year_month="2026-07",
    county="台北市",
    district="大安區",
    median_rent_per_ping=1920.0,
    p25_rent_per_ping=1650.0,
    p75_rent_per_ping=2200.0,
    sample_count=42,
    confidence_pct=0.92,
    fallback_level=BenchmarkFallbackLevel.CELL,
    property_tier=PropertyTier.RETAIL_STORE,
    created_at="2026-07-01T00:00:00Z",
    updated_at="2026-07-15T00:00:00Z",
)

SAMPLE_OBSERVATION_DOCUMENT = PropertyObservationDocument(
    created_at="2026-07-15T08:00:00Z",
    properties=[SAMPLE_PROPERTY_ENTITY],
    listing_observations=[SAMPLE_LISTING_OBSERVATION],
    status_histories=[
        ListingStatusHistory(
            listing_obs_id="OBS-591-99881122",
            property_id="PROP-TPE-DAAN-001",
            current_status=ListingStatus.ACTIVE,
            days_on_market=14.0,
            first_seen_at="2026-07-01T00:00:00Z",
            last_seen_at="2026-07-15T08:00:00Z",
        )
    ],
)


def _build_transport() -> InMemoryDataPlatformTransport:
    transport = InMemoryDataPlatformTransport()
    transport.store_document(
        "emgi.property-observation.v1",
        "DOC-001",
        SAMPLE_OBSERVATION_DOCUMENT.to_dict(),
    )
    transport.store_document(
        "emgi.property-observation.v1",
        SAMPLE_PROPERTY_ENTITY.property_id,
        SAMPLE_OBSERVATION_DOCUMENT.to_dict(),
    )
    transport.store_document(
        "emgi.property-observation.v1",
        SAMPLE_LISTING_OBSERVATION.source_listing_id,
        SAMPLE_OBSERVATION_DOCUMENT.to_dict(),
    )
    transport.store_document(
        "emgi.property-observation.v1",
        SAMPLE_LISTING_OBSERVATION.listing_obs_id,
        SAMPLE_OBSERVATION_DOCUMENT.to_dict(),
    )
    return transport


def _authorized_principal(tenant_id: str = "tenant-a", role: Role = Role.EXPANSION_USER) -> Principal:
    return Principal(
        subject_id="user-expansion-1",
        roles=frozenset({role}),
        scope=Scope(tenant_id=tenant_id),
        authenticated=True,
    )


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_bridge_contract_constants_and_identity() -> None:
    """Verify that AssistedListingPlatformBridge exposes the required contract."""
    bridge = AssistedListingPlatformBridge()
    assert bridge.contract == BRIDGE_CONTRACT
    assert bridge.contract == "odayplus.assisted-listing-platform-bridge.v2"
    assert bridge.version == BRIDGE_VERSION
    assert bridge.version == "2.0.0"


def test_market_data_facade_authorized_property_observation_reads() -> None:
    """Verify authorized facade read of PropertyEntity, ListingObservation, and Document."""
    client = DataPlatformClient(transport=_build_transport())
    facade = MarketDataFacade(client=client)
    principal = _authorized_principal("tenant-a", Role.EXPANSION_USER)

    # 1. Read PropertyObservationDocument
    doc = facade.get_property_observation_document(
        listing_id="s591-99881122",
        tenant_id="tenant-a",
        principal=principal,
    )
    assert isinstance(doc, PropertyObservationDocument)
    assert len(doc.properties) == 1
    assert doc.properties[0].property_id == "PROP-TPE-DAAN-001"
    assert len(doc.listing_observations) == 1
    assert doc.listing_observations[0].listing_obs_id == "OBS-591-99881122"

    # 2. Read PropertyEntity
    prop = facade.get_property_entity(
        "PROP-TPE-DAAN-001",
        tenant_id="tenant-a",
        principal=principal,
    )
    assert isinstance(prop, PropertyEntity)
    assert prop.county == "台北市"
    assert prop.district == "大安區"

    # 3. Read ListingObservation
    obs = facade.get_listing_observation(
        "s591-99881122",
        tenant_id="tenant-a",
        principal=principal,
    )
    assert isinstance(obs, PropertyListingObservation)
    assert obs.monthly_rent == 48000.0
    assert obs.floor_area_ping == 25.0


def test_market_data_facade_authorization_and_tenant_isolation() -> None:
    """Verify that unauthorized roles and cross-tenant reads fail closed."""
    client = DataPlatformClient(transport=_build_transport())
    facade = MarketDataFacade(client=client)

    # 1. Unauthenticated / None principal
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_property_entity("PROP-TPE-DAAN-001", principal=None)
    assert "Authentication required" in str(exc_info.value)

    # 2. Cross-tenant access denied
    attacker_principal = _authorized_principal("tenant-attacker", Role.EXPANSION_USER)
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_property_entity(
            "PROP-TPE-DAAN-001",
            tenant_id="tenant-victim",
            principal=attacker_principal,
        )
    assert "Cross-tenant access denied" in str(exc_info.value)


def test_platform_bridge_reconciliation_and_identity_binding() -> None:
    """Verify AssistedListingPlatformBridge reconciles observations and binds identities in IdentityGraph."""
    client = DataPlatformClient(transport=_build_transport())
    facade = MarketDataFacade(client=client)
    identity_graph = IdentityGraph()
    bridge = AssistedListingPlatformBridge(facade=facade, identity_graph=identity_graph)
    principal = _authorized_principal("tenant-a", Role.EXPANSION_USER)

    # 1. Reconcile observation
    enrichment = bridge.reconcile_observation_for_listing(
        source_listing_id="s591-99881122",
        tenant_id="tenant-a",
        principal=principal,
    )
    assert enrichment is not None
    assert enrichment.property_id == "PROP-TPE-DAAN-001"
    assert enrichment.listing_obs_id == "OBS-591-99881122"
    assert enrichment.is_evidentiary is True

    # 2. Bind identity in IdentityGraph
    edge = bridge.reconcile_and_bind_identity(
        tenant_id="tenant-a",
        listing_id="LST-ODAY-001",
        property_id="PROP-TPE-DAAN-001",
        source_entity_id="s591-99881122",
        match_strategy="platform_observation",
        confidence=0.95,
    )
    assert edge.property_id == "PROP-TPE-DAAN-001"
    assert edge.listing_id == "LST-ODAY-001"
    assert edge.source.source_id == "platform.property_observation"
    assert edge.confidence == 0.95

    # Verify resolution in identity graph
    lineage = identity_graph.resolve_source(
        SourceIdentity("tenant-a", "platform.property_observation", "s591-99881122")
    )
    assert lineage.effective_property_id == "PROP-TPE-DAAN-001"


def test_assisted_intake_field_precedence_with_platform_observations() -> None:
    """Verify strict field precedence: Manual Correction > Normalized Intake > Platform Observation Raw."""
    raw_snapshot = {
        "source_listing_id": "s591-99881122",
        "address_raw": "台北市大安區復興南路二段100號1F",
        "rent_amount": 48000.0,
        "area_ping": 25.0,
        "floor": "1F",
        "listing_type": "店面",
        "listing_status": "active",
        "confidence": 0.90,
        "property_id": "PROP-TPE-DAAN-001",
    }
    retrieval = RetrievalResult(
        snapshot_id="SNAP-TEST-001",
        captured_at="2026-07-15T08:00:00Z",
        raw=raw_snapshot,
    )

    # 1. Parse snapshot
    fields = parse_snapshot(retrieval)
    assert fields["address"]["normalizedValue"] == "台北市大安區復興南路二段100號"
    assert fields["rent"]["normalizedValue"] == 48000.0
    assert fields["areaPing"]["normalizedValue"] == 25.0
    assert fields["propertyId"]["normalizedValue"] == "PROP-TPE-DAAN-001"

    # Effective fields before correction -> uses normalized values
    effective_before = effective_fields(fields)
    assert effective_before["rent"] == 48000.0
    assert effective_before["address"] == "台北市大安區復興南路二段100號"

    # 2. Apply manual correction to rent (operator negotiated rent down to 45000)
    fields["rent"]["correctedValue"] = 45000.0
    fields["rent"]["correctionReason"] = "現場洽詢屋主同意降價至 45,000"

    # Effective fields after correction -> manual correction strictly wins
    effective_after = effective_fields(fields)
    assert effective_after["rent"] == 45000.0
    assert effective_after["rent"] != fields["rent"]["sourceValue"]

    # 3. Match listing with corrected values against existing corpus
    existing_listings = [
        {
            "id": "LST-EXISTING-001",
            "sourceId": "SRC-591",
            "sourceListingId": "s591-99881122",
            "canonicalUrl": "",
            "address": "台北市大安區復興南路二段100號",
            "rentPerMonth": 48000.0,
            "areaPing": 25.0,
            "floor": "1F",
            "contentFingerprint": "old_fingerprint",
        }
    ]
    match = match_listing(
        values=effective_after,
        canonical_url="",
        source_id="SRC-591",
        fingerprint="new_fingerprint_45k",
        listings=existing_listings,
    )
    # Same provider ID with changed rent is classified as REVISION, preserving single listing authority
    assert match.outcome == "REVISION"
    assert match.target_listing_id == "LST-EXISTING-001"
    assert "租金" in match.summary


def test_xlsx_import_matches_platform_property_entities() -> None:
    """Verify governed XLSX import matches and binds platform PropertyEntity when confidence >= 0.85."""
    known_properties = {
        "台北市大安區復興南路二段100號": ("PROP-TPE-DAAN-001", 1.0),
        "台北市大安區復興南路二段102號": ("PROP-TPE-DAAN-002", 0.90),
        "新北市板橋區府中路50號": ("PROP-NWT-BANQIAO-001", 0.80),  # Below 0.85 threshold
    }

    def property_resolver(norm_address: str) -> tuple[str | None, float]:
        return known_properties.get(norm_address, (None, 0.0))

    raw_rows = [
        {
            "_row_index": 1,
            "地址": "台北市大安區復興南路二段100號1F",
            "租金": "45000",
            "坪數": "25",
            "樓層": "1F",
        },
        {
            "_row_index": 2,
            "地址": "台北市大安區復興南路二段102號1F",
            "租金": "50000",
            "坪數": "20",
            "樓層": "1F",
        },
        {
            "_row_index": 3,
            "地址": "新北市板橋區府中路50號1F",
            "租金": "35000",
            "坪數": "18",
            "樓層": "1F",
        },
    ]

    mapping, valid_rows, row_errors = map_and_validate_rows(
        raw_rows,
        property_resolver=property_resolver,
    )

    assert len(row_errors) == 0
    assert len(valid_rows) == 3

    # Row 1: exact match (confidence 1.0 >= 0.85) -> bound
    assert valid_rows[0]["platform_property_id"] == "PROP-TPE-DAAN-001"
    assert valid_rows[0]["property_match_confidence"] == 1.0

    # Row 2: high confidence match (0.90 >= 0.85) -> bound
    assert valid_rows[1]["platform_property_id"] == "PROP-TPE-DAAN-002"
    assert valid_rows[1]["property_match_confidence"] == 0.90

    # Row 3: low confidence match (0.80 < 0.85) -> NOT bound
    assert "platform_property_id" not in valid_rows[2]


def test_candidate_promotion_saga_with_platform_observations_and_benchmarks() -> None:
    """Verify Candidate Promotion saga incorporates platform observation metadata and RentBenchmark."""
    # Setup in-memory repositories
    listing_repo = InMemoryListingRepository()

    intake_repo = MagicMock()
    intake_repo.get_listing_intake.return_value = {
        "intakeId": "IN-001",
        "tenantId": "tenant-a",
        "matchResult": {"targetListingId": "LST-001"},
    }

    promo_repo = MagicMock()
    promotions_store: dict[str, Any] = {}
    promo_repo.list_promotions.side_effect = lambda: list(promotions_store.values())
    promo_repo.get_promotion.side_effect = lambda pid: promotions_store.get(pid)
    promo_repo.save_promotion.side_effect = lambda p: promotions_store.update({p["promotion_decision_id"]: p})

    promotion_service = PromotionService(
        promotion_repository=promo_repo,
        listing_repository=listing_repo,
        intake_repository=intake_repo,
    )

    # Create listing with platform observation and rent benchmark metadata
    address = AddressLocation(
        address_id="ADDR-001",
        raw_address="台北市大安區復興南路二段100號",
        normalized_address="台北市大安區復興南路二段100號",
        geocode_confidence=0.95,
        h3_res_9="8928308281bffff",
    )
    listing = Listing(
        listing_id="LST-001",
        source_listing_id="s591-99881122",
        source_id="SRC-591",
        address_id="ADDR-001",
        rent_amount=45000.0,
        area_ping=25.0,
        floor="1F",
        listing_status="active",
        confidence=0.95,
        tenant_id="tenant-a",
    )

    from apps.api.app.routes.listings import ListingAdapterWrapper, V1ListingRepositoryAdapter
    from modules.listing.domain.models import ListingDedupKey

    key = ListingDedupKey(
        source_id=listing.source_id,
        source_listing_id=listing.source_listing_id,
        normalized_address=address.normalized_address,
        rent_amount=listing.rent_amount,
        area_ping=listing.area_ping,
    )
    listing_repo.save_listing(listing, address, key)

    listing_dict = {
        "id": "LST-001",
        "listing_id": "LST-001",
        "source_listing_id": "s591-99881122",
        "source_id": "SRC-591",
        "address": "台北市大安區復興南路二段100號",
        "address_id": "ADDR-001",
        "rent_amount": 45000.0,
        "rentPerMonth": 45000.0,
        "area_ping": 25.0,
        "areaPing": 25.0,
        "floor": "1F",
        "status": "active",
        "h3_index": "8928308281bffff",
        "h3Index": "8928308281bffff",
        "geocode_confidence": 0.95,
        "geocodeConfidence": 0.95,
        "property_id": "PROP-TPE-DAAN-001",
        "listing_obs_id": "OBS-591-99881122",
        "rent_benchmark": SAMPLE_RENT_BENCHMARK.to_dict(),
        "rent_benchmark_median": 1920.0,
        "rent_benchmark_p25": 1650.0,
        "rent_benchmark_p75": 2200.0,
        "rent_benchmark_sample_count": 42,
        "rent_benchmark_id": "BM-DAAN-RETAIL-202607",
    }

    adapted_listing_repo = V1ListingRepositoryAdapter(listing_repo)
    adapted_listing_repo.get_listing = MagicMock(return_value=ListingAdapterWrapper(listing_dict))
    promotion_service = PromotionService(
        promotion_repository=promo_repo,
        listing_repository=adapted_listing_repo,
        intake_repository=intake_repo,
    )

    # 1. Proposer requests promotion
    proposer_actor = Actor(
        actor_id="user-proposer",
        role=PrincipalRole.EXPANSION_STAFF,
        tenant_id="tenant-a",
    )
    propose_ctx = TransitionContext(
        actor=proposer_actor,
        idempotency_key="idemp-promo-req-001",
        correlation_id="corr-promo-001",
    )

    promo = promotion_service.request_promotion(
        intake_id="IN-001",
        target_format_code="ODAY_G2",
        reason="符合 G2 標準店型且租金低於市場基準中位數",
        gate_snapshot_sha256="a" * 64,
        context=propose_ctx,
    )
    assert promo["status"] == "PENDING_REVIEW"
    promo_id = promo["promotion_decision_id"]

    # 2. Reviewer approves promotion
    reviewer_actor = Actor(
        actor_id="user-reviewer",
        role=PrincipalRole.EXPANSION_MANAGER,
        tenant_id="tenant-a",
    )
    review_ctx = TransitionContext(
        actor=reviewer_actor,
        idempotency_key="idemp-promo-rev-001",
        correlation_id="corr-promo-001",
    )

    completed_promo = promotion_service.review_promotion(
        promotion_decision_id=promo_id,
        decision="APPROVE",
        reason="審核通過：商圈效益優異，核准立案為候選點",
        risk_acknowledged=True,
        context=review_ctx,
    )
    assert completed_promo["status"] == "COMPLETED"

    # 3. Verify that CandidateSiteDraft in repo captured observation & benchmark fields
    candidates = listing_repo.list_candidates()
    assert len(candidates) == 1
    cand = candidates[0]
    assert isinstance(cand, CandidateSiteDraft)
    assert cand.property_entity_id == "PROP-TPE-DAAN-001"
    assert cand.listing_observation_id == "OBS-591-99881122"
    assert cand.rent_benchmark_median == 1920.0
    assert cand.rent_benchmark_p25 == 1650.0
    assert cand.rent_benchmark_p75 == 2200.0
    assert cand.rent_benchmark_sample_count == 42
    assert cand.rent_benchmark_id == "BM-DAAN-RETAIL-202607"

    # Verify to_card_dict contains observation fields
    card = cand.to_card_dict()
    assert card["propertyId"] == "PROP-TPE-DAAN-001"
    assert card["listingObsId"] == "OBS-591-99881122"
    assert card["rentBenchmarkMedian"] == 1920.0
    assert card["rentBenchmarkSampleCount"] == 42


def test_candidate_promotion_tenant_isolation_mismatch() -> None:
    """Verify that promotion request across different tenants is rejected."""
    intake_repo = MagicMock()
    intake_repo.get_listing_intake.return_value = {
        "intakeId": "IN-002",
        "tenantId": "tenant-victim",
        "matchResult": {"targetListingId": "LST-002"},
    }
    promo_repo = MagicMock()
    promo_repo.list_promotions.return_value = []

    service = PromotionService(
        promotion_repository=promo_repo,
        listing_repository=MagicMock(),
        intake_repository=intake_repo,
    )

    attacker_actor = Actor(
        actor_id="user-attacker",
        role=PrincipalRole.EXPANSION_STAFF,
        tenant_id="tenant-attacker",
    )
    ctx = TransitionContext(
        actor=attacker_actor,
        idempotency_key="idemp-attack-001",
    )

    with pytest.raises(DomainValidationError) as exc_info:
        service.request_promotion(
            intake_id="IN-002",
            target_format_code="ODAY_G2",
            reason="Illegal cross-tenant attempt",
            gate_snapshot_sha256="b" * 64,
            context=ctx,
        )
    assert exc_info.value.code == DenialCode.TENANT_SCOPE_DENIED
