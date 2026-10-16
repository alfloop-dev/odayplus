"""Keyless, purpose-separated audit credentials from the runtime metadata identity."""
from __future__ import annotations

import math
import re
from threading import Lock
from typing import Any, Literal

_SERVICE_ACCOUNT = re.compile(
    r"[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com"
)
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_STORAGE_SCOPES = {
    "write": "https://www.googleapis.com/auth/devstorage.read_write",
    "recover": "https://www.googleapis.com/auth/devstorage.read_only",
}


class AuditWorkloadIdentityError(RuntimeError):
    """The configured metadata identity cannot obtain the required limited token."""


def validate_audit_service_accounts(writer: str, recovery: str) -> None:
    """Reject ambiguous identities and a shared read/write principal before I/O."""
    if not _SERVICE_ACCOUNT.fullmatch(writer) or not _SERVICE_ACCOUNT.fullmatch(recovery):
        raise ValueError("Audit writer and recovery identities must be explicit service account emails")
    if writer == recovery:
        raise ValueError("Audit writer and recovery service accounts must differ")


class ImpersonatedAuditCredentials:
    """Refresh separate short-lived tokens; never send the runtime token to GCS.

    Source credentials always use the Cloud Run/Compute metadata service. An
    ambient developer login, service-account key file or OAuth token variable
    cannot replace this metadata identity. Object authority remains limited by
    the target service accounts' bucket IAM grants.
    """

    def __init__(self, writer: str, recovery: str, *, timeout_seconds: float = 10.0) -> None:
        validate_audit_service_accounts(writer, recovery)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Audit identity timeout must be positive")
        self._principals = {"write": writer, "recover": recovery}
        self._timeout_seconds = timeout_seconds
        self._credentials: dict[str, Any] = {}
        self._source: Any = None
        self._request: Any = None
        self._lock = Lock()

    def token(self, purpose: Literal["write", "recover"]) -> str:
        if purpose not in self._principals:
            raise ValueError("Unknown audit credential purpose")
        try:
            with self._lock:
                if self._source is None:
                    from google.auth import compute_engine
                    from google.auth.transport.requests import Request

                    self._source = compute_engine.Credentials(
                        scopes=[_CLOUD_PLATFORM_SCOPE], universe_domain="googleapis.com"
                    )
                    self._request = Request()
                credential = self._credentials.get(purpose)
                if credential is None:
                    from google.auth import impersonated_credentials

                    credential = impersonated_credentials.Credentials(
                        source_credentials=self._source,
                        target_principal=self._principals[purpose],
                        target_scopes=[_STORAGE_SCOPES[purpose]],
                        lifetime=300,
                    )
                    self._credentials[purpose] = credential
                if not credential.valid:
                    credential.refresh(self._bounded_request)
                if not isinstance(credential.token, str) or not credential.token.strip():
                    raise AuditWorkloadIdentityError("Audit workload identity returned no usable token")
                return credential.token
        except AuditWorkloadIdentityError:
            raise
        except Exception:
            # Some SDK parsing errors carry the full IAM response, including
            # accessToken. Leave the handler before creating the public error:
            # neither its traceback nor its exception context may retain it.
            pass
        raise AuditWorkloadIdentityError("Audit workload identity token is unavailable") from None

    def _bounded_request(self, url: str, method: str = "GET", body: bytes | None = None,
                         headers: dict[str, str] | None = None, timeout: float | None = None,
                         **kwargs: Any) -> Any:
        return self._request(url=url, method=method, body=body, headers=headers,
                             timeout=min(timeout, self._timeout_seconds) if timeout is not None
                             else self._timeout_seconds, **kwargs)
