"""Request-bound ownership for the original AVM and NetPlan HTTP contracts.

The native services remain the writers. This boundary supplies their actual
tenant repository, admits canonical parents before reads/replay, and keeps the
entire HTTP operation inside the native physical owner transaction.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request

from apps.api.oday_api.runtime_mode import live_data_required
from apps.api.oday_api.security.dependencies import (
    _record_denial,
    default_boundary,
    require_permission,
)
from modules.opsboard.application.network_read_scope import candidate_scope_evidence
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
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import (
    DurableAVMRepository,
    DurableNetPlanRepository,
)


def native_audit_metadata(request: Request, metadata: dict[str, Any] | None):
    result = dict(metadata or {})
    owner = getattr(request.state, "native_request_owner", None)
    if owner is not None:
        result["tenant_id"] = owner.principal.tenant_id
        result["authenticated_principal"] = owner.credential_binding()
    return result


def native_audit_actor(request: Request, engineering_actor: str) -> str:
    owner = getattr(request.state, "native_request_owner", None)
    return owner.principal.subject_id if owner is not None else engineering_actor


def native_response(request: Request, payload):
    owner = getattr(request.state, "native_request_owner", None)
    if owner is None or owner.factory.resource != "avm" or (
        rbac_allows(owner.principal, "avm", Action.APPROVE)
        or rbac_allows(owner.principal, "avm", Action.EXPORT)
    ):
        return payload

    def redact(value):
        if isinstance(value, dict):
            return {key: None if key in {"reserve_price", "asking_price", "settlement_price"}
                    else redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    return redact(payload)


class NativeRequestOwnerFactory:
    def __init__(self, *, resource, repository_for_tenant, stores, brands,
                 audit_log, queue, boundary, engine,
                 candidate_snapshot_for_tenant=None, outcome_registry_for_tenant=None, formal=False,
                 production_executor=None, depreciation_version_pin=None, rollback_receipt=None):
        self.resource, self.repositories = resource, repository_for_tenant
        self.stores, self.brands = stores, brands
        self.audit, self.queue, self.boundary = audit_log, queue, boundary
        self.authz = engine
        self.candidates = candidate_snapshot_for_tenant
        self.outcomes = outcome_registry_for_tenant
        self.formal = formal
        self.production_executor = production_executor
        self.depreciation_version_pin = depreciation_version_pin
        self.rollback_receipt = rollback_receipt

    def permission(self, action):
        original = require_permission(self.resource, action, engine=self.authz,
                                      boundary=self.boundary)

        def guard(request: Request):
            # None cannot later adopt another application's global boundary.
            if self.boundary is None:
                raise HTTPException(503, "NATIVE_AUTHENTICATION_OWNER_UNAVAILABLE")
            if (getattr(self.boundary, "_session_service", None) is None
                    and default_boundary() is not self.boundary):
                raise HTTPException(503, "NATIVE_AUTHENTICATION_OWNER_UNAVAILABLE")
            if not getattr(request.state, "correlation_id", None):
                request.state.correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
            return original(request)

        return guard

    @contextmanager
    def operation(self, request, action, body=None, *, force_production=False):
        principal = request.state.operator_principal
        if not principal.authenticated or not principal.tenant_id:
            raise HTTPException(401, "NATIVE_AUTHENTICATED_TENANT_REQUIRED")
        if principal.attributes.get("token_type") not in {"local", "oidc", "service"}:
            raise HTTPException(503, "NATIVE_CURRENT_CREDENTIAL_SOURCE_UNAVAILABLE")
        if not callable(self.repositories):
            raise HTTPException(503, "NATIVE_TENANT_OWNER_UNAVAILABLE")
        repository = self.repositories(principal.tenant_id)
        documents = getattr(repository, "_store", None)
        sql = getattr(documents, "engine", None)
        expected = DurableAVMRepository if self.resource == "avm" else DurableNetPlanRepository
        if (not isinstance(repository, expected) or not isinstance(documents, TenantScopedDocumentStore)
                or documents.tenant_id != principal.tenant_id or sql is None
                or not isinstance(self.audit, DurableAuditLog) or not isinstance(self.queue, DurableJobQueue)
                or getattr(self.audit, "_engine", None) is not sql
                or getattr(self.queue, "_engine", None) is not sql
                or self.stores is None or self.brands is None):
            raise HTTPException(503, "NATIVE_TENANT_OWNER_UNAVAILABLE")
        owner = NativeRequestOwner(self, request, repository, action, force_production=force_production)
        parent = request.path_params.get("case_id" if self.resource == "avm" else "scenario_id")
        if self.resource == "netplan" and parent is None and body is not None:
            parent = getattr(body, "scenario_id", None)
        transaction = repository.case_transaction if self.resource == "avm" else repository.scenario_transaction
        try:
            with ExitStack() as stack:
                stack.enter_context(transaction(parent))
                sink = getattr(self.audit, "_worm_sink", None)
                if sink is not None:
                    from shared.audit.durable_delivery import DurableAuditDelivery
                    delivery = DurableAuditDelivery(sql, principal.tenant_id, self.queue, sink)
                    stack.enter_context(self.audit.transactional_worm_sink(delivery))
                request.state.native_request_owner = owner
                owner.pin_body(body)
                owner.admit_request(body)
                yield owner
        except HTTPException:
            # The native transaction rolls back domain/receipt writes first.
            # A decided object denial must still survive that rollback.
            if owner.denial is not None:
                _record_denial(self.authz, *owner.denial)
            raise
        finally:
            request.state.native_request_owner = None


class NativeRequestOwner:
    def __init__(self, factory, request, repository, action, *, force_production=False):
        self.factory, self.request, self.raw = factory, request, repository
        self.principal = request.state.operator_principal
        self.action, self.denial = action, None
        self.formal = factory.formal or force_production
        self.repository = _NativeRepositoryView(self)

    def outcome_registry(self):
        # The same current raw scenario/ABAC is rechecked under the registry's
        # physical scenario lock, including native execute/outcome transitions.
        from shared.infrastructure.persistence.netplan_outcomes import DurableNetPlanOutcomeRegistry

        def authorize(current):
            if self.scenario(current.scenario_id) != current:
                raise HTTPException(409, "NETPLAN_OUTCOME_SCENARIO_CHANGED")

        provider = self.factory.outcomes
        if not callable(provider):
            raise HTTPException(503, "NETPLAN_OUTCOME_OWNER_UNAVAILABLE")
        registry = provider(repository=self.raw, principal=self.principal,
            authorize=authorize, correlation_id=self.request.state.correlation_id)
        if (not isinstance(registry, DurableNetPlanOutcomeRegistry)
                or registry.repository is not self.raw or registry.documents is not self.raw._store
                or registry.principal is not self.principal or registry.authorize is not authorize
                or registry.audit is not self.factory.audit or registry.queue is not self.factory.queue
                or registry.engine is not self.raw._store.engine
                or registry.documents.tenant_id != self.principal.tenant_id
                or registry.correlation_id != self.request.state.correlation_id):
            raise HTTPException(503, "NETPLAN_OUTCOME_OWNER_UNAVAILABLE")
        return registry

    def credential_binding(self):
        return {"subject_id": self.principal.subject_id,
                "tenant_id": self.principal.tenant_id,
                "token_type": self.principal.attributes.get("token_type")}

    def _decision(self, resource, *, valid=True, hide=False, scoped_candidate=False):
        access = AccessRequest(principal=self.principal, action=self.action, resource=resource,
            environment=Environment(attributes={"correlation_id": self.request.state.correlation_id}))
        if scoped_candidate:
            valid = (valid and self.principal.scope.permits_module(resource.module)
                     and self.principal.scope.permits_classification(resource.data_classification))
        decision = (Decision.allow("canonical candidate parent axes verified") if scoped_candidate else evaluate_abac(access)) if valid and rbac_allows(self.principal, self.factory.resource, self.action) else Decision.deny(
            "native canonical object outside current scope", policy_id="native.owner.scope")
        if not decision.allowed:
            self.denial = (access, decision)
            self.factory.audit.record(build_security_event(access, decision))
            raise HTTPException(404 if hide else 403, "NATIVE_OBJECT_NOT_FOUND" if hide else "NATIVE_OBJECT_SCOPE_DENIED")
        self.factory.audit.record(build_security_event(access, decision))

    def store(self, store_id, *, hide=False):
        store = self.factory.stores.get_store(store_id)
        brand = self.factory.brands.get_brand(store.brand_id) if store is not None else None
        valid = (store is not None and store.is_current
                 and store.tenant_id == self.principal.tenant_id
                 and brand is not None and brand.tenant_id == self.principal.tenant_id
                 and self.principal.scope.permits_assigned_area(None)
                 and self.principal.scope.permits_heat_zone(None))
        self._decision(ResourceDescriptor(type=self.factory.resource, resource_id=store_id,
            tenant_id=getattr(store, "tenant_id", None), brand_id=getattr(store, "brand_id", None),
            store_id=store_id, region_id=getattr(store, "region_code", None) or None,
            module="avm" if self.factory.resource == "avm" else "operator_console",
            data_classification=DataClassification.CONFIDENTIAL), valid=valid, hide=hide)

    def candidate(self, candidate_id, *, hide=False):
        snapshot = self.factory.candidates(self.principal.tenant_id) if callable(self.factory.candidates) else {}
        permitted = candidate_scope_evidence(self.principal, snapshot)
        self._decision(ResourceDescriptor(type="netplan", resource_id=candidate_id,
            tenant_id=self.principal.tenant_id, module="operator_console",
            data_classification=DataClassification.CONFIDENTIAL),
            valid=candidate_id in permitted, hide=hide, scoped_candidate=True)

    def entity(self, identity, *, hide=False):
        if self.factory.stores.get_store(identity) is not None:
            self.store(identity, hide=hide)
        else:
            self.candidate(identity, hide=hide)

    def case(self, case_id, *, hide=True):
        # Read raw first: legacy migration must not write before object admission.
        row = self.raw._store.get(self.raw._CASES, case_id)
        if row is None:
            raise HTTPException(404, "NATIVE_OBJECT_NOT_FOUND")
        from modules.avm.domain import ValuationCase
        if not isinstance(row, ValuationCase) or row.case_id != case_id or row.valuation_input.store_id != row.store_id:
            raise HTTPException(503, "NATIVE_CASE_SOURCE_INVALID")
        self.store(row.store_id, hide=hide)
        return row

    def scenario(self, scenario_id, *, hide=True):
        row = self.raw.get_scenario(scenario_id)
        if row is None or row.tenant_id != self.principal.tenant_id:
            raise HTTPException(404, "NATIVE_OBJECT_NOT_FOUND")
        if not row.options_by_entity:
            self._decision(ResourceDescriptor(type="netplan", resource_id=scenario_id,
                tenant_id=self.principal.tenant_id, module="operator_console",
                data_classification=DataClassification.CONFIDENTIAL), hide=hide,
                valid=self.principal.scope.permits_assigned_area(None) and self.principal.scope.permits_heat_zone(None))
        for identity in row.options_by_entity:
            self.entity(identity, hide=hide)
        return row

    def outcome(self, value, *, hide=True):
        # Outcomes name a real report (older native contracts also accept caseId).
        matches = []
        for case in self.raw._store.list_all(self.raw._CASES):
            reports = self.raw._store.list_by_group(self.raw._REPORTS, case.case_id)
            if value.valuation_id == case.case_id or any(r.report_id == value.valuation_id for r in reports):
                matches.append(case)
        if len(matches) != 1 or matches[0].store_id != value.store_id:
            raise HTTPException(404, "NATIVE_OBJECT_NOT_FOUND")
        return self.case(matches[0].case_id, hide=hide)

    def pin_body(self, body):
        if body is None:
            return
        supplied = body.model_dump(exclude_unset=True)
        for name in ("created_by", "actor", "actor_id", "executed_by"):
            if name in supplied and supplied[name] != self.principal.subject_id:
                self.reject_identity("NATIVE_BODY_ACTOR_MISMATCH")
            if name in type(body).model_fields:
                # Existing optional 'system' defaults are not caller authority.
                setattr(body, name, self.principal.subject_id)
        if "tenant_id" in supplied and supplied["tenant_id"] != self.principal.tenant_id:
            self.reject_identity("NATIVE_BODY_TENANT_MISMATCH")
        rollback = getattr(body, "rollback_receipt", None)
        if rollback is not None and rollback.decider != self.principal.subject_id:
            # Body records can name only this authenticated decision-maker.
            # Server-configured historical receipts remain a separate trusted
            # input; this check creates no new approval or exception policy.
            self.reject_identity("NATIVE_BODY_ROLLBACK_DECIDER_MISMATCH")

    def reject_identity(self, code):
        access = AccessRequest(principal=self.principal, action=self.action,
            resource=ResourceDescriptor(type=self.factory.resource,
                resource_id=self.request.url.path, tenant_id=self.principal.tenant_id,
                module="avm" if self.factory.resource == "avm" else "operator_console",
                data_classification=DataClassification.CONFIDENTIAL),
            environment=Environment(attributes={"correlation_id": self.request.state.correlation_id}))
        decision = Decision.deny(code, policy_id="native.identity.pin")
        self.denial = (access, decision)
        self.factory.audit.record(build_security_event(access, decision))
        raise HTTPException(403, code)

    def admit_request(self, body):
        params = self.request.path_params
        if self.factory.resource == "avm":
            if params.get("case_id"):
                self.case(params["case_id"])
                self.admit_avm_source(params["case_id"])
            elif params.get("outcome_id"):
                value = self.raw.get_deal_outcome(params["outcome_id"])
                if value is None:
                    raise HTTPException(404, "NATIVE_OBJECT_NOT_FOUND")
                self.outcome(value)
            elif body is not None and hasattr(body, "valuation_id"):
                self.outcome(body, hide=False)
            elif body is not None and hasattr(body, "store_id"):
                self.store(body.store_id)
        else:
            if params.get("scenario_id"):
                self.scenario(params["scenario_id"])
            elif body is not None and getattr(body, "scenario_id", None):
                existing = self.raw.get_scenario(body.scenario_id)
                if existing is not None:
                    # An explicit create id cannot reset another object's
                    # scope, executed state or authentic approval history.
                    self.scenario(body.scenario_id)
                    raise HTTPException(409, "NATIVE_SCENARIO_ID_ALREADY_EXISTS")
            if body is not None:
                for row in getattr(body, "existing_stores", None) or []:
                    self.store(row["store_id"])
                for row in getattr(body, "candidate_sites", None) or []:
                    self.candidate(row["candidate_site_id"])

    def admit_avm_source(self, case_id):
        suffix = self.request.url.path.rstrip("/").rsplit("/", 1)[-1]
        commands = {"finance-approval": "approve", "dataroom": "build", "export": "export"}
        operation = commands.get(suffix) if self.request.method == "POST" else None
        formal_read = (self.formal or live_data_required()) and suffix in {"report", "reports", "dataroom"}
        if operation is None and not formal_read:
            return
        # Reuse the exact existing creator/finance/report provenance admission,
        # not a second interpretation of body names or historical CID events.
        from modules.opsboard.application.avm_dealroom import ScopedAVMDealRoom
        authority = ScopedAVMDealRoom(principal=self.principal, repository=self.raw,
            stores=self.factory.stores, brands=self.factory.brands,
            audit_log=self.factory.audit, queue=self.factory.queue,
            correlation_id=self.request.state.correlation_id,
            production_executor=self.factory.production_executor,
            depreciation_version_pin=self.factory.depreciation_version_pin,
            rollback_receipt=self.factory.rollback_receipt,
            force_production=self.formal or live_data_required())
        detail = authority.read(case_id)
        if detail["reportAvailability"]["state"] != "available":
            raise HTTPException(503, "AVM_PRODUCTION_REPORT_UNAVAILABLE")
        if operation == "approve" and not detail["creatorVerification"]["verified"]:
            raise HTTPException(503, "AVM_CREATOR_AUTHORITY_UNAVAILABLE")
        if operation in {"build", "export"} and not detail["financeVerification"]["verified"]:
            raise HTTPException(503, "AVM_FINANCE_AUTHORITY_UNAVAILABLE")
        if operation == "approve" and detail["case"]["created_by"] == self.principal.subject_id:
            raise HTTPException(403, "AVM_CREATOR_APPROVAL_FORBIDDEN")


class _NativeRepositoryView:
    """Preserve native methods; only collection reads filter admitted parents."""
    def __init__(self, owner):
        self.owner = owner

    def __getattr__(self, name):
        return getattr(self.owner.raw, name)

    def _visible(self, rows, admit):
        visible = []
        for row in rows:
            try:
                admit(row)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                continue
            visible.append(row)
        return visible

    def get_case(self, case_id):
        self.owner.case(case_id)
        return self.owner.raw.get_case(case_id)

    def list_cases(self):
        rows = self._visible(self.owner.raw._store.list_all(self.owner.raw._CASES),
                             lambda row: self.owner.case(row.case_id))
        return [self.owner.raw.get_case(row.case_id) for row in rows]

    def list_scenarios(self):
        return self._visible(self.owner.raw.list_scenarios(),
                             lambda row: self.owner.scenario(row.scenario_id))

    def list_deal_outcomes(self):
        return self._visible(self.owner.raw.list_deal_outcomes(), self.owner.outcome)
