"""Fresh model authority and a held guard for fenced score publication.

MLflow metadata reads alone cannot provide an atomic SQL publication boundary.
This port binds them to the durable registry's existing model writer key. Remote
administrators that do not use that key remain an external deployment control.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from models.shared_ml.artifact_store import compute_content_digest
from models.shared_ml.production_runtime import (
    ModelInferenceResult,
    ProductionModelApprovalError,
    ProductionModelLineageError,
    ProductionModelRegistryError,
    _normalize_sha256,
)
from models.shared_ml.registry import ModelAlias, ModelStage

_REQUIRED_GOVERNANCE = (
    "release_id",
    "release_revision",
    "approval_id",
    "release_scope",
    "requested_by",
    "release_approved_by",
    "model_card_checksum",
    "validation_run_id",
    "validation_status",
    "backtest_receipt_id",
    "backtest_status",
    "backtest_policy_version_id",
    "backtest_code_version",
)


class CurrentModelAdmissionChanged(ProductionModelApprovalError):
    code = "CURRENT_MODEL_ADMISSION_CHANGED"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class CurrentModelAdmissionPort:
    """Use only an injected metadata reader and its same-engine registry guard."""

    def __init__(
        self,
        *,
        runtime: Any,
        registry_repository: Any,
        engine: Any,
        tenant_id: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.registry_repository = registry_repository
        self.engine = engine
        self.tenant_id = tenant_id

    def _authority(self) -> None:
        repository = self.registry_repository
        store = getattr(repository, "_store", None)
        if (
            getattr(self.engine, "dialect", None) != "postgresql"
            or store is None
            or getattr(store, "engine", None) is not self.engine
            or not callable(getattr(repository, "release_recovery_guard", None))
            or not callable(getattr(self.runtime, "read_admission", None))
        ):
            raise ProductionModelRegistryError(
                "current model admission requires the actual metadata reader and same PostgreSQL writer authority"
            )

    def read(
        self,
        *,
        service: str = "sitescore",
        expected_feature_schema_version: str,
    ) -> dict[str, Any]:
        self._authority()
        model_names = getattr(self.runtime, "model_names", None)
        if not isinstance(model_names, Mapping):
            raise ProductionModelRegistryError(
                "current model reader has no trusted service mapping"
            )
        if service not in model_names:
            raise ProductionModelRegistryError(
                "current model service has no configured production mapping"
            )
        model_name = model_names[service]
        if not isinstance(model_name, str) or not model_name.strip():
            raise ProductionModelRegistryError("current model reader has no model identity")
        with self.registry_repository.release_recovery_guard(model_name):
            raw = self.runtime.read_admission(
                service=service,
                expected_feature_schema_version=expected_feature_schema_version,
            )
            if not isinstance(raw, dict) or raw.get("model_name") != model_name:
                raise ProductionModelLineageError(
                    "current registry metadata has a different model identity"
                )
            # A JSON round trip isolates this immutable receipt from client-owned
            # mutable dictionaries, including nested monitoring configuration.
            facts = json.loads(_canonical(raw))
            facts.update(self._durable_facts(facts))
            facts["receipt_sha256"] = _digest(facts)
            return facts

    def _durable_facts(self, facts: dict[str, Any]) -> dict[str, Any]:
        repo = self.registry_repository
        model_name = facts["model_name"]
        current = repo.get_alias(model_name, ModelAlias.PRODUCTION)
        if current is None or current.stage is not ModelStage.PRODUCTION:
            raise ProductionModelApprovalError("durable production alias is unavailable")
        binding_pairs = {
            "model_version": current.version,
            "model_id": current.model_id,
            "model_stage": current.stage.value,
            "model_aliases": sorted(alias.value for alias in current.aliases),
            "artifact_uri": current.artifact_uri,
            "artifact_sha256": _normalize_sha256(current.artifact_sha256 or ""),
            "dataset_snapshot_id": current.dataset_snapshot_id,
            "feature_schema_version": current.feature_schema_version,
            "label_version": current.label_version,
            "model_git_sha": current.git_sha,
            "source_run_id": current.run_id,
            "model_approved_by": current.approved_by,
            "model_approved_at": current.approved_at.isoformat() if current.approved_at else None,
            "model_metadata": dict(current.monitoring_config),
        }
        if (
            not current.approved_by
            or current.approved_at is None
            or any(facts.get(key) != value for key, value in binding_pairs.items())
        ):
            raise CurrentModelAdmissionChanged(
                "remote model metadata and durable approved production alias disagree"
            )
        governance = facts.get("governance")
        if not isinstance(governance, dict) or any(
            not isinstance(governance.get(key), str) or not governance[key].strip()
            for key in _REQUIRED_GOVERNANCE
        ):
            raise ProductionModelApprovalError(
                "production model has incomplete approved release evidence"
            )
        if governance["release_scope"] != "global" or governance["validation_status"] != "PASSED":
            raise ProductionModelApprovalError(
                "production model release scope or validation is invalid"
            )
        for key in _REQUIRED_GOVERNANCE:
            if str(current.monitoring_config.get(key, "")) != governance[key]:
                raise CurrentModelAdmissionChanged(
                    "remote release evidence and durable release projection disagree"
                )
        try:
            release_revision = int(governance["release_revision"])
        except (TypeError, ValueError) as exc:
            raise ProductionModelApprovalError(
                "production model release revision is invalid"
            ) from exc
        authority_revision = repo.get_release_revision(model_name)
        if release_revision < 1 or authority_revision < release_revision:
            raise ProductionModelApprovalError(
                "production model release revision has no durable authority"
            )
        decision = repo.get_release_decision(governance["release_id"])
        saga = repo.get_release_saga(governance["release_id"])
        expected_decision = {
            "model_name": model_name,
            "to_version": current.version,
            "approval_id": governance["approval_id"],
            "release_revision": release_revision,
            "scope": "global",
            "requested_by": governance["requested_by"],
            "approved_by": governance["release_approved_by"],
            "model_card_checksum": governance["model_card_checksum"],
            "dataset_snapshot_id": current.dataset_snapshot_id,
            "feature_schema_version": current.feature_schema_version,
            "label_version": current.label_version,
            "model_artifact_uri": current.artifact_uri,
            "backtest_receipt_id": governance["backtest_receipt_id"],
        }
        if (
            decision is None
            or any(
                getattr(decision, key, None) != value for key, value in expected_decision.items()
            )
            or saga is None
            or getattr(saga, "state", None) != "COMPLETED"
        ):
            raise ProductionModelApprovalError(
                "production model has no matching completed approved release"
            )
        card = repo.get_model_card(model_name, current.version)
        validation = repo.get_validation_run(governance["validation_run_id"])
        if (
            card is None
            or not card.is_complete
            or not card.is_approved
            or card.validation_run_id != governance["validation_run_id"]
            or compute_content_digest(_canonical(card.to_dict()))
            != governance["model_card_checksum"]
            or validation is None
            or not validation.passed
            or validation.model_name != model_name
            or validation.model_version != current.version
            or validation.dataset_snapshot_id != current.dataset_snapshot_id
        ):
            raise ProductionModelApprovalError(
                "current model card or validation approval has been withdrawn"
            )
        backtest = repo.get_backtest_receipt(model_name, current.version)
        expected_backtest = {
            "receipt_id": governance["backtest_receipt_id"],
            "model_name": model_name,
            "model_version": current.version,
            "dataset_snapshot_id": current.dataset_snapshot_id,
            "code_version": current.git_sha,
            "decision_policy_version_id": governance["backtest_policy_version_id"],
        }
        if (
            governance["backtest_status"] != "PASSED"
            or governance["backtest_code_version"] != current.git_sha
            or backtest is None
            or not backtest.passed
            or any(
                getattr(backtest, key, None) != value for key, value in expected_backtest.items()
            )
            or (
                validation.decision_policy_version_id
                and validation.decision_policy_version_id != backtest.decision_policy_version_id
            )
        ):
            raise ProductionModelApprovalError(
                "current model backtest approval has been withdrawn or changed"
            )
        return {
            "release_revision": release_revision,
            "authority_revision": authority_revision,
            "release_id": governance["release_id"],
            "release_scope": "global",
        }

    @staticmethod
    def _verify_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(_canonical(dict(receipt)))
            observed = value.pop("receipt_sha256")
            if observed != _digest(value):
                raise ValueError("receipt digest mismatch")
        except (KeyError, TypeError, ValueError) as exc:
            raise CurrentModelAdmissionChanged("model admission receipt is malformed") from exc
        return value

    @contextmanager
    def guard(self, receipt: Mapping[str, Any], *, tenant_id: str) -> Iterator[None]:
        self._authority()
        if (
            not isinstance(tenant_id, str)
            or not tenant_id.strip()
            or (self.tenant_id is not None and self.tenant_id != tenant_id)
        ):
            raise ProductionModelRegistryError(
                "model publication guard has a different tenant scope"
            )
        store_tenant = getattr(self.registry_repository._store, "tenant_id", None)
        if store_tenant is not None and store_tenant != tenant_id:
            raise ProductionModelRegistryError(
                "model publication authority has a different tenant scope"
            )
        expected = self._verify_receipt(receipt)
        model_name = expected.get("model_name")
        if not isinstance(model_name, str) or not model_name.strip():
            raise CurrentModelAdmissionChanged("model admission receipt has no identity")
        with self.registry_repository.release_recovery_guard(model_name):
            current = self.read(
                service=expected.get("service"),
                expected_feature_schema_version=expected.get("feature_schema_version"),
            )
            if current != dict(receipt):
                raise CurrentModelAdmissionChanged(
                    "model authority changed after execution admission"
                )
            yield

    def verify_inference(self, receipt: Mapping[str, Any], result: ModelInferenceResult) -> None:
        expected = self._verify_receipt(receipt)
        if not isinstance(result, ModelInferenceResult):
            raise CurrentModelAdmissionChanged("inference returned no executable model evidence")
        actual = result.binding.to_audit_metadata()
        actual.pop("binding_resolved_at")
        actual.pop("model_engine")
        if (
            any(actual.get(key) != expected.get(key) for key in actual)
            or result.artifact_sha256 != expected.get("artifact_sha256")
            or result.registry_version != expected.get("registry_version")
            or dict(result.model_metadata) != expected.get("model_metadata")
        ):
            raise CurrentModelAdmissionChanged(
                "inference used a different model than the admitted receipt"
            )
