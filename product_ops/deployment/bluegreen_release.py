#!/usr/bin/env python3
"""Production blue-green release primitives.

This module implements the atomic operations needed for a blue-green
production release, as specified in §8 of
``EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md``.

Responsibilities
~~~~~~~~~~~~~~~~
- Green 0% tag smoke → atomic 100% traffic switch
- Scheduler pause / resume with job digest switching
- Data platform selector / snapshot pointer recovery
- Full rollback sequence

Design rules
~~~~~~~~~~~~
- Every operation is **idempotent**: calling it twice with the same inputs
  produces the same result without side effects.
- Every operation has a **dry-run** mode that logs what *would* happen
  without executing any mutations.
- All failures are **fail-closed**: partial states are never committed,
  and callers always receive explicit error reports.
- This module **does not modify the deploy workflow entrypoint**
  (``deploy_cloud_run_waji.sh`` / Runtime Release workflow).
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ServiceTarget:
    """Identifies a Cloud Run service for traffic operations."""

    service: str
    project: str
    region: str

    def gcloud_args(self) -> list[str]:
        return [
            f"--project={self.project}",
            f"--region={self.region}",
        ]


@dataclass(frozen=True)
class SchedulerTarget:
    """Identifies a Cloud Scheduler job for pause / resume / digest switch."""

    job_name: str
    project: str
    location: str

    def gcloud_args(self) -> list[str]:
        return [
            f"--project={self.project}",
            f"--location={self.location}",
        ]


@dataclass(frozen=True)
class DataPlatformPointer:
    """Snapshot pointer for data platform selector / snapshot recovery."""

    selector_label: str
    snapshot_id: str
    namespace: str


@dataclass
class ReleaseState:
    """Captures the pre-release state for rollback.

    ``to_json`` / ``from_json`` serialize this as a JSON receipt for
    auditing and automated rollback.
    """

    release_id: str
    blue_api_revision: str = ""
    blue_web_revision: str = ""
    green_api_revision: str = ""
    green_web_revision: str = ""
    api_traffic_snapshot_path: str = ""
    web_traffic_snapshot_path: str = ""
    scheduler_states: dict[str, str] = field(default_factory=dict)
    scheduler_digests: dict[str, str] = field(default_factory=dict)
    data_platform_pointer: dict[str, str] = field(default_factory=dict)
    data_platform_pointer_snapshot_path: str = ""
    switch_completed_at: str = ""
    rollback_completed_at: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "release_id": self.release_id,
                "blue_api_revision": self.blue_api_revision,
                "blue_web_revision": self.blue_web_revision,
                "green_api_revision": self.green_api_revision,
                "green_web_revision": self.green_web_revision,
                "api_traffic_snapshot_path": self.api_traffic_snapshot_path,
                "web_traffic_snapshot_path": self.web_traffic_snapshot_path,
                "scheduler_states": self.scheduler_states,
                "scheduler_digests": self.scheduler_digests,
                "data_platform_pointer": self.data_platform_pointer,
                "data_platform_pointer_snapshot_path": self.data_platform_pointer_snapshot_path,
                "switch_completed_at": self.switch_completed_at,
                "rollback_completed_at": self.rollback_completed_at,
            },
            indent=2,
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, text: str) -> "ReleaseState":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("ReleaseState JSON must be an object")
        return cls(
            release_id=data.get("release_id", ""),
            blue_api_revision=data.get("blue_api_revision", ""),
            blue_web_revision=data.get("blue_web_revision", ""),
            green_api_revision=data.get("green_api_revision", ""),
            green_web_revision=data.get("green_web_revision", ""),
            api_traffic_snapshot_path=data.get("api_traffic_snapshot_path", ""),
            web_traffic_snapshot_path=data.get("web_traffic_snapshot_path", ""),
            scheduler_states=data.get("scheduler_states", {}),
            scheduler_digests=data.get("scheduler_digests", {}),
            data_platform_pointer=data.get("data_platform_pointer", {}),
            data_platform_pointer_snapshot_path=data.get("data_platform_pointer_snapshot_path", ""),
            switch_completed_at=data.get("switch_completed_at", ""),
            rollback_completed_at=data.get("rollback_completed_at", ""),
        )


@dataclass
class OperationResult:
    """Result of a blue-green operation."""

    success: bool
    operation: str
    message: str
    dry_run: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "operation": self.operation,
            "message": self.message,
            "dry_run": self.dry_run,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Shell helpers (thin wrappers for testability)
# ---------------------------------------------------------------------------

def _run_gcloud(
    args: list[str],
    *,
    dry_run: bool = False,
    capture_json: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a gcloud command, or log it in dry-run mode.

    In dry-run mode the command is logged but not executed; a synthetic
    ``CompletedProcess`` with returncode 0 is returned instead.
    """
    cmd = ["gcloud"] + args
    logger.info("gcloud: %s%s", " ".join(cmd), " [DRY-RUN]" if dry_run else "")

    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, stdout="{}" if capture_json else "", stderr="")

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Tag resolution (Green 0% tag smoke verification)
# ---------------------------------------------------------------------------

