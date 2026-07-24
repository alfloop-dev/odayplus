from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from importlib.metadata import version
from typing import Any
from uuid import uuid4

import numpy as np

# Versions (output-contract principle §5.1 of ODP-MOD-07).
ADLIFT_MODEL_VERSION = "adlift-statsmodels-matched-did-v2"
ADLIFT_FEATURE_VERSION = "matched-control-view-v1"
ADLIFT_EVIDENCE_POLICY_VERSION = "causal-evidence-level-v1"
ADLIFT_MEASUREMENT_METHOD = "DID"  # difference-in-differences (ODP-ML-05 §9)

# Parallel-trends tolerance: max divergence between treatment and control
# pre-period normalised daily growth rates before the pre-trend test fails.
DEFAULT_PRE_TREND_THRESHOLD = 0.01
MIN_PRE_TREND_POINTS = 2

# IROMI = incremental gross margin / ad spend (ODP-ML-05 §4, AC-07-04).
SCALE_IROMI = 1.5
CONTINUE_IROMI = 1.0

# Two-sided confidence level for the matched-pair DiD effect interval (90% CI).
DID_EFFECT_CI_ALPHA = 0.10


class AdLiftProductionExecutionError(RuntimeError):
    """Raised when production DiD data or the statsmodels runtime is unavailable."""


class EvidenceLevel(StrEnum):
    """Causal evidence ladder (ODP-ML-05 §5). v1 produces L0–L3 only."""

    L0_ANECDOTAL = "L0"  # only anecdotal / no usable treatment data
    L1_BEFORE_AFTER = "L1"  # before/after, no control group
    L2_MATCHED_DESCRIPTIVE = "L2"  # matched control but pre-trend/balance not clean
    L3_DID_VALIDATED = "L3"  # control + pre-trend + balance checks pass
    L4_RANDOMIZED = "L4"  # experimental / near-random (out of v1 scope)
    L5_POLICY_READY = "L5"  # replicated, policy ready (out of v1 scope)


# Ordering for ladder comparisons; causal claims require >= L3 (ODP-ML-05 §5).
_EVIDENCE_ORDER: tuple[EvidenceLevel, ...] = (
    EvidenceLevel.L0_ANECDOTAL,
    EvidenceLevel.L1_BEFORE_AFTER,
    EvidenceLevel.L2_MATCHED_DESCRIPTIVE,
    EvidenceLevel.L3_DID_VALIDATED,
    EvidenceLevel.L4_RANDOMIZED,
    EvidenceLevel.L5_POLICY_READY,
)
CAUSAL_MIN_EVIDENCE = EvidenceLevel.L3_DID_VALIDATED


def is_causal_evidence(level: EvidenceLevel) -> bool:
    return _EVIDENCE_ORDER.index(level) >= _EVIDENCE_ORDER.index(CAUSAL_MIN_EVIDENCE)


class PreTrendStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"  # not enough pre-period points to test
    NOT_TESTED = "NOT_TESTED"  # no control group to test against


