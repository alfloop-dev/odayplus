from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from models.shared_ml.artifact_store import (
    ArtifactRecord,
    compute_content_digest,
    make_artifact_id,
)

from .contracts import (
    DataBounds,
    ModelSpec,
    ModelTrainingConfigurationError,
)

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


class ModelReadyDataError(RuntimeError):
    """Raised when canonical model-ready data cannot satisfy a model contract."""


@dataclass(frozen=True)
class ModelReadyInventory:
    model_key: str
    relation: str
    contract_registry_exists: bool
    contract_version: str | None
    contract_trainable: bool
    blocked_reason: str | None
    relation_exists: bool
    available_columns: tuple[str, ...]
    missing_columns: tuple[str, ...]
    eligible_row_count: int
    labeled_row_count: int
    temporal_min: str | None
    temporal_max: str | None

    @property
    def ready(self) -> bool:
        return (
            self.contract_registry_exists
            and self.contract_trainable
            and self.relation_exists
            and not self.missing_columns
            and self.eligible_row_count > 0
            and self.labeled_row_count > 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_key": self.model_key,
            "relation": self.relation,
            "contract_registry_exists": self.contract_registry_exists,
            "contract_version": self.contract_version,
            "contract_trainable": self.contract_trainable,
            "blocked_reason": self.blocked_reason,
            "relation_exists": self.relation_exists,
            "available_columns": list(self.available_columns),
            "missing_columns": list(self.missing_columns),
            "eligible_row_count": self.eligible_row_count,
            "labeled_row_count": self.labeled_row_count,
            "temporal_min": self.temporal_min,
            "temporal_max": self.temporal_max,
            "ready": self.ready,
        }


@dataclass(frozen=True)
class LoadedModelReadyRows:
    rows: tuple[Mapping[str, Any], ...]
    relation: str
    bounds: DataBounds
    query_sha256: str


