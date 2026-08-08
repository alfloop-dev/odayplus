# ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001 Acceptance Packet

## Packet identity

| Field | Value |
|---|---|
| Sidecar task | `ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001-SIDECAR-ACCEPTANCE` |
| Parent task | `ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001` |
| Helper kind | `acceptance_packet` |
| Sidecar owner / reviewer (current) | `Claude` / `Claude2` |
| Sidecar owner / reviewer (at first delivery) | `Codex` / `Antigravity4` |
| Current parent owner / reviewer | `Codex` / `Codex9` |
| Observed parent branch | `task/ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001` |
| Observed parent head (2026-08-02; superseded) | `1a381de9037b712f34ab513e036b62ba7f5d4331` |
| Parent PR | `#562`; open and `DIRTY` when observed, **merged 2026-08-02T14:15:19Z** (see Closeout refresh) |
| Packet verdict | **Support only; no parent acceptance, merge, or production GO claim** |

This packet is a review aid for separating ordinary task/dev merge CI from
final production release authority. It does not change workflows, checkers,
registries, receipts, branch protection, runtime behavior, or canonical truth.
The parent owner decides whether to compose this packet; the parent reviewer
must independently stamp the exact implementation head.

## Observed state and review freeze

The remote parent head observed on 2026-08-02 UTC is
`1a381de9037b712f34ab513e036b62ba7f5d4331`. GitHub reports PR `#562` as open
with merge state `DIRTY`; its only reported rollup entry at observation time is
a failing `task-review-gate`. The implementation is therefore **not accepted or
merge-ready** by this packet.

The task-owned review range is the five-commit linear range
`eed83c0937f491211247ee3fdb0bdf8d932564fb..1a381de9037b712f34ab513e036b62ba7f5d4331`:

1. `76b5a43e1e4e0b0592de1acd24bc4e3663519de2` — anchor CI boundary;
2. `b047aa18baa81b4b47fda8b58fba0b7a7d4bb1d7` — finalize evidence;
3. `26ebc052cf60c181d685cce26b4f34b1110fe3ee` — bind immutable release authority;
4. `cfcfeb2e65847dd422026e2ccd9f628bcc1a6aae` — cover merge-only product changes;
5. `1a381de9037b712f34ab513e036b62ba7f5d4331` — use first-parent merge deltas.

Reviewers should audit this range rather than attribute every path in
`origin/dev..parent-head` to this task. The parent branch composes other work,
so that broader comparison contains unrelated product paths. Any conflict
resolution, base refresh, commit, or force of a new PR head invalidates this
observed-head record and requires a new exact-head handoff and full focused
verification.

## Task-owned surface map

| Layer | Task-owned paths in the five-commit range | Intended responsibility |
|---|---|---|
| Dev CI | `.github/workflows/ci.yml`, `Makefile` | Run the deterministic product E2E gate for task/dev merges while accepting an internally valid `NO-GO` registry. |
| Production promotion | `.github/workflows/promote-dev-to-main.yml`, `Makefile` | Require authentic final `GO` and bind validation, PR head, status stamp, and auto-merge to one immutable promotion SHA. |
| Gate validation | `scripts/e2e/check_product_release_gate.py`, `scripts/e2e/check_release_gate_registry.py` | Separate dev-merge and production modes; enforce exact/evidence-only candidate ancestry and fail closed on product drift. |
| E2E receipt mechanics | `scripts/e2e/product_e2e_receipt.py` | Support runner-generated exact-source evidence without manufacturing production authority. |
| Release truth and evidence | `docs/evidence/gates/RELEASE_GATE_REGISTRY.json`, `docs/evidence/ci/ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001.md` | Preserve honest `NO-GO` truth and record the implementation boundary. |
| Regressions | `tests/e2e/test_release_gate_registry.py`, `tests/integration/test_flow_002_expansion_persistence.py` | Prove mode separation, SHA binding, merge ancestry behavior, and the tenant-scoped Flow-002 fix. |

No Package 10 UI, deployment, Human/Ops approval, genuine Gate 0-6 receipt, or
production release decision is supplied by this task.

## Dependency and consumer map

