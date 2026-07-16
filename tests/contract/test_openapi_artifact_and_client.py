"""The OpenAPI artifact, the generated client, and the CI gate that guards them.

Criterion 6 is "OpenAPI diff and generated-client drift block unapproved
breaking changes in CI". A gate is only worth its runtime if it actually fails,
so these tests assert the failure paths, not just the happy one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.openapi.export_openapi import ARTIFACT_PATH, build_schema, serialize
from scripts.openapi.generate_client import OUTPUT_PATH, render
from scripts.openapi.openapi_diff import diff_openapi

REPO_ROOT = Path(__file__).resolve().parents[2]


def _artifact() -> dict[str, Any]:
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


def _operation(path: str, method: str = "post", **overrides: Any) -> dict[str, Any]:
    """A minimal artifact with one operation, for the diff tests."""
    schema: dict[str, Any] = {
        "properties": {"name": {"type": "string"}, "size": {"type": "integer"}},
        "required": ["name"],
        "type": "object",
    }
    schema.update(overrides.pop("schema", {}))
    return {
        "openapi": "3.1.0",
        "paths": {
            path: {
                method: {
                    "requestBody": {"content": {"application/json": {"schema": schema}}},
                    "responses": overrides.pop("responses", {"200": {}}),
                }
            }
        },
    }


# --- the checked-in artifact is real, fresh, and describes the versioned API ---


def test_artifact_is_checked_in_and_matches_the_live_app() -> None:
    """The artifact is exported from the app, never hand-written."""
    assert ARTIFACT_PATH.exists(), "the OpenAPI artifact must be committed"
    assert ARTIFACT_PATH.read_text(encoding="utf-8") == serialize(build_schema())


def test_generated_client_matches_the_artifact() -> None:
    assert OUTPUT_PATH.exists(), "the generated client must be committed"
    assert OUTPUT_PATH.read_text(encoding="utf-8") == render(_artifact())


def test_generated_client_is_marked_do_not_edit() -> None:
    assert "DO NOT EDIT" in OUTPUT_PATH.read_text(encoding="utf-8")


def test_artifact_export_is_deterministic() -> None:
    """Byte-stable across runs, or the drift gate would flap."""
    assert serialize(build_schema()) == serialize(build_schema())


def test_artifact_documents_the_error_envelope() -> None:
    """The envelope is contract, so it must reach the generated client."""
    envelope = _artifact()["components"]["schemas"]["ErrorEnvelope"]["properties"]
    assert set(envelope) >= {
        "code",
        "message",
        "next_action",
        "occurred_at",
        "details",
        "correlation_id",
    }


def test_generated_client_exposes_only_versioned_paths() -> None:
    text = OUTPUT_PATH.read_text(encoding="utf-8")
    assert '"/api/v1/audit/events": ["GET"]' in text
    assert '\n  "/audit/events"' not in text, "a deprecated alias leaked into the client"


# --- the diff classifier ---


def test_removing_an_operation_is_breaking() -> None:
    base = _operation("/api/v1/things")
    head = {"openapi": "3.1.0", "paths": {}}
    changes = [c for c in diff_openapi(base, head) if c.is_breaking]
    assert [c.signature for c in changes] == ["operation.removed:POST /api/v1/things"]


def test_adding_an_operation_is_additive() -> None:
    base = {"openapi": "3.1.0", "paths": {}}
    head = _operation("/api/v1/things")
    changes = diff_openapi(base, head)
    assert changes and not any(c.is_breaking for c in changes)


def test_new_required_request_field_is_breaking() -> None:
    """Existing callers do not send it, so every one of them starts failing."""
    base = _operation("/api/v1/things")
    head = _operation("/api/v1/things", schema={"required": ["name", "size"]})
    breaking = [c for c in diff_openapi(base, head) if c.is_breaking]
    assert [c.signature for c in breaking] == ["request.required:POST /api/v1/things:size"]


def test_new_optional_request_field_is_not_breaking() -> None:
    base = _operation("/api/v1/things")
    head = _operation(
        "/api/v1/things",
        schema={"properties": {"name": {"type": "string"}, "size": {"type": "integer"},
                               "note": {"type": "string"}}},
    )
    assert not any(c.is_breaking for c in diff_openapi(base, head))


def test_request_field_type_change_is_breaking() -> None:
    base = _operation("/api/v1/things")
    head = _operation(
        "/api/v1/things",
        schema={"properties": {"name": {"type": "string"}, "size": {"type": "string"}}},
    )
    breaking = [c for c in diff_openapi(base, head) if c.is_breaking]
    assert [c.signature for c in breaking] == ["request.type:POST /api/v1/things:size"]


def test_removing_an_enum_member_is_breaking_and_adding_one_is_not() -> None:
    """Direction matters: the server accepting *more* cannot break a caller."""
    with_enum = {"properties": {"name": {"type": "string", "enum": ["a", "b"]}}}
    narrowed = {"properties": {"name": {"type": "string", "enum": ["a"]}}}
    widened = {"properties": {"name": {"type": "string", "enum": ["a", "b", "c"]}}}

    removed = diff_openapi(
        _operation("/api/v1/t", schema=with_enum), _operation("/api/v1/t", schema=narrowed)
    )
    assert [c.signature for c in removed if c.is_breaking] == ["request.enum:POST /api/v1/t:name:b"]

    added = diff_openapi(
        _operation("/api/v1/t", schema=with_enum), _operation("/api/v1/t", schema=widened)
    )
    assert not any(c.is_breaking for c in added)


def test_removing_a_declared_response_is_breaking() -> None:
    base = _operation("/api/v1/things", responses={"200": {}, "404": {}})
    head = _operation("/api/v1/things", responses={"200": {}})
    breaking = [c for c in diff_openapi(base, head) if c.is_breaking]
    assert [c.signature for c in breaking] == ["response.removed:POST /api/v1/things:404"]


def test_description_only_change_is_not_reported() -> None:
    """Copy edits must not trip the gate, or reviewers learn to ignore it."""
    base = _operation("/api/v1/things")
    head = _operation(
        "/api/v1/things",
        schema={"properties": {"name": {"type": "string", "description": "The name."},
                               "size": {"type": "integer"}}},
    )
    assert diff_openapi(base, head) == []


def test_self_referential_schema_does_not_recurse_forever() -> None:
    """A model containing itself must not blow the stack in CI."""
    artifact = {
        "openapi": "3.1.0",
        "components": {
            "schemas": {"Node": {"type": "object", "properties": {
                "child": {"$ref": "#/components/schemas/Node"}}}}
        },
        "paths": {
            "/api/v1/tree": {
                "post": {
                    "requestBody": {"content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/Node"}}}},
                    "responses": {"200": {}},
                }
            }
        },
    }
    assert diff_openapi(artifact, artifact) == []


def test_diffing_the_real_artifact_against_itself_is_clean() -> None:
    """No change must read as a change, or the gate cries wolf."""
    artifact = _artifact()
    assert diff_openapi(artifact, artifact) == []


# --- the approvals file ---


def test_approved_breaking_changes_file_is_valid_and_reviewed() -> None:
    payload = json.loads(
        (REPO_ROOT / "scripts" / "openapi" / "approved_breaking_changes.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(payload["approved"], list)
    for entry in payload["approved"]:
        # An approval without a reason is a mute button, not a decision.
        assert entry.get("signature"), "each approval needs the signature it waives"
        assert entry.get("reason"), f"approval {entry.get('signature')} needs a reason"
        assert entry.get("task_id"), f"approval {entry.get('signature')} needs an owning task"
