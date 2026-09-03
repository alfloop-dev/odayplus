#!/usr/bin/env python3
"""Hold set-valued requirements to their own member lists and disposition gate.

Five of the fifteen gaps found in the ODP-FR verification are the same story:
a requirement enumerates N things and the implementation did M of them.

    ODP-FR-NET-002    eight hard-constraint classes    one modelled
    ODP-FR-SITE-001   five demand components           three
    ODP-FR-LH-003     five release modes               four
    ODP-FR-INTV-006   four intervention responses      three
    ODP-FR-SHARED-001 six job states                   five

None of the five broke a rule, because there was no rule to break.
``ODP-SA-06``'s Trigger and Acceptance columns are the same boilerplate
repeated seventy-one times, so nothing anywhere says whether the eighth
constraint class was required or optional. An implementer could not know, and a
reviewer had nothing to point at.

Writing acceptance criteria for all 112 requirements would be 112 units of work
producing a document that drifts. This is the narrow version: a requirement that
*lists* N items can carry those N items in machine-readable form, each one
naming where it is satisfied, and every un-implemented MUST requirement member
carrying an auditable, machine-verifiable disposition.

WHAT IT ENFORCES
----------------

1. Member List Completeness:
   * ``member_count`` is required and must match ``len(members)`` exactly.
   * Member names must be non-empty and unique within the requirement.

2. Implementation Evidence:
   * ``satisfied`` -- the evidence reference must resolve to a symbol that exists.
     A member whose implementation is deleted or renamed fails here.

3. Structured Dispositions & Lifecycle Gates:
   * ``absent`` -- is an index, NOT a decision. It cannot claim ``VERIFIED``.
   * Every absent member must carry a structured ``disposition`` object with one
     of five valid states: ``OPEN``, ``BLOCKED_BY_EVIDENCE``, ``DECIDED``,
     ``IMPLEMENTATION_READY``, ``VERIFIED``.
   * ``DECIDED`` (Waiver / Risk Acceptance / Formal Amendment) MUST provide all 6
     statutory fields:
     - ``formal_decision_ref``: link to formal governance/amendment doc.
     - ``decider``: authorized human role/authority. AI self-signed waivers are
       strictly forbidden and rejected.
     - ``scope``: explicit applicability boundary.
     - ``risk_owner``: designated human risk owner.
     - ``expiry``: ISO date (YYYY-MM-DD); expired waivers fail CI automatically.
     - ``reopen_trigger``: objective observable condition to re-evaluate.
   * ``BLOCKED_BY_EVIDENCE`` MUST provide ``evidence_needed``, ``evidence_owner``,
     and ``next_review_date``.
   * ``IMPLEMENTATION_READY`` MUST provide ``assigned_to`` and ``target_phase``
     (or ``acceptance_criteria``).
   * ``OPEN`` MUST provide ``rationale`` (or ``note``) and tracking metadata.
   * Valid state transitions are enforced across all recorded histories.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = Path(__file__).resolve().parent / "set_valued_requirements.json"

VALID_STATUSES = frozenset({"satisfied", "absent"})

VALID_DISPOSITION_STATES = frozenset({
    "OPEN",
    "BLOCKED_BY_EVIDENCE",
    "DECIDED",
    "IMPLEMENTATION_READY",
    "VERIFIED",
})

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "OPEN": frozenset({"OPEN", "BLOCKED_BY_EVIDENCE", "DECIDED", "IMPLEMENTATION_READY", "VERIFIED"}),
    "BLOCKED_BY_EVIDENCE": frozenset({"BLOCKED_BY_EVIDENCE", "OPEN", "DECIDED", "IMPLEMENTATION_READY"}),
    "DECIDED": frozenset({"DECIDED", "IMPLEMENTATION_READY", "OPEN", "BLOCKED_BY_EVIDENCE"}),
    "IMPLEMENTATION_READY": frozenset({"IMPLEMENTATION_READY", "VERIFIED", "BLOCKED_BY_EVIDENCE", "DECIDED", "OPEN"}),
    "VERIFIED": frozenset({"VERIFIED", "OPEN", "BLOCKED_BY_EVIDENCE"}),
}

AI_AGENT_PATTERN = re.compile(
    r"^(claude|antigravity|gemini|codex|copilot|gpt|chatgpt|llm|ai|agent|bot|autoworker|orchestrator)[\d_\-\s]*$",
    re.IGNORECASE,
)

KNOWN_AI_PREFIXES = ("ai:", "ai/", "agent:", "bot:", "llm:", "gpt:")
KNOWN_AI_KEYWORDS = {
    "claude", "claude2", "claude3",
    "antigravity", "antigravity2", "antigravity3", "antigravity4",
    "antigravity5", "antigravity6", "antigravity7",
    "gemini", "gemini2", "gemini3",
    "codex", "codex2",
    "copilot",
}

HUMAN_AUTHORITY_KEYWORDS = {
    "human", "board", "lead", "officer", "committee", "ops", "director", "head",
    "architect", "chair", "owner", "team", "governance", "manager", "principal",
}


def is_ai_decider(decider: str) -> bool:
    """Return True if the decider identifier represents an AI agent rather than a human authority."""
    if not isinstance(decider, str):
        return True
    cleaned = decider.strip().lower()
    if not cleaned:
        return True
    if AI_AGENT_PATTERN.match(cleaned):
        return True
    if any(cleaned.startswith(p) for p in KNOWN_AI_PREFIXES):
        return True
    tokens = [t for t in re.split(r"[\s/,_\-]+", cleaned) if t]
    ai_role_words = {"ai", "agent", "bot", "llm", "model", "autoworker", "orchestrator", "gpt", "chatgpt"}
    if any(t in KNOWN_AI_KEYWORDS for t in tokens):
        if not any(k in tokens for k in HUMAN_AUTHORITY_KEYWORDS):
            return True
    if set(tokens) <= ai_role_words:
        return True
    if any(phrase in cleaned for phrase in ("ai agent", "auto worker", "llm agent", "ai model", "ai decider", "virtual agent")):
        if not any(k in tokens for k in HUMAN_AUTHORITY_KEYWORDS):
            return True
    return False


def check_expiry(expiry_val: Any, reference_date: date | None = None) -> tuple[bool, str | None]:
    """Check if an ISO expiry date is valid and unexpired."""
    if reference_date is None:
        reference_date = datetime.now(UTC).date()
    if not expiry_val or not isinstance(expiry_val, str):
        return False, "missing or non-string expiry date"
    raw = expiry_val.strip()
    if "T" in raw:
        raw = raw.split("T")[0]
    try:
        exp_date = date.fromisoformat(raw)
    except (ValueError, TypeError):
        return False, f"invalid ISO expiry date format: {expiry_val!r} (expected YYYY-MM-DD)"
    if exp_date < reference_date:
        return False, f"waiver expired on {exp_date.isoformat()} (reference date: {reference_date.isoformat()})"
    return True, None


def validate_transition(from_state: str, to_state: str) -> bool:
    """Validate if a disposition state transition is permitted."""
    if from_state not in VALID_DISPOSITION_STATES or to_state not in VALID_DISPOSITION_STATES:
        return False
    return to_state in VALID_TRANSITIONS.get(from_state, frozenset())


@dataclass(frozen=True)
class Failure:
    requirement: str
    member: str
    problem: str

    def describe(self) -> str:
        return f"{self.requirement} member {self.member!r}: {self.problem}"


@cache
def _module_symbols(path: Path) -> frozenset[str]:
    """Every top-level name a module defines, plus ``Class.member`` pairs.

    Enum members, dataclass fields and plain assignments all resolve, because a
    requirement member is as often a field or an enum value as it is a class.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return frozenset()

    names: set[str] = set()

    def _record_target(target: ast.expr, prefix: str = "") -> None:
        if isinstance(target, ast.Name):
            names.add(f"{prefix}{target.id}")

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign):
                    _record_target(statement.target, f"{node.name}.")
                elif isinstance(statement, ast.Assign):
                    for target in statement.targets:
                        _record_target(target, f"{node.name}.")
                elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(f"{node.name}.{statement.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                _record_target(target)
        elif isinstance(node, ast.AnnAssign):
            _record_target(node.target)
    return frozenset(names)


def resolve(repo_root: Path, reference: str) -> str | None:
    """Return why *reference* does not resolve, or ``None`` when it does.

    A reference is ``relative/path.py::Symbol`` or
    ``relative/path.py::Class.member``.
    """
    if "::" not in reference:
        return "evidence must be 'path.py::Symbol' or 'path.py::Class.member'"
    raw_path, _, symbol = reference.partition("::")
    path = repo_root / raw_path
    if not path.is_file():
        return f"no such file: {raw_path}"
    if symbol not in _module_symbols(path):
        return f"{raw_path} defines no {symbol!r}"
    return None


def validate_disposition_schema(
    requirement: str,
    member_name: str,
    status: str,
    disposition: Any,
    reference_date: date | None = None,
) -> list[Failure]:
    """Validate structured disposition object for schema, fields, and policy gates."""
    failures: list[Failure] = []

    if disposition is None:
        if status == "absent":
            failures.append(
                Failure(
                    requirement,
                    member_name,
                    "absent member declares no 'disposition' block; an un-dispositioned gap is refused",
                )
            )
        return failures

    if not isinstance(disposition, dict):
        failures.append(
            Failure(
                requirement,
                member_name,
                f"'disposition' must be a JSON object/dict, got {type(disposition).__name__}",
            )
        )
        return failures

    state = disposition.get("state")
    if not state or not isinstance(state, str):
        failures.append(
            Failure(
                requirement,
                member_name,
                "disposition missing 'state' field",
            )
        )
        return failures

    if state not in VALID_DISPOSITION_STATES:
        failures.append(
            Failure(
                requirement,
                member_name,
                f"disposition state {state!r} is not one of {sorted(VALID_DISPOSITION_STATES)}",
            )
        )
        return failures

    # Absent status is only an index and cannot masquerade as VERIFIED
    if status == "absent" and state == "VERIFIED":
        failures.append(
            Failure(
                requirement,
                member_name,
                "absent member cannot have disposition state 'VERIFIED'; "
                "absent status is an unfulfilled index and cannot masquerade as verified",
            )
        )

    # Validate transition history if present
    history = disposition.get("history")
    if history is not None:
        if not isinstance(history, list):
            failures.append(
                Failure(requirement, member_name, "disposition 'history' must be a list of transition objects")
            )
        else:
            prev = None
            for idx, item in enumerate(history):
                if not isinstance(item, dict) or "state" not in item:
                    failures.append(
                        Failure(requirement, member_name, f"history item #{idx} must have 'state'")
                    )
                    continue
                curr = item["state"]
                if curr not in VALID_DISPOSITION_STATES:
                    failures.append(
                        Failure(requirement, member_name, f"history item #{idx} has invalid state {curr!r}")
                    )
                    continue
                if prev is not None and not validate_transition(prev, curr):
                    failures.append(
                        Failure(
                            requirement,
                            member_name,
                            f"illegal disposition transition in history: {prev} -> {curr}",
                        )
                    )
                prev = curr
            if prev is not None and prev != state and not validate_transition(prev, state):
                failures.append(
                    Failure(
                        requirement,
                        member_name,
                        f"illegal disposition transition from history tip to current: {prev} -> {state}",
                    )
                )

    previous_state = disposition.get("previous_state")
    if previous_state is not None:
        if previous_state not in VALID_DISPOSITION_STATES:
            failures.append(
                Failure(
                    requirement,
                    member_name,
                    f"previous_state {previous_state!r} is not a valid disposition state",
                )
            )
        elif not validate_transition(previous_state, state):
            failures.append(
                Failure(
                    requirement,
                    member_name,
                    f"illegal disposition transition: {previous_state} -> {state}",
                )
            )

    # State-specific statutory requirements
    if state == "DECIDED":
        # Required 6 statutory fields
        decision_ref = disposition.get("formal_decision_ref") or disposition.get("decision_ref")
        decider = disposition.get("decider")
        scope = disposition.get("scope") or disposition.get("applicable_scope")
        risk_owner = disposition.get("risk_owner")
        expiry = disposition.get("expiry") or disposition.get("expiry_date")
        reopen_trigger = disposition.get("reopen_trigger")

        missing: list[str] = []
        if not decision_ref or not str(decision_ref).strip():
            missing.append("formal_decision_ref")
        if not decider or not str(decider).strip():
            missing.append("decider")
        if not scope or not str(scope).strip():
            missing.append("scope")
        if not risk_owner or not str(risk_owner).strip():
            missing.append("risk_owner")
        if not expiry or not str(expiry).strip():
            missing.append("expiry")
        if not reopen_trigger or not str(reopen_trigger).strip():
            missing.append("reopen_trigger")

        if missing:
            failures.append(
                Failure(
                    requirement,
                    member_name,
                    f"DECIDED disposition missing required statutory field(s): {', '.join(missing)}",
                )
            )
        else:
            # AI decider check
            if is_ai_decider(str(decider)):
                failures.append(
                    Failure(
                        requirement,
                        member_name,
                        f"AI decider {decider!r} is forbidden from signing requirement waivers or amendments; "
                        "must be an authorized human governance authority",
                    )
                )

            # Expiry check
            valid_expiry, expiry_err = check_expiry(expiry, reference_date)
            if not valid_expiry:
                failures.append(
                    Failure(
                        requirement,
                        member_name,
                        f"invalid or expired waiver: {expiry_err}",
                    )
                )

    elif state == "BLOCKED_BY_EVIDENCE":
        evidence_needed = disposition.get("evidence_needed") or disposition.get("query_or_command") or disposition.get("evidence_query")
        evidence_owner = disposition.get("evidence_owner") or disposition.get("owner") or disposition.get("assigned_to")
        next_review_date = disposition.get("next_review_date") or disposition.get("review_date")

        missing = []
        if not evidence_needed or not str(evidence_needed).strip():
            missing.append("evidence_needed")
        if not evidence_owner or not str(evidence_owner).strip():
            missing.append("evidence_owner")
        if not next_review_date or not str(next_review_date).strip():
            missing.append("next_review_date")
        if missing:
            failures.append(
                Failure(
                    requirement,
                    member_name,
                    f"BLOCKED_BY_EVIDENCE disposition missing required field(s): {', '.join(missing)}",
                )
            )

    elif state == "IMPLEMENTATION_READY":
        assigned_to = disposition.get("assigned_to") or disposition.get("owner")
        target_phase = disposition.get("target_phase") or disposition.get("acceptance_criteria")
        missing = []
        if not assigned_to or not str(assigned_to).strip():
            missing.append("assigned_to")
        if not target_phase or not str(target_phase).strip():
            missing.append("target_phase")
        if missing:
            failures.append(
                Failure(
                    requirement,
                    member_name,
                    f"IMPLEMENTATION_READY disposition missing required field(s): {', '.join(missing)}",
                )
            )

    elif state == "OPEN":
        rationale = disposition.get("rationale") or disposition.get("note")
        if not rationale or not str(rationale).strip():
            failures.append(
                Failure(
                    requirement,
                    member_name,
                    "OPEN disposition missing 'rationale' / 'note'",
                )
            )

    return failures


def check(
    repo_root: Path,
    manifest_path: Path,
    reference_date: date | None = None,
) -> tuple[list[Failure], dict[str, Any]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[Failure] = []
    tally: dict[str, Any] = {
        "requirements": 0,
        "members": 0,
        "satisfied": 0,
        "absent": 0,
        "dispositions": {s: 0 for s in sorted(VALID_DISPOSITION_STATES)},
    }

    for entry in payload.get("requirements", []):
        requirement = entry.get("id", "<unnamed>")
        tally["requirements"] += 1
        members = entry.get("members", [])
        if not members:
            failures.append(Failure(requirement, "-", "declares no members"))
            continue

        declared_total = entry.get("member_count")
        if declared_total is None:
            failures.append(
                Failure(
                    requirement,
                    "-",
                    "declares no member_count; without it the member list can shrink "
                    "and still pass, which is the drift this check exists to catch",
                )
            )
        elif not isinstance(declared_total, int) or isinstance(declared_total, bool):
            failures.append(
                Failure(requirement, "-", f"member_count must be an integer, got {declared_total!r}")
            )
        elif declared_total != len(members):
            failures.append(
                Failure(
                    requirement,
                    "-",
                    f"member_count says {declared_total} but {len(members)} are listed; "
                    "the count is there to catch a member being dropped from the list",
                )
            )

        seen: set[str] = set()
        for member in members:
            name = member.get("name", "")
            tally["members"] += 1
            if not name:
                failures.append(Failure(requirement, "<blank>", "member has no name"))
                continue
            if name in seen:
                failures.append(Failure(requirement, name, "listed twice"))
            seen.add(name)

            status = member.get("status", "")
            if status not in VALID_STATUSES:
                failures.append(
                    Failure(requirement, name, f"status {status!r} is not one of {sorted(VALID_STATUSES)}")
                )
                continue

            disposition = member.get("disposition")

            if status == "satisfied":
                tally["satisfied"] += 1
                evidence = member.get("evidence", "")
                if not evidence:
                    failures.append(
                        Failure(requirement, name, "claimed satisfied with no evidence reference")
                    )
                else:
                    problem = resolve(repo_root, evidence)
                    if problem:
                        failures.append(Failure(requirement, name, problem))

                if disposition is not None:
                    disp_failures = validate_disposition_schema(
                        requirement, name, status, disposition, reference_date
                    )
                    failures.extend(disp_failures)
                    disp_state = disposition.get("state", "VERIFIED")
                    if disp_state in tally["dispositions"]:
                        tally["dispositions"][disp_state] += 1
                else:
                    tally["dispositions"]["VERIFIED"] += 1

            else:  # status == "absent"
                tally["absent"] += 1
                if not member.get("note", "").strip():
                    failures.append(
                        Failure(
                            requirement,
                            name,
                            "marked absent with no note; an unwritten gap is the state "
                            "this check exists to leave",
                        )
                    )

                # Validate disposition block
                disp_failures = validate_disposition_schema(
                    requirement, name, status, disposition, reference_date
                )
                failures.extend(disp_failures)
                if isinstance(disposition, dict):
                    disp_state = disposition.get("state")
                    if disp_state in tally["dispositions"]:
                        tally["dispositions"][disp_state] += 1

    return failures, tally


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--show-gaps",
        action="store_true",
        help="list the members recorded as absent and their dispositions",
    )
    parser.add_argument(
        "--show-dispositions",
        action="store_true",
        help="list all members grouped by disposition state",
    )
    parser.add_argument(
        "--reference-date",
        type=str,
        default=None,
        help="override reference date (YYYY-MM-DD) for waiver expiry evaluation",
    )
    args = parser.parse_args(argv)

    ref_date = None
    if args.reference_date:
        ref_date = date.fromisoformat(args.reference_date)

    failures, tally = check(REPO_ROOT, MANIFEST_PATH, ref_date)

    if args.show_gaps:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        for entry in payload.get("requirements", []):
            gaps = [m for m in entry.get("members", []) if m.get("status") == "absent"]
            if gaps:
                print(f"{entry['id']} -- {len(gaps)}/{len(entry['members'])} absent")
                for gap in gaps:
                    disp = gap.get("disposition", {})
                    state = disp.get("state", "UNSET")
                    print(f"    {gap['name']} [{state}]: {gap.get('note', '')}")
                    if state == "DECIDED":
                        print(f"        Decider: {disp.get('decider')}, Expiry: {disp.get('expiry')}, Ref: {disp.get('formal_decision_ref')}")
                    elif state == "BLOCKED_BY_EVIDENCE":
                        print(f"        Evidence Needed: {disp.get('evidence_needed')}, Owner: {disp.get('evidence_owner')}")
        print()

    if args.show_dispositions:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        by_state: dict[str, list[str]] = {s: [] for s in sorted(VALID_DISPOSITION_STATES)}
        for entry in payload.get("requirements", []):
            req_id = entry.get("id")
            for m in entry.get("members", []):
                disp = m.get("disposition", {})
                st = disp.get("state", "VERIFIED" if m.get("status") == "satisfied" else "UNSET")
                if st in by_state:
                    by_state[st].append(f"{req_id}::{m.get('name')}")
        for st, items in by_state.items():
            print(f"State {st} ({len(items)}):")
            for it in items:
                print(f"  - {it}")
        print()

    if failures:
        print("Requirement member checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure.describe()}", file=sys.stderr)
        return 1

    disp_parts = [f"{k}={v}" for k, v in tally["dispositions"].items() if v > 0]
    disp_summary = ", ".join(disp_parts)
    print(
        f"Requirement member checks passed: {tally['requirements']} set-valued requirements, "
        f"{tally['members']} members ({tally['satisfied']} satisfied, {tally['absent']} absent and noted; "
        f"dispositions: {disp_summary})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
