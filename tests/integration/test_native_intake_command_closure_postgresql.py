"""Real closure journals, forged terminal pairs and bounded partition progress."""

import json
import sys
from datetime import timedelta
from uuid import UUID, uuid4, uuid5

import pytest

from apps.worker.oday_worker.main import ODayWorker
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_pending_commands import NativeIntakePendingCommands
from modules.listing.application.native_intake_source_policy import NativeIntakeSourcePolicyOwner
from shared.jobs.queue import JobRequest, JobStatus
from tests.integration.test_native_intake_assisted_entry_history_postgresql import retire_settings
from tests.integration.test_native_intake_assisted_recheck_postgresql import (
    assisted as assisted,
)
from tests.integration.test_native_intake_assisted_recheck_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_assisted_recheck_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_assisted_recheck_postgresql import (
    command,
    snapshot,
    withdraw,
)
from tests.integration.test_native_intake_assisted_recheck_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_assisted_recheck_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_assisted_recheck_postgresql import (
    past_manual as past_manual,
)
from tests.integration.test_native_intake_assisted_recheck_postgresql import (
    recheck as recheck,
)
from tests.integration.test_native_intake_assisted_recheck_postgresql import (
    request as assisted_recheck,
)
from tests.integration.test_native_intake_assisted_recheck_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_assisted_recheck_postgresql import (
    start_case as start_case,
)
from tests.integration.test_native_intake_assisted_recheck_postgresql import (
    waiting as waiting,
)
from tests.integration.test_native_intake_identity_postgresql import defer_audit
from tests.integration.test_native_intake_source_policy_recheck_postgresql import request, state

pytestmark = pytest.mark.requires_live_env
JOB_TYPE = "native-intake-source-policy-recheck"


def recheck_preserved_business_state(case):
    rows = state(case[0])
    return {key: rows[key] for key in ("intake.intakes", "intake.intake_stage_transitions",
        "identity.match_cases", "expansion.listings", "source_registry", "intake.source_snapshots",
        "intake.parser_runs", "intake.human_corrections", "expansion.promotion_decisions")}


def retire_recheck_execution(monkeypatch, loss):
    from modules.listing.application.native_intake_source_policy_recheck import (
        POLICY_ENV,
        SIGNING_KEY_ENV,
    )

    if loss == "all_settings":
        retire_settings(monkeypatch)
    elif loss == "key_rotated":
        monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-retired-key-replacement-" * 2)
    else:
        monkeypatch.delenv(POLICY_ENV if loss == "policy_removed" else SIGNING_KEY_ENV)


def case_for(recheck):
    (start, prior), _, _ = recheck
    graph, ids, _, db = start[0]
    return recheck, graph, ids, db, prior, {}


def clear(case):
    _, graph, ids, _, prior, _ = case
    with graph.transaction(ids["tenant"]):
        source = NativeIntakeSourcePolicyOwner(graph)
        deadline = source._clock() + timedelta(milliseconds=750)
        source._admission_budget(deadline)
        intake = graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
            (ids["tenant"], prior["intake_id"]))
        NativeIntakePendingCommands(graph).require_clear(intake, before=lambda: source._admission_budget(deadline))


def negative(recheck, cancelled):
    case = case_for(recheck)
    _, graph, ids, db, _, _ = case
    response = request(recheck)
    assert response.status_code == 202, response.text
    admitted = recheck[1].admit_event(ids["tenant"], response.json()["request_id"])
    if cancelled:
        receipt = withdraw(case, response.json(), kind="source-policy-rechecks")
        assert receipt.status_code == 200 and receipt.json()["status"] == "CANCELLATION_REQUESTED", receipt.text
    else:
        with db.connect() as connection:
            connection.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (ids["actor"],))
    defer_audit(graph)
    assert ODayWorker(persistence=recheck[0][0][2]).run_once() is True
    assert graph.queue.get(admitted["job_id"]).status == (JobStatus.CANCELLED if cancelled else JobStatus.FAILED)
    return case, response.json(), admitted


@pytest.mark.parametrize("status,physical,delivery_state", [("SUCCEEDED", "succeeded", None),
    ("FAILED", "failed", "dead_letter"), ("CANCELLED", "cancelled", None)])
