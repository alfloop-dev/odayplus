# Package 10 live gap evidence refresh acceptance packet

- Sidecar task: `ODP-P10-LIVE-GAP-EVIDENCE-REFRESH-20260809-SIDECAR-ACCEPTANCE`
- Parent task: `ODP-P10-LIVE-GAP-EVIDENCE-REFRESH-20260809`
- Helper kind: `acceptance_packet`
- Sidecar owner: Claude
- Sidecar reviewer: Claude2
- Parent owner: Claude; parent reviewer: Antigravity
- Phase: `Package10LiveClosure`
- Prepared: `2026-08-09T12:47Z`
- Prepared at base: `4d89bea64ce98753895a16194e320c9a8ea45852` (equal to `origin/dev`)

## Scope boundary

This is a support-only acceptance checklist, evidence ledger, and dependency
map. It does not change L1 canonical truth, the Package 10 gap analysis, the
execution task pack, live task state, runtime code, workflows, or governance
contracts. The parent owner decides whether to compose this packet into the
parent's review. This packet is neither approval of the parent task nor
authority to close it.

## Frozen parent baseline

Observed while this sidecar was `in_progress` on `2026-08-09`:

- Parent live status: `in_progress`; owner Claude; reviewer Antigravity.
- Parent `base_sha`: `4d89bea64ce98753895a16194e320c9a8ea45852`.
- Parent branch `task/ODP-P10-LIVE-GAP-EVIDENCE-REFRESH-20260809`: **absent on
  origin**; no parent PR exists yet.
- Parent target artifacts exist at `origin/dev` tip `4d89bea6` and still carry
  the pre-burst evidence snapshot (see § Stale anchor inventory).
- Parent's own parent: `ODP-P10-LIVE-GAP-DISPATCH-20260809`, PR 745, merged.

This is a **pre-implementation contract packet**. It must not be read as
evidence that the refresh already exists. Once the parent produces a commit,
the reviewer must bind acceptance to that exact HEAD and re-run the replay
matrix below. Any parent HEAD movement or base advance invalidates a prior
approval.

## Verified evidence ledger

All rows below were re-read from GitHub and from the run artifact at packet
preparation time; they are the facts the refreshed documents must reproduce.

### Deploy Dev runs in the merge burst

| Run | Head SHA | Created (UTC) | Status | Conclusion | Required classification |
| --- | --- | --- | --- | --- | --- |
| 31311664947 | `817d53052e23cf867085342fcafa340743e4a7cb` | 2026-08-09T11:47:34Z | completed | `cancelled` | superseded cancellation, not a gate verdict |
| 31312411417 | `188bec5411846fcb7439fb63991daadad7fee60f` | 2026-08-09T12:05:24Z | completed | `cancelled` | superseded cancellation, not a gate verdict |
| 31312735093 | `4d89bea64ce98753895a16194e320c9a8ea45852` | 2026-08-09T12:13:20Z | completed | `failure` | **latest completed run**; sole live-gate authority |

Run 31312735093 job results: `e2e-operational-evidence` = `success`,
`deploy` = `failure`. The operational-evidence job succeeding is what makes the
gate artifact readable; it is not a release pass.

### Live gate artifact at `4d89bea6`

Artifact `cloud-run-dev-validation` (id `9038077081`, not expired),
file `live-e2e-gate.json`:

- `ok`: `false`
- `expected_release_sha`: `4d89bea64ce98753895a16194e320c9a8ea45852`
- `expected_deployment`: `dev`
- `generated_at`: `2026-08-09T12:32:33Z`
- `correlation_id`: `corr-live-e2e-4d89bea64ce9-1786278753`
- `blocking_dependencies`: `external-data`, `mlflow`
- checks: 50 total, 43 `ok`, 7 failing

Exact blockers (7), verbatim `check` names:

