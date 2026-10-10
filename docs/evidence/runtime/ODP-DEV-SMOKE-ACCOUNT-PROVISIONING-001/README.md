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

Verification declaration is **none**. No tests, builds, linters, live requests,
credentials reads or configuration writes were run. No test-pass claim is made.

## Remaining work (must precede review)

1. Mount issuance/revocation under the canonical authenticated user-admin
   router, with verified principal only (no header fallback), and token-only
   acceptance via the existing Web/BFF trust boundary. Strict DTOs must not
   echo passwords/tokens in validation responses; no-store responses, origin/
   CSRF policy, bounded bodies and acceptance abuse controls are required.
2. Complete actual-router and PostgreSQL concurrency/rollback regressions.
   Register narrowly scoped verification commands with the coordinator before
   running them; the current task declares none.
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
