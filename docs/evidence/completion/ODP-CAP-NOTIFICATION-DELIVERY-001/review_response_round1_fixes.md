# ODP-CAP-NOTIFICATION-DELIVERY-001 — Round 1 Review Response

- Owner: Claude (helper-claimed from Antigravity4)
- Reviewer: Claude3
- Responds to: `docs/evidence/completion/ODP-CAP-NOTIFICATION-DELIVERY-001/review_findings_claude3_round1.md`
- Reviewed head (round 1): `2ab1ad6480f841c0d27c38d3dbc352ec56b6c38d`
- Date: 2026-08-06

All four blocking findings are fixed, each with a regression test, and the branch
has been advanced onto the current `origin/dev`.

## Base Advance

`task/ODP-CAP-NOTIFICATION-DELIVERY-001` was 105 commits behind `origin/dev` at
review time. Merged `origin/dev` (`71d44d03`) into the task branch — merge commit
`5e444313`, no conflicts. Task history was preserved (merge, not rebase/force-push),
per the branch-protection and no-force-push rules.

## B1 — Production fail-closed adapter gate is bypassed

`modules/notifications/infrastructure/adapters.py`

The `email` / `in_app` / `multi` early-returns now sit **below** the `is_prod`
evaluation in `get_notification_adapter`, inside an explicit `if is_prod: ... else: ...`
split. In production every channel route must be genuinely configured or the
factory raises:

- `email` → `_build_prod_email_adapter()` raises `ValueError` unless a real
  `SMTP_HOST` / `EMAIL_SMTP_HOST` is set.
- `in_app` → `_build_prod_inapp_adapter()` raises `ValueError` without a durable
  repository (the in-process inbox does not survive restart).
- `multi` → the composite's `default_adapter` is the on-call adapter, never
  `ConsoleNotificationAdapter`; it is built through the same fail-closed endpoint
  validation, and its `email` / `in_app` children go through the two builders above.
- `console` in production still raises, and the pre-existing on-call path is
  unchanged (`_build_oncall_adapter` is the extracted original validation).

The mock SMTP branch is now unreachable in production from two directions:
`EmailNotificationAdapter.send` fails closed when there is no `smtp_host`, the
transport is the built-in one, and the environment is production; and
`_default_smtp_transport` itself returns `(False, ...)` instead of printing
`[MOCK EMAIL DELIVERY]` under a production env. An explicitly injected
`smtp_transport` is untouched — that is a real transport, not the mock.

Reviewer's probes, re-run on the fixed head:

```
$ APP_ENV=production NOTIFICATION_ADAPTER_TYPE=multi ...
multi in prod -> ValueError: Production mode or on-call route requires a configured valid ONCALL_ENDPOINT_URL. Fail-closed gate enforced.

$ APP_ENV=production NOTIFICATION_ADAPTER_TYPE=email ...
email in prod -> ValueError: Production mode email notification route requires a configured SMTP_HOST (mock stdout delivery would report unsent mail as sent). Fail-closed gate enforced.
```

Tests: `test_production_gate_rejects_unconfigured_channel_adapters` (parametrized over
`email`, `in_app`, `inapp`, `in-app`, `multi`, `composite`),
`test_production_multi_adapter_never_defaults_to_console`,
`test_production_email_route_requires_real_smtp_host`,
`test_email_adapter_mock_transport_fails_closed_in_production`,
`test_non_production_channel_adapters_still_resolve` (guards that dev behaviour is unchanged).

**One deliberate behaviour change beyond the finding:** the unknown-adapter-type
`ValueError` moved to the top of the factory. Previously an unknown
`NOTIFICATION_ADAPTER_TYPE` was silently ignored whenever `require_oncall` was true;
it now always raises. That is the fail-closed reading of the same gate.

## B2 — `hash()` in the failure dedup key is not stable across processes

`modules/notifications/application/service.py`, `send_failure_notification`:

```python
error_digest = hashlib.sha256((error_message or "").encode("utf-8")).hexdigest()[:16]
...
dedup_key=f"task_failed:{task_id}:{error_digest}",
```

Three runs under `PYTHONHASHSEED` 0 / 1 / 2 now produce the identical key
`task_failed:T-1:81f52337ebb4cb16`.

