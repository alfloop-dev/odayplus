from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
from shared.infrastructure.persistence.identity_graph_sql import CanonicalIdentityGraphRepository


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
            "tenant-a",
            ("p-original",),
            "p-canonical",
            "duplicate property",
            graph.version("tenant-a"),
        )
    )
    assert (
        resolver.resolve_source(
            IdentityKey("listing", "approved-feed", "source-17", "tenant-a")
        ).effective_property_id
        == "p-canonical"
    )
    assert resolver.resolve_listing("tenant-a", "listing-17").effective_property_id == "p-canonical"
    assert (
        resolver.resolve_candidate("tenant-a", "candidate-9").effective_property_id == "p-canonical"
    )
    assert listing.property_id_at_creation == candidate.property_id_at_creation == "p-original"

    split = commands.split(
        SplitIdentityCommand(
            "tenant-a", {source: "p-split"}, "separate unit", graph.version("tenant-a")
        )
    )
    assert (
        resolver.resolve_source(
            IdentityKey("listing", "approved-feed", "source-17", "tenant-a")
        ).effective_property_id
        == "p-split"
    )

    commands.unmerge(
        UnmergeIdentityCommand(
            "tenant-a", split.decision_id, "reverse split", graph.version("tenant-a")
        )
    )
    commands.unmerge(
        UnmergeIdentityCommand(
            "tenant-a", merge.decision_id, "reverse merge", graph.version("tenant-a")
        )
    )
    assert resolver.resolve_listing("tenant-a", "listing-17").effective_property_id == "p-original"
    assert (
        resolver.resolve_candidate("tenant-a", "candidate-9").effective_property_id == "p-original"
    )
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


