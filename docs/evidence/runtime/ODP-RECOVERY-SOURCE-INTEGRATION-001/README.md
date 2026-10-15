# ODP-RECOVERY-SOURCE-INTEGRATION-001 — source-only composition record

Source-only successor authorized by the user event recorded in
`support/handoffs/recovery-response-boundary-repair-20261012/source-integration-authorized-20261015/`
(event `22352527`, exact text「授權接受」). That delegation is NOT a Human/Ops
signature, security acceptance, merge permission or operational GO.

Canonical reviewer: **Human/Ops**. Final admission is **PENDING**. This record
grants no approval, merge arming, merge, promotion, deployment, recovery or
credential/account/session effect.

## Pi continuation — run pi-20261015T042723Z-64b2f81a (NOT review-ready)

Current admitted `origin/dev` was fetched and remains `c0c8c8226acaab3cbe8bb76e79f01e5e6694abb3`,
already an ancestor of this task. Manager allocated the expected task branch to Pi;
Human/Ops remains final reviewer. No additional source path was leased or edited.

- Anchor **8010e6570c806b1a99fb38e67683b04271be7911** installs the existing,
  authoritative `000008_postgresql_runtime_persistence.sql` before 0025's outbox
  FK, in the same transaction, restoring the caller's search_path. The migration
  manifest now hashes BOTH executed SQL files. FK/state constraints and
  no-delivery-backfill semantics are unchanged. This adopts the inspected prior
  Antigravity DB helper's prerequisite strategy, not its patch wholesale.
  Real throwaway-PG regressions live in the ALREADY allocated migration-backfill
  module: fresh/legacy upgrades, unchanged historical hash/no publication intent,
  missing-event FK refusal, rerun/runtime idempotence, incompatible-target refusal
  and atomic rollback/version preservation. They isolate0024->0025; they do NOT
  claim full fresh production-chain coverage. The existing unmodified official
  real-estate PG module (stamped baseline) also exits0 at8010.
- Anchor **a83c6d674342e1bbcea5b98288649db498e3cd5b** attributes future accepted
  enqueue/replay/retry audit events to the verified server tenant AFTER the actual
  queue record is authorized. No audit read filter, role/module/object authority,
  mutation allowlist, historical event or signature is relaxed/rewritten. Mounted
  signed-JWT tests exercise both aliases, own-tenant visibility, foreign/anonymous
  denial, spoofed-payload refusal, replay and hidden unchanged tenantless history;
  retry assertions retain every existing complete-object denial case.
- The initial new PG regression exposed a test-expectation defect: raw psycopg
  `InvalidForeignKey`, not SQLAlchemy `ProgrammingError`, is the actual cursor
  exception. The first failed run is retained; the test now requires that exact
  exception and unchanged atomicity assertions. The first fresh-case failure was
  genuinely missing runtime schema, and the legacy case passed before the fix.
- New job-audit assertions genuinely failed in all four enqueue/retry alias cases
  before the metadata fix. All original assertions remain. Five owning security
  modules exit0 afterward. Other audit producers and CI fixtures remain unfinished.

Distinct author diagnostic directories under this run's canonical scratch root
`$PANTHEON_STATUS_ROOT/.orchestrator/worker-runtime/scratch/pi-20261015T042723Z-64b2f81a/`
retain exact commands/selections, real exits/durations, head/tree, dirty flags,
420s caps, environment sanitation, NODE_ENV=test and raw-log SHA256. They are
**not canonical receipts**. Before-fix runs include declared test-only working
diffs; after-fix runs below are clean exact-head measurements.

