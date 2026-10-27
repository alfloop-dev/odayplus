"""Real listing SQL writes and their operator receipt must commit together.

All identities and source rows here are synthetic. These tests establish the
legacy marker's SQL transaction boundary, not physical identity execution,
producer-owned revision CAS, human acceptance, or production cloud delivery.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.api.app.routes.operator import create_operator_router
from apps.api.app.routes.operator_modules import network_listings as routes
from modules.opsboard.application.network_listings import NetworkListingService
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.infrastructure.persistence.repositories import (
    DurableDecisionStore,
    DurableListingRepository,
)
from tests.unit.test_network_listing_mutation_safety import ACTOR, SOURCE, TARGET, TENANT, _service


def _rows(engine):
    return {
        table: [dict(row) for row in engine.query(f"SELECT * FROM {table} ORDER BY 1, 2")]
        for table in ("durable_documents", "durable_sequences")
    }


def _wired_resolver(engine, monkeypatch, *, seed=True):
    """Capture the actual production router's resolver, without a new opt-in."""
    store = SqliteDocumentStore(engine)
    scoped = TenantScopedDocumentStore(store, TENANT)
    listings = DurableListingRepository(scoped)
    intakes = DurableAssistedIntakeRepository(scoped)
    state = NetworkListingService(tenant_id=TENANT).export_state()
    state["listings"].extend(_service().export_state()["listings"])
    writer = NetworkListingService(initial_state=state, tenant_id=TENANT)
    if seed:
        for row in state["listings"]:
            listings.save_listing(*writer._dict_to_listing(row))
            intakes.save_listing_metadata(row["id"], deepcopy(row))
    captured = []
    original = routes.create_network_listings_sub_router

    def capture(*args, **kwargs):
        captured.append(kwargs["service_resolver"])
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(routes, "create_network_listings_sub_router", capture)
        create_operator_router(
            document_store=store, require_live_data=True, allow_test_reset=True,
            listing_repository_for_tenant=lambda tenant: DurableListingRepository(TenantScopedDocumentStore(store, tenant)),
            sitescore_decision_repository_for_tenant=lambda tenant: DurableDecisionStore(TenantScopedDocumentStore(store, tenant)),
        )
    assert len(captured) == 1
    resolver = captured[0]
    if seed:
        resolver._repository.save(TENANT, writer.export_state())
    return resolver, listings, intakes


def _invoke(resolver, action, *, key="synthetic-atomic-once"):
    args = {
        "reason": "Synthetic operator reason retained with the atomic receipt",
        "actor_name": ACTOR, "actor_role_id": "site_reviewer",
        "idempotency_key": key,
        "correlation_id": "00000000-0000-0000-0000-000000000301",
        "if_match": 'W/"7"',
        "authorize_existing": lambda row: None,
    }
    if action == "merge":
        args.update(source_listing_id=SOURCE, target_listing_id=TARGET,
                    target_if_match='W/"11"', risk_acknowledged=True,
                    risk_summary="Synthetic merge retains both original evidence sets")
    else:
        args.update(listing_id=SOURCE)
    return resolver._invoke(TENANT, f"{action}_listing", **args)


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.requires_live_env)])
def listing_engine(request, tmp_path):
    if request.param == "sqlite":
        engine = SqliteEngine(tmp_path / "listing-atomic.sqlite3")
    else:
        db = request.getfixturevalue("intake_blank_db")
        from shared.infrastructure.persistence.postgresql import PostgresEngine
        engine = PostgresEngine(db.url(), validate_schema=False)
        # This local listing-only fixture supplies the audit reader's relation,
        # not the complete canonical schema. The actual admission gate reads
        # its empty SQL corpus; no guard is replaced with a healthy stub.
        engine.execute("CREATE SCHEMA audit")
        engine.execute("CREATE TABLE audit.audit_events (audit_event_id UUID PRIMARY KEY, "
                       "tenant_id UUID NOT NULL, event_sha256 TEXT NOT NULL, worm_object_uri TEXT)")
    try:
        yield engine
    finally:
        engine.close()