class Recommendation(StrEnum):
    CONTINUE = "CONTINUE"
    SCALE = "SCALE"
    STOP = "STOP"
    CHANGE_CHANNEL = "CHANGE_CHANNEL"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class StoreDayMetric:
    store_id: str
    business_date: date
    revenue: float
    gross_margin: float = 0.0
    ad_spend: float = 0.0
    active_intervention_ids: tuple[str, ...] = ()
    source_snapshot_ids: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> StoreDayMetric:
        revenue = float(_first_present(data, "revenue", "actual_revenue", default=0.0))
        gross_margin = data.get("gross_margin")
        if gross_margin is None:
            rate = data.get("gross_margin_rate")
            gross_margin = revenue * float(rate) if rate is not None else 0.0
        return cls(
            store_id=str(data["store_id"]),
            business_date=_parse_date(data.get("business_date") or data.get("date")),
            revenue=revenue,
            gross_margin=float(gross_margin),
            ad_spend=float(_first_present(data, "ad_spend", "spend", default=0.0)),
            active_intervention_ids=tuple(
                str(value) for value in data.get("active_intervention_ids", ())
            ),
            source_snapshot_ids=tuple(str(value) for value in data.get("source_snapshot_ids", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "business_date": self.business_date.isoformat(),
            "revenue": self.revenue,
            "gross_margin": self.gross_margin,
            "ad_spend": self.ad_spend,
            "active_intervention_ids": list(self.active_intervention_ids),
            "source_snapshot_ids": list(self.source_snapshot_ids),
        }


@dataclass(frozen=True)
class AdCampaign:
    """An ad campaign with its treatment scope, control candidates and panel.

    Captures channel, budget (ad_spend), period and treatment scope (AC-07-01).
    """

    campaign_id: str
    name: str
    treatment_store_ids: tuple[str, ...]
    candidate_control_store_ids: tuple[str, ...]
    pre_period_start: date
    pre_period_end: date
    campaign_period_start: date
    campaign_period_end: date
    ad_spend: float
    observations: tuple[StoreDayMetric, ...]
    channel: str = "paid_search"
    audience: str | None = None
    creative: str | None = None
    # The ad intervention's own id; excluded when scanning for contamination.
    campaign_intervention_id: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> AdCampaign:
        observations = tuple(
            _coerce_metric(item) for item in data.get("observations", data.get("metrics", ()))
        )
        return cls(
            campaign_id=str(data["campaign_id"]),
            name=str(data.get("name") or data["campaign_id"]),
            treatment_store_ids=tuple(str(value) for value in data.get("treatment_store_ids", ())),
            candidate_control_store_ids=tuple(
                str(value) for value in data.get("candidate_control_store_ids", ())
            ),
            pre_period_start=_parse_date(data["pre_period_start"]),
            pre_period_end=_parse_date(data["pre_period_end"]),
            campaign_period_start=_parse_date(data["campaign_period_start"]),
            campaign_period_end=_parse_date(data["campaign_period_end"]),
            ad_spend=float(_first_present(data, "ad_spend", "spend", default=0.0)),
            observations=observations,
            channel=str(data.get("channel") or "paid_search"),
            audience=_optional_str(data.get("audience")),
            creative=_optional_str(data.get("creative")),
            campaign_intervention_id=_optional_str(data.get("campaign_intervention_id")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "name": self.name,
            "channel": self.channel,
            "audience": self.audience,
            "creative": self.creative,
            "ad_spend": self.ad_spend,
            "treatment_store_ids": list(self.treatment_store_ids),
            "candidate_control_store_ids": list(self.candidate_control_store_ids),
            "pre_period": {
                "start": self.pre_period_start.isoformat(),
                "end": self.pre_period_end.isoformat(),
            },
            "campaign_period": {
                "start": self.campaign_period_start.isoformat(),
                "end": self.campaign_period_end.isoformat(),
            },
        }


@dataclass(frozen=True)
class MatchedControl:
    treatment_store_id: str
    control_store_id: str
    match_distance: float
    treatment_pre_avg: float
    control_pre_avg: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "treatment_store_id": self.treatment_store_id,
            "control_store_id": self.control_store_id,
            "match_distance": self.match_distance,
            "treatment_pre_avg": self.treatment_pre_avg,
            "control_pre_avg": self.control_pre_avg,
        }


@dataclass(frozen=True)
class PreTrendResult:
    status: PreTrendStatus
    treatment_slope: float
    control_slope: float
    slope_divergence: float
    threshold: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "treatment_slope": self.treatment_slope,
            "control_slope": self.control_slope,
            "slope_divergence": self.slope_divergence,
            "threshold": self.threshold,
        }


@dataclass(frozen=True)
class ContaminationFinding:
    store_id: str
    role: str  # "treatment" | "control"
    intervention_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "role": self.role,
            "intervention_ids": list(self.intervention_ids),
        }


@dataclass(frozen=True)
class EffectInterval:
    """Confidence interval for the per-store-day matched-pair gross-margin effect.

    For the DiD design ``low``/``high`` are a t-based confidence interval around
    the mean pairwise effect and ``standard_error`` is its OLS standard error;
    for the non-causal before/after fallback the interval collapses to the point
    estimate.
    """

    metric: str
    low: float
    point: float
    high: float
    standard_error: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "low": self.low,
            "point": self.point,
            "high": self.high,
            "standard_error": self.standard_error,
        }


