"""Runtime WORM sink writers for audit events and retained evidence.

The product database is useful for query and restore, but immutable governance
evidence also needs a write path outside product-owned mutable tables. This
module keeps that path dependency-light: production can point at a GCS bucket
with object-creator credentials, while tests and local runs use an append-only
file sink that rejects overwrites.
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from shared.audit.integrity import canonical_json, sha256_hex


class AuditWormSinkError(RuntimeError):
    """Raised when the external immutable sink rejects an audit write."""


@dataclass(frozen=True)
class AuditWormReceipt:
    sink_id: str
    object_uri: str
    record_type: str
    record_id: str
    checksum: str
    written_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sink_id": self.sink_id,
            "object_uri": self.object_uri,
            "record_type": self.record_type,
            "record_id": self.record_id,
            "checksum": self.checksum,
            "written_at": self.written_at.isoformat(),
        }


class AuditWormSink(Protocol):
    """Append-only external sink used by audit stores after integrity stamping."""

    @property
    def sink_id(self) -> str: ...

    def write_audit_event(self, event: Any) -> AuditWormReceipt: ...

    def write_retained_evidence(self, record: Any) -> AuditWormReceipt: ...


class LocalAppendOnlyWormSink:
    """Append-only local sink used for CI and development.

    This is not a replacement for production WORM storage. It exists so the
    runtime code path is exercised without GCP credentials; each object is
    created with exclusive-create semantics and never overwritten.
    """

    def __init__(self, root: str | Path, *, sink_id: str | None = None) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._sink_id = sink_id or f"file://{self._root.resolve()}"

    @property
    def sink_id(self) -> str:
        return self._sink_id

    def write_audit_event(self, event: Any) -> AuditWormReceipt:
        return self._write(
            record_type="audit-events",
            record_id=str(event.event_id),
            sequence=getattr(event, "sequence", None),
            payload=event.to_dict(),
        )

    def write_retained_evidence(self, record: Any) -> AuditWormReceipt:
        return self._write(
            record_type="retained-evidence",
            record_id=str(record.export_id),
            sequence=getattr(record, "sequence", None),
            payload=record.to_dict(),
        )

    def _write(
        self,
        *,
        record_type: str,
        record_id: str,
        sequence: int | None,
        payload: dict[str, Any],
    ) -> AuditWormReceipt:
        checksum = sha256_hex(payload)
        written_at = datetime.now(UTC)
        envelope = {
            "record_type": record_type,
            "record_id": record_id,
            "sequence": sequence,
            "checksum": checksum,
            "written_at": written_at.isoformat(),
            "payload": payload,
        }
        object_name = _object_name(
            record_type=record_type,
            record_id=record_id,
            sequence=sequence,
            checksum=checksum,
        )
        path = self._root / object_name
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(path, flags, 0o440)
        except FileExistsError as exc:
            raise AuditWormSinkError(
                f"WORM object already exists: {path}"
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(canonical_json(envelope))
            handle.write("\n")
        return AuditWormReceipt(
            sink_id=self.sink_id,
            object_uri=f"file://{path.resolve()}",
            record_type=record_type,
            record_id=record_id,
            checksum=checksum,
            written_at=written_at,
        )


class GcsWormEvidenceSink:
    """Google Cloud Storage JSON API writer using object-create semantics.

    The bucket IAM shape is defined in ``infra/terraform/audit``: product
    runtime impersonates a writer service account with ``storage.objectCreator``.
    The upload uses ``ifGenerationMatch=0`` so an existing object cannot be
    overwritten even if the caller retries with the same object name.
    """

    def __init__(
        self,
        sink_uri: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not sink_uri.startswith("gs://"):
            raise ValueError("GCS WORM sink URI must start with gs://")
        without_scheme = sink_uri[5:]
        bucket, _, prefix = without_scheme.partition("/")
        if not bucket:
            raise ValueError("GCS WORM sink URI requires a bucket name")
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._sink_id = sink_uri.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds

    @property
    def sink_id(self) -> str:
        return self._sink_id

    def write_audit_event(self, event: Any) -> AuditWormReceipt:
        return self._write(
            record_type="audit-events",
            record_id=str(event.event_id),
            sequence=getattr(event, "sequence", None),
            payload=event.to_dict(),
        )

    def write_retained_evidence(self, record: Any) -> AuditWormReceipt:
        return self._write(
            record_type="retained-evidence",
            record_id=str(record.export_id),
            sequence=getattr(record, "sequence", None),
            payload=record.to_dict(),
        )

    def _write(
        self,
        *,
        record_type: str,
        record_id: str,
        sequence: int | None,
        payload: dict[str, Any],
    ) -> AuditWormReceipt:
        token = self._token or _configured_gcs_token()
        if not token:
            raise AuditWormSinkError(
                "GCS WORM sink requires ODP_AUDIT_WORM_GCS_TOKEN or "
                "GOOGLE_OAUTH_ACCESS_TOKEN"
            )
        checksum = sha256_hex(payload)
        written_at = datetime.now(UTC)
        envelope = {
            "record_type": record_type,
            "record_id": record_id,
            "sequence": sequence,
            "checksum": checksum,
            "written_at": written_at.isoformat(),
            "payload": payload,
        }
        object_name = _object_name(
            record_type=record_type,
            record_id=record_id,
            sequence=sequence,
            checksum=checksum,
        )
        if self._prefix:
            object_name = f"{self._prefix}/{object_name}"
        endpoint = (
            "https://storage.googleapis.com/upload/storage/v1/b/"
            f"{urllib.parse.quote(self._bucket, safe='')}/o"
            "?uploadType=media"
            f"&name={urllib.parse.quote(object_name, safe='')}"
            "&ifGenerationMatch=0"
        )
        data = canonical_json(envelope).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Content-Length": str(len(data)),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds):
                pass
        except OSError as exc:
            raise AuditWormSinkError(f"GCS WORM write failed: {exc}") from exc
        return AuditWormReceipt(
            sink_id=self.sink_id,
            object_uri=f"gs://{self._bucket}/{object_name}",
            record_type=record_type,
            record_id=record_id,
            checksum=checksum,
            written_at=written_at,
        )


def build_audit_worm_sink_from_env(
    *,
    default_root: str | Path = ".odp_data/audit-worm",
) -> AuditWormSink:
    """Build the configured runtime sink.

    ``ODP_AUDIT_WORM_SINK_URI=gs://bucket/prefix`` selects the production GCS
    writer. ``file:///path`` selects the local append-only writer. With no env
    set, local runs still exercise the WORM write path under ``default_root``.
    """

    sink_uri = os.environ.get("ODP_AUDIT_WORM_SINK_URI", "").strip()
    if sink_uri.startswith("gs://"):
        return GcsWormEvidenceSink(sink_uri)
    if sink_uri.startswith("file://"):
        return LocalAppendOnlyWormSink(Path(sink_uri[7:]), sink_id=sink_uri.rstrip("/"))
    if sink_uri:
        return LocalAppendOnlyWormSink(Path(sink_uri), sink_id=f"file://{Path(sink_uri).resolve()}")
    local_root = os.environ.get("ODP_AUDIT_WORM_LOCAL_PATH", "").strip()
    return LocalAppendOnlyWormSink(local_root or default_root)


def _configured_gcs_token() -> str:
    return (
        os.environ.get("ODP_AUDIT_WORM_GCS_TOKEN", "").strip()
        or os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN", "").strip()
    )


def _object_name(
    *,
    record_type: str,
    record_id: str,
    sequence: int | None,
    checksum: str,
) -> str:
    prefix = f"{sequence:020d}" if sequence is not None else "unsequenced"
    return f"{record_type}/{prefix}-{_safe_object_token(record_id)}-{checksum[:16]}.json"


def _safe_object_token(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)


__all__ = [
    "AuditWormReceipt",
    "AuditWormSink",
    "AuditWormSinkError",
    "GcsWormEvidenceSink",
    "LocalAppendOnlyWormSink",
    "build_audit_worm_sink_from_env",
]
