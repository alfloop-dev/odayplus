# Recovery implementation evidence — source/security review packet

Owner Pi · independent reviewer Codex2 · no operational approval.

## Submission boundary and final verification declaration

The full task-branch composition now contains the mounted same-account API/BFF,
PG capability/rotation/session fences, strict recovery-lineage consumer, bounded
foreground custody/observation/action/staging/gate primitives, and separate native
NO-DEPLOY consumer. The chronological increment notes below are historical, not
claims that their older receipts verify the submission SHA.

`ForegroundRecoveryPreflight.observe_and_hold` unconditionally stops before
credential effects. Genuine external-writer exclusion is unavailable with the
existing GitHub API/resources; an effect/fresh-job dispatch coordinator is
therefore deliberately absent. Independent review is requested for the implemented
source and the explicitly proposed native CI custody amendment, NOT an executable
recovery plan, permission to dispatch, deployment admission or F11 acceptance.
The protocol document lists the exact remaining before-effects blockers.

Final verification is run only AFTER this packet is committed, on the clean
submission anchor. The canonical commands are read from the LIVE status
root by `task_verification.py`. Additional declared selections are:

- `uv run --frozen --python 3.12 pytest tests/security/test_dev_smoke_recovery_gate.py tests/release/test_automatic_dev_deployment.py -q`
- `pnpm --dir apps/web test src/lib/auth/__tests__/password.test.ts src/lib/auth/__tests__/identityStore.test.ts src/lib/auth/__tests__/localAuth.test.ts src/lib/auth/__tests__/sessionStore.test.ts src/app/login/__tests__/route.test.ts src/lib/auth/__tests__/login.test.ts src/lib/auth/__tests__/loginThrottleRoute.test.ts 'src/app/api/v1/[...path]/route.test.ts'`
- `pnpm --dir apps/web lint`
- `pnpm --dir apps/web build`
- `pnpm --dir apps/web bundle:budget`
- `uv run --frozen --python 3.12 python delivery_toolchain/openapi/check_drift.py --base-ref origin/dev`
- Frozen Python3.12 Ruff on every changed Python file (exact expanded command
  retained in its receipt).

Original final-head command/exit/duration/selection receipts are written to the
existing `.orchestrator/evidence/` store and identified by a canonical task note
before `task_finalize.sh` publishes the real PR. They are NOT predicted here or
copied onto a different SHA by a post-test evidence commit. The committed
`receipts/` below preserve earlier successful AND failed measurements unchanged.
No test is repeated merely for counts. A failure prevents a passing submission
claim; any source repair requires a new clean anchor and exact-head verification.

Offline pytest/Vitest use `NODE_ENV=test`; the production Next build/budget use
`NODE_ENV=production`. Existing frozen donor Node dependencies are privately
byte-copied, not installed or upgraded; workspace links resolve this candidate.
The package-lock must match the donor AND `origin/dev` before any tests. The
actual TypeScript-store PG child must run, not be credited from a skipped case.
The canonical 300kB route budget is unchanged. Build products/dependency copies
are disposable local verification artifacts, not runtime or credential evidence.

Base verified by `git fetch origin dev` and equal HEAD/origin-dev
`e995aa26032542160a177d421ad13157dd6db058` on the allocated task branch.
No frozen plan/invitation delivery was edited or cherry-picked.

## Reviewer repair and required base composition (2026-10-11)

PR1459 rejected head703703682 is preserved, not reset/amended. History-preserving
merge71ab654b0 composes origin/dev1286834caf3a (fetched before work), conflict-free.
Anchor90560f073 returns the self-password-change CAS's committed revision and
forwards it to production replacement-session insertion. Mounted production-store
SQL adapter regression verifies successful password change, no caller revision,
no newer credential read, own old-session revocation and other-account preservation.
The production change now refuses stale verification rather than overwriting a
concurrent recovery. Dedicated adapter negatives cover missing/stale revisions.

Native consumer repair retains ONLY newly owned cookies in memory, including
malformed login responses; evaluator users/audit/missing-ACK/principal early
failures and exceptions still cause once-only logout and original-cookie session/
principal verification within the existing absolute reserve. Lost login custody
or logout acknowledgement remains UNKNOWN, with no retry/account-wide revocation
or positive consumption report. Canonical gate checks/provenance are not waived.

`automatic_dev.previous_deployment_metadata` extracts the SAME latest-attempt/
restoration/environment/artifact history without manifest/Git validation. The
ordinary `previous_deployment` continues to validate by default. The native caller
removes GH_TOKEN BEFORE all manifest/Git work in its existing sanitized child.
Regression runs the actual history/ZIP/child-validator chain with only gh HTTP
stubbed (not a mocked `previous_deployment` success).

Exploratory focused Web command first exited127 (pnpm absent). The documented
frozen donor dependencies and cached Corepack9.15.9, Node22.23.2 were reused with
matching root lock hash vs origin/dev, network disabled and explicit .bin PATH.
Focused Web tests then exited0 (14 passed,1 isolated PG test skipped); that skip
is NOT PG proof. Uncommitted native/release regression exited0 twice; the second
selection intentionally included the newly added real metadata-chain regression.
These exploratory runs are not immutable final-head receipts. Full declared
exact-head verification follows the committed repair; original failures remain
failures. No live credentials, account/session/secret/cloud/recovery effects or
custody/deployment approval. All preflight HOLDs and old uncertainty remain.

## Scope

- New internal shared recovery plan/root/capability and PostgreSQL tests, not a
  mounted API or completed recovery protocol.
- Tests cover strict scope/expiry/redaction, durable original-session checks on
  every operation, original/target identity and old NULL-binding preservation,
  target/lineage drift, one-way quarantine, audit rollback and reservation races
  across independent PostgreSQL pools. The first root anchor did NOT cover
  capability/rotation; the newer increment's explicit coverage is recorded below.
  Neither anchor claims transport/consumer or live proof.
- New protocol document enumerates unimplemented increments and concrete writer
  exclusion/native CI secret-custody gaps. No operational permission claimed.

## Verification boundary

At clean immutable anchor `4eb247bd2e5f6520085da2a97b7d2a74df4a024a`,
`delivery_toolchain/git/task_verification.py run --task-id <task> --timeout 600`
executed the four canonical declared commands. Original receipt JSON is retained
in [`receipts/`](receipts/) including failed attempts, not rewritten as passes.

| Command | Receipt/result | Duration |
| --- | --- | --- |
| `git diff --check` | `5819d2985c4a029c`: exit0 | 0.015s |
| Declared six-file `uv run --frozen --python 3.12 pytest ... -q` selection | `73231b824b1b9b68`: exit0, all displayed cases pass | 525.095s |
| `uv run --frozen --python 3.12 python delivery_toolchain/governance/check_code_boundaries.py` | `402102955c6f8886`: exit1, inventory stale | 10.982s |
| `pnpm --dir apps/web typecheck` | `723428db5796cf8d`: exit127, pnpm unavailable | 0.001s |

Initial uv/pnpm attempts exited127 because the worker PATH omitted uv's standard
`/home/lupin/.local/bin` location. An explicit retry reason preserved those
receipts before retrying identical declared selections with the corrected PATH.
Pytest completed via the original terminal exit receipt; no test-count-only
rerun or log-based wait was used. The PostgreSQL fixture uses the existing
isolated bundled server; no live account/DB credential was supplied.

The subsequent inventory/evidence checkpoint changes **no tested source** but
has a different Git head: these are previous-anchor receipts, not exact final-head
verification or review readiness. Inventory now includes the three added Python
files. At clean inventory anchor `68e793e7d7f0`, the exact declared boundary
command was remeasured using the existing shared verification receipt policy
(single-command adapter in worker scratch, not a new task/status writer):
`1fe5df74d5746247`, exit0, 11.995s, 1224 files. Its JSON is retained here.
This later evidence-only commit does not transfer prior-head receipts onto its
own SHA. Web verification remains explicitly unproven, not waived because this
increment has no Web diff. The whole task is NOT ready for formal review/finalization.

## Capability increment at clean c4122b485f67

`c4122b485f674dd48586585638968566f531acd3` adds Alembic0023/expand-only000029,
independent hash-only256-bit <=600s single issuance, issuer-session/root/plan/
source/baseline and credential-update-time fencing, a fixed independently
committed five-submission budget, NFKC + Argon2id and same-engine atomic target
credential update/committed pre-cutoff session revocation/consume/audit. It never
changes original credentials/other sessions or old creation history. A nested
caller transaction is refused, rather than permitting budget rollback.

The declared unchanged six-file selection was run once at this clean anchor via
`task_verification.py run --task-id <task> --timeout 1000`, original receiptstore
and terminal exit status (not log-summary wait). Preserved original JSON:

| Command | Receipt/result | Duration |
| --- | --- | --- |
| `git diff --check` | `4fb5908d70149b47`: exit0 | 0.019s |
| Declared six-file pytest selection | `2fbd5f98cd119c46`: exit0 | 575.823s |
| Declared boundary check | `8edf341b441a3398`: exit0 | 11.502s |
| `pnpm --dir apps/web typecheck` | `fe16e377cab00751`: exit127, unavailable | 0.001s |

New real PG cases include separate-pool issuance/consume races (at most one
success), audit failure rollback, actual deferred-trigger COMMIT failure rollback
of credential/session/capability/audit with the independent budget retained,
expired/quarantined/replayed capability, fresh or revoked issuer session, source/
credential/role drift, post-Argon session expiry, NFKC PHC verification, token
repr/audit redaction, lifetime and nested-transaction refusal. Old regression
selection remains unchanged. No tests were rerun solely to count cases.

Source/API/consumer acceptance is incomplete: mounted auth/CSRF/no-store and safe
audit projection, strict versioned bundle/gate integration, bounded foreground
custody and transport-failure handling still need implementation. Read-only
origin/dev inspection also identifies an old-PHC login / later session-insertion
race: current domain revocation covers committed target sessions at its cutoff,
not credential verification in-flight across Web `/login` session creation. A
reviewed credential-revision session fence and real interleaving regression are
required before mounting/execution; serialized Web identity/session file scope
must be obtained for that integration. Writer exclusion/no-env native consumer
remain concrete execution blockers; no decorative proof or waiver was added.

The later evidence/protocol checkpoint changes no tested runtime source but has a
different SHA. These are **c4122b** receipts, not final-head verification or formal
review readiness. Failed Web receipts remain retained and unproven.

## Credential/session fence increment (2026-10-11, not final submission)

Anchors `8d81047663d2` / `797677962dbe` add expand-only000030/Alembic0024
credential revision and session-insert row-lock fence, root/capability revision
binding and client-clock-independent target-only active-session revocation.
Anchors `aa8bdd3b39b0` / `8d17153ae514` integrate the actual production Web
credential read, CAS rehash, verified-revision propagation and mounted `/login`
insertion. No submitted DTO/header revision can replace the verified one. No
stale rehash can overwrite rotation; no stale verifier can obtain a cookie after
the session trigger refuses it. Other accounts' existing sessions are unchanged.

