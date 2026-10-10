# Dedicated dev smoke provisioning — implementation in progress

Task: `ODP-DEV-SMOKE-ACCOUNT-PROVISIONING-001` · Pi / Codex

**Not ready for review, activation, deployment or acceptance.** This is an
intermediate source anchor, not provisioning evidence. Human authorization
`HUMAN-ODP-DEV-SMOKE-20261010-001` permits the bounded future operation; it is
not evidence that any account, credential binding or deployment changed.

## Anchor 1: internal invitation transaction layer

Base/configuration inspected against fetched `origin/dev`
`10eb6224fa31010fbf3c6f27612ca7d17ba4401e`. The task branch initially matched
that commit with no diffs. The current implementation adds:

- `shared/identity/invitation_service.py`: an internal PostgreSQL application
  service, no CLI, bootstrap reuse, router, or parallel authentication path.
  Issuance/revocation requires an authenticated `platform_admin` principal and
  rechecks the persisted same-tenant active account, role, rotated credential
  and unrevoked/unexpired account-bound session in the write transaction.
- Fixed single `platform_admin` role and tenant scope. No role/scope/actor/tenant
  recipient payload overrides. No business role grants. Recipient chooses the
  username during acceptance; the invitation fixes the email and tenant.
- 256-bit single-use capability, stored only as SHA-256. TTL is a positive
  integer <=72 hours, measured using database time. Capability returned only
  by the in-memory issuance result; its repr and identifier receipt exclude
  it. No email delivery/deliverability claim is made.
- No account until acceptance. Acceptance atomically inserts a new active
  account, the canonical Argon2id credential, role, scope, consumption time and
  `identity.account.accept` audit event. It does not log in or issue sessions.
  Existing accounts/passwords/sessions are never updated or reset.
- Shared tenant administration advisory lock, pending/email/username collision
  refusal, explicit revoke-before-reissue recovery, and same-engine durable
  audit requirement. Errors name static codes only; transport adapters still
  need generic handling of infrastructure exceptions.
- `tests/security/test_dev_smoke_invitation.py`: **unexecuted** PostgreSQL
  regressions reusing the existing identity/auth/session stack. Source cases
  cover authoritative actor/session recheck, exact original inventory
  preservation, password/token receipt absence, expiry, revocation, replay,
  preset tampering, case-insensitive collisions and audit rollback/recovery.
  These are application-layer cases, not invitation-router coverage.

At anchor 1, verification was declared **none** and no tests were run. The
canonical declaration was updated at 2026-10-10 03:27 UTC to require diffcheck,
invitation/integration/workflow regressions and the existing dev-admin gate /
operator authorization regressions. Results must be bound to their measured
head by `task_verification.py` receipts; no test-pass claim is made here.

## Anchor 2: authenticated issuance/revocation adapter

The existing `IdentityUserRoleManagementService` exposes the same-engine
invitation service. Its existing live composition mounts these two routes in
`apps/api/app/routes/operator_modules/users_roles.py`:

- `POST /api/v1/operator/users/invitations` (JSON email / optional lifetime).
- `POST /api/v1/operator/users/invitations/{id}/revoke` (empty JSON object).

Both reuse the existing `user:UPDATE` dependency and require its verified
`request.state.operator_principal`; the core additionally requires persisted
platform-admin and a valid account-bound session. No trusted-header, request
actor, document-store, missing-guard or fake-principal fallback is used.
Strict request fields, bounded JSON (4096 bytes), no input echo on validation
failure, safe infrastructure errors, threadpool execution and no-store responses
protect private capability custody. The token is returned once in the successful
authenticated issuance response, never in an identifier receipt or audit event.

`tests/integration/test_dev_smoke_provisioning.py` now exercises those actual
routers with the existing production boundary / real PostgreSQL fixture, not
permission overrides. Cases cover anonymous/header forgery/wrong-role/rotation/
revocation denial before service calls, strict/oversized/malformed input,
server-fixed tenant/actor, duplicate/revocation recovery, safe audit-failure
rollback and concurrent acceptance through independent PostgreSQL engines.
These are offline source regressions, not account or deployment receipts.

**Acceptance is still internal**, deliberately not exposed as an unfinished
public endpoint. The bounded Web/BFF capability adapter and durable abuse
controls must land before an acceptance HTTP route is enabled. No Web, GH,
workflow, deployment, IAM or gate changes are part of anchor 2.

## Verification repair checkpoint (after anchor 2)

At `92c35e541d161a33cb963b57e583c1603869e140` the canonical runner
first recorded both pytest commands as exit 127: the worker PATH omitted the
standard `/home/lupin/.local/bin` where uv is installed. A same-head retry with
an explicit infrastructure reason and corrected PATH recorded:

- `git diff --check`: exit 0 (receipt `03ab722bbd22d6cf`).
- Declared invitation/integration/workflow command: exit 1, 72.45 seconds,
  six failures (receipt `55e9f4b78546689f`). Real PostgreSQL exposed the core's
  incorrect comparison of normalized JSONB **text** against Python objects.
  Acceptance now explicitly parses JSONB and still rejects any non-exact preset.
  One router test incorrectly claimed the existing must-change check emits a
  durable denial; it now proves only the actual refusal/no-service-call there.
- Declared existing gate/operator authorization command: exit 0, 57.964 seconds
  (receipt `0bafcd49cfd3355e`). No gate change was made.

This repair also refuses invalid/expired/revoked/consumed capabilities before
constructing an expensive Argon2 credential service, while keeping the locked
consumption recheck. The negative capability tests now spy that no hasher is
constructed. The repaired anchor needs its own exact-head receipts; these prior
receipts do not prove the repair passed. All receipts above were produced by
`task_verification.py` under `.orchestrator/evidence/`, not handwritten evidence.

## Anchor 4: bounded acceptance adapters (not activated)

- Added `apps/api/app/routes/identity_invitations.py`, an **unmounted** factory
  for capability-only `POST /api/v1/auth/invitations/accept`. It uses the same
  invitation transaction service, strict bounded DTO validation without secret
  echo, generic infrastructure errors, a threadpool and identifier-only receipts.
  No request identity headers, account creation without a capability, password
  reset, session creation or cookie issuance are supported.
- Added durable acceptance reservations using existing `identity.login_attempts`
  under a distinct namespace. Every attempted service acceptance costs budget
  before Argon2: 50 globally and 5 per existing invitation in 15 minutes, measured
  using DB time and serialized by a PostgreSQL advisory lock across instances.
  The reservation commits separately, surviving refusal/audit rollback. Random
  invitation ids cannot grow per-invitation rows; original login counters and
  account sessions are not changed. Valid capabilities also cost budget. These
  are new acceptance abuse limits, not a change to the existing login policy.
- Added `apps/web/src/app/auth/invitations/route.ts` and declared-path Vitest
  regressions. It requires same-origin JSON, rejects query capabilities, bounds
  request/upstream bodies, strips every browser identity/cookie header and uses
  the canonical Cloud Run transport resolver/header builder. It does not read,
  create or rotate a Web session. Responses project only UUID receipts or static
  approved error codes; arbitrary upstream fields, redirects, secrets and errors
  are never passed through. Password/token custody remains memory-only.
- API and Web source regressions now cover acceptance, safe input/infrastructure
  errors, replay, no cookies/sessions, reservation persistence and DB-time reset.
  **Not yet executed at this anchor.** No pass or live-proof claim is made.
- The runtime factory is intentionally not mounted: middleware currently sends
  anonymous `/auth/invitations` callers to login. Canonical note requests exact
  `apps/web/src/middleware.ts` owned-path expansion before activation; it is not
  edited here. Main composition, OpenAPI/inventory generation and real middleware
  coverage remain pending. `USER-AUTHORIZATION-20261010.json` in this directory
  copies the supplied sanitized authorization, not execution evidence.

## Anchor 4 measured verification / repair checkpoint

Canonical `task_verification.py run` at `aa7ec8b292b68e53400218dfd659c64591493e44`
finished all five declared commands (no background jobs or summary polling):

| Selection | Exit | Duration | Receipt id |
|---|---:|---:|---|
| `git diff --check` | 0 | 0.054s | `a4027f51296a12d1` |
| Invitation/security + provisioning/integration + workflow contract pytest | 1 | 58.335s | `f80dc2ddac0ddbc5` |
| Existing dev-admin gate + operator-read authorization pytest | 0 | 44.530s | `99ef23a2d648645c` |
| Declared invitation Vitest | 127 | 0.001s | `9930d8da823f579d` |
| Web typecheck | 127 | 0.001s | `5c404f7c1688e8cd` |

The single pytest failure was a **test SQL** literal `%` passed to psycopg;
replaced the LIKE pattern with a bound parameter. Web commands never launched:
`pnpm` was absent from PATH, and this fresh worktree had no node dependencies.
Project-standard `npm ci` completed exit 0; Corepack installed a pnpm shim only
in the declared scratch directory. No tracked lock/config files changed.
Additional source repair binds uppercase invitation UUID input to canonical
lowercase receipts without misreporting successful creation as unavailable.
Added an independent-PG-pool budget race regression. These changes need new-head
receipts; the above passes do not attest the repair. Full command strings,
head, exit, duration and output tails are in the canonical runner's existing
`.orchestrator/evidence/verification-odp_dev_smoke_account_provisioning_001-*.json`.