| Relationship | Dependency or consumer | Contract supplied or consumed | Required disposition |
|---|---|---|---|
| Input authority | Successful `dev` workflow-run event | Exact `github.event.workflow_run.head_sha` selected for promotion | Check out and validate that SHA. Never infer authority from a moving `dev` ref. |
| Input truth | `docs/evidence/gates/RELEASE_GATE_REGISTRY.json` | Candidate SHA, Gate 0-6 receipts, blockers, Human/Ops sign-off, and release decision | Default integrity validation may accept honest `NO-GO`; production must use `--require-go` and candidate binding. |
| Dev merge consumer | Pull requests and ordinary CI into `dev` | Complete deterministic product E2E execution and a fresh runner receipt | A valid `NO-GO` must not deadlock the merge solely because production approval is absent. Structural, inventory, runtime, or E2E failures still fail CI. |
| Promotion consumer | `promote-dev-to-main.yml` and the `dev -> main` PR | Final production release authorization for the validated immutable SHA | Require Gate 0-6 `GO`, reject PR-head drift, and stamp/auto-merge only the validated head. |
| Git topology input | Candidate-to-expected ancestry | Exact candidate or evidence-only descendant policy | Reject non-ancestor, unknown SHA, product/test/config drift, and merge-resolution product changes. Accept stale-second-parent merges only when the resulting first-parent delta is evidence-only. |
| Branch-protection consumer | Required `product-e2e-gate` and `task-review-gate` statuses | Merge and review signals bound to a commit | The status context must not be treated as production GO, and a status must never be stamped on a different SHA. |
| Blocked task-PR consumers | Sidecar and product PRs whose fresh heads descend from an older release candidate | Evidence-only ancestry without weakening production release checks | After the parent fix merges to `dev`, refresh each affected PR, rerun its complete checks, and request exact-head re-review. No blanket approval carries forward. |

## Fail-closed acceptance checklist

The parent reviewer should replace each `PENDING` with `PASS` or `FAIL` and cite
an exact command/run plus immutable head. Every row is required; this sidecar
does not pre-fill a parent verdict.

### A. Scope and provenance

| ID | Required proof | Reject when | Status | Evidence |
|---|---|---|---|---|
| A1 | Remote PR head, reviewed source head, and tested head are the same full 40-character SHA. | A short, nonexistent, local-only, or drifted SHA is cited. | `PENDING` | Parent reviewer to fill |
| A2 | Review covers the five task commits and ten paths listed above, with composed unrelated paths identified separately. | The broad `origin/dev..head` diff is attributed wholesale to this task or a task-owned path escapes review. | `PENDING` | Parent reviewer to fill |
| A3 | PR `#562` is conflict-free against current `dev` and all required checks are green at the reviewed head. | Merge state is `DIRTY`, checks are absent/pending/failing, or conflict resolution changes the reviewed head without re-review. | `PENDING` | Parent reviewer to fill |

### B. Dev-merge behavior

| ID | Required proof | Reject when | Status | Evidence |
|---|---|---|---|---|
| B1 | `.github/workflows/ci.yml` retains the required product E2E job and calls `make product-e2e-gate`. | Ordinary task/dev CI calls the production `--require-go` boundary or skips deterministic E2E. | `PENDING` | Parent reviewer to fill |
| B2 | `product-e2e-gate` validates registry integrity, runs `check_product_release_gate.py --dev-merge`, then executes the complete deterministic runner. | A malformed registry, missing inventory, stale structural receipt, runner failure, or product regression passes. | `PENDING` | Parent reviewer to fill |
| B3 | A structurally valid registry whose decision is `NO-GO` exits zero in dev-merge mode without changing gate truth. | The gate manufactures `GO`, clears blockers/receipts, or rejects solely because authentic production approval is absent. | `PENDING` | Parent reviewer to fill |

### C. Production release authority

