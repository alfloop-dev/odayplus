# Session infrastructure / legacy migration follow-up (auxiliary, unapproved)

Parent018e79df4a7f9d0c4b1aae8a41491ed285d1c4c3 independently CHANGES_REQUIRED:
missing production store/key setup treated as absent session, and legacy OIDC
cookie created random new SID on each resolution even after successful logout.
Four actual Web cookie/handler controls failed at intended assertions on unchanged
018e source (explicit memory store, NOT PG proof). No launcher/cookie fixture issue.

## Repairs

- Crypto key setup errors now throw STATIC availability before untrusted decode
  catch. Production missing store throws rather than returnsNULL in both opaque
  and legacy paths. Logout's existing availability handler returns503/no clearance.
- Additional actual route repro with a different valid-length sealing key showed
  logout200 despite undecodable live cookie. Logout now refuses cookie-present
  unresolved sessions with401/no clearance; no-cookie logout stays idempotent.
  AEAD cannot distinguish invalid cookie from a wrong valid-length key, so either
  is not cleanup proof. No success is inferred from temporary /session401.
- Legacy OIDC migration uses SHA256 of authenticated encrypted cookie (not raw
  cookie/bearer in migration ledger). New expand-only identity migration0033,
  Alembic0026, binds it to ONE random SID/account/original cookie expiry. Session
  insertion and migration claim share PG transaction. Federation/account active
  identity is rechecked/locked inside it. Cross-pool conflict reuses same mapping,
  never renews expired/revoked/missing session. Tombstone has deliberately NO FK
  to session/account and survives session cleanup/account deletion. No purge/reset
  path, historical backfill, implicit DDL or guessed prior random-SID association.
- Session absolute lifetime remains capped8h and bounded by cookie expiry; mapping
  persists at least original cookie expiry. Missing schema is unavailable, not a
  fallback to random remigration. Postcommit validation happens after returning
  connection; lost commit/read reply does not create another SID on later read.
- Memory fixture models same one-bound identity/tombstone semantics. Existing
  no-store test now requires explicit availability rejection rather thanNULL;
  refusal is strengthened, no assertion/budget/production guard weakened.

## Isolated controls and limits

Six memory handler controls include missing/short key, missing store, derivation
failure (no private provider message), wrong healthy-length key, and legacy cookie
logout/replay. Four actualPG production Web-store/handler child selections cover
cross-pool concurrency/restart/logout/session-row deletion, real deferred commit
rollback (claim and session), lost postcommit read reply, and absolute/idle expiry.
Each uses its own disposable database; existing original account credential/session
snapshots remain unchanged. Child environment removes ambient credentials/DB URLs.
No live credential/session/cloud effects or external OIDC calls. Frozen-lock Node
proof is required, not silently skipped in the PG parent selection.

Initial repaired TypeScript excess provider field and existing NULL-expectation
failure retained, then corrected. New migration is covered by exact head/asset/hash/
preserving downgrade assertions before immutable measurement. Broader auth tests
passed. Fresh exact-head receipts/review remain in matching handoff; passing local
proof is NOT canonical Manager verification, hosted CI, whole-repository approval,
source admission, native custody/exclusion/production reserves or execution plan.

Original ownerAntigravity2/reviewerCodex/six reopens/Human-Ops, rejected a129/PR1459,
quarantine/bindingNULL/cleanupUNKNOWN and unconditional preflight HOLD remain.
No canonical reenrollment/resubmission/merge/approval/finalization or full/F11 claim.
