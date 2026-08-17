# Package 10 external-data diagnosis — review packet and evidence summary

- Sidecar task: `ODP-P10-LIVE-EXTDATA-DIAG-001-SIDECAR-REVIEW`
- Parent task: `ODP-P10-LIVE-EXTDATA-DIAG-001` (pack order `T10`)
- Helper kind: `review_packet`
- Sidecar owner: Claude2; sidecar reviewer: Claude
- Parent owner: Claude; parent reviewer: Antigravity (reassigned from Claude2 at `16:17:31Z`)
- Phase: `Package10LiveClosure`
- Prepared: `2026-08-09T16:44Z`–`16:49Z`
- Prepared at base: `e385c5d9de82ba78951c5afc6dd1f31e700575a6`
- Parent head under review: `29d9321d10232cd31b3ab18a9cdc4fa9a2bd5836` (PR #759)
- Live status read at: `ai-status.json` `updated_at = 2026-08-09T16:44:31Z`

## Scope boundary

Support-only review packet. It changes no L1 canonical truth, no live task state,
no `next` field, no runtime, registry, or governance code, and it creates no
task. It is not approval of the parent and not authority to close it —
independent review authority is Antigravity's, and that review **has already
been given** (`16:20:27Z`). This packet exists to give the parent owner and the
coordinator an independently re-measured picture at closeout time, because the
supervisor wrote a blocking instruction into T10's `next` that this packet finds
to be **false**.

## Headline

**T10 is closeout-ready right now, and the instruction currently sitting in its
`next` field is wrong.**

T10's `next` reads *"CI checks for task ODP-P10-LIVE-EXTDATA-DIAG-001 failed;
resolve failing checks before finalization."* Three independently measured facts
contradict it:

1. The one red check, `performance-gate`, is **not a required status check on
   `dev`**. Required contexts are `orchestrator`, `product`, `product-e2e-gate`,
   `task-review-gate` — and all four are `SUCCESS`.
2. `performance-gate` is **flaky, and provably so**: it passed twice and failed
   once *inside the same run on the same commit*, and the PR's own base commit
   `8eabc973` recorded both `failure` and `success` for it.
3. **PR #759 already merged**, at `2026-08-09T16:44:41Z`, merge commit
   `9166d3ca`, now `origin/dev` tip. `29d9321d` is an ancestor of `dev`.

So there is nothing to resolve. The owner should **not** attempt to fix
`performance-gate` — it is not this task's defect, not in this task's writable
ceiling, and not blocking anything. The correct next action is `done`.

This matters beyond cosmetics: **T11 (`ODP-P10-LIVE-EXTDATA-REMEDIATE-001`) and
T30 (`ODP-P10-DEV-REDEPLOY-VERIFY-001`) are both `blocked` on T10.** A false CI
blocker in `next` stalls two downstream tasks at the head of the Package 10
closure chain.

On substance: the diagnosis is correct. Every code citation in the parent's
README was independently re-read at `origin/dev` and every one holds, including
the two tenant-partition digests, which were recomputed from scratch and match
to the character.

## Current state of record

| Fact | Value | Measured |
| --- | --- | --- |
| T10 live status | `review_approved` | `16:44Z` |
| T10 owner / reviewer | Claude / Antigravity | `16:44Z` |
| Approval event | Antigravity, `2026-08-09T16:20:27Z` | activity log |
| PR | #759 — **MERGED** | `16:46Z` |
| PR head | `29d9321d` — unmoved since `16:08:42Z` | `16:46Z` |
| Merged at / by | `2026-08-09T16:44:41Z` / `ajoe734` (manual) | `16:46Z` |
| Merge commit | `9166d3ca` | `16:46Z` |
| `origin/dev` tip | `9166d3ca` — **equals the merge commit** | `16:49Z` |
| `29d9321d` ancestor of `dev` | **yes** (`git merge-base --is-ancestor`) | `16:49Z` |
| Required checks on `dev` | orchestrator, product, product-e2e-gate, task-review-gate — **all SUCCESS** | `16:46Z` |
| `performance-gate` | FAILURE — **not a required context** | `16:46Z` |
| T10 `next` | supervisor `ci_failed` boilerplate — **stale and false** | `16:44Z` |

**Head binding is intact.** The commit has not moved since it was authored at
`16:08:42Z`; approval at `16:20:27Z` and the merge at `16:44:41Z` both bind to
`29d9321d`. No head drift, no `re_review` trigger.

## The false CI blocker (F1) — measured

The supervisor emitted `ci_failed` at `2026-08-09T16:20:52Z`, 25 seconds after
Antigravity's approval, and that message became T10's `next`. It was raised on a
check that cannot block this merge.

`gh api repos/alfloop-dev/odayplus/branches/dev/protection` returns exactly four
required contexts:

```
orchestrator
product
product-e2e-gate
task-review-gate
```

`performance-gate` is absent. Rollup on #759 at `16:46Z`:

| Check | Result | Required? |
| --- | --- | --- |
| `orchestrator` | SUCCESS | yes |
| `product` | SUCCESS | yes |
| `product-e2e-gate` | SUCCESS | yes |
| `task-review-gate` | SUCCESS | yes |
| `performance-gate` | **FAILURE** | **no** |

The merge itself is the empirical proof: GitHub merged #759 at `16:44:41Z` with
`performance-gate` still red.

**Disposition: no work required.** The `next` field is stale in both directions
— it names a blocker that was never blocking, and it predates the merge that
cleared the only real precondition. Do **not** run `progress` or `note` to
correct it: on a `review_approved` task that downgrades the status to
`in_progress` and turns `task-review-gate` red, which is strictly worse than a
wrong sentence. The `done` checkpoint message is the correct and durable place
to record this; the archive record then carries it.

## The performance-gate flake (F2) — proven, pre-existing, not this PR's

The PR changes three files, all `docs/evidence/runtime/…` Markdown and JSON. It
imports nothing, is imported by nothing, and cannot affect a pytest timing
budget. Two independent measurements make that not merely implausible but
demonstrated.

**Same commit, same run, both outcomes.** `.github/workflows/ci.yml:174-193`
runs the gate three times and exits on the *first* red attempt — an all-three-
must-pass soak, not a retry-until-green. Run `31323018885` on `29d9321d`:

| Attempt | Result | Wall clock |
| --- | --- | --- |
| 1 | `3 passed, 4 deselected` | 16:09:40Z |
| 2 | `3 passed, 4 deselected` | 16:09:50Z |
| 3 | **`1 failed, 2 passed`** | 16:10:01Z |

Identical code, identical runner, 21 seconds apart, two greens then a red.

**Same base commit, both outcomes.** `8eabc973` is #759's parent — the tree the
PR was built on:

| Run | Event | SHA | Result |
| --- | --- | --- | --- |
| 31322265923 | `push` to `dev` | `8eabc973` | **failure** |
| 31321422749 | `merge_group` (PR 756) | `8eabc973` | **success** |

The gate contradicts itself on a commit that predates this PR entirely. For
completeness, `dev` tip `e385c5d9` recorded `success` on run `31323609465`.

**Mechanism.** The failing assertion is
`tests/performance/assisted_listing_intake/test_capacity.py::test_approved_capacity_and_slo_are_measured`,
at `assert report["missed_targets"] == []`, with
`['url_submission_durable_receipt', 'url_submission_receipt_error_budget']`
observed. Reading `delivery_toolchain/load/assisted_listing_intake/runtime.py`, those
targets are wall-clock percentiles: `summarize()` compares `time.perf_counter()`
deltas against a `0.5s` p95 / `1.5s` p99 budget (line 153) across 20 concurrent
submitters. On a shared GitHub-hosted runner that is a contention-sensitive
measurement, so a red attempt reports runner scheduling noise, not a regression.

**Disposition: route, do not block, and do not fix here.** `scripts/**` and
`tests/**` are both in T10's forbidden paths, so this is out of the parent's
ceiling by construction. `ODP-CI-FLAKE-REMEDIATION-001` is `done` and archived
and covered a vitest teardown race and httpx thread concurrency — not this test,
which remains uncovered. Recommend the coordinator open a CI-lane follow-up:
either widen the p95 budget to reflect shared-runner variance, or make the three
attempts a best-of rather than an all-of. Until then this gate will keep
emitting `ci_failed` against innocent docs-only PRs and writing false blockers
into their `next` fields — F1 is a symptom of F2, and the pairing is the reason
both belong in this packet.

## Change surface, verified

`git diff --name-only origin/dev...29d9321d` (measured pre-merge) — 3 files,
793 insertions, 0 deletions, all additive:

```
docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/README.md
docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/live-e2e-gate-run-31316767710.json
docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/worker-validation-run-31316767710.json
```

Conformance against T10's declared ceiling:

- Every path is inside `docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/**`,
  the sole `writable_paths` entry, and matches the declared `artifacts` exactly.
- Zero hits against all ten forbidden globs (`apps/`, `modules/`, `shared/`,
  `scripts/`, `tests/`, `.github/`, `.orchestrator/`, `models/`, `docs/design/`,
  `docs_archive/`). This is the notable one: the diagnosis **reads** heavily from
  `apps/`, `modules/`, `shared/`, `scripts/`, and `.github/`, and **writes** to
  none of them. `mutates_canonical: false` holds.
- `git diff --check` clean.
- Commit `29d9321d` carries `LLM-Agent: Claude`, `Task-ID:
  ODP-P10-LIVE-EXTDATA-DIAG-001`, `Reviewer: Claude2`, and a `Verified:` trailer.
  Reviewer trailer staleness is F3.

No scope leak.

## Independent re-verification of the diagnosis

The parent's root cause is a five-link chain. A review packet that merely
restated it would add nothing, so every link was re-read from `origin/dev`
source at `16:47Z`–`16:49Z`. **All five hold.**

| # | Parent's claim | Independently confirmed |
| --- | --- | --- |
| 1 | `deploy-dev.yml:101-102` supplies `\|\| 'tenant-dev'` | **Yes**, verbatim at both lines. `deploy-staging.yml:84-85` carries the same pattern with `'tenant-staging'` |
| 2 | `_enqueue_body` falls back to `ODP_SCHEDULED_INGESTION_TENANT_ID` | **Yes**, `check_live_e2e_gate.py:1318-1321`, exactly the stated chain |
| 3 | `POST /api/v1/jobs` rebinds tenant **only** for `forecast` | **Yes**, `main.py:772` — `if body.job_type == "forecast":` is the sole guard; `TENANT_SCOPE_MISMATCH` and the `payload = {**payload, "tenant_id": active_tenant_id}` rewrite are both inside it. For `external-fetch`, `payload` reaches `job_queue.enqueue` unmodified |
| 4 | `TenantScopedDocumentStore` *renames* the collection | **Yes**, `operator_domains.py:_collection()` returns `f"{collection}.tenant.{self._partition}"` where `_partition = sha256(tenant_id).hexdigest()`. It is a partition rename, not a filter — so a cross-tenant read cannot return rows, it addresses a different collection |
| 5 | `deploy_cloud_run_waji.sh:45-46` fail-closed guard is defeated | **Yes**, the `if [ -z … ] && [ -z … ]; then exit 1` guard exists exactly as described, and the workflow default guarantees it can never observe the unset state |

Supporting citations also re-read and confirmed:
`ingestion_service.py:337-362` (`run_scheduled` raises `ScheduledIngestionTenantError`
on an empty tenant) and `handlers.py:92-120` (`handle_external_fetch` raises
`NonRetryableJobError` on an untenanted payload). Both fail-closed paths are real
and neither fired, which is what makes §2's elimination argument sound.

### Partition digests — recomputed from scratch

The single most falsifiable claim in the README is its table of two collection
names. Both were recomputed locally with `sha256(tenant_id).hexdigest()`:

```
tenant-dev                            -> 7c51172bedb79ef6b6d0d0eb675210470d2cc2e0a4947ab7221616199a9c01f6
a11ce505-70bc-56d9-8564-ad22efa23c9e  -> da57d47ac40b5f8fa57ac349b3b1a154b3b64d4e807c142a1e9ba1bdef834b5b
```

Both match the README character for character.

### Primary artifact — re-parsed, not quoted

`live-e2e-gate-run-31316767710.json` was parsed directly rather than read from
the README's table:

- `ok=false`, `expected_release_sha=9c95ecc3…`, `generated_at=2026-08-09T14:07:16Z`,
  `correlation_id=corr-live-e2e-9c95ecc3e1f2-1786284436`
- **50 checks, 43 ok, 7 blockers**, `blocking_dependencies=[external-data, mlflow]`
- `worker`: `job_type=external-fetch`, `terminal_status=succeeded`,
  `job_id=06cb31de-f047-4a16-bc74-d97deb709a0e`,
  `ingestion_probe_provider_ids=[admin_boundary.official_dataset, poi.commercial_api]`
- Every row of the README's §1 table reproduces exactly — 5 `worker:*` PASS, all
  3 `runtime:provider_probe:*` PASS with `reasonCode=ok`, `data:no_surrogate_markers`
  PASS, and the 3 `data:*` FAIL with `runs=0` / `no persisted ingestion run for a
  required live provider`.

**The contradiction is real and is in one primary artifact.** Five worker checks
green and three data checks at zero, in a single gate execution, is the whole
case — and it is not reconstructed from prose.

The four remaining blockers are `mlflow` (`models:registry`,
`models:forecastops:production_alias`, `runtime:model_bindings`,
`runtime:model_capability:forecastops`) and are correctly disclaimed as
model-readiness-lane scope.

### Secret-disclosure scan — independent

Both committed artifacts were scanned by the preparer against bearer-token,
credential-field, connection-string, JWT, and PEM patterns. **Zero hits in
both.** `inputs.secret_values_redacted` is `true` in the gate report, and
`secret_values_redacted` is `true` at top level in the worker receipt. Secret
material appears only as environment-variable *names*
(`ODAY_DATABASE_URL`, `ODP_*_PROVIDER_TOKEN`, `ODP_*_API_KEY`). Tenant ids and
their sha256 digests are non-secret identifiers. Acceptance criterion 6 is met on
the secret-disclosure limb by independent measurement, not by assertion.

## Acceptance re-adjudication

Against T10's six declared criteria.

| # | Criterion | Verdict | Basis |
| --- | --- | --- | --- |
| 1 | one evidence-backed root cause explains worker success and API runs zero | **MET** | Five-link chain, every link re-verified at `origin/dev`; §2 eliminates the five rival explanations against exact-SHA artifacts |
| 2 | release / tenant / schema / store correlation and provider identities proven and redacted | **MET** | §4 correlation table; digests recomputed; commit ancestry `fcc9d4a0`/`17f35834`/`f7bd3d9b` → `9c95ecc3` → `dev`; independent secret scan clean |
| 3 | both required snapshot providers have a disposition | **MET** | `admin_boundary.official_dataset` and `poi.commercial_api` each dispositioned in §5; `geocode.primary_api` correctly exempted as required-but-not-snapshot-schedulable, matching `inputs.snapshot_provider_ids` in the artifact |
| 4 | runtime-only closure yields non-empty, succeeded, lineage-complete, API-readable runs | **NOT ATTAINED — correctly** | §6 argues the stop condition fired; see below |
| 5 | code-or-config need produces exact T11 handoff and no patch in T10 | **MET** | §7 names four items with file:line targets; zero code files in the diff |
| 6 | review proves no direct DB write, fake data, or secret disclosure | **MET** | Diff is 3 docs files; independent secret scan clean; §6.3 records that `gcloud` had no usable credentials, so no live call was even possible |

### On criterion 4 — the right call, and the reasoning is load-bearing

Criterion 4 is unmet, and that is the correct outcome rather than a shortfall.
The parent's §6 argument was checked, not taken on trust, and the second limb is
the strong one: the gate runs *inside* `deploy_cloud_run_waji.sh` before
`DEPLOYMENT_COMMITTED=true`, and it creates its own probe runs on every
execution. Pre-seeding rows under the operator tenant — which is what the
2026-08-03 prior art did — would therefore mask the split for exactly one run
while the next deploy writes to `tenant-dev` and reads the operator tenant
again. **A runtime-only closure here would have manufactured a false green.**
Criterion 5 explicitly provides for this path, and T10's stop condition
*"code or configuration edit required"* is genuinely hit: every remedy touches
`.github/**`, `apps/**`, `scripts/**`, or a deployment secret, and all four are
forbidden to T10.

Declining to close is the correct behaviour under the criteria as written, not
an incomplete delivery.

### Quality note on the T11 handoff

§7 item 1 gets the *direction* of the fix right, and that is the part most
likely to be reversed by a hurried implementer. Aligning the ingestion variable
to the existing operator tenant strands only the disposable probe runs from
2026-08-08/09; repointing the principal map at `tenant-dev` would move every
already-live operator surface in dev into an empty partition. §7 item 3 also
correctly identifies that the enqueue path is a live authorization gap in its own
right — any `job:execute` caller can direct canonical ingestion into an arbitrary
tenant partition and receive a 202 — and that finding stands independently of
this gate failure.

## Residual findings

Five items. Ranked. **None blocks closeout.**

### F1 — T10's `next` carries a false CI blocker (severity: high impact, zero work)

Covered in full above. Two downstream tasks (T11, T30) are `blocked` behind T10,
so the stale instruction has real cost. **Disposition: record the correction in
the `done` message; change nothing else.** Do not run `progress` or `note` — on
a `review_approved` task either downgrades the approval.

### F2 — `performance-gate` is flaky and uncovered (severity: medium, route to CI lane)

Covered in full above. Proven by within-run and same-SHA contradiction; mechanism
is a wall-clock p95 budget under runner contention. Out of T10's ceiling
(`scripts/**`, `tests/**` both forbidden). `ODP-CI-FLAKE-REMEDIATION-001` is done
and did not cover this test. **Disposition: coordinator opens a CI-lane
follow-up.** Left unfixed, it will keep writing false `ci_failed` blockers into
the `next` field of unrelated docs-only tasks.

### F3 — Commit reviewer trailer is stale (severity: low, do not fix)

Commit `29d9321d` carries `Reviewer: Claude2`, authored `16:08:42Z`. The
orchestrator reassigned review to Antigravity at `16:17:31Z` — 9 minutes later —
because "Claude2 shares account pool with owner Claude, so independent review
requires a different pool". The approval at `16:20:27Z` came from Antigravity,
so the trailer names someone who did not perform the review.

**Disposition: no action.** The commit predates the reassignment, so this is not
an authoring error; the `reviewer != owner` hook constraint held at write time;
and the commit is merged and immutable. Do **not** create a fresh commit with
updated trailers — that would move the head after approval and after merge, for
a cosmetic gain. The activity log is the authoritative reviewer record and is
already correct. Worth one line in the `done` message so the archive is
unambiguous about who approved.

### F4 — Minor citation imprecision in README §1 (severity: cosmetic, do not fix)

§1 lists `worker_probe_provider_id=admin_boundary.official_dataset` among the
gate's `worker` block fields. In the artifact that key lives under `inputs`, not
`worker`; the `worker` block has five keys and this is not one of them. **The
value is correct** and nothing downstream depends on the location. Not worth a
post-merge commit; noted so a T11 implementer parsing the artifact by path
knows where to look.

### F5 — The read-side tenant value is not live-verified (severity: medium, already disclosed, carry to T11)

`a11ce505-70bc-56d9-8564-ad22efa23c9e` is carried from 2026-08-03 prior art and
`docs/evidence/runtime/ODP-OPERATOR-SMOKE-RBAC-LIVE-002/`, not re-read from
`ODP_AUTH_PRINCIPAL_MAP_SECRET` in this task. **The parent discloses this
explicitly** in §3.3 and §7 item 1 ("verify, do not assume"), which is the right
handling.

Confirmed independently: **the root cause does not depend on that value.** §2
establishes a non-`FAILED` run was persisted for both snapshot providers, the
artifact shows the readback returned zero, and two-written-zero-read is only
possible if the partitions differ — i.e. if the read tenant is anything other
than `tenant-dev`. The specific value is needed to *apply* the fix, not to
*prove* the diagnosis. **Disposition: T11 must read the current value from the
secret before setting the deployment variables.** If it has drifted, §7 item 1
still works; only the literal changes.

Also carried forward, from the parent's own §6 open sub-question: the 2026-08-03
governed runs did not appear in the readback either, which the tenant split alone
does not explain. Correctly flagged as not affecting the root cause — the gate
creates its own runs before reading — and correctly routed to T11, which will
have database access.

## Closeout readiness

Against `.orchestrator/skills/task-closeout-finalization.md`, for the parent
owner (Claude), for T10:

| Gate | State |
| --- | --- |
| Task is `review_approved` | yes, `16:20:27Z` |
| Approved scope still true in worktree | yes — head `29d9321d` unmoved since authoring |
| Task-scoped commit with required trailers | yes — `29d9321d` (reviewer trailer stale, F3) |
| Focused verification recorded | yes — `Verified:` trailer; read-only evidence capture, independently replayed here |
| Scope within declared ceiling | yes — 3 files, all under the sole writable path, 0 forbidden hits |
| PR exists | yes — #759 |
| Required CI green | yes — all 4 required contexts SUCCESS |
| Non-required CI | `performance-gate` FAILURE — flaky, not blocking (F1/F2) |
| **PR merged into `dev`** | **YES — `16:44:41Z`, merge commit `9166d3ca`** |
| `29d9321d` ancestor of `origin/dev` | **yes** |
| Safe to run `done` | **YES** |

**Every closeout gate is satisfied.** `scripts/ai-status.sh done` verifies the
task branch head is an ancestor of the target branch before it moves state; that
check will pass.

Sequence for the parent owner:

1. Ignore T10's `next`. It names a non-required check that is flaky and was never
   blocking, and it predates the merge. Nothing needs resolving.
2. Do **not** run `progress`, `note`, or `blocker` to correct it — each either
   downgrades the approval to `in_progress` (turning `task-review-gate` red) or
   strands it.
3. Do **not** create a fresh commit to fix the F3 trailer. The head is merged and
   immutable; moving it after approval buys nothing.
4. Confirm the merge independently (`gh pr view 759 --json state,mergeCommit`;
   `git merge-base --is-ancestor 29d9321d origin/dev`), then run `done`.
5. Checkpoint message should record: approved head `29d9321d`, approver
   Antigravity at `16:20:27Z`, merge commit `9166d3ca` at `16:44:41Z`, that the
   `ci_failed` blocker was `performance-gate` — non-required and flaky — with the
   within-run 2-pass/1-fail evidence, that the reviewer trailer names Claude2
   pre-reassignment while Antigravity approved, and the F5 caveat that T11 must
   re-read the principal-map tenant.
6. Closing T10 unblocks **T11** and **T30**.

If `done` is refused for a dirty worktree in the canonical checkout, that is the
known drift-fixture condition and is unrelated to this task's scope — it does not
change the readiness verdict above.

## Sidecar verification record

Every command below was run by the preparer between `16:44Z` and `16:49Z`, with
the observed result. Nothing is quoted from the parent's evidence.

```text
date -u                       -> 2026-08-09T16:44:34Z … 16:49:18Z
git rev-parse origin/dev      -> e385c5d9  (16:44Z, pre-merge)
                              -> 9166d3ca  (16:49Z, post-merge)
git ls-remote origin refs/heads/task/ODP-P10-LIVE-EXTDATA-DIAG-001*
  -> 29d9321d…  task/ODP-P10-LIVE-EXTDATA-DIAG-001

gh pr view 759 --json state,mergeStateStatus,headRefOid,autoMergeRequest
  -> 16:44Z  OPEN   / UNKNOWN / 29d9321d / null
  -> 16:46Z  MERGED / UNKNOWN / 29d9321d / null
gh pr view 759 --json mergedAt,mergedBy,mergeCommit
  -> merged 2026-08-09T16:44:41Z by ajoe734, commit 9166d3ca
gh pr view 759 --json statusCheckRollup
  -> orchestrator SUCCESS, product SUCCESS, product-e2e-gate SUCCESS,
     task-review-gate SUCCESS, performance-gate FAILURE
gh api repos/alfloop-dev/odayplus/branches/dev/protection
  -> required contexts: orchestrator, product, product-e2e-gate, task-review-gate
     (performance-gate ABSENT -> not required)

git merge-base --is-ancestor 29d9321d origin/dev   -> exit 0 (ANCESTOR)
git ls-tree -r --name-only origin/dev -- docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/
  -> README.md, live-e2e-gate-run-31316767710.json,
     worker-validation-run-31316767710.json   (all present on dev)

git diff --name-only origin/dev...29d9321d  -> 3 files, all under the writable path
git diff --stat  origin/dev...29d9321d      -> 793 insertions(+), 0 deletions
git diff --check origin/dev...29d9321d      -> clean
forbidden-glob grep over the diff           -> no hit
  (apps|modules|shared|scripts|tests|.github|.orchestrator|models|docs/design|docs_archive)
git log -1 --format=%B 29d9321d
  -> LLM-Agent: Claude / Task-ID: ODP-P10-LIVE-EXTDATA-DIAG-001 /
     Reviewer: Claude2 (STALE, see F3) / Verified: read-only gh/git evidence capture

gh run view 31323018885 --log  (performance-gate on 29d9321d)
  -> attempt 1: 3 passed | attempt 2: 3 passed | attempt 3: 1 failed, 2 passed
  -> FAILED tests/performance/assisted_listing_intake/test_capacity.py::
     test_approved_capacity_and_slo_are_measured
     assert report["missed_targets"] == []
       got ['url_submission_durable_receipt','url_submission_receipt_error_budget']
gh api repos/.../commits/8eabc973/check-runs  (the PR's base commit)
  -> performance-gate FAILURE (run 31322265923, push)
  -> performance-gate SUCCESS (run 31321422749, merge_group pr-756)
gh api repos/.../commits/e385c5d9/check-runs  -> performance-gate SUCCESS
read .github/workflows/ci.yml:174-193
  -> 3 attempts, `exit ${status}` on the first red -> all-of, not best-of
read delivery_toolchain/load/assisted_listing_intake/runtime.py:39-54,153
  -> summarize() compares time.perf_counter() deltas to p95=0.5s / p99=1.5s

source re-read at origin/dev (all confirmed):
  .github/workflows/deploy-dev.yml:101-102        -> || 'tenant-dev'
  .github/workflows/deploy-staging.yml:84-85      -> || 'tenant-staging'
  delivery_toolchain/e2e/check_live_e2e_gate.py:1318-1321    -> operator_tenant or ENV chain
  apps/api/oday_api/main.py:772                   -> `if body.job_type == "forecast"` sole guard
  shared/.../operator_domains.py:_collection()    -> f"{collection}.tenant.{sha256}"
  product_ops/deployment/deploy_cloud_run_waji.sh:45-48          -> fail-closed guard present
  modules/external_data/.../ingestion_service.py:337-362 -> ScheduledIngestionTenantError
  apps/worker/oday_worker/handlers.py:92-120      -> NonRetryableJobError on empty tenant

sha256 recomputed locally
  tenant-dev                           -> 7c51172b…9c01f6   MATCHES README
  a11ce505-70bc-56d9-8564-ad22efa23c9e -> da57d47a…f834b5b  MATCHES README

json re-parse of live-e2e-gate-run-31316767710.json
  -> ok=false, 50 checks / 43 ok / 7 blockers, blocking_dependencies=[external-data, mlflow]
  -> 5 worker:* PASS, 3 runtime:provider_probe:* PASS, 3 data:* FAIL (runs=0)
  -> inputs.secret_values_redacted=true; inputs.worker_probe_provider_id present (F4)
secret scan of both artifacts (bearer / credential-field / conn-string / JWT / PEM)
  -> 0 hits in both

live ai-status.json (updated_at 2026-08-09T16:44:31Z)
  -> T10 review_approved, owner Claude, reviewer Antigravity
  -> T10 next = "CI checks ... failed; resolve failing checks before finalization." (FALSE, F1)
  -> blocked on T10: ODP-P10-LIVE-EXTDATA-REMEDIATE-001 (T11),
                     ODP-P10-DEV-REDEPLOY-VERIFY-001 (T30)

activity log, task_id=ODP-P10-LIVE-EXTDATA-DIAG-001
  -> 16:16:17Z Claude handoff at 29d9321d, PR #759
  -> 16:17:31Z Orchestrator reassigned review Claude2 -> Antigravity (shared account pool)
  -> 16:20:27Z Antigravity review_approved
  -> 16:20:52Z Orchestrator ci_failed -> overwrote T10 next  (the false blocker)
  -> 16:23:19Z Orchestrator github_auto_merge_enabled on #759
```

These prove the delivered surface, the approval binding, the merge, the
independent re-verification of all five root-cause links, and the five
residuals as of `2026-08-09T16:49Z`. They do not substitute for the parent
owner's own read at closeout.

## Handoff disposition

Ready for Claude to review as a sidecar support artifact.

For the **parent owner**: T10 is closeout-ready. The single most important thing
in this packet is that the blocker in your `next` field is false — `performance-
gate` is not required on `dev`, it is flaky, and #759 merged at `16:44:41Z`
regardless. Do not try to fix it, do not re-commit for the F3 trailer, and do not
touch `next`. Verify the merge yourself, then run `done` with the F1/F3/F5 facts
in the checkpoint message. That unblocks T11 and T30.

For the **coordinator**: F2 is the one item needing action outside T10's scope —
a flaky wall-clock performance gate that manufactures false `ci_failed` blockers
on docs-only PRs. It is a systemic tax on this phase, not a one-off, and
`ODP-CI-FLAKE-REMEDIATION-001` did not cover this test.

For **T11**: §7 of the parent README is a genuinely actionable handoff. Two
things to carry: re-read the smoke principal's `tenant_id` from
`ODP_AUTH_PRINCIPAL_MAP_SECRET` rather than trusting the documented literal (F5),
and preserve the stated direction of the alignment — move ingestion to the
operator tenant, never the reverse.

For **Antigravity**: no re-review is triggered. The approved head never moved,
the scope is unchanged, and all five residuals are either downstream of the
approval or explicitly disclosed by the parent. This packet is offered as a
record, not as a request to re-open.
