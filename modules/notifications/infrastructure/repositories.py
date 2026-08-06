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
    _inapp_inbox: list[dict] = field(default_factory=list)

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

    def save_inapp_item(self, item: dict) -> dict:
        self._inapp_inbox.append(item)
        return item

    def get_inapp_items(
        self,
        user_id: str | None = None,
        severity: str | None = None,
        acknowledged: bool | None = None,
    ) -> list[dict]:
        res = list(self._inapp_inbox)
        if user_id:
            res = [i for i in res if i.get("user_id") == user_id]
        if severity:
            res = [i for i in res if i.get("severity") == severity]
        if acknowledged is not None:
            res = [i for i in res if i.get("acknowledged") is acknowledged]
        return res

    def acknowledge_inapp_item(self, user_id: str, notification_id: str) -> bool:
        now_iso = datetime.now(UTC).isoformat()
        found = False
        for i in self._inapp_inbox:
            if (not user_id or i.get("user_id") == user_id) and i.get("notification_id") == notification_id:
                i["acknowledged"] = True
                i["acknowledged_at"] = now_iso
                found = True
        return found


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

    def save_inapp_item(self, item: dict) -> dict:
        created_at = item.get("created_at") or datetime.now(UTC).isoformat()
        self._engine.execute(
            "INSERT INTO notification_inapp_inbox (notification_id, user_id, title, detail, severity, created_at, acknowledged, acknowledged_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(notification_id) DO UPDATE SET "
            "  title = excluded.title, "
            "  detail = excluded.detail, "
            "  severity = excluded.severity, "
            "  acknowledged = excluded.acknowledged, "
            "  acknowledged_at = excluded.acknowledged_at",
            (
                item["notification_id"],
                item["user_id"],
                item["title"],
                item["detail"],
                item.get("severity", "info"),
                created_at,
                1 if item.get("acknowledged") else 0,
                item.get("acknowledged_at"),
            ),
        )
        return item

    def get_inapp_items(
        self,
        user_id: str | None = None,
        severity: str | None = None,
        acknowledged: bool | None = None,
    ) -> list[dict]:
        query = "SELECT * FROM notification_inapp_inbox WHERE 1=1"
        params: list[Any] = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        if acknowledged is not None:
            query += " AND acknowledged = ?"
            params.append(1 if acknowledged else 0)
        query += " ORDER BY created_at DESC"
        rows = self._engine.query(query, tuple(params))
        return [
            {
                "notification_id": row["notification_id"],
                "user_id": row["user_id"],
                "title": row["title"],
                "detail": row["detail"],
                "severity": row["severity"],
                "created_at": row["created_at"],
                "acknowledged": bool(row["acknowledged"]),
                "acknowledged_at": row["acknowledged_at"],
            }
            for row in rows
        ]

    def acknowledge_inapp_item(self, user_id: str, notification_id: str) -> bool:
        """Acknowledge one inbox item, scoped to ``user_id`` when given.

        Returns whether a row actually matched — the same contract as the
        in-memory repository. Acknowledging a nonexistent notification, or one
        owned by another user, must report ``False`` so an API layer built on
        this cannot answer "acknowledged" to a cross-user attempt.
        """
        now_iso = datetime.now(UTC).isoformat()
        if user_id:
            result = self._engine.execute(
                "UPDATE notification_inapp_inbox SET acknowledged = 1, acknowledged_at = ? WHERE notification_id = ? AND user_id = ?",
                (now_iso, notification_id, user_id),
            )
        else:
            result = self._engine.execute(
                "UPDATE notification_inapp_inbox SET acknowledged = 1, acknowledged_at = ? WHERE notification_id = ?",
                (now_iso, notification_id),
            )
        return int(getattr(result, "rowcount", -1)) > 0
