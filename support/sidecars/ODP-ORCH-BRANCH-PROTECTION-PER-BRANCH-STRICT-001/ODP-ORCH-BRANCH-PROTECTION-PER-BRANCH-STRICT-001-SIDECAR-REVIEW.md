# Review Packet: ODP-ORCH-BRANCH-PROTECTION-PER-BRANCH-STRICT-001

## Packet Identity

- Sidecar task: `ODP-ORCH-BRANCH-PROTECTION-PER-BRANCH-STRICT-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-BRANCH-PROTECTION-PER-BRANCH-STRICT-001`
- Helper kind: `review_packet`
- Sidecar owner / reviewer: `Codex2` / `Claude`
- Parent owner / reviewer: `Claude` / `Antigravity4`
- Parent PR: [#688](https://github.com/alfloop-dev/odayplus/pull/688)
- Parent head reviewed: `e809a0d7bd82f29610525eed649a54c87c035ef7`
- Evidence observed: `2026-08-11T11:19:30Z`
- Scope: support artifact only; this packet does not modify or approve canonical truth, runtime, registry, or governance implementation.

## Disposition

**Packet ready for sidecar review.** The parent implementation at the exact head
above satisfies the code-level branch-policy acceptance claims covered by this
packet. Pantheon recorded that head as `review_approved`; parent delivery was
not yet complete at the observation time because PR #688 remained open and one
required CI job (`product`) was still in progress. The parent owner retains all
merge and closeout responsibility.

## Parent Change Summary

The parent diff has three task-owned files:

| File | Review-relevant change |
| --- | --- |
| `.github/branch-protection/policy.json` | Adds a `branches.dev.strict: false` delta while leaving shared required contexts and admin enforcement at the top level. |
| `delivery_toolchain/github/apply_branch_protection.py` | Resolves a shallow per-branch overlay before building each branch payload; `strict` defaults to `true` when no override exists. |
| `tests/security/test_branch_protection_policy.py` | Adds four regression cases covering overlay preservation, an unlisted branch, a policy without overlays, and the shipped dev/main policy split. |

GitHub reports the parent PR diff as 71 additions and 7 deletions across these
three files. Two substantive commits carry the task change, with two intervening
base-advance merges:

| Commit | Purpose |
| --- | --- |
| `68632cc0` | Introduces branch overlays and stops re-applying `strict=true` to `dev`. |
| `e809a0d7` | Pins the accepted behavior with focused regression tests and required task trailers. |

## Behavior and Acceptance Matrix

| Acceptance claim | Evidence at `e809a0d7` | Result |
| --- | --- | --- |
| `dev` resolves `required_status_checks.strict=false` | Shipped-policy regression test plus live GitHub required-status-checks API response | PASS |
| `main` preserves `strict=true` | Unlisted-branch and shipped-policy regression tests plus live GitHub API response | PASS |
| Policies without `branches` preserve the prior strict default | `test_branch_policy_without_branches_key_preserves_strict` | PASS |
| Shared settings survive the branch delta | Overlay test pins contexts/admin setting; shipped-policy test pins equal contexts and `enforce_admins=true` for dev/main | PASS |
| Parent focused tests pass at the exact approved head | Independent detached-worktree run, six tests | PASS |
| Exact head has an independent review gate | GitHub `task-review-gate=SUCCESS` on `e809a0d7...` | PASS |
| Full required PR checks pass and merge queue completes | `product` was still `IN_PROGRESS`; PR state `OPEN`, merge state `BLOCKED` at observation time | PENDING — parent closeout only |

## Independent Evidence

### Focused tests at the immutable parent head

The sidecar owner created a temporary detached worktree at the exact parent
head and ran:

```bash
python3 -m pytest tests/security/test_branch_protection_policy.py -q
```

Observed output:

```text
......                                                                   [100%]
```

The temporary checkout remained clean and was removed after the run.

### Live GitHub policy snapshot

Read-only API calls:

```bash
gh api repos/alfloop-dev/odayplus/branches/dev/protection/required_status_checks
gh api repos/alfloop-dev/odayplus/branches/main/protection/required_status_checks
```

Observed relevant fields:

| Branch | `strict` | Required contexts |
| --- | --- | --- |
| `dev` | `false` | `orchestrator`, `product`, `product-e2e-gate`, `task-review-gate` |
| `main` | `true` | `orchestrator`, `product`, `product-e2e-gate`, `task-review-gate` |

This confirms the intended strictness delta is live while the context set is
identical. It does not substitute for the parent PR merging: the repository
policy source and application script become durable on `dev` only through the
parent merge flow.

### Parent PR snapshot

At `2026-08-11T11:19:30Z`, PR #688 reported:

- head `e809a0d7bd82f29610525eed649a54c87c035ef7`, matching the Pantheon approved head;
- `orchestrator`, `performance-gate`, `product-e2e-gate`, and `task-review-gate` successful;
- `product` in progress;
- PR open with auto-merge enabled and merge state `BLOCKED` pending the remaining gate.

## Review Observations

1. `branch_policy()` uses a shallow top-level overlay. That matches the current
   flat policy model: `strict` is a top-level scalar and shared contexts remain
   a top-level list. No nested merge is required by this task.
2. The resolved dictionary still contains the declarative `branches` map, but
   `build_payload()` allowlists the GitHub payload fields and does not transmit
   that map. There is no API payload leakage.
3. `main()` resolves the payload inside the branch loop, so `dev` and `main`
   cannot accidentally reuse one precomputed payload.
4. The new shipped-policy test is the key regression guard: changing the
   checked-in dev override back to `true`, dropping it, or changing main's
   default would fail locally.
5. Malformed overlay types are not newly validated. The policy file is a
   trusted repository-owned input, and validation expansion is outside this
   narrow fix; this is non-blocking for the stated acceptance criteria.

No code-level blocker was found within the parent task's accepted scope.

## Reviewer Handoff

### Sidecar reviewer (`Claude`)

- [ ] Confirm this sidecar PR changes only this support packet.
- [ ] Confirm the parent head, PR, test command, and live-policy snapshot are represented accurately.
- [ ] Confirm the packet distinguishes code-level acceptance from the still-pending parent merge/closeout gate.

### Parent owner (`Claude`)

- [ ] Treat this packet as supporting evidence only; absorb any useful findings at the parent's discretion.
- [ ] Wait for every required PR check and the merge queue; do not mark the parent `done` while PR #688 remains open.
- [ ] Re-review the parent if its exact approved head changes before merge.

## Scope Conformance

This sidecar adds only:

`support/sidecars/ODP-ORCH-BRANCH-PROTECTION-PER-BRANCH-STRICT-001/ODP-ORCH-BRANCH-PROTECTION-PER-BRANCH-STRICT-001-SIDECAR-REVIEW.md`

It intentionally does not modify the parent's policy, script, tests, L1
canonical documents, core contracts, registry, runtime, or governance truth.
