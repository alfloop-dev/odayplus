from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.heatzone.domain.scoring import (
    HeatZoneFeatureInput,
    HeatZoneScoreResult,
    score_heatzones,
)
from modules.heatzone.v3.adapter import (
    from_catchment_profile,
    from_market_cell_profile,
)
from modules.heatzone.v3.contract import (
    CONTRACT_VERSION,
    MODEL_VERSION,
    ExecutionMode,
    HeatZoneV3BatchResult,
    HeatZoneV3Input,
    HeatZoneV3ScoreResult,
    HeatZoneV3ShadowComparison,
)
from modules.heatzone.v3.scoring import HeatZoneV3ScoringWeights, score_heatzones_v3
from packages.oday_data_contracts_client.models.machine_capacity import MachineCapacityRecord
from packages.oday_data_contracts_client.models.manifests import ManifestDocument
from packages.oday_data_contracts_client.models.store_coverage import StoreDayCoverage
from packages.oday_data_product_contracts_client.models.catchment_profile import (
    CatchmentProfileDocument,
)
from packages.oday_data_product_contracts_client.models.market_cell_profile import (
    MarketCellProfileDocument,
)


class HeatZoneV3ShadowRunner:
    """Shadow evaluation engine running HeatZone v3 side-by-side with baseline heuristic.

    Ensures v3 runs in shadow mode, abstains outside platform support, and generates
    detailed shadow comparison receipts.
    """

    def __init__(
        self,
        *,
        weights: HeatZoneV3ScoringWeights | None = None,
        model_version: str = MODEL_VERSION,
        execution_mode: ExecutionMode = ExecutionMode.SHADOW,
    ) -> None:
        self.weights = weights
        self.model_version = model_version
        self.execution_mode = execution_mode

    def evaluate_market_cells(
        self,
        document: MarketCellProfileDocument | Mapping[str, Any],
        *,
        baseline_features: Sequence[HeatZoneFeatureInput | Mapping[str, Any]] | None = None,
        own_store_capacities: Sequence[MachineCapacityRecord] | None = None,
        store_coverage_records: Sequence[StoreDayCoverage] | None = None,
        store_performances: Sequence[Any] | None = None,
        operational_starts: Mapping[str, Any] | Sequence[Any] | None = None,
        decision_policy: Any | None = None,
        as_of: Any | None = None,
        original_demand: float | None = None,
        tenant_id: str = "default",
        manifest_document: ManifestDocument | None = None,
    ) -> HeatZoneV3BatchResult:
        """Evaluate a released MarketCellProfileDocument in shadow mode."""
        if isinstance(document, Mapping):
            doc = MarketCellProfileDocument.from_dict(document)
        else:
            doc = document

        v3_inputs = [
            from_market_cell_profile(
                cell,
                own_store_capacities=own_store_capacities,
                store_coverage_records=store_coverage_records,
                store_performances=store_performances,
                operational_starts=operational_starts,
                decision_policy=decision_policy,
                as_of=as_of,
                original_demand=original_demand,
                tenant_id=tenant_id,
            )
            for cell in doc.cells
        ]

        doc_id = f"heatzone-v3-shadow-{doc.profile_id or uuid4()}"
        return self._evaluate_and_compare(
            document_id=doc_id,
            v3_inputs=v3_inputs,
            baseline_features=baseline_features,
            tenant_id=tenant_id,
            manifest_document=manifest_document,
        )

    def evaluate_catchment_profiles(
        self,
        document: CatchmentProfileDocument | Mapping[str, Any],
        *,
        baseline_features: Sequence[HeatZoneFeatureInput | Mapping[str, Any]] | None = None,
        own_store_capacities: Sequence[MachineCapacityRecord] | None = None,
        store_coverage_records: Sequence[StoreDayCoverage] | None = None,
        store_performances: Sequence[Any] | None = None,
        operational_starts: Mapping[str, Any] | Sequence[Any] | None = None,
        decision_policy: Any | None = None,
        as_of: Any | None = None,
        original_demand: float | None = None,
        tenant_id: str = "default",
        manifest_document: ManifestDocument | None = None,
    ) -> HeatZoneV3BatchResult:
        """Evaluate a released CatchmentProfileDocument in shadow mode."""
        if isinstance(document, Mapping):
            doc = CatchmentProfileDocument.from_dict(document)
        else:
            doc = document

        v3_inputs = [
            from_catchment_profile(
                prof,
                own_store_capacities=own_store_capacities,
                store_coverage_records=store_coverage_records,
                store_performances=store_performances,
                operational_starts=operational_starts,
                decision_policy=decision_policy,
                as_of=as_of,
                original_demand=original_demand,
                tenant_id=tenant_id,
            )
            for prof in doc.profiles
        ]

        doc_id = f"heatzone-v3-shadow-{doc.document_id or uuid4()}"
        return self._evaluate_and_compare(
            document_id=doc_id,
            v3_inputs=v3_inputs,
            baseline_features=baseline_features,
            tenant_id=tenant_id,
            manifest_document=manifest_document,
        )

    def evaluate_inputs(
        self,
        inputs: Sequence[HeatZoneV3Input],
        *,
        baseline_features: Sequence[HeatZoneFeatureInput | Mapping[str, Any]] | None = None,
        tenant_id: str = "default",
        manifest_document: ManifestDocument | None = None,
        document_id: str | None = None,
    ) -> HeatZoneV3BatchResult:
        """Evaluate arbitrary HeatZoneV3Input items in shadow mode."""
        doc_id = document_id or f"heatzone-v3-shadow-{uuid4()}"
        return self._evaluate_and_compare(
            document_id=doc_id,
            v3_inputs=inputs,
            baseline_features=baseline_features,
            tenant_id=tenant_id,
            manifest_document=manifest_document,
        )

    def _evaluate_and_compare(
        self,
        *,
        document_id: str,
        v3_inputs: Sequence[HeatZoneV3Input],
        baseline_features: Sequence[HeatZoneFeatureInput | Mapping[str, Any]] | None = None,
        tenant_id: str = "default",
        manifest_document: ManifestDocument | None = None,
    ) -> HeatZoneV3BatchResult:
        eval_time = datetime.now(UTC)

        # 1. Score v3 features
        v3_scores = score_heatzones_v3(
            v3_inputs,
            evaluated_at=eval_time,
            weights=self.weights,
            execution_mode=self.execution_mode,
            model_version=self.model_version,
        )

        # 2. Score baseline features if available, or generate from inputs
        if baseline_features is not None:
            baseline_score_results = score_heatzones(baseline_features, scored_at=eval_time)
        else:
            synth_baseline = [
                HeatZoneFeatureInput(
                    h3_index=inp.h3_index,
                    h3_resolution=inp.h3_resolution,
                    poi_count=inp.poi_count,
                    competitor_count=inp.active_competitor_count,
                    competitor_capacity=inp.competitor_capacity,
                    median_listing_rent=inp.median_rent_per_ping,
                    active_listing_count=inp.active_listing_count,
                    existing_store_count=inp.own_store_count,
                    average_confidence=inp.confidence,
                    admin_city=inp.county,
                    admin_district=inp.district,
                    cell_latitude=inp.centroid_lat or 0.0,
                    cell_longitude=inp.centroid_lng or 0.0,
                )
                for inp in v3_inputs
            ]
            baseline_score_results = score_heatzones(synth_baseline, scored_at=eval_time)

        # 3. Compute side-by-side shadow comparisons and metrics
        comparisons, metrics = compute_shadow_comparisons_and_metrics(v3_scores, baseline_score_results)

        scored_count = sum(1 for s in v3_scores if not s.abstained and s.score is not None)
        abstained_count = sum(1 for s in v3_scores if s.abstained or s.score is None)

        manifest_id = None
        if manifest_document is not None:
            if hasattr(manifest_document, "manifest_id"):
                manifest_id = manifest_document.manifest_id
            elif hasattr(manifest_document, "manifest"):
                raw_m = manifest_document.manifest
                if isinstance(raw_m, dict):
                    manifest_id = raw_m.get("manifest_id")
                elif hasattr(raw_m, "manifest_id"):
                    manifest_id = raw_m.manifest_id

        return HeatZoneV3BatchResult(
            document_id=document_id,
            contract_version=CONTRACT_VERSION,
            execution_mode=self.execution_mode,
            is_shadow=self.execution_mode is ExecutionMode.SHADOW,
            total_evaluated=len(v3_scores),
            scored_count=scored_count,
            abstained_count=abstained_count,
            scores=tuple(v3_scores),
            comparisons=tuple(comparisons),
            shadow_metrics=metrics,
            evaluated_at=eval_time,
            manifest_id=manifest_id,
            tenant_id=tenant_id,
            metadata={
                "model_version": self.model_version,
                "execution_mode": self.execution_mode.value,
            },
        )