Canonical task scope was expanded before Web edits. The Manager's current
registered task worktree/branch/base and active-worker readback matched this run;
canonical active task scopes showed no overlapping owner on the four Web paths.
All four paths matched origin/dev before editing. Frozen invitation files were
not edited/rebased/cherry-picked. This is isolated source work, not a live lease
or authorization to run a recovery.

Real PostgreSQL tests cover target insertion before/after rotation, a bounded
lock timeout during uncommitted rotation, the ORIGINAL blocked INSERT resuming
and refusing after commit (including legacy NULL revision), successful insertion
only after a rotation/audit rollback, future-skewed client created_at revocation,
and same-transaction timestamp collisions with monotonic non-rewindable revision.
The Python PG fixture also invokes the **actual production TypeScript stores**
against its isolated database through Vitest: old verified revision2 rehash and
insertion refused after rotation3, actual new-password Argon verification,
current-revision insertion, test-owned-session cleanup, and committed CAS rehash4.
The parent asserts the original child exit0 AND final revision4, so this run did
not skip/mock that proof. A Python-only environment without Node tooling skips
this explicitly and cannot claim Web-store PG verification. A standalone Vitest
run intentionally skips its isolated-DB-only case; the Python invocation above
supplies that coverage. No real human password/capability/token is supplied.

Focused diagnostics are allowed incremental selections, **not the final declared
six-file regression suite**. Original receiptstore JSON is retained in receipts/:

| Clean head | Command/selection | Receipt/result | Duration |
| --- | --- | --- | --- |
| 8d810476 | two recovery security/integration pytest files | 10c1b17eb703395d exit1: new fixture lacked production access_token column | 75.330s |
| 797677962 | same two-file pytest; fixture now loads existing000012 | 487f4ae72eaebc70 exit0 | 71.511s |
| 797677962 | declared boundary checker | b42a5e23f8798bb0 exit0 | 10.631s |
| aa8bdd3b | same two-file pytest including actual Web-store PG child | 0ce7ff2dcec7f4bd exit0 | 76.529s |
| aa8bdd3b | pnpm --dir apps/web typecheck | 4f7a8440b2cc4243 exit0 | 36.958s |
| aa8bdd3b | six-file Vitest auth/login selection, exact command in receipt | 23b09bb4bde39b1e exit1: new NextRequest fixture origin caused CSRF403 | 7.151s |
| 8d17153a | identical six-file Vitest selection after fixture correction, no production CSRF weakening | 9860ae66a3263109 exit0 | 5.113s |
| 8d17153a | git diff --check | ab8b85b27da8ca39 exit0 | 0.026s |
| 8d17153a | declared boundary checker | 5320f55bf811e4f7 exit0 | 12.607s |
| 8d17153a | pnpm --dir apps/web lint | 3ca2263ab20e9969 exit0 | 10.124s |

Node tooling used only existing invitation-worktree node_modules, via temporary
local symlinks and declared PATH. Its package-lock SHA256 exactly matched this
checkout (`bb287c16ade4a4a83abe9792499adab7ad8c38607fb6750dbd45f41e89edf9e2`).
No install, lockfile rewrite or host-wide search occurred; symlinks were removed
before closeout. All original statuses/exits were preserved; no summary-based
wait or test-count rerun. New heads/fixture fixes justify these measurements;
failed receipts remain failures. Prior pnpm127 receipts are retained unchanged.

This evidence-only checkpoint has a DIFFERENT head from those measurements;
none is transferred to its SHA. No build/bundle-budget proof is claimed (this
increment changes server-only auth, not client entries); final whole-composition
verification, including the existing300kB budget, remains required before review.
The task remains in_progress and **not review-ready**, without a PR claim. Next:
mounted original-admin authenticated bounded recovery API/BFF/CSRF/no-store and
safe tenant-audit projection; strict versioned recovery bundle/full-gate lineage;
finite trusted foreground parent custody/phase receipts/deadline and single-attempt
transport. Genuine external writer exclusion and native no-env fresh consumer
remain concrete execution blockers, not waived by these source tests. No live
rotation, binding intent/PUT/ACK or new-bundle gate/consumer was performed.

## Mounted API/BFF increment (2026-10-11, not final submission)

Pushed anchors `537520fec7f7`, `9b5dfbdc318a`, `9fc63a673c26` add the existing
composed users-router dev-smoke recovery surface: reserve/issue/consume/quarantine
and inspection, original-admin verified permission/session boundary, bounded
strict duplicate-key-free bodies, static no-store errors including dependency
denials, exact runtime tuple and no outer consume transaction. No main.py edit
or alternate identity provider is needed: origin/dev operator/main readbacks
show the same durable users router already mounted. The old AUTH001 endpoints,
journal, invitation history and old binding NULL are unchanged.

The Web catch-all enforces same-origin CSRF BEFORE upstream/provider/body work,
GET/POST only, no query credential transport and <=8192 JSON bytes through a
bounded tee preserving the original durable cookie reference. Existing proxy
owns durable session/Bearer, service IAM, stripped caller identity, no retries
and manual redirect handling. New audit types have exact safe metadata
allowlists and target-account filtering; corrupt extended credential metadata is
omitted, making lineage unavailable rather than leaking it. Composed OpenAPI and
generated client digest are regenerated; no dangling local DTO references.

Tests use the REAL user-permission/session boundary and isolated PostgreSQL,
not a permission override: mounted same-account rotation, old history/identity
preservation, pure target/original denials before malformed DTO/domain work,
no-store, body/query/runtime/duplicate-key refusal, quarantine and safe projection.
The BFF handler tests plus unchanged production proxy tests cover CSRF/body/
query refusal before upstream, cookie/body preservation and existing IAM/session
forwarding. This is isolated source proof, not live account or deployed proof.

Original receipt JSON is retained including failures (no test-count reruns):

| Clean head | Measurement | Receipt/result | Duration |
| --- | --- | --- | --- |
| 537520fec | two recovery security/integration pytest files | abcd837735927b75 exit1: old identity.account prefix excluded new audit types | 103.559s |
| 9b5dfbdc | identical pytest selection after explicit audit inclusion | 591b8e9a771e240a exit0 | 95.271s |
| 9b5dfbdc | two-file mounted BFF + existing proxy Vitest via npm | 86589ced2d497abc exit1: temporary app dependency link omitted Next | 3.412s |
| 9b5dfbdc | npm Web typecheck | 69b3500b248cabe0 exit2: same missing app-specific Next dependency | 34.594s |
| 9b5dfbdc | identical Vitest after corrected dependency link with explicit retry reason | 29e1009198be684f exit1: happy-dom cookie fixture | 2.708s |
| 9fc63a67 | identical Vitest after cookie fixture/bounded-tee correction | 9c813711d05d6fc3 exit0 | 3.136s |
| 9fc63a67 | npm Web typecheck | 32b48325a8d4dc79 exit0 | 46.652s |
| 9fc63a67 | declared boundary checker | b5980b3046cb1ebb exit0 | 20.902s |
| 9fc63a67 | existing OpenAPI check_drift.py | 6f84533f97a24f7c exit0 | 35.329s |
| 9fc63a67 | git diff --check | 83da322fddffd56b exit0 | 0.030s |
| 9fc63a67 | npm Web lint | 7e9d37217460246e exit0 | 6.545s |

These were run through the existing shared receipt policy in task scratch.
The policy conflates workspace npm typecheck/lint selections; lint's initial
request was refused without running, then its FIRST measurement used an explicit
reason to distinguish the command. No typecheck was rerun solely for counting.
Commands/exits/durations/selections remain in original receipts. Corrected uv
PATH uses its prior documented standard location. pnpm is absent in this run,
so the existing npm workspace scripts provide incremental Web diagnostics,
**not satisfaction of the final declared pnpm command**. Frozen-lock existing
invitation-worktree dependencies were temporarily linked separately at root and
apps/web; root package-lock SHA256 still matches bb287c16...edf9e2. Links removed
before yielding; no install, lockfile rewrite or broad host search occurred.

The evidence checkpoint has a DIFFERENT SHA from measurements. No prior receipt
is transferred to its head; no full declared suite/build/300kB budget or final
verification claim. Task remains in_progress, NOT review-ready/no PR yet. Next:
strict versioned recovery bundle/full-gate proof linking immutable old quarantine
and invitation to new root/issue/rotation/intent/real ACK, then finite trusted
foreground memory custody/deadline/phase receipts/single-attempt transport. Genuine
external writer exclusion/native no-env fresh consumer remain precise execution
blockers. Independent review, normal deployment and fresh measured foreground
preflight remain mandatory. No live rotation, session revocation or PUT/ACK/gate
consumer was performed; original interrupted-session uncertainty is unchanged.

## Strict recovery consumer/gate increment (2026-10-11, not final submission)

Anchors `0d9e8f256bfc` and `017e522a2495` add strict schema2/new-authorization
matched-bundle parsing and exact non-secret nested lineage. The hardened reader
rejects replay of the old execution UUID before network/gate work. Schema1 legacy
behavior remains unchanged; its quarantined root cannot become acknowledged by
adding a new recovery ACK. Malformed/nonempty/duplicate-key/version/partial input
never falls back to original/bootstrap fields.

The canonical full gate carries schema/lineage and refuses partial IDs that
could bypass ACK. Strict consumer validation connects the still-quarantined old
root, absent old binding and actual original invitation provenance to one new
root, same-session capability issue/rotation, distinct intent and201-ACK. Plan
hashes and execution/capability timestamp bounds are checked. Extra/duplicate/
missing events, cross-lineage references, new quarantine,204, reordered phases,
wrong target/tenant/roles/scope or expiry refuse. Safe explicit binding audit
projection is added, but its schema is a **consumer contract only**: no binding
producer/server action, real PUT or foreground executor exists yet. Therefore
this source layer cannot establish a live recovery binding.

Synthetic security fixtures and mocked full-evaluator tests exercise that
contract, not actual creation of a secret or live consumer proof. Full-gate
positive retains genuine invitation/principal, tenant/negative-role, runtime/
transport and own-session cleanup checks, without full/F11 claims. No new test
fixture is represented as a real signed201 runtime receipt. Source SHA verification
against origin/dev confirmed decoder/gate were unchanged from canonical base
before this increment; origin/dev remains e995aa26.

Original shared-policy receipts are retained here. Each measurement ran once at
its clean anchor; the new-head hardening justifies the later focused selection.
No tests were rerun for counting or completion inference.

| Clean head | Measurement | Receipt/result | Duration |
| --- | --- | --- | --- |
| 0d9e8f256bfc | recovery security + canonical dev-admin gate pytest files | ab1cb3c0efd97b79 exit0 | 42.485s |
| 0d9e8f256bfc | exact declared six-file pytest selection | d24971301e98a199 exit0, one displayed skip | 571.020s |
| 017e522a2495 | same focused two-file selection, new old-execution decoder negative | 8005462d59b9e754 exit0 | 31.265s |
| 017e522a2495 | declared boundary checker | 66f4883c3bf0c294 exit0 | 12.428s |
| 017e522a2495 | git diff --check | c253a6c97c52eb2c original command exit0 | 0.018s |