@pytest.mark.parametrize("action", ["archive", "merge"])
@pytest.mark.parametrize("failure", ["listing", "metadata", "receipt", "aggregate", "after_save"])
def test_actual_router_failure_after_sql_write_rolls_back_all_rows(listing_engine, monkeypatch, action, failure):
    resolver, _, _ = _wired_resolver(listing_engine, monkeypatch)
    injected = []

    def fail_after_real_write(original):
        def invoke(*args, **kwargs):
            original(*args, **kwargs)
            injected.append(failure)
            raise RuntimeError("Synthetic injected failure after a real SQL write")
        return invoke

    if failure == "listing":
        monkeypatch.setattr(DurableListingRepository, "save_listing", fail_after_real_write(DurableListingRepository.save_listing))
    elif failure == "metadata":
        monkeypatch.setattr(DurableAssistedIntakeRepository, "save_listing_metadata", fail_after_real_write(DurableAssistedIntakeRepository.save_listing_metadata))
    elif failure == "receipt":
        monkeypatch.setattr(DurableAssistedIntakeRepository, "save_idempotency_record", fail_after_real_write(DurableAssistedIntakeRepository.save_idempotency_record))
    elif failure == "aggregate":
        monkeypatch.setattr(resolver._repository, "save", fail_after_real_write(resolver._repository.save))
    else:
        def fail_finalization(service, tenant):
            injected.append(failure)
            raise RuntimeError("Synthetic finalization failure")

        resolver._after_save = fail_finalization
    before = _rows(listing_engine)
    with pytest.raises((RuntimeError, HTTPException)):
        _invoke(resolver, action)
    assert injected == [failure]
    assert _rows(listing_engine) == before


@pytest.mark.parametrize("action", ["archive", "merge"])
def test_committed_records_receipt_and_restart_projection_agree(listing_engine, monkeypatch, action):
    resolver, listings, intakes = _wired_resolver(listing_engine, monkeypatch)
    result = _invoke(resolver, action)
    state = resolver._repository.load(TENANT)
    service = NetworkListingService(
        listing_repository=listings, intake_repository=intakes,
        initial_state=state, seed_fixtures=False, tenant_id=TENANT,
    )
    row = result["listing"] if action == "archive" else result["source"]
    assert service.get_listing(SOURCE)["status"].lower() == row["status"].lower()
    assert len(intakes.list_idempotency_records()) == 1
    assert len([event for event in state["auditEvents"] if event["action"] == f"listing.{action}"]) == 1
    before_replay = _rows(listing_engine)
    assert _invoke(resolver, action) == result
    # The aggregate is a rebuilt read projection and can gain nullable fields;
    # canonical resources and the logical receipt must not be written again.
    after_replay = _rows(listing_engine)
    for rows in (before_replay, after_replay):
        rows["durable_documents"] = [
            document for document in rows["durable_documents"]
            if not document["collection"].startswith("operator.live_domain_state.")
        ]
    assert after_replay == before_replay
    replay_state = resolver._repository.load(TENANT)
    assert replay_state["auditEvents"] == state["auditEvents"]
    assert replay_state["idempotencyCache"] == state["idempotencyCache"]


