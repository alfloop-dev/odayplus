# Per-branch strict branch-protection acceptance packet

- Sidecar task: `ODP-ORCH-BRANCH-PROTECTION-PER-BRANCH-STRICT-001-SIDECAR-ACCEPTANCE`
- Parent task: `ODP-ORCH-BRANCH-PROTECTION-PER-BRANCH-STRICT-001`
- Helper kind: `acceptance_packet`
- Sidecar owner: `Codex`
- Assigned sidecar reviewer: `Claude`
- Prepared: `2026-08-11`
- Scope: support artifact only

## Purpose and authority boundary

This packet gives the parent owner and reviewers a concrete acceptance
checklist, dependency map, evidence contract, and replay plan for stopping the
branch-protection applicator from re-enabling GitHub's up-to-date-branch
requirement on `dev`. It is advisory support material: it does not define
canonical policy, approve the parent implementation, change live GitHub
settings, or close the parent task.

Only this Markdown file belongs to the sidecar. The parent owns all changes to
`.github/branch-protection/policy.json`, `delivery_toolchain/github/apply_branch_protection.py`,
and `tests/security/test_branch_protection_policy.py`.

## Problem statement and target invariant

The baseline applicator builds one payload with
`required_status_checks.strict=true` and sends it to both `dev` and `main`.
That makes every later applicator run capable of undoing the operational
`dev` setting required by the merge-queue flow.

The parent acceptance contract is:

```text
shared policy defaults
        + branch-specific overlay, when present
        -> resolved policy for that branch
        -> GitHub branch-protection payload

dev  -> required_status_checks.strict = false
main -> required_status_checks.strict = true
legacy policy with no branch overlays -> strict = true
```

`strict=false` here changes only GitHub's "branch must be up to date before
merging" bit. It must not remove required check contexts, bypass the merge
queue, weaken administrator enforcement, or change pull-request review policy.

## Candidate implementation snapshot

The following is a time-bounded observation, not sidecar approval:

- Parent implementation commit
  `68632cc03f00b97af2cda2510779c9cea5b0143a` adds a `branches.dev.strict=false`
  overlay, a branch-policy resolver, a default of `strict=true`, and payload
  construction inside the per-branch apply loop.
- That commit changes only the parent policy and applicator; it does **not**
  change the task-listed focused test file.
- At packet preparation time, the published parent head was
  `69806ef32c6f85c44ebb0d8f2fd6992f22865f40`, and the implementation commit was
  its ancestor.
- GitHub PR #688 was open against `dev` at that exact published head. Its
  `orchestrator`, `product`, `performance-gate`, and `product-e2e-gate` checks
  were successful, but `task-review-gate` was failing and GitHub reported the
  PR as `BLOCKED`.

Consequently, the candidate is inspectable but the parent acceptance contract
is not yet fully evidenced. In particular, historical green checks cannot
replace focused regressions, a successful independent review gate on the exact
final head, or merge-queue integration.

## Dependency map

| Authority or input | Consumer | Required result | Evidence boundary |
| --- | --- | --- | --- |
| Top-level policy defaults | Branch-policy resolver | Supplies shared required checks, admin enforcement, and review settings | Unit inspection plus payload assertions |
| Optional `branches.<name>` entry | Branch-policy resolver | Overlays only the named branch; other branches retain defaults | Dedicated `dev` and `main` regression cases |
| Resolved branch policy | Payload builder | Maps `strict` to `required_status_checks.strict` while preserving contexts | Focused unit tests |
| `dev` resolved payload | `PUT /repos/{repo}/branches/dev/protection` | Sends `strict=false` with every required context intact | Mocked CLI-call test or captured request evidence |
| `main` resolved payload | `PUT /repos/{repo}/branches/main/protection` | Sends `strict=true` unless explicitly overridden | Mocked CLI-call test or captured request evidence |
| Policy without `branches` | Resolver and payload builder | Preserves the historical `strict=true` default | Backward-compatibility regression |
| GitHub `dev` branch protection | Merge queue | Does not require each PR branch to be current before queue composition | Exact-head PR checks plus queue/merge evidence |
| GitHub `main` branch protection | Promotion path | Retains the existing safe default | Readback or request-payload evidence; no `main` mutation by this sidecar |
| Exact parent PR head | Independent `task-review-gate` | Receives a success conclusion for the reviewed SHA | GitHub check-rollup evidence |

### Composition flow

```text
.github/branch-protection/policy.json
                    |
                    v
         resolve policy for branch
          /                     \
         v                       v
 dev overlay: strict=false   main: default strict=true
         |                       |
         +----------+------------+
                    v
              build payload
                    |
                    v
     PUT branch-specific protection
                    |
        +-----------+-----------+
        v                       v
 dev merge queue             main promotion
```

## Acceptance checklist

Statuses in this packet mean `READY FOR REPLAY` or `PENDING EVIDENCE`; they do
not grant parent approval.

