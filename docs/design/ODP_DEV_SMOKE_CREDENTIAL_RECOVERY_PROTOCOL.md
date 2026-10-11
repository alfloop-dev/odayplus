# Dev-smoke credential recovery — implementation protocol (incremental)

Task: ODP-DEV-SMOKE-CREDENTIAL-RECOVERY-IMPLEMENTATION-001 · Pi · Codex2

**In progress, not independently approved, not deployed, NOT executable.**
Authorization HUMAN-ODP-DEV-SMOKE-RECOVERY-20261011-001 records real foreground
user message `bf235b62` (“授權”) at 2026-10-11T03:27:01.286Z. It is bounded
consent, not a completed execution plan, runtime admission or permission for a
worker to access credentials. The historical incident recovery design remains
unchanged. No live effects have been performed by this task.

## Implemented domain increment

`shared/identity/dev_smoke_recovery.py` owns an **unmounted internal** recovery
root, not password reset. It provides:

- Strict non-secret exact-account/tenant/incident plan, canonical digest, distinct
  nonzero execution UUID, server-owned SHA/manifest binding and DB-time expiry
  of at most one hour. Plan/digest/receipt identifiers confer no authority.
- Same-engine PostgreSQL durable audit and tenant administration advisory lock.
  Every operation rechecks authenticated ORIGINAL subject, persisted active
  account, current exact auditor + platform_admin roles, non-must-change
  credential, live durable original session and fixed scope. No selected persona
  or caller tenant/actor header is an authorization input.
- One reservation per new authorization across processes; no idempotent reserve
  response, resume, reset, replacement execution or release of authority on expiry.
- Exact existing active pure-admin target and persisted accepted invitation plus
  signed original issue/accept provenance. Exact old quarantined execution and
  its plan/quarantine IDs, binding NULL, are required. Nothing writes those rows
  or the old journal. No invitation reissue is introduced.
- New append-only identity recovery event, original/target **non-credential**
  identity-baseline digests, issuer session identifier and plan binding. No
  password/PHC/capability/cookie is read into evidence or audit. Quarantine is
  one-way; authenticated original-admin reconciliation can use a fresh session
  to quarantine, but cannot obtain continuation authority. Target/source drift
  must not prevent recording uncertainty.

The runtime constructor's dev/profile/tuple arguments must eventually come
from server configuration, never a request plan. Tests use isolated PostgreSQL
fixtures with incident IDs as inputs, not fabricated live recovery receipts.

## Required subsequent increments (not implemented)

1. Reviewed dedicated recovery capability/abuse-budget migration. Issue ONE
   independent random 256-bit capability (hash only, <=600s DB expiry, returned
   once no-store); pin root, authenticated issuer session, target and plan.
2. Atomic locked single consumption: cheap validity and independently committed
   abuse budget before Argon2; revalidate baseline/session/source under production
   PostgreSQL locks. NFKC policy, Argon2id >=64 MiB/time3/parallel1, target-only
   credential metadata/session revocation/capability consumption/audit all commit
   or all roll back. Preserve every original/other account and session. Same-engine
   concurrency, replay, expiry, rollback and post-commit transport uncertainty tests.
3. Official bounded authenticated API/BFF mounting with CSRF/service transport,
   permission-before-malformed-DTO denial, generated OpenAPI/client/inventory and
   audit projection. Root inspection alone is not a supported recovery API.
4. Strict NEW versioned recovery envelope and canonical full-gate lineage proof,
   connecting immutable old invitation/quarantine/binding NULL to new root,
   capability issue/rotation/intent/real PUT ACK. Keep schema-1 AUTH001 behavior
   and quarantined-root rejection unchanged. No missing-ID bypass or fallback.
5. Trusted foreground parent memory custody and new finite entrypoint: absolute
   <=2400s, <=1h plan window, bounded non-secret observers, measured ceilings and
   sufficient downstream full gate/cleanup/quarantine reserves before each effect.
   Persist allowlisted phase start/terminal receipts before proceeding. Timeout,
   lost reply, session/custody loss or receipt failure stops and quarantines NEW
   execution. Later readback never resumes authority. Never replay the old600s
   creation child or remove local uncertainty barriers.
6. One encrypted dev-only same-pair PUT after durable intent, fresh absence AND
   genuine external writer exclusion, independently pinned custodian/key/repo.
   Require actual201 before ACK;204, lost reply, ACK failure or gate uncertainty
   quarantines, with no overwrite/read/delete/retry/rollback compensation.
7. Complete canonical same-pair gate, actual matching Web/API SHA/profile/digest,
   original preservation, exact provenance and cleanup of only execution-owned
   sessions. Independently normally admitted fresh-job actual bundle consumer
   proof is separate from staging ACK, same-process gate and full/F11 acceptance.

## Concrete execution blockers: custody and writer exclusion

GitHub secret PUT has no conditional create-only operation. Fresh absence,
process-local locks, audit reservation and workflow concurrency cannot exclude
an external API writer. The existing repository mechanism provides no genuine
exclusive external-writer authority. Until an independently reviewed exclusion
boundary is established without wider IAM, **refuse before any rotation/PUT**.
Do not accept a passing boolean or decorative lock receipt as exclusion.

Native Actions secrets are snapshotted for a job; a job already started before
PUT cannot fetch the new decrypted value. GitHub's public secrets API exposes
metadata/ciphertext input, not plaintext retrieval. Existing CLI gate transport
uses a plaintext environment variable and violates the proposal's no-plaintext-
env/argv/file custody rule. No native memory-only consumer transport is present
in this increment. A later design must either implement and independently review
an actual native trusted secret-input boundary, or explicitly propose a narrow
independently approved amendment for native CI env-to-memory ingress (individual
field masking, no inheritance to children/artifacts/logs, no foreground GH PAT,
no fallback, no same-job refresh). Such amendment is **not approved here**.
A cosmetic commit/rebuild, second rollout, imagined decryption or mocked consumer
receipt cannot resolve this gap. Hold effects until the real normally admitted
fresh consumer and finite measured budget exist.

Target session revocation will never prove cleanup of the interrupted ORIGINAL
admin session; that historical uncertainty remains explicit. No business/data/
model effects, source activation, role widening, IAM changes, SQL/bootstrap
recovery or F11 self-approval are authorized.
