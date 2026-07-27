# ODP-SITESCORE-OPERATOR-V2-001 Acceptance Receipt

Task: Wire Operator canonical flow to SiteScore v2 contract
Owner: Codex · Reviewer: Codex9 · PR: #387 → `dev`; receipt PR: #421
Integration baseline: PR #381 head `59da5c51`

This receipt records only runs that completed with a terminal exit code on
this branch. Counts below are collected/executed test counts from those runs,
not estimates.

## Authoritative Test Runs

Working tree: `task/ODP-SITESCORE-OPERATOR-V2-001`, worker sandbox
(Python 3.12, pytest).

| # | Command | Result | Exit code |
|---|---|---|---|
| 1 | `python3 -m pytest tests/integration/test_operator_canonical_wiring.py modules/sitescore/tests modules/opsboard -q -p no:randomly` | 30 tests executed, 29 passed, 1 failed (`test_rebalance_invokes_avm_and_netplan_oss_and_persists_results`) | 1 |
| 2 | `python3 -m pytest tests/integration/test_operator_canonical_wiring.py modules/sitescore/tests --tb=no -q --deselect tests/integration/test_operator_canonical_wiring.py::test_rebalance_invokes_avm_and_netplan_oss_and_persists_results` | 29 passed, 1 deselected | 0 |
| 3 | `python3 -m ruff check modules/opsboard/application/network_listings.py modules/sitescore/application/reporting.py modules/sitescore/tests/test_sitescore_production_runtime.py tests/integration/test_operator_canonical_wiring.py` | All checks passed | 0 |

Collected counts (`pytest --co -q`):

- `tests/integration/test_operator_canonical_wiring.py`: 15 tests
  (5 of them are the new `heat_zone` cases)
- `modules/sitescore/tests`: 15 tests
  (6 of them are the new `model_metadata` / `output_transform` cases)
- `modules/opsboard`: 0 tests collected — the module has no test package,
  so run #1 gains nothing from that path. Recorded here so the 30-test total
  is not read as broader coverage than it is.

### The one failure in run #1 is environmental, not a regression

`test_rebalance_invokes_avm_and_netplan_oss_and_persists_results` fails with:

```
modules.netplan.application.production.NetPlanProductionExecutionError:
CVXPY robust NetPlan failed closed: CVXPY is not installed; robust NetPlan failed closed.
```

`cvxpy>=1.7,<2` is a declared runtime dependency (`pyproject.toml:38`) that is
not installed in this worker sandbox. The test and the NetPlan module are
untouched by this branch's diff. Owner-side acceptance is therefore **not**
claimed for that test — it is deferred to product CI, which installs the
declared dependency set. No claim is made here about full product CI having
passed; that gate has not been run to completion by the owner.

## Rejection Items Closed In This Round

Both items came from the coordinator NO-GO on the previous evidence packet.

### M7 — blank `heatZoneId` must be rejected before persistence

`modules/opsboard/application/network_listings.py` `_dict_to_candidate()`

Before: the guard only tested `"heatZoneId" not in d`, and the draft was built
with `heat_zone_id=d["heatZoneId"] or ""`. `None`, `""` and whitespace-only
values therefore **persisted as `""`**, leaving the candidate unjoinable to its
H3 cell aggregates. This was a real fail-open path.

After: missing, `None`, non-`str`, empty and whitespace-only are all rejected
with `ValueError("... has no usable heatZoneId ...")` before
`save_candidate()` is reached.

Tests (`tests/integration/test_operator_canonical_wiring.py`):

- `test_operator_candidate_write_rejects_blank_heat_zone_before_persistence`
  — parametrized over `None`, `""`, `"   "`, `"\t\n"` (4 cases). Drives the
  production writer `NetworkListingService.save_candidate()` →
  `_sync_candidate_to_repo()`, then reads the durable repository back to prove
  the pre-existing candidate still carries its original heat zone and
  point-in-time contract, i.e. the rejection happened before any partial write.
- `test_operator_candidate_write_rejects_missing_heat_zone_key` — the original
  absent-key gap stays closed (note: the raised type changes from `KeyError`
  to `ValueError`; no caller on this path catches `KeyError`).

