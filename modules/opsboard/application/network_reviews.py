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

Local/test mode remains self-contained for the package fixture. Production
mode reads and mutates the tenant-scoped canonical SiteScore decision store
through ``SiteScoreDecisionWorkflow``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any, Literal

from modules.sitescore.domain.scoring import SiteScoreReport
from shared.domain.models import MEASUREMENT_MEASURED, MEASUREMENT_UNMEASURED
from shared.workflow.sitescore import (
    DecisionAction,
    DecisionStatus,
    SiteScoreDecisionError,
)

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
    "unknown": "未記錄 Network 核決動作",
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


class NetworkReviewRuntimeUnavailable(RuntimeError):
    """Raised when canonical SiteScore decision dependencies are unavailable."""

    def to_detail(self) -> dict[str, Any]:
        return {
            "code": "SITESCORE_DECISION_DEPENDENCY_UNAVAILABLE",
            "message": str(self),
            "retryable": True,
        }


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _finite_measurement(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _payload_fingerprint(
    *,
    action: str,
    reason: str,
    conditions: str,
    required_data: list[str],
    override_ack: bool,
    actor_role_id: str,
    expected_target: dict[str, Any] | None = None,
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
            **({"expectedTarget": expected_target} if expected_target is not None else {}),
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
        decision_repository: Any | None = None,
        sitescore_repository: Any | None = None,
        decision_workflow: Any | None = None,
        require_canonical: bool = False,
        audit_delivery_factory: Any | None = None,
        candidate_workflow_service: Any | None = None,
    ) -> None:
        self._seed_fixtures = seed_fixtures
        self._decision_repository = decision_repository
        self._sitescore_repository = sitescore_repository
        self._decision_workflow = decision_workflow
        self._require_canonical = require_canonical
        self._audit_delivery_factory = audit_delivery_factory
        self._candidate_workflow_service = candidate_workflow_service
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

    def transaction_scope(self, engine: Any, tenant_id: str):
        """Require all canonical mutation writes to share the loaded SQL unit."""
        self._require_canonical_dependencies()
        audit = self._decision_workflow.audit_log
        if (self._decision_repository.engine is not engine
                or self._decision_repository.tenant_id != tenant_id
                or getattr(audit, "_engine", None) is not engine):
            raise NetworkReviewRuntimeUnavailable("canonical review persistence must share the tenant SQL engine")
        if self._decision_workflow._store is not self._decision_repository:
            raise NetworkReviewRuntimeUnavailable("canonical workflow and review must share the decision repository")
        if (getattr(self._sitescore_repository, "engine", None) is not engine
                or getattr(self._sitescore_repository, "tenant_id", None) != tenant_id):
            raise NetworkReviewRuntimeUnavailable("canonical SiteScore reader must share the tenant SQL engine")
        approval_store = self._decision_workflow._approval_store
        if approval_store is None or approval_store.engine is not engine or approval_store.tenant_id != tenant_id:
            raise NetworkReviewRuntimeUnavailable("canonical approval must share the tenant SQL engine")
        candidate_service = self._candidate_workflow_service
        if candidate_service is None or (
            candidate_service._listing_repository._store.engine is not engine
            or candidate_service._listing_repository.tenant_id != tenant_id
            or candidate_service._intakes._store.engine is not engine
            or candidate_service._intakes._store.tenant_id != tenant_id
            or candidate_service._tenant_id != tenant_id
            or candidate_service._review_decision_repository is not self._decision_repository
        ):
            raise NetworkReviewRuntimeUnavailable("canonical candidate must share the tenant SQL engine")
        if getattr(audit, "_worm_sink", None) is not None:
            if self._audit_delivery_factory is None:
                raise NetworkReviewRuntimeUnavailable("canonical review WORM requires a transactional durable delivery")
            try:
                delivery = self._audit_delivery_factory(tenant_id)
            except ValueError as exc:
                raise NetworkReviewRuntimeUnavailable(str(exc)) from exc
            return audit.transactional_worm_sink(delivery)
        return nullcontext()

    def reset(self) -> dict[str, Any]:
        if self._require_canonical:
            self._audit_events = []
            self._idempotency_cache = {}
            return self.snapshot()
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
        if self._require_canonical:
            return self._canonical_snapshot(correlation_id=correlation_id)
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

    def submission_context(self, *, candidate_id: str, principal: Any, decision_id: str | None = None,
                           module: Literal['operator_console', 'sitescore'] = 'operator_console'):
        from .network_review_submission import NetworkReviewSubmissionOwner
        return NetworkReviewSubmissionOwner(self).context(candidate_id=candidate_id, principal=principal, decision_id=decision_id, module=module)

    def authorize_native_report_candidate(self, *, candidate_id: str | None, principal: Any, report_repository: Any):
        """Authorize retained report reads without acquiring current model admission."""
        from shared.auth import Action

        from .network_review_submission import NetworkReviewSubmissionOwner
        self._require_canonical_dependencies()
        if (getattr(report_repository, 'engine', None) is not self._sitescore_repository.engine
                or getattr(report_repository, 'tenant_id', None) != self._sitescore_repository.tenant_id):
            raise NetworkReviewRuntimeUnavailable('native report reader must share the canonical tenant SQL engine')
        self.transaction_scope(self._decision_repository.engine, self._decision_repository.tenant_id)
        if candidate_id is None:
            return None
        candidate = NetworkReviewSubmissionOwner(self)._candidate(
            candidate_id, principal, action=Action.VIEW, module='sitescore',
        )
        if candidate.candidate_site.candidate_site_id != candidate_id:
            raise NetworkReviewConflict('CURRENT_CANDIDATE_RELATIONSHIP_CONFLICT')
        return candidate.candidate_site.candidate_site_id

    def submission_context_for_report(self, *, report_id: str, principal: Any):
        self._require_canonical_dependencies()
        report = self._sitescore_repository.get_report(report_id)
        if report is None:
            raise NetworkReviewNotFound("report not found")
        context = self.submission_context(candidate_id=report.candidate_site_id, principal=principal, module='sitescore')
        if context["target"]["expectedReportId"] != report_id:
            raise NetworkReviewConflict("SUBMISSION_REPORT_CONTEXT_CONFLICT")
        return context

    def submit_native_report(self, *, report_id: str, principal: Any, request_payload: dict[str, Any],
                             idempotency_key: str | None, correlation_id: str | None):
        from .network_review_submission import NetworkReviewSubmissionOwner
        self._require_canonical_dependencies()
        report = self._sitescore_repository.get_report(report_id)
        if report is None:
            raise NetworkReviewNotFound("report not found")
        NetworkReviewSubmissionOwner(self)._candidate(report.candidate_site_id, principal, module='sitescore')
        if not idempotency_key:
            raise NetworkReviewConflict("IDEMPOTENCY_KEY_REQUIRED")
        fingerprint = hashlib.sha256(json.dumps(request_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        key = ("canonical-native-submit", principal.tenant_id, principal.subject_id, report_id, idempotency_key)
        cached = self._idempotency_cache.get(key)
        if cached:
            if cached["requestFingerprint"] != fingerprint:
                raise NetworkReviewConflict("IDEMPOTENCY_PAYLOAD_CONFLICT")
            return {**_copy(cached["response"]), "idempotentReplay": True}
        context = self.submission_context_for_report(report_id=report_id, principal=principal)
        receipt = self.submit_review(candidate_id=report.candidate_site_id, principal=principal,
            target=context["target"], idempotency_key="native:" + idempotency_key, correlation_id=correlation_id,
            submission_summary=request_payload.get("submission_summary"), reviewer_group_id=request_payload.get("reviewer_group_id"), module='sitescore')
        receipt["commandReceipt"]["idempotencyKey"] = idempotency_key
        self._idempotency_cache[key] = {"requestFingerprint": fingerprint, "response": _copy(receipt)}
        return receipt

    def submit_review(self, *, candidate_id: str, principal: Any, target: dict[str, Any],
                      idempotency_key: str | None, correlation_id: str | None,
                      submission_summary: str, reviewer_group_id: str,
                      module: Literal['operator_console', 'sitescore'] = 'operator_console'):
        from .network_review_submission import NetworkReviewSubmissionOwner
        return NetworkReviewSubmissionOwner(self).submit(candidate_id=candidate_id, principal=principal, target=target,
            idempotency_key=idempotency_key, correlation_id=correlation_id,
            submission_summary=submission_summary, reviewer_group_id=reviewer_group_id, module=module)

    @staticmethod
    def _has_recorded_decision_identity(decision: Any) -> bool:
        return (not isinstance(decision.version, bool) and isinstance(decision.version, int) and decision.version > 0
                and isinstance(decision.candidate_site_id, str) and bool(decision.candidate_site_id.strip())
                and isinstance(decision.report_id, str) and bool(decision.report_id.strip())
                and not isinstance(decision.report_version, bool) and isinstance(decision.report_version, int)
                and decision.report_version > 0
                and isinstance(decision.model_version, str) and bool(decision.model_version.strip()))

    def decision_context(self, *, review_id: str, candidate_id: str, principal: Any) -> dict[str, Any]:
        """Read this actor's exact pending owner; never grant authority from a rail row."""
        from shared.auth import Action

        from .network_review_submission import NetworkReviewSubmissionOwner, candidate_digest

        self._require_canonical_dependencies()
        owner = NetworkReviewSubmissionOwner(self)
        candidate = owner._candidate(candidate_id, principal, action=Action.VIEW)
        with self._sitescore_repository.report_guard(candidate_id), owner.service._candidate_workflow_service._listing_repository.candidate_guard(candidate_id):
            candidate = owner._candidate(candidate_id, principal, action=Action.VIEW)
            decision = self._decision_repository.get_decision(review_id)
            if (decision is None or decision.candidate_site_id != candidate_id
                    or decision.tenant_id != principal.tenant_id or candidate.review_id != review_id):
                raise NetworkReviewConflict("CURRENT_REVIEW_OWNER_REQUIRED")
            _, availability = self._retained_decision_report(decision)
            approval = self._decision_workflow._approval_store.get(decision.approval_id) if decision.approval_id else None
            if (not self._has_recorded_decision_identity(decision) or availability != "available"
                    or approval is None or approval.decision_id != review_id):
                raise NetworkReviewConflict("RECORDED_REVIEW_CONTEXT_REQUIRED")
            refusal = None
            try:
                self.authorize_review_actor(decision, principal)
            except NetworkReviewRoleError as exc:
                refusal = str(exc)
            pending = decision.status is DecisionStatus.PENDING_REVIEW and approval.approval_status == "pending"
            return {"source": "canonical", "actorSubjectId": principal.subject_id, "tenantId": principal.tenant_id,
                    "canDecide": pending and refusal is None, "refusalReason": refusal,
                    "target": {"expectedCandidateId": candidate_id, "expectedCandidateDigest": candidate_digest(candidate),
                               "expectedDecisionVersion": decision.version, "expectedReportId": decision.report_id,
                               "expectedReportVersion": decision.report_version, "expectedApprovalId": approval.approval_id}}

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
        principal: Any | None = None,
        expected_target: dict[str, Any] | None = None,
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
                expected_target=expected_target,
            ),
        )
        if not self._require_canonical and idempotency_key and cache_key in self._idempotency_cache:
            return {**_copy(self._idempotency_cache[cache_key]), "idempotentReplay": True}

        if self._require_canonical:
            return self._decide_canonical(
                review_id=review_id,
                action=action,
                reason=reason_text,
                conditions=condition_text,
                required_data=required_list,
                override_ack=override_ack,
                actor_role_id=actor_role_id,
                actor_name=actor_name,
                idempotency_key=idempotency_key,
                cache_key=cache_key,
                correlation_id=correlation_id,
                principal=principal,
                expected_target=expected_target,
            )

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

    def _require_canonical_dependencies(self) -> None:
        missing = [
            name
            for name, value in (
                ("decision_repository", self._decision_repository),
                ("sitescore_repository", self._sitescore_repository),
                ("decision_workflow", self._decision_workflow),
            )
            if value is None
        ]
        if missing:
            raise NetworkReviewRuntimeUnavailable(
                "canonical SiteScore decision dependencies are unavailable: "
                + ", ".join(missing)
            )

    @staticmethod
    def _reviewer_groups():
        from .network_review_submission import reviewer_groups
        return reviewer_groups()

    def _canonical_snapshot(
        self,
        *,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        self._require_canonical_dependencies()
        decisions = self._decision_repository.list_decisions()
        reviews: list[dict[str, Any]] = []
        approvals: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        status_map = {
            DecisionStatus.SYSTEM_RECOMMENDED: "pending",
            DecisionStatus.PENDING_REVIEW: "pending",
            DecisionStatus.DRAFT: "needdata",
            DecisionStatus.APPROVED: "approved",
            DecisionStatus.REJECTED: "rejected",
        }
        for decision in decisions:
            report, report_availability = self._retained_decision_report(decision)
            status_key = status_map[decision.status]
            recorded_action = getattr(decision, "network_action", None)
            if recorded_action is not None:
                status_key = DECISION_REVIEW_STATUS[recorded_action]
            elif decision.status in {DecisionStatus.DRAFT, DecisionStatus.APPROVED}:
                status_key = "unknown"
            # A report records forecasts and confidence, not a scalar suitability
            # score. Use only this decision's frozen report, never latest(report).
            score = None
            confidence = (
                report.confidence
                if report is not None
                and getattr(report, "confidence_status", MEASUREMENT_UNMEASURED) == MEASUREMENT_MEASURED
                and _finite_measurement(report.confidence)
                and 0 <= report.confidence <= 1
                else None
            )
            risks = list(report.key_negative_factors) if report is not None else []
            recorded_action = getattr(decision, "network_action", None)
            terminal = decision.history[-1] if recorded_action and decision.history else None
            decision_view = None
            if terminal is not None:
                action = recorded_action
                decision_view = {
                    "decision": action,
                    "finalLabel": DECISION_FINAL_LABEL[action],
                    "mappedStatus": status_key,
                    "reason": terminal.reason,
                    "override": _is_override(action, decision.recommendation.value),
                    "decidedAt": terminal.at.isoformat(),
                    "decidedBy": terminal.actor,
                    "decisionId": decision.decision_id,
                    "conditions": getattr(decision, "conditions", None),
                    "requiredData": (list(decision.required_data) if getattr(decision, "required_data", None) is not None else None),
                    "overrideAck": getattr(decision, "override_ack", None),
                    "actorRoleId": getattr(decision, "actor_role_id", None),
                    "approvalId": getattr(decision, "approval_id", None),
                }
            row = {
                "id": decision.decision_id,
                "candidateId": decision.candidate_site_id,
                "candidateTitle": decision.candidate_site_id,
                "zoneLabel": report.heat_zone_id if report is not None else None,
                "recommendation": decision.recommendation.value,
                "score": score,
                "confidence": confidence,
                "confidenceStatus": MEASUREMENT_MEASURED if confidence is not None else MEASUREMENT_UNMEASURED,
                "reportAvailability": report_availability,
                "risk": "、".join(risks),
                "status": status_key,
                "statusLabel": (
                    "系統建議（尚未送審）"
                    if decision.status is DecisionStatus.SYSTEM_RECOMMENDED
                    else STATUS_LABEL[status_key]
                ),
                "pending": decision.status is DecisionStatus.PENDING_REVIEW,
                "decided": recorded_action is not None,
                "submittedAt": decision.created_at.isoformat(),
                "requestedBy": decision.created_by,
                "reviewerRole": (
                    next((group["label"] for group in self._reviewer_groups()
                          if group["id"] == getattr(decision, "reviewer_group_id", None)), None)
                    if getattr(decision, "submission_actor_verified", False) else "Site Reviewer"
                ),
                "dueAt": None,
                "payback": self._recorded_number(report.payback_p50_months, " 個月") if report is not None else None,
                "m12P50": self._recorded_number(report.m12.p50) if report is not None else None,
                "rentReasonableness": self._recorded_number(report.rent_reasonableness) if report is not None else None,
                "cannibalization": self._recorded_number(report.cannibalization_risk) if report is not None else None,
                "sourceListingId": None,
                "fieldVisit": None,
                "brokerContact": None,
                "notes": getattr(decision, "submission_summary", None) if getattr(decision, "submission_actor_verified", False) else None,
                "compareText": None,
                "eventChips": [],
                "modelVersion": (
                    decision.model_version
                    if isinstance(decision.model_version, str) and decision.model_version.strip()
                    else None
                ),
                "datasetSnapshotId": (
                    report.source_snapshot_ids[0]
                    if report is not None and report.source_snapshot_ids
                    else None
                ),
                "history": [
                    {
                        "t": transition.at.isoformat(),
                        "v": (
                            f"{transition.actor}: {transition.action} "
                            f"{transition.to_status.value}"
                        ),
                    }
                    for transition in reversed(decision.history)
                ],
                "decision": decision_view,
                "canonicalDecisionStatus": decision.status.value,
                "reportId": decision.report_id,
                "reportVersion": decision.report_version,
            }
            reviews.append(row)
            candidates.append(
                {
                    "id": decision.candidate_site_id,
                    "status": status_key,
                    "statusLabel": STATUS_LABEL[status_key],
                    "recommendation": decision.recommendation.value,
                    "score": score,
                    "reviewId": decision.decision_id,
                    "missingData": (list(decision.required_data or ()) if recorded_action == "RETURN" else []),
                }
            )
            approval_store = self._decision_workflow._approval_store
            approval_id = getattr(decision, "approval_id", None)
            approval = approval_store.get(approval_id) if approval_store is not None and approval_id else None
            approval_valid = approval is not None and approval.decision_id == decision.decision_id
            approvals.append(
                {
                    "id": approval_id if approval_valid else None,
                    "reviewId": decision.decision_id,
                    "candidateId": decision.candidate_site_id,
                    "title": decision.candidate_site_id,
                    "systemRecommendation": decision.recommendation.value,
                    "risk": risks,
                    "status": DECISION_APPROVAL_STATUS[recorded_action] if approval_valid and recorded_action else "pending" if approval_valid and decision.status in {DecisionStatus.SYSTEM_RECOMMENDED, DecisionStatus.PENDING_REVIEW} else None,
                    "approvalStatus": approval.approval_status if approval_valid else None,
                    "recordSource": "persisted" if approval_valid else "conflict" if approval_id else "unavailable",
                    "submittedAt": decision.created_at.isoformat(),
                    "decidedAt": (
                        approval.approved_at.isoformat()
                        if approval_valid and approval.approved_at is not None
                        else None
                    ),
                    "decidedBy": (
                        approval.approver_id
                        if approval_valid and approval.approved_at is not None
                        else None
                    ),
                }
            )
        reviews.sort(key=lambda row: (row["submittedAt"], row["id"]), reverse=True)
        pending = sum(row["pending"] for row in reviews)
        resources = {f"sitescore/decision/{row.decision_id}": row.decision_id for row in decisions}
        audit_rows = [
            {**event.to_dict(), "id": event.event_id, "targetType": "review",
             "targetId": resources[event.resource], "occurredAt": event.occurred_at.isoformat(),
             "actorName": event.actor, "actorRoleId": event.metadata.get("actor_role_id"),
             "correlationId": event.correlation_id}
            for event in self._decision_workflow.audit_log.list_events(
                tenant_id=self._decision_repository.tenant_id or None)
            if event.resource in resources and event.event_type == "sitescore.decision.v1"
        ]
        return {
            "source": "canonical",
            "reviews": reviews,
            "candidates": candidates,
            "approvals": approvals,
            "decisions": [decision.to_dict() for decision in decisions],
            "auditEvents": audit_rows,
            "decisionMapping": dict(DECISION_FINAL_LABEL),
            "counts": {
                "reviews": len(reviews),
                "pending": pending,
                "decided": sum(row["decided"] for row in reviews),
            },
            "correlationId": correlation_id,
        }

    def _retained_decision_report(self, decision: Any) -> tuple[SiteScoreReport | None, str]:
        """Read this decision's retained report without repairing or substituting it."""
        report = self._decision_repository.get_report(decision.decision_id)
        if report is None:
            return None, "unavailable"
        if (
            not isinstance(report, SiteScoreReport)
            or report.candidate_site_id != decision.candidate_site_id
            or report.report_id != decision.report_id
            or report.report_version != decision.report_version
            or report.model_version != decision.model_version
        ):
            return None, "conflict"
        return report, "available"

    @staticmethod
    def _recorded_number(value: Any, suffix: str = "") -> str | None:
        """Format an existing measurement without filling missing values."""
        if not _finite_measurement(value):
            return None
        return f"{value:g}{suffix}"

    def _decide_canonical(self, **kwargs):
        existing = self._decision_repository.get_decision(kwargs["review_id"])
        if existing is None:
            raise NetworkReviewNotFound("review not found")
        principal = kwargs.pop("principal", None)
        with self._sitescore_repository.report_guard(existing.candidate_site_id):
            candidate_service = self._candidate_workflow_service
            guard = (candidate_service._listing_repository.candidate_guard(existing.candidate_site_id)
                     if candidate_service is not None else nullcontext())
            with guard:
                current = self._decision_repository.get_decision(existing.decision_id)
                if principal is not None:
                    self.authorize_review_actor(current, principal)
                elif (
                    getattr(current, "submission_actor_verified", False) and current.submitted_by == kwargs["actor_name"]
                    or getattr(current, "created_by_verified", False) and current.created_by == kwargs["actor_name"]
                ):
                    raise NetworkReviewRoleError("proposer cannot approve own submission")
                return self._decide_canonical_unlocked(**kwargs)

    def authorize_review_actor(self, decision: Any, principal: Any, *,
                               module: Literal['operator_console', 'sitescore'] = 'operator_console') -> None:
        from shared.auth import Action

        from .network_review_submission import NetworkReviewSubmissionOwner
        NetworkReviewSubmissionOwner(self)._candidate(decision.candidate_site_id, principal, action=Action.APPROVE, module=module)
        # The type-level dependency does not evaluate the shared SoD hook.
        # Keep both verified proposers segregated: the current submitter and
        # the original creator retained across RETURN/resubmission. A legacy
        # body created_by or an automatic submit transition is not authority.
        if (
            getattr(decision, "submission_actor_verified", False) and decision.submitted_by == principal.subject_id
            or getattr(decision, "created_by_verified", False) and decision.created_by == principal.subject_id
        ):
            raise NetworkReviewRoleError("proposer cannot approve own submission")

    def read_native_review(self, *, review_id: str, principal: Any):
        from shared.auth import Action

        from .network_review_submission import NetworkReviewSubmissionOwner
        self._require_canonical_dependencies()
        decision = self._decision_repository.get_decision(review_id)
        if decision is None:
            raise NetworkReviewNotFound("review not found")
        NetworkReviewSubmissionOwner(self)._candidate(decision.candidate_site_id, principal, action=Action.VIEW, module='sitescore')
        return decision.to_dict()

    def decide_native_review(self, *, review_id: str, action: str, reason: str, principal: Any,
                             expected_version: int | None, correlation_id: str | None):
        from shared.workflow.sitescore import DecisionAction
        existing = self._decision_repository.get_decision(review_id)
        if existing is None:
            raise NetworkReviewNotFound("review not found")
        with self._sitescore_repository.report_guard(existing.candidate_site_id), self._candidate_workflow_service._listing_repository.candidate_guard(existing.candidate_site_id):
            existing = self._decision_repository.get_decision(review_id)
            self.authorize_review_actor(existing, principal, module='sitescore')
            if (isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1
                    or isinstance(existing.version, bool) or not isinstance(existing.version, int) or existing.version < 1
                    or existing.version != expected_version):
                raise NetworkReviewConflict("DECISION_VERSION_CONFLICT")
            _, availability = self._retained_decision_report(existing)
            if availability != "available":
                raise NetworkReviewConflict("IMMUTABLE_RETAINED_REPORT_REQUIRED")
            try:
                outcome = self._decision_workflow.decide(review_id, action=DecisionAction(action),
                    actor=principal.subject_id, reason=reason, correlation_id=correlation_id or "")
            except (SiteScoreDecisionError, ValueError) as exc:
                raise NetworkReviewConflict(str(exc)) from exc
            if outcome.decision.network_action is not None:
                self._candidate_workflow_service.record_review_decision(outcome.decision)
            return outcome.to_dict()

    def _decide_canonical_unlocked(
        self,
        *,
        review_id: str,
        action: str,
        reason: str,
        conditions: str,
        required_data: list[str],
        override_ack: bool,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        cache_key: tuple[str, ...],
        correlation_id: str | None,
        expected_target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_canonical_dependencies()
        if not isinstance(actor_name, str) or not actor_name.strip():
            raise NetworkReviewRoleError("canonical review requires the authenticated actor")
        if action not in DECISION_ACTIONS:
            raise NetworkReviewPolicyError(
                f"decision must be one of {', '.join(DECISION_ACTIONS)}"
            )
        if actor_role_id not in DECIDING_ROLE_IDS:
            raise NetworkReviewRoleError(
                f"role {actor_role_id!r} may prepare or submit but not decide network reviews"
            )
        if len(reason) < _MIN_REASON_LEN:
            raise NetworkReviewPolicyError(
                "決策原因必填（至少 10 字），寫入 Decision Log"
            )
        if action == "WAIT" and not conditions:
            raise NetworkReviewPolicyError("核准 WAIT 需填寫通過條件")
        if action == "RETURN" and not required_data:
            raise NetworkReviewPolicyError("退回修改需填寫需補資料")

        existing = self._decision_repository.get_decision(review_id)
        if existing is None:
            raise NetworkReviewNotFound(f"review {review_id} not found")
        if self._decision_repository.tenant_id and existing.tenant_id != self._decision_repository.tenant_id:
            raise NetworkReviewConflict("canonical decision tenant differs from the repository tenant")
        natural = _NATURAL_ACTION.get(existing.recommendation.value)
        override = action not in {natural, "RETURN"}
        if override and not override_ack:
            raise NetworkReviewPolicyError(
                "本決策覆寫系統建議，需勾選風險確認後才能送出"
            )
        actor = actor_name or actor_role_id
        # A canonical receipt belongs to the verified actor, tenant and review.
        # Payload reuse conflicts rather than starting a second RETURN revision.
        canonical_key = ("canonical-decide", existing.tenant_id, actor, review_id, idempotency_key or "")
        if idempotency_key and canonical_key in self._idempotency_cache:
            receipt = self._idempotency_cache[canonical_key]
            if receipt["requestFingerprint"] != cache_key[-1]:
                raise NetworkReviewConflict("idempotency key was reused with a different decision payload")
            return {**_copy(receipt["response"]), "idempotentReplay": True}
        # Matching unknown values do not establish a usable frozen identity.
        # Keep historical reads and authenticated receipt replay available;
        # this check applies before any fresh submit/decision transition.
        if not self._has_recorded_decision_identity(existing):
            raise NetworkReviewConflict(
                f"review {review_id} retained report identity is unavailable; decision was not changed"
            )
        # The replay fingerprint above includes the original read context. A fresh
        # transition additionally compares it under the report/candidate locks.
        if expected_target is not None:
            from .network_review_submission import candidate_digest
            candidate = self._candidate_workflow_service._listing_repository._store.get(
                self._candidate_workflow_service._listing_repository._CANDIDATES, existing.candidate_site_id)
            current_target = {"expectedCandidateId": existing.candidate_site_id,
                "expectedCandidateDigest": candidate_digest(candidate) if candidate is not None else None,
                "expectedDecisionVersion": existing.version, "expectedReportId": existing.report_id,
                "expectedReportVersion": existing.report_version, "expectedApprovalId": existing.approval_id}
            if (candidate is None or candidate.review_id != review_id or current_target != expected_target
                    or existing.status is not DecisionStatus.PENDING_REVIEW):
                raise NetworkReviewConflict("DECISION_CONTEXT_CONFLICT")
        _, report_availability = self._retained_decision_report(existing)
        if report_availability != "available":
            raise NetworkReviewConflict(
                f"review {review_id} retained report is {report_availability}; decision was not changed"
            )
        if self._candidate_workflow_service is not None:
            try:
                self._candidate_workflow_service.validate_review_candidate(existing)
            except RuntimeError as exc:
                raise NetworkReviewConflict(str(exc)) from exc
        try:
            if existing.status in {
                DecisionStatus.SYSTEM_RECOMMENDED,
                DecisionStatus.DRAFT,
            }:
                self._decision_workflow.submit_for_review(
                    review_id,
                    submitted_by=actor,
                    correlation_id=correlation_id or "",
                )
            outcome = self._decision_workflow.decide(
                review_id,
                action={
                    "GO": DecisionAction.APPROVE,
                    "WAIT": DecisionAction.APPROVE,
                    "RETURN": DecisionAction.REQUEST_REVISION,
                    "REJECT": DecisionAction.REJECT,
                }[action],
                actor=actor,
                reason=reason,
                correlation_id=correlation_id or "",
                network_action=action,
                conditions=conditions,
                required_data=required_data,
                override_ack=override_ack,
                actor_role_id=actor_role_id,
            )
        except SiteScoreDecisionError as exc:
            raise NetworkReviewConflict(str(exc)) from exc

        if self._candidate_workflow_service is not None:
            self._candidate_workflow_service.record_review_decision(outcome.decision)
        review_record = {
            "id": outcome.decision.decision_id, "candidateId": outcome.decision.candidate_site_id,
            "approvalId": outcome.decision.approval_id, "reportId": outcome.decision.report_id,
            "reportVersion": outcome.decision.report_version, "decisionVersion": outcome.decision.version,
            "tenantId": outcome.decision.tenant_id, "networkAction": action,
            "status": DECISION_REVIEW_STATUS[action], "canonicalDecisionStatus": outcome.decision.status.value,
            "conditions": conditions, "requiredData": _copy(required_data), "overrideAck": override_ack,
            "actor": actor, "actorRoleId": actor_role_id,
            "decidedAt": outcome.decision.history[-1].at.isoformat(), "auditId": outcome.audit_event_id,
        }
        self._reviews = [row for row in self._reviews if row["id"] != review_id] + [review_record]

        result = {
            "review": outcome.decision.to_dict(),
            "decision": outcome.to_dict(),
            "records": {
                "candidateId": outcome.decision.candidate_site_id,
                "reviewId": outcome.decision.decision_id,
                "decisionId": outcome.decision.decision_id,
                "auditId": outcome.audit_event_id,
                "approvalId": outcome.decision.approval_id,
            },
            "conditions": conditions,
            "requiredData": required_data,
            "override": override,
            "correlationId": correlation_id,
            "idempotentReplay": False,
        }
        if expected_target is not None:
            result["commandContext"] = {"actorSubjectId": actor, "tenantId": existing.tenant_id,
                "idempotencyKey": idempotency_key, "correlationId": correlation_id,
                "request": {**expected_target, "expectedActorSubjectId": actor, "expectedTenantId": existing.tenant_id,
                    "decision": action, "reason": reason, "conditions": conditions,
                    "requiredData": required_data, "overrideAck": override_ack}}
        if idempotency_key:
            self._idempotency_cache[canonical_key] = {"requestFingerprint": cache_key[-1], "response": _copy(result)}
        return result

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
