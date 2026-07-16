#!/usr/bin/env python3
"""Classify the difference between two OpenAPI artifacts.

"Breaking" here means *breaking for an existing client*: a caller that was
correct against the base artifact becomes incorrect against the head one. That
is the only definition CI can act on, so it is the one implemented.

Breaking
    - an operation disappears (path or method removed)
    - a request field becomes required, or a new required field appears
    - a request field's type changes
    - an enum loses a member the client may still send
    - a declared response status disappears

Non-breaking
    - a new path, method, or optional request field
    - a new enum member (the server accepts more than before)
    - a new response status
    - description/title/example churn

Additive-but-notable changes are reported as informational so a reviewer sees
the surface grow without the build failing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

__all__ = ["Change", "diff_openapi", "BREAKING", "ADDITIVE"]

BREAKING = "breaking"
ADDITIVE = "additive"

_METHODS = ("get", "put", "post", "delete", "patch", "options", "head", "trace")


@dataclass(frozen=True)
class Change:
    """A single classified difference."""

    kind: str
    signature: str
    description: str

    @property
    def is_breaking(self) -> bool:
        return self.kind == BREAKING


def _operations(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Flatten to {"GET /api/v1/x": operation}."""
    out: dict[str, dict[str, Any]] = {}
    for path, item in (artifact.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method.lower() in _METHODS and isinstance(operation, dict):
                out[f"{method.upper()} {path}"] = operation
    return out


def _resolve(schema: Any, artifact: dict[str, Any], _seen: frozenset[str] = frozenset()) -> Any:
    """Follow ``$ref`` into ``components``.

    ``_seen`` guards against a self-referential schema (a tree node whose child
    is the same model), which would otherwise recurse until the stack blows.
    """
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return schema
    if ref in _seen:
        return {}
    node: Any = artifact
    for part in ref.lstrip("#/").split("/"):
        if not isinstance(node, dict):
            return {}
        node = node.get(part, {})
    return _resolve(node, artifact, _seen | {ref})


def _request_schema(operation: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    content = ((operation.get("requestBody") or {}).get("content") or {}).get("application/json")
    if not isinstance(content, dict):
        return {}
    resolved = _resolve(content.get("schema") or {}, artifact)
    return resolved if isinstance(resolved, dict) else {}


def _required(schema: dict[str, Any]) -> set[str]:
    value = schema.get("required")
    return set(value) if isinstance(value, list) else set()


def _type_of(schema: Any) -> str:
    """A comparable type signature, ignoring cosmetic metadata.

    Only the fields that constrain what a client may send are considered, so a
    description edit is never mistaken for a type change.
    """
    if not isinstance(schema, dict):
        return "unknown"
    if "$ref" in schema:
        return f"ref:{schema['$ref'].rsplit('/', 1)[-1]}"
    for combinator in ("anyOf", "oneOf", "allOf"):
        if combinator in schema:
            inner = sorted(_type_of(s) for s in schema[combinator])
            return f"{combinator}({','.join(inner)})"
    schema_type = schema.get("type", "unknown")
    if schema_type == "array":
        return f"array<{_type_of(schema.get('items') or {})}>"
    return str(schema_type)


def _diff_request_fields(
    name: str,
    base_op: dict[str, Any],
    head_op: dict[str, Any],
    base_artifact: dict[str, Any],
    head_artifact: dict[str, Any],
) -> list[Change]:
    changes: list[Change] = []
    base_schema = _request_schema(base_op, base_artifact)
    head_schema = _request_schema(head_op, head_artifact)
    if not base_schema and not head_schema:
        return changes

    base_props: dict[str, Any] = base_schema.get("properties") or {}
    head_props: dict[str, Any] = head_schema.get("properties") or {}
    base_required = _required(base_schema)
    head_required = _required(head_schema)

    for field in sorted(head_required - base_required):
        verb = "is now required" if field in base_props else "was added as a required field"
        changes.append(
            Change(
                BREAKING,
                f"request.required:{name}:{field}",
                f"{name}: request field {field!r} {verb}; existing callers omit it.",
            )
        )

    for field in sorted(set(base_props) & set(head_props)):
        base_type = _type_of(base_props[field])
        head_type = _type_of(head_props[field])
        if base_type != head_type:
            changes.append(
                Change(
                    BREAKING,
                    f"request.type:{name}:{field}",
                    f"{name}: request field {field!r} changed type "
                    f"({base_type} -> {head_type}).",
                )
            )
        base_enum = base_props[field].get("enum")
        head_enum = head_props[field].get("enum")
        if isinstance(base_enum, list) and isinstance(head_enum, list):
            for removed in sorted(set(map(str, base_enum)) - set(map(str, head_enum))):
                changes.append(
                    Change(
                        BREAKING,
                        f"request.enum:{name}:{field}:{removed}",
                        f"{name}: field {field!r} no longer accepts {removed!r}.",
                    )
                )

    for field in sorted(set(head_props) - set(base_props) - head_required):
        changes.append(
            Change(
                ADDITIVE,
                f"request.optional:{name}:{field}",
                f"{name}: new optional request field {field!r}.",
            )
        )
    return changes


def diff_openapi(base: dict[str, Any], head: dict[str, Any]) -> list[Change]:
    """Classify every difference from ``base`` to ``head``."""
    changes: list[Change] = []
    base_ops = _operations(base)
    head_ops = _operations(head)

    for name in sorted(set(base_ops) - set(head_ops)):
        changes.append(
            Change(
                BREAKING,
                f"operation.removed:{name}",
                f"{name}: operation removed; callers get 404/405.",
            )
        )

    for name in sorted(set(head_ops) - set(base_ops)):
        changes.append(Change(ADDITIVE, f"operation.added:{name}", f"{name}: new operation."))

    for name in sorted(set(base_ops) & set(head_ops)):
        base_op, head_op = base_ops[name], head_ops[name]
        changes.extend(_diff_request_fields(name, base_op, head_op, base, head))

        base_codes = {str(c) for c in (base_op.get("responses") or {})}
        head_codes = {str(c) for c in (head_op.get("responses") or {})}
        for code in sorted(base_codes - head_codes):
            changes.append(
                Change(
                    BREAKING,
                    f"response.removed:{name}:{code}",
                    f"{name}: response {code} no longer declared.",
                )
            )
        for code in sorted(head_codes - base_codes):
            changes.append(
                Change(
                    ADDITIVE,
                    f"response.added:{name}:{code}",
                    f"{name}: new declared response {code}.",
                )
            )
    return changes


def load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
