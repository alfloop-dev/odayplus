"""Canonical candidate checklist reads recorded evidence without adding admission rules."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_scoring import create_network_scoring_sub_router
from modules.listing.domain.models import CandidateSiteDraft
from modules.opsboard.application import network_scoring
from modules.opsboard.application.network_scoring import (
    NetworkScoringGateError,
    NetworkScoringService,
)
from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository
from shared.auth import DataClassification, Principal, Role, Scope
from shared.domain.models import AddressLocation, CandidateSite, Listing
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import (
    DurableListingRepository,
    DurableSiteScoreRepository,
)

TENANT = "synthetic-evidence-tenant"
AT = datetime(2026, 10, 13, tzinfo=UTC)
ABSENT = object()


def _draft(*, hard_rules: Any = ABSENT, candidate_id: str = "synthetic-candidate") -> CandidateSiteDraft:
    listing = Listing(
        listing_id="synthetic-listing", address_id="synthetic-address",
        rent_amount=54321.5, currency="TWD", area_ping=23.75, floor="1F",
        snapshot_id="synthetic-source-snapshot", confidence=0.0, tenant_id=TENANT,
    )
    if hard_rules is not ABSENT:
        object.__setattr__(listing, "hard_rule_failures", hard_rules)
    return CandidateSiteDraft(
        listing=listing,
        address=AddressLocation(
            address_id="synthetic-address", raw_address="synthetic raw address",
            normalized_address="synthetic normalized address", district="synthetic district",
            latitude=25.0, longitude=121.0, geocode_confidence=0.93, tenant_id=TENANT,
        ),
        candidate_site=CandidateSite(
            candidate_site_id=candidate_id, listing_id=listing.listing_id,
            address_id=listing.address_id, created_at=AT,
        ),
        heat_zone_id="synthetic-zone",
    )


class _ReadRepository:
    tenant_id = TENANT

    def __init__(self, drafts: list[CandidateSiteDraft]) -> None:
        self.drafts = drafts

    def list_candidates(self) -> list[CandidateSiteDraft]:
        return self.drafts


class _UnusedRuntime:
    calls = 0

    def infer(self, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("candidate evidence reads must never run inference")


def _service(draft: CandidateSiteDraft) -> NetworkScoringService:
    return NetworkScoringService(
        seed_fixtures=False, require_canonical=True,
        listing_repository=_ReadRepository([draft]),
        sitescore_repository=InMemorySiteScoreRepository(), model_runtime=_UnusedRuntime(),
    )


def _row(draft: CandidateSiteDraft, **kwargs: Any) -> dict[str, Any]:
    return _service(draft).snapshot(**kwargs)["candidates"][0]


def _principal(clearance: DataClassification, *, viewer: bool = False) -> Principal:
    return Principal(
        subject_id="synthetic-trusted-reader",
        roles=frozenset({Role.OPERATOR_VIEWER if viewer else Role.EXPANSION_USER}),
        scope=Scope(tenant_id=TENANT, clearance=clearance),
    )


def test_canonical_fields_have_exact_recorded_values_units_and_provenance() -> None:
    draft = _draft(hard_rules=[])
    row = _row(draft)
    assert row["readEvidence"] == {
        "source": "canonical", "candidateId": "synthetic-candidate",
        "listingId": "synthetic-listing", "addressId": "synthetic-address",
        "datasetSnapshotId": "synthetic-source-snapshot",
        "fields": {
            "address": {"value": "synthetic normalized address", "unit": None,
                        "availability": "recorded", "reason": None,
                        "provenance": {"recordType": "AddressLocation", "recordId": "synthetic-address",
                                       "fieldPath": "normalized_address", "snapshotId": None}},
            "geocode": {"value": 0.93, "unit": "ratio", "availability": "recorded", "reason": None,
                        "provenance": {"recordType": "AddressLocation", "recordId": "synthetic-address",
                                       "fieldPath": "geocode_confidence", "snapshotId": None}},
            "rent": {"value": 54321.5, "unit": "TWD", "availability": "recorded", "reason": None,
                     "provenance": {"recordType": "Listing", "recordId": "synthetic-listing",
                                    "fieldPath": "rent_amount", "snapshotId": "synthetic-source-snapshot"}},
            "area": {"value": 23.75, "unit": "ping", "availability": "recorded", "reason": None,
                     "provenance": {"recordType": "Listing", "recordId": "synthetic-listing",
                                    "fieldPath": "area_ping", "snapshotId": "synthetic-source-snapshot"}},
            "floor": {"value": "1F", "unit": None, "availability": "recorded", "reason": None,
                      "provenance": {"recordType": "Listing", "recordId": "synthetic-listing",
                                     "fieldPath": "floor", "snapshotId": "synthetic-source-snapshot"}},
            "hardRule": {"value": [], "unit": None, "availability": "recorded", "reason": None,
                         "provenance": {"recordType": "Listing", "recordId": "synthetic-listing",
                                        "fieldPath": "hard_rule_failures", "snapshotId": "synthetic-source-snapshot"}},
            "sourceConfidence": {"value": 0.0, "unit": "ratio", "availability": "recorded", "reason": None,
                                 "provenance": {"recordType": "Listing", "recordId": "synthetic-listing",
                                                "fieldPath": "confidence", "snapshotId": "synthetic-source-snapshot",
                                                "measurementStatus": "measured"}},
        },
    }
    assert row["score"] is None
    assert row["gate"]["totalCount"] == 6
    assert [check["key"] for check in row["gate"]["checks"]] == [
        "address", "geocode", "rent", "area", "floor", "hardRule",
    ]


@pytest.mark.parametrize("hard_rules, availability, value", [
    (ABSENT, "unmeasured", None), (None, "unmeasured", None),
    ([], "recorded", []), (["recorded-floor-failure"], "recorded", ["recorded-floor-failure"]),
    ("not-a-verdict", "invalid", None), ([42], "invalid", None),
])
def test_hard_rule_read_does_not_treat_default_feasibility_flags_as_measurement(
    hard_rules: Any, availability: str, value: Any,
) -> None:
    row = _row(_draft(hard_rules=hard_rules))
    field = row["readEvidence"]["fields"]["hardRule"]
    assert field["availability"] == availability
    assert field["value"] == value
    # Only a recorded empty verdict satisfies the existing hard-rule dimension.
    assert row["gate"]["passed"] is (availability == "recorded" and value == [])
    assert row["gate"]["totalCount"] == 6


@pytest.mark.parametrize("status, schema, confidence, expected, reason", [
    (None, "v2", 0.0, 0.0, None), ("unmeasured", "v2", 0.73, None, "UNMEASURED"),
    (None, "v1", 1.0, None, "LEGACY_UNKNOWN"), (None, "v2", None, None, "UNMEASURED"),
])
def test_source_confidence_preserves_canonical_measurement_semantics(
    status: str | None, schema: str, confidence: float | None, expected: float | None,
    reason: str | None,
) -> None:
    draft = _draft(hard_rules=[])
    draft.listing = replace(draft.listing, confidence=confidence, confidence_status=status,
                            measurement_schema_version=schema)
    # dataclasses.replace drops extension attributes; preserve this test's
    # explicit local recorded pass while changing only confidence evidence.
    object.__setattr__(draft.listing, "hard_rule_failures", [])
    row = _row(draft)
    field = row["readEvidence"]["fields"]["sourceConfidence"]
    assert field["value"] == expected
    assert field["availability"] == ("recorded" if expected is not None else "unmeasured")
    assert field["reason"] == reason
    assert field["provenance"]["measurementStatus"] == draft.listing.effective_confidence_provenance
    assert row["gate"]["passed"] is True
    assert row["gate"]["totalCount"] == 6


@pytest.mark.parametrize("attribute, invalid", [
    ("rent_amount", -1.0), ("area_ping", float("inf")), ("confidence", float("nan")),
])
def test_invalid_numbers_are_not_published_as_recorded_json_values(attribute: str, invalid: float) -> None:
    draft = _draft(hard_rules=[])
    draft.listing = replace(draft.listing, **{attribute: invalid})
    key = {"rent_amount": "rent", "area_ping": "area", "confidence": "sourceConfidence"}[attribute]
    field = _row(draft)["readEvidence"]["fields"][key]
    assert field["availability"] == "invalid"
    assert field["value"] is None
    json.dumps(field, allow_nan=False)


def test_default_zero_amounts_empty_floor_and_missing_snapshot_remain_unmeasured() -> None:
    draft = _draft()
    draft.listing = replace(draft.listing, rent_amount=0.0, area_ping=0.0, floor="", snapshot_id="")
    fields = _row(draft)["readEvidence"]["fields"]
    for key in ("rent", "area"):
        assert fields[key]["value"] is None
        assert fields[key]["availability"] == "unmeasured"
        assert fields[key]["reason"] == "DEFAULT_OR_UNMEASURED"
        assert fields[key]["provenance"]["snapshotId"] is None
    assert fields["floor"]["value"] is None
    assert fields["floor"]["availability"] == "unmeasured"


@pytest.mark.parametrize("attribute, value, availability", [
    ("rent_amount", None, "unmeasured"), ("area_ping", None, "unmeasured"),
    ("rent_amount", "not-a-measurement", "invalid"), ("area_ping", True, "invalid"),
])
def test_nullable_or_malformed_amounts_read_truthfully_and_fail_existing_presence_gate(
    attribute: str, value: Any, availability: str,
) -> None:
    draft = _draft(hard_rules=[])
    draft.listing = replace(draft.listing, **{attribute: value})
    row = _row(draft)
    key = "rent" if attribute == "rent_amount" else "area"
    assert row["readEvidence"]["fields"][key]["availability"] == availability
    assert row["readEvidence"]["fields"][key]["value"] is None
    assert row["gate"]["passed"] is False
    assert row["gate"]["totalCount"] == 6
    assert next(check for check in row["gate"]["checks"] if check["key"] == key)["state"] == "fail"


def test_raw_address_fallback_names_the_actual_recorded_field() -> None:
    draft = _draft()
    draft.address = replace(draft.address, normalized_address="")
    field = _row(draft)["readEvidence"]["fields"]["address"]
    assert field["value"] == "synthetic raw address"
    assert field["provenance"]["fieldPath"] == "raw_address"


@pytest.mark.parametrize("confidence, passed", [(None, False), (0.0, False), (0.8, True)])
def test_none_safe_geocode_and_zero_coordinates_preserve_existing_threshold(
    confidence: float | None, passed: bool,
) -> None:
    draft = _draft(hard_rules=[])
    draft.address = replace(draft.address, latitude=0.0, longitude=0.0, geocode_confidence=confidence)
    row = _row(draft)
    assert row["gate"]["passed"] is passed
    assert row["gate"]["totalCount"] == 6
    field = row["readEvidence"]["fields"]["geocode"]
    assert field["value"] == confidence
    assert field["availability"] == ("recorded" if confidence is not None else "unmeasured")
    assert "coordinates" not in row["readEvidence"]
    assert "latitude" not in json.dumps(row["readEvidence"])


def test_invalid_geocode_confidence_is_visible_as_invalid_and_does_not_raise() -> None:
    draft = _draft()
    draft.address = replace(draft.address, geocode_confidence="not-a-confidence")
    row = _row(draft)
    field = row["readEvidence"]["fields"]["geocode"]
    assert field["value"] is None
    assert field["availability"] == "invalid"
    assert row["gate"]["passed"] is False
    assert row["gate"]["totalCount"] == 6


@pytest.mark.parametrize("clearance", [DataClassification.PUBLIC, DataClassification.INTERNAL])
def test_existing_listing_masking_covers_evidence_and_canonical_address_aliases(
    clearance: DataClassification,
) -> None:
    row = _row(_draft(hard_rules=[]), read_principal=_principal(clearance))
    fields = row["readEvidence"]["fields"]
    for key in ("address", "rent", "area", "floor"):
        if clearance == DataClassification.PUBLIC:
            assert fields[key]["value"] is None
            assert fields[key]["availability"] == "denied"
            assert fields[key]["reason"] == "FIELD_MASKED"
        else:
            assert fields[key]["availability"] == "recorded"
    assert fields["geocode"]["value"] == 0.93
    if clearance == DataClassification.PUBLIC:
        serialized = json.dumps(row)
        assert "synthetic normalized address" not in serialized
        assert "synthetic raw address" not in serialized
        assert row["address"] is None
        assert row["title"] == "synthetic-candidate"


def test_evidence_projection_is_not_mutated_by_a_lower_clearance_read() -> None:
    draft = _draft(hard_rules=[])
    service = _service(draft)
    public = service.snapshot(read_principal=_principal(DataClassification.PUBLIC))["candidates"][0]
    privileged = service.snapshot(read_principal=_principal(DataClassification.CONFIDENTIAL))["candidates"][0]
    assert public["readEvidence"]["fields"]["rent"]["availability"] == "denied"
    assert privileged["readEvidence"]["fields"]["rent"]["value"] == 54321.5
    assert draft.listing.rent_amount == 54321.5
    assert service._model_runtime.calls == 0


def test_trusted_route_principal_masks_non_viewer_reads_without_applying_viewer_scope() -> None:
    app = FastAPI()
    principal = _principal(DataClassification.PUBLIC)

    def trusted_read(request: Request) -> Principal:
        request.state.operator_principal = principal
        return principal

    app.include_router(create_network_scoring_sub_router(
        _service(_draft(hard_rules=[])), require_view_permission_fn=trusted_read,
        require_write_permission_fn=trusted_read,
    ))
    response = TestClient(app).get("/network-scoring", headers={"x-clearance": "RESTRICTED"})
    assert response.status_code == 200, response.text
    row = response.json()["candidates"][0]
    assert row["readEvidence"]["fields"]["rent"]["availability"] == "denied"
    assert row["address"] is None


def test_cached_score_response_uses_trusted_mask_without_changing_cache_or_running_inference() -> None:
    app = FastAPI()
    principal = _principal(DataClassification.PUBLIC)
    service = _service(_draft(hard_rules=[]))
    cached = {"candidate": service.snapshot()["candidates"][0], "scorecard": {"id": "synthetic-card"}}
    service._idempotency_cache[("score", "synthetic-cache-key")] = deepcopy(cached)

    def trusted_write(request: Request) -> Principal:
        request.state.operator_principal = principal
        return principal

    app.include_router(create_network_scoring_sub_router(
        service, require_view_permission_fn=trusted_write, require_write_permission_fn=trusted_write,
    ))
    client = TestClient(app)
    for _attempt in range(2):
        response = client.post(
            "/network-scoring/candidates/synthetic-candidate/score",
            json={"actorRoleId": "expansionManager"},
            headers={"Idempotency-Key": "synthetic-cache-key", "x-clearance": "RESTRICTED"},
        )
        assert response.status_code == 200, response.text
        row = response.json()["candidate"]
        assert row["address"] is None
        assert row["readEvidence"]["fields"]["rent"]["availability"] == "denied"
        assert "synthetic normalized address" not in response.text
    assert service._idempotency_cache[("score", "synthetic-cache-key")] == cached
    assert service._model_runtime.calls == 0


def test_canonical_reset_response_masks_evidence_without_changing_reset_rules() -> None:
    app = FastAPI()
    principal = _principal(DataClassification.PUBLIC)
    service = _service(_draft(hard_rules=[]))

    def trusted_write(request: Request) -> Principal:
        request.state.operator_principal = principal
        return principal

    app.include_router(create_network_scoring_sub_router(
        service, require_view_permission_fn=trusted_write, require_write_permission_fn=trusted_write,
    ))
    response = TestClient(app).post("/network-scoring/reset")
    assert response.status_code == 200, response.text
    row = response.json()["candidates"][0]
    assert row["readEvidence"]["fields"]["address"]["availability"] == "denied"
    assert row["address"] is None
    assert row["gate"]["totalCount"] == 6
    assert service._model_runtime.calls == 0


def test_viewer_scope_still_filters_excluded_parent_before_evidence_projection() -> None:
    service = _service(_draft(hard_rules=[]))
    principal = _principal(DataClassification.CONFIDENTIAL, viewer=True)
    denied = service.snapshot(principal=principal, read_principal=principal, scope_snapshot={
        "listings": [{"id": "synthetic-listing", "tenantId": "another-synthetic-tenant"}],
    })
    assert denied["candidates"] == []
    assert denied["counts"]["candidates"] == 0


def test_feature_build_occurs_only_for_explicit_eligible_score_with_exact_draft_and_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = _draft(hard_rules=[])
    feature_calls: list[tuple[Any, str]] = []

    def forbidden_feature(actual_draft: Any, *, tenant_id: str) -> Any:
        feature_calls.append((actual_draft, tenant_id))
        raise AssertionError("Request/read may not hydrate or infer worker features")

    class QueuedOwner:
        def create(self, command: Any, *, principal: Any) -> Any:
            assert command.candidate_site_id == draft.candidate_site.candidate_site_id
            assert principal.tenant_id == TENANT
            return {"status": "QUEUED", "result": None, "candidateSiteId": command.candidate_site_id}

    from modules.sitescore.domain.execution import ScoreExecutionCommand
    monkeypatch.setattr(network_scoring, "feature_from_candidate", forbidden_feature)
    service = _service(draft)
    service._execution_service = QueuedOwner()
    snapshot = service.snapshot()
    assert feature_calls == []
    assert "canonicalDraft" not in json.dumps(snapshot)
    assert "canonicalTenantId" not in json.dumps(snapshot)
    result = service.score_candidate(
        candidate_id=draft.candidate_site.candidate_site_id, actor_role_id="expansionManager",
        actor_name=None, idempotency_key=None, correlation_id=None,
        execution_command=ScoreExecutionCommand(draft.candidate_site.candidate_site_id, "synthetic-saved-report", 1, 7,
            "Synthetic queued request", "synthetic-key", "synthetic-correlation"),
        principal=_principal(DataClassification.CONFIDENTIAL),
    )
    assert feature_calls == []
    assert result["executionRequest"]["status"] == "QUEUED"
    assert result["executionRequest"]["result"] is None
    assert service._model_runtime.calls == 0
    assert "canonicalDraft" not in json.dumps(result)
    assert "canonicalTenantId" not in json.dumps(result)


def test_blocked_score_never_builds_a_model_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_feature_build(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("blocked reads and scoring must not build a model feature")

    monkeypatch.setattr(network_scoring, "feature_from_candidate", forbidden_feature_build)
    draft = _draft()
    draft.address = replace(draft.address, geocode_confidence="not-a-confidence")
    service = _service(draft)
    assert service.snapshot()["candidates"][0]["readEvidence"]["fields"]["geocode"]["availability"] == "invalid"
    with pytest.raises(NetworkScoringGateError, match="Geocode"):
        service.score_candidate(
            candidate_id=draft.candidate_site.candidate_site_id, actor_role_id="expansionManager",
            actor_name=None, idempotency_key=None, correlation_id=None,
        )
    assert service._model_runtime.calls == 0


def test_tenant_scoped_sqlite_reload_retains_exact_evidence_without_read_writes(tmp_path: Any) -> None:
    path = tmp_path / "synthetic-candidate-evidence.sqlite3"
    engine = SqliteEngine(path)
    try:
        document_store = SqliteDocumentStore(engine)
        own_store = TenantScopedDocumentStore(document_store, TENANT)
        foreign_store = TenantScopedDocumentStore(document_store, "another-synthetic-tenant")
        draft = _draft(hard_rules=["recorded-failure"])
        DurableListingRepository(own_store).save_candidate(draft)
        foreign = _draft(hard_rules=[], candidate_id="foreign-synthetic-candidate")
        foreign.listing = replace(foreign.listing, rent_amount=99999.0, tenant_id="another-synthetic-tenant")
        DurableListingRepository(foreign_store).save_candidate(foreign)
    finally:
        engine.close()
    reopened = SqliteEngine(path)
    try:
        own_store = TenantScopedDocumentStore(SqliteDocumentStore(reopened), TENANT)
        runtime = _UnusedRuntime()
        service = NetworkScoringService(
            seed_fixtures=False, require_canonical=True, tenant_id=TENANT,
            listing_repository=DurableListingRepository(own_store),
            sitescore_repository=DurableSiteScoreRepository(own_store), model_runtime=runtime,
        )
        before = [dict(row) for row in reopened.query(
            "SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id"
        )]
        snapshot = service.snapshot()
        assert [row["id"] for row in snapshot["candidates"]] == ["synthetic-candidate"]
        fields = snapshot["candidates"][0]["readEvidence"]["fields"]
        assert fields["hardRule"]["value"] == ["recorded-failure"]
        assert fields["rent"]["value"] == 54321.5
        assert fields["sourceConfidence"]["value"] == 0.0
        after = [dict(row) for row in reopened.query(
            "SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id"
        )]
        assert after == before
        assert runtime.calls == 0
    finally:
        reopened.close()


def test_fixture_snapshot_values_and_six_gate_contract_are_unchanged() -> None:
    service = NetworkScoringService()
    before = service.snapshot()
    after = service.snapshot(read_principal=_principal(DataClassification.PUBLIC))
    assert after == before
    assert all("readEvidence" not in row for row in after["candidates"])
    assert all(row["gate"]["totalCount"] == 6 for row in after["candidates"])
