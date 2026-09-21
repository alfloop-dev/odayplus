"""The OpenAPI artifact, the generated client, and the CI gate that guards them.

Criterion 6 is "OpenAPI diff and generated-client drift block unapproved
breaking changes in CI". A gate is only worth its runtime if it actually fails,
so these tests assert the failure paths, not just the happy one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from delivery_toolchain.openapi import check_drift, export_openapi, generate_client
from delivery_toolchain.openapi.export_openapi import ARTIFACT_PATH, build_schema, serialize
from delivery_toolchain.openapi.generate_client import OUTPUT_PATH, render
from delivery_toolchain.openapi.openapi_diff import diff_openapi

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


def test_avm_quality_score_is_nullable_in_artifact_and_generated_types() -> None:
    quality_score = _artifact()["components"]["schemas"]["AVMCasePayload"]["properties"][
        "quality_score"
    ]
    assert "default" not in quality_score
    assert quality_score["anyOf"] == [{"type": "number"}, {"type": "null"}]
    assert "quality_score" not in _artifact()["components"]["schemas"]["AVMCasePayload"].get(
        "required", []
    )
    assert "quality_score?: number | null;" in OUTPUT_PATH.read_text(encoding="utf-8")


def test_generated_client_exposes_only_versioned_paths() -> None:
    text = OUTPUT_PATH.read_text(encoding="utf-8")
    assert '"/api/v1/audit/events": ["GET"]' in text
    assert '\n  "/audit/events"' not in text, "a deprecated alias leaked into the client"


# --- the freshness checks fail, at the exit code CI reads ---
#
# Everything above proves the checks pass on a clean tree. A gate that passes on
# a clean tree and also passes on a dirty one is worse than no gate, so these
# point the real checks at deliberately stale inputs and assert the non-zero
# exit. Both entrypoints end in ``raise SystemExit(main())``, so a ``main()``
# return value *is* the process exit code.


def _sandbox_emitter(tmp_path: Path, generated: str) -> Path:
    """A throwaway repo root holding the real emitter and a chosen client file.

    ``generate_client.py`` resolves every path from its own ``__file__``, so
    copying it into ``tmp_path`` re-points it at the sandbox. That lets the probe
    run the real CLI against stale content without writing into the checked-in
    tree, which a killed test run would otherwise leave dirty.
    """
    tool_dir = tmp_path / "delivery_toolchain" / "openapi"
    tool_dir.mkdir(parents=True)
    shutil.copy(generate_client.__file__, tool_dir / "generate_client.py")

    artifact = tmp_path / "packages" / "openapi-client" / "openapi.json"
    artifact.parent.mkdir(parents=True)
    shutil.copy(ARTIFACT_PATH, artifact)

    output = tmp_path / "packages" / "openapi-client" / "src" / "generated" / "types.ts"
    output.parent.mkdir(parents=True)
    output.write_text(generated, encoding="utf-8")
    return tool_dir / "generate_client.py"


def _run_check(script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_client_check_cli_exits_zero_on_a_faithfully_generated_client(tmp_path: Path) -> None:
    """The positive control: without it, a probe that always fails proves nothing."""
    result = _run_check(_sandbox_emitter(tmp_path, render(_artifact())))
    assert result.returncode == 0, result.stderr


def test_client_check_cli_exits_non_zero_on_a_stale_generated_client(tmp_path: Path) -> None:
    """An operation dropped from the client is exactly the drift CI must catch."""
    fresh = render(_artifact())
    stale = fresh.replace('  "/api/v1/audit/events": ["GET"],\n', "", 1)
    assert stale != fresh, "the probe must actually make the client stale"

    result = _run_check(_sandbox_emitter(tmp_path, stale))
    assert result.returncode == 1
    assert "is stale" in result.stderr


def test_client_check_cli_exits_non_zero_when_the_client_was_never_generated(
    tmp_path: Path,
) -> None:
    script = _sandbox_emitter(tmp_path, "")
    (tmp_path / "packages" / "openapi-client" / "src" / "generated" / "types.ts").unlink()

    result = _run_check(script)
    assert result.returncode == 1
    assert "is missing" in result.stderr


def test_artifact_check_exits_non_zero_when_the_artifact_no_longer_matches_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An artifact that lost every path is the "endpoint changed, nobody
    re-exported" case, reduced to its smallest reproducible form."""
    stale = tmp_path / "openapi.json"
    stale.write_text(serialize({"openapi": "3.1.0", "paths": {}}), encoding="utf-8")
    # REPO_ROOT travels with the path: the error message renders it relative.
    monkeypatch.setattr(export_openapi, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(export_openapi, "ARTIFACT_PATH", stale)

    assert export_openapi.main(["--check"]) == 1


def test_the_contract_gate_fails_the_build_when_one_stage_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check_drift runs every stage before reporting, so a single stale stage
    must still turn the whole gate non-zero rather than being averaged away."""
    stale = tmp_path / "types.ts"
    stale.write_text("// hand-edited, never regenerated\n", encoding="utf-8")
    monkeypatch.setattr(generate_client, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(generate_client, "OUTPUT_PATH", stale)

    assert check_drift.main(["--skip-diff"]) == 1


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
        schema={
            "properties": {
                "name": {"type": "string"},
                "size": {"type": "integer"},
                "note": {"type": "string"},
            }
        },
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
        schema={
            "properties": {
                "name": {"type": "string", "description": "The name."},
                "size": {"type": "integer"},
            }
        },
    )
    assert diff_openapi(base, head) == []


def test_self_referential_schema_does_not_recurse_forever() -> None:
    """A model containing itself must not blow the stack in CI."""
    artifact = {
        "openapi": "3.1.0",
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {"child": {"$ref": "#/components/schemas/Node"}},
                }
            }
        },
        "paths": {
            "/api/v1/tree": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Node"}}
                        }
                    },
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
        (
            REPO_ROOT
            / "delivery_toolchain"
            / "openapi"
            / "approved_breaking_changes.json"
        ).read_text(encoding="utf-8")
    )
    assert isinstance(payload["approved"], list)
    for entry in payload["approved"]:
        # An approval without a reason is a mute button, not a decision.
        assert entry.get("signature"), "each approval needs the signature it waives"
        assert entry.get("reason"), f"approval {entry.get('signature')} needs a reason"
        assert entry.get("task_id"), f"approval {entry.get('signature')} needs an owning task"
