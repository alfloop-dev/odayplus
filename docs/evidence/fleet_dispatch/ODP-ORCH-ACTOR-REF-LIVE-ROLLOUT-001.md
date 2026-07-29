# ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001: roll the reviewed actor-reference guard into both live status roots

Owner: Claude3 · Reviewer: Codex2 · Phase: Fleet Control Plane Live Rollout
Deployed: 2026-07-29T07:49:55Z (gated atomic publish; supersedes the 07:38:12Z
and 07:19:43Z runs)

Depends on ODP-ORCH-ACTOR-REF-VALIDATION-001, merged as PR #496
(`1d07de67b1b6d75345feb55c2f35e6f39c41817a`).

This task deploys one file and proves it. It changes no product code, restarts
nothing, and edits no task assignment. Receipts live under
`docs/evidence/runtime/ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001/`; the four drivers
that produced them (`deploy.py`, `supervisor_gate_selftest.py`,
`sandbox_cli_matrix.py`, `live_authority_probe.py`) are committed next to their
output so the reviewer can re-run any of it.

## 0. What round 3 changed, and why

Round 2 was rejected at exact head `afde1048`, and the finding was not about the
bytes. `deploy.py` printed `ActiveState`, `SubState` and `NRestarts` but only
*failed* the run on `MainPID` / `ExecMainStartTimestamp` changes. The reviewer
put a fake `systemctl` in front
of the driver, made systemd report `inactive/dead` with `NRestarts` 0→1, and
the driver still answered `RESULT: PASS`, rc 0. The live outcome happened to be
good; the procedure that was supposed to guarantee it was not fail-closed.

Round 3 fixes the executable gate:

- **Preflight**, evaluated *before any target is touched*: the unit must be
  `loaded` and `active/running`, `MainPID` must be a live PID whose `/proc`
  cmdline is the Supervisor, and `ExecMainStartTimestamp` / `NRestarts` must be
  readable. Otherwise the driver exits **rc 2** with every target byte-for-byte
  untouched.
- **Continuity**, after the last publish: the unit must still be
  `active/running`, and `MainPID`, `ExecMainStartTimestamp` **and** `NRestarts`
  must each be *exactly* equal to the preflight reading. Any drift fails the run.
- A failed or unparseable `systemctl` query is **fatal in both positions** —
  there is no `<unavailable>` sentinel left that could compare equal to itself.
  Both snapshots are now a single `systemctl show` call, so BEFORE and AFTER are
  each one consistent read rather than five raced ones.
- The `--corrupt-payload` rehearsal path is gated too: a supervisor failure
  cannot be laundered by the payload gate firing "successfully".

The proof is executable, not narrative: `supervisor_gate_selftest.py` drives the
real `deploy.py` under a scripted fake `systemctl` against throwaway git roots
(§7a). It reproduces the reviewer's exact probe and asserts the driver refuses.

The merged blob was then re-published to both live roots through the gated path
at 07:49:55Z, and every live receipt in this document was regenerated against
the inodes that run created.

## 0b. What round 2 changed, and why

Round 1 was rejected at exact head `01d84fc8`. The bytes it landed were correct
and every live outcome check passed, but the procedure violated the reviewer's
stop gate: `deploy.sh` line 62 ran `install -m "$MODE" "$SRC" "$TARGET"`, which
truncates and rewrites the live target in place. A reader that opens the file
mid-write sees a partial file. The gate required a verified same-directory
temporary sibling published by atomic rename.

Round 2 replaces the driver, not the payload:

- `deploy.sh` is **deleted**. `deploy.py` replaces it and is the only driver.
- Every target is now written to a same-directory sibling
  (`.ai_status.py.<TASK-ID>.<pid>.tmp`, `O_CREAT|O_EXCL`), `fsync`ed, `chmod`ed
  to the target's own existing mode, then **verified** — same directory, same
  filesystem, sha256 == merged blob, byte length, mode, and a `filecmp`
  byte-for-byte compare — and only then published with
  `os.replace(sibling, target)` followed by an `fsync` of the directory.
  Any failed check unlinks the sibling and aborts with the target untouched.
- The merged blob was re-deployed to both roots through that path at
  07:38:12Z, with fresh before/after receipts.

The round-1 transcript is kept verbatim as
`runtime/.../deploy-transcript-round1-superseded.txt`.

## 1. Source: the merge commit, not a working tree

`deploy.py` never copies from a checkout. It resolves the blob out of the merge
commit and holds the bytes in memory:

```
git rev-parse 1d07de67:scripts/ai_status.py   -> 73800e2810e4b693f2e940beec582f3a6c27c7ef
git cat-file blob 73800e28...                 -> payload
sha256                                        = 5e19c1c1ef4729f32470956cad3e3fe5972cb92dee5225a5d65db16df074950d
bytes                                         = 203611
```

