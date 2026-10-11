# Dev-smoke credential recovery — implementation protocol (incremental)

Task: ODP-DEV-SMOKE-CREDENTIAL-RECOVERY-IMPLEMENTATION-001 · Pi · Codex2

**In progress, not independently approved, not deployed, NOT executable.**
Authorization HUMAN-ODP-DEV-SMOKE-RECOVERY-20261011-001 records real foreground
user message `bf235b62` (“授權”) at 2026-10-11T03:27:01.286Z. It is bounded
consent, not a completed execution plan, runtime admission or permission for a
worker to access credentials. The historical incident recovery design remains
unchanged. No live effects have been performed by this task.

## Implemented domain increment

`shared/identity/dev_smoke_recovery.py` owns the recovery root and
`RecoveryCapabilityService`. The later transport increment mounts these through
`/api/v1/operator/users/dev-smoke-recovery`; it is not a generic password-reset
endpoint or an executable foreground protocol. The domain provides:

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

`000029` / Alembic `0023` add dedicated capability and abuse-budget tables
(expand-only; rollback preserves single-use history). The internal service now:

- Issues one CSPRNG256-bit capability per authorization, stores only SHA-256,
  binds root/plan/target/tenant/issuer session and credential update timestamps,
  expires by DB time within600s AND the execution window, and returns the bearer
  once only after the issuance audit/storage transaction commits. Safe receipts
  and repr exclude the token. Event types/correlation remain distinct from root.
- Commits an independent, fixed five-submission authorization budget before
  cheap capability/password refusal and before constructing Argon2. It never
  writes login/invitation budgets, resets counters or creates arbitrary-input rows.
  Nested caller transactions are refused so they cannot undo budget or expose
  uncommitted success. Possession also costs budget.
- Validates exact persisted original-session/root/source/baselines and immutable
  old lineage before hashing, then rechecks all under tenant, recovery and row
  locks after hashing. Credential timestamp drift, expiry, quarantine, replay,
  new issuer session, role/scope/status/source drift refuse; invalid requests do
  not start Argon2. The existing NFKC policy and Argon2id64MiB/time3/parallel1
  apply, with no caller PHC. Credential update, only target pre-recovery session
  revocation, capability consumption and safe rotation audit share one PG commit.
  Original credential, identity/roles/scope and all other sessions stay untouched.
- Has isolated real PostgreSQL tests for issue/consume races across independent
  pools, rollback at audit failure, post-hash session expiry, lifetime/replay/
  credential drift and preserved old history. These are not deployed/live proof.

The runtime constructor's dev/profile/tuple arguments must eventually come
from server configuration, never a request plan. There is still no foreground
executor. The internal binding ledger described below cannot PUT a secret or
establish a live binding. No store/receipt alone
supplies human/source/admission/exclusion authority. Tests use isolated PostgreSQL
fixtures with incident IDs as inputs, not fabricated live recovery receipts.

## Credential/session fence increment (unapproved source; no live proof)

Expand-only `000030` / Alembic `0024` adds a non-secret monotonic credential
revision. The DB trigger advances it on PHC/algorithm/must-change changes, even
when PostgreSQL transaction timestamps collide. A caller-assigned revision cannot
rewind it. New root/capability checks bind both account revisions in addition to
timestamps; revisions, not hashes, may accompany server-only session insertion.

The session-insert trigger takes the SAME target credential row SHARE lock that
rotation takes for UPDATE. A target session inserted before rotation commits is
therefore visible to target-only revocation; an insert after rotation must supply
the current verified revision. An old revision fails. Legacy target writers with
no revision fail once recovery is consumed, instead of inserting an old-password
session. Other accounts' legacy inserts are unchanged. Rotation revokes all active
target sessions under that fence, not a client-time cutoff: a future-skewed Web
created_at must not evade revocation. Existing session rows remain intact apart
from the already authorized target revocation; the trigger never deletes them.
Isolated PG tests exercise independent pools, insert lock timeout during an
uncommitted rotation, stale/missing revision refusal after commit, and future-clock
pre-rotation session revocation. No database credentials or live effects are used.

Canonical scope was expanded for the four Web adapters; the current Manager
worktree lease/active-worker and canonical task scope readbacks show this worker
owns the task and no active overlapping writer. No frozen invitation source was
edited. Web `identityStore` now reads the revision WITH its PHC as text (no JS
number truncation), rehashes by revision CAS and returns that UPDATE's committed
revision. `localAuth` carries the verified revision, replacing it only with the
result of its own successful CAS. A failed rehash retains the OLD revision;
it cannot overwrite a concurrent rotation or fetch a newer revision to relabel
an old verification. `/login` passes that server-only value to `sessionStore`,
not any submitted JSON/header value. Production local-password insertion
requires a revision; the atomic DB trigger refuses drift before a cookie can be
sealed. Unconfigured/missing migration fails closed, not mock/legacy fallback.

