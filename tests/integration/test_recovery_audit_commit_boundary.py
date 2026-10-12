"""Disposable PG / local or intercepted GCS audit tests, never live recovery."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shared.audit.worm import GcsWormEvidenceSink, LocalAppendOnlyWormSink
from shared.identity.dev_smoke_journal import DevSmokeBindingJournal, ProvisioningJournal
from shared.identity.dev_smoke_recovery import (
    RECOVERY_BINDING_CORRELATION,
    RecoveryCapabilityService,
    RecoveryJournal,
    RecoveryRefused,
)
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from tests.integration.test_dev_smoke_recovery import (
    binding_proven,
    binding_ready,
    consume,
    issue,
    snapshot,
)
from tests.integration.test_dev_smoke_recovery import identity_stack as identity_stack
from tests.integration.test_dev_smoke_recovery import recovery as recovery
from tests.integration.test_dev_smoke_recovery import recovery_runtime as recovery_runtime


@pytest.mark.parametrize("correlation", [RecoveryJournal._CORRELATION, RecoveryCapabilityService._CAP_CORRELATION,
    RECOVERY_BINDING_CORRELATION, ProvisioningJournal._CORRELATION, DevSmokeBindingJournal._CORRELATION])
def test_untrusted_request_cannot_append_to_reserved_ledger_correlation(recovery_runtime: Any, correlation: str) -> None:
    s = recovery_runtime
    before = [e.to_dict() for e in s.audit.list_events(correlation_id=correlation)]
    # Authentication failure ALSO records audit before authorization completes.
    response = s.client.get("/api/v1/operator/users/audit-trail", headers={"x-correlation-id": correlation})
    assert response.status_code == 401
    assert [e.to_dict() for e in s.audit.list_events(correlation_id=correlation)] == before, "caller poisoned fixed ledger"
    good = s.client.get("/api/v1/operator/users/audit-trail", headers=s.headers)
    assert good.status_code == 200
    assert s.audit.verify_chain().ok


@pytest.mark.parametrize("sink_kind", ["local", "gcs"])
@pytest.mark.parametrize("operation", ["reserve", "issue", "rotate", "intent", "ack"])
def test_real_pg_commit_rollback_never_publishes_worm_success(
        recovery: Any, monkeypatch: Any, tmp_path: Path, sink_kind: str, operation: str) -> None:
    s = recovery
    if operation == "issue":
        root = s.recovery.reserve(s.principal, s.plan)
    elif operation == "rotate":
        root, cap = issue(s)
    elif operation in {"intent", "ack"}:
        root, cap = binding_ready(s)
        if operation == "ack":
            intent = s.binding.binding_intent(s.principal, root)
    records = []
    if sink_kind == "local":
        sink = LocalAppendOnlyWormSink(tmp_path / "worm")
    else:
        sink = GcsWormEvidenceSink("gs://offline-never-network/audit", token="OFFLINE-TEST-ONLY")
        class Reply:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        def intercept(request, **kwargs):
            records.append(json.loads(request.data))
            return Reply()
        monkeypatch.setattr("urllib.request.urlopen", intercept)
    audit = DurableAuditLog(s.engine, worm_sink=sink)
    for service in (s.recovery, s.capabilities, s.binding):
        service._audit = audit
    event_type = RecoveryJournal._TYPE if operation == "reserve" else RecoveryCapabilityService._CAP_TYPE if operation in {"issue", "rotate"} else "release.dev_smoke.recovery_binding"
    with s.db.connect(autocommit=True) as conn:
        conn.execute("""
            CREATE FUNCTION odp_runtime.refuse_worm_commit() RETURNS trigger AS $$
            BEGIN
                IF NEW.event_type = TG_ARGV[0] THEN
                    RAISE EXCEPTION 'offline-deferred-commit-refusal';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        conn.execute(f"""
            CREATE CONSTRAINT TRIGGER worm_commit_failure
            AFTER INSERT ON odp_runtime.durable_audit_events
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION odp_runtime.refuse_worm_commit('{event_type}');
        """)
    before = snapshot(s)
    count = s.engine.query_one("SELECT count(*) AS n FROM durable_audit_events")["n"]
    with pytest.raises(RecoveryRefused):
        if operation == "reserve":
            s.recovery.reserve(s.principal, s.plan)
        elif operation == "issue":
            s.capabilities.issue(s.principal, root)
        elif operation == "rotate":
            consume(s, root, cap)
        elif operation == "intent":
            s.binding.binding_intent(s.principal, root)
        else:
            s.binding.binding_acknowledge(s.principal, root, intent_event_id=intent["audit_event_id"], put_status=201)
    assert snapshot(s) == before
    assert s.engine.query_one("SELECT count(*) AS n FROM durable_audit_events")["n"] == count
    if sink_kind == "local":
        records = [json.loads(path.read_text()) for path in (tmp_path / "worm").rglob("*.json")]
    assert records == [], "rolled-back transaction left immutable success in WORM"
    assert s.engine.query("SELECT * FROM durable_audit_worm_outbox") == []