### Policy resolution

- [ ] `dev` resolves to `required_status_checks.strict=false`.
- [ ] `main` resolves to `required_status_checks.strict=true` when it has no
  explicit overlay.
- [ ] A policy with no `branches` key preserves `strict=true` for every target
  branch.
- [ ] An overlay affects only its named branch and cannot leak into the payload
  for the next branch processed in the loop.
- [ ] The overlay preserves top-level `required_status_checks`,
  `enforce_admins`, and pull-request review settings unless that branch
  explicitly overrides them.
- [ ] `policy.json` contains the narrow `dev` delta and does not weaken the
  required check list.

### Application behavior

- [ ] The applicator builds a fresh resolved payload for each target branch;
  it does not reuse one pre-loop payload for both branches.
- [ ] The `dev` API request contains `strict=false` and the complete required
  context list.
- [ ] The `main` API request contains `strict=true` and the same shared
  contexts.
- [ ] Existing parse failures, API failure aggregation, and non-zero exit
  behavior remain intact.
- [ ] Human/ops fallback output does not falsely describe one shared strictness
  value when branch-specific payloads differ.

### Regression coverage

- [ ] `tests/security/test_branch_protection_policy.py` directly proves the
  `dev`, `main`, and missing-`branches` cases.
- [ ] A two-branch regression proves processing `dev` first cannot mutate or
  contaminate `main`.
- [ ] Existing payload-builder tests remain green, including review-enabled and
  review-disabled configurations.
- [ ] If `main()` is covered with mocked GitHub calls, the test asserts the
  actual per-endpoint request bodies rather than only helper return values.

### Delivery and live acceptance

- [ ] The parent diff is limited to its declared policy, applicator, and test
  surfaces, with no sidecar or canonical-truth sweep-in.
- [ ] Focused tests and `git diff --check` pass at the exact final parent head.
- [ ] All required PR checks pass for that same head.
- [ ] Independent `task-review-gate` succeeds for that same head; an owner
  cannot substitute a local assertion or an older green run.
- [ ] The parent PR enters the `dev` merge queue and merges through it.
- [ ] Post-merge/readback evidence confirms `dev.strict=false`; if operational
  authority permits a `main` readback, it remains `true`.

## Required evidence matrix

| Evidence | Minimum acceptable record | Current packet state |
| --- | --- | --- |
| Focused unit tests | Command, exit code, test count, exact tested SHA | Pending; candidate commit added no focused-test delta |
| Scope audit | `git diff --name-only <base>...<head>` and `git diff --check` | Pending at final parent head |
| PR identity | PR URL/number, base, head ref, exact head SHA | PR #688 observed at `69806ef3`; must be refreshed after any parent change |
| Required checks | Check names and conclusions bound to exact head SHA | Four green checks observed; `task-review-gate` failed |
| Queue integration | Queue/merge evidence and resulting `dev` merge SHA | Pending |
| Protection outcome | Branch-specific request capture or authoritative GitHub readback | Pending; this sidecar performs no live mutation |

## Reviewer replay plan

Run these from the final parent task branch after the parent owner has completed
the focused tests:

```bash
python3 -m pytest tests/security/test_branch_protection_policy.py -q
git diff --check origin/dev...HEAD
git diff --name-only origin/dev...HEAD
```

Then inspect the exact final diff and verify these payloads independently:

```text
dev.required_status_checks.strict  == false
main.required_status_checks.strict == true
dev.required_status_checks.contexts == main.required_status_checks.contexts
```

Finally, bind GitHub evidence to the same final SHA:

1. confirm the open parent PR is `task/<parent-task-id> -> dev` and non-draft;
2. confirm every required check, including independent `task-review-gate`, is
   successful on that SHA;
3. confirm the PR merges through the `dev` queue;
4. record the merge SHA and branch-protection readback without changing policy
   from this sidecar lane.

## Findings for parent-owner absorption

1. **Blocking evidence gap — focused regressions:** the observed parent
   implementation commit does not modify
   `tests/security/test_branch_protection_policy.py`, although the task brief
   names that file and explicitly requires focused tests. The parent owner
   should add direct branch-resolution and backward-compatibility coverage.
2. **Blocking delivery gap — independent review gate:** PR #688 currently has a
   failed `task-review-gate`; therefore it is not ready to merge or satisfy the
   exact-head acceptance criterion.
3. **Review note — operator fallback wording:** the failure message currently
   lists shared checks but not branch-specific strictness. The reviewer should
   decide whether accurate per-branch fallback output is required in this
   parent slice; at minimum it must not imply that `dev` and `main` receive an
   identical payload.

## Handoff disposition

The packet is ready for sidecar review by `Claude`. It supplies a replayable
acceptance contract and records current parent gaps without changing or
approving parent implementation. After sidecar review, the parent owner may
absorb the checklist and findings; only the parent reviewer may approve the
parent task.