### M6 — missing / non-dict `model_metadata` must fail closed

`modules/sitescore/application/reporting.py`
`score_candidates_with_execution()`

Accuracy note, since the previous packet overstated this one: the pre-fix code
was **already fail-closed in effect**. `output_transform=None` reached
`require_output_contract()`, which raises `ModelOutputContractError` before any
report is scored or persisted. What was wrong was the *diagnosis and the
boundary*: the `getattr(inference, "model_metadata", {})` duck-typing masked a
malformed runtime, and the operator saw the generic message "registered model
output transform is missing" rather than the actual fault — the registered
model returned no metadata mapping at all.

After: the metadata mapping is validated at the reporting boundary and raises
`ModelOutputContractError` naming the observed type, or naming the absent
`output_transform` key. Direct attribute access replaces the `getattr`
duck-typing (N8).

Tests (`modules/sitescore/tests/test_sitescore_production_runtime.py`):

- `test_production_scoring_fails_closed_on_unusable_model_metadata` —
  parametrized over `None`, a `str`, a `list`, `{}`, and a mapping carrying
  only `feature_schema_version` (5 cases). Each asserts the specific error and
  then asserts `repository.history(...) == []` and `repository.latest(...) is
  None`, so "fails closed" means "before persistence", not "after a partial
  write".
- `test_production_scoring_accepts_declared_output_transform` — control case
  proving a conforming transform still scores (`m12.p50 == 106_531.25`).

## Fault Injection (tests proven to fail without the fix)

The previous round was rejected because a green suite coexisted with the bugs.
Both guards were therefore reverted in the working tree and the new tests were
re-run against the pre-fix code:

```
FAILED ...::test_operator_candidate_write_rejects_blank_heat_zone_before_persistence[None-null]
FAILED ...::test_operator_candidate_write_rejects_blank_heat_zone_before_persistence[-empty]
FAILED ...::test_operator_candidate_write_rejects_blank_heat_zone_before_persistence[   -whitespace]
FAILED ...::test_operator_candidate_write_rejects_blank_heat_zone_before_persistence[\t\n-whitespace-control]
FAILED ...::test_operator_candidate_write_rejects_missing_heat_zone_key
FAILED ...::test_production_scoring_fails_closed_on_unusable_model_metadata[None-type NoneType]
FAILED ...::test_production_scoring_fails_closed_on_unusable_model_metadata[output_transform=90d-type str]
FAILED ...::test_production_scoring_fails_closed_on_unusable_model_metadata[metadata2-type list]
FAILED ...::test_production_scoring_fails_closed_on_unusable_model_metadata[metadata3-declared no output_transform]
FAILED ...::test_production_scoring_fails_closed_on_unusable_model_metadata[metadata4-declared no output]
```

10 of 11 new cases fail without the fix. Honest reading of the split:

- The 5 `heat_zone` failures are a real fail-open regression being closed —
  pre-fix, the blank values were persisted with no error raised at all.
- The 5 `model_metadata` failures are **message-specificity** failures. Pre-fix
  those inputs also refused to score, but with the wrong diagnosis. Reviewers
  should read M6 as a diagnosability and boundary fix, not as the closing of a
  fail-open hole.

The guards were restored immediately after this run; the committed tree
contains the fixed code.

## Acceptance Criteria Status

| Criterion | Status | Evidence |
|---|---|---|
| Use PR #381 head `59da5c51` as the integration baseline | Met | `59da5c51` is in this branch's history (`git log origin/dev..HEAD`) |
| Operator canonical workflow supplies the complete SiteScore v2 feature contract | Met | `test_operator_convert_carries_point_in_time_contract_into_scoring`, `test_operator_listing_write_does_not_wipe_candidate_features` (API-driven; no test-side `save_candidate`) |
| Missing or stale point-in-time inputs still fail closed | Met | `test_canonical_scoring_fails_closed_when_point_in_time_features_missing`, `..._when_feature_snapshot_time_is_stale_or_missing`, `..._when_ungeocoded_cell_has_no_h3`, `test_operator_convert_without_cell_aggregates_still_fails_closed` |
| Both failing operator canonical wiring tests pass | Met | Run #2, exit 0 |
| Full product CI and independent review pass before merge | Met | PR #387 product, product-e2e-gate, and orchestrator CI passed; independent review approved the merged scope |

