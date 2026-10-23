"""Actual PostgreSQL owner/locks/CAS/rollback; mocked storage HTTP is separate.

Uses the original fresh owned PostgreSQL fixture and all canonical migrations,
not a PostgreSQL dialect label on SQLite. Two real engines observe each other.
Synthetic canonical candidate/identity rows and the optional GCS HTTP transport
are engineering inputs; no real media, GCS IAM, approval or UAT is asserted.
The whole file retains original600/INT30/TERM60; lock observation5/result20.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.parser import BytesParser
from email.policy import default
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from modules.listing.domain.models import CandidateSiteDraft, ListingDedupKey
from modules.market_survey.application.assignment_content import (
    SurveyGcsObjectStore,
    SurveyWorkloadCredentials,
)
from modules.market_survey.application.assignment_owner import SurveyAssignmentNotFound
from modules.market_survey.domain.models import AssignmentStatus, SurveyStateConflictError
from modules.market_survey.infrastructure.assignment_factory import CanonicalSurveyFactory
from modules.market_survey.infrastructure.assignment_repository import SurveyRevisionConflict
from shared.api.idempotency import IdempotencyConflictError
from shared.audit.durable_delivery import COLLECTION as DELIVERY_COLLECTION
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.auth import DataClassification, Principal, Role, Scope
from shared.domain import AddressLocation, CandidateSite, Listing
from shared.identity.store import Account
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from tests.integration.test_intake_registry_calendar_postgresql import _bundle, _upgrade

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def owners(intake_blank_db, monkeypatch, tmp_path):
    _upgrade(intake_blank_db, "head")
    apply_upgrade_to_database(intake_blank_db.url())
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", (tmp_path / "owned-survey-audit-worm").as_uri())
    # No configured cloud is borrowed. Zero-photo native evidence remains legal.
    monkeypatch.delenv("ODP_SOURCE_SNAPSHOT_BUCKET", raising=False)
    first = second = None
    try:
        first = _bundle(intake_blank_db, monkeypatch)
        second = _bundle(intake_blank_db, monkeypatch)
        assert first.engine is not second.engine
        assert first.engine.dialect == second.engine.dialect == "postgresql"
        tenant, actor, assignee, cid = (str(uuid4()) for _ in range(4))
        with intake_blank_db.connect() as conn:
            conn.execute("INSERT INTO core.tenants(tenant_id,tenant_name) VALUES (%s,%s)",
                (tenant, "Explicit synthetic Survey PostgreSQL controls"))
        scope = Scope(tenant_id=tenant, modules=frozenset({"operator_console"}),
            brand_ids=frozenset({"brand-owned"}), region_ids=frozenset({"region-owned"}),
            store_ids=frozenset({"store-owned"}), assigned_area_ids=frozenset({"area-owned"}),
            heat_zone_ids=frozenset({"zone-owned"}), clearance=DataClassification.CONFIDENTIAL)
        for subject in (actor, assignee):
            first.identity_store.save_account(Account(account_id=UUID(subject), tenant_id=UUID(tenant),
                username=subject, email=subject + "@example.invalid", display_name="Synthetic PG surveyor"))
            first.identity_store.set_account_roles(subject, [Role.EXPANSION_USER])
            first.identity_store.set_account_scope(subject, scope)
        principal = Principal(actor, roles=frozenset({Role.EXPANSION_USER}), scope=scope)
        surveyor = replace(principal, subject_id=assignee)
        listing = first.listing_repository_for_tenant(tenant)
        metadata = first.operator_intake_repository_for_tenant(tenant)
        address = AddressLocation(address_id="address-owned", tenant_id=tenant,
            raw_address="Explicit synthetic Survey parent")
        source = Listing(listing_id="listing-owned", tenant_id=tenant, address_id=address.address_id,
            source_id="synthetic-survey-PG", source_listing_id="one", rent_amount=1000, area_ping=20)
        listing.save_listing(source, address, ListingDedupKey("synthetic", "one", "one", 1000, 20))
        listing.save_candidate(CandidateSiteDraft(listing=source, address=address,
            candidate_site=CandidateSite(candidate_site_id="candidate-owned", listing_id=source.listing_id,
                address_id=address.address_id), heat_zone_id="zone-owned"))
        metadata.save_listing_metadata(source.listing_id, {"tenantId": tenant, "brandId": "brand-owned",
            "regionId": "region-owned", "storeId": "store-owned", "assignedAreaId": "area-owned",
            "heatZoneId": "zone-owned"})
        factories = [CanonicalSurveyFactory(bundle, audit_log=bundle.audit_log, queue=bundle.job_queue)
            for bundle in (first, second)]
        for bundle in (first, second):
            repo = bundle.survey_assignment_repository_for_tenant(tenant)
            assert repo.engine is bundle.engine is bundle.audit_log._engine is bundle.job_queue._engine
        yield SimpleNamespace(database=intake_blank_db, first=first, second=second, factories=factories,
            principal=principal, surveyor=surveyor, tenant=tenant, actor=actor, assignee=assignee,
            cid=cid, listing=listing, metadata=metadata)
    finally:
        if second is not None:
            second.engine.close()
        if first is not None:
            first.engine.close()


def _service(owners, index=0, principal=None):
    return owners.factories[index](principal or owners.principal, correlation_id=owners.cid)


def _payload(owners):
    return {"targetKind": "CANDIDATE_SITE", "targetId": "candidate-owned", "assignedTo": owners.assignee,
        "expiresAt": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        "reason": "Inspect this synthetic PostgreSQL canonical parent"}


def _create(owners, *, index=0, key="create-one", payload=None):
    return _service(owners, index).command(owners.principal, operation="create", payload=payload or _payload(owners),
        idempotency_key=key, correlation_id=owners.cid)


def _business(engine):
    # PostgreSQL returns dict rows: compare actual values, never tuple(row)
    # (which would compare only column names and miss changed business bytes).
    return {"documents": [dict(row) for row in engine.query(
        "SELECT * FROM durable_documents ORDER BY collection,doc_id")],
        "jobs": [dict(row) for row in engine.query("SELECT * FROM durable_jobs ORDER BY job_id")],
        "events": [dict(row) for row in engine.query(
            "SELECT * FROM durable_audit_events WHERE event_type LIKE ? ORDER BY seq",
            ("market_survey.%",))]}


def _wait_for_owned_backend_lock(database, pid):
    deadline = monotonic() + 5
    with database.connect() as connection:
        while monotonic() < deadline:
            row = connection.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,)).fetchone()
            if row is not None and row[0] == "Lock":
                return True
            sleep(.01)
    return False


def _observe_real_guard_transaction(monkeypatch, service):
    original = service.parents.guard
    entered, observed = Event(), {}

    @contextmanager
    def observed_guard(*args, **kwargs):
        # Instrument only this real guard. The actual PostgreSQL transaction
        # remains bound to the same connection through the original guard and
        # command; exceptions unwind before the normal denial-audit decorator.
        with service.repository.engine.transaction():
            observed["pid"] = service.repository.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"]
            entered.set()
            with original(*args, **kwargs):
                yield
    monkeypatch.setattr(service.parents, "guard", observed_guard)
    return entered, observed


def test_locked_parent_change_is_reauthorized_before_assignment_or_receipt(owners, monkeypatch):
    service = _service(owners, 1)
    initial_read = Event()
    original = service.parents.read

    def observe_read(*args, **kwargs):
        value = original(*args, **kwargs)
        initial_read.set()
        return value
    monkeypatch.setattr(service.parents, "read", observe_read)
    entered, observed = _observe_real_guard_transaction(monkeypatch, service)
    payload = _payload(owners)
    before = _business(owners.first.engine)

    def command():
        try:
            return service.command(owners.principal, operation="create", payload=payload,
                idempotency_key="parent-change", correlation_id=owners.cid)
        except SurveyAssignmentNotFound as exc:
            return exc

    with ThreadPoolExecutor(max_workers=1) as executor:
        with owners.first.engine.transaction(), owners.listing.candidate_guard("candidate-owned"), \
                owners.metadata.candidate_metadata_guard("candidate-owned"):
            current = owners.metadata.get_listing_metadata("listing-owned")
            owners.metadata.save_listing_metadata("listing-owned", {**current, "storeId": "revoked-store"})
            expected = _business(owners.first.engine)
            pending = executor.submit(command)
            assert initial_read.wait(5), "the second engine did not first read the committed in-scope parent"
            assert entered.wait(5)
            assert _wait_for_owned_backend_lock(owners.database, observed["pid"])
            assert not pending.done()
        result = pending.result(timeout=20)
    assert isinstance(result, SurveyAssignmentNotFound)
    assert expected != before  # the deliberate parent update actually changed stored values
    assert _business(owners.first.engine) == expected
    assert _business(owners.second.engine) == expected
    assert owners.first.survey_assignment_repository_for_tenant(owners.tenant).list_assignments() == []
    denied = [event for event in owners.second.audit_log.list_events(tenant_id=owners.tenant)
        if event.event_type == "security.authorization" and event.outcome == "deny"]
    assert denied and denied[-1].actor == owners.actor
    assert denied[-1].action == "create" and denied[-1].resource == "listing/candidate-owned"
    assert denied[-1].correlation_id == owners.cid


def test_two_actual_writers_cannot_both_replace_one_assignment_revision(owners, monkeypatch):
    identity = _create(owners)["assignment"]["assignment_id"]
    first = _service(owners, 0, owners.surveyor)
    second = _service(owners, 1, owners.surveyor)
    entered, observed = _observe_real_guard_transaction(monkeypatch, second)
    payload = {"expectedRevision": 1, "reason": "Claim the actual native task once"}

    def command():
        try:
            return second.command(owners.surveyor, operation="claim", assignment_id=identity, payload=payload,
                idempotency_key="second-writer", correlation_id=owners.cid)
        except SurveyRevisionConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=1) as executor:
        with owners.first.engine.transaction():
            accepted = first.command(owners.surveyor, operation="claim", assignment_id=identity, payload=payload,
                idempotency_key="first-writer", correlation_id=owners.cid)
            assert accepted["assignment"]["revision"] == 2
            expected = _business(owners.first.engine)
            pending = executor.submit(command)
            assert entered.wait(5)
            assert _wait_for_owned_backend_lock(owners.database, observed["pid"])
            assert not pending.done()
        result = pending.result(timeout=20)
    assert isinstance(result, SurveyRevisionConflict)
    assert _business(owners.first.engine) == expected == _business(owners.second.engine)
    row, version = owners.second.survey_assignment_repository_for_tenant(owners.tenant).get_versioned(identity)
    assert version == 2 and row.status is AssignmentStatus.CLAIMED
    claimed = [event for event in owners.second.audit_log.list_events(tenant_id=owners.tenant)
        if event.event_type == "market_survey.assignment_claimed.v1"]
    assert len(claimed) == 1 and claimed[0].actor == owners.assignee


def test_real_pg_second_instance_current_scope_and_exact_receipt_replay(owners):
    payload = _payload(owners)
    accepted = _create(owners, payload=payload)
    before = _business(owners.first.engine)
    read = _service(owners, 1).get(owners.principal, accepted["assignment"]["assignment_id"])
    assert read == accepted["assignment"]
    replay = _create(owners, index=1, payload=payload)
    assert replay["replayed"] is True and replay["assignment"] == accepted["assignment"]
    assert replay["receiptId"] == accepted["receiptId"] and replay["auditEventIds"] == accepted["auditEventIds"]
    assert _business(owners.second.engine) == before
    with pytest.raises(IdempotencyConflictError):
        _create(owners, index=1, payload={**payload, "reason": "Different body under the same exact command key"})
    assert _business(owners.second.engine) == before
    revoked = replace(owners.principal, scope=replace(owners.principal.scope, store_ids=frozenset({"other-store"})))
    with pytest.raises(SurveyAssignmentNotFound):
        _service(owners, 1, revoked).command(revoked, operation="create", payload=payload,
            idempotency_key="create-one", correlation_id=owners.cid)
    assert _business(owners.second.engine) == before
    foreign = replace(owners.principal, scope=replace(owners.principal.scope, tenant_id=str(uuid4())))
    with pytest.raises(SurveyAssignmentNotFound):
        _service(owners, 1, foreign).get(foreign, accepted["assignment"]["assignment_id"])
    assert _business(owners.second.engine) == before
    # Actual outbox intents are pending; local file configuration is not ACK.
    delivery = DurableAuditDelivery(owners.second.engine, owners.tenant, owners.second.job_queue,
        owners.second.audit_log._worm_sink)
    for event_id in accepted["auditEventIds"]:
        intent = delivery.store.get(DELIVERY_COLLECTION, event_id)
        assert intent["status"] == "pending" and "receipt" not in intent
        job = owners.second.engine.query_one(
            "SELECT status FROM durable_jobs WHERE job_type = ? AND idempotency_key = ?",
            ("durable-audit-delivery", "durable-audit:" + owners.tenant + ":" + event_id))
        assert job is not None and job["status"] == "queued"


def _submission():
    return {"expectedRevision": 1, "location": {"latitude": 25.03, "longitude": 121.56, "srid": 4326},
        "surveyedAt": datetime.now(UTC).isoformat(), "attributes": {"fieldObservation": "Explicit engineering input"},
        "mediaIds": [], "confidence": None, "reason": "Submit native pending evidence, not an approval"}


@pytest.mark.parametrize("operation", ["create", "claim", "submit"])
def test_actual_pg_audit_failure_rolls_back_native_documents_receipts_and_delivery_intents(owners, monkeypatch, operation):
    identity = None if operation == "create" else _create(owners)["assignment"]["assignment_id"]
    principal = owners.principal if operation == "create" else owners.surveyor
    service = _service(owners, 0, principal)
    payload = _payload(owners) if operation == "create" else _submission() if operation == "submit" else {
        "expectedRevision": 1, "reason": "Claim once under the original native policy"}
    before = _business(owners.second.engine)
    original = owners.first.audit_log.record

    def fail(event):
        if event.event_type.startswith("market_survey."):
            raise RuntimeError("Explicit owned PostgreSQL audit failure")
        return original(event)
    monkeypatch.setattr(owners.first.audit_log, "record", fail)
    with pytest.raises(RuntimeError, match="Explicit owned PostgreSQL audit failure"):
        if operation == "submit":
            service.submit_evidence(principal, identity=identity, payload=payload, idempotency_key="rollback", correlation_id=owners.cid)
        else:
            service.command(principal, operation=operation, assignment_id=identity, payload=payload,
                idempotency_key="rollback", correlation_id=owners.cid)
    assert _business(owners.second.engine) == before
    monkeypatch.setattr(owners.first.audit_log, "record", original)
    subsequent = _service(owners, 1, principal)
    if operation == "submit":
        result = subsequent.submit_evidence(principal, identity=identity, payload=payload, idempotency_key="rollback", correlation_id=owners.cid)
        evidence = owners.first.survey_assignment_repository_for_tenant(owners.tenant).get_evidence(result["surveyId"])
        assert evidence.assignment_id == identity and evidence.submitter_id == owners.assignee
        assert evidence.media_attachments == [] and evidence.confidence is None
        assert evidence.review_status.value == "PENDING_REVIEW" and evidence.promotion_status.value == "NOT_PROMOTED"
    else:
        result = subsequent.command(principal, operation=operation, assignment_id=identity, payload=payload,
            idempotency_key="rollback", correlation_id=owners.cid)
    assert result["replayed"] is False
    assert result["receiptId"] and result["auditEventIds"]


@pytest.mark.parametrize("operation", ["claim", "submit"])
def test_actual_pg_native_deadline_is_not_extended_or_overridden_by_a_command_receipt(owners, operation):
    created = _create(owners)["assignment"]
    service = _service(owners, 1, owners.surveyor)
    service.clock = lambda: datetime.fromisoformat(created["expires_at"]) + timedelta(seconds=1)
    before = _business(owners.first.engine)
    with pytest.raises(SurveyStateConflictError):
        if operation == "submit":
            service.submit_evidence(owners.surveyor, identity=created["assignment_id"], payload=_submission(),
                idempotency_key="expired", correlation_id=owners.cid)
        else:
            service.command(owners.surveyor, operation="claim", assignment_id=created["assignment_id"],
                payload={"expectedRevision": 1, "reason": "A saved task does not extend its deadline"},
                idempotency_key="expired", correlation_id=owners.cid)
    assert _business(owners.first.engine) == before == _business(owners.second.engine)
    assert owners.first.survey_assignment_repository_for_tenant(owners.tenant).get_versioned(created["assignment_id"])[1] == 1


class _OwnedGcsHttpTransport:
    """Mock HTTP only: real SurveyGcs parser/ADC/HEAD/generation/bytes path.

    This is never installed in production. It creates no bucket and proves no
    cloud IAM; its actual byte dictionary deliberately survives a SQL rollback.
    """
    def __init__(self):
        self.objects = {}

    def open(self, request, timeout):
        assert timeout == 10.0 and request.get_header("Authorization") == "Bearer explicit-mocked-workload-token"
        parsed = urllib.parse.urlsplit(request.full_url)
        assert parsed.hostname == "storage.googleapis.com"
        if request.method == "POST":
            assert urllib.parse.parse_qs(parsed.query) == {"uploadType": ["multipart"], "ifGenerationMatch": ["0"]}
            message = BytesParser(policy=default).parsebytes(
                ("Content-Type: " + request.get_header("Content-type") + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + request.data)
            parts = list(message.iter_parts())
            metadata = json.loads(parts[0].get_payload(decode=True))
            content = parts[1].get_payload(decode=True)
            key = metadata["name"]
            assert metadata["metadata"]["sha256"] == hashlib.sha256(content).hexdigest()
            if key in self.objects:
                raise urllib.error.HTTPError(request.full_url, 412, "Owned create-only precondition", {}, None)
            self.objects[key] = {"generation": "1", "size": str(len(content)), "metadata": metadata["metadata"],
                "contentType": metadata["contentType"], "bytes": content}
            return io.BytesIO(b'{"generation":"1"}')
        key = urllib.parse.unquote(parsed.path.split("/o/", 1)[1])
        row = self.objects[key]
        if urllib.parse.parse_qs(parsed.query).get("alt") == ["media"]:
            assert urllib.parse.parse_qs(parsed.query)["generation"] == [row["generation"]]
            return io.BytesIO(row["bytes"])
        return io.BytesIO(json.dumps({k: v for k, v in row.items() if k != "bytes"}).encode())


def test_actual_pg_verified_content_retry_recovers_only_owned_mock_bytes_after_sql_rollback(owners, monkeypatch):
    transport = _OwnedGcsHttpTransport()
    provider = SurveyWorkloadCredentials(credentials=SimpleNamespace(valid=True, token="explicit-mocked-workload-token"))
    store = SurveyGcsObjectStore(provider, opener=transport)
    for factory in owners.factories:
        factory.object_store, factory.content_bucket = store, "taiwan-snapshots"
    created = _create(owners)["assignment"]
    data = b"explicit-owned-PG-photo-fixture-bytes"
    payload = {"expectedRevision": 1, "dataBase64": base64.b64encode(data).decode(), "contentType": "image/jpeg",
        "mediaKind": "PHOTO", "capturedAt": datetime.now(UTC).isoformat(), "caption": None,
        "reason": "Verify actual mocked bytes before committing SQL attachment metadata"}
    first = _service(owners, 0, owners.surveyor)
    before = _business(owners.second.engine)
    original = owners.first.audit_log.record

    def fail(event):
        if event.event_type == "market_survey.attachment_uploaded.v1":
            raise RuntimeError("Explicit attachment audit failure")
        return original(event)
    monkeypatch.setattr(owners.first.audit_log, "record", fail)
    with pytest.raises(RuntimeError, match="Explicit attachment audit failure"):
        first.upload_attachment(owners.surveyor, identity=created["assignment_id"], payload=payload,
            idempotency_key="same-object-and-SQL-intent", correlation_id=owners.cid)
    assert _business(owners.second.engine) == before
    assert len(transport.objects) == 1  # external creation is not SQL rollback
    monkeypatch.setattr(owners.first.audit_log, "record", original)
    second = _service(owners, 1, owners.surveyor)
    result = second.upload_attachment(owners.surveyor, identity=created["assignment_id"], payload=payload,
        idempotency_key="same-object-and-SQL-intent", correlation_id=owners.cid)
    assert result["replayed"] is False and result["attachment"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert len(transport.objects) == 1
    assert next(iter(transport.objects.values()))["generation"] == "1"
    record = owners.first.survey_assignment_repository_for_tenant(owners.tenant).get_media(
        result["attachment"]["mediaId"], created["assignment_id"])
    assert record is not None and record.size_bytes == len(data) and record.uploader_id == owners.assignee
    downloaded = second.download_attachment(owners.surveyor, identity=created["assignment_id"], media_id=record.media_id)
    assert downloaded["data"] == data
