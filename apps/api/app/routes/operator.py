from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from shared.audit import AuditEvent, InMemoryAuditLog


class TransitionPayload(BaseModel):
    issueId: str | None = None
    status: str | None = None
    note: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None

class ApprovalDecisionPayload(BaseModel):
    status: str
    reason: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None

# ---------------------------------------------------------------------------
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


class EvidencePurposePayload(BaseModel):
    purpose: str
    cameraLocation: str | None = None
    timeWindow: str | None = None
    retentionHours: int | None = None
    privacyAcknowledged: bool | None = None
    auditNote: str | None = None

def create_operator_router(
    *,
    audit_log: InMemoryAuditLog | None = None,
) -> APIRouter:
    from apps.api.oday_api.security.dependencies import build_engine, require_permission
    from shared.auth import Action

    active_audit_log = audit_log or InMemoryAuditLog()
    authz_engine = build_engine(audit_log=active_audit_log)
    idempotency_cache: dict[str, Any] = {}

    router = APIRouter(prefix="/operator", tags=["operator"])

    # In-memory operator database state
    state = {
        "kpis": [
            {"label": "Critical SLA", "value": "9", "delta": "+3 since 09:00", "meta": "4 due in 2h", "tone": "danger"},
            {"label": "待核准", "value": "5", "delta": "2 SiteScore", "meta": "1 returned", "tone": "warning"},
            {"label": "高風險門市", "value": "7", "delta": "3 payment", "meta": "2 hygiene", "tone": "accent"},
            {"label": "今日待處理", "value": "18", "delta": "-6 vs yesterday", "meta": "72% owned", "tone": "info"},
            {"label": "AI 建議", "value": "12", "delta": "8 high confidence", "meta": "v2.6", "tone": "success"},
            {"label": "觀察中", "value": "6", "delta": "3 outcome-ready", "meta": "M3/M6 watch", "tone": "neutral"},
        ],
        "workQueue": [
            {
                "id": "ISS-1024",
                "title": "支付失敗率異常升高",
                "description": "大安復興店 12 分鐘內連續 18 筆失敗，收銀機 A3 需 triage。",
                "meta": "Payment + Google review + ForecastOps 四燈號",
                "owner": "營運",
                "status": "SLA 1h",
                "time": "09:42",
                "tone": "danger",
                "workspace": "store",
            },
            {
                "id": "ISS-1021",
                "title": "Kiosk offline 影響午尖峰",
                "description": "板橋中山店設備離線 24 分鐘，工務主任可直接指派現場處理。",
                "meta": "IoT device state + CS cases",
                "owner": "工務",
                "status": "New",
                "time": "09:20",
                "tone": "warning",
                "workspace": "store",
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
            },
            {
                "id": "APR-487",
                "title": "Google review 回覆",
                "meta": "負評涉及付款失敗，客服主管已補充草稿。",
                "status": "Needs reason",
                "cta": "Review",
                "tone": "danger",
            },
            {
                "id": "GRW-207",
                "title": "PriceOps 折扣上限",
                "meta": "模型建議 8%，需確認毛利保護線。",
                "status": "Policy",
                "cta": "Compare",
                "tone": "info",
            },
        ],
        "riskRows": [
            {"label": "大安復興店", "score": 92, "signal": "Payment failure + queue spike", "tone": "danger"},
            {"label": "板橋中山店", "score": 78, "signal": "Kiosk offline + CS wait", "tone": "warning"},
            {"label": "忠孝敦化店", "score": 64, "signal": "Demand gap with staff buffer", "tone": "accent"},
            {"label": "台北車站店", "score": 38, "signal": "Recovered after remote restart", "tone": "success"},
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
    }

    @router.get("/bootstrap")
    def bootstrap() -> dict[str, Any]:
        return state

    @router.get("/today")
    def get_today() -> dict[str, Any]:
        return state

    @router.get("/issues")
    def get_issues() -> dict[str, Any]:
        return {"items": state["workQueue"], "count": len(state["workQueue"])}

    @router.get("/approvals")
    def get_approvals() -> dict[str, Any]:
        return {"items": state["decisions"], "count": len(state["decisions"])}

    # Workflow write transitions
    @router.post(
        "/issues/{issue_id}/{action_type}",
        dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))]
    )
    def transition_issue(
        issue_id: str,
        action_type: str,
        body: TransitionPayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        import copy
        if idempotency_key and idempotency_key in idempotency_cache:
            return idempotency_cache[idempotency_key]

        # Perform state update or record audit
        # Find issue in workQueue and update status
        for issue in state["workQueue"]:
            if issue["id"] == issue_id:
                # E.g. triage maps to "triaged"
                new_status = "Closed"
                if action_type == "triage":
                    new_status = "Triaged"
                elif action_type == "assign":
                    new_status = "Assigned"
                elif action_type == "actions":
                    new_status = "In Progress"
                elif action_type == "outcome":
                    new_status = "Closed"
                issue["status"] = new_status
                break
        
        # Add to audit feed
        state["auditFeed"].insert(0, {
            "actor": body.actorName or "System",
            "category": "Workflow",
            "detail": f"Issue {issue_id} transition via {action_type}.",
            "time": datetime.now(UTC).strftime("%H:%M")
        })

        res_state = copy.deepcopy(state)
        if idempotency_key:
            idempotency_cache[idempotency_key] = res_state
        return res_state

    @router.post(
        "/approvals/{approval_id}/decision",
        dependencies=[Depends(require_permission("intervention", Action.APPROVE, engine=authz_engine))]
    )
    def decide_approval(
        approval_id: str,
        body: ApprovalDecisionPayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        import copy
        if idempotency_key and idempotency_key in idempotency_cache:
            return idempotency_cache[idempotency_key]

        # Update approval status in decisions list
        for dec in state["decisions"]:
            if dec["id"] == approval_id:
                dec["status"] = body.status
        
        state["auditFeed"].insert(0, {
            "actor": body.actorName or "System",
            "category": "Decision log",
            "detail": f"Approval {approval_id} decided: {body.status}. Reason: {body.reason or ''}",
            "time": datetime.now(UTC).strftime("%H:%M")
        })

        res_state = copy.deepcopy(state)
        if idempotency_key:
            idempotency_cache[idempotency_key] = res_state
        return res_state

    @router.post(
        "/evidence/{evidence_id}/purpose",
        dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))]
    )
    def confirm_evidence_purpose(
        evidence_id: str,
        body: EvidencePurposePayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        import copy
        if idempotency_key and idempotency_key in idempotency_cache:
            return idempotency_cache[idempotency_key]

        state["auditFeed"].insert(0, {
            "actor": "Operator",
            "category": "Audit trail",
            "detail": f"Unlocked evidence {evidence_id} with purpose: {body.purpose}",
            "time": datetime.now(UTC).strftime("%H:%M")
        })

        res_state = copy.deepcopy(state)
        if idempotency_key:
            idempotency_cache[idempotency_key] = res_state
        return res_state

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
            "constraintDetail": "硬限制通過；競品價差在政策範圍內。",
            "confidence": "medium",
            "decisionStatus": "SYSTEM_RECOMMENDED",
        },
        {
            "id": "rec-9002",
            "segmentId": "seg-latenight-delivery",
            "title": "宵夜外送費 -2% 試點",
            "currentPrice": "現行外送費 NT$ 39",
            "candidatePrice": "候選外送費 NT$ 38",
            "expectedRevenueLift": 0.8,
            "expectedMarginLift": 1.0,
            "constraintStatus": "SOFT_WARNING",
            "constraintDetail": "軟警告：可比樣本偏少，建議搭配對照組。",
            "confidence": "low",
            "decisionStatus": "SYSTEM_RECOMMENDED",
        },
        {
            "id": "rec-9003",
            "segmentId": "seg-suburb-lunch",
            "title": "午餐主力商品 +9% 調價",
            "currentPrice": "現行 NT$ 129 / 149",
            "candidatePrice": "候選 NT$ 141 / 163",
            "expectedRevenueLift": 1.1,
            "expectedMarginLift": 1.4,
            "constraintStatus": "HARD_CONSTRAINT_FAILED",
            "constraintDetail": "HARD_CONSTRAINT_FAILED：max_delta_pct 6%、競品價差超出政策上限。",
            "confidence": "low",
            "decisionStatus": "SYSTEM_RECOMMENDED",
        },
    ]

    growth_actions: list[dict[str, Any]] = [
        {
            "id": "growth-7001",
            "name": "都會晚餐套餐調價活動",
            "segmentId": "seg-metro-dinner",
            "sourceRecommendationId": "rec-9001",
            "objective": "晚餐時段營收 P50 +2.0%，毛利不低於現況。",
            "status": "OUTCOME_READY",
            "observationWindow": "2026-06-20 ~ 2026-07-04（14 天）",
            "targetLift": 2.0,
            "observedLift": 2.6,
            "evidenceLevel": "high",
            "rationale": "對照組配對通過 pre-trend 檢定；調價後晚餐營收顯著高於基準。",
            "rollbackPlan": "回復價目表 pb-2026.06.19，30 分鐘內生效，觀察 48 小時。",
            "audit": {
                "decisionId": "dec-growth-7001",
                "correlationId": "corr-growth-7001",
                "modelVersion": "growth-uplift-v1.4.0",
                "policyVersion": "growth-policy-2026.07",
                "featureSnapshotTime": "2026-07-04T06:00:00Z",
            },
        },
        {
            "id": "growth-7002",
            "name": "宵夜外送費試點活動",
            "segmentId": "seg-latenight-delivery",
            "sourceRecommendationId": "rec-9002",
            "objective": "宵夜外送訂單量 P50 +3.0%，營收不低於現況。",
            "status": "OUTCOME_READY",
            "observationWindow": "2026-06-18 ~ 2026-07-02（14 天）",
            "targetLift": 3.0,
            "observedLift": -1.4,
            "evidenceLevel": "medium",
            "rationale": "下調外送費後訂單量未提升，宵夜營收較基準下滑。",
            "rollbackPlan": "回復外送費結構 fs-2026.06.17，先 canary 12 小時再全量。",
            "audit": {
                "decisionId": "dec-growth-7002",
                "correlationId": "corr-growth-7002",
                "modelVersion": "growth-uplift-v1.4.0",
                "policyVersion": "growth-policy-2026.07",
                "featureSnapshotTime": "2026-07-02T06:00:00Z",
            },
        },
        {
            "id": "growth-7003",
            "name": "郊區午餐加價包觀察活動",
            "segmentId": "seg-suburb-lunch",
            "objective": "午餐加價包滲透率 P50 +1.5%，需求彈性可控。",
            "status": "OUTCOME_READY",
            "observationWindow": "2026-06-25 ~ 2026-07-09（14 天）",
            "targetLift": 1.5,
            "observedLift": 0.6,
            "evidenceLevel": "low",
            "rationale": "觀察期營收微幅上升但未達標，對照組樣本不足以判定因果。",
            "rollbackPlan": "回復加價包設定 cfg-2026.06.24；維持既有午餐主力價。",
            "audit": {
                "decisionId": "dec-growth-7003",
                "correlationId": "corr-growth-7003",
                "modelVersion": "growth-uplift-v1.4.0",
                "policyVersion": "growth-policy-2026.07",
                "featureSnapshotTime": "2026-07-09T06:00:00Z",
            },
        },
        {
            "id": "growth-7004",
            "name": "都會晚餐加點推薦活動",
            "segmentId": "seg-metro-dinner",
            "objective": "晚餐加點附加營收 P50 +2.5%。",
            "status": "OBSERVING",
            "observationWindow": "2026-07-05 ~ 2026-07-19（觀察中）",
            "targetLift": 2.5,
            "observedLift": None,
            "evidenceLevel": "medium",
            "rationale": "活動執行中，觀察窗尚未成熟，暫無成效判定。",
            "rollbackPlan": "停用加點推薦模組設定 cfg-2026.07.05。",
            "audit": {
                "decisionId": "dec-growth-7004",
                "correlationId": "corr-growth-7004",
                "modelVersion": "growth-uplift-v1.4.0",
                "policyVersion": "growth-policy-2026.07",
                "featureSnapshotTime": "2026-07-09T06:00:00Z",
            },
        },
        {
            "id": "growth-7005",
            "name": "宵夜外送廣告增量草稿",
            "segmentId": "seg-latenight-delivery",
            "objective": "宵夜外送營收 P50 +2.0%，iROMI 為正。",
            "status": "DRAFT",
            "observationWindow": "尚未排程",
            "targetLift": 2.0,
            "observedLift": None,
            "evidenceLevel": "low",
            "rationale": "草稿：待補齊對照組與 pre-trend 檢定後送審。",
            "rollbackPlan": "草稿階段無執行，無需 rollback。",
            "audit": {
                "decisionId": "draft-growth-7005",
                "correlationId": "corr-growth-7005",
                "modelVersion": "growth-uplift-v1.4.0",
                "policyVersion": "growth-policy-2026.07",
                "featureSnapshotTime": "2026-07-09T06:00:00Z",
            },
        },
    ]

    growth_freshness: dict[str, Any] = {
        "status": "FRESH",
        "updatedAt": "2026-07-09 14:20",
        "modelVersion": "growth-uplift-v1.4.0",
        "policyVersion": "growth-policy-2026.07",
        "featureSnapshotTime": "2026-07-09T06:00:00Z",
        "sourceSnapshotId": "snap-growth-20260709-0600",
    }

    # Idempotency cache for growth writes
    growth_idempotency: dict[str, Any] = {}

    @router.get("/growth/freshness", tags=["growth"])
    def get_growth_freshness() -> dict[str, Any]:
        """Return freshness / model-version metadata for the Growth workspace."""
        return growth_freshness

    @router.get("/growth/segments", tags=["growth"])
    def list_growth_segments() -> dict[str, Any]:
        """List all growth segments."""
        return {"items": growth_segments, "count": len(growth_segments)}

    @router.get("/growth/recommendations", tags=["growth"])
    def list_growth_recommendations(
        segment_id: str | None = None,
    ) -> dict[str, Any]:
        """List PriceOps recommendations, optionally filtered by segment."""
        items = (
            [r for r in growth_recommendations if r["segmentId"] == segment_id]
            if segment_id
            else growth_recommendations
        )
        return {"items": items, "count": len(items)}

    @router.get("/growth/actions", tags=["growth"])
    def list_growth_actions(
        segment_id: str | None = None,
    ) -> dict[str, Any]:
        """List growth actions, optionally filtered by segment."""
        items = (
            [a for a in growth_actions if a["segmentId"] == segment_id]
            if segment_id
            else growth_actions
        )
        return {"items": items, "count": len(items)}

    @router.post(
        "/growth/actions",
        status_code=status.HTTP_201_CREATED,
        tags=["growth"],
        dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))],
    )
    def create_growth_draft(
        body: GrowthDraftPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Create a Growth Action draft (DRAFT status). Idempotent on Idempotency-Key."""
        effective_key = body.idempotency_key or idempotency_key
        if effective_key and effective_key in growth_idempotency:
            return growth_idempotency[effective_key]

        correlation_id = x_correlation_id or getattr(request.state, "correlation_id", None) or ""
        action_id = f"growth-{uuid.uuid4().hex[:8]}"
        obs_window = f"尚未排程（{body.observationWindowDays} 天）"
        new_action: dict[str, Any] = {
            "id": action_id,
            "name": body.name,
            "segmentId": body.segmentId,
            "sourceRecommendationId": body.sourceRecommendationId,
            "objective": body.objective,
            "status": "DRAFT",
            "observationWindow": obs_window,
            "targetLift": body.targetLift,
            "observedLift": None,
            "evidenceLevel": "low",
            "rationale": body.rationale,
            "rollbackPlan": body.rollbackPlan,
            "audit": {
                "decisionId": f"draft-{action_id}",
                "correlationId": correlation_id,
                "modelVersion": growth_freshness["modelVersion"],
                "policyVersion": growth_freshness["policyVersion"],
                "featureSnapshotTime": growth_freshness["featureSnapshotTime"],
            },
        }
        growth_actions.append(new_action)

        active_audit_log.record(
            AuditEvent(
                event_type="growth.draft_created.v1",
                actor=body.actorName or "system",
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

        result = {**new_action, "created": True, "correlation_id": correlation_id}
        if effective_key:
            growth_idempotency[effective_key] = result
        return result

    @router.post(
        "/growth/actions/{action_id}/outcome",
        tags=["growth"],
        dependencies=[Depends(require_permission("intervention", Action.APPROVE, engine=authz_engine))],
    )
    def write_growth_outcome(
        action_id: str,
        body: GrowthOutcomePayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Record effectiveness verdict and required action for a Growth Action.

        Idempotent on Idempotency-Key. An INEFFECTIVE outcome does not close
        the action — the required action is recorded and the audit trail updated.
        """
        outcome_key = f"outcome-{action_id}-{idempotency_key or ''}"
        if idempotency_key and outcome_key in growth_idempotency:
            return growth_idempotency[outcome_key]

        action = next((a for a in growth_actions if a["id"] == action_id), None)
        if action is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"growth action {action_id!r} not found",
            )

        correlation_id = x_correlation_id or getattr(request.state, "correlation_id", None) or ""

        if body.observedLift is not None:
            action["observedLift"] = body.observedLift
        if body.evidenceLevel:
            action["evidenceLevel"] = body.evidenceLevel
        if body.rationale:
            action["rationale"] = body.rationale

        # Only close if EFFECTIVE; otherwise record required action but keep status
        if body.outcome == "EFFECTIVE" and body.requiredAction == "CLOSE":
            action["status"] = "CLOSED"
        elif body.outcome == "INEFFECTIVE":
            action["status"] = "OUTCOME_READY"  # blocked, awaiting rollback
        else:
            action["status"] = "OUTCOME_READY"

        active_audit_log.record(
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

__all__ = ["create_operator_router"]
