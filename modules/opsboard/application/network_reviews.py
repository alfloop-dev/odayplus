"""Network Review decision service for Operator Console R4 (ODP-OC-R4-007).

Owns the task-scoped review-decision surface behind
``/api/v1/operator/network-reviews``:

- **Atomic governance sync**: a single review decision moves five records in
  one synchronous transaction — Candidate stage/status, Review status,
  Approval status, a new Decision Log row, and a new Audit event. Either all
  five change or none do; a rejected (policy-blocked) decision leaves every
  record untouched, and an idempotent replay produces no duplicate records.
- **Decision mapping** (ODP-OC-R4-007 acceptance):
  ``GO → Approved``, ``WAIT → On Hold``, ``Return → Need Data``,
  ``Reject → Rejected``.
- **Reason rules**: every decision needs a reason (>= 10 chars, written to the
  Decision Log); ``WAIT`` also needs pass conditions; ``Return`` needs the
  missing-data list (synced to the Candidate); a decision that overrides the
  SiteScore recommendation needs an explicit risk acknowledgement.
- **Role rule**: an Expansion role may prepare/submit a review but may not
  decide it. The authoritative enforcement is the HTTP guard
  (``sitescore`` + ``Action.APPROVE`` is granted to Site Reviewer / Executive
  but not to Expansion); this service adds a defense-in-depth allowlist so a
  mis-scoped caller still fails closed.

The service is deliberately in-memory and self-contained for the Operator
Console product slice, mirroring the R4-006 ``NetworkScoringService`` and the
R4-009 ``GovernanceService`` idioms. Candidate CS-1002 (RV-701, WAIT) and
CS-1004 (RV-698, REJECT) reuse the review ids seeded by the scoring service;
CS-1001 (RV-702, GO) is the golden approve flow.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

# The review decision verbs, matching the R4 design buttons
# 核准 GO / 核准 WAIT / 退回修改 / 駁回.
DECISION_ACTIONS = ("GO", "WAIT", "RETURN", "REJECT")

# GO → Approved, WAIT → On Hold, Return → Need Data, Reject → Rejected.
DECISION_FINAL_LABEL: dict[str, str] = {
    "GO": "Approved",
    "WAIT": "On Hold",
    "RETURN": "Need Data",
    "REJECT": "Rejected",
}

# Review record status (internal key) per decision.
DECISION_REVIEW_STATUS: dict[str, str] = {
    "GO": "approved",
    "WAIT": "onhold",
    "RETURN": "needdata",
    "REJECT": "rejected",
}

# Candidate record status (internal key) per decision — mirrors the review.
DECISION_CANDIDATE_STATUS: dict[str, str] = {
    "GO": "approved",
    "WAIT": "onhold",
    "RETURN": "needdata",
    "REJECT": "rejected",
}

# Approval record status (governance envelope) per decision.
DECISION_APPROVAL_STATUS: dict[str, str] = {
    "GO": "approved",
    "WAIT": "on_hold",
    "RETURN": "need_data",
    "REJECT": "rejected",
}

STATUS_LABEL: dict[str, str] = {
    "pending": "待審核",
    "approved": "已核准 GO",
    "onhold": "On Hold（WAIT）",
    "needdata": "退回補件（Need Data）",
    "rejected": "已駁回",
}

# The SiteScore recommendation each decision "agrees" with. A decision whose
# verb differs from this natural verb overrides the model recommendation and
# requires a risk acknowledgement. RETURN (defer for data) is never an override.
_NATURAL_ACTION: dict[str, str] = {"GO": "GO", "WAIT": "WAIT", "REJECT": "REJECT"}

# Reason must be substantive (matches the R4-009 governance policy and the
# existing Review panel client rule).
_MIN_REASON_LEN = 10

# Role ids permitted to decide a review. Expansion roles are intentionally
# excluded — they can prepare/submit but not decide. This is defense-in-depth;
# the HTTP layer already fails closed via ``sitescore`` + ``Action.APPROVE``.
DECIDING_ROLE_IDS = frozenset(
    {
        "siteReviewer",
        "site_reviewer",
        "networkReviewer",
        "executive",
        "opsLead",
        "operationsManager",
    }
)


class NetworkReviewNotFound(RuntimeError):
    """Raised when a review id is unknown."""


class NetworkReviewConflict(RuntimeError):
    """Raised when a review has already been decided (idempotency miss)."""


class NetworkReviewPolicyError(RuntimeError):
    """Raised when a decision fails a reason / condition / override policy."""


class NetworkReviewRoleError(RuntimeError):
    """Raised when the actor role is not allowed to decide (fail closed)."""


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _payload_fingerprint(
    *,
    action: str,
    reason: str,
    conditions: str,
    required_data: list[str],
    override_ack: bool,
    actor_role_id: str,
) -> str:
    """Stable hash of the decision payload.

    Combined with the review id and Idempotency-Key it scopes the replay cache
    so a key can only replay the *same decision on the same review*. A key
    reused across reviews (or with a different payload) misses the cache instead
    of returning another review's cached result.
    """

    canonical = json.dumps(
        {
            "action": action,
            "reason": reason,
            "conditions": conditions,
            "requiredData": required_data,
            "overrideAck": bool(override_ack),
            "actorRoleId": actor_role_id,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_override(action: str, recommendation: str) -> bool:
    """A decision overrides the model when its verb differs from the SiteScore
    recommendation's natural verb. RETURN (defer for data) never overrides."""

    if action == "RETURN":
        return False
    natural = _NATURAL_ACTION.get(recommendation.upper())
    return natural is not None and action != natural


