"""Contract tests for NetPlan constraint class disclosure transport.

Verifies ODP-FR-NET-002 constraint class disclosure across the entire pipeline:
1. Library MIP candidate generation (``solve_network_plan``).
2. Solve -> OpsBoard network rebalance projection, over both solvers: the
   library MIP and, separately, the CP-SAT ``NetPlanProductionExecutor`` that
   ``NetPlanService`` routes to when ``production_required`` is set. Asserting
   only the first is what let the two drift apart before.
3. Operator HTTP API response contract (/api/v1/operator/network-rebalance).
4. Strict semantics distinguishing None, empty list, and missing fields.
5. Fail-closed contract validation that catches any field loss in projection.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
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
                source_snapshot_ids=("snap-2026-09",),
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
                source_snapshot_ids=("snap-2026-09",),
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
                source_snapshot_ids=("snap-2026-09",),
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
                source_snapshot_ids=("snap-2026-09",),
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
                source_snapshot_ids=("snap-2026-09",),
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

    def test_solve_netplan_projection_fails_closed_when_solve_result_missing_or_none_classes(self) -> None:
        """NetworkRebalanceService.solve_netplan must fail closed if solver result lacks constraint classes."""
        from unittest.mock import MagicMock, patch

        from modules.avm.domain.valuation import ValuationCase, ValuationInput
        from modules.avm.infrastructure.repositories import InMemoryAVMRepository

        avm_repo = InMemoryAVMRepository()
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

        netplan_repo = InMemoryNetPlanRepository()
        scenario = _build_test_scenario("sc-failclosed-001")
        netplan_repo.save_scenario(scenario)

        service = NetworkRebalanceService(
            avm_repository=avm_repo,
            netplan_repository=netplan_repo,
            tenant_id="tenant-test",
            require_canonical=True,
        )
        store = service._store("store-1")
        store["status"] = "avmready"

        # 1. Missing modelled_constraint_classes in result_payload
        mock_solve = MagicMock()
        mock_solve.is_stale.return_value = False
        mock_solve.solved_at.isoformat.return_value = "2026-09-03T12:00:00Z"
        mock_solve.result.to_dict.return_value = {
            "solver_status": "optimal",
            "solver_version": "netplan-v1",
            "objective_value": 100.0,
            "expected_gross_margin": 100.0,
            "budget_usage": 50.0,
            "average_risk": 0.1,
            "capacity_delta": 1,
            "selected_actions": [],
            "binding_constraints": ["max_budget"],
            "unmodelled_constraint_classes": ["LEASE", "SEQUENCING"],
            # modelled_constraint_classes omitted
        }

        with patch("modules.opsboard.application.network_rebalance.NetPlanService") as mock_netplan_service_cls:
            mock_netplan_service_cls.return_value.solve.return_value = mock_solve
            with pytest.raises(ValueError, match="missing 'modelled_constraint_classes'"):
                service.solve_netplan(
                    store_id="store-1",
                    actor_role_id="expansionManager",
                    actor_name="Test Actor",
                    idempotency_key=None,
                    correlation_id="test-corr",
                )

        # 2. None unmodelled_constraint_classes in result_payload
        mock_solve.result.to_dict.return_value = {
            "solver_status": "optimal",
            "solver_version": "netplan-v1",
            "objective_value": 100.0,
            "expected_gross_margin": 100.0,
            "budget_usage": 50.0,
            "average_risk": 0.1,
            "capacity_delta": 1,
            "selected_actions": [],
            "binding_constraints": ["max_budget"],
            "modelled_constraint_classes": ["CAPITAL"],
            "unmodelled_constraint_classes": None,
        }

        with patch("modules.opsboard.application.network_rebalance.NetPlanService") as mock_netplan_service_cls:
            mock_netplan_service_cls.return_value.solve.return_value = mock_solve
            with pytest.raises(ValueError, match="missing 'unmodelled_constraint_classes'"):
                service.solve_netplan(
                    store_id="store-1",
                    actor_role_id="expansionManager",
                    actor_name="Test Actor",
                    idempotency_key=None,
                    correlation_id="test-corr",
                )

        # 3. Alternative missing modelled_constraint_classes
        mock_solve.result.to_dict.return_value = {
            "solver_status": "optimal",
            "solver_version": "netplan-v1",
            "objective_value": 100.0,
            "expected_gross_margin": 100.0,
            "budget_usage": 50.0,
            "average_risk": 0.1,
            "capacity_delta": 1,
            "selected_actions": [],
            "binding_constraints": ["max_budget"],
            "modelled_constraint_classes": ["CAPITAL"],
            "unmodelled_constraint_classes": [
                "LEASE",
                "CONSTRUCTION",
                "EQUIPMENT",
                "LABOUR",
                "COVERAGE",
                "DILUTION",
                "SEQUENCING",
            ],
            "alternatives": [
                {
                    "objective_value": 90.0,
                    "expected_gross_margin": 90.0,
                    "budget_usage": 40.0,
                    "average_risk": 0.1,
                    "capacity_delta": 1,
                    "actions": [],
                    "binding_constraints": ["max_budget"],
                    # modelled_constraint_classes omitted in alternative
                    "unmodelled_constraint_classes": ["LEASE", "SEQUENCING"],
                }
            ],
        }

        with patch("modules.opsboard.application.network_rebalance.NetPlanService") as mock_netplan_service_cls:
            mock_netplan_service_cls.return_value.solve.return_value = mock_solve
            with pytest.raises(ValueError, match="Alternative 1 missing 'modelled_constraint_classes'"):
                service.solve_netplan(
                    store_id="store-1",
                    actor_role_id="expansionManager",
                    actor_name="Test Actor",
                    idempotency_key=None,
                    correlation_id="test-corr",
                )

    def test_production_cp_sat_route_projects_constraint_classes_through_opsboard(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The route production actually takes must transport disclosure, not just the library one.

        There are two solvers. ``solve_network_plan`` builds a pywraplp MIP;
        ``NetPlanProductionExecutor`` builds a CP-SAT model, and
        ``NetPlanService`` routes to the second whenever ``production_required``
        is set. Every other test in this file leaves ``production_required``
        false, so they all walk the library. That is the exact shape
        ODP_NETPLAN_CONSTRAINT_CLASSES_2026-09-01 records being committed inside
        the change that catalogued it: a guarantee proven only on the path the
        runtime does not use. This test pins the other one.
        """
        pytest.importorskip("ortools", reason="the production solver needs OR-Tools CP-SAT")

        from modules.avm.domain.valuation import ValuationCase, ValuationInput
        from modules.avm.infrastructure.repositories import InMemoryAVMRepository
        from modules.netplan.application.production import NetPlanProductionExecutor

        # runtime_mode stays None so strict_production_composition does not also
        # demand a durable repository; production_required still flips, which is
        # the branch under test.
        monkeypatch.setenv("ODP_PRODUCT_MODE", "production")

        avm_repo = InMemoryAVMRepository()
        case = ValuationCase.create(
            ValuationInput(
                store_id="store-1",
                gm_ttm=100000.0,
                forecast_gm_next_12m=120000.0,
                asset_book_value=500000.0,
                equipment_fair_value=300000.0,
            ),
            created_by="test-op",
            correlation_id="corr-avm-prod-001",
        )
        avm_repo.save_case(case)

        netplan_repo = InMemoryNetPlanRepository()
        netplan_repo.save_scenario(
            _build_test_scenario(
                scenario_id="NP-SCEN-PROD-801",
                max_budget=200.0,
                max_construction_days=60.0,
            )
        )

        # Recording wrapper: without it a green assertion below would also be
        # satisfied by the library MIP, which is precisely how the two solvers
        # drifted apart unnoticed the first time.
        executed: list[str] = []

        class RecordingExecutor(NetPlanProductionExecutor):
            def execute(self, scenario, **kwargs):  # type: ignore[no-untyped-def]
                executed.append(scenario.scenario_id)
                return super().execute(scenario, **kwargs)

        executor = RecordingExecutor()
        service = NetworkRebalanceService(
            avm_repository=avm_repo,
            netplan_repository=netplan_repo,
            netplan_production_executor=executor,
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
            correlation_id="corr-reb-prod-001",
        )

        assert executed == ["NP-SCEN-PROD-801"], (
            "NetPlanService did not route through the production CP-SAT executor; "
            "this test would then be asserting the library path a second time"
        )

        scenarios = response["store"]["netPlanScenarios"]
        assert len(scenarios) >= 1, "expected at least primary scenario"
        for plan_row in scenarios:
            _validate_netplan_scenario_disclosure_contract(plan_row)
            assert plan_row["modelledConstraintClasses"] == ["CAPITAL", "CONSTRUCTION"]

    def test_openapi_schema_and_generated_types_contain_typed_netplan_contract(self) -> None:
        """OpenAPI artifact and generated TypeScript types must define typed ConstraintClass and RebalanceScenario."""
        import json
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        openapi_path = repo_root / "packages" / "openapi-client" / "openapi.json"
        types_path = repo_root / "packages" / "openapi-client" / "src" / "generated" / "types.ts"

        assert openapi_path.exists(), "openapi.json must exist"
        assert types_path.exists(), "types.ts must exist"

        openapi_data = json.loads(openapi_path.read_text(encoding="utf-8"))
        schemas = openapi_data.get("components", {}).get("schemas", {})

        # 1. ConstraintClass schema exists and has 8 enum values
        assert "ConstraintClass" in schemas
        constraint_enum = schemas["ConstraintClass"].get("enum", [])
        assert set(constraint_enum) == ALL_CONSTRAINT_CLASSES

        # 2. RebalanceScenario schema exists and has required constraint class arrays
        assert "RebalanceScenario" in schemas
        scenario_schema = schemas["RebalanceScenario"]
        scenario_props = scenario_schema.get("properties", {})
        assert "modelledConstraintClasses" in scenario_props
        assert "unmodelledConstraintClasses" in scenario_props
        assert "modelled_constraint_classes" in scenario_props
        assert "unmodelled_constraint_classes" in scenario_props

        # Verify items ref ConstraintClass
        modelled_items = scenario_props["modelledConstraintClasses"].get("items", {})
        assert modelled_items.get("$ref") == "#/components/schemas/ConstraintClass"

        # Verify required list includes constraint class fields
        scenario_required = scenario_schema.get("required", [])
        assert "modelledConstraintClasses" in scenario_required
        assert "unmodelledConstraintClasses" in scenario_required
        assert "modelled_constraint_classes" in scenario_required
        assert "unmodelled_constraint_classes" in scenario_required

        # 3. Verify response models are attached to operator network-rebalance routes
        paths = openapi_data.get("paths", {})
        get_op = paths.get("/api/v1/operator/network-rebalance", {}).get("get", {})
        get_200 = get_op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema", {})
        assert get_200.get("$ref") == "#/components/schemas/NetworkRebalanceSnapshotResponse"

        solve_op = paths.get("/api/v1/operator/network-rebalance/stores/{store_id}/netplan/solve", {}).get("post", {})
        solve_200 = solve_op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema", {})
        assert solve_200.get("$ref") == "#/components/schemas/NetworkRebalanceMutationResponse"

        # 4. Verify generated types.ts content
        types_content = types_path.read_text(encoding="utf-8")
        assert "export type ConstraintClass =" in types_content
        for c in ALL_CONSTRAINT_CLASSES:
            assert f'"{c}"' in types_content
        assert "export type RebalanceScenario =" in types_content
        assert "modelledConstraintClasses: (ConstraintClass)[];" in types_content or "modelledConstraintClasses: ConstraintClass[];" in types_content
        assert "export type NetworkRebalanceSnapshotResponse =" in types_content
        assert "export type NetworkRebalanceMutationResponse =" in types_content
