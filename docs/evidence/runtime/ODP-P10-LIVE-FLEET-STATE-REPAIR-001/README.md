# ODP-P10-LIVE-FLEET-STATE-REPAIR-001 — Canonical Package 10 Live-Closure Task Truth Repair

- Task: `ODP-P10-LIVE-FLEET-STATE-REPAIR-001` (order `T00`, priority `P0`, phase `Package10LiveClosure`)
- Owner: `Claude` (helper-claimed; pack declares `Claude2`) · Reviewer: `Antigravity4`
- Repaired at: `2026-08-09T15:00Z`
- Canonical writer: `$PANTHEON_STATUS_ROOT/scripts/ai-status.sh` against `/home/lupin/oday-plus-supervisor-live`
- Dispatch authority: `docs/evidence/fleet_dispatch/PACKAGE_10_LIVE_COMPLETION_EXECUTION_TASKS_2026-08-09.json`
- Gap analysis: `docs/evidence/PACKAGE_10_LIVE_COMPLETION_GAP_ANALYSIS_2026-08-09.md` (GAP-08)

This task repairs the canonical Package 10 live-closure task graph. It changes no
product code, no deployment workflow, and no archived design source. Every
canonical mutation went through the live status writer; nothing was hand-edited
into `ai-status.json`, `current-work.md`, or `ai-activity-log.jsonl`.

---

## 1. Gate authority used for this repair

The pack anchors its evidence at `4d89bea6` / Deploy Dev run `31312735093`. That
anchor was stale by the time this task ran: the `dev` merge burst continued and
two further Deploy Dev runs completed.

| Run | Head SHA | Conclusion | Window (UTC) | Artifact | Standing |
|---|---|---|---|---|---|
| `31312735093` | `4d89bea6` | `failure` | `12:13:20Z` → `12:33:49Z` | yes | pack anchor, superseded |
| `31314125275` | `ebfe128e` | `failure` | `12:46:30Z` → `13:06:21Z` | yes | superseded |
| `31316767710` | `9c95ecc3` | `failure` | `13:48:24Z` → `14:08:37Z` | yes | **latest completed run, gate authority for this repair** |
| `31319450627` | `5baa0931` | in progress | started `14:49:12Z` | — | in flight at repair time, not citable |

Gate authority `31316767710` at `9c95ecc3e1f2d0885bb4078070a116e852487f69`:
`live-e2e-gate.json` `generated_at=2026-08-09T14:07:16Z`,
`correlation_id=corr-live-e2e-9c95ecc3e1f2-1786284436`, `ok=false`, 50 checks,
43 passed, 7 failed, `blocking_dependencies=[external-data, mlflow]`.

**The blocker set is byte-identical to the pack anchor at `4d89bea6`** — the same
seven checks, the same two blocking dependencies, the same 43/50 split. No gap was
opened, closed, or re-scoped by the move from `4d89bea6` to `9c95ecc3`; only the
exact-SHA anchor advanced. The pack's gap register therefore stands unchanged, and
re-anchoring the gap analysis itself remains the evidence-refresh task's scope, not
this one.

The seven blockers, verbatim from the artifact archived here as
`live-e2e-gate-9c95ecc3.json`:

| Check | Dependency | Detail |
|---|---|---|
| `runtime:model_bindings` | `mlflow` | `PRODUCTION_MODEL_REGISTRY_UNAVAILABLE: forecast_revenue_interval` |
| `runtime:model_capability:forecastops` | `mlflow` | `available=False reasonCode=PRODUCTION_MODEL_REGISTRY_UNAVAILABLE` |
| `models:registry` | `mlflow` | `versions=0` |
| `models:forecastops:production_alias` | `mlflow` | `versionsWithProductionAlias=0 (exactly one required)` |
| `data:ingestion_runs` | `external-data` | `runs=0` |
| `data:admin_boundary.official_dataset:run_exists` | `external-data` | no persisted ingestion run |
| `data:poi.commercial_api:run_exists` | `external-data` | no persisted ingestion run |

---