The task branch adds nothing under `scripts/`
(`git diff --stat 1d07de67..HEAD -- scripts/` is empty) and
`git diff --exit-code 1d07de67 -- scripts/ai_status.py` returns 0 — so the
tested source and the deployed source are the same bytes.

Receipt: `runtime/.../source-verification.txt`.

## 2. Verification from the exact source

```
python3 -m pytest scripts/test_ai_status.py                       # 98 passed, 41 subtests
python3 -m ruff check scripts/ai_status.py scripts/test_ai_status.py   # All checks passed!
git diff --exit-code 1d07de67 -- scripts/ai_status.py             # exit 0
git status --porcelain -- scripts/ai_status.py                    # empty
git diff --check 1d07de67..HEAD                                   # clean
```

Re-run at 07:52Z from the round-3 worktree, not carried over from an earlier
round. The `git diff --check` trailing-whitespace hit the reviewer flagged was in a diff
excerpt inside `superseded-delta.txt`; it is stripped, with a note in that file
recording that no hunk content was altered.

Same receipt as above.

## 3. Deployment — atomic publish

Net effect across both rounds:

| root | role | sha256 pre-rollout | sha256 now | equals merged |
| --- | --- | --- | --- | --- |
| `/home/lupin/oday-plus` | control root (`run-supervisor.sh`, `dashboard_server.py`) | `32b6cdcd…0340da` | `5e19c1c1…4950d` | **True** |
| `/home/lupin/oday-plus-supervisor-live` | status root the live Supervisor runs from | `216c1a87…66a17b` | `5e19c1c1…4950d` | **True** |

The 07:49:55Z gated run's own receipts. Because round 1 had already landed the
correct bytes, `sha256 before` equals `sha256 after` here — so the proof that
the file was genuinely republished through the verified-sibling path is the
**inode change**, which only a rename can produce:

| root | mode | sha256 before → after | inode before → after | verified sibling | published by |
| --- | --- | --- | --- | --- | --- |
| `/home/lupin/oday-plus` | 775 → 775 | `5e19c1c1…4950d` → `5e19c1c1…4950d` | 633499 → 595636 | 6/6 checks PASS | `os.replace` |
| `/home/lupin/oday-plus-supervisor-live` | 775 → 775 | `5e19c1c1…4950d` → `5e19c1c1…4950d` | 633653 → 633499 | 6/6 checks PASS | `os.replace` |

The preflight gate ran first and passed against the real unit — `loaded`,
`active/running`, `MainPID` 1487837 alive with cmdline
`python3 -u .orchestrator/supervisor.py --verbose` — pinning continuity to
`MainPID=1487837, ExecMainStartTimestamp=Wed 2026-07-29 06:08:57 UTC,
NRestarts=0` before the first target was opened.

Per-target post-publish assertions, both roots: `target sha256 == merged blob`,
`target bytes == merged blob`, `mode preserved`, `inode CHANGED (proves rename,
not in-place write)`, `no temporary sibling left behind` — all PASS, and a glob
for `.ai_status.py.*.tmp` in each `scripts/` directory reports `none`.

