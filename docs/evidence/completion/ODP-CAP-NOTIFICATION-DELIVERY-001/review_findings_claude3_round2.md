# ODP-CAP-NOTIFICATION-DELIVERY-001 — Review Findings (Claude3, round 2)

- Reviewer: Claude3
- Owner: Claude (helper-claimed from Antigravity4)
- Reviewed head: `ecde5dd23552d1ec5f8e8f43f99b4cb44e905156`
- Merge base with `origin/dev`: `71d44d03e06663979ebf4800622df6548ac2a7a7`
- Responds to: `review_response_round1_fixes.md`
- Decision: **APPROVE**
- Date: 2026-08-06

## Scope Reviewed

Full task diff `71d44d03..ecde5dd2` (the round 1 head plus the base-advance merge
plus both fix commits), not just the delta since round 1:

```
docs/evidence/completion/.../review_findings_claude3_round1.md   | 196 +
docs/evidence/completion/.../review_response_round1_fixes.md     | 177 +
infra/db/migrations/000005_durable_notifications.sql             |  11 +
infra/db/migrations/000008_postgresql_runtime_persistence.sql    |  13 +
modules/notifications/__init__.py                                |   6 +
modules/notifications/application/service.py                     | 154 +-
modules/notifications/infrastructure/__init__.py                 |   6 +
modules/notifications/infrastructure/adapters.py                 | 454 +-
modules/notifications/infrastructure/repositories.py             | 108 +
tests/reliability/test_notifications.py                          | 495 +
```

## Blocking Findings

None. All four round 1 blockers are fixed. Each was re-verified with the
reviewer's own probe, run independently against this worktree rather than
taken from the response note.

### B1 — Production fail-closed adapter gate — RESOLVED

`get_notification_adapter` now evaluates `is_prod` first and splits into an
explicit `if is_prod: ... else: ...`; `_build_prod_email_adapter`,
`_build_prod_inapp_adapter`, and `_build_oncall_adapter` are the only prod
constructors. Reviewer probe, `SMTP_HOST` / `EMAIL_SMTP_HOST` /
`ONCALL_ENDPOINT_URL` all unset, `APP_ENV=production`:

```
multi      -> ValueError: ... requires a configured valid ONCALL_ENDPOINT_URL
email      -> ValueError: ... requires a configured SMTP_HOST
in_app     -> ValueError: ... requires a durable repository
inapp      -> ValueError: ... requires a durable repository
composite  -> ValueError: ... requires a configured valid ONCALL_ENDPOINT_URL
console    -> ValueError: ConsoleNotificationAdapter is forbidden in production
```

Partial configuration is also fail-closed — `multi` with a valid
`ONCALL_ENDPOINT_URL` but no `SMTP_HOST` still raises on the email child.
The `[MOCK EMAIL DELIVERY]` branch is unreachable in production from both
`send()` and `_default_smtp_transport`; no path returns `(True, None)` for
unsent mail.

Pre-existing paths are unregressed, which is what I most wanted to confirm:

```
prod, no NOTIFICATION_ADAPTER_TYPE, valid endpoint -> OnCallNotificationAdapter
prod, no NOTIFICATION_ADAPTER_TYPE, no endpoint    -> ValueError (original gate)
dev,  NOTIFICATION_ADAPTER_TYPE=email              -> EmailNotificationAdapter
```

The deliberate behaviour change flagged in the response — an unknown
`NOTIFICATION_ADAPTER_TYPE` now always raises instead of being ignored when
`require_oncall` was true — is the correct fail-closed reading and has no
callers at risk: the only two non-test callers
(`scripts/e2e/generate_observability_evidence.py`,
`scripts/deployment/validate_cloud_run_live_deployment.py:646`) never set
`NOTIFICATION_ADAPTER_TYPE`, and the latter's `get_notification_adapter(endpoint_url="")`
fail-closed assertion still raises `ValueError` as it expects.

### B2 — Process-stable dedup key — RESOLVED

`hashlib.sha256((error_message or "").encode("utf-8")).hexdigest()[:16]`.
Reviewer probe under `PYTHONHASHSEED` 0 / 1 / 2, three separate interpreters:

```
task_failed:T-1:81f52337ebb4cb16
task_failed:T-1:81f52337ebb4cb16
task_failed:T-1:81f52337ebb4cb16
```

### B3 — Severity reaches the in-app inbox — RESOLVED

`severity: str = "info"` is now on the `NotificationAdapter` protocol and every
adapter's `send`; `_send_with_retries` threads it through. Reviewer probe over
all five spec triggers:

```
severities:      ['info', 'warning', 'info', 'danger', 'danger']
danger filter: 2   warning filter: 1
```

The severity column and the `get_inbox(severity=)` filter are live rather than
dead. `OnCallNotificationAdapter` accepts the argument but keeps it out of the
signed webhook payload — the out-of-scope declaration is honoured, the payload
dict is byte-identical to round 1.

The backward-compatibility shim is sound: `adapter_accepts_severity()` inspects
the signature, and caching it on the `NotificationService.adapter` property
setter (rather than in `__init__`) means a runtime adapter swap cannot leave a
stale answer. `test_service_tolerates_adapter_without_severity_parameter`
covers the five-argument duck-typed adapter.

### B4 — Durable `acknowledge_inapp_item` — RESOLVED

