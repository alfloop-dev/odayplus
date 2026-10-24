"""Bind field tasks to the actual application bundle and current parent owners."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import Any

from modules.market_survey.application.assignment_owner import (
    CanonicalSurveyAssignmentService,
    SurveyAssignmentForbidden,
    SurveyAssignmentNotFound,
)
from modules.market_survey.infrastructure.assignment_repository import (
    DurableSurveyAssignmentRepository,
    SurveyOwnerUnavailable,
)
from modules.opsboard.application.network_listings import NetworkListingService
from modules.opsboard.application.network_read_scope import (
    candidate_scope_evidence,
    record_in_scope,
)
from shared.audit.policy import build_security_event
from shared.auth import (
    AccessRequest,
    Action,
    DataClassification,
    Decision,
    Environment,
    Principal,
    ResourceDescriptor,
    rbac_allows,
)
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.infrastructure.persistence.repositories import (
    DurableHeatZoneResultStore,
    DurableListingRepository,
)


class CanonicalSurveyFactory:
    def __init__(self, bundle: Any, *, audit_log: Any, queue: Any, object_store: Any = None, content_bucket: str | None = None,
                 content_credentials: Any = None):
        self.bundle, self.audit_log, self.queue = bundle, audit_log, queue
        self.engine = getattr(bundle, "engine", None)
        self.object_store, self.content_bucket = object_store, content_bucket
        from modules.market_survey.application.assignment_content import SurveyWorkloadCredentials
        self.content_credentials = content_credentials or SurveyWorkloadCredentials()

    def _hidden(self, principal: Principal, kind: str, identity: str,
                action: Action = Action.VIEW):
        exc = SurveyAssignmentNotFound("SURVEY_TARGET_NOT_FOUND")
        exc.audit_event = build_security_event(AccessRequest(principal=principal, action=action,
            resource=ResourceDescriptor(type="heatzone" if kind == "HEAT_ZONE" else "listing",
                resource_id=identity, tenant_id=principal.tenant_id, module="operator_console"),
            environment=Environment(attributes={"correlation_id": self.correlation_id})),
            Decision.deny("SURVEY_CURRENT_TARGET_DENIED", policy_id="survey.parent_scope"))
        return exc

    def require_principal(self, principal: Any, action: Action) -> None:
        if (not isinstance(principal, Principal) or not principal.authenticated
                or not isinstance(principal.subject_id, str) or not principal.subject_id.strip()
                or not principal.tenant_id or not principal.scope.permits_module("operator_console")
                or not principal.scope.permits_classification(DataClassification.CONFIDENTIAL)
                or not (rbac_allows(principal, "listing", action)
                        or action == Action.VIEW and rbac_allows(principal, "operator_network", Action.VIEW))):
            raise SurveyAssignmentForbidden("SURVEY_ASSIGNMENT_PERMISSION_DENIED")

    def _owner(self, attribute: str, tenant: str):
        if self.engine is None:
            raise SurveyOwnerUnavailable("SURVEY_DURABLE_SQL_OWNER_REQUIRED")
        configured = getattr(self.bundle, attribute, None)
        bound_tenant = str(getattr(configured, "tenant_id", "")
                           or getattr(getattr(configured, "_store", None), "tenant_id", "") or "")
        if bound_tenant and bound_tenant != tenant:
            raise SurveyOwnerUnavailable("SURVEY_PARENT_TENANT_REBIND_DENIED")
        provider = getattr(self.bundle, attribute + "_for_tenant", None)
        owner = provider(tenant) if callable(provider) else None
        store = getattr(owner, "_store", None)
        if (not isinstance(store, TenantScopedDocumentStore)
                or store.tenant_id != tenant or store.engine is not self.engine):
            raise SurveyOwnerUnavailable("SURVEY_PARENT_SQL_OWNER_UNAVAILABLE")
        return owner

    def _listing_owner(self, tenant: str) -> DurableListingRepository:
        owner = self._owner("listing_repository", tenant)
        if not isinstance(owner, DurableListingRepository):
            raise SurveyOwnerUnavailable("SURVEY_LISTING_OWNER_UNAVAILABLE")
        return owner

    def read(self, principal: Principal, target_kind: str, target_id: str,
             action: Action) -> dict[str, Any]:
        self.require_principal(principal, action)
        if not isinstance(target_id, str) or not target_id.strip():
            raise ValueError("SURVEY_TARGET_REQUIRED")
        tenant = principal.tenant_id
        if target_kind == "CANDIDATE_SITE":
            owner = self._listing_owner(tenant)
            metadata = self._owner("operator_intake_repository", tenant)
            if not isinstance(metadata, DurableAssistedIntakeRepository):
                raise SurveyOwnerUnavailable("SURVEY_PARENT_METADATA_OWNER_UNAVAILABLE")
            drafts = [row for row in owner.list_candidates()
                      if row.candidate_site.candidate_site_id == target_id]
            if len(drafts) != 1:
                raise self._hidden(principal, target_kind, target_id, action)
            draft = drafts[0]
            candidate = draft.candidate_site
            listing = owner.get_listing(candidate.listing_id or "")
            address = owner.get_address(candidate.address_id)
            if (listing is None or address is None or listing.tenant_id != tenant or address.tenant_id != tenant
                    or candidate.listing_id != draft.listing.listing_id
                    or candidate.address_id != draft.address.address_id
                    or listing.address_id != candidate.address_id):
                raise self._hidden(principal, target_kind, target_id, action)
            # Reuse the canonical listing/candidate projection, not a UI row or
            # an independently denormalized copy of candidate scope.
            snapshot = NetworkListingService(listing_repository=owner, intake_repository=metadata,
                seed_fixtures=False, tenant_id=tenant).snapshot(tenant_id=tenant)
            matches = [row for row in snapshot["candidates"] if row.get("id") == target_id]
            if len(matches) != 1:
                raise self._hidden(principal, target_kind, target_id, action)
            evidence = candidate_scope_evidence(principal, snapshot).get(target_id)
            if evidence is None:
                raise self._hidden(principal, target_kind, target_id, action)
            return evidence
        if target_kind == "HEAT_ZONE":
            owner = self._owner("heatzone_store", tenant)
            if not isinstance(owner, DurableHeatZoneResultStore):
                raise SurveyOwnerUnavailable("SURVEY_HEATZONE_OWNER_UNAVAILABLE")
            rows = [row for row in owner.list_scores() if row.get("heat_zone_id") == target_id]
            if len(rows) != 1:
                raise self._hidden(principal, target_kind, target_id, action)
            # Tenant comes from the actual partition. Unprovided restricted
            # axes remain absent and therefore fail record_in_scope; a city
            # label or H3 string is never guessed to be an assigned area.
            evidence = {"tenantId": tenant, **rows[0], "heatZoneId": target_id}
            if (not rbac_allows(principal, "heatzone", Action.VIEW)
                    or not record_in_scope(principal, evidence)):
                raise self._hidden(principal, target_kind, target_id, action)
            return evidence
        raise ValueError("SURVEY_TARGET_KIND_UNSUPPORTED")

    def assignee(self, principal: Principal, kind: str, identity: str,
                 assigned_to: str, action: Action = Action.CREATE) -> dict[str, str]:
        owner = getattr(self.bundle, "identity_store", None)
        if owner is None:
            raise SurveyOwnerUnavailable("SURVEY_IDENTITY_OWNER_UNAVAILABLE")
        try:
            account = owner.find_account_by_id(assigned_to)
            if (account is None or not account.is_active
                    or str(account.tenant_id) != principal.tenant_id):
                raise SurveyAssignmentForbidden("SURVEY_ASSIGNEE_UNAVAILABLE")
            subject = Principal(subject_id=str(account.account_id),
                roles=owner.get_account_roles(account.account_id),
                scope=owner.get_account_scope(account.account_id, require_explicit=True))
            self.read(subject, kind, identity, Action.VIEW)
        except (ValueError, SurveyAssignmentNotFound, SurveyAssignmentForbidden) as exc:
            denied = SurveyAssignmentForbidden("SURVEY_ASSIGNEE_UNAVAILABLE")
            denied.audit_event = self._hidden(principal, kind, identity, action).audit_event
            raise denied from exc
        return {"subjectId": str(account.account_id), "displayName": account.display_name or account.username}

    def assignees(self, principal: Principal, kind: str, identity: str) -> list[dict[str, str]]:
        owner = getattr(self.bundle, "identity_store", None)
        if owner is None:
            raise SurveyOwnerUnavailable("SURVEY_IDENTITY_OWNER_UNAVAILABLE")
        accounts = owner.list_scoped_active_accounts(principal.tenant_id)
        result = []
        for account in accounts:
            try:
                result.append(self.assignee(principal, kind, identity, str(account.account_id)))
            except SurveyAssignmentForbidden:
                continue
        return result

    def _lock_document(self, store: Any, collection: str, identity: str) -> None:
        if getattr(self.engine, "dialect", "") == "postgresql":
            self.engine.query_one(
                "SELECT doc_id FROM durable_documents WHERE collection = ? AND doc_id = ? FOR UPDATE",
                (store._collection(collection), identity))

    @contextmanager
    def guard(self, principal: Principal, kind: str, identity: str, action: Action = Action.VIEW):
        tenant = principal.tenant_id
        with self.engine.lock, self.engine.transaction(), ExitStack() as stack:
            if getattr(self.engine, "dialect", "") != "postgresql" and not self.engine._conn.in_transaction:
                self.engine.execute("BEGIN IMMEDIATE")
            if kind == "CANDIDATE_SITE":
                owner = self._listing_owner(tenant)
                stack.enter_context(owner.candidate_guard(identity))
                metadata = self._owner("operator_intake_repository", tenant)
                stack.enter_context(metadata.candidate_metadata_guard(identity))
                rows = [row for row in owner.list_candidates()
                        if row.candidate_site.candidate_site_id == identity]
                if len(rows) != 1:
                    raise self._hidden(principal, kind, identity, action)
                candidate = rows[0].candidate_site
                if getattr(self.engine, "dialect", "") == "postgresql":
                    self.engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                        (f"{metadata._store._collection(metadata._LISTING_META)}:{candidate.listing_id}",))
                # The native candidate fence pins its real listing/address
                # references. Lock those physical parent rows before admission.
                for collection, key in sorted([(owner._LISTINGS, candidate.listing_id or ""),
                                               (owner._ADDRESSES, candidate.address_id)]):
                    self._lock_document(owner._store, collection, key)
            elif kind == "HEAT_ZONE":
                owner = self._owner("heatzone_store", tenant)
                self._lock_document(owner._store, owner._META, owner._LATEST)
                latest = owner._store.get(owner._META, owner._LATEST)
                if latest is None:
                    raise self._hidden(principal, kind, identity, action)
                self._lock_document(owner._store, owner._JOBS, latest)
            else:
                raise ValueError("SURVEY_TARGET_KIND_UNSUPPORTED")
            yield

    def __call__(self, principal: Principal, *, correlation_id: str) -> CanonicalSurveyAssignmentService:
        self.require_principal(principal, Action.VIEW)
        provider = getattr(self.bundle, "survey_assignment_repository_for_tenant", None)
        repository = provider(principal.tenant_id) if callable(provider) else None
        if not isinstance(repository, DurableSurveyAssignmentRepository):
            raise SurveyOwnerUnavailable("SURVEY_ASSIGNMENT_OWNER_UNAVAILABLE")
        if not isinstance(correlation_id, str) or not correlation_id.strip():
            raise SurveyOwnerUnavailable("SURVEY_CORRELATION_REQUIRED")
        context = type(self)(self.bundle, audit_log=self.audit_log, queue=self.queue,
                             object_store=self.object_store, content_bucket=self.content_bucket,
                             content_credentials=self.content_credentials)
        context.correlation_id = correlation_id
        return CanonicalSurveyAssignmentService(repository=repository,
            audit_log=self.audit_log, queue=self.queue, parents=context)


    def content(self):
        from modules.market_survey.application.assignment_content import VerifiedSurveyObjectContent
        return VerifiedSurveyObjectContent.configured(store=self.object_store, bucket=self.content_bucket,
                                                      credentials=self.content_credentials)
