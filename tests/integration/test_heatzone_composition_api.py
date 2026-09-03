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
from dataclasses import replace
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.heatzone.domain.composition import (
    CompositionKind,
    HeatZoneCompositionRecord,
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
from tests.integration._heatzone_absorption_rows import outcome_request
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


def _record_outcome(client: TestClient, body: dict, *, headers=ABSORPTION_HEADERS):
    return client.post(
        "/api/v1/heatzones/absorption/outcomes", json=body, headers=headers
    )


def _bundle_with_registered_cell(cell_id: str = HZ004_CELL):
    """A bundle whose geo pipeline has published the cell, and nothing else.

    Registering a cell is identity, not evidence: it is what makes
    `geo_cell_id` resolvable, which PostgreSQL requires through a foreign key.
    No absorption history is written here -- that is what the route under test
    is for.
    """
    bundle = build_persistence(mode="memory")
    bundle.heatzone_evidence_repository.register_cell(
        TENANT_ID, CellRegistration(cell_id, f"8a{cell_id}", "Taipei", "Xinyi")
    )
    return bundle


def test_recording_an_absorption_outcome_measures_it_server_side() -> None:
    """The route computes the outcome; the request only names the inputs."""
    bundle = _bundle_with_registered_cell()
    client = TestClient(create_app(persistence=bundle))

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
    client = TestClient(create_app(persistence=_bundle_with_registered_cell()))

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
    client = TestClient(create_app(persistence=bundle))

    body = outcome_request(
        cell_id="cell-hz004-00", window_start=WINDOW_START, window_end=WINDOW_END
    )
    for row in body["performances"]:
        row["raw_contract_fingerprint"] = ""

    response = _record_outcome(client, body)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_NOT_MEASURABLE"
    assert bundle.heatzone_evidence_repository.list_absorption_outcomes(TENANT_ID) == []


def test_an_incomplete_window_is_refused_rather_than_recorded() -> None:
    """A partial period would look measured and would not be."""
    bundle = _bundle_with_registered_cell()
    client = TestClient(create_app(persistence=bundle))

    body = outcome_request(
        cell_id="cell-hz004-00", window_start=WINDOW_START, window_end=WINDOW_END
    )
    # Drop one business day; the assembler must fail closed on the gap rather
    # than sum what is left and call it the period's absorption.
    body["performances"] = [
        row for row in body["performances"] if not row["business_date"].endswith("-15")
    ]

    response = _record_outcome(client, body)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HZ004_NOT_MEASURABLE"
    assert bundle.heatzone_evidence_repository.list_absorption_outcomes(TENANT_ID) == []


def test_a_declared_start_date_is_refused_under_the_seeded_policy() -> None:
    """Admitting a claimed start date is a governance decision, not a default."""
    bundle = _bundle_with_registered_cell()
    client = TestClient(create_app(persistence=bundle))

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
    client = TestClient(create_app(persistence=bundle))

    body = outcome_request(
        cell_id="cell-hz004-00", window_start=WINDOW_START, window_end=WINDOW_END
    )
    assert _record_outcome(client, body).status_code == 200
    assert _record_outcome(client, body).status_code == 200
    assert len(bundle.heatzone_evidence_repository.list_absorption_outcomes(TENANT_ID)) == 1

    # A recomputation that disagrees is a finding, not an update.
    conflicting = outcome_request(
        cell_id="cell-hz004-00",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        daily_revenue=900.0,
    )
    response = _record_outcome(client, conflicting)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "HZ004_OUTCOME_CONFLICT"
    stored = bundle.heatzone_evidence_repository.list_absorption_outcomes(TENANT_ID)
    assert [o.absorbed_demand for o in stored] == [28_000.0]


def test_a_side_labelled_outcome_needs_the_barrier_it_was_split_on() -> None:
    """Side labels are the only admissible basis for a split, so they carry one.

    A side without a recorded barrier would let a split be taken on a division
    nobody observed, which is the guess the readiness ruling forbids.
    """
    bundle = _bundle_with_registered_cell()
    client = TestClient(create_app(persistence=bundle))

    unnamed = _record_outcome(
        client,
        outcome_request(
            cell_id=HZ004_CELL,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            barrier_side="A",
        ),
    )
    assert unnamed.status_code == 422
    assert unnamed.json()["detail"]["code"] == "HZ004_OUTCOME_REFUSED"
    assert bundle.heatzone_evidence_repository.list_absorption_outcomes(TENANT_ID) == []

    named = _record_outcome(
        client,
        outcome_request(
            cell_id=HZ004_CELL,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            barrier_side="A",
            barrier_description="Love River, no crossing within the zone",
        ),
    )
    assert named.status_code == 200, named.text
    assert named.json()["barrier_side"] == "A"

    stored = bundle.heatzone_evidence_repository.list_cells(TENANT_ID)
    assert [o.barrier_side for o in stored[0].side_outcomes] == ["A"]

    # A side outside the recorded pair is not a side.
    invalid = _record_outcome(
        client,
        outcome_request(
            cell_id=HZ004_CELL,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            barrier_side="C",
            barrier_description="Love River, no crossing within the zone",
        ),
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "HZ004_OUTCOME_REFUSED"


def test_an_outcome_for_an_unpublished_cell_is_refused() -> None:
    """`geo_cell_id` is a foreign key, so an invented cell id is not a cell.

    Without this the SQLite and PostgreSQL paths would disagree: Postgres
    refuses the insert, SQLite would accept a row the evidence reader's join
    then silently drops, so the outcome would look recorded and never be read.
    """
    bundle = _bundle_with_registered_cell()
    client = TestClient(create_app(persistence=bundle))

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

    The two roles checked here are exactly the ones granted heatzone approval
    and override, so this is the separation itself rather than a sample of it.
    """
    client = TestClient(create_app(persistence=_bundle_with_registered_cell()))
    body = outcome_request(
        cell_id="cell-hz004-00", window_start=WINDOW_START, window_end=WINDOW_END
    )

    for role in (Role.EXPANSION_USER, Role.EXECUTIVE):
        response = _record_outcome(
            client,
            body,
            headers={
                **auth_headers(role, subject="operator-a"),
                "x-tenant-id": TENANT_ID,
            },
        )
        assert response.status_code == 403, role


def test_recorded_outcomes_reach_the_merge_split_engine(monkeypatch, tmp_path) -> None:
    """The written history is the history evaluate reads.

    Recording is the production entry, so an outcome written through the route
    has to be visible to a later evaluate on the same bundle -- otherwise the
    writer fills a relation the engine is not reading.
    """
    bundle = _bundle_with_registered_cell()
    use_matured_receipt(monkeypatch, tmp_path)
    client = TestClient(create_app(persistence=bundle))

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