def _seed_reviews() -> list[dict[str, Any]]:
    """Package-6 canonical Network review queue.

    RV-702 信義松仁 (GO 82) is the golden approve flow. RV-701 板橋府中
    (WAIT 76) exposes the pass-conditions path. RV-698 大安和平 (REJECT 49)
    exposes the reject / return paths. Review ids RV-701 / RV-698 match the
    ``reviewId`` fields seeded by ``NetworkScoringService``.
    """

    return [
        {
            "id": "RV-702",
            "candidateId": "CS-1001",
            "candidateTitle": "信義松仁候選點",
            "zoneLabel": "信義松仁 86",
            "recommendation": "GO",
            "score": 82,
            "risk": "回本 22 個月 · 週末停車不易",
            "requestedBy": "王若寧（拓展）",
            "reviewerRole": "選址審核 / Site Reviewer",
            "submittedAt": "2026-07-13 09:20",
            "dueAt": "2026-07-16 18:00",
            "payback": "22 個月",
            "m12P50": "NT$428K",
            "rentReasonableness": "合理（區間 P45）",
            "cannibalization": "4%（低）",
            "sourceListingId": "L-2024",
            "fieldVisit": "已完成（現勘照片 6 張）",
            "brokerContact": "已聯絡（王仲介）",
            "notes": "住宅＋商辦混合，夜間洗烘需求強；既有店 650m 稀釋僅 4%。",
            "modelVersion": "SiteScore v2.3",
            "datasetSnapshotId": "FS-20260704-0600",
            "compareText": "P1 主推（GO 82，領先板橋府中 6 分）",
            "candidateStatus": "pendingreview",
            "eventChips": ["SiteScore v2.3", "Dataset FS-20260704-0600", "現勘完成", "比較 P1"],
            "history": [
                {"t": "2026-07-04 06:10", "v": "SiteScore 完成 GO 82"},
                {"t": "2026-07-13 09:20", "v": "王若寧送審（拓展）"},
            ],
        },
        {
            "id": "RV-701",
            "candidateId": "CS-1002",
            "candidateTitle": "板橋府中候選點",
            "zoneLabel": "板橋府中 78",
            "recommendation": "WAIT",
            "score": 76,
            "risk": "站前施工至 12 月 · 與府中店重疊 11%",
            "requestedBy": "王若寧（拓展）",
            "reviewerRole": "選址審核 / Site Reviewer",
            "submittedAt": "2026-07-13 16:42",
            "dueAt": "2026-07-17 18:00",
            "payback": "27 個月",
            "m12P50": "NT$372K",
            "rentReasonableness": "偏高（區間 P70）",
            "cannibalization": "11%（中）",
            "sourceListingId": "L-2025",
            "fieldVisit": "已完成（6/28 現勘）",
            "brokerContact": "已聯絡（王仲介）",
            "notes": "捷運通勤人流大，惟站前施工圍籬與府中店重疊需以條件管理。",
            "modelVersion": "SiteScore v2.3",
            "datasetSnapshotId": "FS-20260703-0600",
            "compareText": "P2 備選（WAIT 76，條件改善後可重評）",
            "candidateStatus": "pendingreview",
            "eventChips": ["SiteScore v2.3", "Dataset FS-20260703-0600", "現勘完成", "比較 P2"],
            "history": [
                {"t": "2026-07-13 16:42", "v": "王若寧送審（拓展）"},
            ],
        },
        {
            "id": "RV-698",
            "candidateId": "CS-1004",
            "candidateTitle": "大安和平候選點",
            "zoneLabel": "大安和平 74",
            "recommendation": "REJECT",
            "score": 49,
            "risk": "租金 P90 · 回本 41 個月超品牌上限",
            "requestedBy": "王若寧（拓展）",
            "reviewerRole": "選址審核 / Site Reviewer",
            "submittedAt": "2026-06-30 14:20",
            "dueAt": "2026-07-15 18:00",
            "payback": "41 個月",
            "m12P50": "NT$268K",
            "rentReasonableness": "過高（區間 P90）",
            "cannibalization": "12%（中高）",
            "sourceListingId": "L-2027",
            "fieldVisit": "已完成（6/24 現勘）",
            "brokerContact": "已聯絡",
            "notes": "租金過高、回本期 41 個月超出品牌上限 30 個月，與大安和平店重疊 12%。",
            "modelVersion": "SiteScore v2.3",
            "datasetSnapshotId": "FS-20260630-0600",
            "compareText": "不建議（REJECT 49，租金 P90 回本 41 個月）",
            "candidateStatus": "pendingreview",
            "eventChips": ["SiteScore v2.3", "Dataset FS-20260630-0600", "現勘完成", "比較 不建議"],
            "history": [
                {"t": "2026-06-30 14:20", "v": "SiteScore 完成 REJECT 49"},
                {"t": "2026-06-30 14:20", "v": "王若寧送審（拓展）"},
            ],
        },
    ]


