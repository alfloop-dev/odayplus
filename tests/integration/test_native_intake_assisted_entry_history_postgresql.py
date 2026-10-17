"""Historical provenance survives retired keys and never grants current work."""

import hashlib
import os
from datetime import timedelta

import pytest
from fastapi import HTTPException

from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_assisted_entry_source import (
    NativeAssistedEntrySourceProof,
)
from modules.listing.application.native_intake_requester_authority import (
    current_native_assisted_principal,
)
from modules.listing.application.native_intake_source_policy_recheck import (
    SIGNING_KEY_ENV,
    NativeIntakeSourcePolicyRecheckUnavailable,
)
from modules.listing.application.native_intake_source_policy_recheck_history import (
    NativeHistoricalRecheckProof,
)
from modules.listing.domain.identity_graph import IdentityConflictError
from shared.jobs.queue import JobFenceRejectedError
from tests.integration.test_native_intake_source_policy_postgresql import drive_upstream, native_row
from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
    drive,
    request,
    state,
)
from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
    recheck as recheck,
)
from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
    start_case as start_case,
)
from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
    waiting as waiting,
)

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def past_manual(recheck):
    (start, _), owner, upstream = recheck
    graph, ids, _, db = start[0]
    with db.connect() as connection:
        connection.execute("UPDATE intake.source_registry SET assisted_entry_approval_ref='synthetic-explicit-manual',"
            "assisted_entry_approved_by=%s,assisted_entry_review_expires_at=clock_timestamp()+interval '1 hour',"
            "version=version+1 WHERE source_id='synthetic-feed'", (ids["reviewer"],))
    response = request(recheck)
    assert response.status_code == 202, response.text
    checked = drive(recheck, response)
    source = upstream[1]
    admitted = source.admit_event(ids["tenant"], checked["outbox_event_id"])
    result = drive_upstream(upstream, admitted, source)
    assert result["processing_state"] == "AWAITING_ASSISTED_ENTRY"
    return recheck, graph, ids, db, result, checked


def history(case, *, bounded=False):
    recheck, graph, ids, _, result, _ = case
    start = recheck[0][0]
    intake = native_row(start, result["intake_id"])
    with graph.transaction(ids["tenant"]):
        source = NativeAssistedEntrySourceProof(graph)
        deadline = source.source._clock() + timedelta(milliseconds=750)
        before = (lambda: source.source._admission_budget(deadline)) if bounded else (lambda: None)
        proof = source.current_parent(intake, before=before)
        assert proof.intake_id == result["intake_id"] and proof.intake_version == result["version"]
        assert proof.effective_policy == "AUTH_REQUIRED" and proof.event_id == result["outbox_event_id"]
        return proof


def retire_settings(monkeypatch):
    for name in list(os.environ):
        if name.startswith("ODP_NATIVE_INTAKE_") and (
            name.endswith("SIGNING_KEY") or name.endswith("POLICY_JSON") or name.endswith("STORAGE_JSON")
        ):
            monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("all_settings", [False, True])
def test_exact_read_proof_survives_retired_key_and_positive_original_verifier_stays_strict(past_manual, monkeypatch, all_settings):
    recheck, graph, ids, _, result, checked = past_manual
    before = state(recheck[0])
    if all_settings:
        retire_settings(monkeypatch)
    else:
        monkeypatch.delenv(SIGNING_KEY_ENV)
    # Genuine counter: the unchanged execution-oriented legacy verifier cannot
    # validate with a retired key. Its required authentication is not weakened.
    with graph.transaction(ids["tenant"]):
        with pytest.raises(NativeIntakeSourcePolicyRecheckUnavailable):
            recheck[1]._source_producer(ids["tenant"], checked["outbox_event_id"], lambda: None)
    proof = history(past_manual)
    assert proof.root_identity_event_id == checked["root_identity_event_id"]
    assert proof.event_id == result["outbox_event_id"] and state(recheck[0]) == before


def test_full_read_proof_honours_the_same_750ms_root_budget(past_manual, monkeypatch):
    retire_settings(monkeypatch)
    assert history(past_manual, bounded=True).event_sha256