@pytest.mark.parametrize("participant", [
    "listing-engine", "listing-tenant", "metadata-engine", "metadata-tenant",
    "decision-engine", "decision-tenant", "service-tenant", "missing-listings", "missing-metadata",
])
def test_mismatched_participants_are_503_before_either_database_is_written(tmp_path, monkeypatch, participant):
    engine = SqliteEngine(tmp_path / "binding.sqlite3")
    other = SqliteEngine(tmp_path / "different-engine.sqlite3")
    try:
        resolver, _, _ = _wired_resolver(engine, monkeypatch)
        service = resolver._factory(resolver._repository.load(TENANT), TENANT)
        if participant.startswith("listing-"):
            service._listing_repository._store = TenantScopedDocumentStore(
                SqliteDocumentStore(other if participant.endswith("engine") else engine),
                "another-tenant" if participant.endswith("tenant") else TENANT,
            )
        elif participant.startswith("metadata-"):
            service._intakes._store = TenantScopedDocumentStore(
                SqliteDocumentStore(other if participant.endswith("engine") else engine),
                "another-tenant" if participant.endswith("tenant") else TENANT,
            )
        elif participant.startswith("decision-"):
            service._review_decision_repository = DurableDecisionStore(TenantScopedDocumentStore(
                SqliteDocumentStore(other if participant.endswith("engine") else engine),
                "another-tenant" if participant.endswith("tenant") else TENANT,
            ))
        elif participant == "service-tenant":
            service._tenant_id = "another-tenant"
        elif participant == "missing-listings":
            service._listing_repository = None
        else:
            service._intakes = None
        resolver._factory = lambda state, tenant: service
        before, other_before = _rows(engine), _rows(other)
        with pytest.raises(HTTPException) as error:
            _invoke(resolver, "archive")
        assert error.value.status_code == 503
        assert error.value.detail["operation"] == "archive_listing.bind"
        assert _rows(engine) == before
        assert _rows(other) == other_before
    finally:
        other.close()
        engine.close()


def test_missing_transaction_engine_is_503_before_factory_or_mutation(tmp_path, monkeypatch):
    engine = SqliteEngine(tmp_path / "missing-engine.sqlite3")
    try:
        resolver, _, _ = _wired_resolver(engine, monkeypatch)
        before = _rows(engine)
        resolver._repository._store = SimpleNamespace(engine=None)
        with pytest.raises(HTTPException) as error:
            _invoke(resolver, "archive")
        assert error.value.status_code == 503
        assert _rows(engine) == before
    finally:
        engine.close()


@pytest.mark.requires_live_env
@pytest.mark.parametrize("action", ["archive", "merge"])
def test_separate_postgres_replicas_serialize_before_loading_receipt(intake_blank_db, monkeypatch, action):
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    first = PostgresEngine(intake_blank_db.url(), validate_schema=False)
    second = PostgresEngine(intake_blank_db.url(), validate_schema=False)
    entered, release, second_started = Event(), Event(), Event()
    try:
        first.execute("CREATE SCHEMA audit")
        first.execute("CREATE TABLE audit.audit_events (audit_event_id UUID PRIMARY KEY, "
                      "tenant_id UUID NOT NULL, event_sha256 TEXT NOT NULL, worm_object_uri TEXT)")
        resolver, _, _ = _wired_resolver(first, monkeypatch)
        replica, _, intakes = _wired_resolver(second, monkeypatch, seed=False)

        def pause_after_all_writes(service, tenant):
            entered.set()
            assert release.wait(10), "Synthetic writer coordination timed out"

        def invoke_replica():
            second_started.set()
            return _invoke(replica, action)

        resolver._after_save = pause_after_all_writes
        with ThreadPoolExecutor(max_workers=2) as workers:
            write = workers.submit(_invoke, resolver, action)
            try:
                assert entered.wait(10)
                lock = second.query_one(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended(?, 0)) AS available",
                    (f"operator-domain:network-listings:{TENANT}",),
                )
                assert lock["available"] is False
                replay = workers.submit(invoke_replica)
                assert second_started.wait(10)
            finally:
                release.set()
            assert replay.result(timeout=10) == write.result(timeout=10)
        state = replica._repository.load(TENANT)
        assert len(intakes.list_idempotency_records()) == 1
        assert len([event for event in state["auditEvents"] if event["action"] == f"listing.{action}"]) == 1
    finally:
        release.set()
        second.close()
        first.close()
