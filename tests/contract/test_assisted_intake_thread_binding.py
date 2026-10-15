"""Exercise a real API handoff between separate dependency and endpoint threads."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import partial
from threading import get_ident
from types import SimpleNamespace
from uuid import uuid4

import fastapi.dependencies.utils
import fastapi.routing
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.listings import create_assisted_intake_router
from shared.infrastructure.persistence.assisted_listing_intake import (
    _STATE_COLLECTIONS,
    DurableAssistedIntakeStore,
)

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
ACTOR = "00000000-0000-0000-0000-000000000101"
HEADERS = {"x-tenant-id": TENANT, "x-subject-id": ACTOR, "x-roles": "site_reviewer,expansion_user"}


class DocumentDouble:
    """Explicit document fixture; no database or production admission is claimed."""

    engine = SimpleNamespace(dialect="postgresql")

    def __init__(self, rows):
        self.rows = rows

    def list_all(self, collection):
        return deepcopy(self.rows.get(collection, []))

    def get(self, collection, key):
        return None


@pytest.mark.parametrize("warm_other_tenant", [False, True])
def test_promotion_read_binds_verified_tenant_in_endpoint_thread(monkeypatch, warm_other_tenant):
    intake_id, listing_id, promotion_id, audit_id, correlation_id = (str(uuid4()) for _ in range(5))
    intake = {
        "intake_id": intake_id,
        "scope": {"tenant_id": TENANT},
        "submitted_by": ACTOR,
        "stage": "READY",
        "version": 1,
    }
    promotion = {
        "promotion_decision_id": promotion_id,
        "intake_id": intake_id,
        "listing_id": listing_id,
        "tenant_id": TENANT,
        "status": "PENDING_REVIEW",
        "decision_type": "STANDARD",
        "version": 2,
        "audit_event_id": audit_id,
        "correlation_id": correlation_id,
        "proposer_subject_id": ACTOR,
        "target_format_code": "synthetic-thread-control",
    }
    rows = {
        _STATE_COLLECTIONS[name]: [{"_state_key": key, "_tenant_id": TENANT, "value": value}]
        for name, key, value in [
            ("intakes", intake_id, intake),
            ("promotions", promotion_id, promotion),
        ]
    }
    original_rows = deepcopy(rows)
    store = DurableAssistedIntakeStore(DocumentDouble(rows))
    app = FastAPI()
    app.include_router(create_assisted_intake_router(store), prefix="/api/v1")
    thread_ids = {}

    with (
        ThreadPoolExecutor(max_workers=1) as dependency_pool,
        ThreadPoolExecutor(max_workers=1) as endpoint_pool,
    ):

        async def run_dependency(func, *args, **kwargs):
            def invoke():
                thread_ids["dependency"] = get_ident()
                return func(*args, **kwargs)

            return await asyncio.get_running_loop().run_in_executor(dependency_pool, invoke)

        async def run_endpoint(func, *args, **kwargs):
            def invoke():
                thread_ids["endpoint"] = get_ident()
                return func(*args, **kwargs)

            return await asyncio.get_running_loop().run_in_executor(endpoint_pool, invoke)

        monkeypatch.setattr(fastapi.dependencies.utils, "run_in_threadpool", run_dependency)
        monkeypatch.setattr(fastapi.routing, "run_in_threadpool", run_endpoint)
        if warm_other_tenant:
            endpoint_pool.submit(partial(store.refresh, OTHER_TENANT)).result()
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/intakes/{intake_id}/promotion-decision", headers=HEADERS
            )
            assert response.status_code == 200, response.text
            assert response.json()["promotion_decision_id"] == promotion_id
            assert response.headers["etag"] == 'W/"2"'
            denied = client.get(
                f"/api/v1/intakes/{intake_id}/promotion-decision",
                headers={**HEADERS, "x-tenant-id": OTHER_TENANT},
            )
            assert denied.status_code == 404, denied.text
            restored = client.get(
                f"/api/v1/intakes/{intake_id}/promotion-decision", headers=HEADERS
            )
            assert restored.status_code == 200, restored.text
    assert thread_ids["dependency"] != thread_ids["endpoint"]
    assert rows == original_rows
