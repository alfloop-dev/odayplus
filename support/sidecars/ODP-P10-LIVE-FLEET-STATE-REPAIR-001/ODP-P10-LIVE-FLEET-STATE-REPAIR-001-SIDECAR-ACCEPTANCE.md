# Package 10 live fleet state repair acceptance packet

- Sidecar task: `ODP-P10-LIVE-FLEET-STATE-REPAIR-001-SIDECAR-ACCEPTANCE`
- Parent task: `ODP-P10-LIVE-FLEET-STATE-REPAIR-001` (pack order `T00`)
- Helper kind: `acceptance_packet`
- Sidecar owner: Claude2
- Sidecar reviewer: Claude
- Parent owner: Claude (live); parent reviewer: Antigravity4
- Phase: `Package10LiveClosure`
- Prepared: `2026-08-09T14:57Z`
- Prepared at base: `5baa093192450549ff851d69913f6d0b3e5b1a90` (equal to `origin/dev`)
- Live status snapshot read at: `ai-status.json` `updated_at = 2026-08-09T14:57:18Z`

## Scope boundary

This is a support-only acceptance contract, dependency map, and reviewer replay
harness. It does not change L1 canonical truth, live task state, the Package 10
gap analysis, the execution task pack, runtime code, workflows, or governance
contracts. It creates no task, edits no `next` field, and grants no authority.
The parent owner decides whether to compose it into the parent's review. This
packet is neither approval of the parent task nor authority to close it.

## Snapshot volatility warning (read first)

The parent is **mutating live task state while this packet was being prepared**.
Between two reads eight minutes apart, T11, T20, T21, T30, T40 and T41 all
changed `next`, `action`, `phase`, and `mutates_canonical`. The sweep is
proceeding in pack order and had reached T41 at `14:57:17Z`.

Every live-state observation below is therefore a **timestamped moving
target**, not a verdict. The reviewer must re-run § Reviewer replay matrix
against the state that exists at review time and bind approval to that read.
Nothing in § Live-state conformance should be quoted as a defect without
re-measuring first.

## Frozen baseline

Observed at packet preparation time:

- Parent live status: `in_progress`; owner Claude; reviewer Antigravity4;
  `last_update = 2026-08-09T14:42:18Z`.
- Parent branch `task/ODP-P10-LIVE-FLEET-STATE-REPAIR-001`: **absent on
  origin** (`git ls-remote` empty). No parent PR exists.
- Parent evidence directory `docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/`:
  **does not exist**, at the worktree or at `origin/dev`.
- `origin/dev` tip: `5baa093192450549ff851d69913f6d0b3e5b1a90`.
- Parent dependency `ODP-P10-LIVE-GAP-EVIDENCE-REFRESH-20260809`: `done`; the
  refreshed pack carries `evidence_refreshed_at = 2026-08-09T12:43Z` and
  `evidence_base_sha = 4d89bea6…`.

**Consequence.** As of this read the entire repair exists only as live
coordination state. Live task state is mutable, unversioned, and not carried by
any PR. Until T00 produces a committed artifact under its own writable path,
there is nothing for Antigravity4 to review at an exact head and nothing that
survives a subsequent sweep overwriting the same fields. This is the single
most important gap in the packet — see A2 and A7.

## Verified evidence ledger

Re-read directly from GitHub by the preparer, not copied from the pack.

### Deploy Dev run state

| Run | Head SHA | Created (UTC) | Status | Conclusion | Standing |
| --- | --- | --- | --- | --- | --- |
| 31319450627 | `5baa0931…` | 2026-08-09T14:49:12Z | **in_progress** | — | active; `dev` tip; T00 stop condition |
| 31316767710 | `9c95ecc3…` | 2026-08-09T13:48:24Z | completed | `failure` | latest **completed** run; current gate authority |
| 31314125275 | `ebfe128e…` | 2026-08-09T12:46:30Z | completed | `failure` | superseded |
| 31312735093 | `4d89bea6…` | 2026-08-09T12:13:20Z | completed | `failure` | the pack's `evidence_base_sha`; now two runs stale |
| 31312411417 | `188bec54…` | 2026-08-09T12:05:24Z | completed | `cancelled` | superseded cancellation, not a gate verdict |
| 31311664947 | `817d5305…` | 2026-08-09T11:47:34Z | completed | `cancelled` | superseded cancellation, not a gate verdict |

