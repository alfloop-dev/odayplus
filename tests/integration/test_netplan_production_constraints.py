"""The production solver has to honour the same constraints as the other one.

There are two solvers. `solver/netplan/optimizer.py` builds a pywraplp MIP;
`modules/netplan/application/production.py::_solve_ortools_cp_sat` builds a
CP-SAT model, and `NetPlanService` routes production solves through the second
one when `production_required` is set.

ODP-FR-NET-002's new resource, coverage and dilution constraints went into the
first and not the second. Every test asserting them passed, because they all
exercised the path production does not take. Codex2 found it in review with a
direct CP-SAT repro: two 40-day OPEN options against `max_construction_days=50`
came back with both selected and no constraint classes declared at all.

That is the same defect this whole branch catalogues -- a guarantee added to a
path the runtime does not use -- committed inside the change that catalogues
it. These tests exist so the two solvers cannot drift apart again silently:
each one runs the *production* entry point, not the library.
"""

from __future__ import annotations

import pytest

from modules.netplan.application.production import (
    NetPlanProductionExecutionError,
    NetPlanProductionExecutor,
)
from modules.netplan.domain.planning import NetPlanScenario, NetPlanScenarioStatus
from solver.netplan.model import (
    ActionOption,
    ConstraintClass,
    NetPlanConstraints,
    NetworkAction,
)

pytest.importorskip("ortools", reason="the production solver needs OR-Tools CP-SAT")

from datetime import UTC, datetime  # noqa: E402


def _option(
    entity: str,
    action: NetworkAction = NetworkAction.OPEN,
    *,
    gm: float = 500_000.0,
    cost: float = 100_000.0,
    **resources: object,
) -> ActionOption:
    return ActionOption(
        entity_id=entity,
        action=action,
        expected_gross_margin=gm,
        budget_cost=cost,
        risk_score=0.2,
        capacity_delta=1,
        # The production executor refuses an option with no provenance, which is
        # correct and unrelated to what these tests exercise.
        source_snapshot_ids=("snap-2026-09",),
        **resources,  # type: ignore[arg-type]
    )


def _scenario(options: dict[str, tuple[ActionOption, ...]], constraints: NetPlanConstraints):
    return NetPlanScenario(
        scenario_id="scn-1",
        tenant_id="tenant-a",
        scenario_name="production constraint coverage",
        planning_horizon="2026Q4",
        options_by_entity=options,
        constraints=constraints,
        status=NetPlanScenarioStatus.DRAFT,
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
        correlation_id="corr-1",
    )


def _solve(options, constraints):
    return (
        NetPlanProductionExecutor()
        .execute(_scenario(options, constraints), alternative_limit=3)
        .result
    )


class TestTheProductionSolverHonoursTheResourceCaps:
    def test_construction_capacity_binds_in_the_production_path(self) -> None:
        """Codex2's repro, kept as a test.

        Two openings the budget affords and a construction pool that fits one.
        Before the fix the CP-SAT model had no construction constraint at all
        and returned both.
        """
        options = {
            "site-a": (
                _option("site-a", construction_days=40.0),
                _option("site-a", NetworkAction.KEEP, gm=10_000.0, cost=0.0, construction_days=0.0),
            ),
            "site-b": (
                _option("site-b", construction_days=40.0),
                _option("site-b", NetworkAction.KEEP, gm=10_000.0, cost=0.0, construction_days=0.0),
            ),
        }
        result = _solve(
            options, NetPlanConstraints(max_budget=1_000_000.0, max_construction_days=50.0)
        )
        used = sum(a.construction_days or 0.0 for a in result.selected_actions)
        assert used <= 50.0
        assert sum(1 for a in result.selected_actions if a.action is NetworkAction.OPEN) == 1

    def test_equipment_and_labour_bind_in_the_production_path(self) -> None:
        options = {
            "site-a": (
                _option("site-a", equipment_units=6.0, labour_headcount=8.0),
                _option(
                    "site-a", NetworkAction.KEEP, gm=10_000.0, cost=0.0,
                    equipment_units=0.0, labour_headcount=0.0,
                ),
            ),
            "site-b": (
                _option("site-b", equipment_units=6.0, labour_headcount=8.0),
                _option(
                    "site-b", NetworkAction.KEEP, gm=10_000.0, cost=0.0,
                    equipment_units=0.0, labour_headcount=0.0,
                ),
            ),
        }
        result = _solve(
            options,
            NetPlanConstraints(
                max_budget=1_000_000.0, max_equipment_units=7.0, max_labour_headcount=100.0
            ),
        )
        assert sum(a.equipment_units or 0.0 for a in result.selected_actions) <= 7.0

    def test_coverage_floor_binds_in_the_production_path(self) -> None:
        options = {
            "site-a": (
                _option("site-a", NetworkAction.EXIT, gm=800_000.0, cost=0.0, coverage_delta=-5.0),
                _option("site-a", NetworkAction.KEEP, gm=100_000.0, cost=0.0, coverage_delta=0.0),
            ),
        }
        result = _solve(
            options, NetPlanConstraints(max_budget=1_000_000.0, min_coverage_delta=0.0)
        )
        assert result.selected_actions[0].action is NetworkAction.KEEP

    def test_dilution_cap_binds_in_the_production_path(self) -> None:
        options = {
            "site-a": (
                _option("site-a", dilution_zone_id="hz-1"),
                _option("site-a", NetworkAction.KEEP, gm=10_000.0, cost=0.0),
            ),
            "site-b": (
                _option("site-b", dilution_zone_id="hz-1"),
                _option("site-b", NetworkAction.KEEP, gm=10_000.0, cost=0.0),
            ),
        }
        result = _solve(
            options, NetPlanConstraints(max_budget=1_000_000.0, max_open_per_dilution_zone=1)
        )
        assert len([a for a in result.selected_actions if a.action is NetworkAction.OPEN]) == 1


