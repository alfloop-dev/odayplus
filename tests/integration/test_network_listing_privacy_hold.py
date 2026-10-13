"""Actual Privacy hold records protect Radar writes and cached receipts.

Synthetic principals and a local append-only sink exercise the existing durable
producer. These controls do not attest human approval, the complete canonical
schema, production WORM, or a live Privacy UI journey.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest

from modules.listing.application.intake_privacy import IntakePrivacyService
from modules.opsboard.application.network_listings import NetworkListingConflict
from shared.audit.integrity import sha256_hex
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.auth import Principal, Role, Scope
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from tests.integration.test_network_listing_mutation_atomicity import (
    _invoke,
    _rows,
    _wired_resolver,
)
from tests.integration.test_network_listing_mutation_atomicity import (
    listing_engine as listing_engine,
)
from tests.unit.test_network_listing_mutation_safety import SOURCE, TARGET, TENANT

PLACER = "00000000-0000-0000-0000-000000000401"
OFFICER = "00000000-0000-0000-0000-000000000402"
APPROVER = "00000000-0000-0000-0000-000000000403"


def _privacy(intakes, engine, tmp_path):
    sink = LocalAppendOnlyWormSink(tmp_path / "synthetic-privacy-worm")
    return IntakePrivacyService(
        audit_log=DurableAuditLog(engine, worm_sink=sink),
        evidence_store=SimpleNamespace(_worm_sink=sink),
        intake_repository=intakes, document_store=intakes._store,
    )


def _place(service, subject):
    return service.place_legal_hold(
        principal=Principal(subject_id=PLACER, roles=frozenset({Role.SITE_REVIEWER}),
                            scope=Scope(tenant_id=TENANT)),
        tenant_id=TENANT, subject_type="listing", subject_id=subject,
        reason="Synthetic independent preservation request", approved_by=APPROVER,
        correlation_id="00000000-0000-0000-0000-000000000404",
    )


def _release(service, subject):
    return service.release_legal_hold(
        principal=Principal(subject_id=OFFICER, roles=frozenset({Role.FINANCE_LEGAL}),
                            scope=Scope(tenant_id=TENANT)),
        tenant_id=TENANT, subject_type="listing", subject_id=subject,
        reason="Synthetic second officer records release", approved_by=APPROVER,
        correlation_id="00000000-0000-0000-0000-000000000405",
    )


def _verify_local_hold_receipt(hold):
    path = Path(unquote(urlsplit(hold["worm_object_uri"]).path))
    envelope = json.loads(path.read_text())
    assert envelope["record_type"] == "legal-holds"
    assert envelope["record_id"] == hold["legal_hold_id"]
    assert envelope["payload"]["subject_id"] == hold["subject_id"]
    assert envelope["payload"]["tenant_id"] == TENANT
    assert envelope["checksum"] == hold["worm_checksum"] == sha256_hex(envelope["payload"])
    return path.read_bytes()


@pytest.mark.parametrize("action,subject", [("archive", SOURCE), ("merge", SOURCE), ("merge", TARGET)])
def test_actual_privacy_producer_blocks_radar_without_client_hold_fields(listing_engine, monkeypatch, tmp_path, action, subject):
    resolver, _, intakes = _wired_resolver(listing_engine, monkeypatch)
    privacy = _privacy(intakes, listing_engine, tmp_path)
    hold = _place(privacy, subject)
    _verify_local_hold_receipt(hold)
    metadata = intakes.get_listing_metadata(subject)
    assert metadata["has_legal_hold"] is True
    assert metadata["hasLegalHold"] is False
    assert resolver._invoke(TENANT, "get_listing", subject)["hasLegalHold"] is True
    before = _rows(listing_engine)
    with pytest.raises(NetworkListingConflict, match="LEGAL_HOLD_CONFLICT"):
        _invoke(resolver, action)
    assert _rows(listing_engine) == before
    assert intakes.list_idempotency_records() == []


@pytest.mark.parametrize("action,subject", [("archive", SOURCE), ("merge", SOURCE), ("merge", TARGET)])
def test_privacy_hold_placed_after_success_blocks_original_receipt_on_fresh_resolver(listing_engine, monkeypatch, tmp_path, action, subject):
    resolver, _, intakes = _wired_resolver(listing_engine, monkeypatch)
    _invoke(resolver, action)
    privacy = _privacy(intakes, listing_engine, tmp_path)
    hold = _place(privacy, subject)
    _verify_local_hold_receipt(hold)
    restarted, _, _ = _wired_resolver(listing_engine, monkeypatch, seed=False)
    before = _rows(listing_engine)
    with pytest.raises(NetworkListingConflict, match="LEGAL_HOLD_CONFLICT"):
        _invoke(restarted, action)
    assert _rows(listing_engine) == before
    assert len(intakes.list_idempotency_records()) == 1


@pytest.mark.parametrize("action", ["archive", "merge"])
def test_second_officer_release_restores_write_and_retains_original_worm_object(listing_engine, monkeypatch, tmp_path, action):
    resolver, _, intakes = _wired_resolver(listing_engine, monkeypatch)
    privacy = _privacy(intakes, listing_engine, tmp_path)
    placed = _place(privacy, SOURCE)
    original = _verify_local_hold_receipt(placed)
    with pytest.raises(NetworkListingConflict, match="LEGAL_HOLD_CONFLICT"):
        _invoke(resolver, action)
    released = _release(privacy, SOURCE)
    _verify_local_hold_receipt(released)
    assert released["legal_hold_id"] == placed["legal_hold_id"]
    assert released["released_by"] == OFFICER and released["version"] == 2
    assert released["worm_object_uri"] != placed["worm_object_uri"]
    assert _verify_local_hold_receipt(placed) == original
    assert intakes.get_listing_metadata(SOURCE)["has_legal_hold"] is False
    assert resolver._invoke(TENANT, "get_listing", SOURCE)["hasLegalHold"] is False
    assert _invoke(resolver, action)["actorSubjectId"]
    assert len(intakes.list_idempotency_records()) == 1


@pytest.mark.parametrize("field", ["has_legal_hold", "legal_hold"])
def test_durable_unknown_hold_is_projected_as_unknown_and_cannot_replay(listing_engine, monkeypatch, field):
    resolver, _, intakes = _wired_resolver(listing_engine, monkeypatch)
    _invoke(resolver, "archive")
    metadata = intakes.get_listing_metadata(SOURCE)
    intakes.save_listing_metadata(SOURCE, {**metadata, field: None})
    assert resolver._invoke(TENANT, "get_listing", SOURCE)["hasLegalHold"] is None
    before = _rows(listing_engine)
    from modules.opsboard.application.network_listings import NetworkListingPolicyError

    with pytest.raises(NetworkListingPolicyError, match="LEGAL_HOLD_STATE_UNAVAILABLE"):
        _invoke(resolver, "archive")
    assert _rows(listing_engine) == before
