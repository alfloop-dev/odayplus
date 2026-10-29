"""Native Intake submission/read; processing remains an explicit next stage."""

import base64
import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.api.app.routes.canonical_match_cases import (
    NativeReceiptCommitted,
    NativeReceiptNotObserved,
    _http_errors,
    _receipt_observation,
)
from apps.api.oday_api.security.dependencies import principal_from_headers
from modules.external_data.security.assisted_listing_retrieval import (
    contains_sensitive_submission_material,
)
from modules.listing.application.intake_authorization import (
    authorize_intake_action,
    mask_intake,
    native_intake_authorization_resource,
)
from modules.listing.application.native_intake_assisted_entry_input import (
    NativeAssistedEntryCommand,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryUnavailable,
)
from modules.listing.application.native_intake_assisted_entry_request import (
    NativeAssistedEntryRequestOwner,
)
from modules.listing.application.native_intake_command_withdrawal import (
    NativeIntakeCommandWithdrawalOwner,
)
from modules.listing.application.native_intake_parser_policy import (
    NativeParserConflict,
    NativeParserUnavailable,
)
from modules.listing.application.native_intake_parser_prerequisites import (
    NativeParserCommandBudget,
    NativeParserSQLClock,
)
from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from modules.listing.application.native_intake_requester_authority import (
    NativeIntakeRequesterAuthorityUnavailable,
)
from modules.listing.application.native_intake_source_policy import (
    NativeIntakeSourcePolicyConflict,
    NativeIntakeSourcePolicyUnavailable,
)
from modules.listing.application.native_intake_source_policy_recheck import (
    NativeIntakeSourcePolicyRecheckOwner,
    NativeIntakeSourcePolicyRecheckUnavailable,
)
from modules.listing.application.native_intake_submission_validation import (
    validate_native_intake_url,
)
from modules.listing.domain.intake_states import (
    AssignmentState,
    IntakeStage,
    ListingState,
    SlaState,
)
from shared.audit.backlog import CanonicalAuditBacklog
from shared.auth import Role
from shared.jobs.queue import JobFenceRejectedError


class _SubmissionValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                if request.method != "POST":
                    raise
                # Validation inputs can include URL credentials, unknown fields
                # or malformed JSON. Never reflect that raw submission.
                raise HTTPException(422, "INVALID_CANONICAL_INTAKE_SUBMISSION") from None

        return validated


class NativeIntakeScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand_id: UUID | None = None
    region_id: UUID | None = None
    assigned_area_id: UUID | None = None
    heat_zone_id: UUID | None = None


class SubmitCanonicalIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intake_method: Literal["URL", "MANUAL"]
    original_url: str | None = Field(default=None, min_length=1, max_length=4096)
    scope: NativeIntakeScope
    reason: str = Field(min_length=3, max_length=4000)

    @field_validator("reason")
    @classmethod
    def explicit_reason(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("An explicit submission reason is required")
        return value

    @model_validator(mode="after")
    def validate_submission(self):
        if contains_sensitive_submission_material(self.model_dump()):
            raise ValueError("A native Intake must not contain credentials")
        if self.intake_method == "MANUAL":
            if self.original_url is not None:
                raise ValueError("A manual Intake must not claim a URL submission")
        else:
            validate_native_intake_url(self.original_url)
        return self


class CanonicalIntakeReceipt(BaseModel):
    intake_id: UUID
    processing_state: Literal["SUBMITTED"]
    version: int = Field(ge=1)
    intake_method: Literal["URL", "MANUAL"]
    submitted_at: datetime
    correlation_id: UUID
    transition_id: UUID
    audit_event_id: UUID
    outbox_event_id: UUID


class CanonicalIntakeSummary(BaseModel):
    intake_id: UUID
    tenant_id: UUID
    submitter_subject_id: UUID
    intake_method: Literal["URL", "MANUAL", "CSV", "APPROVED_FEED", "OPERATOR_SNAPSHOT"]
    processing_state: IntakeStage
    version: int = Field(ge=1)
    submitted_at: datetime
    correlation_id: UUID
    scope: NativeIntakeScope


class CanonicalPersistedListingField(BaseModel):
    field_path: str
    classification: str | None = None
    effective: str | int | float | bool | None = None
    masked: bool = False
    mask_reason_code: Literal["FIELD_MASKED"] | None = None


class CanonicalIntakeResolution(BaseModel):
    listing_id: UUID
    listing_version: int = Field(ge=1)
    lifecycle_state: ListingState
    current_revision_id: UUID | None
    property_id_at_creation: UUID
    effective_property_id: UUID
    property_version: int = Field(ge=1)
    fields: list[CanonicalPersistedListingField] = Field(default_factory=list, max_length=100)


class CanonicalIntakeWorkflow(BaseModel):
    assignment_id: UUID
    assignment_version: int = Field(ge=1)
    assignment_status: AssignmentState
    owner_subject_id: UUID | None
    owner_role: str | None
    queue_key: str
    sla_instance_id: UUID
    sla_version: int = Field(ge=1)
    sla_state: SlaState
    policy_version: str
    calendar_version: str
    started_at: AwareDatetime
    due_soon_at: AwareDatetime
    due_at: AwareDatetime
    breach_at: AwareDatetime


class CanonicalIntakeSourceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str | None
    recorded_policy_state: Literal["APPROVED_RETRIEVAL", "ASSISTED_ENTRY_ONLY", "AUTH_REQUIRED", "SOURCE_BLOCKED", "POLICY_UNKNOWN"] | None
    last_transition_at: AwareDatetime
    completed_at: AwareDatetime | None
    url_fields: list[CanonicalPersistedListingField] = Field(min_length=2, max_length=2)




class CancelCanonicalIntakeAuthorizationWaitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=4000)

    @field_validator("reason")
    @classmethod
    def explicit_safe_reason(cls, value):
        if len(value.strip()) < 3 or contains_sensitive_submission_material({"reason": value}):
            raise ValueError("An explicit cancellation reason without credentials is required")
        return value


class CanonicalIntakeCancellationReceipt(BaseModel):
    intake_id: UUID
    processing_state: Literal["CANCELLED"]
    version: int = Field(ge=1)
    cancelled_at: AwareDatetime
    correlation_id: UUID
    transition_id: UUID
    audit_event_id: UUID
    outbox_event_id: UUID


