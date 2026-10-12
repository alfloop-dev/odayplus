# Dev-smoke credential recovery — source/security review protocol

Task: ODP-DEV-SMOKE-CREDENTIAL-RECOVERY-IMPLEMENTATION-001 · Pi · Codex2

**Source review candidate, not independently approved, not deployed, NOT executable.**
Authorization HUMAN-ODP-DEV-SMOKE-RECOVERY-20261011-001 records real foreground
user message `bf235b62` (“授權”) at 2026-10-11T03:27:01.286Z. It is bounded
consent, not a completed execution plan, runtime admission or permission for a
worker to access credentials. The historical incident recovery design remains
unchanged. No live effects have been performed by this task.

## Whole-composition review disposition

The numbered/increment sections below preserve their historical engineering
boundaries; later sections supersede earlier statements about missing adapters.
The implemented source includes the mounted authenticated recovery surface,
same-engine atomic target-only rotation/session fences, strict lineage and bounded
foreground primitives, real full canonical gate integration, and separate native
NO-DEPLOY consumer. It does NOT include an effect/fresh-job dispatch coordinator:
the only composed preflight entrypoint unconditionally HOLDs before reserve or
rotation because no genuine external-writer exclusion can be established by the
existing resources. There is no hidden approval flag or live worker entrypoint.

Codex2's whole-tree security review must cover migrations/Web insertion races,
permission-before-body and CSRF/no-store boundaries, one-attempt/uncertain-response
semantics, original-account/session preservation, legacy quarantine rejection,
strict same-pair provenance, finite custody/deadline/cleanup lanes and the explicit
native env-to-memory amendment. This source review is separate from accepting
that amendment for operational custody, measuring production reserves or approving
an exact execution plan. No source/CI success supplies those authorities.

Before ANY credential effect, a genuinely exclusive external-writer boundary,
independently approved native custody/configuration, normal merged deployment,
fresh exact account/release/source/admission reconciliation, measured reserves,
and a reviewed foreground effect-to-fresh-job coordinator/plan remain mandatory.
If existing grants/resources cannot meet them without widening IAM, continue HOLD;
do not improvise locks, callbacks, boolean receipts, dispatch or retry. The final
verification declaration/receipt locations are in the task evidence packet.

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
- Re-reads authoritative DB-time bounds as the LAST statement of every effectful
  transaction (reserve, issue, consume, binding intent/ACK), after all blocking
  work: one `clock_timestamp()` instant against the persisted ORIGINAL issuer
  session (unrevoked, idle and absolute expiry), the execution window and, for
  issue/consume, the capability expiry. The earlier session `FOR SHARE` lock
  stops explicit revocation but NOT natural expiry while waiting on the target
  credential row, target sessions, capability row or audit; any lapse raises
  inside the transaction and rolls back credential/session/capability/audit
  writes together. One-way quarantine is deliberately exempt (restrictive only).
- Has isolated real PostgreSQL tests for issue/consume races across independent
  pools, rollback at audit failure, post-hash session expiry, natural original-
  session expiry while blocked on a held target credential/session row lock,
  lifetime/replay/credential drift and preserved old history. These are not
  deployed/live proof.

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
Recovery audit events are projected only with their exact safe root/capability/
binding metadata allowlists. Non-object metadata, missing/extra fields or a
non-true redaction marker refuse the ENTIRE authenticated audit read with the
static `RECOVERY_AUDIT_LINEAGE_INVALID` policy error, before subject filtering.
Never drop malformed quarantine/duplicate rows: a surviving otherwise-valid ACK
history could then falsely pass the decoder. The response contains neither a
partial events list nor unsafe metadata. Target-filtered audit includes
target_account_id lineage. No capability hash, password/PHC or cookie is projected.

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

The next increment supplies limited same-session action adapters below. Fresh
source/CI/current-admission/Web+API and account/provenance coordinator, measured
downstream gate/consumer/cleanup reserves, actual GitHub201 transport, genuine
writer exclusion and native fresh-job consumer remain missing. All effect blockers
continue to apply BEFORE any rotation.

## Same-session action increment (unapproved; NOT execution-ready)

`SameSessionRecoveryActions` composes the fixed HTTPS primitive, exact non-secret
plan, parent memory custody and durable phase budget for reserve/issue/rotation
and one-way quarantine only. The trusted foreground must supply its SAME
execution-owned original-admin cookie in memory, independently pinned origin/IP/CA
and an already completed real preflight. This adapter neither authenticates/owns
that session nor establishes approval, source/CI/admission, writer exclusion or
fresh consumer availability. There is no CLI, GET/resume, arbitrary action/route,
login, binding intent/PUT/ACK, gate or cleanup implementation in this increment.
Do not construct it with live credentials in a background worker.

