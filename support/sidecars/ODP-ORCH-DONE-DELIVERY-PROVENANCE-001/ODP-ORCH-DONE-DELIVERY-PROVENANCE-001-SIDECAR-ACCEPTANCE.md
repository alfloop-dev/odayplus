# ODP-ORCH-DONE-DELIVERY-PROVENANCE-001 acceptance packet

Status: support packet ready for parent-owner review  
Parent task: `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001`  
Sidecar task: `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001-SIDECAR-ACCEPTANCE`  
Prepared by: Antigravity  
Assigned sidecar reviewer: Codex  
Parent owner: Codex7  
Parent reviewer: Codex8

## Scope boundary

This is a support-only acceptance checklist and dependency map. It does not
change `scripts/ai_status.py`, supervisor behavior, task truth, canonical
documents, registry/governance policy, runtime code, or release state. Codex7
decides whether and how to use this packet in the parent review. Nothing here
is approval of the parent task or permission to run `done`.

## Frozen parent snapshot

Observed at `2026-08-02T03:28:54Z`:

- Parent live status: `review`; owner Codex7; reviewer Codex8.
- Task branch: `task/ODP-ORCH-DONE-DELIVERY-PROVENANCE-001`.
- Exact pushed parent HEAD: `bb1ea971ef4da1dd3dbc8144241ba9225fdc91bd`.
- Code and regression-test HEAD: `8363d968d0e0e0b6f6d256ce011edb0e5d68e7f9`.
- Descendant `bb1ea971...` changes only the three Product E2E evidence files
  and binds those results to code HEAD `8363d968...`.
- PR: `#567`, base `dev`, open, mergeable, and `BLOCKED`.
- PR head reported by GitHub equals `bb1ea971...`.
- Completed successful checks: `orchestrator`, `performance-gate`, and
  `product-e2e-gate`.
- Outstanding checks: `product` is in progress and `task-review-gate` is
  pending.
- Parent diff from the then-current `origin/dev` tip contains six paths:
  `scripts/ai_status.py`, `scripts/test_ai_status.py`,
  `.orchestrator/test_supervisor.py`, and three
  `docs/evidence/e2e/*` artifacts.

Any parent commit after `bb1ea971...`, changed PR head, changed base, or new
task-owned diff invalidates this snapshot and requires a fresh packet check.

## Dependency map

| Authority / input | Consumer and recorded output | Required pass condition | Fail-closed coverage |
| --- | --- | --- | --- |
| Lifecycle freeze: task is `review_approved` with immutable `approved_head` | `command_done` and delivery metadata | approved head exists; task-owned checkout HEAD and merged PR `headRefOid` both equal it | missing or moved checkout/PR head is rejected; merge commit cannot substitute for the reviewed branch head |
| Configured artifact repository and local path | `task_delivery_checkout` and delivery repository fields | exactly one checkout owns `task/<TASK-ID>`; configured and Git remote repository slugs agree | zero/multiple task checkouts, unresolved HEAD, or wrong remote repository are rejected |
| Task branch and commit metadata | delivery branch, commit, author, subject, and trailer fields | exact task branch; subject contains task ID; `LLM-Agent`, `Task-ID`, and `Reviewer` match live task metadata | mismatched branch, subject, or trailer metadata is rejected |
| Task-owned checkout status | `git_clean`, dirty count, ignored seed count | no tracked or untracked task-owned changes | only the exact injected guide, status snapshot, and task brief paths may be ignored; every other entry remains dirty |
| GitHub PR provenance | delivery `pull_request` object | PR is `MERGED`; head branch is exact task branch; head SHA is approved head; base is configured target; merge timestamp and merge commit exist | open/closed PR, network failure, moved head, wrong task branch/base, or missing merge facts are rejected |
| Target branch topology | merge target SHA and merge-commit ancestry | PR merge commit is an ancestor of fetched target ref | missing target ref or merge commit outside target is rejected; squash merge is supported without requiring source-head ancestry |
| GitHub check rollup | normalized `ci_checks` and `ci_status=success` | non-empty rollup; every CheckRun completed with `SUCCESS`, `NEUTRAL`, or `SKIPPED`; every StatusContext is `SUCCESS` | empty, malformed, pending, failed, or unknown check shapes are rejected |
| Ephemeral remote task ref | no second closeout authority | merged PR provenance remains authoritative after GitHub deletes the task ref | regression proves `done` does not resolve or require a deleted remote task ref |
| Live canonical writer | status transition, archive, handoff and delivery record | owner invokes the live-root status wrapper only after exact-head approval and merged delivery are proven | no manual JSON/log mutation; local or environment delivery-gate relaxation cannot disable mandatory `done` gates |