| Check | Dependency | Detail |
| --- | --- | --- |
| `runtime:model_bindings` | mlflow | `mode=mlflow-production-unverified ready=False autoSeeded=False error=forecastops: PRODUCTION_MODEL_REGISTRY_UNAVAILABLE` |
| `runtime:model_capability:forecastops` | mlflow | `available=False reasonCode=PRODUCTION_MODEL_REGISTRY_UNAVAILABLE` |
| `models:registry` | mlflow | `versions=0` |
| `models:forecastops:production_alias` | mlflow | `model=forecast_revenue_interval versionsWithProductionAlias=0 (exactly one required)` |
| `data:ingestion_runs` | external-data | `runs=0` |
| `data:admin_boundary.official_dataset:run_exists` | external-data | no persisted ingestion run for a required live provider |
| `data:poi.commercial_api:run_exists` | external-data | no persisted ingestion run for a required live provider |

Load-bearing passes at this SHA (the ones the gap register relies on):

- `release:platform_version` — actual equals expected `4d89bea6…`
- `runtime:readiness`, `runtime:no_blocking_reasons` (`api-runtime`)
- `runtime:persistence` — `postgresql durable=True reachable=True`
- `runtime:data_origin` — `mode=live origin=authoritative operatorReady=True`
- `runtime:provider_probe:{admin_boundary.official_dataset, geocode.primary_api, poi.commercial_api}` — connectivity, auth, schema all true
- `auth:anonymous_denied` (401), `auth:operator_bootstrap` (200),
  `auth:operator_bootstrap:provenance` (`data_mode=live`,
  `data_source=operator-shell-production`, `surrogatePaths=none`),
  `auth:web_operator_requires_login` (307 → `/login?returnTo=%2Foperator`)
- `worker:enqueue`, `worker:idempotent_replay`, `worker:drain_trigger`,
  `worker:terminal_success`, `worker:ingestion_probe:poi.commercial_api`
- `audit:durable_receipt`, `audit:idempotent_replay_receipt`,
  `audit:receipt_integrity`
- `models:{avm,heatzone,sitescore}:no_fabricated_alias`, and the four
  `*:no_surrogate_markers` checks

Candidate binding at this SHA is still candidate-scoped: the smoke artifact
records `api_url = https://candidate-4d89bea64ce98753---oday-api-…run.app`.
GAP-05 (candidate proven, public binding unproven) therefore remains open on
its own terms and must not be silently downgraded by this refresh.

### Delta against the currently committed snapshot

The committed artifacts describe run `31308339896` at `9e5434cd…`. The refresh
must move to `31312735093` at `4d89bea6…` **and** carry these substantive
deltas, not just swap identifiers:

| Field | Committed (stale) | Verified at `4d89bea6` |
| --- | --- | --- |
| `last_completed_deploy_run` | `31308339896` | `31312735093` |
| `last_completed_deploy_sha` | `9e5434cd8a9f798769f4891c3610280a7982a175` | `4d89bea64ce98753895a16194e320c9a8ea45852` |
| `last_completed_deploy_result` | `failure_rolled_back` | `failure` (deploy job failed; classify from the run, do not re-assert rollback without evidence) |
| `live_gate_generated_at` | `2026-08-09T10:46:11Z` | `2026-08-09T12:32:33Z` |
| `live_gate_correlation_id` | `corr-live-e2e-9e5434cd8a9f-1786272371` | `corr-live-e2e-4d89bea64ce9-1786278753` |
| `candidate_failures` | 5 entries | **7 blockers** — adds `runtime:model_bindings` and `runtime:model_capability:forecastops` |
| `active_deploy_run_at_finalization` | `null` | must be re-read immediately before handoff |

The failure count growing from 5 to 7 is the single most important delta in
this refresh. A refresh that only rewrites SHAs and run ids, and leaves the
five-item failure list intact, fails acceptance criterion 2.

### Stale anchor inventory

Every occurrence of the superseded snapshot that a complete refresh must
resolve (line numbers at base `4d89bea6`):

| File | Lines carrying `9e5434cd` / `31308339896` / old correlation id |
| --- | --- |
| `docs/evidence/PACKAGE_10_LIVE_COMPLETION_GAP_ANALYSIS_2026-08-09.md` | 7, 8, 9, 71, 91 |
| `docs/evidence/fleet_dispatch/PACKAGE_10_LIVE_COMPLETION_EXECUTION_TASKS_2026-08-09.md` | 6, 484, 495, 496 |
| `docs/evidence/fleet_dispatch/PACKAGE_10_LIVE_COMPLETION_EXECUTION_TASKS_2026-08-09.json` | 7, 20, 21, 24, 627, 634 |

