# ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001: roll the reviewed actor-reference guard into both live status roots

Owner: Claude3 · Reviewer: Codex2 · Phase: Fleet Control Plane Live Rollout
Deployed: 2026-07-29T07:19:43Z

Depends on ODP-ORCH-ACTOR-REF-VALIDATION-001, merged as PR #496
(`1d07de67b1b6d75345feb55c2f35e6f39c41817a`).

This task deploys one file and proves it. It changes no product code, restarts
nothing, and edits no task assignment. Receipts live under
`docs/evidence/runtime/ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001/`; the three drivers
that produced them (`deploy.sh`, `sandbox_cli_matrix.py`,
`live_authority_probe.py`) are committed next to their output so the reviewer
can re-run any of it.

## 1. Source: the merge commit, not a working tree

`deploy.sh` never copies from a checkout. It resolves the blob out of the merge
commit and materialises it:

```
git rev-parse 1d07de67:scripts/ai_status.py   -> 73800e2810e4b693f2e940beec582f3a6c27c7ef
git cat-file blob 73800e28... > /tmp/ai_status.merged-1d07de67.py
sha256                                        = 5e19c1c1ef4729f32470956cad3e3fe5972cb92dee5225a5d65db16df074950d
bytes                                         = 203611
```

The task worktree is checked out at exactly that merge commit, and
`git diff --exit-code 1d07de67 -- scripts/ai_status.py` returns 0 — so the
tested source and the deployed source are the same bytes.

Receipt: `runtime/.../source-verification.txt`.

## 2. Verification from the exact source

```
python3 -m pytest scripts/test_ai_status.py                       # 98 passed, 41 subtests
python3 -m ruff check scripts/ai_status.py scripts/test_ai_status.py   # All checks passed!
git diff --exit-code 1d07de67 -- scripts/ai_status.py             # exit 0
git status --porcelain -- scripts/ai_status.py                    # empty
```

Same receipt as above.

## 3. Deployment

| root | role | sha256 before | sha256 after | equals merged |
| --- | --- | --- | --- | --- |
| `/home/lupin/oday-plus` | control root (`run-supervisor.sh`, `dashboard_server.py`) | `32b6cdcd…0340da` | `5e19c1c1…4950d` | **True** |
| `/home/lupin/oday-plus-supervisor-live` | status root the live Supervisor runs from | `216c1a87…66a17b` | `5e19c1c1…4950d` | **True** |

`cmp -s` against the materialised blob reports `identical` for both. File mode
(`775`) is preserved. Both prior files are backed up under
`/home/lupin/pantheon-deploy-backups/ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001-20260729T071943Z/`,
with a one-line rollback recorded in `runtime/.../superseded-delta.txt`.

Unrelated dirty changes are preserved. `git status --porcelain` was captured in
each root before and after; with `scripts/ai_status.py` filtered out the two
inventories are identical — 580 dirty entries in the control root and 21 in the
status root, none of them touched. The status root's count goes 21 → 22 only
because `scripts/ai_status.py` itself was clean before and is now modified
against its ops branch, which is the deployment.

Receipt: `runtime/.../deploy-transcript.txt`.

### What the swap superseded

The status root's previous file was byte-identical to dev commit `1a7c0149`
(2026-07-21) — a clean earlier point on the same lineage, no local edits — so
the only delta is the PR #496 hardening, and its `KNOWN_AGENTS`
`target_workload` values are identical before and after.

The control root's previous file was dev commit `bc212754` (2026-07-09) **plus
18 lines of uncommitted local edits**: `target_workload` tuning and two
`AGENT_ALIASES` entries mapping whole blocker sentences to `Human/Ops`. That
root's HEAD is a squashed snapshot with 580 dirty files, so those edits were
never on a branch. They are superseded by the deployment and preserved in the
backup. Two things make this safe rather than a silent regression, and the
reviewer should check both:

- The control root has **no live Supervisor of its own**. Its
  `.orchestrator/supervisor.pid` names PID 302531, which no longer exists; the
  one running supervisor (PID 1487837) has cwd
  `/home/lupin/oday-plus-supervisor-live` and reads that root's copy. No
  dispatch weighting actually in force changed.
- The two alias entries were the manual workaround for prose landing in
  actor-shaped fields — the defect PR #496 fixes at the source. Neither string
  occurs in either root's `ai-status.json` any more (0 and 0).

Flagged, not fixed here: if that workload tuning is meant to be authoritative it
belongs in `.orchestrator/config.json`, not in a local edit to a tracked file.
That is a separate task.

Receipt: `runtime/.../superseded-delta.txt`.

## 4. Fail-closed CLI matrix — isolated status root only

`sandbox_cli_matrix.py` copies the live status root byte-for-byte into a fresh
`/tmp` sandbox and runs the **real CLI** — all 16 actor-bearing commands
(`ai_status.MUTATING_COMMANDS` minus the declared-actorless `sync`) — twice:
once with a 99-character prose `AI_NAME`, once with the well-shaped but
unregistered `Nessie9`. Arguments are valid enough to clear each command's usage
check, so the only thing that can reject the call is the `AI_NAME` gate.

Result, for the deployed copy in **each** root: 32/32 invocations exit non-zero
with `state=UNCHANGED log=UNCHANGED roster=UNCHANGED current-work=UNCHANGED`.

```
assign      exit=1 state=UNCHANGED log=UNCHANGED roster=UNCHANGED current-work=UNCHANGED
  first line: Invalid AI_NAME: actor reference is 99 characters (max 40); this looks like prose, not an agent name
assign      exit=1 state=UNCHANGED log=UNCHANGED roster=UNCHANGED current-work=UNCHANGED
  first line: Unknown AI_NAME: 'Nessie9' is not a registered agent.
```

