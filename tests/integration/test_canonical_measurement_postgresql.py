"""PostgreSQL forward migration and view execution for the measurement cutover.

ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001

Two claims can only be settled against a real PostgreSQL server:

1. Migration ``000026`` applied to tables already holding pre-cutover rows
   drops the substituting default without rewriting any stored ``1.00``, and
   adds the marker column those rows are disambiguated by.
2. ``geo_grid_view`` agrees with ``modules/external_data/geo/pipeline.py`` on a
   partially measured bucket. ``avg()`` skips NULL inputs, so the guarded
   aggregate has to be executed -- reading the SQL cannot show whether the
   guard actually fires.

The pre-cutover DDL below is the baseline shape (``000001``) of only the
columns these two claims touch; it deliberately omits postgis-dependent
columns, which is what lets this run against the bundled ``pgserver``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "infra/db/migrations/000026_canonical_measurement_nullable.sql"
GEO_GRID_VIEW = REPO_ROOT / "pipelines/dbt/models/model_ready/geo_grid_view.sql"

# Baseline (000001) shape of the six measured columns, before the cutover:
# every one of them substitutes 1.00 when the writer says nothing.
PRE_CUTOVER_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS geo;
CREATE SCHEMA IF NOT EXISTS expansion;
CREATE SCHEMA IF NOT EXISTS learning;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE core.address_locations (
    address_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_address TEXT NOT NULL,
    h3_res_9 VARCHAR(15)
);

CREATE TABLE geo.h3_cells (
    geo_cell_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    h3_index VARCHAR(15) NOT NULL UNIQUE,
    h3_resolution INTEGER NOT NULL DEFAULT 9,
    admin_city VARCHAR(100),
    admin_district VARCHAR(100)
);

CREATE TABLE geo.pois (
    poi_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_poi_id VARCHAR(255) NOT NULL,
    poi_name VARCHAR(255) NOT NULL,
    poi_category VARCHAR(100) NOT NULL,
    geo_cell_id UUID REFERENCES geo.h3_cells(geo_cell_id),
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    confidence NUMERIC(3, 2) DEFAULT 1.00,
    snapshot_id VARCHAR(100) NOT NULL
);

CREATE TABLE geo.competitor_stores (
    competitor_store_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_name VARCHAR(255) NOT NULL,
    store_name VARCHAR(255) NOT NULL,
    geo_cell_id UUID REFERENCES geo.h3_cells(geo_cell_id),
    estimated_capacity NUMERIC(5, 2) DEFAULT 0.00,
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    confidence NUMERIC(3, 2) DEFAULT 1.00
);

CREATE TABLE expansion.listings (
    listing_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_listing_id VARCHAR(255) NOT NULL,
    source_id VARCHAR(100) NOT NULL,
    listing_status VARCHAR(50) NOT NULL DEFAULT 'active',
    address_id UUID REFERENCES core.address_locations(address_id),
    rent_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    area_ping NUMERIC(8, 2) NOT NULL DEFAULT 0.00,
    snapshot_id VARCHAR(100) NOT NULL,
    confidence NUMERIC(3, 2) DEFAULT 1.00
);

CREATE TABLE expansion.heatzone_scores (
    heatzone_score_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    geo_cell_id UUID REFERENCES geo.h3_cells(geo_cell_id),
    confidence NUMERIC(3, 2) DEFAULT 1.00
);

CREATE TABLE learning.prediction_runs (
    prediction_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_version_id VARCHAR(100) NOT NULL
);

CREATE TABLE learning.predictions (
    prediction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prediction_run_id UUID REFERENCES learning.prediction_runs(prediction_run_id),
    entity_type VARCHAR(100) NOT NULL,
    entity_id VARCHAR(255) NOT NULL,
    confidence NUMERIC(3, 2) DEFAULT 1.00
);

CREATE TABLE audit.data_snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_type VARCHAR(50) NOT NULL DEFAULT 'raw',
    schema_version VARCHAR(50),
    row_count INTEGER NOT NULL DEFAULT 0,
    quality_score NUMERIC(3, 2) DEFAULT 1.00
);
"""

