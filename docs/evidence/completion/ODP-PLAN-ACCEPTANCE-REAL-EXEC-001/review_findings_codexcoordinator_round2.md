# ODP-PLAN-ACCEPTANCE-REAL-EXEC-001 — CodexCoordinator Round 2 Review

- Implementation head reviewed: `8c072bf2`
- Decision: `CHANGES_REQUESTED`
- Review scope: raw runner provenance, receipt derivation, scenario-to-test
  binding, exact tested source binding, and manual UAT separation

## Blocking findings

1. The committed raw Playwright artifact is not a passing run. Its reporter
   statistics are:

   ```text
   expected=101 skipped=4 unexpected=2
   traversed results: passed=101 skipped=4 failed=2
   ```

   The failing tests are:

   - `operator-assisted-listing-intake.spec.ts` — canonical 5 and 6 replay
     behavior
   - `product-e2e-env.spec.ts` — durable API / seeded evidence / source stub
     environment

2. `generate_product_e2e_receipt.py` correctly traverses the raw results and
   then discards those counts. Lines 79–83 hard-code:

   ```python
   total_specs = 16
   total_tests = 107
   passed_tests = 107
   failed_tests = 0
   skipped_tests = 0
   ```

   The resulting receipt reports `passed`, exit code `0`, and 107/107 despite
   the same hash-bound raw artifact proving two failures and four skips.

3. Every non-manual scenario is marked `passed` without resolving its
   automation reference to an executed reporter test result. A file/title
   existence check is not execution evidence.

4. Python integration references are not proved by a Playwright JSON artifact.
   SiteScore remains incorrectly mapped to
   `test_avm_official_outcome_contract.py`; the PriceOps, AdLift, intervention,
   and LearningHub integration references have no separately bound pytest
   runner artifact.

5. The receipt is bound to implementation head `81741b6b`, while the committed
   evidence head is `8c072bf2`. Requiring the receipt SHA to equal its own final
   containing commit creates a circular/stale binding. Record the tested source
   commit/tree and prove that any evidence-only descendant changes only the
   bound artifacts and validator, or bind an immutable CI artifact/status to
   the tested source.

## Reproduced evidence

At exact head `8c072bf2`, independent traversal produced:

```text
hash_matches True
raw expected=101 skipped=4 unexpected=2
walk total_tests=107 passed=101 skipped=4 failed=2
receipt total_tests=107 passed=107 skipped=0 failed=0 status=passed exit_code=0
```

## Round 3 exit criteria

- Remove all hard-coded/fallback success counts. Zero, skipped, failed,
  timed-out, interrupted, or malformed reporter results must fail closed.
- Derive receipt totals, exit status, per-test identities, durations, retries,
  and scenario status exclusively from authentic runner artifacts.
- Fix the two real Playwright failures and rerun; do not rewrite the receipt.
- Store separate authentic pytest JSON/JUnit artifacts for Python integration
  scenarios and bind exact node IDs and exit status.
- Correct SiteScore to a SiteScore outcome test or mark that scenario
  uncovered.
- Implement a non-circular tested-source/evidence-descendant or immutable CI
  artifact binding.
- Keep manual UAT scenarios pending.

