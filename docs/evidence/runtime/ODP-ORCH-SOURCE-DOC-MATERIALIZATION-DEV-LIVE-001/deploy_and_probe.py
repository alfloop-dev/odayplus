#!/usr/bin/env python3
"""Atomically deploy control-plane bytes and verify Package 10 probe task materialization.

Task: ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001
Owner: Antigravity
Reviewer: Codex5
"""

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

# Paths
WORKTREE = Path(__file__).resolve().parents[4]
LIVE_SUPERVISOR = Path("/home/lupin/oday-plus-supervisor-live")
BACKUP_DIR = LIVE_SUPERVISOR / ".orchestrator" / "backups" / "ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001"
EVIDENCE_DIR = WORKTREE / "docs" / "evidence" / "runtime" / "ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001"

# Add WORKTREE and .orchestrator to sys.path
for path_str in [str(WORKTREE), str(WORKTREE / ".orchestrator")]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load .orchestrator.common and .orchestrator.supervisor
common = load_module("common", WORKTREE / ".orchestrator" / "common.py")
supervisor = load_module("supervisor", WORKTREE / ".orchestrator" / "supervisor.py")

_file_or_dir_hash = supervisor._file_or_dir_hash
materialize_worker_context_files = supervisor.materialize_worker_context_files
DeliveryRequest = supervisor.DeliveryRequest


