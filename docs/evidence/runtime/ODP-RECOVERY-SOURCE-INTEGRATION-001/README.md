# ODP-RECOVERY-SOURCE-INTEGRATION-001 — source-only composition record

Source-only successor authorized by the user event recorded in
`support/handoffs/recovery-response-boundary-repair-20261012/source-integration-authorized-20261015/`
(event `22352527`, exact text「授權接受」). That delegation is NOT a Human/Ops
signature, security acceptance, merge permission or operational GO.

Canonical reviewer: **Human/Ops**. Final admission is **PENDING**. This record
grants no approval, merge arming, merge, promotion, deployment, recovery or
credential/account/session effect.

## Pinned identities

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

## Scope inventory and justification

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
