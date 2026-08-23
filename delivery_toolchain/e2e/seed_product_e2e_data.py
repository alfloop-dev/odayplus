#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CORRELATION_ID = "corr-product-e2e-seed-001"
TENANT_ID = "tenant-a"

# ---------------------------------------------------------------------------
# Cutover switch (ODP-XR-CUTOVER-ACTIVATE-002)
# ---------------------------------------------------------------------------
# `modules.external_data.application.market_data_facade` owns this contract.
# This script is launched by run_product_e2e.sh with a bare `python3` and no
# repository on sys.path, so it restates the vocabulary instead of importing it.
# tests/integration/test_external_data_cutover_prep.py pins these names against
# the facade's constants so the restatement cannot drift.
FACADE_MODE_ENV = "ODAY_MARKET_DATA_FACADE_MODE"
KILL_SWITCH_ENV = "ODAY_MARKET_DATA_KILL_SWITCH_ACTIVE"
LEGACY_FETCH_MODES = frozenset({"LEGACY_ONLY", "LEGACY_FALLBACK", "DUAL_RUN"})
DEFAULT_CUTOVER_MODE = "PLATFORM_PRIMARY"
KILL_SWITCH_TRUTHY = frozenset({"1", "true", "yes", "on"})


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed deterministic product E2E data.")
    parser.add_argument("--api-url", default="http://127.0.0.1:8099")
    parser.add_argument("--source-stub-url", default="http://127.0.0.1:8077")
    parser.add_argument("--web-url")
    parser.add_argument("--diagnostics-dir", default=".odp_data/e2e-diagnostics")
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait for API, source stub, and the optional web URL readiness.",
    )
    args = parser.parse_args()

    api_url = args.api_url.rstrip("/")
    source_stub_url = args.source_stub_url.rstrip("/")
    diagnostics_dir = Path(args.diagnostics_dir)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    if args.wait:
        wait_for_url(f"{api_url}/platform/health")
        wait_for_url(f"{source_stub_url}/external/listing_raw_snapshot.valid.json")
        if args.web_url:
            wait_for_http_url(args.web_url)

    source_fixture = get_json(f"{source_stub_url}/external/listing_raw_snapshot.valid.json")
    health = get_json(f"{api_url}/platform/health")
    if legacy_ingestion_trigger_available():
        ingestion_run = seed_tenant_ingestion(api_url)
        freshness = wait_for_persisted_freshness(api_url)
    else:
        # The trigger is retired in this mode and answers 410. Calling it to
        # find that out would make the seed the one caller that still asks a
        # decommissioned deployment to fetch, so it is not called at all.
        ingestion_run = None
        freshness = wait_for_platform_freshness(api_url)
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
                    "audit_event_ids": [evt_id for evt_id in [avm_case.get("audit_event_id"), queued_job.get("audit_event_id")] if evt_id],
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
        "external_freshness": freshness,
        "external_freshness_source": freshness.get("availability", {}).get("source"),
        "external_ingestion_run_id": ingestion_run["run_id"] if ingestion_run else None,
        "cutover_mode": resolve_cutover_mode(),
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


def resolve_cutover_mode(env: dict[str, str] | None = None) -> str:
    """Effective cutover mode, mirroring the facade's precedence rules.

    The kill switch wins over the configured mode and always returns
    ``LEGACY_ONLY``, while an unset mode defaults to ``PLATFORM_PRIMARY``.
    """
    source = os.environ if env is None else env
    if str(source.get(KILL_SWITCH_ENV, "") or "").strip().lower() in KILL_SWITCH_TRUTHY:
        return "LEGACY_ONLY"
    raw = str(source.get(FACADE_MODE_ENV, "") or "").strip().upper()
    return raw or DEFAULT_CUTOVER_MODE


def legacy_ingestion_trigger_available(env: dict[str, str] | None = None) -> bool:
    """True while ``POST /external-data/ingestion-runs`` still accepts a trigger."""
    return resolve_cutover_mode(env) in LEGACY_FETCH_MODES


def wait_for_platform_freshness(
    api_url: str, *, timeout_seconds: float = 180
) -> dict[str, Any]:
    """Wait until freshness is served from the published data-platform snapshot.

    The cut-over counterpart of :func:`wait_for_persisted_freshness`. Seeding
    cannot create this evidence -- the platform publishes it -- so the seed waits
    for the deployment to read a real released snapshot and fails loudly if it
    never does, rather than recording an empty run as success.
    """
    deadline = time.time() + timeout_seconds
    last: dict[str, Any] | None = None
    while True:
        last = get_json(f"{api_url}/external-data/freshness")
        availability = last.get("availability", {})
        if availability.get("source") == "data_platform" and last.get("freshness"):
            return last
        if time.time() >= deadline:
            break
        time.sleep(2)
    raise RuntimeError(
        "timed out waiting for data-platform external-data freshness: the cut-over "
        "deployment read no published platform snapshot. Last response: "
        + json.dumps(last, sort_keys=True)
    )


def seed_tenant_ingestion(api_url: str) -> dict[str, Any]:
    """Create deterministic evidence through the tenant-scoped product API."""

    return post_json(
        f"{api_url}/external-data/ingestion-runs",
        {
            "provider_id": "listing.partner_feed",
            "schedule_id": "product-e2e-seed",
            "window_start": "2026-06-28T08:00:00Z",
            "window_end": "2026-06-28T09:00:00Z",
            "idempotency_key": "product-e2e-external-ingestion-001",
        },
    )


def wait_for_persisted_freshness(
    api_url: str, *, timeout_seconds: float = 180
) -> dict[str, Any]:
    """Wait until the tenant-scoped ingestion API has persisted its evidence.

    The seed writes an `IngestionRunRecord` through the public API, so
    `/external-data/freshness` must read durable evidence from that same tenant
    partition instead of the poc fixture fallback that only applies while the
    partition is empty.
    """
    deadline = time.time() + timeout_seconds
    last: dict[str, Any] | None = None
    while True:
        last = get_json(f"{api_url}/external-data/freshness")
        if last.get("availability", {}).get("source") == "persisted":
            return last
        if time.time() >= deadline:
            break
        time.sleep(2)
    raise RuntimeError(
        "timed out waiting for persisted external-data freshness: the tenant-scoped "
        "ingestion API wrote no readable run. Last response: "
        + json.dumps(last, sort_keys=True)
    )


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


def wait_for_http_url(url: str, *, timeout_seconds: int = 120) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=10) as response:
                if 200 <= response.status < 400:
                    return
        except (HTTPError, URLError, OSError) as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")


def get_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "x-correlation-id": CORRELATION_ID,
            "x-subject-id": "product-e2e-seed",
            "x-tenant-id": TENANT_ID,
            "x-roles": "finance_legal,expansion_user,operations_manager,regional_supervisor,site_reviewer,data_owner,auditor,executive",
        }
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-correlation-id": CORRELATION_ID,
            "x-subject-id": "product-e2e-seed",
            "x-tenant-id": TENANT_ID,
            "x-roles": "finance_legal,expansion_user,operations_manager,regional_supervisor,site_reviewer,data_owner,auditor,executive",
        },
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