| ID | Required proof | Reject when | Status | Evidence |
|---|---|---|---|---|
| C1 | Promotion checks out the successful workflow-run head and invokes `make product-release-gate EXPECTED_SHA=<that exact SHA>`. | A moving branch ref or unbound registry candidate is validated. | `PENDING` | Parent reviewer to fill |
| C2 | Production mode supplies both `--require-go` and `--expected-sha`; the current honest `NO-GO` registry exits non-zero. | Production accepts `NO-GO`, missing Human/Ops sign-off, uncleared gates, stale receipts, or a candidate mismatch. | `PENDING` | Parent reviewer to fill |
| C3 | The opened/reused promotion PR head equals `PROMOTION_SHA` before status stamping and auto-merge. | `dev` advances, the PR head drifts, or the stamped SHA differs from the validated SHA. | `PENDING` | Parent reviewer to fill |

### D. Candidate ancestry and merge topology

| ID | Required proof | Reject when | Status | Evidence |
|---|---|---|---|---|
| D1 | Exact candidate equality passes; an evidence-only descendant may pass under the explicit evidence allowlist. | Candidate is not an ancestor, Git resolution fails, or an intervening path is outside the evidence allowlist. | `PENDING` | Parent reviewer to fill |
| D2 | Tree diff plus first-parent merge-delta inspection catches non-evidence changes introduced by merge resolution. | A merge whose resulting tree changes product code is accepted because its individual parent commits appear evidence-only. | `PENDING` | Parent reviewer to fill |
| D3 | An evidence-only merge from a stale second parent passes when the candidate is the merge first parent and the resulting delta is evidence-only. | Product changes already present in the candidate are falsely re-reported solely from an all-parent merge diff. | `PENDING` | Parent reviewer to fill |
| D4 | Tests cover exact match, non-ancestor, ordinary product drift, merge-only product change, and stale-second-parent evidence merge. | Any topology is untested or fails open on subprocess/git errors. | `PENDING` | Parent reviewer to fill |

### E. Truth preservation and downstream recovery

| ID | Required proof | Reject when | Status | Evidence |
|---|---|---|---|---|
| E1 | Registry remains internally valid `NO-GO`, with zero of seven gates cleared and zero passing receipts unless authentic independent evidence changed it. | Archived task completion, synthetic evidence, or this CI repair is used to clear a release gate. | `PENDING` | Parent reviewer to fill |
| E2 | Archived-done task diagnostics are reconciled without erasing the real Gate 0-6 blockers. | A planning-status cleanup is represented as release evidence or production readiness. | `PENDING` | Parent reviewer to fill |
| E3 | Each previously blocked PR refreshes from the merged fix and reruns its own exact-head checks and review. | Prior approval/checks are copied to a new head or the CI repair is treated as blanket product acceptance. | `PENDING` | Parent reviewer to fill |

## Required verification ledger

Run on the final, conflict-resolved parent head and record commands, exit codes,
test counts, and GitHub run URLs. The minimum focused batch is:

```text
uv run pytest -q tests/e2e/test_release_gate_registry.py \
  tests/e2e/test_acceptance_coverage.py \
  tests/integration/test_flow_002_expansion_persistence.py \
  tests/security/test_branch_protection_policy.py
uv run ruff check scripts/e2e/check_product_release_gate.py \
  scripts/e2e/check_release_gate_registry.py \
  scripts/e2e/product_e2e_receipt.py \
  tests/e2e/test_release_gate_registry.py \
  tests/integration/test_flow_002_expansion_persistence.py
python3 scripts/e2e/check_release_gate_registry.py
python3 scripts/e2e/check_product_release_gate.py --dev-merge
python3 scripts/e2e/check_product_release_gate.py --require-go
git diff --check
```

Expected polarity on the current registry is: registry integrity `0`, dev-merge
mode `0`, and production `--require-go` mode non-zero with an explicit `NO-GO`
message. The non-zero production command is a required negative assertion, not
a failed verification batch.

The exact ancestry regressions that must pass include:

- `test_cli_expected_sha_ancestry_merge_commit_product_change_fails_closed`;
- `test_cli_expected_sha_ancestry_stale_second_parent_evidence_merge_passes`;
- `test_dev_merge_gate_accepts_valid_no_go_but_release_gate_fails_closed`;
- `test_ci_and_promotion_workflows_use_separate_gate_modes`.

