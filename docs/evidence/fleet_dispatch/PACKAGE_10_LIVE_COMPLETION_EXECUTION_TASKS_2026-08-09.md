# Package 10 Live Completion Execution Tasks

- Program ID: `ODP-P10-LIVE-CLOSURE-R1`
- Status: `PREPARED_NO_GO`
- Prepared at: `2026-08-09T10:49:01Z`
- Base SHA: `9e5434cd8a9f798769f4891c3610280a7982a175`
- Program owner: `CodexCoordinator`
- Preferred execution pools: `Claude`, then `Antigravity`; Codex is fallback/integration only
- Dispatch authority: this file and its JSON peer after merge to `dev`
- Gap authority: `docs/evidence/PACKAGE_10_LIVE_COMPLETION_GAP_ANALYSIS_2026-08-09.md`
- Execution model: dependency-gated parallel lanes with exact-head independent review

## 1. Dispatch Rules

1. Do not dispatch a task from an uncommitted worktree or chat summary.
2. Every worker must read this pack, its JSON peer, the gap analysis, and the task-specific sources.
3. Source-doc materialization must record identical 64-hex SHA-256 values in owner and reviewer workspaces.
4. Owner and reviewer must be distinct logical agents and distinct worker runs.
5. A worker may edit only declared writable paths and perform only declared runtime actions.
6. A task that discovers a code/config defect outside scope stops and opens the named conditional remediation task.
7. A task that needs human authority records a handback packet and blocks; it does not infer approval.
8. No task may push or merge `dev` while an exact-SHA Deploy Dev run is active.
9. Before merge or runtime mutation, compare current assignments, PRs, release runs, model aliases, and provider runs for other-LLM overlap.
10. Completion requires durable evidence, exact head, CI, independent review, merged ancestry when applicable, and canonical status closeout.

## 2. Dependency Graph

```text
T00 Fleet state repair
  ├── T10 External-data diagnosis
  │     └── T11 Conditional external-data remediation
  │             └── T30 Exact-SHA deploy and API verification
  └── T20 Forecast history backfill (parallel; human source gate)
        └── T21 ForecastOps MLflow release (human approval gate)
                └── T30 Exact-SHA deploy and API verification

T30 successful public promotion
  ├── T40 Package 10 visual/API parity
  ├── T41 Legacy visual retirement
  └── T42 Live staging proof
          └── T50 UAT packet and human signoff
                  └── T60 Final gate audit and release decision
```

T10 may close T11 as `not_required` only when it proves that no code/config
change is needed and produces authentic persisted runs through the supported
runtime path. Otherwise T11 is mandatory.

## 3. Task Summary

| Order | Task ID | Action | Automation | Preferred owner | Reviewer | Entry condition |
|---:|---|---|---|---|---|---|
| T00 | `ODP-P10-LIVE-FLEET-STATE-REPAIR-001` | Create | Auto now | Claude2 | Antigravity4 | Pack merged to `dev` |
| T10 | `ODP-P10-LIVE-EXTDATA-DIAG-001` | Create | Auto now | Antigravity5 | Claude2 | T00 done; no active Deploy Dev |
| T11 | `ODP-P10-LIVE-EXTDATA-REMEDIATE-001` | Conditional create | Auto after diagnosis | Claude2 or Antigravity5 | Antigravity6 | T10 names exact root cause |
| T20 | `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` | Update existing | Hybrid | Antigravity | Claude2 | T00 done; source inventory can start immediately |
| T21 | `ODP-PRODUCTION-MODEL-REGISTRY-001` | Update existing | Hybrid | Antigravity | Claude | T20 has sufficient authoritative history |
| T30 | `ODP-P10-DEV-REDEPLOY-VERIFY-001` | Update existing | Auto after dependencies | Antigravity3 | Claude2 | T10/T11 and T21 done |
| T40 | `ODP-P10-LIVE-VISUAL-PARITY-001` | Create | Auto after deploy | Claude | Antigravity4 | T30 public promotion passed |
| T41 | `ODP-P10-LIVE-LEGACY-RETIREMENT-001` | Create | Auto; static half can start early | Claude2 | Antigravity6 | Pack merged; runtime half waits T30 |
| T42 | `ODP-PLAN-LIVE-STAGING-PROOF-001` | Update existing | Auto after deploy | Antigravity | Claude2 | T30 passed |
| T50 | `ODP-PLAN-UAT-SIGNOFF-001` | Update existing | Auto evidence + human signoff | Antigravity | Human/Ops | T40/T41/T42 passed |
| T60 | `ODP-PLAN-FINAL-GATE-AUDIT-001` | Update existing | Auto audit + human release decision | Antigravity2 | Human/Ops | T50 human signoff recorded |

