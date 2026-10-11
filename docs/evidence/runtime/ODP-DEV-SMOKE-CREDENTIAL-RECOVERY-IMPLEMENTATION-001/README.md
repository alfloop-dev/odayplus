# Recovery implementation evidence — internal root/capability increments

Owner Pi · independent reviewer Codex2 · task in progress.

Base verified by `git fetch origin dev` and equal HEAD/origin-dev
`e995aa26032542160a177d421ad13157dd6db058` on the allocated task branch.
No frozen plan/invitation delivery was edited or cherry-picked.

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

## Live effect status

No live account/credential/session effect, live database/cloud access, secret
intent/PUT/ACK, new bundle gate or fresh-job consumer was executed. Old execution remains
quarantined/binding NULL. Original interrupted-session cleanup remains UNKNOWN.
Implementation/source CI/independent security review/normal deployment and fresh
reviewed foreground execution preflight remain separate unmet prerequisites.
