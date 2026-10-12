"""Tenant audit reads: validate fixed ledger history BEFORE any projection/filter.

No caller-controlled tenant can replace a namespace's owning contract pin.
Errors are static; persisted poison is never hidden, rewritten or re-signed.
"""

from __future__ import annotations

from typing import Any

from shared.audit.events import AuditEvent

INVALID_LINEAGE = "RECOVERY_AUDIT_LINEAGE_INVALID"


def _ledger_contracts() -> dict[str, tuple[str, frozenset[str], str]]:
    # Runtime imports avoid identity/audit initialization cycles and honor each
    # owning contract (including independently pinned lifecycle fixtures).
    from shared.identity.dev_smoke_journal import (
        TENANT_ID as CREATION_TENANT_ID,
    )
    from shared.identity.dev_smoke_journal import DevSmokeBindingJournal, ProvisioningJournal
    from shared.identity.dev_smoke_recovery import (
        RECOVERY_BINDING_CORRELATION,
        RECOVERY_BINDING_KEYS,
        RECOVERY_BINDING_TYPE,
        TENANT_ID,
        RecoveryCapabilityService,
        RecoveryJournal,
    )

    return {
        ProvisioningJournal._CORRELATION: (
            ProvisioningJournal._TYPE,
            ProvisioningJournal._KEYS,
            CREATION_TENANT_ID,
        ),
        DevSmokeBindingJournal._CORRELATION: (
            DevSmokeBindingJournal._TYPE,
            DevSmokeBindingJournal._KEYS,
            CREATION_TENANT_ID,
        ),
        RecoveryJournal._CORRELATION: (RecoveryJournal._TYPE, RecoveryJournal._KEYS, TENANT_ID),
        RecoveryCapabilityService._CAP_CORRELATION: (
            RecoveryCapabilityService._CAP_TYPE,
            RecoveryCapabilityService._CAP_KEYS,
            TENANT_ID,
        ),
        RECOVERY_BINDING_CORRELATION: (RECOVERY_BINDING_TYPE, RECOVERY_BINDING_KEYS, TENANT_ID),
    }


def _validate(
    event: AuditEvent, correlation: str, contract: tuple[str, frozenset[str], str], tenant: str
) -> None:
    event_type, keys, owning_tenant = contract
    metadata = event.metadata
    if (
        tenant != owning_tenant
        or event.correlation_id != correlation
        or event.event_type != event_type
        or type(metadata) is not dict
        or set(metadata) != keys
        or metadata.get("tenant_id") != owning_tenant
        or metadata.get("secret_values_redacted") is not True
    ):
        raise ValueError(INVALID_LINEAGE)


def fixed_ledger_events(audit_log: Any, tenant: str) -> dict[str, AuditEvent]:
    events: dict[str, AuditEvent] = {}
    try:
        for correlation, contract in _ledger_contracts().items():
            if tenant != contract[2]:
                continue  # Never perform another tenant's global fixed-history read.
            for event in audit_log.list_events(correlation_id=correlation):
                _validate(event, correlation, contract, tenant)
                events[event.event_id] = event
    except (ValueError, TypeError):
        raise ValueError(INVALID_LINEAGE) from None
    return events


def validated_tenant_events(
    audit_log: Any, tenant: str, *, correlation_id: str | None = None
) -> list[AuditEvent]:
    """Shared by generic and administration reads; filtering is the LAST step."""
    if not isinstance(tenant, str) or not tenant.strip():
        raise ValueError("AUDIT_TENANT_REQUIRED")
    try:
        fixed = fixed_ledger_events(audit_log, tenant)
        selected = {event.event_id: event for event in audit_log.list_events(tenant_id=tenant)}
        selected.update(fixed)
        by_type = {
            contract[0]: (correlation, contract)
            for correlation, contract in _ledger_contracts().items()
        }
        for event in selected.values():
            contract = by_type.get(event.event_type)
            if contract is not None:
                _validate(event, *contract, tenant)
        return [
            event
            for event in selected.values()
            if correlation_id is None or event.correlation_id == correlation_id
        ]
    except (ValueError, TypeError):
        raise ValueError(INVALID_LINEAGE) from None