## 2. State found, and what was actually still broken

A partial repair by `CodexCoordinator` landed between `14:40:20Z` and `14:47:18Z`,
concurrently with this task's dispatch. It had already created `T40` and `T41`,
installed writable and forbidden ceilings on `T42`, `T50`, and `T60`, and
corrected the `T30` reviewer to `Claude2`. That work is preserved, not redone.

The defects that remained are below. Each was verified against the live status
root before the fix and re-verified after.

### D1 — `mutates_canonical` undeclared on `T21`

`ODP-PRODUCTION-MODEL-REGISTRY-001` carried no `mutates_canonical` key at all, so
10 of 11 tasks declared it and one gave a consumer no answer. Set to `true`: the
task's ceiling includes `models/**` and `scripts/models/**` and it moves a
production alias.

### D2 — six tasks pointed at source documents that do not exist

`T30`, `T40`, `T41`, `T42`, `T50`, and `T60` each carried these two
`target_context_paths`:

- `docs/evidence/PACKAGE_10_LIVE_RUNTIME_GAP_ANALYSIS_2026-08-09.md` — **does not exist**
- `docs/evidence/PACKAGE_10_LIVE_COMPLETION_EXECUTION_TASKS_2026-08-09.md` — **does not exist** (the real file is under `docs/evidence/fleet_dispatch/`)

Owner and reviewer materialize the same generated brief from these references, so
an unresolvable path yields a missing source ref and the two manifests cannot be
proven identical. All 11 tasks now carry the same resolvable dispatch-authority
triple in `target_context_paths`: the gap analysis, and the execution-task pack in
both its Markdown and JSON peers.

This is not cosmetic. `common.execution_context_files()` **fail-closes** any P0 or
`mutates_canonical` task whose `source_docs` contain an unresolvable reference:

```python
if not valid:
    if is_mutating_or_p0:
        raise ValueError(f"Fail-closed on task {task_id}: {err_reason} for source_doc '{doc_entry}'")
```

All 11 tasks are P0, so a bad reference blocks brief materialization for owner and
reviewer alike. See section 5 for the resolution-root constraint this exposed.

### D3 — `T40` and `T41` had an empty canonical `acceptance`

Both were created with `acceptance: []` and a paraphrased `acceptance_criteria`
list that was not the pack text. The canonical field was empty while a
non-canonical field carried the only wording. The pack's acceptance lists are now
installed in `acceptance`, with `acceptance_criteria` mirrored to the identical
text so the two cannot drift apart.

### D4 — `T30` carried two disagreeing acceptance lists

`acceptance` held six criteria and `acceptance_criteria` held five different ones,
with no rule saying which governed. Resolved to one canonical `acceptance`, with
the pack's wording preserved under `package10_acceptance_addendum` — the naming
already used by `T20`, `T21`, `T42`, `T50`, and `T60`.

### D5 — `T11` had an empty writable ceiling

`ODP-P10-LIVE-EXTDATA-REMEDIATE-001` carried `writable_paths: []` while its own
`dispatch_condition` read "T10 records remediation_required **and T00 installs
exact writable ceiling**". The pack's `writable_path_ceiling` is now installed as
`writable_paths`, so the second half of its own dispatch condition is satisfied.

### D6 — the provider-ingestion task was absent from active state and archive

See section 3.

### D7 — every `next` field was placeholder text

All eleven read `Assignment created`, `Ownership updated`, or the supervisor's
auto-start line. None named a run SHA, a blocker, a dependency, or a resume
condition. All ten non-`T00` tasks now carry a `next` that names the gate
authority run and SHA, the specific blocking checks that apply to that task, its
dependencies with their current status, and an explicit `Resume when:` clause.

### D8 — three tasks failed the plan-execution-pack validator

`validate_plan_execution_pack.py` reported `source_docs must reference the
control-pack JSON` for `T42`, `T50`, and `T60`. The control-pack JSON and its
Markdown peer were added to each task's `source_docs`.

---

## 3. Restoring the provider-ingestion work (GAP-08, acceptance 2)

`ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001` was absent from both canonical active
state and the task archive, while its delivered evidence sat committed in the
repository at `docs/evidence/runtime/ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001/`
(delivered `2026-08-03` at release `5a1aee5b`, commit `f63381a7`, owner
`Antigravity`, reviewer `Antigravity7`). Real delivered work had no canonical
resolution at all.

The record is restored with its historical attribution preserved in
`historical_owner`, `historical_reviewer`, `historical_release_sha`, and
`delivery_commit`, then immediately superseded into
`ODP-P10-LIVE-EXTDATA-DIAG-001` and archived. It is terminal and durable; it is
not a dispatchable duplicate of `T10`.

Superseded rather than closed as `done`, because its diagnosis was delivered but
its defect was not fixed. Its own evidence states that no code or configuration
change was made. The four findings preserved on the archived record:

1. the worker's `handle_external_fetch` → `run_scheduled()` → `ingest()` path passes no `tenant_id`, so `ExternalIngestionService._resolve_store("")` returns the unscoped store and persists `IngestionRunRecord` with `tenant_id=""`;
2. `GET /api/v1/external-data/ingestion-runs` resolves tenant `a11ce505-70bc-56d9-8564-ad22efa23c9e` and reads through `TenantScopedDocumentStore`, so worker-written rows are invisible and `count=0`;
3. worker and API bind the same Secret Manager reference `oday-plus-dev-api-database-url-pg16` and the same `durable_documents` table, so the asymmetry is tenant scope, not database binding;
4. two governed runs were created through the authenticated API path and read back with lineage, but no code or configuration change was made, so the worker-path defect was never fixed.

That is why `data:ingestion_runs` still reports `runs=0` at `9c95ecc3`: the two
runs were created under the operator tenant through the API, and the worker path
that the gate exercises still writes where the API cannot read. The residual scope
is recorded on the archived task and carried into `T10`'s `next` as the leading
hypothesis to confirm or refute first — stated as a hypothesis, not as a
conclusion, because `T10` owns the diagnosis and must reach it on its own evidence.

---

## 4. `mutates_canonical` normalization (acceptance 8)

Pack section 8.3 assigns this to `T00`: declare the field explicitly on all 11,
use `false` for read-only evidence tasks, and widen no task's authority to justify
a `true`.

| Order | Task | Value | Justification |
|---|---|---|---|
| `T00` | `ODP-P10-LIVE-FLEET-STATE-REPAIR-001` | `true` | writes canonical task truth through the status writer |
| `T10` | `ODP-P10-LIVE-EXTDATA-DIAG-001` | `false` | read-only evidence; ceiling is one evidence directory |
| `T11` | `ODP-P10-LIVE-EXTDATA-REMEDIATE-001` | `true` | scoped product remediation under the pack ceiling |
| `T20` | `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` | `true` | governed ingestion into the data plane |
| `T21` | `ODP-PRODUCTION-MODEL-REGISTRY-001` | `true` | model registry and production alias movement |
| `T30` | `ODP-P10-DEV-REDEPLOY-VERIFY-001` | `true` | deploys and promotes a release |
| `T40` | `ODP-P10-LIVE-VISUAL-PARITY-001` | `false` | read-only evidence |
| `T41` | `ODP-P10-LIVE-LEGACY-RETIREMENT-001` | `false` | read-only evidence |
| `T42` | `ODP-PLAN-LIVE-STAGING-PROOF-001` | `false` | read-only evidence |
| `T50` | `ODP-PLAN-UAT-SIGNOFF-001` | `false` | evidence packet plus human signoff |
| `T60` | `ODP-PLAN-FINAL-GATE-AUDIT-001` | `false` | audit packet plus human signoff |

Three of the pack's eight `true` declarations — `T10`, `T40`, `T41` — are
corrected to `false` here, exactly as section 8.3 requires. No task's
`writable_paths` was widened to justify a `true`; the declaration is descriptive
metadata and grants nothing.