@pytest.fixture
def canonical_graph(intake_db, tmp_path):
    """Actual native rows/queue/audit; all actors and policies are synthetic."""
    from psycopg.types.json import Jsonb

    from modules.listing.application.intake_audit import CanonicalIntakeAuditSink
    from shared.audit.canonical_delivery import CanonicalAuditDelivery
    from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
    from shared.audit.worm import LocalAppendOnlyWormSink
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.postgresql import PostgresEngine
    from shared.jobs.queue import JobRequest
    from tests.intake_workflow_fixture import SyntheticIntakeAuditRetention

    ids = {key: str(uuid4()) for key in (
        "tenant", "actor", "reviewer", "intake", "case", "decision", "job", "listing", "edge",
        "source", "target", "split_target", "correlation",
    )}
    plan = {
        "source_property_ids": [ids["source"]], "target_property_id": ids["target"],
        "expected_property_versions": {ids["source"]: 7, ids["target"]: 11},
        "expected_listing_versions": {ids["listing"]: 13},
        "expected_edge_versions": {ids["edge"]: 5}, "candidate_reassignment_plan": [],
    }
    with intake_db.connect() as conn:
        # Full-production-schema consumers retain the real Core tenant FK;
        # the older focused Intake fixture has no Core schema at all.
        if conn.execute("SELECT to_regclass('core.tenants')").fetchone()[0] is not None:
            conn.execute("INSERT INTO core.tenants (tenant_id,tenant_name,status) VALUES (%s,'Synthetic identity fixture','active')",
                (ids["tenant"],))
        conn.execute(
            "INSERT INTO intake.source_registry (source_id,display_name,canonicalization_rule_version,"
            "retrieval_mode,policy_owner_subject_id) VALUES ('synthetic-feed','Synthetic only','1',"
            "'ASSISTED_ENTRY_ONLY',%s)", (ids["actor"],),
        )
        for key, version in (("source", 7), ("target", 11), ("split_target", 3)):
            conn.execute(
                "INSERT INTO identity.properties (property_id,tenant_id,normalized_address,address_fingerprint,version) "
                "VALUES (%s,%s,%s,%s,%s)",
                (ids[key], ids["tenant"], key, hashlib.sha256(key.encode()).hexdigest(), version),
            )
        conn.execute(
            "INSERT INTO intake.intakes (intake_id,tenant_id,submitter_subject_id,intake_method,"
            "processing_state,correlation_id,version) VALUES (%s,%s,%s,'MANUAL','NEEDS_REVIEW',%s,17)",
            (ids["intake"], ids["tenant"], ids["actor"], ids["correlation"]),
        )
        conn.execute(
            "INSERT INTO expansion.listings (listing_id,tenant_id,property_id,source_id,source_listing_id,"
            "lifecycle_state,version,created_at) VALUES (%s,%s,%s,'synthetic-feed','source-17','ACTIVE',13,"
            "clock_timestamp()-interval '2 days')", (ids["listing"], ids["tenant"], ids["source"]),
        )
        conn.execute(
            "INSERT INTO identity.match_cases (match_case_id,tenant_id,intake_id,outcome,status,confidence,proposed_by,version) "
            "VALUES (%s,%s,%s,'POSSIBLE_MATCH','EXECUTING',0.75,%s,23)",
            (ids["case"], ids["tenant"], ids["intake"], ids["actor"]),
        )
        conn.execute(
            "INSERT INTO identity.source_identity_edges (edge_id,tenant_id,source_id,source_entity_id,listing_id,"
            "property_id,match_strategy,confidence,edge_version,effective_from) "
            "VALUES (%s,%s,'synthetic-feed','source-17',%s,%s,'HUMAN',0.75,5,clock_timestamp()-interval '1 day')",
            (ids["edge"], ids["tenant"], ids["listing"], ids["source"]),
        )
        conn.execute(
            "INSERT INTO identity.match_decisions (match_decision_id,tenant_id,match_case_id,decision_type,status,"
            "proposer_subject_id,reviewer_subject_id,reason,risk_acknowledged,before_graph,after_graph,version,reviewed_at) "
            "VALUES (%s,%s,%s,'MERGE','EXECUTING',%s,%s,'Synthetic independent native execution',true,%s,%s,29,clock_timestamp())",
            (ids["decision"], ids["tenant"], ids["case"], ids["actor"], ids["reviewer"],
             Jsonb({"property_id": ids["source"]}), Jsonb({"plan": plan})),
        )
    engine = PostgresEngine(intake_db.url(), bootstrap=False, validate_schema=False)
    engine.apply_runtime_migration()
    queue = DurableJobQueue(engine)
    writer = CanonicalPostgresAuditLog(engine)
    sink = CanonicalIntakeAuditSink(writer, SyntheticIntakeAuditRetention(),
        CanonicalAuditDelivery(writer, queue, LocalAppendOnlyWormSink(tmp_path / "synthetic-worm")))
    repo = CanonicalIdentityGraphRepository(engine, queue, sink)
    delivery, _ = queue.enqueue(JobRequest(repo.job_type,
        {"tenant_id": ids["tenant"], "decision_id": ids["decision"], "job_id": ids["job"]},
        idempotency_key="synthetic-identity:" + ids["decision"], job_id=ids["job"]),
        correlation_id=ids["correlation"])
    delivery = queue.claim_next(worker_id="synthetic-native-identity")
    assert delivery.job_id == ids["job"]
    with repo.transaction(ids["tenant"]):
        engine.execute(
            "INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,status,checkpoint,"
            "attempt,max_attempts,fence_token,lease_expires_at,timeout_at,payload,correlation_id,idempotency_key,version) "
            "VALUES (?,?,?,'identity_decision',?,'RUNNING','EXECUTING',1,1,31,clock_timestamp()+interval '120 seconds',"
            "clock_timestamp()+interval '120 seconds',?::jsonb,?,?,37)",
            (ids["job"], ids["tenant"], repo.job_type, ids["decision"], json.dumps({
                "decision_version": 29, "match_case_version": 23,
                "delivery_fence": delivery.fence_token, "delivery_worker": delivery.locked_by,
                "plan_sha256": hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            }), ids["correlation"], ids["decision"]),
        )
    try:
        yield repo, ids, delivery, intake_db
    finally:
        engine.close()


