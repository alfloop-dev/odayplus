# Package 10 live fleet state repair — review packet and evidence summary

- Sidecar task: `ODP-P10-LIVE-FLEET-STATE-REPAIR-001-SIDECAR-REVIEW`
- Parent task: `ODP-P10-LIVE-FLEET-STATE-REPAIR-001` (pack order `T00`)
- Helper kind: `review_packet`
- Sidecar owner: Claude2; sidecar reviewer: Claude
- Parent owner: Claude (live, helper claim); parent reviewer: Antigravity4
- Phase: `Package10LiveClosure`
- Prepared: `2026-08-09T15:46Z`
- Prepared at base: `c4c2bcffd4d812e40935dfa7dbbf5bec78a0561f` (equal to `origin/dev`)
- Parent head under review: `59beb96a60b8f15dbe732138fe2216c5a71525d0` (PR #756)
- Live status read at: `ai-status.json` `updated_at = 2026-08-09T15:41:06Z`

## Scope boundary

Support-only review packet. It changes no L1 canonical truth, no live task
state, no `next` field, no runtime, registry, or governance code, and it creates
no task. It is not approval of the parent and not authority to close it —
independent review authority is Antigravity4's, and that review **has already
been given** (see § Current state of record). This packet exists to give the
parent owner and the coordinator an independently re-measured picture at
closeout time, after the acceptance packet's snapshot went stale.

Companion artifact, merged to `dev` at `c4c2bcff` via PR #755:
`support/sidecars/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/ODP-P10-LIVE-FLEET-STATE-REPAIR-001-SIDECAR-ACCEPTANCE.md`.
This packet does not restate it; it **re-adjudicates** it against delivered work.

## Headline

The parent delivered. Every measurement in this packet was taken independently
by the preparer between `15:41Z` and `15:46Z`, against live state and GitHub,
not copied from the parent's own evidence.

- The two blocking risks the acceptance packet raised (**R2** live-state-only
  delivery, **R3** partial sweep) are **closed by delivered work**.
- Both verifiers the parent shipped **reproduce green on independent replay**,
  run by the preparer at a later time and against a live state that had already
  moved since the parent ran them.
- Antigravity4 approved at `15:17:39Z` **bound to `59beb96a`**, which is still
  the exact PR head. There is no head drift and no re-review trigger.
- Three residual items remain. **None blocks closeout**; two are cosmetic-at-
  closeout and one predates the repair. All are routed in § Residual findings.

The parent is closeout-ready. The one thing standing between it and `done` is
that PR #756 is open with auto-merge unarmed.

## Current state of record

| Fact | Value | Measured |
| --- | --- | --- |
| T00 live status | `review_approved` | `15:41Z` |
| T00 owner / reviewer | Claude / Antigravity4 | `15:41Z` |
| Approval event | Antigravity4, `2026-08-09T15:17:39Z` | activity log |
| Approved head | `59beb96a` ("base advance composed with origin/dev") | activity log |
| PR | #756, **OPEN**, `CLEAN`, not draft | `15:46Z` |
| PR head | `59beb96a` — **equals approved head** | `15:46Z` |
| Auto-merge | `autoMergeRequest: null` — **not armed** | `15:46Z` |
| CI | orchestrator, product, performance-gate, product-e2e-gate, task-review-gate — **all SUCCESS** | `15:46Z` |
| `origin/dev` tip | `c4c2bcff` | `15:46Z` |

**Head binding is intact.** Approval names `59beb96a`; the PR head is
`59beb96a`. The acceptance packet's A7 requirement that "any post-approval head
movement triggers `re_review`" is satisfied by there being no movement. Do
**not** advance the base of this PR — the `CLEAN` status means the base advance
already happened, inside the reviewed merge commit, before approval.

## Change surface, verified

`git diff --name-only origin/dev...59beb96a` — 9 files, 1884 insertions, 0
deletions, all additive:

```
docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/README.md
docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/canonical-state-after.json
docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/live-e2e-gate-9c95ecc3.json
docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/plan_pack_validator_after.txt
docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/plan_pack_validator_before.txt
docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/verify_brief_materialization.out
docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/verify_brief_materialization.py
docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/verify_fleet_state.out
docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/verify_fleet_state.py
```

Conformance against T00's declared ceiling, measured:

- Every path is inside `docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/**`.
  (The second writable glob, `.orchestrator/task-briefs/odp_p10_live_*.md`, went
  unused — the briefs are generated, not committed.)
- Zero hits against all seven forbidden globs (`apps/`, `modules/`, `shared/`,
  `models/`, `.github/`, `docs/design/`, `docs_archive/`).
- `git diff --check` clean.
- Commit `04821448` carries `LLM-Agent: Claude`, `Task-ID:
  ODP-P10-LIVE-FLEET-STATE-REPAIR-001`, `Reviewer: Antigravity4`. Head
  `59beb96a` is the base-advance merge commit, which is trailer-exempt by the
  `Merge ` subject rule.

D9 satisfied. No scope leak.

## Acceptance packet risks, re-adjudicated

The acceptance packet's five open risks, measured against delivered work.

| Risk | Status at `15:46Z` | Evidence |
| --- | --- | --- |
| **R1 — anchor decay** | **Materialized, benign.** Now *two* runs stale, not one. Substance unaffected. | § Anchor decay below |
| **R2 — live-state-only delivery** | **CLOSED.** Branch, PR #756, and a 9-file committed evidence directory all exist. | § Change surface |
| **R3 — partial sweep** | **CLOSED for the ten; T00's own `next` was later clobbered by the supervisor.** | § F2 |
| **R4 — dangling T60 edge** | **OPEN, unaddressed, pre-existing.** | § F3 |
| **R5 — two manifests, one truth** | **CLOSED.** Recorded in README § 4 and § 6.1. | § A8, § A4 |

### Anchor decay (R1) — measured, and why it does not block

The parent anchored all ten refreshed `next` fields to gate authority run
`31316767710` at `9c95ecc3`. Since the repair, two more Deploy Dev runs have
completed and a third is in flight:

| Run | Head SHA | Created | Conclusion | Standing at `15:46Z` |
| --- | --- | --- | --- | --- |
| 31321379955 | `c4c2bcff` | 15:32:22Z | — | **in flight** (`dev` tip) |
| 31320417513 | `6986c0f1` | 15:10:54Z | failure | **current gate authority** |
| 31319450627 | `5baa0931` | 14:49:12Z | failure | superseded |
| 31316767710 | `9c95ecc3` | 13:48:24Z | failure | **the parent's anchor — now two runs stale** |

The preparer downloaded and parsed `live-e2e-gate.json` from the *new* authority
run 31320417513:

- `ok`: `false`
- `expected_release_sha`: `6986c0f1b0219d1382571ac1328bcb783aae5e97`
- `generated_at`: `2026-08-09T15:30:21Z`
- `correlation_id`: `corr-live-e2e-6986c0f1b021-1786289421`
- `blocking_dependencies`: `external-data`, `mlflow`
- checks: **50 total, 43 ok, 7 blockers**

The 7 blockers, compared against the `9c95ecc3` set the parent recorded:

| Check | Dependency | Changed? |
| --- | --- | --- |
| `data:admin_boundary.official_dataset:run_exists` | external-data | no |
| `data:ingestion_runs` (`runs=0`) | external-data | no |
| `data:poi.commercial_api:run_exists` | external-data | no |
| `models:forecastops:production_alias` (`versionsWithProductionAlias=0`) | mlflow | no |
| `models:registry` (`versions=0`) | mlflow | no |
| `runtime:model_bindings` (`PRODUCTION_MODEL_REGISTRY_UNAVAILABLE`) | mlflow | no |
| `runtime:model_capability:forecastops` (`PRODUCTION_MODEL_REGISTRY_UNAVAILABLE`) | mlflow | no |

**Identical in check name, dependency, and detail string.** Counts identical at
43/50. Only the SHA, `generated_at`, and `correlation_id` moved.

**Adjudication.** This is now the third consecutive Deploy Dev run whose blocker
set is byte-identical — `4d89bea6` → `9c95ecc3` → `6986c0f1`. The parent's
substantive claim, which is what downstream owners act on, is still exactly
true. What has decayed is a citation, not a fact. Forcing a re-anchor sweep
would rewrite ten `next` fields to say the same thing about a different run id,
and would decay again the moment run 31321379955 completes. Re-anchoring is
churn, not correction.

Recommended disposition: **do not re-anchor for closeout.** Record in the
closeout message that the anchor is the latest run completed at write time, that
the blocker set has been independently re-verified unchanged at `6986c0f1`, and
that a run was in flight at `c4c2bcff`. This packet is that record.

## Independent verifier replay

The parent shipped two read-only verifiers. The preparer extracted both from
`59beb96a` and re-ran them from a separate checkout, at `15:44Z` — later than
the parent's own run, and against a live state that had already moved.

### `verify_fleet_state.py` — 7/7 pass, exit 0

```
active tasks: 56   archived tasks: 109

[SKIP] 7. independent exact-state review passes -- owned by the reviewer, not self-assertable
[PASS] 1. each summarized task has exactly one canonical resolution
[PASS] 2. missing provider-ingestion work is restored durably
[PASS] 3. next fields name current run SHA, blockers, dependencies, resume condition
[PASS] 4. owner and reviewer source manifests match
[PASS] 5. historical R3 implementation tasks are not reopened
[PASS] 6. update_existing tasks have explicit writable and forbidden ceilings
[PASS] 8. mutates_canonical normalized on all 11 without widening authority

7/7 acceptance criteria pass
```

### `verify_brief_materialization.py` — 11/11 pass, exit 0

All eleven tasks materialize an execution brief without fail-closing, 5 to 9
context files each. This is the operative proof for A4: owner and reviewer
materialize from the same `execution_context_files()` call, so a task that
raises here is undispatchable for both sides.

**The replay is genuine and reproducible.** That matters more than the parent's
own green: these results survived a state change and a different runner.

### Verifier coverage audit — what green does *not* prove

Read the scripts, do not just read their output. Two deliberate limits:

1. **Criterion 3 skips T00.** `check_next_fields()` has an explicit
   `if order == "T00": continue`, commented "T00 is the task doing the repair;
   its own next is set at handoff". So the green on criterion 3 covers ten
   tasks, not eleven. The acceptance packet's A3 required T00 to meet the
   standard it imposes on the other ten. It does not, at `15:41Z` — see F2.
   The design choice is defensible; the *gap it leaves* is the point.

2. **The gate anchor is hardcoded.** `CURRENT_RUN_SHA = "9c95ecc3"` and
   `CURRENT_RUN_ID = "31316767710"` are module constants. Criterion 3 asserts
   the ten `next` fields name *those* strings. The verifier will therefore keep
   printing green indefinitely as the real gate authority advances. It is a
   point-in-time conformance proof, **not a freshness proof**. Anyone re-running
   it later must pair it with a live `gh run list` — as this packet did.

Neither limit is a defect in the delivered work. Both are things a reviewer
would otherwise infer wrongly from a green result, which is exactly what an
independent packet is for.

## Acceptance re-adjudication (A1–A8)

Against the parent's eight declared criteria, measured at `15:41Z`/`15:46Z`.

| # | Criterion | Verdict | Basis |
| --- | --- | --- | --- |
| A1 | one canonical resolution per task | **MET** | 11/11 resolve uniquely; 0 active∩archive duplicates; graph acyclic over 56 active tasks |
| A2 | provider-ingestion work restored durably | **MET, exceeded** | see below |
| A3 | `next` names run, SHA, blockers, deps, resume | **MET for 10/11**; T00 clobbered post-approval | see F2 |
| A4 | owner/reviewer manifests match | **MET** | 11/11 briefs materialize; T00 helper claim and T11 placeholder both recorded in README § 6.1 |
| A5 | historical R3 tasks not reopened | **MET** | `[]` R3 ids in active state; `ODP-P10-R3CD-DEV-COMPOSE-001` archive-only |
| A6 | explicit ceilings on `update_existing` tasks | **MET** | T20/T21/T30/T42/T50/T60 all carry non-empty `writable_paths` + `forbidden_paths` |
| A7 | independent exact-state review passes | **MET** | Antigravity4 approved `15:17:39Z` at `59beb96a`; head unmoved; scope clean |
| A8 | `mutates_canonical` normalized, no widening | **MET** | 11/11 explicit; pack↔live divergence recorded in README § 4 |

### A2 — the strongest part of the delivery, and a correction to this sidecar's own prior packet

The acceptance packet required the restoration to live in a committed artifact
because a `next` field is mutable. The parent did better: it restored
`ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001` as a **structured archive record**,
then superseded it into T10. Verified directly in
`ai-task-archive/tasks/ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001.json`:

- `terminal_outcome: "superseded"`, `superseded_by: "ODP-P10-LIVE-EXTDATA-DIAG-001"`
- `owner: Claude`, `reviewer: Antigravity4`, `status: done`
- both surviving evidence files recorded in `artifacts`
- `residual_scope` recorded, naming T10 and conditionally T11
- **4 `delivered_findings` preserved**, including the exact mechanism: worker
  `handle_external_fetch` → `run_scheduled()` → `ingest()` without `tenant_id`,
  so `ExternalIngestionService._resolve_store("")` persists `IngestionRunRecord`
  unscoped, while `GET /api/v1/external-data/ingestion-runs` reads tenant-scoped
  under `a11ce505-70bc-56d9-8564-ad22efa23c9e`; same Secret Manager reference and
  same `durable_documents` table on both sides, so the asymmetry is tenant scope
  and not database binding; and explicitly, that **no code or configuration
  change was ever made**.

This is a durable, queryable, schema-shaped record — strictly better than the
prose-in-a-committed-README the acceptance packet asked for. The linkage also
survives in T10's `next` (1190 chars) and `prior_art`.

**Correction to the acceptance packet.** Its A5 checklist carried the item
"Restored *evidence* is cited by path; restored *tasks* are not." That was the
preparer's own over-strict reading, not the parent's declared criterion — A5 as
declared concerns **historical R3 implementation tasks**, and
`ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001` is not one. Re-creating it purely to
archive it as `superseded` is the correct mechanism, not a violation. **That
checklist item is withdrawn.** Archive count moved 106 → 109 accordingly.

## Residual findings

Three items. Ranked. **None blocks closeout.**

### F1 — Gate anchor is two completed runs stale (severity: low, do not fix)

Ten `next` fields cite run `31316767710` at `9c95ecc3`; current authority is
`31320417513` at `6986c0f1`, with `31321379955` at `c4c2bcff` in flight.
Independently re-verified: the 7-blocker set is byte-identical, 43/50 unchanged.

**Disposition: accept as-is.** Re-anchoring rewrites ten fields to assert the
same facts and decays again within the hour. Record the re-verification in the
closeout message instead. Routing: pack-level re-anchoring belongs to
`ODP-P10-LIVE-GAP-EVIDENCE-REFRESH-*`, as README § 6.2 already states.

### F2 — T00's own `next` was overwritten by the supervisor after approval (severity: low, cosmetic at closeout)

At `15:41Z`, T00's `next` reads, in full:

```
Supervisor resumed ODP-P10-LIVE-FLEET-STATE-REPAIR-001 for finalize after successful dispatch.
```

94 characters of dispatch boilerplate. The other ten carry 1009–1391 characters
of anchored detail.

**This is not owner negligence.** The activity log shows the exact cause: at
`2026-08-09T15:35:08Z` the orchestrator emitted a `note` on T00 as part of its
own `owned_finalize_dispatch`, and that note **is** the `next` field. The parent
had refreshed the other ten at `15:06`–`15:09`; the system then clobbered T00's.

This is the acceptance packet's central durability warning, reproduced
empirically against the repair task itself: *a `next` field is the least
durable place to store anything.* The verifier cannot catch it, because
criterion 3 skips T00 by construction.

**Disposition: no action required for closeout.** T00 is `review_approved`; the
approved deliverable is the committed evidence directory, which is durable and
unaffected. Writing to T00's `next` now via `progress` would **downgrade
`review_approved` to `in_progress`** and turn `task-review-gate` red — a strictly
worse outcome. The `done` checkpoint message is the correct place to record
T00's final state, and it is durable in the archive record.

### F3 — Dangling dependency edge on T60 (severity: medium, pre-existing, unaddressed)

`ODP-PLAN-FINAL-GATE-AUDIT-001` (T60) declares `depends_on:
["ODP-PLAN-OSS-LICENSE-GATE-001"]`. That id resolves in **neither** live active
state (56 tasks) nor the archive (109 records). Re-measured at `15:41Z`: still
dangling, and now with **two** referents —
`ODP-PLAN-AVM-OUTCOME-001-SIDECAR-ACCEPTANCE` declares the same edge.

The parent README's § 6 deviations do not mention it (`OSS-LICENSE-GATE`: 0
occurrences in the README). It is the **only** acceptance-packet divergence left
unaddressed.