def get_tagged_target_from_description(description: dict[str, Any], tag: str) -> tuple[str, str]:
    """Extract (revisionName, url) for a tag from a Cloud Run service description.

    Raises ValueError if tag is missing or malformed.
    """
    status = description.get("status")
    traffic = status.get("traffic") if isinstance(status, dict) else None
    if not isinstance(traffic, list):
        raise ValueError("Cloud Run service description is missing status.traffic")

    matches = [
        item for item in traffic if isinstance(item, dict) and str(item.get("tag") or "") == tag
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one Cloud Run traffic target for tag {tag!r}")

    target = matches[0]
    revision = str(target.get("revisionName") or "").strip()
    url = str(target.get("url") or "").strip()
    if not revision:
        raise ValueError(f"tag {tag!r} has no immutable revisionName")
    if not url.startswith("https://"):
        raise ValueError(f"tag {tag!r} has no HTTPS URL")
    return revision, url


def resolve_tagged_target(
    target: ServiceTarget,
    tag: str,
    *,
    dry_run: bool = False,
) -> OperationResult:
    """Resolve the revision and HTTPS URL for a given tag on a Cloud Run service.

    Used during green 0% smoke verification prior to 100% traffic switch.
    """
    if not tag:
        return OperationResult(
            success=False,
            operation="resolve_tagged_target",
            message="tag must be a non-empty string",
        )

    result = _run_gcloud(
        [
            "run", "services", "describe", target.service,
            *target.gcloud_args(),
            "--format=json",
        ],
        dry_run=dry_run,
        capture_json=True,
    )
    if dry_run:
        return OperationResult(
            success=True,
            operation="resolve_tagged_target",
            message=f"[DRY-RUN] Would resolve tag '{tag}' for {target.service}",
            dry_run=True,
            details={"tag": tag, "revision": f"{target.service}-{tag}-mock", "url": f"https://{tag}---{target.service}.run.app"},
        )

    if result.returncode != 0:
        return OperationResult(
            success=False,
            operation="resolve_tagged_target",
            message=f"Failed to describe {target.service}: {result.stderr.strip()}",
            details={"returncode": result.returncode},
        )

    try:
        description = json.loads(result.stdout)
        revision, url = get_tagged_target_from_description(description, tag)
    except Exception as exc:
        return OperationResult(
            success=False,
            operation="resolve_tagged_target",
            message=f"Failed to resolve tagged revision for {tag}: {exc}",
        )

    return OperationResult(
        success=True,
        operation="resolve_tagged_target",
        message=f"Resolved tag '{tag}' -> {revision} ({url})",
        details={"tag": tag, "revision": revision, "url": url},
    )


# ---------------------------------------------------------------------------
# Traffic operations
# ---------------------------------------------------------------------------

def capture_traffic_snapshot(
    target: ServiceTarget,
    output_path: Path,
    *,
    dry_run: bool = False,
) -> OperationResult:
    """Capture the current traffic allocation for a Cloud Run service.

    The snapshot is written as a JSON file suitable for ``restore_arg``
    in ``cloud_run_traffic.py``.
    """
    result = _run_gcloud(
        [
            "run", "services", "describe", target.service,
            *target.gcloud_args(),
            "--format=json",
        ],
        dry_run=dry_run,
        capture_json=True,
    )
    if dry_run:
        return OperationResult(
            success=True,
            operation="capture_traffic_snapshot",
            message=f"[DRY-RUN] Would capture {target.service} traffic to {output_path}",
            dry_run=True,
        )

    if result.returncode != 0:
        return OperationResult(
            success=False,
            operation="capture_traffic_snapshot",
            message=f"Failed to describe {target.service}: {result.stderr.strip()}",
            details={"returncode": result.returncode},
        )

    try:
        description = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return OperationResult(
            success=False,
            operation="capture_traffic_snapshot",
            message=f"Invalid JSON from gcloud describe: {exc}",
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(description, indent=2), encoding="utf-8")
    return OperationResult(
        success=True,
        operation="capture_traffic_snapshot",
        message=f"Captured {target.service} traffic snapshot to {output_path}",
        details={"path": str(output_path)},
    )


def atomic_traffic_switch(
    target: ServiceTarget,
    green_revision: str,
    *,
    dry_run: bool = False,
) -> OperationResult:
    """Atomically switch a Cloud Run service to 100% on ``green_revision``.

    This is a single ``gcloud run services update-traffic`` call that
    moves **all** traffic in one operation. There is no 10/90 canary
    step — the rollout plan §8.3 explicitly avoids long mixed-runs.
    """
    if not green_revision:
        return OperationResult(
            success=False,
            operation="atomic_traffic_switch",
            message="green_revision must be a non-empty string",
        )

    result = _run_gcloud(
        [
            "run", "services", "update-traffic", target.service,
            *target.gcloud_args(),
            f"--to-revisions={green_revision}=100",
            "--quiet",
        ],
        dry_run=dry_run,
    )
    if dry_run:
        return OperationResult(
            success=True,
            operation="atomic_traffic_switch",
            message=f"[DRY-RUN] Would switch {target.service} to {green_revision}=100%",
            dry_run=True,
            details={"revision": green_revision},
        )

    if result.returncode != 0:
        return OperationResult(
            success=False,
            operation="atomic_traffic_switch",
            message=f"Traffic switch failed for {target.service}: {result.stderr.strip()}",
            details={"returncode": result.returncode},
        )

    return OperationResult(
        success=True,
        operation="atomic_traffic_switch",
        message=f"Switched {target.service} to {green_revision}=100%",
        details={"revision": green_revision},
    )


def restore_traffic_from_snapshot(
    target: ServiceTarget,
    snapshot_path: Path,
    *,
    dry_run: bool = False,
    traffic_helper: str = "product_ops/deployment/cloud_run_traffic.py",
) -> OperationResult:
    """Restore traffic allocation from a previously captured snapshot.

    Uses ``cloud_run_traffic.py restore-arg`` to compute the gcloud
    ``--to-revisions`` argument from the snapshot.
    """
    if not snapshot_path.exists():
        return OperationResult(
            success=False,
            operation="restore_traffic_from_snapshot",
            message=f"Snapshot file not found: {snapshot_path}",
        )

    try:
        helper_result = subprocess.run(
            [sys.executable, traffic_helper, "restore-arg", f"--description={snapshot_path}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if helper_result.returncode != 0:
            return OperationResult(
                success=False,
                operation="restore_traffic_from_snapshot",
                message=f"Failed to compute restore-arg: {helper_result.stderr.strip()}",
            )
        traffic_arg = helper_result.stdout.strip()
    except Exception as exc:
        return OperationResult(
            success=False,
            operation="restore_traffic_from_snapshot",
            message=f"Exception computing restore-arg: {exc}",
        )

    if not traffic_arg:
        return OperationResult(
            success=False,
            operation="restore_traffic_from_snapshot",
            message="Empty traffic restore argument from snapshot",
        )

    result = _run_gcloud(
        [
            "run", "services", "update-traffic", target.service,
            *target.gcloud_args(),
            f"--to-revisions={traffic_arg}",
            "--quiet",
        ],
        dry_run=dry_run,
    )
    if dry_run:
        return OperationResult(
            success=True,
            operation="restore_traffic_from_snapshot",
            message=f"[DRY-RUN] Would restore {target.service} traffic to {traffic_arg}",
            dry_run=True,
            details={"traffic_arg": traffic_arg},
        )

    if result.returncode != 0:
        return OperationResult(
            success=False,
            operation="restore_traffic_from_snapshot",
            message=f"Traffic restore failed for {target.service}: {result.stderr.strip()}",
            details={"returncode": result.returncode, "traffic_arg": traffic_arg},
        )

    return OperationResult(
        success=True,
        operation="restore_traffic_from_snapshot",
        message=f"Restored {target.service} traffic to {traffic_arg}",
        details={"traffic_arg": traffic_arg},
    )


# ---------------------------------------------------------------------------
# Scheduler operations
# ---------------------------------------------------------------------------

def pause_all_schedulers(
    targets: list[SchedulerTarget],
    *,
    dry_run: bool = False,
) -> OperationResult:
    """Pause all Cloud Scheduler triggers.

    Per rollout plan §8.2, schedulers start paused during green
    deployment. This is also the first rollback step (§8.4 step 1).
    """
    if not targets:
        return OperationResult(
            success=True,
            operation="pause_all_schedulers",
            message="No scheduler targets to pause",
        )

    failed: list[str] = []
    for t in targets:
        result = _run_gcloud(
            ["scheduler", "jobs", "pause", t.job_name, *t.gcloud_args(), "--quiet"],
            dry_run=dry_run,
        )
        if not dry_run and result.returncode != 0:
            failed.append(f"{t.job_name}: {result.stderr.strip()}")

    if failed:
        return OperationResult(
            success=False,
            operation="pause_all_schedulers",
            message=f"Failed to pause {len(failed)}/{len(targets)} scheduler(s)",
            details={"failures": failed},
        )

    return OperationResult(
        success=True,
        operation="pause_all_schedulers",
        message=f"{'[DRY-RUN] Would pause' if dry_run else 'Paused'} {len(targets)} scheduler(s)",
        dry_run=dry_run,
    )


def resume_schedulers(
    targets: list[SchedulerTarget],
    *,
    dry_run: bool = False,
) -> OperationResult:
    """Resume Cloud Scheduler triggers after successful switch.

    Per rollout plan §8.3 step 5, schedulers are resumed only after
    one-shot verification succeeds.
    """
    if not targets:
        return OperationResult(
            success=True,
            operation="resume_schedulers",
            message="No scheduler targets to resume",
        )

    failed: list[str] = []
    for t in targets:
        result = _run_gcloud(
            ["scheduler", "jobs", "resume", t.job_name, *t.gcloud_args(), "--quiet"],
            dry_run=dry_run,
        )
        if not dry_run and result.returncode != 0:
            failed.append(f"{t.job_name}: {result.stderr.strip()}")

    if failed:
        return OperationResult(
            success=False,
            operation="resume_schedulers",
            message=f"Failed to resume {len(failed)}/{len(targets)} scheduler(s)",
            details={"failures": failed},
        )

    return OperationResult(
        success=True,
        operation="resume_schedulers",
        message=f"{'[DRY-RUN] Would resume' if dry_run else 'Resumed'} {len(targets)} scheduler(s)",
        dry_run=dry_run,
    )


def switch_job_digests(
    targets: list[SchedulerTarget],
    green_digest: str,
    *,
    dry_run: bool = False,
    body_key: str = "image_digest",
) -> OperationResult:
    """Update Cloud Scheduler job HTTP bodies to reference the green digest.

    Per rollout plan §8.3 step 4, worker/scheduler job targets are
    updated to the green digest. The job's HTTP POST body is updated
    with the new digest, preserving all other fields.

    ``body_key`` controls which JSON field within the HTTP body holds
    the digest (defaults to ``image_digest``).

    This operation is idempotent: if the body already contains the
    target digest, no mutation is performed.
    """
    if not green_digest:
        return OperationResult(
            success=False,
            operation="switch_job_digests",
            message="green_digest must be a non-empty string",
        )

    if not targets:
        return OperationResult(
            success=True,
            operation="switch_job_digests",
            message="No scheduler targets for digest switch",
        )

    failed: list[str] = []
    skipped: list[str] = []
    switched: list[str] = []

    for t in targets:
        # Read current job description
        describe_result = _run_gcloud(
            [
                "scheduler", "jobs", "describe", t.job_name,
                *t.gcloud_args(),
                "--format=json",
            ],
            dry_run=dry_run,
            capture_json=True,
        )

        if dry_run:
            switched.append(t.job_name)
            continue

        if describe_result.returncode != 0:
            failed.append(f"{t.job_name}: describe failed: {describe_result.stderr.strip()}")
            continue

        try:
            job_data = json.loads(describe_result.stdout)
            http_target = job_data.get("httpTarget", {})
            raw_body = http_target.get("body", "")
            if isinstance(raw_body, dict):
                body_json = raw_body
            elif isinstance(raw_body, str) and raw_body:
                try:
                    body_bytes = base64.b64decode(raw_body, validate=True)
                    body_json = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    body_json = json.loads(raw_body) if raw_body.startswith("{") else {}
            else:
                body_json = {}
        except (json.JSONDecodeError, KeyError) as exc:
            failed.append(f"{t.job_name}: body parse failed: {exc}")
            continue

        # Check idempotency
        if body_json.get(body_key) == green_digest:
            skipped.append(t.job_name)
            continue

        # Update body
        body_json[body_key] = green_digest

        update_result = _run_gcloud(
            [
                "scheduler", "jobs", "update", "http", t.job_name,
                *t.gcloud_args(),
                f"--message-body={json.dumps(body_json)}",
                "--quiet",
            ],
            dry_run=dry_run,
        )

        if update_result.returncode != 0:
            failed.append(f"{t.job_name}: update failed: {update_result.stderr.strip()}")
        else:
            switched.append(t.job_name)

    if failed:
        return OperationResult(
            success=False,
            operation="switch_job_digests",
            message=f"Failed to switch {len(failed)}/{len(targets)} job digest(s)",
            details={"failures": failed, "switched": switched, "skipped": skipped},
        )

    return OperationResult(
        success=True,
        operation="switch_job_digests",
        message=(
            f"{'[DRY-RUN] Would switch' if dry_run else 'Switched'} "
            f"{len(switched)} job digest(s), skipped {len(skipped)} (already current)"
        ),
        dry_run=dry_run,
        details={"switched": switched, "skipped": skipped, "digest": green_digest},
    )


# ---------------------------------------------------------------------------
# Data platform pointer operations
# ---------------------------------------------------------------------------

def capture_data_platform_pointer(
    pointer: DataPlatformPointer,
    output_path: Path,
    *,
    dry_run: bool = False,
) -> OperationResult:
    """Capture the current data platform selector / snapshot pointer.

    The pointer is a lightweight JSON file recording which namespace,
    selector label, and snapshot ID are currently active. This is used
    for rollback (§8.4 step 4).
    """
    data = {
        "selector_label": pointer.selector_label,
        "snapshot_id": pointer.snapshot_id,
        "namespace": pointer.namespace,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }

    if dry_run:
        return OperationResult(
            success=True,
            operation="capture_data_platform_pointer",
            message=f"[DRY-RUN] Would capture data platform pointer to {output_path}",
            dry_run=True,
            details=data,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return OperationResult(
        success=True,
        operation="capture_data_platform_pointer",
        message=f"Captured data platform pointer to {output_path}",
        details=data,
    )


def restore_data_platform_pointer(
    snapshot_path: Path,
    *,
    output_path: Path | None = None,
    dry_run: bool = False,
) -> OperationResult:
    """Restore data platform selector / snapshot pointer from a snapshot.

    Per rollout plan §8.4 step 4, the service selector and snapshot
    pointer are reverted to the last approved version.

    In the current implementation this writes a ``restore-pointer.json``
    marker that the data platform deploy tooling reads to reconcile
    state. The actual reconciliation is handled by the data platform
    layer (out of scope for this module), but the pointer file is the
    durable instruction.
    """
    if not snapshot_path.exists():
        return OperationResult(
            success=False,
            operation="restore_data_platform_pointer",
            message=f"Data platform pointer snapshot not found: {snapshot_path}",
        )

    try:
        pointer_data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if not isinstance(pointer_data, dict):
            raise ValueError("Data platform pointer snapshot must be a JSON object")
    except Exception as exc:
        return OperationResult(
            success=False,
            operation="restore_data_platform_pointer",
            message=f"Failed to read pointer snapshot: {exc}",
        )

    if dry_run:
        return OperationResult(
            success=True,
            operation="restore_data_platform_pointer",
            message=f"[DRY-RUN] Would restore data platform pointer from {snapshot_path}",
            dry_run=True,
            details=pointer_data,
        )

    target_restore_path = output_path or (snapshot_path.parent / "restore-pointer.json")
    target_restore_path.parent.mkdir(parents=True, exist_ok=True)
    restore_data = copy.deepcopy(pointer_data)
    restore_data["restore_requested_at"] = datetime.now(timezone.utc).isoformat()
    target_restore_path.write_text(json.dumps(restore_data, indent=2), encoding="utf-8")

    return OperationResult(
        success=True,
        operation="restore_data_platform_pointer",
        message=f"Restore pointer written to {target_restore_path}",
        details=restore_data,
    )


# ---------------------------------------------------------------------------
# Composite operations: full switch and full rollback
# ---------------------------------------------------------------------------

def execute_bluegreen_switch(
    *,
    api_target: ServiceTarget,
    web_target: ServiceTarget,
    green_api_revision: str,
    green_web_revision: str,
    scheduler_targets: list[SchedulerTarget],
    green_job_digest: str,
    release_state: ReleaseState,
    dry_run: bool = False,
) -> list[OperationResult]:
    """Execute the complete blue→green 100% switch sequence.

    Per rollout plan §8.3, the sequence is:

    1. Confirm API contract backward compatible (caller responsibility).
    2. API: blue 100% → green 100%, then authenticated smoke.
    3. Web: blue 100% → green 100%, then E2E.
    4. Worker/scheduler job targets → green digest.
    5. One-shot verify, then resume scheduler triggers.
    6. Start watch window.

    Steps 2–5 are implemented here. Steps 1 and 6 are the caller's
    responsibility.
    """
    results: list[OperationResult] = []

    # Step 2: API atomic switch
    r = atomic_traffic_switch(api_target, green_api_revision, dry_run=dry_run)
    results.append(r)
    if not r.success and not dry_run:
        return results

    # Step 3: Web atomic switch
    r = atomic_traffic_switch(web_target, green_web_revision, dry_run=dry_run)
    results.append(r)
    if not r.success and not dry_run:
        return results

    # Step 4: Switch job digests
    if green_job_digest and scheduler_targets:
        r = switch_job_digests(scheduler_targets, green_job_digest, dry_run=dry_run)
        results.append(r)
        if not r.success and not dry_run:
            return results

    # Step 5: Resume schedulers (caller should one-shot verify first)
    if scheduler_targets:
        r = resume_schedulers(scheduler_targets, dry_run=dry_run)
        results.append(r)

    # Record completion time
    if not dry_run and all(op.success for op in results):
        release_state.switch_completed_at = datetime.now(timezone.utc).isoformat()

    return results


def execute_rollback(
    *,
    api_target: ServiceTarget,
    web_target: ServiceTarget,
    api_snapshot_path: Path,
    web_snapshot_path: Path,
    scheduler_targets: list[SchedulerTarget],
    blue_job_digest: str,
    data_platform_pointer_path: Path | None = None,
    release_state: ReleaseState,
    dry_run: bool = False,
    traffic_helper: str = "product_ops/deployment/cloud_run_traffic.py",
) -> list[OperationResult]:
    """Execute the complete rollback sequence.

    Per rollout plan §8.4:

    1. Pause scheduler triggers.
    2. Web & API traffic → blue 100%.
    3. Worker/scheduler target → blue digest.
    4. Data platform selector/snapshot pointer → previous approved version.
    5. Verify old version can read expand-migrated schema.
    6. Rollback smoke & data consistency check.
    7. Preserve staging & failed green evidence, create incident task.

    Steps 1–4 are automated here. Steps 5–7 require caller validation.
    """
    results: list[OperationResult] = []

    # Step 1: Pause all schedulers
    if scheduler_targets:
        r = pause_all_schedulers(scheduler_targets, dry_run=dry_run)
        results.append(r)
        if not r.success and not dry_run:
            return results

    # Step 2: Restore Web traffic first (reverse order of switch)
    r = restore_traffic_from_snapshot(
        web_target, web_snapshot_path, dry_run=dry_run, traffic_helper=traffic_helper,
    )
    results.append(r)

    # Step 2 (cont): Restore API traffic
    r = restore_traffic_from_snapshot(
        api_target, api_snapshot_path, dry_run=dry_run, traffic_helper=traffic_helper,
    )
    results.append(r)

    # Step 3: Switch job digests back to blue
    if blue_job_digest and scheduler_targets:
        r = switch_job_digests(scheduler_targets, blue_job_digest, dry_run=dry_run)
        results.append(r)

    # Step 4: Restore data platform pointer
    effective_pointer_path = data_platform_pointer_path or (
        Path(release_state.data_platform_pointer_snapshot_path)
        if release_state.data_platform_pointer_snapshot_path
        else None
    )
    if effective_pointer_path:
        r = restore_data_platform_pointer(effective_pointer_path, dry_run=dry_run)
        results.append(r)

    # Record completion time
    if not dry_run and all(op.success for op in results):
        release_state.rollback_completed_at = datetime.now(timezone.utc).isoformat()

    return results


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Log operations without executing")
    parser.add_argument("--project", required=True, help="GCP project ID")
    parser.add_argument("--region", required=True, help="GCP region")

    sub = parser.add_subparsers(dest="command", required=True)

    # capture-state
    cap = sub.add_parser("capture-state", help="Capture pre-release state for rollback")
    cap.add_argument("--api-service", required=True)
    cap.add_argument("--web-service", required=True)
    cap.add_argument("--output-dir", required=True, type=Path)
    cap.add_argument("--release-id", required=True)
    cap.add_argument("--selector-label", default="")
    cap.add_argument("--snapshot-id", default="")
    cap.add_argument("--namespace", default="")

    # switch
    sw = sub.add_parser("switch", help="Execute blue→green 100% traffic switch")
    sw.add_argument("--api-service", required=True)
    sw.add_argument("--web-service", required=True)
    sw.add_argument("--green-api-revision", required=True)
    sw.add_argument("--green-web-revision", required=True)
    sw.add_argument("--green-job-digest", default="")
    sw.add_argument("--scheduler-jobs", nargs="*", default=[])
    sw.add_argument("--state-file", required=True, type=Path)

    # rollback
    rb = sub.add_parser("rollback", help="Execute green→blue rollback")
    rb.add_argument("--api-service", required=True)
    rb.add_argument("--web-service", required=True)
    rb.add_argument("--state-file", required=True, type=Path)
    rb.add_argument("--blue-job-digest", default="")
    rb.add_argument("--scheduler-jobs", nargs="*", default=[])
    rb.add_argument("--data-platform-pointer", type=Path, default=None)

    # resolve-tag
    rt = sub.add_parser("resolve-tag", help="Resolve revision and URL for a tag")
    rt.add_argument("--service", required=True)
    rt.add_argument("--tag", required=True)

    # pause-schedulers
    ps = sub.add_parser("pause-schedulers", help="Pause Cloud Scheduler triggers")
    ps.add_argument("--jobs", nargs="+", required=True)

    # resume-schedulers
    rs = sub.add_parser("resume-schedulers", help="Resume Cloud Scheduler triggers")
    rs.add_argument("--jobs", nargs="+", required=True)

    # switch-digests
    sd = sub.add_parser("switch-digests", help="Update job digests for Cloud Scheduler triggers")
    sd.add_argument("--jobs", nargs="+", required=True)
    sd.add_argument("--digest", required=True)
    sd.add_argument("--body-key", default="image_digest")

    # capture-pointer
    cp = sub.add_parser("capture-pointer", help="Capture data platform selector / snapshot pointer")
    cp.add_argument("--selector-label", required=True)
    cp.add_argument("--snapshot-id", required=True)
    cp.add_argument("--namespace", required=True)
    cp.add_argument("--output-file", required=True, type=Path)

    # restore-pointer
    rp = sub.add_parser("restore-pointer", help="Restore data platform selector / snapshot pointer")
    rp.add_argument("--snapshot-file", required=True, type=Path)
    rp.add_argument("--output-file", type=Path, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    api_target = ServiceTarget(
        service=getattr(args, "api_service", ""),
        project=args.project,
        region=args.region,
    )
    web_target = ServiceTarget(
        service=getattr(args, "web_service", ""),
        project=args.project,
        region=args.region,
    )

    if args.command == "capture-state":
        output_dir: Path = args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        state = ReleaseState(release_id=args.release_id)

        # Capture API traffic
        r = capture_traffic_snapshot(
            api_target, output_dir / "api-traffic.json", dry_run=args.dry_run,
        )
        print(json.dumps(r.to_dict(), indent=2))
        if not r.success:
            return 1
        state.api_traffic_snapshot_path = str(output_dir / "api-traffic.json")

        # Capture Web traffic
        r = capture_traffic_snapshot(
            web_target, output_dir / "web-traffic.json", dry_run=args.dry_run,
        )
        print(json.dumps(r.to_dict(), indent=2))
        if not r.success:
            return 1
        state.web_traffic_snapshot_path = str(output_dir / "web-traffic.json")

        # Optional data platform pointer capture
        if getattr(args, "selector_label", "") and getattr(args, "snapshot_id", ""):
            pointer = DataPlatformPointer(
                selector_label=args.selector_label,
                snapshot_id=args.snapshot_id,
                namespace=getattr(args, "namespace", "default"),
            )
            pointer_path = output_dir / "data-platform-pointer.json"
            r = capture_data_platform_pointer(pointer, pointer_path, dry_run=args.dry_run)
            print(json.dumps(r.to_dict(), indent=2))
            if not r.success:
                return 1
            state.data_platform_pointer = {
                "selector_label": pointer.selector_label,
                "snapshot_id": pointer.snapshot_id,
                "namespace": pointer.namespace,
            }
            state.data_platform_pointer_snapshot_path = str(pointer_path)

        # Write release state
        state_path = output_dir / "release-state.json"
        state_path.write_text(state.to_json(), encoding="utf-8")
        print(f"Release state written to {state_path}")
        return 0

    if args.command == "switch":
        state = ReleaseState.from_json(args.state_file.read_text(encoding="utf-8"))
        schedulers = [
            SchedulerTarget(job_name=j, project=args.project, location=args.region)
            for j in getattr(args, "scheduler_jobs", [])
        ]
        results = execute_bluegreen_switch(
            api_target=api_target,
            web_target=web_target,
            green_api_revision=args.green_api_revision,
            green_web_revision=args.green_web_revision,
            scheduler_targets=schedulers,
            green_job_digest=args.green_job_digest,
            release_state=state,
            dry_run=args.dry_run,
        )
        for r in results:
            print(json.dumps(r.to_dict(), indent=2))
        args.state_file.write_text(state.to_json(), encoding="utf-8")
        return 0 if all(r.success for r in results) else 1

    if args.command == "rollback":
        state = ReleaseState.from_json(args.state_file.read_text(encoding="utf-8"))
        schedulers = [
            SchedulerTarget(job_name=j, project=args.project, location=args.region)
            for j in getattr(args, "scheduler_jobs", [])
        ]
        results = execute_rollback(
            api_target=api_target,
            web_target=web_target,
            api_snapshot_path=Path(state.api_traffic_snapshot_path),
            web_snapshot_path=Path(state.web_traffic_snapshot_path),
            scheduler_targets=schedulers,
            blue_job_digest=args.blue_job_digest,
            data_platform_pointer_path=getattr(args, "data_platform_pointer", None),
            release_state=state,
            dry_run=args.dry_run,
        )
        for r in results:
            print(json.dumps(r.to_dict(), indent=2))
        args.state_file.write_text(state.to_json(), encoding="utf-8")
        return 0 if all(r.success for r in results) else 1

    if args.command == "resolve-tag":
        svc_target = ServiceTarget(service=args.service, project=args.project, region=args.region)
        r = resolve_tagged_target(svc_target, args.tag, dry_run=args.dry_run)
        print(json.dumps(r.to_dict(), indent=2))
        return 0 if r.success else 1

    if args.command == "pause-schedulers":
        targets = [
            SchedulerTarget(job_name=j, project=args.project, location=args.region)
            for j in args.jobs
        ]
        r = pause_all_schedulers(targets, dry_run=args.dry_run)
        print(json.dumps(r.to_dict(), indent=2))
        return 0 if r.success else 1

    if args.command == "resume-schedulers":
        targets = [
            SchedulerTarget(job_name=j, project=args.project, location=args.region)
            for j in args.jobs
        ]
        r = resume_schedulers(targets, dry_run=args.dry_run)
        print(json.dumps(r.to_dict(), indent=2))
        return 0 if r.success else 1

    if args.command == "switch-digests":
        targets = [
            SchedulerTarget(job_name=j, project=args.project, location=args.region)
            for j in args.jobs
        ]
        r = switch_job_digests(
            targets, args.digest, dry_run=args.dry_run, body_key=args.body_key,
        )
        print(json.dumps(r.to_dict(), indent=2))
        return 0 if r.success else 1

    if args.command == "capture-pointer":
        pointer = DataPlatformPointer(
            selector_label=args.selector_label,
            snapshot_id=args.snapshot_id,
            namespace=args.namespace,
        )
        r = capture_data_platform_pointer(pointer, args.output_file, dry_run=args.dry_run)
        print(json.dumps(r.to_dict(), indent=2))
        return 0 if r.success else 1

    if args.command == "restore-pointer":
        r = restore_data_platform_pointer(
            args.snapshot_file, output_path=args.output_file, dry_run=args.dry_run,
        )
        print(json.dumps(r.to_dict(), indent=2))
        return 0 if r.success else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