The scratch receipt adapter crashed AFTER saving the diff-check receipt because
empty output has no output_tail key. The adapter terminal exit1 is retained as a
failure of report printing, not mislabeled as its own success; the original
completed git child exit0/duration is explicitly recorded in c253a6c97c52eb2c.
Read that existing receipt, fixed the optional-output access in scratch, did NOT
rerun git merely to manufacture a new receipt. All pytest completions come from
the original foreground tool receipt/exit, never a grep-based wait.

No Web typecheck/lint/build was measured by this consumer-only dispatch; prior
Web/300kB/dependency/pnpm boundaries remain as documented. The declared six-file
run's displayed skip is not passing Web-store proof. New evidence checkpoint
has a DIFFERENT SHA from measured anchors; the six-file pass is NOT a final
whole-composition receipt at the hardening/evidence head. Task remains in_progress,
NOT review-ready/no PR. NEXT: implement same-engine append-only binding producer
with post-rotation original/target/session/source preservation fences and strict
safe projection, actual PG-produced lineage/rollback/uncertainty tests; then finite
trusted foreground memory custody/phase receipts/deadline/single-attempt transport.
Genuine external writer exclusion/native no-env fresh consumer remain precise
effect blockers. Final exact-head declared suite/Web budget, independent Codex2
security review and normal deployment remain mandatory. Old history/binding NULL
and interrupted-original-session uncertainty stay unchanged; no live effects.

## Internal binding producer checkpoint (not mounted, no effects)

Anchor6530bb93b54b adds `RecoveryBindingJournal`: same-engine append-only
post-rotation intent/ACK with exact root/session/source/expiry, original identity/
credential preservation, exact target identity and reserved revision+1, consumed
capability/signed issue/rotation and old quarantine/NULL-binding/invitation fences.
A live fresh local-password target session at the rotated verified revision is
required; account/credential/session SHARE locks fence the append. No PHC/bearer/
capability hash is queried for binding. Inspection is no continuation authority.
Root quarantine invalidates the consumer without rewriting either ledger.

The isolated PG producer roundtrip feeds actual mounted allowlisted tenant audit
and user readbacks into the strict schema2 predicate. This is a synthetic201
declaration, NOT a real external response, PUT or consumer. The internal ledger
is intentionally not mounted: the reviewed trusted foreground response boundary,
finite memory-custody executor, writer exclusion and native fresh-consumer input
remain outstanding. No claim of end-to-end supported executable recovery.

Retained initial failures at clean6530:
- `29480650bb7f87a9`: focused binding pytest command exit127,0.001s because wake
  PATH omitted standard user bin containing the installed uv. No tests ran.
- `a1adc3a51ec70bdb`: explicit same-selection retry with user-bin PATH restored,
  exit1,70.103s. Two rollback test failures: generic injected audit/commit failure
  also broke subsequent mounted readback authentication audit, not just binding.
  Narrowed injection to binding correlation only; no failure erased/relabelled.

The follow-up source also validates ledger plan/ID/timestamps before safe
inspection, rejects boolean issue counts, and adds committed-receipt-loss/new
quarantine and old/new-lineage drift cases. Fixed clean head6b4bd4ac5664
was measured once per selection, from each original tool completion/exit:

| Measurement at6b4bd4ac5664 | Receipt | Exit | Duration |
| --- | --- | --- | --- |
| focused integration `-k binding -q` | c356b44cf4aad311 | 0 | 76.581s |
| exact declared six-file pytest `-q` | 24c5174780f3635d | 0 | 625.882s |
| declared code-boundary checker | 58482b28365e2761 | 0 | 13.344s |
| declared `git diff --check` | bebf296c82fa82cf | 0 | 0.035s |
| declared `pnpm --dir apps/web typecheck` | 39ae3487c2e3511a | 127 | 0.001s |

The full-suite output displays one skip; it is NOT actual Web-store PG proof.
The typecheck did NOT run: `pnpm` absent from PATH. A single bounded diagnostic
found cached `corepack pnpm`9.15.9 (network disabled), but both repository-declared
Node test and Web typecheck binaries are absent in this dependency environment. No host
filesystem scan, dependency/lock rewrite, install or test rerun for counting was
performed. Web typecheck/lint/build and300kB budget remain unverified blockers.

Isolated PG cases include exact safe audit consumer roundtrip, before/after-ACK
preservation/session/source/expiry drift, old-binding/new-lineage drift, no201/
wrong-intent/replay/nested-transaction refusal, two-pool intent/ACK races, audit
and real deferred-commit rollback, and committed reply loss followed by new-root
quarantine. There is still NO real HTTP PUT/response-authenticity proof, mounted
binding mutation action, foreground executor or fresh consumer. Test faults are
scoped to binding correlation so the actual authenticated readback can still run.

Latest origin/dev readback remains e995aa26032542160a177d421ad13157dd6db058.
This evidence-only checkpoint will have a DIFFERENT head from measured6b4bd4;
these are incremental receipts, NOT final exact-head approval/CI or review-ready
source. Task stays in_progress/no PR. NEXT: authenticated bounded binding API
integration (same engine, no caller actor/tenant), then trusted foreground parent
memory custody with absolute<=2400 deadline/allowlisted phase receipts and actual
single-attempt transport classification. ACK integer alone cannot authenticate
GitHub201. Hold all effects until genuine writer exclusion, independently reviewed
native fresh-consumer custody, complete Web/final clean-head verification,
independent Codex2 security review and ordinary deployment/current admission.
Original interrupted-admin cleanup remains UNKNOWN.

## Mounted binding declaration checkpoint (2026-10-11, not final submission)

Clean source anchor `df1a0af3da130fdb1acf9f07fee92dffc0dafd4a` exposes
binding-intent/binding-acknowledged through the existing bounded recovery POST
union and safe binding history through GET. Same persisted original-admin,
tenant, issuer-session/root/source/expiry and post-rotation target-revision fences
remain in the same-engine domain. The existing route-level no-store, strict
8192-byte duplicate-key-free JSON, extra-field refusal and Web CSRF boundary
apply; no new route or Web adapter was introduced. OpenAPI is regenerated and
client freshness checked. Old AUTH001 and old NULL binding remain untouched.

Strict integer201 is an original-admin declaration only. The API does NOT
authenticate a GitHub response; the trusted foreground actual-response boundary
still does not exist. The new mounted roundtrip drives reserve/issue/consume/
intent/ACK through actual HTTP handlers and PG storage, then actual authenticated
safe audit/user projection into the strict schema2 predicate. Its target-session
insertion and201 are explicitly isolated synthetic inputs, NOT deployed Web
login, PUT, writer exclusion, decrypted-value or fresh-consumer evidence.

Mounted cases cover non201/boolean/string/extra authority/credential fields,
different original issuer session, revoked fresh target session, source drift,
quarantine and replay. Committed intent/ACK receipt-loss injection returns static
409, preserves the actual committed ledger, and requires new-root quarantine;
a later GET resolves truth but supplies no retry authority. Existing permission
negative tests now instrument all binding methods too, proving denial before
malformed DTO/domain work without caller actor/tenant shortcuts.

Each command below ran once at the clean source anchor through the shared
verification receipt policy. Original child terminal exit/duration decides
completion; no grep wait, count-only rerun or receipt reassignment was used.
Original JSON is retained in receipts/:

| Measurement at df1a0af3da13 | Receipt | Exit | Duration |
| --- | --- | --- | --- |
| mounted integration pytest `-k mounted -q` | 9d2ef85ed508a211 | 0 | 48.786s |
| declared six-file pytest `-q` | 67ef902d758f69fb | 0 | 659.722s |
| declared code-boundary checker | effe781a74ead3b1 | 0 | 11.790s |
| existing OpenAPI/client drift gate vs origin/dev | 213c368879be0005 | 0 | 22.406s |
| declared `git diff --check` | 75b16a01a43dd137 | 0 | 0.023s |
| declared `pnpm --dir apps/web typecheck` | 0b24986b3d6472af | 127 | 0.001s |

The full-suite displayed skip remains NOT actual Web-store PG verification.
The pnpm command did NOT run because pnpm is absent; no dependency install,
lockfile rewrite or host search was performed. No new Web diff occurred in this
dispatch, but final composed Web typecheck/lint/build/300kB proof remains unmet.
Latest fetched origin/dev is e995aa26032542160a177d421ad13157dd6db058.

This evidence checkpoint has a DIFFERENT SHA; none of the source-anchor receipts
is transferred to it. Task stays in_progress, NOT review-ready/no PR. NEXT:
trusted foreground parent memory-custody executor with absolute<=2400 deadline,
allowlisted pre-effect/terminal receipts and actual single-attempt response
classification. Genuine external-writer exclusion and independently reviewed
native fresh-job custody remain precise execution blockers, not invented
proofs or missing general human consent. Final clean-head verification,
independent Codex2 review, ordinary deployment/current admission and fresh exact
reviewed foreground plan remain required before effects. Original interrupted
admin-session cleanup remains UNKNOWN.

## Foreground primitives checkpoint (2026-10-11, not an executor/submission)

Clean source anchor `4b3c65dd31ad6836d001cebe5a342a0bd5d71d59` adds
`delivery_toolchain/release/recover_dev_smoke.py`: creating-process random-password/
once-attached-capability custody, an exclusive NEW non-secret execution receipt
directory and one absolute<=2400s fail-stop phase budget. The budget caps by plan
expiry and reserves downstream full gate, fresh consumer, cleanup and quarantine
ceilings before phases. Starts and terminals are fsynced; persistence uncertainty,
phase replay/skip, timeout, nonzero terminal exit and missing/non201 PUT status
cannot continue the normal sequence. Best-effort custody release is NOT proven
zeroization. Existing original-account custody is not provided by these primitives.

This increment has **no executable coordinator, HTTP call, secret writer, child
runner/cancellation, source/admission approval service or CLI**. Tests simulate
elapsed time/statuses. Their memory retention across a simulated timeout is not
actual child-handle cancellation proof.201 classification is still not an
authenticated remote response or exclusion/one-PUT proof. The receipt directory
is only a local uncertainty barrier, not external writer exclusion. Actual
source/admission/release/principal/provenance observers and official operations
must still be composed with enforced absolute cancellation BEFORE effects.

Original receipts at this clean anchor are retained in receipts/, including the
failed attempt and its explicit identical-selection PATH correction:

| Measurement at4b3c65dd31ad | Receipt | Exit | Duration |
| --- | --- | --- | --- |
| security recovery pytest `-q`, initial missing uv PATH | 9c5e3afe4f12d093 | 127 | 0.001s |
| identical security selection after evidence-documented PATH correction | 0e1acce9b9be9601 | 0 | 9.237s |
| declared `git diff --check` | 2d3ff7f62ea907f6 | 0 | 0.016s |
| exact declared six-file pytest `-q` | cfbea2662b0c1fca | 0 | 630.391s |
| declared code-boundary checker | a87270a13a00ca55 | 0 | 11.440s |
| declared `pnpm --dir apps/web typecheck` | 41bc97c8ab7fc3d3 | 127 | 0.001s |

