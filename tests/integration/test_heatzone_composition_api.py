"""Production-entry tests for HeatZone merge/split (ODP-FR-HZ-006).

These drive the mounted API through `create_app`, so what they exercise is the
path a real Operator request takes: the router the application wires, the
persistence bundle it was built with, and the authorization dependency that
establishes who is asking.

Two properties are load-bearing here and are asserted rather than assumed:
a request cannot supply the evidence it is judged against, and it cannot supply
the identity that gets written into the governance record.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.api.oday_api.routes.heatzone import create_heatzone_router
from modules.external_data.application.market_data_facade import MarketDataFacade
from modules.external_data.infrastructure.data_platform_client import (
    InMemoryDataPlatformTransport,
)
from modules.heatzone.application.merge_split_evidence import AbsorptionOutcomeRecord
from modules.heatzone.domain.composition import (
    COMPOSITION_MODEL_VERSION,
    CompositionKind,
    HeatZoneCompositionRecord,
    MergeSplitProposalRecord,
    generate_merged_zone_id,
)
from modules.heatzone.infrastructure import CellRegistration
from shared.auth import Role
from shared.governance import (
    DecisionPolicy,
    default_heatzone_merge_policy,
    default_model_performance_drift_policy,
)
from shared.infrastructure.persistence import build_persistence
from tests.integration._authz import HEATZONE_HEADERS, auth_headers
from tests.integration._heatzone_absorption_rows import (
    operational_start_rows,
    outcome_request,
    performance_rows,
)
from tests.integration._heatzone_evidence import (
    ABSORPTION_POLICY_ID,
    MERGE_LEFT,
    MERGE_RIGHT,
    SPLIT_LEFT,
    SPLIT_RIGHT,
    build_evidence_repository,
    populate_evidence_repository,
    use_matured_receipt,
)

TENANT_ID = "tenant-a"

#: `auth_headers` signs the request as this subject; it is the identity the
#: server must record, whatever the request body says.
AUTHENTICATED_SUBJECT = "test-operator"


def _bundle_with_evidence():
    bundle = build_persistence(mode="memory")
    populate_evidence_repository(
        bundle.heatzone_evidence_repository, tenant_id=TENANT_ID
    )
    return bundle


def _evaluate(client: TestClient, **body: object):
    return client.post(
        "/api/v1/heatzones/merge-split/evaluate",
        json=body,
        headers=HEATZONE_HEADERS,
    )


def test_evaluate_abstains_on_the_release_bound_production_snapshot() -> None:
    """Six months of real history is not enough while the contract is immature."""
    bundle = _bundle_with_evidence()
    client = TestClient(create_app(persistence=bundle))

    response = _evaluate(client)

    assert response.status_code == 200
    body = response.json()
    assert body["abstained"] is True
    assert body["proposals"] == []
    assert "governed_disabled_by_data_contract_maturity" in body["abstain_reasons"]
    assert any(
        "sample_size_insufficient" in reason for reason in body["abstain_reasons"]
    )
    assert body["readiness"]["metrics"]["mature_labels_count"] == 0

    events = bundle.audit_log.list_events()
    evaluated = [
        event
        for event in events
        if event.event_type == "heatzone.composition.evaluated.v1"
    ]
    assert evaluated[-1].outcome == "abstained"
    assert evaluated[-1].metadata["governed_disabled"] is True
    assert evaluated[-1].metadata["source_snapshot_sha256"]


def test_evaluate_refuses_a_request_that_supplies_its_own_readiness() -> None:
    """The 2026-09 probe: a caller naming its own maturity is now rejected."""
    client = TestClient(create_app(persistence=_bundle_with_evidence()))

    response = _evaluate(
        client,
        readiness={
            "observation_days": 400,
            "mature_labels_count": 5_000,
            "active_store_count": 900,
            "adjacent_pairs_count": 90,
            "metro_clusters_count": 4,
            "spatial_contiguity_ratio": 0.99,
            "absorption_ratio_cv": 0.01,
            "drift_psi": 0.01,
            "wasserstein_distance": 0.01,
            "source_snapshot_id": "snap-fake-not-registered",
        },
    )

    assert response.status_code == 422
    assert "readiness" in response.text


def test_evaluate_refuses_a_request_that_supplies_its_own_cell_outcomes() -> None:
    client = TestClient(create_app(persistence=_bundle_with_evidence()))

    response = _evaluate(
        client,
        cells=[
            {
                "cell_id": "cell-1",
                "absorbed_demand": 99_999.0,
                "adjacent_cell_ids": ["cell-2"],
            }
        ],
    )

    assert response.status_code == 422
    assert "cells" in response.text


def test_evaluate_fails_closed_when_no_evidence_repository_is_wired() -> None:
    """An unwired reader reads the same as "no history"; refusing is the safe one."""
    base = build_persistence(mode="memory")
    bundle = type(base)(**{**base.__dict__, "heatzone_evidence_repository": None})
    client = TestClient(create_app(persistence=bundle))

    response = _evaluate(client)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "HZ006_EVIDENCE_UNAVAILABLE"


def test_evaluate_fails_closed_on_an_unresolvable_policy_version() -> None:
    client = TestClient(create_app(persistence=_bundle_with_evidence()))

    response = _evaluate(client, policy_version_id="non-existent-policy-version")

    assert response.status_code == 422
    assert "not found" in response.json()["detail"]


def test_evaluate_proposes_once_the_production_contract_matures(
    monkeypatch, tmp_path
) -> None:
    bundle = _bundle_with_evidence()
    use_matured_receipt(monkeypatch, tmp_path)
    client = TestClient(create_app(persistence=bundle))

    response = _evaluate(client)

    assert response.status_code == 200
    body = response.json()
    assert body["abstained"] is False
    merges = [
        proposal
        for proposal in body["proposals"]
        if proposal["composition_kind"] == "MERGED"
    ]
    assert len(merges) == 1
    assert merges[0]["member_cell_ids"] == [MERGE_LEFT, MERGE_RIGHT]
    assert merges[0]["ndcg_gain"] >= 0.05
    assert merges[0]["cannibalization_variance_reduction"] >= 0.20
    assert uuid.UUID(merges[0]["proposal_id"])

    # Adjacency alone proposes nothing: every other neighbour was declined.
    assert len(body["declined_candidates"]) == 29

    listed = client.get(
        "/api/v1/heatzones/merge-split/proposals", headers=HEATZONE_HEADERS
    )
    assert listed.status_code == 200
    assert [p["proposal_id"] for p in listed.json()["items"]] == [
        merges[0]["proposal_id"]
    ]


def test_approval_records_the_authenticated_operator_not_the_request_body(
    monkeypatch, tmp_path
) -> None:
    """Identity comes from the credential; a body claiming otherwise is refused."""
    bundle = _bundle_with_evidence()
    use_matured_receipt(monkeypatch, tmp_path)
    client = TestClient(create_app(persistence=bundle))

    proposal = _evaluate(client).json()["proposals"][0]
    proposal_id = proposal["proposal_id"]

    spoofed = client.post(
        f"/api/v1/heatzones/merge-split/proposals/{proposal_id}/approve",
        json={"decided_by": "someone.else@odayplus.com", "notes": "not mine to sign"},
        headers=HEATZONE_HEADERS,
    )
    assert spoofed.status_code == 422
    assert "decided_by" in spoofed.text

    approved = client.post(
        f"/api/v1/heatzones/merge-split/proposals/{proposal_id}/approve",
        json={"notes": "Approved on the measured counterfactual gain"},
        headers=HEATZONE_HEADERS,
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["proposal"]["approved_by"] == AUTHENTICATED_SUBJECT
    assert len(body["created_compositions"]) == 2
    for record in body["created_compositions"]:
        assert record["decided_by"] == AUTHENTICATED_SUBJECT
        assert record["model_version"] == proposal["model_version"]
        assert record["decision_policy_version_id"] == proposal["policy_version_id"]

    approval_events = [
        event
        for event in bundle.audit_log.list_events()
        if event.event_type == "heatzone.composition.proposal.approved.v1"
    ]
    assert approval_events[-1].actor == AUTHENTICATED_SUBJECT


def test_rejection_records_the_authenticated_operator(monkeypatch, tmp_path) -> None:
    bundle = _bundle_with_evidence()
    use_matured_receipt(monkeypatch, tmp_path)
    client = TestClient(create_app(persistence=bundle))

    proposal_id = _evaluate(client).json()["proposals"][0]["proposal_id"]

    spoofed = client.post(
        f"/api/v1/heatzones/merge-split/proposals/{proposal_id}/reject",
        json={"rejected_by": "someone.else@odayplus.com", "reason": "field survey"},
        headers=HEATZONE_HEADERS,
    )
    assert spoofed.status_code == 422

    rejected = client.post(
        f"/api/v1/heatzones/merge-split/proposals/{proposal_id}/reject",
        json={"reason": "Field survey found a service road between the cells"},
        headers=HEATZONE_HEADERS,
    )
    assert rejected.status_code == 200
    assert rejected.json()["proposal"]["status"] == "REJECTED"
    assert rejected.json()["proposal"]["approved_by"] == AUTHENTICATED_SUBJECT


def test_preview_approve_and_rollback_round_trip(monkeypatch, tmp_path) -> None:
    """The whole reversible path: preview, approve, read lineage, roll back."""
    bundle = _bundle_with_evidence()
    use_matured_receipt(monkeypatch, tmp_path)
    client = TestClient(create_app(persistence=bundle))

    proposal = _evaluate(client).json()["proposals"][0]
    proposal_id = proposal["proposal_id"]
    zone_id = proposal["zone_id"]

    preview = client.post(
        f"/api/v1/heatzones/merge-split/proposals/{proposal_id}/preview",
        headers=HEATZONE_HEADERS,
    )
    assert preview.status_code == 200
    preview_body = preview.json()
    assert preview_body["proposed_zone_id"] == zone_id
    assert preview_body["current_active_compositions"] == []
    assert preview_body["expected_ndcg_gain"] == proposal["ndcg_gain"]

    approved = client.post(
        f"/api/v1/heatzones/merge-split/proposals/{proposal_id}/approve",
        json={"notes": "Approved"},
        headers=HEATZONE_HEADERS,
    )
    assert approved.status_code == 200

    lineage = client.get(
        f"/api/v1/heatzones/zones/{zone_id}/lineage", headers=HEATZONE_HEADERS
    )
    assert lineage.status_code == 200
    lineage_body = lineage.json()
    assert lineage_body["is_active"] is True
    assert lineage_body["member_cell_ids"] == [MERGE_LEFT, MERGE_RIGHT]
    assert lineage_body["model_version"] == proposal["model_version"]
    assert lineage_body["decision_policy_version_id"] == proposal["policy_version_id"]

    rollback = client.post(
        f"/api/v1/heatzones/zones/{zone_id}/rollback",
        json={"revert_reason": "Shadow period showed no ranking benefit"},
        headers=HEATZONE_HEADERS,
    )
    assert rollback.status_code == 200
    assert len(rollback.json()["reverted_records"]) == 2

    after = client.get(
        f"/api/v1/heatzones/zones/{zone_id}/lineage", headers=HEATZONE_HEADERS
    )
    assert after.json()["is_active"] is False

    # The cells are atomic again, so the same proposal could be made afresh.
    compositions = client.get(
        "/api/v1/heatzones/compositions?active_only=true", headers=HEATZONE_HEADERS
    )
    assert compositions.json()["items"] == []

    revert_events = [
        event
        for event in bundle.audit_log.list_events()
        if event.event_type == "heatzone.composition.reverted.v1"
    ]
    assert revert_events[-1].actor == AUTHENTICATED_SUBJECT


def test_composition_override_and_rollback_flow() -> None:
    bundle = _bundle_with_evidence()
    client = TestClient(create_app(persistence=bundle))

    zone_id = "MZ-aabb112233445566"
    comp_repo = bundle.heatzone_composition_repository
    assert comp_repo is not None
    comp_repo.save_composition_batch(
        [
            HeatZoneCompositionRecord(
                zone_id=zone_id,
                tenant_id=TENANT_ID,
                member_cell_id=cell_id,
                composition_kind=CompositionKind.MERGED,
                decision_policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
            )
            for cell_id in ("cell-alpha", "cell-beta")
        ]
    )

    composition = client.get(
        f"/api/v1/heatzones/zones/{zone_id}/composition", headers=HEATZONE_HEADERS
    )
    assert composition.status_code == 200
    assert set(composition.json()["member_cell_ids"]) == {"cell-alpha", "cell-beta"}

    empty_reason = client.post(
        f"/api/v1/heatzones/zones/{zone_id}/override",
        json={"override_reason": "", "member_cell_ids": ["cell-alpha", "cell-beta"]},
        headers=HEATZONE_HEADERS,
    )
    assert empty_reason.status_code == 422

    claimed_identity = client.post(
        f"/api/v1/heatzones/zones/{zone_id}/override",
        json={
            "decided_by": "operator@odayplus.com",
            "override_reason": "Field survey",
            "member_cell_ids": ["cell-alpha", "cell-beta"],
        },
        headers=HEATZONE_HEADERS,
    )
    assert claimed_identity.status_code == 422

    override = client.post(
        f"/api/v1/heatzones/zones/{zone_id}/override",
        json={
            "override_reason": "Spatial boundary adjusted after an operator field survey",
            "member_cell_ids": ["cell-alpha", "cell-beta", "cell-gamma"],
        },
        headers=HEATZONE_HEADERS,
    )
    assert override.status_code == 200
    assert override.json()["decided_by"] == AUTHENTICATED_SUBJECT
    assert len(override.json()["records"]) == 3

    lineage = client.get(
        f"/api/v1/heatzones/zones/{zone_id}/lineage", headers=HEATZONE_HEADERS
    ).json()
    assert lineage["decided_by"] == AUTHENTICATED_SUBJECT
    assert lineage["member_count"] == 3

    rollback = client.post(
        f"/api/v1/heatzones/zones/{zone_id}/rollback",
        json={"revert_reason": "Reverting operator boundary adjustment"},
        headers=HEATZONE_HEADERS,
    )
    assert rollback.status_code == 200
    assert len(rollback.json()["reverted_records"]) == 3

    after = client.get(
        f"/api/v1/heatzones/zones/{zone_id}/lineage", headers=HEATZONE_HEADERS
    )
    assert after.json()["is_active"] is False

    types = [event.event_type for event in bundle.audit_log.list_events()]
    assert "heatzone.composition.overridden.v1" in types
    assert "heatzone.composition.reverted.v1" in types


def test_durable_policy_resolution_and_proposal_persistence(
    monkeypatch, tmp_path
) -> None:
    """The same flow against SQL-backed policy and composition repositories."""
    from shared.infrastructure.persistence.decision_policy import (
        SqlDecisionPolicyRepository,
    )
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.repositories import (
        DurableHeatZoneCompositionRepository,
    )

    engine = SqliteEngine(tmp_path / "heatzone_composition.sqlite3")
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

    policy_version_id = f"heatzone-merge-v1:{TENANT_ID}"
    import json as _json

    from shared.governance import DEFAULT_HEATZONE_MERGE_PARAMETERS

    engine.execute(
        """
        INSERT INTO workflow.decision_policies
        (policy_version_id, policy_label, policy_id, policy_version, policy_kind,
         tenant_id, effective_from, parameters, declared_inputs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            policy_version_id,
            "heatzone-merge-v1",
            "heatzone-merge",
            "1.0.0",
            "heatzone_merge",
            TENANT_ID,
            "2026-01-01T00:00:00+00:00",
            _json.dumps(DEFAULT_HEATZONE_MERGE_PARAMETERS),
            '["store_daily_performance", "heatzone_absorption_outcomes"]',
        ),
    )

    policy_repository = SqlDecisionPolicyRepository(engine)
    assert policy_repository.find_version(policy_version_id) is not None

    durable_compositions = DurableHeatZoneCompositionRepository(
        SqliteDocumentStore(engine)
    )
    base = build_persistence(mode="memory")
    bundle = type(base)(
        **{
            **base.__dict__,
            "heatzone_composition_repository": durable_compositions,
            "forecastops_policy_repository": policy_repository,
        }
    )
    populate_evidence_repository(
        bundle.heatzone_evidence_repository, tenant_id=TENANT_ID
    )
    use_matured_receipt(monkeypatch, tmp_path)
    client = TestClient(create_app(persistence=bundle))

    response = _evaluate(client, policy_version_id=policy_version_id)
    assert response.status_code == 200
    body = response.json()
    assert body["abstained"] is False
    proposal = body["proposals"][0]
    proposal_id = proposal["proposal_id"]
    assert str(uuid.UUID(proposal_id)) == proposal_id

    stored = durable_compositions.get_proposal(proposal_id, TENANT_ID)
    assert stored is not None
    assert stored.policy_version_id == policy_version_id
    snapshot_id = body["evidence"]["snapshot"]["inventory_version"]
    assert any(f"source_snapshot:{snapshot_id}" in reason for reason in stored.reasons)

    approved = client.post(
        f"/api/v1/heatzones/merge-split/proposals/{proposal_id}/approve",
        json={"notes": "Approved durable proposal"},
        headers=HEATZONE_HEADERS,
    )
    assert approved.status_code == 200
    assert approved.json()["proposal"]["approved_by"] == AUTHENTICATED_SUBJECT
    assert len(approved.json()["created_compositions"]) == 2

    persisted = durable_compositions.get_composition(proposal["zone_id"], TENANT_ID)
    assert [record.decided_by for record in persisted] == [AUTHENTICATED_SUBJECT] * 2

    evaluated = [
        event
        for event in bundle.audit_log.list_events()
        if event.event_type == "heatzone.composition.evaluated.v1"
    ]
    assert evaluated[-1].metadata["source_snapshot_id"] == snapshot_id


