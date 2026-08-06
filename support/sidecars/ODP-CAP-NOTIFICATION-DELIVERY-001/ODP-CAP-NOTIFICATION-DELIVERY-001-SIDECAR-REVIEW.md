# Sidecar Review Packet: ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW

- **Task ID**: `ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-CAP-NOTIFICATION-DELIVERY-001` (owner `Claude`, reviewer `Claude3`, status `in_progress`)
- **Helper Kind**: `review_packet`
- **Owner**: `Claude2`
- **Reviewer**: `Antigravity4`
- **Packet Revision**: **round 2 (2026-08-06)** — re-derived against parent head `c73a6710`; round 1 was derived against `2ab1ad64`
- **Target Artifact**: `support/sidecars/ODP-CAP-NOTIFICATION-DELIVERY-001/ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW.md`

### Parent Pin (read this before trusting any number below)

| Field | Round 1 pin | **Round 2 pin (current)** |
|---|---|---|
| Parent branch | `task/ODP-CAP-NOTIFICATION-DELIVERY-001` (local only) | `task/ODP-CAP-NOTIFICATION-DELIVERY-001` — **now on `origin`** |
| Parent head | `2ab1ad64` — *anchor notification delivery handlers and triggers* | **`c73a6710`** — *fix round 1 review findings B1–B4* |
| Parent `review_gate_sha` | — | `c73a6710` (matches head) |
| Merge-base with `origin/dev` | `77567b5e` | `71d44d03` |
| Landed on `dev`? | No | **Still no.** `git merge-base --is-ancestor c73a6710 origin/dev` → false; `gh pr list --head task/ODP-CAP-NOTIFICATION-DELIVERY-001 --state all` → `[]` |
| Deliverable surface | 8 files, 703 ins / 3 del | **10 files, 1577 ins / 29 del** (see §7.2) |
| `dev` at time of review | `71d44d03` | `5499b7a4` |

> [!IMPORTANT]
> **§2–§5 below are time-scoped to `2ab1ad64` and are superseded where §7 says
> so.** The parent advanced two commits after round 1 (`048dad9a` record round 1
> findings, `c73a6710` fix B1–B4) and that commit rewrote 237 lines of
> `adapters.py`. Read **§7 first**: it re-derives every round-1 claim against
> `c73a6710` and is the standing verdict. Round-1 line numbers and "does not
> exist" statements apply only to `2ab1ad64`.

> [!WARNING]
> **Round 1's two blocking findings are resolved at `c73a6710`.** F1 (production
> factory bypass) and F2 (email mock-delivery reporting `SENT`) no longer
> reproduce — see §7.3 for the probe transcript. **No blocking finding remains.**
> F3, F6 and F7 still stand; F4, F5 and F8 are also resolved.

---

## Core Notice & Scope Boundary

> [!NOTE]
> This sidecar task is support-only. It creates only support artifacts under
> `support/sidecars/ODP-CAP-NOTIFICATION-DELIVERY-001/` and modifies **no** L1
> canonical document, contract truth, runtime, registry, or governance
> implementation. Nothing under `modules/`, `infra/`, `apps/`, `scripts/`, or
> `tests/` is touched by this branch. All verification was performed read-only
> against a throwaway detached worktree at `2ab1ad64`.

---

## 1. Capability Gap Being Closed

### Spec source

`ODP-FR-SHARED-006` requires 站內 (in-app), Email, and Webhook notification
channels, and requires that 任務指派、逾時、核准、失敗、回滾 all notify the
relevant Actor.

The gap was registered twice in canonical evidence at `dev`:

| Doc | Ref | Statement |
|---|---|---|
| `docs/evidence/ODAY_PLUS_CONSOLIDATED_GAP_AUDIT_2026-08-03.md` L275 | **U-4** | "僅 console + on-call webhook；全 repo 搜尋 `smtp`/`sendgrid`/`ses` 零命中" |
| `docs/design/ODAY_PLUS_UNOWNED_CAPABILITY_SCOPE_DECISION_2026-08-03.md` §U-4 (L65–71) | **U-4, option A, P1** | "`channels` 預設 `["email"]`、`channel = "email"` — 欄位存在但沒有實際投遞實作" |

The scope decision also records the downstream dependency: `ODP-PLAN-UAT-SIGNOFF-001`
needs six roles to *actually receive* task-assignment notifications, otherwise
the six-role signoff cannot be executed for real.

So the parent task is correctly framed: **the data model already had `channels`;
what was missing was delivery.** The parent does not change the data model, and
that is the right call.

### What "closing U-4" requires

| # | Requirement | Source |
|---|---|---|
| R1 | An Email delivery path (not console) | U-4 現況 row |
| R2 | An in-app (站內) delivery path with a durable store | `ODP-FR-SHARED-006` |
| R3 | Webhook preserved | already shipped as `OnCallNotificationAdapter` |
| R4 | Five triggers: assigned, timeout, approval, failure, rollback | `ODP-FR-SHARED-006` |
| R5 | Six canonical roles can each receive a delivery | UAT signoff dependency |
| R6 | The pre-existing production fail-closed posture is not weakened | `get_notification_adapter()` docstring, `validate_cloud_run_live_deployment.py` |

R1–R5 are addressed by the parent. **R6 is regressed** — see §5 F1.

---

## 2. Delivered Surface @ `2ab1ad64`

```
 infra/db/migrations/000005_durable_notifications.sql            +12   (SQLite  in-app inbox table)
 infra/db/migrations/000008_postgresql_runtime_persistence.sql   +14   (Postgres in-app inbox table + index)
 modules/notifications/__init__.py                                +7   (re-exports)
 modules/notifications/infrastructure/__init__.py                 +7   (re-exports)
 modules/notifications/application/service.py                   +112   (5 trigger helpers)
 modules/notifications/infrastructure/adapters.py           +284  -3   (3 new adapters + factory branches)
 modules/notifications/infrastructure/repositories.py           +105   (in-app inbox persistence, both repos)
 tests/reliability/test_notifications.py                        +162   (6 new tests)
                                                          ────────────
                                                            703  -3    across 8 files
```