`T30` is worth naming explicitly: it writes only its own evidence directory, yet
declares `true`, because its canonical mutation is a runtime deployment and
promotion rather than a file write. The verifier accepts a `true` backed by
declared `runtime_actions` for exactly this case.

---

## 5. Verification

`verify_fleet_state.py` in this directory checks the seven self-assertable
acceptance criteria against the live status root, the archive, and the repository.
It is read-only. Output is archived as `verify_fleet_state.out`.

```
python3 verify_fleet_state.py \
  --status /home/lupin/oday-plus-supervisor-live/ai-status.json \
  --archive-root /home/lupin/oday-plus-supervisor-live/ai-task-archive \
  --repo-root .
```

Result: **7/7 pass**, exit `0`.

| Acceptance criterion | Result |
|---|---|
| each summarized task has exactly one canonical resolution | pass |
| missing provider-ingestion work is restored durably | pass |
| existing task next fields name current run SHA blockers dependencies and resume condition | pass |
| owner and reviewer source manifests match | pass |
| historical R3 implementation tasks are not reopened | pass |
| all update_existing tasks receive explicit writable and forbidden ceilings | pass |
| mutates_canonical is normalized for all 11 tasks without widening authority | pass |
| independent exact-state review passes | **reviewer-owned; not self-assertable** |

The verifier found two defects in its first run and both were fixed rather than
argued away:

1. `T11.source_docs` named `docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/`, which is `T10`'s future output and does not exist yet. An unresolvable source ref is precisely the manifest-mismatch failure mode, so it moved to `upstream_evidence_inputs` as a forward reference; `T10` closeout promotes it into `source_docs`.
2. The `mutates_canonical` justification rule initially demanded a writable path beyond the task's own evidence directory, which wrongly flagged `T30`. The rule was corrected to also accept declared runtime mutation actions — a refinement of the check, since `T30`'s deploy-and-promote authority is real.

### Brief materialization, and the resolution-root constraint

`verify_brief_materialization.py` calls `common.execution_context_files()` for all
11 tasks. Output is archived as `verify_brief_materialization.out`.

Result: **11/11 materialize without fail-closed**, exit `0`.

This check caught a regression introduced by the D2 fix itself, and it is the most
important finding in this report for anyone editing task metadata:

`validate_source_doc_path()` resolves every `source_doc` **against the live status
root checkout**, not against `dev`. That checkout lags `dev` by a large margin. The
first D2 fix added the three 2026-08-09 dispatch-authority documents to
`source_docs` on seven tasks. Those files exist in `dev` — they are what this task
was dispatched to work from — but they do not exist in the status root, so
materialization dropped from 11/11 to 4/11 and seven tasks became undispatchable.

Every candidate path was then validated against the status root directly. Only
those three 2026-08-09 documents are missing; all older sources resolve, including
the control-pack JSON that the D8 fix depends on. The three are therefore carried in
`target_context_paths`, which is descriptive and not fail-closed validated, exactly
as `T00` itself already carried them. Each affected task records the reason in
`dispatch_authority_note`.

**A document merged to `dev` cannot be cited as a `source_doc` until the live status
root advances to include it.** Doing so fail-closes dispatch for every P0 task that
cites it.

### Plan-execution-pack validator

`scripts/ops/validate_plan_execution_pack.py` against the live status root:

- before: 84 errors, of which **3 in scope** (`T42`, `T50`, `T60` missing the control-pack JSON in `source_docs`);
- after: 81 errors, **0 in scope**, and a line-level diff confirms **no new error was introduced**.

Both runs are archived as `plan_pack_validator_before.txt` and
`plan_pack_validator_after.txt`.

### Task briefs

The five `.orchestrator/task-briefs/odp_p10_live_*.md` briefs — `T00`, `T10`,
`T11`, `T40`, `T41` — were regenerated from repaired canonical state with the
orchestrator's own `generate_task_brief_content()`, not hand-written, and all five
report `FRESH` against `is_task_brief_stale()`.

