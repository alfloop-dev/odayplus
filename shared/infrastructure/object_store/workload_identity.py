"""Bounded keyless object-store credentials from the runtime metadata service."""
from __future__ import annotations

import math
from threading import Lock
from typing import Any

_STORAGE_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"


class ObjectStoreWorkloadIdentityError(RuntimeError):
    """No current token can be obtained from the configured runtime identity."""


class MetadataObjectStoreCredentials:
    """Cache and refresh metadata credentials without ambient ADC/key fallback."""

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Object-store identity timeout must be positive")
        self._timeout_seconds = timeout_seconds
        self._credentials: Any = None
        self._request: Any = None
        self._lock = Lock()

    def token(self) -> str:
        try:
            with self._lock:
                if self._credentials is None:
                    from google.auth import compute_engine
                    from google.auth.transport.requests import Request

                    self._credentials = compute_engine.Credentials(
                        scopes=[_STORAGE_SCOPE], universe_domain="googleapis.com"
                    )
                    self._request = Request()
                credentials = self._credentials
                if not credentials.valid or not credentials.token:
                    credentials.refresh(self._bounded_request)
                if not credentials.valid or not isinstance(credentials.token, str) or not credentials.token.strip():
                    raise ValueError("No usable current object-store token")
                return credentials.token.strip()
        except Exception:
            # SDK exceptions may contain complete metadata responses. Construct
            # the public error outside the handler to retain no private chain.
            pass
        raise ObjectStoreWorkloadIdentityError("Object-store workload identity is unavailable") from None

    def _bounded_request(self, url: str, method: str = "GET", body: bytes | None = None,
                         headers: dict[str, str] | None = None, timeout: float | None = None,
                         **kwargs: Any) -> Any:
        kwargs["allow_redirects"] = False
        return self._request(url=url, method=method, body=body, headers=headers,
                             timeout=min(timeout, self._timeout_seconds) if timeout is not None
                             else self._timeout_seconds, **kwargs)
