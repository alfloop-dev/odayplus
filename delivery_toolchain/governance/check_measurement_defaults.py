#!/usr/bin/env python3
"""Refuse a bounded quality score that defaults to its maximum, in any layer.

A field like ``data_quality_score: float = 1.0`` reads as ordinary defensive
coding. What it actually says is: when nobody supplied a quality figure, assume
the data is perfect. The value that means "we did not measure this" and the
value that means "we measured it and it was flawless" become the same number,
and everything downstream treats them alike.

That is not a hypothetical. Three examples from this repository:

* ``HeatZoneV3Input.confidence`` and ``.coverage_ratio`` both defaulted to 1.0,
  and ``check_support_and_abstention`` abstains when confidence < 0.25 or
  coverage_ratio < 0.50. A zone built without either figure defaulted to perfect
  and could never trigger the abstention gate that exists to fail closed outside
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

This rule flags one shape only: a bounded score assumed perfect in the absence
of evidence. Narrow and true beats broad and ignored. The field-name predicate
(``BOUNDED_SCORE_SUFFIX``) is what buys the precision, and it is the same
predicate in every layer below -- widening it widens all six at once, which is
why it has its own negative tests.

WHY SIX LAYERS AND NOT ONE
--------------------------
The first version of this check read Python dataclasses only. Its own evidence
note said so: "it will not catch a Pydantic default, ``.get(..., 1.0)``, a dbt
``coalesce``, a DB ``DEFAULT 1.00``, OpenAPI or TS, so '16 were all real' cannot
be turned into 'the tree has only 16'."

That gap is not academic, because a bounded score does not travel through one
layer. It is declared in a dataclass, re-declared in the request model, rebuilt
by a row mapper, materialised by a view, stored by a column, published in a
schema and consumed by a client -- and *any one of those* can re-narrow absence
back to perfect after the layer above it was fixed. ``modules/heatzone/v3``
is the worked example already sitting in the tree: ``HeatZoneV3Input`` was
correctly changed to ``float | None = None``, and ``contract.py`` still rebuilds
the output with ``float(data.get("confidence", 1.0))``. Fixing the dataclass and
declaring victory would have left the defect live one function below.

So the same question is asked of six layers:

``dataclass``   a dataclass field annotated ``float`` defaulting to ``1.0``
``pydantic``    the same shape in a request/response model, including
                ``Field(default=1.0)`` -- the API's own copy of the contract
``mapper``      ``record.get("confidence", 1.0)`` and ``default=1.0`` fallbacks,
                which put the value back after the annotation was fixed
``sql``         ``coalesce(x.confidence, 1.0)``, a bare ``1.0 as confidence``
                projection, and ``confidence REAL NOT NULL DEFAULT 1.00``
``openapi``     ``"default": 1.0`` on a bounded score in a published schema,
                which makes an omitted field arrive as perfect at every client
``typescript``  ``confidence ?? 1`` and friends in the consumer

The SQL layer deliberately separates two things that look alike. A projection
that discriminates -- ``case when h3_index is not null then 1.0 else 0.0 end as
data_quality_score`` -- is a rule that can produce a low value, and is not
flagged. A bare ``1.0 as data_quality_score`` cannot, and is.

A second tier -- measured quantities defaulting to 0.0, such as
``EffectInterval.standard_error = 0.0`` (zero standard error means perfect
certainty) -- is real but mixes with legitimate zero-initialised accumulators,
so it is reported under ``--report-second-tier`` and not enforced.

EXEMPTIONS
----------
``measurement_default_exemptions.json`` carries the fields that predate this
check. Each entry needs an owner, a reason and an expiry date, so the debt is
written down, attributable, and dated rather than permanent. New violations
fail; they are not exemptible by adding a line without saying who owns it, why,
and by when. An entry past its ``expires`` date fails the check exactly as an
unexempted violation does -- an exemption that never expires is a decision to
keep the defect, written in a file that reads like a plan to remove it.

Exemptions are per field, per layer, per file. That is deliberate: fixing
``shared/domain/models.py::Poi.confidence`` while leaving ``pois.confidence
REAL NOT NULL DEFAULT 1.00`` in the migration is a half-fix, and two entries
are what makes the half visible. The stale-exemption check is the other half of
that contract: once a field stops violating, its entry must go in the same
commit, or the check fails.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
EXEMPTIONS_PATH = Path(__file__).resolve().parent / "measurement_default_exemptions.json"

#: Python source roots. Kept under the old name because it is the scanned domain
#: quoted in the evidence note for the dataclass tier.
SCANNED_ROOTS = ("modules", "shared", "solver", "models", "apps")

#: Where SQL that defines or materialises a governed measurement can live:
#: dbt model-ready views under ``pipelines`` and schema DDL under ``infra``.
SQL_ROOTS = ("pipelines", "infra", "apps", "modules", "shared", "models", "solver", "product_ops")

#: Published contracts and their generated consumers.
CONTRACT_ROOTS = ("packages", "apps")

#: Field-name suffixes that denote a bounded quality or confidence score, where
#: the top of the range is "perfect" and the bottom is "unusable". This is the
#: single predicate that keeps every layer narrow; widening it widens all six.
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

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def is_bounded_score(name: str) -> bool:
    """True for a name that denotes a bounded score, in snake or camel case.

    TypeScript spells the same field ``coverageRatio``; the predicate has to be
    one predicate or the layers drift apart.
    """
    snake = _CAMEL_BOUNDARY.sub("_", name).lower()
    return bool(BOUNDED_SCORE_SUFFIX.search(snake))


def _is_perfect(value: object) -> bool:
    """``1``, ``1.0`` and ``1.00`` all mean perfect; ``True`` does not."""
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float)) and float(value) == PERFECT


@dataclass(frozen=True)
class Violation:
    path: str
    lineno: int
    class_name: str
    field_name: str
    default: object
    layer: str = "dataclass"
    detail: str = ""

    @property
    def key(self) -> str:
        return f"{self.path}::{self.class_name}.{self.field_name}"

    @property
    def location(self) -> str:
        """``path:line``, or just ``path`` where the format has no line to give.

        A JSON schema is parsed, not read line by line; claiming a line number
        for it would be a number nobody can act on.
        """
        return f"{self.path}:{self.lineno}" if self.lineno else self.path

    def describe(self) -> str:
        return f"[{self.layer}] {self.location} {self.class_name}.{self.field_name} -- {self._why()}"

    def _why(self) -> str:
        if self.layer == "dataclass":
            return (
                f"= {self.default!r}; a bounded score defaulting to perfect, so absence "
                f"becomes indistinguishable from a flawless measurement"
            )
        if self.layer == "pydantic":
            return (
                f"= {self.default!r}; a request field omitted by the caller arrives as a "
                f"perfect score, and the API is where absence still could have been seen"
            )
        if self.layer == "mapper":
            return (
                f"`{self.detail}`; the mapper puts the perfect value back after the "
                f"annotation above it was made optional"
            )
        if self.layer == "sql":
            return (
                f"`{self.detail}`; the row reaches every consumer already claiming a "
                f"perfect score, with no column left saying it was never measured"
            )
        if self.layer == "openapi":
            return (
                f"default {self.default!r} in a published schema; every generated client "
                f"omitting the field sends a perfect score without its author choosing to"
            )
        if self.layer == "typescript":
            return (
                f"`{self.detail}`; the consumer substitutes a perfect score for one the "
                f"API declined to assert"
            )
        return f"= {self.default!r}"


# --------------------------------------------------------------------------
# Python: dataclass fields, Pydantic model fields, and row mappers
# --------------------------------------------------------------------------

_SKIP_DIR_PARTS = frozenset({"__pycache__", "node_modules", ".venv", "archive", "docs_archive"})


def _source_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """Every non-test source file under ``root`` with one of ``suffixes``.

    Test files are excluded on purpose: a fixture that constructs a perfect
    record is stating a premise, not asserting one about production data.
    """
    files: list[Path] = []
    for suffix in suffixes:
        for candidate in root.rglob(f"*{suffix}"):
            parts = set(candidate.parts)
            if parts & _SKIP_DIR_PARTS or "tests" in parts or "__tests__" in parts:
                continue
            name = candidate.name
            if name.startswith("test_") or ".test." in name or ".spec." in name:
                continue
            files.append(candidate)
    return sorted(files)


def _python_files(root: Path) -> list[Path]:
    return _source_files(root, (".py",))


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


def _is_pydantic_model(node: ast.ClassDef) -> bool:
    """A class whose declared base is (or ends in) ``BaseModel``.

    This sees ``BaseModel``, ``pydantic.BaseModel`` and a project's own
    ``CamelBaseModel``. It does not resolve a base defined in another module, so
    a model inheriting from a local alias is invisible here -- the dataclass and
    OpenAPI layers are what cover that case from either side.
    """
    for base in node.bases:
        rendered = ast.unparse(base)
        if rendered.split(".")[-1].endswith("BaseModel"):
            return True
    return False


def _annotation_is_float(annotation: ast.expr) -> bool:
    return ast.unparse(annotation) in {"float", "'float'", '"float"'}


def _declared_call_default(value: ast.expr) -> object | None:
    """The default a ``Field(...)`` or ``field(...)`` call declares, if any.

    Both spellings hide the same constant behind a call, which is enough to make
    the plain ``= 1.0`` scan miss it: ``quality_score: float = Field(default=1.0)``
    and ``quality_score: float = field(default=1.0)`` say exactly what the bare
    literal says.
    """
    if not isinstance(value, ast.Call):
        return None
    if ast.unparse(value.func).split(".")[-1] not in {"Field", "field"}:
        return None
    if value.args and isinstance(value.args[0], ast.Constant):
        return value.args[0].value
    for keyword in value.keywords:
        if keyword.arg == "default" and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def _class_field_violations(
    path: str, node: ast.ClassDef, layer: str, target_default: object, pattern: re.Pattern[str]
) -> Iterator[Violation]:
    for statement in node.body:
        if not isinstance(statement, ast.AnnAssign) or statement.value is None:
            continue
        if not _annotation_is_float(statement.annotation):
            continue
        if isinstance(statement.value, ast.Constant):
            default = statement.value.value
        else:
            default = _declared_call_default(statement.value)
            if default is None:
                continue
        if default != target_default or isinstance(default, bool):
            continue
        field_name = ast.unparse(statement.target)
        if not pattern.search(field_name):
            continue
        yield Violation(
            path=path,
            lineno=statement.lineno,
            class_name=node.name,
            field_name=field_name,
            default=default,
            layer=layer,
        )


def _mapper_violations(path: str, tree: ast.Module) -> Iterator[Violation]:
    """Fallbacks that rebuild a perfect score from an absent key.

    Two shapes, both present in this tree:

    * ``record.get("confidence", 1.0)`` -- the direct one
    * ``_first_present(data, "average_confidence", "confidence", default=1.0)``
      -- a helper that hides the same substitution behind a keyword
    """
    for scope, call in _calls_with_scope(tree):
        key = _perfect_fallback_key(call)
        if key is None:
            continue
        yield Violation(
            path=path,
            lineno=call.lineno,
            class_name=scope or "<module>",
            field_name=key,
            default=PERFECT,
            layer="mapper",
            detail=_truncate(ast.unparse(call)),
        )


def _perfect_fallback_key(call: ast.Call) -> str | None:
    """The bounded-score key this call substitutes a perfect value for."""
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "get"
        and len(call.args) == 2
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
        and isinstance(call.args[1], ast.Constant)
        and _is_perfect(call.args[1].value)
        and is_bounded_score(call.args[0].value)
    ):
        return call.args[0].value

    default_kw = next(
        (
            kw
            for kw in call.keywords
            if kw.arg == "default"
            and isinstance(kw.value, ast.Constant)
            and _is_perfect(kw.value.value)
        ),
        None,
    )
    if default_kw is None:
        return None
    for argument in call.args:
        if (
            isinstance(argument, ast.Constant)
            and isinstance(argument.value, str)
            and is_bounded_score(argument.value)
        ):
            return argument.value
    return None


def _calls_with_scope(tree: ast.Module) -> Iterator[tuple[str, ast.Call]]:
    """Every call in the module, tagged with its enclosing class/function path.

    The scope is what makes an exemption key stable: two connectors in one file
    can hold the identical ``record.get("confidence", 1.0)``, and they are two
    separate pieces of debt with potentially two different owners.
    """

    def walk(node: ast.AST, scope: tuple[str, ...]) -> Iterator[tuple[str, ast.Call]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from walk(child, scope + (child.name,))
                continue
            if isinstance(child, ast.Call):
                yield ".".join(scope), child
            yield from walk(child, scope)

    yield from walk(tree, ())


def _truncate(text: str, limit: int = 90) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


# --------------------------------------------------------------------------
# SQL: column defaults, coalesce fallbacks, and constant projections
# --------------------------------------------------------------------------

_SQL_LINE_COMMENT = re.compile(r"--[^\n]*")
_SQL_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

_SQL_CREATE_TABLE = re.compile(
    r"\bcreate\s+(?:or\s+replace\s+)?table\s+(?:if\s+not\s+exists\s+)?"
    r"(?P<table>[a-z_][\w.\"]*)",
    re.IGNORECASE,
)
_SQL_NUMERIC_TYPE = (
    r"(?:real|double\s+precision|float\d*"
    r"|numeric(?:\s*\([^)]*\))?|decimal(?:\s*\([^)]*\))?)"
)
_SQL_COLUMN_DEFAULT = re.compile(
    r"^[ \t]*(?:add\s+column\s+(?:if\s+not\s+exists\s+)?)?\"?(?P<col>[a-z_][a-z0-9_]*)\"?[ \t]+"
    + _SQL_NUMERIC_TYPE
    + r"\b[^,;]*?\bdefault[ \t]+(?P<val>1(?:\.0+)?)\b",
    re.IGNORECASE | re.MULTILINE,
)
_SQL_SET_DEFAULT = re.compile(
    r"\balter\s+column\s+\"?(?P<col>[a-z_][a-z0-9_]*)\"?\s+set\s+default\s+(?P<val>1(?:\.0+)?)\b",
    re.IGNORECASE,
)
_SQL_COALESCE = re.compile(
    r"\b(?:coalesce|ifnull|nvl)\s*\(\s*(?P<expr>[a-z_][\w.\"]*)\s*,\s*(?P<val>1(?:\.0+)?)\s*\)",
    re.IGNORECASE,
)
#: A bare constant projected as a score. ``case when ... then 1.0 else 0.0 end
#: as data_quality_score`` does not match, and must not: it discriminates, so it
#: is a rule that can report a bad row. ``1.0 as data_quality_score`` cannot.
_SQL_CONSTANT_PROJECTION = re.compile(
    r"(?<![\w.])(?P<val>1(?:\.0+)?)\s+as\s+\"?(?P<alias>[a-z_][a-z0-9_]*)\"?",
    re.IGNORECASE,
)


def _blank_sql_comments(text: str) -> str:
    """Blank comments while keeping every offset and line number intact."""

    def blank(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    return _SQL_LINE_COMMENT.sub(blank, _SQL_BLOCK_COMMENT.sub(blank, text))


def _sql_violations(path: str, text: str) -> Iterator[Violation]:
    body = _blank_sql_comments(text)
    tables = [(m.start(), m.group("table").strip('"')) for m in _SQL_CREATE_TABLE.finditer(body)]

    def line_of(offset: int) -> int:
        return body.count("\n", 0, offset) + 1

    def table_at(offset: int) -> str:
        enclosing = [name for start, name in tables if start < offset]
        return enclosing[-1] if enclosing else "<schema>"

    for match in _SQL_COLUMN_DEFAULT.finditer(body):
        column = match.group("col")
        if not is_bounded_score(column):
            continue
        yield Violation(
            path=path,
            lineno=line_of(match.start()),
            class_name=table_at(match.start()),
            field_name=column,
            default=float(match.group("val")),
            layer="sql",
            detail=_truncate(match.group(0)),
        )

    for match in _SQL_SET_DEFAULT.finditer(body):
        column = match.group("col")
        if not is_bounded_score(column):
            continue
        yield Violation(
            path=path,
            lineno=line_of(match.start()),
            class_name=table_at(match.start()),
            field_name=column,
            default=float(match.group("val")),
            layer="sql",
            detail=_truncate(match.group(0)),
        )

    for match in _SQL_COALESCE.finditer(body):
        expression = match.group("expr").replace('"', "")
        column = expression.rsplit(".", 1)[-1]
        if not is_bounded_score(column):
            continue
        qualifier = expression.rsplit(".", 1)[0] if "." in expression else "<select>"
        yield Violation(
            path=path,
            lineno=line_of(match.start()),
            class_name=qualifier,
            field_name=column,
            default=float(match.group("val")),
            layer="sql",
            detail=_truncate(match.group(0)),
        )

    for match in _SQL_CONSTANT_PROJECTION.finditer(body):
        alias = match.group("alias")
        if not is_bounded_score(alias):
            continue
        yield Violation(
            path=path,
            lineno=line_of(match.start()),
            class_name="<select>",
            field_name=alias,
            default=float(match.group("val")),
            layer="sql",
            detail=_truncate(match.group(0)),
        )


# --------------------------------------------------------------------------
# Published contracts: OpenAPI schema defaults and TypeScript consumers
# --------------------------------------------------------------------------


def _openapi_documents(root: Path) -> Iterator[tuple[Path, dict, str]]:
    """Every JSON file under ``root`` that declares itself an API description.

    Selection is by content (an ``openapi`` or ``swagger`` key) rather than by
    filename, so a spec that gets renamed does not quietly leave the scan.
    """
    for candidate in _source_files(root, (".json",)):
        if candidate.name in {"package.json", "package-lock.json"}:
            continue
        try:
            raw = candidate.read_text(encoding="utf-8")
            document = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if isinstance(document, dict) and ("openapi" in document or "swagger" in document):
            yield candidate, document, raw


def _openapi_violations(path: str, document: dict, raw: str = "") -> Iterator[Violation]:
    """``"default": 1.0`` on a bounded score in a published schema.

    This is the layer with the widest blast radius per line: the default is
    applied by the server for every client that omits the field, and the
    generated TypeScript type does not carry it, so no consumer author ever sees
    the choice being made on their behalf.
    """

    def walk(node: object, pointer: tuple[str, ...]) -> Iterator[Violation]:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                for name, schema in sorted(properties.items()):
                    if not isinstance(schema, dict) or not is_bounded_score(name):
                        continue
                    if "default" in schema and _is_perfect(schema["default"]):
                        yield Violation(
                            path=path,
                            lineno=_unambiguous_line(raw, f'"{name}"'),
                            class_name=".".join(pointer) or "<root>",
                            field_name=name,
                            default=schema["default"],
                            layer="openapi",
                        )
            for key, value in node.items():
                # "properties" is structural noise in the pointer; the schema
                # name above it is what an owner has to recognise.
                child = pointer if key == "properties" else pointer + (str(key),)
                yield from walk(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from walk(value, pointer + (str(index),))

    yield from walk(document, ())


def _unambiguous_line(raw: str, needle: str) -> int:
    """The line holding ``needle``, but only when there is exactly one.

    Better no line number than a confidently wrong one pointing at the second
    schema that happens to declare the same property name.
    """
    hits = [index for index, line in enumerate(raw.splitlines(), start=1) if needle in line]
    return hits[0] if len(hits) == 1 else 0


#: ``confidence ?? 1``, ``coverageRatio || 1``, ``const quality = 1``. Comparison
#: operators are excluded by the lookbehind: ``confidence >= 1`` is a threshold
#: test, which is the opposite of substituting a value.
_TS_PERFECT_FALLBACK = re.compile(
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?::\s*number\s*)?"
    r"(?:(?<![=!<>*/+\-%&|^~])=(?!=)|\?\?|\|\|)\s*"
    r"(?P<val>1(?:\.0+)?)(?![\w.])"
)
_TS_LINE_COMMENT = re.compile(r"//[^\n]*")


def _typescript_violations(path: str, text: str) -> Iterator[Violation]:
    body = _TS_LINE_COMMENT.sub("", _SQL_BLOCK_COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text))
    for match in _TS_PERFECT_FALLBACK.finditer(body):
        name = match.group("name")
        if not is_bounded_score(name):
            continue
        yield Violation(
            path=path,
            lineno=body.count("\n", 0, match.start()) + 1,
            class_name="<module>",
            field_name=name,
            default=float(match.group("val")),
            layer="typescript",
            detail=_truncate(match.group(0)),
        )


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

LAYERS = ("dataclass", "pydantic", "mapper", "sql", "openapi", "typescript")


def scan(
    repo_root: Path, *, second_tier: bool = False, layers: tuple[str, ...] = LAYERS
) -> list[Violation]:
    """Collect every bounded score that is assumed perfect, in every layer.

    ``second_tier`` switches the Python tier to the reported-only rule
    (measured quantities defaulting to 0.0) and is dataclass-scoped: that tier
    is not enforced, so extending it across layers would only add noise.
    """
    if second_tier:
        return _scan_python_classes(
            repo_root, pattern=SECOND_TIER_QUANTITY, target_default=0.0, layers=("dataclass",)
        )

    violations: list[Violation] = []
    violations += _scan_python_classes(
        repo_root, pattern=BOUNDED_SCORE_SUFFIX, target_default=PERFECT, layers=layers
    )
    if "sql" in layers:
        violations += _scan_sql(repo_root)
    if "openapi" in layers:
        violations += _scan_openapi(repo_root)
    if "typescript" in layers:
        violations += _scan_typescript(repo_root)
    return sorted(violations, key=lambda v: (v.layer, v.path, v.lineno, v.field_name))


def _scan_python_classes(
    repo_root: Path,
    *,
    pattern: re.Pattern[str],
    target_default: object,
    layers: tuple[str, ...],
) -> list[Violation]:
    wanted = {layer for layer in layers if layer in {"dataclass", "pydantic", "mapper"}}
    if not wanted:
        return []
    violations: list[Violation] = []
    for scanned_root in SCANNED_ROOTS:
        root = repo_root / scanned_root
        if not root.is_dir():
            continue
        for path in _python_files(root):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, OSError):
                # check_code_boundaries.py already reports unparseable files.
                continue
            relative = str(path.relative_to(repo_root))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if "dataclass" in wanted and _is_dataclass(node):
                    violations.extend(
                        _class_field_violations(
                            relative, node, "dataclass", target_default, pattern
                        )
                    )
                elif "pydantic" in wanted and _is_pydantic_model(node):
                    violations.extend(
                        _class_field_violations(relative, node, "pydantic", target_default, pattern)
                    )
            if "mapper" in wanted and target_default == PERFECT:
                violations.extend(_mapper_violations(relative, tree))
    return violations


def _scan_sql(repo_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for scanned_root in SQL_ROOTS:
        root = repo_root / scanned_root
        if not root.is_dir():
            continue
        for path in _source_files(root, (".sql",)):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            violations.extend(_sql_violations(str(path.relative_to(repo_root)), text))
    return violations


def _scan_openapi(repo_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for scanned_root in CONTRACT_ROOTS:
        root = repo_root / scanned_root
        if not root.is_dir():
            continue
        for path, document, raw in _openapi_documents(root):
            violations.extend(
                _openapi_violations(str(path.relative_to(repo_root)), document, raw)
            )
    return violations


def _scan_typescript(repo_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for scanned_root in CONTRACT_ROOTS:
        root = repo_root / scanned_root
        if not root.is_dir():
            continue
        for path in _source_files(root, (".ts", ".tsx")):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            violations.extend(_typescript_violations(str(path.relative_to(repo_root)), text))
    return violations


# --------------------------------------------------------------------------
# Exemptions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Exemption:
    field: str
    owner: str
    reason: str
    expires: date

    def is_expired(self, today: date) -> bool:
        return today > self.expires


def load_exemptions(path: Path) -> dict[str, Exemption]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    exemptions: dict[str, Exemption] = {}
    for entry in payload.get("exemptions", []):
        key = entry.get("field", "")
        owner = entry.get("owner", "").strip()
        reason = entry.get("reason", "").strip()
        expires_raw = entry.get("expires", "").strip()
        if not key:
            raise SystemExit("exemption entry is missing 'field'")
        if not owner or not reason:
            # An exemption without an owner is how debt goes back to being
            # invisible; the file exists to keep it attributable.
            raise SystemExit(f"exemption {key} needs both 'owner' and 'reason'")
        if not expires_raw:
            # An exemption without a date is a permanent decision wearing the
            # clothes of a temporary one.
            raise SystemExit(f"exemption {key} needs an 'expires' date (YYYY-MM-DD)")
        try:
            expires = date.fromisoformat(expires_raw)
        except ValueError:
            raise SystemExit(
                f"exemption {key} has an unreadable 'expires' value {expires_raw!r}; "
                "use YYYY-MM-DD"
            ) from None
        exemptions[key] = Exemption(field=key, owner=owner, reason=reason, expires=expires)
    return exemptions


def expired_exemptions(exemptions: dict[str, Exemption], today: date) -> list[Exemption]:
    return sorted(
        (e for e in exemptions.values() if e.is_expired(today)), key=lambda e: (e.expires, e.field)
    )


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

#: How long a newly written stub is allowed to live before it has to be either
#: fixed or re-argued. One quarter is long enough to schedule real remediation
#: and short enough that nobody inherits the entry without noticing.
DEFAULT_EXEMPTION_DAYS = 90


def _summarise_by_layer(violations: list[Violation]) -> str:
    counts: dict[str, int] = {}
    for violation in violations:
        counts[violation.layer] = counts.get(violation.layer, 0) + 1
    return ", ".join(f"{layer} {counts[layer]}" for layer in LAYERS if layer in counts) or "none"


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
    today = date.today()

    if args.write_exemptions:
        default_expiry = date.fromordinal(today.toordinal() + DEFAULT_EXEMPTION_DAYS)
        payload = {
            "_comment": (
                "Bounded scores that default to perfect, predating "
                "check_measurement_defaults.py. Each needs an owner, a reason and an "
                "expiry date. Removing an entry means the field no longer assumes "
                "perfect data."
            ),
            "exemptions": [
                {
                    "field": v.key,
                    "layer": v.layer,
                    "owner": getattr(exemptions.get(v.key), "owner", "UNASSIGNED"),
                    "reason": getattr(
                        exemptions.get(v.key), "reason", "pre-existing; not yet reviewed"
                    ),
                    "expires": getattr(
                        exemptions.get(v.key), "expires", default_expiry
                    ).isoformat(),
                }
                for v in violations
            ],
        }
        EXEMPTIONS_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Wrote {len(violations)} exemption stubs to {EXEMPTIONS_PATH.name}")
        return 0

    if args.report_second_tier:
        second = scan(REPO_ROOT, second_tier=True)
        print(
            f"Second tier (reported, not enforced): {len(second)} measured quantities "
            f"default to 0.0"
        )
        for violation in second:
            print(
                f"  - {violation.path}:{violation.lineno} "
                f"{violation.class_name}.{violation.field_name}"
            )
        print()

    failed = False

    unexempted = [v for v in violations if v.key not in exemptions]
    if unexempted:
        failed = True
        print("Measurement default checks failed:", file=sys.stderr)
        for violation in unexempted:
            print(f"  - {violation.describe()}", file=sys.stderr)
        print(
            "\nA bounded score must not default to perfect. Either make absence explicit "
            "(`float | None = None` / a nullable column / no schema default, and refuse or "
            "abstain when it is missing), or record the field in "
            "measurement_default_exemptions.json with an owner, a reason and an expiry date.",
            file=sys.stderr,
        )

    expired = expired_exemptions(exemptions, today)
    if expired:
        failed = True
        print("\nExemptions past their expiry date:", file=sys.stderr)
        for exemption in expired:
            print(
                f"  - {exemption.field} (owner {exemption.owner}, expired {exemption.expires})",
                file=sys.stderr,
            )
        print(
            "\nFix the field and delete the entry, or agree a new date with the owner and "
            "say in the reason what changed. An exemption that is simply renewed on the day "
            "it fires is a decision to keep the defect.",
            file=sys.stderr,
        )

    live = {v.key for v in violations}
    stale = sorted(set(exemptions) - live)
    if stale:
        failed = True
        print(
            "\nExemptions no longer needed (the field was fixed or removed):", file=sys.stderr
        )
        for key in stale:
            print(f"  - {key}", file=sys.stderr)
        print("\nDelete them so the file stays a list of live debt.", file=sys.stderr)

    if failed:
        return 1

    soonest = min((e.expires for e in exemptions.values()), default=None)
    print(
        f"Measurement default checks passed: {len(violations)} known "
        f"({_summarise_by_layer(violations)}), {len(exemptions)} exempted with an owner"
        + (f"; next expiry {soonest.isoformat()}." if soonest else ".")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
