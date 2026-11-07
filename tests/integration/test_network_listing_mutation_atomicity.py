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


CANDIDATE = "00000000-0000-0000-0000-000000000901"


def _candidate_projection_service(engine, monkeypatch):
    resolver, listings, intakes = _wired_resolver(engine, monkeypatch)
    service = NetworkListingService(listing_repository=listings, intake_repository=intakes,
        seed_fixtures=False, tenant_id=TENANT)
    service.save_candidate({"id": CANDIDATE, "listingId": SOURCE, "status": "scored",
        "heatZoneId": "synthetic-recorded-H3-cell", "feasibilityFlags": [], "title": "Original title",
        "score": 72, "recommendation": "WAIT", "modelVersion": "synthetic-model-v1",
        "datasetSnapshotId": "synthetic-data-v1", "missingData": [], "reviewId": None})
    return resolver, listings, intakes


@pytest.mark.parametrize("hold", [True, False, None])
def test_candidate_public_write_preserves_unprojected_source_metadata_and_explicit_nulls(listing_engine, monkeypatch, hold):
    _, listings, intakes = _candidate_projection_service(listing_engine, monkeypatch)
    facts = {"tenantId": TENANT, "brandId": "recorded-legacy-brand", "regionId": "00000000-0000-0000-0000-000000000902",
        "assignedAreaId": "recorded-legacy-area", "heatZoneId": "00000000-0000-0000-0000-000000000903",
        "has_legal_hold": hold, "hasLegalHold": False, "version": 7,
        "data_classification": "INTERNAL", "sensitive": True, "created_at": "2025-01-01T00:00:00Z",
        "archivedAt": None, "custodyReference": {"original": "source-sidecar", "checksum": "d" * 64}}
    original = intakes.get_candidate_metadata(CANDIDATE)
    intakes.save_candidate_metadata(CANDIDATE, {**original, **facts})
    # The actual live projection excludes these source-owned fields. A fresh
    # service must update its own values without pretending it projects them.
    fresh = NetworkListingService(listing_repository=listings, intake_repository=intakes,
        seed_fixtures=False, tenant_id=TENANT)
    candidate = deepcopy(fresh.get_candidate(CANDIDATE))
    assert "custodyReference" not in candidate and "regionId" not in candidate
    candidate.update(title=None, score=None, recommendation=None, modelVersion=None, datasetSnapshotId=None,
                     missingData=None, reviewId=None)
    fresh.save_candidate(candidate)
    saved = intakes.get_candidate_metadata(CANDIDATE)
    assert set(facts) <= set(saved)
    assert {key: saved[key] for key in facts} == facts
    assert all(key in saved and saved[key] is None for key in (
        "title", "score", "recommendation", "modelVersion", "datasetSnapshotId", "missingData", "reviewId"))
    _, _, restored = _wired_resolver(listing_engine, monkeypatch, seed=False)
    assert restored.get_candidate_metadata(CANDIDATE) == saved
    physical = listings.list_candidates()[0]
    assert physical.candidate_site.candidate_site_id == CANDIDATE and physical.score is None
    assert physical.heat_zone_id == "synthetic-recorded-H3-cell"  # Not the metadata authorization UUID.


def test_candidate_metadata_full_replace_and_review_cas_keep_their_original_semantics(listing_engine, monkeypatch):
    _, _, intakes = _candidate_projection_service(listing_engine, monkeypatch)
    owned = {"reviewWorkflow": {"synthetic": "existing-owned-workflow"}, "reviewSubmission": {"synthetic": "existing-owned-submission"}}
    intakes.save_review_metadata(CANDIDATE, {"regionId": "recorded-to-remove", **owned}, expected_owned={})
    intakes.save_candidate_metadata(CANDIDATE, {"title": "Explicit full replacement"})
    assert intakes.get_candidate_metadata(CANDIDATE) == {"title": "Explicit full replacement", **owned}
    before = intakes._store.get_versioned(intakes._CANDIDATE_META, CANDIDATE)
    with pytest.raises(ValueError, match="CANDIDATE_REVIEW_METADATA_CONFLICT"):
        intakes.save_review_metadata(CANDIDATE, {"title": "Must not write"}, expected_owned={})
    assert intakes._store.get_versioned(intakes._CANDIDATE_META, CANDIDATE) == before
    intakes.save_review_metadata(CANDIDATE, {"title": "Owned removal of review keys"}, expected_owned=owned)
    assert intakes.get_candidate_metadata(CANDIDATE) == {"title": "Owned removal of review keys"}


