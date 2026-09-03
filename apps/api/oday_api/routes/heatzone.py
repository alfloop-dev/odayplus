from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models.shared_ml import (
    ModelBinding,
    ProductionModelInputError,
    ProductionModelRuntime,
    ProductionModelRuntimeError,
    ScoringInputUnavailableError,
    production_model_execution_required,
    require_live_inputs,
)
from modules.heatzone.application.merge_split_engine import (
    CandidateCellFeature,
    MergeSplitReadinessInput,
    evaluate_merge_split,
)
from modules.heatzone.domain.composition import (
    CompositionKind,
    CompositionValidationError,
)
from modules.heatzone.infrastructure import (
    HeatZoneCompositionRepository,
    HeatZoneResultStore,
    InMemoryHeatZoneCompositionRepository,
)
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.governance import (
    DecisionPolicyRepository,
    InMemoryDecisionPolicyRepository,
    resolve_policy,
)

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:
    from modules.heatzone.workers import run_heatzone_batch_score


    class HeatZoneScoreJobPayload(BaseModel):
        features: list[dict[str, Any]] = Field(default_factory=list)
        prediction_origin_time: str | None = None
        idempotency_key: str | None = None


    class HeatZoneOverridePayload(BaseModel):
        decided_by: str = Field(min_length=1)
        override_reason: str = Field(min_length=1)
        decision_policy_version_id: str | None = None
        new_kind: str | None = None
        member_cell_ids: list[str] | None = None
        parent_zone_id: str | None = None


    class HeatZoneRollbackPayload(BaseModel):
        revert_reason: str | None = None


    class HeatZoneMergeSplitEvaluatePayload(BaseModel):
        cells: list[dict[str, Any]] = Field(default_factory=list)
        readiness: dict[str, Any] = Field(default_factory=dict)
        policy_version_id: str | None = None


    class HeatZoneProposalApprovePayload(BaseModel):
        decided_by: str = Field(min_length=1)
        notes: str | None = None


    class HeatZoneProposalRejectPayload(BaseModel):
        rejected_by: str = Field(min_length=1)
        reason: str = Field(min_length=1)


    def create_heatzone_router(
        *,
        store: HeatZoneResultStore | None = None,
        composition_repository: HeatZoneCompositionRepository | None = None,
        policy_repository: DecisionPolicyRepository | None = None,
        heatzone_store_for_tenant: Any | None = None,
        composition_repository_for_tenant: Any | None = None,
        audit_log: InMemoryAuditLog | None = None,
        model_binding: ModelBinding | None = None,
        model_runtime: ProductionModelRuntime | None = None,
        require_production_model: bool | None = None,
    ) -> APIRouter:
        from apps.api.app.routes._common import resolve_tenant_id
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        router = APIRouter(prefix="/heatzones", tags=["heatzones"])
        result_store = store or HeatZoneResultStore()
        comp_repo = composition_repository or InMemoryHeatZoneCompositionRepository()
        pol_repo = policy_repository or InMemoryDecisionPolicyRepository()
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        production_model_required = (
            production_model_execution_required()
            if require_production_model is None
            else require_production_model
        )

        def store_for_request(request: Request) -> Any:
            tid = resolve_tenant_id(request)
            if heatzone_store_for_tenant is not None:
                scoped = heatzone_store_for_tenant(tid)
                if scoped is not None:
                    return scoped
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to resolve tenant-scoped heatzone store",
                )
            if hasattr(result_store, "_store"):
                base_store = getattr(result_store._store, "_store", result_store._store)
                if hasattr(base_store, "get") and hasattr(base_store, "put"):
                    from shared.infrastructure.persistence.operator_domains import (
                        TenantScopedDocumentStore,
                    )
                    return type(result_store)(TenantScopedDocumentStore(base_store, tid))
            return result_store

        def comp_repo_for_request(request: Request) -> Any:
            tid = resolve_tenant_id(request)
            if composition_repository_for_tenant is not None:
                scoped = composition_repository_for_tenant(tid)
                if scoped is not None:
                    return scoped
            if hasattr(comp_repo, "_store"):
                base_store = getattr(comp_repo._store, "_store", comp_repo._store)
                if hasattr(base_store, "get") and hasattr(base_store, "put"):
                    from shared.infrastructure.persistence.operator_domains import (
                        TenantScopedDocumentStore,
                    )
                    return type(comp_repo)(TenantScopedDocumentStore(base_store, tid))
            return comp_repo

        @router.get("", dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))])
        def list_heatzones(request: Request, limit: int = 100) -> dict[str, Any]:
            active_store = store_for_request(request)
            scores = active_store.list_scores()[: max(0, limit)]
            return {"items": scores, "count": len(scores)}

        @router.get("/map", dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))])
        def heatzone_map(request: Request) -> dict[str, Any]:
            active_store = store_for_request(request)
            features = active_store.map_features()
            return {
                "type": "FeatureCollection",
                "features": features,
                "count": len(features),
            }

        @router.post(
            "/score-jobs",
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=[Depends(require_permission("heatzone", Action.CREATE, engine=authz_engine))],
        )
        def create_score_job(
            body: HeatZoneScoreJobPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_idempotency_key = body.idempotency_key or idempotency_key
            active_store = store_for_request(request)
            tid = resolve_tenant_id(request)
            existing = active_store.find_by_idempotency_key(effective_idempotency_key)
            if existing is not None:
                result, created = existing, False
            else:
                # Fail closed: refuse a fresh run when live inputs are absent.
                try:
                    require_live_inputs(body.features, service="heatzone")
                except ScoringInputUnavailableError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                    ) from exc
                try:
                    result, created = active_store.put(
                        run_heatzone_batch_score(
                            features=body.features,
                            prediction_origin_time=body.prediction_origin_time,
                            model_runtime=model_runtime,
                            require_production_model=production_model_required,
                            tenant_id=tid,
                        ),
                        idempotency_key=effective_idempotency_key,
                    )
                except ProductionModelInputError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail={"code": exc.code, "message": str(exc)},
                    ) from exc
                except ProductionModelRuntimeError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail={"code": exc.code, "message": str(exc)},
                    ) from exc
            executed_binding = (
                result.model_inference.binding
                if result.model_inference is not None
                else model_binding
            )
            metadata: dict[str, Any] = {
                "idempotency_key": effective_idempotency_key,
                "feature_count": len(body.features),
                "created": created,
            }
            if executed_binding is not None:
                metadata["model_binding"] = executed_binding.to_audit_metadata()
            audit_event = active_audit_log.record(
                AuditEvent(
                    event_type="heatzone.scored.v1",
                    actor="system",
                    action="run_model",
                    resource="heatzone/score-job",
                    outcome="accepted" if created else "idempotent_replay",
                    correlation_id=request.state.correlation_id,
                    job_id=result.job_id,
                    metadata=metadata,
                )
            )
            from shared.observability.metrics import record_business_kpi_signal, record_model_signal
            record_model_signal("heatzone", "heatzone_batch_score", prediction_count=len(body.features))
            measured_adoption = getattr(result_store, "get_measured_topk_adoption_rate", lambda: None)()
            if measured_adoption is not None and isinstance(measured_adoption, (int, float)):
                record_business_kpi_signal("heatzone_topk_adoption_rate", float(measured_adoption))
            payload = result.to_dict()
            payload["created"] = created
            payload["audit_event_id"] = audit_event.event_id
            payload["correlation_id"] = request.state.correlation_id
            if executed_binding is not None:
                payload["model_binding"] = executed_binding.to_audit_metadata()
            return payload

        @router.post(
            "/merge-split/evaluate",
            dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))],
        )
        def evaluate_heatzone_merge_split(
            body: HeatZoneMergeSplitEvaluatePayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            policy = None
            if body.policy_version_id:
                if hasattr(pol_repo, "versions"):
                    for v in pol_repo.versions:
                        if v.policy_version_id == body.policy_version_id:
                            policy = v
                            break
                elif hasattr(pol_repo, "get_by_version"):
                    policy = pol_repo.get_by_version(body.policy_version_id)
                elif hasattr(pol_repo, "find_version"):
                    policy = pol_repo.find_version(body.policy_version_id)

                if policy is None:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"decision policy version '{body.policy_version_id}' not found",
                    )
                if policy.tenant_id != tid:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"decision policy version '{body.policy_version_id}' does not belong to tenant '{tid}'",
                    )
            else:
                try:
                    policy = resolve_policy(
                        pol_repo,
                        policy_kind="heatzone_merge",
                        tenant_id=tid,
                        at=datetime.now(UTC),
                    )
                except Exception as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"failed to resolve governed heatzone_merge policy for tenant '{tid}': {exc}",
                    ) from exc

            r_data = body.readiness or {}
            readiness_input = MergeSplitReadinessInput(
                observation_days=int(r_data.get("observation_days", 0)),
                mature_labels_count=int(r_data.get("mature_labels_count", 0)),
                active_store_count=int(r_data.get("active_store_count", 0)),
                adjacent_pairs_count=int(r_data.get("adjacent_pairs_count", 0)),
                metro_clusters_count=int(r_data.get("metro_clusters_count", 0)),
                spatial_contiguity_ratio=float(r_data.get("spatial_contiguity_ratio", 0.0)),
                absorption_ratio_cv=(
                    float(r_data["absorption_ratio_cv"])
                    if r_data.get("absorption_ratio_cv") is not None
                    else None
                ),
                drift_psi=(
                    float(r_data["drift_psi"])
                    if r_data.get("drift_psi") is not None
                    else None
                ),
                wasserstein_distance=(
                    float(r_data["wasserstein_distance"])
                    if r_data.get("wasserstein_distance") is not None
                    else None
                ),
                is_synthetic=bool(r_data.get("is_synthetic", False)),
                governed_disabled=bool(r_data.get("governed_disabled", False)),
                source_snapshot_id=str(r_data.get("source_snapshot_id", "")),
            )

            candidate_cells: list[CandidateCellFeature] = []
            for c in body.cells:
                raw_partitions = c.get("child_partition_cell_ids", ())
                partition_tuples = tuple(
                    tuple(str(x) for x in part) for part in raw_partitions
                )
                candidate_cells.append(
                    CandidateCellFeature(
                        cell_id=str(c.get("cell_id", "")),
                        h3_index=str(c.get("h3_index", "")),
                        tenant_id=tid,
                        admin_city=str(c.get("admin_city", "")),
                        admin_district=str(c.get("admin_district", "")),
                        population=float(c.get("population", 0.0)),
                        poi_count=int(c.get("poi_count", 0)),
                        own_store_count=int(c.get("own_store_count", 0)),
                        competitor_count=int(c.get("competitor_count", 0)),
                        median_rent_per_ping=float(c.get("median_rent_per_ping", 0.0)),
                        unmet_demand=float(c.get("unmet_demand", 0.0)),
                        absorbed_demand=float(c.get("absorbed_demand", 0.0)),
                        realized_revenue=float(c.get("realized_revenue", 0.0)),
                        has_natural_barrier=bool(c.get("has_natural_barrier", False)),
                        barrier_description=str(c.get("barrier_description", "")),
                        adjacent_cell_ids=tuple(str(x) for x in c.get("adjacent_cell_ids", [])),
                        barrier_side_a_revenue=float(c.get("barrier_side_a_revenue", 0.0)),
                        barrier_side_a_absorbed_demand=float(c.get("barrier_side_a_absorbed_demand", 0.0)),
                        barrier_side_b_revenue=float(c.get("barrier_side_b_revenue", 0.0)),
                        barrier_side_b_absorbed_demand=float(c.get("barrier_side_b_absorbed_demand", 0.0)),
                        child_partition_cell_ids=partition_tuples,
                    )
                )

            evaluation = evaluate_merge_split(
                candidate_cells,
                readiness_input=readiness_input,
                policy=policy,
            )

            active_comp_repo = comp_repo_for_request(request)
            if not evaluation.abstained and active_comp_repo is not None:
                for proposal in evaluation.proposals:
                    active_comp_repo.save_proposal(proposal.to_record())

            active_audit_log.record(
                AuditEvent(
                    event_type="heatzone.composition.evaluated.v1",
                    actor="system",
                    action="evaluate",
                    resource="heatzone/merge-split",
                    outcome="abstained" if evaluation.abstained else "proposed",
                    correlation_id=request.state.correlation_id,
                    metadata={
                        "abstained": evaluation.abstained,
                        "abstain_reasons": list(evaluation.abstain_reasons),
                        "proposal_count": len(evaluation.proposals),
                        "policy_version_id": policy.policy_version_id,
                    },
                )
            )
            return evaluation.to_dict()

        @router.get(
            "/merge-split/proposals",
            dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))],
        )
        def list_heatzone_proposals(
            request: Request,
            status_filter: str | None = Query(default=None, alias="status"),
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            active_comp_repo = comp_repo_for_request(request)
            proposals = active_comp_repo.list_proposals(tid, status=status_filter)
            return {
                "items": [p.to_dict() for p in proposals],
                "count": len(proposals),
            }

        @router.get(
            "/merge-split/proposals/{proposal_id}",
            dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))],
        )
        def get_heatzone_proposal(proposal_id: str, request: Request) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            active_comp_repo = comp_repo_for_request(request)
            prop = active_comp_repo.get_proposal(proposal_id, tid)
            if prop is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Proposal '{proposal_id}' not found for tenant '{tid}'",
                )
            return prop.to_dict()

        @router.post(
            "/merge-split/proposals/{proposal_id}/approve",
            dependencies=[Depends(require_permission("heatzone", Action.OVERRIDE, engine=authz_engine))],
        )
        def approve_heatzone_proposal(
            proposal_id: str,
            body: HeatZoneProposalApprovePayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            active_comp_repo = comp_repo_for_request(request)
            try:
                updated_prop, created_records = active_comp_repo.approve_proposal(
                    proposal_id=proposal_id,
                    tenant_id=tid,
                    approved_by=body.decided_by,
                    notes=body.notes,
                )
            except CompositionValidationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc

            active_audit_log.record(
                AuditEvent(
                    event_type="heatzone.composition.proposal.approved.v1",
                    actor=body.decided_by,
                    action="approve",
                    resource=f"heatzone/proposals/{proposal_id}",
                    outcome="approved",
                    correlation_id=request.state.correlation_id,
                    metadata={
                        "proposal_id": proposal_id,
                        "zone_id": updated_prop.zone_id,
                        "created_count": len(created_records),
                        "notes": body.notes,
                    },
                )
            )
            return {
                "proposal": updated_prop.to_dict(),
                "created_compositions": [r.to_dict() for r in created_records],
            }

        @router.post(
            "/merge-split/proposals/{proposal_id}/reject",
            dependencies=[Depends(require_permission("heatzone", Action.OVERRIDE, engine=authz_engine))],
        )
        def reject_heatzone_proposal(
            proposal_id: str,
            body: HeatZoneProposalRejectPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            active_comp_repo = comp_repo_for_request(request)
            try:
                updated_prop = active_comp_repo.reject_proposal(
                    proposal_id=proposal_id,
                    tenant_id=tid,
                    rejected_by=body.rejected_by,
                    reason=body.reason,
                )
            except CompositionValidationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc

            active_audit_log.record(
                AuditEvent(
                    event_type="heatzone.composition.proposal.rejected.v1",
                    actor=body.rejected_by,
                    action="reject",
                    resource=f"heatzone/proposals/{proposal_id}",
                    outcome="rejected",
                    correlation_id=request.state.correlation_id,
                    metadata={
                        "proposal_id": proposal_id,
                        "reason": body.reason,
                    },
                )
            )
            return {
                "proposal": updated_prop.to_dict(),
            }

        @router.post(
            "/merge-split/proposals/{proposal_id}/preview",
            dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))],
        )
        def preview_heatzone_proposal(
            proposal_id: str,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            active_comp_repo = comp_repo_for_request(request)
            prop = active_comp_repo.get_proposal(proposal_id, tid)
            if prop is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Proposal '{proposal_id}' not found for tenant '{tid}'",
                )

            current_compositions = []
            for cell_id in prop.member_cell_ids:
                curr = active_comp_repo.get_active_for_cell(cell_id, tid)
                if curr is not None:
                    current_compositions.append(curr.to_dict())

            return {
                "proposal": prop.to_dict(),
                "current_active_compositions": current_compositions,
                "proposed_zone_id": prop.zone_id,
                "proposed_kind": prop.composition_kind.value,
                "proposed_member_cells": list(prop.member_cell_ids),
                "expected_ndcg_gain": prop.ndcg_gain,
                "expected_cannibalization_variance_reduction": prop.cannibalization_variance_reduction,
                "correlation_rho": prop.correlation_rho,
                "disconnect_index": prop.disconnect_index,
                "confidence": prop.confidence,
            }

        @router.get(
            "/compositions",
            dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))],
        )
        def list_compositions(
            request: Request,
            active_only: bool = Query(default=True),
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            active_comp_repo = comp_repo_for_request(request)
            records = active_comp_repo.list_compositions(tid, active_only=active_only)
            return {
                "items": [r.to_dict() for r in records],
                "count": len(records),
            }

        @router.get(
            "/zones/{zone_id}/composition",
            dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))],
        )
        def get_zone_composition(zone_id: str, request: Request) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            active_comp_repo = comp_repo_for_request(request)
            records = active_comp_repo.get_composition(zone_id, tid)
            if not records:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No composition records found for zone '{zone_id}' in tenant '{tid}'",
                )
            active_records = [r for r in records if r.is_active]
            return {
                "zone_id": zone_id,
                "tenant_id": tid,
                "composition_kind": records[0].composition_kind.value,
                "parent_zone_id": records[0].parent_zone_id,
                "member_cell_ids": [r.member_cell_id for r in (active_records or records)],
                "is_active": len(active_records) > 0,
                "records": [r.to_dict() for r in records],
            }

        @router.get(
            "/zones/{zone_id}/lineage",
            dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))],
        )
        def get_zone_lineage(zone_id: str, request: Request) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            active_comp_repo = comp_repo_for_request(request)
            lineage = active_comp_repo.get_lineage(zone_id, tid)
            if lineage is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No lineage found for zone '{zone_id}' in tenant '{tid}'",
                )
            return lineage.to_dict()

        @router.post(
            "/zones/{zone_id}/override",
            dependencies=[Depends(require_permission("heatzone", Action.OVERRIDE, engine=authz_engine))],
        )
        def override_zone_composition(
            zone_id: str,
            body: HeatZoneOverridePayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            if body.decided_by == "system":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Human override must specify operator identity in 'decided_by' (not 'system')",
                )
            if not body.override_reason or not body.override_reason.strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Human override requires a non-empty 'override_reason'",
                )

            policy_version = body.decision_policy_version_id or f"heatzone-merge-v1:{tid}"
            active_comp_repo = comp_repo_for_request(request)

            new_kind = None
            if body.new_kind:
                try:
                    new_kind = CompositionKind(body.new_kind)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Invalid composition_kind '{body.new_kind}'",
                    ) from exc

            try:
                updated = active_comp_repo.override_composition(
                    zone_id=zone_id,
                    tenant_id=tid,
                    decided_by=body.decided_by,
                    override_reason=body.override_reason,
                    decision_policy_version_id=policy_version,
                    new_kind=new_kind,
                    new_cells=body.member_cell_ids,
                    parent_zone_id=body.parent_zone_id,
                )
            except CompositionValidationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc

            active_audit_log.record(
                AuditEvent(
                    event_type="heatzone.composition.overridden.v1",
                    actor=body.decided_by,
                    action="override",
                    resource=f"heatzone/zones/{zone_id}",
                    outcome="success",
                    correlation_id=request.state.correlation_id,
                    metadata={
                        "zone_id": zone_id,
                        "override_reason": body.override_reason,
                        "decided_by": body.decided_by,
                        "decision_policy_version_id": policy_version,
                        "updated_cell_count": len(updated),
                    },
                )
            )

            return {
                "zone_id": zone_id,
                "status": "overridden",
                "decided_by": body.decided_by,
                "override_reason": body.override_reason,
                "records": [r.to_dict() for r in updated],
            }

        @router.post(
            "/zones/{zone_id}/rollback",
            dependencies=[Depends(require_permission("heatzone", Action.ROLLBACK, engine=authz_engine))],
        )
        def rollback_zone_composition(
            zone_id: str,
            body: HeatZoneRollbackPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            active_comp_repo = comp_repo_for_request(request)

            try:
                reverted = active_comp_repo.revert_composition(zone_id, tid)
            except CompositionValidationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc

            active_audit_log.record(
                AuditEvent(
                    event_type="heatzone.composition.reverted.v1",
                    actor="operator",
                    action="rollback",
                    resource=f"heatzone/zones/{zone_id}",
                    outcome="success",
                    correlation_id=request.state.correlation_id,
                    metadata={
                        "zone_id": zone_id,
                        "revert_reason": body.revert_reason,
                        "reverted_count": len(reverted),
                    },
                )
            )

            return {
                "zone_id": zone_id,
                "status": "reverted",
                "revert_reason": body.revert_reason,
                "reverted_records": [r.to_dict() for r in reverted],
            }

        @router.get("/snapshots/{snapshot_id}", dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))])
        def snapshot(snapshot_id: str, request: Request) -> dict[str, Any] | None:
            active_store = store_for_request(request)
            result = active_store.snapshot(snapshot_id)
            if result is None:
                return None
            return result.to_dict()

        @router.get("/{h3_index}", dependencies=[Depends(require_permission("heatzone", Action.VIEW, engine=authz_engine))])
        def heatzone_detail(h3_index: str, request: Request) -> dict[str, Any] | None:
            active_store = store_for_request(request)
            for item in active_store.list_scores():
                if item["h3_index"] == h3_index:
                    return item
            return None

        return router


    __all__ = [
        "HeatZoneMergeSplitEvaluatePayload",
        "HeatZoneOverridePayload",
        "HeatZoneResultStore",
        "HeatZoneRollbackPayload",
        "HeatZoneScoreJobPayload",
        "create_heatzone_router",
    ]
