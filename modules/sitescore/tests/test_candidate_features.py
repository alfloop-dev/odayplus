"""Recorded candidate facts must survive conversion into model features."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from models.shared_ml.production_runtime import ProductionModelInputError
from modules.listing.domain.models import CandidateSiteDraft
from modules.sitescore.application.candidate_features import feature_from_candidate
from shared.domain import AddressLocation, CandidateSite, Listing


def _draft() -> CandidateSiteDraft:
    return CandidateSiteDraft(
        listing=Listing(
            listing_id="synthetic-listing", snapshot_id="synthetic-source-snapshot",
            rent_amount=40000, area_ping=25, frontage_m=5, confidence=0.93,
        ),
        address=AddressLocation(
            latitude=25.1, longitude=121.4, h3_res_9="8926308280fffff",
            geocode_confidence=0.9,
        ),
        candidate_site=CandidateSite(
            candidate_site_id="synthetic-candidate", target_format_code="ODAY_G2",
        ),
        heat_zone_id="synthetic-heat-zone",
        feature_snapshot_time=datetime(2026, 10, 9, tzinfo=UTC),
        prior_90d_cell_net_revenue=75000,
        prior_90d_cell_transaction_count=180,
        prior_90d_cell_store_count=2,
    )


def test_candidate_feature_builder_preserves_recorded_time_and_measurements() -> None:
    draft = _draft()
    feature = feature_from_candidate(draft, tenant_id="authorized-synthetic-tenant")
    assert feature.tenant_id == "authorized-synthetic-tenant"
    assert feature.feature_snapshot_time is draft.feature_snapshot_time
    assert feature.prior_90d_cell_net_revenue == 75000
    assert feature.prior_90d_cell_transaction_count == 180
    assert feature.prior_90d_cell_store_count == 2
    assert feature.h3_index == draft.address.h3_res_9
    assert feature.source_snapshot_ids == (draft.listing.snapshot_id,)
    assert feature.average_confidence == 0.9


def test_candidate_feature_builder_keeps_absent_evidence_absent() -> None:
    draft = _draft()
    draft.listing = replace(draft.listing, confidence=None, snapshot_id="")
    draft.address = replace(draft.address, h3_res_9="")
    draft.candidate_site = replace(draft.candidate_site, target_format_code="")
    draft.feature_snapshot_time = None
    draft.prior_90d_cell_net_revenue = None
    draft.prior_90d_cell_transaction_count = None
    draft.prior_90d_cell_store_count = None
    feature = feature_from_candidate(draft, tenant_id="authorized-synthetic-tenant")
    assert feature.average_confidence is None
    assert feature.data_quality_score is None
    assert feature.feature_snapshot_time is None
    assert feature.prior_90d_cell_net_revenue is None
    assert feature.prior_90d_cell_transaction_count is None
    assert feature.prior_90d_cell_store_count is None
    assert feature.target_format_code == ""
    assert feature.h3_index == ""
    assert feature.source_snapshot_ids == ()


def test_candidate_feature_builder_rejects_ambiguous_time_without_restamping() -> None:
    draft = _draft()
    original = datetime(2026, 10, 9)
    draft.feature_snapshot_time = original
    with pytest.raises(ProductionModelInputError, match="TIMEZONE_REQUIRED"):
        feature_from_candidate(draft, tenant_id="authorized-synthetic-tenant")
    assert draft.feature_snapshot_time is original