Test: `test_failure_dedup_key_is_process_stable` — asserts the in-process key equals
the key produced by three fresh interpreters under different `PYTHONHASHSEED` values,
and that a different error text still yields a different key.

## B3 — Severity never reaches the in-app inbox

`severity: str = "info"` is now part of the `NotificationAdapter` protocol and of
every adapter's `send` (`Console`, `OnCall`, `Email`, `InApp`, `MultiChannel`,
`Mock`). `NotificationService._send_with_retries` takes the severity through from
`send_notification` and passes it to the adapter, so
`InAppNotificationAdapter.send` stores the caller's severity instead of a hardcoded
`"info"`. `MultiChannelNotificationAdapter` forwards it to the resolved child adapter.

`OnCallNotificationAdapter` accepts the argument but deliberately keeps it out of the
signed webhook payload — that request contract was declared out of scope and is unchanged.

Compatibility: `adapter_accepts_severity()` inspects the adapter's signature, so a
duck-typed adapter still on the old five-argument form keeps working (it just does not
receive severity). The result is cached on the `NotificationService.adapter` setter, so
swapping the adapter at runtime cannot leave a stale answer.

Observed after the fix:

```
[Task Failed] Task T-1 executi -> severity= danger
[Rollback Executed] Task T-2 r -> severity= danger
danger filter -> 2
```

Tests: `test_trigger_severity_propagates_to_inapp_inbox` (asserts all five spec
triggers land with their real severity and that the `severity=` inbox filter
partitions them 2 danger / 1 warning / 2 info),
`test_multi_channel_adapter_forwards_severity`,
`test_service_tolerates_adapter_without_severity_parameter`.

## B4 — Durable `acknowledge_inapp_item` always returns `True`

`modules/notifications/infrastructure/repositories.py` now returns
`int(getattr(result, "rowcount", -1)) > 0` and no longer swallows exceptions into a
bare `False`. Both engines support this: `SqliteEngine.execute` returns a
`sqlite3.Cursor` and `PostgreSqlEngine.execute` returns `ExecutionResult(rowcount=...)`.
The user scoping stays in the `WHERE` clause.

Observed after the fix:

```
ack nonexistent -> False
bob acks alice item -> False
alice inbox acked -> False
alice acks own -> True
```

Tests: `test_durable_inapp_notification_flow` (durable mirror of
`test_inapp_notification_adapter_flow`, including the durable severity filter),
`test_durable_acknowledge_reports_false_for_unmatched_rows`,
`test_durable_and_in_memory_acknowledge_agree` (parity between the two
implementations on the same contract).

## Non-Blocking Findings

Addressed:

- `EmailNotificationAdapter.trusted_release_sha` is now recorded on the delivery
  receipt as `release_sha` (`"unbound"` when absent) instead of being read and dropped.
- `request_hash` carries a comment stating it is an audit field that nothing verifies.
- `MultiChannelNotificationAdapter.send`: the unreachable-by-construction `email` and
  `in_app` re-lookups are collapsed; only the `inapp` and `webhook`/`oncall` aliases plus
  the default remain.
- `delivery_receipts` / `inbox_items` / `sent_messages` are capped at
  `_MAX_IN_PROCESS_RECORDS = 500` via `_append_capped`.
- Local `import os` / `uuid` / `hashlib` / `json` in `__init__` and `send` bodies hoisted
  to module level, including the stray mid-file `import os`.
- Trailing blank-line churn at EOF removed from `__init__.py`, `service.py`,
  `adapters.py`, `repositories.py`, and both migrations.

## Verification

All run on the post-base-advance head.

```bash
/home/lupin/.local/bin/uv run pytest tests/reliability/test_notifications.py -q
# 27 passed (was 10)

/home/lupin/.local/bin/uv run pytest tests/reliability/test_runtime_observability.py -q
# 71 passed — the on-call adapter and factory contract are unregressed

/home/lupin/.local/bin/uv run pytest \
  tests/integration/test_operator_shell_persistence.py \
  tests/integration/test_operator_live_repository.py \
  tests/integration/test_operator_live_domain_modules.py \
  tests/contract/test_operator_shell_api.py \
  tests/security/test_operator_shell_security.py \
  tests/test_scaffold.py -q
# 89 passed — every other suite that touches notifications
```

The reviewer's four reproduction probes were also re-run verbatim against this
worktree; their output is quoted under each finding above.
