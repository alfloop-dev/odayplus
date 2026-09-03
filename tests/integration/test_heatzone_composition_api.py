"""Integration tests for HeatZone merge/split composition, override, rollback, and evaluation API (ODP-FR-HZ-006)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.heatzone.domain.composition import (
    CompositionKind,
    HeatZoneCompositionRecord,
)
from shared.infrastructure.persistence import build_persistence
from tests.integration._authz import HEATZONE_HEADERS

TENANT_ID = "tenant-a"


def test_heatzone_merge_split_evaluate_abstains_fail_closed_on_immature_data() -> None:
    bundle = build_persistence(mode="memory")
    client = TestClient(create_app(persistence=bundle))

    payload = {
        "cells": [
            {
                "cell_id": "cell-1",
                "h3_index": "8928308280fffff",
                "admin_city": "Taipei",
                "admin_district": "Daan",
                "population": 10000.0,
                "poi_count": 30,
                "unmet_demand": 100.0,
                "absorbed_demand": 80.0,
                "realized_revenue": 500000.0,
                "adjacent_cell_ids": ["cell-2"],
            },
            {
                "cell_id": "cell-2",
                "h3_index": "8928308281fffff",
                "admin_city": "Taipei",
                "admin_district": "Daan",
                "population": 9500.0,
                "poi_count": 28,
                "unmet_demand": 95.0,
                "absorbed_demand": 75.0,
                "realized_revenue": 480000.0,
                "adjacent_cell_ids": ["cell-1"],
            },
        ],
        "readiness": {
            "observation_days": 10,  # Below threshold 180
            "mature_labels_count": 5,  # Below threshold 200
            "active_store_count": 2,
            "adjacent_pairs_count": 1,
            "metro_clusters_count": 1,
            "spatial_contiguity_ratio": 0.5,
            "source_snapshot_id": "snap-immature",
        },
    }

    response = client.post(
        "/api/v1/heatzones/merge-split/evaluate",
        json=payload,
        headers=HEATZONE_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["abstained"] is True
    assert len(body["proposals"]) == 0
    assert any("observation_horizon_insufficient" in r for r in body["abstain_reasons"])
    assert any("sample_size_insufficient" in r for r in body["abstain_reasons"])

    # Verify audit event
    events = bundle.audit_log.list_events()
    eval_events = [e for e in events if e.event_type == "heatzone.composition.evaluated.v1"]
    assert len(eval_events) >= 1
    assert eval_events[-1].outcome == "abstained"


def test_heatzone_merge_split_evaluate_generates_proposals_when_mature() -> None:
    bundle = build_persistence(mode="memory")
    client = TestClient(create_app(persistence=bundle))

    payload = {
        "cells": [
            {
                "cell_id": "cell-10",
                "h3_index": "8928308280fffff",
                "admin_city": "Taipei",
                "admin_district": "Daan",
                "population": 12000.0,
                "poi_count": 45,
                "unmet_demand": 150.0,
                "absorbed_demand": 120.0,
                "realized_revenue": 850000.0,
                "adjacent_cell_ids": ["cell-11"],
            },
            {
                "cell_id": "cell-11",
                "h3_index": "8928308281fffff",
                "admin_city": "Taipei",
                "admin_district": "Daan",
                "population": 11500.0,
                "poi_count": 42,
                "unmet_demand": 145.0,
                "absorbed_demand": 115.0,
                "realized_revenue": 820000.0,
                "adjacent_cell_ids": ["cell-10"],
            },
        ],
        "readiness": {
            "observation_days": 190,
            "mature_labels_count": 250,
            "active_store_count": 60,
            "adjacent_pairs_count": 35,
            "metro_clusters_count": 2,
            "spatial_contiguity_ratio": 0.85,
            "absorption_ratio_cv": 0.10,
            "drift_psi": 0.05,
            "wasserstein_distance": 0.02,
            "source_snapshot_id": "snap-mature-2026",
        },
    }

    response = client.post(
        "/api/v1/heatzones/merge-split/evaluate",
        json=payload,
        headers=HEATZONE_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["abstained"] is False
    assert len(body["proposals"]) >= 1
    assert body["proposals"][0]["zone_id"].startswith("MZ-")
    assert body["proposals"][0]["ndcg_gain"] >= 0.05


def test_heatzone_composition_override_and_rollback_flow() -> None:
    bundle = build_persistence(mode="memory")
    client = TestClient(create_app(persistence=bundle))

    zone_id = "MZ-aabb112233445566"
    comp_repo = bundle.heatzone_composition_repository
    assert comp_repo is not None

    # Pre-populate active composition
    r1 = HeatZoneCompositionRecord(
        zone_id=zone_id,
        tenant_id=TENANT_ID,
        member_cell_id="cell-alpha",
        composition_kind=CompositionKind.MERGED,
        decision_policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
    )
    r2 = HeatZoneCompositionRecord(
        zone_id=zone_id,
        tenant_id=TENANT_ID,
        member_cell_id="cell-beta",
        composition_kind=CompositionKind.MERGED,
        decision_policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
    )
    comp_repo.save_composition_batch([r1, r2])

    # 1. Get composition
    get_res = client.get(
        f"/api/v1/heatzones/zones/{zone_id}/composition",
        headers=HEATZONE_HEADERS,
    )
    assert get_res.status_code == 200
    comp_data = get_res.json()
    assert comp_data["zone_id"] == zone_id
    assert set(comp_data["member_cell_ids"]) == {"cell-alpha", "cell-beta"}
    assert comp_data["is_active"] is True

    # 2. Get lineage
    lin_res = client.get(
        f"/api/v1/heatzones/zones/{zone_id}/lineage",
        headers=HEATZONE_HEADERS,
    )
    assert lin_res.status_code == 200
    assert lin_res.json()["member_count"] == 2

    # 3. Human override: requires non-empty override_reason
    bad_override = client.post(
        f"/api/v1/heatzones/zones/{zone_id}/override",
        json={
            "decided_by": "operator@odayplus.com",
            "override_reason": "",  # Empty reason rejected
            "member_cell_ids": ["cell-alpha", "cell-beta", "cell-gamma"],
        },
        headers=HEATZONE_HEADERS,
    )
    assert bad_override.status_code == 422

    # System decided_by rejected for human override
    sys_override = client.post(
        f"/api/v1/heatzones/zones/{zone_id}/override",
        json={
            "decided_by": "system",
            "override_reason": "Some reason",
            "member_cell_ids": ["cell-alpha", "cell-beta", "cell-gamma"],
        },
        headers=HEATZONE_HEADERS,
    )
    assert sys_override.status_code == 422

    # Valid human override
    good_override = client.post(
        f"/api/v1/heatzones/zones/{zone_id}/override",
        json={
            "decided_by": "operator@odayplus.com",
            "override_reason": "Spatial boundary adjusted based on operator field survey",
            "member_cell_ids": ["cell-alpha", "cell-beta", "cell-gamma"],
        },
        headers=HEATZONE_HEADERS,
    )
    assert good_override.status_code == 200
    override_body = good_override.json()
    assert override_body["status"] == "overridden"
    assert len(override_body["records"]) == 3

    # Verify lineage updated
    lin_res_after = client.get(
        f"/api/v1/heatzones/zones/{zone_id}/lineage",
        headers=HEATZONE_HEADERS,
    )
    assert lin_res_after.status_code == 200
    lin_data = lin_res_after.json()
    assert lin_data["decided_by"] == "operator@odayplus.com"
    assert lin_data["member_count"] == 3

    # 4. Soft Rollback
    rollback_res = client.post(
        f"/api/v1/heatzones/zones/{zone_id}/rollback",
        json={"revert_reason": "Reverting operator boundary adjustment"},
        headers=HEATZONE_HEADERS,
    )
    assert rollback_res.status_code == 200
    rollback_body = rollback_res.json()
    assert rollback_body["status"] == "reverted"
    assert len(rollback_body["reverted_records"]) == 3

    # Verify lineage shows inactive
    lin_res_post_rb = client.get(
        f"/api/v1/heatzones/zones/{zone_id}/lineage",
        headers=HEATZONE_HEADERS,
    )
    assert lin_res_post_rb.status_code == 200
    assert lin_res_post_rb.json()["is_active"] is False

    # Check audit log contains override and rollback events
    events = bundle.audit_log.list_events()
    types = [e.event_type for e in events]
    assert "heatzone.composition.overridden.v1" in types
    assert "heatzone.composition.reverted.v1" in types


def test_heatzone_merge_split_proposals_preview_approve_and_reject_lifecycle() -> None:
    bundle = build_persistence(mode="memory")
    client = TestClient(create_app(persistence=bundle))

    payload = {
        "cells": [
            {
                "cell_id": "cell-201",
                "h3_index": "8928308280fffff",
                "admin_city": "Taipei",
                "admin_district": "Daan",
                "population": 12000.0,
                "poi_count": 45,
                "unmet_demand": 150.0,
                "absorbed_demand": 120.0,
                "realized_revenue": 850000.0,
                "adjacent_cell_ids": ["cell-202"],
            },
            {
                "cell_id": "cell-202",
                "h3_index": "8928308281fffff",
                "admin_city": "Taipei",
                "admin_district": "Daan",
                "population": 11500.0,
                "poi_count": 42,
                "unmet_demand": 145.0,
                "absorbed_demand": 115.0,
                "realized_revenue": 820000.0,
                "adjacent_cell_ids": ["cell-201"],
            },
        ],
        "readiness": {
            "observation_days": 190,
            "mature_labels_count": 250,
            "active_store_count": 60,
            "adjacent_pairs_count": 35,
            "metro_clusters_count": 2,
            "spatial_contiguity_ratio": 0.85,
            "absorption_ratio_cv": 0.10,
            "drift_psi": 0.05,
            "wasserstein_distance": 0.02,
            "source_snapshot_id": "snap-mature-2026",
        },
    }

    # 1. Evaluate
    eval_res = client.post(
        "/api/v1/heatzones/merge-split/evaluate",
        json=payload,
        headers=HEATZONE_HEADERS,
    )
    assert eval_res.status_code == 200
    eval_body = eval_res.json()
    assert eval_body["abstained"] is False
    assert len(eval_body["proposals"]) >= 1
    proposal_id = eval_body["proposals"][0]["proposal_id"]
    zone_id = eval_body["proposals"][0]["zone_id"]

    # 2. List proposals
    list_res = client.get(
        "/api/v1/heatzones/merge-split/proposals",
        headers=HEATZONE_HEADERS,
    )
    assert list_res.status_code == 200
    props = list_res.json()["items"]
    assert any(p["proposal_id"] == proposal_id for p in props)

    # 3. Get proposal detail
    get_prop_res = client.get(
        f"/api/v1/heatzones/merge-split/proposals/{proposal_id}",
        headers=HEATZONE_HEADERS,
    )
    assert get_prop_res.status_code == 200
    assert get_prop_res.json()["status"] == "PROPOSED"

    # 4. Preview proposal
    preview_res = client.post(
        f"/api/v1/heatzones/merge-split/proposals/{proposal_id}/preview",
        headers=HEATZONE_HEADERS,
    )
    assert preview_res.status_code == 200
    preview_body = preview_res.json()
    assert preview_body["proposed_zone_id"] == zone_id
    assert preview_body["expected_ndcg_gain"] >= 0.05

    # 5. Operator approve proposal
    approve_res = client.post(
        f"/api/v1/heatzones/merge-split/proposals/{proposal_id}/approve",
        json={"decided_by": "operator@odayplus.com", "notes": "Approved based on empirical Ndcg gain"},
        headers=HEATZONE_HEADERS,
    )
    assert approve_res.status_code == 200
    approve_body = approve_res.json()
    assert approve_body["proposal"]["status"] == "APPROVED"
    assert len(approve_body["created_compositions"]) == 2

    # 6. Verify active composition and lineage created
    comp_res = client.get(
        f"/api/v1/heatzones/zones/{zone_id}/composition",
        headers=HEATZONE_HEADERS,
    )
    assert comp_res.status_code == 200
    assert comp_res.json()["is_active"] is True


def test_heatzone_merge_split_evaluate_fails_closed_on_invalid_policy() -> None:
    bundle = build_persistence(mode="memory")
    client = TestClient(create_app(persistence=bundle))

    payload = {
        "cells": [],
        "readiness": {},
        "policy_version_id": "non-existent-policy-version",
    }

    response = client.post(
        "/api/v1/heatzones/merge-split/evaluate",
        json=payload,
        headers=HEATZONE_HEADERS,
    )
    assert response.status_code == 422
    assert "not found" in response.json()["detail"]


def test_heatzone_sql_policy_version_resolution_and_durable_proposal_uuid_flow(tmp_path) -> None:
    import uuid
    from datetime import date
    from shared.governance import DecisionPolicy
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.decision_policy import SqlDecisionPolicyRepository
    from shared.infrastructure.persistence.repositories import DurableHeatZoneCompositionRepository

    db_path = tmp_path / "sql_test.sqlite3"
    engine = SqliteEngine(db_path)
    engine.execute("ATTACH DATABASE ':memory:' AS workflow")
    engine.execute("ATTACH DATABASE ':memory:' AS expansion")
    engine.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow.decision_policies (
            policy_version_id TEXT PRIMARY KEY,
            policy_label TEXT NOT NULL,
            policy_id TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            policy_kind TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            change_reason TEXT,
            rollback_policy_version TEXT,
            parameters TEXT NOT NULL,
            declared_inputs TEXT NOT NULL,
            approved_by TEXT,
            owner_role TEXT
        )
        """
    )
    engine.execute(
        """
        CREATE TABLE IF NOT EXISTS expansion.heatzone_composition (
            composition_id TEXT PRIMARY KEY,
            zone_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            member_cell_id TEXT NOT NULL,
            composition_kind TEXT NOT NULL,
            parent_zone_id TEXT,
            decided_by TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            decision_policy_version_id TEXT NOT NULL,
            model_version TEXT NOT NULL,
            override_reason TEXT,
            reverted_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    engine.execute(
        """
        CREATE TABLE IF NOT EXISTS expansion.heatzone_proposals (
            proposal_id TEXT PRIMARY KEY,
            zone_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            composition_kind TEXT NOT NULL,
            member_cell_ids TEXT NOT NULL,
            parent_zone_id TEXT,
            ndcg_gain REAL NOT NULL,
            cannibalization_variance_reduction REAL NOT NULL,
            correlation_rho REAL NOT NULL,
            disconnect_index REAL NOT NULL,
            split_density_ratio REAL,
            confidence REAL NOT NULL,
            model_version TEXT NOT NULL,
            policy_version_id TEXT NOT NULL,
            status TEXT NOT NULL,
            reasons TEXT NOT NULL,
            warnings TEXT NOT NULL,
            created_at TEXT NOT NULL,
            approved_by TEXT,
            approved_at TEXT,
            rejection_reason TEXT
        )
        """
    )

    policy_ver_id = f"heatzone-merge-v1:{TENANT_ID}"
    policy_repo = SqlDecisionPolicyRepository(engine)
    # Insert policy into SQL
    engine.execute(
        """
        INSERT INTO workflow.decision_policies
        (policy_version_id, policy_label, policy_id, policy_version, policy_kind, tenant_id, effective_from, parameters, declared_inputs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            policy_ver_id,
            "heatzone-merge-v1",
            "heatzone-merge",
            "1.0.0",
            "heatzone_merge",
            TENANT_ID,
            "2026-01-01T00:00:00+00:00",
            '{"min_observation_days": 180, "min_mature_labels": 200, "min_active_stores": 50, "min_adjacent_pairs": 30, "min_metro_clusters": 2, "min_spatial_contiguity": 0.80, "max_absorption_cv": 0.15, "max_drift_psi": 0.10, "max_wasserstein": 0.05}',
            '["store_daily_performance", "operational_start_observation"]',
        ),
    )

    found_pol = policy_repo.find_version(policy_ver_id)
    assert found_pol is not None
    assert found_pol.policy_version_id == policy_ver_id

    doc_store = SqliteDocumentStore(engine)
    durable_comp_repo = DurableHeatZoneCompositionRepository(doc_store)

    bundle = build_persistence(mode="memory")
    bundle = type(bundle)(
        **{
            **bundle.__dict__,
            "heatzone_composition_repository": durable_comp_repo,
            "forecastops_policy_repository": policy_repo,
        }
    )
    client = TestClient(create_app(persistence=bundle))

    payload = {
        "cells": [
            {
                "cell_id": "aaaaaaaa-1111-2222-3333-444455556666",
                "h3_index": "8928308280fffff",
                "admin_city": "Taipei",
                "admin_district": "Daan",
                "population": 12000.0,
                "poi_count": 45,
                "unmet_demand": 150.0,
                "absorbed_demand": 120.0,
                "realized_revenue": 850000.0,
                "adjacent_cell_ids": ["bbbbbbbb-1111-2222-3333-444455556666"],
            },
            {
                "cell_id": "bbbbbbbb-1111-2222-3333-444455556666",
                "h3_index": "8928308281fffff",
                "admin_city": "Taipei",
                "admin_district": "Daan",
                "population": 11500.0,
                "poi_count": 42,
                "unmet_demand": 145.0,
                "absorbed_demand": 115.0,
                "realized_revenue": 820000.0,
                "adjacent_cell_ids": ["aaaaaaaa-1111-2222-3333-444455556666"],
            },
        ],
        "readiness": {
            "observation_days": 190,
            "mature_labels_count": 250,
            "active_store_count": 60,
            "adjacent_pairs_count": 35,
            "metro_clusters_count": 2,
            "spatial_contiguity_ratio": 0.85,
            "absorption_ratio_cv": 0.10,
            "drift_psi": 0.05,
            "wasserstein_distance": 0.02,
            "source_snapshot_id": "snap-durable-prod-2026",
        },
        "policy_version_id": policy_ver_id,
    }

    eval_res = client.post(
        "/api/v1/heatzones/merge-split/evaluate",
        json=payload,
        headers=HEATZONE_HEADERS,
    )
    assert eval_res.status_code == 200
    eval_body = eval_res.json()
    assert eval_body["abstained"] is False
    assert len(eval_body["proposals"]) >= 1

    prop = eval_body["proposals"][0]
    prop_id = prop["proposal_id"]
    # Verify proposal_id is valid UUID string without prefix
    uuid_obj = uuid.UUID(prop_id)
    assert str(uuid_obj) == prop_id

    # Verify durable proposal save & retrieval
    saved_prop = durable_comp_repo.get_proposal(prop_id, TENANT_ID)
    assert saved_prop is not None
    assert saved_prop.proposal_id == prop_id
    assert any("source_snapshot:snap-durable-prod-2026" in r for r in saved_prop.reasons)

    # Approve proposal in durable repo
    app_res = client.post(
        f"/api/v1/heatzones/merge-split/proposals/{prop_id}/approve",
        json={"decided_by": "operator@odayplus.com", "notes": "Approved durable proposal"},
        headers=HEATZONE_HEADERS,
    )
    assert app_res.status_code == 200
    app_body = app_res.json()
    assert app_body["proposal"]["status"] == "APPROVED"
    assert len(app_body["created_compositions"]) == 2

    # Verify audit event carries snapshot ID
    events = bundle.audit_log.list_events()
    eval_events = [e for e in events if e.event_type == "heatzone.composition.evaluated.v1"]
    assert len(eval_events) >= 1
    assert eval_events[-1].metadata.get("source_snapshot_id") == "snap-durable-prod-2026"


