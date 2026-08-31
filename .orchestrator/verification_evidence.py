#!/usr/bin/env python3
"""Verification evidence policy: command audit, receipts, and rerun control.

This module is the single place that decides whether a declared task
verification command may be trusted, what its receipt must contain, and
whether re-measuring an already-measured head SHA is allowed.

It deliberately owns no scheduling. The supervisor keeps its existing
dispatch loop and its existing ``.orchestrator/evidence`` receipt directory;
this module only audits commands, classifies real exit codes, and answers
"is this run allowed and what does it prove".

Three failure modes motivated it:

1. A verification command whose exit code is swallowed by a pipeline or a
   ``|| true`` tail reports success no matter what the tests did.
2. A receipt that does not name the head SHA, the exact command, the real
   exit code, the duration, and the test selection cannot be replayed, so a
   later reader cannot tell what was actually measured.
3. A run killed by a signal is not a result. Treating it as a pass is wrong,
   and quietly re-running a wider selection to "make sure" turns one
   interrupted targeted run into an unbounded suite loop.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# --- outcomes ---------------------------------------------------------------

OUTCOME_PASSED = "passed"
OUTCOME_FAILED = "failed"
OUTCOME_INTERRUPTED = "interrupted"
OUTCOME_NO_TESTS = "no_tests_collected"
OUTCOME_REJECTED = "rejected"

#: Only this outcome may be read as "the declared verification passed".
PASSING_OUTCOMES = frozenset({OUTCOME_PASSED})

#: Outcomes that produced a real measurement of the head SHA, so a further
#: run against the same SHA and selection is a duplicate rather than a resume.
SETTLED_OUTCOMES = frozenset({OUTCOME_PASSED, OUTCOME_FAILED, OUTCOME_NO_TESTS})

# --- audit violation codes --------------------------------------------------

V_EMPTY = "empty_command"
V_UNPARSABLE = "unparsable_command"
V_MASKED_PIPELINE = "masked_pipeline"
V_FORCED_SUCCESS = "forced_success"
V_TRAILING_COMMAND = "trailing_command_mask"
V_DISABLED_ERREXIT = "disabled_errexit"
V_BACKGROUNDED = "backgrounded_command"

_VIOLATION_HINTS = {
    V_EMPTY: "command is empty",
    V_UNPARSABLE: "command could not be tokenized as a shell command",
    V_MASKED_PIPELINE: (
        "pipeline reports only the last stage's exit code; "
        "declare `set -o pipefail` or drop the pipe"
    ),
    V_FORCED_SUCCESS: "`|| true`-style tail forces a zero exit code",
    V_TRAILING_COMMAND: "`;` tail replaces the runner's exit code with the tail command's",
    V_DISABLED_ERREXIT: "`set +e` disables errexit for the verification command",
    V_BACKGROUNDED: "`&` backgrounds the runner, so the recorded exit code is not the test result",
}

# --- shell tokenization -----------------------------------------------------

_CONTROL_OPERATORS = frozenset({"|", "|&", "||", "&&", ";", ";;", "&"})

#: A token that only moves a stream around: ``>``, ``>>``, ``2>&1``'s ``>&``,
#: ``&>``, ``<``. It never changes the exit code, but it is also not an
#: argument -- it must reach a shell rather than the runner's argv.
_REDIRECTION_RE = re.compile(r"^[<>&]+$")

_SUCCESS_TAIL_TOKENS = frozenset({"true", ":", "echo", "printf"})
_DISABLE_ERREXIT_RE = re.compile(r"set\s+\+[a-zA-Z]*e[a-zA-Z]*\b")


def _segment_enables_pipefail(segment: list[str]) -> bool:
    """True when a segment is a standalone ``set -o pipefail`` command.

    Only unquoted tokens in a command segment (no pipes, no control operators)
    can enable the option.  A ``-k "set -o pipefail"`` test-selection string
    tokenizes as a single quoted token and never reaches here unquoted.
    """
    stripped = [_strip_quotes(tok) for tok in segment]
    if "set" not in stripped:
        return False
    idx = stripped.index("set")
    rest = stripped[idx + 1:]
    # `set -o pipefail` or `set -eo pipefail` (the option name follows -o)
    for i, token in enumerate(rest):
        if token == "-o" and i + 1 < len(rest) and rest[i + 1] == "pipefail":
            return True
        # combined form: -eo, -xo etc. where `o` is followed by pipefail arg
        if token.startswith("-") and "o" in token[1:] and token.endswith("o"):
            if i + 1 < len(rest) and rest[i + 1] == "pipefail":
                return True
    return False


def _pipefail_before_pipe(segments: list[list[str]], operators: list[str]) -> bool:
    """True only when ``set -o pipefail`` precedes the first pipe operator.

    A ``set -o pipefail`` that appears *after* a pipe in the same compound
    command does not protect that pipe's exit code.
    """
    first_pipe = None
    for i, op in enumerate(operators):
        if op in {"|", "|&"}:
            first_pipe = i
            break
    if first_pipe is None:
        return False  # no pipe, so pipefail is irrelevant
    for seg_idx in range(first_pipe + 1):  # segments before and including the pipe's left
        if _segment_enables_pipefail(segments[seg_idx]):
            return True
    return False

#: Tokens that mark the segment which actually produces the verification result.
RUNNER_TOKENS = frozenset(
    {
        "pytest",
        "py.test",
        "unittest",
        "tox",
        "nox",
        "ruff",
        "mypy",
        "vitest",
        "jest",
        "npm",
        "pnpm",
        "yarn",
        "go",
        "cargo",
        "make",
    }
)


def _tokenize(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=False, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return token[1:-1]
    return token


def _is_runner_token(token: str) -> bool:
    return os.path.basename(_strip_quotes(token)) in RUNNER_TOKENS


def _is_redirection_token(token: str) -> bool:
    """True for a bare redirection operator, not for a quoted ``'>'`` argument."""
    return bool(_REDIRECTION_RE.match(token)) and token not in _CONTROL_OPERATORS


def strip_redirections(tokens: list[str]) -> list[str]:
    """Drop redirection operators together with their fd prefix and target.

    ``pytest tests/unit > reports/run.log`` selects ``tests/unit``; the log
    path is where output went, not a test that was run. Leaving it in would
    put it in the selection fingerprint and make two runs of the same tests
    look like two different selections.
    """
    kept: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _is_redirection_token(token):
            # `2>&1` tokenizes as `2`, `>&`, `1`: the fd prefix already landed
            # in `kept`, and the target is the token after the operator.
            if kept and _strip_quotes(kept[-1]).isdigit():
                kept.pop()
            index += 2
            continue
        kept.append(token)
        index += 1
    return kept


def command_key(command: str) -> str:
    """Normalize a command so a declaration and a receipt compare exactly.

    Only whitespace is normalized. ``pytest -q tests`` and ``pytest tests``
    are deliberately different keys: they run the same files but they are not
    the same command, and a receipt for one does not prove the other.
    """
    text = str(command or "").strip()
    if not text:
        return ""
    try:
        return " ".join(_tokenize(text))
    except ValueError:
        return " ".join(text.split())


def _split_segments(tokens: list[str]) -> tuple[list[list[str]], list[str]]:
    """Split a token list on control operators.

    Redirections (``>``, ``2>&1``) stay inside their segment because they move
    output around without changing the exit code we care about.
    """
    segments: list[list[str]] = []
    operators: list[str] = []
    current: list[str] = []
    for token in tokens:
        if token in _CONTROL_OPERATORS:
            segments.append(current)
            operators.append(token)
            current = []
        else:
            current.append(token)
    segments.append(current)
    return segments, operators


def detect_runner(command: str) -> str | None:
    """Return the basename of the test runner the command drives, if known."""
    try:
        tokens = _tokenize(command)
    except ValueError:
        return None
    for token in tokens:
        if token in _CONTROL_OPERATORS:
            continue
        if _is_runner_token(token):
            return os.path.basename(_strip_quotes(token))
    return None


# --- command audit ----------------------------------------------------------


@dataclass(frozen=True)
class CommandAudit:
    """Verdict on whether a command's exit code can be trusted."""

    command: str
    ok: bool
    violations: tuple[str, ...] = ()
    details: tuple[str, ...] = ()
    runner: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "ok": self.ok,
            "violations": list(self.violations),
            "details": list(self.details),
            "runner": self.runner,
        }


