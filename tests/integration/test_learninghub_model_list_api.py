from dataclasses import replace

import pytest

from apps.api.app.routes.learninghub import create_learninghub_router
from models.shared_ml import ModelAlias, ModelStage, ModelVersion
from models.shared_ml.model_card import ModelCard, ModelCardApproval, ModelRiskLevel
from models.shared_ml.validation import MetricThreshold, ValidationRun, ValidationStatus
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


def _registered_review_records():
    repository = InMemoryLearningHubRepository()
    model = ModelVersion(
        model_name="forecastops-review",
        version="4.2.0",
        artifact_uri="gs://unit-model-artifacts/forecastops-review/4.2.0/model.bin",
        dataset_snapshot_id="unit-review-dataset-1",
        feature_schema_version="store-timeseries-v1",
        label_version="revenue-v1",
        metrics={"mape": 0.08},
        stage=ModelStage.CANARY,
    )
    card = ModelCard(
        model_name=model.model_name,
        model_version=model.version,
        owner="model-owner",
        risk_level=ModelRiskLevel.R3,
        intended_use="Review a registered revenue forecast",
        not_intended_use="Lending decisions",
        dataset_snapshot_id=model.dataset_snapshot_id,
        validation_run_id="unit-review-validation-1",
        feature_set_id="store-features-v1",
        label_set_id="revenue-labels-v1",
        training_period="2026-01/2026-06",
        validation_period="2026-07",
        algorithm="linear_regression",
        baseline="previous_champion",
        metrics_summary={"mape": 0.08},
        privacy_review="PASSED",
        security_review="PASSED",
        rollback_conditions=("MAPE exceeds the approved threshold",),
        approvals=(ModelCardApproval(approver="unit-reviewer", role="model-review-board"),),
    )
    validation = ValidationRun(
        validation_run_id=card.validation_run_id,
        model_name=model.model_name,
        model_version=model.version,
        dataset_snapshot_id=model.dataset_snapshot_id,
        status=ValidationStatus.PASSED,
        metrics={"mape": 0.08},
        baseline_metrics={"mape": 0.09},
        thresholds=(MetricThreshold(metric_name="mape", max_value=0.1),),
    )
    repository.save_model_version(model)
    repository.save_model_card(card)
    repository.save_validation_run(validation)
    return repository, model, card, validation


def _registered_review_row(repository, model, detail):
    router = create_learninghub_router(repository=repository)
    path = "/learninghub/models/{model_name}" if detail else "/learninghub/models"
    route = next(route for route in router.routes if route.path == path)
    payload = route.endpoint(model.model_name) if detail else route.endpoint()
    rows = payload["versions"] if detail else payload["items"]
    assert len(rows) == 1
    return rows[0]


@pytest.mark.parametrize("detail", [False, True], ids=["list", "detail"])
def test_model_reads_include_the_exact_registered_card_and_validation(detail):
    repository, model, card, validation = _registered_review_records()

    row = _registered_review_row(repository, model, detail)

    assert row["model_id"] == model.model_id
    assert row["stage"] == "canary"
    assert row["model_card"]["intended_use"] == card.intended_use
    assert row["model_card"]["dataset_snapshot_id"] == model.dataset_snapshot_id
    assert row["model_card"]["is_complete"] is True
    assert row["model_card"]["is_approved"] is True
    assert row["model_card"]["approvals"] == [item.to_dict() for item in card.approvals]
    assert row["validation_run"]["validation_run_id"] == card.validation_run_id
    assert row["validation_run"]["metrics"] == dict(validation.metrics)
    assert row["validation_run"]["thresholds"] == [item.to_dict() for item in validation.thresholds]
    assert row["validation_run"]["status"] == "PASSED"
    assert row["validation_run"]["passed"] is True
    # A read must not publish or approve a model as a side effect.
    assert repository.get_model_card(model.model_name, model.version) == card
    assert repository.get_model_version(model.model_name, model.version) == model
    assert repository.list_release_decisions() == []


@pytest.mark.parametrize("detail", [False, True], ids=["list", "detail"])
def test_model_reads_preserve_incomplete_unapproved_and_warning_evidence(detail):
    repository, model, card, validation = _registered_review_records()
    repository.save_model_card(replace(card, owner="", approvals=()))
    repository.save_validation_run(replace(validation, status=ValidationStatus.WARNING))

    row = _registered_review_row(repository, model, detail)

    assert row["model_card"]["owner"] == ""
    assert row["model_card"]["is_complete"] is False
    assert row["model_card"]["is_approved"] is False
    assert row["model_card"]["approvals"] == []
    assert row["validation_run"]["status"] == "WARNING"
    assert row["validation_run"]["passed"] is False
    assert row["stage"] == "canary"
    assert repository.list_release_decisions() == []


@pytest.mark.parametrize("detail", [False, True], ids=["list", "detail"])
@pytest.mark.parametrize(
    "missing_or_crossed_evidence",
    ["missing-card", "missing-validation", "card-dataset", "validation-model", "validation-version", "validation-dataset"],
)
def test_model_reads_do_not_attach_missing_or_other_version_review_evidence(detail, missing_or_crossed_evidence):
    repository, model, card, validation = _registered_review_records()
    # Public repository writes represent incomplete/corrupted historical input;
    # they are test records, never evidence of a production approval.
    repository = InMemoryLearningHubRepository()
    repository.save_model_version(model)
    if missing_or_crossed_evidence != "missing-card":
        if missing_or_crossed_evidence == "card-dataset":
            card = replace(card, dataset_snapshot_id="other-dataset")
        repository.save_model_card(card)
    if missing_or_crossed_evidence != "missing-validation":
        changes = {
            "validation-model": {"model_name": "other-model"},
            "validation-version": {"model_version": "other-version"},
            "validation-dataset": {"dataset_snapshot_id": "other-dataset"},
        }.get(missing_or_crossed_evidence, {})
        repository.save_validation_run(replace(validation, **changes))

    row = _registered_review_row(repository, model, detail)

    assert row["model_id"] == model.model_id
    assert row["stage"] == "canary"
    assert "validation_run" not in row
    if missing_or_crossed_evidence in {"missing-card", "card-dataset"}:
        assert "model_card" not in row
    else:
        assert row["model_card"]["model_version"] == model.version
    assert repository.list_release_decisions() == []


def test_learninghub_model_detail_keeps_the_model_view_guard():
    router = create_learninghub_router(repository=InMemoryLearningHubRepository())
    route = next(route for route in router.routes if route.path == "/learninghub/models/{model_name}")

    assert route.dependencies


@pytest.mark.parametrize("detail", [False, True], ids=["list", "detail"])
def test_model_reads_expose_the_current_model_release_revision(detail):
    repository, model, _, _ = _registered_review_records()

    initial = _registered_review_row(repository, model, detail)
    assert initial["release_revision"] == 0
    with repository.release_guard(model.model_name, expected_revision=0) as reserved:
        assert reserved == 1
    with repository.release_guard(model.model_name, expected_revision=1) as reserved:
        assert reserved == 2
    with repository.release_guard("other-model", expected_revision=0) as reserved:
        assert reserved == 1

    current = _registered_review_row(repository, model, detail)
    assert current["release_revision"] == 2
    assert initial["release_revision"] == 0
    assert repository.get_release_revision(model.model_name) == 2
    assert repository.get_release_revision("other-model") == 1
    assert repository.list_release_decisions() == []