| Directory | Measured head | Exit | Raw-log SHA256 |
|---|---|---:|---|
| `audit-prerequisite-before` | 1c19 + new tests | 1 | `bcabd121e87912590ba497deb7c8b87d7828607e5d3afef28e4fb2cfebb1ccc0` |
| `audit-prerequisite-after` | 8010e6570c80 | 0 | `adab773e983d60f1ca201825d128934cd2bf175b8a9bea22491308017eaf2a48` |
| `official-pg-composition` | 8010e6570c80 | 0 | `17a9d0ea7d37b8dfe21a5a011c072c8baba3779fbaf74aa52a1d6af3622dea9f` |
| `migration-source-lint` | 8010e6570c80 | 0 | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| `job-audit-tenant-before` | 8010 + new tests | 1 | `10d10287411f610bff82328376e7961401bc2b1993352a73ad45dd104e1dfc0e` |
| `job-audit-tenant-after` | a83c6d674342 | 0 | `7ede3989d553500dac25c6a0c83a6c9fb4a3088a73e940319bce0b8e16caa828` |
| `job-audit-source-lint` | a83c6d674342 | 0 | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| `source-boundaries` | a83c6d674342 | 0 | `eb0ae805dc18817f00a3e391583c44cb64fe5dc5fa58829e974507df4cf56d2d` |

**Manager scope/lease action needed:** [exact proposed batch and preserved controls](MANAGER-SCOPE-REQUEST.md).
The named remaining producer/fixture paths are absent from canonical allocated
`files`; no self-granted expansion was performed. Full failed-job logs have NOT
all been inspected, and not every CI cause is diagnosed. At old1c19, PR1478 is OPEN,
autoMergeRequest=null; CI38681182077 already has four failed leaves while
product-lint-unit was still IN_PROGRESS at readback. This is not new-head evidence.
Original FULL blocked-task record still equals the preceding Pi allocation snapshot.

Final-head canonical14/all17/normal CI/configured independent Codex2 assessment
remain owed. Finish author repairs before governed submission; no final-head proof
is transferred from these incremental measurements, even to a documentation-only
successor. Human/Ops/PENDING, all original failed epochs/history, operational HOLD
and goalfalse persist. No submission, approval, merge arming, merge/done or live
credential/account/session/recovery/deployment/cloud/data/model/F11 effect.

## Previous Pi checkpoint — run pi-20261015T041654Z-d24c3830 (historical)

Worker Manager allocated Pi run `pi-20261015T041654Z-d24c3830` to the existing
successor workspace; canonical owner is Pi, final reviewer remains **Human/Ops**.
Pi also appears in the ORIGINAL task's failed-owner history: this allocation is
not failed-epoch clearance or independent execution from every previous account.
The quota interruption, refused Codex owner request, previous Antigravity author
contributions and initial overwritten Vitest raw-log gap remain historical facts.

- Current admitted base at this dispatch: `c0c8c8226acaab3cbe8bb76e79f01e5e6694abb3`.
  Two-parent merge `cf8eafd7a826a1a11f86d7a38e3d41d4b426d2fa` preserves local
  `1c4c3c55f9d9fb96f30d628d834e7108a0a11c9a` (Antigravity scanner fixture fix)
  and the current base. No conflict, reset, rebase or discarded task history.
  Incoming `.orchestrator/common.py`, `test_common.py` and
  `test_adapter_fallback_policy.py` remain byte-identical to that base; no new
  control-plane policy/authentication change was authored by this continuation.