No generated state mirrors are present in this range — the whole diff is real
deliverable code. That is cleaner than the sidecar-lane norm and worth noting.

### Layer map

```
┌──────────────────────────────────────────────────────────────────────────────┐
│      Notification delivery — delivered surface @ 2ab1ad64                     │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  NotificationService (service.py)                                            │
│    send_task_assigned / timeout / approval / failure / rollback   ← NEW L171+ │
│         └─► send_notification()  ── preference → dedup → channels[0]         │
│                     └─► _send_with_retries()  ── max_retries, then escalate   │
│                                 │                                            │
│                                 ▼   NotificationAdapter Protocol             │
│                                     send(nid, channel, user, title, detail)  │
│      ┌──────────────┬──────────────┬───────────────┬─────────────────┐        │
│      │ Console      │ OnCall       │ Email      NEW│ InApp        NEW│  Multi │
│      │ (pre-exist)  │ (webhook,    │ L358          │ L494            │  NEW   │
│      │              │  pre-exist)  │ SMTP/custom   │ inbox store     │  L580  │
│      └──────────────┴──────────────┴───────────────┴─────────────────┘        │
│                                        │                    │                 │
│                                        ▼                    ▼                 │
│                          delivery_receipts[]    notification_inapp_inbox  NEW  │
│                                                 (SQLite 000005 / PG 000008)   │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Delivered modules

**1. `EmailNotificationAdapter`** — `adapters.py` L358–491

- Env-or-arg SMTP config: `SMTP_HOST`/`EMAIL_SMTP_HOST`, `SMTP_PORT` (default `587`),
  `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL` (default `notifications@oday.plus`),
  `SMTP_USE_TLS` (default true).
- `_default_smtp_transport()` (L395) uses stdlib `smtplib` + `MIMEText`, with
  `starttls()` and optional login. Injectable via `smtp_transport=`.
- Emits a `delivery_receipts` entry per send: `delivery_id`, `request_hash`
  (SHA-256 over the sorted-key payload), `status` ∈ {`SENT`,`FAILED`}, `error`.
- Fail-closed gate at L441–443: `REQUIRE_EMAIL_ROUTE=1|true` + no `smtp_host` →
  hard fail. **Opt-in only** — see §5 F2.

**2. `InAppNotificationAdapter`** — `adapters.py` L494–578

- `send()` writes an inbox item and returns `(True, None)`.
- Persists via `repository.save_inapp_item()` when the repository supports it
  (duck-typed `hasattr`), else falls back to an in-process `inbox_items` list.
- Read/ack surface: `get_inbox(user_id, severity, acknowledged)` and
  `acknowledge_notification(user_id, notification_id)`, both delegating to the
  repository when available.

**3. `MultiChannelNotificationAdapter`** — `adapters.py` L580–623

- Composite router (`send()` at L594): `register_adapter(channel, adapter)` normalises
  `channel.lower().replace("-", "_")`; `send()` resolves `email` / `in_app` /
  `webhook`, aliasing `inapp`→`in_app` and `oncall`→`webhook`, else
  `default_adapter`.
- Mirrors the delegate's last receipt into its own `delivery_receipts`.

**4. Factory branches** — `get_notification_adapter()` L626–681

New early returns for `NOTIFICATION_ADAPTER_TYPE` ∈ {`email`}, {`in_app`,`inapp`,`in-app`},
{`multi`,`composite`} (L653–665), plus a new `repository=` parameter and a widened
unknown-type allowlist (L680).

**5. Repository in-app persistence** — `repositories.py`

| Repo | Methods | Lines |
|---|---|---|
| `InMemoryNotificationRepository` | `save_inapp_item`, `get_inapp_items`, `acknowledge_inapp_item` | L41–69 |
| `DurableNotificationRepository` | same three, SQL-backed with `ON CONFLICT(notification_id) DO UPDATE` | L170–243 |

**6. Five trigger helpers** — `service.py` L171–281

Each builds a `[Tag] …` title plus a field-per-line detail body and calls
`send_notification()` with a fixed severity and dedup key:

| Trigger | Line | Severity | Dedup key |
|---|---|---|---|
| assigned | L171 | `info` | `task_assigned:{task_id}:{user_id}` |
| timeout | L195 | `warning` | `task_timeout:{task_id}:{int(timeout_seconds)}` |
| approval | L217 | `info` | `task_approved:{task_id}:{approver_id or 'system'}` |
| failure | L240 | `danger` | `task_failed:{task_id}:{hash(error_message or '')}` ← **see F4** |
| rollback | L261 | `danger` | `task_rollback:{task_id}:{rollback_target or 'default'}` |

**7. In-app inbox schema** — both migrations add the same shape:

`notification_id` (PK), `user_id`, `title`, `detail`, `severity` (default `'info'`),
`created_at`, `acknowledged` (default 0), `acknowledged_at`. The Postgres variant
adds `CHECK (acknowledged IN (0,1))` and `idx_runtime_notification_inapp_user`.

> [!NOTE]
> **Migration hygiene — checked, and it is correct.** Appending to the already-numbered
> `000005_durable_notifications.sql` looked risky, so it was verified rather than
> assumed. `SqliteEngine._bootstrap()` (`shared/infrastructure/persistence/engine.py`
> L67+) re-executes every file in `_SCHEMA_FILES` on **every** engine construction,
> and `PostgresEngine.apply_runtime_migration()` (`postgresql.py` L245) re-reads
> `000008` in full on bootstrap. Both DDL blocks use `CREATE TABLE IF NOT EXISTS`.
> No versioned-migration ledger gates these files, and the alembic tree under
> `infra/db/migrations/versions/` contains only `0001`–`0003` and does not reference
> `000005`. In-place append is therefore idempotent and reaches already-provisioned
> databases. **Not a finding.**

**8. Tests** — `tests/reliability/test_notifications.py`, 4 pre-existing + 6 new = **10**

| Test | Line | Status |
|---|---|---|
| `test_in_memory_notification_flow` | L12 | pre-existing |
| `test_notifications_deduplication` | L44 | pre-existing |
| `test_notifications_retries_and_escalation` | L59 | pre-existing |
| `test_durable_notifications_flow` | L86 | pre-existing |
| `test_email_notification_adapter_flow` | L115 | **new** |
| `test_email_notification_adapter_fail_closed` | L143 | **new** |
| `test_inapp_notification_adapter_flow` | L155 | **new** |
| `test_multi_channel_notification_adapter_flow` | L180 | **new** |
| `test_five_spec_triggers_flow` | L207 | **new** |
| `test_six_canonical_roles_delivery` | L248 | **new** |

The parent's `Verified:` trailer claims 10 passed. Reproduced — see §4.

---

## 3. Acceptance Verification Matrix

Ref IDs A1–A5 map to requirements R1–R5 of §1; A6–A8 are cross-cutting.

The **Round 1** column is the verdict at `2ab1ad64`; the **Round 2** column is
the standing verdict at `c73a6710` and is the one to act on.

| Ref | Req | Summary | Round 1 @ `2ab1ad64` | **Round 2 @ `c73a6710`** |
|---|---|---|---|---|
| **A1** | R1 | Email delivery exists over real SMTP, config-driven, with a durable receipt. | Met, with F2 caveat | **Met** — F2 caveat cleared in production (§7.3 P2) |
| **A2** | R2 | In-app delivery persists to a durable inbox with read + acknowledge. | Met, with F5 caveat | **Met** — F5 caveat cleared (§7.3 P3); `acknowledge_inapp_item` now returns `rowcount > 0` |
| **A3** | R3 | Webhook (`OnCallNotificationAdapter`) contract unchanged. | Met | **Met, with one note** — `send()` gained a `severity: str = "info"` parameter for protocol conformance, but the signed on-call payload is byte-identical (§7.4) |
| **A4** | R4 | All five spec triggers exist and each produces a delivery. | Met as API; not wired (F6) | **Met as API; still not wired** — F6 stands (§7.3 P6) |
| **A5** | R5 | Each of the six canonical roles receives its own delivery. | Met as API; not wired (F6) | **Met as API; still not wired** — F6 stands |
| **A6** | R6 | Production fail-closed posture is preserved. | **NOT MET** (F1) | **Met** — all six production factory paths raise; the `validate_cloud_run_live_deployment.py` predicate evaluates `True` (§7.3 P1, P7) |
| **A7** | — | Delivery outcome is observable by the caller. | **NOT MET** (F3) | **Still NOT MET** — F3 stands (§7.3 P1/P2) |
| **A8** | — | Clean lint / compile across the delivered surface. | Met | **Met** — `ruff` clean, `py_compile` clean, **27 passed** (was 10) |

---

## 4. Verification Suite Commands

Run from the repo root **at parent commit `2ab1ad64`**. The parent branch is
local-only and unpushed, so reproduce via a detached worktree:

```bash
# 0. Materialise the pin (throwaway; delete when done)
git worktree add --detach /tmp/odp-parent-pin-2ab1ad64 2ab1ad64
cd /tmp/odp-parent-pin-2ab1ad64

