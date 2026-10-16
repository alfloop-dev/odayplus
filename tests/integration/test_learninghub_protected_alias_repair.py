"""Protected alias repair needs a completed release, not a registered candidate.

All source data, signing identities and MLflow/SQLite files are owned synthetic
engineering fixtures. Raw alias writes deliberately inject historical drift;
they are not successful release authorization or production data evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from mlflow.tracking import MlflowClient

from apps.api.app.routes.learninghub import create_learninghub_router
from models.shared_ml import LocalModelArtifactStore, ModelAlias
from modules.learninghub import (
    InMemoryLearningHubRepository,
    LearningHubError,
    LearningHubService,
    MlflowRegistryAdapter,
    ModelReleaseDecision,
    ReleaseSagaState,
    ReleaseType,
)
from modules.learninghub.application.release import AliasReconciliationReceipt
from shared.audit import InMemoryAuditLog
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.auth.feature_flags import DEFAULT_FLAGS, FeatureFlagRegistry
from shared.infrastructure.persistence import (
    DurableAuditLog,
    DurableLearningHubRepository,
    SqliteDocumentStore,
    SqliteEngine,
)
from tests.integration._learninghub_fixtures import prepare_candidate
from tests.integration._model_publish_flag_fixture import enable_model_publish
from tests.integration._operation_flag_fixtures import new_operation_flags
from tests.integration.test_real_domain_operation_feature_flags import (
    MODEL_FLAG,
    _app,
    _disable,
    _enable,
    _release_body,
)
from tests.security.test_feature_flag_admin_authorization import _headers

MODEL = "forecast_revenue_interval"


@contextmanager
def _case(tmp_path, monkeypatch):
    owner = FeatureFlagRegistry(dict(DEFAULT_FLAGS))
    repository = InMemoryLearningHubRepository()
    audit = InMemoryAuditLog()
    registry = MlflowRegistryAdapter(repository,
        tracking_uri="sqlite:///" + str(tmp_path / "own-alias-repair-mlflow.sqlite3"))
    service = LearningHubService(repository=repository, registry=registry, audit_log=audit,
        artifact_store=LocalModelArtifactStore(tmp_path / "own-model-artifacts"),
        runtime_mode="poc", feature_flags=owner)
    for version in ("1.0.0", "1.1.0", "1.2.0"):
        prepare_candidate(service, version)
    app = _app(owner, audit, None, monkeypatch, tenant="synthetic-model-repair-tenant")
    app.include_router(create_learninghub_router(repository=repository, registry=registry,
        audit_log=audit, artifact_store=service.artifact_store,
        runtime_mode="poc", feature_flags=owner), prefix="/api/v1")
    with TestClient(app) as client:
        yield SimpleNamespace(owner=owner, repository=repository, registry=registry,
                              service=service, audit=audit, client=client)


def _raw_remote(case, version):
    registered = case.registry._require_model_version(MODEL, version)
    case.registry._require_client().set_registered_model_alias(
        MODEL, ModelAlias.PRODUCTION.value, str(registered.version))


def _aliases(case):
    remote = case.registry.get_by_alias(model_name=MODEL, alias=ModelAlias.PRODUCTION)
    durable = case.repository.get_alias(MODEL, ModelAlias.PRODUCTION)
    return (remote.version if remote else None, durable.version if durable else None)


def _complete_two_releases(case):
    _enable(case.client, MODEL_FLAG)
    for revision, version in enumerate(("1.0.0", "1.1.0")):
        response = case.client.post("/api/v1/learninghub/releases",
            json=_release_body(version, revision, "canonical-full-" + version, rollback="1.0.0"),
            headers=_headers("ml-owner"))
        assert response.status_code == 201
    _disable(case.client, MODEL_FLAG)
    assert _aliases(case) == ("1.1.0", "1.1.0")


def _repair(case, authority, *, key="synthetic-repair", revision=None):
    return case.service.reconcile_alias(model_name=MODEL, alias=ModelAlias.PRODUCTION,
        authority=authority, reason="Restore already committed canonical release projection",
        requested_by="synthetic-registry-operator", idempotency_key=key,
        expected_release_revision=case.repository.get_release_revision(MODEL) if revision is None else revision)


@pytest.mark.parametrize("authority", ["remote", "durable"])
@pytest.mark.parametrize("flag_enabled", [False, True])
def test_registered_unreleased_target_cannot_be_promoted_by_alias_repair(tmp_path, monkeypatch, authority, flag_enabled):
    with _case(tmp_path, monkeypatch) as case:
        if flag_enabled:
            _enable(case.client, MODEL_FLAG)
        case.repository.set_alias(MODEL, ModelAlias.PRODUCTION, "1.0.0" if authority == "remote" else "1.1.0")
        _raw_remote(case, "1.1.0" if authority == "remote" else "1.0.0")
        before = _aliases(case)
        revision = case.repository.get_release_revision(MODEL)
        with pytest.raises(LearningHubError, match="ALIAS_REPAIR_COMMITTED_RELEASE_REQUIRED"):
            _repair(case, authority)
        assert _aliases(case) == before
        assert case.repository.get_release_revision(MODEL) == revision
        assert case.repository.list_release_sagas(MODEL) == []
        assert not any(event.event_type == "learninghub.alias_reconciliation.v1" for event in case.audit.list_events())


@pytest.mark.parametrize("authority", ["remote", "durable"])
def test_current_completed_target_is_repairable_while_publish_flag_off_and_replay_is_readonly(tmp_path, monkeypatch, authority):
    with _case(tmp_path, monkeypatch) as case:
        _complete_two_releases(case)
        if authority == "remote":
            case.repository.set_alias(MODEL, ModelAlias.PRODUCTION, "1.0.0")
        else:
            _raw_remote(case, "1.0.0")
        before_revision = case.repository.get_release_revision(MODEL)
        repaired = _repair(case, authority, revision=before_revision)
        assert repaired.resolved_version == "1.1.0"
        assert _aliases(case) == ("1.1.0", "1.1.0")
        assert case.owner.is_enabled(MODEL_FLAG) is False
        after_revision = case.repository.get_release_revision(MODEL)
        event_count = len(case.audit.list_events())
        replay = _repair(case, authority, revision=before_revision)
        assert replay == repaired
        assert case.repository.get_release_revision(MODEL) == after_revision
        assert len(case.audit.list_events()) == event_count


@pytest.mark.parametrize("authority", ["remote", "durable"])
def test_historical_approved_version_is_not_current_repair_authority(tmp_path, monkeypatch, authority):
    with _case(tmp_path, monkeypatch) as case:
        _complete_two_releases(case)
        if authority == "remote":
            _raw_remote(case, "1.0.0")
        else:
            case.repository.set_alias(MODEL, ModelAlias.PRODUCTION, "1.0.0")
        before = _aliases(case)
        revision = case.repository.get_release_revision(MODEL)
        with pytest.raises(LearningHubError, match="ALIAS_REPAIR_CURRENT_RELEASE_TARGET_REQUIRED"):
            _repair(case, authority)
        assert _aliases(case) == before
        assert case.repository.get_release_revision(MODEL) == revision


@pytest.mark.parametrize("authority", ["remote", "durable"])
def test_actual_repair_source_cannot_substitute_new_target_after_canonical_preflight(tmp_path, monkeypatch, authority):
    with _case(tmp_path, monkeypatch) as case:
        _complete_two_releases(case)
        if authority == "remote":
            case.repository.set_alias(MODEL, ModelAlias.PRODUCTION, "1.0.0")
        else:
            _raw_remote(case, "1.0.0")
        snapshots = _aliases(case)
        actual = case.registry._reconcile_alias_unsafe

        def interleave(*, model_name, alias, authority, expected_target_version):
            assert expected_target_version == "1.1.0"
            # A deliberate outside-writer fault between preflight and the
            # genuine primitive, not a mock of the guard or its result.
            if authority == "remote":
                _raw_remote(case, "1.2.0")
            else:
                case.repository.set_alias(MODEL, ModelAlias.PRODUCTION, "1.2.0")
            return actual(model_name=model_name, alias=alias, authority=authority,
                          expected_target_version=expected_target_version)

        monkeypatch.setattr(case.registry, "_reconcile_alias_unsafe", interleave)
        with pytest.raises(LearningHubError, match="failed and was compensated"):
            _repair(case, authority)
        assert _aliases(case) == snapshots
        assert not any(isinstance(receipt, AliasReconciliationReceipt)
                       for receipt in case.repository.list_release_decisions())
        assert case.owner.is_enabled(MODEL_FLAG) is False


def test_resolved_rollback_receipt_can_authorize_repair_without_recreating_request_fingerprint(tmp_path, monkeypatch):
    with _case(tmp_path, monkeypatch) as case:
        _complete_two_releases(case)
        rollback = _release_body("1.1.0", 2, "canonical-rollback", kind="ROLLBACK")
        rollback["rollback_target"] = None  # Original saga resolves its recorded prior target.
        response = case.client.post("/api/v1/learninghub/releases", json=rollback, headers=_headers("ml-owner"))
        assert response.status_code == 201 and response.json()["to_version"] == "1.0.0"
        case.repository.set_alias(MODEL, ModelAlias.PRODUCTION, "1.1.0")
        repaired = _repair(case, "remote", key="repair-real-rollback")
        assert repaired.resolved_version == "1.0.0"
        assert _aliases(case) == ("1.0.0", "1.0.0")
        assert case.owner.is_enabled(MODEL_FLAG) is False


def test_alias_repair_keeps_original_wrong_expected_revision_refusal_without_alias_write(tmp_path, monkeypatch):
    with _case(tmp_path, monkeypatch) as case:
        _complete_two_releases(case)
        case.repository.set_alias(MODEL, ModelAlias.PRODUCTION, "1.0.0")
        before = _aliases(case)
        revision = case.repository.get_release_revision(MODEL)
        audit_count = len(case.audit.list_events())
        with pytest.raises(LearningHubError, match="revision"):
            _repair(case, "remote", revision=revision - 1)
        assert _aliases(case) == before
        assert case.repository.get_release_revision(MODEL) == revision
        assert len(case.audit.list_events()) == audit_count


def _assert_completed_anchor_triplet(case, decision):
    receipt = case.repository.get_release_decision(decision.release_id)
    saga = case.repository.get_release_saga(decision.release_id)
    assert isinstance(receipt, ModelReleaseDecision)
    assert receipt == decision
    assert receipt.executed_at is not None and receipt.executed_at.utcoffset() is not None
    assert saga.state is ReleaseSagaState.COMPLETED
    assert saga.decision == receipt == saga.command["decision"]
    assert saga.decision.executed_at == receipt.executed_at == saga.command["decision"].executed_at
    assert saga.command["requested_by"] == receipt.requested_by
    assert saga.command["approved_by"] == receipt.approved_by
    return receipt


def test_completed_release_and_rollback_keep_one_recorded_anchor_and_authorize_current_alias_repair(tmp_path, monkeypatch):
    with _case(tmp_path, monkeypatch) as case:
        _complete_two_releases(case)
        receipts = [row for row in case.repository.list_release_decisions()
                    if isinstance(row, ModelReleaseDecision)]
        assert len(receipts) == 2
        for receipt in receipts:
            _assert_completed_anchor_triplet(case, receipt)
        case.repository.set_alias(MODEL, ModelAlias.PRODUCTION, "1.0.0")
        repaired_full = _repair(case, "remote", key="anchored-full-repair")
        assert repaired_full.resolved_version == "1.1.0"
        assert _aliases(case) == ("1.1.0", "1.1.0")
        revision = case.repository.get_release_revision(MODEL)
        rollback = _release_body("1.1.0", revision, "anchored-canonical-rollback", kind="ROLLBACK")
        rollback["rollback_target"] = None
        response = case.client.post("/api/v1/learninghub/releases", json=rollback, headers=_headers("ml-owner"))
        assert response.status_code == 201, response.text
        assert response.json()["to_version"] == "1.0.0"
        receipt = case.repository.get_release_decision(response.json()["release_id"])
        _assert_completed_anchor_triplet(case, receipt)
        before_release_replay = (len(case.repository.list_release_decisions()),
                                 len([e for e in case.audit.list_events() if e.event_type == "learninghub.model_release.v1"]),
                                 case.repository.get_release_revision(MODEL))
        replay = case.client.post("/api/v1/learninghub/releases", json=rollback, headers=_headers("ml-owner"))
        assert replay.status_code == 201, replay.text
        assert replay.json() == response.json()
        assert len(case.repository.list_release_decisions()) == before_release_replay[0]
        # HTTP authorization remains audited; compare actual business release events.
        assert len([e for e in case.audit.list_events() if e.event_type == "learninghub.model_release.v1"]) == before_release_replay[1]
        assert case.repository.get_release_revision(MODEL) == before_release_replay[2]
        _assert_completed_anchor_triplet(case, receipt)
        case.repository.set_alias(MODEL, ModelAlias.PRODUCTION, "1.1.0")
        before_repair_revision = case.repository.get_release_revision(MODEL)
        repaired_rollback = _repair(case, "remote", key="anchored-rollback-repair")
        assert repaired_rollback.resolved_version == "1.0.0"
        assert _aliases(case) == ("1.0.0", "1.0.0")
        after_repair = (case.repository.get_release_revision(MODEL), len(case.audit.list_events()))
        repeated = _repair(case, "remote", key="anchored-rollback-repair", revision=before_repair_revision)
        assert repeated == repaired_rollback
        assert (case.repository.get_release_revision(MODEL), len(case.audit.list_events())) == after_repair
        assert case.owner.is_enabled(MODEL_FLAG) is False


@pytest.mark.parametrize("changed_copy", ["saga-decision", "command-decision"])
def test_completed_anchor_mismatch_refuses_alias_repair_without_mutation(tmp_path, monkeypatch, changed_copy):
    with _case(tmp_path, monkeypatch) as case:
        _complete_two_releases(case)
        receipt = max((row for row in case.repository.list_release_decisions()
                       if isinstance(row, ModelReleaseDecision)), key=lambda row: row.release_revision)
        _assert_completed_anchor_triplet(case, receipt)
        saga = case.repository.get_release_saga(receipt.release_id)
        changed = replace(receipt, executed_at=receipt.executed_at + timedelta(seconds=1))
        if changed_copy == "saga-decision":
            case.repository.save_release_saga(saga.evolve(decision=changed))
        else:
            case.repository.save_release_saga(saga.evolve(command={**saga.command, "decision": changed}))
        case.repository.set_alias(MODEL, ModelAlias.PRODUCTION, "1.0.0")
        before = (_aliases(case), case.repository.get_release_revision(MODEL),
                  len(case.repository.list_release_decisions()), len(case.audit.list_events()))
        with pytest.raises(LearningHubError, match="ALIAS_REPAIR_COMMITTED_RELEASE_REQUIRED"):
            _repair(case, "remote", key="timestamp-copy-mismatch-" + changed_copy)
        assert (_aliases(case), case.repository.get_release_revision(MODEL),
                len(case.repository.list_release_decisions()), len(case.audit.list_events())) == before
        assert case.repository.get_release_decision(receipt.release_id) == receipt
        assert case.owner.is_enabled(MODEL_FLAG) is False


class _PersistedReleaseInterrupted(BaseException):
    pass


@pytest.mark.parametrize(
    "crash_state",
    [ReleaseSagaState.RECEIPT_RECORDED, ReleaseSagaState.ALIASES_APPLIED],
)
def test_real_persisted_release_recovery_preserves_requester_and_records_distinct_operator(
    tmp_path, monkeypatch, crash_state,
):
    # Owned SQLite, actual OSS MLflow and local bytes/WORM are engineering
    # fixtures. Neither a PostgreSQL result nor an external delivery is implied.
    database = tmp_path / "owned-release.sqlite3"
    tracking_uri = "sqlite:///" + str(tmp_path / "owned-mlflow.sqlite3")
    artifacts = LocalModelArtifactStore(tmp_path / "owned-model-artifacts")
    worm_root = tmp_path / "owned-worm"
    engine = SqliteEngine(database)
    repository = DurableLearningHubRepository(SqliteDocumentStore(engine))
    registry = MlflowRegistryAdapter(
        repository, tracking_uri=tracking_uri,
        client=MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri),
    )
    service = LearningHubService(
        repository=repository, registry=registry, artifact_store=artifacts,
        audit_log=DurableAuditLog(engine, worm_sink=LocalAppendOnlyWormSink(worm_root)),
        runtime_mode="poc", worker_id="original-release-worker",
        release_lease_seconds=0.0, recover_on_startup=False,
    )
    try:
        enable_model_publish(service, tenant_id="tenant-modeling")
        for version in ("1.0.0", "1.1.0"):
            prepare_candidate(service, version)

        def release_command(version):
            return dict(
                model_name=MODEL, version=version, release_type=ReleaseType.FULL,
                reason="Persist the real governed release before an owned crash",
                approval_id="owned-recovery-approval-" + version,
                rollback_target="1.0.0", monitoring_window="48h",
                success_criteria=("canonical recovery stays verifiable",),
                fail_criteria=("receipt or actor provenance changes",),
                affected_modules=("ForecastOps",), requested_by="ml-owner",
                approved_by="reviewer-a", correlation_id="owned-recovery-" + version,
                expected_release_revision=repository.get_release_revision(MODEL),
                idempotency_key="owned-recovery-key-" + version,
            )

        service.request_release(**release_command("1.0.0"))
        interrupted_command = release_command("1.1.0")
        save_saga = repository.save_release_saga
        crashed = False

        def interrupt_after_real_persist(saga):
            nonlocal crashed
            saved = save_saga(saga)
            if saga.command.get("version") == "1.1.0" and saga.state is crash_state and not crashed:
                crashed = True
                raise _PersistedReleaseInterrupted(crash_state.value)
            return saved

        monkeypatch.setattr(repository, "save_release_saga", interrupt_after_real_persist)
        with pytest.raises(_PersistedReleaseInterrupted, match=crash_state.value):
            service.request_release(**interrupted_command)
        assert crashed
        orphaned_rows = repository.list_release_sagas(MODEL, include_terminal=False)
        assert len(orphaned_rows) == 1
        orphaned = orphaned_rows[0]
        assert orphaned.state is crash_state
        assert orphaned.lease_owner == "original-release-worker"
        assert not orphaned.lease_active()
        assert orphaned.command["requested_by"] == "ml-owner"
        assert "recovery_requested_by" not in orphaned.command
        recorded_before = repository.get_release_decision(orphaned.release_id)
        if crash_state is ReleaseSagaState.RECEIPT_RECORDED:
            assert recorded_before == orphaned.decision == orphaned.command["decision"]
            assert recorded_before.executed_at is not None
        else:
            assert recorded_before is None
    finally:
        engine.close()

    # A fresh durable owner and worker read the actual saved saga after the
    # interrupted engine closes; the original save method is never bypassed.
    reopened_engine = SqliteEngine(database)
    reopened = DurableLearningHubRepository(SqliteDocumentStore(reopened_engine))
    audit = DurableAuditLog(reopened_engine, worm_sink=LocalAppendOnlyWormSink(worm_root))
    flags, queue = new_operation_flags(reopened_engine)
    recovering = LearningHubService(
        repository=reopened,
        registry=MlflowRegistryAdapter(
            reopened, tracking_uri=tracking_uri,
            client=MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri),
        ),
        artifact_store=artifacts, audit_log=audit, feature_flags=flags, job_queue=queue,
        runtime_mode="poc", worker_id="different-recovery-worker",
        release_lease_seconds=0.0, recover_on_startup=False,
    )
    case = SimpleNamespace(repository=reopened, service=recovering, registry=recovering.registry, audit=audit)
    try:
        assert recovering.worker_id != orphaned.lease_owner
        recovered = recovering.recover_incomplete_releases(
            release_id=orphaned.release_id, requested_by="different-recovery-operator",
        )
        assert len(recovered) == 1
        terminal = reopened.get_release_saga(orphaned.release_id)
        assert recovered[0] == terminal
        assert terminal.command["requested_by"] == "ml-owner"
        assert terminal.command["recovery_requested_by"] == "different-recovery-operator"
        assert terminal.command["approved_by"] == "reviewer-a"
        assert terminal.request_fingerprint == orphaned.request_fingerprint
        assert terminal.idempotency_key == orphaned.idempotency_key
        assert terminal.fence_token == orphaned.fence_token + 1
        assert terminal.lease_owner is None and terminal.lease_expires_at is None
        compensation_events = [event for event in audit.list_events()
                               if event.event_type == "learninghub.model_release_compensation.v1"
                               and event.metadata.get("release_id") == orphaned.release_id]
        if crash_state is ReleaseSagaState.RECEIPT_RECORDED:
            assert terminal.state is ReleaseSagaState.COMPLETED
            receipt = _assert_completed_anchor_triplet(case, recorded_before)
            assert receipt.requested_by == "ml-owner" and receipt.approved_by == "reviewer-a"
            assert receipt.executed_at == recorded_before.executed_at
            assert terminal.audit_event_id == receipt.audit_event_id == orphaned.audit_event_id
            assert compensation_events == []
            before_replay = (reopened.get_release_revision(MODEL),
                             len(reopened.list_release_decisions()), len(audit.list_events()))
            assert recovering.request_release(**interrupted_command) == receipt
            assert (reopened.get_release_revision(MODEL),
                    len(reopened.list_release_decisions()), len(audit.list_events())) == before_replay
            reopened.set_alias(MODEL, ModelAlias.PRODUCTION, "1.0.0")
            assert _aliases(case) == ("1.1.0", "1.0.0")
            before_repair_revision = reopened.get_release_revision(MODEL)
            repaired = _repair(case, "remote", key="owned-recovery-repair")
            assert repaired.resolved_version == "1.1.0"
            assert _aliases(case) == ("1.1.0", "1.1.0")
            after_repair = (reopened.get_release_revision(MODEL), len(audit.list_events()))
            assert _repair(case, "remote", key="owned-recovery-repair", revision=before_repair_revision) == repaired
            assert (reopened.get_release_revision(MODEL), len(audit.list_events())) == after_repair
        else:
            assert terminal.state is ReleaseSagaState.COMPENSATED
            assert reopened.get_release_decision(orphaned.release_id) is None
            assert _aliases(case) == ("1.0.0", "1.0.0")
            assert len(compensation_events) == 1
            event = compensation_events[0]
            assert event.actor == "different-recovery-operator"
            assert event.action == "compensate_release" and event.outcome == "compensated"
            assert event.correlation_id == interrupted_command["correlation_id"]
            assert event.metadata["release_revision"] == orphaned.release_revision
            assert event.metadata["failed_audit_event_id"] == orphaned.command["audit_event"].event_id
        assert audit.verify_chain().ok
        assert list(worm_root.rglob("*.json"))
    finally:
        reopened_engine.close()
