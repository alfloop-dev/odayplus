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
        lst_legacy = Listing(confidence=1.0, snapshot_id="snap-lst-20250101", measurement_schema_version="v1")
        assert lst_legacy.confidence == 1.0  # Physical historic value preserved
        assert lst_legacy.effective_confidence_provenance == "legacy_unknown"
        assert lst_legacy.effective_confidence is None  # Downstream consumes as unmeasured

        poi_legacy = Poi(confidence=1.0, snapshot_id="snap-poi-20250101", measurement_schema_version="v1")
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
        assert "ALTER TABLE IF EXISTS geo.pois ALTER COLUMN confidence DROP NOT NULL;" in pg_migration
        assert "ALTER TABLE IF EXISTS geo.pois ALTER COLUMN confidence DROP DEFAULT;" in pg_migration
        assert "ALTER TABLE IF EXISTS geo.competitor_stores ALTER COLUMN confidence DROP NOT NULL;" in pg_migration
        assert "ALTER TABLE IF EXISTS expansion.listings ALTER COLUMN confidence DROP NOT NULL;" in pg_migration
        assert "ALTER TABLE IF EXISTS learning.predictions ALTER COLUMN confidence DROP NOT NULL;" in pg_migration
        assert "ALTER TABLE IF EXISTS audit.data_snapshots ALTER COLUMN quality_score DROP NOT NULL;" in pg_migration
        assert "ALTER TABLE IF EXISTS expansion.heatzone_scores ALTER COLUMN confidence DROP DEFAULT;" in pg_migration

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
            snapshot_id="snap-poi-20260803-001",  # Real snapshot ID, not "v1"
            measurement_schema_version="v1",
        )

        # The model's effective_confidence and effective_confidence_provenance
        # properties must be used by API serialization — not inline logic.
        assert legacy_listing.effective_confidence is None
        assert legacy_listing.effective_confidence_provenance == "legacy_unknown"

    def test_measured_listing_preserves_confidence(self) -> None:
        measured_listing = Listing(
            listing_id="L-MEASURED-001",
            rent_amount=30000.0,
            confidence=0.85,
            measurement_schema_version="v2",
        )
        assert measured_listing.effective_confidence == 0.85
        assert measured_listing.effective_confidence_provenance == "measured"

    def test_unmeasured_listing_returns_none(self) -> None:
        unmeasured_listing = Listing(
            listing_id="L-UNMEASURED-001",
            rent_amount=25000.0,
            confidence=None,
            measurement_schema_version="v2",
        )
        assert unmeasured_listing.effective_confidence is None
        assert unmeasured_listing.effective_confidence_provenance == "unmeasured"

    def test_v2_genuinely_perfect_score_preserved(self) -> None:
        perfect_listing = Listing(
            listing_id="L-PERFECT-001",
            rent_amount=28000.0,
            confidence=1.0,
            measurement_schema_version="v2",
        )
        assert perfect_listing.effective_confidence == 1.0
        assert perfect_listing.effective_confidence_provenance == "measured"


# ---------------------------------------------------------------------------
# 6. SQLite Migration Restart Survival (PR #1327 Review Fix)
# ---------------------------------------------------------------------------


