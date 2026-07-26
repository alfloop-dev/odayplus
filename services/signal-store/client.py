"""Signal store client contract for research-to-execution handoff.

This module intentionally defines only the boundary used by downstream work.
It does not choose a database, queue, schema validator, or runtime transport.

Acceptance coverage:
- The SignalStoreClient interface is documented below.
- EXAMPLE_SIGNAL_PAYLOAD gives P2 a concrete JSON schema seed.
- CONSUMER_ASSUMPTIONS lists the expectations P3/P4 consumers can build on.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, TypedDict, runtime_checkable

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | Mapping[str, "JsonValue"] | Sequence["JsonValue"]
SignalState = Literal["pending", "leased", "consumed", "rejected", "expired"]

# Producers validate before put_signal and workers validate after reading by
# resolving this same canonical artifact. Keep the URI/version synchronized with
# schema.json; major-version changes require a new schema and consumer opt-in.
SIGNAL_SCHEMA_VERSION = "1.0.0"
SIGNAL_SCHEMA_ID = "https://oday.plus/schemas/research/signal-envelope/1.0.0"
SIGNAL_SCHEMA_PATH = Path(__file__).parents[1] / "research" / "schema.json"


class SignalStoreError(Exception):
    """Base class for contract-level signal store failures."""


class SignalValidationError(SignalStoreError):
    """Raised when a signal envelope or payload fails contract validation."""


class SignalConflictError(SignalStoreError):
    """Raised when idempotency, lease, or state transitions conflict."""


class SignalNotFoundError(SignalStoreError):
    """Raised when a requested signal id is unknown or not visible."""


class SignalDomain(StrEnum):
    """Domains expected to produce executable signals."""

    SITESCORE = "sitescore"
    FORECAST = "forecast"
    INTERVENTION = "intervention"
    PRICING = "pricing"
    ADLIFT = "adlift"
    VALUATION = "valuation"
    NETPLAN = "netplan"
    MODEL_RELEASE = "model_release"


class SignalPriority(StrEnum):
    """Consumer scheduling hint, not an authorization bypass."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class SignalIntent(StrEnum):
    """Decision/execution intent carried by a signal."""

    SCORE_REQUESTED = "score_requested"
    DECISION_RECOMMENDED = "decision_recommended"
    EXECUTION_REQUESTED = "execution_requested"
    ROLLBACK_REQUESTED = "rollback_requested"
    MONITORING_ALERT = "monitoring_alert"
    MODEL_RELEASE_REQUESTED = "model_release_requested"


class SignalSubject(TypedDict):
    """Entity the signal is about."""

    entity_type: str
    entity_id: str


class SignalTrace(TypedDict, total=False):
    """Trace fields shared with API/event contracts."""

    correlation_id: str
    causation_id: str | None
    request_id: str | None
    source_event_id: str | None
    job_id: str | None


class SignalDecision(TypedDict, total=False):
    """Decision metadata when a signal can trigger execution."""

    decision_id: str
    policy_version: str
    actor_id: str
    approved_at: str
    decision_reason: str


class SignalEvidence(TypedDict, total=False):
    """Model, feature, and evidence lineage attached to a signal."""

    model_name: str
    model_version: str
    model_alias: str
    feature_view_version: str
    dataset_snapshot_id: str
    feature_snapshot_time: str
    prediction_origin_time: str
    prediction_horizon: str
    input_hash: str
    output_hash: str
    evidence_level: str


class SignalPayload(TypedDict, total=False):
    """Business payload seed for the Phase 2 JSON schema."""

    recommended_action: str
    recommendation_summary: str
    confidence: float
    score: float
    risk_flags: list[str]
    constraints: list[str]
    metrics: dict[str, float | int | str | None]
    decision: SignalDecision
    evidence: SignalEvidence
    details: Mapping[str, JsonValue]


class SignalEnvelope(TypedDict):
    """Persisted signal shape exchanged between producers and consumers."""

    signal_id: str
    signal_version: str
    signal_type: str
    domain: str
    intent: str
    priority: str
    subject: SignalSubject
    tenant_id: str
    idempotency_key: str
    produced_at: str
    effective_at: str | None
    expires_at: str | None
    producer: str
    trace: SignalTrace
    payload: SignalPayload


