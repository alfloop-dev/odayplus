# ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001 acceptance packet

- Status: **NOT ACCEPTED** — parent is `in_progress` with CHANGES REQUESTED at the current review gate SHA
- Parent task: `ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001`
- Sidecar task: `ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001-SIDECAR-ACCEPTANCE`
- Helper kind: `acceptance_packet`
- Prepared by: Claude
- Assigned sidecar reviewer: Antigravity3
- Parent owner: Antigravity3 · Parent reviewer: Claude3
- Parent PR: [#579](https://github.com/alfloop-dev/odayplus/pull/579), base `dev`, state `OPEN`, merge state `BLOCKED`
- Reviewed parent HEAD (`review_gate_sha`): `ebadeab0d24ce19e9d37775dca3429dff70ae83d`
- Last approved head (superseded by reopen): `dbe0f10cb9f9d194ac32cabbac402c040d30451a`
- Evidence captured: `2026-08-10` UTC

## Scope boundary

This is a support-only acceptance checklist and dependency map. It does not modify
`.orchestrator/github_bus.py`, `.orchestrator/test_github_bus.py`, supervisor behaviour, task truth,
canonical documents, registry/governance policy, or release state. Parent acceptance is explicitly
**NOT claimed**. Parent owner Antigravity3 and parent reviewer Claude3 decide remediation and
re-review; this packet only makes the exit conditions checkable.

## Supersession notice

The sibling packet `ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001-SIDECAR-REVIEW.md` recommends
**APPROVE** at parent HEAD `dbe0f10c` (captured `2026-08-05`). That recommendation is **stale**.
The parent has since advanced to `ebadeab0` and was reopened to `in_progress` by reviewer Claude3
on `2026-08-10T13:19:39Z` with one blocking finding. Read that packet as a historical record of the
`dbe0f10c` surface only; this packet is authoritative for the current gate SHA.

## Current parent snapshot

| Field | Value |
| --- | --- |
| Live task status | `in_progress` (reopened by `Claude3`) |
| Task branch | `task/ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001` |
| Commits ahead of `dev` | `23ef0374` (anchor origin ref), `ebadeab0` (test recovery) |
| Merge base with `dev` | `d37e6e5cfae0a4c936b121b363906a17739d293c` |
| Change surface | `.orchestrator/github_bus.py` (+55/−…), `.orchestrator/test_github_bus.py` (+214/−…); 2 files, 247 insertions, 22 deletions |
| Review verdict | CHANGES REQUESTED (PR #579 comment `5240841006`) |
| CI at gate SHA | `task-review-gate` **fail** ("Review rejected or reopened. Task status is in_progress"); `orchestrator`, `performance-gate`, `product`, `product-e2e-gate` **pass** |
| Upstream provenance | `source_task_id`: `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001` · `repro_task_id`: `ODP-API-HEALTH-DATA-MODE-CONTRACT-001` |
| Declared dependencies | none |

The red `task-review-gate` is a *consequence* of the reopen, not an independent defect. It clears
when the task returns to `review`/`review_approved`; it is not a separate remediation item.

## Dependency map

Authority chain the parent change sits on. Each row is a place where wrong provenance produces a
wrong review-PR decision.

| Authority / input | Consumer in `github_bus.py` | Required pass condition | Fail-closed coverage at `ebadeab0` |
| --- | --- | --- | --- |
| Origin ref namespace (`git ls-remote --heads`) | `remote_branch_head_sha` (`:307`) | exact `refs/heads/<branch>` match; SHA matches `[0-9a-f]{40,64}` | Covered — `test_remote_branch_head_sha_requires_exact_origin_ref` rejects the `<branch>-SIDECAR` prefix sibling |
| Cached remote head snapshot | `remote_branch_heads` (`:385`) → TTL + max-stale window | snapshot fresh, or within stale tolerance; timeout kills the process group | Covered for the snapshot path; **not** covered for the new per-branch fallback (see item 2) |
| Network-git execution guard | `run_git_network_process` (`:467`) | hard timeout, `start_new_session`, `killpg` on expiry | **Bypassed** by the `ls-remote` fallback at `:314`, which uses `common.run_command` (`timeout=None`) |
| Task branch candidate resolution | `review_branch_for_task` (`:647`) | origin ref decides before local checkout state | Covered — `remote_branch_exists(candidate) or branch_exists(candidate)` on all three candidate paths |
| Published-branch existence | `upsert_review_pr` unpublished gate (`:1029`) | live origin recheck every cycle, no TTL suppression of a real push | Covered — recheck tests now assert `remote_branch_head_sha` is called once per cycle |
| Remote head SHA → local object store | `branch_has_diff(base, head_sha)` (`:1080`) | head must be resolvable in the same namespace as base | **Not satisfied** — a raw SHA cannot resolve in ref-pairs 1–2, and pair 3 needs the object fetched locally (see item 1) |
| Review sync idempotency hash | `pr_hash` / `last_review_hash` (`:1076`–`:1077`) | hash must change when the underlying fact changes, so a wrong skip self-heals | **Not satisfied** — `head_sha` is now fetch-independent, so the skip is byte-identical forever; only the operator `recheck` verb (`:1498`) clears it |
| Durable audit record | `bus_state[...]["review_pr"]` | records `branch`, `head_sha`, `remote_ref` | Covered — `remote_ref` = `refs/heads/task/<TASK-ID>` persisted alongside remote `head_sha` |

## Acceptance checklist — declared criteria

Parent acceptance criteria as recorded in `ai-status.json`:

- [x] **Criterion 1** — "review PR branch selection resolves `task/ODP` task ref independently of live status-root HEAD".
  `review_branch_for_task` (`:647`) now tries `remote_branch_exists` before `branch_exists` for every
  candidate, including the agent-branch and current-branch fallbacks. Covered by
  `test_upsert_review_pr_uses_task_origin_ref_when_status_root_and_owner_branch_differ`.
- [~] **Criterion 2** — "remote branch existence and exact SHA are corroborated from origin ref".
  Met for *existence*. **Not met for consumption**: the corroborated SHA is passed straight into
  `branch_has_diff`, which cannot use it. See remediation item 1.
- [x] **Criterion 3** — "false `skipped_unpublished_branch` repro has a regression test".
  `test_upsert_review_pr_recovers_false_unpublished_state_from_task_origin_ref` covers recovery, and
  the two TTL tests were rewritten to assert a live per-cycle origin recheck.

**Overall: NOT ACCEPTED.** One of three criteria is partially met, and the gap converts the defect
class this task exists to remove into a new, more durable instance of the same class.

## Remediation checklist — exit conditions for re-review

Derived from PR #579 comment `5240841006`. Each item is written so the parent owner can mark it
done against an observable check.

### Item 1 — BLOCKING · remote SHA fed into a branch-name-only diff

- [ ] `upsert_review_pr` no longer treats "remote head SHA not resolvable locally" as *no commits*.
      Either confirm resolvability first (`git cat-file -e <sha>^{commit}`) and treat the unknown case
      as **unknown**, or fetch the ref before diffing.
- [ ] When the answer is unknown, neither `state: skipped_no_commits` nor `last_review_hash` is
      persisted, so the next poll retries live.
- [ ] A regression test exercises the **real** `branch_has_diff` with an unresolvable head SHA and
      asserts the skip is not persisted. Every existing `upsert_review_pr` test mocks
      `branch_has_diff`, so this path currently has zero coverage.
- [ ] Repro below flips: the published-but-unfetched case must not stay `skipped_no_commits` across
      cycles with `gh_calls=0`.

### Item 2 — NON-BLOCKING · `ls-remote` fallback bypasses the network guard

- [ ] `remote_branch_head_sha` (`:314`) routes its `git ls-remote --heads origin refs/heads/<branch>`
      call through `run_git_network_process(..., timeout_seconds=git_network_timeout_seconds())`.
- [ ] Blast radius is understood: `remote_branch_exists` is the first check for each of ~3 candidate
      branches per review task, and the fallback fires exactly when the branch is missing from the
      cached snapshot — the unpublished-branch case. An unreachable or credential-prompting origin
      can hang the whole bus poll under `common.run_command`, whose `timeout` defaults to `None`.

### Item 3 — MINOR · new unit test performs a live network call

- [ ] `test_remote_branch_head_sha_requires_exact_origin_ref` mocks `remote_branch_heads` (or
      `run_git_network_process`) to return `{}`. `setUp` clears the snapshot cache, so today the test
      makes a real `ls-remote` against origin before reaching the mocked fallback; offline CI pays the
      full `git_network_timeout_seconds` on every run.

### Closeout gate

- [ ] All three items landed in one pass, re-reviewed by Claude3, task back to `review_approved`,
      `task-review-gate` green, PR #579 merged into `dev`, then owner runs `done`.

## Independent verification record

Run by the packet preparer on `2026-08-10`. The parent tree was extracted read-only with
`git archive origin/task/ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001 .orchestrator` into a scratch
directory — no git worktree was added for the parent branch, so the parent's single-checkout
delivery gate is untouched.

```bash
# 1. Parent test suite at ebadeab0 (extracted tree)
python3 -m pytest .orchestrator/test_github_bus.py -q
# -> 68 passed, 3 subtests passed in 0.59s

# 2. branch_has_diff ref-pair probe with a raw 40-char SHA absent from the object store
git rev-list --count refs/remotes/origin/dev..refs/remotes/origin/deadbeef...  # rc=128
git rev-list --count origin/dev..origin/deadbeef...                            # rc=128
git rev-list --count dev..deadbeef...                                          # rc=128
git rev-list --count dev..ebadeab0d24ce19e9d37775dca3429dff70ae83d             # rc=0, 827
```

The 68/68 pass confirms the suite is green at the gate SHA — the blocking defect is a **coverage
gap**, not a failing test. The probe confirms all three ref-pairs fail for an unfetched SHA while the
third pair succeeds once the object is local, which is exactly the fetch-dependence the change was
meant to remove.

Third, `upsert_review_pr` was driven for three cycles with the real `branch_has_diff` (only
`remote_branch_head_sha`, `run_gh`, and the local-branch probes stubbed), against the extracted
parent module:

```
=== published-but-unfetched remote SHA (head_sha=deadbeef...) ===
cycle 1: changed=True  state=skipped_no_commits  gh_calls=0
cycle 2: changed=False state=skipped_no_commits  gh_calls=0
cycle 3: changed=False state=skipped_no_commits  gh_calls=0

=== control: remote SHA already fetched locally (head_sha=ebadeab0...) ===
cycle 1: changed=True  state=open                gh_calls=1
cycle 2: changed=False state=open                gh_calls=1
cycle 3: changed=False state=open                gh_calls=1
```

This independently reproduces the reviewer's finding and adds the control arm: the review PR is
created normally once the SHA is a local object, and is never created — with zero `gh` calls — while
it is not. The regression framing also checks out against `origin/dev`: pre-change,
`head_sha = branch_head_sha(branch)` read local refs, so `pr_hash` changed as soon as the fetch
landed and the skip self-healed. Post-change, `head_sha = remote_head_sha` is fetch-independent, so
the hash is stable and the skip is permanent.

## Reviewer replay

1. Read PR #579 comment `5240841006` (reviewer Claude3, `2026-08-10T13:19:39Z`) for the verdict of
   record, and confirm `ai-status.json` shows the parent as `in_progress` with
   `review_gate_sha = ebadeab0`.
2. Confirm `last_approved_head` (`dbe0f10c`) no longer matches the gate SHA — this is why the sibling
   SIDECAR-REVIEW packet's APPROVE is stale.
3. Extract the parent tree with `git archive` (not `git worktree add` — a second checkout of
   `task/ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001` would break the parent's delivery gate) and
   re-run `python3 -m pytest .orchestrator/test_github_bus.py -q`; expect 68 passed.
4. Re-run the ref-pair probe and the three-cycle repro above; expect `skipped_no_commits` to stick
   with `gh_calls=0` for the unfetched SHA and `open` with `gh_calls=1` for the fetched control.
5. Verify this sidecar branch's diff against `origin/dev` contains only
   `support/sidecars/ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001/ODP-ORCH-REVIEW-PR-ORIGIN-REF-PROVENANCE-001-SIDECAR-ACCEPTANCE.md`
   and that `git diff --check` is clean.

## Handoff disposition

Acceptance is **withheld**. The parent needs one remediation pass covering items 1–3, then
re-review by Claude3 at a new gate SHA. Handed off to sidecar reviewer Antigravity3, who is also the
parent owner and therefore the actor for the remediation pass. Parent owner Antigravity3 and parent
reviewer Claude3 decide whether to absorb this checklist into the mainline task record.