def test_deep_history_sql_reads_retain_the_callers_budget_even_without_an_explicit_inner_callback(past_manual, monkeypatch):
    recheck, graph, ids, _, result, _ = past_manual
    retire_settings(monkeypatch)
    original = NativeHistoricalRecheckProof._committed_result
    inside, checks = False, 0

    def deep(self, *args, **kwargs):
        nonlocal inside
        inside = True
        try:
            return original(self, *args, **kwargs)
        finally:
            inside = False

    monkeypatch.setattr(NativeHistoricalRecheckProof, "_committed_result", deep)
    old = state(recheck[0])
    with graph.transaction(ids["tenant"]):
        source = NativeAssistedEntrySourceProof(graph)
        def before():
            nonlocal checks
            if inside:
                checks += 1
                raise NativeAssistedEntryConflict("SYNTHETIC_DEEP_ROOT_BUDGET_EXPIRED")
        with pytest.raises(NativeAssistedEntryConflict, match="DEEP_ROOT_BUDGET_EXPIRED"):
            source.current_parent(native_row(recheck[0][0], result["intake_id"]), before=before)
    assert checks == 1
    assert state(recheck[0]) == old


def test_historical_source_approval_never_overrides_a_current_kill_switch(past_manual, monkeypatch):
    recheck, graph, ids, db, result, _ = past_manual
    retire_settings(monkeypatch)
    with db.connect() as connection:
        connection.execute("UPDATE intake.source_registry SET kill_switch=true,version=version+1 WHERE source_id='synthetic-feed'")
    assert history(past_manual).intake_id == result["intake_id"]
    with graph.transaction(ids["tenant"]):
        intake = native_row(recheck[0][0], result["intake_id"])
        with pytest.raises(NativeAssistedEntryConflict, match="CURRENT_MANUAL_APPROVAL_REQUIRED"):
            NativeAssistedEntrySourceProof(graph).current_manual_source(intake, before=lambda: None)


def test_historical_human_requester_does_not_restore_current_revoked_account(past_manual, monkeypatch):
    recheck, graph, ids, db, result, _ = past_manual
    retire_settings(monkeypatch)
    with db.connect() as connection:
        connection.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (ids["actor"],))
    assert history(past_manual).intake_id == result["intake_id"]
    with graph.transaction(ids["tenant"]):
        with pytest.raises((HTTPException, PermissionError)):
            current_native_assisted_principal(graph.engine, ids["actor"], native_row(recheck[0][0], result["intake_id"]),
                risk_summary="Only authenticate the current initiating human", risk_acknowledged=True, before=lambda: None)


@pytest.mark.parametrize("tamper", ["physical_token", "token_and_hash", "admission_audit", "terminal_audit", "transition"])
def test_changed_token_or_canonical_chain_cannot_be_hidden_by_retired_keys(past_manual, monkeypatch, tamper):
    _, graph, ids, db, _, checked = past_manual
    retire_settings(monkeypatch)
    with graph.transaction(ids["tenant"]):
        job = graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (ids["tenant"], checked["job_id"]))
        payload = job["payload"]
    if tamper in {"physical_token", "token_and_hash"}:
        token = str(payload["delivery_generation"]) + "." + "f" * 64
        with db.connect() as connection:
            changed = connection.execute("UPDATE odp_runtime.durable_jobs SET payload_json=jsonb_set(payload_json,'{attempt_token}',to_jsonb(%s::text)) "
                "WHERE job_id=%s", (token, checked["job_id"]))
            assert changed.rowcount == 1
            if tamper == "token_and_hash":
                changed = connection.execute("UPDATE workflow.jobs SET payload=jsonb_set(payload,'{attempt_token_sha256}',to_jsonb(%s::text)) "
                    "WHERE tenant_id=%s AND job_id=%s", (hashlib.sha256(token.encode()).hexdigest(), ids["tenant"], checked["job_id"]))
                assert changed.rowcount == 1
        assert graph.queue.get(checked["job_id"]).payload["attempt_token"] == token
        if tamper == "token_and_hash":
            with graph.transaction(ids["tenant"]):
                changed = graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                    (ids["tenant"], checked["job_id"]))
                assert changed["payload"]["attempt_token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
    elif tamper in {"admission_audit", "terminal_audit"}:
        audit_id = payload["admission_receipt"]["audit_event_id"] if tamper == "admission_audit" else checked["audit_event_id"]
        with db.connect() as connection:
            connection.execute("UPDATE audit.audit_events SET after_value=jsonb_set(after_value,"
                "'{native_recheck_provenance,attempt_token_sha256}',to_jsonb(%s::text)) WHERE tenant_id=%s AND audit_event_id=%s",
                ("0" * 64, ids["tenant"], audit_id))
    else:
        with db.connect() as connection:
            connection.execute("UPDATE intake.intake_stage_transitions SET reason_code='UNPROVEN' "
                "WHERE tenant_id=%s AND transition_id=%s", (ids["tenant"], checked["transition_id"]))
    with pytest.raises((IdentityConflictError, JobFenceRejectedError)):
        history(past_manual)