# ---------------------------------------------------------------------------
# HZ-004 evidence recording (ODP-FR-HZ-006)
#
# Merge/split is required to reason only from realised absorption history, so
# `expansion.heatzone_absorption_outcomes` has to be a relation something in
# production writes. These drive that entry through the mounted app.
# ---------------------------------------------------------------------------

ABSORPTION_HEADERS = {
    **auth_headers(Role.DATA_OWNER, subject="hz004-pipeline"),
    "x-tenant-id": TENANT_ID,
}

WINDOW_START = date(2026, 1, 5)
WINDOW_END = date(2026, 2, 1)


HZ004_CELL = "cell-hz004-00"
HZ004_H3 = "894ba0a4e77ffff"


def _record_outcome(client: TestClient, body: dict, *, headers=ABSORPTION_HEADERS):
    return client.post(
        "/api/v1/heatzones/absorption/outcomes", json=body, headers=headers
    )


def _bundle_with_registered_cell(
    cell_id: str = HZ004_CELL,
    *,
    h3_index: str = HZ004_H3,
    barrier_side: str | None = None,
    barrier_description: str = "",
):
    """A bundle whose geo pipeline has published the cell, and optionally its barrier."""
    bundle = build_persistence(mode="memory")
    bundle.heatzone_evidence_repository.register_cell(
        TENANT_ID,
        CellRegistration(
            cell_id,
            h3_index,
            "Taipei",
            "Xinyi",
            barrier_side=barrier_side,
            barrier_description=barrier_description,
        ),
    )
    return bundle


