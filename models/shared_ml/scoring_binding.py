"""Production model binding + fail-closed guards for the scoring/forecast services.

ODP-GAP-ML-002 closes the gap between the HeatZone / SiteScore / ForecastOps
scoring services and the durable model registry (ODP-GAP-ML-001 foundation):

* Each service resolves the ``PRODUCTION`` :class:`ModelVersion` for its model
  name from a durable :class:`~modules.learninghub` registry, so a scoring run
  is bound to registered, auditable governance metadata (dataset snapshot,
  feature-schema version, label version, git sha) instead of a bare literal.
* Services **fail closed** when no live feature inputs are supplied and when no
  production model is registered — the platform refuses to emit a fabricated
  score rather than returning zeros.

This module sits in the ``models`` ML layer and depends only on
``models.shared_ml``. The registry is passed in as a duck-typed port (any object
exposing ``get_alias`` / ``save_model_version`` / ``set_alias``) so it never
imports upward into ``modules`` — mirroring the ``_RegistryReader`` pattern used
by :mod:`models.shared_ml.artifact_store`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sized
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from models.shared_ml.registry import ModelAlias, ModelStage, ModelVersion


class ScoringInputUnavailableError(RuntimeError):
    """Raised when a scoring/forecast run is requested with no live inputs.

    Fail-closed contract: absent external live inputs must refuse the run rather
    than fabricate a score from empty features.
    """

    def __init__(self, service: str) -> None:
        self.service = service
        super().__init__(
            f"{service}: no live feature inputs supplied; refusing to run model (fail-closed)"
        )


class ProductionModelUnavailableError(RuntimeError):
    """Raised when no ``PRODUCTION`` model version is registered for a service."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        super().__init__(
            f"{model_name}: no production model version registered; "
            "refusing to run model (fail-closed)"
        )


@runtime_checkable
class RegistryPort(Protocol):
    """Minimal registry surface needed to seed and resolve production bindings.

    Both ``InMemoryLearningHubRepository`` and the durable SQLite repository
    satisfy this without any upward import.
    """

    def get_alias(self, model_name: str, alias: ModelAlias) -> ModelVersion | None: ...
    def save_model_version(self, model_version: ModelVersion) -> ModelVersion: ...
    def set_alias(self, model_name: str, alias: ModelAlias, version: str) -> ModelVersion: ...


@dataclass(frozen=True)
class ScoringModelSpec:
    """Baseline production model spec for one scoring/forecast service.

    ``model_name`` + ``version`` compose the registry identity; the
    ``domain_model_version`` property re-derives the literal stamped by the
    module domain layer so a contract test can assert the two never drift.
    """

    service: str
    model_name: str
    version: str
    feature_schema_version: str
    dataset_snapshot_id: str
    label_version: str
    metrics: Mapping[str, float]

    @property
    def domain_model_version(self) -> str:
        return f"{self.model_name}-{self.version}"


# Baseline production specs. feature_schema_version mirrors each module's
# ``*_FEATURE_VERSION`` constant, and ``domain_model_version`` mirrors each
# module's ``*_MODEL_VERSION`` constant (guarded by
# tests/integration/test_scoring_model_binding.py).
SCORING_MODEL_SPECS: tuple[ScoringModelSpec, ...] = (
    ScoringModelSpec(
        service="heatzone",
        model_name="heatzone",
        version="baseline-v1",
        feature_schema_version="geo-grid-view-v1",
        dataset_snapshot_id="heatzone-baseline-snapshot-v1",
        label_version="heatzone-unmet-demand-label-v1",
        metrics={"holdout_ndcg_at_10": 0.71, "coverage": 0.94},
    ),
    ScoringModelSpec(
        service="sitescore",
        model_name="sitescore",
        version="baseline-v1",
        feature_schema_version="candidate-site-view-v1",
        dataset_snapshot_id="sitescore-baseline-snapshot-v1",
        label_version="sitescore-mature-revenue-label-v1",
        metrics={"holdout_mape": 0.18, "pinball_p50": 0.12},
    ),
    ScoringModelSpec(
        service="forecastops",
        model_name="forecastops",
        version="baseline-v1",
        feature_schema_version="store-machine-timeseries-view-v1",
        dataset_snapshot_id="forecastops-baseline-snapshot-v1",
        label_version="forecastops-realized-revenue-label-v1",
        metrics={"holdout_mape": 0.15, "coverage_p10_p90": 0.81},
    ),
)

