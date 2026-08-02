#!/usr/bin/env python3
"""Atomically deploy reviewed worktree base-advance policy to live Supervisor.

Rolls out the reviewed `.orchestrator/supervisor.py` from commit `475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2f`
(PR #569: ODP-ORCH-WORKTREE-BASE-ADVANCE-001) across live target roots:
  - /home/lupin/oday-plus
  - /home/lupin/oday-plus-supervisor-live
  - /home/lupin/oday-plus-supervisor-runtime-945a8366

Guarantees:
  1. Stage-all-then-publish with same-directory sibling + fsync + chmod + verification.
  2. Preflight supervisor probe and continuity verification.
  3. Post-publish import smoke test per root.
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[4]
TASK_ID = "ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001"
TARGET_FILES = (
    ".orchestrator/supervisor.py",
)

SUPERVISOR_UNIT = "pantheon-supervisor.service"
SUPERVISOR_PROPS = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "MainPID",
    "ExecMainStartTimestamp",
    "NRestarts",
)
CONTINUITY_FIELDS = ("MainPID", "ExecMainStartTimestamp", "NRestarts")
IDENTITY_MARKER = "supervisor"


def sh(*argv: str, cwd: Path | None = None) -> str:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def without_deployed(lines: list[str]) -> list[str]:
    deployed = {Path(p).as_posix() for p in TARGET_FILES}
    result = []
    for line in lines:
        if any(d in line for d in deployed):
            continue
        if ".lock" in line or "worktree-dirt-backups" in line or "runtime/" in line:
            continue
        result.append(line)
    return result


class SupervisorProbeError(RuntimeError):
    """The unit's state could not be read."""


def probe_supervisor() -> dict[str, str]:
    argv = ["systemctl", "--user", "show", SUPERVISOR_UNIT]
    argv += [f"--property={prop}" for prop in SUPERVISOR_PROPS]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:
        raise SupervisorProbeError(f"could not execute systemctl: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise SupervisorProbeError(
            f"systemctl exited {proc.returncode}: {detail[0] if detail else '<no output>'}"
        )
    state: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            state[key.strip()] = value.strip()
    missing = [p for p in SUPERVISOR_PROPS if not state.get(p)]
    if missing:
        raise SupervisorProbeError(f"systemctl did not report {', '.join(missing)}")
    return state


