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
key. Nothing ever removed it, so the live roster had accumulated 30 entries for
a 12-agent fleet: 5 prose/task-id entries plus 3 undeclared, unreferenced ones.
Baseline receipt: `roster-before.txt`.

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
command line (`assign` owner/reviewer, `handoff` target, `blocker` waiting-for,
`AI_NAME`) and exits non-zero before the command touches state. A reference is
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

**Registered actors.** The accepted set is the declared `KNOWN_AGENTS` roster,
plus agents declared in `.orchestrator/config.json`, plus names already present
in the durable roster, plus anything listed in `AI_STATUS_EXTRA_AGENTS`. Aliases
resolve before validation, so `agy3`, `claude 2`, `codex (3)`, `ops`,
`human ops` and `copilot host` keep working, and `Human/Ops` remains a valid
one-slash actor name.

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
  removable only when it is undeclared in `KNOWN_AGENTS`, undeclared in
  `.orchestrator/config.json`, **and** unreferenced by any task, blocker or
  handoff. Each removal is written to `ai-activity-log.jsonl` as an
  `agent_pruned` event. A valid actor is never removed for being idle.

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
- **kept** although undeclared in `KNOWN_AGENTS`: `Claude3` (declared in
  `.orchestrator/config.json`), and `Codex5`, `Codex6`, `Codex8`, `Codex9`,
  `CodexCoordinator` (still referenced by live tasks, blockers or handoffs)

Ordering matters and the tool enforces it: on the first dry run the four prose
entries were reported KEEP — "still referenced by a task, blocker or handoff" —
and only became removable after `retarget_blocker` freed them. The cleanup path
cannot orphan a live reference.

After the repair, `ai-status.sh sync` exits 0 with no invalid-actor warnings.
Roster and workload are both 21 valid entries, down from 30 roster entries and
25 workload keys.

## 4. Verification

```
python3 -m pytest scripts/test_ai_status.py -q -k ActorReference   # 20 passed
python3 -m pytest scripts/test_ai_status.py -q                      # 83 passed
python3 -m pytest .orchestrator/test_supervisor.py \
                  .orchestrator/test_dispatch_policy.py -q          # 249 passed
python3 -m ruff check scripts/ai_status.py scripts/test_ai_status.py
AI_NAME=Claude ./scripts/ai-status.sh sync                          # exit 0, no warnings
```

`ActorReferenceValidationTests` in `scripts/test_ai_status.py` covers task ids,
prose, oversized strings, empty references, valid named actors, alias and
`Human/Ops` preservation, unknown-but-well-shaped rejection, roster-declared
actors staying usable, byte-identical state after a rejected `blocker` and
`assign`, corrupt on-disk references never reaching `agents[]` or `workload`,
`retarget_blocker` preserving the displaced text, `retarget_blocker` refusing a
prose replacement, another owner's valid blocker being left alone, `prune_agents`
defaulting to dry-run, pruning only unreferenced synthetic entries, and never
pruning a declared or config-declared agent.

The rehearsal ran first against a copy of the live state under
`PANTHEON_STATUS_ROOT=/tmp/arv-sandbox`; the live root was only touched after
the sandbox produced a clean roster.
