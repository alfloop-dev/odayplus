from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from typing import Any
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from shared.audit import AuditEvent, InMemoryAuditLog


class TransitionPayload(BaseModel):
    issueId: str | None = None
    status: str | None = None
    note: str | None = None
    notes: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None
    ownerRoleId: str | None = None
    ownerName: str | None = None
    severity: str | None = None


class ApprovalDecisionPayload(BaseModel):
    status: str | None = None
    action: str | None = None
    reason: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None
    role: str | None = None


class EvidencePurposePayload(BaseModel):
    purpose: str = Field(min_length=1)
    cameraLocation: str | None = None
    timeWindow: str | None = None
    retentionHours: int | None = None
    privacyAcknowledged: bool | None = None
    auditNote: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None


# Growth workspace payloads
# ---------------------------------------------------------------------------

class GrowthDraftPayload(BaseModel):
    """Payload for creating a Growth Action draft from a PriceOps recommendation."""
    name: str = Field(min_length=1)
    segmentId: str = Field(min_length=1)
    sourceRecommendationId: str | None = None
    objective: str = Field(min_length=1)
    targetLift: float
    observationWindowDays: int = Field(default=14, ge=1)
    rationale: str = ""
    rollbackPlan: str = ""
    actorName: str | None = None
    idempotency_key: str | None = None


class GrowthOutcomePayload(BaseModel):
    """Writeback payload for effectiveness verdict + closeout."""
    outcome: str  # EFFECTIVE | INEFFECTIVE | INCONCLUSIVE | PENDING
    observedLift: float | None = None
    evidenceLevel: str = "medium"  # high | medium | low
    rationale: str = ""
    requiredAction: str = "CLOSE"  # CLOSE | ROLLBACK | CONTINUE_OBSERVATION | STRENGTHEN_EVIDENCE
    actorName: str | None = None
    idempotency_key: str | None = None


