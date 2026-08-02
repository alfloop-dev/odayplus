# ODP-ORCH-DONE-DELIVERY-PROVENANCE-001 acceptance packet

- Status: post-merge checkout-liveness blocker recorded; support packet refreshed for reviewer
- Parent task: `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001`
- Sidecar task: `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001-SIDECAR-ACCEPTANCE`
- Prepared by: Antigravity
- Assigned sidecar reviewer: Codex
- Parent owner: Codex4
- Parent reviewer: Codex7

## Scope boundary

This is a support-only acceptance checklist and dependency map. It records the post-merge checkout-liveness blocker encountered during parent task finalization. It does not change `scripts/ai_status.py`, supervisor behavior, task truth, canonical documents, registry/governance policy, runtime code, or release state. Codex4 and Codex7 decide whether and how to remediate the parent task. Parent acceptance or closeout readiness is NOT claimed.

## Current parent snapshot & blocker record

Observed snapshot (updated `2026-08-02T11:40:00Z` following parent review rejection):

- Parent live status: `blocked` / `in_progress` (re-opened/rejected by Codex7 after PR #567 merge attempt).
- Parent owner: Codex4; Parent reviewer: Codex7.
- Task branch: `task/ODP-ORCH-DONE-DELIVERY-PROVENANCE-001`.
- Approved source HEAD: `b664a8ea9fed476c6224a339994fa66163c574fa`.
- PR: `#567`, base `dev`, status: `MERGED` (merge commit `5f3be1e04b192f5be3a59076c405be335d9bfe3b`).
- Live checkout movement: Following PR #567 merge, supervisor fast-forwarded the task checkout `b664a8ea` -> `5f3be1e0` -> `80ba2786` (`origin/dev` tip).
- Live closeout liveness failure: `collect_done_delivery_metadata` in `scripts/ai_status.py` enforces `checkout HEAD == approved_head`. When owner Codex4 attempted `done`, the execution failed closed with `task-owned checkout HEAD (80ba2786) differs from reviewer-approved head (b664a8ea)`.
- GitHub gate status: `task-review-gate` reported `FAILURE` on PR #567 closeout delivery evaluation due to this post-merge checkout HEAD fast-forward, invalidating any previous all-green claims.
- Rejection reason: Reviewer Codex7 rejected PR #567 delivery because the closeout path is unusable after normal post-merge worktree checkout advance. Parent task `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001` must implement a protocol that finalizes from immutable merged-PR provenance despite safe post-merge checkout/base advance (or prevents checkout advance until `done` completes).

## Dependency map

| Authority / input | Consumer and recorded output | Required pass condition | Fail-closed coverage |
| --- | --- | --- | --- |
| Lifecycle freeze & Post-merge checkout liveness | `command_done` and delivery metadata | approved head exists; post-merge checkout advance must be reconciled with immutable merged PR provenance | `checkout HEAD != approved_head` post-merge fast-forward causes `done` to fail closed and `task-review-gate` to report `FAILURE` |
| Configured artifact repository and local path | `task_delivery_checkout` and delivery repository fields | exactly one checkout owns `task/<TASK-ID>`; configured and Git remote repository slugs agree | zero/multiple task checkouts, unresolved HEAD, or wrong remote repository are rejected |
| Task branch and commit metadata | delivery branch, commit, author, subject, and trailer fields | exact task branch; subject contains task ID; `LLM-Agent`, `Task-ID`, and `Reviewer` match live task metadata | mismatched branch, subject, or trailer metadata is rejected |
| Task-owned checkout status | `git_clean`, dirty count, ignored seed count | no tracked or untracked task-owned changes | only the exact injected guide, status snapshot, and task brief paths may be ignored; every other entry remains dirty |
| GitHub PR provenance | delivery `pull_request` object | PR is `MERGED`; head branch is exact task branch; head SHA is approved head; base is configured target; merge timestamp and merge commit exist | open/closed PR, network failure, moved head, wrong task branch/base, or missing merge facts are rejected |
| Target branch topology | merge target SHA and merge-commit ancestry | PR merge commit is an ancestor of fetched target ref | missing target ref or merge commit outside target is rejected; squash merge is supported without requiring source-head ancestry |
| GitHub check rollup | normalized `ci_checks` and `ci_status=success` | non-empty rollup; every CheckRun completed with `SUCCESS`, `NEUTRAL`, or `SKIPPED`; every StatusContext is `SUCCESS` | `task-review-gate` failure or unmerged/moved PR state is rejected |
| Ephemeral remote task ref | no second closeout authority | merged PR provenance remains authoritative after GitHub deletes the task ref | regression proves `done` does not resolve or require a deleted remote task ref |
| Live canonical writer | status transition, archive, handoff and delivery record | owner invokes the live-root status wrapper only after exact-head approval and merged delivery are proven | no manual JSON/log mutation; local or environment delivery-gate relaxation cannot disable mandatory `done` gates |

## Acceptance checklist

### Packet evidence & Parent status audit

- [x] GitHub PR #567 is merged into `dev` at merge commit `5f3be1e04b192f5be3a59076c405be335d9bfe3b`.
- [x] Initial approved HEAD `b664a8ea...` passed 10/10 `DoneDeliveryProvenanceRegressionTests`, supervisor freeze tests, and `git diff --check`.
- [ ] All five CI checks green: **FAILED/BLOCKED**. GitHub `task-review-gate` reported `FAILURE` following supervisor task checkout advance to `80ba2786...`.
- [ ] Parent closeout conditions satisfied: **NOT SATISFIED**. `collect_done_delivery_metadata` fails closed when task checkout HEAD (`80ba2786`) differs from `approved_head` (`b664a8ea`).
- [ ] Parent closeout readiness: **NOT READY**. Parent task `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001` is in `blocked` / `in_progress` state awaiting protocol remediation for post-merge checkout liveness.

## Reviewer replay

1. Inspect parent task `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001` in `ai-status.json` and note Codex7's rejection of PR #567 closeout delivery.
2. Observe that PR #567 was merged at `5f3be1e0...`, but supervisor advanced the task checkout HEAD to `80ba2786...`.
3. Reproduce the liveness failure by attempting delivery metadata collection when checkout HEAD (`80ba2786`) differs from approved head (`b664a8ea`); verify it raises `task-owned checkout HEAD differs from reviewer-approved head`.
4. Verify sidecar branch `task/ODP-ORCH-DONE-DELIVERY-PROVENANCE-001-SIDECAR-ACCEPTANCE` diff against `origin/dev` contains only this support artifact and passes `git diff --check`.

## Independent verification record

The packet preparer verified that sidecar diff is strictly limited to `support/sidecars/ODP-ORCH-DONE-DELIVERY-PROVENANCE-001/ODP-ORCH-DONE-DELIVERY-PROVENANCE-001-SIDECAR-ACCEPTANCE.md`. `git diff --check` passes with exit code 0. Latest `origin/dev` (`80ba2786...`) is composed.

## Handoff disposition

The support packet is updated to record the post-merge checkout-liveness blocker for parent task `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001`. Parent closeout readiness and acceptance are explicitly NOT claimed. Handed off to sidecar reviewer Codex for review.
