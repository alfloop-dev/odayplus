"""CI tripwire for HeatZone composition migration (ODP-FR-HZ-006, ODP-SD-AMD-001 §5.2)."""

from __future__ import annotations

from pathlib import Path

MIGRATION_SQL = Path("infra/db/migrations/000018_heatzone_composition.sql")
ALEMBIC_REV = Path("infra/db/migrations/versions/0012_heatzone_composition.py")


def _sql() -> str:
    return MIGRATION_SQL.read_text(encoding="utf-8")


def _rev() -> str:
    return ALEMBIC_REV.read_text(encoding="utf-8")


def test_heatzone_composition_table_schema_and_constraints() -> None:
    sql = _sql()

    assert "CREATE TABLE IF NOT EXISTS expansion.heatzone_composition" in sql
    assert "composition_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4()" in sql
    assert "zone_id             VARCHAR(100) NOT NULL" in sql
    assert "tenant_id           UUID NOT NULL REFERENCES core.tenants(tenant_id)" in sql
    assert "member_cell_id      UUID NOT NULL REFERENCES geo.h3_cells(geo_cell_id)" in sql
    assert "composition_kind    VARCHAR(50) NOT NULL" in sql
    assert "parent_zone_id      VARCHAR(100)" in sql
    assert "decided_by          VARCHAR(255) NOT NULL" in sql
    assert "decision_policy_version_id VARCHAR(100) NOT NULL" in sql
    assert "override_reason     TEXT" in sql
    assert "reverted_at         TIMESTAMP WITH TIME ZONE" in sql

    # Constraints
    assert "CONSTRAINT chk_composition_kind CHECK" in sql
    assert "composition_kind IN ('MERGED', 'SPLIT_CHILD', 'ATOMIC')" in sql
    assert "CONSTRAINT chk_composition_parent CHECK" in sql
    assert "CONSTRAINT chk_composition_override_reason CHECK" in sql
    assert "CONSTRAINT chk_composition_revert_order CHECK" in sql
    assert "CONSTRAINT chk_composition_zone_id_format CHECK (zone_id ~ '^MZ-[0-9a-f]{16}$')" in sql
    assert "CONSTRAINT fk_heatzone_composition_decision_policy" in sql
    assert "REFERENCES workflow.decision_policies(policy_version_id, tenant_id)" in sql


def test_active_member_uniqueness_and_audit_index() -> None:
    sql = _sql()

    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_heatzone_composition_active_member" in sql
    assert "ON expansion.heatzone_composition (tenant_id, member_cell_id)" in sql
    assert "WHERE reverted_at IS NULL" in sql

    assert "CREATE INDEX IF NOT EXISTS idx_heatzone_composition_audit" in sql
    assert "ON expansion.heatzone_composition (tenant_id, zone_id, decided_at)" in sql


def test_append_only_trigger_and_forbidden_updates() -> None:
    sql = _sql()

    assert "CREATE OR REPLACE FUNCTION expansion.heatzone_composition_append_only()" in sql
    assert "DELETE is not permitted" in sql
    assert "is already reverted" in sql
    assert "the only permitted UPDATE is setting reverted_at" in sql
    assert "only reverted_at may change" in sql

    assert "CREATE TRIGGER trg_heatzone_composition_append_only" in sql
    assert "BEFORE UPDATE OR DELETE ON expansion.heatzone_composition" in sql


def test_heatzone_merge_policy_seeding_and_tenant_trigger() -> None:
    sql = _sql()

    assert "CREATE OR REPLACE FUNCTION workflow.seed_heatzone_merge_policy(p_tenant_id UUID)" in sql
    assert "INSERT INTO workflow.decision_policies" in sql
    assert "'heatzone-merge-v1:' || p_tenant_id::text" in sql
    assert "'heatzone_merge'" in sql
    assert '"min_observation_days": 180' in sql
    assert '"min_mature_labels": 200' in sql
    assert '"min_active_stores": 50' in sql
    assert '"min_adjacent_pairs": 30' in sql
    assert '"min_metro_clusters": 2' in sql
    assert '"max_absorption_cv": 0.15' in sql
    assert '"max_drift_psi": 0.10' in sql
    assert '"min_correlation_rho": 0.75' in sql
    assert '"max_disconnect_index": 0.20' in sql
    assert '"min_split_density_ratio": 2.5' in sql
    assert '"min_ndcg_gain": 0.05' in sql
    assert '"min_cannibalization_variance_reduction": 0.20' in sql

    assert "SELECT workflow.seed_heatzone_merge_policy(t.tenant_id) FROM core.tenants t;" in sql
    assert "CREATE TRIGGER trg_seed_heatzone_merge_policy" in sql
    assert "AFTER INSERT ON core.tenants" in sql


def test_alembic_revision_chain() -> None:
    rev = _rev()

    assert 'revision: str = "0012"' in rev
    assert 'down_revision: str = "0011"' in rev
    assert "000018_heatzone_composition.sql" in rev
