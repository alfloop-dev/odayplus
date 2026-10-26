"""Attempt-bound, secret-free proof of pre-deploy Scheduler configuration."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_ops.deployment.cloud_scheduler_trigger import redact_snapshot, validate_snapshot

KIND = "dev-scheduler-baseline-v1"
BINDINGS = (
    "ODP_DEPLOY_ENV", "ODAY_RELEASE_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
    "GCP_PROJECT", "GCP_REGION", "API_SERVICE", "WEB_SERVICE", "MIGRATION_JOB",
    "WORKER_JOB", "SCHEDULER_JOB", "WORKER_SCHEDULE_NAME", "SCHEDULER_SCHEDULE_NAME",
)


def scope(env: dict[str, str]) -> dict[str, str]:
    bound = {key: env.get(key, "") for key in BINDINGS}
    if (not all(bound.values()) or bound["ODP_DEPLOY_ENV"] != "dev"
            or not re.fullmatch(r"[0-9a-f]{40}", bound["ODAY_RELEASE_SHA"])
            or any(not re.fullmatch(r"[1-9][0-9]*", bound[key])
                   for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
            or bound["WORKER_SCHEDULE_NAME"] == bound["SCHEDULER_SCHEDULE_NAME"]):
        raise ValueError("Scheduler proof requires distinct triggers and exact dev attempt bindings.")
    return bound


def descriptions(bound: dict[str, str], read: Callable[..., Any]) -> dict[str, dict[str, Any]]:
    found = {}
    for key in ("SCHEDULER_SCHEDULE_NAME", "WORKER_SCHEDULE_NAME"):
        trigger = bound[key]
        listed = read("scheduler", "jobs", "list", f"--project={bound['GCP_PROJECT']}",
                      f"--location={bound['GCP_REGION']}", f"--filter=name:{trigger}")
        prefix = f"projects/{bound['GCP_PROJECT']}/locations/{bound['GCP_REGION']}/jobs/"
        if not isinstance(listed, list) or any(
                not isinstance(job, dict) or not isinstance(job.get("name"), str)
                or not job["name"].startswith(prefix) or job["name"] == prefix for job in listed):
            raise ValueError("Scheduler configuration readback is incomplete.")
        matches = [job for job in listed
                   if str(job.get("name", "")).rsplit("/", 1)[-1] == trigger]
        expected_name = (f"projects/{bound['GCP_PROJECT']}/locations/"
                         f"{bound['GCP_REGION']}/jobs/{trigger}")
        if len(matches) > 1 or (matches and matches[0].get("name") != expected_name):
            raise ValueError("Scheduler configuration readback has ambiguous or wrong target scope.")
        found[trigger] = matches[0] if matches else {"exists": False}
    return found


def configuration(payload: dict[str, Any]) -> dict[str, Any]:
    validate_snapshot(payload)
    if payload.get("exists") is not False and payload.get("state") not in {"ENABLED", "PAUSED"}:
        raise ValueError("Scheduler pause policy is missing or cannot be proven.")
    # Reuse the actual restore helper's comparison semantics (including state,
    # auth, body, headers, schedule and retry policy). Never persist raw fields:
    # a header/body/URI may contain credentials even though the helper calls
    # its normalization 'redact_snapshot'.
    normalized = redact_snapshot(payload)
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()
    return {"exists": normalized["exists"], "configuration_sha256": digest}


def capture(env: dict[str, str], read: Callable[..., Any]) -> dict[str, Any]:
    bound = scope(env)
    return {"kind": KIND, "scope": bound,
            "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "triggers": {name: configuration(payload)
                         for name, payload in descriptions(bound, read).items()}}


def verify(env: dict[str, str], path: Path | None,
           read: Callable[..., Any]) -> tuple[str, dict[str, dict[str, Any]]]:
    if path is None or not path.is_file() or path.stat().st_size > 100_000:
        raise ValueError("Authoritative pre-deploy Scheduler baseline is missing.")
    raw = path.read_bytes()
    baseline = json.loads(raw)
    bound = scope(env)
    if (not isinstance(baseline, dict) or baseline.get("kind") != KIND
            or baseline.get("scope") != bound):
        raise ValueError("Scheduler baseline does not bind this exact dev target and attempt.")
    try:
        captured = datetime.fromisoformat(baseline["captured_at"])
        age = (datetime.now(UTC) - captured).total_seconds()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Scheduler baseline capture time is unproven.") from exc
    if not 0 <= age <= 6 * 3600:
        raise ValueError("Scheduler baseline is stale or dated after readback.")
    observed = descriptions(bound, read)
    if baseline.get("triggers") != {name: configuration(payload)
                                  for name, payload in observed.items()}:
        raise ValueError("Scheduler configuration was not restored to its pre-deploy state.")
    return hashlib.sha256(raw).hexdigest(), observed