Each action persists its budget phase-start before network access and validates
actual official HTTP200 plus EXACT action-specific payload shape and lineage
before recording completion: root execution/plan/stage/strict false authority,
capability/issue UUIDs and <=600s/window expiry, exact fixed target/tenant and
issue/rotation chain, strict integer target-session count and safe flags. Extra
fields, duplicate/reused event IDs, cross-lineage responses and malformed success
replies stop UNKNOWN: a response body cannot prove rollback of an attempted POST.
Issuance attaches the bearer directly to parent memory BEFORE durable terminal
completion; neither returned safe result nor phase file contains it. Consume
sends only that same retained bearer and random password. Repr/pickle do not
export cookie, password or capability; access in a different PID refuses.

Normal actions cannot repeat or continue after transport, custody, phase-order,
validation or receipt failure, even if a new transport could be constructed or
later GET reveals commit truth. A confirmed root permits only a finite once-only
quarantine call under the retained original session, including after custody
loss. A lost reserve reply has NO locally proven root and cannot fabricate a
quarantine-completion receipt or retry reservation; separately authorized bounded
reconciliation must establish truth. Cookie clearing on close is NOT logout or
zeroization proof. Original interrupted-session cleanup remains UNKNOWN.

Isolated synthetic-response tests exercise the complete action sequence, matching
memory pair, strict lineage/expiry/flags, event replay, phase receipt failure,
PID/custody loss and quarantine. Real isolated loopback TLS covers validated
reservation followed by lost issuance reply with one POST and no continuation.
These tests are NOT actual authenticated Web-session ownership, production timing,
recovery execution, GitHub201, full gate/consumer or deployment evidence. The full
reviewed foreground coordinator and all previously documented execution blockers
remain mandatory BEFORE effects.

## Native fresh-job consumer — implemented narrow ingress PROPOSAL, NOT approved

`dev-smoke-credential-consumer.yml` and `consume_dev_smoke.py` supply a separate
manual protected-dev, NO-BUILD/NO-DEPLOY consumer. This is real source integration,
not an executed workflow, decrypted-value receipt or approval. The proposed
boundary is explicitly different from the preceding universal no-plaintext-env
rule: GitHub's trusted runner->shell->single Python launch projects the encrypted
matched bundle into native process environment. This narrow native CI ingress
MUST receive independent security approval of the exact complete source and be
covered by the final authorized operational plan BEFORE recovery effects. It
never licenses plaintext foreground env/argv/files, a disposable credential child,
foreground GH PAT in CI or unreviewed/offline live dispatch. Rejection of this
amendment remains an execution blocker, not a silently waived rule.

Frozen dependencies are installed BEFORE bundle projection. Only the consuming
step receives the bundle; no user credential is interpolated into shell source.
The trusted Python process removes bundle, all legacy/bootstrap inputs and native
OIDC request handles, parses strict schema2 without fallback, individually masks
nested fields, and keeps the pair in memory. Only the EXISTING native read-only
GitHub token is scoped back into env for canonical gh metadata helpers; no bundle,
password or invoker token is inherited by them. `previous_deployment_metadata`
uses the same latest-attempt/history/ZIP selection as `previous_deployment`, but
returns explicitly UNVALIDATED metadata without starting manifest/Git predicates.
The consumer removes GH_TOKEN BEFORE all canonical manifest/admission/Git work
in its sanitized child. Ordinary `previous_deployment` still validates that same
metadata with canonical admission; no existing release gate is weakened.
Transient OS fork memory/dump/swap
limitations are not called provable isolation or zeroization.

Native origin is NOT accepted from GITHUB_ACTIONS=true or a public run id alone.
The consumer verifies a fresh signed GitHub RS256 OIDC token using issuer-pinned
bounded JWKS and existing frozen cryptography (no new dependency/key). Exact repo,
protected dev environment/ref, actor/id, workflow/source SHA, hosted runner,
manual event, run/id/attempt and NumericDate lifetime are bound. Wrong signatures,
unsigned JWTs, duplicate keys, ambiguous keys, unknown hosts, redirects and bounded
body/lifetime refusals cannot proceed to the gate. Its authenticated own GitHub
run is also verified; no source approval is inferred from the environment.

Google transport identity uses only the observed EXISTING dev WIF provider and
GSA. The process exchanges a second native OIDC token through Google STS and the
existing IAM generateIdToken endpoint, with exact API audience; all values stay
in memory. There is no gcloud child, credential key/file, auth-action output file,
invoker env, new IAM/trust/resource or provider activation. Config drift refuses.

Existing exact whole-tree/source-review/latest reviewed+candidate CI predicates
remain. Current ordinary Runtime Release history is revalidated with canonical
manifest/admission predicates. The foreground-only DeployedDevReleaseObserver
guard is NOT changed or bypassed: the new CI observer first cryptographically
proves its own distinct NO-DEPLOY workflow, so excluding its run from the separate
deployment workflow history cannot hide a live-changing attempt. No cached pass,
consumed-lease replay, fresh build, tag move or competing rollout is introduced.

