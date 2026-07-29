#!/usr/bin/env python3
"""Deterministic negative self-test for `deploy.py`'s supervisor gate.

Round 2's driver printed `ActiveState`, `SubState` and `NRestarts` but only
failed the run on `MainPID` / `ExecMainStartTimestamp` changes. The reviewer
made systemd report `inactive/dead` with `NRestarts` 0->1 and the driver still
answered `RESULT: PASS`, rc 0. Receipts alone cannot show that hole is closed —
only an executable test that *makes* the failure happen can.

This test drives the real `deploy.py` end to end against a throwaway git root,
with a fake `systemctl` first on `PATH`. The fake answers from a scripted
scenario: invocation 1 is the driver's BEFORE snapshot, invocation 2 its AFTER
snapshot, so any BEFORE/AFTER pair can be forced deterministically — no timing,
no sleeping, and the live Supervisor is never queried, signalled or touched.

Each scenario asserts three things:

  * the driver's exit code (2 = preflight refused, 1 = failed after work began,
    0 = clean),
  * whether the sandbox target was published or left byte-for-byte untouched,
  * that `RESULT: PASS` is absent from every negative run — the exact string the
    rejected round emitted while the unit was dead.

The positive control is what keeps the rest honest: with a steady unit the same
driver must still publish and exit 0, so the negatives are the gate firing, not
a driver that fails unconditionally.

Usage:
    python3 supervisor_gate_selftest.py --merge <commit>
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
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEPLOY = HERE / "deploy.py"
STUB_NAME = "pantheon-supervisor-selftest-stub.py"

# A fake `systemctl` that ignores its arguments and replays the scenario: the
# Nth invocation answers with phase N (the last phase repeats if the driver asks
# again). `rc` non-zero simulates a failed query, which must be fatal.
SHIM = '''#!/usr/bin/env python3
import json
import pathlib
import sys

here = pathlib.Path(__file__).resolve().parent
scenario = json.loads((here / "scenario.json").read_text())
counter = here / "calls.txt"
seen = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(seen + 1))

phases = scenario["phases"]
phase = phases[min(seen, len(phases) - 1)]
if phase.get("rc"):
    sys.stderr.write(phase.get("stderr", "Failed to connect to bus: No such file\\n"))
    raise SystemExit(phase["rc"])
for key, value in phase["props"].items():
    print(f"{key}={value}")
'''

STUB = '''#!/usr/bin/env python3
# Stands in for the Supervisor process in the positive control: its argv carries
# "pantheon-supervisor", so deploy.py's /proc identity check has something real
# and deterministic to resolve. It does nothing else.
import time

time.sleep(600)
'''

# A live process that is emphatically *not* the Supervisor. It lives under its
# own neutrally-named directory so nothing in its argv can match the identity
# marker by accident.
DECOY_NAME = "unrelated-worker-stub.py"
DECOY = '''#!/usr/bin/env python3
import time

time.sleep(600)
'''


def healthy(pid: int, *, restarts: int = 0, stamp: str = "Wed 2026-07-29 06:08:57 UTC") -> dict:
    return {
        "props": {
            "Id": "pantheon-supervisor.service",
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "running",
            "MainPID": str(pid),
            "ExecMainStartTimestamp": stamp,
            "NRestarts": str(restarts),
        }
    }


def mutate(phase: dict, **props: str) -> dict:
    merged = dict(phase["props"])
    merged.update(props)
    return {"props": merged}


def dead_pid() -> int:
    """A PID with no /proc entry — used to prove the identity check bites."""
    candidate = 4_000_000
    while Path(f"/proc/{candidate}").exists():
        candidate += 1
    return candidate


def make_sandbox_root(tmp: Path) -> tuple[Path, str]:
    """A throwaway git root with a placeholder target, so a publish is visible."""
    root = tmp / "sandbox-root"
    (root / "scripts").mkdir(parents=True)
    target = root / "scripts" / "ai_status.py"
    target.write_text("# placeholder — must survive every preflight refusal\n")
    target.chmod(0o755)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "selftest",
        "GIT_AUTHOR_EMAIL": "selftest@example.com",
        "GIT_COMMITTER_NAME": "selftest",
        "GIT_COMMITTER_EMAIL": "selftest@example.com",
    }
    subprocess.run(["git", "init", "-q", "-b", "selftest"], cwd=root, check=True, env=env)
    subprocess.run(["git", "add", "scripts/ai_status.py"], cwd=root, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "selftest baseline"], cwd=root, check=True, env=env
    )
    return root, hashlib.sha256(target.read_bytes()).hexdigest()


def run_scenario(
    scenario: dict, merge: str, merged_sha: str, tmp: Path, index: int
) -> tuple[bool, str]:
    case = tmp / f"case-{index}"
    bindir = case / "bin"
    bindir.mkdir(parents=True)
    (bindir / "scenario.json").write_text(json.dumps({"phases": scenario["phases"]}, indent=2))
    shim = bindir / "systemctl"
    shim.write_text(SHIM)
    shim.chmod(0o755)

    root, placeholder_sha = make_sandbox_root(case)
    target = root / "scripts" / "ai_status.py"

    argv = [
        sys.executable,
        str(DEPLOY),
        "--merge",
        merge,
        "--backup-dir",
        str(case / "backups"),
        "--root",
        str(root),
    ]
    argv += scenario.get("extra_args", [])
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"},
    )

    after_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    published = after_sha == merged_sha
    untouched = after_sha == placeholder_sha

    problems: list[str] = []
    if proc.returncode != scenario["expect_rc"]:
        problems.append(f"exit code {proc.returncode}, expected {scenario['expect_rc']}")
    if scenario["expect_target"] == "untouched" and not untouched:
        problems.append("sandbox target was modified but had to stay byte-for-byte untouched")
    if scenario["expect_target"] == "published" and not published:
        problems.append("sandbox target was not published though the run was expected to reach it")
    if scenario["expect_rc"] != 0 and "RESULT: PASS" in proc.stdout:
        problems.append("driver still printed 'RESULT: PASS' on a failing run")
    if scenario["expect_rc"] == 0 and "RESULT: PASS" not in proc.stdout:
        problems.append("positive control did not pass — the gate is failing unconditionally")
    for needle in scenario.get("expect_stdout", []):
        if needle not in proc.stdout:
            problems.append(f"stdout is missing the expected reason {needle!r}")

    print(f"### {index}. {scenario['name']}")
    print(f"  what systemd is made to report : {scenario['story']}")
    print(f"  expected                       : rc {scenario['expect_rc']}, target {scenario['expect_target']}")
    print(f"  observed                       : rc {proc.returncode}, target {'published' if published else 'untouched' if untouched else 'CORRUPT'}")
    verdict = "PASS" if not problems else "FAIL"
    for line in scenario.get("show_stdout", []):
        for out in proc.stdout.splitlines():
            if line in out:
                print(f"  driver said                    : {out.strip()}")
    for problem in problems:
        print(f"  PROBLEM                        : {problem}")
    print(f"  {verdict}")
    print()
    return not problems, proc.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--merge", required=True)
    args = parser.parse_args()

    print("# Supervisor-gate self-test — deploy.py under a scripted fake systemctl")
    print(f"# run at   : {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    print(f"# driver   : {DEPLOY}")
    print(f"# merge    : {args.merge}")
    print("# The live Supervisor is never queried, signalled or touched: a fake")
    print("# `systemctl` is first on PATH and every target is a throwaway git root.")
    print()

    payload = subprocess.run(
        ["git", "cat-file", "blob", f"{args.merge}:scripts/ai_status.py"],
        cwd=HERE,
        capture_output=True,
        check=True,
    ).stdout
    merged_sha = hashlib.sha256(payload).hexdigest()
    print(f"# merged payload sha256 : {merged_sha} ({len(payload)} bytes)")
    print()

    tmp = Path(tempfile.mkdtemp(prefix="supervisor-gate-selftest-"))
    stub_path = tmp / STUB_NAME
    stub_path.write_text(STUB)
    stub = subprocess.Popen([sys.executable, str(stub_path)])
    decoy_dir = Path(tempfile.mkdtemp(prefix="odp-unrelated-"))
    decoy_path = decoy_dir / DECOY_NAME
    decoy_path.write_text(DECOY)
    decoy = subprocess.Popen([sys.executable, str(decoy_path)])
    try:
        live = healthy(stub.pid)
        gone = dead_pid()
        query_failure = {"rc": 1, "stderr": "Failed to connect to bus: No such file or directory\n"}

        scenarios = [
            {
                "name": "positive control — steady unit",
                "story": "active/running, same MainPID, timestamp and NRestarts on both sides",
                "phases": [live, live],
                "expect_rc": 0,
                "expect_target": "published",
                "show_stdout": ["RESULT:"],
            },
            {
                "name": "preflight — unit is inactive/dead",
                "story": "BEFORE reports inactive/dead (the reviewer's probe)",
                "phases": [mutate(live, ActiveState="inactive", SubState="dead")],
                "expect_rc": 2,
                "expect_target": "untouched",
                "expect_stdout": ["ActiveState is 'inactive'", "no target was touched"],
                "show_stdout": ["ActiveState is", "RESULT:"],
            },
            {
                "name": "preflight — MainPID names a dead process",
                "story": f"BEFORE claims active/running but MainPID {gone} has no /proc entry",
                "phases": [mutate(live, MainPID=str(gone))],
                "expect_rc": 2,
                "expect_target": "untouched",
                "expect_stdout": ["has no /proc entry"],
                "show_stdout": ["/proc entry", "RESULT:"],
            },
            {
                "name": "preflight — MainPID is not the Supervisor",
                "story": f"BEFORE points at live PID {decoy.pid}, a real process that is some other program",
                "phases": [mutate(live, MainPID=str(decoy.pid))],
                "expect_rc": 2,
                "expect_target": "untouched",
                "expect_stdout": ["is not the Supervisor"],
                "show_stdout": ["is not the Supervisor", "RESULT:"],
            },
            {
                "name": "preflight — unit not loaded",
                "story": "BEFORE reports LoadState=not-found (unit renamed or removed)",
                "phases": [mutate(live, LoadState="not-found")],
                "expect_rc": 2,
                "expect_target": "untouched",
                "expect_stdout": ["LoadState is 'not-found'"],
                "show_stdout": ["LoadState is", "RESULT:"],
            },
            {
                "name": "preflight — systemctl query fails",
                "story": "BEFORE query exits 1 (no bus); an unreadable state must never read as healthy",
                "phases": [query_failure],
                "expect_rc": 2,
                "expect_target": "untouched",
                "expect_stdout": ["PROBE FAILED", "no target was touched"],
                "show_stdout": ["PROBE FAILED", "RESULT:"],
            },
            {
                "name": "continuity — NRestarts drifts 0 -> 1",
                "story": "AFTER is active/running with the same PID, but the restart counter moved",
                "phases": [live, mutate(live, NRestarts="1")],
                "expect_rc": 1,
                "expect_target": "published",
                "expect_stdout": ["NRestarts changed across the deploy: '0' -> '1'"],
                "show_stdout": ["NRestarts changed", "RESULT:"],
            },
            {
                "name": "continuity — unit died during the deploy",
                "story": "AFTER reports inactive/dead with NRestarts 0 -> 1 (the exact rejected case)",
                "phases": [live, mutate(live, ActiveState="inactive", SubState="dead", NRestarts="1")],
                "expect_rc": 1,
                "expect_target": "published",
                "expect_stdout": ["expected active/running", "NRestarts changed"],
                "show_stdout": ["inactive/dead", "NRestarts changed", "RESULT:"],
            },
            {
                "name": "continuity — restarted with a new PID and start time",
                "story": "AFTER shows a fresh MainPID, a later ExecMainStartTimestamp and NRestarts 1",
                "phases": [
                    live,
                    mutate(
                        live,
                        MainPID=str(stub.pid + 1),
                        ExecMainStartTimestamp="Wed 2026-07-29 09:00:00 UTC",
                        NRestarts="1",
                    ),
                ],
                "expect_rc": 1,
                "expect_target": "published",
                "expect_stdout": ["MainPID changed", "ExecMainStartTimestamp changed"],
                "show_stdout": ["MainPID changed", "RESULT:"],
            },
            {
                "name": "continuity — systemctl query fails afterwards",
                "story": "BEFORE is healthy, the AFTER query exits 1; continuity is unprovable, so the run fails",
                "phases": [live, query_failure],
                "expect_rc": 1,
                "expect_target": "published",
                "expect_stdout": ["unreadable after the deploy"],
                "show_stdout": ["PROBE FAILED", "RESULT:"],
            },
            {
                "name": "rehearsal mode cannot launder a dead unit",
                "story": "--corrupt-payload with AFTER inactive/dead: the payload gate firing must not mask it",
                "phases": [live, mutate(live, ActiveState="inactive", SubState="dead", NRestarts="1")],
                "extra_args": ["--corrupt-payload"],
                "expect_rc": 1,
                "expect_target": "untouched",
                "expect_stdout": ["expected active/running"],
                "show_stdout": ["RESULT:"],
            },
        ]

        results = []
        for index, scenario in enumerate(scenarios, start=1):
            ok, _ = run_scenario(scenario, args.merge, merged_sha, tmp, index)
            results.append((scenario["name"], ok))
    finally:
        for process in (stub, decoy):
            process.terminate()
            process.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(decoy_dir, ignore_errors=True)

    passed = sum(1 for _, ok in results if ok)
    print(f"## summary: {passed}/{len(results)} scenarios behaved as required")
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print()
    if passed == len(results):
        print("RESULT: PASS — the gate refuses to start on an unhealthy unit and fails")
        print("        the run on any restart drift, while a steady unit still deploys.")
        return 0
    print("RESULT: FAIL — the supervisor gate does not behave as required")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