@pytest.mark.requires_live_env
def test_candidate_projection_reads_latest_sidecar_only_after_the_original_postgres_guard(intake_blank_db, monkeypatch):
    from time import monotonic, sleep

    from shared.infrastructure.persistence.postgresql import PostgresEngine

    first = PostgresEngine(intake_blank_db.url(), validate_schema=False)
    second = PostgresEngine(intake_blank_db.url(), validate_schema=False)
    physical_written, read_current = Event(), Event()
    try:
        first.execute("CREATE SCHEMA audit")
        first.execute("CREATE TABLE audit.audit_events (audit_event_id UUID PRIMARY KEY, "
                      "tenant_id UUID NOT NULL, event_sha256 TEXT NOT NULL, worm_object_uri TEXT)")
        _, listings, intakes = _candidate_projection_service(first, monkeypatch)
        scoped = TenantScopedDocumentStore(SqliteDocumentStore(second), TENANT)
        other = DurableAssistedIntakeRepository(scoped)
        service = NetworkListingService(listing_repository=listings, intake_repository=intakes,
            seed_fixtures=False, tenant_id=TENANT)
        candidate = deepcopy(service.get_candidate(CANDIDATE))
        candidate.update(score=91, recommendation="GO")
        original_save = listings.save_candidate
        original_read = service._get_candidate_metadata

        def observe_physical_save(value):
            original_save(value)
            physical_written.set()

        def observe_metadata_read(key):
            result = original_read(key)
            read_current.set()
            return result

        monkeypatch.setattr(listings, "save_candidate", observe_physical_save)
        monkeypatch.setattr(service, "_get_candidate_metadata", observe_metadata_read)
        with ThreadPoolExecutor(max_workers=1) as workers:
            with other.candidate_metadata_guard(CANDIDATE):
                blocker = second.query_one("SELECT pg_backend_pid() AS pid")["pid"]
                future = workers.submit(service.save_candidate, candidate)
                assert physical_written.wait(10), "Original physical write did not reach its commit"
                deadline = monotonic() + 10
                with intake_blank_db.connect() as observer:
                    while True:
                        waiting = observer.execute("SELECT count(*) FROM pg_locks l WHERE NOT l.granted "
                            "AND l.locktype='advisory' AND %s=ANY(pg_blocking_pids(l.pid))", (blocker,)).fetchone()[0]
                        if waiting:
                            break
                        assert monotonic() < deadline, "Original metadata advisory wait was not observed"
                        sleep(0.01)
                assert not future.done() and not read_current.is_set()
                latest = {**other.get_candidate_metadata(CANDIDATE), "regionId": "late-recorded-region", "has_legal_hold": True,
                          "custodyReference": {"original": "committed-by-other-replica"}}
                other.save_candidate_metadata(CANDIDATE, latest)
            future.result(timeout=10)
        saved = intakes.get_candidate_metadata(CANDIDATE)
        assert read_current.is_set()
        assert saved["regionId"] == latest["regionId"] and saved["has_legal_hold"] is True
        assert saved["custodyReference"] == latest["custodyReference"]
        assert saved["score"] == 91 and saved["recommendation"] == "GO"
    finally:
        second.close()
        first.close()