### Live gate artifact at the current authority run

`cloud-run-dev-validation` / `live-e2e-gate.json` from run **31316767710**,
downloaded and parsed by the preparer:

- `ok`: `false`
- `expected_release_sha`: `9c95ecc3e1f2d0885bb4078070a116e852487f69`
- `generated_at`: `2026-08-09T14:07:16Z`
- `correlation_id`: `corr-live-e2e-9c95ecc3e1f2-1786284436`
- `blocking_dependencies`: `external-data`, `mlflow`
- checks: 50 total, 43 `ok`, **7 blockers**

| Check | Dependency | Detail |
| --- | --- | --- |
| `runtime:model_bindings` | mlflow | `mode=mlflow-production-unverified ready=False autoSeeded=False error=forecastops: PRODUCTION_MODEL_REGISTRY_UNAVAILABLE` |
| `runtime:model_capability:forecastops` | mlflow | `available=False reasonCode=PRODUCTION_MODEL_REGISTRY_UNAVAILABLE` |
| `models:registry` | mlflow | `versions=0` |
| `models:forecastops:production_alias` | mlflow | `model=forecast_revenue_interval versionsWithProductionAlias=0 (exactly one required)` |
| `data:ingestion_runs` | external-data | `runs=0` |
| `data:admin_boundary.official_dataset:run_exists` | external-data | no persisted ingestion run for a required live provider |
| `data:poi.commercial_api:run_exists` | external-data | no persisted ingestion run for a required live provider |

**This independently confirms the anchor the parent wrote into the `next`
fields.** The blocker set is byte-identical in `check` name and dependency to
the 7-blocker set at the pack anchor `4d89bea6`; only the SHA, `generated_at`
and `correlation_id` moved. The parent's claim of an "identical 7-blocker set"
is accurate.

**But the anchor is already aging.** Run 31319450627 at `5baa0931` was in
flight during the sweep and is still in flight at packet preparation. When it
completes it becomes the gate authority, and every `next` field the parent just
wrote will name a superseded run. See A3 and § Open risks R1.

## Live-state conformance

Live state read at `updated_at = 2026-08-09T14:57:18Z`. `mc` = declared
`mutates_canonical`. `wp`/`fp` = count of declared writable / forbidden paths.

| Ord | Task id | Status | Owner | Reviewer | action | mc | wp | fp | `next` refreshed | last_update |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T00 | `ODP-P10-LIVE-FLEET-STATE-REPAIR-001` | in_progress | Claude | Antigravity4 | create | true | 2 | 7 | **no** — "Supervisor auto-started…" | 14:42:18Z |
| T10 | `ODP-P10-LIVE-EXTDATA-DIAG-001` | todo | Antigravity5 | Claude2 | create | false | 1 | 10 | yes | 14:54:20Z |
| T11 | `ODP-P10-LIVE-EXTDATA-REMEDIATE-001` | blocked | Claude2 | Antigravity6 | conditional_create | true | 7 | 6 | yes | 14:55:01Z |
| T20 | `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` | blocked | Antigravity | Claude2 | update_existing | true | 3 | 11 | yes | 14:55:28Z |
| T21 | `ODP-PRODUCTION-MODEL-REGISTRY-001` | blocked | Antigravity | Claude | update_existing | true | 3 | 8 | yes | 14:55:54Z |
| T30 | `ODP-P10-DEV-REDEPLOY-VERIFY-001` | blocked | Antigravity3 | Claude2 | update_existing | **true** | 1 | 11 | yes | 14:56:20Z |
| T40 | `ODP-P10-LIVE-VISUAL-PARITY-001` | todo | Claude | Antigravity4 | create | false | 1 | 11 | yes | 14:56:51Z |
| T41 | `ODP-P10-LIVE-LEGACY-RETIREMENT-001` | todo | Claude2 | Antigravity6 | create | false | 1 | 11 | yes | 14:57:17Z |
| T42 | `ODP-PLAN-LIVE-STAGING-PROOF-001` | todo | Antigravity | Claude2 | update_existing_registered | false | 1 | 11 | **no** — "Ownership updated" | 14:46:11Z |
| T50 | `ODP-PLAN-UAT-SIGNOFF-001` | todo | Antigravity | Human/Ops | update_existing_registered | false | 1 | 11 | **no** — "Ownership updated" | 14:46:43Z |
| T60 | `ODP-PLAN-FINAL-GATE-AUDIT-001` | todo | Antigravity2 | Human/Ops | update_existing_registered | false | 1 | 11 | **no** — "Ownership updated" | 14:47:18Z |

