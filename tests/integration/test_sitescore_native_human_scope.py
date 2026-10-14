"""Actual signed SQL principals for the native SiteScore human module scope."""

import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from modules.opsboard.auth import SigningKey, encode_compact_jwt
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.infrastructure.persistence.repositories import DurableApprovalRepository
from shared.workflow.sitescore import SiteScoreDecisionWorkflow
from tests.integration.test_network_review_submission_sod import (
    CHECKER,
    ORIGINAL,
    RESUBMITTER,
    _decision,
    _signed_client,
)
from tests.integration.test_network_review_submission_sod import (
    _business as _base_business,
)
from tests.unit.test_network_canonical_review_projection import report

BASE = "/api/v1/sitescore/decisions"
OPERATOR = "/api/v1/operator/network-reviews"
pytestmark = pytest.mark.requires_live_env


def _record(label, value):
    directory = os.environ.get("ODP_NATIVE_SCOPE_EVIDENCE_DIR")
    if directory:
        target = Path(directory) / (label + ".json")
        target.write_text(json.dumps(value, indent=2, default=str) + "\n")
        target.chmod(0o600)


def _business(bundle):
    snapshot = _base_business(bundle)
    for table in ("durable_outbox_events", "workflow.jobs", "workflow.outbox_events"):
        snapshot[table] = [tuple(dict(row).items()) for row in bundle.engine.query(f"SELECT * FROM {table} ORDER BY 1")]
    return snapshot


@pytest.fixture
def native_scope_pg(monkeypatch):
    import psycopg
    from psycopg import sql
    from psycopg.types.json import Jsonb

    from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
    from tests.conftest import IntakeDatabase, IntakePgServer
    from tests.intake_registry_fixture import TENANT
    from tests.integration.test_intake_registry_calendar_postgresql import (
        _bundle,
        _seed_identities,
        _upgrade,
    )
    from tests.integration.test_operator_canonical_wiring import CanonicalHarness, _seed_candidate

    private_path = os.environ.get("ODP_NATIVE_SCOPE_ADMIN_DSN_FILE")
    if private_path is None:
        pytest.skip("New owned localhost PostgreSQL authority was not requested")
    private = Path(private_path)
    assert private.stat().st_mode & 0o777 == 0o600
    admin = psycopg.conninfo.conninfo_to_dict(private.read_text().strip())
    assert admin.get("host") in {"localhost", "127.0.0.1", "::1"}
    admin["dbname"] = "postgres"
    server = IntakePgServer(psycopg=psycopg, admin_params=admin)
    name = "odp_native_scope_author_" + uuid4().hex[:16]
    bundle = None
    with server.connect("postgres") as connection:
        assert connection.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone() is None
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        oid = connection.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone()[0]
    _record("create-" + name, {"name": name, "oid": oid, "originalDatabaseConnected": False})
    database = IntakeDatabase(server=server, dbname=name)
    try:
        _upgrade(database, "head")
        apply_upgrade_to_database(database.url())
        with database.connect() as connection:
            _seed_identities(connection)
            for actor in (RESUBMITTER, CHECKER):
                connection.execute(
                    "INSERT INTO identity.accounts(account_id,tenant_id,username,email,status,created_by) "
                    "VALUES(%s,%s,%s,%s,'active','synthetic-native-scope')",
                    (actor, TENANT, actor, actor + "@example.invalid"),
                )
                connection.execute(
                    "INSERT INTO identity.account_roles(account_id,role,granted_by) "
                    "VALUES(%s,'site_reviewer','synthetic-native-scope')", (actor,),
                )
                connection.execute("INSERT INTO identity.account_scopes(account_id,modules) VALUES(%s,%s)",
                                   (actor, Jsonb(["sitescore"])))
            connection.execute("UPDATE identity.account_scopes SET modules=%s WHERE account_id=%s",
                               (Jsonb(["sitescore"]), ORIGINAL))
        bundle = _bundle(database, monkeypatch)
        harness = CanonicalHarness(None, bundle=bundle)
        candidate = _seed_candidate(harness, TENANT)
        saved = harness.sitescore(TENANT).save_report(replace(report(), candidate_site_id=candidate))
        _record("sql-authority-" + name, {"accounts": [dict(row) for row in bundle.engine.query(
            "SELECT a.account_id,a.tenant_id,a.status,r.role,s.modules,s.clearance "
            "FROM identity.accounts a JOIN identity.account_roles r USING(account_id) "
            "JOIN identity.account_scopes s USING(account_id) WHERE a.account_id IN (?,?,?) ORDER BY a.account_id",
            (ORIGINAL, RESUBMITTER, CHECKER))], "signedAuthentication": "local JWT + actual SQL accounts/roles/scopes/sessions",
            "realPasswordLoginOrBrowser": False})
        yield database, bundle, harness, TENANT, candidate, saved
    finally:
        if bundle is not None:
            bundle.engine.close()
        assert name.startswith("odp_native_scope_author_")
        with server.connect("postgres") as connection:
            assert connection.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone() == (oid,)
            deadline = time.monotonic() + 5
            while connection.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=%s", (name,)).fetchone()[0]:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Owned database remains active; no termination/FORCE authorized")
                time.sleep(0.05)
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            assert connection.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone() is None
        _record("drop-" + name, {"name": name, "oid": oid, "normalDrop": True,
                               "terminateOrForce": False, "remainingConnections": 0})


