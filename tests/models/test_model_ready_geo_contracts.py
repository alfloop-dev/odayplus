from __future__ import annotations

from pathlib import Path

import yaml

from product_ops.modeling.contracts import MODEL_SPECS
from product_ops.modeling.install_views import MODEL_READY_SQL_PATH


def _view_body(sql: str, relation: str, next_marker: str) -> str:
    start = sql.index(f"CREATE OR REPLACE VIEW {relation}")
    end = sql.index(next_marker, start)
    return sql[start:end]


def test_sitescore_sql_has_fixed_mature_horizon_and_strict_prior_features() -> None:
    sql = MODEL_READY_SQL_PATH.read_text(encoding="utf-8")
    body = _view_body(
        sql,
        "model_ready.candidate_site_view",
        "CREATE OR REPLACE VIEW model_ready.heatzone_training_view",
    )

    assert "90::integer AS label_horizon_days" in body
    assert "AS realized_90d_net_revenue" in body
    assert "source_txn.event_time >= anchor.feature_cutoff_time" in body
    assert "anchor.feature_cutoff_time + interval '90 days'" in body
    assert "source_txn.event_time < anchor.feature_cutoff_time" in body
    assert "source_txn.store_id <> anchor.store_id" in body
    assert "source_txn.tenant_id = anchor.tenant_id" in body
    assert "source_txn.h3_index = anchor.h3_index" in body
    assert "count(DISTINCT lineage.canonical_table) = 2" in body
    assert "FROM data_plane.place_geography AS place" in body
    assert "place.valid_from <= txn.event_time" in body
    assert "place.valid_from <=" in body
    assert "INNER JOIN data_plane.transaction_authority AS authority" in body
    assert "authority.source_kind = 'orders'" in body
    assert "ingestion.reconciled" in body
    assert "ingestion.partition_complete" in body
    assert "ingestion.quarantined_count = 0" in body
    assert "ingestion.processed_count = ingestion.valid_loaded" in body
    assert "prior_covered_days = 90" in body
    assert "label_covered_days = 90" in body
    assert "identity_available_at < feature_cutoff_time" in body
    assert "prior_available_at < feature_cutoff_time" in body
    assert "prior_run_available_at < feature_cutoff_time" in body
    assert "feature_cutoff_time AS prediction_origin_time" in body
    assert "label_maturity_time AS feature_snapshot_time" not in body
    assert "label_maturity_time + interval '1 microsecond'" not in body
    assert "label_maturity_time <= CURRENT_TIMESTAMP" in body
    assert "count(*) = 0" in body
    assert "coalesce(sum(source_txn.net_amount), 0)" in body
    assert "label_transaction_count > 0" not in body
    assert "REALIZED_REVENUE_LABEL_MISSING" not in body
    assert "MATURE_CANDIDATE_SITE_OUTCOME_RELATION_MISSING" not in sql


def test_heatzone_sql_has_forward_cell_label_and_no_feature_leakage() -> None:
    sql = MODEL_READY_SQL_PATH.read_text(encoding="utf-8")
    body = _view_body(
        sql,
        "model_ready.heatzone_training_view",
        "INSERT INTO model_ready.view_contracts",
    )

    assert "28::integer AS label_horizon_days" in body
    assert "AS realized_28d_cell_net_revenue" in body
    assert "source_txn.event_time >= origin.feature_cutoff_time" in body
    assert "origin.feature_cutoff_time + interval '28 days'" in body
    assert "source_txn.event_time < origin.feature_cutoff_time" in body
    assert "source_txn.tenant_id = origin.tenant_id" in body
    assert "source_txn.h3_index = origin.h3_index" in body
    assert "count(DISTINCT lineage.canonical_table) = 2" in body
    assert "FROM data_plane.place_geography AS place" in body
    assert "place.valid_from <= txn.event_time" in body
    assert "INNER JOIN data_plane.transaction_authority AS authority" in body
    assert "authority.source_kind = 'orders'" in body
    assert "ingestion.reconciled" in body
    assert "ingestion.partition_complete" in body
    assert "prior_covered_days = 90" in body
    assert "label_covered_days = 28" in body
    assert "identity_available_at < feature_cutoff_time" in body
    assert "prior_available_at < feature_cutoff_time" in body
    assert "prior_run_available_at < feature_cutoff_time" in body
    assert "feature_cutoff_time AS prediction_origin_time" in body
    assert "label_maturity_time AS feature_snapshot_time" not in body
    assert "label_maturity_time + interval '1 microsecond'" not in body
    assert "label_maturity_time <= CURRENT_TIMESTAMP" in body
    assert "count(*) = 0" in body
    assert "coalesce(sum(source_txn.net_amount), 0)" in body
    assert "label_transaction_count > 0" not in body
    assert "REALIZED_CELL_REVENUE_LABEL_MISSING" not in body
    assert "POINT_IN_TIME_GEO_OUTCOME_RELATION_MISSING" not in sql


def test_supported_geo_contracts_are_active_only_after_views_are_declared() -> None:
    sql = MODEL_READY_SQL_PATH.read_text(encoding="utf-8")
    registry_offset = sql.index("INSERT INTO model_ready.view_contracts")

    assert sql.index("CREATE OR REPLACE VIEW model_ready.candidate_site_view") < registry_offset
    assert sql.index("CREATE OR REPLACE VIEW model_ready.heatzone_training_view") < registry_offset
    for relation, version in (
        ("model_ready.candidate_site_view", "candidate-site-view-v2"),
        ("model_ready.heatzone_training_view", "heatzone-training-view-v2"),
    ):
        registry_entry = sql[registry_offset:]
        assert f"'{relation}'" in registry_entry
        assert f"'{version}'" in registry_entry
        assert "'ACTIVE'" in registry_entry
        assert "TRUE" in registry_entry


