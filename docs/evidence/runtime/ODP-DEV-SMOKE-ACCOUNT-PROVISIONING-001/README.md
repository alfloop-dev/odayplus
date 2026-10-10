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

## Invitation provenance anchor (2026-10-10, source only)

The next increment replaces no gate requirement. A pure invitation-created
administrator now needs an exact durable issue/accept lifecycle rather than a
manufactured bootstrap event. Issuance audit records non-secret fixed presets
and expiry; authenticated identity audit projection includes outcome. The gate
binds UUIDs, actor/creator, tenant, pure roles, fixed scope, active acceptance,
distinct event IDs, resource/correlation and offset-aware lifetime <=72h. It
rejects duplicate, revoked, mismatched and fake-bootstrap histories and requires
an additional cookie-to-verified-principal binding for invited pure admins.
No input/config flag grants this provenance. Existing bootstrap/read-enabled
journeys, finite roles, `full`, admission, models and all other checks remain.

Offline regression adds a successful invitation-only gate journey, malformed
and ambiguous lifecycle/principal failures, and the release predicate applied
to actual account and audit readbacks through the real PG/runtime routers.
The original account snapshot still has to be unchanged. These tests are not
live account creation, encrypted secret binding or deployment evidence.

### Exact provenance-anchor verification

Canonical `task_verification.py` ran the five **exact task declarations** at
`9d109e7d2b8f2b6f8752f1d72d13d2929231a81c`. Original foreground terminal
status completed, exit 0 on the final infrastructure retry (no background
wait, timeout, wider suite or invented test count):

| Declared selection | Exit | Duration | Receipt id |
|---|---:|---:|---|
| `git diff --check` | 0 | 0.015s | `5267744adb1e2c16` |
| Invitation/provisioning/workflow pytest | 0 | 85.480s | `02e9489ea98d02b4` |
| Existing gate/operator pytest | 0 | 46.850s | `4587baaced77f7e4` |
| Invitation Web Vitest (34 tests) | 0 | 2.837s | `221571df6b16bbe9` |
| Web typecheck | 0 | 8.031s | `84746ee1ff870882` |

Failed setup receipts are retained: initial runner exit1 with Web exit127
(`8a4c9a31bbb7e8db`, `ef7ffcf357099af7`); first explicit retry used the existing
Corepack pnpm9.15.9 scratch shim, but npm-workspace binary PATH was still absent
(Web exit254 `6360d9c8ad670a8f`, typecheck exit1 `2c28348db4b98317`). Both Python
selections and diffcheck passed on each run. The final explicit infrastructure
retry added the already-installed repository `node_modules/.bin`, exactly as
this task's earlier setup evidence prescribed. No install or tracked lock/config
change was made. The canonical runner repeats all declarations, so both retries
explicitly justify remeasuring that same SHA/selection. Full receipts, exact
commands and original exits are preserved in canonical `.orchestrator/evidence`.
This documentation-only follow-up does not claim these receipts attest its new
commit SHA; future source increments/final submission require their own head.

