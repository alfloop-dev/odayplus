from __future__ import annotations

import json
import tomllib
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from infra.mlflow.runtime import MlflowServerSettings, MlflowServerSettingsError
from scripts.models.contracts import (
    MODEL_SPECS,
    DataBounds,
    ModelTrainingConfigurationError,
    ProductionTrainingSettings,
    require_approval_document,
    require_production_database_url,
)
from scripts.models.install_views import (
    MODEL_READY_SQL_PATH,
    ModelReadyViewInstaller,
)
from scripts.models.install_views import (
    main as install_views_main,
)
from scripts.models.release import (
    _temporal_split,
    _validate_regression_temporally,
    prepare_model_rows,
)
from scripts.models.storage import (
    GcsArtifactStore,
    GcsObject,
    LoadedModelReadyRows,
    ModelReadyDataError,
    PostgresModelReadySource,
)


class FakeGcsTransport:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, GcsObject]] = {}

    def upload(
        self,
        *,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> GcsObject:
        identity = (bucket, key)
        existing = self.objects.get(identity)
        if existing is not None:
            assert existing[0] == data
            return existing[1]
        obj = GcsObject(
            bucket=bucket,
            key=key,
            generation="1",
            size_bytes=len(data),
            metadata=dict(metadata),
        )
        self.objects[identity] = (bytes(data), obj)
        return obj

    def download(self, *, bucket: str, key: str) -> bytes:
        return self.objects[(bucket, key)][0]

    def head(self, *, bucket: str, key: str) -> GcsObject | None:
        stored = self.objects.get((bucket, key))
        return stored[1] if stored else None


class FakeQueryClient:
    def __init__(
        self,
        *,
        columns: tuple[str, ...],
        rows: list[dict[str, Any]] | None = None,
        exists: bool = True,
        registry_exists: bool = True,
        contract_trainable: bool = True,
        contract_version: str = "forecast-training-view-v2",
        blocked_reason: str | None = None,
    ) -> None:
        self.columns = columns
        self.rows = rows or []
        self.exists = exists
        self.registry_exists = registry_exists
        self.contract_trainable = contract_trainable
        self.contract_version = contract_version
        self.blocked_reason = blocked_reason
        self.load_calls: list[tuple[str, tuple[Any, ...]]] = []

    def query_one(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> dict[str, Any] | None:
        if "to_regclass" in sql:
            if params[0] == "model_ready.view_contracts":
                return {
                    "relation": (
                        "model_ready.view_contracts"
                        if self.registry_exists
                        else None
                    )
                }
            return {"relation": params[0] if self.exists else None}
        if "FROM model_ready.view_contracts" in sql:
            return {
                "view_version": self.contract_version,
                "contract_state": (
                    "ACTIVE" if self.contract_trainable else "BLOCKED"
                ),
                "training_enabled": self.contract_trainable,
                "blocked_reason": self.blocked_reason,
                "installer_sha256": "b" * 64,
            }
        return {
            "eligible_count": len(self.rows),
            "labeled_count": len(self.rows),
            "temporal_min": "2026-01-01",
            "temporal_max": "2026-06-30",
        }

    def query(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> list[dict[str, Any]]:
        if "information_schema.columns" in sql:
            return [{"column_name": name} for name in self.columns]
        self.load_calls.append((sql, params))
        return self.rows[: int(params[-1])]


class FakeInstallationClient:
    def __init__(
        self,
        *,
        missing_relations: tuple[str, ...] = (),
    ) -> None:
        from scripts.models.install_views import PREREQUISITE_COLUMNS

        self.columns = {
            relation: tuple(columns)
            for relation, columns in PREREQUISITE_COLUMNS.items()
        }
        self.relations = set(self.columns) - set(missing_relations)
        self.executions: list[tuple[str, tuple[Any, ...]]] = []
        self.transactions = 0
        self.contract: dict[str, Any] | None = None

    @contextmanager
    def transaction(self) -> Any:
        self.transactions += 1
        yield self

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.executions.append((sql, params))
        if "CREATE OR REPLACE VIEW model_ready.forecast_training_view" in sql:
            self.relations.add("model_ready.forecast_training_view")
            self.contract = {
                "relation_name": "model_ready.forecast_training_view",
                "view_name": "forecast_training_view",
                "view_version": "forecast-training-view-v2",
                "contract_state": "ACTIVE",
                "training_enabled": True,
                "blocked_reason": None,
                "installer_sha256": None,
            }
        if sql.startswith("UPDATE model_ready.view_contracts") and self.contract:
            self.contract["installer_sha256"] = params[0]

    def query_one(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> dict[str, Any] | None:
        if "to_regclass" in sql:
            return {"relation": params[0] if params[0] in self.relations else None}
        if "FROM model_ready.view_contracts" in sql:
            return dict(self.contract) if self.contract else None
        return None

    def query(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> list[dict[str, Any]]:
        if "information_schema.columns" not in sql:
            return []
        relation = f"{params[0]}.{params[1]}"
        return [
            {"column_name": column}
            for column in self.columns.get(relation, ())
        ]


def _production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ODAY_DATABASE_URL",
        "postgresql://runtime-user@10.20.30.40:5432/oday_models",
    )
    monkeypatch.setenv(
        "MLFLOW_TRACKING_URI",
        "https://mlflow.internal.oday.invalid",
    )
    monkeypatch.setenv(
        "ODP_MODEL_ARTIFACT_ROOT",
        "gs://oday-model-artifacts/production",
    )
    monkeypatch.setenv("ODP_RELEASE_COMMIT_SHA", "0123456789abcdef")
    monkeypatch.setenv("ODP_MODEL_TRAINING_ACTOR", "ml-training-operator")


def test_production_database_url_accepts_only_a_named_cloud_sql_socket() -> None:
    cloud_sql_url = (
        "postgresql://oday_app:secret@/oday_app"
        "?host=/cloudsql/alfaloop-data-project:asia-east1:oday-plus-dev-postgres"
    )
    assert require_production_database_url(cloud_sql_url) == cloud_sql_url

    with pytest.raises(
        ModelTrainingConfigurationError,
        match="rejects localhost",
    ):
        require_production_database_url("postgresql://oday_app:secret@/oday_app")

    with pytest.raises(
        ModelTrainingConfigurationError,
        match="rejects localhost",
    ):
        require_production_database_url(
            "postgresql://oday_app:secret@/oday_app?host=/tmp/postgres.sock"
        )


def test_api_runtime_declares_the_production_gcs_client() -> None:
    dependencies = tomllib.loads(
        Path("pyproject.toml").read_text(encoding="utf-8")
    )["project"]["dependencies"]
    assert any(
        dependency.startswith("google-cloud-storage")
        for dependency in dependencies
    )


def _approval(**changes: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "approval_id": "MRB-2026-0017",
        "model_name": "forecast_revenue_interval",
        "model_version": "2026.07.24.1",
        "decision": "approved",
        "approver": "reviewer-17",
        "role": "model-review-board",
        "approved_at": "2026-07-24T12:00:00Z",
        "release_type": "shadow",
        "reason": "Temporal and segment validation accepted",
    }
    payload.update(changes)
    return payload


def _raw_forecast_rows(count: int = 120) -> list[dict[str, Any]]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[dict[str, Any]] = []
    for index in range(count):
        observed = start + timedelta(days=index // 2)
        store_id = f"store-{index % 2 + 1}"
        label = 1000.0 + index * 5.0
        rows.append(
            {
                "view_name": "forecast_training_view",
                "view_version": "forecast-training-view-v2",
                "entity_id": store_id,
                "tenant_id": "tenant-1",
                "feature_snapshot_time": datetime(2026, 7, 1, tzinfo=UTC),
                "prediction_origin_time": datetime(2026, 7, 2, tzinfo=UTC),
                "label_maturity_time": datetime(2026, 7, 1, tzinfo=UTC),
                "source_snapshot_ids": [f"snapshot-{index:04d}"],
                "is_training_eligible": True,
                "date": observed.date(),
                "store_id": store_id,
                "daily_net_revenue": label,
                "revenue_lag_1": label - 5.0,
                "revenue_lag_7": label - 35.0,
                "rolling_mean_7": label - 20.0,
                "rolling_mean_28": label - 70.0,
            }
        )
    return rows


def _loaded(rows: list[dict[str, Any]]) -> LoadedModelReadyRows:
    return LoadedModelReadyRows(
        rows=tuple(rows),
        relation="model_ready.forecast_training_view",
        bounds=DataBounds(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 7, 1, tzinfo=UTC),
            1000,
        ),
        query_sha256="a" * 64,
    )


def test_mlflow_server_rejects_sqlite_localhost_and_non_gcs() -> None:
    with pytest.raises(MlflowServerSettingsError, match="remote PostgreSQL"):
        MlflowServerSettings(
            backend_store_uri="sqlite:////tmp/mlflow.db",
            default_artifact_root="gs://oday-models/production",
            allowed_hosts="mlflow.internal.oday.invalid",
        ).validate()
    with pytest.raises(MlflowServerSettingsError, match="localhost"):
        MlflowServerSettings(
            backend_store_uri="postgresql://user@localhost/mlflow",
            default_artifact_root="gs://oday-models/production",
            allowed_hosts="mlflow.internal.oday.invalid",
        ).validate()
    with pytest.raises(MlflowServerSettingsError, match="gs://"):
        MlflowServerSettings(
            backend_store_uri="postgresql://user@10.0.0.3/mlflow",
            default_artifact_root="file:///tmp/mlruns",
            allowed_hosts="mlflow.internal.oday.invalid",
        ).validate()


def test_mlflow_server_command_uses_remote_backend_and_disables_artifact_proxy() -> None:
    settings = MlflowServerSettings(
        backend_store_uri="postgresql://runtime@10.0.0.3/mlflow",
        default_artifact_root="gs://oday-models/production",
        allowed_hosts="mlflow.internal.oday.invalid",
    )
    command = settings.server_command()
    assert command[:2] == ("mlflow", "server")
    assert "--no-serve-artifacts" in command
    assert "--allowed-hosts" in command
    assert "sqlite" not in " ".join(command)
    assert "file://" not in " ".join(command)
    assert settings.backend_store_uri not in command


def test_mlflow_server_accepts_only_exact_cloud_sql_socket_binding() -> None:
    instance = "alfaloop-data-project:asia-east1:oday-plus-dev-postgres"
    backend = (
        "postgresql://runtime:secret@/mlflow"
        f"?host=/cloudsql/{instance}"
    )
    settings = MlflowServerSettings(
        backend_store_uri=backend,
        default_artifact_root="gs://oday-models/production",
        allowed_hosts="oday-mlflow.internal",
        cloud_sql_instance=instance,
    )
    settings.validate()

    with pytest.raises(MlflowServerSettingsError, match="exact"):
        MlflowServerSettings(
            backend_store_uri=backend,
            default_artifact_root="gs://oday-models/production",
            allowed_hosts="oday-mlflow.internal",
        ).validate()
    with pytest.raises(MlflowServerSettingsError, match="exact"):
        MlflowServerSettings(
            backend_store_uri=backend,
            default_artifact_root="gs://oday-models/production",
            allowed_hosts="oday-mlflow.internal",
            cloud_sql_instance=(
                "alfaloop-data-project:asia-east1:different-postgres"
            ),
        ).validate()


def test_production_training_settings_fail_closed_on_local_or_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production_env(monkeypatch)
    assert ProductionTrainingSettings.from_environment().redacted_summary() == {
        "database_host": "10.20.30.40",
        "database_name": "oday_models",
        "mlflow_host": "mlflow.internal.oday.invalid",
        "artifact_bucket": "oday-model-artifacts",
        "artifact_prefix": "production",
        "git_sha": "0123456789abcdef",
        "actor": "ml-training-operator",
    }
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
    with pytest.raises(ModelTrainingConfigurationError, match="https"):
        ProductionTrainingSettings.from_environment()
    _production_env(monkeypatch)
    monkeypatch.setenv("ODP_MODEL_ARTIFACT_ROOT", "gs://example-bucket/change-me")
    with pytest.raises(ModelTrainingConfigurationError, match="placeholder"):
        ProductionTrainingSettings.from_environment()


def test_model_ready_sql_is_real_causal_and_blocks_missing_outcomes() -> None:
    sql = MODEL_READY_SQL_PATH.read_text(encoding="utf-8")
    lowered = sql.lower()
    assert "from core.transactions as txn" in lowered
    assert "inner join core.stores as store" in lowered
    assert "data_plane.canonical_lineage" in lowered
    assert "txn.transaction_status = 'succeeded'" in lowered
    assert "prior.date < target.date" in lowered
    assert "prior.date >= target.date - 28" in lowered
    assert "target.date - 1" in lowered
    assert "target.date - 7" in lowered
    assert "daily_net_revenue" in lowered
    assert "feature_snapshot_time" in lowered
    assert "prediction_origin_time" in lowered
    assert "label_maturity_time" in lowered
    assert "is_training_eligible" in lowered
    assert "tenant_id" in lowered
    assert "store_id" in lowered
    assert "forecast-training-view-v2" in lowered
    assert "mature_realized_transaction_outcome_relation_missing" in lowered
    assert "mature_candidate_site_outcome_relation_missing" in lowered
    assert "mature_liquidity_event_relation_missing" in lowered
    assert "create or replace view model_ready.valuation_view" not in lowered
    assert "create or replace view model_ready.candidate_site_view" not in lowered
    assert "create or replace view model_ready.avm_liquidity_training_view" not in lowered
    assert "asset.valuation_runs" not in lowered
    assert "expansion.site_score_runs" not in lowered
    for prohibited in ("generate_series(", "random(", "setseed(", "create table as"):
        assert prohibited not in lowered


def test_model_ready_view_installer_preflights_and_applies_one_sql_transaction() -> None:
    client = FakeInstallationClient()
    installer = ModelReadyViewInstaller(client)
    assert installer.preflight().ready
    result = installer.install()
    assert result["status"] == "installed"
    assert len(result["sql_sha256"]) == 64
    assert result["forecast"]["installer_sha256"] == result["sql_sha256"]
    assert result["optional_outcome_models_trainable"] is False
    assert client.transactions == 1
    assert any(
        "pg_advisory_xact_lock" in statement
        for statement, _params in client.executions
    )


def test_model_ready_view_install_and_inventory_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeInstallationClient(
        missing_relations=("data_plane.canonical_lineage",),
    )
    installer = ModelReadyViewInstaller(client)
    preflight = installer.preflight()
    assert not preflight.ready
    assert preflight.missing_relations == ("data_plane.canonical_lineage",)
    with pytest.raises(RuntimeError, match="prerequisites are incomplete"):
        installer.install()
    assert client.executions == []

    monkeypatch.setenv("ODAY_DATABASE_URL", "postgresql://user@localhost/oday")
    assert install_views_main(["inventory"]) == 2

    spec = MODEL_SPECS["forecastops"]
    source = PostgresModelReadySource(
        FakeQueryClient(
            columns=spec.required_columns,
            rows=_raw_forecast_rows(120),
            registry_exists=False,
        )
    )
    inventory = source.inventory(spec)
    assert not inventory.ready
    assert inventory.blocked_reason == "MODEL_READY_CONTRACT_REGISTRY_MISSING"
    assert inventory.to_dict()["ready"] is False


def test_gcs_artifact_store_is_content_addressed_and_verifies_bytes() -> None:
    transport = FakeGcsTransport()
    store = GcsArtifactStore(
        "gs://oday-model-artifacts/production",
        transport,
    )
    first = store.put_artifact(
        model_name="forecast_revenue_interval",
        version="2026.07.24.1",
        kind="model",
        data=b"actual-trained-model",
        metadata={"dataset_snapshot_id": "ds-001"},
    )
    second = store.put_artifact(
        model_name="forecast_revenue_interval",
        version="2026.07.24.1",
        kind="model",
        data=b"actual-trained-model",
        metadata={"dataset_snapshot_id": "ds-001"},
    )
    assert first.uri.startswith("gs://oday-model-artifacts/production/models/")
    assert first.content_digest.startswith("sha256:")
    assert second.uri == first.uri
    assert store.verify(first.artifact_id)
    assert store.verify_uri(first.uri, first.content_digest)
    assert store.open_artifact(first.artifact_id) == b"actual-trained-model"


def test_model_ready_inventory_reports_missing_realized_labels() -> None:
    spec = MODEL_SPECS["avm"]
    columns = tuple(
        name
        for name in spec.required_columns
        if name not in {spec.label_column, spec.temporal_column}
    )
    inventory = PostgresModelReadySource(
        FakeQueryClient(
            columns=columns,
            contract_version=spec.expected_view_version,
        )
    ).inventory(spec)
    assert not inventory.ready
    assert "realized_transaction_price" in inventory.missing_columns
    assert "realized_transaction_at" in inventory.missing_columns
    assert inventory.labeled_row_count == 0


def test_model_ready_inventory_reports_outcome_contract_block() -> None:
    spec = MODEL_SPECS["sitescore"]
    inventory = PostgresModelReadySource(
        FakeQueryClient(
            columns=spec.required_columns,
            contract_trainable=False,
            contract_version=spec.expected_view_version,
            blocked_reason="MATURE_CANDIDATE_SITE_OUTCOME_RELATION_MISSING",
        )
    ).inventory(spec)
    assert not inventory.ready
    assert not inventory.contract_trainable
    assert (
        inventory.blocked_reason
        == "MATURE_CANDIDATE_SITE_OUTCOME_RELATION_MISSING"
    )
    assert inventory.to_dict()["ready"] is False


def test_postgres_source_uses_bounded_ordered_query() -> None:
    spec = MODEL_SPECS["forecastops"]
    rows = _raw_forecast_rows(4)
    client = FakeQueryClient(columns=spec.required_columns, rows=rows)
    source = PostgresModelReadySource(client)
    bounds = DataBounds(
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 2, 1, tzinfo=UTC),
        3,
    )
    loaded = source.load(spec, bounds)
    assert len(loaded.rows) == 3
    sql, params = client.load_calls[-1]
    assert "is_training_eligible = true" in sql
    assert "daily_net_revenue IS NOT NULL" in sql
    assert "ORDER BY date, entity_id" in sql
    assert "LIMIT ?" in sql
    assert params == (bounds.start, bounds.end, 3)
    assert len(loaded.query_sha256) == 64


def test_prepare_rows_uses_canonical_lineage_and_never_fills_missing_features() -> None:
    rows = _raw_forecast_rows(120)
    rows[0]["rolling_mean_28"] = None
    prepared = prepare_model_rows(MODEL_SPECS["forecastops"], _loaded(rows))
    assert len(prepared) == 119
    first = prepared[0].mapping
    assert first["source_snapshot_ids"] == [
        "postgres:model_ready.forecast_training_view:sha256:" + "a" * 64,
        "snapshot-0001",
    ]
    assert set(first["features"]) == set(
        MODEL_SPECS["forecastops"].feature_columns
    )
    assert "daily_net_revenue" not in first["features"]
    assert first["labels"]["daily_net_revenue"] > 0


def test_prepare_rows_rejects_mock_fixture_or_seed_lineage() -> None:
    rows = _raw_forecast_rows(120)
    rows[0]["view_version"] = "fixture-v1"
    with pytest.raises(ModelReadyDataError, match="blocked source marker"):
        prepare_model_rows(MODEL_SPECS["forecastops"], _loaded(rows))


def test_prepare_rows_requires_source_lineage_and_strict_causal_time() -> None:
    rows = _raw_forecast_rows(120)
    rows[0]["source_snapshot_ids"] = []
    with pytest.raises(ModelReadyDataError, match="source snapshot lineage"):
        prepare_model_rows(MODEL_SPECS["forecastops"], _loaded(rows))

    rows = _raw_forecast_rows(120)
    rows[0]["prediction_origin_time"] = rows[0]["feature_snapshot_time"]
    with pytest.raises(ModelReadyDataError, match="must precede"):
        prepare_model_rows(MODEL_SPECS["forecastops"], _loaded(rows))


def test_temporal_validation_uses_future_holdout_and_segment_gates() -> None:
    prepared = prepare_model_rows(
        MODEL_SPECS["forecastops"],
        _loaded(_raw_forecast_rows(120)),
    )
    training, holdout = _temporal_split(prepared, holdout_fraction=0.20)
    assert max(row.temporal_value for row in training) < min(
        row.temporal_value for row in holdout
    )

    class PerfectEstimator:
        def predict(self, rows: list[dict[str, Any]]) -> tuple[float, ...]:
            return tuple(float(row["revenue_lag_1"]) + 5.0 for row in rows)

        def predict_interval(
            self,
            rows: list[dict[str, Any]],
        ) -> tuple[tuple[float, ...], tuple[float, ...]]:
            point = self.predict(rows)
            return (
                tuple(value - 20.0 for value in point),
                tuple(value + 20.0 for value in point),
            )

    def trainer(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            estimator=PerfectEstimator(),
            resolved_algorithm="lightgbm_quantile",
        )

    report = _validate_regression_temporally(
        MODEL_SPECS["forecastops"],
        training,
        holdout,
        trainer=trainer,
    )
    assert report.passed
    assert report.metrics["normalized_mae"] == 0.0
    assert report.metrics["p80_coverage"] == 1.0
    assert {segment["segment_value"] for segment in report.segments} == {
        "store-1",
        "store-2",
    }


def test_forecast_binding_executes_actual_lightgbm_temporal_training() -> None:
    pytest.importorskip("lightgbm")
    spec = replace(
        MODEL_SPECS["forecastops"],
        max_normalized_mae=2.0,
        min_p80_coverage=0.0,
    )
    prepared = prepare_model_rows(spec, _loaded(_raw_forecast_rows(120)))
    training, holdout = _temporal_split(prepared, holdout_fraction=0.20)
    report = _validate_regression_temporally(spec, training, holdout)
    assert report.algorithm == "lightgbm_quantile"
    assert report.training_rows + report.holdout_rows == 120
    assert report.passed
    assert np_is_finite(report.metrics["normalized_mae"])


def test_segment_validation_fails_when_holdout_has_no_sufficient_segment() -> None:
    spec = replace(MODEL_SPECS["forecastops"], minimum_segment_rows=100)
    prepared = prepare_model_rows(spec, _loaded(_raw_forecast_rows(120)))
    training, holdout = _temporal_split(prepared, holdout_fraction=0.20)

    class Estimator:
        def predict(self, rows: list[dict[str, Any]]) -> tuple[float, ...]:
            return tuple(float(row["revenue_lag_1"]) + 5.0 for row in rows)

        def predict_interval(
            self,
            rows: list[dict[str, Any]],
        ) -> tuple[tuple[float, ...], tuple[float, ...]]:
            point = self.predict(rows)
            return point, point

    report = _validate_regression_temporally(
        spec,
        training,
        holdout,
        trainer=lambda **_kwargs: SimpleNamespace(
            estimator=Estimator(),
            resolved_algorithm="lightgbm_quantile",
        ),
    )
    assert not report.passed
    assert "no store_id segment" in report.failed_rules[-1]


def test_promotion_approval_is_version_bound_and_prohibits_self_review() -> None:
    approval = require_approval_document(
        _approval(),
        model_name="forecast_revenue_interval",
        version="2026.07.24.1",
        requested_by="ml-training-operator",
    )
    assert approval["approval_id"] == "MRB-2026-0017"
    with pytest.raises(ModelTrainingConfigurationError, match="self-review"):
        require_approval_document(
            _approval(approver="ml-training-operator"),
            model_name="forecast_revenue_interval",
            version="2026.07.24.1",
            requested_by="ml-training-operator",
        )
    with pytest.raises(ModelTrainingConfigurationError, match="does not bind"):
        require_approval_document(
            _approval(model_version="2026.07.25.1"),
            model_name="forecast_revenue_interval",
            version="2026.07.24.1",
            requested_by="ml-training-operator",
        )
    with pytest.raises(ModelTrainingConfigurationError, match="credential fields"):
        require_approval_document(
            {**_approval(), "access_token": "must-not-be-here"},
            model_name="forecast_revenue_interval",
            version="2026.07.24.1",
            requested_by="ml-training-operator",
        )


def test_documented_package_contains_no_embedded_credentials() -> None:
    paths = (
        Path("infra/mlflow/Dockerfile"),
        Path("infra/mlflow/README.md"),
        Path("scripts/models/README.md"),
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "BEGIN PRIVATE KEY" not in combined
    assert "GOOGLE_APPLICATION_CREDENTIALS=" not in combined
    assert "postgresql://admin:password@" not in combined
    assert "service_account" not in combined.lower()
    approval_example = json.loads(
        """
        {
          "approval_id": "MRB-1",
          "decision": "approved"
        }
        """
    )
    assert "token" not in approval_example


def test_mlflow_image_installs_its_postgresql_runtime_driver() -> None:
    requirements = Path("infra/mlflow/requirements.txt").read_text(encoding="utf-8")
    dockerfile = Path("infra/mlflow/Dockerfile").read_text(encoding="utf-8")
    assert "psycopg2-binary==2.9.10" in requirements
    assert 'python -c "import mlflow, psycopg2"' in dockerfile


def np_is_finite(value: float) -> bool:
    return value == value and value not in {float("inf"), float("-inf")}
