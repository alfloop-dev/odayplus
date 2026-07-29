#!/usr/bin/env python3
"""Atomically deploy the exact PR #496 merged `scripts/ai_status.py`.

Round 1 of this rollout wrote the merged blob straight onto each live target
with `install -m`. That lands the right bytes but it is *not* atomic: a reader
(the live Supervisor, or any worker CLI) that opens the target mid-write sees a
truncated file. The reviewer's stop gate requires the write to go through a
same-directory temporary sibling that is fully verified before it is published.

This driver enforces that order for every target:

  1. capture BEFORE receipts (sha256, size, mode, inode, mtime) and the root's
     full dirty inventory
  2. back the current file up, with a one-line rollback printed
  3. write the merged bytes into a *same-directory* temporary sibling
     (``O_CREAT|O_EXCL``), flush and ``fsync`` it, then ``chmod`` it to the
     target's existing mode
  4. VERIFY the sibling — sha256 == merged blob, byte length, mode, and a
     byte-for-byte compare against the materialised blob. Any mismatch unlinks
     the sibling and aborts with the target untouched.
  5. only then ``os.replace(sibling, target)`` — a same-filesystem rename(2),
     so every reader sees either the whole old file or the whole new one, never
     a partial one — and ``fsync`` the directory so the rename is durable
  6. capture AFTER receipts. The inode MUST change: that is the on-disk proof
     the file was published by rename rather than overwritten in place.

Nothing here starts, stops, restarts or reloads the Supervisor, and the run is
gated on that being observably true — not merely printed:

  * PREFLIGHT, before any target is touched: the unit must be loaded and
    ``active/running``, ``MainPID`` must be a live PID whose ``/proc`` entry
    still looks like the Supervisor, and ``ExecMainStartTimestamp`` /
    ``NRestarts`` must be readable. Anything else aborts with rc 2 and every
    target byte-for-byte untouched.
  * CONTINUITY, after the last publish: the unit must still be
    ``active/running`` and ``MainPID``, ``ExecMainStartTimestamp`` and
    ``NRestarts`` must each be *exactly* equal to the preflight reading. Any
    drift fails the run.
  * A failed or unparseable ``systemctl`` query is fatal in both positions.
    Round 2 printed ``ActiveState``/``SubState``/``NRestarts`` but only failed
    on PID and start-timestamp changes, so a unit that went ``inactive/dead``
    with ``NRestarts`` 0->1 still returned ``RESULT: PASS``. That hole is what
    this gate closes; ``supervisor_gate_selftest.py`` proves it closed.

Usage:
    deploy.py --merge <commit> --backup-dir <dir> --root <path> [--root <path>]
              [--corrupt-payload]

``--corrupt-payload`` is the negative rehearsal: it flips one byte of the
payload *after* the merged digest is computed, so the verification step in (4)
must abort before any rename happens. Only ever point that at a sandbox copy.

Exit codes: 0 pass, 1 the run failed after work began, 2 the preflight gate
refused to start and nothing was touched.
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import os
import subprocess
import time
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[4]
TASK_ID = "ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001"

SUPERVISOR_UNIT = "pantheon-supervisor.service"
# Queried in a single `systemctl show`, so BEFORE and AFTER are each one
# consistent snapshot rather than five independently-raced reads.
SUPERVISOR_PROPS = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "MainPID",
    "ExecMainStartTimestamp",
    "NRestarts",
)
# The three fields that must be *identical* across the deploy. A restart moves
# all three; a restart that happened to reuse the PID still moves the other two.
CONTINUITY_FIELDS = ("MainPID", "ExecMainStartTimestamp", "NRestarts")
IDENTITY_MARKER = "supervisor"


def sh(*argv: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SupervisorProbeError(RuntimeError):
    """The unit's state could not be read. Always fatal — never assumed benign."""


