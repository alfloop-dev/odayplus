# ODP-ORCH-APPROVAL-RESUME-ROOT-001: fix cross-root deferred approval resume override

Owner: Claude · Reviewer: Antigravity6 (round 1: Antigravity4) · Phase: Orchestrator Control Plane

Depends on ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001 (done) and
ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001 (done).

This task changes the approval **control plane** only. It touches no Package 10
UI, no design API, no worker business logic, and no cloud resources. Receipts
live under `docs/evidence/runtime/ODP-ORCH-APPROVAL-RESUME-ROOT-001/`; every
driver that produced them is committed next to its output so the reviewer can
re-run any of it.

**Status: reviewed, merged, and deployed.** Antigravity6 approved exact head
`bb7d78c1` on 2026-07-29T09:52:26Z; PR #502 merged into `dev` as `bd818ad5` at
09:57:00Z; the reviewed blobs were published to both authoritative roots at
09:57:27Z. See §7 for the executed rollout record.

## 1. The live failure, reproduced from live state

Receipt: `docs/evidence/runtime/.../live-defect-observation.txt` (read-only; it
mutates nothing).

The Claude hook wiring lives in exactly one file on this host,
`/home/lupin/oday-plus/.claude/settings.local.json`, and it pins an **absolute**
path:

```
python3 /home/lupin/oday-plus/.orchestrator/permission_broker.py hook PreToolUse
```

So every worker on the host, in every fleet, runs the broker that lives in the
*control root*. Meanwhile `supervisor.resume_claude_worker` injects
`PANTHEON_STATUS_ROOT=/home/lupin/oday-plus-supervisor-live` into the worker
environment, and the hook subprocess inherits it.

`common.ROOT` is `Path(__file__).resolve().parents[1]`, and every `paths.*`
entry in `config.json` is relative, so `config_path(config, "approval_queue")`
resolved under the *control* root. The queue that answered was chosen by
**which copy of the code ran**, not by **which fleet owned the worker**.

The consequence, observed live on 2026-07-29 for session
`c2cafc00-aa5f-4340-8c49-5eb1aedd30b2`, tool signature `7e0ef5d05b8a6151…`:

| root | queue | approval id | decision | override |
|---|---|---|---|---|
| `/home/lupin/oday-plus` (control) | `pending` | `apr-20260729T082950Z-578a3304` | *(none)* | `false` |
| `/home/lupin/oday-plus-supervisor-live` (authoritative) | `history` | `apr-20260729T083018Z-f3737b93` | `allow` | `true`, unconsumed |

Same `worker_run_id`, same `session_id`, same `tool_use_id`, same signature —
two queues, two verdicts. On resume the hook read the control root, found no
override, re-classified, and emitted `defer` for the exact command the
coordinator had just allowed. The session ended `stop_reason: tool_deferred`
(`.orchestrator/logs/claude-resume-20260729T083138Z-98b08b9b.log`).

**Second-order damage.** The supervisor grants a one-time allow by inserting a
temporary `allow` rule and suspending matching `ask` rules in
`CLAUDE_LOCAL_SETTINGS_PATH` — also `ROOT`-anchored. The supervisor wrote them
into the live root; consumption would have withdrawn them from the control
root. So the temporary rule was **stranded**, and
`/home/lupin/oday-plus-supervisor-live/.claude/settings.local.json` still
carried a standing `Bash(docker rm -f odp-wje-probe-pg …)` allow at observation
time. A permission broadening that was supposed to last one tool call became
permanent.

## 2. The fix

Resolution is by **declaration, not by location**. The supervisor already tells
each worker which fleet owns it; the broker now honours that.

`.orchestrator/common.py` — three primitives:

- `authoritative_status_root(env=None)` reads `PANTHEON_STATUS_ROOT` and
  **fails closed**: unset, blank, relative, non-existent, not a directory, or
  missing `.orchestrator/config.json` all return `None`, and callers keep their
  existing module-local behaviour. It never guesses at another root.