def create_operator_router(
    *,
    audit_log: InMemoryAuditLog | None = None,
    document_store: Any = None,
) -> APIRouter:
    from apps.api.oday_api.security.dependencies import build_engine, require_permission
    from shared.auth import Action

    active_audit_log = audit_log or InMemoryAuditLog()
    authz_engine = build_engine(audit_log=active_audit_log)
    store = OperatorStateStore(audit_log=active_audit_log, document_store=document_store)
    idempotency_cache: dict[str, dict[str, Any]] = {}

    router = APIRouter(prefix="/operator", tags=["operator"])

    read_guard = Depends(require_permission("intervention", Action.VIEW, engine=authz_engine))
    write_guard = Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))
    approve_guard = Depends(require_permission("intervention", Action.APPROVE, engine=authz_engine))

    @router.get("/bootstrap", dependencies=[read_guard])
    def bootstrap() -> dict[str, Any]:
        return store.snapshot()

    @router.get("/today", dependencies=[read_guard])
    def get_today() -> dict[str, Any]:
        state = store.snapshot()
        return {
            "kpis": state["kpis"],
            "workQueue": state["workQueue"],
            "decisions": state["decisions"],
            "riskRows": state["riskRows"],
            "auditFeed": state["auditFeed"],
            "notifications": state["notifications"],
            "tasks": state["tasks"],
        }

    @router.get("/issues", dependencies=[read_guard])
    def get_issues() -> dict[str, Any]:
        items = store.snapshot()["issues"]
        return {"items": items, "count": len(items)}

    @router.get("/approvals", dependencies=[read_guard])
    def get_approvals() -> dict[str, Any]:
        state = store.snapshot()
        return {
            "items": state["approvals"],
            "count": len(state["approvals"]),
            "decisions": state["governanceDecisions"],
            "auditRows": state["governanceAuditRows"],
        }

    @router.get("/notifications", dependencies=[read_guard])
    def get_notifications() -> dict[str, Any]:
        items = store.snapshot()["notifications"]
        return {"items": items, "count": len(items)}

    @router.get("/tasks", dependencies=[read_guard])
    def get_tasks() -> dict[str, Any]:
        items = store.snapshot()["tasks"]
        return {"items": items, "count": len(items)}

    @router.get("/search", dependencies=[read_guard])
    def search(q: str = Query(default="")) -> dict[str, Any]:
        return store.search(q)

    @router.post("/issues/{issue_id}/{action_type}", dependencies=[write_guard])
    def transition_issue(
        issue_id: str,
        action_type: str,
        body: TransitionPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        cache_key = _cache_key(request, idempotency_key)
        if cache_key in idempotency_cache:
            return deepcopy(idempotency_cache[cache_key])

        result = store.transition_issue(
            issue_id=issue_id,
            action_type=action_type,
            body=body,
            correlation_id=request.state.correlation_id,
            idempotency_key=idempotency_key,
        )
        _remember(idempotency_cache, cache_key, result)
        return result

    @router.post("/approvals/{approval_id}/decision", dependencies=[approve_guard])
    def decide_approval(
        approval_id: str,
        body: ApprovalDecisionPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        cache_key = _cache_key(request, idempotency_key)
        if cache_key in idempotency_cache:
            return deepcopy(idempotency_cache[cache_key])

        result = store.decide_approval(
            approval_id=approval_id,
            body=body,
            correlation_id=request.state.correlation_id,
            idempotency_key=idempotency_key,
        )
        _remember(idempotency_cache, cache_key, result)
        return result

    @router.post("/evidence/{evidence_id}/purpose", dependencies=[write_guard])
    def confirm_evidence_purpose(
        evidence_id: str,
        body: EvidencePurposePayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        cache_key = _cache_key(request, idempotency_key)
        if cache_key in idempotency_cache:
            return deepcopy(idempotency_cache[cache_key])

        result = store.confirm_evidence_purpose(
            evidence_id=evidence_id,
            body=body,
            correlation_id=request.state.correlation_id,
            idempotency_key=idempotency_key,
        )
        _remember(idempotency_cache, cache_key, result)
        return result

    # -----------------------------------------------------------------------
    # Growth workspace — segments / recommendations / actions / draft / outcome
    # -----------------------------------------------------------------------

    # In-memory growth store — seeded from the canonical fixture set so reads
    # reflect real domain data; writes (draft + outcome) mutate this store.
    growth_segments: list[dict[str, Any]] = [
        {
            "id": "seg-metro-dinner",
            "name": "都會晚餐高潛力組",
            "definition": "六都 · 晚餐時段營收占比 > 45% · 近 8 週交易量成長",
            "storeCount": 42,
            "revenueShare": "31.4%",
            "trend": "up",
            "opportunity": "晚餐客單價仍低於同商圈基準，具備定價上調空間。",
            "dataStatus": "FRESH",
        },
        {
            "id": "seg-suburb-lunch",
            "name": "郊區午餐守成組",
            "definition": "非六都 · 午餐時段營收占比 > 50% · 交易量持平",
            "storeCount": 28,
            "revenueShare": "18.7%",
            "trend": "flat",
            "opportunity": "午餐主力商品需求彈性高，調價風險大，優先觀察。",
            "dataStatus": "FRESH",
        },
        {
            "id": "seg-latenight-delivery",
            "name": "宵夜外送流失組",
            "definition": "外送占比 > 60% · 近 12 週宵夜時段營收下滑",
            "storeCount": 17,
            "revenueShare": "9.2%",
            "trend": "down",
            "opportunity": "外送費結構偏高，可測試小幅下調搭配廣告增量。",
            "dataStatus": "LOW_CONFIDENCE",
        },
    ]

    growth_recommendations: list[dict[str, Any]] = [
        {
            "id": "rec-9001",
            "segmentId": "seg-metro-dinner",
            "title": "晚餐套餐 +3% 加權調價",
            "currentPrice": "現行 NT$ 168 / 198 / 238",
            "candidatePrice": "候選 NT$ 173 / 204 / 245",
            "expectedRevenueLift": 2.1,
            "expectedMarginLift": 2.8,
            "constraintStatus": "PASS",
            "confidenceScore": 0.81,
            "validUntil": "2026-07-20T00:00:00Z",
            "rationale": "近 8 週晚餐時段需求彈性 -0.34（低彈性），基準商圈定價高 4.2%，調幅在 constraint 範圍內。",
        },
        {
            "id": "rec-9002",
            "segmentId": "seg-latenight-delivery",
            "title": "宵夜外送 -5% 試降",
            "currentPrice": "現行 NT$ 45 / 外送費",
            "candidatePrice": "候選 NT$ 43 / 外送費",
            "expectedRevenueLift": -0.8,
            "expectedMarginLift": 1.2,
            "constraintStatus": "WATCH",
            "confidenceScore": 0.61,
            "validUntil": "2026-07-18T00:00:00Z",
            "rationale": "宵夜外送需求彈性 -1.12，小幅降費可能回拉流失單量；信心分數偏低，建議先限量測試。",
        },
    ]

    growth_actions: list[dict[str, Any]] = [
        {
            "id": "growth-7001",
            "name": "晚餐套餐加權調價 · 第一批",
            "segmentId": "seg-metro-dinner",
            "sourceRecommendationId": "rec-9001",
            "status": "ACTIVE",
            "objective": "提升晚餐時段套餐客單價",
            "targetLift": 2.1,
            "observationWindowDays": 14,
            "rationale": "低彈性時段可承受溫和調幅",
            "rollbackPlan": "若 7 日內 conversion 下滑 > 5%，回復原價。",
            "createdAt": "2026-07-01T09:00:00Z",
            "metadata": {
                "decisionId": "dec-growth-7001",
                "correlationId": "corr-growth-7001",
                "modelVersion": "growth-uplift-v1.4.0",
                "policyVersion": "growth-policy-2026.07",
                "featureSnapshotTime": "2026-07-01T06:00:00Z",
            },
        },
        {
            "id": "growth-7002",
            "name": "宵夜外送試降 · A 組",
            "segmentId": "seg-latenight-delivery",
            "sourceRecommendationId": "rec-9002",
            "status": "ACTIVE",
            "objective": "回拉宵夜外送流失單量",
            "targetLift": 1.2,
            "observationWindowDays": 14,
            "rationale": "限量測試低費率對單量的回拉效果",
            "rollbackPlan": "若 GMV 淨負，立即回復。",
            "createdAt": "2026-07-03T11:00:00Z",
            "metadata": {
                "decisionId": "dec-growth-7002",
                "correlationId": "corr-growth-7002",
                "modelVersion": "growth-uplift-v1.4.0",
                "policyVersion": "growth-policy-2026.07",
                "featureSnapshotTime": "2026-07-03T08:00:00Z",
            },
        },
        {
            "id": "growth-7003",
            "name": "午餐觀察期延長 · 郊區組",
            "segmentId": "seg-suburb-lunch",
            "sourceRecommendationId": None,
            "status": "PENDING_EVIDENCE",
            "objective": "等待更多需求彈性數據再決策",
            "targetLift": 0.5,
            "observationWindowDays": 28,
            "rationale": "現有數據信心不足，延長觀察期",
            "rollbackPlan": "N/A（純觀察，無價格動作）",
            "createdAt": "2026-07-05T14:00:00Z",
            "metadata": {
                "decisionId": "dec-growth-7003",
                "correlationId": "corr-growth-7003",
                "modelVersion": "growth-uplift-v1.4.0",
                "policyVersion": "growth-policy-2026.07",
                "featureSnapshotTime": "2026-07-05T10:00:00Z",
            },
        },
        {
            "id": "growth-7004",
            "name": "晚餐調價第一批成效驗收",
            "segmentId": "seg-metro-dinner",
            "sourceRecommendationId": "rec-9001",
            "status": "EFFECTIVE",
            "objective": "驗收加權調價的實際增益",
            "targetLift": 2.1,
            "observationWindowDays": 14,
            "rationale": "觀察期已結束，數據顯示正向增益",
            "rollbackPlan": "已達標，維持現價。",
            "createdAt": "2026-06-17T09:00:00Z",
            "growth_outcome": "EFFECTIVE",
            "required_action": "CLOSE",
            "metadata": {
                "decisionId": "dec-growth-7004",
                "correlationId": "corr-growth-7004",
                "modelVersion": "growth-uplift-v1.4.0",
                "policyVersion": "growth-policy-2026.07",
                "featureSnapshotTime": "2026-06-17T06:00:00Z",
            },
        },
    ]

    growth_freshness: dict[str, Any] = {
        "modelVersion": "growth-uplift-v1.4.0",
        "policyVersion": "growth-policy-2026.07",
        "featureSnapshotTime": "2026-07-13T06:00:00Z",
        "dataStatus": "FRESH",
        "lastRefreshed": "2026-07-13T06:05:00Z",
    }

    growth_idempotency: dict[str, Any] = {}

    growth_rbac = Depends(
        require_permission("growth", Action.CREATE, engine=authz_engine)
    )

    @router.get(
        "/growth/freshness",
        dependencies=[read_guard],
        tags=["growth"],
    )
    def get_growth_freshness() -> dict[str, Any]:
        """Return data freshness metadata for the Growth workspace."""
        return growth_freshness

    @router.get(
        "/growth/segments",
        dependencies=[read_guard],
        tags=["growth"],
    )
    def get_growth_segments() -> dict[str, Any]:
        """List available growth segments."""
        return {"items": growth_segments, "count": len(growth_segments)}

    @router.get(
        "/growth/recommendations",
        dependencies=[read_guard],
        tags=["growth"],
    )
    def get_growth_recommendations(
        segment_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Return PriceOps recommendations, optionally filtered by segment_id."""
        items = (
            [r for r in growth_recommendations if r["segmentId"] == segment_id]
            if segment_id
            else growth_recommendations
        )
        return {"items": items, "count": len(items)}

    @router.get(
        "/growth/actions",
        dependencies=[read_guard],
        tags=["growth"],
    )
    def get_growth_actions(
        segment_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Return Growth actions, optionally filtered by segment_id."""
        items = (
            [a for a in growth_actions if a["segmentId"] == segment_id]
            if segment_id
            else growth_actions
        )
        return {"items": items, "count": len(items)}

    @router.post(
        "/growth/actions",
        dependencies=[growth_rbac],
        tags=["growth"],
    )
    def create_growth_draft(
        body: GrowthDraftPayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Create a Growth Action draft (DRAFT status). Idempotent on Idempotency-Key."""
        effective_key = idempotency_key or body.idempotency_key
        if effective_key and effective_key in growth_idempotency:
            return growth_idempotency[effective_key]

        correlation_id = x_correlation_id or f"corr-{uuid.uuid4().hex[:12]}"
        action_id = f"growth-{uuid.uuid4().hex[:8]}"
        new_action: dict[str, Any] = {
            "id": action_id,
            "name": body.name,
            "segmentId": body.segmentId,
            "sourceRecommendationId": body.sourceRecommendationId,
            "status": "DRAFT",
            "objective": body.objective,
            "targetLift": body.targetLift,
            "observationWindowDays": body.observationWindowDays,
            "rationale": body.rationale,
            "rollbackPlan": body.rollbackPlan,
            "createdAt": datetime.now(UTC).isoformat(),
            "metadata": {
                "decisionId": f"dec-{action_id}",
                "correlationId": correlation_id,
                "modelVersion": growth_freshness["modelVersion"],
                "policyVersion": growth_freshness["policyVersion"],
                "featureSnapshotTime": growth_freshness["featureSnapshotTime"],
            },
        }

        growth_actions.append(new_action)
        if audit_log is not None:
            audit_log.append(
                AuditEvent(
                    event_type="growth.draft_created.v1",
                    actor=body.actorName or "operator",
                    action="create",
                    resource=f"growth/actions/{action_id}",
                    outcome="accepted",
                    correlation_id=correlation_id,
                    metadata={
                        "action_id": action_id,
                        "segment_id": body.segmentId,
                        "source_recommendation_id": body.sourceRecommendationId,
                        "idempotency_key": effective_key,
                    },
                )
            )

        result = {**new_action, "correlation_id": correlation_id}
        if effective_key:
            growth_idempotency[effective_key] = result
        return result

    @router.post(
        "/growth/actions/{action_id}/outcome",
        dependencies=[growth_rbac],
        tags=["growth"],
    )
    def write_growth_outcome(
        action_id: str,
        body: GrowthOutcomePayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Record effectiveness verdict and required action for a Growth Action.

        Idempotent: repeat POST with same Idempotency-Key returns cached response.
        """
        outcome_key = idempotency_key or body.idempotency_key
        if idempotency_key and outcome_key in growth_idempotency:
            return growth_idempotency[outcome_key]

        action = next((a for a in growth_actions if a["id"] == action_id), None)
        if action is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"growth action {action_id!r} not found",
            )

        correlation_id = x_correlation_id or f"corr-{uuid.uuid4().hex[:12]}"
        action["growth_outcome"] = body.outcome
        action["required_action"] = body.requiredAction
        action["status"] = {
            "EFFECTIVE": "EFFECTIVE",
            "INEFFECTIVE": "INEFFECTIVE",
            "INCONCLUSIVE": "PENDING_EVIDENCE",
            "PENDING": "PENDING_EVIDENCE",
        }.get(body.outcome, "PENDING_EVIDENCE")

        if audit_log is not None:
            audit_log.append(
                AuditEvent(
                    event_type="growth.outcome_written.v1",
                    actor=body.actorName or "system",
                    action="evaluate",
                    resource=f"growth/actions/{action_id}",
                    outcome="accepted",
                    correlation_id=correlation_id,
                    metadata={
                        "action_id": action_id,
                        "growth_outcome": body.outcome,
                        "required_action": body.requiredAction,
                        "observed_lift": body.observedLift,
                        "evidence_level": body.evidenceLevel,
                        "idempotency_key": idempotency_key,
                    },
                )
            )

        result = {
            **action,
            "growth_outcome": body.outcome,
            "required_action": body.requiredAction,
            "correlation_id": correlation_id,
        }
        if idempotency_key:
            growth_idempotency[outcome_key] = result
        return result

    return router


class OperatorStateStore:
    """Small operator-console state store used by API-backed product E2E.

    Memory mode mirrors the rest of the default API test runtime. When a
    SqliteDocumentStore is supplied by the durable persistence bundle, every
    workflow write saves the whole operator state so the console survives API
    process restarts during product-grade E2E.
    """

    _COLLECTION = "opsboard.operator"
    _DOC_ID = "console-state"

    def __init__(self, *, audit_log: InMemoryAuditLog, document_store: Any = None) -> None:
        self._audit_log = audit_log
        self._document_store = document_store
        self._lock = RLock()
        persisted = document_store.get(self._COLLECTION, self._DOC_ID) if document_store else None
        self._state: dict[str, Any] = persisted or _initial_operator_state()
        if document_store and persisted is None:
            self._persist()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def search(self, query: str) -> dict[str, Any]:
        normalized = query.strip().lower()
        with self._lock:
            candidates = []
            for item in self._state["workQueue"]:
                candidates.append(
                    {
                        "type": "issue",
                        "id": item["id"],
                        "title": item["title"],
                        "summary": item["description"],
                        "workspace": item["workspace"],
                    }
                )
            for item in self._state["approvals"]:
                candidates.append(
                    {
                        "type": "approval",
                        "id": item["id"],
                        "title": item["title"],
                        "summary": item["summary"],
                        "workspace": "govern",
                    }
                )
            for item in self._state["governanceAuditRows"][:8]:
                candidates.append(
                    {
                        "type": "audit",
                        "id": item["id"],
                        "title": item["action"],
                        "summary": item["summary"],
                        "workspace": "govern",
                    }
                )
            if normalized:
                items = [
                    item
                    for item in candidates
                    if normalized
                    in " ".join(str(value).lower() for value in item.values()).lower()
                ]
            else:
                items = candidates[:10]
            return {"query": query, "items": items, "count": len(items)}

    def transition_issue(
        self,
        *,
        issue_id: str,
        action_type: str,
        body: TransitionPayload,
        correlation_id: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        action = _normalize_issue_action(action_type)
        now = _now()
        now_label = _time_label(now)
        actor = _actor(body.actorName, body.actorRoleId)
        note = body.note or body.notes or f"{issue_id} {action['label']} submitted."

        with self._lock:
            issue = _find_by_id(self._state["issues"], issue_id)
            queue_item = _find_by_id(self._state["workQueue"], issue_id)
            if issue is None and queue_item is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="issue not found")

            if issue is not None:
                issue["status"] = action["status"]
                issue["updatedAt"] = now.isoformat()
                if body.ownerRoleId:
                    issue["ownerRoleId"] = body.ownerRoleId
                if body.ownerName:
                    issue["ownerName"] = body.ownerName
                if body.severity:
                    issue["severity"] = body.severity

            if queue_item is not None:
                queue_item["status"] = action["display_status"]
                queue_item["time"] = now_label
                queue_item["owner"] = body.ownerName or queue_item.get("owner", "營運")

            self._append_audit_feed(
                actor=actor,
                category="Workflow",
                detail=f"{issue_id} {action['label']} -> {action['status']}. {note}",
                time=now_label,
            )
            self._append_governance_audit(
                category="issue",
                actor=actor,
                action=action["audit_action"],
                module="Store Ops",
                entity_ref=issue_id,
                summary=f"{issue_id} moved to {action['status']} with note: {note}",
                correlation_id=correlation_id,
                reason=note,
                timestamp=now,
            )
            self._append_notification(
                title=f"{issue_id} {action['display_status']}",
                detail=f"{actor} completed {action['label']} for {issue_id}.",
                tone=action["tone"],
            )
            self._append_task(
                target_id=issue_id,
                title=f"Follow up {issue_id} after {action['label']}",
                owner=body.ownerName or actor,
                status="open" if action["status"] not in {"closed", "observing"} else "watching",
                created_at=now,
            )
            self._record_platform_audit(
                event_type="operator.issue.transition",
                actor=actor,
                action=action_type,
                resource=f"operator/issue/{issue_id}",
                outcome=action["status"],
                correlation_id=correlation_id,
                metadata={"idempotency_key": idempotency_key, "note": note},
            )
            self._persist()
            return self.snapshot()

    def decide_approval(
        self,
        *,
        approval_id: str,
        body: ApprovalDecisionPayload,
        correlation_id: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        decision = _normalize_approval_decision(body.status or body.action)
        reason = (body.reason or "").strip()
        if decision in {"returned", "rejected"} and len(reason) < 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="return/reject decisions require a reason of at least 10 characters",
            )

        now = _now()
        actor = _actor(body.actorName or body.role, body.actorRoleId)
        label = {"approved": "Approved", "returned": "Returned", "rejected": "Rejected"}[decision]
        reason = reason or "符合風險與預算規範"

        with self._lock:
            approval = _find_by_id(self._state["approvals"], approval_id)
            rail_decision = _find_by_id(self._state["decisions"], approval_id)
            if approval is None and rail_decision is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="approval not found")

            if approval is not None:
                approval["status"] = decision
                approval["reason"] = reason
                approval["decidedAt"] = _display_timestamp(now)
                approval["decidedBy"] = actor
            if rail_decision is not None:
                rail_decision["status"] = label

            decision_row = {
                "id": f"dec-{5000 + len(self._state['governanceDecisions']) + 1}",
                "module": approval["module"] if approval else "Govern",
                "item": f"{approval.get('entityRef', approval_id)} {approval['title']}" if approval else approval_id,
                "systemRecommendation": approval.get("systemRecommendation", "Review") if approval else "Review",
                "finalDecision": label,
                "reason": reason,
                "actor": actor,
                "decidedAt": _display_timestamp(now),
                "model": "sitescore-v4.8" if approval and approval["module"] == "Network" else "ops-risk-v2.6",
                "datasetSnapshot": "operator-2026-W27",
                "approvalId": approval_id,
            }
            self._state["governanceDecisions"].insert(0, decision_row)
            self._append_audit_feed(
                actor=actor,
                category="Decision log",
                detail=f"Approval {approval_id} decided: {decision}. Reason: {reason}",
                time=_time_label(now),
            )
            self._append_governance_audit(
                category="approval",
                actor=actor,
                action="決策核准" if decision == "approved" else "決策退回" if decision == "returned" else "決策駁回",
                module=approval["module"] if approval else "Govern",
                entity_ref=approval.get("entityRef", approval_id) if approval else approval_id,
                summary=f"核准中心審查決策：{approval['title'] if approval else approval_id}，狀態變更為 {label}。",
                correlation_id=correlation_id,
                reason=reason,
                timestamp=now,
            )
            self._append_notification(
                title=f"Approval {label}",
                detail=f"{approval_id} was {decision} by {actor}.",
                tone="success" if decision == "approved" else "warning",
            )
            self._append_task(
                target_id=approval_id,
                title=f"Notify requester for {approval_id} {decision}",
                owner=actor,
                status="open",
                created_at=now,
            )
            self._record_platform_audit(
                event_type="operator.approval.decision",
                actor=actor,
                action=decision,
                resource=f"operator/approval/{approval_id}",
                outcome=decision,
                correlation_id=correlation_id,
                metadata={"idempotency_key": idempotency_key, "reason": reason},
            )
            self._persist()
            return self.snapshot()

    def confirm_evidence_purpose(
        self,
        *,
        evidence_id: str,
        body: EvidencePurposePayload,
        correlation_id: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        if body.privacyAcknowledged is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="privacy acknowledgement is required for camera evidence",
            )

        now = _now()
        actor = _actor(body.actorName, body.actorRoleId)
        detail = (
            f"Unlocked evidence {evidence_id} for purpose: {body.purpose}; "
            f"window={body.timeWindow or 'n/a'}; retention={body.retentionHours or 24}h."
        )

        with self._lock:
            self._append_audit_feed(actor=actor, category="Audit trail", detail=detail, time=_time_label(now))
            self._append_governance_audit(
                category="camera",
                actor=actor,
                action="Evidence purpose recorded",
                module="Store Ops",
                entity_ref=evidence_id,
                summary=detail,
                correlation_id=correlation_id,
                reason=body.auditNote or body.purpose,
                timestamp=now,
            )
            self._append_notification(
                title="Evidence purpose recorded",
                detail=f"{evidence_id} purpose retained for audit.",
                tone="info",
            )
            self._record_platform_audit(
                event_type="operator.evidence.purpose",
                actor=actor,
                action="purpose",
                resource=f"operator/evidence/{evidence_id}",
                outcome="recorded",
                correlation_id=correlation_id,
                metadata={
                    "idempotency_key": idempotency_key,
                    "purpose": body.purpose,
                    "camera_location": body.cameraLocation,
                    "time_window": body.timeWindow,
                    "retention_hours": body.retentionHours,
                },
            )
            self._persist()
            return self.snapshot()

    def _append_audit_feed(self, *, actor: str, category: str, detail: str, time: str) -> None:
        self._state["auditFeed"].insert(0, {"actor": actor, "category": category, "detail": detail, "time": time})
        self._state["auditFeed"] = self._state["auditFeed"][:12]

    def _append_governance_audit(
        self,
        *,
        category: str,
        actor: str,
        action: str,
        module: str,
        entity_ref: str,
        summary: str,
        correlation_id: str,
        reason: str | None = None,
        timestamp: datetime,
    ) -> None:
        row = {
            "id": f"aud-{7100 + len(self._state['governanceAuditRows']) + 1}",
            "category": category,
            "timestamp": _display_timestamp(timestamp),
            "actor": actor,
            "action": action,
            "module": module,
            "entityRef": entity_ref,
            "summary": summary,
            "reason": reason,
            "correlationId": correlation_id,
        }
        self._state["governanceAuditRows"].insert(0, row)

    def _append_notification(self, *, title: str, detail: str, tone: str) -> None:
        self._state["notifications"].insert(0, {"title": title, "detail": detail, "tone": tone})
        self._state["notifications"] = self._state["notifications"][:10]

    def _append_task(self, *, target_id: str, title: str, owner: str, status: str, created_at: datetime) -> None:
        self._state["tasks"].insert(
            0,
            {
                "id": f"TSK-{3000 + len(self._state['tasks']) + 1}",
                "targetId": target_id,
                "title": title,
                "owner": owner,
                "status": status,
                "createdAt": created_at.isoformat(),
            },
        )

    def _record_platform_audit(
        self,
        *,
        event_type: str,
        actor: str,
        action: str,
        resource: str,
        outcome: str,
        correlation_id: str,
        metadata: dict[str, Any],
    ) -> None:
        self._audit_log.record(
            AuditEvent(
                event_type=event_type,
                actor=actor,
                action=action,
                resource=resource,
                outcome=outcome,
                correlation_id=correlation_id,
                metadata=metadata,
            )
        )

    def _persist(self) -> None:
        if self._document_store is None:
            return
        self._document_store.put(self._COLLECTION, self._DOC_ID, deepcopy(self._state))


def _cache_key(request: Request, idempotency_key: str | None) -> str:
    if not idempotency_key:
        return ""
    return f"{request.method}:{request.url.path}:{idempotency_key}"


def _remember(cache: dict[str, dict[str, Any]], key: str, value: dict[str, Any]) -> None:
    if key:
        cache[key] = deepcopy(value)


def _find_by_id(items: list[dict[str, Any]], item_id: str) -> dict[str, Any] | None:
    return next((item for item in items if item.get("id") == item_id), None)


def _normalize_issue_action(action_type: str) -> dict[str, str]:
    actions = {
        "triage": {
            "status": "triaged",
            "display_status": "Triaged",
            "label": "triage",
            "audit_action": "Issue triaged",
            "tone": "info",
        },
        "assign": {
            "status": "assigned",
            "display_status": "Assigned",
            "label": "assignment",
            "audit_action": "Owner assigned",
            "tone": "info",
        },
        "actions": {
            "status": "inprogress",
            "display_status": "In Progress",
            "label": "action",
            "audit_action": "Action created",
            "tone": "warning",
        },
        "field-report": {
            "status": "executed",
            "display_status": "Executed",
            "label": "field report",
            "audit_action": "Field report submitted",
            "tone": "success",
        },
        "outcome": {
            "status": "closed",
            "display_status": "Closed",
            "label": "outcome review",
            "audit_action": "Outcome reviewed",
            "tone": "success",
        },
        "escalate": {
            "status": "escalated",
            "display_status": "Escalated",
            "label": "escalation",
            "audit_action": "Issue escalated",
            "tone": "danger",
        },
    }
    if action_type not in actions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported issue action")
    return actions[action_type]


def _normalize_approval_decision(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    aliases = {"approve": "approved", "return": "returned", "reject": "rejected"}
    decision = aliases.get(normalized, normalized)
    if decision not in {"approved", "returned", "rejected"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported approval decision")
    return decision


def _actor(actor_name: str | None, actor_role_id: str | None) -> str:
    return actor_name or actor_role_id or "Operator"


def _now() -> datetime:
    return datetime.now(UTC)


def _time_label(value: datetime) -> str:
    return value.strftime("%H:%M")


def _display_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def _initial_operator_state() -> dict[str, Any]:
    return {
        "version": "ODP-FLOW-010",
        "kpis": [
            {"label": "高風險未指派", "value": "1", "note": "下一步：完成 Triage 與指派", "tone": "danger"},
            {"label": "待核准", "value": "5", "note": "SiteScore、Growth、退款與證據匯出", "tone": "warning"},
            {"label": "高風險門市", "value": "7", "note": "3 payment / 2 hygiene", "tone": "accent"},
            {"label": "今日待處理", "value": "18", "note": "72% owned", "tone": "info"},
            {"label": "AI 建議", "value": "12", "note": "8 high confidence", "tone": "success"},
            {"label": "觀察中", "value": "6", "note": "M3/M6 watch", "tone": "neutral"},
        ],
        "workQueue": [
            {
                "id": "ISS-1024",
                "title": "付款機前卡住＋付款失敗＋Google 負評",
                "description": "大安復興店 12 分鐘內連續 18 筆失敗，收銀機 A3 需 triage。",
                "meta": "Payment + Google review + ForecastOps 四燈號",
                "owner": "未指派",
                "status": "New",
                "time": "09:42",
                "tone": "danger",
                "workspace": "store",
                "store": "Oday 信義松仁店",
                "signals": ["支付異常", "評價", "客服", "影像", "IoT"],
                "due": "3h 12m",
                "cta": "完成 Triage",
            },
            {
                "id": "ISS-1021",
                "title": "Kiosk offline 影響午尖峰",
                "description": "板橋中山店設備離線 24 分鐘，工務主任可直接指派現場處理。",
                "meta": "IoT device state + CS cases",
                "owner": "工務",
                "status": "Assigned",
                "time": "09:20",
                "tone": "warning",
                "workspace": "store",
                "store": "皇羽自助洗衣 新莊店",
                "signals": ["設備異常", "IoT", "支付"],
                "due": "已逾期 1h 24m",
                "cta": "建立處置",
            },
            {
                "id": "GRW-201",
                "title": "夜間會員回流活動建議",
                "description": "忠孝商圈夜間需求未滿足，建議 20:00-23:00 定向券。",
                "meta": "Segment fit 84 / conflict clear",
                "owner": "行銷",
                "status": "Draft",
                "time": "08:55",
                "tone": "success",
                "workspace": "growth",
                "store": "忠孝敦化店",
                "signals": ["成長", "分群", "PriceOps"],
                "due": "今日內",
                "cta": "建立草稿",
            },
            {
                "id": "APR-501",
                "title": "CS-1002 SiteScore WAIT",
                "description": "候選點信心 76，需要營運主管判定是否進入複審。",
                "meta": "Model SiteScore v2.3 / snapshot FS-20260703-0600",
                "owner": "展店",
                "status": "Review",
                "time": "08:30",
                "tone": "info",
                "workspace": "govern",
                "store": "板橋府中候選點",
                "signals": ["SiteScore", "租金", "競品"],
                "due": "2h SLA",
                "cta": "進行核准",
            },
            {
                "id": "RV-701",
                "title": "物件看板照片缺漏",
                "description": "Listing Radar 已完成去重，仍缺路口可視性佐證。",
                "meta": "Source compliance checked",
                "owner": "展店",
                "status": "Need data",
                "time": "08:18",
                "tone": "warning",
                "workspace": "network",
                "store": "新北板橋文化",
                "signals": ["Listing", "Evidence"],
                "due": "今日 17:00 前",
                "cta": "補強證據",
            },
            {
                "id": "NET-305",
                "title": "低效門市重配建議",
                "description": "西門小南門店進入 AVM request，NetPlan 三方案待比較。",
                "meta": "Rent pressure + cannibalization risk",
                "owner": "PM",
                "status": "Observe",
                "time": "07:54",
                "tone": "accent",
                "workspace": "network",
                "store": "西門小南門店",
                "signals": ["AVM", "NetPlan", "租金"],
                "due": "M6 watch",
                "cta": "比較方案",
            },
        ],
        "decisions": [
            {
                "id": "APR-501",
                "title": "SiteScore 複審",
                "meta": "CS-1002 WAIT 76，租金合理但競品密度偏高。",
                "status": "2h SLA",
                "cta": "Open Govern",
                "tone": "warning",
                "tag": "核准",
                "time": "7/8 前",
            },
            {
                "id": "ap-store-1042",
                "title": "Close escalated service issue",
                "meta": "負評涉及付款失敗，客服主管已補充草稿。",
                "status": "Needs reason",
                "cta": "Review",
                "tone": "danger",
                "tag": "核准",
                "time": "今日 17:00 前",
            },
            {
                "id": "ap-growth-2207",
                "title": "Schedule promo campaign",
                "meta": "模型建議 8%，需確認毛利保護線。",
                "status": "Policy",
                "cta": "Compare",
                "tone": "info",
                "tag": "核准",
                "time": "2h 10m",
            },
        ],
        "riskRows": [
            {"name": "Oday 信義松仁店", "label": "Oday 信義松仁店", "note": "支付異常處理中（ISS-1024）", "signal": "Payment failure + queue spike", "score": 92, "tone": "danger"},
            {"name": "皇羽自助洗衣 新莊店", "label": "皇羽自助洗衣 新莊店", "note": "低回訪＋Kiosk 工單處理中", "signal": "Kiosk offline + CS wait", "score": 78, "tone": "warning"},
            {"name": "忠孝敦化店", "label": "忠孝敦化店", "note": "夜間需求缺口", "signal": "Demand gap with staff buffer", "score": 64, "tone": "accent"},
            {"name": "台北車站店", "label": "台北車站店", "note": "遠端重啟後恢復", "signal": "Recovered after remote restart", "score": 38, "tone": "success"},
        ],
        "auditFeed": [
            {
                "actor": "system / ForecastOps",
                "category": "Model snapshot",
                "detail": "Updated four-light evidence for ISS-1024 with payment confidence 0.91.",
                "time": "09:46",
            },
            {
                "actor": "客服主管",
                "category": "Decision log",
                "detail": "Returned APR-487 reply draft for clearer compensation reason.",
                "time": "09:33",
            },
            {
                "actor": "展店經理",
                "category": "Network review",
                "detail": "Marked RV-701 as pending street-front visibility evidence.",
                "time": "09:12",
            },
            {
                "actor": "PM／稽核",
                "category": "Audit trail",
                "detail": "Exported approval packet for CS-1002 SiteScore comparison.",
                "time": "08:41",
            },
        ],
        "notifications": [
            {"title": "SLA 即將到期", "detail": "ISS-1024 需在 58 分鐘內完成 Triage。", "tone": "danger"},
            {"title": "核准中心新增", "detail": "SiteScore APR-501 已送出複審。", "tone": "warning"},
            {"title": "模型快照更新", "detail": "ForecastOps v2.6 完成 06:00 refresh。", "tone": "info"},
        ],
        "tasks": [
            {
                "id": "TSK-3000",
                "targetId": "ISS-1024",
                "title": "Complete triage and assign owner for ISS-1024",
                "owner": "營運主管",
                "status": "open",
                "createdAt": "2026-07-05T08:20:00+00:00",
            }
        ],
        "issues": [
            {
                "id": "ISS-1024",
                "title": "晚間負評與清潔分數同步惡化",
                "storeId": "ST-008",
                "storeName": "台北信義 A11",
                "status": "new",
                "severity": "critical",
                "source": "multiSignal",
                "ownerRoleId": "opsLead",
                "ownerName": "營運主管",
                "slaDueAt": "2026-07-05T11:00:00.000Z",
                "createdAt": "2026-07-05T06:24:00.000Z",
                "updatedAt": "2026-07-05T06:24:00.000Z",
                "evidenceIds": ["EV-1024-GR", "EV-1024-CS", "EV-1024-CAM", "EV-1024-IOT", "EV-1024-PAY", "EV-1024-FOUR", "EV-1024-CLN"],
                "summary": "Google one-star reviews, CS complaints, and cleaning audit all point to a peak-hour service quality incident.",
            },
            {
                "id": "ISS-1021",
                "title": "冷氣遠端重啟等待核准",
                "storeId": "ST-014",
                "storeName": "台北大安復興",
                "status": "waitingapproval",
                "severity": "high",
                "source": "iot",
                "ownerRoleId": "facilitiesLead",
                "ownerName": "工務主任",
                "slaDueAt": "2026-07-05T10:30:00.000Z",
                "createdAt": "2026-07-05T04:50:00.000Z",
                "updatedAt": "2026-07-05T07:30:00.000Z",
                "evidenceIds": ["EV-1021-IOT", "EV-1021-PAY"],
                "relatedApprovalId": "APR-502",
                "summary": "HVAC telemetry shows repeated compressor fault codes; remote restart needs manager approval before peak traffic.",
            },
            {
                "id": "ISS-1008",
                "title": "補班日人力不足觀察中",
                "storeId": "ST-021",
                "storeName": "新北板橋文化",
                "status": "observing",
                "severity": "medium",
                "source": "forecastOps",
                "ownerRoleId": "supportLead",
                "ownerName": "客服主管",
                "slaDueAt": "2026-07-06T03:00:00.000Z",
                "createdAt": "2026-07-04T02:10:00.000Z",
                "updatedAt": "2026-07-05T01:15:00.000Z",
                "evidenceIds": ["EV-1008-FOUR", "EV-1008-CS"],
                "summary": "ForecastOps staffing light remains red after shift swap; CS queue is improving but still above baseline.",
            },
        ],
        "approvals": [
            {
                "id": "ap-store-1042",
                "module": "Store Ops",
                "title": "Close escalated service issue",
                "requestor": "Store Ops Lead",
                "submittedAt": "2026-07-05 08:12",
                "status": "pending",
                "priority": "high",
                "owner": "營運主管",
                "sla": "42m",
                "entityRef": "ISS-1042",
                "summary": "Manager requests closure after staff resolution and customer callback.",
                "systemRecommendation": "Approve with customer follow-up audit retained.",
                "risk": "Customer-facing escalation",
                "roleNote": "營運主管 can decide after reviewing evidence package.",
                "evidence": [
                    {"id": "ev-issue", "label": "Issue timeline", "type": "issue", "state": "ready"},
                    {"id": "ev-call", "label": "Customer callback", "type": "note", "state": "ready"},
                    {"id": "ev-photo", "label": "Counter photo", "type": "camera", "state": "ready"},
                ],
            },
            {
                "id": "ap-growth-2207",
                "module": "Growth",
                "title": "Schedule promo campaign",
                "requestor": "Growth Manager",
                "submittedAt": "2026-07-05 07:48",
                "status": "pending",
                "priority": "medium",
                "owner": "行銷經理",
                "sla": "2h 10m",
                "entityRef": "CMP-2207",
                "summary": "Campaign needs final governance approval before audience export.",
                "systemRecommendation": "Return unless audience mask proof is attached.",
                "risk": "Export and consent policy",
                "roleNote": "Return requires a reason for downstream Growth revision.",
                "evidence": [
                    {"id": "ev-draft", "label": "Campaign draft", "type": "growth", "state": "ready"},
                    {"id": "ev-mask", "label": "Masking proof", "type": "export", "state": "missing"},
                ],
            },
            {
                "id": "APR-501",
                "module": "Network",
                "title": "Approve SiteScore override",
                "requestor": "Expansion Manager",
                "submittedAt": "2026-07-05 06:35",
                "status": "pending",
                "priority": "critical",
                "owner": "展店經理",
                "sla": "18m",
                "entityRef": "CS-1002",
                "summary": "Team requests WAIT to GO override for a high-traffic corner candidate.",
                "systemRecommendation": "Reject override due to competitor density and lease risk.",
                "risk": "Model override",
                "roleNote": "展店經理 decision must include model and dataset snapshot context.",
                "evidence": [
                    {"id": "ev-score", "label": "SiteScore v4.8", "type": "model", "state": "ready"},
                    {"id": "ev-snapshot", "label": "Dataset 2026-W27", "type": "dataset", "state": "ready"},
                    {"id": "ev-comp", "label": "Competitor scan", "type": "network", "state": "ready"},
                ],
            },
            {
                "id": "RV-701",
                "module": "Network",
                "title": "物件看板照片缺漏",
                "requestor": "Expansion Manager",
                "submittedAt": "2026-07-05 08:18",
                "status": "pending",
                "priority": "high",
                "owner": "展店經理",
                "sla": "今日 17:00 前",
                "entityRef": "RV-701",
                "summary": "Listing Radar 已完成去重，仍缺路口可視性佐證。",
                "systemRecommendation": "Return until street-front visibility evidence is attached.",
                "risk": "Missing site visibility evidence",
                "roleNote": "展店經理 must retain review reason and evidence gap in audit trail.",
                "evidence": [
                    {"id": "ev-listing", "label": "Listing Radar duplicate check", "type": "network", "state": "ready"},
                    {"id": "ev-photo-gap", "label": "Street-front visibility photo", "type": "camera", "state": "missing"},
                ],
            },
        ],
        "governanceDecisions": [
            {
                "id": "dec-8841",
                "module": "Store Ops",
                "item": "ISS-0994 resolution close",
                "systemRecommendation": "Approve",
                "finalDecision": "Approved",
                "reason": "Evidence package matched closure policy.",
                "actor": "營運主管",
                "decidedAt": "2026-07-05 04:51",
                "model": "ops-risk-v2.2",
                "datasetSnapshot": "ops-2026-W27",
                "approvalId": "ap-store-0994",
            }
        ],
        "governanceAuditRows": [
            {
                "id": "aud-7101",
                "category": "approval",
                "timestamp": "2026-07-05 08:12",
                "actor": "Store Ops Lead",
                "action": "Approval requested",
                "module": "Store Ops",
                "entityRef": "ISS-1042",
                "summary": "Issue closure approval entered queue.",
                "correlationId": "corr-iss-1042",
            },
            {
                "id": "aud-7100",
                "category": "camera",
                "timestamp": "2026-07-05 08:08",
                "actor": "Camera service",
                "action": "Evidence attached",
                "module": "Store Ops",
                "entityRef": "ISS-1042",
                "summary": "Counter photo linked to closure packet.",
                "correlationId": "corr-iss-1042",
            },
        ],
    }


__all__ = ["OperatorStateStore", "create_operator_router"]
