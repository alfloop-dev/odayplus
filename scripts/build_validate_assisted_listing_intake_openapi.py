#!/usr/bin/env python3
"""Build and validate the effective Assisted Listing Intake OpenAPI bundle.

This validator applies the committed OpenAPI Overlay documents in normative
order, writes the resulting OpenAPI 3.1 document, validates it with
openapi-spec-validator, and performs additional contract checks that previously
escaped string-presence validation.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import yaml
from jsonpath_ng.ext import parse as parse_jsonpath
from jsonpath_ng.jsonpath import Fields, Index
from openapi_spec_validator import validate_spec

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_REVIEW_MANIFEST.yaml"

def _manifest_openapi_order() -> list[Path]:
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    order = manifest.get("openapi_bundle_order")
    if not isinstance(order, list) or len(order) < 2 or not all(isinstance(item, str) for item in order):
        raise ValueError("review manifest must define openapi_bundle_order with base plus overlays")
    return [ROOT / item for item in order]

_OPENAPI_ORDER = _manifest_openapi_order()
DEFAULT_BASE = _OPENAPI_ORDER[0]
DEFAULT_OVERLAYS = _OPENAPI_ORDER[1:]
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return value


def deep_merge(current: Any, patch: Any) -> Any:
    if isinstance(current, dict) and isinstance(patch, dict):
        result = copy.deepcopy(current)
        for key, value in patch.items():
            result[key] = deep_merge(result[key], value) if key in result else copy.deepcopy(value)
        return result
    return copy.deepcopy(patch)


def replace_match(match: Any, value: Any) -> None:
    if match.context is None:
        raise ValueError("Overlay cannot replace the document root")
    parent = match.context.value
    if isinstance(match.path, Fields):
        if len(match.path.fields) != 1:
            raise ValueError(f"Unsupported multi-field target: {match.path}")
        parent[match.path.fields[0]] = value
        return
    if isinstance(match.path, Index):
        parent[match.path.index] = value
        return
    raise ValueError(f"Unsupported overlay target path type: {type(match.path).__name__}")


def remove_match(match: Any) -> None:
    if match.context is None:
        raise ValueError("Overlay cannot remove the document root")
    parent = match.context.value
    if isinstance(match.path, Fields):
        for field in match.path.fields:
            parent.pop(field, None)
        return
    if isinstance(match.path, Index):
        del parent[match.path.index]
        return
    raise ValueError(f"Unsupported overlay remove path type: {type(match.path).__name__}")


def apply_overlay(document: dict[str, Any], overlay_path: Path) -> dict[str, Any]:
    overlay = load_yaml(overlay_path)
    if overlay.get("overlay") != "1.0.0":
        raise ValueError(f"Unsupported overlay version in {overlay_path}")
    actions = overlay.get("actions")
    if not isinstance(actions, list):
        raise ValueError(f"{overlay_path} actions must be a list")

    result = copy.deepcopy(document)
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict) or not isinstance(action.get("target"), str):
            raise ValueError(f"Invalid action {index} in {overlay_path}")
        expression = parse_jsonpath(action["target"])
        matches = expression.find(result)
        if not matches:
            raise ValueError(
                f"Overlay target not found: {action['target']} "
                f"({overlay_path.name} action {index})"
            )
        if action.get("remove") is True:
            for match in sorted(
                matches,
                key=lambda item: item.path.index if isinstance(item.path, Index) else -1,
                reverse=True,
            ):
                remove_match(match)
            continue
        if "update" not in action:
            raise ValueError(f"Overlay action {index} has neither update nor remove")
        for match in matches:
            replace_match(match, deep_merge(match.value, action["update"]))
    return result


def resolve_local_ref(document: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise ValueError(f"External or invalid reference is not allowed in effective bundle: {ref}")
    current: Any = document
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            raise ValueError(f"Unresolved local reference: {ref}")
        current = current[token]
    return current


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def custom_validate(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    for node in walk(document):
        ref = node.get("$ref")
        if isinstance(ref, str):
            try:
                resolve_local_ref(document, ref)
            except ValueError as exc:
                errors.append(str(exc))

    operation_ids: dict[str, str] = {}
    paths = document.get("paths", {})
    for path_name, path_item in paths.items():
        if not isinstance(path_item, dict):
            errors.append(f"Path item is not an object: {path_name}")
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            location = f"{method.upper()} {path_name}"
            operation_id = operation.get("operationId")
            if not operation_id:
                errors.append(f"Missing operationId: {location}")
            elif operation_id in operation_ids:
                errors.append(
                    f"Duplicate operationId {operation_id}: {operation_ids[operation_id]} and {location}"
                )
            else:
                operation_ids[operation_id] = location

            responses = operation.get("responses")
            if not isinstance(responses, dict) or not responses:
                errors.append(f"Missing responses: {location}")
                continue
            for status, response in responses.items():
                if not isinstance(response, dict):
                    errors.append(f"Invalid response object: {location} {status}")
                    continue
                if "$ref" in response:
                    try:
                        resolved = resolve_local_ref(document, response["$ref"])
                    except ValueError as exc:
                        errors.append(str(exc))
                        continue
                    if not isinstance(resolved, dict) or not resolved.get("description"):
                        errors.append(f"Referenced response lacks description: {location} {status}")
                elif not response.get("description"):
                    errors.append(f"Inline response lacks description: {location} {status}")

    for name, response in document.get("components", {}).get("responses", {}).items():
        if not isinstance(response, dict) or not response.get("description"):
            errors.append(f"Component response lacks description: {name}")

    api_error = document.get("components", {}).get("schemas", {}).get("ApiError", {})
    required = set(api_error.get("required", []))
    properties = api_error.get("properties", {})
    for field in ("code", "message", "retryable", "correlation_id", "occurred_at", "next_action"):
        if field not in required:
            errors.append(f"ApiError.required missing {field}")
        if field not in properties:
            errors.append(f"ApiError.properties missing {field}")

    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--overlay", action="append", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build/assisted-listing-intake-openapi-effective.yaml",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    overlays = args.overlay or DEFAULT_OVERLAYS
    document = load_yaml(args.base)
    for overlay_path in overlays:
        document = apply_overlay(document, overlay_path)

    validation_errors: list[str] = []
    try:
        validate_spec(document)
    except Exception as exc:
        validation_errors.append(f"openapi-spec-validator: {exc}")
    validation_errors.extend(custom_validate(document))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    result = {
        "status": "PASS" if not validation_errors else "FAIL",
        "base": str(args.base.relative_to(ROOT)),
        "overlays": [str(path.relative_to(ROOT)) for path in overlays],
        "effective_version": document.get("info", {}).get("version"),
        "output": str(args.output.relative_to(ROOT)),
        "errors": validation_errors,
    }
    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Effective OpenAPI: {result['effective_version']}")
        for error in validation_errors:
            print(f"[FAIL] {error}")
        print(f"Result: {result['status']}")
    return 0 if not validation_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
