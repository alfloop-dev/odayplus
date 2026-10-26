"""Canonical read provenance, using synthetic reports and isolated local stores.

Report availability is historical evidence, not current inference admission.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_scoring import create_network_scoring_sub_router
from modules.listing.domain.models import CandidateSiteDraft
from modules.listing.infrastructure.repositories import InMemoryListingRepository
from modules.opsboard.application.network_scoring import NetworkScoringService
from modules.sitescore.domain.scoring import Interval, SiteScoreRecommendation, SiteScoreReport
from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository
from shared.auth import Principal, Role, Scope
from shared.domain import AddressLocation, CandidateSite, Listing
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import (
    DurableListingRepository,
    DurableSiteScoreRepository,
)

TENANT = "synthetic-projection-tenant-a"
AT = datetime(2026, 10, 13, tzinfo=UTC)


class ReadOnlyRuntime:
    def __init__(self):
        self.calls = 0

    def infer(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("A report projection must never infer")


def _draft(candidate_id, *, tenant=TENANT, model_version="stale-cached-model"):
    address = AddressLocation(
        address_id=f"address-{candidate_id}", normalized_address=f"Synthetic {candidate_id}",
        latitude=25.03, longitude=121.56, geocode_confidence=0.9,
    )
    listing = Listing(
        listing_id=f"listing-{candidate_id}", address_id=address.address_id,
        rent_amount=40000, area_ping=24, floor="1F", tenant_id=tenant,
        snapshot_id=f"current-input-{candidate_id}",
    )
    return CandidateSiteDraft(
        listing=listing, address=address,
        candidate_site=CandidateSite(
            candidate_site_id=candidate_id, listing_id=listing.listing_id,
            address_id=address.address_id, target_format_code="synthetic-format", created_at=AT,
        ),
        heat_zone_id="synthetic-zone", model_version=model_version,
        # A stale cached scalar must not become the report's business score.
        score=99, recommendation="GO",
    )


def _report(candidate_id, model_version, *, run="1"):
    interval = Interval(100000, 150000, 200000)
    return SiteScoreReport(
        candidate_site_id=candidate_id, sitescore_run_id=f"run-{candidate_id}-{run}",
        target_format_code="synthetic-format", recommendation=SiteScoreRecommendation.INVESTIGATE,
        m1=interval, m3=interval, m6=interval, m12=interval,
        payback_period=Interval(24, 36, 48), payback_p50_months=36,
        rent_reasonableness=0.6, cannibalization_risk=0.2, comparable_stores=3,
        key_positive_factors=(), key_negative_factors=(), confidence=0.91,
        model_version=model_version, feature_version="synthetic-feature-v1",
        feature_snapshot_time=AT, prediction_origin_time=AT, scored_at=AT,
        source_snapshot_ids=(f"historical-report-input-{candidate_id}",),
    )


def _listing_repo(*drafts):
    repo = InMemoryListingRepository()
    for draft in drafts:
        repo.save_candidate(draft)
    return repo


def test_canonical_http_preserves_partial_payback_and_text_financial_contract():
    candidate_id = "candidate-partial-finance"
    reports = InMemorySiteScoreRepository()
    saved = reports.save_report(replace(_report(candidate_id, "recorded-partial-model"),
        payback_period=None, payback_p50_months=None, rent_reasonableness=None,
        monthly_rent=40000, key_negative_factors=("financial_inputs_unavailable",)))
    service = _service(_listing_repo(_draft(candidate_id)), reports, ReadOnlyRuntime())
    with _client(lambda: service) as client:
        response = client.get("/network-scoring")
    assert response.status_code == 200
    card = response.json()["scorecards"][0]
    assert card["reportId"] == saved.report_id
    assert card["payback"] is None and card["capex"] is None
    assert card["rentAssumption"] == "40000"
    assert card["revenuePath"]["m12"] == saved.m12.to_dict()
    assert card["conditions"] == ["financial_inputs_unavailable"]


def test_recorded_complete_financial_numbers_keep_existing_text_dto():
    saved = replace(_report("candidate-complete-finance", "recorded-complete-model"),
        buildout_capex=5546000, gross_margin_ratio=0.6, monthly_rent=40000)
    card = NetworkScoringService._scorecard_from_report(saved)
    assert card["capex"] == "5546000" and card["rentAssumption"] == "40000"
    assert card["payback"] == 36


def _service(listings, reports, runtime=None, *, tenant=TENANT):
    return NetworkScoringService(
        seed_fixtures=False, require_canonical=True, tenant_id=tenant,
        listing_repository=listings, sitescore_repository=reports,
        model_runtime=runtime or ReadOnlyRuntime(),
    )


def _client(service_provider):
    # This small HTTP harness checks serialization/503 translation. Permission
    # enforcement is covered separately by the existing API contracts.
    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):
        request.state.operator_principal = Principal("synthetic-reader")
        return await call_next(request)

    app.include_router(create_network_scoring_sub_router(
        NetworkScoringService(seed_fixtures=False),
        require_view_permission_fn=lambda: None, require_write_permission_fn=lambda: None,
        service_resolver=lambda request: service_provider(), allow_reset=False,
    ))
    return TestClient(app)


def test_each_candidate_uses_own_latest_committed_model_without_inference_or_score_fabrication():
    listings = _listing_repo(_draft("candidate-B"), _draft("candidate-A"))
    reports = InMemorySiteScoreRepository()
    reports.save_report(_report("candidate-B", "actual-model-B-v1"))
    latest_b = reports.save_report(_report("candidate-B", "actual-model-B-v2", run="2"))
    latest_a = reports.save_report(_report("candidate-A", "actual-model-A-v1"))
    before = deepcopy((listings.list_candidates(), reports.history("candidate-B")))
    runtime = ReadOnlyRuntime()
    service = _service(listings, reports, runtime)
    with _client(lambda: service) as client:
        response = client.get("/network-scoring")
    assert response.status_code == 200
    body = response.json()
    candidates = {row["id"]: row for row in body["candidates"]}
    cards = {row["id"]: row for row in body["scorecards"]}
    for candidate_id, report in [("candidate-A", latest_a), ("candidate-B", latest_b)]:
        assert candidates[candidate_id]["modelVersion"] == report.model_version
        assert candidates[candidate_id]["score"] is None
        assert cards[candidate_id]["reportId"] == report.report_id
        assert cards[candidate_id]["reportVersion"] == report.report_version
        assert cards[candidate_id]["sitescoreRunId"] == report.sitescore_run_id
        assert cards[candidate_id]["confidence"] == 0.91
        # Keep current candidate inputs and immutable report inputs distinct.
        assert candidates[candidate_id]["datasetSnapshotId"] == f"current-input-{candidate_id}"
        assert cards[candidate_id]["datasetSnapshotId"] == f"historical-report-input-{candidate_id}"
    assert latest_b.report_version == 2
    assert runtime.calls == 0
    assert (listings.list_candidates(), reports.history("candidate-B")) == before
    assert service.snapshot()["auditEvents"] == []


@pytest.mark.parametrize(("site_status", "recommendation"), [
    ("SCORING_FAILED", SiteScoreRecommendation.GO),
    ("SCORING_QUEUED", SiteScoreRecommendation.REJECT),
    ("approved", SiteScoreRecommendation.REJECT),
    ("opened", SiteScoreRecommendation.GO),
    (None, SiteScoreRecommendation.GO),
])
def test_canonical_http_preserves_persisted_lifecycle_independently_of_model_outcome(tmp_path, site_status, recommendation):
    engine = SqliteEngine(tmp_path / "synthetic-candidate-state.sqlite3")
    try:
        scoped = TenantScopedDocumentStore(SqliteDocumentStore(engine), TENANT)
        listings, reports = DurableListingRepository(scoped), DurableSiteScoreRepository(scoped)
        draft = _draft("candidate-state")
        listings.save_candidate(replace(draft, candidate_site=replace(draft.candidate_site, site_status=site_status)))
        report = reports.save_report(replace(_report("candidate-state", "actual-committed-model"), recommendation=recommendation))
        before = engine.query("SELECT collection,doc_id,seq,data FROM durable_documents ORDER BY collection,doc_id")
        runtime = ReadOnlyRuntime()
        service = _service(listings, reports, runtime)
        with _client(lambda: service) as client:
            response = client.get("/network-scoring")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source"] == "canonical"
        assert body["candidates"][0]["canonicalSiteStatus"] == site_status
        assert body["candidates"][0]["stage"] == (site_status or "unknown")
        assert body["scorecards"][0]["reportId"] == report.report_id
        assert body["scorecards"][0]["recommendation"] == recommendation.value
        assert runtime.calls == 0
        assert engine.query("SELECT collection,doc_id,seq,data FROM durable_documents ORDER BY collection,doc_id") == before
    finally:
        engine.close()


def test_missing_own_report_does_not_borrow_cached_or_other_candidate_model():
    reports = InMemorySiteScoreRepository()
    reports.save_report(_report("candidate-A", "other-candidate-model"))
    runtime = ReadOnlyRuntime()
    body = _service(_listing_repo(_draft("candidate-A"), _draft("candidate-B")), reports, runtime).snapshot()
    candidate = next(row for row in body["candidates"] if row["id"] == "candidate-B")
    assert candidate["modelVersion"] is None
    assert candidate["scored"] is False
    assert candidate["score"] is None and candidate["recommendation"] is None
    assert runtime.calls == 0


@pytest.mark.parametrize("model_version", ["", "   "])
def test_missing_report_model_is_unavailable_without_replacing_report_history(model_version):
    reports = InMemorySiteScoreRepository()
    report = reports.save_report(_report("candidate-A", model_version))
    body = _service(_listing_repo(_draft("candidate-A")), reports).snapshot()
    assert body["candidates"][0]["modelVersion"] is None
    assert body["candidates"][0]["scored"] is True
    assert body["candidates"][0]["score"] is None
    assert body["scorecards"][0]["reportId"] == report.report_id
    assert reports.get_report(report.report_id) == report


@pytest.mark.parametrize("corruption", ["duplicate-candidate", "reused-report-id", "empty-report-id", "invalid-version", "empty-run-id"])
def test_conflicting_report_lineage_returns_unavailable_not_an_arbitrary_model(corruption):
    first = _report("candidate-A", "first-model")
    second = _report("candidate-B", "second-model")
    if corruption == "duplicate-candidate":
        second = replace(second, candidate_site_id="candidate-A")
    elif corruption == "reused-report-id":
        second = replace(second, report_id=first.report_id)
    elif corruption == "empty-report-id":
        second = replace(second, report_id="")
    elif corruption == "invalid-version":
        second = replace(second, report_version=0)
    else:
        second = replace(second, sitescore_run_id="")
    reports = SimpleNamespace(list_latest=lambda: [first, second])
    runtime = ReadOnlyRuntime()
    def provider():
        return _service(_listing_repo(_draft("candidate-A"), _draft("candidate-B")), reports, runtime)

    with _client(provider) as client:
        response = client.get("/network-scoring")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SITESCORE_CANONICAL_REPORT_CONFLICT"
    assert "first-model" not in response.text and "second-model" not in response.text
    assert runtime.calls == 0


def test_orphan_report_is_not_a_candidate_model_or_exposed_scorecard():
    reports = InMemorySiteScoreRepository()
    reports.save_report(_report("not-in-listing-repository", "unrelated-model"))
    body = _service(_listing_repo(_draft("candidate-A")), reports).snapshot()
    assert body["candidates"][0]["modelVersion"] is None
    assert body["scorecards"] == []
    assert body["counts"]["scored"] == 0


def test_restricted_reader_cannot_borrow_an_excluded_candidate_model():
    reports = InMemorySiteScoreRepository()
    reports.save_report(_report("candidate-B", "excluded-model"))
    principal = Principal("synthetic-scoped-reader", roles=frozenset({Role.OPERATOR_VIEWER}),
                          scope=Scope(tenant_id=TENANT, brand_ids=frozenset({"allowed-brand"})))
    body = _service(_listing_repo(_draft("candidate-A"), _draft("candidate-B")), reports).snapshot(
        principal=principal, scope_snapshot={"listings": [
            {"id": "listing-candidate-A", "tenantId": TENANT, "brandId": "allowed-brand"},
            {"id": "listing-candidate-B", "tenantId": TENANT, "brandId": "excluded-brand"},
        ]},
    )
    assert [row["id"] for row in body["candidates"]] == ["candidate-A"]
    assert body["candidates"][0]["modelVersion"] is None
    assert body["scorecards"] == [] and body["modelVersion"] is None


def test_same_candidate_id_in_two_tenants_reads_own_report_after_restart_without_writes(tmp_path):
    path = tmp_path / "synthetic-model-projection.sqlite3"
    engine = SqliteEngine(path)
    expected = {}
    try:
        store = SqliteDocumentStore(engine)
        for tenant in [TENANT, "synthetic-projection-tenant-b"]:
            scoped = TenantScopedDocumentStore(store, tenant)
            DurableListingRepository(scoped).save_candidate(_draft("shared-candidate-id", tenant=tenant))
            expected[tenant] = DurableSiteScoreRepository(scoped).save_report(
                _report("shared-candidate-id", f"{tenant}-actual-model"))
    finally:
        engine.close()
    engine = SqliteEngine(path)
    try:
        before = engine.query("SELECT collection, doc_id, seq, data FROM durable_documents ORDER BY collection, doc_id")
        for tenant, report in expected.items():
            scoped = TenantScopedDocumentStore(SqliteDocumentStore(engine), tenant)
            runtime = ReadOnlyRuntime()
            body = _service(DurableListingRepository(scoped), DurableSiteScoreRepository(scoped), runtime, tenant=tenant).snapshot()
            assert body["candidates"][0]["modelVersion"] == report.model_version
            assert body["scorecards"][0]["reportId"] == report.report_id
            assert body["scorecards"][0]["sitescoreRunId"] == report.sitescore_run_id
            assert body["candidates"][0]["score"] is None
            assert runtime.calls == 0
        after = engine.query("SELECT collection, doc_id, seq, data FROM durable_documents ORDER BY collection, doc_id")
        assert after == before
    finally:
        engine.close()
