"""Typed canonical identity commands; the controller is composed after its owner.

Actors come from the existing authenticated boundary. A command carries the
selected native case and observed resource versions, never a caller-selected
reviewer, inferred model confidence or an executable graph in a queue payload.
"""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from apps.api.app.routes.canonical_match_cases import _http_errors
from apps.api.oday_api.security.dependencies import principal_from_headers
from modules.listing.application.intake_authorization import (
    authorize_intake_action,
    mask_intake,
    native_intake_authorization_resource,
)
from modules.listing.domain.identity_graph import IdentityConflictError
from shared.audit.backlog import CanonicalAuditBacklog
from shared.audit.errors import CanonicalAuditUnavailable
from shared.audit.policy import build_security_event
from shared.auth import AccessRequest, Action, Decision, Environment, ResourceDescriptor
from shared.infrastructure.persistence.identity_decisions_sql import canonical_identity_plan

NativeVersion = Annotated[int, Field(ge=1, strict=True)]


class CandidateDispositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_site_id: UUID
    expected_version: NativeVersion
    disposition: Literal["KEEP_HISTORICAL", "REQUIRE_REVIEW", "REASSIGN"]
    target_property_id: UUID | None = None


class IdentityPlanVersions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Preserve raw keys until canonical_identity_plan validates UUID uniqueness.
    # Parsing UUID dictionary keys here would silently collapse two spellings
    # of the same identity before the native plan can reject that ambiguity.
    expected_property_versions: dict[str, NativeVersion]
    expected_listing_versions: dict[str, NativeVersion]
    expected_edge_versions: dict[str, NativeVersion]
    candidate_reassignment_plan: list[CandidateDispositionRequest] = Field(max_length=1000)


class ListingInputBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parser_job_id: UUID
    parser_run_id: UUID
    parser_run_version: NativeVersion
    source_snapshot_id: UUID
    original_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_correction_versions: dict[str, NativeVersion]


class ListingPlan(IdentityPlanVersions):
    expected_intake_version: NativeVersion
    input_binding: ListingInputBinding


class MergePlan(IdentityPlanVersions):
    source_property_ids: list[UUID] = Field(min_length=1, max_length=100)
    target_property_id: UUID


class SplitPartition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_property_id: UUID
    source_identity_edge_ids: list[UUID] = Field(min_length=1, max_length=1000)


class SplitPlan(IdentityPlanVersions):
    source_property_id: UUID
    partitions: list[SplitPartition] = Field(min_length=2, max_length=100)


class _ProposalReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=20, max_length=4000)
    risk_acknowledged: Literal[True]

    @field_validator("risk_acknowledged", mode="before")
    @classmethod
    def explicit_acknowledgement(cls, value):
        if value is not True:
            raise ValueError("An explicit boolean risk acknowledgement is required")
        return value


class MergeProposal(_ProposalReason):
    decision_type: Literal["MERGE"]
    plan: MergePlan


class SplitProposal(_ProposalReason):
    decision_type: Literal["SPLIT"]
    plan: SplitPlan


class ListingConfirmation(_ProposalReason):
    decision_type: Literal["CREATE", "REVISE"]
    reason: str = Field(min_length=3, max_length=4000)
    plan: ListingPlan


class DuplicateProposal(_ProposalReason):
    decision_type: Literal["DUPLICATE"]
    plan: ListingPlan


IdentityProposalRequest = Annotated[
    MergeProposal | SplitProposal | ListingConfirmation | DuplicateProposal, Field(discriminator="decision_type")]


class IdentityReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVE", "REJECT"]
    expected_case_version: NativeVersion
    reason: str = Field(min_length=3, max_length=4000)
    risk_acknowledged: StrictBool

    @model_validator(mode="after")
    def require_approval_acknowledgement(self):
        if self.decision == "APPROVE" and self.risk_acknowledged is not True:
            raise ValueError("Approval requires explicit risk acknowledgement")
        return self


class CanonicalIdentityReceipt(BaseModel):
    decision_id: UUID
    match_case_id: UUID
    match_case_version: NativeVersion
    status: Literal["PENDING_REVIEW", "APPROVED", "REJECTED", "EXECUTING", "EXECUTED", "FAILED"]
    version: NativeVersion
    job_id: UUID | None
    job_status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "RETRYING", "CANCELLED", "DEAD_LETTER"] | None = None
    audit_event_id: UUID
    review_audit_event_id: UUID | None = None
    correlation_id: UUID


class ExactIdentityAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version_id: str = Field(min_length=1, max_length=512)
    identity_policy_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    proposal_audit_event_id: UUID


class ExactIdentityPlan(ListingPlan):
    exact_authority: ExactIdentityAuthority


class CanonicalIdentitySummary(BaseModel):
    decision_id: UUID
    decision_type: Literal["MERGE", "SPLIT", "CREATE", "REVISE", "DUPLICATE"]
    status: Literal["DRAFT", "PENDING_REVIEW", "APPROVED", "REJECTED", "EXECUTING", "EXECUTED", "FAILED",
                    "REVERSAL_PENDING", "REVERSED", "SUPERSEDED"]
    version: NativeVersion
    match_case_id: UUID
    match_case_version: NativeVersion
    intake_id: UUID
    intake_version: NativeVersion
    proposer_subject_id: UUID | None
    proposer_service_principal: Literal["native-intake-identity"] | None = None
    reviewer_subject_id: UUID | None
    plan: MergePlan | SplitPlan | ListingPlan | ExactIdentityPlan
    job_id: UUID | None
    job_status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "RETRYING", "CANCELLED", "DEAD_LETTER"] | None
    job_version: NativeVersion | None

    @model_validator(mode="after")
    def recorded_actor(self):
        if self.proposer_service_principal is not None:
            if (self.decision_type != "DUPLICATE" or self.proposer_subject_id is not None
                    or self.reviewer_subject_id is not None or not isinstance(self.plan, ExactIdentityPlan)):
                raise ValueError("The exact decision must retain its real service proposer")
        elif self.proposer_subject_id is None or isinstance(self.plan, ExactIdentityPlan):
            raise ValueError("A human decision must retain its real human proposer")
        elif self.decision_type == "DUPLICATE" and self.status not in {"DRAFT", "PENDING_REVIEW"}:
            if self.reviewer_subject_id is None or self.reviewer_subject_id == self.proposer_subject_id:
                raise ValueError("A human duplicate decision requires its actual independent reviewer")
        return self


class IdentityPlanningAnchor(BaseModel):
    match_candidate_id: UUID
    effective_property_id: UUID
    listing_id: UUID | None
    listing_version: NativeVersion | None


class IdentityPlanningProperty(BaseModel):
    property_id: UUID
    version: NativeVersion
    status: str


class IdentityPlanningListing(BaseModel):
    listing_id: UUID
    property_id: UUID | None
    version: NativeVersion
    lifecycle_state: str


class IdentityPlanningEdge(BaseModel):
    edge_id: UUID
    property_id: UUID
    listing_id: UUID | None
    edge_version: NativeVersion


class IdentityPlanningCandidate(BaseModel):
    candidate_site_id: UUID
    property_id: UUID
    version: NativeVersion
    status: str
    target_format_code: str


class CanonicalListingInputField(BaseModel):
    correction_id: UUID
    field_path: str
    classification: str
    status: Literal["PROPOSED", "APPLIED", "REJECTED", "SUPERSEDED", "REVERSED"]
    version: NativeVersion
    identity_affecting: bool
    proposed_by: UUID
    parsed: str | int | float | None
    normalized: str | int | float | None
    corrected: str | int | float | None
    masked: bool = False
    mask_reason_code: Literal["FIELD_MASKED"] | None = None


class CanonicalListingInputReference(BaseModel):
    parser_job_id: UUID
    parser_run_id: UUID
    parser_run_version: NativeVersion
    source_snapshot_id: UUID
    original_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposed_correction_ids: list[UUID]
    manual_entry_valid_until: datetime
    fields: list[CanonicalListingInputField] = Field(max_length=100)


class CanonicalIdentityPlanningContext(BaseModel):
    match_case_id: UUID
    match_case_version: NativeVersion
    intake_id: UUID
    intake_version: NativeVersion
    anchors: list[IdentityPlanningAnchor] = Field(max_length=100)
    properties: list[IdentityPlanningProperty] = Field(max_length=100)
    listings: list[IdentityPlanningListing] = Field(max_length=1000)
    source_identity_edges: list[IdentityPlanningEdge] = Field(max_length=1000)
    candidate_sites: list[IdentityPlanningCandidate] = Field(max_length=1000)
    listing_input: CanonicalListingInputReference | None = None


class IdentityJobCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=4000)

    @field_validator("reason")
    @classmethod
    def require_reason(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("A cancellation reason is required")
        return value.strip()


class CanonicalIdentityJobSummary(BaseModel):
    job_id: UUID
    decision_id: UUID
    match_case_id: UUID
    intake_id: UUID
    version: NativeVersion
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "RETRYING", "CANCELLED", "DEAD_LETTER"]
    checkpoint: str
    attempt: Annotated[int, Field(ge=0, strict=True)]
    max_attempts: NativeVersion
    timeout_at: datetime
    cancellation_requested_at: datetime | None
    correlation_id: UUID


class CanonicalIdentityJobReceipt(CanonicalIdentityJobSummary):
    audit_event_id: UUID


KeyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128,
                                  pattern=r"^[A-Za-z0-9._:-]+$")]
VersionHeader = Annotated[str, Header(alias="If-Match", pattern=r'^W/"[1-9][0-9]*"$')]


@contextmanager
def _identity_errors(request):
    from modules.listing.application.identity_delivery import IdentityDeliveryUnavailable
    from modules.listing.application.native_intake_requester_authority import (
        NativeIntakeRequesterAuthorityUnavailable,
    )
    from modules.listing.domain.intake_states import DenialCode, DomainValidationError

    sink = getattr(request.app.state, "audit_log", None)
    denials = []

    class AccessAudit:
        def record(self, event):
            if event.outcome == "deny":
                denials.append(event)
                return event
            try:
                return sink.record(event)
            except Exception as exc:
                raise CanonicalAuditUnavailable("CANONICAL_IDENTITY_AUTH_AUDIT_UNAVAILABLE") from exc

    request.state.canonical_identity_access_audit = AccessAudit()
    try:
        try:
            with _http_errors():
                yield
        except IdentityDeliveryUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except NativeIntakeRequesterAuthorityUnavailable as exc:
            raise HTTPException(503, "CANONICAL_IDENTITY_CURRENT_AUTHORITY_UNAVAILABLE") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except DomainValidationError as exc:
            status = 403 if exc.code in {DenialCode.ROLE_DENIED, DenialCode.TENANT_SCOPE_DENIED, DenialCode.SELF_REVIEW_DENIED} else 409
            raise HTTPException(status, exc.code.value) from exc
    except HTTPException as exc:
        principal = getattr(request.state, "canonical_identity_principal", None)
        if exc.status_code in {401, 403} and not denials and principal is not None:
            # Context and segregation denials occur outside the matrix helper.
            identity = request.path_params.get("decision_id") or request.path_params.get("match_case_id")
            action = Action.VIEW if request.method in {"GET", "HEAD"} else Action.UPDATE
            kind = "identity_decision" if "decision_id" in request.path_params else "match_case"
            denials.append(build_security_event(AccessRequest(principal, action,
                ResourceDescriptor(type=kind, resource_id=str(identity), tenant_id=principal.tenant_id),
                Environment(source_ip=request.client.host if request.client else None,
                    attributes={"correlation_id": request.headers.get("x-correlation-id") or "unknown"})),
                Decision.deny(str(exc.detail), policy_id="canonical_identity")))
        raise
    finally:
        # The graph unit has now committed/rolled back. A refused command's
        # audit must not disappear in the business rollback that it caused.
        del request.state.canonical_identity_access_audit
        for event in denials:
            try:
                sink.record(event)
            except Exception:
                # Match the existing security boundary: audit failure remains
                # observable and can never turn a refusal into an allow.
                logging.getLogger(__name__).exception("Canonical identity denial audit failed")


