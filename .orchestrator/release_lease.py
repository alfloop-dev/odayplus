#!/usr/bin/env python3
"""Supervisor-side issuer for signed, durable release leases.

The Supervisor is the only party that can mint a release lease, because it is
the only party that holds the Ed25519 private key. This module is where that
power is constrained: a lease is issued only when the task's declared
dependencies are actually `done` and the staged gate registry already admits the
requested environment for the exact candidate SHA and manifest recorded at that
SHA (EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN §6.2).

The binding is derived, never asserted. `candidate_sha`, `manifest_digest`, and
`release_id` come from the committed registry and manifest, so a caller cannot
ask for a lease that names a release the repository does not describe. The only
things a caller chooses are which task, which environment, which action, and how
long the lease lives.

Verification lives in `delivery_toolchain/release/release_lease.py` and the
admission entrypoint in `delivery_toolchain/release/check_runtime_admission.py`.
Deployment workflows get the public key and the verifier; they never get this
module's key, so possessing the ability to check a lease never confers the
ability to issue one.

Production is refused here by the gate stage rather than by a special case:
`staging-verified` is the stage whose admission target is `production`, and no
release has reached it. There is no production blue-green path yet, and a lease
that pretended otherwise would be the same shape-only fiction this replaces.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_archive import archive_task_path, task_status  # noqa: E402

from delivery_toolchain.release.check_runtime_admission import (  # noqa: E402
    registry_admission_errors,
)
from delivery_toolchain.release.release_lease import (  # noqa: E402
    DEFAULT_ACTION,
    DEFAULT_TTL_SECONDS,
    SHA_PATTERN,
    STATE_ISSUED,
    TARGET_ENVIRONMENTS,
    Ed25519PrivateKey,
    LeaseError,
    LeaseIssuanceError,
    LeaseStateStore,
    build_lease,
    load_private_key,
)
from delivery_toolchain.release.release_manifest import load_manifest  # noqa: E402

DEFAULT_STATUS = ROOT / "ai-status.json"
DEFAULT_REGISTRY = ROOT / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json"
DEFAULT_MANIFEST = ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json"
DEFAULT_STATE_DIR = ROOT / ".orchestrator/state/release-leases"
DEFAULT_ARCHIVE_DIR = ROOT / "ai-task-archive/tasks"

DONE_STATUS = "done"
ISSUER_NAME = ".orchestrator/release_lease.py"


def dependency_errors(
    status: Any,
    task_id: str,
    *,
    archive_dir: Path | None = None,
) -> list[str]:
    """Return why *task_id*'s dependency graph does not authorise a release yet.

    Every id in `depends_on` must resolve to a task that is `done`. A dependency
    that cannot be resolved at all is a blocker, not a pass: an unknown
    dependency is exactly the case where the Supervisor does not know whether
    the prerequisite work happened.
    """

    if not isinstance(status, dict):
        return ["status document must be a JSON object"]
    tasks = status.get("tasks")
    if not isinstance(tasks, list):
        return ["status document has no tasks list"]

    index = {
        str(task.get("id")).upper(): task
        for task in tasks
        if isinstance(task, dict) and task.get("id")
    }
    task = index.get(task_id.upper())
    if task is None:
        return [f"task {task_id} is not present in the Supervisor status document"]

    depends_on = task.get("depends_on")
    if depends_on is None:
        depends_on = []
    if not isinstance(depends_on, list):
        return [f"task {task_id} has a malformed depends_on field"]

    errors: list[str] = []
    for raw in depends_on:
        dependency_id = str(raw).strip()
        if not dependency_id:
            errors.append(f"task {task_id} declares an empty dependency id")
            continue
        state = _dependency_state(index, dependency_id, archive_dir)
        if state is None:
            errors.append(
                f"dependency {dependency_id} of {task_id} cannot be resolved in the status "
                "document or the task archive; refusing to assume it is complete"
            )
        elif state != DONE_STATUS:
            errors.append(
                f"dependency {dependency_id} of {task_id} is {state!r}, expected {DONE_STATUS!r}"
            )
    return errors


def _dependency_state(
    index: dict[str, Any], dependency_id: str, archive_dir: Path | None
) -> str | None:
    task = index.get(dependency_id.upper())
    if isinstance(task, dict):
        return str(task.get("status"))
    if archive_dir is None:
        return None
    # Name the snapshot with the archiver's own rule instead of a second
    # guess at it. `archive_task_path` keeps the task id verbatim, so the
    # files on disk are upper-case; lower-casing the name here missed every
    # one of them on a case-sensitive filesystem, and a miss is reported as
    # "cannot be resolved" -- a finished prerequisite blocked its own release
    # while `scripts/orchestrator/check_task_dependency_resolvability.py`
    # called the very same graph fully resolvable.
    path = archive_dir / archive_task_path(dependency_id).name
    try:
        archived = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A missing or malformed snapshot must not satisfy a dependency.
        return None
    if not isinstance(archived, dict):
        return None
    # The archive envelope carries the terminal state in `terminal_status`
    # and preserves the task under `task`. It has no top-level `status`, so
    # reading one resolved every archived dependency to the string "None".
    snapshot = archived.get("task")
    if isinstance(snapshot, dict):
        status = task_status(snapshot)
        if status:
            return status
    terminal_status = str(archived.get("terminal_status") or "").strip().lower()
    return terminal_status or None


def issuance_errors(
    *,
    status: Any,
    registry: Any,
    manifest: dict[str, Any] | None,
    manifest_errors: list[str],
    task_id: str,
    target_environment: str,
    release_sha: str | None = None,
    archive_dir: Path | None = None,
    root: Path = ROOT,
) -> list[str]:
    """Return every precondition that blocks issuing a lease for this request."""

    errors: list[str] = []
    if target_environment not in TARGET_ENVIRONMENTS:
        errors.append(
            f"target_environment {target_environment!r} must be one of {list(TARGET_ENVIRONMENTS)}"
        )
    errors.extend(dependency_errors(status, task_id, archive_dir=archive_dir))
    errors.extend(manifest_errors)

    candidate_sha = None
    if isinstance(registry, dict):
        candidate = (registry.get("release") or {}).get("candidate_sha")
        if isinstance(candidate, str) and SHA_PATTERN.fullmatch(candidate):
            candidate_sha = candidate

    expected_digest = manifest.get("manifest_digest") if manifest and not manifest_errors else None
    errors.extend(
        registry_admission_errors(
            registry,
            release_sha=release_sha or candidate_sha or "",
            environment=target_environment,
            expected_manifest_digest=expected_digest,
            allowed_environments=TARGET_ENVIRONMENTS,
            root=root,
        )
    )
    return errors


def issue_release_lease(
    *,
    task_id: str,
    target_environment: str,
    status: Any,
    registry: Any,
    manifest: dict[str, Any] | None,
    manifest_errors: list[str],
    private_key: Ed25519PrivateKey,
    state_store: LeaseStateStore,
    allowed_action: str = DEFAULT_ACTION,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    release_sha: str | None = None,
    archive_dir: Path | None = None,
    issued_at: datetime | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Mint and durably record one lease, or raise `LeaseIssuanceError`.

    The lease is recorded `issued` before it is returned. If recording fails the
    lease is never handed out, so a lease can only exist in a caller's hands
    after the Supervisor has committed to being able to see it consumed.
    """

    errors = issuance_errors(
        status=status,
        registry=registry,
        manifest=manifest,
        manifest_errors=manifest_errors,
        task_id=task_id,
        target_environment=target_environment,
        release_sha=release_sha,
        archive_dir=archive_dir,
        root=root,
    )
    if errors:
        raise LeaseIssuanceError(errors)

    if manifest is None:
        # Unreachable while issuance_errors reports an unloadable manifest, but
        # a silent None here would mint a lease with no artifact identity.
        raise LeaseIssuanceError(["manifest could not be loaded; refusing to issue a lease"])

    lease = build_lease(
        task_id=task_id,
        release_id=str(manifest["release_id"]),
        candidate_sha=str(manifest["candidate_sha"]),
        manifest_digest=str(manifest["manifest_digest"]),
        target_environment=target_environment,
        allowed_action=allowed_action,
        private_key=private_key,
        ttl_seconds=ttl_seconds,
        issued_at=issued_at,
    )
    state_store.record_issued(lease, issued_by=ISSUER_NAME)
    return lease