# 1. Parent test surface — 10 tests
/home/lupin/.local/bin/uv run pytest -q tests/reliability/test_notifications.py

# 2. Syntax compilation of the three changed Python modules
/home/lupin/.local/bin/uv run python -m py_compile \
  modules/notifications/application/service.py \
  modules/notifications/infrastructure/adapters.py \
  modules/notifications/infrastructure/repositories.py

# 3. Lint
/home/lupin/.local/bin/uv run python -m ruff check \
  modules/notifications/ tests/reliability/test_notifications.py

# 4. Deliverable shape
git diff --numstat 77567b5e 2ab1ad64

# 5. Cleanup
cd - && git worktree remove /tmp/odp-parent-pin-2ab1ad64
```

### Recorded results (owner run, round 1, 2026-08-06, `dev` = `71d44d03`)

| # | Command | Result |
|---|---|---|
| 1 | `pytest -q tests/reliability/test_notifications.py` | **10 passed** — matches the parent's `Verified:` trailer |
| 2 | `py_compile` on the three modules | clean |
| 3 | `ruff check modules/notifications/ tests/…` | **All checks passed!** |
| 4 | `git diff --numstat 77567b5e 2ab1ad64` | 8 files, 703 insertions, 3 deletions |
| — | `git merge-base --is-ancestor 2ab1ad64 origin/dev` | **false** — parent has not landed |
| — | `git branch -r --list '*NOTIFICATION-DELIVERY*'` | empty — parent branch is not on `origin` |

> [!NOTE]
> Reviewer reproduction notes: `ruff` and `pytest` are not on `PATH` in the task
> worktree, so commands 1–3 must be run through `uv` exactly as written. The
> first `uv run` in a fresh worktree provisions a `.venv` (234 packages, ~2 s).

### Probe results (§5 evidence)

Each finding below was reproduced with a small read-only script exercising only
public APIs at `2ab1ad64`. Probes were deleted after the run; they are
reproducible from the code blocks in §5.

| Probe | Claim | Observed |
|---|---|---|
| P1 | Undelivered send returns a non-`None` id | `nid=20a63b37-…`, sole receipt status `failed` |
| P2 | Fully-failed escalation returns a non-`None` id | `nid=8640b53f-…`, receipts `['escalated','failed']` |
| P3 | `danger` triggers land in the inbox as `info` | severities `['info','info']`; `get_inbox(severity="danger")` → `[]` |
| P4 | Only `channels[0]` is delivered on success | channels sent `['email']` for pref `["email","in_app"]` |
| P5a/b/c | Prod + `multi` yields a Console-backed webhook route that reports success | returned `MultiChannelNotificationAdapter`; webhook resolved to `ConsoleNotificationAdapter`; `send(...)` → `(True, None)` |
| P5-ctl | Same prod env without `NOTIFICATION_ADAPTER_TYPE` still fails closed | `ValueError: Production mode or on-call route requires a configured valid ONCALL_ENDPOINT_URL.` |
| P6 | Prod email with no SMTP host produces a `SENT` receipt via stdout | `ok=True`, receipt `status="SENT"`, `smtp_host=None` |
| P7 | Deployment fail-closed gate assertion | `<unset>`→ValueError, `console`→ValueError, `email`/`in_app`/`multi`→**adapter returned, no raise** |
| P8 | Failure dedup key varies per process | `PYTHONHASHSEED=1/2/3` → three different keys for identical input |

---

## 5. Findings for the Parent Reviewer

Severity: **B** = should block approval, **N** = non-blocking, record and schedule,
**Q** = scope question needing an owner decision, not a defect.

> [!IMPORTANT]
> **This section is a round-1 record, time-scoped to `2ab1ad64`.** Its line
> numbers, code excerpts and verdicts describe that commit and that commit only.
> F1, F2, F4, F5 and F8 have since been fixed at `c73a6710`; F3, F6 and F7 still
> reproduce. §7.3 carries the per-finding re-derivation. Do not quote a finding
> from this section without its §7.3 disposition.

### F1 (B) — New factory branches bypass the production fail-closed gate

`adapters.py` L653–665 inserts three early returns **before** the `require_oncall`
block at L667:

```python
    if adapter_type == "email":            # L653
        return EmailNotificationAdapter()
    if adapter_type in {"in_app", "inapp", "in-app"}:      # L656
        return InAppNotificationAdapter(repository=repository)
    if adapter_type in {"multi", "composite"}:             # L659
        multi = MultiChannelNotificationAdapter(default_adapter=ConsoleNotificationAdapter())
        ...
        return multi
    if require_oncall:                     # L667 — now unreachable for those three types
        if is_prod and adapter_type == "console":
            raise ValueError("ConsoleNotificationAdapter is forbidden in production environment. …")
