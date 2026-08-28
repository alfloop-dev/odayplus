import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

ACTIVE_WORKER_STATUSES = {
    "running",
    "waiting_approval",
    "suspended_approval",
    "retry_backoff",
    "manual_pending",
    "stalled",
}
HARD_GATE_MARKERS = {
    "human gate",
    "manual approval",
    "credential",
    "license",
    "production proof",
    "legal",
}
DEFAULT_SIDECAR_CATALOG = Path(__file__).with_name("sidecar_catalog.json")
MAX_SIDECAR_ID_LENGTH = 48


def build_sidecar_task_id(parent_id: str, kind: str) -> str:
    """Build a deterministic, bounded sidecar task ID (at most 48 characters).

    If {parent_id}-SIDECAR-{kind_slug} fits within 48 characters, it remains
    unchanged to preserve existing short sidecar IDs. Otherwise, the -SIDECAR-
    marker is preserved with a truncated parent prefix and an 8-character
    collision-resistant SHA-256 digest of the canonical parent:kind signature.
    """
    parent = str(parent_id or "").strip()
    clean_kind = str(kind or "").strip()
    kind_slug = clean_kind.replace("_", "-").upper()
    raw_id = f"{parent}-SIDECAR-{kind_slug}"
    if len(raw_id) <= MAX_SIDECAR_ID_LENGTH:
        return raw_id

    digest = hashlib.sha256(f"{parent}:{clean_kind}".encode()).hexdigest()[:8].upper()
    marker = "-SIDECAR-"
    max_parent_len = MAX_SIDECAR_ID_LENGTH - len(marker) - len(digest)
    parent_prefix = parent[:max_parent_len].rstrip("-")
    return f"{parent_prefix}{marker}{digest}"


sidecar_task_id = build_sidecar_task_id


