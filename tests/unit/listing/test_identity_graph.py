from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from modules.listing.domain.identity_graph import (
    IdentityConflictError,
    IdentityCycleError,
    IdentityGraph,
    IdentityNotFoundError,
    SourceIdentity,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 18, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.value += timedelta(microseconds=1)
        return self.value


def graph_with_sources() -> tuple[IdentityGraph, SourceIdentity, SourceIdentity]:
    graph = IdentityGraph(clock=Clock())
    for property_id in ("property-a", "property-b", "property-c"):
        graph.add_property("tenant-a", property_id)
    first = SourceIdentity("tenant-a", "portal", "listing-1")
    second = SourceIdentity("tenant-a", "broker", "listing-2")
    graph.bind_source(first, "property-a", listing_id="listing-a")
    graph.bind_source(second, "property-b", listing_id="listing-b")
    return graph, first, second


def test_merge_closes_edges_creates_redirect_and_preserves_history() -> None:
    graph, first, _ = graph_with_sources()
    before = graph.version("tenant-a")

    decision = graph.merge(
        "tenant-a", ("property-a",), "property-b", reason="same building", expected_version=before
    )

    lineage = graph.resolve_source(first)
    assert lineage.property_id_at_reference == "property-b"
    assert lineage.effective_property_id == "property-b"
    assert [edge.property_id for edge in lineage.edges] == ["property-a", "property-b"]
    assert lineage.edges[0].effective_to is not None
    assert lineage.edges[1].supersedes_edge_id == lineage.edges[0].edge_id
    assert graph.redirects[0].decision_id == decision.decision_id


def test_split_and_unmerge_are_append_only_reversals() -> None:
    graph, first, second = graph_with_sources()
    merged = graph.merge(
        "tenant-a", ("property-a",), "property-b", reason="merge", expected_version=graph.version("tenant-a")
    )
    split = graph.split(
        "tenant-a",
        {first: "property-c"},
        reason="unit distinction",
        expected_version=graph.version("tenant-a"),
    )
    assert graph.resolve_source(first).effective_property_id == "property-c"
    assert split.before_edges != split.after_edges

    graph.reverse(
        "tenant-a", split.decision_id, reason="split evidence invalid", expected_version=graph.version("tenant-a")
    )
    assert graph.resolve_source(first).effective_property_id == "property-b"

    graph.unmerge(
        "tenant-a", merged.decision_id, reason="merge evidence invalid", expected_version=graph.version("tenant-a")
    )
    assert graph.resolve_source(first).effective_property_id == "property-a"
    assert graph.resolve_source(second).effective_property_id == "property-b"
    assert graph.redirects[0].reversed_at is not None
    assert len(graph.edge_history(first)) == 5


def test_cycle_concurrency_and_cross_tenant_access_are_rejected() -> None:
    graph, first, _ = graph_with_sources()
    graph.merge(
        "tenant-a", ("property-a",), "property-b", reason="merge", expected_version=graph.version("tenant-a")
    )
    with pytest.raises(IdentityCycleError):
        graph.merge(
            "tenant-a", ("property-b",), "property-a", reason="cycle", expected_version=graph.version("tenant-a")
        )
    with pytest.raises(IdentityConflictError):
        graph.split("tenant-a", {first: "property-c"}, reason="stale", expected_version=0)
    with pytest.raises(IdentityNotFoundError):
        graph.resolve_source(SourceIdentity("tenant-b", "portal", "listing-1"))


def test_failed_operation_rolls_back_every_edge_and_redirect() -> None:
    graph, first, _ = graph_with_sources()
    version = graph.version("tenant-a")
    edges = graph.edges

    with pytest.raises(RuntimeError, match="injected"):
        graph.merge(
            "tenant-a",
            ("property-a",),
            "property-b",
            reason="failure test",
            expected_version=version,
            fail_after=2,
        )

    assert graph.version("tenant-a") == version
    assert graph.edges == edges
    assert graph.redirects == ()
    assert graph.resolve_source(first).effective_property_id == "property-a"


def test_as_of_query_selects_historical_edge() -> None:
    clock = Clock()
    graph = IdentityGraph(clock=clock)
    graph.add_property("tenant-a", "old")
    graph.add_property("tenant-a", "new")
    source = SourceIdentity("tenant-a", "feed", "42")
    old_edge = graph.bind_source(source, "old")
    between = clock.value
    graph.bind_source(source, "new")

    assert graph.resolve_source(source, as_of=between).effective_property_id == "old"
    assert graph.resolve_source(source).effective_property_id == "new"
    assert old_edge.edge_id == graph.edge_history(source)[0].edge_id