```

The function's own docstring at L637 states *"ConsoleNotificationAdapter is
strictly forbidden in production environments."* The `multi` branch hard-codes
`default_adapter=ConsoleNotificationAdapter()` and only registers a `webhook`
adapter when `ONCALL_ENDPOINT_URL`/`endpoint_url` is set. So in production
without that env var, every unregistered channel — including `webhook` — routes
to `ConsoleNotificationAdapter`.

Reproduce (P5):

```python
import os
os.environ["ODP_PRODUCT_MODE"] = "production"
os.environ["NOTIFICATION_ADAPTER_TYPE"] = "multi"
os.environ.pop("ONCALL_ENDPOINT_URL", None)
from modules.notifications import get_notification_adapter
m = get_notification_adapter()
print(type(m.channel_adapters.get("webhook", m.default_adapter)).__name__)
# -> ConsoleNotificationAdapter
print(m.send("nid", "webhook", "oncall-route", "PROD ALERT", "detail"))
# -> (True, None)   # prints to stdout, reports success
```

Control: with `NOTIFICATION_ADAPTER_TYPE` unset the same environment still
raises `ValueError: Production mode or on-call route requires a configured valid
ONCALL_ENDPOINT_URL. Fail-closed gate enforced.` — so the gate is intact and the
new branches specifically route around it.

**Blast radius (P7).** `scripts/deployment/validate_cloud_run_live_deployment.py`
L644–648 asserts the gate as part of the live check `observability:fail_closed_gates`:

```python
notification_fail_closed = False
try:
    get_notification_adapter(endpoint_url="")
except ValueError:
    notification_fail_closed = True
```

At `2ab1ad64`, in production mode:

| `NOTIFICATION_ADAPTER_TYPE` | result | `notification_fail_closed` |
|---|---|---|
| unset | `ValueError` | `True` |
| `console` | `ValueError` | `True` |
| `email` | returns `EmailNotificationAdapter` | **`False`** |
| `in_app` | returns `InAppNotificationAdapter` | **`False`** |
| `multi` | returns `MultiChannelNotificationAdapter` | **`False`** |

Any deployment environment that sets one of the three new types turns
`fail_closed_ok` false and reds the deployment gate — while simultaneously
removing the protection the gate exists to prove.

**Suggested remedy (parent owner's call):** move the three branches below the
`require_oncall` block, or gate them with `if not is_prod`, or give
`MultiChannelNotificationAdapter` a fail-closed `default_adapter` in production
instead of `ConsoleNotificationAdapter`. Whichever is chosen, the deployment
gate above needs a matching test so the coupling is not rediscovered in prod.

### F2 (B) — Email adapter silently mock-delivers and records `status="SENT"`

`_default_smtp_transport()` L399–406:

```python
if not self.smtp_host:
    print(f"\n[MOCK EMAIL DELIVERY] Sent email to {message_data['user_id']}\n…")
    return True, None
