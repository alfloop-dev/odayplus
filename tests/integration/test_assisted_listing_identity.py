from __future__ import annotations

import pytest

from modules.integration.application.identity_resolution import IdentityGraphResolver, IdentityKey
from modules.listing.application.identity_commands import (
    IdentityCommandService,
    MergeIdentityCommand,
    SplitIdentityCommand,
    UnmergeIdentityCommand,
)
from modules.listing.domain.identity_graph import (
    IdentityGraph,
    IdentityNotFoundError,
    SourceIdentity,
)


def test_commands_resolve_listing_candidate_and_source_without_rewriting_lineage() -> None:
    graph = IdentityGraph()
    for property_id in ("p-original", "p-canonical", "p-split"):
        graph.add_property("tenant-a", property_id)
    source = SourceIdentity("tenant-a", "approved-feed", "source-17")
    graph.bind_source(source, "p-original", listing_id="listing-17")
    listing = graph.register_reference("listing", "listing-17", "tenant-a", "p-original")
    candidate = graph.register_reference("candidate", "candidate-9", "tenant-a", "p-original")
    commands = IdentityCommandService(graph)
    resolver = IdentityGraphResolver(graph)

    merge = commands.merge(
        MergeIdentityCommand(
            "tenant-a", ("p-original",), "p-canonical", "duplicate property", graph.version("tenant-a")
        )
    )
    assert resolver.resolve_source(IdentityKey("listing", "approved-feed", "source-17", "tenant-a")).effective_property_id == "p-canonical"
    assert resolver.resolve_listing("tenant-a", "listing-17").effective_property_id == "p-canonical"
    assert resolver.resolve_candidate("tenant-a", "candidate-9").effective_property_id == "p-canonical"
    assert listing.property_id_at_creation == candidate.property_id_at_creation == "p-original"

    split = commands.split(
        SplitIdentityCommand(
            "tenant-a", {source: "p-split"}, "separate unit", graph.version("tenant-a")
        )
    )
    assert resolver.resolve_source(IdentityKey("listing", "approved-feed", "source-17", "tenant-a")).effective_property_id == "p-split"

    commands.unmerge(
        UnmergeIdentityCommand("tenant-a", split.decision_id, "reverse split", graph.version("tenant-a"))
    )
    commands.unmerge(
        UnmergeIdentityCommand("tenant-a", merge.decision_id, "reverse merge", graph.version("tenant-a"))
    )
    assert resolver.resolve_listing("tenant-a", "listing-17").effective_property_id == "p-original"
    assert resolver.resolve_candidate("tenant-a", "candidate-9").effective_property_id == "p-original"
    assert len(graph.edge_history(source)) == 5


def test_tenant_isolation_applies_to_source_and_reference_queries() -> None:
    graph = IdentityGraph()
    graph.add_property("tenant-a", "shared-looking-id")
    graph.add_property("tenant-b", "shared-looking-id")
    graph.bind_source(SourceIdentity("tenant-a", "feed", "same"), "shared-looking-id")
    graph.register_reference("listing", "same", "tenant-a", "shared-looking-id")
    resolver = IdentityGraphResolver(graph)

    with pytest.raises(IdentityNotFoundError):
        resolver.resolve_source(IdentityKey("listing", "feed", "same", "tenant-b"))
    with pytest.raises(IdentityNotFoundError):
        resolver.resolve_listing("tenant-b", "same")