Measured invariants at this read:

- All 11 pack ids resolve to exactly one active record; **none** appears in both
  active state and `ai-task-archive/tasks/` (106 records). No duplicate identity.
- The live dependency graph over the 11 roots is **acyclic**.
- All 11 declare `mutates_canonical` explicitly. Presence is complete.
- All 11 carry a non-empty `writable_paths` and `forbidden_paths` list,
  including the six `update_existing` tasks that acceptance item 6 targets.
- No `R3` task id exists in active state; the single archived R3 record
  (`ODP-P10-R3CD-DEV-COMPOSE-001`) was not re-created.

Divergences the reviewer must adjudicate:

1. **T00 owner mismatch (acceptance item 4).** The pack declares
   `owner: Claude2, reviewer: Antigravity4`. Live carries `owner: Claude` with
   `assignment_note: "Helper-claimed by idle Claude; designated reviewer
   Antigravity4 preserved."` The claim is legitimate, but the pack manifest and
   the live manifest now disagree on T00's owner while T00's own acceptance
   requires them to match. T00 cannot silently pass its own criterion; it must
   record the helper claim as the resolution or restore the pack owner.
2. **T30 `mutates_canonical: true` against evidence-only writable paths.**
   Acceptance item 8 binds the value to declared writable paths and requires
   `false` for read-only evidence tasks. T30's only writable path is
   `docs/evidence/runtime/ODP-P10-DEV-REDEPLOY-VERIFY-001/**`. Under the literal
   rule that is `false`. T30 does mutate live runtime by redeploying dev, which
   is a defensible reason for `true` — but that reason must be stated, not
   assumed, or the value flipped. T30 is the only task where the declared value
   and the declared paths disagree.
3. **Pack↔live divergence on `mutates_canonical` for T10, T40, T41.** The
   committed pack JSON declares `true` for all three; live now declares `false`.
   The live values are the ones acceptance item 8 asks for. T00 **cannot** fix
   the pack, because `docs/evidence/fleet_dispatch/**` is outside its
   `writable_paths`. This divergence is therefore authorized-by-necessity and
   must be recorded as such in T00's evidence artifact, with the pack correction
   routed to a named follow-up — not left as a silent contradiction between two
   documents that both claim to be the task manifest.
4. **Action vocabulary is mixed.** T42/T50/T60 still read
   `update_existing_registered` while T20/T21/T30 now read `update_existing`,
   and T40/T41 moved from `register_new` to `create`. If the sweep completes,
   the trailing three should normalize too; if the two vocabularies are
   intentionally distinct, the distinction needs a definition.
5. **Dangling dependency edge (see § Dependency map, D7).**

## Provider-ingestion restoration (acceptance item 2)

This is the criterion most at risk of being scored as met when it is not.

Measured facts:

- `ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001` exists in **neither** live task
  state **nor** the 106-record archive. As a task identity it is gone.
- Its evidence survives in git at `origin/dev`:
  `docs/evidence/runtime/ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001/README.md`
  and `ingestion-runs-evidence.json`.