def identity_failures(state: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    pid = state["MainPID"]
    if not pid.isdigit() or int(pid) <= 0:
        return [f"MainPID {pid!r} is not a live PID"], {}
    proc_dir = Path(f"/proc/{pid}")
    if not proc_dir.exists():
        return [f"MainPID {pid} has no /proc entry"], {}
    try:
        cmdline = proc_dir.joinpath("cmdline").read_bytes().decode(errors="replace")
        cmdline = cmdline.replace("\0", " ").strip()
        cwd = os.readlink(str(proc_dir / "cwd"))
    except OSError as exc:
        return [f"MainPID {pid} identity unreadable: {exc}"], {}
    detail = {"cmdline": cmdline, "cwd": cwd}
    if IDENTITY_MARKER not in cmdline.lower():
        return [f"MainPID {pid} cmdline is not the Supervisor: {cmdline!r}"], detail
    return [], detail


def preflight_failures(state: dict[str, str]) -> list[str]:
    failures: list[str] = []
    if state["Id"] != SUPERVISOR_UNIT:
        failures.append(f"unit is {state['Id']!r}, expected {SUPERVISOR_UNIT!r}")
    if state["LoadState"] != "loaded":
        failures.append(f"LoadState is {state['LoadState']!r}, expected 'loaded'")
    if state["ActiveState"] != "active":
        failures.append(f"ActiveState is {state['ActiveState']!r}, expected 'active'")
    if state["SubState"] != "running":
        failures.append(f"SubState is {state['SubState']!r}, expected 'running'")
    if not state["NRestarts"].isdigit():
        failures.append(f"NRestarts is {state['NRestarts']!r}, not a readable count")
    stamp = state["ExecMainStartTimestamp"]
    if not stamp or stamp in {"n/a", "<unavailable>"}:
        failures.append(f"ExecMainStartTimestamp is {stamp!r} — no start time to pin")
    failures.extend(identity_failures(state)[0])
    return failures


def continuity_failures(before: dict[str, str], after: dict[str, str]) -> list[str]:
    failures: list[str] = []
    if after["ActiveState"] != "active" or after["SubState"] != "running":
        failures.append(
            f"supervisor is {after['ActiveState']}/{after['SubState']} after deploy"
        )
    if after["LoadState"] != "loaded":
        failures.append(f"LoadState became {after['LoadState']!r} after deploy")
    for field in CONTINUITY_FIELDS:
        if before[field] != after[field]:
            failures.append(f"{field} changed across deploy: {before[field]!r} -> {after[field]!r}")
    return failures


def supervisor_block(heading: str, state: dict[str, str]) -> None:
    print(f"## supervisor state {heading}")
    for key in SUPERVISOR_PROPS:
        print(f"  {key:22s}: {state[key]}")
    for key, value in identity_failures(state)[1].items():
        print(f"  {'process ' + key:22s}: {value}")
    print()


def receipts(target: Path, label: str) -> dict[str, object]:
    st = target.stat()
    data: dict[str, object] = {
        "sha256": sha256_of(target),
        "bytes": st.st_size,
        "mode": oct(st.st_mode & 0o7777)[2:],
        "inode": st.st_ino,
        "device": st.st_dev,
        "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
    }
    for key, value in data.items():
        print(f"    {label} {key:10s}: {value}")
    return data


def materialise(source_ref: str, rel_path: str) -> tuple[str, bytes, str]:
    blob = sh("git", "rev-parse", f"{source_ref}:{rel_path}", cwd=WORKTREE)
    payload = subprocess.run(
        ["git", "cat-file", "blob", blob], cwd=WORKTREE, capture_output=True, check=True
    ).stdout
    return blob, payload, hashlib.sha256(payload).hexdigest()


def stage(target: Path, payload: bytes, merged_sha: str, mode: int, corrupt: bool) -> tuple[Path, bool]:
    sibling = target.with_name(f".{target.name}.{TASK_ID}.{os.getpid()}.tmp")
    if sibling.exists():
        sibling.unlink()

    written = bytearray(payload)
    if corrupt:
        written[0] ^= 0xFF

    fd = os.open(sibling, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(bytes(written))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        sibling.unlink(missing_ok=True)
        raise
    os.chmod(sibling, mode)

    sib_stat = sibling.stat()
    sib_sha = sha256_of(sibling)
    blob_copy = Path(f"/tmp/{target.name}.verify-{os.getpid()}")
    blob_copy.write_bytes(payload)
    identical = filecmp.cmp(sibling, blob_copy, shallow=False)
    blob_copy.unlink(missing_ok=True)

    checks = [
        ("same directory as target", sibling.parent == target.parent),
        ("same filesystem (rename is atomic)", sib_stat.st_dev == target.stat().st_dev),
        ("sha256 == reviewed blob", sib_sha == merged_sha),
        ("byte length == reviewed blob", sib_stat.st_size == len(payload)),
        ("mode == target's existing mode", oct(sib_stat.st_mode & 0o7777)[2:] == oct(mode)[2:]),
        ("byte-for-byte compare vs blob", identical),
    ]
    print(f"    staged sibling      : {sibling.name}")
    print(f"    sibling sha256      : {sib_sha}")
    print(f"    sibling bytes/mode  : {sib_stat.st_size} / {oct(sib_stat.st_mode & 0o7777)[2:]}")
    ok = True
    for name, got in checks:
        print(f"      {'PASS' if got else 'FAIL'}  {name}")
        ok = ok and got
    if not ok:
        sibling.unlink(missing_ok=True)
        print("    VERIFICATION FAILED -> sibling unlinked, target NOT touched")
    return sibling, ok


def publish(sibling: Path, target: Path) -> None:
    dir_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.replace(sibling, target)
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    print(f"    published           : os.replace({sibling.name} -> {target.name}) [atomic rename]")


def import_smoke_test(root: Path) -> tuple[bool, str]:
    probe = (
        "import sys; sys.path.insert(0, '.');"
        "import supervisor;"
        "assert hasattr(supervisor, '_refresh_reused_worker_worktree');"
        "assert hasattr(supervisor, 'acquire_singleton_lock');"
        "print('import-ok', getattr(supervisor, '__file__', ''))"
    )
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, "-B", "-c", probe],
        cwd=str(root / ".orchestrator"),
        capture_output=True,
        text=True,
        env=env,
    )
    detail = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode == 0, detail[-1] if detail else "<no output>"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-ref", required=True, help="commit whose blobs are deployed")
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--corrupt-payload", action="store_true")
    parser.add_argument(
        "--skip-supervisor-gate",
        action="store_true",
        help="sandbox rehearsal only",
    )
    args = parser.parse_args()

    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    print(f"# Deployment transcript — {TASK_ID}")
    print(f"# host date  : {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    print(f"# driver     : {Path(__file__).name} (stage-all -> verify-all -> atomic publish)")
    print(f"# source ref : {args.source_ref}")
    print(f"# backups    : {backup_dir}")
    print(f"# order      : {' -> '.join(Path(p).name for p in TARGET_FILES)}")
    if args.corrupt_payload:
        print("# MODE       : NEGATIVE REHEARSAL — payload is corrupted after hashing;")
        print("#              staging gate must abort before any rename is issued.")
    print()

    print("## payloads materialised from the source commit (no working-tree copy)")
    payloads: dict[str, tuple[str, bytes, str]] = {}
    for rel_path in TARGET_FILES:
        blob, payload, digest = materialise(args.source_ref, rel_path)
        payloads[rel_path] = (blob, payload, digest)
        print(f"  {rel_path}")
        print(f"    blob   : {blob}")
        print(f"    sha256 : {digest}")
        print(f"    bytes  : {len(payload)}")
    print()

    before_state: dict[str, str] | None
    try:
        before_state = probe_supervisor()
    except SupervisorProbeError as exc:
        print("## supervisor state BEFORE")
        print(f"  PROBE FAILED: {exc}")
        print()
        if not args.skip_supervisor_gate:
            print("RESULT: FAIL — preflight gate: Supervisor state is unreadable")
            return 2
        before_state = None
        print("  (--skip-supervisor-gate: sandbox rehearsal continues)")
        print()

    if before_state is not None:
        supervisor_block("BEFORE", before_state)
        preflight = preflight_failures(before_state)
        print("## preflight gate (evaluated before any target is touched)")
        for label in preflight:
            print(f"    FAIL  {label}")
        if preflight and not args.skip_supervisor_gate:
            print()
            print("RESULT: FAIL — preflight gate refused to deploy")
            return 2
        if not preflight:
            print("    PASS  unit loaded and active/running")
            print("    PASS  MainPID is a live process whose cmdline is the Supervisor")
            print(f"    PASS  continuity pinned to {', '.join(f'{f}={before_state[f]}' for f in CONTINUITY_FIELDS)}")
        print()

    failures: list[str] = []
    roots = [Path(r) for r in args.root]

    print("## phase 1 — stage and verify every sibling (no rename issued)")
    staged: list[tuple[Path, Path, Path, dict[str, object], int]] = []
    all_staged_ok = True
    for root in roots:
        print(f"  root: {root}")
        print(f"    git branch          : {sh('git', 'rev-parse', '--abbrev-ref', 'HEAD', cwd=root)}")
        print(f"    git HEAD            : {sh('git', 'rev-parse', 'HEAD', cwd=root)}")
        dirty_before = sh("git", "status", "--porcelain", cwd=root)
        (backup_dir / f"{root.name}.dirty-before.txt").write_text(dirty_before + "\n")
        print(f"    dirty files         : {len(dirty_before.splitlines())}")
        for rel_path in TARGET_FILES:
            target = root / rel_path
            _blob, payload, digest = payloads[rel_path]
            print(f"  target: {target}")
            before = receipts(target, "BEFORE")
            mode = int(str(before["mode"]), 8)
            backup = backup_dir / f"{root.name}.{Path(rel_path).name}.bak"
            backup.write_bytes(target.read_bytes())
            os.chmod(backup, mode)
            print(f"    backup              : {backup}")
            print(f"    rollback            : install -m {before['mode']} {backup} {target}")
            corrupt = args.corrupt_payload and rel_path == TARGET_FILES[-1]
            sibling, ok = stage(target, payload, digest, mode, corrupt)
            all_staged_ok = all_staged_ok and ok
            if ok:
                staged.append((root, target, sibling, before, mode))
    print()

    if not all_staged_ok:
        print("## phase 2 — SKIPPED: staging failed, so no rename is issued anywhere")
        for _root, target, sibling, before, _mode in staged:
            sibling.unlink(missing_ok=True)
            after = receipts(target, "AFTER ")
            if after["sha256"] != before["sha256"] or after["inode"] != before["inode"]:
                failures.append(f"{target}: changed despite aborted run")
        print()
    else:
        print("## phase 2 — atomic publish per root")
        for root, target, sibling, before, _mode in staged:
            print(f"  target: {target}")
            publish(sibling, target)
            after = receipts(target, "AFTER ")
            rel = str(target.relative_to(root))
            _blob, payload, digest = payloads[rel]
            checks = [
                ("target sha256 == reviewed blob", after["sha256"] == digest),
                ("target bytes == reviewed blob", after["bytes"] == len(payload)),
                ("mode preserved", after["mode"] == before["mode"]),
                ("inode CHANGED (proves rename, not in-place write)", after["inode"] != before["inode"]),
                ("no temporary sibling left behind", not sibling.exists()),
            ]
            for label, got in checks:
                print(f"      {'PASS' if got else 'FAIL'}  {label}")
                if not got:
                    failures.append(f"{target}: {label}")
        print()

        print("## phase 3 — import smoke test per root")
        for root in roots:
            ok, detail = import_smoke_test(root)
            print(f"  {root}: {'PASS' if ok else 'FAIL'}  {detail}")
            if not ok:
                failures.append(f"{root}: deployed module set does not import ({detail})")
        print()

    print("## leftover staging siblings and dirty inventory")
    for root in roots:
        leftovers = sorted(
            p.name for p in (root / ".orchestrator").glob(f".*.{TASK_ID}.*.tmp")
        )
        print(f"  {root}: leftover siblings {leftovers or 'none'}")
        if leftovers:
            failures.append(f"{root}: leftover staging siblings {leftovers}")
        dirty_before = (backup_dir / f"{root.name}.dirty-before.txt").read_text().strip().splitlines()
        dirty_after = sh("git", "status", "--porcelain", cwd=root).splitlines()
        (backup_dir / f"{root.name}.dirty-after.txt").write_text("\n".join(dirty_after) + "\n")
        same_rest = without_deployed(dirty_before) == without_deployed(dirty_after)
        print(f"    every other dirty file unchanged : {same_rest}")
        if not same_rest:
            only_before = set(without_deployed(dirty_before)) - set(without_deployed(dirty_after))
            only_after = set(without_deployed(dirty_after)) - set(without_deployed(dirty_after))
            print(f"      only before: {sorted(only_before)}")
            print(f"      only after : {sorted(only_after)}")
            failures.append(f"{root}: unrelated dirty inventory changed")
    print()

    if before_state is not None:
        try:
            after_state: dict[str, str] | None = probe_supervisor()
        except SupervisorProbeError as exc:
            after_state = None
            print("## supervisor state AFTER")
            print(f"  PROBE FAILED: {exc}")
            print()
            failures.append(f"supervisor state unreadable after deploy: {exc}")
        if after_state is not None:
            supervisor_block("AFTER", after_state)
            continuity = continuity_failures(before_state, after_state)
            print("## continuity gate")
            for label in continuity:
                print(f"    FAIL  {label}")
            failures.extend(continuity)
            if not continuity:
                print("    PASS  still active/running")
                for field in CONTINUITY_FIELDS:
                    print(f"    PASS  {field} identical: {before_state[field]}")
    print()

    if args.corrupt_payload:
        gate_fired = (not all_staged_ok) and not failures
        print(
            "RESULT: PASS — staging gate aborted corrupted payload and left targets untouched"
            if gate_fired
            else "RESULT: FAIL — corrupted payload was not stopped cleanly"
        )
        return 0 if gate_fired else 1

    if not all_staged_ok:
        failures.append("a target aborted verification during deploy")
    if failures:
        print("RESULT: FAIL")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("RESULT: PASS — reviewed blob published atomically to all targets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