class NetworkReviewService:
    """Application service for R4 review decision + atomic governance sync."""

    def __init__(
        self,
        *,
        initial_state: dict[str, Any] | None = None,
        seed_fixtures: bool = True,
    ) -> None:
        self._seed_fixtures = seed_fixtures
        if initial_state is not None:
            self._reviews = _copy(initial_state.get("reviews", []))
            self._candidates = _copy(initial_state.get("candidates", {}))
            self._approvals = _copy(initial_state.get("approvals", {}))
            self._decisions = _copy(initial_state.get("decisions", []))
            self._audit_events = _copy(initial_state.get("auditEvents", []))
            self._idempotency_cache = _copy(
                initial_state.get("idempotencyCache", {})
            )
            return

        self._reviews = _seed_reviews() if seed_fixtures else []
        self._candidates: dict[str, dict[str, Any]] = {}
        self._approvals: dict[str, dict[str, Any]] = {}
        self._decisions: list[dict[str, Any]] = []
        self._audit_events: list[dict[str, Any]] = []
        self._idempotency_cache: dict[tuple[str, ...], dict[str, Any]] = {}
        for review in self._reviews:
            review["status"] = "pending"
            review["statusLabel"] = STATUS_LABEL["pending"]
            review["decision"] = None
            self._candidates[review["candidateId"]] = {
                "id": review["candidateId"],
                "title": review["candidateTitle"],
                "zoneLabel": review["zoneLabel"],
                "status": "pendingreview",
                "statusLabel": "待審核",
                "recommendation": review["recommendation"],
                "score": review["score"],
                "reviewId": review["id"],
                "missingData": [],
            }
            self._approvals[review["id"]] = {
                "id": f"AP-{review['id']}",
                "reviewId": review["id"],
                "candidateId": review["candidateId"],
                "title": review["candidateTitle"],
                "systemRecommendation": review["recommendation"],
                "risk": review["risk"],
                "status": "pending",
                "submittedAt": review["submittedAt"],
                "decidedAt": None,
                "decidedBy": None,
            }

    # -- public API ----------------------------------------------------

    def reset(self) -> dict[str, Any]:
        self.__init__(seed_fixtures=self._seed_fixtures)
        return self.snapshot()

    def export_state(self) -> dict[str, Any]:
        return {
            "reviews": _copy(self._reviews),
            "candidates": _copy(self._candidates),
            "approvals": _copy(self._approvals),
            "decisions": _copy(self._decisions),
            "auditEvents": _copy(self._audit_events),
            "idempotencyCache": _copy(self._idempotency_cache),
        }

    def snapshot(self, *, correlation_id: str | None = None) -> dict[str, Any]:
        reviews = [self._review_view(review) for review in self._sorted_reviews()]
        pending = sum(1 for review in self._reviews if review["status"] == "pending")
        return {
            "source": "api",
            "reviews": reviews,
            "candidates": [_copy(candidate) for candidate in self._candidates.values()],
            "approvals": [_copy(approval) for approval in self._approvals.values()],
            "decisions": _copy(self._decisions),
            "auditEvents": _copy(self._audit_events),
            "decisionMapping": dict(DECISION_FINAL_LABEL),
            "counts": {
                "reviews": len(self._reviews),
                "pending": pending,
                "decided": len(self._reviews) - pending,
            },
            "correlationId": correlation_id,
        }

    def decide_review(
        self,
        *,
        review_id: str,
        decision: str,
        reason: str,
        conditions: str | None = None,
        required_data: list[str] | None = None,
        override_ack: bool = False,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        """Atomically apply a review decision across all five governance records.

        Validation happens first and mutates nothing, so a policy failure leaves
        Candidate / Review / Approval / Decision / Audit unchanged. The commit is
        wrapped in a rollback guard so an unexpected mid-commit error also leaves
        no partial write. Replays on the same Idempotency-Key return the cached
        result and create no duplicate records.

        The replay cache is scoped by ``(review_id, Idempotency-Key, payload
        fingerprint)`` so a key can only replay the same decision on the same
        review — a key accidentally reused across reviews (or with a different
        payload) never returns another review's cached result.
        """

        action = (decision or "").strip().upper()
        reason_text = (reason or "").strip()
        condition_text = (conditions or "").strip()
        required_list = [item.strip() for item in (required_data or []) if item and item.strip()]

        cache_key = (
            "decide",
            review_id,
            idempotency_key or "",
            _payload_fingerprint(
                action=action,
                reason=reason_text,
                conditions=condition_text,
                required_data=required_list,
                override_ack=override_ack,
                actor_role_id=actor_role_id,
            ),
        )
        if idempotency_key and cache_key in self._idempotency_cache:
            return {**_copy(self._idempotency_cache[cache_key]), "idempotentReplay": True}

        if action not in DECISION_ACTIONS:
            raise NetworkReviewPolicyError(
                f"decision must be one of {', '.join(DECISION_ACTIONS)}"
            )

        review = self._review(review_id)
        if review["status"] != "pending":
            raise NetworkReviewConflict(
                f"review {review_id} already decided: {review['status']}"
            )

        # Role rule: Expansion may prepare/submit but not decide (fail closed).
        if actor_role_id not in DECIDING_ROLE_IDS:
            raise NetworkReviewRoleError(
                f"role {actor_role_id!r} may prepare or submit but not decide network reviews"
            )

        if len(reason_text) < _MIN_REASON_LEN:
            raise NetworkReviewPolicyError(
                "決策原因必填（至少 10 字），寫入 Decision Log"
            )

        if action == "WAIT" and not condition_text:
            raise NetworkReviewPolicyError("核准 WAIT 需填寫通過條件（條件達成後可重評為 GO）")

        if action == "RETURN" and not required_list:
            raise NetworkReviewPolicyError("退回修改需填寫需補資料（會同步至 Candidate 缺資料清單）")

        override = _is_override(action, review["recommendation"])
        if override and not override_ack:
            raise NetworkReviewPolicyError(
                "本決策覆寫系統建議，需勾選風險確認後才能送出"
            )

        # ---- build all five records before touching state --------------
        now = _now()
        candidate = self._candidates[review["candidateId"]]
        approval = self._approvals[review["id"]]

        review_status = DECISION_REVIEW_STATUS[action]
        candidate_status = DECISION_CANDIDATE_STATUS[action]
        approval_status = DECISION_APPROVAL_STATUS[action]
        final_label = DECISION_FINAL_LABEL[action]

        decision_id = f"DEC-REVIEW-{uuid.uuid4().hex[:8]}"
        audit_id = f"AUD-REVIEW-{uuid.uuid4().hex[:10]}"

        decision_summary = {
            "decision": action,
            "finalLabel": final_label,
            "mappedStatus": review_status,
            "reason": reason_text,
            "conditions": condition_text,
            "requiredData": required_list,
            "override": override,
            "decidedAt": now,
            "decidedBy": actor_name or actor_role_id,
            "actorRoleId": actor_role_id,
            "decisionId": decision_id,
            "approvalId": approval["id"],
            "auditId": audit_id,
        }

        decision_row = {
            "id": decision_id,
            "reviewId": review["id"],
            "candidateId": review["candidateId"],
            "module": "Network 選址審核",
            "item": review["candidateTitle"],
            "systemRecommendation": review["recommendation"],
            "decision": action,
            "finalDecision": final_label,
            "mappedStatus": review_status,
            "reason": reason_text,
            "conditions": condition_text,
            "requiredData": required_list,
            "override": override,
            "actor": actor_name or actor_role_id,
            "actorRoleId": actor_role_id,
            "decidedAt": now,
            "modelVersion": review["modelVersion"],
            "datasetSnapshot": review["datasetSnapshotId"],
            "approvalId": approval["id"],
            "correlationId": correlation_id,
        }

        audit_event = {
            "id": audit_id,
            "occurredAt": now,
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Site Reviewer",
            "category": "governance",
            "action": "review.decision",
            "targetType": "review",
            "targetId": review["id"],
            "message": (
                f"{review['id']} {action} → {final_label} for {review['candidateId']}"
                + ("（覆寫系統建議）" if override else "")
            ),
            "correlationId": correlation_id,
            "metadata": {
                "decision": action,
                "finalLabel": final_label,
                "mappedStatus": review_status,
                "candidateId": review["candidateId"],
                "approvalId": approval["id"],
                "decisionId": decision_id,
                "override": override,
                "systemRecommendation": review["recommendation"],
            },
        }

        history_entry = {
            "t": now,
            "v": f"{review['reviewerRole']}決策 {action} → {final_label}"
            + ("（覆寫）" if override else ""),
        }

        # ---- atomic commit (rollback on any unexpected error) ----------
        with self._transaction():
            review["status"] = review_status
            review["statusLabel"] = STATUS_LABEL[review_status]
            review["decision"] = decision_summary
            review["history"] = [history_entry, *review.get("history", [])]

            candidate["status"] = candidate_status
            candidate["statusLabel"] = STATUS_LABEL[candidate_status]
            if action == "RETURN":
                candidate["missingData"] = required_list
            else:
                candidate["missingData"] = []

            approval["status"] = approval_status
            approval["decidedAt"] = now
            approval["decidedBy"] = actor_name or actor_role_id

            self._decisions.insert(0, decision_row)
            self._audit_events.insert(0, audit_event)

        result = {
            "review": self._review_view(review),
            "candidate": _copy(candidate),
            "approval": _copy(approval),
            "decision": _copy(decision_row),
            "auditEvent": _copy(audit_event),
            "records": {
                "candidateId": review["candidateId"],
                "reviewId": review["id"],
                "approvalId": approval["id"],
                "decisionId": decision_id,
                "auditId": audit_id,
            },
            "correlationId": correlation_id,
            "idempotentReplay": False,
        }
        if idempotency_key:
            self._idempotency_cache[cache_key] = _copy(result)
        return result

    # -- internals -----------------------------------------------------

    def _review(self, review_id: str) -> dict[str, Any]:
        for review in self._reviews:
            if review["id"] == review_id:
                return review
        raise NetworkReviewNotFound(f"review {review_id} not found")

    def _sorted_reviews(self) -> list[dict[str, Any]]:
        # Pending first, then decided; stable on submitted time (newest first).
        return sorted(
            self._reviews,
            key=lambda review: (review["status"] != "pending", review["submittedAt"]),
        )

    def _review_view(self, review: dict[str, Any]) -> dict[str, Any]:
        candidate = self._candidates.get(review["candidateId"], {})
        view = _copy(review)
        view["candidateStatus"] = candidate.get("status", review.get("candidateStatus"))
        view["candidateStatusLabel"] = candidate.get("statusLabel", "待審核")
        view["candidateMissingData"] = list(candidate.get("missingData", []))
        view["pending"] = review["status"] == "pending"
        view["decided"] = review["status"] != "pending"
        view["overrideAvailable"] = review["status"] == "pending"
        return view

    def _snapshot_state(self) -> dict[str, Any]:
        return {
            "reviews": _copy(self._reviews),
            "candidates": _copy(self._candidates),
            "approvals": _copy(self._approvals),
            "decisions": _copy(self._decisions),
            "audit_events": _copy(self._audit_events),
        }

    def _restore_state(self, state: dict[str, Any]) -> None:
        self._reviews = state["reviews"]
        self._candidates = state["candidates"]
        self._approvals = state["approvals"]
        self._decisions = state["decisions"]
        self._audit_events = state["audit_events"]

    class _Transaction:
        def __init__(self, service: NetworkReviewService) -> None:
            self._service = service
            self._backup: dict[str, Any] | None = None

        def __enter__(self) -> NetworkReviewService._Transaction:
            self._backup = self._service._snapshot_state()
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            if exc_type is not None and self._backup is not None:
                # Roll back every record so a failed transaction leaves all
                # five unchanged (ODP-OC-R4-007 atomicity acceptance).
                self._service._restore_state(self._backup)
            return False

    def _transaction(self) -> NetworkReviewService._Transaction:
        return NetworkReviewService._Transaction(self)


__all__ = [
    "DECISION_ACTIONS",
    "DECISION_FINAL_LABEL",
    "DECIDING_ROLE_IDS",
    "NetworkReviewConflict",
    "NetworkReviewNotFound",
    "NetworkReviewPolicyError",
    "NetworkReviewRoleError",
    "NetworkReviewService",
]