`8ec12c02` (public rollback release) occurrences are a **separate** claim about
public promotion, not part of the deploy-run snapshot. Do not rewrite them from
the Deploy Dev artifact; either re-verify the public readback independently or
leave GAP-01/GAP-05 wording unchanged and say so.

### Section 8.3 ground truth

Current §8.3 of the execution-tasks Markdown asserts: *"The JSON marks all 11
tasks `mutates_canonical: true`, including the read-only evidence tasks T10,
T40, and T41."* Measured against the committed JSON, that is wrong twice over:

- 8 of 11 tasks carry `mutates_canonical: true` — T00, T10, T11, T20, T21,
  T30, T40, T41.
- 3 tasks omit the field entirely — `ODP-PLAN-LIVE-STAGING-PROOF-001` (T42),
  `ODP-PLAN-UAT-SIGNOFF-001` (T50), `ODP-PLAN-FINAL-GATE-AUDIT-001` (T60).
- The Markdown declares `Mutates canonical coordination state:` for T00 only
  (line 81); the other ten task entries carry no such line at all.

Normalization is already owned elsewhere: T00's acceptance item 8 makes
`ODP-P10-LIVE-FLEET-STATE-REPAIR-001` responsible for supplying explicit
writable-path ceilings and forbidden-path sets for the six `update_existing`
tasks (T20, T21, T30, T42, T50, T60), without silently widening any existing
task's authority. §8.3 should therefore be corrected to state the real field
presence and route the normalization to T00 — **not** to change the eleven
tasks' metadata inside this evidence refresh.

## Dependency map

| Authority or input | Parent consumer | Required result | Fail-closed condition |
| --- | --- | --- | --- |
| Deploy Dev run list for `dev` | run classification in gap analysis §2 and pack §8 | 31311664947 and 31312411417 recorded as superseded cancellations; 31312735093 named the latest completed run | A `cancelled` run must never be rendered as a gate verdict, pass, or failure cause |
| `cloud-run-dev-validation` / `live-e2e-gate.json` at `4d89bea6` | `evidence_snapshot`, GAP-02/03/04, candidate failure list | All 7 blockers and the named passes reproduced verbatim by `check` name | Any blocker dropped, renamed, or summarized away fails acceptance 2 |
| `cloud-run-smoke.json` candidate URL | GAP-01/GAP-05 wording | Candidate-scoped binding stays distinguished from public promotion | Candidate evidence must not be promoted into a public-binding claim |
| Public runtime readback (`8ec12c02`, `503`) | GAP-01, GAP-05, exit criteria | Either independently re-verified at refresh time, or explicitly left unchanged with that stated | Silent reuse of an unverified public claim inside a "refreshed" document |
| Markdown ↔ JSON task set | pack §2, §3, §4 and JSON `tasks[]` | Same 11 task ids, same edges, graph still acyclic with no external node | Any added/removed task, new edge, or cycle fails acceptance 3 |
| JSON `mutates_canonical` field presence | pack §8.3 | §8.3 restated as 8 present / 3 absent, with normalization assigned to T00 | Editing the 11 tasks' metadata here instead of correcting the observation |
| `writable_paths` / `forbidden_paths` on the parent task | parent commit scope | Only the three declared evidence artifacts change | Any hit under `apps/**`, `modules/**`, `shared/**`, `models/**`, `.github/**`, `docs_archive/**`, `scripts/**`, `tests/**` blocks the commit |
| Active Deploy Dev at finalization | coordination rule 3, `active_deploy_run_at_finalization` | Re-read immediately before handoff; recorded honestly | A run in flight at handoff means the snapshot is already at risk of being stale |
| Parent coordination rule 1 | T00 dispatch | T00 stays undispatched until this refresh merges to `dev` | Dispatching T00 against the stale pack |

### Intended composition boundary

