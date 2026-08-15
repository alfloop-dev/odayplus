"""Audit-event pipeline hooks for observability.

This module keeps the canonical audit record from :mod:`shared.audit.events`
and adds the ODP-SD-11 pipeline behavior around it: validation, append-only
sink writes, structured logs, metrics, dead-letter replay, and evidence bundle
checksums.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from shared.audit.events import AuditEvent, InMemoryAuditLog
from shared.observability.logging import StructuredLogger
from shared.observability.metrics import MetricsRegistry, default_registry

AUDIT_EVIDENCE_EXPORT_EVENT_TYPE = "audit.evidence_export.v1"
HIGH_RISK_AUDIT_ACTIONS = frozenset(
    {"approve", "execute", "publish", "override", "rollback", "export"}
)
REASON_METADATA_KEYS = frozenset({"reason", "reason_code", "comment"})


class AuditPipelineError(RuntimeError):
    """Raised when the audit pipeline cannot durably write an event."""


class AuditValidationError(ValueError):
    """Raised when an event is missing required audit fields."""


@runtime_checkable
class AuditSink(Protocol):
    """Append-only audit sink interface.

    ``shared.audit.InMemoryAuditLog`` already implements this protocol. A Cloud
    SQL/BigQuery writer can implement the same ``record`` method later.
    """

    def record(self, event: AuditEvent) -> AuditEvent: ...


@dataclass(frozen=True)
class DeadLetterAuditEvent:
    event: AuditEvent
    error_class: str
    error_message: str
    failed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    retryable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.to_dict(),
            "error_class": self.error_class,
            "error_message": self.error_message,
            "failed_at": self.failed_at.isoformat(),
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class AuditCompletenessRule:
    name: str
    required_event_types: tuple[str, ...]
    correlation_id: str | None = None
    resource: str | None = None


@dataclass(frozen=True)
class AuditCompletenessReport:
    rule: AuditCompletenessRule
    observed_event_types: tuple[str, ...]
    missing_event_types: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_event_types

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule.name,
            "correlation_id": self.rule.correlation_id,
            "resource": self.rule.resource,
            "complete": self.complete,
            "observed_event_types": list(self.observed_event_types),
            "missing_event_types": list(self.missing_event_types),
        }


@dataclass(frozen=True)
class AuditEvidenceBundle:
    correlation_id: str
    scope: str
    generated_by: str
    reason: str
    events: tuple[dict[str, Any], ...]
    bundle_checksum: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "scope": self.scope,
            "generated_by": self.generated_by,
            "reason": self.reason,
            "events": list(self.events),
            "bundle_checksum": self.bundle_checksum,
            "generated_at": self.generated_at.isoformat(),
        }


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _event_sort_key(event: AuditEvent) -> tuple[str, str]:
    return (event.occurred_at.isoformat(), event.event_id)


def build_audit_event(
    *,
    event_type: str,
    actor_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    result: str,
    correlation_id: str,
    actor_type: str = "user",
    actor_role_snapshot: str | None = None,
    tenant_id: str | None = None,
    scope: str | None = None,
    before_ref: str | None = None,
    after_ref: str | None = None,
    reason_code: str | None = None,
    comment: str | None = None,
    policy_version: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    occurred_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AuditEvent:
    """Build a canonical ``AuditEvent`` with ODP-SD-11 metadata fields."""

    event_metadata = {
        "actor_type": actor_type,
        "actor_role_snapshot": actor_role_snapshot,
        "tenant_id": tenant_id,
        "scope": scope,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "before_ref": before_ref,
        "after_ref": after_ref,
        "reason_code": reason_code,
        "comment": comment,
        "policy_version": policy_version,
        "ip": ip,
        "user_agent": user_agent,
    }
    event_metadata.update(dict(metadata or {}))
    return AuditEvent(
        event_type=event_type,
        actor=actor_id,
        action=action,
        resource=f"{entity_type}/{entity_id}",
        outcome=result,
        correlation_id=correlation_id,
        metadata={key: value for key, value in event_metadata.items() if value is not None},
        occurred_at=occurred_at or datetime.now(UTC),
    )


def build_evidence_bundle(
    events: Iterable[AuditEvent],
    *,
    correlation_id: str,
    generated_by: str,
    reason: str,
    scope: str = "correlation",
) -> AuditEvidenceBundle:
    """Create a deterministic evidence bundle for one correlation id."""

    selected = sorted(
        (event for event in events if event.correlation_id == correlation_id),
        key=_event_sort_key,
    )
    payloads = tuple(event.to_dict() for event in selected)
    checksum_payload = {
        "correlation_id": correlation_id,
        "scope": scope,
        "events": payloads,
    }
    checksum = hashlib.sha256(_canonical_json(checksum_payload).encode("utf-8")).hexdigest()
    return AuditEvidenceBundle(
        correlation_id=correlation_id,
        scope=scope,
        generated_by=generated_by,
        reason=reason,
        events=payloads,
        bundle_checksum=checksum,
    )


def check_audit_completeness(
    events: Iterable[AuditEvent],
    rule: AuditCompletenessRule,
) -> AuditCompletenessReport:
    """Check whether all event types required by ``rule`` are present."""

    filtered = []
    for event in events:
        if rule.correlation_id is not None and event.correlation_id != rule.correlation_id:
            continue
        if rule.resource is not None and event.resource != rule.resource:
            continue
        filtered.append(event)
    observed = tuple(dict.fromkeys(event.event_type for event in sorted(filtered, key=_event_sort_key)))
    missing = tuple(event_type for event_type in rule.required_event_types if event_type not in observed)
    return AuditCompletenessReport(
        rule=rule,
        observed_event_types=observed,
        missing_event_types=missing,
    )


class AuditPipeline:
    """Records canonical audit events through observable pipeline hooks."""

    def __init__(
        self,
        *,
        sink: AuditSink | None = None,
        metrics: MetricsRegistry | None = None,
        logger: StructuredLogger | None = None,
        service: str = "audit-pipeline",
        raise_on_failure: bool = True,
    ) -> None:
        self.sink = sink if sink is not None else InMemoryAuditLog()
        self.metrics = metrics if metrics is not None else default_registry()
        self.logger = logger if logger is not None else StructuredLogger(service)
        self.raise_on_failure = raise_on_failure
        self._dead_letter: list[DeadLetterAuditEvent] = []

    @property
    def dead_letter(self) -> tuple[DeadLetterAuditEvent, ...]:
        return tuple(self._dead_letter)

    def record(self, event: AuditEvent) -> AuditEvent:
        try:
            self._validate(event)
        except AuditValidationError as exc:
            self._record_failure(event, exc, retryable=False)
            raise

        try:
            recorded = self.sink.record(event)
        except Exception as exc:
            self._record_failure(event, exc, retryable=True)
            if self.raise_on_failure:
                raise AuditPipelineError(f"audit event write failed: {exc}") from exc
            return event

        self._record_success(recorded)
        return recorded

    def record_export(
        self,
        *,
        actor_id: str,
        resource: str,
        correlation_id: str,
        reason: str,
        scope: str,
        result: str = "success",
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        export_metadata = {"reason": reason, "scope": scope}
        export_metadata.update(dict(metadata or {}))
        return self.record(
            AuditEvent(
                event_type=AUDIT_EVIDENCE_EXPORT_EVENT_TYPE,
                actor=actor_id,
                action="export",
                resource=resource,
                outcome=result,
                correlation_id=correlation_id,
                metadata=export_metadata,
            )
        )

    def replay_failed(self) -> int:
        pending = list(self._dead_letter)
        self._dead_letter.clear()
        replayed = 0
        for dead in pending:
            try:
                self.record(dead.event)
            except (AuditPipelineError, AuditValidationError):
                self.metrics.increment("audit_event_replay_count", labels={"result": "failure"})
                continue
            replayed += 1
            self.metrics.increment("audit_event_replay_count", labels={"result": "success"})
        return replayed

    def record_completeness_report(self, report: AuditCompletenessReport) -> None:
        if report.complete:
            return
        resource = report.rule.resource or "all"
        for missing_event_type in report.missing_event_types:
            self.metrics.increment(
                "audit_completeness_gap_count",
                labels={
                    "rule": report.rule.name,
                    "resource": resource,
                    "missing_event_type": missing_event_type,
                },
            )
            self.logger.warning(
                "audit completeness gap",
                correlation_id=report.rule.correlation_id or "unknown",
                actor="system",
                resource=resource,
                action="audit_completeness_check",
                result="gap",
                extra=report.to_dict(),
            )

    def _validate(self, event: AuditEvent) -> None:
        required = {
            "event_type": event.event_type,
            "actor": event.actor,
            "action": event.action,
            "resource": event.resource,
            "outcome": event.outcome,
            "correlation_id": event.correlation_id,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise AuditValidationError(f"audit event missing required fields: {', '.join(missing)}")
        if event.action in HIGH_RISK_AUDIT_ACTIONS and not (
            REASON_METADATA_KEYS & set(event.metadata)
        ):
            raise AuditValidationError(
                f"audit event action {event.action!r} requires reason metadata"
            )

    def _record_success(self, event: AuditEvent) -> None:
        self.metrics.increment(
            "audit_event_record_count",
            labels={
                "event_type": event.event_type,
                "action": event.action,
                "result": event.outcome,
            },
        )
        self.metrics.observe(
            "audit_event_pipeline_lag_seconds",
            max((datetime.now(UTC) - event.occurred_at).total_seconds(), 0.0),
            labels={"sink": type(self.sink).__name__, "event_type": event.event_type},
        )
        if event.action == "export" or event.event_type == AUDIT_EVIDENCE_EXPORT_EVENT_TYPE:
            self.metrics.increment(
                "audit_evidence_export_count",
                labels={
                    "scope": str(event.metadata.get("scope", "unknown")),
                    "result": event.outcome,
                },
            )
        self.logger.info(
            "audit event recorded",
            correlation_id=event.correlation_id,
            actor=event.actor,
            resource=event.resource,
            action=event.action,
            result=event.outcome,
            extra={"event_id": event.event_id, "event_type": event.event_type},
        )

    def _record_failure(self, event: AuditEvent, exc: Exception, *, retryable: bool) -> None:
        self._dead_letter.append(
            DeadLetterAuditEvent(
                event=event,
                error_class=type(exc).__name__,
                error_message=str(exc),
                retryable=retryable,
            )
        )
        self.metrics.increment(
            "audit_event_write_failure_count",
            labels={
                "event_type": event.event_type or "unknown",
                "action": event.action or "unknown",
                "error_class": type(exc).__name__,
            },
        )
        self.logger.error(
            "audit event write failed",
            correlation_id=event.correlation_id or "unknown",
            actor=event.actor or "system",
            resource=event.resource or "audit",
            action=event.action or "record",
            error_code=type(exc).__name__,
            retryable=retryable,
            extra={"event_type": event.event_type, "error": str(exc)},
        )
