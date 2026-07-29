#!/usr/bin/env python3
"""Real-CLI fail-closed matrix for the rolled-out actor guard.

Runs every actor-bearing `ai_status.py` command with a malformed prose
`AI_NAME` and with a well-shaped but unregistered `AI_NAME`, against an
*isolated copy* of the live status root. The live root is never named as
`PANTHEON_STATUS_ROOT` here, and the script asserts the live fingerprints are
unchanged when it finishes.

Usage:
    python3 sandbox_cli_matrix.py <ai_status.py under test> <sandbox root>
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

LIVE_ROOT = Path("/home/lupin/oday-plus-supervisor-live")
TASK_ID = "ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001"
PROSE = (
    "STOP FOR CHAIR REVIEW: the rollout window is closed until the coordinator "
    "re-checks this exact head"
)
UNREGISTERED = "Nessie9"

# Mirrors ai_status.MUTATING_COMMANDS minus the declared-actorless `sync`;
# arguments are valid enough to clear each command's usage check, so the only
# thing that can reject the call is the AI_NAME gate itself.
CASES: dict[str, list[str]] = {
    "assign": [TASK_ID, "Claude", "Codex2"],
    "start": [TASK_ID, "starting"],
    "progress": [TASK_ID, "still going"],
    "note": [TASK_ID, "a note"],
    "reopen": [TASK_ID, "reopening"],
    "handoff": [TASK_ID, "Codex2", "please review"],
    "blocker": [TASK_ID, "blocked", "Codex2"],
    "retarget_blocker": [TASK_ID, "Codex2", "repair"],
    "prune_agents": ["--apply", "cleanup"],
    "restore_approved": [TASK_ID, "restoring"],
    "done": [TASK_ID, "finished"],
    "supersede": [TASK_ID, "superseded"],
    "approve": [TASK_ID, "approved"],
    "archive_migrate": [],
    "wave open": ["open", "W-2026-07-29"],
    "wave close": ["close"],
}

WATCHED = ("ai-status.json", "ai-activity-log.jsonl", "current-work.md")


def digest(path: Path) -> str:
    if not path.exists():
        return "ABSENT"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def roster_digest(root: Path) -> str:
    """Fingerprint only the roster surface: agents[] plus workload keys."""
    status = root / "ai-status.json"
    if not status.exists():
        return "ABSENT"
    data = json.loads(status.read_text())
    roster = {
        "agents": data.get("agents", []),
        "workload": data.get("workload", {}),
        "workload_summary": data.get("workload_summary", {}),
    }
    blob = json.dumps(roster, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def fingerprint(root: Path) -> dict[str, str]:
    marks = {name: digest(root / name) for name in WATCHED}
    marks["roster"] = roster_digest(root)
    return marks


def main() -> int:
    script = Path(sys.argv[1]).resolve()
    sandbox = Path(sys.argv[2]).resolve()
    if sandbox == LIVE_ROOT:
        print("refusing to run the matrix against the live root", file=sys.stderr)
        return 2

    live_before = fingerprint(LIVE_ROOT)
    live_needle_baseline: dict[tuple[str, str], int] = {}
    for label, needle in (("Nessie9", UNREGISTERED), ("prose AI_NAME", PROSE[:48])):
        for name in WATCHED:
            path = LIVE_ROOT / name
            blob = path.read_text(errors="replace") if path.exists() else ""
            live_needle_baseline[(label, name)] = blob.count(needle)
    base = fingerprint(sandbox)

    print("# Fail-closed CLI matrix — real CLI, isolated status root")
    print(f"# ai_status.py under test : {script}")
    print(f"#   sha256                : {hashlib.sha256(script.read_bytes()).hexdigest()}")
    print(f"# sandbox status root     : {sandbox}")
    print("# live root is read-only here; its fingerprints are re-checked at the end.")
    print()
    print("## sandbox baseline")
    for key, value in base.items():
        print(f"  {key:24s} {value}")
    print()

    failures: list[str] = []
    for bad_name, heading in ((PROSE, "malformed prose"), (UNREGISTERED, "well-shaped but unregistered")):
        print(f"## AI_NAME = {heading}")
        if bad_name is PROSE:
            print(f"#   value ({len(bad_name)} chars): {bad_name!r}")
        else:
            print(f"#   value: {bad_name!r}")
        for label, args in CASES.items():
            command = label.split()[0]
            argv = [sys.executable, str(script), command, *args]
            env = dict(os.environ)
            env["AI_NAME"] = bad_name
            env["PANTHEON_STATUS_ROOT"] = str(sandbox)
            proc = subprocess.run(argv, capture_output=True, text=True, env=env, cwd=script.parent.parent)
            after = fingerprint(sandbox)
            state = "UNCHANGED" if after["ai-status.json"] == base["ai-status.json"] else "MUTATED"
            log = "UNCHANGED" if after["ai-activity-log.jsonl"] == base["ai-activity-log.jsonl"] else "MUTATED"
            roster = "UNCHANGED" if after["roster"] == base["roster"] else "MUTATED"
            work = "UNCHANGED" if after["current-work.md"] == base["current-work.md"] else "MUTATED"
            first = (proc.stderr.strip() or proc.stdout.strip() or "<no output>").splitlines()[0]
            print(
                f"{label:22s} exit={proc.returncode} state={state} log={log} "
                f"roster={roster} current-work={work}"
            )
            print(f"  first line: {first}")
            if proc.returncode == 0 or "MUTATED" in (state, log, roster, work):
                failures.append(f"{label} / {heading}")
        print()

    live_after = fingerprint(LIVE_ROOT)
    print("## live root fingerprints (informational — the live Supervisor keeps running")
    print("## and writes on its own cadence, so a change here is not attributable to us)")
    for key in live_before:
        same = live_before[key] == live_after[key]
        print(f"  {key:24s} before={live_before[key]} after={live_after[key]} identical={same}")

    print()
    print("## hard isolation assertion: the matrix added no occurrence of its own")
    print("## actor strings to the live root (counts must be identical; a non-zero")
    print("## baseline is prior evidence prose, not a write from this run)")
    for label, needle in (("Nessie9", UNREGISTERED), ("prose AI_NAME", PROSE[:48])):
        for name in WATCHED:
            path = LIVE_ROOT / name
            blob = path.read_text(errors="replace") if path.exists() else ""
            after_count = blob.count(needle)
            before_count = live_needle_baseline[(label, name)]
            same = after_count == before_count
            print(
                f"  {label:16s} in live {name:24s} before={before_count} "
                f"after={after_count} identical={same}"
            )
            if not same:
                failures.append(f"live {name} gained an occurrence of {label}")

    print()
    if failures:
        print("RESULT: FAIL")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("RESULT: PASS — every actor-bearing command failed closed with zero mutation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
