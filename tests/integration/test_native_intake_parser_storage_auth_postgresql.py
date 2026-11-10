"""Actual SQL401/403 closure; explicitly synthetic fixed-GCS operation seams."""
import copy
import time
from contextlib import contextmanager

import pytest

from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from shared.audit.errors import CanonicalAuditUnavailable
from shared.jobs.queue import JobFenceRejectedError, JobStatus
from tests.integration.test_native_intake_parser_history_postgresql import (
    retire_all_positive_settings,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    accepted_retry,
    rows,
    transient,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    parser_evidence_fields as parser_evidence_fields,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    parser_io_case as parser_io_case,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    parser_realm as parser_realm,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    start_case as start_case,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import (
    assert_failure,
    snapshot,
    withdraw,
)

pytestmark = pytest.mark.requires_live_env
CAUSE = "OBSERVED_GCS_AUTHORIZATION_UNAVAILABLE"
METHODS = {"gcs_bucket": "bucket_info", "gcs_head": "head_version",
    "gcs_download": "download_version_bounded", "parser_child": "parse"}


def deny(io, monkeypatch, *, operation="gcs_bucket", status=403, code="NATIVE_GCS_HTTP_REJECTED", unreaped=False):
    """A typed provider seam; the real SQL owner records every actual call."""
    error = (NativeIOUnreaped if unreaped else NativeIORejected)(code, status=status)
    parse = io.parser.parse
    def denied(*args, **kwargs):
        if operation == "parser_child":
            # Inject the wrong-family error only after the real fixed child
            # has returned. An arbitrary zero-run exception proves no reap.
            parse(*args, **kwargs)
        raise error
    with monkeypatch.context() as patch:
        patch.setattr(io.parser if operation == "parser_child" else io.storage, METHODS[operation], denied)
        with pytest.raises(type(error)) as observed:
            io.read_and_parse()
        assert observed.value is error


def settle(case, leases, delivery):
    return case[0].settle_unavailable(delivery, business_fence=leases.business_fence, leases=leases)


def replace_payload(case, delivery, payload):
    with case[2].connect() as connection:
        assert connection.execute("UPDATE workflow.jobs SET payload=%s::jsonb WHERE job_id=%s",
            (canonical_json(payload), delivery.job_id)).rowcount == 1


@pytest.mark.parametrize("operation,operations", [("gcs_bucket", 1), ("gcs_head", 2), ("gcs_download", 3)])
@pytest.mark.parametrize("status", [401, 403])
def test_actual_current_gcs_auth_failure_closes_only_owned_run_and_jobs_with_live_local_config(
        parser_io_case, monkeypatch, operation, operations, status):
    case, io, leases, delivery = parser_io_case
    initial = rows(case, delivery.job_id)
    deny(io, monkeypatch, operation=operation, status=status)
    previous = snapshot(case)
    assert settle(case, leases, delivery) is None
    receipt = assert_failure(case, delivery, previous, operations=operations, errors=1)
    assert receipt["cause_code"] == CAUSE and receipt["error_class"] == "NativeIORejected"
    job, run, _ = rows(case, delivery.job_id)
    assert job["attempt"] == initial[0]["attempt"] == 1
    assert job["timeout_at"] == initial[0]["timeout_at"] and run["started_at"] == initial[1]["started_at"]
    assert job["payload"]["attempt_intents"][-1]["failure_observation"] == {
        "version": 1, "family": "NativeIORejected", "code": "NATIVE_GCS_HTTP_REJECTED", "status": status}
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    current = snapshot(case)
    assert case[0].replay(delivery) == {"terminal": True, "result": None}
    assert settle(case, leases, delivery) is None and snapshot(case) == current


@pytest.mark.parametrize("status", [400, 404, 408, 429, 500, None])
def test_other_http_failures_do_not_prove_storage_authorization_loss(parser_io_case, monkeypatch, status):
    case, io, leases, delivery = parser_io_case
    deny(io, monkeypatch, status=status)
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_CAPABILITY_STILL_CURRENT$"):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("code", ["NATIVE_GCS_PROVIDER_REJECTED", "NATIVE_HTTP_STATUS_REJECTED", "NATIVE_IO_CONNECTION_FAILED"])
def test_an_auth_status_on_an_unrecognized_observation_code_is_not_a_storage_grant_failure(
        parser_io_case, monkeypatch, code):
    case, io, leases, delivery = parser_io_case
    deny(io, monkeypatch, code=code)
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_CAPABILITY_STILL_CURRENT$"):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


def test_a_synthetic_parser_child_error_cannot_impersonate_gcs_authorization_loss(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    deny(io, monkeypatch, operation="parser_child")
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_CAPABILITY_STILL_CURRENT$"):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


def test_auth_status_with_unknown_reaping_retains_the_admitted_operation_and_quota(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    deny(io, monkeypatch, unreaped=True)
    previous = snapshot(case)
    assert len(previous["slots"]) == 1
    with pytest.raises(NativeParserConflict):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


def test_auth_loss_after_real_retry_authenticates_every_attempt_and_preserves_intake(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    transient(io, monkeypatch)
    case[0].prepare_retry(delivery, leases=leases)
    with accepted_retry(case, delivery.job_id) as (io2, leases2, delivery2):
        deny(io2, monkeypatch, operation="gcs_head", status=401)
        previous = snapshot(case)
        assert settle(case, leases2, delivery2) is None
        receipt = assert_failure(case, delivery2, previous, operations=3, errors=2)
        assert receipt["cause_code"] == CAUSE and receipt["attempt"] == 2
        payload = rows(case, delivery.job_id)[0]["payload"]
        assert receipt["attempt_history_sha256"] == canonical_sha256(payload["attempt_history"])
        retire_all_positive_settings(monkeypatch)
        case[0].artifact_registry = None
        current = snapshot(case)
        assert case[0].replay(delivery2) == {"terminal": True, "result": None}
        assert snapshot(case) == current


def test_gcs_failure_does_not_hide_a_subsequent_local_credential_loss(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    deny(io, monkeypatch)
    withdraw(case, "token", monkeypatch)
    previous = snapshot(case)
    assert settle(case, leases, delivery) is None
    receipt = assert_failure(case, delivery, previous, operations=1, errors=1)
    assert receipt["cause_code"] == "CAPABILITY_UNAVAILABLE"
    assert case[0].replay(delivery) == {"terminal": True, "result": None}


@pytest.mark.parametrize("failure", ["audit", "root_expiry", "physical_version"])
def test_observed_auth_loss_does_not_bypass_audit_original_root_or_current_delivery_version(
        parser_io_case, monkeypatch, failure):
    case, io, leases, delivery = parser_io_case
    owner = case[0]
    deny(io, monkeypatch)
    previous = snapshot(case)
    if failure == "root_expiry":
        original = owner._confirm
        def expired(*args):
            value = original(*args)
            args[-1]._monotonic_deadline = time.monotonic()-1
            return value
        monkeypatch.setattr(owner, "_confirm", expired)
        expected = NativeParserConflict
    else:
        original = owner.graph.audit_sink.persist
        def conflict(event, scope):
            value = original(event, scope)
            if event["action"] == "parser.run_completed.v1":
                if failure == "audit":
                    raise CanonicalAuditUnavailable("Synthetic observed-auth audit unavailable")
                current = owner.queue.get(delivery.job_id)
                owner.queue.heartbeat(current.job_id, expected_version=current.version, fence_token=current.fence_token)
            return value
        monkeypatch.setattr(owner.graph.audit_sink, "persist", conflict)
        expected = CanonicalAuditUnavailable if failure == "audit" else JobFenceRejectedError
    with pytest.raises(expected):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


def test_auth_loss_commit_with_unknown_successful_response_reconciles_the_complete_terminal_proof(
        parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    owner = case[0]
    deny(io, monkeypatch)
    previous = snapshot(case)
    original, injected = owner.graph.transaction, []
    @contextmanager
    def uncertain(tenant):
        with original(tenant):
            yield
        if not injected and owner.queue.get(delivery.job_id).status == JobStatus.FAILED:
            injected.append(True)
            raise OSError("Synthetic response loss after actual observed-auth commit")
    monkeypatch.setattr(owner.graph, "transaction", uncertain)
    assert settle(case, leases, delivery) is None and len(injected) == 1
    assert_failure(case, delivery, previous, operations=1, errors=1)
    current = snapshot(case)
    assert settle(case, leases, delivery) is None and snapshot(case) == current


@pytest.mark.parametrize("tamper", ["status_type", "code", "missing_observation"])
def test_current_auth_labels_must_still_have_the_exact_typed_operation_fact(parser_io_case, monkeypatch, tamper):
    case, io, leases, delivery = parser_io_case
    deny(io, monkeypatch)
    payload = copy.deepcopy(rows(case, delivery.job_id)[0]["payload"])
    last = payload["attempt_intents"][-1]
    if tamper == "status_type":
        last["failure_observation"]["status"] = True
    elif tamper == "code":
        last["failure_observation"]["code"] = "NATIVE_GCS_PROVIDER_REJECTED"
    else:
        del last["failure_observation"]
    replace_payload(case, delivery, payload)
    previous = snapshot(case)
    with pytest.raises((NativeParserConflict, JobFenceRejectedError)):
        settle(case, leases, delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("tamper", ["code", "status", "missing_observation"])
def test_coherently_changed_journal_and_audit_cannot_replace_actual_auth_failure_facts(
        parser_io_case, monkeypatch, tamper):
    case, io, leases, delivery = parser_io_case
    deny(io, monkeypatch)
    assert settle(case, leases, delivery) is None
    payload = copy.deepcopy(rows(case, delivery.job_id)[0]["payload"])
    last = payload["attempt_intents"][-1]
    if tamper == "code":
        last["failure_observation"]["code"] = "NATIVE_GCS_PROVIDER_REJECTED"
    elif tamper == "status":
        last["failure_observation"]["status"] = 500
    else:
        del last["failure_observation"]
    receipt = payload["negative_receipt"]
    receipt["journal_sha256"] = canonical_sha256(payload["attempt_intents"])
    replace_payload(case, delivery, payload)
    with case[2].connect() as connection:
        assert connection.execute("UPDATE audit.audit_events SET after_value=%s::jsonb WHERE audit_event_id=%s",
            (canonical_json({**receipt, "intake_mutated": False}), receipt["audit_event_id"])).rowcount == 1
    previous = snapshot(case)
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    with pytest.raises(NativeParserConflict):
        case[0].replay(delivery)
    assert snapshot(case) == previous


def test_coherently_relabelled_local_token_loss_is_not_an_observed_storage_failure(parser_io_case, monkeypatch):
    case, _, leases, delivery = parser_io_case
    withdraw(case, "token", monkeypatch)
    assert settle(case, leases, delivery) is None
    payload = copy.deepcopy(rows(case, delivery.job_id)[0]["payload"])
    receipt = payload["negative_receipt"]
    assert receipt["error_class"] == "NativeIORejected" and payload["attempt_intents"] == []
    receipt["cause_code"] = CAUSE
    replace_payload(case, delivery, payload)
    with case[2].connect() as connection:
        assert connection.execute("UPDATE audit.audit_events SET after_value=%s::jsonb WHERE audit_event_id=%s",
            (canonical_json({**receipt, "intake_mutated": False}), receipt["audit_event_id"])).rowcount == 1
    previous = snapshot(case)
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    with pytest.raises(NativeParserConflict):
        case[0].replay(delivery)
    assert snapshot(case) == previous
