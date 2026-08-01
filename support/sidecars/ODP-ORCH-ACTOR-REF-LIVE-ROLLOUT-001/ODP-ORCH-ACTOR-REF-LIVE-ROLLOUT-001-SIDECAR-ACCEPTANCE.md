# ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001 acceptance packet

Packet task: `ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001-SIDECAR-ACCEPTANCE`

Parent task: `ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001`

Packet owner / reviewer: Codex4 / Antigravity

Parent owner / reviewer at packet preparation: Antigravity / Codex2

Prepared: 2026-08-01

## Scope and disposition

This is a support-only index over already committed parent evidence. It does
not change canonical truth, runtime code, either live status root, Supervisor
state, task assignments, or the parent task's approval state.

The committed evidence supports all technical rollout acceptance items at the
final parent implementation head `08f045b242f4ce42908e8175778bc40de29e25c9`.
The packet is **ready for parent-owner consumption, but is not a parent GO**:
the task-scoped state available while preparing this packet contains Codex2's
rejection of the earlier `01d84fc8` head and no independent Codex2 approval of
the final `08f045b2` head. That exact-head review remains open.

## Dependency map

```text
ODP-ORCH-ACTOR-REF-VALIDATION-001
  PR #496 merge 1d07de67
  scripts/ai_status.py SHA-256 5e19c1c1...4950d
                    |
                    v
ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001
  01d84fc8  round 1: rejected (non-atomic target write)
       |
  afde1048  round 2: rejected (Supervisor continuity not fail-closed)
       |
  08f045b2  round 3: atomic publish + fail-closed continuity gate
       |
  PR #499 merge 427e3290 -> dev
       |
       +--> /home/lupin/oday-plus/scripts/ai_status.py
       +--> /home/lupin/oday-plus-supervisor-live/scripts/ai_status.py
       |
       +--> exact-source tests and lint
       +--> isolated bad-actor CLI matrices
       +--> live read-only authority and assignment probes
       +--> Supervisor continuity and negative gate self-test
                    |
                    v
        Codex2 exact-head/live-state review (OPEN in task state)
                    |
                    v
        Parent owner decides review/closeout

This sidecar packet -> Antigravity packet review -> optional parent absorption
```

| Dependency | Pinned input or output | Why it matters | Evidence |
| --- | --- | --- | --- |
| Validation task | PR #496 merge `1d07de67b1b6d75345feb55c2f35e6f39c41817a` | Defines the only permitted payload bytes | `docs/evidence/fleet_dispatch/ODP-ORCH-ACTOR-REF-VALIDATION-001.md` |
| Source blob | SHA-256 `5e19c1c1ef4729f32470956cad3e3fe5972cb92dee5225a5d65db16df074950d` | Connects reviewed source, tests, and both deployed targets | `docs/evidence/runtime/ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001/source-verification.txt` |
| Final rollout candidate | `08f045b242f4ce42908e8175778bc40de29e25c9` | Supersedes both rejected procedural implementations | Parent dispatch evidence and runtime receipts listed below |
| Repository integration | PR #499 merge `427e32909c38339b127753c0bba0e9beaf7670be` | Makes the final evidence and harness durable on `dev` | Git ancestry from `08f045b2` to `427e3290` |
| Live targets | Control root and supervisor-live root | Both must receive identical reviewed bytes without disturbing unrelated changes | `deploy-transcript.txt`, `superseded-delta.txt` |
| Runtime continuity | `pantheon-supervisor.service` | Rollout must neither restart nor leave the Supervisor unhealthy | `supervisor-continuity.txt`, `supervisor-gate-selftest.txt` |
| Independent acceptance | Codex2 review of the exact final head and live state | Mandatory parent closeout gate; sidecar review cannot substitute for it | Parent task state / handoff history |

## Acceptance checklist

Status meanings:

- **EVIDENCED**: the committed final-round receipt explicitly reports the
  required result.
- **OPEN**: a required authority action is not present in the task-scoped state
  used to prepare this packet.