@dataclass(frozen=True)
class IncrementalityEstimate:
    surface_revenue: float
    surface_gross_margin: float
    incremental_revenue: float
    incremental_gross_margin: float
    effect_interval: EffectInterval
    estimator_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IncrementalityReport:
    report_id: str
    campaign_id: str
    campaign_name: str
    channel: str
    treatment_store_ids: tuple[str, ...]
    control_store_ids: tuple[str, ...]
    matched_controls: tuple[MatchedControl, ...]
    pre_trend_status: PreTrendStatus
    pre_trend: PreTrendResult
    measurement_method: str
    surface_revenue: float
    incremental_revenue: float
    incremental_gross_margin: float
    effect_interval: EffectInterval
    iromi: float
    ad_spend: float
    evidence_level: EvidenceLevel
    causal_claim_allowed: bool
    recommendation: Recommendation
    contamination: tuple[ContaminationFinding, ...]
    intervention_writeback: dict[str, Any]
    label_registry_entry: dict[str, Any]
    model_version: str
    feature_version: str
    policy_version: str
    generated_at: datetime
    source_snapshot_ids: tuple[str, ...] = ()
    estimator_metadata: dict[str, Any] = field(default_factory=dict)
    report_version: int = 1

    def with_version(self, *, report_version: int, report_id: str) -> IncrementalityReport:
        return IncrementalityReport(
            **{**self.__dict__, "report_version": report_version, "report_id": report_id}
        )

    def to_report_card(self) -> dict[str, Any]:
        """Project onto the ``AdLiftReportCard`` contract (component contracts §5.9)."""
        return {
            "campaign": self.campaign_name,
            "treatmentStores": list(self.treatment_store_ids),
            "controlStores": list(self.control_store_ids),
            "preTrendStatus": self.pre_trend_status.value,
            "incrementalRevenue": self.incremental_revenue,
            "incrementalGrossMargin": self.incremental_gross_margin,
            "iromi": self.iromi,
            "evidenceLevel": self.evidence_level.value,
            "continueStopRecommendation": self.recommendation.value,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "report_version": self.report_version,
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "channel": self.channel,
            "treatment_store_ids": list(self.treatment_store_ids),
            "control_store_ids": list(self.control_store_ids),
            "matched_controls": [match.to_dict() for match in self.matched_controls],
            "pre_trend_status": self.pre_trend_status.value,
            "pre_trend": self.pre_trend.to_dict(),
            "measurement_method": self.measurement_method,
            "surface_revenue": self.surface_revenue,
            "incremental_revenue": self.incremental_revenue,
            "incremental_gross_margin": self.incremental_gross_margin,
            "effect_interval": self.effect_interval.to_dict(),
            "estimator_metadata": self.estimator_metadata,
            "iromi": self.iromi,
            "ad_spend": self.ad_spend,
            "evidence_level": self.evidence_level.value,
            "causal_claim_allowed": self.causal_claim_allowed,
            "recommendation": self.recommendation.value,
            "contamination": [finding.to_dict() for finding in self.contamination],
            "intervention_writeback": self.intervention_writeback,
            "label_registry_entry": self.label_registry_entry,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "policy_version": self.policy_version,
            "generated_at": self.generated_at.isoformat(),
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "report_card": self.to_report_card(),
        }


def match_controls(
    campaign: AdCampaign,
) -> tuple[list[MatchedControl], dict[str, list[StoreDayMetric]]]:
    """Greedy 1:1 nearest-pre-average matching of treatment to candidate controls.

    Returns the matched pairs plus the campaign observations grouped by store, so
    callers do not regroup. Controls are matched without replacement; once
    candidates run out, remaining treatment stores are left unmatched (ODP-ML-05 §8).
    """
    by_store = _group_by_store(campaign.observations)
    treatment_pre = {
        store_id: _mean(_revenues(by_store.get(store_id, []), campaign, period="pre"))
        for store_id in campaign.treatment_store_ids
    }
    control_pre = {
        store_id: _mean(_revenues(by_store.get(store_id, []), campaign, period="pre"))
        for store_id in campaign.candidate_control_store_ids
    }

    matches: list[MatchedControl] = []
    used: set[str] = set()
    for treatment_store_id in campaign.treatment_store_ids:
        treatment_avg = treatment_pre[treatment_store_id]
        best_control: str | None = None
        best_distance = float("inf")
        for control_store_id in campaign.candidate_control_store_ids:
            if control_store_id in used:
                continue
            distance = abs(treatment_avg - control_pre[control_store_id])
            if distance < best_distance:
                best_distance = distance
                best_control = control_store_id
        if best_control is None:
            continue
        used.add(best_control)
        matches.append(
            MatchedControl(
                treatment_store_id=treatment_store_id,
                control_store_id=best_control,
                match_distance=round(best_distance, 4),
                treatment_pre_avg=round(treatment_avg, 2),
                control_pre_avg=round(control_pre[best_control], 2),
            )
        )
    return matches, by_store