def probe_supervisor() -> dict[str, str]:
    """One `systemctl show` snapshot. A failed or incomplete query raises."""
    argv = ["systemctl", "--user", "show", SUPERVISOR_UNIT]
    argv += [f"--property={prop}" for prop in SUPERVISOR_PROPS]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:  # systemctl missing entirely
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
    """Is MainPID a live process that still looks like the Supervisor?"""
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
    """Must the run refuse to start? Evaluated before any target is touched."""
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
    """Did the Supervisor survive the deploy as the exact same process?"""
    failures: list[str] = []
    if after["ActiveState"] != "active" or after["SubState"] != "running":
        failures.append(
            f"supervisor is {after['ActiveState']}/{after['SubState']} after the deploy,"
            " expected active/running"
        )
    if after["LoadState"] != "loaded":
        failures.append(f"LoadState became {after['LoadState']!r} after the deploy")
    for field in CONTINUITY_FIELDS:
        if before[field] != after[field]:
            failures.append(
                f"{field} changed across the deploy: {before[field]!r} -> {after[field]!r}"
            )
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
    target: Path, payload: bytes, merged_sha: str, mode: int, corrupt: bool
) -> tuple[Path, bool]:
    """Write a verified same-directory sibling, then atomically rename it in."""
    sibling = target.with_name(f".{target.name}.{TASK_ID}.{os.getpid()}.tmp")
    if sibling.exists():  # never reuse a stale sibling
        sibling.unlink()

    written = bytearray(payload)
    if corrupt:
        written[0] ^= 0xFF  # rehearsal only: break the payload after hashing

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

    print(f"  staged sibling      : {sibling}")
    sib_stat = sibling.stat()
    sib_sha = sha256_of(sibling)
    same_dir = sibling.parent == target.parent
    same_fs = sib_stat.st_dev == target.stat().st_dev
    checks = [
        ("same directory as target", same_dir, True),
        ("same filesystem (rename is atomic)", same_fs, True),
        ("sha256 == merged blob", sib_sha == merged_sha, True),
        ("byte length == merged blob", sib_stat.st_size == len(payload), True),
        ("mode == target's existing mode", oct(sib_stat.st_mode & 0o7777)[2:] == oct(mode)[2:], True),
        ("byte-for-byte compare vs blob", None, True),
    ]
    blob_copy = Path(f"/tmp/ai_status.merged-verify-{os.getpid()}.py")
    blob_copy.write_bytes(payload)
    checks[-1] = ("byte-for-byte compare vs blob", filecmp.cmp(sibling, blob_copy, shallow=False), True)
    blob_copy.unlink(missing_ok=True)

    print(f"  sibling sha256      : {sib_sha}")
    print(f"  sibling bytes/mode  : {sib_stat.st_size} / {oct(sib_stat.st_mode & 0o7777)[2:]}")
    print("  verification before publishing:")
    ok = True
    for name, got, want in checks:
        print(f"    {'PASS' if got == want else 'FAIL'}  {name}")
        ok = ok and got == want

    if not ok:
        sibling.unlink(missing_ok=True)
        print("  VERIFICATION FAILED -> sibling unlinked, target NOT touched, no rename issued")
        return sibling, False

    dir_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.replace(sibling, target)  # atomic same-directory rename(2)
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    print(f"  published           : os.replace({sibling.name} -> {target.name}) [atomic rename]")
    return sibling, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--merge", required=True)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--corrupt-payload", action="store_true")
    args = parser.parse_args()

    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    print("# Deployment transcript — atomic publish of the exact merged scripts/ai_status.py")
    print(f"# host date : {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    print(f"# driver    : {Path(__file__).name} (same-dir sibling + verify + os.replace)")
    print(f"# merge     : {args.merge}")
    print(f"# backups   : {backup_dir}")
    if args.corrupt_payload:
        print("# MODE      : NEGATIVE REHEARSAL — payload is corrupted after hashing;")
        print("#             the verification gate must abort before any rename.")
    print()

    print("## source materialised from the merge commit blob (no working-tree copy)")
    blob = sh("git", "rev-parse", f"{args.merge}:scripts/ai_status.py", cwd=WORKTREE)
    payload = subprocess.run(
        ["git", "cat-file", "blob", blob],
        cwd=WORKTREE,
        capture_output=True,
        check=True,
    ).stdout
    merged_sha = hashlib.sha256(payload).hexdigest()
    print(f"  blob        : {blob}")
    print(f"  sha256      : {merged_sha}")
    print(f"  bytes       : {len(payload)}")
    print()

    try:
        before_state = probe_supervisor()
    except SupervisorProbeError as exc:
        print("## supervisor state BEFORE")
        print(f"  PROBE FAILED: {exc}")
        print()
        print("RESULT: FAIL — preflight gate: the Supervisor's state is unreadable,")
        print("               so no target was touched.")
        return 2

    supervisor_block("BEFORE", before_state)
    preflight = preflight_failures(before_state)
    print("## preflight gate (evaluated before any target is touched)")
    for label in preflight:
        print(f"    FAIL  {label}")
    if preflight:
        print()
        print("RESULT: FAIL — preflight gate refused to deploy; no target was touched.")
        return 2
    print("    PASS  unit loaded and active/running")
    print("    PASS  MainPID is a live process whose cmdline is the Supervisor")
    print(f"    PASS  continuity pinned to {', '.join(f'{f}={before_state[f]}' for f in CONTINUITY_FIELDS)}")
    print()

    failures: list[str] = []
    aborted_targets: list[bool] = []
    for root_arg in args.root:
        root = Path(root_arg)
        name = root.name
        target = root / "scripts" / "ai_status.py"
        print(f"## target: {target}")
        print(f"  git branch          : {sh('git', 'rev-parse', '--abbrev-ref', 'HEAD', cwd=root)}")
        print(f"  git HEAD            : {sh('git', 'rev-parse', 'HEAD', cwd=root)}")

        dirty_before = sh("git", "status", "--porcelain", cwd=root)
        (backup_dir / f"{name}.dirty-before.txt").write_text(dirty_before + "\n")
        print(f"  dirty files         : {len(dirty_before.splitlines())}")
        print(f"  dirty inventory     : {backup_dir / f'{name}.dirty-before.txt'}")

        before = receipts(target, "BEFORE")
        mode = int(str(before["mode"]), 8)

        backup = backup_dir / f"{name}.ai_status.py.bak"
        backup.write_bytes(target.read_bytes())
        os.chmod(backup, mode)
        print(f"  backup              : {backup}")
        print(f"  backup sha256       : {sha256_of(backup)}")
        print(f"  rollback            : install -m {before['mode']} {backup} {target}")

        sibling, published = stage_and_publish(target, payload, merged_sha, mode, args.corrupt_payload)
        aborted_targets.append(not published)

        after = receipts(target, "AFTER ")
        if published:
            checks = [
                ("target sha256 == merged blob", after["sha256"] == merged_sha),
                ("target bytes == merged blob", after["bytes"] == len(payload)),
                ("mode preserved", after["mode"] == before["mode"]),
                ("inode CHANGED (proves rename, not in-place write)", after["inode"] != before["inode"]),
                ("no temporary sibling left behind", not sibling.exists()),
            ]
        else:
            checks = [
                ("target sha256 UNCHANGED by the aborted run", after["sha256"] == before["sha256"]),
                ("target bytes UNCHANGED", after["bytes"] == before["bytes"]),
                ("target mode UNCHANGED", after["mode"] == before["mode"]),
                ("target inode UNCHANGED (no rename was issued)", after["inode"] == before["inode"]),
                ("no temporary sibling left behind", not sibling.exists()),
            ]
        print("  post-publish assertions:")
        for label, got in checks:
            print(f"    {'PASS' if got else 'FAIL'}  {label}")
            if not got:
                failures.append(f"{target}: {label}")

        leftovers = sorted(p.name for p in target.parent.glob(f".{target.name}.*.tmp"))
        print(f"  leftover siblings   : {leftovers or 'none'}")
        if leftovers:
            failures.append(f"{target}: leftover staging siblings {leftovers}")

        dirty_after = sh("git", "status", "--porcelain", cwd=root)
        (backup_dir / f"{name}.dirty-after.txt").write_text(dirty_after + "\n")
        print(f"  dirty files after   : {len(dirty_after.splitlines())}")
        keep = lambda lines: [ln for ln in lines if "scripts/ai_status.py" not in ln]  # noqa: E731
        same_rest = keep(dirty_before.splitlines()) == keep(dirty_after.splitlines())
        print(f"  every other dirty file unchanged : {same_rest}")
        if not same_rest:
            only_before = set(keep(dirty_before.splitlines())) - set(keep(dirty_after.splitlines()))
            only_after = set(keep(dirty_after.splitlines())) - set(keep(dirty_before.splitlines()))
            print(f"    only before: {sorted(only_before)}")
            print(f"    only after : {sorted(only_after)}")
            failures.append(f"{root}: unrelated dirty inventory changed")
        print()

    try:
        after_state: dict[str, str] | None = probe_supervisor()
    except SupervisorProbeError as exc:
        after_state = None
        print("## supervisor state AFTER")
        print(f"  PROBE FAILED: {exc}")
        print()
        failures.append(f"supervisor state unreadable after the deploy: {exc}")

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
    print("# No systemctl start/stop/restart/reload was issued by this script.")
    print()

    if args.corrupt_payload:
        # In rehearsal the gate is supposed to fire; a clean publish would be the bug.
        gate_fired = all(aborted_targets) and bool(aborted_targets) and not failures
        print(
            "RESULT: PASS — verification gate aborted the corrupted payload and left"
            " every target byte-for-byte untouched"
            if gate_fired
            else "RESULT: FAIL — the corrupted payload was not stopped cleanly"
        )
        for item in failures:
            print(f"  - {item}")
        return 0 if gate_fired else 1

    if any(aborted_targets):
        failures.append("a target aborted verification during a real deploy")

    if failures:
        print("RESULT: FAIL")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("RESULT: PASS — merged blob published atomically to every target")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