- Focused base controls and diff check both exited 0 at **cf8eafd7a826 only**.
  Normal push updated existing PR1478 without review submission or merge arming.
  Readback at cf8 showed `autoMergeRequest=null`, `task-review-gate=FAILURE`,
  and normal CI [38680820149](https://github.com/alfloop-dev/odayplus/actions/runs/38680820149)
  still IN_PROGRESS. These are NOT terminal CI acceptance or later-head proof.
- Code anchor `68bd4ab038c92a16ac970dccf60e0974a4e98af3` changes ONLY typed
  revision/parent/branch metadata in Alembic 0025/0026, matching the repository
  convention consumed by the existing single-head tripwire. Revision IDs,
  parents, SQL, upgrade/downgrade behavior and all test assertions are unchanged.
  Existing `test_migration_revisions_have_one_head` genuinely failed at cf8
  (`0025_audit_worm_outbox.py declares no revision`); its whole owning module
  exits 0 at 68bd. `tests/ops/test_migration_backfill.py -q -n 2` also exits 0
  at 68bd. This does NOT fix or prove the missing runtime audit FK prerequisite.
- Only the metadata portion of the previous Antigravity DB helper's frozen
  `peer-3.uncommitted.patch` was adopted manually. Snapshot SHA256 checked:
  `b0d19150579bf810331ac299cf507b67a55f77f9227ba42189794956ca96990b`.
  No old helper workspace/process was edited, cleaned, signalled or resumed.
  No added repair path outside the existing allocated source scope was modified.

Author diagnostic logs and terminal records are retained under the canonical
run scratch root
`$PANTHEON_STATUS_ROOT/.orchestrator/worker-runtime/scratch/pi-20261015T041654Z-d24c3830/`.
Each selection directory has `raw.log` and `terminal.json` binding actual command,
head/tree, exit code, duration, 420s cap, environment sanitation, NODE_ENV=test,
checkout cleanliness and raw-log SHA256. These records are explicitly
**author-diagnostic-not-canonical**, never copied into canonical receipt storage.

| Directory | Measured head | Exit | Raw-log SHA256 |
|---|---|---:|---|
| `base-composition-controls` | cf8eafd7a826 | 0 | `30a6af55cf4b3ea108e20d227f8aa2880bc480b2f41d4c708ef0b8b3d1c0d91a` |
| `base-diff-check` | cf8eafd7a826 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `migration-metadata-before` | cf8eafd7a826 | 1 | `314b98581b94abe771679d16bc259b4ef7199cce9fd9d8c0b5b8a5760618677c` |
| `migration-metadata-after` | 68bd4ab038c9 | 0 | `06955fb47f16312e5d13266c774a2aa4648c46e423cbe6564a92e25b68e39131` |
| `migration-plan-after` | 68bd4ab038c9 | 0 | `314a40cae7f36ed2b46bbee5f41bb6ad134f2acb05f07aab92bf77e55571e87c` |

**Still in progress, not ready for review:** full retained failed-job logs have
not all been inspected in this continuation; the runtime migration prerequisite,
verified future audit-producer tenant attribution, authenticated performance/E2E
fixtures, GO/expiry fixtures and other CI findings remain to be repaired/verified.
Inventory/justify any additional paths with Manager before modifying them. Preserve
all authentication/tenant/FK/GO/expiry denials, actual PostgreSQL controls, performance
150 requests and waves10/20/50, P95 3s/three-run gate, and all E2E members.
Before review submission, finish owned author work and obtain fresh final-head
canonical14, all17 bounded selections, actual terminal hosted CI and independent
configured Codex2 supplied-text assessment with explicit coverage gaps. No previous
553/dcf1/cf8/68bd evidence transfers to a later head. No approval, merge arming,
merge, done or live effect is authorized. Original blocked six-reopen history,
absent-original UNKNOWN, operational HOLD and goalfalse persist.

## Original composition checkpoint (historical, not current-head equivalence)

The identities and equality observations below describe the original 553-era
composition against base 7f4abcce80c7, not the changed continuation head or moving
`origin/dev`. The scanner fix and metadata repair above intentionally differ
from pinned dcf1; the current base also includes PR1479's three auth-probe paths.

### Pinned identities

| Item | Value |
|---|---|
| Admitted base (`origin/dev` at allocation) | `7f4abcce80c76ea619710988c1a833a509dbbb8b` |
| Pinned repair source | `dcf1f258f6544e478b14352dcbbbffbb0b602ede` |
| Pinned repair tree | `048395fae4faa37fff4ce07248c5034564c4b374` |
| Repair branch (not edited) | `task/ODP-DEV-SMOKE-CREDENTIAL-RECOVERY-IMPLEMENTATION-001-response-boundary-repair` |
| Merge base | `359e480a46df56940cebef1346dfd5f855626b9e` |
| dev...repair | 3 / 116 commits |
| Successor branch | `task/ODP-RECOVERY-SOURCE-INTEGRATION-001` (fresh Worker-Manager worktree from dev) |
| Composed tree after the three composition commits | `f3e93639cf64595225d4a160363bef930d0ed2cd` |

The composed tree is identical to `git merge-tree --write-tree origin/dev dcf1f258f`
(clean, no conflicts). History of the 116 repair commits is NOT imported: the
composition was applied by restoring exactly the 315 repair-side paths from
`dcf1f258f` onto the admitted base, then committing in three scoped,
private-index, trailered commits.

### Original scope inventory and justification

All 315 paths are those changed by `359e480a4..dcf1f258f` (258 added, 57
modified, 0 deleted). Mainline-only changes are the seven
`docs/evidence/human-decisions/` files from the three dev commits; overlap with
the repair side is 0 and they are untouched (`git diff origin/dev HEAD --
docs/evidence/human-decisions` is empty).

| Commit | Paths | Justification |
|---|---:|---|
| compose pinned dcf1 source paths | 60 | Production/tooling/schema/workflow source of the recovery implementation and its response-boundary repair: `apps/api`, `apps/web` (non-test), `delivery_toolchain/{e2e,release}`, `infra/db/migrations`, `modules/{listing,notifications,opsboard}`, `packages/openapi-client`, `shared/{audit,identity,infrastructure,observability}`, `docs/design`, `docs/audits/code-boundary-inventory.csv`, and the dispatch-only `.github/workflows/dev-smoke-credential-consumer.yml` |
| compose pinned dcf1 test paths | 64 | Python and Web tests exercising the above; required by the declared verification selections |
| preserve original task evidence | 191 | `docs/evidence/runtime/ODP-DEV-SMOKE-CREDENTIAL-RECOVERY-IMPLEMENTATION-001/**` preserved as historical record only |

Every path is byte-identical to `dcf1f258f` (`git diff dcf1f258f HEAD -- <315 paths>`
is empty). No path outside that set changed relative to `origin/dev`, apart
from this record.

### Workflow note

`.github/workflows/dev-smoke-credential-consumer.yml` is part of the pinned
source. It declares only a `workflow_dispatch` trigger, its job is gated to
`refs/heads/dev`, and it does not alter the CI workflow triggers. Merging it is
part of the Human/Ops source decision; this successor does not dispatch it.

## Preserved original-history state

- `ODP-DEV-SMOKE-CREDENTIAL-RECOVERY-IMPLEMENTATION-001` stays BLOCKED with its
  owner/reviewer, six reopens, failed owners, Human/Ops escalation and
  allocated `a1294dbe62b6259d70823fb5e252ed476758cbc0` / PR1459 unchanged.
  It is not reset, reassigned, unblocked, superseded, re-enrolled or resubmitted.
- The absent original worktree remains UNKNOWN and is not recreated.
- The 191 historical evidence files, including their `receipts/` JSON, are NOT
  evidence for this successor. Nothing was copied into `.orchestrator/evidence`.
- dcf1 author measurements and the four bounded advisories are not transferred.

## Verification and review routing

Fresh canonical receipts are produced only by
`delivery_toolchain/git/task_verification.py run --task-id ODP-RECOVERY-SOURCE-INTEGRATION-001`
at the immutable head that contains this record. The 17 inherited
comprehensive/supplemental selections are re-measured separately at that same
head with unchanged members, caps and environment sanitization. Results, CI
URLs and statuses are reported in the task status / PR body, not by editing this
record after measurement (that would move the head).

Outstanding before any admission:

1. Hosted CI terminal statuses at the NEW head (normal PR trigger only).
2. Fresh independent Codex2 supplied-text security assessment with explicit
   inspected composition, omissions and whole-composition coverage gaps.
3. Human/Ops final decision.

Goal acceptance stays false. Operational HOLD, quarantine, bindingNULL and
cleanupUNKNOWN persist. External-writer exclusion, native custody/configuration,
source admission, reserves and a reviewed operational plan remain unapproved.
