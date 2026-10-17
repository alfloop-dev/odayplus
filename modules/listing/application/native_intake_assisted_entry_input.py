"""Closed human proposals and a truthful, deterministic manual JSON document.

Validation does not authorize a human, source or transition. The SQL owner
must create PROPOSED corrections and reread them before using this renderer.
No parser output, confidence or effective identity change is manufactured.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from modules.external_data.security.assisted_listing_retrieval import (
    contains_sensitive_submission_material,
)
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)

MAX_ASSISTED_COMMAND_BYTES = 16_384
REQUIRED_FIELDS = frozenset({"address", "rent", "areaPing"})
IDENTITY_FIELDS = REQUIRED_FIELDS | {"providerListingId"}


def _safe_text(value: str) -> str:
    # These are typed fields, not an HTML/file ingress. Preserve accepted human
    # text exactly; normalisation and effective values belong to later stages.
    if (not value.strip() or any(ord(c) < 32 or c in "<>" for c in value)
            or contains_sensitive_submission_material({"value": value})):
        raise ValueError("NATIVE_ASSISTED_TYPED_TEXT_REQUIRED")
    return value


class NativeAssistedEntryFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    address: str = Field(min_length=1, max_length=512)
    rent: float = Field(gt=0)
    areaPing: float = Field(gt=0)
    providerListingId: str | None = Field(default=None, min_length=1, max_length=128)
    floor: str | None = Field(default=None, min_length=1, max_length=128)
    listingType: str | None = Field(default=None, min_length=1, max_length=128)
    listingStatus: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("address", "providerListingId", "floor", "listingType", "listingStatus")
    @classmethod
    def safe_typed_text(cls, value):
        return _safe_text(value) if value is not None else None

    def values(self) -> dict:
        return self.model_dump(mode="json", exclude_none=True)

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.values())


class NativeAssistedEntryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    fields: NativeAssistedEntryFields
    reason: str = Field(min_length=3, max_length=4000)
    risk_summary: str = Field(min_length=3, max_length=4000)
    risk_acknowledged: bool

    @field_validator("reason", "risk_summary")
    @classmethod
    def safe_explicit_reason(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("NATIVE_ASSISTED_EXPLICIT_REASON_REQUIRED")
        return _safe_text(value)

    @model_validator(mode="after")
    def acknowledged_and_bounded(self):
        if self.risk_acknowledged is not True:
            raise ValueError("NATIVE_ASSISTED_RISK_ACKNOWLEDGEMENT_REQUIRED")
        if len(canonical_json(self.model_dump(mode="json")).encode()) > MAX_ASSISTED_COMMAND_BYTES:
            raise ValueError("NATIVE_ASSISTED_COMMAND_TOO_LARGE")
        return self

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))

    def correction_reason(self) -> str:
        """Keep the human risk acknowledgement in the installed reason column.

        No extra correction column or raw-input store is assumed. The request
        audit can bind this exact reason by hash, without copying field values.
        All accepted reason text is preserved in this human-readable format.
        """
        return ("Native assisted-entry proposal v1\nReason: " + self.reason
                + "\nRisk acknowledged: yes\nRisk summary: " + self.risk_summary)


def manual_evidence_document(*, tenant_id: str, intake_id: str, request_id: str,
                             requested_by: str, source_id: str,
                             fields: NativeAssistedEntryFields,
                             correction_ids: Mapping[str, str]) -> bytes:
    """Render only the exact frozen, reread proposal set supplied by the owner.

    Embedding request/correction identities prevents one legitimate request
    from adopting another request's equal-field snapshot natural key.
    """
    for value in (tenant_id, intake_id, request_id, requested_by):
        native_intake_uuid(value)
    if (not isinstance(source_id, str) or not 1 <= len(source_id) <= 256
            or any(ord(c) < 32 for c in source_id)
            or not isinstance(fields, NativeAssistedEntryFields)
            or not isinstance(correction_ids, Mapping)):
        raise ValueError("NATIVE_ASSISTED_DOCUMENT_INPUT_REJECTED")
    values = fields.values()
    if set(correction_ids) != set(values):
        raise ValueError("NATIVE_ASSISTED_CORRECTION_SET_REJECTED")
    ids = [native_intake_uuid(value) for value in correction_ids.values()]
    if len(set(ids)) != len(ids):
        raise ValueError("NATIVE_ASSISTED_CORRECTION_SET_REJECTED")
    return canonical_json({
        "schema_version": 1, "capture_method": "MANUAL_ENTRY", "tenant_id": tenant_id,
        "intake_id": intake_id, "request_id": request_id, "requested_by": requested_by,
        "source_id": source_id, "fields_sha256": fields.sha256,
        "corrections": [{"correction_id": correction_ids[name], "field_path": name,
                         "corrected_value": values[name]} for name in sorted(values)],
    }).encode()