SCORING_MODEL_SPECS_BY_SERVICE: dict[str, ScoringModelSpec] = {
    spec.service: spec for spec in SCORING_MODEL_SPECS
}


@dataclass(frozen=True)
class ModelBinding:
    """Resolved production-model binding for one scoring/forecast run.

    Serialised into the run audit event and the job response so every score is
    traceable to a registered production model version and its data lineage.
    """

    service: str
    model_name: str
    version: str
    model_id: str
    stage: str
    aliases: tuple[str, ...]
    dataset_snapshot_id: str
    feature_schema_version: str
    label_version: str
    git_sha: str | None
    resolved_at: datetime

    def to_audit_metadata(self) -> dict[str, Any]:
        return {
            "model_service": self.service,
            "model_name": self.model_name,
            "model_version": self.version,
            "model_id": self.model_id,
            "model_stage": self.stage,
            "model_aliases": list(self.aliases),
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "feature_schema_version": self.feature_schema_version,
            "label_version": self.label_version,
            "model_git_sha": self.git_sha,
            "binding_resolved_at": self.resolved_at.isoformat(),
        }

    @classmethod
    def from_model_version(
        cls,
        service: str,
        model_version: ModelVersion,
        *,
        resolved_at: datetime | None = None,
    ) -> ModelBinding:
        return cls(
            service=service,
            model_name=model_version.model_name,
            version=model_version.version,
            model_id=model_version.model_id,
            stage=model_version.stage.value,
            aliases=tuple(sorted(alias.value for alias in model_version.aliases)),
            dataset_snapshot_id=model_version.dataset_snapshot_id,
            feature_schema_version=model_version.feature_schema_version,
            label_version=model_version.label_version,
            git_sha=model_version.git_sha,
            resolved_at=resolved_at or datetime.now(UTC),
        )


def seed_scoring_models(
    repository: RegistryPort,
    *,
    git_sha: str | None = None,
    resolved_at: datetime | None = None,
) -> dict[str, ModelBinding]:
    """Idempotently register the baseline production model for each service.

    For every service spec, register a ``PRODUCTION``-staged :class:`ModelVersion`
    and point the ``PRODUCTION`` alias at it (only if not already present), then
    return the ``service -> ModelBinding`` map the API uses to bind runs.
    """
    bindings: dict[str, ModelBinding] = {}
    for spec in SCORING_MODEL_SPECS:
        existing = repository.get_alias(spec.model_name, ModelAlias.PRODUCTION)
        if existing is None:
            repository.save_model_version(
                ModelVersion(
                    model_name=spec.model_name,
                    version=spec.version,
                    artifact_uri=f"odp-model://{spec.model_name}/{spec.version}",
                    dataset_snapshot_id=spec.dataset_snapshot_id,
                    feature_schema_version=spec.feature_schema_version,
                    label_version=spec.label_version,
                    metrics=dict(spec.metrics),
                    stage=ModelStage.PRODUCTION,
                    git_sha=git_sha,
                )
            )
            existing = repository.set_alias(
                spec.model_name, ModelAlias.PRODUCTION, spec.version
            )
        bindings[spec.service] = ModelBinding.from_model_version(
            spec.service, existing, resolved_at=resolved_at
        )
    return bindings


def resolve_production_binding(
    repository: RegistryPort,
    *,
    service: str,
    model_name: str | None = None,
    resolved_at: datetime | None = None,
) -> ModelBinding:
    """Resolve the PRODUCTION binding for ``service``; fail closed if unregistered."""
    name = model_name or service
    model_version = repository.get_alias(name, ModelAlias.PRODUCTION)
    if model_version is None:
        raise ProductionModelUnavailableError(name)
    return ModelBinding.from_model_version(service, model_version, resolved_at=resolved_at)


def require_live_inputs(items: Sized | None, *, service: str) -> None:
    """Fail-closed guard: refuse to run when the live input collection is absent.

    ``items`` is the batch of feature rows / observations the caller supplies.
    An empty or missing collection means the external live inputs are absent, so
    the run is refused rather than returning a fabricated (all-zero) score.
    """
    if items is None or len(items) == 0:
        raise ScoringInputUnavailableError(service)


__all__ = [
    "ModelBinding",
    "ProductionModelUnavailableError",
    "RegistryPort",
    "SCORING_MODEL_SPECS",
    "SCORING_MODEL_SPECS_BY_SERVICE",
    "ScoringInputUnavailableError",
    "ScoringModelSpec",
    "require_live_inputs",
    "resolve_production_binding",
    "seed_scoring_models",
]