GitHub acceptance additionally requires the real PR jobs at the same reviewed
SHA. Local focused tests cannot prove branch protection, GitHub token scope,
status-stamp targeting, or auto-merge behavior.

### Sidecar verification observation

The sidecar owner independently inspected parent head
`1a381de9037b712f34ab513e036b62ba7f5d4331` in a detached temporary worktree.
The first four-test run produced three passes and one environment failure because
the fresh worktree had not installed `@playwright/test`. After reproducing the
workflow prerequisite with `npm ci --ignore-scripts --no-audit --no-fund`, the
same batch produced `4 passed`.

The focused Ruff invocation over the five changed Python files passed. Registry
integrity and dev-merge static mode both exited `0`; production `--require-go`
exited `1` with the expected `NO-GO` result; `git diff --check` passed. These
results corroborate the checklist semantics only. They do not override the open
PR's `DIRTY` merge state, replace the full suite, or supply GitHub exact-head
acceptance.

## Reviewer handoff record

Reviewer `Antigravity4` reviews this sidecar only for accuracy, completeness,
and support-only scope. Parent reviewer `Codex9` owns the implementation verdict.
After the 2026-08-08 reassignment the sidecar review role is held by `Claude2`
and the sidecar owner role by `Claude`; the parent verdict ownership is
unchanged by that reassignment. See the Closeout refresh section below.

| Review question | Expected answer |
|---|---|
| Did this sidecar change canonical, workflow, checker, registry, receipt, or runtime truth? | No; only this support artifact is in scope. |
| Does it approve parent head `1a381de9...`? | No; PR `#562` was open and `DIRTY` when observed. |
| May dev CI accept an honest `NO-GO`? | Yes, while still requiring registry integrity and complete deterministic product E2E. |
| May production promotion accept that same `NO-GO`? | No; it requires authentic Gate 0-6 `GO` bound to the exact immutable promotion SHA. |
| What invalidates the packet's observed-head facts? | Any new head, conflict resolution, base refresh, status/check change, or PR replacement; refresh evidence and re-review. |
| Who decides whether to absorb this packet? | The parent owner, followed by exact-head acceptance from the parent reviewer. |

## Closeout refresh (2026-08-08 UTC)

This section is added at sidecar closeout. It does not restate a verdict; it
records which of the packet's dated facts have since been superseded, so a later
reader does not mistake a 2026-08-02 observation for live state. The packet's
own invalidation clause required this refresh once the parent head moved.

### 1. The observed-head freeze is superseded

| Fact | 2026-08-02 record | 2026-08-08 verified state |
|---|---|---|
| Parent PR `#562` | open against `dev`, merge state `DIRTY`, failing `task-review-gate` | `MERGED` at `2026-08-02T14:15:19Z` |
| Parent PR head | `1a381de9037b712f34ab513e036b62ba7f5d4331` | `f19611ed6368e9e74f77b8ec6a2fd33367734698` |
| Merge commit | none | `aff272d3da55967497d2aba0e72d569b9b15ff70` |
| Position relative to `dev` | not merged | `f19611ed` is an ancestor of `origin/dev` |

`1a381de9` is an ancestor of the merged head `f19611ed`, but
`git diff --stat 1a381de9 f19611ed` spans 110 files (`+25284 / -1187`). The
five-commit review range frozen above therefore no longer describes the head
that actually merged: the parent branch composed a large amount of unrelated
work before merging.

Consequence for the checklist: rows `A1`-`A3` are historical, not live. `A3` in
particular asked for a conflict-free `#562` at the reviewed head; the branch was
instead refreshed and merged at a different head. The `B`, `C`, `D`, and `E`
rows remain usable as a post-merge audit aid against `f19611ed` or `dev`, but a
reviewer filling them must cite that head, not `1a381de9`. Nothing in this
packet is, or ever was, parent acceptance or a production `GO`.

### 2. Parent task record

The parent task id `ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001` does not resolve
in the live canonical status root's active task set or in `ai-task-archive/` as
read on 2026-08-08. This packet makes no claim about the parent's terminal task
status; only the PR facts in § 1 are independently verified from Git and GitHub.

### 3. Gate behavior re-observed at `0c36566c`

