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


def test_email_notification_adapter_flow(tmp_path) -> None:
    from modules.notifications import EmailNotificationAdapter

    sent_emails: list[dict] = []

    def mock_smtp_transport(payload: dict) -> tuple[bool, str | None]:
        sent_emails.append(payload)
        return True, None

    adapter = EmailNotificationAdapter(
        smtp_host="smtp.oday.plus",
        smtp_port=587,
        smtp_from_email="noreply@oday.plus",
        smtp_transport=mock_smtp_transport,
    )

    success, err = adapter.send("nid-email-1", "email", "ops-lead@oday.plus", "Task Assigned", "Task #123 assigned")
    assert success is True
    assert err is None
    assert len(sent_emails) == 1
    assert sent_emails[0]["user_id"] == "ops-lead@oday.plus"

    receipts = adapter.delivery_receipts
    assert len(receipts) == 1
    assert receipts[0]["channel"] == "email"
    assert receipts[0]["status"] == "SENT"


def test_email_notification_adapter_fail_closed(monkeypatch) -> None:
    from modules.notifications import EmailNotificationAdapter

    monkeypatch.setenv("REQUIRE_EMAIL_ROUTE", "1")
    adapter = EmailNotificationAdapter(smtp_host=None)

    success, err = adapter.send("nid-fail-1", "email", "user-1", "Title", "Detail")
    assert success is False
    assert err is not None
    assert "REQUIRE_EMAIL_ROUTE" in err


def test_inapp_notification_adapter_flow() -> None:
    from modules.notifications import InAppNotificationAdapter, InMemoryNotificationRepository

    repo = InMemoryNotificationRepository()
    adapter = InAppNotificationAdapter(repository=repo)

    success, err = adapter.send("nid-inapp-1", "in_app", "store-manager", "Approval Needed", "Please approve task T-99")
    assert success is True
    assert err is None

    # Retrieve inbox
    inbox = adapter.get_inbox("store-manager")
    assert len(inbox) == 1
    assert inbox[0]["notification_id"] == "nid-inapp-1"
    assert inbox[0]["acknowledged"] is False

    # Acknowledge
    ack_res = adapter.acknowledge_notification("store-manager", "nid-inapp-1")
    assert ack_res is True

    inbox_after = adapter.get_inbox("store-manager")
    assert inbox_after[0]["acknowledged"] is True
    assert inbox_after[0]["acknowledged_at"] is not None


def test_multi_channel_notification_adapter_flow() -> None:
    from modules.notifications import (
        EmailNotificationAdapter,
        InAppNotificationAdapter,
        InMemoryNotificationRepository,
        MultiChannelNotificationAdapter,
    )

    repo = InMemoryNotificationRepository()
    email_adapter = EmailNotificationAdapter()
    inapp_adapter = InAppNotificationAdapter(repository=repo)

    multi = MultiChannelNotificationAdapter()
    multi.register_adapter("email", email_adapter)
    multi.register_adapter("in_app", inapp_adapter)

    # Send email
    ok1, err1 = multi.send("nid-m1", "email", "user@oday.plus", "Email Title", "Email detail")
    assert ok1 is True

    # Send in-app
    ok2, err2 = multi.send("nid-m2", "in_app", "hq-admin", "InApp Title", "InApp detail")
    assert ok2 is True

    assert len(multi.delivery_receipts) == 2


def test_five_spec_triggers_flow() -> None:
    from modules.notifications import (
        InAppNotificationAdapter,
        InMemoryNotificationRepository,
        NotificationService,
    )

    repo = InMemoryNotificationRepository()
    adapter = InAppNotificationAdapter(repository=repo)
    service = NotificationService(repository=repo, adapter=adapter)

    # 1. Task Assigned
    nid_assign = service.send_task_assigned_notification("ops-lead", "ODP-TASK-100", "Deploy Cloud Run", "system-operator")
    assert nid_assign is not None

    # 2. Timeout
    nid_timeout = service.send_timeout_notification("area-manager", "ODP-TASK-101", 300)
    assert nid_timeout is not None

    # 3. Approval
    nid_approve = service.send_approval_notification("hq-admin", "ODP-TASK-102", "franchisee-ops", "Approved for deployment")
    assert nid_approve is not None

    # 4. Failure
    nid_fail = service.send_failure_notification("system-operator", "ODP-TASK-103", "Container exit code 1")
    assert nid_fail is not None

    # 5. Rollback
    nid_rollback = service.send_rollback_notification("store-manager", "ODP-TASK-104", "v1.4.2-stable")
    assert nid_rollback is not None

    inbox = adapter.get_inbox()
    assert len(inbox) == 5
    titles = [item["title"] for item in inbox]
    assert any("[Task Assigned]" in t for t in titles)
    assert any("[Timeout Alert]" in t for t in titles)
    assert any("[Task Approved]" in t for t in titles)
    assert any("[Task Failed]" in t for t in titles)
    assert any("[Rollback Executed]" in t for t in titles)


def test_six_canonical_roles_delivery() -> None:
    from modules.notifications import (
        InAppNotificationAdapter,
        InMemoryNotificationRepository,
        NotificationService,
    )

    repo = InMemoryNotificationRepository()
    adapter = InAppNotificationAdapter(repository=repo)
    service = NotificationService(repository=repo, adapter=adapter)

    roles = [
        "ops-lead",
        "franchisee-ops",
        "store-manager",
        "area-manager",
        "hq-admin",
        "system-operator",
    ]

    for role in roles:
        nid = service.send_task_assigned_notification(role, f"TASK-{role}", f"Task for {role}")
        assert nid is not None
        user_inbox = adapter.get_inbox(role)
        assert len(user_inbox) == 1
        assert user_inbox[0]["user_id"] == role