## 4. Execution Tasks

### T00 - `ODP-P10-LIVE-FLEET-STATE-REPAIR-001`

- Title: Repair canonical Package 10 live-closure task truth
- Priority: `P0`
- Automation: `AUTO_NOW`
- Owner: `Claude2`
- Reviewer: `Antigravity4`
- Mutates canonical coordination state: yes
- Product code mutation: no

Objective:

Restore a durable, current Package 10 closure graph before any runtime work.
Create T10 and T41, update the existing T20/T21/T30/T42/T50/T60 tasks, and
remove stale `next` text without rewriting historical task archives.

Required sources:

- `docs/evidence/PACKAGE_10_LIVE_COMPLETION_GAP_ANALYSIS_2026-08-09.md`
- this Markdown file and JSON peer;
- `docs/design/PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.md`;
- `docs/evidence/PACKAGE_10_PAGE_BY_PAGE_RUNTIME_DIFF_2026-07-26.md`;
- Package 10 archived ZIP manifest.

Writable paths/state:

- canonical `ai-status.json` through `scripts/ai-status.sh` only;
- generated `.orchestrator/task-briefs/odp_p10_live_*.md`;
- `docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/**`.

Forbidden:

- `apps/**`, `modules/**`, `shared/**`, `models/**`, `.github/**`;
- direct JSON edits to the status file;
- reopening archived historical R3 implementation tasks;
- deleting unrelated tasks or handoffs.

Acceptance:

1. Every task in the summary has one canonical active/archive resolution and no duplicate active ID.
2. Existing tasks retain their history and are updated with current blockers, dependencies, source docs, and resume conditions.
3. T10 and T41 are created with the exact metadata in this pack.
4. Missing provider-ingestion work is no longer absent from both active and archive state.
5. Owner/reviewer source manifests match and include this pack, its JSON peer, the gap analysis, page diff, and ZIP manifest.
6. Supervisor restart is not required unless tracked control-plane bytes differ; any restart has a rollback receipt.
7. A Package 10 dispatch probe reaches separate owner/reviewer workspaces and reads every source.
8. Independent exact-state review passes before closeout.

Stop conditions:

- active Deploy Dev run touching the same exact release graph;
- another LLM concurrently mutating any target task;
- status writer or archive ambiguity;
- any need for destructive state recovery.

### T10 - `ODP-P10-LIVE-EXTDATA-DIAG-001`

- Title: Diagnose successful worker probes with zero persisted ingestion runs
- Priority: `P0`
- Automation: `AUTO_NOW`
- Owner: `Antigravity5`
- Reviewer: `Claude2`
- Product code mutation: no
- Runtime mutation: read-only until write/read identity is proven

Objective:

Explain why Deploy Dev reports successful enqueue and worker execution while
the authenticated API returns zero ingestion runs. Determine whether the root
cause is tenant/schema/store mismatch, transaction/persistence wiring, release
drift, unsupported runtime invocation, or genuinely absent provider output.

Writable paths:

- `docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/**` only.

Forbidden:

- all product, test, workflow, model, design, archive, and control-plane code;
- direct SQL writes;
- provider secret mutation;
- marking empty/failed runs successful.

Runtime actions:

1. Capture redacted exact-SHA Deploy Dev artifacts and Cloud Logging.
2. Resolve worker job/execution IDs, job payloads, tenant scope, release SHA, and correlation IDs.
3. Prove API and worker use the same Secret Manager database reference without exposing its value.
4. Compare PG16 tenant/schema tables for `fetch_runs`, `ingestion_runs`, snapshots, watermarks, audit events, and job records using read-only queries.
5. Trace `ExternalIngestionService` and persistence factory selection from deployed release evidence.
6. If identity is equal, invoke one supported governed run per required snapshot provider and read it back through the authenticated API.