def _scope(bundle, actor, *, modules=None, clearance="CONFIDENTIAL", area=None):
    before = bundle.engine.query_one("SELECT modules,clearance,assigned_area_ids FROM identity.account_scopes WHERE account_id=?", (actor,))
    bundle.engine.execute(
        "UPDATE identity.account_scopes SET modules=?,clearance=?,assigned_area_ids=? WHERE account_id=?",
        (json.dumps(["sitescore"] if modules is None else modules), clearance,
         json.dumps([] if area is None else [area]), actor),
    )
    after = bundle.engine.query_one("SELECT modules,clearance,assigned_area_ids FROM identity.account_scopes WHERE account_id=?", (actor,))
    _record("sql-scope-" + actor[-3:] + "-" + uuid4().hex[:8], {"actor": actor,
        "beforeActualSQL": dict(before), "afterActualSQL": dict(after), "headerRoleAuthority": False})


def _open(client, headers, tenant, saved, actor=ORIGINAL, key="native-open", **extra):
    body = {"report_id": saved.report_id, "created_by": CHECKER,
            "submission_summary": "Recorded SiteScore-only module saved report submission",
            "reviewer_group_id": "site_reviewer", "expected_actor_subject_id": actor,
            "expected_tenant_id": tenant, **extra}
    response = client.post(BASE, headers=headers(actor, key), json=body)
    _record(key, {"status": response.status_code, "response": response.json(), "body": body})
    return response, body


def _pending(pg, client, headers):
    """Existing unrestricted module-axis setup, then actual persisted native-only scope."""
    _, bundle, _, tenant, _, saved = pg
    _scope(bundle, ORIGINAL, modules=[])
    response, _ = _open(client, headers, tenant, saved, key="actual-setup-pending")
    assert response.status_code == 201, response.text
    _scope(bundle, ORIGINAL)
    return response.json()["decision_id"]


def test_native_only_signed_sql_initial_submit_read_selfdeny_checker_and_replay(native_scope_pg, monkeypatch):
    _, bundle, harness, tenant, candidate, saved = native_scope_pg
    with _signed_client(native_scope_pg, monkeypatch) as (client, headers, _, _):
        opened, body = _open(client, headers, tenant, saved)
        assert opened.status_code == 201, opened.text
        decision_id = opened.json()["decision_id"]
        receipt = opened.json()["submission_receipt"]
        assert receipt["records"]["candidateId"] == candidate
        assert opened.json()["created_by"] == ORIGINAL and opened.json()["created_by_verified"] is True
        assert opened.json()["submitted_by"] == ORIGINAL and opened.json()["submission_actor_verified"] is True
        current = harness.decisions(tenant).get_decision(decision_id)
        assert harness.decisions(tenant).get_report(decision_id).to_dict() == saved.to_dict()
        before = _business(bundle)
        replay = client.post(BASE, headers=headers(ORIGINAL, "native-open"), json=body)
        assert replay.status_code == 201 and replay.json()["submission_receipt"]["idempotentReplay"] is True
        assert _business(bundle) == before
        read = client.get(BASE + "/" + decision_id, headers=headers(CHECKER, "native-read"))
        assert read.status_code == 200 and read.json() == current.to_dict()
        assert client.get(OPERATOR, headers=headers(CHECKER, "no-operator-read")).status_code == 403
        denied = _decision(client, headers, tenant, decision_id, current.version, ORIGINAL, "native", "GO", "own-denial")
        assert denied.status_code == 403 and _business(bundle) == before
        accepted = _decision(client, headers, tenant, decision_id, current.version, CHECKER, "native", "GO", "checker-positive")
        assert accepted.status_code == 200, accepted.text
        actual = harness.decisions(tenant).get_decision(decision_id)
        assert actual.history[-1].actor == CHECKER and actual.status.value == "APPROVED"
        assert DurableApprovalRepository(harness.scoped(tenant)).get(actual.approval_id).approval_status == "approved"
        assert harness.decisions(tenant).get_report(decision_id).to_dict() == saved.to_dict()
        assert harness.runtime.calls == []


