#!/usr/bin/env python3
"""Validate a Pantheon task commit message.

Contract source: `.orchestrator/skills/task-closeout-finalization.md`
§ Commit Requirements, which declares these rules "enforced by
`.githooks/commit-msg`". This module is the implementation behind both that
hook and ``delivery_toolchain/git/worker_commit.py``.

Rules:

  * Subject is ``<TASK-ID>: <imperative summary>`` and at most 72 characters
    (the skill recommends <= 70; 72 is the hard limit callers such as
    ``.orchestrator/auto_commit_archive.py`` are written against).
  * Required trailers: ``LLM-Agent``, ``Task-ID``, ``Reviewer``.
  * ``Reviewer`` must differ from ``LLM-Agent``.
  * ``Cross-Dir: yes`` is required when the commit spans more than 3
    top-level directories (only checked when the file list is supplied).
  * Subjects with a maintenance prefix (``Merge ``, ``Revert ``, ``promote:``,
    ``hotfix:``, ``publish:``, ``OPS-GIT-WORKFLOW-``, ``OPS-GIT-REDESIGN-``,
    ``OPS-DOC-``, ``OPS-REBASE-``) skip the whole check.

Usage:

    python3 delivery_toolchain/git/check_commit_trailers.py <message-file> \\
      [--task-id ODP-X-001] [--files a.py b.py]

Exit codes: 0 = valid, 1 = invalid, 2 = usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SUBJECT_MAX = 72
CROSS_DIR_THRESHOLD = 3

SKIP_SUBJECT_PREFIXES = (
    "Merge ",
    "Revert ",
    "promote:",
    "hotfix:",
    "publish:",
    "OPS-GIT-WORKFLOW-",
    "OPS-GIT-REDESIGN-",
    "OPS-DOC-",
    "OPS-REBASE-",
)

REQUIRED_TRAILERS = ("LLM-Agent", "Task-ID", "Reviewer")

_TRAILER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):[ \t]*(.*)$")


def strip_comments(message: str) -> str:
    """Drop git's ``#`` comment lines and the scissors section."""
    lines: list[str] = []
    for line in message.split("\n"):
        if line.startswith("# ------------------------ >8"):
            break
        if line.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def subject_of(message: str) -> str:
    for line in strip_comments(message).split("\n"):
        if line.strip():
            return line.strip()
    return ""


def parse_trailers(message: str) -> dict[str, str]:
    """Collect ``Key: value`` pairs from the message body.

    Scans the whole body rather than only the last paragraph: workers
    routinely append a ``Verified:`` line or an extra note after the trailer
    block, and rejecting an otherwise correct commit for that is noise.
    """
    trailers: dict[str, str] = {}
    for line in strip_comments(message).split("\n")[1:]:
        match = _TRAILER_RE.match(line.rstrip())
        if match:
            key, value = match.group(1), match.group(2).strip()
            if value:
                trailers[key.lower()] = value
    return trailers


def top_level_dirs(files: list[str]) -> set[str]:
    dirs: set[str] = set()
    for raw in files:
        path = str(raw).strip().replace("\\", "/").lstrip("./")
        if not path:
            continue
        dirs.add(path.split("/", 1)[0] if "/" in path else path)
    return dirs


def validate_message(
    message: str,
    *,
    task_id: str | None = None,
    files: list[str] | None = None,
) -> list[str]:
    """Return a list of rule violations; empty means the message is valid."""
    errors: list[str] = []
    body = strip_comments(message)
    subject = subject_of(body)

    if not subject:
        return ["commit message is empty"]
    if subject.startswith(SKIP_SUBJECT_PREFIXES):
        return []

    if len(subject) > SUBJECT_MAX:
        errors.append(f"subject is {len(subject)} chars, limit is {SUBJECT_MAX}: {subject!r}")
    if ":" not in subject:
        errors.append(f"subject must be '<TASK-ID>: <summary>', got {subject!r}")

    trailers = parse_trailers(body)
    for name in REQUIRED_TRAILERS:
        if not trailers.get(name.lower()):
            errors.append(f"missing required trailer '{name}:'")

    owner = trailers.get("llm-agent", "")
    reviewer = trailers.get("reviewer", "")
    if owner and reviewer and owner.strip().lower() == reviewer.strip().lower():
        errors.append(f"Reviewer must differ from LLM-Agent (both are {owner!r})")

    trailer_task = trailers.get("task-id", "")
    if task_id:
        if trailer_task and trailer_task.strip().lower() != task_id.strip().lower():
            errors.append(f"Task-ID trailer {trailer_task!r} does not match {task_id!r}")
        if not subject.lower().startswith(task_id.strip().lower()):
            errors.append(f"subject must start with the task id {task_id!r}, got {subject!r}")
    elif trailer_task and not subject.lower().startswith(trailer_task.strip().lower()):
        errors.append(f"subject must start with the Task-ID trailer {trailer_task!r}, got {subject!r}")

    if files:
        dirs = top_level_dirs(files)
        if len(dirs) > CROSS_DIR_THRESHOLD and trailers.get("cross-dir", "").lower() != "yes":
            errors.append(
                f"commit spans {len(dirs)} top-level directories "
                f"({', '.join(sorted(dirs))}); add 'Cross-Dir: yes' or narrow the scope"
            )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message_file", help="Path to the commit message file (git passes $1 here).")
    parser.add_argument("--task-id", help="Expected task id; also checked against the subject.")
    parser.add_argument("--files", nargs="*", default=None, help="Committed paths, for the Cross-Dir rule.")
    args = parser.parse_args(argv)

    path = Path(args.message_file)
    try:
        message = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"check_commit_trailers: cannot read {path}: {exc}", file=sys.stderr)
        return 2

    errors = validate_message(message, task_id=args.task_id, files=args.files)
    if errors:
        print("check_commit_trailers: commit message rejected:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print(
            "\nRequired shape (see .orchestrator/skills/task-closeout-finalization.md):\n"
            "  <TASK-ID>: <imperative summary>\n\n"
            "  <body>\n\n"
            "  LLM-Agent: <owner>\n"
            "  Task-ID: <task-id>\n"
            "  Reviewer: <reviewer, != owner>",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
