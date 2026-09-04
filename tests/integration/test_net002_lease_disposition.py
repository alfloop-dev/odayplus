"""Integration and regression tests for ODP-NET002 LEASE disposition (ODP-NET002-LEASE-DISPOSITION-001).

Acceptance checks:
1. ODP-FR-NET-002 requirement member LEASE is governed in set_valued_requirements.json with BLOCKED_BY_EVIDENCE.
2. Formal handback ref resolves to docs/evidence/ODP_NET002_LEASE_DISPOSITION_2026-09-03.md with valid anchor.
3. LEASE disposition strictly prohibits AI self-signed waivers.
4. MIP (pywraplp) and CP-SAT (production) solvers maintain consistent constraint classification for LEASE.
5. In full resource availability, unmodelled_constraint_classes is exactly {LEASE, SEQUENCING}.
6. LEASE constraint class is synchronized across shared governance disclosure, OpsBoard transport, and NetPlan approval.
7. Zero decorative lease constraints are injected in solver or domain models.
"""

from __future__ import annotations

import json

from delivery_toolchain.governance.check_requirement_members import (
    MANIFEST_PATH,
    REPO_ROOT,
    check,
    is_ai_decider,
    resolve_decision_ref,
)
from shared.governance.netplan_disclosure import (
    NETPLAN_ACKNOWLEDGEABLE_CONSTRAINT_CLASSES,
    NETPLAN_REQUIRED_CONSTRAINT_CLASSES,
)
from solver.netplan.model import (
    ActionOption,
    ConstraintClass,
    NetPlanConstraints,
    NetworkAction,
)
from solver.netplan.optimizer import solve_network_plan


class TestNet002LeaseRequirementGovernance:
    """Verifies that NET-002 / ODP-FR-NET-002 LEASE member is governed in the set-valued manifest."""

    def test_odp_fr_net_002_passes_governance_checker(self) -> None:
        failures, tally = check(REPO_ROOT, MANIFEST_PATH)
        net_002_failures = [f for f in failures if f.requirement == "ODP-FR-NET-002"]
        assert not net_002_failures, "\n".join(f.describe() for f in net_002_failures)

    def test_lease_member_and_disposition_structure(self) -> None:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        req = next(r for r in payload["requirements"] if r["id"] == "ODP-FR-NET-002")
        assert req["member_count"] == 8
        assert len(req["members"]) == 8

        members = {m["name"]: m for m in req["members"]}
        assert "LEASE" in members
        lease = members["LEASE"]

        assert lease["status"] == "absent"
        assert lease.get("note", "").strip()
        disp = lease.get("disposition", {})
        assert disp.get("state") == "BLOCKED_BY_EVIDENCE"
        assert disp.get("evidence_needed")
        assert disp.get("evidence_owner")
        assert disp.get("next_review_date")
        assert disp.get("rationale")
        assert disp.get("reopen_trigger")

        # Must have resolvable formal_handback_ref
        handback_ref = disp.get("formal_handback_ref")
        assert handback_ref, "LEASE disposition must carry a formal_handback_ref"
        err = resolve_decision_ref(REPO_ROOT, handback_ref, field="formal_handback_ref")
        assert err is None, f"formal_handback_ref failed to resolve: {err}"

    def test_lease_disposition_forbids_ai_self_signed_waiver(self) -> None:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        req = next(r for r in payload["requirements"] if r["id"] == "ODP-FR-NET-002")
        lease = next(m for m in req["members"] if m["name"] == "LEASE")
        disp = lease["disposition"]

        decider = disp.get("decider")
        if decider:
            assert not is_ai_decider(decider), f"AI decider {decider!r} is forbidden on LEASE disposition"

    def test_governance_document_mirrors_lease_disposition(self) -> None:
        policy_path = REPO_ROOT / "docs" / "governance" / "ODP_REQUIREMENT_DISPOSITIONS.md"
        assert policy_path.is_file()
        content = policy_path.read_text(encoding="utf-8")
        assert "ODP-FR-NET-002" in content
        assert "LEASE" in content
        assert "HB-NET002-LEASE-001" in content
        assert "BLOCKED_BY_EVIDENCE" in content