During verification `origin/dev` advanced to
`0dd210dbe04fb420825abbddc08c8d3141de9ab1` (PR #1445, Cloud Run minimum instances).
Its warm-instance deploy changes must be preserved when composing the upcoming
provisioning orchestration. No task provenance/identity/gate conflict was found;
no merge/rebase or unrelated edits were made in this increment.

Foreground `provision_dev_smoke.py`, encrypted matched GH dev binding/recovery,
Human-approved promoted-before-live-gate orchestration, required exact-head CI,
independent Codex review and formal PR submission remain outstanding. Custodian
must supply an explicitly approved new username/owner-controlled email and
original administrator credentials; no fabricated recipient or worker access.
No live login, credential access, account mutation, GH secret/config write, IAM,
source activation, model promotion or deployment occurred. Not review-ready.

## Base composition / foreground preflight increment (2026-10-10)

Normal non-rewriting merge at `411973d2d4986abf073ab543755e58b02d1c7de1`
composed `origin/dev=0dd210dbe04fb420825abbddc08c8d3141de9ab1` with prior
`097d80a6dbfe0ed08e1e99a3317d32685d6b1003`. Both are parents; no reset,
rebase, discard or force push. Merge had no conflicts. PR1445's service-level
API/Web warm-instance logic, test and inventory entry are preserved verbatim.

All five declared commands completed at that merge head through the canonical
verification runner (original foreground terminal exit 0):

| Declared selection | Exit | Duration | Receipt id |
|---|---:|---:|---|
| `git diff --check` | 0 | 0.016s | `36b2c32d5feac31e` |
| Invitation/provisioning/workflow pytest | 0 | 84.257s | `b8c9497756467990` |
| Existing gate/operator pytest | 0 | 43.713s | `91f1eb60124ba246` |
| Invitation Web Vitest | 0 | 2.257s | `b9ab7ef65f3d1f4a` |
| Web typecheck | 0 | 5.825s | `b52a7cd914bd3838` |

The subsequent source increment adds **only a pure preflight validator**, not
an executor, CLI or workflow binding. It rejects unknown/secret-bearing fields,
wrong repo/environment/profile/tenant/purpose/original actor, mismatched exact
candidate/manifest, missing/noncanonical execution UUID, expired/naive/over-one-
hour windows, original-account roles/status/scope/identity changes and reuse of
its username/email. It requires explicit recipient/custodian assertions without
claiming authenticated ownership or alias deliverability. Static refusal codes
never echo inputs; identifier-only receipts say `execution_authorized=false`.
Offline tests are part of the already declared provisioning selection.

The preflight cannot authenticate a custodian, independently verify the human
record/admission or durably consume the execution UUID. Future orchestration
must do those **before any** account or configuration mutation. Encrypted matched
GH dev binding/recovery, promoted-before-unchanged-gate wiring, exact-head CI and
independent Codex review remain incomplete. No new live execution is enabled.
The base receipts above do not attest this later source increment; it needs its
own measured head. No account, password, other session, IAM, source/model,
secret/configuration or deployment was accessed or changed by this worker.

## Internal reservation journal increment (2026-10-10, not execution approval)

`provision_dev_smoke.py` now includes an **internal PostgreSQL reservation
journal** alongside the pure preflight. It is not an executor, CLI, HTTP endpoint,
workflow hook or human/custodian verifier. Neither its constructor, plan digest,
receipt nor stored event authorizes account creation or a GitHub mutation.
The eventual trusted foreground orchestrator must independently authenticate the
human approval/custodian, admission tuple and fresh original-account readback
before calling it. Recipient-control assertions remain assertions, not proof.

The journal revalidates the complete plan against DB time while holding a
transactional advisory lock keyed by the root authorization. It hashes all plan
fields together in memory (including recipient/custodian, execution UUID, exact
candidate/manifest and expiry), then appends one same-engine durable audit event.
No clear email, credentials, capability, arbitrary error or remote payload is
persisted. System journal attribution deliberately does not impersonate the
existing administrator or claim that a server-authenticated admin acted.

A second reservation is refused even with an identical request, different UUID,
new candidate or recipient. Audit/commit errors cannot return a success receipt.
A restart can inspect the integrity-verified record and append an exact-reservation
`recovery-required` quarantine after expiry, but cannot release/reset/retry the
root or automatically reset a password, delete an account or roll back secrets.
This is bookkeeping, **not a completed encrypted binding/recovery implementation**.
Receipts continue to say `execution_authorized=false`.

Declared integration coverage adds independent-PG-pool concurrency, root replay
with changed tuple/recipient, DB-clock expiry, secret-bearing plan rejection,
append-then-fail transaction rollback, restart/readback, exact reservation mismatch,
failed quarantine rollback and tampered audit refusal. Process/invitation spies
and original identity-table snapshots prove this layer does not invoke a worker,
CLI or invitation/account operation. All inputs remain offline test fixtures;
no live identity or configuration was inspected or changed.

New-head verification is recorded separately after this source anchor. Trusted
foreground approval/custody, memory-only Web invite/accept, encrypted matched
GitHub dev binding and recovery, promoted-before-unchanged-gate orchestration,
required exact-head CI, independent Codex review and formal review submission
remain outstanding. PR1445 deployment/inventory is untouched. Not review-ready.

### Exact journal-anchor verification

Canonical `task_verification.py` completed all five exact declarations at
`024cded34162e824357ee14f39ec675e171b791a`. The original foreground runner
returned exit 0; no background/summary polling or broader test selection:

| Declared selection | Exit | Duration | Receipt id |
|---|---:|---:|---|
| `git diff --check` | 0 | 0.029s | `fdb6b138e5f8e689` |
| Invitation/provisioning/workflow pytest | 0 | 96.431s | `81f064e90f99d868` |
| Existing gate/operator pytest | 0 | 44.778s | `8e0390e08eca0699` |
| Invitation Web Vitest | 0 | 2.304s | `24185a8b71018178` |
| Web typecheck | 0 | 5.888s | `60f23160e35256c7` |

The first run's Python commands both exited 127 before launch because the fresh
worker PATH omitted the existing `/home/lupin/.local/bin/uv`. Its receipts are
retained (`4b2392ac255c3983`, `e93d422646594319`), together with the first run's
successful diff/Web/typecheck (`fad4f5ceeefcf08f`, `6251c80b02298824`,
`ebe1fa70d8298f94`). The explicit retry reason permitted remeasurement of all
five declarations at the same head after adding that existing directory. The
existing scratch Corepack pnpm shim and npm workspace binary PATH were reused;
no installation, dependency or tracked config changes were needed. Original
runner receipts were copied into canonical `.orchestrator/evidence`.

These receipts attest the source anchor above, not this later evidence-only
follow-up's SHA, CI, human approval, mailbox custody, account/configuration
execution or deployment success. The task stays in progress, not review.

## Anchor 10 — callable memory-only Web lifecycle executor

Source anchor: `22d921d0b5933f08c0bb2bfe8ea51effcaafd547`.
The previous internal-only journal is now composed with an actual callable
`WebInvitationExecutor.execute` in `delivery_toolchain/release/provision_dev_smoke.py`.
It is a foreground library, not an anonymous CLI or automatic workflow hook.
Source approval, admitted/promoted exact release and recipient custody must be
verified by the trusted coordinator before invocation; this library does not
pretend to authenticate those control-plane approvals.

Execution path:

1. Original administrator password login through Web, canonical session cookie,
   `/auth/session`, server-verified `/api/v1/auth/principal`, authoritative
   tenant user inventory. Exact three roles, original subject/tenant/username,
   active status, original scope and authoritative source must match.
2. Validate the bounded plan with database time, reject existing recipient
   username/email, durably reserve the original Human authorization once.
3. Issue through `/api/v1/operator/users/invitations` using only the Web cookie;
   accept through `/auth/invitations` without carrying the administrator session.
   Capabilities and passwords remain in memory; no subprocess/file/output path.
4. Fresh login as the new account; server principal and authoritative account
   must be pure admin with the fixed scope. Read back the original account
   unchanged and require the gate's existing strict issue/accept provenance
   predicate, bound to both actual HTTP audit event receipts and issuing admin.
5. Logout only the two newly created sessions and require revoked session401.
   Other existing sessions are neither revoked nor reset.

Every remote action is single-attempt. Any uncertainty after reservation,
including lost HTTP reply **after durable commit**, failed provenance or logout,
marks `recovery-required`. No automatic retry, reset, deletion, replacement or
secret rebinding occurs. A failed quarantine append still leaves the original
reservation as the durable no-retry boundary. A successful lifecycle receipt
contains identifiers only and still says `execution_authorized=false`,
`credential_binding_verified=false`, `live_gate_passed=false`,
`deployment_success=false`.

Offline regressions bridge the executor's HTTP protocol with an explicitly
labelled memory BFF adapter to the actual API routers, PostgreSQL Argon2id
credentials, session service and production AuthenticationBoundary. They are
**not** a running Next server or live proof. The independently declared Next
invitation route tests still cover actual adapter forwarding/sanitization.
Cases include successful issue/accept/provenance and current-session cleanup;
original account/credential/scope/roles and other-session preservation;
pre-reservation login/session/principal/inventory failures; post-reservation
issue/accept/audit/logout failures; lost replies after issue/accept commit;
root replay refused with zero extra issue/accept; secret absence and zero
subprocess calls. No fake Principal/request actor/permission override is used.

### Exact source-anchor receipts

Canonical foreground `task_verification.py run` completed exit 0 at the source
anchor above. All **five exact declarations** were run once; no polling, broader
suite, or rerun for counts. The first CLI invocation was rejected by argparse
(exit2, missing `--task-id`) before any verification command launched; the
correct invocation then ran normally. Existing uv and scratch pnpm PATH entries
were reused, with no install or dependency/config changes.

| Exact declared selection | Exit | Duration | Receipt id |
|---|---:|---:|---|
| `git diff --check` | 0 | 0.018s | `6279287be558eff5` |
| Invitation/provisioning/workflow pytest | 0 | 121.330s | `1d5faa9c23e44122` |
| Gate/operator pytest | 0 | 42.963s | `f68da67a08091b7f` |
| Invitation Web Vitest | 0 | 2.423s | `5c101f9f979daff6` |
| Web typecheck | 0 | 5.838s | `bf8d9faa9cd0895f` |

Original JSON receipts are retained in canonical `.orchestrator/evidence`.
They bind exact commands, source head, selections, exit codes, durations and
output tails. This evidence-only follow-up is not the measured source head.

**Exact remaining boundary:** connect authenticated foreground custody and
admitted dev promotion to encrypted **matched** GitHub credential binding with
uncertain/partial-write recovery and the unchanged final live gate. This Web
library does not solve or claim that binding/orchestration. No default workflow
invokes it. PR1445 deploy/inventory code, IAM and finite gate remain untouched.
Custodian still supplies the approved new username, owner-controlled email and
credentials; worker never obtains them. Task remains in progress, not ready for
formal PR/review: exact-head CI and independent Codex approval remain required
once that end-to-end boundary is implemented. No live account/login/session,
secret/configuration, deployment, source or model action occurred here.

## Anchor 11 — encrypted matched-pair bundle staging (not activation)

Source: `e1a683b29baad8a1d968ffa934f8246e4d47706f`.
Verified current repository configuration against fetched `origin/dev`; default
workflow still reads the standing username variable/password and optional initial
password secrets. No workflow or PR1445 deployment/inventory code was changed.
Canonical task scope was extended only for `pyproject.toml` / `uv.lock` to use
standard PyNaCl/libsodium sealed-box encryption (`pynacl==1.6.2` locked), not a
custom cryptographic implementation. `uv lock --python 3.12` returned exit0;
only that dependency's records were added.

`DevCredentialBundleExecutor.execute` composes the actual Web lifecycle with
GitHub staging in a **single call**; it does not accept an arbitrary lifecycle
receipt or separate username/password pair as creation proof. The very same new
password used for invitation acceptance and fresh-login proof is encrypted with
the approved plan username and new account/tenant/execution identifiers. Original
admin password, email, acceptance capability, session cookies and initial-password
fallback are absent from this JSON bundle.

The pinned HTTPS GitHub API adapter authenticates with a foreground-owned token,
reads the exact repository identity and dev environment public key, and refuses
an existing `ODP_DEV_ADMIN_CREDENTIAL_BUNDLE`. It stages **one encrypted secret**
rather than partially writing a username variable and password secret. Existing
standing vars/secrets remain untouched. There is no CLI, redirect, subprocess,
plaintext request, file output, delete, value readback, retry or workflow hook.
GitHub cannot retrieve decrypted secrets; HTTP201 acknowledges creation only.
External concurrent secret writers must be excluded by the trusted custodian:
GitHub offers no create-only conditional PUT. HTTP204 (replacement/race), timeout
or error is uncertainty, not acknowledged creation or a rollback instruction.

A separate same-engine integrity-verified audit journal commits `binding-intent`
before PUT and `binding-acknowledged` only after HTTP201. Audit failure, lost
reply after the simulated remote commit, forbidden/replacement result or any
post-lifecycle uncertainty quarantines the root. A committed intent/root
reservation still forbids retry if quarantine cannot persist. Restarts inspect
identifier-only receipts, never recreate/reset/delete/replace the account or
retry PUT. This increment supplies diagnosis/quarantine, **not automatic remote
reconciliation or a recovery activation authority**.

Offline tests use actual sealed-box encrypt/decrypt, mocked pinned GitHub HTTP,
the existing labelled memory BFF adapter, actual API routers, PostgreSQL,
Argon2id/session/canonical-boundary lifecycle. They prove one matched pair,
durable intent before ciphertext upload, original identity/credentials/other
sessions preservation, no subprocess/secret output, repository/key/absence and
redirect refusals before login, uncertain/lost/replacement PUT quarantine,
append-then-fail transaction rollback, and restart with no second lifecycle/PUT.
These are not real Next/live custody/GitHub secret/deployment receipts.

### Exact source-head verification

The canonical foreground runner returned exit0, recording all five **exact**
declared commands once at the source head. No timeout, polling, count-only rerun
or broader suite. PATH used existing uv, Corepack pnpm (scratch launcher) and
workspace binaries; no Node installation/config change. The frozen Python run
installed the newly locked PyNaCl dependency.

| Exact declared selection | Exit | Duration | Receipt id |
|---|---:|---:|---|
| `git diff --check` | 0 | 0.015s | `d93090e878c8dff4` |
| Invitation/provisioning/workflow pytest | 0 | 181.732s | `f2d39e4d1d96902c` |
| Gate/operator pytest | 0 | 46.955s | `10382d020fef6739` |
| Invitation Web Vitest | 0 | 2.781s | `b43381b7cbc6eaf3` |
| Web typecheck | 0 | 6.772s | `c478cad0ad7d2baf` |

Original JSON receipts copied to canonical `.orchestrator/evidence`. A preliminary
runner path lookup exited2 (no such file) before any test command launched; the
correct canonical tool then ran once. This later evidence-only commit is not the
measured source head.

**Remaining exact boundary:** implement the strict memory-only matched-bundle
consumer (including no stale bootstrap-secret mixing), authenticated foreground
custody/source-approval/exact dev admission and promoted-before-unchanged-gate
orchestration. The staged bundle is currently **not consumed** by any workflow;
`binding_write_acknowledged` is not `credential_binding_verified` and neither is
live gate/deployment success. Exact-head CI, independent Codex review and formal
`task_finalize.sh` submission are required once end-to-end source is ready.
No live account/login/session, GitHub token/secret/configuration, IAM, deployment,
source activation or model action occurred. Task remains in progress.

## Anchor 12 — memory-only bundle consumer with durable quarantine refusal

Compared current deploy/workflow configuration with fetched `origin/dev` before
editing. Added only the optional bundle secret to the existing live-deploy step;
no lifecycle/writer or provisioning token is supplied by the workflow. Deploy
preflight validates the bundle before cloud mutation without decoding values into
shell/argv/files/outputs. The gate takes the exact matched pair in process memory
and suppresses every old username/password/initial/bootstrap fallback. Nonempty
invalid JSON, duplicate keys, incomplete pair, preserved subject, wrong dev scope,
unknown schema/keys or malformed UUIDs refuse without fallback or raw diagnostics.

Secret presence/parse alone is insufficient: the authenticated tenant-scoped
administration audit projection now includes only the two identifier-only dev
journal types in addition to existing identity events. The gate requires a unique
reserved root, binding intent and durable ACK matching execution/plan/creation
tuple/account/tenant; missing ACK, quarantine, duplicates or inconsistent events
fail closed. It also requires pure-admin account and invitation principal/audit
proof, preserving all existing finite-role/session/business/model/worker gates.
Original standing configuration still applies only when the bundle is absent.
No receipt claims decrypted GitHub readback or deployment success.

Offline coverage added for parser strictness, old-secret suppression, pre-network
refusal, actual deploy preflight, gate account/journal mismatches, and the actual
sealed-box lifecycle's matched-pair reader plus tenant-filtered journal projection.
Verification pending at this anchor. Still unfinished: authenticated foreground
custody/source approval and normally admitted dev promotion -> same-pair unchanged
final gate orchestration; explicit recovery reconciliation remains coordinator-owned.
Not review-ready; no live account/session/secret/IAM/deploy/source/model action.

### Exact consumer verification

Source anchor `33077f14f68ba95223dfb1d02809ae1d23d6ac66` completed all five
exact declared commands (original foreground terminal exit1 overall). Only the
provisioning selection failed (exit1, 205.415s, `6690639070fd627e`): the checker
expected a bare plan hash while the actual journal emits `sha256:` + hash.
This was a checker defect, not a live mutation. Four other commands passed;
all original receipts were retained, not discarded or misreported.

Fix anchor `8248405d3ae1c7634941059b3a1e66d16bc19099` aligns the checker and
negative fixtures with the existing journal digest format and adds actual
remote-committed lost-reply/replacement bundle consumption refusal. The canonical
foreground verification runner executed all five **exact** declarations at this
new source head, terminal exit0 (no background waiting, wider suite or count-only
rerun). Existing local uv, scratch Corepack pnpm9.15.9 launcher and already
installed worktree `node_modules/.bin` were used; no tracked dependency change.

| Exact declared selection | Exit | Duration | Receipt id |
|---|---:|---:|---|
| `git diff --check` | 0 | 0.024s | `d40ef9b265598563` |
| Invitation/provisioning/workflow pytest | 0 | 193.308s | `f5ccec26a8794ee1` |
| Gate/operator pytest | 0 | 56.183s | `a5aab1469d817b64` |
| Invitation Web Vitest | 0 | 2.766s | `b75cf39e2e111d00` |
| Web typecheck | 0 | 7.185s | `4723d9d9439c2234` |

All ten original JSON receipts copied to canonical `.orchestrator/evidence`.
This evidence-only follow-up is not the measured source head; final submission
still needs exact-head required CI and independent Codex review. The matched
reader now composes with actual sealed-box lifecycle/journals and the unchanged
finite gate. The foreground custody/source-approval/admitted-promotion driver
remains unfinished; source checks are not activation/live-gate/deployment proof.

## Anchor 13 — authenticated serving-pair observation

Verified the existing API `/platform/release-identity` and Web BFF augmentation
against fetched `origin/dev`. Reused that route without adding configuration,
router, permission, scope or IAM changes. The foreground Web lifecycle now reads
API and Web SHA, manifest digest and `dev-admin` profile through the **same**
authenticated BFF cookie path before reservation, before invitation acceptance,
and after fresh new-account principal/provenance proof. Every field must match
the exact coordinator tuple; API profile validity must be boolean true. An
independent API URL, caller plan/receipt or matching single revision is insufficient.

Malformed/missing/mismatched serving metadata or a read failure before reservation
refuses without account/invitation creation or root consumption. A mixed/rolled-back
revision or uncertainty after reservation quarantines the root, leaving any issued
invitation or accepted identity untouched for explicit recovery; no automatic retry,
password reset, account delete or other-session revocation. Successful output adds
only `serving_release_observed=true`, a point-in-time readback, **not** source
approval, dev admission authority, traffic stability, binding or deployment proof.

Offline tests use the existing labelled memory BFF adapter and actual PostgreSQL,
API invitation routers, Argon2id and canonical session boundary. Added all seven
serving fields' missing/wrong/type cases (including truthy integer validity),
pre-reservation network failure and mid-lifecycle API/Web mismatch before acceptance
or final readback. They verify no unintended lifecycle mutation, cleanup only of
newly created sessions, durable quarantine and no repeated issue/accept on restart.
The adapter's release metadata is an offline test input, not real Next/live evidence.

### Exact serving-pair verification

Source anchor `70449312b7f92bb31990e7d5481906d770b9b084` completed the five
exact declared commands once through the canonical foreground verification module;
the original terminal returned exit0. Each receipt binds actual command, source
SHA, selection, real exit code and duration. No polling, broader suite, count-only
rerun or swallowed exit. Existing uv and scratch Corepack pnpm9.15.9 launcher
plus worktree root workspace binaries were used; no dependency/config change.

| Exact declared selection | Exit | Duration | Receipt id |
|---|---:|---:|---|
| `git diff --check` | 0 | 0.020s | `a75c58693a490444` |
| Invitation/provisioning/workflow pytest | 0 | 232.783s | `c8b6db5d1d8ed4eb` |
| Gate/operator pytest | 0 | 60.676s | `4f2b669701d86b4a` |
| Invitation Web Vitest | 0 | 7.333s | `a3ec8d2a65f1abc7` |
| Web typecheck | 0 | 21.846s | `743015ae48aaa9da` |

Original JSON receipts are in canonical `.orchestrator/evidence`; this later
evidence-only commit is not the measured source head. Required exact-head CI and
independent Codex review remain necessary before activation/formal submission.
Still unfinished: authenticated recipient custody/source approval/exact dev
admission driver and the implementable one-time promoted-before-unchanged-gate
cycle (GitHub job secret-context timing remains a real constraint). The new
serving-pair readback is mandatory in the actual lifecycle, not just an unused
validator, but proves only observation at those three points, not control-plane
authority. Explicit remote reconciliation remains coordinator-owned. No worker
live account/login/session, GitHub token/secret/config, IAM, deployment, source or
model action. This is incremental source, not review-ready.

## Anchor 14 — same-process canonical gate composition

Added foreground-only `DevCredentialBundleExecutor.execute_and_check_gate`: the
actual Web lifecycle and sealed-box binding ACK now compose with the existing
canonical evaluator, HTTP clients and Cloud Run worker driver without reloading
GitHub's stale same-job secret context. The same new password remains in process
memory; no shell/env/argv/file transfer or caller-supplied passing gate receipt.
Before lifecycle mutation, the method refuses mismatched SHA/digest, non-dev or
full profile, different Web origin, HTTP, sources enabled or missing canonical
inputs. Standing, initial and bootstrap credentials are cleared. The new subject,
tenant and execution are required by the unchanged principal/invitation/journal
checks; every session/business/model/persistence/worker check remains in place.

Red or uncertain gate results quarantine the root. There is no auto retry, secret
replacement/deletion, identity reset or other-session revocation. Even a positive
evaluator result cannot claim GitHub decrypted-value readback or deployment success.
The actual rollout owner must keep rollback armed and only commit after the final
gate; this method itself does neither traffic promotion nor deployment commit.

Offline coverage: real PG/router/session lifecycle and actual sealed-box staging,
pre-mutation configuration negatives, a labelled evaluator composition spy (NOT
live gate proof), actual canonical evaluator with unavailable HTTP and no worker
process, and malformed/exception results. Verification pending at source anchor.
Still missing: authenticated foreground custody/source approval/exact admission
and promoted/rollback-owned wiring. No CLI/workflow hookup or live action; not
review-ready. This increment resolves memory handoff, not rollout authority.

### Exact same-process composition verification

Source anchor `3afa9db85b7a17cfd390b9f1e7ddb6f14c3372d6` completed all five exact
declared commands once through the canonical foreground verification module.
Original tool terminal exit0; each retained JSON receipt binds command, selection,
source SHA, actual exit code and duration. No background wait, polling, broadened
suite or count-only rerun. Existing uv, scratch pnpm9.15.9 launcher and installed
workspace binaries were reused without tracked dependency changes.

| Exact declared selection | Exit | Duration | Receipt id |
|---|---:|---:|---|
| `git diff --check` | 0 | 0.018s | `7d18cec9e51f6e14` |
| Invitation/provisioning/workflow pytest | 0 | 238.918s | `4b3727996bf48c4e` |
| Gate/operator pytest | 0 | 45.648s | `1d9716278411df2c` |
| Invitation Web Vitest | 0 | 2.536s | `d4384046783eed4b` |
| Web typecheck | 0 | 6.639s | `595f8e38177c0a55` |

Original receipts remain in canonical `.orchestrator/evidence`. This subsequent
evidence-only commit is not the tested source SHA. Exact-head required CI and
independent Codex review are still required before activation. The passing
composition spy is explicitly NOT an actual passing gate; the actual evaluator
negative proves unavailable runtime quarantine, not live release acceptance.
Source/admission/custody authentication and rollback-owned deployment hookup are
still the next source work. No worker live identity, secret/config, IAM, traffic,
source or model action; no deployment/F11/full-product claim or review submission.

## Anchor 15 — consumed exact-dev admission observation

Added read-only foreground `verify_consumed_dev_admission`. Rather than trusting
an `admitted=true` receipt, it reuses canonical manifest integrity/admissibility,
component binding and staged registry predicates, with exact candidate equality
(no ancestor substitution), four API/Web/worker/scheduler images, dev-admin and
sources disabled. It verifies the Supervisor signature and canonical lease
format/window/bindings, then reads the real store for the same signed lease in
`consumed` state with the expected task/release/consumer and offset-aware
consumption time. Issued, revoked, absent, mismatched, expired or unavailable
records refuse with a static secret-free error. It neither mints nor consumes a
lease, and never projects a fake issued state to reuse new-admission verification.

The coordinator must independently pin public key, durable store, registry and
rollout consumer; caller-provided trust roots cannot authenticate themselves.
The safe receipt says `execution_authorized=false`, `deployment_success=false`.
This is observation plus canonical predicate revalidation, **not** proof of the
historical caller of admission, source review, recipient custody, promotion or
rollback ownership. No executor/deployment hook invokes this helper yet. Those
remaining authentication/wiring steps are required before review-ready use.

Offline tests first run the actual canonical `admit_release` with a generated
Ed25519 key and real local durable state; positive observation is read-only,
with process/mint/consume spies. Thirty negative cases cover tuple, key/signature,
manifest/profile/sources/images/registry, state and time uncertainty. These are
labelled offline inputs, not live release or control-plane approval receipts.
No worker live login, identity, secret/configuration, IAM, traffic, sources or
model action.

### Exact consumed-admission observation verification

Source anchor `e437484644b3e69e1ca7a2b80a9ac44938ec8000` completed all five exact
task declarations once via the canonical foreground verification module; the
original tool terminal returned exit0. Every retained receipt binds the exact
command/selection, source SHA, actual exit code and duration. Existing uv,
scratch pnpm9.15.9 launcher and workspace binaries were reused without dependency
changes. No background polling, summary-count rerun or wider suite.

| Exact declared selection | Exit | Duration | Receipt id |
|---|---:|---:|---|
| `git diff --check` | 0 | 0.017s | `b580f130dfd0a25c` |
| Invitation/provisioning/workflow pytest | 0 | 248.531s | `a6b258ad5f474739` |
| Gate/operator pytest | 0 | 64.668s | `46e84ebdc3f44bc3` |
| Invitation Web Vitest | 0 | 3.745s | `41e69a54c21f20f0` |
| Web typecheck | 0 | 7.935s | `d0f605e60a57aaa5` |

Original JSON receipts remain in canonical `.orchestrator/evidence`. This later
evidence-only commit is not the measured source SHA. Required exact-head CI and
independent Codex review remain before activation. Next: independently authenticated
foreground custody/source approval, mandatory exact-dev observation enforcement,
and rollback-owned promoted-before-gate hookup. The helper remains read-only and
unwired; no live control-plane approval, gate pass or deployment is claimed.
Task remains in progress, not review-ready; no review submission was attempted.

## Anchor 16 — admission enforcement in actual executors

`WebInvitationExecutor` now requires an exact `ConsumedDevAdmissionObserver`,
not an `admitted=true` flag, callback or observation receipt. The coordinator
must independently obtain its key, Supervisor store, registry, rollout consumer
and admission documents. The observer snapshots documents while re-reading
actual lease state through canonical admission predicates on every check.
Its return value is not execution authority; source/custody authentication and
rollback-owned promoted-before-gate wiring remain unfinished.

Checks precede the admin login, journal reservation, invitation issue, acceptance,
new-account login and final lifecycle success. Bundle execution additionally
checks before any GitHub request and immediately before its single encrypted
PUT, after durable binding intent. The canonical gate result is rechecked against
admission before return. Missing, expired, issued, revoked, mismatched or
unavailable evidence refuses. After reservation, uncertainty quarantines without
retry, reset, replacement, delete or rollback. Cleanup still logs out only the
sessions made here, even when admission becomes unavailable.

Offline executor fixtures now use actual canonical `admit_release`, generated
Ed25519 material and a real temporary durable Supervisor store, not a passing
receipt. Added admission document snapshot coverage, receipt-substitution refusal,
pre-login tuple/issued/expiry negatives, store-loss injection at all six lifecycle
boundaries, both binding boundaries and after a stubbed positive gate. Assertions
cover no later mutation/PUT, durable quarantine, preserved existing credentials/
sessions and no replay. The gate positive stub is a composition spy only, not a
live gate. Existing actual-router/PostgreSQL lifecycle and refusal tests remain.

This is an incremental source anchor, not review-ready or deployment evidence.
No worker live login, identity/secret/config, IAM, traffic, sources or model action.
Verification pending at this anchor; exact declarations will be measured next.

### Exact admission-enforcement verification

Source anchor `c25e742970e04ad311c7c466e0e69c6017bb4b4b` completed all five exact
declarations once through the canonical foreground verification module. The
original tool terminal returned exit0; every retained receipt binds source SHA,
exact command/selection, actual exit code and duration. Existing uv, scratch
Corepack pnpm9.15.9 launcher and installed workspace binaries were reused; no
tracked dependency changes, background polling, count-only rerun or wider suite.

| Exact declared selection | Exit | Duration | Receipt id |
|---|---:|---:|---|
| `git diff --check` | 0 | 0.018s | `c3968c7c7339195d` |
| Invitation/provisioning/workflow pytest | 0 | 291.108s | `f1ffb3a3cd72ca99` |
| Gate/operator pytest | 0 | 45.246s | `aef9f6fc10b8b99f` |
| Invitation Web Vitest | 0 | 3.140s | `c3983c5e5fa24889` |
| Web typecheck | 0 | 8.075s | `fe8956887b73ac42` |

Original JSON receipts remain in canonical `.orchestrator/evidence`. This later
evidence-only commit is not the measured source SHA. Required exact-head CI and
independent Codex review remain before activation. Next source work: authenticated
foreground source/custody authority and rollback-owned promoted-before-gate hookup
with independently acquired admission trust roots. Library admission enforcement
is now wired; default workflow still only consumes an already staged bundle.
No live login/account/secret/configuration/IAM/traffic/source/model action or
passing live gate/deployment/full-product/F11 acceptance is claimed. Task remains
in progress, not review-ready; this is an increment, not review submission.
