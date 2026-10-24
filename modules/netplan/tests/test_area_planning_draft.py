"""Human planning intent, missing inputs and one-time native handoff bookkeeping."""
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

import pytest

from modules.netplan.domain.area_planning_draft import (
    AreaPlanningDraft,
    AreaPlanningDraftConflict,
    AreaPlanningDraftInvalid,
    AreaPlanningDraftStatus,
    AreaPlanningFields,
)


def fields(**changes):
    return AreaPlanningFields.from_mapping({
        "name": "台南區域規劃", "quarter": None, "budget": None,
        "candidateIds": [], **changes,
    })


def draft(value=None):
    return AreaPlanningDraft.create(tenant_id=str(uuid4()), created_by=str(uuid4()),
                                    heat_zone_id=str(uuid4()), fields=value or fields())


def test_initial_area_proposal_retains_unset_human_inputs():
    row = draft()
    wire = row.to_dict()
    assert wire["quarter"] is None and wire["budget"] is None and wire["candidateIds"] == []
    assert wire["status"] == "DRAFT" and wire["nativeScenarioId"] is None
    assert not row.fields.inputs_complete
    assert row.created_at.utcoffset() is not None and row.created_at == row.updated_at


def test_explicit_zero_budget_does_not_turn_missing_budget_into_zero():
    empty, explicit = fields(), fields(budget=0)
    assert empty.budget is None and explicit.budget == 0
    assert not explicit.inputs_complete


@pytest.mark.parametrize("value", [True, False, "1000", -1, float("nan"),
                                   float("inf"), -float("inf"), 10 ** 1000, {}, []])
def test_financial_inputs_reject_nonfinite_coerced_or_negative_values(value):
    with pytest.raises(AreaPlanningDraftInvalid):
        fields(budget=value)


@pytest.mark.parametrize("value", ["", "2026", "2026Q0", "2026Q5", "2026-Q4",
                                   " 2026Q4", "2026Q4 ", 2026, True])
def test_quarter_requires_an_explicit_unambiguous_calendar_quarter(value):
    with pytest.raises(AreaPlanningDraftInvalid):
        fields(quarter=value)


@pytest.mark.parametrize("value", [["a", "a"], [""], [" candidate"], ["candidate "],
                                   [None], [True], "candidate", {"candidate": 1}])
def test_candidate_references_are_not_silently_coerced_or_deduplicated(value):
    with pytest.raises(AreaPlanningDraftInvalid):
        fields(candidateIds=value)


def test_direct_value_construction_cannot_bypass_input_validation():
    with pytest.raises(AreaPlanningDraftInvalid):
        AreaPlanningFields("台南區域規劃", "2026Q4", float("nan"), ("candidate",))
    with pytest.raises(AreaPlanningDraftInvalid):
        AreaPlanningFields("台南區域規劃", None, None, ["candidate"])


def test_stale_edits_preserve_the_entire_original_proposal():
    initial = draft()
    current = initial.update(fields(quarter="2026Q4"), expected_version=1)
    before = current.to_dict()
    with pytest.raises(AreaPlanningDraftConflict):
        current.update(fields(budget=1200000), expected_version=1)
    assert current.to_dict() == before
    assert initial.version == 1 and initial.fields.quarter is None


@pytest.mark.parametrize("value", [fields(), fields(quarter="2026Q4"),
                                   fields(quarter="2026Q4", budget=1200000)])
def test_incomplete_drafts_do_not_record_a_native_scenario(value):
    current = draft(value)
    before = current.to_dict()
    with pytest.raises(AreaPlanningDraftInvalid):
        current.materialize(scenario_id="netplan-scenario-1", actor=str(uuid4()), expected_version=1)
    assert current.to_dict() == before


def test_recording_a_native_handoff_preserves_creator_and_freezes_the_draft():
    initial = draft(fields(quarter="2026Q4", budget=1200000, candidateIds=["candidate-1"]))
    actor = str(uuid4())
    linked = initial.materialize(scenario_id="netplan-scenario-1", actor=actor, expected_version=1)
    assert linked.status == AreaPlanningDraftStatus.MATERIALIZED
    assert linked.native_scenario_id == "netplan-scenario-1" and linked.materialized_by == actor
    assert linked.created_by == initial.created_by and linked.created_at == initial.created_at
    assert linked.version == 2 and initial.native_scenario_id is None
    with pytest.raises(AreaPlanningDraftConflict):
        linked.update(fields(), expected_version=2)
    with pytest.raises(AreaPlanningDraftConflict):
        linked.materialize(scenario_id="netplan-scenario-2", actor=actor, expected_version=2)


def test_malformed_stored_record_does_not_become_an_editable_proposal():
    row = draft()
    for changes in [dict(version=True), dict(version=0), dict(created_at=datetime(2026, 1, 1)),
                    dict(native_scenario_id="uncommitted-scenario"), dict(status="DRAFT")]:
        with pytest.raises(AreaPlanningDraftInvalid):
            replace(row, **changes)
