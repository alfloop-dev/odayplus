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
