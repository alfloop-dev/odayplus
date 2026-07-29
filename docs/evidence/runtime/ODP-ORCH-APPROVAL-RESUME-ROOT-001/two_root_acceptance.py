#!/usr/bin/env python3
"""Two-root deferred-approval resume acceptance harness.

Models the live topology of the 2026-07-29 failure without mocks: the hook
executable lives in one checkout (the *control root*) while ``PANTHEON_STATUS_ROOT``
names the fleet whose approval queue is authoritative (the *live root*).

It drives the real CLI entrypoint::

    python3 <control-root>/.orchestrator/permission_broker.py hook <Event>

feeding the hook payload on stdin exactly as Claude Code does, and reports a
non-secret receipt (session id, approval id, tool signature, queue root,
decision per phase).

Usage::

    python3 two_root_acceptance.py --broker-dir <dir-with-.orchestrator> --label fixed

Exit code 0 means the observed verdict is internally consistent; the caller
compares the verdict against what that revision is expected to do.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

APPROVED_COMMAND = (
    "docker rm -f odp-acceptance-probe-pg >/dev/null 2>&1; "
    "docker run -d --name odp-acceptance-probe-pg -e POSTGRES_PASSWORD=probe postgres:16"
)
APPROVED_INPUT = {"command": APPROVED_COMMAND, "description": "Start isolated probe postgres"}
APPROVED_RULE = f"Bash({APPROVED_COMMAND})"
SUSPENDED_ASK_RULE = "Bash(docker run *)"
SESSION_ID = "c2cafc00-aa5f-4340-8c49-5eb1aedd30b2"
FOREIGN_SESSION_ID = "11111111-2222-3333-4444-555555555555"
LIVE_APPROVAL_ID = "apr-20260729T083018Z-f3737b93"
CONTROL_APPROVAL_ID = "apr-20260729T082950Z-578a3304"
DENY_APPROVAL_ID = "apr-20260729T083018Z-deny0001"


def tool_input_signature(tool_input: dict[str, Any]) -> str:
    """Mirror of common.approval_tool_input_signature, kept independent on purpose."""
    payload = json.dumps(tool_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def base_config() -> dict[str, Any]:
    return {
        "paths": {
            "status_file": "ai-status.json",
            "activity_log": "ai-activity-log.jsonl",
            "current_work": "current-work.md",
            "state_file": ".orchestrator/state.json",
            "event_queue": ".orchestrator/event-queue.jsonl",
            "approval_queue": ".orchestrator/approval-queue.json",
            "evidence_dir": ".orchestrator/evidence",
        },
        "providers": {"claude": {"delivery_mode": "claude_cli", "broker": {"approval_wait_seconds": 60}}},
    }


def seed_root(root: Path) -> None:
    write_json(root / ".orchestrator" / "config.json", base_config())
    write_json(root / ".orchestrator" / "state.json", {"workers": {}, "queue": {"events": {}}})
    write_json(root / "ai-status.json", {"tasks": [], "agents": []})
    write_json(root / ".claude" / "settings.local.json", {"permissions": {"allow": [], "ask": []}})
    (root / ".orchestrator" / "event-queue.jsonl").write_text("", encoding="utf-8")


def resolved_allow_item(signature: str) -> dict[str, Any]:
    return {
        "approval_id": LIVE_APPROVAL_ID,
        "status": "resolved",
        "decision": "allow",
        "provider": "claude",
        "session_id": SESSION_ID,
        "tool_name": "Bash",
        "tool_input": APPROVED_INPUT,
        "tool_input_signature": signature,
        "risk_class": "needs_review",
        "resume_override_active": True,
        "resume_override_rule": APPROVED_RULE,
        "resume_override_rule_inserted": True,
        "resume_override_suspended_ask_rules": [SUSPENDED_ASK_RULE],
        "resume_override_consumed_at": None,
        "note": "Coordinator one-time allow: exact command only.",
        "created_at": "2026-07-29T08:30:18Z",
        "resolved_at": "2026-07-29T08:31:07Z",
    }


def resolved_deny_item(signature: str) -> dict[str, Any]:
    return {
        "approval_id": DENY_APPROVAL_ID,
        "status": "resolved",
        "decision": "deny",
        "provider": "claude",
        "session_id": SESSION_ID,
        "tool_name": "Bash",
        "tool_input": APPROVED_INPUT,
        "tool_input_signature": signature,
        "risk_class": "needs_review",
        "resume_override_active": False,
        "resume_override_consumed_at": None,
        "note": "Coordinator denied: container churn not authorised.",
        "created_at": "2026-07-29T08:30:18Z",
        "resolved_at": "2026-07-29T08:31:07Z",
    }


def pending_item(signature: str) -> dict[str, Any]:
    return {
        "approval_id": CONTROL_APPROVAL_ID,
        "status": "pending",
        "decision": None,
        "provider": "claude",
        "session_id": SESSION_ID,
        "tool_name": "Bash",
        "tool_input": APPROVED_INPUT,
        "tool_input_signature": signature,
        "risk_class": "needs_review",
        "resume_override_active": False,
        "created_at": "2026-07-29T08:29:50Z",
    }


def run_hook(
    control_root: Path,
    status_root: Path,
    event: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Invoke the real hook CLI the way Claude Code does."""
    env = dict(os.environ)
    env.update(
        {
            "PANTHEON_STATUS_ROOT": str(status_root),
            "ORCH_PROVIDER": "claude",
            "ORCH_SESSION_ID": SESSION_ID,
            "ORCH_TASK_ID": "ODP-ORCH-APPROVAL-RESUME-ROOT-001",
        }
    )
    proc = subprocess.run(
        [sys.executable, str(control_root / ".orchestrator" / "permission_broker.py"), "hook", event],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(control_root),
        timeout=120,
    )
    decision = None
    emitted: dict[str, Any] | None = None
    stdout = proc.stdout.strip()
    if stdout:
        emitted = json.loads(stdout.splitlines()[-1])
        output = emitted.get("hookSpecificOutput", {})
        decision = output.get("permissionDecision") or output.get("decision", {}).get("behavior")
    return {
        "event": event,
        "exit_code": proc.returncode,
        "decision": decision,
        "reason": (emitted or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason"),
        "stderr_tail": proc.stderr.strip().splitlines()[-3:] if proc.stderr.strip() else [],
    }


def pre_tool_use_payload(tool_input: dict[str, Any], session_id: str = SESSION_ID) -> dict[str, Any]:
    return {"session_id": session_id, "tool_name": "Bash", "tool_input": tool_input}


def read_permissions(root: Path) -> dict[str, list[str]]:
    data = json.loads((root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    return data.get("permissions", {})


def read_queue(root: Path) -> dict[str, Any]:
    return json.loads((root / ".orchestrator" / "approval-queue.json").read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker-dir", required=True, help="Directory containing the .orchestrator package under test")
    parser.add_argument("--label", required=True, help="Revision label recorded in the receipt")
    parser.add_argument("--out", help="Write the JSON receipt to this path as well as stdout")
    args = parser.parse_args()

    broker_dir = Path(args.broker_dir).resolve()
    orchestrator_src = broker_dir / ".orchestrator"
    if not (orchestrator_src / "permission_broker.py").is_file():
        print(f"no permission_broker.py under {orchestrator_src}", file=sys.stderr)
        return 2

    signature = tool_input_signature(APPROVED_INPUT)
    workdir = Path(tempfile.mkdtemp(prefix="odp-two-root-"))
    try:
        # Control root: owns the hook executable, only ever saw an unresolved request.
        control_root = workdir / "control-root"
        control_root.mkdir(parents=True)
        shutil.copytree(orchestrator_src, control_root / ".orchestrator", ignore=shutil.ignore_patterns("__pycache__"))
        seed_root(control_root)
        write_json(
            control_root / ".orchestrator" / "approval-queue.json",
            {"version": 2, "pending": [pending_item(signature)], "history": []},
        )

        # Live root: the fleet the supervisor actually resolved the approval in.
        live_root = workdir / "live-root"
        live_root.mkdir(parents=True)
        seed_root(live_root)
        write_json(
            live_root / ".orchestrator" / "approval-queue.json",
            {"version": 2, "pending": [], "history": [resolved_allow_item(signature)]},
        )
        write_json(
            live_root / ".claude" / "settings.local.json",
            {"permissions": {"allow": [APPROVED_RULE], "ask": []}},
        )

        phases: dict[str, Any] = {}
        phases["resume_exact_approved_input"] = run_hook(
            control_root, live_root, "PreToolUse", pre_tool_use_payload(APPROVED_INPUT)
        )
        phases["different_command_same_session"] = run_hook(
            control_root,
            live_root,
            "PreToolUse",
            pre_tool_use_payload({"command": "docker run -d --name unrelated postgres:16"}),
        )
        phases["same_input_foreign_session"] = run_hook(
            control_root, live_root, "PreToolUse", pre_tool_use_payload(APPROVED_INPUT, FOREIGN_SESSION_ID)
        )

        permissions_before = read_permissions(live_root)
        phases["post_tool_use_consume"] = run_hook(
            control_root,
            live_root,
            "PostToolUse",
            {"session_id": SESSION_ID, "tool_name": "Bash", "tool_input": APPROVED_INPUT},
        )
        permissions_after = read_permissions(live_root)
        live_history = read_queue(live_root)["history"]
        consumed_item = next((i for i in live_history if i.get("approval_id") == LIVE_APPROVAL_ID), {})

        phases["replay_after_consume"] = run_hook(
            control_root, live_root, "PreToolUse", pre_tool_use_payload(APPROVED_INPUT)
        )

        # Separate live root carrying a resolved deny for the same signature.
        deny_root = workdir / "live-root-deny"
        deny_root.mkdir(parents=True)
        seed_root(deny_root)
        write_json(
            deny_root / ".orchestrator" / "approval-queue.json",
            {"version": 2, "pending": [], "history": [resolved_deny_item(signature)]},
        )
        phases["resolved_deny"] = run_hook(
            control_root, deny_root, "PreToolUse", pre_tool_use_payload(APPROVED_INPUT)
        )

        resumed = phases["resume_exact_approved_input"]["decision"] == "allow"
        override_consumed = bool(consumed_item.get("resume_override_consumed_at"))
        rule_withdrawn = APPROVED_RULE not in (permissions_after.get("allow") or [])
        ask_restored = SUSPENDED_ASK_RULE in (permissions_after.get("ask") or [])
        single_use = phases["replay_after_consume"]["decision"] == "defer"
        no_broadening = (
            phases["different_command_same_session"]["decision"] == "defer"
            and phases["same_input_foreign_session"]["decision"] == "defer"
        )
        deny_never_allows = phases["resolved_deny"]["decision"] != "allow"

        checks = {
            "resume_honours_authoritative_root": resumed,
            "override_consumed_in_authoritative_root": override_consumed,
            "temporary_allow_rule_withdrawn": rule_withdrawn,
            "suspended_ask_rule_restored": ask_restored,
            "override_is_single_use": single_use,
            "no_permission_broadening": no_broadening,
            "resolved_deny_never_allows": deny_never_allows,
        }
        verdict = "resume_honoured" if all(checks.values()) else "re_deferred_or_incomplete"

        receipt = {
            "label": args.label,
            "verdict": verdict,
            "broker_dir": str(broker_dir),
            "broker_sha256": hashlib.sha256(
                (orchestrator_src / "permission_broker.py").read_bytes()
            ).hexdigest(),
            "control_root": str(control_root),
            "authoritative_status_root": str(live_root),
            "session_id": SESSION_ID,
            "authoritative_approval_id": LIVE_APPROVAL_ID,
            "control_root_approval_id": CONTROL_APPROVAL_ID,
            "tool_name": "Bash",
            "tool_input_signature": signature,
            "control_root_queue": str(control_root / ".orchestrator" / "approval-queue.json"),
            "authoritative_queue": str(live_root / ".orchestrator" / "approval-queue.json"),
            "phases": phases,
            "permissions_before": permissions_before,
            "permissions_after": permissions_after,
            "checks": checks,
        }
        rendered = json.dumps(receipt, indent=2, ensure_ascii=False)
        print(rendered)
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(rendered + "\n", encoding="utf-8")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