- `anchor_config_paths(config, root)` rewrites *relative* `paths.*` entries to
  absolute under `root`. Absolute entries are left alone — those are explicit
  operator overrides.
- `load_config_for_status_root(root)` loads that root's `config.json` +
  `config.local.json` and anchors them.

`.orchestrator/permission_broker.py`:

- `resolve_hook_config()` replaces the bare `load_config()` in `main()` and
  returns `(config, root, source)` where `source` is `status_root_env` or
  `module_root`.
- `claude_local_settings_path(config)` derives the rule file from
  `repo_root_for_config(config)`, so `remember_rule`, `suspend_matching_rules`,
  `restore_rules`, `add_temporary_allow_rule` and `remove_temporary_allow_rule`
  all act on the fleet that actually owns the rules. This is what closes the
  stranded-rule leak. With an unanchored config the derived path is identical to
  the old constant, so standalone behaviour is unchanged.
- `approval_queue_audit(config)` puts `status_root`, `module_root` and
  `approval_queue_path` on **every** hook activity-log record, so which queue
  answered is auditable after the fact. Paths only, no secrets.

`.orchestrator/claude_permission_prompt_mcp.py` — `resolve_broker_config()`
applies the same rule. The MCP approval server had the identical defect: it is
launched with a workspace-relative `--config`, so the queue it enqueued into
depended on the CLI's cwd. Left alone it would have been a second route to the
same split-brain.

### What was deliberately *not* changed

- **Actor validation.** `find_resume_override` still matches on
  `(session_id, tool_name, tool_input_signature)`. Nothing was relaxed.
- **Classification policy.** `evaluate_tool_request` is untouched, and it does
  not read the settings file (`_verified_claude_policy` is only used by
  `print-policy` and provider reporting), so no rule was broadened.
- **Supervisor dispatch, Package 10 UI, worker business logic, cloud resources.**

## 3. Deterministic unit proof

`.orchestrator/test_permission_broker_status_root.py` — 14 tests modelling both
roots on disk, driving the real `hook_mode` with the control root patched in as
the running module:

- declared status root wins; module-root fallback when unset
- fail-closed matrix: blank / relative / missing / non-orchestrator / file-not-dir
- absolute path overrides survive anchoring
- **the live re-defer, reproduced** — control-root config → `defer`
- authoritative root → `allow`, with the coordinator's own note as the reason
- a different command in the same session still defers (no broadening)
- the same input from a **different session** still defers (actor check intact)
- a resolved **deny** never allows
- `PermissionRequest` honours the same root
- `PostToolUse` consumes the override *in the authoritative root*
- consumption withdraws the temporary allow **and** restores the suspended `ask`
  rule there, while the control root's rule file stays untouched
- the override is single-use: replay after consumption defers
- the activity log records the queue root that answered, and the control root
  does not absorb the other fleet's audit trail

Plus 5 tests added in round 2 (§6) pinning the worker-worktree workspace
boundary in both directions.

```
pytest .orchestrator/{test_common,test_approval_queue,test_runtime_state,
  test_provider_permissions,test_permission_broker_status_root,test_supervisor,
  test_dispatch_policy}.py -q -m "not requires_live_env"
→ 357 passed, 1 deselected
pytest .orchestrator -q -m "not requires_live_env" → 433 passed, 10 deselected
ruff check (touched files) → All checks passed
```

`test_provider_permissions.py::…::test_edit_allows_configured_execute_plans_workspace_root`
is excluded by its own `@pytest.mark.requires_live_env`; it fails identically on
pre-fix `HEAD` (verified against a materialised pre-fix tree) and is unrelated.

## 4. Two-root acceptance proof, without mocks

`docs/evidence/runtime/.../two_root_acceptance.py` builds the two roots on disk
and drives the **real CLI entrypoint** —
`python3 <control-root>/.orchestrator/permission_broker.py hook <Event>` with
the payload on stdin, exactly as Claude Code invokes it.

