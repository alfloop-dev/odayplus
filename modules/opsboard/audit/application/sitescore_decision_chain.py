"""Safe recorded QA03 decision detail from existing canonical SiteScore owners.

This is a read projection, not model/source readiness or an outcome producer.
RealizedSite proves the canonical candidate realization only, never physical
opening, paid revenue or a business outcome. Missing slots stay explicit.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime

from modules.opsboard.audit.application.sitescore_evidence import (
    SiteScoreCanonicalEvidenceResolver,
    SiteScoreEvidenceSelector,
    SiteScoreEvidenceUnavailable,
)
from modules.sitescore.domain.scoring import Interval, SiteScoreReport
from shared.audit.integrity import sha256_hex
from shared.audit.policy import mask_email, mask_text
from shared.auth import Principal
from shared.infrastructure.persistence.repositories import DurableRealizedSiteStore
from shared.workflow.sitescore import DecisionStatus, RealizedSite


def _missing(name: str) -> SiteScoreEvidenceUnavailable:
    return SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:" + name)


def _finite(value) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _time(value) -> str:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise _missing("recorded_chain_time")
    return value.isoformat()


def _interval(value) -> dict:
    if not isinstance(value, Interval) or not all(_finite(v) for v in (value.p10, value.p50, value.p90)):
        raise _missing("recorded_prediction_shape")
    if not value.p10 <= value.p50 <= value.p90:
        raise _missing("recorded_prediction_shape")
    return {"p10": value.p10, "p50": value.p50, "p90": value.p90}


def _actor(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _missing("recorded_chain_actor")
    return mask_email(value) if "@" in value else mask_text(value)


@dataclass(frozen=True)
class RecordedSiteScoreDecisionChain:
    """Immutable allowlist bytes only; never an opaque native report object."""

    source_digest: str
    content: bytes

    def document(self) -> dict:
        # Contents are constructed and checked by the canonical owner below.
        # Rendering must still reject an internal value changed after creation.
        try:
            value = json.loads(self.content)
        except (TypeError, ValueError) as exc:
            raise _missing("recorded_chain_shape") from exc
        if (not isinstance(value, dict) or set(value) != {"schema", "source_digest", "prediction",
                "recommendation", "human_decision", "execution", "outcome", "coverage", "limits"}
                or value.get("schema") != "sitescore.recorded-decision-chain.v1"
                or value.get("source_digest") != self.source_digest
                or any(not isinstance(value.get(name), dict) for name in (
                    "prediction", "recommendation", "human_decision", "execution", "outcome", "coverage", "limits"))):
            raise _missing("recorded_chain_shape")
        _validate_document(value)
        return value


def _validate_document(value):
    def keys(row, expected):
        if not isinstance(row, dict) or set(row) != set(expected):
            raise _missing("recorded_chain_shape")
    def text(item):
        if not isinstance(item, str) or not item.strip():
            raise _missing("recorded_chain_shape")
    def masked_actor(item):
        text(item)
        if "@" in item:
            local, _, domain = item.partition("@")
            if not domain or (len(local) > 1 and set(local[1:]) - {"*"}):
                raise _missing("recorded_chain_shape")
        elif set(item) - {"*"}:
            raise _missing("recorded_chain_shape")
    def digest(item):
        if not isinstance(item, str) or len(item) != 64 or any(c not in "0123456789abcdef" for c in item):
            raise _missing("recorded_chain_shape")
    def instant(item):
        try:
            if not isinstance(item, str) or datetime.fromisoformat(item).utcoffset() is None:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise _missing("recorded_chain_shape") from exc
    digest(value["source_digest"])
    prediction = value["prediction"]
    keys(prediction, ("status", "report_id", "report_version", "report_sha256", "intervals",
        "payback_period", "payback_p50_months", "confidence", "confidence_status", "model_reference",
        "feature_reference", "feature_snapshot_time", "prediction_origin_time", "scored_at", "source_snapshot_ids"))
    if (prediction["status"] != "recorded" or type(prediction["report_version"]) is not int
            or prediction["report_version"] < 1 or not _finite(prediction["payback_p50_months"])):
        raise _missing("recorded_chain_shape")
    for field in ("report_id", "model_reference", "feature_reference"):
        text(prediction[field])
    digest(prediction["report_sha256"])
    for field in ("feature_snapshot_time", "prediction_origin_time", "scored_at"):
        instant(prediction[field])
    keys(prediction["intervals"], ("m1", "m3", "m6", "m12"))
    for interval in (*prediction["intervals"].values(), prediction["payback_period"]):
        keys(interval, ("p10", "p50", "p90"))
        if not all(_finite(v) for v in interval.values()) or not interval["p10"] <= interval["p50"] <= interval["p90"]:
            raise _missing("recorded_chain_shape")
    snapshots = prediction["source_snapshot_ids"]
    if not isinstance(snapshots, list) or any(not isinstance(i, str) or not i.strip() for i in snapshots):
        raise _missing("recorded_chain_shape")
    if prediction["confidence_status"] not in ("measured", "unmeasured", "legacy_unknown"):
        raise _missing("recorded_chain_shape")
    if prediction["confidence_status"] == "measured":
        if not _finite(prediction["confidence"]) or not 0 <= prediction["confidence"] <= 1:
            raise _missing("recorded_chain_shape")
    elif prediction["confidence"] is not None:
        raise _missing("recorded_chain_shape")
    recommendation = value["recommendation"]
    keys(recommendation, ("status", "recorded_recommendation", "recorded_network_action"))
    if (recommendation["status"] != "recorded" or recommendation["recorded_recommendation"] not in (
            "GO", "WAIT", "REJECT", "INVESTIGATE")
            or recommendation["recorded_network_action"] not in (None, "GO", "WAIT", "REJECT", "RETURN")):
        raise _missing("recorded_chain_shape")
    human = value["human_decision"]
    keys(human, ("status", "decision_id", "decision_version", "approval_id", "approval_status", "actor",
        "decided_at", "history", "policy_reference", "reason_masked", "reason_sha256", "actor_fields_masked"))
    if (human["status"] != "recorded" or type(human["decision_version"]) is not int
            or human["decision_version"] < 1 or human["actor_fields_masked"] is not True
            or human["approval_status"] not in ("approved", "rejected", "returned")
            or not isinstance(human["history"], list) or not human["history"]):
        raise _missing("recorded_chain_shape")
    for field in ("decision_id", "approval_id", "actor", "policy_reference"):
        text(human[field])
    masked_actor(human["actor"])
    if not isinstance(human["reason_masked"], str) or set(human["reason_masked"]) - {"*"}:
        raise _missing("recorded_chain_shape")
    digest(human["reason_sha256"])
    instant(human["decided_at"])
    for row in human["history"]:
        keys(row, ("version", "action", "from_status", "to_status", "actor", "reason_masked",
            "reason_sha256", "occurred_at", "approval_id", "correlation_id"))
        if (type(row["version"]) is not int or row["version"] < 1
                or row["from_status"] not in tuple(status.value for status in DecisionStatus)
                or row["to_status"] not in tuple(status.value for status in DecisionStatus)):
            raise _missing("recorded_chain_shape")
        text(row["action"])
        masked_actor(row["actor"])
        digest(row["reason_sha256"])
        instant(row["occurred_at"])
        if not isinstance(row["reason_masked"], str) or set(row["reason_masked"]) - {"*"}:
            raise _missing("recorded_chain_shape")
        for field in ("approval_id", "correlation_id"):
            if row[field] is not None:
                text(row[field])
    execution = value["execution"]
    keys(execution, ("status", "record", "reason"))
    if execution["status"] == "recorded":
        row = execution["record"]
        keys(row, ("candidate_id", "decision_id", "recorded_at", "site_status", "baseline_trajectory", "kind"))
        if execution["reason"] is not None or row["kind"] != "canonical_candidate_realization":
            raise _missing("recorded_chain_shape")
        for field in ("candidate_id", "decision_id", "site_status"):
            text(row[field])
        instant(row["recorded_at"])
        keys(row["baseline_trajectory"], ("m1", "m3", "m6", "m12"))
        if not all(_finite(v) for v in row["baseline_trajectory"].values()):
            raise _missing("recorded_chain_shape")
    elif execution["status"] in ("unavailable", "not_applicable"):
        if execution["record"] is not None:
            raise _missing("recorded_chain_shape")
        allowed_reasons = (("DECISION_DID_NOT_AUTHORIZE_NEW_CANDIDATE_REALIZATION",)
            if execution["status"] == "not_applicable" else (
                "CANONICAL_REALIZATION_OWNER_NOT_BOUND", "NO_MATCHING_CANONICAL_REALIZATION_RECORD",
                "REALIZATION_OWNER_HAS_DIFFERENT_DECISION"))
        if execution["reason"] not in allowed_reasons:
            raise _missing("recorded_chain_shape")
    else:
        raise _missing("recorded_chain_shape")
    if value["outcome"] != {"status": "unavailable", "record": None,
            "reason": "NO_CANONICAL_DECISION_BOUND_SITESCORE_OUTCOME_OWNER"}:
        raise _missing("recorded_chain_shape")
    expected_missing = ["outcome"]
    if not snapshots:
        expected_missing.append("input_snapshot_references")
    if execution["status"] == "unavailable":
        expected_missing.append("execution")
    if value["coverage"] != {"state": "degraded", "complete": False, "missing": expected_missing}:
        raise _missing("recorded_chain_shape")
    if value["limits"] != {"model_readiness_claimed": False, "source_content_included": False,
            "physical_opening_or_revenue_outcome_claimed": False,
            "actor_and_reason_masked_under_existing_shared_policy": True}:
        raise _missing("recorded_chain_shape")


class SiteScoreRecordedDecisionChainReader:
    def __init__(self, source_owner: SiteScoreCanonicalEvidenceResolver,
                 realization_owner: DurableRealizedSiteStore | None = None) -> None:
        if not isinstance(source_owner, SiteScoreCanonicalEvidenceResolver):
            raise _missing("canonical_source_owner")
        self.source_owner, self.realization_owner = source_owner, realization_owner

    def _execution(self, tenant, engine, source, decision, report):
        # A canonical rejection, WAIT or reconsideration did not authorize a
        # new candidate realization. This is not a fabricated successful job.
        expected = (decision.status == DecisionStatus.APPROVED
            and decision.network_action != "WAIT" and decision.previous_decision_id is None)
        if not expected:
            return {"status": "not_applicable", "record": None,
                "reason": "DECISION_DID_NOT_AUTHORIZE_NEW_CANDIDATE_REALIZATION"}
        owner = self.realization_owner
        if owner is None:
            return {"status": "unavailable", "record": None, "reason": "CANONICAL_REALIZATION_OWNER_NOT_BOUND"}
        store = getattr(owner, "_store", None)
        if (not isinstance(owner, DurableRealizedSiteStore) or owner.tenant_id != tenant
                or getattr(store, "engine", None) is not engine):
            raise _missing("canonical_realization_owner_binding")
        row = owner.get(source.candidate_id)
        if row is None:
            return {"status": "unavailable", "record": None, "reason": "NO_MATCHING_CANONICAL_REALIZATION_RECORD"}
        if not isinstance(row, RealizedSite):
            raise _missing("canonical_realization_shape")
        if row.decision_id != source.decision_id:
            # Keyed-by-candidate owner may now hold another round. Do not use
            # it for this decision or claim the earlier execution never ran.
            return {"status": "unavailable", "record": None, "reason": "REALIZATION_OWNER_HAS_DIFFERENT_DECISION"}
        baseline = {name: report.horizons[name].p50 for name in ("m1", "m3", "m6", "m12")}
        if (row.candidate_site_id != source.candidate_id or row.baseline_trajectory != baseline
                or not isinstance(row.site_status, str) or not row.site_status.strip()
                or not isinstance(row.realized_at, datetime) or row.realized_at.utcoffset() is None
                or not decision.history[-1].at <= row.realized_at <= source.events[-1].occurred_at):
            raise _missing("canonical_realization_binding")
        return {"status": "recorded", "reason": None, "record": {
            "candidate_id": row.candidate_site_id, "decision_id": row.decision_id,
            "recorded_at": _time(row.realized_at), "site_status": row.site_status,
            "baseline_trajectory": baseline, "kind": "canonical_candidate_realization"}}

    def read(self, principal: Principal, selector: SiteScoreEvidenceSelector) -> RecordedSiteScoreDecisionChain:
        source = self.source_owner.resolve_source(principal, selector)
        engine = self.source_owner._binding(principal.tenant_id)
        with engine.transaction():
            if self.source_owner.resolve_source(principal, selector) != source:
                raise _missing("canonical_source_changed")
            decision = self.source_owner.decisions.get_decision(source.decision_id)
            report = self.source_owner.decisions.get_report(source.decision_id)
            if not isinstance(report, SiteScoreReport) or sha256_hex(report.to_dict()) != source.report_sha256:
                raise _missing("recorded_chain_report_binding")
            intervals = {key: _interval(report.horizons[key]) for key in ("m1", "m3", "m6", "m12")}
            payback = _interval(report.payback_period)
            # Abstention/legacy unknown is absence, never measured 0 or 100%.
            confidence = report.confidence if report.confidence_status == "measured" else None
            if confidence is not None and (not _finite(confidence) or not 0 <= confidence <= 1):
                raise _missing("recorded_prediction_confidence")
            if not _finite(report.payback_p50_months):
                raise _missing("recorded_prediction_shape")
            prediction = {"status": "recorded", "report_id": source.report_id,
                "report_version": source.report_version, "report_sha256": source.report_sha256,
                "intervals": intervals, "payback_period": payback,
                "payback_p50_months": report.payback_p50_months, "confidence": confidence,
                "confidence_status": report.confidence_status, "model_reference": source.recorded_model_reference,
                "feature_reference": source.recorded_feature_reference,
                "feature_snapshot_time": _time(report.feature_snapshot_time),
                "prediction_origin_time": _time(report.prediction_origin_time), "scored_at": _time(report.scored_at),
                "source_snapshot_ids": list(source.source_snapshot_ids)}
            history = []
            for transition in decision.history:
                history.append({"version": transition.version, "action": transition.action,
                    "from_status": transition.from_status.value, "to_status": transition.to_status.value,
                    "actor": _actor(transition.actor), "reason_masked": mask_text(transition.reason),
                    "reason_sha256": sha256_hex(transition.reason), "occurred_at": _time(transition.at),
                    "approval_id": transition.approval_id, "correlation_id": transition.correlation_id})
            human = {"status": "recorded", "decision_id": source.decision_id,
                "decision_version": source.decision_version, "approval_id": source.approval_id,
                "approval_status": source.approval_status, "actor": _actor(source.decision_actor),
                "decided_at": _time(source.decided_at), "history": history,
                "policy_reference": source.recorded_policy_reference,
                "reason_masked": mask_text(decision.history[-1].reason),
                "reason_sha256": source.rationale_sha256, "actor_fields_masked": True}
            execution = self._execution(principal.tenant_id, engine, source, decision, report)
            # No decision-bound SiteScore outcome owner is available in this
            # implementation. Store-level Forecast observations and another
            # domain's outcomes cannot be joined by convenience.
            outcome = {"status": "unavailable", "record": None,
                "reason": "NO_CANONICAL_DECISION_BOUND_SITESCORE_OUTCOME_OWNER"}
            missing = ["outcome"]
            if not source.source_snapshot_ids:
                missing.append("input_snapshot_references")
            if execution["status"] == "unavailable":
                missing.append("execution")
            document = {"schema": "sitescore.recorded-decision-chain.v1", "source_digest": source.source_digest,
                "prediction": prediction, "recommendation": {"status": "recorded",
                    "recorded_recommendation": report.recommendation.value,
                    "recorded_network_action": source.recorded_network_action},
                "human_decision": human, "execution": execution, "outcome": outcome,
                "coverage": {"state": "degraded", "complete": False, "missing": missing},
                "limits": {"model_readiness_claimed": False, "source_content_included": False,
                    "physical_opening_or_revenue_outcome_claimed": False,
                    "actor_and_reason_masked_under_existing_shared_policy": True}}
            if (self.source_owner.resolve_source(principal, selector) != source
                    or self.source_owner.decisions.get_report(source.decision_id) != report
                    or self.source_owner.decisions.get_decision(source.decision_id) != decision
                    or self._execution(principal.tenant_id, engine, source, decision, report) != execution):
                raise _missing("canonical_source_changed")
            content = json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False,
                separators=(",", ":")).encode("utf-8")
            result = RecordedSiteScoreDecisionChain(source.source_digest, content)
            result.document()
            return result
