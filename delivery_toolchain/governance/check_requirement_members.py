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
   * ``DECIDED`` (Waiver / Risk Acceptance / Formal Amendment) MUST provide all 7
     statutory fields:
     - ``formal_decision_ref``: link to formal governance/amendment doc.
     - ``decider``: authorized human role/authority. AI self-signed waivers are
       strictly forbidden and rejected.
     - ``decision_date``: ISO date the ruling was made; a decision nobody dated
       cannot be aged, superseded, or traced back to the meeting that made it.
     - ``scope``: explicit applicability boundary.
     - ``risk_owner``: designated human risk owner.
     - ``expiry``: ISO date (YYYY-MM-DD); expired waivers fail CI automatically.
     - ``reopen_trigger``: objective observable condition to re-evaluate.
   * A ``note`` is an index entry, NOT an amendment. A member whose note (or
     disposition rationale) asserts a non-implementation ruling -- "decided not
     to do", "not pursued", "已裁決", "決定不做" -- while its disposition state is
     anything other than ``DECIDED`` is refused. Prose cannot close a MUST.
   * The statutory fields are validated wherever they appear, not only under
     ``DECIDED``. A waiver parked on a ``VERIFIED`` or ``OPEN`` member used to
     escape every gate -- its decider was never checked and its expiry never
     came due. Carrying any statutory field now requires carrying all of them,
     correctly, whatever the state says.
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
    "OPEN": frozenset({"OPEN", "BLOCKED_BY_EVIDENCE", "DECIDED", "IMPLEMENTATION_READY"}),
    "BLOCKED_BY_EVIDENCE": frozenset({"BLOCKED_BY_EVIDENCE", "OPEN", "DECIDED", "IMPLEMENTATION_READY"}),
    "DECIDED": frozenset({"DECIDED", "IMPLEMENTATION_READY", "OPEN"}),
    "IMPLEMENTATION_READY": frozenset({"IMPLEMENTATION_READY", "VERIFIED", "BLOCKED_BY_EVIDENCE", "OPEN"}),
    "VERIFIED": frozenset({"VERIFIED", "OPEN", "BLOCKED_BY_EVIDENCE"}),
}

AI_NAME_PATTERN = re.compile(
    r"\b(antigravity|claude|gemini|codex|copilot|gpt|chatgpt|llm|autoworker|orchestrator|bot|agent|ai)\d*\b",
    re.IGNORECASE,
)

KNOWN_AI_PREFIXES = ("ai:", "ai/", "ai-", "agent:", "agent/", "bot:", "bot/", "llm:", "gpt:")


def is_ai_decider(decider: str) -> bool:
    """Return True if the decider identifier represents an AI agent rather than a human authority."""
    if not isinstance(decider, str):
        return True
    cleaned = decider.strip().lower()
    if not cleaned:
        return True
    if any(cleaned.startswith(p) for p in KNOWN_AI_PREFIXES):
        return True
    if AI_NAME_PATTERN.search(cleaned):
        return True
    return False


DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The fields a formal ruling must carry to be auditable: who decided, when they
# decided, over what, who owns the residual risk, when it lapses, and what
# observation reopens it. Named once so the DECIDED gate and the disguised-waiver
# gate below cannot drift apart.
STATUTORY_DECISION_FIELDS = (
    "formal_decision_ref",
    "decider",
    "decision_date",
    "scope",
    "risk_owner",
    "expiry",
    "reopen_trigger",
)

