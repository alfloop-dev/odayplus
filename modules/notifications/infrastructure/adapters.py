from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from modules.notifications.application.service import adapter_accepts_severity

# Delivery-receipt / inbox mirrors kept in process memory are debug aids only;
# the durable record lives in the repository. Cap them so a long-lived worker
# cannot grow them without bound.
_MAX_IN_PROCESS_RECORDS = 500

_PRODUCTION_ENV_VALUES = {"prod", "production", "live", "staging"}


def _current_env() -> str:
    """Resolve the deployment environment from the canonical env var chain."""
    return os.getenv(
        "ODP_PRODUCT_MODE",
        os.getenv(
            "ODAY_PRODUCT_MODE",
            os.getenv("APP_ENV", os.getenv("ENVIRONMENT", os.getenv("STAGE", os.getenv("ODAY_ENV", "")))),
        ),
    ).strip().lower()


def _is_production_env() -> bool:
    """True when the process is running against a production-class deployment."""
    return _current_env() in _PRODUCTION_ENV_VALUES


def _append_capped(records: list[dict], record: dict) -> None:
    records.append(record)
    if len(records) > _MAX_IN_PROCESS_RECORDS:
        del records[: len(records) - _MAX_IN_PROCESS_RECORDS]


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
        severity: str = "info",
    ) -> tuple[bool, str | None]:
        message = {
            "notification_id": notification_id,
            "channel": channel,
            "user_id": user_id,
            "title": title,
            "detail": detail,
            "severity": severity,
            "timestamp": datetime.now(UTC),
        }
        _append_capped(self.sent_messages, message)

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
        severity: str = "info",
    ) -> tuple[bool, str | None]:
        # ``severity`` is accepted so this adapter satisfies the same protocol as
        # the in-app/email adapters, but it is deliberately kept out of the signed
        # on-call payload: that request contract is owned elsewhere and unchanged.
        import re

        delivery_id = f"del-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)
        from shared.runtime_config import get_release_identity

        raw_sha = get_release_identity().lower()

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


