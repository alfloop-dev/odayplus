"""Object storage client interface and implementations with residency enforcement.

Provides both InMemory (unit/local tests) and GCS (production) backend
implementations, enforcing TW_ONLY residency limits before opening sockets or
touching files.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from typing import Any, Protocol


class ResidencyDeniedError(ValueError):
    """Raised when request attempts to write/read to/from a bucket violating tenant residency policy."""


class ObjectStore(Protocol):
    """Common storage protocol for raw or redacted immutable snapshots."""

    def upload_object(
        self,
        tenant_id: str,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
        if_generation_match: int | None = 0,
    ) -> str:
        """Upload data to bucket and return its canonical URI (e.g. gs://bucket/key)."""
        ...

    def download_object(self, tenant_id: str, uri: str) -> bytes:
        """Download and return object content as bytes."""
        ...

    def delete_object(self, tenant_id: str, uri: str, if_generation_match: int | None = None) -> None:
        """Delete object from storage."""
        ...

    def head_object(self, tenant_id: str, uri: str) -> dict[str, Any]:
        """Return metadata of object (generation, size, sha256, content_type)."""
        ...

    def list_objects(self, tenant_id: str, bucket: str) -> list[str]:
        """List all object keys in the bucket."""
        ...


def parse_gs_uri(uri: str) -> tuple[str, str]:
    """Parse gs://bucket/key URI into (bucket, key)."""
    if not uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {uri}. Must start with gs://")
    without_scheme = uri[5:]
    bucket, _, key = without_scheme.partition("/")
    if not bucket or not key:
        raise ValueError(f"Invalid GCS URI: {uri}")
    return bucket, key.strip("/")


RESIDENCY_APPROVED_BUCKETS: dict[str, set[str]] = {
    "TW_ONLY": {
        "snapshots-taiwan",
        "taiwan-snapshots",
        "tw-intake-snapshots",
        "taiwan-snapshots-dev",
        "taiwan-snapshots-prod",
    },
    "APPROVED_APAC_DR": {
        "snapshots-taiwan",
        "taiwan-snapshots",
        "tw-intake-snapshots",
        "taiwan-snapshots-dev",
        "taiwan-snapshots-prod",
        "snapshots-apac-dr",
        "apac-snapshots",
        "apac-snapshots-dr",
    }
}


def check_bucket_residency(residency_mode: str, bucket: str) -> None:
    """Enforce fail-closed residency mapping.
    
    Checks if bucket is explicitly allowed for the given residency_mode.
    """
    mode = (residency_mode or "").strip().upper()
    if mode not in RESIDENCY_APPROVED_BUCKETS:
        raise ResidencyDeniedError(f"RESIDENCY_DENIED: Unknown residency mode {mode!r}")
    
    allowed_set = set()
    # Check mode-specific env first, e.g. ODP_RESIDENCY_APPROVED_BUCKETS_TW_ONLY
    env_buckets_mode = os.environ.get(f"ODP_RESIDENCY_APPROVED_BUCKETS_{mode}")
    if env_buckets_mode:
        allowed_set.update(b.strip() for b in env_buckets_mode.split(",") if b.strip())
    else:
        # Fallback to general env
        env_buckets = os.environ.get("ODP_RESIDENCY_APPROVED_BUCKETS")
        if env_buckets:
            allowed_set.update(b.strip() for b in env_buckets.split(",") if b.strip())
    
    # Fallback/default mapping
    allowed_set.update(RESIDENCY_APPROVED_BUCKETS[mode])
        
    # If the bucket is not in the allowlist, fail-closed!
    if bucket not in allowed_set:
        raise ResidencyDeniedError(f"RESIDENCY_DENIED: Bucket {bucket!r} is not allowed for residency mode {mode!r}")



class InMemoryObjectStore:
    """In-memory object store for unit tests and local runs.

    Supports GCS generation precondition logic, custom metadata, and residency
    validation.
    """

    def __init__(self, tenant_residency_resolver: Any = None) -> None:
        # Structure: self._objects[bucket][key] = {
        #   "data": bytes,
        #   "generation": int,
        #   "content_type": str,
        #   "sha256": str,
        # }
        self._objects: dict[str, dict[str, dict[str, Any]]] = {}
        self._resolver = tenant_residency_resolver

    def _get_residency(self, tenant_id: str) -> str:
        if self._resolver:
            return self._resolver(tenant_id)
        return "TW_ONLY"

    def _enforce_residency(self, tenant_id: str, bucket: str) -> None:
        residency = self._get_residency(tenant_id)
        check_bucket_residency(residency, bucket)

    def _enforce_tenant_prefix(self, tenant_id: str, key: str) -> None:
        expected_prefix = f"tenants/{tenant_id}/"
        if not key.startswith(expected_prefix):
            raise ResidencyDeniedError(
                f"Access Denied: Object tenant prefix mismatch: {key!r} does not start with {expected_prefix!r}"
            )

    def upload_object(
        self,
        tenant_id: str,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
        if_generation_match: int | None = 0,
    ) -> tuple[str, int]:
        self._enforce_residency(tenant_id, bucket)
        self._enforce_tenant_prefix(tenant_id, key)
        self._objects.setdefault(bucket, {})
        bucket_data = self._objects[bucket]

        existing = bucket_data.get(key)
        existing_gen = existing["generation"] if existing else 0

        if if_generation_match is not None:
            if existing_gen != if_generation_match:
                # GCS Precondition Failed returns 412. In Python, raise a ValueError or similar.
                raise ValueError(f"Precondition Failed: generation match {if_generation_match} != {existing_gen}")

        next_gen = existing_gen + 1
        content_sha256 = hashlib.sha256(data).hexdigest()

        bucket_data[key] = {
            "data": data,
            "generation": next_gen,
            "content_type": content_type,
            "sha256": content_sha256,
        }
        return f"gs://{bucket}/{key}", next_gen

    def download_object(self, tenant_id: str, uri: str, generation: int | None = None) -> bytes:
        bucket, key = parse_gs_uri(uri)
        self._enforce_residency(tenant_id, bucket)
        self._enforce_tenant_prefix(tenant_id, key)
        if bucket not in self._objects or key not in self._objects[bucket]:
            raise FileNotFoundError(f"Object not found: {uri}")
        obj = self._objects[bucket][key]
        if generation is not None and obj["generation"] != generation:
            raise FileNotFoundError(f"Object generation {generation} not found for {uri}")
        return obj["data"]

    def delete_object(self, tenant_id: str, uri: str, if_generation_match: int | None = None) -> None:
        bucket, key = parse_gs_uri(uri)
        self._enforce_residency(tenant_id, bucket)
        self._enforce_tenant_prefix(tenant_id, key)
        if bucket not in self._objects or key not in self._objects[bucket]:
            raise FileNotFoundError(f"Object not found: {uri}")

        existing = self._objects[bucket][key]
        if if_generation_match is not None and existing["generation"] != if_generation_match:
            raise ValueError(f"Precondition Failed: generation match {if_generation_match} != {existing['generation']}")

        del self._objects[bucket][key]

    def head_object(self, tenant_id: str, uri: str) -> dict[str, Any]:
        bucket, key = parse_gs_uri(uri)
        self._enforce_residency(tenant_id, bucket)
        self._enforce_tenant_prefix(tenant_id, key)
        if bucket not in self._objects or key not in self._objects[bucket]:
            raise FileNotFoundError(f"Object not found: {uri}")
        obj = self._objects[bucket][key]
        return {
            "generation": obj["generation"],
            "size": len(obj["data"]),
            "sha256": obj["sha256"],
            "content_type": obj["content_type"],
        }

    def list_objects(self, tenant_id: str, bucket: str) -> list[str]:
        self._enforce_residency(tenant_id, bucket)
        bucket_data = self._objects.get(bucket, {})
        prefix = f"tenants/{tenant_id}/"
        return [k for k in bucket_data.keys() if k.startswith(prefix)]


class GcsObjectStore:
    """Production GCS object store client utilizing urllib.request.

    Enforces residency and respects token/timeout configurations.
    """

    def __init__(self, tenant_residency_resolver: Any = None, timeout_seconds: float = 10.0) -> None:
        self._resolver = tenant_residency_resolver
        self._timeout_seconds = timeout_seconds

    def _get_residency(self, tenant_id: str) -> str:
        if self._resolver:
            return self._resolver(tenant_id)
        return "TW_ONLY"

    def _enforce_residency(self, tenant_id: str, bucket: str) -> None:
        residency = self._get_residency(tenant_id)
        check_bucket_residency(residency, bucket)

    def _enforce_tenant_prefix(self, tenant_id: str, key: str) -> None:
        expected_prefix = f"tenants/{tenant_id}/"
        if not key.startswith(expected_prefix):
            raise ResidencyDeniedError(
                f"Access Denied: Object tenant prefix mismatch: {key!r} does not start with {expected_prefix!r}"
            )

    def _get_token(self) -> str:
        token = os.environ.get("ODP_AUDIT_WORM_GCS_TOKEN", "").strip() or os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN", "").strip()
        if not token:
            raise RuntimeError("GCS storage requires GOOGLE_OAUTH_ACCESS_TOKEN or ODP_AUDIT_WORM_GCS_TOKEN")
        return token

    def upload_object(
        self,
        tenant_id: str,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
        if_generation_match: int | None = 0,
    ) -> tuple[str, int]:
        self._enforce_residency(tenant_id, bucket)
        self._enforce_tenant_prefix(tenant_id, key)
        token = self._get_token()
        content_sha256 = hashlib.sha256(data).hexdigest()

        # GCS Media Upload Endpoint
        endpoint = (
            "https://storage.googleapis.com/upload/storage/v1/b/"
            f"{urllib.parse.quote(bucket, safe='')}/o"
            "?uploadType=media"
            f"&name={urllib.parse.quote(key, safe='')}"
        )
        if if_generation_match is not None:
            endpoint += f"&ifGenerationMatch={if_generation_match}"

        request = urllib.request.Request(
            endpoint,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": content_type,
                "Content-Length": str(len(data)),
                "x-goog-meta-sha256": content_sha256,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
                generation = int(body.get("generation", 0))
        except urllib.error.HTTPError as exc:
            if exc.code == 412:
                raise ValueError(f"Precondition Failed: generation match failed: {exc.read().decode('utf-8')}") from exc
            raise OSError(f"GCS upload failed: {exc.read().decode('utf-8')}") from exc
        except Exception as exc:
            raise OSError(f"GCS upload failed: {exc}") from exc

        return f"gs://{bucket}/{key}", generation

    def download_object(self, tenant_id: str, uri: str, generation: int | None = None) -> bytes:
        bucket, key = parse_gs_uri(uri)
        self._enforce_residency(tenant_id, bucket)
        self._enforce_tenant_prefix(tenant_id, key)
        token = self._get_token()

        endpoint = (
            "https://storage.googleapis.com/storage/v1/b/"
            f"{urllib.parse.quote(bucket, safe='')}/o/"
            f"{urllib.parse.quote(key, safe='')}"
            "?alt=media"
        )
        if generation is not None:
            endpoint += f"&generation={generation}"

        request = urllib.request.Request(
            endpoint,
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(f"Object not found: {uri}") from exc
            raise OSError(f"GCS download failed: {exc.read().decode('utf-8')}") from exc
        except Exception as exc:
            raise OSError(f"GCS download failed: {exc}") from exc

    def delete_object(self, tenant_id: str, uri: str, if_generation_match: int | None = None) -> None:
        bucket, key = parse_gs_uri(uri)
        self._enforce_residency(tenant_id, bucket)
        self._enforce_tenant_prefix(tenant_id, key)
        token = self._get_token()

        endpoint = (
            "https://storage.googleapis.com/storage/v1/b/"
            f"{urllib.parse.quote(bucket, safe='')}/o/"
            f"{urllib.parse.quote(key, safe='')}"
        )
        params = []
        if if_generation_match is not None:
            params.append(f"ifGenerationMatch={if_generation_match}")
        if params:
            endpoint += "?" + "&".join(params)

        request = urllib.request.Request(
            endpoint,
            method="DELETE",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(f"Object not found: {uri}") from exc
            if exc.code == 412:
                raise ValueError(f"Precondition Failed: generation match failed: {exc.read().decode('utf-8')}") from exc
            raise OSError(f"GCS delete failed: {exc.read().decode('utf-8')}") from exc
        except Exception as exc:
            raise OSError(f"GCS delete failed: {exc}") from exc

    def head_object(self, tenant_id: str, uri: str) -> dict[str, Any]:
        bucket, key = parse_gs_uri(uri)
        self._enforce_residency(tenant_id, bucket)
        self._enforce_tenant_prefix(tenant_id, key)
        token = self._get_token()

        endpoint = (
            "https://storage.googleapis.com/storage/v1/b/"
            f"{urllib.parse.quote(bucket, safe='')}/o/"
            f"{urllib.parse.quote(key, safe='')}"
        )
        request = urllib.request.Request(
            endpoint,
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
                metadata = body.get("metadata", {})
                sha256 = metadata.get("sha256", "")
                return {
                    "generation": int(body["generation"]),
                    "size": int(body["size"]),
                    "sha256": sha256,
                    "content_type": body.get("contentType", ""),
                }
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(f"Object not found: {uri}") from exc
            raise OSError(f"GCS head failed: {exc.read().decode('utf-8')}") from exc
        except Exception as exc:
            raise OSError(f"GCS head failed: {exc}") from exc

    def list_objects(self, tenant_id: str, bucket: str) -> list[str]:
        self._enforce_residency(tenant_id, bucket)
        token = self._get_token()
        prefix = f"tenants/{tenant_id}/"

        endpoint = (
            "https://storage.googleapis.com/storage/v1/b/"
            f"{urllib.parse.quote(bucket, safe='')}/o"
            f"?prefix={urllib.parse.quote(prefix, safe='')}"
        )
        request = urllib.request.Request(
            endpoint,
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
                items = body.get("items", [])
                return [item["name"] for item in items if "name" in item]
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            raise OSError(f"GCS list failed: {exc.read().decode('utf-8')}") from exc
        except Exception as exc:
            raise OSError(f"GCS list failed: {exc}") from exc
