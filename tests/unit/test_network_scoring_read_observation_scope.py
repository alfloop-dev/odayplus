"""Per-refresh metadata observation without caching any production authority.

SQLite producer tests exercise actual durable records. Positive model states
use an explicit in-process port double, never production model admission.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from models.shared_ml.current_model_admission import CurrentModelAdmissionPort
from models.shared_ml.production_runtime import (
    ProductionModelApprovalError,
    ProductionModelLineageError,
    ProductionModelRegistryError,
)
from modules.listing.application.candidate_score_context import (
    CandidateScoreContextRead,
    CandidateScoreContextReader,
    unverified_current_admission,
)
from modules.listing.domain.models import ListingDedupKey
from modules.opsboard.application.network_scoring import (
    NetworkScoringRuntimeUnavailable,
    NetworkScoringService,
)
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import (
    DurableListingRepository,
    DurableSiteScoreRepository,
)
from tests.unit.test_network_candidate_report_projection import (
    TENANT,
    ReadOnlyRuntime,
    _draft,
    _report,
)


class ExplicitSyntheticModelPort(CurrentModelAdmissionPort):
    """Counting local double; no registry reader, SQL authority or inference."""

    def __init__(self, engine):
        super().__init__(runtime=ReadOnlyRuntime(), engine=engine, tenant_id=TENANT,
            registry_repository=SimpleNamespace(_store=SimpleNamespace(tenant_id=TENANT)))
        self.calls = 0
        self.failure = None

    def read(self, *, service, expected_feature_schema_version):
        self.calls += 1
        assert service == "sitescore"
        assert expected_feature_schema_version
        if self.failure is not None:
            raise self.failure
        return {"explicitSyntheticLocalObservation": True}


class ExplicitSyntheticContextReader:
    def __init__(self, engine):
        self.engine = engine
        self.tenant_id = TENANT
        self.reads = []
        self.on_read = lambda candidate: None

    def read(self, candidate):
        self.reads.append(candidate.candidate_site.candidate_site_id)
        self.on_read(candidate)
        result = unverified_current_admission(candidate, "EXPLICIT_SYNTHETIC_CONTEXT")
        # Keep distinct candidate facts; the global model observation must not
        # overwrite candidate-specific source, PIT or current-input refusals.
        result["source"] = {"state": "blocked", "reasonCode": f"SOURCE_{len(self.reads)}"}
        result["pit"] = {"state": "unverified", "reasonCode": f"PIT_{len(self.reads)}"}
        result["currentInputs"] = {"state": "blocked", "reasonCode": f"INPUT_{len(self.reads)}"}
        return CandidateScoreContextRead(result)


class Listings:
    tenant_id = TENANT

    def __init__(self, size):
        self.records = [_draft(f"scoped-observation-{index}") for index in range(size)]
        self.calls = 0

    def list_candidates(self):
        self.calls += 1
        return deepcopy(self.records)


def synthetic_service(size=10, *, defer=True):
    engine = object()
    listings = Listings(size)
    reader = ExplicitSyntheticContextReader(engine)
    observer = ExplicitSyntheticModelPort(engine)
    service = NetworkScoringService(seed_fixtures=False, require_canonical=True,
        listing_repository=listings, sitescore_repository=SimpleNamespace(list_latest=lambda: []),
        model_runtime=ReadOnlyRuntime(), tenant_id=TENANT, current_context_reader=reader,
        current_model_admission=observer, defer_canonical_refresh=defer)
    return service, listings, reader, observer


@pytest.mark.parametrize("size", [1, 10, 50])
def test_each_refresh_observes_same_model_once_and_every_candidate_separately(size):
    service, listings, reader, observer = synthetic_service(size)
    assert listings.calls == observer.calls == 0
    result = service.snapshot()
    assert len(result["candidates"]) == size
    assert listings.calls == observer.calls == 1
    assert reader.reads == [record.candidate_site.candidate_site_id for record in listings.records]
    for index, candidate in enumerate(result["candidates"], 1):
        admission = candidate["currentAdmission"]
        assert admission["model"] == {"state": "verified", "reasonCode": None}
        assert admission["source"]["reasonCode"] == f"SOURCE_{index}"
        assert admission["pit"]["reasonCode"] == f"PIT_{index}"
        assert admission["currentInputs"]["reasonCode"] == f"INPUT_{index}"
    result["candidates"][0]["currentAdmission"]["model"]["state"] = "tampered"
    assert all(row["currentAdmission"]["model"]["state"] == "verified"
               for row in service._candidates)


@pytest.mark.parametrize("failure,state", [
    (ProductionModelApprovalError("withdrawn"), "blocked"),
    (ProductionModelLineageError("changed"), "blocked"),
    (ProductionModelRegistryError("offline"), "unverified"),
    (ConnectionError("unknown failure"), "unverified"),
])
def test_withdrawal_between_requests_is_fresh_and_never_cached(failure, state):
    service, _, reader, observer = synthetic_service(10)
    assert all(row["currentAdmission"]["model"]["state"] == "verified"
               for row in service.snapshot()["candidates"])
    observer.failure = failure
    second = service.snapshot()
    assert observer.calls == 2
    assert len(reader.reads) == 20
    assert all(row["currentAdmission"]["model"]["state"] == state for row in second["candidates"])
    observer.failure = None
    assert all(row["currentAdmission"]["model"]["state"] == "verified"
               for row in service.snapshot()["candidates"])
    assert observer.calls == 3


def test_model_withdrawn_during_candidate_reads_is_observed_after_all_rows():
    service, _, reader, observer = synthetic_service(50)
    def withdraw_on_last(_candidate):
        if len(reader.reads) == 50:
            observer.failure = ProductionModelApprovalError("withdrawn before final model observation")
    reader.on_read = withdraw_on_last
    result = service.snapshot()
    assert observer.calls == 1
    assert all(row["currentAdmission"]["model"]["state"] == "blocked" for row in result["candidates"])


@pytest.mark.parametrize("mutation", ["foreign-port-tenant", "foreign-registry-tenant", "foreign-reader-tenant", "other-engine", "not-port"])
def test_shared_model_observation_keeps_original_binding_refusals(mutation):
    service, _, reader, observer = synthetic_service()
    if mutation == "foreign-port-tenant":
        observer.tenant_id = "foreign-tenant"
    elif mutation == "foreign-registry-tenant":
        observer.registry_repository._store.tenant_id = "foreign-tenant"
    elif mutation == "foreign-reader-tenant":
        reader.tenant_id = "foreign-tenant"
    elif mutation == "other-engine":
        observer.engine = object()
    else:
        service._current_model_admission = SimpleNamespace(read=lambda **_kwargs: pytest.fail("non-native port"))
    result = service.snapshot()
    assert observer.calls == 0
    assert all(row["currentAdmission"]["model"]["state"] == "unverified" for row in result["candidates"])


def test_lazy_compare_and_export_load_canonical_rows_before_using_persisted_cache():
    service, listings, reader, observer = synthetic_service(1)
    service._candidates = [{"id": "persisted-stale-id"}]
    result = service.set_compare_set(candidate_ids=[listings.records[0].candidate_site.candidate_site_id],
        actor_role_id="local-only", actor_name=None, correlation_id=None)
    assert result["compareSet"] == [listings.records[0].candidate_site.candidate_site_id]
    assert observer.calls == listings.calls == len(reader.reads) == 1
    assert service.export_state()["candidates"][0]["id"] != "persisted-stale-id"
    assert observer.calls == 1
    fresh, fresh_listings, _, fresh_observer = synthetic_service(1)
    assert fresh.export_state()["candidates"][0]["id"] == fresh_listings.records[0].candidate_site.candidate_site_id
    assert fresh_observer.calls == 1


def test_lazy_candidate_lookup_refreshes_before_any_canonical_legacy_command():
    service, listings, reader, observer = synthetic_service(1)
    assert service._candidate(listings.records[0].candidate_site.candidate_site_id)["id"] == listings.records[0].candidate_site.candidate_site_id
    assert observer.calls == listings.calls == len(reader.reads) == 1


@pytest.mark.parametrize("command", ["single", "batch"])
def test_lazy_commands_preserve_conflicting_report_refusal_before_owner_is_called(command):
    listings = Listings(1)
    record = listings.records[0]
    report = _report(record.candidate_site.candidate_site_id, "local-recorded-model")
    owner_calls = []
    owner = SimpleNamespace(create=lambda *_args, **_kwargs: owner_calls.append("called"))
    service = NetworkScoringService(seed_fixtures=False, require_canonical=True,
        listing_repository=listings, sitescore_repository=SimpleNamespace(list_latest=lambda: [report, report]),
        model_runtime=ReadOnlyRuntime(), tenant_id=TENANT, execution_service=owner,
        defer_canonical_refresh=True)
    with pytest.raises(NetworkScoringRuntimeUnavailable) as failure:
        if command == "single":
            service.score_candidate(candidate_id=record.candidate_site.candidate_site_id,
                actor_role_id="local-only", actor_name=None, idempotency_key=None,
                correlation_id=None, execution_command=object(), principal=object())
        else:
            service.score_batch(candidate_ids=[record.candidate_site.candidate_site_id],
                actor_role_id="local-only", actor_name=None, idempotency_key=None, correlation_id=None)
    assert failure.value.code == "SITESCORE_CANONICAL_REPORT_CONFLICT"
    assert listings.calls == 1
    assert owner_calls == []


@pytest.mark.parametrize("missing", ["listing_repository", "sitescore_repository", "model_runtime"])
def test_lazy_observation_does_not_defer_required_runtime_wiring_guard(missing):
    dependencies = {"listing_repository": Listings(1),
                    "sitescore_repository": SimpleNamespace(list_latest=lambda: []),
                    "model_runtime": ReadOnlyRuntime()}
    dependencies[missing] = None
    with pytest.raises(NetworkScoringRuntimeUnavailable) as failure:
        NetworkScoringService(seed_fixtures=False, require_canonical=True, tenant_id=TENANT,
            defer_canonical_refresh=True, **dependencies)
    assert failure.value.code == "SITESCORE_CANONICAL_DEPENDENCY_UNAVAILABLE"


def test_empty_snapshot_does_not_contact_model_and_reset_observes_only_once():
    empty, _, _, observer = synthetic_service(0)
    assert empty.snapshot()["candidates"] == []
    assert observer.calls == 0
    service, listings, _, observer = synthetic_service(10)
    assert len(service.reset()["candidates"]) == 10
    assert observer.calls == listings.calls == 1


def test_real_sqlite_production_authority_stays_unverified_and_immutable_reports_survive(tmp_path):
    engine = SqliteEngine(tmp_path / "owned-read-observations.sqlite3")
    store = TenantScopedDocumentStore(SqliteDocumentStore(engine), TENANT)
    listings, reports = DurableListingRepository(store), DurableSiteScoreRepository(store)
    try:
        old_reports = []
        for index in range(50):
            record = _draft(f"owned-durable-{index}")
            listings.save_listing(record.listing, record.address, ListingDedupKey(
                f"source-{index}", f"property-{index}", record.address.normalized_address, 40000, 24))
            listings.save_candidate(record)
            old_reports.append(reports.save_report(_report(record.candidate_site.candidate_site_id, "recorded-v1")))
            reports.save_report(_report(record.candidate_site.candidate_site_id, "recorded-v2", run="2"))
        before = [dict(row) for row in engine.query("SELECT * FROM durable_documents ORDER BY collection,doc_id")]
        runtime = ReadOnlyRuntime()
        port = CurrentModelAdmissionPort(runtime=runtime, engine=engine, tenant_id=TENANT,
            registry_repository=SimpleNamespace(_store=store))
        context = CandidateScoreContextReader(tenant_id=TENANT, repository=listings,
            engine=engine, store=SimpleNamespace(_engine=engine))
        sql = []
        engine._conn.set_trace_callback(sql.append)
        service = NetworkScoringService(seed_fixtures=False, require_canonical=True, tenant_id=TENANT,
            listing_repository=listings, sitescore_repository=reports, model_runtime=runtime,
            current_context_reader=context, current_model_admission=port, defer_canonical_refresh=True)
        result = service.snapshot()
        engine._conn.set_trace_callback(None)
        assert len(sql) == 2  # actual candidate read + grouped latest-report read
        assert len(result["candidates"]) == len(result["scorecards"]) == 50
        assert all(card["reportVersion"] == 2 and card["modelVersion"] == "recorded-v2" for card in result["scorecards"])
        assert all(row["currentAdmission"]["model"]["state"] == "unverified" for row in result["candidates"])
        assert runtime.calls == 0
        assert [dict(row) for row in engine.query("SELECT * FROM durable_documents ORDER BY collection,doc_id")] == before
        assert all(reports.get_report(report.report_id) == report for report in old_reports)
    finally:
        engine.close()
