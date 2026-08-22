#!/usr/bin/env python3
"""Remove retired worker-worktree switches from a live supervisor config.

The Worker Manager now always leases an isolated registered worktree, resolves
the base branch from the repository registry, and reuses a task's existing
branch.  These old keys must therefore disappear together; retaining one
creates the impression that it still controls runtime behaviour.

Run with ``--write`` only after reviewing the default dry-run output.  The
script does not fetch, reset, or otherwise modify any Git checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

RETIRED_KEYS = frozenset(
    {
        "enabled",
        "base_ref",
        "reuse_existing",
        "recover_clean_diverged_worktrees",
        "execution_reasons",
    }
)


def load_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return value


def write_atomically(path: Path, payload: dict) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.next-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(".orchestrator/config.json"),
        help="live config path (default: .orchestrator/config.json)",
    )
    parser.add_argument("--write", action="store_true", help="apply the reviewed migration")
    args = parser.parse_args(argv)

    path = args.config.expanduser().resolve()
    config = load_object(path)
    settings = config.get("worker_worktrees")
    if not isinstance(settings, dict):
        print("worker_worktrees is absent or not an object; nothing to migrate")
        return 0
    removed = sorted(key for key in RETIRED_KEYS if key in settings)
    if not removed:
        print("worker_worktrees already uses registry-base configuration")
        return 0
    print(f"{path}: remove worker_worktrees keys: {', '.join(removed)}")
    if not args.write:
        print("dry run; re-run with --write after reviewing this exact target")
        return 0
    for key in removed:
        settings.pop(key, None)
    write_atomically(path, config)
    print("migration applied; restart only through immutable runtime rollout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