The unchanged canonical FULL dev-admin gate receives the pair in memory and owns
fresh target login, actual principal/lineage/negative probes and own logout/replay
checks, including HTML-form session cleanup. Its authenticated audit observation
must show exactly one recovery201 ACK strictly BEFORE the new run was created;
older/same-job snapshots cannot produce consumption evidence. The reviewed
foreground coordinator must dispatch only AFTER real ACK. A failed ordering or
missing ACK still fails the gate/result and never permits resume or a write.

The native wrapper retains execution-owned cookies in memory from EVERY login
reply, including malformed replies, and unconditionally cleans them on evaluator
early return or exception. Each original cookie gets at most ONE logout attempt;
canonical logout is observed, not repeated. Original-cookie session401 and denied
principal readback are mandatory before returning success. Lost login replies
without a usable cookie, lost/malformed logout acknowledgement or late/unavailable
cleanup remain UNKNOWN even if a later read is401; no blanket revocation, retry,
recovery authority or consumption receipt is introduced. Cookie reference release
is best effort, not proof of zeroization. The original interrupted admin session
is outside this new consumer's owned inventory and remains unproven.

Uncertainty is latched IMMEDIATELY, not only at the final cleanup verdict. The
first lost/malformed login reply (including a cookie with any non-success reply),
unacknowledged/lost/duplicate logout or owned-inventory overflow sets a one-way
latch shared by the API and Web observers. The canonical evaluator intentionally
continues after a failed logout (negative and successful HTML-form logins), so the
wrapper refuses every later request locally before transport except bounded
cleanup for an ALREADY-OWNED cookie: its single unattempted logout and the
original-cookie session/principal revocation reads. No later login can create a
new privileged session after an unknown prior effect; the final result stays
UNKNOWN, with no retry or account-wide revocation.

A failed post-logout revocation proof is itself such an effect. Once an owned
cookie's single logout has been attempted, every later non-logout request with
that cookie is revocation/replay proof: `/auth/session` must be a clean401 and
principal or protected admin API replay a clean401/403. A lost/timed-out read,
200, 5xx or any other status latches UNKNOWN at that read, BEFORE the evaluator's
negative/form logins, and a later401 during final cleanup cannot clear it.

Owned-cookie fencing depends on extracting EXACTLY one session cookie. The
canonical evaluator joins every response cookie into one `Cookie` header, so the
wrapper accepts only an absent header or a single `__Host-oday_web_session=<v>`
pair. Any extra/duplicate/other cookie, separator or whitespace is ambiguous: it
latches UNKNOWN and is refused locally before transport, never prefix-matched or
guessed. A login reply carrying ANY other non-empty cookie beside the session
cookie is likewise malformed: the session cookie is owned for cleanup but the
latch is set at that reply. The owned cookie's single logout then happens only
in bounded cleanup, with a single-pair header; no later login reaches transport.

Response custody is decided from RAW `Set-Cookie` multiplicity, never from the
canonical name->value map alone (that map collapses duplicate names, last wins).
The canonical parser additionally exposes the ordered raw (name, value) fields
in memory (never repr/persisted); canonical checks are unchanged. On ANY reply,
a duplicate name, a malformed field or a normalized map that disagrees with the
raw fields latches UNKNOWN at that reply. For a login reply, EVERY distinct
safely-known issued session value is owned first, so each receives exactly one
bounded logout plus revocation reads; an unusable value stays unaccounted and
therefore UNKNOWN. A logout acknowledgement requires exactly one raw session
deletion and no other non-empty cookie. Login replies must also match the
canonical evaluator's exact predicate for their channel: JSON positive200
ok/subject equal to the submitted username, JSON diagnostic401
AUTH_INVALID_CREDENTIALS, form positive same-origin303 `/operator?view=admin`,
and form diagnostic same-origin303 `/login` whose strictly parsed query is
EXACTLY `error=AUTH_INVALID_CREDENTIALS` plus `returnTo=/operator?view=admin`
(no missing/wrong returnTo, extra or blank parameter, duplicate or fragment).
The foreground gate applies the same complete diagnostic form predicate, and
its raw transport, which already refuses multiple login `Set-Cookie` fields,
now retains every distinct safely-known raw session value for once-only cleanup
before stopping UNKNOWN.