def audit_command(command: str) -> CommandAudit:
    """Reject verification commands whose real exit code cannot survive.

    The rule is narrow on purpose: a command is rejected only when the shell
    would report a status that is not the runner's own status.
    """
    text = str(command or "").strip()
    if not text:
        return CommandAudit(command=text, ok=False, violations=(V_EMPTY,), details=(_VIOLATION_HINTS[V_EMPTY],))

    try:
        tokens = _tokenize(text)
    except ValueError as exc:
        return CommandAudit(
            command=text,
            ok=False,
            violations=(V_UNPARSABLE,),
            details=(f"{_VIOLATION_HINTS[V_UNPARSABLE]}: {exc}",),
        )

    segments, operators = _split_segments(tokens)
    pipefail = _pipefail_before_pipe(segments, operators)
    runner_indexes = [idx for idx, seg in enumerate(segments) if any(_is_runner_token(tok) for tok in seg)]
    first_runner = runner_indexes[0] if runner_indexes else None

    violations: list[str] = []
    details: list[str] = []

    def flag(code: str, detail: str | None = None) -> None:
        if code not in violations:
            violations.append(code)
            details.append(detail or _VIOLATION_HINTS.get(code, code))

    if _DISABLE_ERREXIT_RE.search(text):
        flag(V_DISABLED_ERREXIT)

    for op_index, operator in enumerate(operators):
        tail = segments[op_index + 1] if op_index + 1 < len(segments) else []
        tail_head = _strip_quotes(tail[0]) if tail else ""

        if operator in {"|", "|&"}:
            if not pipefail:
                flag(V_MASKED_PIPELINE)
            continue

        if operator == "&":
            # A trailing `&` backgrounds everything before it.
            if first_runner is not None and first_runner <= op_index:
                flag(V_BACKGROUNDED)
            continue

        if operator == "||":
            # `||` after the runner replaces the runner's failing exit code
            # with whatever the tail produces. Only `exit N` where N != 0
            # preserves the failure signal; everything else masks it.
            if first_runner is not None and first_runner <= op_index:
                if tail_head == "exit":
                    exit_arg = _exit_status_arg(tail)
                    if exit_arg is not None and exit_arg == 0:
                        flag(V_FORCED_SUCCESS)
                    elif exit_arg is None and _strip_quotes(tail[1]) not in {"$?", "${?}"} if len(tail) > 1 else True:
                        # bare `exit` without argument exits 0 by spec
                        flag(V_FORCED_SUCCESS)
                    # `|| exit 1`, `|| exit $?` keep the failure visible
                else:
                    flag(V_FORCED_SUCCESS)
            continue

        if operator in {";", ";;"}:
            # `;` after the runner replaces its status unless the tail
            # explicitly re-raises it.
            if first_runner is not None and first_runner <= op_index and tail and not _reraises_status(tail):
                flag(V_TRAILING_COMMAND)
            continue

        # `&&` short-circuits, so a failing runner still surfaces.

    return CommandAudit(
        command=text,
        ok=not violations,
        violations=tuple(violations),
        details=tuple(details),
        runner=detect_runner(text),
    )