The original full-suite receipt displays one skip, NOT actual Web-store PG proof.
No count-only rerun, grep wait or inferred completion was used. pnpm remains
missing: Web typecheck/lint/build/300kB verification is NOT complete or waived.
No host search, dependency installation or frozen-lock rewrite was performed.
Remote dev readback remains e995aa26032542160a177d421ad13157dd6db058.

The evidence checkpoint has a DIFFERENT SHA; source-anchor receipts are not
transferred to that head. Task remains in_progress, not review-ready/no PR.
NEXT: trusted foreground coordinator/official API and actual single-attempt
response adapters with absolute cancellation/original terminal handles, fresh
source/admission/Web+API/account/provenance observations, measured bounds and
cleanup/quarantine. Genuine external-writer exclusion and independently reviewed
native fresh-consumer custody remain precise execution blockers. Independent
Codex2 review, normal deployment/current admission and exact reviewed foreground
plan remain mandatory. Old execution/quarantine/NULL binding and interrupted
original-admin session uncertainty are unchanged. No live effects performed.

## Bounded local checkout observer checkpoint (2026-10-11, NOT full preflight)

Source anchors `3ae1c4811805b57530a2cd383e00c1f44dea9d51` and test correction
`cf6d18d2e8389187872bb78871bfeaa101ae45e2` add an actual NON-SECRET read-only
Git checkout observer and an absolute active-phase deadline accessor. Fixed Git
reads revalidate candidate HEAD, independently pinned ENTIRE reviewed tree,
tracked/untracked/submodule status and unchanged HEAD. No cached pass, raw path/
output receipt, shell, fsmonitor, automatic maintenance, inherited env/stdin/FDs.
This checks LOCAL source consistency only, NOT remote review/latest CI, serving
Web/API identity, normal current admission or execution authority.

The exec adapter caps combined stdout/stderr64KiB, shares one absolute phase
deadline across commands, and reserves2s INSIDE it for group SIGKILL and ORIGINAL
Popen wait. Timeout, output overflow, failed exit or missing completion refuse;
parent exit0 while a descendant holds a pipe does not pass. No log-grep waiting,
PID-name inference, retry or deadline extension. Actual isolated subprocess tests
prove original exit-9 on timeout and parent object retention, env/FD/stdin
exclusion, nonzero exit7, missing wait receipt UNKNOWN, and bounded output/group
cancellation. Finite descendant-marker absence is supplementary, NOT a descendant
terminal receipt. Exec can transiently fork; these tests do NOT prove OS custody
isolation/zeroization or cancellation of unkillable/stalled process creation.
Missing original terminal receipt remains UNKNOWN and blocks continuation.

Initial security run at3ae1 failed on the new test's incorrect indexing of the
existing SimpleNamespace fake clock. The failed receipt is retained unchanged;
only the test was fixed in cf6d. The identical security selection passed at that
NEW clean head (not a relabelled old-head pass). All measured commands use original
terminal exits and durations from the shared verification policy. uv's existing
standard user bin was explicitly included in the verification environment; no
host-wide tool search, installation or frozen-lock change occurred.

| Exact-head measurement | Receipt | Exit | Duration |
| --- | --- | --- | --- |
| security recovery pytest `-q` at3ae1 (clock-test failure) | 5499be9382277c66 | 1 | 22.279s |
| same security selection at clean cf6d | 735ddb6d87674a34 | 0 | 12.143s |
| declared `git diff --check` at clean cf6d | 97c4b4f9683517b1 | 0 | 0.028s |
| declared code-boundary checker at clean cf6d | b78748fc32e341ce | 0 | 12.539s |
| declared `pnpm --dir apps/web typecheck` at clean cf6d (tool missing) | 79222827f2b1b031 | 127 | 0.001s |
| exact declared six-file pytest `-q` at clean cf6d | b422afd6b4bf4927 | 0 | 638.999s |

Original JSON receipts are retained in receipts/. The full-suite displayed skip
is NOT actual Web-store PG proof. pnpm typecheck did NOT run; final Web typecheck/
lint/build/300kB verification remains unmet, not waived. No count-only rerun,
grep-based completion inference or receipt transfer to a new SHA was performed.
Latest fetched origin/dev remains e995aa26032542160a177d421ad13157dd6db058.

This evidence commit has a DIFFERENT SHA. Task remains in_progress, NOT ready for
review/no PR: full trusted foreground coordinator, same-session official HTTP
adapters, current full-tree source/CI/admission/Web+API/account/provenance preflight,
actual response/PUT201 classification and enforced credential-transport deadlines
still need composition. The local observer cannot finish the full preflight phase
alone. Genuine external writer exclusion and reviewed native fresh-job custody
remain concrete effect blockers. Next source increment must compose these fresh
observers/official transport without passing booleans, fallback or automatic resume,
and retain adequate full-gate/consumer/cleanup/quarantine reserves before effects.
Independent Codex2 security review, normal deployment/current admission and an
exact reviewed foreground execution plan remain mandatory. No live effects; old
quarantine/NULL binding and interrupted original-admin session uncertainty unchanged.

## One-use HTTPS primitive checkpoint (2026-10-11, NOT an executor/submission)

Anchors `326f8c1bbfea` / `35c6da37fea0` add a trusted in-process one-use transport
for the FIXED official recovery route. Numeric connection address avoids DNS;
normal public CA chain and origin-hostname verification/SNI stay mandatory. No
proxy/env, redirect, retry, reconnect, child or remote-body logging. The caller's
same original-admin cookie stays in memory; the server revalidates that durable
session on each call. No login/logout/session owner or full coordinator exists.

One absolute deadline covers numeric TCP connect, TLS handshake, entire sendall,
EVERY underlying read and final parsing; dribble cannot refresh timeouts. Aggregate
header/framing/body64KiB, JSON16KiB and request8192bytes are enforced, including
ambiguous/truncated framing, duplicate keys/nonfinite numbers and no-store. A
private repr-hidden/non-pickleable200/201 response is NOT action/lineage validation,
phase completion or execution permission. Once POST send starts, any uncertain
response remains UNKNOWN even if the original HTTP status is available. Instances
are poisoned before validation/network work; later GET cannot reconstruct ACK or
resume. New instances confer no retry authority. No GitHub PUT/actual201 proof.

Real isolated loopback TLS tests use ephemeral offline certificates and synthetic
cookies/JSON, never foreground secrets. They cover exact route/cookie/origin,
certificate-hostname refusal, absent DNS/proxy, real length/chunk completion,
redirect/error/lost reply, truncated framing, aggregate bounds, duplicate keys,
slow-dribble/handshake deadlines, private response redaction and one-use poisoning.
These are adapter regressions, NOT measured production preflight/gate/cleanup
bounds or live custody proof. OS scheduling/stalls are not hard-real-time promises;
late completion refuses rather than granting continuation.

Original shared-policy receipt JSON is retained in receipts/, including failures:

| Exact head | Measurement | Receipt | Exit | Duration |
| --- | --- | --- | --- | --- |
|326f8c1|security pytest, initial uv missing from worker PATH|86bda3ab603f06db|127|0.001s|
|326f8c1|same selection, explicit known-user-bin PATH correction|dd00df7b69a523fd|1|12.445s|
|35c6da37|security pytest after aggregate-header fixture correction|bc4a2dbe5fa7c83a|0|12.358s|
|35c6da37|exact declared six-file pytest|7ad8a24aa72b6ddb|0|633.515s|
|35c6da37|declared diff check|f70e0b495e5a812a|0|0.018s|
|35c6da37|declared boundary checker|e6e65d88c574ba6f|0|10.459s|
|35c6da37|declared pnpm Web typecheck, tool missing|a04e1199074000f6|127|0.001s|
|35c6da37|two-file Python lint, import ordering findings|29240085f34b347f|1|0.470s|
|2e319d15|same lint after import-only correction|439541bd25b8faac|0|0.131s|
|2e319d15|same security pytest, import-only NEW head|2c0c7b06a96b08d1|0|11.955s|

Initial security failure was a fixture expecting65000 bytes to exceed the65536
aggregate cap; fixed to two33000-byte headers. Production cap was not weakened.
The correction also adds strict cookie/config controls, defective/duplicate-type
header refusal and additive CA trust. Import-only follow-up changes no runtime
behavior or selection. Both failed verification receipts remain failures; no
count-only rerun or output/process-name completion polling was used. Each original
terminal status/exit and duration is retained. The full-suite displayed one skip
is NOT actual Web-store PG proof. pnpm typecheck did NOT run; Web lint/build/300kB
and final entire-tree verification remain unmet, not waived. No dependency install,
lockfile rewrite, host-wide search or live credentials were used.

This evidence-only checkpoint has a DIFFERENT head from these measurements;
none is transferred onto its SHA. Task remains in_progress, NOT review-ready/no
PR. NEXT: same-session action-specific official adapters, fresh full-tree remote
review/latest CI/current admission/actual Web+API tuple/account/provenance
coordinator and measured gate/consumer/cleanup/quarantine reserves; actual
single-attempt GitHub201 transport AFTER genuine external writer exclusion.
Native fresh-job memory custody is still a concrete blocker, not imagined value
retrieval or a silent env waiver. Independent Codex2 review, ordinary deployment
and an exact reviewed foreground plan remain mandatory BEFORE effects. Old
quarantine/NULL binding and original interrupted-session uncertainty unchanged.

## Foreground native fresh-consumer source checkpoint (2026-10-11)

Foreground Pi continued real source engineering in the SAME Manager allocation,
not another inventory/credential operation. The scheduling hold superseded the
active source-only worker (original terminal143 retained); its d3405d690 action
anchor and770023573 failure checkpoint were clean/preserved. No credentials or
live effect were interrupted. Canonical owner/expanded file scope and existing
Manager path/branch were verified, task_start validated the current allocation,
and no competing active writer remained before edits. No new worktree/lease authority was created.

Added the real protected-dev manual NO-BUILD/NO-DEPLOY workflow and consumer
entrypoint, strict native matched-bundle ingress/no fallback, cryptographic native
GitHub OIDC run/source proof, canonical full-tree review/CI/current ordinary
admission, memory-only existing WIF/SA token exchange, canonical dev-admin gate,
authenticated ACK-before-run ordering, both login-flow cleanup proofs and minimal
allowlisted consumption report. A passing env flag/mock report cannot attest a
native runner: RS256 is verified against issuer-pinned JWKS; source/ref/environment/
actor/id/run/attempt/hosted-runner and expiry must match. No new IAM/key/resource,
plaintext invoker file/env, gcloud child, secret write, source/provider activation
or rollout was added. Source WIF bindings were observed as public GitHub dev vars
only, not claimed as executed token generation.

This is an explicit NARROW NATIVE CI ENV-TO-MEMORY CUSTODY AMENDMENT PROPOSAL;
independent exact-tree security approval/final operational plan are NOT supplied
by the code. Foreground no-plaintext-env rules remain. The real consumer workflow
has NOT been dispatched; mocked pipelines/ephemeral RSA fixtures are NOT live
GitHub decrypted consumption evidence or F11 acceptance.

