# Recovery implementation evidence — incremental root only

Owner Pi · independent reviewer Codex2 · task in progress.

Base verified by `git fetch origin dev` and equal HEAD/origin-dev
`e995aa26032542160a177d421ad13157dd6db058` on the allocated task branch.
No frozen plan/invitation delivery was edited or cherry-picked.

## Scope

- New internal shared recovery plan/root and PostgreSQL tests, not a mounted API.
- Tests cover strict scope/expiry/redaction, durable original-session checks on
  every operation, original/target identity and old NULL-binding preservation,
  target/lineage drift, one-way quarantine, audit rollback and reservation races
  across independent PostgreSQL pools. They do NOT claim capability consumption,
  Argon2 rotation/session-revocation atomicity, transport/consumer or live proof.
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
files; boundary verification must be remeasured at the clean new anchor. Web
verification remains explicitly unproven, not waived because this increment
has no Web diff. The whole task is NOT ready for formal review/finalization.

## Live effect status

No account/credential/session effect, database/cloud access, secret intent/PUT/ACK,
new bundle gate or fresh-job consumer was executed. Old execution remains
quarantined/binding NULL. Original interrupted-session cleanup remains UNKNOWN.
Implementation/source CI/independent security review/normal deployment and fresh
reviewed foreground execution preflight remain separate unmet prerequisites.