The live root is never named as `PANTHEON_STATUS_ROOT` in this matrix. Two
isolation checks close it out: the live fingerprints are printed before and
after, and the count of each bad actor string in the live state/log/current-work
is compared before and after. The `Nessie9` count in
`ai-activity-log.jsonl` is 1 both times — that is prior evidence prose from
PR #496's own receipt, not a write from this run.

Receipts: `runtime/.../fail-closed-cli-live-root.txt`,
`runtime/.../fail-closed-cli-control-root.txt`, plus the pre-deployment
rehearsal `runtime/.../fail-closed-cli-sandbox-rehearsal.txt`.

## 5. Live merged-config authority — read-only

`live_authority_probe.py` loads the **deployed** module from an explicit path,
points it at the live status root, and only reads.

Against `/home/lupin/oday-plus-supervisor-live/scripts/ai_status.py`
(`CONFIG_FILE` + `config.local.json` overlay, 19 configured + 3 non-worker = 22
registered):

```
accept Codex3 -> Codex3   accept Codex6 -> Codex6   accept Codex9 -> Codex9
accept Codex4 -> Codex4   accept Codex7 -> Codex7   accept Human/Ops -> Human/Ops
accept Codex5 -> Codex5   accept Codex8 -> Codex8   accept Orchestrator -> Orchestrator
                                                    accept CodexCoordinator -> CodexCoordinator
reject Gemini   -> Unknown probe: 'Gemini' is not a registered agent.    (in KNOWN_AGENTS: True)
reject Gemini2  -> Unknown probe: 'Gemini2' is not a registered agent.   (in KNOWN_AGENTS: True)
reject Copilot  -> Unknown probe: 'Copilot' is not a registered agent.   (in KNOWN_AGENTS: True)
```

Every accepted name round-trips to its own spelling — the guard resolves, it
does not rewrite. The three rejected names are present in the static
`KNOWN_AGENTS` table and absent from the merged config, which is the behaviour
change PR #496 called out at review.

Live fingerprints (`ai-status.json`, `ai-activity-log.jsonl`,
`current-work.md`, `dashboard-bundle.json`) are identical before and after the
probe.

Receipts: `runtime/.../merged-config-authority-live-root.txt`,
`runtime/.../merged-config-authority-control-root.txt`,
`runtime/.../merged-config-authority-pre-deploy.txt`.

## 6. The six audited Codex5/6/8/9 assignments are unchanged

The probe re-reads live state and compares its Codex5/6/8/9 reference set
against PR #496's audited snapshot
(`runtime/ODP-ORCH-ACTOR-REF-VALIDATION-001/ai-status.after.json`):

```
distinct tasks carrying one : 6
  - ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001
  - ODP-LIVE-RUNTIME-DEV-COMPOSE-001
  - ODP-ORCH-CLAUDE-SESSION-LIMIT-REVIEW-001
  - ODP-P10-DEV-REDEPLOY-VERIFY-001
  - ODP-PRODUCTION-MODEL-REGISTRY-001
  - ODP-RUNTIME-GCP-001
total references            : 18   (9 owner/reviewer fields, 4 blocker fields, 5 handoff fields)
baseline references         : 18
identical multiset          : True
missing from live           : []
added since audit           : []
```

Each of the 18 references also resolves to its own spelling under the deployed
guard (`exact=True`), so nothing would be re-attributed on the next write.

## 7. Supervisor left running, no restart

| | before deploy | after deploy |
| --- | --- | --- |
| `MainPID` | 1487837 | 1487837 |
| `ActiveState/SubState` | active/running | active/running |
| `ExecMainStartTimestamp` | Wed 2026-07-29 06:08:57 UTC | Wed 2026-07-29 06:08:57 UTC |
| `NRestarts` | 0 | 0 |

No `systemctl start/stop/restart/reload` was issued. The journal shows the same
PID ticking on both sides of the 07:19:43Z swap (07:19:14 and 07:20:35), and a
read-only `show` against the deployed live-root CLI exits 0.

Receipt: `runtime/.../supervisor-continuity.txt`.

## 8. Operational note for the reviewer: worker worktrees need the gitignored config

`.orchestrator/config.json` and `.orchestrator/config.local.json` are gitignored
on this fleet, so a freshly seeded worker worktree has neither. Under the
merged guard the merged config is the *only* authority, so in a bare worktree
`registered_agent_names()` collapses to whatever the status-root overlay
declares — here `Codex3`-`Codex9` plus the three non-worker actors — and every
other worker's own `AI_NAME` is rejected:

```
$ AI_NAME=Claude3 ./scripts/ai-status.sh start ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001 ...
Unknown AI_NAME: 'Claude3' is not a registered agent.
  registered: Codex3, Codex4, ..., CodexCoordinator, Human/Ops, Orchestrator
```

This is the guard failing closed, not a defect introduced by the deployment —
and it is already in force for every worktree seeded from `dev` since #496
merged, independently of this rollout. The existing remedy is the documented
one: copy the two gitignored config files into the worktree (this task did),
or set `AI_STATUS_EXTRA_AGENTS`. Worth a follow-up so worktree seeding does it
automatically rather than leaving each worker to discover it.

A second, smaller instance of the same shape: the control root's
`.orchestrator/config.json` is an older copy that does not declare `Claude3`
(18 configured agents vs the status root's 19). Commands run through the control
root's CLI would reject `AI_NAME=Claude3`. Nothing dispatches from there today,
but the two configs have drifted.

## 9. Scope

Changed: `scripts/ai_status.py` in two live roots (byte-for-byte to the merged
blob), plus this task's evidence in the repo. Not changed: product code,
`ai_status.py` content, the Supervisor process, config files, task
assignments, roster, or any other dirty file in either root.
