"""Real PostgreSQL receipt matching; isolated document-owner protocol only.

No claim of production source admission, model readiness or HTTP acceptance.
The runtime document migration is real; unrelated canonical business relations
are outside these storage tests, so full application boot is tested elsewhere.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.worker.oday_worker.handlers import build_default_registry
from shared.infrastructure.persistence.assisted_listing_intake import (
    AssistedIntakePersistenceConflict,
    DurableAssistedIntakeStore,
)
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.listing_search_delivery import (
    JOB_TYPE,
    ListingSearchDelivery,
)
from shared.infrastructure.persistence.listing_search_matches import (
    ListingSearchMatchConflict,
    ListingSearchMatchStore,
)
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.postgresql import PostgresDocumentStore, PostgresEngine
from shared.jobs.queue import JobRequest, JobStatus, NonRetryableJobError
from tests.unit.test_saved_listing_search_criteria import query, receipt

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def owned_case(intake_blank_db):
    url = intake_blank_db.url()
    engines = []

    def open_store():
        engine = PostgresEngine(url, validate_schema=False, min_pool_size=1, max_pool_size=2)
        engines.append(engine)
        return DurableAssistedIntakeStore(PostgresDocumentStore(engine))

    case = {
        "open": open_store,
        "tenant": str(uuid4()),
        "zone": str(uuid4()),
        "owner": str(uuid4()),
        "viewId": str(uuid4()),
        "intakeId": str(uuid4()),
    }
    try:
        yield case
    finally:
        for engine in engines:
            engine.close()


def search(case):
    return {
        "saved_view_id": case["viewId"],
        "tenant_id": case["tenant"],
        "owner_subject_id": case["owner"],
        "version": 1,
        "visibility": "PRIVATE",
        "resource": "intake",
        "name": "Synthetic receipt criteria",
        "query": query(heatZoneId=case["zone"]),
    }


def intake(case):
    return receipt(
        intake_id=case["intakeId"],
        version=1,
        scope={"tenant_id": case["tenant"], "heat_zone_id": case["zone"]},
    )


def drain(store):
    queue = DurableJobQueue(store._engine)
    registry = build_default_registry()
    persistence = SimpleNamespace(engine=store._engine, job_queue=queue)
    count = 0
    while job := queue.claim_next(worker_id="synthetic-listing-search-worker"):
        assert job.job_type == JOB_TYPE
        registry.handle(job, persistence)
        queue.update_status(
            job.job_id,
            JobStatus.SUCCEEDED,
            expected_version=job.version,
            fence_token=job.fence_token,
        )
        count += 1
    return count


def history(store, case):
    return ListingSearchMatchStore(TenantScopedDocumentStore(store._store, case["tenant"])).history(
        search_id=case["viewId"], search_version=1
    )


def test_search_first_then_new_receipt_matches_durably_on_another_connection(owned_case):
    c = owned_case
    store = c["open"]()
    store.refresh(c["tenant"])
    store.saved_views.append(search(c))
    store.flush()
    original = intake(c)
    store.intakes[c["intakeId"]] = deepcopy(original)
    store.flush()
    drain(store)
    restart = c["open"]()
    restart.refresh(c["tenant"])
    rows = history(restart, c)
    assert len(rows) == 1 and rows[0]["outcome"] == "MATCH"
    assert rows[0]["ownerSubjectId"] == c["owner"] and rows[0]["intakeVersion"] == 1
    assert restart.intakes[c["intakeId"]] == original


def test_new_search_matches_receipts_that_already_committed(owned_case):
    c = owned_case
    store = c["open"]()
    store.refresh(c["tenant"])
    store.intakes[c["intakeId"]] = intake(c)
    store.flush()
    new = c["open"]()
    new.refresh(c["tenant"])
    new.saved_views.append(search(c))
    new.flush()
    drain(new)
    assert [r["outcome"] for r in history(new, c)] == ["MATCH"]


def test_corrected_receipt_has_new_disposition_and_keeps_original_match(owned_case):
    c = owned_case
    store = c["open"]()
    store.refresh(c["tenant"])
    store.saved_views.append(search(c))
    store.intakes[c["intakeId"]] = intake(c)
    store.flush()
    store.intakes[c["intakeId"]]["version"] = 2
    store.intakes[c["intakeId"]]["fields"][1]["effective"] = 70000
    store.flush()
    drain(store)
    assert sorted((r["intakeVersion"], r["outcome"]) for r in history(store, c)) == [
        (1, "MATCH"),
        (2, "NO_MATCH"),
    ]


def test_existing_unrelated_saved_view_query_is_unchanged(owned_case):
    c = owned_case
    store = c["open"]()
    store.refresh(c["tenant"])
    view = {**search(c), "query": {"stage": "READY"}}
    store.saved_views.append(deepcopy(view))
    store.intakes[c["intakeId"]] = intake(c)
    store.flush()
    drain(store)
    assert history(store, c) == []
    new = c["open"]()
    new.refresh(c["tenant"])
    assert new.saved_views == [view]


def test_matching_failure_rolls_back_receipt_and_private_match_together(owned_case, monkeypatch):
    c = owned_case
    store = c["open"]()
    store.refresh(c["tenant"])
    store.saved_views.append(search(c))
    store.flush()
    real = store._stage_listing_search_changes

    def fail_after_actual_match(*args):
        real(*args)
        raise RuntimeError("Synthetic transaction failure")

    monkeypatch.setattr(store, "_stage_listing_search_changes", fail_after_actual_match)
    store.intakes[c["intakeId"]] = intake(c)
    with pytest.raises(RuntimeError, match="Synthetic transaction failure"):
        store.flush()
    new = c["open"]()
    new.refresh(c["tenant"])
    assert c["intakeId"] not in new.intakes and history(new, c) == []


def test_two_stale_replicas_creating_search_and_receipt_cannot_miss_the_pair(owned_case):
    c = owned_case
    stores = [c["open"](), c["open"]()]
    barrier = Barrier(2)

    def write(index):
        store = stores[index]
        store.refresh(c["tenant"])
        if index == 0:
            store.saved_views.append(search(c))
        else:
            store.intakes[c["intakeId"]] = intake(c)
        barrier.wait(timeout=10)
        store.flush()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, i) for i in range(2)]
        for future in futures:
            future.result(timeout=15)
    new = c["open"]()
    new.refresh(c["tenant"])
    drain(new)
    assert len(history(new, c)) == 1 and history(new, c)[0]["outcome"] == "MATCH"


def test_changed_facts_without_new_receipt_version_are_refused_atomically(owned_case):
    c = owned_case
    store = c["open"]()
    store.refresh(c["tenant"])
    store.saved_views.append(search(c))
    store.intakes[c["intakeId"]] = intake(c)
    store.flush()
    store.intakes[c["intakeId"]]["fields"][1]["effective"] = 70000
    with pytest.raises(ListingSearchMatchConflict):
        store.flush()
    new = c["open"]()
    new.refresh(c["tenant"])
    drain(new)
    assert new.intakes[c["intakeId"]]["fields"][1]["effective"] == 50000
    drain(new)
    assert len(history(new, c)) == 1 and history(new, c)[0]["outcome"] == "MATCH"


def test_tenant_partition_preserves_private_results_even_with_same_ids(owned_case):
    c = owned_case
    store = c["open"]()
    store.refresh(c["tenant"])
    store.saved_views.append(search(c))
    store.intakes[c["intakeId"]] = intake(c)
    store.flush()
    other = {**c, "tenant": str(uuid4()), "owner": str(uuid4())}
    store.refresh(other["tenant"])
    assert store.saved_views == [] and history(store, other) == []
    store.saved_views.append(search(other))
    store.intakes[other["intakeId"]] = intake(other)
    store.intakes[other["intakeId"]]["fields"][1]["effective"] = 70000
    store.flush()
    drain(store)
    assert history(store, other)[0]["outcome"] == "NO_MATCH"
    store.refresh(c["tenant"])
    assert history(store, c)[0]["outcome"] == "MATCH"


@pytest.mark.parametrize("initial,changed", [(50000, 55000), (None, 0)])
def test_same_outcome_cannot_hide_facts_changes_without_new_version(owned_case, initial, changed):
    c = owned_case
    store = c["open"]()
    store.refresh(c["tenant"])
    store.saved_views.append(search(c))
    record = intake(c)
    record["fields"][1]["effective"] = initial
    store.intakes[c["intakeId"]] = record
    store.flush()
    drain(store)
    original = history(store, c)[0]
    store.intakes[c["intakeId"]]["fields"][1]["effective"] = changed
    with pytest.raises(ListingSearchMatchConflict, match="REVISION_FACTS_CHANGED"):
        store.flush()
    new = c["open"]()
    new.refresh(c["tenant"])
    assert new.intakes[c["intakeId"]]["fields"][1]["effective"] == initial
    assert history(new, c) == [original]


def test_commit_events_keep_original_revision_if_worker_runs_after_correction(owned_case):
    c = owned_case
    store = c["open"]()
    store.refresh(c["tenant"])
    store.saved_views.append(search(c))
    store.intakes[c["intakeId"]] = intake(c)
    store.flush()
    assert history(store, c) == []
    store.intakes[c["intakeId"]]["version"] = 2
    store.intakes[c["intakeId"]]["fields"][1]["effective"] = 70000
    store.flush()
    assert drain(store) == 3
    rows = history(store, c)
    assert sorted((x["intakeVersion"], x["outcome"]) for x in rows) == [
        (1, "MATCH"),
        (2, "NO_MATCH"),
    ]
    assert len({x["receiptFactsSha256"] for x in rows}) == 2
    assert drain(store) == 0


def test_earlier_event_delivered_before_other_side_commit_is_not_a_missed_pair(owned_case):
    c = owned_case
    first = c["open"]()
    first.refresh(c["tenant"])
    first.saved_views.append(search(c))
    first.flush()
    assert drain(first) == 1 and history(first, c) == []
    other = c["open"]()
    other.refresh(c["tenant"])
    other.intakes[c["intakeId"]] = intake(c)
    other.flush()
    assert drain(other) == 1
    assert history(other, c)[0]["outcome"] == "MATCH"


def test_match_ledger_failure_rolls_back_ledger_and_real_queue_job_can_retry(
    owned_case, monkeypatch
):
    c = owned_case
    store = c["open"]()
    store.refresh(c["tenant"])
    store.saved_views.append(search(c))
    store.intakes[c["intakeId"]] = intake(c)
    store.flush()
    queue = DurableJobQueue(store._engine)
    job = queue.claim_next(worker_id="synthetic-listing-search-worker")
    assert job
    real = ListingSearchMatchStore.reconcile

    def fail_after_write(self, **kwargs):
        real(self, **kwargs)
        raise RuntimeError("Synthetic ledger failure")

    monkeypatch.setattr(ListingSearchMatchStore, "reconcile", fail_after_write)
    delivery = ListingSearchDelivery(TenantScopedDocumentStore(store._store, c["tenant"]), queue)
    with pytest.raises(RuntimeError, match="Synthetic ledger failure"):
        delivery.deliver(job)
    assert history(store, c) == []
    new = c["open"]()
    new.refresh(c["tenant"])
    assert c["intakeId"] in new.intakes
    monkeypatch.setattr(ListingSearchMatchStore, "reconcile", real)
    delivery.deliver(job)
    delivery.deliver(job)
    assert len(history(store, c)) == 1


def test_publicly_forged_job_cannot_impersonate_a_server_reserved_event(owned_case):
    c = owned_case
    store = c["open"]()
    store.refresh(c["tenant"])
    store.saved_views.append(search(c))
    store.intakes[c["intakeId"]] = intake(c)
    store.flush()
    queue = DurableJobQueue(store._engine)
    original = queue.claim_next(worker_id="synthetic-listing-search-worker")
    assert original
    fake, _ = queue.enqueue(
        JobRequest(
            job_type=JOB_TYPE, payload=dict(original.payload), idempotency_key="synthetic-forged"
        ),
        correlation_id="synthetic",
    )
    delivery = ListingSearchDelivery(TenantScopedDocumentStore(store._store, c["tenant"]), queue)
    with pytest.raises(NonRetryableJobError, match="UNBOUND_EVENT"):
        delivery.deliver(fake)
    with pytest.raises(NonRetryableJobError, match="UNBOUND_EVENT"):
        delivery.deliver(replace(original, fence_token=original.fence_token + 1))
    assert history(store, c) == []


def test_pre_feature_canonical_receipt_is_bootstrapped_when_search_is_created(owned_case):
    c = owned_case
    store = c["open"]()
    store.refresh(c["tenant"])
    record = intake(c)
    # Exact pre-feature native owner layout, before any search events existed.
    store._store.put(
        "operator.assisted_intakes",
        f"{c['tenant']}:{c['intakeId']}",
        store._wrap(c["tenant"], c["intakeId"], record),
    )
    store.refresh(c["tenant"])
    assert store.intakes[c["intakeId"]] == record
    store.saved_views.append(search(c))
    store.flush()
    drain(store)
    assert history(store, c)[0]["outcome"] == "MATCH"


def test_receipt_parent_holder_can_flush_while_competing_correction_waits_for_parent(owned_case):
    c = owned_case
    initial = c["open"]()
    initial.refresh(c["tenant"])
    initial.saved_views.append(search(c))
    initial.intakes[c["intakeId"]] = intake(c)
    initial.flush()
    drain(initial)
    holder = c["open"]()
    correction = c["open"]()
    parent_locked = Event()
    correction_attempted = Event()

    def promotion_order():
        holder.refresh(c["tenant"])
        with holder.transaction():
            holder._engine.execute("SET LOCAL lock_timeout = '3s'")
            holder._read_locked("operator.assisted_intakes", f"{c['tenant']}:{c['intakeId']}")
            parent_locked.set()
            assert correction_attempted.wait(5)
            holder.intakes[c["intakeId"]]["version"] = 2
            holder.intakes[c["intakeId"]]["fields"][1]["effective"] = 55000
            holder.flush()

    def correction_order():
        assert parent_locked.wait(5)
        correction.refresh(c["tenant"])
        original = correction._read_locked

        def mark_waiting(collection, key):
            if collection == "operator.assisted_intakes":
                correction_attempted.set()
            return original(collection, key)

        correction._read_locked = mark_waiting
        correction.intakes[c["intakeId"]]["version"] = 2
        correction.intakes[c["intakeId"]]["fields"][1]["effective"] = 57000
        with pytest.raises(AssistedIntakePersistenceConflict):
            correction.flush()

    with ThreadPoolExecutor(max_workers=2) as pool:
        fs = [pool.submit(promotion_order), pool.submit(correction_order)]
        for f in fs:
            f.result(timeout=12)
    new = c["open"]()
    new.refresh(c["tenant"])
    assert new.intakes[c["intakeId"]]["fields"][1]["effective"] == 55000
    drain(new)
    assert len(history(new, c)) == 2


def personal_owner(case):
    from modules.listing.application.saved_search_owner import SavedListingSearchOwner
    from shared.auth import Principal, Role, Scope
    from shared.infrastructure.persistence.audit_log import DurableAuditLog

    store = case["open"]()
    queue = DurableJobQueue(store._engine)
    audit = DurableAuditLog(store._engine)
    principal = Principal(
        subject_id=case["owner"],
        roles=frozenset({Role.SITE_REVIEWER}),
        scope=Scope(tenant_id=case["tenant"]),
    )
    return SavedListingSearchOwner(store, queue, audit), principal


def personal_create(owner, principal, case, **query_updates):
    return owner.command(
        principal,
        operation="create",
        payload={
            "name": "Synthetic personal search",
            "query": query(heatZoneId=case["zone"], **query_updates),
        },
        key="synthetic-create-criteria-0001",
        correlation_id="synthetic-search-command",
    )


def test_personal_search_create_reopen_edit_delete_and_replay_keep_native_owner(owned_case):
    c = owned_case
    owner, principal = personal_owner(c)
    created = personal_create(owner, principal, c)
    identity = created["search"]["saved_view_id"]
    assert created["search"]["visibility"] == "PRIVATE" and created["auditEventIds"]
    replay = personal_create(owner, principal, c)
    assert (
        replay["replayed"]
        and replay["search"] == created["search"]
        and replay["receiptId"] == created["receiptId"]
    )
    restarted, p = personal_owner(c)
    assert restarted.get(p, identity) == created["search"]
    assert [x["saved_view_id"] for x in restarted.list(p, heat_zone_id=c["zone"])["items"]] == [
        identity
    ]
    updated = restarted.command(
        p,
        operation="update",
        identity=identity,
        payload={
            "name": "Edited personal search",
            "query": query(heatZoneId=c["zone"], maxRentPerMonth=45000),
            "expectedVersion": 1,
        },
        key="synthetic-update-criteria-0001",
        correlation_id="synthetic-search-update",
    )
    assert updated["search"]["version"] == 2
    with pytest.raises(HTTPException) as failure:
        restarted.command(
            p,
            operation="update",
            identity=identity,
            payload={
                "name": "Stale update",
                "query": query(heatZoneId=c["zone"]),
                "expectedVersion": 1,
            },
            key="synthetic-stale-criteria-0001",
            correlation_id="synthetic-search-stale",
        )
    assert getattr(failure.value, "status_code", None) == 409
    assert restarted.get(p, identity)["name"] == "Edited personal search"
    args = dict(
        operation="delete",
        identity=identity,
        payload={"expectedVersion": 2},
        key="synthetic-delete-criteria-0001",
        correlation_id="synthetic-search-delete",
    )
    deleted = restarted.command(p, **args)
    assert deleted["search"]["deleted_at"] and deleted["search"]["version"] == 3
    assert restarted.command(p, **args)["replayed"]
    assert restarted.list(p, heat_zone_id=c["zone"])["items"] == []
    assert len(restarted.audit_log.list_events()) == 3
    with pytest.raises(HTTPException) as missing:
        restarted.get(p, identity)
    assert getattr(missing.value, "status_code", None) == 404


def test_current_permissions_masks_and_receipt_versions_govern_saved_hits(owned_case):
    from shared.auth import DataClassification, Role, Scope

    c = owned_case
    owner, principal = personal_owner(c)
    created = personal_create(owner, principal, c)
    identity = created["search"]["saved_view_id"]
    owner.store.refresh(c["tenant"])
    record = intake(c)
    record["submitted_by"] = c["owner"]
    for field in record["fields"]:
        field["classification"] = "INTERNAL"
    owner.store.intakes[c["intakeId"]] = record
    owner.store.flush()
    drain(owner.store)
    assert owner.results(principal, identity)["total"] == 1
    masked = replace(
        principal, scope=Scope(tenant_id=c["tenant"], clearance=DataClassification.PUBLIC)
    )
    assert owner.results(masked, identity)["items"] == []
    staff = replace(principal, roles=frozenset({Role.EXPANSION_USER}))
    assert owner.results(staff, identity)["total"] == 1
    # Change the canonical ownership and revision; historical hits confer no
    # permission to expose the current row or another person's submission.
    owner.store.refresh(c["tenant"])
    current = owner.store.intakes[c["intakeId"]]
    current.update(version=2, submitted_by=str(uuid4()))
    owner.store.flush()
    assert owner.results(principal, identity)["total"] == 0
    drain(owner.store)
    assert owner.results(staff, identity)["total"] == 0
    assert owner.results(principal, identity)["total"] == 1
    scope_lost = replace(
        principal, scope=Scope(tenant_id=c["tenant"], heat_zone_ids=frozenset({str(uuid4())}))
    )
    with pytest.raises(HTTPException) as denied:
        owner.results(scope_lost, identity)
    assert getattr(denied.value, "status_code", None) == 403


def test_same_tenant_other_subject_cannot_read_modify_delete_or_replay_private_search(owned_case):
    c = owned_case
    owner, principal = personal_owner(c)
    identity = personal_create(owner, principal, c)["search"]["saved_view_id"]
    other = replace(principal, subject_id=str(uuid4()))
    assert owner.list(other, heat_zone_id=c["zone"])["items"] == []
    for action in [
        lambda: owner.get(other, identity),
        lambda: owner.results(other, identity),
        lambda: owner.command(
            other,
            operation="delete",
            identity=identity,
            payload={"expectedVersion": 1},
            key="synthetic-delete-criteria-0001",
            correlation_id="synthetic-other",
        ),
    ]:
        with pytest.raises(HTTPException) as denied:
            action()
        assert getattr(denied.value, "status_code", None) == 404
    assert owner.get(principal, identity)["version"] == 1
    assert len(owner.audit_log.list_events()) == 1


def test_http_personal_criteria_uses_real_operator_guard_and_real_postgresql_owner(owned_case):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.api.app.routes.operator_modules.listing_searches import (
        create_listing_search_sub_router,
    )
    from apps.api.oday_api.security.dependencies import build_engine, require_operator_permission
    from shared.auth import Action

    c = owned_case
    owner, principal = personal_owner(c)
    app = FastAPI()
    app.state.assisted_intake_store = owner.store
    app.state.job_queue = owner.queue
    app.state.audit_log = owner.audit_log

    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = "synthetic-real-HTTP-search"
        return await call_next(request)

    app.include_router(
        create_listing_search_sub_router(
            require_operator_permission(
                "listing", Action.VIEW, engine=build_engine(audit_log=owner.audit_log)
            )
        ),
        prefix="/api/v1/operator/network-listings",
    )
    from apps.api.app.routes.listings import create_assisted_intake_router

    app.include_router(
        create_assisted_intake_router(store=owner.store, audit_log=owner.audit_log),
        prefix="/api/v1",
    )
    client = TestClient(app)
    base = "/api/v1/operator/network-listings/searches"
    headers = {
        "x-subject-id": principal.subject_id,
        "x-tenant-id": principal.tenant_id,
        "x-roles": "site_reviewer",
        "x-operator-role": "expansion-manager",
        "Idempotency-Key": "synthetic-real-http-search-0001",
    }
    body = {"name": "Synthetic HTTP criteria", "query": query(heatZoneId=c["zone"])}
    assert client.get(base, params={"heatZoneId": c["zone"]}).status_code == 401
    response = client.post(base, json=body, headers=headers)
    assert response.status_code == 201, response.text
    identity = response.json()["search"]["saved_view_id"]
    owner.store.refresh(c["tenant"])
    visible_record = intake(c)
    visible_record["submitted_by"] = principal.subject_id
    for field in visible_record["fields"]:
        field["classification"] = "INTERNAL"
    owner.store.intakes[c["intakeId"]] = visible_record
    owner.store.flush()
    drain(owner.store)
    result = client.get(base + "/" + identity + "/results", headers=headers)
    assert result.status_code == 200 and result.json()["items"][0]["intakeId"] == c["intakeId"]
    assert (
        client.get(base + "/" + identity, headers=headers).json()["owner_subject_id"]
        == principal.subject_id
    )
    assert (
        client.get(base, params={"heatZoneId": c["zone"]}, headers=headers).json()["items"][0][
            "saved_view_id"
        ]
        == identity
    )
    assert client.put(base + "/" + identity, json=body, headers=headers).status_code == 428
    assert (
        client.post(base, json={**body, "visibility": "TENANT"}, headers=headers).status_code == 422
    )
    assert (
        client.post(
            base,
            json={**body, "query": query(heatZoneId=c["zone"], maxRentPerMonth=True)},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.get(
            base + "/" + identity, headers={**headers, "x-subject-id": str(uuid4())}
        ).status_code
        == 404
    )
    for role, persona in [
        ("auditor", "pm-audit"),
        ("operations_manager", "ops-lead"),
        ("platform_admin", "platform-admin"),
    ]:
        denied = client.get(
            base,
            params={"heatZoneId": c["zone"]},
            headers={**headers, "x-roles": role, "x-operator-role": persona},
        )
        assert denied.status_code == 403
    changed = client.put(
        base + "/" + identity,
        json={**body, "name": "Updated HTTP criteria", "expectedVersion": 1},
        headers={**headers, "Idempotency-Key": "synthetic-real-http-update-0001"},
    )
    assert changed.status_code == 200, changed.text
    removed = client.request(
        "DELETE",
        base + "/" + identity,
        json={"expectedVersion": 2},
        headers={**headers, "Idempotency-Key": "synthetic-real-http-delete-0001"},
    )
    assert removed.status_code == 200, removed.text
    assert client.get(base + "/" + identity, headers=headers).status_code == 404
    native = client.post(
        "/api/v1/saved-views",
        json={**body, "resource": "intake", "visibility": "PRIVATE"},
        headers={**headers, "Idempotency-Key": "synthetic-native-search-create-0001"},
    )
    assert native.status_code == 201, native.text
    native_id = native.json()["saved_view_id"]
    assert client.get(base + "/" + native_id, headers=headers).json()["saved_view_id"] == native_id
    native_list = client.get("/api/v1/saved-views", headers=headers)
    assert native_list.status_code == 200 and [v["saved_view_id"] for v in native_list.json()] == [
        native_id
    ]


def test_current_approved_feed_policy_and_snapshot_are_checked_again_after_revocation(
    owned_case, intake_blank_db
):
    from datetime import UTC, datetime, timedelta

    c = owned_case
    intake_blank_db.apply_migration()
    owner, principal = personal_owner(c)
    source = "synthetic-listing-search-feed"
    snapshot = str(uuid4())
    now = datetime.now(UTC)
    with intake_blank_db.connect() as conn:
        conn.execute(
            "INSERT INTO intake.source_registry(source_id,display_name,allowed_hosts,canonicalization_rule_version,retrieval_mode,policy_owner_subject_id,legal_approval_ref,license_approval_ref,review_expires_at,kill_switch,production_enabled) VALUES(%s,%s,ARRAY['example.invalid'],'synthetic-v1','APPROVED_RETRIEVAL',%s,%s,%s,%s,false,true)",
            (
                source,
                "Synthetic approved feed only",
                c["owner"],
                "synthetic-legal",
                "synthetic-license",
                now + timedelta(days=1),
            ),
        )
        conn.execute(
            "INSERT INTO intake.intakes(intake_id,tenant_id,submitter_subject_id,intake_method,source_id,source_policy_state,processing_state,correlation_id) VALUES(%s,%s,%s,'APPROVED_FEED',%s,'APPROVED_RETRIEVAL','READY',%s)",
            (c["intakeId"], c["tenant"], c["owner"], source, str(uuid4())),
        )
        conn.execute(
            "INSERT INTO intake.source_snapshots(source_snapshot_id,tenant_id,intake_id,source_id,raw_object_uri,content_sha256,media_type,byte_length,captured_at,observed_at,capture_method,retention_class,encryption_key_ref) VALUES(%s,%s,%s,%s,%s,%s,'application/json',2,%s,%s,'APPROVED_FEED','BUSINESS_5Y','synthetic-owned-test-key')",
            (
                snapshot,
                c["tenant"],
                c["intakeId"],
                source,
                "gs://synthetic-no-cloud-claim/" + snapshot,
                "a" * 64,
                now - timedelta(minutes=1),
                now - timedelta(minutes=1),
            ),
        )
    identity = personal_create(owner, principal, c)["search"]["saved_view_id"]
    owner.store.refresh(c["tenant"])
    record = intake(c)
    record.update(
        intake_method="APPROVED_FEED",
        policy_state="APPROVED_RETRIEVAL",
        source_id=source,
        source_policy_version=1,
        source_snapshot_id=snapshot,
        source_snapshot_version=1,
        submitted_by=c["owner"],
    )
    for field in record["fields"]:
        field["classification"] = "INTERNAL"
    owner.store.intakes[c["intakeId"]] = record
    owner.store.flush()
    drain(owner.store)
    assert owner.results(principal, identity)["total"] == 1
    with intake_blank_db.connect() as conn:
        conn.execute(
            "UPDATE intake.source_registry SET kill_switch=true WHERE source_id=%s", (source,)
        )
    assert owner.results(principal, identity)["items"] == []
    with intake_blank_db.connect() as conn:
        conn.execute(
            "UPDATE intake.source_registry SET kill_switch=false,version=2 WHERE source_id=%s",
            (source,),
        )
    assert owner.results(principal, identity)["items"] == []
    with intake_blank_db.connect() as conn:
        conn.execute(
            "UPDATE intake.source_registry SET version=1,review_expires_at=%s WHERE source_id=%s",
            (now - timedelta(days=1), source),
        )
    assert owner.results(principal, identity)["items"] == []


def test_declared_personal_filters_project_all_current_record_axes_before_counting(owned_case):
    from shared.auth import Scope

    c = owned_case
    owner, principal = personal_owner(c)
    axes = {
        name: str(uuid4()) for name in ("brand_id", "region_id", "store_id", "assigned_area_id")
    }
    principal = replace(
        principal,
        scope=Scope(
            tenant_id=c["tenant"],
            brand_ids=frozenset({axes["brand_id"]}),
            region_ids=frozenset({axes["region_id"]}),
            store_ids=frozenset({axes["store_id"]}),
            assigned_area_ids=frozenset({axes["assigned_area_id"]}),
            heat_zone_ids=frozenset({c["zone"]}),
        ),
    )
    identity = personal_create(owner, principal, c)["search"]["saved_view_id"]
    owner.store.refresh(c["tenant"])
    for wrong_brand in (False, True):
        record = intake(c)
        record["intake_id"] = str(uuid4())
        record["scope"].update(axes)
        if wrong_brand:
            record["scope"]["brand_id"] = str(uuid4())
        record["submitted_by"] = c["owner"]
        for field in record["fields"]:
            field["classification"] = "INTERNAL"
        owner.store.intakes[record["intake_id"]] = record
    owner.store.flush()
    drain(owner.store)
    assert len(owner.list(principal, heat_zone_id=c["zone"])["items"]) == 1
    result = owner.results(principal, identity)
    assert result["total"] == 1 and len(result["items"]) == 1
