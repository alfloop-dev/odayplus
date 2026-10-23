"""Synthetic actors exercise real native jobs, transport fences and audit SQL."""

from dataclasses import replace
from uuid import uuid4

import pytest

from modules.listing.application.identity_delivery import (
    IDENTITY_POLICY_ENV,
    IDENTITY_SIGNING_KEY_ENV,
    IdentityJobAdmission,
)
from modules.listing.application.identity_execution import IdentityExecutionService
from shared.audit.errors import CanonicalAuditUnavailable
from shared.auth import Principal, Role, Scope
from shared.infrastructure.persistence.identity_graph_sql import CanonicalIdentityGraphRepository
from shared.jobs.queue import JobFenceRejectedError, JobStatus
from tests.integration.test_assisted_listing_identity import canonical_graph as canonical_graph
from tests.integration.test_canonical_identity_admission_postgresql import _claim
from tests.integration.test_canonical_identity_http_postgresql import (
    _headers,
    _proposal,
    _review_request,
)
from tests.integration.test_canonical_identity_http_postgresql import (
    identity_api as identity_api,
)
from tests.integration.test_canonical_identity_proposals_postgresql import GRAPH_TABLES, _state

pytestmark = pytest.mark.requires_live_env


def _approved(case, api):
    proposal, _, _, _ = _proposal(case, api)
    client, path, body, headers = _review_request(case, api, proposal)
    result = client.post(path, json=body, headers=headers)
    assert result.status_code == 200, result.text
    return result.json(), path.removesuffix("/actions/review")


def _cancel(client, path, version=1, **kwargs):
    return client.post(path + "/actions/cancel-job", json={"reason": "Stop this synthetic identity operation"},
        headers=_headers(version, "synthetic-native-job-cancel-001"), **kwargs)


def _graph_state(case):
    return {k: v for k, v in _state(case).items() if k in GRAPH_TABLES - {"workflow.jobs"}}


@pytest.mark.parametrize("claimed", [False, True])
def test_cancel_unaccepted_native_job_is_atomic_durable_and_never_executes(canonical_graph, identity_api, claimed):
    graph, ids, _, _ = canonical_graph
    client, caller, _ = identity_api
    receipt, path = _approved(canonical_graph, identity_api)
    delivery = registry = bundle = None
    if claimed:
        delivery, registry, bundle = _claim(canonical_graph, receipt["job_id"])
    # Own staff can stop work that has not accepted an identity decision.
    caller["principal"] = Principal(ids["actor"], frozenset({Role.EXPANSION_USER}), Scope(tenant_id=ids["tenant"]))
    before = _graph_state(canonical_graph)
    selected = client.get(path + "/job")
    assert selected.status_code == 200 and selected.headers["etag"] == 'W/"1"'
    assert set(selected.json()) == {"job_id", "decision_id", "match_case_id", "intake_id", "version", "status",
        "checkpoint", "attempt", "max_attempts", "timeout_at", "cancellation_requested_at", "correlation_id"}
    stopped = _cancel(client, path)
    assert stopped.status_code == 200, stopped.text
    assert stopped.headers["etag"] == 'W/"2"'
    assert stopped.json()["status"] == ("QUEUED" if claimed else "CANCELLED")
    assert stopped.json()["cancellation_requested_at"] and stopped.json()["audit_event_id"]
    assert stopped.json()["attempt"] == 0
    if claimed:
        registry.handle(delivery, bundle)
    assert graph.queue.get(receipt["job_id"]).status == JobStatus.CANCELLED
    assert _graph_state(canonical_graph) == before
    selected = client.get(path + "/job")
    assert selected.json()["status"] == "CANCELLED"
    assert "attempt_token" not in selected.text and "policy" not in selected.text
    replay_before = _state(canonical_graph)
    replay = _cancel(client, path)
    assert replay.status_code == 200 and replay.json() == stopped.json()
    assert replay.headers["idempotency-replayed"] == "true"
    assert _state(canonical_graph) == replay_before
    caller["principal"] = replace(caller["principal"], subject_id=str(uuid4()))
    assert _cancel(client, path).status_code == 403
    assert _state(canonical_graph) == replay_before


