"""Listing-marker mutations preserve holds, terminal state and original receipts."""

from copy import deepcopy
from dataclasses import replace

import pytest

from modules.opsboard.application.network_listings import (
    NetworkListingConflict,
    NetworkListingNotFound,
    NetworkListingPolicyError,
    NetworkListingPreconditionRequired,
    NetworkListingVersionUnavailable,
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
SOURCE = "00000000-0000-0000-0000-000000000201"
TARGET = "00000000-0000-0000-0000-000000000202"


def _service():
    state = NetworkListingService(tenant_id=TENANT).export_state()
    source = deepcopy(next(row for row in state["listings"] if row["id"] == "L-2029"))
    target = deepcopy(next(row for row in state["listings"] if row["id"] == "L-2025"))
    source.update(id=SOURCE, duplicateOfId=TARGET, version=7, hasLegalHold=False)
    target.update(id=TARGET, version=11, hasLegalHold=False)
    state.update(listings=[source, target], candidates=[], auditEvents=[])
    return NetworkListingService(initial_state=state, seed_fixtures=False, tenant_id=TENANT)


def _authorize(row):
    if row.get("tenantId") != TENANT:
        raise PermissionError("TENANT_SCOPE_DENIED")


def _archive(service, *, key="archive-once", reason="Operator recorded archive reason", actor=ACTOR):
    return service.archive_listing(
        listing_id=SOURCE, reason=reason, actor_role_id="site_reviewer", actor_name=actor,
        idempotency_key=key, correlation_id="00000000-0000-0000-0000-000000000301",
        if_match='W/"7"', authorize_existing=_authorize,
    )


def _merge(service, *, key="merge-once", target=TARGET, actor=ACTOR):
    return service.merge_listing(
        source_listing_id=SOURCE, target_listing_id=target,
        reason="Independent source evidence confirms the duplicate listing",
        risk_summary=f"Retain evidence while marking {SOURCE} a duplicate of {target}",
        risk_acknowledged=True, actor_role_id="site_reviewer", actor_name=actor,
        idempotency_key=key, correlation_id="00000000-0000-0000-0000-000000000302",
        if_match='W/"7"', target_if_match='W/"11"', authorize_existing=_authorize,
    )


@pytest.mark.parametrize("field", ["hasLegalHold", "legalHold", "has_legal_hold", "legal_hold"])
@pytest.mark.parametrize("operation", ["archive", "merge-source", "merge-target"])
def test_recorded_hold_denies_mutation_without_state_or_receipt_change(field, operation):
    service = _service()
    record = TARGET if operation == "merge-target" else SOURCE
    service._listing(record)[field] = True
    before = service.export_state()
    with pytest.raises(NetworkListingConflict, match="LEGAL_HOLD_CONFLICT"):
        (_archive if operation == "archive" else _merge)(service)
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == []


@pytest.mark.parametrize("record", [SOURCE, TARGET])
@pytest.mark.parametrize("terminal", [{"status": "archived"}, {"status": "ARCHIVED"},
                                    {"mergedIntoId": "previous-survivor"}])
def test_merge_rejects_terminal_source_or_target_without_moving_evidence(record, terminal):
    service = _service()
    service._listing(record).update(terminal)
    before = service.export_state()
    with pytest.raises(NetworkListingConflict):
        _merge(service)
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == []


@pytest.mark.parametrize("terminal", [{"status": "ARCHIVED"},
                                    {"mergedIntoId": "previous-survivor"}])
def test_archive_rejects_terminal_record_without_rewriting_reason(terminal):
    service = _service()
    service._listing(SOURCE).update(terminal, archivedReason="Original recorded reason")
    before = service.export_state()
    with pytest.raises(NetworkListingConflict):
        _archive(service)
    assert service.export_state() == before


def test_archive_same_key_replays_original_but_new_key_cannot_overwrite_it():
    service = _service()
    evidence = deepcopy(service._listing(SOURCE)["sourceEvidence"])
    first = _archive(service)
    after_first = service.export_state()
    assert first["listing"]["sourceEvidence"] == evidence
    assert _archive(service) == first
    assert service.export_state() == after_first
    with pytest.raises(NetworkListingConflict):
        _archive(service, key="new-archive", reason="Different reason must not replace original")
    assert service.export_state() == after_first
    assert len(service._intakes.list_idempotency_records()) == 1


def test_archive_changed_same_key_payload_conflicts_and_other_actor_cannot_replay():
    service = _service()
    first = _archive(service)
    after_first = service.export_state()
    with pytest.raises(NetworkListingConflict, match="IDEMPOTENCY_CONFLICT"):
        _archive(service, reason="Changed reason")
    with pytest.raises(NetworkListingConflict):
        _archive(service, actor="00000000-0000-0000-0000-000000000102")
    assert service.export_state() == after_first
    assert _archive(service) == first


def test_merge_original_receipt_survives_later_target_archive():
    service = _service()
    source_evidence = deepcopy(service._listing(SOURCE)["sourceEvidence"])
    target_evidence = deepcopy(service._listing(TARGET)["sourceEvidence"])
    first = _merge(service)
    assert first["source"]["sourceEvidence"] == source_evidence
    assert first["target"]["sourceEvidence"] == list(dict.fromkeys(target_evidence + source_evidence))
    service.archive_listing(
        listing_id=TARGET, reason="Subsequent archive retains merged history",
        actor_role_id="site_reviewer", actor_name=ACTOR,
        idempotency_key="archive-target", correlation_id="00000000-0000-0000-0000-000000000303",
        if_match='W/"11"', authorize_existing=_authorize,
    )
    before_replay = service.export_state()
    assert _merge(service) == first
    assert service.export_state() == before_replay


def test_hold_added_after_archive_denies_service_replay_without_disclosing_cached_receipt():
    service = _service()
    _archive(service)
    service._listing(SOURCE)["legalHold"] = True
    before = service.export_state()
    with pytest.raises(NetworkListingConflict, match="LEGAL_HOLD_CONFLICT"):
        _archive(service)
    assert service.export_state() == before


@pytest.mark.parametrize("field", ["has_legal_hold", "legal_hold"])
@pytest.mark.parametrize("value", [None, "true", 1, []])
@pytest.mark.parametrize("operation", ["archive", "merge"])
def test_unavailable_native_hold_cannot_be_replaced_by_an_old_false_projection(field, value, operation):
    service = _service()
    service._listing(SOURCE)[field] = value
    before = service.export_state()
    with pytest.raises(NetworkListingPolicyError, match="LEGAL_HOLD_STATE_UNAVAILABLE"):
        (_archive if operation == "archive" else _merge)(service)
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == []


@pytest.mark.parametrize("field", ["has_legal_hold", "legal_hold"])
@pytest.mark.parametrize("operation", ["archive", "merge"])
def test_native_hold_observation_revoked_after_commit_denies_cached_receipt(field, operation):
    service = _service()
    service._listing(SOURCE)[field] = False
    (_archive if operation == "archive" else _merge)(service)
    service._listing(SOURCE)[field] = None
    before = service.export_state()
    with pytest.raises(NetworkListingPolicyError, match="LEGAL_HOLD_STATE_UNAVAILABLE"):
        (_archive if operation == "archive" else _merge)(service)
    assert service.export_state() == before
    assert len(service._intakes.list_idempotency_records()) == 1


def _durable_service(engine, state=None):
    store = SqliteDocumentStore(engine)
    listings = DurableListingRepository(store)
    intakes = DurableAssistedIntakeRepository(store)
    if state is not None:
        # Explicit fixture writer records preexisting revisions and hold facts;
        # the mutation adapter cannot invent those inputs itself.
        writer = NetworkListingService(initial_state=state, seed_fixtures=False, tenant_id=TENANT)
        for row in state["listings"]:
            listings.save_listing(*writer._dict_to_listing(row))
            intakes.save_listing_metadata(row["id"], deepcopy(row))
    return NetworkListingService(
        listing_repository=listings, intake_repository=intakes,
        seed_fixtures=False, tenant_id=TENANT,
    )


def test_original_archive_reason_and_actor_bound_receipt_survive_real_sqlite_restart(tmp_path):
    path = tmp_path / "listing-safety.sqlite3"
    engine = SqliteEngine(path)
    try:
        service = _durable_service(engine, _service().export_state())
        for record in (SOURCE, TARGET):
            service._sync_listing_to_repo(record)
        first = _archive(service)
    finally:
        engine.close()
    restarted = SqliteEngine(path)
    try:
        restored = _durable_service(restarted)
        assert _archive(restored) == first
        with pytest.raises(NetworkListingConflict):
            _archive(restored, key="new-after-restart", reason="Do not replace original")
        assert restored.get_listing(SOURCE)["archivedReason"] == first["listing"]["archivedReason"]
        assert restored.get_listing(SOURCE)["sourceEvidence"] == first["listing"]["sourceEvidence"]
        assert len(restored._intakes.list_idempotency_records()) == 1
    finally:
        restarted.close()


@pytest.mark.parametrize("operation", ["archive", "merge"])
def test_empty_durable_collection_cannot_replay_a_deleted_resource(tmp_path, operation):
    engine = SqliteEngine(tmp_path / "deleted-resource.sqlite3")
    try:
        service = _durable_service(engine, _service().export_state())
        (_archive if operation == "archive" else _merge)(service)
        service._listing_repository.clear()
        with pytest.raises(NetworkListingNotFound):
            (_archive if operation == "archive" else _merge)(service)
        assert service._listing_repository.list_listings() == []
        assert len(service._intakes.list_idempotency_records()) == 1
    finally:
        engine.close()


def test_compatibility_write_preserves_existing_durable_custody_and_scope(tmp_path):
    engine = SqliteEngine(tmp_path / "preserved-owner-facts.sqlite3")
    try:
        service = _durable_service(engine, _service().export_state())
        original = service._intakes.get_listing_metadata(SOURCE)
        owner_facts = {"version": 7, "hasLegalHold": False, "owner": ACTOR,
                       "proposer": "00000000-0000-0000-0000-000000000102",
                       "brandId": "authorized-brand", "custodyReference": "retained-receipt"}
        service._intakes.save_listing_metadata(SOURCE, {**original, **owner_facts})
        _archive(service)
        saved = service._intakes.get_listing_metadata(SOURCE)
        assert {key: saved[key] for key in owner_facts} == owner_facts
        assert saved["archivedReason"] == "Operator recorded archive reason"
    finally:
        engine.close()


@pytest.mark.parametrize("operation", ["archive", "merge"])
def test_unknown_current_hold_cannot_be_substituted_by_cached_receipt(tmp_path, operation):
    engine = SqliteEngine(tmp_path / "revoked-hold-observation.sqlite3")
    try:
        service = _durable_service(engine, _service().export_state())
        (_archive if operation == "archive" else _merge)(service)
        stored = service._intakes.get_listing_metadata(SOURCE)
        service._intakes.save_listing_metadata(SOURCE, {**stored, "hasLegalHold": None, "legalHold": None})
        with pytest.raises(NetworkListingPolicyError, match="LEGAL_HOLD_STATE_UNAVAILABLE"):
            (_archive if operation == "archive" else _merge)(service)
        assert len(service._intakes.list_idempotency_records()) == 1
    finally:
        engine.close()


def test_an_instance_created_before_another_writer_can_replay_its_durable_receipt(tmp_path):
    engine = SqliteEngine(tmp_path / "receipt-after-instance-load.sqlite3")
    try:
        first = _durable_service(engine, _service().export_state())
        second = _durable_service(engine)
        receipt = _archive(first)
        assert _archive(second) == receipt
        assert len(second._intakes.list_idempotency_records()) == 1
    finally:
        engine.close()


def test_stale_metadata_cannot_reactivate_a_durably_archived_listing(tmp_path):
    engine = SqliteEngine(tmp_path / "terminal-status-authority.sqlite3")
    try:
        service = _durable_service(engine, _service().export_state())
        first = _archive(service)
        metadata = service._intakes.get_listing_metadata(SOURCE)
        # A supplemental metadata writer retains an older presentation label.
        # The actual listing aggregate still records its terminal archive.
        service._intakes.save_listing_metadata(SOURCE, {**metadata, "status": "duplicate"})
        with pytest.raises(NetworkListingConflict, match="LISTING_TERMINAL"):
            _archive(service, key="attempt-reactivation", reason="Do not replace archive")
        assert service._listing_repository.get_listing(SOURCE).listing_status == "archived"
        assert service._intakes.get_listing_metadata(SOURCE)["archivedReason"] == first["listing"]["archivedReason"]
        assert len(service._intakes.list_idempotency_records()) == 1
    finally:
        engine.close()


@pytest.mark.parametrize("operation", ["archive", "merge"])
def test_existing_receipt_is_denied_after_independent_durable_hold_writer(tmp_path, operation):
    engine = SqliteEngine(tmp_path / "replay-new-hold.sqlite3")
    try:
        service = _durable_service(engine, _service().export_state())
        (_archive if operation == "archive" else _merge)(service)
        before = service._intakes.get_listing_metadata(SOURCE)
        service._intakes.save_listing_metadata(SOURCE, {**before, "legalHold": True})
        held = service._intakes.get_listing_metadata(SOURCE)
        with pytest.raises(NetworkListingConflict, match="LEGAL_HOLD_CONFLICT"):
            (_archive if operation == "archive" else _merge)(service)
        assert service._intakes.get_listing_metadata(SOURCE) == held
        assert len(service._intakes.list_idempotency_records()) == 1
    finally:
        engine.close()


@pytest.mark.parametrize("operation", ["archive", "merge"])
def test_service_refreshes_durable_hold_recorded_after_instance_creation(tmp_path, operation):
    engine = SqliteEngine(tmp_path / "fresh-hold.sqlite3")
    try:
        service = _durable_service(engine, _service().export_state())
        for record in (SOURCE, TARGET):
            service._sync_listing_to_repo(record)
        # An independent repository writer changes the hold after this service
        # has loaded its snapshot. The command must consult the new record.
        metadata = service._intakes.get_listing_metadata(SOURCE)
        service._intakes.save_listing_metadata(SOURCE, {**metadata, "legalHold": True})
        before = service._intakes.get_listing_metadata(SOURCE)
        with pytest.raises(NetworkListingConflict, match="LEGAL_HOLD_CONFLICT"):
            (_archive if operation == "archive" else _merge)(service)
        assert service._intakes.get_listing_metadata(SOURCE) == before
        assert service._intakes.list_idempotency_records() == []
        assert service._listing_repository.get_listing(SOURCE).listing_status == "duplicate"
    finally:
        engine.close()



DECISION_INTAKE = "00000000-0000-0000-0000-000000000501"
DECISION_OTHER = "00000000-0000-0000-0000-000000000502"


def _save_decision_intake(service, *, ident=DECISION_INTAKE, target=TARGET):
    intake = {
        "id": ident, "tenantId": TENANT, "sourceId": "recorded-source",
        "originalUrl": "https://example.invalid/durable-decision", "snapshotId": "intake-evidence",
        "stage": "NEEDS_REVIEW", "version": 1, "auditEvents": [],
        "matchResult": {"outcome": "REVISION", "targetListingId": target, "confidence": 0.63},
        "parsedFields": {name: {"normalizedValue": value, "correctedValue": None}
                         for name, value in {"rent": 55000, "areaPing": 25, "floor": "1F"}.items()},
    }
    service._intakes.save_intake(intake)
    service.get_intake(ident)
    return intake


def _decide(service, *, action="revise", ident=DECISION_INTAKE, target=TARGET,
            authority=_authorize, if_match='W/"1"'):
    return service.decide_intake(
        intake_id=ident, action=action, target_listing_id=target,
        reason="Durable original decision reason", risk_summary="Keep source and target authority",
        risk_acknowledged=True, actor_role_id="site_reviewer", actor_name=ACTOR,
        idempotency_key="durable-decision", correlation_id="durable-decision-correlation",
        authorize_existing=authority,
        if_match=if_match,
    )


def test_fresh_decision_requires_its_own_intake_version_without_mutation():
    service = _service()
    _save_decision_intake(service)
    before = service.export_state()
    with pytest.raises(NetworkListingPreconditionRequired, match="PRECONDITION_REQUIRED"):
        _decide(service, if_match=None)
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == []


@pytest.mark.parametrize("version", [None, 0, -1, True, 1.5, "1"])
def test_fresh_decision_rejects_an_unavailable_actual_intake_version(version):
    service = _service()
    intake = _save_decision_intake(service)
    intake["version"] = version
    service._intakes.save_intake(intake)
    service.get_intake(intake["id"])
    before = service.export_state()
    with pytest.raises(NetworkListingVersionUnavailable, match="INTAKE_VERSION_UNAVAILABLE"):
        _decide(service)
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == []


def test_newer_intake_cannot_apply_a_captured_decision_to_a_different_target():
    service = _service()
    intake = _save_decision_intake(service)
    intake.update(version=2, matchResult={"outcome": "REVISION", "targetListingId": SOURCE})
    service._intakes.save_intake(intake)
    service.get_intake(intake["id"])
    before = service.export_state()
    with pytest.raises(NetworkListingConflict, match="VERSION_CONFLICT"):
        _decide(service, action="duplicate")
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == []


def test_historical_tokenless_receipt_keeps_its_original_fingerprint_on_replay():
    service = _service()
    _save_decision_intake(service)
    # Build the exact pre-header request/receipt format through the explicit
    # fixture compatibility path, then restore the production guard.
    service._seed_fixtures = True
    first = _decide(service, if_match=None)
    service._seed_fixtures = False
    before = service.export_state()
    receipts = deepcopy(service._intakes.list_idempotency_records())
    assert _decide(service, if_match=None) == first
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == receipts


@pytest.mark.parametrize("action", ["revise", "duplicate"])
def test_decide_instance_older_than_receipt_replays_durable_original_after_restart(tmp_path, action):
    path = tmp_path / "intake-target-replay.sqlite3"
    engine = SqliteEngine(path)
    try:
        first = _durable_service(engine, _service().export_state())
        _save_decision_intake(first)
        second = _durable_service(engine)
        receipt = _decide(first, action=action)
        assert _decide(second, action=action) == receipt
        assert len(first._intakes.list_idempotency_records()) == 1
    finally:
        engine.close()
    reopened = SqliteEngine(path)
    try:
        restored = _durable_service(reopened)
        assert _decide(restored, action=action) == receipt
        assert len(restored._intakes.list_idempotency_records()) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("action", ["revise", "duplicate"])
@pytest.mark.parametrize("change", ["scope", "deleted"])
def test_decide_replay_refreshes_original_target_after_another_writer_changes_it(tmp_path, action, change):
    engine = SqliteEngine(tmp_path / "intake-target-fresh.sqlite3")
    try:
        first = _durable_service(engine, _service().export_state())
        _save_decision_intake(first)
        second = _durable_service(engine)
        _decide(first, action=action)
        if change == "deleted":
            first._listing_repository.clear()
            expected = NetworkListingNotFound
        else:
            metadata = first._intakes.get_listing_metadata(TARGET)
            first._intakes.save_listing_metadata(TARGET, {**metadata, "heatZoneId": "revoked"})
            expected = PermissionError

        def current_target(row):
            _authorize(row)
            if row.get("heatZoneId") == "revoked":
                raise PermissionError("TARGET_SCOPE_REVOKED")

        records = deepcopy(first._intakes.list_idempotency_records())
        intake = deepcopy(first._intakes.get_intake(DECISION_INTAKE))
        listings = deepcopy(first._listing_repository.list_listings())
        with pytest.raises(expected):
            _decide(second, action=action, authority=current_target)
        assert first._intakes.get_intake(DECISION_INTAKE) == intake
        assert first._listing_repository.list_listings() == listings
        assert first._intakes.list_idempotency_records() == records
    finally:
        engine.close()


@pytest.mark.parametrize("changed_action", ["revise", "quarantine"])
def test_decide_replay_reauthorizes_original_intake_before_changed_body_conflict(tmp_path, changed_action):
    engine = SqliteEngine(tmp_path / "intake-original-authority.sqlite3")
    try:
        first = _durable_service(engine, _service().export_state())
        _save_decision_intake(first)
        _save_decision_intake(first, ident=DECISION_OTHER, target=SOURCE)
        second = _durable_service(engine)
        _decide(first)
        old = first._intakes.get_intake(DECISION_INTAKE)
        first._intakes.save_intake({**old, "heatZoneId": "revoked"})
        checked = []

        def intake_authority(row):
            checked.append(row["id"])
            _authorize(row)
            if row.get("heatZoneId") == "revoked":
                raise PermissionError("ORIGINAL_INTAKE_SCOPE_REVOKED")

        records = deepcopy(first._intakes.list_idempotency_records())
        before = deepcopy(first._intakes.list_intakes())
        with pytest.raises(PermissionError, match="ORIGINAL_INTAKE_SCOPE_REVOKED"):
            _decide(second, action=changed_action, ident=DECISION_OTHER, target=SOURCE,
                    authority=intake_authority)
        assert DECISION_OTHER in checked and DECISION_INTAKE in checked
        assert first._intakes.list_intakes() == before
        assert first._intakes.list_idempotency_records() == records
    finally:
        engine.close()


def test_decide_replay_does_not_replace_original_target_with_current_intake_match(tmp_path):
    engine = SqliteEngine(tmp_path / "intake-current-match.sqlite3")
    try:
        service = _durable_service(engine, _service().export_state())
        _save_decision_intake(service)
        _decide(service)
        current = service._intakes.get_intake(DECISION_INTAKE)
        current["matchResult"]["targetListingId"] = SOURCE
        service._intakes.save_intake(current)
        metadata = service._intakes.get_listing_metadata(TARGET)
        service._intakes.save_listing_metadata(TARGET, {**metadata, "heatZoneId": "revoked"})
        seen = []

        def target_authority(row):
            _authorize(row)
            if "parsedFields" in row or "url" in row:
                return
            seen.append(row["id"])
            if row.get("heatZoneId") == "revoked":
                raise PermissionError("ORIGINAL_TARGET_SCOPE_REVOKED")

        before = deepcopy(service._intakes.get_intake(DECISION_INTAKE))
        receipts = deepcopy(service._intakes.list_idempotency_records())
        with pytest.raises(PermissionError, match="ORIGINAL_TARGET_SCOPE_REVOKED"):
            _decide(service, authority=target_authority)
        assert seen == [TARGET]
        assert service._intakes.get_intake(DECISION_INTAKE) == before
        assert service._intakes.list_idempotency_records() == receipts
    finally:
        engine.close()


@pytest.mark.parametrize("corruption", ["missing-match", "missing-decision", "target-disagrees"])
def test_decide_replay_cannot_infer_original_target_from_a_corrupt_receipt(tmp_path, corruption):
    engine = SqliteEngine(tmp_path / "intake-corrupt-receipt.sqlite3")
    try:
        service = _durable_service(engine, _service().export_state())
        _save_decision_intake(service)
        _decide(service)
        original = service._intakes.list_idempotency_records()[0]
        payload = deepcopy(original.response)
        if corruption == "missing-match":
            payload["matchResult"] = None
        elif corruption == "missing-decision":
            payload["auditEvents"] = []
        else:
            payload["matchResult"]["targetListingId"] = SOURCE
        service._intakes.save_idempotency_record(replace(original, response=payload))
        before = deepcopy(service._intakes.list_intakes())
        receipts = deepcopy(service._intakes.list_idempotency_records())
        rows = deepcopy(service._listing_repository.list_listings())
        with pytest.raises((NetworkListingPolicyError, NetworkListingConflict)):
            _decide(service)
        assert service._intakes.list_intakes() == before
        assert service._intakes.list_idempotency_records() == receipts
        assert service._listing_repository.list_listings() == rows
    finally:
        engine.close()