## Exact-head Revalidation After Reviewer Substitution

The task was reopened after merge so the authorized owner/reviewer pair could
revalidate the delivered scope at the current `origin/dev` exact head
`a2a3c920`. The original delivery commit `b2645e8e` remains an ancestor of
`origin/dev` through PR #387 merge commit `2cfc2252`.

Runs on 2026-07-27:

- `python3 -m pytest tests/integration/test_operator_canonical_wiring.py -q
  --tb=short` — 14 passed; the one failure was the unchanged NetPlan test
  because this worker does not have the declared `cvxpy` dependency.
- The same command with
  `--deselect tests/integration/test_operator_canonical_wiring.py::test_rebalance_invokes_avm_and_netplan_oss_and_persists_results`
  — 14 passed, 1 deselected, exit 0.
- `python3 -m ruff check modules/opsboard/application/network_listings.py
  modules/sitescore/application/reporting.py
  modules/sitescore/tests/test_sitescore_production_runtime.py
  tests/integration/test_operator_canonical_wiring.py` — exit 0.

Codex2's independent review is recorded as APPROVE in task state: the complete
SiteScore v2 contract, missing/stale fail-closed paths, tenant isolation, and
lineage paths all have direct coverage. The reviewer also confirmed PR #387's
product, product-e2e-gate, and orchestrator CI were green. The sole closeout
hygiene note was this receipt's stale owner/reviewer header, corrected above.

## Fresh Revalidation After Dev Advanced

PR #421 was refreshed after reviewed P4 merge `88e2dbd4` advanced `origin/dev`.
Merge commit `958d0c36` composes that exact dev head without changing the
SiteScore, OpsBoard, NetPlan, or AVM implementation delivered by PR #387.
These are fresh local runs from the refreshed tree on 2026-07-27; no result
from the stale `9909d1e5` PR head is reused:

- `python3 -m pytest tests/integration/test_operator_canonical_wiring.py
  modules/sitescore/tests -q -p no:randomly --tb=short` — 29 passed and the
  unchanged cross-module NetPlan test failed because CVXPY is not installed in
  this worker environment.
- The same command with
  `--deselect tests/integration/test_operator_canonical_wiring.py::test_rebalance_invokes_avm_and_netplan_oss_and_persists_results`
  — 29 passed, 1 deselected, exit 0.
- `python3 -m ruff check modules/opsboard/application/network_listings.py
  modules/sitescore/application/reporting.py
  modules/sitescore/tests/test_sitescore_production_runtime.py
  tests/integration/test_operator_canonical_wiring.py` — exit 0.

The refreshed exact PR head must receive new required CI results and independent
approval from the currently assigned reviewer, Codex9, before merge.

## Model-ready Composition Revalidation

After PR #421 head `5cb20055` passed CI, `origin/dev` advanced again to
`b3f0fba6` through the approved model-ready composition in PR #417. This task
branch was rebased onto that exact base. The intervening changes affect model
data ingestion, release contracts, migrations, and their tests; they do not
modify SiteScore or OpsBoard. The rebase completed without a content conflict,
and the task diff remains limited to this receipt.

Fresh runs on 2026-07-27 from the recomposed tree:

- `python3 -m pytest tests/integration/test_operator_canonical_wiring.py
  modules/sitescore/tests -q -p no:randomly --tb=short` — 29 passed and the
  unchanged cross-module NetPlan test failed because CVXPY is not installed in
  this worker environment.
- The same command with
  `--deselect tests/integration/test_operator_canonical_wiring.py::test_rebalance_invokes_avm_and_netplan_oss_and_persists_results`
  — 29 passed, 1 deselected, exit 0.
- `python3 -m ruff check modules/opsboard/application/network_listings.py
  modules/sitescore/application/reporting.py
  modules/sitescore/tests/test_sitescore_production_runtime.py
  tests/integration/test_operator_canonical_wiring.py` — exit 0.

The new exact PR head must receive fresh required CI results and independent
approval from Codex9. Approval of `5cb20055` must not be reused.
