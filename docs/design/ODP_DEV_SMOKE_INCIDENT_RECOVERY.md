# Quarantined dev-smoke identity: recovery decision and design

Task: **ODP-DEV-SMOKE-INCIDENT-RECOVERY-PLAN-001** · Owner: Pi · Independent reviewer: Codex2

**DOCS ONLY. Proposed design, not an approved recovery or an execution runbook.**
No credentials, browser/cloud/secret/database/account calls, scripts, endpoints or
framework changes are authorized by this task. Independent plan review is still
required. The safe present decision is **hold credential effects**: the deployed
source has no supported same-account lost-password recovery API, and its bundle
consumer cannot admit a new recovery lineage without a separately reviewed change.
This does not stop invitation #1447, ordinary UI releases using unchanged original
credentials, or independent data/model lanes.

## 1. Incident truth and evidence boundary

The task-scoped [898 handoff](../../support/handoffs/dev-live-898630fe-20261010/README.md)
and [d50 handoff](../../support/handoffs/dev-live-d50cd331-20261010/README.md)
report actual authenticated observations, not simulated creation. The
[evidence packet](../evidence/runtime/ODP-DEV-SMOKE-INCIDENT-RECOVERY-PLAN-001/README.md)
retains the facts and source-analysis boundary when these supervisor-materialized
handoffs are not present in a normal checkout. This worker has not recollected
live evidence; the underlying collectors' receipts are named by the handoff but
not supplied here. Historical observations are not fresh authorization.

| Item | Retained factual state |
| --- | --- |
| Creation consent | `HUMAN-ODP-DEV-SMOKE-20261010-001` (AUTH001), valid for the original bounded creation; not a missing-consent incident and not permission for automatic reset |
| Target | `odp-dev-smoke`, **13faae19-21c6-4663-8e89-b93ea7f1107d**, active, roles exactly `[platform_admin]` |
| Tenant | `e34f2117-de4b-478c-82fd-13c4ef428d42` |
| Preserved original | `17e9cb99-db46-4a07-8e61-6bf9b22cf5d2` (`ajoe734`), actual roles `auditor + platform_admin`; selected status/clearance/scope/identity unchanged, original password still authenticated |
| Invitation | `1a012edb-842b-4846-8699-6ebd4e350218`; issuer is original UUID, accepting actor is target UUID |
| Durable issue / accept | `0b85d791-129a-4dab-8c51-0978c4111a12` / `a9c756a7-cdf6-48f3-9bd9-7c9437958939`; acceptance committed `2026-10-10T16:44:42.681277Z` |
| Interrupted execution | `89dad153-138f-4541-8bd2-e972d59cce08`; plan `sha256:d466580ea9c4b941b92e302669db5777ebf746c6b06e8efb9c812fbc69d581c9` |
| Durable quarantine | Once at 16:52, event `019e39ed-27f9-4a33-bc99-9bd0e561e576`; execution `recovery-required`, **binding NULL** |
| Secret observation | GitHub dev bundle metadata absent at 16:47; no binding intent, acknowledged PUT, decrypted binding or later fresh-job new-bundle consumer proof |
| Uncertainty | Parent killed memory-only child at 600 seconds; no child completion receipt; interrupted-child session cleanup **UNKNOWN** |

Quarantine describes the execution, not account disablement or password rollback.
The lost random password is not recoverable by extending a timeout, retrying the
old child, reading a password hash, invitation replay, memory/log scraping or
password guessing. The parent timeout is the proven interruption boundary;
repeated history reads are a latency concern, not an established Cloud Run,
proxy, signing or per-phase root cause. Preparation failures and failed/limited
collectors remain failures; do not relabel them or reset owner/churn history.
Archived #1448/#1452/#1451 and their frozen sources are untouched.

Latest *provided* runtime observation: Runtime Release **38075833887**, SHA
`d50cd331a53b7aba3a6f2f8fe8919423620f0754`, manifest
`sha256:ef0226f5e81cb8b3567e2cd9175af3806dedeb2799b0d9d7df20c0da69df7af8`,
`dev-admin`, sources disabled, model readiness `not_claimed`. Actual Web and API
own matching identity readbacks and UI checks passed using original credentials.
That success does **not** prove new-bundle consumption, full-product or F11 acceptance.

