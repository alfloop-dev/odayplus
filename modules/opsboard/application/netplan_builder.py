"""Scoped NetPlan builder over existing canonical options and SQL owners.

Only scenario assumptions are edited. Model values, source snapshots, policy
pins, approvals and execution records are read from their existing owners.
"""
from __future__ import annotations

import hashlib
import json
import math
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from modules.netplan.domain.planning import NetPlanScenarioStatus, compute_solver_problem_hash
from modules.opsboard.application.network_read_scope import candidate_scope_evidence
from shared.audit.events import AuditEvent
from shared.audit.policy import build_security_event
from shared.auth import (
    AccessRequest,
    Action,
    DataClassification,
    Decision,
    Environment,
    ResourceDescriptor,
    evaluate_abac,
)
from shared.auth.rbac import rbac_allows
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from solver.netplan.model import NetPlanConstraints, NetworkAction

LIFECYCLE_STATUSES = {"decide": NetPlanScenarioStatus.PENDING_APPROVAL,
                      "execute": NetPlanScenarioStatus.APPROVED,
                      "outcome": NetPlanScenarioStatus.EXECUTED,
                      "close": NetPlanScenarioStatus.OUTCOME_OBSERVED}


def validate_native_decision(value: Any) -> dict[str, str]:
    """Reuse native decisions/receipt authority, never accept a body actor."""
    if (not isinstance(value, dict) or set(value) != {"decision", "approvalReceiptId"}
            or type(value.get("decision")) is not str
            or value["decision"] not in {"approved", "rejected"}
            or type(value.get("approvalReceiptId")) is not str
            or (value["decision"] == "approved" and not value["approvalReceiptId"].strip())):
        raise HTTPException(422, "NETPLAN_NATIVE_DECISION_INVALID")
    return {"decision": value["decision"], "approvalReceiptId": value["approvalReceiptId"].strip()}


def validate_recorded_outcome(value: Any) -> dict[str, Any]:
    """Original B10 manual capture; no estimated value or external delivery claim."""
    if not isinstance(value, dict) or set(value) != {"actualGrossMargin", "sourceSnapshotIds"}:
        raise HTTPException(422, "NETPLAN_RECORDED_OUTCOME_INVALID")
    amount = value["actualGrossMargin"]
    try:
        finite = type(amount) in (int, float) and math.isfinite(amount)
    except OverflowError:
        finite = False
    refs = value["sourceSnapshotIds"]
    if not finite or not isinstance(refs, list) or any(type(item) is not str or not item.strip() for item in refs):
        raise HTTPException(422, "NETPLAN_RECORDED_OUTCOME_INVALID")
    return {"actualGrossMargin": amount, "sourceSnapshotIds": list(refs)}


EDITABLE_CONSTRAINTS = frozenset({
    "max_budget", "min_expected_gross_margin", "min_capacity_delta",
    "max_average_risk", "min_action_counts", "max_action_counts",
})


def validate_builder_constraints(value: Any) -> dict[str, Any]:
    """Reject invalid raw JSON before shared error rendering can echo NaN."""
    if not isinstance(value, dict) or set(value) - EDITABLE_CONSTRAINTS:
        raise HTTPException(422, "NETPLAN_BUILDER_CONSTRAINTS_INVALID")
    result = dict(value)
    for key, number in result.items():
        if key.endswith("action_counts"):
            if not isinstance(number, dict) or any(
                action not in {a.value for a in NetworkAction}
                or type(count) is not int or count < 0
                for action, count in number.items()
            ):
                raise HTTPException(422, "NETPLAN_BUILDER_CONSTRAINTS_INVALID")
        elif number is not None:
            try:
                finite = type(number) in (int, float) and math.isfinite(number)
            except OverflowError:
                finite = False
            if not finite:
                raise HTTPException(422, "NETPLAN_BUILDER_CONSTRAINTS_INVALID")
            if key == "min_capacity_delta" and type(number) is not int:
                raise HTTPException(422, "NETPLAN_BUILDER_CONSTRAINTS_INVALID")
            if key == "max_budget" and number < 0:
                raise HTTPException(422, "NETPLAN_BUILDER_CONSTRAINTS_INVALID")
            if key == "max_average_risk" and not 0 <= number <= 1:
                raise HTTPException(422, "NETPLAN_BUILDER_CONSTRAINTS_INVALID")
        elif key == "max_budget":
            raise HTTPException(422, "NETPLAN_BUILDER_CONSTRAINTS_INVALID")
    return result


