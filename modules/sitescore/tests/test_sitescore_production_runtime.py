from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from models.shared_ml.production_runtime import ModelInferenceResult
from models.shared_ml.registry import ModelAlias, ModelStage, ModelVersion
from models.shared_ml.scoring_binding import ModelBinding
from modules.sitescore import (
    InMemorySiteScoreRepository,
    SiteScoreRuntimeConfigurationError,
)
from modules.sitescore.application.reporting import SiteScoreReportService
from modules.sitescore.domain.scoring import SITESCORE_FEATURE_VERSION
from modules.sitescore.workers.scoring_worker import SiteScoreScoringWorker
from shared.infrastructure.persistence import (
    DurableSiteScoreRepository,
    SqliteDocumentStore,
    SqliteEngine,
)

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


class RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        model = ModelVersion(
            model_name="sitescore",
            version="approved-2026.07.24",
            artifact_uri="gs://oday-models/sitescore/model.zip",
            dataset_snapshot_id="training-live-001",
            feature_schema_version=SITESCORE_FEATURE_VERSION,
            label_version="mature-revenue-v3",
            metrics={"mae": 8200.0},
            stage=ModelStage.PRODUCTION,
            aliases=frozenset({ModelAlias.PRODUCTION}),
            run_id="mlflow-run-sitescore-001",
            git_sha="abc1234",
            approved_by="model-review-board",
            approved_at=NOW,
        )
        self.binding = ModelBinding.from_model_version(
            "sitescore",
            model,
            artifact_sha256="sha256:" + ("a" * 64),
            engine="lightgbm.LGBMRegressor",
            mlflow_run_id="mlflow-run-sitescore-001",
        )

    def infer(self, **kwargs: Any) -> ModelInferenceResult:
        self.calls.append(kwargs)
        count = len(kwargs["rows"])
        return ModelInferenceResult(
            binding=self.binding,
            point=(315_000.0,) * count,
            lower=(270_000.0,) * count,
            upper=(360_000.0,) * count,
            engine="lightgbm.LGBMRegressor",
            artifact_sha256="sha256:" + ("a" * 64),
        )


def _feature() -> dict[str, Any]:
    return {
        "candidate_site_id": "candidate-live-001",
        "heat_zone_score": 84.0,
        "monthly_rent": 52_000.0,
        "area_ping": 24.0,
        "comparable_store_count": 5,
        "feature_snapshot_time": NOW.isoformat(),
        "view_version": SITESCORE_FEATURE_VERSION,
        "source_snapshot_ids": ["listing-live-001", "poi-live-001"],
    }


def _repository(path: Path) -> tuple[SqliteEngine, DurableSiteScoreRepository]:
    engine = SqliteEngine(path)
    return engine, DurableSiteScoreRepository(SqliteDocumentStore(engine))


def test_production_worker_invokes_model_runtime_and_survives_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "sitescore.sqlite3"
    engine, repository = _repository(database)
    runtime = RecordingRuntime()
    try:
        result = SiteScoreScoringWorker(
            repository=repository,
            model_runtime=runtime,
            runtime_mode="production",
        ).run(features=[_feature()], prediction_origin_time=NOW)
        report_id = result.reports[0].report_id
        assert result.reports[0].m12.p50 == 315_000.0
        assert result.reports[0].model_version == "sitescore:approved-2026.07.24"
        assert runtime.calls[0]["service"] == "sitescore"
        assert runtime.calls[0]["rows"][0]["source_snapshot_ids"]
    finally:
        engine.close()

    reopened_engine, reopened = _repository(database)
    try:
        restored = reopened.get_report(report_id)
        assert restored is not None
        assert restored.m12.p50 == 315_000.0
        assert reopened.latest("candidate-live-001") == restored
    finally:
        reopened_engine.close()


def test_production_rejects_missing_memory_or_model_bindings(tmp_path: Path) -> None:
    with pytest.raises(
        SiteScoreRuntimeConfigurationError,
        match="injected durable repository",
    ):
        SiteScoreReportService(runtime_mode="production")
    with pytest.raises(
        SiteScoreRuntimeConfigurationError,
        match="injected durable repository",
    ):
        SiteScoreReportService(
            repository=InMemorySiteScoreRepository(),
            runtime_mode="production",
        )

    engine, repository = _repository(tmp_path / "sitescore.sqlite3")
    try:
        service = SiteScoreReportService(
            repository=repository,
            runtime_mode="production",
        )
        with pytest.raises(RuntimeError, match="runtime was not composed"):
            service.score_candidates([_feature()])
    finally:
        engine.close()


def test_production_flag_cannot_enable_fixed_scorecard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from modules.sitescore.application import reporting

    monkeypatch.setattr(
        reporting,
        "score_sites",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("fixed SiteScore baseline was invoked")
        ),
    )
    engine, repository = _repository(tmp_path / "sitescore.sqlite3")
    try:
        runtime = RecordingRuntime()
        reports = SiteScoreReportService(
            repository=repository,
            model_runtime=runtime,
            require_production_model=False,
            runtime_mode="production",
        ).score_candidates([_feature()])
        assert reports[0].m12.p50 == 315_000.0
        assert len(runtime.calls) == 1
    finally:
        engine.close()