def _execute(case):
    repo, ids, delivery, _ = case
    return repo.execute(ids["tenant"], ids["decision"], delivery, business_fence=31)


def _native_snapshot(case):
    repo, ids, _, _ = case
    with repo.transaction(ids["tenant"]):
        return {name: repo.engine.query("SELECT * FROM " + name + " WHERE tenant_id=?", (ids["tenant"],))
                for name in ("identity.properties", "identity.source_identity_edges", "identity.property_redirects",
                             "identity.match_cases", "identity.match_decisions", "expansion.candidate_sites",
                             "workflow.jobs", "audit.audit_events", "workflow.outbox_events")}


@pytest.mark.requires_live_env
def test_native_merge_commits_real_versions_lineage_audit_and_outbox(canonical_graph):
    from shared.domain.events import validate_event

    repo, ids, _, db = canonical_graph
    context = repo.case_context(ids["tenant"], ids["case"])
    assert context["match_case"]["version"] == 23
    assert context["intake"]["intake_id"] == ids["intake"] and context["intake"]["version"] == 17
    historic = datetime.now(UTC) - timedelta(hours=1)
    assert repo.resolve_listing(ids["tenant"], ids["listing"], as_of=historic)["effective_property_id"] == ids["source"]
    receipt = _execute(canonical_graph)
    assert receipt["property_versions"] == {ids["source"]: 8, ids["target"]: 12}
    assert receipt["status"] == "EXECUTED" and receipt["job_id"] == ids["job"]
    before_replay = _native_snapshot(canonical_graph)
    assert _execute(canonical_graph) == receipt
    assert _native_snapshot(canonical_graph) == before_replay
    resolved = repo.resolve_listing(ids["tenant"], ids["listing"])
    assert resolved["listing_version"] == 13 and resolved["property_id_at_creation"] == ids["source"]
    assert resolved["effective_property_id"] == ids["target"]
    roots = repo.resolve_properties(ids["tenant"], [ids["source"], ids["target"], ids["split_target"]])
    assert roots[ids["source"]]["redirect_path"] == [ids["source"], ids["target"]]
    assert roots[ids["source"]]["effective_property_id"] == resolved["effective_property_id"]
    assert roots[ids["source"]]["current_property_version"] == roots[ids["target"]]["current_property_version"] == 12
    assert roots[ids["split_target"]]["effective_property_id"] == ids["split_target"]
    assert repo.resolve_properties(ids["tenant"], [ids["source"]], as_of=historic)[ids["source"]]["effective_property_id"] == ids["source"]
    with pytest.raises(IdentityNotFoundError):
        repo.resolve_properties(str(uuid4()), [ids["source"]])
    assert repo.resolve_listing(ids["tenant"], ids["listing"], as_of=historic)["effective_property_id"] == ids["source"]
    with db.connect() as conn:
        edges = conn.execute("SELECT effective_to,supersedes_edge_id,edge_version FROM identity.source_identity_edges ORDER BY edge_version").fetchall()
        assert len(edges) == 2 and edges[0][0] and edges[1][0] is None
        assert str(edges[1][1]) == ids["edge"] and edges[1][2] == 6
        assert conn.execute("SELECT status,version FROM identity.match_decisions").fetchone() == ("EXECUTED", 30)
        assert conn.execute("SELECT status,version FROM identity.match_cases").fetchone() == ("EXECUTED", 24)
        assert conn.execute("SELECT status,version FROM workflow.jobs").fetchone() == ("SUCCEEDED", 38)
        assert conn.execute("SELECT count(*) FROM audit.audit_events").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM workflow.outbox_events").fetchone()[0] == 2
        events = conn.execute("SELECT payload,occurred_at,retention_until FROM workflow.outbox_events").fetchall()
        assert all(validate_event(event) == [] and until - at == timedelta(days=30) for event, at, until in events)
        resolution = next(event for event, _, _ in events if event["event_type"] == "identity.resolution_changed")
        assert resolution["aggregate_id"] == ids["target"] and resolution["aggregate_version"] == 12
        assert resolution["payload"]["closed_edge_ids"] == [ids["edge"]]
        assert conn.execute("SELECT count(*) FROM odp_runtime.durable_jobs WHERE job_type='canonical-audit-delivery'").fetchone()[0] == 1


