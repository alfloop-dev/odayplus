"""Production-entry Integration tests for Durable Manual Correction Persistence & Rollback (ODP-INT-006 / ODP-INT-MANUAL-CORRECTION-AUDIT-001).

Tests:
1. SQLite durable persistence: save, correct, audit chain, restart survival, rollback, restart survival.
2. Alembic migration 0015 schema execution and downgrade idempotency.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.domain.models import AddressLocation
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.factory import build_persistence
from shared.infrastructure.persistence.repositories import (
    DurableAddressLocationRepository,
    DurableManualCorrectionRepository,
    InMemoryAddressLocationRepository,
    StaleRevisionError,
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
    assert re.match(r"^[a-f0-9]{64}$", final_corrections[0].decision_card_hash)

    # Verify audit hash chain verification on all logged events across restarts
    assert audit_log3.verify_chain().ok is True
    events = audit_log3.list_events()
    assert len(events) == 2
    assert events[0].action == "manual_override"
    assert events[1].action == "rollback_manual_override"


def test_production_app_entry_manual_correction_wiring(tmp_path: Path) -> None:
    """Acceptance: create_app wiring properly routes to persistence bundle repositories."""
    # 1. Test with in-memory persistence bundle
    bundle_mem = build_persistence(mode="memory")
    app_mem = create_app(persistence=bundle_mem)
    client_mem = TestClient(app_mem)

    addr_id = str(uuid4())
    addr = AddressLocation(
        address_id=addr_id,
        raw_address="Taipei 101 Tower",
        latitude=25.0339,
        longitude=121.5645,
        manual_override_flag=False,
        tenant_id="tenant-prod-entry",
        revision=1,
    )
    bundle_mem.address_location_repository.save_address(addr)

    headers = {
        "x-subject-id": "site-reviewer-prod",
        "x-roles": "expansion_user",
        "x-tenant-id": "tenant-prod-entry",
    }
    payload = {
        "latitude": 25.0340,
        "longitude": 121.5646,
        "reason": "Production entry route check for coordinate update",
    }

    resp = client_mem.post(f"/api/v1/listings/addresses/{addr_id}/corrections", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["manual_override_flag"] is True
    assert data["address"]["revision"] == 2

    # Verify state was mutated in the injected bundle, not a forgotten in-memory copy
    bundle_addr = bundle_mem.address_location_repository.get_address(addr_id)
    assert bundle_addr is not None
    assert bundle_addr.manual_override_flag is True
    assert bundle_addr.revision == 2
    assert bundle_addr.latitude == 25.0340

    bundle_corrs = bundle_mem.manual_correction_repository.list_corrections(entity_id=addr_id)
    assert len(bundle_corrs) == 1
    assert bundle_corrs[0].status == "applied"


def test_durable_sqlite_app_entry_and_multi_rollback_lifecycle(tmp_path: Path) -> None:
    """Acceptance: create_app with durable SQLite persistence survives restart and respects top-of-stack."""
    db_file = tmp_path / "app_durable.sqlite3"
    bundle1 = build_persistence(mode="durable", db_path=db_file)
    app1 = create_app(persistence=bundle1)
    client1 = TestClient(app1)

    addr_id = str(uuid4())
    addr = AddressLocation(
        address_id=addr_id,
        raw_address="Durable Tower",
        city="Taipei",
        road="Xinyi Rd",
        latitude=25.0300,
        longitude=121.5600,
        manual_override_flag=False,
        tenant_id="tenant-durable-e2e",
        revision=1,
    )
    bundle1.address_location_repository.save_address(addr)

    headers = {
        "x-subject-id": "reviewer-e2e",
        "x-roles": "expansion_user",
        "x-tenant-id": "tenant-durable-e2e",
    }

    # Step 1: Apply correction A (road -> Xinyi Rd Sec 5, rev 2)
    resp_a = client1.post(
        f"/api/v1/listings/addresses/{addr_id}/corrections",
        json={"road": "Xinyi Rd Sec 5", "reason": "Accurate road section specified"},
        headers=headers,
    )
    assert resp_a.status_code == 200
    corr_a_id = resp_a.json()["correction_id"]
    assert resp_a.json()["address"]["revision"] == 2

    # Step 2: Apply correction B (latitude -> 25.0350, rev 3)
    resp_b = client1.post(
        f"/api/v1/listings/addresses/{addr_id}/corrections",
        json={"latitude": 25.0350, "reason": "High-accuracy GPS fix at lobby"},
        headers=headers,
    )
    assert resp_b.status_code == 200
    corr_b_id = resp_b.json()["correction_id"]
    assert resp_b.json()["address"]["revision"] == 3

    # Step 3: Simulate restart with new app and bundle against same DB
    bundle2 = build_persistence(mode="durable", db_path=db_file)
    app2 = create_app(persistence=bundle2)
    client2 = TestClient(app2)

    # Readback address and corrections
    get_resp = client2.get(f"/api/v1/listings/addresses/{addr_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["revision"] == 3
    assert get_resp.json()["manual_override_flag"] is True
    assert get_resp.json()["road"] == "Xinyi Rd Sec 5"
    assert get_resp.json()["latitude"] == 25.0350

    list_resp = client2.get(f"/api/v1/listings/addresses/{addr_id}/corrections", headers=headers)
    assert list_resp.status_code == 200
    corrs = list_resp.json()["corrections"]
    assert len(corrs) == 2
    for c in corrs:
        assert re.match(r"^[a-f0-9]{64}$", c["decision_card_hash"])

    # Step 4: Out-of-order rollback of A must fail with 422
    rb_a_fail = client2.post(
        f"/api/v1/listings/addresses/{addr_id}/corrections/{corr_a_id}/rollback",
        json={"reason": "Attempting invalid non-top rollback"},
        headers=headers,
    )
    assert rb_a_fail.status_code == 422
    assert "ROLLBACK_ORDER_VIOLATION" in rb_a_fail.text

    # Step 5: Rollback B (top of stack) -> rev 4, leaves A intact
    rb_b_ok = client2.post(
        f"/api/v1/listings/addresses/{addr_id}/corrections/{corr_b_id}/rollback",
        json={"reason": "Reverting GPS override B"},
        headers=headers,
    )
    assert rb_b_ok.status_code == 200
    assert rb_b_ok.json()["status"] == "rolled_back"
    assert rb_b_ok.json()["manual_override_flag"] is True
    assert rb_b_ok.json()["address"]["road"] == "Xinyi Rd Sec 5"
    assert rb_b_ok.json()["address"]["latitude"] == 25.0300
    assert rb_b_ok.json()["address"]["revision"] == 4

    # Step 6: Rollback A (now top of stack) -> rev 5, restores initial state
    rb_a_ok = client2.post(
        f"/api/v1/listings/addresses/{addr_id}/corrections/{corr_a_id}/rollback",
        json={"reason": "Reverting road section override A"},
        headers=headers,
    )
    assert rb_a_ok.status_code == 200
    assert rb_a_ok.json()["status"] == "rolled_back"
    assert rb_a_ok.json()["manual_override_flag"] is False
    assert rb_a_ok.json()["address"]["road"] == "Xinyi Rd"
    assert rb_a_ok.json()["address"]["revision"] == 5


def test_migration_0015_sql_and_alembic_structure() -> None:
    migration_sql_path = Path("infra/db/migrations/000020_manual_corrections_audit_schema.sql")
    assert migration_sql_path.exists()
    sql_content = migration_sql_path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS odp_runtime.durable_manual_corrections" in sql_content
    assert "idx_runtime_manual_corrections_entity" in sql_content

    alembic_script_path = Path("infra/db/migrations/versions/0015_manual_corrections_audit_schema.py")
    assert alembic_script_path.exists()
    script_content = alembic_script_path.read_text(encoding="utf-8")
    assert 'revision: str = "0015"' in script_content
    assert 'down_revision: str | None = "0014"' in script_content


def test_durable_sqlite_h3_restoration_and_tenant_isolation_regressions(tmp_path: Path) -> None:
    """Integration Regression Test for SQLite: H3 cell restoration on rollback and strict tenant isolation."""
    import h3

    db_file = tmp_path / "regression_durable.sqlite3"
    bundle = build_persistence(mode="durable", db_path=db_file)
    addr_repo = bundle.address_location_repository

    # 1. Test Legacy NULL/Empty Tenant Protection on SQLite
    untenanted_id = str(uuid4())
    untenanted_addr = AddressLocation(
        address_id=untenanted_id,
        raw_address="Legacy Global Address",
        latitude=25.0330,
        longitude=121.5654,
        manual_override_flag=False,
        tenant_id="",
        revision=1,
    )
    addr_repo.save_address(untenanted_addr)

    # Cross-tenant modification/claim attempt on un-tenanted record MUST raise PermissionError
    import pytest
    with pytest.raises(PermissionError):
        addr_repo.apply_correction(
            untenanted_id,
            updates={"latitude": 25.0400},
            reason="Illegal attempt to claim un-tenanted record",
            actor_id="tenant-beta-user",
            tenant_id="tenant-beta",
        )

    # Ensure list_addresses for tenant-beta does NOT leak un-tenanted row
    assert not any(a.address_id == untenanted_id for a in addr_repo.list_addresses(tenant_id="tenant-beta"))

    # 2. Test H3 Cell Restoration and Self-Contained Snapshots on SQLite
    tenanted_id = str(uuid4())
    orig_lat, orig_lng = 25.0330, 121.5654
    orig_h3_8 = h3.latlng_to_cell(orig_lat, orig_lng, 8)
    orig_h3_9 = h3.latlng_to_cell(orig_lat, orig_lng, 9)
    orig_h3_10 = h3.latlng_to_cell(orig_lat, orig_lng, 10)

    tenanted_addr = AddressLocation(
        address_id=tenanted_id,
        raw_address="Taipei 101 Base",
        latitude=orig_lat,
        longitude=orig_lng,
        h3_res_8=orig_h3_8,
        h3_res_9=orig_h3_9,
        h3_res_10=orig_h3_10,
        manual_override_flag=False,
        tenant_id="tenant-alpha",
        revision=1,
    )
    addr_repo.save_address(tenanted_addr)

    # Apply coordinate correction
    corr_lat, corr_lng = 25.0450, 121.5200
    updated_addr, corr, dec_card = addr_repo.apply_correction(
        tenanted_id,
        updates={"latitude": corr_lat, "longitude": corr_lng},
        reason="Updated coordinates for site entrance",
        actor_id="reviewer-alpha",
        tenant_id="tenant-alpha",
    )
    assert updated_addr.h3_res_8 != orig_h3_8
    assert updated_addr.h3_res_8 == h3.latlng_to_cell(corr_lat, corr_lng, 8)

    # Rollback coordinate correction
    restored_addr, rolled_corr, rb_card = addr_repo.rollback_correction(
        tenanted_id,
        corr.correction_id,
        reason="Reverting coordinate override back to original",
        actor_id="admin-alpha",
        tenant_id="tenant-alpha",
    )

    # Verify restored coordinates and restored H3 cells
    assert restored_addr.latitude == orig_lat
    assert restored_addr.longitude == orig_lng
    assert restored_addr.h3_res_8 == orig_h3_8
    assert restored_addr.h3_res_9 == orig_h3_9
    assert restored_addr.h3_res_10 == orig_h3_10

    # Verify self-contained rollback snapshots in decision card metrics
    assert rb_card.metrics["old_value"]["latitude"] == corr_lat
    assert rb_card.metrics["old_value"]["h3_res_8"] == h3.latlng_to_cell(corr_lat, corr_lng, 8)
    assert rb_card.metrics["new_value"]["latitude"] == orig_lat
    assert rb_card.metrics["new_value"]["h3_res_8"] == orig_h3_8

    # Verify self-contained snapshots in audit log
    events = bundle.audit_log.list_events()
    rb_event = [e for e in events if e.action == "rollback_manual_override"][-1]
    assert rb_event.metadata["old_value"]["latitude"] == corr_lat
    assert rb_event.metadata["new_value"]["latitude"] == orig_lat
    assert bundle.audit_log.verify_chain().ok is True


def _correction_regression_address(address_id: str, **overrides: object) -> AddressLocation:
    """A saved address whose derived geocode fields differ from the values a
    correction would silently impose."""
    fields: dict[str, object] = {
        "address_id": address_id,
        "raw_address": "Taipei City Xinyi Dist Section 5 No 7",
        "normalized_address": "Taipei City Xinyi District Section 5 No 7",
        "city": "Taipei City",
        "district": "Xinyi District",
        "road": "Section 5",
        "latitude": 25.0330,
        "longitude": 121.5650,
        "geocode_precision": "rooftop",
        "geocode_confidence": 0.90,
        "manual_override_flag": False,
        "tenant_id": "tenant-regress",
        "revision": 1,
    }
    fields.update(overrides)
    return AddressLocation(**fields)  # type: ignore[arg-type]


def _address_repositories(tmp_path: Path) -> list[object]:
    """Both production write paths, so a fix to one cannot silently skip the other."""
    engine = SqliteEngine(tmp_path / "correction_regressions.sqlite3")
    corrections = DurableManualCorrectionRepository(engine)
    return [
        InMemoryAddressLocationRepository(),
        DurableAddressLocationRepository(
            engine, correction_repo=corrections, audit_log=DurableAuditLog(engine)
        ),
    ]


def test_apply_preserves_omitted_geocode_precision_and_rollback_restores_explicit_precision(
    tmp_path: Path,
) -> None:
    """When geocode_precision is omitted from updates, existing precision is preserved.
    When geocode_precision is explicitly changed, rollback restores the original precision."""
    for repo in _address_repositories(tmp_path):
        address_id = str(uuid4())
        repo.save_address(  # type: ignore[attr-defined]
            _correction_regression_address(address_id, geocode_precision="rooftop")
        )

        # 1. Omitted geocode_precision must be preserved on apply and readback
        applied, correction, _ = repo.apply_correction(  # type: ignore[attr-defined]
            address_id,
            updates={"latitude": 25.0500},
            reason="shift the pin to the building entrance",
            actor_id="operator-1",
            tenant_id="tenant-regress",
            expected_revision=1,
        )

        assert applied.geocode_precision == "rooftop"
        assert repo.get_address(address_id).geocode_precision == "rooftop"  # type: ignore[attr-defined]
        assert "geocode_precision" not in correction.old_value

        # 2. Explicit geocode_precision update changes precision and rollback restores it
        applied2, correction2, _ = repo.apply_correction(  # type: ignore[attr-defined]
            address_id,
            updates={"geocode_precision": "manual"},
            reason="explicitly setting manual geocode precision",
            actor_id="operator-1",
            tenant_id="tenant-regress",
            expected_revision=2,
        )
        assert applied2.geocode_precision == "manual"
        assert repo.get_address(address_id).geocode_precision == "manual"  # type: ignore[attr-defined]
        assert correction2.old_value["geocode_precision"] == "rooftop"
        assert correction2.new_value["geocode_precision"] == "manual"

        repo.rollback_correction(  # type: ignore[attr-defined]
            address_id,
            correction_id=correction2.correction_id,
            reason="reverting explicit manual precision override",
            actor_id="operator-1",
            tenant_id="tenant-regress",
        )

        restored = repo.get_address(address_id)  # type: ignore[attr-defined]
        assert restored.geocode_precision == "rooftop"
        assert restored.latitude == 25.0500


def test_rollback_restores_zero_geocode_confidence(tmp_path: Path) -> None:
    """When geocode_confidence is omitted from updates, an existing 0.0 must stay 0.0
    on the applied address and survive rollback without being corrupted to 1.0."""
    for repo in _address_repositories(tmp_path):
        address_id = str(uuid4())
        repo.save_address(  # type: ignore[attr-defined]
            _correction_regression_address(address_id, geocode_confidence=0.0)
        )

        applied, correction, _ = repo.apply_correction(  # type: ignore[attr-defined]
            address_id,
            updates={"city": "New Taipei City"},
            reason="city was recorded incorrectly",
            actor_id="operator-1",
            tenant_id="tenant-regress",
            expected_revision=1,
        )
        assert applied.geocode_confidence == 0.0
        assert repo.get_address(address_id).geocode_confidence == 0.0

        repo.rollback_correction(  # type: ignore[attr-defined]
            address_id,
            correction_id=correction.correction_id,
            reason="city change was not approved",
            actor_id="operator-1",
            tenant_id="tenant-regress",
        )

        restored = repo.get_address(address_id)  # type: ignore[attr-defined]
        assert restored.geocode_confidence == 0.0
        assert restored.city == "Taipei City"


def test_rollback_restores_zero_geocode_confidence_when_updated(tmp_path: Path) -> None:
    """When geocode_confidence is explicitly updated from 0.0 to 0.85, rollback restores 0.0."""
    for repo in _address_repositories(tmp_path):
        address_id = str(uuid4())
        repo.save_address(  # type: ignore[attr-defined]
            _correction_regression_address(address_id, geocode_confidence=0.0)
        )

        applied, correction, _ = repo.apply_correction(  # type: ignore[attr-defined]
            address_id,
            updates={"geocode_confidence": 0.85},
            reason="geocoding verified",
            actor_id="operator-1",
            tenant_id="tenant-regress",
            expected_revision=1,
        )
        assert applied.geocode_confidence == 0.85
        assert repo.get_address(address_id).geocode_confidence == 0.85
        assert correction.old_value["geocode_confidence"] == 0.0
        assert correction.new_value["geocode_confidence"] == 0.85

        repo.rollback_correction(  # type: ignore[attr-defined]
            address_id,
            correction_id=correction.correction_id,
            reason="verification rejected",
            actor_id="operator-1",
            tenant_id="tenant-regress",
        )

        restored = repo.get_address(address_id)  # type: ignore[attr-defined]
        assert restored.geocode_confidence == 0.0


def test_rollback_preserves_override_flag_that_predates_the_correction(
    tmp_path: Path,
) -> None:
    """The flag is restored from ``old_value``, not inferred from how many other
    corrections remain applied: a record flagged before any correction existed
    must stay flagged after a rollback."""
    for repo in _address_repositories(tmp_path):
        address_id = str(uuid4())
        repo.save_address(  # type: ignore[attr-defined]
            _correction_regression_address(address_id, manual_override_flag=True)
        )

        _, correction, _ = repo.apply_correction(  # type: ignore[attr-defined]
            address_id,
            updates={"road": "Section 6"},
            reason="road name needs correcting",
            actor_id="operator-1",
            tenant_id="tenant-regress",
            expected_revision=1,
        )
        assert correction.old_value["manual_override_flag"] is True

        _, rollback_card = repo.rollback_correction(  # type: ignore[attr-defined]
            address_id,
            correction_id=correction.correction_id,
            reason="road name was already correct",
            actor_id="operator-1",
            tenant_id="tenant-regress",
        )[1:]

        restored = repo.get_address(address_id)  # type: ignore[attr-defined]
        assert restored.manual_override_flag is True
        assert restored.road == "Section 5"
        assert rollback_card.metrics["new_value"]["manual_override_flag"] is True


def test_rollback_clears_override_flag_the_correction_introduced(tmp_path: Path) -> None:
    """The complement of the case above: a record that was not flagged before the
    correction must come back unflagged."""
    for repo in _address_repositories(tmp_path):
        address_id = str(uuid4())
        repo.save_address(  # type: ignore[attr-defined]
            _correction_regression_address(address_id, manual_override_flag=False)
        )

        _, correction, _ = repo.apply_correction(  # type: ignore[attr-defined]
            address_id,
            updates={"road": "Section 6"},
            reason="road name needs correcting",
            actor_id="operator-1",
            tenant_id="tenant-regress",
            expected_revision=1,
        )
        assert repo.get_address(address_id).manual_override_flag is True  # type: ignore[attr-defined]

        repo.rollback_correction(  # type: ignore[attr-defined]
            address_id,
            correction_id=correction.correction_id,
            reason="road name was already correct",
            actor_id="operator-1",
            tenant_id="tenant-regress",
        )

        assert repo.get_address(address_id).manual_override_flag is False  # type: ignore[attr-defined]


def test_durable_correction_rejects_stale_write_from_concurrent_engine(
    tmp_path: Path,
) -> None:
    """Regression test (P1): optimistic concurrency is enforced by the database.

    ``engine.lock`` is a handle-local lock, so two engines over the same file
    (two API processes in production) both passed the in-memory
    ``expected_revision`` check and both wrote revision 2 -- one correction
    silently overwriting the other. The revision bump is now a conditional
    UPDATE, so the writer holding a stale snapshot matches zero rows.

    The stale snapshot is injected rather than raced: the read that loses is
    exactly the failure a passing concurrent test can never guarantee it hit.
    """
    db_file = tmp_path / "durable_correction_race.sqlite3"

    def build_repo() -> DurableAddressLocationRepository:
        engine = SqliteEngine(db_file)
        return DurableAddressLocationRepository(
            engine, correction_repo=DurableManualCorrectionRepository(engine)
        )

    writer_a = build_repo()
    writer_b = build_repo()

    addr_id = str(uuid4())
    writer_a.save_address(
        AddressLocation(
            address_id=addr_id,
            raw_address="Taipei Concurrency Road 1",
            latitude=25.0000,
            longitude=121.0000,
            manual_override_flag=False,
            tenant_id="tenant-race",
            revision=1,
        )
    )

    # Both writers observe revision 1 before either has written.
    snapshot_a = writer_a.get_address(addr_id)
    snapshot_b = writer_b.get_address(addr_id)
    assert snapshot_a is not None and snapshot_b is not None
    assert snapshot_a.revision == snapshot_b.revision == 1

    common = {
        "reason": "Concurrent correction from an independent engine",
        "actor_id": "operator-race-1",
        "tenant_id": "tenant-race",
        "expected_revision": 1,
    }

    winner_address, winner_correction, _ = writer_a.apply_correction(
        addr_id, updates={"latitude": 25.1111}, **common
    )
    assert winner_address.revision == 2
    assert winner_correction.applied_revision == 2

    # Writer B still holds its revision-1 snapshot: pin the read to it so the
    # in-process pre-check passes and only the database can reject the write.
    writer_b.get_address = lambda _address_id, _snapshot=snapshot_b: _snapshot  # type: ignore[method-assign]

    with pytest.raises(StaleRevisionError):
        writer_b.apply_correction(addr_id, updates={"latitude": 25.2222}, **common)

    # The winner's write survives intact, and the loser left no partial trail.
    verifier = build_repo()
    final = verifier.get_address(addr_id)
    assert final is not None
    assert final.revision == 2
    assert final.latitude == 25.1111
    assert final.manual_override_flag is True

    corrections = verifier.get_corrections(addr_id)
    assert len(corrections) == 1
    assert corrections[0].correction_id == winner_correction.correction_id
    assert corrections[0].applied_revision == 2


def test_durable_rollback_rejects_stale_write_from_concurrent_engine(
    tmp_path: Path,
) -> None:
    """Regression test (P1): a raced rollback must not rewind the winner.

    Rollback shares the read-then-write shape of ``apply_correction``. The
    top-of-stack rule rejects most stale rollbacks, but it is evaluated against
    a correction list read before the write; a rollback that read the whole
    world at revision 2 and only then lost the race would still restore its
    snapshot over the revision-3 winner. The conditional UPDATE is what makes
    that impossible, so both stale reads are pinned here to isolate it.
    """
    db_file = tmp_path / "durable_rollback_race.sqlite3"

    def build_repo() -> DurableAddressLocationRepository:
        engine = SqliteEngine(db_file)
        return DurableAddressLocationRepository(
            engine, correction_repo=DurableManualCorrectionRepository(engine)
        )

    writer_a = build_repo()
    writer_b = build_repo()
    engine_b = SqliteEngine(db_file)
    corr_repo_b = DurableManualCorrectionRepository(engine_b)

    addr_id = str(uuid4())
    writer_a.save_address(
        AddressLocation(
            address_id=addr_id,
            raw_address="Taipei Rollback Race Road 1",
            latitude=25.0000,
            longitude=121.0000,
            manual_override_flag=False,
            tenant_id="tenant-race",
            revision=1,
        )
    )

    _, first_correction, _ = writer_a.apply_correction(
        addr_id,
        updates={"latitude": 25.1111},
        reason="First correction before the rollback race",
        actor_id="operator-race-1",
        tenant_id="tenant-race",
        expected_revision=1,
    )
    assert first_correction.applied_revision == 2

    # Writer B reads the address and the correction stack at revision 2 and
    # decides to roll the (then top-of-stack) first correction back.
    stale_address = writer_b.get_address(addr_id)
    assert stale_address is not None
    assert stale_address.revision == 2
    stale_corrections = corr_repo_b.list_corrections(
        entity_type="address_location", entity_id=addr_id
    )
    assert [c.correction_id for c in stale_corrections] == [first_correction.correction_id]

    # Writer A lands another correction first, moving the row to revision 3.
    second_address, _, _ = writer_a.apply_correction(
        addr_id,
        updates={"latitude": 25.3333},
        reason="Second correction that wins the rollback race",
        actor_id="operator-race-2",
        tenant_id="tenant-race",
        expected_revision=2,
    )
    assert second_address.revision == 3

    class _FrozenCorrectionView:
        """Writer B's pre-race view: writes stay live, reads stay stale."""

        def get_correction(self, correction_id: str):
            return corr_repo_b.get_correction(correction_id)

        def list_corrections(self, **_kwargs):
            return list(stale_corrections)

        def record_correction(self, *args, **kwargs):
            return corr_repo_b.record_correction(*args, **kwargs)

    writer_b.get_address = lambda _address_id, _snapshot=stale_address: _snapshot  # type: ignore[method-assign]

    with pytest.raises(StaleRevisionError):
        writer_b.rollback_correction(
            addr_id,
            first_correction.correction_id,
            reason="Rollback racing a correction that already landed",
            actor_id="operator-race-3",
            tenant_id="tenant-race",
            expected_revision=2,
            correction_repo=_FrozenCorrectionView(),
        )

    verifier = build_repo()
    final = verifier.get_address(addr_id)
    assert final is not None
    assert final.revision == 3
    assert final.latitude == 25.3333
    assert final.manual_override_flag is True

    # The raced rollback must not have flipped the correction's status either.
    corrections = {c.correction_id: c for c in verifier.get_corrections(addr_id)}
    assert corrections[first_correction.correction_id].status == "applied"


