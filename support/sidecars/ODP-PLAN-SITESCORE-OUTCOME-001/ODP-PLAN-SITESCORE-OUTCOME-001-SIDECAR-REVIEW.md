# ODP-PLAN-SITESCORE-OUTCOME-001 Sidecar Review Packet

## Packet identity

- Sidecar task: `ODP-PLAN-SITESCORE-OUTCOME-001-SIDECAR-REVIEW`
- Parent task: `ODP-PLAN-SITESCORE-OUTCOME-001`
- Prepared by: Codex3
- Assigned sidecar reviewer: Antigravity
- Parent implementation head reviewed: `fb42ef7a1bc92a2eb1191ddc8bbda677d97f8eac`
- Current parent branch head at packet capture: `b2d9250f43316f10f25c62d3e77cd89cfc055c2a`
- Previous blocking head: `ebe994b15c75071571556d5eb68ecb05e559e542`
- Captured at: `2026-08-01T15:31:58Z`
- Scope: support evidence and reviewer handoff only; this packet changes no canonical contract, runtime, registry, receipt, or model-card truth.

## Outcome

The evidence supports clearing the two blockers recorded at `ebe994b1` for the owner implementation at exact head `fb42ef7a`. No new task-scoped blocker was found in the remediation delta.

Codex subsequently recorded an exact-head approval addendum in commit `b2d9250f`. The only change from `fb42ef7a` to `b2d9250f` is 60 lines of reviewer evidence in `docs/evidence/models/ODP-PLAN-SITESCORE-OUTCOME-001-review.md`; implementation, tests, generated receipt, and model-card bytes are unchanged. This sidecar packet is supporting material, not a second canonical approval.

SiteScore must remain `GOVERNED_DISABLED`. Neither this packet nor the parent approval authorizes `ACTIVE`: authoritative prediction-source evidence and authoritative M6/M12 outcome backfill remain owned by `ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001` and `ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001`.

## Remediation evidence

| Review concern | Evidence at `fb42ef7a` | Sidecar assessment |
| --- | --- | --- |
| B1: receipt aggregates were self-attested rather than derived from the supplied manifest | `verify_sitescore_gate2_receipt` reruns `evaluate_sitescore_opening_outcome_benchmark` over `dataset_manifest` and compares population digests, aggregate digest, observed/eligible/mature/matched counts, matched and unmatched means, revenue sum, and overall mean (`models/sitescore/opening_outcome.py:1283-1323`). | Cleared for the reviewed attack: a fully rebound unmatched-mean/revenue-sum forgery against the unchanged manifest is rejected. |
| B2: manifest binding omitted M6/M12, interval, eligibility, and segment evidence | `_build_canonical_manifest_record` includes strict eligibility, opening/maturity inputs, explicit M6/M12 outcomes, prediction, P10/P90 bounds, and target-format identity. The verifier re-derives M6/M12, prediction, interval and P80 counts/coverage, normalized MAE, calibration summary, and segment metrics (`models/sitescore/opening_outcome.py:1325-1348`). | Cleared for the reviewed attack: a fully rebound M6 coverage forgery against the unchanged manifest is rejected. |
| Governed lineage boundary | Producer and verifier keep governed lineage false until the authoritative prediction-source dependency exists; submitted receipt status or reason cannot establish lineage. | Preserved. Forged `ACTIVE`/`PASSED` remains fail-closed. |
| Outcome semantics | M6/M12 coverage requires explicit realized 180d/365d outcomes plus elapsed maturity; store age alone is insufficient. | Preserved. The present no-source artifacts truthfully remain governed-disabled. |

## Verification evidence

