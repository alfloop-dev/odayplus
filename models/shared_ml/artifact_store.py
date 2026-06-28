"""Content-addressed model artifact store and registry evidence (ODP-PV-013).

The Learning Hub registry primitives in this package (``ModelVersion``,
``ModelCard``, ``ValidationRun``) are storage-neutral domain objects. What turns
a *demo* registry into a *production* one is durable, tamper-evident **artifact
evidence**: every registered model version must point at a real, content-hashed
artifact, and the registry state (versions, validation, aliases, promotions,
rollbacks) must be reproducible into an auditable manifest.

This module owns two things, both storage-neutral so the durable SQLite backend
(``shared/infrastructure/persistence``) and in-memory tests share one contract:

* :class:`ArtifactStore` — a content-addressed blob store. ``put_artifact``
  hashes the bytes (SHA-256) and returns an :class:`ArtifactRecord` whose
  ``uri`` is derived from the digest, so a ``ModelVersion.artifact_uri`` cannot
  silently drift from the bytes it claims to describe. ``verify`` re-hashes the
  stored bytes to prove the artifact has not been tampered with.
* :func:`build_model_registry_evidence` — reduces registry + artifact state for
  one model into a JSON-serializable :class:`ModelRegistryEvidence` manifest
  (versions, metrics, aliases, model-card completeness, release decisions,
  artifact digests). This is the durable evidence the PV phase requires instead
  of demo registry state.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from models.shared_ml.registry import ModelAlias

DIGEST_ALGORITHM = "sha256"


class ArtifactKind(StrEnum):
    """Common artifact roles. The store accepts any string; these are the
    well-known kinds the model registry links from a ``ModelVersion``."""

    MODEL = "model"
    MODEL_CARD = "model_card"
    METRICS = "metrics"
    FEATURE_SPEC = "feature_spec"
    VALIDATION_REPORT = "validation_report"
    OTHER = "other"


def compute_content_digest(data: bytes) -> str:
    """Return the canonical ``sha256:<hex>`` digest for ``data``."""
    return f"{DIGEST_ALGORITHM}:{hashlib.sha256(data).hexdigest()}"


def artifact_uri(content_digest: str) -> str:
    """Canonical content-addressed URI for an artifact digest.

    Using the digest as the URI is what makes the evidence tamper-evident: a
    ``ModelVersion.artifact_uri`` set to this value is bound to exact bytes.
    """
    algorithm, _, hexdigest = content_digest.partition(":")
    return f"odp-artifact://{algorithm}/{hexdigest}"


@dataclass(frozen=True)
class ArtifactRecord:
    """Immutable metadata for one stored, content-addressed artifact."""

    artifact_id: str
    model_name: str
    version: str
    kind: str
    content_digest: str
    size_bytes: int
    content_type: str
    uri: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "model_name": self.model_name,
            "version": self.version,
            "kind": self.kind,
            "content_digest": self.content_digest,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
            "uri": self.uri,
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
        }


@runtime_checkable
class ArtifactStore(Protocol):
    """Public surface shared by in-memory and durable artifact stores."""

    def put_artifact(
        self,
        *,
        model_name: str,
        version: str,
        kind: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord: ...

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None: ...

    def open_artifact(self, artifact_id: str) -> bytes | None: ...

    def list_artifacts(self, model_name: str) -> list[ArtifactRecord]: ...

    def list_artifacts_for_version(
        self, model_name: str, version: str
    ) -> list[ArtifactRecord]: ...

    def verify(self, artifact_id: str) -> bool: ...


def make_artifact_id(model_name: str, version: str, kind: str) -> str:
    """Stable, logical id for an artifact slot (one per kind per version)."""
    return f"{model_name}/{version}/{kind}"


@dataclass
class InMemoryArtifactStore:
    """Content-addressed artifact store for tests and fast local boot.

    Bytes are deduplicated by digest; records are keyed by logical
    ``artifact_id`` (``model_name/version/kind``) so re-putting an identical
    artifact is idempotent.
    """

    _records: dict[str, ArtifactRecord] = field(default_factory=dict)
    _blobs: dict[str, bytes] = field(default_factory=dict)

    def put_artifact(
        self,
        *,
        model_name: str,
        version: str,
        kind: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord:
        digest = compute_content_digest(data)
        self._blobs[digest] = bytes(data)
        record = ArtifactRecord(
            artifact_id=make_artifact_id(model_name, version, kind),
            model_name=model_name,
            version=version,
            kind=kind,
            content_digest=digest,
            size_bytes=len(data),
            content_type=content_type,
            uri=artifact_uri(digest),
            metadata=dict(metadata or {}),
        )
        self._records[record.artifact_id] = record
        return record

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        return self._records.get(artifact_id)

    def open_artifact(self, artifact_id: str) -> bytes | None:
        record = self._records.get(artifact_id)
        if record is None:
            return None
        return self._blobs.get(record.content_digest)

    def list_artifacts(self, model_name: str) -> list[ArtifactRecord]:
        return [r for r in self._records.values() if r.model_name == model_name]

    def list_artifacts_for_version(
        self, model_name: str, version: str
    ) -> list[ArtifactRecord]:
        return [
            r
            for r in self._records.values()
            if r.model_name == model_name and r.version == version
        ]

    def verify(self, artifact_id: str) -> bool:
        record = self._records.get(artifact_id)
        if record is None:
            return False
        blob = self._blobs.get(record.content_digest)
        if blob is None:
            return False
        return compute_content_digest(blob) == record.content_digest


# -- registry evidence --------------------------------------------------------


@runtime_checkable
class _RegistryReader(Protocol):
    """Minimal read surface of a Learning Hub repository needed for evidence.

    Declared here (rather than imported from ``modules.learninghub``) so this
    lower-level package never depends upward on the module layer.
    """

    def list_model_versions(self, model_name: str) -> list[Any]: ...
    def get_model_card(self, model_name: str, version: str) -> Any | None: ...
    def get_validation_run(self, validation_run_id: str) -> Any | None: ...
    def get_alias(self, model_name: str, alias: ModelAlias) -> Any | None: ...
    def list_release_decisions(self) -> list[Any]: ...


@dataclass(frozen=True)
class ModelRegistryEvidence:
    """JSON-serializable audit manifest for one model's registry state."""

    model_name: str
    generated_at: datetime
    versions: tuple[Mapping[str, Any], ...]
    aliases: Mapping[str, str]
    release_decisions: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "generated_at": self.generated_at.isoformat(),
            "versions": [dict(v) for v in self.versions],
            "aliases": dict(self.aliases),
            "release_decisions": [dict(d) for d in self.release_decisions],
        }


