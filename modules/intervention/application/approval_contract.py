"""Immutable, actor-bound native approval read context.

The digest is a concurrency precondition, not an authorization credential.
Authorization always resolves the current canonical store and Principal again.
"""

from __future__ import annotations

from typing import Any

from modules.intervention.domain.lifecycle import Intervention, InterventionError
from shared.api.idempotency import request_fingerprint


class InterventionOwnerIntegrityError(InterventionError):
    """Relational ownership and document indexing disagree; no fallback read."""


def require_positive_intervention_version(value: Any) -> int:
    """Validate the actual persisted/next type without normalizing corrupt data."""
    if type(value) is not int or value < 1:
        raise InterventionError("stale update: intervention version must be a positive built-in integer")
    return value


def approval_read_context(
    intervention: Intervention, *, tenant_id: str, actor_subject_id: str
) -> dict[str, Any]:
    require_positive_intervention_version(intervention.version)
    return {
        "schema_version": 1,
        "intervention_id": intervention.intervention_id,
        "store_id": intervention.store_id,
        "tenant_id": tenant_id,
        "actor_subject_id": actor_subject_id,
        "version": intervention.version,
        "policy_version": intervention.policy_version,
        "snapshot_sha256": request_fingerprint(intervention.to_dict()),
    }


def verified_creator_subject(intervention: Intervention, *, tenant_id: str, audit_log: Any) -> str | None:
    """Only new Principal-bound creation evidence attests to its creator.

    Legacy body-supplied create audits or coincident security correlations do
    not establish this attestation and are never upgraded by this reader.
    """
    evidence = [event for event in audit_log.list_events(tenant_id=tenant_id)
        if event.event_type == "intervention.lifecycle.v1"
        and event.resource == f"intervention/{intervention.intervention_id}"
        and event.action == "create"
        and event.metadata.get("actor_evidence") == "authenticated_principal.v1"
        and event.metadata.get("actor_subject_id") == event.actor
        and event.metadata.get("store_id") == intervention.store_id
        and event.actor == intervention.created_by]
    return evidence[0].actor if len(evidence) == 1 else None
