from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlparse

_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}
_PLACEHOLDER_TOKENS = (
    "<",
    ">",
    "change-me",
    "changeme",
    "example",
    "placeholder",
    "your-",
)
MAX_TRAINING_ROWS = 250_000


class ModelTrainingConfigurationError(RuntimeError):
    """Raised when production model training cannot be bound safely."""


class ModelKind(StrEnum):
    REGRESSION = "regression"
    SURVIVAL = "survival"


@dataclass(frozen=True)
class DataBounds:
    start: datetime
    end: datetime
    max_rows: int

    def __post_init__(self) -> None:
        start = _aware(self.start)
        end = _aware(self.end)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        if start >= end:
            raise ModelTrainingConfigurationError("training start must precede end")
        if self.max_rows < 2 or self.max_rows > MAX_TRAINING_ROWS:
            raise ModelTrainingConfigurationError(
                f"max_rows must be between 2 and {MAX_TRAINING_ROWS}"
            )

    @classmethod
    def parse(cls, *, start: str, end: str, max_rows: int) -> DataBounds:
        try:
            return cls(
                start=datetime.fromisoformat(start.replace("Z", "+00:00")),
                end=datetime.fromisoformat(end.replace("Z", "+00:00")),
                max_rows=max_rows,
            )
        except ValueError as exc:
            raise ModelTrainingConfigurationError(
                "training bounds must be ISO-8601 timestamps"
            ) from exc


@dataclass(frozen=True)
class ModelSpec:
    key: str
    model_name: str
    relation: str
    expected_view_version: str
    kind: ModelKind
    algorithm: str
    label_name: str
    label_column: str
    label_version: str
    feature_schema_version: str
    feature_set_id: str
    label_set_id: str
    temporal_column: str
    segment_column: str
    feature_columns: tuple[str, ...]
    minimum_rows: int
    holdout_fraction: float
    minimum_segment_rows: int
    max_normalized_mae: float
    min_p80_coverage: float
    intended_use: str
    not_intended_use: str
    risk_level: str = "R3"
    event_column: str | None = None
    label_maturity_column: str | None = None
    scope_columns: tuple[str, ...] = ()

    @property
    def required_columns(self) -> tuple[str, ...]:
        common = (
            "view_name",
            "view_version",
            "entity_id",
            "feature_snapshot_time",
            "prediction_origin_time",
            "source_snapshot_ids",
            "is_training_eligible",
        )
        optional_event = (self.event_column,) if self.event_column else ()
        optional_maturity = (
            (self.label_maturity_column,) if self.label_maturity_column else ()
        )
        return tuple(
            dict.fromkeys(
                (
                    *common,
                    self.temporal_column,
                    self.segment_column,
                    self.label_column,
                    *optional_event,
                    *optional_maturity,
                    *self.scope_columns,
                    *self.feature_columns,
                )
            )
        )


