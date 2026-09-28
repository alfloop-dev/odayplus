# Evidence: ODP-RUNTIME-RELEASE-ANCESTRY-INVENTORY-002

## Summary

Fixed `check_candidate_ancestry` in `delivery_toolchain/e2e/check_release_gate_registry.py`
to accept **only** `docs/audits/code-boundary-inventory.csv` as an evidence-only path,
preventing false rejections during the rollout PR candidate-to-merge ancestry check
while preserving fail-closed behavior for all other paths including sibling audit files.

## Root Cause

The `is_evidence_path()` function used by `check_candidate_ancestry()` did not include
`docs/audits/code-boundary-inventory.csv` in its allowlist of evidence-only paths. When
the generated boundary inventory CSV was modified between the release candidate SHA and
the merge SHA, the ancestry checker treated it as a product change and rejected the
release with:

> intervening commits touch non-evidence paths: docs/audits/code-boundary-inventory.csv

## Fix

Changed `is_evidence_path()` to accept the exact path only (not a prefix):

```python
or normalized == "docs/audits/code-boundary-inventory.csv"
```

The previous submission (PR #1379, head `68f7859a`) used an overbroad
`normalized.startswith("docs/audits/")` prefix that would have accepted arbitrary
sibling files. This revision narrows the exception to the single requested inventory
file.

## Files Changed

- `delivery_toolchain/e2e/check_release_gate_registry.py` — narrowed `docs/audits/`
  prefix to exact `docs/audits/code-boundary-inventory.csv` match
- `tests/e2e/test_release_gate_registry.py` — added/updated 4 regression tests
- `docs/evidence/runtime/ODP-RUNTIME-RELEASE-ANCESTRY-INVENTORY-002/evidence.md` —
  this file, with auditable verification

## Regression Tests

1. **`test_is_evidence_path_accepts_generated_boundary_inventory`** — unit-level check that
   `is_evidence_path` accepts the inventory CSV, rejects sibling audit files
   (`some-other-audit.json`, `security-scan.csv`), rejects product paths, and rejects
   build paths (`Makefile`, `Dockerfile`, `delivery_toolchain/release/release_manifest.py`)
2. **`test_cli_expected_sha_ancestry_inventory_only_merge_descendant_passes`** — merge-topology
   reproduction: candidate as first parent, inventory-only branch merged as second parent;
   verifies the ancestry check passes
3. **`test_cli_expected_sha_ancestry_inventory_plus_product_change_fails_closed`** — verifies
   fail-closed behavior when inventory + product changes are present
4. **`test_cli_expected_sha_ancestry_sibling_audit_file_rejected`** — verifies that a
   sibling file in `docs/audits/` (not the inventory CSV) is rejected by the ancestry check

## Scope Constraints

- No changes to Runtime Release workflow, manifest, registry, lease, or deployment
- No changes to `check_product_release_gate.py`
- Preserves fail-closed behavior for product and build paths
- Exception scoped to exactly one file: `docs/audits/code-boundary-inventory.csv`

## Focused Verification

- **Tested HEAD**: will be bound to commit SHA after anchor commit
- **Command**:
  ```
  uv run pytest tests/e2e/test_release_gate_registry.py -x -v --tb=short \
    --deselect tests/e2e/test_release_gate_registry.py::test_product_gate_accepts_expected_sha \
    --deselect tests/e2e/test_release_gate_registry.py::test_dev_merge_gate_accepts_valid_registry_and_require_go_checks_packet
  ```
- **Result**: 59 passed, 2 deselected in 16.34s, exit code 0
- **Deselected tests**: `test_product_gate_accepts_expected_sha` and
  `test_dev_merge_gate_accepts_valid_registry_and_require_go_checks_packet` — both fail
  due to pre-existing missing `@playwright/test` node module in the worktree environment,
  unrelated to this task's changes
- **PR**: #1379 (resubmitted with new head)
