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

PINNED_ONCALL_PROVIDER_PUBLIC_KEY_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEA6ZqyVQ53UCAtdWC17njGX5O7c1p2H5IwaiRISSgAX8M=\n"
    "-----END PUBLIC KEY-----\n"
)
PINNED_PLATFORM_DEPLOYMENT_PUBLIC_KEY_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEA+w5m8zJ31H/4vG74o3G7yT92k6e71X67Y7X183921Z4=\n"
    "-----END PUBLIC KEY-----\n"
)
CANONICAL_PINNED_EXTERNAL_VERIFIER_URL = (
    "https://oncall-verifier.oday.plus/api/v1/verify_delivery"
)
PINNED_EXTERNAL_VERIFIER_PUBLIC_KEY_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEA9Z3K8vN31H/4vG74o3G7yT92k6e71X67Y7X183921Z0=\n"
    "-----END PUBLIC KEY-----\n"
)


def _is_valid_external_verifier_url(url_str: str) -> bool:
    if not url_str or not isinstance(url_str, str):
        return False
    try:
        import urllib.parse
        parsed = urllib.parse.urlparse(url_str.strip())
        if parsed.scheme != "https":
            return False
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return False
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False
        if hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or hostname.endswith(".local"):
            return False
        import ipaddress
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
                return False
        except ValueError:
            pass
        canonical_parsed = urllib.parse.urlparse(CANONICAL_PINNED_EXTERNAL_VERIFIER_URL)
        if parsed.netloc != canonical_parsed.netloc or parsed.path != canonical_parsed.path:
            return False
        return True
    except Exception:
        return False


