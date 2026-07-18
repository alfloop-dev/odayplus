"""Tenant-isolation (RLS) security tests for the Assisted Listing Intake schema.

These prove the fail-closed row level security installed by patch 0004 actually
isolates tenants on a real PostgreSQL 16 server, exercised as a NON-superuser role
(superusers bypass RLS, so testing as the superuser would prove nothing). See
``tests/conftest.py`` for provisioning; tests are marked ``requires_live_env`` and
skip cleanly when no PostgreSQL 16 is reachable.
"""
from __future__ import annotations

import pytest

from shared.infrastructure.persistence import assisted_listing_intake as intake

pytestmark = pytest.mark.requires_live_env

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
SUBJECT = "cccccccc-cccc-cccc-cccc-cccccccccccc"
FP = "f" * 64
APP_ROLE = "intake_app_rw"

# Representative tenant tables spanning all five business schemas, each seedable
# with one self-contained row per tenant.
SEEDED_TABLES = (
    "intake.intakes",
    "identity.properties",
    "expansion.listings",
    "workflow.jobs",
    "audit.audit_events",
)


def _seed_tenant(cur, tenant: str, tag: str) -> None:
    cur.execute(
        """
        INSERT INTO intake.intakes
          (tenant_id, submitter_subject_id, intake_method, processing_state, correlation_id)
        VALUES (%s, %s, 'MANUAL', 'SUBMITTED', gen_random_uuid())
        """,
        (tenant, SUBJECT),
    )
    cur.execute(
        """
        INSERT INTO identity.properties (tenant_id, normalized_address, address_fingerprint)
        VALUES (%s, %s, %s)
        """,
        (tenant, f"addr-{tag}", tag.ljust(64, "0")[:64]),
    )
    cur.execute(
        """
        INSERT INTO expansion.listings (tenant_id, source_id, lifecycle_state)
        VALUES (%s, 'src-approved', 'ACTIVE')
        """,
        (tenant,),
    )
    cur.execute(
        """
        INSERT INTO workflow.jobs
          (tenant_id, job_type, aggregate_type, aggregate_id, status, checkpoint,
           max_attempts, timeout_at, payload, correlation_id)
        VALUES (%s, 'parse', 'intake', gen_random_uuid(), 'QUEUED', 'start',
                5, now() + interval '1 hour', '{}'::jsonb, gen_random_uuid())
        """,
        (tenant,),
    )
    cur.execute(
        """
        INSERT INTO audit.audit_events
          (tenant_id, sequence_no, event_type, action, resource_type, resource_id,
           result, correlation_id, event_sha256, occurred_at, retained_until)
        VALUES (%s, 1, 'intake.submitted', 'CREATE', 'intake', gen_random_uuid(),
                'SUCCEEDED', gen_random_uuid(), %s, now(), now() + interval '5 years')
        """,
        (tenant, tag.ljust(64, "0")[:64]),
    )


@pytest.fixture
def rls_db(intake_db):
    """Intake migration applied, seeded for two tenants, with a non-superuser role."""
    with intake_db.connect(autocommit=True) as conn:
        cur = conn.cursor()
        # Global reference row required by expansion.listings.source_id FK.
        cur.execute(
            """
            INSERT INTO intake.source_registry
              (source_id, display_name, canonicalization_rule_version, retrieval_mode,
               policy_owner_subject_id, production_enabled)
            VALUES ('src-approved', 'Approved Source', 'v1', 'APPROVED_RETRIEVAL', %s, true)
            """,
            (SUBJECT,),
        )
        _seed_tenant(cur, TENANT_A, "aaaa")
        _seed_tenant(cur, TENANT_B, "bbbb")

        cur.execute(f"DROP ROLE IF EXISTS {APP_ROLE}")
        cur.execute(f"CREATE ROLE {APP_ROLE} NOSUPERUSER")
        for schema in intake.SCHEMAS:
            cur.execute(f"GRANT USAGE ON SCHEMA {schema} TO {APP_ROLE}")
            cur.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {APP_ROLE}"
            )
    return intake_db


def _as_app(conn, tenant: str | None):
    """Enter a transaction acting as the app role with an optional tenant context."""
    cur = conn.cursor()
    cur.execute(f"SET ROLE {APP_ROLE}")
    if tenant is not None:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
    return cur


def test_missing_tenant_context_is_fail_closed(rls_db) -> None:
    with rls_db.connect(autocommit=False) as conn:
        cur = _as_app(conn, tenant=None)
        for table in SEEDED_TABLES:
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0, f"{table} leaked rows without tenant context"
        conn.rollback()


def test_empty_tenant_context_is_fail_closed(rls_db) -> None:
    with rls_db.connect(autocommit=False) as conn:
        cur = _as_app(conn, tenant="")
        for table in SEEDED_TABLES:
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0, f"{table} leaked rows with empty tenant context"
        conn.rollback()


def test_tenant_context_scopes_reads_to_that_tenant(rls_db) -> None:
    with rls_db.connect(autocommit=False) as conn:
        for tenant in (TENANT_A, TENANT_B):
            cur = _as_app(conn, tenant=tenant)
            for table in SEEDED_TABLES:
                cur.execute(f"SELECT tenant_id FROM {table}")
                rows = cur.fetchall()
                assert len(rows) == 1, f"{table} not scoped for {tenant}"
                assert str(rows[0][0]) == tenant, f"{table} returned another tenant"
            conn.rollback()


def test_with_check_blocks_cross_tenant_insert(rls_db) -> None:
    psycopg = pytest.importorskip("psycopg")
    with rls_db.connect(autocommit=False) as conn:
        cur = _as_app(conn, tenant=TENANT_A)
        # Same-tenant insert is allowed.
        cur.execute(
            """
            INSERT INTO identity.properties (tenant_id, normalized_address, address_fingerprint)
            VALUES (%s, 'ok', %s)
            """,
            (TENANT_A, FP),
        )
        # Cross-tenant insert is rejected by the WITH CHECK clause.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                """
                INSERT INTO identity.properties (tenant_id, normalized_address, address_fingerprint)
                VALUES (%s, 'evil', %s)
                """,
                (TENANT_B, FP),
            )
        conn.rollback()


def test_updates_and_deletes_cannot_reach_other_tenants(rls_db) -> None:
    # Acting as tenant A, an UPDATE/DELETE targeting tenant B's row affects nothing.
    with rls_db.connect(autocommit=False) as conn:
        cur = _as_app(conn, tenant=TENANT_A)
        cur.execute("UPDATE identity.properties SET normalized_address = 'hijack'")
        assert cur.rowcount == 1  # only tenant A's own row is visible/updatable
        cur.execute("DELETE FROM workflow.jobs")
        assert cur.rowcount == 1
        conn.rollback()

    # Tenant B's rows are intact after tenant A's attempt (rolled back anyway).
    with rls_db.connect(autocommit=False) as conn:
        cur = _as_app(conn, tenant=TENANT_B)
        cur.execute("SELECT count(*) FROM workflow.jobs")
        assert cur.fetchone()[0] == 1
        conn.rollback()


def test_reference_tables_are_not_tenant_filtered(rls_db) -> None:
    # source_registry is a global reference table with no RLS; the app role reads it
    # regardless of tenant context.
    with rls_db.connect(autocommit=False) as conn:
        cur = _as_app(conn, tenant=TENANT_A)
        cur.execute("SELECT count(*) FROM intake.source_registry")
        assert cur.fetchone()[0] == 1
        conn.rollback()