Unit production-store SQL/propagation tests and mounted login tests check CAS,
missing/stale revision, no cookie on refusal and no credential/revision response.
A recovery PG fixture can invoke the actual production Web stores through the
existing frozen-lock Vitest environment: target rotation3 refuses old revision2
insertion/rehash, unchanged new PHC authenticates, current revision3 creates a
session, only that test-owned session is cleaned up, and a successful rehash
returns4. This child receives only ISOLATED fixture database configuration, never
foreground credentials. Python-only environments explicitly skip that Web-store
proof if the declared Node environment is missing; such a skip cannot count as
Web verification. These offline tests are not deployed source/runtime proof or
permission to execute. Legacy serving writers without revision remain fail-closed
for the recovered target; ordinary deployment must compose all adapters and the
migration before any recovery. No blanket session denial is the final design.

## Mounted transport increment (unapproved source, no effects)

The existing composed users router now mounts authenticated GET inspection and
one bounded POST action union: reserve, issue, consume, quarantine,
binding-intent and binding-acknowledged. There is no
caller target/tenant/actor substitution, anonymous path, reset or resume. The
existing user-update permission dependency executes BEFORE request parsing;
fixed original claims, durable identity provider and every domain call's persisted
session/identity checks remain mandatory. Constructor tuple comes only from server
environment. No new main.py mounting or alternate engine is introduced. The
consume adapter has no enclosing transaction that could undo its abuse budget.

POST accepts <=8192 streamed bytes of application/json, rejects duplicate keys
at any nesting depth, extra fields, malformed bodies and query parameters without
input echo. Tokens/password DTO fields have repr disabled. GET also rejects query
transport. All responses, including dependency refusals and unavailable persistence,
are no-store/static-code only. Issuance returns the private bearer only once after
commit; subsequent inspection cannot retrieve it. OpenAPI is generated from the
composed app with inline bounded action schemas and no dangling DTO references.

The mounted Web catch-all applies same-origin CSRF to this exact route BEFORE
proxy, body or service-token-provider work, enforces GET/POST and bounded JSON
transport, and rejects query token transport. It then uses the EXISTING durable
Web-session/Bearer + service IAM proxy, not caller actor/tenant headers or a new
authentication scheme. No redirects/retries or credential logging are added.
Recovery audit events are projected only with their exact safe root/capability
metadata allowlists; unexpected credential-bearing extensions are omitted so
lineage fails closed. Target-filtered audit now includes target_account_id lineage.
No capability hash, password/PHC or cookie is projected.

Isolated PG mounted tests cover real permission/session denials before malformed
DTO/domain work, same-account rotation, old-history preservation, no-store,
redaction, duplicate keys, runtime refusal, body bounds and quarantine. BFF unit
tests exercise the actual mounted handler's CSRF/body/query boundary; existing
proxy tests retain the durable session/IAM/header boundary. These are source tests,
not deployed recovery, ACK or fresh-consumer evidence.

## Strict schema-2 consumer increment (unapproved, no live binding)

The canonical matched-bundle decoder now distinguishes schema1/AUTH001 from
schema2/new recovery authorization, with exact fixed target/tenant/username and
an exact nested non-secret lineage envelope. It carries plan/root, capability/
issuance/rotation, binding intent and immutable old execution/plan/quarantine and
invitation issue/accept identifiers. A missing key, duplicate key at any depth,
wrong version/authorization/account, malformed nonempty input or partial pair
refuses with a static error; no legacy/bootstrap/per-field fallback. Password
remains repr-hidden memory input. The existing CLI environment ingress is NOT a
new approved custody boundary: the no-env native consumer blocker still applies.

`recovery_bundle_acknowledged` verifies the authenticated tenant-audit projection:
exact old reserved + quarantine, STILL no old binding; exact original invitation
and authoritative active pure-admin fixed-scope target; one new root, one issue
and one rotation under its same issuer session/plan/tuple; one distinct intent and
201-ACK. Missing/extra/duplicate/cross-lineage events, new quarantine, timestamp
reordering, execution expiry, capability expiry/replay or ACK204 refuse. Plan
hash is recomputed and old provenance must remain intact. Expiry constrains the
execution, not use of a standing bundle by later normally admitted candidates.
Identifiers, parsed bundle and projected ACK are never execution authority or
proof of decrypted GitHub value. Schema1 quarantined-root rejection is unchanged.

