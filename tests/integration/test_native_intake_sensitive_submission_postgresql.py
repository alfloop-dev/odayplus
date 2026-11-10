"""Sensitive native submissions: in-process HTTP, real owned SQL, synthetic values."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.listings import create_assisted_intake_router
from shared.api.errors import install_error_handlers
from tests.integration.test_canonical_intakes_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_canonical_intakes_postgresql import command, snapshot
from tests.integration.test_canonical_intakes_postgresql import native_api as native_api

pytestmark = pytest.mark.requires_live_env
SECRET = "synthetic-private-material-12345"


@contextmanager
def api_with_handlers(native_api, enabled):
    client, _, store = native_api
    if not enabled:
        yield client
        return
    app = FastAPI()
    app.state.persistence_bundle = client.app.state.persistence_bundle
    app.state.audit_log = client.app.state.audit_log
    install_error_handlers(app)
    app.include_router(create_assisted_intake_router(store), prefix="/api/v1")
    with TestClient(app) as protected:
        yield protected


@pytest.mark.parametrize("query", [
    "access_token", "api_key", "apikey", "auth", "authorization", "bearer", "cookie", "credential",
    "jwt", "password", "private_api_endpoint", "refresh_token", "secret", "session", "sessionid", "token",
    "ACCESS_TOKEN", "%61ccess_token",
])
def test_sensitive_query_is_rejected_without_echo_or_any_write(canonical_graph, native_api, query):
    client, _, _ = native_api
    before = snapshot(canonical_graph)
    response = client.post("/api/v1/canonical-intakes", json=command(intake_method="URL",
        original_url=f"https://synthetic.example/listing?{query}={SECRET}"),
        headers={"Idempotency-Key": "synthetic-sensitive-query-" + uuid4().hex})
    after = snapshot(canonical_graph)
    observed = {"status": response.status_code, "intake_delta": len(after["intake.intakes"]) - len(before["intake.intakes"]),
                "audit_delta": len(after["audit.audit_events"]) - len(before["audit.audit_events"])}
    assert observed == {"status": 422, "intake_delta": 0, "audit_delta": 0}, observed
    assert SECRET not in response.text
    assert after == before


@pytest.mark.parametrize("global_handlers", [False, True])
@pytest.mark.parametrize("body", [
    command(intake_method="URL", original_url=f"https://user:{SECRET}@synthetic.example/listing"),
    command(reason=f"Authorization: Bearer {SECRET}"),
    command(reason=f"Cookie: session={SECRET}"),
    command(api_key=SECRET),
    command(scope={"brand_id": SECRET}),
    command(scope={"credentials": {"password": SECRET}}),
    command(intake_method="URL", original_url={"password": SECRET}),
])
def test_all_submission_validation_errors_hide_input_and_preserve_sql(canonical_graph, native_api, global_handlers, body):
    before = snapshot(canonical_graph)
    with api_with_handlers(native_api, global_handlers) as client:
        response = client.post("/api/v1/canonical-intakes", json=body,
            headers={"Idempotency-Key": "synthetic-sensitive-invalid-" + uuid4().hex})
    after = snapshot(canonical_graph)
    assert response.status_code == 422, response.text
    assert SECRET not in response.text, response.text
    assert after == before


@pytest.mark.parametrize("global_handlers", [False, True])
def test_malformed_json_is_not_reflected(canonical_graph, native_api, global_handlers):
    before = snapshot(canonical_graph)
    # A decoder error must not reflect an otherwise unparsed credential value.
    with api_with_handlers(native_api, global_handlers) as client:
        response = client.post("/api/v1/canonical-intakes", content='{"password":"' + SECRET + '",',
            headers={"Content-Type": "application/json", "Idempotency-Key": "synthetic-invalid-json-0001"})
    assert response.status_code == 422, response.text
    assert SECRET not in response.text
    assert snapshot(canonical_graph) == before


@pytest.mark.parametrize("url", ["http://127.0.0.1/listing", "http://169.254.169.254/latest/meta-data",
                                 "http://metadata.google.internal/listing"])
def test_native_url_uses_existing_public_target_boundary(canonical_graph, native_api, url):
    client, _, _ = native_api
    before = snapshot(canonical_graph)
    response = client.post("/api/v1/canonical-intakes", json=command(intake_method="URL", original_url=url),
        headers={"Idempotency-Key": "synthetic-private-target-0001"})
    assert response.status_code == 422, response.text
    assert snapshot(canonical_graph) == before


@pytest.mark.parametrize("url", [None, "https://synthetic.example/Original?id=synthetic-public-listing-17",
                                 "https://synthetic.example/Original?private=synthetic-value"])
def test_public_unknown_source_and_manual_remain_submitted_and_replay(canonical_graph, native_api, url):
    client, _, _ = native_api
    body = command() if url is None else command(intake_method="URL", original_url=url)
    headers = {"Idempotency-Key": "synthetic-public-control-0001"}
    before = snapshot(canonical_graph)
    response = client.post("/api/v1/canonical-intakes", json=body, headers=headers)
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["processing_state"] == "SUBMITTED" and receipt["version"] == 1
    assert "job_id" not in receipt and "match_case_id" not in receipt
    after = snapshot(canonical_graph)
    row = next(r for r in after["intake.intakes"] if r["intake_id"] == receipt["intake_id"])
    assert row["original_url"] == url and row["source_id"] is None and row["canonical_url"] is None
    for table in ("identity.match_cases", "expansion.listings", "workflow.jobs"):
        assert after[table] == before[table]
    replay = client.post("/api/v1/canonical-intakes", json=body, headers=headers)
    assert replay.status_code == 201 and replay.json() == receipt
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert snapshot(canonical_graph) == after
    assert graph_audit_ok(canonical_graph)


def graph_audit_ok(case):
    graph, ids, _, _ = case
    return graph.audit_sink.writer.verify_chain(ids["tenant"]).ok


@pytest.mark.parametrize("fragment", ["access_token=", "api_key=", "/listing?ACCESS_TOKEN=", "/listing?%61ccess_token="])
def test_fragment_credentials_are_rejected_without_echo_or_write(canonical_graph, native_api, fragment):
    client, _, _ = native_api
    before = snapshot(canonical_graph)
    response = client.post("/api/v1/canonical-intakes", json=command(intake_method="URL",
        original_url="https://synthetic.example/listing#" + fragment + SECRET),
        headers={"Idempotency-Key": "synthetic-fragment-secret-0001"})
    after = snapshot(canonical_graph)
    observed = {"status": response.status_code, "intake_delta": len(after["intake.intakes"]) - len(before["intake.intakes"])}
    assert observed == {"status": 422, "intake_delta": 0}, observed
    assert SECRET not in response.text
    assert after == before


@pytest.mark.parametrize("fragment", ["details", "/listing?id=synthetic-public-17"])
def test_public_fragment_original_evidence_is_preserved(canonical_graph, native_api, fragment):
    client, _, _ = native_api
    url = "https://synthetic.example/listing#" + fragment
    response = client.post("/api/v1/canonical-intakes", json=command(intake_method="URL", original_url=url),
        headers={"Idempotency-Key": "synthetic-fragment-public-0001"})
    assert response.status_code == 201, response.text
    assert response.json()["processing_state"] == "SUBMITTED"
    after = snapshot(canonical_graph)
    row = next(r for r in after["intake.intakes"] if r["intake_id"] == response.json()["intake_id"])
    assert row["original_url"] == url and row["canonical_url"] is None and row["source_id"] is None


@pytest.mark.parametrize("key", ["access_token", "api_key"])
def test_fragment_delimiter_cannot_hide_a_leading_credential_key(canonical_graph, native_api, key):
    client, _, _ = native_api
    before = snapshot(canonical_graph)
    response = client.post("/api/v1/canonical-intakes", json=command(intake_method="URL",
        original_url=f"https://synthetic.example/listing#{key}={SECRET}?public=control"),
        headers={"Idempotency-Key": "synthetic-fragment-delimiter-0001"})
    after = snapshot(canonical_graph)
    observed = {"status": response.status_code, "intake_delta": len(after["intake.intakes"]) - len(before["intake.intakes"])}
    assert observed == {"status": 422, "intake_delta": 0}, observed
    assert SECRET not in response.text
    assert after == before