def test_terminal_status_pairs_without_real_receipts_never_clear_pending_work(recheck, status, physical, delivery_state):
    case = case_for(recheck)
    _, graph, ids, _, _, _ = case
    response = request(recheck)
    assert response.status_code == 202, response.text
    admitted = recheck[1].admit_event(ids["tenant"], response.json()["request_id"])
    # Deliberate forged pair: neither the owner nor its worker wrote a result.
    with graph.transaction(ids["tenant"]):
        graph.engine.execute("UPDATE workflow.jobs SET status=? WHERE tenant_id=? AND job_id=?", (status, ids["tenant"], admitted["job_id"]))
        graph.engine.execute("UPDATE durable_jobs SET status=?,delivery_state=? WHERE job_id=?", (physical, delivery_state, admitted["job_id"]))
    before = state(recheck[0])
    with pytest.raises(ValueError):
        clear(case)
    assert state(recheck[0]) == before


def test_real_success_is_closed_history_even_after_all_current_keys_and_settings_retire(past_manual, monkeypatch):
    before = snapshot(past_manual)
    retire_settings(monkeypatch)
    clear(past_manual)
    assert snapshot(past_manual) == before


def test_a_forged_manual_terminal_pair_cannot_stand_in_for_the_missing_owners_receipt(assisted):
    _, graph, ids, _, prior, _ = assisted
    response = command(assisted)
    assert response.status_code == 202, response.text
    request_id = response.json()["request_id"]
    job_type = "native-intake-assisted-entry"
    job_id = str(uuid5(UUID(request_id), job_type))
    with graph.transaction(ids["tenant"]):
        graph.engine.execute("INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,status,"
            "checkpoint,max_attempts,timeout_at,payload,correlation_id,idempotency_key) "
            "VALUES (?,?,?,'intake_assisted_entry',?,'SUCCEEDED','PARSING',3,clock_timestamp()+interval '60 seconds',?::jsonb,?,?)",
            (job_id, ids["tenant"], job_type, request_id, json.dumps({"request_id": request_id, "intake_id": prior["intake_id"]}),
             response.json()["correlation_id"], request_id))
        _, created = graph.queue.enqueue(JobRequest(job_type, {"job_id": job_id, "tenant_id": ids["tenant"]},
            job_id=job_id, idempotency_key=job_type + ":" + ids["tenant"] + ":" + request_id),
            correlation_id=response.json()["correlation_id"])
        assert created
        graph.engine.execute("UPDATE durable_jobs SET status='succeeded',delivery_state=NULL WHERE job_id=?", (job_id,))
    before = snapshot(assisted)
    with pytest.raises(ValueError):
        clear(assisted)
    assert snapshot(assisted) == before


@pytest.mark.parametrize("cancelled", [False, True])
def test_real_four_field_negative_writer_is_proved_without_current_keys_or_original_human(recheck, monkeypatch, cancelled):
    case, _, _ = negative(recheck, cancelled)
    before = state(recheck[0])
    retire_settings(monkeypatch)
    clear(case)
    assert state(recheck[0]) == before


@pytest.mark.parametrize("defect", ["audit", "delivery_error", "business_fence", "dead_letter", "result"])
def test_mutated_real_negative_evidence_cannot_clear_an_intake(recheck, defect):
    case, response, admitted = negative(recheck, False)
    _, graph, ids, _, _, _ = case
    with graph.transaction(ids["tenant"]):
        if defect == "audit":
            graph.engine.execute("DELETE FROM audit.audit_events WHERE tenant_id=? "
                "AND action='intake.source_policy_recheck_delivery_settled.v1' AND causation_id=?", (ids["tenant"], response["request_id"]))
        elif defect == "delivery_error":
            graph.engine.execute("UPDATE durable_jobs SET error_message='FORGED' WHERE job_id=?", (admitted["job_id"],))
        elif defect == "business_fence":
            graph.engine.execute("UPDATE workflow.jobs SET fence_token=fence_token+1 WHERE tenant_id=? AND job_id=?", (ids["tenant"], admitted["job_id"]))
        elif defect == "dead_letter":
            graph.engine.execute("DELETE FROM workflow.outbox_events WHERE tenant_id=? AND aggregate_type='job' "
                "AND aggregate_id=? AND event_type='job.dead_lettered'", (ids["tenant"], admitted["job_id"]))
        else:
            graph.engine.execute("UPDATE workflow.jobs SET result='{}'::jsonb WHERE tenant_id=? AND job_id=?", (ids["tenant"], admitted["job_id"]))
    before = state(recheck[0])
    with pytest.raises(ValueError):
        clear(case)
    assert state(recheck[0]) == before