def test_candidate_projection_sidecar_survives_closed_sqlite_engine_and_reload(tmp_path, monkeypatch):
    path = tmp_path / "candidate-source-custody-restart.sqlite3"
    original = SqliteEngine(path)
    facts = {"regionId": "recorded-region", "brandId": "recorded-brand", "has_legal_hold": None,
             "custodyReference": {"original": "retained across restart"}}
    try:
        _, listings, intakes = _candidate_projection_service(original, monkeypatch)
        intakes.save_candidate_metadata(CANDIDATE, {**intakes.get_candidate_metadata(CANDIDATE), **facts})
        service = NetworkListingService(listing_repository=listings, intake_repository=intakes,
            seed_fixtures=False, tenant_id=TENANT)
        candidate = deepcopy(service.get_candidate(CANDIDATE))
        candidate.update(score=93, recommendation="GO", title="Latest presentation")
        service.save_candidate(candidate)
    finally:
        original.close()
    restarted = SqliteEngine(path)
    try:
        _, listings, intakes = _wired_resolver(restarted, monkeypatch, seed=False)
        persisted = intakes.get_candidate_metadata(CANDIDATE)
        assert set(facts) <= set(persisted) and {key: persisted[key] for key in facts} == facts
        assert persisted["score"] == 93 and persisted["title"] == "Latest presentation"
        assert listings.list_candidates()[0].score == 93
    finally:
        restarted.close()


@pytest.mark.requires_live_env
@pytest.mark.parametrize("scope_write", ["replace", "omit"])
def test_listing_projection_reads_latest_sidecar_after_its_actual_postgres_writer_guard(intake_blank_db, monkeypatch, scope_write):
    from time import monotonic, sleep

    from shared.infrastructure.persistence.postgresql import PostgresEngine

    first = PostgresEngine(intake_blank_db.url(), validate_schema=False)
    second = PostgresEngine(intake_blank_db.url(), validate_schema=False)
    fenced, proceed, physical_written, read_current = Event(), Event(), Event(), Event()
    holder = []
    try:
        first.execute("CREATE SCHEMA audit")
        first.execute("CREATE TABLE audit.audit_events (audit_event_id UUID PRIMARY KEY, "
                      "tenant_id UUID NOT NULL, event_sha256 TEXT NOT NULL, worm_object_uri TEXT)")
        _, listings, intakes = _wired_resolver(first, monkeypatch)
        other = DurableAssistedIntakeRepository(TenantScopedDocumentStore(SqliteDocumentStore(second), TENANT))
        service = NetworkListingService(listing_repository=listings, intake_repository=intakes,
            seed_fixtures=False, tenant_id=TENANT)
        listing = deepcopy(service.get_listing(SOURCE))
        listing["rentPerMonth"] = 55555.0
        latest = {**other.get_listing_metadata(SOURCE), "regionId": "late-listing-region", "heatZoneId": "late-owned-heat-zone", "has_legal_hold": True,
                  "custodyReference": {"original": "actual-other-writer"}}
        if scope_write == "omit":
            latest.pop("heatZoneId", None)
            latest.pop("tenantId", None)
        original_put = other._store.put
        original_save = listings.save_listing
        original_read = service._get_listing_metadata

        def pause_actual_metadata_writer(collection, key, value):
            if collection == other._LISTING_META and key == SOURCE:
                holder.append(second.query_one("SELECT pg_backend_pid() AS pid")["pid"])
                fenced.set()
                assert proceed.wait(10), "Synthetic writer coordination timed out"
            return original_put(collection, key, value)

        def observe_physical_save(*args):
            original_save(*args)
            physical_written.set()

        def observe_metadata_read(key):
            value = original_read(key)
            read_current.set()
            return value

        monkeypatch.setattr(other._store, "put", pause_actual_metadata_writer)
        monkeypatch.setattr(listings, "save_listing", observe_physical_save)
        monkeypatch.setattr(service, "_get_listing_metadata", observe_metadata_read)
        with ThreadPoolExecutor(max_workers=2) as workers:
            metadata = workers.submit(other.save_listing_metadata, SOURCE, latest)
            try:
                assert fenced.wait(10)
                write = workers.submit(service.save_listing, listing)
                assert physical_written.wait(10)
                deadline = monotonic() + 10
                with intake_blank_db.connect() as observer:
                    while True:
                        waiting = observer.execute("SELECT count(*) FROM pg_locks l WHERE NOT l.granted "
                            "AND l.locktype='advisory' AND %s=ANY(pg_blocking_pids(l.pid))", (holder[0],)).fetchone()[0]
                        if waiting:
                            break
                        assert monotonic() < deadline, "Actual Listing metadata advisory wait not observed"
                        sleep(0.01)
                read_before_release = read_current.is_set()
                assert not write.done()
            finally:
                proceed.set()
            metadata.result(timeout=10)
            write.result(timeout=10)
        saved = intakes.get_listing_metadata(SOURCE)
        assert saved.get("regionId") == latest["regionId"] and saved.get("has_legal_hold") is True
        assert saved.get("custodyReference") == latest["custodyReference"]
        if scope_write == "omit":
            assert "heatZoneId" not in saved and "tenantId" not in saved
        else:
            assert saved["heatZoneId"] == latest["heatZoneId"] and saved["tenantId"] == TENANT
        assert read_before_release is False, "Projection read old metadata before the actual writer guard"
        assert listings.get_listing(SOURCE).rent_amount == 55555.0
    finally:
        proceed.set()
        second.close()
        first.close()


