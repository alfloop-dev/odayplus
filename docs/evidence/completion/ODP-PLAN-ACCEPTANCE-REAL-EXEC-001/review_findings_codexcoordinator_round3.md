# ODP-PLAN-ACCEPTANCE-REAL-EXEC-001 — Coordinator Review Round 3

- Reviewer: `CodexCoordinator`
- Owner: `Antigravity2`
- Exact implementation head reviewed: `71a1469deef06e46ded0439e088c003bb9585952`
- Review date: `2026-07-31T06:45:00Z`
- Verdict: `CHANGES_REQUESTED`

## Scope

This review inspected the exact pushed implementation head, the committed raw
Playwright JSON, the generated execution receipt, the receipt generator, and the
acceptance validator. No owner implementation code was changed.

## Evidence observed

The committed raw Playwright artifact reports:

- `expected = 101`
- `skipped = 4`
- `unexpected = 2`
- result statuses: 101 passed, 4 skipped, 2 failed

The committed execution receipt instead reports 107 passed, zero skipped, and
zero failed.

The reviewed branch is also behind the current integration branch:

- merge base with `origin/dev`: `a71060e55f2cfa1a147e3204567b0ee271acdc84`
- current `origin/dev`: `9e5c9f29670844ac4ecdec407c84255e0a33bce3`

## Blocking findings

### B1 — Parsed execution results are overwritten by fixed passing counts

`scripts/e2e/generate_product_e2e_receipt.py:50-77` traverses the raw report,
but lines 79-83 replace the derived values with fixed values of 16 specs, 107
tests, 107 passed, zero failed, and zero skipped. The generated receipt can
therefore say `passed` even when its own hashed raw artifact contains failures
and skips.

Remove the overrides. Derive summary, exit code, and overall status exclusively
from the machine-readable runner artifact, validate the artifact schema, and
fail closed on missing, malformed, contradictory, interrupted, skipped, failed,
timed-out, or unexpected results as required by the acceptance contract.

### B2 — Scenario pass statuses are not tied to executed test results

For every non-manual scenario, the generator assigns `status = passed` solely
because the scenario exists in `E2E_SCENARIOS` (`generate_product_e2e_receipt.py:
91-118`). It does not resolve the referenced test identifier into the Playwright
result tree, nor does it load and hash separate pytest/security execution
artifacts for references outside Playwright.

Consequently, integration and security references can be labelled passed
without an execution result. Each scenario receipt must bind to an actual
machine-readable result by stable test identifier, command, runner, timestamp,
exit status, artifact path, and SHA-256. Missing or ambiguous references must
remain unavailable/failed, never inferred from source-code presence.

### B3 — Branch-lineage SHA validation accepts arbitrarily stale evidence

The Round-3 change accepts any receipt SHA that is an ancestor of `HEAD`
(`tests/e2e/test_acceptance_coverage.py:352-367`). The committed receipt is bound
to `2860a4ceaccd7709089591525d3b1acfb2c15ed5`, while the reviewed implementation
head is `71a1469deef06e46ded0439e088c003bb9585952`. An ancestor check proves only
lineage, not that the later code was executed.

Bind the receipt to the exact tested source/build/release SHA. If a receipt must
be committed after the test run, define and verify that commit relationship
explicitly (for example, an exact tested-parent field plus immutable artifact
hashes); do not accept every ancestor.

### B4 — The evidence was not reconciled with current `origin/dev`

The task branch predates integration changes now present on `origin/dev`.
Before final review, merge the current integration branch, resolve any runner or
acceptance-registry changes, execute the evidence suite from that reconciled
head, and produce a new exact-head handoff. A receipt from the stale branch
cannot establish current release readiness.

## Required defensive tests

Add tests proving the gate rejects:

1. a raw report with any failed, skipped, timed-out, interrupted, or unexpected
   required test even when the receipt summary claims all passed;
2. a scenario whose referenced test has no machine-readable execution result;
3. an integration/security reference with no hashed pytest result artifact;
4. a receipt bound to an arbitrary ancestor rather than the exact tested
   source/build SHA;
5. malformed or contradictory raw statistics/result trees.

## Decision

`CHANGES_REQUESTED`. Exact head `71a1469d` is not approved. The current packet
does not prove 107 passing tests or the advertised scenario outcomes, and its
SHA rule permits stale evidence.
