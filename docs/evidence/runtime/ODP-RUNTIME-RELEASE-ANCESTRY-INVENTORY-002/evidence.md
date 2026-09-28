# Evidence: ODP-RUNTIME-RELEASE-ANCESTRY-INVENTORY-002

## Summary

Fixed `check_candidate_ancestry` in `delivery_toolchain/e2e/check_release_gate_registry.py`
to accept `docs/audits/code-boundary-inventory.csv` (and any file under `docs/audits/`)
as an evidence-only path, preventing false rejections during the rollout PR
candidate-to-merge ancestry check.

## Root Cause

The `is_evidence_path()` function used by `check_candidate_ancestry()` did not include
`docs/audits/` in its allowlist of evidence-only path prefixes. When the generated
boundary inventory CSV (`docs/audits/code-boundary-inventory.csv`) was modified between
the release candidate SHA and the merge SHA, the ancestry checker treated it as a
product change and rejected the release with:

> intervening commits touch non-evidence paths: docs/audits/code-boundary-inventory.csv

## Fix

Added `docs/audits/` to the evidence-path allowlist in `is_evidence_path()`:

```python
or normalized.startswith("docs/audits/")
```

This is consistent with the existing pattern where `docs/evidence/`, `docs/release/`,
`docs/runbooks/`, `docs/testing/`, and `docs/uat/` are all treated as evidence-only.

## Files Changed

- `delivery_toolchain/e2e/check_release_gate_registry.py` — added `docs/audits/` prefix
- `tests/e2e/test_release_gate_registry.py` — added 3 regression tests

## Regression Tests Added

1. `test_is_evidence_path_accepts_generated_boundary_inventory` — unit-level check that
   `is_evidence_path` accepts `docs/audits/` paths and still rejects product paths
2. `test_cli_expected_sha_ancestry_inventory_only_descendant_passes` — end-to-end test
   reproducing the rollout PR inventory-only case (candidate → inventory commit → HEAD)
3. `test_cli_expected_sha_ancestry_inventory_plus_product_change_fails_closed` — verifies
   fail-closed behavior: inventory + product change still blocked

## Scope Constraints

- No changes to Runtime Release workflow, manifest, registry, lease, or deployment
- No changes to `check_product_release_gate.py`
- Preserves fail-closed behavior for product and build paths
