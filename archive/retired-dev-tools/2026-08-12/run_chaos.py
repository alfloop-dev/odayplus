#!/usr/bin/env python3
"""Run chaos testing simulation and verify recovery behavior."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.external_data.geo.pipeline import NormalizedAddress
from modules.external_data.providers import (
    PrimaryGeocodeProvider,
)
from tests.reliability.test_concurrency_recovery import MockGeocodeClient

EVIDENCE_DIR = ROOT / "docs/evidence/completion/ODP-PGAP-RELIABILITY-001"


def main() -> int:
    print("Starting Chaos Simulation Drill...")

    # 1. Instantiate provider and mock client
    client = MockGeocodeClient()
    # Mode fixture to skip real authentication check
    provider = PrimaryGeocodeProvider(client=client, mode="fixture", retry_budget=2)

    states = ["outage", "quota_exceeded", "malformed", "healthy"]
    report_events = []

    for state in states:
        client.state = state
        client.call_count = 0
        t0 = time.perf_counter()

        passed = False
        error_msg = ""

        try:
            res = provider.lookup(NormalizedAddress(normalized_address="123 Main St", raw_address="123 Main St"))
            if res is not None and res.latitude == 37.7749:
                passed = True
        except Exception as e:
            error_msg = str(e)
            passed = False

        duration = time.perf_counter() - t0
        print(
            f"- State: {state:<15} | Calls: {client.call_count} | Duration: {duration:.4f}s | Result: {'PASSED' if passed else 'QUARANTINED'} ({error_msg})"
        )

        report_events.append(
            {
                "state": state,
                "calls_attempted": client.call_count,
                "duration_seconds": duration,
                "outcome": "success" if passed else "quarantined",
                "error": error_msg,
            }
        )

    # Assert that all non-healthy states quarantine, and healthy passes
    passed_all = (
        report_events[0]["outcome"] == "quarantined"
        and report_events[1]["outcome"] == "quarantined"
        and report_events[2]["outcome"] == "quarantined"
        and report_events[3]["outcome"] == "success"
    )

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    report_file = EVIDENCE_DIR / "chaos_drill_report.json"
    report_file.write_text(
        json.dumps(
            {"timestamp": time.time(), "events": report_events, "passed": passed_all}, indent=2
        )
    )

    print(f"\nOverall Chaos Drill Result: {'SUCCESS' if passed_all else 'FAILED'}")
    return 0 if passed_all else 1


if __name__ == "__main__":
    sys.exit(main())
