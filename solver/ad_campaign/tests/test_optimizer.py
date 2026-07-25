from solver.ad_campaign import CampaignConstraints, CampaignOption, solve_ad_campaigns


def _options() -> tuple[CampaignOption, ...]:
    return (
        CampaignOption("a-search", "store-a", "SEARCH", 100, 180, 20),
        CampaignOption("a-social", "store-a", "SOCIAL", 80, 120, 10),
        CampaignOption("b-search", "store-b", "SEARCH", 120, 250, 30),
        CampaignOption("c-social", "store-c", "SOCIAL", 70, 90, 10),
        CampaignOption(
            "d-blocked",
            "store-d",
            "SEARCH",
            1,
            1_000,
            overlapping_intervention=True,
        ),
    )


def test_cp_sat_selects_risk_adjusted_campaigns_and_retains_controls() -> None:
    result = solve_ad_campaigns(
        options=_options(),
        constraints=CampaignConstraints(
            max_budget=220,
            min_campaigns=2,
            max_campaigns=2,
            max_execution_units=2,
            max_campaigns_by_channel={"SEARCH": 2},
            control_store_ids=("store-a", "store-b", "store-c"),
            min_control_stores=1,
        ),
    )

    assert result.solver_status == "OPTIMAL"
    assert {item.option_id for item in result.selected_campaigns} == {
        "a-search",
        "b-search",
    }
    assert result.budget_usage == 220
    assert result.expected_incremental_gm == 430
    assert result.objective_value == 380
    assert result.retained_control_stores == ("store-c",)
    assert set(result.binding_constraints) >= {
        "max_campaigns",
        "max_budget",
        "max_execution_units",
        "min_control_stores",
        "max_campaigns_by_channel.SEARCH",
    }
    assert result.constraint_evaluation["max_budget"]["satisfied"] is True
    assert result.not_selected_reasons["d-blocked"] == "INTERVENTION_OVERLAP"


def test_cp_sat_reports_infeasible_campaign_contract_without_relaxation() -> None:
    result = solve_ad_campaigns(
        options=_options(),
        constraints=CampaignConstraints(
            max_budget=1,
            min_campaigns=3,
            max_campaigns=3,
            max_execution_units=3,
        ),
    )

    assert result.solver_status == "INFEASIBLE"
    assert result.selected_campaigns == ()
    assert {item.constraint for item in result.diagnostics} >= {"max_budget"}
