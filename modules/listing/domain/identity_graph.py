from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class IdentityGraphError(ValueError):
    """Base error for rejected identity graph mutations."""


class IdentityNotFoundError(IdentityGraphError):
    pass


class IdentityConflictError(IdentityGraphError):
    pass


class IdentityCycleError(IdentityGraphError):
    pass


class DecisionKind(StrEnum):
    MERGE = "MERGE"
    SPLIT = "SPLIT"
    UNMERGE = "UNMERGE"
    REVERSAL = "REVERSAL"


@dataclass(frozen=True, order=True)
class SourceIdentity:
    tenant_id: str
    source_id: str
    source_entity_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.source_id or not self.source_entity_id:
            raise IdentityGraphError("tenant_id, source_id, and source_entity_id are required")


@dataclass(frozen=True)
class PropertyRecord:
    tenant_id: str
    property_id: str
    version: int = 1


@dataclass(frozen=True)
class SourceIdentityEdge:
    edge_id: str
    source: SourceIdentity
    property_id: str
    listing_id: str | None
    match_strategy: str
    confidence: float
    decision_id: str | None
    effective_from: datetime
    effective_to: datetime | None = None
    supersedes_edge_id: str | None = None
    edge_version: int = 1


@dataclass(frozen=True)
class PropertyRedirect:
    redirect_id: str
    tenant_id: str
    from_property_id: str
    to_property_id: str
    decision_id: str
    effective_from: datetime
    reversed_at: datetime | None = None
    version: int = 1


@dataclass(frozen=True)
class IdentityReference:
    reference_type: str
    reference_id: str
    tenant_id: str
    property_id_at_creation: str


@dataclass(frozen=True)
class IdentityDecision:
    decision_id: str
    tenant_id: str
    kind: DecisionKind
    reason: str
    created_at: datetime
    graph_version_before: int
    graph_version_after: int
    before_edges: tuple[tuple[SourceIdentity, str], ...]
    after_edges: tuple[tuple[SourceIdentity, str], ...]
    created_redirect_ids: tuple[str, ...] = ()
    reversed_decision_id: str | None = None


@dataclass(frozen=True)
class IdentityLineage:
    tenant_id: str
    source: SourceIdentity | None
    property_id_at_reference: str
    effective_property_id: str
    redirect_path: tuple[str, ...]
    edges: tuple[SourceIdentityEdge, ...]


@dataclass
class _GraphState:
    properties: dict[tuple[str, str], PropertyRecord] = field(default_factory=dict)
    edges: list[SourceIdentityEdge] = field(default_factory=list)
    redirects: list[PropertyRedirect] = field(default_factory=list)
    references: dict[tuple[str, str, str], IdentityReference] = field(default_factory=dict)
    decisions: dict[str, IdentityDecision] = field(default_factory=dict)
    tenant_versions: dict[str, int] = field(default_factory=dict)


