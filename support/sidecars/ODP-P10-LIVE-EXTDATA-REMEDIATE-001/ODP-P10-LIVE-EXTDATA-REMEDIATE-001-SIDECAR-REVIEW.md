# Package 10 Live External Data Remediation — Review Packet

- Sidecar task: `ODP-P10-LIVE-EXTDATA-REMEDIATE-001-SIDECAR-REVIEW`
- Parent task: `ODP-P10-LIVE-EXTDATA-REMEDIATE-001` (pack order `T11`)
- Helper kind: `review_packet`
- Sidecar owner: Claude2 · Sidecar reviewer: Codex
- Parent owner: Claude · Parent reviewer: Antigravity6
- Phase: `Package10LiveClosure`
- Prepared: `2026-08-11T11:49Z` · Revised: `2026-08-11T11:58Z` (§5.4 gates closed, §8 added)
- Companion artifact: `ODP-P10-LIVE-EXTDATA-REMEDIATE-001-SIDECAR-ACCEPTANCE.md` (same directory)

---

## 0. What this packet is, and what it is not

This is a **support-only review packet** for the parent's submitted change. It exists to
give the parent reviewer (`Antigravity6`) an *independently executed* verification of the
parent's claims, plus the decision points that a reviewer — not a helper — must rule on.

- **Non-mutating:** this sidecar changes no L1 canonical truth, no contract schema, no
  runtime/registry/governance implementation, and no task status other than its own.
- **Not an approval:** nothing here approves T11 or widens its scope. Where this packet
  finds that a decision exceeds the parent's authority, it routes the decision and stops.
- **Independently run, not restated:** every result in §3 and §4 was executed by this
  sidecar against a detached checkout of the frozen review head. Where a parent claim was
  reproduced, this packet says so and gives the observed output; the parent's evidence
  README was read *after* the replays were run, and no replay result is copied from it.

### Relationship to the ACCEPTANCE sidecar

The companion acceptance packet was written `2026-08-10`, when T11 was still `blocked` and
had shipped no code. Its §2 pre-condition analysis describes a state that has since moved:
T11 has dispatched, implemented, and submitted. **Where the two packets disagree on task
state, this one is current.** The acceptance packet's §3 forecast — that the required fix
would fall outside T11's declared writable ceiling and need a coordinator amendment — is
confirmed by what actually shipped (§5.1 below).

---

## 1. Frozen review target

