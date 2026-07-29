# ODP-ORCH-ACTOR-REF-VALIDATION-001: harden actor references and remove synthetic fleet agents

Owner: Claude · Reviewer: Codex2 · Phase: Fleet Control Plane Integrity ·
2026-07-29

Actor-shaped fields in `ai-status.json` must hold an agent name. They were
holding blocker sentences, and `ai_status.py` was turning each sentence into a
permanent fleet agent.

## 1. Reproduction of the fabrication path

Full transcript: `docs/evidence/runtime/ODP-ORCH-ACTOR-REF-VALIDATION-001/repro-prefix.txt`

Driving the pre-fix module (commit `647970da`) with a 154-character blocker
sentence in the `<waiting-for>` argument:

```
ai_status.command_blocker(state, ["REPRO-ACTOR-001", "deploy blocked", <sentence>])
```

`command_blocker` canonicalized the sentence, `ensure_agent()` accepted it, and
`recompute_agents()` / `recompute_workload()` promoted it into durable state:

```
KNOWN_AGENTS[<sentence>] = {
  'capability_lane': ['gcp', 'ci-cd', 'runtime-packaging', 'worker-ops'],
  'default_branch': 'feat/cannot run deploy dev until pr #484 (task/odp-deploy-job-secret-binding-selection-001, claude2) is codex6-approved, ci-passed, and merged to dev.-branch',
  'target_workload': 5,
}
```

The sentence then appeared as an `agents[]` roster entry and as a `workload`
key. Nothing ever removed it, so the live roster had accumulated 30 entries: 4
prose sentences, 2 task ids, and `Antigravity4/user`, on top of a fleet of 19
configured workers plus the non-worker actors. Baseline receipt:
`roster-before.txt`.

The mechanism is self-sustaining. `validate_state()` calls `ensure_agent()` on
every actor field already on disk, and `recompute_agents()` iterates
`KNOWN_AGENTS` and writes each name back into `agents[]`, so one bad write kept
regenerating itself on every sync.

## 2. What changed in `scripts/ai_status.py`

Two strictness levels, because the two failure modes pull in opposite
directions. Rejecting corrupt values *on load* is what caused the 2026-07-20
fleet-wide stall, when every command aborted with `Unknown agent: <sentence>`
and no worker could finalize or hand off.

**Caller input — reject before any durable mutation.**
`resolve_actor_reference()` validates every actor argument supplied on the
command line — `assign` owner/reviewer, `handoff` target, `blocker` waiting-for,
`retarget_blocker` target — and `current_actor_validated()` validates `AI_NAME`
for **every** command that records an actor. Both exit non-zero before the
command touches state. §6 covers the `AI_NAME` half, which an earlier revision
of this branch left half-finished. A reference is
rejected when it is longer than 40 characters, contains control characters,
looks like a task id (`ODP-P10-FLEET-CONFLICT-REAUDIT-001`), or is not
name-shaped. A well-shaped but unregistered name is also rejected, with the
registered set printed.

**State already on disk — quarantine, never crash.** `ensure_agent()` stays
tolerant: an invalid reference is registered as *quarantined* so derived views
do not raise, but `get_agent()`, `recompute_agents()` and `recompute_workload()`
refuse to promote a quarantined name into `agents[]` or `workload`. Corrupt
records stay readable and every command keeps working; the corruption simply
stops growing. `validate_state()` prints each invalid reference with its
location and the repair command.

**Registered actors.** The accepted set is exactly three things — see §5, which
corrects an earlier, narrower revision of this rule:

1. the **merged Supervisor config**: `.orchestrator/config.json` deep-merged with
   `.orchestrator/config.local.json`, i.e. what `common.load_config()` — the
   function dispatch itself reads — returns;
2. the explicit **non-worker actors** `Human/Ops`, `Orchestrator` and
   `CodexCoordinator`, which appear in actor-shaped fields but are never
   dispatch targets and so are never in the config;