Working-tree focused diagnostics reached85 passing source/security/workflow tests,
Ruff passed, diff check passed and the actual boundary generator/check passed1230
files. Initial unused-import/style failures, absent optional PyJWT collection,
and four synthetic harness-index failures were not suppressed; existing frozen
cryptography replaced the absent dependency with fixed standard RS256
cryptography-library verification, NO install/lock rewrite. The harness was corrected to preserve the
explicit sole native read-only GH-token helper exception while proving pair/OIDC/
legacy credentials absent. No live secrets/data were used. These working-tree
results are NOT transferred to the future anchor head: exact-head canonical
receipts must follow the source commit. Complete task-wide final verification,
review, deployment, foreground coordinator/actual201 transport, external writer
exclusion, measured budget and operational execution remain incomplete.

## Bounded encrypted staging increment (2026-10-11; source-only continuation)

This dispatch verified the allocated task branch and clean starting HEAD4c0e1395,
then fetched `origin/dev` (still e995aa26032542160a177d421ad13157dd6db058).
No stale supervisor checkout was treated as source configuration. Only existing
owned recovery source/security/protocol/evidence paths were changed. No branch
sync/cherry-pick, credential access or operational effect was performed.

Anchor `8c7b3bda7119` adds same-session intent/ACK and a pinned bounded GitHub
HTTPS sealed-box staging primitive. Only the retained same pair and exact
root/issue/rotation/intent enter schema2. Preparation and immediately-before-PUT
reads check exact human token owner, repository numeric ID, dev public key and
absence. Durable phase start precedes the ONE encrypted PUT; only an ORIGINAL
fully framed201 plus successful terminal fsync allows ACK. There is no public
status argument, GET-based reconstruction, plaintext bundle child/file/env, retry,
replacement/delete or writer-exclusion boolean. These are primitives, NOT the
full session/preflight/gate coordinator or exclusion authority.

Anchor `158d97d33354a81db59e4393b03c43e76b8c18aa` additionally poisons repeated
preparation (including after a passing first observation), and tests fresh key/
custodian/repository/absence/deadline drift, original201/lost/204/redirect/
truncated TLS responses, sealed same-pair composition, storage failure at intent/
PUT/ACK, ACK-session drift and lost reply, no caller201/intent-only ACK, once-only
quarantine and absolute TLS dribble/handshake deadlines. TLS is ephemeral isolated
loopback; mocked GitHub metadata and fixture sealed-box decryption are explicitly
NOT live custodian/exclusion/consumer evidence. No fake approval was introduced.

Original receipt-policy JSON is preserved in [`receipts/`](receipts/). Commands
ran once per listed head/selection through the existing verification policy;
completion derives from its original subprocess terminal/exit, not output polling.
PATH included the documented `/home/lupin/.local/bin`; the six-file offline
selection used `NODE_ENV=test` as allowed, not a production-guard relaxation.

| Clean measured head | Command/selection | Receipt ID | Exit | Seconds |
| --- | --- | --- | --- | --- |
|8c7b3bda7119|`uv run --frozen --python 3.12 pytest tests/security/test_dev_smoke_recovery.py -q`|9117bfcf83d0bd02|0|14.272|
|8c7b3bda7119|`uv run --frozen --python 3.12 ruff check delivery_toolchain/release/recover_dev_smoke.py tests/security/test_dev_smoke_recovery.py`|82cef6d1ed518ae6|0|0.078|
|8c7b3bda7119|`git diff --check`|a303e8798219d535|0|0.017|
|158d97d33354|declared six-file recovery/invitation/provisioning/full-gate/workflow pytest selection (exact command in JSON)|490612a755a90d4c|0|611.338|
|158d97d33354|same two-file frozen Ruff command|710b27c48844fa70|0|0.094|
|158d97d33354|declared frozen code-boundary check,1230 files|849024af770a42c7|0|10.861|
|158d97d33354|`git diff --check`|849bf533c6bc06ca|0|0.025|

The six-file output has one explicit skip: production Web-store PG proof needs
its frozen Node test environment, absent in this worktree. It is NOT credited as
Web-store proof. The task brief's earlier genuine4c0 foreground Web/PG/typecheck/
native-consumer receipts remain separate and are not reassigned to this new SHA.
No count-only rerun or cosmetic remeasurement of those prior settled selections.
This dispatch does NOT claim final-head Web lint/typecheck/300kB verification or
fresh native consumption. All earlier failed receipts/history remain intact.

This later evidence-only commit changes the Git SHA, not tested runtime source;
the table remains evidence for the two exact anchors, NOT final-head review
readiness. Complete trusted foreground session/source/admission/actual Web+API/
account/provenance reconciliation, measured production reserves, full gate,
bounded cleanup/quarantine and normally authorized fresh-job orchestration remain
NEXT source work under this SAME task. Genuine external writer exclusion is still
unavailable; no local receipt/absence/concurrency lock can substitute for it.
Native env-to-memory ingress is still an UNAPPROVED custody amendment. The worker
must not execute recovery; final composition, independent Codex2 review, ordinary
deployment and the exact foreground operational plan are still mandatory.

## Owned Web sessions increment (2026-10-11; source only, not final submission)

Anchors `31fda6a6b475` / `1b96fd08d019` / `2c9792d9d3c0` add already-approved
foreground original-password memory custody, fixed bounded Web login/session/
principal/logout requests, durable per-login/logout sub-operation receipts and
ownership of only the two fresh sessions created by this instance. One original
login, one same-pair target login, no caller cookies or session-list/revoke sweep.
Actions compose with the retained SAME original cookie once, only after session/
principal validation and durable terminal persistence. This is not full preflight
or permission to reserve/rotate. Target roles remain pureadmin; original roles
are the actual auditor+platform_admin, never restored operations_manager.

A validated login cookie survives a lost/malformed body for cleanup ONLY; an
unrecoverable reply/cookie leaves that fresh session's cleanup UNKNOWN. Any
session/PID/custody/receipt/deadline/role/tenant uncertainty stops progression.
Known own cookies each get at most one logout200 and original-cookie session401;
a failed target logout still attempts only the other known own original session.
No retries, compensating account-wide revocation, serialization, child handoff
or zeroization claim. Caller must orchestrate new-root quarantine BEFORE cleanup.
Historical interrupted original-admin cleanup remains UNKNOWN even on successful
own cleanup. No live original credential/cookie was received by the worker.

Fresh `git fetch origin dev` retained canonical `e995aa26032542160a177d421ad13157dd6db058`;
Web login/session/logout/cookie contracts were read from origin/dev, not inferred
from supervisor/local status snapshots. No branch sync or frozen source edit.
The source passes full declared six-file offline regression and consumer tests
at clean `2c9792d9d3c00acea2f20d393be4030888ee6c1e`. Original receipt-policy JSON
(including initial fixture-import and Ruff failures) is preserved in `receipts/`:

| Clean measured head | Command/selection (exact command in JSON) | Receipt ID | Exit | Seconds |
| --- | --- | --- | --- | --- |
|31fda6a6b475|security recovery pytest, failed new fixture imports|ed705656243b1e5c|1|19.322|
|1b96fd08d019|security recovery pytest, corrected imports|e98a636ca22c6c79|0|17.043|
|1b96fd08d019|two-file Ruff, imports/logout closure binding|88b917e7e8cda6f9|1|0.167|
|2c9792d9d3c0|two-file frozen `python -m ruff check`|33f300ed7a303929|0|0.122|
|2c9792d9d3c0|`git diff --check`|6550e5bd47a005d5|0|0.021|
|2c9792d9d3c0|declared frozen code-boundary check|6c97c3821ec3c235|0|10.753|
|2c9792d9d3c0|declared six-file recovery/invitation/journal/decoder/full-gate/workflow pytest|284692691cfff4e1|0|684.135|
|2c9792d9d3c0|declared consumer security/workflow pytest|fa25d0d43cf0fb2b|0|8.215|

Tests use only offline fixtures and ephemeral loopback TLS, not runtime evidence.
The fixture corrections and closure/import fixes are new meaningful heads, not
reruns of settled same-head selections. Completion is the original tool exit,
never a passed-summary grep/process-name wait. PATH uses documented user-bin;
both full pytest selections used allowed `NODE_ENV=test`. One production Web-store
PG case still skips because the frozen Node environment is absent; NOT credited.
No test-count-only rerun, final-head Web typecheck/lint/budget claim, or reassignment
of previous4c0/158d/8c7 receipts. This subsequent evidence-only commit changes SHA;
these receipts are for the exact listed anchors, not final review readiness.

NEXT: compose actual independently pinned source/latest review-CI/current admitted
release, matching actual Web+API tuple and fresh original/target preservation +
old quarantineNULL/invitation provenance readbacks with this session owner; then
full same-pair gate, quarantine-before-cleanup and normally authorized fresh-job
orchestration. Declare final exact-head regression/Web verification and use
`task_finalize.sh` for real PR submission to Codex2 only after source is complete.
External writer exclusion, measured production reserves, UNAPPROVED native
custody amendment, independent security review, normal deployment and exact
foreground operational plan remain effects blockers. This is incremental source
progress, not a ready/awaiting-review or completed-recovery claim.

## Account/release/provenance observation increment (2026-10-11; not final submission)

Anchors `eb4bc6952849` / `66d8909151a6` / `933a283fd386` / `79304aec8cd1`
compose five fixed bounded Web/BFF GETs with the owned original session: actual
Web+API matching nonempty SHA/profile/digest, complete user and tenant-audit
inventories, old journal and new recovery inspection. Before-state stays only in
parent memory. Reconciliation requires the same owner/plan/actions/custody/budget,
successful same-pair target login, unchanged public selected fields of ALL accounts,
exact old quarantine/NULL binding and invitation provenance, and matching new
root/issuance/rotation. Pending/unknown/quarantined/malformed/duplicate/drifted
lineage stops; GET cannot restore unknown rotation authority. Durable observation
sub-operation starts/terminals contain no raw inventories, email, audit metadata,
cookie or credential. The server's domain/binding separately fences non-public
credential revisions/times; public snapshots alone cannot prove original password
or all pre-existing session preservation. Historical original-session cleanup
remains UNKNOWN.

GETs share each phase's absolute deadline and existing16KiB JSON/64KiB total HTTP
bound, without filtering/pagination/retry. Over-limit complete history is a
concrete pre-effect blocker requiring reviewed bounded inventory support, never
permission to truncate away provenance. Isolated negative tests cover wrong Web
SHA/digest, tenant/roles/status, counts/duplicate IDs, old quarantine/binding,
invitation/bootstrap, pending roots, altered original/target/other-account fields,
wrong rotation/session and unknown execution; loopback TLS tests cover all five
fixed read routes and one-use requests. These are not deployed runtime evidence.

Fresh canonical fetch retained `origin/dev` at `e995aa26032542160a177d421ad13157dd6db058`.
Canonical journal/user/audit response contracts were read from that ref; no branch
sync/cherry-pick or frozen-source edits. Each exact clean-head command ran through
the shared verification policy; original receipts, including failures, are retained:

