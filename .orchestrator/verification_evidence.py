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
_SUCCESS_TAIL_TOKENS = frozenset({"true", ":", "echo", "printf"})
_PIPEFAIL_RE = re.compile(r"set\s+-o\s+pipefail\b|set\s+-[a-zA-Z]*o[a-zA-Z]*\s+pipefail\b")
_DISABLE_ERREXIT_RE = re.compile(r"set\s+\+[a-zA-Z]*e[a-zA-Z]*\b")

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
    pipefail = bool(_PIPEFAIL_RE.search(text))
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
            if tail_head in _SUCCESS_TAIL_TOKENS or (tail_head == "exit" and _exit_status_arg(tail) == 0):
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

_SELECTION_VALUE_FLAGS = frozenset({"-k", "-m", "--deselect", "--ignore", "--rootdir", "-p"})
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
        tokens = [tok for tok in _tokenize(text) if tok not in _CONTROL_OPERATORS]
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

    audit_payload = receipt.get("command_audit")
    if isinstance(audit_payload, dict) and not audit_payload.get("ok"):
        codes = ", ".join(str(item) for item in (audit_payload.get("violations") or []))
        problems.append(f"command failed the exit-code masking audit: {codes}")

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
) -> list[dict[str, Any]]:
    sha = str(head_sha or "").strip().lower()
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
    uses_shell = any(tok in _CONTROL_OPERATORS for tok in tokens)
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
        return {
            "audit": audit,
            "executed": False,
            "exit_code": None,
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
