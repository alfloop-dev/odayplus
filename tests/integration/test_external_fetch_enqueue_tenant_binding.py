"""The ``external-fetch`` enqueue tenant is bound to the authenticated
principal (ODP-P10-LIVE-EXTDATA-REMEDIATE-001).

ODP-P10-LIVE-EXTDATA-DIAG-001 diagnosed a write/read tenant-partition split in
the live E2E gate: ``worker:terminal_success`` passed with ``attempts=1`` while
``data:ingestion_runs`` reported ``runs=0`` in the *same* gate execution,
seconds apart. Nothing was broken on either side -- the probe was enqueued
under the deployment's placeholder tenant, ``POST /api/v1/jobs`` passed that
value through verbatim for ``external-fetch``, and the readback resolved its
tenant from the smoke principal's ``scope.tenant_id``. Because
``TenantScopedDocumentStore`` *renames* the collection rather than filtering a
shared one, the two sides addressed disjoint partitions and the successful runs
were unreadable.

These tests pin the fix and the defect it replaces:

1. the defect itself is reproduced deterministically on a durable bundle --
   worker success with no API-readable run -- so the partition mechanism, not
   just the patched route, stays covered;
2. an enqueue that omits the tenant lands in the *caller's own* partition, so
   the worker's write and the API's readback share one durable run;
3. a payload tenant that disagrees with the principal is refused with
   ``TENANT_SCOPE_MISMATCH`` instead of silently writing out of scope;
4. anonymous, under-privileged, and tenant-less callers fail closed;
5. idempotent replay stays per tenant, so the gate's ``worker:idempotent_replay``
   probe still sees ``sameJob=True created=False`` while two tenants sharing one
   key are never collapsed into one job.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.worker.oday_worker.main import ODayWorker
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.jobs.queue import JobRequest, JobStatus

PROVIDER_ID = "listing.partner_feed"
# The tenant the operator credential actually carries.
OPERATOR_TENANT = "a11ce505-70bc-56d9-8564-ad22efa23c9e"
# The deployment placeholder the gate used to enqueue under (deploy-dev.yml's
# `|| 'tenant-dev'` default).
DEPLOYMENT_TENANT = "tenant-dev"

# `integration:create` enqueues, `integration:view` reads back; DATA_OWNER holds
# both, and it is the role the live smoke principal was granted in
# ODP-OPERATOR-SMOKE-RBAC-LIVE-002.
OPERATOR_HEADERS = {
    "x-subject-id": "live-e2e-gate",
    "x-roles": "data_owner",
    "x-tenant-id": OPERATOR_TENANT,
}


@pytest.fixture()
def bundle(tmp_path):
    """A durable bundle: only there is the run *physically* partitioned."""
    made = _durable_bundle(str(tmp_path / "extdata-tenant.sqlite3"))
    try:
        yield made
    finally:
        made.engine.close()


def _headers(*, tenant: str | None = OPERATOR_TENANT, roles: str = "data_owner",
             subject: str | None = "live-e2e-gate") -> dict[str, str]:
    headers = {"x-roles": roles}
    if subject is not None:
        headers["x-subject-id"] = subject
    if tenant is not None:
        headers["x-tenant-id"] = tenant
    return headers


def _enqueue(client: TestClient, payload: dict, *, headers: dict, key: str | None = None):
    request_headers = dict(headers)
    if key is not None:
        request_headers["Idempotency-Key"] = key
    return client.post(
        "/api/v1/jobs",
        json={"job_type": "external-fetch", "payload": payload},
        headers=request_headers,
    )


def _readback(client: TestClient, *, headers: dict = OPERATOR_HEADERS):
    response = client.get("/api/v1/external-data/ingestion-runs?limit=100", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


# -- 1. the diagnosed defect, reproduced ------------------------------------


def test_foreign_enqueue_tenant_reproduces_worker_success_with_no_api_readable_run(
    bundle,
) -> None:
    """The T10 contradiction, deterministically: succeeded job, ``runs=0``.

    The queue is loaded directly with the payload the pre-fix route would have
    produced -- the caller's tenant, passed through untouched -- so this pins
    the partition mechanism rather than the route that used to allow it.
    """
    job, _ = bundle.job_queue.enqueue(
        JobRequest(
            job_type="external-fetch",
            payload={
                "tenant_id": DEPLOYMENT_TENANT,
                "provider_id": PROVIDER_ID,
                "schedule_id": "live-e2e-gate",
            },
        ),
        correlation_id="corr-live-e2e-split",
    )

    assert ODayWorker(persistence=bundle).run_once() is True

    # Write side: the worker is healthy and says so, on the first attempt.
    executed = bundle.job_queue.get(job.job_id)
    assert executed.status == JobStatus.SUCCEEDED
    written = bundle.ingestion_run_store_for_tenant(DEPLOYMENT_TENANT).list_runs()
    assert [record.tenant_id for record in written] == [DEPLOYMENT_TENANT]

    # Read side: the operator credential sees nothing at all -- not a filtered
    # view of someone else's run, an empty partition.
    client = TestClient(create_app(persistence=bundle))
    assert _readback(client) == []


# -- 2. the fix: one durable run, shared -------------------------------------


def test_enqueue_without_a_tenant_writes_where_the_same_credential_reads(bundle) -> None:
    """Worker write and API readback resolve to one durable run."""
    client = TestClient(create_app(persistence=bundle))

    accepted = _enqueue(
        client,
        {"provider_id": PROVIDER_ID, "schedule_id": "live-e2e-gate"},
        headers=OPERATOR_HEADERS,
    )
    assert accepted.status_code == 202, accepted.text
    # The route supplied the tenant the caller never sent.
    job_id = accepted.json()["job_id"]
    assert bundle.job_queue.get(job_id).payload["tenant_id"] == OPERATOR_TENANT

    assert ODayWorker(persistence=bundle).run_once() is True
    assert bundle.job_queue.get(job_id).status == JobStatus.SUCCEEDED

    runs = _readback(client)
    assert [run["provider_id"] for run in runs] == [PROVIDER_ID]
    assert runs[0]["schedule_id"] == "live-e2e-gate"
    # Same run, not a second copy: the worker's partition is the reader's.
    assert len(bundle.ingestion_run_store_for_tenant(OPERATOR_TENANT).list_runs()) == 1
    assert bundle.ingestion_run_store_for_tenant(DEPLOYMENT_TENANT).list_runs() == []


def test_matching_tenant_on_the_payload_is_accepted(bundle) -> None:
    """An explicit, correct tenant is still allowed -- it is redundant, not wrong."""
    client = TestClient(create_app(persistence=bundle))

    accepted = _enqueue(
        client,
        {
            "tenant_id": OPERATOR_TENANT,
            "provider_id": PROVIDER_ID,
            "schedule_id": "live-e2e-gate",
        },
        headers=OPERATOR_HEADERS,
    )

    assert accepted.status_code == 202, accepted.text
    assert (
        bundle.job_queue.get(accepted.json()["job_id"]).payload["tenant_id"]
        == OPERATOR_TENANT
    )


# -- 3. a foreign tenant is refused, not honoured ----------------------------


def test_foreign_tenant_on_the_payload_is_refused_and_enqueues_nothing(bundle) -> None:
    """The exact request the gate used to send is now a loud 403."""
    client = TestClient(create_app(persistence=bundle))

    refused = _enqueue(
        client,
        {
            "tenant_id": DEPLOYMENT_TENANT,
            "provider_id": PROVIDER_ID,
            "schedule_id": "live-e2e-gate",
        },
        headers=OPERATOR_HEADERS,
    )

    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "TENANT_SCOPE_MISMATCH"
    # Fail-closed: nothing reached the queue, so the worker has nothing to drain
    # and neither partition gained a run.
    assert ODayWorker(persistence=bundle).run_once() is False
    assert bundle.ingestion_run_store_for_tenant(DEPLOYMENT_TENANT).list_runs() == []
    assert _readback(client) == []


# -- 4. every missing-credential path fails closed ---------------------------


def test_anonymous_enqueue_is_unauthenticated(bundle) -> None:
    client = TestClient(create_app(persistence=bundle))

    denied = _enqueue(
        client,
        {"provider_id": PROVIDER_ID},
        headers=_headers(subject=None, tenant=None),
    )

    assert denied.status_code == 401
    assert denied.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"


def test_principal_without_the_integration_create_grant_is_forbidden(bundle) -> None:
    """`integration:view` alone reads runs; it must not create them."""
    client = TestClient(create_app(persistence=bundle))

    denied = _enqueue(client, {"provider_id": PROVIDER_ID}, headers=_headers(roles="auditor"))

    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "EXTERNAL_FETCH_CREATE_FORBIDDEN"


def test_principal_without_a_tenant_scope_is_forbidden(bundle) -> None:
    """No tenant claim means no partition to write to -- refuse, never guess."""
    client = TestClient(create_app(persistence=bundle))

    denied = _enqueue(client, {"provider_id": PROVIDER_ID}, headers=_headers(tenant=None))

    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "TENANT_SCOPE_REQUIRED"


# -- 5. idempotency stays per tenant ----------------------------------------


def test_idempotent_replay_returns_the_same_job_for_one_tenant(bundle) -> None:
    """The gate's ``worker:idempotent_replay`` probe: sameJob=True created=False."""
    client = TestClient(create_app(persistence=bundle))
    payload = {"provider_id": PROVIDER_ID, "schedule_id": "live-e2e-gate"}

    first = _enqueue(client, payload, headers=OPERATOR_HEADERS, key="gate-probe-1")
    replay = _enqueue(client, payload, headers=OPERATOR_HEADERS, key="gate-probe-1")

    assert first.status_code == 202 and replay.status_code == 202
    assert first.json()["created"] is True
    assert replay.json()["created"] is False
    assert replay.json()["job_id"] == first.json()["job_id"]
    assert replay.json()["idempotency_key"] == "gate-probe-1"


