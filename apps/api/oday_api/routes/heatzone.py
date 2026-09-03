from __future__ import annotations

from datetime import UTC, date, datetime
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
from modules.heatzone.application.absorption_inputs import (
    AbsorptionInputError,
    assemble_zone_absorption,
)
from modules.heatzone.application.absorption_outcome_recorder import (
    AbsorptionOutcomeConflictError,
    AbsorptionOutcomeWriteError,
    UnregisteredCellError,
    record_absorption_outcome,
)
from modules.heatzone.application.merge_split_engine import (
    MergeSplitPolicyError,
    evaluate_merge_split,
)
from modules.heatzone.application.merge_split_evidence import (
    EvidenceUnavailableError,
    ExistingZoneComposition,
    MergeSplitEvidenceRepository,
    assemble_merge_split_evidence,
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
    HEATZONE_ABSORPTION_POLICY_KIND,
    HEATZONE_MERGE_POLICY_KIND,
    DecisionPolicyRepository,
    InMemoryDecisionPolicyRepository,
    resolve_policy,
)

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
    from pydantic import BaseModel, ConfigDict, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:
    from modules.heatzone.workers import run_heatzone_batch_score


    class HeatZoneScoreJobPayload(BaseModel):
        features: list[dict[str, Any]] = Field(default_factory=list)
        prediction_origin_time: str | None = None
        idempotency_key: str | None = None


    class HeatZoneOverridePayload(BaseModel):
        """Human override of a composition.

        The deciding operator is taken from the authenticated principal, so the
        body carries only the reason and the shape of the override. `extra` is
        forbidden so a client that still sends `decided_by` is told its identity
        claim was rejected instead of having it silently dropped.
        """

        model_config = ConfigDict(extra="forbid")

        override_reason: str = Field(min_length=1)
        decision_policy_version_id: str | None = None
        new_kind: str | None = None
        member_cell_ids: list[str] | None = None
        parent_zone_id: str | None = None


    class HeatZoneRollbackPayload(BaseModel):
        model_config = ConfigDict(extra="forbid")

        revert_reason: str | None = None


    class HeatZoneMergeSplitEvaluatePayload(BaseModel):
        """Request to evaluate merge/split for the caller's tenant.

        There is nothing to send but the policy to evaluate under. Readiness
        metrics and cell outcomes are read from trusted server-side HZ-004
        evidence; a request that supplies them is refused rather than obeyed,
        because a caller able to name its own maturity could talk the engine
        past a gate the production snapshot fails.
        """

        model_config = ConfigDict(extra="forbid")

        policy_version_id: str | None = None


    class HeatZoneProposalApprovePayload(BaseModel):
        model_config = ConfigDict(extra="forbid")

        notes: str | None = None


    class HeatZoneProposalRejectPayload(BaseModel):
        model_config = ConfigDict(extra="forbid")

        reason: str = Field(min_length=1)


    class HeatZoneAbsorptionOutcomePayload(BaseModel):
        """One cell-period of HZ-004 absorption, to be measured and recorded.

        The body carries *inputs*, not results. `absorbed_demand`,
        `absorption_ratio`, `absorbing_store_count` and `under_realized` are
        computed here from the published `oday.store-daily-performance.v1` and
        `oday.operational-start-observation.v1` rows, and the basis snapshot ids
        are lifted from each row's `raw_contract_fingerprint`. A caller cannot
        state what a zone absorbed, because merge/split is judged against this
        history and a caller who could write the evidence could decide the
        merge.
        """

        model_config = ConfigDict(extra="forbid")

        cell_id: str = Field(min_length=1)
        period_start: str = Field(min_length=1)
        period_end: str = Field(min_length=1)
        original_demand: float = Field(gt=0.0)
        store_ids: list[str] = Field(min_length=1)
        performances: list[dict[str, Any]] = Field(min_length=1)
        operational_starts: list[dict[str, Any]] = Field(min_length=1)
        barrier_side: str | None = None
        barrier_description: str = ""


    def create_heatzone_router(
        *,
        store: HeatZoneResultStore | None = None,
        composition_repository: HeatZoneCompositionRepository | None = None,
        policy_repository: DecisionPolicyRepository | None = None,
        heatzone_store_for_tenant: Any | None = None,
        composition_repository_for_tenant: Any | None = None,
        evidence_repository: MergeSplitEvidenceRepository | None = None,
        evidence_repository_for_tenant: Any | None = None,
        absorption_outcome_writer: Any | None = None,
        absorption_outcome_writer_for_tenant: Any | None = None,
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

        def evidence_repo_for_request(request: Request) -> Any:
            tid = resolve_tenant_id(request)
            if evidence_repository_for_tenant is not None:
                return evidence_repository_for_tenant(tid)
            return evidence_repository

        def absorption_writer_for_request(request: Request) -> Any:
            tid = resolve_tenant_id(request)
            if absorption_outcome_writer_for_tenant is not None:
                return absorption_outcome_writer_for_tenant(tid)
            return absorption_outcome_writer

        def resolve_merge_policy(
            tenant_id: str, explicit_version_id: str | None, at: datetime
        ) -> Any:
            """The governed heatzone_merge policy this decision runs under.

            Naming a version explicitly selects one, it does not exempt it: the
            named row still has to be a `heatzone_merge` policy, belong to this
            tenant, and be in force now. Without those checks a caller could
            point the engine at any policy row it could name -- a retired
            version whose thresholds it prefers, or a policy of another kind
            whose parameters happen to parse -- which is the substitution
            `resolve_policy` refuses to make on the implicit path.
            """
            if not explicit_version_id:
                try:
                    return resolve_policy(
                        pol_repo,
                        policy_kind=HEATZONE_MERGE_POLICY_KIND,
                        tenant_id=tenant_id,
                        at=at,
                    )
                except Exception as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            f"failed to resolve governed {HEATZONE_MERGE_POLICY_KIND} policy "
                            f"for tenant '{tenant_id}': {exc}"
                        ),
                    ) from exc

            # `find_version` is on the DecisionPolicyRepository protocol, so
            # both the in-memory and SQL registries answer it directly.
            policy = pol_repo.find_version(explicit_version_id)
            if policy is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"decision policy version '{explicit_version_id}' not found",
                )
            if policy.tenant_id != tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"decision policy version '{explicit_version_id}' does not belong "
                        f"to tenant '{tenant_id}'"
                    ),
                )
            if policy.policy_kind != HEATZONE_MERGE_POLICY_KIND:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"decision policy version '{explicit_version_id}' is of kind "
                        f"'{policy.policy_kind}', not '{HEATZONE_MERGE_POLICY_KIND}'"
                    ),
                )
            if not policy.covers(at):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"decision policy version '{explicit_version_id}' is not in force at "
                        f"{at.isoformat()}"
                    ),
                )
            return policy

        def operator_actor(request: Request) -> str:
            """Identity of the authenticated operator behind this request.

            Governance rows name a person, so the name has to come from the
            credential the authorization dependency already verified. Taking it
            from the body would let any holder of the permission write someone
            else into the audit trail.
            """
            principal = getattr(request.state, "operator_principal", None)
            actor = getattr(principal, "subject_id", None) or getattr(
                request.state, "operator_subject_id", None
            )
            if not actor or not str(actor).strip():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "a verified operator identity is required to decide a "
                        "heat-zone composition"
                    ),
                )
            return str(actor).strip()

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
            policy = resolve_merge_policy(tid, body.policy_version_id, datetime.now(UTC))

            active_comp_repo = comp_repo_for_request(request)
            existing_zones: list[ExistingZoneComposition] = []
            zone_members: dict[str, dict[str, Any]] = {}
            for record in active_comp_repo.list_compositions(tid, active_only=True):
                entry = zone_members.setdefault(
                    record.zone_id,
                    {"kind": record.composition_kind.value, "cells": []},
                )
                entry["cells"].append(record.member_cell_id)
            for zone_id, entry in sorted(zone_members.items()):
                existing_zones.append(
                    ExistingZoneComposition(
                        zone_id=zone_id,
                        composition_kind=entry["kind"],
                        member_cell_ids=tuple(sorted(entry["cells"])),
                    )
                )

            try:
                evidence = assemble_merge_split_evidence(
                    evidence_repo_for_request(request),
                    tenant_id=tid,
                    existing_zones=existing_zones,
                )
            except EvidenceUnavailableError as exc:
                # No trusted evidence at all is not the same as immature
                # evidence: the service cannot tell whether it is allowed to
                # act, so it refuses instead of evaluating on nothing.
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "HZ006_EVIDENCE_UNAVAILABLE",
                        "message": str(exc),
                    },
                ) from exc

            try:
                evaluation = evaluate_merge_split(evidence, policy=policy)
            except MergeSplitPolicyError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "HZ006_POLICY_INCOMPLETE", "message": str(exc)},
                ) from exc

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
                        "source_snapshot_id": evidence.snapshot.inventory_version,
                        "source_snapshot_sha256": evidence.snapshot.content_sha256,
                        "governed_disabled": evidence.snapshot.governed_disabled,
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
            decided_by = operator_actor(request)
            active_comp_repo = comp_repo_for_request(request)
            try:
                updated_prop, created_records = active_comp_repo.approve_proposal(
                    proposal_id=proposal_id,
                    tenant_id=tid,
                    approved_by=decided_by,
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
                    actor=decided_by,
                    action="approve",
                    resource=f"heatzone/proposals/{proposal_id}",
                    outcome="approved",
                    correlation_id=request.state.correlation_id,
                    metadata={
                        "proposal_id": proposal_id,
                        "zone_id": updated_prop.zone_id,
                        "created_count": len(created_records),
                        "decided_by": decided_by,
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
            rejected_by = operator_actor(request)
            active_comp_repo = comp_repo_for_request(request)
            try:
                updated_prop = active_comp_repo.reject_proposal(
                    proposal_id=proposal_id,
                    tenant_id=tid,
                    rejected_by=rejected_by,
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
                    actor=rejected_by,
                    action="reject",
                    resource=f"heatzone/proposals/{proposal_id}",
                    outcome="rejected",
                    correlation_id=request.state.correlation_id,
                    metadata={
                        "proposal_id": proposal_id,
                        "rejected_by": rejected_by,
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
            decided_by = operator_actor(request)
            if decided_by == "system":
                # 'system' is the reserved actor for automated decisions, and
                # the composition constraint keys off it: a principal carrying
                # that subject id would write a human override that the
                # override_reason check then rejects at the storage layer.
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="'system' is reserved for automated decisions and cannot override",
                )
            if not body.override_reason or not body.override_reason.strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Human override requires a non-empty 'override_reason'",
                )

            # Spelling `heatzone-merge-v1:<tenant>` here would put a policy
            # version id on a governance row without ever asking the registry
            # whether that version exists or is in force. The composition's
            # foreign key would catch it eventually, but only at write time,
            # after the override had already been decided (ODP-SD-AMD-001 3.3).
            policy_version = resolve_merge_policy(
                tid, body.decision_policy_version_id, datetime.now(UTC)
            ).policy_version_id
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
                    decided_by=decided_by,
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
                    actor=decided_by,
                    action="override",
                    resource=f"heatzone/zones/{zone_id}",
                    outcome="success",
                    correlation_id=request.state.correlation_id,
                    metadata={
                        "zone_id": zone_id,
                        "override_reason": body.override_reason,
                        "decided_by": decided_by,
                        "decision_policy_version_id": policy_version,
                        "updated_cell_count": len(updated),
                    },
                )
            )

            return {
                "zone_id": zone_id,
                "status": "overridden",
                "decided_by": decided_by,
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
            reverted_by = operator_actor(request)
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
                    actor=reverted_by,
                    action="rollback",
                    resource=f"heatzone/zones/{zone_id}",
                    outcome="success",
                    correlation_id=request.state.correlation_id,
                    metadata={
                        "zone_id": zone_id,
                        "reverted_by": reverted_by,
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

        @router.post(
            "/absorption/outcomes",
            dependencies=[
                Depends(
                    require_permission("heatzone_absorption", Action.CREATE, engine=authz_engine)
                )
            ],
        )
        def record_heatzone_absorption_outcome(
            body: HeatZoneAbsorptionOutcomePayload,
            request: Request,
        ) -> dict[str, Any]:
            """Measure one cell-period of HZ-004 absorption and append it to evidence.

            This is the production entry that fills
            `expansion.heatzone_absorption_outcomes`. Without it the relation
            merge/split reads is one nothing writes, so `evaluate` would abstain
            on empty evidence forever and the only histories that ever existed
            would be the ones tests put in the fixture by hand.

            It is deliberately not on the merge/split surface: it carries its
            own `heatzone_absorption` permission, which the roles that approve
            compositions are not granted.
            """
            tid = resolve_tenant_id(request)
            writer = absorption_writer_for_request(request)
            if writer is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "HZ004_OUTCOME_WRITER_UNAVAILABLE",
                        "message": "no HZ-004 absorption outcome writer is configured",
                    },
                )

            try:
                period_start = date.fromisoformat(body.period_start)
                period_end = date.fromisoformat(body.period_end)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"period bounds must be ISO dates: {exc}",
                ) from exc

            try:
                policy = resolve_policy(
                    pol_repo,
                    policy_kind=HEATZONE_ABSORPTION_POLICY_KIND,
                    tenant_id=tid,
                    at=datetime.now(UTC),
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"failed to resolve governed {HEATZONE_ABSORPTION_POLICY_KIND} policy "
                        f"for tenant '{tid}': {exc}"
                    ),
                ) from exc

            try:
                result = assemble_zone_absorption(
                    store_ids=list(body.store_ids),
                    performances=body.performances,
                    operational_starts=body.operational_starts,
                    original_demand=body.original_demand,
                    policy=policy,
                    as_of=period_end,
                    observation_window_start=period_start,
                    observation_window_end=period_end,
                )
            except AbsorptionInputError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "HZ004_INPUT_REFUSED", "message": str(exc)},
                ) from exc

            if result is None:
                # `assemble_zone_absorption` fails closed on incomplete
                # coverage. Recording a partial period would put a number in the
                # history that looks measured and is not, and every later merge
                # would inherit it.
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "HZ004_NOT_MEASURABLE",
                        "message": (
                            f"absorption for cell '{body.cell_id}' over "
                            f"{body.period_start}..{body.period_end} is not measurable from the "
                            "supplied rows; coverage must be complete and traceable"
                        ),
                    },
                )

            try:
                recorded = record_absorption_outcome(
                    writer,
                    tenant_id=tid,
                    cell_id=body.cell_id,
                    period_start=period_start,
                    period_end=period_end,
                    result=result,
                    policy=policy,
                    barrier_side=body.barrier_side,
                    barrier_description=body.barrier_description,
                )
            except AbsorptionOutcomeConflictError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "HZ004_OUTCOME_CONFLICT", "message": str(exc)},
                ) from exc
            except UnregisteredCellError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "HZ004_UNREGISTERED_CELL", "message": str(exc)},
                ) from exc
            except AbsorptionOutcomeWriteError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "HZ004_OUTCOME_REFUSED", "message": str(exc)},
                ) from exc

            active_audit_log.record(
                AuditEvent(
                    event_type="heatzone.absorption.outcome.recorded.v1",
                    actor="system",
                    action="record_outcome",
                    resource=f"heatzone/absorption/{body.cell_id}",
                    outcome="recorded",
                    correlation_id=request.state.correlation_id,
                    metadata={
                        "cell_id": recorded.cell_id,
                        "period_start": recorded.period_start.isoformat(),
                        "period_end": recorded.period_end.isoformat(),
                        "barrier_side": recorded.barrier_side,
                        "absorption_ratio": recorded.absorption_ratio,
                        "absorbing_store_count": recorded.absorbing_store_count,
                        "basis_source_ids": list(recorded.basis_source_ids),
                        "absorption_policy_version_id": recorded.absorption_policy_version_id,
                    },
                )
            )
            return {
                "cell_id": recorded.cell_id,
                "period_start": recorded.period_start.isoformat(),
                "period_end": recorded.period_end.isoformat(),
                "original_demand": recorded.original_demand,
                "absorbed_demand": recorded.absorbed_demand,
                "remaining_demand": recorded.remaining_demand,
                "absorption_ratio": recorded.absorption_ratio,
                "absorbing_store_count": recorded.absorbing_store_count,
                "under_realized": recorded.under_realized,
                "barrier_side": recorded.barrier_side,
                "barrier_description": recorded.barrier_description,
                "basis_source_ids": list(recorded.basis_source_ids),
                "basis_at": recorded.basis_at.isoformat(),
                "absorption_policy_version_id": recorded.absorption_policy_version_id,
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
        "HeatZoneAbsorptionOutcomePayload",
        "HeatZoneMergeSplitEvaluatePayload",
        "HeatZoneOverridePayload",
        "HeatZoneResultStore",
        "HeatZoneRollbackPayload",
        "HeatZoneScoreJobPayload",
        "create_heatzone_router",
    ]
