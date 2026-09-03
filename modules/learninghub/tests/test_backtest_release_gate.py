from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import pytest

from models.shared_ml import (
    BacktestReceipt,
    MetricThreshold,
    ModelAlias,
    ModelCardApproval,
    ModelRiskLevel,
    ModelStage,
    ModelVersion,
    ValidationRun,
    ValidationStatus,
    evaluate_backtest_run,
)
from modules.learninghub import (
    InMemoryLearningHubRepository,
    LearningHubError,
    LearningHubService,
    MlflowRegistryAdapter,
    ReleaseType,
)
from shared.audit import InMemoryAuditLog
from shared.governance import (
    DecisionPolicy,
    default_model_performance_drift_policy,
)
from shared.infrastructure.persistence import (
    DurableLearningHubRepository,
    SqliteDocumentStore,
    SqliteEngine,
)
from tests.integration._learninghub_fixtures import (
    DEFAULT_MODEL_NAME,
    dataset_rows,
    model_card as fixture_model_card,
    model_version as fixture_model_version,
    prepare_candidate,
)


def _setup_service(repo=None):
    repository = repo or InMemoryLearningHubRepository()
    audit_log = InMemoryAuditLog()
    service = LearningHubService(
        repository=repository,
        registry=MlflowRegistryAdapter(repository),
        audit_log=audit_log,
    )
    return service, repository


def _request_release(service: LearningHubService, **kwargs):
    kwargs.setdefault(
        "expected_release_revision",
        service.repository.get_release_revision(str(kwargs["model_name"])),
    )
    kwargs.setdefault("idempotency_key", str(kwargs["approval_id"]))
    kwargs.setdefault("requested_by", "ml-owner")
    kwargs.setdefault("approved_by", "reviewer-a")
    return service.request_release(**kwargs)


def test_backtest_receipt_validation_and_serialization():
    receipt = BacktestReceipt(
        receipt_id="backtest-001",
        model_name=DEFAULT_MODEL_NAME,
        model_version="1.0.0",
        dataset_snapshot_id="snapshot-001",
        code_version="git-sha-abc",
        decision_policy_version_id="policy-v1:tenant-001",
        status=ValidationStatus.PASSED,
        metrics={"normalized_mae": 0.12, "p80_coverage": 0.85},
        baseline_metrics={"normalized_mae": 0.15, "p80_coverage": 0.80},
    )
    assert receipt.passed
    as_dict = receipt.to_dict()
    assert as_dict["receipt_id"] == "backtest-001"
    assert as_dict["model_name"] == DEFAULT_MODEL_NAME
    assert as_dict["model_version"] == "1.0.0"
    assert as_dict["dataset_snapshot_id"] == "snapshot-001"
    assert as_dict["code_version"] == "git-sha-abc"
    assert as_dict["decision_policy_version_id"] == "policy-v1:tenant-001"
    assert as_dict["status"] == "PASSED"
    assert as_dict["passed"] is True

    # Missing required field raises ValueError
    with pytest.raises(ValueError, match="backtest receipt requires"):
        BacktestReceipt(
            receipt_id="",
            model_name=DEFAULT_MODEL_NAME,
            model_version="1.0.0",
            dataset_snapshot_id="snapshot-001",
            code_version="git-sha-abc",
            decision_policy_version_id="policy-v1:tenant-001",
        )


def test_in_memory_and_durable_repository_backtest_storage():
    receipt = BacktestReceipt(
        receipt_id="backtest-001",
        model_name=DEFAULT_MODEL_NAME,
        model_version="1.0.0",
        dataset_snapshot_id="snapshot-001",
        code_version="git-sha-abc",
        decision_policy_version_id="policy-v1:tenant-001",
        status=ValidationStatus.PASSED,
        metrics={"normalized_mae": 0.12},
    )

    # InMemory repository
    in_mem_repo = InMemoryLearningHubRepository()
    in_mem_repo.save_backtest_receipt(receipt)
    assert in_mem_repo.get_backtest_receipt(DEFAULT_MODEL_NAME, "1.0.0") == receipt
    assert in_mem_repo.get_backtest_receipt_by_id("backtest-001") == receipt
    assert len(in_mem_repo.list_backtest_receipts(DEFAULT_MODEL_NAME)) == 1

    # Durable SQLite repository
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test.db"
        engine = SqliteEngine(str(db_path))
        store = SqliteDocumentStore(engine)
        durable_repo = DurableLearningHubRepository(store)

        durable_repo.save_backtest_receipt(receipt)
        fetched = durable_repo.get_backtest_receipt(DEFAULT_MODEL_NAME, "1.0.0")
        assert fetched is not None
        assert fetched.receipt_id == "backtest-001"
        assert fetched.code_version == "git-sha-abc"
        assert durable_repo.get_backtest_receipt_by_id("backtest-001").receipt_id == "backtest-001"
        assert len(durable_repo.list_backtest_receipts(DEFAULT_MODEL_NAME)) == 1
        engine.close()


