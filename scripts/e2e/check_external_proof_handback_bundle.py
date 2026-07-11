#!/usr/bin/env python3
"""Validate a complete external-proof handback bundle for release closeout.

`check_external_proof_handback_artifact.py` validates each completed handback.
This checker validates the set: Product Validation should use it when #132-#138
are all claimed complete so a release cannot move forward with a missing,
duplicated, or mixed-release handback.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
TEMPLATE_PATH = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json"
ARTIFACT_CHECKER_PATH = ROOT / "scripts/e2e/check_external_proof_handback_artifact.py"


def load_artifact_checker():
    spec = importlib.util.spec_from_file_location("check_external_proof_handback_artifact", ARTIFACT_CHECKER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load artifact checker from {ARTIFACT_CHECKER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_handback_paths(paths: list[Path]) -> list[Path]:
    handback_paths: list[Path] = []
    for path in paths:
        if path.is_dir():
            handback_paths.extend(sorted(child for child in path.glob("*.json") if child.is_file()))
        else:
            handback_paths.append(path)
    return handback_paths


def validate_bundle(paths: list[Path], *, expected_sha: str | None) -> list[str]:
    errors: list[str] = []
    artifact_checker = load_artifact_checker()
    queue = load_json(QUEUE_PATH)
    template = load_json(TEMPLATE_PATH)
    queue_entries = {entry["task_id"]: entry for entry in queue.get("queue", [])}
    template_entries = {entry["task_id"]: entry for entry in template.get("tasks", [])}
    expected_task_ids = set(queue_entries)

    seen_task_paths: dict[str, list[Path]] = {}
    release_shas: dict[str, list[Path]] = {}
    handback_paths = find_handback_paths(paths)
    if not handback_paths:
        return ["no handback JSON files were provided"]

    for path in handback_paths:
        try:
            handback = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path}: unable to load JSON: {exc}")
            continue

        task_id = str(handback.get("task_id", ""))
        if task_id:
            seen_task_paths.setdefault(task_id, []).append(path)
        release_sha = str(handback.get("release_head_ref_oid", ""))
        if release_sha:
            release_shas.setdefault(release_sha, []).append(path)

        for error in artifact_checker.validate_handback(
            handback,
            queue_entries,
            template_entries,
            expected_sha=expected_sha,
        ):
            errors.append(f"{path}: {error}")

    seen_task_ids = set(seen_task_paths)
    missing = expected_task_ids - seen_task_ids
    extra = seen_task_ids - expected_task_ids
    if missing:
        errors.append(f"missing handbacks for tasks: {sorted(missing)}")
    if extra:
        errors.append(f"handbacks include unknown tasks: {sorted(extra)}")
    for task_id, task_paths in sorted(seen_task_paths.items()):
        if len(task_paths) > 1:
            joined = ", ".join(str(path) for path in task_paths)
            errors.append(f"duplicate handbacks for {task_id}: {joined}")

    if expected_sha is None and len(release_shas) > 1:
        errors.append(f"handbacks cite multiple release_head_ref_oid values: {sorted(release_shas)}")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handbacks", nargs="+", type=Path, help="Handback JSON files or directories containing JSON files.")
    parser.add_argument("--expected-sha", help="Require every handback to cite this PR #82 headRefOid.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors = validate_bundle(args.handbacks, expected_sha=args.expected_sha)
    if errors:
        print("External proof handback bundle check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("External proof handback bundle checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