```

`send()` then records a receipt with `"status": "SENT"` (L476, L486). The only
guard is `REQUIRE_EMAIL_ROUTE=1|true` (L441–443), which is **opt-in**, so the
default posture for an unconfigured environment — including production — is
fail-open.

Reproduce (P6): production mode, `NOTIFICATION_ADAPTER_TYPE=multi`, no
`SMTP_HOST`, no `REQUIRE_EMAIL_ROUTE` →
`send(...)` returns `(True, None)` and `delivery_receipts[-1]["status"] == "SENT"`
with `smtp_host is None`.

This is the precise failure mode U-4 was opened to eliminate: a receipt that
attests delivery when the message only reached stdout. It also makes the
`ODP-PLAN-UAT-SIGNOFF-001` evidence trail unsafe — six `SENT` receipts prove
nothing unless SMTP was actually configured.

**Suggested remedy:** invert the default (fail closed unless an explicit
`ALLOW_MOCK_EMAIL`/non-prod mode is set), or at minimum record
`status="MOCK"`/`"TEST_ONLY"` rather than `"SENT"` — the sibling
`OnCallNotificationAdapter` already models this distinction at L314–315, where
`REQUIRE_EXTERNAL_VERIFICATION` selects `PENDING_VERIFICATION` vs `TEST_ONLY`
instead of claiming `SENT`. The pattern is already in the file and only needs
applying to email.

### F3 (N) — `send_notification()` returns the same value for delivered and undelivered

`service.py` L115 / L135 / L137 all `return notification_id`. `None` is returned
only for two *suppression* cases: preferences disabled or empty (L91–93) and a
dedup hit (L96–100). A total delivery failure — every retry on every channel
exhausted — returns a truthy id indistinguishable from success.

Reproduce (P1): single-channel pref, adapter fails all attempts, `severity="info"`
→ `nid` is a UUID, and the sole receipt has `status == "failed"`.
Reproduce (P2): two channels, both fail, `severity="danger"` → `nid` is a UUID,
receipts `['escalated', 'failed']`.

All five trigger helpers propagate this return value verbatim, and both new spec
tests assert only `assert nid is not None` (`test_five_spec_triggers_flow` L219–239,
`test_six_canonical_roles_delivery` L269). Those two assertions therefore prove
that the trigger was *accepted*, not that anything was *delivered*. The tests are
still meaningful — both go on to assert inbox contents, which is the real check —
but the `nid is not None` lines should not be read as delivery evidence, and a
caller in production has no way to detect total failure short of re-reading
receipts.

**Suggested remedy:** return a small result object (or `None` on total failure,
with suppression signalled separately), and add one test asserting that a
fully-failed send is distinguishable from a successful one.

### F4 (N) — Failure-trigger dedup key uses `hash()`, which is not stable across processes

`service.py` L258:

```python
dedup_key=f"task_failed:{task_id}:{hash(error_message or '')}",
```

CPython randomises `str.__hash__` per process via `PYTHONHASHSEED`. Reproduce (P8),
identical input `("u", "ODP-TASK-1", "Container exit code 1")`:

```
seed=1  task_failed:ODP-TASK-1:-1772017447938218609
seed=2  task_failed:ODP-TASK-1:-985849413702237732
seed=3  task_failed:ODP-TASK-1:9111451749518394643
```

With `InMemoryNotificationRepository` this is invisible (the store dies with the
process). With `DurableNotificationRepository` — the whole point of the durable
path — the dedup row written by one process never matches the key computed by
the next, so a recurring failure re-notifies after every restart, redeploy, or
worker respawn. It also makes `notification_deduplication` rows unreadable for
operators. The other four triggers use stable natural keys; only this one does not.

**Suggested remedy:** `hashlib.sha256((error_message or "").encode()).hexdigest()[:16]`.

### F5 (N) — In-app inbox severity is hard-coded, so the new `severity` column is inert

`adapters.py` L519 hard-codes `"severity": "info"` on every inbox item. The
severity the service computed (`danger` for failure/rollback, `warning` for
timeout) never reaches the adapter, because the `NotificationAdapter` Protocol
(`service.py` L13, `send()` signature L14–23) has no severity parameter.

Reproduce (P3): `send_rollback_notification` + `send_failure_notification`
(both `severity="danger"`) → inbox severities `['info', 'info']`, and
`get_inbox(user_id, severity="danger")` returns `[]`.

Consequence: `notification_inapp_inbox.severity` (migration 000005 / 000008) can
only ever hold `'info'`, and the `severity=` filter on both
`InAppNotificationAdapter.get_inbox()` and the two repositories'
`get_inapp_items()` is dead surface. An operator inbox cannot distinguish a
rollback alert from a routine assignment.

**Suggested remedy:** thread `severity` through the adapter Protocol (an optional
keyword keeps the existing adapters source-compatible), or have
`InAppNotificationAdapter` accept a per-send severity. Either way, one test
should assert a `danger` trigger is retrievable via `get_inbox(severity="danger")`.

### F6 (Q) — Five triggers exist as API but have no production caller

`grep` across the tree at `2ab1ad64` finds callers of the five helpers only in
`service.py` (definitions) and `tests/reliability/test_notifications.py`.
`NotificationService(...)` is constructed outside the module in exactly one
non-test place — `scripts/e2e/generate_observability_evidence.py` L75 — and
`get_notification_adapter` in two scripts
(`generate_observability_evidence.py` L74, `validate_cloud_run_live_deployment.py` L382/L646).

No task-assignment, timeout, approval, failure, or rollback code path invokes any
trigger. The commit body is explicit and honest about this ("Not changing: …
operator shell API routes"), so it is a declared boundary rather than an
oversight — but the U-4 scope decision ties this task to
`ODP-PLAN-UAT-SIGNOFF-001`, whose acceptance is "six roles actually receive task
assignment notifications". As delivered, six roles can receive notifications only
if a test calls the helper.

**Decision needed from the parent owner/reviewer:** either (a) accept
`ODP-CAP-NOTIFICATION-DELIVERY-001` as the adapter/trigger layer and open a
follow-up task for lifecycle wiring before UAT signoff can proceed, or (b) widen
this task to include the call sites. This packet does not assert which; it flags
that UAT signoff is currently blocked either way.

### F7 (Q) — Only the primary channel is delivered; there is no fan-out

`service.py` L104 selects `primary_channel = channels[0]`, and the secondary
channel is reached only through the escalation path at L118, which requires
`severity in ("danger", "high", "warning")` **and** a prior failure.

Reproduce (P4): preferences `["email", "in_app"]`, `severity="danger"`, adapter
succeeding → only `email` is sent.

`ODP-FR-SHARED-006` says 站內、Email、Webhook「或後續擴充通道」. Whether that
requires simultaneous fan-out or per-user channel selection is a spec-reading
question, not a code defect — the current failover semantics are a coherent
design. Flagging it because the default preference is `["email"]` (`service.py`
L70), which means **no user receives an in-app notification by default**, even
though in-app is the channel this task newly built. That interacts directly with
F6: whatever wires the triggers will also have to decide the default channel set.

### F8 (N, cosmetic) — Dead branch in the composite router

`adapters.py` L605–613: the `if not adapter:` block re-queries
`self.channel_adapters` with the same key that just missed at L604. For
`norm_channel == "email"` (L606–607) the lookup
`self.channel_adapters.get("email", self.default_adapter)`
can only return `self.default_adapter`. The `inapp`→`in_app` and
`oncall`→`webhook` aliases in the neighbouring branches *are* meaningful; only
the `email` branch is a no-op. Harmless, but it reads as if `email` had an alias
when it does not.

### Positives worth recording

- The diff is 100% deliverable code — no generated state mirrors swept in, which
  is unusual for this lane and makes review cheap.
- Migration append pattern was verified correct against both engines rather than
  assumed (see the §2 note); the parent picked the right approach.
- Receipt hashing (`request_hash` over a sorted-key payload) is consistent with
  the pre-existing `OnCallNotificationAdapter` convention rather than inventing
  a new one.
- `OnCallNotificationAdapter` is genuinely untouched — the webhook contract
  declared out of scope in the commit body really is out of scope in the diff.
- Both new duck-typed repository hooks (`save_inapp_item`, `get_inapp_items`,
  `acknowledge_inapp_item`) are implemented on **both** repositories, so the
  in-memory and durable paths do not diverge.
- `ruff` and `py_compile` are clean on the whole delivered surface, and the
  claimed 10 passing tests reproduce exactly.

---

## 6. Handoff Note & Reviewer Transition

> [!NOTE]
> Round-1 handoff record, kept for audit. The standing handoff is **§8**.

This sidecar review packet is round 1 and ready for review.

- **Owner**: `Claude2`
- **Assigned Reviewer**: `Antigravity4` (who is also the parent task's owner)
- **Sidecar scope compliance**: this branch adds exactly one file,
  `support/sidecars/ODP-CAP-NOTIFICATION-DELIVERY-001/ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW.md`.
  No canonical truth, contract, runtime, registry, or governance file is touched.
  `git diff --stat origin/dev...HEAD` should show that single path.
- **Reviewer diff shortcut**: `git diff origin/dev...HEAD` — one added file.
- **Reviewing the *evidence* rather than the prose**: every §5 finding is
  reproducible from the code blocks in that section against a detached worktree
  at `2ab1ad64` (§4 step 0). The probes were deleted after running; nothing in
  this branch executes.
- **Parent-state caveat the reviewer should know**: `ODP-CAP-NOTIFICATION-DELIVERY-001`
  is in status `review` but its branch `task/ODP-CAP-NOTIFICATION-DELIVERY-001`
  is **local-only** — not pushed, no PR, not an ancestor of `origin/dev`. There
  is therefore no PR for the parent reviewer (`Antigravity2`) to review through
  ReviewBus. That is a lane observation for the parent owner, outside this
  sidecar's authority to fix.
- **Recommendation to the parent owner/reviewer**: F1 and F2 are production
  fail-closed regressions and should be resolved before
  `ODP-CAP-NOTIFICATION-DELIVERY-001` is approved. F3–F5 and F8 are safe to
  schedule as a follow-up. F6 and F7 are scope decisions only the parent owner
  can make, and F6 in particular determines whether
  `ODP-PLAN-UAT-SIGNOFF-001` can proceed.
- **Next Action**: hand off `ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW`
  to reviewer `Antigravity4`. On approval, the parent owner may absorb this
  packet into `ODP-CAP-NOTIFICATION-DELIVERY-001`.

---

## 7. Round 2 Re-derivation @ `c73a6710`

### 7.1 Why this round exists

Round 1 was approved at sidecar commit `84c22fb9`. `origin/dev` then advanced
past that commit, so `task/ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW`
went `BEHIND` and the approved head stopped being deliverable. Composing the
base advance moves the sidecar head, which is a re-review event by
construction.

The packet file itself is unchanged by a base advance — which is exactly when a
sidecar review packet is most likely to be silently stale. It was: while this
sidecar sat in `review_approved`, the parent shipped two commits.

| Parent commit | Subject | Effect on this packet |
|---|---|---|
| `048dad9a` | record round 1 review findings | Adds `docs/evidence/completion/ODP-CAP-NOTIFICATION-DELIVERY-001/review_findings_claude3_round1.md` (196 lines) — the parent reviewer `Claude3`'s own round-1 findings B1–B4, raised independently of this sidecar |
| `c73a6710` | fix round 1 review findings B1–B4 | Rewrites `adapters.py` (+427/−27 vs merge-base), `service.py` (+152/−2), `repositories.py` (+108), tests (+495). **Invalidates five of this packet's eight findings.** |

Parent task state also moved: status is now `in_progress` (reopened by
`Claude3`), owner `Claude` / reviewer `Claude3` — not the `Antigravity4` /
`Antigravity2` pair recorded in the round-1 header — and the parent branch is
now pushed to `origin` (round 1 correctly recorded it as local-only; that claim
is superseded). There is still **no PR** for the parent.

### 7.2 Re-derived deliverable shape

```bash
git fetch origin
git worktree add --detach /tmp/odp-parent-pin-c73a6710 c73a6710
cd /tmp/odp-parent-pin-c73a6710
git diff --numstat $(git merge-base origin/dev c73a6710) c73a6710
```

| Path | ins | del |
|---|---|---|
| `docs/evidence/completion/…/review_findings_claude3_round1.md` | 196 | 0 |
| `docs/evidence/completion/…/review_response_round1_fixes.md` | 163 | 0 |
| `infra/db/migrations/000005_durable_notifications.sql` | 11 | 0 |
| `infra/db/migrations/000008_postgresql_runtime_persistence.sql` | 13 | 0 |
| `modules/notifications/__init__.py` | 6 | 0 |
| `modules/notifications/application/service.py` | 152 | 2 |
| `modules/notifications/infrastructure/__init__.py` | 6 | 0 |
| `modules/notifications/infrastructure/adapters.py` | 427 | 27 |
| `modules/notifications/infrastructure/repositories.py` | 108 | 0 |
| `tests/reliability/test_notifications.py` | 495 | 0 |
| **Total** | **1577** | **29** |

Code-only subtotal (excluding the two evidence docs): **1218 / 29**. Module
sizes at `c73a6710`: `adapters.py` 801 L, `service.py` 319 L, `repositories.py`
246 L, `test_notifications.py` 607 L.

> The round-1 §2 layer map is structurally still correct — same five adapter
> classes, same two repositories, same five triggers — but every line number in
> §2 and §5 is stale. The factory now lives at `adapters.py` L734–801.

### 7.3 Per-finding disposition

Re-run verbatim at `c73a6710`. **B** = blocking, **N** = non-blocking,
**Q** = scope question.

| # | Round-1 finding | Round-2 status | Evidence at `c73a6710` |
|---|---|---|---|
| **F1** (B) | Factory `email`/`in_app`/`multi` branches sit above the `require_oncall` gate, so prod + `multi` yields a Console-backed webhook route that reports success | **RESOLVED** | The gate is inverted: `is_prod` is evaluated at L763 *before* the channel branches, and the prod arms call `_build_prod_email_adapter` / `_build_prod_inapp_adapter` / `_build_oncall_adapter`, each of which raises when unconfigured. Probe P1 below: all six production paths raise. |
| **F2** (B) | `EmailNotificationAdapter` mock-delivers to stdout and records `status="SENT"` with `SMTP_HOST` unset; `REQUIRE_EMAIL_ROUTE` only opt-in | **RESOLVED for production** | Two independent guards: `send()` L486–499 fails closed on `_is_production_env() and self.uses_default_transport` regardless of the opt-in, and `_default_smtp_transport` L443–448 refuses the mock branch under a production env. The `[MOCK EMAIL DELIVERY]` path survives only outside production, which is its intended use. |
| **F3** (N) | `send_notification()` returns the same truthy id whether delivery succeeded or failed | **STILL OPEN** | `service.py` L166 still `return notification_id` after the escalation branch falls through. Probe P2: total failure → non-`None` id, receipts `['failed']`; failed escalation → non-`None` id, receipts `['escalated','failed']`. Identical to a success return. This is the sole reason **A7** is still NOT MET. |
| **F4** (N) | Failure dedup key uses builtin `hash()`, unstable across `PYTHONHASHSEED` | **RESOLVED** | `service.py` L285 uses `hashlib.sha256(error_message.encode()).hexdigest()[:16]`. Probe P4: key `task_failed:T-500:81f52337ebb4cb16`; `sha256(b"boom").hexdigest()[:16]` = `81f52337ebb4cb16` — deterministic across processes. |
| **F5** (N) | In-app severity hard-coded to `info`, so the new `severity` column and its filter are inert | **RESOLVED** | `severity` is on the `NotificationAdapter` protocol (L23) and threaded through `_send_with_retries` (L183) behind an `adapter_accepts_severity` compatibility shim. Probe P3: a failure + a rollback trigger now land as `['danger','danger']` and `get_inbox(severity="danger")` returns 2 items (round 1: `['info','info']`, filter → `[]`). |
| **F6** (Q) | No production caller wires any of the five triggers; blocks `ODP-PLAN-UAT-SIGNOFF-001` | **STILL OPEN — unchanged** | Probe P6: grep for all five helper names across `*.py`/`*.ts`/`*.tsx` outside `modules/notifications/` and `tests/` returns **zero** hits. The five triggers remain an API with test-only callers. This is still the gating scope question for the parent owner. |
| **F7** (Q) | Only `channels[0]` is delivered; default preference is `["email"]`, so nobody gets in-app by default | **PARTIALLY ADDRESSED** | Success path is unchanged — probe P5: preference `["email","in_app"]` still produces one receipt, `['email']`, and the default is still `channels=["email"]` (L99). What is new is a *failure* escalation (L146–164): if the primary send fails **and** severity ∈ `{danger, high, warning}` **and** a `channels[1]` exists, delivery escalates to the secondary channel. That is failover, not fan-out; the scope question stands. |
| **F8** (N) | Dead `email` branch in the composite router | **RESOLVED** | `MultiChannelNotificationAdapter.send` L670–686 resolves `email` through `channel_adapters` like every other channel; the unreachable alias branch is gone. |

The parent's own round-1 reviewer (`Claude3`) additionally raised **B4** —
`DurableNotificationRepository.acknowledge_inapp_item` swallowed exceptions and
did not report a miss. This sidecar did not find it in round 1; it is fixed at
`c73a6710` (returns `rowcount > 0`, matching the in-memory sibling). Recorded
here so the packet is not read as a complete finding set for round 1.

### 7.4 `OnCallNotificationAdapter` contract (A3) — one change, non-breaking

Round 1 recorded the webhook adapter as untouched. At `c73a6710` there is
exactly one hunk inside it:

```diff
@@ class OnCallNotificationAdapter:
         detail: str,