## Anchor 5 measured regression checkpoint

At `a77b6428abfd346e0797692d4abd4e3bb24b2142`, initial canonical runs passed
both pytest selections and diffcheck. pnpm 12 tried to auto-install the npm
workspaces from the public registry and refused unresolved private workspace
packages; neither Web test started successfully. This is not a source failure.
An explicit infrastructure retry activated pnpm 9.15.9 through Corepack and
added the repository's npm-installed `node_modules/.bin` to PATH. It recorded:

| Declared selection | Exit | Duration | Receipt id |
|---|---:|---:|---|
| Diffcheck | 0 | 0.017s | `6f820d2c7617433d` |
| Invitation/provisioning/workflow pytest | 0 | 61.759s | `8e6393b3c67b85e0` |
| Existing gate/operator pytest | 0 | 49.374s | `3267c5fd680fe6ab` |
| Invitation Vitest | 1 | 2.962s | `eb5188518c18b1be` |
| Web typecheck | 0 | 25.460s | `230d5ca427cd6a00` |

Vitest now actually launched. Its failures consistently stopped at CSRF 403:
like the existing password/login tests, happy-dom strips forbidden `Origin`
when constructing Request. The new tests now set it **after** construction;
the production CSRF guard is not changed or mocked. This fixture repair needs
new-head receipts. No test count was inferred or broader suite launched.

## Remaining work (must precede review)

1. Routing/contract activation is implemented in the routing anchor below;
   obtain exact-head regressions and independent review before any live use.
2. Execute the canonical declared regressions at the new anchor and repair any
   findings. Focused Web matcher/handler and full-runtime PostgreSQL composition
   now have offline coverage; none is a live provisioning receipt.
3. Implement explicit one-time Human-bound foreground provisioning and recovery
   within a normally signed/admitted dev rollout after route availability,
   before the unchanged finite live gate. Bind environment/repo/tenant/purpose,
   original account and exact pure-admin target, plus genuinely owner-approved
   email. Default automation must not provision or bind.
4. Implement safe encrypted GitHub dev username/password rebinding, matched
   credential ownership, optional initial-secret cleanup and failure recovery.
   No plaintext stdout, logs, local files, browser storage or secret reads.
   A binding failure cannot be repaired by resetting either account. Durable
   sanitized receipts/readbacks must distinguish identity creation, binding
   and gate success (none is currently established).
5. A new invited admin must prove its actual invite/accept audit provenance;
   it cannot satisfy the existing bootstrap-only audit assertion by emitting a
   fake `identity.account.bootstrap`. Add a strictly equivalent provenance
   proof without expanding finite permissions or waiving any gate.
6. Update canonical release-profile documentation and exact-head CI; submit
   the complete task through `task_finalize.sh` for independent Codex review.
   The anchor alone must not be submitted as completed acceptance.

The existing `ajoe734` account, three roles, tenant/scope/clearance/status,
password and other sessions remain untouched. No IAM changes, source
activation, backfill, fixtures as live data, model promotion, gate waiver,
full-product acceptance or F11 approval. Archived PRs 1435/1441/1443 are not
reopened.

## Routing / contract anchor (2026-10-10)

Pre-edit checks: correct task branch, clean worktree at `7fe54daadf1d`, fetched
`origin/dev=10eb6224fa31010fbf3c6f27612ca7d17ba4401e`. Canonical active-task
artifact inspection found no other owner on middleware/generated artifacts.
Scope was extended via the live canonical status CLI while preserving the
original artifacts, not by editing seeded worktree status files.

- Runtime mounts `/api/v1/auth/invitations/accept` and its standard deprecated
  alias using the same PostgreSQL engine/durable audit as user administration.
  Memory/document composition returns a sanitized 503, never provisions.
- Web middleware exempts **exactly** `/auth/invitations`; nearby auth routes,
  password changes and protected pages still require the durable session.
  Its bounded same-origin POST handler neither reads nor changes the issuer's
  session and sends only the canonical server transport identity upstream.
- Added actual production matcher/handler composition with anonymous/issuer
  cookies, and a full runtime PostgreSQL issue/accept/replay regression through
  the genuine existing authentication boundary. Original account preservation
  remains asserted; no fake principal or guard override is used.
- OpenAPI and client regenerated with the project exporters. Exactly three
  invitation paths are added (289 total paths vs canonical baseline 286);
  status codes, strict JSON request bodies, accepted/issued/revoked receipts
  and write-only capability/password inputs are documented. Hand validation
  remains intentional so rejected secrets are never echoed by FastAPI.
  Offline integration guards exact invitation inventory, artifact/client drift,
  paired aliases and safe infrastructure refusal.

