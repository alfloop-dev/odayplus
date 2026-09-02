"""A plan is only feasible with respect to the constraints that were modelled.

ODP-FR-NET-002 requires the plan to honour eight classes of hard constraint:
capital, lease, construction, equipment, labour, coverage, dilution and
sequencing. Before this work the solver modelled one of them. It did not fail;
it returned a plan, reported ``solver_status`` as optimal, and listed its
binding constraints -- an answer that reads as "this plan is deliverable" while
having tested only whether it was affordable.

That is the dangerous shape. A missing feature is a known unknown; a plan that
opens four stores in a quarter the construction crew can deliver two of, and
says so in the language of a satisfied optimiser, is a wrong answer wearing a
right answer's clothes.

Three of the seven missing classes are the same shape as the budget -- a pool
every option draws from -- and are modelled here. Dilution gets the part of
itself a linear model can carry. Lease and sequencing are not modelled, and the
result now says so rather than leaving the reader to assume otherwise.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from modules.netplan.application.production import (
    NetPlanProductionExecutionError,
    _solve_ortools_cp_sat,
)
from modules.netplan.domain.planning import NetPlanScenario, NetPlanScenarioStatus
from solver.netplan.model import (
    ActionOption,
    ConstraintClass,
    NetPlanConstraints,
    NetworkAction,
)
from solver.netplan.optimizer import build_feasible_candidates, solve_network_plan


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
        **resources,  # type: ignore[arg-type]
    )


def _solve(options, constraints):
    return solve_network_plan(
        options_by_entity=options,
        constraints=constraints,
        isolate_process=False,
    )


def _scenario(options, constraints) -> NetPlanScenario:
    return NetPlanScenario(
        scenario_id="sc-1",
        tenant_id="t-1",
        scenario_name="hard constraint coverage",
        planning_horizon="2026Q1",
        options_by_entity=options,
        constraints=constraints,
        status=NetPlanScenarioStatus.DRAFT,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        correlation_id="corr-1",
    )


class TestSharedResourcesAreHonoured:
    """Construction, equipment and labour are pools, exactly like the budget."""

    def test_a_plan_beyond_construction_capacity_is_not_returned(self) -> None:
        """The failure this work exists for.

        Two openings the budget can afford, and a construction pool that fits
        one of them. Before this change both were selected and the result said
        optimal.
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
            options,
            NetPlanConstraints(max_budget=1_000_000.0, max_construction_days=50.0),
        )
        used = sum(a.construction_days or 0.0 for a in result.selected_actions)
        assert used <= 50.0
        assert sum(1 for a in result.selected_actions if a.action is NetworkAction.OPEN) == 1

    def test_the_same_plan_is_returned_when_the_pool_is_large_enough(self) -> None:
        """Pins the counterfactual: the cap is what excluded the second opening,
        not something else about these options."""
        options = {
            "site-a": (_option("site-a", construction_days=40.0),),
            "site-b": (_option("site-b", construction_days=40.0),),
        }
        result = _solve(
            options,
            NetPlanConstraints(max_budget=1_000_000.0, max_construction_days=100.0),
        )
        assert sum(1 for a in result.selected_actions if a.action is NetworkAction.OPEN) == 2

    def test_equipment_and_labour_bind_the_same_way(self) -> None:
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

    def test_coverage_may_not_be_thinned_past_the_floor(self) -> None:
        """EXIT is cheap and profitable on paper; the network still has to be
        covered afterwards."""
        options = {
            "site-a": (
                _option("site-a", NetworkAction.EXIT, gm=800_000.0, cost=0.0, coverage_delta=-5.0),
                _option("site-a", NetworkAction.KEEP, gm=100_000.0, cost=0.0, coverage_delta=0.0),
            ),
        }
        result = _solve(
            options,
            NetPlanConstraints(max_budget=1_000_000.0, min_coverage_delta=0.0),
        )
        assert result.selected_actions[0].action is NetworkAction.KEEP


