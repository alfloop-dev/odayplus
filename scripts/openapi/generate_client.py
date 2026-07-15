#!/usr/bin/env python3
"""Generate TypeScript client types from the checked-in OpenAPI artifact.

Reads ``packages/openapi-client/openapi.json`` and writes
``packages/openapi-client/src/generated/types.ts``. The output is derived
entirely from the artifact -- which is itself exported from the live app -- so a
backend change that is not reflected in the client makes CI fail rather than
reaching a caller as a runtime shape mismatch.

A hand-rolled emitter is used rather than ``openapi-typescript`` deliberately:
the generated surface is narrow (JSON Schema draft subset that FastAPI emits),
and adding an npm generator would put a network install on the critical path of
the drift gate, which must be reproducible offline and identical on every
machine.

What is generated
-----------------
* Every ``components.schemas`` entry -- all request payload DTOs, plus the
  ``ErrorResponse``/``ErrorEnvelope`` and ``Page`` envelopes.
* ``ApiPaths``: every versioned operation, so a caller cannot invent a URL.

What is not generated, and why
------------------------------
Response DTOs. Every route is annotated ``-> dict[str, Any]``, so FastAPI infers
``additionalProperties: true`` for all 156 success responses -- the artifact
genuinely carries no response shape to generate from. Those DTOs remain
hand-written and are quarantined in ``src/handwritten.ts`` with the reason
recorded there. Declaring ``response_model=`` per route is the fix, but it is
not a mechanical one: ``response_model`` *filters* the response to the declared
fields, so an incomplete model silently drops data the console renders. That
work is tracked as a follow-up and must be done per-route with its tests.

Usage::

    python3 scripts/openapi/generate_client.py           # write
    python3 scripts/openapi/generate_client.py --check    # fail if stale
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_PATH = REPO_ROOT / "packages" / "openapi-client" / "openapi.json"
OUTPUT_PATH = REPO_ROOT / "packages" / "openapi-client" / "src" / "generated" / "types.ts"

HEADER = """/**
 * GENERATED FILE — DO NOT EDIT.
 *
 * Source:    packages/openapi-client/openapi.json
 * Generator: scripts/openapi/generate_client.py
 *
 * Regenerate with:
 *   python3 scripts/openapi/export_openapi.py     # refresh the artifact from the app
 *   python3 scripts/openapi/generate_client.py    # refresh this file
 *
 * CI runs `scripts/openapi/check_drift.py`, which fails if this file does not
 * match the artifact, or the artifact does not match the live API.
 */

