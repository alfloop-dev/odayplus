"""Commit/publication/replay boundaries with local-only SQLite WORM."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from shared.audit.events import AuditEvent, InMemoryAuditLog
from shared.audit.integrity import AuditIntegrityError
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.observability.correlation import CorrelationContext, request_correlation_id


def event() -> AuditEvent:
    return AuditEvent(event_type="identity.dev_smoke.recovery", actor="offline", action="RESERVE",
        resource="offline", outcome="success", correlation_id="dev-smoke-recovery:offline", metadata={"offline": True})


def test_nested_transaction_cannot_publish_before_commit_and_replay_needs_commit_evidence(tmp_path: Path) -> None:
    engine = SqliteEngine(tmp_path / "source.db")
    sink = LocalAppendOnlyWormSink(tmp_path / "worm")
    audit = DurableAuditLog(engine, worm_sink=sink)
    try:
        with engine.transaction():
            value = audit.record(event())
            assert not list((tmp_path / "worm").rglob("*.json"))
            with pytest.raises(RuntimeError, match="PUBLICATION_REFUSED"):
                audit.flush_worm()
        envelopes = [json.loads(p.read_text()) for p in (tmp_path / "worm").rglob("*.json")]
        assert len(envelopes) == 1 and envelopes[0]["transaction_state"] == "committed"
        assert audit.flush_worm() == 0
        assert engine.query_one("SELECT state FROM durable_audit_worm_outbox")["state"] == "delivered"
        restored_engine = SqliteEngine(tmp_path / "restore.db")
        try:
            restored = DurableAuditLog(restored_engine)
            with pytest.raises(AuditIntegrityError, match="COMMIT_EVIDENCE_REQUIRED"):
                restored.replay([value])
            legacy = dict(envelopes[0])
            legacy.pop("transaction_state")
            with pytest.raises(AuditIntegrityError, match="COMMIT_EVIDENCE_INVALID"):
                restored.replay_committed_envelopes([legacy])
            assert restored.list_events() == []
            got = restored.replay_committed_envelopes(envelopes)
            assert got[0].event_hash == value.event_hash and restored.verify_chain().ok
            with pytest.raises(AuditIntegrityError, match="COMMIT_EVIDENCE_REQUIRED"):
                InMemoryAuditLog().replay([value])
        finally:
            restored_engine.close()
    finally:
        engine.close()


def test_rollback_discards_callbacks_audit_and_outbox(tmp_path: Path) -> None:
    engine = SqliteEngine(tmp_path / "source.db")
    audit = DurableAuditLog(engine, worm_sink=LocalAppendOnlyWormSink(tmp_path / "worm"))
    try:
        with pytest.raises(ValueError):
            with engine.transaction():
                audit.record(event())
                raise ValueError("offline-rollback")
        assert audit.list_events() == []
        assert engine.query("SELECT * FROM durable_audit_worm_outbox") == []
        assert not list((tmp_path / "worm").rglob("*.json"))
        audit.record(event())  # rolled-back callback cannot leak into a later txn
        assert len(list((tmp_path / "worm").rglob("*.json"))) == 1
    finally:
        engine.close()


def test_lost_worm_reply_survives_restart_as_unknown_without_retry(tmp_path: Path) -> None:
    class LostReply(LocalAppendOnlyWormSink):
        calls = 0
        def write_committed_audit_event(self, value):
            self.calls += 1
            super().write_committed_audit_event(value)
            raise OSError("PRIVATE-EXCEPTION-MARKER")
    engine = SqliteEngine(tmp_path / "source.db")
    sink = LostReply(tmp_path / "worm")
    audit = DurableAuditLog(engine, worm_sink=sink)
    audit.record(event())
    assert audit.flush_worm() == 0 and sink.calls == 1
    assert engine.query_one("SELECT state FROM durable_audit_worm_outbox")["state"] == "unknown"
    engine.close()
    engine = SqliteEngine(tmp_path / "source.db")
    try:
        assert DurableAuditLog(engine, worm_sink=sink).flush_worm() == 0 and sink.calls == 1
        assert len(list((tmp_path / "worm").rglob("*.json"))) == 1
        assert "PRIVATE-EXCEPTION-MARKER" not in json.dumps(engine.query("SELECT * FROM durable_audit_worm_outbox"), default=dict)
    finally:
        engine.close()


def test_pending_commit_can_publish_after_process_callback_loss(tmp_path: Path) -> None:
    engine = SqliteEngine(tmp_path / "source.db")
    sink = LocalAppendOnlyWormSink(tmp_path / "worm")
    audit = DurableAuditLog(engine, worm_sink=sink)
    with engine.transaction():
        audit.record(event())
        engine._commit_callbacks.clear()  # model process loss after durable commit before callback
    engine.close()
    engine = SqliteEngine(tmp_path / "source.db")
    try:
        assert DurableAuditLog(engine, worm_sink=sink).flush_worm() == 1
        assert len(list((tmp_path / "worm").rglob("*.json"))) == 1
    finally:
        engine.close()


@pytest.mark.parametrize("prefix", ["dev-smoke-recovery:", "dev-smoke-recovery-capability:",
    "dev-smoke-recovery-binding:", "dev-smoke-provisioning:", "dev-smoke-binding:"])
def test_reserved_request_namespaces_are_consistent_and_direct_appends_refused(prefix: str) -> None:
    value = "  " + prefix + "offline  "
    safe = request_correlation_id(value)
    assert safe.startswith("request-") and CorrelationContext.from_header(value).correlation_id == safe
    audit = InMemoryAuditLog()
    with pytest.raises(ValueError, match="^AUDIT_RESERVED_CORRELATION_REFUSED$"):
        audit.record(replace(event(), event_type="security.authentication", correlation_id=prefix + "offline"))
    assert audit.list_events() == []
    assert request_correlation_id("corr-ordinary") == "corr-ordinary"