def test_cancel_after_acceptance_is_acknowledged_by_current_worker_and_preserves_graph(canonical_graph, identity_api, monkeypatch):
    graph, ids, _, _ = canonical_graph
    client, caller, _ = identity_api
    receipt, path = _approved(canonical_graph, identity_api)
    before = _graph_state(canonical_graph)
    delivery, registry, bundle = _claim(canonical_graph, receipt["job_id"])
    execute = CanonicalIdentityGraphRepository.execute

    def cancel_at_checkpoint(self, tenant, decision_id, current, *, business_fence):
        # begin() has committed EXECUTING. Cancellation uses a separate HTTP
        # transaction before graph execution, not a direct fixture flag write.
        staff = Principal(ids["actor"], frozenset({Role.EXPANSION_USER}), Scope(tenant_id=ids["tenant"]))
        manager = caller["principal"]
        caller["principal"] = staff
        assert _cancel(client, path, version=2).status_code == 403
        caller["principal"] = manager
        stopped = _cancel(client, path, version=2)
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["status"] == "RUNNING" and stopped.json()["cancellation_requested_at"]
        return execute(self, tenant, decision_id, current, business_fence=business_fence)

    monkeypatch.setattr(CanonicalIdentityGraphRepository, "execute", cancel_at_checkpoint)
    registry.handle(delivery, bundle)
    assert graph.queue.get(delivery.job_id).status == JobStatus.CANCELLED
    selected = client.get(path)
    assert selected.json()["status"] == "FAILED" and selected.json()["job_status"] == "CANCELLED"
    assert client.get(path + "/job").json()["status"] == "CANCELLED"
    assert _graph_state(canonical_graph) == before
    with graph.transaction(ids["tenant"]):
        decision = graph._one("SELECT * FROM identity.match_decisions WHERE match_decision_id=?", (receipt["decision_id"],))
    assert decision["after_graph"]["execution_failure"]["reason_code"] == "IDENTITY_CANCELLATION_REQUESTED"
    with pytest.raises(JobFenceRejectedError):
        execute(bundle.canonical_identity_graph(), ids["tenant"], receipt["decision_id"], delivery, business_fence=2)
    assert _graph_state(canonical_graph) == before


