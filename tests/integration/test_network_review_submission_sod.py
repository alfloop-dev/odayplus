"""Owned local signed-account SoD regressions; no production grants or inference."""

import json
import os
import shlex
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes.sitescore import create_sitescore_router
from apps.api.oday_api.security import dependencies
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    SigningKey,
    encode_compact_jwt,
)
from modules.sitescore import InMemorySiteScoreRepository
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.infrastructure.persistence.repositories import DurableApprovalRepository
from shared.workflow.sitescore import DecisionAction, SiteScoreDecisionWorkflow
from tests.integration.test_network_review_canonical_atomicity import durable_bytes
from tests.unit.test_network_canonical_review_projection import report

BASE = "/api/v1/operator/network-reviews"
ORIGINAL = "00000000-0000-0000-0000-000000000102"
RESUBMITTER = "00000000-0000-0000-0000-000000000297"
CHECKER = "00000000-0000-0000-0000-000000000298"
pytestmark = pytest.mark.requires_live_env


def _record(case, value):
    """Optional owned evidence only; JWTs/private connection parameters excluded."""
    directory = os.environ.get("ODP_SUBMIT_SOD_EVIDENCE_DIR")
    if directory:
        target = Path(directory) / (case + ".json")
        target.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")


@pytest.fixture
def submit_sod_pg(monkeypatch):
    import psycopg
    from psycopg import sql

    from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
    from tests.conftest import IntakeDatabase, IntakePgServer
    from tests.intake_registry_fixture import TENANT
    from tests.integration.test_intake_registry_calendar_postgresql import (
        _bundle,
        _seed_identities,
        _upgrade,
    )
    from tests.integration.test_operator_canonical_wiring import CanonicalHarness, _seed_candidate

    private_path = os.environ.get("ODP_SUBMIT_AUTHOR_ADMIN_DSN_FILE")
    if private_path is None:
        pytest.skip("Explicitly owned localhost PostgreSQL scratch authority not requested")
    path = Path(private_path)
    assert path.stat().st_mode & 0o077 == 0
    raw = path.read_text().strip()
    if raw.startswith("{"):
        values = json.loads(raw)
        dsn = values.get("dsn") or values.get("database_url") or values.get("ODAY_DATABASE_URL")
    elif raw.startswith(("postgresql://", "postgres://", "dbname=")):
        dsn = raw
    else:
        entries = [line.split("=", 1)[1] for line in raw.splitlines() if line.lstrip().startswith(
            ("ODAY_DATABASE_URL=", "DATABASE_URL=", "export ODAY_DATABASE_URL="))]
        assert len(entries) == 1
        dsn = shlex.split(entries[0])[0]
    admin = psycopg.conninfo.conninfo_to_dict(dsn)
    assert str(admin.get("host", "localhost")) in {"localhost", "127.0.0.1", "::1"}
    # Discard the private DSN's original database before making any connection.
    admin["dbname"] = "postgres"
    server = IntakePgServer(psycopg=psycopg, admin_params=admin)
    name = "odp_submit_sod_author_" + uuid4().hex[:16]
    with server.connect("postgres") as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        oid = connection.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone()[0]
    database = IntakeDatabase(server=server, dbname=name)
    bundle = None
    try:
        _upgrade(database, "head")
        apply_upgrade_to_database(database.url())
        with database.connect() as connection:
            _seed_identities(connection)
            for actor in (RESUBMITTER, CHECKER):
                connection.execute(
                    "INSERT INTO identity.accounts(account_id,tenant_id,username,email,status,created_by) "
                    "VALUES(%s,%s,%s,%s,'active','synthetic-sod-test')",
                    (actor, TENANT, actor, actor + "@example.invalid"),
                )
                connection.execute(
                    "INSERT INTO identity.account_roles(account_id,role,granted_by) "
                    "VALUES(%s,'site_reviewer','synthetic-sod-test')", (actor,),
                )
                connection.execute("INSERT INTO identity.account_scopes(account_id) VALUES(%s)", (actor,))
        bundle = _bundle(database, monkeypatch)
        harness = CanonicalHarness(None, bundle=bundle)
        candidate = _seed_candidate(harness, TENANT)
        saved = harness.sitescore(TENANT).save_report(replace(report(), candidate_site_id=candidate))
        yield database, bundle, harness, TENANT, candidate, saved
    finally:
        if bundle is not None:
            bundle.engine.close()
        assert name.startswith("odp_submit_sod_author_")
        with server.connect("postgres") as connection:
            actual = connection.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone()
            assert actual and actual[0] == oid
            # No FORCE, reset or client termination: drop only this exact new OID/name.
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        _record("scratch-" + name, {"database": name, "oid": oid, "createdAndDropped": True,
                                   "originalDatabaseConnected": False, "forceOrReset": False})