def test_native_only_pending_decision_read_uses_same_actual_scope(native_scope_pg, monkeypatch):
    _, bundle, harness, tenant, _, _ = native_scope_pg
    with _signed_client(native_scope_pg, monkeypatch) as (client, headers, _, _):
        decision_id = _pending(native_scope_pg, client, headers)
        current = harness.decisions(tenant).get_decision(decision_id)
        before = _business(bundle)
        response = client.get(BASE + "/" + decision_id, headers=headers(CHECKER, "actual-read"))
        _record("read-only", {"status": response.status_code, "response": response.json()})
        assert response.status_code == 200 and response.json() == current.to_dict()
        assert _business(bundle) == before


def test_native_only_pending_independent_checker_approve(native_scope_pg, monkeypatch):
    _, _, harness, tenant, _, saved = native_scope_pg
    with _signed_client(native_scope_pg, monkeypatch) as (client, headers, _, _):
        decision_id = _pending(native_scope_pg, client, headers)
        current = harness.decisions(tenant).get_decision(decision_id)
        response = _decision(client, headers, tenant, decision_id, current.version, CHECKER, "native", "GO", "actual-checker")
        _record("checker-only", {"status": response.status_code, "response": response.json()})
        assert response.status_code == 200, response.text
        assert harness.decisions(tenant).get_decision(decision_id).history[-1].actor == CHECKER
        assert harness.decisions(tenant).get_report(decision_id).to_dict() == saved.to_dict()


def test_native_only_return_resubmit_preserves_both_verified_sod_and_frozen_report(native_scope_pg, monkeypatch):
    _, bundle, harness, tenant, candidate, saved = native_scope_pg
    with _signed_client(native_scope_pg, monkeypatch) as (client, headers, _, _):
        decision_id = _pending(native_scope_pg, client, headers)
        # Native legacy REQUEST_REVISION has no tasks DTO; actual existing
        # Operator RETURN performs its normative required-data transition.
        _scope(bundle, CHECKER, modules=[])
        returned = client.post(OPERATOR + "/" + decision_id + "/decide", headers=headers(CHECKER, "actual-return"),
            json={"decision": "RETURN", "reason": "Recorded lease and photographs must be supplemented",
                  "requiredData": ["lease", "photos"], "overrideAck": True})
        assert returned.status_code == 200, returned.text
        old_approval = returned.json()["records"]["approvalId"]
        _scope(bundle, CHECKER)
        latest = harness.sitescore(tenant).save_report(replace(saved, model_version="later-local-recorded-model"))
        resubmitted, _ = _open(client, headers, tenant, saved, actor=RESUBMITTER, key="native-resubmit")
        assert resubmitted.status_code == 201, resubmitted.text
        current = harness.decisions(tenant).get_decision(decision_id)
        assert current.created_by == ORIGINAL and current.created_by_verified is True
        assert current.submitted_by == RESUBMITTER and current.submission_actor_verified is True
        assert current.report_id == saved.report_id and current.report_version == saved.report_version
        assert latest.report_version == 2 and current.approval_id != old_approval
        metadata = DurableAssistedIntakeRepository(harness.scoped(tenant)).get_candidate_metadata(candidate)
        assert metadata["reviewWorkflow"]["networkAction"] == "RETURN"
        assert metadata["reviewSubmission"]["decisionVersion"] == current.version
        for actor in (ORIGINAL, RESUBMITTER):
            before = _business(bundle)
            denied = _decision(client, headers, tenant, decision_id, current.version, actor, "native", "GO", "self-" + actor[-3:])
            assert denied.status_code == 403 and _business(bundle) == before
            assert harness.decisions(tenant).get_decision(decision_id) == current
        accepted = _decision(client, headers, tenant, decision_id, current.version, CHECKER, "native", "GO", "independent-return-checker")
        assert accepted.status_code == 200, accepted.text
        assert harness.decisions(tenant).get_report(decision_id).to_dict() == saved.to_dict()
        assert harness.sitescore(tenant).latest(candidate).to_dict() == latest.to_dict()
        assert harness.runtime.calls == []