def evaluate_pre_trend(
    campaign: AdCampaign,
    *,
    by_store: Mapping[str, list[StoreDayMetric]],
    treatment_store_ids: Sequence[str],
    control_store_ids: Sequence[str],
    threshold: float = DEFAULT_PRE_TREND_THRESHOLD,
) -> PreTrendResult:
    """Parallel-trends check on normalised pre-period daily group revenue slopes."""
    if not control_store_ids:
        return PreTrendResult(
            status=PreTrendStatus.NOT_TESTED,
            treatment_slope=0.0,
            control_slope=0.0,
            slope_divergence=0.0,
            threshold=threshold,
        )
    treatment_days = _group_daily_means(campaign, by_store, treatment_store_ids, "pre")
    control_days = _group_daily_means(campaign, by_store, control_store_ids, "pre")
    if len(treatment_days) < MIN_PRE_TREND_POINTS or len(control_days) < MIN_PRE_TREND_POINTS:
        return PreTrendResult(
            status=PreTrendStatus.INCONCLUSIVE,
            treatment_slope=0.0,
            control_slope=0.0,
            slope_divergence=0.0,
            threshold=threshold,
        )
    treatment_slope = _normalised_slope([value for _, value in treatment_days])
    control_slope = _normalised_slope([value for _, value in control_days])
    divergence = abs(treatment_slope - control_slope)
    status = PreTrendStatus.PASS if divergence <= threshold else PreTrendStatus.FAIL
    return PreTrendResult(
        status=status,
        treatment_slope=round(treatment_slope, 6),
        control_slope=round(control_slope, 6),
        slope_divergence=round(divergence, 6),
        threshold=threshold,
    )


def detect_contamination(
    campaign: AdCampaign,
    *,
    by_store: Mapping[str, list[StoreDayMetric]],
    treatment_store_ids: Sequence[str],
    control_store_ids: Sequence[str],
) -> list[ContaminationFinding]:
    """Flag stores carrying non-ad interventions inside the campaign window.

    Overlapping interventions make the effect unidentifiable (ODP-ML-05 §8.3,
    §15.3) and cap the evidence level at L2.
    """
    findings: list[ContaminationFinding] = []
    roles = [(treatment_store_ids, "treatment"), (control_store_ids, "control")]
    for store_ids, role in roles:
        for store_id in store_ids:
            intervention_ids: set[str] = set()
            for metric in _in_period(by_store.get(store_id, []), campaign, period="campaign"):
                for intervention_id in metric.active_intervention_ids:
                    if intervention_id and intervention_id != campaign.campaign_intervention_id:
                        intervention_ids.add(intervention_id)
            if intervention_ids:
                findings.append(
                    ContaminationFinding(
                        store_id=store_id,
                        role=role,
                        intervention_ids=tuple(sorted(intervention_ids)),
                    )
                )
    return findings


def assign_evidence_level(
    *,
    has_treatment_data: bool,
    control_store_ids: Sequence[str],
    pre_trend_status: PreTrendStatus,
    contamination: Sequence[ContaminationFinding],
) -> EvidenceLevel:
    """Map the design quality onto the L0–L5 ladder (ODP-ML-05 §5, §8.3, AC-07-02)."""
    if not has_treatment_data:
        return EvidenceLevel.L0_ANECDOTAL
    if not control_store_ids:
        return EvidenceLevel.L1_BEFORE_AFTER
    # Matched control present. Pre-trend not cleanly passing OR a balance failure
    # (intervention overlap) caps the evidence at L2 (cannot claim causality).
    if pre_trend_status is not PreTrendStatus.PASS:
        return EvidenceLevel.L2_MATCHED_DESCRIPTIVE
    if contamination:
        return EvidenceLevel.L2_MATCHED_DESCRIPTIVE
    return EvidenceLevel.L3_DID_VALIDATED