@contextmanager
def _signed_client(pg, monkeypatch, *, memory=False):
    _, bundle, harness, tenant, _, saved = pg
    signing_key = SigningKey(kid="synthetic-sod-local", algorithm="HS256",
                             secret=b"isolated-local-sod-regression-key")
    config = AuthBoundaryConfig(local_issuer="urn:odp:identity:local",
        local_audiences=frozenset({"isolated-submit-sod"}),
        local_signing_keys={signing_key.kid: signing_key},
        identity_store=bundle.identity_store, session_service=bundle.session_service)
    monkeypatch.setattr(dependencies, "_default_boundary", AuthenticationBoundary(config, audit_log=bundle.audit_log))

    def headers(actor, key):
        session = bundle.session_service.create_session(account_id=UUID(actor), provider="local_password")
        now = datetime.now(UTC)
        token = encode_compact_jwt({"sub": actor, "sid": str(session.session_id), "tenant_id": tenant,
            "iss": "urn:odp:identity:local", "aud": "isolated-submit-sod", "iat": now.timestamp(),
            "exp": (now + timedelta(minutes=20)).timestamp()}, signing_key)
        return {"Authorization": "Bearer " + token, "If-Actor-Match": actor,
                "Idempotency-Key": key, "X-Correlation-Id": "sod-" + key}

    app = harness.app()
    workflow = None
    if memory:
        repository = InMemorySiteScoreRepository()
        stored = repository.save_report(saved)
        workflow = SiteScoreDecisionWorkflow()
        router = create_sitescore_router(repository=repository, workflow=workflow,
            runtime_mode="local", require_production_model=False, require_durable_jobs=False)
    else:
        router = create_sitescore_router(repository=harness.sitescore(tenant), audit_log=bundle.audit_log,
            sitescore_decision_repository_for_tenant=harness.decisions,
            review_service_resolver=bundle.network_review_resolver(), runtime_mode="production",
            require_production_model=True)
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as client:
        yield client, headers, workflow, stored if memory else saved


def _business(bundle):
    value = durable_bytes(bundle.engine)
    value["durable_audit_events"] = [row for row in value["durable_audit_events"]
                                     if not str(dict(row)["event_type"]).startswith("security.")]
    return value


def _submit(client, headers, tenant, candidate, actor, key, *, decision_id=None):
    suffix = "?decisionId=" + decision_id if decision_id else ""
    context = client.get(f"{BASE}/candidates/{candidate}/submission-context" + suffix,
                         headers=headers(actor, "context-" + key))
    assert context.status_code == 200, context.text
    body = {**context.json()["target"], "expectedActorSubjectId": actor, "expectedTenantId": tenant,
            "submissionSummary": "Recorded signed-account saved report round " + key,
            "reviewerGroupId": context.json()["reviewerGroups"][0]["id"]}
    result = client.post(f"{BASE}/candidates/{candidate}/submit", json=body, headers=headers(actor, key))
    assert result.status_code == 200, result.text
    return context.json(), body, result.json()


def _decision(client, headers, tenant, decision_id, version, actor, surface, action, key):
    if surface == "native":
        path = f"/api/v1/sitescore/decisions/{decision_id}/decision"
        body = {"action": "APPROVE", "actor": CHECKER, "reason": "Signed independent retained-report review",
                "expected_version": version, "expected_actor_subject_id": actor, "expected_tenant_id": tenant}
    else:
        path = f"{BASE}/{decision_id}/decide"
        body = {"decision": action, "reason": "Signed independent retained-report review",
                "actorName": CHECKER if actor != CHECKER else ORIGINAL, "actorRoleId": "executive",
                "overrideAck": True, "conditions": "Recorded lease condition" if action == "WAIT" else ""}
    return client.post(path, headers=headers(actor, key), json=body)