MODEL_SPECS: dict[str, ModelSpec] = {
    "forecastops": ModelSpec(
        key="forecastops",
        model_name="forecast_revenue_interval",
        relation="model_ready.forecast_training_view",
        expected_view_version="forecast-training-view-v2",
        kind=ModelKind.REGRESSION,
        algorithm="lightgbm_quantile",
        label_name="daily_net_revenue",
        label_column="daily_net_revenue",
        label_version="forecast-daily-net-revenue-v1",
        feature_schema_version="forecast-training-view-v2",
        feature_set_id="fs_forecastops_daily_revenue_v1",
        label_set_id="ls_forecastops_daily_revenue_v1",
        temporal_column="date",
        label_maturity_column="label_maturity_time",
        segment_column="store_id",
        feature_columns=(
            "tenant_id",
            "store_id",
            "revenue_lag_1",
            "revenue_lag_7",
            "rolling_mean_7",
            "rolling_mean_28",
        ),
        scope_columns=("tenant_id", "store_id"),
        minimum_rows=90,
        holdout_fraction=0.20,
        minimum_segment_rows=7,
        max_normalized_mae=0.35,
        min_p80_coverage=0.65,
        intended_use="ForecastOps store-level daily net revenue interval planning",
        not_intended_use=(
            "Automated store closure, employee decisions, or unsupervised financial commitments"
        ),
    ),
    "avm": ModelSpec(
        key="avm",
        model_name="dealroom_avm",
        relation="model_ready.valuation_view",
        expected_view_version="valuation-view-v1",
        kind=ModelKind.REGRESSION,
        algorithm="lightgbm_quantile",
        label_name="realized_transaction_price",
        label_column="realized_transaction_price",
        label_version="avm-realized-transaction-price-v1",
        feature_schema_version="valuation-view-v1",
        feature_set_id="fs_avm_realized_value_v1",
        label_set_id="ls_avm_realized_value_v1",
        temporal_column="realized_transaction_at",
        label_maturity_column="realized_transaction_at",
        segment_column="store_id",
        feature_columns=(
            "tenant_id",
            "store_id",
            "gm_ttm",
            "gm_fwd_p10",
            "gm_fwd_p50",
            "gm_fwd_p90",
            "asset_book_value",
            "lease_remaining_months",
            "rent_amount",
            "forecast_confidence",
            "comparable_count",
        ),
        scope_columns=("tenant_id", "store_id"),
        minimum_rows=120,
        holdout_fraction=0.20,
        minimum_segment_rows=5,
        max_normalized_mae=0.30,
        min_p80_coverage=0.70,
        intended_use="Human-reviewed asset valuation range support",
        not_intended_use="Automatic acquisition, disposal, or binding fair-value approval",
        risk_level="R4",
    ),
    "sitescore": ModelSpec(
        key="sitescore",
        model_name="sitescore_propensity",
        relation="model_ready.candidate_site_view",
        expected_view_version="candidate-site-view-v1",
        kind=ModelKind.REGRESSION,
        algorithm="catboost_regressor",
        label_name="realized_site_success",
        label_column="realized_site_success",
        label_version="sitescore-realized-success-v1",
        feature_schema_version="candidate-site-view-v1",
        feature_set_id="fs_sitescore_realized_success_v1",
        label_set_id="ls_sitescore_realized_success_v1",
        temporal_column="realized_outcome_at",
        label_maturity_column="realized_outcome_at",
        segment_column="target_format_code",
        feature_columns=(
            "tenant_id",
            "target_format_code",
            "rent_amount",
            "area_ping",
            "frontage_m",
            "floor",
            "geocode_confidence",
            "rent_per_ping",
        ),
        scope_columns=("tenant_id",),
        minimum_rows=200,
        holdout_fraction=0.20,
        minimum_segment_rows=10,
        max_normalized_mae=0.25,
        min_p80_coverage=0.70,
        intended_use="Human-reviewed Candidate Site prioritization",
        not_intended_use="Automatic site promotion, lease approval, or ambiguous identity merge",
        risk_level="R4",
    ),
    "avm-liquidity": ModelSpec(
        key="avm-liquidity",
        model_name="avm_liquidity",
        relation="model_ready.avm_liquidity_training_view",
        expected_view_version="avm-liquidity-training-view-v1",
        kind=ModelKind.SURVIVAL,
        algorithm="lifelines_coxph",
        label_name="duration_days",
        label_column="duration_days",
        label_version="avm-liquidity-observed-sale-v1",
        feature_schema_version="avm-liquidity-training-view-v1",
        feature_set_id="fs_avm_liquidity_v1",
        label_set_id="ls_avm_liquidity_v1",
        temporal_column="observation_date",
        segment_column="market_segment",
        feature_columns=(
            "tenant_id",
            "store_id",
            "rent_per_ping",
            "area_ping",
            "frontage_m",
            "price_to_gm",
            "listing_age_days",
        ),
        scope_columns=("tenant_id", "store_id"),
        event_column="sold",
        label_maturity_column="observation_date",
        minimum_rows=150,
        holdout_fraction=0.20,
        minimum_segment_rows=10,
        max_normalized_mae=0.45,
        min_p80_coverage=0.0,
        intended_use="Human-reviewed liquidity horizon estimation for AVM",
        not_intended_use="Automatic sale, acquisition, or binding valuation decision",
        risk_level="R4",
    ),
}