3. anything listed in `AI_STATUS_EXTRA_AGENTS`.

The mutable `KNOWN_AGENTS` table and the durable `agents[]` roster are
deliberately **not** authority. `KNOWN_AGENTS` is mutated at runtime by
`ensure_agent()`, so admitting it would let a name invented earlier in the same
process validate a later call; `agents[]` is exactly where fabricated entries
land, so admitting it would let one bad record legitimise itself on the next
command. Aliases still resolve before validation (`agy3`, `claude 2`, `ops`,
`human ops`), and case-folding now consults the merged config, so `codex5`
resolves to the declared spelling `Codex5`.

**Two audited repair commands.**

- `retarget_blocker <task-id> <agent> <reason> [--index N]` repoints a blocker's
  `waiting_for` at a real agent. The displaced text is preserved in
  `original_waiting_for` and folded into the blocker `message`, and
  `retargeted_by` / `retargeted_at` / `retarget_reason` are recorded. It only
  touches a blocker whose current `waiting_for` fails validation, or one the
  caller owns — it cannot be used to silently reassign someone else's valid
  blocker.
- `prune_agents [--apply] [reason]` removes synthetic roster entries. Dry-run by
  default; prints a KEEP/REMOVE line with a reason for every entry. An entry is
  removable only when it is undeclared in the merged Supervisor config, not a
  non-worker actor, not registered through `AI_STATUS_EXTRA_AGENTS`, not a
  static `KNOWN_AGENTS` lane, not carrying live workload, **and** unreferenced by
  any task, blocker or handoff. Each removal is written to
  `ai-activity-log.jsonl` as an `agent_pruned` event. A valid actor is never
  removed for being idle.

  The static-lane rule is a truthfulness rule, not an authority one:
  `recompute_agents()` recreates a roster row for every `KNOWN_AGENTS` name on
  the next sync, so reporting `Gemini` or `Copilot` as removable would churn the
  roster and misreport the outcome. They are kept in the roster and still
  rejected as actor references.

## 3. Live roster repair

Receipts under `docs/evidence/runtime/ODP-ORCH-ACTOR-REF-VALIDATION-001/`:
`ai-status.before.json`, `prune-dry-run-before.txt`, `retarget-transcript.txt`,
`prune-apply.txt`, `sync-after.txt`, `roster-after.txt`, `ai-status.after.json`.

Four blockers held prose. The mandated one is the deferred-approval rollout
blocker, whose `waiting_for` held a 1446-character STOP-gate report:

```
AI_NAME=Claude ./scripts/ai-status.sh retarget_blocker \
  ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001 Codex2 \
  "STOP-gate text belongs in the blocker message; the actor waited on is the reviewer"
```

| blocker | task | before | after |
| --- | --- | --- | --- |
| 15 | ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001 | 1446-char STOP-gate report | `Codex2` |
| 5 | ODP-PRODUCTION-MODEL-REGISTRY-001 | `Human/Data owner: authoritative ForecastOps daily-history backfill` | `Human/Ops` |
| 8 | ODP-P10-DEV-REDEPLOY-VERIFY-001 | 314-char PR #484 status paragraph | `Claude2` |
| 9 | ODP-P10-DEV-REDEPLOY-VERIFY-001 | `Remediation task for migration-compatibility-smoke timeout` | `Antigravity3` |

Blocker 5 also repaired `ODP-PRODUCTION-MODEL-REGISTRY-001.waiting_for`. All
four original strings survive in `original_waiting_for` and in the blocker
message — verified for blocker 15: `message` grew to 1481 characters and still
contains the full STOP-gate text.

`prune_agents --apply` then removed 9 entries and kept 21:

- removed as task ids: `ODP-ORCH-ANTIGRAVITY-LIVE-FALLBACK-001`,
  `ODP-P10-FLEET-CONFLICT-REAUDIT-001`
