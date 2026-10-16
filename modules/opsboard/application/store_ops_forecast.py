"""Read the saved forecast for one already authorized Store Ops alert issue.

This projection does not execute a model, resolve today's model alias or policy,
choose the latest forecast, or save copied predictions into workflow state.
Model metadata is recorded execution evidence, not a new artifact verification.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from modules.forecastops.domain.forecasting import (
    Alert,
    ForecastBand,
    ForecastOutput,
    InterventionHandoff,
    _stable_id,
)
from modules.opsboard.application.store_ops import StoreOpsLiveDataUnavailable
from shared.domain.models import ForecastOutput as CanonicalForecastOutput
from shared.domain.models import Prediction, PredictionRun
from shared.infrastructure.persistence.repositories import DurableForecastOpsRepository

_WEEKS = (4, 8, 12, 24)
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _finite(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _aware(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _band(value: Any) -> bool:
    return (
        isinstance(value, ForecastBand)
        and all(_finite(item) for item in (value.p10, value.p50, value.p90))
        and value.p10 <= value.p50 <= value.p90
    )


def _binding(output: ForecastOutput) -> dict[str, Any] | None:
    metadata = output.model_metadata
    if (
        not isinstance(metadata, dict)
        or metadata.get("model_service") != "forecastops"
        or metadata.get("model_id") != output.model_version
        or metadata.get("model_name") != output.model_name
        or metadata.get("model_engine") != output.engine_name
        or not _text(output.engine_name)
        or output.engine_name == "baseline"
        or not all(_text(metadata.get(key)) for key in (
            "model_id", "model_name", "model_version", "model_stage",
            "dataset_snapshot_id", "feature_schema_version", "label_version",
        ))
        or not isinstance(metadata.get("artifact_sha256"), str)
        or _SHA256.fullmatch(metadata["artifact_sha256"]) is None
        or type(metadata.get("prediction_count")) is not int
        or metadata["prediction_count"] != len(_WEEKS)
        or any(metadata.get(key) is not None and not _text(metadata[key]) for key in (
            "registry_version", "source_run_id", "mlflow_run_id", "model_approved_by",
            "model_approved_at", "binding_resolved_at",
        ))
    ):
        return None
    # Explicit public execution fields only; never expose an artifact URI,
    # nested estimator payload, credentials or caller-supplied metadata blob.
    return {key: metadata.get(key) for key in (
        "model_service", "model_id", "model_name", "model_version", "model_stage",
        "dataset_snapshot_id", "feature_schema_version", "label_version",
        "artifact_sha256", "model_engine", "registry_version", "source_run_id",
        "mlflow_run_id", "model_approved_by", "model_approved_at", "binding_resolved_at",
    )}


def _matches_alert(alert: Alert, output: ForecastOutput) -> bool:
    evidence = alert.evidence_json
    return (
        isinstance(evidence, dict)
        and evidence.get("policy_id") == alert.policy_id
        and evidence.get("policy_version") == alert.policy_version
        and evidence.get("policy_version_id") == alert.policy_version_id
        and all(evidence.get(key) == getattr(output, field) for key, field in (
            ("actual_revenue", "actual_revenue"), ("forecast_p50", "p50"),
            ("sitescore_baseline_p50", "sitescore_baseline_p50"),
            ("sitescore_gap_ratio", "sitescore_gap_ratio"),
            ("trajectory_class", "trajectory_class"), ("engine_name", "engine_name"),
            ("model_name", "model_name"), ("model_version", "model_version"),
        ))
    )


def _joined_output(
    output: ForecastOutput, canonical: Any, run: Any, predictions: Any,
) -> bool:
    bands = [getattr(output, f"w{weeks}") for weeks in _WEEKS]
    if (
        type(output.horizon_days) is not int
        or output.horizon_days not in tuple(weeks * 7 for weeks in _WEEKS)
        or not all(_band(item) for item in bands)
        or not all(_finite(getattr(output, key)) for key in (
            "p10", "p50", "p90", "actual_revenue", "sitescore_gap_ratio",
            "turning_point_probability",
        ))
        or output.sitescore_baseline_p50 is not None and not _finite(output.sitescore_baseline_p50)
        or not all(_aware(value) for value in (output.prediction_origin_time, output.scored_at))
        or type(output.forecast_version) is not int or output.forecast_version < 1
        or not _text(output.feature_version) or not _text(output.policy_version)
        or not isinstance(output.source_snapshot_ids, tuple) or not output.source_snapshot_ids
        or not all(_text(value) for value in output.source_snapshot_ids)
        or output.data_quality_score is not None and not _finite(output.data_quality_score)
        or output.data_staleness_days is not None and (
            type(output.data_staleness_days) is not int or output.data_staleness_days < 0
        )
        or not isinstance(canonical, CanonicalForecastOutput)
        or not all(getattr(canonical, key) == getattr(output, key) for key in (
            "forecast_output_id", "store_id", "prediction_run_id", "horizon_days",
            "target_metric", "p10", "p50", "p90", "trajectory_class",
            "turning_point_probability", "sitescore_gap_ratio",
        ))
        or not isinstance(run, PredictionRun)
        or run.prediction_run_id != output.prediction_run_id
        or run.model_version_id != output.model_version or run.run_status != "succeeded"
        or run.prediction_origin_time != output.prediction_origin_time
        or not _aware(run.feature_snapshot_time)
        or run.feature_snapshot_time > output.prediction_origin_time
        or not isinstance(run.prediction_horizon, str)
        or f"w{output.horizon_days // 7}" not in run.prediction_horizon.split(",")
        or not isinstance(predictions, list)
    ):
        return False
    selected = getattr(output, f"w{output.horizon_days // 7}")
    if (output.p10, output.p50, output.p90) != (selected.p10, selected.p50, selected.p90):
        return False
    from uuid import NAMESPACE_URL, uuid5

    expected_id = "prediction-" + str(uuid5(
        NAMESPACE_URL, f"{output.prediction_run_id}:{output.store_id}:w{output.horizon_days // 7}",
    ))
    matches = [row for row in predictions if isinstance(row, Prediction)
               and row.prediction_id == expected_id]
    if len(matches) != 1:
        return False
    prediction = matches[0]
    return (
        prediction.prediction_run_id == output.prediction_run_id
        and prediction.entity_type == "store" and prediction.entity_id == output.store_id
        and prediction.target_name == "revenue" and prediction.unit == "TWD"
        and (prediction.p10_value, prediction.p50_value, prediction.p90_value)
        == (output.p10, output.p50, output.p90)
        and prediction.explanation_json == {
            "engine_name": output.engine_name, "model_name": output.model_name,
            "model_version": output.model_version, "model_metadata": output.model_metadata,
        }
    )


def _quality_at_run(alert: Alert, output: ForecastOutput, run: PredictionRun) -> dict[str, Any]:
    """Project the saved decision, never rerun today's policy or infer freshness."""
    unavailable = {"state": "unavailable", "reason": "RECORDED_QUALITY_GUARD_UNAVAILABLE", "data": None}
    evidence = alert.evidence_json
    parameters = evidence.get("policy_parameters")
    guard = evidence.get("data_quality_guard")
    policy_guard = parameters.get("data_quality_guard") if isinstance(parameters, dict) else None
    if not isinstance(guard, dict) or not isinstance(policy_guard, dict):
        return unavailable
    try:
        # Match the original producer's saved policy normalization. This reads
        # that exact policy snapshot, not a current registry/default threshold.
        maximum = int(policy_guard["max_staleness_days"])
        action = str(policy_guard.get("on_violation") or "").strip().upper()
    except (KeyError, TypeError, ValueError, OverflowError):
        return unavailable
    if (
        maximum < 0 or action not in {"SUPPRESS_HIGH_CONFIDENCE", "SUPPRESS_ALERT"}
        or type(guard.get("max_staleness_days")) is not int
        or guard["max_staleness_days"] != maximum
        or "staleness_days" not in guard
        or guard["staleness_days"] is not None and (
            type(guard["staleness_days"]) is not int or guard["staleness_days"] < 0
        )
        or guard["staleness_days"] != output.data_staleness_days
        or type(guard.get("violated")) is not bool
        or guard.get("on_violation") != action
        or evidence.get("threshold_alert_level") not in ("red", "orange", "yellow", "green")
        or not _aware(alert.opened_at) or not _aware(run.feature_snapshot_time)
        or getattr(alert.alert_level, "value", None) not in ("red", "orange", "yellow", "green")
        or not _text(alert.alert_reason_code)
    ):
        return unavailable
    # Verify internal consistency of this saved decision only. A contradiction
    # is unavailable evidence; never replace it with a newly computed decision.
    threshold = evidence["threshold_alert_level"]
    if guard["violated"] != (guard["staleness_days"] is None or guard["staleness_days"] > maximum):
        return unavailable
    level = threshold
    reason = "within_expected_band" if threshold == "green" else "sitescore_gap"
    if guard["violated"] and action == "SUPPRESS_ALERT":
        level, reason = "green", "data_quality_stale"
    elif guard["violated"] and action == "SUPPRESS_HIGH_CONFIDENCE" and threshold == "red":
        level, reason = "orange", "data_quality_stale"
    if alert.alert_level.value != level or alert.alert_reason_code != reason:
        return unavailable
    return {"state": "recorded", "reason": None, "data": {
        "alertId": alert.alert_id, "forecastOutputId": output.forecast_output_id,
        "predictionRunId": run.prediction_run_id,
        "policy": {"id": alert.policy_id, "version": alert.policy_version,
                   "versionId": alert.policy_version_id},
        "evaluatedAt": alert.opened_at.isoformat(),
        "featureSnapshotTime": run.feature_snapshot_time.isoformat(),
        "maxStalenessDays": maximum, "stalenessDays": guard["staleness_days"],
        "violated": guard["violated"], "onViolation": action,
        "actualAlertLevel": alert.alert_level.value,
        "thresholdAlertLevel": evidence["threshold_alert_level"],
        "alertReasonCode": alert.alert_reason_code,
    }}