@pytest.mark.parametrize("defect", ["missing", "moved_payload", "moved_payload_and_partition"])
def test_request_audit_still_discovers_a_deleted_or_rehomed_original(recheck, defect):
    case = case_for(recheck)
    _, graph, ids, _, _, _ = case
    response = request(recheck)
    assert response.status_code == 202, response.text
    assert withdraw(case, response.json(), kind="source-policy-rechecks").status_code == 200
    with graph.transaction(ids["tenant"]):
        if defect == "missing":
            graph.engine.execute("DELETE FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?", (ids["tenant"], response.json()["request_id"]))
        else:
            foreign = str(uuid4())
            graph.engine.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,'{payload,intake_id}',to_jsonb(?::text)) "
                "WHERE tenant_id=? AND event_id=?", (foreign, ids["tenant"], response.json()["request_id"]))
            if defect == "moved_payload_and_partition":
                graph.engine.execute("UPDATE workflow.outbox_events SET partition_key=? WHERE tenant_id=? AND event_id=?",
                    (ids["tenant"] + ":" + foreign, ids["tenant"], response.json()["request_id"]))
    before = state(recheck[0])
    with pytest.raises(ValueError):
        clear(case)
    assert state(recheck[0]) == before


@pytest.mark.parametrize("defect", ["actor", "service", "resource", "reason", "key_hash"])
def test_batched_audit_reads_preserve_every_original_predicate(recheck, defect):
    case = case_for(recheck)
    _, graph, ids, _, _, _ = case
    response = request(recheck)
    assert response.status_code == 202, response.text
    cancelled = withdraw(case, response.json(), kind="source-policy-rechecks")
    assert cancelled.status_code == 200, cancelled.text
    with graph.transaction(ids["tenant"]):
        if defect == "key_hash":
            graph.engine.execute("UPDATE audit.audit_events SET after_value=jsonb_set(after_value,'{idempotency_key_sha256}',to_jsonb(?::text)) "
                "WHERE tenant_id=? AND audit_event_id=?", ("0" * 64, ids["tenant"], cancelled.json()["audit_event_id"]))
        else:
            statement, value = {
                "actor": ("UPDATE audit.audit_events SET actor_subject_id=? WHERE tenant_id=? AND audit_event_id=?", ids["reviewer"]),
                "service": ("UPDATE audit.audit_events SET service_principal=? WHERE tenant_id=? AND audit_event_id=?", "forged-service"),
                "resource": ("UPDATE audit.audit_events SET resource_id=? WHERE tenant_id=? AND audit_event_id=?", str(uuid4())),
                "reason": ("UPDATE audit.audit_events SET reason=? WHERE tenant_id=? AND audit_event_id=?", "A different synthetic reason"),
            }[defect]
            graph.engine.execute(statement,
                (value, ids["tenant"], cancelled.json()["audit_event_id"]))
    before = state(recheck[0])
    with pytest.raises(ValueError):
        clear(case)
    assert state(recheck[0]) == before


def test_jobless_withdrawal_never_hides_an_actual_orphan_physical_reservation(recheck):
    case = case_for(recheck)
    _, graph, ids, _, _, _ = case
    response = request(recheck)
    assert response.status_code == 202, response.text
    assert withdraw(case, response.json(), kind="source-policy-rechecks").status_code == 200
    request_id = response.json()["request_id"]
    job_id = str(uuid5(UUID(request_id), JOB_TYPE))
    with graph.transaction(ids["tenant"]):
        token = recheck[1].auth.sign(job_id, ids["tenant"], 1)
        _, created = graph.queue.enqueue(JobRequest(JOB_TYPE,
            {"job_id": job_id, "tenant_id": ids["tenant"], "attempt_token": token}, job_id=job_id,
            idempotency_key=JOB_TYPE + ":" + ids["tenant"] + ":" + request_id), correlation_id=response.json()["correlation_id"])
        assert created
    with pytest.raises(NativeAssistedEntryConflict, match="ORPHAN_PHYSICAL_JOB"):
        clear(case)