## 2. Exact deployed source inventory (read only)

All source citations below pin **d50cd331a53b7aba3a6f2f8fe8919423620f0754**
(the observed deployed source and `origin/dev` at analysis). No inference is made
from unapproved invitation work. Source matches deployment by the supplied
handoff; Git inspection alone is not a fresh serving-revision observation.

### Official HTTP surface

| Official operation at this SHA | Authorization / semantics | Incident suitability |
| --- | --- | --- |
| `GET /api/v1/operator/users`, `/{subject_id}`, `/audit-trail` | Authenticated user-view permission, live identity service, verified same tenant | Future authorized reconciliation only; tenant audit must include issuance, not only a target-subject filter |
| `POST /api/v1/operator/users/invitations` | User-update permission, verified principal plus persisted active admin, non-must-change credential and durable live session; returns new 256-bit capability once | Creates an invitation, **not** recovery; existing target email refused |
| `POST /api/v1/operator/users/invitations/{invitation_id}/revoke` | Same persisted admin/tenant boundary | Accepted/revoked invitation refused; cannot undo acceptance or recover account |
| `POST /api/v1/auth/invitations/accept` through Web `POST /auth/invitations` | Hashed expiring single-use invitation capability; no caller tenant/role substitution; Web origin/CSRF, bounded body, service transport IAM | Atomically creates **new** UUID, Argon2 credential, pureadmin role/scope and acceptance audit; rejects consumed capability / existing account, no reset and no login |
| Web `POST /auth/password` | CSRF, durable authenticated **own** session, active own account and **current password verification** | Self-change only; no arbitrary target; lost password cannot satisfy it. Revokes that account's other sessions, so original-admin session would target the wrong account |
| `POST /api/v1/operator/users` / `/{subject_id}/status` | Live same-tenant administration; existing accounts only | Roles/scope/status, not passwords. Disable revokes sessions; no credential recovery. Not authorized as a shortcut |
| `GET` / `POST /api/v1/operator/users/dev-smoke-journal` | Dev + dev-admin; verified persisted original admin UUID and fixed tenant on every call | Inspect, reserve, quarantine and binding stages only. No reset/retry/resume/recovery-execution action |

