from __future__ import annotations

from datetime import UTC, datetime

import pytest

from modules.external_data.application.source_snapshots import SourceSnapshotService
from shared.infrastructure.object_store.client import InMemoryObjectStore, ResidencyDeniedError


def test_in_memory_residency_enforcement() -> None:
    # Setup document store Mock for resolving residency mode
    tenants = {
        "tenant-tw": {"residency_mode": "TW_ONLY"},
        "tenant-apac": {"residency_mode": "APPROVED_APAC_DR"},
    }

    class MockDocumentStore:
        def get(self, collection: str, tenant_id: str) -> dict | None:
            if collection == "operator.tenant_metadata":
                return tenants.get(tenant_id)
            return None

    doc_store = MockDocumentStore()
    
    def residency_resolver(tenant_id: str) -> str:
        meta = doc_store.get("operator.tenant_metadata", tenant_id)
        return meta.get("residency_mode", "TW_ONLY") if meta else "TW_ONLY"

    store = InMemoryObjectStore(tenant_residency_resolver=residency_resolver)
    service = SourceSnapshotService(db_conn=None, object_store=store, document_store=doc_store)

    service.register_source(
        source_id="src-1",
        display_name="Source 1",
        allowed_hosts=["src1.com"],
        retrieval_mode="APPROVED_RETRIEVAL",
    )

    # 1. Tenant TW_ONLY can upload to Taiwan bucket
    snapshot_id_1 = service.create_snapshot(
        tenant_id="tenant-tw",
        intake_id="IN-1",
        source_id="src-1",
        raw_data=b"taiwan data",
        original_url="http://src1.com",
        canonical_url="http://src1.com",
        media_type="text/html",
        capture_method="SERVER_RETRIEVAL",
        retention_class="STANDARD",
        encryption_key_ref="kms://1",
        observed_at=datetime.now(UTC),
        captured_at=datetime.now(UTC),
        bucket="snapshots-taiwan",
    )
    assert snapshot_id_1 is not None

    # 2. Tenant TW_ONLY uploading to US bucket must fail residency check
    with pytest.raises(ResidencyDeniedError) as exc:
        service.create_snapshot(
            tenant_id="tenant-tw",
            intake_id="IN-2",
            source_id="src-1",
            raw_data=b"cross-region data",
            original_url="http://src1.com",
            canonical_url="http://src1.com",
            media_type="text/html",
            capture_method="SERVER_RETRIEVAL",
            retention_class="STANDARD",
            encryption_key_ref="kms://1",
            observed_at=datetime.now(UTC),
            captured_at=datetime.now(UTC),
            bucket="snapshots-us-east",
        )
    assert "RESIDENCY_DENIED" in str(exc.value)

    # 3. Tenant APPROVED_APAC_DR can upload to apac-dr bucket
    snapshot_id_2 = service.create_snapshot(
        tenant_id="tenant-apac",
        intake_id="IN-3",
        source_id="src-1",
        raw_data=b"apac data",
        original_url="http://src1.com",
        canonical_url="http://src1.com",
        media_type="text/html",
        capture_method="SERVER_RETRIEVAL",
        retention_class="STANDARD",
        encryption_key_ref="kms://1",
        observed_at=datetime.now(UTC),
        captured_at=datetime.now(UTC),
        bucket="snapshots-apac-dr",
    )
    assert snapshot_id_2 is not None


# --- PostgreSQL RLS tests ---
@pytest.mark.requires_live_env
def test_postgres_rls_isolation_on_snapshots(intake_db) -> None:
    """Prove that tenant isolation policy (RLS) restricts read access on source_snapshots."""
    tenant_a = "00000000-0000-0000-0000-00000000000a"
    tenant_b = "00000000-0000-0000-0000-00000000000b"
    source_id = "src-rls"
    
    # 1. Setup global source
    with intake_db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO intake.source_registry (
                source_id, display_name, allowed_hosts, canonicalization_rule_version,
                retrieval_mode, policy_owner_subject_id, kill_switch, production_enabled
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id) DO NOTHING
            """,
            (source_id, "RLS Source", ["rls.com"], "v1.0", "APPROVED_RETRIEVAL", "00000000-0000-0000-0000-000000000000", False, True)
        )

        # 2. Insert intakes for both tenants
        # We must SET LOCAL app.tenant_id inside psycopg transaction context to insert
        cur.execute("SET LOCAL app.tenant_id = %s", (tenant_a,))
        cur.execute(
            "INSERT INTO intake.intakes (intake_id, tenant_id, source_id, canonical_url, stage, version) VALUES (%s, %s, %s, %s, %s, %s)",
            ("00000000-0000-0000-0000-000000000001", tenant_a, source_id, "https://rls.com/a", "SUBMITTED", 1)
        )

        cur.execute("SET LOCAL app.tenant_id = %s", (tenant_b,))
        cur.execute(
            "INSERT INTO intake.intakes (intake_id, tenant_id, source_id, canonical_url, stage, version) VALUES (%s, %s, %s, %s, %s, %s)",
            ("00000000-0000-0000-0000-000000000002", tenant_b, source_id, "https://rls.com/b", "SUBMITTED", 1)
        )

        # 3. Insert snapshots
        cur.execute("SET LOCAL app.tenant_id = %s", (tenant_a,))
        cur.execute(
            """
            INSERT INTO intake.source_snapshots (
                source_snapshot_id, tenant_id, intake_id, source_id, raw_object_uri,
                content_sha256, media_type, byte_length, captured_at, observed_at,
                capture_method, retention_class, encryption_key_ref
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "00000000-0000-0000-0000-000000000003", tenant_a, "00000000-0000-0000-0000-000000000001",
                source_id, "gs://taiwan/a", "sha-a", "text/html", 100, datetime.now(UTC), datetime.now(UTC),
                "SERVER_RETRIEVAL", "STANDARD", "key-a"
            )
        )

        cur.execute("SET LOCAL app.tenant_id = %s", (tenant_b,))
        cur.execute(
            """
            INSERT INTO intake.source_snapshots (
                source_snapshot_id, tenant_id, intake_id, source_id, raw_object_uri,
                content_sha256, media_type, byte_length, captured_at, observed_at,
                capture_method, retention_class, encryption_key_ref
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "00000000-0000-0000-0000-000000000004", tenant_b, "00000000-0000-0000-0000-000000000002",
                source_id, "gs://taiwan/b", "sha-b", "text/html", 120, datetime.now(UTC), datetime.now(UTC),
                "SERVER_RETRIEVAL", "STANDARD", "key-b"
            )
        )

    # 4. Read under tenant_a -> should ONLY see tenant_a snapshot
    with intake_db.connect() as conn:
        cur = conn.cursor()
        cur.execute("SET LOCAL app.tenant_id = %s", (tenant_a,))
        cur.execute("SELECT source_snapshot_id FROM intake.source_snapshots")
        rows_a = [str(r[0]) for r in cur.fetchall()]
        assert "00000000-0000-0000-0000-000000000003" in rows_a
        assert "00000000-0000-0000-0000-000000000004" not in rows_a

    # 5. Read under tenant_b -> should ONLY see tenant_b snapshot
    with intake_db.connect() as conn:
        cur = conn.cursor()
        cur.execute("SET LOCAL app.tenant_id = %s", (tenant_b,))
        cur.execute("SELECT source_snapshot_id FROM intake.source_snapshots")
        rows_b = [str(r[0]) for r in cur.fetchall()]
        assert "00000000-0000-0000-0000-000000000004" in rows_b
        assert "00000000-0000-0000-0000-000000000003" not in rows_b