def test_two_tenants_sharing_one_idempotency_key_are_not_collapsed(bundle) -> None:
    """Tenant B's probe must not be answered with tenant A's job."""
    client = TestClient(create_app(persistence=bundle))
    payload = {"provider_id": PROVIDER_ID, "schedule_id": "live-e2e-gate"}

    first = _enqueue(client, payload, headers=OPERATOR_HEADERS, key="shared-key")
    other = _enqueue(
        client, payload, headers=_headers(tenant="tenant-other"), key="shared-key"
    )

    assert first.status_code == 202 and other.status_code == 202
    assert other.json()["created"] is True
    assert other.json()["job_id"] != first.json()["job_id"]
    assert bundle.job_queue.get(other.json()["job_id"]).payload["tenant_id"] == "tenant-other"


# -- 6. the gate no longer guesses a tenant ----------------------------------


def _gate_config(**overrides):
    from scripts.e2e import check_live_e2e_gate as gate

    return gate.GateConfig(
        api_url="https://api.example.invalid",
        expected_sha="0" * 40,
        bearer_token="unused",
        operator_role="data_owner",
        worker_probe_provider_id=PROVIDER_ID,
        **overrides,
    )


def test_gate_enqueue_body_omits_the_tenant_instead_of_guessing(monkeypatch) -> None:
    """Item 4 of the T11 handoff: no environment fallback chain.

    With the deployment variables set to a tenant the operator credential
    cannot read, the probe body must carry no tenant at all -- the API binds it
    -- rather than reintroducing the split through the environment.
    """
    from scripts.e2e import check_live_e2e_gate as gate

    monkeypatch.setenv("ODP_SCHEDULED_INGESTION_TENANT_ID", DEPLOYMENT_TENANT)
    monkeypatch.setenv("ODP_TENANT_ID", DEPLOYMENT_TENANT)

    body = gate._enqueue_body(_gate_config(), "gate-probe-1")

    assert "tenant_id" not in body["payload"]
    assert body["payload"]["provider_id"] == PROVIDER_ID
    assert body["job_type"] == gate.WORKER_PROBE_JOB_TYPE


def test_gate_still_sends_an_explicit_operator_tenant(monkeypatch) -> None:
    """An explicit ``--operator-tenant`` is asserted, not silently dropped."""
    from scripts.e2e import check_live_e2e_gate as gate

    monkeypatch.setenv("ODP_SCHEDULED_INGESTION_TENANT_ID", DEPLOYMENT_TENANT)

    body = gate._enqueue_body(_gate_config(operator_tenant=OPERATOR_TENANT), "gate-probe-1")

    assert body["payload"]["tenant_id"] == OPERATOR_TENANT
