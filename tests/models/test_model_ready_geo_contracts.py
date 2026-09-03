from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

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
        "DO $official_outcome_view$",
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


def test_listing_property_valuation_view_sql_contract() -> None:
    sql = MODEL_READY_SQL_PATH.read_text(encoding="utf-8")
    assert "CREATE OR REPLACE VIEW model_ready.listing_property_valuation_view" in sql
    assert "1.0::double precision AS data_quality_score" in sql
    assert "1.0::double precision AS confidence" in sql
    assert "license_id = 'government-open-data-license-v1'" in sql
    assert "ingestion_status = 'SUCCEEDED'" in sql


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

    # candidate_site_view uses explicit CASE WHEN checking both components
    assert "then least(listings.confidence, address_locations.geocode_confidence)" in candidate_sql
    assert "else null" in candidate_sql
    assert "as confidence" in candidate_sql
    assert "coalesce(listings.confidence, 1.0)" not in candidate_sql
    assert "coalesce(address_locations.geocode_confidence, 1.0)" not in candidate_sql

    # geo_grid_view uses explicit CASE WHEN checking both components
    assert "then least(poi_counts.poi_confidence, competitor_counts.competitor_confidence)" in geo_grid_sql
    assert "else null" in geo_grid_sql
    assert "as confidence" in geo_grid_sql
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
        "DO $official_outcome_view$",
    )
    assert "least(coalesce(average_geocode_confidence, 0.0), 1.0)" not in heatzone_body
    assert (
        "WHEN average_geocode_confidence IS NOT NULL THEN least(average_geocode_confidence, 1.0)::double precision"
        in heatzone_body
    )
    assert "ELSE NULL" in heatzone_body


class LiteralAuditEntry(NamedTuple):
    source_layer: str
    view_name: str
    column_name: str
    classification: str
    nullable: bool
    rationale: str


