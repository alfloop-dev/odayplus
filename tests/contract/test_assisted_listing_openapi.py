from __future__ import annotations

import json

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
    live_paths = create_app().openapi()["paths"]
    for path, item in artifact["paths"].items():
        live = live_paths[f"/api{path}"]
        for method in item:
            if method in {"get", "post", "put", "patch", "delete"}:
                assert method in live, f"runtime missing {method.upper()} /api{path}"


def test_high_impact_operations_declare_replay_and_concurrency() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    parameters = artifact["components"]["parameters"]
    assert parameters["IdempotencyKey"]["required"] is True
    assert parameters["IfMatch"]["required"] is True
    for item in artifact["paths"].values():
        for method, operation in item.items():
            if method not in {"post", "put", "patch", "delete"}: continue
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