# Two POIs in the same cell, one of them never measured, plus a measured
# competitor. This is the bucket `avg()` silently averages down to 1.00.
PRE_CUTOVER_ROWS = """
INSERT INTO geo.h3_cells (geo_cell_id, h3_index, admin_city)
VALUES ('11111111-1111-1111-1111-111111111111', '8929a1d4d67ffff', 'Taipei');

INSERT INTO geo.pois (source_poi_id, poi_name, poi_category, geo_cell_id, snapshot_id)
VALUES ('SRC-POI-1', 'legacy school', 'school',
        '11111111-1111-1111-1111-111111111111', 'snap-1');

INSERT INTO geo.competitor_stores (brand_name, store_name, geo_cell_id)
VALUES ('rival', 'rival branch', '11111111-1111-1111-1111-111111111111');

INSERT INTO expansion.listings (source_listing_id, source_id, snapshot_id)
VALUES ('SRC-LST-1', 'provider-a', 'snap-1');

INSERT INTO expansion.heatzone_scores (geo_cell_id)
VALUES ('11111111-1111-1111-1111-111111111111');

INSERT INTO learning.prediction_runs (prediction_run_id, model_version_id)
VALUES ('22222222-2222-2222-2222-222222222222', 'sitescore-baseline-v1');

INSERT INTO learning.predictions (prediction_run_id, entity_type, entity_id)
VALUES ('22222222-2222-2222-2222-222222222222', 'candidate_site', 'CS-1');

INSERT INTO audit.data_snapshots (snapshot_type, schema_version)
VALUES ('model_ready', 'v1');
"""

MEASURED_COLUMNS = (
    ("geo", "pois", "confidence"),
    ("geo", "competitor_stores", "confidence"),
    ("expansion", "listings", "confidence"),
    ("expansion", "heatzone_scores", "confidence"),
    ("learning", "predictions", "confidence"),
    ("audit", "data_snapshots", "quality_score"),
)


def _render_geo_grid_view() -> str:
    """The dbt model with its Jinja vars bound, so PostgreSQL can execute it.

    Only ``var()`` calls appear in this model; binding them to a literal
    timestamp is exactly what ``dbt --vars`` does at compile time.
    """
    sql = GEO_GRID_VIEW.read_text(encoding="utf-8")
    rendered = re.sub(
        r"\{\{\s*var\([^)]*\)\s*\}\}", "TIMESTAMPTZ '2026-09-01T00:00:00Z'", sql
    )
    assert "{{" not in rendered, f"unbound dbt template left in model: {rendered}"
    return rendered


@pytest.fixture
def migrated_db(intake_blank_db):
    """A database holding pre-cutover rows with migration 000026 applied."""
    with intake_blank_db.connect() as connection:
        connection.execute(PRE_CUTOVER_SCHEMA)
        connection.execute(PRE_CUTOVER_ROWS)
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
    return intake_blank_db


class TestForwardMigrationOnPreCutoverRows:
    def test_every_measured_column_becomes_nullable_without_a_default(
        self, migrated_db
    ) -> None:
        with migrated_db.connect() as connection:
            rows = connection.execute(
                "SELECT table_schema, table_name, column_name, is_nullable, "
                "       column_default "
                "FROM information_schema.columns "
                "WHERE (table_schema, table_name, column_name) IN "
                "      (('geo','pois','confidence'), "
                "       ('geo','competitor_stores','confidence'), "
                "       ('expansion','listings','confidence'), "
                "       ('expansion','heatzone_scores','confidence'), "
                "       ('learning','predictions','confidence'), "
                "       ('audit','data_snapshots','quality_score'))"
            ).fetchall()

        found = {(r[0], r[1], r[2]): (r[3], r[4]) for r in rows}
        assert set(found) == set(MEASURED_COLUMNS)
        for key, (is_nullable, default) in found.items():
            assert is_nullable == "YES", f"{key} still refuses NULL"
            assert default is None, f"{key} still substitutes {default}"

    def test_stored_rows_are_not_bulk_rewritten_to_null(self, migrated_db) -> None:
        """The lineage decision forbids collapsing stored 1.00 into NULL.

        A stored 1.00 may be a genuine perfect measurement; the migration only
        changes the constraint, and the marker column is what lets the
        application tell the two apart afterwards.
        """
        with migrated_db.connect() as connection:
            for schema, table, column in MEASURED_COLUMNS:
                stored = connection.execute(
                    f"SELECT {column} FROM {schema}.{table}"  # nosec B608
                ).fetchone()
                assert stored[0] is not None, f"{schema}.{table}.{column} was nulled"
                assert float(stored[0]) == 1.0

    @pytest.mark.parametrize(
        ("schema", "table"),
        [
            ("geo", "pois"),
            ("geo", "competitor_stores"),
            ("expansion", "listings"),
            ("expansion", "heatzone_scores"),
            ("audit", "data_snapshots"),
        ],
    )
    def test_pre_cutover_rows_are_marked_v1(self, migrated_db, schema, table) -> None:
        with migrated_db.connect() as connection:
            marker = connection.execute(
                f"SELECT measurement_schema_version FROM {schema}.{table}"  # nosec B608
            ).fetchone()
        assert marker[0] == "v1"

    def test_a_post_cutover_writer_can_express_absence(self, migrated_db) -> None:
        with migrated_db.connect() as connection:
            connection.execute(
                "INSERT INTO geo.pois "
                "(source_poi_id, poi_name, poi_category, geo_cell_id, snapshot_id, "
                " confidence, measurement_schema_version) "
                "VALUES ('SRC-POI-2', 'unmeasured shop', 'retail', "
                "        '11111111-1111-1111-1111-111111111111', 'snap-2', NULL, 'v2')"
            )
            stored = connection.execute(
                "SELECT confidence, measurement_schema_version FROM geo.pois "
                "WHERE source_poi_id = 'SRC-POI-2'"
            ).fetchone()

        assert stored[0] is None
        assert stored[1] == "v2"

    def test_the_migration_is_replayable(self, migrated_db) -> None:
        """Re-running the forward migration must not fail or change stored values."""
        with migrated_db.connect() as connection:
            connection.execute(MIGRATION.read_text(encoding="utf-8"))
            stored = connection.execute(
                "SELECT confidence, measurement_schema_version FROM geo.pois"
            ).fetchone()

        assert float(stored[0]) == 1.0
        assert stored[1] == "v1"


