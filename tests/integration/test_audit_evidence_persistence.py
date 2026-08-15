"""Durable audit evidence store and export retention (ODP-PV-011).

These tests exercise the task acceptance criteria:

1. Exported evidence bundles are persisted with hash, actor/reason, privacy
   scope, and a resolved retention window.
2. The store and the API survive a process restart (re-opened on-disk DB).
3. Retention is resolved from the privacy scope, and expired, non-held records
   can be purged while legal holds are respected.
4. Audit events and persisted bundles share the same correlation/hash metadata.

"Process restart" is simulated by closing the durable engine and rebuilding a
fresh bundle on the same on-disk SQLite file, then reading back through the
public interfaces.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.opsboard.audit import (
    AuditEvidenceExportError,
    AuditEvidenceExportService,
    DecisionCard,
    EvidenceExportRequest,
)
from shared.audit import AuditEvent, AuditIntegrityError, InMemoryAuditLog
from shared.audit.persistence import (
    RETENTION_REGULATORY,
    RETENTION_STANDARD,
    EvidenceGovernanceError,
    EvidenceImmutabilityError,
    EvidenceIntegrityError,
    GovernedEvidenceOperation,
    InMemoryEvidenceBundleStore,
    resolve_retention_policy,
)
from shared.audit.worm import AuditWormReceipt
from shared.auth import Role
from shared.infrastructure.persistence import build_persistence
from shared.infrastructure.persistence.factory import _durable_bundle
from tests.integration._authz import (
    AUDIT_HEADERS,
    AUDIT_LEGAL_HEADERS,
    AUDIT_RECORDS_HEADERS,
    auth_headers,
)

NOW = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)


class RecordingWormSink:
    sink_id = "gs://odp-test-audit-worm/audit-evidence"

    def __init__(self) -> None:
        self.writes: list[dict[str, str]] = []

    def write_audit_event(self, event) -> AuditWormReceipt:
        receipt = AuditWormReceipt(
            sink_id=self.sink_id,
            object_uri=f"{self.sink_id}/audit-events/{event.sequence}-{event.event_id}.json",
            record_type="audit-events",
            record_id=event.event_id,
            checksum=event.event_hash or "",
        )
        self.writes.append(receipt.to_dict())
        return receipt

    def write_retained_evidence(self, record) -> AuditWormReceipt:
        receipt = AuditWormReceipt(
            sink_id=self.sink_id,
            object_uri=f"{self.sink_id}/retained-evidence/{record.sequence}-{record.export_id}.json",
            record_type="retained-evidence",
            record_id=record.export_id,
            checksum=record.record_hash or record.tombstone_hash or "",
        )
        self.writes.append(receipt.to_dict())
        return receipt


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "evidence.sqlite3")


def _ready_card(audit_event_id: str) -> DecisionCard:
    return DecisionCard(
        decision_id="decision-intervention-001",
        decision_type="INTERVENTION_EFFECT",
        module="InterventionOps",
        title="Price intervention gross margin effect",
        subject_ref="intervention/intv-001",
        outcome="COMPLETED",
        owner="ops-manager",
        decided_at=NOW,
        rationale="Observation window matured with DID evidence and positive margin.",
        input_snapshot_id="intervention-input-snapshot-20260628",
        evidence_refs=("label/intv-001", "artifact/effect-report-001"),
        model_refs=("forecast_revenue_interval:1.1.0",),
        policy_refs=("intervention-lifecycle-policy-v1",),
        audit_event_ids=(audit_event_id,),
        subsidy_requirements=("ELIGIBILITY", "DECISION", "EFFECT", "CONTROL", "TRACE"),
        controls=("approval_recorded", "observation_window_matured"),
        data_snapshot_id="canonical-store-snapshot-20260628",
        artifact_hash="sha256:effect-report-001",
        metrics={
            "incremental_gross_margin": 48_000.0,
            "evidence_level": "L3",
            "reviewer_email": "alice@example.com",
        },
    )


def _request(
    *,
    sensitive: bool = True,
    classification: str = "restricted",
    correlation_id: str = "corr-evidence-persist-1",
    requested_by: str = "reviewer-a",
) -> EvidenceExportRequest:
    return EvidenceExportRequest(
        program_id="subsidy-program-2026-q2",
        purpose="quarterly subsidy review",
        requested_by=requested_by,
        from_time=NOW - timedelta(days=1),
        to_time=NOW + timedelta(days=1),
        correlation_ids=(correlation_id,),
        export_scope="tenant=t1;region=north;program=subsidy-program-2026-q2",
        environment="ci",
        build_version="test-build",
        data_classification=classification,
        sensitive=sensitive,
        purpose_scope="subsidy-review:q2",
        expires_at=NOW + timedelta(hours=4),
        authorized_by="legal-approver",
        authorization_id="authz-sub-2026-q2",
        masking_profile="masked",
    )


# -- retention policy ---------------------------------------------------------


def test_retention_policy_resolves_from_privacy_scope() -> None:
    restricted = resolve_retention_policy("restricted", sensitive=False)
    assert restricted.retention_class == RETENTION_REGULATORY

    sensitive_internal = resolve_retention_policy("internal", sensitive=True)
    assert sensitive_internal.retention_class == RETENTION_REGULATORY

    standard = resolve_retention_policy("internal", sensitive=False)
    assert standard.retention_class == RETENTION_STANDARD
    assert standard.retain_until(NOW) == NOW + timedelta(days=standard.retention_days)


# -- sensitive export denial is audited ---------------------------------------


def test_sensitive_export_denial_is_audited() -> None:
    bundle_persistence = build_persistence()  # memory mode
    service = AuditEvidenceExportService(
        audit_log=bundle_persistence.audit_log,
        evidence_store=bundle_persistence.evidence_store,
    )
    # Sensitive request with an empty export_scope is denied.
    bad_request = EvidenceExportRequest(
        program_id="subsidy-program-2026-q2",
        purpose="quarterly subsidy review",
        requested_by="reviewer-a",
        from_time=NOW - timedelta(days=1),
        to_time=NOW + timedelta(days=1),
        correlation_ids=("corr-denied-1",),
        export_scope="   ",
        data_classification="restricted",
        sensitive=True,
    )
    with pytest.raises(AuditEvidenceExportError):
        service.export(bad_request, decision_cards=(_ready_card("evt-x"),))

    denials = [
        event
        for event in service.audit_log.list_events(correlation_id="corr-denied-1")
        if event.event_type == "audit.evidence_export.v1" and event.outcome == "denied"
    ]
    assert len(denials) == 1
    assert denials[0].metadata["sensitive"] is True
    assert "export_scope" in denials[0].metadata["reason"]
    # Nothing was persisted for a denied export.
    assert bundle_persistence.evidence_store.list_all() == []


# -- service persists with hash / actor / privacy scope / retention -----------


def test_export_persists_retained_record_with_metadata() -> None:
    bundle_persistence = build_persistence()  # memory mode -> in-memory store
    store = bundle_persistence.evidence_store
    assert isinstance(store, InMemoryEvidenceBundleStore)
    service = AuditEvidenceExportService(
        audit_log=bundle_persistence.audit_log, evidence_store=store
    )
    event = service.audit_log.record(
        AuditEvent(
            event_type="intervention.effect_evaluated.v1",
            actor="analyst-a",
            action="evaluate",
            resource="intervention/intv-001",
            outcome="completed",
            correlation_id="corr-evidence-persist-1",
            occurred_at=NOW,
            metadata={"evidence_level": "L3"},
        )
    )

    bundle = service.export(
        _request(),
        decision_cards=(_ready_card(event.event_id),),
        generated_at=NOW,
    )

    record = store.get(bundle.export_id)
    assert record is not None
    # hash
    assert record.bundle_checksum == bundle.bundle_checksum
    # actor / reason
    assert record.requested_by == "reviewer-a"
    assert record.purpose == "quarterly subsidy review"
    # privacy scope
    assert record.data_classification == "restricted"
    assert record.sensitive is True
    assert "tenant=t1" in record.export_scope
    # retention
    assert record.retention_class == RETENTION_REGULATORY
    assert record.retain_until == NOW + timedelta(days=record_retention_days())
    assert record.record_hash is not None
    assert record.previous_hash == "0" * 64
    assert record.signature_key_id
    # full bundle preserved + audit linkage
    assert record.bundle["bundle_checksum"] == bundle.bundle_checksum
    assert record.audit_event_id == bundle.audit_event_id
    assert bundle.to_dict()["retention"]["retention_class"] == RETENTION_REGULATORY
    assert bundle.to_dict()["export_governance"]["authorization_id"] == "authz-sub-2026-q2"
    assert bundle.decision_cards[0].metrics["reviewer_email"] == "a****@example.com"
    assert set(bundle.audit_events[0]) >= {"event_id", "integrity"}


def test_export_writes_audit_event_and_bundle_to_worm_sink() -> None:
    sink = RecordingWormSink()
    audit_log = InMemoryAuditLog(worm_sink=sink)
    store = InMemoryEvidenceBundleStore(worm_sink=sink)
    service = AuditEvidenceExportService(audit_log=audit_log, evidence_store=store)
    event = service.audit_log.record(
        AuditEvent(
            event_type="intervention.effect_evaluated.v1",
            actor="analyst-a",
            action="evaluate",
            resource="intervention/intv-001",
            outcome="completed",
            correlation_id="corr-worm-runtime-1",
            occurred_at=NOW,
        )
    )

    bundle = service.export(
        _request(correlation_id="corr-worm-runtime-1"),
        decision_cards=(_ready_card(event.event_id),),
        generated_at=NOW,
    )

    record = store.get(bundle.export_id)
    assert record is not None
    assert event.worm_sink_id == sink.sink_id
    assert record.worm_sink_id == sink.sink_id
    assert {write["record_type"] for write in sink.writes} == {
        "audit-events",
        "retained-evidence",
    }
    assert bundle.export_id in {
        write["record_id"] for write in sink.writes if write["record_type"] == "retained-evidence"
    }


def record_retention_days() -> int:
    return resolve_retention_policy("restricted", sensitive=True).retention_days


# -- durable store survives a restart -----------------------------------------


def test_durable_evidence_store_survives_restart(db_path) -> None:
    bundle_persistence = _durable_bundle(db_path)
    try:
        service = AuditEvidenceExportService(
            audit_log=bundle_persistence.audit_log,
            evidence_store=bundle_persistence.evidence_store,
        )
        event = service.audit_log.record(
            AuditEvent(
                event_type="intervention.effect_evaluated.v1",
                actor="analyst-a",
                action="evaluate",
                resource="intervention/intv-001",
                outcome="completed",
                correlation_id="corr-evidence-persist-1",
                occurred_at=NOW,
            )
        )
        bundle = service.export(
            _request(),
            decision_cards=(_ready_card(event.event_id),),
            generated_at=NOW,
        )
        export_id = bundle.export_id
        checksum = bundle.bundle_checksum
    finally:
        bundle_persistence.engine.close()

    # Simulated restart: fresh bundle on the same file.
    reopened = _durable_bundle(db_path)
    try:
        record = reopened.evidence_store.get(export_id)
        assert record is not None
        assert record.bundle_checksum == checksum
        assert record.retention_class == RETENTION_REGULATORY
        assert record.bundle["program_id"] == "subsidy-program-2026-q2"
        assert reopened.audit_log.verify_chain().ok is True
        assert reopened.evidence_store.verify_integrity().ok is True
        # listing by program also resolves after restart
        listed = reopened.evidence_store.list_for_program("subsidy-program-2026-q2")
        assert [r.export_id for r in listed] == [export_id]
        assert listed[0].requested_by == "reviewer-a"
        assert listed[0].correlation_id == "corr-evidence-persist-1"
        assert listed[0].retain_until == NOW + timedelta(days=record_retention_days())
    finally:
        reopened.engine.close()


# -- retention purge respects legal hold --------------------------------------


def test_purge_expired_respects_legal_hold(db_path) -> None:
    bundle_persistence = _durable_bundle(db_path)
    try:
        store = bundle_persistence.evidence_store
        service = AuditEvidenceExportService(
            audit_log=bundle_persistence.audit_log, evidence_store=store
        )
        standard_bundle = service.export(
            EvidenceExportRequest(
                program_id="program-standard",
                purpose="low sensitivity export",
                requested_by="reviewer-b",
                from_time=NOW - timedelta(days=1),
                to_time=NOW + timedelta(days=1),
                correlation_ids=("corr-standard-1",),
                export_scope="tenant=t1",
                data_classification="internal",
                sensitive=False,
            ),
            decision_cards=(_ready_card("evt-x"),),
            generated_at=NOW,
        )
        standard_record = store.get(standard_bundle.export_id)
        assert standard_record.retention_class == RETENTION_STANDARD
        with pytest.raises(EvidenceImmutabilityError):
            store.save(standard_record)
        with pytest.raises(EvidenceImmutabilityError):
            store.delete(standard_bundle.export_id)

        # As-of well beyond every retention window: standard record is expired.
        as_of = NOW + timedelta(days=4000)
        assert standard_bundle.export_id in [r.export_id for r in store.list_expired(as_of)]
        with pytest.raises(EvidenceGovernanceError):
            store.purge_expired(as_of)
        with pytest.raises(EvidenceGovernanceError):
            store.apply_legal_hold(
                standard_bundle.export_id,
                context=GovernedEvidenceOperation(
                    actor="reviewer-b",
                    role="legal",
                    reason="self-hold attempt",
                    correlation_id="corr-standard-1",
                ),
            )

        # Put it on legal hold -> excluded from expiry + purge.
        held = store.apply_legal_hold(
            standard_bundle.export_id,
            context=GovernedEvidenceOperation(
                actor="legal-a",
                role="legal",
                reason="litigation hold",
                correlation_id="corr-standard-1",
            ),
        )
        assert held.is_expired(as_of) is False
        assert held.governance_log[0]["operation"] == "legal_hold"
        assert (
            store.purge_expired(
                as_of,
                context=GovernedEvidenceOperation(
                    actor="records-a",
                    role="retention_manager",
                    reason="scheduled retention sweep",
                    correlation_id="corr-retention-sweep",
                ),
            )
            == []
        )
        assert store.get(standard_bundle.export_id) is not None
    finally:
        bundle_persistence.engine.close()


def test_retention_purge_tombstones_non_tail_without_breaking_chain(db_path) -> None:
    old = NOW - timedelta(days=500)
    for store_factory in (
        lambda: (InMemoryAuditLog(), InMemoryEvidenceBundleStore(), None),
        lambda: _durable_store_for_test(db_path),
    ):
        audit_log, store, close = store_factory()
        try:
            service = AuditEvidenceExportService(
                audit_log=audit_log,
                evidence_store=store,
            )
            standard = service.export(
                EvidenceExportRequest(
                    program_id="program-standard-expired",
                    purpose="standard retention export",
                    requested_by="reviewer-standard",
                    from_time=old - timedelta(days=1),
                    to_time=old + timedelta(days=1),
                    correlation_ids=("corr-standard-expired",),
                    export_scope="tenant=t1",
                    data_classification="internal",
                    sensitive=False,
                ),
                decision_cards=(_ready_card("evt-standard"),),
                generated_at=old,
            )
            regulatory = service.export(
                _request(
                    correlation_id="corr-regulatory-retained",
                    requested_by="reviewer-regulatory",
                    sensitive=True,
                    classification="restricted",
                ),
                decision_cards=(_ready_card("evt-regulatory"),),
                generated_at=old,
            )

            purged = store.purge_expired(
                NOW,
                context=GovernedEvidenceOperation(
                    actor="records-a",
                    role="records_manager",
                    reason="scheduled standard-retention sweep",
                    correlation_id="corr-non-tail-purge",
                ),
            )

            assert purged == [standard.export_id]
            records = store.list_all()
            assert [record.export_id for record in records[:2]] == [
                standard.export_id,
                regulatory.export_id,
            ]
            assert records[0].purged_at is not None
            assert records[0].bundle["purged"] is True
            assert records[1].previous_hash == records[0].record_hash
            assert store.verify_integrity().ok is True

            new_bundle = service.export(
                EvidenceExportRequest(
                    program_id="program-standard-new",
                    purpose="new standard export",
                    requested_by="reviewer-new",
                    from_time=NOW - timedelta(days=1),
                    to_time=NOW + timedelta(days=1),
                    correlation_ids=("corr-standard-new",),
                    export_scope="tenant=t1",
                    data_classification="internal",
                    sensitive=False,
                ),
                decision_cards=(_ready_card("evt-new"),),
                generated_at=NOW,
            )
            assert store.get(new_bundle.export_id).sequence == 3
        finally:
            if close is not None:
                close()


def test_retention_purge_rejects_exporter_actor_sod() -> None:
    old = NOW - timedelta(days=500)
    audit_log = InMemoryAuditLog()
    store = InMemoryEvidenceBundleStore()
    service = AuditEvidenceExportService(audit_log=audit_log, evidence_store=store)
    bundle = service.export(
        EvidenceExportRequest(
            program_id="program-self-purge",
            purpose="standard export by records actor",
            requested_by="records-a",
            from_time=old - timedelta(days=1),
            to_time=old + timedelta(days=1),
            correlation_ids=("corr-self-purge",),
            export_scope="tenant=t1",
            data_classification="internal",
            sensitive=False,
        ),
        decision_cards=(_ready_card("evt-self-purge"),),
        generated_at=old,
    )

    with pytest.raises(EvidenceGovernanceError, match="exporter cannot purge"):
        store.purge_expired(
            NOW,
            context=GovernedEvidenceOperation(
                actor="records-a",
                role="records_manager",
                reason="self purge attempt",
                correlation_id="corr-self-purge",
            ),
        )
    assert store.get(bundle.export_id).purged_at is None


def _durable_store_for_test(db_path):
    bundle_persistence = _durable_bundle(db_path)
    return (
        bundle_persistence.audit_log,
        bundle_persistence.evidence_store,
        bundle_persistence.engine.close,
    )


def test_api_governance_blocks_spoofing_and_purges_only_non_held(db_path) -> None:
    old = NOW - timedelta(days=500)
    bundle_persistence = _durable_bundle(db_path)
    try:
        store = bundle_persistence.evidence_store
        service = AuditEvidenceExportService(
            audit_log=bundle_persistence.audit_log, evidence_store=store
        )
        for suffix in ("held", "purge"):
            event = service.audit_log.record(
                AuditEvent(
                    event_type="intervention.effect_evaluated.v1",
                    actor=f"analyst-{suffix}",
                    action="evaluate",
                    resource=f"intervention/intv-{suffix}",
                    outcome="completed",
                    correlation_id=f"corr-standard-{suffix}",
                    occurred_at=old,
                )
            )
            service.export(
                EvidenceExportRequest(
                    program_id=f"program-{suffix}",
                    purpose=f"standard export {suffix}",
                    requested_by="auditor-a",
                    from_time=old - timedelta(days=1),
                    to_time=old + timedelta(days=1),
                    correlation_ids=(f"corr-standard-{suffix}",),
                    export_scope="tenant=t1",
                    data_classification="internal",
                    sensitive=False,
                ),
                decision_cards=(_ready_card(event.event_id),),
                generated_at=old,
            )
        held_id = store.list_for_program("program-held")[0].export_id
        purge_id = store.list_for_program("program-purge")[0].export_id

        app = create_app(persistence=bundle_persistence)
        client = TestClient(app, headers=AUDIT_HEADERS)

        spoofed = client.post(
            f"/audit/evidence/exports/{held_id}/legal-hold",
            headers={
                **AUDIT_HEADERS,
                "X-Correlation-ID": "corr-spoofed-legal-hold",
            },
            json={
                "role": "legal",
                "reason": "spoofed body role must not grant legal authority",
            },
        )
        assert spoofed.status_code == 403

        self_hold = client.post(
            f"/audit/evidence/exports/{held_id}/legal-hold",
            headers={
                **auth_headers(Role.FINANCE_LEGAL, subject="auditor-a"),
                "X-Correlation-ID": "corr-self-hold",
            },
            json={
                "role": "legal",
                "reason": "exporter cannot hold own export",
            },
        )
        assert self_hold.status_code == 422
        assert "exporter cannot apply legal hold" in self_hold.json()["detail"]

        held = client.post(
            f"/audit/evidence/exports/{held_id}/legal-hold",
            headers={
                **AUDIT_LEGAL_HEADERS,
                "X-Correlation-ID": "corr-legal-hold",
            },
            json={
                "role": "legal",
                "reason": "litigation hold",
                "correlation_id": "corr-legal-hold",
            },
        )
        assert held.status_code == 200
        assert held.json()["legal_hold"] is True
        assert held.json()["governance_log"][0]["actor"] == "legal-a"

        expired = client.get(
            "/audit/evidence/retention/expired",
            headers=AUDIT_HEADERS,
            params={"as_of": NOW.isoformat()},
        )
        assert expired.status_code == 200
        assert [item["export_id"] for item in expired.json()["exports"]] == [purge_id]

        purged = client.post(
            "/audit/evidence/retention/purge",
            headers={
                **AUDIT_RECORDS_HEADERS,
                "X-Correlation-ID": "corr-retention-purge",
            },
            json={
                "role": "records_manager",
                "reason": "scheduled retention sweep",
                "correlation_id": "corr-retention-purge",
                "as_of": NOW.isoformat(),
            },
        )
        assert purged.status_code == 200
        assert purged.json()["purged_export_ids"] == [purge_id]
        assert store.get(held_id) is not None
        purged_record = store.get(purge_id)
        assert purged_record is not None
        assert purged_record.purged_at is not None
        assert purged_record.bundle["purged"] is True
        assert store.verify_integrity().ok is True
    finally:
        bundle_persistence.engine.close()


def test_durable_evidence_store_rejects_bundle_tamper(db_path) -> None:
    bundle_persistence = _durable_bundle(db_path)
    try:
        service = AuditEvidenceExportService(
            audit_log=bundle_persistence.audit_log,
            evidence_store=bundle_persistence.evidence_store,
        )
        event = service.audit_log.record(
            AuditEvent(
                event_type="intervention.effect_evaluated.v1",
                actor="analyst-a",
                action="evaluate",
                resource="intervention/intv-001",
                outcome="completed",
                correlation_id="corr-evidence-persist-1",
                occurred_at=NOW,
            )
        )
        bundle = service.export(
            _request(),
            decision_cards=(_ready_card(event.event_id),),
            generated_at=NOW,
        )
        record = bundle_persistence.evidence_store.get(bundle.export_id)
        tampered = dict(record.bundle)
        tampered["program_id"] = "tampered-program"
        bundle_persistence.engine.execute(
            "UPDATE durable_evidence_bundles SET bundle_json = ? WHERE export_id = ?",
            (json.dumps(tampered), bundle.export_id),
        )

        with pytest.raises(EvidenceIntegrityError):
            bundle_persistence.evidence_store.get(bundle.export_id)
    finally:
        bundle_persistence.engine.close()


def test_durable_evidence_store_rejects_legal_hold_tamper(db_path) -> None:
    bundle_persistence = _durable_bundle(db_path)
    try:
        service = AuditEvidenceExportService(
            audit_log=bundle_persistence.audit_log,
            evidence_store=bundle_persistence.evidence_store,
        )
        bundle = service.export(
            EvidenceExportRequest(
                program_id="program-hold-tamper",
                purpose="legal hold tamper test",
                requested_by="reviewer-b",
                from_time=NOW - timedelta(days=1),
                to_time=NOW + timedelta(days=1),
                correlation_ids=("corr-hold-tamper",),
                export_scope="tenant=t1",
                data_classification="internal",
                sensitive=False,
            ),
            decision_cards=(_ready_card("evt-hold-tamper"),),
            generated_at=NOW,
        )
        held = bundle_persistence.evidence_store.apply_legal_hold(
            bundle.export_id,
            context=GovernedEvidenceOperation(
                actor="legal-a",
                role="legal",
                reason="litigation hold",
                correlation_id="corr-hold-tamper",
            ),
        )
        assert held.legal_hold is True
        assert bundle_persistence.evidence_store.verify_integrity().ok is True

        bundle_persistence.engine.execute(
            "UPDATE durable_evidence_bundles SET legal_hold = 0 WHERE export_id = ?",
            (bundle.export_id,),
        )

        with pytest.raises(EvidenceIntegrityError):
            bundle_persistence.evidence_store.get(bundle.export_id)
        assert bundle_persistence.evidence_store.verify_integrity().ok is False
    finally:
        bundle_persistence.engine.close()


def test_durable_audit_log_rejects_event_tamper(db_path) -> None:
    bundle_persistence = _durable_bundle(db_path)
    try:
        event = bundle_persistence.audit_log.record(
            AuditEvent(
                event_type="intervention.effect_evaluated.v1",
                actor="analyst-a",
                action="evaluate",
                resource="intervention/intv-001",
                outcome="completed",
                correlation_id="corr-audit-tamper-1",
                occurred_at=NOW,
                metadata={"evidence_level": "L3"},
            )
        )
        assert bundle_persistence.audit_log.verify_chain().ok is True
        bundle_persistence.engine.execute(
            "UPDATE durable_audit_events SET metadata_json = ? WHERE event_id = ?",
            (json.dumps({"evidence_level": "L0"}), event.event_id),
        )

        assert bundle_persistence.audit_log.verify_chain().ok is False
        with pytest.raises(AuditIntegrityError):
            bundle_persistence.audit_log.list_events(
                correlation_id="corr-audit-tamper-1"
            )
    finally:
        bundle_persistence.engine.close()


def test_durable_audit_log_serializes_concurrent_sequence_allocation(db_path) -> None:
    bundle_persistence = _durable_bundle(db_path)

    def record_event(index: int) -> AuditEvent:
        return bundle_persistence.audit_log.record(
            AuditEvent(
                event_type="learninghub.model_release.v1",
                actor=f"release-operator-{index}",
                action="request_release",
                resource=f"learninghub/release/{index}",
                outcome="accepted",
                correlation_id="corr-audit-concurrent",
                occurred_at=NOW + timedelta(seconds=index),
                metadata={"release_index": index},
            )
        )

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            events = list(executor.map(record_event, range(24)))

        assert sorted(event.sequence for event in events) == list(range(1, 25))
        assert bundle_persistence.audit_log.verify_chain().ok is True
    finally:
        bundle_persistence.engine.close()


def test_restore_replay_preserves_audit_and_retention_metadata(db_path, tmp_path) -> None:
    source = _durable_bundle(db_path)
    try:
        service = AuditEvidenceExportService(
            audit_log=source.audit_log,
            evidence_store=source.evidence_store,
        )
        event_ids: list[str] = []
        for index in range(2):
            event = service.audit_log.record(
                AuditEvent(
                    event_type="intervention.effect_evaluated.v1",
                    actor=f"analyst-{index}",
                    action="evaluate",
                    resource=f"intervention/intv-{index}",
                    outcome="completed",
                    correlation_id=f"corr-replay-{index}",
                    occurred_at=NOW + timedelta(minutes=index),
                    metadata={"evidence_level": "L3", "ordinal": index},
                )
            )
            event_ids.append(event.event_id)
            service.export(
                _request(
                    correlation_id=f"corr-replay-{index}",
                    requested_by=f"reviewer-{index}",
                    sensitive=index == 0,
                    classification="restricted" if index == 0 else "internal",
                ),
                decision_cards=(_ready_card(event.event_id),),
                generated_at=NOW + timedelta(minutes=index),
            )
        first_export_id = source.evidence_store.list_for_program("subsidy-program-2026-q2")[
            0
        ].export_id
        source.evidence_store.apply_legal_hold(
            first_export_id,
            context=GovernedEvidenceOperation(
                actor="legal-a",
                role="legal",
                reason="restore replay hold",
                correlation_id="corr-replay-hold",
            ),
        )
        audit_snapshot = source.audit_log.list_events()
        evidence_snapshot = source.evidence_store.list_all()
    finally:
        source.engine.close()

    target = _durable_bundle(tmp_path / "replayed.sqlite3")
    try:
        replayed_events = target.audit_log.replay(audit_snapshot)
        replayed_records = target.evidence_store.replay(evidence_snapshot)

        assert [event.event_id for event in replayed_events] == [
            event.event_id for event in audit_snapshot
        ]
        assert [event.event_hash for event in replayed_events] == [
            event.event_hash for event in audit_snapshot
        ]
        assert [event.actor for event in replayed_events if event.event_id in event_ids] == [
            "analyst-0",
            "analyst-1",
        ]
        assert [
            event.correlation_id for event in replayed_events if event.event_id in event_ids
        ] == [
            "corr-replay-0",
            "corr-replay-1",
        ]
        assert [record.record_hash for record in replayed_records] == [
            record.record_hash for record in evidence_snapshot
        ]
        assert [record.retention_class for record in replayed_records] == [
            record.retention_class for record in evidence_snapshot
        ]
        assert replayed_records[0].legal_hold is True
        assert replayed_records[0].governance_log[0]["operation"] == "legal_hold"
        assert target.audit_log.verify_chain().ok is True
        assert target.evidence_store.verify_integrity().ok is True
    finally:
        target.engine.close()


# -- API round-trips the persisted bundle -------------------------------------


def test_api_persists_and_serves_retained_evidence(db_path) -> None:
    bundle_persistence = _durable_bundle(db_path)
    try:
        event = bundle_persistence.audit_log.record(
            AuditEvent(
                event_type="learninghub.model_release.v1",
                actor="ml-owner",
                action="release",
                resource="model/forecast_revenue_interval:1.1.0",
                outcome="approved",
                correlation_id="corr-api-evidence-1",
                occurred_at=NOW,
            )
        )
        app = create_app(persistence=bundle_persistence)
        client = TestClient(app, headers=AUDIT_HEADERS)

        response = client.post(
            "/audit/evidence/export",
            headers={
                **auth_headers(Role.AUDITOR, subject="auditor-a"),
                "X-Correlation-ID": "corr-api-evidence-1",
            },
            json={
                "program_id": "subsidy-program-2026-q2",
                "purpose": "model release subsidy audit",
                "requested_by": "auditor-a",
                "from_time": (NOW - timedelta(hours=1)).isoformat(),
                "to_time": (NOW + timedelta(hours=1)).isoformat(),
                "correlation_ids": ["corr-api-evidence-1"],
                "export_scope": "tenant=t1;model=forecast_revenue_interval",
                "environment": "ci",
                "build_version": "test-build",
                "data_classification": "restricted",
                "sensitive": True,
                "purpose_scope": "model-release-subsidy-review",
                "expires_at": (NOW + timedelta(days=60)).isoformat(),
                "authorized_by": "legal-approver",
                "authorization_id": "authz-model-release-q2",
                "masking_profile": "masked",
                "decision_cards": [_ready_card(event.event_id).to_dict()],
            },
        )
        assert response.status_code == 201
        payload = response.json()
        export_id = payload["export_id"]
        assert payload["retention"]["retention_class"] == RETENTION_REGULATORY
        assert payload["identity_boundary_subject"] == "auditor-a"
        assert payload["export_governance"]["identity_boundary"] == "http-principal:auditor-a"
        assert payload["audit_chain"]["end"]

        # Retrieve the persisted bundle through the read endpoint.
        fetched = client.get(f"/audit/evidence/exports/{export_id}", headers=AUDIT_HEADERS)
        assert fetched.status_code == 200
        fetched_payload = fetched.json()
        assert fetched_payload["bundle_checksum"] == payload["bundle_checksum"]
        assert fetched_payload["requested_by"] == "auditor-a"
        assert fetched_payload["retention_class"] == RETENTION_REGULATORY
        assert fetched_payload["bundle"]["program_id"] == "subsidy-program-2026-q2"

        # Listing by program returns the summary.
        listed = client.get(
            "/audit/evidence/exports",
            headers=AUDIT_HEADERS,
            params={"program_id": "subsidy-program-2026-q2"},
        )
        assert listed.status_code == 200
        exports = listed.json()["exports"]
        assert [item["export_id"] for item in exports] == [export_id]
        assert exports[0]["bundle_checksum"] == payload["bundle_checksum"]

        unknown = client.get("/audit/evidence/exports/audit-export-missing", headers=AUDIT_HEADERS)
        assert unknown.status_code == 404
    finally:
        bundle_persistence.engine.close()
