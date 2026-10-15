from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

CORRELATION_ID_HEADER = "x-correlation-id"

# Server-owned ledger indexes, never request tracing identifiers.
_RESERVED_LEDGER_CORRELATIONS = {
    "dev-smoke-provisioning:": "release.dev_smoke.reservation",
    "dev-smoke-binding:": "release.dev_smoke.binding",
    "dev-smoke-recovery:": "identity.dev_smoke.recovery",
    "dev-smoke-recovery-capability:": "identity.dev_smoke.recovery_capability",
    "dev-smoke-recovery-binding:": "release.dev_smoke.recovery_binding",
}


def request_correlation_id(value: str | None) -> str:
    """Keep ordinary trace IDs; deterministically separate reserved namespaces."""
    import hashlib
    clean = value.strip() if isinstance(value, str) else ""
    if not clean:
        return new_correlation_id()
    if any(clean.startswith(prefix) for prefix in _RESERVED_LEDGER_CORRELATIONS):
        return "request-" + hashlib.sha256(clean.encode()).hexdigest()
    return clean


def validate_audit_correlation(event_type: str, correlation_id: str) -> None:
    """Last append barrier for callers bypassing HTTP/auth normalization."""
    for prefix, ledger_type in _RESERVED_LEDGER_CORRELATIONS.items():
        if correlation_id.startswith(prefix) and event_type != ledger_type:
            raise ValueError("AUDIT_RESERVED_CORRELATION_REFUSED")


def new_correlation_id() -> str:
    return str(uuid4())


@dataclass(frozen=True)
class CorrelationContext:
    correlation_id: str = field(default_factory=new_correlation_id)

    @classmethod
    def from_header(cls, value: str | None) -> CorrelationContext:
        return cls(correlation_id=request_correlation_id(value))