class TestGeoGridViewAgreesWithThePythonPipeline:
    """``avg()`` skips NULLs; the guarded aggregate has to actually fire."""

    def _confidence(self, database) -> object:
        with database.connect() as connection:
            row = connection.execute(
                f"WITH view_rows AS ({_render_geo_grid_view()}) "  # nosec B608
                "SELECT confidence FROM view_rows"
            ).fetchone()
        return row[0]

    def test_a_partially_measured_poi_bucket_publishes_no_confidence(
        self, migrated_db
    ) -> None:
        """One measured POI plus one unmeasured POI must not average to 1.00.

        ``GeoPipeline`` returns ``None`` for exactly this bucket. Before the
        guard, PostgreSQL dropped the NULL row, averaged the remaining 1.00,
        and published a fully confident cell.
        """
        with migrated_db.connect() as connection:
            connection.execute(
                "INSERT INTO geo.pois "
                "(source_poi_id, poi_name, poi_category, geo_cell_id, snapshot_id, "
                " confidence, measurement_schema_version) "
                "VALUES ('SRC-POI-2', 'unmeasured shop', 'retail', "
                "        '11111111-1111-1111-1111-111111111111', 'snap-2', NULL, 'v2')"
            )

        assert self._confidence(migrated_db) is None

    def test_a_partially_measured_competitor_bucket_publishes_no_confidence(
        self, migrated_db
    ) -> None:
        with migrated_db.connect() as connection:
            connection.execute(
                "INSERT INTO geo.competitor_stores "
                "(brand_name, store_name, geo_cell_id, confidence, "
                " measurement_schema_version) "
                "VALUES ('rival', 'unmeasured branch', "
                "        '11111111-1111-1111-1111-111111111111', NULL, 'v2')"
            )

        assert self._confidence(migrated_db) is None

    def test_a_fully_measured_bucket_still_publishes_a_confidence(
        self, migrated_db
    ) -> None:
        assert float(self._confidence(migrated_db)) == 1.0

    def test_a_cell_with_no_pois_at_all_publishes_no_confidence(
        self, migrated_db
    ) -> None:
        with migrated_db.connect() as connection:
            connection.execute(
                "INSERT INTO geo.h3_cells (geo_cell_id, h3_index, admin_city) "
                "VALUES ('33333333-3333-3333-3333-333333333333', "
                "        '8929a1d4d6bffff', 'Taipei')"
            )
            row = connection.execute(
                f"WITH view_rows AS ({_render_geo_grid_view()}) "  # nosec B608
                "SELECT confidence FROM view_rows WHERE h3_index = '8929a1d4d6bffff'"
            ).fetchone()

        assert row[0] is None