+        severity: str = "info",
     ) -> tuple[bool, str | None]:
-        import hashlib
-        import json
-        import os
+        # ``severity`` is accepted so this adapter satisfies the same protocol as
+        # the in-app/email adapters, but it is deliberately kept out of the signed
+        # on-call payload: that request contract is owned elsewhere and unchanged.
         import re
-        import uuid
```

The parameter is keyword-defaulted and never reaches the payload, and the
removed lines are local imports hoisted to module scope. The signed request
body and `request_hash` are unchanged, so A3 stays **Met**; the note exists only
so a reader who greps for "unchanged" is not surprised by a diff hunk.

### 7.5 Re-derived verification suite (round 2)

```bash
# 0. Materialise the round-2 pin
git fetch origin
git worktree add --detach /tmp/odp-parent-pin-c73a6710 c73a6710
cd /tmp/odp-parent-pin-c73a6710

# 1. Parent test surface
/home/lupin/.local/bin/uv run pytest tests/reliability/test_notifications.py

# 2. Lint + compile
/home/lupin/.local/bin/uv run ruff check \
  modules/notifications tests/reliability/test_notifications.py
python3 -m py_compile \
  modules/notifications/application/service.py \
  modules/notifications/infrastructure/adapters.py \
  modules/notifications/infrastructure/repositories.py

