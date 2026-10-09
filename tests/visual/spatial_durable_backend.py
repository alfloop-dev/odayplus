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
from shared.infrastructure.persistence import build_persistence
from tests.integration._heatzone_evidence import (
    build_evidence_repository,
    matured_receipt,
)


def scratch_root() -> Path:
    root = Path(os.environ["NETWORK_SPATIAL_DURABLE_DIR"]).resolve()
    if not root.is_dir():
        raise RuntimeError("Create an isolated scratch directory before starting")
    return root


def seed_generated_history(bundle) -> None:
    """Same isolated SQL fixture path as the existing durable integration test.

    The production evidence reader is intentionally read-only. Do not add a
    writer to it or submit caller-supplied maturity to the evaluate endpoint.
    """
    reference = build_evidence_repository(tenant_id="tenant-a")
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
    bundle = build_persistence(mode="durable", db_path=database)
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
    bundle = build_persistence(mode="durable", db_path=database)
    proposal = bundle.heatzone_composition_repository.get_proposal(proposal_id, "tenant-a")
    if proposal is None:
        raise RuntimeError("Proposal not persisted")
    records = bundle.heatzone_composition_repository.get_composition(proposal.zone_id, "tenant-a")
    events = [event.to_dict() for event in bundle.audit_log.list_events()
              if event.resource == f"heatzone/proposals/{proposal_id}"]
    chain = bundle.audit_log.verify_chain()
    return {
        "evidence_mode": "local-sqlite-generated-history-not-live",
        "fresh_process": True,
        "proposal": proposal.to_dict(),
        "compositions": [record.to_dict() for record in records],
        "events": events,
        "audit_chain": chain.to_dict(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", required=True)
    args = parser.parse_args()
    print(json.dumps(inspect(args.inspect), sort_keys=True))
