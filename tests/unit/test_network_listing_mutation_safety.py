"""Listing-marker mutations preserve holds, terminal state and original receipts."""

from copy import deepcopy

import pytest

from modules.opsboard.application.network_listings import (
    NetworkListingConflict,
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
    source.update(id=SOURCE, duplicateOfId=TARGET)
    target.update(id=TARGET)
    state.update(listings=[source, target], candidates=[], auditEvents=[])
    return NetworkListingService(initial_state=state, seed_fixtures=False, tenant_id=TENANT)


def _archive(service, *, key="archive-once", reason="Operator recorded archive reason", actor=ACTOR):
    return service.archive_listing(
        listing_id=SOURCE, reason=reason, actor_role_id="site_reviewer", actor_name=actor,
        idempotency_key=key, correlation_id="00000000-0000-0000-0000-000000000301",
    )


def _merge(service, *, key="merge-once", target=TARGET, actor=ACTOR):
    return service.merge_listing(
        source_listing_id=SOURCE, target_listing_id=target,
        reason="Independent source evidence confirms the duplicate listing",
        risk_summary=f"Retain evidence while marking {SOURCE} a duplicate of {target}",
        risk_acknowledged=True, actor_role_id="site_reviewer", actor_name=actor,
        idempotency_key=key, correlation_id="00000000-0000-0000-0000-000000000302",
    )


@pytest.mark.parametrize("field", ["hasLegalHold", "legalHold"])
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


def _durable_service(engine, state=None):
    store = SqliteDocumentStore(engine)
    return NetworkListingService(
        listing_repository=DurableListingRepository(store),
        intake_repository=DurableAssistedIntakeRepository(store),
        initial_state=state, seed_fixtures=False, tenant_id=TENANT,
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