def test_durable_correction_concurrent_writers_produce_one_winner(
    tmp_path: Path,
) -> None:
    """Two threads on independent engines: exactly one may take revision 2."""
    db_file = tmp_path / "durable_correction_threads.sqlite3"

    def build_repo() -> DurableAddressLocationRepository:
        engine = SqliteEngine(db_file)
        return DurableAddressLocationRepository(
            engine, correction_repo=DurableManualCorrectionRepository(engine)
        )

    seeder = build_repo()
    addr_id = str(uuid4())
    seeder.save_address(
        AddressLocation(
            address_id=addr_id,
            raw_address="Taipei Thread Race Road 1",
            latitude=25.0000,
            longitude=121.0000,
            manual_override_flag=False,
            tenant_id="tenant-race",
            revision=1,
        )
    )

    repos = [build_repo(), build_repo()]
    barrier = threading.Barrier(len(repos))
    outcomes: list[object] = [None] * len(repos)

    def attempt(index: int) -> None:
        repo = repos[index]
        barrier.wait(timeout=10)
        try:
            address, _, _ = repo.apply_correction(
                addr_id,
                updates={"latitude": 25.0 + index + 1},
                reason=f"Concurrent thread {index} correction attempt",
                actor_id=f"operator-thread-{index}",
                tenant_id="tenant-race",
                expected_revision=1,
            )
            outcomes[index] = address.revision
        except StaleRevisionError as exc:
            outcomes[index] = exc

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(len(repos))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    winners = [o for o in outcomes if o == 2]
    losers = [o for o in outcomes if isinstance(o, StaleRevisionError)]
    assert len(winners) == 1, outcomes
    assert len(losers) == 1, outcomes

    verifier = build_repo()
    final = verifier.get_address(addr_id)
    assert final is not None
    assert final.revision == 2
    assert len(verifier.get_corrections(addr_id)) == 1