def test_over_121_real_withdrawals_across_both_lanes_allow_new_work_and_partition_progress(assisted, record_property):
    recheck, graph, ids, _, prior, _ = assisted
    for number in range(122):
        manual = number == 60
        response = command(assisted, key=f"synthetic-closed-history-manual-{number:04d}") if manual else assisted_recheck(
            assisted, prior["version"], key=f"synthetic-closed-history-recheck-{number:04d}")
        assert response.status_code == 202, (number, response.text)
        cancelled = withdraw(assisted, response.json(), kind="assisted-entry-completions" if manual else "source-policy-rechecks",
            key=f"synthetic-closed-history-withdrawal-{number:04d}")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "WITHDRAWN", (number, cancelled.text)
    human = command(assisted, key="synthetic-manual-after-more-than-121-real-closures")
    assert human.status_code == 202, human.text
    assert withdraw(assisted, human.json(), key="synthetic-withdraw-new-manual-for-recheck").status_code == 200
    fresh = assisted_recheck(assisted, prior["version"], key="synthetic-recheck-after-123-real-closures")
    assert fresh.status_code == 202, fresh.text
    record_property("actual_qualified_jobless_withdrawals", 123)
    before = snapshot(assisted)
    admitted, cursor, seen = [], None, set()
    withdrawn_count, scanned_count = 0, 0
    while not admitted:
        admitted = list(recheck[1].admit_partition(ids["tenant"], limit=1, cursor=cursor))
        progress = recheck[1].last_partition_progress
        assert progress["conflicts"] == 0, progress
        assert progress["scanned"] > 0, progress
        withdrawn_count += progress["withdrawn"]
        scanned_count += progress["scanned"]
        assert scanned_count <= 122, progress
        if not admitted:
            assert progress["has_more"] is True, progress
            cursor = progress["next_cursor"]
            assert cursor and cursor not in seen, progress
            seen.add(cursor)
    assert len(admitted) == 1 and admitted[0]["request_id"] == fresh.json()["request_id"]
    assert withdrawn_count == 121
    assert scanned_count == 122
    assert recheck[1].last_partition_progress["has_more"] is False
    assert snapshot(assisted)["intake.human_corrections"] == before["intake.human_corrections"]
    record_property("actual_recheck_closures_scanned_before_new_admission", withdrawn_count)


def test_operational_scan_bound_returns_a_real_resumable_cursor(recheck, monkeypatch):
    import modules.listing.application.native_intake_source_policy_recheck_partition as partition

    case = case_for(recheck)
    ids = case[2]
    for number in range(3):
        response = request(recheck, key=f"synthetic-partition-continuation-old-{number}")
        assert response.status_code == 202, response.text
        assert withdraw(case, response.json(), kind="source-policy-rechecks", key=f"synthetic-partition-withdraw-{number}").status_code == 200
    response = request(recheck, key="synthetic-partition-continuation-current")
    assert response.status_code == 202, response.text
    monkeypatch.setattr(partition, "MAX_SCAN_EVENTS", 2)
    assert list(recheck[1].admit_partition(ids["tenant"], limit=1)) == []
    progress = recheck[1].last_partition_progress
    assert progress["scanned"] == progress["withdrawn"] == 2 and progress["has_more"] is True
    admitted = list(recheck[1].admit_partition(ids["tenant"], limit=1, cursor=progress["next_cursor"]))
    assert len(admitted) == 1 and admitted[0]["request_id"] == response.json()["request_id"]
    assert recheck[1].last_partition_progress["withdrawn"] == 1 and recheck[1].last_partition_progress["has_more"] is False


def test_partition_refuses_an_outer_root_instead_of_extending_its_budget(recheck):
    case = case_for(recheck)
    _, graph, ids, _, _, _ = case
    before = state(recheck[0])
    with graph.transaction(ids["tenant"]):
        with pytest.raises(NativeAssistedEntryConflict, match="PARTITION_OUTER_ROOT_REJECTED"):
            list(recheck[1].admit_partition(ids["tenant"], limit=1))
    assert state(recheck[0]) == before


