from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from shared.audit import InMemoryAuditLog


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

    return router

__all__ = ["create_operator_router"]
