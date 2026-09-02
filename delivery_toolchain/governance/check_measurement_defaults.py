#!/usr/bin/env python3
"""Refuse a bounded quality score that defaults to its maximum.

A field like ``data_quality_score: float = 1.0`` reads as ordinary defensive
coding. What it actually says is: when nobody supplied a quality figure, assume
the data is perfect. The value that means "we did not measure this" and the
value that means "we measured it and it was flawless" become the same number,
and everything downstream treats them alike.

That is not a hypothetical. Three examples from this repository:

* ``HeatZoneV3Input.confidence`` and ``.coverage_ratio`` both default to 1.0,
  and ``check_support_and_abstention`` abstains when confidence < 0.25 or
  coverage_ratio < 0.50. A zone built without either figure defaults to perfect
  and can never trigger the abstention gate that exists to fail closed outside
  platform support.
* ``StoreDayObservation.data_quality_score`` defaults to 1.0, so a low-quality
  observation that arrives without its score is weighted as a flawless one.
* The same field was written into the heat zone absorption module during this
  work, declared and never read, and removed in review (fb75a142).

The pattern survives because each instance is locally reasonable and because
nothing looks for it. Type checkers accept it, tests that construct the object
with real values never exercise the default, and the diff that introduces one
looks like adding a sensible fallback.

WHY THIS RULE AND NOT A BROADER ONE
-----------------------------------
The tempting rule is "no defaults on measurement fields". Measured against this
tree that flags 311 fields, most of them legitimate: ``srid: int = 4326``,
``limit: int = 100``, ``horizon_days: int = 28``. A gate that noisy earns a
blanket exemption within a week and then guards nothing.

This rule flags 16, and every one is the same defect: a bounded score assumed
perfect in the absence of evidence. Narrow and true beats broad and ignored.

A second tier -- measured quantities defaulting to 0.0, such as
``EffectInterval.standard_error = 0.0`` (zero standard error means perfect
certainty) -- is real but mixes with legitimate zero-initialised accumulators,
so it is reported under ``--report-second-tier`` and not enforced.

EXEMPTIONS
----------
``measurement_default_exemptions.json`` carries the fields that predate this
check. Each entry needs an owner and a reason, so the debt is written down and
attributable rather than invisible. New violations fail; they are not
exemptible by adding a line without saying who owns it and why.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXEMPTIONS_PATH = Path(__file__).resolve().parent / "measurement_default_exemptions.json"

SCANNED_ROOTS = ("modules", "shared", "solver", "models", "apps")

#: Field-name suffixes that denote a bounded quality or confidence score, where
#: the top of the range is "perfect" and the bottom is "unusable".
BOUNDED_SCORE_SUFFIX = re.compile(
    r"(?:^|_)(score|quality|confidence|reliability|completeness)$"
    r"|^coverage_ratio$"
    r"|(?:^|_)(quality_score|confidence_score)$"
)

#: The value that means "perfect" for such a score.
PERFECT = 1.0

SECOND_TIER_QUANTITY = re.compile(
    r"(?:^|_)(revenue|margin|spend|cost|amount|error|delta|uplift|elasticity)$"
)


@dataclass(frozen=True)
class Violation:
    path: str
    lineno: int
    class_name: str
    field_name: str
    default: object

    @property
    def key(self) -> str:
        return f"{self.path}::{self.class_name}.{self.field_name}"

    def describe(self) -> str:
        return (
            f"{self.path}:{self.lineno} {self.class_name}.{self.field_name} = {self.default!r} "
            f"-- a bounded score defaulting to perfect; absence becomes indistinguishable "
            f"from a flawless measurement"
        )


def _is_dataclass(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "dataclass":
            return True
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name):
            if decorator.func.id == "dataclass":
                return True
        if isinstance(decorator, ast.Attribute) and decorator.attr == "dataclass":
            return True
    return False


def _python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for candidate in root.rglob("*.py"):
        parts = candidate.parts
        if "tests" in parts or candidate.name.startswith("test_"):
            continue
        if "__pycache__" in parts or "node_modules" in parts:
            continue
        files.append(candidate)
    return files


def scan(repo_root: Path, *, second_tier: bool = False) -> list[Violation]:
    """Collect every bounded score that defaults to perfect."""
    pattern = SECOND_TIER_QUANTITY if second_tier else BOUNDED_SCORE_SUFFIX
    target_default: object = 0.0 if second_tier else PERFECT

    violations: list[Violation] = []
    for scanned_root in SCANNED_ROOTS:
        root = repo_root / scanned_root
        if not root.is_dir():
            continue
        for path in _python_files(root):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                # check_code_boundaries.py already reports unparseable files.
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef) or not _is_dataclass(node):
                    continue
                for statement in node.body:
                    if not isinstance(statement, ast.AnnAssign) or statement.value is None:
                        continue
                    if ast.unparse(statement.annotation) != "float":
                        continue
                    if not isinstance(statement.value, ast.Constant):
                        continue
                    if statement.value.value != target_default:
                        continue
                    field_name = ast.unparse(statement.target)
                    if not pattern.search(field_name):
                        continue
                    violations.append(
                        Violation(
                            path=str(path.relative_to(repo_root)),
                            lineno=statement.lineno,
                            class_name=node.name,
                            field_name=field_name,
                            default=statement.value.value,
                        )
                    )
    return sorted(violations, key=lambda v: (v.path, v.lineno))


def load_exemptions(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    exemptions: dict[str, dict[str, str]] = {}
    for entry in payload.get("exemptions", []):
        key = entry.get("field", "")
        owner = entry.get("owner", "").strip()
        reason = entry.get("reason", "").strip()
        if not key:
            raise SystemExit("exemption entry is missing 'field'")
        if not owner or not reason:
            # An exemption without an owner is how debt goes back to being
            # invisible; the file exists to keep it attributable.
            raise SystemExit(f"exemption {key} needs both 'owner' and 'reason'")
        exemptions[key] = {"owner": owner, "reason": reason}
    return exemptions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--report-second-tier",
        action="store_true",
        help="also list measured quantities defaulting to 0.0 (reported, never enforced)",
    )
    parser.add_argument(
        "--write-exemptions",
        action="store_true",
        help="record every current violation as an exemption stub for review",
    )
    args = parser.parse_args(argv)

    violations = scan(REPO_ROOT)
    exemptions = load_exemptions(EXEMPTIONS_PATH)

    if args.write_exemptions:
        payload = {
            "_comment": (
                "Bounded scores that default to perfect, predating "
                "check_measurement_defaults.py. Each needs an owner and a reason. "
                "Removing an entry means the field no longer assumes perfect data."
            ),
            "exemptions": [
                {
                    "field": v.key,
                    "owner": exemptions.get(v.key, {}).get("owner", "UNASSIGNED"),
                    "reason": exemptions.get(v.key, {}).get(
                        "reason", "pre-existing; not yet reviewed"
                    ),
                }
                for v in violations
            ],
        }
        EXEMPTIONS_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Wrote {len(violations)} exemption stubs to {EXEMPTIONS_PATH.name}")
        return 0

    unexempted = [v for v in violations if v.key not in exemptions]

    if args.report_second_tier:
        second = scan(REPO_ROOT, second_tier=True)
        print(f"Second tier (reported, not enforced): {len(second)} measured quantities default to 0.0")
        for violation in second:
            print(f"  - {violation.path}:{violation.lineno} {violation.class_name}.{violation.field_name}")
        print()

    if unexempted:
        print("Measurement default checks failed:", file=sys.stderr)
        for violation in unexempted:
            print(f"  - {violation.describe()}", file=sys.stderr)
        print(
            "\nA bounded score must not default to perfect. Either make absence explicit "
            "(`float | None = None`, and refuse or abstain when it is None), or record the "
            "field in measurement_default_exemptions.json with an owner and a reason.",
            file=sys.stderr,
        )
        return 1

    stale = sorted(set(exemptions) - {v.key for v in violations})
    if stale:
        print("Exemptions no longer needed (the field was fixed or removed):", file=sys.stderr)
        for key in stale:
            print(f"  - {key}", file=sys.stderr)
        print("\nDelete them so the file stays a list of live debt.", file=sys.stderr)
        return 1

    print(
        f"Measurement default checks passed: {len(violations)} known, "
        f"{len(exemptions)} exempted with an owner."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