| Clean measured head | Selection (exact command in JSON) | Receipt | Exit | Seconds |
| --- | --- | --- | --- | --- |
|eb4bc6952849|security recovery pytest; fixture time arithmetic/TLS tuple assertions failed|86ff6869cc55b99d|1|20.144|
|66d8909151a6|security recovery pytest; synthetic capability issue edge overwritten by original issue ID|adf2235542f388cb|1|18.343|
|933a283fd386|security recovery pytest; corrected fixture edge|be313b57abee870b|0|18.909|
|933a283fd386|two-file Ruff; import layout/explicit adjacent zip failed|692812745e2b8825|1|0.191|
|79304aec8cd1|two-file frozen Ruff|7b6cd8364a4df267|0|0.106|
|79304aec8cd1|`git diff --check`|9271922f460f0384|0|0.018|
|79304aec8cd1|declared frozen code-boundary check|ea047e2832c5bc8f|0|10.130|
|79304aec8cd1|declared six-file recovery/PG/API/BFF/invitation/journal/decoder/full-gate/workflow regression|47a7df44a3f7236f|0|605.325|
|79304aec8cd1|declared consumer security/workflow regression|e35c35e4bf845bcc|0|8.399|

Fixture and lint fixes are meaningful new heads, not repeated settled selections.
Two single-shot pure-offline predicate diagnostics identified the fixture edge;
no live reads or test-count-only reruns. Completion is original terminal exit,
not summary grep/process-name polling. The suites used permitted `NODE_ENV=test`
and documented user-bin PATH. One absent frozen-Node Web-store PG skip is NOT
credited. No earlier Web receipts are reassigned, and this evidence commit's
new SHA is not proven by the source-anchor receipts. No final-head Web
lint/typecheck/300kB or final review readiness is claimed.

NEXT: implement bounded independently pinned remote whole-tree review/latest CI
and current ordinary admission observers; compose full same-pair gate,
quarantine-before-own-cleanup and normally authorized fresh-job consumer orchestration
with this account observer/session owner. Complete declared exact-head regression
and actual Web checks, then `task_finalize.sh` actual PR submission to Codex2.
External writer exclusion, measured production bounds, unapproved native custody
amendment, independent review, normal deployment and final operational plan remain
BEFORE-effect blockers. This dispatch delivers source progress only, not an
operational approval or docs-only ready/awaiting-review handoff.

## Bounded remote source increment (2026-10-11; not final submission)

Anchors `143b93735859` / `77db0d9d7e3d` add `TrustedGitHubSourceObserver`:
canonical merged-PR/review-writer/latest reviewed+candidate CI/check predicates,
plus independent ENTIRE reviewed-tree pin matched by BOTH remote commits. Fresh
fixed-path GitHub HTTPS GETs stay in parent memory; no credential-bearing gh/child,
env/proxy, retry, redirect or passing receipt input. Complete64KiB JSON bodies,
duplicate/nonfinite/next-page/compressed/incomplete evidence refusal and ONE
absolute main-thread timer prevent convenient partial/history selection. Any
uncertainty permanently stops the observer, including later repaired readback.
The timer never replaces an existing runtime timer; PID/pickle/close fencing
and static errors avoid token/raw evidence persistence. Source identifiers in
safe output do not grant current admission/custody/exclusion or execution.

Offline tests cover pinned full trees (equal but unpinned trees also refuse),
latest pending/failed gate/CI superseding old success, skipped/wrong-app/future/
duplicate checks, list incompleteness, oversize/malformed evidence, redaction,
real timer/dribble termination, existing timer preservation and no resume.
First security run failed because reusing the source fixture with a40min recovery
plan generated a FUTURE expiry-minus30min evidence time. Only the synthetic expiry
was corrected; production future-time refusal remained intact. Failure is retained.
No live GitHub source/custody/serving/account calls or recovery effects occurred.

Fresh `origin/dev` fetch retained `e995aa26032542160a177d421ad13157dd6db058`.
Canonical source observer and current deployment-history predicates were inspected
at that ref; no branch sync/frozen delivery edits. Each command ran through the
existing shared receipt policy at a clean listed anchor, using documented user-bin
PATH and allowed offline `NODE_ENV=test`. Original terminal exit/duration is
completion proof; no summary/count rerun, output wait loop or receipt reassignment.
Nine original receipt JSONs are retained in `receipts/`:

| Clean measured head | Selection (exact command in JSON) | Receipt | Exit | Seconds |
| --- | --- | --- | --- | --- |
|143b93735859|security recovery pytest; future synthetic source timestamp fixture failed|327e2e704cfe157f|1|19.423|
|143b93735859|two-file frozen Ruff|a0332e3eb0f905bb|0|0.181|
|143b93735859|`git diff --check`|a73c539c96bf59f0|0|0.029|
|77db0d9d7e3d|same security recovery pytest, corrected fixture|c02866b657a5a05c|0|18.784|
|77db0d9d7e3d|two-file frozen Ruff|51d46b443a125cf4|0|0.108|
|77db0d9d7e3d|`git diff --check`|2399f46663257fbb|0|0.017|
|77db0d9d7e3d|declared frozen code-boundary check|ce6af3644178673b|0|10.166|
|77db0d9d7e3d|declared six-file recovery/PG/API/BFF/invitation/journal/decoder/fullgate/workflow regression|781e1a576322d8e6|0|600.443|
|77db0d9d7e3d|declared consumer security/workflow regression|bab9ef0ca18dc51a|0|7.268|

One absent frozen-Node Web-store PG skip remains NOT credited. No previous Web
receipts are assigned to these heads. This later evidence commit changes SHA;
its head is NOT proven by source-anchor receipts, and final-head Web typecheck/
lint/300kB verification or final review readiness is not claimed.

NEXT: bounded current ordinary admission reads reusing canonical newest-changing-
attempt/restoration/complete manifest/admission predicates without credential
children. The existing `automatic_dev` reader uses gh subprocesses and cannot be
silently used as this memory-only adapter; do not substitute cached admission or
replay a consumed lease. Then compose full samepair gate, quarantine BEFORE own
cleanup and normally authorized fresh-job consumer coordination. Complete declared
exact-head regression/Web checks and actual `task_finalize.sh` PR to Codex2 once
source is ready. External writer exclusion, measured production bounds, explicit
UNAPPROVED native-ingress amendment, independent security review, ordinary deployment
and reviewed final execution plan remain BEFORE-effect blockers. This is durable
implementation progress, not ready/awaiting-review or operational approval.

## Current ordinary-admission increment (isolated source only)

Added `TrustedGitHubAdmissionObserver`: parent-memory bounded fixed GitHub GETs,
newest-changing-attempt/rerun selection, actual dev environment proof, genuine
retained ZIP restoration/manifest verification and canonical live-step/manifest
predicates. ZIP downloads use one signed redirect to an independently pinned exact
trusted storage host, with no Authorization/cookie forwarding or second redirect.
No gh/credential child, proxy inheritance, dispatch, lease replay, cached admission
flag or observer-result execution authority. All calls share one absolute timer;
incomplete/oversized/duplicate-key/expired/ambiguous evidence stops permanently.
Environment URL proof additionally refuses other-host and run-ID-prefix confusion.

Offline tests added for fresh rereads, failed-after-commit/unknown/newer-rerun
refusal, restoration-only predecessor selection, manifest and ZIP integrity,
redirect credential isolation, pagination/completeness, redaction and real timer
expiry. At the source anchor these are **unmeasured**; receipts will identify the
actual clean tested SHA, not transfer earlier77db/af4 proofs. Fresh origin/dev
readback remains e995aa26032542160a177d421ad13157dd6db058; canonical admission
source was read at that ref. No ordinary release/CI/cloud/account calls occurred.

NEXT remains full samepair canonical gate and normally authorized fresh-job
consumer coordination, quarantine BEFORE own cleanup, exact-head declared/Web
verification then actual task_finalize PR to Codex2. This is not final review
ready. External writer exclusion, production measured reserves, explicit native
custody amendment review, normal deployment and final reviewed plan still block
all effects. Old quarantine/NULL binding and original incident-session UNKNOWN
are unchanged.

## Admission increment: original measured receipts (2026-10-11)

Anchored source `fe233b518a67` exposed a genuine custody boundary issue: canonical
manifest validation internally starts Git with its default environment. The test
that denied credential-bearing children failed; it was NOT waived. Source fix
`45d40a37d88bc5b275bab6019c2ae8c0c4ced080` isolates only NON-SECRET manifest and
tuple input in a fixed bounded child, with sanitized env/no inherited FDs, whole-
group cancellation/reaping and candidate-object absence refusal before fetch.
The regression now inspects that actual child boundary, not a mocked validator.
Initial Ruff errors were corrected; original failed receipts remain below.

All commands used the shared verification policy on clean exact anchors,
standard user-bin uv PATH and allowed offline `NODE_ENV=test`. Original terminal
exit/duration decides completion; no summary counting rerun. Ten receipt JSONs
are retained without changing their measured SHAs:

| Clean measured head | Selection (exact command in JSON) | Receipt | Exit | Seconds |
| --- | --- | --- | --- | --- |
|fe233b518a67|security recovery pytest; canonical internal-Git custody regression|21a911e77e079e70|1|25.184|
|fe233b518a67|two-file frozen Ruff; import/shadowing findings|f981909911290d70|1|0.083|
|fe233b518a67|`git diff --check`|3f692c0a99f5416a|0|0.019|
|45d40a37d88b|same security recovery selection; corrected boundary|20c59df81379d2d3|0|24.579|
|45d40a37d88b|two-file frozen Ruff|c840bf3d25ee5726|0|0.074|
|45d40a37d88b|`git diff --check`|fef283a7dcad7e25|0|0.017|
|45d40a37d88b|declared frozen code-boundary check|8b1f1ec040c76f57|0|10.168|
|45d40a37d88b|declared six-file PG/API/BFF/invitation/journal/decoder/fullgate/workflow selection|f0bd02a674d64aac|0|614.074|
|45d40a37d88b|existing canonical automatic-dev deployment regressions|22e6c986ece8a6d9|0|37.767|
|45d40a37d88b|declared consumer security/workflow selection|0da8d8a9e2053361|0|7.939|

The six-file selection has one absent frozen-Node Web-store PG skip, NOT credited
as Web proof. No prior Web/head receipts were reassigned. This subsequent evidence
commit has a different SHA and does not inherit the source anchor's verification;
final exact-head declared/Web typecheck/lint/300kB verification remains due.

Source/admission observers are now concrete bounded adapters, not admitted flags.
NEXT: complete the actual foreground coordinator with canonical full samepair gate,
quarantine BEFORE own cleanup and the separately normally authorized fresh-job
consumer boundary; then final exact-head declared/Web checks and task_finalize PR
to Codex2. Genuine writer exclusion, measured production reserves, independently
reviewed native custody amendment, ordinary deployment and exact final execution
plan remain before-effects blockers. No operational permission or final review
readiness is claimed by this incremental checkpoint.

## Canonical same-pair gate/failure coordinator source increment (unmeasured)

Added `ForegroundSamePairGate` and numeric TLS canonical HTTP adapter. The actual
canonical evaluator (not supplied booleans/reports) receives the same memory pair
and strict schema2 lineage only after real201/durable ACK in the same actions
object. Its optional additive cleanup barrier stops on prior failed checks before
normal/HTML own logout. Failures quarantine with the retained original cookie
BEFORE remaining known-own cleanup under the single reserved cleanup budget.
Lost login/logout/quarantine stays UNKNOWN; no retry/other-session revocation.
Success leaves fresh_consumer pending, decrypted binding/full acceptance false.

