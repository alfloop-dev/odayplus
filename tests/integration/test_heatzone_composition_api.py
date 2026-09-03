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

import uuid

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.heatzone.domain.composition import (
    CompositionKind,
    HeatZoneCompositionRecord,
)
from shared.infrastructure.persistence import build_persistence
from tests.integration._authz import HEATZONE_HEADERS
from tests.integration._heatzone_evidence import (
    MERGE_LEFT,
    MERGE_RIGHT,
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
