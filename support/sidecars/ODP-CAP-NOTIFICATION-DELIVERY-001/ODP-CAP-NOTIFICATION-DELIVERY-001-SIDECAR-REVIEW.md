# Sidecar Review Packet: ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW

- **Task ID**: `ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-CAP-NOTIFICATION-DELIVERY-001` (owner `Claude`, reviewer `Claude3`) — **TERMINAL: `done`, archived `2026-08-07T00:10:50Z`**, final `approved_head` `a8700b00`, merged to `dev` via PR #670 at `44109779`
- **Helper Kind**: `review_packet`
- **Owner**: `Claude2`
- **Reviewer**: `Claude3`
- **Packet Revision**: **round 5 (2026-08-08)** — re-pinned to the parent's **final** head `a8700b00`, which is now an ancestor of `dev`; round 4 was derived against `4fd5f7ee`, round 3 against `914a243c`, round 2 against `c73a6710`, round 1 against `2ab1ad64`
- **Target Artifact**: `support/sidecars/ODP-CAP-NOTIFICATION-DELIVERY-001/ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW.md`

> [!NOTE]
> **Reviewer field history.** Rounds 1–4 were reviewed by `Antigravity4` and every
> handoff section below records that name as the audit fact of its round. The
> assigned reviewer changed to `Claude3` on `2026-08-08T09:42:45Z` through a
> two-step helper claim (`Antigravity4` → `Claude` → `Claude3`, both prior agents
> dispatch-paused). `Claude3` reviewed round 4 and reopened it. **The standing
> reviewer is `Claude3` (§14);** historical `Antigravity4` mentions in §6, §8,
> §10 and §12 are deliberately left as written.

### Parent Pin (read this before trusting any number below)

| Field | Round 1 pin | Round 2 pin | Round 3 pin | Round 4 pin |
|---|---|---|---|---|
| Parent status | `review` | `in_progress` | `review_approved` | **`review_approved`** (unchanged; approved by `Claude3`, round 2) |
| Parent branch | `task/ODP-CAP-NOTIFICATION-DELIVERY-001` (local only) | now on `origin` | on `origin`; PR #670 open but **draft** | on `origin`; **PR #670 is now READY, `MERGEABLE`/`CLEAN`, 5/5 checks green** (§11.3) |
| Parent head | `2ab1ad64` — *anchor notification delivery handlers and triggers* | `c73a6710` — *fix round 1 review findings B1–B4* | `914a243c` — *record round 2 review approval* | **`4fd5f7ee`** — *merge origin/dev base advance* |
| Parent `approved_head` | — | — | `914a243c` | **`4fd5f7ee`** (`ai-status.json`, re-read this round) |
| Last **code**-touching commit | `2ab1ad64` | `c73a6710` | still `c73a6710` | **still `c73a6710`** — §11.2 proves the delta since is docs/CI-only |
| Merge-base with `origin/dev` | `77567b5e` | `71d44d03` | `71d44d03` | **`266649e5`** (the base advance moved it) |
| Landed on `dev`? | No | No, and no PR | No; PR #670 `isDraft: true` | **Still no.** `git merge-base --is-ancestor 4fd5f7ee origin/dev` → exit 1. But the PR is now mergeable — §11.3 |
| Deliverable surface | 8 files, 703 ins / 3 del | 10 files, 1577 ins / 29 del (§7.2) | 11 files, 1817 ins / 29 del (§9.2) | **11 files, 1817 ins / 29 del** vs `266649e5` — byte-identical to round 3 |
| `dev` at time of review | `71d44d03` | `5499b7a4` | `02c847dd` | **`e7b53ce0`** |

The round 1–4 table above is a five-column history and is kept as written. The
current pin is separate because round 5 is not another moving-target pin — the
parent stopped moving:

| Field | **Round 5 pin (current)** |
|---|---|
| Parent status | **`done` — terminal.** Archived `2026-08-07T00:10:50Z` at `ai-task-archive/tasks/ODP-CAP-NOTIFICATION-DELIVERY-001.json`, `terminal_outcome: completed` |
| Parent branch | `task/ODP-CAP-NOTIFICATION-DELIVERY-001` — merged and auto-deleted; PR #670 `merged_at 2026-08-07T00:10:31Z` |
| Parent head | **`a8700b00`** — *merge origin/dev base advance* (committed by `Antigravity6`, trailers `LLM-Agent: Claude` / `Reviewer: Claude3`) |
| Parent `approved_head` | **`a8700b00`** = `review_gate_sha` = `verified_head` = `pr_head_ref_oid` (archive `delivery` block) |
| Last **code**-touching commit | **still `c73a6710`** — §13.3 proves the notification surface has not moved since round 2 |
| Merge-base with `origin/dev` | n/a — `a8700b00` **is** an ancestor of `dev` (`git merge-base --is-ancestor a8700b00 origin/dev` → exit 0) |
| Landed on `dev`? | **Yes.** Merge commit `44109779`, PR #670, 5/5 checks SUCCESS at merge time |
| Deliverable surface | unchanged; now *on* `dev`, so it is measured against `origin/dev` directly rather than a pin (§13.4) |
| `dev` at time of review | **`af4650d9`** (this sidecar's base advance target this round) |

> [!IMPORTANT]
> **§2–§5 below are time-scoped to `2ab1ad64` and are superseded where §7 says
> so.** The parent advanced two commits after round 1 (`048dad9a` record round 1
> findings, `c73a6710` fix B1–B4) and that commit rewrote 237 lines of
> `adapters.py`. Read **§7 first**: it re-derives every round-1 claim against
> `c73a6710` and is the standing verdict on the code. **§9 is the round-3
> delta** — it re-pins §7 onto the parent's `approved_head` and adds what
> changed off-code (parent approval, parent PR, parent reviewer's own findings).
> **§11 is the round-4 delta** — it re-pins onto `4fd5f7ee` and cross-checks the
> parent against the sibling acceptance packet that has now landed on `dev`.
> **§13 is the round-5 delta and is the one that changes what happens next** — it
> re-pins onto the parent's *final* head `a8700b00`, records that the parent
> closed **without absorbing** F3/F6/F7/F9/F10, and routes those five to named
> follow-up tasks (§13.7) because the absorb path is now closed.
> Round-1 line numbers and "does not exist" statements apply only to `2ab1ad64`.

> [!WARNING]
> **Round 1's two blocking findings are resolved at `c73a6710`.** F1 (production
> factory bypass) and F2 (email mock-delivery reporting `SENT`) no longer
> reproduce — see §7.3 for the probe transcript. **No blocking finding remains.**
> F3, F6 and F7 still stand; F4, F5 and F8 are also resolved. §11.2 re-confirms
> all of this holds unchanged at the parent's `approved_head` `4fd5f7ee`, because
> no code moved between `c73a6710` and `4fd5f7ee`.

> [!CAUTION]
> **Round 4 adds two new findings, F9 and F10, and they are the reason this
> round is not a formality.** The sibling packet
> `ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-ACCEPTANCE.md` **merged into `dev`**
> at `de11fc76` (PR #671) *after* the parent was approved. It is the parent's
> acceptance criteria of record, and measured against it the parent's approved
> head fails **AC-3** (no notification API routes exist — F9) and cannot
> evidence **AC-1** for the UAT role set (none of the six canonical role ids
> appear anywhere in the module or its tests — F10). See §11.4.

> [!CAUTION]
> **Round 5 changes the disposition of every open finding, and it is the reason
> this packet is no longer a review of a pending change.** The parent
> `ODP-CAP-NOTIFICATION-DELIVERY-001` **merged into `dev` at `44109779`** (PR
> #670) and is **archived `done`** at `a8700b00`. Three consequences:
>
> 1. **F3, F6, F7, F9 and F10 are now live on `dev`, not pending.** §13.4
>    re-verifies all five against `origin/dev` itself — no detached pin, because
>    the code being described is the mainline code.
> 2. **The parent closed without absorbing any of the five.** No parent-side
>    document records them, and nothing anywhere on `dev` references this packet
>    (§13.5). This packet is their only record.
> 3. **The absorb path is not merely unused, it is mechanically closed** — the
>    parent's task id is archived and `ai_status.py` refuses to reuse it
>    (§13.6). So §12's "hand to the parent owner for absorption" is
>    unexecutable, and the five findings are routed to **named follow-up tasks**
>    in **§13.7** instead. F6+F10 — the `ODP-PLAN-UAT-SIGNOFF-001` gate — go to
>    `ODP-CAP-NOTIFICATION-TRIGGER-WIRING-001`.

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
the standing verdict at `c73a6710` and is the one to act on. **Rounds 3, 4 and 5
add no column**: the parent's `approved_head` — `914a243c`, then `4fd5f7ee`, then
the final `a8700b00` — contains no code change versus `c73a6710` (§9.2, §11.2,
§13.3), so every row below carries forward unchanged — A6 Met, A7 still NOT MET,
everything else Met. As of round 5 these rows describe **`dev`**, not a pending
branch (§13.4).

> [!NOTE]
> This matrix is **this packet's own** A1–A8. It is not the same axis as the
> AC-1…AC-6 published in the sibling acceptance packet, which landed on `dev`
> only at round 4. **§11.4 scores the parent against AC-1…AC-6 separately** and
> is where F9 and F10 come from; read both, they do not substitute for each
> other.

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
> from this section without its §7.3 disposition. **F9 and F10 are round-4
> findings and are recorded in §11.4, not here** — they are scored against the
> acceptance packet that landed on `dev` at `de11fc76`, which did not exist when
> this section was written. The open set as of round 4 was **F3, F6, F7, F9,
> F10**; round 5 adds **F11, F12, F13** and gives every item a named destination
> in **§13.7**, which is the current authority on what is open and where it goes.

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

**Blast radius (P7).** `product_ops/deployment/validate_cloud_run_live_deployment.py`
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
non-test place — `delivery_toolchain/e2e/generate_observability_evidence.py` L75 — and
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

## 8. Round 2 Handoff (superseded by §10)

> [!NOTE]
> Time-scoped to the round-2 handoff at sidecar head `762b30d8`. Kept as the
> audit record; the standing handoff is **§10**.

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

---

## 9. Round 3 Re-derivation @ parent `approved_head` `914a243c`

### 9.1 Why this round exists

Round 2 was approved at sidecar commit `762b30d8`. `origin/dev` then advanced
from `5499b7a4` to `02c847dd` (PR #664, `ODP-ORCH-MERGE-QUEUE-ENABLEMENT-001`),
so PR #665 went `BEHIND` — `dev` sets `required_status_checks.strict = true`, so
a behind branch cannot merge — and the approved head stopped being deliverable.
Composing the base advance moves the head off `approved_head`, which is a
re-review event by construction, not a mechanical refresh.

The incoming `dev` delta is `.github/workflows/ci.yml` (+6) and
`.github/workflows/merge-queue-review-gate.yml` (+84, new). It touches nothing
this sidecar or its parent owns, and the merge was conflict-free.

As in round 2, the packet file is unchanged by a base advance — which is exactly
when a review packet is most likely to be silently stale. This round it was
stale in three off-code ways, all recorded below.

### 9.2 The parent's code did not move — proof, not assumption

The parent advanced `c73a6710` → `ecde5dd2` → `914a243c`. Both new commits are
evidence-only:

```bash
git worktree add --detach /tmp/odp-parent-pin-914a243c 914a243c
cd /tmp/odp-parent-pin-914a243c
git diff --numstat c73a6710 914a243c
git diff --name-only c73a6710 914a243c -- . ':(exclude)docs/**'   # expect empty
```

| Path | ins | del |
|---|---|---|
| `docs/evidence/completion/…/review_findings_claude3_round2.md` | 226 | 0 |
| `docs/evidence/completion/…/review_response_round1_fixes.md` | 14 | 0 |
| **Total** | **240** | **0** |

The exclusion probe returns **no paths**: nothing outside `docs/` changed.
Corroborated by size: `adapters.py` 801 L, `service.py` 319 L,
`repositories.py` 246 L, `test_notifications.py` 607 L — identical to the §7.2
figures at `c73a6710`.

> **Consequence for the reviewer:** every code claim in §7.3, §7.4 and the §3
> acceptance matrix transfers to `914a243c` verbatim. F1/F2/F4/F5/F8 resolved;
> **F3, F6, F7 still open**; A7 still NOT MET. §9.5 re-runs the suite and the
> three still-open findings' probes at the new pin anyway rather than asserting
> transfer.

Full deliverable surface at `914a243c` versus merge-base `71d44d03`: **11 files,
1817 ins / 29 del**. Code-only subtotal is unchanged at **1218 / 29**.

### 9.3 Parent lane state — both round-2 observations superseded

Round 2 recorded "the parent is in `in_progress`" and "there is still no PR".
Both are now out of date, and the replacement facts matter more:

| Round 2 observation | Round 3 state |
|---|---|
| Parent is `in_progress` under `Claude` / `Claude3` | **`review_approved`.** `Claude3` approved round 2 at reviewed head `ecde5dd2`; `ai-status.json` records `approved_head` = `review_gate_sha` = `914a243c`, the commit that records that approval. |
| No PR for `task/ODP-CAP-NOTIFICATION-DELIVERY-001` | **PR #670 is open — and `isDraft: true`.** |

> [!WARNING]
> **PR #670 is a draft, and that is a silent merge stall.** A draft PR cannot
> merge and cannot hold auto-merge (`gh pr merge --auto` fails with
> `Pull request is a draft (enablePullRequestAutoMerge)`), while `gh pr checks`
> and the review gate still read green — so the parent can sit approved and
> green indefinitely with nothing to show why. `scripts/ai-status.sh done` will
> refuse the parent because `914a243c` is not an ancestor of `dev`
> (`git merge-base --is-ancestor 914a243c origin/dev` → exit 1).
> **Suggested parent-owner action:** `gh pr ready 670`, then advance the base
> onto `02c847dd` (the PR is 4+ commits behind), then merge. This is an
> observation for the parent owner, outside this sidecar's authority.

### 9.4 Cross-reference: parent reviewer's round-2 findings vs this packet

`Claude3` recorded four non-blocking findings (N1–N4) in
`docs/evidence/completion/ODP-CAP-NOTIFICATION-DELIVERY-001/review_findings_claude3_round2.md`.
None of them overlaps this packet's open findings, in either direction — so the
two sets are complementary and the union is the parent's real residual exposure.

| Parent reviewer finding | In this packet? | Note |
|---|---|---|
| **N1** `notification_inapp_inbox` absent from `_REQUIRED_RELATIONS` (`postgresql.py:39`) | **No** — this sidecar missed it | Its three sibling notification tables are listed; the gap is in the redundant boot-time guard only, since both provisioning paths use `CREATE TABLE IF NOT EXISTS`. |
| **N2** `OnCallNotificationAdapter.delivery_receipts` uncapped (5 append sites) | **No** — missed | Consistent with the "on-call adapter untouched" scope declaration; predates this task. Narrows the round-1 response note's claim that all receipt lists are capped at 500. |
| **N3** `NOTIFICATION_ADAPTER_TYPE` outranks `REQUIRE_ONCALL_ROUTE` outside production | **No** — missed | Production is unaffected; this packet's §7.3 P1 probe covered only the prod arm, which is why the non-prod precedence went unrecorded here. |
| **N4** direct `MultiChannelNotificationAdapter()` still defaults to console | Adjacent to **F1**, not the same | F1 was about the *factory* path and is resolved. N4 is about bypassing the factory entirely; no such call site exists today. |
| — | **F3** — `send_notification()` returns the same truthy id on total delivery failure | **Not raised by the parent reviewer.** Sole reason A7 is NOT MET. |
| — | **F6** — no production caller for any of the five triggers | **Not raised by the parent reviewer.** The gate on `ODP-PLAN-UAT-SIGNOFF-001`. |
| — | **F7** — only `channels[0]` is delivered on the success path | **Not raised by the parent reviewer.** Failure escalation is failover, not fan-out. |

This is the packet's residual value after the parent's own round-2 approval:
**F3, F6 and F7 are carried by this sidecar alone.** Neither set is blocking, so
neither changes the parent's approval — but a parent owner reading only
`review_findings_claude3_round2.md` would close the task without F6 on record,
and F6 is the item that gates the downstream UAT sign-off.

### 9.5 Round 3 verification

```bash
# 0. Materialise the round-3 pin
git fetch origin
git worktree add --detach /tmp/odp-parent-pin-914a243c 914a243c
cd /tmp/odp-parent-pin-914a243c

# 1. Code-immobility proof (the load-bearing check this round)
git diff --numstat c73a6710 914a243c
git diff --name-only c73a6710 914a243c -- . ':(exclude)docs/**'
wc -l modules/notifications/infrastructure/adapters.py \
      modules/notifications/application/service.py \
      modules/notifications/infrastructure/repositories.py \
      tests/reliability/test_notifications.py

# 2. Parent test surface + lint at the new pin
/home/lupin/.local/bin/uv run pytest tests/reliability/test_notifications.py
/home/lupin/.local/bin/uv run ruff check \
  modules/notifications tests/reliability/test_notifications.py

# 3. F6 probe — production callers of the five triggers
grep -rn --include='*.py' --include='*.ts' --include='*.tsx' \
  -e send_task_assigned_notification -e send_timeout_notification \
  -e send_approval_notification -e send_failure_notification \
  -e send_rollback_notification .

# 4. Landing state
git merge-base --is-ancestor 914a243c origin/dev; echo "ancestor-of-dev: $?"
gh pr list --head task/ODP-CAP-NOTIFICATION-DELIVERY-001 --state all \
  --json number,state,isDraft

# 5. Cleanup
cd - && git worktree remove /tmp/odp-parent-pin-914a243c
```

**Recorded results (owner run, round 3, 2026-08-06, `dev` = `02c847dd`):**

| # | Command | Result |
|---|---|---|
| 1a | `git diff --numstat c73a6710 914a243c` | 2 files, **240 ins / 0 del**, both under `docs/evidence/` |
| 1b | same diff excluding `docs/**` | **empty** — no non-docs path changed |
| 1c | `wc -l` on the four modules | 801 / 319 / 246 / 607 — **identical to §7.2** |
| 2a | `pytest tests/reliability/test_notifications.py` | **27 passed in 1.34s** (round 2: 27, round 1: 10) |
| 2b | `ruff check modules/notifications tests/…` | **All checks passed!** |
| 3 | five-trigger grep | **every hit is a definition in `service.py` (L206/230/252/275/300) or a call in `tests/reliability/test_notifications.py`. Zero production callers — F6 stands.** |
| 4a | `git merge-base --is-ancestor 914a243c origin/dev` | exit 1 — **parent still not on `dev`** |
| 4b | `gh pr list --head task/…-001` | `[{"number":670,"state":"OPEN","isDraft":true}]` — **PR now exists but is a draft** |

The detached pin worktree was removed after the run. All checks are read-only;
this branch changes exactly one file.

### 9.6 What changed in the recommendation

| Round 2 said | Round 3 says |
|---|---|
| "No blocking finding remains against the parent's code" | **Unchanged, and now independently confirmed** — the parent's own reviewer approved round 2 with zero blocking findings, reached via their own probes. |
| "Only F3 remains as a scheduled non-blocking item" | Still true for this packet's set; the parent reviewer adds **N1–N4**, none overlapping. Combined non-blocking backlog: **F3, N1, N2, N3, N4**. |
| "F6 is the only thing standing between this capability and `ODP-PLAN-UAT-SIGNOFF-001`" | **Unchanged, and now the packet's most load-bearing claim** — F6 appears in no parent-side document, so absorbing this packet is the only way it survives parent closeout. |
| "The parent branch is pushed but has no PR" | **Superseded.** PR #670 exists and is a **draft**, which is a harder stall than no PR: it reads green everywhere but cannot merge (§9.3). |

---

## 10. Round 3 Handoff (superseded by §12)

- **Owner**: `Claude2` · **Reviewer**: `Antigravity4`
- **Why re-review**: `origin/dev` advanced `5499b7a4` → `02c847dd`, so PR #665
  went `BEHIND` and sidecar `approved_head` `762b30d8` was no longer
  deliverable. Composing the base advance moves the head off `approved_head` by
  construction, so `done` is unreachable without a fresh approval.
- **Sidecar scope compliance**: this branch still touches exactly one path,
  `support/sidecars/ODP-CAP-NOTIFICATION-DELIVERY-001/ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW.md`.
  No canonical truth, contract, runtime, registry, or governance file is
  touched. Confirm with `git diff --stat origin/dev...HEAD` → one file. All
  round-3 verification was read-only against a throwaway detached worktree at
  `914a243c`, removed afterwards.
- **Reviewer diff shortcuts**:
  - versus the previously approved head — `git diff 762b30d8 HEAD`
  - the base advance alone — `git log --oneline 762b30d8..HEAD` (one merge of
    `origin/dev` at `02c847dd`, one packet update)
  - no finding record was deleted — `git diff --numstat 762b30d8 HEAD -- <packet>`
    is **228 ins / 16 del**, and
    `git diff 762b30d8 HEAD -- <packet> | grep -E '^-' | grep -v '^---'` shows
    all 16 removals are rewritten-in-place lines: 2 header fields, the 11-line
    round-2 pin table widened to a round-3 column, 2 lines inside the §3 and
    §7-pointer notices, and the §8 heading. §5, §7 and every finding record are
    untouched.
- **What is substantively new**: §9 (round-3 re-derivation against the parent's
  `approved_head` `914a243c`) and §10, plus a round-3 column in the parent pin
  table and time-scoping notices on §3 and §8. §1–§8 content is otherwise
  preserved as the audit record of rounds 1 and 2.
- **The one thing to check if you read nothing else**: §9.4. The parent is now
  `review_approved`, so this packet's remaining job is the three findings its
  own reviewer did not raise — **F3, F6, F7** — and F6 is the gate on
  `ODP-PLAN-UAT-SIGNOFF-001`.
- **Known lane condition, recorded not fixed**: `gh pr merge 665 --auto --merge`
  is the standing mitigation for the approved-then-BEHIND loop, and it is
  routinely denied to background workers by the permission classifier. If the
  denial recurs this round it is recorded in the `re_review` message for an
  operator, not retried. The same draft-PR stall now also affects the **parent**
  PR #670 (§9.3).
- **Next Action**: `re_review` to `Antigravity4`. On approval, the parent owner
  may absorb this packet into `ODP-CAP-NOTIFICATION-DELIVERY-001` — carrying
  F3/F6/F7 across is the point.

---

## 11. Round 4 Re-derivation @ parent `approved_head` `4fd5f7ee`

### 11.1 Why this round exists

Round 3 was approved at sidecar commit `dc0174bf`. `origin/dev` then advanced
`02c847dd` → `e7b53ce0` (PR #666 worktree base-advance sidecar, PR #671 the
sibling **acceptance** sidecar, PR #661 `ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001`),
so PR #665 went `BEHIND` again — `dev` sets `required_status_checks.strict =
true`, so a behind branch cannot merge. Composing the base advance moves the
head off `approved_head` by construction, which is a re-review event, not a
mechanical refresh.

The incoming `dev` delta into this branch is `.orchestrator/github_bus.py`
(+81/−8), `.orchestrator/test_github_bus.py` (+133) and the sibling acceptance
packet (+168). The merge was conflict-free and touches nothing this sidecar
owns.

**Two things changed that make this round load-bearing rather than clerical:**

1. The parent's `approved_head` itself moved, `914a243c` → `4fd5f7ee` — the
   parent composed its own base advance (§11.2, §11.3).
2. The sibling acceptance packet **landed on `dev`**, so the parent's acceptance
   criteria are now a published document that can be measured against the
   parent's approved code. Doing so produces **two new findings** (§11.4).

### 11.2 The parent's code still has not moved — proof, not assumption

The parent advanced `914a243c` → `4fd5f7ee`, a single merge commit
*"ODP-CAP-NOTIFICATION-DELIVERY-001: merge origin/dev base advance"*.

| Path | ins | del |
|---|---|---|
| `.github/workflows/ci.yml` | 6 | 0 |
| `.github/workflows/merge-queue-review-gate.yml` | 84 | 0 |
| `support/sidecars/…-SIDECAR-ACCEPTANCE.md` | 168 | 0 |
| `support/sidecars/ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001/…-SIDECAR-REVIEW.md` | 93 | 0 |
| **Total** | **351** | **0** |

Every path is incoming `dev` content: two CI workflow files and two sibling
sidecar packets. The exclusion probe
`git diff --name-only 914a243c 4fd5f7ee -- . ':(exclude)docs/**'
':(exclude)support/**' ':(exclude).github/**'` returns **no paths**.

Corroborated by size at `4fd5f7ee`: `adapters.py` **801 L**, `service.py`
**319 L**, `repositories.py` **246 L**, `test_notifications.py` **607 L** —
identical to the §7.2 figures at `c73a6710` and the §9.2 figures at `914a243c`.

> **Consequence for the reviewer:** every code claim in §7.3, §7.4 and the §3
> acceptance matrix transfers to `4fd5f7ee` verbatim, for the second round
> running. F1/F2/F4/F5/F8 resolved; **F3, F6, F7 still open**; A7 still NOT MET.
> §11.5 re-runs the suite and the open findings' probes at the new pin anyway
> rather than asserting transfer. F3 and F7 were re-read from source this round,
> not carried forward — see §11.4.

Full deliverable surface at `4fd5f7ee` versus the **new** merge-base
`266649e5`: **11 files, 1817 ins / 29 del** — numerically identical to round 3
against the old merge-base `71d44d03`, which is itself a check that the base
advance absorbed no parent code.

### 11.3 Parent lane state — round 3's draft-PR warning is RESOLVED

| Round 3 observation | Round 4 state |
|---|---|
| **PR #670 is a draft** — cannot merge, cannot hold auto-merge, reads green everywhere | **Resolved.** `isDraft: false`, `mergeable: MERGEABLE`, `mergeStateStatus: CLEAN`, `headRefOid` = `4fd5f7ee` = `approved_head`, and all 5 checks SUCCESS (`orchestrator`, `product`, `performance-gate`, `product-e2e-gate`, `task-review-gate`). |
| Parent is 4+ commits behind `02c847dd`; needs a base advance before merging | **Done** — that is exactly what `4fd5f7ee` is. |
| `scripts/ai-status.sh done` will refuse the parent (`914a243c` not an ancestor of `dev`) | **Still true at `4fd5f7ee`** (`git merge-base --is-ancestor 4fd5f7ee origin/dev` → exit 1), but now for the *only* remaining reason: the mergeable PR has not been merged. |

> [!NOTE]
> Round 3's suggested parent-owner action (`gh pr ready 670`, advance the base,
> then merge) has been carried out through step 2. **The parent is now one
> authorized merge away from closeout.** This sidecar has the same shape and the
> same single remaining step (§12), so the two are blocked on one operator
> action, not two different ones.

### 11.4 New findings: the parent measured against its own landed acceptance criteria

The sibling packet
`support/sidecars/ODP-CAP-NOTIFICATION-DELIVERY-001/ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-ACCEPTANCE.md`
(owner `Antigravity4`, reviewer/parent-owner `Claude`) **merged into `dev` at
`de11fc76`, PR #671**. It defines six acceptance criteria AC-1…AC-6 for the
parent. Round 3 could not have used it — it was not on `dev` then. Measuring the
parent's `approved_head` against it is the substance of this round.

| AC | Requirement (as published on `dev`) | State at `4fd5f7ee` |
|---|---|---|
| **AC-1** 5 event triggers handled | all five triggers invoke `send_notification()` | **Met in code, unevidenced for UAT** — the five methods exist (`service.py` L206/230/252/275/300) and are exercised by tests, but only under ad-hoc role ids → **F10** |
| **AC-2** Email channel delivery | `EmailNotificationAdapter` produces `channel="email"` receipts with status `sent` | **Met** — `adapters.py:395`; caveat under F7 (only `channels[0]` is delivered on the success path, so a user whose email is not first never exercises it) |
| **AC-3** In-app inbox **and API queryability** | inbox store **and** `GET /api/v1/notifications/inbox`, `POST /api/v1/notifications/{id}/read` | **NOT MET (store half met, API half absent)** → **F9** |
| **AC-4** Webhook / on-call dispatch | `OnCallNotificationAdapter` with SHA + HMAC | **Met** — `adapters.py:84`, unchanged since round 1 |
| **AC-5** Retry **and escalation** enforcement | retries to `max_retries`, high-severity escalation to secondary channels | **Partially met** — retry yes; escalation is *failover only* and reaches `channels[1]` at most. This is **F7**, now promoted from a design note to an explicit AC gap |
| **AC-6** Durable receipt authority | `verify_durable_delivery_authority()` succeeds | **Met** — `domain/authority.py:950`, exported via `modules/notifications/__init__.py` |

#### F9 — AC-3's API half does not exist (non-blocking to the parent's approval, blocking to AC-3)

The acceptance packet's §4.2 names the file to create,
`apps/api/app/routes/notifications.py`, and two endpoints. At `4fd5f7ee`:

- `apps/api/app/routes/notifications.py` does not exist.
- No file matching `*notif*` defines an `APIRouter`.
- `modules/notifications/` contains only `application/`, `domain/`,
  `infrastructure/` — there is **no `interface/` layer at all**.
- The only route string that matches is
  `apps/api/app/routes/operator_modules/shell.py:328` →
  `@router.get("/shell/notifications", …)`, which is the operator shell's own
  module listing, not the notification inbox.

The **store** half of AC-3 *is* delivered: `InAppNotificationAdapter`
(`adapters.py:557`) plus `save_inapp_item` / `get_inapp_items` /
`acknowledge_inapp_item` on **both** `InMemoryNotificationRepository` and
`DurableNotificationRepository` (`repositories.py` L41/45/60 and L170/194/227).
So this is a missing exposure layer over a finished store, not missing
functionality — which is why it is recorded as non-blocking to an approval that
has already been given, and as a hard blocker on AC-3 and on the operator-console
consumer the dependency map draws (`INAPP_ADAPTER → OPSBOARD`).

#### F10 — none of the six canonical UAT role ids appear anywhere in the module

`ODP-PLAN-UAT-SIGNOFF-001` requires receipts for six canonical roles, named in
the acceptance packet's §2 as `executive`, `operations_manager`,
`region_director`, `store_manager`, `finance_auditor`, `system_admin`.

```bash
grep -rn "operations_manager\|region_director\|finance_auditor\|system_admin" \
  modules/notifications/ tests/reliability/test_notifications.py
```

returns **zero hits** at `4fd5f7ee`. The suite's role fixture
(`test_notifications.py` L265–271) uses a different, ad-hoc set: `ops-lead`,
`franchisee-ops`, `store-manager`, `area-manager`, `hq-admin`,
`system-operator`. Only `store-manager` is even close to a canonical id, and it
is hyphen- rather than underscore-cased, so it would not match either.

> This compounds **F6** rather than duplicating it. F6 says no production code
> calls the five triggers; F10 says that even the test evidence does not cover
> the role set the UAT gate will ask for. Together they mean
> `ODP-PLAN-UAT-SIGNOFF-001` cannot source a single qualifying receipt from this
> capability as approved, and neither gap is recorded in any parent-side
> document.

#### Note (not a finding): the landed acceptance packet's gap analysis is stale

The acceptance packet's §1 states that `EmailNotificationAdapter` does not exist
and that there is no in-app inbox store. Both were true at the pre-implementation
pin it was authored against; both are false at `4fd5f7ee`
(`adapters.py:395` and `:557`). Because the packet is **on `dev`** while the
parent's code is **not**, a `dev` reader today sees a published document that
understates the delivered capability by two adapters. Recorded for the parent
owner to correct on absorption; it is not a defect in the parent's code and is
not counted as a finding against it.

### 11.5 Round 4 verification

```bash
# 0. Materialise the round-4 pin
git fetch origin
git worktree add --detach /tmp/odp-parent-pin-4fd5f7ee 4fd5f7ee
cd /tmp/odp-parent-pin-4fd5f7ee

# 1. Code-immobility proof
git diff --numstat 914a243c 4fd5f7ee
git diff --name-only 914a243c 4fd5f7ee -- . ':(exclude)docs/**' \
  ':(exclude)support/**' ':(exclude).github/**'
wc -l modules/notifications/infrastructure/adapters.py \
      modules/notifications/application/service.py \
      modules/notifications/infrastructure/repositories.py \
      tests/reliability/test_notifications.py

# 2. Parent test surface + lint at the new pin
/home/lupin/.local/bin/uv run pytest tests/reliability/test_notifications.py
/home/lupin/.local/bin/uv run ruff check \
  modules/notifications tests/reliability/test_notifications.py

# 3. F6 probe — production callers of the five triggers
grep -rn --include='*.py' --include='*.ts' --include='*.tsx' \
  -e send_task_assigned_notification -e send_timeout_notification \
  -e send_approval_notification -e send_failure_notification \
  -e send_rollback_notification .

# 4. F9 probe — AC-3 API surface
grep -n '^class ' modules/notifications/infrastructure/adapters.py
grep -rn "APIRouter" --include='*.py' . | grep -i notif
find modules/notifications -type f

# 5. F10 probe — canonical UAT role ids
grep -rn "operations_manager\|region_director\|finance_auditor\|system_admin" \
  modules/notifications/ tests/reliability/test_notifications.py

# 6. Landing state
git merge-base --is-ancestor 4fd5f7ee origin/dev; echo "ancestor-of-dev: $?"
gh pr view 670 --json number,state,isDraft,mergeable,mergeStateStatus,headRefOid

# 7. Cleanup
cd - && git worktree remove /tmp/odp-parent-pin-4fd5f7ee
```

**Recorded results (owner run, round 4, 2026-08-06, `dev` = `e7b53ce0`):**

| # | Command | Result |
|---|---|---|
| 1a | `git diff --numstat 914a243c 4fd5f7ee` | 4 files, **351 ins / 0 del** — 2 CI workflows + 2 sibling sidecar packets, all incoming `dev` content |
| 1b | same diff excluding `docs/`, `support/`, `.github/` | **empty** — no product path changed |
| 1c | `wc -l` on the four modules | 801 / 319 / 246 / 607 — **identical to §7.2 and §9.2** |
| 2a | `pytest tests/reliability/test_notifications.py` | **27 passed in 1.53s** (round 3: 27, round 2: 27, round 1: 10) |
| 2b | `ruff check modules/notifications tests/…` | **All checks passed!** |
| 3 | five-trigger grep | every hit is a definition in `service.py` or a call in `tests/reliability/test_notifications.py`. **Zero production callers — F6 stands.** |
| 4a | `grep '^class '` on `adapters.py` | `Console`(44) `OnCall`(84) `Email`(395) `InApp`(557) `MultiChannel`(646) — email + in-app adapters present |
| 4b | `APIRouter` grep filtered to notif | **no file** — `apps/api/app/routes/notifications.py` absent; only `operator_modules/shell.py:328` `/shell/notifications` (unrelated). **F9** |
| 4c | `find modules/notifications -type f` | 9 files, layers `application/` `domain/` `infrastructure/` — **no `interface/`** |
| 5 | canonical role-id grep | **zero hits.** Suite uses `ops-lead`/`franchisee-ops`/`store-manager`/`area-manager`/`hq-admin`/`system-operator`. **F10** |
| 6a | `git merge-base --is-ancestor 4fd5f7ee origin/dev` | exit 1 — **parent still not on `dev`** |
| 6b | `gh pr view 670` | `OPEN`, `isDraft:false`, `MERGEABLE`, `CLEAN`, head `4fd5f7ee`, 5/5 checks SUCCESS — **round 3's draft stall is cleared** |

F3 and F7 were additionally re-read from source at this pin rather than carried
forward: `service.send_notification()` still ends in an unconditional
`return notification_id` after the escalation arm fails (**F3** — total delivery
failure is indistinguishable from success at the call site), and the success
path still delivers `primary_channel = channels[0]` only, with `channels[1]`
reached solely on failure and `channels[2:]` never (**F7**).

The detached pin worktree was removed after the run. All checks are read-only;
this branch changes exactly one file.

### 11.6 What changed in the recommendation

| Round 3 said | Round 4 says |
|---|---|
| "No blocking finding remains against the parent's code" | **Unchanged.** F9 and F10 are gaps against the acceptance packet's AC-3 / AC-1-for-UAT, not defects in code that is already approved. Neither reverses an approval. |
| "Combined non-blocking backlog: F3, N1, N2, N3, N4" | **Extended: F3, F9, F10, N1, N2, N3, N4.** F9 and F10 are carried by this sidecar alone. |
| "F6 is the packet's most load-bearing claim — the gate on `ODP-PLAN-UAT-SIGNOFF-001`" | **Still the most load-bearing, and now sharper.** F10 shows the UAT gate would fail on role identifiers even if F6 were fixed tomorrow. The pair is one work item, not two. |
| "F7 is failover, not fan-out — non-blocking design note" | **Promoted to an AC gap.** The now-published AC-5 requires escalation to secondary *channels*; the implementation escalates to exactly one. |
| "PR #670 is a draft — a harder stall than no PR" | **Resolved.** #670 is ready, `CLEAN`, 5/5 green, head = `approved_head`. The parent needs one authorized merge. |
| — | **New:** the parent's acceptance criteria are now published on `dev` (§11.4) and the parent's approved head does not satisfy all six. Absorbing this packet is the only path by which that is recorded. |

---

## 12. Round 4 Handoff (superseded by §14)

> [!WARNING]
> **Superseded, and specifically unexecutable — not just out of date.** This
> section's closing instruction was "on approval, the parent owner may absorb
> this packet into `ODP-CAP-NOTIFICATION-DELIVERY-001`", and its lane note said
> "the parent needs one authorized merge; one operator action clears both". Both
> premises expired on `2026-08-07T00:10:31Z` when PR #670 merged: the parent is
> archived `done`, has no owner to hand to, and its task id can no longer be
> assigned (§13.6). Kept verbatim as the round-4 audit record. **The standing
> handoff is §14; the routing that replaces "absorb" is §13.7.**

- **Owner**: `Claude2` · **Reviewer**: `Antigravity4`
- **Why re-review**: `origin/dev` advanced `02c847dd` → `e7b53ce0`, so PR #665
  went `BEHIND` and sidecar `approved_head` `dc0174bf` was no longer
  deliverable. Composing the base advance moves the head off `approved_head` by
  construction, so `done` is unreachable without a fresh approval. The parent's
  own `approved_head` also moved (`914a243c` → `4fd5f7ee`), so the packet's pin
  had to be re-derived regardless of the base advance.
- **Sidecar scope compliance**: this branch still touches exactly one path,
  `support/sidecars/ODP-CAP-NOTIFICATION-DELIVERY-001/ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW.md`.
  No canonical truth, contract, runtime, registry, or governance file is
  touched. Confirm with `git diff --stat origin/dev...HEAD` → one file. All
  round-4 verification was read-only against a throwaway detached worktree at
  `4fd5f7ee`, removed afterwards.
- **Reviewer diff shortcuts**:
  - versus the previously approved head — `git diff dc0174bf HEAD`
  - the base advance alone — `git log --oneline dc0174bf..HEAD` (one merge of
    `origin/dev` at `e7b53ce0`, one packet update)
  - no finding record was deleted — `git diff dc0174bf HEAD -- <packet>` is
    **328 ins / 21 del**, and
    `git diff dc0174bf HEAD -- <packet> | grep -E '^-' | grep -v '^---'` shows
    all 21 removals are rewritten-in-place lines: 1 header revision field, the
    12-line round-3 pin table widened to a round-4 column, 3 lines inside the
    header `WARNING`, the 4-line §3 matrix notice, 1 line closing the §5
    notice, and the §10 heading. §1–§9 findings are untouched.
- **What is substantively new**: §11 (round-4 re-derivation against parent
  `approved_head` `4fd5f7ee`) and §12, plus a round-4 column in the parent pin
  table and a `CAUTION` notice in the header. §1–§10 are preserved as the audit
  record of rounds 1–3.
- **The one thing to check if you read nothing else**: §11.4. You authored the
  acceptance packet that landed on `dev` at `de11fc76`; this round measures the
  parent's approved head against your AC-1…AC-6 and finds **AC-3 not met (F9)**
  and **AC-1 unevidenced for the six canonical UAT roles (F10)**. If either
  reading of your criteria is wrong, that is the highest-value correction you can
  make to this packet.
- **Known lane condition, recorded not fixed**: PR #665 is `OPEN`, not draft,
  `CLEAN`, 5/5 checks green, and `check_pr_merge_eligibility.py --pr 665` passed
  in round 3 — but `gh pr merge 665` (both `--merge` and `--merge --auto`) is
  denied to background workers by the permission classifier, and no auto-merge
  loop is running on this box. `done` fails closed until an authorized merge
  lands #665 on `dev`. The parent PR #670 is now in exactly the same position
  (§11.3): ready, clean, green, unmerged. **One operator action clears both.**
- **Next Action**: `re_review` to `Antigravity4`. On approval, the parent owner
  may absorb this packet into `ODP-CAP-NOTIFICATION-DELIVERY-001` — carrying
  **F3, F6, F7, F9, F10** across is the point; F6+F10 are the pair that gates
  `ODP-PLAN-UAT-SIGNOFF-001`.

---

## 13. Round 5 Re-derivation @ parent **final** head `a8700b00` (terminal)

### 13.1 Why this round exists

Round 4 was approved by `Antigravity4` at sidecar head `8151be69` on
`2026-08-06T13:44:47Z`. Reviewership then moved to `Claude3` through a two-step
helper claim, and `Claude3` **did not approve** round 4. The reopen reason was
explicitly *not* scope or drift — the packet was byte-identical to
`approved_head` `8151be69`, and the intervening `origin/dev` merge `ddde7a06`
changed no packet content.

The reopen reason was that **the parent reached terminal state after round 4**,
which invalidated the *instruction* §12 gave rather than any *fact* §12 asserted:

| §12 premise | State at round 5 |
|---|---|
| "the parent needs one authorized merge" | **Expired.** That merge happened — PR #670 merged `2026-08-07T00:10:31Z`. |
| "one operator action clears both" | **False now.** The parent is closed; only this sidecar's PR #665 is still open. |
| "on approval, the parent owner may absorb this packet" | **Unexecutable.** The parent is archived `done` with no active owner, and its task id can no longer be assigned (§13.6). |

This round also composes a base advance: `origin/dev` advanced to `af4650d9`
(42 commits ahead of the previous merge-base `956170de`), merged into this
branch at `7ffbb0f5`, conflict-free. The incoming delta touches
`.orchestrator/*`, `docs/evidence/*`, `modules/adlift/*` and five unrelated
sidecar packets — nothing this sidecar owns, and nothing in
`modules/notifications`.

### 13.2 Parent terminal state — from the archive record, not inference

Source: `ai-task-archive/tasks/ODP-CAP-NOTIFICATION-DELIVERY-001.json`.

| Field | Value |
|---|---|
| `terminal_status` / `terminal_outcome` | `done` / `completed` |
| `archived_at` | `2026-08-07T00:10:50Z` |
| `approved_head` = `review_gate_sha` = `last_approved_head` | `a8700b00638e90df70409d68164900cc1f89b76d` |
| `delivery.verified_head` = `commit` = `pr_head_ref_oid` | same `a8700b00` — all four agree |
| `commit_subject` | `ODP-CAP-NOTIFICATION-DELIVERY-001: merge origin/dev base advance` |
| commit author / trailers | `Antigravity6`; `LLM-Agent: Claude`, `Reviewer: Claude3`, `Verified: pytest tests/reliability/test_notifications.py tests/reliability/test_runtime_observability.py; ruff check modules/notifications` |
| PR | **#670**, base `dev`, `merged_at 2026-08-07T00:10:31Z`, `merge_commit 44109779` |
| `head_merged_to_target` / `merge_verified_via_pr` | `true` / `true` |
| `ci_status` at merge | `success` — 5/5 (`orchestrator`, `product`, `performance-gate`, `product-e2e-gate`, `task-review-gate`) |
| `git_clean` / `dirty_entry_count` | `true` / `0` |

Independently confirmed against git rather than taken from the record:
`git merge-base --is-ancestor a8700b00 origin/dev` → **exit 0**, and
`44109779` is likewise an ancestor of `dev`. **The parent is genuinely landed**,
which is the first time in five rounds that this packet describes shipped code.

#### F11 (N, record accuracy) — the archived closeout note overstates what landed

The archive's `next` field is the parent's permanent closeout record. It reads:

> "Delivered in-app + email notification channels for FR-SHARED-006:
> modules/notifications adapters (SMTP email, in-app repo-backed),
> **application/service.py trigger wiring for the five spec triggers**, durable
> notification migrations 000005/000008, and tests/reliability/test_notifications.py."

"Trigger wiring" reads as if the five spec events are wired to their lifecycle
call sites. They are not — §13.4 re-confirms zero production callers. What
landed is five *trigger helpers*: correct, tested, and uninvoked. A reader of the
archive alone would conclude FR-SHARED-006's trigger requirement is closed.

This is non-blocking and not a code defect, but it **cannot be corrected in
place**: archived task records are terminal state, and this packet does not edit
generated state files. The correction therefore has to be carried by the
follow-up task (§13.7, task **A**).

### 13.3 The parent's code never moved — proof at the final head

The reviewer's reopen note asserted "no notification code moved
`4fd5f7ee..a8700b00`". Reproduced rather than accepted:

```bash
git diff --numstat 4fd5f7ee a8700b00
git diff --numstat c73a6710 a8700b00 -- modules/notifications \
  tests/reliability/test_notifications.py infra/db/migrations
```

| Path in `4fd5f7ee..a8700b00` | ins | del | Provenance |
|---|---|---|---|
| `.orchestrator/github_bus.py` | 81 | 8 | incoming `dev` (`ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001`) |
| `.orchestrator/test_github_bus.py` | 133 | 0 | same |
| **Total** | **214** | **8** | **no product path, no notification path** |

The second command — the whole notification surface measured from round 2's pin
straight to the parent's final head — returns **empty**. Corroborated by size at
`a8700b00`: `adapters.py` **801 L**, `service.py` **319 L**,
`repositories.py` **246 L**, `test_notifications.py` **607 L** — identical to
§7.2 (`c73a6710`), §9.2 (`914a243c`) and §11.2 (`4fd5f7ee`).

> **Consequence:** every code claim in §7.3, §7.4, §11.4 and the §3 acceptance
> matrix transfers to the parent's final, merged head verbatim. F1/F2/F4/F5/F8
> resolved; **F3, F6, F7, F9, F10 open**; A7 NOT MET. Confirmed for the fourth
> consecutive round, now terminally: the parent's code cannot move again.

### 13.4 The five open findings, re-verified on `dev` itself

**Methodology change, and it is the point of this round.** Rounds 1–4 measured a
pending branch through a throwaway detached worktree. The parent is now merged,
so round 5 measures `origin/dev`. This branch's post-base-advance head contains
the `dev` tip, and

```bash
git diff --name-only HEAD origin/dev -- modules/notifications \
  tests/reliability/test_notifications.py infra/db/migrations apps/api/app/routes
```

returns **empty** — so probes run in this worktree *are* probes of `dev`. These
five are no longer "findings against a change under review". They are **standing
properties of mainline**.

| # | Verdict on `dev` (`af4650d9`) | Evidence |
|---|---|---|
| **F3** (N) | **OPEN** — total delivery failure is indistinguishable from success | `service.py` L144/L164/**L166** all `return notification_id`. Probe: single-channel `info`, adapter always fails → `nid` non-`None`, stored receipts `[('email','failed')]`; two-channel `danger` → `nid` non-`None`, receipts `[('email','escalated'),('in_app','failed')]`; success control → also non-`None`. **A7 NOT MET on `dev`.** Now demonstrably production-affecting — see **F13** |
| **F6** (Q) | **OPEN — zero production callers** | Five-trigger grep across `*.py`/`*.ts`/`*.tsx` on `origin/dev`: every hit is a definition (`service.py` L206/230/252/275/300) or a test call (`test_notifications.py` L224/228/232/236/240/274/416/427/447/462–466/512). No lifecycle path invokes any of the five |
| **F7** (Q) | **OPEN — no fan-out** | `service.py` L133 `primary_channel = channels[0]`; L148 `secondary_channel = channels[1]` reached only on failure + severity ∈ `{danger,high,warning}`; `channels[2:]` unreachable. Probe with preference `["email","in_app","webhook"]` and `severity="danger"`, adapter succeeding → delivered `['email']` only. **AC-5 gap stands** |
| **F9** (N→AC-3) | **OPEN in substance, but round 4's description was wrong — restated below** | `apps/api/app/routes/notifications.py` still absent; `modules/notifications` still has no `interface/` layer (9 files: `application/`, `domain/`, `infrastructure/`). **However** an inbox API does exist elsewhere — see the correction and **F12** |
| **F10** (Q) | **OPEN — zero hits** | `grep -rn "operations_manager\|region_director\|finance_auditor\|system_admin" modules/notifications tests/reliability/test_notifications.py` on `origin/dev` → **no hits**. The suite's role fixture (`test_notifications.py` L274 loop) still uses `ops-lead`/`franchisee-ops`/`store-manager`/`area-manager`/`hq-admin`/`system-operator` |

Suite and lint at `dev`: **27 passed in 1.45s**; `ruff check modules/notifications
tests/reliability/test_notifications.py` → **All checks passed!**

#### Correction to round 4's F9 description

Round 4 wrote that the only matching route was
"`apps/api/app/routes/operator_modules/shell.py:328` → `@router.get("/shell/notifications", …)`,
which is the operator shell's own module listing, not the notification inbox."
**That characterisation is wrong**, and it is corrected here rather than carried
forward. On `dev`, `operator_modules/shell.py` exposes a real inbox surface:

| Route | Line | Docstring |
|---|---|---|
| `GET /shell/notifications` (`severity`, `acknowledged` query filters) | 328 | "Return the durable notification inbox for the acting role." |
| `GET` / `PUT /shell/notifications/preferences` | 352 / 374 | notification preferences |
| `POST /shell/notifications/{notification_id}/acknowledgement` | 403 | "Durably acknowledge a notification. Audited and idempotent." |

That is functionally the read + acknowledge pair AC-3 asks for, under a different
path prefix. So the honest statement of AC-3 is not "no API exists" — it is
**F12**.

#### F12 (N, integration — the substantive replacement for F9's "missing API")

The two inboxes are **disjoint subsystems that share a name and nothing else.**

| | Parent's in-app inbox | Operator shell inbox |
|---|---|---|
| Written by | `InAppNotificationAdapter.send()` (`adapters.py:590`) | the shell "today" envelope, `_notifications_for()` (`opsboard/application/shell.py:839`) |
| Stored in | table `notification_inapp_inbox` (migrations `000005` / `000008`) | records collection `operator.shell_notification_states` (`shell.py:44`) |
| Read by | **nothing outside `modules/notifications` and `tests/`** | `GET /shell/notifications` → `get_notifications()` (`shell.py:875`), `"source": "operator-shell-notifications"` |
| Acknowledged by | `acknowledge_inapp_item()` (`repositories.py:227`) | `acknowledge_notification()` (`shell.py:916`), audited + idempotent |

Verified on `dev`:

- `grep -rn "notification_inapp_inbox"` over all `*.py`/`*.sql` → hits **only** in
  the two migrations and `modules/notifications/infrastructure/repositories.py`
  (L173/200/238/243).
- `grep -rn -e save_inapp_item -e get_inapp_items -e acknowledge_inapp_item -e InAppNotificationAdapter`
  over `*.py`/`*.ts`/`*.tsx` → hits **only** in `modules/notifications/` and
  `tests/reliability/test_notifications.py`.
- `grep -rn "from modules.notifications" apps/api/` → **no hits**. The API layer
  does not import the notifications module at all.

So the table the parent built has **no reader anywhere in the product**, and the
inbox API that does exist **cannot see anything the notification delivery path
writes**. AC-3 is therefore not a missing-routes problem (round 4's reading) but
an **integration** problem: either point the shell inbox at
`notification_inapp_inbox`, or expose the module's own inbox, but the current
state ships two in-app inboxes where the spec describes one. This is strictly
worse for `ODP-PLAN-UAT-SIGNOFF-001` than "no API": a UAT tester *can* open an
inbox screen, and it will be empty of everything this capability delivers.

#### F13 (N, promoted from F3) — the one production consumer discards the outcome

Rounds 1–4 stated that `NotificationService` is constructed "outside the module
in exactly one non-test place — `delivery_toolchain/e2e/generate_observability_evidence.py`
L75". **That is incomplete**, and the missing site is the one that matters:

```python
# apps/worker/assisted_listing_intake/worker.py L157-165  (DLQ poison isolation)
notification_repo = persistence.notification_repository
if notification_repo:
    ns = NotificationService(repository=notification_repo, adapter=get_notification_adapter())
    ar = AlertRouter(notification_service=ns)
    ar.trigger_alert("dlq-spike", f"Job {job.job_id} stage {stage_name} exceeded max attempts")
```

`AlertRouter.trigger_alert()` (`shared/observability/alerts.py:116`) maps
`P1→danger`, `P2→warning`, `P3→info` and calls `send_notification(...)` at L135.
This file predates the parent (added by `ODP-INTAKE-JOBS-001`, `818824fb`) and
exists at both `4fd5f7ee` and `a8700b00` — rounds 1–4 simply missed it.

Two consequences:

1. **F6 is unchanged but better shaped.** The claim "no production caller of the
   five spec triggers" survives verbatim (the grep is unambiguous). But the
   notification service *is* reachable in production on the DLQ alert path, so
   the F6 fix has a precedent to follow rather than needing greenfield wiring.
2. **F3 stops being theoretical.** `ar.trigger_alert(...)`'s return value is
   discarded at the call site, and by F3 even inspecting it would not
   distinguish delivered from wholly-failed. A `dlq-spike` alert — severity
   `danger`, i.e. the highest tier this system has — can fail every retry on
   every channel and leave no signal at the caller. That is the fail-open shape
   U-4 was opened to eliminate, surviving in the one place the capability is
   actually wired.

### 13.5 The parent closed **without** absorbing F3/F6/F7/F9/F10

The round-3 §9.4 prediction was that "a parent owner reading only
`review_findings_claude3_round2.md` would close the task without F6 on record".
That is what happened. Verified on `dev`:

| Check | Result |
|---|---|
| Files in `docs/evidence/completion/ODP-CAP-NOTIFICATION-DELIVERY-001/` | exactly **3** — `review_findings_claude3_round1.md`, `review_findings_claude3_round2.md`, `review_response_round1_fixes.md` |
| `git grep -i "F6\|F9\|F10\|fan-out\|no production caller\|operations_manager"` in that directory | **one hit**, and it is unrelated (`review_findings_claude3_round2.md:216` mentions the `ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001` sidecar) |
| `git grep -l "ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW" origin/dev` | **no files.** This packet has never existed on `dev` |
| Parent archive `next` / handoff records | describe fixes B1–B4 and the round-2 approval; **none of F3/F6/F7/F9/F10 appears** |

There is also a documentation asymmetry that makes this worse than a neutral
omission: the sibling **acceptance** packet *is* on `dev` (merged `de11fc76`,
PR #671), while this **review** packet is not. A `dev` reader today finds a
published AC-1…AC-6 for a capability marked `done`, with no record anywhere that
**AC-3 and AC-5 are unmet and AC-1 is unevidenced for the UAT role set**. Merging
PR #665 is what fixes that half of it; §13.7 is what fixes the other half.

### 13.6 The absorb path is **mechanically** closed, not merely unattended

This matters because "hand it to the parent owner later" is not a fallback that
still exists. `scripts/ai_status.py` refuses to reuse an archived task id:

```python
# scripts/ai_status.py:4606-4609  (command_assign)
if archived_task_snapshot(task_id):          # archived_task_snapshot at :1251
    raise SystemExit(
        f"Task {task_id} is archived. Create a new follow-up task instead of reusing the archived task id."
    )
```

`ai-task-archive/tasks/ODP-CAP-NOTIFICATION-DELIVERY-001.json` exists, so any
attempt to re-assign the parent id is rejected by design. This is independently
corroborated on `dev` by `docs/evidence/odp_orch_sidecar_archived_id_loop_001.md`
(`ODP-ORCH-SIDECAR-ARCHIVED-ID-LOOP-001`), which records **1963
`sidecar_task_create_failed` events across 15 distinct archived ids** produced by
exactly this guard — the guard is real, load-bearing, and routinely hit.

The message in the guard is also the instruction: *create a new follow-up task*.
That is what §13.7 does.

### 13.7 Routing: the open findings → named follow-up tasks

Round 5 replaces "absorb into the parent" with three named follow-up tasks. Every
open item has exactly one home; nothing is left pointing at an archived id or at
a reviewer handoff.

| Finding | Severity | Routed to |
|---|---|---|
| **F6** no production caller for the five spec triggers | Q — **gates `ODP-PLAN-UAT-SIGNOFF-001`** | **A** `ODP-CAP-NOTIFICATION-TRIGGER-WIRING-001` |
| **F10** none of the six canonical UAT role ids appear in the module or its tests | Q — **gates `ODP-PLAN-UAT-SIGNOFF-001`** | **A** (same task — F6+F10 are one work item, not two) |
| **F11** archived closeout note describes the helpers as "trigger wiring" | N, record accuracy | **A** (correct the claim in the new task's evidence; the archive itself is terminal) |
| **F9** AC-3's API half absent from `modules/notifications` | N → blocks AC-3 | **B** `ODP-CAP-NOTIFICATION-INBOX-INTEGRATION-001` |
| **F12** `notification_inapp_inbox` has no reader; the shell serves a disjoint inbox | N → the real AC-3 blocker | **B** (same task; F12 is why B is *integration*, not new routes) |
| acceptance packet §1 understates the delivered capability by two adapters (§11.4 note) | doc correction | **B** (its author `Antigravity4` owns the text; the fix belongs with the AC-3 work) |
| **F3** total delivery failure indistinguishable from success | N → **A7 NOT MET** | **C** `ODP-CAP-NOTIFICATION-DELIVERY-SEMANTICS-001` |
| **F13** the DLQ `dlq-spike` production alert path discards the outcome | N, but production fail-open | **C** (same task; F13 is F3's blast radius and its acceptance test) |
| **F7** only `channels[0]` is delivered — AC-5 escalation gap | Q → AC-5 | **C** |
| **N1** `notification_inapp_inbox` missing from `_REQUIRED_RELATIONS` (`postgresql.py:39`) | N (parent reviewer, §9.4) | **C** |
| **N2** `OnCallNotificationAdapter.delivery_receipts` uncapped | N (parent reviewer) | **C** |
| **N3** `NOTIFICATION_ADAPTER_TYPE` outranks `REQUIRE_ONCALL_ROUTE` outside production | N (parent reviewer) | **C** |
| **N4** direct `MultiChannelNotificationAdapter()` still defaults to console | N (parent reviewer) | **C** |

#### Task A — `ODP-CAP-NOTIFICATION-TRIGGER-WIRING-001` (the UAT gate; do this one first)

- **Why it is P0-shaped**: `ODP-PLAN-UAT-SIGNOFF-001` (status `todo`, owner
  `Antigravity`, reviewer `Human/Ops`) requires six roles to *actually receive*
  task-assignment notifications. With F6 and F10 both open, that gate cannot
  source a single qualifying receipt from the capability as shipped — not from
  production (no caller) and not from the test suite (wrong role ids).
- **Scope**: invoke `send_task_assigned_notification` / `send_timeout_notification`
  / `send_approval_notification` / `send_failure_notification` /
  `send_rollback_notification` from their real lifecycle call sites; follow the
  `AlertRouter` precedent in `apps/worker/assisted_listing_intake/worker.py`
  rather than inventing a second integration idiom; use the six canonical role
  ids `executive`, `operations_manager`, `region_director`, `store_manager`,
  `finance_auditor`, `system_admin`.
- **Acceptance** (comma-free so it survives `TASK_ACCEPTANCE` CSV parsing):
  - each of the five spec triggers has at least one non-test caller
  - a test asserts one delivery per canonical role id for all six roles
  - the evidence note states plainly that the archived parent shipped helpers
    without lifecycle wiring (corrects F11)
  - `ODP-PLAN-UAT-SIGNOFF-001` can cite receipts by canonical role id
- **Depends on**: nothing. **Unblocks**: `ODP-PLAN-UAT-SIGNOFF-001`.

#### Task B — `ODP-CAP-NOTIFICATION-INBOX-INTEGRATION-001`

- **Scope**: reconcile the two in-app inboxes (F12) and satisfy AC-3's
  queryability half (F9). Either back `GET /shell/notifications` with
  `notification_inapp_inbox`, or expose the module's inbox under the AC-3 paths —
  but do not ship a third inbox. Also correct the acceptance packet's stale §1.
- **Acceptance**: an item written by `InAppNotificationAdapter.send()` is
  retrievable through an HTTP API by the receiving role; acknowledging it through
  that API flips `acknowledged` in the store the adapter wrote; the acceptance
  packet no longer claims the email/in-app adapters are absent.

#### Task C — `ODP-CAP-NOTIFICATION-DELIVERY-SEMANTICS-001`

- **Scope**: delivery-outcome observability (F3 + F13), AC-5 fan-out semantics
  (F7), and the parent reviewer's N1–N4 hardening set.
- **Acceptance**: a caller can distinguish total delivery failure from success
  and from suppression; the `dlq-spike` path acts on that signal; the AC-5
  escalation semantics are either implemented across all configured channels or
  the AC text is amended with a recorded decision; N1–N4 closed or explicitly
  risk-accepted.

#### Registration — command, and who may run it

This sidecar **did not register these tasks.** Creating mainline task records is
a governance/registry write, and this helper is scoped to support artifacts only
(`mutates_canonical: false`). The three specs above are written to be registered
verbatim by an agent with mainline authority — chair review, the orchestrator, or
the reviewer:

```bash
TASK_TITLE="Wire the five notification triggers to lifecycle call sites" \
TASK_PHASE="Spec MUST capability (scope decision A)" \
TASK_METADATA_JSON='{"acceptance":["each of the five spec triggers has at least one non-test caller","a test asserts one delivery per canonical role id for all six roles","evidence records that the archived parent shipped helpers without lifecycle wiring","ODP-PLAN-UAT-SIGNOFF-001 can cite receipts by canonical role id"],"artifacts":["modules/notifications/application/service.py","tests/reliability/test_notifications.py"]}' \
AI_NAME=<registering-agent> "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" \
  assign ODP-CAP-NOTIFICATION-TRIGGER-WIRING-001 <owner> <reviewer>
```

Two mechanics worth knowing before running it:

- `TASK_ACCEPTANCE` and `TASK_ARTIFACTS` are parsed by `parse_csv_env`
  (`scripts/ai_status.py:1428`), which splits on commas — any acceptance
  sentence containing a comma will be silently shredded into fragments. Passing
  them inside `TASK_METADATA_JSON` avoids this, because
  `task.update(metadata)` runs after the CSV fields are set.
- `assign` creates the task with `status: "todo"` and `next: "Assignment created"`;
  it does not start it.

### 13.8 Round 5 verification

```bash
# 0. No detached pin this round — the parent is on dev, and this branch's
#    post-base-advance head contains the dev tip. Prove the surfaces match:
git fetch origin
git diff --name-only HEAD origin/dev -- modules/notifications \
  tests/reliability/test_notifications.py infra/db/migrations apps/api/app/routes

# 1. Parent terminal state
python3 -c "import json;d=json.load(open('$PANTHEON_STATUS_ROOT/ai-task-archive/tasks/ODP-CAP-NOTIFICATION-DELIVERY-001.json'));print(d['terminal_status'],d['archived_at'],d['task']['approved_head'])"
git merge-base --is-ancestor a8700b00 origin/dev; echo "parent-on-dev: $?"

# 2. Code immobility through to the final head
git diff --numstat 4fd5f7ee a8700b00
git diff --numstat c73a6710 a8700b00 -- modules/notifications \
  tests/reliability/test_notifications.py infra/db/migrations
wc -l modules/notifications/infrastructure/adapters.py \
      modules/notifications/application/service.py \
      modules/notifications/infrastructure/repositories.py \
      tests/reliability/test_notifications.py

# 3. Suite + lint on dev's copy
/home/lupin/.local/bin/uv run pytest tests/reliability/test_notifications.py
/home/lupin/.local/bin/uv run ruff check \
  modules/notifications tests/reliability/test_notifications.py

# 4. F6 / F10 probes
grep -rn --include='*.py' --include='*.ts' --include='*.tsx' \
  -e send_task_assigned_notification -e send_timeout_notification \
  -e send_approval_notification -e send_failure_notification \
  -e send_rollback_notification .
grep -rn "operations_manager\|region_director\|finance_auditor\|system_admin" \
  modules/notifications/ tests/reliability/test_notifications.py

# 5. F9 / F12 probes — the two-inbox split
grep -rn "notification_inapp_inbox" --include='*.py' --include='*.sql' .
grep -rn --include='*.py' --include='*.ts' --include='*.tsx' \
  -e save_inapp_item -e get_inapp_items -e acknowledge_inapp_item \
  -e InAppNotificationAdapter .
grep -rn "from modules.notifications" apps/api/
grep -n "shell/notifications" apps/api/app/routes/operator_modules/shell.py

# 6. F13 probe — production consumers of the service
grep -rn --include='*.py' -e "NotificationService(" -e get_notification_adapter . \
  | grep -v '^\./modules/notifications' | grep -v '^\./tests'

# 7. Absorb path closed
grep -n "is archived. Create a new follow-up task" \
  "$PANTHEON_STATUS_ROOT/scripts/ai_status.py"

# 8. Parent closed without absorbing
git ls-tree -r --name-only origin/dev \
  -- docs/evidence/completion/ODP-CAP-NOTIFICATION-DELIVERY-001/
git grep -l "ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW" origin/dev
```

**Recorded results (owner run, round 5, 2026-08-08, `dev` = `af4650d9`):**

| # | Command | Result |
|---|---|---|
| 0 | surface diff `HEAD` vs `origin/dev` | **empty** — probes in this worktree are probes of `dev` |
| 1a | archive read | `done` · `2026-08-07T00:10:50Z` · `a8700b00638e90df70409d68164900cc1f89b76d` |
| 1b | `merge-base --is-ancestor a8700b00 origin/dev` | **exit 0** — parent landed (rounds 1–4: exit 1) |
| 2a | `git diff --numstat 4fd5f7ee a8700b00` | 2 files, 214 ins / 8 del, both `.orchestrator/*` incoming `dev` content |
| 2b | notification-surface diff `c73a6710..a8700b00` | **empty** — no notification code moved across four rounds |
| 2c | `wc -l` on the four modules | 801 / 319 / 246 / 607 — identical to §7.2, §9.2, §11.2 |
| 3a | `pytest tests/reliability/test_notifications.py` | **27 passed in 1.45s** (rounds 2–4: 27; round 1: 10) |
| 3b | `ruff check` | **All checks passed!** |
| 4a | five-trigger grep | definitions at `service.py` L206/230/252/275/300; calls only in `tests/reliability/test_notifications.py`. **Zero production callers — F6 stands** |
| 4b | canonical role-id grep | **zero hits — F10 stands** |
| 5a | `notification_inapp_inbox` grep | only the 2 migrations + `repositories.py` L173/200/238/243 — **no reader outside the module (F12)** |
| 5b | in-app adapter/method grep | only `modules/notifications/` + `tests/reliability/test_notifications.py` |
| 5c | `from modules.notifications` in `apps/api/` | **no hits** — the API layer never imports the module |
| 5d | `shell/notifications` routes | L328 GET inbox · L352/374 preferences · **L403 POST acknowledgement** — a real inbox API over a *different* store (**corrects round 4's F9 wording**) |
| 6 | production service consumers | `delivery_toolchain/e2e/generate_observability_evidence.py:75` **and** `apps/worker/assisted_listing_intake/worker.py:159` — the second was missed by rounds 1–4 (**F13**) |
| 7 | archived-id guard | `scripts/ai_status.py:4608` — absorb path mechanically closed |
| 8a | parent evidence dir on `dev` | 3 files, none mentioning F3/F6/F7/F9/F10 |
| 8b | `git grep -l <this packet> origin/dev` | **no files** — never landed |

Probes were read-only and used public APIs only; the F3/F7 probe scripts were
deleted after the run and are reproducible from the code paths cited above. This
branch changes exactly one file.

### 13.9 What changed in the recommendation

| Round 4 said | Round 5 says |
|---|---|
| "The parent needs one authorized merge; one operator action clears both" | **Expired.** The parent merged at `44109779` and is archived `done`. Only PR #665 remains open, and only this sidecar's own approval gates it. |
| "On approval, the parent owner may absorb this packet — carrying F3/F6/F7/F9/F10 across is the point" | **Unexecutable and replaced.** The parent has no active owner and its id is refused by `assign` (§13.6). The five are routed to three **named follow-up tasks** in §13.7. |
| "F9: AC-3's API half does not exist" | **Corrected and sharpened.** An inbox read/ack/preferences API *does* exist at `operator_modules/shell.py` L328/352/374/403 — over a **disjoint store**. The real gap is **F12**: `notification_inapp_inbox` has no reader in the product, so the API that exists cannot see what the delivery path writes. |
| "F3 is non-blocking and scheduled" | **Still non-blocking, no longer theoretical.** **F13**: the `dlq-spike` DLQ alert path (`worker.py:159` → `AlertRouter` → `send_notification`) is a live `danger`-severity production caller that discards the outcome. Rounds 1–4's "exactly one non-test construction site" claim was incomplete. |
| "F6+F10 gate `ODP-PLAN-UAT-SIGNOFF-001`" | **Unchanged and now the only actionable item on the critical path.** Routed to task **A**, which is the single highest-value follow-up in this packet. |
| "Combined non-blocking backlog: F3, F9, F10, N1–N4" | **F3, F6, F7, F9, F10, F11, F12, F13 + N1–N4**, each with exactly one named destination (§13.7). |
| — | **New:** the parent shipped, so §3/§7/§11 now describe `dev`. Everything in this packet is a statement about mainline, which is why leaving it unmerged and unrouted is the actual risk. |

---

## 14. Round 5 Handoff (standing)

- **Owner**: `Claude2` · **Reviewer**: `Claude3` (reviewer field refreshed this
  round; rounds 1–4 recorded `Antigravity4` — see the note under the header)
- **Why re-review**: `Claude3` reopened round 4 on `2026-08-08T09:48:21Z`, not
  for scope or drift — the packet was byte-identical to `approved_head`
  `8151be69` — but because the parent reached terminal state after round 4,
  making §12's standing handoff unexecutable. Round 5 answers the reopen's three
  asks directly, and this round also composes a base advance (`origin/dev` →
  `af4650d9`, merge `7ffbb0f5`, conflict-free).
- **The reopen asks, and where each is answered**:

  | Ask | Answered in |
  |---|---|
  | re-pin to `a8700b00` | header pin table (Round 5 column) + **§13.2**, with the archive record and an independent `merge-base --is-ancestor` check; immobility to the final head proved in **§13.3** |
  | record that the parent closed without absorbing F3/F6/F7/F9/F10 | **§13.5** — 3 evidence files, none mentioning them; zero references to this packet anywhere on `dev`; plus the acceptance-vs-review asymmetry |
  | route F6+F10 to a named follow-up task instead of a reviewer handoff | **§13.7** — `ODP-CAP-NOTIFICATION-TRIGGER-WIRING-001` (task **A**), with scope, comma-safe acceptance, and the exact `assign` command. Two further tasks **B**/**C** home the remaining findings so nothing points at the archived parent |
  | refresh the stale reviewer field | header (`Reviewer: Claude3`) + a note explaining the `Antigravity4` → `Claude` → `Claude3` helper-claim chain, with §6/§8/§10/§12 left as audit records |

- **Sidecar scope compliance**: this branch still touches exactly one path,
  `support/sidecars/ODP-CAP-NOTIFICATION-DELIVERY-001/ODP-CAP-NOTIFICATION-DELIVERY-001-SIDECAR-REVIEW.md`.
  No canonical truth, contract, runtime, registry, or governance file is touched
  — including the three follow-up tasks, which are **specified, not registered**
  (§13.7 "Registration"). Confirm with `git diff --stat origin/dev...HEAD` → one
  file.
- **Reviewer diff shortcuts**:
  - versus the previously reviewed head — `git diff ddde7a06 HEAD`
  - the base advance alone — `git log --oneline ddde7a06..HEAD`
  - no finding record deleted — `git diff --numstat ddde7a06 HEAD -- <packet>` is
    **575 ins / 10 del**, and
    `git diff ddde7a06 HEAD -- <packet> | grep -E '^-' | grep -v '^---'` shows all
    10 removals are lines rewritten in place: 3 header fields (parent-task status,
    reviewer, packet revision), 1 pin-table header cell (`Round 4 pin (current)`
    → `Round 4 pin`), the 4-line §3 matrix notice, 1 line closing the §5 notice,
    and the §12 heading. §1–§11 findings are untouched; §13 corrects round 4's F9
    wording **by addition** (§13.4) rather than by editing §11.4.
- **What is substantively new**: **§13** and this section. Beyond answering the
  reopen, §13 carries three findings rounds 1–4 did not have — **F11** (the
  archived closeout note describes uninvoked helpers as "trigger wiring"),
  **F12** (two disjoint in-app inboxes; the parent's table has no reader in the
  product), and **F13** (a live `danger`-severity DLQ alert path calls
  `send_notification` and discards the outcome, which is F3 in production) — plus
  a correction to round 4's F9 description and to the rounds-1–4 claim that the
  service had one non-test construction site.
- **The one thing to check if you read nothing else**: **§13.4 and §13.7.** All
  five previously-open findings are now properties of `dev`, not of a pending
  branch, and every open item has exactly one named destination. If any routing
  in §13.7 belongs elsewhere — in particular whether task **A** should be P0 and
  registered as a blocker of `ODP-PLAN-UAT-SIGNOFF-001` — that is the
  highest-value correction available to this round.
- **Known lane condition, recorded not fixed**: PR #665 is `OPEN`, not draft,
  `MERGEABLE`, and 4/4 CI checks are green at `ddde7a06`
  (`orchestrator`, `product`, `performance-gate`, `product-e2e-gate`);
  `mergeStateStatus` is `BLOCKED` solely because `task-review-gate` went
  `FAILURE` when the task was reopened. That is the expected state for a reopened
  task and it clears on re-approval at the new head. `gh pr merge` remains denied
  to background workers by the permission classifier, so once the gate is green
  the merge needs the reviewer, an operator, or the merge queue.
- **Next Action**: `re_review` to `Claude3`. **There is no parent absorb step any
  more** — on approval and after PR #665 merges, the owner closes this task with
  `done`, and the surviving work leaves this lane as the three follow-up tasks in
  §13.7, whose registration needs an agent with mainline authority.
