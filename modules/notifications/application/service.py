from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from modules.notifications.domain.models import NotificationReceipt, UserPreference

logger = logging.getLogger("oday-notifications")


class NotificationAdapter(Protocol):
    def send(
        self,
        notification_id: str,
        channel: str,
        user_id: str,
        title: str,
        detail: str,
    ) -> tuple[bool, str | None]:
        """Send a notification. Returns (success, error_message)."""
        ...


class MockNotificationAdapter:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []
        self.should_fail_channels: dict[str, int] = {} # channel -> fail count

    def send(
        self,
        notification_id: str,
        channel: str,
        user_id: str,
        title: str,
        detail: str,
    ) -> tuple[bool, str | None]:
        fail_count = self.should_fail_channels.get(channel, 0)
        if fail_count > 0:
            self.should_fail_channels[channel] = fail_count - 1
            return False, "Adapter error"

        self.sent_messages.append({
            "notification_id": notification_id,
            "channel": channel,
            "user_id": user_id,
            "title": title,
            "detail": detail,
            "timestamp": datetime.now(UTC),
        })
        return True, None


class NotificationService:
    def __init__(
        self,
        repository: Any,
        adapter: NotificationAdapter | None = None,
        max_retries: int = 3,
    ) -> None:
        self.repository = repository
        self.adapter = adapter or MockNotificationAdapter()
        self.max_retries = max_retries

    def get_preferences(self, user_id: str) -> UserPreference:
        pref = self.repository.get_preference(user_id)
        if pref is None:
            # Default preferences
            pref = UserPreference(user_id=user_id, channels=["email"], enabled=True)
            self.repository.save_preference(pref)
        return pref

    def set_preferences(self, user_id: str, channels: list[str], enabled: bool = True) -> UserPreference:
        pref = UserPreference(user_id=user_id, channels=channels, enabled=enabled)
        return self.repository.save_preference(pref)

    def send_notification(
        self,
        user_id: str,
        title: str,
        detail: str,
        *,
        dedup_key: str | None = None,
        severity: str = "info",
    ) -> str | None:
        notification_id = str(uuid.uuid4())

        # 1. Preferences
        pref = self.get_preferences(user_id)
        if not pref.enabled or not pref.channels:
            logger.info("Notification to user %s skipped: disabled or no channels", user_id)
            return None

        # 2. Deduplication
        if dedup_key:
            registered = self.repository.register_deduplication(dedup_key, notification_id)
            if not registered:
                logger.info("Notification with dedup_key %s skipped: duplicate", dedup_key)
                return None

        # 3. Sending logic
        channels = list(pref.channels)
        primary_channel = channels[0]

        receipt = NotificationReceipt(
            notification_id=notification_id,
            channel=primary_channel,
            status="queued",
        )
        self.repository.save_receipt(receipt)

        success = self._send_with_retries(receipt, user_id, title, detail)
        if success:
            return notification_id

        # 4. Escalation logic (if severity is high and there's a secondary channel)
        if severity in ("danger", "high", "warning") and len(channels) > 1:
            secondary_channel = channels[1]
            logger.warning("Primary delivery failed on %s. Escalating to %s", primary_channel, secondary_channel)

            # Update primary receipt to show it was escalated/failed
            receipt.status = "escalated"
            self.repository.save_receipt(receipt)

            escalated_receipt = NotificationReceipt(
                notification_id=notification_id,
                channel=secondary_channel,
                status="queued",
            )
            self.repository.save_receipt(escalated_receipt)

            esc_success = self._send_with_retries(escalated_receipt, user_id, title, detail)
            if esc_success:
                return notification_id

        return notification_id

    def _send_with_retries(
        self,
        receipt: NotificationReceipt,
        user_id: str,
        title: str,
        detail: str,
    ) -> bool:
        for attempt in range(1, self.max_retries + 1):
            receipt.last_attempt = datetime.now(UTC)
            receipt.retry_count = attempt - 1

            success, error_msg = self.adapter.send(
                receipt.notification_id,
                receipt.channel,
                user_id,
                title,
                detail,
            )

            if success:
                receipt.status = "sent"
                receipt.delivered_at = datetime.now(UTC)
                receipt.error_message = None
                self.repository.save_receipt(receipt)
                return True
            else:
                receipt.status = "failed"
                receipt.error_message = error_msg
                self.repository.save_receipt(receipt)

        return False
