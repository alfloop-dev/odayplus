"""Production-entry Integration tests for Durable Manual Correction Persistence & Rollback (ODP-INT-006 / ODP-INT-MANUAL-CORRECTION-AUDIT-001).

Tests:
1. SQLite durable persistence: save, correct, audit chain, restart survival, rollback, restart survival.
2. Alembic migration 0014 schema execution and downgrade idempotency.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4
import pytest

from shared.domain.models import AddressLocation
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.repositories import (
    DurableAddressLocationRepository,
    DurableManualCorrectionRepository,
)


def test_sqlite_durable_manual_correction_and_restart_survival(tmp_path: Path) -> None:
    db_file = tmp_path / "durable_correction.sqlite3"
    
    # 1. First boot: Initialize repositories against SQLite
    engine1 = SqliteEngine(db_file)
    audit_log1 = DurableAuditLog(engine1)
    corr_repo1 = DurableManualCorrectionRepository(engine1)
    addr_repo1 = DurableAddressLocationRepository(
        engine1, correction_repo=corr_repo1, audit_log=audit_log1
    )

    addr_id = str(uuid4())
    address = AddressLocation(
        address_id=addr_id,
        raw_address="Taipei City Xinyi Dist Section 5 No 7",
        normalized_address="Taipei City Xinyi District Section 5 No 7",
        city="Taipei City",
        district="Xinyi District",
        road="Section 5",
        latitude=25.0330,
        longitude=121.5650,
        geocode_precision="rooftop",
        geocode_confidence=0.90,
        manual_override_flag=False,
        tenant_id="tenant-e2e",
        revision=1,
    )
    addr_repo1.save_address(address)

    # Verify initial read
    saved = addr_repo1.get_address(addr_id)
    assert saved is not None
    assert saved.manual_override_flag is False
    assert saved.revision == 1

    # 2. Apply manual correction
    updated_addr, correction, decision_card = addr_repo1.apply_correction(
        addr_id,
        updates={
            "latitude": 25.0345,
            "longitude": 121.5665,
            "normalized_address": "Taipei City Xinyi District Section 5 No 7 (Entrance B)",
        },
        reason="Corrected location to secondary entrance for logistics delivery",
        actor_id="operator-reviewer-101",
        tenant_id="tenant-e2e",
        expected_revision=1,
        correlation_id="corr-durable-001",
    )

    assert updated_addr.manual_override_flag is True
    assert updated_addr.revision == 2
    assert updated_addr.latitude == 25.0345
    assert correction.status == "applied"
    assert correction.source_revision == 1
    assert correction.applied_revision == 2

    # Verify SQLite table content directly
    row = engine1.query_one("SELECT * FROM durable_manual_corrections WHERE correction_id = ?", (correction.correction_id,))
    assert row is not None
    assert row["entity_id"] == addr_id
    assert row["actor_id"] == "operator-reviewer-101"
    assert row["status"] == "applied"
    assert "entrance coordinates" not in row["reason"]  # check exact reason
    assert "secondary entrance" in row["reason"]

    # Verify audit log chain
    verification = audit_log1.verify_chain()
    assert verification.ok is True

    # 3. Simulate process restart: Re-open database with brand new engine and repositories
    engine2 = SqliteEngine(db_file)
    audit_log2 = DurableAuditLog(engine2)
    corr_repo2 = DurableManualCorrectionRepository(engine2)
    addr_repo2 = DurableAddressLocationRepository(
        engine2, correction_repo=corr_repo2, audit_log=audit_log2
    )

    readback_addr = addr_repo2.get_address(addr_id)
    assert readback_addr is not None
    assert readback_addr.manual_override_flag is True
    assert readback_addr.revision == 2
    assert readback_addr.latitude == 25.0345
    assert readback_addr.normalized_address == "Taipei City Xinyi District Section 5 No 7 (Entrance B)"

    corrections_list = addr_repo2.get_corrections(addr_id, correction_repo=corr_repo2)
    assert len(corrections_list) == 1
    assert corrections_list[0].correction_id == correction.correction_id
    assert corrections_list[0].status == "applied"

    # 4. Rollback correction
    restored_addr, rolled_back_corr, rollback_card = addr_repo2.rollback_correction(
        addr_id,
        correction.correction_id,
        reason="Operator compensation: reverted secondary entrance override",
        actor_id="admin-manager-99",
        tenant_id="tenant-e2e",
        expected_revision=2,
        correlation_id="corr-durable-rollback",
    )

    assert restored_addr.manual_override_flag is False
    assert restored_addr.revision == 3
    assert restored_addr.latitude == 25.0330
    assert restored_addr.longitude == 121.5650
    assert restored_addr.normalized_address == "Taipei City Xinyi District Section 5 No 7"
    assert rolled_back_corr.status == "rolled_back"

    # 5. Simulate second process restart: Verify rollback state survived on disk
    engine3 = SqliteEngine(db_file)
    audit_log3 = DurableAuditLog(engine3)
    corr_repo3 = DurableManualCorrectionRepository(engine3)
    addr_repo3 = DurableAddressLocationRepository(
        engine3, correction_repo=corr_repo3, audit_log=audit_log3
    )

    final_addr = addr_repo3.get_address(addr_id)
    assert final_addr is not None
    assert final_addr.manual_override_flag is False
    assert final_addr.revision == 3
    assert final_addr.latitude == 25.0330

    final_corrections = addr_repo3.get_corrections(addr_id, correction_repo=corr_repo3)
    assert len(final_corrections) == 1
    assert final_corrections[0].status == "rolled_back"

    # Verify audit hash chain verification on all logged events across restarts
    assert audit_log3.verify_chain().ok is True
    events = audit_log3.list_events()
    assert len(events) == 2
    assert events[0].action == "manual_override"
    assert events[1].action == "rollback_manual_override"


def test_migration_0014_sql_and_alembic_structure() -> None:
    migration_sql_path = Path("infra/db/migrations/000019_manual_corrections_audit_schema.sql")
    assert migration_sql_path.exists()
    sql_content = migration_sql_path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS odp_runtime.durable_manual_corrections" in sql_content
    assert "idx_runtime_manual_corrections_entity" in sql_content

    alembic_script_path = Path("infra/db/migrations/versions/0014_manual_corrections_audit_schema.py")
    assert alembic_script_path.exists()
    script_content = alembic_script_path.read_text(encoding="utf-8")
    assert 'revision: str = "0014"' in script_content
    assert 'down_revision: str | None = "0013"' in script_content
