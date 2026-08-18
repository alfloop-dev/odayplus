#!/usr/bin/env python3
"""Atomically deploy the exact PR #505 merged `supervisor.py` and `ai_status.py`.

Merge commit: 6af7b86ba4aa34d5bf26142f64f3cb96c429b557

This driver enforces atomic publishing for both target roots:
  1. /home/lupin/oday-plus-supervisor-live
  2. /home/lupin/oday-plus

For each target file (.orchestrator/supervisor.py and scripts/ai_status.py):
  1. Capture BEFORE receipts (sha256, size, mode, inode, mtime)
  2. Save a byte backup with a printed rollback command
  3. Write the merged bytes into a same-directory temporary sibling (O_CREAT|O_EXCL)
  4. VERIFY the sibling (sha256 == merged blob, byte length, mode, byte-for-byte compare)
  5. Atomically issue os.replace(sibling, target) and fsync the parent directory
  6. Capture AFTER receipts and assert inode CHANGED
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import os
import subprocess
import time
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[3]
TASK_ID = "ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001"
MERGE_COMMIT = "6af7b86ba4aa34d5bf26142f64f3cb96c429b557"

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
IDENTITY_MARKER = "supervisor"

FILES_TO_DEPLOY = [
    ".orchestrator/supervisor.py",
    "scripts/ai_status.py",
]


def sh(*argv: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        return [f"MainPID {pid} has no /proc entry — the process is gone"], {}
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


def supervisor_block(heading: str, state: dict[str, str]) -> None:
    print(f"## supervisor state {heading}")
    for key in SUPERVISOR_PROPS:
        print(f"  {key:22s}: {state[key]}")
    for key, value in identity_failures(state)[1].items():
        print(f"  {'process ' + key:22s}: {value}")
    print()


def receipts(target: Path, label: str) -> dict[str, object]:
    st = target.stat()
    data = {
        "sha256": sha256_of(target),
        "bytes": st.st_size,
        "mode": oct(st.st_mode & 0o7777)[2:],
        "inode": st.st_ino,
        "device": st.st_dev,
        "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
    }
    for key, value in data.items():
        print(f"  {label} {key:10s}: {value}")
    return data


def stage_and_publish(
    target: Path, payload: bytes, merged_sha: str, mode: int
) -> tuple[Path, bool]:
    sibling = target.with_name(f".{target.name}.{TASK_ID}.{os.getpid()}.tmp")
    if sibling.exists():
        sibling.unlink()

    fd = os.open(sibling, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        sibling.unlink(missing_ok=True)
        raise
    os.chmod(sibling, mode)

    print(f"  staged sibling      : {sibling}")
    sib_stat = sibling.stat()
    sib_sha = sha256_of(sibling)
    same_dir = sibling.parent == target.parent
    same_fs = sib_stat.st_dev == target.stat().st_dev

    blob_copy = Path(f"/tmp/deploy_merged_verify_{os.getpid()}.tmp")
    blob_copy.write_bytes(payload)
    is_identical = filecmp.cmp(sibling, blob_copy, shallow=False)
    blob_copy.unlink(missing_ok=True)

    checks = [
        ("same directory as target", same_dir, True),
        ("same filesystem (rename is atomic)", same_fs, True),
        ("sha256 == merged blob", sib_sha == merged_sha, True),
        ("byte length == merged blob", sib_stat.st_size == len(payload), True),
        ("mode == target's existing mode", oct(sib_stat.st_mode & 0o7777)[2:] == oct(mode)[2:], True),
        ("byte-for-byte compare vs blob", is_identical, True),
    ]

    print(f"  sibling sha256      : {sib_sha}")
    print(f"  sibling bytes/mode  : {sib_stat.st_size} / {oct(sib_stat.st_mode & 0o7777)[2:]}")
    print("  verification before publishing:")
    ok = True
    for name, got, want in checks:
        print(f"    {'PASS' if got == want else 'FAIL'}  {name}")
        ok = ok and (got == want)

    if not ok:
        sibling.unlink(missing_ok=True)
        print("  VERIFICATION FAILED -> sibling unlinked, target NOT touched, no rename issued")
        return sibling, False

    dir_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.replace(sibling, target)
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    print(f"  published           : os.replace({sibling.name} -> {target.name}) [atomic rename]")
    return sibling, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--merge", default=MERGE_COMMIT)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--root", action="append", required=True)
    args = parser.parse_args()

    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    print("# Deployment transcript — atomic publish of exact PR #505 merged files")
    print(f"# host date : {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    print(f"# driver    : {Path(__file__).name} (same-dir sibling + verify + os.replace)")
    print(f"# merge     : {args.merge}")
    print(f"# backups   : {backup_dir}")
    print()

    payloads: dict[str, tuple[bytes, str]] = {}
    print("## source materialised from merge commit blob")
    for rel_path in FILES_TO_DEPLOY:
        blob = sh("git", "rev-parse", f"{args.merge}:{rel_path}", cwd=WORKTREE)
        payload = subprocess.run(
            ["git", "cat-file", "blob", blob],
            cwd=WORKTREE,
            capture_output=True,
            check=True,
        ).stdout
        merged_sha = hashlib.sha256(payload).hexdigest()
        payloads[rel_path] = (payload, merged_sha)
        print(f"  {rel_path:30s}: blob {blob[:10]}... sha256 {merged_sha} ({len(payload)} bytes)")
    print()

    try:
        before_state = probe_supervisor()
    except SupervisorProbeError as exc:
        print("## supervisor state BEFORE")
        print(f"  PROBE FAILED: {exc}")
        print()
        print("RESULT: FAIL — preflight gate: Supervisor state unreadable")
        return 2

    supervisor_block("BEFORE", before_state)
    preflight = preflight_failures(before_state)
    print("## preflight gate")
    for label in preflight:
        print(f"    FAIL  {label}")
    if preflight:
        print()
        print("RESULT: FAIL — preflight gate refused to deploy")
        return 2
    print("    PASS  unit loaded and active/running")
    print("    PASS  MainPID is a live process whose cmdline is the Supervisor")
    print(f"    PASS  pinned to {', '.join(f'{f}={before_state[f]}' for f in ('MainPID', 'ExecMainStartTimestamp', 'NRestarts'))}")
    print()

    failures: list[str] = []
    for root_arg in args.root:
        root = Path(root_arg)
        name = root.name
        print(f"=== Root: {root} ===")
        print(f"  git branch          : {sh('git', 'rev-parse', '--abbrev-ref', 'HEAD', cwd=root)}")
        print(f"  git HEAD            : {sh('git', 'rev-parse', 'HEAD', cwd=root)}")

        dirty_before = sh("git", "status", "--porcelain", cwd=root)
        (backup_dir / f"{name}.dirty-before.txt").write_text(dirty_before + "\n")
        print(f"  dirty files before  : {len(dirty_before.splitlines())}")

        for rel_path in FILES_TO_DEPLOY:
            target = root / rel_path
            payload, merged_sha = payloads[rel_path]
            print(f"\n--- Target: {target} ---")
            before = receipts(target, "BEFORE")
            mode = int(str(before["mode"]), 8)

            file_slug = rel_path.replace("/", "_")
            backup = backup_dir / f"{name}.{file_slug}.bak"
            backup.write_bytes(target.read_bytes())
            os.chmod(backup, mode)
            print(f"  backup              : {backup}")
            print(f"  backup sha256       : {sha256_of(backup)}")
            print(f"  rollback            : install -m {before['mode']} {backup} {target}")

            sibling, published = stage_and_publish(target, payload, merged_sha, mode)
            if not published:
                failures.append(f"{target}: stage and publish failed")

            after = receipts(target, "AFTER ")
            checks = [
                ("target sha256 == merged blob", after["sha256"] == merged_sha),
                ("target bytes == merged blob", after["bytes"] == len(payload)),
                ("mode preserved", after["mode"] == before["mode"]),
                ("inode CHANGED (proves rename, not in-place write)", after["inode"] != before["inode"]),
                ("no temporary sibling left behind", not sibling.exists()),
            ]
            print("  post-publish assertions:")
            for label, got in checks:
                print(f"    {'PASS' if got else 'FAIL'}  {label}")
                if not got:
                    failures.append(f"{target}: {label}")

        dirty_after = sh("git", "status", "--porcelain", cwd=root)
        (backup_dir / f"{name}.dirty-after.txt").write_text(dirty_after + "\n")
        print(f"\n  dirty files after   : {len(dirty_after.splitlines())}")
        keep = lambda lines: [ln[3:].strip() for ln in lines if ln.strip() and not any(f in ln for f in FILES_TO_DEPLOY)]
        same_rest = sorted(keep(dirty_before.splitlines())) == sorted(keep(dirty_after.splitlines()))
        print(f"  unrelated dirty files unchanged : {same_rest}")
        if not same_rest:
            failures.append(f"{root}: unrelated dirty inventory changed")
        print()

    if failures:
        print("RESULT: FAIL")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("RESULT: PASS — all PR #505 files published atomically to all roots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
