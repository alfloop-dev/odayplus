#!/usr/bin/env python3
"""Fail when config.schema.json declares a setting no code ever reads.

A key that exists only in config is a promise the system does not keep. The
branch workflow is the worked example: `branch_workflow.task_pr.target_branch`
declared "dev" from 2026-06-27, nothing read it, and ReviewBus went on opening
task PRs against the repository default until someone noticed by eye months
later. `branch_workflow.drift_alarms` -- the mechanism meant to catch exactly
that kind of drift -- was dead for the same reason, so nothing raised a hand.

This guard makes "declared but not wired" a CI failure at the moment the key is
added, instead of an archaeology exercise later. Unimplemented settings do not
belong in runtime configuration: add them when code actually consumes them.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONFIG_SCHEMA_PATH = ROOT / ".orchestrator" / "config.schema.json"
SOURCE_DIRS = (
    ROOT / ".orchestrator",
    ROOT / "delivery_toolchain",
    ROOT / "development_platform",
    ROOT / "scripts",
)
SOURCE_SUFFIXES = (".py", ".sh")

# Containers whose children are data -- agent ids, provider ids, label lists --
# rather than settings. Their own name still has to be wired; their keys do not.
DATA_CONTAINERS = frozenset(
    {
        "agents",
        "account_pools",
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


def schema_config_shape(node: Any) -> Any:
    """Project JSON Schema properties into the key tree audited below."""
    if not isinstance(node, dict):
        return None
    properties = node.get("properties")
    if not isinstance(properties, dict):
        return None
    return {str(key): schema_config_shape(value) for key, value in properties.items()}


def load_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
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
            sources[str(path)] = path.read_text(encoding="utf-8", errors="ignore")
    return sources


def _quoted(key: str) -> re.Pattern[str]:
    return re.compile(r"[\"']" + re.escape(key) + r"[\"']")


def _mentioned(name: str) -> re.Pattern[str]:
    return re.compile(r"\b" + re.escape(name) + r"\b")


def is_wired(path: tuple[str, ...], sources: dict[str, str]) -> bool:
    """Does some source file read this setting?

    The leaf name alone is not enough, because distinct settings share leaf
    names: wiring branch_workflow.task_pr.target_branch would otherwise mark
    branch_workflow.promote.target_branch as read too, and the guard would wave
    through a genuinely dead key. So the parent must appear in the same file.

    The two names are matched differently on purpose. A leaf is read through its
    literal string, so it must appear quoted. A parent is usually already bound
    to a variable by the time the leaf is read -- `schema["status_field"]` never
    quotes `schema` -- so requiring quotes there would report live settings as
    dead. Matching the parent as a bare word covers both forms.

    Within a file the check stays generous: any mention counts, in any
    construct. The guard catches keys nothing references at all; it does not
    police how they are read.
    """
    key = path[-1]
    parent = path[-2] if len(path) > 1 else None
    for text in sources.values():
        if not _quoted(key).search(text):
            continue
        if parent is None or _mentioned(parent).search(text):
            return True
    return False


def audit(config: dict[str, Any], sources: dict[str, str]) -> list[str]:
    """Return every declared setting that no production source reads."""
    unwired: list[str] = []
    for path in iter_setting_paths(config):
        dotted = ".".join(path)
        if is_wired(path, sources):
            continue
        unwired.append(dotted)

    return unwired


def main() -> int:
    schema = json.loads(CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"))
    config = schema_config_shape(schema)
    sources = load_sources()
    unexpected = audit(config, sources)

    if unexpected:
        print("Config keys declared but never read by any code:", file=sys.stderr)
        for key in unexpected:
            print(f"  {key}", file=sys.stderr)
        print(
            "\nWire the setting or delete it; runtime config has no dead-key exceptions.",
            file=sys.stderr,
        )
    if unexpected:
        return 1

    print(f"All {len(iter_setting_paths(config))} config keys are read by production code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
