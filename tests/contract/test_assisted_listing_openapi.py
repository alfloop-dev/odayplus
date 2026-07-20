from __future__ import annotations

import json
from typing import Any
from fastapi.testclient import TestClient
from uuid import uuid4

from apps.api.oday_api.main import create_app
from scripts.generate_assisted_listing_intake_client import ARTIFACT, CLIENT, build
from scripts.openapi.generate_client import render

EXPECTED_OPERATIONS = {
    "listIntakes", "submitUrlIntake", "submitIntakeBatch", "getIntake",
    "proposeCorrection", "decideMatchCase", "mergeProperties", "splitProperty",
    "unmergeProperty", "assignIntake", "retryJob", "listSavedViews",
    "createSavedView", "requestCandidatePromotion", "getPromotionDecision",
    "reviewPromotionDecision", "cancelIntake", "quarantineIntake", "reopenIntake",
    "claimAssignment", "transferAssignment", "completeAssignment", "pauseSla",
    "resumeSla", "getIdentityDecision", "reviewIdentityDecision",
    "requestIdentityDecisionReversal",
}


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


def test_live_runtime_serves_every_effective_operation() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    app = create_app()
    live = app.openapi()
    live_paths = live["paths"]

    # 1. Path-by-path / Operation-by-operation detailed schema comparison
    def resolve_ref(spec: Any, doc: dict) -> Any:
        if isinstance(spec, dict):
            if "$ref" in spec:
                ref_path = spec["$ref"]
                parts = ref_path.lstrip("#/").split("/")
                resolved = doc
                for part in parts:
                    resolved = resolved[part]
                return resolve_ref(resolved, doc)
            return {k: resolve_ref(v, doc) for k, v in spec.items()}
        elif isinstance(spec, list):
            return [resolve_ref(item, doc) for item in spec]
        return spec

    def normalize(schema: Any) -> Any:
        if not isinstance(schema, dict):
            if isinstance(schema, list):
                return [normalize(x) for x in schema]
            return schema
        res = {}
        for k, v in schema.items():
            if k in {"title", "description", "example", "examples"}:
                continue
            res[k] = normalize(v)
        # Normalize anyOf nullability
        if "anyOf" in res:
            any_of = res["anyOf"]
            non_null = [x for x in any_of if x != {"type": "null"}]
            has_null = len(non_null) < len(any_of)
            if has_null and len(non_null) == 1:
                res.pop("anyOf")
                res.update(non_null[0])
                if "type" in res:
                    if isinstance(res["type"], list):
                        if "null" not in res["type"]:
                            res["type"] = list(res["type"]) + ["null"]
                    else:
                        res["type"] = [res["type"], "null"]
                else:
                    res["type"] = ["object", "null"]
        if "type" in res and isinstance(res["type"], list):
            if len(res["type"]) == 1:
                res["type"] = res["type"][0]
        return res

    for path, path_item in artifact["paths"].items():
        live_path = f"/api{path}"
        assert live_path in live_paths, f"runtime missing path {live_path}"
        for method, op in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            assert method in live_paths[live_path], f"runtime missing method {method.upper()} {live_path}"
            
            # Resolve and compare requestBody schemas if present
            if "requestBody" in op:
                live_op = live_paths[live_path][method]
                assert "requestBody" in live_op, f"runtime missing requestBody on {method.upper()} {live_path}"
                
                spec_rb = resolve_ref(op["requestBody"], artifact)
                live_rb = resolve_ref(live_op["requestBody"], live)
                
                spec_content = resolve_ref(spec_rb.get("content", {}).get("application/json", {}).get("schema", {}), artifact)
                live_content = resolve_ref(live_rb.get("content", {}).get("application/json", {}).get("schema", {}), live)
                
                if spec_content.get("properties"):
                    assert "properties" in live_content, f"properties missing in runtime requestBody schema for {method.upper()} {live_path}"
                    spec_props = normalize(spec_content["properties"])
                    live_props = normalize(live_content["properties"])
                    for prop_name, spec_prop in spec_props.items():
                        assert prop_name in live_props, f"property {prop_name} missing in runtime requestBody schema for {method.upper()} {live_path}"

            # Resolve and compare responses
            for status, resp in op.get("responses", {}).items():
                try:
                    status_int = int(status)
                except ValueError:
                    status_int = 200
                if status_int < 400:
                    live_op = live_paths[live_path][method]
                    if status in {"200", "201", "202", "207"}:
                        if not ({"200", "201", "202", "207"} & set(live_op["responses"])):
                            assert status in live_op["responses"], f"runtime missing response status {status} for {method.upper()} {live_path}"
                    else:
                        assert status in live_op["responses"], f"runtime missing response status {status} for {method.upper()} {live_path}"

    # 2. Schema Negative validation tests in live runtime
    client = TestClient(app)
    HEADERS_A = {
        "x-subject-id": "actor-a",
        "x-tenant-id": "tenant-a",
        "x-roles": "site_reviewer,data_owner,expansion_user",
        "x-operator-role": "expansion-manager",
    }

    # Negative Test 1: UUID format validation failure in query parameter
    resp_uuid = client.get("/api/v1/intakes", params={"submitted_by": "invalid-uuid"}, headers=HEADERS_A)
    assert resp_uuid.status_code == 422, "UUID parameter validation bypass"

    # Negative Test 2: Date-time format validation failure in request body
    intake_id = str(uuid4())
    resp_dt = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json={
            "owner_subject_id": "actor-a",
            "owner_role": "reviewer",
            "due_at": "invalid-date",
            "reason": "Triage assignment",
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-assign-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assert resp_dt.status_code == 422, "Date-time parameter validation bypass"

    # Negative Test 3: Enum validation failure in request body
    resp_enum = client.post(
        f"/api/v1/match-cases/mc-123/decisions",
        json={
            "decision_type": "INVALID_ENUM_VALUE",
            "reason": "Test enum validation",
            "risk_acknowledged": True,
            "target_property_id": "prop-123",
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-decide-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assert resp_enum.status_code == 422, "Enum parameter validation bypass"

    # Negative Test 4: minItems array length validation failure in request body
    resp_minitems = client.post(
        "/api/v1/identity/split",
        json={
            "source_property_id": "prop-target",
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
        json={"original_url": "https://example.com", "scope": {"tenant_id": "tenant-a"}},
        headers={**HEADERS_A, "Idempotency-Key": "short"}
    )
    assert resp_idem.status_code == 422, "Idempotency key format validation bypass"

    # Negative Test 6: If-Match format validation failure (missing W/ prefix)
    resp_ifmatch = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json={
            "owner_subject_id": "actor-a",
            "owner_role": "reviewer",
            "due_at": "2026-07-25T12:00:00Z",
            "reason": "Triage assignment",
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-assign-{uuid4()}", "If-Match": "1"}
    )
    assert resp_ifmatch.status_code == 400, "If-Match format validation bypass"

    # Negative Test 7: Missing If-Match
    resp_ifmatch_missing = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json={
            "owner_subject_id": "actor-a",
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