Acceptance:

1. The contradiction is explained by one evidence-backed root cause, not a guess.
2. Release SHA, tenant, schema, store, correlation ID, and provider IDs are named and redacted.
3. `admin_boundary.official_dataset` and `poi.commercial_api` each have a disposition.
4. If runtime-only closure is possible, each provider produces a non-empty `SUCCEEDED` run with snapshot, window, counts, DQ, watermark, and audit lineage readable through the API.
5. If code/config remediation is required, T11 receives exact reproducer, writable paths, forbidden paths, acceptance, and rollback scope; T10 blocks instead of patching.
6. Independent reviewer confirms no direct DB write, fake data, or secret disclosure.

### T11 - `ODP-P10-LIVE-EXTDATA-REMEDIATE-001`

- Title: Correct diagnosed external-data persistence/readback defect
- Priority: `P0`
- Automation: `CONDITIONAL_AUTO_REMEDIATION`
- Owner selection:
  - `Claude2` for application/persistence/config code;
  - `Antigravity5` for runtime-only configuration or governed execution.
- Reviewer: `Antigravity6`
- Creation condition: T10 proves remediation is required.

The coordinator must copy T10's exact root cause into this task. Generic
phrases such as `run ingestion` or `fix persistence` are insufficient.

Default writable-path ceiling:

- `modules/external_data/application/**`;
- `modules/external_data/workers/**`;
- `shared/infrastructure/persistence/**external_data**`;
- `apps/worker/oday_worker/**`;
- `apps/api/app/routes/external_data.py`;
- focused tests matching the diagnosed path;
- task evidence directory.

Forbidden unless T10 explicitly proves necessity and the coordinator amends scope:

- Package 10 web UI, design/archive, model code, auth/RBAC, deployment workflow;
- direct production database repair;
- weakening live E2E assertions.

Acceptance:

1. A deterministic regression reproduces worker success with missing API run.
2. The smallest scoped change makes worker and API persist/read the same durable run.
3. Real candidate execution produces both required provider runs with lineage.
4. Retry, idempotency, DQ quarantine, tenant isolation, audit, and failure classification remain fail closed.
5. Focused, integration, live-gate, Ruff, diff, and exact-head CI pass.
6. Independent exact-head review and rollback evidence pass before merge.

### T20 - `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001`

- Action: update existing task; do not create a duplicate.
- Priority: `P0`
- Automation: `HYBRID_HUMAN_GATE`
- Owner: `Antigravity`
- Reviewer: `Claude2`

Auto-worker phase A can start now:

1. inventory all already accessible authoritative transaction sources;
2. report tenant/store/date/lineage coverage and missing canonical windows;
3. generate a Human/Ops handback packet only for unavailable source authority;
4. if an authorized immutable source already exists, run the governed backfill and prove point-in-time safety.

Human input required only when the source is not already authorized/accessible:

- source location and immutable snapshot/hash;
- owner and permitted-use attestation;
- tenant/store scope and cutoff;
- discrepancy decision for records requiring business authority.

Acceptance additions:

1. At least continuous canonical 7/14/28-day windows exist per eligible store.
2. No fixture, synthetic, duplicated-date, repeated-window, auto-seed, or immature label enters production eligibility.
3. Before/after counts, dates, source hashes, eligibility query, and lineage are durable.
4. If Human/Ops input is missing, the task blocks with a field-complete handback packet, not a generic `waiting_for=Human/Ops`.

### T21 - `ODP-PRODUCTION-MODEL-REGISTRY-001`

- Action: update existing task; do not create a duplicate.
- Priority: `P0`
- Automation: `HYBRID_HUMAN_GATE`
- Owner: `Antigravity`
- Reviewer: `Claude`
- Depends on: T20 sufficient-history receipt.