The full canonical gate carries the schema/lineage (including CLI parsing),
refuses partial bundle IDs that could skip ACK, and supplies its authoritative
user/audit readbacks to the strict predicate. Invitation/principal/tenant binding,
negative-role probes, all existing transport/runtime checks and own-session
cleanup remain required. Offline mocked full-gate tests do not establish a live
fresh consumer or F11 acceptance.

## Binding ledger and mounted declaration increment (unapproved; NOT executable)

`RecoveryBindingJournal` now produces the exact safe intent/ACK schema in the
same-engine PG audit transaction. It revalidates the original execution-owned
session, unquarantined root, source tuple, execution expiry and old quarantine/
NULL binding/invitation history on each append. Original identity/credential
revision/timestamp must remain unchanged; target identity remains exact, target
revision is exactly the reserved revision +1, and last_rotated_at matches the
committed capability consumption. The dedicated capability row and signed issue/
rotation events must match root/plan/session/IDs/timestamps. No bearer/hash/PHC is
queried for binding. Account/credential SHARE locks fence changes until commit.

A live persisted local-password target session at the rotated verified revision
and created after consumption is required before intent AND ACK. This proves a
fresh password authentication occurred under the existing revision fence, not
that an arbitrary caller-supplied credential pair matches or that a consumer ran.
Intent is once-only across PG pools. ACK requires its exact event ID and integer
201;204/unknown/malformed declarations fail. Neither intent nor ACK writes any
identity/session/invitation/old journal. Failed audit or deferred PG commit returns
no success. Root quarantine remains the one-way uncertainty transition, including
after ACK; it invalidates the consumer predicate without editing binding history.
Inspection provides no retry/continuation permission.

**Important trust boundary:** the bounded API now mounts intent/ACK declarations
under the SAME persisted original-admin/session/tenant and runtime boundary.
GET adds safe binding history (including after quarantine); it never retrieves a
capability or grants continuation. POST accepts only the exact reservation,
intent event ID and strict integer201 for ACK, with no caller account/actor/tenant,
password or token field. Both actions reuse the8192-byte/duplicate-key/CSRF/no-store
boundary and same-engine domain transactions, with no enclosing transaction.
A201 integer is an authenticated original-admin declaration, NOT an independently
authenticated GitHub response receipt. The server cannot distinguish a fabricated
201 from a real201 using that field. A reviewed trusted foreground single-attempt
transport must own actual response classification and submit ACK only after real
201, with no readback-based reconstruction after uncertainty. This increment
intentionally does NOT claim that unimplemented foreground boundary, any PUT/
exclusion, deployment, live binding or fresh-consumer proof. Isolated tests drive
reserve/issue/consume/intent/ACK through the real mounted API and PG producer,
then use actual mounted safe audit/user projection in the strict predicate.
The target-session insertion and201 declaration are isolated test inputs, NOT
actual Web login or remote response proof. Tests also cover malformed/non201
ACK, caller authority fields, different issuer session, revoked target session,
source drift, quarantine, replay and committed-receipt loss followed by hold/
new-root quarantine. A later successful GET is commit truth, not permission to
reconstruct ACK after an unknown PUT or resume an execution.

## Foreground custody/budget primitives (unapproved; NOT an executor)

`delivery_toolchain/release/recover_dev_smoke.py` now provides isolated building
blocks, with no CLI, HTTP/cloud call, secret writer, lifecycle or admission
callback. The later bounded read-only child adapter is described separately below.
Neither increment can perform recovery or authorize effects:

- `ParentMemoryCustody` owns one384-bit random password and one once-attached
  recovery capability. Repr/pickle/deepcopy do not export them. Access after fork
  or close refuses. Managed-memory release is best effort, NOT zeroization or
  protection from a debugger/core dump; trusted foreground runtime custody
  requirements remain mandatory. Original password custody is not implemented.
- `ExclusivePhaseReceipts` creates a NEW execution directory exclusively beneath
  a trusted existing runtime directory. Safe phase records alone are persisted,
  with0600 files, fsync of records/directories and no overwrite/reopen/resume API.
  Partial-write/fsync failures permanently poison the instance. This LOCAL
  uncertainty barrier cannot exclude external GitHub writers or prove remote
  commit. Old incident locks/history are not opened or changed.