**Assessment.** This predates the repair and sits outside T00's writable
ceiling, so it is not a defect in the delivered work. But it does sit inside the
spirit of "exactly one canonical resolution", and as written T60 can never
satisfy its dependency gate — it is a latent deadlock at the far end of the
Package 10 closure chain, where it will be most expensive to discover.

**Disposition: route, do not block.** T60 is `todo` and four hops downstream;
there is time. The coordinator should either register
`ODP-PLAN-OSS-LICENSE-GATE-001` or remove both edges with a stated reason. This
belongs with the 81 out-of-scope plan-pack validator errors the parent already
flagged for the coordinator in README § 6.3 — same owner, same sweep.

## Closeout readiness

Against `.orchestrator/skills/task-closeout-finalization.md`, for the parent
owner (Claude), for T00:

| Gate | State |
| --- | --- |
| Task is `review_approved` | yes, `15:35Z` |
| Approved scope still true in worktree | yes — head `59beb96a` unmoved |
| Task-scoped commit with required trailers | yes — `04821448` |
| Focused verification recorded | yes — both verifiers, `.out` files committed, replayed green here |
| PR exists | yes — #756 |
| CI green | yes — all 5 checks SUCCESS |
| `mergeStateStatus` | `CLEAN` |
| **PR merged into `dev`** | **NO — still OPEN** |
| Safe to run `done` | **not yet** |

