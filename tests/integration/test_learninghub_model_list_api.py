from apps.api.app.routes.learninghub import create_learninghub_router
from models.shared_ml import ModelAlias, ModelStage, ModelVersion
from modules.learninghub import InMemoryLearningHubRepository
def _model_list_route(repository: InMemoryLearningHubRepository | None = None):
    router = create_learninghub_router(repository=repository)
    return next(route for route in router.routes if route.path == "/learninghub/models")


def test_learninghub_lists_every_persisted_model_version() -> None:
    repository = InMemoryLearningHubRepository()
    repository.save_model_version(
        ModelVersion(
            model_name="sitescore",
            version="2.1.0",
            artifact_uri="gs://model-artifacts/sitescore/2.1.0/model.bin",
            dataset_snapshot_id="dataset-live-20260724",
            feature_schema_version="sitescore-feature-view-v1",
            label_version="sitescore-label-v1",
            metrics={"mae": 0.12},
            stage=ModelStage.PRODUCTION,
            aliases=frozenset({ModelAlias.PRODUCTION}),
            run_id="mlflow-run-live-1",
            git_sha="a" * 40,
        )
    )
    repository.save_model_version(
        ModelVersion(
            model_name="forecastops",
            version="4.0.0",
            artifact_uri="gs://model-artifacts/forecastops/4.0.0/model.bin",
            dataset_snapshot_id="forecast-live-20260724",
            feature_schema_version="store-machine-timeseries-view-v1",
            label_version="forecast-label-v1",
            metrics={"smape": 0.08},
            stage=ModelStage.CANARY,
        )
    )
    route = _model_list_route(repository)
    payload = route.endpoint()

    assert payload["count"] == 2
    assert [
        (item["model_name"], item["version"])
        for item in payload["items"]
    ] == [
        ("forecastops", "4.0.0"),
        ("sitescore", "2.1.0"),
    ]
    assert payload["items"][1]["aliases"] == ["production"]


def test_learninghub_model_list_keeps_the_model_view_guard() -> None:
    route = _model_list_route()

    assert route.dependencies