- `ForegroundRecoveryBudget` pins a shape-valid exact non-secret plan/digest to
  that receipt store. It enforces one monotonic absolute<=2400s deadline capped by
  plan expiry, wall-clock expiry refusal, strict once-only phase order and durable
  starts BEFORE a caller can begin a phase. Before every effect it reserves all
  downstream full-gate/fresh-consumer/cleanup/quarantine ceilings. Interrupted,
  timed-out, failed or missing PUT201 classifications stop normal continuation;
  finite once-only cleanup/quarantine lanes remain. Terminal persistence failure
  also stops. No readback can restore progression. A budget allowance is NOT a
  timeout/cancellation adapter or evidence that a phase actually ran.

The design ceilings split the reviewed2400s envelope as preflight600,
reserve30/issue30, rotate120, reconciliation120, intent20/PUT80/ACK20,
gate540/fresh-consumer540, cleanup180/quarantine120. They are **not measured
production bounds**. The original primitive tests simulate elapsed time and response/exit statuses;
those simulations are not actual subprocess cancellation proof.201 input remains
a declaration/classification, NOT authenticated remote-response or single-attempt
PUT proof. A full trusted coordinator, credential transport with enforced absolute
cancellation and actual response authentication remain unimplemented. The later
read-only child increment below supplies original handles only for local observers.
Do not treat these reusable primitives as an executable foreground entrypoint.

## Bounded local checkout observer (unapproved; read-only, NOT preflight approval)

`TrustedCheckoutObserver` executes only fixed `/usr/bin/git` read operations with
shell, fsmonitor, automatic maintenance, inherited environment/stdin/FDs disabled.
Foreground independently pins the checkout, candidate SHA and ENTIRE reviewed
Git-tree ID, never from a passing boolean or recovery plan. Each observation reads
HEAD, tree, tracked/untracked/submodule status, then HEAD again; dirty, changed,
incomplete or mismatched output fails closed without emitting paths or raw output.
There is no cached success. This is **local source consistency only**, not remote
GitHub review/latest CI, deployment/current admission, Web/API own identity,
account/provenance reconciliation or execution authority. It cannot complete the
full preflight on its own; all existing fresh authority observers remain mandatory.

`ForegroundRecoveryBudget.call_deadline` exposes the active phase's absolute
monotonic limit, capped by plan expiry, without refreshing the phase allowance.
The internal non-secret exec adapter uses that same deadline for all commands,
bounds combined stdout/stderr to64KiB, and reserves2s INSIDE it for SIGKILL of
the child process group and wait on the ORIGINAL `Popen` handle. Pipe EOF alone
is not success; exit0 is required. Timeout/cancellation, output overflow or nonzero
exit refuses. A missing start/kill/wait receipt is UNKNOWN, never fabricated exit0
or indefinite waiting. A parent exit0 with descendants keeping a pipe open still
refuses. No log grep, PID-name search or independently supplied terminal receipt.
The caller must persist phase-start BEFORE observer invocation, then its terminal
classification; failure cannot proceed to reservation/rotation. Cancellation of
an OS-stuck/unreapable child or a stalled process creation cannot be guaranteed:
missing original terminal receipt stops UNKNOWN, not an extended deadline/pass.

Real isolated subprocess tests cover timeout with original exit-9, parent memory
retention, no inherited test env/FD/stdin, output caps, nonzero exit, missing wait
receipt and process-group cancellation. Isolated Git fixtures verify clean exact
tree, later dirty/untracked changes, mismatched SHA/tree, HEAD drift and fsmonitor
suppression. These are not measured production source/CI/admission observer bounds
or live credential-custody proof. Exec may transiently fork in the OS; environment
and FD isolation do NOT establish OS memory isolation/zeroization. No Python fork
worker or credential-bearing child is introduced. Approved runtime dump/debug/swap
controls and the reviewed custody boundary remain mandatory before real use.

The next increment below supplies a one-use official-route HTTPS transport only.
No same-session lifecycle adapter, secret PUT/actual201 classifier, gate runner,
full foreground coordinator, external writer exclusion or native fresh-job consumer
is supplied by this checkout-observer increment. The old600s creation runner and all old uncertainty
barriers remain untouched. Do not mistake a local checkout receipt or successful
child cancellation test for full preflight completion or effect permission.

## One-use absolute-deadline HTTPS primitive (unapproved, NOT a coordinator)