- removed as prose: the four sentences above, once nothing referenced them
- removed as undeclared and unreferenced: `Antigravity4/user`, `Codex4`,
  `Codex7`
- **kept**: `Claude3` (declared in `.orchestrator/config.json`), `Codex5`,
  `Codex6`, `Codex8`, `Codex9` (declared in `.orchestrator/config.local.json`
  and referenced by live tasks) and `CodexCoordinator` (non-worker actor)

Two of those removals need the §5 correction read alongside them. `Codex4` and
`Codex7` are declared in `config.local.json`; the revision that ran the cleanup
read only the tracked half of the config and classified them as undeclared. They
had no tasks, blockers or handoffs, so their roster rows were dropped —
`recompute_agents()` recreates a row the moment either is assigned work, and
neither lost an assignment. Under the corrected classifier both are KEEP. No
`Codex5/6/8/9` row or assignment was ever touched.

Ordering matters and the tool enforces it: on the first dry run the four prose
entries were reported KEEP — "still referenced by a task, blocker or handoff" —
and only became removable after `retarget_blocker` freed them. The cleanup path
cannot orphan a live reference.

After the repair, `ai-status.sh sync` exits 0 with no invalid-actor warnings.
Roster and workload are both 21 valid entries, down from 30 roster entries and
25 workload keys.

## 4. Verification

```
python3 -m pytest scripts/test_ai_status.py            # 98 passed, 41 subtests
python3 -m pytest .orchestrator/test_supervisor.py \
                  .orchestrator/test_dispatch_policy.py             # 249 passed
python3 -m ruff check scripts/ai_status.py scripts/test_ai_status.py
```

Transcript: `docs/evidence/runtime/ODP-ORCH-ACTOR-REF-VALIDATION-001/verification-r2.txt`.
(`pytest -q` hides the summary line under this repo's plugin set, hence the
unquieted form.)

Three test classes in `scripts/test_ai_status.py`:

`ActorReferenceValidationTests` — task ids, prose, oversized strings, empty
references, valid named actors, alias and `Human/Ops` preservation,
unknown-but-well-shaped rejection, **durable `agents[]` not being an authority**,
**static `KNOWN_AGENTS` names being rejected without a config declaration**,
**non-worker actors accepted without a config declaration**,
`AI_STATUS_EXTRA_AGENTS` registration, corrupt on-disk references never reaching
`agents[]` or `workload`, `retarget_blocker` preserving the displaced text,
refusing a prose replacement and leaving another owner's valid blocker alone,
`prune_agents` defaulting to dry-run, pruning only unreferenced synthetic
entries, keeping a configured worker that is idle, keeping an undeclared agent
that is carrying work, and not advertising static lanes as removable.

`MergedConfigActorAuthorityTests` — local-overlay parity: a base config plus a
`config.local.json` overlay declaring `Codex3`-`Codex9`, asserted equal to
`common.deep_merge` of the two and yielding all nine names; the same fleet
*without* the overlay rejecting `Codex5`, which proves the overlay rather than a
static table is what admits them; the status-root overlay covering worker
worktrees; the live path delegating to `common.load_config()` verbatim; and
`codex3` resolving to `Codex3` rather than folding into `Codex`.

`ActorCommandMutationGuardTests` — a prose or task-id actor argument, an
unregistered actor argument, and a bad `AI_NAME` each raise `SystemExit` while
leaving the serialized state byte-identical, `KNOWN_AGENTS` unchanged, and
`append_log` uncalled. The `AI_NAME` table covers **all 15 actor-bearing
commands** (§6), against both a malformed prose name and the well-shaped but
unregistered `Nessie9`. Two structural guards keep the table honest:
`test_ai_name_case_table_covers_every_actor_bearing_command` compares it against
`MUTATING_COMMANDS` minus the declared-actorless `sync`, so a new command cannot
be added without either covering it or exempting it on purpose; and
`test_no_unvalidated_actor_read_remains` walks the module AST and fails on any
reference to the deleted `current_actor`.

