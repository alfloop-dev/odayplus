"""Manual creation records supplied facts without inventing measured evidence."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from modules.listing.infrastructure.repositories import InMemoryListingRepository
from modules.opsboard.application.network_listings import (
    InMemoryAssistedIntakeRepository,
    NetworkListingPolicyError,
    NetworkListingService,
)
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.infrastructure.persistence.repositories import DurableListingRepository

TENANT = "00000000-0000-0000-0000-000000000001"
ACTOR = "00000000-0000-0000-0000-000000000101"


def _intake(**overrides):
    values = {"providerListingId": "provider-record-123", "address": "台北市信義區松仁路96號1樓",
              "rent": 58000.75, "areaPing": 23.25, "floor": "1F"}
    record = {
        "id": "00000000-0000-0000-0000-000000000201", "tenantId": TENANT,
        "sourceId": "provider-a", "originalUrl": "https://example.invalid/listing/123",
        "canonicalUrl": "https://example.invalid/listing/123", "heatZoneId": None,
        "snapshotId": None, "stage": "NEEDS_REVIEW", "matchResult": None,
        "auditEvents": [], "version": 1,
        "parsedFields": {key: {"normalizedValue": value, "correctedValue": None}
                         for key, value in values.items()},
    }
    record.update(overrides)
    return record


def _service(listings=None, intakes=None):
    return NetworkListingService(listing_repository=listings, intake_repository=intakes,
                                 seed_fixtures=False, tenant_id=TENANT)


def _create(service, intake, key="manual-create"):
    return service.decide_intake(
        intake_id=intake["id"], action="create", reason="Verified distinct listing",
        risk_summary="Geocode and hard-rule evidence still require verification",
        risk_acknowledged=True, actor_role_id="site_reviewer", actor_name=ACTOR,
        idempotency_key=key, correlation_id="manual-create-correlation",
    )


@pytest.mark.parametrize("snapshot_id", [None, "persisted-source-snapshot-123"])
def test_manual_create_keeps_unknowns_and_actual_evidence_across_restart(tmp_path, snapshot_id):
    path = tmp_path / "manual-provenance.sqlite3"
    engine = SqliteEngine(path)
    intake = _intake(snapshotId=snapshot_id)
    try:
        store = SqliteDocumentStore(engine)
        intakes = DurableAssistedIntakeRepository(store)
        listings = DurableListingRepository(store)
        intakes.save_intake(intake)
        service = _service(listings, intakes)
        result = _create(service, intake)
        listing_id = result["matchResult"]["targetListingId"]
        row = service.get_listing(listing_id)
        assert row["rentPerMonth"] == 58000.75
        assert row["areaPing"] == 23.25 and row["floor"] == "1F"
        assert row["sourceListingId"] == "provider-record-123"
        assert row["heatZoneId"] is None
        assert row["frontageMeters"] is None and row["fitScore"] is None
        assert row["geocodeConfidence"] is None
        assert row["hardRuleFailures"] == ["missing_geocode_evidence"]
        assert "pass" not in row["hardRuleSummary"]
        assert row["sourceEvidence"] == ([snapshot_id] if snapshot_id else [])
        assert listings.get_listing(listing_id).snapshot_id == (snapshot_id or "")
        address = listings.get_address(listings.get_listing(listing_id).address_id)
        assert str(UUID(address.address_id)) == row["addressId"]
        assert address.latitude is None and address.longitude is None
        assert address.geocode_confidence is None and address.h3_res_9 == ""
        with pytest.raises(NetworkListingPolicyError, match="hard-rule"):
            service.convert_listing(listing_id=listing_id, actor_role_id="site_reviewer",
                                    actor_name=ACTOR, idempotency_key="convert-unmeasured",
                                    correlation_id="convert-unmeasured-correlation")
    finally:
        engine.close()

    restarted = SqliteEngine(path)
    try:
        store = SqliteDocumentStore(restarted)
        listings = DurableListingRepository(store)
        service = _service(listings, DurableAssistedIntakeRepository(store))
        restored = service.get_listing(listing_id)
        assert restored["sourceUrl"] == intake["originalUrl"]
        assert restored["snapshotId"] == snapshot_id
        for key in ("rentPerMonth", "areaPing", "frontageMeters", "geocodeConfidence", "fitScore",
                    "hardRuleFailures", "hardRuleSummary", "sourceEvidence", "heatZoneId"):
            assert restored[key] == row[key], key
        assert _create(service, intake) == result
        assert len(listings.list_listings()) == 1 and listings.list_candidates() == []
    finally:
        restarted.close()


def test_two_production_instances_create_distinct_durable_listing_ids(tmp_path):
    engine = SqliteEngine(tmp_path / "two-creators.sqlite3")
    try:
        store = SqliteDocumentStore(engine)
        intakes = DurableAssistedIntakeRepository(store)
        listings = DurableListingRepository(store)
        first = _intake()
        second = _intake(id="00000000-0000-0000-0000-000000000202")
        second["parsedFields"]["providerListingId"]["normalizedValue"] = "provider-record-456"
        for intake in (first, second):
            intakes.save_intake(intake)
        # Both instances see the same initial count. IDs cannot be derived
        # from a process-local listing count and overwrite the first write.
        one, two = _service(listings, intakes), _service(listings, intakes)
        ids = [_create(service, intake, key)["matchResult"]["targetListingId"]
               for service, intake, key in ((one, first, "create-first"), (two, second, "create-second"))]
        assert len({UUID(value) for value in ids}) == 2
        assert {row.listing_id for row in listings.list_listings()} == set(ids)
    finally:
        engine.close()


def test_manual_correction_does_not_manufacture_geocoder_evidence():
    intake = _intake(heatZoneId="HZ-02")
    # Even an untrusted record containing these arbitrary fields has no
    # independently verified geocoder receipt. Corrections cannot mint one.
    intake["parsedFields"].update({name: {"normalizedValue": None, "correctedValue": value}
                                   for name, value in {
                                       "geocodeConfidence": 0.99, "latitude": 25.03,
                                       "longitude": 121.56, "h3Index": "892a100d2d7ffff",
                                       "frontageMeters": 5, "fitScore": 99,
                                   }.items()})
    intake["parsedFields"]["rent"]["correctedValue"] = 61000.25
    intakes = InMemoryAssistedIntakeRepository()
    intakes.save_intake(intake)
    listings = InMemoryListingRepository()
    service = _service(listings, intakes)
    result = _create(service, intake)
    row = service.get_listing(result["matchResult"]["targetListingId"])
    assert row["rentPerMonth"] == 61000.25
    assert row["heatZoneId"] == "HZ-02"
    assert row["geocodeConfidence"] is None and row["frontageMeters"] is None
    assert row["fitScore"] is None
    assert listings.addresses[0].h3_res_9 == ""


def test_manual_create_accepts_unknown_optional_provider_id_and_floor():
    intake = _intake()
    for name in ("providerListingId", "floor"):
        intake["parsedFields"][name]["normalizedValue"] = None
    intakes = InMemoryAssistedIntakeRepository()
    intakes.save_intake(intake)
    listings = InMemoryListingRepository()
    service = _service(listings, intakes)
    result = _create(service, intake)
    listing = listings.get_listing(result["matchResult"]["targetListingId"])
    assert listing.source_listing_id == "" and listing.floor == ""
    assert listing.rent_amount == 58000.75 and listing.area_ping == 23.25


@pytest.mark.parametrize("field,value", [
    ("address", None), ("address", "  "), ("rent", None), ("rent", 0),
    ("rent", float("nan")), ("areaPing", -1), ("areaPing", float("inf")),
])
def test_manual_create_rejects_missing_required_values_without_side_effects(field, value):
    intake = _intake()
    intake["parsedFields"][field]["normalizedValue"] = value
    intakes = InMemoryAssistedIntakeRepository()
    intakes.save_intake(intake)
    before = deepcopy(intakes.get_intake(intake["id"]))
    listings = InMemoryListingRepository()
    service = _service(listings, intakes)
    with pytest.raises(NetworkListingPolicyError, match="required.*" + field):
        _create(service, intake)
    assert listings.list_listings() == []
    assert intakes.get_intake(intake["id"]) == before
    assert intakes.list_idempotency_records() == []


def _measured_listing():
    return {"id": "measured-listing", "sourceId": "source-a", "sourceListingId": "provider-a",
            "status": "new", "rentPerMonth": 58000.75, "areaPing": 23.25, "floor": "1F",
            "address": "台北市信義區松仁路96號1樓", "frontageMeters": 5.75,
            "heatZoneId": "HZ-02", "h3Index": "892a100d2d7ffff",
            "latitude": 0.0, "longitude": 0.0, "geocodeConfidence": 0.92}


def test_canonical_measured_values_keep_precision_and_zero_coordinates():
    repository = InMemoryListingRepository()
    service = _service(repository)
    row = _measured_listing()
    row["addressId"] = "00000000-0000-0000-0000-000000000601"
    listing, address, key = service._dict_to_listing(row)
    repository.save_listing(listing, address, key)
    restored = service._listing_to_dict(listing)
    for name in ("addressId", "rentPerMonth", "frontageMeters", "areaPing", "geocodeConfidence",
                 "latitude", "longitude", "h3Index"):
        assert restored[name] == row[name], name


def test_heat_zone_identifier_is_not_geocoded_h3():
    row = _measured_listing()
    for name in ("h3Index", "geocodeConfidence", "latitude", "longitude"):
        row.pop(name)
    _, address, _ = _service()._dict_to_listing(row)
    assert address.h3_res_9 == ""
    assert address.geocode_confidence is None
    assert address.latitude is None and address.longitude is None


@pytest.mark.parametrize("action", ["revise", "duplicate"])
@pytest.mark.parametrize("snapshot_id", [None, "recorded-new-snapshot"])
def test_operator_decision_preserves_lineage_and_real_snapshot_after_reload(tmp_path, action, snapshot_id):
    path = tmp_path / "recorded-lineage.sqlite3"
    engine = SqliteEngine(path)
    bindings = {"property_id": "00000000-0000-0000-0000-000000000301",
                "platform_property_id": "00000000-0000-0000-0000-000000000302",
                "listing_obs_id": "00000000-0000-0000-0000-000000000401",
                "platform_observation_id": "00000000-0000-0000-0000-000000000402"}
    try:
        store = SqliteDocumentStore(engine)
        intakes = DurableAssistedIntakeRepository(store)
        listings = DurableListingRepository(store)
        row = _measured_listing()
        row["addressId"] = "00000000-0000-0000-0000-000000000601"
        listing, address, key = _service()._dict_to_listing(row)
        listing = replace(
            listing, currency="USD", depth_m=12.5, corner_flag=True, parking_flag=True,
            utility_electricity_flag=True, utility_drainage_flag=True, utility_gas_flag=True,
            available_from=date(2026, 10, 1), tenant_id=TENANT, confidence=1.0,
            measurement_schema_version="v1", confidence_status="legacy_unknown",
        )
        address = replace(
            address, raw_address="台北市 信義區 松仁路96號 1F", city="台北市", district="信義區",
            village="synthetic-village", road="松仁路", tenant_id=TENANT, revision=7,
            geocode_precision="rooftop", manual_override_flag=True,
            h3_res_8="882a100d2dfffff", h3_res_10="8a2a100d2d47fff",
        )
        object.__setattr__(listing, "recorded_measurement_receipt", {"id": "synthetic-listing-receipt"})
        object.__setattr__(address, "recorded_geocode_receipt", {"id": "synthetic-geocode-receipt"})
        for name, value in bindings.items():
            object.__setattr__(listing, name, value)
        listings.save_listing(listing, address, key)
        intakes.save_listing_metadata(row["id"], {"sourceEvidence": ["recorded-original-snapshot"]})
        intake = _intake(snapshotId=snapshot_id,
                         matchResult={"outcome": "POSSIBLE_MATCH", "targetListingId": row["id"]})
        intake["parsedFields"]["rent"]["correctedValue"] = 60000.5
        intakes.save_intake(intake)
        service = _service(listings, intakes)
        service.decide_intake(
            intake_id=intake["id"], action=action, target_listing_id=row["id"],
            reason="Recorded observation confirms new rent", risk_summary="Retain property binding",
            risk_acknowledged=True, actor_role_id="site_reviewer", actor_name=ACTOR,
            idempotency_key="revise-recorded-lineage", correlation_id="revise-recorded-lineage-correlation",
        )
        assert listings.get_listing(row["id"]).rent_amount == (60000.5 if action == "revise" else 58000.75)
        expected = replace(listing, listing_status="watching",
                           rent_amount=60000.5 if action == "revise" else listing.rent_amount)
        assert listings.get_listing(row["id"]) == expected
        assert listings.get_address(address.address_id) == address
    finally:
        engine.close()
    restarted = SqliteEngine(path)
    try:
        store = SqliteDocumentStore(restarted)
        listings = DurableListingRepository(store)
        service = _service(listings, DurableAssistedIntakeRepository(store))
        restored = service.get_listing(row["id"])
        persisted_listing = listings.get_listing(row["id"])
        persisted_address = listings.get_address(persisted_listing.address_id)
        assert persisted_listing == expected and persisted_address == address
        assert persisted_listing.recorded_measurement_receipt == listing.recorded_measurement_receipt
        assert persisted_address.recorded_geocode_receipt == address.recorded_geocode_receipt
        assert restored["sourceEvidence"] == (
            ["recorded-original-snapshot"] + ([snapshot_id] if snapshot_id else [])
        )
        for name, value in bindings.items():
            assert getattr(listings.get_listing(row["id"]), name) == value
            assert restored[name] == value
    finally:
        restarted.close()


@pytest.mark.parametrize("target_format", [None, "FMT-MICRO-STORE"])
def test_candidate_sync_preserves_explicit_target_format_and_unset_default(target_format):
    repository = InMemoryListingRepository()
    service = _service(repository)
    row = _measured_listing()
    listing, address, key = service._dict_to_listing(row)
    repository.save_listing(listing, address, key)
    service = _service(repository)
    candidate = {"id": "candidate-a", "listingId": row["id"], "status": "CANDIDATE",
                 "heatZoneId": "HZ-02", "feasibilityFlags": []}
    if target_format is not None:
        candidate["targetFormatCode"] = target_format
    service.save_candidate(candidate)
    draft = repository.list_candidates()[0]
    assert draft.candidate_site.target_format_code == (target_format or "")
    reloaded = _service(repository)
    restored = reloaded.get_candidate(candidate["id"])
    assert restored["targetFormatCode"] == (target_format or "")
    reloaded.save_candidate(restored)
    assert repository.list_candidates()[0].candidate_site.target_format_code == (target_format or "")


def _legacy_convert_state():
    state = _service().export_state()
    state["listings"] = [{**_measured_listing(), "hardRuleFailures": []}]
    state["candidates"] = [{"id": "recorded-candidate", "listingId": "measured-listing",
                            "targetFormatCode": "Recorded exact format", "status": "new"}]
    promotion = {"promotion_decision_id": "recorded-promotion", "tenant_id": TENANT,
                 "listing_id": "measured-listing", "candidate_site_id": "recorded-candidate",
                 "decision_type": "STANDARD", "status": "SCORE_QUEUED",
                 "target_format_code": "Recorded exact format", "reviewed_at": "2026-10-13T00:00:00+00:00",
                 "proposer_subject_id": ACTOR, "reviewer_subject_id": "00000000-0000-0000-0000-000000000102"}
    return state, promotion


@pytest.mark.parametrize("change", [
    {"tenant_id": "other-tenant"}, {"listing_id": "other-listing"},
    {"candidate_site_id": "other-candidate"}, {"decision_type": "LEGACY_RECONCILED"},
    {"status": "PENDING_REVIEW"}, {"status": "REJECTED"}, {"reviewed_at": None},
    {"proposer_subject_id": None}, {"reviewer_subject_id": ACTOR},
    {"reviewer_subject_id": None}, {"target_format_code": "other-format"},
])
def test_legacy_convert_requires_matching_recorded_independent_approval(change):
    state, promotion = _legacy_convert_state()
    intakes = InMemoryAssistedIntakeRepository()
    intakes.save_promotion({**promotion, **change})
    service = NetworkListingService(intake_repository=intakes, initial_state=state,
                                    seed_fixtures=False, tenant_id=TENANT)
    before = service.export_state()
    with pytest.raises(NetworkListingPolicyError, match="PROMOTION_APPROVAL_REQUIRED"):
        service.convert_listing(listing_id="measured-listing", actor_role_id="site_reviewer",
                                actor_name=ACTOR, idempotency_key="legacy-denied", correlation_id="legacy-denied")
    assert service.export_state() == before
    assert intakes.list_idempotency_records() == []


@pytest.mark.parametrize("verdict", [None, False, 0, "", {}, [None]])
def test_legacy_convert_unknown_hard_rule_evidence_is_not_a_pass(verdict):
    state, promotion = _legacy_convert_state()
    state["listings"][0]["hardRuleFailures"] = verdict
    intakes = InMemoryAssistedIntakeRepository()
    intakes.save_promotion(promotion)
    service = NetworkListingService(intake_repository=intakes, initial_state=state,
                                    seed_fixtures=False, tenant_id=TENANT)
    before = service.export_state()
    with pytest.raises(NetworkListingPolicyError, match="HARD_RULE_EVIDENCE_REQUIRED"):
        service.convert_listing(listing_id="measured-listing", actor_role_id="site_reviewer",
                                actor_name=ACTOR, idempotency_key="legacy-verdict", correlation_id="legacy-verdict")
    assert service.export_state() == before


def test_archive_keeps_unknown_hard_rules_unknown():
    state, _ = _legacy_convert_state()
    state["listings"][0]["hardRuleFailures"] = None
    state["listings"][0]["sourceEvidence"] = None
    service = NetworkListingService(initial_state=state, seed_fixtures=False, tenant_id=TENANT)
    result = service.archive_listing(listing_id="measured-listing", reason="Recorded archive request",
                                     actor_role_id="site_reviewer", actor_name=ACTOR,
                                     idempotency_key="archive-unknown", correlation_id="archive-unknown")
    assert result["listing"]["hardRuleFailures"] is None
    assert result["auditEvent"]["metadata"]["hardRuleFailures"] is None
    assert result["auditEvent"]["metadata"]["sourceEvidenceCount"] is None