def test_evaluate_backtest_with_decision_policy_thresholds():
    policy = default_model_performance_drift_policy("tenant-test")
    service, _ = _setup_service()

    snapshot = service.register_dataset_snapshot(
        dataset_rows(), dataset_snapshot_id="snapshot-backtest-eval"
    )

    # Positive evaluation
    passed_receipt = service.evaluate_backtest(
        model_name=DEFAULT_MODEL_NAME,
        model_version="1.0.0",
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        code_version="git-sha-abc",
        metrics={"normalized_mae": 0.20, "p80_coverage": 0.80},
        baseline_metrics={"normalized_mae": 0.22, "p80_coverage": 0.78},
        decision_policy=policy,
    )
    assert passed_receipt.passed
    assert passed_receipt.status is ValidationStatus.PASSED
    assert len(passed_receipt.failed_rules) == 0

    # Negative evaluation (degradation threshold breach)
    failed_receipt = service.evaluate_backtest(
        model_name=DEFAULT_MODEL_NAME,
        model_version="1.0.1",
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        code_version="git-sha-abc",
        metrics={"normalized_mae": 0.40, "p80_coverage": 0.60},
        baseline_metrics={"normalized_mae": 0.20, "p80_coverage": 0.80},
        decision_policy=policy,
    )
    assert not failed_receipt.passed
    assert failed_receipt.status is ValidationStatus.FAILED
    assert len(failed_receipt.failed_rules) > 0


def test_full_and_canary_release_succeed_with_valid_backtest_receipt():
    service, repo = _setup_service()
    candidate = prepare_candidate(service, "1.0.0")

    # FULL release succeeds
    full_decision = _request_release(
        service,
        model_name=candidate.model_name,
        version=candidate.version,
        release_type=ReleaseType.FULL,
        reason="promote version with passed backtest",
        approval_id="approval-full-001",
        rollback_target="1.0.0",
        monitoring_window="48h",
        success_criteria=("low error",),
        fail_criteria=("drift",),
        affected_modules=("ForecastOps",),
        requested_by="ml-owner",
        approved_by="reviewer-a",
        correlation_id="corr-full-001",
    )
    assert full_decision.release_type is ReleaseType.FULL
    assert full_decision.backtest_receipt_id is not None
    assert repo.get_alias(candidate.model_name, ModelAlias.PRODUCTION).version == "1.0.0"

    # CANARY release for v2
    v2 = prepare_candidate(service, "1.1.0")
    canary_decision = _request_release(
        service,
        model_name=v2.model_name,
        version=v2.version,
        release_type=ReleaseType.CANARY,
        reason="canary deployment for v1.1.0",
        approval_id="approval-canary-001",
        rollback_target="1.0.0",
        monitoring_window="24h",
        success_criteria=("low error",),
        fail_criteria=("drift",),
        affected_modules=("ForecastOps",),
        requested_by="ml-owner",
        approved_by="reviewer-a",
        correlation_id="corr-canary-001",
    )
    assert canary_decision.release_type is ReleaseType.CANARY
    assert canary_decision.backtest_receipt_id is not None
    assert repo.get_alias(v2.model_name, ModelAlias.CANARY).version == "1.1.0"


def test_full_and_canary_fail_closed_when_backtest_receipt_is_missing():
    service, repo = _setup_service()
    candidate = prepare_candidate(service, "1.0.0")

    # Remove the backtest receipt
    repo._backtest_receipts.clear()
    repo._backtest_receipts_by_id.clear()

    with pytest.raises(LearningHubError, match="requires a recorded backtest receipt"):
        _request_release(
        service,
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.FULL,
            reason="promote without backtest receipt",
            approval_id="approval-full-002",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("low error",),
            fail_criteria=("drift",),
            affected_modules=("ForecastOps",),
            requested_by="ml-owner",
            approved_by="reviewer-a",
            correlation_id="corr-full-002",
        )

    with pytest.raises(LearningHubError, match="requires a recorded backtest receipt"):
        _request_release(
        service,
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.CANARY,
            reason="canary without backtest receipt",
            approval_id="approval-canary-002",
            rollback_target="1.0.0",
            monitoring_window="24h",
            success_criteria=("low error",),
            fail_criteria=("drift",),
            affected_modules=("ForecastOps",),
            requested_by="ml-owner",
            approved_by="reviewer-a",
            correlation_id="corr-canary-002",
        )