def test_late_db_original_session_expiry_after_audit_insert_rolls_back_without_worm(recovery: Any, monkeypatch: Any, tmp_path: Path) -> None:
    s = recovery
    audit = DurableAuditLog(s.engine, worm_sink=LocalAppendOnlyWormSink(tmp_path / "worm"))
    s.recovery._audit = audit
    record = audit.record
    def late_record(value):
        result = record(value)
        # Final fence reads DB clock/session, NOT Python identity._now. Expire
        # that actual persisted session after provisional event+intent insertion.
        s.engine.execute("UPDATE identity.sessions SET idle_expires_at = clock_timestamp() - interval '1 second' WHERE session_id = ?",
                         (value.metadata["issuer_session_id"],))
        return result
    monkeypatch.setattr(audit, "record", late_record)
    count = s.engine.query_one("SELECT count(*) AS n FROM durable_audit_events")["n"]
    before = snapshot(s)
    with pytest.raises(RecoveryRefused):
        s.recovery.reserve(s.principal, s.plan)
    assert snapshot(s) == before
    assert s.engine.query_one("SELECT count(*) AS n FROM durable_audit_events")["n"] == count
    assert s.engine.query("SELECT * FROM durable_audit_worm_outbox") == []
    assert not list((tmp_path / "worm").rglob("*.json"))


@pytest.mark.parametrize("correlation", [ProvisioningJournal._CORRELATION, DevSmokeBindingJournal._CORRELATION])
@pytest.mark.parametrize("tenant", [None, "other-tenant", "matching"])
def test_already_persisted_legacy_auth_poison_cannot_hide_behind_valid_ack(recovery_runtime: Any, correlation: str, tenant: str | None) -> None:
    from shared.audit.events import AuditEvent
    from shared.audit.integrity import attach_audit_event_integrity
    from shared.identity.dev_smoke_journal import TENANT_ID
    from shared.identity.dev_smoke_recovery import PRESERVED_ACCOUNT_ID, TARGET_ACCOUNT_ID
    s = recovery_runtime
    root, cap = binding_ready(s)
    intent = s.binding.binding_intent(s.principal, root)
    s.binding.binding_acknowledge(s.principal, root, intent_event_id=intent["audit_event_id"], put_status=201)
    assert binding_proven(s, root, cap, intent)
    previous = s.audit.list_events()[-1]
    # Restore an already-persisted, INTEGRITY-VALID historical row below the new
    # append barrier. No future raw request/audit writer is permitted to do this.
    meta = {} if tenant is None else {"tenant_id": TENANT_ID if tenant == "matching" else tenant}
    poisoned = attach_audit_event_integrity(AuditEvent(event_type="security.authentication", actor="offline-other",
        action="AUTHENTICATE", resource="offline", outcome="denied", correlation_id=correlation, metadata=meta),
        sequence=previous.sequence + 1, previous_hash=previous.event_hash)
    s.engine.execute("INSERT INTO durable_audit_events(event_id,event_type,actor,action,resource,outcome,correlation_id,job_id,metadata_json,occurred_at,sequence,previous_hash,event_hash,signature_key_id,signature_version,signature_alg,worm_sink_id) VALUES (?,?,?,?,?,?,?,?,CAST(? AS jsonb),?,?,?,?,?,?,?,?)",
        (poisoned.event_id, poisoned.event_type, poisoned.actor, poisoned.action, poisoned.resource, poisoned.outcome,
         poisoned.correlation_id, poisoned.job_id, json.dumps(poisoned.metadata), poisoned.occurred_at.isoformat(),
         poisoned.sequence, poisoned.previous_hash, poisoned.event_hash, poisoned.signature_key_id,
         poisoned.signature_version, poisoned.signature_alg, poisoned.worm_sink_id))
    assert s.audit.verify_chain().ok
    before = snapshot(s)
    for subject in (None, TARGET_ACCOUNT_ID, PRESERVED_ACCOUNT_ID):
        response = s.client.get("/api/v1/operator/users/audit-trail", headers=s.headers,
                               params={} if subject is None else {"subject_id": subject})
        assert response.status_code == 403 and "RECOVERY_AUDIT_LINEAGE_INVALID" in response.text
    assert snapshot(s) == before
    assert any(e.event_id == poisoned.event_id for e in s.audit.list_events(correlation_id=correlation))