- T00 restored the finding as **prose inside T10's `next` field** plus a
  `prior_art` pointer to those two files: the worker persists
  `IngestionRunRecord` under `tenant_id=''` via
  `ExternalIngestionService._resolve_store('')` while the API reads
  tenant-scoped under `a11ce505-70bc-56d9-8564-ad22efa23c9e`; that task changed
  no code, so the write/read tenant asymmetry is the leading hypothesis.

The restored content is substantive and correct. The **durability** is the
problem. A `next` field is mutable, unversioned, and overwritten by the next
`progress`, `note`, or `handoff` on that task — this repair itself just
overwrote nine `next` fields, and the six `update_existing` tasks arrived
carrying nothing but "Ownership updated" where prior content used to be.
Storing the only narrative restoration of a deleted task in the field most
likely to be overwritten does not satisfy "restored durably".

The `prior_art` file pointers are durable, because those files are committed at
`dev`. The *linkage* — that this prior art explains the current `data:*`
blockers and is the first hypothesis T10 must test — is not.

**Required for A2:** the restoration must also exist as a committed artifact
under `docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/`, which is
T00's declared writable path. That artifact is what Antigravity4 reviews at an
exact head and what survives the next sweep.

## Dependency map

| # | Authority or input | Parent consumer | Required result | Fail-closed condition |
| --- | --- | --- | --- | --- |
| D1 | Committed pack JSON at `dev` (`…EXECUTION_TASKS_2026-08-09.json`) | the 11 task records T00 writes | same 11 ids, same orders T00–T60, same pack edges present in live | any id created, dropped, or renamed relative to the pack |
| D2 | `ODP-P10-LIVE-GAP-EVIDENCE-REFRESH-20260809` (`done`, merged) | pack `evidence_snapshot` T00 quotes | T00 anchors to the **latest completed** Deploy Dev run, not to the pack's frozen `evidence_base_sha` | anchoring `next` fields to `4d89bea6` when two later completed runs exist |
| D3 | Latest completed Deploy Dev run + `live-e2e-gate.json` | every refreshed `next` field | run id, head SHA, `generated_at`, `correlation_id`, 43/50, and the 7 blockers reproduced verbatim | a blocker dropped, renamed, or summarized; a cancelled run cited as a gate verdict |
| D4 | Active Deploy Dev run 31319450627 | T00 stop condition "active overlapping Deploy Dev mutation" | re-read immediately before handoff and recorded honestly | handing off while a run is in flight without saying so |
| D5 | `ai-task-archive/tasks/` (106 records) | acceptance items 1 and 5 | each of the 11 resolves to exactly one record; no archived task re-created | any id present in both active and archive; any R3 record reopened |
| D6 | Live dependency edges across all 57 active tasks | acceptance item 1 | graph stays acyclic; live edges are a superset of pack edges only where a pre-existing dependency is genuine | a cycle, or a pack edge silently dropped |
| D7 | `ODP-PLAN-OSS-LICENSE-GATE-001` | T60 `depends_on` | the edge resolves, or is removed with a stated reason | the id resolves in **neither** live state nor archive — T60 currently depends on a task that does not exist |
| D8 | `docs/evidence/runtime/ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001/**` at `dev` | acceptance item 2, T10 `prior_art` | restoration committed under T00's writable path, not only in a `next` field | prose-only restoration in mutable live state |
| D9 | T00 `writable_paths` / `forbidden_paths` | T00's own commit scope | only `docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/**` and `.orchestrator/task-briefs/odp_p10_live_*.md` change | any hit under `apps/**`, `modules/**`, `shared/**`, `models/**`, `.github/**`, `docs/design/**`, `docs_archive/**` |
| D10 | Pack `dispatch_rules` and T00 `stop_conditions` | dispatch of T10/T41 | T10 and T41 stay undispatched until T00 is `done` | dispatching a downstream task against half-repaired state |
| D11 | Registered canonical agent roster | acceptance item 4 | every owner/reviewer on the 11 is a registered agent and matches the pack, or the divergence is recorded | T00's own owner silently differing from the pack manifest |

### Intended composition boundary

