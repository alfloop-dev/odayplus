"""Geocode provider error contract retained after XR-CUTOVER-001.

The live geocode client that used to raise these was decommissioned with the
rest of odayplus-side external ingestion by XR-CUTOVER-001. The *error
taxonomy* has to outlive it:
:class:`~apps.data_platform.geography_backfill.PlaceGeographyBackfill` takes
its geocode provider by injection and still has to tell a per-address
rejection (HTTP 400 for one address — skip it and keep going) apart from an
infrastructure failure (auth, quota, timeout, 5xx — abort the batch without
advancing its checkpoint).

These are exception types only: no endpoint, no credential, no HTTP client.
"""

from __future__ import annotations


class GeocodeProviderError(RuntimeError):
    """Fail-closed geocode provider error with redacted rendering."""

    def __init__(
        self,
        message: str,
        *,
        provider_id: str,
        correlation_id: str,
        code: str,
        status_code: int | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.correlation_id = correlation_id
        self.code = code
        self.status_code = status_code
        super().__init__(
            f"{message} (provider_id={provider_id}, correlation_id={correlation_id}, code={code})"
        )


class GeocodeProviderAuthError(GeocodeProviderError):
    """Raised when the geocoder refuses credentials."""


class GeocodeProviderTimeoutError(GeocodeProviderError):
    """Raised when the geocoder request times out."""


class GeocodeProviderRateLimitError(GeocodeProviderError):
    """Raised when the geocoder reports a retryable quota limit."""


class GeocodeQuarantineError(GeocodeProviderError):
    """Raised when a geocode provider fails repeatedly and is quarantined."""

    def __init__(
        self,
        message: str,
        *,
        provider_id: str,
        correlation_id: str,
        code: str = "quarantined",
    ) -> None:
        super().__init__(
            message,
            provider_id=provider_id,
            correlation_id=correlation_id,
            code=code,
        )


__all__ = [
    "GeocodeProviderAuthError",
    "GeocodeProviderError",
    "GeocodeProviderRateLimitError",
    "GeocodeProviderTimeoutError",
    "GeocodeQuarantineError",
]