def test_native_only_unverified_legacy_creator_is_not_authority(native_scope_pg, monkeypatch):
    _, bundle, harness, tenant, _, saved = native_scope_pg
    workflow = SiteScoreDecisionWorkflow(store=harness.decisions(tenant), audit_log=bundle.audit_log,
        approval_store=DurableApprovalRepository(harness.scoped(tenant)))
    legacy = workflow.open_decision(saved, created_by=ORIGINAL, tenant_id=tenant, verified_creator=False)
    with _signed_client(native_scope_pg, monkeypatch) as (client, headers, _, _):
        posted, _ = _open(client, headers, tenant, saved, actor=RESUBMITTER, key="native-legacy-submit")
        assert posted.status_code == 201 and posted.json()["decision_id"] == legacy.decision_id
        current = harness.decisions(tenant).get_decision(legacy.decision_id)
        assert current.created_by_verified is False and current.submission_actor_verified is True
        before = _business(bundle)
        denied = _decision(client, headers, tenant, current.decision_id, current.version, RESUBMITTER, "native", "GO", "legacy-self")
        assert denied.status_code == 403 and _business(bundle) == before
        positive = _decision(client, headers, tenant, current.decision_id, current.version, ORIGINAL, "native", "GO", "legacy-distinct")
        assert positive.status_code == 200, positive.text


@pytest.mark.parametrize("restriction,expected", [("operator-module", 403), ("clearance", 403), ("viewless", 403),
                                                   ("area", 403), ("address", 409)])
def test_native_human_scope_denials_do_not_publish(native_scope_pg, monkeypatch, restriction, expected):
    _, bundle, harness, tenant, candidate, saved = native_scope_pg
    with _signed_client(native_scope_pg, monkeypatch) as (client, headers, _, _):
        decision_id = _pending(native_scope_pg, client, headers)
        if restriction == "operator-module":
            _scope(bundle, CHECKER, modules=["operator_console"])
        elif restriction == "clearance":
            _scope(bundle, CHECKER, clearance="INTERNAL")
        elif restriction == "area":
            _scope(bundle, CHECKER, area="foreign-recorded-area")
        elif restriction == "viewless":
            bundle.engine.execute("UPDATE identity.account_roles SET role='operator_viewer' WHERE account_id=?", (CHECKER,))
        else:
            # Explicit RAW owned corruption fixture; normal producer preserves
            # human-owned fields. This is not a production mutation loophole.
            repository = harness.listing(tenant)
            physical = repository._store.get(repository._CANDIDATES, candidate)
            repository._store.put(repository._CANDIDATES, candidate,
                replace(physical, address=replace(physical.address, address_id="known-conflicting-address")))
        _record("restriction-authority-" + restriction, {"actualSQLAuthority": [dict(row) for row in bundle.engine.query(
            "SELECT a.account_id,a.tenant_id,r.role,s.modules,s.clearance,s.assigned_area_ids "
            "FROM identity.accounts a JOIN identity.account_roles r USING(account_id) "
            "JOIN identity.account_scopes s USING(account_id) WHERE a.account_id=?", (CHECKER,))]})
        current = harness.decisions(tenant).get_decision(decision_id)
        before = _business(bundle)
        read = client.get(BASE + "/" + decision_id, headers=headers(CHECKER, "deny-read-" + restriction))
        assert read.status_code == expected, read.text
        denied = _decision(client, headers, tenant, decision_id, current.version, CHECKER, "native", "GO", "deny-decide-" + restriction)
        assert denied.status_code == expected, denied.text
        posted, _ = _open(client, headers, tenant, saved, actor=CHECKER, key="deny-open-" + restriction,
                          module="sitescore")
        assert posted.status_code == expected, posted.text
        assert _business(bundle) == before
        assert harness.decisions(tenant).get_decision(decision_id) == current