| # | Parent acceptance criterion | Status | Receipt and reviewer check |
| --- | --- | --- | --- |
| 1 | Use the exact PR #496 merge commit as source | **EVIDENCED** | `source-verification.txt` pins merge `1d07de67`, blob `73800e28...`, 203611 bytes, and SHA-256 `5e19c1c1...4950d`. |
| 2 | Deploy byte-for-byte to both live roots while preserving unrelated dirty changes | **EVIDENCED** | `deploy-transcript.txt` reports verified same-directory siblings, `os.replace`, mode preservation, changed inodes, no leftover sibling, and PASS. `superseded-delta.txt` records the preserved dirty inventories and rollback boundary. |
| 3 | Prove both deployed hashes equal the merged file | **EVIDENCED** | `deploy-transcript.txt` records `5e19c1c1...4950d` for both targets and the merged source, with byte-for-byte comparison PASS. |
| 4 | Run focused tests, Ruff, and git diff checks from the exact source | **EVIDENCED** | `source-verification.txt`: 98 tests and 41 subtests passed, Ruff passed, source diff exited 0, and `git diff --check` passed. |
| 5 | Run malformed and unregistered `AI_NAME` CLI matrix only in isolated status-root copies with no mutation | **EVIDENCED** | `fail-closed-cli-live-root.txt` and `fail-closed-cli-control-root.txt`: every actor-bearing command rejected both bad actor classes; state, log, roster, and current-work remained unchanged. The live roots were fingerprinted only. |
| 6 | Resolve Codex3-Codex9 and non-worker actors exactly; reject Gemini, Gemini2, and Copilot | **EVIDENCED** | Both `merged-config-authority-*.txt` receipts report exact accepted spellings, all three required rejections, unchanged live fingerprints, and `RESULT: PASS`. |
| 7 | Prove the audited Codex5/6/8/9 assignments remain byte-for-byte unchanged | **EVIDENCED** | Both authority receipts report 6 distinct tasks, 18 total references, `identical multiset: True`, no missing or added references, and exact resolution for all 18. |
| 8 | Leave the Supervisor active/running without restart; record before and after | **EVIDENCED** | `supervisor-continuity.txt` pins loaded, active/running, PID 1487837, unchanged start timestamp, and `NRestarts=0`. `supervisor-gate-selftest.txt` proves 11/11 positive and negative scenarios, including the reviewer-reproduced death/restart case. |
| 9 | Independent Codex2 exact-head and live-state review | **OPEN** | The available task state records rejection of `01d84fc8`, followed by owner reassignment, but no approval of final head `08f045b2`. Antigravity should request or confirm Codex2 review against that exact head before parent closeout. |

## Superseded rounds and review boundary

- `01d84fc8` is not an acceptable candidate even though its deployed bytes and
  outcome checks passed: it wrote directly to each target instead of publishing
  a verified sibling by atomic rename.
- `afde1048` is not an acceptable candidate even though it added atomic
  publication: its continuity check could report PASS when the unit became
  inactive/dead and `NRestarts` increased.
- Only `08f045b2` combines atomic publication with fail-closed Supervisor
  preflight and continuity checks. Review must pin this exact head, not infer
  approval from either earlier round or from PR #499 merely being merged.

The receipts describe a historical rollout snapshot from 2026-07-29. If a
reviewer requires present-time live confirmation, it should be a fresh,
read-only comparison against the intended current source lineage. Later
legitimate deployments can change current hashes or PID values and should not
retroactively be treated as failure of the recorded rollout.

## Operational follow-ups, not absorbed here

The parent evidence flags two configuration drifts:

1. freshly seeded worker worktrees may lack the gitignored merged Supervisor
   configuration and therefore reject an otherwise valid worker `AI_NAME`;
2. the inactive control root's config is older than the supervisor-live root's
   config and does not declare Claude3.

Neither issue is repaired or reclassified by this sidecar. They require a
separate owner and scope if the parent owner decides they are actionable.

## Reviewer fast path

The packet preparer ran these read-only repository checks from the sidecar task
branch:

```bash
git cat-file -t 1d07de67b1b6d75345feb55c2f35e6f39c41817a
git cat-file -t 08f045b242f4ce42908e8175778bc40de29e25c9
git cat-file -t 427e32909c38339b127753c0bba0e9beaf7670be
git show 1d07de67b1b6d75345feb55c2f35e6f39c41817a:scripts/ai_status.py | sha256sum
git merge-base --is-ancestor 1d07de67b1b6d75345feb55c2f35e6f39c41817a 08f045b242f4ce42908e8175778bc40de29e25c9
git merge-base --is-ancestor 08f045b242f4ce42908e8175778bc40de29e25c9 427e32909c38339b127753c0bba0e9beaf7670be
git merge-base --is-ancestor 427e32909c38339b127753c0bba0e9beaf7670be HEAD
git diff --check 1d07de67b1b6d75345feb55c2f35e6f39c41817a..08f045b242f4ce42908e8175778bc40de29e25c9
```

Results: all three objects are commits; all ancestry checks and the diff check
exit 0; the historical source hash is exactly
`5e19c1c1ef4729f32470956cad3e3fe5972cb92dee5225a5d65db16df074950d`;
and all eight primary receipts named in the checklist are present and nonempty.

## Handoff

Antigravity should review this packet for accurate indexing and decide whether
to absorb it into the parent review flow. For the parent task, the next safe
action is to obtain Codex2's independent acceptance of exact final head
`08f045b2` and the required live-state evidence. This packet itself grants no
deployment, merge, approval, or closeout authority.