They do not appear in this PR: `.orchestrator/task-briefs/` is excluded through
`.git/info/exclude` in the status root, because briefs are derived artifacts that
`write_task_brief()` regenerates whenever canonical state moves. Regenerating them
here confirms the repaired metadata produces a valid brief; the durable fix is the
canonical state they are generated from.

### Historical R3 records

`ODP-P10-CAN-001-R3A` and `ODP-P10-CAN-001-R3B` are read read-only by `T41` to
reconstruct the 117-path inventory. No R3 task is open in active state, and no
archived R3 record was moved out of a terminal status. `T41`'s `next` states
explicitly that these ACKs must not be reopened or redispatched as implementation
work.

---

## 6. Deviations and findings for the reviewer

1. **`T00` owner deviates from the pack.** The pack names `Claude2`; the live task
   is owned by `Claude` under `assignment_note: "Helper-claimed by idle Claude;
   designated reviewer Antigravity4 preserved."` This is a legitimate helper claim
   that preserved the designated reviewer, recorded here rather than silently
   reconciled in either direction. Every other owner and reviewer matches the pack;
   `T11`'s pack owner is the placeholder
   `coordinator_selects_Claude2_or_Antigravity5_from_root_cause`, resolved to
   `Claude2`.

2. **The pack's evidence anchor is stale.** `4d89bea6` is two completed Deploy Dev
   runs behind. The blocker set is unchanged, so no gap moved, but the pack's
   `evidence_snapshot` no longer names the latest completed run. Re-anchoring the
   pack and the gap analysis is the evidence-refresh task's scope; this task
   anchored only the `next` fields it owns.

3. **81 plan-execution-pack validator errors remain, all out of scope.** They
   belong to other `ODP-PLAN-*` tasks — `ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001`,
   `ODP-PLAN-AVM-OUTCOME-BACKFILL-001`, `ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001`
   and others — that are not among the 11. Several are substantive (`owner must not
   equal reviewer`, `human gate owner must be Human/Ops`). Flagged for the
   coordinator; not repaired here, because they are outside this task's declared
   scope and outside its writable ceiling.

4. **A Deploy Dev run was in flight during this repair** (`31319450627` at
   `5baa0931`, started `14:49:12Z`). This task performs no deploy, no `dev` push,
   and no product mutation, so the "active overlapping Deploy Dev mutation" stop
   condition was not triggered. The in-flight run is named in the `next` fields so
   downstream owners know a newer anchor may land.

5. **`mutates_canonical` is descriptive metadata.** Tracing `writable_paths`,
   `forbidden_paths`, `automation_class`, and `target_context_paths` through the
   status writer and the supervisor runtime shows none of them is read by dispatch
   code. Normalizing them changes no dispatch behaviour; it corrects what the
   canonical record states about scope. `source_docs` is the exception and is
   genuinely load-bearing — see the resolution-root constraint in section 5.

6. **The status root lags `dev` far enough to constrain task metadata.** The
   Package 10 dispatch-authority documents merged to `dev` on 2026-08-09 are not
   present in the live status root, so they cannot appear in any task's
   `source_docs` without fail-closing that task's dispatch. This will keep biting
   whoever next edits these tasks; the coordinator may want either to advance the
   status root checkout or to make the resolution root explicit in the pack's
   dispatch rules.

---

## 7. Files in this directory

| File | Contents |
|---|---|
| `README.md` | this report |
| `verify_fleet_state.py` | read-only exact-state verifier for the seven self-assertable criteria |
| `verify_fleet_state.out` | verifier output, 7/7 pass |
| `verify_brief_materialization.py` | read-only check that all 11 tasks materialize an execution brief |
| `verify_brief_materialization.out` | materialization output, 11/11 pass |
| `canonical-state-after.json` | post-repair snapshot of all 11 tasks plus the restored record |
| `live-e2e-gate-9c95ecc3.json` | gate artifact from run `31316767710`, the authority for this repair |
| `plan_pack_validator_before.txt` | plan-execution-pack validator baseline |
| `plan_pack_validator_after.txt` | plan-execution-pack validator after repair |