def _exit_status_arg(segment: list[str]) -> int | None:
    for token in segment[1:]:
        stripped = _strip_quotes(token)
        if stripped.isdigit():
            return int(stripped)
    return None


def _reraises_status(segment: list[str]) -> bool:
    """True for tails like `exit $?` that deliberately propagate the status."""
    head = _strip_quotes(segment[0]) if segment else ""
    if head != "exit":
        return False
    return any(_strip_quotes(tok) in {"$?", "${?}"} for tok in segment[1:])


def audit_commands(commands: Any) -> list[CommandAudit]:
    if not commands:
        return []
    if isinstance(commands, str):
        commands = [commands]
    return [audit_command(str(item)) for item in commands]


# --- test selection ---------------------------------------------------------

_SELECTION_VALUE_FLAGS = frozenset({"-k", "-m", "--deselect", "--ignore"})
_SCOPE_TARGETED = "targeted"
_SCOPE_SUITE = "suite"


def _looks_like_selection_path(token: str) -> bool:
    stripped = _strip_quotes(token)
    if not stripped or stripped.startswith("-"):
        return False
    return "::" in stripped or stripped.endswith(".py") or "/" in stripped


def extract_selection(command: str) -> dict[str, Any]:
    """Describe which tests a command selects.

    Only tokens after the runner count, so ``uv run --with pytest pytest x.py``
    reports ``x.py`` rather than the launcher's own flags.
    """
    text = str(command or "").strip()
    try:
        tokens = [tok for tok in strip_redirections(_tokenize(text)) if tok not in _CONTROL_OPERATORS]
    except ValueError:
        tokens = []

    start = 0
    for idx, token in enumerate(tokens):
        if _is_runner_token(token):
            start = idx + 1
    relevant = tokens[start:]

    items: list[str] = []
    index = 0
    while index < len(relevant):
        token = relevant[index]
        bare = _strip_quotes(token)
        if token in _SELECTION_VALUE_FLAGS and index + 1 < len(relevant):
            items.append(f"{token}={_strip_quotes(relevant[index + 1])}")
            index += 2
            continue
        if bare.startswith(("-k=", "-m=", "--deselect=", "--ignore=")):
            items.append(bare)
            index += 1
            continue
        if _looks_like_selection_path(token):
            items.append(bare)
        index += 1

    normalized = sorted(set(items))
    payload = {
        "scope": _SCOPE_TARGETED if normalized else _SCOPE_SUITE,
        "items": normalized,
    }
    payload["fingerprint"] = selection_fingerprint(payload)
    return payload