def _client_with_populated_sources(
    bundle,
    *,
    stores: tuple[str, ...] = ("store-1", "store-2"),
    window_start: date = WINDOW_START,
    window_end: date = WINDOW_END,
    daily_revenue: float = 500.0,
    fingerprint: str = "sdp-fingerprint",
    method: str = "FIRST_OBSERVED_TRANSACTION",
    confidence: str = "HIGH",
    drop_date: str | None = None,
    facade_override: Any = None,
    store_coords: dict[str, tuple[float, float]] | None = None,
    skip_store_refs: bool = False,
    skip_cell_profile: bool = False,
    original_demand: float | None = 100_000.0,
    cell_id: str = HZ004_CELL,
    h3_index: str = HZ004_H3,
) -> TestClient:
    if facade_override is not None:
        return TestClient(
            create_app(persistence=bundle, market_intelligence_facade=facade_override)
        )
    transport = InMemoryDataPlatformTransport()
    perfs = performance_rows(
        store_ids=stores,
        window_start=window_start,
        window_end=window_end,
        daily_revenue=daily_revenue,
        fingerprint=fingerprint,
    )
    op_starts = operational_start_rows(
        store_ids=stores,
        window_start=window_start,
        window_end=window_end,
        method=method,
        confidence=confidence,
    )
    for p in perfs:
        if drop_date and p["business_date"] == drop_date:
            continue
        doc_id = f"{p['store_id']}:{p['business_date']}"
        transport.store_document("oday.store-daily-performance.v1", doc_id, p)
    for op in op_starts:
        transport.store_document(
            "oday.operational-start-observation.v1", op["store_id"], op
        )

    if not skip_store_refs:
        for s_id in stores:
            coords = (store_coords or {}).get(s_id, (25.03, 121.56))
            transport.store_document(
                "oday.store-reference.v1",
                s_id,
                {
                    "store_id": s_id,
                    "store_name": f"Test Store {s_id}",
                    "effective_from": "2025-01-01",
                    "registered_at": "2025-01-01T00:00:00Z",
                    "source_row_digest": f"digest-{s_id}",
                    "geolocation": {
                        "latitude": coords[0],
                        "longitude": coords[1],
                        "srid": 4326,
                    },
                    "time_contract": {
                        "contract_version": "emgi.time-contract.v4",
                        "materialization_kind": "observation",
                        "materialization_environment": "development",
                        "store_timezone": "Asia/Taipei",
                    },
                },
            )

    if not skip_cell_profile:
        period_key = f"{window_start.year:04d}-{window_start.month:02d}"
        doc_payload = {
            "contract_version": "emgi.market-cell-profile.v1",
            "profile_id": f"mcp-{cell_id}-{period_key}",
            "product_version": "0.4.1",
            "period_grain": "MONTHLY",
            "period_key": period_key,
            "h3_resolution": 9,
            "generated_at": "2026-01-01T00:00:00Z",
            "tenant_id": TENANT_ID,
            "cells": [
                {
                    "cell_id": cell_id,
                    "h3_index": h3_index,
                    "h3_resolution": 9,
                    "period_grain": "MONTHLY",
                    "period_key": period_key,
                    "demographics": {"total_population": 5000.0},
                    "competitors": {
                        "total_competitors": 2,
                        "active_competitors": 2,
                        "brands_present": ["BrandA"],
                        "stores_by_brand": {"BrandA": 2},
                        "stores_by_category": {"convenience": 2},
                    },
                    "rent": {"sample_count": 10},
                    "mobility": {},
                    "coverage": {"overall_readiness": "ready", "domain_coverage": {}},
                    "source_support": {
                        "source_dataset_ids": ["ds-1"],
                        "observation_count": 10,
                        "sample_count": 10,
                        "first_observed_at": "2026-01-01T00:00:00Z",
                        "last_observed_at": "2026-01-31T00:00:00Z",
                    },
                    "metadata": (
                        {"original_demand": original_demand}
                        if original_demand is not None
                        else {"provider": "emgi"}
                    ),
                }
            ],
            "source_support": {
                "source_dataset_ids": ["ds-1"],
                "observation_count": 10,
                "sample_count": 10,
                "first_observed_at": "2026-01-01T00:00:00Z",
                "last_observed_at": "2026-01-31T00:00:00Z",
            },
        }
        transport.store_document(
            "emgi.market-cell-profile.v1",
            f"mcp-{cell_id}-{period_key}",
            doc_payload,
        )

    facade = MarketDataFacade(transport=transport)
    return TestClient(create_app(persistence=bundle, market_intelligence_facade=facade))