@runtime_checkable
class QueryClient(Protocol):
    def query(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> list[dict[str, Any]]: ...

    def query_one(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> dict[str, Any] | None: ...


@runtime_checkable
class ModelReadySource(Protocol):
    def inventory(self, spec: ModelSpec) -> ModelReadyInventory: ...

    def load(self, spec: ModelSpec, bounds: DataBounds) -> LoadedModelReadyRows: ...


class PostgresModelReadySource:
    def __init__(self, client: QueryClient) -> None:
        self.client = client

    @classmethod
    def from_database_url(cls, database_url: str) -> PostgresModelReadySource:
        from shared.infrastructure.persistence.postgresql import PostgresEngine

        return cls(PostgresEngine(database_url))

    def inventory(self, spec: ModelSpec) -> ModelReadyInventory:
        schema, relation = _relation_parts(spec.relation)
        registry = self.client.query_one(
            "SELECT to_regclass(?) AS relation",
            ("model_ready.view_contracts",),
        )
        registry_exists = bool(registry and registry.get("relation"))
        if not registry_exists:
            return _blocked_inventory(
                spec,
                registry_exists=False,
                reason="MODEL_READY_CONTRACT_REGISTRY_MISSING",
            )
        contract = self.client.query_one(
            "SELECT view_version, contract_state, training_enabled, blocked_reason, "
            "installer_sha256 "
            "FROM model_ready.view_contracts WHERE relation_name = ?",
            (spec.relation,),
        )
        if contract is None:
            return _blocked_inventory(
                spec,
                registry_exists=True,
                reason="MODEL_READY_CONTRACT_NOT_REGISTERED",
            )
        contract_version = str(contract.get("view_version") or "")
        if contract_version != spec.expected_view_version:
            return _blocked_inventory(
                spec,
                registry_exists=True,
                contract_version=contract_version,
                reason="MODEL_READY_CONTRACT_VERSION_MISMATCH",
            )
        installer_sha256 = str(contract.get("installer_sha256") or "")
        if re.fullmatch(r"[0-9a-f]{64}", installer_sha256) is None:
            return _blocked_inventory(
                spec,
                registry_exists=True,
                contract_version=contract_version,
                reason="MODEL_READY_CONTRACT_INSTALLER_SHA_MISSING",
            )
        contract_trainable = (
            contract.get("contract_state") == "ACTIVE"
            and contract.get("training_enabled") is True
            and not contract.get("blocked_reason")
        )
        if not contract_trainable:
            return _blocked_inventory(
                spec,
                registry_exists=True,
                contract_version=contract_version,
                reason=str(
                    contract.get("blocked_reason")
                    or "MODEL_READY_CONTRACT_BLOCKED"
                ),
            )
        exists = self.client.query_one(
            "SELECT to_regclass(?) AS relation",
            (spec.relation,),
        )
        relation_exists = bool(exists and exists.get("relation"))
        if not relation_exists:
            return ModelReadyInventory(
                model_key=spec.key,
                relation=spec.relation,
                contract_registry_exists=True,
                contract_version=contract_version,
                contract_trainable=True,
                blocked_reason="MODEL_READY_RELATION_MISSING",
                relation_exists=False,
                available_columns=(),
                missing_columns=spec.required_columns,
                eligible_row_count=0,
                labeled_row_count=0,
                temporal_min=None,
                temporal_max=None,
            )
        column_rows = self.client.query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
            (schema, relation),
        )
        columns = tuple(str(row["column_name"]) for row in column_rows)
        missing = tuple(name for name in spec.required_columns if name not in columns)
        if missing:
            return ModelReadyInventory(
                model_key=spec.key,
                relation=spec.relation,
                contract_registry_exists=True,
                contract_version=contract_version,
                contract_trainable=True,
                blocked_reason="MODEL_READY_COLUMNS_MISSING",
                relation_exists=True,
                available_columns=columns,
                missing_columns=missing,
                eligible_row_count=0,
                labeled_row_count=0,
                temporal_min=None,
                temporal_max=None,
            )
        stats = self.client.query_one(
            f"SELECT "
            f"count(*) FILTER (WHERE is_training_eligible = true) AS eligible_count, "
            f"count(*) FILTER (WHERE is_training_eligible = true "
            f"AND {spec.label_column} IS NOT NULL) AS labeled_count, "
            f"min({spec.temporal_column}) AS temporal_min, "
            f"max({spec.temporal_column}) AS temporal_max "
            f"FROM {spec.relation} "
            f"WHERE view_name = ? AND view_version = ?",
            (
                relation,
                spec.expected_view_version,
            ),
        ) or {}
        return ModelReadyInventory(
            model_key=spec.key,
            relation=spec.relation,
            contract_registry_exists=True,
            contract_version=contract_version,
            contract_trainable=True,
            blocked_reason=None,
            relation_exists=True,
            available_columns=columns,
            missing_columns=(),
            eligible_row_count=int(stats.get("eligible_count") or 0),
            labeled_row_count=int(stats.get("labeled_count") or 0),
            temporal_min=_text_timestamp(stats.get("temporal_min")),
            temporal_max=_text_timestamp(stats.get("temporal_max")),
        )

    def load(self, spec: ModelSpec, bounds: DataBounds) -> LoadedModelReadyRows:
        inventory = self.inventory(spec)
        if not inventory.ready:
            missing = ", ".join(inventory.missing_columns) or "eligible labeled rows"
            reason = inventory.blocked_reason or f"missing {missing}"
            raise ModelReadyDataError(
                f"{spec.key}: model-ready relation is not trainable; {reason}"
            )
        columns = ", ".join(spec.required_columns)
        sql = (
            f"SELECT {columns} FROM {spec.relation} "
            f"WHERE is_training_eligible = true "
            f"AND {spec.label_column} IS NOT NULL "
            f"AND {spec.temporal_column} >= ? "
            f"AND {spec.temporal_column} < ? "
            f"ORDER BY {spec.temporal_column}, entity_id "
            f"LIMIT ?"
        )
        rows = self.client.query(
            sql,
            (bounds.start, bounds.end, bounds.max_rows),
        )
        if not rows:
            raise ModelReadyDataError(
                f"{spec.key}: no eligible labeled rows exist inside the requested bounds"
            )
        import hashlib

        query_fingerprint = hashlib.sha256(
            (
                f"{spec.relation}|{','.join(spec.required_columns)}|"
                f"{bounds.start.isoformat()}|{bounds.end.isoformat()}|{bounds.max_rows}"
            ).encode()
        ).hexdigest()
        return LoadedModelReadyRows(
            rows=tuple(rows),
            relation=spec.relation,
            bounds=bounds,
            query_sha256=query_fingerprint,
        )


@dataclass(frozen=True)
class GcsObject:
    bucket: str
    key: str
    generation: str
    size_bytes: int
    metadata: Mapping[str, str]


@runtime_checkable
class GcsTransport(Protocol):
    def upload(
        self,
        *,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> GcsObject: ...

    def download(self, *, bucket: str, key: str) -> bytes: ...

    def head(self, *, bucket: str, key: str) -> GcsObject | None: ...


class GoogleCloudStorageTransport:
    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            try:
                from google.cloud import storage

                client = storage.Client()
            except Exception as exc:
                raise ModelTrainingConfigurationError(
                    "Google Cloud Storage client requires workload identity "
                    "and google-cloud-storage"
                ) from exc
        self.client = client

    def upload(
        self,
        *,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> GcsObject:
        blob = self.client.bucket(bucket).blob(key)
        blob.metadata = dict(metadata)
        try:
            blob.upload_from_string(
                data,
                content_type=content_type,
                if_generation_match=0,
                checksum="crc32c",
            )
        except Exception:
            existing = self.head(bucket=bucket, key=key)
            expected = metadata.get("sha256")
            if existing is None or existing.metadata.get("sha256") != expected:
                raise
            return existing
        blob.reload()
        return _blob_to_object(bucket, key, blob)

    def download(self, *, bucket: str, key: str) -> bytes:
        return bytes(self.client.bucket(bucket).blob(key).download_as_bytes())

    def head(self, *, bucket: str, key: str) -> GcsObject | None:
        blob = self.client.bucket(bucket).blob(key)
        try:
            blob.reload()
        except Exception as exc:
            if type(exc).__name__ in {"NotFound", "NoSuchKey"}:
                return None
            raise
        return _blob_to_object(bucket, key, blob)


@dataclass
class GcsArtifactStore:
    root_uri: str
    transport: GcsTransport
    _records: dict[str, ArtifactRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        parsed = urlparse(self.root_uri)
        if parsed.scheme.lower() != "gs" or not parsed.netloc or not parsed.path.strip("/"):
            raise ModelTrainingConfigurationError(
                "GCS artifact store requires a dedicated gs:// bucket prefix"
            )
        self.bucket = parsed.netloc
        self.prefix = parsed.path.strip("/")

    @classmethod
    def from_environment(cls, root_uri: str) -> GcsArtifactStore:
        return cls(
            root_uri=root_uri,
            transport=GoogleCloudStorageTransport(),
        )

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
        digest_hex = digest.removeprefix("sha256:")
        artifact_id = make_artifact_id(model_name, version, str(kind))
        key = (
            f"{self.prefix}/models/{_path_token(model_name)}/"
            f"{_path_token(version)}/{_path_token(str(kind))}/sha256/{digest_hex}"
        )
        safe_metadata = {
            "sha256": digest,
            "artifact_id": artifact_id,
            "model_name": model_name,
            "model_version": version,
            "artifact_kind": str(kind),
        }
        uploaded = self.transport.upload(
            bucket=self.bucket,
            key=key,
            data=bytes(data),
            content_type=content_type,
            metadata=safe_metadata,
        )
        uri = f"gs://{self.bucket}/{key}"
        record = ArtifactRecord(
            artifact_id=artifact_id,
            model_name=model_name,
            version=version,
            kind=str(kind),
            content_digest=digest,
            size_bytes=len(data),
            content_type=content_type,
            uri=uri,
            metadata={
                **dict(metadata or {}),
                "gcs_generation": uploaded.generation,
                "artifact_sha256": digest,
            },
        )
        self._records[artifact_id] = record
        if not self.verify(artifact_id):
            raise ModelReadyDataError(f"GCS artifact verification failed for {artifact_id}")
        return record

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        return self._records.get(artifact_id)

    def open_artifact(self, artifact_id: str) -> bytes | None:
        record = self.get_artifact(artifact_id)
        if record is None:
            return None
        parsed = urlparse(record.uri)
        return self.transport.download(bucket=parsed.netloc, key=parsed.path.lstrip("/"))

    def list_artifacts(self, model_name: str) -> list[ArtifactRecord]:
        return [
            record
            for record in self._records.values()
            if record.model_name == model_name
        ]

    def list_artifacts_for_version(
        self,
        model_name: str,
        version: str,
    ) -> list[ArtifactRecord]:
        return [
            record
            for record in self.list_artifacts(model_name)
            if record.version == version
        ]

    def verify(self, artifact_id: str) -> bool:
        record = self.get_artifact(artifact_id)
        if record is None:
            return False
        parsed = urlparse(record.uri)
        obj = self.transport.head(bucket=parsed.netloc, key=parsed.path.lstrip("/"))
        if obj is None or obj.metadata.get("sha256") != record.content_digest:
            return False
        payload = self.transport.download(
            bucket=parsed.netloc,
            key=parsed.path.lstrip("/"),
        )
        return compute_content_digest(payload) == record.content_digest

    def verify_uri(self, uri: str, content_digest: str) -> bool:
        parsed = urlparse(uri)
        key = parsed.path.lstrip("/")
        if (
            parsed.scheme.lower() != "gs"
            or parsed.netloc != self.bucket
            or not key.startswith(f"{self.prefix}/")
        ):
            return False
        obj = self.transport.head(bucket=self.bucket, key=key)
        if obj is None or obj.metadata.get("sha256") != content_digest:
            return False
        payload = self.transport.download(bucket=self.bucket, key=key)
        return compute_content_digest(payload) == content_digest


def _blocked_inventory(
    spec: ModelSpec,
    *,
    registry_exists: bool,
    reason: str,
    contract_version: str | None = None,
) -> ModelReadyInventory:
    return ModelReadyInventory(
        model_key=spec.key,
        relation=spec.relation,
        contract_registry_exists=registry_exists,
        contract_version=contract_version,
        contract_trainable=False,
        blocked_reason=reason,
        relation_exists=False,
        available_columns=(),
        missing_columns=spec.required_columns,
        eligible_row_count=0,
        labeled_row_count=0,
        temporal_min=None,
        temporal_max=None,
    )


def _relation_parts(value: str) -> tuple[str, str]:
    parts = value.split(".")
    if len(parts) != 2 or any(not _IDENTIFIER.fullmatch(part) for part in parts):
        raise ModelTrainingConfigurationError(
            f"model-ready relation {value!r} is not a safe schema-qualified identifier"
        )
    return parts[0], parts[1]


def _path_token(value: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_.-]", "-", value).strip(".-")
    if not token:
        raise ModelTrainingConfigurationError("artifact path token is empty")
    return token


def _text_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _blob_to_object(bucket: str, key: str, blob: Any) -> GcsObject:
    return GcsObject(
        bucket=bucket,
        key=key,
        generation=str(blob.generation),
        size_bytes=int(blob.size or 0),
        metadata=dict(blob.metadata or {}),
    )


__all__ = [
    "GcsArtifactStore",
    "GcsObject",
    "GcsTransport",
    "GoogleCloudStorageTransport",
    "LoadedModelReadyRows",
    "ModelReadyDataError",
    "ModelReadyInventory",
    "ModelReadySource",
    "PostgresModelReadySource",
    "QueryClient",
]