Native credential-step bounds reserve preflight180 + gate300 + cleanup60 seconds
in one monotonic540 envelope (NOT measured production ceilings). Signal-bound
HTTP refuses late work; cleanup gets a separate reserved absolute window, not a
refreshed full budget. OS-stuck calls are UNKNOWN/failure, not hard-real-time
success. Only a minimal allowlisted positive result is retained; raw gate details,
audit blobs, cookies, JWTs, passwords/PHCs and password-derived hashes are never
artifacts. Missing gate/cleanup or failed/late job produces no consumption receipt.
Mocked pipeline and ephemeral OFFLINE RSA/HTTP tests are explicitly not live
consumption or F11 evidence. External writer exclusion, full foreground coordinator,
actual budget measurements, independent review/deployment and final execution
plan remain unresolved. This source increment does not authorize effects.

## Canonical same-pair gate / failure coordinator increment (unapproved)

`ForegroundSamePairGate` prevalidates the real canonical GateConfig before
reservation and runs the complete canonical evaluator in the custody parent only
after the same action object's original201 plus durable ACK. It binds ALL schema2
identifiers/lineage through the strict decoder; no bootstrap/initial-password
fallback, supplied passing report, gcloud worker trigger or credential child.
Pinned numeric TLS enforces hostname/CA verification, absolute phase deadlines,
bounded response/request totals, no DNS/proxy/retry/redirect credential forwarding.
Gate body/request limits are4MiB/256, with a separate bounded512KiB/16 cleanup
reserve; exhausted gate I/O cannot bar the known-cookie cleanup lane.
Application bearer and API transport identity are separate mandatory foreground
memory inputs. Empty application bearer remains a canonical refusal; the native
consumer increment below supplies it without config-guard relaxation.

The canonical evaluator has an optional in-process cleanup barrier, default absent
for existing callers. It can only STOP on already failed checks before normal or
HTML own logout; it cannot waive, alter or replace any check. The recovery adapter
retains each newly issued diagnostic cookie BEFORE response-body validation,
records login/logout start/terminal identifiers without credential values, and
on gate failure quarantines the new root using the retained original session
BEFORE remaining own cleanup. Unknown/attempted logout is never retried. Other
known execution-owned cookies are still cleaned once within the single reserved
cleanup phase. Missing login response/quarantine/logout remains UNKNOWN, never
blanket revocation or recovery resume. The old interrupted original-admin session
remains UNKNOWN. Successful same-pair gate does NOT prove decrypted GitHub binding
or full/F11 acceptance and still leaves `fresh_consumer` pending.

The adapter STOPS immediately, inside the request, on a complete but invalid
logout reply (anything but200 + `ok:true` + cleared session cookie, e.g. HTTP500,
missing ok or missing clear) and on any failed/lost/non-denied post-logout read
with that cookie (session not401; protected replay not401/403). The logout's
terminal receipt is then UNKNOWN, not completed; `execute()` quarantines the new
root once and runs once-only known-session cleanup. The evaluator's subsequent
negative/form logins never reach transport, so no new session follows an
unproven cleanup.

Every gate login reply, positive AND diagnostic, is validated inside the request
against the canonical evaluator's own predicate for that operation: positive
JSON 200 + `ok:true` + subject + the ONE cookie retained from that reply;
positive form 303 to same-origin `/operator?view=admin` + that cookie; diagnostic
JSON 401 `AUTH_INVALID_CREDENTIALS` with no cookie; diagnostic form 303 to
same-origin `/login?error=AUTH_INVALID_CREDENTIALS` with no cookie. Anything else
(e.g. complete 200/500 without a retained cookie, 500 with a cookie, diagnostic
reply carrying a cookie or 500) raises BEFORE a completed receipt or any further
transport: login UNKNOWN, terminal receipt UNKNOWN, quarantine then once-only
cleanup of any acquired cookie, and own-session cleanup can never be reported
completed. Request `Cookie` headers must be absent or exactly one session pair.

This incremental adapter is NOT the full preflight-to-fresh-job coordinator.
The next increment must compose fresh source/admission rechecks and a separately
normally authorized actual fresh-job boundary, then exact-head declared/Web
verification and task_finalize PR. Genuine external writer exclusion, production
measured reserves, native custody amendment approval, final reviewed plan and
ordinary deployment still block all effects. Offline synthetic gate/ACK/session
fixtures do not establish any of these authorities.

## One-shot encrypted staging increment (unapproved; NOT a full coordinator)

`SameSessionRecoveryActions` adds once-only binding-intent and ACK using the
SAME retained original-admin cookie. Exact mounted binding metadata must match
plan/root/capability/issue/rotation and safe flags; ACK must retain the intent's
issuer-session ID. Reconciliation must finish in the budget before intent, but
this adapter cannot independently authenticate that caller-driven reconciliation.
It is not session ownership, account preflight or writer-exclusion proof.

