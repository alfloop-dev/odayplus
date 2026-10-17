"""Campaign configuration, distinct from observed spend and spend authority.

Stage 1 persists only DRAFT configuration and proposed budgets. No value here
is an approval, an authorized zero baseline, or a marketing-platform receipt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


class CampaignInvalid(ValueError):
    """Invalid campaign input or retained configuration."""


class CampaignConflict(RuntimeError):
    """The expected configuration revision is no longer current."""


class CampaignUnavailable(RuntimeError):
    """Canonical campaign persistence or its required owners are unavailable."""


class CampaignNotFound(LookupError):
    """No campaign is visible within the selected tenant partition."""


class CampaignForbidden(PermissionError):
    """The current principal lacks campaign or target-store authority."""


def required_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise CampaignInvalid("CAMPAIGN_INPUT_INVALID")
    return value.strip()


def budget_amount(value: Any) -> str:
    # Plain decimal text avoids lossy JSON float coercion and exponent overflow.
    if (not isinstance(value, str) or len(value) > 64
            or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) is None):
        raise CampaignInvalid("CAMPAIGN_BUDGET_INVALID")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise CampaignInvalid("CAMPAIGN_BUDGET_INVALID") from exc
    if not amount.is_finite() or amount < 0:
        raise CampaignInvalid("CAMPAIGN_BUDGET_INVALID")
    return format(amount, "f")


def budget_currency(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Z]{3}", value) is None:
        raise CampaignInvalid("CAMPAIGN_BUDGET_CURRENCY_REQUIRED")
    return value


def campaign_period(start: Any, end: Any) -> tuple[date, date]:
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError) as exc:
        raise CampaignInvalid("CAMPAIGN_PERIOD_INVALID") from exc
    if first.isoformat() != start or last.isoformat() != end or last < first:
        raise CampaignInvalid("CAMPAIGN_PERIOD_INVALID")
    return first, last


@dataclass(frozen=True)
class BudgetProposal:
    revision: int
    amount: str
    currency: str
    declared_data_version: str
    reason: str
    proposed_by: str
    proposed_at: datetime
    correlation_id: str
    based_on_authorized_budget_revision: int | None = None
    authorized_before_amount: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "amount": self.amount,
            "currency": self.currency,
            "declared_data_version": self.declared_data_version,
            "reason": self.reason,
            "proposed_by": self.proposed_by,
            "proposed_at": self.proposed_at.isoformat(),
            "correlation_id": self.correlation_id,
            # Missing authorization is deliberately not an authorized zero.
            "based_on_authorized_budget_revision": self.based_on_authorized_budget_revision,
            "authorized_before_amount": self.authorized_before_amount,
        }


@dataclass(frozen=True)
class CampaignConfiguration:
    tenant_id: str
    campaign_id: str
    revision: int
    name: str
    channel: str
    treatment_store_ids: tuple[str, ...]
    period_start: date
    period_end: date
    creative_reference: str | None
    proposals: tuple[BudgetProposal, ...]
    created_by: str
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "campaign_id": self.campaign_id,
            "revision": self.revision,
            "name": self.name,
            "channel": self.channel,
            "treatment_store_ids": list(self.treatment_store_ids),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "creative_reference": self.creative_reference,
            "status": "DRAFT",
            "budget_proposals": [p.to_dict() for p in self.proposals],
            "authorized_budget_revision": None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "status_history": [
                {"from_status": None if p.revision == 1 else "DRAFT", "to_status": "DRAFT",
                 "actor": p.proposed_by, "reason": p.reason,
                 "occurred_at": p.proposed_at.isoformat(), "correlation_id": p.correlation_id,
                 "configuration_revision": p.revision}
                for p in self.proposals
            ],
        }


def validate_configuration(value: Any, *, tenant_id: str, campaign_id: str,
                           revision: int) -> CampaignConfiguration:
    """Reject corrupt or foreign retained data before it becomes a response."""
    if (not isinstance(value, CampaignConfiguration)
            or value.tenant_id != tenant_id or value.campaign_id != campaign_id
            or type(value.revision) is not int or value.revision != revision
            or revision < 1 or not isinstance(value.proposals, tuple)
            or len(value.proposals) != revision
            or not isinstance(value.treatment_store_ids, tuple)
            or not value.treatment_store_ids
            or any(not isinstance(s, str) for s in value.treatment_store_ids)
            or len(set(value.treatment_store_ids)) != len(value.treatment_store_ids)):
        raise CampaignUnavailable("CAMPAIGN_STATE_INVALID")
    try:
        for field in (value.tenant_id, value.campaign_id, value.name, value.channel,
                      value.created_by, *value.treatment_store_ids):
            required_text(field)
        if value.creative_reference is not None:
            required_text(value.creative_reference)
        campaign_period(value.period_start.isoformat(), value.period_end.isoformat())
        if not isinstance(value.created_at, datetime) or value.created_at.tzinfo is None:
            raise CampaignInvalid("CAMPAIGN_TIME_INVALID")
        first_currency = None
        for index, proposal in enumerate(value.proposals, start=1):
            if (not isinstance(proposal, BudgetProposal)
                    or type(proposal.revision) is not int or proposal.revision != index):
                raise CampaignInvalid("CAMPAIGN_PROPOSAL_INVALID")
            budget_amount(proposal.amount)
            if proposal.based_on_authorized_budget_revision is None:
                if proposal.authorized_before_amount is not None:
                    raise CampaignInvalid("CAMPAIGN_AUTHORIZED_BASELINE_INVALID")
            elif (type(proposal.based_on_authorized_budget_revision) is not int
                    or proposal.based_on_authorized_budget_revision < 1
                    or proposal.authorized_before_amount is None):
                raise CampaignInvalid("CAMPAIGN_AUTHORIZED_BASELINE_INVALID")
            else:
                budget_amount(proposal.authorized_before_amount)
            currency = budget_currency(proposal.currency)
            if first_currency is not None and currency != first_currency:
                raise CampaignInvalid("CAMPAIGN_BUDGET_CURRENCY_CONFLICT")
            first_currency = currency
            for field in (proposal.reason, proposal.proposed_by, proposal.declared_data_version,
                          proposal.correlation_id):
                required_text(field)
            if not isinstance(proposal.proposed_at, datetime) or proposal.proposed_at.tzinfo is None:
                raise CampaignInvalid("CAMPAIGN_TIME_INVALID")
    except (CampaignInvalid, AttributeError, TypeError, ValueError) as exc:
        raise CampaignUnavailable("CAMPAIGN_STATE_INVALID") from exc
    return value