New offline tests exercise the actual canonical evaluator over synthetic HTTP and
ACK/session fixtures; those fixtures are not serving/account/consumer authority.
At source anchor these checks are unmeasured. Failed or successful later receipts
will retain their original SHA/selection/exit; earlier45d proofs are not inherited.
Bootstrap/initial credentials and missing application bearer fail prevalidation.
A concrete native consumer finding was sent through the live canonical task note:
its empty application bearer cannot satisfy unchanged canonical validation. That
four-file foreground-owned lane is not edited here.

Increment remains NOT final review ready: full source/admission-to-fresh-job
coordination and final exact-head declared/Web checks are still due. Writer
exclusion, measured production bounds, independently reviewed native custody
amendment, exact final plan and normal deployment remain before-effects blockers.
No live effect or external credential material was used.

## Gate increment: original terminal receipts (2026-10-11)

Source anchors culminate at `d4f440de92065a30abe785ed226fe4cd9a3f7377`.
The independent foreground native commits `e06dd1fbe`/`e46abad3e` are already
ancestors of the gate anchor; no cherry-pick, branch reset or frozen-source edit.
Fresh fetched origin/dev still reads `e995aa26032542160a177d421ad13157dd6db058`.
All22 original receipt JSONs below are retained in receipts/ without changing
measured SHA/command/status/duration. No grep waiting/count-only rerun occurred.

Initial positive synthetic packet failed strict expiry and capability-reference
checks: the historical packet clock and the same-named capability issue_event_id
were corrected, not the production predicates. Original three pytest failures
and Ruff failure remain failures. Additive canonical cleanup barrier preserves
all default callers and can only stop, never waive checks. Final gate27 tests
include the actual complete canonical evaluator, real isolated numeric TLS,
duplicate/truncated bodies, dribble timeout, redirect nonforwarding, insecure
cookie retention for cleanup only, receipt loss, actual parent timer and separate
finite cleanup I/O reserves after gate quota exhaustion.

| Exact clean measured head | Selection (exact command in JSON) | Receipt | Exit | Seconds |
| --- | --- | --- | --- | --- |
|63f58ef3a951|new gate tests, initial historical packet failure|f867aebb4041c7a2|1|11.882|
|9d65dae9c020|new gate tests, capability lifecycle-reference failure|30066a7f0b3d9374|1|9.101|
|c830c26e25f4|same gate tests, safe predicate diagnostic|efe086e8c4cc0db9|1|9.243|
|3584e01f9239|new gate17 tests with corrected packet|d4b1434d66f78d8a|0|8.653|
|3584e01f9239|three-file Ruff, import/fixture findings retained|707828171d53af7c|1|0.126|
|3584e01f9239|existing recovery security + canonical dev-admin regressions|8e98ed5162fa8b6f|0|45.180|
|f3db5fd03ae4|expanded gate26 tests, actual TLS/timer/receipt coverage|0a3449ff57edee32|0|14.450|
|f3db5fd03ae4|three-file Ruff|14b2f20ab3a57695|0|0.486|
|f3db5fd03ae4|diff check|c3f74d76f22a099e|0|0.037|
|f3db5fd03ae4|declared boundary check|1812858c2c595be5|0|12.033|
|f3db5fd03ae4|existing recovery/canonical gate regressions|6b557e43a53a4141|0|48.990|
|d4f440de9206|gate27 tests including exhausted gate/separate cleanup quota|ccea4ee50be0f7a9|0|9.805|
|d4f440de9206|three-file Ruff|00ad98449bcd880a|0|0.355|
|d4f440de9206|diff check|e52b1191cddea837|0|0.082|
|d4f440de9206|declared boundary check|001a84c2877e6e35|0|11.656|
|d4f440de9206|declared six-file PG/API/BFF/invitation/journal/decoder/fullgate/workflow regression|b3297adfc7f2a120|0|623.054|
|d4f440de9206|declared Web typecheck, initial incomplete tool PATH|6a3f6e0a82440ccf|1|0.694|
|d4f440de9206|same declared Web typecheck, restored frozen graph + PATH|7f56cc543e593f4f|0|32.482|
|d4f440de9206|actual production Web-store PG fence case, NOskip|562d4dcc96afd52a|0|20.747|
|d4f440de9206|declared native consumer security/workflow102 offline regressions|17ae9f45c23cfa93|0|13.496|
|d4f440de9206|seven actual Web auth/login/BFF Vitest files|e009bde03b09e844|0|4.567|
|d4f440de9206|actual Web lint|5677e57287ec2272|0|5.423|

The six-file selection's one missing-Node skip is NOT credited. Afterwards the
actual declared Web typecheck, production TypeScript/PG-store child case and
seven-file mounted Web/auth/CSRF tests ran against the BYTE-COPIED existing frozen
Node graph, not a new install or an assumed skip. Donor path is the existing
`/tmp/pantheon-worker-worktrees/pantheon/odp-identity-account-invitation-001`;
root, Web and available package node_modules were copied privately so relative
workspace links resolve THIS candidate's packages, not donor source. Root lock
SHA256 matches donor AND origin/dev exactly:
`bb287c16ade4a4a83abe9792499adab7ad8c38607fb6750dbd45f41e89edf9e2`.
Node22.23.2 and cached real Corepack pnpm9.15.9 ran with network disabled and
explicit local .bin PATH. No install/lock rewrite/module upgrade. Temporary graph
copies are disposable and removed before incremental closeout. Web Vitest's
isolated-DB-only skipped case is covered by the separate actual PG-store receipt,
not reclassified. Actual lint has an explicit policy retry reason because pnpm
lint/typecheck share the policy's apps/web selection fingerprint; it is a distinct
command, not a count-only repeated typecheck.

The current native consume source at foreground head e46 still constructs an
EMPTY application bearer. Its102 mocked/offline tests do NOT establish an actual
full canonical native gate: unchanged `config:operator_credential` requires the
separate normally authorized application credential. This precise gap was sent
through canonical task notes; the worker did not edit the four foreground-owned
native files or weaken the guard. Native custody/credential ingress remains a
reviewed before-effects blocker, not a license to manufacture an app token.

This evidence commit is NOT the measured source head and does not inherit its
exact-head receipts. NEXT: compose actual fresh source/admission rechecks,
preflight-to-gate/fresh-job coordinator with genuine effect fences, resolve native
application-credential/custody viability in the assigned foreground lane; declare
the new gate test file as part of final verification; then final exact-head
full/Web/300kB verification and task_finalize actualPR to Codex2. No final review
readiness is claimed. External writer exclusion, measured production reserves,
final reviewed plan, independent review and normal deployment still block effects.
Old89 quarantine/NULL binding and interrupted original17 cleanup UNKNOWN remain.

## Concrete preflight-to-HOLD coordinator checkpoint (2026-10-11)

Anchors `ff5711a82226` / `3da88578fb342cbc24b7e6997d959dc560c546fa`
compose real typed checkout/source/current ordinary-admission/account/release/
old-quarantine/invitation/absent-bundle observers and owned original-session
cleanup. The real canonical gate config is prevalidated BEFORE login; no arbitrary
callbacks, caller baseline/pass receipt or exclusion/approval boolean is accepted.
Source/admission are actually reread after account/metadata observations; even a
same-SHA replacement run/attempt stops. Per-observer starts/terminals share the
same absolute preflight deadline. The constructor checks that admission's fixed
canonical validator uses the same independently verified candidate checkout.

This is a diagnostic **preflight-to-HOLD**, not the missing effect/fresh-job
coordinator. It unconditionally stops before reserve/issue/rotate/intent/PUT/ACK/
gate/dispatch because genuine external writer exclusion is unavailable. It cleans
only its known fresh original cookie once; there is NO new root to quarantine.
Lost login/logout/receipt remains UNKNOWN, and the historical original-admin
incident session cleanup remains UNKNOWN. Owned custody and observers are closed;
no retry/readback/resume or caller switch can turn observation into effects.
Native application-credential implementation, approval of its proposed custody
boundary, measured production bounds and final operational-plan review are
explicit additional blockers. No foreground-exclusive native files were edited.

Offline composition fixtures use synthetic GitHub/Web records, real canonical
source/admission history selection, strict account/provenance validation, durable
phase receipts and owned session cleanup. Local checkout/manifest-child transport
is explicitly mocked ONLY in these composition cases; its actual bounded original
child handles/canonical predicates have separate existing regressions. Fixtures
are NOT live admission, writer exclusion, consent or decrypted-value proof.

The initial gate/preflight test failure at `ff5711` changed a synthetic live STEP
but incorrectly left the job successful. Fixed the JOB and original live-step
conclusions, not the canonical successful-job predicate. Added simulated absolute
phase expiry; simulation is not production timing measurement. The failed receipt
remains retained unchanged. A commit-message length rejection was corrected before
creating the follow-up anchor; no failed test was relabelled successful.

All11 original shared-policy receipt JSONs are retained in receipts/. Original
terminal exit/duration is completion proof; no grep wait, count-only rerun, late
head reassignment or fabricated summary. Each command ran at the clean listed
head with documented user-bin PATH and permitted offline NODE_ENV=test:

| Clean measured head | Selection (exact command in JSON) | Receipt | Exit | Seconds |
| --- | --- | --- | --- | --- |
|ff5711a82226|gate/preflight tests, inconsistent synthetic job failure|754df5f912306bab|1|16.746|
|ff5711a82226|two-file frozen Ruff|bd7225bd8c5f7c4b|0|0.553|
|3da88578fb34|gate/preflight47 cases|ff920a616627e142|0|21.029|
|3da88578fb34|two-file frozen Ruff|99fae964e3998a6b|0|0.438|
|3da88578fb34|declared diff check|f9abcfe8761ed9ab|0|0.043|
|3da88578fb34|existing security recovery/canonical dev-admin gate regression|f638f838c312d0b1|0|47.084|
|3da88578fb34|declared boundary check|cea3b71bbf72d611|0|11.564|
|3da88578fb34|declared six-file PG/API/BFF/invitation/journal/decoder/fullgate/workflow regression|4db8c92c6124efc4|0|681.026|
|3da88578fb34|declared Web typecheck|b88f09c49eaf01be|0|10.759|
|3da88578fb34|declared native consumer102 OFFLINE cases|133cc5e62675e7ce|0|11.852|
|3da88578fb34|actual Web lint|16aa07f722bc32e2|0|6.605|

The full declared six-file selection has NO displayed skip: the actual production
Web-store PG child was available WITH the existing frozen Node graph, not rerun
merely for counting. BYTE-COPIED donor root/Web dependencies came only from the
already declared invitation worktree; workspace relative links therefore resolve
THIS candidate source. Root package-lock SHA256 matches donor AND origin/dev:
`bb287c16ade4a4a83abe9792499adab7ad8c38607fb6750dbd45f41e89edf9e2`.
Node22.23.2 / existing Corepack pnpm9.15.9 ran with network disabled and explicit
root/Web .bin PATH; a scratch pnpm shim only execs the existing Corepack binary.
Missing optional .bin diagnostic paths returned exit2 during setup, not a test
pass or dependency installation. No frozen-lock rewrite, install, host-wide scan
or package upgrade. Temporary graph copies were removed before handoff. Web lint
has an explicit reason distinguishing its command from the policy's shared
apps/web typecheck selection fingerprint; typecheck was not cosmetically rerun.