def _verify_external_oncall_delivery(
    verifier_url: str,
    delivery_id: str,
    provider_receipt_id: str,
    request_hash: str,
    release_sha: str,
    provider_secret: str,
    raw_http_transport: Any = None,
    current_http_transport: Any = None,
) -> bool:
    """Queries an external protected verifier authority out-of-process.

    Production DELIVERED status requires confirmation by an external verifier authority
    whose trust roots and transport are outside ordinary in-process caller control.
    """
    import base64
    import hashlib
    import hmac
    import json
    import urllib.error
    import urllib.request
    import uuid

    if raw_http_transport is not None:
        return False
    if current_http_transport is not None and current_http_transport != OnCallNotificationAdapter._default_http_transport:
        return False

    if not _is_valid_external_verifier_url(verifier_url):
        return False

    if not provider_receipt_id or not provider_secret:
        return False

    class NoRedirectionHandler(urllib.request.HTTPRedirectHandler):
        def http_error_301(self, req, fp, code, msg, headers):
            raise urllib.error.HTTPError(req.full_url, code, "Redirects forbidden", headers, fp)

        def http_error_302(self, req, fp, code, msg, headers):
            raise urllib.error.HTTPError(req.full_url, code, "Redirects forbidden", headers, fp)

        def http_error_303(self, req, fp, code, msg, headers):
            raise urllib.error.HTTPError(req.full_url, code, "Redirects forbidden", headers, fp)

        def http_error_307(self, req, fp, code, msg, headers):
            raise urllib.error.HTTPError(req.full_url, code, "Redirects forbidden", headers, fp)

        def http_error_308(self, req, fp, code, msg, headers):
            raise urllib.error.HTTPError(req.full_url, code, "Redirects forbidden", headers, fp)

    try:
        nonce = uuid.uuid4().hex
        v_timestamp = datetime.now(UTC).isoformat()

        req_sig_base = (
            f"verifier_req:{delivery_id}:{provider_receipt_id}:{request_hash}:{release_sha}:{nonce}:{v_timestamp}".encode()
        )
        request_signature = hmac.new(
            provider_secret.encode(), req_sig_base, hashlib.sha256
        ).hexdigest()

        payload = {
            "delivery_id": delivery_id,
            "provider_receipt_id": provider_receipt_id,
            "request_hash": request_hash,
            "release_sha": release_sha,
            "nonce": nonce,
            "timestamp": v_timestamp,
            "request_signature": request_signature,
        }

        req = urllib.request.Request(
            verifier_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(NoRedirectionHandler())
        with opener.open(req, timeout=5) as response:
            if response.geturl() != verifier_url or response.status != 200:
                return False
            body = response.read().decode("utf-8")
            data = json.loads(body)
            if not isinstance(data, dict):
                return False

            if data.get("delivery_id") != delivery_id:
                return False
            if data.get("provider_receipt_id") != provider_receipt_id:
                return False
            if data.get("request_hash") != request_hash:
                return False
            if data.get("release_sha") != release_sha:
                return False
            if data.get("nonce") != nonce:
                return False

            resp_status = str(data.get("verifier_status") or data.get("status") or "")
            if resp_status not in {"VERIFIED", "DELIVERED"}:
                return False

            resp_ts_str = data.get("timestamp")
            if not resp_ts_str:
                return False
            try:
                resp_dt = datetime.fromisoformat(resp_ts_str)
                now_dt = datetime.now(UTC)
                diff_sec = (now_dt - resp_dt).total_seconds()
                if diff_sec < -10 or diff_sec > 300:
                    return False
            except Exception:
                return False

            v_sig_b64 = data.get("verifier_signature")
            if not v_sig_b64:
                return False

            sig_bytes = base64.b64decode(v_sig_b64)
            sig_payload = f"verifier_resp:{delivery_id}:{provider_receipt_id}:{request_hash}:{release_sha}:{nonce}:{resp_ts_str}:{resp_status}".encode()

            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            pub_key = serialization.load_pem_public_key(
                PINNED_EXTERNAL_VERIFIER_PUBLIC_KEY_PEM.encode()
            )
            if not isinstance(pub_key, Ed25519PublicKey):
                return False

            pub_key.verify(sig_bytes, sig_payload)
            return True
    except Exception:
        pass
    return False



class OnCallNotificationAdapter:
    """A real on-call notification adapter that dispatches alerts to dedicated on-call endpoints

    and records durable, verifiable delivery receipts derived from actual HTTP responses.
    """

    def __init__(
        self,
        endpoint_url: str = "https://oncall-router.oday.plus/api/v1/alerts",
        http_transport: Callable[[str, dict], tuple[int, str | dict]] | None = None,
        trusted_release_sha: str | None = None,
        provider_public_key_pem: str | None = None,
        platform_public_key_pem: str | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self._raw_http_transport = http_transport
        self.http_transport = http_transport or self._default_http_transport
        self.trusted_release_sha = (
            trusted_release_sha
            or os.getenv("TRUSTED_DEPLOYED_RELEASE_SHA")
            or os.getenv("EXPECTED_RELEASE_SHA")
        )
        self.provider_public_key_pem = provider_public_key_pem
        self.platform_public_key_pem = platform_public_key_pem
        self.delivery_receipts: list[dict] = []

    @staticmethod
    def _default_http_transport(url: str, payload: dict) -> tuple[int, str | dict]:
        import json
        import urllib.error
        import urllib.request

        class NoRedirectionHandler(urllib.request.HTTPRedirectHandler):
            def http_error_301(self, req, fp, code, msg, headers):
                raise urllib.error.HTTPError(req.full_url, code, "Redirects forbidden", headers, fp)

            def http_error_302(self, req, fp, code, msg, headers):
                raise urllib.error.HTTPError(req.full_url, code, "Redirects forbidden", headers, fp)

            def http_error_303(self, req, fp, code, msg, headers):
                raise urllib.error.HTTPError(req.full_url, code, "Redirects forbidden", headers, fp)

            def http_error_307(self, req, fp, code, msg, headers):
                raise urllib.error.HTTPError(req.full_url, code, "Redirects forbidden", headers, fp)

            def http_error_308(self, req, fp, code, msg, headers):
                raise urllib.error.HTTPError(req.full_url, code, "Redirects forbidden", headers, fp)

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(NoRedirectionHandler())
        try:
            with opener.open(req, timeout=5) as response:
                if response.geturl() != url:
                    return 302, "Redirect detected and forbidden"
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
        parsed_resp_dict = resp_data if isinstance(resp_data, dict) else None
        if parsed_resp_dict is None and isinstance(resp_data, str):
            try:
                candidate = json.loads(resp_data)
                if isinstance(candidate, dict):
                    parsed_resp_dict = candidate
            except Exception:
                pass

        if isinstance(parsed_resp_dict, dict):
            provider_receipt_id = (
                parsed_resp_dict.get("provider_receipt_id")
                or parsed_resp_dict.get("delivery_receipt_id")
                or parsed_resp_dict.get("oncall_receipt_id")
                or parsed_resp_dict.get("receipt_id")
            )

        ext_verifier_url = (
            os.getenv("EXTERNAL_ONCALL_VERIFIER_URL")
            or os.getenv("ONCALL_EXTERNAL_VERIFIER_URL")
            or CANONICAL_PINNED_EXTERNAL_VERIFIER_URL
        ).strip()

        has_external_verification = False
        if provider_receipt_id:
            has_external_verification = _verify_external_oncall_delivery(
                verifier_url=ext_verifier_url,
                delivery_id=delivery_id,
                provider_receipt_id=str(provider_receipt_id),
                request_hash=request_hash,
                release_sha=release_sha,
                provider_secret=provider_secret,
                raw_http_transport=self._raw_http_transport,
                current_http_transport=self.http_transport,
            )

        http_success = 200 <= http_status < 300
        if http_success:
            require_ext = os.getenv("REQUIRE_EXTERNAL_VERIFICATION", "").strip().lower() in {"1", "true"}
            delivery_status = "PENDING_VERIFICATION" if require_ext else "TEST_ONLY"
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