# 3. Deliverable shape
git diff --numstat $(git merge-base origin/dev c73a6710) c73a6710

# 4. Landing state
git merge-base --is-ancestor c73a6710 origin/dev; echo "ancestor-of-dev: $?"
gh pr list --head task/ODP-CAP-NOTIFICATION-DELIVERY-001 --state all --json number

# 5. Cleanup
cd - && git worktree remove /tmp/odp-parent-pin-c73a6710
```

**Recorded results (owner run, round 2, 2026-08-06, `dev` = `5499b7a4`):**

| # | Command | Result |
|---|---|---|
| 1 | `pytest tests/reliability/test_notifications.py` | **27 passed in 1.35s** (round 1: 10 passed) |
| 2a | `ruff check modules/notifications tests/…` | **All checks passed!** |
| 2b | `py_compile` on the three modules | clean |
| 3 | `git diff --numstat <merge-base> c73a6710` | 10 files, **1577 insertions / 29 deletions** |
| 4a | `git merge-base --is-ancestor c73a6710 origin/dev` | exit 1 — **parent still not on `dev`** |
| 4b | `gh pr list --head task/ODP-CAP-NOTIFICATION-DELIVERY-001 --state all` | `[]` — **still no parent PR** |

**Probe transcript** (read-only, public APIs only, run against the detached pin
and deleted afterwards):

```
P1  factory gate, APP_ENV=production
    prod + multi,  no ONCALL_ENDPOINT_URL  -> RAISED  ...requires a configured valid ONCALL_ENDPOINT_URL
    prod + email,  no SMTP_HOST            -> RAISED  ...requires a configured SMTP_HOST
    prod + in_app, no repository           -> RAISED  ...requires a durable repository
    prod + console                         -> RAISED  ConsoleNotificationAdapter is forbidden in production
    prod, no adapter type                  -> RAISED  ...requires a configured valid ONCALL_ENDPOINT_URL
    prod + bogus type                      -> RAISED  Unknown notification adapter type 'bogus'
    prod + multi, endpoint AND smtp set    -> RAISED  ...requires a durable repository   (still fail-closed)
    non-prod + multi (control)             -> MultiChannelNotificationAdapter            (expected)