/* eslint-disable */
"""


def _sanitize(name: str) -> str:
    """Turn an OpenAPI component name into a valid TS identifier."""
    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    return f"_{cleaned}" if cleaned and cleaned[0].isdigit() else cleaned


def _quote_key(key: str) -> str:
    """Quote an object key unless it is a plain JS identifier."""
    return key if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", key) else json.dumps(key)


def _render_type(schema: Any, indent: int = 0) -> str:
    """Render a JSON Schema node as a TypeScript type expression.

    Handles the subset FastAPI/Pydantic v2 emits: $ref, enum, const, anyOf/
    oneOf/allOf, arrays, objects, and the primitive types. Anything unrecognised
    degrades to `unknown` rather than `any`, so an unmodelled corner stays
    type-checked at the call site instead of silently opting out.
    """
    if not isinstance(schema, dict) or not schema:
        return "unknown"

    if "$ref" in schema:
        return _sanitize(schema["$ref"].rsplit("/", 1)[-1])

    if "const" in schema:
        return json.dumps(schema["const"])

    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"]) or "never"

    for combinator in ("anyOf", "oneOf"):
        if combinator in schema:
            parts = [_render_type(s, indent) for s in schema[combinator]]
            # De-duplicate while preserving order: Optional[X] emits
            # [X, null] and unions of aliases can repeat.
            seen: list[str] = []
            for part in parts:
                if part not in seen:
                    seen.append(part)
            return " | ".join(seen) or "unknown"

    if "allOf" in schema:
        parts = [_render_type(s, indent) for s in schema["allOf"]]
        return " & ".join(parts) if parts else "unknown"

    schema_type = schema.get("type")

    if schema_type == "array":
        item = _render_type(schema.get("items", {}), indent)
        # Parenthesise unions so `A | B[]` cannot be misparsed.
        return f"({item})[]" if "|" in item or "&" in item else f"{item}[]"

    if schema_type == "object" or "properties" in schema:
        return _render_object(schema, indent)

    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(schema_type, "unknown")


def _render_object(schema: dict[str, Any], indent: int) -> str:
    properties: dict[str, Any] = schema.get("properties") or {}
    required: set[str] = set(schema.get("required") or [])
    additional = schema.get("additionalProperties")

    if not properties:
        if additional is True or additional is None:
            return "Record<string, unknown>"
        if isinstance(additional, dict):
            return f"Record<string, {_render_type(additional, indent)}>"
        return "Record<string, never>"

    pad = "  " * (indent + 1)
    lines: list[str] = ["{"]
    for prop_name, prop_schema in properties.items():
        description = (prop_schema or {}).get("description")
        if description:
            lines.append(f"{pad}/** {description} */")
        optional = "" if prop_name in required else "?"
        rendered = _render_type(prop_schema, indent + 1)
        lines.append(f"{pad}{_quote_key(prop_name)}{optional}: {rendered};")
    if isinstance(additional, dict):
        lines.append(f"{pad}[key: string]: {_render_type(additional, indent + 1)};")
    lines.append("  " * indent + "}")
    return "\n".join(lines)


def _render_schemas(schemas: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    # sorted(): the emitted file must be byte-stable for the drift check.
    for name in sorted(schemas):
        schema = schemas[name] or {}
        description = schema.get("description") or schema.get("title")
        doc = f"/** {description} */\n" if description else ""
        blocks.append(f"{doc}export type {_sanitize(name)} = {_render_type(schema)};")
    return blocks


def _render_paths(paths: dict[str, Any]) -> str:
    """Emit the versioned operation map.

    Only ``/api/v1`` operations appear: the unversioned aliases are mounted with
    ``include_in_schema=False`` precisely so a generated client can never target
    a deprecated path.
    """
    rows: list[str] = []
    for path in sorted(paths):
        if not path.startswith("/api/v1"):
            continue
        methods = sorted(m.upper() for m in paths[path] if m.lower() != "parameters")
        rows.append(f"  {json.dumps(path)}: [{', '.join(json.dumps(m) for m in methods)}],")
    body = "\n".join(rows)
    return (
        "/** Every versioned operation the API serves, and its methods. */\n"
        "export const API_PATHS = {\n"
        f"{body}\n"
        "} as const;\n\n"
        "export type ApiPath = keyof typeof API_PATHS;"
    )


def render(artifact: dict[str, Any]) -> str:
    schemas = artifact.get("components", {}).get("schemas", {})
    info = artifact.get("info", {})
    parts: list[str] = [
        HEADER,
        f"/** OpenAPI {artifact.get('openapi', '?')} — "
        f"{info.get('title', 'API')} v{info.get('version', '?')} */",
        f"export const API_VERSION = {json.dumps(str(info.get('version', '')))};",
        "",
        *_render_schemas(schemas),
        "",
        _render_paths(artifact.get("paths", {})),
    ]
    return "\n\n".join(part for part in parts if part is not None).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Exit non-zero if stale.")
    args = parser.parse_args(argv)

    if not ARTIFACT_PATH.exists():
        print(f"ERROR: {ARTIFACT_PATH.relative_to(REPO_ROOT)} is missing.", file=sys.stderr)
        print("Run: python3 scripts/openapi/export_openapi.py", file=sys.stderr)
        return 1

    payload = render(json.loads(ARTIFACT_PATH.read_text(encoding="utf-8")))

    if args.check:
        if not OUTPUT_PATH.exists():
            print(f"ERROR: {OUTPUT_PATH.relative_to(REPO_ROOT)} is missing.", file=sys.stderr)
            print("Run: python3 scripts/openapi/generate_client.py", file=sys.stderr)
            return 1
        if OUTPUT_PATH.read_text(encoding="utf-8") != payload:
            print(
                f"ERROR: {OUTPUT_PATH.relative_to(REPO_ROOT)} is stale — the OpenAPI "
                "artifact changed but the client was not regenerated.",
                file=sys.stderr,
            )
            print("Run: python3 scripts/openapi/generate_client.py", file=sys.stderr)
            return 1
        print(f"OK: {OUTPUT_PATH.relative_to(REPO_ROOT)} matches the artifact.")
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(payload, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