@dataclass(frozen=True, slots=True)
class StoredSignal:
    """Signal plus storage metadata returned by SignalStoreClient."""

    envelope: SignalEnvelope
    state: SignalState
    stored_at: datetime
    updated_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    consumed_at: datetime | None = None
    rejection_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SignalQuery:
    """Read-side filter for polling or inspection."""

    tenant_id: str
    states: tuple[SignalState, ...] = ("pending",)
    domains: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    subject: SignalSubject | None = None
    priority_at_least: SignalPriority | None = None
    limit: int = 100
    page_token: str | None = None


@dataclass(frozen=True, slots=True)
class SignalPage:
    """Paged list response."""

    items: tuple[StoredSignal, ...]
    next_page_token: str | None = None


@runtime_checkable
class SignalStoreClient(Protocol):
    """Contract for durable, idempotent signal handoff.

    Producers call put_signal once per business signal using a stable
    idempotency_key. Consumers call lease_pending before side effects and must
    finish with mark_consumed or reject_signal. Implementations must persist
    the full envelope before acknowledging the producer.
    """

    def put_signal(self, envelope: SignalEnvelope) -> StoredSignal:
        """Persist a signal or return the existing row for its idempotency key.

        Raises:
            SignalValidationError: envelope or payload violates the contract.
            SignalConflictError: idempotency key exists with a different body.
        """

    def get_signal(self, signal_id: str, *, tenant_id: str) -> StoredSignal:
        """Return a single signal visible to tenant_id.

        Raises:
            SignalNotFoundError: signal_id is absent or outside tenant scope.
        """

    def list_signals(self, query: SignalQuery) -> SignalPage:
        """Return stored signals matching query with stable cursor pagination."""

    def lease_pending(
        self,
        *,
        tenant_id: str,
        consumer_id: str,
        limit: int = 10,
        lease_seconds: int = 300,
        domains: Iterable[str] = (),
        intents: Iterable[str] = (),
    ) -> tuple[StoredSignal, ...]:
        """Atomically lease pending signals for one consumer.

        Leased signals must not be delivered to another consumer until the
        lease expires, is marked consumed, or is rejected.
        """

    def mark_consumed(
        self,
        signal_id: str,
        *,
        tenant_id: str,
        consumer_id: str,
        consumed_at: datetime,
        result_ref: str | None = None,
    ) -> StoredSignal:
        """Finalize successful processing for the active lease holder.

        Raises:
            SignalConflictError: signal is not leased to consumer_id.
        """

    def reject_signal(
        self,
        signal_id: str,
        *,
        tenant_id: str,
        consumer_id: str,
        reason: str,
        retryable: bool,
    ) -> StoredSignal:
        """Reject a signal after failed processing.

        retryable=True lets the implementation return it to pending state after
        its retry policy; retryable=False marks it rejected for operator review.
        """