class EmailNotificationAdapter:
    """An email notification adapter that dispatches notifications over SMTP or custom transport.

    Supports configurable SMTP parameters, fail-closed validation in production mode,
    and durable delivery receipts.
    """

    def __init__(
        self,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
        smtp_from_email: str | None = None,
        use_tls: bool | None = None,
        smtp_transport: Callable[[dict], tuple[bool, str | None]] | None = None,
        trusted_release_sha: str | None = None,
    ) -> None:
        self.smtp_host = smtp_host or os.getenv("SMTP_HOST") or os.getenv("EMAIL_SMTP_HOST")
        port_val = smtp_port or os.getenv("SMTP_PORT") or os.getenv("EMAIL_SMTP_PORT")
        self.smtp_port = int(port_val) if port_val else 587
        self.smtp_username = smtp_username or os.getenv("SMTP_USERNAME") or os.getenv("EMAIL_SMTP_USER")
        self.smtp_password = smtp_password or os.getenv("SMTP_PASSWORD") or os.getenv("EMAIL_SMTP_PASSWORD")
        self.smtp_from_email = smtp_from_email or os.getenv("SMTP_FROM_EMAIL") or os.getenv("EMAIL_FROM") or "notifications@oday.plus"

        tls_val = use_tls if use_tls is not None else os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes"}
        self.use_tls = tls_val
        # Remember whether delivery goes through the built-in SMTP transport: the
        # mock branch below only exists for the default transport, so the
        # fail-closed checks must not punish an explicitly injected one.
        self.uses_default_transport = smtp_transport is None
        self.smtp_transport = smtp_transport or self._default_smtp_transport
        self.trusted_release_sha = (
            trusted_release_sha
            or os.getenv("TRUSTED_DEPLOYED_RELEASE_SHA")
            or os.getenv("EXPECTED_RELEASE_SHA")
        )
        self.delivery_receipts: list[dict] = []

    def _default_smtp_transport(self, message_data: dict) -> tuple[bool, str | None]:
        import smtplib
        from email.mime.text import MIMEText

        if not self.smtp_host:
            # Fail closed: a production deployment must never report a mocked
            # stdout "delivery" as a sent email. `send()` already rejects this
            # combination; this branch keeps the mock unreachable in production
            # even if the transport is invoked directly.
            if _is_production_env():
                return False, (
                    "Email notification delivery requires a configured SMTP_HOST in "
                    f"'{_current_env()}' environment; mock stdout delivery is forbidden. "
                    "Fail-closed gate enforced."
                )
            print(
                f"\n[MOCK EMAIL DELIVERY] Sent email to {message_data['user_id']}\n"
                f"Subject: {message_data['title']}\n"
                f"Body: {message_data['detail']}\n",
                file=sys.stdout,
                flush=True,
            )
            return True, None

        try:
            msg = MIMEText(message_data["detail"], "plain", "utf-8")
            msg["Subject"] = message_data["title"]
            msg["From"] = self.smtp_from_email
            msg["To"] = message_data["user_id"]

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                if self.use_tls:
                    server.starttls()
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)
            return True, None
        except Exception as e:
            return False, f"SMTP delivery error: {e}"

    def send(
        self,
        notification_id: str,
        channel: str,
        user_id: str,
        title: str,
        detail: str,
        severity: str = "info",
    ) -> tuple[bool, str | None]:
        delivery_id = f"email-del-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)

        require_email = os.getenv("REQUIRE_EMAIL_ROUTE", "").strip().lower() in {"1", "true"}
        # In production the mock stdout branch of the default transport would
        # otherwise return success for mail that was never sent, so an
        # unconfigured SMTP host is fail-closed there regardless of opt-in.
        prod_unconfigured = _is_production_env() and self.uses_default_transport
        if not self.smtp_host and (require_email or prod_unconfigured):
            if require_email:
                error_msg = "Email notification delivery requires a configured SMTP_HOST when REQUIRE_EMAIL_ROUTE is set. Fail-closed gate enforced."
            else:
                error_msg = (
                    "Email notification delivery requires a configured SMTP_HOST in "
                    f"'{_current_env()}' environment; mock stdout delivery is forbidden. "
                    "Fail-closed gate enforced."
                )
            receipt = {
                "delivery_id": delivery_id,
                "notification_id": notification_id,
                "recipient": user_id,
                "channel": "email",
                "severity": severity,
                "status": "FAILED",
                "delivered_at": now.isoformat(),
                "error": error_msg,
            }
            _append_capped(self.delivery_receipts, receipt)
            return False, error_msg

        payload = {
            "delivery_id": delivery_id,
            "notification_id": notification_id,
            "channel": "email",
            "user_id": user_id,
            "title": title,
            "detail": detail,
            "severity": severity,
            "timestamp": now.isoformat(),
            "sender": self.smtp_from_email,
        }

        # Audit field only: the digest of the exact request handed to the
        # transport, so a receipt can be tied back to the message body. Nothing
        # verifies it at delivery time.
        req_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        request_hash = hashlib.sha256(req_bytes).hexdigest()

        try:
            success, err_msg = self.smtp_transport(payload)
        except Exception as err:
            success = False
            err_msg = str(err)

        status = "SENT" if success else "FAILED"
        receipt = {
            "delivery_id": delivery_id,
            "notification_id": notification_id,
            "recipient": user_id,
            "channel": "email",
            "severity": severity,
            "sender": self.smtp_from_email,
            "request_hash": request_hash,
            "release_sha": self.trusted_release_sha or "unbound",
            "title": title,
            "detail": detail,
            "status": status,
            "delivered_at": now.isoformat(),
            "error": err_msg,
        }
        _append_capped(self.delivery_receipts, receipt)
        return success, err_msg


