# ODP-ORCH-APPROVAL-RESUME-ROOT-001: fix cross-root deferred approval resume override

Owner: Claude · Reviewer: Antigravity4 · Phase: Orchestrator Control Plane

Depends on ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001 (done) and
ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001 (done).

This task changes the approval **control plane** only. It touches no Package 10
UI, no design API, no worker business logic, and no cloud resources. Receipts
live under `docs/evidence/runtime/ODP-ORCH-APPROVAL-RESUME-ROOT-001/`; every
driver that produced them is committed next to its output so the reviewer can
re-run any of it.

**Status: implementation and proof complete; the live rollout is deliberately
not executed.** See §7 — the acceptance criterion says deploy *the reviewed
fix*, and this revision is not reviewed yet.

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

```
pytest .orchestrator/{test_common,test_approval_queue,test_runtime_state,
  test_provider_permissions,test_permission_broker_status_root,test_supervisor,
  test_dispatch_policy}.py -q -m "not requires_live_env"
→ 352 passed
ruff check (5 touched files) → All checks passed
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

Rehearsed against throwaway git roots seeded with the pre-fix modules:

- `atomic-publish-rehearsal-negative.txt` — corrupted payload → staging gate
  aborts, **no rename anywhere**, every target still `1ed64d46…`, rc 0.
- `atomic-publish-rehearsal-positive.txt` — 6/6 targets published, inode changed
  on each, import smoke test PASS on both roots, unrelated dirty inventory
  unchanged, rc 0.
- `receipts/two-root-deployed-sandbox.json` — the acceptance harness re-run
  against a root **deployed by the driver**: verdict `resume_honoured`, 7/7.

Both rehearsals ran against the real live supervisor unit and left it byte-for-byte
alone: `MainPID 1487837`, `ExecMainStartTimestamp Wed 2026-07-29 06:08:57 UTC`,
`NRestarts 0`, identical before and after.

## 6. Operational finding: the control root is behind

At observation time the two deployed brokers differ:

```
/home/lupin/oday-plus/.orchestrator/permission_broker.py                 sha256 c4ecfe5a…
/home/lupin/oday-plus-supervisor-live/.orchestrator/permission_broker.py sha256 1ed64d46…
```

The control root — the copy that actually executes every hook on this host — is
on an **older** revision than the live supervisor root. The drift is small
(`SAFE_TOOLS`, one workspace-root pattern) and does not cause this bug, but it
means hook behaviour and supervisor behaviour are not currently the same code.
The deployment in §7 publishes the same reviewed blobs to both roots, which
also closes this drift for the three files it touches.

## 7. What remains before `done`

The rollout criterion is *"deploy the **reviewed** fix … and verify PID restart
continuity"*. This revision has not been reviewed, and writing to
`/home/lupin/oday-plus` and `/home/lupin/oday-plus-supervisor-live` mutates live
fleet infrastructure outside this worktree. Per the rollout discipline
established by ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001, that is coordinator-gated
and is not a worker's call to make unilaterally.

So the live publish is **prepared and rehearsed but not executed**. After
Antigravity4 approves and the PR merges into `dev`, the owner runs:

```bash
python3 docs/evidence/runtime/ODP-ORCH-APPROVAL-RESUME-ROOT-001/deploy.py \
  --source-ref <merge-commit> \
  --backup-dir /tmp/odp-approval-resume-root-backup-<stamp> \
  --root /home/lupin/oday-plus \
  --root /home/lupin/oday-plus-supervisor-live \
  | tee docs/evidence/runtime/ODP-ORCH-APPROVAL-RESUME-ROOT-001/deploy-transcript.txt
```

then re-runs `two_root_acceptance.py --broker-dir /home/lupin/oday-plus` against
the deployed control root, commits the transcript plus that receipt, and only
then runs `scripts/ai-status.sh done`. The driver refuses to start (rc 2,
nothing touched) unless the supervisor is `active/running` with a live
Supervisor `MainPID`, and fails the run if `MainPID`,
`ExecMainStartTimestamp` or `NRestarts` moved.

The two stale live queue records from §1 (`apr-20260729T082950Z-578a3304`
pending in the control root, and the unconsumed override in the live root) were
left exactly as found — they are the evidence, and reconciling them is
supervisor work, not a hand edit.
