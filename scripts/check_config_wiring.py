#!/usr/bin/env python3
"""Fail when config.example.json declares a setting no code ever reads.

A key that exists only in config is a promise the system does not keep. The
branch workflow is the worked example: `branch_workflow.task_pr.target_branch`
declared "dev" from 2026-06-27, nothing read it, and ReviewBus went on opening
task PRs against the repository default until someone noticed by eye months
later. `branch_workflow.drift_alarms` -- the mechanism meant to catch exactly
that kind of drift -- was dead for the same reason, so nothing raised a hand.

This guard makes "declared but not wired" a CI failure at the moment the key is
added, instead of an archaeology exercise later. Keys that are genuinely not
implemented yet live in the allowlist with a reason, which doubles as the
backlog of unkept promises.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / ".orchestrator" / "config.example.json"
ALLOWLIST_PATH = ROOT / ".orchestrator" / "config_wiring_allowlist.json"
SOURCE_DIRS = (ROOT / ".orchestrator", ROOT / "scripts")
SOURCE_SUFFIXES = (".py", ".sh")

# Containers whose children are data -- agent ids, provider ids, label lists --
# rather than settings. Their own name still has to be wired; their keys do not.
DATA_CONTAINERS = frozenset(
    {
        "agents",
        "providers",
        "reviewers",
        "owner_fallbacks",
        "reviewer_fallbacks",
        "labels",
        "paths",
        "env",
        "models",
        "commands",
        "status_targets",
    }
)


def iter_setting_paths(node: Any, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            path = prefix + (key,)
            paths.append(path)
            if key in DATA_CONTAINERS:
                continue
            paths.extend(iter_setting_paths(value, path))
    return paths


def load_sources() -> str:
    chunks: list[str] = []
    for directory in SOURCE_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
                continue
            # Tests may name a key while asserting it is absent, which would
            # mask a genuinely unwired setting.
            if path.name.startswith("test_"):
                continue
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def is_wired(key: str, sources: str) -> bool:
    # Deliberately generous: any quoted mention counts, in any construct. The
    # guard is here to catch keys nothing references at all, not to police how
    # they are read.
    return re.search(r"[\"']" + re.escape(key) + r"[\"']", sources) is not None


def load_allowlist() -> dict[str, str]:
    if not ALLOWLIST_PATH.exists():
        return {}
    payload = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    entries = payload.get("unwired") or {}
    return {str(k): str(v) for k, v in entries.items()}


def audit(config: dict[str, Any], sources: str, allowlist: dict[str, str]) -> tuple[list[str], list[str]]:
    """Return (unwired keys not allowlisted, allowlist entries now wired)."""
    unwired: list[str] = []
    for path in iter_setting_paths(config):
        dotted = ".".join(path)
        if is_wired(path[-1], sources):
            continue
        unwired.append(dotted)

    unexpected = [key for key in unwired if key not in allowlist]
    stale = [key for key in allowlist if key not in set(unwired)]
    return unexpected, stale


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-allowlist",
        action="store_true",
        help="rewrite the allowlist from the current state (review the diff before committing)",
    )
    args = parser.parse_args()

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    sources = load_sources()
    allowlist = load_allowlist()

    if args.write_allowlist:
        unwired = [
            ".".join(path)
            for path in iter_setting_paths(config)
            if not is_wired(path[-1], sources)
        ]
        payload = {
            "_comment": (
                "Settings declared in config.example.json that no code reads yet. "
                "Each entry needs a reason. Wire the setting and delete the entry, "
                "or delete the setting. See scripts/check_config_wiring.py."
            ),
            "unwired": {key: allowlist.get(key, "TODO: state why this is not wired yet") for key in unwired},
        }
        ALLOWLIST_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {len(unwired)} entries to {ALLOWLIST_PATH.relative_to(ROOT)}")
        return 0

    unexpected, stale = audit(config, sources, allowlist)

    if unexpected:
        print("Config keys declared but never read by any code:", file=sys.stderr)
        for key in unexpected:
            print(f"  {key}", file=sys.stderr)
        print(
            "\nWire the setting, delete it, or add it to "
            f"{ALLOWLIST_PATH.relative_to(ROOT)} with a reason.",
            file=sys.stderr,
        )
    if stale:
        print("\nAllowlist entries that are now wired -- delete them:", file=sys.stderr)
        for key in stale:
            print(f"  {key}", file=sys.stderr)

    if unexpected or stale:
        return 1

    print(f"All {len(iter_setting_paths(config))} config keys are read by code or allowlisted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