@pytest.mark.requires_live_env
@pytest.mark.parametrize("mutation,error", [
    ("UPDATE identity.properties SET version=version+1 WHERE normalized_address='source'", "VERSION_CONFLICT"),
    ("UPDATE expansion.listings SET version=version+1", "LISTING_VERSION_CONFLICT"),
    ("UPDATE identity.source_identity_edges SET edge_version=edge_version+1", "SOURCE_EDGE_VERSION_CONFLICT"),
    ("UPDATE expansion.listings SET legal_hold=true", "LEGAL_HOLD_CONFLICT"),
    ("UPDATE intake.intakes SET legal_hold=true", "LEGAL_HOLD_CONFLICT"),
    ("UPDATE expansion.listings SET lifecycle_state='ARCHIVED'", "LISTING_TERMINAL"),
    ("UPDATE identity.properties SET status='ARCHIVED' WHERE normalized_address='source'", "PROPERTY_TERMINAL"),
    ("UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp()", "IDENTITY_BUSINESS_FENCE_REJECTED"),
    ("UPDATE workflow.jobs SET fence_token=fence_token+1", "IDENTITY_BUSINESS_FENCE_REJECTED"),
    ("UPDATE workflow.jobs SET timeout_at=clock_timestamp()-interval '1 second'", "IDENTITY_BUSINESS_FENCE_REJECTED"),
    ("UPDATE identity.match_cases SET version=version+1", "IDENTITY_ACCEPTANCE_BINDING_REJECTED"),
    ("UPDATE identity.match_decisions SET status='APPROVED'", "IDENTITY_EXECUTION_NOT_ACCEPTED"),
])
def test_native_execution_rejects_changed_authority_without_partial_writes(canonical_graph, mutation, error):
    _, _, _, db = canonical_graph
    with db.connect() as conn:
        conn.execute(mutation)
    before = _native_snapshot(canonical_graph)
    with pytest.raises(ValueError, match=error):
        _execute(canonical_graph)
    assert _native_snapshot(canonical_graph) == before


@pytest.mark.requires_live_env
def test_native_execution_rolls_back_graph_when_canonical_audit_fails(canonical_graph, monkeypatch):
    from shared.audit.errors import CanonicalAuditUnavailable

    repo, _, _, _ = canonical_graph
    before = _native_snapshot(canonical_graph)

    def fail(*args):
        raise CanonicalAuditUnavailable("Synthetic audit unavailable")

    monkeypatch.setattr(repo.audit_sink, "persist", fail)
    with pytest.raises(CanonicalAuditUnavailable):
        _execute(canonical_graph)
    assert _native_snapshot(canonical_graph) == before


@pytest.mark.requires_live_env
def test_native_execution_rejects_old_transport_fence_and_cross_tenant(canonical_graph):
    from shared.jobs.queue import JobFenceRejectedError

    repo, ids, delivery, _ = canonical_graph
    before = _native_snapshot(canonical_graph)
    with pytest.raises(JobFenceRejectedError):
        repo.execute(ids["tenant"], ids["decision"], replace(delivery, fence_token=delivery.fence_token - 1), business_fence=31)
    with pytest.raises(JobFenceRejectedError):
        repo.execute(str(uuid4()), ids["decision"], delivery, business_fence=31)
    with pytest.raises(IdentityNotFoundError):
        repo.case_context(str(uuid4()), ids["case"])
    assert _native_snapshot(canonical_graph) == before