def test_invalid_withdrawal_proof_is_reported_and_never_skipped_as_closed(recheck):
    case = case_for(recheck)
    _, graph, ids, _, _, _ = case
    old = request(recheck)
    assert old.status_code == 202, old.text
    withdrawn = withdraw(case, old.json(), kind="source-policy-rechecks")
    assert withdrawn.status_code == 200, withdrawn.text
    fresh = request(recheck, key="synthetic-partition-after-once-genuine-withdrawal")
    assert fresh.status_code == 202, fresh.text
    with graph.transaction(ids["tenant"]):
        graph.engine.execute("UPDATE audit.audit_events SET reason='A forged replacement reason' "
            "WHERE tenant_id=? AND audit_event_id=?", (ids["tenant"], withdrawn.json()["audit_event_id"]))
    before = state(recheck[0])
    assert list(recheck[1].admit_partition(ids["tenant"], limit=1)) == []
    progress = recheck[1].last_partition_progress
    assert progress["scanned"] == progress["conflicts"] == 2 and progress["withdrawn"] == 0
    assert state(recheck[0]) == before


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("loss", ["key_removed", "key_rotated", "policy_removed", "all_settings"])
def test_actual_default_worker_settles_after_execution_retirement_without_changing_business_state(
        recheck, monkeypatch, cancelled, loss):
    case = case_for(recheck)
    _, graph, ids, _, _, _ = case
    response = request(recheck)
    assert response.status_code == 202, response.text
    admitted = recheck[1].admit_event(ids["tenant"], response.json()["request_id"])
    if cancelled:
        withdrawal = withdraw(case, response.json(), kind="source-policy-rechecks")
        assert withdrawal.status_code == 200 and withdrawal.json()["status"] == "CANCELLATION_REQUESTED"
    before = recheck_preserved_business_state(recheck)
    retire_recheck_execution(monkeypatch, loss)
    defer_audit(graph)
    assert ODayWorker(persistence=recheck[0][0][2]).run_once() is True
    delivery = graph.queue.get(admitted["job_id"])
    assert delivery.status == (JobStatus.CANCELLED if cancelled else JobStatus.FAILED)
    assert recheck_preserved_business_state(recheck) == before
    # The same full reader must both release exclusion and prove an unknown
    # terminal replay, without another audit/dead letter or current key.
    clear(case)
    settled = state(recheck[0])
    assert recheck[1].settle_failure(delivery, RuntimeError("synthetic repeated outcome observation")) is None
    assert state(recheck[0]) == settled


@pytest.mark.parametrize("loss", ["key_removed", "key_rotated", "policy_removed", "requester_role"])
def test_retryable_error_cannot_schedule_another_attempt_without_current_execution_authority(recheck, monkeypatch, loss):
    from psycopg import errors

    from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
        claimed_recheck,
    )

    _, _, delivery = claimed_recheck(recheck)
    (start, _), owner, _ = recheck
    graph, ids, _, db = start[0]
    before = recheck_preserved_business_state(recheck)
    if loss == "requester_role":
        with db.connect() as connection:
            connection.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (ids["actor"],))
    else:
        retire_recheck_execution(monkeypatch, loss)
    assert owner.settle_failure(delivery, errors.SerializationFailure("synthetic retryable error")) is None
    assert graph.queue.get(delivery.job_id).status == JobStatus.FAILED
    assert recheck_preserved_business_state(recheck) == before
    clear(case_for(recheck))


