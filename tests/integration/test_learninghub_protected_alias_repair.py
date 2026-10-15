"""Protected alias repair needs a completed release, not a registered candidate.

All source data, signing identities and MLflow/SQLite files are owned synthetic
engineering fixtures. Raw alias writes deliberately inject historical drift;
they are not successful release authorization or production data evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes.learninghub import create_learninghub_router
from models.shared_ml import LocalModelArtifactStore, ModelAlias
from modules.learninghub import (
    InMemoryLearningHubRepository,
    LearningHubError,
    LearningHubService,
    MlflowRegistryAdapter,
)
from modules.learninghub.application.release import AliasReconciliationReceipt
from shared.audit import InMemoryAuditLog
from shared.auth.feature_flags import DEFAULT_FLAGS, FeatureFlagRegistry
from tests.integration._learninghub_fixtures import prepare_candidate
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
