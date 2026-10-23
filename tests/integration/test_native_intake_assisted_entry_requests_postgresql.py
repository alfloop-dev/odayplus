"""Real HTTP/SQL proposal and withdrawal journals with synthetic grants only."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime

import pytest

from modules.listing.application.native_intake_assisted_entry_policy import (
    POLICY_ENV,
    SIGNING_KEY_ENV,
    STORAGE_ENV,
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import (
    NativeAssistedEntryRequestProof,
)
from modules.listing.application.native_intake_pending_commands import NativeIntakePendingCommands
from shared.audit.errors import CanonicalAuditUnavailable
from shared.auth import Role
from shared.jobs.queue import JobStatus
from tests.integration.test_native_intake_assisted_entry_history_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_assisted_entry_history_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_assisted_entry_history_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_assisted_entry_history_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_assisted_entry_history_postgresql import (
    past_manual as past_manual,
)
from tests.integration.test_native_intake_assisted_entry_history_postgresql import (
    recheck as recheck,
)
from tests.integration.test_native_intake_assisted_entry_history_postgresql import retire_settings
from tests.integration.test_native_intake_assisted_entry_history_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_assisted_entry_history_postgresql import (
    start_case as start_case,
)
from tests.integration.test_native_intake_assisted_entry_history_postgresql import (
    waiting as waiting,
)
from tests.integration.test_native_intake_source_policy_postgresql import native_row
from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
    request as request_recheck,
)
from tests.integration.test_native_intake_waiting_cancel_postgresql import state

pytestmark = pytest.mark.requires_live_env
BODY = {"fields": {"address": "Synthetic human address 8", "rent": 43210, "areaPing": 42.5},
    "reason": "Record explicit synthetic human proposals", "risk_summary": "Initial proposals need later independent review",
    "risk_acknowledged": True}


@pytest.fixture
def assisted(past_manual, monkeypatch):
    _, _, ids, _, _, _ = past_manual
    monkeypatch.setenv(POLICY_ENV, json.dumps({"policy_id": "synthetic-assisted-request", "version": 1,
        "tenant_ids": [ids["tenant"]], "max_attempts": 3, "deadline_seconds": 120, "backoff_seconds": [1, 3]}))
    monkeypatch.setenv(STORAGE_ENV, json.dumps({"version": 1, "global_max_concurrency": 5,
        "per_source_max_concurrency": 1, "storage_bindings": [{"tenant_id": ids["tenant"],
        "bucket": "native-synthetic-manual", "residency_mode": "TW_ONLY", "retention_class": "STANDARD",
        "kms_key_name": "projects/test/locations/asia-east1/keyRings/native/cryptoKeys/manual/cryptoKeyVersions/3"}]}))
    monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-manual-request-capability-" * 2)
    return past_manual


def command(case, *, key="synthetic-assisted-human-request-001", body=None, version=None):
    recheck, _, _, _, result, _ = case
    start = recheck[0][0]
    return start[1][0].post(f'/api/v1/canonical-intakes/{result["intake_id"]}/assisted-entry-completions',
        headers={"Idempotency-Key": key, "If-Match": f'W/"{version or result["version"]}"'},
        json=BODY if body is None else body)


def withdraw(case, receipt, *, kind="assisted-entry-completions", key="synthetic-withdraw-human-command-001",
             reason="Withdraw this synthetic pending human command", version=None):
    recheck, _, _, _, result, _ = case
    start = recheck[0][0]
    return start[1][0].post(f'/api/v1/canonical-intakes/{result["intake_id"]}/{kind}/{receipt["request_id"]}/cancel',
        headers={"Idempotency-Key": key, "If-Match": f'W/"{version or result["version"]}"'}, json={"reason": reason})


def snapshot(case):
    return state(case[0][0])


@pytest.mark.parametrize("role", [Role.EXPANSION_USER, Role.SITE_REVIEWER, Role.DATA_OWNER])
def test_actual_request_is_proposed_only_and_replay_uses_no_legacy_store(assisted, monkeypatch, role):
    recheck, graph, ids, db, result, _ = assisted
    start = recheck[0][0]
    start[1][1]["principal"] = replace(start[1][1]["principal"], roles=frozenset({role}))
    with db.connect() as connection:
        connection.execute("UPDATE identity.account_roles SET role=%s WHERE account_id=%s", (role.value, ids["actor"]))
        connection.execute("UPDATE identity.account_scopes SET clearance='PUBLIC' WHERE account_id=%s", (ids["actor"],))
    def no_legacy(*args, **kwargs):
        raise AssertionError("A native request must not refresh/flush the legacy pickle journal")
    monkeypatch.setattr(start[1][2], "refresh", no_legacy)
    monkeypatch.setattr(start[1][2], "flush", no_legacy)
    old = native_row(start, result["intake_id"])
    response = command(assisted)
    assert response.status_code == 202, response.text
    receipt = response.json()
    assert receipt["version"] == result["version"] and receipt["status"] == "REQUESTED"
    assert response.headers["etag"] == f'W/"{result["version"]}"'
    assert response.headers["Idempotency-Replayed"] == "false"
    assert native_row(start, result["intake_id"]) == old
    with graph.transaction(ids["tenant"]):
        proof = NativeAssistedEntryRequestProof(graph).read(ids["tenant"], result["intake_id"], receipt["request_id"], before=lambda: None)
        assert {k: v for k, v in proof.receipt.items() if k != "requested_at"} == {
            k: v for k, v in receipt.items() if k != "requested_at"}
        assert datetime.fromisoformat(proof.receipt["requested_at"]) == datetime.fromisoformat(receipt["requested_at"])
        assert proof.command.fields.values() == BODY["fields"]
        assert len(proof.rows) == 3 and all(r["status"] == "PROPOSED" and r["reviewed_by"] is None for r in proof.rows)
        assert all(r["source_snapshot_id"] is None and r["identity_affecting"] is True for r in proof.rows)
        assert all(r["proposed_by"] == ids["actor"] and r["field_classification"] == "INTERNAL" for r in proof.rows)
        assert graph.engine.query_one("SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND aggregate_type='intake_assisted_entry'", (ids["tenant"],)) is None
    after = snapshot(assisted)
    assert BODY["fields"]["address"] not in json.dumps(after["workflow.outbox_events"])
    assert BODY["fields"]["address"] not in json.dumps(after["audit.audit_events"])
    replay = command(assisted)
    assert replay.status_code == 202 and replay.json() == receipt and replay.headers["Idempotency-Replayed"] == "true"
    assert snapshot(assisted) == after
    assert command(assisted, key="synthetic-competing-pending-human-002").status_code == 409
    assert snapshot(assisted) == after


@pytest.mark.parametrize("role,expected", [(Role.EXPANSION_USER, 403), (Role.SITE_REVIEWER, 202), (Role.DATA_OWNER, 202)])
def test_actual_other_human_does_not_replace_the_immutable_submitter(assisted, role, expected):
    recheck, graph, ids, db, result, _ = assisted
    start = recheck[0][0]
    start[1][1]["principal"] = replace(start[1][1]["principal"], subject_id=ids["reviewer"], roles=frozenset({role}))
    with db.connect() as connection:
        connection.execute("INSERT INTO identity.accounts (account_id,tenant_id,username,email,status,created_by) "
            "VALUES (%s,%s,'synthetic-second-human','second@example.invalid','active','synthetic')", (ids["reviewer"], ids["tenant"]))
        connection.execute("INSERT INTO identity.account_roles (account_id,role,granted_by) VALUES (%s,%s,'synthetic')", (ids["reviewer"], role.value))
        connection.execute("INSERT INTO identity.account_scopes (account_id,modules,clearance) VALUES (%s,'[\"listing\"]'::jsonb,'PUBLIC')", (ids["reviewer"],))
    before = snapshot(assisted)
    response = command(assisted)
    assert response.status_code == expected, response.text
    if expected == 403:
        assert snapshot(assisted) == before
    else:
        assert native_row(start, result["intake_id"])["submitter_subject_id"] == ids["actor"]
        with graph.transaction(ids["tenant"]):
            proof = NativeAssistedEntryRequestProof(graph).read(ids["tenant"], result["intake_id"], response.json()["request_id"], before=lambda: None)
            assert all(r["proposed_by"] == ids["reviewer"] for r in proof.rows)


@pytest.mark.parametrize("own", [False, True])
def test_dual_sql_staff_steward_roles_keep_the_matrixs_staff_ownership_and_audit_role(assisted, own):
    recheck, graph, ids, db, result, _ = assisted
    subject = ids["actor"] if own else ids["reviewer"]
    caller = recheck[0][0][1][1]
    caller["principal"] = replace(caller["principal"], subject_id=subject, roles=frozenset({Role.DATA_OWNER}))
    with db.connect() as connection:
        if not own:
            connection.execute("INSERT INTO identity.accounts (account_id,tenant_id,username,email,status,created_by) "
                "VALUES (%s,%s,'synthetic-dual-role','dual@example.invalid','active','synthetic')", (subject, ids["tenant"]))
            connection.execute("INSERT INTO identity.account_roles (account_id,role,granted_by) VALUES (%s,'data_owner','synthetic')", (subject,))
            connection.execute("INSERT INTO identity.account_scopes (account_id,modules,clearance) VALUES (%s,'[\"listing\"]'::jsonb,'PUBLIC')", (subject,))
        connection.execute("INSERT INTO identity.account_roles (account_id,role,granted_by) VALUES (%s,'expansion_user','synthetic')", (subject,))
    before = snapshot(assisted)
    response = command(assisted)
    assert response.status_code == (202 if own else 403), response.text
    if own:
        with graph.transaction(ids["tenant"]):
            audit = graph._one("SELECT actor_role FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=?", (ids["tenant"], response.json()["audit_event_id"]))
            assert audit["actor_role"] == "EXPANSION_STAFF"
    else:
        assert snapshot(assisted) == before


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("defect", ["http_role", "sql_role", "disabled", "module", "scope", "kill", "manual_grant", "manual_expiry"])
def test_request_and_replay_require_both_current_human_and_current_manual_source(assisted, replay, defect):
    recheck, _, ids, db, _, _ = assisted
    if replay:
        assert command(assisted).status_code == 202
    if defect == "http_role":
        caller = recheck[0][0][1][1]
        caller["principal"] = replace(caller["principal"], roles=frozenset({Role.PLATFORM_ADMIN}))
    else:
        sql = {"sql_role": "DELETE FROM identity.account_roles WHERE account_id=%s",
            "disabled": "UPDATE identity.accounts SET status='disabled' WHERE account_id=%s",
            "module": "UPDATE identity.account_scopes SET modules='[\"priceops\"]'::jsonb WHERE account_id=%s",
            "scope": "UPDATE identity.account_scopes SET store_ids='[\"other-store\"]'::jsonb WHERE account_id=%s",
            "kill": "UPDATE intake.source_registry SET kill_switch=true,version=version+1 WHERE source_id='synthetic-feed'",
            "manual_grant": "UPDATE intake.source_registry SET assisted_entry_approval_ref=NULL,assisted_entry_approved_by=NULL,"
                "assisted_entry_review_expires_at=NULL,version=version+1 WHERE source_id='synthetic-feed'",
            "manual_expiry": "UPDATE intake.source_registry SET assisted_entry_review_expires_at=clock_timestamp()-interval '1 second',"
                "version=version+1 WHERE source_id='synthetic-feed'"}[defect]
        with db.connect() as connection:
            connection.execute(sql, (ids["actor"],) if defect in {"sql_role", "disabled", "module", "scope"} else None)
    before = snapshot(assisted)
    response = command(assisted)
    assert response.status_code == (403 if defect in {"http_role", "sql_role", "disabled", "module", "scope"} else 409), response.text
    assert snapshot(assisted) == before


@pytest.mark.parametrize("defect", ["unknown", "contact", "html", "credential", "risk", "nan", "version", "changed_replay"])
def test_closed_human_input_and_exact_replay_intent(assisted, defect):
    body = json.loads(json.dumps(BODY))
    if defect == "changed_replay":
        assert command(assisted).status_code == 202
        body["fields"]["rent"] += 1
    elif defect == "unknown":
        body["actor_subject_id"] = "forged-human"
    elif defect == "contact":
        body["fields"]["phone"] = "unknown-contact"
    elif defect == "html":
        body["fields"]["address"] = "<script>unsafe</script>"
    elif defect == "credential":
        body["reason"] = "Authorization: Bearer synthetic-forbidden-secret"
    elif defect == "risk":
        body["risk_acknowledged"] = False
    elif defect == "nan":
        body["fields"]["rent"] = "NaN"
    before = snapshot(assisted)
    response = command(assisted, body=body, version=999 if defect == "version" else None)
    assert response.status_code == (409 if defect in {"version", "changed_replay"} else 422), response.text
    assert "synthetic-forbidden-secret" not in response.text and "unsafe</script>" not in response.text
    assert snapshot(assisted) == before


@pytest.mark.parametrize("fault", ["audit", "outbox", "second_proposal"])
def test_proposals_audit_and_request_event_roll_back_atomically(assisted, monkeypatch, fault):
    _, graph, _, _, _, _ = assisted
    def fail(*args, **kwargs):
        raise CanonicalAuditUnavailable("Synthetic proposal atomicity failure")
    if fault == "audit":
        monkeypatch.setattr(graph.audit_sink, "persist", fail)
    elif fault == "outbox":
        monkeypatch.setattr(type(graph), "_outbox", fail)
    else:
        original = graph.engine.execute
        inserted = 0
        def second(sql, *args, **kwargs):
            nonlocal inserted
            result = original(sql, *args, **kwargs)
            if sql.startswith("INSERT INTO intake.human_corrections"):
                inserted += 1
                if inserted == 2:
                    fail()
            return result
        monkeypatch.setattr(graph.engine, "execute", second)
    before = snapshot(assisted)
    response = command(assisted)
    assert response.status_code == 503, response.text
    assert snapshot(assisted) == before


def test_competing_human_requests_create_only_one_complete_proposal_set(assisted):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda key: command(assisted, key=key), ["synthetic-racing-human-request-001", "synthetic-racing-human-request-002"]))
    assert sorted(r.status_code for r in results) == [202, 409], [r.text for r in results]
    _, graph, ids, _, result, _ = assisted
    with graph.transaction(ids["tenant"]):
        rows = graph.engine.query("SELECT correction_id FROM intake.human_corrections WHERE tenant_id=? AND intake_id=?", (ids["tenant"], result["intake_id"]))
        assert len(rows) == 3


def test_unknown_request_commit_reconciles_the_same_ids_without_duplicate_proposals(assisted, monkeypatch):
    _, graph, _, _, _, _ = assisted
    original = type(graph).transaction
    injected = 0
    @contextmanager
    def uncertain(self, *args, **kwargs):
        nonlocal injected
        with original(self, *args, **kwargs):
            yield
        injected += 1
        raise CanonicalAuditUnavailable("Synthetic response loss after actual request commit")
    with monkeypatch.context() as patch:
        patch.setattr(type(graph), "transaction", uncertain)
        assert command(assisted).status_code == 503
    assert injected == 1
    after = snapshot(assisted)
    response = command(assisted)
    assert response.status_code == 202 and response.headers["Idempotency-Replayed"] == "true", response.text
    assert snapshot(assisted) == after


@pytest.mark.parametrize("loss", ["none", "all_runtime", "source", "old_requester"])
def test_manual_withdrawal_is_a_fresh_negative_action_and_preserves_every_proposal(assisted, monkeypatch, loss):
    recheck, graph, ids, db, result, _ = assisted
    response = command(assisted)
    assert response.status_code == 202, response.text
    if loss == "all_runtime":
        retire_settings(monkeypatch)
    elif loss == "source":
        with db.connect() as connection:
            connection.execute("UPDATE intake.source_registry SET kill_switch=true,version=version+1 WHERE source_id='synthetic-feed'")
    elif loss == "old_requester":
        start = recheck[0][0]
        start[1][1]["principal"] = replace(start[1][1]["principal"], subject_id=ids["reviewer"], roles=frozenset({Role.SITE_REVIEWER}))
        with db.connect() as connection:
            connection.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (ids["actor"],))
            connection.execute("INSERT INTO identity.accounts (account_id,tenant_id,username,email,status,created_by) "
                "VALUES (%s,%s,'synthetic-withdrawing-manager','manager@example.invalid','active','synthetic')", (ids["reviewer"], ids["tenant"]))
            connection.execute("INSERT INTO identity.account_roles (account_id,role,granted_by) VALUES (%s,'site_reviewer','synthetic')", (ids["reviewer"],))
            connection.execute("INSERT INTO identity.account_scopes (account_id,modules,clearance) VALUES (%s,'[\"listing\"]'::jsonb,'PUBLIC')", (ids["reviewer"],))
    before = snapshot(assisted)
    cancelled = withdraw(assisted, response.json())
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "WITHDRAWN" and cancelled.json()["job_id"] is None
    after = snapshot(assisted)
    for key in ["intake.human_corrections", "intake.source_snapshots", "intake.parser_runs", "workflow.jobs", "intake.intake_stage_transitions"]:
        assert after[key] == before[key], key
    assert native_row(recheck[0][0], result["intake_id"])["processing_state"] == "AWAITING_ASSISTED_ENTRY"
    replay = withdraw(assisted, response.json())
    assert replay.status_code == 200 and replay.json() == cancelled.json() and replay.headers["Idempotency-Replayed"] == "true"
    assert snapshot(assisted) == after
    with graph.transaction(ids["tenant"]):
        intake = native_row(recheck[0][0], result["intake_id"])
        NativeIntakePendingCommands(graph).require_clear(intake, before=lambda: None)
    if loss == "none":
        assert command(assisted).status_code == 409
        fresh = command(assisted, key="synthetic-new-request-after-withdrawal-002")
        assert fresh.status_code == 202, fresh.text
        assert set(fresh.json()["correction_ids"]).isdisjoint(response.json()["correction_ids"])


@pytest.mark.parametrize("tamper", ["producer", "schema", "request_hash", "audit", "retention"])
def test_a_marker_without_the_full_negative_journal_cannot_hide_pending_work(assisted, tamper):
    recheck, graph, ids, _, result, _ = assisted
    requested = command(assisted)
    assert requested.status_code == 202, requested.text
    cancelled = withdraw(assisted, requested.json())
    assert cancelled.status_code == 200, cancelled.text
    with graph.transaction(ids["tenant"]):
        if tamper in {"producer", "schema", "request_hash"}:
            path, value = {"producer": ("{producer}", "untrusted_service"), "schema": ("{schema_ref}", "#/payloads/IntakeStateChangedV1"),
                "request_hash": ("{payload,original_request_sha256}", "0" * 64)}[tamper]
            graph.engine.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,?::text[],to_jsonb(?::text)) WHERE tenant_id=? AND event_id=?",
                (path, value, ids["tenant"], cancelled.json()["outbox_event_id"]))
        elif tamper == "audit":
            graph.engine.execute("UPDATE audit.audit_events SET result='DENIED' WHERE tenant_id=? AND audit_event_id=?", (ids["tenant"], cancelled.json()["audit_event_id"]))
        else:
            graph.engine.execute("UPDATE workflow.outbox_events SET retention_until=retention_until+interval '1 day' WHERE tenant_id=? AND event_id=?", (ids["tenant"], cancelled.json()["outbox_event_id"]))
    with graph.transaction(ids["tenant"]):
        with pytest.raises(NativeAssistedEntryConflict):
            NativeIntakePendingCommands(graph).require_clear(native_row(recheck[0][0], result["intake_id"]), before=lambda: None)
    before = snapshot(assisted)
    refused = command(assisted, key="synthetic-invalid-marker-blocks-new-001")
    assert refused.status_code == 409, refused.text
    expected = "NATIVE_COMMAND_WITHDRAWAL_AUDIT_MISSING" if tamper == "audit" else "NATIVE_COMMAND_WITHDRAWAL_EVENT_CONFLICT"
    assert refused.json()["detail"] == expected
    assert snapshot(assisted) == before


def test_unadmitted_recheck_can_be_withdrawn_without_runtime_keys_and_cannot_be_admitted(recheck, monkeypatch):
    (start, prior), owner, _ = recheck
    response = request_recheck(recheck)
    assert response.status_code == 202, response.text
    case = recheck, start[0][0], start[0][1], start[0][3], prior, {}
    with monkeypatch.context() as patch:
        retire_settings(patch)
        cancelled = withdraw(case, response.json(), kind="source-policy-rechecks")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "WITHDRAWN", cancelled.text
    with pytest.raises(NativeAssistedEntryConflict, match="ALREADY_WITHDRAWN"):
        owner.admit_event(start[0][1]["tenant"], response.json()["request_id"])
    assert request_recheck(recheck, key="synthetic-recheck-after-explicit-withdrawal-002").status_code == 202


def test_active_recheck_cancellation_changes_only_business_flag_then_real_worker_settles(recheck):
    from apps.worker.oday_worker.main import ODayWorker
    from tests.integration.test_native_intake_identity_postgresql import defer_audit

    (start, prior), owner, _ = recheck
    graph, ids, _, _ = start[0]
    response = request_recheck(recheck)
    assert response.status_code == 202, response.text
    admitted = owner.admit_event(ids["tenant"], response.json()["request_id"])
    physical = graph.queue.get(admitted["job_id"])
    case = recheck, graph, ids, start[0][3], prior, {}
    before = state(recheck[0])
    cancelled = withdraw(case, response.json(), kind="source-policy-rechecks")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLATION_REQUESTED", cancelled.text
    assert graph.queue.get(admitted["job_id"]) == physical
    after = state(recheck[0])
    for table in ["intake.human_corrections", "intake.source_snapshots", "intake.intake_stage_transitions"]:
        assert before[table] == after[table]
    with graph.transaction(ids["tenant"]):
        with pytest.raises(NativeAssistedEntryConflict, match="ACTIVE_JOB"):
            NativeIntakePendingCommands(graph).require_clear(native_row(start, prior["intake_id"]), before=lambda: None)
    defer_audit(graph)
    assert ODayWorker(persistence=start[2]).run_once() is True
    assert graph.queue.get(admitted["job_id"]).status == JobStatus.CANCELLED
    assert native_row(start, prior["intake_id"])["processing_state"] == "AWAITING_SOURCE_AUTHORIZATION"
