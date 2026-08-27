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

from common import normalize_agent_id
from dispatch_policy import normalized_status_set, ready_dispatch_settings
from task_archive import TaskResolver
from worker_failure_policy import agent_can_take_task, is_human_gate_agent

ACTIVE_WORKER_STATUSES = {
    "running",
    "waiting_approval",
    "suspended_approval",
    "retry_backoff",
    "manual_pending",
    "stalled",
}
RUNNABLE_TASK_STATUSES = {"todo", "in_progress"}
HARD_GATE_MARKERS = {
    "human gate",
    "manual approval",
    "credential",
    "license",
    "production proof",
    "legal",
}
DEFAULT_SIDECAR_CATALOG = Path(__file__).with_name("sidecar_catalog.json")


def _supervisor_module():
    import supervisor
    return supervisor


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


def is_task_runnable(
    config: dict[str, Any],
    task: dict[str, Any],
    resolver: TaskResolver | dict[str, dict[str, Any]] | None = None,
) -> bool:
    if not isinstance(task, dict):
        return False
    if task.get("non_dispatchable"):
        return False

    owner = str(task.get("owner") or "").strip()
    waiting_for = str(task.get("waiting_for") or "").strip()
    if is_human_gate_agent(owner) or is_human_gate_agent(waiting_for):
        return False

    sv = _supervisor_module()
    if sv.task_is_human_gate(task):
        return False
    if sv.task_is_sidecar(task):
        return False

    settings_map = ready_dispatch_settings(config)
    owned_statuses = normalized_status_set(
        settings_map.get("owned_statuses"), ["in_progress", "todo"]
    )
    status = str(task.get("status") or "").lower()
    if status not in owned_statuses:
        return False

    dependency_done_statuses = normalized_status_set(
        settings_map.get("dependency_done_statuses"), ["done"]
    )
    if resolver is not None and not sv.dependencies_satisfied(
        task, resolver, dependency_done_statuses
    ):
        return False

    if owner and owner not in {"AUTO_ASSIGN", "DIFFERENT_AGENT_REQUIRED"}:
        agents = config.get("agents", {}) or {}
        norm_owner = normalize_agent_id(owner)
        if norm_owner in agents or any(normalize_agent_id(k) == norm_owner for k in agents):
            if not agent_can_take_task(config, owner, task):
                return False

    return True


def capacity_snapshot(
    config: dict[str, Any],
    runtime_state: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    slot_total = configured_slots(config)
    active_workers = [
        worker
        for worker in (runtime_state.get("workers", {}) or {}).values()
        if str(worker.get("status") or "").lower() in ACTIVE_WORKER_STATUSES
    ]
    resolver = TaskResolver(tasks)
    runnable = [
        task
        for task in tasks
        if is_task_runnable(config, task, resolver=resolver)
    ]
    active_sidecars = sum(
        1
        for worker in active_workers
        if str((worker.get("request_snapshot", {}).get("metadata", {}).get("task", {}) or {}).get("task_class") or "").lower()
        == "sidecar"
    )
    active_count = len(active_workers)
    return {
        "slot_total": slot_total,
        "active_workers": active_count,
        "available_slots": max(0, slot_total - active_count),
        "runnable_tasks": len(runnable),
        "active_sidecars": active_sidecars,
        "utilization_ratio": round(active_count / slot_total, 4) if slot_total else 0.0,
    }


def expired_helper_claim_task_ids(
    tasks: list[dict[str, Any]], *, now: datetime | None = None
) -> list[str]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    expired: list[str] = []
    for task in tasks:
        claim = task.get("helper_execution_lease") or {}
        if not claim:
            continue
        expires_at = _parse_iso(claim.get("lease_expires_at"))
        if expires_at is None or expires_at <= current:
            task_id = str(task.get("id") or "")
            if task_id:
                expired.append(task_id)
    return expired


def evaluate_chair(
    config: dict[str, Any],
    runtime_state: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    cfg = settings(config)
    snapshot = capacity_snapshot(config, runtime_state, tasks)
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
            snapshot["runnable_tasks"] > snapshot["active_workers"]
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

    snapshot = capacity_snapshot(config, runtime_state, tasks)
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
            parent_id = str(parent.get("id") or "")
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
            kind_slug = kind.replace("_", "-").upper()
            sidecar_id = f"{parent_id}-SIDECAR-{kind_slug}"
            variables = {
                "parent_task_id": parent_id,
                "sidecar_task_id": sidecar_id,
            }

            artifacts = [
                _render_template(value, variables)
                for value in template.get("artifact_targets", [])
            ]
            candidates.append(
                {
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
            )
            existing_signatures.add(signature)
            if len(candidates) >= budget:
                return candidates
    return candidates
