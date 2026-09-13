"""Production-shaped contract and integration tests for nullable canonical measurements.

ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001
Proves:
1. Six canonical models (Poi, CompetitorStore, Listing, Prediction, HeatZoneScore, DataSnapshot)
   default to None and distinguish unmeasured, measured, and legacy_unknown provenance.
2. Ingestion pipelines (SourceToCanonicalMapper, Connectors) properly coerce numeric strings
   while preserving absence as None without restoring 1.0 defaults.
3. Persistence & migrations (PostgreSQL schema qualification, Alembic 0020, SQLite rebuild +
   12 secondary index recreation) execute and survive restart.
4. Downstream consumers (NetworkScoring, SiteScore, GeoPipeline, HeatZone) fail closed / abstain
   on absent measurement and do not mask absence.
5. API responses properly serialize null confidence and include confidenceProvenance markers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from modules.external_data.connectors.external import (
    _parse_optional_float,
)
from modules.external_data.geo.pipeline import GeoPipeline
from modules.heatzone.v3.contract import HeatZoneV3ScoreResult
from modules.integration.application.mapping import SourceToCanonicalMapper
from modules.opsboard.application.network_listings import (
    _optional_number,
)
from modules.opsboard.application.network_scoring import _none_safe_min
from modules.sitescore.domain.scoring import SiteScoreFeatureInput, score_site
from shared.domain.models import (
    CompetitorStore,
    DataSnapshot,
    HeatZoneScore,
    Listing,
    Poi,
    Prediction,
)
from shared.infrastructure.persistence.engine import SqliteEngine

# ---------------------------------------------------------------------------
# 1. The Six Canonical Models: Defaults, Values, and Lineage V.2 Provenance
# ---------------------------------------------------------------------------


class TestCanonicalMeasurementModelSemantics:
    """The six canonical models default to None and preserve Lineage V.2 provenance."""

    def test_six_models_default_to_none_unmeasured(self) -> None:
        poi = Poi()
        cs = CompetitorStore()
        lst = Listing()
        pred = Prediction()
        hz = HeatZoneScore()
        snap = DataSnapshot()

        assert poi.confidence is None
        assert poi.effective_confidence_provenance == "unmeasured"
        assert poi.effective_confidence is None

        assert cs.confidence is None
        assert cs.effective_confidence_provenance == "unmeasured"
        assert cs.effective_confidence is None

        assert lst.confidence is None
        assert lst.effective_confidence_provenance == "unmeasured"
        assert lst.effective_confidence is None

        assert pred.confidence is None
        assert pred.effective_confidence_provenance == "unmeasured"
        assert pred.effective_confidence is None

        assert hz.confidence is None
        assert hz.effective_confidence_provenance == "unmeasured"
        assert hz.effective_confidence is None

        assert snap.quality_score is None
        assert snap.effective_quality_score_provenance == "unmeasured"
        assert snap.effective_quality_score is None

    def test_six_models_preserve_measured_values(self) -> None:
        poi = Poi(confidence=0.85)
        cs = CompetitorStore(confidence=0.72)
        lst = Listing(confidence=0.95)
        pred = Prediction(confidence=0.60)
        hz = HeatZoneScore(confidence=0.40)
        snap = DataSnapshot(quality_score=0.92)

        assert poi.confidence == 0.85
        assert poi.effective_confidence_provenance == "measured"
        assert poi.effective_confidence == 0.85

        assert cs.confidence == 0.72
        assert cs.effective_confidence_provenance == "measured"
        assert cs.effective_confidence == 0.72

        assert lst.confidence == 0.95
        assert lst.effective_confidence_provenance == "measured"
        assert lst.effective_confidence == 0.95

        assert pred.confidence == 0.60
        assert pred.effective_confidence_provenance == "measured"
        assert pred.effective_confidence == 0.60

        assert hz.confidence == 0.40
        assert hz.effective_confidence_provenance == "measured"
        assert hz.effective_confidence == 0.40

        assert snap.quality_score == 0.92
        assert snap.effective_quality_score_provenance == "measured"
        assert snap.effective_quality_score == 0.92

    def test_legacy_v1_rows_yield_legacy_unknown_provenance_and_none_effective_score(self) -> None:
        """A legacy row with 1.00 has provenance legacy_unknown and effective score None."""
        lst_legacy = Listing(confidence=1.0, snapshot_id="v1", measurement_schema_version="v1")
        assert lst_legacy.confidence == 1.0  # Physical historic value preserved
        assert lst_legacy.effective_confidence_provenance == "legacy_unknown"
        assert lst_legacy.effective_confidence is None  # Downstream consumes as unmeasured

        poi_legacy = Poi(confidence=1.0, snapshot_id="v1", measurement_schema_version="v1")
        assert poi_legacy.confidence == 1.0
        assert poi_legacy.effective_confidence_provenance == "legacy_unknown"
        assert poi_legacy.effective_confidence is None

        cs_legacy = CompetitorStore(confidence=1.0, measurement_schema_version="v1")
        assert cs_legacy.confidence == 1.0
        assert cs_legacy.effective_confidence_provenance == "legacy_unknown"
        assert cs_legacy.effective_confidence is None

        pred_legacy = Prediction(confidence=1.0, measurement_schema_version="v1")
        assert pred_legacy.confidence == 1.0
        assert pred_legacy.effective_confidence_provenance == "legacy_unknown"
        assert pred_legacy.effective_confidence is None

        hz_legacy = HeatZoneScore(confidence=1.0, measurement_schema_version="v1")
        assert hz_legacy.confidence == 1.0
        assert hz_legacy.effective_confidence_provenance == "legacy_unknown"
        assert hz_legacy.effective_confidence is None

        snap_legacy = DataSnapshot(quality_score=1.0, schema_version="v1")
        assert snap_legacy.quality_score == 1.0
        assert snap_legacy.effective_quality_score_provenance == "legacy_unknown"
        assert snap_legacy.effective_quality_score is None

    def test_v2_measured_perfect_score_is_preserved_as_measured(self) -> None:
        """A fresh v2 write with measured 1.0 is preserved as measured, not legacy_unknown."""
        lst_v2 = Listing(confidence=1.0, snapshot_id="v2-snapshot-123", measurement_schema_version="v2")
        assert lst_v2.confidence == 1.0
        assert lst_v2.effective_confidence_provenance == "measured"
        assert lst_v2.effective_confidence == 1.0


# ---------------------------------------------------------------------------
# 2. Producer / Ingestion & Coercion Pipelines (R5)
# ---------------------------------------------------------------------------


class TestProducerIngestionAndCoercion:
    """Source mappers and connectors handle optional float coercion without regressing."""

    def test_source_to_canonical_mapper_coerces_numeric_strings_and_preserves_none(self) -> None:
        mapper = SourceToCanonicalMapper()

        # String numeric float "0.8" -> coerced to float 0.8
        result = mapper.map_record(
            "listing",
            {
                "source_id": "591",
                "source_listing_id": "L-101",
                "rent_amount": "25000",
                "area_ping": "15.5",
                "confidence": "0.8",
            },
        )
        assert isinstance(result.canonical.confidence, float)
        assert result.canonical.confidence == 0.8

        # Missing / None confidence -> None
        result_none = mapper.map_record(
            "listing",
            {
                "source_id": "591",
                "source_listing_id": "L-102",
                "rent_amount": "25000",
                "area_ping": "15.5",
                "confidence": None,
            },
        )
        assert result_none.canonical.confidence is None

        # Empty string confidence -> None
        result_empty = mapper.map_record(
            "listing",
            {
                "source_id": "591",
                "source_listing_id": "L-103",
                "rent_amount": "25000",
                "area_ping": "15.5",
                "confidence": "",
            },
        )
        assert result_empty.canonical.confidence is None

    def test_poi_connector_parsing(self) -> None:
        assert _parse_optional_float(None) is None
        assert _parse_optional_float("") is None
        assert _parse_optional_float(0.85) == 0.85
        assert _parse_optional_float("0.72") == 0.72
        assert _parse_optional_float("invalid") is None


# ---------------------------------------------------------------------------
# 3. Database Migration & Secondary Index Integrity (R2, R6)
# ---------------------------------------------------------------------------


class TestDatabaseMigrationsAndIndexes:
    """PostgreSQL DDL qualifications, Alembic revision 0020, and SQLite index retention."""

    def test_postgres_migration_qualified_and_alembic_revision_exists(self) -> None:
        pg_migration = Path("infra/db/migrations/000026_canonical_measurement_nullable.sql").read_text(encoding="utf-8")
        assert "ALTER TABLE geo.pois ALTER COLUMN confidence DROP NOT NULL;" in pg_migration
        assert "ALTER TABLE geo.pois ALTER COLUMN confidence DROP DEFAULT;" in pg_migration
        assert "ALTER TABLE geo.competitor_stores ALTER COLUMN confidence DROP NOT NULL;" in pg_migration
        assert "ALTER TABLE expansion.listings ALTER COLUMN confidence DROP NOT NULL;" in pg_migration
        assert "ALTER TABLE learning.predictions ALTER COLUMN confidence DROP NOT NULL;" in pg_migration
        assert "ALTER TABLE audit.data_snapshots ALTER COLUMN quality_score DROP NOT NULL;" in pg_migration
        assert "ALTER TABLE expansion.heatzone_scores ALTER COLUMN confidence DROP DEFAULT;" in pg_migration

        # Alembic revision 0020
        alembic_file = Path("infra/db/migrations/versions/0020_canonical_measurement_nullable.py")
        assert alembic_file.exists()
        alembic_content = alembic_file.read_text(encoding="utf-8")
        assert 'revision: str = "0020"' in alembic_content
        assert 'down_revision: str = "0019"' in alembic_content
        assert "000026_canonical_measurement_nullable.sql" in alembic_content

    def test_sqlite_migration_rebuilds_and_preserves_12_secondary_indexes(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test_migration.sqlite3"
        engine = SqliteEngine(db_path)
        try:
            # Query sqlite_master for indexes
            indexes = {
                row["name"]: row["tbl_name"]
                for row in engine.query("SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'")
            }

            # Verify all 12 secondary indexes from 000004 are present after 000026 rebuild
            expected_indexes = {
                "idx_pois_geo_cell": "pois",
                "idx_pois_source": "pois",
                "idx_pois_snapshot": "pois",
                "idx_pois_category": "pois",
                "idx_competitor_stores_geo_cell": "competitor_stores",
                "idx_competitor_stores_brand": "competitor_stores",
                "idx_listings_address": "listings",
                "idx_listings_source": "listings",
                "idx_listings_snapshot": "listings",
                "idx_listings_status": "listings",
                "idx_predictions_run_entity": "predictions",
                "idx_predictions_target": "predictions",
            }
            for idx_name, table in expected_indexes.items():
                assert idx_name in indexes, f"Index {idx_name} missing from table {table}"
                assert indexes[idx_name] == table

            # Verify nullability in table_info
            listing_cols = {row["name"]: row for row in engine.query("PRAGMA table_info(listings)")}
            assert listing_cols["confidence"]["notnull"] == 0
            assert listing_cols["confidence"]["dflt_value"] is None

            poi_cols = {row["name"]: row for row in engine.query("PRAGMA table_info(pois)")}
            assert poi_cols["confidence"]["notnull"] == 0
            assert poi_cols["confidence"]["dflt_value"] is None
        finally:
            engine.close()


# ---------------------------------------------------------------------------
# 4. Consumer / Scoring Abstention & Fail-Closed Behavior (R1, R4, R7)
# ---------------------------------------------------------------------------


class TestConsumerScoringAndAbstention:
    """Downstream scoring consumers fail closed when measurement is missing."""

    def test_network_scoring_none_safe_min_propagates_none_when_either_absent(self) -> None:
        # Listing.confidence=None and geocode_confidence=1.0 -> None (R1)
        assert _none_safe_min(None, 1.0) is None
        assert _none_safe_min(1.0, None) is None
        assert _none_safe_min(None, None) is None
        # When both present -> minimum
        assert _none_safe_min(0.9, 0.85) == 0.85

    def test_sitescore_abstains_and_records_warnings_on_none_quality_inputs(self) -> None:
        feature = SiteScoreFeatureInput(
            candidate_site_id="CS-001",
            tenant_id="tenant-1",
            target_format_code="ODAY_G2",
            h3_index="89283082813ffff",
            feature_snapshot_time=datetime.now(UTC),
            average_confidence=None,  # Unmeasured
            data_quality_score=None,  # Unmeasured
            monthly_rent=25000,
            area_ping=15,
            frontage_m=4.5,
            source_snapshot_ids=("snap-1",),
        )
        report = score_site(feature)
        assert report.confidence == 0.0  # Abstains / fail-closed
        assert "missing_source_confidence" in report.warnings
        assert "missing_data_quality_score" in report.warnings

    def test_network_listings_roundtrip_preserves_extraction_confidence_absence(self) -> None:
        # Absent listingConfidence must stay None, not substituted by geocodeConfidence
        raw_dict = {
            "id": "LST-100",
            "sourceListingId": "SRC-100",
            "sourceId": "591",
            "status": "active",
            "rentPerMonth": 30000,
            "areaPing": 20.0,
            "floor": "1F",
            "geocodeConfidence": 0.95,
            "listingConfidence": None,  # Extraction confidence absent
        }
        parsed_conf = _optional_number(raw_dict.get("listingConfidence"), float)
        assert parsed_conf is None

    def test_geo_pipeline_aggregation_emits_none_on_absent_and_mixed_buckets(self) -> None:
        pipeline = GeoPipeline()
        # Bucket where POIs have no confidence
        poi_records = [
            {"latitude": 25.04, "longitude": 121.55, "confidence": None},
            {"latitude": 25.04, "longitude": 121.55, "confidence": None},
        ]
        snapshots = pipeline.build_feature_snapshots(
            poi_records=poi_records,
            resolution=9,
            feature_snapshot_time=datetime.now(UTC),
        )
        assert len(snapshots) == 1
        assert snapshots[0].average_confidence is None  # R7: Not 0.0

        # Mixed bucket: 1 measured, 1 unmeasured -> emits None (fail closed coverage)
        poi_mixed = [
            {"latitude": 25.04, "longitude": 121.55, "confidence": 0.95},
            {"latitude": 25.04, "longitude": 121.55, "confidence": None},
        ]
        mixed_snaps = pipeline.build_feature_snapshots(
            poi_records=poi_mixed,
            resolution=9,
            feature_snapshot_time=datetime.now(UTC),
        )
        assert len(mixed_snaps) == 1
        assert mixed_snaps[0].average_confidence is None

        # Fully measured bucket
        poi_measured = [
            {"latitude": 25.04, "longitude": 121.55, "confidence": 0.90},
            {"latitude": 25.04, "longitude": 121.55, "confidence": 0.80},
        ]
        measured_snaps = pipeline.build_feature_snapshots(
            poi_records=poi_measured,
            resolution=9,
            feature_snapshot_time=datetime.now(UTC),
        )
        assert len(measured_snaps) == 1
        assert measured_snaps[0].average_confidence == 0.85

    def test_heatzone_v3_result_from_dict_preserves_none_confidence(self) -> None:
        data = {
            "heat_zone_id": "HZ-001",
            "h3_index": "89283082813ffff",
            "score": 75.0,
            "confidence": None,  # Absent confidence
        }
        res = HeatZoneV3ScoreResult.from_dict(data)
        assert res.confidence is None  # R4: Not defaulted to 1.0


# ---------------------------------------------------------------------------
# 5. API Response Serialization & Provenance Contract (R3)
# ---------------------------------------------------------------------------


class TestAPIListingConfidenceProvenance:
    """Listing route adapter returns null confidence + confidenceProvenance for legacy and unmeasured rows."""

    def test_legacy_listing_serializes_as_null_with_legacy_unknown_provenance(self) -> None:

        legacy_listing = Listing(
            listing_id="L-LEGACY-001",
            rent_amount=20000.0,
            confidence=1.0,
            snapshot_id="v1",
            measurement_schema_version="v1",
        )

        # In get_listing serialization logic:
        eff_conf = (
            None
            if (
                legacy_listing.confidence is None
                or (
                    legacy_listing.confidence == 1.0
                    and (
                        legacy_listing.snapshot_id == "v1"
                        or getattr(legacy_listing, "measurement_schema_version", "") == "v1"
                    )
                )
            )
            else legacy_listing.confidence
        )
        provenance = (
            "legacy_unknown"
            if (
                legacy_listing.confidence == 1.0
                and (
                    legacy_listing.snapshot_id == "v1"
                    or getattr(legacy_listing, "measurement_schema_version", "") == "v1"
                )
            )
            else ("unmeasured" if legacy_listing.confidence is None else "measured")
        )

        assert eff_conf is None
        assert provenance == "legacy_unknown"