| Field | Value |
| --- | --- |
| PR | [#809](https://github.com/alfloop-dev/odayplus/pull/809) → `dev` |
| Task branch | `task/ODP-P10-LIVE-EXTDATA-REMEDIATE-001` |
| `review_gate_sha` | `147fdcfd0f483a3a45bac913be59afb526b02ba2` |
| PR `headRefOid` | `147fdcfd0f483a3a45bac913be59afb526b02ba2` |
| Branch base (merge-base with `dev`) | `dc8fdb9b6d19d4d36091af6aabe713e1677eda54` |
| Parent task status | `review_approved` (was `review` at packet time — see §8) |
| Parent `approved_head` | `147fdcfd0f483a3a45bac913be59afb526b02ba2` |

**No head drift.** The PR head, the task record's `review_gate_sha`, and the commit
replayed in §3 are the same SHA. The reviewer is looking at exactly what was verified.

**Base drift is benign.** `origin/dev` has advanced to `e16dfde4` since the branch base.
The five intervening commits are all `ODP-ORCH-BRANCH-PROTECTION-PER-BRANCH-STRICT-001`
orchestrator work, and `git diff dc8fdb9b origin/dev` over the four reviewed paths is
**empty** — no overlap, so the merge queue composes this cleanly. The PR's `BLOCKED`
merge state is `task-review-gate` still `PENDING` (i.e. awaiting this review), not a
conflict. Per standing practice, do not base-advance the branch to clear `BEHIND`.

### Commits under review

```
147fdcfd  ODP-P10-LIVE-EXTDATA-REMEDIATE-001: record remediation evidence
755a72aa  ODP-P10-LIVE-EXTDATA-REMEDIATE-001: anchor external-fetch tenant binding
```

### Change inventory (4 files, +655 / −12)

| Status | Path | Lines |
| --- | --- | --- |
| `M` | `apps/api/oday_api/main.py` | +68 −1 |
| `M` | `delivery_toolchain/e2e/check_live_e2e_gate.py` | +19 −11 |
| `A` | `tests/integration/test_external_fetch_enqueue_tenant_binding.py` | +319 |
| `A` | `docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/README.md` | +249 |

---

## 2. The change in one paragraph (reviewer orientation)

T10 proved a split-brain: the live gate's worker probe enqueued an `external-fetch` job
carrying a **caller-supplied** `tenant_id`, the worker persisted the ingestion run into
that tenant's partition and reported success, and the gate's readback — scoped to the
*smoke principal's own* tenant — saw `runs=0` in the same execution. The fix makes
`POST /api/v1/jobs` stop trusting the caller for that value: for `job_type ==
"external-fetch"` the ingestion tenant is now bound to the authenticated principal (the
rule `forecast` already followed), a mismatched payload tenant is refused `403`, and the
queue idempotency key is tenant-qualified so two tenants cannot collapse onto one job.
The gate probe correspondingly stops guessing a tenant from deployment environment
variables and lets the API bind it.

---

## 3. Independent verification replay

Executed by this sidecar in a **detached worktree at `147fdcfd`**, isolated from both the
canonical worktree and this sidecar's task branch, and removed afterward. Toolchain:
Python `3.12.3`, pytest `9.1.1`, ruff `0.15.20`.

| # | Command | Result | Parent claim | Match |
| --- | --- | --- | --- | --- |
| V1 | `pytest tests/integration/test_external_fetch_enqueue_tenant_binding.py -q` | **11 passed**, exit 0 | 11 passed | ✅ |
| V2 | `pytest` over the 5 named regression suites † | **64 passed**, exit 0 | 64 passed | ✅ |
| V3 | `pytest tests/e2e/test_live_e2e_gate.py -q` | **132 passed**, exit 0 | 132 passed | ✅ |
| V4 | `ruff check` over the 3 changed code/test files | **All checks passed!** | All checks passed | ✅ |
| V5 | **Pre-fix replay** (see below) | **7 failed, 4 passed**, exit 1 | 7 failed, 4 passed | ✅ |

† `tests/integration/test_external_ingestion_persistence.py`,
`tests/integration/test_scheduled_ingestion_tenant_propagation.py`,
`tests/integration/test_forecastops_tenant_runtime_contract.py`,
`tests/reliability/test_cross_flow_gate.py`,
`tests/integration/test_worker_scheduler_runtime.py`.

### V5 — the regression genuinely reproduces

This is the claim most worth checking independently, because it is the one that
distinguishes a real deterministic regression from tests written to pass. Both code files
were reverted to the base blobs (`git checkout dc8fdb9b -- apps/api/oday_api/main.py
delivery_toolchain/e2e/check_live_e2e_gate.py`), leaving the new test file intact, and the focused
suite was re-run:

```
.F.FFFF.FF.                                                              [100%]
FAILED ...::test_enqueue_without_a_tenant_writes_where_the_same_credential_reads
FAILED ...::test_foreign_tenant_on_the_payload_is_refused_and_enqueues_nothing
FAILED ...::test_anonymous_enqueue_is_unauthenticated
FAILED ...::test_principal_without_the_integration_create_grant_is_forbidden
FAILED ...::test_principal_without_a_tenant_scope_is_forbidden
FAILED ...::test_two_tenants_sharing_one_idempotency_key_are_not_collapsed
FAILED ...::test_gate_enqueue_body_omits_the_tenant_instead_of_guessing
exit=1
```

7 failed / 4 passed, exactly as claimed. The parent's specific sub-claim that
`test_foreign_enqueue_tenant_reproduces_worker_success_with_no_api_readable_run` **passes
in both states by design** (it pins the partition mechanism, which this task does not
change) is also confirmed — it is absent from the failure list. The worktree was restored
to `147fdcfd` and removed; the canonical worktree was never used for test execution.

**Reviewer takeaway:** the tests fail without the fix and pass with it, on the durable
bundle. The regression is real and the suite has teeth.

---

## 4. Claim-by-claim verification of the parent's evidence README

Each row was checked against the diff and the tree, independently of the README's prose.

| # | Parent claim (README §) | Verified how | Verdict |
| --- | --- | --- | --- |
| C1 | `forecast:v1:` key format is unchanged **byte-for-byte** (§1) | Pre-fix built `f"forecast:v1:{tenant}:{key}"` under `idempotency_tenant_id is not None`, which only the `forecast` branch set. Post-fix builds `f"{idempotency_scope}:{tenant}:{key}"` with `idempotency_scope = "forecast:v1"` set in the same branch. Both variables are set together in each branch; no other branch sets either. | ✅ Equivalent |
| C2 | Cloud Scheduler never reaches the new binding (§4) | `apps/scheduler/oday_scheduler/main.py:105` calls `self.job_queue.enqueue(JobRequest(job_type="external-fetch", …))` **directly**, not over HTTP. Its key namespace (`scheduled-fetch:<tenant>:<window>`) cannot collide with `external-fetch:v1:`. | ✅ Confirmed |
| C3 | Idempotency keys are "per-request dedupe markers, not durable identity", so a mid-flight revert is at worst one re-enqueue (§4) | `shared/jobs/queue.py:128` — `InMemoryJobQueue._idempotency_index` is a process-local `dict`, not persisted. The key-shape change therefore has **no cross-deploy migration hazard**; the index resets on restart. | ✅ Confirmed, and stronger than claimed |
| C4 | Two of four files sit outside the writable ceiling (§6) | Ceiling read from the live task record's `writable_paths` (7 globs). `apps/api/oday_api/main.py` and `delivery_toolchain/e2e/check_live_e2e_gate.py` match none of them. The test file falls under `tests/**` (focused, matches the diagnosed path) and the evidence README under its own glob. | ✅ Confirmed — see §5.1 |
| C5 | "There is no fix inside the ceiling" (§6) | `apps/api/app/routes/external_data.py` **does exist** in the tree, so the ceiling entry is not a dead path — but it is the readback surface, not the enqueue surface. The tenant enters the system at `POST /api/v1/jobs`, which is in `main.py`. No writable path can bind an enqueue-time tenant. | ✅ Confirmed |
| C6 | New enqueue guard needs no credential change (smoke principal already holds `data_owner` → `integration:create`) | Not independently verifiable from the repo — depends on the live principal-map secret, which this worker cannot read. Cited to `ODP-OPERATOR-SMOKE-RBAC-LIVE-002`. | ⚠️ Carried on cited evidence |
| C7 | Acceptance criterion 3 (live candidate) cannot be closed from this worker | Consistent with T10 §6 (`gcloud` has no usable credentials on this host). Confirming artifact is the next `live-e2e-gate.json`. | ✅ Consistent — open by design |

### Structural checks the README does not make (retired non-findings)

Two things a careful reviewer would reasonably worry about; both check out clean, so they
are recorded here to save the reviewer the trace:

- **No mirrored unfixed enqueue path.** `main.py` defines the app inside a single
  `try: from fastapi import … / except ModuleNotFoundError: app = None / else:` block
  (line 109). The `else:` arm is the only app definition — there is no second copy of
  `enqueue_job` that kept the old trust-the-caller behaviour.
- **No second HTTP enqueue surface for this job type.** A tree-wide grep for
  `external-fetch` across `apps/`, `modules/`, `shared/`, `scripts/` returns only the
  worker's job-type constant, the scheduler's direct enqueue (C2), the gate script, and
  snapshot-id string literals. `POST /api/v1/jobs` is the only HTTP producer, so the guard
  has no bypass for `external-fetch`.

---

## 5. Reviewer decision points

### 5.1 Writable-path ceiling — a coordinator call, not a reviewer-only call

**The parent disclosed this itself, in bold, in README §6.** That is the right behaviour
and should be credited: it did not quietly assume the amendment. This packet independently
confirms the facts (C4, C5) and adds only the routing judgement.

The situation is genuinely forced:

- T11's ceiling was authored `2026-08-09`, **before** T10's diagnosis, on the expectation
  that the defect sat inside `modules/external_data/**`. T10 proved it does not.
- T10 §7 names `apps/api/oday_api/main.py` and `delivery_toolchain/e2e/check_live_e2e_gate.py`
  verbatim as the surfaces the successor task needs write access to, and the coordinator's
  dispatch note says "Execute README section 7 exact T11 handoff". **The intent is on
  record; only the JSON lags.**
- No correct fix exists inside the ceiling (C5).

So the reviewer's options are: (a) accept the two files as covered by the T10 §7 +
dispatch-note intent and record the ceiling as amended; or (b) rule them out of bounds, in
which case **T11 cannot be completed as specified** and must go back to the coordinator for
an explicit amendment before any correct fix can land. There is no third option where T11
completes inside the current JSON.

**This sidecar's recommendation:** option (a), with the amendment recorded explicitly on
the task record rather than left implicit in the PR — otherwise the next reader of
`writable_paths` sees a violation with no trail. This is a recommendation only; the
sidecar has no authority to widen the parent's scope.

### 5.2 Residual risk — a sibling job type keeps the pattern this task removed

**Found by this sidecar; not mentioned in the parent's evidence.** Out of T11 scope, and
**not a defect in this PR** — recorded so it is tracked rather than lost.

`POST /api/v1/jobs` takes `job_type` as a free-form string (`Field(min_length=1)`, no
allowlist). After this change, tenant binding covers exactly two branches — `forecast` and
`external-fetch`. A third registered job type, `assisted-listing-intake`
(`apps/worker/assisted_listing_intake/worker.py:28`), still reads its tenant straight from
the job payload and uses it as a partition key:

```
worker.py:172   tenant_id_val = ensure_uuid(payload.get("tenant_id", "0000…0000"))
worker.py:191   partition_key = f"{tenant_id_val}:{job.job_id}"
worker.py:422   resolved_tenant_id = payload.get("tenant_id") or "0000…0000"
```

That is the same trust-the-caller shape T11 just removed for `external-fetch`, including a
zero-UUID default when the field is absent. I also found no router-level dependency on
`platform_router = APIRouter()` (line 799) and no `add_middleware` in this module, meaning
authentication on this route is enforced *inside* the per-job-type branches — so job types
without a branch get neither tenant binding nor an auth check **in this module**.

**Confidence and scope of that last point:** traced in `main.py` only. A deployment-level
gateway or an auth layer outside this module could cover it, and this worker cannot verify
the deployed topology. It should be confirmed, not assumed either way.

**Framing for the reviewer:** T11 strictly *improves* this surface and regresses nothing.
Widening the guard to `assisted-listing-intake` would exceed T11's diagnosed path and its
ceiling, so it correctly does not belong in this PR. Recommend a follow-up task to (i)
confirm the auth posture of the generic `/jobs` route and (ii) decide whether tenant
binding should be default-on for all job types with an explicit opt-out, rather than
opt-in per branch.

### 5.3 Known documentation gap — disclosed, non-blocking

README §6 records that `docs/evidence/ODP_LIVE_E2E_GATE.md` is now stale in one respect:
its "grants the smoke principal must carry" matrix needs a `POST /api/v1/jobs`
(`external-fetch`) → `integration:create` → `data_owner` row, because the enqueue route now
requires a grant it previously did not. The parent deliberately did not ship it (outside
the ceiling, not named in T10 §7). Nothing is blocked — the live principal already holds
`data_owner` — but this should be captured as a follow-up so it is not lost at closeout.

### 5.4 CI state — now closed

| Check | At packet time (`11:49Z`) | At revision (`11:58Z`) |
| --- | --- | --- |
| `orchestrator` | ✅ SUCCESS | ✅ pass (1m22s) |
| `performance-gate` | ✅ SUCCESS | ✅ pass (59s) |
| `product` | ⏳ IN_PROGRESS | ✅ pass (13m58s) |
| `product-e2e-gate` | ⏳ IN_PROGRESS | ✅ pass (6m20s) |
| `task-review-gate` | ⏳ PENDING (awaits this review) | ✅ pass — approved by `Antigravity6` |

All five checks are green **at the exact head `147fdcfd`**, and PR #809 now reports
`mergeStateStatus: CLEAN`. Acceptance criterion 5 ("focused integration, live gate, Ruff
diff and exact-head CI pass") is therefore **fully evidenced**: V1–V4 locally plus
exact-head CI. Read via `gh pr checks 809` / `gh pr view 809` at `2026-08-11T11:58Z`.

---

## 6. Acceptance criteria — evidence map

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Deterministic regression reproduces worker success with missing API run | ✅ **Independently confirmed** | V5: 7 failed pre-fix / 11 passed post-fix. `test_foreign_enqueue_tenant_reproduces_worker_success_with_no_api_readable_run` pins worker `SUCCEEDED` + readback `[]`. |
| 2 | Smallest fix makes worker and API share one durable run | ✅ Confirmed on the merits | Fix is request-path only, at the single point where the tenant enters. C5 shows the alternatives (deployment variable, principal-map secret) fix one instance, not the failure mode. |
| 3 | Real candidate produces both provider runs with lineage | ⏳ **Open — cannot close here** | Needs a live Deploy Dev run; no `gcloud` credentials on this host. Confirming artifact: next `live-e2e-gate.json`. Exit conditions enumerated in README §7. |
| 4 | Retry idempotency, DQ, tenant audit, failure classification remain fail-closed | ✅ Confirmed | V2 (64 passed) — `test_scheduled_ingestion_tenant_propagation.py` re-asserts scheduler fail-closed tenant handling, cross-tenant replay refusal, audit-event tenant, and dead-lettering of untenanted payloads, all unchanged. Plus C1/C3. |
| 5 | Focused integration, live gate, Ruff diff, exact-head CI pass | ✅ **Confirmed** (was partial at packet time) | V1–V4 green locally at the exact head, and all five GitHub checks now green at `147fdcfd` (§5.4). |
| 6 | Independent review and rollback evidence pass | ✅ Rollback sound; review is the reviewer's call | Rollback is a single revert: request-path only, no migration, no data change, no config to undo. C3 confirms the one forward-compat caveat is weaker than the parent claimed. This packet is the independent-verification input, not the approval. |

---

## 7. Recommendation and handoff

**To the parent reviewer (`Antigravity6`):** every locally reproducible claim in the
parent's evidence README was independently re-executed by this sidecar and matched exactly
— including the pre-fix regression replay, which is the claim that matters most and the
one most easily faked. The diff is narrow, the tests have teeth, the rollback is trivial,
and the one scope deviation was self-disclosed rather than hidden.

Two things were flagged as gating approval. **The parent has since been approved by
`Antigravity6` at `147fdcfd`**, so both are recorded here at their settled state:

1. **§5.1 — still open as a trail gap, not as a blocker.** The parent's task record at
   `2026-08-11T11:58Z` still carries the original seven `writable_paths` globs, and no
   amendment note was added. The reviewer effectively took option (a) — accepting the two
   files under the T10 §7 + dispatch-note intent — but the ruling lives only in the review,
   not on the task record. That is precisely the outcome §5.1 warned against: the next
   reader of `writable_paths` sees two files outside the ceiling with no trail. **Recommend
   the coordinator record the amendment on T11 at closeout**, retroactively if necessary.
   This does not warrant reopening an approved head.
2. **§5.4 — closed.** `product` (13m58s) and `product-e2e-gate` (6m20s) both landed green
   at `147fdcfd`, `task-review-gate` passed, and PR #809 is `CLEAN`.

Criterion 3 (§6) stays open by construction and is expected to close on the next Deploy
Dev at the SHA carrying this change; it was correctly not a reason to withhold approval.

**To the sidecar reviewer (`Codex`):** this packet is support-only. Please confirm it
stays inside the sidecar boundary — no canonical truth, no contract schema, no
runtime/registry/governance implementation, single artifact under
`support/sidecars/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/`.

---

## 8. Sidecar resubmission record (head-drift reopen)

`Codex` reopened this sidecar at `2026-08-11T11:54Z` on exact-head integrity, not on the
merits: *"Review merits pass, but approval is blocked by exact-head integrity."* No content
change was requested. This section records what moved and why the head changed again.

| Event | SHA | Note |
| --- | --- | --- |
| Original submission (PR #810) | `d3fb0679` | `review_submission.remote_sha` recorded here |
| `origin/dev` composition into the task branch | `15ed73c3` | Merge of `origin/dev` (`e16dfde4`); PR head advanced, `review_gate_sha` followed, `remote_sha` did not → drift |
| This revision | new head | §5.4 / §6-5 / §7 updated; reviewer name corrected `Claude` → `Codex` |

**The packet content did not change across the composition.** `git diff d3fb0679 15ed73c3
-- support/` is empty; the three files the merge touched are unrelated
`ODP-ORCH-BRANCH-PROTECTION-PER-BRANCH-STRICT-001` orchestrator work. The reopen was a
bookkeeping mismatch between the recorded submission SHA and the live PR head.

**Why this revision is a new commit rather than a bare resubmit at `15ed73c3`.**
`task_finalize.sh` validates `HEAD` for the task id plus the `LLM-Agent` / `Task-ID` /
`Reviewer` trailers (lines 105–129). `15ed73c3` is a merge commit with an empty body, so a
resubmit at that exact SHA is refused by the script. A trailer-carrying commit was required
regardless; this revision folds the genuinely new facts (§5.4 gates closed, §7 item 1
settled) into it rather than shipping an empty one. The commit also corrects the stale
`Reviewer: Claude` trailer to `Reviewer: Codex` after the assignment reconciliation.

Sidecar boundary is unchanged: one file under
`support/sidecars/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/`, no canonical truth, no contract
schema, no runtime/registry/governance implementation.

### Provenance

- Task state read from the live canonical status root
  (`$PANTHEON_STATUS_ROOT/ai-status.json`), not the repo worktree copy — the tracked
  `ai-status.json` is a four-task sample fixture and contains neither T10 nor T11.
- PR and check state read via `gh pr view 809` at `2026-08-11T11:49Z`, and re-read via
  `gh pr checks 809` / `gh pr view 809 --json headRefOid,mergeStateStatus` at
  `2026-08-11T11:58Z` for §5.4 and §7.
- All test and lint results in §3 executed by this sidecar at `147fdcfd`.
- No secret value, bearer token, or credential appears in this packet.