class TestTheProductionResultDeclaresWhatItTested:
    def test_a_capital_only_production_solve_declares_the_other_seven_absent(self) -> None:
        """The production result came back with both class tuples empty, so a
        plan from the runtime carried no statement about what it had been
        tested against at all."""
        options = {"site-a": (_option("site-a"),)}
        result = _solve(options, NetPlanConstraints(max_budget=1_000_000.0))
        assert result.modelled_constraint_classes == (ConstraintClass.CAPITAL,)
        assert ConstraintClass.SEQUENCING in result.unmodelled_constraint_classes

    def test_supplying_a_cap_moves_the_class_in_the_production_path_too(self) -> None:
        options = {"site-a": (_option("site-a", construction_days=1.0),)}
        result = _solve(
            options, NetPlanConstraints(max_budget=1_000_000.0, max_construction_days=10.0)
        )
        assert ConstraintClass.CONSTRUCTION in result.modelled_constraint_classes


class TestAnUndeclaredCostIsRefusedInProductionToo:
    def test_a_cap_with_an_undeclared_option_cost_is_refused(self) -> None:
        options = {
            "site-a": (_option("site-a", construction_days=40.0),),
            "site-b": (_option("site-b"),),
        }
        with pytest.raises(NetPlanProductionExecutionError) as excinfo:
            _solve(
                options, NetPlanConstraints(max_budget=1_000_000.0, max_construction_days=50.0)
            )
        # The reason has to be in the message, not only in __cause__: an operator
        # reading "OR-Tools NetPlan execution failed" goes to look at the solver,
        # when what happened is that an option declared no construction cost.
        assert "max_construction_days" in str(excinfo.value)
        assert "site-b" in str(excinfo.value)


class TestMixedDilutionMetadataIsRefused:
    """The second review finding.

    The original check refused only when *no* OPEN option declared a zone. With
    some declared and some not, the cap still bound -- on the ones that declared
    -- so the solve reported DILUTION as modelled while the undeclared openings
    were bound by nothing. That is worse than not constraining at all, because
    the result claims the constraint was applied.
    """

    def test_one_open_option_without_a_zone_is_refused(self) -> None:
        options = {
            "site-a": (_option("site-a", dilution_zone_id="hz-1"),),
            "site-b": (_option("site-b"),),  # OPEN, no zone
        }
        with pytest.raises(NetPlanProductionExecutionError) as excinfo:
            _solve(
                options, NetPlanConstraints(max_budget=1_000_000.0, max_open_per_dilution_zone=1)
            )
        message = str(excinfo.value)
        assert "dilution_zone_id" in message
        assert "site-b" in message

    def test_a_non_open_option_without_a_zone_is_fine(self) -> None:
        """Only openings land in a catchment; a KEEP or EXIT has nothing to
        dilute, so requiring a zone from it would be noise."""
        options = {
            "site-a": (_option("site-a", dilution_zone_id="hz-1"),),
            "site-b": (_option("site-b", NetworkAction.KEEP, gm=10_000.0, cost=0.0),),
        }
        result = _solve(
            options, NetPlanConstraints(max_budget=1_000_000.0, max_open_per_dilution_zone=1)
        )
        assert len(result.selected_actions) == 2