Prior exact `7fe54daadf1d6e3f5212bcd25a1318d6f477d420` canonical receipts:
all five declared selections exited 0 (diff `92ae0bee87532246`, invitation /
provisioning / workflow `f5fac960d8f88b07`, gate / operator `47f725602e2509ac`,
Web `48eba17907ffc5de`, typecheck `f8c99bc38b8947bb`). Those do **not** attest
this new routing anchor; its receipts are recorded after committing.

Still not review-ready: foreground Human-bound provisioning, encrypted dev
binding/recovery, rollout-before-gate orchestration, strict real invite/accept
provenance in the unchanged finite gate, updated canonical release profile,
exact-head required CI and independent Codex approval remain outstanding.
No live credentials, account creation, secret binding, IAM changes or deploy
were performed. Source activation is not successful deployment or acceptance.

### First routing-anchor verification

Canonical runner at exact `380487b0217743fb8ebdd9af339e97d3d7738ad8` completed
all five declared commands (terminal exit 1 overall, no timeout/background job):

| Declared selection | Exit | Duration | Receipt id |
|---|---:|---:|---|
| Diffcheck | 0 | 0.017s | `4065a57bcfbb419a` |
| Invitation/provisioning/workflow pytest | 1 | 82.667s | `329f1e2599804014` |
| Existing gate/operator pytest | 0 | 45.308s | `085c0233135cedaa` |
| Invitation Web Vitest (includes production matcher composition) | 0 | 2.258s | `e10e29aa583708f4` |
| Web typecheck | 0 | 17.632s | `9ed7345426610a3d` |

Two new regression assertions failed. Client comparison now renders the sorted
serialized schema, exactly as the project generator reads it, instead of raw
in-memory FastAPI property order. The full-runtime PostgreSQL regression now
explicitly requests live-data composition (otherwise the local document-service
router is selected). This is an offline composition selector, not a live flag
change or auth override. These repairs require new-head checks; the failure
above is retained, not converted to a pass.

The same declared pytest selection was then run foreground at exact
`72e3cdc0a9d0` (shell `time`, original terminal exit 1, real duration 78.441s).
Client/inventory comparison passed; the remaining test refused constructing
its memory-backed **non-identity test adapters** after live-data had already
been selected. Construction is now ordered before that offline selector;
identity engine/store/sessions/audit are still real PostgreSQL, and no factory
or permission guard is changed. This hybrid composition is not evidence of
healthy live business persistence, providers or deployment. New-head canonical
checks are still required. No broader suite was launched.

At exact `56c0af50558d732c4902aab40d798374ee4dd89c`, canonical runner again
completed all declarations: diff exit0/0.015s (`c458f1e5de026110`), invitation /
provisioning / workflow exit1/81.597s (`9783751b45666339`), gate / operator
exit0/43.010s (`a2db4dc54c054c38`), Web exit0/2.210s (`a708e6fce3317e7e`),
typecheck exit0/6.106s (`59864447f89cc774`). The single new composition test
correctly hit the global persistence refusal: hybrid memory bundle is not
production persistence. **No guard was relaxed.** The test fixture is now
replaced by the normal deployment migration command plus the real PostgreSQL
persistence factory, with its own closed pool and same-database genuine auth
boundary. This correction supersedes the hybrid approach, not the negative
receipts, and still needs exact-head verification.

At exact `7a42a09aefec93c7726ef319df86018a8e1c3bb2` the five declarations
completed: diff exit0/0.014s (`27ffe731abb95804`), invitation / provisioning /
workflow exit1/80.660s (`b409bbf19231d307`), gate / operator exit0/43.902s
(`c5d9db4945dafbda`), Web exit0/2.328s (`1b971be28b01598c`), typecheck
exit0/5.819s (`155d5a66ecdf7a89`). A single explicit diagnostic retry of the
same declared pytest selection/head (full scratch log because the canonical
2000-character output tail hid the cause) exited 1, real 86.878s: bundled
`pgserver` has **no PostGIS**, so full domain Alembic setup could not run.
No host-wide tool search, install, guard relaxation or wider suite followed.

The fixture now reuses the project's existing
`test_assisted_listing_postgresql_runtime._install_canonical_runtime` helper
for unrelated **offline core/workflow relation test inputs**, with actual
identity/runtime migrations and actual PostgreSQL persistence factory. This
is not proof of full canonical domain migration support or business readiness;
those remain separate. Invitation issue/accept/replay/preservation use the real
production route/boundary/PG transaction/audit, without permission overrides.
The normal deployment migrations must still succeed before live use. New-head
verification is pending; none of these earlier failures is re-labelled success.
