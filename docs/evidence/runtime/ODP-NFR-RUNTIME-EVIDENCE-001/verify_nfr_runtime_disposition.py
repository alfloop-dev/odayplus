#!/usr/bin/env python3
"""Check the NFR runtime disposition against its own receipts.

Offline: reads only committed files. It fails when
  * the readback receipt does not hash to the recorded digest;
  * an item lacks environment / command / threshold / window / owner /
    next review date, or leaves BLOCKED_BY_EVIDENCE without a runtime
    receipt that exists in the repo;
  * any call in the receipt, at any nesting depth, exited non-zero, or a
    required call is missing;
  * the deployment history is incomplete: row count differs from the
    captured totalCount, a deployment's statuses are truncated, or a run
    that put a deployment into success/inactive has no complete job list;
  * the rollout precondition in disposition.json disagrees with the counts
    and runs recomputed from that complete history (e.g. claims "not
    deployed" while a deploy job succeeded);
  * any environment lacks a fingerprint for one of the 10 identity keys, or
    the SHARED-008 partial claim disagrees with equality derived from the
    fingerprints (the receipt's own shared_with_production is not trusted);
  * the receipt carries a raw identity or secret value;
  * the Markdown summary disagrees with disposition.json, or its prose
    makes a waiver / handback claim the governance gate would reject.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
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
RUNTIME_RELEASE = "Runtime Release"
ENVIRONMENTS = ("dev", "staging", "production")
DEPLOYED_STATES = {"SUCCESS", "INACTIVE"}
IDENTITY_KEYS = (
    "GCP_PROJECT_ID",
    "GCP_SERVICE_ACCOUNT",
    "GCP_WORKLOAD_IDENTITY_PROVIDER",
    "ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT",
    "ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT",
    "ODAY_DATABASE_URL_SECRET",
    "ODP_AUTH_PRINCIPAL_MAP_SECRET",
    "ODP_WEB_OIDC_CLIENT_SECRET_SECRET",
    "ODP_WEB_SESSION_SECRET_SECRET",
    "ODP_SNAPSHOT_BUCKET",
)
REQUIRED_CALLS = (
    "runtime_release_runs",
    "latest_run_jobs",
    "deployed_run_jobs",
    *(f"deployment_{kind}_{env}" for env in ENVIRONMENTS for kind in ("count", "history")),
    *(f"variables_{env}" for env in ENVIRONMENTS),
)
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


def failed_calls(node, path: str = "calls") -> list[str]:
    """Every nested call record whose exit code is not 0."""
    if isinstance(node, dict):
        if "exit_code" in node:
            return [] if node["exit_code"] == 0 else [path]
        return [p for k, v in node.items() for p in failed_calls(v, f"{path}.{k}")]
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in failed_calls(v, f"{path}[{i}]")]
    return []


def observed_rollout(calls: dict, errors: list[str]) -> dict[str, dict]:
    """Recompute the per-environment rollout facts from the full history."""
    run_jobs = {rid: json.loads(call["stdout"]) for rid, call in calls["deployed_run_jobs"].items()}
    for rid, body in run_jobs.items():
        if body["total_count"] != len(body["jobs"]):
            errors.append(f"run {rid}: job list truncated")
    observed = {}
    for env in ENVIRONMENTS:
        rows = [
            json.loads(ln) for ln in calls[f"deployment_history_{env}"]["stdout"].splitlines() if ln
        ]
        if len(rows) != int(calls[f"deployment_count_{env}"]["stdout"]):
            errors.append(f"{env}: deployment history incomplete")
        deployed = [r for r in rows if DEPLOYED_STATES & set(r["states"])]
        for row in rows:
            if row["status_total"] != len(row["states"]):
                errors.append(f"{env}: deployment {row['id']} statuses truncated")
        for row in deployed:
            if not row["run_ids"] or any(rid not in run_jobs for rid in row["run_ids"]):
                errors.append(f"{env}: deployment {row['id']} has an unresolved run")
        runs = sorted({rid for r in deployed for rid in r["run_ids"] if rid in run_jobs}, key=int)
        legacy_runs = [rid for rid in runs if run_jobs[rid]["workflow_name"] != RUNTIME_RELEASE]
        legacy_rows = [r for r in deployed if set(r["run_ids"]) & set(legacy_runs)]
        observed[env] = {
            "deployments_total": len(rows),
            "deployments_reached_success": len(deployed),
            "runs_resolved": len(runs),
            "runtime_release_runs": [r for r in runs if r not in legacy_runs],
            "admitted_deploy_success_runs": [
                rid
                for rid in runs
                if any(
                    j["name"] == DEPLOY_JOB and j["conclusion"] == "success"
                    for j in run_jobs[rid]["jobs"]
                )
            ],
            "legacy_workflow_runs": dict(
                sorted(Counter(run_jobs[rid]["workflow_name"] for rid in legacy_runs).items())
            ),
            "legacy_deployment_window": (
                [
                    min(r["created_at"] for r in legacy_rows),
                    max(r["created_at"] for r in legacy_rows),
                ]
                if legacy_rows
                else None
            ),
        }
    return observed


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

    calls = readback["calls"]
    missing = [name for name in REQUIRED_CALLS if name not in calls]
    if missing:
        errors.append(f"readback lacks required calls: {missing}")
        for err in errors:
            print(f"FAIL {err}")
        return 1
    failed = failed_calls(calls)
    if failed:
        errors.append(f"readback has failed calls: {failed}")

    observed = observed_rollout(calls, errors)
    precondition = disposition["rollout_precondition"]
    for env in ENVIRONMENTS:
        claimed = {k: v for k, v in precondition["observed"][env].items() if k != "summary"}
        if claimed != observed[env]:
            errors.append(
                f"rollout_precondition.observed.{env} {claimed} != receipt {observed[env]}"
            )
    latest = json.loads(calls["latest_run_jobs"]["stdout"])["jobs"]
    latest_deployed = any(j["name"] == DEPLOY_JOB and j["conclusion"] == "success" for j in latest)
    deployed = {env: bool(observed[env]["admitted_deploy_success_runs"]) for env in ENVIRONMENTS}
    if (any(deployed.values()) or latest_deployed) and not precondition["met"]:
        errors.append(f"receipt shows a successful deploy job {deployed} but met=false")

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
    if set(comparison) != set(IDENTITY_KEYS):
        errors.append(f"identity comparison keys {sorted(comparison)} != the 10 expected")
    for env in ENVIRONMENTS:
        pages = [json.loads(page["stdout"]) for page in calls[f"variables_{env}"]]
        names = {name for body in pages for name in body["names"]}
        total = pages[-1]["total_count"] if pages else None
        if len(names) != total:
            errors.append(f"variables_{env}: listed {len(names)} of {total}")
        absent = [k for k in IDENTITY_KEYS if k not in names]
        if absent:
            errors.append(f"variables_{env} does not list {absent}")
    fps = {k: comparison.get(k, {}).get("fingerprints", {}) for k in IDENTITY_KEYS}
    incomplete = sorted(k for k, fp in fps.items() if any(not fp.get(env) for env in ENVIRONMENTS))
    if incomplete:
        errors.append(f"identity fingerprints missing for {incomplete}")
    shared = {
        k: sorted(env for env in ("dev", "staging") if fp.get(env) and fp[env] == fp["production"])
        for k, fp in fps.items()
    }
    for key, envs in shared.items():
        if envs != comparison.get(key, {}).get("shared_with_production"):
            errors.append(f"{key}: recorded shared_with_production disagrees with fingerprints")
    if any(shared.values()):
        errors.append(f"receipt shows references shared with production: {shared}")
    dev_staging_same = sorted(
        k for k, fp in fps.items() if fp.get("dev") and fp["dev"] == fp.get("staging")
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
        f"window/owner/next review; admitted deploy success per env {deployed} over "
        f"{ {env: observed[env]['deployments_total'] for env in ENVIRONMENTS} } deployments; "
        f"no reference shared with production; dev/staging shared {dev_staging_same}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
