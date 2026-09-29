#!/usr/bin/env python3
"""Check the NFR runtime disposition against its own receipts.

Offline: reads only committed files. It fails when
  * the readback receipt does not hash to the recorded digest;
  * an item lacks environment / command / threshold / window / owner /
    next review date, or leaves BLOCKED_BY_EVIDENCE without a runtime
    receipt that exists in the repo;
  * the rollout precondition in disposition.json disagrees with what the
    readback receipt shows (e.g. claims "not deployed" while a deploy job
    succeeded);
  * the SHARED-008 partial claim disagrees with the fingerprint comparison;
  * the receipt carries a raw identity or secret value;
  * the Markdown summary disagrees with disposition.json, or its prose
    makes a waiver / handback claim the governance gate would reject.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO))

from delivery_toolchain.governance.check_requirement_members import (  # noqa: E402
    find_handback_claim,
    find_nonimplementation_claim,
)

DISPOSITION = HERE / "disposition.json"
SUMMARY = REPO / "docs/evidence/ODP_NFR_RUNTIME_DISPOSITION_2026-09-03.md"
DEPLOY_JOB = "Deploy the admitted artifact by immutable digest"
REQUIRED_FIELDS = (
    "environments",
    "threshold",
    "command_or_query",
    "time_window",
    "evidence_owner",
    "next_review_date",
    "reopen_trigger",
)
EXPECTED_ITEMS = {
    "ODP-FR-SHARED-008",
    "ODP-NFR-PERF-001",
    "ODP-NFR-BATCH-002",
    "ODP-NFR-AVAIL-003",
    "ODP-NFR-RPO-004",
}
RAW_VALUE_PATTERN = re.compile(
    r"gserviceaccount\.com|projects/\d+/|secretmanager\.googleapis|postgres(ql)?://"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deploy_succeeded(readback: dict) -> dict[str, bool]:
    calls = readback["calls"]
    result = {}
    for env in ("dev", "staging", "production"):
        succeeded = False
        for call in calls.get(f"success_deployment_runs_{env}", {}).values():
            jobs = json.loads(call["stdout"])["jobs"]
            succeeded |= any(j["name"] == DEPLOY_JOB and j["conclusion"] == "success" for j in jobs)
        result[env] = succeeded
    latest = json.loads(calls["latest_run_jobs"]["stdout"])["jobs"]
    if any(j["name"] == DEPLOY_JOB and j["conclusion"] == "success" for j in latest):
        # The latest run's environment is not in its job list; any success
        # there invalidates the "nothing deployed" claim outright.
        result["latest_run"] = True
    return result


def main() -> int:
    errors: list[str] = []
    disposition = json.loads(DISPOSITION.read_text())

    receipt_path = REPO / disposition["readback_receipt"]["path"]
    if sha256(receipt_path) != disposition["readback_receipt"]["sha256"]:
        errors.append("rollout-readback.json does not match its recorded sha256")
    raw_receipt = receipt_path.read_text()
    if RAW_VALUE_PATTERN.search(raw_receipt):
        errors.append("rollout-readback.json carries a raw identity or secret value")
    readback = json.loads(raw_receipt)

    for prior in disposition["prior_receipts"]:
        if sha256(REPO / prior["path"]) != prior["sha256"]:
            errors.append(f"prior receipt hash mismatch: {prior['path']}")

    failed_calls = [
        name
        for name, call in readback["calls"].items()
        if isinstance(call, dict) and "exit_code" in call and call["exit_code"] != 0
    ]
    if failed_calls:
        errors.append(f"readback has failed calls: {failed_calls}")

    deployed = deploy_succeeded(readback)
    production_deployments = json.loads(readback["calls"]["deployments_production"]["stdout"])
    if any(deployed.values()) and not disposition["rollout_precondition"]["met"]:
        errors.append(f"receipt shows a successful deploy job {deployed} but met=false")
    if (
        production_deployments
        and "zero" in disposition["rollout_precondition"]["observed"]["production"]
    ):
        errors.append("receipt has production deployments but disposition says zero")

    items = {item["requirement_id"]: item for item in disposition["items"]}
    if set(items) != EXPECTED_ITEMS:
        errors.append(f"items {sorted(items)} != {sorted(EXPECTED_ITEMS)}")
    for rid, item in items.items():
        for field in REQUIRED_FIELDS:
            if not item.get(field):
                errors.append(f"{rid}: missing {field}")
        if item["state"] != "BLOCKED_BY_EVIDENCE":
            receipts = item.get("runtime_receipts") or []
            if not receipts:
                errors.append(f"{rid}: state {item['state']} without runtime receipt")
            for ref in receipts:
                if not (REPO / ref).is_file():
                    errors.append(f"{rid}: runtime receipt {ref} does not exist")
        elif not item.get("evidence_needed"):
            errors.append(f"{rid}: BLOCKED_BY_EVIDENCE without evidence_needed")

    comparison = readback["identity_reference_comparison"]
    shared = {k: v["shared_with_production"] for k, v in comparison.items()}
    if any(shared.values()):
        errors.append(f"receipt shows references shared with production: {shared}")
    dev_staging_same = sorted(
        k
        for k, v in comparison.items()
        if v["fingerprints"]["dev"] and v["fingerprints"]["dev"] == v["fingerprints"]["staging"]
    )
    disclosed = items["ODP-FR-SHARED-008"]["observed_partial"]["disclosed"]
    for key in dev_staging_same:
        if key not in disclosed:
            errors.append(f"dev/staging share {key} but it is not disclosed")

    summary = SUMMARY.read_text()
    for rid, item in items.items():
        row = next(
            (ln for ln in summary.splitlines() if f"`{rid}`" in ln and ln.startswith("|")), None
        )
        if row is None or f"`{item['state']}`" not in row:
            errors.append(f"summary row for {rid} missing or disagrees on state")
        if row is not None and item["next_review_date"] not in row:
            errors.append(f"summary row for {rid} disagrees on next_review_date")
    if disposition["readback_receipt"]["sha256"] not in summary:
        errors.append("summary does not cite the readback receipt digest")
    for text in (summary, DISPOSITION.read_text()):
        claim = find_nonimplementation_claim(text) or find_handback_claim(text)
        if claim:
            errors.append(f"prose makes a governance claim the gate rejects: {claim!r}")

    for err in errors:
        print(f"FAIL {err}")
    if errors:
        return 1
    print(
        "PASS: 5 items BLOCKED_BY_EVIDENCE, each with environment/command/threshold/"
        f"window/owner/next review; deploy success per env {deployed}; "
        f"no reference shared with production; dev/staging shared {dev_staging_same}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