class CanonicalNetPlanBuilder:
    """One verified request, tenant, canonical scenario owner and transaction."""

    def __init__(self, *, principal, scenario_repository, state_repository,
                 rebalance_factory, store_repository, brand_repository,
                 listing_snapshot_reader, audit_log, correlation_id, role_id, job_queue, policy_repository=None, feature_flags=None):
        self.principal = principal
        self.repository = scenario_repository
        self.state_repository = state_repository
        self.factory = rebalance_factory
        self.stores = store_repository
        self.brands = brand_repository
        self.listings = listing_snapshot_reader
        self.audit = audit_log
        self.correlation_id = correlation_id
        self.role_id = role_id
        self.queue = job_queue
        self.feature_flags = feature_flags
        self.policies = policy_repository
        self.documents = getattr(scenario_repository, "_store", None)
        self.engine = getattr(self.documents, "engine", None)
        if not principal.authenticated or not principal.tenant_id:
            raise HTTPException(401, "AUTHENTICATED_ACTOR_REQUIRED")
        if (not isinstance(self.documents, TenantScopedDocumentStore)
                or self.documents.tenant_id != principal.tenant_id
                or self.engine is None
                or getattr(state_repository, "engine", None) is not self.engine
                or getattr(state_repository, "domain", None) != "network-rebalance"
                or getattr(audit_log, "_engine", None) is not self.engine
                or store_repository is None or brand_repository is None):
            raise HTTPException(503, "NETPLAN_BUILDER_OWNER_UNAVAILABLE")

    @staticmethod
    def problem_hash(scenario) -> str:
        return compute_solver_problem_hash(
            scenario.options_by_entity, scenario.constraints,
            100_000.0, 3, scenario.model_version,
        )

    def _state(self):
        return self.state_repository.load(self.principal.tenant_id)

    def _scenario(self, store_id, scenario_id=None, *, enforce_active=True):
        state = self._state() or {}
        row = next((s for s in state.get("stores", []) if s.get("id") == store_id), {})
        active_id = row.get("netPlanBuilderScenarioId")
        if enforce_active and active_id and scenario_id and scenario_id != active_id:
            raise HTTPException(409, "NETPLAN_BUILDER_SCENARIO_SUPERSEDED")
        wanted = scenario_id or active_id
        if wanted:
            scenario = self.repository.get_scenario(wanted)
        else:
            choices = [s for s in self.repository.list_scenarios()
                       if s.tenant_id == self.principal.tenant_id
                       and store_id in s.options_by_entity]
            scenario = max(choices, key=lambda s: s.created_at) if choices else None
        if (scenario is None or scenario.tenant_id != self.principal.tenant_id
                or store_id not in scenario.options_by_entity):
            raise HTTPException(404, "NETPLAN_BUILDER_SCENARIO_NOT_FOUND")
        return scenario

    def _authorize(self, scenario, action):
        """Check every real entity, never just the store chosen in the panel."""
        candidates = None
        for entity_id in scenario.options_by_entity:
            store = self.stores.get_store(entity_id)
            if store is not None:
                brand = self.brands.get_brand(store.brand_id)
                valid = (store.is_current and store.tenant_id == self.principal.tenant_id
                         and brand is not None and brand.tenant_id == self.principal.tenant_id
                         and self.principal.scope.permits_assigned_area(None)
                         and self.principal.scope.permits_heat_zone(None))
                resource = ResourceDescriptor(
                    type="netplan", resource_id=entity_id,
                    tenant_id=store.tenant_id, brand_id=store.brand_id,
                    region_id=store.region_code or None, store_id=store.store_id,
                    module="operator_console",
                    data_classification=DataClassification.CONFIDENTIAL,
                )
            else:
                if candidates is None:
                    candidates = candidate_scope_evidence(self.principal, self.listings())
                valid = entity_id in candidates
                resource = ResourceDescriptor(
                    type="netplan", resource_id=entity_id,
                    tenant_id=self.principal.tenant_id, module="operator_console",
                    data_classification=DataClassification.CONFIDENTIAL,
                )
                # The canonical parent reader has checked all candidate axes.
                # Do not run absent store/region fields through a second guess.
            access = AccessRequest(
                principal=self.principal, action=action, resource=resource,
                environment=Environment(attributes={"correlation_id": self.correlation_id}),
            )
            allowed_role = (rbac_allows(self.principal, "netplan", action)
                            or (action == Action.VIEW and rbac_allows(self.principal, "operator_network", Action.VIEW)))
            decision = (evaluate_abac(access) if store is not None else
                        Decision.allow("canonical candidate scope verified"))
            if not valid or not allowed_role or not self.principal.scope.permits_module("operator_console") or not self.principal.scope.permits_classification(DataClassification.CONFIDENTIAL):
                decision = Decision.deny("canonical scenario entity outside scope", policy_id="netplan.builder.scope")
            self.audit.record(build_security_event(access, decision))
            if not decision.allowed:
                raise HTTPException(403, "NETPLAN_BUILDER_SCOPE_DENIED")

    @contextmanager
    def _transaction(self, scenario_id):
        with self.engine.transaction():
            if getattr(self.engine, "dialect", None) == "postgresql":
                self.engine.query_one(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0)) AS locked",
                    (f"operator-domain:{self.state_repository.domain}:{self.principal.tenant_id}",),
                )
            elif not self.engine._conn.in_transaction:
                self.engine.execute("BEGIN IMMEDIATE")
            sink = getattr(self.audit, "_worm_sink", None)
            if sink is None:
                yield
            else:
                from shared.audit.durable_delivery import DurableAuditDelivery
                try:
                    delivery = DurableAuditDelivery(self.engine, self.principal.tenant_id, self.queue, sink)
                except ValueError as exc:
                    raise HTTPException(503, "NETPLAN_BUILDER_AUDIT_DELIVERY_UNAVAILABLE") from exc
                with self.audit.transactional_worm_sink(delivery):
                    yield

    def _outcome_registry(self):
        from shared.infrastructure.persistence.netplan_outcomes import DurableNetPlanOutcomeRegistry
        return DurableNetPlanOutcomeRegistry(repository=self.repository, principal=self.principal,
            authorize=lambda current: self._authorize(current, Action.VIEW),
            audit_log=self.audit, queue=self.queue, correlation_id=self.correlation_id)

    def _projection(self, scenario):
        # Verified canonical historical approvals may be reconciled without
        # rewriting approval/execution history. Registry reauthorizes the exact
        # locked row and rejects a projection whose earlier row changed.
        outcome_tracking = self._outcome_registry().reconcile(
            scenario.scenario_id, expected_scenario=scenario)
        solve = self.repository.get_solve(scenario.scenario_id)
        policy = None
        if self.policies is not None:
            from shared.governance.decision_policy import PolicyResolutionError, resolve_policy
            from shared.governance.netplan_disclosure import NETPLAN_DISCLOSURE_POLICY_KIND
            try:
                policy = resolve_policy(self.policies, policy_kind=NETPLAN_DISCLOSURE_POLICY_KIND,
                                        tenant_id=self.principal.tenant_id, at=datetime.now(UTC))
            except PolicyResolutionError:
                pass  # Planning remains available; original approval gate still refuses.
        execution = self.repository.get_execution(scenario.scenario_id)
        outcome = self.repository.get_outcome(scenario.scenario_id)
        value = {
            "outcomeTracking": outcome_tracking,
            "lifecycle": {"canExecute": rbac_allows(self.principal, "netplan", Action.EXECUTE),
                          "canApprove": rbac_allows(self.principal, "netplan", Action.APPROVE),
                          "execution": execution.to_dict() if execution is not None else None,
                          "outcome": outcome.to_dict() if outcome is not None else None},
            "scenarioId": scenario.scenario_id, "scenarioName": scenario.scenario_name,
            "planningHorizon": scenario.planning_horizon, "status": scenario.status.value,
            "problemHash": self.problem_hash(scenario),
            "constraints": scenario.constraints.to_dict(),
            "optionsByEntity": {key: [o.to_dict() for o in options]
                                for key, options in scenario.options_by_entity.items()},
            "statusHistory": [h.to_dict() for h in scenario.status_history],
            "modelVersion": scenario.model_version, "featureVersion": scenario.feature_version,
            "solverVersion": scenario.solver_version,
            "policyVersion": scenario.constraints.policy_version,
            "policyAvailable": policy is not None,
            "disclosurePolicyVersionId": policy.policy_version_id if policy is not None else None,
            "editable": rbac_allows(self.principal, "netplan", Action.CREATE) and scenario.status in {NetPlanScenarioStatus.DRAFT, NetPlanScenarioStatus.SOLVED, NetPlanScenarioStatus.INFEASIBLE},
            "canSolve": rbac_allows(self.principal, "netplan", Action.EXECUTE),
            "requiresNewDraft": scenario.status == NetPlanScenarioStatus.INFEASIBLE,
            "solve": solve.to_dict() if solve is not None and scenario.status != NetPlanScenarioStatus.DRAFT and not solve.is_stale(scenario) else None,
            "isStale": solve is not None and (scenario.status == NetPlanScenarioStatus.DRAFT or solve.is_stale(scenario)),
        }
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError, OverflowError) as exc:
            raise HTTPException(503, "NETPLAN_BUILDER_SOURCE_INVALID") from exc
        return value

    def read(self, store_id):
        scenario = self._scenario(store_id)
        self._authorize(scenario, Action.VIEW)
        return self._projection(scenario)

    def command(self, store_id, *, operation, expected_problem_hash,
                scenario_id, reason, idempotency_key, constraints=None, outcome=None, decision=None):
        scenario = self._scenario(store_id, scenario_id, enforce_active=False)
        action = (Action.APPROVE if operation == "decide" else
                  Action.CREATE if operation == "save" else Action.EXECUTE)
        self._authorize(scenario, action)
        current = self._scenario(store_id)
        if current.scenario_id != scenario.scenario_id:
            self._authorize(current, action)
        if not reason.strip() or not idempotency_key.strip():
            raise HTTPException(422, "NETPLAN_BUILDER_REASON_AND_IDEMPOTENCY_REQUIRED")
        patch = (validate_builder_constraints(constraints) if operation == "save" else
                 validate_recorded_outcome(outcome) if operation == "outcome" else
                 validate_native_decision(decision) if operation == "decide" else None)
        payload = {"storeId": store_id, "operation": operation, "scenarioId": scenario_id,
                   "expectedProblemHash": expected_problem_hash, "reason": reason.strip(),
                   "constraints": patch}
        if operation == "outcome":
            payload["outcome"] = patch
        elif operation == "decide":
            payload["decision"] = patch
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
        receipt_key = hashlib.sha256(json.dumps([self.principal.subject_id, operation, idempotency_key]).encode()).hexdigest()
        scope_denial = None
        with self._transaction(scenario.scenario_id):
            state = self._state()
            if state is None:
                raise HTTPException(503, "NETPLAN_BUILDER_STATE_UNAVAILABLE")
            # The preflight is not authority for a row reloaded after waiting
            # for the tenant lock. Use and authorize these exact versioned rows
            # before either replay projection or the first business mutation.
            active = self._scenario(store_id)
            if getattr(self.engine, "dialect", None) == "postgresql":
                collection = self.documents._collection(self.repository._SCENARIOS)
                for locked_id in sorted({scenario_id, active.scenario_id}):
                    self.engine.query_one(
                        "SELECT doc_id FROM durable_documents "
                        "WHERE collection = ? AND doc_id = ? FOR UPDATE",
                        (collection, locked_id),
                    )
            locked_scenario, version = self._locked_scenario(store_id, scenario_id)
            locked_current = (locked_scenario if active.scenario_id == scenario_id else
                              self._locked_scenario(store_id, active.scenario_id)[0])
            try:
                self._authorize(locked_scenario, action)
                if locked_current.scenario_id != locked_scenario.scenario_id:
                    self._authorize(locked_current, action)
            except HTTPException as exc:
                if exc.status_code != 403:
                    raise
                # This block precedes every business write. Commit only the
                # actual security decision, then return denial outside the SQL
                # context; do not roll its Auth005 event back with an exception.
                scope_denial = exc
            if scope_denial is None:
                return self._apply_authorized_locked(
                    state, locked_scenario, version, locked_current,
                    store_id=store_id, operation=operation,
                    scenario_id=scenario_id, expected_problem_hash=expected_problem_hash,
                    reason=reason, patch=patch, request_hash=request_hash,
                    receipt_key=receipt_key,
                )
        assert scope_denial is not None
        raise scope_denial

    def _locked_scenario(self, store_id, scenario_id):
        row = self.documents.get_versioned(self.repository._SCENARIOS, scenario_id)
        if row is None:
            raise HTTPException(404, "NETPLAN_BUILDER_SCENARIO_NOT_FOUND")
        scenario, version = row
        if (scenario.scenario_id != scenario_id or scenario.tenant_id != self.principal.tenant_id
                or store_id not in scenario.options_by_entity):
            raise HTTPException(404, "NETPLAN_BUILDER_SCENARIO_NOT_FOUND")
        return scenario, version

    def _apply_authorized_locked(self, state, scenario, version, current, *, store_id,
                                 operation, scenario_id, expected_problem_hash, reason,
                                 patch, request_hash, receipt_key):
        # Only command() enters here, after current authorization under lock.
        receipts = state.setdefault("netPlanBuilderReceipts", {})
        receipt = receipts.get(receipt_key)
        if receipt is not None:
            if receipt["requestHash"] != request_hash:
                raise HTTPException(409, "NETPLAN_BUILDER_IDEMPOTENCY_CONFLICT")
            return {"builder": self._projection(current), "audit": receipt["audit"], "idempotentReplay": True}
        active_store = next((row for row in state.get("stores", []) if row.get("id") == store_id), {})
        active_id = active_store.get("netPlanBuilderScenarioId")
        if active_id and active_id != scenario_id:
            raise HTTPException(409, "NETPLAN_BUILDER_SCENARIO_SUPERSEDED")
        if self.problem_hash(scenario) != expected_problem_hash:
            raise HTTPException(409, "NETPLAN_BUILDER_VERSION_CONFLICT")
        if operation in LIFECYCLE_STATUSES:
            if scenario.status != LIFECYCLE_STATUSES[operation]:
                raise HTTPException(409, "NETPLAN_LIFECYCLE_STATUS_CONFLICT")
        elif scenario.status not in {NetPlanScenarioStatus.DRAFT, NetPlanScenarioStatus.SOLVED, NetPlanScenarioStatus.INFEASIBLE}:
            raise HTTPException(409, "NETPLAN_BUILDER_STATUS_CONFLICT")
        service = self.factory(state, self.principal.tenant_id)
        with service.transaction_scope(self.engine, self.principal.tenant_id):
            store = service._store(store_id)
        if operation not in LIFECYCLE_STATUSES and store.get("status") not in {"avmready", "netplanreview"}:
            raise HTTPException(409, "NETPLAN_BUILDER_AVM_WORKFLOW_REQUIRED")
        if operation == "save":
            merged = {**scenario.constraints.to_dict(), **patch}
            for action, count in merged["min_action_counts"].items():
                maximum = merged["max_action_counts"].get(action)
                if maximum is not None and count > maximum:
                    raise HTTPException(422, "NETPLAN_BUILDER_CONSTRAINTS_INVALID")
            if scenario.status in {NetPlanScenarioStatus.DRAFT, NetPlanScenarioStatus.SOLVED}:
                draft = (scenario.transition(NetPlanScenarioStatus.DRAFT,
                                             actor=self.principal.subject_id,
                                             reason=reason.strip(), correlation_id=self.correlation_id)
                         if scenario.status == NetPlanScenarioStatus.SOLVED else scenario)
                changed = replace(draft, constraints=NetPlanConstraints.from_mapping(merged), selected_candidate_id=None)
            else:
                # The spec's infeasible UI path creates a new draft. No
                # old terminal record or approval is rewritten by the editor.
                changed = replace(scenario, scenario_id=f"netplan-scenario-{uuid4()}",
                                  constraints=NetPlanConstraints.from_mapping(merged),
                                  status=NetPlanScenarioStatus.DRAFT, selected_candidate_id=None,
                                  created_at=datetime.now(UTC), correlation_id=self.correlation_id,
                                  status_history=())
                version = None
            if self.documents.compare_and_put(self.repository._SCENARIOS, changed.scenario_id,
                                              changed, expected_version=version) is None:
                raise HTTPException(409, "NETPLAN_BUILDER_VERSION_CONFLICT")
            for target in service._state["stores"]:
                if target.get("storeId", target.get("id")) in scenario.options_by_entity:
                    target.update(netPlanBuilderScenarioId=changed.scenario_id, status="avmready",
                                  netPlanScenarios=[], selectedScenarioId=None, selectedScenarioOwner=None,
                                  selectedScenarioEvidenceId=None, netPlanOptionId=None, netPlanJob=None)
            invalidated = service._state.setdefault("netPlanBuilderInvalidatedCommands", [])
            for key, cached in service._idempotency_cache.items():
                target = cached.get("store") or {}
                if (key[0] in {"solve_netplan", "select_scenario", "submit_review"}
                        and target.get("storeId", target.get("id")) in scenario.options_by_entity
                        and list(key) not in invalidated):
                    invalidated.append(list(key))
            result = self._projection(changed)
        elif operation == "solve":
            if scenario.status != NetPlanScenarioStatus.DRAFT:
                raise HTTPException(409, "NETPLAN_BUILDER_STATUS_CONFLICT")
            store.update(netPlanBuilderScenarioId=scenario.scenario_id, status="avmready")
            service.solve_netplan(store_id=store_id, actor_role_id=self.role_id,
                                  actor_name=self.principal.subject_id, idempotency_key=None,
                                  correlation_id=self.correlation_id, canonical_scenario_id=scenario.scenario_id)
            result = self._projection(self.repository.get_scenario(scenario.scenario_id))
        elif operation in LIFECYCLE_STATUSES:
            native = service._canonical_netplan_service()
            # Same request-bound owner and queue as the rest of this SQL unit;
            # never fall back to a process-global operation flag registry.
            native.feature_flags = self.feature_flags
            native.job_queue = self.queue
            native.outcome_tracker = self._outcome_registry()
            if operation == "decide":
                native.decide(scenario_id, actor_id=self.principal.subject_id,
                              reason=reason.strip(), decision=patch["decision"],
                              approval_receipt_id=patch["approvalReceiptId"])
            elif operation == "execute":
                native.execute(scenario_id, executed_by=self.principal.subject_id)
            elif operation == "outcome":
                native.record_outcome(scenario_id, actual_gross_margin=patch["actualGrossMargin"],
                                      source_snapshot_ids=patch["sourceSnapshotIds"],
                                      actor=self.principal.subject_id)
            else:
                native.close(scenario_id, actor=self.principal.subject_id, reason=reason.strip())
            result = self._projection(self.repository.get_scenario(scenario_id))
        else:
            raise HTTPException(422, "NETPLAN_BUILDER_OPERATION_INVALID")
        event = self.audit.record(AuditEvent(
            event_type=f"operator.netplan.builder.{operation}", actor=self.principal.subject_id,
            action=operation, resource=f"netplan/scenarios/{result['scenarioId']}",
            outcome="accepted", correlation_id=self.correlation_id,
            metadata={"tenant_id": self.principal.tenant_id, "store_id": store_id,
                      "previousScenarioId": scenario_id, "previousProblemHash": expected_problem_hash,
                      "problemHash": result["problemHash"], "reason": reason.strip()},
        ))
        audit = {"event_id": event.event_id}
        if getattr(self.audit, "_worm_sink", None) is not None:
            audit["delivery_status"] = "pending"
        updated_state = service.export_state()
        updated_state.setdefault("netPlanBuilderReceipts", {})[receipt_key] = {
            "requestHash": request_hash, "audit": audit,
        }
        self.state_repository.save(self.principal.tenant_id, updated_state)
        return {"builder": result, "audit": audit, "idempotentReplay": False}
