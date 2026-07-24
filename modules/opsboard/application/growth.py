"""Growth workspace application service for the Operator Console.

Manages three entry-point paths for creating a Growth Action draft:
  1. Via the "建立草稿" button on a PriceOps recommendation row
     → POST /operator/growth/actions  (with source_recommendation_id)
  2. Via the PriceOps recommendations entry point (same endpoint, payload driven)
  3. Via the direct "new action" entry point (same endpoint, no recommendation seed)

Five-step Draft Builder contract (browser side):
  step 1 — name + objective
  step 2 — segment selection
  step 3 — targetLift + observationWindowDays
  step 4 — rationale + rollbackPlan
  step 5 — review + submit (calls this service)

Conflict Gate:
  HARD_CONSTRAINT_FAILED recommendations block draft creation at the API layer —
  the service raises GrowthPolicyError so the HTTP layer returns 422.

Lifecycle:
  DRAFT → APPROVED → EXECUTED → OBSERVING → OUTCOME_READY → CLOSED
  Ineffective and inconclusive actions may not transition to CLOSED directly —
  the service enforces this via GrowthCloseoutGateError (409).

Not changing: auth/RBAC engine, shared audit module, persistence adapters.
Composes with: apps/api/app/routes/operator_modules/growth.py route module.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from shared.audit.events import AuditEvent, InMemoryAuditLog

# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class GrowthError(Exception):
    """Base Growth service error."""


class GrowthNotFound(GrowthError):
    """Requested Growth resource does not exist."""


class GrowthConflict(GrowthError):
    """Lifecycle transition is invalid for the current action state."""


class GrowthPolicyError(GrowthError):
    """Policy-controlled Growth action was rejected (e.g. hard constraint)."""


class GrowthCloseoutGateError(GrowthConflict):
    """Closeout blocked: action is not EFFECTIVE and cannot be closed directly."""


# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

_ACTIVE_STATUSES = {
    "PENDING_APPROVAL",
    "APPROVED",
    "SCHEDULED",
    "RUNNING",
    "EXECUTED",
    "OBSERVING",
    "OUTCOME_READY",
}
_OUTCOME_STAGES = {"OUTCOME_READY", "CLOSED"}

# Canonical R4 Growth lifecycle (package 6 design):
#   DRAFT → PENDING_APPROVAL → APPROVED → SCHEDULED → RUNNING → OBSERVING
#         → OUTCOME_READY → CLOSED, with an INEFFECTIVE branch back to DRAFT.
# EXECUTED is retained as a backward-compatible alias for RUNNING so earlier
# callers/tests that drove DRAFT→APPROVED→EXECUTED keep working.
_LIFECYCLE_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"PENDING_APPROVAL", "APPROVED", "CANCELLED"},
    "PENDING_APPROVAL": {"APPROVED", "DRAFT", "CANCELLED"},
    "APPROVED": {"SCHEDULED", "EXECUTED", "CANCELLED"},
    "SCHEDULED": {"RUNNING", "CANCELLED"},
    "RUNNING": {"OBSERVING"},
    "EXECUTED": {"OBSERVING"},
    "OBSERVING": {"OUTCOME_READY"},
    "OUTCOME_READY": {"CLOSED", "INEFFECTIVE", "OBSERVING"},
    "INEFFECTIVE": {"DRAFT"},
    "CLOSED": set(),
    "CANCELLED": set(),
}

# The three canonical create-entry draft types (package 6 entry cards).
_DRAFT_TYPES = {"offpeak", "winback", "priceops"}

# Statuses that count as "an active campaign" for conflict detection.
_CONFLICT_ACTIVE_STATUSES = {
    "PENDING_APPROVAL",
    "APPROVED",
    "SCHEDULED",
    "RUNNING",
    "OBSERVING",
}

# Single-campaign budget ceiling; above this a second-level approval is needed.
_BUDGET_SECOND_APPROVAL_CEILING = 50000


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_audit_id(prefix: str) -> str:
    return f"AUD-{prefix}-{uuid.uuid4().hex[:8].upper()}"


def _new_action_id() -> str:
    return f"growth-{uuid.uuid4().hex[:8]}"


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


# ---------------------------------------------------------------------------
# Effectiveness gate (mirrors growthViewModel.ts logic for backend enforcement)
# ---------------------------------------------------------------------------


def _judge_effectiveness(
    status: str,
    observed_lift: float | None,
    target_lift: float,
    evidence_level: str,
) -> str:
    """Classify effectiveness: PENDING | EFFECTIVE | INEFFECTIVE | INCONCLUSIVE."""
    if status not in _OUTCOME_STAGES or observed_lift is None:
        return "PENDING"
    if observed_lift <= 0:
        return "INEFFECTIVE"
    if evidence_level == "low" or observed_lift < target_lift:
        return "INCONCLUSIVE"
    return "EFFECTIVE"


def _closeout_gate(action: dict[str, Any]) -> dict[str, Any]:
    outcome = _judge_effectiveness(
        status=action.get("status", "DRAFT"),
        observed_lift=action.get("observedLift"),
        target_lift=float(action.get("targetLift") or 0),
        evidence_level=action.get("evidenceLevel", "medium"),
    )
    if outcome == "EFFECTIVE":
        return {
            "outcome": outcome,
            "canClose": True,
            "requiredAction": "CLOSE",
            "reason": "達標且證據充足，可結案並回寫 Label Registry。",
        }
    if outcome == "INEFFECTIVE":
        return {
            "outcome": outcome,
            "canClose": False,
            "requiredAction": "ROLLBACK",
            "reason": "活動無效：必須先執行 rollback 或修正方案，無效活動不可直接結案。",
        }
    if outcome == "INCONCLUSIVE":
        return {
            "outcome": outcome,
            "canClose": False,
            "requiredAction": "STRENGTHEN_EVIDENCE",
            "reason": "未達標或證據不足，需補強對照組/延長觀察後再判定，不可直接結案。",
        }
    return {
        "outcome": outcome,
        "canClose": False,
        "requiredAction": "CONTINUE_OBSERVATION",
        "reason": "觀察窗尚未成熟，無法判定成效，不可結案。",
    }


# ---------------------------------------------------------------------------
# Seed data (mirrors growthViewModel.ts fixtures for deterministic API)
# ---------------------------------------------------------------------------

_SEED_FRESHNESS: dict[str, Any] = {
    "status": "FRESH",
    "updatedAt": "2026-07-09 14:20",
    "modelVersion": "growth-uplift-v1.4.0",
    "policyVersion": "growth-policy-2026.07",
    "featureSnapshotTime": "2026-07-09T06:00:00Z",
    "sourceSnapshotId": "snap-growth-20260709-0600",
}

_SEED_SEGMENTS: list[dict[str, Any]] = [
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

_SEED_RECOMMENDATIONS: list[dict[str, Any]] = [
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

_SEED_ACTIONS: list[dict[str, Any]] = [
    {
        "id": "growth-7001",
        "name": "都會晚餐套餐調價活動",
        "segmentId": "seg-metro-dinner",
        "sourceRecommendationId": "rec-9001",
        "objective": "晚餐時段營收 P50 +2.0%，毛利不低於現況。",
        "status": "OUTCOME_READY",
        "observationWindow": "2026-06-20 ~ 2026-07-04（14 天）",
        "observationWindowDays": 14,
        "targetLift": 2.0,
        "observedLift": 2.6,
        "evidenceLevel": "high",
        "rationale": "對照組配對通過 pre-trend 檢定；調價後晚餐營收顯著高於基準。",
        "rollbackPlan": "回復價目表 pb-2026.06.19，30 分鐘內生效，觀察 48 小時。",
        "growthOutcome": None,
        "createdAt": "2026-06-06T08:00:00Z",
        "updatedAt": "2026-07-04T06:10:00Z",
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
        "observationWindowDays": 14,
        "targetLift": 3.0,
        "observedLift": -1.4,
        "evidenceLevel": "medium",
        "rationale": "下調外送費後訂單量未提升，宵夜營收較基準下滑。",
        "rollbackPlan": "回復外送費結構 fs-2026.06.17，先 canary 12 小時再全量。",
        "growthOutcome": None,
        "createdAt": "2026-06-04T08:00:00Z",
        "updatedAt": "2026-07-02T06:10:00Z",
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
        "observationWindowDays": 14,
        "targetLift": 1.5,
        "observedLift": 0.6,
        "evidenceLevel": "low",
        "rationale": "觀察期營收微幅上升但未達標，對照組樣本不足以判定因果。",
        "rollbackPlan": "回復加價包設定 cfg-2026.06.24；維持既有午餐主力價。",
        "growthOutcome": None,
        "createdAt": "2026-06-11T08:00:00Z",
        "updatedAt": "2026-07-09T06:10:00Z",
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
        "observationWindowDays": 14,
        "targetLift": 2.5,
        "observedLift": None,
        "evidenceLevel": "medium",
        "rationale": "活動執行中，觀察窗尚未成熟，暫無成效判定。",
        "rollbackPlan": "停用加點推薦模組設定 cfg-2026.07.05。",
        "growthOutcome": None,
        "createdAt": "2026-07-05T08:00:00Z",
        "updatedAt": "2026-07-05T08:00:00Z",
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
        "observationWindowDays": 14,
        "targetLift": 2.0,
        "observedLift": None,
        "evidenceLevel": "low",
        "rationale": "草稿：待補齊對照組與 pre-trend 檢定後送審。",
        "rollbackPlan": "草稿階段無執行，無需 rollback。",
        "growthOutcome": None,
        "createdAt": "2026-07-09T10:00:00Z",
        "updatedAt": "2026-07-09T10:00:00Z",
        "audit": {
            "decisionId": "draft-growth-7005",
            "correlationId": "corr-growth-7005",
            "modelVersion": "growth-uplift-v1.4.0",
            "policyVersion": "growth-policy-2026.07",
            "featureSnapshotTime": "2026-07-09T06:00:00Z",
        },
    },
]


# Draft type (kind) and conflict-relevant fields per seed action.  These mirror
# the package 6 demo state so conflict detection and type filters have data.
_SEED_ACTION_META: dict[str, dict[str, Any]] = {
    "growth-7001": {"kind": "priceops", "store": "Oday 信義松仁店", "channel": "店內告示＋App 價格頁", "budget": 0},
    "growth-7002": {"kind": "priceops", "store": "Oday 板橋府中店", "channel": "店內告示", "budget": 0},
    "growth-7003": {"kind": "offpeak", "store": "Oday 中壢中原店", "channel": "LINE 推播", "budget": 12000},
    "growth-7004": {"kind": "offpeak", "store": "Oday 信義松仁店", "channel": "App 首頁", "budget": 8000},
    "growth-7005": {"kind": "winback", "store": "全品牌", "channel": "LINE 推播", "budget": 18000},
}


def _seed_state() -> dict[str, Any]:
    actions = _clone(_SEED_ACTIONS)
    for action in actions:
        meta = _SEED_ACTION_META.get(action["id"], {})
        action.setdefault("kind", meta.get("kind", "offpeak"))
        action.setdefault("store", meta.get("store", "全品牌"))
        action.setdefault("channel", meta.get("channel", "LINE 推播"))
        action.setdefault("budget", meta.get("budget", 0))
    return {
        "freshness": _clone(_SEED_FRESHNESS),
        "segments": _clone(_SEED_SEGMENTS),
        "recommendations": _clone(_SEED_RECOMMENDATIONS),
        "actions": actions,
        "approvals": [],
        "decisions": [],
        "auditEvents": [],
        "nextAuditOrdinal": 8001,
        "nextApprovalOrdinal": 501,
        "nextDecisionOrdinal": 9001,
        "idempotencyResults": {},
    }


def _empty_state() -> dict[str, Any]:
    return {
        "freshness": {
            "status": "UNAVAILABLE",
            "updatedAt": None,
            "modelVersion": "UNBOUND",
            "policyVersion": "UNBOUND",
            "featureSnapshotTime": None,
            "sourceSnapshotId": None,
        },
        "segments": [],
        "recommendations": [],
        "actions": [],
        "approvals": [],
        "decisions": [],
        "auditEvents": [],
        "nextAuditOrdinal": 1,
        "nextApprovalOrdinal": 1,
        "nextDecisionOrdinal": 1,
        "idempotencyResults": {},
    }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class GrowthService:
    """Application service for the Growth workspace.

    All state is held in a mutable in-memory dict that can be seeded
    deterministically for tests.  The service is designed to be shared
    as a singleton per router lifetime (same pattern as StoreOpsService).
    """

    def __init__(
        self,
        initial_state: dict[str, Any] | None = None,
        *,
        audit_log: InMemoryAuditLog | None = None,
        seed_fixtures: bool = True,
    ) -> None:
        self._seed_fixtures = seed_fixtures
        self._state = _clone(
            initial_state
            if initial_state is not None
            else _seed_state()
            if seed_fixtures
            else _empty_state()
        )
        self._audit_log = audit_log or InMemoryAuditLog()

    def export_state(self) -> dict[str, Any]:
        return _clone(self._state)

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def get_freshness(self) -> dict[str, Any]:
        return _clone(self._state["freshness"])

    def list_segments(self, *, segment_id: str | None = None) -> list[dict[str, Any]]:
        segments = self._state["segments"]
        if segment_id:
            return _clone([s for s in segments if s["id"] == segment_id])
        return _clone(segments)

    def list_recommendations(
        self,
        *,
        segment_id: str | None = None,
    ) -> list[dict[str, Any]]:
        recs = self._state["recommendations"]
        if segment_id:
            recs = [r for r in recs if r["segmentId"] == segment_id]
        return _clone(recs)

    def list_actions(
        self,
        *,
        segment_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        actions = self._state["actions"]
        if segment_id:
            actions = [a for a in actions if a["segmentId"] == segment_id]
        if status:
            actions = [a for a in actions if a["status"] == status]
        result = []
        for action in actions:
            enriched = _clone(action)
            enriched["closeoutGate"] = _closeout_gate(action)
            result.append(enriched)
        return result

    def get_action(self, action_id: str) -> dict[str, Any]:
        action = self._find_action(action_id)
        enriched = _clone(action)
        enriched["closeoutGate"] = _closeout_gate(action)
        return enriched

    def get_summary(self) -> dict[str, Any]:
        actions = self._state["actions"]
        segments = self._state["segments"]
        active_count = sum(1 for a in actions if a.get("status") in _ACTIVE_STATUSES)
        effective_count = sum(
            1
            for a in actions
            if _judge_effectiveness(
                a.get("status", "DRAFT"),
                a.get("observedLift"),
                float(a.get("targetLift") or 0),
                a.get("evidenceLevel", "medium"),
            )
            == "EFFECTIVE"
        )
        blocked_closeout_count = sum(
            1
            for a in actions
            if a.get("status") == "OUTCOME_READY"
            and not _closeout_gate(a)["canClose"]
        )
        return {
            "segmentCount": len(segments),
            "activeCount": active_count,
            "effectiveCount": effective_count,
            "blockedCloseoutCount": blocked_closeout_count,
        }

    # ------------------------------------------------------------------
    # Write paths — three creation entry points
    # ------------------------------------------------------------------

    def create_action(
        self,
        *,
        name: str,
        segment_id: str,
        objective: str,
        target_lift: float,
        kind: str = "offpeak",
        observation_window_days: int = 14,
        observation_window: str | None = None,
        store: str = "全品牌",
        channel: str = "LINE 推播",
        budget: float = 0,
        rationale: str = "",
        rollback_plan: str = "",
        source_recommendation_id: str | None = None,
        actor_role_id: str = "opsLead",
        actor_name: str = "Operator",
        idempotency_key: str | None = None,
        correlation_id: str = "",
    ) -> dict[str, Any]:
        """Create a Growth Action draft.

        Entry points (three create-entry cards, package 6):
          1. Off-peak promotion  (kind="offpeak")
          2. Member winback      (kind="winback")
          3. PriceOps test       (kind="priceops")

        The draft ``kind`` is persisted so each entry card round-trips its own
        draft type.  A ``source_recommendation_id`` may additionally seed a
        draft from a PriceOps recommendation row.

        Raises GrowthPolicyError when the seeding recommendation has
        HARD_CONSTRAINT_FAILED — callers must resolve the constraint first.
        """
        # Idempotency replay
        if idempotency_key:
            cached = self._state["idempotencyResults"].get(idempotency_key)
            if cached is not None:
                return _clone(cached)

        draft_kind = kind if kind in _DRAFT_TYPES else "offpeak"

        # Conflict gate — block drafts seeded from hard-constrained recs
        if source_recommendation_id:
            rec = self._find_recommendation(source_recommendation_id)
            if rec.get("constraintStatus") == "HARD_CONSTRAINT_FAILED":
                raise GrowthPolicyError(
                    f"recommendation {source_recommendation_id} has HARD_CONSTRAINT_FAILED; "
                    "resolve the constraint before creating a draft"
                )

        # Validate segment exists
        self._find_segment(segment_id)

        action_id = _new_action_id()
        now = _now_iso()
        new_action: dict[str, Any] = {
            "id": action_id,
            "name": name,
            "kind": draft_kind,
            "segmentId": segment_id,
            "sourceRecommendationId": source_recommendation_id,
            "objective": objective,
            "status": "DRAFT",
            "observationWindow": observation_window or "尚未排程",
            "observationWindowDays": observation_window_days,
            "store": store,
            "channel": channel,
            "budget": budget,
            "targetLift": target_lift,
            "observedLift": None,
            "evidenceLevel": "low",
            "rationale": rationale,
            "rollbackPlan": rollback_plan,
            "growthOutcome": None,
            "approvalId": None,
            "createdAt": now,
            "updatedAt": now,
            "audit": {
                "decisionId": f"draft-{action_id}",
                "correlationId": correlation_id,
                "modelVersion": self._state["freshness"]["modelVersion"],
                "policyVersion": self._state["freshness"]["policyVersion"],
                "featureSnapshotTime": self._state["freshness"]["featureSnapshotTime"],
            },
        }
        self._state["actions"].append(new_action)

        audit = self._append_audit_event(
            action="growth.action.created",
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            category="workflow",
            message=f"Growth Action {action_id} created as DRAFT ({draft_kind}).",
            metadata={
                "actionId": action_id,
                "kind": draft_kind,
                "segmentId": segment_id,
                "sourceRecommendationId": source_recommendation_id,
                "idempotencyKey": idempotency_key,
            },
        )
        self._record_shared_audit(
            event_type="operator.growth.action.created",
            actor=actor_name,
            action="create_action",
            resource=f"operator/growth/actions/{action_id}",
            outcome="accepted",
            correlation_id=correlation_id,
            metadata=audit["metadata"],
        )

        result: dict[str, Any] = {
            "id": action_id,
            "status": "DRAFT",
            "kind": draft_kind,
            "name": name,
            "correlation_id": correlation_id,
            "audit": _clone(new_action["audit"]),
            "idempotentReplay": False,
        }
        if idempotency_key:
            self._state["idempotencyResults"][idempotency_key] = _clone(result)
        return result

    # ------------------------------------------------------------------
    # Conflict gate — server-side checks (overlap / PriceOps / budget /
    # fatigue / approval).  Mirrors package 6 gwBuilderChecks so the builder's
    # step 4 blocks submit when a hard conflict exists.
    # ------------------------------------------------------------------

    def check_conflicts(
        self,
        *,
        kind: str = "offpeak",
        store: str = "全品牌",
        observation_window: str = "",
        channel: str = "LINE 推播",
        budget: float = 0,
        exclude_action_id: str | None = None,
    ) -> dict[str, Any]:
        """Run the five Growth conflict checks for a draft payload.

        Returns ``{"checks": [...], "blocked": bool, "reasons": [...]}``.
        A check ``level`` is one of ok | warn | fail.  ``blocked`` is True when
        any check is ``fail`` — the builder must not submit a blocked draft.
        """
        actives = [
            a
            for a in self._state["actions"]
            if a.get("status") in _CONFLICT_ACTIVE_STATUSES
            and a.get("id") != exclude_action_id
        ]

        def _store_overlap(a: str, b: str) -> bool:
            if not a or not b:
                return False
            if "全品牌" in a or "全品牌" in b:
                return True
            return a == b

        # 1. Overlap with another active promotion on the same store + window.
        same_store = [a for a in actives if _store_overlap(store, a.get("store", ""))]
        hard_overlap = next(
            (a for a in same_store if observation_window and a.get("observationWindow") == observation_window),
            None,
        )
        if hard_overlap is not None:
            overlap_check = {
                "id": "overlap",
                "label": "與其他促銷重疊",
                "level": "fail",
                "note": f"同門市同時窗已有進行中活動 {hard_overlap['id']} — 需先錯開時段或結束該活動",
            }
        elif same_store:
            overlap_check = {
                "id": "overlap",
                "label": "與其他促銷重疊",
                "level": "warn",
                "note": f"同門市有 {same_store[0]['id']} 進行中 — 建議錯開時段",
            }
        else:
            overlap_check = {
                "id": "overlap",
                "label": "與其他促銷重疊",
                "level": "ok",
                "note": "無重疊促銷",
            }

        # 2. Conflict with an active PriceOps test on the same store.
        price_clash = next(
            (
                a
                for a in actives
                if a.get("kind") == "priceops" and _store_overlap(store, a.get("store", ""))
            ),
            None,
        )
        priceops_check = {
            "id": "priceops",
            "label": "與 PriceOps 衝突",
            "level": "warn" if price_clash else "ok",
            "note": (
                f"同門市有 {price_clash['id']} PriceOps 進行中 — 建議錯開時段"
                if price_clash
                else "無進行中 PriceOps 衝突"
            ),
        }

        # 3. Budget ceiling — over the single-campaign limit needs 2nd approval.
        over_budget = float(budget or 0) > _BUDGET_SECOND_APPROVAL_CEILING
        budget_check = {
            "id": "budget",
            "label": "預算檢查",
            "level": "warn" if over_budget else "ok",
            "note": (
                f"超出單檔上限 NT${_BUDGET_SECOND_APPROVAL_CEILING:,} — 需二階核准"
                if over_budget
                else f"預算 NT${float(budget or 0):,.0f} 於權限內"
            ),
        }

        # 4. Member fatigue — recent LINE push on an overlapping store.
        line_busy = "LINE" in (channel or "") and any(
            "LINE" in a.get("channel", "") and _store_overlap(store, a.get("store", ""))
            for a in actives
        )
        fatigue_check = {
            "id": "fatigue",
            "label": "會員打擾頻率",
            "level": "warn" if line_busy else "ok",
            "note": (
                "30 天內已有其他 LINE 推播鎖定相近客群 — 注意打擾"
                if line_busy
                else "推播頻率於規範內（30 天 ≤ 2 次）"
            ),
        }

        # 5. Approval — Growth activities are always approval-gated; PriceOps
        #    additionally requires a rollback condition.
        approval_check = {
            "id": "approval",
            "label": "主管核准",
            "level": "warn" if kind == "priceops" else "ok",
            "note": (
                "PriceOps 測試一律需營運主管核准＋回滾條件"
                if kind == "priceops"
                else "送出後需營運主管核准（Growth 活動一律核准制）"
            ),
        }

        checks = [overlap_check, priceops_check, budget_check, fatigue_check, approval_check]
        blocked = any(c["level"] == "fail" for c in checks)
        reasons = [c["note"] for c in checks if c["level"] == "fail"]
        return {"checks": checks, "blocked": blocked, "reasons": reasons}

    # ------------------------------------------------------------------
    # Write path — submit for approval (creates a Govern approval item)
    # ------------------------------------------------------------------

    def submit_for_approval(
        self,
        *,
        action_id: str,
        actor_role_id: str = "growthLead",
        actor_name: str = "Operator",
        idempotency_key: str | None = None,
        correlation_id: str = "",
    ) -> dict[str, Any]:
        """Submit a DRAFT Growth Action for approval.

        Runs the conflict gate first: a blocked (``fail``) draft raises
        GrowthPolicyError carrying the actionable server reasons.  On success a
        Govern approval item (``module="Growth"``) is created and the action
        advances to PENDING_APPROVAL.
        """
        if idempotency_key:
            cached = self._state["idempotencyResults"].get(idempotency_key)
            if cached is not None:
                return {**_clone(cached), "idempotentReplay": True}

        action = self._find_action(action_id)
        if action["status"] != "DRAFT":
            raise GrowthConflict(
                f"only DRAFT actions can be submitted for approval; "
                f"{action_id} is {action['status']}"
            )

        gate = self.check_conflicts(
            kind=action.get("kind", "offpeak"),
            store=action.get("store", "全品牌"),
            observation_window=action.get("observationWindow", ""),
            channel=action.get("channel", "LINE 推播"),
            budget=action.get("budget", 0),
            exclude_action_id=action_id,
        )
        if gate["blocked"]:
            raise GrowthPolicyError(
                "submit blocked by conflict gate: " + "；".join(gate["reasons"])
            )

        approval_id = f"APR-{self._state['nextApprovalOrdinal']}"
        self._state["nextApprovalOrdinal"] += 1
        now = _now_iso()
        risk = "高" if any(c["level"] == "warn" for c in gate["checks"]) else "低"
        approval = {
            "id": approval_id,
            "module": "Growth",
            "kind": "growth",
            "ref": action_id,
            "title": f"活動核准：{action['name']}",
            "requester": actor_name,
            "approver": "營運主管",
            "risk": risk,
            "status": "pending",
            "evidence": [
                f"受眾快照：{action.get('segmentId')}",
                f"預算試算：NT${float(action.get('budget') or 0):,.0f}",
                f"時窗：{action.get('observationWindow')}",
                "衝突檢查：" + ("通過" if risk == "低" else "有警示，已記錄"),
            ],
            "conflictChecks": gate["checks"],
            "reason": "",
            "decidedBy": "",
            "decidedAt": "",
            "createdAt": now,
            "correlationId": correlation_id,
        }
        self._state["approvals"].insert(0, approval)

        action["status"] = "PENDING_APPROVAL"
        action["approvalId"] = approval_id
        action["updatedAt"] = now

        audit = self._append_audit_event(
            action="growth.action.submitted",
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            category="workflow",
            message=f"Growth Action {action_id} submitted for approval → {approval_id}.",
            metadata={
                "actionId": action_id,
                "approvalId": approval_id,
                "status": "PENDING_APPROVAL",
                "idempotencyKey": idempotency_key,
            },
        )
        self._record_shared_audit(
            event_type="operator.growth.action.submitted",
            actor=actor_name,
            action="submit_for_approval",
            resource=f"operator/growth/actions/{action_id}",
            outcome="accepted",
            correlation_id=correlation_id,
            metadata=audit["metadata"],
        )

        result = {
            "id": action_id,
            "status": "PENDING_APPROVAL",
            "approval": _clone(approval),
            "correlation_id": correlation_id,
            "idempotentReplay": False,
        }
        if idempotency_key:
            self._state["idempotencyResults"][idempotency_key] = _clone(result)
        return result

    def list_approvals(self) -> list[dict[str, Any]]:
        """Return the Govern approval items created from Growth submissions."""
        return _clone(self._state["approvals"])

    def resolve_approval(
        self,
        *,
        approval_id: str,
        decision: str,
        reason: str = "",
        actor_role_id: str = "opsLead",
        actor_name: str = "營運主管",
        idempotency_key: str | None = None,
        correlation_id: str = "",
    ) -> dict[str, Any]:
        """Record an approval decision and advance the linked Growth state.

        ``decision`` is "approved" or "rejected".  An approval advances the
        action to APPROVED; a rejection returns it to DRAFT for revision.  Both
        write a Decision Log entry and an Audit Trail event.
        """
        if idempotency_key:
            cached = self._state["idempotencyResults"].get(idempotency_key)
            if cached is not None:
                return {**_clone(cached), "idempotentReplay": True}

        normalized = decision.lower()
        if normalized not in {"approved", "rejected"}:
            raise GrowthConflict(
                f"approval decision must be 'approved' or 'rejected'; got {decision!r}"
            )

        approval = self._find_approval(approval_id)
        if approval["status"] != "pending":
            raise GrowthConflict(
                f"approval {approval_id} already decided: {approval['status']}"
            )

        action = self._find_action(approval["ref"])
        now = _now_iso()
        approval["status"] = normalized
        approval["reason"] = reason
        approval["decidedBy"] = actor_name
        approval["decidedAt"] = now

        new_status = "APPROVED" if normalized == "approved" else "DRAFT"
        action["status"] = new_status
        action["updatedAt"] = now
        if normalized == "rejected":
            action["approvalId"] = None

        decision_entry = self._append_decision_log(
            module="Growth",
            ref=action["id"],
            title=action["name"],
            recommendation="送主管核准",
            verdict="核准" if normalized == "approved" else "駁回",
            reason=reason or ("核准通過" if normalized == "approved" else "退回修改"),
        )

        audit = self._append_audit_event(
            action=f"growth.approval.{normalized}",
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            category="Decision log",
            message=f"Approval {approval_id} {normalized}; {action['id']} → {new_status}.",
            metadata={
                "approvalId": approval_id,
                "actionId": action["id"],
                "decision": normalized,
                "newStatus": new_status,
                "decisionId": decision_entry["id"],
                "idempotencyKey": idempotency_key,
            },
        )
        self._record_shared_audit(
            event_type="operator.growth.approval.decision",
            actor=actor_name,
            action="resolve_approval",
            resource=f"operator/growth/approvals/{approval_id}",
            outcome=normalized,
            correlation_id=correlation_id,
            metadata=audit["metadata"],
        )

        result = {
            "id": approval_id,
            "status": normalized,
            "actionId": action["id"],
            "growthStatus": new_status,
            "decision": _clone(decision_entry),
            "correlation_id": correlation_id,
            "idempotentReplay": False,
        }
        if idempotency_key:
            self._state["idempotencyResults"][idempotency_key] = _clone(result)
        return result

    def list_decisions(self) -> list[dict[str, Any]]:
        """Return the Growth Decision Log entries."""
        return _clone(self._state["decisions"])

    # ------------------------------------------------------------------
    # Write path — lifecycle transition
    # ------------------------------------------------------------------

    def transition_action(
        self,
        *,
        action_id: str,
        target_status: str,
        actor_role_id: str = "opsLead",
        actor_name: str = "Operator",
        idempotency_key: str | None = None,
        correlation_id: str = "",
    ) -> dict[str, Any]:
        """Advance a Growth Action through its lifecycle."""
        if idempotency_key:
            cached = self._state["idempotencyResults"].get(idempotency_key)
            if cached is not None:
                return {**_clone(cached), "idempotentReplay": True}

        action = self._find_action(action_id)
        current_status = action["status"]
        allowed = _LIFECYCLE_TRANSITIONS.get(current_status, set())
        if target_status not in allowed:
            raise GrowthConflict(
                f"cannot transition {action_id} from {current_status} to {target_status}; "
                f"allowed: {', '.join(sorted(allowed)) or 'none'}"
            )
        if target_status == "CLOSED":
            gate = _closeout_gate(action)
            if not gate["canClose"]:
                raise GrowthCloseoutGateError(
                    f"closeout blocked for {action_id}: {gate['reason']}"
                )

        action["status"] = target_status
        action["updatedAt"] = _now_iso()

        audit = self._append_audit_event(
            action=f"growth.action.{target_status.lower()}",
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            category="workflow",
            message=f"Growth Action {action_id} transitioned {current_status} → {target_status}.",
            metadata={
                "actionId": action_id,
                "previousStatus": current_status,
                "status": target_status,
                "idempotencyKey": idempotency_key,
            },
        )
        self._record_shared_audit(
            event_type="operator.growth.action.transition",
            actor=actor_name,
            action="transition_action",
            resource=f"operator/growth/actions/{action_id}",
            outcome="accepted",
            correlation_id=correlation_id,
            metadata=audit["metadata"],
        )

        result = {
            "id": action_id,
            "status": target_status,
            "previousStatus": current_status,
            "correlation_id": correlation_id,
            "auditEvent": _clone(audit),
            "idempotentReplay": False,
        }
        if idempotency_key:
            self._state["idempotencyResults"][idempotency_key] = _clone(result)
        return result

    # ------------------------------------------------------------------
    # Write path — effectiveness outcome writeback
    # ------------------------------------------------------------------

    def write_outcome(
        self,
        *,
        action_id: str,
        outcome: str,
        required_action: str,
        observed_lift: float | None = None,
        evidence_level: str = "medium",
        rationale: str = "",
        actor_role_id: str = "opsLead",
        actor_name: str = "Operator",
        idempotency_key: str | None = None,
        correlation_id: str = "",
    ) -> dict[str, Any]:
        """Write effectiveness verdict back to a Growth Action.

        This endpoint accepts OUTCOME_READY actions.  If outcome==EFFECTIVE
        and required_action==CLOSE, the action moves to CLOSED — but only
        when the derived gate also confirms canClose=True.
        """
        if idempotency_key:
            cached = self._state["idempotencyResults"].get(idempotency_key)
            if cached is not None:
                return {**_clone(cached), "idempotentReplay": True}

        action = self._find_action(action_id)
        if action["status"] not in _OUTCOME_STAGES:
            raise GrowthConflict(
                f"outcome writeback is only valid for OUTCOME_READY or CLOSED actions; "
                f"current status: {action['status']}"
            )

        if observed_lift is not None:
            action["observedLift"] = observed_lift
        if evidence_level:
            action["evidenceLevel"] = evidence_level
        if rationale:
            action["rationale"] = rationale
        action["growthOutcome"] = outcome

        derived = _closeout_gate(action)
        if outcome == "EFFECTIVE" and required_action == "CLOSE" and derived["canClose"]:
            action["status"] = "CLOSED"
        elif outcome == "INEFFECTIVE":
            # Ineffective, matured actions are marked INEFFECTIVE (revise/rollback
            # branch); they cannot be closed directly.
            if action["status"] == "OUTCOME_READY":
                action["status"] = "INEFFECTIVE"
        action["updatedAt"] = _now_iso()

        # Every outcome verdict (effective / ineffective / inconclusive) is
        # persisted to the Decision Log in addition to the Audit Trail.
        _verdict_label = {
            "EFFECTIVE": "判定有效",
            "INEFFECTIVE": "判定無效",
            "INCONCLUSIVE": "判定待判定",
            "PENDING": "觀察中",
        }.get(outcome, outcome)
        decision_entry = self._append_decision_log(
            module="Growth",
            ref=action_id,
            title=action["name"],
            recommendation="成效判斷",
            verdict=_verdict_label,
            reason=rationale or derived["reason"],
        )

        audit = self._append_audit_event(
            action="growth.action.outcome",
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            category="workflow",
            message=f"Growth Action {action_id} outcome recorded: {outcome}.",
            metadata={
                "actionId": action_id,
                "outcome": outcome,
                "requiredAction": required_action,
                "observedLift": observed_lift,
                "evidenceLevel": evidence_level,
                "newStatus": action["status"],
                "idempotencyKey": idempotency_key,
            },
        )
        self._record_shared_audit(
            event_type="operator.growth.action.outcome",
            actor=actor_name,
            action="write_outcome",
            resource=f"operator/growth/actions/{action_id}/outcome",
            outcome="accepted",
            correlation_id=correlation_id,
            metadata=audit["metadata"],
        )

        result = {
            "id": action_id,
            "growth_outcome": outcome,
            "status": action["status"],
            "closeoutGate": derived,
            "decision": _clone(decision_entry),
            "correlation_id": correlation_id,
            "auditEvent": _clone(audit),
            "idempotentReplay": False,
        }
        if idempotency_key:
            self._state["idempotencyResults"][idempotency_key] = _clone(result)
        return result

    # ------------------------------------------------------------------
    # Reset (for tests)
    # ------------------------------------------------------------------

    def reset_to_seed(self) -> None:
        self._state = (
            _seed_state() if self._seed_fixtures else _empty_state()
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_segment(self, segment_id: str) -> dict[str, Any]:
        for segment in self._state["segments"]:
            if segment["id"] == segment_id:
                return segment
        raise GrowthNotFound(f"segment {segment_id!r} not found")

    def _find_recommendation(self, rec_id: str) -> dict[str, Any]:
        for rec in self._state["recommendations"]:
            if rec["id"] == rec_id:
                return rec
        raise GrowthNotFound(f"recommendation {rec_id!r} not found")

    def _find_action(self, action_id: str) -> dict[str, Any]:
        for action in self._state["actions"]:
            if action["id"] == action_id:
                return action
        raise GrowthNotFound(f"growth action {action_id!r} not found")

    def _find_approval(self, approval_id: str) -> dict[str, Any]:
        for approval in self._state["approvals"]:
            if approval["id"] == approval_id:
                return approval
        raise GrowthNotFound(f"growth approval {approval_id!r} not found")

    def _append_decision_log(
        self,
        *,
        module: str,
        ref: str,
        title: str,
        recommendation: str,
        verdict: str,
        reason: str,
    ) -> dict[str, Any]:
        ordinal = self._state.get("nextDecisionOrdinal", 9001)
        entry: dict[str, Any] = {
            "id": f"DEC-{ordinal}",
            "ordinal": ordinal,
            "occurredAt": _now_iso(),
            "module": module,
            "ref": ref,
            "title": title,
            "recommendation": recommendation,
            "verdict": verdict,
            "reason": reason,
        }
        self._state.setdefault("decisions", []).insert(0, entry)
        self._state["nextDecisionOrdinal"] = ordinal + 1
        return entry

    def _append_audit_event(
        self,
        *,
        action: str,
        actor_role_id: str,
        actor_name: str,
        category: str,
        message: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        ordinal = self._state.get("nextAuditOrdinal", 8001)
        event: dict[str, Any] = {
            "id": _new_audit_id("GRW"),
            "ordinal": ordinal,
            "occurredAt": _now_iso(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name,
            "category": category,
            "action": action,
            "message": message,
            "metadata": metadata,
        }
        self._state.setdefault("auditEvents", []).append(event)
        self._state["nextAuditOrdinal"] = ordinal + 1
        return event

    def _record_shared_audit(
        self,
        *,
        event_type: str,
        actor: str,
        action: str,
        resource: str,
        outcome: str,
        correlation_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        self._audit_log.record(
            AuditEvent(
                event_type=event_type,
                actor=actor,
                action=action,
                resource=resource,
                outcome=outcome,
                correlation_id=correlation_id,
                metadata=dict(metadata),
            )
        )


__all__ = [
    "GrowthService",
    "GrowthError",
    "GrowthNotFound",
    "GrowthConflict",
    "GrowthPolicyError",
    "GrowthCloseoutGateError",
]