def test_native_actor_tenant_and_replay_revocation_do_not_publish(native_scope_pg, monkeypatch):
    _, bundle, harness, tenant, _, saved = native_scope_pg
    with _signed_client(native_scope_pg, monkeypatch) as (client, headers, _, _):
        opened, body = _open(client, headers, tenant, saved, key="actual-revocation-key")
        assert opened.status_code == 201, opened.text
        current = harness.decisions(tenant).get_decision(opened.json()["decision_id"])
        before = _business(bundle)
        foreign = client.get(BASE + "/" + current.decision_id,
            headers={**headers(CHECKER, "foreign-tenant"), "x-tenant-id": str(uuid4())})
        assert foreign.status_code == 403, foreign.text
        foreign_tenant, foreign_actor = str(uuid4()), str(uuid4())
        bundle.engine.execute("INSERT INTO core.tenants(tenant_id,tenant_name) VALUES(?,?)",
                              (foreign_tenant, "Owned native-scope foreign tenant"))
        bundle.engine.execute(
            "INSERT INTO identity.accounts(account_id,tenant_id,username,email,status,created_by) "
            "VALUES(?,?,?,?,'active','synthetic-native-scope')",
            (foreign_actor, foreign_tenant, foreign_actor, foreign_actor + "@example.invalid"))
        bundle.engine.execute("INSERT INTO identity.account_roles(account_id,role,granted_by) "
                              "VALUES(?,'site_reviewer','synthetic-native-scope')", (foreign_actor,))
        bundle.engine.execute("INSERT INTO identity.account_scopes(account_id,modules) VALUES(?,?)",
                              (foreign_actor, '["sitescore"]'))
        session = bundle.session_service.create_session(account_id=UUID(foreign_actor), provider="local_password")
        at = datetime.now(UTC)
        key = SigningKey(kid="synthetic-sod-local", algorithm="HS256", secret=b"isolated-local-sod-regression-key")
        token = encode_compact_jwt({"sub": foreign_actor, "sid": str(session.session_id),
            "tenant_id": foreign_tenant, "iss": "urn:odp:identity:local", "aud": "isolated-submit-sod",
            "iat": at.timestamp(), "exp": (at + timedelta(minutes=20)).timestamp()}, key)
        foreign_headers = {"Authorization": "Bearer " + token, "If-Actor-Match": foreign_actor,
                           "Idempotency-Key": "actual-foreign-tenant", "X-Correlation-Id": "native-foreign"}
        _record("foreign-sql-authority", {"actualSQLAuthority": [dict(row) for row in bundle.engine.query(
            "SELECT a.account_id,a.tenant_id,r.role,s.modules,s.clearance FROM identity.accounts a "
            "JOIN identity.account_roles r USING(account_id) JOIN identity.account_scopes s USING(account_id) "
            "WHERE a.account_id=?", (foreign_actor,))], "actualSQLSessionCreated": True, "tokenRecorded": False})
        for response in [client.get(BASE + "/" + current.decision_id, headers=foreign_headers),
            client.post(BASE, headers=foreign_headers, json={**body,
                        "expected_actor_subject_id": foreign_actor, "expected_tenant_id": foreign_tenant}),
            client.post(BASE + "/" + current.decision_id + "/decision", headers=foreign_headers,
                json={"action": "APPROVE", "actor": CHECKER, "reason": "Foreign tenant must not see the owner",
                      "expected_version": current.version, "expected_actor_subject_id": foreign_actor,
                      "expected_tenant_id": foreign_tenant})]:
            assert response.status_code == 404, response.text
            assert "decision_id" not in response.json()
        assert _business(bundle) == before
        mismatched, _ = _open(client, headers, tenant, saved, key="mismatched-actor", expected_actor_subject_id=CHECKER)
        assert mismatched.status_code == 409 and _business(bundle) == before
        _scope(bundle, ORIGINAL, modules=["operator_console"])
        before = _business(bundle)
        replay = client.post(BASE, headers=headers(ORIGINAL, "actual-revocation-key"), json={**body, "module": "sitescore"})
        assert replay.status_code == 403, replay.text
        assert _business(bundle) == before
        assert harness.decisions(tenant).get_decision(current.decision_id) == current
        fresh = client.post("/api/v1/sitescore/score-jobs", headers=headers(CHECKER, "fresh-still-unavailable"), json={"features": []})
        assert fresh.status_code == 503 and harness.runtime.calls == []
