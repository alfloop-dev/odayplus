from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from modules.listing.domain.identity_graph import IdentityGraph, IdentityLineage, SourceIdentity

ODAY_ID_NAMESPACE = "https://oday.plus/source-identity"


@dataclass(frozen=True)
class IdentityKey:
    entity_type: str
    source_id: str
    source_entity_id: str
    tenant_id: str = ""

    @property
    def fingerprint(self) -> str:
        parts = [
            self.tenant_id.strip().lower(),
            self.entity_type.strip().lower(),
            self.source_id.strip().lower(),
            self.source_entity_id.strip().lower(),
        ]
        return "|".join(parts)


@dataclass(frozen=True)
class IdentityResolution:
    entity_type: str
    canonical_id: str
    source_key: IdentityKey
    match_strategy: str
    confidence: float
    is_new: bool
    lineage: dict[str, Any] = field(default_factory=dict)


class IdentityResolutionError(ValueError):
    pass


def deterministic_canonical_id(key: IdentityKey) -> str:
    if not key.entity_type or not key.source_id or not key.source_entity_id:
        raise IdentityResolutionError("entity_type, source_id, and source_entity_id are required")
    return str(uuid5(NAMESPACE_URL, f"{ODAY_ID_NAMESPACE}:{key.fingerprint}"))


def source_key_from_payload(
    entity_type: str,
    payload: Mapping[str, Any],
    *,
    source_id: str | None = None,
    source_entity_fields: Iterable[str] = (),
    tenant_id: str = "",
) -> IdentityKey:
    resolved_source_id = str(source_id or payload.get("source_id") or payload.get("source_system") or "").strip()
    source_entity_id = ""
    for field_name in source_entity_fields:
        value = payload.get(field_name)
        if value not in (None, ""):
            source_entity_id = str(value).strip()
            break
    if not source_entity_id:
        fallback_name = f"source_{entity_type}_id"
        source_entity_id = str(payload.get(fallback_name) or payload.get("source_id") or "").strip()
    return IdentityKey(
        entity_type=entity_type,
        source_id=resolved_source_id,
        source_entity_id=source_entity_id,
        tenant_id=tenant_id,
    )


class InMemoryIdentityResolver:
    def __init__(self, existing: Mapping[str, str] | None = None) -> None:
        self._canonical_by_fingerprint: dict[str, str] = dict(existing or {})

    def resolve(self, key: IdentityKey, *, lineage: Mapping[str, Any] | None = None) -> IdentityResolution:
        canonical_id = self._canonical_by_fingerprint.get(key.fingerprint)
        is_new = canonical_id is None
        if canonical_id is None:
            canonical_id = deterministic_canonical_id(key)
            self._canonical_by_fingerprint[key.fingerprint] = canonical_id
        return IdentityResolution(
            entity_type=key.entity_type,
            canonical_id=canonical_id,
            source_key=key,
            match_strategy="source_key" if not is_new else "deterministic_source_key",
            confidence=1.0,
            is_new=is_new,
            lineage=dict(lineage or {}),
        )

    def known_id(self, key: IdentityKey) -> str | None:
        return self._canonical_by_fingerprint.get(key.fingerprint)


class IdentityGraphResolver:
    """Read adapter exposing deterministic current and historical identity queries."""

    def __init__(self, graph: IdentityGraph) -> None:
        self.graph = graph

    def resolve_source(self, key: IdentityKey, *, as_of: Any = None) -> IdentityLineage:
        return self.graph.resolve_source(
            SourceIdentity(key.tenant_id, key.source_id, key.source_entity_id), as_of=as_of
        )

    def resolve_listing(self, tenant_id: str, listing_id: str) -> IdentityLineage:
        return self.graph.resolve_reference("listing", listing_id, tenant_id)

    def resolve_candidate(self, tenant_id: str, candidate_id: str) -> IdentityLineage:
        return self.graph.resolve_reference("candidate", candidate_id, tenant_id)


__all__ = [
    "IdentityKey",
    "IdentityResolution",
    "IdentityResolutionError",
    "InMemoryIdentityResolver",
    "IdentityGraphResolver",
    "deterministic_canonical_id",
    "source_key_from_payload",
]