def selection_fingerprint(selection: dict[str, Any] | None) -> str:
    """Stable id for a selection, so the same tests re-run is detectable."""
    payload = {
        "scope": str((selection or {}).get("scope") or _SCOPE_SUITE),
        "items": sorted(str(item) for item in ((selection or {}).get("items") or [])),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def selection_is_broader(candidate: dict[str, Any] | None, baseline: dict[str, Any] | None) -> bool:
    """True when ``candidate`` would run strictly more than ``baseline``."""
    cand = candidate or {}
    base = baseline or {}
    cand_scope = str(cand.get("scope") or _SCOPE_SUITE)
    base_scope = str(base.get("scope") or _SCOPE_SUITE)
    if base_scope == _SCOPE_SUITE:
        # A whole-suite baseline already covers everything; nothing widens it.
        return False
    if cand_scope == _SCOPE_SUITE:
        return True
    cand_items = {str(item) for item in (cand.get("items") or [])}
    base_items = {str(item) for item in (base.get("items") or [])}
    return base_items.issubset(cand_items) and bool(cand_items - base_items)


# --- exit code classification -----------------------------------------------

# pytest's documented exit codes; 2 and 5 are the ones that must never read
# as "the tests were run and were fine".
PYTEST_INTERRUPTED = 2
PYTEST_INTERNAL_ERROR = 3
PYTEST_USAGE_ERROR = 4
PYTEST_NO_TESTS_COLLECTED = 5

_SIGNAL_NAMES = {
    1: "SIGHUP",
    2: "SIGINT",
    3: "SIGQUIT",
    6: "SIGABRT",
    9: "SIGKILL",
    11: "SIGSEGV",
    13: "SIGPIPE",
    15: "SIGTERM",
}


def signal_from_exit_code(exit_code: int | None) -> str | None:
    """Return the signal name behind a process exit code, if it was signalled."""
    if exit_code is None:
        return None
    if exit_code < 0:
        number = -exit_code
    elif 128 < exit_code < 192:
        number = exit_code - 128
    else:
        return None
    return _SIGNAL_NAMES.get(number, f"SIG{number}")


def classify_outcome(exit_code: int | None, *, runner: str | None = None, timed_out: bool = False) -> str:
    """Map a raw exit code to an outcome that never over-reports success."""
    if timed_out or exit_code is None:
        return OUTCOME_INTERRUPTED
    code = int(exit_code)
    if signal_from_exit_code(code):
        return OUTCOME_INTERRUPTED
    if code == 0:
        return OUTCOME_PASSED
    if (runner or "").startswith("py"):
        if code == PYTEST_INTERRUPTED:
            return OUTCOME_INTERRUPTED
        if code == PYTEST_NO_TESTS_COLLECTED:
            return OUTCOME_NO_TESTS
    return OUTCOME_FAILED


def outcome_is_pass(outcome: str | None) -> bool:
    return str(outcome or "") in PASSING_OUTCOMES


# --- receipts ---------------------------------------------------------------

REQUIRED_RECEIPT_FIELDS = (
    "task_id",
    "head_sha",
    "command",
    "exit_code",
    "duration_seconds",
    "selection",
    "outcome",
)

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_receipt(
    *,
    task_id: str,
    head_sha: str,
    command: str,
    exit_code: int | None,
    duration_seconds: float | None,
    selection: dict[str, Any] | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    retry_reason: str | None = None,
    attempt: int = 1,
    kind: str = "baseline",
    agent: str | None = None,
    run_id: str | None = None,
    produced_by: str | None = None,
    audit: CommandAudit | None = None,
    timed_out: bool = False,
    output_tail: str | None = None,
) -> dict[str, Any]:
    """Bind a run to the head SHA, command, exit code, duration and selection.

    Any of those five missing makes the receipt unreplayable, so this raises
    rather than emitting a half-receipt a later reader would have to trust.
    """
    task = str(task_id or "").strip()
    sha = str(head_sha or "").strip().lower()
    cmd = str(command or "").strip()
    if not task:
        raise ValueError("verification receipt requires task_id")
    if not _SHA_RE.match(sha):
        raise ValueError(f"verification receipt requires a git head SHA, got {head_sha!r}")
    if not cmd:
        raise ValueError("verification receipt requires the exact command")
    if duration_seconds is None:
        raise ValueError("verification receipt requires duration_seconds")

    command_audit = audit or audit_command(cmd)
    sel = selection or extract_selection(cmd)
    sel = dict(sel)
    sel.setdefault("scope", _SCOPE_SUITE)
    sel.setdefault("items", [])
    sel["fingerprint"] = sel.get("fingerprint") or selection_fingerprint(sel)

    runner = command_audit.runner or detect_runner(cmd)
    if command_audit.ok:
        outcome = classify_outcome(exit_code, runner=runner, timed_out=timed_out)
    else:
        outcome = OUTCOME_REJECTED

    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "verification_receipt",
        "task_id": task,
        "head_sha": sha,
        "command": cmd,
        "command_audit": command_audit.as_dict(),
        "runner": runner,
        "selection": sel,
        "exit_code": None if exit_code is None else int(exit_code),
        "signal": signal_from_exit_code(exit_code),
        "timed_out": bool(timed_out),
        "outcome": outcome,
        "passed": outcome_is_pass(outcome),
        "duration_seconds": round(float(duration_seconds), 3),
        "started_at": started_at or _utc_now(),
        "finished_at": finished_at or _utc_now(),
        "recorded_at": _utc_now(),
        "run_kind": kind,
        "attempt": int(attempt),
        "retry_reason": (retry_reason or "").strip() or None,
        "agent": agent,
        "run_id": run_id,
        "produced_by": produced_by,
    }
    if output_tail:
        receipt["output_tail"] = output_tail
    receipt["receipt_id"] = receipt_id(receipt)
    return receipt