@pytest.mark.parametrize("expired", ["transport", "business", "deadline", "replaced_worker"])
def test_cancellation_never_gives_stale_worker_failure_settlement_authority(canonical_graph, identity_api, expired):
    graph, ids, _, db = canonical_graph
    client, _, _ = identity_api
    receipt, path = _approved(canonical_graph, identity_api)
    delivery, _, bundle = _claim(canonical_graph, receipt["job_id"])
    runtime = bundle.canonical_identity_graph()
    with runtime.engine.transaction():
        reference = runtime.delivery_reference(delivery)
    service = IdentityExecutionService(runtime)
    fence = service.begin(ids["tenant"], reference["decision_id"], delivery)
    assert _cancel(client, path, version=2).status_code == 200
    with db.connect() as conn:
        if expired == "transport":
            conn.execute("UPDATE odp_runtime.durable_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (delivery.job_id,))
        elif expired == "business":
            conn.execute("UPDATE workflow.jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (delivery.job_id,))
        elif expired == "deadline":
            conn.execute("UPDATE workflow.jobs SET timeout_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (delivery.job_id,))
        else:
            conn.execute("UPDATE odp_runtime.durable_jobs SET locked_by='replacement-worker',fence_token=fence_token+1 WHERE job_id=%s", (delivery.job_id,))
    before = _state(canonical_graph)
    with pytest.raises(JobFenceRejectedError):
        service.fail(ids["tenant"], reference["decision_id"], delivery, business_fence=fence)
    assert _state(canonical_graph) == before
    assert client.get(path + "/job").json()["status"] == "RUNNING"


def test_cancellation_durable_audit_failure_rolls_back_native_transport_and_receipt(canonical_graph, identity_api, monkeypatch):
    graph, _, _, _ = canonical_graph
    client, _, _ = identity_api
    _, path = _approved(canonical_graph, identity_api)
    persist = graph.audit_sink.persist

    def unavailable(audit, scope):
        if audit["action"] == "identity.cancellation_requested.v1":
            raise CanonicalAuditUnavailable("Synthetic cancellation audit failure")
        return persist(audit, scope)

    monkeypatch.setattr(graph.audit_sink, "persist", unavailable)
    before = _state(canonical_graph)
    assert _cancel(client, path).status_code == 503
    assert _state(canonical_graph) == before
    monkeypatch.setattr(graph.audit_sink, "persist", persist)
    assert _cancel(client, path).status_code == 200


def test_protective_cancellation_requires_no_new_execution_policy_or_signing_key(canonical_graph, identity_api, monkeypatch):
    client, _, _ = identity_api
    _, path = _approved(canonical_graph, identity_api)
    monkeypatch.delenv(IDENTITY_POLICY_ENV)
    monkeypatch.delenv(IDENTITY_SIGNING_KEY_ENV)
    assert _cancel(client, path).status_code == 200


def test_own_staff_can_cancel_a_real_preaccept_retry_without_reapproving(canonical_graph, identity_api):
    graph, ids, _, _ = canonical_graph
    client, caller, _ = identity_api
    receipt, path = _approved(canonical_graph, identity_api)
    delivery, _, bundle = _claim(canonical_graph, receipt["job_id"])

    class RetryableStorageFailure(RuntimeError):
        sqlstate = "40001"

    IdentityJobAdmission(bundle.canonical_identity_graph()).settle_delivery(delivery, error=RetryableStorageFailure())
    selected = client.get(path + "/job").json()
    assert selected["status"] == "RETRYING" and selected["attempt"] == 1
    caller["principal"] = Principal(ids["actor"], frozenset({Role.EXPANSION_USER}), Scope(tenant_id=ids["tenant"]))
    stopped = _cancel(client, path, version=selected["version"])
    assert stopped.status_code == 200 and stopped.json()["status"] == "CANCELLED"
    assert graph.queue.get(receipt["job_id"]).status == JobStatus.CANCELLED


def test_cancel_idempotency_binds_body_and_job_version(canonical_graph, identity_api):
    client, _, _ = identity_api
    _, path = _approved(canonical_graph, identity_api)
    assert _cancel(client, path).status_code == 200
    before = _state(canonical_graph)
    assert _cancel(client, path, version=2).status_code == 409
    assert client.post(path + "/actions/cancel-job", json={"reason": "Changed cancellation intent"},
        headers=_headers(1, "synthetic-native-job-cancel-001")).status_code == 409
    assert _state(canonical_graph) == before


@pytest.mark.parametrize("corruption", ["tenant_id", "job_id", "attempt_token"])
def test_native_job_controls_refuse_foreign_or_replaced_delivery_binding(canonical_graph, identity_api, corruption):
    import json

    _, _, _, db = canonical_graph
    client, _, _ = identity_api
    receipt, path = _approved(canonical_graph, identity_api)
    with db.connect() as conn:
        value = "replaced-token" if corruption == "attempt_token" else str(uuid4())
        conn.execute("UPDATE odp_runtime.durable_jobs SET payload_json=jsonb_set(payload_json::jsonb, %s, %s::jsonb) "
            "WHERE job_id=%s", ([corruption], json.dumps(value), receipt["job_id"]))
    before = _state(canonical_graph)
    assert client.get(path + "/job").status_code == 409
    assert _cancel(client, path).status_code == 409
    assert _state(canonical_graph) == before


def test_cancel_version_key_reason_and_completed_job_conflicts_preserve_state(canonical_graph, identity_api):
    graph, _, _, _ = canonical_graph
    client, _, _ = identity_api
    receipt, path = _approved(canonical_graph, identity_api)
    before = _state(canonical_graph)
    assert _cancel(client, path, version=9).status_code == 409
    assert client.post(path + "/actions/cancel-job", json={"reason": "   "},
        headers=_headers(1, "synthetic-native-job-cancel-001")).status_code == 422
    assert _state(canonical_graph) == before
    delivery, registry, bundle = _claim(canonical_graph, receipt["job_id"])
    registry.handle(delivery, bundle)
    before = _state(canonical_graph)
    selected = client.get(path + "/job").json()
    assert selected["status"] == "SUCCEEDED"
    assert _cancel(client, path, version=selected["version"]).status_code == 409
    assert _state(canonical_graph) == before


@pytest.mark.parametrize("change", ["foreign_tenant", "outside_scope", "governance", "platform_admin", "anonymous"])
def test_native_job_read_and_cancel_enforce_current_principal(canonical_graph, identity_api, change):
    _, _, _, _ = canonical_graph
    client, caller, _ = identity_api
    _, path = _approved(canonical_graph, identity_api)
    current = caller["principal"]
    if change == "foreign_tenant":
        current = replace(current, scope=Scope(tenant_id=str(uuid4())))
    elif change == "outside_scope":
        current = replace(current, scope=Scope(tenant_id=current.tenant_id, brand_ids=frozenset({"unrelated-brand"})))
    elif change == "governance":
        current = replace(current, roles=frozenset({Role.AUDITOR}))
    elif change == "platform_admin":
        current = replace(current, roles=frozenset({Role.PLATFORM_ADMIN}))
    else:
        current = replace(current, authenticated=False)
    caller["principal"] = current
    before = _state(canonical_graph)
    expected = 401 if change == "anonymous" else 404 if change == "foreign_tenant" else 403
    assert _cancel(client, path).status_code == expected
    if change != "governance":
        assert client.get(path + "/job").status_code == expected
    assert _state(canonical_graph) == before
