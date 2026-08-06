# Acceptance Packet & Dependency Map: ODP-CAP-NOTIFICATION-DELIVERY-001

- Sidecar Task ID: `ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-ACCEPTANCE`
- Parent Task ID: `ODP-CAP-NOTIFICATION-DELIVERY-001`
- Helper Kind: `acceptance_packet`
- Owner / Author: `Antigravity4`
- Assigned Reviewer / Parent Task Owner: `Claude`
- Date: 2026-08-06
- Target Capability: `FR-SHARED-006` Notification Delivery (In-App, Email, Webhook)
- Boundary Declaration: Sidecar support packet only. Does not mutate canonical architecture docs or L1 contracts.

---

## 1. Executive Summary & Functional Objective

The parent task **ODP-CAP-NOTIFICATION-DELIVERY-001** addresses critical functional requirement **FR-SHARED-006** (In-app, Email, and Webhook notification delivery). 

### Current Gap Analysis
An audit of `modules/notifications/` reveals:
1. **Existing Webhook & Console Support**: `OnCallNotificationAdapter` (HTTP webhook with SHA validation) and `ConsoleNotificationAdapter` are fully implemented.
2. **Missing Email Delivery**: `UserPreference` defaults to `channels = ["email"]`, but no dedicated SMTP / Email provider adapter (`EmailNotificationAdapter`) currently exists in `modules/notifications/infrastructure/adapters.py`.
3. **Missing In-App Inbox Store & API**: No queryable persistence layer or FastAPI router endpoint exists for fetching user in-app notifications (`GET /api/v1/notifications/unread` or `UX-SCR-NOTIF-001`).
4. **UAT Blocker**: The downstream task **ODP-PLAN-UAT-SIGNOFF-001** requires 6 canonical roles (`executive`, `operations_manager`, `region_director`, `store_manager`, `finance_auditor`, `system_admin`) to receive real or testable notifications across 5 key operational triggers.

This acceptance packet establishes the exact dependency map, trigger-channel delivery matrix, adapter contract blueprints, and verification protocol required for parent task closeout.

---

## 2. Dependency Map

```mermaid
graph TD
    subgraph Upstream Contracts & Specs
        FR_SHARED_006["FR-SHARED-006<br/>Notification Channels (In-App, Email, Webhook)"]
        FR_OPS_002["ODP-FR-OPS-002<br/>Task Lifecycle Events & Escalation"]
        USER_ROLE_MANAGEMENT["ODP-CAP-USER-ROLE-UI-001<br/>6 Canonical User Roles & Preferences"]
    end

    subgraph Core Delivery Engine (ODP-CAP-NOTIFICATION-DELIVERY-001)
        NS["modules.notifications.application.NotificationService"]
        PREF["domain.models.UserPreference"]
        RECEIPT["domain.models.NotificationReceipt"]

        subgraph Infrastructure Adapters
            CONSOLE_ADAPTER["ConsoleNotificationAdapter<br/>(Dev/Logs)"]
            WEBHOOK_ADAPTER["OnCallNotificationAdapter<br/>(HTTP Webhook)"]
            EMAIL_ADAPTER["EmailNotificationAdapter<br/>(SMTP / Provider) [NEW]"]
            INAPP_ADAPTER["InAppNotificationAdapter<br/>(Inbox Store / API) [NEW]"]
        end
    end

    subgraph Downstream Consumers & UAT Signoff
        UAT_SIGNOFF["ODP-PLAN-UAT-SIGNOFF-001<br/>6-Role UAT Signoff & Acceptance"]
        OBS_ALERTS["shared.observability.alerts<br/>Runtime Observability Alerts"]
        OPSBOARD["apps/web/features/operator<br/>Operator Console Header / Drawer"]
    end

    FR_SHARED_006 --> NS
    FR_OPS_002 --> NS
    USER_ROLE_MANAGEMENT --> PREF

    NS --> CONSOLE_ADAPTER
    NS --> WEBHOOK_ADAPTER
    NS --> EMAIL_ADAPTER
    NS --> INAPP_ADAPTER

    NS --> RECEIPT
    INAPP_ADAPTER --> OPSBOARD
    RECEIPT --> UAT_SIGNOFF
    OBS_ALERTS --> WEBHOOK_ADAPTER
```

### Upstream & Downstream Interconnects

| Surface / Task | Relationship | Impact / Requirement |
| --- | --- | --- |
| **`FR-SHARED-006`** | Specification Origin | Defines mandatory support for In-App, Email, and Webhook notification channels. |
| **`ODP-CAP-USER-ROLE-UI-001`** | User Context | Provides user IDs and roles (`executive`, `operations_manager`, `region_director`, `store_manager`, `finance_auditor`, `system_admin`) for preference resolution. |
| **`ODP-FR-OPS-002`** | Event Origin | Emits task lifecycle events: assignment, SLA timeout, approval request, task failure, and rollback. |
| **`ODP-PLAN-UAT-SIGNOFF-001`** | Downstream UAT Gate | Requires verifiable delivery receipts across email and in-app channels for all 6 roles before UAT signoff. |
| **`shared/observability/alerts.py`** | Observability Consumer | Dispatches high-severity alert notifications via `NotificationService`. |

---

## 3. Trigger & Channel Delivery Matrix

