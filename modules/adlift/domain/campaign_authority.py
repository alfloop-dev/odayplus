"""Technical bindings to existing native cases, not a second approval policy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from modules.adlift.domain.campaign import CampaignUnavailable
from modules.adlift.domain.incrementality import IncrementalityReport
from shared.audit.integrity import canonical_json


def content_digest(value: Any) -> str:
    try:
        return sha256(canonical_json(value).encode()).hexdigest()
    except (TypeError, ValueError, OverflowError) as exc:
        raise CampaignUnavailable("CAMPAIGN_CANONICAL_CONTENT_INVALID") from exc


@dataclass(frozen=True)
class CampaignReadiness:
    tenant_id: str
    campaign_id: str
    configuration_revision: int
    configuration_sha256: str
    declared_data_version: str
    report: IncrementalityReport
    report_sha256: str
    analysis_store_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class NativeBudgetBinding:
    tenant_id: str
    campaign_id: str
    configuration_revision: int
    configuration_sha256: str
    proposal_revision: int
    declared_data_version: str
    treatment_store_ids: tuple[str, ...]
    before_authorization_revision: int | None
    before_amount: str | None
    after_amount: str
    currency: str
    readiness_sha256: str

    @property
    def is_increase(self) -> bool:
        # None is unapproved, not an approved zero. A first allocation still
        # requires every original per-store native approval below.
        return self.before_amount is not None and Decimal(self.after_amount) > Decimal(self.before_amount)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "adlift.native-budget-binding.v1", **self.__dict__,
                "treatment_store_ids": list(self.treatment_store_ids)}

    @property
    def digest(self) -> str:
        return content_digest(self.to_dict())


@dataclass(frozen=True)
class NativeCampaignRound:
    version: int
    binding: NativeBudgetBinding
    # Each identity is created by the existing native workflow, never a body
    # APPROVED flag or caller-supplied list of supposed approval records.
    cases: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CampaignBudgetAuthorization:
    revision: int
    binding: NativeBudgetBinding
    # store_id, native intervention_id, actual approved version, actual audit id
    native_approval_references: tuple[tuple[str, str, int, str], ...]
    authorized_by: str
    authorized_at: datetime
    correlation_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"revision": self.revision, "binding": self.binding.to_dict(),
                "native_approval_references": [list(r) for r in self.native_approval_references],
                "authorized_by": self.authorized_by, "authorized_at": self.authorized_at.isoformat(),
                "correlation_id": self.correlation_id,
                "external_execution_acknowledged": False}


@dataclass(frozen=True)
class CampaignBudgetHistory:
    version: int
    authorizations: tuple[CampaignBudgetAuthorization, ...]