def recommend(
    *,
    evidence_level: EvidenceLevel,
    iromi: float,
    scale_iromi: float = SCALE_IROMI,
    continue_iromi: float = CONTINUE_IROMI,
) -> Recommendation:
    """Continue/Stop/Scale call (ODP-ML-05 §15.2/§15.3).

    Below L3 the effect is not a causal estimate, so no continue/stop call is
    made — the report is INCONCLUSIVE until the design is strengthened.
    """
    if not is_causal_evidence(evidence_level):
        return Recommendation.INCONCLUSIVE
    if iromi >= scale_iromi:
        return Recommendation.SCALE
    if iromi >= continue_iromi:
        return Recommendation.CONTINUE
    return Recommendation.STOP


def run_incrementality(
    campaign: AdCampaign | Mapping[str, Any],
    *,
    generated_at: datetime | None = None,
    pre_trend_threshold: float = DEFAULT_PRE_TREND_THRESHOLD,
    scale_iromi: float = SCALE_IROMI,
    continue_iromi: float = CONTINUE_IROMI,
    require_statsmodels: bool = False,
) -> IncrementalityReport:
    campaign = _coerce_campaign(campaign)
    generated = generated_at or datetime.now(UTC)
    if require_statsmodels:
        if not campaign.candidate_control_store_ids:
            raise AdLiftProductionExecutionError(
                "production AdLift requires an eligible control group; "
                "before/after fallback is prohibited"
            )
        missing_lineage = [
            f"{metric.store_id}:{metric.business_date.isoformat()}"
            for metric in campaign.observations
            if not metric.source_snapshot_ids
        ]
        if missing_lineage:
            raise AdLiftProductionExecutionError(
                "production AdLift observations are missing source snapshot lineage"
            )

    matches, by_store = match_controls(campaign)
    if require_statsmodels and not matches:
        raise AdLiftProductionExecutionError("production AdLift could not create matched controls")
    control_store_ids = tuple(match.control_store_id for match in matches)
    treatment_store_ids = campaign.treatment_store_ids

    pre_trend = evaluate_pre_trend(
        campaign,
        by_store=by_store,
        treatment_store_ids=treatment_store_ids,
        control_store_ids=control_store_ids,
        threshold=pre_trend_threshold,
    )
    contamination = detect_contamination(
        campaign,
        by_store=by_store,
        treatment_store_ids=treatment_store_ids,
        control_store_ids=control_store_ids,
    )

    try:
        estimate = _estimate_incrementality(
            campaign,
            by_store=by_store,
            matches=matches,
            treatment_store_ids=treatment_store_ids,
        )
    except Exception as exc:
        if require_statsmodels:
            raise AdLiftProductionExecutionError(
                "production statsmodels DiD execution failed"
            ) from exc
        raise
    if require_statsmodels and estimate.estimator_metadata.get("library") != "statsmodels":
        raise AdLiftProductionExecutionError(
            "production AdLift did not execute the statsmodels DiD contract"
        )
    iromi = (
        round(estimate.incremental_gross_margin / campaign.ad_spend, 4)
        if campaign.ad_spend > 0
        else 0.0
    )

    has_treatment_data = any(
        _in_period(by_store.get(store_id, []), campaign, period="campaign")
        for store_id in treatment_store_ids
    )
    evidence_level = assign_evidence_level(
        has_treatment_data=has_treatment_data,
        control_store_ids=control_store_ids,
        pre_trend_status=pre_trend.status,
        contamination=contamination,
    )
    causal_claim_allowed = is_causal_evidence(evidence_level)
    recommendation = recommend(
        evidence_level=evidence_level,
        iromi=iromi,
        scale_iromi=scale_iromi,
        continue_iromi=continue_iromi,
    )

    source_snapshot_ids = tuple(
        snapshot_id
        for metric in campaign.observations
        for snapshot_id in metric.source_snapshot_ids
    )

    intervention_writeback = _build_intervention_writeback(
        campaign,
        evidence_level=evidence_level,
        estimate=estimate,
        iromi=iromi,
        recommendation=recommendation,
        causal_claim_allowed=causal_claim_allowed,
    )
    label_registry_entry = _build_label_registry_entry(
        campaign,
        evidence_level=evidence_level,
        estimate=estimate,
        iromi=iromi,
        generated_at=generated,
        causal_claim_allowed=causal_claim_allowed,
    )

    return IncrementalityReport(
        report_id=f"adlift-report-{uuid4()}",
        campaign_id=campaign.campaign_id,
        campaign_name=campaign.name,
        channel=campaign.channel,
        treatment_store_ids=treatment_store_ids,
        control_store_ids=control_store_ids,
        matched_controls=tuple(matches),
        pre_trend_status=pre_trend.status,
        pre_trend=pre_trend,
        measurement_method=ADLIFT_MEASUREMENT_METHOD,
        surface_revenue=estimate.surface_revenue,
        incremental_revenue=estimate.incremental_revenue,
        incremental_gross_margin=estimate.incremental_gross_margin,
        effect_interval=estimate.effect_interval,
        iromi=iromi,
        ad_spend=campaign.ad_spend,
        evidence_level=evidence_level,
        causal_claim_allowed=causal_claim_allowed,
        recommendation=recommendation,
        contamination=tuple(contamination),
        intervention_writeback=intervention_writeback,
        label_registry_entry=label_registry_entry,
        model_version=ADLIFT_MODEL_VERSION,
        feature_version=ADLIFT_FEATURE_VERSION,
        policy_version=ADLIFT_EVIDENCE_POLICY_VERSION,
        generated_at=generated,
        source_snapshot_ids=source_snapshot_ids,
        estimator_metadata=(
            {
                **estimate.estimator_metadata,
                "execution_mode": "production_oss",
                "library_version": version("statsmodels"),
                "model_version": ADLIFT_MODEL_VERSION,
            }
            if require_statsmodels
            else estimate.estimator_metadata
        ),
    )