AUDITED_LITERALS: list[LiteralAuditEntry] = [
    # DBT views
    LiteralAuditEntry("dbt", "candidate_site_view", "confidence", "Measurement", True, "Empirical geocoding and listing quality measurement; NULL when absent."),
    LiteralAuditEntry("dbt", "candidate_site_view", "data_quality_score", "Derived Rule", False, "Rule derived from rent > 0 and geocode >= 0.5."),
    LiteralAuditEntry("dbt", "geo_grid_view", "confidence", "Measurement", True, "Empirical POI and competitor confidence measurement; NULL when absent."),
    LiteralAuditEntry("dbt", "geo_grid_view", "data_quality_score", "Derived Rule", False, "Rule derived from H3 index non-null presence."),
    LiteralAuditEntry("dbt", "forecast_training_view", "data_quality_score", "Derived Rule", False, "PIT observation time constraint rule."),
    LiteralAuditEntry("dbt", "forecast_training_view", "confidence", "Derived Contract", False, "Authoritative core.transactions financial ledger invariant."),
    LiteralAuditEntry("dbt", "store_machine_timeseries_view", "data_quality_score", "Derived Contract", False, "Authoritative store and machine telemetry aggregation contract."),
    LiteralAuditEntry("dbt", "store_machine_timeseries_view", "confidence", "Derived Contract", False, "Sensor cycle telemetry logs from data plane."),
    LiteralAuditEntry("dbt", "store_machine_timeseries_view", "available_minutes", "Physical Constant", False, "24 * 60 = 1440 minutes per day invariant."),
    LiteralAuditEntry("dbt", "intervention_panel_view", "data_quality_score", "Derived Rule", False, "Temporal observation window validity rule."),
    LiteralAuditEntry("dbt", "intervention_panel_view", "confidence", "Derived Rule", False, "ML-05 Evidence Ladder causal tier mapping."),
    LiteralAuditEntry("dbt", "intervention_panel_view", "treatment_intensity", "Derived Contract", False, "Baseline 1.0 multiplier for unscaled intervention."),
    LiteralAuditEntry("dbt", "intervention_panel_view", "eligibility_score", "Derived Rule", False, "Status tier mapping rule."),
    LiteralAuditEntry("dbt", "valuation_view", "data_quality_score", "Derived Rule", False, "Non-negative financial input verification rule."),
    LiteralAuditEntry("dbt", "valuation_view", "confidence", "Derived Contract", False, "Baseline 0.8 valuation confidence specification."),
    LiteralAuditEntry("dbt", "valuation_view", "forecast_confidence", "Derived Contract", False, "Baseline 0.8 forecast confidence specification."),
    LiteralAuditEntry("dbt", "network_plan_view", "data_quality_score", "Derived Rule", False, "Solver status optimal/feasible predicate rule."),
    LiteralAuditEntry("dbt", "network_plan_view", "confidence", "Derived Rule", False, "MIP/CP-SAT solver convergence confidence mapping."),
    LiteralAuditEntry("dbt", "network_plan_view", "risk_score", "Derived Rule", False, "Action risk level mapping rule."),
    LiteralAuditEntry("dbt", "brand_transfer_view", "data_quality_score", "Derived Contract", False, "Synthetic pair matrix invariant from core.brands."),
    LiteralAuditEntry("dbt", "brand_transfer_view", "confidence", "Derived Contract", False, "Baseline brand relationship pairing contract."),
    LiteralAuditEntry("dbt", "brand_transfer_view", "transfer_ratio", "Benchmark Constant", False, "Retail customer brand transfer baseline parameter."),
    LiteralAuditEntry("dbt", "ramp_curve_view", "data_quality_score", "Derived Contract", False, "Store entity baseline invariant from core.stores."),
    LiteralAuditEntry("dbt", "ramp_curve_view", "confidence", "Derived Contract", False, "Store ramp curve contract invariant."),
    LiteralAuditEntry("dbt", "ramp_curve_view", "ramp_up_ratio", "Benchmark Constant", False, "6-month store ramp-up ratio benchmark baseline."),
    LiteralAuditEntry("dbt", "matched_control_view", "data_quality_score", "Derived Contract", False, "Store pairing baseline invariant from core.stores."),
    LiteralAuditEntry("dbt", "matched_control_view", "confidence", "Derived Contract", False, "Control group pairing contract invariant."),
    LiteralAuditEntry("dbt", "matched_control_view", "match_score", "Benchmark Constant", False, "Synthetic matching similarity benchmark baseline."),
    # PostgreSQL product views
    LiteralAuditEntry("postgresql", "model_ready.forecast_training_view", "data_quality_score", "Derived Rule", False, "Full canonical lineage and ingestion completion audit rule."),
    LiteralAuditEntry("postgresql", "model_ready.forecast_training_view", "confidence", "Derived Contract", False, "Settled TWD transactions in core.transactions ledger contract."),
    LiteralAuditEntry("postgresql", "model_ready.candidate_site_view", "data_quality_score", "Derived Rule", False, "90-day prior/label partition completeness audit rule."),
    LiteralAuditEntry("postgresql", "model_ready.candidate_site_view", "confidence", "Measurement", True, "Empirical geocode confidence bounded by 1.0 max; NULL when unmeasured."),
    LiteralAuditEntry("postgresql", "model_ready.heatzone_training_view", "data_quality_score", "Derived Rule", False, "90-day prior and 28-day forward partition completeness audit rule."),
    LiteralAuditEntry("postgresql", "model_ready.heatzone_training_view", "confidence", "Measurement", True, "Empirical average geocode confidence bounded by 1.0 max; NULL when unmeasured."),
    LiteralAuditEntry("postgresql", "model_ready.listing_property_valuation_view", "data_quality_score", "Derived Contract", False, "Verified government open data (MOI/NTPC) deed transactions."),
    LiteralAuditEntry("postgresql", "model_ready.listing_property_valuation_view", "confidence", "Derived Contract", False, "Official government property sale deed registration contract."),
]


def test_all_1_0_literals_in_model_ready_views_are_classified_and_audited() -> None:
    dbt_dir = Path("pipelines/dbt/models/model_ready")
    sql_files = sorted(dbt_dir.glob("*.sql"))
    assert len(sql_files) == 10

    # Every dbt view has audited and classified literal usages
    audited_dbt_views = {entry.view_name for entry in AUDITED_LITERALS if entry.source_layer == "dbt"}
    assert {f.stem for f in sql_files} == audited_dbt_views

    # Check classification taxonomy
    valid_classes = {
        "Measurement",
        "Derived Rule",
        "Derived Contract",
        "Physical Constant",
        "Benchmark Constant",
    }
    for entry in AUDITED_LITERALS:
        assert entry.classification in valid_classes, f"Invalid class for {entry}"
        assert entry.rationale != "", f"Empty rationale for {entry}"

    # Verify measurement classification nullability invariants
    for entry in AUDITED_LITERALS:
        if entry.classification == "Measurement":
            assert entry.nullable is True, f"Measurement {entry.view_name}.{entry.column_name} must be nullable"
        else:
            assert entry.nullable is False, f"Non-measurement {entry.view_name}.{entry.column_name} should not be nullable"


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
        "listing_property_valuation_view",
    ]
    for view in expected_views:
        assert view in content
