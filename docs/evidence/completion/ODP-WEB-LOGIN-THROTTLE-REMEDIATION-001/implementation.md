# ODP-WEB-LOGIN-THROTTLE-REMEDIATION-001 implementation evidence

Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §2.2, §6.4

## What was blocked

ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001 accepted Wave Auth as a conditional
pass and recorded two §6.4 blockers:

- **B1** the throttle service had no production call site — `/login` applied no
  throttle at all;
- **B2** the throttle layer had no durable repository, so nothing was shared
  between Cloud Run instances.

## Delivered runtime behavior

`POST /login` (`apps/web/src/app/login/route.ts`) now drives
`apps/web/src/lib/auth/loginThrottle.ts` on every attempt:

- The gate reads and counts the attempt in `identity.login_attempts` **before**
  `authenticateLocalCredentials` runs. Counting up front is what makes the
  control fail safe: an attempt that never reaches a verdict — a crash, a
  timeout, an instance torn down mid-request — stays counted. Only a verified
  success gives it back.
- Five failures per account inside a fifteen minute window lock the account for
  fifteen minutes, doubling on each further lockout round to a sixty minute
  ceiling. The multiplier comes from `lockout_count` (rounds already served),
  not from the in-window failure count: the window is fifteen minutes while a
  lockout runs up to sixty, so binding the multiplier to the failure count
  would reset the escalation whenever the window expired and the doubling
  required by §6.4 would never happen.
- Fifty failures per source IP inside the window reject further attempts from
  it, with a fixed lockout — §6.4 asks the IP dimension only to "reject and
  record", so it does not double.
- A successful login deletes the account row and returns the attempt charged to
  the source IP, which budgets failures only.
- The IP dimension is evaluated first, so a blocked source cannot drive an
  otherwise untouched account towards its own lockout.

### Shared across every Cloud Run instance

`PostgresLoginThrottleStore` is the only thing that touches
`identity.login_attempts` (migration `000011_identity_schema.sql`, already
deployed). Read-modify-write runs inside a transaction with
`SELECT ... FOR UPDATE`, so concurrent instances on the same key serialize and
no increment is lost. The state machine itself is a set of pure functions
shared by the Postgres store and the in-memory store used by non-production
development, so both dimensions behave identically wherever they run.

In production there is no in-memory fallback: with no database URL
`getDefaultLoginThrottle` returns null and `/login` answers
`503 WEB_AUTH_NOT_CONFIGURED` rather than serving an unthrottled login form. An
unreachable store answers `503 WEB_AUTH_UNAVAILABLE` — failing closed, because
an attacker who can disturb the database must not thereby switch the throttle
off.

### No account-existence disclosure

The account key is derived from the **submitted username**, never from a
resolved account. The gate has to run before verification, and resolving an
account first would mean unknown usernames could not be throttled at all —
both a bypass and an enumeration oracle. Because an unknown username is
counted and locked exactly like a real one, the refusal (`AUTH_ACCOUNT_LOCKED`
423, or `AUTH_RATE_LIMITED` 429 for the IP dimension) carries no signal about
whether the account exists. `POST /login is throttled …: throttles an unknown
username exactly like a real one` asserts the two response sequences are
byte-identical through lockout.

The pre-existing post-verification `AUTH_ACCOUNT_LOCKED` for
`accounts.status = 'locked'` is unchanged and still only reachable after a
correct password.

### `attempt_key` holds no plaintext identifier

Per §2.2 the plaintext client IP is never stored. `attempt_key` is
`account:<hex>` / `ip:<hex>` where the digest is HMAC-SHA256 over a
dimension-tagged message. The username is digested too, so mistyped passwords
that land in the username field do not accumulate in the table.

The pepper defaults to `ODP_WEB_SESSION_SECRET`, which is already required in
every mode and never leaves the server, so no new deployment variable becomes
mandatory (`ODP_WEB_LOGIN_THROTTLE_PEPPER` overrides it). Without a pepper the
IPv4 space is small enough that a bare SHA-256 could be reversed offline.
Rotating the session secret re-keys the table and clears in-flight lockouts;
lockouts last at most an hour, so that is an acceptable consequence of a rare
operation.

