#!/usr/bin/env python3
"""Production blue-green release primitives.

This module implements the atomic operations needed for a blue-green
production release, as specified in §8 of
``EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md``.

Responsibilities
~~~~~~~~~~~~~~~~
- Green 0% tag smoke → atomic 100% traffic switch
- Scheduler pause / verified resume with immutable release-scoped Job targets
- Data platform selector / snapshot pointer recovery
- Full rollback sequence

Deploy and manual recovery call the same shell mutation owner. Switch leaves
triggers paused; a separate resume requires the existing jobs-smoke receipt
and fresh Job/execution/trigger readback. Technical failures compensate from
captured snapshots; SLO rollback remains a Human/Ops decision.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    """Identifies a trigger and, for switch/resume, its immutable Job binding."""

    job_name: str
    project: str
    location: str
    job: str = ""
    image: str = ""
    kind: str = ""
    cron: str = ""
    time_zone: str = ""
    service_account: str = ""
    first_create_state: str = "PAUSED"

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
    scheduler_snapshots: dict[str, str] = field(default_factory=dict)
    scheduler_bindings: dict[str, dict[str, str]] = field(default_factory=dict)
    release_sha: str = ""
    project: str = ""
    region: str = ""
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
                "scheduler_snapshots": self.scheduler_snapshots,
                "scheduler_bindings": self.scheduler_bindings,
                "release_sha": self.release_sha,
                "project": self.project,
                "region": self.region,
                "data_platform_pointer": self.data_platform_pointer,
                "data_platform_pointer_snapshot_path": self.data_platform_pointer_snapshot_path,
                "switch_completed_at": self.switch_completed_at,
                "rollback_completed_at": self.rollback_completed_at,
            },
            indent=2,
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, text: str) -> ReleaseState:
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
            scheduler_snapshots=data.get("scheduler_snapshots", {}),
            scheduler_bindings=data.get("scheduler_bindings", {}),
            release_sha=data.get("release_sha", ""),
            project=data.get("project", ""),
            region=data.get("region", ""),
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
    return _snapshot_contract().tagged_target(description, tag)


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
            "run",
            "services",
            "describe",
            target.service,
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
            details={
                "tag": tag,
                "revision": f"{target.service}-{tag}-mock",
                "url": f"https://{tag}---{target.service}.run.app",
            },
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


def _snapshot_contract(filename: str = "cloud_run_traffic.py") -> Any:
    """Load ``cloud_run_traffic.py``, the one owner of the snapshot shape.

    The deploy script and this module must agree on what an absent service
    looks like, so the marker is not restated here.
    """
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location("odp_" + path.stem, path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise RuntimeError(f"cannot load the Cloud Run traffic contract from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capture_traffic_snapshot(
    target: ServiceTarget,
    output_path: Path,
    *,
    dry_run: bool = False,
) -> OperationResult:
    """Capture the current traffic allocation for a Cloud Run service.

    The snapshot is written as a JSON file suitable for ``restore_arg``
    in ``cloud_run_traffic.py``.  Only ``status.url`` and ``status.traffic``
    are kept: the raw ``describe`` payload carries the deployed env block and
    secret selectors, and this snapshot is published as a release artifact.
    A service that does not exist yet (first release) is recorded with the
    explicit absence marker instead of failing.
    """
    listing = _run_gcloud(
        [
            "run",
            "services",
            "list",
            *target.gcloud_args(),
            f"--filter=metadata.name={target.service}",
            "--format=value(metadata.name)",
        ],
        dry_run=dry_run,
    )
    if dry_run:
        return OperationResult(
            success=True,
            operation="capture_traffic_snapshot",
            message=f"[DRY-RUN] Would capture {target.service} traffic to {output_path}",
            dry_run=True,
        )
    if listing.returncode != 0:
        return OperationResult(
            success=False,
            operation="capture_traffic_snapshot",
            message=f"Failed to list {target.service}: {listing.stderr.strip()}",
            details={"returncode": listing.returncode},
        )
    existing = listing.stdout.strip()
    if not existing:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(_snapshot_contract().absent_snapshot(target.service), indent=2),
            encoding="utf-8",
        )
        return OperationResult(
            success=True,
            operation="capture_traffic_snapshot",
            message=f"{target.service} does not exist yet; recorded it as absent in {output_path}",
            details={"path": str(output_path), "exists": False},
        )
    if existing != target.service:
        return OperationResult(
            success=False,
            operation="capture_traffic_snapshot",
            message=f"Cloud Run service lookup for {target.service} was ambiguous: {existing}",
        )

    result = _run_gcloud(
        [
            "run",
            "services",
            "describe",
            target.service,
            *target.gcloud_args(),
            "--format=json",
        ],
        capture_json=True,
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

    status = description.get("status") if isinstance(description, dict) else None
    traffic = status.get("traffic") if isinstance(status, dict) else None
    if not isinstance(traffic, list):
        return OperationResult(
            success=False,
            operation="capture_traffic_snapshot",
            message=f"{target.service} description is missing status.traffic",
        )

    snapshot = {
        "schema_version": 1,
        "kind": "cloud-run-service-traffic-snapshot",
        "exists": True,
        "service": target.service,
        "status": {"url": str(status.get("url") or ""), "traffic": traffic},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return OperationResult(
        success=True,
        operation="capture_traffic_snapshot",
        message=f"Captured {target.service} traffic snapshot to {output_path}",
        details={"path": str(output_path), "exists": True},
    )


def _run_release_helper(
    target: ServiceTarget | SchedulerTarget, function: str, arguments: list[str], *,
    dry_run: bool = False,
) -> OperationResult:
    """Call the existing shell mutation owner with positional, unexpanded arguments."""
    if dry_run:
        return OperationResult(True, function, f"[DRY-RUN] Would call {function}", True)
    directory = Path(__file__).resolve().parent
    environment = {
        **os.environ, "GCP_PROJECT": target.project,
        "GCP_REGION": target.region if isinstance(target, ServiceTarget) else target.location,
        "ODP_TRAFFIC_HELPER": str(directory / "cloud_run_traffic.py"),
        "ODP_SCHEDULER_HELPER": str(directory / "cloud_scheduler_trigger.py"),
        "ODP_JOB_VALIDATOR": str(directory / "validate_cloud_run_live_deployment.py"),
    }
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", 'source "$1"; shift; "$@"',
         "bluegreen-release", str(directory / "cloud_run_release_traffic.sh"), function, *arguments],
        env=environment, capture_output=True, text=True, check=False,
    )
    return OperationResult(
        result.returncode == 0, function,
        result.stdout.strip() if result.returncode == 0 else result.stderr.strip(),
        details={"returncode": result.returncode},
    )


def atomic_traffic_switch(
    target: ServiceTarget, green_revision: str, *, dry_run: bool = False,
) -> OperationResult:
    if not green_revision:
        return OperationResult(False, "atomic_traffic_switch", "green_revision must be non-empty")
    return _run_release_helper(target, "promote_service_traffic",
                               [target.service, green_revision], dry_run=dry_run)


def restore_traffic_from_snapshot(
    target: ServiceTarget, snapshot_path: Path, *, dry_run: bool = False,
) -> OperationResult:
    if not snapshot_path.is_file():
        return OperationResult(False, "restore_traffic_from_snapshot", f"Snapshot file not found: {snapshot_path}")
    return _run_release_helper(target, "restore_service_traffic",
                               [target.service, str(snapshot_path.resolve())], dry_run=dry_run)


def pause_all_schedulers(targets: list[SchedulerTarget], *, dry_run: bool = False) -> OperationResult:
    results = [_run_release_helper(target, "pause_scheduler_trigger", [target.job_name], dry_run=dry_run)
               for target in targets]
    return OperationResult(all(result.success for result in results), "pause_all_schedulers",
                           "; ".join(result.message for result in results), dry_run)


def _scheduler_arguments(target: SchedulerTarget, state: ReleaseState, state_dir: Path) -> list[str]:
    return [target.job_name, target.job, target.image, target.cron, target.time_zone,
            target.service_account, str((state_dir / state.scheduler_snapshots[target.job_name]).resolve()),
            state.release_sha]


def resume_schedulers(
    targets: list[SchedulerTarget], *, release_state: ReleaseState, state_dir: Path,
    receipts: dict[str, Path], dry_run: bool = False,
) -> OperationResult:
    """Resume only captured ENABLED triggers after the existing jobs-smoke proof."""
    if not release_state.switch_completed_at:
        return OperationResult(False, "resume_schedulers", "a completed paused switch is required")
    if (len(targets) != 2 or {target.kind for target in targets} != {"worker", "scheduler"}
            or {target.job_name for target in targets} != set(release_state.scheduler_bindings)):
        return OperationResult(False, "resume_schedulers", "both captured immutable scheduler bindings are required")
    if set(receipts) != {target.job_name for target in targets}:
        return OperationResult(False, "resume_schedulers", "a jobs-smoke receipt for every captured binding is required")
    for mode in ("verify-only", "resume"):
        for target in targets:
            result = _run_release_helper(
                target, "resume_scheduler_trigger",
                _scheduler_arguments(target, release_state, state_dir) +
                [target.kind, str(receipts[target.job_name].resolve()), target.first_create_state, mode],
                dry_run=dry_run,
            )
            if not result.success:
                compensation = pause_all_schedulers(targets, dry_run=dry_run)
                result.details["pause_compensation"] = compensation.to_dict()
                result.details["mutation_started"] = mode == "resume" or not compensation.success
                return result
    return OperationResult(True, "resume_schedulers", "verified intended triggers resumed; prior PAUSED preserved", dry_run)


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
        "captured_at": datetime.now(UTC).isoformat(),
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
    restore_data["restore_requested_at"] = datetime.now(UTC).isoformat()
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


def _validate_release_snapshots(
    state: ReleaseState, state_dir: Path, api: ServiceTarget, web: ServiceTarget,
    schedulers: list[SchedulerTarget],
) -> None:
    """Validate recovery material before any mutation can need compensation."""
    if (state.project != api.project or state.region != api.region
            or (web.project, web.region) != (api.project, api.region)
            or not re.fullmatch(r"[0-9a-f]{40}", state.release_sha)):
        raise ValueError("release state must bind this exact project/region and release SHA")
    if (not state.scheduler_snapshots or len(schedulers) != len(state.scheduler_snapshots)
            or {target.job_name for target in schedulers} != set(state.scheduler_snapshots)
            or any((target.project, target.location) != (api.project, api.region) for target in schedulers)):
        raise ValueError("scheduler snapshot inventory/scope does not match")
    for target, relative in ((api, state.api_traffic_snapshot_path), (web, state.web_traffic_snapshot_path)):
        if not relative:
            raise ValueError("release state records no API/Web traffic snapshot")
        payload = json.loads((state_dir / relative).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("traffic snapshot must be an object")
        if payload.get("service") != target.service:
            raise ValueError("traffic snapshot belongs to another service")
        if _snapshot_contract().is_present(payload):
            _snapshot_contract().restore_traffic_argument(payload)
            _snapshot_contract().service_url(payload)
    for name, relative in state.scheduler_snapshots.items():
        payload = json.loads((state_dir / relative).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("scheduler snapshot must be an object")
        _snapshot_contract("cloud_scheduler_trigger.py").validate_snapshot(
            payload, trigger=name, project=api.project, location=api.region,
        )
        if payload.get("exists") is not False and not payload.get("jobBinding"):
            raise ValueError("scheduler snapshot is missing its captured immutable job binding")


def execute_bluegreen_switch(
    *, api_target: ServiceTarget, web_target: ServiceTarget,
    green_api_revision: str, green_web_revision: str,
    scheduler_targets: list[SchedulerTarget], release_state: ReleaseState,
    state_dir: Path, dry_run: bool = False,
) -> list[OperationResult]:
    """Switch using the deployment owner, leaving all triggers PAUSED."""
    try:
        _validate_release_snapshots(release_state, state_dir, api_target, web_target, scheduler_targets)
        for target in scheduler_targets:
            _snapshot_contract("cloud_scheduler_trigger.py").validate_trigger_state(target.first_create_state)
    except (OSError, ValueError) as exc:
        return [OperationResult(False, "switch", str(exc))]
    names = {target.job_name for target in scheduler_targets}
    if (not names or len(names) != len(scheduler_targets)
            or names != set(release_state.scheduler_snapshots)
            or {target.kind for target in scheduler_targets} != {"worker", "scheduler"}
            or not release_state.api_traffic_snapshot_path or not release_state.web_traffic_snapshot_path):
        return [OperationResult(False, "switch", "captured traffic and both scheduler snapshots/bindings are required")]
    results: list[OperationResult] = []
    for target in scheduler_targets:
        result = _run_release_helper(target, "verify_release_job",
                                     [target.job, target.image, release_state.release_sha], dry_run=dry_run)
        results.append(result)
        if not result.success:
            return results
    steps = [(pause_all_schedulers, (scheduler_targets,))]
    for target in scheduler_targets:
        steps.append((_run_release_helper, (target, "upsert_scheduler_trigger",
                                           _scheduler_arguments(target, release_state, state_dir))))
    steps.extend([
        (atomic_traffic_switch, (api_target, green_api_revision)),
        (atomic_traffic_switch, (web_target, green_web_revision)),
    ])
    for operation, arguments in steps:
        result = operation(*arguments, dry_run=dry_run)
        results.append(result)
        if not result.success:
            results.extend(execute_rollback(
                api_target=api_target, web_target=web_target,
                api_snapshot_path=state_dir / release_state.api_traffic_snapshot_path,
                web_snapshot_path=state_dir / release_state.web_traffic_snapshot_path,
                scheduler_targets=scheduler_targets, release_state=release_state,
                state_dir=state_dir, dry_run=dry_run,
            ))
            return results
    if not dry_run:
        release_state.green_api_revision = green_api_revision
        release_state.green_web_revision = green_web_revision
        release_state.scheduler_bindings = {
            target.job_name: {key: getattr(target, key) for key in
                              ("job", "image", "kind", "cron", "time_zone", "service_account", "first_create_state")}
            for target in scheduler_targets
        }
        release_state.switch_completed_at = datetime.now(UTC).isoformat()
    return results


def execute_rollback(
    *, api_target: ServiceTarget, web_target: ServiceTarget,
    api_snapshot_path: Path, web_snapshot_path: Path,
    scheduler_targets: list[SchedulerTarget], release_state: ReleaseState,
    state_dir: Path, data_platform_pointer_path: Path | None = None,
    dry_run: bool = False,
) -> list[OperationResult]:
    """Human/Ops rollback and technical compensation restore the same snapshots."""
    try:
        _validate_release_snapshots(release_state, state_dir, api_target, web_target, scheduler_targets)
    except (OSError, ValueError) as exc:
        return [OperationResult(False, "rollback", str(exc))]
    results = [pause_all_schedulers(scheduler_targets, dry_run=dry_run)]
    results.append(restore_traffic_from_snapshot(web_target, web_snapshot_path, dry_run=dry_run))
    results.append(restore_traffic_from_snapshot(api_target, api_snapshot_path, dry_run=dry_run))
    for target in scheduler_targets:
        results.append(_run_release_helper(
            target, "restore_scheduler_trigger",
            [target.job_name, str((state_dir / release_state.scheduler_snapshots[target.job_name]).resolve())],
            dry_run=dry_run,
        ))
    if data_platform_pointer_path:
        results.append(restore_data_platform_pointer(data_platform_pointer_path, dry_run=dry_run))
    if not dry_run and all(result.success for result in results):
        release_state.rollback_completed_at = datetime.now(UTC).isoformat()
        release_state.switch_completed_at = ""
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
    cap.add_argument("--release-sha", required=True)
    cap.add_argument("--scheduler-jobs", nargs="+", required=True)
    cap.add_argument("--selector-label", default="")
    cap.add_argument("--snapshot-id", default="")
    cap.add_argument("--namespace", default="")

    # switch
    sw = sub.add_parser("switch", help="Execute blue→green 100% traffic switch")
    sw.add_argument("--api-service", required=True)
    sw.add_argument("--web-service", required=True)
    sw.add_argument("--green-api-revision", required=True)
    sw.add_argument("--green-web-revision", required=True)
    for kind in ("worker", "scheduler"):
        sw.add_argument(f"--{kind}-trigger", required=True)
        sw.add_argument(f"--{kind}-job", required=True)
        sw.add_argument(f"--{kind}-image", required=True)
        sw.add_argument(f"--{kind}-cron", required=True)
    sw.add_argument("--time-zone", required=True)
    sw.add_argument("--service-account", required=True)
    sw.add_argument("--new-trigger-state", choices=("ENABLED", "PAUSED"), required=True)
    sw.add_argument("--state-file", required=True, type=Path)

    # rollback
    rb = sub.add_parser("rollback", help="Execute green→blue rollback")
    rb.add_argument("--api-service", required=True)
    rb.add_argument("--web-service", required=True)
    rb.add_argument("--state-file", required=True, type=Path)
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
    rs.add_argument("--state-file", required=True, type=Path)
    rs.add_argument("--api-service", required=True)
    rs.add_argument("--web-service", required=True)
    rs.add_argument("--worker-receipt", required=True, type=Path)
    rs.add_argument("--scheduler-receipt", required=True, type=Path)

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
        if not re.fullmatch(r"[0-9a-f]{40}", args.release_sha) or len(set(args.scheduler_jobs)) != len(args.scheduler_jobs):
            parser.error("capture-state requires an exact release SHA and unique scheduler names")
        output_dir: Path = args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        state = ReleaseState(release_id=args.release_id, release_sha=args.release_sha,
                             project=args.project, region=args.region)

        # Capture API traffic
        r = capture_traffic_snapshot(
            api_target,
            output_dir / "api-traffic.json",
            dry_run=args.dry_run,
        )
        print(json.dumps(r.to_dict(), indent=2))
        if not r.success:
            return 1
        state.api_traffic_snapshot_path = "api-traffic.json"

        # Capture Web traffic
        r = capture_traffic_snapshot(
            web_target,
            output_dir / "web-traffic.json",
            dry_run=args.dry_run,
        )
        print(json.dumps(r.to_dict(), indent=2))
        if not r.success:
            return 1
        state.web_traffic_snapshot_path = "web-traffic.json"

        # Same capture owner as the deploy rollback trap; paths remain portable.
        for index, name in enumerate(args.scheduler_jobs):
            path = output_dir / f"scheduler-{index}.json"
            target = SchedulerTarget(name, args.project, args.region)
            result = _run_release_helper(target, "capture_scheduler_trigger", [name, str(path.resolve()), "with-job-binding"],
                                         dry_run=args.dry_run)
            print(json.dumps(result.to_dict(), indent=2))
            if not result.success:
                return 1
            state.scheduler_snapshots[name] = path.name
            if not args.dry_run:
                payload = json.loads(path.read_text(encoding="utf-8"))
                state.scheduler_states[name] = payload.get("state", "ENABLED") if payload.get("exists") is not False else "ABSENT"

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
            state.data_platform_pointer_snapshot_path = pointer_path.name

        # Write release state
        state_path = output_dir / "release-state.json"
        if args.dry_run:
            print(f"[DRY-RUN] Would write release state to {state_path}")
            return 0
        state_path.write_text(state.to_json(), encoding="utf-8")
        print(f"Release state written to {state_path}")
        return 0

    if args.command in ("switch", "rollback", "resume-schedulers"):
        state = ReleaseState.from_json(args.state_file.read_text(encoding="utf-8"))
        state_dir = args.state_file.resolve().parent
        if (state.project != args.project or state.region != args.region
                or not state.scheduler_snapshots):
            print("Error: recapture a complete release state for this project/region, including scheduler snapshots", file=sys.stderr)
            return 1
        if args.command == "switch":
            schedulers = [
                SchedulerTarget(
                    job_name=getattr(args, f"{kind}_trigger"), project=args.project, location=args.region,
                    job=getattr(args, f"{kind}_job"), image=getattr(args, f"{kind}_image"), kind=kind,
                    cron=getattr(args, f"{kind}_cron"), time_zone=args.time_zone,
                    service_account=args.service_account, first_create_state=args.new_trigger_state,
                ) for kind in ("worker", "scheduler")
            ]
            results = execute_bluegreen_switch(
                api_target=api_target, web_target=web_target,
                green_api_revision=args.green_api_revision, green_web_revision=args.green_web_revision,
                scheduler_targets=schedulers, release_state=state, state_dir=state_dir, dry_run=args.dry_run,
            )
        else:
            schedulers = [SchedulerTarget(
                job_name=name, project=args.project, location=args.region,
                **state.scheduler_bindings.get(name, {}),
            ) for name in state.scheduler_snapshots]
            if args.command == "rollback":
                pointer = args.data_platform_pointer
                if pointer is None and state.data_platform_pointer_snapshot_path:
                    pointer = state_dir / state.data_platform_pointer_snapshot_path
                results = execute_rollback(
                    api_target=api_target, web_target=web_target,
                    api_snapshot_path=state_dir / state.api_traffic_snapshot_path,
                    web_snapshot_path=state_dir / state.web_traffic_snapshot_path,
                    scheduler_targets=schedulers, release_state=state, state_dir=state_dir,
                    data_platform_pointer_path=pointer, dry_run=args.dry_run,
                )
            else:
                receipts = {target.job_name: getattr(args, f"{target.kind}_receipt")
                            for target in schedulers if target.kind in ("worker", "scheduler")}
                result = resume_schedulers(schedulers, release_state=state, state_dir=state_dir,
                                           receipts=receipts, dry_run=args.dry_run)
                results = [result]
                if not result.success and result.details.get("mutation_started"):
                    results.extend(execute_rollback(
                        api_target=api_target, web_target=web_target,
                        api_snapshot_path=state_dir / state.api_traffic_snapshot_path,
                        web_snapshot_path=state_dir / state.web_traffic_snapshot_path,
                        scheduler_targets=schedulers, release_state=state, state_dir=state_dir,
                        dry_run=args.dry_run,
                    ))
        for result in results:
            print(json.dumps(result.to_dict(), indent=2))
        if not args.dry_run:
            args.state_file.write_text(state.to_json(), encoding="utf-8")
        return 0 if all(result.success for result in results) else 1

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
            args.snapshot_file,
            output_path=args.output_file,
            dry_run=args.dry_run,
        )
        print(json.dumps(r.to_dict(), indent=2))
        return 0 if r.success else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