The verification gate is not decorative. `deploy.py --corrupt-payload` flips one
payload byte after the merged digest is computed and was run against a sandbox
copy first (`/tmp/deploy-atomic-rehearsal-r3`, re-run in round 3 against the
hardened driver): the sibling failed `sha256 == merged blob` and the
byte-for-byte compare, was unlinked, **no rename was issued**, and the sandbox
target's sha256, bytes, mode and inode were all unchanged. A clean positive
rehearsal on the same sandbox (deliberately at mode `750`, to show the
*target's* mode is what gets preserved rather than a hardcoded one) published
normally with the inode changing. Both rehearsals ran the real preflight and
continuity gates against the real unit, and both passed them. Receipts: `runtime/.../atomic-publish-rehearsal-negative.txt`,
`runtime/.../atomic-publish-rehearsal-positive.txt`.

Unrelated dirty changes are preserved. `git status --porcelain` was captured in
each root before and after the atomic run; with `scripts/ai_status.py` filtered
out the two inventories are identical — 580 dirty entries in the control root
and 22 in the status root, none of them touched. (The status root's count was 21
before round 1 and is 22 now: `scripts/ai_status.py` itself was clean against
its ops branch before the rollout and is now modified, which is the deployment.)

Rollback still restores from the **first** backup set,
`…-20260729T071943Z/`, since that is the only one holding the pre-rollout files;
the round-2 and round-3 backups (`…-atomic-20260729T073811Z/`,
`…-gated-20260729T074954Z/`) contain the merged blob itself. Exact commands and the
expected post-rollback hashes: `runtime/.../superseded-delta.txt`.

Receipts: `runtime/.../deploy-transcript.txt` (the 07:49:55Z gated run),
`runtime/.../deploy-transcript-round1-superseded.txt` (rejected round 1).

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

Both receipts were regenerated at 07:50Z, i.e. against the inodes the gated
atomic publish created, not carried over from an earlier round.

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

Both probes were re-run at 07:51Z against the atomically published files.

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
guard (`exact=True`), so nothing would be re-attributed on the next write. Still
18/18 with an identical multiset when re-checked at 07:51Z after the gated
atomic publish.

## 7. Supervisor left running, no restart

| | preflight (before) | continuity (after) | gate verdict |
| --- | --- | --- | --- |
| `Id` / `LoadState` | `pantheon-supervisor.service` / `loaded` | same | PASS |
| `ActiveState/SubState` | active/running | active/running | PASS |
| `MainPID` | 1487837 | 1487837 | PASS — identical |
| `ExecMainStartTimestamp` | Wed 2026-07-29 06:08:57 UTC | Wed 2026-07-29 06:08:57 UTC | PASS — identical |
| `NRestarts` | 0 | 0 | PASS — identical |

These are now gate verdicts, not printed observations: had any cell differed the
driver would have exited non-zero (§7a). Same PID and start timestamp as before
round 1 at 07:19:43Z, so the process has been continuous across all three swaps.
No `systemctl start/stop/restart/reload` was issued by any driver. The journal
shows the same PID ticking on both sides of the 07:49:55Z swap (07:49:11 and
07:50:30), and a read-only `show` against the deployed live-root CLI exits 0
afterwards.

The Supervisor holds no long-lived fd on the file (0 fds under
`/proc/1487837/fd` name `ai_status.py`). The readers that matter are the
short-lived CLI invocations, and those are exactly what `os.replace` protects:
each `open()` resolves to a whole inode, old or new, never a half-written file.

Receipt: `runtime/.../supervisor-continuity.txt`.

## 7a. The supervisor gate, proved by making it fail

An observed-good outcome is not a fail-closed procedure, which is exactly what
round 2 was rejected for. `supervisor_gate_selftest.py` runs the real
`deploy.py` end to end with a scripted fake `systemctl` first on `PATH` and a
throwaway git root as the target, so any BEFORE/AFTER pair can be forced
deterministically — no timing, no sleeping, and the live Supervisor is never
queried, signalled or touched. Each scenario asserts the exit code, whether the
target was published or left byte-for-byte untouched, and that `RESULT: PASS` is
absent from every negative run.

```
 1. positive control — steady unit                       rc 0, published   PASS
 2. preflight — unit is inactive/dead                    rc 2, untouched   PASS
 3. preflight — MainPID names a dead process             rc 2, untouched   PASS
 4. preflight — MainPID is not the Supervisor            rc 2, untouched   PASS
 5. preflight — unit not loaded                          rc 2, untouched   PASS
 6. preflight — systemctl query fails                    rc 2, untouched   PASS
 7. continuity — NRestarts drifts 0 -> 1                 rc 1, published   PASS
 8. continuity — unit died during the deploy             rc 1, published   PASS
 9. continuity — restarted with a new PID and start time rc 1, published   PASS
10. continuity — systemctl query fails afterwards        rc 1, published   PASS
11. rehearsal mode cannot launder a dead unit            rc 1, untouched   PASS
summary: 11/11 scenarios behaved as required
```

Scenario 8 is the reviewer's probe reproduced verbatim — `inactive/dead` with
`NRestarts` 0→1 — and the driver now prints
`FAIL  supervisor is inactive/dead after the deploy, expected active/running`
plus `FAIL  NRestarts changed across the deploy: '0' -> '1'` and exits 1. Under
round 2's driver that same input returned `RESULT: PASS`.

Scenario 1 is what keeps the other ten honest: with a steady unit the same
driver must still publish and exit 0, so the failures above are the gate firing
rather than a driver that refuses unconditionally.

Two design notes the reviewer should check:

- Preflight failures use rc **2** and the transcript states `no target was
  touched`; post-publish failures use rc **1**. A deploy that is refused and a
  deploy that ran and then lost the Supervisor are not the same event.
- Scenarios 7–10 leave the target *published*. That is deliberate and correct:
  the write itself is atomic and already durable by then, so the gate's job at
  that point is to fail the run loudly, not to pretend a rename can be undone.

Receipt: `runtime/.../supervisor-gate-selftest.txt`; driver
`runtime/.../supervisor_gate_selftest.py`.

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
blob, published by atomic rename), plus this task's evidence in the repo. Not
changed: product code, `ai_status.py` content, the Supervisor process, config
files, task assignments, roster, or any other dirty file in either root.

Rounds 2 and 3 changed the rollout procedure and the evidence only. The repo
diff against the merge commit touches nothing under `scripts/`; `deploy.sh` was
deleted in favour of `deploy.py` so no non-atomic path remains committed, and
round 3 added the supervisor gate plus its self-test inside that same driver
directory. No product code, config file, task assignment or roster entry is
touched by this task in any round.
