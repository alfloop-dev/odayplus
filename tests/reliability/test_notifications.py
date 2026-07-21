from __future__ import annotations

from modules.notifications import (
    DurableNotificationRepository,
    InMemoryNotificationRepository,
    MockNotificationAdapter,
    NotificationService,
)
from shared.infrastructure.persistence.engine import SqliteEngine


def test_in_memory_notification_flow() -> None:
    repo = InMemoryNotificationRepository()
    adapter = MockNotificationAdapter()
    service = NotificationService(repository=repo, adapter=adapter)

    # 1. Test default preferences
    pref = service.get_preferences("user-1")
    assert pref.user_id == "user-1"
    assert pref.channels == ["email"]
    assert pref.enabled is True

    # 2. Update preferences
    service.set_preferences("user-1", ["email", "sms"], enabled=True)
    pref2 = service.get_preferences("user-1")
    assert pref2.channels == ["email", "sms"]

    # 3. Send notification
    nid = service.send_notification("user-1", "Hello", "Welcome to ODay Plus")
    assert nid is not None

    # Check adapter sent it
    assert len(adapter.sent_messages) == 1
    assert adapter.sent_messages[0]["notification_id"] == nid
    assert adapter.sent_messages[0]["channel"] == "email"

    # Check receipts
    receipts = repo.list_receipts_for_notification(nid)
    assert len(receipts) == 1
    assert receipts[0].status == "sent"
    assert receipts[0].channel == "email"


def test_notifications_deduplication() -> None:
    repo = InMemoryNotificationRepository()
    adapter = MockNotificationAdapter()
    service = NotificationService(repository=repo, adapter=adapter)

    # Send first time
    nid1 = service.send_notification("user-1", "Alert", "Critical issue", dedup_key="key-123")
    assert nid1 is not None

    # Send second time with same key
    nid2 = service.send_notification("user-1", "Alert", "Critical issue", dedup_key="key-123")
    assert nid2 is None
    assert len(adapter.sent_messages) == 1


def test_notifications_retries_and_escalation() -> None:
    repo = InMemoryNotificationRepository()
    adapter = MockNotificationAdapter()
    # Configure email to fail, sms to succeed
    adapter.should_fail_channels["email"] = 3
    service = NotificationService(repository=repo, adapter=adapter, max_retries=3)

    # Configure multiple channels for user
    service.set_preferences("user-2", ["email", "sms"])

    # Send high-severity notification (warning/danger/high)
    nid = service.send_notification("user-2", "Danger", "System down!", severity="high")
    assert nid is not None

    # Verify escalation occurred
    receipts = repo.list_receipts_for_notification(nid)
    # We should have two receipts: email (escalated/failed) and sms (sent)
    receipts_by_channel = {r.channel: r for r in receipts}
    assert "email" in receipts_by_channel
    assert "sms" in receipts_by_channel

    assert receipts_by_channel["email"].status == "escalated"
    assert receipts_by_channel["email"].retry_count == 2  # 0, 1, 2 = 3 attempts
    assert receipts_by_channel["sms"].status == "sent"
    assert receipts_by_channel["sms"].retry_count == 0


def test_durable_notifications_flow(tmp_path) -> None:
    db_file = tmp_path / "test_notifications.sqlite3"
    engine = SqliteEngine(db_file)

    repo = DurableNotificationRepository(engine)
    adapter = MockNotificationAdapter()
    service = NotificationService(repository=repo, adapter=adapter)

    # 1. Preferences
    service.set_preferences("user-durable", ["sms", "email"], enabled=True)
    pref = service.get_preferences("user-durable")
    assert pref.channels == ["sms", "email"]
    assert pref.enabled is True

    # 2. Deduplication
    nid1 = service.send_notification("user-durable", "Title", "Body", dedup_key="dup-1")
    assert nid1 is not None
    nid2 = service.send_notification("user-durable", "Title", "Body", dedup_key="dup-1")
    assert nid2 is None

    # 3. Receipts retrieval
    receipts = repo.list_receipts_for_notification(nid1)
    assert len(receipts) == 1
    assert receipts[0].channel == "sms"
    assert receipts[0].status == "sent"

    engine.close()