def test_full_and_canary_fail_closed_when_backtest_receipt_is_failed():
    service, repo = _setup_service()
    candidate = prepare_candidate(service, "1.0.0")

    # Replace with a failed backtest receipt
    current_receipt = repo.get_backtest_receipt(candidate.model_name, candidate.version)
    failed_receipt = replace(
        current_receipt,
        status=ValidationStatus.FAILED,
    )
    repo.save_backtest_receipt(failed_receipt)

    with pytest.raises(LearningHubError, match="backtest gate failed"):
        _request_release(
        service,
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.FULL,
            reason="promote with failed backtest",
            approval_id="approval-full-003",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("low error",),
            fail_criteria=("drift",),
            affected_modules=("ForecastOps",),
            requested_by="ml-owner",
            approved_by="reviewer-a",
            correlation_id="corr-full-003",
        )


def test_full_and_canary_fail_closed_on_stale_dataset_snapshot():
    service, repo = _setup_service()
    candidate = prepare_candidate(service, "1.0.0")

    # Replace receipt with mismatched snapshot
    current_receipt = repo.get_backtest_receipt(candidate.model_name, candidate.version)
    stale_receipt = replace(
        current_receipt,
        dataset_snapshot_id="stale-dataset-snapshot-999",
    )
    repo.save_backtest_receipt(stale_receipt)

    with pytest.raises(LearningHubError, match="stale backtest receipt: dataset snapshot"):
        _request_release(
        service,
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.FULL,
            reason="promote with stale snapshot",
            approval_id="approval-full-004",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("low error",),
            fail_criteria=("drift",),
            affected_modules=("ForecastOps",),
            requested_by="ml-owner",
            approved_by="reviewer-a",
            correlation_id="corr-full-004",
        )


def test_full_and_canary_fail_closed_on_stale_code_version():
    service, repo = _setup_service()
    candidate = prepare_candidate(service, "1.0.0")

    # Replace receipt with mismatched code version (git_sha)
    current_receipt = repo.get_backtest_receipt(candidate.model_name, candidate.version)
    stale_receipt = replace(
        current_receipt,
        code_version="stale-git-sha-999",
    )
    repo.save_backtest_receipt(stale_receipt)

    with pytest.raises(LearningHubError, match="stale backtest receipt: code version"):
        _request_release(
        service,
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.FULL,
            reason="promote with stale code version",
            approval_id="approval-full-005",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("low error",),
            fail_criteria=("drift",),
            affected_modules=("ForecastOps",),
            requested_by="ml-owner",
            approved_by="reviewer-a",
            correlation_id="corr-full-005",
        )


def test_full_and_canary_fail_closed_on_stale_policy_version():
    service, repo = _setup_service()
    candidate = prepare_candidate(service, "1.0.0")

    # Replace receipt with mismatched policy version
    current_receipt = repo.get_backtest_receipt(candidate.model_name, candidate.version)
    stale_receipt = replace(
        current_receipt,
        decision_policy_version_id="stale-policy:tenant-999",
    )
    repo.save_backtest_receipt(stale_receipt)

    with pytest.raises(LearningHubError, match="stale backtest receipt: decision policy"):
        _request_release(
        service,
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.FULL,
            reason="promote with stale policy version",
            approval_id="approval-full-006",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("low error",),
            fail_criteria=("drift",),
            affected_modules=("ForecastOps",),
            requested_by="ml-owner",
            approved_by="reviewer-a",
            correlation_id="corr-full-006",
        )