def test_durable_correction_compensates_when_audit_write_fails(tmp_path: Path) -> None:
    """Regression test: a correction whose audit trail fails must not stay applied.

    The revision is claimed before the audit and correction records are written,
    so the losing writer of a race leaves no trail. The same ordering means a
    failing audit write would otherwise leave the row moved and unauditable.
    The failure is injected, because a write path that never fails is a write
    path whose compensation is never exercised.
    """
    db_file = tmp_path / "durable_correction_compensation.sqlite3"
    engine = SqliteEngine(db_file)
    corr_repo = DurableManualCorrectionRepository(engine)
    repo = DurableAddressLocationRepository(engine, correction_repo=corr_repo)

    addr_id = str(uuid4())
    repo.save_address(
        AddressLocation(
            address_id=addr_id,
            raw_address="Taipei Compensation Road 1",
            latitude=25.0000,
            longitude=121.0000,
            geocode_confidence=0.5,
            manual_override_flag=False,
            tenant_id="tenant-compensate",
            revision=1,
        )
    )

    class _FailingAuditLog:
        def record(self, _event: object) -> None:
            raise RuntimeError("audit sink unavailable")

    with pytest.raises(RuntimeError, match="audit sink unavailable"):
        repo.apply_correction(
            addr_id,
            updates={"latitude": 25.9999, "longitude": 121.9999},
            reason="Correction whose audit write is going to fail",
            actor_id="operator-compensate-1",
            tenant_id="tenant-compensate",
            expected_revision=1,
            audit_log=_FailingAuditLog(),
        )

    # The row must be back at its pre-correction state, on disk.
    verifier_engine = SqliteEngine(db_file)
    verifier = DurableAddressLocationRepository(
        verifier_engine, correction_repo=DurableManualCorrectionRepository(verifier_engine)
    )
    restored = verifier.get_address(addr_id)
    assert restored is not None
    assert restored.revision == 1
    assert restored.latitude == 25.0000
    assert restored.longitude == 121.0000
    assert restored.manual_override_flag is False
    assert verifier.get_corrections(addr_id) == []

    # And the address is still correctable afterwards at the original revision.
    applied, correction, _ = verifier.apply_correction(
        addr_id,
        updates={"latitude": 25.5555},
        reason="Correction retried after the audit sink recovered",
        actor_id="operator-compensate-2",
        tenant_id="tenant-compensate",
        expected_revision=1,
    )
    assert applied.revision == 2
    assert correction.source_revision == 1


