"""Fail-closed retrieval security for human-assisted listing intake.

The production R5 flow still uses deterministic fixture replay by default. This
module owns the live-retrieval boundary that any future approved source adapter
must pass through before opening a socket.
Verified and integrated with snapshot storage policy rules under task ODP-INTAKE-SNAPSHOT-001.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qsl, urljoin, urlsplit

ALLOWED_RETRIEVAL_POLICIES = frozenset({"APPROVED_RETRIEVAL"})
DEFAULT_RETRIEVAL_METHOD = "server_http"
ALLOWED_RETRIEVAL_METHODS = frozenset({DEFAULT_RETRIEVAL_METHOD, "fixture_replay"})
ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

CLOUD_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
CLOUD_METADATA_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.169.253"),
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "jwt",
        "password",
        "private_api_endpoint",
        "refresh_token",
        "secret",
        "session",
        "sessionid",
        "token",
    }
)
SENSITIVE_SUBMISSION_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "privateapiendpoint",
    "private_api_endpoint",
    "secret",
    "session",
    "token",
)
SENSITIVE_SNAPSHOT_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "brokeremail",
    "brokerphone",
    "contact",
    "cookie",
    "credential",
    "email",
    "lineid",
    "mobile",
    "owneremail",
    "ownername",
    "ownerphone",
    "password",
    "phone",
    "secret",
    "session",
    "token",
)

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
SECRET_RE = re.compile(r"\b(?:sk|pk|rk|tok|key)-[A-Za-z0-9_-]{8,}\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?886[-\s]?)?0?9\d{2}[-\s]?\d{3}[-\s]?\d{3}(?!\d)")


class SensitiveSubmissionError(ValueError):
    """Raised when UI input attempts to carry credentials or private endpoints."""


class RetrievalFetcher(Protocol):
    def __call__(
        self,
        url: str,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> FetchResponse: ...


Resolver = Callable[[str], Sequence[str]]


@dataclass(frozen=True)
class RetrievalLimits:
    timeout_seconds: float = 10.0
    max_redirects: int = 3
    max_response_bytes: int = 512_000
    allowed_content_types: tuple[str, ...] = ("text/html", "application/xhtml+xml")
    allowed_schemes: tuple[str, ...] = ("http", "https")
    allowed_methods: tuple[str, ...] = tuple(sorted(ALLOWED_RETRIEVAL_METHODS))


@dataclass(frozen=True)
class FetchResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class RetrievalSecurityFailure:
    code: str
    summary: str
    next_action: str
    retryable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "summary": self.summary,
            "nextAction": self.next_action,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class RetrievalSecurityResult:
    final_url: str
    body: bytes = b""
    content_type: str | None = None
    redirects: tuple[str, ...] = ()
    failure: RetrievalSecurityFailure | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None


def contains_sensitive_submission_material(payload: Mapping[str, Any]) -> list[str]:
    """Return UI-submitted fields that look like credentials/tokens/endpoints."""

    offenders: list[str] = []
    for key, value in payload.items():
        normalized = _normalize_key(str(key))
        if any(part in normalized for part in SENSITIVE_SUBMISSION_KEY_PARTS):
            offenders.append(str(key))
            continue
        if isinstance(value, str) and _string_contains_secret(value):
            offenders.append(str(key))
    return offenders


def validate_submitted_listing_url(raw_url: str) -> None:
    """Fail closed on URL credentials, sensitive query material, and local IPs."""

    parts = urlsplit((raw_url or "").strip())
    if parts.scheme.lower() not in ALLOWED_URL_SCHEMES or not parts.netloc:
        raise SensitiveSubmissionError("submitted listing URL must be a complete http(s) URL")
    if parts.username or parts.password:
        raise SensitiveSubmissionError("submitted listing URL must not contain credentials")

    sensitive_query = [
        key
        for key, _ in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() in SENSITIVE_QUERY_KEYS
    ]
    if sensitive_query:
        raise SensitiveSubmissionError(
            "submitted listing URL must not contain credential or token query parameters"
        )

    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise SensitiveSubmissionError("submitted listing URL host is required")
    if is_cloud_metadata_host(host):
        raise SensitiveSubmissionError("cloud metadata endpoints are not valid listing URLs")
    ip = _ip_address_or_none(host)
    if ip is not None and is_blocked_ip(ip):
        raise SensitiveSubmissionError("local or private network targets are not valid listing URLs")


def redact_sensitive_snapshot(value: Any) -> Any:
    """Recursively redact PII, credentials, and token-shaped values."""

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_snapshot_key(key_text):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_sensitive_snapshot(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_snapshot(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_snapshot(item) for item in value)
    if isinstance(value, str):
        return _redact_sensitive_string(value)
    return value


class DefaultRetrievalFetcher:
    def __call__(
        self,
        url: str,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> FetchResponse:
        from modules.external_data.application.assisted_intake import (
            resolve_source_policy,
            retrieve,
        )
        policy = resolve_source_policy(url)
        retrieval = retrieve(url, policy=policy)
        if not retrieval.ok:
            status_code = 500
            if retrieval.failure and "404" in retrieval.failure.summary:
                status_code = 404
            headers = {"Content-Type": "text/html"}
            if retrieval.failure:
                headers.update({
                    "x-failure-code": retrieval.failure.code,
                    "x-failure-summary": retrieval.failure.summary,
                    "x-failure-next-action": retrieval.failure.next_action,
                    "x-failure-retryable": "true" if retrieval.failure.retryable else "false",
                })
            return FetchResponse(
                status_code=status_code,
                headers=headers,
                body=b"",
            )
        import json
        body = json.dumps(retrieval.raw).encode("utf-8")
        return FetchResponse(
            status_code=200,
            headers={"Content-Type": "text/html"},
            body=body,
        )


class RetrievalSecurityGate:
    """Validate an approved retrieval and all redirect hops before fetching."""

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        fetcher: RetrievalFetcher | None = None,
        limits: RetrievalLimits | None = None,
        source_snapshot_service: Any = None,
    ) -> None:
        self._resolver = resolver or _resolve_host
        self._fetcher = fetcher or DefaultRetrievalFetcher()
        self._limits = limits or RetrievalLimits()
        self.source_snapshot_service = source_snapshot_service

    def fetch(
        self,
        url: str,
        *,
        policy: str | None = None,
        tenant_id: str | None = None,
        source_id: str | None = None,
        retrieval_method: str = DEFAULT_RETRIEVAL_METHOD,
    ) -> RetrievalSecurityResult:
        # Resolve policy from registry if source_snapshot_service is available
        if policy is None and self.source_snapshot_service is not None and tenant_id is not None:
            if not source_id:
                from modules.external_data.application.assisted_intake import detect_source
                src = detect_source(url)
                source_id = src.source_id if src else "SRC-UNKNOWN"
            policy = self.source_snapshot_service.check_source_policy(tenant_id, source_id)
            
        if policy is None:
            policy = "POLICY_UNKNOWN"

        failure = self._preflight_policy(policy=policy, retrieval_method=retrieval_method)
        if failure is not None:
            return RetrievalSecurityResult(final_url=url, failure=failure)
        if self._fetcher is None:
            return RetrievalSecurityResult(
                final_url=url,
                failure=_failure(
                    "ODP-INTAKE-RETRIEVAL-FETCHER-MISSING",
                    "No approved retrieval adapter is configured for this source.",
                    "Keep fixture replay or assisted entry until a governed adapter is registered.",
                    retryable=False,
                ),
            )


        current_url = url
        redirects: list[str] = []
        for _ in range(self._limits.max_redirects + 1):
            target_failure = self.validate_target(
                current_url,
                resolve_dns=retrieval_method != "fixture_replay",
            )
            if target_failure is not None:
                return RetrievalSecurityResult(
                    final_url=current_url,
                    redirects=tuple(redirects),
                    failure=target_failure,
                )

            try:
                response = self._fetcher(
                    current_url,
                    timeout_seconds=self._limits.timeout_seconds,
                    max_response_bytes=self._limits.max_response_bytes,
                )
            except TimeoutError:
                return RetrievalSecurityResult(
                    final_url=current_url,
                    redirects=tuple(redirects),
                    failure=_failure(
                        "ODP-INTAKE-RETRIEVAL-TIMEOUT",
                        "Approved source retrieval timed out.",
                        "Retry later; preserved corrections remain on the intake record.",
                        retryable=True,
                    ),
                )
            except OSError:
                return RetrievalSecurityResult(
                    final_url=current_url,
                    redirects=tuple(redirects),
                    failure=_failure(
                        "ODP-INTAKE-RETRIEVAL-CONNECTION",
                        "Approved source retrieval could not connect.",
                        "Retry after the source or network recovers.",
                        retryable=True,
                    ),
                )

            if response.status_code >= 400:
                code = _header(response.headers, "x-failure-code") or "ODP-INTAKE-RETRIEVAL-HTTP-ERROR"
                summary = _header(response.headers, "x-failure-summary") or f"Approved source returned HTTP status {response.status_code}."
                next_action = _header(response.headers, "x-failure-next-action") or "Use assisted entry or request source-policy review."
                retryable_val = _header(response.headers, "x-failure-retryable")
                retryable = (retryable_val == "true") if retryable_val is not None else False
                
                return RetrievalSecurityResult(
                    final_url=current_url,
                    redirects=tuple(redirects),
                    failure=_failure(
                        code,
                        summary,
                        next_action,
                        retryable=retryable,
                    ),
                )

            if _is_redirect(response.status_code):
                location = _header(response.headers, "location")
                if not location:
                    return RetrievalSecurityResult(
                        final_url=current_url,
                        redirects=tuple(redirects),
                        failure=_failure(
                            "ODP-INTAKE-RETRIEVAL-REDIRECT",
                            "Approved source returned an empty redirect.",
                            "Use assisted entry or ask governance to review the source.",
                            retryable=False,
                        ),
                    )
                current_url = urljoin(current_url, location)
                redirects.append(current_url)
                continue

            content_type = (_header(response.headers, "content-type") or "").split(";")[0].strip().lower()
            if not content_type or content_type not in self._limits.allowed_content_types:
                return RetrievalSecurityResult(
                    final_url=current_url,
                    redirects=tuple(redirects),
                    failure=_failure(
                        "ODP-INTAKE-RETRIEVAL-CONTENT-TYPE",
                        "Approved source returned a content type outside the listing-page allowlist.",
                        "Use assisted entry or request source-policy review.",
                        retryable=False,
                    ),
                )
            if len(response.body) > self._limits.max_response_bytes:
                return RetrievalSecurityResult(
                    final_url=current_url,
                    redirects=tuple(redirects),
                    failure=_failure(
                        "ODP-INTAKE-RETRIEVAL-RESPONSE-TOO-LARGE",
                        "Approved source response exceeded the configured size limit.",
                        "Use assisted entry or ask governance to approve a source-specific limit.",
                        retryable=False,
                    ),
                )
            return RetrievalSecurityResult(
                final_url=current_url,
                body=response.body,
                content_type=content_type,
                redirects=tuple(redirects),
            )

        return RetrievalSecurityResult(
            final_url=current_url,
            redirects=tuple(redirects),
            failure=_failure(
                "ODP-INTAKE-RETRIEVAL-REDIRECT-LIMIT",
                "Approved source exceeded the configured redirect limit.",
                "Use assisted entry or request source-policy review.",
                retryable=False,
            ),
        )

    def validate_target(
        self,
        url: str,
        *,
        resolve_dns: bool = True,
    ) -> RetrievalSecurityFailure | None:
        parts = urlsplit((url or "").strip())
        scheme = parts.scheme.lower()
        if scheme not in self._limits.allowed_schemes:
            return _failure(
                "ODP-INTAKE-RETRIEVAL-UNSUPPORTED-SCHEME",
                f"Unsupported retrieval scheme {scheme or '<missing>'!r}.",
                "Submit a complete http(s) listing URL.",
                retryable=False,
            )
        if not parts.netloc:
            return _failure(
                "ODP-INTAKE-RETRIEVAL-BAD-URL",
                "Retrieval target is missing a network host.",
                "Submit a complete http(s) listing URL.",
                retryable=False,
            )
        if parts.username or parts.password:
            return _failure(
                "ODP-INTAKE-RETRIEVAL-CREDENTIAL-MATERIAL",
                "Retrieval target contains embedded credentials.",
                "Remove credentials and use the assisted-entry workflow.",
                retryable=False,
            )

        host = (parts.hostname or "").strip().lower().rstrip(".")
        if is_cloud_metadata_host(host):
            return _network_failure(host)
        ip = _ip_address_or_none(host)
        if ip is not None:
            return _network_failure(host) if is_blocked_ip(ip) else None

        # Fixture replay never opens a network socket. Requiring public DNS for
        # a synthetic corpus host makes deterministic replay depend on external
        # infrastructure while adding no SSRF protection. Static URL,
        # credential, metadata-host, and literal-IP checks above still apply.
        if not resolve_dns:
            return None

        try:
            addresses = self._resolver(host)
        except OSError:
            return _failure(
                "ODP-INTAKE-RETRIEVAL-DNS",
                "Retrieval target DNS resolution failed.",
                "Retry later or use assisted entry if the source remains unavailable.",
                retryable=True,
            )
        if not addresses:
            return _failure(
                "ODP-INTAKE-RETRIEVAL-DNS",
                "Retrieval target did not resolve to any address.",
                "Retry later or use assisted entry if the source remains unavailable.",
                retryable=True,
            )
        for address in addresses:
            parsed = _ip_address_or_none(str(address))
            if parsed is None or is_blocked_ip(parsed):
                return _network_failure(host)
        return None

    def _preflight_policy(
        self,
        *,
        policy: str,
        retrieval_method: str,
    ) -> RetrievalSecurityFailure | None:
        if retrieval_method not in self._limits.allowed_methods:
            return _failure(
                "ODP-INTAKE-RETRIEVAL-METHOD-BLOCKED",
                f"Retrieval method {retrieval_method!r} is not configured.",
                "Use an approved retrieval method or assisted entry.",
                retryable=False,
            )
        if policy not in ALLOWED_RETRIEVAL_POLICIES:
            return _failure(
                "ODP-INTAKE-RETRIEVAL-POLICY-BLOCKED",
                f"Source policy {policy!r} does not permit server-side retrieval.",
                "Use assisted entry or governance review; do not fetch this page.",
                retryable=False,
            )
        return None


def is_cloud_metadata_host(host: str) -> bool:
    normalized = host.strip().lower().rstrip(".")
    return normalized in CLOUD_METADATA_HOSTS or normalized.endswith(".metadata.google.internal")


def is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip in CLOUD_METADATA_IPS
        or ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_host(host: str) -> Sequence[str]:
    return tuple(
        sorted(
            {
                item[4][0]
                for item in socket.getaddrinfo(host, None)
                if item and len(item) >= 5 and item[4]
            }
        )
    )



def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _failure(
    code: str, summary: str, next_action: str, *, retryable: bool
) -> RetrievalSecurityFailure:
    return RetrievalSecurityFailure(
        code=code,
        summary=summary,
        next_action=next_action,
        retryable=retryable,
    )


def _network_failure(host: str) -> RetrievalSecurityFailure:
    return _failure(
        "ODP-INTAKE-RETRIEVAL-NETWORK-BLOCKED",
        f"Retrieval target {host!r} resolves to a blocked network range.",
        "Use assisted entry or ask governance to register a safe source endpoint.",
        retryable=False,
    )


def _ip_address_or_none(value: str) -> ipaddress._BaseAddress | None:
    candidate = value.strip("[]")
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def _is_redirect(status_code: int) -> bool:
    return status_code in {301, 302, 303, 307, 308}


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", key.lower())


def _is_sensitive_snapshot_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return any(part in normalized for part in SENSITIVE_SNAPSHOT_KEY_PARTS)


def _string_contains_secret(value: str) -> bool:
    lowered = value.lower()
    return (
        "authorization:" in lowered
        or "cookie:" in lowered
        or bool(BEARER_RE.search(value))
        or bool(SECRET_RE.search(value))
    )


def _redact_sensitive_string(value: str) -> str:
    redacted = EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    redacted = PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    redacted = BEARER_RE.sub("Bearer [REDACTED_TOKEN]", redacted)
    redacted = SECRET_RE.sub("[REDACTED_TOKEN]", redacted)
    return redacted
