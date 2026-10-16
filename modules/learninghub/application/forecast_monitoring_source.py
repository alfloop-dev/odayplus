"""Canonical Forecast prediction observations for released-model monitoring.

Selection contains only saved IDs. Values come from the actual tenant's
Forecast writer, its UUID5 Prediction, canonical output and recorded MLflow
binding. This loader neither executes a forecast nor turns pre-origin revenue
into a mature error label. It returns source facts for the native drift writer;
it is not a mounted monitor endpoint or a HEALTHY/rollback decision.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from models.shared_ml.registry import ModelAlias, ModelStage, ModelVersion
from modules.forecastops.domain.forecasting import ForecastOutput, _stable_id
from modules.learninghub.application.release import ModelReleaseDecision
from modules.opsboard.application.store_ops_forecast import _binding, _joined_output
from shared.auth import Action
from shared.auth.abac import AccessRequest, ResourceDescriptor
from shared.auth.engine import AuthorizationEngine
from shared.auth.identity import Principal
from shared.domain.models import Store
from shared.governance.decision_policy import DecisionPolicy, resolve_policy
from shared.governance.model_performance import (
    MODEL_PERFORMANCE_DRIFT_POLICY_KIND,
    MODEL_PERFORMANCE_DRIFT_POLICY_TENANT_ID,
)
from shared.infrastructure.persistence.decision_policy import SqlDecisionPolicyRepository
from shared.infrastructure.persistence.repositories import (
    DurableForecastOpsRepository,
    DurableLearningHubRepository,
    DurableStoreRepository,
)


class CanonicalMonitoringUnavailable(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _aware(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _digest(value: Any) -> str:
    def encode(item: Any) -> str:
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError("unsupported canonical monitoring source value")
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False, default=encode).encode()).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ) or len(set(value)) != len(value):
        raise CanonicalMonitoringUnavailable("MONITORING_SOURCE_IDS_INVALID")
    return tuple(value)


def _period(release: ModelReleaseDecision) -> tuple[datetime, datetime]:
    start = getattr(release, "executed_at", None)
    if not _aware(start):
        raise CanonicalMonitoringUnavailable("RELEASE_EXECUTION_ANCHOR_UNAVAILABLE")
    # Only an explicit unit-bearing duration is interpretable. Free text such
    # as 'immediate' or 'until stable' is retained by the release, not guessed.
    if not isinstance(release.monitoring_window, str):
        raise CanonicalMonitoringUnavailable("RELEASE_MONITORING_PERIOD_UNAVAILABLE")
    match = re.fullmatch(r"([1-9][0-9]*)(h|d|w)", release.monitoring_window)
    if match is None:
        raise CanonicalMonitoringUnavailable("RELEASE_MONITORING_PERIOD_UNAVAILABLE")
    try:
        duration = timedelta(**{{"h": "hours", "d": "days", "w": "weeks"}[match[2]]: int(match[1])})
        return start, start + duration
    except (ValueError, OverflowError):
        raise CanonicalMonitoringUnavailable("RELEASE_MONITORING_PERIOD_UNAVAILABLE") from None


@dataclass(frozen=True)
class ForecastMonitoringSources:
    """Frozen source selection, not a completed monitoring assessment."""

    release_id: str
    release_revision: int
    model_name: str
    model_version: str
    model_dataset_snapshot_id: str
    tenant_id: str
    store_id: str
    horizon_days: int
    monitoring_window: str
    release_executed_at: datetime
    release_window_end: datetime
    decision_policy: DecisionPolicy
    reference: tuple[Mapping[str, Any], ...]
    current: tuple[Mapping[str, Any], ...]
    source_manifest: tuple[Mapping[str, Any], ...]
    source_digest: str
    reference_coverage: tuple[datetime, datetime]
    current_coverage: tuple[datetime, datetime]

    @property
    def cohort_key(self) -> str:
        return f"forecastops:{self.tenant_id}:{self.store_id}:revenue:w{self.horizon_days // 7}"


class CanonicalForecastMonitoringSource:
    def __init__(self, *, learning: DurableLearningHubRepository,
                 forecasts: DurableForecastOpsRepository, stores: DurableStoreRepository,
                 policies: SqlDecisionPolicyRepository, authorization: AuthorizationEngine) -> None:
        engine = getattr(getattr(learning, "_store", None), "engine", None)
        if (
            engine is None or not isinstance(learning, DurableLearningHubRepository)
            or not isinstance(forecasts, DurableForecastOpsRepository)
            or not isinstance(stores, DurableStoreRepository)
            or not isinstance(policies, SqlDecisionPolicyRepository)
            or getattr(getattr(forecasts, "_store", None), "engine", None) is not engine
            or getattr(stores, "_engine", None) is not engine
            or getattr(policies, "_engine", None) is not engine
            or getattr(authorization.audit_log, "_engine", None) is not engine
        ):
            raise CanonicalMonitoringUnavailable("MONITORING_DURABLE_OWNER_MISMATCH")
        self.learning, self.forecasts, self.stores = learning, forecasts, stores
        self.policies, self.authorization, self.engine = policies, authorization, engine

    def _release(self, release_id: str) -> tuple[ModelReleaseDecision, ModelVersion]:
        release = self.learning.get_release_decision(release_id)
        if not isinstance(release, ModelReleaseDecision) or release.release_id != release_id:
            raise CanonicalMonitoringUnavailable("MONITORING_RELEASE_UNAVAILABLE")
        # The global revision reserves writer intents; compensation can advance
        # it while this originating release remains the actual production alias.
        model = self.learning.get_alias(release.model_name, ModelAlias.PRODUCTION)
        if (
            not isinstance(model, ModelVersion) or model.stage is not ModelStage.PRODUCTION
            or model.model_name != release.model_name or model.version != release.to_version
            or release.scope != "global" or type(release.release_revision) is not int
            or release.release_revision < 1
            or not isinstance(model.monitoring_config, Mapping)
            or model.monitoring_config.get("release_id") != release.release_id
            or model.monitoring_config.get("release_revision") != release.release_revision
            or type(model.monitoring_config.get("release_revision")) is not int
            or model.dataset_snapshot_id != release.dataset_snapshot_id
        ):
            raise CanonicalMonitoringUnavailable("MONITORING_CURRENT_RELEASE_MISMATCH")
        return release, model

    def load(self, *, principal: Principal, release_id: str, store_id: str,
             horizon_days: int, reference_ids: tuple[str, ...], current_ids: tuple[str, ...],
             as_of: datetime) -> ForecastMonitoringSources:
        reference_ids, current_ids = _ids(reference_ids), _ids(current_ids)
        if set(reference_ids) & set(current_ids) or type(horizon_days) is not int or horizon_days not in (28, 56, 84, 168):
            raise CanonicalMonitoringUnavailable("MONITORING_COHORT_INVALID")
        if not isinstance(principal, Principal) or not principal.authenticated or not principal.tenant_id or not _aware(as_of):
            raise CanonicalMonitoringUnavailable("MONITORING_CURRENT_PRINCIPAL_REQUIRED")
        store = self.stores.get_store(store_id)
        if not isinstance(store, Store) or store.store_id != store_id or store.tenant_id != principal.tenant_id or not store.is_current:
            raise CanonicalMonitoringUnavailable("MONITORING_SOURCE_NOT_FOUND")
        # Store has no authoritative assigned-area/HeatZone parent. An explicit
        # restriction on those axes cannot be satisfied by inventing a mapping.
        if principal.scope.assigned_area_ids or principal.scope.heat_zone_ids:
            raise CanonicalMonitoringUnavailable("MONITORING_STORE_SCOPE_UNPROVEN")
        for resource_type in ("model", "forecastops"):
            decision = self.authorization.authorize(AccessRequest(
                principal=principal, action=Action.VIEW,
                resource=ResourceDescriptor(type=resource_type, resource_id=store_id,
                    tenant_id=store.tenant_id, store_id=store.store_id,
                    brand_id=store.brand_id, region_id=store.region_code, module="forecastops"),
            ))
            if not decision.allowed:
                raise CanonicalMonitoringUnavailable("MONITORING_SOURCE_NOT_FOUND")
        release, model = self._release(release_id)
        start, end = _period(release)
        if as_of < start:
            raise CanonicalMonitoringUnavailable("MONITORING_PERIOD_NOT_STARTED")
        policy = resolve_policy(self.policies, policy_kind=MODEL_PERFORMANCE_DRIFT_POLICY_KIND,
                                tenant_id=MODEL_PERFORMANCE_DRIFT_POLICY_TENANT_ID, at=as_of)
        if not policy.reads("prediction_outputs"):
            raise CanonicalMonitoringUnavailable("MONITORING_POLICY_INPUT_MISMATCH")
        history = self.forecasts.history(principal.tenant_id, store_id)
        if not isinstance(history, list) or not all(isinstance(row, ForecastOutput) for row in history):
            raise CanonicalMonitoringUnavailable("MONITORING_SOURCE_INVALID")
        rows: dict[str, ForecastOutput] = {}
        manifest = []
        saved_facts = {}
        model_binding = None
        for identity in (*reference_ids, *current_ids):
            matches = [row for row in history if row.forecast_output_id == identity]
            if len(matches) != 1:
                raise CanonicalMonitoringUnavailable("MONITORING_SOURCE_NOT_FOUND")
            row = matches[0]
            canonical = self.forecasts.get_canonical_forecast(principal.tenant_id, identity)
            run = self.forecasts.get_prediction_run(principal.tenant_id, row.prediction_run_id)
            predictions = self.forecasts.get_predictions(principal.tenant_id, row.prediction_run_id)
            binding = _binding(row)
            nested = row.model_metadata.get("model_metadata") if isinstance(row.model_metadata, Mapping) else None
            if (
                row.tenant_id != principal.tenant_id or row.store_id != store_id
                or row.horizon_days != horizon_days or row.target_metric != "revenue"
                or row.forecast_output_id != _stable_id("forecast-output", row.prediction_run_id,
                    principal.tenant_id, store_id, f"w{horizon_days // 7}")
                or binding is None or not _joined_output(row, canonical, run, predictions)
                or binding["model_name"] != model.model_name or binding["model_version"] != model.version
                or binding["model_id"] != model.model_id or binding["model_stage"] != "production"
                or binding["dataset_snapshot_id"] != model.dataset_snapshot_id
                or binding["feature_schema_version"] != model.feature_schema_version
                or binding["label_version"] != model.label_version
                or not isinstance(nested, Mapping) or nested.get("release_id") != release_id
                or type(nested.get("release_revision")) is not int
                or nested["release_revision"] != release.release_revision
                or not start <= row.scored_at <= min(end, as_of)
            ):
                raise CanonicalMonitoringUnavailable("MONITORING_SOURCE_RELEASE_JOIN_MISMATCH")
            # Resolved-at can legitimately differ between inferences. Artifact,
            # registry/model-run and approval facts for this one release cannot.
            immutable_binding = {key: value for key, value in binding.items()
                                 if key != "binding_resolved_at"}
            if model_binding is not None and immutable_binding != model_binding:
                raise CanonicalMonitoringUnavailable("MONITORING_MODEL_BINDING_CHANGED")
            model_binding = immutable_binding
            rows[identity] = deepcopy(row)
            saved_facts[identity] = _digest({"domain": row.to_dict(), "canonical": asdict(canonical),
                "run": asdict(run), "predictions": [asdict(item) for item in predictions]})
            manifest.append({"forecast_output_id": identity, "forecast_version": row.forecast_version,
                "prediction_value": row.p50,
                "prediction_run_id": row.prediction_run_id, "scored_at": row.scored_at.isoformat(),
                "prediction_origin_time": row.prediction_origin_time.isoformat(),
                "feature_snapshot_time": run.feature_snapshot_time.isoformat(),
                "source_snapshot_ids": list(row.source_snapshot_ids), "binding": binding,
                "sha256": saved_facts[identity]})
        ref_times = [rows[key].scored_at for key in reference_ids]
        cur_times = [rows[key].scored_at for key in current_ids]
        if max(ref_times) >= min(cur_times):
            raise CanonicalMonitoringUnavailable("MONITORING_SOURCE_PERIODS_OVERLAP")
        # Guard against an intervening release or policy supersession before a
        # caller can freeze these facts. The writer must revalidate again under
        # the same existing release transaction/lock immediately before commit.
        if (self._release(release_id) != (release, model)
            or self.stores.get_store(store_id) != store
            or self.policies.find_version(policy.policy_version_id) != policy
            or self.policies.find_effective(policy_kind=MODEL_PERFORMANCE_DRIFT_POLICY_KIND,
                tenant_id=MODEL_PERFORMANCE_DRIFT_POLICY_TENANT_ID, at=as_of) != policy):
            raise CanonicalMonitoringUnavailable("MONITORING_SOURCE_CHANGED")
        fresh = self.forecasts.history(principal.tenant_id, store_id)
        for identity, frozen_hash in saved_facts.items():
            matches = [row for row in fresh if isinstance(row, ForecastOutput) and row.forecast_output_id == identity]
            if len(matches) != 1:
                raise CanonicalMonitoringUnavailable("MONITORING_SOURCE_CHANGED")
            row = matches[0]
            canonical = self.forecasts.get_canonical_forecast(principal.tenant_id, identity)
            run = self.forecasts.get_prediction_run(principal.tenant_id, row.prediction_run_id)
            predictions = self.forecasts.get_predictions(principal.tenant_id, row.prediction_run_id)
            if (not _joined_output(row, canonical, run, predictions)
                or _digest({"domain": row.to_dict(), "canonical": asdict(canonical),
                    "run": asdict(run), "predictions": [asdict(item) for item in predictions]}) != frozen_hash):
                raise CanonicalMonitoringUnavailable("MONITORING_SOURCE_CHANGED")
        cohort = f"forecastops:{principal.tenant_id}:{store_id}:revenue:w{horizon_days // 7}"
        def values(identities: tuple[str, ...]) -> tuple[Mapping[str, Any], ...]:
            return tuple(_freeze({"prediction": rows[key].p50, "model_name": model.model_name,
                "model_version": model.version, "cohort_key": cohort, "entity_id": store_id})
                for key in identities)
        return ForecastMonitoringSources(release_id, release.release_revision, model.model_name,
            model.version, model.dataset_snapshot_id, principal.tenant_id, store_id, horizon_days,
            release.monitoring_window, start, end, replace(policy, parameters=_freeze(policy.parameters)), values(reference_ids), values(current_ids),
            _freeze(manifest), _digest(manifest), (min(ref_times), max(ref_times)),
            (min(cur_times), max(cur_times)))