```text
committed pack JSON (11 tasks, T00-T60)  +  archive (106 records)
        |
        v
T00 sweep over live state via scripts/ai-status.sh
        |
        +--> identity: exactly one resolution per id, none duplicated
        +--> ceilings: writable + forbidden on all 11, no widening
        +--> mutates_canonical: explicit on all 11, matched to paths
        +--> next: current run / SHA / blockers / deps / resume condition
        +--> provider-ingestion prior art relinked to T10
        |
        v
committed evidence artifact under
docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/   <-- MISSING TODAY
        |
        v
task PR -> Antigravity4 exact-head review -> merge to dev
        |
        v
T00 done  ->  only then dispatch T10 and T41
```

The change surface is live coordination state plus one evidence directory and
the generated task briefs. This packet requires no change to
`.orchestrator/supervisor.py`, `scripts/ai_status.py`, workflows, product
runtime, task schemas, or canonical architecture docs.

## Acceptance checklist

Mapped 1:1 onto the parent's eight declared acceptance criteria.

### A1 — Each summarized task has exactly one canonical resolution

- [ ] All 11 pack ids exist in live active state, exactly once each.
- [ ] No pack id appears in both active state and `ai-task-archive/tasks/`.
- [ ] No near-miss duplicate id was created (same work under a second id).
- [ ] Each record's `action` states the resolution actually applied, and the
  vocabulary is consistent across the eleven (see divergence 4).
- [ ] Live `depends_on` contains every pack edge; extra edges are pre-existing
  and justified, not invented by the repair.
- [ ] The live graph over the 11 is acyclic.

### A2 — Missing provider-ingestion work is restored durably

- [ ] The `ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001` finding is recorded in a
  **committed** artifact under `docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/`.
- [ ] That artifact names the tenant asymmetry (`tenant_id=''` write via
  `ExternalIngestionService._resolve_store('')` vs tenant-scoped read under
  `a11ce505-70bc-56d9-8564-ad22efa23c9e`) and states that the task changed no code.
- [ ] It links the two surviving evidence files at `dev`.
- [ ] It states explicitly that the task identity exists in neither active state
  nor the archive, so the evidence files and this artifact are the only record.
- [ ] T10's `prior_art` and `next` reference it, and the restoration does **not**
  depend on the `next` field alone.

### A3 — `next` fields name current run, SHA, blockers, dependencies, resume condition

- [ ] All six `update_existing` tasks (T20, T21, T30, T42, T50, T60) carry a
  refreshed `next`. **T42, T50 and T60 still read "Ownership updated" at
  `14:57Z`** — the sweep had not reached them.
- [ ] T00's own `next` is refreshed. It still reads "Supervisor auto-started…"
  at `14:57Z`; T00 must meet the standard it imposes on the other ten.
- [ ] Every refreshed `next` names the **latest completed** Deploy Dev run and
  its head SHA at write time, with `generated_at` and `correlation_id`.
- [ ] Every refreshed `next` names the task's own blocking checks, its
  dependencies with their current status, and an explicit resume condition.
- [ ] If run 31319450627 (`5baa0931`) completed before handoff, the `next`
  fields either name it or state why the earlier anchor is still authoritative.
- [ ] No `next` cites a `cancelled` run as a gate verdict.

### A4 — Owner and reviewer source manifests match

- [ ] Every owner and reviewer on the 11 is a registered canonical agent.
- [ ] Each live owner/reviewer pair equals the pack pair, **or** the divergence
  is recorded with its reason.
- [ ] T00's own owner divergence (pack `Claude2` vs live `Claude`, helper claim)
  is explicitly resolved — not left as an unremarked failure of T00's own criterion.
- [ ] T11's pack placeholder owner
  (`coordinator_selects_Claude2_or_Antigravity5_from_root_cause`) is recorded as
  resolved to the live owner, with the selection basis stated.
- [ ] Owner ≠ reviewer on every task.

### A5 — Historical R3 implementation tasks are not reopened