Latest remote dev remains e995aa26032542160a177d421ad13157dd6db058. No branch
sync/cherry-pick/frozen delivery edit occurred. This subsequent evidence commit
has a different SHA and does not inherit these source-anchor receipts. Final
exact-head entire-composition/Web300kB verification and PR submission remain due.
The existing gate/preflight test command was explicitly declared through a live
canonical task note; canonical verification-list addition was requested before
final review. Native e46's empty application bearer still blocks the real canonical
native gate; its102 offline passes are not actual consumption/full-gate authority.

NEXT: resolve native application-credential/custody viability in the assigned
foreground-exclusive lane and compose the separately normally authorized actual
fresh-job boundary. Genuine external writer exclusion must be independently
established; this diagnostic cannot manufacture it or start effects without it.
Final reviewed operational plan, production measured reserves, independent Codex2
security review and ordinary deployment/current admission remain before-effects
gates. After complete source composition, final exact-head declared/full/Web
budget receipts and task_finalize real PR are mandatory. This is meaningful
pushed source progress, NOT a ready/awaiting-review or completed-recovery claim.

## Native application identity source checkpoint (2026-10-11)

Anchors `925c2174ac59`, `1f4625cd64f6`, `7f111d5079cf` resolve the
previous empty native application bearer WITHOUT relaxing canonical config.
Verified current `origin/dev` e995aa26 `deploy_cloud_run_waji.sh` uses the existing
`ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT` and first `ODP_AUTH_AUDIENCES` audience for
application ID-token minting, separately from Cloud Run transport. The native
workflow now projects these SAME non-secret protected-dev variables. Existing
memory-only WIF/STS/IAM code generates application and transport tokens separately,
supplies the actual API client/config, and validates ALL canonical config checks
before evaluator/login. No account, principal map, permission or IAM grant changes.

A same-project non-deployer smoke SA and fixed API first audience are mandatory;
missing/drifted config refuses before federation. Negative mocked transport covers
denied/empty/header-injected/oversized application responses and no retry/fallback.
Exact current values, existing impersonation and least-privilege application RBAC
MUST still be reconciled under the final independently reviewed operational plan;
source syntax and mock tokens do not prove those grants. Worker read NO live
runtime variables/grants and minted NO credential. Native launch custody remains
an explicitly unapproved boundary amendment.

The new offline composition case invokes the REAL full canonical evaluator with
synthetic HTTP/lineage and mocked authority/identity observers. Original first
failure used separately generated synthetic bundle and audit UUIDs; canonical
`admin:credential_bundle_durable_acknowledgement` correctly refused. Added failed
check-name diagnostics, then reused ONE packet's matching UUIDs; the canonical
ACK predicate is unchanged. Both original failures remain immutable receipts.
This passes the former empty-bearer source gap, not actual fresh-job consumption.

All six original shared-policy receipts are retained byte-for-byte in receipts/:

| Clean measured head | Command/selection (exact JSON retained) | Receipt | Exit | Seconds |
| --- | --- | --- | --- | --- |
|925c2174ac59|declared two-file native selection, mismatched synthetic packet|6889879e3e285e21|1|18.706|
|1f4625cd64f6|same selection, failed-check diagnostics|a3b5dda227392bdb|1|17.751|
|7f111d5079cf|declared native security/workflow selection, REAL offline evaluator|9ad22ac6d01ffc19|0|17.779|
|7f111d5079cf|three-file frozen Ruff|aac6c0c94c1f4240|0|0.167|
|7f111d5079cf|declared boundary check|8b08cbc3e4073ca0|0|10.407|
|7f111d5079cf|declared diff check|5c51d6453f6c5b79|0|0.021|

Commands used user-bin PATH and permitted offline NODE_ENV=test, frozen Python3.12.
The scratch receipt printer raised KeyError AFTER saving the diff command's real
exit0 receipt because empty output omits output_tail; the wrapper terminal was
exit1, not misrepresented as a passing wrapper. Read the existing original receipt
for command completion; no count-only or cosmetic rerun was made. No stdout grep
wait or head reassignment. This evidence commit does not inherit source receipts.

NEXT: final composition remains due: canonical addition of gate/preflight test
selection, exact FINAL head entire declared PG/API/BFF/invitation/journal/decoder/
fullgate/workflow and actual Web typecheck/lint/300kB receipts, then mandatory
`task_finalize.sh` remote PR to Codex2. The diagnostic still unconditionally HOLDs;
no effect/fresh-job dispatch coordinator was added. Genuine external writer
exclusion, independent native-custody review, final exact operational plan and
measured production reserves remain BEFORE-effects blockers. No final review
readiness, actual consumption or completed recovery is claimed by this increment.

## Whole-composition verification checkpoint at 27f689f777b6

The first submission measurement at clean `27f689f777b6` passed all five
canonical commands and the declared supplemental gate/admission, seven-file
Web, lint and OpenAPI checks. The actual PG/Web-store child ran (full pytest
output has no displayed skip); the separate standalone Web selection's one
isolated-DB skip is not credited independently. No final build/budget was run
at this head. Original receipt JSONs are retained unchanged in `receipts/`:

| Command/selection | Receipt | Exit | Seconds |
| --- | --- | --- | --- |
| canonical diff | 63564288122d1c71 | 0 | 0.018 |
| canonical six-file PG/API/invitation/journal/fullgate/workflow | 57fa63ebb9135279 | 0 | 679.450 |
| canonical boundary | a9969f9eee561a97 | 0 | 15.233 |
| canonical Web typecheck | e79bf2c1b8ea1cc4 | 0 | 10.600 |
| canonical native consumer/workflow | 36ef53639c9050a8 | 0 | 20.678 |
| supplemental gate/preflight + automatic-admission | e77b2b5473437cd8 | 0 | 40.330 |
| actual seven-file Web/auth/BFF | 00854f763148a32f | 0 | 8.199 |
| actual Web lint | 12e06af9b4e5dc8c | 0 | 5.984 |
| OpenAPI/client additive-only drift | 009a03da54fd8a87 | 0 | 22.434 |
| all changed-Python Ruff (FAILED9 findings) | 36f2a5740854017b | 1 | 0.241 |
| SAME Ruff selection concise diagnostic | 0cd4e7b1f3f1c559 | 1 | 0.125 |

The original Ruff output tail truncated earlier actionable findings; the
explicitly declared/reasoned concise diagnostic obtained their locations, not
a pass or count-only remeasurement. A separate Ruff `--diff` diagnostic proposed
seven import-order corrections without modifying files (exit1). Targeted edits
apply those corrections, explicitly mark adjacent timestamp zip `strict=False`,
and use pytest's `request.getfixturevalue` for the imported identity fixture
instead of shadowing its binding. No config suppressions, permission/gate
relaxation, dependency upgrades or runtime-effect changes were made.

The corrected source is a NEW head. These receipts are not transferred to it;
all canonical and supplemental final checks must run at that clean anchor,
including production build/unchanged300kB budget. Final-head receipts remain in
the shared ignored receiptstore and the canonical task note to avoid a post-test
commit invalidating exact-head proof. Review submission is only through the
actual `task_finalize.sh` PR/atomic-status flow, not a fabricated handoff.

## Submitted04a972f7 CI repair (2026-10-11, foreground Pi)

PR1459's genuine CI38139616828 failed two independent source gates:
product-lint-unit's xdist workers collected different node IDs because four
negative lineage parameters minted random UUIDs during import, with one more
in the new gate test. Product-security found five Bandit B608 SQL construction
sites in RecoveryCapabilityService/RecoveryBindingJournal. The aggregate product
failure correctly reflected those failures. Neither is treated as transient,
approved source or reason to replay the same head.

Foreground same-owner Pi acquired the existing Manager allocation after verifying
no active recovery writer and placing a canonical temporary source scheduling
hold. task_start validated the existing branch/worktree; canonical scope added
the already tracked gate test plus actual xdist and SAST regression selections.
No archived head/history, consent, actor/provider, dependency, scan policy or
production gate was rewritten.

Fixed query identifiers are now literal schema-qualified table names. Every
value remains bound via existing parameterized calls; the subclass-overridable
table identifier/f-string surface is removed, NOT hidden behind nosec/baselines.
Negative tests retain random wrong UUID values but declare stable descriptive
IDs. The native consumer's collection-time NumericDate parameters likewise have
stable IDs to prevent the same intermittent xdist issue. No single-process-only
workaround, xdist disable, reduced test selection or weakened negative assertion
is introduced. Original submitted04a and CI failures remain evidence.

Focused changed-Python Ruff/diff diagnostics pass in the working tree. These are
not transferred to the new repair anchor. All seven updated canonical commands,
including actual two-worker negative/gate/native tests and unchanged real SAST,
MUST produce final exact-head receipts before legitimate resubmission. The
frozen Node graph is temporarily reused only after byte-identical package-lock,
root/Web package definitions were checked; no install/lock edit. Production
Web/PostgreSQL store test must execute, not be credited from a missing-Node skip.

This is source CI repair, NOT live recovery/native consumption or independent
security/custody approval. The external-writer boundary and operational plan
remain unmet; the composed preflight still unconditionally HOLDs before effects.

## Full-CI migration composition repair (2026-10-11, foreground Pi)

Genuine ba0328 CI38142383164 completed FAILURE13:40:55: eight component jobs
passed, including security/node/db/contract/e2e. Actual full xdist product tests
finished1failed/8422passed/23skipped/16subtests in1465.73s. The only failure was
the migration-plan contract's exact revision list still ending0022, despite
this source's required0023 and0024; both prior SAST/collection defects passed.
The earlier900s watch timeout is still not this terminal CI failure.

Same legitimate owner used a new canonical source hold/declared file scope;
no concurrent recovery writer, task_start validated existing allocation.
Repair preserves the EXACT revision-list assertion and adds0023/0024 plus real
normal migration-plan SQL-asset/hash reachability for both, actual parent chain
0022->0023->0024, and invokes the actual downgrade with mocked SQL execution to
require ONLY SELECT1 preservation. No migration asset, downgrade implementation,
manifest policy, data/DDL, runtime role or old history is weakened or executed.
Original ba0328 failure remains. The updated mandatory two-worker migration
contract plus all seven previous checks must run at the NEW clean repair head
before resubmission; no previous source/CI receipt is transferred.

## Live effect status

No live account/credential/session effect, live database/cloud access, secret
intent/PUT/ACK, new bundle gate or fresh-job consumer was executed. Old execution remains
quarantined/binding NULL. Original interrupted-session cleanup remains UNKNOWN.
Implementation/source CI/independent security review/normal deployment and fresh
reviewed foreground execution preflight remain separate unmet prerequisites.
