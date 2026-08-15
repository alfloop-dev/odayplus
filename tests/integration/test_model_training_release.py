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
from models.shared_ml import MetricThreshold, ModelAlias, SegmentMetric
from modules.learninghub import (
    InMemoryLearningHubRepository,
    LearningHubConflictError,
    LearningHubService,
    ReleaseSagaState,
)
from product_ops.modeling.contracts import (
    MODEL_SPECS,
    DataBounds,
    ModelTrainingConfigurationError,
    ProductionTrainingSettings,
    require_approval_document,
    require_production_database_url,
)
from product_ops.modeling.install_views import (
    MODEL_READY_SQL_PATH,
    ModelReadyViewInstaller,
)
from product_ops.modeling.install_views import (
    main as install_views_main,
)
from product_ops.modeling.release import (
    BoundedModelTrainingRelease,
    _temporal_split,
    _validate_regression_temporally,
    prepare_model_rows,
)
from product_ops.modeling.release import (
    main as release_main,
)
from product_ops.modeling.storage import (
    GcsArtifactStore,
    GcsObject,
    LoadedModelReadyRows,
    ModelReadyDataError,
    PostgresModelReadySource,
)
from tests.integration._learninghub_fixtures import (
    DEFAULT_MODEL_NAME,
    dataset_rows,
    model_card,
    model_version,
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
        self.stats_sql: str | None = None

    def query_one(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> dict[str, Any] | None:
        if "to_regclass" in sql:
            if params[0] == "model_ready.view_contracts":
                return {
                    "relation": ("model_ready.view_contracts" if self.registry_exists else None)
                }
            return {"relation": params[0] if self.exists else None}
        if "FROM model_ready.view_contracts" in sql:
            return {
                "view_version": self.contract_version,
                "contract_state": ("ACTIVE" if self.contract_trainable else "BLOCKED"),
                "training_enabled": self.contract_trainable,
                "blocked_reason": self.blocked_reason,
                "installer_sha256": "b" * 64,
            }
        self.stats_sql = sql
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
        from product_ops.modeling.install_views import PREREQUISITE_COLUMNS

        self.columns = {
            relation: tuple(columns) for relation, columns in PREREQUISITE_COLUMNS.items()
        }
        self.relations = set(self.columns) - set(missing_relations)
        self.executions: list[tuple[str, tuple[Any, ...]]] = []
        self.transactions = 0
        self.transaction_active = False
        self.verification_transaction_states: list[bool] = []
        self.contracts: dict[str, dict[str, Any]] = {}
        self.prerequisite_counts = {
            "total_store_rows": 250,
            "stores_with_opened_on": 220,
            "stores_missing_opened_on": 30,
            "stores_missing_address": 5,
            "stores_missing_coordinates": 10,
            "stores_missing_h3_res_9": 12,
            "stores_missing_format": 3,
            "sitescore_anchor_prerequisite_rows": 205,
            "heatzone_cell_prerequisite_rows": 201,
            "successful_twd_transaction_rows": 10_000,
        }

    @contextmanager
    def transaction(self) -> Any:
        self.transactions += 1
        self.transaction_active = True
        try:
            yield self
        finally:
            self.transaction_active = False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.executions.append((sql, params))
        if "CREATE OR REPLACE VIEW model_ready.forecast_training_view" in sql:
            from product_ops.modeling.install_views import ACTIVE_VIEW_CONTRACTS

            for relation_name, version in ACTIVE_VIEW_CONTRACTS.items():
                self.relations.add(relation_name)
                self.contracts[relation_name] = {
                    "relation_name": relation_name,
                    "view_name": relation_name.rsplit(".", 1)[-1],
                    "view_version": version,
                    "contract_state": "ACTIVE",
                    "training_enabled": True,
                    "blocked_reason": None,
                    "installer_sha256": None,
                }
            official_ready = {
                "external_data.real_estate_transactions",
                "external_data.real_estate_ingestion_runs",
            }.issubset(self.relations)
            if official_ready:
                self.relations.add(
                    "model_ready.listing_property_valuation_view"
                )
            self.contracts["model_ready.valuation_view"] = {
                "relation_name": "model_ready.valuation_view",
                "view_name": "valuation_view",
                "view_version": "valuation-view-v1",
                "contract_state": "BLOCKED",
                "training_enabled": False,
                "blocked_reason": (
                    "MATURE_REALIZED_TRANSACTION_OUTCOME_RELATION_MISSING"
                ),
                "installer_sha256": None,
            }
            self.contracts["model_ready.listing_property_valuation_view"] = {
                "relation_name": "model_ready.listing_property_valuation_view",
                "view_name": "listing_property_valuation_view",
                "view_version": "listing-property-valuation-view-v1",
                "contract_state": "ACTIVE" if official_ready else "BLOCKED",
                "training_enabled": official_ready,
                "blocked_reason": (
                    None
                    if official_ready
                    else "OFFICIAL_REAL_ESTATE_OUTCOME_RELATION_MISSING"
                ),
                "installer_sha256": None,
            }
            self.contracts["model_ready.avm_liquidity_training_view"] = {
                "relation_name": "model_ready.avm_liquidity_training_view",
                "view_name": "avm_liquidity_training_view",
                "view_version": "avm-liquidity-training-view-v1",
                "contract_state": "BLOCKED",
                "training_enabled": False,
                "blocked_reason": (
                    "OFFICIAL_SALE_OUTCOME_HAS_NO_MARKETING_INTERVAL"
                ),
                "installer_sha256": None,
            }
        if sql.startswith("UPDATE model_ready.view_contracts"):
            for relation_name in params[1:]:
                if relation_name in self.contracts:
                    self.contracts[relation_name]["installer_sha256"] = params[0]

    def query_one(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> dict[str, Any] | None:
        if "to_regclass" in sql:
            if str(params[0]).startswith("model_ready."):
                self.verification_transaction_states.append(self.transaction_active)
            return {"relation": params[0] if params[0] in self.relations else None}
        if "AS total_store_rows" in sql:
            return dict(self.prerequisite_counts)
        return None

    def query(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> list[dict[str, Any]]:
        if "information_schema.columns" in sql:
            relation = f"{params[0]}.{params[1]}"
            return [{"column_name": column} for column in self.columns.get(relation, ())]
        if "FROM model_ready.view_contracts" in sql:
            self.verification_transaction_states.append(self.transaction_active)
            return [
                dict(self.contracts[relation])
                for relation in sorted(params or self.contracts)
                if relation in self.contracts
            ]
        return []


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
    dependencies = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    assert any(dependency.startswith("google-cloud-storage") for dependency in dependencies)


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
                "feature_snapshot_time": observed,
                "prediction_origin_time": observed + timedelta(microseconds=1),
                "label_maturity_time": observed + timedelta(days=1),
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
        as_of_time=datetime(2026, 8, 1, tzinfo=UTC),
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
    backend = f"postgresql://runtime:secret@/mlflow?host=/cloudsql/{instance}"
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
            cloud_sql_instance=("alfaloop-data-project:asia-east1:different-postgres"),
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


def test_model_ready_sql_is_real_causal_and_activates_supported_outcomes() -> None:
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
    assert "from external_data.real_estate_transactions as outcome" in lowered
    assert "external_data.real_estate_ingestion_runs as ingestion" in lowered
    assert "government-open-data-license-v1" in lowered
    assert "total_price_twd::double precision as realized_transaction_price" in lowered
    assert "listing-property-valuation-view-v1" in lowered
    assert "official_sale_outcome_has_no_marketing_interval" in lowered
    assert "mature_realized_transaction_outcome_relation_missing" in lowered
    assert "mature_liquidity_event_relation_missing" in lowered
    assert "create or replace view model_ready.candidate_site_view" in lowered
    assert "candidate-site-view-v2" in lowered
    assert "realized_90d_net_revenue" in lowered
    assert "anchor.feature_cutoff_time - interval '90 days'" in lowered
    assert "source_txn.event_time < anchor.feature_cutoff_time" in lowered
    assert "create or replace view model_ready.heatzone_training_view" in lowered
    assert "heatzone-training-view-v2" in lowered
    assert "realized_28d_cell_net_revenue" in lowered
    assert "origin.feature_cutoff_time - interval '90 days'" in lowered
    assert "source_txn.event_time < origin.feature_cutoff_time" in lowered
    assert "source_txn.event_time >= origin.feature_cutoff_time" in lowered
    assert "count(distinct day.partition_date)" in lowered
    assert "identity_available_at < feature_cutoff_time" in lowered
    assert "authority.source_kind = 'orders'" in lowered
    assert "ingestion.reconciled" in lowered
    assert "ingestion.partition_complete" in lowered
    assert "from data_plane.place_geography as place" in lowered
    assert "place.valid_from <= txn.event_time" in lowered
    assert "create or replace view model_ready.valuation_view" not in lowered
    assert "create or replace view model_ready.avm_liquidity_training_view" not in lowered
    assert "asset.valuation_runs" not in lowered
    assert "expansion.site_score_runs" not in lowered
    for prohibited in ("generate_series(", "random(", "setseed(", "create table as"):
        assert prohibited not in lowered


def test_model_ready_view_installer_preflights_and_applies_one_sql_transaction() -> None:
    client = FakeInstallationClient()
    client.contracts["model_ready.unrelated_view"] = {
        "relation_name": "model_ready.unrelated_view",
        "view_name": "unrelated_view",
        "view_version": "unrelated-v1",
        "contract_state": "ACTIVE",
        "training_enabled": True,
        "blocked_reason": None,
        "installer_sha256": "unchanged",
    }
    installer = ModelReadyViewInstaller(client)
    preflight = installer.preflight()
    assert preflight.ready
    inventory = preflight.to_dict()
    assert inventory["optional_outcome_models"]["sitescore"]["contract_installable"] is True
    assert inventory["optional_outcome_models"]["heatzone"]["contract_installable"] is True
    assert (
        inventory["eligible_row_prerequisite_evidence"]["counts"]["stores_missing_opened_on"] == 30
    )
    result = installer.install()
    assert result["status"] == "installed"
    assert len(result["sql_sha256"]) == 64
    assert set(result["active_contracts"]) == {
        "model_ready.forecast_training_view",
        "model_ready.candidate_site_view",
        "model_ready.heatzone_training_view",
    }
    assert all(
        contract["installer_sha256"] == result["sql_sha256"]
        for contract in result["active_contracts"].values()
    )
    assert result["eligible_row_prerequisite_evidence"]["sitescore_anchor_prerequisite_rows"] == 205
    assert result["minimum_data_checks"] == "trainer"
    assert client.transactions == 1
    assert client.contracts["model_ready.unrelated_view"]["installer_sha256"] == "unchanged"
    assert client.verification_transaction_states
    assert all(client.verification_transaction_states)
    assert any("pg_advisory_xact_lock" in statement for statement, _params in client.executions)


def test_model_ready_view_preflight_requires_opening_and_geo_columns() -> None:
    client = FakeInstallationClient()
    client.columns["core.stores"] = tuple(
        column for column in client.columns["core.stores"] if column != "opened_on"
    )
    client.columns["data_plane.place_geography"] = tuple(
        column for column in client.columns["data_plane.place_geography"] if column != "h3_res_9"
    )

    preflight = ModelReadyViewInstaller(client).preflight()

    assert not preflight.ready
    assert preflight.missing_columns == {
        "core.stores": ("opened_on",),
        "data_plane.place_geography": ("h3_res_9",),
    }
    assert preflight.prerequisite_counts == {}


def test_model_ready_view_install_rejects_non_active_geo_contract() -> None:
    class InactiveGeoContractClient(FakeInstallationClient):
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
            super().execute(sql, params)
            if "CREATE OR REPLACE VIEW model_ready.forecast_training_view" in sql:
                self.contracts["model_ready.heatzone_training_view"]["contract_state"] = "BLOCKED"

    with pytest.raises(
        RuntimeError,
        match="model_ready.heatzone_training_view: not ACTIVE",
    ):
        client = InactiveGeoContractClient()
        ModelReadyViewInstaller(client).install()
    assert client.verification_transaction_states
    assert all(client.verification_transaction_states)


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


def test_model_ready_view_install_keeps_forecast_independent_of_official_data() -> None:
    client = FakeInstallationClient(
        missing_relations=(
            "external_data.real_estate_transactions",
            "external_data.real_estate_ingestion_runs",
        ),
    )
    installer = ModelReadyViewInstaller(client)

    preflight = installer.preflight()
    assert preflight.ready
    assert not preflight.official_outcomes_ready
    assert set(preflight.optional_missing_relations) == {
        "external_data.real_estate_transactions",
        "external_data.real_estate_ingestion_runs",
    }

    result = installer.install()

    assert result["forecast"]["contract_state"] == "ACTIVE"
    assert result["listing_property_avm"]["contract_state"] == "BLOCKED"
    assert (
        result["listing_property_avm"]["blocked_reason"]
        == "OFFICIAL_REAL_ESTATE_OUTCOME_RELATION_MISSING"
    )
    assert not result["optional_outcome_models_trainable"][
        "listing_property_avm"
    ]


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


def test_global_official_outcome_inventory_does_not_require_tenant_column() -> None:
    spec = MODEL_SPECS["listing_property_avm"]
    client = FakeQueryClient(
        columns=spec.required_columns,
        contract_version=spec.expected_view_version,
    )

    PostgresModelReadySource(client).inventory(spec)

    assert client.stats_sql is not None
    assert "NULL::text AS tenant_id" in client.stats_sql


def test_sitescore_model_spec_binds_real_opened_store_outcome_contract() -> None:
    spec = MODEL_SPECS["sitescore"]
    assert spec.expected_view_version == "candidate-site-view-v2"
    assert spec.label_column == "realized_90d_net_revenue"
    assert spec.temporal_column == "opened_on"
    assert spec.label_maturity_column == "label_maturity_time"
    assert spec.segment_column == "target_format_code"
    assert {
        "tenant_id",
        "store_id",
        "h3_index",
        "prior_90d_cell_net_revenue",
        "prior_90d_cell_transaction_count",
        "prior_90d_cell_store_count",
    } <= set(spec.required_columns)
    assert {"rent_amount", "area_ping", "frontage_m", "rent_per_ping"}.isdisjoint(
        spec.required_columns
    )
    assert spec.output_transform == {
        "version": "sitescore-90d-net-revenue-to-mature-monthly-v1",
        "kind": "fixed_horizon_sum_to_monthly_rate",
        "input_unit": "TWD_NET_REVENUE_90D",
        "output_unit": "TWD_NET_REVENUE_MONTHLY",
        "horizon_days": 90,
        "days_per_month": 30.4375,
    }


def test_heatzone_model_spec_binds_real_point_in_time_cell_outcome_contract() -> None:
    spec = MODEL_SPECS["heatzone"]
    assert spec.algorithm == "catboost_regressor"
    assert spec.model_name == "heatzone_priority"
    assert spec.expected_view_version == "heatzone-training-view-v2"
    assert spec.label_column == "realized_28d_cell_net_revenue"
    assert spec.temporal_column == "origin_date"
    assert spec.label_maturity_column == "label_maturity_time"
    assert spec.segment_column == "h3_index"
    assert {
        "tenant_id",
        "h3_index",
        "prior_28d_cell_net_revenue",
        "prior_90d_cell_net_revenue",
        "prior_28d_transaction_count",
        "prior_90d_transaction_count",
        "prior_90d_transaction_days",
    } <= set(spec.required_columns)
    assert {
        "poi_count",
        "competitor_count",
        "active_listing_count",
        "median_listing_rent",
    }.isdisjoint(spec.required_columns)
    assert spec.output_transform == {
        "version": "heatzone-28d-revenue-percentile-priority-v1",
        "kind": "batch_percentile_rank",
        "input_unit": "TWD_NET_REVENUE_28D",
        "output_unit": "PRIORITY_SCORE_0_100",
        "direction": "higher_is_better",
        "tie_method": "average",
    }


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
    # 120 daily rows = 60 days x 2 stores -> 33 w4 + 5 w8 horizon rows per
    # store; the store-1 day-0 origin with the missing feature drops its two
    # horizon rows.
    assert len(prepared) == 74
    first = prepared[0].mapping
    lineage = "postgres:model_ready.forecast_training_view:sha256:" + "a" * 64
    assert lineage in first["source_snapshot_ids"]
    # lineage plus the 28 daily source snapshots of the w4 observation window
    assert len(first["source_snapshot_ids"]) == 29
    assert set(first["features"]) == set(MODEL_SPECS["forecastops"].feature_columns)
    assert "daily_net_revenue" not in first["features"]
    assert first["labels"][MODEL_SPECS["forecastops"].label_name] > 0


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


def test_prepare_rows_allows_labels_to_mature_after_prediction_origin() -> None:
    prepared = prepare_model_rows(MODEL_SPECS["forecastops"], _loaded(_raw_forecast_rows(120)))

    assert (
        prepared[0].mapping["feature_snapshot_time"] < prepared[0].mapping["prediction_origin_time"]
    )
    assert (
        prepared[0].mapping["prediction_origin_time"] < prepared[0].mapping["label_maturity_time"]
    )
    assert prepared[0].mapping["label_maturity_time"] <= prepared[0].mapping["training_as_of_time"]


def test_prepare_rows_rejects_labels_not_mature_at_training_as_of() -> None:
    rows = _raw_forecast_rows(120)
    for row in rows:
        row["label_maturity_time"] = datetime(2026, 8, 2, tzinfo=UTC)

    with pytest.raises(ModelReadyDataError, match="training as_of_time"):
        prepare_model_rows(MODEL_SPECS["forecastops"], _loaded(rows))


def test_temporal_validation_uses_future_holdout_and_segment_gates() -> None:
    prepared = prepare_model_rows(
        MODEL_SPECS["forecastops"],
        _loaded(_raw_forecast_rows(120)),
    )
    training, holdout = _temporal_split(prepared, holdout_fraction=0.20)
    assert max(row.temporal_value for row in training) < min(row.temporal_value for row in holdout)

    class PerfectEstimator:
        def predict(self, rows: list[dict[str, Any]]) -> tuple[float, ...]:
            # daily revenue rises 10/day per store, so the horizon average is
            # revenue_lag_1 + 5 + 5 * (horizon_days - 1) = lag + 35 * weeks.
            return tuple(
                float(row["revenue_lag_1"]) + 35.0 * float(row["horizon_weeks"])
                for row in rows
            )

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
    assert report.algorithm == "lightgbm_regressor"
    assert report.training_rows + report.holdout_rows == 76
    assert report.passed
    assert np_is_finite(report.metrics["normalized_mae"])


def test_segment_validation_fails_when_holdout_has_no_sufficient_segment() -> None:
    spec = replace(MODEL_SPECS["forecastops"], minimum_segment_rows=100)
    prepared = prepare_model_rows(spec, _loaded(_raw_forecast_rows(120)))
    training, holdout = _temporal_split(prepared, holdout_fraction=0.20)

    class Estimator:
        def predict(self, rows: list[dict[str, Any]]) -> tuple[float, ...]:
            return tuple(
                float(row["revenue_lag_1"]) + 35.0 * float(row["horizon_weeks"])
                for row in rows
            )

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


class _RacedReleaseRepository(InMemoryLearningHubRepository):
    """Serves one stale release revision, as if a concurrent release landed
    between the promotion wrapper's revision read and the release guard."""

    stale_revision_once: int | None = None

    def get_release_revision(self, model_name: str) -> int:
        if self.stale_revision_once is not None:
            stale, self.stale_revision_once = self.stale_revision_once, None
            return stale
        return super().get_release_revision(model_name)


def _promotion_application(
    repository: InMemoryLearningHubRepository | None = None,
) -> BoundedModelTrainingRelease:
    service = LearningHubService(repository=repository)
    store = GcsArtifactStore(
        "gs://oday-model-artifacts/production",
        FakeGcsTransport(),
    )
    return BoundedModelTrainingRelease(
        source=SimpleNamespace(),
        service=service,
        artifact_store=store,
        git_sha="abc1234",
        actor="ml-training-operator",
    )


def _register_promotable_candidate(
    application: BoundedModelTrainingRelease,
    version: str,
) -> None:
    service = application.service
    snapshot = service.register_dataset_snapshot(
        dataset_rows(),
        dataset_snapshot_id=f"{DEFAULT_MODEL_NAME}-training-{version}",
    )
    validation = service.validate_candidate(
        model_name=DEFAULT_MODEL_NAME,
        model_version=version,
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        metrics={"w4_smape": 0.11, "p80_coverage": 0.82},
        baseline_metrics={"w4_smape": 0.15, "p80_coverage": 0.78},
        thresholds=(
            MetricThreshold("w4_smape", max_value=0.12),
            MetricThreshold("p80_coverage", min_value=0.80),
        ),
        segment_metrics=(
            SegmentMetric(
                segment_name="region",
                segment_value="north",
                metrics={"w4_smape": 0.10},
                record_count=1,
            ),
        ),
        calibration_summary={"p80_coverage": 0.82},
    )
    assert validation.passed
    artifact = application.artifact_store.put_artifact(
        model_name=DEFAULT_MODEL_NAME,
        version=version,
        kind="model",
        data=f"trained-model-{version}".encode(),
        metadata={"dataset_snapshot_id": snapshot.dataset_snapshot_id},
    )
    candidate = model_version(version, snapshot.dataset_snapshot_id)._replace(
        artifact_uri=artifact.uri,
        monitoring_config={"artifact_sha256": artifact.content_digest},
    )
    service.register_model_version(
        model_version=candidate,
        model_card=model_card(
            version,
            snapshot.dataset_snapshot_id,
            validation.validation_run_id,
        ),
        validation_run=validation,
    )


def test_promote_first_success_and_identical_replay_share_one_durable_release() -> None:
    spec = MODEL_SPECS["forecastops"]
    assert spec.production_release_enabled
    assert spec.model_name == DEFAULT_MODEL_NAME
    application = _promotion_application()
    repository = application.service.repository
    version = "2026.07.24.1"
    _register_promotable_candidate(application, version)
    approval_payload = _approval(model_version=version)

    first = application.promote(
        spec=spec,
        version=version,
        approval_payload=approval_payload,
        rollback_target=None,
    )

    assert first["status"] == "promoted"
    assert first["replayed"] is False
    assert first["release_revision"] == 1
    assert repository.get_alias(DEFAULT_MODEL_NAME, ModelAlias.SHADOW).version == version
    sagas = repository.list_release_sagas(DEFAULT_MODEL_NAME)
    assert [saga.state for saga in sagas] == [ReleaseSagaState.COMPLETED]
    approvals_after_first = repository.get_model_card(DEFAULT_MODEL_NAME, version).approvals

    # Identical replay against the normal post-commit repository state: the
    # release revision has already advanced, so only approval-bound saga
    # resolution can return the durable decision instead of a conflict.
    replay = application.promote(
        spec=spec,
        version=version,
        approval_payload=approval_payload,
        rollback_target=None,
    )

    assert replay["status"] == "promoted"
    assert replay["replayed"] is True
    assert replay["release_id"] == first["release_id"]
    assert replay["release_revision"] == first["release_revision"]
    assert replay["approval_sha256"] == first["approval_sha256"]
    assert repository.get_release_revision(DEFAULT_MODEL_NAME) == 1
    assert len(repository.list_release_sagas(DEFAULT_MODEL_NAME)) == 1
    assert len(repository.list_release_decisions()) == 1
    assert repository.get_alias(DEFAULT_MODEL_NAME, ModelAlias.SHADOW).version == version
    assert (
        repository.get_model_card(DEFAULT_MODEL_NAME, version).approvals
        == approvals_after_first
    )


def test_promote_rejects_reusing_an_approval_for_a_different_command() -> None:
    spec = MODEL_SPECS["forecastops"]
    application = _promotion_application()
    version = "2026.07.24.1"
    _register_promotable_candidate(application, version)
    application.promote(
        spec=spec,
        version=version,
        approval_payload=_approval(model_version=version),
        rollback_target=None,
    )
    with pytest.raises(ModelTrainingConfigurationError, match="different release command"):
        application.promote(
            spec=spec,
            version=version,
            approval_payload=_approval(
                model_version=version,
                release_type="canary",
            ),
            rollback_target="2026.07.23.1",
        )


@pytest.mark.parametrize(
    ("field", "mutated_value"),
    (
        ("reason", "Approval reason was changed after commit"),
        ("approver", "different-reviewer"),
        ("approved_at", "2026-07-24T12:00:01Z"),
    ),
)
def test_promote_replay_rejects_post_commit_approval_mutation(
    field: str,
    mutated_value: str,
) -> None:
    spec = MODEL_SPECS["forecastops"]
    application = _promotion_application()
    version = "2026.07.24.1"
    _register_promotable_candidate(application, version)
    approval = _approval(model_version=version)
    application.promote(
        spec=spec,
        version=version,
        approval_payload=approval,
        rollback_target=None,
    )

    with pytest.raises(ModelTrainingConfigurationError, match="different release command"):
        application.promote(
            spec=spec,
            version=version,
            approval_payload={**approval, field: mutated_value},
            rollback_target=None,
        )


def test_promote_replay_rejects_mutated_persisted_approval_digest() -> None:
    spec = MODEL_SPECS["forecastops"]
    application = _promotion_application()
    repository = application.service.repository
    version = "2026.07.24.1"
    _register_promotable_candidate(application, version)
    approval = _approval(model_version=version)
    application.promote(
        spec=spec,
        version=version,
        approval_payload=approval,
        rollback_target=None,
    )
    saga = repository.list_release_sagas(DEFAULT_MODEL_NAME)[0]
    repository.save_release_saga(
        saga.evolve(
            command={
                **saga.command,
                "approval_sha256": "sha256:" + "0" * 64,
            }
        )
    )

    with pytest.raises(ModelTrainingConfigurationError, match="different release command"):
        application.promote(
            spec=spec,
            version=version,
            approval_payload=approval,
            rollback_target=None,
        )


def test_promote_incomplete_saga_replay_requires_operator_recovery() -> None:
    spec = MODEL_SPECS["forecastops"]
    application = _promotion_application()
    version = "2026.07.24.1"
    _register_promotable_candidate(application, version)
    application.promote(
        spec=spec,
        version=version,
        approval_payload=_approval(model_version=version),
        rollback_target=None,
    )
    repository = application.service.repository
    saga = repository.list_release_sagas(DEFAULT_MODEL_NAME)[0]
    repository.save_release_saga(saga.evolve(state=ReleaseSagaState.COMPENSATING))
    with pytest.raises(ModelTrainingConfigurationError, match="operator recovery"):
        application.promote(
            spec=spec,
            version=version,
            approval_payload=_approval(model_version=version),
            rollback_target=None,
        )


def test_promote_rejects_stale_release_revision_without_mutation() -> None:
    spec = MODEL_SPECS["forecastops"]
    repository = _RacedReleaseRepository()
    application = _promotion_application(repository)
    first_version = "2026.07.24.1"
    _register_promotable_candidate(application, first_version)
    application.promote(
        spec=spec,
        version=first_version,
        approval_payload=_approval(model_version=first_version),
        rollback_target=None,
    )
    assert repository.get_release_revision(DEFAULT_MODEL_NAME) == 1

    second_version = "2026.07.25.1"
    _register_promotable_candidate(application, second_version)
    repository.stale_revision_once = 0
    with pytest.raises(LearningHubConflictError, match="release revision conflict"):
        application.promote(
            spec=spec,
            version=second_version,
            approval_payload=_approval(
                approval_id="MRB-2026-0018",
                model_version=second_version,
            ),
            rollback_target=None,
        )

    assert repository.get_release_revision(DEFAULT_MODEL_NAME) == 1
    assert repository.get_alias(DEFAULT_MODEL_NAME, ModelAlias.SHADOW).version == first_version
    assert repository.get_alias(DEFAULT_MODEL_NAME, ModelAlias.PRODUCTION) is None
    sagas = repository.list_release_sagas(DEFAULT_MODEL_NAME)
    assert len(sagas) == 1
    assert sagas[0].command["version"] == first_version


def test_listing_property_avm_production_promotion_is_explicitly_blocked() -> None:
    application = BoundedModelTrainingRelease.__new__(
        BoundedModelTrainingRelease
    )
    spec = MODEL_SPECS["listing_property_avm"]

    assert not spec.production_release_enabled
    assert (
        spec.production_block_reason
        == "NO_PRODUCTION_RUNTIME_CONSUMER_OR_LIVE_INFERENCE_SMOKE"
    )
    with pytest.raises(
        ModelTrainingConfigurationError,
        match="NO_PRODUCTION_RUNTIME_CONSUMER_OR_LIVE_INFERENCE_SMOKE",
    ):
        application.promote(
            spec=spec,
            version="2026.07.26.1",
            approval_payload=_approval(
                model_name="listing_property_avm",
                model_version="2026.07.26.1",
                release_type="full",
            ),
            rollback_target="2026.07.25.1",
        )


@pytest.mark.parametrize("release_type", ("full", "canary"))
def test_listing_property_avm_production_cli_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    release_type: str,
) -> None:
    _production_env(monkeypatch)
    approval_path = tmp_path / f"{release_type}-approval.json"
    approval_path.write_text(
        json.dumps(
            _approval(
                model_name="listing_property_avm",
                model_version="2026.07.26.1",
                release_type=release_type,
            )
        ),
        encoding="utf-8",
    )
    application = BoundedModelTrainingRelease.__new__(
        BoundedModelTrainingRelease
    )
    resources = SimpleNamespace(
        application=application,
        close=lambda: None,
    )

    exit_code = release_main(
        [
            "promote",
            "--model",
            "listing_property_avm",
            "--version",
            "2026.07.26.1",
            "--approval-file",
            str(approval_path),
            "--rollback-target",
            "2026.07.25.1",
        ],
        resource_builder=lambda _settings: resources,
    )

    assert exit_code == 2
    failure = json.loads(capsys.readouterr().err)
    assert failure["status"] == "failed-closed"
    assert "production release is BLOCKED" in failure["message"]
    assert (
        "NO_PRODUCTION_RUNTIME_CONSUMER_OR_LIVE_INFERENCE_SMOKE"
        in failure["message"]
    )


def test_documented_package_contains_no_embedded_credentials() -> None:
    paths = (
        Path("infra/mlflow/Dockerfile"),
        Path("infra/mlflow/README.md"),
        Path("product_ops/modeling/README.md"),
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
