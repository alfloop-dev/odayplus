"""DRAFT campaign commands over one canonical SQL transaction owner."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from modules.adlift.domain.campaign import (
    BudgetProposal,
    CampaignConfiguration,
    CampaignConflict,
    CampaignForbidden,
    CampaignInvalid,
    CampaignNotFound,
    CampaignUnavailable,
    budget_amount,
    budget_currency,
    campaign_period,
    required_text,
)
from modules.adlift.infrastructure.campaign_repository import DurableAdLiftCampaignRepository
from shared.audit import AuditEvent
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.audit.policy import build_security_event, requires_audit
from shared.auth import (
    AccessRequest,
    Action,
    AuthorizationEngine,
    DataClassification,
    Decision,
    Environment,
    ResourceDescriptor,
    rbac_allows,
)
from shared.auth.abac import evaluate_abac
from shared.auth.tenant import check_tenant_isolation
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.command_receipts import TenantScopedCommandReceiptStore
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.repositories import (
    DurableBrandRepository,
    DurableStoreRepository,
)


class AdLiftCampaignService:
    def __init__(self, *, repository: Any, audit_log: Any, job_queue: Any,
                 store_repository: Any, brand_repository: Any, intervention_repository: Any = None) -> None:
        self.repository = repository
        self.audit_log = audit_log
        self.job_queue = job_queue
        self.store_repository = store_repository
        self.brand_repository = brand_repository
        self.intervention_repository = intervention_repository

    def _principal(self, principal: Any, action: Action, *, resource: str = "adlift") -> tuple[str, str]:
        if (principal is None or not principal.authenticated
                or not isinstance(principal.subject_id, str) or not principal.subject_id.strip()
                or not rbac_allows(principal, resource, action)):
            raise CampaignForbidden("CAMPAIGN_PERMISSION_DENIED")
        tenant = principal.scope.tenant_id
        if not isinstance(tenant, str) or not tenant.strip():
            raise CampaignForbidden("CAMPAIGN_TENANT_SCOPE_REQUIRED")
        return tenant, principal.subject_id

    def _binding(self) -> Any:
        repo, audit, queue = self.repository, self.audit_log, self.job_queue
        if (not isinstance(repo, DurableAdLiftCampaignRepository)
                or not isinstance(audit, DurableAuditLog)
                or not isinstance(queue, DurableJobQueue)
                or not isinstance(self.store_repository, DurableStoreRepository)
                or not isinstance(self.brand_repository, DurableBrandRepository)):
            raise CampaignUnavailable("CAMPAIGN_SHARED_SQL_OWNER_REQUIRED")
        engine = repo.engine
        if (engine is None or audit._engine is not engine or queue._engine is not engine
                or self.store_repository._engine is not engine
                or self.brand_repository._engine is not engine):
            raise CampaignUnavailable("CAMPAIGN_SHARED_SQL_OWNER_REQUIRED")
        return engine

    def _stores(self, principal: Any, store_ids: tuple[str, ...], *, action: Action,
                correlation_id: str, lock: bool = False, resource: str = "adlift") -> None:
        tenant, _ = self._principal(principal, action, resource=resource)
        engine = self._binding()
        authz = AuthorizationEngine(audit_log=self.audit_log)
        for store_id in sorted(store_ids):
            try:
                if lock and getattr(engine, "dialect", "") == "postgresql":
                    engine.query_one("SELECT store_id FROM stores WHERE store_id = ? FOR UPDATE", (store_id,))
                store = self.store_repository.get_store(store_id)
            except Exception as exc:
                raise CampaignUnavailable("CAMPAIGN_STORE_OWNER_UNAVAILABLE") from exc
            if store is None or store.store_id != store_id or not store.is_current:
                raise CampaignNotFound("CAMPAIGN_STORE_NOT_FOUND")
            access = AccessRequest(principal=principal, action=action,
                resource=ResourceDescriptor(type=resource, resource_id=store_id,
                    tenant_id=store.tenant_id, brand_id=store.brand_id, store_id=store_id,
                    region_id=store.region_code or None, module=resource,
                    data_classification=DataClassification.CONFIDENTIAL),
                environment=Environment(attributes={"correlation_id": correlation_id}))
            if (store.tenant_id != tenant or not store.brand_id
                    or not check_tenant_isolation(principal, store.tenant_id,
                        resource_type="adlift", resource_id=store_id).allowed
                    or not principal.scope.permits_assigned_area(None)
                    or not principal.scope.permits_heat_zone(None)):
                self.audit_log.record(build_security_event(access, Decision.deny(
                    "CAMPAIGN_STORE_SCOPE_DENIED", policy_id="adlift.object_scope")))
                raise CampaignForbidden("CAMPAIGN_STORE_SCOPE_DENIED")
            try:
                if lock and getattr(engine, "dialect", "") == "postgresql":
                    engine.query_one("SELECT brand_id FROM brands WHERE brand_id = ? FOR UPDATE", (store.brand_id,))
                brand = self.brand_repository.get_brand(store.brand_id)
            except Exception as exc:
                raise CampaignUnavailable("CAMPAIGN_STORE_OWNER_UNAVAILABLE") from exc
            if brand is None or brand.brand_id != store.brand_id or brand.tenant_id != tenant:
                raise CampaignUnavailable("CAMPAIGN_STORE_TENANCY_CONFLICT")
            if resource == "intervention":
                # The original native router owns type-level RBAC, verified
                # creator separation and the precise campaign increase gate.
                # Object scope must not introduce an unregistered blanket
                # high_risk.intervention.approve/execute flag requirement.
                decision = evaluate_abac(access)
                if not decision.allowed or requires_audit(action, access.resource.data_classification):
                    self.audit_log.record(build_security_event(access, decision))
            else:
                decision = authz.authorize(access)
            if not decision.allowed:
                raise CampaignForbidden("CAMPAIGN_STORE_SCOPE_DENIED")

    def _require(self, tenant: str, campaign_id: str) -> CampaignConfiguration:
        self._binding()
        value = self.repository.for_tenant(tenant).get(campaign_id)
        if value is None:
            raise CampaignNotFound("CAMPAIGN_NOT_FOUND")
        return value

    @contextmanager
    def _transaction(self, principal: Any, store_ids: tuple[str, ...], *,
                     campaign_id: str | None, correlation_id: str):
        tenant, _ = self._principal(principal, Action.CREATE)
        engine = self._binding()
        with engine.transaction(), ExitStack() as stack:
            if getattr(engine, "dialect", "") == "postgresql":
                if campaign_id is not None:
                    engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                        (f"adlift-campaign:{tenant}:{campaign_id}",))
            elif not engine._conn.in_transaction:
                engine.execute("BEGIN IMMEDIATE")
            if self.audit_log._worm_sink is not None:
                delivery = DurableAuditDelivery(engine, tenant, self.job_queue,
                                                self.audit_log._worm_sink)
                stack.enter_context(self.audit_log.transactional_worm_sink(delivery))
            self._stores(principal, store_ids, action=Action.CREATE,
                         correlation_id=correlation_id, lock=True)
            yield

    def _command(self, *, principal: Any, store_ids: tuple[str, ...], campaign_id: str | None,
                 idempotency_key: str, payload: dict[str, Any], correlation_id: str,
                 operation: Any) -> dict[str, Any]:
        tenant, subject = self._principal(principal, Action.CREATE)
        key = required_text(idempotency_key)
        self._stores(principal, store_ids, action=Action.CREATE, correlation_id=correlation_id)
        scope = f"{campaign_id or 'create'}:{sha256(subject.encode()).hexdigest()}"
        receipts = TenantScopedCommandReceiptStore(queue=self.job_queue,
            service="adlift.campaign-configurations",
            transaction_factory=lambda: self._transaction(principal, store_ids,
                campaign_id=campaign_id, correlation_id=correlation_id))
        outcome = receipts.run(tenant_id=tenant, idempotency_key=key, scope=scope,
            payload={**payload, "actor_subject_id": subject},
            correlation_id=correlation_id, operation=lambda _: operation())
        return {**outcome.value, "replayed": outcome.replayed}

    def _record(self, value: CampaignConfiguration, *, subject: str,
                correlation_id: str, action: str) -> str:
        event = self.audit_log.record(AuditEvent(
            event_type="adlift.campaign_configuration.v1", actor=subject,
            action=action, resource=f"adlift/campaign/{value.campaign_id}",
            outcome="draft", correlation_id=correlation_id,
            metadata={"tenant_id": value.tenant_id, "store_ids": list(value.treatment_store_ids),
                "configuration_revision": value.revision, "status": "DRAFT",
                "budget_proposal_revision": value.proposals[-1].revision,
                "reason": value.proposals[-1].reason,
                "actor_evidence": "authenticated_principal.v1"}))
        return event.event_id

    def create(self, *, principal: Any, payload: dict[str, Any], idempotency_key: str,
               correlation_id: str) -> dict[str, Any]:
        tenant, subject = self._principal(principal, Action.CREATE)
        stores = tuple(payload["treatment_store_ids"])
        if not stores or len(set(stores)) != len(stores):
            raise CampaignInvalid("CAMPAIGN_STORE_SET_INVALID")
        stores = tuple(required_text(s) for s in stores)
        if len(set(stores)) != len(stores):
            raise CampaignInvalid("CAMPAIGN_STORE_SET_INVALID")
        start, end = campaign_period(payload["period_start"], payload["period_end"])
        proposal = BudgetProposal(revision=1, amount=budget_amount(payload["budget_amount"]),
            currency=budget_currency(payload["budget_currency"]),
            declared_data_version=required_text(payload["declared_data_version"]),
            reason=required_text(payload["reason"]), proposed_by=subject,
            proposed_at=datetime.now(UTC), correlation_id=required_text(correlation_id))
        configuration = {field: required_text(payload[field]) for field in ("name", "channel")}
        creative = payload.get("creative_reference")
        configuration["creative_reference"] = None if creative is None else required_text(creative)

        def write() -> dict[str, Any]:
            value = CampaignConfiguration(tenant_id=tenant, campaign_id=str(uuid4()),
                revision=1, treatment_store_ids=stores, period_start=start, period_end=end,
                proposals=(proposal,), created_by=subject, created_at=datetime.now(UTC),
                **configuration)
            self.repository.for_tenant(tenant).compare_and_save(value, expected_revision=None)
            event_id = self._record(value, subject=subject, correlation_id=correlation_id,
                                    action="create_campaign_draft")
            return {**value.to_dict(), "audit_event_id": event_id,
                    "external_execution_acknowledged": False}

        return self._command(principal=principal, store_ids=stores, campaign_id=None,
            idempotency_key=idempotency_key, payload=payload,
            correlation_id=correlation_id, operation=write)

    def propose_budget(self, campaign_id: str, *, principal: Any, payload: dict[str, Any],
                       expected_revision: int, idempotency_key: str,
                       correlation_id: str) -> dict[str, Any]:
        tenant, subject = self._principal(principal, Action.CREATE)
        current = self._require(tenant, campaign_id)
        amount, currency = budget_amount(payload["budget_amount"]), budget_currency(payload["budget_currency"])
        reason = required_text(payload["reason"])
        version = required_text(payload["declared_data_version"])
        if type(expected_revision) is not int or expected_revision < 1:
            raise CampaignInvalid("CAMPAIGN_EXPECTED_REVISION_REQUIRED")

        def write() -> dict[str, Any]:
            latest = self._require(tenant, campaign_id)
            if latest.revision != expected_revision:
                raise CampaignConflict("CAMPAIGN_REVISION_CONFLICT")
            if latest.treatment_store_ids != current.treatment_store_ids:
                raise CampaignConflict("CAMPAIGN_STORE_SET_CHANGED")
            if currency != latest.proposals[-1].currency:
                raise CampaignInvalid("CAMPAIGN_BUDGET_CURRENCY_CONFLICT")
            history = self._verified_history(tenant, campaign_id)
            baseline = history[0].authorizations[-1] if history is not None else None
            proposal = BudgetProposal(revision=latest.revision + 1, amount=amount,
                currency=currency, declared_data_version=version, reason=reason,
                proposed_by=subject, proposed_at=datetime.now(UTC),
                correlation_id=required_text(correlation_id),
                based_on_authorized_budget_revision=None if baseline is None else baseline.revision,
                authorized_before_amount=None if baseline is None else baseline.binding.after_amount)
            value = replace(latest, revision=latest.revision + 1,
                            proposals=(*latest.proposals, proposal))
            self.repository.for_tenant(tenant).compare_and_save(value,
                expected_revision=expected_revision)
            event_id = self._record(value, subject=subject, correlation_id=correlation_id,
                                    action="propose_campaign_budget")
            return {**value.to_dict(), "audit_event_id": event_id,
                    "external_execution_acknowledged": False}

        return self._command(principal=principal, store_ids=current.treatment_store_ids,
            campaign_id=campaign_id, idempotency_key=idempotency_key,
            payload={**payload, "expected_revision": expected_revision},
            correlation_id=correlation_id, operation=write)

    def _verified_history(self, tenant: str, campaign_id: str):
        from modules.adlift.domain.campaign_authority import NativeBudgetBinding, content_digest
        from modules.intervention.application.approval_contract import verified_creator_subject
        from modules.intervention.domain.lifecycle import InterventionKind
        from shared.infrastructure.persistence.repositories import DurableInterventionRepository

        row = self.repository.for_tenant(tenant).budget_history(campaign_id)
        if row is None:
            return None
        native = self.intervention_repository
        if not isinstance(native, DurableInterventionRepository) or native._store.engine is not self._binding():
            raise CampaignUnavailable("CAMPAIGN_NATIVE_BUDGET_PROVENANCE_REQUIRED")
        current = self._require(tenant, campaign_id)
        previous = None
        for authorization in row[0].authorizations:
            binding = authorization.binding
            if (not isinstance(binding, NativeBudgetBinding) or binding.tenant_id != tenant
                    or binding.campaign_id != campaign_id or type(binding.configuration_revision) is not int
                    or binding.configuration_revision > current.revision or binding.configuration_revision < 1):
                raise CampaignUnavailable("CAMPAIGN_NATIVE_BUDGET_PROVENANCE_INVALID")
            snapshot = replace(current, revision=binding.configuration_revision,
                               proposals=current.proposals[:binding.configuration_revision])
            proposal = snapshot.proposals[-1]
            if (binding.configuration_sha256 != content_digest(snapshot.to_dict())
                    or binding.after_amount != proposal.amount or binding.currency != proposal.currency
                    or binding.declared_data_version != proposal.declared_data_version
                    or binding.before_authorization_revision != proposal.based_on_authorized_budget_revision
                    or binding.before_amount != proposal.authorized_before_amount
                    or binding.proposal_revision != proposal.revision
                    or binding.treatment_store_ids != snapshot.treatment_store_ids
                    or binding.before_authorization_revision != (None if previous is None else previous.revision)
                    or binding.before_amount != (None if previous is None else previous.binding.after_amount)):
                raise CampaignUnavailable("CAMPAIGN_NATIVE_BUDGET_PROVENANCE_INVALID")
            refs = authorization.native_approval_references
            if (len(refs) != len(binding.treatment_store_ids)
                    or {ref[0] for ref in refs} != set(binding.treatment_store_ids)):
                raise CampaignUnavailable("CAMPAIGN_NATIVE_BUDGET_PROVENANCE_INVALID")
            for store_id, case_id, version, event_id in refs:
                case = native.get(case_id)
                if (case is None or case.store_id != store_id or case.kind is not InterventionKind.AD_CAMPAIGN
                        or case.action_spec.get("campaign_budget_binding") != binding.to_dict()
                        or case.approval is None or not case.approval.approved
                        or case.approval.actor_id == case.created_by
                        or verified_creator_subject(case, tenant_id=tenant, audit_log=self.audit_log) != case.created_by):
                    raise CampaignUnavailable("CAMPAIGN_NATIVE_BUDGET_PROVENANCE_INVALID")
                transitions = [(i + 2, t) for i, t in enumerate(case.history)
                    if t.action == "approve" and t.actor == case.approval.actor_id
                    and t.reason == case.approval.decision_reason]
                events = [e for e in self.audit_log.list_events(tenant_id=tenant)
                    if e.event_id == event_id and e.event_type == "intervention.lifecycle.v1"
                    and e.resource == f"intervention/{case_id}" and e.action == "approve"
                    and e.actor == case.approval.actor_id
                    and e.metadata.get("reason") == case.approval.decision_reason
                    and e.metadata.get("policy_version") == case.approval.policy_version
                    and e.metadata.get("actor_evidence") == "authenticated_principal.v1"]
                if len(transitions) != 1 or transitions[0][0] != version or len(events) != 1:
                    raise CampaignUnavailable("CAMPAIGN_NATIVE_BUDGET_PROVENANCE_INVALID")
            previous = authorization
        return row

    def get(self, campaign_id: str, *, principal: Any, correlation_id: str) -> dict[str, Any]:
        tenant, _ = self._principal(principal, Action.VIEW)
        value = self._require(tenant, campaign_id)
        self._stores(principal, value.treatment_store_ids, action=Action.VIEW,
                     correlation_id=correlation_id)
        result = value.to_dict()
        history = self._verified_history(tenant, campaign_id)
        if history is not None:
            authorization = history[0].authorizations[-1]
            result["authorized_budget_revision"] = authorization.revision
            result["authorized_budget"] = authorization.to_dict()
            if authorization.binding.configuration_revision == value.revision:
                result["status"] = "APPROVED"
            result["external_execution_acknowledged"] = False
        return result
