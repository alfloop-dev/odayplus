"""NetPlan application service."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from models.shared_ml.production_runtime import (
    ProductionExecutionConfigurationError,
    production_execution_required,
)
from modules.netplan.application.production import (
    NETPLAN_PRODUCTION_SOLVER_VERSION,
    NetPlanProductionExecutor,
)
from modules.netplan.domain.planning import (
    VALID_TRANSITIONS,
    ApprovalRecord,
    CandidateSiteInput,
    ConstraintDisclosureAcknowledgement,
    ExecutionRecord,
    ExistingStoreInput,
    InvalidNetPlanTransitionError,
    NetPlanScenario,
    NetPlanScenarioStatus,
    OutcomeRecord,
    ScenarioSolveRecord,
    build_outcome_record,
    build_scenario_options,
)
from modules.netplan.infrastructure.repositories import InMemoryNetPlanRepository
from shared.governance.decision_policy import (
    DecisionPolicy,
    DecisionPolicyRepository,
    resolve_policy,
)
from shared.governance.netplan_disclosure import (
    NETPLAN_DISCLOSURE_POLICY_KIND,
    evaluate_disclosure,
    role_is_authorized,
)
from solver.netplan import (
    STATUS_INFEASIBLE,
    ActionOption,
    ConstraintClass,
    ManagementApprovalExpectation,
    ManagementApprovalReceiptVerifier,
    ManagementApprovalVerification,
    ManagementBaselineInput,
    NetPlanConstraints,
    NetworkAction,
    compute_solver_problem_hash,
    solve_network_plan,
    validate_network_plan_solve_result,
)


class NetPlanNotFoundError(LookupError):
    """Raised when a scenario or solve record is missing."""


class NetPlanApprovalError(ValueError):
    """Raised when a high-risk approval request is incomplete."""


class NetPlanConstraintDisclosureError(NetPlanApprovalError):
    """Raised when a plan is approved without answering for what was not modelled.

    A subclass of `NetPlanApprovalError` so that existing callers which already
    treat an approval refusal as a refusal keep working, and a distinct type so
    that "this plan was never checked against construction capacity" can be
    told apart from "this receipt does not verify" by anything that cares --
    they are both refusals, but only one of them is fixed by supplying a cap.
    """


@dataclass(frozen=True)
class ScenarioBuildRequest:
    tenant_id: str
    scenario_name: str
    planning_horizon: str
    constraints: NetPlanConstraints
    existing_stores: Sequence[ExistingStoreInput | Mapping[str, Any]] = ()
    candidate_sites: Sequence[CandidateSiteInput | Mapping[str, Any]] = ()
    scenario_id: str | None = None
    correlation_id: str = "netplan-correlation"


@dataclass(frozen=True)
class PreparedConstraintDisclosureAcknowledgement:
    """A validated, sealed acknowledgement that has not been stored yet.

    A disclosure receipt is immutable once written -- the in-memory repository
    and the ``trg_netplan_disclosure_ack_immutable`` trigger both refuse to
    rewrite one -- so the write is the point of no return. A caller that has to
    perform other writes around it (the Operator submit path advances the
    NetPlan lifecycle and creates a Govern approval) therefore needs the
    validation and the write separated: everything that can refuse should refuse
    before anything durable exists, so a refusal leaves nothing behind.

    Holding the sealed receipt rather than the arguments to build one matters:
    ``acknowledgement_id`` and ``acknowledged_at`` are fixed here, so the Govern
    approval payload can name the receipt it is about to be authorised by, and
    the id it names is the id that gets stored.
    """

    acknowledgement: ConstraintDisclosureAcknowledgement
    scenario: NetPlanScenario
    selected_candidate_id: str

    @property
    def acknowledgement_id(self) -> str:
        return self.acknowledgement.acknowledgement_id

    @property
    def acknowledged_classes(self) -> tuple[ConstraintClass, ...]:
        return self.acknowledgement.acknowledged_classes


@dataclass(frozen=True)
class _ApprovalSubject:
    """One canonical candidate that can be submitted and approved.

    A solve contains a primary candidate plus alternatives. Their disclosure
    classes are usually identical, but their actions and baseline hashes are
    not. Keeping the selected candidate as one value prevents the approval
    path from mixing the Operator row with the primary solve by accident.
    """

    candidate_id: str
    actions: tuple[ActionOption, ...]
    modelled_constraint_classes: tuple[ConstraintClass, ...]
    unmodelled_constraint_classes: tuple[ConstraintClass, ...]

    @property
    def action_signature(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted((action.entity_id, action.action.value) for action in self.actions)
        )


class NetPlanService:
    def __init__(
        self,
        *,
        repository: InMemoryNetPlanRepository | None = None,
        production_executor: NetPlanProductionExecutor | None = None,
        approval_verifier: ManagementApprovalReceiptVerifier | None = None,
        policy_repository: DecisionPolicyRepository | None = None,
        runtime_mode: str | None = None,
    ) -> None:
        self.production_required = production_execution_required(runtime_mode)
        self.strict_production_composition = runtime_mode is not None and self.production_required
        if self.strict_production_composition and (
            repository is None or isinstance(repository, InMemoryNetPlanRepository)
        ):
            raise ProductionExecutionConfigurationError(
                "NetPlan production requires an injected durable repository"
            )
        if self.strict_production_composition and production_executor is None:
            raise ProductionExecutionConfigurationError(
                "NetPlan production requires an injected OSS solver executor"
            )
        self.repository = repository or InMemoryNetPlanRepository()
        self.production_executor = production_executor
        self.approval_verifier = approval_verifier
        # Not defaulted. An approval path that falls back to built-in rules
        # when the registry is missing cannot say what governed the decision,
        # and -- worse -- would approve every plan while the gate looks
        # installed. `_require_disclosure_policy` turns the absence into a
        # refusal at the point of decision rather than here, so that solving
        # and rejecting still work without a registry.
        self.policy_repository = policy_repository

    def create_scenario(
        self,
        *,
        tenant_id: str,
        scenario_name: str,
        planning_horizon: str,
        constraints: NetPlanConstraints | Mapping[str, Any],
        existing_stores: Sequence[ExistingStoreInput | Mapping[str, Any]] = (),
        candidate_sites: Sequence[CandidateSiteInput | Mapping[str, Any]] = (),
        scenario_id: str | None = None,
        correlation_id: str,
        created_at: datetime | None = None,
    ) -> NetPlanScenario:
        parsed_constraints = (
            constraints
            if isinstance(constraints, NetPlanConstraints)
            else NetPlanConstraints.from_mapping(constraints)
        )
        scenario = NetPlanScenario.create(
            tenant_id=tenant_id,
            scenario_name=scenario_name,
            planning_horizon=planning_horizon,
            options_by_entity=build_scenario_options(
                existing_stores=existing_stores,
                candidate_sites=candidate_sites,
            ),
            constraints=parsed_constraints,
            correlation_id=correlation_id,
            scenario_id=scenario_id,
            created_at=created_at,
        )
        return self.repository.save_scenario(scenario)

    def update_scenario(
        self,
        scenario_id: str,
        *,
        scenario_name: str | None = None,
        planning_horizon: str | None = None,
        constraints: NetPlanConstraints | Mapping[str, Any] | None = None,
        existing_stores: Sequence[ExistingStoreInput | Mapping[str, Any]] | None = None,
        candidate_sites: Sequence[CandidateSiteInput | Mapping[str, Any]] | None = None,
    ) -> NetPlanScenario:
        from dataclasses import replace

        scenario = self._require_scenario(scenario_id)
        if scenario.status not in (
            NetPlanScenarioStatus.DRAFT,
            NetPlanScenarioStatus.SOLVED,
            NetPlanScenarioStatus.INFEASIBLE,
        ):
            raise ValueError(
                f"cannot update scenario {scenario_id} in {scenario.status.value} status"
            )
        if scenario.status in (NetPlanScenarioStatus.SOLVED, NetPlanScenarioStatus.INFEASIBLE):
            scenario = scenario.transition(
                NetPlanScenarioStatus.DRAFT,
                actor="system",
                reason="reset to draft on parameter update",
            )
        updated_constraints = scenario.constraints
        if constraints is not None:
            updated_constraints = (
                constraints
                if isinstance(constraints, NetPlanConstraints)
                else NetPlanConstraints.from_mapping(constraints)
            )
        updated_options = scenario.options_by_entity
        if existing_stores is not None or candidate_sites is not None:
            new_options: dict[str, tuple[ActionOption, ...]] = {}
            if existing_stores is not None:
                new_options.update(
                    build_scenario_options(existing_stores=existing_stores, candidate_sites=())
                )
            else:
                for entity_id, opts in scenario.options_by_entity.items():
                    if opts and opts[0].action != NetworkAction.OPEN:
                        new_options[entity_id] = opts

            if candidate_sites is not None:
                new_options.update(
                    build_scenario_options(existing_stores=(), candidate_sites=candidate_sites)
                )
            else:
                for entity_id, opts in scenario.options_by_entity.items():
                    if opts and opts[0].action == NetworkAction.OPEN:
                        new_options[entity_id] = opts

            updated_options = new_options

        updated = replace(
            scenario,
            scenario_name=scenario_name or scenario.scenario_name,
            planning_horizon=planning_horizon or scenario.planning_horizon,
            constraints=updated_constraints,
            options_by_entity=updated_options,
            selected_candidate_id=None,
        )
        return self.repository.save_scenario(updated)

    def solve(
        self,
        scenario_id: str,
        *,
        actor: str = "system",
        reason: str = "netplan constrained network solve",
        solved_at: datetime | None = None,
        alternative_limit: int = 3,
    ) -> ScenarioSolveRecord:
        scenario = self._require_scenario(scenario_id)
        # A new solve invalidates any candidate selected from the previous
        # result. Approval must explicitly bind to a candidate from this solve.
        scenario = replace(scenario, selected_candidate_id=None)
        now = solved_at or datetime.now(UTC)
        execution_metadata: dict[str, Any] = {}
        if self.production_required:
            executor = self.production_executor or NetPlanProductionExecutor()
            execution = executor.execute(
                scenario,
                alternative_limit=alternative_limit,
            )
            result = execution.result
            execution_metadata = execution.metadata
        else:
            result = solve_network_plan(
                options_by_entity=scenario.options_by_entity,
                constraints=scenario.constraints,
                alternative_limit=alternative_limit,
            )
        target = (
            NetPlanScenarioStatus.INFEASIBLE
            if result.solver_status == STATUS_INFEASIBLE
            else NetPlanScenarioStatus.SOLVED
        )
        transitioned = scenario.transition(
            target,
            actor=actor,
            reason=reason,
            occurred_at=now,
        )
        problem_hash = compute_solver_problem_hash(
            scenario.options_by_entity,
            scenario.constraints,
            100_000.0,
            alternative_limit,
            scenario.model_version,
        )
        solve = self.repository.save_solve(
            ScenarioSolveRecord(
                scenario_id=scenario.scenario_id,
                result=result,
                solved_at=now,
                alternative_limit=alternative_limit,
                execution_metadata=execution_metadata,
                problem_hash=problem_hash,
                model_version=scenario.model_version,
            )
        )
        self.repository.save_scenario(transitioned)
        return solve

    def submit_for_approval(
        self,
        scenario_id: str,
        *,
        actor: str = "system",
        reason: str = "submitted for network planning approval",
        occurred_at: datetime | None = None,
        selected_candidate_id: str | None = None,
    ) -> NetPlanScenario:
        scenario = self._require_scenario(scenario_id)
        solve = self.repository.get_solve(scenario_id)
        if solve is not None and solve.is_stale(scenario):
            raise NetPlanApprovalError(
                "stale solve result cannot be submitted for approval: scenario parameters have changed since last solve"
            )
        selected_id = scenario.selected_candidate_id
        if solve is not None:
            selected_id = self._selected_approval_subject(
                scenario,
                solve,
                selected_candidate_id=selected_candidate_id,
            ).candidate_id
        elif selected_candidate_id is not None:
            raise NetPlanApprovalError(
                "a selected NetPlan candidate cannot be submitted without a solve"
            )
        return self._advance(
            replace(scenario, selected_candidate_id=selected_id),
            NetPlanScenarioStatus.PENDING_APPROVAL,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )

    def prepare_unmodelled_constraint_acknowledgement(
        self,
        scenario_id: str,
        *,
        actor_id: str,
        reason: str,
        acknowledged_classes: Sequence[ConstraintClass | str],
        approval_receipt_id: str,
        acknowledged_at: datetime | None = None,
        selected_candidate_id: str | None = None,
    ) -> PreparedConstraintDisclosureAcknowledgement:
        """Validate and seal an acknowledgement without storing it.

        This is the only way a plan with an unmodelled required class reaches
        approval, and everything it checks is checked because the alternative
        is a signature that means less than it appears to:

        - The classes are named by the caller and never inferred. An
          "acknowledge whatever is outstanding" call would produce a receipt
          whose meaning changes with the scenario, which is a blank cheque with
          a person's name on it.
        - Authority comes from `principal_role` on the verified management
          receipt, not from an argument. An actor who could state their own
          role could authorise themselves.
        - The solve must not be stale. Accepting the exposure of a plan that no
          longer exists is not an acceptance of anything.
        - A class must be both acknowledgeable under the policy *and* actually
          unmodelled in this solve. Signing for exposure the plan does not
          carry means the signer was shown something other than this plan.

        Every one of those refusals happens here, before anything is written.
        `commit_unmodelled_constraint_acknowledgement` performs the one durable
        write, so a caller sequencing this alongside other writes can order the
        irrevocable step wherever it needs to and know that reaching it means the
        signature itself has nothing left to object to.
        """
        cleaned_reason = str(reason or "").strip()
        if not cleaned_reason:
            raise NetPlanConstraintDisclosureError(
                "acknowledging an unmodelled constraint class requires a reason: "
                "the receipt has to record why the exposure was accepted"
            )
        scenario = self._require_scenario(scenario_id)
        solve = self._require_solve(scenario_id)
        if solve.is_stale(scenario):
            raise NetPlanConstraintDisclosureError(
                "stale solve result cannot be acknowledged: scenario parameters "
                "have changed since last solve"
            )
        subject = self._selected_approval_subject(
            scenario,
            solve,
            selected_candidate_id=selected_candidate_id,
        )
        now = acknowledged_at or datetime.now(UTC)
        verification = self._verify_authoritative_solve(
            scenario,
            solve,
            approval_receipt_id=approval_receipt_id,
            selected_candidate_id=subject.candidate_id,
        )
        assert verification.receipt is not None
        if actor_id != verification.receipt.principal_id:
            raise NetPlanConstraintDisclosureError(
                "acknowledging actor does not match the verified approval principal"
            )
        policy = self._require_disclosure_policy(scenario, at=now)
        actor_role = verification.receipt.principal_role
        if not role_is_authorized(policy, actor_role):
            raise NetPlanConstraintDisclosureError(
                f"principal role {actor_role!r} is not authorised to acknowledge "
                f"unmodelled constraint classes under policy {policy.policy_version_id}"
            )

        disclosed = self._require_disclosed_classes(
            scenario,
            solve,
            selected_candidate_id=subject.candidate_id,
        )
        evaluation = evaluate_disclosure(
            policy,
            unmodelled_classes=[item.value for item in disclosed],
        )
        named = self._normalize_classes(acknowledged_classes)
        if not named:
            raise NetPlanConstraintDisclosureError(
                "acknowledging an unmodelled constraint class requires naming at "
                "least one class"
            )
        waivable = set(evaluation.acknowledgeable)
        not_waivable = tuple(
            item.value for item in named if item.value not in waivable
        )
        if not_waivable:
            raise NetPlanConstraintDisclosureError(
                f"cannot acknowledge {','.join(sorted(not_waivable))} under policy "
                f"{policy.policy_version_id}: a class is acknowledgeable only when "
                "the policy permits it and this solve actually left it unmodelled"
            )

        acknowledgement = ConstraintDisclosureAcknowledgement(
            acknowledgement_id=f"netplan-disclosure-ack-{uuid4()}",
            scenario_id=scenario.scenario_id,
            tenant_id=scenario.tenant_id,
            acknowledged_classes=named,
            actor_id=actor_id,
            actor_role=actor_role,
            reason=cleaned_reason,
            policy_version_id=policy.policy_version_id,
            policy_label=policy.policy_label,
            policy_version=policy.policy_version,
            solver_problem_hash=solve.problem_hash,
            model_version=solve.model_version,
            approval_receipt_id=verification.receipt.receipt_id,
            acknowledged_at=now,
            selected_candidate_id=subject.candidate_id,
            selected_action_signature=subject.action_signature,
            selected_baseline_content_hash=verification.receipt.baseline_content_hash,
        ).sealed()
        return PreparedConstraintDisclosureAcknowledgement(
            acknowledgement=acknowledgement,
            scenario=scenario,
            selected_candidate_id=subject.candidate_id,
        )

    def pin_prepared_candidate(
        self,
        prepared: PreparedConstraintDisclosureAcknowledgement,
    ) -> NetPlanScenario:
        """Record the candidate a prepared receipt was taken against.

        The receipt names one candidate. A later lifecycle step that does not
        repeat the candidate id would otherwise fall back to the primary, which
        would approve a plan the signature was not given for. Callers that
        advance the lifecycle themselves pass the candidate through that
        transition instead and do not need this.
        """
        return self.repository.save_scenario(
            replace(
                prepared.scenario,
                selected_candidate_id=prepared.selected_candidate_id,
            )
        )

    def commit_unmodelled_constraint_acknowledgement(
        self,
        prepared: PreparedConstraintDisclosureAcknowledgement,
    ) -> ConstraintDisclosureAcknowledgement:
        """Store a prepared receipt, and nothing else.

        Deliberately narrow. An earlier version of this write also re-saved the
        scenario to pin the selected candidate, which is correct for a direct
        caller and wrong for one that has already advanced the lifecycle: the
        re-save carried the *pre-transition* scenario and would silently roll
        PENDING_APPROVAL back to SOLVED. Callers that sequence their own
        lifecycle writes select the candidate through that transition instead.
        """
        return self.repository.save_disclosure_acknowledgement(prepared.acknowledgement)

    def acknowledge_unmodelled_constraints(
        self,
        scenario_id: str,
        *,
        actor_id: str,
        reason: str,
        acknowledged_classes: Sequence[ConstraintClass | str],
        approval_receipt_id: str,
        acknowledged_at: datetime | None = None,
        selected_candidate_id: str | None = None,
    ) -> ConstraintDisclosureAcknowledgement:
        """Validate, seal and store one acknowledgement in a single call.

        The direct entry point, for callers whose only write is this one. The
        selected candidate is pinned here so that a later lifecycle step which
        does not repeat the candidate id does not silently fall back to the
        primary; a caller doing its own lifecycle writes uses the prepare/commit
        pair instead and pins the candidate through its own transition.
        """
        prepared = self.prepare_unmodelled_constraint_acknowledgement(
            scenario_id,
            actor_id=actor_id,
            reason=reason,
            acknowledged_classes=acknowledged_classes,
            approval_receipt_id=approval_receipt_id,
            acknowledged_at=acknowledged_at,
            selected_candidate_id=selected_candidate_id,
        )
        self.pin_prepared_candidate(prepared)
        return self.commit_unmodelled_constraint_acknowledgement(prepared)

    def decide(
        self,
        scenario_id: str,
        *,
        actor_id: str,
        reason: str,
        decision: str = "approved",
        approval_receipt_id: str = "",
        decided_at: datetime | None = None,
    ) -> ApprovalRecord:
        if not reason:
            raise NetPlanApprovalError("netplan decisions require a reason")
        normalized = decision.lower()
        scenario = self._require_scenario(scenario_id)
        now = decided_at or datetime.now(UTC)
        # Establish that this scenario can be decided at all before asking
        # whether it should be. A scenario that was never submitted is not
        # approvable for a reason no acknowledgement can fix, so reporting the
        # disclosure gap first would send the operator to collect a signature
        # that still would not let the plan through.
        target_status = (
            NetPlanScenarioStatus.APPROVED
            if normalized == "approved"
            else NetPlanScenarioStatus.REJECTED
        )
        if target_status not in VALID_TRANSITIONS.get(scenario.status, frozenset()):
            raise InvalidNetPlanTransitionError(
                f"cannot move scenario {scenario.scenario_id} from "
                f"{scenario.status.value} to {target_status.value}"
            )
        authority_receipt = None
        authority_verification = None
        verification_violations: tuple[str, ...] = ()
        modelled_classes: tuple[ConstraintClass, ...] = ()
        unmodelled_classes: tuple[ConstraintClass, ...] = ()
        acknowledged_classes: tuple[ConstraintClass, ...] = ()
        disclosure_policy_version_id = ""
        disclosure_policy_label = ""
        disclosure_policy_version = ""
        disclosure_acknowledgement_id = ""
        solver_problem_hash = ""
        selected_candidate_id = ""
        selected_action_signature: tuple[tuple[str, str], ...] = ()
        selected_baseline_content_hash = ""
        if normalized == "approved":
            solve = self._require_solve(scenario_id)
            if solve.is_stale(scenario):
                raise NetPlanApprovalError(
                    "stale solve result cannot be approved: scenario parameters have changed since last solve"
                )
            subject = self._selected_approval_subject(scenario, solve)
            verification = self._verify_authoritative_solve(
                scenario,
                solve,
                approval_receipt_id=approval_receipt_id,
                selected_candidate_id=subject.candidate_id,
            )
            assert verification.receipt is not None
            if actor_id != verification.receipt.principal_id:
                raise NetPlanApprovalError(
                    "audit actor does not match the verified approval principal"
                )
            # The disclosure gate runs after authority verification and before
            # the record is written. After, because the acknowledgement is bound
            # to the same verified receipt and there is nothing to check against
            # until that receipt is trusted. Before, because an ApprovalRecord
            # that exists is an approval: a plan must not reach persistence and
            # then be argued about.
            policy, acknowledgement = self._enforce_constraint_disclosure(
                scenario,
                solve,
                selected_candidate_id=subject.candidate_id,
                at=now,
            )
            modelled_classes = subject.modelled_constraint_classes
            unmodelled_classes = subject.unmodelled_constraint_classes
            solver_problem_hash = solve.problem_hash
            selected_candidate_id = subject.candidate_id
            selected_action_signature = subject.action_signature
            assert verification.receipt is not None
            selected_baseline_content_hash = verification.receipt.baseline_content_hash
            disclosure_policy_version_id = policy.policy_version_id
            disclosure_policy_label = policy.policy_label
            disclosure_policy_version = policy.policy_version
            if acknowledgement is not None:
                acknowledged_classes = acknowledgement.acknowledged_classes
                disclosure_acknowledgement_id = acknowledgement.acknowledgement_id
            authority_receipt = verification.receipt
            authority_verification = verification
            verification_violations = verification.violations
        approval = ApprovalRecord(
            approval_id=f"netplan-approval-{uuid4()}",
            scenario_id=scenario.scenario_id,
            actor_id=actor_id,
            decision=normalized,
            reason=reason,
            decided_at=now,
            policy_version=scenario.constraints.policy_version,
            authority_receipt=authority_receipt,
            authority_verification=authority_verification,
            verification_violations=verification_violations,
            modelled_constraint_classes=modelled_classes,
            unmodelled_constraint_classes=unmodelled_classes,
            acknowledged_constraint_classes=acknowledged_classes,
            disclosure_policy_version_id=disclosure_policy_version_id,
            disclosure_policy_label=disclosure_policy_label,
            disclosure_policy_version=disclosure_policy_version,
            disclosure_acknowledgement_id=disclosure_acknowledgement_id,
            solver_problem_hash=solver_problem_hash,
            selected_candidate_id=selected_candidate_id,
            selected_action_signature=selected_action_signature,
            selected_baseline_content_hash=selected_baseline_content_hash,
        )
        transitioned = scenario.transition(
            target_status,
            actor=actor_id,
            reason=reason,
            occurred_at=now,
        )
        self.repository.save_approval(approval)
        self.repository.save_scenario(transitioned)
        return approval

    def execute(
        self,
        scenario_id: str,
        *,
        executed_by: str = "system",
        executed_at: datetime | None = None,
    ) -> ExecutionRecord:
        scenario = self._require_scenario(scenario_id)
        solve = self._require_solve(scenario_id)
        subject = self._selected_approval_subject(scenario, solve)
        approval = self._require_authentic_approval(scenario_id)
        assert approval.authority_receipt is not None
        verification = self._verify_authoritative_solve(
            scenario,
            solve,
            approval_receipt_id=approval.authority_receipt.receipt_id,
            selected_candidate_id=subject.candidate_id,
        )
        assert verification.receipt is not None
        if (
            approval.actor_id != verification.receipt.principal_id
            or approval.authority_receipt.receipt_hash
            != verification.receipt.receipt_hash
            or (
                approval.selected_action_signature
                and approval.selected_action_signature != subject.action_signature
            )
            or (
                approval.selected_candidate_id
                and approval.selected_candidate_id != subject.candidate_id
            )
        ):
            raise NetPlanApprovalError(
                "persisted approval does not match authoritative management readback"
            )
        now = executed_at or datetime.now(UTC)
        transitioned = scenario.transition(
            NetPlanScenarioStatus.EXECUTED,
            actor=executed_by,
            reason="network plan actions executed",
            occurred_at=now,
        )
        execution = self.repository.save_execution(
            ExecutionRecord(
                execution_id=f"netplan-execution-{uuid4()}",
                scenario_id=scenario_id,
                actions=subject.actions,
                executed_by=executed_by,
                executed_at=now,
            )
        )
        self.repository.save_scenario(transitioned)
        return execution

    def record_outcome(
        self,
        scenario_id: str,
        *,
        actual_gross_margin: float,
        observed_at: datetime | None = None,
        source_snapshot_ids: Sequence[str] = (),
        actor: str = "system",
    ) -> OutcomeRecord:
        scenario = self._require_scenario(scenario_id)
        solve = self._require_solve(scenario_id)
        now = observed_at or datetime.now(UTC)
        transitioned = scenario.transition(
            NetPlanScenarioStatus.OUTCOME_OBSERVED,
            actor=actor,
            reason="network plan outcome observed",
            occurred_at=now,
        )
        outcome = self.repository.save_outcome(
            build_outcome_record(
                scenario_id=scenario_id,
                solve_result=solve.result,
                actual_gross_margin=actual_gross_margin,
                observed_at=now,
                source_snapshot_ids=source_snapshot_ids,
            )
        )
        self.repository.save_scenario(transitioned)
        return outcome

    def close(
        self,
        scenario_id: str,
        *,
        actor: str = "system",
        reason: str = "netplan outcome written to label registry",
        occurred_at: datetime | None = None,
    ) -> NetPlanScenario:
        scenario = self._require_scenario(scenario_id)
        return self._advance(
            scenario,
            NetPlanScenarioStatus.CLOSED,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )

    def _advance(
        self,
        scenario: NetPlanScenario,
        to_status: NetPlanScenarioStatus,
        *,
        actor: str,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> NetPlanScenario:
        updated = scenario.transition(
            to_status,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )
        return self.repository.save_scenario(updated)

    def _require_scenario(self, scenario_id: str) -> NetPlanScenario:
        scenario = self.repository.get_scenario(scenario_id)
        if scenario is None:
            raise NetPlanNotFoundError(f"scenario {scenario_id} not found")
        return scenario

    def _require_solve(self, scenario_id: str) -> ScenarioSolveRecord:
        solve = self.repository.get_solve(scenario_id)
        if solve is None:
            raise NetPlanNotFoundError(f"scenario {scenario_id} has no solve record")
        return solve

    @staticmethod
    def _normalize_classes(
        classes: Sequence[ConstraintClass | str],
    ) -> tuple[ConstraintClass, ...]:
        """Parse caller-named classes, refusing names the solver does not define.

        An unrecognised name is rejected rather than dropped. Silently ignoring
        `"SEQUENCNG"` would produce a receipt that reads as covering sequencing
        while covering nothing.
        """
        parsed: list[ConstraintClass] = []
        for item in classes:
            if isinstance(item, ConstraintClass):
                candidate = item
            else:
                raw = str(item or "").strip().upper()
                if not raw:
                    continue
                try:
                    candidate = ConstraintClass(raw)
                except ValueError as exc:
                    raise NetPlanConstraintDisclosureError(
                        f"{raw!r} is not a network plan constraint class"
                    ) from exc
            if candidate not in parsed:
                parsed.append(candidate)
        return tuple(parsed)

    def _require_disclosure_policy(
        self, scenario: NetPlanScenario, *, at: datetime
    ) -> DecisionPolicy:
        """Resolve the disclosure policy in force for this tenant at `at`.

        Point-in-time rather than latest, for the same reason the registry is:
        re-deriving why a six-month-old plan was approved has to resolve to the
        rules that approved it.
        """
        if self.policy_repository is None:
            raise NetPlanConstraintDisclosureError(
                "netplan constraint disclosure policy repository is not configured; "
                "refusing to approve a plan without resolving which unmodelled "
                "constraint classes the policy blocks"
            )
        return resolve_policy(
            self.policy_repository,
            policy_kind=NETPLAN_DISCLOSURE_POLICY_KIND,
            tenant_id=scenario.tenant_id,
            at=at,
        )

    @staticmethod
    def _selected_approval_subject(
        scenario: NetPlanScenario,
        solve: ScenarioSolveRecord,
        *,
        selected_candidate_id: str | None = None,
    ) -> _ApprovalSubject:
        """Resolve the primary or alternative named by the approval subject."""
        candidate_id = str(
            selected_candidate_id or scenario.selected_candidate_id or scenario.scenario_id
        ).strip()
        if candidate_id == scenario.scenario_id:
            result = solve.result
            return _ApprovalSubject(
                candidate_id=candidate_id,
                actions=tuple(result.selected_actions),
                modelled_constraint_classes=tuple(result.modelled_constraint_classes),
                unmodelled_constraint_classes=tuple(result.unmodelled_constraint_classes),
            )

        prefix = f"{scenario.scenario_id}:alternative:"
        if not candidate_id.startswith(prefix):
            raise NetPlanApprovalError(
                f"selected NetPlan candidate {candidate_id!r} is not part of "
                f"solve {scenario.scenario_id}"
            )
        raw_index = candidate_id[len(prefix) :]
        if not raw_index.isdigit() or int(raw_index) < 1:
            raise NetPlanApprovalError(
                f"selected NetPlan candidate {candidate_id!r} has an invalid index"
            )
        index = int(raw_index) - 1
        alternatives = tuple(solve.result.alternatives)
        if index >= len(alternatives):
            raise NetPlanApprovalError(
                f"selected NetPlan candidate {candidate_id!r} is absent from "
                f"solve {scenario.scenario_id}"
            )
        candidate = alternatives[index]
        return _ApprovalSubject(
            candidate_id=candidate_id,
            actions=tuple(candidate.actions),
            modelled_constraint_classes=tuple(candidate.modelled_constraint_classes),
            unmodelled_constraint_classes=tuple(candidate.unmodelled_constraint_classes),
        )

    def _require_disclosed_classes(
        self,
        scenario: NetPlanScenario,
        solve: ScenarioSolveRecord,
        *,
        selected_candidate_id: str | None = None,
    ) -> tuple[ConstraintClass, ...]:
        """The classes this solve declared it did not bind, or a refusal.

        Two distinct failures are caught here, and neither is a missing feature:

        A result carrying *neither* class set has not disclosed that it bound
        nothing -- it has failed to disclose anything. Reading its empty
        unmodelled set as "nothing is unmodelled" is the exact fail-open the
        CP-SAT production path shipped with (see the 2026-09-02 correction in
        the constraint-classes design note), where a production plan came back
        with both sets empty and would have sailed through any gate that
        trusted them.

        A result whose declaration disagrees with the constraints it was solved
        under is claiming to have bound something it did not. The staleness
        check above has already established that the scenario has not moved
        since the solve, so the two must agree; if they do not, the declaration
        is wrong and cannot be the basis of an approval.
        """
        subject = self._selected_approval_subject(
            scenario,
            solve,
            selected_candidate_id=selected_candidate_id,
        )
        declared_modelled = subject.modelled_constraint_classes
        declared_unmodelled = subject.unmodelled_constraint_classes
        if not declared_modelled and not declared_unmodelled:
            raise NetPlanConstraintDisclosureError(
                "solve result declares neither modelled nor unmodelled constraint "
                "classes; an undisclosed solve cannot be approved"
            )
        if set(declared_modelled) & set(declared_unmodelled):
            raise NetPlanConstraintDisclosureError(
                "solve result constraint disclosure overlaps modelled and unmodelled "
                "constraint classes; an ambiguous solve cannot be approved"
            )
        expected_unmodelled = set(scenario.constraints.unmodelled_classes())
        if set(declared_unmodelled) != expected_unmodelled:
            raise NetPlanConstraintDisclosureError(
                "solve result constraint disclosure does not match the scenario "
                f"constraints: declared unmodelled "
                f"{sorted(item.value for item in declared_unmodelled)}, "
                f"constraints imply {sorted(item.value for item in expected_unmodelled)}"
            )
        expected_modelled = set(scenario.constraints.modelled_classes())
        if set(declared_modelled) != expected_modelled:
            raise NetPlanConstraintDisclosureError(
                "solve result modelled constraint disclosure does not match the "
                "scenario constraints: declared modelled "
                f"{sorted(item.value for item in declared_modelled)}, "
                f"constraints imply {sorted(item.value for item in expected_modelled)}"
            )
        return declared_unmodelled

    def _enforce_constraint_disclosure(
        self,
        scenario: NetPlanScenario,
        solve: ScenarioSolveRecord,
        *,
        selected_candidate_id: str | None = None,
        at: datetime,
    ) -> tuple[DecisionPolicy, ConstraintDisclosureAcknowledgement | None]:
        """Refuse the approval, or return the policy and the signature permitting it.

        The policy is returned rather than re-resolved by the caller so that the
        version recorded on the `ApprovalRecord` is provably the same one the
        decision was checked against -- resolving twice would let a registry
        write between the two calls produce an approval that names a policy it
        was never evaluated under.

        The acknowledgement is None when the plan needs no signature: every
        required class was modelled. Raises when a required class was not
        modelled and either the policy forbids waiving it or no valid signature
        covers it.
        """
        policy = self._require_disclosure_policy(scenario, at=at)
        subject = self._selected_approval_subject(
            scenario,
            solve,
            selected_candidate_id=selected_candidate_id,
        )
        disclosed = self._require_disclosed_classes(
            scenario,
            solve,
            selected_candidate_id=subject.candidate_id,
        )
        evaluation = evaluate_disclosure(
            policy,
            unmodelled_classes=[item.value for item in disclosed],
        )
        if evaluation.is_blocked:
            raise NetPlanConstraintDisclosureError(
                "network plan cannot be approved: required constraint classes "
                f"{','.join(sorted(evaluation.blocking))} were not modelled by this "
                f"solve and policy {policy.policy_version_id} does not permit "
                "acknowledging them; supply the corresponding constraint caps and "
                "re-solve"
            )
        if not evaluation.requires_acknowledgement:
            return policy, None
        required_signature = self._normalize_classes(evaluation.acknowledgeable)
        acknowledgement = self._find_valid_acknowledgement(
            scenario.scenario_id,
            classes=required_signature,
            solver_problem_hash=solve.problem_hash,
            policy_version_id=policy.policy_version_id,
            selected_action_signature=subject.action_signature,
        )
        if acknowledgement is None:
            raise NetPlanConstraintDisclosureError(
                "network plan cannot be approved: required constraint classes "
                f"{','.join(sorted(evaluation.acknowledgeable))} were not modelled "
                "and no valid acknowledgement covers them for this solve under "
                f"policy {policy.policy_version_id}"
            )
        return policy, acknowledgement

    def _find_valid_acknowledgement(
        self,
        scenario_id: str,
        *,
        classes: Sequence[ConstraintClass],
        solver_problem_hash: str,
        policy_version_id: str,
        selected_action_signature: Sequence[tuple[str, str]],
    ) -> ConstraintDisclosureAcknowledgement | None:
        """The most recent signature that still answers for this exact solve.

        Signatures are never mutated or expired in place; they simply stop
        matching once the plan or the policy moves, which is what makes
        "reuse after a re-solve" impossible without deleting anything.
        """
        candidates = [
            candidate
            for candidate in self.repository.list_disclosure_acknowledgements(scenario_id)
            if candidate.covers(
                classes=classes,
                solver_problem_hash=solver_problem_hash,
                policy_version_id=policy_version_id,
                selected_action_signature=selected_action_signature,
            )
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: candidate.acknowledged_at)

    def _require_authentic_approval(self, scenario_id: str) -> ApprovalRecord:
        approvals = self.repository.list_approvals(scenario_id)
        approval = next(
            (
                candidate
                for candidate in reversed(approvals)
                if candidate.is_approved
            ),
            None,
        )
        if approval is None or not approval.authentic_approval_verified:
            raise NetPlanApprovalError(
                "governed execution requires an authentic management approval"
            )
        return approval

    def _verify_authoritative_solve(
        self,
        scenario: NetPlanScenario,
        solve: ScenarioSolveRecord,
        *,
        approval_receipt_id: str,
        selected_candidate_id: str | None = None,
    ) -> ManagementApprovalVerification:
        if self.approval_verifier is None:
            raise NetPlanApprovalError(
                "authoritative management approval verifier is not configured"
            )
        validation_kwargs: dict[str, Any] = {
            "alternative_limit": solve.alternative_limit,
        }
        if solve.execution_metadata.get("mode") == "production_oss":
            authoritative = (
                (solve.execution_metadata.get("engines") or {}).get("authoritative")
                or {}
            )
            if authoritative.get("contract_version") != NETPLAN_PRODUCTION_SOLVER_VERSION:
                raise NetPlanApprovalError(
                    "persisted production solve has an unrecognised authoritative "
                    "solver contract"
                )
            validation_kwargs = {
                "alternative_limit": None,
                "expected_solver_version": NETPLAN_PRODUCTION_SOLVER_VERSION,
            }
        solve_violations = validate_network_plan_solve_result(
            options_by_entity=scenario.options_by_entity,
            constraints=scenario.constraints,
            solve_result=solve.result,
            **validation_kwargs,
        )
        if solve_violations:
            raise NetPlanApprovalError(
                "persisted solve result verification failed: "
                + ",".join(solve_violations)
            )

        subject = self._selected_approval_subject(
            scenario,
            solve,
            selected_candidate_id=selected_candidate_id,
        )
        actions_by_entity = {
            action.entity_id: action.action for action in subject.actions
        }
        source_snapshot_ids = tuple(
            sorted(
                {
                    snapshot_id
                    for action in subject.actions
                    for snapshot_id in action.source_snapshot_ids
                }
            )
        )
        baseline = ManagementBaselineInput(
            baseline_id=scenario.scenario_id,
            baseline_name=scenario.scenario_name,
            scenario_id=scenario.scenario_id,
            actions_by_entity=actions_by_entity,
            approval_receipt_id=approval_receipt_id,
            source_snapshot_ids=source_snapshot_ids,
            scope=f"tenant:{scenario.tenant_id}",
            release_id=scenario.planning_horizon,
        )
        verification = self.approval_verifier.verify(
            ManagementApprovalExpectation(
                receipt_id=approval_receipt_id,
                scenario_id=scenario.scenario_id,
                baseline_id=scenario.scenario_id,
                baseline_name=scenario.scenario_name,
                scope=baseline.scope,
                release_id=baseline.release_id,
                policy_version=scenario.constraints.policy_version,
                actions_by_entity=actions_by_entity,
                source_snapshot_ids=source_snapshot_ids,
                baseline_content_hash=baseline.compute_canonical_hash(
                    constraints=scenario.constraints
                ),
                solver_problem_hash=compute_solver_problem_hash(
                    scenario.options_by_entity,
                    scenario.constraints,
                    100_000.0,
                    solve.alternative_limit,
                    scenario.model_version,
                ),
            ),
        )
        if (
            verification.receipt is None
            or not verification.authority_attests_receipt(verification.receipt)
        ):
            detail = ",".join(
                verification.violations
                or ("authority_verification_attestation_missing",)
            )
            raise NetPlanApprovalError(
                f"authoritative management approval readback failed: {detail}"
            )
        return verification


__all__ = [
    "NetPlanApprovalError",
    "NetPlanConstraintDisclosureError",
    "NetPlanNotFoundError",
    "NetPlanService",
    "PreparedConstraintDisclosureAcknowledgement",
    "ScenarioBuildRequest",
]
