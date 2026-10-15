"""API binds exact asynchronous execution facts to its verified Principal."""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_scoring import create_network_scoring_sub_router
from modules.opsboard.application.network_scoring import NetworkScoringService
from modules.sitescore.application.score_execution import SiteScoreExecutionService
from shared.auth import Principal, Role, Scope
from tests.unit.test_network_scoring_gate_measurement_contract import (
    NeverInfer,
    ReadOnlyListings,
    ReadOnlyReports,
    draft,
)


class Owner:
    def __init__(self):
        self.commands = []
    def create_execution(self, command, *, principal):
        self.commands.append((command, principal))
        return {"source": "canonical", "executionRequestId": "synthetic-own-request", "candidateSiteId": command.candidate_site_id,
                "jobId": "synthetic-own-job", "status": "QUEUED", "actualVersion": 1, "result": None}


def client_and_owner(*, role=Role.EXPANSION_USER):
    owner = Owner()
    record = draft()
    scorer = NetworkScoringService(seed_fixtures=False, require_canonical=True, listing_repository=ReadOnlyListings(record),
        sitescore_repository=ReadOnlyReports(), model_runtime=NeverInfer(), execution_service=SiteScoreExecutionService(owner))
    app = FastAPI()
    principal = Principal(str(uuid4()), frozenset({role}), Scope(tenant_id=str(uuid4())))
    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.operator_principal = principal
        request.state.correlation_id = str(uuid4())
        return await call_next(request)
    app.include_router(create_network_scoring_sub_router(scorer, require_view_permission_fn=lambda: None, require_write_permission_fn=lambda: None), prefix="/operator")
    return TestClient(app), owner, principal, record.candidate_site.candidate_site_id


def test_canonical_api_accepts_queued_receipt_and_ignores_body_actor_authority():
    client, owner, principal, candidate = client_and_owner()
    response = client.post(f"/operator/network-scoring/candidates/{candidate}/score", headers={"Idempotency-Key": "synthetic-key"}, json={
        "actorRoleId": "platformAdmin", "actorName": "spoofed", "expectedReportId": "saved-report", "expectedReportVersion": 2,
        "expectedJobVersion": 7, "reason": "Recorded reason"})
    assert response.status_code == 202
    assert response.json()["executionRequest"]["status"] == "QUEUED"
    assert "scorecard" not in response.json()
    assert len(owner.commands) == 1
    command, verified = owner.commands[0]
    assert verified is principal and verified.subject_id != "spoofed"
    assert command.expected_job_version == 7 and command.expected_report_version == 2
    assert response.json()["commandReceipt"] == {
        "operation": "create", "actorSubjectId": principal.subject_id,
        "tenantId": principal.tenant_id, "idempotencyKey": "synthetic-key",
        "correlationId": command.correlation_id,
        "executionRequestId": "synthetic-own-request", "jobId": "synthetic-own-job",
    }


@pytest.mark.parametrize("constraint", ["body-actor", "header-actor", "tenant"])
def test_actor_and_tenant_intent_constraints_deny_before_the_owner(constraint):
    client, owner, principal, candidate = client_and_owner()
    body = {"expectedReportId": "saved-report", "expectedReportVersion": 2,
            "expectedJobVersion": 7, "reason": "Recorded reason",
            "expectedActorSubjectId": principal.subject_id, "expectedTenantId": principal.tenant_id}
    headers = {"Idempotency-Key": "synthetic-key", "If-Actor-Match": principal.subject_id}
    if constraint == "body-actor":
        body["expectedActorSubjectId"] = str(uuid4())
    elif constraint == "header-actor":
        headers["If-Actor-Match"] = str(uuid4())
    else:
        body["expectedTenantId"] = str(uuid4())
    response = client.post(f"/operator/network-scoring/candidates/{candidate}/score",
                           headers=headers, json=body)
    assert response.status_code == 409
    assert owner.commands == []


@pytest.mark.parametrize("bad", [None, True, "2", 0, -1])
def test_invalid_exact_versions_never_reach_owner(bad):
    client, owner, _, candidate = client_and_owner()
    response = client.post(f"/operator/network-scoring/candidates/{candidate}/score", headers={"Idempotency-Key": "key"}, json={
        "expectedReportId": "saved-report", "expectedReportVersion": bad, "expectedJobVersion": 7, "reason": "reason"})
    assert response.status_code == 422
    assert owner.commands == []


def test_body_role_cannot_grant_execution_to_reader():
    client, owner, _, candidate = client_and_owner(role=Role.OPERATOR_VIEWER)
    response = client.post(f"/operator/network-scoring/candidates/{candidate}/score", headers={"Idempotency-Key": "key"}, json={
        "actorRoleId": "siteReviewer", "expectedReportId": "saved-report", "expectedReportVersion": 2, "expectedJobVersion": 7, "reason": "reason"})
    assert response.status_code == 403
    assert owner.commands == []
