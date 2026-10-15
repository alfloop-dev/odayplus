"""R7 Today cards derived from issue facts, never from unrelated shell counts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from modules.opsboard.application.store_ops import _ISSUE_STATUSES

TODAY_CARD_LABELS = (
    "高風險未指派",
    "已逾期 Issue",
    "即將逾期",
    "成效待判斷",
    "待我核准",
    "需升級門市",
)

ROLE_CARD_LABELS = {
    "ops-lead": TODAY_CARD_LABELS,
    "cs-lead": ("AI 未解決案件", "Google 負評未回覆", "退款／付款案件", "回覆 SLA 即將逾期", "需轉交工務"),
    "field-lead": ("指派給我的工單", "設備離線", "遠端重啟失敗", "付款機異常", "巡檢待回報"),
    "marketing-manager": ("離峰機會", "會員召回候選", "促銷草稿", "待核准活動", "成效待判斷"),
    "expansion-manager": ("新外部物件", "追蹤中物件", "缺資料候選", "待評分／待送審", "待審 Review", "重配候選門市"),
    "pm-audit": ("待核准項", "Audit 待補", "今日決策數", "隱私敏感操作"),
}
ROLE_CARD_LABELS["expansion-staff"] = ROLE_CARD_LABELS["expansion-manager"]


def build_today_metrics(
    *,
    issues: list[dict[str, Any]] | None,
    approvals_count: int | None,
    now: datetime,
    role_id: str = "ops-lead",
) -> list[dict[str, Any]]:
    """An unavailable fact stays unavailable, including missing SLA day policy.

    The prototype's due-soon card means due within the business day. The live
    issue contract supplies an absolute deadline but no approved business-day
    timezone/calendar. Do not invent a two-hour threshold or a UTC day policy.
    """
    if role_id != "ops-lead":
        # These personas need their own domain projections. Never put the
        # operations card counts (or prototype example constants) under them.
        return [
            {
                "label": label,
                "value": str(approvals_count) if role_id == "pm-audit" and index == 0 and approvals_count is not None else None,
                "delta": "",
                "meta": "含 SiteScore 審核" if role_id == "pm-audit" and index == 0 and approvals_count is not None else "資料尚未備妥",
                "tone": "info" if role_id == "pm-audit" and index == 0 else "neutral",
                "availability": "available" if role_id == "pm-audit" and index == 0 and approvals_count is not None else "unavailable",
            }
            for index, label in enumerate(ROLE_CARD_LABELS.get(role_id, ()))
        ]
    active = (
        [issue for issue in issues if issue.get("status") != "closed"]
        if issues is not None
        and all(isinstance(issue.get("status"), str) and issue["status"] in _ISSUE_STATUSES for issue in issues)
        else None
    )
    high_unassigned: int | None = None
    overdue: int | None = None
    outcome: int | None = None
    escalation_needed: int | None = None
    if active is not None:
        outcome = sum(issue["status"] == "outcomeready" for issue in active)
        if all(
            issue.get("severity") in {"low", "medium", "high", "critical"}
            and isinstance(issue.get("ownerName"), str)
            for issue in active
        ):
            high_unassigned = sum(
                issue["severity"] == "critical"
                and not issue["ownerName"].strip()
                for issue in active
            )
        escalations = [issue for issue in active if issue["status"] == "escalated"]
        if all(isinstance(issue.get("storeId"), str) and issue["storeId"].strip() for issue in escalations):
            escalation_needed = len({issue["storeId"] for issue in escalations})
        deadlines: list[datetime] = []
        for issue in active:
            try:
                deadline = datetime.fromisoformat(str(issue.get("slaDueAt", "")).replace("Z", "+00:00"))
            except ValueError:
                break
            if deadline.tzinfo is None or deadline.utcoffset() is None:
                break
            deadlines.append(deadline)
        else:
            overdue = sum(deadline < now.astimezone(UTC) for deadline in deadlines)

    # Use committed escalation facts, without inferring them from red lights
    # or introducing a new escalation threshold into the read projection.
    values = (high_unassigned, overdue, 0 if active == [] else None, outcome, approvals_count, escalation_needed)
    descriptions = (
        "下一步：完成 Triage 與指派",
        "優先處理 SLA 逾期",
        "今日期限內需完成",
        "請完成 Outcome Review",
        "前往核准中心",
        "連續紅燈 · 店網重估",
    )
    tones = ("danger", "danger", "warning", "info", "info", "neutral")
    return [
        {
            "label": label,
            "value": str(value) if value is not None else None,
            "delta": "",
            "meta": description if value is not None else "資料尚未備妥",
            "tone": tone,
            "availability": "available" if value is not None else "unavailable",
        }
        for label, value, description, tone in zip(TODAY_CARD_LABELS, values, descriptions, tones, strict=True)
    ]