def evaluate_campaigns(
    campaigns: Iterable[AdCampaign | Mapping[str, Any]],
    *,
    generated_at: datetime | None = None,
) -> list[IncrementalityReport]:
    return [run_incrementality(campaign, generated_at=generated_at) for campaign in campaigns]


def _estimate_incrementality(
    campaign: AdCampaign,
    *,
    by_store: Mapping[str, list[StoreDayMetric]],
    matches: Sequence[MatchedControl],
    treatment_store_ids: Sequence[str],
) -> IncrementalityEstimate:
    """Matched-pair difference-in-differences (ODP-ML-05 §9.1).

    Effect = (Treated_Post - Treated_Pre) - (Control_Post - Control_Pre), per
    store-day, scaled by the treatment store's observed campaign days. Surface
    revenue (raw observed) is kept separate from the incremental estimate so the
    report can show both (AC-07-03).
    """
    treatment_campaign = _period_metrics(campaign, by_store, treatment_store_ids, "campaign")
    surface_revenue = round(sum(metric.revenue for metric in treatment_campaign), 2)
    surface_gross_margin = round(sum(metric.gross_margin for metric in treatment_campaign), 2)

    if not matches:
        # No control: report the before/after change (non-causal, L1).
        treatment_pre = _period_metrics(campaign, by_store, treatment_store_ids, "pre")
        rev_delta = _mean(_revenue_values(treatment_campaign)) - _mean(
            _revenue_values(treatment_pre)
        )
        gm_delta = _mean(_margin_values(treatment_campaign)) - _mean(_margin_values(treatment_pre))
        store_days = len(treatment_campaign)
        point = round(gm_delta, 4)
        return IncrementalityEstimate(
            surface_revenue=surface_revenue,
            surface_gross_margin=surface_gross_margin,
            incremental_revenue=round(rev_delta * store_days, 2),
            incremental_gross_margin=round(gm_delta * store_days, 2),
            effect_interval=EffectInterval(
                metric="before_after_gm_per_store_day", low=point, point=point, high=point
            ),
            estimator_metadata={
                "library": None,
                "estimator": "before_after_difference",
                "causal": False,
                "sample_size": store_days,
            },
        )

    revenue_effects: list[float] = []
    gm_effects: list[float] = []
    treated_campaign_days: list[int] = []
    for match in matches:
        t_pre = _period_metrics(campaign, by_store, [match.treatment_store_id], "pre")
        t_post = _period_metrics(campaign, by_store, [match.treatment_store_id], "campaign")
        c_pre = _period_metrics(campaign, by_store, [match.control_store_id], "pre")
        c_post = _period_metrics(campaign, by_store, [match.control_store_id], "campaign")

        rev_did = (_mean(_revenue_values(t_post)) - _mean(_revenue_values(t_pre))) - (
            _mean(_revenue_values(c_post)) - _mean(_revenue_values(c_pre))
        )
        gm_did = (_mean(_margin_values(t_post)) - _mean(_margin_values(t_pre))) - (
            _mean(_margin_values(c_post)) - _mean(_margin_values(c_pre))
        )
        campaign_days = len(t_post)
        revenue_effects.append(rev_did)
        gm_effects.append(gm_did)
        treated_campaign_days.append(campaign_days)

    revenue_fit = _fit_statsmodels_matched_did(
        revenue_effects,
        treated_campaign_days,
        metric="did_revenue_per_store_day",
    )
    margin_fit = _fit_statsmodels_matched_did(
        gm_effects,
        treated_campaign_days,
        metric="did_gm_per_store_day",
    )

    return IncrementalityEstimate(
        surface_revenue=surface_revenue,
        surface_gross_margin=surface_gross_margin,
        incremental_revenue=round(revenue_fit.total_effect, 2),
        incremental_gross_margin=round(margin_fit.total_effect, 2),
        effect_interval=margin_fit.interval,
        estimator_metadata={
            "library": "statsmodels",
            "estimator": "WLS",
            "design": "matched_pair_difference_in_differences",
            "formula": "pair_did_effect ~ 1",
            "weights": "treated_campaign_days",
            "pair_count": len(gm_effects),
            "treated_campaign_days": sum(treated_campaign_days),
        },
    )