def test_geo_model_specs_match_the_sql_contract_versions_and_labels() -> None:
    sitescore = MODEL_SPECS["sitescore"]
    heatzone = MODEL_SPECS["heatzone"]

    assert sitescore.relation == "model_ready.candidate_site_view"
    assert sitescore.expected_view_version == "candidate-site-view-v2"
    assert sitescore.label_column == "realized_90d_net_revenue"
    assert sitescore.temporal_column == "opened_on"
    assert sitescore.minimum_rows == 200

    assert heatzone.relation == "model_ready.heatzone_training_view"
    assert heatzone.expected_view_version == "heatzone-training-view-v2"
    assert heatzone.label_column == "realized_28d_cell_net_revenue"
    assert heatzone.temporal_column == "origin_date"
    assert heatzone.minimum_rows == 200


def test_dbt_views_do_not_coalesce_confidence_to_fake_perfect() -> None:
    dbt_dir = Path("pipelines/dbt/models/model_ready")
    candidate_sql = (dbt_dir / "candidate_site_view.sql").read_text(encoding="utf-8")
    geo_grid_sql = (dbt_dir / "geo_grid_view.sql").read_text(encoding="utf-8")

    assert (
        "least(listings.confidence, address_locations.geocode_confidence) as confidence"
        in candidate_sql
    )
    assert "coalesce(listings.confidence, 1.0)" not in candidate_sql
    assert "coalesce(address_locations.geocode_confidence, 1.0)" not in candidate_sql

    assert (
        "least(poi_counts.poi_confidence, competitor_counts.competitor_confidence) as confidence"
        in geo_grid_sql
    )
    assert "coalesce(poi_counts.poi_confidence, 1.0)" not in geo_grid_sql
    assert "coalesce(competitor_counts.competitor_confidence, 1.0)" not in geo_grid_sql


def test_postgresql_model_ready_views_propagate_null_confidence() -> None:
    sql = MODEL_READY_SQL_PATH.read_text(encoding="utf-8")

    candidate_body = _view_body(
        sql,
        "model_ready.candidate_site_view",
        "CREATE OR REPLACE VIEW model_ready.heatzone_training_view",
    )
    assert "least(coalesce(geocode_confidence, 0.0), 1.0)" not in candidate_body
    assert (
        "WHEN geocode_confidence IS NOT NULL THEN least(geocode_confidence, 1.0)::double precision"
        in candidate_body
    )
    assert "ELSE NULL" in candidate_body

    heatzone_body = _view_body(
        sql,
        "model_ready.heatzone_training_view",
        "INSERT INTO model_ready.view_contracts",
    )
    assert "least(coalesce(average_geocode_confidence, 0.0), 1.0)" not in heatzone_body
    assert (
        "WHEN average_geocode_confidence IS NOT NULL THEN least(average_geocode_confidence, 1.0)::double precision"
        in heatzone_body
    )
    assert "ELSE NULL" in heatzone_body


def test_all_1_0_literals_in_model_ready_views_are_classified_and_audited() -> None:
    dbt_dir = Path("pipelines/dbt/models/model_ready")
    sql_files = sorted(dbt_dir.glob("*.sql"))
    assert len(sql_files) == 10

    # Every view has audited and classified literal usages
    audited_views = {
        "candidate_site_view",
        "geo_grid_view",
        "forecast_training_view",
        "store_machine_timeseries_view",
        "intervention_panel_view",
        "valuation_view",
        "network_plan_view",
        "brand_transfer_view",
        "ramp_curve_view",
        "matched_control_view",
    }
    assert {f.stem for f in sql_files} == audited_views


def test_dbt_schema_yaml_defines_all_models_and_nullable_confidence() -> None:
    schema_file = Path("pipelines/dbt/models/model_ready/schema.yml")
    assert schema_file.exists()
    content = yaml.safe_load(schema_file.read_text(encoding="utf-8"))

    models = {m["name"]: m for m in content["models"]}
    assert len(models) == 10

    # candidate_site_view confidence must be nullable (no not_null test)
    candidate_cols = {c["name"]: c for c in models["candidate_site_view"]["columns"]}
    assert "confidence" in candidate_cols
    assert "tests" not in candidate_cols["confidence"] or "not_null" not in candidate_cols[
        "confidence"
    ].get("tests", [])

    # geo_grid_view confidence must be nullable (no not_null test)
    geo_cols = {c["name"]: c for c in models["geo_grid_view"]["columns"]}
    assert "confidence" in geo_cols
    assert "tests" not in geo_cols["confidence"] or "not_null" not in geo_cols["confidence"].get(
        "tests", []
    )


def test_model_ready_field_lineage_doc_exists_and_covers_all_views() -> None:
    lineage_doc = Path("docs/data/MODEL_READY_FIELD_LINEAGE.md")
    assert lineage_doc.exists()
    content = lineage_doc.read_text(encoding="utf-8")

    expected_views = [
        "candidate_site_view",
        "geo_grid_view",
        "forecast_training_view",
        "store_machine_timeseries_view",
        "intervention_panel_view",
        "valuation_view",
        "network_plan_view",
        "brand_transfer_view",
        "ramp_curve_view",
        "matched_control_view",
    ]
    for view in expected_views:
        assert view in content