```text
GitHub Deploy Dev run list (3 runs in the burst)
        |
        v
latest completed run 31312735093 @ 4d89bea6 ---- cancelled runs marked superseded
        |
        v
live-e2e-gate.json artifact  ->  7 blockers / 43 passes
        |
        v
evidence_snapshot (JSON)  ==  gap analysis §2  ==  pack §8 narrative
        |
        v
unchanged: 11 tasks, acyclic edges, per-task metadata
        |
        v
§8.3 observation corrected -> normalization deferred to T00
        |
        v
Antigravity exact-head review -> merge to dev -> only then dispatch T00
```

The change surface is limited to the three declared evidence artifacts. This
packet does not require any change to `.orchestrator/supervisor.py`,
`scripts/ai_status.py`, workflows, product runtime, task schemas, or canonical
architecture docs.

## Acceptance checklist

Mapped 1:1 onto the parent's six declared acceptance criteria.

### A1 — Superseded cancellations recorded

- [ ] Run 31311664947 at `817d5305…` appears as a **cancelled, superseded** run.
- [ ] Run 31312411417 at `188bec54…` appears as a **cancelled, superseded** run.
- [ ] Neither cancelled run is cited anywhere as a live-gate pass or failure.
- [ ] The reason for cancellation (superseded by the next push in the burst) is
  stated, not implied.

### A2 — Latest completed run reproduced exactly

- [ ] `31312735093` at `4d89bea64ce98753895a16194e320c9a8ea45852` is named the
  latest completed Deploy Dev run.
- [ ] All **7** blockers appear with their exact `check` names.
- [ ] `blocking_dependencies` reads `external-data`, `mlflow`.
- [ ] `correlation_id` is `corr-live-e2e-4d89bea64ce9-1786278753` and
  `generated_at` is `2026-08-09T12:32:33Z`.
- [ ] The pass set is refreshed too — release SHA equality, readiness,
  persistence, data origin, three provider probes, four auth checks, five
  worker checks, three audit checks, no-fabricated-alias and no-surrogate
  checks.
- [ ] The stale five-item failure list is gone; `runtime:model_bindings` and
  `runtime:model_capability:forecastops` are present.
- [ ] `last_completed_deploy_result` matches the observed run conclusion and
  does not assert a rollback that this run's evidence does not show.

### A3 — Same 11 tasks, still acyclic

- [ ] Markdown and JSON both still describe exactly 11 tasks, T00–T60.
- [ ] Task ids, owners, reviewers, and entry conditions are unchanged.
- [ ] The dependency graph is acyclic and every `depends_on` target resolves
  inside the pack (no external node).
- [ ] The §2 ASCII graph and JSON `depends_on` edges still agree, including
  T41's two-phase split and T50's three-way requirement.

### A4 — §8.3 corrected

- [ ] §8.3 no longer claims all 11 tasks are marked `mutates_canonical: true`.
- [ ] It states the real presence: 8 tasks `true`; T42, T50, T60 omit the field.
- [ ] It records that the Markdown declares the coordination-state line for T00
  only.
- [ ] Normalization responsibility is explicitly assigned to T00 (its acceptance
  item 8), not performed inside this refresh.
- [ ] The 11 tasks' `mutates_canonical` values are **not** edited by this task.

### A5 — No runtime, workflow, or archive mutation

- [ ] `git diff --name-only origin/dev...HEAD` lists only the three declared
  evidence artifacts.
- [ ] No path under `apps/**`, `modules/**`, `shared/**`, `models/**`,
  `.github/**`, `docs_archive/**`, `scripts/**`, `tests/**` is touched.
- [ ] No live status file, task archive, or generated mirror is hand-edited.
- [ ] Markdown and JSON remain mutually consistent after the edit (JSON still
  parses; document-level `base_sha` and prepared metadata agree between peers).

### A6 — Independent exact-head review

- [ ] The parent commit carries `LLM-Agent: Claude`, `Task-ID:
  ODP-P10-LIVE-GAP-EVIDENCE-REFRESH-20260809`, `Reviewer: Antigravity`.
- [ ] Antigravity reviews the **exact** new head, and the approved SHA is
  recorded.
- [ ] Any post-approval head movement triggers `re_review` rather than a
  carried-forward approval.
- [ ] Active Deploy Dev was re-read immediately before handoff (coordination
  rule 3) and the result recorded.
- [ ] T00 is not dispatched until this refresh is merged to `dev`
  (coordination rule 1).