Auto worker may train, evaluate, create model card/lineage/rollback evidence,
register a candidate, and run shadow inference. A human model/data owner must
approve the exact model version before the worker assigns the `production`
alias.

Acceptance additions:

1. The current exact-dev gate is the authority for required active capabilities.
2. ForecastOps has exactly one approved `production` alias.
3. Alias movement cites a durable human approval for the exact version.
4. Runtime inference, rollback, temporal validation, and no-auto-seed evidence pass.
5. Other capabilities remain governed-disabled unless current exact-dev contracts and authentic data support activation.

### T30 - `ODP-P10-DEV-REDEPLOY-VERIFY-001`

- Action: update and resume existing task; do not create a duplicate.
- Priority: `P0`
- Automation: `AUTO_AFTER_DEPENDENCY`
- Owner: `Antigravity3`
- Reviewer: `Claude2`
- Depends on: T10/T11 external-data closure and T21 model closure.

Required metadata correction:

- replace reassignment-only `next` text with exact current blockers, latest run,
  exact SHA, dependencies, and resume condition;
- clear `waiting_for=Human/Ops` only when the named Human/Ops inputs are durably
  satisfied through T20/T21.

Acceptance:

1. Deploy Dev runs on the current exact `origin/dev` SHA with no concurrent dev merge.
2. Build, migration, worker, scheduler, smoke, authenticated live E2E, and rollback checks pass.
3. API/Web public versions, image labels, Cloud Run revisions, and target SHA agree.
4. Public `/platform/health` is healthy and traffic remains on the candidate after validation.
5. Operator bootstrap is live, tenant-correct, non-placeholder, and fail closed for invalid access.
6. Required provider runs and ForecastOps production alias are visible through deployed APIs.
7. Failure produces evidence and rollback, never a product patch in this task.

### T40 - `ODP-P10-LIVE-VISUAL-PARITY-001`

- Title: Verify all Package 10 screens against the promoted canonical HTML
- Priority: `P0`
- Automation: `AUTO_AFTER_DEPENDENCY`
- Owner: `Claude`
- Reviewer: `Antigravity4`
- Depends on: T30.
- Product mutation: no; findings create scoped remediation tasks.

Writable paths:

- `docs/evidence/runtime/ODP-P10-LIVE-VISUAL-PARITY-001/**`.

Runtime actions:

- authenticated Playwright/agent-browser verification;
- desktop, tablet, and mobile screenshots;
- DOM, layout, overflow, console, network, accessibility, and API correlation;
- compare all 40 screen/state contracts to the archived Package 10 HTML and page diff.

Acceptance:

1. Inventory all 40 screen contracts with route/workspace/state/viewport/result.
2. Capture exact-SHA screenshots for required desktop and mobile states.
3. No page remains in loading, seed, empty fallback, error, or old shell unless the contract explicitly requires that state.
4. Every API-bound view names request, response/provenance, tenant, and correlation evidence.
5. Text, controls, board/grid dimensions, overlays, dialogs, and responsive behavior do not overlap or overflow.
6. Accessibility and keyboard checks pass.
7. Any mismatch opens a separate task; this evidence task does not redesign or weaken assertions.

### T41 - `ODP-P10-LIVE-LEGACY-RETIREMENT-001`

- Title: Reprove 117 retired paths and legacy visuals on current dev and live SHA
- Priority: `P0`
- Automation: `AUTO_NOW_STATIC` plus `AUTO_AFTER_T30_RUNTIME`
- Owner: `Claude2`
- Reviewer: `Antigravity6`

Writable paths:

- `docs/evidence/runtime/ODP-P10-LIVE-LEGACY-RETIREMENT-001/**`.

Acceptance:

1. Reconstruct the 117 unique retired paths from committed R3A/R3B ACKs.
2. Current-dev static inventory finds zero executable survivor, old selector family, alternate intake detail, old OpsBoard identity, retired feature-root import, or retired visual E2E resurrection.
3. After T30, every retired HTTP route is absent or redirects to the declared canonical workspace without serving old chunks/components.
4. Only canonical executable page ownership remains as declared by current Package 10 contract.
5. Runtime bundle/chunk/import graph cannot execute retired implementation.
6. Evidence is bound to current `origin/dev` and final public SHA, not historical `435c79e3` alone.