def test_recording_an_absorption_outcome_measures_it_server_side() -> None:
    """The route computes the outcome; the request only names the inputs."""
    bundle = _bundle_with_registered_cell()
    client = _client_with_populated_sources(bundle)

    response = _record_outcome(
        client,
        outcome_request(
            cell_id="cell-hz004-00",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            store_ids=("store-1", "store-2"),
            original_demand=100_000.0,
            daily_revenue=500.0,
        ),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # Two stores at 500/day over a 28-day window is 28_000 of 100_000 demand.
    assert body["absorbed_demand"] == 28_000.0
    assert body["remaining_demand"] == 72_000.0
    assert body["absorption_ratio"] == 0.28
    assert body["absorbing_store_count"] == 2
    assert body["absorption_policy_version_id"] == f"heatzone-absorption-v1:{TENANT_ID}"
    # Traceability is not optional: the basis ids come off each source row's
    # raw_contract_fingerprint, so the outcome can be tied back to what it read.
    assert len(body["basis_source_ids"]) == 56
    assert all(
        source_id.startswith("sdp-fingerprint-") for source_id in body["basis_source_ids"]
    )

    stored = bundle.heatzone_evidence_repository.list_absorption_outcomes(TENANT_ID)
    assert [(o.cell_id, o.absorbed_demand) for o in stored] == [
        ("cell-hz004-00", 28_000.0)
    ]

    events = [
        event
        for event in bundle.audit_log.list_events()
        if event.event_type == "heatzone.absorption.outcome.recorded.v1"
    ]
    assert len(events) == 1
    assert events[0].metadata["absorption_ratio"] == 0.28


def test_a_caller_cannot_state_what_a_zone_absorbed() -> None:
    """Supplying the measurement is refused, not merged with the computed one."""
    bundle = _bundle_with_registered_cell()
    client = _client_with_populated_sources(bundle)

    body = outcome_request(
        cell_id="cell-hz004-00", window_start=WINDOW_START, window_end=WINDOW_END
    )
    for field, value in (
        ("absorbed_demand", 99_000.0),
        ("absorption_ratio", 0.99),
        ("absorbing_store_count", 40),
        ("under_realized", False),
        ("basis_source_ids", ["made-up"]),
    ):
        response = _record_outcome(client, {**body, field: value})
        assert response.status_code == 422, field
        assert field in response.text


def test_an_untraceable_period_is_refused_rather_than_recorded() -> None:
    """No fingerprint, no evidence: an unsourced outcome is not admissible."""
    bundle = _bundle_with_registered_cell()
    client = _client_with_populated_sources(bundle, fingerprint="")

    body = outcome_request(
        cell_id="cell-hz004-00",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        fingerprint="",
    )

    response = _record_outcome(client, body)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_NOT_MEASURABLE"
    assert bundle.heatzone_evidence_repository.list_absorption_outcomes(TENANT_ID) == []


def test_an_incomplete_window_is_refused_rather_than_recorded() -> None:
    """A partial period would look measured and would not be."""
    bundle = _bundle_with_registered_cell()
    drop_day = f"{WINDOW_START.year:04d}-{WINDOW_START.month:02d}-15"
    client = _client_with_populated_sources(bundle, drop_date=drop_day)

    body = outcome_request(
        cell_id="cell-hz004-00", window_start=WINDOW_START, window_end=WINDOW_END
    )

    response = _record_outcome(client, body)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_SOURCE_NOT_FOUND"
    assert bundle.heatzone_evidence_repository.list_absorption_outcomes(TENANT_ID) == []


def test_a_declared_start_date_is_refused_under_the_seeded_policy() -> None:
    """Admitting a claimed start date is a governance decision, not a default."""
    bundle = _bundle_with_registered_cell()
    client = _client_with_populated_sources(bundle, method="DECLARED")

    response = _record_outcome(
        client,
        outcome_request(
            cell_id="cell-hz004-00",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            method="DECLARED",
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_NOT_MEASURABLE"
    assert bundle.heatzone_evidence_repository.list_absorption_outcomes(TENANT_ID) == []


def test_rerecording_a_period_is_idempotent_but_a_disagreement_is_refused() -> None:
    """History is appended to, never rewritten."""
    bundle = _bundle_with_registered_cell()
    client = _client_with_populated_sources(bundle)

    body = outcome_request(
        cell_id="cell-hz004-00", window_start=WINDOW_START, window_end=WINDOW_END
    )
    assert _record_outcome(client, body).status_code == 200
    assert _record_outcome(client, body).status_code == 200
    assert len(bundle.heatzone_evidence_repository.list_absorption_outcomes(TENANT_ID)) == 1

    # If the stored outcome in repository differs from a new calculation, it's a conflict
    differing_outcome = AbsorptionOutcomeRecord(
        cell_id="cell-hz004-00",
        period_start=WINDOW_START,
        period_end=WINDOW_END,
        original_demand=100_000.0,
        absorbed_demand=50_000.0,
        remaining_demand=50_000.0,
        absorption_ratio=0.50,
        absorbing_store_count=2,
        basis_source_ids=("other-fingerprint",),
        absorption_policy_version_id=f"heatzone-absorption-v1:{TENANT_ID}",
        basis_at=datetime(2026, 9, 1, tzinfo=UTC),
        under_realized=False,
    )
    bundle.heatzone_evidence_repository._outcomes[TENANT_ID] = [differing_outcome]
    response = _record_outcome(client, body)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "HZ004_OUTCOME_CONFLICT"
    stored = bundle.heatzone_evidence_repository.list_absorption_outcomes(TENANT_ID)
    assert [o.absorbed_demand for o in stored] == [50_000.0]


def test_a_side_labelled_outcome_needs_the_barrier_it_was_split_on() -> None:
    """Side labels are the only admissible basis for a split, so they carry one.

    A side without a recorded barrier would let a split be taken on a division
    nobody observed, which is the guess the readiness ruling forbids.
    """
    barrier_desc = "Love River, no crossing within the zone"
    bundle = _bundle_with_registered_cell(
        cell_id=HZ004_CELL, barrier_side="A", barrier_description=barrier_desc
    )
    client = _client_with_populated_sources(bundle)

    # 1. Matching registered barrier succeeds
    named = _record_outcome(
        client,
        outcome_request(
            cell_id=HZ004_CELL,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            barrier_side="A",
            barrier_description=barrier_desc,
        ),
    )
    assert named.status_code == 200, named.text
    assert named.json()["barrier_side"] == "A"

    stored = bundle.heatzone_evidence_repository.list_cells(TENANT_ID)
    assert [o.barrier_side for o in stored[0].side_outcomes] == ["A"]

    # 2. A side outside the recorded barrier is refused.
    invalid = _record_outcome(
        client,
        outcome_request(
            cell_id=HZ004_CELL,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            barrier_side="C",
            barrier_description=barrier_desc,
        ),
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "HZ004_BARRIER_UNBACKED"

    # 3. Supplying barrier_side for a cell with no registered barrier is refused.
    bundle_unbacked = _bundle_with_registered_cell(cell_id="cell-no-barrier")
    client_unbacked = _client_with_populated_sources(bundle_unbacked)
    unbacked = _record_outcome(
        client_unbacked,
        outcome_request(
            cell_id="cell-no-barrier",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            barrier_side="A",
            barrier_description=barrier_desc,
        ),
    )
    assert unbacked.status_code == 422
    assert unbacked.json()["detail"]["code"] == "HZ004_BARRIER_UNBACKED"


def test_durable_path_side_based_split_refused_and_barrier_unbacked(
    tmp_path, monkeypatch
) -> None:
    """On durable persistence, cells have no registered geo barrier pipeline.

    Attempting to record a side-labelled outcome on a durable bundle is refused
    with HZ004_BARRIER_UNBACKED. When evaluating merge/split on durable history,
    any split candidate is refused fail-closed by the named rule
    'no_side_labelled_hz004_outcomes_for_every_member_cell'.
    """
    db_path = tmp_path / "durable_barrier.db"
    bundle = build_persistence(mode="sqlite", db_path=db_path)
    bundle.engine.execute(
        "INSERT INTO h3_cells (geo_cell_id, h3_index, centroid_latitude, centroid_longitude, admin_city, admin_district) "
        "VALUES (?, ?, 25.03, 121.56, 'Taipei', 'Xinyi')",
        (HZ004_CELL, f"8a{HZ004_CELL}"),
    )
    client = _client_with_populated_sources(bundle)

    # 1. Attempting to record side-labelled outcome fails closed on durable path
    response = _record_outcome(
        client,
        outcome_request(
            cell_id=HZ004_CELL,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            barrier_side="A",
            barrier_description="Provincial Highway 17",
        ),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_BARRIER_UNBACKED"

    # 2. On durable evaluation with mature evidence, candidate split zones without side evidence decline
    reference = build_evidence_repository(tenant_id=TENANT_ID)
    for cell in reference.list_cells(TENANT_ID):
        bundle.engine.execute(
            "INSERT OR IGNORE INTO h3_cells (geo_cell_id, h3_index, centroid_latitude, centroid_longitude, admin_city, admin_district) "
            "VALUES (?, ?, 25.03, 121.56, ?, ?)",
            (cell.cell_id, cell.h3_index, cell.admin_city, cell.admin_district),
        )
    for index, outcome in enumerate(reference.list_absorption_outcomes(TENANT_ID)):
        bundle.engine.execute(
            """
            INSERT OR IGNORE INTO heatzone_absorption_outcomes (
                outcome_id, tenant_id, geo_cell_id, period_start, period_end,
                original_demand, absorbed_demand, remaining_demand,
                absorption_ratio, absorbing_store_count, under_realized,
                barrier_side, barrier_description, basis_source_ids, basis_at,
                absorption_policy_version_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"outcome-{index}",
                TENANT_ID,
                outcome.cell_id,
                outcome.period_start.isoformat(),
                outcome.period_end.isoformat(),
                outcome.original_demand,
                outcome.absorbed_demand,
                outcome.remaining_demand,
                outcome.absorption_ratio,
                outcome.absorbing_store_count,
                int(outcome.under_realized),
                outcome.barrier_side,
                outcome.barrier_description,
                json.dumps(list(outcome.basis_source_ids)),
                outcome.basis_at.isoformat(),
                ABSORPTION_POLICY_ID,
                outcome.basis_at.isoformat(),
            ),
        )
    for index, (left, right) in enumerate(reference.list_adjacency(TENANT_ID)):
        bundle.engine.execute(
            "INSERT OR IGNORE INTO h3_cell_adjacency (adjacency_id, cell_id, neighbor_cell_id, k_ring) "
            "VALUES (?, ?, ?, 1)",
            (f"edge-{index}", left, right),
        )

    use_matured_receipt(monkeypatch, tmp_path)
    zone_id = "MZ-0011223344556677"
    bundle.heatzone_composition_repository.save_composition_batch(
        [
            HeatZoneCompositionRecord(
                zone_id=zone_id,
                tenant_id=TENANT_ID,
                member_cell_id=cell_id,
                composition_kind=CompositionKind.MERGED,
                decided_by="system",
                decision_policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
            )
            for cell_id in (SPLIT_LEFT, SPLIT_RIGHT)
        ]
    )
    eval_resp = _evaluate(client)
    assert eval_resp.status_code == 200
    eval_body = eval_resp.json()
    assert eval_body["abstained"] is False
    assert all(p["composition_kind"] != "split_child" for p in eval_body["proposals"])
    assert any(
        d.get("reason") == "no_side_labelled_hz004_outcomes_for_every_member_cell"
        for d in eval_body.get("declined_candidates", [])
    )


def test_an_outcome_for_an_unpublished_cell_is_refused() -> None:
    """`geo_cell_id` is a foreign key, so an invented cell id is not a cell.

    Without this the SQLite and PostgreSQL paths would disagree: Postgres
    refuses the insert, SQLite would accept a row the evidence reader's join
    then silently drops, so the outcome would look recorded and never be read.
    """
    bundle = _bundle_with_registered_cell()
    client = _client_with_populated_sources(bundle)

    response = _record_outcome(
        client,
        outcome_request(
            cell_id="cell-never-published",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_UNREGISTERED_CELL"
    assert bundle.heatzone_evidence_repository.list_absorption_outcomes(TENANT_ID) == []


def test_the_roles_that_decide_a_merge_cannot_write_its_evidence() -> None:
    """Structural separation: whoever approves a composition cannot record for it.

    The three roles checked here are exactly the ones granted heatzone approval
    and override, so this is the separation itself rather than a sample of it.
    """
    bundle = _bundle_with_registered_cell()
    client = _client_with_populated_sources(bundle)
    body = outcome_request(
        cell_id="cell-hz004-00", window_start=WINDOW_START, window_end=WINDOW_END
    )

    for role in (Role.EXPANSION_USER, Role.SITE_REVIEWER, Role.EXECUTIVE):
        response = _record_outcome(
            client,
            body,
            headers={
                **auth_headers(role, subject="operator-a"),
                "x-tenant-id": TENANT_ID,
            },
        )
        assert response.status_code == 403, role


def test_heatzone_composition_approver_set_is_pinned(monkeypatch, tmp_path) -> None:
    """Pin the authorized approver set for composition decisions.

    Role.SITE_REVIEWER (expansion-manager), Role.EXPANSION_USER (expansion-staff),
    and Role.EXECUTIVE are granted OVERRIDE/ROLLBACK authority on heatzone.
    Unprivileged roles (AUDITOR, MARKETING_MANAGER, REGIONAL_SUPERVISOR) are denied HTTP 403.
    """
    bundle = _bundle_with_evidence()
    use_matured_receipt(monkeypatch, tmp_path)
    client = TestClient(create_app(persistence=bundle))

    proposal_resp = _evaluate(client)
    assert proposal_resp.status_code == 200
    proposal_id = proposal_resp.json()["proposals"][0]["proposal_id"]

    # 1. Privileged approvers can reach approve
    for role in (Role.SITE_REVIEWER, Role.EXPANSION_USER, Role.EXECUTIVE):
        resp = client.post(
            f"/api/v1/heatzones/merge-split/proposals/{proposal_id}/preview",
            headers={**auth_headers(role, subject="approver"), "x-tenant-id": TENANT_ID},
        )
        assert resp.status_code == 200, f"Role {role} must be permitted to preview"

    # 2. Unprivileged roles are denied on approve/override/rollback
    for unprivileged in (Role.AUDITOR, Role.MARKETING_MANAGER, Role.REGIONAL_SUPERVISOR):
        app_resp = client.post(
            f"/api/v1/heatzones/merge-split/proposals/{proposal_id}/approve",
            json={"notes": "unauthorized attempt"},
            headers={**auth_headers(unprivileged, subject="attacker"), "x-tenant-id": TENANT_ID},
        )
        assert app_resp.status_code == 403, f"Role {unprivileged} must be denied approve"

        ovr_resp = client.post(
            "/api/v1/heatzones/zones/MZ-00112233/override",
            json={"override_reason": "unauthorized attempt"},
            headers={**auth_headers(unprivileged, subject="attacker"), "x-tenant-id": TENANT_ID},
        )
        assert ovr_resp.status_code == 403, f"Role {unprivileged} must be denied override"

        rb_resp = client.post(
            "/api/v1/heatzones/zones/MZ-00112233/rollback",
            json={"revert_reason": "unauthorized attempt"},
            headers={**auth_headers(unprivileged, subject="attacker"), "x-tenant-id": TENANT_ID},
        )
        assert rb_resp.status_code == 403, f"Role {unprivileged} must be denied rollback"


def test_recorded_outcomes_reach_the_merge_split_engine(monkeypatch, tmp_path) -> None:
    """The written history is the history evaluate reads.

    Recording is the production entry, so an outcome written through the route
    has to be visible to a later evaluate on the same bundle -- otherwise the
    writer fills a relation the engine is not reading.
    """
    bundle = _bundle_with_registered_cell()
    use_matured_receipt(monkeypatch, tmp_path)
    client = _client_with_populated_sources(bundle)

    # Nothing recorded yet: the repository is wired but the history is empty,
    # so the engine abstains on measured emptiness rather than proposing.
    empty = _evaluate(client)
    assert empty.status_code == 200
    empty_body = empty.json()
    assert empty_body["abstained"] is True
    assert empty_body["readiness"]["metrics"]["outcome_period_count"] == 0
    assert empty_body["readiness"]["metrics"]["basis_source_id_count"] == 0
    assert empty_body["proposals"] == []

    assert (
        _record_outcome(
            client,
            outcome_request(
                cell_id="cell-hz004-00",
                window_start=WINDOW_START,
                window_end=WINDOW_END,
            ),
        ).status_code
        == 200
    )

    # The recorded period is now what the engine measures maturity from. One
    # period is nowhere near a decision, so it still abstains -- but it abstains
    # on evidence the production entry wrote, which is the property under test.
    after = _evaluate(client)
    assert after.status_code == 200
    body = after.json()
    assert body["abstained"] is True
    assert body["readiness"]["metrics"]["outcome_period_count"] == 1
    assert body["readiness"]["metrics"]["basis_source_id_count"] == 56


def test_record_outcome_fails_closed_when_facade_unavailable() -> None:
    """If MarketDataFacade is not configured, the route fails closed with 503."""
    bundle = _bundle_with_registered_cell()
    app = FastAPI()
    app.include_router(
        create_heatzone_router(
            store=bundle.heatzone_store,
            composition_repository=bundle.heatzone_composition_repository,
            policy_repository=bundle.forecastops_policy_repository,
            evidence_repository=bundle.heatzone_evidence_repository,
            absorption_outcome_writer=bundle.heatzone_absorption_outcome_writer,
            market_data_facade=None,
            audit_log=bundle.audit_log,
        ),
        prefix="/api/v1",
    )
    client = TestClient(app)

    response = _record_outcome(
        client,
        {
            "cell_id": "cell-hz004-00",
            "period_start": WINDOW_START.isoformat(),
            "period_end": WINDOW_END.isoformat(),
            "original_demand": 100_000.0,
            "store_ids": ["store-1"],
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "HZ004_FACADE_UNAVAILABLE"


def test_record_outcome_fails_closed_when_published_sources_missing() -> None:
    """If store documents are missing in published repository, fail closed with 422."""
    bundle = _bundle_with_registered_cell()
    transport = InMemoryDataPlatformTransport()
    # Empty transport - no documents stored
    facade = MarketDataFacade(transport=transport)
    client = TestClient(
        create_app(persistence=bundle, market_intelligence_facade=facade)
    )

    response = _record_outcome(
        client,
        {
            "cell_id": "cell-hz004-00",
            "period_start": WINDOW_START.isoformat(),
            "period_end": WINDOW_END.isoformat(),
            "original_demand": 100_000.0,
            "store_ids": ["store-1"],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_SOURCE_NOT_FOUND"


def test_record_outcome_enforces_market_data_principal_authorization() -> None:
    """MarketDataFacade validates caller principal and tenant isolation."""
    bundle = _bundle_with_registered_cell()
    client = _client_with_populated_sources(bundle)

    # Caller with unpermitted role
    unauthorized_headers = {
        **auth_headers(Role.AUDITOR, subject="unauthorized-auditor"),
        "x-tenant-id": TENANT_ID,
    }
    # First, auditor is not allowed to create heatzone absorption (403 from require_permission)
    response = _record_outcome(
        client,
        {
            "cell_id": "cell-hz004-00",
            "period_start": WINDOW_START.isoformat(),
            "period_end": WINDOW_END.isoformat(),
            "original_demand": 100_000.0,
            "store_ids": ["store-1"],
        },
        headers=unauthorized_headers,
    )
    assert response.status_code == 403


def test_record_outcome_with_server_side_facade_lookup_and_verification() -> None:
    """The route hydrates and verifies rows from the server-side published source repository."""
    bundle = _bundle_with_registered_cell()
    client = _client_with_populated_sources(bundle)
    stores = ("store-1", "store-2")

    # 1. Caller only names the store_ids and period; server looks up from facade
    response = _record_outcome(
        client,
        {
            "cell_id": "cell-hz004-00",
            "period_start": WINDOW_START.isoformat(),
            "period_end": WINDOW_END.isoformat(),
            "original_demand": 100_000.0,
            "store_ids": list(stores),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["absorbed_demand"] == 28_000.0
    assert body["absorption_ratio"] == 0.28
    assert body["absorbing_store_count"] == 2
    assert len(body["basis_source_ids"]) == 56


def test_record_outcome_refuses_client_tampered_performance_row() -> None:
    """If client supplies fabricated rows that disagree with the published source, fail closed."""
    bundle = _bundle_with_registered_cell()
    client = _client_with_populated_sources(bundle)
    stores = ("store-1", "store-2")
    perfs = performance_rows(
        store_ids=stores,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        daily_revenue=500.0,
    )
    op_starts = operational_start_rows(
        store_ids=stores,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )

    # Client tries to pass inflated revenue (tampering)
    tampered_perfs = [dict(p) for p in perfs]
    tampered_perfs[0]["paid_amount"] = 99_999.0

    response = _record_outcome(
        client,
        {
            "cell_id": "cell-hz004-00",
            "period_start": WINDOW_START.isoformat(),
            "period_end": WINDOW_END.isoformat(),
            "original_demand": 100_000.0,
            "store_ids": list(stores),
            "performances": tampered_perfs,
            "operational_starts": op_starts,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_INPUT_REFUSED"
    assert "does not match published source record" in response.json()["detail"]["message"]


def test_evaluate_split_declined_when_split_edge_removed_from_adjacency(
    monkeypatch, tmp_path
) -> None:
    """A mature fixture with side evidence but missing adjacency edge is declined during evaluate."""
    bundle = _bundle_with_evidence()
    use_matured_receipt(monkeypatch, tmp_path)
    client = TestClient(create_app(persistence=bundle))

    # Add split candidate zone and barrier evidence
    from modules.heatzone.domain.composition import generate_merged_zone_id
    from tests.integration._heatzone_evidence import add_barrier_evidence

    zone_id = generate_merged_zone_id((SPLIT_LEFT, SPLIT_RIGHT))
    bundle.heatzone_composition_repository.save_composition(
        HeatZoneCompositionRecord(
            zone_id=zone_id,
            tenant_id=TENANT_ID,
            member_cell_id=SPLIT_LEFT,
            composition_kind=CompositionKind.MERGED,
            decision_policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
        )
    )
    bundle.heatzone_composition_repository.save_composition(
        HeatZoneCompositionRecord(
            zone_id=zone_id,
            tenant_id=TENANT_ID,
            member_cell_id=SPLIT_RIGHT,
            composition_kind=CompositionKind.MERGED,
            decision_policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
        )
    )
    add_barrier_evidence(bundle.heatzone_evidence_repository, tenant_id=TENANT_ID)

    # Link an extra unrelated pair in Taipei so adjacent pair count remains >= 30
    bundle.heatzone_evidence_repository.link_adjacent(
        TENANT_ID, "cell-taipei-00", "cell-taipei-02"
    )

    # Remove the split candidate edge from adjacency while leaving all other pairs
    bundle.heatzone_evidence_repository._adjacency[TENANT_ID] = {
        edge
        for edge in bundle.heatzone_evidence_repository._adjacency.get(TENANT_ID, set())
        if edge != (SPLIT_LEFT, SPLIT_RIGHT) and edge != (SPLIT_RIGHT, SPLIT_LEFT)
    }

    response = _evaluate(client)
    assert response.status_code == 200
    body = response.json()
    assert body["abstained"] is False
    # SPLIT_CHILD proposal is NOT proposed because adjacency edge is missing
    assert any(
        d.get("reason") in (
            "split_partitions_not_spatially_adjacent",
            "split_candidate_zone_not_spatially_contiguous",
        )
        for d in body["declined_candidates"]
    )


# ---------------------------------------------------------------------------
# Effective merge policy (ODP-SD-AMD-001 3.3)
#
# Naming a policy version selects one; it does not exempt it from being the
# right kind, the right tenant's, and in force.
# ---------------------------------------------------------------------------


def _retired_merge_policy() -> DecisionPolicy:
    """A superseded heatzone_merge version: right kind, right tenant, not in force."""
    policy = default_heatzone_merge_policy(TENANT_ID)
    return replace(
        policy,
        policy_label="heatzone-merge-v0",
        policy_version_id=f"heatzone-merge-v0:{TENANT_ID}",
        policy_version="0.9.0",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        effective_to=datetime(2026, 6, 1, tzinfo=UTC),
    )


def test_evaluate_refuses_a_policy_version_of_the_wrong_kind() -> None:
    """A row that parses is not a heat-zone merge policy."""
    bundle = _bundle_with_evidence()
    # Already seeded for this tenant: a real registry row of a different kind.
    other_kind = default_model_performance_drift_policy(TENANT_ID)
    client = TestClient(create_app(persistence=bundle))

    response = _evaluate(client, policy_version_id=other_kind.policy_version_id)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "not 'heatzone_merge'" in detail
    assert other_kind.policy_kind in detail


def test_evaluate_refuses_a_policy_version_that_is_no_longer_in_force() -> None:
    """A retired version's thresholds are not the ones governing today."""
    bundle = _bundle_with_evidence()
    retired = _retired_merge_policy()
    bundle.forecastops_policy_repository.add(retired)
    client = TestClient(create_app(persistence=bundle))

    response = _evaluate(client, policy_version_id=retired.policy_version_id)

    assert response.status_code == 422
    assert "not in force" in response.json()["detail"]


def test_evaluate_refuses_another_tenants_policy_version() -> None:
    bundle = _bundle_with_evidence()
    foreign = default_heatzone_merge_policy("tenant-b")
    client = TestClient(create_app(persistence=bundle))

    response = _evaluate(client, policy_version_id=foreign.policy_version_id)

    assert response.status_code == 422
    assert "does not belong to tenant" in response.json()["detail"]


def _seed_overridable_zone(bundle) -> str:
    zone_id = "MZ-00ff112233445566"
    bundle.heatzone_composition_repository.save_composition_batch(
        [
            HeatZoneCompositionRecord(
                zone_id=zone_id,
                tenant_id=TENANT_ID,
                member_cell_id=cell_id,
                composition_kind=CompositionKind.MERGED,
                decided_by="system",
                decision_policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
            )
            for cell_id in ("cell-override-00", "cell-override-01")
        ]
    )
    return zone_id


def _override(client: TestClient, zone_id: str, **body: object):
    return client.post(
        f"/api/v1/heatzones/zones/{zone_id}/override",
        json={"override_reason": "operator judgement on a recorded barrier", **body},
        headers=HEATZONE_HEADERS,
    )


def test_override_resolves_a_governed_policy_instead_of_spelling_one() -> None:
    """The recorded version comes from the registry, not from string assembly."""
    bundle = _bundle_with_evidence()
    zone_id = _seed_overridable_zone(bundle)
    client = TestClient(create_app(persistence=bundle))

    response = _override(client, zone_id)

    assert response.status_code == 200
    governed = default_heatzone_merge_policy(TENANT_ID)
    assert all(
        record["decision_policy_version_id"] == governed.policy_version_id
        for record in response.json()["records"]
    )


def test_override_refuses_a_policy_version_it_cannot_resolve() -> None:
    """Previously this route fabricated a version id and wrote it regardless."""
    bundle = _bundle_with_evidence()
    zone_id = _seed_overridable_zone(bundle)
    client = TestClient(create_app(persistence=bundle))

    response = _override(
        client, zone_id, decision_policy_version_id=f"heatzone-merge-v9:{TENANT_ID}"
    )

    assert response.status_code == 422
    assert "not found" in response.json()["detail"]
    # And nothing was decided: the zone still holds its original composition.
    lineage = client.get(
        f"/api/v1/heatzones/zones/{zone_id}/lineage", headers=HEATZONE_HEADERS
    )
    assert lineage.json()["decided_by"] == "system"


def test_override_refuses_a_policy_version_of_the_wrong_kind() -> None:
    bundle = _bundle_with_evidence()
    zone_id = _seed_overridable_zone(bundle)
    # Already seeded for this tenant: a real registry row of a different kind.
    other_kind = default_model_performance_drift_policy(TENANT_ID)
    client = TestClient(create_app(persistence=bundle))

    response = _override(
        client, zone_id, decision_policy_version_id=other_kind.policy_version_id
    )

    assert response.status_code == 422
    assert "not 'heatzone_merge'" in response.json()["detail"]


def test_approve_refuses_partial_replacement_of_active_multi_cell_zone() -> None:
    """API endpoint rejects proposal approval when it would strand sibling cells (422)."""
    bundle = _bundle_with_evidence()
    multi_cells = ("cell-api-a", "cell-api-b", "cell-api-c")
    zone_id = generate_merged_zone_id(multi_cells)
    for cell_id in multi_cells:
        bundle.heatzone_composition_repository.save_composition(
            HeatZoneCompositionRecord(
                zone_id=zone_id,
                tenant_id=TENANT_ID,
                member_cell_id=cell_id,
                composition_kind=CompositionKind.MERGED,
                decided_by="system",
                decision_policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
            )
        )

    # Save a partial merge proposal
    partial_proposal = MergeSplitProposalRecord(
        proposal_id="prop-partial-api",
        zone_id=generate_merged_zone_id(("cell-api-a", "cell-api-x")),
        tenant_id=TENANT_ID,
        composition_kind=CompositionKind.MERGED,
        member_cell_ids=("cell-api-a", "cell-api-x"),
        parent_zone_id=None,
        ndcg_gain=0.08,
        cannibalization_variance_reduction=0.30,
        correlation_rho=0.85,
        disconnect_index=0.05,
        confidence=0.80,
        model_version=COMPOSITION_MODEL_VERSION,
        policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
    )
    bundle.heatzone_composition_repository.save_proposal(partial_proposal)

    client = TestClient(create_app(persistence=bundle))
    response = client.post(
        f"/api/v1/heatzones/merge-split/proposals/{partial_proposal.proposal_id}/approve",
        json={"notes": "attempted partial merge via API"},
        headers=HEATZONE_HEADERS,
    )

    assert response.status_code == 422
    assert "partial replacement of active zone" in response.json()["detail"]

    # Original zone and cells remain active
    active = bundle.heatzone_composition_repository.list_compositions(
        TENANT_ID, active_only=True
    )
    assert len(active) == 3
    assert {r.zone_id for r in active} == {zone_id}


def test_recording_outcome_refuses_when_store_belongs_to_different_cell() -> None:
    """Store with geolocation in Taipei cannot be attributed to a Kaohsiung cell."""
    bundle = _bundle_with_registered_cell(
        cell_id="cell-kh-01",
        h3_index="894bb10a1c3ffff",
    )
    # Populate sources with store coordinates in Taipei (25.03, 121.56)
    client = _client_with_populated_sources(
        bundle,
        cell_id="cell-kh-01",
        h3_index="894bb10a1c3ffff",
        store_coords={"store-1": (25.03, 121.56), "store-2": (25.03, 121.56)},
    )
    response = _record_outcome(
        client,
        outcome_request(
            cell_id="cell-kh-01",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            store_ids=("store-1", "store-2"),
            original_demand=100_000.0,
            daily_revenue=500.0,
        ),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_STORE_CELL_MISMATCH"
    assert "does not belong to target cell" in response.json()["detail"]["message"]


def test_recording_outcome_refuses_when_demand_baseline_not_published() -> None:
    """If authoritative MarketCellProfile is not published at all, fail closed (422)."""
    bundle = _bundle_with_registered_cell()
    client = _client_with_populated_sources(bundle, skip_cell_profile=True)

    response = _record_outcome(
        client,
        outcome_request(
            cell_id="cell-hz004-00",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            store_ids=("store-1", "store-2"),
            original_demand=100_000.0,
            daily_revenue=500.0,
        ),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_DEMAND_BASELINE_NOT_FOUND"


def test_recording_outcome_refuses_when_published_cell_profile_has_no_demand_baseline() -> None:
    """If authoritative MarketCellProfile exists but lacks demand baseline in metadata, fail closed (422)."""
    bundle = _bundle_with_registered_cell()
    client = _client_with_populated_sources(bundle, original_demand=None)

    response = _record_outcome(
        client,
        outcome_request(
            cell_id="cell-hz004-00",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            store_ids=("store-1", "store-2"),
            original_demand=100_000.0,
            daily_revenue=500.0,
        ),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_DEMAND_BASELINE_NOT_FOUND"
    assert "contains no authoritative demand baseline" in response.json()["detail"]["message"]


def test_recording_outcome_refuses_when_original_demand_disagrees_with_published_baseline() -> None:
    """If caller provides original_demand that contradicts published baseline metadata, refuse."""
    bundle = _bundle_with_registered_cell()
    client = _client_with_populated_sources(bundle, original_demand=100_000.0)

    response = _record_outcome(
        client,
        outcome_request(
            cell_id="cell-hz004-00",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            store_ids=("store-1", "store-2"),
            original_demand=250_000.0,
            daily_revenue=500.0,
        ),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_INPUT_REFUSED"
    assert "does not match published demand baseline" in response.json()["detail"]["message"]


def test_recording_outcome_refuses_when_registered_cell_has_invalid_h3_index() -> None:
    """If registered cell has an invalid/malformed H3 index, refuse fail-closed (422)."""
    bundle = _bundle_with_registered_cell(h3_index="NOT-AN-H3-IDX")
    client = _client_with_populated_sources(bundle, h3_index="NOT-AN-H3-IDX")

    response = _record_outcome(
        client,
        outcome_request(
            cell_id="cell-hz004-00",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            store_ids=("store-1", "store-2"),
            original_demand=100_000.0,
            daily_revenue=500.0,
        ),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_INPUT_REFUSED"
    assert "invalid H3 index" in response.json()["detail"]["message"]