def _load_json(path: Path, label: str) -> tuple[Any, list[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"cannot read {label} at {path}: {exc}"]


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-dir",
        type=str,
        default=str(DEFAULT_STATE_DIR),
        help="Supervisor lease state directory or gs://bucket/prefix.",
    )
    parser.add_argument(
        "--private-key-file",
        type=Path,
        default=None,
        help="Ed25519 signing key; defaults to the ODP_RELEASE_LEASE_PRIVATE_KEY env var.",
    )


def _cmd_issue(args: argparse.Namespace) -> int:
    status, status_errors = _load_json(args.status, "Supervisor status document")
    registry, registry_errors = _load_json(args.registry, "release gate registry")
    blockers = status_errors + registry_errors
    if blockers:
        return _print_blocked(blockers)

    candidate_sha = None
    if isinstance(registry, dict):
        candidate = (registry.get("release") or {}).get("candidate_sha")
        if isinstance(candidate, str) and SHA_PATTERN.fullmatch(candidate):
            candidate_sha = candidate
    manifest, manifest_errors = load_manifest(
        args.manifest, expected_candidate_sha=candidate_sha
    )

    try:
        private_key = load_private_key(key_path=args.private_key_file)
        state_store = LeaseStateStore(args.state_dir)
        lease = issue_release_lease(
            task_id=args.task_id,
            target_environment=args.environment,
            status=status,
            registry=registry,
            manifest=manifest,
            manifest_errors=manifest_errors,
            private_key=private_key,
            state_store=state_store,
            allowed_action=args.action,
            ttl_seconds=args.ttl_seconds,
            release_sha=args.release_sha,
            archive_dir=args.archive_dir,
        )
    except LeaseIssuanceError as exc:
        return _print_blocked(exc.errors)
    except LeaseError as exc:
        return _print_blocked([str(exc)])

    document = json.dumps(lease, indent=2, ensure_ascii=False) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document, encoding="utf-8")
        print(
            f"issued {lease['lease_id']} for {args.task_id} -> {args.environment} "
            f"(expires {lease['expires_at']}); written to {args.output}"
        )
    else:
        print(document, end="")
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    try:
        state_store = LeaseStateStore(args.state_dir, require_existing=True)
        record = state_store.get(args.lease_id)
        if record is None:
            return _print_blocked([f"lease {args.lease_id} is not in the durable state store"])
        if not isinstance(record.get("lease"), dict):
            return _print_blocked(
                [f"lease state record for {args.lease_id} carries no lease; refusing to act on it"]
            )
        state_store.revoke(record["lease"], reason=args.reason)
    except LeaseError as exc:
        return _print_blocked([str(exc)])
    print(f"revoked {args.lease_id}: {args.reason}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        state_store = LeaseStateStore(args.state_dir, require_existing=True)
        record = state_store.get(args.lease_id)
    except LeaseError as exc:
        return _print_blocked([str(exc)])
    if record is None:
        return _print_blocked([f"lease {args.lease_id} is not in the durable state store"])
    # The stored lease carries the signature and nonce; a status query has no
    # business handing those back out.
    stored = record.get("lease")
    summary = {key: value for key, value in record.items() if key != "lease"}
    if isinstance(stored, dict):
        summary["target_environment"] = stored.get("target_environment")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if record.get("state") == STATE_ISSUED else 1


def _print_blocked(errors: list[str]) -> int:
    print("release lease issuance blocked:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser("issue", help="Issue a signed lease when preconditions hold.")
    issue.add_argument("--task-id", required=True)
    issue.add_argument("--environment", required=True, choices=list(TARGET_ENVIRONMENTS))
    issue.add_argument("--action", default=DEFAULT_ACTION)
    issue.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    issue.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    issue.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    issue.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    issue.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR)
    issue.add_argument(
        "--release-sha",
        default=None,
        help="Deployed SHA when it differs from the candidate SHA (evidence-only descendant).",
    )
    issue.add_argument("--output", type=Path, default=None)
    _add_common_arguments(issue)
    issue.set_defaults(handler=_cmd_issue)

    revoke = sub.add_parser("revoke", help="Revoke an issued lease so it can never be consumed.")
    revoke.add_argument("lease_id")
    revoke.add_argument("--reason", required=True)
    _add_common_arguments(revoke)
    revoke.set_defaults(handler=_cmd_revoke)

    show = sub.add_parser("show", help="Print lease lifecycle state without the credential.")
    show.add_argument("lease_id")
    _add_common_arguments(show)
    show.set_defaults(handler=_cmd_show)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