It was run against a pre-fix tree materialised from `HEAD`, whose
`permission_broker.py` is **byte-identical (`47722c1d…`) to the broker deployed
in the live supervisor root**, and against the fixed tree:

| check | pre-fix | fixed |
|---|---|---|
| `resume_honours_authoritative_root` | **false** | true |
| `override_consumed_in_authoritative_root` | **false** | true |
| `temporary_allow_rule_withdrawn` | **false** | true |
| `suspended_ask_rule_restored` | **false** | true |
| `override_is_single_use` | true | true |
| `no_permission_broadening` | true | true |
| `resolved_deny_never_allows` | true | true |
| **verdict** | `re_deferred_or_incomplete` | `resume_honoured` |

The four checks that flip are exactly the defect; the three that were already
true stayed true, which is the evidence that nothing was broadened to buy the
fix. Receipts (`receipts/two-root-pre-fix.json`, `receipts/two-root-fixed.json`)
record session id, both approval ids, the tool signature, and both queue paths.
The only credential-shaped string in them is the synthetic fixture value
`POSTGRES_PASSWORD=probe`.

On the "resolved allow cannot end as *Approval state disappeared before the
worker could resume*" criterion: that supervisor branch is reached when a
resolved worker is no longer alive. Pre-fix, the resumed worker re-deferred and
exited, which is how a correctly-resolved **allow** ended as a worker failure.
Post-fix the resume executes and consumes the override, so the branch is not
reached — proven by `resume_honours_authoritative_root` +
`override_consumed_in_authoritative_root` above. The supervisor branch itself is
unchanged.

## 5. Deployment driver

`docs/evidence/runtime/.../deploy.py`, adapted from the reviewed
ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001 driver and extended for the one hazard that
task did not have: this fix spans **three interdependent modules**.

`os.replace` is atomic per file; a set of three renames is not. Two rules make
the on-disk set import-consistent at every instant:

1. **Stage-all-then-publish** — every sibling for every target is written and
   fully verified before the first rename is issued.
2. **Dependency order** — `common.py` publishes first. It only *adds* symbols,
   so `new-common/old-broker` is a safe intermediate; `old-common/new-broker`
   would be an `ImportError` window for any hook firing inside it.

Everything else is the reviewed discipline: same-directory `O_CREAT|O_EXCL`
sibling, fsync, mode preserved, sha256 + length + mode + byte-for-byte
verification before publish, `os.replace` + directory fsync, inode-changed
assertion afterwards, backups with a printed one-line rollback, and the
fail-closed supervisor preflight/continuity gate (`MainPID`,
`ExecMainStartTimestamp`, `NRestarts` all pinned; `systemctl` stderr never
discarded). Added on top: a **phase-3 import smoke test** per root — a set that
renames cleanly but cannot import is still a failure — run with
`PYTHONDONTWRITEBYTECODE=1` so the driver does not dirty the root it is auditing.

Rehearsed against throwaway git roots. Round 1 seeded them from `dev`; round 2
re-ran both **seeded byte-for-byte from what is actually deployed** — the
control-root sandbox starts at `c4ecfe5a…`, the live-root sandbox at
`1ed64d46…` — so the rehearsal is a dress rehearsal of the real publish rather
than of an idealised one. That reseeding is what makes §6's post-publish probe
meaningful.

- `atomic-publish-rehearsal-negative.txt` — corrupted payload → staging gate
  aborts, **no rename anywhere**, every target still at its seeded sha, rc 0.
- `atomic-publish-rehearsal-positive.txt` — 6/6 targets published, inode changed
  on each, import smoke test PASS on both roots, unrelated dirty inventory
  unchanged, rc 0.
- `receipts/two-root-deployed-sandbox.json` — the acceptance harness re-run
  against a root **deployed by the driver**: verdict `resume_honoured`, 7/7.

Both rehearsals ran against the real live supervisor unit and left it byte-for-byte
alone: `MainPID 1487837`, `ExecMainStartTimestamp Wed 2026-07-29 06:08:57 UTC`,
`NRestarts 0`, identical before and after.

