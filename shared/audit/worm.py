"""Runtime WORM sink writers for audit events and retained evidence.

The product database is useful for query and restore, but immutable governance
evidence also needs a write path outside product-owned mutable tables. This
module keeps that path dependency-light: production can point at a GCS bucket
with object-creator credentials, while tests and local runs use an append-only
file sink that rejects overwrites.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from shared.audit.gcs_identity import AuditWorkloadIdentityError, ImpersonatedAuditCredentials
from shared.audit.integrity import canonical_json, sha256_hex


class AuditWormSinkError(RuntimeError):
    """Raised when the external immutable sink rejects an audit write."""


class _NoGcsRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Neither the writer nor the recovery credential may leave the
        # configured Cloud Storage endpoint via an HTTP redirect.
        return None


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

    def lookup_audit_event(self, event: Any) -> AuditWormReceipt | None:
        """Verify an existing immutable object after a delivery interruption."""
        checksum = sha256_hex(event.to_dict())
        path = self._root / _object_name(
            record_type="audit-events",
            record_id=event.event_id,
            sequence=event.sequence,
            checksum=checksum,
        )
        if not path.exists():
            return None
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if (
            envelope.get("record_id") != event.event_id
            or envelope.get("checksum") != checksum
            or envelope.get("payload") != event.to_dict()
        ):
            raise AuditWormSinkError("existing WORM object does not match the audit event")
        return AuditWormReceipt(
            sink_id=self.sink_id,
            object_uri=path.resolve().as_uri(),
            record_type="audit-events",
            record_id=event.event_id,
            checksum=checksum,
            written_at=datetime.fromisoformat(envelope["written_at"]),
        )

    def write_retained_evidence(self, record: Any) -> AuditWormReceipt:
        return self._write(
            record_type="retained-evidence",
            record_id=str(record.export_id),
            sequence=getattr(record, "sequence", None),
            payload=record.to_dict(),
        )

    def lookup_retained_evidence(self, record: Any) -> AuditWormReceipt | None:
        """Recover only the exact retained immutable object after a lost response."""
        checksum = sha256_hex(record.to_dict())
        path = self._root / _object_name(record_type="retained-evidence", record_id=record.export_id,
                                         sequence=record.sequence, checksum=checksum)
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            written_at = datetime.fromisoformat(envelope["written_at"])
            if (envelope.get("record_type") != "retained-evidence"
                    or envelope.get("record_id") != record.export_id
                    or type(envelope.get("sequence")) is not type(record.sequence)
                    or envelope.get("sequence") != record.sequence
                    or envelope.get("checksum") != checksum
                    or envelope.get("payload") != record.to_dict()
                    or written_at.utcoffset() is None):
                raise ValueError("Retained immutable bytes differ")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise AuditWormSinkError("Existing WORM object does not match retained evidence") from exc
        return AuditWormReceipt(self.sink_id, path.resolve().as_uri(), "retained-evidence",
                                record.export_id, checksum, written_at)

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
            raise AuditWormSinkError(f"WORM object already exists: {path}") from exc
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
    After a 412, recovery uses only an explicitly configured read credential
    to verify the exact existing object. It never reuses the create-only
    writer credential, and never marks an unreadable object delivered.
    """

    def __init__(
        self,
        sink_uri: str,
        *,
        token: str | None = None,
        recovery_token: str | None = None,
        writer_service_account: str | None = None,
        recovery_service_account: str | None = None,
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
        self._recovery_token = recovery_token
        self._timeout_seconds = timeout_seconds
        self._workload_identity = None
        if writer_service_account is not None or recovery_service_account is not None:
            if token is not None or recovery_token is not None:
                raise ValueError("Explicit tokens cannot be mixed with workload identity")
            self._workload_identity = ImpersonatedAuditCredentials(
                writer_service_account or "", recovery_service_account or "",
                timeout_seconds=timeout_seconds,
            )
        self._opener = urllib.request.build_opener(_NoGcsRedirect())

    @property
    def sink_id(self) -> str:
        return self._sink_id

    def _access_token(self, purpose: str) -> str:
        if self._workload_identity is not None:
            try:
                return self._workload_identity.token(purpose)
            except AuditWorkloadIdentityError as exc:
                raise AuditWormSinkError("Audit workload identity token is unavailable") from exc
        if purpose == "write":
            return self._token or _configured_gcs_token()
        return (self._recovery_token if self._recovery_token is not None
                else os.environ.get("ODP_AUDIT_WORM_GCS_RECOVERY_TOKEN", "").strip())

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

    def lookup_retained_evidence(self, record: Any) -> AuditWormReceipt | None:
        """Read only with the existing explicit recovery credential, never writer."""
        if self._workload_identity is None and not (
            self._recovery_token or os.environ.get("ODP_AUDIT_WORM_GCS_RECOVERY_TOKEN", "").strip()
        ):
            return None
        checksum = sha256_hex(record.to_dict())
        name = _object_name(record_type="retained-evidence", record_id=record.export_id,
                            sequence=record.sequence, checksum=checksum)
        if self._prefix:
            name = self._prefix + "/" + name
        return self._recover_existing(object_name=name, record_type="retained-evidence",
                                      record_id=record.export_id, sequence=record.sequence,
                                      checksum=checksum,
                                      max_response_bytes=len(canonical_json(record.to_dict()).encode()) + 4096,
                                      missing_ok=True)

    def _write(
        self,
        *,
        record_type: str,
        record_id: str,
        sequence: int | None,
        payload: dict[str, Any],
    ) -> AuditWormReceipt:
        token = self._access_token("write")
        if not token:
            raise AuditWormSinkError(
                "GCS WORM sink requires ODP_AUDIT_WORM_GCS_TOKEN or GOOGLE_OAUTH_ACCESS_TOKEN"
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
            with self._opener.open(request, timeout=self._timeout_seconds):
                pass
        except urllib.error.HTTPError as exc:
            if exc.code == 412:
                exc.close()
                return self._recover_existing(
                    object_name=object_name,
                    record_type=record_type,
                    record_id=record_id,
                    sequence=sequence,
                    checksum=checksum,
                    max_response_bytes=len(data) + 4096,
                )
            exc.close()
            raise AuditWormSinkError(f"GCS WORM write failed with HTTP {exc.code}") from exc
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

    def _recover_existing(
        self,
        *,
        object_name: str,
        record_type: str,
        record_id: str,
        sequence: int | None,
        checksum: str,
        max_response_bytes: int,
        missing_ok: bool = False,
    ) -> AuditWormReceipt | None:
        token = self._access_token("recover")
        if not token:
            raise AuditWormSinkError(
                "Existing GCS WORM object requires an explicitly configured recovery credential"
            )
        endpoint = (
            "https://storage.googleapis.com/storage/v1/b/"
            f"{urllib.parse.quote(self._bucket, safe='')}/o/"
            f"{urllib.parse.quote(object_name, safe='')}?alt=media"
        )
        request = urllib.request.Request(
            endpoint,
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                data = response.read(max_response_bytes + 1)
                if len(data) > max_response_bytes:
                    raise ValueError("GCS WORM recovery object is larger than the expected record")
                envelope = json.loads(data)
        except urllib.error.HTTPError as exc:
            exc.close()
            if missing_ok and exc.code == 404:
                return None
            raise AuditWormSinkError(
                f"GCS WORM recovery read failed with HTTP {exc.code}"
            ) from exc
        except (OSError, ValueError) as exc:
            raise AuditWormSinkError("GCS WORM recovery read is unavailable or invalid") from exc
        if (
            not isinstance(envelope, dict)
            or envelope.get("record_type") != record_type
            or envelope.get("record_id") != record_id
            or type(envelope.get("sequence")) is not type(sequence)
            or envelope.get("sequence") != sequence
            or envelope.get("checksum") != checksum
            or sha256_hex(envelope.get("payload")) != checksum
        ):
            raise AuditWormSinkError("Existing GCS WORM object does not match the committed record")
        try:
            written_at = datetime.fromisoformat(envelope["written_at"])
            if written_at.tzinfo is None or written_at.utcoffset() is None:
                raise ValueError("WORM object timestamp must include its timezone")
        except (KeyError, TypeError, ValueError) as exc:
            raise AuditWormSinkError("Existing GCS WORM object timestamp is invalid") from exc
        return AuditWormReceipt(
            sink_id=self.sink_id,
            object_uri=f"gs://{self._bucket}/{object_name}",
            record_type=record_type,
            record_id=record_id,
            checksum=checksum,
            written_at=written_at,
        )


def is_gcs_worm_sink_uri(value: str) -> bool:
    """Validate an address without claiming bucket/IAM/retention readiness."""
    if (not value.startswith("gs://") or "?" in value or "#" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        return (
            parsed.scheme == "gs"
            and bool(parsed.netloc)
            and parsed.netloc == parsed.hostname
            and not any(char.isspace() for char in parsed.netloc)
            and not any(char in parsed.netloc for char in "@:\\")
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


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
    # Use the actual deployment target; dev/staging also use live data flags.
    deployment_mode = next(
        (os.environ[name].strip().lower()
         for name in ("ODP_DEPLOY_ENV", "ODAY_ENV", "ODP_ENV", "APP_ENV", "ENVIRONMENT")
         if os.environ.get(name, "").strip()),
        "development",
    )
    if deployment_mode in {"prod", "production"} and not is_gcs_worm_sink_uri(sink_uri):
        raise AuditWormSinkError("Production audit WORM requires a configured GCS sink URI")
    if sink_uri.startswith("gs://"):
        writer = os.environ.get("ODP_AUDIT_WORM_WRITER_SERVICE_ACCOUNT", "").strip()
        recovery = os.environ.get("ODP_AUDIT_WORM_RECOVERY_SERVICE_ACCOUNT", "").strip()
        if deployment_mode in {"prod", "production"} and not (writer and recovery):
            raise AuditWormSinkError("Production audit WORM requires separate workload identity writer and recovery service accounts")
        try:
            return GcsWormEvidenceSink(
                sink_uri, writer_service_account=writer or None,
                recovery_service_account=recovery or None,
            )
        except ValueError as exc:
            raise AuditWormSinkError("Audit WORM workload identity service accounts are invalid") from exc
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


def audit_event_object_name(record_id: str, sequence: int, checksum: str) -> str:
    """The existing deterministic object key for a verified audit record."""
    return _object_name(
        record_type="audit-events", record_id=record_id, sequence=sequence, checksum=checksum
    )


def _safe_object_token(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)


__all__ = [
    "AuditWormReceipt",
    "AuditWormSink",
    "AuditWormSinkError",
    "GcsWormEvidenceSink",
    "LocalAppendOnlyWormSink",
    "audit_event_object_name",
    "build_audit_worm_sink_from_env",
    "is_gcs_worm_sink_uri",
]