def test_passing_backtest_cannot_bypass_model_card_and_approval_gates():
    service, repo = _setup_service()
    candidate = prepare_candidate(service, "1.0.0")

    # 1. Unapproved model card
    card = repo.get_model_card(candidate.model_name, candidate.version)
    unapproved_card = replace(
        card,
        approvals=(
            ModelCardApproval(approver="reviewer-a", role="model-review-board", decision="rejected"),
        ),
    )
    repo.save_model_card(unapproved_card)

    with pytest.raises(
        LearningHubError,
        match="(release requires approved model card|does not match a recorded model card approval)",
    ):
        _request_release(
            service,
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.FULL,
            reason="promote unapproved card",
            approval_id="approval-full-007",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("low error",),
            fail_criteria=("drift",),
            affected_modules=("ForecastOps",),
            requested_by="ml-owner",
            approved_by="reviewer-a",
            correlation_id="corr-full-007",
        )

    # 2. Incomplete model card
    incomplete_card = replace(card, intended_use="")
    repo.save_model_card(incomplete_card)
    with pytest.raises(LearningHubError, match="release requires complete model card"):
        _request_release(
        service,
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.FULL,
            reason="promote incomplete card",
            approval_id="approval-full-008",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("low error",),
            fail_criteria=("drift",),
            affected_modules=("ForecastOps",),
            requested_by="ml-owner",
            approved_by="reviewer-a",
            correlation_id="corr-full-008",
        )

    # 3. Self-review prohibition
    repo.save_model_card(card)
    with pytest.raises(LearningHubError, match="model release self-review is prohibited"):
        _request_release(
        service,
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.FULL,
            reason="self review",
            approval_id="approval-full-009",
            rollback_target="1.0.0",
            monitoring_window="48h",
            success_criteria=("low error",),
            fail_criteria=("drift",),
            affected_modules=("ForecastOps",),
            requested_by="reviewer-a",
            approved_by="reviewer-a",
            correlation_id="corr-full-009",
        )

    # 4. Missing rollback target for FULL
    with pytest.raises(LearningHubError, match="release requires rollback target"):
        _request_release(
        service,
            model_name=candidate.model_name,
            version=candidate.version,
            release_type=ReleaseType.FULL,
            reason="missing rollback target",
            approval_id="approval-full-010",
            rollback_target=None,
            monitoring_window="48h",
            success_criteria=("low error",),
            fail_criteria=("drift",),
            affected_modules=("ForecastOps",),
            requested_by="ml-owner",
            approved_by="reviewer-a",
            correlation_id="corr-full-010",
        )


def test_shadow_and_rollback_release_behavior():
    service, repo = _setup_service()
    v1 = prepare_candidate(service, "1.0.0")

    # Shadow release
    shadow = _request_release(
        service,
        model_name=v1.model_name,
        version=v1.version,
        release_type=ReleaseType.SHADOW,
        reason="shadow deployment",
        approval_id="approval-shadow-001",
        rollback_target=None,
        monitoring_window="24h",
        success_criteria=("latency",),
        fail_criteria=("drift",),
        affected_modules=("ForecastOps",),
        requested_by="ml-owner",
        approved_by="reviewer-a",
        correlation_id="corr-shadow-001",
    )
    assert shadow.release_type is ReleaseType.SHADOW
    assert repo.get_alias(v1.model_name, ModelAlias.SHADOW).version == "1.0.0"

    # Promote v1 to Production
    _request_release(
        service,
        model_name=v1.model_name,
        version=v1.version,
        release_type=ReleaseType.FULL,
        reason="promote v1",
        approval_id="approval-full-v1",
        rollback_target="1.0.0",
        monitoring_window="48h",
        success_criteria=("low error",),
        fail_criteria=("drift",),
        affected_modules=("ForecastOps",),
        requested_by="ml-owner",
        approved_by="reviewer-a",
        correlation_id="corr-full-v1",
    )

    # Promote v2 to Production
    v2 = prepare_candidate(service, "1.1.0")
    _request_release(
        service,
        model_name=v2.model_name,
        version=v2.version,
        release_type=ReleaseType.FULL,
        reason="promote v2",
        approval_id="approval-full-v2",
        rollback_target="1.0.0",
        monitoring_window="48h",
        success_criteria=("low error",),
        fail_criteria=("drift",),
        affected_modules=("ForecastOps",),
        requested_by="ml-owner",
        approved_by="reviewer-a",
        correlation_id="corr-full-v2",
    )
    assert repo.get_alias(v1.model_name, ModelAlias.PRODUCTION).version == "1.1.0"

    # Rollback to v1
    rollback_decision = _request_release(
        service,
        model_name=v2.model_name,
        version=v2.version,
        release_type=ReleaseType.ROLLBACK,
        reason="rollback to v1",
        approval_id="approval-rollback-001",
        rollback_target="1.0.0",
        monitoring_window="48h",
        success_criteria=("restored error",),
        fail_criteria=("drift",),
        affected_modules=("ForecastOps",),
        requested_by="ml-owner",
        approved_by="reviewer-a",
        correlation_id="corr-rollback-001",
    )
    assert rollback_decision.release_type is ReleaseType.ROLLBACK
    assert repo.get_alias(v1.model_name, ModelAlias.PRODUCTION).version == "1.0.0"