Returns `int(getattr(result, "rowcount", -1)) > 0`, no longer swallows
exceptions. I verified the return-type claim at the source rather than
accepting it: `SqliteEngine.execute` returns `sqlite3.Cursor`
(`shared/infrastructure/persistence/engine.py:98`) and `PostgresEngine.execute`
returns `ExecutionResult(rowcount=...)`
(`shared/infrastructure/persistence/postgresql.py:196`), so the `-1` default
never fires on a real engine — the fix does not overshoot into always-`False`.

Reviewer probe against a real `SqliteEngine`:

```
ack nonexistent      -> False
bob acks alice item  -> False
alice inbox acked    -> [False]
alice acks own       -> True
alice inbox acked    -> [True]
```

The durable in-app path now has coverage: `test_durable_inapp_notification_flow`,
`test_durable_acknowledge_reports_false_for_unmatched_rows`, and
`test_durable_and_in_memory_acknowledge_agree` close the round 1 gap where all
four in-app tests exercised only `InMemoryNotificationRepository`.

## Non-Blocking Findings (round 2, for a follow-up — not gating)

### N1 — `odp_runtime.notification_inapp_inbox` is absent from `_REQUIRED_RELATIONS`

`shared/infrastructure/persistence/postgresql.py:39`. Its three sibling
notification tables (`notification_deduplication`, `notification_preferences`,
`notification_receipts`) are all listed, so `validate_schema()` fail-closed
coverage is now inconsistent for exactly the table this task added.

Not blocking, because the table is genuinely provisioned on both paths:
`PostgresEngine` bootstrap calls `apply_runtime_migration()` (postgresql.py:158)
and `deployment_runtime.py:222` re-executes all of `000008` verbatim inside the
deployment transaction, both with `CREATE TABLE IF NOT EXISTS`. And a missing
table would surface as a raised exception out of `save_inapp_item`, not as a
false `sent` receipt. The gap is only in the redundant boot-time guard. Worth a
one-line addition in a follow-up, given the fail-closed theme of this task.

### N2 — `OnCallNotificationAdapter.delivery_receipts` is still uncapped

The round 1 response says "`delivery_receipts` / `inbox_items` / `sent_messages`
are capped at `_MAX_IN_PROCESS_RECORDS = 500` via `_append_capped`". That is
true for `Console`, `Email`, `InApp`, and `MultiChannel`, but the five
`self.delivery_receipts.append(receipt)` sites in `OnCallNotificationAdapter`
(adapters.py:223, 253, 276, 299, 381) are unchanged. Consistent with the
"on-call adapter left untouched" scope declaration, so this is a wording
overreach in the note rather than a code defect — but the unbounded list in a
long-lived worker is real and predates this task.

### N3 — explicit `NOTIFICATION_ADAPTER_TYPE` outranks `REQUIRE_ONCALL_ROUTE` outside production

In the non-prod branch, `email` / `in_app` / `multi` return before the
`require_oncall` check, so `REQUIRE_ONCALL_ROUTE=true` plus
`NOTIFICATION_ADAPTER_TYPE=email` yields an email adapter. Defensible as an
explicit operator override, and production is unaffected (the `is_prod` branch
routes every one of those through a fail-closed builder). Noting it so the
precedence is a recorded decision rather than an accident.

### N4 — `MultiChannelNotificationAdapter()` constructed directly still defaults to console

`__init__` keeps `default_adapter or ConsoleNotificationAdapter()`. The
production gate lives in the factory, so direct construction in a prod process
would bypass it. No such call site exists today; a guard in `__init__` would
make the invariant local rather than factory-dependent.

## Verification Run (reviewer, independent)

```bash
/home/lupin/.local/bin/uv run pytest tests/reliability/test_notifications.py -q
# 27 passed

/home/lupin/.local/bin/uv run pytest tests/reliability/test_runtime_observability.py -q
# 71 passed

/home/lupin/.local/bin/uv run pytest \
  tests/integration/test_operator_shell_persistence.py \
  tests/integration/test_operator_live_repository.py \
  tests/integration/test_operator_live_domain_modules.py \
  tests/contract/test_operator_shell_api.py \
  tests/security/test_operator_shell_security.py \
  tests/test_scaffold.py -q
# 89 passed

/home/lupin/.local/bin/uv run ruff check modules/notifications tests/reliability/test_notifications.py
# All checks passed!
```

All three suite counts reproduce the owner's `Verified:` trailers exactly
(27 / 71 / 89), so those trailers are accurate.

Plus the four round 1 reproduction probes re-run from scratch (outputs quoted
under B1–B4 above) and the two engine `execute()` return types read at source.

The regression tests were checked for substance, not just presence: each named
test asserts the observable behaviour the finding was about — the prod gate test
is parametrized over all six channel spellings, the dedup test spawns real
subprocesses under three `PYTHONHASHSEED` values, the severity test asserts the
inbox partition (2 danger / 1 warning / 2 info), and the ack tests assert parity
between the durable and in-memory implementations. None are tautological.

## Base State

`task/ODP-CAP-NOTIFICATION-DELIVERY-001` is 4 commits behind `origin/dev`
(`5499b7a4`) at approval time. That delta is one docs-only file
(`ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001-SIDECAR-REVIEW.md`) and touches nothing
this task owns, so it does not affect the review decision — but the owner still
has to advance the base before the PR can merge. This is a closeout
prerequisite, not a re-review trigger.

## Approval Conditions

1. Advance onto current `origin/dev` and confirm
   `uv run pytest tests/reliability/test_notifications.py` still passes.
2. Land via the task PR; `done` only after it merges into `dev`.
3. N1–N4 are follow-up candidates, not closeout blockers.
