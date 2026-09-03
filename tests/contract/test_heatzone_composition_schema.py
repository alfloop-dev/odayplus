"""HeatZone composition PostgreSQL schema and constraints contract test (ODP-FR-HZ-006).

Tests DDL constraints, foreign keys, unique active indexes, and append-only trigger
against a live PostgreSQL server via pgserver fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION_POLICY_MIGRATION = (
    REPO_ROOT / "infra/db/migrations/000014_decision_policy_registry.sql"
)
COMPOSITION_MIGRATION = (
    REPO_ROOT / "infra/db/migrations/000018_heatzone_composition.sql"
)

live = pytest.mark.requires_live_env

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "11111111-1111-1111-1111-222222222222"
CELL_1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CELL_2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
CELL_3 = "cccccccc-cccc-cccc-cccc-cccccccccccc"

BASELINE_STUB = f"""
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS geo;
CREATE SCHEMA IF NOT EXISTS workflow;
CREATE SCHEMA IF NOT EXISTS expansion;

CREATE TABLE IF NOT EXISTS core.tenants (
    tenant_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_name VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS geo.h3_cells (
    geo_cell_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    h3_index    VARCHAR(32) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS workflow.decisions (
    decision_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    decision_type     VARCHAR(100) NOT NULL DEFAULT 'site_go_wait_reject',
    entity_type       VARCHAR(100) NOT NULL,
    entity_id         VARCHAR(255) NOT NULL,
    decision_status   VARCHAR(50)  NOT NULL DEFAULT 'proposed',
    policy_version_id VARCHAR(100) NOT NULL,
    created_by        VARCHAR(255) NOT NULL,
    created_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def composition_db(intake_blank_db):
    """PostgreSQL 16 with baseline stubs, decision policy migration, and composition migration."""
    with intake_blank_db.connect(autocommit=True) as conn:
        conn.execute(BASELINE_STUB)
        conn.execute(
            "INSERT INTO core.tenants (tenant_id, tenant_name) VALUES (%s, %s), (%s, %s)",
            (TENANT_A, "tenant-a", TENANT_B, "tenant-b"),
        )
        conn.execute(
            "INSERT INTO geo.h3_cells (geo_cell_id, h3_index) VALUES (%s, %s), (%s, %s), (%s, %s)",
            (CELL_1, "8928308280fffff", CELL_2, "8928308281fffff", CELL_3, "8928308282fffff"),
        )
        conn.execute(DECISION_POLICY_MIGRATION.read_text(encoding="utf-8"))
        conn.execute(COMPOSITION_MIGRATION.read_text(encoding="utf-8"))
    return intake_blank_db


@live
class TestCompositionSchemaConstraints:
    def test_zone_id_format_constraint(self, composition_db) -> None:
        psycopg = composition_db.server.psycopg
        with composition_db.connect(autocommit=True) as conn:
            # Valid insert
            conn.execute(
                """
                INSERT INTO expansion.heatzone_composition
                (zone_id, tenant_id, member_cell_id, composition_kind, decided_by, decided_at, decision_policy_version_id)
                VALUES ('MZ-1234567890abcdef', %s, %s, 'MERGED', 'system', NOW(), %s)
                """,
                (TENANT_A, CELL_1, f"heatzone-merge-v1:{TENANT_A}"),
            )

            # Invalid zone_id: not starting with MZ- or wrong length
            with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
                conn.execute(
                    """
                    INSERT INTO expansion.heatzone_composition
                    (zone_id, tenant_id, member_cell_id, composition_kind, decided_by, decided_at, decision_policy_version_id)
                    VALUES ('bad-zone-id', %s, %s, 'MERGED', 'system', NOW(), %s)
                    """,
                    (TENANT_A, CELL_2, f"heatzone-merge-v1:{TENANT_A}"),
                )
            assert excinfo.value.diag.constraint_name == "chk_composition_zone_id_format"

    def test_parent_zone_constraint(self, composition_db) -> None:
        psycopg = composition_db.server.psycopg
        with composition_db.connect(autocommit=True) as conn:
            # SPLIT_CHILD requires parent_zone_id
            with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
                conn.execute(
                    """
                    INSERT INTO expansion.heatzone_composition
                    (zone_id, tenant_id, member_cell_id, composition_kind, parent_zone_id, decided_by, decided_at, decision_policy_version_id)
                    VALUES ('MZ-aaaaaaaaaaaaaaaa', %s, %s, 'SPLIT_CHILD', NULL, 'system', NOW(), %s)
                    """,
                    (TENANT_A, CELL_1, f"heatzone-merge-v1:{TENANT_A}"),
                )
            assert excinfo.value.diag.constraint_name == "chk_composition_parent"

            # MERGED must NOT have parent_zone_id
            with pytest.raises(psycopg.errors.CheckViolation) as excinfo2:
                conn.execute(
                    """
                    INSERT INTO expansion.heatzone_composition
                    (zone_id, tenant_id, member_cell_id, composition_kind, parent_zone_id, decided_by, decided_at, decision_policy_version_id)
                    VALUES ('MZ-aaaaaaaaaaaaaaaa', %s, %s, 'MERGED', 'MZ-parent000000000', 'system', NOW(), %s)
                    """,
                    (TENANT_A, CELL_1, f"heatzone-merge-v1:{TENANT_A}"),
                )
            assert excinfo2.value.diag.constraint_name == "chk_composition_parent"

    def test_override_reason_constraint(self, composition_db) -> None:
        psycopg = composition_db.server.psycopg
        with composition_db.connect(autocommit=True) as conn:
            # Human override requires reason
            with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
                conn.execute(
                    """
                    INSERT INTO expansion.heatzone_composition
                    (zone_id, tenant_id, member_cell_id, composition_kind, decided_by, decided_at, decision_policy_version_id, override_reason)
                    VALUES ('MZ-bbbbbbbbbbbbbbbb', %s, %s, 'MERGED', 'operator@oday.com', NOW(), %s, NULL)
                    """,
                    (TENANT_A, CELL_1, f"heatzone-merge-v1:{TENANT_A}"),
                )
            assert excinfo.value.diag.constraint_name == "chk_composition_override_reason"

            # System must NOT have override_reason
            with pytest.raises(psycopg.errors.CheckViolation) as excinfo2:
                conn.execute(
                    """
                    INSERT INTO expansion.heatzone_composition
                    (zone_id, tenant_id, member_cell_id, composition_kind, decided_by, decided_at, decision_policy_version_id, override_reason)
                    VALUES ('MZ-bbbbbbbbbbbbbbbb', %s, %s, 'MERGED', 'system', NOW(), %s, 'invalid-for-system')
                    """,
                    (TENANT_A, CELL_1, f"heatzone-merge-v1:{TENANT_A}"),
                )
            assert excinfo2.value.diag.constraint_name == "chk_composition_override_reason"

    def test_active_member_uniqueness_index(self, composition_db) -> None:
        psycopg = composition_db.server.psycopg
        with composition_db.connect(autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO expansion.heatzone_composition
                (zone_id, tenant_id, member_cell_id, composition_kind, decided_by, decided_at, decision_policy_version_id)
                VALUES ('MZ-cccccccccccccccc', %s, %s, 'MERGED', 'system', NOW(), %s)
                """,
                (TENANT_A, CELL_1, f"heatzone-merge-v1:{TENANT_A}"),
            )

            # Inserting CELL_1 into another active zone fails
            with pytest.raises(psycopg.errors.UniqueViolation) as excinfo:
                conn.execute(
                    """
                    INSERT INTO expansion.heatzone_composition
                    (zone_id, tenant_id, member_cell_id, composition_kind, decided_by, decided_at, decision_policy_version_id)
                    VALUES ('MZ-dddddddddddddddd', %s, %s, 'MERGED', 'system', NOW(), %s)
                    """,
                    (TENANT_A, CELL_1, f"heatzone-merge-v1:{TENANT_A}"),
                )
            assert excinfo.value.diag.constraint_name == "idx_heatzone_composition_active_member"

    def test_append_only_trigger_prevents_delete_and_disallowed_updates(self, composition_db) -> None:
        psycopg = composition_db.server.psycopg
        with composition_db.connect(autocommit=True) as conn:
            row = conn.execute(
                """
                INSERT INTO expansion.heatzone_composition
                (zone_id, tenant_id, member_cell_id, composition_kind, decided_by, decided_at, decision_policy_version_id)
                VALUES ('MZ-eeeeeeeeeeeeeeee', %s, %s, 'MERGED', 'system', NOW(), %s)
                RETURNING composition_id
                """,
                (TENANT_A, CELL_1, f"heatzone-merge-v1:{TENANT_A}"),
            ).fetchone()
            comp_id = row[0]

            # DELETE is forbidden
            with pytest.raises(psycopg.errors.CheckViolation) as excinfo_del:
                conn.execute(
                    "DELETE FROM expansion.heatzone_composition WHERE composition_id = %s",
                    (comp_id,),
                )
            assert "DELETE is not permitted" in str(excinfo_del.value)

            # Updating zone_id is forbidden
            with pytest.raises(psycopg.errors.CheckViolation) as excinfo_upd:
                conn.execute(
                    "UPDATE expansion.heatzone_composition SET zone_id = 'MZ-ffffffffffffffff' WHERE composition_id = %s",
                    (comp_id,),
                )
            assert "only permitted UPDATE is setting reverted_at" in str(excinfo_upd.value)

            # Setting reverted_at is permitted
            conn.execute(
                "UPDATE expansion.heatzone_composition SET reverted_at = NOW() WHERE composition_id = %s",
                (comp_id,),
            )

            # Updating already reverted record is forbidden
            with pytest.raises(psycopg.errors.CheckViolation) as excinfo_reupd:
                conn.execute(
                    "UPDATE expansion.heatzone_composition SET reverted_at = NOW() WHERE composition_id = %s",
                    (comp_id,),
                )
            assert "is already reverted" in str(excinfo_reupd.value)