- [ ] No task id containing `R3` exists in active state.
- [ ] `ODP-P10-R3CD-DEV-COMPOSE-001` remains archive-only and was not re-created.
- [ ] No archived record was moved back into active state by this repair.
- [ ] Restored *evidence* is cited by path; restored *tasks* are not.

### A6 — Explicit ceilings on every `update_existing` task

- [ ] T20, T21, T30, T42, T50 and T60 each declare a non-empty
  `writable_paths` and a non-empty `forbidden_paths`.
- [ ] No task's writable set is **wider** than what it held before the repair.
- [ ] Each ceiling is consistent with the task's actual deliverable — evidence
  tasks bounded to their own `docs/evidence/runtime/<id>/**`, mutation tasks
  bounded to the named code or model surfaces.
- [ ] Forbidden sets include `docs/evidence/PACKAGE_10_*` for tasks that must
  not rewrite the pack or the gap analysis.
- [ ] T11's conditional writable ceiling is recorded as a ceiling, not a grant.

### A7 — Independent exact-state review passes

- [ ] A commit exists under T00's writable paths carrying
  `LLM-Agent: <live owner>`, `Task-ID: ODP-P10-LIVE-FLEET-STATE-REPAIR-001`,
  `Reviewer: Antigravity4`.
- [ ] A task PR exists and the reviewed head is recorded. **No parent branch
  exists on origin at packet time.**
- [ ] Antigravity4 re-reads live state at review time — not this packet's
  `14:57Z` snapshot — and records the `updated_at` observed.
- [ ] `git diff --name-only origin/dev...HEAD` lists only paths inside
  `docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/**` and
  `.orchestrator/task-briefs/odp_p10_live_*.md`.
- [ ] Active Deploy Dev was re-read immediately before handoff (stop condition
  D4) and the result recorded.
- [ ] Any post-approval state or head movement triggers `re_review`, not a
  carried-forward approval.

### A8 — `mutates_canonical` normalized without widening authority

- [ ] All 11 declare `mutates_canonical` explicitly (satisfied at `14:57Z`).
- [ ] Each value matches the task's declared writable paths: `false` for
  read-only evidence tasks, `true` only where real mutation authority exists.
- [ ] T30's `true` against an evidence-only writable set is either justified in
  writing (dev redeploy mutates live runtime) or flipped to `false`.
- [ ] **No** task's writable paths were widened in order to justify a `true`.
- [ ] The pack↔live divergence for T10, T40, T41 (pack `true`, live `false`) is
  recorded as authorized-by-necessity, with the reason that
  `docs/evidence/fleet_dispatch/**` lies outside T00's writable paths, and the
  pack correction routed to a named follow-up task.

## Reviewer replay matrix

Run at review time. Steps 1–2 read live state, which moves; steps 3–6 read git
and GitHub.