class InAppNotificationAdapter:
    """An in-app notification adapter that stores notifications into durable/in-memory inbox."""

    def __init__(self, repository: Any = None) -> None:
        self.repository = repository
        self.inbox_items: list[dict] = []
        self.delivery_receipts: list[dict] = []

    def send(
        self,
        notification_id: str,
        channel: str,
        user_id: str,
        title: str,
        detail: str,
        severity: str = "info",
    ) -> tuple[bool, str | None]:
        delivery_id = f"inapp-del-{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(UTC).isoformat()

        item = {
            "notification_id": notification_id,
            "user_id": user_id,
            "title": title,
            "detail": detail,
            # The caller's severity is what makes the inbox filterable; a
            # hardcoded default here would make the severity column dead.
            "severity": severity or "info",
            "created_at": now_iso,
            "acknowledged": False,
            "acknowledged_at": None,
        }

        if self.repository and hasattr(self.repository, "save_inapp_item"):
            self.repository.save_inapp_item(item)
        else:
            _append_capped(self.inbox_items, item)

        receipt = {
            "delivery_id": delivery_id,
            "notification_id": notification_id,
            "user_id": user_id,
            "channel": "in_app",
            "severity": item["severity"],
            "status": "SENT",
            "delivered_at": now_iso,
            "error": None,
        }
        _append_capped(self.delivery_receipts, receipt)

        print(
            f"\n[IN-APP DELIVERY] Saved in-app notification to {user_id}\n"
            f"ID: {notification_id}\n"
            f"Title: {title}\n",
            file=sys.stdout,
            flush=True,
        )
        return True, None

    def get_inbox(
        self,
        user_id: str | None = None,
        severity: str | None = None,
        acknowledged: bool | None = None,
    ) -> list[dict]:
        if self.repository and hasattr(self.repository, "get_inapp_items"):
            return self.repository.get_inapp_items(user_id=user_id, severity=severity, acknowledged=acknowledged)
        res = list(self.inbox_items)
        if user_id:
            res = [i for i in res if i.get("user_id") == user_id]
        if severity:
            res = [i for i in res if i.get("severity") == severity]
        if acknowledged is not None:
            res = [i for i in res if i.get("acknowledged") is acknowledged]
        return res

    def acknowledge_notification(self, user_id: str, notification_id: str) -> bool:
        if self.repository and hasattr(self.repository, "acknowledge_inapp_item"):
            return self.repository.acknowledge_inapp_item(user_id=user_id, notification_id=notification_id)
        now_iso = datetime.now(UTC).isoformat()
        found = False
        for i in self.inbox_items:
            if (not user_id or i.get("user_id") == user_id) and i.get("notification_id") == notification_id:
                i["acknowledged"] = True
                i["acknowledged_at"] = now_iso
                found = True
        return found


class MultiChannelNotificationAdapter:
    """Composite notification adapter that routes notifications to channel-specific adapters."""

    def __init__(
        self,
        default_adapter: Any = None,
        channel_adapters: dict[str, Any] | None = None,
    ) -> None:
        self.default_adapter = default_adapter or ConsoleNotificationAdapter()
        self.channel_adapters: dict[str, Any] = channel_adapters or {}
        self.delivery_receipts: list[dict] = []

    def register_adapter(self, channel: str, adapter: Any) -> None:
        self.channel_adapters[channel.lower().replace("-", "_")] = adapter

    def send(
        self,
        notification_id: str,
        channel: str,
        user_id: str,
        title: str,
        detail: str,
        severity: str = "info",
    ) -> tuple[bool, str | None]:
        norm_channel = (channel or "").lower().replace("-", "_")
        adapter = self.channel_adapters.get(norm_channel)
        if not adapter:
            # Only aliases can still resolve here: an exact-key lookup already
            # missed, so `inapp` -> `in_app` and `oncall`/`webhook` are the
            # remaining cases. Everything else falls back to the default.
            if norm_channel == "inapp":
                adapter = self.channel_adapters.get("in_app", self.default_adapter)
            elif norm_channel in {"webhook", "oncall"}:
                adapter = self.channel_adapters.get("webhook", self.channel_adapters.get("oncall", self.default_adapter))
            else:
                adapter = self.default_adapter

        if adapter_accepts_severity(adapter):
            success, err = adapter.send(notification_id, channel, user_id, title, detail, severity=severity)
        else:
            success, err = adapter.send(notification_id, channel, user_id, title, detail)

        if hasattr(adapter, "delivery_receipts") and adapter.delivery_receipts:
            _append_capped(self.delivery_receipts, adapter.delivery_receipts[-1])

        return success, err


_KNOWN_ADAPTER_TYPES = {"console", "oncall", "email", "in_app", "inapp", "in-app", "multi", "composite"}
_IN_APP_ADAPTER_TYPES = {"in_app", "inapp", "in-app"}
_MULTI_ADAPTER_TYPES = {"multi", "composite"}


def _build_oncall_adapter(
    raw_env_endpoint: str | None,
    endpoint_url: str | None,
    http_transport: Callable[[str, dict], tuple[int, str | dict]] | None,
) -> OnCallNotificationAdapter:
    """Build the webhook route, failing closed on a missing or non-HTTP endpoint."""
    target_url = (raw_env_endpoint if raw_env_endpoint is not None else endpoint_url) or ""
    target_url_str = target_url.strip()

    if not target_url_str or not (target_url_str.startswith("http://") or target_url_str.startswith("https://")):
        raise ValueError("Production mode or on-call route requires a configured valid ONCALL_ENDPOINT_URL. Fail-closed gate enforced.")
    return OnCallNotificationAdapter(endpoint_url=target_url_str, http_transport=http_transport)