def test_durable_rollback_compensates_when_audit_write_fails(tmp_path: Path) -> None:
    """Regression test: a rollback whose audit trail fails must compensate back to applied state."""
    db_file = tmp_path / "durable_rollback_compensation.sqlite3"
    engine = SqliteEngine(db_file)
    corr_repo = DurableManualCorrectionRepository(engine)
    audit_log = DurableAuditLog(engine)
    repo = DurableAddressLocationRepository(engine, correction_repo=corr_repo, audit_log=audit_log)

    addr_id = str(uuid4())
    repo.save_address(
        AddressLocation(
            address_id=addr_id,
            raw_address="Taipei Rollback Road 1",
            latitude=25.0000,
            longitude=121.0000,
            geocode_confidence=0.5,
            manual_override_flag=False,
            tenant_id="tenant-rollback-comp",
            revision=1,
        )
    )

    # 1. Apply correction successfully
    applied, correction, _ = repo.apply_correction(
        addr_id,
        updates={"latitude": 25.5555},
        reason="Initial valid correction before rollback test",
        actor_id="operator-1",
        tenant_id="tenant-rollback-comp",
        expected_revision=1,
    )
    assert applied.revision == 2
    assert correction.status == "applied"

    class _FailingAuditLog:
        def record(self, _event: object) -> None:
            raise RuntimeError("audit log unavailable during rollback")

    # 2. Attempt rollback with failing audit log
    with pytest.raises(RuntimeError, match="audit log unavailable during rollback"):
        repo.rollback_correction(
            addr_id,
            correction.correction_id,
            reason="Rollback with audit failure",
            actor_id="operator-rollback-fail",
            tenant_id="tenant-rollback-comp",
            expected_revision=2,
            audit_log=_FailingAuditLog(),
        )

    # 3. Verify on disk that address is back at revision 2 and correction is still 'applied'
    verifier_engine = SqliteEngine(db_file)
    verifier_corr_repo = DurableManualCorrectionRepository(verifier_engine)
    verifier_audit = DurableAuditLog(verifier_engine)
    verifier = DurableAddressLocationRepository(
        verifier_engine, correction_repo=verifier_corr_repo, audit_log=verifier_audit
    )

    restored = verifier.get_address(addr_id)
    assert restored is not None
    assert restored.revision == 2
    assert restored.latitude == 25.5555
    assert restored.manual_override_flag is True

    stored_corrections = verifier.get_corrections(addr_id)
    assert len(stored_corrections) == 1
    assert stored_corrections[0].status == "applied"

    # 4. Rollback succeeds after audit recovers
    restored_final, rolled_back, _ = verifier.rollback_correction(
        addr_id,
        correction.correction_id,
        reason="Rollback retried after audit sink restored",
        actor_id="operator-2",
        tenant_id="tenant-rollback-comp",
        expected_revision=2,
    )
    assert restored_final.revision == 3
    assert restored_final.latitude == 25.0000
    assert restored_final.manual_override_flag is False
    assert rolled_back.status == "rolled_back"