`FR-SHARED-006` and `ODP-FR-OPS-002` require notification delivery across **5 core triggers** and **3 delivery channels**.

| Trigger Event (`trigger_type`) | Trigger Scenario | In-App (`in_app`) | Email (`email`) | Webhook (`webhook`) | Default Severity | Escalation Path |
| --- | --- | --- | --- | --- | --- | --- |
| **1. `task_assignment`** | Task assigned to operator or role | Yes (Inbox) | Yes (Digest / Instant) | Optional | `info` | In-App → Email |
| **2. `timeout`** | Task SLA deadline breached or expiring | Yes (Banner) | Yes (Urgent Email) | Yes (On-Call) | `warning` | In-App + Email → Webhook |
| **3. `approval`** | Approval requested, approved, or rejected | Yes (Badge) | Yes (Action Email) | Optional | `info` | In-App → Email |
| **4. `failure`** | Automation execution or job failed | Yes (Toast) | Yes (Alert Email) | Yes (On-Call) | `high` / `danger` | Webhook + Email |
| **5. `rollback`** | Deployment or canary rollback triggered | Yes (Alert Banner)| Yes (High Priority Email)| Yes (On-Call Pager)| `danger` | Webhook + Email + In-App |

---

## 4. Adapter Blueprint & Architecture Specifications

To fulfill `ODP-CAP-NOTIFICATION-DELIVERY-001` without modifying canonical contracts, the parent implementation should expand `modules/notifications/infrastructure/adapters.py` and register the corresponding adapters:

### 4.1 Email Notification Adapter (`EmailNotificationAdapter`)
- **Protocol Method**: `send(notification_id, channel, user_id, title, detail)`
- **Configuration**:
  - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL` (or `EMAIL_PROVIDER_TYPE=smtp|mock|ses`).
  - Fallback to Mock/Console in non-production environments (`ODP_PRODUCT_MODE != prod`).
- **Receipt Payload**: Includes recipient email address, SMTP message ID or provider delivery token, timestamp, and status (`sent` / `failed`).

### 4.2 In-App Notification Adapter & Inbox Store (`InAppNotificationAdapter`)
- **Protocol Method**: `send(notification_id, channel, user_id, title, detail)`
- **Inbox Store Interface**:
  - `save_inbox_item(user_id, notification_id, title, detail, severity, read=False)`
  - `get_user_inbox(user_id, unread_only=False, limit=50)`
  - `mark_as_read(user_id, notification_id)`
- **API Endpoint (`apps/api/app/routes/notifications.py`)**:
  - `GET /api/v1/notifications/inbox` -> returns list of user notifications.
  - `POST /api/v1/notifications/{notification_id}/read` -> marks notification as read.

### 4.3 Multi-Channel Dispatcher in `NotificationService`
- Expands `send_notification()` to evaluate all channels in `UserPreference.channels` (e.g. `["in_app", "email", "webhook"]`).
- Implements primary delivery and channel escalation when severity is `warning`, `high`, or `danger`.

---

## 5. Acceptance Checklist & Verification Matrix

The parent task **ODP-CAP-NOTIFICATION-DELIVERY-001** can be verified against the following 6 acceptance criteria:

- [ ] **AC-1: 5 Event Triggers Handled**
  - Verify that `task_assignment`, `timeout`, `approval`, `failure`, and `rollback` triggers successfully invoke `NotificationService.send_notification()`.
- [ ] **AC-2: Email Channel Delivery**
  - Verify `EmailNotificationAdapter` sends formatted email notifications and produces valid `NotificationReceipt` records with `channel="email"` and status `sent`.
- [ ] **AC-3: In-App Inbox & API Queryability**
  - Verify `InAppNotificationAdapter` persists notifications to the inbox store and `GET /api/v1/notifications/inbox` returns unread notifications for the user.
- [ ] **AC-4: Webhook & On-Call Dispatch**
  - Verify `OnCallNotificationAdapter` correctly dispatches webhook payloads with trusted SHA and HMAC signatures.
- [ ] **AC-5: Retry & Escalation Enforcement**
  - Verify primary delivery failures trigger retries (up to `max_retries`) and escalate high-severity alerts to secondary channels.
- [ ] **AC-6: Durable Receipt Authority Verification**
  - Verify `verify_durable_delivery_authority()` succeeds for all delivered receipts.

---

## 6. Recommended Verification Commands for Parent Owner

Parent owner (`Claude`) should execute the following test suite upon completing the implementation:

```bash
# 1. Run notification module unit and integration tests
pytest -q tests/reliability/test_notifications.py

# 2. Run API route tests for in-app notification endpoints (if added)
pytest -q tests/api/test_notification_routes.py

# 3. Verify code style and formatting
ruff check modules/notifications/ apps/api/app/routes/
git diff --check
```

---

## 7. Sidecar Handoff & Summary

- **Deliverable**: Support packet and dependency map for `ODP-CAP-NOTIFICATION-DELIVERY-001`.
- **Target File**: `support/sidecars/ODP-CAP-NOTIFICATION-DELIVERY-001/ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-ACCEPTANCE.md`
- **Canonical Files Modified**: None (0 files modified outside support directory).
- **Recommended Action for Parent Owner (`Claude`)**: Absorb this acceptance packet and dependency map to guide the implementation of `EmailNotificationAdapter` and `InAppNotificationAdapter`.
