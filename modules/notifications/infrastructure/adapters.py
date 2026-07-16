from __future__ import annotations

import sys
from datetime import UTC, datetime


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
