# ODP-CAP-NOTIFICATION-DELIVERY-001 — Review Findings (Claude3, round 1)

- Reviewer: Claude3 (helper-claimed; reviewer of record Antigravity2 dispatch-paused)
- Owner: Antigravity4
- Reviewed head: `2ab1ad6480f841c0d27c38d3dbc352ec56b6c38d`
- Merge base with `origin/dev`: `git merge-base origin/dev HEAD`
- Decision: **REOPEN** (send back to `in_progress`)
- Date: 2026-08-06

## Scope Reviewed

```
infra/db/migrations/000005_durable_notifications.sql          |  12 +
infra/db/migrations/000008_postgresql_runtime_persistence.sql |  14 +
modules/notifications/__init__.py                             |   7 +
modules/notifications/application/service.py                  | 112 ++++
modules/notifications/infrastructure/__init__.py              |   7 +
modules/notifications/infrastructure/adapters.py              | 287 +++++-
modules/notifications/infrastructure/repositories.py          | 105 ++++
tests/reliability/test_notifications.py                       | 162 ++++
```

## Verification Run

```bash
/home/lupin/.local/bin/uv run pytest tests/reliability/test_notifications.py -q
# 10 passed  -> the Verified: trailer on 2ab1ad64 is accurate
```

Reproduction probes for each finding below were run against this worktree
with `python3 -c ...` one-liners; the observed output is quoted inline.

## What Is Correct

- FR-SHARED-006 gap is genuinely closed at the type level: `EmailNotificationAdapter`,
  `InAppNotificationAdapter`, `MultiChannelNotificationAdapter` are added and exported,
  and the webhook `OnCallNotificationAdapter` contract is left untouched as declared.
- The five spec triggers exist as named `NotificationService` helpers and are covered
  by `test_five_spec_triggers_flow`; the six canonical roles are covered by
  `test_six_canonical_roles_delivery`.
- `EmailNotificationAdapter` is transport-injectable (`smtp_transport`), so the SMTP
  path is testable without a live mail server. Good boundary.
- Editing `000005` / `000008` in place is safe here (not a finding): `SqliteEngine._bootstrap`
  re-executes every listed migration verbatim on each boot, and
  `deployment_runtime` re-executes `000008` on each deployment, both with
  `CREATE TABLE IF NOT EXISTS`. Existing databases do pick up the new table.

## Blocking Findings

### B1 — Production fail-closed adapter gate is bypassed (regression)

`modules/notifications/infrastructure/adapters.py`, `get_notification_adapter`.

The new `adapter_type` early-returns for `email` / `in_app` / `multi` are placed
**above** the `is_prod` / `require_oncall` block, so they return before the
pre-existing production gate can run. That gate is the one whose own docstring
says "ConsoleNotificationAdapter is strictly forbidden in production environments"
and which raises `ValueError` when a prod deployment has no valid `ONCALL_ENDPOINT_URL`.

Observed:

```
$ APP_ENV=production NOTIFICATION_ADAPTER_TYPE=multi python3 -c "..."
multi in prod -> MultiChannelNotificationAdapter default= ConsoleNotificationAdapter

$ APP_ENV=production NOTIFICATION_ADAPTER_TYPE=email python3 -c "..."
email in prod -> EmailNotificationAdapter smtp_host= None
[MOCK EMAIL DELIVERY] Sent email to ops@x.com
send -> (True, None)
```

Two distinct failures:

1. `multi` in production yields a composite whose `default_adapter` is exactly the
   `ConsoleNotificationAdapter` the gate forbids.
2. `email` in production with no `SMTP_HOST` falls into `_default_smtp_transport`'s
   mock branch, prints to stdout, and returns `(True, None)`. The service then writes
   a `sent` receipt. Production reports successful delivery for mail that was never sent —
   silent data loss on the UAT signoff path that `ODP-PLAN-UAT-SIGNOFF-001` depends on.

`REQUIRE_EMAIL_ROUTE` does not mitigate this: it only fires when an operator has
explicitly opted in, which is fail-open by default, the inverse of the gate it sits next to.

Required: move the new adapter-type branches below the `is_prod` / `require_oncall`
evaluation, make the mock SMTP branch unreachable when `is_prod`, and add a test that
asserts `APP_ENV=production` with `NOTIFICATION_ADAPTER_TYPE` in `{email, in_app, multi}`
either raises or returns a genuinely-configured transport.

### B2 — `hash()` in the failure dedup key is not stable across processes

`modules/notifications/application/service.py`, `send_failure_notification`:

```python
dedup_key=f"task_failed:{task_id}:{hash(error_message or '')}"
```

Python randomizes `str` hashing per interpreter process unless `PYTHONHASHSEED` is pinned.
Three consecutive runs on the same input:

```
4604483491635200728
-7072918555633207099
6412839253414557351
```

Deduplication is backed by a durable repository, so the whole point is suppression
across restarts and across worker processes. With a per-process random component the
same failure re-notifies on every restart and every parallel worker, and the stored
dedup rows accumulate unbounded. This is the one trigger of the five that carries a
non-deterministic key.

Required: use a stable digest (`hashlib.sha256(...).hexdigest()[:16]`) or drop the
error text from the key entirely.

### B3 — Severity never reaches the in-app inbox

`InAppNotificationAdapter.send` hardcodes `"severity": "info"`. The `NotificationAdapter`
protocol has no `severity` parameter, so the `severity="danger"` that
`send_failure_notification` and `send_rollback_notification` pass to `send_notification`
is used only for the escalation branch and is then dropped.

Observed:

```
[Task Failed] Task T-1 executi -> severity= info
[Rollback Executed] Task T-2 r -> severity= info
danger filter -> 0
```

Consequence: the new `severity` column in both migrations, and the `severity=` filter on
`get_inbox` / `get_inapp_items`, are dead — they can only ever hold or match `'info'`.
An operator inbox cannot distinguish a rollback from a task assignment, which is the
main thing the in-app channel was added for. `test_five_spec_triggers_flow` asserts on
titles only, so it passes over this.

Required: thread severity through to the adapter (extend the `send` signature or pass the
item through the repository from the service), and assert non-`info` severity in the trigger test.

### B4 — Durable `acknowledge_inapp_item` always returns `True`

`DurableNotificationRepository.acknowledge_inapp_item` returns `True` whenever the
`UPDATE` does not raise, without consulting `rowcount`. The in-memory sibling returns
whether a row actually matched, so the two implementations disagree on the same contract —
and the durable one is what production uses.

Observed against a real `SqliteEngine`:

```
ack nonexistent -> True
bob acks alice item -> True
alice inbox -> [... 'acknowledged': False ...]
```

A caller acknowledging a notification that does not exist, or that belongs to another user,
gets `True` back while nothing changed. Any API layer built on this will report success on
a cross-user acknowledge attempt — both a correctness bug and an authorization-signal bug.

Also note the durable in-app path (`save_inapp_item` / `get_inapp_items` /
`acknowledge_inapp_item` on `DurableNotificationRepository`) has **no test coverage** at all;
all four new tests exercise only `InMemoryNotificationRepository`.

Required: return `rowcount > 0`, keep the user scoping in the `WHERE` clause, stop swallowing
the exception into a bare `False`, and add a durable-path test mirroring
`test_inapp_notification_adapter_flow`.

## Non-Blocking Findings

- `EmailNotificationAdapter.trusted_release_sha` is read from three env vars and stored,
  then never used anywhere. Either wire it into the receipt or drop it.
- `EmailNotificationAdapter.send` computes `request_hash` and stores it on the receipt,
  but nothing ever verifies it. Fine as an audit field; worth a comment saying so.
- `MultiChannelNotificationAdapter.send`: inside `if not adapter:`, the branches
  `self.channel_adapters.get("email", self.default_adapter)` and the `in_app` equivalent
  are unreachable-by-construction — `channel_adapters.get(norm_channel)` already returned
  `None` for that exact key. Collapse to the `webhook`/`oncall` aliasing plus a default.
- `self.delivery_receipts` and `self.inbox_items` are unbounded in-process lists. In a
  long-lived worker these grow without limit. Cap them or make them debug-only.
- Local imports (`import os`, `import uuid`, `import hashlib`, `import json`) inside
  `__init__` and `send` bodies; the module already imports at top level elsewhere.
- Trailing blank-line churn added at EOF in `__init__.py`, `service.py`, `adapters.py`,
  `repositories.py`, and both migrations. Harmless, but it is unrelated diff noise.

## Base State

`task/ODP-CAP-NOTIFICATION-DELIVERY-001` is 105 commits behind `origin/dev` at review time.
The owner must rebase/compose onto the current base and re-verify before this can merge,
independent of the findings above.

## Required For Re-Review

1. Fix B1, B2, B3, B4.
2. Add the regression tests named in B1, B3, B4 (prod gate, severity propagation,
   durable in-app path).
3. Rebase onto current `origin/dev` and re-run
   `uv run pytest tests/reliability/test_notifications.py`.
4. Hand back to review with the new head SHA.