```bash
ROOT="$PANTHEON_STATUS_ROOT"

# 1. Live-state conformance across the 11 (A1, A3, A4, A6, A8)
python3 - "$ROOT" <<'PY'
import json,sys,glob,os
root=sys.argv[1]
d=json.load(open(os.path.join(root,'ai-status.json')))
print('updated_at',d['updated_at'])
by={t['id']:t for t in d['tasks']}
ids=[('T00','ODP-P10-LIVE-FLEET-STATE-REPAIR-001'),('T10','ODP-P10-LIVE-EXTDATA-DIAG-001'),
     ('T11','ODP-P10-LIVE-EXTDATA-REMEDIATE-001'),('T20','ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001'),
     ('T21','ODP-PRODUCTION-MODEL-REGISTRY-001'),('T30','ODP-P10-DEV-REDEPLOY-VERIFY-001'),
     ('T40','ODP-P10-LIVE-VISUAL-PARITY-001'),('T41','ODP-P10-LIVE-LEGACY-RETIREMENT-001'),
     ('T42','ODP-PLAN-LIVE-STAGING-PROOF-001'),('T50','ODP-PLAN-UAT-SIGNOFF-001'),
     ('T60','ODP-PLAN-FINAL-GATE-AUDIT-001')]
arch={os.path.basename(f)[:-5] for f in glob.glob(os.path.join(root,'ai-task-archive/tasks/*.json'))}
for o,i in ids:
    t=by.get(i)
    if not t: print(o,i,'MISSING'); continue
    stale = (t.get('next') or '') .strip() in ('','Ownership updated','Assignment created') \
            or 'Supervisor auto-started' in (t.get('next') or '')
    print(o, t['status'], t['owner'], t['reviewer'],
          'mc='+str(t.get('mutates_canonical','ABSENT')),
          'wp='+str(len(t.get('writable_paths') or [])),
          'fp='+str(len(t.get('forbidden_paths') or [])),
          'STALE_NEXT' if stale else 'next_ok',
          'DUP_IN_ARCHIVE' if i in arch else '')
PY
# expect: 11 rows, no MISSING, no DUP_IN_ARCHIVE, no ABSENT, wp>0, fp>0, no STALE_NEXT

# 2. Graph integrity: acyclic, no dangling edge (A1, D6, D7)
python3 - "$ROOT" <<'PY'
import json,sys,glob,os
root=sys.argv[1]
d=json.load(open(os.path.join(root,'ai-status.json')))
by={t['id']:t for t in d['tasks']}
arch={os.path.basename(f)[:-5] for f in glob.glob(os.path.join(root,'ai-task-archive/tasks/*.json'))}
for t in d['tasks']:
    m=[x for x in (t.get('depends_on') or []) if x not in by and x not in arch]
    if m: print('DANGLING',t['id'],m)
seen=set()
def visit(n,stack):
    if n in stack: print('CYCLE',stack+[n]); return
    if n in seen: return
    seen.add(n)
    for m in by.get(n,{}).get('depends_on',[]) or []:
        if m in by: visit(m,stack+[n])
for i in by: visit(i,[])
print('graph walk complete')
PY
# known at 2026-08-09T14:57Z: DANGLING ODP-PLAN-FINAL-GATE-AUDIT-001 -> ODP-PLAN-OSS-LICENSE-GATE-001

# 3. Gate anchor freshness (A3, D2, D3, D4)
gh run list --workflow="Deploy Dev" --limit 5 \
  --json databaseId,headSha,status,conclusion,createdAt
# the latest COMPLETED run is the gate authority; any in_progress run is stop condition D4

RUN=<latest completed run id>
gh run download "$RUN" -n cloud-run-dev-validation -D /tmp/p10r-gate
python3 -c "import json;d=json.load(open('/tmp/p10r-gate/live-e2e-gate.json'));\
print(d['ok'],d['expected_release_sha'],d['generated_at'],d['correlation_id']);\
print(len(d['checks']),sum(1 for c in d['checks'] if c['ok']));\
print(sorted(b['check'] for b in d['blockers']))"
# at run 31316767710: False 9c95ecc3... 2026-08-09T14:07:16Z corr-live-e2e-9c95ecc3e1f2-1786284436
#                     50 43 / 7 blockers, identical check names to the 4d89bea6 set

# 4. Provider-ingestion durability (A2, D8)
git ls-tree -r --name-only origin/dev -- docs/evidence/runtime/ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001/
git ls-tree -r --name-only origin/dev -- docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/
# the first must list README.md + ingestion-runs-evidence.json
# the second must be NON-EMPTY at review time; it is empty at packet preparation

# 5. Scope conformance (A7, D9)
git diff --name-only origin/dev...HEAD
git diff --check origin/dev...HEAD
# every path must sit under docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/**
# or .orchestrator/task-briefs/odp_p10_live_*.md

# 6. No R3 reopening (A5, D5)
python3 -c "import json,os;root=os.environ['PANTHEON_STATUS_ROOT'];\
d=json.load(open(os.path.join(root,'ai-status.json')));\
print([t['id'] for t in d['tasks'] if 'R3' in t['id'].upper()])"
# expect: []
```

Record the observed `updated_at`, the reviewed HEAD, the changed path list, and
the step 1–3 outputs in the parent review note. Any live-state or head movement
after approval invalidates the result.

## Open risks