Sources:
- [users router L168–500](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/api/app/routes/operator_modules/users_roles.py#L168-L500),
  [live operator permission wiring L977–1002](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/api/app/routes/operator.py#L977-L1002),
  [durable router mounting L2139–2178](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/api/oday_api/main.py#L2139-L2178).
- [acceptance API](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/api/app/routes/identity_invitations.py),
  [bounded Web acceptance adapter](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/web/src/app/auth/invitations/route.ts),
  [invitation service L119–365](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/shared/identity/invitation_service.py#L119-L365).
- [self password change L92–281](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/web/src/app/auth/password/route.ts#L92-L281),
  [identity administration](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/modules/opsboard/application/identity_user_role_management.py).

### Internal facilities are not supported recovery APIs

[PostgresIdentityStore.changePassword L215–225](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/web/src/lib/auth/identityStore.ts#L215-L225)
is an internal SQL hash update, not an authenticated admin-reset endpoint. It
cannot be invoked directly to bypass self-change verification, and by itself
does not atomically couple capability consumption, audit and sessions. Its mock
implementation is not deployed recovery evidence. The
[CredentialService](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/shared/identity/credential_service.py)
provides Argon2id (64 MiB minimum, time cost 3, parallelism 1), not reset authority.
Invitation's SHA-256 token storage is a useful future pattern, not permission to
reuse an accepted invitation as a recovery capability. Bootstrap is not recovery.

### Reservation, binding and consumer blockers

- [ProvisioningJournal L202–334](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/shared/identity/dev_smoke_journal.py#L202-L334)
  reserves AUTH001 once across processes, appends quarantine once and offers no
  reset/resume. Another UUID does not free the authorization root.
- [Server journal L297–373](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/api/app/routes/operator_modules/users_roles.py#L297-L373)
  requires `reserved` for binding operations; this incident is
  `recovery-required`. Existing NULL binding cannot be filled by the old writer.
- [WebInvitationExecutor L864–974](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/delivery_toolchain/release/provision_dev_smoke.py#L864-L974)
  refuses existing username/email, rechecks source/admission/custody and owns
  single-attempt invitation/acceptance. It does not recover an existing identity.
- [Bundle writer L1337–1400](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/delivery_toolchain/release/provision_dev_smoke.py#L1337-L1400)
  composes successful creation and durable intent before one encrypted PUT.
  [GitHub store L977–1079](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/delivery_toolchain/release/provision_dev_smoke.py#L977-L1079)
  requires authenticated pinned custodian, absent metadata, correct repo/key,
  no redirect/retry, and **201**; 204 means replacement, not acknowledged creation.
- [Strict decoder / ACK predicate L1094–1195](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/delivery_toolchain/release/provision_dev_smoke.py#L1094-L1195)
  accepts schema 1 with AUTH001 only. ACK proof requires exactly one reserved
  root plus matching intent/ACK, **without quarantine**. This old root fails.
  A new authorization/schema is refused; relabelling a new bundle AUTH001 does
  not repair provenance. [Gate L2106–2138](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/delivery_toolchain/e2e/check_live_e2e_gate.py#L2106-L2138)
  requires that predicate for bundle consumers; [L3347–3400](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/delivery_toolchain/e2e/check_live_e2e_gate.py#L3347-L3400)
  refuses malformed nonempty bundles without per-field/legacy fallback.
- [Source/admission observers L338–558](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/delivery_toolchain/release/provision_dev_smoke.py#L338-L558)
  require full reviewed/candidate tree equality, fresh canonical review writer
  and latest CI, bounded complete evidence, and fresh current deployment history
  or genuine consumed-lease state. A passing local receipt is not authority.
  Standing automatic-dev policy does not require replaying a consumed lease.

**Conclusion:** an authenticated same-tenant original admin can issue invitations
and inspect/quarantine the original journal, but at this SHA cannot recover the
**same 13faae19 identity** via a newly random, hash-only recovery capability with
atomic credential/audit completion. Two concrete gaps block execution:
(1) supported authenticated same-account recovery API and (2) distinct recovery
journal/bundle-consumer provenance integration. Neither longer timeout nor owner
consent alone supplies those missing capabilities.

## 3. Decision boundary

Keep the incident held pending independent review of a conditional protocol and
a separate narrow owner decision. No source or credential effects follow from
reviewing or merging these documents. Do not resume the original creation task,
rewrite old audit records, invent an ACK, or reinterpret broad “continue” as reset
permission. A future implementation must use a new task/PR, independent security
review, tests/CI and normal deployment admission, never edit frozen deliveries.

## 4. Proposed narrow owner decision (NOT a granted authorization)

Owner may choose **hold** (default) or request a separately scoped supported
recovery implementation and, after it is independently approved/deployed, approve
one foreground execution. The following is permission-request text, not a receipt:

> Request a NEW recovery authorization, not an extension of AUTH001, bound to an
> exact non-secret recovery-plan digest, fresh execution UUID, expiry, approved
> full source tree and current admitted dev release. Permit the authenticated
> original admin `17e9cb99-db46-4a07-8e61-6bf9b22cf5d2`, in tenant
> `e34f2117-de4b-478c-82fd-13c4ef428d42`, to issue ONE fresh short-lived recovery
> capability and consume it ONCE to rotate ONLY the existing `odp-dev-smoke`
> credential, UUID `13faae19-21c6-4663-8e89-b93ea7f1107d`, to a new random
> password held exclusively in the trusted foreground custodian's memory.
> Explicitly permit atomic revocation of this TARGET account's pre-recovery
> sessions (including its possibly interrupted session), not any other account's
> sessions. Preserve target username/email/UUID/tenant, active status, exact
> `[platform_admin]` roles and existing clearance/scope; no replacement activation.
> Permit a distinct durable recovery audit/intent and ONE encrypted create-only-
> in-policy staging attempt to `alfloop-dev/odayplus` environment `dev`, secret
> `ODP_DEV_ADMIN_CREDENTIAL_BUNDLE`, ONLY if fresh metadata proves absence and
> concurrent external writers are excluded. Permit own-session verification,
> full canonical dev-admin gate and a separate normally admitted fresh-job
> consumer proof, subject to their ordinary authority boundaries.
> Preserve AUTH001 reservation/quarantine, invitation issue/acceptance and ACK
> absence forever. Preserve original account password, status, roles, scope,
> clearance and every pre-existing original/other-account session; collectors may
> log out only sessions they themselves create. No IAM, source activation,
> backfill, fixture, business/model mutations, role widening, gate relaxation,
> second rollout, account creation/deletion, invitation reissue/replay, secret
> read/delete/overwrite or retry after uncertainty. Any uncertain effect stops
> and quarantines the NEW execution. A future attempt requires another decision.

The real owner must approve the **completed exact plan**, including custodian,
expiry and session-revocation choice, through the genuine foreground approval
channel. Do not populate a fake authorization ID, approved boolean, user reply,
SMTP/mailbox proof or GitHub comment. AUTH001 remains valid historical creation
consent; recipient alias remains metadata, not proven mailbox control. No new
SMTP/social ceremony is required merely to design recovery. If the owner does
not explicitly permit target-session revocation or encrypted staging, hold that
step; do not silently substitute account disablement or original-session revocation.

## 5. Conditional supported recovery contract — future separate implementation

These are requirements for a future official API, **not existing route names or
worker-executable instructions**. No direct SQL/ORM/internal hash helper may be
used by the foreground as an API substitute.

1. **Fresh preflight and new reservation:** trusted foreground pins new owner
   approval/custodian, entire reviewed Git tree, independent review gate and
   latest required CI on reviewed and candidate heads. Obtain current canonical
   release admission/history and actual Web AND API own SHA/profile/digest;
   source approval is not deployment admission. Reconcile original selected
   identity fields/roles/scope and genuine password authentication, target
   account + invitation issue/acceptance, old exact quarantine and NULL binding,
   bundle absence, and interrupted-session uncertainty. Stop on drift. Never
   restore the historical three-role upper bound (`operations_manager` is not
   an actual role baseline). Reserve a distinct recovery root referencing old
   execution, plan digest, quarantine and issue/accept event IDs; never re-reserve
   AUTH001. Identifier-only receipts grant no execution authority.
2. **Capability issuance:** official authenticated Web/BFF/API boundary must
   revalidate persisted same-tenant active original admin, non-must-change
   credential, live durable session and permission on every call, not selected
   persona or caller actor/tenant headers. Target must be the fixed existing UUID,
   same tenant and active pureadmin; no email/username account lookup substitution.
   Serialize issuance with identity administration and recovery reservation.
   Generate 256-bit CSPRNG capability, return it once in a bounded no-store
   response, persist only its hash in dedicated recovery storage (not audit).
   Bind to new authorization, target/tenant, issuer, new execution/plan and DB-time
   expiry (proposed maximum 10 minutes). One issuance per execution; no rotation
   of a pending/unknown capability. Durable issuance audit commits atomically.
3. **Single consumption and rotation:** same trusted issuer session plus matching
   capability through HTTPS/CSRF/service IAM transport; bounded JSON only, no
   query/URL token, redirects, retries or user/tenant header bypass. Durable abuse
   budget must commit even on refusal, before expensive hashing; validate hash,
   target and expiry cheaply, then recheck under write lock. Apply existing NFKC
   password policy and Argon2id (memory ≥65536 KiB, time ≥3, parallelism 1), not a
   caller-supplied PHC. In one production same-engine transaction, consume the
   capability, update ONLY target credential/rotation metadata, revoke ONLY
   authorized target pre-recovery sessions, and append durable recovery audit.
   Audit/commit failure must roll back all these effects. Concurrent consume
   accepts at most once; stale role/scope/status/session or source-plan drift
   refuses. No new account, invitation or privilege grant; target remains active.
   Return safe identifiers only after commit. The capability hash and Argon2
   PHC stay in credential storage; neither enters audit, evidence or error output.
4. **Reconciliation before staging:** use the retained same random pair for ONE
   fresh target login, principal/tenant/roles and original-account comparison,
   genuine original issue/accept provenance AND new rotation audit. Required
   durable phase receipts and serving/source/admission/custody rechecks must
   succeed before next effect. Any missing/ambiguous reply, audit or collector
   persistence failure stops, even if a later GET proves rotation committed.
   Read-only reconciliation may resolve truth, not reopen execution authority.
5. **Distinct binding intent then PUT:** append new lineage intent before any
   encrypted upload; exclude external writers because GitHub lacks a conditional
   create-only PUT. Recheck absence, pinned repository/environment/public key and
   authenticated human custodian. Encrypt in memory with sealed-box public key,
   single PUT, exact 201 required. Timeout/204/other response is uncertain, no
   overwrite/retry/delete compensation. ACK append requires the real PUT receipt;
   append failure leaves intent/unknown, never manufacture ACK from metadata.
6. **Gate, fresh consumer and cleanup:** preserve complete canonical dev-admin
   evaluation, true invitation provenance, denied-role probes, own-session
   cleanup and current admitted Web/API identity binding. A same-process gate
   may use the new pair in memory but cannot prove GitHub decrypted value. No
   same-job secret refresh assumption: a separately normally admitted fresh job
   must consume the matched bundle and pass the full gate to establish binding.
   No extra rollout is authorized by deployed-release observation alone. Persist
   only redacted gate/phase results and original terminal exit/completion receipt.
   Logout every newly created own session with 200 followed by session 401; failure
   is recovery-required, not permission to revoke other sessions.

### Future lineage/decoder integration is mandatory, not a gate exception

The current schema 1 reader/ACK cannot express this recovery. A separate reviewed
implementation must introduce a strict versioned recovery envelope/projection:
new authorization + execution/plan, same target/tenant, exact original creation
provenance and immutable quarantine reference, new issuance/rotation, distinct
binding intent and real ACK. Preserve old schema-1 behavior exactly, including
rejection of this quarantined root. New lineage can be accepted only through the
new validated contract; never omit bundle identifiers to skip ACK checking or
reuse old event names/correlation IDs to hide quarantine. Tenant audit projection
must expose the new safe events; the current projection includes only identity
and old release journal types, so new types need explicit reviewed integration.

The full gate's business/auth/transport checks remain mandatory. Only an
independently reviewed strict provenance extension may distinguish historical
creation quarantine from a successful separately authorized recovery. Malformed,
nonempty, duplicate-key, wrong-version/tenant/account/authorization or unmatched
bundles must fail closed, with no per-field mixing or original/bootstrap fallback.
An ACK or metadata timestamp is not decrypted-value proof. The current gate CLI
reads its bundle from an environment variable (L3355); that is **not** a no-env
custody adapter. Under this proposal's no-plaintext-env/argv rule, the future
ordinary fresh-job consumer also needs a separately reviewed trusted memory-only
secret-input boundary feeding the strict decoder and canonical evaluator; it
cannot just invoke today's CLI with a plaintext environment. This additional
custody integration must exist before effects, not be improvised by this worker.
Before any rotation, future declared verification must demonstrate all these
paths (including audit rollback, concurrency, timeout-after-commit, replay refusal, original-session
preservation and legacy rejection). This task adds no such implementation/tests.

## 6. Finite budget, non-secret receipts and custody across child timeout

Proposed execution limit: **2400 seconds wall-clock total**, within an owner
approval window of at most one hour, never an automatic extension. Start one
monotonic absolute deadline before preflight; nested calls/children inherit
remaining time, not a new per-phase deadline. Proposed ceilings, subject to
future measured preflight and owner review:

| Phase | Ceiling (seconds) | Receipt before the next effect |
| --- | ---: | --- |
| Full source/CI/admission/custody and account preflight | 600 | exact tree/release tuple, decision/plan refs, unchanged-account comparison, old incident refs, bundle absence, no execution authority |
| New reservation + capability issuance | 60 | distinct root, issue event/target/expiry; no capability/hash |
| Capability consumption/rotation | 120 | rotation event/commit observed or UNKNOWN; target-only session-revocation result |
| Fresh target verification / unchanged original comparison | 120 | login/principal/provenance checks; no cookies/passwords |
| Binding intent / encrypted PUT / ACK | 120 | real intent and response classification, ACK event only if committed |
| Full gate and separately admitted fresh consumer proof | 1080 | separate gate reports/job refs; no same-job refreshed-secret claim |
| Own-session cleanup | 180 | own session refs, logout/session status, terminal outcome |
| Bounded uncertainty reconciliation/quarantine reserve | 120 | append-only new recovery-required/unknown outcome |

Ceilings sum to 2400, not eight independent clocks. No effect starts unless its
worst-case downstream gate/cleanup/quarantine budget remains; if source observers
cannot finish within preflight, **refuse before rotation**. If a fresh consumer
cannot be admitted and finish in the allocated window, hold before effects and
revise the plan through review, not stretch the timer or reduce the gate. Original
observer rechecks, pagination refusal and latest-attempt semantics remain; do
not replace them with cached pass flags to fit the budget. Need measured safe
bounds and orchestrator cancellation support before this proposal is executable.

Custody proposal: the trusted foreground parent/supervisor, not a disposable
600-second child, owns the new random password and recovery capability in memory.
Credential-bearing HTTP/encryption/gate work stays in that trusted process.
Non-secret source observers/children receive only public identifiers and bounded
remaining deadlines; no password, capability, token, cookie or secret bundle in
child argv/env/stdin/artifacts. A child timeout cannot erase the parent's password,
but still triggers a stop/quarantine if an effect has begun. If a child cannot
operate without credentials, it is not part of this design: hold for a separately
reviewed custody boundary, not a plaintext handoff. Foreground-approved credentials
are never handed to this background worker.

Disable body/debug tracing, shell echo, crash/core dumps and secret-bearing report
serialization; control swap/debug access under the approved custodian runtime.
No plaintext files, browser state, logs, audit credential blobs, password digest
receipts, clipboard, environment or command-line transport. Dedicated capability
hash and Argon2 credential storage are necessary server state, not evidence. The
intended GitHub sealed ciphertext is the only bundle persistence. Memory cleanup
is best effort in managed runtimes, not a claim of provable zeroization.

Receipts are allowlisted identifiers, phase start/end monotonic durations, deadline
remaining, response/commit classification and audit refs. Persist a non-secret
phase-start receipt **before** each effect and its terminal receipt after; if
receipt storage fails, do not proceed. A phase-start without completion is UNKNOWN,
not failed creation. The original process terminal status/exit code or original
job handle receipt decides completion; no grep-based wait or process-name guess.

| Uncertain boundary | Required treatment |
| --- | --- |
| Reservation/issuance reply missing | New root/issuance may have committed; inspect once under authority, quarantine/hold, never repeat or issue replacement |
| Consumption reply/child completion lost after commit | Same target may have rotated; retain parent password only in memory while reconciling, quarantine; no automatic consume/PUT continuation |
| Parent dies or custody memory lost | New credential cannot be recovered from a hash or a longer timeout. Record unknown/quarantine via authorized collector; any further same-account rotation requires another narrow decision |
| PUT reply lost / 204 / ACK append failed | Intent remains, GitHub may contain a value; metadata cannot prove it. Quarantine, no read/delete/overwrite/retry |
| Gate/fresh consumer fails or times out | Preserve real intent/ACK; append separate failure/quarantine, no account/password rollback or old-journal change |
| Logout or quarantine call unavailable | Preserve committed root/intent as no-retry boundary; cleanup/quarantine completion remains UNKNOWN; no blanket session revocation |

For a future reconciliation collector, use one bounded own session and a finite
attempt budget; outages end with UNKNOWN and an owner blocker, not endless polling.
Original interrupted original-admin sessions remain UNKNOWN unless their exact
ownership and a supported narrowly authorized cleanup are independently proven;
revoking target sessions cannot be represented as cleanup of original sessions.

## 7. Review and disposition

Codex2 should independently review the submitted exact docs head for factual API
inventory, explicit gaps, preservation/no-replay semantics, narrow proposed owner
scope, distinct lineage, finite budget and custody failure truth. Docs review/CI
or merge is not runtime permission. This task can deliver a reviewed plan while
recovery stays held; it cannot claim that recovery happened. Subsequent capability
implementation, its declared verification, normal deployment, exact owner consent
and foreground execution receipts are separate gates. Keep independent source/UI/
data lanes moving and preserve all archived delivery/failure history.