class TestDilutionCapsOpeningsPerCatchment:
    def test_two_openings_in_one_catchment_are_refused(self) -> None:
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
            options,
            NetPlanConstraints(max_budget=1_000_000.0, max_open_per_dilution_zone=1),
        )
        opens = [a for a in result.selected_actions if a.action is NetworkAction.OPEN]
        assert len(opens) == 1

    def test_openings_in_different_catchments_are_both_allowed(self) -> None:
        options = {
            "site-a": (_option("site-a", dilution_zone_id="hz-1"),),
            "site-b": (_option("site-b", dilution_zone_id="hz-2"),),
        }
        result = _solve(
            options,
            NetPlanConstraints(max_budget=1_000_000.0, max_open_per_dilution_zone=1),
        )
        assert len([a for a in result.selected_actions if a.action is NetworkAction.OPEN]) == 2


class TestAnUndeclaredCostIsRefusedRatherThanReadAsZero:
    """``None`` means the caller did not supply the figure. ``0.0`` means the
    option was measured and consumes none. Collapsing the first into the second
    is how an uncounted cost gets into a plan that then reports itself within
    the cap."""

    def test_a_cap_with_an_undeclared_option_cost_is_refused(self) -> None:
        options = {
            "site-a": (_option("site-a", construction_days=40.0),),
            "site-b": (_option("site-b"),),  # declares nothing
        }
        with pytest.raises(ValueError) as excinfo:
            _solve(
                options,
                NetPlanConstraints(max_budget=1_000_000.0, max_construction_days=50.0),
            )
        message = str(excinfo.value)
        assert "max_construction_days" in message
        assert "construction_days" in message
        assert "site-b" in message

    def test_a_declared_zero_is_accepted(self) -> None:
        options = {
            "site-a": (_option("site-a", construction_days=40.0),),
            "site-b": (_option("site-b", construction_days=0.0),),
        }
        result = _solve(
            options,
            NetPlanConstraints(max_budget=1_000_000.0, max_construction_days=50.0),
        )
        assert len(result.selected_actions) == 2

    def test_the_refusal_survives_process_isolation(self) -> None:
        """`solve_network_plan` runs the model in a separate process by default,
        which is the production path. A refusal that only surfaced in-process
        would leave production with the behaviour this change removes."""
        options = {"site-a": (_option("site-a"),)}
        with pytest.raises(ValueError) as excinfo:
            solve_network_plan(
                options_by_entity=options,
                constraints=NetPlanConstraints(
                    max_budget=1_000_000.0, max_construction_days=50.0
                ),
            )
        assert "max_construction_days" in str(excinfo.value)

    def test_an_unzoned_opening_may_not_slip_past_the_dilution_cap(self) -> None:
        """The fail-open shape.

        The cap counted only the OPEN options that happened to carry a zone, so
        an opening with a blank zone was invisible to it: a plan could hold its
        one permitted opening in `hz-1` and an unmeasured opening beside it, and
        still report DILUTION as modelled. A blank zone is not "dilutes
        nothing", it is "nobody said", and the two may not be read alike.
        """
        options = {
            "site-a": (_option("site-a", dilution_zone_id="hz-1"),),
            "site-b": (_option("site-b"),),
        }
        with pytest.raises(ValueError) as excinfo:
            _solve(
                options,
                NetPlanConstraints(max_budget=1_000_000.0, max_open_per_dilution_zone=1),
            )
        assert "site-b" in str(excinfo.value)

    def test_an_unzoned_opening_is_not_offered_as_a_candidate(self) -> None:
        """The same rule on the enumeration path, which supplies both the
        SCIP-unavailable fallback plan and the alternatives a reader is shown.
        An opening whose dilution nobody measured did not clear the cap."""
        candidates = build_feasible_candidates(
            options_by_entity={"site-a": (_option("site-a"),)},
            constraints=NetPlanConstraints(
                max_budget=1_000_000.0, max_open_per_dilution_zone=1
            ),
            risk_penalty=100_000.0,
        )
        assert candidates == []

    def test_a_dilution_cap_with_no_zoned_opening_is_refused(self) -> None:
        """A cap that cannot bind anything is not a constraint, and leaving it
        silent would let DILUTION be reported as modelled when nothing was
        constrained."""
        options = {"site-a": (_option("site-a"),)}
        with pytest.raises(ValueError) as excinfo:
            _solve(
                options,
                NetPlanConstraints(max_budget=1_000_000.0, max_open_per_dilution_zone=1),
            )
        assert "dilution_zone_id" in str(excinfo.value)


