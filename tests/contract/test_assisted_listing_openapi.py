from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from scripts.generate_assisted_listing_intake_client import ARTIFACT, CLIENT, build
from scripts.openapi.generate_client import render

EXPECTED_OPERATIONS = {
    "listIntakes", "submitUrlIntake", "submitIntakeBatch", "getIntake",
    "proposeCorrection", "decideMatchCase", "mergeProperties", "splitProperty",
    "unmergeProperty", "assignIntake", "retryJob", "getJobReceipt", "listSavedViews",
    "createSavedView", "requestCandidatePromotion", "getPromotionDecision",
    "reviewPromotionDecision", "cancelIntake", "quarantineIntake", "reopenIntake",
    "claimAssignment", "transferAssignment", "completeAssignment", "pauseSla",
    "resumeSla", "getIdentityDecision", "reviewIdentityDecision",
    "requestIdentityDecisionReversal",
}

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
SCHEMA_METADATA = {
    "title", "description", "example", "examples", "default",
    "readOnly", "writeOnly", "deprecated",
}


def _resolve_refs(node: Any, document: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            resolved: Any = document
            for part in node["$ref"].removeprefix("#/").split("/"):
                resolved = resolved[part]
            siblings = {key: value for key, value in node.items() if key != "$ref"}
            return _resolve_refs({**resolved, **siblings}, document)
        return {key: _resolve_refs(value, document) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(value, document) for value in node]
    return node


def _merge_schema(base: dict[str, Any], addition: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in addition.items():
        if key == "required":
            merged[key] = sorted(set(merged.get(key, [])) | set(value))
        elif key == "properties":
            merged[key] = {**merged.get(key, {}), **value}
        elif key == "type" and key in merged:
            left = merged[key] if isinstance(merged[key], list) else [merged[key]]
            right = value if isinstance(value, list) else [value]
            merged[key] = sorted(set(left) | set(right))
        else:
            merged[key] = value
    return merged


def _canonical_schema(schema: Any) -> Any:
    if not isinstance(schema, dict):
        if isinstance(schema, list):
            return [_canonical_schema(item) for item in schema]
        return schema

    canonical = {
        key: _canonical_schema(value)
        for key, value in schema.items()
        if key not in SCHEMA_METADATA
    }
    if "allOf" in canonical:
        parts = canonical.pop("allOf")
        for part in parts:
            canonical = _merge_schema(canonical, part)

    if "anyOf" in canonical:
        variants = canonical["anyOf"]
        non_null = [variant for variant in variants if variant != {"type": "null"}]
        if len(non_null) == 1 and len(non_null) != len(variants):
            canonical.pop("anyOf")
            canonical = _merge_schema(canonical, non_null[0])
            # An unconstrained schema already admits null; adding type:null
            # would incorrectly narrow Optional[Any] to null-only.
            if non_null[0]:
                current_type = canonical.get("type")
                types = current_type if isinstance(current_type, list) else [current_type]
                canonical["type"] = sorted({value for value in types if value} | {"null"})
                if "enum" in canonical and None not in canonical["enum"]:
                    canonical["enum"] = [*canonical["enum"], None]

    if canonical.pop("nullable", False):
        current_type = canonical.get("type")
        types = current_type if isinstance(current_type, list) else [current_type]
        canonical["type"] = sorted({value for value in types if value} | {"null"})
        if "enum" in canonical and None not in canonical["enum"]:
            canonical["enum"] = [*canonical["enum"], None]
    if isinstance(canonical.get("type"), list):
        canonical["type"] = sorted(canonical["type"])
        if len(canonical["type"]) == 1:
            canonical["type"] = canonical["type"][0]
    if "required" in canonical:
        canonical["required"] = sorted(canonical["required"])
    if "enum" in canonical:
        canonical["enum"] = sorted(canonical["enum"], key=lambda value: str(value))
    if "const" in canonical:
        inferred_type = {
            bool: "boolean",
            int: "integer",
            float: "number",
            str: "string",
        }.get(type(canonical["const"]))
        if canonical.get("type") == inferred_type:
            canonical.pop("type")
    if canonical.get("additionalProperties") is True:
        canonical.pop("additionalProperties")
    return canonical


def _schema(document: dict[str, Any], schema: Any) -> Any:
    return _canonical_schema(_resolve_refs(schema, document))


def _assert_schema_equal(
    contract_document: dict[str, Any],
    contract_schema: Any,
    live_document: dict[str, Any],
    live_schema: Any,
    location: str,
    *,
    ignore_live_null: bool = False,
) -> None:
    contract_value = _schema(contract_document, contract_schema)
    live_value = _schema(live_document, live_schema)
    if ignore_live_null and isinstance(live_value, dict):
        live_types = live_value.get("type")
        if isinstance(live_types, list) and "null" in live_types:
            live_value["type"] = [value for value in live_types if value != "null"]
            if len(live_value["type"]) == 1:
                live_value["type"] = live_value["type"][0]
        if isinstance(live_value.get("enum"), list) and None in live_value["enum"]:
            live_value["enum"] = [value for value in live_value["enum"] if value is not None]

    assert live_value == contract_value, f"{location}: exact schema drift"


def test_committed_artifact_is_the_effective_five_overlay_bundle() -> None:
    assert json.loads(ARTIFACT.read_text()) == build()


def test_every_approved_operation_reaches_generated_client() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    operations = {op["operationId"] for item in artifact["paths"].values()
                  for method, op in item.items() if method in {"get", "post", "put", "patch", "delete"}}
    assert operations == EXPECTED_OPERATIONS
    text = CLIENT.read_text()
    normalized = dict(artifact)
    normalized["paths"] = {f"/api{path}": item for path, item in artifact["paths"].items()}
    assert text == render(normalized)
    for path in artifact["paths"]:
        assert f'"/api{path}"' in text


def test_live_runtime_request_and_response_schema_match_every_effective_operation() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    app = create_app()
    live = app.openapi()
    live_paths = live["paths"]

    for path, path_item in artifact["paths"].items():
        live_path = f"/api{path}"
        assert live_path in live_paths, f"runtime missing path {live_path}"
        for method, op in path_item.items():
            if method not in HTTP_METHODS:
                continue
            assert method in live_paths[live_path], f"runtime missing method {method.upper()} {live_path}"
            live_op = live_paths[live_path][method]
            operation_id = op["operationId"]
            assert live_op["operationId"] == operation_id
            assert set(live_op.get("responses", {})) == set(op.get("responses", {})), (
                f"runtime response statuses drifted for {operation_id}"
            )

            contract_parameters = {
                (parameter["in"], parameter["name"]): parameter
                for parameter in _resolve_refs(op.get("parameters", []), artifact)
            }
            live_parameters = {
                (parameter["in"], parameter["name"]): parameter
                for parameter in _resolve_refs(live_op.get("parameters", []), live)
            }
            assert set(contract_parameters) <= set(live_parameters), (
                f"runtime parameters drifted for {operation_id}"
            )
            for key, contract_parameter in contract_parameters.items():
                live_parameter = live_parameters[key]
                assert bool(live_parameter.get("required")) == bool(contract_parameter.get("required")), (
                    f"required parameter drift at {operation_id} {key}"
                )
                _assert_schema_equal(
                    artifact,
                    contract_parameter.get("schema", {}),
                    live,
                    live_parameter.get("schema", {}),
                    f"parameter schema drift at {operation_id} {key}",
                    ignore_live_null=not contract_parameter.get("required", False),
                )

            if "requestBody" in op:
                assert "requestBody" in live_op, f"runtime missing requestBody on {method.upper()} {live_path}"
                contract_body = _resolve_refs(op["requestBody"], artifact)
                live_body = _resolve_refs(live_op["requestBody"], live)
                assert bool(live_body.get("required")) == bool(contract_body.get("required"))
                _assert_schema_equal(
                    artifact,
                    contract_body["content"]["application/json"]["schema"],
                    live,
                    live_body["content"]["application/json"]["schema"],
                    f"request body schema drift at {operation_id}",
                )

            for status, resp in op.get("responses", {}).items():
                assert status in live_op["responses"], (
                    f"runtime missing response {status} for {operation_id}"
                )
                contract_response = _resolve_refs(resp, artifact)
                live_response = _resolve_refs(live_op["responses"][status], live)
                contract_headers = contract_response.get("headers", {})
                live_headers = live_response.get("headers", {})
                assert set(live_headers) == set(contract_headers), (
                    f"response header drift at {operation_id} {status}"
                )
                for header_name, contract_header in contract_headers.items():
                    _assert_schema_equal(
                        artifact,
                        contract_header.get("schema", {}),
                        live,
                        live_headers[header_name].get("schema", {}),
                        f"response header schema drift at {operation_id} {status} {header_name}",
                    )
                contract_schema = contract_response.get("content", {}).get("application/json", {}).get("schema")
                live_schema = live_response.get("content", {}).get("application/json", {}).get("schema")
                assert (contract_schema is None) == (live_schema is None), (
                    f"response content drift at {operation_id} {status}"
                )
                if contract_schema is not None:
                    _assert_schema_equal(
                        artifact,
                        contract_schema,
                        live,
                        live_schema,
                        f"response schema drift at {operation_id} {status}",
                    )

    # 2. Schema Negative validation tests in live runtime
    client = TestClient(app)
    HEADERS_A = {
        "x-subject-id": "00000000-0000-0000-0000-000000000101",
        "x-tenant-id": "00000000-0000-0000-0000-000000000001",
        "x-roles": "site_reviewer,data_owner,expansion_user",
        "x-operator-role": "expansion-manager",
    }

    # Negative Test 1: UUID format validation failure in query parameter
    resp_uuid = client.get("/api/v1/intakes", params={"submitted_by": "invalid-uuid"}, headers=HEADERS_A)
    assert resp_uuid.status_code == 400, "UUID query validation bypass"

    # Negative Test 1b: malformed GET identifiers follow the declared 404
    # resource contract instead of leaking FastAPI's undeclared 422.
    resp_path_uuid = client.get("/api/v1/intakes/invalid-uuid", headers=HEADERS_A)
    assert resp_path_uuid.status_code == 404, "UUID path validation bypass"

    # Negative Test 2: Date-time format validation failure in request body
    intake_id = str(uuid4())
    resp_dt = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json={
            "owner_subject_id": "00000000-0000-0000-0000-000000000101",
            "owner_role": "reviewer",
            "due_at": "invalid-date",
            "reason": "Triage assignment",
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-assign-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assert resp_dt.status_code == 422, "Date-time parameter validation bypass"

    # Negative Test 3: Enum validation failure in request body
    resp_enum = client.post(
        f"/api/v1/match-cases/{uuid4()}/decisions",
        json={
            "decision_type": "INVALID_ENUM_VALUE",
            "reason": "Test enum validation",
            "risk_acknowledged": True,
            "target_property_id": str(uuid4()),
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-decide-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assert resp_enum.status_code == 422, "Enum parameter validation bypass"

    # Negative Test 4: minItems array length validation failure in request body
    resp_minitems = client.post(
        "/api/v1/identity/split",
        json={
            "source_property_id": str(uuid4()),
            "partitions": [],  # Empty partitions array, schema requires minItems: 2
            "reason": "Property split by steward validation check",
            "risk_acknowledged": True
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-split-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assert resp_minitems.status_code == 422, "minItems array length validation bypass"

    # Negative Test 5: Idempotency-Key validation failure (length < 16)
    resp_idem = client.post(
        "/api/v1/intakes/url",
        json={"original_url": "https://example.com", "scope": {"tenant_id": "00000000-0000-0000-0000-000000000001"}},
        headers={**HEADERS_A, "Idempotency-Key": "short"}
    )
    assert resp_idem.status_code == 422, "Idempotency key format validation bypass"

    # Negative Test 6: If-Match format validation failure (missing W/ prefix)
    resp_ifmatch = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json={
            "owner_subject_id": "00000000-0000-0000-0000-000000000101",
            "owner_role": "reviewer",
            "due_at": "2026-07-25T12:00:00Z",
            "reason": "Triage assignment",
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-assign-{uuid4()}", "If-Match": "1"}
    )
    assert resp_ifmatch.status_code == 422, "If-Match format validation bypass"

    # Negative Test 7: Missing If-Match
    resp_ifmatch_missing = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json={
            "owner_subject_id": "00000000-0000-0000-0000-000000000101",
            "owner_role": "reviewer",
            "due_at": "2026-07-25T12:00:00Z",
            "reason": "Triage assignment",
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-assign-{uuid4()}"}
    )
    assert resp_ifmatch_missing.status_code == 428, "If-Match missing check bypass"


def test_high_impact_operations_declare_replay_and_concurrency() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    parameters = artifact["components"]["parameters"]
    assert parameters["IdempotencyKey"]["required"] is True
    assert parameters["IfMatch"]["required"] is True
    for item in artifact["paths"].values():
        for method, operation in item.items():
            if method not in {"post", "put", "patch", "delete"}:
                continue
            refs = {p.get("$ref", "") for p in operation.get("parameters", [])}
            if any(ref.endswith("/IfMatch") for ref in refs):
                assert any(ref.endswith("/IdempotencyKey") for ref in refs)
                assert "428" in operation["responses"]


def test_error_and_masking_contracts_are_generated() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    schemas = artifact["components"]["schemas"]
    assert set(schemas["ApiError"]["required"]) >= {"occurred_at", "next_action", "correlation_id"}
    assert set(schemas["FieldValue"]["required"]) >= {"classification", "masked"}
    assert "masked_fields" in schemas["IntakeSummary"]["properties"]