def test_in_memory_compensation_when_audit_fails() -> None:
    """Verify that InMemoryAddressLocationRepository compensates on audit log failure."""
    from shared.infrastructure.persistence.repositories import (
        InMemoryAddressLocationRepository,
        InMemoryManualCorrectionRepository,
    )

    corr_repo = InMemoryManualCorrectionRepository()
    repo = InMemoryAddressLocationRepository(_corrections=corr_repo)

    addr_id = str(uuid4())
    repo.save_address(
        AddressLocation(
            address_id=addr_id,
            raw_address="Taipei Memory Compensation",
            latitude=25.0,
            longitude=121.0,
            manual_override_flag=False,
            tenant_id="tenant-mem",
            revision=1,
        )
    )

    class _FailingAuditLog:
        def record(self, _event: object) -> None:
            raise RuntimeError("sink unavailable")

    # 1. Failing apply_correction
    with pytest.raises(RuntimeError, match="sink unavailable"):
        repo.apply_correction(
            addr_id,
            updates={"latitude": 25.9},
            reason="Failed apply correction",
            actor_id="mem-op-1",
            tenant_id="tenant-mem",
            expected_revision=1,
            audit_log=_FailingAuditLog(),
        )

    # Address should be restored and correction deleted
    saved = repo.get_address(addr_id)
    assert saved is not None
    assert saved.revision == 1
    assert saved.latitude == 25.0
    assert saved.manual_override_flag is False
    assert repo.get_corrections(addr_id) == []

    # 2. Valid apply_correction
    applied, correction, _ = repo.apply_correction(
        addr_id,
        updates={"latitude": 25.9},
        reason="Valid apply correction",
        actor_id="mem-op-1",
        tenant_id="tenant-mem",
        expected_revision=1,
    )
    assert applied.revision == 2
    assert correction.status == "applied"

    # 3. Failing rollback_correction
    with pytest.raises(RuntimeError, match="sink unavailable"):
        repo.rollback_correction(
            addr_id,
            correction.correction_id,
            reason="Failed rollback correction",
            actor_id="mem-op-2",
            tenant_id="tenant-mem",
            expected_revision=2,
            audit_log=_FailingAuditLog(),
        )

    # Address should be at revision 2 and correction status restored to applied
    saved_after_rb_fail = repo.get_address(addr_id)
    assert saved_after_rb_fail is not None
    assert saved_after_rb_fail.revision == 2
    assert saved_after_rb_fail.latitude == 25.9
    assert saved_after_rb_fail.manual_override_flag is True
    assert repo.get_corrections(addr_id)[0].status == "applied"
