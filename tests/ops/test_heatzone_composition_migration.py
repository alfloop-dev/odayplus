"""CI tripwire for HeatZone composition migration (ODP-FR-HZ-006, ODP-SD-AMD-001 §5.2)."""

from __future__ import annotations

import re
from pathlib import Path

MIGRATION_SQL = Path("infra/db/migrations/000020_heatzone_composition.sql")
ALEMBIC_REV = Path("infra/db/migrations/versions/0015_heatzone_composition.py")


def _sql() -> str:
    return MIGRATION_SQL.read_text(encoding="utf-8")


def _rev() -> str:
    return ALEMBIC_REV.read_text(encoding="utf-8")


def test_heatzone_composition_table_schema_and_constraints() -> None:
    sql = _sql()

    assert "CREATE TABLE IF NOT EXISTS expansion.heatzone_composition" in sql
    assert "composition_id      UUID PRIMARY KEY DEFAULT gen_random_uuid()" in sql
    assert "zone_id             VARCHAR(100) NOT NULL" in sql
    assert "tenant_id           UUID NOT NULL REFERENCES core.tenants(tenant_id)" in sql
    assert "member_cell_id      UUID NOT NULL REFERENCES geo.h3_cells(geo_cell_id)" in sql
    assert "composition_kind    VARCHAR(50) NOT NULL" in sql
    assert "parent_zone_id      VARCHAR(100)" in sql
    assert "decided_by          VARCHAR(255) NOT NULL" in sql
    assert "decision_policy_version_id VARCHAR(100) NOT NULL" in sql
    assert "model_version       VARCHAR(100) NOT NULL DEFAULT 'heatzone-composition-v1'" in sql
    assert "override_reason     TEXT" in sql
    assert "reverted_at         TIMESTAMP WITH TIME ZONE" in sql

    # Proposals table
    assert "CREATE TABLE IF NOT EXISTS expansion.heatzone_proposals" in sql
    assert "proposal_id                     UUID PRIMARY KEY DEFAULT gen_random_uuid()" in sql
    assert "CONSTRAINT chk_proposal_status CHECK" in sql

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

    # 0014 is dev's learninghub backtest receipts migration; this one follows it, so
    # the chain stays linear and alembic keeps a single head.
    assert 'revision: str = "0015"' in rev
    assert 'down_revision: str = "0014"' in rev
    assert "000020_heatzone_composition.sql" in rev


def test_migration_revisions_have_one_head() -> None:
    """Two migrations claiming one revision id is a duplicate, not a branch."""
    versions = Path("infra/db/migrations/versions")
    revisions: dict[str, str] = {}
    down_revisions: dict[str, str | None] = {}
    for module in sorted(versions.glob("[0-9]*.py")):
        text = module.read_text(encoding="utf-8")
        revision = re.search(r"^revision: str = ['\"](\w+)['\"]", text, re.M)
        down = re.search(
            r"^down_revision:[^=]*= (?:['\"](\w+)['\"]|None)", text, re.M
        )
        assert revision is not None, f"{module.name} declares no revision"
        assert down is not None, f"{module.name} declares no down_revision"
        assert revision.group(1) not in revisions, (
            f"{module.name} and {revisions[revision.group(1)]} both claim revision "
            f"{revision.group(1)!r}"
        )
        revisions[revision.group(1)] = module.name
        down_revisions[revision.group(1)] = down.group(1)

    heads = set(revisions) - {d for d in down_revisions.values() if d}
    assert len(heads) == 1, f"expected one alembic head, found {sorted(heads)}"
    roots = [rev for rev, down in down_revisions.items() if down is None]
    assert roots == ["0001"]


def test_absorption_evidence_relations_are_declared() -> None:
    """Merge/split reads its evidence from relations, not from request bodies."""
    sql = _sql()

    assert "CREATE TABLE IF NOT EXISTS expansion.heatzone_absorption_outcomes" in sql
    assert "CREATE TABLE IF NOT EXISTS geo.h3_cell_adjacency" in sql
    # Every recorded outcome has to name the HZ-004 snapshot it came from.
    assert "CONSTRAINT chk_absorption_outcome_basis" in sql
    assert "jsonb_array_length(basis_source_ids) > 0" in sql
    # Absorption history is evidence: appended to, never rewritten.
    assert "CREATE TRIGGER trg_heatzone_absorption_outcomes_append_only" in sql
    assert "BEFORE UPDATE OR DELETE ON expansion.heatzone_absorption_outcomes" in sql
    # Adjacency is stored once per unordered pair so readiness cannot double-count.
    assert "CONSTRAINT chk_h3_adjacency_ordered CHECK (cell_id < neighbor_cell_id)" in sql


def test_seeded_policy_declares_the_pairing_thresholds() -> None:
    """A pair is only testable once it has enough jointly observed periods."""
    sql = _sql()

    assert '"min_paired_periods": 6' in sql
    assert '"min_split_side_periods": 6' in sql
    assert "'heatzone_absorption_outcomes'" in sql
