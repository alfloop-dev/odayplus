#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from common import (
    agent_config_for,
    config_path,
    display_name_for,
    execution_context_files,
    load_config,
    load_json,
    load_status,
    new_runtime_id,
    render_template,
    resolve_path,
    snapshot_task,
    utc_now,
    write_activity_log,
)
from runtime_state import enqueue_event, load_runtime_state, save_runtime_state
from task_archive import DEFAULT_RECENT_LIMIT, recent_terminal_summaries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch ai-status.json and wake the right local agent with a minimal event.")
    parser.add_argument("--config", default=".orchestrator/config.json", help="Path to orchestrator config.")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit.")
    parser.add_argument("--replay", action="store_true", help="Replay pending events immediately on startup.")
    parser.add_argument("--poll-interval", type=float, default=None, help="Override poll interval seconds.")
    return parser.parse_args()


def handoff_key(handoff: dict[str, Any]) -> str:
    parts = [
        str(handoff.get("task_id") or ""),
        str(handoff.get("from") or ""),
        str(handoff.get("to") or ""),
        str(handoff.get("created_at") or ""),
        str(handoff.get("message") or ""),
    ]
    return "|".join(parts)


def enqueue_runtime_events_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("events", {}).get("enqueue_runtime_events", False))


def build_snapshot(config: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    schema = config["schema"]
    tasks_path = schema["tasks_path"]
    handoffs_path = schema["handoffs_path"]
    tasks = {
        task.get(schema["task_id_field"]): snapshot_task(task, schema)
        for task in status.get(tasks_path, [])
        if task.get(schema["task_id_field"])
    }
    pending_handoffs = [
        handoff
        for handoff in status.get(handoffs_path, [])
        if str(handoff.get("status") or "").lower() in {s.lower() for s in config.get("events", {}).get("pending_handoff_statuses", ["pending"])}
    ]
    recent_limit = int(config.get("watcher", {}).get("recent_terminal_limit", DEFAULT_RECENT_LIMIT))
    return {
        "tasks": tasks,
        "recent_terminal_tasks": recent_terminal_summaries(limit=recent_limit),
        "pending_handoff_keys": [handoff_key(item) for item in pending_handoffs],
        "pending_handoffs": pending_handoffs,
        "status_updated_at": status.get("updated_at"),
    }


def resolve_target_for_status(task: dict[str, Any], status_value: str, config: dict[str, Any]) -> str | None:
    status_targets = config.get("events", {}).get("status_targets", {})
    target_field = status_targets.get(status_value)
    if not target_field:
        return None
    if target_field == "owner":
        return task.get(config["schema"]["assignee_field"])
    if target_field == "reviewer":
        return task.get(config["schema"]["reviewer_field"])
    return task.get(target_field)


def resolve_target_for_waiting_status(status_value: str, config: dict[str, Any]) -> str | None:
    for pattern in config.get("events", {}).get("waiting_status_patterns", []):
        match = re.match(pattern, status_value)
        if not match:
            continue
        if match.groupdict().get("agent"):
            return match.group("agent")
    return None


def build_task_status_event(task_id: str, task: dict[str, Any], new_status: str, config: dict[str, Any]) -> dict[str, Any] | None:
    lower_status = new_status.lower()
    review_statuses = {value.lower() for value in config.get("events", {}).get("review_statuses", ["review"])}

    if lower_status in review_statuses and task.get("reviewer"):
        return {
            "key": f"{task_id}:status:{lower_status}:{task.get('reviewer')}",
            "task_id": task_id,
            "target_agent": task.get("reviewer"),
            "reason": f"status:{new_status}",
            "task": task,
        }

    waiting_target = resolve_target_for_waiting_status(new_status, config)
    if waiting_target:
        return {
            "key": f"{task_id}:status:{lower_status}:{waiting_target}",
            "task_id": task_id,
            "target_agent": waiting_target,
            "reason": f"status:{new_status}",
            "task": task,
        }

    target = resolve_target_for_status(task, new_status, config)
    if target:
        return {
            "key": f"{task_id}:status:{lower_status}:{target}",
            "task_id": task_id,
            "target_agent": target,
            "reason": f"status:{new_status}",
            "task": task,
        }
    return None


def compute_replay_events(current: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for task_id, task in current.get("tasks", {}).items():
        new_status = str(task.get("status") or "")
        if not new_status:
            continue
        event = build_task_status_event(task_id, task, new_status, config)
        if event:
            events.append(event)

    if config.get("events", {}).get("watch_handoffs", True):
        for handoff in current.get("pending_handoffs", []):
            events.append(
                {
                    "key": f"handoff:{handoff_key(handoff)}",
                    "task_id": handoff.get("task_id"),
                    "target_agent": handoff.get("to"),
                    "reason": "handoff_pending",
                    "task": {
                        "id": handoff.get("task_id"),
                        "artifacts": [],
                        "next": handoff.get("message"),
                    },
                    "handoff": handoff,
                }
            )
    return events


def compute_events(previous: dict[str, Any], current: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    previous_tasks = previous.get("tasks", {})
    current_tasks = current.get("tasks", {})
    {value.lower() for value in config.get("events", {}).get("review_statuses", ["review"])}

    for task_id, task in current_tasks.items():
        old_task = previous_tasks.get(task_id)
        if not old_task:
            continue

        if config.get("events", {}).get("watch_assignee_changes", True) and task.get("owner") != old_task.get("owner") and task.get("owner"):
            events.append(
                {
                    "key": f"{task_id}:owner:{task.get('owner')}:{task.get('status')}",
                    "task_id": task_id,
                    "target_agent": task.get("owner"),
                    "reason": "assignee_changed",
                    "task": task,
                }
            )

        if config.get("events", {}).get("watch_reviewer_changes", False) and task.get("reviewer") != old_task.get("reviewer") and task.get("reviewer"):
            events.append(
                {
                    "key": f"{task_id}:reviewer:{task.get('reviewer')}:{task.get('status')}",
                    "task_id": task_id,
                    "target_agent": task.get("reviewer"),
                    "reason": "reviewer_changed",
                    "task": task,
                }
            )

        new_status = str(task.get("status") or "")
        old_status = str(old_task.get("status") or "")
        if new_status == old_status:
            continue

        event = build_task_status_event(task_id, task, new_status, config)
        if event:
            events.append(event)

    if config.get("events", {}).get("watch_handoffs", True):
        previous_pending = set(previous.get("pending_handoff_keys", []))
        for handoff in current.get("pending_handoffs", []):
            key = handoff_key(handoff)
            if key in previous_pending:
                continue
            events.append(
                {
                    "key": f"handoff:{key}",
                    "task_id": handoff.get("task_id"),
                    "target_agent": handoff.get("to"),
                    "reason": "handoff_pending",
                    "task": {
                        "id": handoff.get("task_id"),
                        "artifacts": [],
                        "next": handoff.get("message"),
                    },
                    "handoff": handoff,
                }
            )
    return events


def render_wakeup_message(config: dict[str, Any], event: dict[str, Any], target_agent: str) -> str:
    agent = agent_config_for(config, target_agent)
    template_path = resolve_path(agent.get("wake_template") or ".orchestrator/templates/wakeup.txt")
    if template_path is None:
        raise RuntimeError("Unable to resolve wake-up template path")
    context_files = event.get("context_files") or execution_context_files(config, event.get("task_id"))
    target_files = event.get("task", {}).get("artifacts") or []
    task_payload = event.get("task", {}) or {}
    sidecar_guardrails = ""
    if str(task_payload.get("task_class") or "").lower() == "sidecar":
        helper_parent = str(task_payload.get("helper_parent") or "").strip() or "(unknown parent)"
        helper_kind = str(task_payload.get("helper_kind") or "").strip() or "support_slice"
        sidecar_guardrails = (
            "\n這是一個 sidecar support slice，不是主線 canonical 實作。\n"
            f"- Parent Task: {helper_parent}\n"
            f"- Helper Kind: {helper_kind}\n"
            "- 只允許建立或更新支援性材料與 handoff packet。\n"
            "- 不要修改 L1 canonical truth、核心 contract 真相、或主要 runtime/registry/governance 實作。\n"
            "- 盡量把輸出限制在上面列出的相關檔案；若需新增檔案，只能新增 support artifact。\n"
            "- 完成後請交接給指定 reviewer，由 parent owner 決定是否吸收進主線。\n"
        )
    task_id = str(event.get("task_id") or "").strip()
    reason = str(event.get("reason") or "wakeup").strip()
    normalized_reason = reason.lower()
    if normalized_reason == "owned_finalize_dispatch":
        lifecycle_guardrails = (
            "這次是 immutable finalize dispatch。不得修改 tracked files、merge/rebase dev、"
            "建立 commit、push branch 或再次執行 task_finalize.sh。只可核對 approved_head、"
            "PR 與 CI；PR 尚未 merge 就保持 review_approved 並退出，merge 後才由 owner 執行 done。"
        )
    elif normalized_reason in {"review_ready_dispatch", "status:review"}:
        lifecycle_guardrails = (
            "這次是 reviewer dispatch。程序退出前必須做出可稽核的 review 決定："
            "通過則 approve，發現問題則 reopen／退回 in_progress。只新增 review note、"
            "但讓 task 留在 review，會被 Supervisor 判定為 no-progress failure。"
        )
    elif normalized_reason in {
        "owned_ready_dispatch",
        "owned_in_progress_dispatch",
    }:
        lifecycle_guardrails = (
            "這次是 owner dispatch。若工作已可送審，程序退出前必須先用 "
            "delivery_toolchain/git/task_finalize.sh 推送 task branch、建立 PR 並原子記錄 review submission；"
            "不得直接 handoff／re_review 製造沒有遠端 PR 證明的 review。只寫『ready/awaiting review』"
            "但不完成正式提交，會被判定為 "
            "no-progress failure。若只完成一段增量，至少要留下新的 task branch commit 或實質 next 狀態。"
        )
    else:
        lifecycle_guardrails = ""
    branch_workflow = config.get("branch_workflow") if isinstance(config.get("branch_workflow"), dict) else {}
    base_branch = str(branch_workflow.get("dev_branch") or "dev")
    task_branch_prefix = str(branch_workflow.get("task_branch_prefix") or "task/")
    task_id_kebab = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-") if task_id else "none"
    branch_name = f"{task_branch_prefix}{task_id}" if task_id else f"{task_branch_prefix}(none)"
    lane = re.sub(r"[^a-z0-9]+", "-", str(target_agent or "").lower()).strip("-") or "unknown"
    if normalized_reason == "owned_finalize_dispatch":
        branch_work_guardrails = (
            "這是 reviewer-approved immutable head 的 finalize lane：\n"
            f"- 核准分支是 `{branch_name}`；只能讀取與核對，不可更新 branch。\n"
            "- 即使 branch 落後 dev，也不可 merge、rebase、cherry-pick、commit 或 push；merge queue 會在暫存 ref 組合 base。\n"
            "- working tree 若有 tracked diff，回報 blocker 並停止，不可把它納入已核准交付。"
        )
        finalize_guardrails = (
            "依 `.orchestrator/skills/task-closeout-finalization.md` 的 immutable finalize 流程："
            "確認 exact approved SHA 的 PR 已 merged，再用 "
            f"`AI_NAME={display_name_for(config, agent['id'])} \"$PANTHEON_STATUS_ROOT/scripts/ai-status.sh\" done` 結案。"
        )
    else:
        branch_work_guardrails = (
            "進入 task 工作前，先確認你在正確的 branch 上：\n"
            f"- 預期 branch 名稱：`{branch_name}`（從 `{base_branch}` 開出的 per-task branch；task id kebab: `{task_id_kebab}`）。\n"
            f"- 如果目前 branch 不對，優先使用 `./delivery_toolchain/git/task_start.sh \"{task_id}\"`，不要手寫臨時 branch 規則。\n"
            "- 如果 working tree 有未 commit diff 且不屬於這個 task，回報 blocker，不要 stash、不要繼續。\n"
            "- 任何跨檔案或 routing 接點的 task-owned 改動，到可描述的中間狀態就依 worker-anchor-commit 規則做 anchor commit。\n"
            f"- Anchor commit subject 建議：`{task_id}: anchor <scope>`；commit body 保留必要 trailers。"
        )
        finalize_guardrails = ""
    variables = {
        "context_files": "\n".join(f"- {path}" for path in context_files) if context_files else "- AI_COLLABORATION_GUIDE.md",
        "task_id": task_id or "(none)",
        "task_id_kebab": task_id_kebab,
        "lane": lane,
        "base_branch": base_branch,
        "branch_name": branch_name,
        "branch_start_command": f"./delivery_toolchain/git/task_start.sh \"{task_id}\"" if task_id else "./delivery_toolchain/git/task_start.sh <TASK-ID>",
        "anchor_commit_subject": f"{task_id}: anchor <scope>" if task_id else "<TASK-ID>: anchor <scope>",
        "reason": reason,
        "target_files": "\n".join(f"- {path}" for path in target_files) if target_files else "- (none inferred)",
        "sidecar_guardrails": sidecar_guardrails.rstrip(),
        "target_agent_display_name": display_name_for(config, agent["id"]),
        "lifecycle_guardrails": lifecycle_guardrails,
        "branch_work_guardrails": branch_work_guardrails,
        "finalize_guardrails": finalize_guardrails,
    }
    return render_template(template_path, variables).strip() + "\n"


def queue_delivery_event(config: dict[str, Any], event: dict[str, Any]) -> bool:
    target_agent = event.get("target_agent")
    if not target_agent:
        write_activity_log(
            config,
            {
                "type": "wake_skipped",
                "task_id": event.get("task_id"),
                "message": f"Skipped wake-up with no target agent for reason {event.get('reason')}.",
            },
        )
        return False

    agent = agent_config_for(config, target_agent)
    context_files = event.get("context_files") or execution_context_files(config, event.get("task_id"))
    event["context_files"] = context_files
    message = render_wakeup_message(config, event, target_agent)
    queue_payload = {
        "event_id": new_runtime_id("evt"),
        "created_at": utc_now(),
        "event_key": event.get("key"),
        "task_id": event.get("task_id"),
        "target_agent": agent["id"],
        "target_display_name": display_name_for(config, agent["id"]),
        "provider": agent.get("provider", agent["id"]),
        "reason": event.get("reason"),
        "message": message,
        "context_files": context_files,
        "target_files": event.get("task", {}).get("artifacts") or [],
        "metadata": {"handoff": event.get("handoff"), "task": event.get("task", {})},
    }
    enqueue_event(config, queue_payload)
    write_activity_log(
        config,
        {
            "type": "wake_queued",
            "task_id": event.get("task_id"),
            "target_agent": display_name_for(config, agent["id"]),
            "delivery_mode": config.get("providers", {}).get(agent.get("provider", agent["id"]), {}).get(
                "delivery_mode", agent.get("adapter", "file_inbox")
            ),
            "message": f"Wake-up queued for supervisor: {event.get('reason')}",
            "queue_event_id": queue_payload["event_id"],
        },
    )
    return True


def trim_seen_events(state: dict[str, Any], max_entries: int) -> None:
    seen = state.get("seen_event_keys", {})
    if len(seen) <= max_entries:
        return
    ordered = sorted(seen.items(), key=lambda item: item[1])
    state["seen_event_keys"] = dict(ordered[-max_entries:])


def run_scan(config: dict[str, Any], state: dict[str, Any], replay: bool, provider_capabilities: dict[str, Any]) -> bool:
    # The supervisor owns runtime state.  A disabled event transport must not
    # still mirror the canonical task store into that state: the mirror is a
    # stale second truth and used to be rewritten on every loop.
    if not enqueue_runtime_events_enabled(config):
        return False
    status = load_status(config)
    snapshot = build_snapshot(config, status)
    is_first_run = not state.get("initialized_at")
    if is_first_run and not replay and not config.get("watcher", {}).get("replay_on_start", False):
        state["initialized_at"] = utc_now()
        state["last_scan_at"] = utc_now()
        state["tasks"] = snapshot["tasks"]
        state["recent_terminal_tasks"] = snapshot.get("recent_terminal_tasks", [])
        state["pending_handoff_keys"] = snapshot["pending_handoff_keys"]
        save_runtime_state(config, state)
        return False

    events = compute_events(state, snapshot, config)
    if replay:
        merged_events: dict[str, dict[str, Any]] = {}
        for event in compute_replay_events(snapshot, config):
            merged_events[event["key"]] = event
        for event in events:
            merged_events[event["key"]] = event
        events = list(merged_events.values())

    seen = state.setdefault("seen_event_keys", {})
    changed = False
    for event in events:
        if event["key"] in seen and not replay:
            continue
        queued = queue_delivery_event(config, event)
        if queued:
            seen[event["key"]] = utc_now()
            changed = True

    state["initialized_at"] = state.get("initialized_at") or utc_now()
    state["last_scan_at"] = utc_now()
    state["tasks"] = snapshot["tasks"]
    state["recent_terminal_tasks"] = snapshot.get("recent_terminal_tasks", [])
    state["pending_handoff_keys"] = snapshot["pending_handoff_keys"]
    trim_seen_events(state, int(config.get("watcher", {}).get("max_seen_events", 2000)))
    save_runtime_state(config, state)
    return changed


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    state = load_runtime_state(config)
    provider_capabilities = load_json(config_path(config, "provider_capabilities"), default={})

    poll_interval = args.poll_interval or float(config.get("watcher", {}).get("poll_interval_seconds", 2.0))
    run_scan(config, state, replay=args.replay, provider_capabilities=provider_capabilities)
    if args.once:
        return 0

    while True:
        time.sleep(poll_interval)
        state = load_runtime_state(config)
        run_scan(config, state, replay=False, provider_capabilities=provider_capabilities)


if __name__ == "__main__":
    raise SystemExit(main())