class TestTheResultSaysWhichClassesItTested:
    """The structural half of this change.

    Adding three constraints narrows the gap; saying which of the eight were
    applied is what stops the remaining gap from reading as compliance.
    """

    def test_a_capital_only_solve_reports_the_other_seven_as_unmodelled(self) -> None:
        options = {"site-a": (_option("site-a"),)}
        result = _solve(options, NetPlanConstraints(max_budget=1_000_000.0))

        assert result.modelled_constraint_classes == (ConstraintClass.CAPITAL,)
        assert set(result.unmodelled_constraint_classes) == {
            ConstraintClass.LEASE,
            ConstraintClass.CONSTRUCTION,
            ConstraintClass.EQUIPMENT,
            ConstraintClass.LABOUR,
            ConstraintClass.COVERAGE,
            ConstraintClass.DILUTION,
            ConstraintClass.SEQUENCING,
        }

    def test_supplying_a_cap_moves_that_class_into_the_modelled_set(self) -> None:
        options = {"site-a": (_option("site-a", construction_days=1.0),)}
        result = _solve(
            options,
            NetPlanConstraints(max_budget=1_000_000.0, max_construction_days=10.0),
        )
        assert ConstraintClass.CONSTRUCTION in result.modelled_constraint_classes
        assert ConstraintClass.CONSTRUCTION not in result.unmodelled_constraint_classes

    def test_lease_and_sequencing_are_never_claimed(self) -> None:
        """There is no lease admissibility check and no time dimension, so no
        combination of inputs should ever report those two as tested."""
        options = {
            "site-a": (
                _option(
                    "site-a",
                    construction_days=1.0,
                    equipment_units=1.0,
                    labour_headcount=1.0,
                    coverage_delta=1.0,
                    dilution_zone_id="hz-1",
                    period_key="2026Q4",
                ),
            )
        }
        result = _solve(
            options,
            NetPlanConstraints(
                max_budget=1_000_000.0,
                max_construction_days=10.0,
                max_equipment_units=10.0,
                max_labour_headcount=10.0,
                min_coverage_delta=0.0,
                max_open_per_dilution_zone=1,
            ),
        )
        assert set(result.unmodelled_constraint_classes) == {
            ConstraintClass.LEASE,
            ConstraintClass.SEQUENCING,
        }

    def test_the_declaration_survives_serialisation(self) -> None:
        options = {"site-a": (_option("site-a"),)}
        payload = _solve(options, NetPlanConstraints(max_budget=1_000_000.0)).to_dict()
        assert payload["modelled_constraint_classes"] == ["CAPITAL"]
        assert "SEQUENCING" in payload["unmodelled_constraint_classes"]


class TestAlternativesClearTheSameBar:
    def test_an_alternative_may_not_breach_a_resource_cap(self) -> None:
        """Alternatives are offered to a human as plans they could adopt
        instead. One that exceeds construction capacity is not an alternative."""
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
            options,
            NetPlanConstraints(max_budget=1_000_000.0, max_construction_days=50.0),
        )
        for candidate in result.alternatives:
            used = sum(a.construction_days or 0.0 for a in candidate.actions)
            assert used <= 50.0, f"alternative uses {used} construction days"