def _saved_alert_evidence(alert: Alert) -> dict[str, Any]:
    unavailable = {"state": "unavailable", "reason": "RECORDED_ALERT_EVIDENCE_UNAVAILABLE", "data": None}
    if (getattr(alert.alert_level, "value", None) not in ("red", "orange", "yellow", "green")
            or not _text(alert.alert_reason_code) or not _aware(alert.opened_at)):
        return unavailable
    return {"state": "recorded", "reason": None, "data": {
        "alertId": alert.alert_id, "level": alert.alert_level.value,
        "reasonCode": alert.alert_reason_code, "openedAt": alert.opened_at.isoformat(),
    }}


def _saved_recommendation(handoff: Any, alert: Alert, output: ForecastOutput) -> dict[str, Any]:
    """Read the original policy recommendation, never infer a cause or execute it."""
    unavailable = {"state": "unavailable", "reason": "RECORDED_RECOMMENDATION_UNAVAILABLE", "data": None}
    if (
        not isinstance(handoff, InterventionHandoff)
        or _saved_alert_evidence(alert)["state"] != "recorded"
        or handoff.handoff_id != _stable_id("intervention-handoff", alert.alert_id)
        or handoff.tenant_id != output.tenant_id or handoff.tenant_id != alert.tenant_id
        or handoff.store_id != output.store_id or handoff.store_id != alert.store_id
        or handoff.alert_id != alert.alert_id or not _aware(handoff.created_at)
        or handoff.status not in ("proposed", "dispatched")
        or not isinstance(handoff.action_set_json, dict)
    ):
        return unavailable
    saved = handoff.action_set_json
    actions = saved.get("recommended_actions")
    # Original producer's action vocabulary, not another recommendation rule.
    vocabulary = (
        "inspect_machine_uptime", "review_staffing", "open_recovery_plan",
        "launch_local_promotion", "review_price_packaging",
        "review_local_demand", "create_intervention_candidate",
    )
    if (
        saved.get("trigger_alert_level") != getattr(alert.alert_level, "value", None)
        or saved.get("trigger_reason_code") != alert.alert_reason_code
        or saved.get("evidence") != alert.evidence_json
        or not isinstance(actions, list) or not actions
        or not all(isinstance(action, str) and action in vocabulary for action in actions)
        or len(set(actions)) != len(actions)
    ):
        return unavailable
    return {"state": "recorded", "reason": None, "data": {
        "handoffId": handoff.handoff_id, "alertId": alert.alert_id,
        "forecastOutputId": output.forecast_output_id, "predictionRunId": output.prediction_run_id,
        "policy": {"id": alert.policy_id, "version": alert.policy_version,
                   "versionId": alert.policy_version_id},
        "createdAt": handoff.created_at.isoformat(), "handoffStatus": handoff.status,
        "actionIds": list(actions),
    }}