@pytest.mark.requires_live_env
def test_native_graph_refuses_outer_read_committed_and_does_not_change_shared_default(canonical_graph):
    from modules.listing.domain.identity_graph import IdentityConflictError

    repo, ids, _, _ = canonical_graph
    with repo.engine.transaction():
        assert repo.engine.query_one("SHOW transaction_isolation")["transaction_isolation"] == "read committed"
        with pytest.raises(IdentityConflictError, match="SERIALIZABLE"):
            repo.case_context(ids["tenant"], ids["case"])
    with repo.engine.transaction():
        assert repo.engine.query_one("SHOW transaction_isolation")["transaction_isolation"] == "read committed"


def _replace_native_plan(case, change, *, kind="MERGE"):
    from psycopg.types.json import Jsonb

    _, ids, _, db = case
    with db.connect() as conn:
        plan = conn.execute("SELECT after_graph->'plan' FROM identity.match_decisions").fetchone()[0]
        change(plan)
        conn.execute("UPDATE identity.match_decisions SET after_graph=%s,decision_type=%s",
                     (Jsonb({"plan": plan}), kind))
        conn.execute("UPDATE workflow.jobs SET payload=jsonb_set(payload,'{plan_sha256}',%s)",
                     (Jsonb(hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()),))


@pytest.mark.requires_live_env
def test_native_split_partitions_sources_without_rewriting_listings(canonical_graph):
    repo, ids, _, db = canonical_graph
    other_listing, other_edge = str(uuid4()), str(uuid4())
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO expansion.listings (listing_id,tenant_id,property_id,source_id,source_listing_id,lifecycle_state,version) "
            "VALUES (%s,%s,%s,'synthetic-feed','source-18','ACTIVE',19)",
            (other_listing, ids["tenant"], ids["source"]),
        )
        conn.execute(
            "INSERT INTO identity.source_identity_edges (edge_id,tenant_id,source_id,source_entity_id,listing_id,property_id,"
            "match_strategy,confidence,effective_from) VALUES (%s,%s,'synthetic-feed','source-18',%s,%s,'HUMAN',0.75,"
            "clock_timestamp()-interval '1 hour')", (other_edge, ids["tenant"], other_listing, ids["source"]),
        )

    def split(plan):
        plan.pop("source_property_ids")
        plan.pop("target_property_id")
        plan["source_property_id"] = ids["source"]
        plan["partitions"] = [
            {"target_property_id": ids["target"], "source_identity_edge_ids": [ids["edge"]]},
            {"target_property_id": ids["split_target"], "source_identity_edge_ids": [other_edge]},
        ]
        plan["expected_property_versions"][ids["split_target"]] = 3
        plan["expected_listing_versions"][other_listing] = 19
        plan["expected_edge_versions"][other_edge] = 1

    _replace_native_plan(canonical_graph, split, kind="SPLIT")
    receipt = _execute(canonical_graph)
    assert receipt["created_redirect_ids"] == [] and len(receipt["created_edge_ids"]) == 2
    assert repo.resolve_listing(ids["tenant"], ids["listing"])["effective_property_id"] == ids["target"]
    resolved = repo.resolve_listing(ids["tenant"], other_listing)
    assert resolved["effective_property_id"] == ids["split_target"]
    assert resolved["property_id_at_creation"] == ids["source"] and resolved["listing_version"] == 19


def _add_candidate(case, *, property_key="source", format_code="SYNTHETIC"):
    _, ids, _, db = case
    promotion, candidate = str(uuid4()), str(uuid4())
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO expansion.promotion_decisions (promotion_decision_id,tenant_id,intake_id,listing_id,property_id,"
            "target_format_code,status,proposer_subject_id,reason,gate_snapshot) "
            "VALUES (%s,%s,%s,%s,%s,%s,'COMPLETED',%s,'Synthetic disposition fixture','{}')",
            (promotion, ids["tenant"], ids["intake"], ids["listing"], ids[property_key], format_code, ids["actor"]),
        )
        conn.execute(
            "INSERT INTO expansion.candidate_sites (candidate_site_id,tenant_id,property_id,source_listing_id,"
            "promotion_decision_id,target_format_code,status,version) VALUES (%s,%s,%s,%s,%s,%s,'NEW',41)",
            (candidate, ids["tenant"], ids[property_key], ids["listing"], promotion, format_code),
        )
    return candidate