# Prose that asserts a requirement will not be implemented. Matching it does not
# make the claim false; it makes the claim answerable -- either the statutory
# fields are there or the sentence is an unauthorised amendment. The phrases are
# deliberately narrow: "it is not a release mode" describes an absence and must
# not match, "DECIDED 2026-09-02: not pursued" rules on one and must.
NONIMPLEMENTATION_CLAIM_PATTERN = re.compile(
    r"""(
      decided [\s_-]* not [\s_-]* (to [\s_-]*)?
        (do|doing|implement|implementing|pursue|pursuing|build|building|model|modell?ing)
    | \bDECIDED\s+\d{4}-\d{2}-\d{2}
    | formally\s+decided
    | not\s+pursued
    | (will|would)\s+not\s+be\s+(implemented|built|modell?ed|delivered|done)
    | won.t\s+be\s+(implemented|built|delivered|done)
    | (formally|permanently)\s+waived
    | waiver\s+granted
    | de-?scoped
    | 已裁決 | 裁決不 | 裁定不 | 決定不做 | 決定不實作 | 已決定不
    | 不予實作 | 不再實作 | 正式豁免 | 已豁免
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def find_nonimplementation_claim(*texts: Any) -> str | None:
    """Return the phrase by which *texts* claims a requirement will not be met."""
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            continue
        match = NONIMPLEMENTATION_CLAIM_PATTERN.search(text)
        if match:
            return match.group(0).strip()
    return None


def resolve_decision_ref(repo_root: Path, reference: str) -> str | None:
    """Return why reference is not a valid formal decision ref, or None when valid.

    A formal decision reference must be one of:
    1. A resolvable repo doc path strictly within repo boundary, e.g. 'docs/.../file.md' or 'docs/.../file.md#anchor'
    2. A valid URL starting with 'http://', 'https://', or 'github://'
    3. A formal PR or RFC reference, e.g. 'PR #123' or 'RFC-123'
    """
    if not isinstance(reference, str) or not reference.strip():
        return "formal_decision_ref must be a non-empty string"
    ref = reference.strip()

    # Check URL
    if ref.startswith(("http://", "https://", "github://")):
        if len(ref) < 10 or "." not in ref:
            return f"invalid URL reference: {reference!r}"
        return None

    # Check formal PR / RFC ref
    if re.match(r"^(PR\s*#?\d+|RFC-\d+|GH-\d+)$", ref, re.IGNORECASE):
        return None

    # Check repo-relative doc path
    raw_path, _, _ = ref.partition("#")
    raw_path = raw_path.strip()

    # Reject attempts to escape repository boundary via absolute path or parent traversals
    if raw_path.startswith("/") or raw_path.startswith("../") or "/../" in raw_path or raw_path == "..":
        return f"formal_decision_ref {reference!r} must be repo-relative and cannot escape repository boundary"

    valid_doc_extensions = (".md", ".rst", ".json", ".txt", ".adoc")
    is_doc_path = (
        raw_path.startswith(("docs/", ".orchestrator/", "delivery_toolchain/"))
        or any(raw_path.endswith(ext) for ext in valid_doc_extensions)
        or "/" in raw_path
    )
    if not is_doc_path:
        return f"formal_decision_ref {reference!r} is not a valid document path, URL, or PR/RFC reference"

    def _is_valid_repo_file(base: Path, rel_path: str) -> bool:
        try:
            target = (base / rel_path).resolve()
            base_resolved = base.resolve()
            target.relative_to(base_resolved)
        except (ValueError, RuntimeError):
            return False
        return target.is_file()

    if not _is_valid_repo_file(repo_root, raw_path):
        if _is_valid_repo_file(REPO_ROOT, raw_path):
            return None
        return f"formal_decision_ref target file does not exist within repository: {raw_path!r}"

    return None


def check_expiry(expiry_val: Any, reference_date: date | None = None) -> tuple[bool, str | None]:
    """Check if an ISO expiry date is valid (strictly YYYY-MM-DD) and unexpired."""
    if reference_date is None:
        reference_date = datetime.now(UTC).date()
    if not expiry_val or not isinstance(expiry_val, str):
        return False, "missing or non-string expiry date"
    raw = expiry_val.strip()
    if not DATE_PATTERN.match(raw):
        return False, f"invalid ISO expiry date format: {expiry_val!r} (expected YYYY-MM-DD)"
    try:
        exp_date = date.fromisoformat(raw)
    except (ValueError, TypeError):
        return False, f"invalid ISO expiry date: {expiry_val!r} (expected valid calendar date in YYYY-MM-DD)"
    if exp_date < reference_date:
        return False, f"waiver expired on {exp_date.isoformat()} (reference date: {reference_date.isoformat()})"
    return True, None


def check_decision_date(value: Any, reference_date: date | None = None) -> tuple[bool, str | None]:
    """Check an ISO decision date is well formed and not dated in the future."""
    if reference_date is None:
        reference_date = datetime.now(UTC).date()
    if not value or not isinstance(value, str):
        return False, "missing or non-string decision date"
    raw = value.strip()
    if not DATE_PATTERN.match(raw):
        return False, f"invalid ISO decision date format: {value!r} (expected YYYY-MM-DD)"
    try:
        decided = date.fromisoformat(raw)
    except (ValueError, TypeError):
        return False, f"invalid ISO decision date: {value!r} (expected valid calendar date in YYYY-MM-DD)"
    if decided > reference_date:
        return False, (
            f"decision_date {decided.isoformat()} is in the future "
            f"(reference date: {reference_date.isoformat()})"
        )
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


def validate_statutory_decision_fields(
    requirement: str,
    member_name: str,
    disposition: dict[str, Any],
    reference_date: date | None,
    repo_root: Path,
    context: str,
) -> list[Failure]:
    """Hold a formal ruling to its statutory fields, wherever it is recorded.

    *context* names what is being judged so the message reads the same whether
    the ruling declared itself ``DECIDED`` or was found parked on another state.
    Every present field is judged even when others are missing: a waiver that is
    both undated and expired should say both things in one run.
    """
    failures: list[Failure] = []

    missing = [
        field
        for field in STATUTORY_DECISION_FIELDS
        if not isinstance(disposition.get(field), str) or not str(disposition.get(field)).strip()
    ]
    if missing:
        failures.append(
            Failure(
                requirement,
                member_name,
                f"{context} missing required statutory field(s): {', '.join(missing)}",
            )
        )

    decision_ref = disposition.get("formal_decision_ref")
    if isinstance(decision_ref, str) and decision_ref.strip():
        ref_err = resolve_decision_ref(repo_root, decision_ref)
        if ref_err:
            failures.append(Failure(requirement, member_name, f"invalid formal_decision_ref: {ref_err}"))

    decider = disposition.get("decider")
    if isinstance(decider, str) and decider.strip() and is_ai_decider(decider):
        failures.append(
            Failure(
                requirement,
                member_name,
                f"AI decider {decider!r} is forbidden from signing requirement waivers or amendments; "
                "must be an authorized human governance authority",
            )
        )

    decision_date = disposition.get("decision_date")
    if decision_date is not None:
        valid_decision_date, decision_date_err = check_decision_date(decision_date, reference_date)
        if not valid_decision_date:
            failures.append(Failure(requirement, member_name, f"invalid decision_date: {decision_date_err}"))

    # No decided-before-expiry comparison: a valid decision_date is on or before
    # the reference date and a valid expiry is on or after it, so an inverted
    # pair is already two failures. A third message would be unreachable.
    expiry = disposition.get("expiry")
    if expiry is not None:
        valid_expiry, expiry_err = check_expiry(expiry, reference_date)
        if not valid_expiry:
            failures.append(Failure(requirement, member_name, f"invalid or expired waiver: {expiry_err}"))

    return failures


def validate_disposition_schema(
    requirement: str,
    member_name: str,
    status: str,
    disposition: Any,
    reference_date: date | None = None,
    repo_root: Path = REPO_ROOT,
    claim_text: str = "",
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
    history_states: list[str] = []
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
                history_states.append(curr)
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

    # Check consistency between history and previous_state
    if previous_state is not None and history_states:
        if history_states[-1] == state:
            expected_prev = history_states[-2] if len(history_states) >= 2 else None
        else:
            expected_prev = history_states[-1]

        if expected_prev is not None and previous_state != expected_prev:
            failures.append(
                Failure(
                    requirement,
                    member_name,
                    f"disposition 'previous_state' ({previous_state!r}) contradicts history predecessor state {expected_prev!r}",
                )
            )

    # State-specific statutory requirements
    if state == "DECIDED":
        # Strictly canonical named fields, no aliases.
        failures.extend(
            validate_statutory_decision_fields(
                requirement,
                member_name,
                disposition,
                reference_date,
                repo_root,
                context="DECIDED disposition",
            )
        )

    elif state == "BLOCKED_BY_EVIDENCE":
        evidence_needed = disposition.get("evidence_needed")
        evidence_owner = disposition.get("evidence_owner")
        next_review_date = disposition.get("next_review_date")

        missing = []
        if not evidence_needed or not isinstance(evidence_needed, str) or not evidence_needed.strip():
            missing.append("evidence_needed")
        if not evidence_owner or not isinstance(evidence_owner, str) or not evidence_owner.strip():
            missing.append("evidence_owner")
        if not next_review_date or not isinstance(next_review_date, str) or not next_review_date.strip():
            missing.append("next_review_date")
        if missing:
            failures.append(
                Failure(
                    requirement,
                    member_name,
                    f"BLOCKED_BY_EVIDENCE disposition missing required field(s): {', '.join(missing)}",
                )
            )
        elif next_review_date:
            valid_date, date_err = check_expiry(next_review_date, reference_date=date.min)
            if not valid_date:
                failures.append(
                    Failure(
                        requirement,
                        member_name,
                        f"invalid ISO next_review_date format: {next_review_date!r} (expected YYYY-MM-DD)",
                    )
                )

    elif state == "IMPLEMENTATION_READY":
        assigned_to = disposition.get("assigned_to")
        target_phase = disposition.get("target_phase")
        acceptance_criteria = disposition.get("acceptance_criteria")

        missing = []
        if not assigned_to or not isinstance(assigned_to, str) or not assigned_to.strip():
            missing.append("assigned_to")
        if (not target_phase or not isinstance(target_phase, str) or not target_phase.strip()) and (
            not acceptance_criteria or not isinstance(acceptance_criteria, str) or not acceptance_criteria.strip()
        ):
            missing.append("target_phase or acceptance_criteria")
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
        assigned_to = disposition.get("assigned_to")
        next_review_date = disposition.get("next_review_date")

        missing_open: list[str] = []
        if not rationale or not isinstance(rationale, str) or not rationale.strip():
            missing_open.append("rationale")
        if (not assigned_to or not isinstance(assigned_to, str) or not assigned_to.strip()) and (
            not next_review_date or not isinstance(next_review_date, str) or not next_review_date.strip()
        ):
            missing_open.append("assigned_to or next_review_date")

        if missing_open:
            failures.append(
                Failure(
                    requirement,
                    member_name,
                    f"OPEN disposition missing required field(s): {', '.join(missing_open)}",
                )
            )
        elif next_review_date:
            valid_date, date_err = check_expiry(next_review_date, reference_date=date.min)
            if not valid_date:
                failures.append(
                    Failure(
                        requirement,
                        member_name,
                        f"invalid ISO next_review_date format: {next_review_date!r} (expected YYYY-MM-DD)",
                    )
                )

    # A ruling recorded anywhere but under DECIDED. Two shapes, one root: the
    # statutory gate only ever looked at members that volunteered for it.
    if state != "DECIDED":
        carried = [
            field
            for field in STATUTORY_DECISION_FIELDS
            if isinstance(disposition.get(field), str) and str(disposition.get(field)).strip()
        ]
        claim = find_nonimplementation_claim(
            claim_text, disposition.get("rationale"), disposition.get("note")
        )
        if carried:
            # Statutory fields are a waiver wherever they sit. Judge them there,
            # so an expiry parked on a VERIFIED member still comes due.
            failures.extend(
                validate_statutory_decision_fields(
                    requirement,
                    member_name,
                    disposition,
                    reference_date,
                    repo_root,
                    context=f"disposition state {state!r} carrying decision fields ({', '.join(carried)}) is",
                )
            )
        elif claim:
            failures.append(
                Failure(
                    requirement,
                    member_name,
                    f"note claims a non-implementation decision ({claim!r}) while the disposition state is "
                    f"{state!r}: a note is an index entry, not a requirement amendment. Record a DECIDED "
                    "disposition carrying the statutory fields signed by an authorized human, or drop the claim",
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
                        requirement,
                        name,
                        status,
                        disposition,
                        reference_date,
                        repo_root=repo_root,
                        claim_text=member.get("note", ""),
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
                    requirement,
                    name,
                    status,
                    disposition,
                    reference_date,
                    repo_root=repo_root,
                    claim_text=member.get("note", ""),
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
                        print(
                            f"        Decider: {disp.get('decider')}, Decided: {disp.get('decision_date')}, "
                            f"Expiry: {disp.get('expiry')}, Ref: {disp.get('formal_decision_ref')}"
                        )
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