def current_issue_forecast(
    *, issue: dict[str, Any], tenant_id: str, repository: Any, engine: Any,
) -> dict[str, Any]:
    """Called inside the existing route transaction, after current issue ABAC.

    A missing optional forecast leaves the authorized issue workflow usable.
    SQL failures raise through the transaction so an aborted PG transaction is
    rolled back before the route emits its established safe 503 envelope.
    """
    result = {
        "status": "unavailable", "reason": "CURRENT_FORECAST_SOURCE_UNAVAILABLE",
        "issueId": issue.get("id"), "storeId": issue.get("storeId"), "data": None,
    }
    source = issue.get("sourceRef")
    if (
        not _text(tenant_id) or not _text(issue.get("storeId"))
        or issue.get("source") != "forecastOps" or not isinstance(source, dict)
        or source.get("kind") != "forecastops.alert" or not _text(source.get("id"))
        or issue.get("id") != "ISS-FC-" + source["id"]
        or not isinstance(repository, DurableForecastOpsRepository)
        or getattr(getattr(repository, "_store", None), "engine", None) is not engine
        or getattr(getattr(repository, "_store", None), "tenant_id", tenant_id) != tenant_id
    ):
        return result
    try:
        alert = repository.get_alert(tenant_id, source["id"])
        if (
            not isinstance(alert, Alert) or alert.alert_id != source["id"]
            or alert.tenant_id != tenant_id or alert.store_id != issue["storeId"]
            or not all(_text(value) for value in (
                alert.policy_id, alert.policy_version, alert.policy_version_id,
            ))
        ):
            return result
        history = repository.history(tenant_id, issue["storeId"])
        if not isinstance(history, list) or not all(isinstance(row, ForecastOutput) for row in history):
            return result
        candidates = [row for row in history if row.tenant_id == tenant_id
                      and row.store_id == issue["storeId"]
                      and _stable_id("forecast-alert", row.forecast_output_id,
                                     alert.policy_version_id) == alert.alert_id]
        if len(candidates) != 1:
            return result
        output = candidates[0]
        if type(output.horizon_days) is not int or output.forecast_output_id != _stable_id(
            "forecast-output", output.prediction_run_id, tenant_id, output.store_id,
            f"w{output.horizon_days // 7}",
        ) or not _matches_alert(alert, output):
            return result
        canonical = repository.get_canonical_forecast(tenant_id, output.forecast_output_id)
        run = repository.get_prediction_run(tenant_id, output.prediction_run_id)
        predictions = repository.get_predictions(tenant_id, output.prediction_run_id)
        if not isinstance(predictions, list):
            return result
        # One input saves one selected-horizon Prediction; its domain output
        # still contains all four bands. Do not invent three missing rows.
        # If another horizon was actually requested in this run, however, its
        # real domain/canonical/Prediction records cannot contradict the band
        # being returned here or be silently ignored as an orphan.
        related = [row for row in history if row.tenant_id == tenant_id
                   and row.store_id == output.store_id
                   and row.prediction_run_id == output.prediction_run_id]
        counterparts = []
        from uuid import NAMESPACE_URL, uuid5

        for weeks in _WEEKS:
            identity = _stable_id(
                "forecast-output", output.prediction_run_id, tenant_id, output.store_id,
                f"w{weeks}",
            )
            domain = [row for row in related if row.horizon_days == weeks * 7]
            if any(row.forecast_output_id != identity for row in domain):
                return result
            native = canonical if identity == output.forecast_output_id else repository.get_canonical_forecast(tenant_id, identity)
            prediction_id = "prediction-" + str(uuid5(
                NAMESPACE_URL, f"{output.prediction_run_id}:{output.store_id}:w{weeks}",
            ))
            matching_predictions = [row for row in predictions if isinstance(row, Prediction)
                                    and row.prediction_id == prediction_id]
            if not domain and native is None and not matching_predictions:
                continue
            if len(domain) != 1 or len(matching_predictions) != 1 or native is None:
                return result
            counterparts.append((weeks, domain[0], native))
        if any(type(row.horizon_days) is not int
               or row.horizon_days not in tuple(weeks * 7 for weeks in _WEEKS) for row in related):
            return result
        handoff = repository.get_handoff(
            tenant_id, _stable_id("intervention-handoff", alert.alert_id),
        )
    except Exception as exc:
        raise StoreOpsLiveDataUnavailable("Current ForecastOps source read unavailable") from exc
    try:
        if not _joined_output(output, canonical, run, predictions):
            return result
        binding = _binding(output)
        if binding is None:
            result["reason"] = "RECORDED_PRODUCTION_MODEL_BINDING_UNAVAILABLE"
            return result
        for weeks, counterpart, native in counterparts:
            band = getattr(output, f"w{weeks}")
            if (
                not _joined_output(counterpart, native, run, predictions)
                or _binding(counterpart) is None
                or (counterpart.p10, counterpart.p50, counterpart.p90)
                != (band.p10, band.p50, band.p90)
            ):
                return result
    except (AttributeError, TypeError, ValueError, OverflowError):
        # Malformed retained values are not a model result. This contains no
        # database access; SQL failures above still roll back at the route.
        return result
    result.update(status="recorded", reason=None, data={
        "alertId": alert.alert_id, "forecastOutputId": output.forecast_output_id,
        "predictionRunId": output.prediction_run_id, "forecastVersion": output.forecast_version,
        "targetMetric": output.target_metric,
        "bands": [{"horizonWeeks": weeks, **getattr(output, f"w{weeks}").to_dict()}
                  for weeks in _WEEKS],
        "actualRevenue": output.actual_revenue,
        "siteScoreBaselineP50": output.sitescore_baseline_p50,
        "siteScoreGapRatio": output.sitescore_gap_ratio,
        "trajectoryClass": output.trajectory_class,
        "turningPointProbability": output.turning_point_probability,
        "predictionOriginTime": output.prediction_origin_time.isoformat(),
        "scoredAt": output.scored_at.isoformat(), "featureVersion": output.feature_version,
        "sourceSnapshotIds": list(output.source_snapshot_ids),
        "dataQualityScore": output.data_quality_score,
        "dataStalenessDays": output.data_staleness_days,
        "featureSnapshotTime": run.feature_snapshot_time.isoformat(),
        "qualityAtRun": _quality_at_run(alert, output, run),
        "alertAtRun": _saved_alert_evidence(alert),
        "recommendationAtAlert": _saved_recommendation(handoff, alert, output),
        "rootCause": {"state": "unavailable", "reason": "ROOT_CAUSE_CANDIDATE_UNPRODUCED",
                      "causeCandidate": None, "evidenceStrength": None, "dataConfidence": None},
        "modelBinding": binding,
        "policy": {"id": alert.policy_id, "version": alert.policy_version,
                   "versionId": alert.policy_version_id},
    })
    return result