@dataclass(frozen=True)
class _StatsmodelsDiDFit:
    total_effect: float
    interval: EffectInterval


def _fit_statsmodels_matched_did(
    effects: Sequence[float],
    treated_campaign_days: Sequence[int],
    *,
    metric: str,
) -> _StatsmodelsDiDFit:
    """Estimate the weighted matched-pair DiD effect with statsmodels WLS."""
    try:
        import statsmodels.api as sm
    except ModuleNotFoundError as exc:
        raise RuntimeError("statsmodels is required for matched-control DiD estimation") from exc

    usable = [
        (float(effect), int(days))
        for effect, days in zip(effects, treated_campaign_days, strict=True)
        if days > 0
    ]
    if not usable:
        interval = EffectInterval(metric=metric, low=0.0, point=0.0, high=0.0)
        return _StatsmodelsDiDFit(total_effect=0.0, interval=interval)

    outcome = np.asarray([effect for effect, _days in usable], dtype=float)
    weights = np.asarray([days for _effect, days in usable], dtype=float)
    fit = sm.WLS(outcome, np.ones((len(usable), 1)), weights=weights).fit()
    point_value = float(fit.params[0])
    total_effect = point_value * float(np.sum(weights))
    point = round(point_value, 4)
    if len(usable) < 2:
        return _StatsmodelsDiDFit(
            total_effect=total_effect,
            interval=EffectInterval(metric=metric, low=point, point=point, high=point),
        )

    standard_error_value = float(fit.bse[0])
    if not np.isfinite(standard_error_value):
        standard_error_value = 0.0
    standard_error = round(standard_error_value, 6)
    if standard_error == 0.0:
        low = high = point
    else:
        low_value, high_value = (
            float(bound) for bound in fit.conf_int(alpha=DID_EFFECT_CI_ALPHA)[0]
        )
        low, high = round(low_value, 4), round(high_value, 4)
    return _StatsmodelsDiDFit(
        total_effect=total_effect,
        interval=EffectInterval(
            metric=metric,
            low=low,
            point=point,
            high=high,
            standard_error=standard_error,
        ),
    )


def _build_intervention_writeback(
    campaign: AdCampaign,
    *,
    evidence_level: EvidenceLevel,
    estimate: IncrementalityEstimate,
    iromi: float,
    recommendation: Recommendation,
    causal_claim_allowed: bool,
) -> dict[str, Any]:
    """Writeback packet for InterventionOps (AC-07-05); composes with ODP-R4-001."""
    return {
        "intervention_type": "ad_campaign",
        "campaign_id": campaign.campaign_id,
        "campaign_intervention_id": campaign.campaign_intervention_id,
        "treatment_store_ids": list(campaign.treatment_store_ids),
        "incremental_gross_margin": estimate.incremental_gross_margin,
        "iromi": iromi,
        "evidence_level": evidence_level.value,
        "causal_claim_allowed": causal_claim_allowed,
        "recommendation": recommendation.value,
    }