Live verification is read-only and captured in
`docs/evidence/runtime/ODP-ORCH-ACTOR-REF-VALIDATION-001/authority-after-correction.txt`.

The §3 rehearsal ran first against a copy of the live state under
`PANTHEON_STATUS_ROOT=/tmp/arv-sandbox`; the live root was only touched after
the sandbox produced a clean roster.

## 5. Correction: `config.local.json` is part of the declaration authority

An earlier revision of this branch resolved declarations from
`.orchestrator/config.json` alone. That was wrong. The Supervisor decides
dispatchability through `common.load_config()`, which deep-merges
`.orchestrator/config.json` with the gitignored `.orchestrator/config.local.json`
overlay — and on this fleet that overlay is the *only* place `Codex3` through
`Codex9` are declared. Reading the tracked half alone classified six live
workers as synthetic.

What changed:

- `merged_orchestrator_config()` uses `common.load_config()` verbatim when this
  process points at the same config path, so the two cannot drift; otherwise it
  applies the same `common.deep_merge` to whatever `CONFIG_FILE` resolves to.
- Because `config.local.json` is gitignored, a worker worktree only checks out
  the tracked half. The status-root overlay is merged as well, so a command
  gives the same answer in a worktree as in the live checkout — otherwise
  `AI_NAME=Codex5` would work at home and fail under a worker.
- The `codex3 -> Codex` alias is removed. It predates the overlay and would have
  silently folded a real worker into another lane.
- Case-folding consults the merged config, so `codex5` resolves to `Codex5`.

Live receipt (`authority-after-correction.txt`): `configured_agent_names()` is
identical to the live `common.load_config()` agent list — all 19 workers,
including `Codex3`-`Codex9` — every one of the six `Codex5/6/8/9` assignments
resolves, live state has zero invalid actor references, and every roster entry
classifies as KEEP. **No cleanup or repair command was run against live state on
this revision.**

Behaviour change worth calling out at review: `Gemini`, `Gemini2` and `Copilot`
exist in the static `KNOWN_AGENTS` table but are absent from the merged config,
so the Supervisor cannot dispatch them and they are no longer accepted as actor
references. Their roster rows are left alone. If either lane is revived, declare
it under `agents` in the config or set `AI_STATUS_EXTRA_AGENTS`.

## 6. Correction: the `AI_NAME` gate covered 4 commands, not all of them

Revision 1 of this branch claimed a fail-before-mutation gate on every mutating
command. It had one on four. Codex2 rejected it at gate 2 on head `06d28b5b`
after scanning for the unvalidated reader directly, and the scan was right:
`current_actor()` merely canonicalized `AI_NAME` and returned it, so eleven
commands still let a malformed or unregistered name through.

Where it reached, and how far:

| command | old call site | what a bad `AI_NAME` reached |
| --- | --- | --- |
| `assign` | activity log, line 4030 | task appended to `tasks[]` first; bad actor then written to the log |
| `archive_migrate` | line 4561 | tasks archived out of state first; bad actor then written to the log |
| `start` | 4042 | state mutation + log |
| `progress` | 4062 | state mutation + log |
| `note` | 4081 | state mutation + log |
| `reopen` | 4095 | state mutation + log |
| `restore_approved` | 4407 | state mutation + log |
| `done` | 4440 | state mutation + log, including delivery metadata |
| `supersede` | 4478 | state mutation + log + archive |
| `approve` | 4514 | state mutation + log |
| `wave` | 4620 | `wave_state` history entries carry `actor` |

`assign` and `archive_migrate` were the worst of the eleven: they read the actor
*inside* the `append_log()` call at the end, so the state mutation had already
happened by the time the name was looked at. The other nine read it early, so
validating in place was enough; those two had the read hoisted to the top of the
function.

Three things changed:

1. All eleven now read `current_actor_validated()` before their first mutation.
   Where the old code followed `current_actor()` with a bare `ensure_agent()`,
   that call is dropped — `resolve_actor_reference()` already registers the name,
   and only after it validates.
2. `current_actor()` is **deleted**, not deprecated. Leaving a tolerant reader
   next to a strict one is what produced this defect: every new command reached
   for the shorter name. Nothing outside `ai_status.py` imported it.
3. `main()`'s command tables are lifted to module scope as `READ_ONLY_COMMANDS`
   / `MUTATING_COMMANDS`, with `ACTORLESS_MUTATING_COMMANDS = {"sync"}` recording
   the single deliberate exemption — `sync` only recomputes derived views and
   records nothing under an agent name. The test table is now checked against
   these sets, so the coverage claim in §4 is enforced rather than asserted.

Receipts:

- `docs/evidence/runtime/ODP-ORCH-ACTOR-REF-VALIDATION-001/fail-closed-cli-r2.txt`
  — the real CLI, all 15 actor-bearing commands (16 invocations, counting both
  `wave` subcommands) × 2 bad names = 32 runs, each `exit=1` with `ai-status.json`
  and `ai-activity-log.jsonl` byte-identical before and after. Run against a
  byte-for-byte copy of the live state under `PANTHEON_STATUS_ROOT`, never
  against the live root. Includes a positive control: `AI_NAME=Claude` still
  drives `note` to completion and logs `Claude` as the actor, so the gate is
  fail-closed rather than fail-always.
- `docs/evidence/runtime/ODP-ORCH-ACTOR-REF-VALIDATION-001/merged-config-authority-r2.txt`
  — live read-only re-read of the merged config at this head: `Codex3`-`Codex9`
  still resolve, `human ops` still folds to `Human/Ops`, `Codex5` is still
  accepted, and the live `ai-status.json` hash is unchanged across the read.
- `docs/evidence/runtime/ODP-ORCH-ACTOR-REF-VALIDATION-001/verification-r2.txt`
  — test and lint transcript.

No live state was mutated and no `Codex5/6/8/9` mapping was touched on this
revision, per the reopen instruction.

## 7. Closeout: integration with `dev` at finalize time

`dev` advanced by 14 commits (through `61b8d46d`, PR #497 for
`ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001`) while PR #496 was waiting
on its `product` check, so the PR went `BEHIND` and branch protection's strict
up-to-date requirement blocked the merge.

Integration was done as a forward merge on the task branch — no rebase, no
force-push:

```
$ git merge origin/dev --no-edit   # -> 3b90e147
```

The incoming range touches none of this task's owned files. Verified before
merging:

```
$ git diff --name-only $(git merge-base HEAD origin/dev) origin/dev -- \
    scripts/ai_status.py scripts/test_ai_status.py ai-status.json \
    docs/evidence/fleet_dispatch/ODP-ORCH-ACTOR-REF-VALIDATION-001.md \
    docs/evidence/runtime/ODP-ORCH-ACTOR-REF-VALIDATION-001
  (empty)
```

Post-merge verification at the integrated head:

```
$ python3 -m pytest scripts/test_ai_status.py .orchestrator/test_supervisor.py \
    .orchestrator/test_dispatch_policy.py
347 passed, 41 subtests passed in 6.61s

$ python3 -m ruff check scripts/ai_status.py scripts/test_ai_status.py
All checks passed!
```

The counts match the reviewed revision component-for-component: 98 + 41 subtests
from `scripts/test_ai_status.py` and 249 from the supervisor/dispatch suites,
run together here as one invocation.

Because `task-review-gate` is a commit status pinned to a specific head SHA, the
merge commit left the new head unstamped. Re-stamping it is the reviewer's
action, not the owner's — `approve` requires `AI_NAME` to equal the assigned
reviewer, which is precisely the gate this task hardened. The task is therefore
handed back to `Codex2` to re-emit the gate on the integrated head; the owner
runs `done` only after PR #496 actually merges into `dev`.