@pytest.mark.requires_live_env
@pytest.mark.parametrize("disposition", ["KEEP_HISTORICAL", "REQUIRE_REVIEW", "REASSIGN"])
def test_native_candidate_dispositions_are_explicit_and_preserve_history(canonical_graph, disposition):
    _, ids, _, db = canonical_graph
    candidate = _add_candidate(canonical_graph)

    def set_plan(plan):
        plan["candidate_reassignment_plan"] = [{
            "candidate_site_id": candidate, "expected_version": 41, "disposition": disposition,
            "target_property_id": ids["target"] if disposition == "REASSIGN" else None,
        }]

    _replace_native_plan(canonical_graph, set_plan)
    _execute(canonical_graph)
    with db.connect() as conn:
        row = conn.execute("SELECT property_id,version FROM expansion.candidate_sites WHERE candidate_site_id=%s", (candidate,)).fetchone()
        assert (str(row[0]), row[1]) == ((ids["target"], 42) if disposition == "REASSIGN" else (ids["source"], 41))
        assert str(conn.execute("SELECT property_id FROM expansion.promotion_decisions").fetchone()[0]) == ids["source"]


@pytest.mark.requires_live_env
def test_native_candidate_collision_and_missing_disposition_roll_back(canonical_graph):
    from modules.listing.domain.identity_graph import IdentityConflictError

    _, ids, _, _ = canonical_graph
    candidate = _add_candidate(canonical_graph)
    before = _native_snapshot(canonical_graph)
    with pytest.raises(IdentityConflictError, match="CANDIDATE_DISPOSITION_REQUIRED"):
        _execute(canonical_graph)
    assert _native_snapshot(canonical_graph) == before
    _add_candidate(canonical_graph, property_key="target")

    def set_plan(plan):
        plan["candidate_reassignment_plan"] = [{"candidate_site_id": candidate, "expected_version": 41,
            "disposition": "REASSIGN", "target_property_id": ids["target"]}]

    _replace_native_plan(canonical_graph, set_plan)
    before = _native_snapshot(canonical_graph)
    with pytest.raises(IdentityConflictError, match="CANDIDATE_PLAN_CONFLICT"):
        _execute(canonical_graph)
    assert _native_snapshot(canonical_graph) == before


@pytest.mark.requires_live_env
def test_native_recursive_cte_cycle_guard_rolls_back_source_edge_writes(canonical_graph):
    from modules.listing.domain.identity_graph import IdentityCycleError

    _, ids, _, db = canonical_graph
    # Deliberately inconsistent historical input proves the recursive SQL guard,
    # rather than merely failing the ACTIVE/current-pointer precondition.
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO identity.property_redirects (tenant_id,from_property_id,to_property_id,decision_id) VALUES (%s,%s,%s,%s)",
            (ids["tenant"], ids["target"], ids["source"], ids["decision"]),
        )
    before = _native_snapshot(canonical_graph)
    with pytest.raises(IdentityCycleError, match="PROPERTY_REDIRECT_CYCLE"):
        _execute(canonical_graph)
    assert _native_snapshot(canonical_graph) == before


@pytest.mark.requires_live_env
def test_native_deadline_expiring_during_audit_rolls_back_all_effects(canonical_graph, monkeypatch):
    from shared.jobs.queue import JobFenceRejectedError

    repo, _, _, _ = canonical_graph
    persist = repo.audit_sink.persist
    before = _native_snapshot(canonical_graph)

    def expire(audit, scope):
        result = persist(audit, scope)
        repo.engine.execute("UPDATE workflow.jobs SET timeout_at=clock_timestamp()-interval '1 second'")
        return result

    monkeypatch.setattr(repo.audit_sink, "persist", expire)
    with pytest.raises(JobFenceRejectedError, match="IDENTITY_BUSINESS_LEASE_EXPIRED"):
        _execute(canonical_graph)
    assert _native_snapshot(canonical_graph) == before