def test_a_real_retry_keeps_original_budget_and_can_settle_after_later_key_retirement(recheck, monkeypatch):
    from psycopg import errors

    from modules.listing.application.native_intake_source_policy import _time
    from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
        claimed_recheck,
    )

    _, admitted, delivery = claimed_recheck(recheck)
    (start, _), owner, _ = recheck
    graph, ids, _, _ = start[0]
    before = recheck_preserved_business_state(recheck)
    assert owner.settle_failure(delivery, errors.SerializationFailure("synthetic retryable first attempt")) is None
    queued = graph.queue.get(delivery.job_id)
    assert queued.status == JobStatus.QUEUED and queued.payload == delivery.payload
    with graph.transaction(ids["tenant"]):
        job = graph._one("SELECT status,attempt,timeout_at FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], delivery.job_id))
        assert job["status"] == "RETRYING" and job["attempt"] == 1
        assert _time(job["timeout_at"]) == _time(admitted["timeout_at"])
        # Advance only this synthetic delivery's availability; this does not
        # claim a real scheduler waited through the configured backoff.
        graph.engine.execute("UPDATE durable_jobs SET available_at=clock_timestamp() WHERE job_id=?", (delivery.job_id,))
    retire_settings(monkeypatch)
    defer_audit(graph)
    assert ODayWorker(persistence=start[2]).run_once() is True
    assert graph.queue.get(delivery.job_id).status == JobStatus.FAILED
    assert recheck_preserved_business_state(recheck) == before
    clear(case_for(recheck))


@pytest.mark.parametrize("defect", ["worker", "physical_fence", "expired_lease", "business_fence",
    "admission_audit", "mutable_token_pair", "request_audit"])
def test_retired_keys_never_let_stale_owners_or_mutable_proof_pairs_settle_a_job(recheck, monkeypatch, defect):
    import hashlib
    from dataclasses import replace

    from modules.listing.domain.identity_graph import IdentityGraphError, IdentityNotFoundError
    from shared.jobs.queue import JobFenceRejectedError
    from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
        claimed_recheck,
    )

    request_receipt, admitted, delivery = claimed_recheck(recheck)
    (start, _), owner, _ = recheck
    graph, ids, _, _ = start[0]
    with graph.transaction(ids["tenant"]):
        if defect == "worker":
            delivery = replace(delivery, locked_by="synthetic-different-worker")
        elif defect == "physical_fence":
            graph.engine.execute("UPDATE durable_jobs SET fence_token=fence_token+1 WHERE job_id=?", (delivery.job_id,))
        elif defect == "expired_lease":
            graph.engine.execute("UPDATE durable_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=?",
                (delivery.job_id,))
        elif defect == "business_fence":
            graph.engine.execute("UPDATE workflow.jobs SET fence_token=fence_token+1 WHERE tenant_id=? AND job_id=?",
                (ids["tenant"], delivery.job_id))
        elif defect == "mutable_token_pair":
            token = "1." + "f" * 64
            forged = {**delivery.payload, "attempt_token": token}
            graph.engine.execute("UPDATE durable_jobs SET payload_json=? WHERE job_id=?", (json.dumps(forged), delivery.job_id))
            graph.engine.execute("UPDATE workflow.jobs SET payload=jsonb_set(payload,'{attempt_token_sha256}',to_jsonb(?::text)) "
                "WHERE tenant_id=? AND job_id=?", (hashlib.sha256(token.encode()).hexdigest(), ids["tenant"], delivery.job_id))
            delivery = graph.queue.get(delivery.job_id)
        else:
            audit_id = admitted["audit_event_id"] if defect == "admission_audit" else request_receipt["audit_event_id"]
            graph.engine.execute("DELETE FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=?", (ids["tenant"], audit_id))
    retire_settings(monkeypatch)
    before = state(recheck[0])
    with pytest.raises((ValueError, IdentityGraphError, JobFenceRejectedError)) as raised:
        owner.settle_failure(delivery, RuntimeError("synthetic retired execution"))
    if defect in {"admission_audit", "request_audit"}:
        assert isinstance(raised.value, IdentityNotFoundError)
        assert str(raised.value) == "CANONICAL_IDENTITY_RESOURCE_NOT_FOUND"
    else:
        expected_code = {"worker": "NATIVE_RECHECK_DELIVERY_NOT_OWNED",
            "physical_fence": "NATIVE_RECHECK_DELIVERY_NOT_OWNED", "expired_lease": "NATIVE_RECHECK_DELIVERY_NOT_OWNED",
            "business_fence": "NATIVE_RECHECK_UNKNOWN_ATTEMPT_OUTCOME",
            "mutable_token_pair": "NATIVE_RECHECK_ADMISSION_AUDIT_CONFLICT"}[defect]
        assert str(raised.value) == expected_code
    assert state(recheck[0]) == before