@dataclass(frozen=True)
class ProductionTrainingSettings:
    database_url: str
    mlflow_tracking_uri: str
    artifact_root: str
    git_sha: str
    actor: str

    @classmethod
    def from_environment(cls) -> ProductionTrainingSettings:
        settings = cls(
            database_url=os.getenv("ODAY_DATABASE_URL", "").strip(),
            mlflow_tracking_uri=os.getenv("MLFLOW_TRACKING_URI", "").strip(),
            artifact_root=os.getenv(
                "ODP_MODEL_ARTIFACT_ROOT",
                os.getenv("MLFLOW_DEFAULT_ARTIFACT_ROOT", ""),
            ).strip(),
            git_sha=os.getenv("ODP_RELEASE_COMMIT_SHA", "").strip(),
            actor=os.getenv("ODP_MODEL_TRAINING_ACTOR", "").strip(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        require_production_database_url(self.database_url)
        _require_remote_url(
            self.mlflow_tracking_uri,
            field="MLFLOW_TRACKING_URI",
            schemes={"https"},
        )
        _require_gcs_root(self.artifact_root)
        if not self.git_sha or len(self.git_sha) < 7:
            raise ModelTrainingConfigurationError(
                "ODP_RELEASE_COMMIT_SHA must identify the exact source commit"
            )
        _reject_placeholder(self.git_sha, "ODP_RELEASE_COMMIT_SHA")
        if not self.actor:
            raise ModelTrainingConfigurationError(
                "ODP_MODEL_TRAINING_ACTOR is required"
            )
        _reject_placeholder(self.actor, "ODP_MODEL_TRAINING_ACTOR")

    def redacted_summary(self) -> dict[str, str]:
        database = urlparse(self.database_url)
        registry = urlparse(self.mlflow_tracking_uri)
        artifacts = urlparse(self.artifact_root)
        return {
            "database_host": database.hostname or "",
            "database_name": database.path.lstrip("/"),
            "mlflow_host": registry.hostname or "",
            "artifact_bucket": artifacts.netloc,
            "artifact_prefix": artifacts.path.lstrip("/"),
            "git_sha": self.git_sha,
            "actor": self.actor,
        }


def require_approval_document(
    payload: dict[str, object],
    *,
    model_name: str,
    version: str,
    requested_by: str,
) -> dict[str, str]:
    prohibited_keys = sorted(
        key
        for key in payload
        if any(
            marker in key.lower()
            for marker in ("credential", "cookie", "password", "private_key", "secret", "token")
        )
    )
    if prohibited_keys:
        raise ModelTrainingConfigurationError(
            "approval document contains prohibited credential fields: "
            + ", ".join(prohibited_keys)
        )
    required = {
        "approval_id",
        "model_name",
        "model_version",
        "decision",
        "approver",
        "role",
        "approved_at",
        "release_type",
        "reason",
    }
    missing = sorted(key for key in required if not str(payload.get(key, "")).strip())
    if missing:
        raise ModelTrainingConfigurationError(
            "approval document is incomplete: " + ", ".join(missing)
        )
    normalized = {key: str(payload[key]).strip() for key in required}
    if normalized["model_name"] != model_name or normalized["model_version"] != version:
        raise ModelTrainingConfigurationError(
            "approval document does not bind the requested model version"
        )
    if normalized["decision"].lower() != "approved":
        raise ModelTrainingConfigurationError("approval decision is not approved")
    if normalized["approver"] == requested_by:
        raise ModelTrainingConfigurationError("model release self-review is prohibited")
    _reject_placeholder(normalized["approver"], "approval approver")
    if normalized["role"] not in {"model-review-board", "model-risk-owner"}:
        raise ModelTrainingConfigurationError(
            "approval role must be model-review-board or model-risk-owner"
        )
    if normalized["release_type"].lower() not in {
        "shadow",
        "canary",
        "full",
        "rollback",
    }:
        raise ModelTrainingConfigurationError("approval release_type is unsupported")
    try:
        approved_at = datetime.fromisoformat(
            normalized["approved_at"].replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ModelTrainingConfigurationError(
            "approval approved_at must be ISO-8601"
        ) from exc
    if approved_at.tzinfo is None:
        raise ModelTrainingConfigurationError("approval approved_at must include timezone")
    return normalized


def require_production_database_url(value: str) -> str:
    normalized = value.strip()
    _require_remote_url(
        normalized,
        field="ODAY_DATABASE_URL",
        schemes={"postgres", "postgresql"},
    )
    return normalized


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _require_remote_url(value: str, *, field: str, schemes: set[str]) -> None:
    if not value:
        raise ModelTrainingConfigurationError(f"{field} is required")
    _reject_placeholder(value, field)
    parsed = urlparse(value)
    if parsed.scheme.lower() not in schemes:
        raise ModelTrainingConfigurationError(
            f"{field} must use {', '.join(sorted(schemes))}"
        )
    if (parsed.hostname or "").lower() in _LOCAL_HOSTS:
        raise ModelTrainingConfigurationError(f"{field} rejects localhost")
    if field == "ODAY_DATABASE_URL" and (not parsed.path or parsed.path == "/"):
        raise ModelTrainingConfigurationError(f"{field} must name a database")


def _require_gcs_root(value: str) -> None:
    if not value:
        raise ModelTrainingConfigurationError("ODP_MODEL_ARTIFACT_ROOT is required")
    _reject_placeholder(value, "ODP_MODEL_ARTIFACT_ROOT")
    parsed = urlparse(value)
    if parsed.scheme.lower() != "gs" or not parsed.netloc or not parsed.path.strip("/"):
        raise ModelTrainingConfigurationError(
            "ODP_MODEL_ARTIFACT_ROOT must be a dedicated gs:// bucket prefix"
        )


def _reject_placeholder(value: str, field: str) -> None:
    lowered = value.lower()
    if any(token in lowered for token in _PLACEHOLDER_TOKENS):
        raise ModelTrainingConfigurationError(f"{field} contains placeholder material")


__all__ = [
    "MAX_TRAINING_ROWS",
    "MODEL_SPECS",
    "DataBounds",
    "ModelKind",
    "ModelSpec",
    "ModelTrainingConfigurationError",
    "ProductionTrainingSettings",
    "require_approval_document",
    "require_production_database_url",
]