## 6. Round 2: the control-root drift is not benign — publishing over it
##    would have denied every worker write

Round 1 recorded the drift and judged it harmless: *"the drift is small
(`SAFE_TOOLS`, one workspace-root pattern) and does not cause this bug."* That
judgement was wrong in the one direction that matters. It compared shas without
reading the diff, and the acceptance harness was built against a tree
byte-identical to the **live root**, so the control root's actual bytes were
never exercised.

```
/home/lupin/oday-plus/.orchestrator/permission_broker.py                 sha256 c4ecfe5a…
/home/lupin/oday-plus-supervisor-live/.orchestrator/permission_broker.py sha256 1ed64d46…
```

The control root is not an *older* revision of anything — it is a hand edit that
has never existed in git history (`git log -S ORCH_WORKSPACE_PATH --
.orchestrator/permission_broker.py` → 0 commits). It carries two hunks `dev`
does not have. One of them is load-bearing:

```python
runtime_workspace = str(os.environ.get("ORCH_WORKSPACE_PATH") or "").strip()
if runtime_workspace:
    candidate = Path(runtime_workspace).expanduser()
    if candidate.is_absolute():
        roots.append(candidate.resolve())
```

Every hook on this host is wired to the control-root file, and every live worker
runs in a per-task worktree under `/tmp/pantheon-worker-worktrees/…`, which is
outside `ROOT`, outside `ROOT.parent/"pantheon"`, and outside the configured
`allowed_workspace_roots` (`['.', '../execute-plans']`). Without that hunk,
`_paths_within_workspace` is false for a worker's own worktree and
`evaluate_tool_request` returns **deny / `out_of_workspace`** for `Edit`,
`MultiEdit` and `Write`. Publishing §5's blobs as reviewed in round 1 would have
stalled the whole fleet, not degraded it.

Proof — both revisions loaded from isolated sandbox copies, `ROOT` pinned to the
production control root, nothing under `/home/lupin` opened for write
(`control_root_drift_probe.py`, transcript in `control-root-drift-blocker.txt`):

```
A. deployed control-root hook (c4ecfe5a)   Write → ALLOW (repo_write)
B. reviewed blob, round 1  (bb5c74a6)      Write → DENY  (out_of_workspace)
```

**Resolution.** Both control-root-only hunks are forward-ported into
`.orchestrator/permission_broker.py`, so what gets published is a strict
superset of what is already running: the cross-root fix plus the live hook's
own behaviour, plus `TaskOutput` in `SAFE_TOOLS` which `dev` has and the control
root lacks. This ends the drift by merging it rather than by bulldozing it —
after the publish the hook root and the supervisor root run identical, committed
code.

`ORCH_WORKSPACE_PATH` is injected into the worker process by the Supervisor
(`common.worker_env` / `supervisor.resume_claude_worker`) and the hook
subprocess inherits it from the CLI, so a tool call cannot set it for its own
hook; a relative value is ignored rather than anchored to `ROOT`. Five tests in
`WorkerWorktreeWorkspaceTests` pin the boundary in both directions: the named
workspace is allowed, no workspace means the same write is denied, naming a
workspace widens nothing else, and a relative value adds no root.

### Incident during this round

While building the probe, a `cp` into a sandbox directory of symlinks followed
those symlinks and overwrote `/home/lupin/oday-plus/.orchestrator/`
`permission_broker.py` and `common.py` with the round-1 blobs at 09:18:2xZ. They
were restored by atomic same-directory rename at **09:19:17.89Z** to
`c4ecfe5a0f62ca21` / `1b5334ff427f9b7b` — the former corroborated by the sha
independently recorded in `live-defect-observation.txt` §6, the latter
byte-compared against the intact live-root copy. Blast radius checked and empty:
the only worker hook in that span fired at **09:20:00.303Z**, 43 s after the
restore, against the original bytes (its re-defer is the §1 defect, unrelated);
no hook executed and no approval-queue entry was created during the actual symlink-clobber interval 09:18:2xZ through restore 09:19:17.89Z (the 09:17:18Z approval entry apr-20260729T091718Z-05ab271d predated the clobber); both
files are mode 664 with no leftover siblings, and the control root's dirty
inventory is unchanged at 580 files. Sandbox copies are now real files, never
symlinks.

