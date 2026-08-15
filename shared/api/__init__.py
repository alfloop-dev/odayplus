"""Platform HTTP boundary primitives (ODP-PGAP-API-001).

``shared/`` supplied auth, audit, jobs and observability primitives but nothing
for the HTTP boundary itself, so each of the 14 routers reinvented its own error
shape, pagination envelope, tenant handling and idempotency store. This package
is the missing layer: one error envelope, one pagination contract, one
idempotency policy, one versioning rule.

Imports are lazy (PEP 562). ``shared.api.errors`` and ``versioning`` need
FastAPI, and this package is imported from dependency-light contexts (the
OpenAPI export script, orchestrator tooling) where importing FastAPI eagerly
would fail -- the same import-cycle/landmine pattern ``modules/external_data``
already uses.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ApiError",
    "ErrorCode",
    "ErrorEnvelope",
    "ERROR_RESPONSES",
    "error_response_body",
    "install_error_handlers",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "Page",
    "PageParams",
    "page_params",
    "paginate",
    "IdempotencyConflictError",
    "IdempotencyOutcome",
    "IdempotencyStore",
    "REPLAY_FIELD",
    "apply_replay_marker",
    "request_fingerprint",
    "API_V1_PREFIX",
    "mount_versioned",
    "install_deprecation_headers",
    "alias_paths",
    "versioned_paths",
]

_EXPORTS: dict[str, str] = {
    "ApiError": "shared.api.errors",
    "ErrorCode": "shared.api.errors",
    "ErrorEnvelope": "shared.api.errors",
    "ERROR_RESPONSES": "shared.api.errors",
    "error_response_body": "shared.api.errors",
    "install_error_handlers": "shared.api.errors",
    "DEFAULT_LIMIT": "shared.api.pagination",
    "MAX_LIMIT": "shared.api.pagination",
    "Page": "shared.api.pagination",
    "PageParams": "shared.api.pagination",
    "page_params": "shared.api.pagination",
    "paginate": "shared.api.pagination",
    "IdempotencyConflictError": "shared.api.idempotency",
    "IdempotencyOutcome": "shared.api.idempotency",
    "IdempotencyStore": "shared.api.idempotency",
    "REPLAY_FIELD": "shared.api.idempotency",
    "apply_replay_marker": "shared.api.idempotency",
    "request_fingerprint": "shared.api.idempotency",
    "API_V1_PREFIX": "shared.api.versioning",
    "mount_versioned": "shared.api.versioning",
    "install_deprecation_headers": "shared.api.versioning",
    "alias_paths": "shared.api.versioning",
    "versioned_paths": "shared.api.versioning",
}


def __getattr__(name: str) -> Any:
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted(__all__)