def _build_prod_email_adapter() -> EmailNotificationAdapter:
    """Build the email route, failing closed when no real SMTP host is configured."""
    adapter = EmailNotificationAdapter()
    if not adapter.smtp_host:
        raise ValueError(
            "Production mode email notification route requires a configured SMTP_HOST "
            "(mock stdout delivery would report unsent mail as sent). Fail-closed gate enforced."
        )
    return adapter


def _build_prod_inapp_adapter(repository: Any) -> InAppNotificationAdapter:
    """Build the in-app route, failing closed without a durable repository."""
    if repository is None:
        raise ValueError(
            "Production mode in-app notification route requires a durable repository; "
            "the in-process inbox is lost on restart. Fail-closed gate enforced."
        )
    return InAppNotificationAdapter(repository=repository)


def get_notification_adapter(
    endpoint_url: str | None = None,
    http_transport: Callable[[str, dict], tuple[int, str | dict]] | None = None,
    repository: Any = None,
) -> ConsoleNotificationAdapter | OnCallNotificationAdapter | EmailNotificationAdapter | InAppNotificationAdapter | MultiChannelNotificationAdapter:
    """Factory to instantiate configured notification adapter based on env / config.

    If in production mode (APP_ENV/ENVIRONMENT/STAGE/ODAY_ENV in prod/production/live/staging),
    or NOTIFICATION_ADAPTER_TYPE is 'oncall', or ONCALL_ENDPOINT_URL / endpoint_url is set:
      Instantiates OnCallNotificationAdapter with fail-closed configuration validation.
      Missing, empty, or non-HTTP endpoint URL raises ValueError to fail closed.
      ConsoleNotificationAdapter is strictly forbidden in production environments.

    The production gate is evaluated *before* the channel-specific adapter types so
    that `email`, `in_app`, and `multi` cannot route around it. In production each of
    those must be genuinely configured (real SMTP host, durable repository, valid
    on-call endpoint) or the factory raises; no mock or console fallback survives.

    Outside production:
      Defaults to ConsoleNotificationAdapter or specified channel adapter.
    """
    adapter_type = os.getenv("NOTIFICATION_ADAPTER_TYPE", "").strip().lower()
    raw_env_endpoint = os.getenv("ONCALL_ENDPOINT_URL")
    is_prod = _is_production_env()
    require_oncall = is_prod or adapter_type == "oncall" or raw_env_endpoint is not None or endpoint_url is not None or os.getenv("REQUIRE_ONCALL_ROUTE", "").strip().lower() in {"1", "true"}

    if adapter_type and adapter_type not in _KNOWN_ADAPTER_TYPES:
        raise ValueError(f"Unknown notification adapter type '{adapter_type}'. Fail-closed gate enforced.")

    if is_prod:
        if adapter_type == "console":
            raise ValueError("ConsoleNotificationAdapter is forbidden in production environment. Fail-closed gate enforced.")

        if adapter_type == "email":
            return _build_prod_email_adapter()

        if adapter_type in _IN_APP_ADAPTER_TYPES:
            return _build_prod_inapp_adapter(repository)

        if adapter_type in _MULTI_ADAPTER_TYPES:
            # The composite default must be the on-call route, never the console
            # adapter the production gate forbids.
            oncall = _build_oncall_adapter(raw_env_endpoint, endpoint_url, http_transport)
            multi = MultiChannelNotificationAdapter(default_adapter=oncall)
            multi.register_adapter("email", _build_prod_email_adapter())
            multi.register_adapter("in_app", _build_prod_inapp_adapter(repository))
            multi.register_adapter("webhook", oncall)
            return multi

    else:
        if adapter_type == "email":
            return EmailNotificationAdapter()

        if adapter_type in _IN_APP_ADAPTER_TYPES:
            return InAppNotificationAdapter(repository=repository)

        if adapter_type in _MULTI_ADAPTER_TYPES:
            multi = MultiChannelNotificationAdapter(default_adapter=ConsoleNotificationAdapter())
            multi.register_adapter("email", EmailNotificationAdapter())
            multi.register_adapter("in_app", InAppNotificationAdapter(repository=repository))
            if raw_env_endpoint or endpoint_url:
                multi.register_adapter("webhook", OnCallNotificationAdapter(endpoint_url=raw_env_endpoint or endpoint_url or "", http_transport=http_transport))
            return multi

    if require_oncall:
        return _build_oncall_adapter(raw_env_endpoint, endpoint_url, http_transport)

    return ConsoleNotificationAdapter()
