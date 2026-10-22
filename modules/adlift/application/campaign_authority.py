"""Canonical campaign budget authority through existing native AD_CAMPAIGN cases.

This owner coordinates existing human decisions. It does not approve a caller's
amount/actor, treat null as zero, or acknowledge external advertising execution.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from modules.adlift.domain.campaign import (
    CampaignConflict,
    CampaignForbidden,
    CampaignInvalid,
    CampaignNotFound,
    CampaignUnavailable,
)
from modules.adlift.domain.campaign_authority import (
    CampaignBudgetAuthorization,
    CampaignBudgetHistory,
    CampaignReadiness,
    NativeBudgetBinding,
    NativeCampaignRound,
    content_digest,
)
from modules.adlift.domain.incrementality import IncrementalityReport, PreTrendStatus
from modules.intervention.application.approval_contract import verified_creator_subject
from modules.intervention.application.lifecycle_command_contract import COMMAND_ACTIONS
from modules.intervention.domain.lifecycle import InterventionKind, InterventionStatus
from shared.auth import Action
from shared.domain.events import DomainEvent
from shared.infrastructure.persistence.feature_flags import require_durable_flag_owner
from shared.infrastructure.persistence.outbox import DurableOutboxRepository
from shared.infrastructure.persistence.repositories import (
    DurableAdLiftRepository,
    DurableInterventionRepository,
)

SELECTOR = "campaign_budget_proposal"
BINDING = "campaign_budget_binding"
FLAG = "high_risk.adlift.approve"


class AdLiftCampaignBudgetAuthority:
    def __init__(self, *, campaigns: Any, interventions: Any, reports: Any,
                 flags: Any, boundary: Any) -> None:
        self.campaigns, self.interventions, self.reports = campaigns, interventions, reports
        self.flags, self.boundary = flags, boundary
        self._context: ContextVar[Any] = ContextVar("adlift_native_budget_command", default=None)

    def _engine(self):
        engine = self.campaigns._binding()
        if (not isinstance(self.interventions, DurableInterventionRepository)
                or self.interventions._store.engine is not engine):
            raise CampaignUnavailable("CAMPAIGN_NATIVE_SAME_SQL_OWNER_REQUIRED")
        return engine

    def _lock(self, tenant: str, campaign_id: str):
        engine = self._engine()
        if getattr(engine, "dialect", "") == "postgresql":
            engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                             (f"adlift-campaign:{tenant}:{campaign_id}",))
        elif not engine._conn.in_transaction:
            raise CampaignUnavailable("CAMPAIGN_NATIVE_TRANSACTION_REQUIRED")

    def _repo(self, tenant: str):
        return self.campaigns.repository.for_tenant(tenant)

    def _configuration(self, principal: Any, campaign_id: str, action: Action):
        tenant, _ = self.campaigns._principal(principal, action, resource="intervention")
        configuration = self.campaigns._require(tenant, campaign_id)
        self.campaigns._stores(principal, configuration.treatment_store_ids,
            action=action, resource="intervention", correlation_id=self._context.get()[1], lock=True)
        return configuration

    @staticmethod
    def _selected(action_spec: Any) -> tuple[str, int] | None:
        if not isinstance(action_spec, dict):
            raise CampaignInvalid("CAMPAIGN_SELECTOR_INVALID")
        selector = action_spec.get(SELECTOR)
        if selector is None:
            return None
        if (not isinstance(selector, dict) or set(selector) != {"campaign_id", "proposal_revision"}
                or not isinstance(selector["campaign_id"], str) or not selector["campaign_id"].strip()
                or type(selector["proposal_revision"]) is not int or selector["proposal_revision"] < 1):
            raise CampaignInvalid("CAMPAIGN_SELECTOR_INVALID")
        return selector["campaign_id"], selector["proposal_revision"]

    @contextmanager
    def native_command_context(self, *, principal: Any, scope: str, payload: Any,
                               correlation_id: str, native_repository: Any, actual_queue: Any):
        """Only the original authenticated/atomic router enters this context.

        Campaign lock precedes all canonical store/native row locks, matching
        configuration commands. An unrelated legacy AD case is not budget authority.
        """
        action = Action.CREATE
        if scope == "interventions:create":
            if payload.get("kind") != InterventionKind.AD_CAMPAIGN.value:
                yield
                return
            spec = payload.get("action_spec", {})
        else:
            native_id = scope.removeprefix("interventions:").rsplit(":", 1)[0]
            current = native_repository.get(native_id)
            if current is None or current.kind is not InterventionKind.AD_CAMPAIGN:
                yield
                return
            command = scope.rsplit(":", 1)[1]
            action = Action.APPROVE if command == "decision" else Action[COMMAND_ACTIONS.get(command, "CREATE")]
            spec = current.action_spec
            if command == "action" and SELECTOR in payload.get("action_spec", {}):
                spec = payload["action_spec"]
        selected = self._selected(spec)
        bound = spec.get(BINDING)
        if selected is None and bound is None:
            yield
            return
        if (self.boundary is None or native_repository is not self.interventions
                or actual_queue is not self.campaigns.job_queue):
            raise CampaignUnavailable("CAMPAIGN_TRUSTED_NATIVE_OWNER_REQUIRED")
        tenant, _ = self.campaigns._principal(principal, action, resource="intervention")
        try:
            UUID(tenant)
        except (TypeError, ValueError, AttributeError) as exc:
            raise CampaignUnavailable("CAMPAIGN_CANONICAL_EVENT_TENANT_REQUIRED") from exc
        try:
            UUID(correlation_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise CampaignInvalid("CAMPAIGN_CANONICAL_EVENT_CORRELATION_REQUIRED") from exc
        campaign_id = selected[0] if selected else bound.get("campaign_id") if isinstance(bound, dict) else None
        if not isinstance(campaign_id, str) or not campaign_id:
            raise CampaignInvalid("CAMPAIGN_BINDING_INVALID")
        token = self._context.set((principal, correlation_id))
        try:
            self._lock(tenant, campaign_id)
            self._configuration(principal, campaign_id, action)
            yield
        finally:
            self._context.reset(token)

    def _current_binding(self, campaign_id: str, revision: int, *, action: Action):
        context = self._context.get()
        if context is None:
            raise CampaignForbidden("CAMPAIGN_VERIFIED_NATIVE_CONTEXT_REQUIRED")
        principal, _ = context
        configuration = self._configuration(principal, campaign_id, action)
        if configuration.revision != revision:
            raise CampaignConflict("CAMPAIGN_CONFIGURATION_CHANGED")
        tenant = principal.tenant_id
        repo = self._repo(tenant)
        proposal = configuration.proposals[-1]
        history = self.campaigns._verified_history(tenant, campaign_id)
        before = history[0].authorizations[-1] if history else None
        # An already approved round is checked against its recorded predecessor,
        # rather than misinterpreting its own after amount as a new baseline.
        if before is not None and before.binding.configuration_revision == revision:
            previous = history[0].authorizations[-2] if len(history[0].authorizations) > 1 else None
        else:
            previous = before
        if (proposal.based_on_authorized_budget_revision != (previous.revision if previous else None)
                or proposal.authorized_before_amount != (previous.binding.after_amount if previous else None)
                or (previous and previous.binding.currency != proposal.currency)):
            raise CampaignConflict("CAMPAIGN_AUTHORIZED_BASELINE_CHANGED")
        readiness = repo.readiness(campaign_id, revision)
        if readiness is None:
            raise CampaignUnavailable("CAMPAIGN_CANONICAL_PRETREND_REQUIRED")
        ready = readiness[0]
        report = ready.report
        if not isinstance(report, IncrementalityReport):
            raise CampaignUnavailable("CAMPAIGN_CANONICAL_PRETREND_UNAVAILABLE")
        if not isinstance(self.reports, DurableAdLiftRepository) or self.reports._store.engine is not self._engine():
            raise CampaignUnavailable("CAMPAIGN_REPORT_SAME_SQL_OWNER_REQUIRED")
        actual = [r for r in self.reports.history(campaign_id)
            if isinstance(r, IncrementalityReport) and r.report_id == report.report_id
            and r.report_version == report.report_version and content_digest(r.to_dict()) == ready.report_sha256]
        provenance = [e for e in self.campaigns.audit_log.list_events(tenant_id=tenant)
            if e.event_type == "adlift.campaign_pretrend_evaluated.v1"
            and e.resource == f"adlift/campaign/{campaign_id}"
            and e.metadata.get("actor_evidence") == "authenticated_principal.v1"
            and e.metadata.get("configuration_sha256") == ready.configuration_sha256
            and e.metadata.get("declared_data_version") == ready.declared_data_version
            and e.metadata.get("report_id") == report.report_id
            and e.metadata.get("report_sha256") == ready.report_sha256]
        if len(actual) != 1 or len(provenance) != 1:
            raise CampaignUnavailable("CAMPAIGN_ACTUAL_ANALYSIS_PROVENANCE_REQUIRED")
        if (ready.tenant_id != tenant or ready.campaign_id != campaign_id
                or ready.configuration_revision != revision
                or ready.configuration_sha256 != content_digest(configuration.to_dict())
                or ready.declared_data_version != proposal.declared_data_version
                or not isinstance(report, IncrementalityReport)
                or ready.report_sha256 != content_digest(report.to_dict())
                or report.campaign_id != campaign_id
                or tuple(report.treatment_store_ids) != configuration.treatment_store_ids
                or report.pre_trend_status is not PreTrendStatus.PASS
                or not report.source_snapshot_ids
                or any(not isinstance(s, str) or not s.strip() for s in report.source_snapshot_ids)
                or {m.treatment_store_id for m in report.matched_controls} != set(configuration.treatment_store_ids)):
            raise CampaignUnavailable("CAMPAIGN_CANONICAL_PRETREND_UNAVAILABLE")
        return NativeBudgetBinding(tenant_id=tenant, campaign_id=campaign_id,
            configuration_revision=revision, configuration_sha256=ready.configuration_sha256,
            proposal_revision=proposal.revision, declared_data_version=proposal.declared_data_version,
            treatment_store_ids=configuration.treatment_store_ids,
            before_authorization_revision=proposal.based_on_authorized_budget_revision,
            before_amount=proposal.authorized_before_amount, after_amount=proposal.amount,
            currency=proposal.currency, readiness_sha256=content_digest({
                "report_id": report.report_id, "report_version": report.report_version,
                "report_sha256": ready.report_sha256, "configuration_sha256": ready.configuration_sha256}))

    def prepare_action(self, *, kind: Any, store_id: str, action_spec: Any,
                       existing: Any = None) -> dict[str, Any]:
        spec = dict(action_spec or {})
        if kind is not InterventionKind.AD_CAMPAIGN:
            if SELECTOR in spec or BINDING in spec:
                raise CampaignInvalid("CAMPAIGN_BINDING_REQUIRES_AD_CAMPAIGN")
            return spec
        selected = self._selected(spec)
        prior = existing.action_spec.get(BINDING) if existing is not None else None
        if BINDING in spec and spec[BINDING] != prior:
            raise CampaignInvalid("CAMPAIGN_BINDING_IS_SERVER_OWNED")
        if selected is None and prior is None:
            return spec
        campaign_id, revision = selected if selected else (prior["campaign_id"], prior["configuration_revision"])
        binding = self._current_binding(campaign_id, revision, action=Action.CREATE)
        if store_id not in binding.treatment_store_ids:
            raise CampaignForbidden("CAMPAIGN_NATIVE_STORE_OUTSIDE_TARGET")
        if prior is not None and prior != binding.to_dict():
            raise CampaignConflict("CAMPAIGN_BINDING_CHANGED")
        spec.pop(SELECTOR, None)
        spec[BINDING] = binding.to_dict()
        return spec

    def _bound(self, intervention: Any, action: Action):
        raw = intervention.action_spec.get(BINDING)
        if raw is None:
            return None
        if intervention.kind is not InterventionKind.AD_CAMPAIGN or not isinstance(raw, dict):
            raise CampaignUnavailable("CAMPAIGN_BINDING_INVALID")
        binding = self._current_binding(raw.get("campaign_id"), raw.get("configuration_revision"), action=action)
        if raw != binding.to_dict() or intervention.store_id not in binding.treatment_store_ids:
            raise CampaignConflict("CAMPAIGN_BINDING_CHANGED")
        return binding

    def register_case(self, intervention: Any) -> None:
        binding = self._bound(intervention, Action.CREATE)
        if binding is None:
            return
        repo = self._repo(binding.tenant_id)
        row = repo.native_round(binding.campaign_id, binding.configuration_revision)
        current, version = row if row else (NativeCampaignRound(0, binding), None)
        if current.binding != binding:
            raise CampaignConflict("CAMPAIGN_NATIVE_ROUND_CHANGED")
        cases = dict(current.cases)
        previous = cases.get(intervention.store_id)
        if previous is not None and previous != intervention.intervention_id:
            raise CampaignConflict("CAMPAIGN_STORE_ALREADY_HAS_NATIVE_CASE")
        if previous is None:
            cases[intervention.store_id] = intervention.intervention_id
            repo.save_native_round(replace(current, version=current.version + 1,
                cases=tuple(sorted(cases.items()))), expected=version)

    def _gate(self, binding: NativeBudgetBinding):
        if binding.is_increase:
            owner = require_durable_flag_owner(self.flags, self._engine(), queue=self.campaigns.job_queue)
            if not owner.is_enabled(FLAG, on=date.today()):
                raise CampaignForbidden("CAMPAIGN_BUDGET_INCREASE_FLAG_DISABLED")

    def before_decision(self, intervention: Any, *, approved: bool):
        if not approved:
            return
        binding = self._bound(intervention, Action.APPROVE)
        if binding is not None:
            row = self._repo(binding.tenant_id).native_round(binding.campaign_id, binding.configuration_revision)
            if row is None or row[0].binding != binding or dict(row[0].cases).get(intervention.store_id) != intervention.intervention_id:
                raise CampaignConflict("CAMPAIGN_NATIVE_CASE_NOT_BOUND")
            self._gate(binding)

    def _native_approvals(self, binding: NativeBudgetBinding, *, require_all: bool):
        row = self._repo(binding.tenant_id).native_round(binding.campaign_id, binding.configuration_revision)
        if row is None or row[0].binding != binding:
            raise CampaignConflict("CAMPAIGN_NATIVE_ROUND_CHANGED")
        cases = dict(row[0].cases)
        if set(cases) != set(binding.treatment_store_ids):
            if require_all:
                raise CampaignConflict("CAMPAIGN_ALL_NATIVE_APPROVALS_REQUIRED")
            return None
        references = []
        for store_id, case_id in sorted(cases.items()):
            current = self.interventions.get(case_id, for_update=True)
            if (current is None or current.store_id != store_id
                    or current.kind is not InterventionKind.AD_CAMPAIGN
                    or current.action_spec.get(BINDING) != binding.to_dict()):
                raise CampaignConflict("CAMPAIGN_NATIVE_CASE_CHANGED")
            if current.status not in {InterventionStatus.APPROVED, InterventionStatus.EXECUTING,
                                       InterventionStatus.OBSERVING, InterventionStatus.EVALUATING,
                                       InterventionStatus.COMPLETED, InterventionStatus.CLOSED}:
                if require_all:
                    raise CampaignConflict("CAMPAIGN_ALL_NATIVE_APPROVALS_REQUIRED")
                return None
            approval = current.approval
            if approval is None or not approval.approved or approval.actor_id == current.created_by:
                raise CampaignConflict("CAMPAIGN_CANONICAL_NATIVE_APPROVAL_REQUIRED")
            creator = verified_creator_subject(current, tenant_id=binding.tenant_id,
                                               audit_log=self.campaigns.audit_log)
            if creator is None or creator != current.created_by:
                raise CampaignConflict("CAMPAIGN_NATIVE_CREATOR_UNVERIFIED")
            transitions = [t for t in current.history if t.action == "approve"
                and t.actor == approval.actor_id and t.reason == approval.decision_reason]
            events = [e for e in self.campaigns.audit_log.list_events(tenant_id=binding.tenant_id)
                if e.event_type == "intervention.lifecycle.v1"
                and e.resource == f"intervention/{case_id}" and e.action == "approve"
                and e.actor == approval.actor_id and e.metadata.get("actor_evidence") == "authenticated_principal.v1"
                and e.metadata.get("reason") == approval.decision_reason
                and e.metadata.get("policy_version") == approval.policy_version]
            if len(transitions) != 1 or len(events) != 1:
                raise CampaignConflict("CAMPAIGN_NATIVE_APPROVAL_AUDIT_REQUIRED")
            # Approval is the exact transition version, not a later execute version.
            approval_version = next(i + 1 for i, t in enumerate(current.history, 1) if t is transitions[0])
            references.append((store_id, case_id, approval_version, events[0].event_id))
        return tuple(references)

    def after_decision(self, intervention: Any, *, approved: bool):
        if not approved:
            return
        binding = self._bound(intervention, Action.APPROVE)
        if binding is None:
            return
        references = self._native_approvals(binding, require_all=False)
        if references is None:
            return  # Honest partial native decisions, not aggregate approval.
        self._gate(binding)
        repo = self._repo(binding.tenant_id)
        row = repo.budget_history(binding.campaign_id)
        history, version = row if row else (CampaignBudgetHistory(0, ()), None)
        if history.authorizations and history.authorizations[-1].binding == binding:
            raise CampaignConflict("CAMPAIGN_BUDGET_ALREADY_AUTHORIZED")
        principal, cid = self._context.get()
        authorization = CampaignBudgetAuthorization(revision=history.version + 1,
            binding=binding, native_approval_references=references,
            authorized_by=principal.subject_id, authorized_at=datetime.now(UTC), correlation_id=cid)
        repo.save_budget_history(binding.campaign_id, CampaignBudgetHistory(history.version + 1,
            (*history.authorizations, authorization)), expected=version)
        self._event(binding, authorization.revision, "adlift.campaign_approved", {
            "authorization": authorization.to_dict(), "external_execution_acknowledged": False})

    def before_execute(self, intervention: Any):
        binding = self._bound(intervention, Action.EXECUTE)
        if binding is None:
            return
        history = self._repo(binding.tenant_id).budget_history(binding.campaign_id)
        references = self._native_approvals(binding, require_all=True)
        if (history is None or history[0].authorizations[-1].binding != binding
                or history[0].authorizations[-1].native_approval_references != references):
            raise CampaignConflict("CAMPAIGN_CURRENT_BUDGET_AUTHORITY_REQUIRED")
        self._gate(binding)

    def after_execute(self, intervention: Any):
        binding = self._bound(intervention, Action.EXECUTE)
        if binding is None:
            return
        # Native EXECUTED/OBSERVING means original coordination only. The
        # marketing platform has not acknowledged an external start or spend.
        self._event(binding, intervention.version, "adlift.campaign_execution_requested", {
            "native_intervention_id": intervention.intervention_id,
            "native_execution_id": intervention.execution.execution_id,
            "external_execution_acknowledged": False}, aggregate_id=intervention.intervention_id)

    def _event(self, binding: NativeBudgetBinding, version: int, event_type: str,
               payload: dict[str, Any], *, aggregate_id: str | None = None):
        principal, cid = self._context.get()
        event = DomainEvent(event_type=event_type, payload={"campaign_id": binding.campaign_id,
            "configuration_revision": binding.configuration_revision, "binding_sha256": binding.digest,
            **payload}, tenant_id=binding.tenant_id, aggregate_type="adlift_campaign",
            aggregate_id=aggregate_id or binding.campaign_id, aggregate_version=version,
            partition_key=f"{binding.tenant_id}:{binding.campaign_id}", correlation_id=cid,
            producer="adlift_native_budget_authority", actor_ref=principal.subject_id,
            policy_version="existing-native-intervention.v1", schema_ref=("#/payloads/AdLiftCampaignApprovedV1" if event_type == "adlift.campaign_approved"
                else "#/payloads/AdLiftCampaignExecutionRequestedV1"))
        DurableOutboxRepository(self._engine()).save(event)
        from shared.audit import AuditEvent
        self.campaigns.audit_log.record(AuditEvent(event_type=event_type + ".v1",
            actor=principal.subject_id, action=event_type.rsplit(".", 1)[-1],
            resource=f"adlift/campaign/{binding.campaign_id}", outcome="pending_delivery",
            correlation_id=cid, metadata={"tenant_id": binding.tenant_id,
                "domain_event_id": event.event_id, "binding_sha256": binding.digest,
                "configuration_revision": binding.configuration_revision,
                "external_execution_acknowledged": False}))

    def touches_native_command(self, scope: str, payload: Any) -> bool:
        if scope == "interventions:create":
            spec = payload.get("action_spec", {})
        else:
            case_id = scope.removeprefix("interventions:").rsplit(":", 1)[0]
            current = self.interventions.get(case_id)
            spec = {} if current is None else current.action_spec
            if scope.endswith(":action"):
                spec = {**spec, **payload.get("action_spec", {})}
        return isinstance(spec, dict) and (SELECTOR in spec or BINDING in spec)

    def current_reports(self, principal: Any, *, correlation_id: str,
                        campaign_id: str | None = None, evidence_level: str | None = None):
        """Only this tenant's real readiness, never global latest, admits a report."""
        tenant, _subject = self.campaigns._principal(principal, Action.VIEW)
        engine = self._engine()
        if not isinstance(self.reports, DurableAdLiftRepository) or self.reports._store.engine is not engine:
            raise CampaignUnavailable("CAMPAIGN_REPORT_SAME_SQL_OWNER_REQUIRED")
        visible = []
        with engine.transaction():
            ready_rows = self._repo(tenant).readiness_rows()
            for ready in ready_rows:
                if campaign_id is not None and ready.campaign_id != campaign_id:
                    continue
                try:
                    configuration = self.campaigns._require(tenant, ready.campaign_id)
                    if ready.configuration_revision != configuration.revision:
                        continue  # A previous configuration is not the current report.
                    report = ready.report
                    targets = ready.analysis_store_ids
                    if (not isinstance(report, IncrementalityReport)
                            or ready.configuration_sha256 != content_digest(configuration.to_dict())
                            or ready.declared_data_version != configuration.proposals[-1].declared_data_version
                            or report.campaign_id != configuration.campaign_id
                            or report.treatment_store_ids != configuration.treatment_store_ids
                            or ready.report_sha256 != content_digest(report.to_dict())
                            or not isinstance(targets, tuple) or not targets
                            or any(not isinstance(i, str) or not i for i in targets)
                            or not set((*report.treatment_store_ids, *report.control_store_ids)) <= set(targets)):
                        raise CampaignUnavailable("CAMPAIGN_CURRENT_REPORT_BINDING_INVALID")
                    self.campaigns._stores(principal, targets, action=Action.VIEW,
                        correlation_id=correlation_id)
                    actual = [r for r in self.reports.history(configuration.campaign_id)
                        if isinstance(r, IncrementalityReport) and r.report_id == report.report_id
                        and r.report_version == report.report_version
                        and content_digest(r.to_dict()) == ready.report_sha256]
                    provenance = [e for e in self.campaigns.audit_log.list_events(tenant_id=tenant)
                        if e.event_type == "adlift.campaign_pretrend_evaluated.v1"
                        and e.resource == f"adlift/campaign/{configuration.campaign_id}"
                        and e.metadata.get("actor_evidence") == "authenticated_principal.v1"
                        and e.metadata.get("configuration_sha256") == ready.configuration_sha256
                        and e.metadata.get("declared_data_version") == ready.declared_data_version
                        and e.metadata.get("report_id") == report.report_id
                        and e.metadata.get("report_sha256") == ready.report_sha256]
                    if len(actual) != 1 or len(provenance) != 1:
                        raise CampaignUnavailable("CAMPAIGN_ACTUAL_ANALYSIS_PROVENANCE_REQUIRED")
                    if evidence_level is None or (report.evidence_level is not None
                            and report.evidence_level.value == evidence_level):
                        visible.append(report)
                except (CampaignForbidden, CampaignNotFound):
                    # List and detail do not reveal another tenant or a revoked
                    # object's existence. Current owner failures remain 503.
                    continue
        if campaign_id is not None:
            if len(visible) > 1:
                raise CampaignUnavailable("CAMPAIGN_CURRENT_REPORT_SET_INVALID")
            return visible[0] if visible else None
        return visible

    @staticmethod
    def is_bound_job(payload: Any) -> bool:
        return isinstance(payload, dict) and "_canonical_adlift_bound_job" in payload

    @staticmethod
    def analysis_request_sha(campaigns: list[Any], generated_at: Any) -> str:
        try:
            return content_digest({"campaigns": campaigns, "generated_at": generated_at})
        except CampaignUnavailable as exc:
            raise CampaignInvalid("CAMPAIGN_ANALYSIS_REQUEST_INVALID") from exc

    @staticmethod
    def _analysis_result_sha(payload: dict[str, Any]) -> str:
        return content_digest({k: v for k, v in payload.items() if k not in {
            "job_id", "_canonical_adlift_bound_job", "created", "audit_event_id", "correlation_id"}})

    def _analysis_receipt_binding(self, principal: Any, campaigns: list[Any],
                                  generated_at: Any, payload: dict[str, Any]) -> dict[str, Any]:
        from modules.adlift.domain.incrementality import AdCampaign

        selections = []
        outputs = payload.get("reports")
        if not isinstance(outputs, list) or len(outputs) != len(campaigns):
            raise CampaignUnavailable("CAMPAIGN_ANALYSIS_REPORT_SET_INVALID")
        for item in campaigns:
            actual = AdCampaign.from_mapping(item)
            configuration = self.campaigns._require(principal.tenant_id, actual.campaign_id)
            matching = [r for r in outputs if r.get("campaign_id") == actual.campaign_id]
            if len(matching) != 1:
                raise CampaignUnavailable("CAMPAIGN_ANALYSIS_ACTUAL_REPORT_REQUIRED")
            output = matching[0]
            selections.append({"campaign_id": configuration.campaign_id,
                "configuration_revision": configuration.revision,
                "configuration_sha256": content_digest(configuration.to_dict()),
                "declared_data_version": configuration.proposals[-1].declared_data_version,
                "store_ids": list(dict.fromkeys((*actual.treatment_store_ids, *actual.candidate_control_store_ids))),
                "report_id": output["report_id"], "report_version": output["report_version"],
                "report_sha256": content_digest(output)})
        return {"schema": "adlift.canonical-bound-job.v1", "tenant_id": principal.tenant_id,
            "subject_id": principal.subject_id, "request_sha256": self.analysis_request_sha(campaigns, generated_at),
            "result_sha256": self._analysis_result_sha(payload), "selections": selections}

    def authorize_bound_job(self, principal: Any, payload: dict[str, Any], *, action: Action,
                            campaigns: list[Any] | None = None, generated_at: Any = None,
                            correlation_id: str) -> None:
        """Stored server facts, never a replay body's asserted target, grant access."""
        tenant, subject = self.campaigns._principal(principal, action)
        binding = payload.get("_canonical_adlift_bound_job")
        if (not isinstance(binding, dict) or set(binding) != {"schema", "tenant_id", "subject_id",
                "request_sha256", "result_sha256", "selections"}
                or binding["schema"] != "adlift.canonical-bound-job.v1"
                or binding["tenant_id"] != tenant):
            raise CampaignForbidden("CAMPAIGN_CACHED_ANALYSIS_SCOPE_DENIED")
        if campaigns is not None:
            if binding["subject_id"] != subject:
                raise CampaignForbidden("CAMPAIGN_CACHED_ANALYSIS_SUBJECT_DENIED")
        if binding["result_sha256"] != self._analysis_result_sha(payload):
            raise CampaignUnavailable("CAMPAIGN_CACHED_ANALYSIS_RESULT_CHANGED")
        selections = binding["selections"]
        if not isinstance(selections, list) or not selections:
            raise CampaignUnavailable("CAMPAIGN_CACHED_ANALYSIS_BINDING_INVALID")
        if not isinstance(self.reports, DurableAdLiftRepository) or self.reports._store.engine is not self._engine():
            raise CampaignUnavailable("CAMPAIGN_REPORT_SAME_SQL_OWNER_REQUIRED")
        for selected in selections:
            if (not isinstance(selected, dict) or set(selected) != {"campaign_id", "configuration_revision",
                    "configuration_sha256", "declared_data_version", "store_ids", "report_id", "report_version", "report_sha256"}
                    or not isinstance(selected["store_ids"], list) or not selected["store_ids"]
                    or any(not isinstance(i, str) or not i for i in selected["store_ids"])):
                raise CampaignUnavailable("CAMPAIGN_CACHED_ANALYSIS_BINDING_INVALID")
            configuration = self.campaigns._require(tenant, selected["campaign_id"])
            # Current grants apply to the frozen actual treatment and control
            # target set even if a new body omits or replaces the selector.
            self.campaigns._stores(principal, tuple(selected["store_ids"]), action=action,
                correlation_id=correlation_id)
            if (configuration.revision != selected["configuration_revision"]
                    or content_digest(configuration.to_dict()) != selected["configuration_sha256"]
                    or configuration.proposals[-1].declared_data_version != selected["declared_data_version"]):
                raise CampaignConflict("CAMPAIGN_CACHED_ANALYSIS_CONFIGURATION_CHANGED")
            rows = [r for r in self.reports.history(configuration.campaign_id)
                if isinstance(r, IncrementalityReport) and r.report_id == selected["report_id"]
                and r.report_version == selected["report_version"]
                and content_digest(r.to_dict()) == selected["report_sha256"]]
            outputs = [r for r in payload.get("reports", []) if isinstance(r, dict)
                and r.get("campaign_id") == configuration.campaign_id
                and content_digest(r) == selected["report_sha256"]]
            if len(rows) != 1 or len(outputs) != 1:
                raise CampaignUnavailable("CAMPAIGN_CACHED_ANALYSIS_ACTUAL_REPORT_REQUIRED")

        if campaigns is not None and binding["request_sha256"] != self.analysis_request_sha(campaigns, generated_at):
            raise CampaignConflict("CAMPAIGN_ANALYSIS_IDEMPOTENCY_CONTENT_CHANGED")

    def bound_job(self, *, principal: Any, campaigns: list[Any], generated_at: Any,
                  idempotency_key: str | None, correlation_id: str, receipts: Any, compute: Any):
        """Serialize canonical computation and receipt; replay is always read-only."""
        from shared.infrastructure.persistence.job_receipts import TenantScopedJobReceiptStore

        engine = self._engine()
        if (not isinstance(receipts, TenantScopedJobReceiptStore) or not receipts.is_durable
                or receipts.queue is not self.campaigns.job_queue or receipts.queue._engine is not engine):
            raise CampaignUnavailable("CAMPAIGN_BOUND_JOB_DURABLE_OWNER_REQUIRED")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise CampaignInvalid("CAMPAIGN_BOUND_JOB_IDEMPOTENCY_REQUIRED")
        tenant, _subject = self.campaigns._principal(principal, Action.CREATE)
        with engine.transaction():
            if getattr(engine, "dialect", "") == "postgresql":
                engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    ("adlift-bound-job:" + tenant + ":" + idempotency_key,))
            elif not engine._conn.in_transaction:
                engine.execute("BEGIN IMMEDIATE")
            prior = receipts.get_by_idempotency_key(tenant, idempotency_key)
            if prior is not None:
                if not self.is_bound_job(prior):
                    raise CampaignConflict("CAMPAIGN_ANALYSIS_IDEMPOTENCY_LANE_CHANGED")
                self.authorize_bound_job(principal, prior, action=Action.CREATE,
                    campaigns=campaigns, generated_at=generated_at, correlation_id=correlation_id)
                return prior, False
            if not campaigns or any(not isinstance(i, dict) or "campaign_configuration_revision" not in i for i in campaigns):
                raise CampaignConflict("CAMPAIGN_CANONICAL_ANALYSIS_SELECTOR_CHANGED")
            result = compute()
            payload = result.to_dict()
            payload["_canonical_adlift_bound_job"] = self._analysis_receipt_binding(principal, campaigns, generated_at, payload)
            saved, created = receipts.put_completed(tenant_id=tenant, idempotency_key=idempotency_key,
                correlation_id=correlation_id, build_receipt=lambda _job_id: payload)
            self.authorize_bound_job(principal, saved, action=Action.CREATE,
                campaigns=campaigns, generated_at=generated_at, correlation_id=correlation_id)
            return saved, created

    def run_bound_analysis(self, *, principal: Any, campaigns: list[Any],
                           correlation_id: str, compute: Any):
        """Attach actual original computation to its canonical tenant/version.

        Only explicitly selected campaign configurations enter this path. The
        original unbound analysis remains unchanged and never establishes budget
        readiness through a global latest-report lookup.
        """
        from contextlib import ExitStack

        from modules.adlift.domain.incrementality import AdCampaign
        from shared.audit import AuditEvent
        from shared.audit.durable_delivery import DurableAuditDelivery

        engine = self._engine()
        if not isinstance(self.reports, DurableAdLiftRepository) or self.reports._store.engine is not engine:
            raise CampaignUnavailable("CAMPAIGN_REPORT_SAME_SQL_OWNER_REQUIRED")
        tenant, subject = self.campaigns._principal(principal, Action.CREATE)
        if not campaigns or any(not isinstance(item, Mapping) for item in campaigns):
            raise CampaignInvalid("CAMPAIGN_ANALYSIS_SELECTOR_REQUIRED")
        ids = [item.get("campaign_id") for item in campaigns]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise CampaignInvalid("CAMPAIGN_ANALYSIS_SELECTOR_INVALID")
        selected = {}
        with engine.transaction(), ExitStack() as stack:
            if getattr(engine, "dialect", "") != "postgresql" and not engine._conn.in_transaction:
                engine.execute("BEGIN IMMEDIATE")
            if self.campaigns.audit_log._worm_sink is not None:
                stack.enter_context(self.campaigns.audit_log.transactional_worm_sink(
                    DurableAuditDelivery(engine, tenant, self.campaigns.job_queue,
                                         self.campaigns.audit_log._worm_sink)))
            for campaign_id in sorted(ids):
                self._lock(tenant, campaign_id)
            for item in campaigns:
                campaign_id = item["campaign_id"]
                configuration = self.campaigns._require(tenant, campaign_id)
                revision = item.get("campaign_configuration_revision")
                if type(revision) is not int or revision != configuration.revision:
                    raise CampaignConflict("CAMPAIGN_ANALYSIS_CONFIGURATION_CHANGED")
                if item.get("declared_data_version") != configuration.proposals[-1].declared_data_version:
                    raise CampaignConflict("CAMPAIGN_ANALYSIS_INPUT_VERSION_CHANGED")
                try:
                    actual = AdCampaign.from_mapping(item)
                except (ValueError, TypeError, KeyError, OverflowError) as exc:
                    raise CampaignInvalid("CAMPAIGN_ANALYSIS_INPUT_INVALID") from exc
                if (actual.treatment_store_ids != configuration.treatment_store_ids
                        or actual.channel != configuration.channel or actual.name != configuration.name
                        or actual.campaign_period_start != configuration.period_start
                        or actual.campaign_period_end != configuration.period_end):
                    raise CampaignConflict("CAMPAIGN_ANALYSIS_TARGET_CHANGED")
                store_ids = tuple(dict.fromkeys((*actual.treatment_store_ids, *actual.candidate_control_store_ids)))
                self.campaigns._stores(principal, store_ids, action=Action.CREATE,
                    correlation_id=correlation_id, lock=True)
                if (not actual.observations or any(metric.store_id not in store_ids
                        or not metric.source_snapshot_ids or any(not isinstance(s, str) or not s.strip()
                            for s in metric.source_snapshot_ids) for metric in actual.observations)):
                    raise CampaignInvalid("CAMPAIGN_ANALYSIS_SOURCE_LINEAGE_REQUIRED")
                selected[campaign_id] = (configuration, actual.candidate_control_store_ids)
            # The same shipped model/worker computes and stores the real report.
            # Its observed ad_spend remains an input, never the proposed budget.
            result = compute(campaigns)
            for output in result.result["reports"]:
                campaign_id = output["campaign_id"]
                if campaign_id not in selected:
                    raise CampaignUnavailable("CAMPAIGN_ANALYSIS_REPORT_IDENTITY_INVALID")
                configuration = selected[campaign_id][0]
                matches = [r for r in self.reports.history(campaign_id)
                           if r.report_id == output["report_id"] and r.to_dict() == output]
                if len(matches) != 1:
                    raise CampaignUnavailable("CAMPAIGN_ANALYSIS_ACTUAL_REPORT_REQUIRED")
                report = matches[0]
                value = CampaignReadiness(tenant_id=tenant, campaign_id=campaign_id,
                    configuration_revision=configuration.revision,
                    configuration_sha256=content_digest(configuration.to_dict()),
                    declared_data_version=configuration.proposals[-1].declared_data_version,
                    report=report, report_sha256=content_digest(report.to_dict()),
                    analysis_store_ids=tuple(dict.fromkeys((*report.treatment_store_ids,
                        *selected[campaign_id][1]))))
                repo = self._repo(tenant)
                current = repo.readiness(campaign_id, configuration.revision)
                repo.save_readiness(value, expected=None if current is None else current[1])
                self.campaigns.audit_log.record(AuditEvent(event_type="adlift.campaign_pretrend_evaluated.v1",
                    actor=subject, action="evaluate_campaign_pretrend", resource=f"adlift/campaign/{campaign_id}",
                    outcome=report.pre_trend_status.value, correlation_id=correlation_id,
                    metadata={"tenant_id": tenant, "configuration_revision": configuration.revision,
                        "configuration_sha256": value.configuration_sha256,
                        "declared_data_version": value.declared_data_version,
                        "report_id": report.report_id, "report_sha256": value.report_sha256,
                        "actor_evidence": "authenticated_principal.v1"}))
            if len(result.result["reports"]) != len(selected):
                raise CampaignUnavailable("CAMPAIGN_ANALYSIS_REPORT_SET_INVALID")
            return result