def compute_shadow_comparisons_and_metrics(
    v3_scores: Sequence[HeatZoneV3ScoreResult],
    baseline_scores: Sequence[HeatZoneScoreResult] | None,
) -> tuple[tuple[HeatZoneV3ShadowComparison, ...], dict[str, Any]]:
    """Build side-by-side shadow comparison rows and aggregate evaluation metrics."""
    baseline_by_h3: dict[str, HeatZoneScoreResult] = {}
    if baseline_scores:
        baseline_by_h3 = {b.h3_index: b for b in baseline_scores}

    comparisons: list[HeatZoneV3ShadowComparison] = []
    score_deltas: list[float] = []
    rank_deltas: list[int] = []
    agreements: list[bool] = []

    for v3 in v3_scores:
        base = baseline_by_h3.get(v3.h3_index)
        base_score = base.score if base else None
        base_rank = base.priority_rank if base else None
        base_state = base.state.value if base and hasattr(base.state, "value") else (str(base.state) if base else None)

        if v3.abstained or v3.score is None:
            s_delta = None
            r_delta = None
            agree = False
            notes = "v3 abstained outside platform support"
            drift = ("v3_abstained",)
        elif base_score is not None:
            s_delta = round(v3.score - base_score, 2)
            r_delta = (base_rank or 0) - v3.priority_rank  # positive = v3 promoted higher
            score_deltas.append(s_delta)
            if base_rank is not None:
                rank_deltas.append(r_delta)

            # Agreement: within 15 score points or same state category
            score_close = abs(s_delta) <= 15.0
            same_direction = (v3.score >= 50.0 and base_score >= 50.0) or (v3.score < 50.0 and base_score < 50.0)
            agree = score_close or same_direction
            agreements.append(agree)

            v3_reasons_set = set(v3.reasons)
            base_reasons_set = set(base.reasons) if base else set()
            added_reasons = v3_reasons_set - base_reasons_set
            removed_reasons = base_reasons_set - v3_reasons_set
            drift = tuple(f"+{r}" for r in sorted(added_reasons)) + tuple(f"-{r}" for r in sorted(removed_reasons))
            notes = f"Score delta: {s_delta:+.2f}, Rank delta: {r_delta:+d}"
        else:
            s_delta = None
            r_delta = None
            agree = True
            notes = "no baseline comparison available"
            drift = ()

        comparisons.append(
            HeatZoneV3ShadowComparison(
                h3_index=v3.h3_index,
                v3_score=v3.score,
                v3_rank=v3.priority_rank,
                v3_state=v3.state.value,
                baseline_score=base_score,
                baseline_rank=base_rank,
                baseline_state=base_state,
                score_delta=s_delta,
                rank_delta=r_delta,
                v3_abstained=v3.abstained,
                agreement=agree,
                reasons_drift=drift,
                notes=notes,
            )
        )

    # Calculate aggregate shadow metrics
    total = len(v3_scores)
    abstained_count = sum(1 for v in v3_scores if v.abstained)
    scored_count = total - abstained_count
    mean_v3 = (
        round(sum(v.score for v in v3_scores if v.score is not None) / scored_count, 2)
        if scored_count > 0
        else 0.0
    )
    mean_base = (
        round(sum(b.score for b in baseline_scores) / len(baseline_scores), 2)
        if baseline_scores
        else 0.0
    )
    mean_delta = (
        round(sum(score_deltas) / len(score_deltas), 2)
        if score_deltas
        else 0.0
    )
    agree_rate = (
        round(sum(1 for a in agreements if a) / len(agreements), 4)
        if agreements
        else 1.0
    )

    # Top-K overlap rate
    top_k = min(5, scored_count)
    if top_k > 0 and baseline_scores:
        v3_top_k = {v.h3_index for v in v3_scores if not v.abstained and v.priority_rank <= top_k}
        base_top_k = {b.h3_index for b in baseline_scores if b.priority_rank <= top_k}
        top_k_overlap = round(len(v3_top_k & base_top_k) / top_k, 4)
    else:
        top_k_overlap = 1.0

    metrics = {
        "total_evaluated": total,
        "scored_count": scored_count,
        "abstained_count": abstained_count,
        "abstention_rate": round(abstained_count / total, 4) if total > 0 else 0.0,
        "mean_v3_score": mean_v3,
        "mean_baseline_score": mean_base,
        "mean_score_delta": mean_delta,
        "agreement_rate": agree_rate,
        "top_k_overlap_rate": top_k_overlap,
    }

    return tuple(comparisons), metrics


__all__ = [
    "HeatZoneV3ShadowRunner",
    "compute_shadow_comparisons_and_metrics",
]