def test_pg_pool_size_one_commits_before_publication_and_claim_visible_to_other_pool(recovery: Any, tmp_path: Path) -> None:
    from shared.audit.events import AuditEvent
    from shared.infrastructure.persistence.postgresql import PostgresEngine
    s = recovery
    engine = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False, min_pool_size=1, max_pool_size=1)
    witness = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    observed = []
    class Sink(LocalAppendOnlyWormSink):
        def write_committed_audit_event(self, value):
            assert not engine.in_transaction
            assert witness.query_one("SELECT event_hash FROM durable_audit_events WHERE event_id = ?", (value.event_id,))["event_hash"] == value.event_hash
            assert witness.query_one("SELECT state FROM durable_audit_worm_outbox WHERE event_id = ?", (value.event_id,))["state"] == "attempted"
            observed.append(value.event_id)
            return super().write_committed_audit_event(value)
    try:
        audit = DurableAuditLog(engine, worm_sink=Sink(tmp_path / "worm"))
        with engine.lock:
            value = audit.record(AuditEvent(event_type="security.offline", actor="offline", action="TEST",
                resource="offline", outcome="success", correlation_id="corr-offline"))
            assert observed == []
        assert observed == [value.event_id]  # no borrowed-connection deadlock
        assert audit.flush_worm() == 0
    finally:
        engine.close()
        witness.close()


def test_pg_outbox_claim_is_once_across_two_publishers(recovery: Any, monkeypatch: Any, tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from shared.audit.events import AuditEvent
    from shared.infrastructure.persistence.postgresql import PostgresEngine
    s = recovery
    other = PostgresEngine(s.db.url(), bootstrap=False, validate_schema=False)
    sink = LocalAppendOnlyWormSink(tmp_path / "worm")
    try:
        audit = DurableAuditLog(s.engine, worm_sink=sink)
        second = DurableAuditLog(other, worm_sink=sink)
        with monkeypatch.context() as patch:
            patch.setattr(s.engine, "after_commit", lambda callback: None)
            audit.record(AuditEvent(event_type="security.offline", actor="offline", action="TEST",
                resource="offline", outcome="success", correlation_id="corr-offline"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda log: log.flush_worm(), (audit, second)))
        assert sorted(results) == [0, 1]
        assert len(list((tmp_path / "worm").rglob("*.json"))) == 1
        assert s.engine.query_one("SELECT state FROM durable_audit_worm_outbox")["state"] == "delivered"
    finally:
        other.close()