`RecoveryGitHubSecretAttempt` implements bounded authenticated GitHub HTTPS with
independently pinned human custodian, numeric repository ID, dev public-key ID/
bytes and numeric API address, verified CA/hostname/SNI, no DNS/proxy/redirect/
retry/reconnect/children. Prepare and the immediately-before-PUT observation each
freshly check /user, exact repository, absent secret metadata and the SAME pinned
key. One absolute phase deadline covers all reads/TLS/send/parsing, with the
existing aggregate64KiB/body16KiB framing limits. No plaintext value is retrieved.
The store builds strict schema2 from the actions' retained SAME random pair and
actual intent/rotation IDs, encrypts with the existing frozen PyNaCl sealed box,
and makes ONE PUT of only key ID/ciphertext after a durable PUT-start receipt.

Only an original fully framed HTTP201 with empty success body AND successful
terminal fsync enables the actions' ACK. No public201 argument, passing callback,
metadata readback or later GET reconstructs it.204/lost/malformed/late response,
key/custodian/repository/absence drift, custody loss or receipt failure poisons
normal progression; the caller must take the bounded quarantine/cleanup lanes.
A failed ACK never permits another PUT/ACK. The primitive itself does not own
cleanup/quarantine orchestration, full gate or normally admitted fresh dispatch.

Offline mocked metadata and ephemeral loopback TLS test actual encrypted request
composition and original201/lost/204/redirect/truncated replies. Fixture-only
sealed-box decryption is NOT GitHub decrypted-value or fresh-consumer proof.
No live GitHub/account/session/secret operation was performed. Managed-memory
reference release is not provable zeroization.

**External writer exclusion is STILL unresolved.** Absence plus this once-only
store/local budget does not exclude another API writer; GitHub PUT is not
conditional-create. The incomplete full coordinator MUST refuse BEFORE rotation
until a genuine independently reviewed exclusion boundary exists. These primitives
expose no exclusion boolean/lock proof and must not be used live by themselves.
Full source/admission/Web+API/account/provenance preflight, session ownership,
measured reserves, full gate/cleanup/fresh-job composition and custody approval
remain outstanding. This increment is durable source progress, not completion.

## Owned Web session increment (unapproved; NOT a full coordinator)

`OriginalPasswordMemory` accepts only an already-approved foreground in-process
password, with redacted representation, pickle/PID/closed fencing and no env,
argv, file or worker loader. It is NOT authorization or a new custody-ingress
approval. `ForegroundOwnedWebSessions` owns one fresh original login during
preflight and one same-pair target login during reconciliation. Fixed Web
login/session/principal/logout adapters reuse the absolute numeric-connect TLS
transport; the recovery route's required-cookie/status contract stays separate.
Every login and own logout has a durable allowlisted sub-operation start/terminal
inside its active phase. Cookies remain private memory, never phase receipts.

The login response must issue one Secure/HttpOnly/Path=/, domain-free host cookie,
return the exact requested username, and match fresh session and API principal
readbacks (fixed UUID/tenant, actual original auditor+platform_admin or target
pureadmin). Successful validation PLUS terminal persistence precedes composing
once-only recovery actions with that same original cookie. A validated own cookie
is retained for cleanup even if the login body is malformed/lost; a missing cookie
leaves session creation/cleanup UNKNOWN. Neither case allows a new login, a
session-list sweep, account-wide original revocation or later continuation.

Cleanup attempts each known execution-owned cookie once, using POST logout200
then GET session401 with the ORIGINAL cookie, not the response's deleted cookie.
It does not claim cleanup of an unknown login or the historical interrupted
original-admin session. Quarantine must be orchestrated BEFORE cleanup while the
original session is retained; this primitive does not automatically quarantine,
reconcile accounts, run the full gate or dispatch the fresh consumer. PID/custody,
receipt/deadline or principal uncertainty stops progression. Close releases
references only, not a logout/zeroization proof. Offline mocks/loopback TLS are
not live login, admitted deployment, recovery or consumer evidence.

Full source/latest review-CI/current admission, matching actual Web+API tuple,
original/target/account/old NULL quarantine/invitation reconciliation, whole-gate
and fresh-job orchestration, genuine external writer exclusion, measured budgets,
native ingress approval and independently reviewed final operational plan remain
required before effects. No worker received a live password/cookie or called a
live account, session, secret or cloud service.

## Fresh Web account/provenance observer increment (unapproved; NOT complete preflight)

The session owner now performs five fixed one-shot HTTPS GETs over its retained
original cookie: actual Web+API own release identity, complete user inventory,
complete tenant audit, old journal, and new recovery inspection. The before-state
is kept only in parent memory; callers cannot supply a baseline or a passing
receipt. Reconciliation requires the same owner/budget/custody/actions/plan and
successful same-pair target login. It checks original/target identity, exact
active roles/scope/source, invitation issue/accept, immutable old quarantine and
NULL binding, absence of any prior recovery before effects, and exact new
root/issue/rotation after rotation. All public selected fields of ALL accounts
must remain unchanged, not just the target. Server binding still separately
fences private credential revisions/times; public snapshots cannot prove passwords
or all pre-existing session preservation. Historical original-session cleanup
stays UNKNOWN.