## Acceptance checklist

### Packet evidence at exact parent HEAD

- [x] GitHub PR head equals frozen HEAD `bb1ea971...`.
- [x] Frozen parent diff is limited to the three implementation/test paths and
  three Product E2E evidence paths listed above.
- [x] All five parent task commits contain the expected task ID plus
  `LLM-Agent: Codex7`, `Task-ID: ODP-ORCH-DONE-DELIVERY-PROVENANCE-001`,
  and `Reviewer: Codex8` trailers.
- [x] `git diff --check origin/dev...bb1ea971...` passes.
- [x] Ten `DoneDeliveryProvenanceRegressionTests` pass on an archive of exact
  HEAD `bb1ea971...`.
- [x] The focused supervisor freeze regression for unresolved/moved delivery
  checkout state passes on the same archive.
- [x] Regression coverage includes squash topology, wrong PR state/head/task/
  base, missing network or merge proof, red/pending/unknown checks, canonical-
  writer versus task-checkout selection, exact seed exclusions, dirty checkout,
  wrong repository, deleted remote ref, and moved checkout/PR head.

### Conditions still required before parent closeout

- [ ] PR #567 is merged into `dev`; an open PR or enabled auto-merge is not
  delivery proof.
- [ ] `product` completes successfully.
- [ ] `task-review-gate` becomes successful.
- [ ] Codex8 reviews and approves exact HEAD `bb1ea971...`; if the head moves,
  Codex7 requests re-review and refreshes this evidence.
- [ ] Live task truth records `review_approved` and freezes
  `approved_head=bb1ea971...` before `done`.
- [ ] The unique parent task checkout remains at `bb1ea971...` and contains no
  task-owned dirty entries at finalization time.
- [ ] The merged PR reports head branch, head SHA, base, merge timestamp, and
  merge commit exactly as required, and the merge commit is an ancestor of the
  fetched `origin/dev`.
- [ ] Codex7 performs closeout through
  `AI_NAME=Codex7 "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" done ...` only
  after all preceding conditions pass.

## Reviewer replay

Run the focused code checks from an exact checkout/archive of `bb1ea971...`:

```bash
python3 -m unittest scripts.test_ai_status.DoneDeliveryProvenanceRegressionTests
python3 .orchestrator/test_supervisor.py \
  ReviewHeadFreezeTests.test_command_done_fails_closed_when_delivery_checkout_sha_unresolved_or_collector_raises
git diff --check origin/dev...HEAD
```

Before accepting the parent, query PR #567 and verify the head and base have not
moved, every check is terminal green, and the PR is merged. After fetching
`origin/dev`, verify the PR-reported merge commit is an ancestor of that ref.
Do not treat source-head ancestry as mandatory because GitHub squash merges do
not preserve it.

## Independent verification record

The packet preparer materialized `bb1ea971...` with `git archive` into a fresh
temporary directory and ran the focused commands above. Results: 10/10 delivery
provenance tests passed, 1/1 supervisor freeze test passed, and parent diff
check passed. The first attempted unittest invocation was discarded because it
was launched from the sidecar working directory and imported the pre-parent
module, which did not contain the new test class; rerunning from the exact
archive produced the recorded passing result.

## Handoff disposition

The support packet itself is ready for Codex7 review. The frozen evidence
supports the parent implementation's fail-closed design, but the parent is not
yet closeout-ready in this snapshot because PR #567 is open and two required
checks are non-terminal. Parent acceptance and composition remain Codex7's
decision, with exact-head review authority held by Codex8.
