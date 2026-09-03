"""Contract tests for NetPlan constraint class disclosure transport.

Verifies ODP-FR-NET-002 constraint class disclosure across the entire pipeline:
1. Pure solver / optimizer candidate generation (MIP and CP-SAT).
2. Production solve -> OpsBoard network rebalance projection.
3. Operator HTTP API response contract (/api/v1/operator/network-rebalance).
4. Strict semantics distinguishing None, empty list, and missing fields.
5. Fail-closed contract validation that catches any field loss in projection.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.netplan.application.planning import NetPlanService
from modules.netplan.domain.planning import NetPlanScenario
from modules.netplan.infrastructure.repositories import InMemoryNetPlanRepository
from modules.opsboard.application.network_rebalance import (
    NetworkRebalanceService,
)
from solver.netplan.model import (
    ActionOption,
    ConstraintClass,
    NetPlanConstraints,
    NetworkAction,
)
from solver.netplan.optimizer import (
    solve_network_plan,
)

ALL_CONSTRAINT_CLASSES = frozenset(c.value for c in ConstraintClass)

NETWORK_HEADERS = {
    "x-subject-id": "operator-expansion-manager",
    "x-roles": "site_reviewer,expansion_user",
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
}


def _validate_netplan_scenario_disclosure_contract(scenario: dict[str, Any]) -> None:
    """Rigorous contract validator for a projected NetPlan scenario row.

    Raises AssertionError if any required field is missing, None, ill-typed,
    or violates the partition invariant of ODP-FR-NET-002.
    """
    assert "id" in scenario, "scenario missing 'id'"
    scenario_id = scenario["id"]

    # 1. Check presence - both camelCase and snake_case must be present
    assert "modelledConstraintClasses" in scenario, (
        f"scenario {scenario_id} missing 'modelledConstraintClasses' in projection"
    )
    assert "unmodelledConstraintClasses" in scenario, (
        f"scenario {scenario_id} missing 'unmodelledConstraintClasses' in projection"
    )
    assert "modelled_constraint_classes" in scenario, (
        f"scenario {scenario_id} missing 'modelled_constraint_classes' in projection"
    )
    assert "unmodelled_constraint_classes" in scenario, (
        f"scenario {scenario_id} missing 'unmodelled_constraint_classes' in projection"
    )

    modelled = scenario["modelledConstraintClasses"]
    unmodelled = scenario["unmodelledConstraintClasses"]
    modelled_snake = scenario["modelled_constraint_classes"]
    unmodelled_snake = scenario["unmodelled_constraint_classes"]

    # 2. Check non-None and correct type (list of str)
    assert modelled is not None, f"scenario {scenario_id} 'modelledConstraintClasses' is None"
    assert unmodelled is not None, f"scenario {scenario_id} 'unmodelledConstraintClasses' is None"
    assert isinstance(modelled, list), f"scenario {scenario_id} 'modelledConstraintClasses' must be list"
    assert isinstance(unmodelled, list), f"scenario {scenario_id} 'unmodelledConstraintClasses' must be list"
    assert modelled == modelled_snake, f"scenario {scenario_id} camelCase and snake_case modelled mismatch"
    assert unmodelled == unmodelled_snake, f"scenario {scenario_id} camelCase and snake_case unmodelled mismatch"

    # 3. Capital is always modelled (max_budget is required) -> modelled is never empty
    assert len(modelled) > 0, (
        f"scenario {scenario_id} 'modelledConstraintClasses' is empty; CAPITAL must always be present"
    )
    assert "CAPITAL" in modelled, f"scenario {scenario_id} missing 'CAPITAL' in modelledConstraintClasses"

    # 4. Lease and sequencing are never modelled in current formulation
    assert "LEASE" in unmodelled, f"scenario {scenario_id} 'LEASE' must be in unmodelledConstraintClasses"
    assert "SEQUENCING" in unmodelled, f"scenario {scenario_id} 'SEQUENCING' must be in unmodelledConstraintClasses"

    # 5. Check valid members of ConstraintClass enum
    for c in modelled:
        assert c in ALL_CONSTRAINT_CLASSES, f"unknown constraint class '{c}' in modelledConstraintClasses"
    for c in unmodelled:
        assert c in ALL_CONSTRAINT_CLASSES, f"unknown constraint class '{c}' in unmodelledConstraintClasses"

    # 6. Check disjoint partition invariant (modelled ∩ unmodelled == ∅, modelled ∪ unmodelled == ALL)
    modelled_set = set(modelled)
    unmodelled_set = set(unmodelled)
    assert len(modelled_set) == len(modelled), f"scenario {scenario_id} duplicate in modelledConstraintClasses"
    assert len(unmodelled_set) == len(unmodelled), f"scenario {scenario_id} duplicate in unmodelledConstraintClasses"
    overlap = modelled_set & unmodelled_set
    assert not overlap, f"scenario {scenario_id} overlap between modelled and unmodelled: {overlap}"
    union = modelled_set | unmodelled_set
    assert union == ALL_CONSTRAINT_CLASSES, (
        f"scenario {scenario_id} union {union} != all 8 classes {ALL_CONSTRAINT_CLASSES}"
    )


def _build_test_scenario(
    scenario_id: str = "sc-transport-001",
    *,
    max_budget: float = 200.0,
    max_construction_days: float | None = None,
    max_equipment_units: float | None = None,
    max_labour_headcount: float | None = None,
    min_coverage_delta: float | None = None,
    max_open_per_dilution_zone: int | None = None,
) -> NetPlanScenario:
    constraints = NetPlanConstraints(
        max_budget=max_budget,
        max_construction_days=max_construction_days,
        max_equipment_units=max_equipment_units,
        max_labour_headcount=max_labour_headcount,
        min_coverage_delta=min_coverage_delta,
        max_open_per_dilution_zone=max_open_per_dilution_zone,
    )
    options_by_entity = {
        "store-1": (
            ActionOption(
                entity_id="store-1",
                action=NetworkAction.KEEP,
                expected_gross_margin=50.0,
                budget_cost=0.0,
                risk_score=0.1,
                construction_days=0.0,
                equipment_units=0.0,
                labour_headcount=0.0,
                coverage_delta=0.0,
            ),
            ActionOption(
                entity_id="store-1",
                action=NetworkAction.IMPROVE,
                expected_gross_margin=80.0,
                budget_cost=30.0,
                risk_score=0.2,
                construction_days=10.0,
                equipment_units=2.0,
                labour_headcount=1.0,
                coverage_delta=0.0,
            ),
        ),
        "store-2": (
            ActionOption(
                entity_id="store-2",
                action=NetworkAction.KEEP,
                expected_gross_margin=40.0,
                budget_cost=0.0,
                risk_score=0.1,
                construction_days=0.0,
                equipment_units=0.0,
                labour_headcount=0.0,
                coverage_delta=0.0,
            ),
            ActionOption(
                entity_id="store-2",
                action=NetworkAction.MOVE,
                expected_gross_margin=100.0,
                budget_cost=80.0,
                risk_score=0.3,
                construction_days=25.0,
                equipment_units=5.0,
                labour_headcount=3.0,
                coverage_delta=1.0,
                dilution_zone_id="zone-north",
            ),
        ),
        "site-new": (
            ActionOption(
                entity_id="site-new",
                action=NetworkAction.OPEN,
                expected_gross_margin=120.0,
                budget_cost=90.0,
                risk_score=0.25,
                construction_days=30.0,
                equipment_units=6.0,
                labour_headcount=4.0,
                coverage_delta=2.0,
                dilution_zone_id="zone-south",
            ),
        ),
    }
    return NetPlanScenario.create(
        scenario_id=scenario_id,
        tenant_id="tenant-test",
        scenario_name="Disclosure Transport Test Scenario",
        planning_horizon="2026-Q4",
        options_by_entity=options_by_entity,
        constraints=constraints,
        correlation_id="corr-test-001",
    )


class TestNetPlanDisclosureTransport:
    """Test suite asserting constraint class disclosure reaches Operator contract."""

    def test_pure_solver_candidate_and_result_preserve_constraint_classes(self) -> None:
        """Both primary result and candidate alternatives must carry modelled & unmodelled classes."""
        scenario = _build_test_scenario(
            max_budget=200.0,
            max_construction_days=50.0,
            max_equipment_units=10.0,
        )
        result = solve_network_plan(
            options_by_entity=scenario.options_by_entity,
            constraints=scenario.constraints,
            alternative_limit=3,
        )

        assert result.solver_status in ("optimal", "feasible")
        assert result.modelled_constraint_classes == (
            ConstraintClass.CAPITAL,
            ConstraintClass.CONSTRUCTION,
            ConstraintClass.EQUIPMENT,
        )
        assert set(result.unmodelled_constraint_classes) == {
            ConstraintClass.LEASE,
            ConstraintClass.LABOUR,
            ConstraintClass.COVERAGE,
            ConstraintClass.DILUTION,
            ConstraintClass.SEQUENCING,
        }

        # Check serialization on solve result
        result_dict = result.to_dict()
        assert result_dict["modelled_constraint_classes"] == ["CAPITAL", "CONSTRUCTION", "EQUIPMENT"]
        assert set(result_dict["unmodelled_constraint_classes"]) == {
            "LEASE",
            "LABOUR",
            "COVERAGE",
            "DILUTION",
            "SEQUENCING",
        }

        # Check alternatives carry constraint classes as well
        assert len(result.alternatives) > 0, "expected at least one alternative"
        for alt in result.alternatives:
            assert alt.modelled_constraint_classes == result.modelled_constraint_classes
            assert alt.unmodelled_constraint_classes == result.unmodelled_constraint_classes
            alt_dict = alt.to_dict()
            assert alt_dict["modelled_constraint_classes"] == ["CAPITAL", "CONSTRUCTION", "EQUIPMENT"]
            assert set(alt_dict["unmodelled_constraint_classes"]) == {
                "LEASE",
                "LABOUR",
                "COVERAGE",
                "DILUTION",
                "SEQUENCING",
            }

    def test_opsboard_service_solve_projects_constraint_classes_to_plan_rows(self) -> None:
        """NetPlan solve through NetworkRebalanceService must retain constraint classes on all plan rows."""
        from modules.avm.domain.valuation import ValuationCase, ValuationInput
        from modules.avm.infrastructure.repositories import InMemoryAVMRepository

        repo = InMemoryNetPlanRepository()
        avm_repo = InMemoryAVMRepository()
        scenario = _build_test_scenario(
            scenario_id="NP-SCEN-801",
            max_budget=200.0,
            max_construction_days=60.0,
        )
        repo.save_scenario(scenario)

        val_input = ValuationInput(
            store_id="store-1",
            gm_ttm=100000.0,
            forecast_gm_next_12m=120000.0,
            asset_book_value=500000.0,
            equipment_fair_value=300000.0,
        )
        case = ValuationCase.create(
            val_input,
            created_by="test-op",
            correlation_id="corr-avm-001",
        )
        avm_repo.save_case(case)

        service = NetworkRebalanceService(
            avm_repository=avm_repo,
            netplan_repository=repo,
            tenant_id="tenant-test",
            require_canonical=True,
        )
        store = service._store("store-1")
        store["status"] = "avmready"

        response = service.solve_netplan(
            store_id="store-1",
            actor_role_id="expansionManager",
            actor_name="Test Operator",
            idempotency_key=None,
            correlation_id="corr-reb-001",
        )

        scenarios = response["store"]["netPlanScenarios"]
        assert len(scenarios) >= 1, "expected at least primary scenario"

        # Validate primary and all alternatives
        for plan_row in scenarios:
            _validate_netplan_scenario_disclosure_contract(plan_row)
            assert plan_row["modelledConstraintClasses"] == ["CAPITAL", "CONSTRUCTION"]
            assert "LEASE" in plan_row["unmodelledConstraintClasses"]
            assert "SEQUENCING" in plan_row["unmodelledConstraintClasses"]

    def test_operator_http_api_response_contract_includes_constraint_classes(self) -> None:
        """Operator HTTP API endpoints return netPlanScenarios adhering to disclosure contract."""
        app = create_app()
        client = TestClient(app)

        # 1. Reset rebalance state
        reset_res = client.post("/api/v1/operator/network-rebalance/reset", headers=NETWORK_HEADERS)
        assert reset_res.status_code == 200

        # 2. Advance to avmready
        client.post(
            "/api/v1/operator/network-rebalance/stores/RB-801/avm/request",
            headers={**NETWORK_HEADERS, "idempotency-key": "idem-dt-avm-req"},
            json={"actorRoleId": "expansionManager"},
        )
        client.post(
            "/api/v1/operator/network-rebalance/stores/RB-801/avm/complete",
            headers={**NETWORK_HEADERS, "idempotency-key": "idem-dt-avm-comp"},
            json={"actorRoleId": "expansionManager"},
        )

        # 3. Solve NetPlan
        solve_res = client.post(
            "/api/v1/operator/network-rebalance/stores/RB-801/netplan/solve",
            headers={**NETWORK_HEADERS, "idempotency-key": "idem-dt-np-solve"},
            json={"actorRoleId": "expansionManager"},
        )
        assert solve_res.status_code == 200, solve_res.text
        solved_store = solve_res.json()["store"]

        scenarios = solved_store["netPlanScenarios"]
        assert len(scenarios) == 3  # keep, move, exit in seed fixture
        for row in scenarios:
            _validate_netplan_scenario_disclosure_contract(row)

        # 4. Verify GET /operator/network-rebalance returns the same valid contract
        get_res = client.get("/api/v1/operator/network-rebalance", headers=NETWORK_HEADERS)
        assert get_res.status_code == 200
        get_store = get_res.json()["stores"][0]
        for row in get_store["netPlanScenarios"]:
            _validate_netplan_scenario_disclosure_contract(row)

    def test_partition_invariant_across_different_constraint_configurations(self) -> None:
        """Every constraint configuration must produce a valid 8-class partition."""
        configs = [
            # Config 1: Budget only
            (
                NetPlanConstraints(max_budget=100.0),
                ["CAPITAL"],
                ["LEASE", "CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION", "SEQUENCING"],
            ),
            # Config 2: Budget + Construction
            (
                NetPlanConstraints(max_budget=100.0, max_construction_days=30.0),
                ["CAPITAL", "CONSTRUCTION"],
                ["LEASE", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION", "SEQUENCING"],
            ),
            # Config 3: Budget + Construction + Equipment + Labour
            (
                NetPlanConstraints(
                    max_budget=100.0,
                    max_construction_days=30.0,
                    max_equipment_units=5.0,
                    max_labour_headcount=3.0,
                ),
                ["CAPITAL", "CONSTRUCTION", "EQUIPMENT", "LABOUR"],
                ["LEASE", "COVERAGE", "DILUTION", "SEQUENCING"],
            ),
            # Config 4: All 6 available caps
            (
                NetPlanConstraints(
                    max_budget=100.0,
                    max_construction_days=30.0,
                    max_equipment_units=5.0,
                    max_labour_headcount=3.0,
                    min_coverage_delta=1.0,
                    max_open_per_dilution_zone=2,
                ),
                ["CAPITAL", "CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION"],
                ["LEASE", "SEQUENCING"],
            ),
        ]

        for constraints, expected_modelled, expected_unmodelled in configs:
            modelled = [c.value for c in constraints.modelled_classes()]
            unmodelled = [c.value for c in constraints.unmodelled_classes()]
            assert modelled == expected_modelled
            assert set(unmodelled) == set(expected_unmodelled)
            assert set(modelled) | set(unmodelled) == ALL_CONSTRAINT_CLASSES
            assert not (set(modelled) & set(unmodelled))

    def test_projection_loss_detection_catches_missing_or_corrupt_fields(self) -> None:
        """The validator must reject any projected scenario missing constraint disclosure."""
        valid_scenario = {
            "id": "valid-001",
            "name": "Valid Scenario",
            "modelledConstraintClasses": ["CAPITAL", "CONSTRUCTION"],
            "unmodelledConstraintClasses": [
                "LEASE",
                "EQUIPMENT",
                "LABOUR",
                "COVERAGE",
                "DILUTION",
                "SEQUENCING",
            ],
            "modelled_constraint_classes": ["CAPITAL", "CONSTRUCTION"],
            "unmodelled_constraint_classes": [
                "LEASE",
                "EQUIPMENT",
                "LABOUR",
                "COVERAGE",
                "DILUTION",
                "SEQUENCING",
            ],
        }
        # Passes for valid
        _validate_netplan_scenario_disclosure_contract(valid_scenario)

        # Fails when modelledConstraintClasses is dropped
        corrupted_1 = {k: v for k, v in valid_scenario.items() if k != "modelledConstraintClasses"}
        with pytest.raises(AssertionError, match="missing 'modelledConstraintClasses'"):
            _validate_netplan_scenario_disclosure_contract(corrupted_1)

        # Fails when unmodelledConstraintClasses is dropped
        corrupted_2 = {k: v for k, v in valid_scenario.items() if k != "unmodelledConstraintClasses"}
        with pytest.raises(AssertionError, match="missing 'unmodelledConstraintClasses'"):
            _validate_netplan_scenario_disclosure_contract(corrupted_2)

        # Fails when modelledConstraintClasses is None
        corrupted_3 = {**valid_scenario, "modelledConstraintClasses": None}
        with pytest.raises(AssertionError, match="'modelledConstraintClasses' is None"):
            _validate_netplan_scenario_disclosure_contract(corrupted_3)

        # Fails when modelledConstraintClasses is empty
        corrupted_4 = {
            **valid_scenario,
            "modelledConstraintClasses": [],
            "modelled_constraint_classes": [],
        }
        with pytest.raises(AssertionError, match="'modelledConstraintClasses' is empty"):
            _validate_netplan_scenario_disclosure_contract(corrupted_4)

        # Fails when overlap exists between modelled and unmodelled
        corrupted_5 = {
            **valid_scenario,
            "unmodelledConstraintClasses": ["CAPITAL", "LEASE", "SEQUENCING"],
            "unmodelled_constraint_classes": ["CAPITAL", "LEASE", "SEQUENCING"],
        }
        with pytest.raises(AssertionError, match="overlap between modelled and unmodelled"):
            _validate_netplan_scenario_disclosure_contract(corrupted_5)