class RequestCanonicalIntakeSourcePolicyRecheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=4000)

    @field_validator("reason")
    @classmethod
    def safe_explicit_reason(cls, value):
        if len(value.strip()) < 3 or contains_sensitive_submission_material({"reason": value}):
            raise ValueError("An explicit recheck reason without credentials is required")
        return value


class CanonicalIntakeSourcePolicyRecheckReceipt(BaseModel):
    request_id: UUID
    intake_id: UUID
    status: Literal["REQUESTED"]
    version: int = Field(ge=1)
    requested_at: AwareDatetime
    correlation_id: UUID
    audit_event_id: UUID
    outbox_event_id: UUID


class CanonicalIntakeAssistedEntryRequestReceipt(CanonicalIntakeSourcePolicyRecheckReceipt):
    correction_ids: list[UUID] = Field(min_length=3, max_length=7)


class CanonicalIntakeCommandWithdrawalReceipt(BaseModel):
    cancellation_id: UUID
    request_id: UUID
    intake_id: UUID
    status: Literal["WITHDRAWN", "CANCELLATION_REQUESTED"]
    version: int = Field(ge=1)
    requested_at: AwareDatetime
    requested_by: UUID
    job_id: UUID | None
    correlation_id: UUID
    audit_event_id: UUID
    outbox_event_id: UUID


@contextmanager
def _assisted_errors():
    try:
        yield
    except PermissionError:
        raise HTTPException(403, "NATIVE_ASSISTED_CURRENT_AUTHORITY_DENIED") from None
    except (NativeIntakeRequesterAuthorityUnavailable, NativeAssistedEntryUnavailable,
            NativeIntakeSourcePolicyUnavailable):
        raise HTTPException(503, "NATIVE_ASSISTED_AUTHORITY_OR_POLICY_UNAVAILABLE") from None
    except NativeIntakeSourcePolicyConflict:
        raise HTTPException(409, "NATIVE_ASSISTED_CURRENT_PROOF_CONFLICT") from None
    except JobFenceRejectedError:
        raise HTTPException(409, "NATIVE_ASSISTED_CURRENT_PROOF_CONFLICT") from None
    except Exception as exc:
        cause, seen = exc, set()
        while cause is not None and id(cause) not in seen and len(seen) < 8:
            seen.add(id(cause))
            if getattr(cause, "sqlstate", None) in {"40001", "40P01", "55P03", "57014"}:
                raise HTTPException(409, "NATIVE_ASSISTED_CONCURRENT_CHANGE") from None
            cause = cause.__cause__ or cause.__context__
        if getattr(exc, "sqlstate", None) in {"42P01", "42703", "3F000"}:
            raise HTTPException(503, "NATIVE_ASSISTED_STORAGE_UNAVAILABLE") from None
        raise


@contextmanager
def _recheck_errors():
    try:
        yield
    except PermissionError:
        raise HTTPException(403, "NATIVE_RECHECK_CURRENT_AUTHORITY_DENIED") from None
    except (NativeIntakeRequesterAuthorityUnavailable, NativeIntakeSourcePolicyRecheckUnavailable,
            NativeIntakeSourcePolicyUnavailable):
        raise HTTPException(503, "NATIVE_RECHECK_AUTHORITY_OR_POLICY_UNAVAILABLE") from None
    except (NativeIntakeSourcePolicyConflict, JobFenceRejectedError):
        raise HTTPException(409, "NATIVE_RECHECK_CURRENT_PROOF_CONFLICT") from None
    except Exception as exc:
        # Canonical audit preserves the SQL cause when wrapping a failed
        # write. A SERIALIZABLE conflict remains a conflict of this command;
        # an actual audit outage keeps the existing 503 handling below.
        cause, seen = exc, set()
        while cause is not None and id(cause) not in seen and len(seen) < 8:
            seen.add(id(cause))
            if getattr(cause, "sqlstate", None) in {"40001", "40P01", "55P03", "57014"}:
                raise HTTPException(409, "NATIVE_RECHECK_CONCURRENT_CHANGE") from None
            cause = cause.__cause__ or cause.__context__
        if getattr(exc, "sqlstate", None) in {"42P01", "42703", "3F000"}:
            raise HTTPException(503, "NATIVE_RECHECK_STORAGE_UNAVAILABLE") from None
        raise


class CanonicalIntakePage(BaseModel):
    items: list[CanonicalIntakeSummary]
    next_cursor: str | None


class _IntakeCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    tenant_id: UUID
    processing_state: IntakeStage | None
    submitted_at: AwareDatetime
    intake_id: UUID


class CanonicalIntakeTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transition_id: UUID
    sequence_no: int = Field(ge=1, strict=True)
    from_state: IntakeStage | None
    to_state: IntakeStage
    actor_subject_id: UUID | None
    service_principal: str | None
    permission: str
    reason_code: str | None
    expected_version: int | None = Field(ge=1, strict=True)
    resulting_version: int = Field(ge=1, strict=True)
    job_id: UUID | None
    source_policy_version: int | None = Field(ge=1, strict=True)
    parser_release_id: UUID | None
    source_snapshot_id: UUID | None
    match_case_id: UUID | None
    correlation_id: UUID
    causation_id: UUID | None
    occurred_at: AwareDatetime

    @model_validator(mode="after")
    def unambiguous_actor(self):
        if self.actor_subject_id is not None and self.service_principal is not None:
            raise ValueError("Recorded transition has ambiguous actor attribution")
        return self


class CanonicalIntakeHistoryPage(BaseModel):
    intake_id: UUID
    intake_version: int = Field(ge=1, strict=True)
    through_sequence: int = Field(ge=0, strict=True)
    items: list[CanonicalIntakeTransition] = Field(max_length=25)
    next_cursor: str | None
    previous_cursor: str | None


class _CanonicalRecordedMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*")
    @classmethod
    def safe_recorded_text(cls, value):
        if isinstance(value, str) and (not value.strip() or any(ord(char) < 32 for char in value)
                or contains_sensitive_submission_material({"value": value})):
            raise ValueError("Recorded metadata is unavailable")
        return value


