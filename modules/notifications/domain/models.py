from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4


@dataclass
class UserPreference:
    user_id: str
    channels: list[str] = field(default_factory=lambda: ["email"])
    enabled: bool = True

@dataclass
class NotificationReceipt:
    receipt_id: str = field(default_factory=lambda: str(uuid4()))
    notification_id: str = field(default_factory=lambda: str(uuid4()))
    channel: str = "email"
    status: str = "queued" # queued, sent, failed, escalated
    retry_count: int = 0
    last_attempt: datetime | None = None
    error_message: str | None = None
    delivered_at: datetime | None = None