### T42 - `ODP-PLAN-LIVE-STAGING-PROOF-001`

- Action: update existing task.
- Automation: `AUTO_AFTER_DEPENDENCY`
- Owner: `Antigravity`
- Reviewer: `Claude2`
- Depends on: T30.

Acceptance additions:

- consume T30/T40/T41 exact-SHA evidence;
- prove browser -> API -> PG16/provider/model -> response on representative end-to-end flows;
- preserve WORM/audit/correlation and rollback receipts;
- no fixture, mock, seed, or local-only evidence.

### T50 - `ODP-PLAN-UAT-SIGNOFF-001`

- Action: update existing task.
- Automation: `AUTO_EVIDENCE_PLUS_HUMAN_SIGNOFF`
- Owner: `Antigravity`
- Reviewer/approver: `Human/Ops`
- Depends on: T40, T41, T42.

Auto worker prepares:

- exact-SHA UAT checklist;
- screen/API/data/model/retirement evidence index;
- known deviations and risk register;
- reproducible steps and correlation IDs.

Human product/UAT owner must record approve/reject and reason for the exact
release SHA. The worker cannot produce that signature.

### T60 - `ODP-PLAN-FINAL-GATE-AUDIT-001`

- Action: update existing task.
- Automation: `AUTO_EVIDENCE_PLUS_HUMAN_SIGNOFF`
- Owner: `Antigravity2`
- Reviewer/approver: `Human/Ops`
- Depends on: T50 human signoff.

Auto worker verifies all dependency receipts, exact SHA/version/alias/source
hashes, CI, rollback, security, audit, and no-open-P0 conditions. Human release
owner makes the final production risk decision. No worker may convert a missing
approval into an implicit pass.

## 5. Human/Ops Handback Contracts

### H01 - Authoritative ForecastOps history

Required only if T20 cannot find an already authorized source.

Human must provide:

- source system and accountable owner;
- immutable snapshot URI/hash or governed query boundary;
- permitted use and tenant/store scope;
- date coverage and cutoff;
- known exclusions/corrections;
- attestation timestamp.

Worker returns a validation receipt before ingestion. A file alone is not an
approval to use it.

### H02 - Model promotion approval

Human model/data owner approves or rejects:

- exact registered model/version;
- data snapshot/hash and cutoff;
- temporal evaluation and business thresholds;
- rollback version;
- known limitations and monitoring plan.

Only then may an auto worker move the MLflow `production` alias.

### H03 - Design and UAT acceptance

Human product/design/UAT owner reviews the exact public SHA and either approves
or records actionable deviations. Automated pixel/DOM/accessibility checks
support this decision but do not replace it.

### H04 - Final release-risk decision

Human release owner confirms that no gate is waived, or separately records an
authorized waiver under organizational policy. This pack does not authorize a
waiver.

### H05 - Conditional privileged operations

IAM escalation, legal/provider-policy acceptance, destructive data repair,
billing/quota changes, and production-secret policy changes always require an
explicit scoped approval. Auto workers may prepare commands and redacted
readback, but cannot grant themselves authority.

## 6. Conflict and Dispatch Gate

Before every dispatch, the Supervisor or coordinator must inspect:

- active task owner/reviewer and worker PID/run ID;
- open PR base/head/exact SHA and writable-path overlap;
- active Deploy Dev run and `cancel-in-progress` risk;
- current public release SHA;
- current model aliases and provider ingestion run IDs;
- source-doc manifest hashes;
- newly merged `dev` commits since the previous review.

If another LLM changes the same path, task, runtime data, alias, or deployment,
the new worker stops and reports the conflict immediately. It must not resolve
the conflict by force push, reset, direct DB edit, duplicate task, or silent
scope expansion.

## 7. Program Completion

The program closes only when T00, T10/T11, T20, T21, T30, T40, T41, T42, T50,
and T60 have terminal evidence-consistent outcomes; all required human
approvals name the exact release/model/source; and the public runtime satisfies
the ten release exit criteria in the gap analysis.
