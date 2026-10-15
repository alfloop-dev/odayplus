"""Real HTTP protocol checks against a local JSON API stub, not live GCS."""

import urllib.parse
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from shared.audit import AuditEvent
from shared.audit.worm import (
    AuditWormSinkError,
    GcsWormEvidenceSink,
    build_audit_worm_sink_from_env,
)
from tests.integration._gcs_worm_stub import GcsWormProtocolStub


@pytest.fixture
def gcs_worm_server():
    server = GcsWormProtocolStub()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def event():
    return AuditEvent(
        event_type="synthetic.gcs-recovery",
        actor="synthetic-worker",
        action="verify",
        resource="synthetic-record",
        outcome="accepted",
        correlation_id="synthetic-gcs-recovery",
    )


def sink(server, monkeypatch, *, recovery_token=None):
    result = GcsWormEvidenceSink(
        "gs://synthetic-bucket/audit prefix",
        token=server.writer_token,
        recovery_token=server.recovery_token if recovery_token is None else recovery_token,
    )
    server.bind(result, monkeypatch)
    return result


def test_first_create_needs_only_the_writer_credential(gcs_worm_server, monkeypatch, event):
    target = sink(gcs_worm_server, monkeypatch, recovery_token="")
    receipt = target.write_audit_event(event)
    assert receipt.record_id == event.event_id
    assert [r[0] for r in gcs_worm_server.requests] == ["POST"]
    assert gcs_worm_server.creations == 1


def test_retry_after_a_lost_response_verifies_original_content_and_timestamp(gcs_worm_server, monkeypatch, event):
    target = sink(gcs_worm_server, monkeypatch)
    gcs_worm_server.drop_after_create_event_type = event.event_type
    with pytest.raises(AuditWormSinkError):
        target.write_audit_event(event)
    original = deepcopy(gcs_worm_server.objects)
    receipt = target.write_audit_event(event)
    assert receipt.written_at == datetime.fromisoformat(next(iter(original.values()))["written_at"])
    assert gcs_worm_server.objects == original
    assert gcs_worm_server.creations == 1
    assert [r[0] for r in gcs_worm_server.requests] == ["POST", "POST", "GET"]
    assert gcs_worm_server.requests[-1][2] == "Bearer " + gcs_worm_server.recovery_token
    name = next(iter(original))
    assert urllib.parse.quote(name, safe="") in gcs_worm_server.requests[-1][1]


def test_existing_object_without_recovery_authority_fails_closed(gcs_worm_server, monkeypatch, event):
    target = sink(gcs_worm_server, monkeypatch, recovery_token="")
    target.write_audit_event(event)
    with pytest.raises(AuditWormSinkError, match="explicitly configured recovery credential"):
        target.write_audit_event(event)
    assert [r[0] for r in gcs_worm_server.requests] == ["POST", "POST"]
    assert gcs_worm_server.creations == 1


@pytest.mark.parametrize("status", [403, 404, 500])
def test_failed_readback_cannot_create_a_delivery_receipt(gcs_worm_server, monkeypatch, event, status):
    target = sink(gcs_worm_server, monkeypatch)
    target.write_audit_event(event)
    gcs_worm_server.read_status = status
    with pytest.raises(AuditWormSinkError, match=f"HTTP {status}"):
        target.write_audit_event(event)
    assert gcs_worm_server.creations == 1


@pytest.mark.parametrize("field,value", [
    ("record_type", "other-type"), ("record_id", "other-id"),
    ("sequence", 999), ("checksum", "0" * 64), ("payload", {"other": "data"}),
    ("written_at", "invalid"), ("written_at", "2026-01-01T00:00:00"),
])
def test_mismatched_object_is_rejected_without_overwriting(gcs_worm_server, monkeypatch, event, field, value):
    target = sink(gcs_worm_server, monkeypatch)
    target.write_audit_event(event)
    original = deepcopy(gcs_worm_server.objects)

    def transform(envelope):
        envelope[field] = value
        return envelope

    gcs_worm_server.read_transform = transform
    with pytest.raises(AuditWormSinkError):
        target.write_audit_event(event)
    assert gcs_worm_server.objects == original
    assert gcs_worm_server.creations == 1


def test_readback_redirect_does_not_forward_the_recovery_credential(gcs_worm_server, monkeypatch, event):
    target = sink(gcs_worm_server, monkeypatch)
    target.write_audit_event(event)
    gcs_worm_server.redirect_on_read = True
    with pytest.raises(AuditWormSinkError, match="HTTP 302"):
        target.write_audit_event(event)
    assert len(gcs_worm_server.requests) == 3
    assert not any("unapproved" in path for _, path, _ in gcs_worm_server.requests)


def test_non_precondition_upload_failure_does_not_attempt_readback(gcs_worm_server, monkeypatch, event):
    target = sink(gcs_worm_server, monkeypatch)
    gcs_worm_server.write_status = 503
    with pytest.raises(AuditWormSinkError, match="HTTP 503"):
        target.write_audit_event(event)
    assert [r[0] for r in gcs_worm_server.requests] == ["POST"]


def test_upload_redirect_does_not_forward_the_writer_credential(gcs_worm_server, monkeypatch, event):
    target = sink(gcs_worm_server, monkeypatch)
    gcs_worm_server.write_status = 302
    with pytest.raises(AuditWormSinkError, match="HTTP 302"):
        target.write_audit_event(event)
    assert [r[0] for r in gcs_worm_server.requests] == ["POST"]


def test_oversized_recovery_body_fails_without_unbounded_read(gcs_worm_server, monkeypatch, event):
    target = sink(gcs_worm_server, monkeypatch)
    target.write_audit_event(event)
    gcs_worm_server.read_transform = lambda row: {**row, "padding": "x" * 10000}
    with pytest.raises(AuditWormSinkError, match="unavailable or invalid"):
        target.write_audit_event(event)
    assert gcs_worm_server.creations == 1


def test_retained_evidence_replay_uses_the_same_verified_create_contract(gcs_worm_server, monkeypatch):
    target = sink(gcs_worm_server, monkeypatch)
    record = SimpleNamespace(export_id="synthetic-export", sequence=2, to_dict=lambda: {"export_id": "synthetic-export", "value": "synthetic"})
    first = target.write_retained_evidence(record)
    replay = target.write_retained_evidence(record)
    assert replay == first
    assert gcs_worm_server.creations == 1


def test_environment_configured_recovery_credential_is_used_only_for_readback(gcs_worm_server, monkeypatch, event):
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", "gs://synthetic-bucket/approved-prefix")
    monkeypatch.setenv("ODP_AUDIT_WORM_GCS_TOKEN", gcs_worm_server.writer_token)
    monkeypatch.setenv("ODP_AUDIT_WORM_GCS_RECOVERY_TOKEN", gcs_worm_server.recovery_token)
    target = build_audit_worm_sink_from_env()
    gcs_worm_server.bind(target, monkeypatch)
    target.write_audit_event(event)
    target.write_audit_event(event)
    assert [r[2] for r in gcs_worm_server.requests] == [
        "Bearer " + gcs_worm_server.writer_token,
        "Bearer " + gcs_worm_server.writer_token,
        "Bearer " + gcs_worm_server.recovery_token,
    ]