def build_model_registry_evidence(
    *,
    model_name: str,
    repository: _RegistryReader,
    artifact_store: ArtifactStore,
    generated_at: datetime | None = None,
) -> ModelRegistryEvidence:
    """Reduce durable registry + artifact state into an audit manifest.

    For each version the manifest records its stage/aliases/metrics, the linked
    validation run status, model-card completeness/approval/rollback link, and
    the content digests of every artifact bound to that version — so promotion
    and rollback are reproducible from durable evidence alone.
    """
    version_entries: list[dict[str, Any]] = []
    for model_version in sorted(
        repository.list_model_versions(model_name), key=lambda mv: mv.version
    ):
        version = model_version.version
        card = repository.get_model_card(model_name, version)
        validation = (
            repository.get_validation_run(card.validation_run_id) if card else None
        )
        artifacts = artifact_store.list_artifacts_for_version(model_name, version)
        version_entries.append(
            {
                "model_id": model_version.model_id,
                "version": version,
                "stage": model_version.stage.value,
                "aliases": sorted(alias.value for alias in model_version.aliases),
                "artifact_uri": model_version.artifact_uri,
                "dataset_snapshot_id": model_version.dataset_snapshot_id,
                "feature_schema_version": model_version.feature_schema_version,
                "label_version": model_version.label_version,
                "metrics": dict(model_version.metrics),
                "rollback_target": model_version.rollback_target,
                "approved_by": model_version.approved_by,
                "git_sha": model_version.git_sha,
                "run_id": model_version.run_id,
                "validation_run_id": (
                    validation.validation_run_id if validation else None
                ),
                "validation_status": (
                    validation.status.value if validation else None
                ),
                "model_card": _model_card_evidence(card),
                "artifacts": [artifact.to_dict() for artifact in artifacts],
            }
        )

    aliases: dict[str, str] = {}
    for alias in ModelAlias:
        pointed = repository.get_alias(model_name, alias)
        if pointed is not None:
            aliases[alias.value] = pointed.version

    releases = tuple(
        decision.to_dict()
        for decision in repository.list_release_decisions()
        if getattr(decision, "model_name", None) == model_name
    )

    return ModelRegistryEvidence(
        model_name=model_name,
        generated_at=generated_at or datetime.now(UTC),
        versions=tuple(version_entries),
        aliases=aliases,
        release_decisions=releases,
    )


def _model_card_evidence(card: Any | None) -> dict[str, Any] | None:
    if card is None:
        return None
    return {
        "owner": card.owner,
        "risk_level": card.risk_level.value,
        "dataset_snapshot_id": card.dataset_snapshot_id,
        "feature_set_id": card.feature_set_id,
        "label_set_id": card.label_set_id,
        "validation_run_id": card.validation_run_id,
        "rollback_conditions": list(card.rollback_conditions),
        "is_complete": card.is_complete,
        "is_approved": card.is_approved,
        "approvals": [approval.to_dict() for approval in card.approvals],
    }


__all__ = [
    "DIGEST_ALGORITHM",
    "ArtifactKind",
    "ArtifactRecord",
    "ArtifactStore",
    "InMemoryArtifactStore",
    "ModelRegistryEvidence",
    "artifact_uri",
    "build_model_registry_evidence",
    "compute_content_digest",
    "make_artifact_id",
]
