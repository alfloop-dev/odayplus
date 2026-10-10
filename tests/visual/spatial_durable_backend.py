"""Isolated SQLite Spatial browser-test backend; NEVER a live maturity receipt.

Uses the existing integration suite's generated absorption history and matured
inventory seam. Production routers, engine, authorization, persistence and audit
remain unmocked. Only this test factory redirects the inventory loader. Requires
an explicit scratch directory; never reads/writes the canonical database.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch

from modules.heatzone.application import merge_split_evidence
from modules.heatzone.domain.composition import (
    CompositionKind,
    HeatZoneCompositionRecord,
    generate_merged_zone_id,
)
from shared.infrastructure.persistence import build_persistence
from tests.integration._heatzone_evidence import (
    SPLIT_LEFT,
    SPLIT_RIGHT,
    add_barrier_evidence,
    build_evidence_repository,
    matured_receipt,
)


def scratch_root() -> Path:
    root = Path(os.environ["NETWORK_SPATIAL_DURABLE_DIR"]).resolve()
    if not root.is_dir():
        raise RuntimeError("Create an isolated scratch directory before starting")
    return root


def test_bundle(database: Path):
    # The public factory selects WORM from environment, not a constructor arg.
    # Scope the override to construction and explicitly forbid a cloud sink.
    with patch.dict(os.environ, {
        "ODP_AUDIT_WORM_SINK_URI": "",
        "ODP_AUDIT_WORM_LOCAL_PATH": str(scratch_root() / "audit-worm"),
    }):
        return build_persistence(mode="durable", db_path=database)


def seed_generated_history(bundle) -> None:
    """Same isolated SQL fixture path as the existing durable integration test.

    The production evidence reader is intentionally read-only. Do not add a
    writer to it or submit caller-supplied maturity to the evaluate endpoint.
    """
    reference = build_evidence_repository(tenant_id="tenant-a")
    mode = os.environ.get("NETWORK_SPATIAL_COMPOSITION", "merge")
    if mode not in {"merge", "split"}:
        raise RuntimeError("NETWORK_SPATIAL_COMPOSITION must be merge or split")
    if mode == "split":
        # Explicit synthetic parent and side-labelled history, not an observed
        # production topology or a caller-supplied readiness verdict.
        add_barrier_evidence(reference, tenant_id="tenant-a")
        parent_id = generate_merged_zone_id((SPLIT_LEFT, SPLIT_RIGHT))
        for cell_id in (SPLIT_LEFT, SPLIT_RIGHT):
            bundle.heatzone_composition_repository.save_composition(
                HeatZoneCompositionRecord(
                    zone_id=parent_id,
                    tenant_id="tenant-a",
                    member_cell_id=cell_id,
                    composition_kind=CompositionKind.MERGED,
                    decided_by="explicit-split-parent-fixture",
                    decision_policy_version_id="heatzone-merge-v1:tenant-a",
                )
            )
    for cell in reference.list_cells("tenant-a"):
        bundle.engine.execute(
            "INSERT INTO h3_cells (geo_cell_id, h3_index, centroid_latitude, "
            "centroid_longitude, admin_city, admin_district) VALUES (?, ?, 25.03, 121.56, ?, ?)",
            (cell.cell_id, cell.h3_index, cell.admin_city, cell.admin_district),
        )
    for index, outcome in enumerate(reference.list_absorption_outcomes("tenant-a")):
        bundle.engine.execute(
            "INSERT INTO heatzone_absorption_outcomes (outcome_id, tenant_id, geo_cell_id, "
            "period_start, period_end, original_demand, absorbed_demand, remaining_demand, "
            "absorption_ratio, absorbing_store_count, under_realized, barrier_side, "
            "barrier_description, basis_source_ids, basis_at, absorption_policy_version_id, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"fixture-{index}", "tenant-a", outcome.cell_id,
             outcome.period_start.isoformat(), outcome.period_end.isoformat(),
             outcome.original_demand, outcome.absorbed_demand, outcome.remaining_demand,
             outcome.absorption_ratio, outcome.absorbing_store_count, int(outcome.under_realized),
             outcome.barrier_side, outcome.barrier_description,
             json.dumps(list(outcome.basis_source_ids)), outcome.basis_at.isoformat(),
             outcome.absorption_policy_version_id, outcome.basis_at.isoformat()),
        )
    for index, (left, right) in enumerate(reference.list_adjacency("tenant-a")):
        bundle.engine.execute(
            "INSERT INTO h3_cell_adjacency (adjacency_id, cell_id, neighbor_cell_id, k_ring) "
            "VALUES (?, ?, ?, 1)", (f"fixture-edge-{index}", left, right),
        )


def create_test_app():
    from apps.api.oday_api.main import create_app

    root = scratch_root()
    database = root / "spatial.sqlite3"
    # Refuse stale data instead of silently treating a previous run as evidence.
    if database.exists():
        raise RuntimeError("Spatial test backend requires a fresh scratch directory")
    bundle = test_bundle(database)
    seed_generated_history(bundle)
    receipt = matured_receipt(root / "fixture-matured-inventory.json")
    real_loader = merge_split_evidence.load_model_ready_receipt
    seam = patch.object(
        merge_split_evidence,
        "load_model_ready_receipt",
        lambda path=None: real_loader(path or receipt),
    )
    seam.start()
    app = create_app(persistence=bundle)
    # Keep the test-only seam alive; no production settings or gate files change.
    app.state.spatial_test_inventory_seam = seam
    return app


def inspect(proposal_id: str) -> dict:
    """Fresh process/repository reads, with no evidence seed or loader override."""
    database = scratch_root() / "spatial.sqlite3"
    if not database.is_file():
        raise RuntimeError("Missing test database")
    bundle = test_bundle(database)
    proposal = bundle.heatzone_composition_repository.get_proposal(proposal_id, "tenant-a")
    if proposal is None:
        raise RuntimeError("Proposal not persisted")
    parent_records = bundle.heatzone_composition_repository.get_composition(proposal.zone_id, "tenant-a")
    records = parent_records
    if proposal.composition_kind == CompositionKind.SPLIT_CHILD:
        records = [
            record
            for child_id in proposal.to_dict()["child_zone_ids"]
            for record in bundle.heatzone_composition_repository.get_composition(child_id, "tenant-a")
        ]
    events = [event.to_dict() for event in bundle.audit_log.list_events()
              if event.resource == f"heatzone/proposals/{proposal_id}"]
    chain = bundle.audit_log.verify_chain()
    return {
        "evidence_mode": "local-sqlite-generated-history-not-live",
        "fresh_process": True,
        "proposal": proposal.to_dict(),
        "compositions": [record.to_dict() for record in records],
        "parent_compositions": [record.to_dict() for record in parent_records],
        "events": events,
        "audit_chain": chain.to_dict(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", required=True)
    args = parser.parse_args()
    print(json.dumps(inspect(args.inspect), sort_keys=True))