`RecoveryHTTPSAttempt` now sends exactly one GET/POST to the fixed official
`/api/v1/operator/users/dev-smoke-recovery` route. The trusted foreground runtime
independently pins HTTPS origin, numeric IPv4/IPv6 connection address and normal
public CA trust; certificate chain/hostname verification and SNI remain mandatory.
No DNS, proxy/environment lookup, redirect, retry, reconnect, subprocess or debug
logging is used. Public CA PEM is trust configuration, not a verification waiver.
No cookie/capability/password/response enters a file, child or receipt. The caller
retains its SAME execution-owned original-admin cookie in memory; the domain's
persisted session validation still runs on each official call. This transport
cannot create/authenticate a session or perform full preflight by itself.

One absolute monotonic deadline bounds numeric TCP connect, TLS handshake, each
sendall, EVERY underlying response read, and final parsing. Slow header/body
dribbles cannot refresh it. Combined header/framing/body bytes are capped64KiB,
JSON body16KiB; request JSON is capped8192bytes. No-store JSON response, unambiguous
length/chunk framing and duplicate-key/nonfinite-number refusal are mandatory.
Only original200/201 responses with complete bounded framing return a private,
repr-hidden/non-pickleable payload (which may contain the once-issued capability).
That response still needs action-specific root/plan/session/lineage validation by
the future coordinator before any completed phase receipt. It is not authority.

Each instance is poisoned before validation/network access, including on connect
or TLS failure. A response error, incomplete reply, malformed JSON or timeout
AFTER starting POST send is UNKNOWN; remote rotation may have committed. No GET
can reconstruct completion/ACK or permit replay. Before-send failure is a refusal,
not a retry grant. Only the original status, when actually received, and static
classification may be persisted. Socket/response closure is best effort with no
compensating operation. There is no GitHub PUT or ACK201 authenticity proof here.
Newly constructing an attempt cannot override durable no-retry/uncertainty truth.

Isolated loopback TLS tests use ephemeral OFFLINE certificates and synthetic
cookies/JSON only. They cover real certificate-hostname verification, no DNS/proxy,
fixed route/same cookie, Content-Length/chunk completion, redirect/error/lost reply,
truncated framing, aggregate limits, duplicate keys, slow-dribble/handshake deadline,
redaction and one-use poisoning. Those bounds are offline adapter checks, NOT
measured production source/CI/admission/gate/cleanup bounds or live custody proof.
OS-stuck calls, scheduling and parsing cannot promise hard-real-time termination;
a missed absolute deadline refuses rather than authorizing late continuation.

Full same-session action adapters, fresh source/CI/current-admission/Web+API and
account/provenance coordinator, measured downstream gate/consumer/cleanup reserves,
actual GitHub201 transport, genuine writer exclusion and native fresh-job consumer
remain missing. All effect blockers continue to apply BEFORE any rotation.

## Required subsequent increments (not implemented)

1. Independent review of migration/capability/session-fence domain increments;
   no rollout/effects may use this unreviewed source. Verify the complete clean
   source composition including Web verified-revision/CAS and real PG/route tests.
   Complete transport failure tests including lost reply after committed rotation
   (never automatic resume).
2. Independently review and verify the complete mounted API/BFF and safe audit
   composition with production identity/session/IAM configuration and generated
   OpenAPI/client/inventory. This transport increment is not deployed or approved.
3. Review the mounted binding ledger composition and implement trusted foreground
   actual201 response classification, including no resume after lost reply/failed
   ACK. Real PG-produced full-lineage tests now exist; synthetic
   status declarations are NOT proof of remote response authenticity. Keep schema1
   behavior/quarantine rejection unchanged. No missing-ID bypass or fallback.
4. Compose the new memory/budget/receipt primitives into a trusted foreground
   entrypoint: absolute<=2400s, <=1h plan window, bounded non-secret observers,
   measured source/CI/admission ceilings, credential-transport cancellation and
   sufficient downstream full gate/cleanup/quarantine reserves before each effect.
   The bounded local checkout child adapter is not the complete preflight. Persist allowlisted phase
   start/terminal receipts before proceeding. The primitives alone are not this
   executor or measured observer proof. Timeout,
   lost reply, session/custody loss or receipt failure stops and quarantines NEW
   execution. Later readback never resumes authority. Never replay the old600s
   creation child or remove local uncertainty barriers.
5. One encrypted dev-only same-pair PUT after durable intent, fresh absence AND
   genuine external writer exclusion, independently pinned custodian/key/repo.
   Require actual201 before ACK;204, lost reply, ACK failure or gate uncertainty
   quarantines, with no overwrite/read/delete/retry/rollback compensation.
6. Complete canonical same-pair gate, actual matching Web/API SHA/profile/digest,
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