@pytest.mark.parametrize("surface,action", [("operator", "GO"), ("operator", "WAIT"), ("native", "GO")])
def test_signed_original_creator_and_current_resubmitter_denied_independent_checker_succeeds(
    submit_sod_pg, monkeypatch, surface, action,
):
    """Actual JWT+SQL A→C RETURN→B resubmit→A/B deny→C accepted, frozen V1/latest V2."""
    _, bundle, harness, tenant, candidate, saved = submit_sod_pg
    case = "signed-" + surface + "-" + action
    with _signed_client(submit_sod_pg, monkeypatch) as (client, headers, _, _):
        _, _, initial = _submit(client, headers, tenant, candidate, ORIGINAL, "initial")
        decision_id = initial["records"]["decisionId"]
        returned = client.post(f"{BASE}/{decision_id}/decide", headers=headers(CHECKER, "return"),
            json={"decision": "RETURN", "reason": "Recorded original lease and photographs are requested",
                  "requiredData": ["lease", "photos"], "overrideAck": True})
        assert returned.status_code == 200, returned.text
        old_approval = returned.json()["records"]["approvalId"]
        latest = harness.sitescore(tenant).save_report(replace(report(), candidate_site_id=candidate,
                                                             model_version="later-synthetic-model"))
        context, body, resubmitted = _submit(client, headers, tenant, candidate, RESUBMITTER, "resubmit")
        decision = harness.decisions(tenant).get_decision(decision_id)
        assert decision.created_by == ORIGINAL and decision.created_by_verified is True
        assert decision.submitted_by == RESUBMITTER and decision.submission_actor_verified is True
        assert decision.version == 4 and decision.status.value == "PENDING_REVIEW"
        assert decision.report_id == saved.report_id and decision.report_version == saved.report_version
        assert context["target"]["expectedLatestReportId"] == latest.report_id
        assert resubmitted["records"]["decisionId"] == decision_id
        assert resubmitted["records"]["approvalId"] != old_approval
        metadata = DurableAssistedIntakeRepository(harness.scoped(tenant)).get_candidate_metadata(candidate)
        assert metadata["reviewWorkflow"]["networkAction"] == "RETURN"
        assert metadata["reviewWorkflow"]["decisionVersion"] == 3
        assert metadata["reviewSubmission"]["decisionVersion"] == decision.version
        reads = {}
        for name, path in [("network", "/api/v1/operator/network-listings"),
                           ("scoring", "/api/v1/operator/network-scoring"), ("reviews", BASE)]:
            response = client.get(path, headers=headers(CHECKER, "read-" + name))
            assert response.status_code == 200, response.text
            reads[name] = response.json()
        row = next(row for row in reads["network"]["candidates"] if row["id"] == candidate)
        assert "reviewWorkflow" not in row
        assert row["reviewSubmission"]["decisionId"] == decision_id
        assert row["reviewSubmission"]["decisionVersion"] == decision.version
        assert row["reviewSubmission"]["reportId"] == saved.report_id
        assert row["reviewSubmission"]["latestReportId"] == latest.report_id
        raw = next(row for row in reads["reviews"]["decisions"] if row["decision_id"] == decision_id)
        assert raw["decision_status"] == "PENDING_REVIEW" and raw["created_by_verified"] is True
        evidence = {"origin": "actual own localhost PostgreSQL + local signed JWT/SQL accounts",
                    "surface": surface, "action": action, "context": context, "submissionBody": body,
                    "resubmittedReceipt": resubmitted, "afterResubmit": reads,
                    "rawHistoricalAndPendingMetadata": metadata, "denials": []}
        for actor, label in [(ORIGINAL, "original"), (RESUBMITTER, "current")]:
            before = _business(bundle)
            denied = _decision(client, headers, tenant, decision_id, decision.version, actor, surface, action, label)
            unchanged = _business(bundle) == before
            evidence["denials"].append({"actor": actor, "status": denied.status_code,
                                       "response": denied.json(), "businessRowsUnchanged": unchanged})
            _record(case, evidence)
            assert denied.status_code == 403, denied.text
            assert unchanged
            assert harness.decisions(tenant).get_decision(decision_id) == decision
        accepted = _decision(client, headers, tenant, decision_id, decision.version, CHECKER, surface, action, "checker")
        evidence["checkerStatus"] = accepted.status_code
        evidence["checkerResponse"] = accepted.json()
        _record(case, evidence)
        assert accepted.status_code == 200, accepted.text
        outcome = harness.decisions(tenant).get_decision(decision_id)
        assert outcome.status.value == "APPROVED" and outcome.history[-1].actor == CHECKER
        assert outcome.created_by == ORIGINAL and outcome.submitted_by == RESUBMITTER
        assert outcome.report_id == saved.report_id and outcome.report_version == saved.report_version
        assert outcome.network_action == action
        approval = DurableApprovalRepository(harness.scoped(tenant)).get(outcome.approval_id)
        assert approval.approval_status == "approved" and approval.approval_id != old_approval
        retained = harness.decisions(tenant).get_report(decision_id)
        assert retained.to_dict() == saved.to_dict()
        audit_id = accepted.json()["records"]["auditId"] if surface == "operator" else accepted.json()["audit_event_id"]
        assert bundle.engine.query_one("SELECT event_id FROM durable_audit_events WHERE event_id=?", (audit_id,)) is not None
        evidence["actualCheckerPublication"] = {"approvalId": approval.approval_id,
            "approvalStatus": approval.approval_status, "retainedReportUnchanged": True, "auditId": audit_id}
        _record(case, evidence)
        assert harness.runtime.calls == []