Both observations have durable sub-operation starts/terminals. Any malformed,
missing, duplicated, drifted, pending/quarantined or over-limit evidence stops
normal progression; a later GET cannot resume an unknown rotation. Reads share
one active absolute deadline and the existing16KiB JSON/64KiB total HTTP bound,
no query filtering, pagination, retries or cached pass. A history too large for
that bound is a concrete preflight blocker requiring reviewed bounded inventory
support, not permission to truncate/filter away old provenance. No raw inventory,
audit metadata, email, cookie or credential is persisted in phase receipts.

This completes only the account/release-identity portion of preflight and
reconciliation. Remote independently pinned whole-tree review/latest CI and
current ordinary admission observers, measured production ceilings, full gate,
quarantine-before-cleanup and fresh-job orchestration remain uncomposed. The
observer does NOT authorize reserve/rotate/PUT and is not a complete foreground
entrypoint. Offline fixtures and loopback TLS tests are not live evidence.

## Bounded remote source observer increment (unapproved; NOT complete preflight)

`TrustedGitHubSourceObserver` reuses the existing canonical merged-PR, full
reviewed/candidate Git-tree equality, latest task-review-gate writer and latest
required reviewed+candidate CI/check predicates. The foreground independently
pins candidate, reviewed head AND entire reviewed tree plus writer/workflow/app/
required-check roots, never from plan flags or a passing receipt. Both remote
commit trees must match that independent tree, not merely each other. Every
observation rereads all evidence; any uncertainty permanently stops the instance.
A later successful GET cannot restore execution authority.

Only fixed GitHub source GET paths are allowed. Token stays in parent memory;
no gh/credential child, environment/proxy, retries or redirects. HTTPS retains
normal CA/hostname verification. A main-thread signal guard and per-request
remaining allowance share ONE absolute phase deadline, never a fresh timeout
per endpoint. Complete JSON bodies are bounded64KiB; duplicate keys/nonfinite
numbers, compressed responses, next-page links and incomplete list counts fail
closed. If evidence exceeds the bound, hold before effects rather than truncate
or select an older passing review/run. Another runtime's active timer is never
replaced. PID/pickle/close fencing and static errors keep tokens/raw evidence out
of receipts. Receipt includes only the pinned source identifiers and false
authority/binding flags; coordinator must still persist actual phase starts and
terminals. Signal/OS-stuck calls are NOT hard-real-time termination guarantees
or measured production bounds: missing/late completion remains refusal.

Offline mocked canonical records and real timer/dribble tests cover full-tree
pinning, latest pending/failed review/CI supersession, skipped/wrong-app checks,
incomplete/duplicate/oversized evidence, malformed JSON, redaction, timer ownership
and no continuation. These are NOT independent live review/CI, operational
custody approval or recovery evidence. Current ordinary admission history still
needs a bounded read adapter without credential-bearing children; source approval
alone cannot admit deployment. Full gate/fresh-job/quarantine-before-own-cleanup
coordinator, genuine writer exclusion, measured bounds and exact final-plan/
native-ingress security approval remain BEFORE-effect blockers.

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
5. Compose the new one-shot encrypted staging and actual201-to-ACK primitives
   into the full coordinator ONLY after genuine external writer exclusion,
   independently pinned custodian/key/repo and real reconciliation. The primitives
   above do NOT establish those authorities.204, lost reply, ACK failure or gate
   uncertainty must trigger orchestration of bounded new-root quarantine/cleanup,
   with no overwrite/read/delete/retry/rollback compensation.
6. Complete canonical same-pair gate, actual matching Web/API SHA/profile/digest,
   original preservation, exact provenance and cleanup of only execution-owned
   sessions. Independently normally admitted fresh-job actual bundle consumer
   proof is separate from staging ACK, same-process gate and full/F11 acceptance.

## Bounded current ordinary-admission observer

`TrustedGitHubAdmissionObserver` replaces the credential-bearing gh history
transport for the foreground recovery composition. Every invocation rereads the
complete bounded Runtime Release history and each rerun's jobs, dev environment
proof, restoration evidence when applicable, and retained deployed manifest.
Canonical `live_step_outcome`, `validate_manifest`, `validate_release_admission`
and profile predicates are reused; selection mirrors canonical newest-changing-
attempt semantics. A failed-after-live commit, unknown live step, unproven
restoration, newer different release or missing/expired/ambiguous manifest refuses,
never chooses an older convenient success. Environment URLs additionally require
exact repository/run paths (no run-ID prefix or other-host matching).