class TestTheProductionSolverEnforcesTheSameBar:
    """`NetPlanService.solve` routes production solves through the CP-SAT model
    in `modules.netplan.application.production`, not through `solve_network_plan`.

    Constraining only the reference solver leaves the shipped path exactly as it
    was, while the tests above go green -- a change that is fully covered and
    fully ineffective. These tests exercise the production model directly.
    """

    def test_construction_capacity_binds_on_the_production_path(self) -> None:
        """The reviewer's repro: two 40-day openings under a 50-day pool.

        Before this change the production model applied budget, gross margin,
        capacity, risk and action counts, and nothing else -- so it returned
        both openings and reported optimal."""
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
        result = _solve_ortools_cp_sat(
            _scenario(
                options,
                NetPlanConstraints(max_budget=1_000_000.0, max_construction_days=50.0),
            )
        )
        used = sum(a.construction_days or 0.0 for a in result.selected_actions)
        assert used <= 50.0, f"production plan uses {used} construction days"
        assert sum(1 for a in result.selected_actions if a.action is NetworkAction.OPEN) == 1

    def test_equipment_and_labour_bind_on_the_production_path(self) -> None:
        for attribute, cap_label in (
            ("equipment_units", "max_equipment_units"),
            ("labour_headcount", "max_labour_headcount"),
        ):
            options = {
                "site-a": (
                    _option("site-a", **{attribute: 8.0}),
                    _option(
                        "site-a",
                        NetworkAction.KEEP,
                        gm=10_000.0,
                        cost=0.0,
                        **{attribute: 0.0},
                    ),
                ),
                "site-b": (
                    _option("site-b", **{attribute: 8.0}),
                    _option(
                        "site-b",
                        NetworkAction.KEEP,
                        gm=10_000.0,
                        cost=0.0,
                        **{attribute: 0.0},
                    ),
                ),
            }
            result = _solve_ortools_cp_sat(
                _scenario(
                    options,
                    NetPlanConstraints(max_budget=1_000_000.0, **{cap_label: 10.0}),
                )
            )
            used = sum(getattr(a, attribute) or 0.0 for a in result.selected_actions)
            assert used <= 10.0, f"production plan uses {used} of {attribute}"

    def test_the_coverage_floor_binds_on_the_production_path(self) -> None:
        options = {
            "site-a": (
                _option("site-a", NetworkAction.EXIT, gm=800_000.0, cost=0.0, coverage_delta=-5.0),
                _option("site-a", NetworkAction.KEEP, gm=10_000.0, cost=0.0, coverage_delta=0.0),
            )
        }
        result = _solve_ortools_cp_sat(
            _scenario(
                options,
                NetPlanConstraints(max_budget=1_000_000.0, min_coverage_delta=0.0),
            )
        )
        assert [a.action for a in result.selected_actions] == [NetworkAction.KEEP]

    def test_two_openings_in_one_catchment_are_refused_on_the_production_path(self) -> None:
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
        result = _solve_ortools_cp_sat(
            _scenario(
                options,
                NetPlanConstraints(max_budget=1_000_000.0, max_open_per_dilution_zone=1),
            )
        )
        opens = [a for a in result.selected_actions if a.action is NetworkAction.OPEN]
        assert len(opens) == 1

    def test_an_undeclared_cost_is_refused_on_the_production_path(self) -> None:
        """The reference solver refuses a cap no option costed. The production
        solver used to accept it and read the missing figure as zero."""
        options = {"site-a": (_option("site-a"),)}
        with pytest.raises(NetPlanProductionExecutionError) as excinfo:
            _solve_ortools_cp_sat(
                _scenario(
                    options,
                    NetPlanConstraints(max_budget=1_000_000.0, max_construction_days=50.0),
                )
            )
        assert "max_construction_days" in str(excinfo.value)

    def test_an_unzoned_opening_is_refused_on_the_production_path(self) -> None:
        options = {
            "site-a": (_option("site-a", dilution_zone_id="hz-1"),),
            "site-b": (_option("site-b"),),
        }
        with pytest.raises(NetPlanProductionExecutionError) as excinfo:
            _solve_ortools_cp_sat(
                _scenario(
                    options,
                    NetPlanConstraints(max_budget=1_000_000.0, max_open_per_dilution_zone=1),
                )
            )
        assert "dilution_zone_id" in str(excinfo.value)

    def test_the_production_result_says_which_classes_it_tested(self) -> None:
        """An empty pair reads as "no claim made". The production result carried
        exactly that while presenting itself as a solved plan."""
        result = _solve_ortools_cp_sat(
            _scenario(
                {"site-a": (_option("site-a"),)},
                NetPlanConstraints(max_budget=1_000_000.0),
            )
        )
        assert result.modelled_constraint_classes == (ConstraintClass.CAPITAL,)
        assert ConstraintClass.CONSTRUCTION in result.unmodelled_constraint_classes
        assert ConstraintClass.LEASE in result.unmodelled_constraint_classes
        assert ConstraintClass.SEQUENCING in result.unmodelled_constraint_classes

    def test_an_infeasible_production_result_still_declares_its_classes(self) -> None:
        result = _solve_ortools_cp_sat(
            _scenario(
                {"site-a": (_option("site-a", cost=900_000.0, construction_days=90.0),)},
                NetPlanConstraints(max_budget=1_000.0, max_construction_days=50.0),
            )
        )
        assert result.infeasible
        assert ConstraintClass.CONSTRUCTION in result.modelled_constraint_classes