class CanonicalIntakeSnapshotMetadata(_CanonicalRecordedMetadata):
    source_snapshot_id: UUID
    intake_id: UUID
    source_id: str = Field(min_length=1, max_length=256)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1, max_length=256)
    byte_length: int = Field(ge=0, le=9_007_199_254_740_991, strict=True)
    captured_at: AwareDatetime
    observed_at: AwareDatetime
    stored_at: AwareDatetime
    capture_method: Literal["SERVER_RETRIEVAL", "OPERATOR_UPLOAD", "APPROVED_FEED", "MANUAL_ENTRY"]
    version: int = Field(ge=1, le=9_007_199_254_740_991, strict=True)
    retention_class: str = Field(min_length=1, max_length=64)
    purge_after: AwareDatetime | None
    legal_hold: bool = Field(strict=True)
    object_generation: str | None = Field(pattern=r"^[1-9][0-9]{0,18}$", strict=True)
    residency_mode: Literal["TW_ONLY", "APPROVED_APAC_DR"]


class CanonicalIntakeParserRunMetadata(_CanonicalRecordedMetadata):
    parser_run_id: UUID
    intake_id: UUID
    source_snapshot_id: UUID
    parser_release_id: UUID
    source_id: str = Field(min_length=1, max_length=256)
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]
    version: int = Field(ge=1, le=9_007_199_254_740_991, strict=True)
    created_at: AwareDatetime
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    correlation_id: UUID
    semantic_version: str = Field(min_length=1, max_length=128)
    package_name: str = Field(min_length=1, max_length=256)
    output_schema_version: str = Field(min_length=1, max_length=256)


class CanonicalIntakeSnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["SNAPSHOT"]
    recorded_at: AwareDatetime
    metadata: CanonicalIntakeSnapshotMetadata


class CanonicalIntakeParserRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["PARSER_RUN"]
    recorded_at: AwareDatetime
    metadata: CanonicalIntakeParserRunMetadata


class CanonicalIntakeEvidencePage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intake_id: UUID
    intake_version: int = Field(ge=1, strict=True)
    through_at: AwareDatetime
    observed_at: AwareDatetime
    items: list[Annotated[CanonicalIntakeSnapshotRecord | CanonicalIntakeParserRunRecord, Field(discriminator="kind")]] = Field(max_length=25)
    next_cursor: str | None
    previous_cursor: str | None

    @model_validator(mode="after")
    def recorded_parent_and_times(self):
        if self.through_at > self.observed_at:
            raise ValueError("Evidence bound exceeds its observation")
        for item in self.items:
            stamp = item.metadata.stored_at if item.kind == "SNAPSHOT" else item.metadata.created_at
            if item.metadata.intake_id != self.intake_id or item.recorded_at != stamp or stamp > self.through_at:
                raise ValueError("Evidence record differs from its parent or recorded time")
        return self


class CanonicalIntakeDetail(CanonicalIntakeSummary):
    resolution: CanonicalIntakeResolution | None = None
    workflow: CanonicalIntakeWorkflow | None = None
    source_info: CanonicalIntakeSourceInfo | None = None

    processing_history: CanonicalIntakeHistoryPage | None = None
    processing_evidence: CanonicalIntakeEvidencePage | None = None


class CanonicalParserField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correction_id: UUID
    field_path: Literal["address", "rent", "areaPing", "providerListingId", "floor", "listingType", "listingStatus"]
    classification: str | None
    status: Literal["PROPOSED", "APPLIED", "REJECTED", "SUPERSEDED", "REVERSED"]
    version: int = Field(ge=1, strict=True)
    identity_affecting: bool
    proposed_by: UUID
    created_at: AwareDatetime
    reviewed_by: UUID | None
    reviewed_at: AwareDatetime | None
    parsed: str | int | float | None
    normalized: str | int | float | None
    corrected: str | int | float | None
    confidence: float | None = Field(ge=0, le=1)
    correction_reason: str | None
    masked: bool
    mask_reason_code: Literal["FIELD_MASKED"] | None = None


class CanonicalIntakeParserFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intake_id: UUID
    intake_version: int = Field(ge=1, strict=True)
    source_id: str = Field(min_length=1, max_length=256)
    parser_run_id: UUID
    parser_run_version: int = Field(ge=1, strict=True)
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]
    parser_job_id: UUID
    parser_job_version: int = Field(ge=1, strict=True)
    job_status: Literal["QUEUED", "RUNNING", "RETRYING", "SUCCEEDED", "FAILED", "CANCELLED"]
    source_snapshot_id: UUID
    parser_release_id: UUID
    observed_at: AwareDatetime
    fields: list[CanonicalParserField] = Field(min_length=3, max_length=7)


class _IntakeEvidenceCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    tenant_id: UUID
    intake_id: UUID
    intake_version: int = Field(ge=1, strict=True)
    through_at: AwareDatetime
    position_at: AwareDatetime
    position_kind: Literal["SNAPSHOT", "PARSER_RUN"]
    position_id: UUID
    direction: Literal["forward", "backward"]

    @model_validator(mode="after")
    def bounded_position(self):
        if self.position_at > self.through_at or not self.position_id.int:
            raise ValueError("Evidence position is outside its recorded range")
        return self


class _IntakeHistoryCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    tenant_id: UUID
    intake_id: UUID
    intake_version: int = Field(ge=1, strict=True)
    through_sequence: int = Field(ge=1, strict=True)
    position: int = Field(ge=1, strict=True)
    direction: Literal["forward", "backward"]

    @model_validator(mode="after")
    def bounded_position(self):
        if self.position > self.through_sequence:
            raise ValueError("History position is outside its recorded range")
        return self


def _encode_cursor(value: BaseModel) -> str:
    return base64.urlsafe_b64encode(value.model_dump_json().encode()).decode()


@contextmanager
def _evidence_errors():
    try:
        yield
    except (NativeParserConflict, NativeParserUnavailable, JobFenceRejectedError):
        raise HTTPException(503, "CANONICAL_INTAKE_EVIDENCE_UNAVAILABLE") from None
    except Exception as error:
        if getattr(error, "sqlstate", None) in {"57014", "55P03"}:
            raise HTTPException(503, "CANONICAL_INTAKE_EVIDENCE_UNAVAILABLE") from None
        raise


