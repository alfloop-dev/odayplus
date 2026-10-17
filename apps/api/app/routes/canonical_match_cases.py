"""Create/select the native Intake anchor before proposing identity changes."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from apps.api.oday_api.security.dependencies import principal_from_headers
from modules.listing.application.intake_authorization import authorize_intake_action
from modules.listing.domain.identity_graph import IdentityConflictError, IdentityNotFoundError
from shared.audit.backlog import AuditBacklogBlocked, AuditBacklogUnavailable, CanonicalAuditBacklog
from shared.audit.errors import CanonicalAuditUnavailable
from shared.infrastructure.persistence.assisted_listing_intake import (
    AssistedIntakePersistenceConflict,
)
from shared.infrastructure.persistence.identity_cases_sql import MatchCandidateEvidence


class MatchEvidenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: UUID
    property_version: int = Field(ge=1, strict=True)
    confidence: Decimal = Field(ge=0, le=1, decimal_places=4)
    evidence: dict[str, Any]
    listing_id: UUID | None = None
    listing_version: int | None = Field(default=None, ge=1, strict=True)


class CreateMatchCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confidence: Decimal = Field(ge=0, le=1, decimal_places=4)
    candidates: list[MatchEvidenceCandidate] = Field(min_length=1, max_length=100)
    supporting_signals: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    contradictory_signals: list[dict[str, Any]] = Field(max_length=100)
    reason: str = Field(min_length=3, max_length=4000)
    risk_acknowledged: Literal[True]


class MatchCaseReceipt(BaseModel):
    match_case_id: UUID
    intake_id: UUID
    status: Literal["PENDING_REVIEW"]
    version: int
    intake_version: int
    audit_event_id: UUID
    correlation_id: UUID
    candidate_ids: list[UUID]


class MatchCandidateSummary(BaseModel):
    match_candidate_id: UUID
    property_id: UUID | None
    listing_id: UUID | None
    rank: int
    confidence: Decimal
    observed_property_version: int | None
    observed_listing_version: int | None


class MatchCaseSummary(BaseModel):
    match_case_id: UUID
    intake_id: UUID
    status: str
    outcome: str
    version: int
    intake_version: int
    candidates: list[MatchCandidateSummary]


@contextmanager
def _http_errors():
    try:
        yield
    except IdentityNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (IdentityConflictError, AssistedIntakePersistenceConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (CanonicalAuditUnavailable, AuditBacklogBlocked, AuditBacklogUnavailable) as exc:
        raise HTTPException(503, "CANONICAL_AUDIT_UNAVAILABLE") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        if getattr(exc, "sqlstate", None) in {"40001", "40P01"}:
            raise HTTPException(409, "CANONICAL_MATCH_CASE_CONCURRENT_CHANGE") from exc
        raise


def create_canonical_match_case_router(store: Any) -> APIRouter:
    router = APIRouter(tags=["assisted-listing-intake"])

    def context(request):
        principal = principal_from_headers(request.headers)
        if not principal.authenticated:
            raise HTTPException(401, "principal not authenticated")
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
            raise HTTPException(503, "CANONICAL_MATCH_CASE_STORAGE_UNAVAILABLE")
        try:
            return principal, bundle.canonical_match_cases()
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(503, "CANONICAL_MATCH_CASE_STORAGE_UNAVAILABLE") from exc

    def authorize(request, principal, row, *, action="view", body=None):
        authorize_intake_action(principal, action,
            resource={**row, "submitter": row.get("submitter_subject_id")},
            risk_acknowledged=body.risk_acknowledged if body else False,
            risk_summary=body.reason if body else None,
            audit_log=getattr(request.app.state, "audit_log", None),
            correlation_id=request.headers.get("x-correlation-id"))

    def authorize_candidates(request, principal, repo, candidates):
        # Do not inherit the Intake's region/brand grants for an unrelated
        # property. Missing native scope axes fail closed for restricted users.
        for candidate in candidates:
            property_id = candidate["property_id"]
            if candidate["listing_id"] is not None:
                listing = repo.graph._one(
                    "SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=?",
                    (principal.tenant_id, str(candidate["listing_id"])),
                )
                authorize(request, principal, listing)
                if property_id is None:
                    property_id = repo.graph.resolve_listing(principal.tenant_id, listing["listing_id"])["effective_property_id"]
            if property_id is None:
                raise HTTPException(409, "MATCH_CANDIDATE_IDENTITY_UNAVAILABLE")
            prop = repo.graph._one(
                "SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=?",
                (principal.tenant_id, str(property_id)),
            )
            authorize(request, principal, prop)

    errors = {code: {"description": description} for code, description in (
        (401, "Authentication required"), (403, "Current actor or resource scope denied"),
        (404, "Canonical Intake, case or candidate identity not found"),
        (409, "Native version, custody or idempotency conflict"),
        (503, "Canonical storage or audit unavailable"),
    )}

    @router.get("/intakes/{intake_id}/match-case", operation_id="getCanonicalMatchCase",
                response_model=MatchCaseSummary, responses=errors)
    def select(intake_id: UUID, request: Request, response: Response) -> dict:
        with _http_errors():
            principal, repo = context(request)
            with repo.graph.transaction(principal.tenant_id):
                parent = repo.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=?",
                    (principal.tenant_id, str(intake_id)))
                authorize(request, principal, parent)
                selected = repo.get_for_intake(principal.tenant_id, str(intake_id))
                case = selected["match_case"]
                authorize_candidates(request, principal, repo, selected["candidates"])
                response.headers["ETag"] = f'W/"{case["version"]}"'
                return {"match_case_id": case["match_case_id"], "intake_id": case["intake_id"],
                    "status": case["status"], "outcome": case["outcome"], "version": case["version"],
                    "intake_version": selected["intake"]["version"],
                    "candidates": [{**{k: row[k] for k in ("match_candidate_id", "property_id", "listing_id", "rank", "confidence")},
                                    "observed_property_version": _observed_version(row, "property_version"),
                                    "observed_listing_version": _observed_version(row, "listing_version")}
                                   for row in selected["candidates"]]}

    @router.post("/intakes/{intake_id}/match-case", operation_id="createCanonicalMatchCase",
                 response_model=MatchCaseReceipt, status_code=201, responses=errors)
    def create(intake_id: UUID, body: CreateMatchCaseRequest, request: Request, response: Response,
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")],
        if_match: Annotated[str, Header(alias="If-Match", pattern=r'^W/"[1-9][0-9]*"$')],
    ) -> dict:
        with _http_errors():
            principal, repo = context(request)
            tenant, actor = principal.tenant_id, principal.subject_id
            candidates = tuple(MatchCandidateEvidence(str(c.property_id), c.property_version, c.confidence,
                c.evidence, str(c.listing_id) if c.listing_id else None, c.listing_version) for c in body.candidates)
            digest = hashlib.sha256(json.dumps({"body": body.model_dump(mode="json"), "if_match": if_match},
                sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            replay_key = f"{tenant}:{actor}:createCanonicalMatchCase:{intake_id}:{key}"
            # The graph owns the SERIALIZABLE root; receipt and graph share its
            # engine. A lost response can never cause a second case/audit/job.
            with repo.graph.transaction(tenant):
                store.refresh(tenant)
                with store.transaction():
                    parent = repo.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                        (tenant, str(intake_id)))
                    authorize(request, principal, parent, action="decide", body=body)
                    authorize_candidates(request, principal, repo, [c.model_dump() for c in body.candidates])
                    prior = store.replays.get(replay_key)
                    if prior is not None:
                        if prior[0] != digest:
                            raise HTTPException(409, "IDEMPOTENCY_KEY_REUSED")
                        result = prior[1]
                        response.headers["Idempotency-Replayed"] = "true"
                    else:
                        CanonicalAuditBacklog(repo.engine).require_available(tenant)
                        result = repo.create_review_case(tenant, str(intake_id),
                            expected_intake_version=int(if_match[3:-1]), actor_id=actor, confidence=body.confidence,
                            candidates=candidates, supporting_signals=body.supporting_signals,
                            contradictory_signals=body.contradictory_signals, reason=body.reason)
                        result["intake_id"] = str(intake_id)
                        result = MatchCaseReceipt.model_validate(result).model_dump(mode="json")
                        store.replays[replay_key] = (digest, result, 201)
                        store.flush()
                    response.headers["ETag"] = f'W/"{result["version"]}"'
            return result

    return router


def _observed_version(candidate: dict, name: str) -> int | None:
    evidence = candidate["evidence"]
    version = evidence.get(name) if isinstance(evidence, dict) else None
    return version if type(version) is int and version > 0 else None
