"""Canonical contract definition for odayplus.site-economics.v1.

Exposes the decision-ready site economics artifact for downstream consumption
by SiteScore v3 (ODP-SITESCORE-V3-001) and NetPlan/OpsBoard (ODP-NETPLAN-001).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.site_economics.domain.models import (
    DecisionAssessment,
    FinancialMetricsSummary,
    MonthlyCashFlowItem,
)

CONTRACT_ID = "odayplus.site-economics.v1"
CONTRACT_VERSION = "1.0.0"
CONTRACT_CATEGORY = "decision_product"
ENGINE_VERSION = "site-economics-simulator-v1.0.0"


@dataclass(frozen=True, slots=True)
class SimulationAssumptionsSnapshot:
    """Audit snapshot of all economic and financial assumptions applied."""

    format_code: str
    format_version: str
    monthly_base_rent: float
    area_ping: float
    total_equipment_capex: float
    total_fitout_capex: float
    total_initial_cash_outlay: float
    debt_ratio: float
    annual_interest_rate: float
    loan_term_months: int
    corporate_tax_rate: float
    equipment_salvage_ratio: float
    demand_multiplier: float
    competitor_discount: float
    cannibalization_discount: float
    lease_term_months: int
    lease_deposit_months: int
    rent_free_months: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SimulationAssumptionsSnapshot:
        return cls(
            format_code=str(data["format_code"]),
            format_version=str(data["format_version"]),
            monthly_base_rent=float(data["monthly_base_rent"]),
            area_ping=float(data["area_ping"]),
            total_equipment_capex=float(data["total_equipment_capex"]),
            total_fitout_capex=float(data["total_fitout_capex"]),
            total_initial_cash_outlay=float(data["total_initial_cash_outlay"]),
            debt_ratio=float(data["debt_ratio"]),
            annual_interest_rate=float(data["annual_interest_rate"]),
            loan_term_months=int(data["loan_term_months"]),
            corporate_tax_rate=float(data["corporate_tax_rate"]),
            equipment_salvage_ratio=float(data["equipment_salvage_ratio"]),
            demand_multiplier=float(data.get("demand_multiplier", 1.0)),
            competitor_discount=float(data.get("competitor_discount", 0.0)),
            cannibalization_discount=float(data.get("cannibalization_discount", 0.0)),
            lease_term_months=int(data.get("lease_term_months", 60)),
            lease_deposit_months=int(data.get("lease_deposit_months", 2)),
            rent_free_months=int(data.get("rent_free_months", 1)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_code": self.format_code,
            "format_version": self.format_version,
            "monthly_base_rent": self.monthly_base_rent,
            "area_ping": self.area_ping,
            "total_equipment_capex": self.total_equipment_capex,
            "total_fitout_capex": self.total_fitout_capex,
            "total_initial_cash_outlay": self.total_initial_cash_outlay,
            "debt_ratio": self.debt_ratio,
            "annual_interest_rate": self.annual_interest_rate,
            "loan_term_months": self.loan_term_months,
            "corporate_tax_rate": self.corporate_tax_rate,
            "equipment_salvage_ratio": self.equipment_salvage_ratio,
            "demand_multiplier": self.demand_multiplier,
            "competitor_discount": self.competitor_discount,
            "cannibalization_discount": self.cannibalization_discount,
            "lease_term_months": self.lease_term_months,
            "lease_deposit_months": self.lease_deposit_months,
            "rent_free_months": self.rent_free_months,
        }


@dataclass(frozen=True, slots=True)
class ScenarioSummary:
    """Summary metrics for a specific sensitivity scenario."""

    scenario_name: str
    description: str
    demand_multiplier: float
    competitor_discount: float
    monthly_rent: float
    levered_npv: float
    unlevered_npv: float
    levered_irr: float | None
    simple_payback_months: float | None
    is_payback_censored: bool
    average_monthly_ebitda: float
    recommendation: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ScenarioSummary:
        return cls(
            scenario_name=str(data["scenario_name"]),
            description=str(data.get("description", "")),
            demand_multiplier=float(data["demand_multiplier"]),
            competitor_discount=float(data["competitor_discount"]),
            monthly_rent=float(data["monthly_rent"]),
            levered_npv=float(data["levered_npv"]),
            unlevered_npv=float(data["unlevered_npv"]),
            levered_irr=(
                float(data["levered_irr"]) if data.get("levered_irr") is not None else None
            ),
            simple_payback_months=(
                float(data["simple_payback_months"])
                if data.get("simple_payback_months") is not None
                else None
            ),
            is_payback_censored=bool(data["is_payback_censored"]),
            average_monthly_ebitda=float(data["average_monthly_ebitda"]),
            recommendation=str(data["recommendation"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "description": self.description,
            "demand_multiplier": self.demand_multiplier,
            "competitor_discount": self.competitor_discount,
            "monthly_rent": self.monthly_rent,
            "levered_npv": self.levered_npv,
            "unlevered_npv": self.unlevered_npv,
            "levered_irr": self.levered_irr,
            "simple_payback_months": self.simple_payback_months,
            "is_payback_censored": self.is_payback_censored,
            "average_monthly_ebitda": self.average_monthly_ebitda,
            "recommendation": self.recommendation,
        }


@dataclass(frozen=True, slots=True)
class SiteEconomicsDocument:
    """Root canonical document for contract ."""

    document_id: str
    site_id: str
    tenant_id: str
    format_code: str
    format_version: str
    evaluated_at: str
    horizon_months: int
    annual_discount_rate: float
    metrics: FinancialMetricsSummary
    decision: DecisionAssessment
    assumptions: SimulationAssumptionsSnapshot
    monthly_schedule: tuple[MonthlyCashFlowItem, ...]
    contract_id: str = CONTRACT_ID
    contract_version: str = CONTRACT_VERSION
    source_market_context_id: str | None = None
    source_market_context_sha256: str | None = None
    scenarios: dict[str, ScenarioSummary] = field(default_factory=dict)
    engine_version: str = ENGINE_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        """Deterministic SHA256 digest of document payload."""
        payload = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SiteEconomicsDocument:
        scenarios = {k: ScenarioSummary.from_dict(v) for k, v in data.get("scenarios", {}).items()}
        schedule = tuple(MonthlyCashFlowItem.from_dict(item) for item in data["monthly_schedule"])
        return cls(
            document_id=str(data.get("document_id", str(uuid4()))),
            site_id=str(data["site_id"]),
            tenant_id=str(data.get("tenant_id", "")),
            format_code=str(data["format_code"]),
            format_version=str(data.get("format_version", "1.0.0")),
            evaluated_at=str(data.get("evaluated_at", datetime.now(UTC).isoformat())),
            horizon_months=int(data.get("horizon_months", 60)),
            annual_discount_rate=float(data.get("annual_discount_rate", 0.08)),
            metrics=FinancialMetricsSummary.from_dict(data["metrics"]),
            decision=DecisionAssessment.from_dict(data["decision"]),
            assumptions=SimulationAssumptionsSnapshot.from_dict(data["assumptions"]),
            monthly_schedule=schedule,
            contract_id=str(data.get("contract_id", CONTRACT_ID)),
            contract_version=str(data.get("contract_version", CONTRACT_VERSION)),
            source_market_context_id=data.get("source_market_context_id"),
            source_market_context_sha256=data.get("source_market_context_sha256"),
            scenarios=scenarios,
            engine_version=str(data.get("engine_version", ENGINE_VERSION)),
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "document_id": self.document_id,
            "site_id": self.site_id,
            "tenant_id": self.tenant_id,
            "format_code": self.format_code,
            "format_version": self.format_version,
            "evaluated_at": self.evaluated_at,
            "horizon_months": self.horizon_months,
            "annual_discount_rate": self.annual_discount_rate,
            "source_market_context_id": self.source_market_context_id,
            "source_market_context_sha256": self.source_market_context_sha256,
            "metrics": self.metrics.to_dict(),
            "decision": self.decision.to_dict(),
            "assumptions": self.assumptions.to_dict(),
            "monthly_schedule": [item.to_dict() for item in self.monthly_schedule],
            "scenarios": {k: v.to_dict() for k, v in self.scenarios.items()},
            "engine_version": self.engine_version,
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> SiteEconomicsDocument:
        return cls.from_dict(json.loads(json_str))


def validate_site_economics_document(doc: SiteEconomicsDocument | Mapping[str, Any]) -> None:
    """Validates document structure against the odayplus.site-economics.v1 contract requirements."""
    data = doc.to_dict() if isinstance(doc, SiteEconomicsDocument) else doc
    if data.get("contract_id") != CONTRACT_ID:
        raise ValueError(
            f"Invalid contract_id: expected '{CONTRACT_ID}', got '{data.get('contract_id')}'"
        )
    if not data.get("site_id"):
        raise ValueError("site_id is required")
    if not data.get("format_code"):
        raise ValueError("format_code is required")
    if "metrics" not in data or "decision" not in data:
        raise ValueError("metrics and decision sections are required")
    if "monthly_schedule" not in data or len(data["monthly_schedule"]) == 0:
        raise ValueError("monthly_schedule must contain at least 1 month")