Executed in the clean parent worktree at exact implementation head `fb42ef7a`:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/models -k "sitescore or opening_outcome"
.venv/bin/ruff check scripts/models models tests/models
git diff --check
git diff --check ebe994b1..fb42ef7a
PYTHONPATH=. .venv/bin/pytest -q tests -k "sitescore or opening_outcome or model_ready"
```

Results:

- Task-scoped selector: 59 passed (57 in `test_sitescore_opening_outcome.py`, plus two selected model contract tests).
- Focused selector: 103 passed; warnings only.
- Ruff: clean.
- Worktree and remediation-delta whitespace checks: clean.

An independent sidecar probe generated a valid governed-disabled receipt from a two-row manifest, verified the baseline, then changed one manifest dimension at a time without changing the receipt:

| Mutation | Result |
| --- | --- |
| `is_training_eligible: true -> false` | Rejected with 14 verifier errors. |
| Valid P10/P90 -> reversed bounds | Rejected with 7 verifier errors. |
| `target_format_code` partition identity changed | Rejected with 3 verifier errors. |

The owner regressions additionally cover the prior fully hash-rebound aggregate and M6-coverage attacks.

## Head movement and PR state

- `fb42ef7a..b2d9250f` changes only the parent reviewer record; `git diff --exit-code` is clean across `models/sitescore/opening_outcome.py`, the benchmark CLI, the task test file, Gate 2 receipt, and model card.
- PR `#525` was open against `dev`, exact head `b2d9250f`, and `BEHIND` at capture time.
- `task-review-gate` was successful. The new CI run was still in progress at capture time.
- The immediately preceding run at `fb42ef7a` had green orchestrator/performance checks and a failing `product-e2e-gate`. Its log failed in `check_product_release_gate.py` because intervening commits touched the parent task's non-evidence paths; it was not a failure of the 59/103 SiteScore-focused tests. This merge-state/release-gate condition remains for the parent owner to resolve and is outside the sidecar's writable scope.

## Sidecar CI follow-up

Captured at `2026-08-01T23:17:19Z` after PR `#551` was returned to the owner:

- PR `#551` still points to the previously approved sidecar head `9ef60d3b19c3749b6253250660b15e2e964763c3`, based on `dev` commit `eed83c0937f491211247ee3fdb0bdf8d932564fb`.
- CI run `30706244856` passed `orchestrator` and `performance-gate`. The `product` job completed with 2,474 passing tests and one failing acceptance-coverage assertion; `product-e2e-gate` failed at the same release-gate check.
- Both failures have one shared diagnostic: `support/sidecars/ODP-PLAN-SITESCORE-OUTCOME-001/ODP-PLAN-SITESCORE-OUTCOME-001-SIDECAR-REVIEW.md` is an intervening non-evidence path relative to the recorded product E2E source.
- `scripts/e2e/product_e2e_receipt.py` currently restricts `EVIDENCE_COMMIT_ALLOWLIST` to the two raw E2E result files and `PRODUCT_E2E_EXECUTION_RECEIPT.json`. Therefore a committed packet at the task-required `support/sidecars/**` path cannot make this check green without changing the cross-cutting release-gate policy or regenerating canonical product E2E evidence.
- Neither action is authorized for this support-only sidecar. This packet records the conflict instead of weakening the gate, rebinding a product receipt, or broadening canonical truth.
- Parent PR `#525` remains open, `BEHIND`, and exact head `b2d9250f`; its task remains `review_approved` with the same CI family blocking finalization.

Required routing: Antigravity should preserve this packet as support evidence and route the release-gate/path-classification conflict to the owner of that canonical policy. The parent owner decides whether and how to absorb the packet; the sidecar supplies no gate override.

### Canonical dependency update

Rechecked at `2026-08-01T23:47:04Z`:

- PR `#551` exact head `f347dfdc1d5d215f005b16685e2eb93fc338d3ec` reproduced the same two failures in CI run `30723127257`: `product-e2e-gate` rejected the support packet as an intervening non-evidence path, while the product job completed 2,474 tests with only the corresponding acceptance-coverage assertion failing. Orchestrator and performance checks passed.
- Canonical policy owner task `ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001` now has PR `#562` at exact head `b047aa18baa81b4b47fda8b58fba0b7a7d4bb1d7`. It separates ordinary task/dev-merge evidence from production Gate 0-6 authorization and decouples static inventory checks from stale exact-source receipts.
- PR `#562`'s `product-e2e-gate` passed, demonstrating that the conflict has a canonical, fail-closed repair path. At capture time PR `#562` remained open: its product job was still running and its task-review gate was not approved. No `#562` bytes are copied into this sidecar.