P2  send_notification() outcome observability          [F3 — still open]
    undelivered send          -> nid=non-None  receipts=['failed']
    failed escalation         -> nid=non-None  receipts=['escalated', 'failed']

P3  in-app severity                                    [F5 — resolved]
    inbox severities -> ['danger', 'danger'] ; get_inbox(severity='danger') -> 2 items

P4  failure dedup key                                  [F4 — resolved]
    dedup keys -> ['task_failed:T-500:81f52337ebb4cb16']
    sha256(b'boom').hexdigest()[:16] = 81f52337ebb4cb16

P5  fan-out on the success path                        [F7 — still open]
    pref ["email","in_app"] -> channels delivered ['email']

P6  production callers of the five triggers            [F6 — still open]
    grep outside modules/notifications + tests -> 0 hits

P7  deployment gate predicate
    validate_cloud_run_live_deployment.py:660 observability:fail_closed_gates
    get_notification_adapter(endpoint_url='') under APP_ENV=production -> ValueError
    notification_fail_closed -> True     (round 1: False)
```

### 7.6 What changed in the recommendation

| Round 1 said | Round 2 says |
|---|---|
| "F1 and F2 are production fail-closed regressions and should be resolved before the parent is approved" | **Done.** Both are fixed at `c73a6710`; no blocking finding remains against the parent's code. |
| "F3–F5 and F8 are safe to schedule as a follow-up" | F4, F5, F8 landed with the B1–B4 fix. **Only F3 remains** as a scheduled non-blocking item. |
| "F6 and F7 are scope decisions only the parent owner can make" | **Unchanged and now the critical path.** F6 (no production caller) is the only thing standing between this capability and `ODP-PLAN-UAT-SIGNOFF-001`. |

Two lane observations for the parent owner, outside this sidecar's authority:

1. `task/ODP-CAP-NOTIFICATION-DELIVERY-001` is now pushed but still has **no
   PR**, so `c73a6710` cannot reach `dev` and the parent reviewer has nothing to
   review through ReviewBus.
2. The parent is back in `in_progress` under a different owner/reviewer pair
   (`Claude` / `Claude3`) than the round-1 header recorded.

---

## 8. Round 2 Handoff (standing)

- **Owner**: `Claude2` · **Reviewer**: `Antigravity4`
- **Why re-review**: `origin/dev` advanced past the approved sidecar head
  `84c22fb9`, so the approval was no longer deliverable. Composing the base
  advance moves the head off `approved_head` by construction.
- **Sidecar scope compliance**: this branch still touches exactly one path,
  `support/sidecars/ODP-CAP-NOTIFICATION-DELIVERY-001/ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW.md`.
  No canonical truth, contract, runtime, registry, or governance file is
  touched. All round-2 verification was read-only against a throwaway detached
  worktree at `c73a6710`, which was removed afterwards.
- **Reviewer diff shortcuts**:
  - versus the previously approved head — `git diff 84c22fb9 HEAD`
  - the base advance alone — `git log --oneline 84c22fb9..HEAD` (one merge of
    `origin/dev`, one packet update)
  - sidecar surface — `git diff --stat origin/dev...HEAD` → the single packet path
- **What is substantively new**: §7 (round-2 re-derivation against parent head
  `c73a6710`) and §8, plus round-2 columns in the §3 acceptance matrix and
  time-scoping notices on §1, §5 and §6. §2 and §4–§6 round-1 content is
  preserved unedited as the audit record of what was true at `2ab1ad64`.
- **The one thing to check if you read nothing else**: the packet's two blocking
  findings are no longer blocking. If §7.3 is right, the parent's remaining
  exposure is F3 (non-blocking) and the F6/F7 scope decisions.
- **Next Action**: `re_review` to `Antigravity4`. On approval, the parent owner
  may absorb this packet into `ODP-CAP-NOTIFICATION-DELIVERY-001`.