@pytest.mark.parametrize("heat_zone", [None, "recorded-original-zone"])
def test_listing_projection_keeps_existing_scope_and_full_save_can_change_it(listing_engine, monkeypatch, heat_zone):
    _, listings, intakes = _wired_resolver(listing_engine, monkeypatch)
    original = intakes.get_listing_metadata(SOURCE)
    intakes.save_listing_metadata(SOURCE, {**original, "heatZoneId": heat_zone, "regionId": "recorded-region"})
    service = NetworkListingService(listing_repository=listings, intake_repository=intakes,
        seed_fixtures=False, tenant_id=TENANT)
    listing = deepcopy(service.get_listing(SOURCE))
    listing["heatZoneId"] = "stale-captured-zone"
    listing["rentPerMonth"] = 57000.0
    service.save_listing(listing)
    saved = intakes.get_listing_metadata(SOURCE)
    assert "heatZoneId" in saved and saved["heatZoneId"] == heat_zone
    assert saved["tenantId"] == TENANT and saved["regionId"] == "recorded-region"
    assert listings.get_listing(SOURCE).rent_amount == 57000.0
    # The authoritative full-save hook retains its replacement contract.
    intakes.save_listing_metadata(SOURCE, {"heatZoneId": "explicit-owner-change"})
    assert intakes.get_listing_metadata(SOURCE) == {"heatZoneId": "explicit-owner-change"}


def test_existing_listing_scope_keys_removed_by_owner_do_not_reappear_from_stale_projection(listing_engine, monkeypatch):
    _, listings, intakes = _wired_resolver(listing_engine, monkeypatch)
    service = NetworkListingService(listing_repository=listings, intake_repository=intakes,
        seed_fixtures=False, tenant_id=TENANT)
    captured = deepcopy(service.get_listing(SOURCE))
    current = intakes.get_listing_metadata(SOURCE)
    current.pop("tenantId", None)
    current.pop("heatZoneId", None)
    intakes.save_listing_metadata(SOURCE, current)
    captured["rentPerMonth"] = 58000.0
    service.save_listing(captured)
    saved = intakes.get_listing_metadata(SOURCE)
    assert "tenantId" not in saved and "heatZoneId" not in saved
    assert listings.get_listing(SOURCE).rent_amount == 58000.0


def test_actual_new_manual_listing_initializes_its_recorded_creation_scope(listing_engine, monkeypatch):
    from tests.unit.test_operator_manual_listing_provenance import _create, _intake

    _, listings, intakes = _wired_resolver(listing_engine, monkeypatch)
    original_ids = {row.listing_id for row in listings.list_listings()}
    intake = _intake(heatZoneId="00000000-0000-0000-0000-000000000904")
    intakes.save_intake(intake)
    service = NetworkListingService(listing_repository=listings, intake_repository=intakes,
        seed_fixtures=False, tenant_id=TENANT)
    result = _create(service, intake)
    listing_id = result["matchResult"]["targetListingId"]
    assert listing_id not in original_ids and listings.get_listing(listing_id) is not None
    saved = intakes.get_listing_metadata(listing_id)
    assert saved["tenantId"] == TENANT and saved["heatZoneId"] == intake["heatZoneId"]