Addresses are canonicalized before hashing (IPv6 expansion, brackets, trailing
port, case) so equivalent spellings cannot be used to get a fresh budget. The
address is taken from the last `X-Forwarded-For` entry — the one the platform
appends and a client cannot forge — with `ODP_WEB_TRUSTED_PROXY_HOPS` for
deployments that add trusted proxies. When no address can be resolved the IP
dimension is skipped rather than collapsing every caller into one bucket; the
account dimension still applies.

## One mechanism, not two

The Python `shared/identity/login_throttle.py` prototype is retired:
`LoginThrottleService`, `ThrottleRepository`, `LoginAttemptRecord`,
`account_attempt_key` and `ip_attempt_key` are removed from
`shared.identity`, and the T05 / T05b classes are removed from what is now
`tests/identity/test_session_lifecycle.py` (renamed, since it no longer covers
throttling). The production login path is TypeScript; keeping a second
implementation in a runtime that `/login` never calls would be exactly the
parallel mechanism the acceptance forbids.

`tests/security/test_login_throttle_wiring.py` pins all three properties: the
route drives the throttle before verifying credentials (B1), the durable store
over `identity.login_attempts` exists and locks rows (B2), and exactly one
module issues statements against that table.

## Owned boundary

This task owns `apps/web/src/lib/auth/loginThrottle.ts`, the `/login` POST
wiring, the two TypeScript test suites, the security wiring guard, the
retirement of the Python prototype, and the `apps/web/README.md` section. It
does not change the session store, the identity store, the OIDC path, or the
`identity` schema — `identity.login_attempts` already carries every column
this needs.

## The two XFAIL guards

Artifact three asked for the two `xfail(strict=True)` guards in the security
E2E suite to be removed and replaced with passing evidence. **That file is not
reachable from this branch**: `tests/e2e/test_password_first_security_e2e.py`
exists only on `task/ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001` (commit
`ea64f02e`), which is unmerged, unpushed, and owned by another lane. Editing
it from here would take over another task's file and guarantee a conflict.

The guards are also no longer correct as written. They assert Python-side
facts — a `LoginThrottleService` production call site, and
`shared.identity.SqlThrottleRepository` — and the architecture correction of
2026-08-31T13:58:05Z forbids exactly those: no Python `SqlThrottleRepository`,
no cross-runtime call from Next.js, and the Python prototype retired once the
TypeScript throttle ships. Under the shipped architecture both guards would
fail rather than XPASS.

What this branch delivers instead is the passing form of the same two
assertions, restated against the shipped architecture and living in a file
this task owns: `tests/security/test_login_throttle_wiring.py::
test_b1_login_route_drives_the_throttle_before_verifying_credentials` and
`::test_b2_throttle_state_is_durable_in_identity_login_attempts`.

**Follow-up owed by the security E2E lane**: when
ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001 rebases onto a `dev` containing this
change, it must delete its two xfail guards and its
`TestT26LoginThrottleContract` import of `LoginThrottleService` (which no
longer exists), and re-point its strict guards at the TypeScript route and the
durable datastore, as the same architecture note directs.

## Verification

Run on 2026-08-31 UTC at commit `1aec7af7` plus the retirement commit:

```text
npm --prefix apps/web run test -- src/lib/auth/__tests__
Test Files  13 passed (13)
     Tests  83 passed (83)

npm --prefix apps/web run test
Test Files  52 passed (52)
     Tests  426 passed (426)

npm --prefix apps/web run typecheck
tsc --noEmit          (no output)

npm --prefix apps/web run lint
✔ No ESLint warnings or errors

uv run pytest tests/identity tests/security/test_login_throttle_wiring.py -q
74 passed

uv run --python 3.12 ruff check shared tests/identity tests/security/test_login_throttle_wiring.py
All checks passed!
```

29 of the 83 auth tests are new: 17 in
`apps/web/src/lib/auth/__tests__/loginThrottle.test.ts` (keys, address
resolution, thresholds, exponential backoff and its ceiling, window rollover
with escalation retained, cross-instance sharing) and 12 in
`apps/web/src/lib/auth/__tests__/loginThrottleRoute.test.ts` (the route
counting before verification, lockout without further credential work, unknown
versus real username, success clearing the counter, IP block, HTML form
redirect, and both fail-closed 503 paths).

No external data fetching and no OIDC requirement was added.