One parent-memory main-thread absolute timer bounds all GETs and parsing; no gh,
credential child, lease replay, dispatch, proxy/env credential loading, retry or
cached admitted flag. JSON is duplicate-key/NaN/encoding/page-completeness checked,
with 64KiB per response, 4MiB aggregate, 256 request and canonical page limits.
Retained ZIPs are bounded to 2MiB, one exact file, no filesystem extraction.
Canonical manifest validation itself invokes Git internally. It therefore runs
in one fixed NON-SECRET observer child, not in the credential-owning parent:
only manifest/tuple input, sanitized environment, no inherited FDs/credentials,
bounded stdin/stdout/stderr and whole-group cancellation/reaping under the SAME
absolute deadline. Missing candidate objects refuse before canonical code could
fetch. The coordinator must first verify the exact reviewed clean checkout;
the validator child is not source approval. Direct in-parent canonical validation
was caught by an offline regression and is not silently exempted from custody.
GitHub artifact downloads genuinely require a signed redirect: only that ZIP GET
may follow **one** freshly returned HTTPS URL to an independently pinned exact
GitHub-storage hostname, with no API Authorization/cookies. No second redirect,
arbitrary authenticated redirect, signed-URL receipt or token transfer is allowed.
The exact trusted storage host must be established in the reviewed preflight;
changing storage topology is a blocker, not permission for a wildcard fallback.

Observer evidence does not grant execution authority, source approval, serving
identity, writer exclusion or native custody approval. OS-stuck-call hard-real-time
termination and measured production bounds are not claimed. This incremental
implementation still requires the complete gate/fresh-consumer/uncertainty
coordinator, final exact-head verification and independent review before effects.

## Concrete preflight-to-HOLD coordinator (unapproved; no effect path)

`ForegroundRecoveryPreflight.observe_and_hold` now composes the actual typed
checkout, remote whole-tree/review/latest-CI, current ordinary admission, owned
original Web session/account/release/provenance and pinned secret-metadata
observers. The canonical same-pair gate's REAL config is validated at construction,
before any login. The independently pinned checkout/tree/source/custodian must
match; the checkout must be the actual source used by admission's fixed canonical
validator child. No arbitrary observer callbacks, passing baseline/receipt,
exclusion boolean or supplied gate report is accepted.

One durable preflight start and allowlisted observer/login/account sub-operation
starts precede each call. All observers share the SAME absolute preflight deadline.
Checkout/source/current-admission are reread after account and absent-bundle
observations; changed deployment run/attempt refuses even at the same SHA. Raw
source/history/account/key/token data never enters receipts. A failed or lost
phase/observer terminal cannot authorize later effects. Original child status is
retained when available, not invented from output. These are offline adapter
bounds, not production measurements or hard-real-time guarantees.

**The effect path is deliberately absent.** Even complete observations return
`preflight-held`, permanently stop the budget, and clean only the fresh original
session once under the reserved cleanup phase. There is no reserve, capability,
rotation, intent, PUT, ACK, gate invocation or fresh-job dispatch. No new root is
created, so no fake quarantine receipt is claimed. Unknown login/cleanup/receipt
remains UNKNOWN; old incident original-session cleanup remains UNKNOWN. Owned
custody/observer instances are closed and cannot resume this execution. Reference
release is not zeroization. This diagnostic boundary is not permission to invoke
it live in a background worker.

The hold specifically exposes missing genuine external writer exclusion, native
application-credential implementation, approval of the native custody amendment,
measured production reserves and the reviewed operational plan. Matching source,
absent metadata and a process-local receipt barrier supply NONE of these. The
native e46 source at this historical checkpoint left canonical application's
bearer empty; its mocked consumer tests were not full-gate/consumption proof.
The subsequent native identity increment below addresses that source gap only. A future reviewed effect
coordinator and actual fresh-job boundary must resolve those gates BEFORE
rotation, not add an `approved=true` escape or dispatch from this diagnostic.

Offline composition tests use synthetic GitHub/Web metadata with real canonical
source/admission selection, strict account/provenance validation, phase receipts
and owned cleanup. Local checkout/manifest child transport is explicitly mocked
in this composition fixture; its actual bounded child/canonical predicates have
separate existing regressions. No fixture is live consent/admission/exclusion,
GitHub decrypted-value evidence or a completed recovery.

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
env/argv/file custody rule. The newly implemented native env-to-memory consumer
above is an EXPLICIT narrow boundary amendment PROPOSAL, **not approved here**.
It resolves a source implementation gap but cannot establish approval, measured
runtime viability or actual fresh consumption by itself. Independent review of
the exact whole composition and this native launch boundary, plus the final
operational plan, remain mandatory. No fallback or same-job refresh is permitted.
A cosmetic commit/rebuild, second rollout, imagined decryption or mocked consumer
receipt cannot resolve this gap. Hold effects until the real normally admitted
fresh consumer and finite measured budget exist.

