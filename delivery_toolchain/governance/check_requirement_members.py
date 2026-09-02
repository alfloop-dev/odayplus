#!/usr/bin/env python3
"""Hold set-valued requirements to their own member lists.

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
*lists* N items can carry those N items in machine-readable form, and each one
can name where it is satisfied. That covers exactly the class of gap above, and
nothing else.

WHAT IT ENFORCES

For every member of every listed requirement:

* ``satisfied`` -- the evidence reference must resolve to a symbol that exists.
  A member whose implementation is deleted or renamed fails here rather than
  quietly reverting to a gap.
* ``absent`` -- must carry a note saying so. A blank gap is the state this
  check exists to leave; a written one can be argued about.

It does not verify that the implementation is *correct*. It verifies that the
member list is complete, that each claim points somewhere real, and that the
gaps are named. That is the part a machine can hold.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = Path(__file__).resolve().parent / "set_valued_requirements.json"

VALID_STATUSES = {"satisfied", "absent"}


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

    # Nested scopes count. `IntakeMethod` is declared inside the router factory
    # in apps/api/app/routes/listings.py and is no less a definition for it;
    # walking only the module body would report a real symbol as missing and
    # push the manifest towards weaker evidence.
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign):
                    _record_target(statement.target, f"{node.name}.")
                elif isinstance(statement, ast.Assign):
                    for target in statement.targets:
                        _record_target(target, f"{node.name}.")
                elif isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                    names.add(f"{node.name}.{statement.name}")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
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


def check(repo_root: Path, manifest_path: Path) -> tuple[list[Failure], dict[str, int]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[Failure] = []
    tally = {"requirements": 0, "members": 0, "satisfied": 0, "absent": 0}

    for entry in payload.get("requirements", []):
        requirement = entry.get("id", "<unnamed>")
        tally["requirements"] += 1
        members = entry.get("members", [])
        if not members:
            failures.append(Failure(requirement, "-", "declares no members"))
            continue
        # The count is the only thing that catches a member being deleted from
        # the list: the list is self-consistent after the deletion, so nothing
        # else in this file would notice. An optional count is therefore not a
        # weaker check but an absent one -- omitting it is exactly the edit a
        # shrinking requirement would make.
        declared_total = entry.get("member_count")
        if declared_total is None:
            failures.append(
                Failure(
                    requirement,
                    "-",
                    f"declares no member_count; state it as {len(members)} so that "
                    "dropping a member from the list is a detectable change",
                )
            )
        elif isinstance(declared_total, bool) or not isinstance(declared_total, int):
            failures.append(
                Failure(
                    requirement,
                    "-",
                    f"member_count must be an integer, got {declared_total!r}",
                )
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

            if status == "satisfied":
                tally["satisfied"] += 1
                evidence = member.get("evidence", "")
                if not evidence:
                    failures.append(
                        Failure(requirement, name, "claimed satisfied with no evidence reference")
                    )
                    continue
                problem = resolve(repo_root, evidence)
                if problem:
                    failures.append(Failure(requirement, name, problem))
            else:
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
    return failures, tally


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--show-gaps",
        action="store_true",
        help="list the members recorded as absent",
    )
    args = parser.parse_args(argv)

    failures, tally = check(REPO_ROOT, MANIFEST_PATH)

    if args.show_gaps:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        for entry in payload.get("requirements", []):
            gaps = [m for m in entry.get("members", []) if m.get("status") == "absent"]
            if gaps:
                print(f"{entry['id']} -- {len(gaps)}/{len(entry['members'])} absent")
                for gap in gaps:
                    print(f"    {gap['name']}: {gap.get('note', '')}")
        print()

    if failures:
        print("Requirement member checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure.describe()}", file=sys.stderr)
        return 1

    print(
        f"Requirement member checks passed: {tally['requirements']} set-valued requirements, "
        f"{tally['members']} members ({tally['satisfied']} satisfied, {tally['absent']} absent and noted)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