def receipt_id(receipt: dict[str, Any]) -> str:
    payload = {
        "task_id": receipt.get("task_id"),
        "head_sha": receipt.get("head_sha"),
        "command": receipt.get("command"),
        "started_at": receipt.get("started_at"),
        "attempt": receipt.get("attempt"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def validate_receipt(receipt: dict[str, Any] | None) -> list[str]:
    """Return the reasons a receipt may not be accepted as evidence."""
    problems: list[str] = []
    if not isinstance(receipt, dict):
        return ["receipt is not an object"]

    for field_name in REQUIRED_RECEIPT_FIELDS:
        if field_name not in receipt:
            problems.append(f"missing field: {field_name}")

    sha = str(receipt.get("head_sha") or "").strip().lower()
    if sha and not _SHA_RE.match(sha):
        problems.append(f"head_sha is not a git SHA: {receipt.get('head_sha')!r}")

    selection = receipt.get("selection")
    if selection is not None and not isinstance(selection, dict):
        problems.append("selection must be an object")
    elif isinstance(selection, dict) and not selection.get("fingerprint"):
        problems.append("selection is missing a fingerprint")

    duration = receipt.get("duration_seconds")
    if duration is not None and (not isinstance(duration, (int, float)) or duration < 0):
        problems.append("duration_seconds must be a non-negative number")

    # The audit is what makes the recorded exit code mean anything, so a
    # receipt without one proves nothing and is not merely under-annotated.
    # Its `ok` flag is re-derived from the recorded command rather than
    # trusted: a hand-written receipt can claim a clean audit for a command
    # that masks its own status, and that is exactly the forgery this gate
    # exists to refuse.
    command_text = str(receipt.get("command") or "").strip()
    audit_payload = receipt.get("command_audit")
    if not isinstance(audit_payload, dict):
        problems.append("missing field: command_audit (a receipt must carry the audit that cleared its command)")
    elif not audit_payload.get("ok"):
        codes = ", ".join(str(item) for item in (audit_payload.get("violations") or []))
        problems.append(f"command failed the exit-code masking audit: {codes}")
    elif command_text:
        recomputed = audit_command(command_text)
        if not recomputed.ok:
            problems.append(
                "command_audit claims ok, but re-auditing the recorded command rejects it "
                f"({', '.join(recomputed.violations)})"
            )
        elif command_key(audit_payload.get("command")) != command_key(command_text):
            problems.append("command_audit records a different command than the receipt")

    outcome = str(receipt.get("outcome") or "")
    if outcome not in {OUTCOME_PASSED, OUTCOME_FAILED, OUTCOME_INTERRUPTED, OUTCOME_NO_TESTS, OUTCOME_REJECTED}:
        problems.append(f"unknown outcome: {outcome!r}")

    if receipt.get("passed") and not outcome_is_pass(outcome):
        problems.append(f"receipt claims passed with outcome {outcome!r}")

    exit_code = receipt.get("exit_code")
    if outcome == OUTCOME_PASSED and exit_code != 0:
        problems.append(f"outcome {OUTCOME_PASSED!r} requires exit_code 0, got {exit_code!r}")

    return problems


# --- duplicate baseline control ---------------------------------------------

MIN_RETRY_REASON_CHARS = 12

_EMPTY_RETRY_REASONS = frozenset(
    {"retry", "rerun", "again", "n/a", "na", "none", "-", "test", "flaky", "just in case"}
)

KIND_BASELINE = "baseline"
KIND_RETRY = "retry"
KIND_RESUME = "resume"
KIND_DUPLICATE = "duplicate"


@dataclass(frozen=True)
class BaselineDecision:
    """Whether a verification run against this head SHA may proceed."""

    allowed: bool
    kind: str
    reason: str
    attempt: int = 1
    prior_receipt_id: str | None = None
    prior_outcome: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "kind": self.kind,
            "reason": self.reason,
            "attempt": self.attempt,
            "prior_receipt_id": self.prior_receipt_id,
            "prior_outcome": self.prior_outcome,
        }


def retry_reason_is_explicit(retry_reason: str | None) -> bool:
    """A retry reason must say something a later reader can act on."""
    text = str(retry_reason or "").strip()
    if len(text) < MIN_RETRY_REASON_CHARS:
        return False
    return text.strip(" .").casefold() not in _EMPTY_RETRY_REASONS


def matching_receipts(
    receipts: Any,
    *,
    head_sha: str,
    selection_id: str,
    task_id: str | None = None,
    command: str | None = None,
) -> list[dict[str, Any]]:
    """Receipts measured against this head and selection, oldest first.

    ``command`` narrows the match to receipts for that exact command. Rerun
    control leaves it unset on purpose -- two commands that select the same
    tests are the same measurement for dedupe purposes -- while the finalize
    gate always sets it, because there a receipt only proves the command it
    actually ran.
    """
    sha = str(head_sha or "").strip().lower()
    wanted_command = command_key(command) if command is not None else None
    matches = []
    for receipt in receipts or []:
        if not isinstance(receipt, dict):
            continue
        if str(receipt.get("head_sha") or "").strip().lower() != sha:
            continue
        selection = receipt.get("selection") or {}
        if str(selection.get("fingerprint") or "") != str(selection_id):
            continue
        if task_id and str(receipt.get("task_id") or "") != str(task_id):
            continue
        if wanted_command is not None and command_key(receipt.get("command")) != wanted_command:
            continue
        matches.append(receipt)
    matches.sort(key=lambda item: (str(item.get("started_at") or ""), int(item.get("attempt") or 0)))
    return matches


def evaluate_baseline_request(
    receipts: Any,
    *,
    head_sha: str,
    selection_id: str,
    task_id: str | None = None,
    retry_reason: str | None = None,
) -> BaselineDecision:
    """Refuse a second baseline for a SHA that already has a settled result.

    A prior run that was interrupted never produced a baseline, so repeating
    it is a resume and needs no retry reason. A prior run that passed or
    failed did produce one, so repeating it needs an explicit reason.
    """
    prior = matching_receipts(receipts, head_sha=head_sha, selection_id=selection_id, task_id=task_id)
    if not prior:
        return BaselineDecision(allowed=True, kind=KIND_BASELINE, reason="no prior receipt for this head SHA and selection")

    settled = [item for item in prior if str(item.get("outcome") or "") in SETTLED_OUTCOMES]
    last = prior[-1]
    attempt = len(prior) + 1

    if not settled:
        return BaselineDecision(
            allowed=True,
            kind=KIND_RESUME,
            reason=f"prior attempt ended {last.get('outcome')!r} without producing a baseline",
            attempt=attempt,
            prior_receipt_id=last.get("receipt_id"),
            prior_outcome=last.get("outcome"),
        )

    newest_settled = settled[-1]
    if not retry_reason_is_explicit(retry_reason):
        return BaselineDecision(
            allowed=False,
            kind=KIND_DUPLICATE,
            reason=(
                f"head SHA {str(head_sha)[:12]} already has a {newest_settled.get('outcome')} receipt for this "
                f"selection; supply an explicit retry reason (>= {MIN_RETRY_REASON_CHARS} chars) to re-run"
            ),
            attempt=attempt,
            prior_receipt_id=newest_settled.get("receipt_id"),
            prior_outcome=newest_settled.get("outcome"),
        )

    return BaselineDecision(
        allowed=True,
        kind=KIND_RETRY,
        reason=f"explicit retry reason accepted: {str(retry_reason).strip()}",
        attempt=attempt,
        prior_receipt_id=newest_settled.get("receipt_id"),
        prior_outcome=newest_settled.get("outcome"),
    )


# --- rerun scope control ----------------------------------------------------


@dataclass(frozen=True)
class RerunPlan:
    """The only selection a follow-up run is allowed to use."""

    allowed: bool
    selection: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    escalated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "selection": self.selection,
            "reason": self.reason,
            "escalated": self.escalated,
        }


