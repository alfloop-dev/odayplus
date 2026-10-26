"""Verify actual object bytes before accepting a Survey attachment reference.

External object creation is not a SQL transaction. A failed SQL append can leave
an owned immutable object, which an exact same-key retry verifies and recovers.
It is never advertised as a committed attachment until its SQL command commits.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from uuid import uuid4

from apps.api.oday_api.runtime_mode import live_data_required
from modules.market_survey.infrastructure.assignment_repository import SurveyOwnerUnavailable
from shared.infrastructure.object_store.client import GcsObjectStore, parse_gs_uri


class SurveyContentConflict(ValueError):
    pass


class SurveyWorkloadCredentials:
    """Refresh existing Google ADC/workload identity, never request-body claims."""
    def __init__(self, *, credentials: Any = None, refresh_request: Any = None):
        self.credentials, self.refresh_request = credentials, refresh_request
        self.lock = threading.Lock()

    def token(self) -> str:
        with self.lock:
            try:
                if self.credentials is None:
                    import google.auth
                    self.credentials, _project = google.auth.default(
                        scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
                if not self.credentials.valid or not self.credentials.token:
                    if self.refresh_request is None:
                        from google.auth.transport.requests import Request
                        transport = Request()

                        def bounded_request(*args, **kwargs):
                            kwargs.setdefault("timeout", 10.0)
                            return transport(*args, **kwargs)
                        self.refresh_request = bounded_request
                    self.credentials.refresh(self.refresh_request)
                token = self.credentials.token
                if not isinstance(token, str) or not token.strip() or not self.credentials.valid:
                    raise ValueError("No current workload token")
                return token.strip()
            except Exception as exc:
                # Do not expose token values, metadata responses or credentials.
                raise SurveyOwnerUnavailable("SURVEY_WORKLOAD_CREDENTIAL_UNAVAILABLE") from exc


class _NoSurveyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SurveyGcsObjectStore(GcsObjectStore):
    """Existing residency/prefix gates, ADC, exact generation and real metadata.

    JSON API multipart upload carries custom SHA metadata together with actual
    bytes. Media-only upload headers are not treated as verified metadata.
    """
    def __init__(self, provider: SurveyWorkloadCredentials, *, opener: Any = None):
        super().__init__()
        self.provider = provider
        self.opener = opener or urllib.request.build_opener(_NoSurveyRedirect())

    def _get_token(self) -> str:
        # Never borrow the audit object-creator token for photograph reads.
        return self.provider.token()

    def _object(self, tenant_id: str, uri: str) -> tuple[str, str]:
        bucket, key = parse_gs_uri(uri)
        self._enforce_residency(tenant_id, bucket)
        self._enforce_tenant_prefix(tenant_id, key)
        return bucket, key

    def _json(self, request: Any) -> dict:
        try:
            with self.opener.open(request, timeout=self._timeout_seconds) as response:
                data = response.read(65537)
            if len(data) > 65536:
                raise ValueError("Storage metadata response too large")
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError("Storage metadata response invalid")
            return value
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code == 404:
                raise FileNotFoundError("Survey content object not found") from exc
            if code == 412:
                raise SurveyContentConflict("SURVEY_CONTENT_CREATE_PRECONDITION") from exc
            raise SurveyOwnerUnavailable("SURVEY_CONTENT_STORAGE_UNAVAILABLE") from exc
        except (OSError, ValueError) as exc:
            raise SurveyOwnerUnavailable("SURVEY_CONTENT_STORAGE_UNAVAILABLE") from exc

    @staticmethod
    def _integer(value: Any) -> int:
        if type(value) is int:
            return value
        if isinstance(value, str) and value.isascii() and value.isdecimal():
            return int(value)
        raise ValueError("Storage integer metadata invalid")

    def upload_object(self, tenant_id: str, bucket: str, key: str, data: bytes,
                      content_type: str, if_generation_match: int | None = 0) -> tuple[str, int]:
        self._enforce_residency(tenant_id, bucket)
        self._enforce_tenant_prefix(tenant_id, key)
        if if_generation_match != 0:
            raise SurveyContentConflict("SURVEY_CONTENT_CREATE_ONLY")
        token = self._get_token()
        digest = hashlib.sha256(data).hexdigest()
        metadata = {"name": key, "contentType": content_type, "metadata": {"sha256": digest}}
        boundary = "odp-survey-" + uuid4().hex
        while boundary.encode() in data:
            boundary = "odp-survey-" + uuid4().hex
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
            + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode()
            + f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
            + data + f"\r\n--{boundary}--\r\n".encode())
        endpoint = ("https://storage.googleapis.com/upload/storage/v1/b/"
                    + urllib.parse.quote(bucket, safe="") + "/o?uploadType=multipart&ifGenerationMatch=0")
        value = self._json(urllib.request.Request(endpoint, method="POST", data=body, headers={
            "Authorization": "Bearer " + token, "Content-Type": f"multipart/related; boundary={boundary}",
            "Content-Length": str(len(body))}))
        try:
            generation = self._integer(value["generation"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SurveyOwnerUnavailable("SURVEY_CONTENT_GENERATION_INVALID") from exc
        if generation < 1:
            raise SurveyOwnerUnavailable("SURVEY_CONTENT_GENERATION_INVALID")
        return f"gs://{bucket}/{key}", generation

    def head_object(self, tenant_id: str, uri: str) -> dict[str, Any]:
        bucket, key = self._object(tenant_id, uri)
        endpoint = ("https://storage.googleapis.com/storage/v1/b/" + urllib.parse.quote(bucket, safe="")
                    + "/o/" + urllib.parse.quote(key, safe=""))
        value = self._json(urllib.request.Request(endpoint, method="GET",
            headers={"Authorization": "Bearer " + self._get_token()}))
        try:
            result = {"generation": self._integer(value["generation"]), "size": self._integer(value["size"]),
                      "sha256": value["metadata"]["sha256"], "content_type": value.get("contentType", "")}
            if result["generation"] < 1 or result["size"] < 1 or not isinstance(result["sha256"], str):
                raise ValueError("Invalid content metadata")
            return result
        except (KeyError, TypeError, ValueError) as exc:
            raise SurveyOwnerUnavailable("SURVEY_CONTENT_HEAD_MISMATCH") from exc

    def download_object(self, tenant_id: str, uri: str, generation: int | None = None) -> bytes:
        bucket, key = self._object(tenant_id, uri)
        head = self.head_object(tenant_id, uri)
        if type(generation) is not int or generation < 1 or head["generation"] != generation:
            raise SurveyOwnerUnavailable("SURVEY_CONTENT_GENERATION_INVALID")
        endpoint = ("https://storage.googleapis.com/storage/v1/b/" + urllib.parse.quote(bucket, safe="")
                    + "/o/" + urllib.parse.quote(key, safe="") + f"?alt=media&generation={generation}")
        request = urllib.request.Request(endpoint, method="GET", headers={"Authorization": "Bearer " + self._get_token()})
        try:
            with self.opener.open(request, timeout=self._timeout_seconds) as response:
                data = response.read(head["size"] + 1)
            if len(data) != head["size"]:
                raise SurveyOwnerUnavailable("SURVEY_CONTENT_BYTES_MISMATCH")
            return data
        except urllib.error.HTTPError as exc:
            exc.close()
            raise SurveyOwnerUnavailable("SURVEY_CONTENT_READ_UNAVAILABLE") from exc
        except (OSError, ValueError) as exc:
            raise SurveyOwnerUnavailable("SURVEY_CONTENT_READ_UNAVAILABLE") from exc


class VerifiedSurveyObjectContent:
    def __init__(self, store: Any, bucket: str):
        if not bucket or not isinstance(bucket, str) or "/" in bucket or bucket != bucket.strip():
            raise SurveyOwnerUnavailable("SURVEY_CONTENT_BUCKET_UNAVAILABLE")
        self.store, self.bucket = store, bucket

    @classmethod
    def configured(cls, *, store: Any = None, bucket: str | None = None,
                   credentials: SurveyWorkloadCredentials | None = None):
        production = live_data_required()
        bucket = bucket or os.environ.get("ODP_SOURCE_SNAPSHOT_BUCKET", "").strip()
        if store is not None:
            # Explicit local engineering injection is not a production fallback.
            # Production uses this lane's refreshable workload credentials and
            # generation-pinned content, never a base audit-writer GCS client.
            if production and not isinstance(store, SurveyGcsObjectStore):
                raise SurveyOwnerUnavailable("SURVEY_PRODUCTION_GCS_REQUIRED")
        else:
            if os.environ.get("ODP_OBJECT_STORE", "").strip() not in {"", "gcs"}:
                raise SurveyOwnerUnavailable("SURVEY_DURABLE_CONTENT_OWNER_REQUIRED")
            # Resolve/refresh lazily during an authorized byte operation, using
            # the repo's existing google-auth dependency and workload identity.
            store = SurveyGcsObjectStore(credentials or SurveyWorkloadCredentials())
        return cls(store, bucket)

    def _expected_uri(self, tenant: str, key: str) -> str:
        # Residency/tenant prefix are also enforced by the actual ObjectStore.
        if not key.startswith(f"tenants/{tenant}/"):
            raise SurveyOwnerUnavailable("SURVEY_CONTENT_TENANT_BINDING_INVALID")
        return f"gs://{self.bucket}/{key}"

    def _read(self, tenant: str, uri: str, generation: int, digest: str, size: int) -> bytes:
        try:
            head = self.store.head_object(tenant, uri)
            if (type(generation) is not int or generation < 1
                    or not isinstance(head, dict) or type(head.get("generation")) is not int
                    or head["generation"] != generation or type(head.get("size")) is not int or head["size"] != size
                    or head.get("sha256") != digest):
                raise SurveyOwnerUnavailable("SURVEY_CONTENT_HEAD_MISMATCH")
            data = self.store.download_object(tenant, uri, generation=generation)
            if (not isinstance(data, bytes) or len(data) != size
                    or hashlib.sha256(data).hexdigest() != digest):
                raise SurveyOwnerUnavailable("SURVEY_CONTENT_BYTES_MISMATCH")
            return data
        except SurveyOwnerUnavailable:
            raise
        except Exception as exc:
            raise SurveyOwnerUnavailable("SURVEY_CONTENT_READ_UNAVAILABLE") from exc

    def put_verified(self, *, tenant: str, key: str, data: bytes, content_type: str) -> tuple[str, int]:
        if not isinstance(data, bytes) or not data:
            raise ValueError("SURVEY_ATTACHMENT_BYTES_REQUIRED")
        uri = self._expected_uri(tenant, key)
        digest = hashlib.sha256(data).hexdigest()
        try:
            received_uri, generation = self.store.upload_object(tenant, self.bucket, key, data,
                content_type, if_generation_match=0)
            if received_uri != uri:
                raise SurveyOwnerUnavailable("SURVEY_CONTENT_URI_MISMATCH")
        except ValueError as exc:
            # Recovery does not rely on provider exception text or trust a URI
            # supplied by a caller. Only the exact server key is considered.
            try:
                head = self.store.head_object(tenant, uri)
            except Exception as read_error:
                raise SurveyOwnerUnavailable("SURVEY_CONTENT_UPLOAD_UNCONFIRMED") from read_error
            if (not isinstance(head, dict) or head.get("sha256") != digest
                    or head.get("size") != len(data)):
                raise SurveyContentConflict("SURVEY_CONTENT_EXISTING_BYTES_CONFLICT") from exc
            generation = head.get("generation")
        except SurveyOwnerUnavailable:
            raise
        except Exception as exc:
            raise SurveyOwnerUnavailable("SURVEY_CONTENT_UPLOAD_UNAVAILABLE") from exc
        self._read(tenant, uri, generation, digest, len(data))
        return uri, generation

    def read_verified(self, record: Any) -> bytes:
        # Reject wrong configured bucket/prefix before invoking storage I/O.
        expected = (f"gs://{self.bucket}/tenants/{record.tenant_id}/market-survey/assignments/"
                    f"{record.assignment_id}/attachments/{record.media_id}")
        if record.object_uri != expected:
            raise SurveyOwnerUnavailable("SURVEY_CONTENT_OBJECT_BINDING_INVALID")
        return self._read(record.tenant_id, record.object_uri, record.object_generation,
                          record.sha256, record.size_bytes)