def _parse_iso(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _render_template(value: Any, variables: dict[str, str]) -> str:
    rendered = str(value or "")
    for key, item in variables.items():
        rendered = rendered.replace("{{" + key + "}}", item)
    return rendered


def settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = dict(config.get("capacity_controller", {}) or {})
    raw.setdefault("enabled", True)
    raw.setdefault("chair_interval_seconds", 1800)
    raw.setdefault("stall_window_seconds", 300)
    raw.setdefault("underutilization_threshold_ratio", 0.5)
    raw.setdefault("underutilization_window_seconds", 600)
    raw.setdefault("coordination_reserved_slots", 1)
    sidecars = dict(raw.get("sidecars", {}) or {})
    sidecars.setdefault("enabled", True)
    sidecars.setdefault("require_chair_approval", True)
    sidecars.setdefault("max_new_per_wave", 3)
    sidecars.setdefault("max_active", 4)
    sidecars.setdefault("max_capacity_ratio", 0.25)
    sidecars.setdefault("ttl_seconds", 7200)
    raw["sidecars"] = sidecars
    return raw


def configured_slots(config: dict[str, Any]) -> int:
    return sum(
        1
        for agent in (config.get("agents", {}) or {}).values()
        if isinstance(agent, dict) and agent.get("slot_id")
    )


def effective_slots(
    config: dict[str, Any],
    runtime_state: dict[str, Any] | None = None,
    provider_report: dict[str, Any] | None = None,
) -> int:
    """Count dispatch slots that are currently eligible for automated execution.

    Consumes agent_auto_dispatch_block_reason and provider_report as the single
    authority on provider health and dispatch eligibility. Configured slots that
    cannot execute automated tasks (e.g. auth failure, inbox fallback, disabled
    or cooling-down account pool) are excluded so that slot_total and available_slots
    reflect real executable capacity rather than dead configured declarations.
    """
    from supervisor import provider_capability_block_reason
    from worker_failure_policy import agent_auto_dispatch_block_reason

    slot_check_state = dict(runtime_state or {})
    # Clear active worker map so that currently executing workers do not falsely
    # mark a healthy slot as unviable or capacity-depleted in the total count.
    slot_check_state["workers"] = {}
    count = 0
    for slot_id, agent in (config.get("agents", {}) or {}).items():
        if not (isinstance(agent, dict) and agent.get("slot_id")):
            continue
        if agent_auto_dispatch_block_reason(
            config, slot_check_state, slot_id, provider_report=provider_report
        ):
            continue
        if provider_report and provider_capability_block_reason(
            config, slot_id, provider_report=provider_report
        ):
            continue
        count += 1
    return count


def _count_runnable_tasks(
    _config: dict[str, Any],
    _tasks: list[dict[str, Any]],
    runnable_tasks: int | list[dict[str, Any]] | set[str] | None = None,
) -> int:
    """Consume the Supervisor's already-computed canonical runnable set.

    Capacity Chair deliberately has no task eligibility predicate of its own.
    The Dispatcher owns that truth; Supervisor passes either its count or the
    exact task-id set produced by the Dispatcher so this module cannot drift
    into a second scheduler.
    """
    if isinstance(runnable_tasks, int):
        return max(0, runnable_tasks)
    if isinstance(runnable_tasks, (list, tuple, set)):
        return len(runnable_tasks)
    return 0


def _extract_runnable_task_ids(
    config: dict[str, Any],
    runnable_tasks: int | list[Any] | tuple[Any, ...] | set[Any] | None = None,
) -> set[str] | None:
    """Extract canonical runnable task IDs if passed as a collection.

    Returns None for int-only or missing inputs to signal conservative fallback.
    """
    if isinstance(runnable_tasks, int) or runnable_tasks is None:
        return None
    task_id_field = (config.get("schema", {}) or {}).get("task_id_field", "id")
    canonical_ids: set[str] = set()
    for item in runnable_tasks:
        if isinstance(item, str):
            tid = item.strip()
            if tid:
                canonical_ids.add(tid)
        elif isinstance(item, dict):
            tid = str(item.get(task_id_field) or item.get("id") or "").strip()
            if tid:
                canonical_ids.add(tid)
    return canonical_ids


def _worker_task_id(worker: dict[str, Any], task_id_field: str = "id") -> str:
    """Extract assigned task ID from a worker record."""
    if not isinstance(worker, dict):
        return ""
    if worker.get("task_id"):
        return str(worker["task_id"]).strip()
    if worker.get(task_id_field):
        return str(worker[task_id_field]).strip()
    req_task = worker.get("request_snapshot", {}).get("metadata", {}).get("task", {})
    if isinstance(req_task, dict):
        tid = str(req_task.get(task_id_field) or req_task.get("id") or "").strip()
        if tid:
            return tid
    metadata = worker.get("metadata", {})
    if isinstance(metadata, dict):
        tid = str(metadata.get(task_id_field) or metadata.get("task_id") or "").strip()
        if tid:
            return tid
    return ""


def capacity_snapshot(
    config: dict[str, Any],
    runtime_state: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    runnable_tasks: int | list[dict[str, Any]] | set[str] | None = None,
    provider_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg_slot_total = configured_slots(config)
    slot_total = effective_slots(
        config, runtime_state=runtime_state, provider_report=provider_report
    )
    active_workers = [
        worker
        for worker in (runtime_state.get("workers", {}) or {}).values()
        if str(worker.get("status") or "").lower() in ACTIVE_WORKER_STATUSES
    ]
    runnable_count = _count_runnable_tasks(
        config,
        tasks,
        runnable_tasks=runnable_tasks,
    )
    active_sidecars = sum(
        1
        for worker in active_workers
        if str((worker.get("request_snapshot", {}).get("metadata", {}).get("task", {}) or {}).get("task_class") or "").lower()
        == "sidecar"
    )
    active_count = len(active_workers)

    task_id_field = (config.get("schema", {}) or {}).get("task_id_field", "id")
    runnable_ids = _extract_runnable_task_ids(config, runnable_tasks)
    if runnable_ids is not None:
        active_runnable_count = sum(
            1
            for worker in active_workers
            if _worker_task_id(worker, task_id_field) in runnable_ids
        )
    else:
        # int-only legacy input: conservative fallback, must not over-dispatch
        active_runnable_count = active_count

    return {
        "slot_total": slot_total,
        "configured_slot_total": cfg_slot_total,
        "active_workers": active_count,
        "active_runnable_workers": active_runnable_count,
        "available_slots": max(0, slot_total - active_count),
        "runnable_tasks": runnable_count,
        "active_sidecars": active_sidecars,
        "utilization_ratio": round(active_count / slot_total, 4) if slot_total else 0.0,
    }


def expired_helper_claim_task_ids(
    tasks: list[dict[str, Any]],
    *,
    task_id_field: str = "id",
    now: datetime | None = None,
) -> list[str]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    expired: list[str] = []
    for task in tasks:
        claim = task.get("helper_execution_lease") or {}
        if not claim:
            continue
        expires_at = _parse_iso(claim.get("lease_expires_at"))
        if expires_at is None or expires_at <= current:
            task_id = str(task.get(task_id_field) or task.get("id") or "")
            if task_id:
                expired.append(task_id)
    return expired


def evaluate_chair(
    config: dict[str, Any],
    runtime_state: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    runnable_tasks: int | list[dict[str, Any]] | set[str] | None = None,
    provider_report: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    cfg = settings(config)
    snapshot = capacity_snapshot(
        config,
        runtime_state,
        tasks,
        runnable_tasks=runnable_tasks,
        provider_report=provider_report,
    )
    controller = runtime_state.setdefault("capacity_controller", {})
    changed = controller.get("snapshot") != snapshot
    controller["snapshot"] = snapshot

    stalled = snapshot["runnable_tasks"] > 0 and snapshot["active_workers"] == 0
    underutilized = (
        snapshot["slot_total"] > 0
        and snapshot["utilization_ratio"] < float(cfg["underutilization_threshold_ratio"])
    )
    for condition, active in (("stall", stalled), ("underutilization", underutilized)):
        key = f"{condition}_since"
        if active and not controller.get(key):
            controller[key] = _iso(now)
            changed = True
        elif not active and controller.pop(key, None) is not None:
            changed = True

    stall_since = _parse_iso(controller.get("stall_since"))
    under_since = _parse_iso(controller.get("underutilization_since"))
    stall_mature = bool(
        stall_since and (now - stall_since).total_seconds() >= float(cfg["stall_window_seconds"])
    )
    under_mature = bool(
        under_since
        and (now - under_since).total_seconds() >= float(cfg["underutilization_window_seconds"])
    )
    last_decision = _parse_iso((controller.get("chair_decision") or {}).get("issued_at"))
    interval_due = not last_decision or (now - last_decision).total_seconds() >= float(
        cfg["chair_interval_seconds"]
    )
    decision_needed = stall_mature or under_mature or interval_due
    if not cfg.get("enabled", True) or not decision_needed:
        return controller, changed

    reasons: list[str] = []
    if stall_mature:
        reasons.append("runnable_work_without_active_workers")
    if under_mature:
        reasons.append("sustained_underutilization")
    if interval_due:
        reasons.append("periodic_capacity_review")
    sidecar_approved = bool(
        under_mature
        and snapshot["runnable_tasks"] == 0
        and snapshot["available_slots"] > int(cfg["coordination_reserved_slots"])
    )
    decision = {
        "schema": "pantheon.capacity-chair-decision.v1",
        "issued_at": _iso(now),
        "valid_until": _iso(now + timedelta(seconds=float(cfg["chair_interval_seconds"]))),
        "reasons": reasons,
        "approve_helper_wave": bool(
            snapshot["runnable_tasks"] > snapshot.get("active_runnable_workers", snapshot["active_workers"])
            and snapshot["available_slots"] > 0
        ),
        "max_helper_claims": min(snapshot["available_slots"], 4),
        "sidecar_wave": {
            "approved": sidecar_approved,
            "reason": (
                "no_runnable_canonical_work_and_capacity_idle"
                if sidecar_approved
                else "canonical_work_or_underutilization_window_not_clear"
            ),
        },
        "snapshot": snapshot,
    }
    if controller.get("chair_decision") != decision:
        controller["chair_decision"] = decision
        changed = True
    return controller, changed


def sidecar_candidates(
    config: dict[str, Any],
    runtime_state: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    runnable_tasks: int | list[dict[str, Any]] | set[str] | None = None,
    provider_report: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    controller_cfg = settings(config)
    if not controller_cfg.get("enabled", True):
        return []
    cfg = controller_cfg["sidecars"]
    if not cfg.get("enabled", True):
        return []
    decision = (runtime_state.get("capacity_controller", {}) or {}).get("chair_decision", {}) or {}
    if cfg.get("require_chair_approval", True) and not (decision.get("sidecar_wave") or {}).get("approved"):
        return []
    valid_until = _parse_iso(decision.get("valid_until"))
    if valid_until is None or valid_until < now:
        return []

    snapshot = capacity_snapshot(
        config,
        runtime_state,
        tasks,
        runnable_tasks=runnable_tasks,
        provider_report=provider_report,
    )
    if snapshot["runnable_tasks"] > 0:
        return []
    max_by_ratio = max(0, int(snapshot["slot_total"] * float(cfg["max_capacity_ratio"])))
    existing = [task for task in tasks if str(task.get("task_class") or "").lower() == "sidecar"]
    budget = min(
        int(cfg["max_new_per_wave"]),
        max(0, int(cfg["max_active"]) - len(existing)),
        max(0, max_by_ratio - snapshot["active_sidecars"]),
    )
    if budget <= 0:
        return []
    catalog_path = Path(
        str(config.get("sidecar_catalog_path") or DEFAULT_SIDECAR_CATALOG)
    )
    try:
        catalog_payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    templates = catalog_payload.get("templates", []) if isinstance(catalog_payload, dict) else []
    existing_signatures = {
        f"{task.get('helper_parent')}:{task.get('helper_kind')}" for task in existing
    }
    schema = config.get("schema", {}) or {}
    task_id_field = schema.get("task_id_field", "id")

    candidates: list[dict[str, Any]] = []
    for template in templates:
        if not isinstance(template, dict):
            continue
        kind = str(template.get("kind") or "").strip()
        statuses = {str(value).lower() for value in template.get("parent_statuses", [])}
        parent_ids = {str(value) for value in template.get("parent_task_ids", [])}
        phase_match = str(template.get("parent_phase_match") or "")
        if not kind:
            continue
        for parent in tasks:
            parent_id = str(parent.get(task_id_field) or "")
            parent_status = str(parent.get("status") or "").lower()
            signature = f"{parent_id}:{kind}"
            if not parent_id or signature in existing_signatures:
                continue
            if statuses and parent_status not in statuses:
                continue
            if parent_ids and parent_id not in parent_ids:
                continue
            if phase_match and str(parent.get("phase") or "") != phase_match:
                continue
            # A template without an explicit parent selector is invalid. This
            # keeps the catalog, rather than controller heuristics, authoritative.
            if not statuses and not parent_ids and not phase_match:
                continue
            prose = " ".join(
                str(parent.get(key) or "")
                for key in ("blocked_reason", "next", "waiting_for")
            ).lower()
            if parent_status == "blocked" and any(marker in prose for marker in HARD_GATE_MARKERS):
                continue
            sidecar_id = build_sidecar_task_id(parent_id, kind)
            variables = {
                "parent_task_id": parent_id,
                "sidecar_task_id": sidecar_id,
            }

            artifacts = [
                _render_template(value, variables)
                for value in template.get("artifact_targets", [])
            ]
            sidecar_dict = {
                "id": sidecar_id,
                "title": _render_template(
                    template.get("title_template") or sidecar_id, variables
                ),
                "summary_zh": _render_template(
                    template.get("summary_zh_template"), variables
                ),
                "phase": "Capacity sidecar",
                "priority": "P3",
                "status": "todo",
                "owner": "AUTO_ASSIGN",
                "reviewer": "DIFFERENT_AGENT_REQUIRED",
                "depends_on": [],
                "repository": parent.get("repository"),
                "base_branch": parent.get("base_branch") or "dev",
                "artifacts": artifacts,
                "task_class": "sidecar",
                "auto_generated": True,
                "helper_parent": parent_id,
                "helper_kind": kind,
                "mutates_canonical": bool(template.get("mutates_canonical", False)),
                "auto_created_by": "capacity-controller",
                "expires_at": _iso(now + timedelta(seconds=float(cfg["ttl_seconds"]))),
                "acceptance": [
                    "Produce evidence-backed blocker diagnosis",
                    "Add bounded verification without changing canonical product behavior",
                ],
                "next": "Capacity Chair approved a bounded diagnostic sidecar wave.",
            }
            if task_id_field != "id":
                sidecar_dict[task_id_field] = sidecar_id
            candidates.append(sidecar_dict)
            existing_signatures.add(signature)
            if len(candidates) >= budget:
                return candidates
    return candidates