## Reviewer replay matrix

Run at the exact parent review HEAD:

```bash
# 1. Run classification — expect cancelled / cancelled / failure
for r in 31311664947 31312411417 31312735093; do
  gh run view "$r" --json databaseId,headSha,status,conclusion,createdAt
done

# 2. Live-gate artifact for the latest completed run
gh run download 31312735093 -n cloud-run-dev-validation -D /tmp/p10-gate
python3 -c "import json;d=json.load(open('/tmp/p10-gate/live-e2e-gate.json'));\
print(d['ok'],d['expected_release_sha'],d['generated_at'],d['correlation_id']);\
print(sorted(b['check'] for b in d['blockers']));\
print(len(d['checks']),sum(1 for c in d['checks'] if c['ok']))"
# expect: False 4d89bea6... 2026-08-09T12:32:33Z corr-live-e2e-4d89bea64ce9-1786278753
#         7 blocker names; 50 checks, 43 ok

# 3. Task-set and acyclicity invariant on the refreshed JSON
python3 -c "import json;d=json.load(open('docs/evidence/fleet_dispatch/PACKAGE_10_LIVE_COMPLETION_EXECUTION_TASKS_2026-08-09.json'));\
t=d['tasks'];ids={x['task_id'] for x in t};dep={x['task_id']:x['depends_on'] for x in t};\
print(len(t), all(m in ids for v in dep.values() for m in v));\
print(sum(1 for x in t if x.get('mutates_canonical') is True), [x['task_id'] for x in t if 'mutates_canonical' not in x])"
# expect: 11 True / 8 ['ODP-PLAN-LIVE-STAGING-PROOF-001','ODP-PLAN-UAT-SIGNOFF-001','ODP-PLAN-FINAL-GATE-AUDIT-001']

# 4. Scope conformance
git diff --name-only origin/dev...HEAD
git diff --check origin/dev...HEAD

# 5. No stale anchors survive
grep -rn "9e5434cd\|31308339896\|corr-live-e2e-9e5434cd8a9f" \
  docs/evidence/PACKAGE_10_LIVE_COMPLETION_GAP_ANALYSIS_2026-08-09.md \
  docs/evidence/fleet_dispatch/PACKAGE_10_LIVE_COMPLETION_EXECUTION_TASKS_2026-08-09.md \
  docs/evidence/fleet_dispatch/PACKAGE_10_LIVE_COMPLETION_EXECUTION_TASKS_2026-08-09.json
# any hit must be a deliberate, labelled historical reference

# 6. Coordination rule 3 — no run in flight at handoff
gh run list --workflow="Deploy Dev" --limit 5 \
  --json databaseId,headSha,status,conclusion,createdAt
```

Record the exact reviewed HEAD, the changed path list, and step 2/3 outputs in
the parent review note. Any HEAD movement invalidates the result.

## Sidecar verification record

Commands the preparer actually ran at base `4d89bea6`, with observed results:

```text
gh run view {31311664947,31312411417,31312735093}
  -> completed/cancelled, completed/cancelled, completed/failure   (as tabled)
gh api repos/:owner/:repo/actions/runs/31312735093/artifacts
  -> cloud-run-dev-validation id=9038077081 expired=false
gh run download 31312735093 -n cloud-run-dev-validation
  -> live-e2e-gate.json: ok=false, 50 checks, 43 ok, 7 blockers,
     blocking_dependencies=[external-data, mlflow]
python3 (acyclicity + field presence on committed JSON)
  -> 11 nodes, acyclic, no external dep;
     mutates_canonical true=8; absent=[T42, T50, T60]
git ls-remote origin refs/heads/task/ODP-P10-LIVE-GAP-EVIDENCE-REFRESH-20260809
  -> empty (no parent branch yet)
```

These prove the baseline facts and the pre-implementation state. They do not
prove any refresh has been applied; the parent's own edit and Antigravity's
exact-head review remain outstanding.

## Handoff disposition

This packet is ready for Claude2 to review as a sidecar support artifact, and
for the parent owner to use as the parent task's acceptance contract.
Implementation of the refresh, exact-head verification, and composition into
the mainline remain the parent owner's responsibility; independent parent
review authority is held by Antigravity.
