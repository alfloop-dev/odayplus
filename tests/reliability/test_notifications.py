from __future__ import annotations

import os
from pathlib import Path

import pytest

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


# --- B1: the production fail-closed gate must sit above the channel adapters ---


@pytest.mark.parametrize("adapter_type", ["email", "in_app", "inapp", "in-app", "multi", "composite"])
def test_production_gate_rejects_unconfigured_channel_adapters(monkeypatch, adapter_type) -> None:
    """APP_ENV=production must never hand back a console/mock delivery route."""
    from modules.notifications import get_notification_adapter

    for var in (
        "ODP_PRODUCT_MODE",
        "ODAY_PRODUCT_MODE",
        "ENVIRONMENT",
        "STAGE",
        "ODAY_ENV",
        "ONCALL_ENDPOINT_URL",
        "SMTP_HOST",
        "EMAIL_SMTP_HOST",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("NOTIFICATION_ADAPTER_TYPE", adapter_type)

    with pytest.raises(ValueError):
        get_notification_adapter()


def test_production_multi_adapter_never_defaults_to_console(monkeypatch) -> None:
    """A fully configured production `multi` route defaults to on-call, not console."""
    from modules.notifications import (
        ConsoleNotificationAdapter,
        EmailNotificationAdapter,
        InAppNotificationAdapter,
        InMemoryNotificationRepository,
        MultiChannelNotificationAdapter,
        OnCallNotificationAdapter,
        get_notification_adapter,
    )

    for var in ("ODP_PRODUCT_MODE", "ODAY_PRODUCT_MODE", "ENVIRONMENT", "STAGE", "ODAY_ENV"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("NOTIFICATION_ADAPTER_TYPE", "multi")
    monkeypatch.setenv("ONCALL_ENDPOINT_URL", "https://oncall-router.oday.plus/api/v1/alerts")
    monkeypatch.setenv("SMTP_HOST", "smtp.oday.plus")

    adapter = get_notification_adapter(repository=InMemoryNotificationRepository())

    assert isinstance(adapter, MultiChannelNotificationAdapter)
    assert not isinstance(adapter.default_adapter, ConsoleNotificationAdapter)
    assert isinstance(adapter.default_adapter, OnCallNotificationAdapter)
    assert isinstance(adapter.channel_adapters["email"], EmailNotificationAdapter)
    assert adapter.channel_adapters["email"].smtp_host == "smtp.oday.plus"
    assert isinstance(adapter.channel_adapters["in_app"], InAppNotificationAdapter)
    assert isinstance(adapter.channel_adapters["webhook"], OnCallNotificationAdapter)


def test_production_email_route_requires_real_smtp_host(monkeypatch) -> None:
    """`email` in production must not fall through to the mock stdout transport."""
    from modules.notifications import EmailNotificationAdapter, get_notification_adapter

    for var in ("ODP_PRODUCT_MODE", "ODAY_PRODUCT_MODE", "ENVIRONMENT", "STAGE", "ODAY_ENV"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_SMTP_HOST", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("NOTIFICATION_ADAPTER_TYPE", "email")

    with pytest.raises(ValueError, match="SMTP_HOST"):
        get_notification_adapter()

    monkeypatch.setenv("SMTP_HOST", "smtp.oday.plus")
    configured = get_notification_adapter()
    assert isinstance(configured, EmailNotificationAdapter)
    assert configured.smtp_host == "smtp.oday.plus"


def test_email_adapter_mock_transport_fails_closed_in_production(monkeypatch) -> None:
    """The mock branch must not report `sent` for mail that was never delivered."""
    from modules.notifications import EmailNotificationAdapter

    for var in ("ODP_PRODUCT_MODE", "ODAY_PRODUCT_MODE", "ENVIRONMENT", "STAGE", "ODAY_ENV"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("REQUIRE_EMAIL_ROUTE", raising=False)
    monkeypatch.setenv("APP_ENV", "production")

    adapter = EmailNotificationAdapter(smtp_host=None)
    success, err = adapter.send("nid-prod-mock", "email", "ops@oday.plus", "Title", "Detail")

    assert success is False
    assert err is not None and "SMTP_HOST" in err
    assert adapter.delivery_receipts[-1]["status"] == "FAILED"

    # The transport itself is fail-closed too, not just the send() precondition.
    transport_ok, transport_err = adapter._default_smtp_transport(
        {"user_id": "ops@oday.plus", "title": "Title", "detail": "Detail"}
    )
    assert transport_ok is False
    assert transport_err is not None


def test_non_production_channel_adapters_still_resolve(monkeypatch) -> None:
    """The prod gate must not change local/dev behaviour."""
    from modules.notifications import (
        EmailNotificationAdapter,
        InAppNotificationAdapter,
        InMemoryNotificationRepository,
        MultiChannelNotificationAdapter,
        get_notification_adapter,
    )

    for var in ("ODP_PRODUCT_MODE", "ODAY_PRODUCT_MODE", "APP_ENV", "ENVIRONMENT", "STAGE", "ODAY_ENV", "ONCALL_ENDPOINT_URL"):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("NOTIFICATION_ADAPTER_TYPE", "email")
    assert isinstance(get_notification_adapter(), EmailNotificationAdapter)

    monkeypatch.setenv("NOTIFICATION_ADAPTER_TYPE", "in_app")
    assert isinstance(get_notification_adapter(repository=InMemoryNotificationRepository()), InAppNotificationAdapter)

    monkeypatch.setenv("NOTIFICATION_ADAPTER_TYPE", "multi")
    assert isinstance(get_notification_adapter(repository=InMemoryNotificationRepository()), MultiChannelNotificationAdapter)


# --- B2: durable dedup keys must be stable across processes ---


def test_failure_dedup_key_is_process_stable() -> None:
    """The failure trigger's dedup key must not embed a PYTHONHASHSEED-random value."""
    import subprocess
    import sys as _sys

    repo = InMemoryNotificationRepository()
    adapter = MockNotificationAdapter()
    service = NotificationService(repository=repo, adapter=adapter)

    assert service.send_failure_notification("ops-lead", "T-500", "boom") is not None
    keys = list(repo._deduplication.keys())
    assert len(keys) == 1
    key = keys[0]

    # Same input in a fresh interpreter (different hash seed) yields the same key.
    probe = (
        "from modules.notifications import InMemoryNotificationRepository, "
        "MockNotificationAdapter, NotificationService;"
        "r=InMemoryNotificationRepository();"
        "NotificationService(repository=r, adapter=MockNotificationAdapter())"
        ".send_failure_notification('ops-lead','T-500','boom');"
        "print(list(r._deduplication.keys())[0])"
    )
    import modules.notifications as _notifications

    repo_root = Path(_notifications.__file__).resolve().parents[2]
    for seed in ("0", "1", "12345"):
        out = subprocess.run(
            [_sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            check=True,
            cwd=repo_root,
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(repo_root)},
        )
        assert out.stdout.strip() == key

    # A different error text must still produce a different key.
    repo2 = InMemoryNotificationRepository()
    service2 = NotificationService(repository=repo2, adapter=MockNotificationAdapter())
    service2.send_failure_notification("ops-lead", "T-500", "other failure")
    assert list(repo2._deduplication.keys())[0] != key


# --- B3: severity must reach the in-app inbox ---


def test_trigger_severity_propagates_to_inapp_inbox() -> None:
    """danger/warning triggers must be filterable in the inbox, not flattened to info."""
    from modules.notifications import InAppNotificationAdapter

    repo = InMemoryNotificationRepository()
    adapter = InAppNotificationAdapter(repository=repo)
    service = NotificationService(repository=repo, adapter=adapter)

    service.send_task_assigned_notification("ops-lead", "T-1", "Deploy")
    service.send_timeout_notification("area-manager", "T-2", 300)
    service.send_approval_notification("hq-admin", "T-3", "franchisee-ops")
    service.send_failure_notification("system-operator", "T-4", "exit code 1")
    service.send_rollback_notification("store-manager", "T-5", "v1.4.2-stable")

    by_title = {item["title"].split("]")[0] + "]": item for item in adapter.get_inbox()}
    assert by_title["[Task Assigned]"]["severity"] == "info"
    assert by_title["[Timeout Alert]"]["severity"] == "warning"
    assert by_title["[Task Approved]"]["severity"] == "info"
    assert by_title["[Task Failed]"]["severity"] == "danger"
    assert by_title["[Rollback Executed]"]["severity"] == "danger"

    assert len(adapter.get_inbox(severity="danger")) == 2
    assert len(adapter.get_inbox(severity="warning")) == 1
    assert len(adapter.get_inbox(severity="info")) == 2


def test_multi_channel_adapter_forwards_severity() -> None:
    from modules.notifications import (
        InAppNotificationAdapter,
        MultiChannelNotificationAdapter,
    )

    repo = InMemoryNotificationRepository()
    inapp = InAppNotificationAdapter(repository=repo)
    multi = MultiChannelNotificationAdapter()
    multi.register_adapter("in_app", inapp)

    ok, err = multi.send("nid-sev-1", "in_app", "hq-admin", "Rollback", "Detail", severity="danger")
    assert ok is True
    assert err is None
    assert inapp.get_inbox("hq-admin")[0]["severity"] == "danger"


def test_service_tolerates_adapter_without_severity_parameter() -> None:
    """A duck-typed adapter on the older five-argument signature must still work."""

    class LegacyAdapter:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def send(self, notification_id, channel, user_id, title, detail):
            self.calls.append((notification_id, channel, user_id, title, detail))
            return True, None

    repo = InMemoryNotificationRepository()
    legacy = LegacyAdapter()
    service = NotificationService(repository=repo, adapter=legacy)

    assert service.send_rollback_notification("store-manager", "T-9", "v1") is not None
    assert len(legacy.calls) == 1


# --- B4: the durable in-app path ---


def test_durable_inapp_notification_flow(tmp_path) -> None:
    """Mirror of test_inapp_notification_adapter_flow against the durable repository."""
    from modules.notifications import InAppNotificationAdapter

    engine = SqliteEngine(tmp_path / "test_inapp.sqlite3")
    repo = DurableNotificationRepository(engine)
    adapter = InAppNotificationAdapter(repository=repo)

    success, err = adapter.send(
        "nid-durable-1", "in_app", "store-manager", "Approval Needed", "Please approve T-99", severity="warning"
    )
    assert success is True
    assert err is None

    inbox = adapter.get_inbox("store-manager")
    assert len(inbox) == 1
    assert inbox[0]["notification_id"] == "nid-durable-1"
    assert inbox[0]["severity"] == "warning"
    assert inbox[0]["acknowledged"] is False

    # severity filter is live against the durable column
    assert len(adapter.get_inbox("store-manager", severity="warning")) == 1
    assert len(adapter.get_inbox("store-manager", severity="danger")) == 0

    assert adapter.acknowledge_notification("store-manager", "nid-durable-1") is True
    acked = adapter.get_inbox("store-manager")[0]
    assert acked["acknowledged"] is True
    assert acked["acknowledged_at"] is not None

    engine.close()


def test_durable_acknowledge_reports_false_for_unmatched_rows(tmp_path) -> None:
    """Acking a missing or another user's notification must not report success."""
    engine = SqliteEngine(tmp_path / "test_inapp_ack.sqlite3")
    repo = DurableNotificationRepository(engine)

    repo.save_inapp_item(
        {
            "notification_id": "nid-alice-1",
            "user_id": "alice",
            "title": "Task Failed",
            "detail": "boom",
            "severity": "danger",
            "created_at": "2026-08-06T00:00:00+00:00",
            "acknowledged": False,
            "acknowledged_at": None,
        }
    )

    assert repo.acknowledge_inapp_item(user_id="alice", notification_id="does-not-exist") is False
    assert repo.acknowledge_inapp_item(user_id="bob", notification_id="nid-alice-1") is False
    assert repo.get_inapp_items(user_id="alice")[0]["acknowledged"] is False

    assert repo.acknowledge_inapp_item(user_id="alice", notification_id="nid-alice-1") is True
    assert repo.get_inapp_items(user_id="alice")[0]["acknowledged"] is True

    # Re-acking an already-acknowledged row still matches a row, so it stays True.
    assert repo.acknowledge_inapp_item(user_id="alice", notification_id="nid-alice-1") is True

    engine.close()


def test_durable_and_in_memory_acknowledge_agree(tmp_path) -> None:
    """The two repository implementations must answer the same contract identically."""
    engine = SqliteEngine(tmp_path / "test_inapp_parity.sqlite3")
    durable = DurableNotificationRepository(engine)
    memory = InMemoryNotificationRepository()

    item = {
        "notification_id": "nid-parity-1",
        "user_id": "alice",
        "title": "Rollback Executed",
        "detail": "rolled back",
        "severity": "danger",
        "created_at": "2026-08-06T00:00:00+00:00",
        "acknowledged": False,
        "acknowledged_at": None,
    }
    durable.save_inapp_item(dict(item))
    memory.save_inapp_item(dict(item))

    for repo in (durable, memory):
        assert repo.acknowledge_inapp_item(user_id="bob", notification_id="nid-parity-1") is False
        assert repo.acknowledge_inapp_item(user_id="alice", notification_id="missing") is False
        assert repo.acknowledge_inapp_item(user_id="alice", notification_id="nid-parity-1") is True

    engine.close()

