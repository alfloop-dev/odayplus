"""READ-ONLY: would publishing the reviewed blob to the control root keep
worker-worktree writes allowed?

Each revision is loaded from an isolated sandbox copy of the control root's
.orchestrator/ (real files, no symlinks -- nothing under /home/lupin is opened
for write). ROOT is pinned to the production control root so the module's own
code is the only variable.
"""
import importlib, importlib.util, json, os, sys
from pathlib import Path

CONTROL_ROOT = Path("/home/lupin/oday-plus")
WORKTREE = Path(os.environ["ORCH_WORKSPACE_PATH"])
CONFIG = json.loads((CONTROL_ROOT / ".orchestrator/config.json").read_text())
TARGET = str(WORKTREE / "docs/evidence/runtime/ODP-ORCH-APPROVAL-RESUME-ROOT-001/x.md")

print("# Blocker probe — ODP-ORCH-APPROVAL-RESUME-ROOT-001 criterion 6")
print(f"  live worker worktree (ORCH_WORKSPACE_PATH): {WORKTREE}")
print(f"  config permission_broker.allowed_workspace_roots: "
      f"{CONFIG.get('permission_broker', {}).get('allowed_workspace_roots')}")
print(f"  probe tool call: Write -> {TARGET}\n")

def load(sandbox):
    for name in ("common", "permission_broker", "approval_queue",
                 "runtime_state", "claude_permission_prompt_mcp"):
        sys.modules.pop(name, None)
    d = f"/tmp/odp-arr-probe/{sandbox}/.orchestrator"
    sys.path.insert(0, d)
    try:
        spec = importlib.util.spec_from_file_location("permission_broker", d + "/permission_broker.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["permission_broker"] = mod
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(d)
    mod.ROOT = CONTROL_ROOT
    return mod

for label, sandbox in (("A. currently deployed control-root hook (c4ecfe5a)", "deployed"),
                       ("B. reviewed blob deploy.py would publish (bb5c74a6)", "reviewed")):
    mod = load(sandbox)
    roots = [str(r) for r in mod._allowed_workspace_roots(CONFIG)]
    verdict = mod.evaluate_tool_request("Write", {"file_path": TARGET}, CONFIG)
    print(f"## {label}")
    print(f"   reads ORCH_WORKSPACE_PATH : {'ORCH_WORKSPACE_PATH' in Path(mod.__file__).read_text()}")
    print(f"   allowed workspace roots   : {roots}")
    print(f"   path within workspace     : {mod._paths_within_workspace([Path(TARGET)], CONFIG)}")
    print(f"   Write decision            : {verdict['decision'].upper()}  ({verdict['risk_class']})")
    print(f"   reason                    : {verdict['reason']}\n")