EXAMPLE_SIGNAL_PAYLOAD: SignalEnvelope = {
    "signal_id": "sig_01J2SITE000000000000000001",
    "signal_version": "1.0.0",
    "signal_type": "sitescore.decision_recommended.v1",
    "domain": SignalDomain.SITESCORE.value,
    "intent": SignalIntent.DECISION_RECOMMENDED.value,
    "priority": SignalPriority.HIGH.value,
    "subject": {
        "entity_type": "candidate_site",
        "entity_id": "cs_123",
    },
    "tenant_id": "tenant_oday",
    "idempotency_key": "sitescore:ssr_123:decision_recommended:v1",
    "produced_at": "2026-06-26T10:00:00+08:00",
    "effective_at": "2026-06-26T10:00:00+08:00",
    "expires_at": "2026-06-27T10:00:00+08:00",
    "producer": "sitescore-score-worker",
    "trace": {
        "correlation_id": "corr_01J2SITE",
        "causation_id": "evt_01J2REQUESTED",
        "source_event_id": "evt_01J2SCORE_COMPLETED",
        "job_id": "job_01J2SCORE",
    },
    "payload": {
        "recommended_action": "advance_to_site_approval",
        "recommendation_summary": "Candidate site is above expansion threshold.",
        "confidence": 0.82,
        "score": 91.4,
        "risk_flags": ["lease_terms_pending"],
        "constraints": ["requires_human_approval", "do_not_execute_directly"],
        "metrics": {
            "revenue_p50_monthly": 1850000,
            "payback_months_p50": 18,
            "brand_transfer_confidence": 0.76,
        },
        "decision": {
            "decision_id": "dec_01J2SITE",
            "policy_version": "site-approval-policy@2026-06-26",
            "actor_id": "system:sitescore-policy",
            "approved_at": "2026-06-26T10:00:00+08:00",
            "decision_reason": "score_above_threshold_with_manual_approval_required",
        },
        "evidence": {
            "model_name": "site_revenue_path_predictor",
            "model_version": "2026.06.0",
            "model_alias": "champion",
            "feature_view_version": "candidate_site_view@2026.06.26",
            "dataset_snapshot_id": "dss_20260626_sitescore",
            "feature_snapshot_time": "2026-06-26T08:00:00+08:00",
            "prediction_origin_time": "2026-06-26T10:00:00+08:00",
            "prediction_horizon": "M12",
            "input_hash": "sha256:input-example",
            "output_hash": "sha256:output-example",
            "evidence_level": "model_validated",
        },
        "details": {
            "candidate_site_id": "cs_123",
            "site_score_run_id": "ssr_123",
            "next_consumer": "control-plane-router",
        },
    },
}


CONSUMER_ASSUMPTIONS: tuple[str, ...] = (
    "Producers and consumers validate against SIGNAL_SCHEMA_PATH and require its "
    "$id to equal SIGNAL_SCHEMA_ID.",
    "Signals are tenant-scoped; consumers must pass tenant_id for every read or state change.",
    "idempotency_key is stable for a business signal and must reject body mismatches.",
    "signal_type follows '<domain>.<event_or_intent>.v<major>' and is versioned independently.",
    "Consumers must reject unsupported signal_version or signal_type major versions.",
    "produced_at, effective_at, expires_at, and evidence timestamps are ISO-8601 strings with "
    "an explicit UTC offset; stores compare normalized instants rather than source offsets.",
    "Consumers must not act before effective_at and must reject or expire signals after expires_at.",
    "Prediction, decision, execution, and outcome remain separate; signals may request execution "
    "but do not bypass approval policy.",
    "Consumers must lease before side effects and then mark_consumed or reject_signal.",
    "Delivery is at least once; lease expiry can redeliver a signal, so side effects must use "
    "signal_id or idempotency_key for deduplication.",
    "Only the active lease owner may mark a signal consumed or rejected.",
    "Polling order and page tokens are store-defined; consumers must not infer priority or "
    "chronology from signal_id or page_token.",
    "Unknown additive payload fields must be ignored by consumers and preserved by stores.",
    "Version 1.x compatibility is additive within payload: existing fields keep their meaning "
    "and required envelope fields cannot be removed; breaking changes require signal_version "
    "2.x, a new schema $id, and explicit consumer opt-in.",
    "PII should be referenced by entity ids, not copied into the signal payload.",
)


__all__ = [
    "CONSUMER_ASSUMPTIONS",
    "EXAMPLE_SIGNAL_PAYLOAD",
    "JsonValue",
    "SIGNAL_SCHEMA_ID",
    "SIGNAL_SCHEMA_PATH",
    "SIGNAL_SCHEMA_VERSION",
    "SignalConflictError",
    "SignalDecision",
    "SignalDomain",
    "SignalEnvelope",
    "SignalEvidence",
    "SignalIntent",
    "SignalNotFoundError",
    "SignalPage",
    "SignalPayload",
    "SignalPriority",
    "SignalQuery",
    "SignalState",
    "SignalStoreClient",
    "SignalStoreError",
    "SignalSubject",
    "SignalTrace",
    "SignalValidationError",
    "StoredSignal",
]