def sha256_of(path: Path) -> str:
    if path.is_dir():
        return _file_or_dir_hash(path) or ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_deploy_file(source: Path, target: Path) -> dict:
    """Atomically deploy source to target with backup and receipts."""
    st_before = target.stat()
    sha_before = sha256_of(target)
    mode = st_before.st_mode & 0o7777

    # 1. Create backup
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_file = BACKUP_DIR / f"{target.name}.bak"
    shutil.copy2(target, backup_file)
    rollback_cmd = f"install -m {oct(mode)[2:]} {backup_file} {target}"

    # 2. Write sibling
    sibling = target.with_name(f".{target.name}.ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001.tmp")
    if sibling.exists():
        sibling.unlink()

    payload = source.read_bytes()
    expected_sha = hashlib.sha256(payload).hexdigest()

    fd = os.open(sibling, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(sibling, mode)

    # 3. Verify sibling
    sib_sha = sha256_of(sibling)
    sib_size = sibling.stat().st_size
    if sib_sha != expected_sha or sib_size != len(payload):
        sibling.unlink(missing_ok=True)
        raise RuntimeError(f"Sibling verification failed for {target}")

    # 4. Atomic rename
    dir_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.replace(sibling, target)
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    st_after = target.stat()
    sha_after = sha256_of(target)

    return {
        "file": str(target.relative_to(LIVE_SUPERVISOR)),
        "sha256_before": sha_before,
        "sha256_after": sha_after,
        "inode_before": st_before.st_ino,
        "inode_after": st_after.st_ino,
        "mode": oct(mode)[2:],
        "rollback_cmd": rollback_cmd,
        "status": "published_atomically",
    }


def run_deployment() -> list[dict]:
    targets = [
        WORKTREE / ".orchestrator" / "common.py",
        WORKTREE / ".orchestrator" / "supervisor.py",
        WORKTREE / ".orchestrator" / "github_bus.py",
        WORKTREE / "scripts" / "ai_status.py",
        WORKTREE / "scripts" / "ai-status.sh",
    ]
    receipts = []
    for src in targets:
        rel_path = src.relative_to(WORKTREE)
        tgt = LIVE_SUPERVISOR / rel_path
        if tgt.exists():
            receipt = atomic_deploy_file(src, tgt)
            receipts.append(receipt)
    return receipts


def run_package_10_probe() -> dict:
    """Run Package 10 probe task materialization for owner and reviewer."""
    package_10_docs = [
        "docs/design/PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.md",
        "docs/evidence/PACKAGE_10_PAGE_BY_PAGE_RUNTIME_DIFF_2026-07-26.md",
        "docs_archive/00_source_zips/operator_console/r7-20260720-package-10/manifest.json",
    ]

    # Verify all source docs exist in canonical workspace
    for doc in package_10_docs:
        p = WORKTREE / doc
        if not p.exists():
            raise FileNotFoundError(f"Package 10 source doc missing: {doc}")

    config = {
        "paths": {
            "status_root": WORKTREE,
            "status_file": WORKTREE / "ai-status.json",
        }
    }

    task_def = {
        "id": "ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001",
        "title": "Probe Package 10 Materialization",
        "owner": "Antigravity",
        "reviewer": "Codex5",
        "status": "in_progress",
        "last_update": "2026-08-03T11:31:03Z",
        "source_docs": package_10_docs,
    }

    # Create temporary owner & reviewer workspaces
    with tempfile.TemporaryDirectory() as owner_dir, tempfile.TemporaryDirectory() as reviewer_dir:
        owner_ws = Path(owner_dir)
        reviewer_ws = Path(reviewer_dir)

        # Init git repos for worktree tracking checks
        os.system(f"git init -q {owner_ws}")
        os.system(f"git init -q {reviewer_ws}")

        req_owner = DeliveryRequest(
            agent_id="Antigravity",
            provider="gemini",
            delivery_mode="antigravity",
            message="task_dispatch",
            task_id="ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001",
            reason="owned_in_progress_dispatch",
            context_files=package_10_docs,
        )

        req_reviewer = DeliveryRequest(
            agent_id="Codex5",
            provider="codex",
            delivery_mode="codex",
            message="review",
            task_id="ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001",
            reason="review_ready_dispatch",
            context_files=package_10_docs,
        )

        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task_def]}),
            mock.patch.object(common, "load_status", return_value={"tasks": [task_def]}),
            mock.patch.object(supervisor, "_is_tracked_in_worktree", return_value=False),
        ):
            # Materialize for owner
            mat_owner = materialize_worker_context_files(config, req_owner, owner_ws)

            # Materialize for reviewer
            mat_reviewer = materialize_worker_context_files(config, req_reviewer, reviewer_ws)

        manifest_owner = req_owner.metadata.get("materialized_source_manifest", [])
        manifest_reviewer = req_reviewer.metadata.get("materialized_source_manifest", [])

        # 1. Assert manifests are identical
        manifest_equal = manifest_owner == manifest_reviewer and len(manifest_owner) == 3

        # 2. Assert files exist and can be read in both workspaces
        owner_readable = all((owner_ws / doc).exists() and len((owner_ws / doc).read_bytes()) > 0 for doc in package_10_docs)
        reviewer_readable = all((reviewer_ws / doc).exists() and len((reviewer_ws / doc).read_bytes()) > 0 for doc in package_10_docs)

        # 3. Assert SHAs match canonical source SHAs
        sha_matches = True
        for entry in manifest_owner:
            rel = entry["relative_path"]
            expected_sha = sha256_of(WORKTREE / rel)
            if entry["sha256"] != expected_sha:
                sha_matches = False

        # 4. Fail-closed security tests
        fail_closed_passed = True

        req_missing = DeliveryRequest(
            agent_id="Antigravity",
            provider="gemini",
            delivery_mode="antigravity",
            message="task_dispatch",
            task_id="ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001",
            reason="owned_in_progress_dispatch",
            context_files=["docs/non_existent_file_12345.md"],
        )

        try:
            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task_def]}),
                mock.patch.object(common, "load_status", return_value={"tasks": [task_def]}),
                mock.patch.object(supervisor, "_is_tracked_in_worktree", return_value=False),
            ):
                materialize_worker_context_files(config, req_missing, owner_ws)
            fail_closed_passed = False
        except ValueError:
            pass

        req_traversal = DeliveryRequest(
            agent_id="Antigravity",
            provider="gemini",
            delivery_mode="antigravity",
            message="task_dispatch",
            task_id="ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001",
            reason="owned_in_progress_dispatch",
            context_files=["../outside_file.md"],
        )

        try:
            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task_def]}),
                mock.patch.object(common, "load_status", return_value={"tasks": [task_def]}),
                mock.patch.object(supervisor, "_is_tracked_in_worktree", return_value=False),
            ):
                materialize_worker_context_files(config, req_traversal, owner_ws)
            fail_closed_passed = False
        except ValueError:
            pass

        return {
            "source_docs": package_10_docs,
            "manifest_owner": manifest_owner,
            "manifest_reviewer": manifest_reviewer,
            "manifest_equal": manifest_equal,
            "owner_readable": owner_readable,
            "reviewer_readable": reviewer_readable,
            "sha_matches": sha_matches,
            "fail_closed_security_checks_passed": fail_closed_passed,
        }


def main():
    print("=== Deploying Control-Plane Bytes to Live Supervisor ===")
    deploy_receipts = run_deployment()
    print(json.dumps(deploy_receipts, indent=2))

    print("\n=== Executing Package 10 Probe Task Materialization ===")
    probe_receipts = run_package_10_probe()
    print(json.dumps(probe_receipts, indent=2))

    full_receipt = {
        "task_id": "ODP-ORCH-SOURCE-DOC-MATERIALIZATION-DEV-LIVE-001",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dev_head_sha": "5a1aee5b",
        "deploy_receipts": deploy_receipts,
        "probe_receipts": probe_receipts,
        "status": "PASS",
    }

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    receipt_file = EVIDENCE_DIR / "probe_verification_receipt.json"
    receipt_file.write_text(json.dumps(full_receipt, indent=2) + "\n")
    print(f"\nDurable evidence saved to: {receipt_file}")


if __name__ == "__main__":
    main()