## 7. Executed rollout — 2026-07-29

The round-1 approval (`review_approved`, Antigravity4 @2b729993, *"ready for
post-merge deploy.py publish"*) had
been given against a blob that §6 shows would have denied every worker write, so
it was returned to review rather than deployed. **Antigravity6 re-reviewed the
forward-ported revision and approved exact head `bb7d78c1` at 09:52:26Z** (433
unit tests passed, 10 deselected). Only then did the publish have a reviewed
source, which is what the rollout criterion asks for.

Order actually followed: **merge first, publish second**, so what runs live is
committed code rather than a worktree blob.

1. **Merged.** PR #502 → `dev`, all four required contexts green
   (`orchestrator`, `product`, `product-e2e-gate`, `task-review-gate` =
   *"Approved by assigned reviewer Antigravity6"*), merge commit
   `bd818ad5f95d0fcb23d3de2bda2664d8f15ebd9e` at 09:57:00Z, a real merge (not a
   squash). The three deployed blobs at `bd818ad5` are byte-identical to the
   reviewed head `bb7d78c1`, verified by `git cat-file blob` sha256 before the
   driver ran.

2. **Published.** `deploy.py --source-ref bd818ad5… --root /home/lupin/oday-plus
   --root /home/lupin/oday-plus-supervisor-live`, transcript in
   `deploy-transcript.txt`, backups in
   `/tmp/odp-approval-resume-root-backup-20260729T095727Z`. 6/6 targets
   published, payloads materialised from the commit (never from the working
   tree), inode changed on every target, no leftover sibling, mode preserved,
   `common.py` first as §5 requires, phase-3 import smoke test PASS on both
   roots, and every other dirty file in both roots unchanged (the control root's
   580-file dirty inventory is untouched).

3. **Continuity.** The Supervisor was never signalled — no `systemctl`
   start/stop/restart/reload was issued. `MainPID 1487837`,
   `ExecMainStartTimestamp Wed 2026-07-29 06:08:57 UTC`, `NRestarts 0`,
   identical before and after, with `active/running` re-asserted after the last
   rename.

4. **Post-deploy acceptance, against the deployed bytes.**
   `two_root_acceptance.py --broker-dir /home/lupin/oday-plus` →
   `receipts/two-root-deployed-control-root.json`: verdict `resume_honoured`,
   7/7 checks true, `broker_sha256 225c002a…` — i.e. the harness drove the
   binary that is now wired into every worker's hook, not a copy of it.

5. **The §6 regression check, run where it matters.**
   `control_root_drift_probe.py` against *both deployed roots*
   (`post-deploy-drift-probe.txt`): each reads `ORCH_WORKSPACE_PATH`, resolves
   the live worker worktree into its allowed roots, and returns
   `Write → ALLOW (repo_write)`. This is the check whose absence let round 1
   nearly publish a fleet-stalling blob.

6. **Drift ended.** All three modules are now byte-identical across the hook
   root and the supervisor root, and identical to `dev`:

   ```
   common.py                      53dce620…   (was 1b5334ff… in both roots)
   permission_broker.py           225c002a…   (was c4ecfe5a… control / 1ed64d46… live)
   claude_permission_prompt_mcp.py 9d9e1913…  (was 9ba6c127… in both roots)
   ```

The two stale live queue records from §1 (`apr-20260729T082950Z-578a3304`
pending in the control root, and the unconsumed override in the live root) were
left exactly as found — they are the evidence, and reconciling them is
supervisor work, not a hand edit. Any *new* deferred approval from this point
resolves against the authoritative root, which is the whole point of the fix.

Rollback, if it is ever needed, is the one-line restore printed in
`deploy-transcript.txt` against the backup directory above.
