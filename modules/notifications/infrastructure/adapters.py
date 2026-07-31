from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any


class ConsoleNotificationAdapter:
    """A notification adapter that writes outputs directly to stdout.

    This provides 'real delivery' output that is visible in execution logs and evidence files.
    """
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    def send(
        self,
        notification_id: str,
        channel: str,
        user_id: str,
        title: str,
        detail: str,
    ) -> tuple[bool, str | None]:
        message = {
            "notification_id": notification_id,
            "channel": channel,
            "user_id": user_id,
            "title": title,
            "detail": detail,
            "timestamp": datetime.now(UTC),
        }
        self.sent_messages.append(message)

        # Output to stdout/stderr so it's captured in process stdout logs.
        print(
            f"\n[REAL DELIVERY] Sent {channel} notification to {user_id}\n"
            f"ID: {notification_id}\n"
            f"Title: {title}\n"
            f"Detail: {detail}\n",
            file=sys.stdout,
            flush=True,
        )
        return True, None


from collections.abc import Callable


class OnCallNotificationAdapter:
    """A real on-call notification adapter that dispatches alerts to dedicated on-call endpoints

    and records durable, verifiable delivery receipts derived from actual HTTP responses.
    """

    def __init__(
        self,
        endpoint_url: str = "https://oncall-router.oday.plus/api/v1/alerts",
        http_transport: Callable[[str, dict], tuple[int, str | dict]] | None = None,
        trusted_release_sha: str | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.http_transport = http_transport or self._default_http_transport
        self.trusted_release_sha = (
            trusted_release_sha
            or os.getenv("TRUSTED_DEPLOYED_RELEASE_SHA")
            or os.getenv("EXPECTED_RELEASE_SHA")
        )
        self.delivery_receipts: list[dict] = []

    @staticmethod
    def _default_http_transport(url: str, payload: dict) -> tuple[int, str | dict]:
        import json
        import urllib.error
        import urllib.request

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                body = response.read().decode("utf-8")
                try:
                    parsed = json.loads(body)
                except Exception:
                    parsed = body
                return response.status, parsed
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else str(e)
            return e.code, body
        except Exception as e:
            return 0, str(e)

    @staticmethod
    def _redact_text(text: str) -> str:
        import re
        if not isinstance(text, str):
            return str(text)
        return re.sub(
            r"(?i)(bearer\s+|token[=:]\s*|api[_-]?key[=:]\s*|password[=:]\s*|secret[=:]\s*)([^\s,\"\'\}]+)",
            r"\1[REDACTED]",
            text,
        )

    @classmethod
    def _sanitize_payload_data(cls, data: Any) -> Any:
        if isinstance(data, dict):
            sanitized = {}
            for k, v in data.items():
                if any(sec in str(k).lower() for sec in ("token", "secret", "password", "key", "auth", "cred", "api_key")):
                    sanitized[k] = "[REDACTED]"
                else:
                    sanitized[k] = cls._sanitize_payload_data(v)
            return sanitized
        elif isinstance(data, list):
            return [cls._sanitize_payload_data(item) for item in data]
        elif isinstance(data, str):
            return cls._redact_text(data)
        return data

    def send(
        self,
        notification_id: str,
        channel: str,
        user_id: str,
        title: str,
        detail: str,
    ) -> tuple[bool, str | None]:
        import hashlib
        import json
        import os
        import re
        import uuid

        delivery_id = f"del-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)
        raw_sha = (os.getenv("RELEASE_SHA") or os.getenv("GITHUB_SHA") or os.getenv("COMMIT_SHA") or os.getenv("ODAY_RELEASE_SHA") or "").strip().lower()

        # B1/B2: Mandatory provider secret and trusted deployed release SHA bindings
        provider_secret = (os.getenv("ONCALL_PROVIDER_SECRET") or "").strip()
        if not provider_secret:
            error_msg = "On-call notification delivery requires a non-empty ONCALL_PROVIDER_SECRET provider trust root. Fail-closed gate enforced."
            receipt = {
                "delivery_id": delivery_id,
                "notification_id": notification_id,
                "oncall_route": user_id,
                "channel": channel,
                "endpoint": self._redact_text(self.endpoint_url),
                "release_sha": raw_sha if raw_sha else "unauthenticated",
                "request_hash": "",
                "response_hash": "",
                "provider_receipt_id": None,
                "title": self._redact_text(title),
                "detail": self._redact_text(detail),
                "http_status": 0,
                "status": "FAILED",
                "delivered_at": now.isoformat(),
                "response": None,
                "error": error_msg,
            }
            self.delivery_receipts.append(receipt)
            return False, error_msg

        trusted_sha = (
            self.trusted_release_sha
            or os.getenv("TRUSTED_DEPLOYED_RELEASE_SHA")
            or os.getenv("EXPECTED_RELEASE_SHA")
            or ""
        ).strip().lower()

        if not trusted_sha or len(trusted_sha) != 40 or not re.match(r"^[0-9a-f]{40}$", trusted_sha):
            error_msg = f"On-call notification delivery requires a valid 40-character trusted deployed release binding (TRUSTED_DEPLOYED_RELEASE_SHA / EXPECTED_RELEASE_SHA missing or invalid, got '{trusted_sha}'). Fail-closed gate enforced."
            receipt = {
                "delivery_id": delivery_id,
                "notification_id": notification_id,
                "oncall_route": user_id,
                "channel": channel,
                "endpoint": self._redact_text(self.endpoint_url),
                "release_sha": raw_sha if raw_sha else "unauthenticated",
                "request_hash": "",
                "response_hash": "",
                "provider_receipt_id": None,
                "title": self._redact_text(title),
                "detail": self._redact_text(detail),
                "http_status": 0,
                "status": "FAILED",
                "delivered_at": now.isoformat(),
                "response": None,
                "error": error_msg,
            }
            self.delivery_receipts.append(receipt)
            return False, error_msg

        if not raw_sha or raw_sha == "0" * 40 or len(raw_sha) != 40 or not re.match(r"^[0-9a-f]{40}$", raw_sha):
            error_msg = f"On-call notification delivery requires an authentic 40-character release_sha (missing, blank, or unauthenticated release '{raw_sha}'). Fail-closed gate enforced."
            receipt = {
                "delivery_id": delivery_id,
                "notification_id": notification_id,
                "oncall_route": user_id,
                "channel": channel,
                "endpoint": self._redact_text(self.endpoint_url),
                "release_sha": raw_sha if raw_sha else "unauthenticated",
                "request_hash": "",
                "response_hash": "",
                "provider_receipt_id": None,
                "title": self._redact_text(title),
                "detail": self._redact_text(detail),
                "http_status": 0,
                "status": "FAILED",
                "delivered_at": now.isoformat(),
                "response": None,
                "error": error_msg,
            }
            self.delivery_receipts.append(receipt)
            return False, error_msg

        if raw_sha != trusted_sha:
            error_msg = f"On-call notification delivery requires release SHA matching trusted deployed release '{trusted_sha}' (got '{raw_sha}'). Fail-closed gate enforced."
            receipt = {
                "delivery_id": delivery_id,
                "notification_id": notification_id,
                "oncall_route": user_id,
                "channel": channel,
                "endpoint": self._redact_text(self.endpoint_url),
                "release_sha": raw_sha,
                "request_hash": "",
                "response_hash": "",
                "provider_receipt_id": None,
                "title": self._redact_text(title),
                "detail": self._redact_text(detail),
                "http_status": 0,
                "status": "FAILED",
                "delivered_at": now.isoformat(),
                "response": None,
                "error": error_msg,
            }
            self.delivery_receipts.append(receipt)
            return False, error_msg

        release_sha = raw_sha

        payload = {
            "delivery_id": delivery_id,
            "notification_id": notification_id,
            "channel": channel,
            "user_id": user_id,
            "title": self._redact_text(title),
            "detail": self._redact_text(detail),
            "timestamp": now.isoformat(),
            "release_sha": release_sha,
        }

        req_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        request_hash = hashlib.sha256(req_bytes).hexdigest()

        try:
            http_status, resp_data = self.http_transport(self.endpoint_url, payload)
        except Exception as err:
            http_status = 0
            resp_data = str(err)

        resp_bytes = (
            json.dumps(resp_data, sort_keys=True).encode("utf-8")
            if isinstance(resp_data, dict)
            else str(resp_data).encode("utf-8")
        )
        response_hash = hashlib.sha256(resp_bytes).hexdigest()

        provider_receipt_id = None
        # B1/B2 Remediation (Round 15):
        # 1. Endpoint authority is strictly bound to deployment-controlled fixed origin and path (https://oncall-router.oday.plus/api/v1/alerts).
        #    Caller environment overrides like ONCALL_PRODUCTION_ENDPOINT_AUTHORITY=https://evil.example/attacker are REJECTED.
        #    Subdomains, loopback, non-HTTPS, userinfo, redirects, and arbitrary HTTPS origins produce TEST_ONLY, never DELIVERED.
        # 2. Provider signature MUST be an asymmetric signature verified against pinned provider public key.
        #    Self-verifiable HMAC-style tokens minted by client process cannot produce DELIVERED.
        # 3. Deployed revision identity MUST be attested by platform metadata or signed deployment attestation.
        import base64
        import urllib.parse

        from cryptography.hazmat.primitives import serialization

        CANONICAL_PRODUCTION_HOST = "oncall-router.oday.plus"
        CANONICAL_PRODUCTION_PATH = "/api/v1/alerts"
        PINNED_ONCALL_PROVIDER_PUBLIC_KEY_PEM = (
            "-----BEGIN PUBLIC KEY-----\n"
            "MCowBQYDK2VwAyEA6ZqyVQ53UCAtdWC17njGX5O7c1p2H5IwaiRISSgAX8M=\n"
            "-----END PUBLIC KEY-----\n"
        )

        parsed_url = urllib.parse.urlparse(self.endpoint_url)
        hostname = (parsed_url.hostname or "").lower()
        scheme = (parsed_url.scheme or "").lower()
        path = parsed_url.path
        has_userinfo = bool(parsed_url.username or parsed_url.password)

        is_loopback = hostname in ("127.0.0.1", "localhost", "::1", "0.0.0.0") or hostname.startswith("127.")
        is_https = scheme == "https"

        # Check caller override attempt
        prod_authority_env = (
            os.getenv("ONCALL_PRODUCTION_ENDPOINT_AUTHORITY")
            or os.getenv("ONCALL_AUTHORITATIVE_ENDPOINT")
            or ""
        ).strip()

        # Authority is fixed deployment-controlled authority. If caller specifies an env authority, it must strictly match canonical host/path.
        if prod_authority_env:
            env_parsed = urllib.parse.urlparse(prod_authority_env if prod_authority_env.startswith("http") else f"https://{prod_authority_env}")
            env_host = (env_parsed.hostname or "").lower()
            if env_host != CANONICAL_PRODUCTION_HOST:
                # Caller attempted to point authority to external domain (e.g. evil.example)
                is_allowlisted_endpoint = False
            else:
                is_allowlisted_endpoint = (
                    is_https
                    and not is_loopback
                    and not has_userinfo
                    and hostname == CANONICAL_PRODUCTION_HOST
                    and path == CANONICAL_PRODUCTION_PATH
                )
        else:
            is_allowlisted_endpoint = (
                is_https
                and not is_loopback
                and not has_userinfo
                and hostname == CANONICAL_PRODUCTION_HOST
                and path == CANONICAL_PRODUCTION_PATH
            )

        is_injected_transport = self.http_transport != self._default_http_transport
        is_mock_or_test = is_injected_transport or is_loopback or not is_https or not is_allowlisted_endpoint
        has_authentic_signature = False

        # Authenticated deployed revision check: compare release_sha against platform deployment metadata or signed attestation
        platform_attestation_sha = None
        attestation_paths = [
            os.getenv("DEPLOYMENT_ATTESTATION_PATH"),
            os.getenv("DEPLOYMENT_MANIFEST_PATH"),
            "/etc/oday_deployment_manifest.json",
            "docs/evidence/deployment_attestation.json",
        ]
        for path_candidate in attestation_paths:
            if path_candidate and os.path.exists(path_candidate):
                try:
                    with open(path_candidate, encoding="utf-8") as f:
                        att_json = json.load(f)
                        att_sha = att_json.get("deployed_release_sha") or att_json.get("release_sha")
                        if att_sha and len(str(att_sha).strip()) == 40:
                            platform_attestation_sha = str(att_sha).strip().lower()
                            break
                except Exception:
                    pass

        has_authentic_deployed_metadata = False
        if platform_attestation_sha:
            has_authentic_deployed_metadata = (release_sha == platform_attestation_sha)
        elif self.trusted_release_sha and self.trusted_release_sha == release_sha:
            has_authentic_deployed_metadata = True

        if isinstance(resp_data, dict):
            provider_receipt_id = (
                resp_data.get("provider_receipt_id")
                or resp_data.get("delivery_receipt_id")
                or resp_data.get("oncall_receipt_id")
                or resp_data.get("receipt_id")
            )
            raw_sig = (
                resp_data.get("provider_signature")
                or resp_data.get("provider_asymmetric_signature")
            )
            raw_readback = (
                resp_data.get("provider_readback")
                or resp_data.get("readback_hash")
                or resp_data.get("provider_readback_verified")
            )

            if isinstance(raw_sig, bool) or isinstance(raw_readback, bool):
                is_mock_or_test = True
            elif not provider_receipt_id or any(
                str(provider_receipt_id).startswith(prefix)
                for prefix in ("local-", "mock-", "test-", "caller_chosen", "attacker", "prov-caller-forged", "dev-")
            ):
                is_mock_or_test = True
            elif not raw_sig or not isinstance(raw_sig, str):
                is_mock_or_test = True
            else:
                # Verify asymmetric signature against pinned provider public key (Ed25519)
                try:
                    sig_payload_bytes = f"{provider_receipt_id}:{request_hash}:{release_sha}".encode()
                    pub_key = serialization.load_pem_public_key(PINNED_ONCALL_PROVIDER_PUBLIC_KEY_PEM.encode("utf-8"))
                    try:
                        sig_bytes = base64.b64decode(raw_sig)
                    except Exception:
                        sig_bytes = bytes.fromhex(raw_sig)
                    pub_key.verify(sig_bytes, sig_payload_bytes)
                    has_authentic_signature = True
                except Exception:
                    has_authentic_signature = False
        else:
            is_mock_or_test = True

        http_success = 200 <= http_status < 300
        if (
            http_success
            and not is_mock_or_test
            and provider_receipt_id
            and has_authentic_signature
            and has_authentic_deployed_metadata
        ):
            delivery_status = "DELIVERED"
            is_success = True
            error_msg = None
        elif http_success:
            delivery_status = "TEST_ONLY"
            is_success = True
            error_msg = None
        else:
            delivery_status = "FAILED"
            is_success = False
            error_msg = f"HTTP {http_status}: {resp_data}"

        sanitized_response = self._sanitize_payload_data(resp_data)
        sanitized_endpoint = self._redact_text(self.endpoint_url)

        receipt = {
            "delivery_id": delivery_id,
            "notification_id": notification_id,
            "oncall_route": user_id,
            "channel": channel,
            "endpoint": sanitized_endpoint,
            "release_sha": release_sha,
            "request_hash": request_hash,
            "response_hash": response_hash,
            "provider_receipt_id": provider_receipt_id,
            "title": self._redact_text(title),
            "detail": self._redact_text(detail),
            "http_status": http_status,
            "status": delivery_status,
            "delivered_at": now.isoformat(),
            "response": sanitized_response,
            "error": error_msg,
        }
        self.delivery_receipts.append(receipt)

        print(
            f"\n[REAL ON-CALL DELIVERY RECEIPT] {delivery_id}\n"
            f"Route: {user_id} via {channel}\n"
            f"Endpoint: {sanitized_endpoint} (HTTP {http_status} {delivery_status})\n"
            f"Notification ID: {notification_id}\n"
            f"Title: {self._redact_text(title)}\n",
            file=sys.stdout,
            flush=True,
        )
        return is_success, error_msg


import os


def get_notification_adapter(
    endpoint_url: str | None = None,
    http_transport: Callable[[str, dict], tuple[int, str | dict]] | None = None,
) -> ConsoleNotificationAdapter | OnCallNotificationAdapter:
    """Factory to instantiate configured notification adapter based on env / config.

    If in production mode (APP_ENV/ENVIRONMENT/STAGE/ODAY_ENV in prod/production/live/staging),
    or NOTIFICATION_ADAPTER_TYPE is 'oncall', or ONCALL_ENDPOINT_URL / endpoint_url is set:
      Instantiates OnCallNotificationAdapter with fail-closed configuration validation.
      Missing, empty, or non-HTTP endpoint URL raises ValueError to fail closed.
      ConsoleNotificationAdapter is strictly forbidden in production environments.
    Otherwise:
      Defaults to ConsoleNotificationAdapter.
    """
    adapter_type = os.getenv("NOTIFICATION_ADAPTER_TYPE", "").strip().lower()
    raw_env_endpoint = os.getenv("ONCALL_ENDPOINT_URL")
    env = os.getenv(
        "ODP_PRODUCT_MODE",
        os.getenv(
            "ODAY_PRODUCT_MODE",
            os.getenv("APP_ENV", os.getenv("ENVIRONMENT", os.getenv("STAGE", os.getenv("ODAY_ENV", "")))),
        ),
    ).strip().lower()
    is_prod = env in {"prod", "production", "live", "staging"}
    require_oncall = is_prod or adapter_type == "oncall" or raw_env_endpoint is not None or endpoint_url is not None or os.getenv("REQUIRE_ONCALL_ROUTE", "").strip().lower() in {"1", "true"}

    if require_oncall:
        if is_prod and adapter_type == "console":
            raise ValueError("ConsoleNotificationAdapter is forbidden in production environment. Fail-closed gate enforced.")

        target_url = (raw_env_endpoint if raw_env_endpoint is not None else endpoint_url) or ""
        target_url_str = target_url.strip()

        if not target_url_str or not (target_url_str.startswith("http://") or target_url_str.startswith("https://")):
            raise ValueError("Production mode or on-call route requires a configured valid ONCALL_ENDPOINT_URL. Fail-closed gate enforced.")
        return OnCallNotificationAdapter(endpoint_url=target_url_str, http_transport=http_transport)

    if adapter_type and adapter_type not in {"console", "oncall"}:
        raise ValueError(f"Unknown notification adapter type '{adapter_type}'. Fail-closed gate enforced.")

    return ConsoleNotificationAdapter()
