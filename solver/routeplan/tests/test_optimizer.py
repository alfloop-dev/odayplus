from solver.routeplan import RouteConstraints, RouteOption, solve_routeplan


def _constraints(**overrides: object) -> RouteConstraints:
    values = {
        "quarters": ("2027Q1", "2027Q2", "2027Q3"),
        "capital_budget_by_quarter": {
            "2027Q1": 200,
            "2027Q2": 200,
            "2027Q3": 200,
        },
        "labor_capacity_by_quarter": {
            "2027Q1": 2,
            "2027Q2": 2,
            "2027Q3": 2,
        },
        "construction_capacity_by_quarter": {
            "2027Q1": 1,
            "2027Q2": 1,
            "2027Q3": 1,
        },
        "min_total_openings": 2,
        "max_total_openings": 2,
        "min_region_openings": {"NORTH": 1, "SOUTH": 1},
        "minimum_region_spacing": {"NORTH": 2},
    }
    values.update(overrides)
    return RouteConstraints(**values)


def _options() -> tuple[RouteOption, ...]:
    return (
        RouteOption("a-q1", "site-a", "2027Q1", "NORTH", 500, 180, 1, 1, 0.1, 0.1),
        RouteOption("a-q2", "site-a", "2027Q2", "NORTH", 480, 180, 1, 1, 0.1, 0.1),
        RouteOption("b-q2", "site-b", "2027Q2", "NORTH", 450, 170, 1, 1, 0.1, 0.1),
        RouteOption("b-q3", "site-b", "2027Q3", "NORTH", 430, 170, 1, 1, 0.1, 0.1),
        RouteOption("c-q2", "site-c", "2027Q2", "SOUTH", 420, 160, 1, 1, 0.1, 0.1),
        RouteOption("c-q3", "site-c", "2027Q3", "SOUTH", 400, 160, 1, 1, 0.1, 0.1),
    )


def test_cp_sat_builds_constrained_multiquarter_route_with_alternative() -> None:
    result = solve_routeplan(
        options=_options(),
        constraints=_constraints(),
        cannibalization_penalty=100,
        risk_penalty=100,
        alternative_limit=1,
    )

    assert result.solver_status == "OPTIMAL"
    assert {(item.site_id, item.quarter) for item in result.scheduled_openings} == {
        ("site-a", "2027Q1"),
        ("site-c", "2027Q2"),
    }
    assert result.total_capital_cost == 340
    assert result.constraint_evaluation["min_region_openings.SOUTH"]["satisfied"] is True
    assert result.alternatives
    assert result.alternatives[0].objective_value <= result.objective_value


def test_cp_sat_returns_region_infeasibility_diagnostic() -> None:
    result = solve_routeplan(
        options=tuple(item for item in _options() if item.region == "NORTH"),
        constraints=_constraints(),
    )

    assert result.solver_status == "INFEASIBLE"
    assert result.scheduled_openings == ()
    assert any(
        item.constraint == "min_region_openings.SOUTH"
        for item in result.diagnostics
    )


def test_cp_sat_enforces_minimum_spacing_between_same_region_openings() -> None:
    result = solve_routeplan(
        options=_options(),
        constraints=_constraints(
            min_total_openings=3,
            max_total_openings=3,
            min_region_openings={"NORTH": 2, "SOUTH": 1},
        ),
        cannibalization_penalty=100,
        risk_penalty=100,
    )

    assert result.solver_status == "OPTIMAL"
    north_quarters = {
        item.quarter for item in result.scheduled_openings if item.region == "NORTH"
    }
    assert north_quarters == {"2027Q1", "2027Q3"}