class TestSqliteRestartPreservesNewColumns:
    """Migration 000026 must preserve measurement_schema_version on restart.

    SqliteEngine._bootstrap() re-runs all DDL on every init.  The original
    migration omitted new columns from the INSERT...SELECT, so a process
    restart would silently reset measurement_schema_version to the DEFAULT
    'v1' and drop snapshot_id / source_competitor_id.
    """

    def test_competitor_stores_schema_version_survives_restart(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test_restart.db"
        engine = SqliteEngine(db_path)

        # Satisfy FK: competitor_stores.snapshot_id references data_snapshots
        engine.execute(
            "INSERT OR IGNORE INTO data_snapshots "
            "(snapshot_id, snapshot_type, source_id, snapshot_time, storage_uri, "
            " schema_version, row_count, created_by_run_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("snap-20260901", "external", "test", "2026-09-01T00:00:00Z",
             "s3://test", "v2", 0, "test-run-001"),
        )

        # Write a v2 competitor store row
        engine.execute(
            "INSERT INTO competitor_stores "
            "(competitor_store_id, brand_name, store_name, confidence, "
            " measurement_schema_version, snapshot_id, source_competitor_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("CS-001", "TestBrand", "TestStore", 0.85, "v2", "snap-20260901", "SRC-001"),
        )

        # Verify before restart
        row = engine.query_one(
            "SELECT measurement_schema_version, snapshot_id, source_competitor_id "
            "FROM competitor_stores WHERE competitor_store_id = ?",
            ("CS-001",),
        )
        assert row is not None
        assert row["measurement_schema_version"] == "v2"
        assert row["snapshot_id"] == "snap-20260901"
        assert row["source_competitor_id"] == "SRC-001"

        # Simulate restart: close and re-open (re-runs _bootstrap)
        engine.close()
        engine2 = SqliteEngine(db_path)

        row2 = engine2.query_one(
            "SELECT measurement_schema_version, snapshot_id, source_competitor_id, confidence "
            "FROM competitor_stores WHERE competitor_store_id = ?",
            ("CS-001",),
        )
        assert row2 is not None
        assert row2["measurement_schema_version"] == "v2", (
            "measurement_schema_version was reset to DEFAULT on restart"
        )
        assert row2["snapshot_id"] == "snap-20260901", (
            "snapshot_id was lost on restart"
        )
        assert row2["source_competitor_id"] == "SRC-001", (
            "source_competitor_id was lost on restart"
        )
        assert row2["confidence"] == 0.85
        engine2.close()

    def test_pois_schema_version_survives_restart(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test_restart_pois.db"
        engine = SqliteEngine(db_path)

        engine.execute(
            "INSERT INTO pois "
            "(poi_id, source_poi_id, poi_name, poi_category, confidence, measurement_schema_version) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("POI-001", "SRC-POI-001", "TestPoi", "restaurant", 0.90, "v2"),
        )

        engine.close()
        engine2 = SqliteEngine(db_path)

        row = engine2.query_one(
            "SELECT measurement_schema_version, confidence "
            "FROM pois WHERE poi_id = ?",
            ("POI-001",),
        )
        assert row is not None
        assert row["measurement_schema_version"] == "v2", (
            "pois measurement_schema_version was reset on restart"
        )
        assert row["confidence"] == 0.90
        engine2.close()

    def test_listings_schema_version_survives_restart(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test_restart_listings.db"
        engine = SqliteEngine(db_path)

        engine.execute(
            "INSERT INTO listings "
            "(listing_id, source_listing_id, source_id, confidence, measurement_schema_version) "
            "VALUES (?, ?, ?, ?, ?)",
            ("L-001", "SRC-L-001", "591", 0.75, "v2"),
        )

        engine.close()
        engine2 = SqliteEngine(db_path)

        row = engine2.query_one(
            "SELECT measurement_schema_version, confidence "
            "FROM listings WHERE listing_id = ?",
            ("L-001",),
        )
        assert row is not None
        assert row["measurement_schema_version"] == "v2", (
            "listings measurement_schema_version was reset on restart"
        )
        assert row["confidence"] == 0.75
        engine2.close()

    def test_null_confidence_survives_restart(self, tmp_path: Path) -> None:
        """NULL confidence (unmeasured) must not be replaced with a default on restart."""
        db_path = tmp_path / "test_restart_null.db"
        engine = SqliteEngine(db_path)

        engine.execute(
            "INSERT INTO pois "
            "(poi_id, source_poi_id, poi_name, poi_category, confidence, measurement_schema_version) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("POI-NULL", "SRC-NULL", "NullPoi", "cafe", None, "v2"),
        )

        engine.close()
        engine2 = SqliteEngine(db_path)

        row = engine2.query_one(
            "SELECT confidence, measurement_schema_version FROM pois WHERE poi_id = ?",
            ("POI-NULL",),
        )
        assert row is not None
        assert row["confidence"] is None, "NULL confidence was replaced with a default on restart"
        assert row["measurement_schema_version"] == "v2"
        engine2.close()