**The single remaining blocker is the merge.** `scripts/ai-status.sh done`
verifies the task branch head is an ancestor of `dev` before it will move state;
it will refuse while #756 is open. `autoMergeRequest` is `null`, so nothing is
currently driving that PR to merge.

Sequence for the parent owner:

1. Leave T00 in `review_approved`. Do not run `progress`, `note`-to-`next`, or
   `blocker` — each either downgrades the approval or strands it.
2. Do not advance the PR base. `CLEAN` means the base advance is already inside
   the reviewed merge commit; advancing again would break the approved-head
   freeze.
3. Get #756 merged. If auto-merge cannot be armed from the worker, escalate by
   commenting on #756.
4. After GitHub reports merged, run `done` with a checkpoint message that
   records: approved head `59beb96a`, merge commit, both verifier results, and
   the F1 re-verification (blocker set unchanged at `6986c0f1`; run
   `31321379955` in flight).
5. Only then do T10 and T41 become dispatchable, per pack `dispatch_rules` and
   T00's `stop_conditions` (D10).

## Sidecar verification record

Every command below was run by the preparer between `15:41Z` and `15:46Z`, with
the observed result. Nothing is quoted from the parent's evidence.

```text
date -u                       -> 2026-08-09T15:46:14Z
git rev-parse origin/dev      -> c4c2bcffd4d812e40935dfa7dbbf5bec78a0561f
git ls-remote origin refs/heads/task/ODP-P10-LIVE-FLEET-STATE-REPAIR-001*
  -> 59beb96a…  task/ODP-P10-LIVE-FLEET-STATE-REPAIR-001          (branch NOW EXISTS)
     17ddcf34…  task/…-SIDECAR-ACCEPTANCE                          (merged)

gh pr list --search ODP-P10-LIVE-FLEET-STATE-REPAIR-001
  -> #756 OPEN CLEAN head 59beb96a; #755 MERGED; #745 MERGED
gh pr view 756 --json state,mergeStateStatus,headRefOid,autoMergeRequest
  -> OPEN / CLEAN / 59beb96a / null
gh pr view 756 --json statusCheckRollup
  -> orchestrator SUCCESS, product SUCCESS, performance-gate SUCCESS,
     product-e2e-gate SUCCESS, task-review-gate SUCCESS

git diff --name-only origin/dev...59beb96a
  -> 9 files, all under docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/
git diff --stat  origin/dev...59beb96a   -> 1884 insertions(+), 0 deletions
git diff --check origin/dev...59beb96a   -> clean
forbidden-glob grep over the diff        -> no hit (apps|modules|shared|models|.github|docs/design|docs_archive)
outside-ceiling grep over the diff       -> no hit

gh run list --workflow="Deploy Dev" --limit 8
  -> 31321379955 c4c2bcff in_progress; 31320417513 6986c0f1 failure;
     31319450627 5baa0931 failure; 31316767710 9c95ecc3 failure;
     31314125275 ebfe128e failure; 31312735093 4d89bea6 failure;
     31312411417 / 31311664947 cancelled
gh run download 31320417513 -n cloud-run-dev-validation
  -> live-e2e-gate.json: ok=false, sha 6986c0f1, generated 2026-08-09T15:30:21Z,
     corr-live-e2e-6986c0f1b021-1786289421, 50 checks / 43 ok / 7 blockers,
     blocking_dependencies=[external-data, mlflow];
     blocker check names, dependencies and details IDENTICAL to the 9c95ecc3 set

python3 verify_fleet_state.py --status <live> --archive-root <live> --repo-root <this worktree>
  -> 7/7 pass, exit 0   (independent replay, 56 active / 109 archived)
python3 verify_brief_materialization.py --supervisor-root <runtime-current> --status-root <live>
  -> 11/11 materialize, exit 0   (independent replay)

python3 conformance sweep over live ai-status.json (updated_at 2026-08-09T15:41:06Z)
  -> 11/11 resolve uniquely; 0 active/archive duplicates; graph acyclic;
     mutates_canonical explicit 11/11; writable+forbidden non-empty 11/11;
     next refreshed on 10/11 — T00 carries supervisor boilerplate (94 chars);
     DANGLING ODP-PLAN-FINAL-GATE-AUDIT-001 -> ODP-PLAN-OSS-LICENSE-GATE-001
     DANGLING ODP-PLAN-AVM-OUTCOME-001-SIDECAR-ACCEPTANCE -> same missing id
     R3 ids in active state: []

archive record ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001.json
  -> terminal_outcome=superseded, superseded_by=ODP-P10-LIVE-EXTDATA-DIAG-001,
     4 delivered_findings, residual_scope recorded, 2 artifacts recorded

source read of verify_fleet_state.py
  -> check_next_fields() skips T00 by design (line ~170)
  -> CURRENT_RUN_SHA/CURRENT_RUN_ID are hardcoded module constants

activity log, task_id=ODP-P10-LIVE-FLEET-STATE-REPAIR-001
  -> 15:13:58Z Claude handoff -> Antigravity4 at 0482144882c2, PR #756
  -> 15:17:39Z Antigravity4 review_approved, base advance composed with 59beb96a
  -> 15:35:08Z Orchestrator note (owned_finalize_dispatch) OVERWROTE T00 next
```

These prove the delivered surface, the approval binding, the independent replay,
and the three residuals as of `2026-08-09T15:46Z`. They do not prove the PR
merged — it was not, at that time — and they do not substitute for the parent
owner's own read at closeout.

## Handoff disposition

Ready for Claude to review as a sidecar support artifact.

For the **parent owner**: this is the closeout evidence summary. F1's
re-verification and F2's cause are the two things worth carrying into the `done`
checkpoint message; neither requires further work. Merge #756 first.

For the **coordinator**: F3 is the one item needing a decision outside T00's
scope, and it batches naturally with the 81 out-of-scope plan-pack validator
errors already flagged in the parent's README § 6.3.

For **Antigravity4**: no re-review is triggered. The approved head has not
moved, the scope is unchanged, and the two residuals discovered since approval
(F1, F2) are both downstream of the approval rather than defects in it. This
packet is offered as a record of that, not as a request to re-open.
