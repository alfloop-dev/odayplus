"""Explicit commit evidence for trusted immutable-sink restore; never request input."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from shared.audit.integrity import AuditIntegrityError, verify_audit_event_integrity
from shared.audit.worm import committed_audit_checksum

LEDGER_TYPES = frozenset({"release.dev_smoke.reservation", "release.dev_smoke.binding",
    "identity.dev_smoke.recovery", "identity.dev_smoke.recovery_capability", "release.dev_smoke.recovery_binding"})


def reject_unproven_ledger_replay(events) -> list:
    values = list(events)
    if any(event.event_type in LEDGER_TYPES for event in values):
        raise AuditIntegrityError("AUDIT_COMMIT_EVIDENCE_REQUIRED")
    return values


def committed_envelope_event(envelope: dict[str, Any]):
    """Caller must authenticate immutable-sink transport; checksum is NOT auth.

    Legacy/unmarked WORM success objects cannot establish transaction commitment.
    No historical object is upgraded/rewritten or silently trusted by this parser.
    """
    from shared.audit.events import AuditEvent

    try:
        if (type(envelope) is not dict or envelope.get("schema_version") != 2
                or envelope.get("transaction_state") != "committed"
                or envelope.get("record_type") != "audit-events"):
            raise ValueError
        payload = envelope["payload"]
        required = {"event_id", "event_type", "actor", "action", "resource", "result", "outcome",
                    "correlation_id", "job_id", "metadata", "occurred_at", "integrity"}
        if (type(payload) is not dict or set(payload) != required
                or payload["outcome"] != payload["result"]
                or envelope["record_id"] != payload["event_id"]
                or envelope["checksum"] != committed_audit_checksum(payload)
                or type(payload["metadata"]) is not dict):
            raise ValueError
        integrity = payload["integrity"]
        keys = {"sequence", "previous_hash", "event_hash", "signature_key_id", "signature_version",
                "signature_alg", "worm_sink_id"}
        if (type(integrity) is not dict or set(integrity) != keys
                or type(integrity["sequence"]) is not int or integrity["sequence"] < 1
                or envelope["sequence"] != integrity["sequence"]):
            raise ValueError
        event = AuditEvent(**{key: payload[key] for key in required - {"result", "occurred_at", "integrity"}},
                           occurred_at=datetime.fromisoformat(payload["occurred_at"]), **integrity)
        if event.occurred_at.tzinfo is None or not verify_audit_event_integrity(event):
            raise ValueError
        return event
    except (ValueError, TypeError, KeyError):
        raise AuditIntegrityError("AUDIT_COMMIT_EVIDENCE_INVALID") from None
