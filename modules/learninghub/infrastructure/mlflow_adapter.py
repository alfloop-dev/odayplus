from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from models.shared_ml.registry import ModelAlias, ModelStage, ModelVersion

from .repositories import LearningHubRepository

if TYPE_CHECKING:
    from mlflow.entities.model_registry import ModelVersion as MlflowModelVersion
    from mlflow.tracking import MlflowClient


_TAG_PREFIX = "oday.model_version."
_TAG_SCHEMA_VERSION = "1"
_LINEAGE_ARTIFACT_ROOT = "oday-lineage/model-versions"
_STAGE_TO_MLFLOW = {
    ModelStage.DEV: "None",
    ModelStage.SHADOW: "Staging",
    ModelStage.CANARY: "Staging",
    ModelStage.PRODUCTION: "Production",
    ModelStage.RETIRED: "Archived",
    ModelStage.ROLLED_BACK: "Archived",
    ModelStage.BLOCKED: "Archived",
}
_MLFLOW_TO_STAGE = {
    "None": ModelStage.DEV,
    "Staging": ModelStage.CANARY,
    "Production": ModelStage.PRODUCTION,
    "Archived": ModelStage.RETIRED,
}


@dataclass
class MlflowRegistryAdapter:
    """Project the Learning Hub model contract onto an OSS MLflow registry."""

    repository: LearningHubRepository
    tracking_uri: str | None = None
    experiment_name: str = "oday-plus-learninghub"
    client: MlflowClient | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.client is None:
            from mlflow.tracking import MlflowClient

            self.client = MlflowClient(tracking_uri=self.tracking_uri)

    def register_model_version(self, model_version: ModelVersion) -> ModelVersion:
        client = self._require_client()
        self._ensure_registered_model(model_version.model_name)

        existing = self._find_model_version(model_version.model_name, model_version.version)
        if existing is not None:
            self._assert_immutable_lineage(existing, model_version)
            mlflow_version = existing
            run_id = existing.run_id
        else:
            run_id = self._resolve_run(model_version)
            tags = self._lineage_tags(model_version, mlflow_run_id=run_id)
            mlflow_version = client.create_model_version(
                name=model_version.model_name,
                source=model_version.artifact_uri,
                run_id=run_id,
                tags=tags,
                description=(
                    f"ODay Plus domain model version {model_version.model_name}:"
                    f"{model_version.version}"
                ),
            )

        if run_id is None:
            raise ValueError(
                f"MLflow model version {model_version.model_name}:{model_version.version} "
                "has no run lineage"
            )

        tags = self._lineage_tags(model_version, mlflow_run_id=run_id)
        self._write_run_lineage(run_id, model_version, tags)
        self._write_model_version_tags(
            model_version.model_name,
            str(mlflow_version.version),
            tags,
        )
        self._transition_mlflow_stage(
            model_version.model_name,
            str(mlflow_version.version),
            model_version.stage,
        )
        for alias in model_version.aliases:
            client.set_registered_model_alias(
                model_version.model_name,
                alias.value,
                str(mlflow_version.version),
            )

        restored = self._to_domain(
            client.get_model_version(model_version.model_name, str(mlflow_version.version))
        )
        self.repository.save_model_version(restored)
        for alias in restored.aliases:
            self.repository.set_alias(restored.model_name, alias, restored.version)
        return self.repository.get_model_version(restored.model_name, restored.version) or restored

    def transition_stage(
        self,
        *,
        model_name: str,
        version: str,
        stage: ModelStage,
    ) -> ModelVersion:
        mlflow_version = self._require_model_version(model_name, version)
        self._transition_mlflow_stage(model_name, str(mlflow_version.version), stage)
        self._require_client().set_model_version_tag(
            model_name,
            str(mlflow_version.version),
            self._tag("stage"),
            stage.value,
        )
        restored = self._to_domain(
            self._require_client().get_model_version(model_name, str(mlflow_version.version))
        )
        return self.repository.save_model_version(restored)

    def set_alias(self, *, model_name: str, alias: ModelAlias, version: str) -> ModelVersion:
        mlflow_version = self._require_model_version(model_name, version)
        client = self._require_client()
        client.set_registered_model_alias(model_name, alias.value, str(mlflow_version.version))
        restored = self._to_domain(client.get_model_version_by_alias(model_name, alias.value))
        self.repository.save_model_version(restored)
        return self.repository.set_alias(model_name, alias, restored.version)

    def get_by_alias(self, *, model_name: str, alias: ModelAlias) -> ModelVersion | None:
        from mlflow.exceptions import MlflowException

        client = self._require_client()
        try:
            mlflow_version = client.get_model_version_by_alias(model_name, alias.value)
        except MlflowException as exc:
            if exc.error_code in {"RESOURCE_DOES_NOT_EXIST", "INVALID_PARAMETER_VALUE"}:
                return None
            raise

        restored = self._to_domain(mlflow_version)
        self.repository.save_model_version(restored)
        return self.repository.set_alias(model_name, alias, restored.version)

    def _require_client(self) -> MlflowClient:
        if self.client is None:
            raise RuntimeError("MLflow client was not initialized")
        return self.client

    def _ensure_registered_model(self, model_name: str) -> None:
        from mlflow.exceptions import MlflowException

        client = self._require_client()
        try:
            client.get_registered_model(model_name)
            return
        except MlflowException as exc:
            if exc.error_code != "RESOURCE_DOES_NOT_EXIST":
                raise

        try:
            client.create_registered_model(
                model_name,
                tags={
                    "oday.owner": "learninghub",
                    "oday.contract": "models.shared_ml.registry.ModelVersion",
                },
                description="ODay Plus Learning Hub managed model",
            )
        except MlflowException as exc:
            if exc.error_code != "RESOURCE_ALREADY_EXISTS":
                raise

    def _resolve_run(self, model_version: ModelVersion) -> str:
        from mlflow.exceptions import MlflowException

        client = self._require_client()
        if model_version.run_id:
            try:
                return client.get_run(model_version.run_id).info.run_id
            except MlflowException as exc:
                if exc.error_code not in {
                    "RESOURCE_DOES_NOT_EXIST",
                    "INVALID_PARAMETER_VALUE",
                }:
                    raise

        experiment = client.get_experiment_by_name(self.experiment_name)
        if experiment is None:
            experiment_id = client.create_experiment(self.experiment_name)
        else:
            experiment_id = experiment.experiment_id
        run = client.create_run(
            experiment_id,
            run_name=f"{model_version.model_name}-{model_version.version}",
            tags={
                "oday.model_name": model_version.model_name,
                "oday.domain_version": model_version.version,
                "oday.requested_run_id": model_version.run_id or "",
            },
        )
        return run.info.run_id

    def _write_run_lineage(
        self,
        run_id: str,
        model_version: ModelVersion,
        tags: dict[str, str],
    ) -> None:
        client = self._require_client()
        for key, value in tags.items():
            client.set_tag(run_id, key, value)
        for metric_name, metric_value in model_version.metrics.items():
            client.log_metric(run_id, metric_name, float(metric_value))
        client.log_dict(
            run_id,
            {
                **model_version.to_dict(),
                "mlflow_run_id": run_id,
                "tag_schema_version": _TAG_SCHEMA_VERSION,
            },
            f"{_LINEAGE_ARTIFACT_ROOT}/{model_version.version}.json",
        )

    def _write_model_version_tags(
        self,
        model_name: str,
        mlflow_version: str,
        tags: dict[str, str],
    ) -> None:
        client = self._require_client()
        for key, value in tags.items():
            client.set_model_version_tag(model_name, mlflow_version, key, value)

    def _transition_mlflow_stage(
        self,
        model_name: str,
        mlflow_version: str,
        stage: ModelStage,
    ) -> None:
        self._require_client().transition_model_version_stage(
            name=model_name,
            version=mlflow_version,
            stage=_STAGE_TO_MLFLOW[stage],
            archive_existing_versions=False,
        )

    def _find_model_version(
        self,
        model_name: str,
        domain_version: str,
    ) -> MlflowModelVersion | None:
        versions = self._require_client().search_model_versions(
            filter_string=f"name = {json.dumps(model_name)}"
        )
        matches = [
            version
            for version in versions
            if version.tags.get(self._tag("domain_version")) == domain_version
        ]
        if not matches:
            return None
        return max(matches, key=lambda version: int(version.version))

    def _require_model_version(self, model_name: str, domain_version: str) -> MlflowModelVersion:
        model_version = self._find_model_version(model_name, domain_version)
        if model_version is None:
            raise ValueError(f"unknown model version {model_name}:{domain_version}")
        return model_version

    def _assert_immutable_lineage(
        self,
        mlflow_version: MlflowModelVersion,
        model_version: ModelVersion,
    ) -> None:
        existing_artifact_uri = mlflow_version.tags.get(
            self._tag("artifact_uri"),
            mlflow_version.source,
        )
        existing_source_run_id = mlflow_version.tags.get(self._tag("source_run_id")) or None
        if existing_artifact_uri != model_version.artifact_uri:
            raise ValueError(
                f"immutable artifact lineage conflict for {model_version.model_id}: "
                f"{existing_artifact_uri!r} != {model_version.artifact_uri!r}"
            )
        if model_version.run_id and existing_source_run_id != model_version.run_id:
            raise ValueError(
                f"immutable run lineage conflict for {model_version.model_id}: "
                f"{existing_source_run_id!r} != {model_version.run_id!r}"
            )

    def _lineage_tags(
        self,
        model_version: ModelVersion,
        *,
        mlflow_run_id: str,
    ) -> dict[str, str]:
        return {
            self._tag("schema_version"): _TAG_SCHEMA_VERSION,
            self._tag("domain_version"): model_version.version,
            self._tag("artifact_uri"): model_version.artifact_uri,
            self._tag("dataset_snapshot_id"): model_version.dataset_snapshot_id,
            self._tag("feature_schema_version"): model_version.feature_schema_version,
            self._tag("label_version"): model_version.label_version,
            self._tag("metrics"): self._json(model_version.metrics),
            self._tag("stage"): model_version.stage.value,
            self._tag("source_run_id"): model_version.run_id or mlflow_run_id,
            self._tag("mlflow_run_id"): mlflow_run_id,
            self._tag("git_sha"): model_version.git_sha or "",
            self._tag("created_at"): model_version.created_at.isoformat(),
            self._tag("approved_by"): model_version.approved_by or "",
            self._tag("approved_at"): (
                model_version.approved_at.isoformat() if model_version.approved_at else ""
            ),
            self._tag("rollback_target"): model_version.rollback_target or "",
            self._tag("monitoring_config"): self._json(model_version.monitoring_config),
        }

    def _to_domain(self, mlflow_version: MlflowModelVersion) -> ModelVersion:
        tags = mlflow_version.tags
        domain_version = tags.get(self._tag("domain_version"))
        if not domain_version:
            raise ValueError(
                f"MLflow version {mlflow_version.name}:{mlflow_version.version} "
                "is missing its ODay Plus domain version tag"
            )
        raw_stage = tags.get(self._tag("stage"))
        stage = (
            ModelStage(raw_stage)
            if raw_stage
            else _MLFLOW_TO_STAGE.get(mlflow_version.current_stage, ModelStage.DEV)
        )
        aliases = frozenset(
            ModelAlias(alias)
            for alias in (getattr(mlflow_version, "aliases", None) or ())
            if alias in ModelAlias._value2member_map_
        )
        created_at = self._parse_datetime(tags.get(self._tag("created_at")))
        approved_at = self._parse_datetime(tags.get(self._tag("approved_at")), optional=True)
        source_run_id = tags.get(self._tag("source_run_id")) or mlflow_version.run_id
        return ModelVersion(
            model_name=mlflow_version.name,
            version=domain_version,
            artifact_uri=tags.get(self._tag("artifact_uri"), mlflow_version.source),
            dataset_snapshot_id=tags.get(self._tag("dataset_snapshot_id"), ""),
            feature_schema_version=tags.get(self._tag("feature_schema_version"), ""),
            label_version=tags.get(self._tag("label_version"), ""),
            metrics={
                key: float(value)
                for key, value in self._parse_json(tags.get(self._tag("metrics")), {}).items()
            },
            stage=stage,
            aliases=aliases,
            run_id=source_run_id,
            git_sha=tags.get(self._tag("git_sha")) or None,
            created_at=created_at,
            approved_by=tags.get(self._tag("approved_by")) or None,
            approved_at=approved_at,
            rollback_target=tags.get(self._tag("rollback_target")) or None,
            monitoring_config=self._parse_json(
                tags.get(self._tag("monitoring_config")),
                {},
            ),
        )

    @staticmethod
    def _tag(name: str) -> str:
        return f"{_TAG_PREFIX}{name}"

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _parse_json(value: str | None, default: Any) -> Any:
        return json.loads(value) if value else default

    @staticmethod
    def _parse_datetime(value: str | None, *, optional: bool = False) -> datetime | None:
        if not value:
            return None if optional else datetime.now(UTC)
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = ["MlflowRegistryAdapter"]
