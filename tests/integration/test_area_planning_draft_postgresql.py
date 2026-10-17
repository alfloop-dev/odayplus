"""Actual independent PostgreSQL owners for preliminary area planning state.

These fixtures exercise durable CAS and atomic native links with explicitly
synthetic financial inputs. They provide no financial admission, authorization,
business approval, human acceptance or production execution evidence.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest

from modules.netplan.application.planning import NetPlanService
from modules.netplan.domain.area_planning_draft import (
    AreaPlanningDraft,
    AreaPlanningDraftConflict,
    AreaPlanningDraftStatus,
    AreaPlanningFields,
)
from modules.netplan.domain.planning import NetPlanScenarioStatus
from modules.netplan.infrastructure.area_planning_drafts import (
    AreaPlanningDraftPersistenceError,
    DurableAreaPlanningDraftRepository,
)
from shared.infrastructure.persistence import PostgresDocumentStore
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurableNetPlanRepository
from solver.netplan import NetPlanConstraints
from tests.integration.test_intake_registry_calendar_postgresql import _bundle, _upgrade

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def owners(intake_blank_db, monkeypatch, tmp_path):
    _upgrade(intake_blank_db, "head")
    apply_upgrade_to_database(intake_blank_db.url())
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", (tmp_path / "owned-worm").as_uri())
    bundles = []
    try:
        bundles.append(_bundle(intake_blank_db, monkeypatch))
        bundles.append(_bundle(intake_blank_db, monkeypatch))
        assert bundles[0].engine is not bundles[1].engine
        assert all(bundle.engine.dialect == "postgresql" for bundle in bundles)
        monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")
        monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
        monkeypatch.setenv("ODP_DEPLOY_ENV", "test")
        tenant = str(uuid4())
        documents = [TenantScopedDocumentStore(PostgresDocumentStore(b.engine), tenant) for b in bundles]
        repositories = [DurableAreaPlanningDraftRepository(d) for d in documents]
        yield documents, repositories
    finally:
        for bundle in reversed(bundles):
            bundle.engine.close()


def _draft(documents, *, complete=False):
    return AreaPlanningDraft.create(
        tenant_id=documents.tenant_id, created_by=str(uuid4()), heat_zone_id=str(uuid4()),
        fields=AreaPlanningFields.from_mapping({
            "name": "Synthetic human planning intent", "quarter": "2026Q4" if complete else None,
            "budget": 100.0 if complete else None,
            "candidateIds": [str(uuid4())] if complete else [],
        }),
    )


def _native(documents, draft, **overrides):
    repository = DurableNetPlanRepository(documents)
    scenario = NetPlanService(repository=repository, runtime_mode="poc").create_scenario(
        tenant_id=draft.tenant_id,
        scenario_id=DurableAreaPlanningDraftRepository.scenario_id_for_draft(draft.draft_id),
        scenario_name=draft.fields.name, planning_horizon=draft.fields.quarter,
        constraints=NetPlanConstraints(max_budget=draft.fields.budget),
        existing_stores=[],
        candidate_sites=[{
            "candidate_site_id": candidate, "expected_gross_margin": 200.0, "open_cost": 100.0,
            "risk_score": 0.2, "capacity_delta": 1,
            "source_snapshot_ids": ["declared-synthetic-area-draft-fixture"],
        } for candidate in draft.fields.candidate_ids],
        correlation_id="synthetic-area-draft-cas-no-production-claim",
    )
    if overrides:
        scenario = replace(scenario, **overrides)
        repository.save_scenario(scenario)
    return scenario


def test_unset_human_inputs_remain_unset_across_independent_owners(owners):
    documents, (first, second) = owners
    draft = first.create(_draft(documents[0]))
    loaded = second.get(draft.draft_id)
    assert loaded == draft
    assert loaded.fields.quarter is loaded.fields.budget is None
    assert loaded.fields.candidate_ids == ()
    assert loaded.status == AreaPlanningDraftStatus.DRAFT
    assert loaded.native_scenario_id is None
    assert second.list_all() == [draft]
    assert DurableNetPlanRepository(documents[1]).list_scenarios() == []


def test_draft_identity_is_private_to_its_native_tenant_partition(owners):
    documents, (first, _) = owners
    draft = first.create(_draft(documents[0]))
    other = DurableAreaPlanningDraftRepository(TenantScopedDocumentStore(
        PostgresDocumentStore(documents[1].engine), str(uuid4()),
    ))
    assert other.get(draft.draft_id) is None
    assert other.list_all() == []
    with pytest.raises(AreaPlanningDraftPersistenceError):
        other.create(draft)
    assert first.get(draft.draft_id) == draft


def test_duplicate_create_cannot_replace_original_human_choices(owners):
    documents, (first, second) = owners
    draft = first.create(_draft(documents[0]))
    with pytest.raises(AreaPlanningDraftConflict):
        second.create(replace(draft, fields=replace(draft.fields, name="Rejected overwrite")))
    assert second.get(draft.draft_id) == draft


def test_stale_save_cannot_overwrite_another_owner_revision(owners):
    documents, (first, second) = owners
    original = first.create(_draft(documents[0]))
    stale = second.get(original.draft_id)
    changed = original.update(replace(original.fields, name="Human revision two"), expected_version=1)
    first.save(original, changed)
    with pytest.raises(AreaPlanningDraftConflict):
        second.save(stale, stale.update(replace(stale.fields, name="Rejected stale edit"), expected_version=1))
    assert second.get(original.draft_id) == changed


def test_actual_two_engine_compare_and_put_allows_only_one_simultaneous_edit(owners, monkeypatch):
    documents, repositories = owners
    original = repositories[0].create(_draft(documents[0]))
    barrier = Barrier(2)
    def attempt(index):
        changed = original.update(replace(original.fields, name=f"Concurrent human {index}"), expected_version=1)
        try:
            return repositories[index].save(original, changed)
        except AreaPlanningDraftConflict:
            return None

    with monkeypatch.context() as patch:
        for repository in repositories:
            actual_get = repository.get

            def read_before_competing_write(identifier, actual_get=actual_get):
                value = actual_get(identifier)
                barrier.wait(timeout=10)
                return value

            patch.setattr(repository, "get", read_before_competing_write)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, [0, 1]))
    winners = [row for row in results if row is not None]
    assert len(winners) == 1
    assert repositories[1].get(original.draft_id) == winners[0]
    assert winners[0].version == 2


def test_outer_sql_rollback_removes_new_proposal_from_both_owners(owners):
    documents, (first, second) = owners
    draft = _draft(documents[0])
    with pytest.raises(RuntimeError, match="synthetic failure after write"):
        with documents[0].engine.transaction():
            first.create(draft)
            raise RuntimeError("synthetic failure after write")
    assert first.get(draft.draft_id) is second.get(draft.draft_id) is None


@pytest.mark.parametrize("corruption", [
    "version", "fields", "id", "status", "tenant", "candidate-string", "candidate-list",
    "name-whitespace", "duplicate-candidates", "budget-nan", "quarter-int",
])
def test_loaded_pickle_is_revalidated_before_returning_a_draft(owners, corruption):
    documents, (first, second) = owners
    draft = first.create(_draft(documents[0]))
    bad = replace(draft, fields=replace(draft.fields))
    if corruption == "version":
        object.__setattr__(bad, "version", 999)
    elif corruption == "fields":
        object.__setattr__(bad, "fields", {"name": "Unvalidated dictionary"})
    elif corruption == "id":
        object.__setattr__(bad, "draft_id", str(uuid4()))
    elif corruption == "status":
        object.__setattr__(bad, "status", "DRAFT")
    elif corruption == "tenant":
        object.__setattr__(bad, "tenant_id", str(uuid4()))
    else:
        field, value = {
            "candidate-string": ("candidate_ids", "ABC"),
            "candidate-list": ("candidate_ids", ["A"]),
            "name-whitespace": ("name", " Human input must not be silently changed "),
            "duplicate-candidates": ("candidate_ids", ("A", "A")),
            "budget-nan": ("budget", float("nan")),
            "quarter-int": ("quarter", 2026),
        }[corruption]
        object.__setattr__(bad.fields, field, value)
    documents[0].put(first.COLLECTION, draft.draft_id, bad, seq=1)
    with pytest.raises(AreaPlanningDraftPersistenceError):
        second.get(draft.draft_id)
    with pytest.raises(AreaPlanningDraftPersistenceError):
        second.list_all()


def test_link_cannot_claim_a_native_scenario_that_does_not_exist(owners):
    documents, (first, second) = owners
    draft = first.create(_draft(documents[0], complete=True))
    linked = draft.materialize(
        scenario_id=first.scenario_id_for_draft(draft.draft_id), actor=str(uuid4()), expected_version=1,
    )
    with pytest.raises(AreaPlanningDraftPersistenceError, match="NATIVE_DRAFT_NOT_BOUND"):
        first.save(draft, linked)
    assert second.get(draft.draft_id) == draft


@pytest.mark.parametrize("mismatch", ["id", "tenant", "status", "name", "quarter", "budget", "candidates", "created"])
def test_mismatched_native_draft_cannot_commit_a_proposal_link(owners, mismatch):
    documents, (first, second) = owners
    draft = first.create(_draft(documents[0], complete=True))
    overrides = {
        "id": {"scenario_id": "unrelated-native-scenario"},
        "tenant": {"tenant_id": str(uuid4())},
        "status": {"status": NetPlanScenarioStatus.APPROVED},
        "name": {"scenario_name": "Different human plan"},
        "quarter": {"planning_horizon": "2027Q1"},
        "budget": {"constraints": NetPlanConstraints(max_budget=101)},
        "candidates": {"options_by_entity": {}},
        "created": {"created_at": draft.created_at - timedelta(days=1)},
    }[mismatch]
    with pytest.raises(AreaPlanningDraftPersistenceError, match="NATIVE_DRAFT_NOT_BOUND"):
        with documents[0].engine.transaction():
            scenario = _native(documents[0], draft, **overrides)
            first.save(draft, draft.materialize(
                scenario_id=scenario.scenario_id, actor=str(uuid4()), expected_version=1,
            ))
    assert second.get(draft.draft_id) == draft
    assert DurableNetPlanRepository(documents[1]).list_scenarios() == []


def test_real_native_draft_and_proposal_link_commit_in_the_same_sql_transaction(owners):
    documents, (first, second) = owners
    draft = first.create(_draft(documents[0], complete=True))
    actor = str(uuid4())
    with documents[0].engine.transaction():
        scenario = _native(documents[0], draft)
        linked = first.save(draft, draft.materialize(
            scenario_id=scenario.scenario_id, actor=actor, expected_version=1,
        ))
    assert second.get(draft.draft_id) == linked
    assert linked.status == AreaPlanningDraftStatus.MATERIALIZED
    assert linked.materialized_by == actor
    persisted = DurableNetPlanRepository(documents[1]).get_scenario(scenario.scenario_id)
    assert persisted == scenario
    assert persisted.status == NetPlanScenarioStatus.DRAFT
    assert persisted.status_history == ()


def test_failure_after_native_link_rolls_both_changes_back(owners):
    documents, (first, second) = owners
    draft = first.create(_draft(documents[0], complete=True))
    with pytest.raises(RuntimeError, match="synthetic audit delivery refusal"):
        with documents[0].engine.transaction():
            scenario = _native(documents[0], draft)
            first.save(draft, draft.materialize(
                scenario_id=scenario.scenario_id, actor=str(uuid4()), expected_version=1,
            ))
            raise RuntimeError("synthetic audit delivery refusal")
    assert second.get(draft.draft_id) == draft
    assert DurableNetPlanRepository(documents[1]).list_scenarios() == []