Closeout dependency: wait for the canonical CI-boundary repair to merge into `dev`, then refresh PR `#551` from `dev` and rerun required checks. Until then this sidecar remains complete at the support layer but cannot honestly claim merge-ready CI.

### Canonical dependency re-review result

Rechecked after the canonical dependency's independent review at `2026-08-01T23:59:15Z`:

- PR `#562` remained open at exact head `b047aa18baa81b4b47fda8b58fba0b7a7d4bb1d7`. Its orchestrator, product, performance, and product-E2E jobs were green, but reviewer Codex9 rejected the task for an uncovered release-authority race in `.github/workflows/promote-dev-to-main.yml`.
- The recorded defect allows an older successful workflow run to validate one `dev` SHA while PR creation/reuse and the `task-review-gate` can act on a later mutable `dev` head. The canonical task must bind both `origin/dev` and the queried promotion PR head to `github.event.workflow_run.head_sha` before status emission or auto-merge.
- Because `#562` is back in progress and has not merged, PR `#551` cannot yet refresh from a repaired `dev` baseline or obtain an honest clean product-E2E rerun. The current `#551` failure remains the previously documented support-path classification conflict, not a SiteScore packet-content failure.
- The workflow and release-authority correction belongs to `ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001`. This support-only sidecar does not copy, modify, or pre-approve that repair.

Disposition remains fail-closed: the packet is reviewable as support evidence, but sidecar merge/finalization must wait for a reviewer-approved canonical repair to merge and for PR `#551` to rerun cleanly on that baseline.

### Owner re-dispatch audit

Rechecked at `2026-08-02T00:10:51Z` after the supervisor returned the sidecar to `in_progress` for required-check failure:

- PR `#551` remains open at exact approved head `e10f43bbd0fb2cb41c6cdb97568a160f81e1ae2f`. CI run `30724447866` passed `orchestrator` and `performance-gate`; `product-e2e-gate` failed again at the release-gate step, while the long-running `product` job was still executing at capture time.
- `task-review-gate` reports failure because the task was mechanically reopened to `in_progress` after CI failure. Antigravity's content review at the same exact head remains recorded as approved; no new packet-content rejection or SiteScore implementation finding was issued.
- Canonical dependency PR `#562` remains open at unchanged head `b047aa18baa81b4b47fda8b58fba0b7a7d4bb1d7`, and its task remains `in_progress` after the release-authority TOCTOU rejection. No reviewer-approved repair has merged into `dev`, so refreshing PR `#551` cannot yet supply the required repaired baseline.
- Parent PR `#525` has advanced to `b669017f7787e309573ab0edbe600b41095334a1` through a `dev` merge, but remains open and blocked with failing `product` and `product-e2e-gate` checks. This does not alter the exact `fb42ef7a` implementation evidence assessed by the packet or authorize parent closeout.

This re-dispatch produces no authorized local CI repair: changing workflow policy, broadening an evidence allowlist, or regenerating canonical product E2E truth would violate the sidecar boundary. The correct next action remains independent re-review of this updated support packet, followed by a clean rerun only after the canonical CI-boundary repair merges.

## Reviewer handoff

Antigravity should use this packet as supporting evidence and may absorb it into the parent closeout record if useful. Before parent finalization:

1. Confirm PR `#525` still points to `b2d9250f` or re-evaluate any later head.
2. Require all mandatory CI and branch-protection checks to reach a mergeable state; `BEHIND`, pending checks, or an open PR are not closeout.
3. Preserve the current `GOVERNED_DISABLED` claim and the explicit prediction-source/outcome-backfill dependencies.
4. Do not treat this sidecar packet as authority to modify or activate canonical SiteScore behavior.
5. Treat PR `#551`'s release-gate failure as an external closeout dependency: do not ask this sidecar to edit the allowlist or canonical E2E receipt.

Packet disposition: `READY_FOR_RE_REVIEW` for the sidecar task; parent implementation evidence supports the recorded exact-head approval, while merge closeout remains fail-closed pending canonical dependency PR `#562` and a clean PR `#551` rerun.