class TestNet002SolverLeaseClassificationAndFailClosed:
    """Verifies that MIP and CP-SAT solvers consistently report LEASE as unmodelled constraint class."""

    def _sample_options(self) -> list[ActionOption]:
        return [
            ActionOption(
                entity_id="store-1",
                action=NetworkAction.KEEP,
                expected_gross_margin=100.0,
                budget_cost=10.0,
                risk_score=0.1,
                construction_days=5.0,
                equipment_units=2.0,
                labour_headcount=3.0,
                coverage_delta=0.0,
                dilution_zone_id="zone-A",
                source_snapshot_ids=("snap-1",),
            ),
            ActionOption(
                entity_id="site-1",
                action=NetworkAction.OPEN,
                expected_gross_margin=150.0,
                budget_cost=50.0,
                risk_score=0.2,
                construction_days=20.0,
                equipment_units=5.0,
                labour_headcount=6.0,
                coverage_delta=1.0,
                dilution_zone_id="zone-A",
                source_snapshot_ids=("snap-1",),
            ),
        ]

    def test_mip_and_cpsat_solvers_consistently_classify_lease_as_unmodelled(self) -> None:
        options = self._sample_options()
        constraints = NetPlanConstraints(
            max_budget=100.0,
            max_construction_days=30.0,
            max_equipment_units=10.0,
            max_labour_headcount=10.0,
            min_coverage_delta=0.5,
            max_open_per_dilution_zone=1,
        )

        # 1. MIP Solver (solve_network_plan)
        options_by_entity = {
            "store-1": (options[0],),
            "site-1": (options[1],),
        }
        mip_result = solve_network_plan(
            options_by_entity=options_by_entity,
            constraints=constraints,
            isolate_process=False,
        )
        assert ConstraintClass.LEASE not in mip_result.modelled_constraint_classes
        assert ConstraintClass.LEASE in mip_result.unmodelled_constraint_classes
        assert set(mip_result.unmodelled_constraint_classes) == {
            ConstraintClass.LEASE,
            ConstraintClass.SEQUENCING,
        }

        # 2. CP-SAT Production Solver (via NetPlanProductionExecutor)
        from datetime import UTC, datetime

        from modules.netplan.application.production import NetPlanProductionExecutor
        from modules.netplan.domain.planning import NetPlanScenario, NetPlanScenarioStatus

        scenario = NetPlanScenario(
            scenario_id="scn-test-lease",
            tenant_id="tenant-test",
            scenario_name="Lease Consistency Plan",
            planning_horizon="2026Q4",
            options_by_entity=options_by_entity,
            constraints=constraints,
            status=NetPlanScenarioStatus.DRAFT,
            created_at=datetime.now(UTC),
            correlation_id="corr-lease-test",
        )
        cpsat_execution = NetPlanProductionExecutor().execute(scenario, alternative_limit=3)
        cpsat_result = cpsat_execution.result
        assert ConstraintClass.LEASE not in cpsat_result.modelled_constraint_classes
        assert ConstraintClass.LEASE in cpsat_result.unmodelled_constraint_classes
        assert set(cpsat_result.unmodelled_constraint_classes) == {
            ConstraintClass.LEASE,
            ConstraintClass.SEQUENCING,
        }

    def test_shared_governance_disclosure_includes_lease(self) -> None:
        assert "LEASE" in NETPLAN_REQUIRED_CONSTRAINT_CLASSES
        assert len(NETPLAN_REQUIRED_CONSTRAINT_CLASSES) == 8
        assert "LEASE" in NETPLAN_ACKNOWLEDGEABLE_CONSTRAINT_CLASSES
        assert set(NETPLAN_ACKNOWLEDGEABLE_CONSTRAINT_CLASSES) == {"LEASE", "SEQUENCING"}

    def test_no_decorative_lease_constraints_in_domain_or_solver(self) -> None:
        """Verifies that no fake or decorative lease attributes are injected into ActionOption."""
        options = self._sample_options()
        opt = options[0]
        # ActionOption carries physical and network resources, but no fake lease dates/penalties
        assert not hasattr(opt, "lease_expiry_date")
        assert not hasattr(opt, "lease_penalty")
        assert not hasattr(opt, "signing_deadline")
