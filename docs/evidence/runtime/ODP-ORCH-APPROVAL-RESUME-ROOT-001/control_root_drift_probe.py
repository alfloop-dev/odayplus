#!/usr/bin/env python3
"""Does this broker revision still let a worker write inside its own worktree?

The hook binary lives in the control root; the worker runs in a per-task
worktree outside it. ``_allowed_workspace_roots`` only reaches that worktree via
``ORCH_WORKSPACE_PATH``, which the Supervisor injects into the worker process
and the hook subprocess inherits from the CLI. A revision that drops that lookup
classifies every ``Edit``/``MultiEdit``/``Write`` a worker makes as
**deny / out_of_workspace** — a fleet stall, not a degraded mode.

Round 1 of ODP-ORCH-APPROVAL-RESUME-ROOT-001 was about to publish exactly such a
revision over the live control root, because the hand-edited hunk that carries
the lookup has never existed in git history. This is the gate that catches it,
and §7 of the acceptance packet requires it again after the real publish.

Read-only: each revision is loaded from the directory named on the command line
and ``ROOT`` is pinned to the production control root, so the module's own code
is the only variable. Nothing is opened for write. Point ``--revision`` at
*copies*; never at a live root you would mind importing.

Usage::

    control_root_drift_probe.py --revision "deployed=/tmp/sandbox/oday-plus" \
                                --revision "candidate=/path/to/worktree"

Each value is a directory containing ``.orchestrator/``. Exit code 0 means every
revision allows the worker-worktree write; 1 means at least one would deny it.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

CONTROL_ROOT = Path("/home/lupin/oday-plus")
SIBLING_MODULES = (
    "common",
    "permission_broker",
    "approval_queue",
    "runtime_state",
    "provider_permissions",
    "claude_permission_prompt_mcp",
)


def load_broker(revision_dir: Path):
    """Import permission_broker from one revision, isolated from the previous one."""
    package_dir = revision_dir / ".orchestrator"
    for name in SIBLING_MODULES:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(package_dir))
    try:
        spec = importlib.util.spec_from_file_location(
            "permission_broker", package_dir / "permission_broker.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["permission_broker"] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(package_dir))
    module.ROOT = CONTROL_ROOT  # pin to the production layout
    return module


def probe(module, config: dict, target: str, workspace: Path) -> dict:
    """Ask one revision for a verdict with the Supervisor's env in place."""
    original = os.environ.copy()
    os.environ["ORCH_WORKSPACE_PATH"] = str(workspace)
    try:
        return {
            "roots": [str(root) for root in module._allowed_workspace_roots(config)],
            "within": module._paths_within_workspace([Path(target)], config),
            "verdict": module.evaluate_tool_request("Write", {"file_path": target}, config),
        }
    finally:
        os.environ.clear()
        os.environ.update(original)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--revision",
        action="append",
        required=True,
        metavar="LABEL=DIR",
        help="a directory containing .orchestrator/ to load and probe",
    )
    parser.add_argument(
        "--workspace",
        default=os.environ.get("ORCH_WORKSPACE_PATH"),
        help="the worker worktree the Supervisor names (default: $ORCH_WORKSPACE_PATH)",
    )
    args = parser.parse_args()

    if not args.workspace:
        print("FAIL: no worker worktree to probe (pass --workspace or set ORCH_WORKSPACE_PATH)")
        return 1

    workspace = Path(args.workspace)
    config = json.loads((CONTROL_ROOT / ".orchestrator/config.json").read_text(encoding="utf-8"))
    configured = (config.get("permission_broker") or {}).get("allowed_workspace_roots")
    target = str(workspace / "docs/evidence/runtime/probe.md")

    print("# Worker-worktree workspace probe — ODP-ORCH-APPROVAL-RESUME-ROOT-001")
    print(f"  live worker worktree (ORCH_WORKSPACE_PATH)       : {workspace}")
    print(f"  config permission_broker.allowed_workspace_roots : {configured}")
    print(f"  ROOT pinned to                                   : {CONTROL_ROOT}")
    print(f"  probe tool call                                  : Write -> {target}\n")

    denied = []
    for spec in args.revision:
        label, _, location = spec.partition("=")
        module = load_broker(Path(location))
        source = Path(module.__file__).read_text(encoding="utf-8")
        result = probe(module, config, target, workspace)
        verdict = result["verdict"]

        print(f"## {label}")
        print(f"   source                    : {module.__file__}")
        print(f"   reads ORCH_WORKSPACE_PATH : {'ORCH_WORKSPACE_PATH' in source}")
        print(f"   allowed workspace roots   : {result['roots']}")
        print(f"   path within workspace     : {result['within']}")
        print(f"   Write decision            : {verdict['decision'].upper()}  ({verdict['risk_class']})")
        print(f"   reason                    : {verdict['reason']}\n")
        if verdict["decision"] != "allow":
            denied.append(label)

    if denied:
        print(f"RESULT: DENY — {', '.join(denied)} would stall every worker-worktree write")
        return 1
    print("RESULT: ALLOW — every probed revision keeps worker-worktree writes allowed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
