"""Signed HTTP/CAS on real owned SQLite; synthetic executed owner setup only."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from modules.netplan.domain.planning import ExecutionRecord, NetPlanScenarioStatus
from tests.integration.test_operator_netplan_scenario_builder import (
    BASE,
    STORE,
    TENANT,
    _business,
    _case,
    _command,
    _get,
)
from tests.security.test_feature_flag_admin_authorization import _headers


def executed_fixture(case):
    current = _get(case).json()
    response = _command(case, current, operation="solve", key="prepare-original-solve")
    assert response.status_code == 200, response.text
    scenario = case.repository.get_scenario(case.scenario_id)
    solve = case.repository.get_solve(case.scenario_id)
    # Deliberate synthetic owner state for the manual outcome/close contract;
    # no claim of authentic approval, physical execution or live production data.
    case.repository.save_scenario(replace(scenario, status=NetPlanScenarioStatus.EXECUTED,
        selected_candidate_id=case.scenario_id))
    case.repository.save_execution(ExecutionRecord("synthetic-executed", case.scenario_id,
        tuple(solve.result.selected_actions), "synthetic-recorder", datetime(2026, 10, 15, tzinfo=UTC)))
    return _get(case).json()


def post(case, current, operation, *, key="same-lifecycle-intent", actor="planner", body_extra=None, **extra):
    return case.client.post(BASE + "/" + operation, headers={**_headers(actor), "Idempotency-Key": key},
        json={"scenarioId": current["scenarioId"], "expectedProblemHash": current["problemHash"],
              "reason": "Recorded actual owned ledger source", **extra, **(body_extra or {})})


def test_manual_zero_outcome_close_and_original_receipt_replay_use_trusted_actor(tmp_path):
    with _case(tmp_path / "manual.sqlite3") as case:
        current = executed_fixture(case)
        response = post(case, current, "outcome", outcome={"actualGrossMargin": 0, "sourceSnapshotIds": ["synthetic-ledger"]})
        assert response.status_code == 200, response.text
        value = response.json()
        record = case.repository.get_outcome(case.scenario_id)
        assert record.actual_gross_margin == 0 and record.source_snapshot_ids == ("synthetic-ledger",)
        assert value["builder"]["lifecycle"]["outcome"] == record.to_dict()
        assert value["builder"]["status"] == "outcome_observed"
        audit = case.bundle.audit_log.get_event(value["audit"]["event_id"], tenant_id=TENANT)
        assert audit.actor == "planner" and audit.metadata["reason"] == "Recorded actual owned ledger source"
        after = _business(case)
        replay = post(case, current, "outcome", outcome={"actualGrossMargin": 0, "sourceSnapshotIds": ["synthetic-ledger"]})
        assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
        assert replay.json()["audit"] == value["audit"] and _business(case) == after
        closed = post(case, value["builder"], "close", key="close-real-record")
        assert closed.status_code == 200, closed.text
        assert closed.json()["builder"]["status"] == "closed"
        assert case.repository.get_outcome(case.scenario_id) == record
        assert case.repository.get_scenario(case.scenario_id).status_history[-1].actor == "planner"


@pytest.mark.parametrize("extra", [{"actor": "forged"}, {"executed_by": "forged"}, {"observed_at": "2000-01-01"}])
def test_lifecycle_never_accepts_a_body_actor_or_observation_clock(tmp_path, extra):
    with _case(tmp_path / "actor.sqlite3") as case:
        current = executed_fixture(case)
        before = _business(case)
        response = post(case, current, "outcome", outcome={"actualGrossMargin": 1, "sourceSnapshotIds": []}, body_extra=extra)
        assert response.status_code == 422 and _business(case) == before


@pytest.mark.parametrize("operation", ["execute", "outcome", "close"])
def test_invalid_native_stage_viewer_and_foreign_scope_cannot_write(tmp_path, operation):
    with _case(tmp_path / "stages.sqlite3") as case:
        current = _get(case).json()
        before = _business(case)
        extra = {"outcome": {"actualGrossMargin": 1, "sourceSnapshotIds": []}} if operation == "outcome" else {}
        refused = post(case, current, operation, **extra)
        assert refused.status_code == 409 and _business(case) == before
        denied = post(case, current, operation, actor="viewer", **extra)
        assert denied.status_code == 403 and _business(case) == before
        foreign = post(case, current, operation, actor="foreign", **extra)
        assert foreign.status_code in {403, 404} and _business(case) == before


def test_stale_hash_idempotency_conflict_and_scope_revocation_precede_outcome_replay(tmp_path):
    with _case(tmp_path / "version.sqlite3") as case:
        current = executed_fixture(case)
        before = _business(case)
        stale = {**current, "problemHash": "0" * 64}
        assert post(case, stale, "outcome", outcome={"actualGrossMargin": 1, "sourceSnapshotIds": []}).status_code == 409
        assert _business(case) == before
        good = post(case, current, "outcome", outcome={"actualGrossMargin": 1, "sourceSnapshotIds": []})
        assert good.status_code == 200, good.text
        before = _business(case)
        assert post(case, current, "outcome", outcome={"actualGrossMargin": 2, "sourceSnapshotIds": []}).status_code == 409
        assert _business(case) == before
        old = case.bundle.store_repository.get_store(STORE)
        case.bundle.store_repository.save_store(replace(old, region_code="south"))
        case.mappings["planner"]["scope"]["region_ids"] = ["north"]
        before = _business(case)
        assert post(case, current, "outcome", outcome={"actualGrossMargin": 1, "sourceSnapshotIds": []}).status_code == 403
        assert _business(case) == before


def test_execute_rechecks_original_authentic_approval_and_records_exact_native_actions(tmp_path):
    from modules.netplan.application.planning import NetPlanService
    from shared.governance.decision_policy import InMemoryDecisionPolicyRepository
    from shared.governance.netplan_disclosure import default_netplan_disclosure_policy
    from tests.integration.test_netplan_solver import (
        _approval_receipt,
        _approval_verifier,
        _management_baseline,
    )

    with _case(tmp_path / "actual-native-execute.sqlite3") as case:
        original = case.repository.get_scenario(case.scenario_id)
        constraints = replace(original.constraints, max_equipment_units=1000, max_labour_headcount=1000,
                              min_coverage_delta=0, max_open_per_dilution_zone=2)
        options = {identity: tuple(replace(option, equipment_units=0, labour_headcount=0,
                                         coverage_delta=0) for option in values)
                   for identity, values in original.options_by_entity.items()}
        case.repository.save_scenario(replace(original, constraints=constraints, options_by_entity=options))
        policy = InMemoryDecisionPolicyRepository([replace(default_netplan_disclosure_policy(TENANT),
            effective_from=datetime(2020, 1, 1, tzinfo=UTC))])
        native = NetPlanService(repository=case.repository, policy_repository=policy, runtime_mode="poc",
            feature_flags=case.bundle.feature_flag_registry, job_queue=case.bundle.job_queue)
        solve = native.solve(case.scenario_id)
        now = datetime.now(UTC)
        baseline = _management_baseline(scenario_id=case.scenario_id, baseline_id=case.scenario_id,
            baseline_name=original.scenario_name, scope="tenant:" + TENANT, release_id=original.planning_horizon,
            actions_by_entity={row.entity_id: row.action for row in solve.result.selected_actions},
            source_snapshot_ids=tuple(sorted({ref for row in solve.result.selected_actions for ref in row.source_snapshot_ids})))
        receipt = _approval_receipt(baseline, options, constraints,
            principal_id="synthetic-management-reviewer", issued_at="2026-10-01T00:00:00Z", expires_at="2099-01-01T00:00:00Z")
        verifier = _approval_verifier(receipt, principal_id="synthetic-management-reviewer", clock=now)
        native.approval_verifier = verifier
        native.acknowledge_unmodelled_constraints(case.scenario_id, actor_id="synthetic-management-reviewer",
            reason="Explicit synthetic lease and sequencing review", acknowledged_classes=("LEASE", "SEQUENCING"),
            approval_receipt_id=receipt.receipt_id)
        native.submit_for_approval(case.scenario_id, actor="planner", reason="Explicit synthetic solve submission")
        native.decide(case.scenario_id, actor_id="synthetic-management-reviewer", reason="Original authentic receipt verified",
            approval_receipt_id=receipt.receipt_id)
        case.rebalance_factory.approval_verifier = verifier
        case.rebalance_factory.policy_repository = policy
        current = _get(case).json()
        response = post(case, current, "execute")
        assert response.status_code == 200, response.text
        execution = case.repository.get_execution(case.scenario_id)
        assert execution.executed_by == "planner" and execution.actions == solve.result.selected_actions
        assert response.json()["builder"]["lifecycle"]["execution"] == execution.to_dict()
        assert response.json()["builder"]["lifecycle"]["outcome"] is None
        assert response.json()["builder"]["status"] == "executed"
        before = _business(case)
        replay = post(case, current, "execute")
        assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
        assert _business(case) == before