Target session revocation will never prove cleanup of the interrupted ORIGINAL
admin session; that historical uncertainty remains explicit. No business/data/
model effects, source activation, role widening, IAM changes, SQL/bootstrap
recovery or F11 self-approval are authorized.

## Native application identity increment (source only; unapproved)

The consumer now reproduces the existing `origin/dev` Runtime Release application
identity selection (`deploy_cloud_run_waji.sh`): protected-dev
`vars.ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT` and the first `vars.ODP_AUTH_AUDIENCES`
audience. Actual protected-dev metadata independently observed2026-10-11 requires
EXACT `oday-dev-smoke-operator@odayplus-runtime-20260825.iam.gserviceaccount.com`
and APPLICATION audience `https://oday-api-767864276141.asia-east1.run.app`, NOT
transport origin `https://oday-api-2l6wuyl67q-de.a.run.app`. Missing/drifted config
refuses before federation. Existing deployer WIUser permits federated STS access
to mint deployer transport ID token and600s deployer access token IN MEMORY;
ONLY that impersonated deployer access calls smoke generateIdToken with application
audience. Existing smoke TokenCreator binds deployer, not federated principal.
Two ID tokens use `includeEmail=true`; no grants/vars/principal-map widening.
The application token is supplied to GateConfig
and the actual API client, never to env/argv/files. All canonical config checks
must pass before evaluator/login. There is no empty-bearer waiver, caller JWT,
legacy credential fallback, key/signature creation, grant installation or role
change. A denied/empty/malformed token response stops without retry.

These repository variables are trusted native deployment configuration, NOT
proof of current IAM grants or principal-map roles. Before effects, the final
independently reviewed operational plan must reconcile their exact values with
the currently admitted deployment, existing impersonation permission, and actual
least-privilege application principal mapping. If existing grants cannot mint
these identities, HOLD; do not add IAM or map the recovered human target to
business roles. Original worker source checkpoint read no live config; later
foreground read ONLY non-secret protected-dev variables and exact two existing SA
IAM policies. Those actual5-original proofs are retained in
`support/handoffs/dev-smoke-credential-recovery-20261011/native-existing-config-20261011/`.
They prove configuration and binding selection, NOT permission to mint/dispatch,
writer exclusion, native custody or operational approval. No token was generated.

### Normal self-password failure safety (source, not recovery execution)

Production `changePassword` CAS and ONLY that account's old-session revocation
commit in one explicit READ COMMITTED transaction. Separate revocation statement
AFTER credential-row lock/CAS sees an old-session INSERT committed while CAS
waited; a single CTE's earlier snapshot would miss it. Revocation SQL failure
rolls back both. Return the committed revision only after COMMIT, then guarded
replacement insertion. Mint/INSERT/lost INSERT reply/seal failure returns503/no
cookie and once-only cleanup of newly server-owned SID, no account-wide retry/
compensation. Isolated actual PG production adapters/mounted route exercise late
commit lock witness, rollback, lost replacement reply/owned cleanup, and preserve
original account credential/session rows. These are no live credential effects.

The offline regression now invokes the REAL complete canonical evaluator with
synthetic HTTP/lineage and mocked source/admission/identity observers, in addition
to negative bounded IAM transport tests. This proves source composition past the
previous `config:operator_credential` failure, NOT live native custody, actual
fresh-job consumption, measured timing or deployment admission. Independent
native-ingress review, writer exclusion, final operational-plan review and
finite production measurements remain BEFORE-effects blockers. The diagnostic
preflight remains an unconditional HOLD; no effect/fresh-job dispatch was added.

### Request namespace / committed WORM boundary (auxiliary, unapproved)

Client correlation headers cannot append to the five server-owned provisioning,
binding and recovery namespaces. Ingress/credentials/authentication/authorization
normalize them before any audit; incompatible direct audit appends statically
refuse. Strict existing ledger validation is unchanged, not a poison-row filter.

Durable audit inserts event and WORM intent in the same outer SQL transaction.
Only after successful commit/connection return may bounded publication claim an
intent durably and send a committed envelope. Rollback/commit failure sends none;
lost transport/receipt/ack remains UNKNOWN/attempted without automatic retry.
SQL success and a destination sink ID do not establish WORM delivery. New
expand-only outbox migrations/Alembic0025 are required for WORM-enabled durable
writers; no historical delivery is backfilled. Pending intents may be explicitly
flushed; no background executor/CLI or operational authority is introduced.

Version2 committed envelopes bind commit state in their checksum. Bare ledger
replay and legacy/unmarked WORM restore refuse; trusted maintenance must supply
and validate committed envelopes through authenticated immutable-sink transport.
A checksum/JSON marker alone is not authority, permission or proof of a remote
GitHub201. Existing legacy orphan objects are retained for adjudication, not
rewritten into committed evidence. See the auxiliary source evidence in
`audit-commit-boundary-repair.md`; production migration/execution stays blocked.
