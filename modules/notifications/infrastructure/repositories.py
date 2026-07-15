from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from modules.notifications.domain.models import NotificationReceipt, UserPreference


@dataclass
class InMemoryNotificationRepository:
    _preferences: dict[str, UserPreference] = field(default_factory=dict)
    _deduplication: dict[str, tuple[str, datetime]] = field(default_factory=dict)
    _receipts: dict[str, NotificationReceipt] = field(default_factory=dict)

    def save_preference(self, pref: UserPreference) -> UserPreference:
        self._preferences[pref.user_id] = pref
        return pref

    def get_preference(self, user_id: str) -> UserPreference | None:
        return self._preferences.get(user_id)

    def register_deduplication(self, dedup_key: str, notification_id: str) -> bool:
        if dedup_key in self._deduplication:
            return False
        self._deduplication[dedup_key] = (notification_id, datetime.now(UTC))
        return True

    def save_receipt(self, receipt: NotificationReceipt) -> NotificationReceipt:
        self._receipts[receipt.receipt_id] = receipt
        return receipt

    def get_receipt(self, receipt_id: str) -> NotificationReceipt | None:
        return self._receipts.get(receipt_id)

    def list_receipts_for_notification(self, notification_id: str) -> list[NotificationReceipt]:
        return [r for r in self._receipts.values() if r.notification_id == notification_id]


class DurableNotificationRepository:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def save_preference(self, pref: UserPreference) -> UserPreference:
        self._engine.execute(
            "INSERT INTO notification_preferences (user_id, channels, enabled) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "  channels = excluded.channels, "
            "  enabled = excluded.enabled",
            (pref.user_id, json.dumps(pref.channels), 1 if pref.enabled else 0)
        )
        return pref

    def get_preference(self, user_id: str) -> UserPreference | None:
        row = self._engine.query_one(
            "SELECT * FROM notification_preferences WHERE user_id = ?",
            (user_id,)
        )
        if not row:
            return None
        return UserPreference(
            user_id=row["user_id"],
            channels=json.loads(row["channels"]),
            enabled=bool(row["enabled"])
        )

    def register_deduplication(self, dedup_key: str, notification_id: str) -> bool:
        try:
            self._engine.execute(
                "INSERT INTO notification_deduplication (dedup_key, notification_id, created_at) "
                "VALUES (?, ?, ?)",
                (dedup_key, notification_id, datetime.now(UTC).isoformat())
            )
            return True
        except Exception:
            return False

    def save_receipt(self, receipt: NotificationReceipt) -> NotificationReceipt:
        self._engine.execute(
            "INSERT INTO notification_receipts (receipt_id, notification_id, channel, status, retry_count, last_attempt, error_message, delivered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(receipt_id) DO UPDATE SET "
            "  status = excluded.status, "
            "  retry_count = excluded.retry_count, "
            "  last_attempt = excluded.last_attempt, "
            "  error_message = excluded.error_message, "
            "  delivered_at = excluded.delivered_at",
            (
                receipt.receipt_id,
                receipt.notification_id,
                receipt.channel,
                receipt.status,
                receipt.retry_count,
                receipt.last_attempt.isoformat() if receipt.last_attempt else None,
                receipt.error_message,
                receipt.delivered_at.isoformat() if receipt.delivered_at else None
            )
        )
        return receipt

    def get_receipt(self, receipt_id: str) -> NotificationReceipt | None:
        row = self._engine.query_one(
            "SELECT * FROM notification_receipts WHERE receipt_id = ?",
            (receipt_id,)
        )
        if not row:
            return None
        return NotificationReceipt(
            receipt_id=row["receipt_id"],
            notification_id=row["notification_id"],
            channel=row["channel"],
            status=row["status"],
            retry_count=row["retry_count"],
            last_attempt=datetime.fromisoformat(row["last_attempt"]) if row["last_attempt"] else None,
            error_message=row["error_message"],
            delivered_at=datetime.fromisoformat(row["delivered_at"]) if row["delivered_at"] else None
        )

    def list_receipts_for_notification(self, notification_id: str) -> list[NotificationReceipt]:
        rows = self._engine.query(
            "SELECT * FROM notification_receipts WHERE notification_id = ?",
            (notification_id,)
        )
        return [
            NotificationReceipt(
                receipt_id=row["receipt_id"],
                notification_id=row["notification_id"],
                channel=row["channel"],
                status=row["status"],
                retry_count=row["retry_count"],
                last_attempt=datetime.fromisoformat(row["last_attempt"]) if row["last_attempt"] else None,
                error_message=row["error_message"],
                delivered_at=datetime.fromisoformat(row["delivered_at"]) if row["delivered_at"] else None
            )
            for row in rows
        ]