def plan_rerun(
    prior_receipt: dict[str, Any] | None,
    *,
    requested_selection: dict[str, Any] | None = None,
    retry_reason: str | None = None,
) -> RerunPlan:
    """Decide the selection a follow-up run may use.

    After an interruption the only defensible next step is to run the same
    selection again. Widening it turns a signal into a suite-wide rerun, which
    is exactly the failure loop this policy exists to stop.
    """
    prior = prior_receipt or {}
    prior_selection = dict(prior.get("selection") or {})
    prior_selection.setdefault("scope", _SCOPE_SUITE)
    prior_selection.setdefault("items", [])
    prior_selection["fingerprint"] = prior_selection.get("fingerprint") or selection_fingerprint(prior_selection)
    outcome = str(prior.get("outcome") or "")

    if not prior:
        return RerunPlan(allowed=True, selection=dict(requested_selection or {}), reason="no prior receipt")

    if outcome == OUTCOME_INTERRUPTED:
        if requested_selection and selection_is_broader(requested_selection, prior_selection):
            return RerunPlan(
                allowed=False,
                selection=prior_selection,
                reason=(
                    "interrupted run must be repeated with the same selection; "
                    "it is not evidence that a wider rerun is needed"
                ),
                escalated=True,
            )
        return RerunPlan(
            allowed=True,
            selection=prior_selection,
            reason="repeating the interrupted selection unchanged",
        )

    if outcome in SETTLED_OUTCOMES and not retry_reason_is_explicit(retry_reason):
        return RerunPlan(
            allowed=False,
            selection=prior_selection,
            reason=f"prior run settled as {outcome!r}; an explicit retry reason is required",
        )

    return RerunPlan(
        allowed=True,
        selection=dict(requested_selection or prior_selection),
        reason=f"retry accepted after {outcome!r}",
        escalated=bool(requested_selection and selection_is_broader(requested_selection, prior_selection)),
    )


# --- receipt store ----------------------------------------------------------

RECEIPT_FILE_PREFIX = "verification"


def receipt_slug(task_id: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(task_id or "task").lower()).strip("_")
    return slug or "task"


def receipt_filename(receipt: dict[str, Any]) -> str:
    slug = receipt_slug(receipt.get("task_id"))
    ident = str(receipt.get("receipt_id") or receipt_id(receipt))
    return f"{RECEIPT_FILE_PREFIX}-{slug}-{ident}.json"


