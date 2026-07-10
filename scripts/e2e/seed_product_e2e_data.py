#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CORRELATION_ID = "corr-product-e2e-seed-001"


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed deterministic product E2E data.")
    parser.add_argument("--api-url", default="http://127.0.0.1:8099")
    parser.add_argument("--source-stub-url", default="http://127.0.0.1:8077")
    parser.add_argument("--diagnostics-dir", default=".odp_data/e2e-diagnostics")
    parser.add_argument("--wait", action="store_true", help="Wait for API and source stub readiness.")
    args = parser.parse_args()

    api_url = args.api_url.rstrip("/")
    source_stub_url = args.source_stub_url.rstrip("/")
    diagnostics_dir = Path(args.diagnostics_dir)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    if args.wait:
        wait_for_url(f"{api_url}/platform/health")
        wait_for_url(f"{source_stub_url}/external/listing_raw_snapshot.valid.json")

    source_fixture = get_json(f"{source_stub_url}/external/listing_raw_snapshot.valid.json")
    health = get_json(f"{api_url}/platform/health")
    avm_case = post_json(
        f"{api_url}/avm/cases",
        {
            "store_id": "e2e-store-taipei-001",
            "gm_ttm": 3_200_000,
            "forecast_gm_next_12m": 3_450_000,
            "asset_book_value": 5_000_000,
            "equipment_fair_value": 1_850_000,
            "lease_liability": 600_000,
            "working_capital": 420_000,
            "comparable_multiples": [3.1, 3.5, 4.0],
            "liquidity_discount": 0.08,
            "quality_score": 0.92,
            "source_snapshot_ids": ["listing_raw_snapshot.valid", "store_master_snapshot.valid"],
            "prediction_origin_time": "2026-06-28T00:00:00Z",
            "created_by": "product-e2e-seed",
            "idempotency_key": "product-e2e-avm-case-001",
        },
    )
    heatzone_job = post_json(
        f"{api_url}/heatzones/score-jobs",
        {
            "idempotency_key": "product-e2e-heatzone-001",
            "prediction_origin_time": "2026-06-28T00:00:00Z",
            "features": [
                {
                    "h3_index": "8928308280fffff",
                    "h3_resolution": 9,
                    "poi_count": 144,
                    "competitor_count": 3,
                    "active_listing_count": 11,
                    "median_listing_rent": 128000,
                    "competitor_capacity": 0.32,
                    "average_confidence": 0.94,
                    "source_snapshot_ids": ["poi_snapshot.valid", "competitor_store_snapshot.valid"],
                    "existing_store_count": 1,
                    "admin_city": "Taipei",
                    "admin_district": "Da-an",
                },
                {
                    "h3_index": "89283082873ffff",
                    "h3_resolution": 9,
                    "poi_count": 61,
                    "competitor_count": 9,
                    "active_listing_count": 4,
                    "median_listing_rent": 188000,
                    "competitor_capacity": 0.76,
                    "average_confidence": 0.71,
                    "source_snapshot_ids": ["poi_snapshot.valid", "listing_raw_snapshot.valid"],
                    "existing_store_count": 2,
                    "admin_city": "Taipei",
                    "admin_district": "Xinyi",
                },
            ],
        },
    )
    queued_job = post_json(
        f"{api_url}/jobs",
        {
            "job_type": "product-e2e.scheduler.heartbeat",
            "payload": {"source": "seed_product_e2e_data", "case_id": avm_case["case_id"]},
            "idempotency_key": "product-e2e-scheduler-job-001",
        },
    )

    now = datetime.now(UTC)
    evidence_export = post_json(
        f"{api_url}/audit/evidence/export",
        {
            "program_id": "product-e2e-subsidy",
            "purpose": "product-grade-e2e-validation",
            "requested_by": "product-e2e-seed",
            "from_time": (now - timedelta(days=1)).isoformat(),
            "to_time": (now + timedelta(days=1)).isoformat(),
            "correlation_ids": [CORRELATION_ID],
            "export_scope": "internal-product-e2e",
            "environment": "e2e",
            "build_version": "local",
            "data_classification": "internal",
            "sensitive": False,
            "decision_cards": [
                {
                    "decision_id": "decision-product-e2e-001",
                    "decision_type": "site-approval",
                    "module": "expansion",
                    "title": "Approve deterministic Taipei E2E candidate",
                    "subject_ref": avm_case["case_id"],
                    "outcome": "approved_for_e2e",
                    "owner": "product-e2e-seed",
                    "decided_at": now.isoformat(),
                    "rationale": "Deterministic seed for product-grade E2E traceability.",
                    "input_snapshot_id": "snapshot-product-e2e-001",
                    "evidence_refs": ["listing_raw_snapshot.valid", heatzone_job["job_id"], avm_case["case_id"]],
                    "model_refs": ["heatzone-baseline-v1", "dealroom-avm-baseline-v1"],
                    "policy_refs": ["audit-evidence-export-policy-v1"],
                    "audit_event_ids": [avm_case["audit_event_id"], queued_job["audit_event_id"]],
                    "subsidy_requirements": ["ELIGIBILITY", "DECISION", "EFFECT", "CONTROL", "TRACE"],
                    "controls": ["seeded-durable-store", "fixture-source-stub", "audit-retention"],
                    "prediction_ref": heatzone_job["job_id"],
                    "recommendation_ref": "recommendation-product-e2e-001",
                    "approval_ref": "approval-product-e2e-001",
                    "execution_ref": queued_job["job_id"],
                    "outcome_ref": "outcome-product-e2e-001",
                    "feature_version": "geo-grid-view-v1",
                    "data_snapshot_id": "source-stub-fixtures-v1",
                    "artifact_hash": "sha256:product-e2e-seed",
                    "metrics": {"fixture_bytes": len(json.dumps(source_fixture, sort_keys=True))},
                }
            ],
        },
    )

    summary = {
        "seeded_at": now.isoformat(),
        "api": health,
        "source_fixture_keys": sorted(source_fixture.keys()),
        "avm_case_id": avm_case["case_id"],
        "heatzone_job_id": heatzone_job["job_id"],
        "scheduler_job_id": queued_job["job_id"],
        "evidence_export_id": evidence_export["export_id"],
        "correlation_id": CORRELATION_ID,
    }
    (diagnostics_dir / "seed-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def wait_for_url(url: str, *, timeout_seconds: int = 120) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            get_json(url)
            return
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")


def get_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-correlation-id": CORRELATION_ID,
        },
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