def create_canonical_identity_decisions_router(store) -> APIRouter:
    """Mount alongside the canonical case router using its actual store owner."""
    router = APIRouter()
    errors = {code: {"description": description} for code, description in (
        (401, "Authentication required"), (403, "Current actor, segregation or resource scope denied"),
        (404, "Canonical Intake, case, decision or affected resource not found"),
        (409, "Native version, custody, plan or idempotency conflict"),
        (422, "Invalid request headers, path, body or identity plan"),
        (503, "Canonical storage, execution configuration or audit unavailable"),
    )}

    def context(request):
        principal = principal_from_headers(request.headers)
        request.state.canonical_identity_principal = principal
        if not principal.authenticated:
            raise HTTPException(401, "AUTHENTICATION_REQUIRED")
        try:
            UUID(principal.subject_id)
            UUID(principal.tenant_id)
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(403, "CANONICAL_ACTOR_AND_TENANT_REQUIRED") from None
        bundle = getattr(request.app.state, "persistence_bundle", None)
        if (bundle is None or getattr(bundle, "engine", None) is None
                or getattr(bundle.engine, "dialect", None) != "postgresql"
                or getattr(bundle, "assisted_intake_store", None) is not store
                or getattr(store, "_engine", None) is not bundle.engine):
            raise HTTPException(503, "CANONICAL_IDENTITY_STORAGE_UNAVAILABLE")
        if getattr(request.app.state, "audit_log", None) is None:
            raise HTTPException(503, "CANONICAL_IDENTITY_AUTH_AUDIT_UNAVAILABLE")
        try:
            return principal, bundle.canonical_identity_decisions()
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(503, "CANONICAL_IDENTITY_STORAGE_UNAVAILABLE") from exc

    def authorize(request, principal, repo, row, action="view", *, body=None):
        authorize_intake_action(principal, action,
            resource=native_intake_authorization_resource(row, graph=repo.graph),
            risk_acknowledged=getattr(body, "risk_acknowledged", False),
            risk_summary=body.reason if body else None,
            audit_log=request.state.canonical_identity_access_audit,
            correlation_id=request.headers.get("x-correlation-id"))

    def authorize_resources(request, principal, repo, plan):
        # Verify each native resource's own scope; the case's region/brand may
        # never manufacture a grant to an unrelated graph or candidate.
        for query, expected in (
            ("SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=?", "expected_property_versions"),
            ("SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=?", "expected_listing_versions"),
        ):
            for identity in sorted(plan[expected]):
                row = repo.graph._one(query, (principal.tenant_id, identity))
                authorize(request, principal, repo, row)
        for item in sorted(plan["candidate_reassignment_plan"], key=lambda row: row["candidate_site_id"]):
            row = repo.graph._one("SELECT * FROM expansion.candidate_sites WHERE tenant_id=? AND candidate_site_id=?",
                (principal.tenant_id, item["candidate_site_id"]))
            authorize(request, principal, repo, row)

    def recorded_plan(decision):
        try:
            return canonical_identity_plan(decision["decision_type"], decision["after_graph"]["plan"])
        except (ValueError, KeyError, TypeError) as exc:
            raise IdentityConflictError("CANONICAL_IDENTITY_PLAN_UNAVAILABLE") from exc

    def command(request, response, principal, repo, key, if_match, resource_id, operation, body, permission, make,
                receipt_model=CanonicalIdentityReceipt):
        tenant, actor = principal.tenant_id, principal.subject_id
        body_json = body.model_dump(mode="json")
        digest = hashlib.sha256(json.dumps({"body": body_json, "if_match": if_match},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        replay_key = f"{tenant}:{actor}:{operation}:{resource_id}:{key}"
        with repo.graph.transaction(tenant):
            store.refresh(tenant)
            with store.transaction():
                permission()
                prior = store.replays.get(replay_key)
                if prior is not None:
                    if prior[0] != digest:
                        raise HTTPException(409, "IDEMPOTENCY_KEY_REUSED")
                    receipt = prior[1]
                    response.headers["Idempotency-Replayed"] = "true"
                else:
                    CanonicalAuditBacklog(repo.engine).require_available(tenant)
                    receipt = receipt_model.model_validate(make(int(if_match[3:-1]))).model_dump(mode="json")
                    store.replays[replay_key] = (digest, receipt, 201 if operation == "proposeCanonicalIdentity" else 200)
                    store.flush()
                response.headers["ETag"] = f'W/"{receipt["version"]}"'
        return receipt

    @router.get("/match-cases/{match_case_id}/identity-context", operation_id="getCanonicalIdentityPlanningContext",
                response_model=CanonicalIdentityPlanningContext, response_model_exclude_unset=True, responses=errors)
    def planning_context(match_case_id: UUID, request: Request, response: Response,
                         include_listing_input: bool = False) -> dict:
        with _identity_errors(request):
            principal, repo = context(request)
            with repo.graph.transaction(principal.tenant_id):
                case, parent = repo.graph._case(principal.tenant_id, str(match_case_id))
                authorize(request, principal, repo, parent)
                selected = repo.planning_context(principal.tenant_id, str(match_case_id),
                    expected_case_version=case["version"], include_listing_input=include_listing_input)
                seen_listings = set()
                for kind in ("properties", "listings", "anchor_listings", "candidate_sites"):
                    for row in selected[kind]:
                        if kind in {"listings", "anchor_listings"}:
                            if row["listing_id"] in seen_listings:
                                continue
                            seen_listings.add(row["listing_id"])
                        authorize(request, principal, repo, row)
                response.headers["ETag"] = f'W/"{case["version"]}"'
                # Authorize the entire closure before projecting any values.
                # Reuse the canonical FieldValue classification mask; no raw
                # payload or effective/applied value is inferred from a proposal.
                listing_input = selected.get("listing_input")
                if listing_input is not None:
                    listing_input = {**listing_input, "fields": mask_intake(principal,
                        {"fields": listing_input["fields"]})["fields"]}
                return {"match_case_id": case["match_case_id"], "match_case_version": case["version"],
                    "intake_id": parent["intake_id"], "intake_version": parent["version"],
                    "anchors": selected["anchors"],
                    "properties": [{key: row[key] for key in ("property_id", "version", "status")}
                                   for row in selected["properties"]],
                    "listings": [{key: row[key] for key in ("listing_id", "property_id", "version", "lifecycle_state")}
                                 for row in selected["listings"]],
                    "source_identity_edges": selected["source_identity_edges"],
                    "candidate_sites": [{key: row[key] for key in ("candidate_site_id", "property_id", "version", "status", "target_format_code")}
                                        for row in selected["candidate_sites"]],
                    **({"listing_input": listing_input} if listing_input is not None else {})}

    @router.post("/match-cases/{match_case_id}/identity-decisions", operation_id="proposeCanonicalIdentity",
                 response_model=CanonicalIdentityReceipt, status_code=201, responses=errors)
    def propose(match_case_id: UUID, body: IdentityProposalRequest, request: Request, response: Response,
                key: KeyHeader, if_match: VersionHeader) -> dict:
        with _identity_errors(request):
            principal, repo = context(request)
            plan = canonical_identity_plan(body.decision_type, body.plan.model_dump(mode="json"))

            def permission():
                case, parent = repo.graph._case(principal.tenant_id, str(match_case_id))
                current = principal
                if body.decision_type in {"CREATE", "REVISE", "DUPLICATE"}:
                    current = repo.confirmation_principal(case, parent, kind=body.decision_type,
                        actor_id=principal.subject_id, reason=body.reason)
                action = "decide" if body.decision_type in {"CREATE", "REVISE", "DUPLICATE"} else body.decision_type.lower()
                authorize(request, current, repo, parent, action, body=body)
                authorize_resources(request, current, repo, plan)

            def make(expected):
                result = repo.propose(principal.tenant_id, str(match_case_id), kind=body.decision_type,
                    plan=plan, expected_case_version=expected, actor_id=principal.subject_id,
                    reason=body.reason, risk_acknowledged=body.risk_acknowledged)
                if body.decision_type in {"CREATE", "REVISE"}:
                    from modules.listing.application.identity_delivery import IdentityJobAdmission

                    admission = IdentityJobAdmission(repo.graph).enqueue(principal.tenant_id, result["decision_id"],
                        expected_version=result["version"], actor_id=principal.subject_id)
                    result = {**result, **admission, "review_audit_event_id": result["audit_event_id"]}
                return result

            return command(request, response, principal, repo, key, if_match, str(match_case_id),
                "proposeCanonicalIdentity", body, permission, make)

    @router.post("/canonical-identity-decisions/{decision_id}/actions/review", operation_id="reviewCanonicalIdentity",
                 response_model=CanonicalIdentityReceipt, responses=errors)
    def review(decision_id: UUID, body: IdentityReviewRequest, request: Request, response: Response,
               key: KeyHeader, if_match: VersionHeader) -> dict:
        from modules.listing.application.identity_delivery import IdentityJobAdmission

        with _identity_errors(request):
            principal, repo = context(request)

            def permission():
                selected = repo.get(principal.tenant_id, str(decision_id))
                decision = selected["decision"]
                current = principal
                if decision["decision_type"] == "DUPLICATE":
                    current = repo.confirmation_principal(selected["match_case"], selected["intake"], kind="DUPLICATE",
                        actor_id=principal.subject_id, proposer_id=decision["proposer_subject_id"], reason=body.reason,
                        approve=body.decision == "APPROVE")
                action = "reject_identity" if body.decision == "REJECT" else decision["decision_type"].lower()
                if body.decision == "APPROVE" and decision["decision_type"] == "DUPLICATE":
                    action = "decide"
                authorize(request, current, repo, selected["intake"], action, body=body)
                if decision["proposer_subject_id"] == principal.subject_id:
                    raise HTTPException(403, "SELF_REVIEW_DENIED")
                authorize_resources(request, current, repo, recorded_plan(decision))

            def make(expected):
                result = repo.review(principal.tenant_id, str(decision_id), expected_decision_version=expected,
                    expected_case_version=body.expected_case_version, reviewer_id=principal.subject_id,
                    approve=body.decision == "APPROVE", reason=body.reason, risk_acknowledged=body.risk_acknowledged)
                if body.decision == "APPROVE":
                    admission = IdentityJobAdmission(repo.graph).enqueue(principal.tenant_id, str(decision_id),
                        expected_version=result["version"], actor_id=principal.subject_id)
                    result = {**result, **admission, "review_audit_event_id": result["audit_event_id"]}
                return result

            return command(request, response, principal, repo, key, if_match, str(decision_id),
                "reviewCanonicalIdentity", body, permission, make)

    @router.get("/canonical-identity-decisions/{decision_id}", operation_id="getCanonicalIdentityDecision",
                response_model=CanonicalIdentitySummary, responses=errors)
    def read(decision_id: UUID, request: Request, response: Response) -> dict:
        with _identity_errors(request):
            principal, repo = context(request)
            with repo.graph.transaction(principal.tenant_id):
                selected = repo.get(principal.tenant_id, str(decision_id))
                decision, case, parent = selected["decision"], selected["match_case"], selected["intake"]
                authorize(request, principal, repo, parent)
                plan = recorded_plan(decision)
                authorize_resources(request, principal, repo, plan)
                job = repo.engine.query_one("SELECT job_id,status,version FROM workflow.jobs WHERE tenant_id=? "
                    "AND job_type='identity-execution' AND aggregate_type='identity_decision' AND aggregate_id=?",
                    (principal.tenant_id, str(decision_id)))
                response.headers["ETag"] = f'W/"{decision["version"]}"'
                return {"decision_id": decision["match_decision_id"], "decision_type": decision["decision_type"],
                    "status": decision["status"], "version": decision["version"], "match_case_id": case["match_case_id"],
                    "match_case_version": case["version"], "intake_id": parent["intake_id"], "intake_version": parent["version"],
                    "proposer_subject_id": decision["proposer_subject_id"], "reviewer_subject_id": decision["reviewer_subject_id"],
                    "proposer_service_principal": decision.get("proposer_service_principal"),
                    "plan": plan, "job_id": job["job_id"] if job else None,
                    "job_status": job["status"] if job else None, "job_version": job["version"] if job else None}

    @router.get("/canonical-identity-decisions/{decision_id}/job", operation_id="getCanonicalIdentityJob",
                response_model=CanonicalIdentityJobSummary, responses=errors)
    def read_job(decision_id: UUID, request: Request, response: Response) -> dict:
        from modules.listing.application.identity_job_controls import IdentityJobControls

        with _identity_errors(request):
            principal, repo = context(request)
            with repo.graph.transaction(principal.tenant_id):
                controls = IdentityJobControls(repo.graph)
                selected = controls.context(principal.tenant_id, str(decision_id))
                authorize(request, principal, repo, selected["intake"])
                response.headers["ETag"] = f'W/"{selected["job"]["version"]}"'
                return controls.summary(selected)

    @router.post("/canonical-identity-decisions/{decision_id}/actions/cancel-job",
                 operation_id="cancelCanonicalIdentityJob", response_model=CanonicalIdentityJobReceipt, responses=errors)
    def cancel_job(decision_id: UUID, body: IdentityJobCancelRequest, request: Request, response: Response,
                   key: KeyHeader, if_match: VersionHeader) -> dict:
        from modules.listing.application.identity_job_controls import IdentityJobControls

        with _identity_errors(request):
            principal, repo = context(request)
            controls = IdentityJobControls(repo.graph)

            def permission():
                selected = controls.context(principal.tenant_id, str(decision_id))
                # An accepted identity decision requires the existing manager/
                # steward authority. Own staff may stop unaccepted work.
                action = "reject_identity" if "delivery_fence" in selected["job"]["payload"] else "cancel"
                authorize(request, principal, repo, selected["intake"], action, body=body)

            return command(request, response, principal, repo, key, if_match, str(decision_id),
                "cancelCanonicalIdentityJob", body, permission,
                lambda expected: controls.cancel(principal.tenant_id, str(decision_id), expected_version=expected,
                    actor_id=principal.subject_id, reason=body.reason), receipt_model=CanonicalIdentityJobReceipt)

    return router