@pytest.mark.parametrize("fault", ["audit", "outbox", "physical_settlement"])
def test_retired_key_negative_job_audit_event_and_delivery_roll_back_together(recheck, monkeypatch, fault):
    from shared.audit.errors import CanonicalAuditUnavailable
    from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
        claimed_recheck,
    )

    _, _, delivery = claimed_recheck(recheck)
    owner, graph = recheck[1], recheck[0][0][0][0]
    retire_settings(monkeypatch)
    before = state(recheck[0])

    def fail(*args, **kwargs):
        raise CanonicalAuditUnavailable("Synthetic negative settlement rollback")

    with monkeypatch.context() as patch:
        if fault == "audit":
            patch.setattr(graph.audit_sink, "persist", fail)
        elif fault == "outbox":
            patch.setattr(type(graph), "_outbox", fail)
        else:
            update = graph.queue.update_status

            def physical_failure(*args, **kwargs):
                update(*args, **kwargs)
                fail()

            patch.setattr(graph.queue, "update_status", physical_failure)
        with pytest.raises(CanonicalAuditUnavailable, match="Synthetic negative settlement rollback"):
            owner.settle_failure(delivery, RuntimeError("synthetic retired execution"))
    assert state(recheck[0]) == before
    assert owner.settle_failure(delivery, RuntimeError("synthetic retried negative settlement")) is None
    assert graph.queue.get(delivery.job_id).status == JobStatus.FAILED
    clear(case_for(recheck))


def test_unknown_negative_commit_replays_its_full_terminal_chain_without_current_settings(recheck, monkeypatch):
    from contextlib import contextmanager

    from tests.integration.test_native_intake_source_policy_recheck_postgresql import (
        claimed_recheck,
    )

    _, _, delivery = claimed_recheck(recheck)
    owner, graph = recheck[1], recheck[0][0][0][0]
    retire_settings(monkeypatch)
    original = graph.transaction

    @contextmanager
    def unknown_commit(*args, **kwargs):
        with original(*args, **kwargs):
            yield
        raise RuntimeError("Synthetic response loss after actual negative commit")

    with monkeypatch.context() as patch:
        patch.setattr(graph, "transaction", unknown_commit)
        with pytest.raises(RuntimeError, match="after actual negative commit"):
            owner.settle_failure(delivery, RuntimeError("synthetic retired execution"))
    assert graph.queue.get(delivery.job_id).status == JobStatus.FAILED
    settled = state(recheck[0])
    assert owner.settle_failure(delivery, RuntimeError("synthetic unknown outcome replay")) is None
    assert state(recheck[0]) == settled
    clear(case_for(recheck))


def test_cursor_from_another_tenant_is_rejected_before_any_admission(recheck, monkeypatch):
    import base64

    import modules.listing.application.native_intake_source_policy_recheck_partition as partition

    case = case_for(recheck)
    ids = case[2]
    old = request(recheck)
    assert old.status_code == 202, old.text
    assert withdraw(case, old.json(), kind="source-policy-rechecks").status_code == 200
    fresh = request(recheck, key="synthetic-tenant-cursor-fresh-request")
    assert fresh.status_code == 202, fresh.text
    monkeypatch.setattr(partition, "MAX_SCAN_EVENTS", 1)
    assert list(recheck[1].admit_partition(ids["tenant"], limit=1)) == []
    data = json.loads(base64.urlsafe_b64decode(recheck[1].last_partition_progress["next_cursor"]))
    data["tenant_id"] = str(uuid4())
    foreign = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    before = state(recheck[0])
    with pytest.raises(ValueError, match="PARTITION_CURSOR_REJECTED"):
        list(recheck[1].admit_partition(ids["tenant"], limit=1, cursor=foreign))
    assert state(recheck[0]) == before


def test_actual_cli_factory_emits_durable_admission_and_truthful_progress(recheck, monkeypatch, capsys):
    from apps.worker.oday_worker.native_intake_source_policy_recheck import main

    case = case_for(recheck)
    response = request(recheck)
    assert response.status_code == 202, response.text
    monkeypatch.setattr("apps.api.server.bootstrap_runtime", lambda: recheck[0][0][2])
    monkeypatch.setattr(sys, "argv", ["native-recheck", "--tenant-id", case[2]["tenant"], "--limit", "1", "--include-progress"])
    main()
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    admission = next(line["admission_receipt"] for line in lines if line["phase"] == "native_source_policy_recheck_only")
    progress = next(line["scan_receipt"] for line in lines if line["phase"] == "native_source_policy_recheck_scan")
    assert admission["request_id"] == response.json()["request_id"] and admission["job_status_at_admission"] == "QUEUED"
    assert progress["scanned"] == progress["admitted"] == 1 and progress["conflicts"] == 0 and progress["has_more"] is False