def write_receipt(directory: Path | str, receipt: dict[str, Any]) -> Path:
    """Persist a receipt, refusing anything that would not survive validation.

    A receipt that cannot be trusted must not exist on disk: a later reader
    finding a file named like evidence will read it as evidence.
    """
    problems = validate_receipt(receipt)
    if problems:
        raise ValueError("refusing to persist an invalid verification receipt: " + "; ".join(problems))
    path = Path(directory) / receipt_filename(receipt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_receipts(directory: Path | str, *, task_id: str | None = None) -> list[dict[str, Any]]:
    """Return recorded receipts, oldest first; unreadable files are skipped."""
    root = Path(directory)
    if not root.is_dir():
        return []
    slug = receipt_slug(task_id) if task_id else "*"
    receipts: list[dict[str, Any]] = []
    for path in sorted(root.glob(f"{RECEIPT_FILE_PREFIX}-{slug}-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("kind") == "verification_receipt":
            receipts.append(payload)
    receipts.sort(key=lambda item: (str(item.get("started_at") or ""), int(item.get("attempt") or 0)))
    return receipts


# --- declaration requirement ------------------------------------------------

# The marker a task carries to say it owes a verification declaration. It is
# stamped once, when the task is created, and read here.
VERIFICATION_REQUIRED_FIELD = "verification_required"

_MARKER_TRUE = frozenset({"1", "true", "yes", "on"})
_MARKER_FALSE = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class DeclarationRequirement:
    """Whether a task must declare verification commands, and on what basis."""

    required: bool
    legacy: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"required": self.required, "legacy": self.legacy, "reason": self.reason}


def _coerce_marker(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _MARKER_TRUE:
            return True
        if normalized in _MARKER_FALSE:
            return False
    return None


def declaration_requirement(task: Any) -> DeclarationRequirement:
    """Read the verification-declaration obligation off a board task.

    The marker is the whole policy. Present and true means the task must name
    the commands that prove it before it can publish; present and false means
    it was created after the field existed and judged not to need one.

    An absent marker is the legacy fallback: the task predates the field, so no
    obligation is retrofitted onto it. Keying the requirement to the stamp
    rather than to the task class is deliberate -- it confines the new rule to
    tasks created under it and leaves every task already on the board
    publishable, so turning this on cannot strand in-flight work.

    A marker that is present but not a boolean is a corrupted declaration, not
    an absent one, so it fails closed.
    """
    if not isinstance(task, dict):
        return DeclarationRequirement(False, False, "no board task to carry a verification requirement")
    if VERIFICATION_REQUIRED_FIELD not in task:
        return DeclarationRequirement(
            False,
            True,
            f"legacy task: created before {VERIFICATION_REQUIRED_FIELD} existed, so no declaration is required of it",
        )
    raw = task.get(VERIFICATION_REQUIRED_FIELD)
    marker = _coerce_marker(raw)
    if marker is None:
        return DeclarationRequirement(
            True,
            False,
            f"{VERIFICATION_REQUIRED_FIELD}={raw!r} is not a boolean; the requirement is treated as in force",
        )
    if marker:
        return DeclarationRequirement(True, False, f"task is marked {VERIFICATION_REQUIRED_FIELD}=true")
    return DeclarationRequirement(False, False, f"task is marked {VERIFICATION_REQUIRED_FIELD}=false")


# --- finalize gate ----------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    """Whether a task's declared verification is actually proven at this head."""

    ok: bool
    problems: tuple[str, ...] = ()
    satisfied: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "problems": list(self.problems), "satisfied": list(self.satisfied)}


def receipt_proves(receipt: dict[str, Any] | None) -> bool:
    """A receipt only proves a command passed if it is valid and exited zero."""
    if not isinstance(receipt, dict):
        return False
    if validate_receipt(receipt):
        return False
    return bool(receipt.get("passed")) and receipt.get("exit_code") == 0


def evaluate_finalize_gate(
    *,
    commands: Any,
    head_sha: str,
    receipts: Any,
    task_id: str | None = None,
    requirement: DeclarationRequirement | None = None,
) -> GateResult:
    """Fail closed when a declared verification command is not proven at HEAD.

    A task that declares no verification commands normally has nothing to
    prove and passes: the obligation follows the declaration, not the task.
    ``requirement`` is the one exception. A task whose board entry is marked
    ``verification_required`` owes a declaration, so declaring nothing is
    itself the violation and is refused here -- otherwise the marker would be
    a note in a file that no gate ever reads.
    """
    declared = [str(item).strip() for item in (commands or []) if str(item).strip()]
    if not declared:
        if requirement is not None and requirement.required:
            return GateResult(
                ok=False,
                problems=(
                    "task declares no verification commands but owes one "
                    f"({requirement.reason}); name the commands that prove this change in the "
                    "task's `verification` field",
                ),
            )
        return GateResult(ok=True)

    sha = str(head_sha or "").strip().lower()
    if not _SHA_RE.match(sha):
        return GateResult(ok=False, problems=(f"cannot resolve a head SHA to verify against, got {head_sha!r}",))

    problems: list[str] = []
    satisfied: list[str] = []

    for command in declared:
        audit = audit_command(command)
        if not audit.ok:
            problems.append(
                f"declared command is rejected by the exit-code masking policy "
                f"({', '.join(audit.violations)}): {command}"
            )
            continue

        selection = extract_selection(command)
        selection_id = str(selection.get("fingerprint") or "")
        matches = matching_receipts(
            receipts,
            head_sha=sha,
            selection_id=selection_id,
            task_id=task_id,
            command=command,
        )
        if not matches:
            # Same tests is not the same command. Report the near miss rather
            # than a bare "no receipt", because a receipt for `pytest -q x` is
            # exactly what a reader would otherwise point at as proof of
            # `pytest x`, and the difference can be the whole result.
            near = matching_receipts(
                receipts,
                head_sha=sha,
                selection_id=selection_id,
                task_id=task_id,
            )
            if near:
                others = ", ".join(sorted({str(item.get("command") or "") for item in near}))
                problems.append(
                    f"no verification receipt at head {sha[:12]} for: {command} "
                    f"(the receipts at this head select the same tests but ran: {others})"
                )
            else:
                problems.append(f"no verification receipt at head {sha[:12]} for: {command}")
            continue

        proving = [item for item in matches if receipt_proves(item)]
        if not proving:
            newest = matches[-1]
            # A receipt can fail to prove its command for two different
            # reasons: the run did not pass, or the receipt is not valid
            # evidence. Reporting only the outcome makes the second case read
            # as a contradiction -- "'passed' (exit 0), which does not prove"
            # -- so name the defect when there is one.
            defects = validate_receipt(newest)
            detail = f"; the receipt is not valid evidence: {defects[0]}" if defects else ""
            problems.append(
                f"newest receipt at head {sha[:12]} is {newest.get('outcome')!r} "
                f"(exit {newest.get('exit_code')!r}), which does not prove: {command}{detail}"
            )
            continue

        satisfied.append(command)

    return GateResult(ok=not problems, problems=tuple(problems), satisfied=tuple(satisfied))


# --- execution --------------------------------------------------------------


def run_verification_command(
    command: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    output_tail_chars: int = 2000,
) -> dict[str, Any]:
    """Run a verification command and report its real exit code.

    Audited-out commands are never executed: running them would produce a
    number that does not mean what the receipt would claim it means.
    """
    audit = audit_command(command)
    started_at = _utc_now()
    if not audit.ok:
        return {
            "audit": audit,
            "executed": False,
            "exit_code": None,
            "duration_seconds": 0.0,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "timed_out": False,
            "output_tail": "",
        }

    text = audit.command
    try:
        tokens = _tokenize(text)
    except ValueError:
        tokens = []
    # Redirections are audited as harmless because they do not touch the exit
    # code -- but only a shell can honour them. Splitting `pytest -q > log
    # 2>&1` into argv would hand `>`, `log` and `2>&1` to pytest as arguments,
    # so the command that ran would not be the command the receipt records.
    uses_shell = any(tok in _CONTROL_OPERATORS or _is_redirection_token(tok) for tok in tokens)
    argv: list[str] | str
    if uses_shell:
        argv = ["bash", "-c", text]
        use_shell_binary = True
    else:
        argv = shlex.split(text)
        use_shell_binary = False

    start = time.monotonic()
    timed_out = False
    output = ""
    try:
        completed = subprocess.run(  # noqa: S603 - command comes from the audited task verification list
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        exit_code: int | None = completed.returncode
        output = (completed.stdout or "") + (completed.stderr or "")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        output = _decode_stream(exc.stdout) + _decode_stream(exc.stderr)
    except FileNotFoundError as exc:
        # 127 is the shell's own "command not found" status. Reporting it keeps
        # the receipt honest: the command did not work, and it is not a pass and
        # not an interruption that a resume would fix.
        return {
            "audit": audit,
            "executed": False,
            "exit_code": 127,
            "duration_seconds": round(time.monotonic() - start, 3),
            "started_at": started_at,
            "finished_at": _utc_now(),
            "timed_out": False,
            "output_tail": f"command not found: {exc}",
        }

    return {
        "audit": audit,
        "executed": True,
        "shell": use_shell_binary,
        "exit_code": exit_code,
        "duration_seconds": round(time.monotonic() - start, 3),
        "started_at": started_at,
        "finished_at": _utc_now(),
        "timed_out": timed_out,
        "output_tail": output[-output_tail_chars:] if output else "",
    }


def _decode_stream(stream: Any) -> str:
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return str(stream)


def verify_and_build_receipt(
    command: str,
    *,
    task_id: str,
    head_sha: str,
    receipts: Any = None,
    retry_reason: str | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    agent: str | None = None,
    run_id: str | None = None,
    produced_by: str | None = None,
) -> tuple[BaselineDecision, dict[str, Any] | None]:
    """Audit, dedupe, run, and receipt a single verification command."""
    selection = extract_selection(command)
    decision = evaluate_baseline_request(
        receipts or [],
        head_sha=head_sha,
        selection_id=str(selection.get("fingerprint") or ""),
        task_id=task_id,
        retry_reason=retry_reason,
    )
    if not decision.allowed:
        return decision, None

    result = run_verification_command(command, cwd=cwd, env=env, timeout=timeout)
    receipt = build_receipt(
        task_id=task_id,
        head_sha=head_sha,
        command=command,
        exit_code=result["exit_code"],
        duration_seconds=result["duration_seconds"],
        selection=selection,
        started_at=result["started_at"],
        finished_at=result["finished_at"],
        retry_reason=retry_reason,
        attempt=decision.attempt,
        kind=decision.kind,
        agent=agent,
        run_id=run_id,
        produced_by=produced_by,
        audit=result["audit"],
        timed_out=result["timed_out"],
        output_tail=result.get("output_tail"),
    )
    return decision, receipt


if __name__ == "__main__":
    import sys

    print(
        "This module is shared by the orchestrator scripts and is not meant to be run directly.",
        file=sys.stderr,
    )
    raise SystemExit(1)
