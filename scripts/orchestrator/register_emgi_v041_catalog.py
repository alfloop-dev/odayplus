#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CATALOG_VERSION = "0.4.1"
EXPECTED_TASK_COUNT = 50
AUTHORITY_REPOSITORY = "alfloop-dev/oday-data-platform"
WAVE_0 = ["DPF-GOV-001"]
WAVE_1 = ["DPF-KRN-MEAS-001", "DPF-KRN-DATASET-001", "DPF-KRN-TIME-001"]
TERMINAL_DONE = {"done"}


class CatalogRegistrationError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogRegistrationError(f"unable to read JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise CatalogRegistrationError(f"expected JSON object: {path}")
    return payload


def hydrate_archived_catalog_tasks(
    status_payload: dict[str, Any],
    catalog: dict[str, Any],
    archive_tasks_dir: Path,
) -> int:
    """Restore terminal catalog lifecycle long enough to release dependencies.

    A rebuilt active board intentionally omits archived terminal tasks.  Catalog
    registration still needs those tasks while deciding whether a staged wave
    may be unlocked.  The ordinary status sync archives them again after the
    registration transaction, so this does not resurrect completed work.
    """
    tasks = status_payload.setdefault("tasks", [])
    if not isinstance(tasks, list):
        raise CatalogRegistrationError("status payload tasks must be an array")
    existing_ids = {
        str(task.get("id"))
        for task in tasks
        if isinstance(task, dict) and task.get("id")
    }
    catalog_ids = {str(task["id"]) for task in catalog["definitions"]}
    restored = 0
    for task_id in sorted(catalog_ids - existing_ids):
        snapshot_path = archive_tasks_dir / f"{task_id}.json"
        if not snapshot_path.exists():
            continue
        snapshot = _load_json(snapshot_path)
        archived_task = snapshot.get("task")
        if not isinstance(archived_task, dict):
            continue
        if str(snapshot.get("terminal_status") or archived_task.get("status") or "").lower() not in TERMINAL_DONE:
            continue
        restored_task = copy.deepcopy(archived_task)
        restored_task["id"] = task_id
        restored_task["status"] = "done"
        restored_task.setdefault("terminal_outcome", snapshot.get("terminal_outcome") or "completed")
        tasks.append(restored_task)
        restored += 1
    return restored


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise CatalogRegistrationError(
            f"git {' '.join(args)} failed in {root}: {(proc.stderr or proc.stdout).strip()}"
        )
    return proc.stdout.strip()


def _authority_root(manifest_root: Path) -> Path:
    proc = subprocess.run(
        ["git", "-C", str(manifest_root), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise CatalogRegistrationError("manifest root is not inside the authority git repository")
    return Path(proc.stdout.strip()).resolve()


def _resolve_authority_sha(authority_root: Path, authority_ref: str | None) -> str:
    reference = authority_ref or "HEAD"
    sha = _git(authority_root, "rev-parse", "--verify", f"{reference}^{{commit}}").lower()
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
        raise CatalogRegistrationError(f"invalid authority commit: {sha}")
    return sha


def _path_at_commit(authority_root: Path, sha: str, path: Path) -> None:
    relative = path.resolve().relative_to(authority_root).as_posix()
    proc = subprocess.run(
        ["git", "-C", str(authority_root), "cat-file", "-e", f"{sha}:{relative}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise CatalogRegistrationError(
            f"authority file does not exist at {sha[:12]}: {relative}"
        )


def load_catalog(
    manifest_root: Path,
    *,
    authority_ref: str | None = None,
) -> dict[str, Any]:
    manifest_root = manifest_root.resolve()
    manifest_path = manifest_root / "manifest.json"
    manifest = _load_json(manifest_path)
    if manifest.get("version") != CATALOG_VERSION:
        raise CatalogRegistrationError(
            f"expected EMGI {CATALOG_VERSION}, got {manifest.get('version')!r}"
        )
    if manifest.get("status") != "approved_for_staged_dispatch":
        raise CatalogRegistrationError(
            f"manifest is not dispatchable: {manifest.get('status')!r}"
        )

    definition_names = list(
        (manifest.get("dispatch_contract") or {}).get("definition_files") or []
    )
    if not definition_names:
        raise CatalogRegistrationError("manifest has no definition files")

    authority_root = _authority_root(manifest_root)
    authority_sha = _resolve_authority_sha(authority_root, authority_ref)
    _path_at_commit(authority_root, authority_sha, manifest_path)

    definitions: list[dict[str, Any]] = []
    definition_by_task: dict[str, str] = {}
    for relative_name in definition_names:
        definition_path = manifest_root / str(relative_name)
        _path_at_commit(authority_root, authority_sha, definition_path)
        payload = _load_json(definition_path)
        for task in payload.get("tasks") or []:
            if not isinstance(task, dict):
                raise CatalogRegistrationError(f"non-object task in {definition_path}")
            task_id = str(task.get("id") or "").strip()
            if not task_id:
                raise CatalogRegistrationError(f"task without id in {definition_path}")
            if task_id in definition_by_task:
                raise CatalogRegistrationError(
                    f"duplicate task definition {task_id}: {definition_by_task[task_id]} and {relative_name}"
                )
            definition_by_task[task_id] = str(relative_name)
            definitions.append(copy.deepcopy(task))

    ids = {str(task["id"]) for task in definitions}
    catalog_ids = set((manifest.get("task_catalog") or {}).get("task_ids") or [])
    if len(definitions) != EXPECTED_TASK_COUNT or len(ids) != EXPECTED_TASK_COUNT:
        raise CatalogRegistrationError(
            f"expected {EXPECTED_TASK_COUNT} unique definitions, got {len(definitions)} / {len(ids)}"
        )
    if ids != catalog_ids:
        missing = sorted(catalog_ids - ids)
        extra = sorted(ids - catalog_ids)
        raise CatalogRegistrationError(
            f"definition/catalog mismatch; missing={missing}, extra={extra}"
        )

    for task in definitions:
        unknown = sorted(set(task.get("depends_on") or []) - ids)
        if unknown:
            raise CatalogRegistrationError(
                f"{task['id']} has unknown dependencies: {unknown}"
            )

    manifest_relative = manifest_path.relative_to(authority_root).as_posix()
    return {
        "manifest": manifest,
        "manifest_path": manifest_relative,
        "authority_root": authority_root,
        "authority_sha": authority_sha,
        "definitions": definitions,
        "definition_by_task": definition_by_task,
    }


def _artifact_prefix(repository: str) -> str:
    if repository == "alfloop-dev/oday-data-platform":
        return "oday-data-platform/"
    if repository == "alfloop-dev/odayplus":
        return "odayplus/"
    raise CatalogRegistrationError(f"unsupported task repository: {repository}")


def _catalog_task(
    definition: dict[str, Any],
    *,
    definition_file: str,
    manifest_path: str,
    authority_sha: str,
) -> dict[str, Any]:
    task_id = str(definition["id"])
    repository = str(definition.get("repository") or "").strip()
    prefix = _artifact_prefix(repository)
    owned_paths = [str(path) for path in definition.get("owned_paths") or []]
    source_docs = [
        f"github://{AUTHORITY_REPOSITORY}@{authority_sha}/{manifest_path}",
        f"github://{AUTHORITY_REPOSITORY}@{authority_sha}/docs/design/emgi/v0.4.1/tasks/{definition_file}",
    ]
    dependencies = [str(value) for value in definition.get("depends_on") or []]
    status = "todo" if task_id in WAVE_0 else "blocked"
    blocked_reason = None if status == "todo" else (
        "staged dispatch gate; waiting for DPF-GOV-001 completion"
        if not dependencies
        else f"waiting for dependencies: {', '.join(dependencies)}"
    )
    task: dict[str, Any] = {
        "id": task_id,
        "title": definition.get("title") or task_id,
        "phase": definition.get("group") or "EMGI v0.4.1",
        "priority": definition.get("priority") or "P0",
        "owner": definition.get("owner") or "AUTO_ASSIGN",
        "reviewer": definition.get("reviewer") or "DIFFERENT_AGENT_REQUIRED",
        "status": status,
        "depends_on": dependencies,
        "artifacts": [prefix + path.lstrip("/") for path in owned_paths],
        "owned_paths": owned_paths,
        "forbidden_paths": list(definition.get("forbidden_paths") or []),
        "requires_contracts": list(definition.get("requires_contracts") or []),
        "provides_contracts": list(definition.get("provides_contracts") or []),
        "source_docs": source_docs,
        "source_commit_sha": authority_sha,
        "source_plane": "emgi-v0.4.1-catalog",
        "source_ref": {
            "repository": AUTHORITY_REPOSITORY,
            "commit_sha": authority_sha,
            "manifest": manifest_path,
            "definition_file": f"docs/design/emgi/v0.4.1/tasks/{definition_file}",
            "catalog_version": CATALOG_VERSION,
        },
        "acceptance": list(definition.get("acceptance") or []),
        "verification": list(definition.get("verification") or []),
        "evidence_path": definition.get("evidence_path"),
        "summary_zh": definition.get("title") or task_id,
        "next": blocked_reason or "Execute Wave 0 governance task.",
        "blocked_reason": blocked_reason,
        "catalog_managed": True,
        "catalog_version": CATALOG_VERSION,
        "catalog_definition_file": definition_file,
        "repository": repository,
        "base_branch": definition.get("base_branch") or "dev",
    }
    return task


def register_catalog(
    status_payload: dict[str, Any],
    catalog: dict[str, Any],
    *,
    unlock_wave_1: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    output = copy.deepcopy(status_payload)
    current_tasks = output.setdefault("tasks", [])
    if not isinstance(current_tasks, list):
        raise CatalogRegistrationError("status payload tasks must be an array")
    existing_by_id = {
        str(task.get("id")): task
        for task in current_tasks
        if isinstance(task, dict) and task.get("id")
    }

    if unlock_wave_1:
        gov = existing_by_id.get("DPF-GOV-001")
        if not gov or str(gov.get("status") or "") not in TERMINAL_DONE:
            raise CatalogRegistrationError(
                "Wave 1 cannot be unlocked before DPF-GOV-001 is done"
            )

    registered = 0
    preserved = 0
    for definition in catalog["definitions"]:
        task_id = str(definition["id"])
        incoming = _catalog_task(
            definition,
            definition_file=catalog["definition_by_task"][task_id],
            manifest_path=catalog["manifest_path"],
            authority_sha=catalog["authority_sha"],
        )
        existing = existing_by_id.get(task_id)
        if existing is None:
            if unlock_wave_1 and task_id in WAVE_1:
                incoming["status"] = "todo"
                incoming["blocked_reason"] = None
                incoming["next"] = "Wave 1 unlocked after DPF-GOV-001 completion."
            current_tasks.append(incoming)
            existing_by_id[task_id] = incoming
            registered += 1
            continue

        # Catalog structure is authoritative; live lifecycle and worker/PR state
        # are preserved. Registration must never move review/done work backwards.
        lifecycle_keys = {
            "status",
            "owner",
            "reviewer",
            "last_update",
            "branch",
            "pr_number",
            "pr_url",
            "run_id",
            "terminal_outcome",
            "review_receipt",
            "implementation_receipt",
        }
        lifecycle = {key: copy.deepcopy(existing.get(key)) for key in lifecycle_keys if key in existing}
        existing.clear()
        existing.update(incoming)
        existing.update(lifecycle)
        if unlock_wave_1 and task_id in WAVE_1 and str(existing.get("status") or "") == "blocked":
            existing["status"] = "todo"
            existing["blocked_reason"] = None
            existing["next"] = "Wave 1 unlocked after DPF-GOV-001 completion."
        preserved += 1

    catalog_ids = {str(task["id"]) for task in catalog["definitions"]}
    live_catalog_tasks = [
        task for task in current_tasks
        if isinstance(task, dict) and str(task.get("id") or "") in catalog_ids
    ]
    if len(live_catalog_tasks) != EXPECTED_TASK_COUNT:
        raise CatalogRegistrationError(
            f"live catalog registration count is {len(live_catalog_tasks)}, expected {EXPECTED_TASK_COUNT}"
        )

    output["emgi_catalog"] = {
        "version": CATALOG_VERSION,
        "authority_repository": AUTHORITY_REPOSITORY,
        "authority_sha": catalog["authority_sha"],
        "manifest": catalog["manifest_path"],
        "registered_task_count": EXPECTED_TASK_COUNT,
        "registration_mode": "staged",
        "wave_0": WAVE_0,
        "wave_1": WAVE_1,
        "full_dispatch": "blocked_by_dependency_and_wave_gates",
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    receipt = {
        "schema": "pantheon.emgi-live-registration-receipt.v1",
        "status": "PASS",
        "catalog_version": CATALOG_VERSION,
        "authority_sha": catalog["authority_sha"],
        "task_count": EXPECTED_TASK_COUNT,
        "newly_registered": registered,
        "existing_preserved": preserved,
        "wave_1_unlocked": bool(unlock_wave_1),
        "status_counts": {},
    }
    for task in live_catalog_tasks:
        status = str(task.get("status") or "unknown")
        receipt["status_counts"][status] = receipt["status_counts"].get(status, 0) + 1
    return output, receipt


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Register the EMGI v0.4.1 catalog in live supervisor state")
    parser.add_argument(
        "--manifest-root",
        type=Path,
        default=Path("../oday-data-platform/docs/design/emgi/v0.4.1/tasks"),
    )
    parser.add_argument("--status-file", type=Path, default=Path("ai-status.json"))
    parser.add_argument("--authority-ref", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--unlock-wave-1", action="store_true")
    parser.add_argument(
        "--archive-tasks-dir",
        type=Path,
        default=Path("ai-task-archive/tasks"),
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=Path(".orchestrator/evidence/emgi-v0.4.1-live-registration.json"),
    )
    args = parser.parse_args()

    catalog = load_catalog(args.manifest_root, authority_ref=args.authority_ref)
    status_payload = _load_json(args.status_file) if args.status_file.exists() else {"tasks": []}
    archived_restored = hydrate_archived_catalog_tasks(
        status_payload,
        catalog,
        args.archive_tasks_dir,
    )
    updated, receipt = register_catalog(
        status_payload,
        catalog,
        unlock_wave_1=args.unlock_wave_1,
    )
    receipt["archived_terminal_restored"] = archived_restored
    print(json.dumps(receipt, indent=2, sort_keys=True))
    if not args.apply:
        return 0

    backup = args.status_file.with_suffix(
        args.status_file.suffix + ".pre-emgi-v0.4.1.bak"
    )
    if args.status_file.exists() and not backup.exists():
        backup.write_bytes(args.status_file.read_bytes())
    _atomic_json(args.status_file, updated)
    _atomic_json(args.receipt, receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