@pytest.mark.parametrize("surface", ["operator", "native"])
def test_signed_unverified_legacy_body_creator_is_not_authority_current_sender_still_denied(
    submit_sod_pg, monkeypatch, surface,
):
    _, bundle, harness, tenant, candidate, saved = submit_sod_pg
    workflow = SiteScoreDecisionWorkflow(store=harness.decisions(tenant), audit_log=bundle.audit_log,
                                        approval_store=DurableApprovalRepository(harness.scoped(tenant)))
    legacy = workflow.open_decision(saved, created_by=ORIGINAL, tenant_id=tenant, verified_creator=False)
    with _signed_client(submit_sod_pg, monkeypatch) as (client, headers, _, _):
        _, _, receipt = _submit(client, headers, tenant, candidate, RESUBMITTER, "legacy-submit", decision_id=legacy.decision_id)
        current = harness.decisions(tenant).get_decision(legacy.decision_id)
        assert current.created_by == ORIGINAL and current.created_by_verified is False
        assert current.submitted_by == RESUBMITTER and current.submission_actor_verified is True
        before = _business(bundle)
        denied = _decision(client, headers, tenant, current.decision_id, current.version, RESUBMITTER, surface, "GO", "current-self")
        assert denied.status_code == 403, denied.text
        assert _business(bundle) == before
        accepted = _decision(client, headers, tenant, current.decision_id, current.version, ORIGINAL, surface, "GO", "independent-original-body-string")
        assert accepted.status_code == 200, accepted.text
        actual = harness.decisions(tenant).get_decision(current.decision_id)
        assert actual.history[-1].actor == ORIGINAL and actual.created_by_verified is False
        assert actual.submitted_by == RESUBMITTER and actual.report_id == saved.report_id
        assert receipt["commandReceipt"]["originalCreatorVerified"] is False
        assert harness.runtime.calls == []
        _record("legacy-" + surface, {"originalCreatorVerified": False, "currentSelfStatus": denied.status_code,
                                      "independentCheckerStatus": accepted.status_code, "receipt": receipt})


def test_signed_native_memory_fallback_uses_genuine_principal_and_verified_creator_not_body(
    submit_sod_pg, monkeypatch,
):
    """Memory compatibility owner only; identity/session/JWT still actual owned SQL."""
    _, _, _, tenant, _, _ = submit_sod_pg
    with _signed_client(submit_sod_pg, monkeypatch, memory=True) as (client, headers, workflow, saved):
        posted = client.post("/api/v1/sitescore/decisions", headers=headers(ORIGINAL, "memory-open"),
                             json={"report_id": saved.report_id, "created_by": CHECKER})
        assert posted.status_code == 201, posted.text
        decision_id = posted.json()["decision_id"]
        assert posted.json()["created_by"] == ORIGINAL and posted.json()["created_by_verified"] is True
        workflow.decide(decision_id, action=DecisionAction.REQUEST_REVISION, actor=CHECKER,
                        reason="Explicit local compatibility RETURN prerequisite")
        # This local owner fixture is not a new native resubmit endpoint or grant.
        workflow.submit_for_review(decision_id, submitted_by=RESUBMITTER, verified_submitter=True)
        current = workflow.get(decision_id)
        evidence = {"origin": "actual signed SQL principal, explicit local in-memory compatibility owner",
                    "originalCreatorVerified": current.created_by_verified, "denials": []}
        for actor in (ORIGINAL, RESUBMITTER):
            denied = _decision(client, headers, tenant, decision_id, current.version, actor, "native", "GO", "memory-" + actor[-3:])
            evidence["denials"].append({"actor": actor, "status": denied.status_code})
            _record("memory-fallback", evidence)
            assert denied.status_code == 403, denied.text
            assert workflow.get(decision_id) == current
        accepted = _decision(client, headers, tenant, decision_id, current.version, CHECKER, "native", "GO", "memory-checker")
        assert accepted.status_code == 200, accepted.text
        assert workflow.get(decision_id).history[-1].actor == CHECKER
        evidence["checkerStatus"] = accepted.status_code
        _record("memory-fallback", evidence)
