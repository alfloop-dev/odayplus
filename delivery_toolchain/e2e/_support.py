"""Shared mechanics for the release-governance command suite.

The individual commands keep their policy and report formatting.  This module
owns the repeated file/module/GitHub transport mechanics so they cannot drift
into subtly different failure and retry behavior.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def issue_number_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def load_github_issue(
    issue_number: str,
    *,
    root: Path,
    fields: str,
) -> dict[str, Any]:
    raw = subprocess.check_output(
        ["gh", "issue", "view", issue_number, "--json", fields],
        cwd=root,
        text=True,
    )
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"GitHub issue #{issue_number} returned a non-object payload")
    return payload


def run_gh_with_retry(
    args: list[str],
    *,
    root: Path,
    attempts: int = 3,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    for attempt in range(1, attempts + 1):
        result = runner(args, cwd=root, check=False)
        if result.returncode == 0:
            return
        if attempt == attempts:
            raise subprocess.CalledProcessError(result.returncode, args)
        sleeper(2 * attempt)