def _build_label_registry_entry(
    campaign: AdCampaign,
    *,
    evidence_level: EvidenceLevel,
    estimate: IncrementalityEstimate,
    iromi: float,
    generated_at: datetime,
    causal_claim_allowed: bool,
) -> dict[str, Any]:
    """Outcome label for the Label Registry (AC-07-05, output-contract §5.1)."""
    return {
        "campaign_id": campaign.campaign_id,
        "label_type": "ad_incrementality",
        "measurement_method": ADLIFT_MEASUREMENT_METHOD,
        "incremental_revenue": estimate.incremental_revenue,
        "incremental_gross_margin": estimate.incremental_gross_margin,
        "iromi": iromi,
        "evidence_level": evidence_level.value,
        "causal_claim_allowed": causal_claim_allowed,
        "label_maturity_time": campaign.campaign_period_end.isoformat(),
        "labeled_at": generated_at.isoformat(),
    }


def _group_daily_means(
    campaign: AdCampaign,
    by_store: Mapping[str, list[StoreDayMetric]],
    store_ids: Sequence[str],
    period: str,
) -> list[tuple[date, float]]:
    per_day: dict[date, list[float]] = defaultdict(list)
    for store_id in store_ids:
        for metric in _in_period(by_store.get(store_id, []), campaign, period=period):
            per_day[metric.business_date].append(metric.revenue)
    return [(day, _mean(values)) for day, values in sorted(per_day.items())]


def _period_metrics(
    campaign: AdCampaign,
    by_store: Mapping[str, list[StoreDayMetric]],
    store_ids: Sequence[str],
    period: str,
) -> list[StoreDayMetric]:
    metrics: list[StoreDayMetric] = []
    for store_id in store_ids:
        metrics.extend(_in_period(by_store.get(store_id, []), campaign, period=period))
    return metrics


def _in_period(
    metrics: Iterable[StoreDayMetric], campaign: AdCampaign, *, period: str
) -> list[StoreDayMetric]:
    if period == "pre":
        start, end = campaign.pre_period_start, campaign.pre_period_end
    else:
        start, end = campaign.campaign_period_start, campaign.campaign_period_end
    return [metric for metric in metrics if start <= metric.business_date <= end]


def _revenues(
    metrics: Iterable[StoreDayMetric], campaign: AdCampaign, *, period: str
) -> list[float]:
    return _revenue_values(_in_period(metrics, campaign, period=period))


def _revenue_values(metrics: Iterable[StoreDayMetric]) -> list[float]:
    return [metric.revenue for metric in metrics]


def _margin_values(metrics: Iterable[StoreDayMetric]) -> list[float]:
    return [metric.gross_margin for metric in metrics]


def _group_by_store(
    metrics: Iterable[StoreDayMetric],
) -> dict[str, list[StoreDayMetric]]:
    grouped: dict[str, list[StoreDayMetric]] = defaultdict(list)
    for metric in metrics:
        grouped[metric.store_id].append(metric)
    for items in grouped.values():
        items.sort(key=lambda metric: metric.business_date)
    return grouped


def _normalised_slope(values: Sequence[float]) -> float:
    slope = _least_squares_slope(values)
    level = _mean(values)
    if level <= 0:
        return 0.0
    return slope / level


def _least_squares_slope(values: Sequence[float]) -> float:
    """Ordinary-least-squares slope of ``values`` against their index.

    Backed by ``numpy.polyfit`` (degree-1 least squares) so the pre-trend test
    uses the library implementation rather than a hand-rolled normal equation.
    """
    n = len(values)
    if n < 2:
        return 0.0
    index = np.arange(n, dtype=float)
    slope = np.polyfit(index, np.asarray(values, dtype=float), 1)[0]
    return float(slope)


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _coerce_campaign(item: AdCampaign | Mapping[str, Any]) -> AdCampaign:
    if isinstance(item, AdCampaign):
        return item
    return AdCampaign.from_mapping(item)


def _coerce_metric(item: StoreDayMetric | Mapping[str, Any]) -> StoreDayMetric:
    if isinstance(item, StoreDayMetric):
        return item
    return StoreDayMetric.from_mapping(item)


def _parse_date(value: date | datetime | str | None) -> date:
    if value is None:
        return datetime.now(UTC).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _first_present(data: Mapping[str, Any], *keys: str, default: Any) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default
