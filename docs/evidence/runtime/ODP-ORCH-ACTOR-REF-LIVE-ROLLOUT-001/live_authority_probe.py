#!/usr/bin/env python3
"""Read-only live authority probe for a deployed `ai_status.py`.

Loads the *deployed* module from an explicit path, points it at the live
status root, and prints what the merged Supervisor config admits as an actor
reference. Nothing here calls a mutating command; the live fingerprints are
captured before and after and printed for comparison.

Usage:
    PANTHEON_STATUS_ROOT=/home/lupin/oday-plus-supervisor-live \
    python3 live_authority_probe.py <path to deployed ai_status.py>
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

LIVE_ROOT = Path("/home/lupin/oday-plus-supervisor-live")
AUDITED_ACTORS = ("Codex5", "Codex6", "Codex8", "Codex9")
MUST_RESOLVE = (
    "Codex3",
    "Codex4",
    "Codex5",
    "Codex6",
    "Codex7",
    "Codex8",
    "Codex9",
    "Human/Ops",
    "Orchestrator",
    "CodexCoordinator",
)
MUST_REJECT = ("Gemini", "Gemini2", "Copilot")
WATCHED = ("ai-status.json", "ai-activity-log.jsonl", "current-work.md", "dashboard-bundle.json")


def digest(path: Path) -> str:
    if not path.exists():
        return "ABSENT"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("deployed_ai_status", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def actor_references(status: dict) -> list[tuple[str, str, str]]:
    """Every place one of the audited Codex actors is named."""
    found: list[tuple[str, str, str]] = []
    for task in status.get("tasks", []):
        for field in ("owner", "reviewer"):
            if task.get(field) in AUDITED_ACTORS:
                found.append((task["id"], field, task[field]))
    for blocker in status.get("blockers", []):
        for field in ("waiting_for", "raised_by", "owner"):
            if blocker.get(field) in AUDITED_ACTORS:
                found.append((f"blocker[{blocker.get('task_id')}]", field, blocker[field]))
    for handoff in status.get("handoffs", []):
        for field in ("from", "to", "from_agent", "to_agent"):
            if handoff.get(field) in AUDITED_ACTORS:
                found.append((f"handoff[{handoff.get('task_id')}]", field, handoff[field]))
    return found


def main() -> int:
    deployed = Path(sys.argv[1]).resolve()
    baseline_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None

    before = {name: digest(LIVE_ROOT / name) for name in WATCHED}
    module = load_module(deployed)

    print("# Live merged-config actor authority — read-only probe")
    print(f"# deployed module        : {deployed}")
    print(f"#   sha256               : {hashlib.sha256(deployed.read_bytes()).hexdigest()}")
    print(f"# probe run at           : {os.environ.get('PROBE_TIMESTAMP', 'unset')}")
    print()
    print("## config sources this module reads")
    print(f"  ROOT                  = {module.ROOT}")
    print(f"  STATUS_ROOT           = {module.STATUS_ROOT}")
    print(f"  CONFIG_FILE           = {module.CONFIG_FILE} exists: {module.CONFIG_FILE.exists()}")
    print(
        f"  repo-local overlay    = {module.CONFIG_LOCAL_FILE} "
        f"exists: {module.CONFIG_LOCAL_FILE.exists()}"
    )
    print(
        f"  status-root overlay   = {module.STATUS_ROOT_CONFIG_LOCAL_FILE} "
        f"exists: {module.STATUS_ROOT_CONFIG_LOCAL_FILE.exists()}"
    )
    print()

    configured = module.configured_agent_names()
    registered = module.registered_agent_names()
    print("## authority resolved through the merged config (config.json + config.local.json)")
    print(f"  configured_agent_names() [{len(configured)}] = {sorted(configured)}")
    print(f"  registered_agent_names() [{len(registered)}] = {sorted(registered)}")
    print(f"  NON_WORKER_ACTORS = {sorted(module.NON_WORKER_ACTORS)}")
    print()

    failures: list[str] = []

    print("## declared actors resolve exactly (spelling must round-trip unchanged)")
    for name in MUST_RESOLVE:
        try:
            resolved = module.resolve_actor_reference(name, field="probe")
        except SystemExit as exc:  # pragma: no cover - only on a real regression
            resolved = f"REJECTED ({exc})"
        ok = resolved == name
        print(f"  accept {name:18s} -> {resolved:18s} exact={ok}")
        if not ok:
            failures.append(f"{name} did not resolve to itself")

    print()
    print("## static KNOWN_AGENTS lanes with no merged-config declaration stay rejected")
    for name in MUST_REJECT:
        in_known = name in module.KNOWN_AGENTS
        try:
            resolved = module.resolve_actor_reference(name, field="probe")
            print(f"  reject {name:18s} -> ACCEPTED as {resolved} (REGRESSION)")
            failures.append(f"{name} was accepted as an actor reference")
        except SystemExit as exc:
            first = str(exc).splitlines()[0] if str(exc) else "SystemExit"
            print(f"  reject {name:18s} -> {first}")
        print(f"         (present in static KNOWN_AGENTS: {in_known})")

    status = json.loads((LIVE_ROOT / "ai-status.json").read_text())
    live_refs = actor_references(status)
    print()
    print("## audited Codex5/6/8/9 assignments in live state")
    tasks_with = sorted({ref[0] for ref in live_refs if not ref[0].startswith(("blocker[", "handoff["))})
    print(f"  distinct tasks carrying one : {len(tasks_with)}")
    for task_id in tasks_with:
        print(f"    - {task_id}")
    print(f"  total references            : {len(live_refs)}")
    for ref in live_refs:
        resolved = module.resolve_actor_reference(ref[2], field="probe")
        exact = resolved == ref[2]
        print(f"    {ref[0]:52s} {ref[1]:12s} {ref[2]:8s} resolves={resolved:8s} exact={exact}")
        if not exact:
            failures.append(f"{ref[0]}.{ref[1]} would be rewritten")

    if baseline_path is not None:
        baseline = json.loads(baseline_path.read_text())
        baseline_refs = actor_references(baseline)
        print()
        print("## byte-for-byte comparison against the audited baseline")
        print(f"  baseline: {baseline_path}")
        print(f"  baseline references: {len(baseline_refs)}   live references: {len(live_refs)}")
        missing = [ref for ref in baseline_refs if ref not in live_refs]
        added = [ref for ref in live_refs if ref not in baseline_refs]
        identical = sorted(baseline_refs) == sorted(live_refs)
        print(f"  identical multiset : {identical}")
        print(f"  missing from live  : {missing}")
        print(f"  added since audit  : {added}")
        if not identical:
            failures.append("audited Codex5/6/8/9 reference set changed")

    print()
    print("## live fingerprints — this probe never writes")
    after = {name: digest(LIVE_ROOT / name) for name in WATCHED}
    for name in WATCHED:
        same = before[name] == after[name]
        print(f"  {name:24s} before={before[name]} after={after[name]} identical={same}")

    print()
    if failures:
        print("RESULT: FAIL")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
