# ODP-SBOM-CANDIDATE-OUTPUT-ISOLATION-001 Completion Evidence

## Task Summary
- **Task ID**: `ODP-SBOM-CANDIDATE-OUTPUT-ISOLATION-001`
- **Title**: 修正既有 SBOM CLI 的歷史證據覆寫副作用
- **Owner**: Antigravity3
- **Reviewer**: Claude2
- **Phase**: NLTK native remediation and merge

## Background and Problem Analysis

Prior to this fix, `delivery_toolchain/security/generate_sbom.py` contained an implicit side effect:
1. It unconditionally referenced a historical task evidence directory `EVIDENCE_TASK_DIR = ROOT / "docs/evidence/completion/ODP-OSS-LICENSE-GATE-002"`.
2. Whenever `generate_sbom.py` was executed (even when `--output` was explicitly specified for another candidate or sandbox), lines 659 and 664-666 created `EVIDENCE_TASK_DIR` and mirrored the generated `sbom.json` into `docs/evidence/completion/ODP-OSS-LICENSE-GATE-002/sbom.json`.
3. This mutated historical completion receipts of completed tasks and created cross-task receipt contamination.
4. Furthermore, CLI messages invoked `target_path.relative_to(ROOT)` directly without error handling, raising `ValueError` whenever `--output` was pointed outside the repository root (such as `/tmp/...`).

## Implementation Details

The following changes were made to `delivery_toolchain/security/generate_sbom.py`:
1. **Removed `EVIDENCE_TASK_DIR` and Mirroring Logic**:
   - Eliminated the `EVIDENCE_TASK_DIR` constant.
   - Removed `EVIDENCE_TASK_DIR.mkdir(...)` and `(EVIDENCE_TASK_DIR / "sbom.json").write_text(...)`.
   - Default output now strictly writes only to `OUTPUT_DIR / "sbom.json"` (`docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json`).
   - Custom `--output` strictly creates only `target_path.parent` and writes only to `target_path`.
2. **Safe Path Display Formatting**:
   - Introduced `_display_path(path: Path) -> str` which attempts `path.resolve().relative_to(ROOT.resolve())` and falls back to `str(path)` if `ValueError` occurs.
   - Applied `_display_path` to all error and confirmation messages across both generation and `--check` modes.
3. **Preserved Strict Fail-Closed Check and Security Contracts**:
   - `--check` mode remains read-only (zero writes to disk).
   - `--check` continues to fail closed on:
     - Missing file (exit 1).
     - Malformed or corrupt JSON (exit 1).
     - Stale components (exit 1).
     - Stale dependency graph (exit 1).
     - Stale repository release digests property (exit 1).
   - SBOM CycloneDX 1.5 schema, hash generation, and component cataloging logic are completely preserved.

## Caller Impact Audit

An audit of all callers across the repository confirmed:
- `deploy-dev.yml`: Configured with `SBOM_PATH: docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json` and executes `python3 delivery_toolchain/security/generate_sbom.py`.
- `tests/security/test_supply_chain_security_gate.py`: Validates `docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json` against `generate_sbom()`.
- `tests/security/test_oss_license_gate.py`: Executes `generate_sbom.py --check` against `docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json`.
- `docs/evidence/ODP_NLTK_UNPATCHED_DEPENDENCY_DISPOSITION_2026-09-05.md`: Explicitly required eliminating the mirror side-effect before candidate SBOM generation.
No caller depended on the implicit historical mirroring to `ODP-OSS-LICENSE-GATE-002`.

## Verification and Test Coverage

A dedicated regression test suite was created in `tests/tooling/test_generate_sbom_output_isolation.py`:
- `test_custom_output_creates_target_parent_and_writes_valid_sbom`: Verifies parent directory creation and valid CycloneDX 1.5 JSON output without mirroring claims.
- `test_custom_output_does_not_modify_or_create_historical_evidence`: Uses directory file SHA256 hashes to verify `ODP-OSS-LICENSE-GATE-002` and default `ODP-PGAP-SUPPLY-001` are untouched when custom output is specified.
- `test_output_outside_repo_and_display_formatting_does_not_raise`: Verifies `/tmp/...` paths format and execute cleanly without `ValueError` in both generation and `--check` modes.
- `test_check_mode_zero_writes`: Verifies `--check` performs zero writes (comparing file hashes and mtimes before and after).
- `test_check_missing_file_fails_closed`: Verifies `--check` exits 1 on missing file.
- `test_check_corrupted_json_fails_closed`: Verifies `--check` exits 1 on malformed JSON.
- `test_check_stale_components_fails_closed`: Verifies `--check` exits 1 on tampered components.
- `test_check_stale_dependencies_fails_closed`: Verifies `--check` exits 1 on tampered dependency graph.
- `test_check_stale_release_digests_fails_closed`: Verifies `--check` exits 1 on tampered repository release digests.

### Verification Matrix

| # | Command | Result |
|---|---|---|
| 1 | `uv run --python 3.12 pytest tests/tooling/test_generate_sbom_output_isolation.py -v` | exit `0`; 9 passed |
| 2 | `uv run --python 3.12 pytest tests/security/test_supply_chain_security_gate.py tests/security/test_oss_license_gate.py -q` | exit `0`; 57 passed |
| 3 | `uv run --python 3.12 ruff check delivery_toolchain/security/generate_sbom.py tests/tooling/test_generate_sbom_output_isolation.py` | exit `0`; `All checks passed!` |
