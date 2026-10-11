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

## Live effect status

No account/credential/session effect, database/cloud access, secret intent/PUT/ACK,
new bundle gate or fresh-job consumer was executed. Old execution remains
quarantined/binding NULL. Original interrupted-session cleanup remains UNKNOWN.
Implementation/source CI/independent security review/normal deployment and fresh
reviewed foreground execution preflight remain separate unmet prerequisites.