Re-run at sidecar closeout on the task worktree head
`0c36566c4bb14b908fa4f896defd5552c1af68e6` (an ancestor of `origin/dev`):

| Command | Exit | Reading |
|---|---|---|
| `python3 scripts/e2e/check_release_gate_registry.py` | `0` | Registry is structurally valid and still reports `RELEASE STATE: NO-GO` with `0/7` gates cleared. Honest `NO-GO` truth is preserved, as row `E1` requires. |
| `python3 scripts/e2e/check_product_release_gate.py --dev-merge` | `1` | Environment prerequisite failure only: `Playwright --list exited 1: Cannot find module '@playwright/test'`. This is the same missing-`npm ci` condition already recorded under Sidecar verification observation, not a gate regression. Re-run after `npm ci --ignore-scripts --no-audit --no-fund` before drawing any conclusion about row `B2`. |
| `python3 scripts/e2e/check_product_release_gate.py --require-go` | `1` | Expected fail-closed, for two independent reasons: `0/7` gates cleared against release candidate `e496be62c47c45d758681b8a4d3abfae16f1c96d`, and evidence-only ancestry rejecting intervening commits that touch non-evidence paths between that candidate and the current head. |

The non-zero `--require-go` exit is the required negative assertion described in
the verification ledger, not a failed verification. The ancestry rejection is
row `D1` behaving as designed: `dev` has advanced well past the recorded release
candidate, so the candidate binding no longer holds and production authority
correctly refuses to infer `GO` from a moving branch.

Because the deterministic Playwright E2E leg could not run in this worktree, this
refresh does **not** re-attest row `B2` or the full focused batch. The earlier
`4 passed` observation stands only for parent head `1a381de9`.

### 4. Sidecar delivery provenance

| Item | Value |
|---|---|
| Content commit | `73681ce9121b0592a5f3990b16122a92102bc522` — this support file only, `+197` lines |
| First delivery PR | `#568`, head `1600c5d5cd0397b9b3354c373f1758d9731a022e`, merged `2026-08-08T11:32:27Z`, merge commit `af631225ca0bb03ab97b788101d34642ca39227b` |
| Second delivery PR | `#711`, head `14a656e3c755b354a725138efc194b14ae328d0b`, merged `2026-08-08T12:23:09Z`, merge commit `ba2738a870c6199ae553f38b0fad0b25da75b7c6` |

Both PR heads are `dev`-into-task merge commits stacked on `73681ce9`; neither
altered the packet content. `git diff 14a656e3..0c36566c -- support/sidecars/ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001/`
is empty, so the artifact that reached `dev` is byte-identical to the reviewed
one.

### 5. Routing change

Sidecar routing moved from `Codex` / `Antigravity4` to `Claude` / `Claude2` on
2026-08-08. Commit `73681ce9` carries the pre-reassignment trailers; this
refresh commit carries the current pair. The reassignment changes accountability
for closeout only — the packet content authored under the earlier pair stands as
written, and none of it is disowned or re-attested by this section.

## Source basis

- Live canonical task state for current owner/reviewer routing and sidecar scope,
  read on 2026-08-02 UTC.
- Remote parent branch exact head
  `1a381de9037b712f34ab513e036b62ba7f5d4331` and PR `#562` metadata observed
  on 2026-08-02 UTC.
- Parent task evidence at
  `docs/evidence/ci/ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001.md` on that head.
- Task-owned workflow, Makefile, checker, receipt, registry, and regression paths
  in `eed83c0937f491211247ee3fdb0bdf8d932564fb..1a381de9037b712f34ab513e036b62ba7f5d4331`.

Added for the Closeout refresh, all read on 2026-08-08 UTC:

- `gh pr view 562` and `gh pr list --head task/ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001-SIDECAR-ACCEPTANCE --state all`
  for merge state, head SHAs, merge commits, and merge timestamps.
- `git merge-base --is-ancestor` and `git diff --stat` against `origin/dev` for
  the ancestry and drift facts in § 1 and § 4.
- The three gate commands in § 3, run in the sidecar task worktree.
- Live canonical task state at `$PANTHEON_STATUS_ROOT` for the § 2 parent-task
  lookup and the § 5 routing change.
