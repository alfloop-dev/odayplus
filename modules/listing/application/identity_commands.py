from __future__ import annotations

from dataclasses import dataclass

from modules.listing.domain.identity_graph import IdentityDecision, IdentityGraph, SourceIdentity


@dataclass(frozen=True)
class MergeIdentityCommand:
    tenant_id: str
    from_property_ids: tuple[str, ...]
    to_property_id: str
    reason: str
    expected_version: int
    decision_id: str | None = None


@dataclass(frozen=True)
class SplitIdentityCommand:
    tenant_id: str
    assignments: dict[SourceIdentity, str]
    reason: str
    expected_version: int
    decision_id: str | None = None


@dataclass(frozen=True)
class UnmergeIdentityCommand:
    tenant_id: str
    decision_id: str
    reason: str
    expected_version: int


class IdentityCommandService:
    def __init__(self, graph: IdentityGraph) -> None:
        self.graph = graph

    def merge(self, command: MergeIdentityCommand) -> IdentityDecision:
        return self.graph.merge(command.tenant_id, command.from_property_ids, command.to_property_id, reason=command.reason, expected_version=command.expected_version, decision_id=command.decision_id)

    def split(self, command: SplitIdentityCommand) -> IdentityDecision:
        return self.graph.split(command.tenant_id, command.assignments, reason=command.reason, expected_version=command.expected_version, decision_id=command.decision_id)

    def unmerge(self, command: UnmergeIdentityCommand) -> IdentityDecision:
        return self.graph.unmerge(command.tenant_id, command.decision_id, reason=command.reason, expected_version=command.expected_version)


__all__ = ["IdentityCommandService", "MergeIdentityCommand", "SplitIdentityCommand", "UnmergeIdentityCommand"]