- **R1 — Anchor decay.** Run 31319450627 at `5baa0931` was in flight throughout
  the sweep. The moment it completes, every `next` field written between
  `14:54Z` and `14:57Z` names a superseded run. The parent should either wait
  for it and re-anchor, or state in T00's evidence artifact that the anchor is
  the latest run completed at write time and name the in-flight successor.
- **R2 — Live-state-only delivery.** No branch, no PR, no evidence directory. If
  this session ends here, the repair is unreviewable and unattributable, and a
  later sweep can overwrite it silently. A2 and A7 both depend on fixing this.
- **R3 — Partial sweep.** T42, T50, T60 and T00 itself had stale `next` fields
  at `14:57Z`. A handoff before those four are refreshed fails A3.
- **R4 — Dangling T60 edge.** `ODP-PLAN-OSS-LICENSE-GATE-001` resolves nowhere
  and is referenced by both T60 and `ODP-PLAN-AVM-OUTCOME-001-SIDECAR-ACCEPTANCE`.
  T60 can never satisfy its dependency gate as written. This predates the repair
  but sits squarely inside "exactly one canonical resolution" for the eleven; if
  T00 declines to resolve it, it should say so and route it.
- **R5 — Two manifests, one truth.** After normalization the pack JSON and live
  state disagree on `mutates_canonical` for T10, T40 and T41, and on T00's
  owner. Both documents are cited as dispatch authority. Leaving the
  disagreement unrecorded is the failure mode; recording it with a routed
  follow-up is acceptable.

## Sidecar verification record

Commands the preparer actually ran at base `5baa0931`, with observed results:

```text
git rev-parse origin/dev
  -> 5baa093192450549ff851d69913f6d0b3e5b1a90
git ls-remote origin refs/heads/task/ODP-P10-LIVE-FLEET-STATE-REPAIR-001*
  -> empty (no parent branch, no parent PR)
git ls-tree -r origin/dev -- docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/
  -> empty (no committed parent evidence)
git ls-tree -r origin/dev -- docs/evidence/runtime/ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001/
  -> README.md, ingestion-runs-evidence.json  (prior art survives at dev)
gh run list --workflow="Deploy Dev" --limit 6
  -> 31319450627 5baa0931 in_progress; 31316767710 9c95ecc3 failure;
     31314125275 ebfe128e failure; 31312735093 4d89bea6 failure;
     31312411417 / 31311664947 cancelled
gh run download 31316767710 -n cloud-run-dev-validation
  -> live-e2e-gate.json: ok=false, sha 9c95ecc3, generated 2026-08-09T14:07:16Z,
     corr-live-e2e-9c95ecc3e1f2-1786284436, 50 checks / 43 ok / 7 blockers,
     blocking_dependencies=[external-data, mlflow]; blocker check names identical
     to the pack anchor 4d89bea6 set
python3 (live ai-status.json vs pack JSON vs ai-task-archive, two reads
         at 14:49Z and 14:57Z)
  -> 11/11 resolve uniquely; 0 active/archive duplicates; graph acyclic;
     mutates_canonical explicit on 11/11 at 14:57Z (T21 was ABSENT at 14:49Z);
     writable+forbidden non-empty on 11/11;
     T42/T50/T60/T00 next still unrefreshed at 14:57Z;
     dangling edge ODP-PLAN-FINAL-GATE-AUDIT-001 -> ODP-PLAN-OSS-LICENSE-GATE-001;
     no R3 id in active state
```

These prove the baseline, the gate anchor, and the state of the sweep at
`2026-08-09T14:57Z`. They do not prove the repair is complete, and they are not
a substitute for the reviewer's own read at review time.

## Handoff disposition

This packet is ready for Claude to review as a sidecar support artifact, and for
the parent owner to use as `ODP-P10-LIVE-FLEET-STATE-REPAIR-001`'s acceptance
contract and reviewer harness. Completing the sweep, committing the evidence
artifact, re-anchoring to the current completed Deploy Dev run, and composing
this into the mainline remain the parent owner's responsibility. Independent
parent review authority is held by Antigravity4.
