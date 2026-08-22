"""``external-fetch`` cannot be enqueued at all (XR-CUTOVER-001).

This suite used to pin the tenant binding of the ``external-fetch`` enqueue.
ODP-P10-LIVE-EXTDATA-DIAG-001 had diagnosed a write/read partition split in the
live E2E gate — ``worker:terminal_success`` passed with ``attempts=1`` while
``data:ingestion_runs`` reported ``runs=0`` seconds later — because the probe
was enqueued under the deployment's placeholder tenant while the readback
resolved the smoke principal's own tenant, and ``TenantScopedDocumentStore``
*renames* the collection rather than filtering a shared one. The fix bound the
enqueue tenant to the authenticated principal.

XR-CUTOVER-001 removes the capability instead of binding it: odayplus no longer
fetches external sources, so ``POST /api/v1/jobs`` refuses the job type
outright. The tenant-partition defect is unreachable because there is no write
path left to mis-partition.

What still has to hold is that the refusal is *total*. A caller who is
authenticated, correctly scoped and fully privileged must be refused exactly
like an anonymous one, and no queue entry may be created for any of them —
otherwise the queue accumulates jobs the worker can only fail.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.infrastructure.persistence.factory import _durable_bundle
from tests.integration._authz import FORECASTOPS_HEADERS

PROVIDER_ID = "listing.partner_feed"
# The tenant the operator credential actually carries.
OPERATOR_TENANT = "a11ce505-70bc-56d9-8564-ad22efa23c9e"
# The deployment placeholder the gate used to enqueue under (deploy-dev.yml's
# `|| 'tenant-dev'` default) — the value that caused the partition split.
DEPLOYMENT_TENANT = "tenant-dev"


@pytest.fixture()
def bundle(tmp_path):
    """A durable bundle: only there was the run *physically* partitioned."""
    made = _durable_bundle(str(tmp_path / "extdata-tenant.sqlite3"))
    try:
        yield made
    finally:
        made.engine.close()


def _headers(
    *,
    tenant: str | None = OPERATOR_TENANT,
    roles: str = "data_owner",
    subject: str | None = "live-e2e-gate",
) -> dict[str, str]:
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


@pytest.mark.parametrize(
    ("label", "headers", "payload"),
    [
        ("fully privileged, own tenant", _headers(), {"tenant_id": OPERATOR_TENANT}),
        ("fully privileged, no payload tenant", _headers(), {}),
        (
            "fully privileged, foreign payload tenant",
            _headers(),
            {"tenant_id": DEPLOYMENT_TENANT},
        ),
        ("anonymous", {}, {}),
        ("no integration:create grant", _headers(roles="viewer"), {}),
        ("no tenant scope", _headers(tenant=None), {}),
    ],
)
def test_every_caller_shape_is_refused_and_enqueues_nothing(
    bundle, label: str, headers: dict, payload: dict
) -> None:
    """The refusal does not depend on who is asking, so none of them can queue work."""
    app = create_app(persistence=bundle)
    key = f"cutover-refusal-{label}"

    with TestClient(app) as client:
        response = _enqueue(
            client,
            {"provider_id": PROVIDER_ID, **payload},
            headers=headers,
            key=key,
        )

    assert response.status_code == 410, (label, response.text)
    assert response.json()["error"]["code"] == "external_fetch_decommissioned"
    # Nothing was queued: neither the raw key nor the tenant-scoped form the
    # route used to build resolves to a job.
    assert bundle.job_queue.get_by_idempotency_key(key) is None
    assert bundle.job_queue.count_active_jobs() == 0


def test_a_repeated_refusal_never_becomes_an_idempotent_replay(bundle) -> None:
    """A refused enqueue leaves no idempotency record to replay against."""
    app = create_app(persistence=bundle)

    with TestClient(app) as client:
        first = _enqueue(
            client,
            {"provider_id": PROVIDER_ID},
            headers=_headers(),
            key="cutover-refusal-replay",
        )
        second = _enqueue(
            client,
            {"provider_id": PROVIDER_ID},
            headers=_headers(),
            key="cutover-refusal-replay",
        )

    assert first.status_code == 410
    assert second.status_code == 410
    assert bundle.job_queue.get_by_idempotency_key("cutover-refusal-replay") is None
    assert bundle.job_queue.count_active_jobs() == 0


def test_forecast_enqueue_is_untouched_by_the_refusal(bundle) -> None:
    """The refusal is scoped to one job type, not to POST /jobs."""
    app = create_app(persistence=bundle)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/jobs",
            json={
                "job_type": "forecast",
                "payload": {
                    "tenant_id": FORECASTOPS_HEADERS["x-tenant-id"],
                    "store_id": "store-cutover-001",
                },
            },
            headers={
                **FORECASTOPS_HEADERS,
                "Idempotency-Key": "cutover-forecast-untouched",
            },
        )

    assert response.status_code == 202, response.text
