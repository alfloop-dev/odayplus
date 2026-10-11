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

## Live effect status

No account/credential/session effect, database/cloud access, secret intent/PUT/ACK,
new bundle gate or fresh-job consumer was executed. Old execution remains
quarantined/binding NULL. Original interrupted-session cleanup remains UNKNOWN.
Implementation/source CI/independent security review/normal deployment and fresh
reviewed foreground execution preflight remain separate unmet prerequisites.
