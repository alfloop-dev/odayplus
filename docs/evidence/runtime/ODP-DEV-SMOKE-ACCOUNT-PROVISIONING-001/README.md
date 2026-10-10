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

## Remaining work (must precede review)

1. Complete token-only acceptance via the existing Web/BFF trust boundary.
   Strict DTOs must not echo passwords/tokens in validation responses; no-store
   responses, origin/CSRF policy, bounded bodies and durable acceptance abuse
   controls are required. Issuance/revocation are now mounted as above.
2. Execute the canonical declared regressions, repair any findings, and extend
   actual acceptance-router coverage. Declare focused Web checks with the
   coordinator before implementing/executing those adapters.
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