def create_canonical_intakes_router(store) -> APIRouter:
    router = APIRouter(tags=["assisted-listing-intake"], route_class=_SubmissionValidationRoute)

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
        if (bundle is None or getattr(getattr(bundle, "engine", None), "dialect", None) != "postgresql"
                or getattr(bundle, "assisted_intake_store", None) is not store
                or getattr(store, "_engine", None) is not bundle.engine):
            raise HTTPException(503, "CANONICAL_INTAKE_STORAGE_UNAVAILABLE")
        factory = getattr(bundle, "canonical_intakes", None)
        if not callable(factory):
            raise HTTPException(503, "CANONICAL_INTAKE_STORAGE_UNAVAILABLE")
        try:
            return principal, factory()
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(503, "CANONICAL_INTAKE_STORAGE_UNAVAILABLE") from exc

    def read_flag_owner(request, repo):
        from shared.infrastructure.persistence.feature_flags import (
            FeatureFlagStoreUnavailable,
            require_durable_flag_owner,
        )

        bundle = request.app.state.persistence_bundle
        try:
            return require_durable_flag_owner(getattr(bundle, "feature_flag_registry", None), repo.engine,
                queue=getattr(bundle, "job_queue", None))
        except FeatureFlagStoreUnavailable as exc:
            raise HTTPException(503, "FEATURE_FLAG_STORE_UNAVAILABLE") from exc

    def authorize(request, principal, repo, row, action="view", *, listing_context=None, flag_owner=None):
        authorize_intake_action(principal, action,
            resource=native_intake_authorization_resource(row, graph=repo.graph, listing_context=listing_context),
            audit_log=getattr(request.app.state, "audit_log", None),
            correlation_id=request.headers.get("x-correlation-id"),
            required_feature_flag=("assisted_intake_v1_read", flag_owner) if flag_owner is not None else None)

    def authorize_read_collection(request, principal, flags):
        authorize_intake_action(principal, "view", tenant_id=principal.tenant_id,
            collection_scope={}, collection_rows_projected=True,
            audit_log=getattr(request.app.state, "audit_log", None),
            correlation_id=request.headers.get("x-correlation-id"),
            required_feature_flag=("assisted_intake_v1_read", flags))

    errors = {code: {"description": description} for code, description in (
        (401, "Authentication required"), (403, "Current actor or resource scope denied"),
        (404, "Native Intake not found"), (409, "Concurrent command or idempotency conflict"),
        (422, "Invalid native submission"), (503, "Canonical storage or audit unavailable"),
    )}

    def history_page(principal, repo, row, intake_version, cursor, page_size):
        tenant, selected_id = principal.tenant_id, row["intake_id"]
        if row["version"] != intake_version:
            raise HTTPException(409, "CANONICAL_INTAKE_VERSION_CHANGED")
        decoded = None
        if cursor is not None:
            try:
                decoded = _IntakeHistoryCursor.model_validate_json(base64.b64decode(cursor, altchars=b"-_", validate=True))
            except ValueError:
                raise HTTPException(422, "INVALID_CANONICAL_INTAKE_HISTORY_CURSOR") from None
            if str(decoded.tenant_id) != tenant or str(decoded.intake_id) != selected_id:
                raise HTTPException(422, "CANONICAL_INTAKE_HISTORY_CURSOR_RESOURCE_MISMATCH")
            if decoded.intake_version != intake_version:
                raise HTTPException(409, "CANONICAL_INTAKE_VERSION_CHANGED")
        span = repo.processing_history_span(tenant, selected_id)
        first, last = span["first_sequence"], span["last_sequence"]
        if first is not None and (type(first) is not int or first < 1 or type(last) is not int or last < first):
            raise HTTPException(503, "CANONICAL_INTAKE_HISTORY_UNAVAILABLE")
        through = decoded.through_sequence if decoded else last or 0
        if decoded and (last is None or through > last):
            raise HTTPException(422, "INVALID_CANONICAL_INTAKE_HISTORY_CURSOR")
        span = repo.processing_history_span(tenant, selected_id, through)
        backward = decoded is not None and decoded.direction == "backward"
        rows = repo.processing_history(tenant, selected_id, through=through,
            position=decoded.position if decoded else 0, backward=backward, limit=page_size + 1)
        if decoded and not rows:
            raise HTTPException(422, "INVALID_CANONICAL_INTAKE_HISTORY_CURSOR")
        chosen = rows[:page_size]
        if backward:
            chosen.reverse()
        try:
            items = [CanonicalIntakeTransition.model_validate(item) for item in chosen]
        except (ValueError, TypeError):
            raise HTTPException(503, "CANONICAL_INTAKE_HISTORY_UNAVAILABLE") from None
        if any(item.resulting_version > intake_version or (item.resulting_version == intake_version
                and item.to_state != row["processing_state"]) for item in items):
            raise HTTPException(503, "CANONICAL_INTAKE_HISTORY_UNAVAILABLE")

        def at(sequence, direction):
            return _encode_cursor(_IntakeHistoryCursor(version=1, tenant_id=tenant, intake_id=selected_id,
                intake_version=intake_version, through_sequence=through, position=sequence, direction=direction))

        return {"intake_id": selected_id, "intake_version": intake_version, "through_sequence": through,
            "items": items,
            "next_cursor": at(items[-1].sequence_no, "forward") if items and items[-1].sequence_no < span["last_sequence"] else None,
            "previous_cursor": at(items[0].sequence_no, "backward") if items and items[0].sequence_no > span["first_sequence"] else None}

    def evidence_page(principal, repo, row, intake_version, cursor, page_size, before):
        tenant, selected_id = principal.tenant_id, row["intake_id"]
        if row["version"] != intake_version:
            raise HTTPException(409, "CANONICAL_INTAKE_VERSION_CHANGED")
        before()
        observed = before.source._clock()
        before.check_in_memory()
        decoded = None
        if cursor is not None:
            try:
                decoded = _IntakeEvidenceCursor.model_validate_json(base64.b64decode(cursor, altchars=b"-_", validate=True))
            except ValueError:
                raise HTTPException(422, "INVALID_CANONICAL_INTAKE_EVIDENCE_CURSOR") from None
            if str(decoded.tenant_id) != tenant or str(decoded.intake_id) != selected_id:
                raise HTTPException(422, "CANONICAL_INTAKE_EVIDENCE_CURSOR_RESOURCE_MISMATCH")
            if decoded.intake_version != intake_version:
                raise HTTPException(409, "CANONICAL_INTAKE_VERSION_CHANGED")
        through = decoded.through_at if decoded else observed
        if through > observed:
            raise HTTPException(422, "INVALID_CANONICAL_INTAKE_EVIDENCE_CURSOR")
        position = (decoded.position_at, decoded.position_kind, str(decoded.position_id)) if decoded else None
        backward = decoded is not None and decoded.direction == "backward"
        try:
            if position is not None and not repo.processing_evidence_position(tenant, selected_id, position,
                    source_id=row["source_id"], before=before):
                raise HTTPException(422, "INVALID_CANONICAL_INTAKE_EVIDENCE_CURSOR")
            records = repo.processing_evidence(tenant, selected_id, through=through, position=position,
                backward=backward, limit=page_size + 1, before=before)
            if decoded and not records:
                raise HTTPException(422, "INVALID_CANONICAL_INTAKE_EVIDENCE_CURSOR")
            chosen = records[:page_size]
            if backward:
                chosen.reverse()
            items = []
            for record in chosen:
                snapshot = record["kind"] == "SNAPSHOT"
                model = CanonicalIntakeSnapshotMetadata if snapshot else CanonicalIntakeParserRunMetadata
                metadata = {key: record["metadata"][key] for key in model.model_fields}
                if metadata["source_id"] != row["source_id"]:
                    raise ValueError("Recorded evidence source differs")
                if snapshot and metadata["object_generation"] is not None:
                    if type(metadata["object_generation"]) is not int:
                        raise ValueError("Recorded generation differs")
                    metadata["object_generation"] = str(metadata["object_generation"])
                record_model = CanonicalIntakeSnapshotRecord if snapshot else CanonicalIntakeParserRunRecord
                items.append(record_model.model_validate({"kind": record["kind"],
                    "recorded_at": record["recorded_at"], "metadata": metadata}))

            def at(item, direction):
                reference = item.metadata.source_snapshot_id if item.kind == "SNAPSHOT" else item.metadata.parser_run_id
                return _encode_cursor(_IntakeEvidenceCursor(version=1, tenant_id=tenant, intake_id=selected_id,
                    intake_version=intake_version, through_at=through, position_at=item.recorded_at,
                    position_kind=item.kind, position_id=reference, direction=direction))

            more = len(records) > page_size
            page = CanonicalIntakeEvidencePage(intake_id=selected_id, intake_version=intake_version,
                through_at=through, observed_at=observed, items=items,
                next_cursor=at(items[-1], "forward") if items and (decoded is not None if backward else more) else None,
                previous_cursor=at(items[0], "backward") if items and (more if backward else decoded is not None) else None)
            before()
            return page
        except (ValueError, TypeError, KeyError):
            raise HTTPException(503, "CANONICAL_INTAKE_EVIDENCE_UNAVAILABLE") from None


    @router.get("/canonical-intakes", operation_id="listCanonicalIntakes",
                response_model=CanonicalIntakePage, responses=errors)
    def page(request: Request, processing_state: IntakeStage | None = None,
             page_size: Annotated[int, Query(ge=1, le=100)] = 25,
             cursor: Annotated[str | None, Query(max_length=2048)] = None):
        with _http_errors():
            principal, repo = context(request)
            tenant = principal.tenant_id
            flags = read_flag_owner(request, repo)
            # Server-owned contract: SQL projects all native scope axes and
            # staff ownership before LIMIT; each row is reauthorized below.
            # This opt-in cannot be supplied through a query/body parameter.
            authorize_read_collection(request, principal, flags)
            position = None
            if cursor is not None:
                try:
                    decoded = _IntakeCursor.model_validate_json(base64.b64decode(cursor, altchars=b"-_", validate=True))
                except ValueError as exc:
                    raise HTTPException(422, "INVALID_CANONICAL_INTAKE_CURSOR") from exc
                if str(decoded.tenant_id) != tenant or decoded.processing_state != processing_state:
                    raise HTTPException(422, "CANONICAL_INTAKE_CURSOR_QUERY_MISMATCH")
                position = decoded.submitted_at.isoformat(), str(decoded.intake_id)
            is_staff = principal.has_role(Role.EXPANSION_USER) and not principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE)
            with repo.graph.transaction(tenant):
                rows = repo.page(tenant, scope=principal.scope, owner=principal.subject_id if is_staff else None,
                                 state=processing_state.value if processing_state else None,
                                 before=position, limit=page_size + 1)
                for row in rows:
                    authorize(request, principal, repo, row, flag_owner=flags)
                selected = rows[:page_size]
                next_cursor = None
                if len(rows) > page_size:
                    last = selected[-1]
                    value = _IntakeCursor(version=1, tenant_id=tenant, processing_state=processing_state,
                                          submitted_at=last["submitted_at"], intake_id=last["intake_id"])
                    next_cursor = _encode_cursor(value)
                return {"items": [{**row, "scope": {key: row[key] for key in NativeIntakeScope.model_fields}}
                                  for row in selected], "next_cursor": next_cursor}

    @router.get("/canonical-intakes/{intake_id}", operation_id="getCanonicalIntake",
                response_model=CanonicalIntakeDetail, responses=errors)
    def get(intake_id: UUID, request: Request, response: Response,
            include_source: bool = False, include_history: bool = False, include_evidence: bool = False):
        with _http_errors(), _evidence_errors():
            principal, repo = context(request)
            flags = read_flag_owner(request, repo)
            with repo.graph.transaction(principal.tenant_id):
                evidence_before = NativeParserCommandBudget(NativeParserSQLClock(repo.engine)) if include_evidence else None
                if evidence_before:
                    evidence_before()
                row = repo.get(principal.tenant_id, str(intake_id))
                authorize(request, principal, repo, row, flag_owner=flags)
                resolution = None
                if row["resolved_listing_id"] is not None:
                    listing = repo.graph._one("SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=?",
                        (principal.tenant_id, row["resolved_listing_id"]))
                    resolved = repo.graph.resolve_listing(principal.tenant_id, listing["listing_id"])
                    prop = repo.graph._one("SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=?",
                        (principal.tenant_id, resolved["effective_property_id"]))
                    # Intake ownership grants no access to a resolved Listing
                    # or a subsequently merged Property outside current scope.
                    authorize(request, principal, repo, listing, listing_context=(resolved, prop), flag_owner=flags)
                    authorize(request, principal, repo, prop, flag_owner=flags)
                    fields = []
                    if listing["current_revision_id"] is not None:
                        revision = repo.graph._one("SELECT effective_values,parser_run_id,source_snapshot_id "
                            "FROM expansion.listing_revisions WHERE tenant_id=? AND listing_id=? AND listing_revision_id=?",
                            (principal.tenant_id, listing["listing_id"], listing["current_revision_id"]))
                        values = revision["effective_values"]
                        values = json.loads(values) if isinstance(values, str) else values
                        if not isinstance(values, dict) or len(values) > 100:
                            raise HTTPException(409, "CANONICAL_LISTING_FIELDS_UNAVAILABLE")
                        classifications = {}
                        for item in repo.engine.query("SELECT field_path,field_classification FROM intake.human_corrections "
                                "WHERE tenant_id=? AND listing_id=? AND parser_run_id=? AND source_snapshot_id=? "
                                "AND status='APPLIED' ORDER BY field_path",
                                (principal.tenant_id, listing["listing_id"], revision["parser_run_id"], revision["source_snapshot_id"])):
                            classifications.setdefault(item["field_path"], []).append(item["field_classification"])
                        # Values come only from the actual current revision.
                        # Missing/ambiguous classification stays unknown; the
                        # shared mask denies it even for highest clearance.
                        fields = mask_intake(principal, {"fields": [
                            {"field_path": path, "effective": value,
                             "classification": classifications[path][0] if len(classifications.get(path, [])) == 1 else None}
                            for path, value in sorted(values.items())]})["fields"]
                    resolution = {"listing_id": listing["listing_id"], "listing_version": listing["version"],
                        "lifecycle_state": listing["lifecycle_state"], "current_revision_id": listing["current_revision_id"],
                        "property_id_at_creation": listing["property_id"], "effective_property_id": prop["property_id"],
                        "property_version": prop["version"], "fields": fields}
                response.headers["ETag"] = f'W/"{row["version"]}"'
                response.headers["Cache-Control"] = "no-store"
                source_info = None
                if include_source:
                    url_fields = []
                    for field, column in (("originalUrl", "original_url"), ("canonicalUrl", "canonical_url")):
                        classification = "CONFIDENTIAL"
                        value = row[column]
                        if value is not None:
                            try:
                                # Original native validation has no DNS/network IO.
                                # Historical credentials remain hidden even at the
                                # highest clearance; this grants no source action.
                                validate_native_intake_url(value)
                                if contains_sensitive_submission_material({field: value}):
                                    classification = None
                            except ValueError:
                                classification = None
                        url_fields.append({"field_path": field, "classification": classification,
                                           "effective": value, "masked": False})
                    source_info = {"source_id": row["source_id"], "recorded_policy_state": row["source_policy_state"],
                        "last_transition_at": row["last_transition_at"], "completed_at": row["completed_at"],
                        "url_fields": mask_intake(principal, {"fields": url_fields})["fields"]}
                return {**row, "scope": {key: row[key] for key in NativeIntakeScope.model_fields},
                        "resolution": resolution, "workflow": repo.workflow(principal.tenant_id, str(intake_id)),
                        "source_info": source_info,
                        "processing_history": history_page(principal, repo, row, row["version"], None, 25) if include_history else None,
                        "processing_evidence": evidence_page(principal, repo, row, row["version"], None, 25, evidence_before)
                            if include_evidence else None}

    @router.get("/canonical-intakes/{intake_id}/processing-evidence", operation_id="getCanonicalIntakeProcessingEvidence",
                response_model=CanonicalIntakeEvidencePage, responses=errors,
                description="Read saved snapshot and parser lifecycle metadata. No object content or verification grant.")
    def processing_evidence(intake_id: UUID, request: Request, response: Response,
        intake_version: Annotated[int, Query(ge=1)],
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        page_size: Annotated[int, Query(ge=1, le=25)] = 25,
    ):
        with _http_errors(), _evidence_errors():
            principal, repo = context(request)
            flags = read_flag_owner(request, repo)
            with repo.graph.transaction(principal.tenant_id):
                before = NativeParserCommandBudget(NativeParserSQLClock(repo.engine))
                before()
                row = repo.get(principal.tenant_id, str(intake_id))
                authorize(request, principal, repo, row, flag_owner=flags)
                page = evidence_page(principal, repo, row, intake_version, cursor, page_size, before)
                response.headers["Cache-Control"] = "no-store"
                return page

    @router.get("/canonical-intakes/{intake_id}/processing-history", operation_id="getCanonicalIntakeProcessingHistory",
                response_model=CanonicalIntakeHistoryPage, responses=errors)
    def processing_history(intake_id: UUID, request: Request, response: Response,
        intake_version: Annotated[int, Query(ge=1)],
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        page_size: Annotated[int, Query(ge=1, le=25)] = 25,
    ):
        with _http_errors():
            principal, repo = context(request)
            flags = read_flag_owner(request, repo)
            tenant, selected_id = principal.tenant_id, str(intake_id)
            with repo.graph.transaction(tenant):
                row = repo.get(tenant, selected_id)
                authorize(request, principal, repo, row, flag_owner=flags)
                value = history_page(principal, repo, row, intake_version, cursor, page_size)
                response.headers["Cache-Control"] = "no-store"
                response.headers["ETag"] = f'W/"{intake_version}"'
                return value

    @router.get("/canonical-intakes/{intake_id}/parser-runs/{parser_run_id}/fields",
                operation_id="getCanonicalIntakeParserFields", response_model=CanonicalIntakeParserFields,
                responses=errors, description="Read the selected run's proven fields and observed correction status; no execution grant.")
    def parser_fields(intake_id: UUID, parser_run_id: UUID, request: Request, response: Response,
        intake_version: Annotated[int, Query(ge=1)], parser_run_version: Annotated[int, Query(ge=1)],
    ):
        with _http_errors(), _evidence_errors():
            principal, repo = context(request)
            flags = read_flag_owner(request, repo)
            with repo.graph.transaction(principal.tenant_id):
                before = NativeParserCommandBudget(NativeParserSQLClock(repo.engine))
                before()
                row = repo.get(principal.tenant_id, str(intake_id))
                authorize(request, principal, repo, row, flag_owner=flags)
                value = repo.parser_fields(row, str(parser_run_id), intake_version=intake_version,
                    run_version=parser_run_version, before=before)
                value["fields"] = mask_intake(principal, {"fields": value["fields"]})["fields"]
                # The shared mask also clears an effective cell. Historical
                # parser/proposal fields never represent a saved Listing value.
                for field in value["fields"]:
                    field.pop("effective", None)
                before.check_in_memory()
                response.headers["Cache-Control"] = "no-store"
                return value

    @router.get("/canonical-intakes/submissions/receipt", operation_id="getCanonicalIntakeSubmissionReceipt",
                response_model=NativeReceiptCommitted[CanonicalIntakeReceipt] | NativeReceiptNotObserved, responses=errors)
    def submission_receipt(request: Request, response: Response,
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")],
    ):
        with _http_errors():
            principal, repo = context(request)
            flags = read_flag_owner(request, repo)
            with repo.graph.transaction(principal.tenant_id):
                authorize_read_collection(request, principal, flags)
                prior = store.read_command_replay(principal.tenant_id, principal.subject_id, "submitCanonicalIntake", key)
                observed = _receipt_observation(CanonicalIntakeReceipt, prior[1] if prior else None,
                    prior[2] if prior else None, expected_status=201)
                if isinstance(observed, NativeReceiptCommitted):
                    row = repo.get(principal.tenant_id, str(observed.receipt.intake_id))
                    authorize(request, principal, repo, row, flag_owner=flags)
                    if (row["submitter_subject_id"] != principal.subject_id
                            or row["intake_method"] != observed.receipt.intake_method
                            or row["correlation_id"] != str(observed.receipt.correlation_id)):
                        raise HTTPException(503, "CANONICAL_INTAKE_STORAGE_UNAVAILABLE")
                response.headers["Cache-Control"] = "no-store"
                return observed

    @router.post("/canonical-intakes", operation_id="submitCanonicalIntake", status_code=201,
                 response_model=CanonicalIntakeReceipt, responses=errors)
    def submit(body: SubmitCanonicalIntakeRequest, request: Request, response: Response,
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")],
    ):
        with _http_errors():
            principal, repo = context(request)
            tenant, actor = principal.tenant_id, principal.subject_id
            scope = body.scope.model_dump(mode="json")
            digest = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()
            replay_key = store.command_replay_key(tenant, actor, "submitCanonicalIntake", key)
            with repo.graph.transaction(tenant):
                store.refresh(tenant)
                with store.transaction():
                    authorize(request, principal, repo, {"scope": {"tenant_id": tenant, **scope}},
                              "submit_url" if body.intake_method == "URL" else "submit_manual")
                    prior = store.replays.get(replay_key)
                    if prior is not None:
                        if prior[0] != digest:
                            raise HTTPException(409, "IDEMPOTENCY_KEY_REUSED")
                        result = prior[1]
                        authorize(request, principal, repo, repo.get(tenant, result["intake_id"]))
                        response.headers["Idempotency-Replayed"] = "true"
                    else:
                        CanonicalAuditBacklog(repo.engine).require_available(tenant)
                        result = repo.submit(tenant, actor, method=body.intake_method, original_url=body.original_url,
                            scope=scope, reason=body.reason, idempotency_key=key)
                        result = CanonicalIntakeReceipt.model_validate(result).model_dump(mode="json")
                        store.replays[replay_key] = (digest, result, 201)
                        store.flush()
                        response.headers["Idempotency-Replayed"] = "false"
                    response.headers["ETag"] = f'W/"{result["version"]}"'
                    return result

    @router.post("/canonical-intakes/{intake_id}/cancel", operation_id="cancelCanonicalIntakeAuthorizationWait",
                 response_model=CanonicalIntakeCancellationReceipt, responses=errors,
                 description="Cancel a settled AUTH_REQUIRED authorization wait with current authority. Other stages are rejected.")
    def cancel_authorization_wait(intake_id: UUID, body: CancelCanonicalIntakeAuthorizationWaitRequest,
        request: Request, response: Response,
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")],
        if_match: Annotated[str, Header(alias="If-Match", pattern=r'^W/"[1-9][0-9]*"$')],
    ):
        with _http_errors():
            principal, repo = context(request)
            tenant, actor = principal.tenant_id, principal.subject_id
            digest = hashlib.sha256(json.dumps({"body": body.model_dump(mode="json"), "if_match": if_match},
                sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            replay_key = store.command_replay_key(tenant, actor, "cancelCanonicalIntakeAuthorizationWait", key, str(intake_id))
            with repo.graph.transaction(tenant):
                store.refresh(tenant)
                with store.transaction():
                    authorize(request, principal, repo, repo.get(tenant, str(intake_id)), "cancel")
                    repo.cancellation_context(principal, str(intake_id))
                    prior = store.replays.get(replay_key)
                    if prior is not None:
                        if prior[0] != digest:
                            raise HTTPException(409, "IDEMPOTENCY_KEY_REUSED")
                        result = CanonicalIntakeCancellationReceipt.model_validate(prior[1]).model_dump(mode="json")
                        response.headers["Idempotency-Replayed"] = "true"
                    else:
                        result = repo.cancel_authorization_wait(principal, str(intake_id),
                            expected_version=int(if_match[3:-1]), reason=body.reason, idempotency_key=key)
                        result = CanonicalIntakeCancellationReceipt.model_validate(result).model_dump(mode="json")
                        store.replays[replay_key] = (digest, result, 200)
                        store.flush()
                        response.headers["Idempotency-Replayed"] = "false"
                    response.headers["ETag"] = f'W/"{result["version"]}"'
                    return result

    @router.post("/canonical-intakes/{intake_id}/source-policy-rechecks", operation_id="requestCanonicalIntakeSourcePolicyRecheck",
                 response_model=CanonicalIntakeSourcePolicyRecheckReceipt, status_code=202, responses=errors,
                 description="Request a governed current-authority recheck from a native authorization or assisted-entry wait.")
    def request_source_policy_recheck(intake_id: UUID, body: RequestCanonicalIntakeSourcePolicyRecheck,
        request: Request, response: Response,
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")],
        if_match: Annotated[str, Header(alias="If-Match", pattern=r'^W/"[1-9][0-9]*"$')],
    ):
        with _http_errors(), _recheck_errors():
            principal, repo = context(request)
            owner = NativeIntakeSourcePolicyRecheckOwner(repo.graph)
            tenant, actor = principal.tenant_id, principal.subject_id
            digest = hashlib.sha256(json.dumps({"body": body.model_dump(mode="json"), "if_match": if_match},
                sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            replay_key = store.command_replay_key(tenant, actor, "requestCanonicalIntakeSourcePolicyRecheck", key, str(intake_id))
            with repo.graph.transaction(tenant):
                store.refresh(tenant)
                with store.transaction():
                    deadline = owner.source._clock() + timedelta(milliseconds=750)
                    owner.command_context(principal, str(intake_id), before=lambda: owner.source._admission_budget(deadline))
                    prior = store.replays.get(replay_key)
                    if prior is not None:
                        if prior[0] != digest:
                            raise HTTPException(409, "IDEMPOTENCY_KEY_REUSED")
                        result = CanonicalIntakeSourcePolicyRecheckReceipt.model_validate(prior[1]).model_dump(mode="json")
                        response.headers["Idempotency-Replayed"] = "true"
                    else:
                        result = owner.request(principal, str(intake_id), expected_version=int(if_match[3:-1]),
                                               reason=body.reason, idempotency_key=key)
                        result = CanonicalIntakeSourcePolicyRecheckReceipt.model_validate(result).model_dump(mode="json")
                        store.replays[replay_key] = (digest, result, 202)
                        store.flush()
                        response.headers["Idempotency-Replayed"] = "false"
                    response.headers["ETag"] = f'W/"{result["version"]}"'
                    return result

    @router.post("/canonical-intakes/{intake_id}/assisted-entry-completions",
                 operation_id="requestCanonicalIntakeAssistedEntryCompletion", status_code=202,
                 response_model=CanonicalIntakeAssistedEntryRequestReceipt, responses=errors,
                 description="Record initial human proposals for a proven current manual-source wait.")
    def request_assisted_entry_completion(intake_id: UUID, body: NativeAssistedEntryCommand,
        request: Request, response: Response,
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")],
        if_match: Annotated[str, Header(alias="If-Match", pattern=r'^W/"[1-9][0-9]*"$')],
    ):
        with _http_errors(), _assisted_errors():
            principal, repo = context(request)
            receipt, replayed = NativeAssistedEntryRequestOwner(repo.graph).request(principal, str(intake_id),
                expected_version=int(if_match[3:-1]), command=body, idempotency_key=key)
            response.headers["ETag"] = f'W/"{receipt["version"]}"'
            response.headers["Idempotency-Replayed"] = str(replayed).lower()
            return receipt

    @router.get("/canonical-intakes/{intake_id}/assisted-entry-completions/receipt",
                operation_id="getCanonicalIntakeAssistedEntryRequestReceipt",
                response_model=NativeReceiptCommitted[CanonicalIntakeAssistedEntryRequestReceipt] | NativeReceiptNotObserved,
                responses=errors)
    def assisted_entry_receipt(intake_id: UUID, request: Request, response: Response,
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")],
    ):
        with _http_errors(), _assisted_errors():
            principal, repo = context(request)
            flags = read_flag_owner(request, repo)
            owner = NativeAssistedEntryRequestOwner(repo.graph)
            with repo.graph.transaction(principal.tenant_id):
                before = NativeIntakeCommandBudget(owner.source.source)
                before()
                row = repo.get(principal.tenant_id, str(intake_id))
                authorize(request, principal, repo, row, flag_owner=flags)
                before()
                receipt = owner.read_receipt(principal, str(intake_id), key, before=before)
                observed = _receipt_observation(CanonicalIntakeAssistedEntryRequestReceipt, receipt, 202, expected_status=202)
                before()
                response.headers["Cache-Control"] = "no-store"
                return observed

    def withdraw_command(intake_id, request_id, body, request, response, key, if_match, kind):
        errors_context = _recheck_errors if kind == "SOURCE_POLICY_RECHECK" else _assisted_errors
        with _http_errors(), errors_context():
            principal, repo = context(request)
            receipt, replayed = NativeIntakeCommandWithdrawalOwner(repo.graph).cancel(principal, str(intake_id), str(request_id),
                request_kind=kind, expected_version=int(if_match[3:-1]), reason=body.reason, idempotency_key=key)
            response.headers["ETag"] = f'W/"{receipt["version"]}"'
            response.headers["Idempotency-Replayed"] = str(replayed).lower()
            return receipt

    @router.post("/canonical-intakes/{intake_id}/assisted-entry-completions/{request_id}/cancel",
                 operation_id="withdrawCanonicalIntakeAssistedEntryRequest",
                 response_model=CanonicalIntakeCommandWithdrawalReceipt, responses=errors)
    def withdraw_assisted_entry_request(intake_id: UUID, request_id: UUID,
        body: CancelCanonicalIntakeAuthorizationWaitRequest, request: Request, response: Response,
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")],
        if_match: Annotated[str, Header(alias="If-Match", pattern=r'^W/"[1-9][0-9]*"$')],
    ):
        return withdraw_command(intake_id, request_id, body, request, response, key, if_match, "ASSISTED_ENTRY")

    @router.post("/canonical-intakes/{intake_id}/source-policy-rechecks/{request_id}/cancel",
                 operation_id="withdrawCanonicalIntakeSourcePolicyRecheckRequest",
                 response_model=CanonicalIntakeCommandWithdrawalReceipt, responses=errors)
    def withdraw_source_policy_recheck_request(intake_id: UUID, request_id: UUID,
        body: CancelCanonicalIntakeAuthorizationWaitRequest, request: Request, response: Response,
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")],
        if_match: Annotated[str, Header(alias="If-Match", pattern=r'^W/"[1-9][0-9]*"$')],
    ):
        return withdraw_command(intake_id, request_id, body, request, response, key, if_match, "SOURCE_POLICY_RECHECK")

    return router
