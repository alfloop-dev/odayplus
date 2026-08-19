# Sidecar Review Packet: ODP-SEC-DENY-SURVIVES-AUDIT-001-SIDECAR-REVIEW

- **Task ID**: `ODP-SEC-DENY-SURVIVES-AUDIT-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-SEC-DENY-SURVIVES-AUDIT-001`
- **Parent Title**: Keep an operator denial from becoming a 500 when the audit sink fails
- **Parent Owner / Parent Reviewer**: `Antigravity` / `Claude2`
- **Helper Kind**: `review_packet`
- **Sidecar Owner**: `Claude3`
- **Sidecar Reviewer**: `Antigravity`
- **Phase**: Unassigned
- **Parent head reviewed by this packet**: `6f3bf784` (PR #689)
- **Last Updated**: 2026-08-07 (first round)

---

## Executive Summary

This support sidecar is a review packet for parent task
`ODP-SEC-DENY-SURVIVES-AUDIT-001`. It describes the parent diff as it actually
exists, records the verification commands that were run and their observed
output, and lists the residual risk the parent diff does not cover.

**Head under review: `6f3bf784`.** This is the parent's `review_gate_sha` in
`ai-status.json` and the tip of `origin/task/ODP-SEC-DENY-SURVIVES-AUDIT-001`,
which is the head of PR #689. There is exactly one substantive commit on the
branch and no `dev` merges.

The parent fix is **one layer**: `_record_operator_denial` in
`apps/api/oday_api/security/dependencies.py` now wraps the audit write in
`try/except Exception` and logs instead of propagating. Three regression tests
cover it, and all three genuinely fail on unfixed source.

The fix is correct and the tests are real. The finding this packet adds is a
**scope** one, not a correctness one: the same defect — audit write on a denial
path, unguarded, immediately before the raise — exists at **three other sites**,
one of them 180 lines above the fix in the same file, on the same app-level
audit log. At the approved head, `GET /api/v1/interventions` with a failing
sink still returns **500 instead of 403** (§4.4). See R1–R3.

Sidecar scope: support artifacts only. No canonical L1 truth, contract, runtime,
registry, or governance file is modified by this task.

### Parent diff under review

```
$ git diff --stat origin/dev...6f3bf784
 apps/api/oday_api/security/dependencies.py        | 29 +++++++++++++-
 tests/security/test_operator_security_platform.py | 47 +++++++++++++++++++++++
 2 files changed, 75 insertions(+), 1 deletion(-)

$ git log --oneline origin/dev..6f3bf784
6f3bf784 ODP-SEC-DENY-SURVIVES-AUDIT-001: keep an operator denial from becoming a 500
```

### PR #689 state at the time of writing (2026-08-07T13:17Z)

| Field | Value |
|---|---|
| base / head | `dev` / `6f3bf784` |
| `mergeStateStatus` | `CLEAN` |
| `orchestrator` | pass |
| `performance-gate` | pass |
| `product-e2e-gate` | pass |
| `task-review-gate` | pass — *"Approved by assigned reviewer Claude2"* |
| `product` | **pending** |

---

## 1. Defect Analysis & Root Cause

### The primitive

`_record_operator_denial` is called immediately before the raise on every
denial path of `require_operator_permission`:

```python
_record_operator_denial(active_engine, access, decision)
_raise_forbidden(decision)          # ← never reached if the line above throws
```

Python evaluates the audit call to completion before the `raise` statement is
reached. So an exception from the audit sink does not merely lose the audit
record — it *replaces the response*. The `HTTPException(403)` is never
constructed, the exception propagates out of the FastAPI dependency, and the
app's error middleware turns it into a generic 500.

### What the caller actually sees, pre-fix

Reproduced directly (§4.3), with `raise_server_exceptions=False` so the real
HTTP response is visible rather than the re-raised exception:

```
status = 500
body   = {"detail":"Internal server error","error":{"code":"internal_error",
          "message":"Internal server error",
          "next_action":"Retry later; if it persists, escalate with the correlation ID.",
          ...}}
```

Two things are lost, not one:

1. **The status class flips.** `403 Forbidden` (client was refused) becomes
   `500 Internal Server Error` (server is broken). A client, a retry policy, and
   an SLO dashboard all read those differently — 500 is retryable and pages an
   on-call; 403 is neither.
2. **The decision reason is gone.** `_raise_forbidden` sets
   `detail=decision.reason`; the 500 body carries only
   `"Internal server error"`. The refusal happened but nothing records *why* —
   not in the response, and not in the audit log either, since the write is what
   failed.

Note the direction of the failure: this is **not** a fail-open. Access is still
refused in both cases. What is wrong is the *report* of the refusal.

### Why the trigger is realistic

The audit sink is not a local list in production. A WORM-backed or remote sink
can be unavailable, and `AuditRecorder.record` is not declared total. The parent
commit records that this surfaced as an intermittently failing e2e — expected
403 on `GET /operator/network-listings/intake`, received 500 — which is the
signature of a flaky sink rather than a flaky test.

---

## 2. Parent Implementation Assessment

### The fix

```diff
+import logging
...
+_LOGGER = logging.getLogger(__name__)
...
 def _record_operator_denial(engine, access, decision) -> None:
+    """Record a denial without letting the recording decide the response. ..."""
     from shared.audit.policy import build_security_event

-    engine.audit_log.record(build_security_event(access, decision))
+    try:
+        engine.audit_log.record(build_security_event(access, decision))
+    except Exception:
+        _LOGGER.exception(
+            "operator denial audit failed; denial still enforced "
+            "(policy_id=%s reason=%s actor=%s resource=%s)",
+            decision.policy_id, decision.reason,
+            access.principal.subject_id, access.resource.type,
+        )
```

Assessment of the choices:

- **One helper, five call sites.** All five denial paths in
  `require_operator_permission` (lines 579, 587, 592, 600, 605) route through
  this one function, so a single `try` fixes all of them. This is the right
  place to put the guard.
- **`except Exception`, not `except BaseException`.** Correct.
  `KeyboardInterrupt` / `SystemExit` still propagate.
- **`_LOGGER.exception`** emits the traceback plus the decision context, so an
  unaudited denial is still discoverable. Verified as actually emitted (§4.5).
- **No PII or credential leak in the log line.** `policy_id` and `reason` are
  policy strings; `subject_id` and `resource.type` already appear in the audit
  event itself. No token material.
- **Denial paths only.** `_record_operator_denial` is never called on an allow
  path, so the `except` cannot mask an audit failure on a permitted request.

### Test coverage added (`tests/security/test_operator_security_platform.py`)

Three tests plus a `_FailingAuditLog(InMemoryAuditLog)` whose `record` always
raises. File goes 4 tests → 7.

| Test | Endpoint | Denial site exercised | `policy_id` / `reason` observed | Fails pre-fix? |
|---|---|---|---|---|
| `test_denial_survives_an_unavailable_audit_sink` | `/operator/network-listings/intake` | site 4 — RBAC (line 600) | `rbac` / `role does not permit view on listing` | **yes** |
| `test_unauthenticated_denial_survives_an_unavailable_audit_sink` | `/operator/bootstrap` | site 1 — authenticated (line 579) | `authenticated` / `principal not authenticated` | **yes** |
| `test_tenant_scope_denial_survives_an_unavailable_audit_sink` | `/operator/bootstrap` + `X-Tenant-Id: tenant-b` | site 5 — scope decision (line 605) | `operator.tenant_isolation` / `Operator Console **scope mismatch**` | **yes** |

All three fail on unfixed source (§4.2), so they pin *this* diff rather than a
path that already passed. That is the single most important property of the
parent's test set and it holds.

**Correction to the parent commit message.** The commit body says:

> Three of the five call sites are covered by tests: RBAC denial, unauthenticated
> 401, and tenant-scope 403. **Role-selection and scope denials** route through
> the same helper and are fixed by the same change, but have no dedicated test here.

The count (3 of 5) is right; the attribution is not. The two sites that use
`policy_id="operator.tenant_isolation"` are distinguishable only by `reason`:

- site 2, `not effective_tenant_id` → `"Operator Console tenant scope is required"`
- site 5, `_operator_scope_decision` → `"Operator Console tenant scope mismatch"`

The test observes **`scope mismatch`** (§4.6), so the covered site is **5
(scope)**, not 2. `/operator/bootstrap` is guarded by `operator_view_guard`,
which pins `tenant_id=OPERATOR_TENANT_ID` (`"tenant-a"`,
`dependencies.py:335`), so `effective_tenant_id` is always truthy there and
site 2 cannot fire on that route at all.

The genuinely untested pair is therefore **site 2 (tenant-required)** and
**site 3 (role selection)** — not "role-selection and scope". Both are verified
to survive a failing sink by direct probe in §4.5, so this is a wording fix for
the commit/PR body, not a coverage gap that changes the verdict.

### Review observations on the parent diff

1. **The `try` wraps event *construction* as well as the sink write.**
   `build_security_event(access, decision)` is evaluated inside the `try`, so a
   future `TypeError`/`AttributeError` in event construction would also be
   demoted to a log line. That is arguably desirable here (the denial still
   stands), but it means a programming error in the audit-event builder becomes
   silent-ish rather than loud. Narrowing to
   `event = build_security_event(...)` outside the `try` and only
   `engine.audit_log.record(event)` inside would keep construction bugs
   surfacing normally. Non-blocking, one-line change.

2. **Nothing asserts the log is emitted.** The docstring's central promise —
   *"The failure stays loud"* — has no test. Replacing the `except` body with
   `pass` would keep all three new tests green. A `caplog`/`assertLogs`
   assertion on one of the three would pin the promise. This is the one
   test-side gap worth acting on; it is cheap. (Verified separately in §4.5 that
   the log *is* emitted today.)

3. **Redundant assertion in test 1.** `assert with_failing_sink.status_code ==
   status.HTTP_403_FORBIDDEN` followed by `assert with_failing_sink.status_code
   == baseline.status_code`, where `baseline` was already asserted to be 403.
   Harmless; cosmetic.

4. **Log-volume note.** Under a sustained sink outage combined with a burst of
   denials (credential-stuffing, scanner traffic), every denial now writes a
   full ERROR traceback. Previously each one wrote an unhandled-exception log
   anyway, so this is not a regression — but if the sink outage is long, this is
   the loop that fills the log budget. Worth a rate-limit or a
   `logger.error(..., exc_info=False)` variant only if it ever bites.
   Non-blocking.

5. **What the fix explicitly does not do.** It does not make the audit record
   durable. Post-fix, a denial during a sink outage is `403` + no audit record +
   an ERROR log. If the compliance requirement is "every 403 produces an audit
   event", that needs an outbox/retry queue, which is out of this task's scope.
   The fix's claim is narrower and correct: an audit failure must not decide the
   HTTP response.

---

## 3. Acceptance Matrix

The parent task record carries no explicit `acceptance` array, so rows A1–A5 are
derived from the commit message's own claims.

### Implemented and covered

| Ref | Acceptance rule | Evidence | Result |
|---|---|---|---|
| **A1** | A failing audit sink no longer converts an operator denial into a 500 | §4.1 (7 passed), §4.3 (500 pre-fix → 403 post-fix) | PASS |
| **A2** | The new tests pin the defect rather than a passing path | §4.2 — all 3 fail with only `dependencies.py` reverted | PASS |
| **A3** | All five `_record_operator_denial` sites survive a failing sink | §4.5 — direct probe of 4 reachable sites; site 5 via §4.6 | PASS |
| **A4** | The audit failure stays visible in logs | §4.5 — traceback emitted per denial | PASS (untested by the suite — obs. 2) |
| **A5** | No collateral regression in `tests/security` | §4.7 — 214 passed, 7 skipped; 4 pre-existing env failures | PASS |

### Residual risk — the same defect at three other sites

Each row below was **observed**, not inferred from reading.

| Ref | Residual risk | Observed at `6f3bf784` | Severity / disposition |
|---|---|---|---|
| **R1** | `require_permission` — the sibling RBAC dependency in the **same file**, `dependencies.py:301` — writes `active_engine.audit_log.record(...)` unguarded, immediately before `_raise_forbidden`. Identical shape, identical sink (`build_engine(audit_log=active_audit_log)`). | `GET /api/v1/interventions` with a failing sink → **500**; healthy sink → 403 (§4.4). Direct call → `RuntimeError` instead of `HTTPException 403`. | **Open, medium.** Widest blast radius of the three: ~40 route registrations across `interventions`, `learninghub`, `adlift`, `heatzone`, `audit`, and the **franchisee guards on Operator Console itself** (`operator.py:479/484/938/941`). The parent task's own product surface is therefore only half-covered. |
| **R2** | `modules/listing/application/intake_authorization.py:128` — `_raise_and_audit()` records then raises. Same shape. | Direct call with anonymous principal: healthy sink → `HTTPException 401 AUTHENTICATION_REQUIRED`; failing sink → `RuntimeError` (§4.4). | **Open, medium.** 30 call sites in `apps/api/app/routes/listings.py`, all passing `audit_log=active_audit_log` — the same app-level sink. Not routed in the default test-app config, so proven by direct call, not over HTTP. |
| **R3** | `modules/opsboard/auth/boundary.py:324` — `_finalize()` records on **every** authentication outcome, before returning. | Direct call: healthy → `authenticated=False reason=no_credentials`; failing → `RuntimeError` (§4.4). | **Open, low today.** `default_boundary()` constructs `AuthenticationBoundary(config)` with **no** `audit_log`, so it defaults to a process-local `InMemoryAuditLog` — a WORM outage does not reach it. Two consequences worth the parent owner's attention: (a) the trigger for R3 is not the same trigger as R1/R2, so it is not urgent; (b) it means operator *authentication* events are not reaching the durable sink at all, which is a separate audit-completeness question, out of this task's scope. |

**Why R1 matters to this parent specifically.** `require_operator_permission`
(fixed) and `require_permission` (not fixed) both guard Operator Console routes —
`operator.py:929` even carries a comment explaining that the franchisee guards
deliberately use `require_permission` "not the operator variant". So the fix
closes the defect on the operator-role paths of the Operator Console while
leaving it open on the franchisee paths of the same console, reachable through
the same failing sink. That is not an argument to block the parent — the fix is
strictly an improvement and its scope is coherently described — but the parent
owner should decide consciously whether to widen the diff or open a follow-up,
rather than closing the task believing the class is eliminated.

**Recommendation:** approve the parent on its stated scope; open one follow-up
covering R1 and R2 together (they are the same one-line pattern and share the
sink), and fold review observation 2 (`caplog` assertion) into whichever change
lands. A shared helper — `_record_denial(recorder, event)` — would let all four
sites share one guard instead of four copies of the same `try`.

### Closeout hygiene note (not a defect in the code)

The parent commit's trailers name `LLM-Agent: Claude` / `Reviewer: Antigravity2`,
while `ai-status.json` records owner `Antigravity` / reviewer `Claude2`, and
`task-review-gate` reports *"Approved by assigned reviewer Claude2"*. The
owner/reviewer pair was evidently swapped after the commit was written. Flagging
it because trailer/assignment mismatches have previously blocked `done`; if the
parent's closeout rejects the trailers, a fresh commit carrying the current pair
is the fix. `product` was still `pending` at the time of writing, so the PR
cannot merge yet regardless.

---

## 4. Verification — commands run and observed output

All commands run 2026-08-07 by `Claude3` in a throwaway worktree pinned at the
parent head, created with
`git worktree add --detach /tmp/odp-sec-verify 6f3bf784` and removed afterwards.
Nothing outside that worktree was modified; `git status --short` was confirmed
empty after every revert/restore cycle.

Runner note: `pytest -q` in this environment prints the progress dots but
suppresses the trailing `N passed` summary. Counts below come from runs
**without** `-q`, which report them explicitly. (Same environment quirk the
`ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001` packet hit.)

### 4.1 Suite at the approved head

```
$ git rev-parse HEAD
6f3bf7842cb7d3ba92a3356b0cb5ee804c85ecef

$ python3 -m pytest tests/security/test_operator_security_platform.py
======================== 7 passed, 1 warning in 45.04s =========================
```

### 4.2 Regression check — do the new tests fail without the fix?

Only the source file is reverted; the test file stays at the parent head.

```
$ git checkout origin/dev -- apps/api/oday_api/security/dependencies.py
$ git status --short
M  apps/api/oday_api/security/dependencies.py

$ python3 -m pytest tests/security/test_operator_security_platform.py
ERROR    oday-api.errors:errors.py:460 Unhandled exception on GET /api/v1/operator/network-listings/intake
ERROR    oday-api.errors:errors.py:460 Unhandled exception on GET /api/v1/operator/bootstrap
ERROR    oday-api.errors:errors.py:460 Unhandled exception on GET /api/v1/operator/bootstrap
FAILED tests/security/test_operator_security_platform.py::test_denial_survives_an_unavailable_audit_sink
FAILED tests/security/test_operator_security_platform.py::test_unauthenticated_denial_survives_an_unavailable_audit_sink
FAILED tests/security/test_operator_security_platform.py::test_tenant_scope_denial_survives_an_unavailable_audit_sink
=================== 3 failed, 4 passed, 1 warning in 48.88s ====================

$ git checkout HEAD -- apps/api/oday_api/security/dependencies.py
$ git status --short          # empty
```

**3 of 3 new tests fail pre-fix, 0 of the 4 pre-existing tests regress.** The
structured error log confirms the mechanism:

```json
{"level":"ERROR","service":"oday-api","result":"error","error_code":"RuntimeError",
 "message":"HTTP GET /api/v1/operator/network-listings/intake failed","retryable":false}
```

### 4.3 The actual HTTP response pre-fix vs post-fix

`TestClient` re-raises server exceptions by default, which is why the tests fail
with a `RuntimeError` rather than an assertion on 500. With
`raise_server_exceptions=False` the real response is visible:

**Pre-fix** (`dependencies.py` reverted to `origin/dev`), failing sink,
`GET /api/v1/operator/network-listings/intake` with `OPS_HEADERS`:

```
status = 500
body   = {"detail":"Internal server error","error":{"code":"internal_error", ...}}
```

**Post-fix**, same request, same failing sink:

```
RESULT operator guard / healthy sink   -> 403
RESULT operator guard / FAILING sink   -> 403
```

The pairing is the core evidence in this packet: the defect reproduces on
unfixed source and the fix holds the status class, with the denial reason
restored to the body.

### 4.4 Residual-risk probes at the **fixed** head

R1, over HTTP (`X-Subject-Id: nobody`, `X-Tenant-Id: tenant-a`):

```
RESULT GET  /api/v1/interventions      healthy  -> 403
RESULT GET  /api/v1/interventions      FAILING  -> 500       # <-- R1
```

R1, R2, R3 by direct call:

```
RESULT require_permission        / healthy sink -> HTTPException: 403 role does not permit approve on intervention
RESULT require_permission        / FAILING sink -> RuntimeError: worm sink unavailable          # R1

RESULT intake_authorization      / healthy      -> HTTPException: 401 AUTHENTICATION_REQUIRED
RESULT intake_authorization      / FAILING      -> RuntimeError: worm sink unavailable          # R2

RESULT boundary.authenticate     / healthy      -> authenticated=False reason=no_credentials
RESULT boundary.authenticate     / FAILING      -> RuntimeError: worm sink unavailable          # R3
```

Site inventory backing R1 (`grep -n` at `6f3bf784`):

```
apps/api/oday_api/security/dependencies.py:301   active_engine.audit_log.record(...)   # require_permission — UNGUARDED
apps/api/oday_api/security/dependencies.py:484   engine.audit_log.record(...)          # _record_operator_denial — guarded by this diff
```

### 4.5 All five denial sites, direct probe (fixed head)

`require_operator_permission` invoked with a stub request; each case run twice,
once with `InMemoryAuditLog` and once with the failing sink:

```
RESULT operator 1 not-authenticated (401)     healthy=HTTPException/401   FAILING=HTTPException/401
RESULT operator 2 tenant-required (403)       healthy=HTTPException/403   FAILING=HTTPException/403
RESULT operator 3 operator.role (403)         healthy=HTTPException/403   FAILING=HTTPException/403
RESULT operator 4 rbac (403)                  healthy=HTTPException/403   FAILING=HTTPException/403
```

Sites 2 and 3 are the two with no dedicated test (§2); they behave identically
to the tested ones, as expected from the shared helper. Site 5 is covered by
§4.6. Each `FAILING` run also emitted the `_LOGGER.exception` traceback to
stderr, which is the evidence for **A4**.

### 4.6 Which denial site each new test actually exercises

`_record_operator_denial` was wrapped in a spy recording
`(policy_id, reason)`:

```
RESULT test1 rbac 403        status=403  [('rbac', 'role does not permit view on listing')]
RESULT test2 unauth 401      status=401  [('authenticated', 'principal not authenticated')]
RESULT test3 tenant-b 403    status=403  [('operator.tenant_isolation', 'Operator Console tenant scope mismatch')]

$ grep -n 'OPERATOR_TENANT_ID = ' apps/api/oday_api/security/dependencies.py
335:OPERATOR_TENANT_ID = "tenant-a"
```

`scope mismatch` (not `scope is required`) is what identifies test 3 as site 5,
per §2.

### 4.7 Wider suite

```
$ python3 -m pytest tests/security
4 failed, 214 passed, 7 skipped, 5 warnings in 293.01s (0:04:53)
```

The 4 failures are all in `tests/security/test_supply_chain_security_gate.py`
and are **environmental**, not caused by the parent diff:

```
E   FileNotFoundError: [Errno 2] No such file or directory: 'uv'
E   AssertionError: SAST scan failed with output:

$ for b in uv ruff bandit semgrep pip-audit; do command -v $b || echo "$b NOT FOUND"; done
uv NOT FOUND / ruff NOT FOUND / bandit NOT FOUND / semgrep NOT FOUND / pip-audit NOT FOUND
```

That file only shells out to the supply-chain toolchain; it does not import
`dependencies.py`. CI runs it with the tools installed — `orchestrator` and
`product-e2e-gate` are green on PR #689.

### 4.8 Not run

- **`ruff check` on the two changed files** — the parent's `Verified:` trailer
  claims this is clean. `ruff` is not installed in this environment
  (§4.7), so the claim is **not independently reproduced here**. CI lint is the
  authority for it.
- **`product` CI job** — still `pending` on PR #689 at the time of writing.
- **Live WORM sink** — the failure is simulated with
  `_FailingAuditLog.record` raising `RuntimeError`, matching the parent's own
  test double. No real WORM outage was induced.

---

## 5. Handoff Note

- **This packet's scope**: support artifact only. The single file touched by this
  sidecar task is
  `support/sidecars/ODP-SEC-DENY-SURVIVES-AUDIT-001/ODP-SEC-DENY-SURVIVES-AUDIT-001-SIDECAR-REVIEW.md`.
  No canonical truth, contract, runtime, registry, or governance file is
  modified.

  ```
  $ git diff --name-only origin/dev...HEAD
  support/sidecars/ODP-SEC-DENY-SURVIVES-AUDIT-001/ODP-SEC-DENY-SURVIVES-AUDIT-001-SIDECAR-REVIEW.md
  ```

- **Base advance**: none. The sidecar branch was opened from `origin/dev` tip and
  was `0 behind / 0 ahead` when work started.

- **Sidecar reviewer `Antigravity`** — the three claims worth checking hardest,
  in order:
  1. **R1** (§3, §4.4). It is the only claim that says something is still broken
     at the approved head. The HTTP probe result — `GET /api/v1/interventions`,
     failing sink, **500** — is reproducible from `dependencies.py:301`; please
     confirm you read that line the same way, and confirm the franchisee-guard
     reachability at `operator.py:479/484/938/941`.
  2. **The §2 correction** to the parent commit message (covered site is 5/scope,
     not 2/tenant-required). This contradicts the parent's own wording, so it
     should be checked against §4.6's `reason` strings and
     `dependencies.py:335`, not taken on trust.
  3. **A4** — the log-emission claim passes on my probe but has no test behind
     it (observation 2). If you think that gap should block, say so; I have it
     as non-blocking.

  Everything else in §3 asserts closure and is backed by the §4.2 pre-fix
  failure run.

- **Parent action**: the parent is at `review` with `task-review-gate` already
  reporting approval by `Claude2`, so this packet is a record and a
  residual-risk list, not a gate. On approval, parent owner `Antigravity`
  decides whether to absorb it into `ODP-SEC-DENY-SURVIVES-AUDIT-001` closeout,
  and whether R1/R2 become one follow-up task or an accepted-risk note. My
  recommendation (§3) is one follow-up covering R1 + R2 + observation 2, since
  they are the same one-line pattern on the same sink.

- **Blocking nothing**: no finding in this packet blocks parent closeout. R1–R3
  are pre-existing conditions the parent diff neither introduced nor widened.
