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

## Reviewer handoff

Antigravity should use this packet as supporting evidence and may absorb it into the parent closeout record if useful. Before parent finalization:

1. Confirm PR `#525` still points to `b2d9250f` or re-evaluate any later head.
2. Require all mandatory CI and branch-protection checks to reach a mergeable state; `BEHIND`, pending checks, or an open PR are not closeout.
3. Preserve the current `GOVERNED_DISABLED` claim and the explicit prediction-source/outcome-backfill dependencies.
4. Do not treat this sidecar packet as authority to modify or activate canonical SiteScore behavior.
5. Treat PR `#551`'s release-gate failure as an external closeout dependency: do not ask this sidecar to edit the allowlist or canonical E2E receipt.

Packet disposition: `READY_FOR_RE_REVIEW` for the sidecar task; parent implementation evidence supports the recorded exact-head approval, while merge closeout remains fail-closed on the release-gate/path-classification conflict above.
