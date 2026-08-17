# ODP-PLAN-OBSERVABILITY-LIVE-001 — CodexCoordinator Round 6 Review

- Implementation head reviewed: `73d96a5cb67f7979c9351d89380d2054f3945652`
- Decision: `CHANGES_REQUESTED`
- Review scope: per-signal window coverage, metric-domain validation, and
  current integration lineage

## Verified improvements

The Round-5 remediation now:

- requires an error/failure category and a latency/health category for a
  passing watch;
- rejects request-count-only evidence;
- recomputes the stored provider response and reconciles project, release SHA,
  metric types, timestamps, values, counts, and derived results;
- binds the recomputed provider proof hash into the canonical receipt.

The focused reliability test file passes: 40 tests passed.

## Blocking findings

### B1 — Window coverage is pooled across unrelated signals

`extract_and_reconcile_provider_proof()` appends every series timestamp into one
global list and checks only the minimum and maximum across that combined list
(`shared/observability/watch_window.py:98-101, 143-166, 209-222`).

A single error-count point at the beginning of the watch and a single latency
point at the end therefore produce a 15-minute combined span and a
`WATCH_PASSED` receipt, even though neither required signal was observed across
the window. The independent review reproduced this at exact head `73d96a5c`,
and the generated receipt also passed canonical verification.

Require each required signal/category to meet the documented cadence and
coverage contract independently. Do not infer continuous monitoring by pooling
timestamps from different metric series.

### B2 — Invalid negative metric values are accepted as healthy

Numeric conversion accepts negative error/failure counts and negative latency
or duration values. Error handling checks only `value > 0`, and latency checks
only the upper threshold (`watch_window.py:168-200`).

The independent review supplied negative values for both required categories.
Recording returned `WATCH_PASSED`, and `verify_watch_window_receipt()` accepted
the receipt. Counts, latency, duration, and similar health measurements must be
finite and within their valid non-negative domains before threshold
evaluation. Define the unit/domain per metric and reject NaN, infinity,
negative, or otherwise impossible values.

### B3 — The branch is not reconciled with current `origin/dev`

The reviewed branch merge base is
`e15df5140bd45968c4121e4cec0abdf8ec241e1f`, while current `origin/dev` is
`9e5c9f29670844ac4ecdec407c84255e0a33bce3`.

Merge current `origin/dev`, resolve observability/release evidence conflicts,
rerun the verification suite, and provide a formal exact-head owner handoff.

## Required defensive tests

Add tests proving:

1. each required signal/category independently covers the watch duration at
   the required cadence;
2. pooled start/end points from different signals cannot prove coverage;
3. negative, NaN, infinite, malformed, or unit-incompatible metric values are
   rejected by both recording and verification;
4. current integration-head reconciliation remains clean.

## Decision

`CHANGES_REQUESTED`. Exact head `73d96a5c` is not approved. The provider proof
binding is materially improved, but the current coverage and value-domain
rules can still issue a passing watch without valid continuous observations.