class IdentityGraph:
    """Tenant-isolated, append-only identity graph with atomic mutations."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._state = _GraphState()
        self._clock = clock or (lambda: datetime.now(UTC))

    def version(self, tenant_id: str) -> int:
        return self._state.tenant_versions.get(tenant_id, 0)

    def add_property(self, tenant_id: str, property_id: str) -> PropertyRecord:
        key = (tenant_id, property_id)
        if not tenant_id or not property_id:
            raise IdentityGraphError("tenant_id and property_id are required")
        existing = self._state.properties.get(key)
        if existing is not None:
            return existing
        record = PropertyRecord(tenant_id=tenant_id, property_id=property_id)
        self._state.properties[key] = record
        return record

    def bind_source(
        self,
        source: SourceIdentity,
        property_id: str,
        *,
        listing_id: str | None = None,
        match_strategy: str = "source_key",
        confidence: float = 1.0,
        decision_id: str | None = None,
        expected_version: int | None = None,
    ) -> SourceIdentityEdge:
        if not 0 <= confidence <= 1:
            raise IdentityGraphError("confidence must be between 0 and 1")
        self._check_version(source.tenant_id, expected_version)
        self._require_property(source.tenant_id, property_id)
        current = self.effective_edge(source)
        if current is not None and current.property_id == property_id:
            return current
        now = self._clock()
        if current is not None:
            self._close_edge(current.edge_id, now)
        edge = SourceIdentityEdge(
            edge_id=str(uuid4()),
            source=source,
            property_id=property_id,
            listing_id=listing_id if listing_id is not None else (current.listing_id if current else None),
            match_strategy=match_strategy,
            confidence=confidence,
            decision_id=decision_id,
            effective_from=now,
            supersedes_edge_id=current.edge_id if current else None,
            edge_version=(current.edge_version + 1) if current else 1,
        )
        self._state.edges.append(edge)
        self._bump(source.tenant_id)
        return edge

    def register_reference(
        self, reference_type: str, reference_id: str, tenant_id: str, property_id: str
    ) -> IdentityReference:
        self._require_property(tenant_id, property_id)
        key = (tenant_id, reference_type, reference_id)
        existing = self._state.references.get(key)
        if existing is not None and existing.property_id_at_creation != property_id:
            raise IdentityConflictError("source reference is immutable")
        reference = existing or IdentityReference(reference_type, reference_id, tenant_id, property_id)
        self._state.references[key] = reference
        return reference

    def effective_edge(self, source: SourceIdentity, *, as_of: datetime | None = None) -> SourceIdentityEdge | None:
        candidates = [
            edge
            for edge in self._state.edges
            if edge.source == source
            and (as_of is None and edge.effective_to is None or as_of is not None and edge.effective_from <= as_of and (edge.effective_to is None or as_of < edge.effective_to))
        ]
        return max(candidates, key=lambda edge: (edge.effective_from, edge.edge_version), default=None)

    def resolve_property(self, tenant_id: str, property_id: str, *, as_of: datetime | None = None) -> tuple[str, ...]:
        self._require_property(tenant_id, property_id)
        path = [property_id]
        while True:
            redirect = self._effective_redirect(tenant_id, path[-1], as_of=as_of)
            if redirect is None:
                return tuple(path)
            if redirect.to_property_id in path:
                raise IdentityCycleError("property redirect cycle detected")
            path.append(redirect.to_property_id)

    def resolve_source(self, source: SourceIdentity, *, as_of: datetime | None = None) -> IdentityLineage:
        edge = self.effective_edge(source, as_of=as_of)
        if edge is None:
            raise IdentityNotFoundError("source identity has no effective edge")
        path = self.resolve_property(source.tenant_id, edge.property_id, as_of=as_of)
        return IdentityLineage(source.tenant_id, source, edge.property_id, path[-1], path, self.edge_history(source))

    def resolve_reference(self, reference_type: str, reference_id: str, tenant_id: str) -> IdentityLineage:
        reference = self._state.references.get((tenant_id, reference_type, reference_id))
        if reference is None:
            raise IdentityNotFoundError("identity reference not found")
        path = self.resolve_property(tenant_id, reference.property_id_at_creation)
        return IdentityLineage(tenant_id, None, reference.property_id_at_creation, path[-1], path, ())

    def edge_history(self, source: SourceIdentity) -> tuple[SourceIdentityEdge, ...]:
        return tuple(sorted((edge for edge in self._state.edges if edge.source == source), key=lambda edge: (edge.effective_from, edge.edge_version)))

    def merge(
        self,
        tenant_id: str,
        from_property_ids: Iterable[str],
        to_property_id: str,
        *,
        reason: str,
        expected_version: int,
        decision_id: str | None = None,
        fail_after: int | None = None,
    ) -> IdentityDecision:
        sources = tuple(dict.fromkeys(from_property_ids))
        if not sources or any(source == to_property_id for source in sources):
            raise IdentityGraphError("merge requires distinct source and target properties")

        def mutation() -> IdentityDecision:
            self._check_version(tenant_id, expected_version)
            self._require_property(tenant_id, to_property_id)
            for source in sources:
                self._require_property(tenant_id, source)
                if to_property_id in self.resolve_property(tenant_id, source):
                    raise IdentityCycleError("merge would create a redirect cycle")
                if source in self.resolve_property(tenant_id, to_property_id):
                    raise IdentityCycleError("merge would create a redirect cycle")
            return self._apply_decision(tenant_id, DecisionKind.MERGE, reason, {source: to_property_id for source in sources}, decision_id, fail_after)

        return self._atomic(mutation)

    def split(
        self,
        tenant_id: str,
        assignments: Mapping[SourceIdentity, str],
        *,
        reason: str,
        expected_version: int,
        decision_id: str | None = None,
        fail_after: int | None = None,
    ) -> IdentityDecision:
        def mutation() -> IdentityDecision:
            self._check_version(tenant_id, expected_version)
            if not assignments:
                raise IdentityGraphError("split requires at least one source assignment")
            for source, target in assignments.items():
                if source.tenant_id != tenant_id:
                    raise IdentityConflictError("cross-tenant source assignment denied")
                self._require_property(tenant_id, target)
                if self.effective_edge(source) is None:
                    raise IdentityNotFoundError("source identity has no effective edge")
            return self._apply_decision(tenant_id, DecisionKind.SPLIT, reason, assignments, decision_id, fail_after)

        return self._atomic(mutation)

    def reverse(
        self,
        tenant_id: str,
        decision_id: str,
        *,
        reason: str,
        expected_version: int,
        reversal_id: str | None = None,
    ) -> IdentityDecision:
        def mutation() -> IdentityDecision:
            self._check_version(tenant_id, expected_version)
            original = self._state.decisions.get(decision_id)
            if original is None or original.tenant_id != tenant_id:
                raise IdentityNotFoundError("identity decision not found")
            if any(decision.reversed_decision_id == decision_id for decision in self._state.decisions.values()):
                raise IdentityConflictError("identity decision is already reversed")
            assignments = dict(original.before_edges)
            decision = self._apply_decision(tenant_id, DecisionKind.REVERSAL, reason, assignments, reversal_id, None, reversed_decision_id=decision_id)
            now = self._clock()
            for redirect_id in original.created_redirect_ids:
                self._reverse_redirect(redirect_id, now)
            return decision

        return self._atomic(mutation)

    def unmerge(self, tenant_id: str, decision_id: str, *, reason: str, expected_version: int) -> IdentityDecision:
        decision = self.reverse(tenant_id, decision_id, reason=reason, expected_version=expected_version)
        replacement = IdentityDecision(**{**decision.__dict__, "kind": DecisionKind.UNMERGE})
        self._state.decisions[replacement.decision_id] = replacement
        return replacement

    @property
    def edges(self) -> tuple[SourceIdentityEdge, ...]:
        return tuple(self._state.edges)

    @property
    def redirects(self) -> tuple[PropertyRedirect, ...]:
        return tuple(self._state.redirects)

    def _apply_decision(self, tenant_id: str, kind: DecisionKind, reason: str, assignments: Mapping[object, str], decision_id: str | None, fail_after: int | None, *, reversed_decision_id: str | None = None) -> IdentityDecision:
        if not reason.strip():
            raise IdentityGraphError("reason is required")
        decision_id = decision_id or str(uuid4())
        if decision_id in self._state.decisions:
            return self._state.decisions[decision_id]
        before_version = self.version(tenant_id)
        effective = {edge.source: edge.property_id for edge in self._state.edges if edge.source.tenant_id == tenant_id and edge.effective_to is None}
        before = tuple(sorted(effective.items()))
        created_redirects: list[str] = []
        operations = 0
        if kind == DecisionKind.MERGE:
            for source_property, target in assignments.items():
                redirect = PropertyRedirect(str(uuid4()), tenant_id, str(source_property), target, decision_id, self._clock())
                self._state.redirects.append(redirect)
                created_redirects.append(redirect.redirect_id)
                operations += 1
                self._maybe_fail(operations, fail_after)
                for source, current_property in tuple(effective.items()):
                    if current_property == source_property:
                        self.bind_source(source, target, decision_id=decision_id, match_strategy="merge", expected_version=None)
                        effective[source] = target
                        operations += 1
                        self._maybe_fail(operations, fail_after)
        else:
            for source, target in assignments.items():
                assert isinstance(source, SourceIdentity)
                self.bind_source(source, target, decision_id=decision_id, match_strategy=kind.value.lower(), expected_version=None)
                effective[source] = target
                operations += 1
                self._maybe_fail(operations, fail_after)
        self._bump(tenant_id)
        decision = IdentityDecision(decision_id, tenant_id, kind, reason, self._clock(), before_version, self.version(tenant_id), before, tuple(sorted(effective.items())), tuple(created_redirects), reversed_decision_id)
        self._state.decisions[decision_id] = decision
        return decision

    def _atomic(self, mutation: Callable[[], IdentityDecision]) -> IdentityDecision:
        original = self._state
        self._state = deepcopy(original)
        try:
            return mutation()
        except Exception:
            self._state = original
            raise

    @staticmethod
    def _maybe_fail(operations: int, fail_after: int | None) -> None:
        if fail_after is not None and operations >= fail_after:
            raise RuntimeError("injected identity transaction failure")

    def _check_version(self, tenant_id: str, expected_version: int | None) -> None:
        if expected_version is not None and expected_version != self.version(tenant_id):
            raise IdentityConflictError(f"graph version conflict: expected {expected_version}, current {self.version(tenant_id)}")

    def _require_property(self, tenant_id: str, property_id: str) -> None:
        if (tenant_id, property_id) not in self._state.properties:
            raise IdentityNotFoundError(f"property {property_id!r} not found in tenant")

    def _bump(self, tenant_id: str) -> None:
        self._state.tenant_versions[tenant_id] = self.version(tenant_id) + 1

    def _close_edge(self, edge_id: str, at: datetime) -> None:
        for index, edge in enumerate(self._state.edges):
            if edge.edge_id == edge_id:
                self._state.edges[index] = SourceIdentityEdge(**{**edge.__dict__, "effective_to": at})
                return

    def _reverse_redirect(self, redirect_id: str, at: datetime) -> None:
        for index, redirect in enumerate(self._state.redirects):
            if redirect.redirect_id == redirect_id and redirect.reversed_at is None:
                self._state.redirects[index] = PropertyRedirect(**{**redirect.__dict__, "reversed_at": at, "version": redirect.version + 1})

    def _effective_redirect(self, tenant_id: str, property_id: str, *, as_of: datetime | None) -> PropertyRedirect | None:
        candidates = [redirect for redirect in self._state.redirects if redirect.tenant_id == tenant_id and redirect.from_property_id == property_id and (as_of is None and redirect.reversed_at is None or as_of is not None and redirect.effective_from <= as_of and (redirect.reversed_at is None or as_of < redirect.reversed_at))]
        return max(candidates, key=lambda redirect: (redirect.effective_from, redirect.version), default=None)


__all__ = [
    "DecisionKind", "IdentityConflictError", "IdentityCycleError", "IdentityDecision",
    "IdentityGraph", "IdentityGraphError", "IdentityLineage", "IdentityNotFoundError",
    "IdentityReference", "PropertyRecord", "PropertyRedirect", "SourceIdentity", "SourceIdentityEdge",
]
