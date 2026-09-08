---
evidence_id: ODP-DRIFT-SECURITY-VERIFY-003
title: "NLTK Dependency Removal & Native Monitoring Security Evidence"
date: 2026-09-08
status: PENDING_INDEPENDENT_REVIEW
owner: Antigravity4
reviewer: Codex
repository: alfloop-dev/odayplus
task: ODP-DRIFT-SECURITY-VERIFY-003
verified_ref: c4bf87d81d55180d6d6769daf5358992b3bc6620
base_ref: c4bf87d81d55180d6d6769daf5358992b3bc6620
upstream_pr_chain:
  - PR_1219: ODP-DRIFT-NATIVE-MIGRATION-001
  - PR_1222: ODP-DRIFT-DEP-REMOVE-002
  - PR_1188: ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001
---

# NLTK Dependency Removal & Native Monitoring — Final Security Evidence

## 1. Executive Summary

This document records the security verification evidence for the complete
removal of `evidently`, `nltk`, `defusedxml`, and `regex` from the production
dependency chain, and the operation of four-dimension native monitoring, on
exact integration SHA `c4bf87d81d55180d6d6769daf5358992b3bc6620`.

**Status**: PENDING_INDEPENDENT_REVIEW — This evidence document awaits
independent reviewer approval and PR merge into `dev` before the remediation
chain can be called verified.

**Receipt storage**: All raw execution outputs are saved as individual files
in `receipts/` alongside this document. Each file's SHA256 hash is recorded
in §11. No receipt is hand-transcribed — every entry is a file saved from
actual stdout/stderr redirection at execution time.

### 1.1 SHA Relationship

- **Base SHA** (`origin/dev` tip at branch creation): `c4bf87d81d55180d6d6769daf5358992b3bc6620`
- **Task branch HEAD**: see PR head at submission time
- The base SHA already contains all upstream PR merges (#1219, #1222, #1188).
  This task adds only verification tests, evidence documents, and raw receipts;
  it does not change production code, dependencies, or lock files.

## 2. Tool Versions & Execution Environment

Recorded in `receipts/tool_versions.json` (SHA256: `663a9d77e698f95bd5b2951696200279fc4d0da5c07ff24d52d1db26b40e1963`):

| Item | Value |
|---|---|
| Python Version | CPython 3.12.14 (main, Aug 14 2026, 15:34:45) [Clang 22.1.3] |
| Platform | Linux-7.0.0-1011-gcp-x86_64-with-glibc2.39 |
| pip-audit Version | 2.10.1 |
| Advisory DB Service | PyPI (default) |
| Execution Timestamp | 2026-09-08T01:24:59Z |

## 3. Dependency Removal Verification

### 3.1 pyproject.toml

- SHA256: `130f024b80d55f439aa5467f4f469bb127aec955ef71444d2951a93d0f5d0391`
- `evidently` is NOT declared in `[project.dependencies]` or `[project.optional-dependencies]`.
  Verified by direct text search and `test_pyproject_does_not_declare_evidently`.

### 3.2 uv.lock

- SHA256: `ba5c393e49538e4da9e59de001cffc28c4cadfab8aebacf6dbf1503eb3a49a5d`

| Banned Package | Status in uv.lock |
|---|---|
| `evidently` | ❌ NOT PRESENT |
| `nltk` | ❌ NOT PRESENT |
| `defusedxml` | ❌ NOT PRESENT (sole reverse-dep of nltk, §3.2 disposition) |
| `regex` | ❌ NOT PRESENT (sole reverse-dep of nltk, §3.2 disposition) |

Lock consistency: `uv lock --check` passed (exit 0), verified by `test_lock_consistency`.

### 3.3 Installed Package Inventory (Full Audited Scope)

**Receipt file**: `receipts/installed_inventory.json`
**SHA256**: `efcd05d48227bd8d8a701c9dcc3e45aeef8c6b88ac323b46a3e63dc48de89de6`

The inventory was generated via `importlib.metadata.distributions()` listing
all 215 installed packages with exact name and version. This is the same scope
that `pip-audit --path` audits.

| Package | `importlib.metadata.distribution()` Result |
|---|---|
| `evidently` | `PackageNotFoundError` — NOT IN INVENTORY |
| `nltk` | `PackageNotFoundError` — NOT IN INVENTORY |
| `defusedxml` | `PackageNotFoundError` — NOT IN INVENTORY |
| `regex` | `PackageNotFoundError` — NOT IN INVENTORY |

Total installed packages: 215 (all enumerated in receipt file).

### 3.4 Production Import Path

Source code scan of `modules/`, `models/`, and `apps/` confirmed:
- **Zero** `import evidently` or `from evidently` in production code
- **Zero** `import nltk` or `from nltk` in production code
- `evidently_monitor.py` imports from `native_drift` engine only
- `native_drift.py` has zero imports of `evidently` or `nltk`

Verified by `test_no_evidently_imports_in_production_code`,
`test_native_drift_engine_is_used`, and
`test_native_drift_engine_does_not_depend_on_banned_packages`.

## 4. pip-audit Gate Verification

### 4.1 pip_audit_gate.py (PR #1188) Execution

**Command**: `uv run python3 delivery_toolchain/security/pip_audit_gate.py`

| Item | Value |
|---|---|
| Exit code | 0 |
| Receipt (stdout) | `receipts/pip_audit_gate_stdout.txt` |
| Receipt (stderr) | `receipts/pip_audit_gate_stderr.txt` |
| Receipt (exit code) | `receipts/pip_audit_gate_exit_code.txt` |
| stdout SHA256 | `9f364760f972e0b2bf612fabe8b5d9c39905adce14a24a91f470799f8b214927` |
| stderr SHA256 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (empty) |
| Audited scope | `.venv/lib/python3.12/site-packages` (215 dependencies) |
| Vulnerability service | PyPI (default) |

**stdout content** (raw, from file):
```
PASS: no Python package vulnerabilities found (215 dependencies audited).
```

### 4.2 Direct pip-audit Execution (JSON format)

**Command**: `uv run --with pip-audit pip-audit --local --format json`

| Item | Value |
|---|---|
| Exit code | 0 |
| Receipt (stdout) | `receipts/pip_audit_json_stdout.json` |
| Receipt (stderr) | `receipts/pip_audit_json_stderr.txt` |
| Receipt (exit code) | `receipts/pip_audit_json_exit_code.txt` |
| stdout SHA256 | `87b8b6a2ed1cf98ddb395969c074495e7ed2149897c07778f0052effe75cb8cf` |
| stderr SHA256 | `15950a68a7ed99c59779717acefdceb3f69cfd31bde67d2c954f2a3cea4d7955` |

**stdout content** (raw JSON, from file):
```json
{"dependencies": [], "fixes": []}
```

**stderr content**: `No known vulnerabilities found`

**Scope note**: The `--local` mode audits the ephemeral `uv run --with`
overlay. The gate's `--path` mode (§4.1) is the authoritative scope
covering all 215 installed packages. Both returned zero findings.

### 4.3 No-Suppression Confirmation

The `pip_audit_gate.py` source contains no `--ignore-vuln`, `--suppress`,
`--skip`, or waiver mechanism. The gate is fail-closed: every reported
vulnerability causes exit 1, transport failures cause exit 2. No suppression
rules exist for this task or any predecessor.

## 5. SBOM Verification (CycloneDX 1.5)

### 5.1 Task-Scoped SBOM

| Item | Value |
|---|---|
| Path | `docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/sbom.json` |
| SBOM Format | CycloneDX 1.5 |
| Components Cataloged | 775 |
| Content Digest | `sha256:938f53ec845d7e10be18eee7f2289516bec661ec29cc0b5ed2c4aeb00e03b70c` |
| Git SHA in SBOM | `c4bf87d81d55180d6d6769daf5358992b3bc6620` |
| Banned packages in SBOM | NONE (verified by `test_sbom_does_not_contain_banned_packages`) |

### 5.2 Root-level SBOM

This task does NOT modify `docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json`.
The root-level SBOM `test_sbom_and_provenance_present_and_valid` fails because
the base `dev` SBOM is stale (pre-existing condition from base SHA, not introduced
by this task). Root SBOM maintenance is out of scope for this task.

## 6. Four-Dimension Monitoring Verification — Detailed Receipts

Each dimension below records the actual command executed, exit code, test count,
and the receipt file containing the full pytest output.

### 6.1 Data Drift Monitoring

**Command**: `uv run pytest tests/models/test_evidently_monitor.py -v --tb=short --no-header`
**Exit code**: 0
**Tests**: 3 passed
**Receipt file**: `receipts/test_data_drift_receipt.txt`
**Receipt SHA256**: `f908244005fd06d9193e09be43366f05c050146652e4a3a974389acb7b045f4a`

Tests verify:
- Native engine (`engine = "native_drift"`) produces correct drift detection
- Per-column feature drift reporting with `drifted_columns` count and `drifted_column_names`
- Same `EvidentlyDriftResult` API contract as former Evidently wrapper
- `drift_share_threshold` evaluation: `drift_detected = (drift_share >= threshold)`

### 6.2 Feature Drift Monitoring

Feature-level drift detection is tested within the same test module
(`test_evidently_monitor.py`). The test `test_evidently_monitor_detects_shifted_features`
confirms per-column drift detection with `drifted_columns == 1` and correct
`drifted_column_names` output. See receipt §6.1.

### 6.3 Prediction Drift Monitoring

**Command**: `uv run pytest modules/learninghub/tests/test_prediction_drift.py -v --tb=short --no-header`
**Exit code**: 0
**Tests**: 6 passed
**Receipt file**: `receipts/test_prediction_drift_receipt.txt`
**Receipt SHA256**: `4da15baada891183573fe8067fac2f3195b5d8dfc1f3f2d17c1d2ec09014575d`

Tests cover:
- Cohort validation (reference/current cohort mismatch → ValueError)
- Policy threshold extraction from `DecisionPolicy.parameters`
- Model version metadata propagation
- Prediction column selection (output-only frame)
- Drift share computation with policy-governed threshold
- `drift_share_threshold` cannot weaken DecisionPolicy threshold

### 6.4 Performance Drift Monitoring

**Command**: `uv run pytest modules/learninghub/tests/test_performance_drift_and_baseline_comparison.py -v --tb=short --no-header`
**Exit code**: 0
**Tests**: 9 passed
**Receipt file**: `receipts/test_performance_drift_receipt.txt`
**Receipt SHA256**: `8022d174947fa564407279f8568c6f25e0b339ca7eb0818e0d4731c18bcc95c8`

Tests cover:
- Metric threshold evaluation (AUC, SMAPE, Precision, Recall, Coverage)
- Absolute thresholds (`min_value`, `max_value`)
- Relative degradation thresholds (`max_degradation`, `max_relative_degradation`)
- Segment metric thresholds (`SegmentMetricThreshold`)
- Guardrail breach detection and `GuardrailBreach` reporting
- Retraining request triggers (`RetrainingRequest`)
- Baseline comparison with `DatasetSnapshot` references

Performance monitoring is implemented entirely in first-party code:
- `models/shared_ml/validation.py` — `MetricThreshold`, `SegmentMetricThreshold`
- `modules/learninghub/application/monitor.py` — `evaluate_guardrails`, `ReleaseMonitorAssessment`
- `modules/learninghub/application/release.py` — `LearningHubService.evaluate_monitoring`
- `modules/learninghub/domain/monitoring.py` — `MonitoringEvaluation`, `MonitoringBreach`, `RetrainingRequest`

These modules do NOT import or depend on Evidently or NLTK.

### 6.5 Integration Tests

**Command**: `uv run pytest tests/integration/test_oss_ai_execution_flow.py -v --tb=short --no-header`
**Exit code**: 0
**Tests**: 4 passed
**Receipt file**: `receipts/test_integration_oss_receipt.txt`
**Receipt SHA256**: `edd91c218f80bc63e4b6a3a12005cc46ab5db6d2238f93c82b74a6a680f8b181`

### 6.6 Contract Tests

**Command**: `uv run pytest tests/contract/test_deferred_oss_adr.py -v --tb=short --no-header`
**Exit code**: 0
**Tests**: 6 passed
**Receipt file**: `receipts/test_contract_adr_receipt.txt`
**Receipt SHA256**: `58a7c8d003a3bb5c927f9e0e4257f0a2f8c386d06240ab34db71fa0fc8eae284`

### 6.7 NLTK Dependency Removal Tests

**Command**: `uv run pytest tests/security/test_nltk_dependency_removal.py -v --tb=short --no-header`
**Exit code**: 0
**Tests**: 9 passed
**Receipt file**: `receipts/test_nltk_removal_receipt.txt`
**Receipt SHA256**: `1a9de07142fbac56766daee606f017053351295693286ebfdfb962a9b5fd4687`

### 6.8 Supply Chain Security Gate Tests

**Command**: `uv run pytest tests/security/test_supply_chain_security_gate.py -v --tb=short --no-header`
**Exit code**: 1 (1 failure, 36 passed)
**Receipt file**: `receipts/test_supply_chain_gate_receipt.txt`
**Receipt SHA256**: `3c44e560518760f6cc5a458ca91a97131c04847a7501c9f18d696b9de5b06381`

**Failure**: `test_sbom_and_provenance_present_and_valid` — The root-level SBOM at
`docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json` is stale relative to
the current lockfiles. This is a pre-existing condition from the base SHA
`c4bf87d81d55180d6d6769daf5358992b3bc6620` — this task does not modify the
root SBOM and the stale condition is NOT introduced by this task's changes.
All 36 other supply chain security gate tests pass.

## 7. Upstream PR Chain Verification (GitHub API receipts)

All PR data below is sourced from `gh pr view --json` at 2026-09-08T01:14:53Z.

### 7.1 PR #1219 — ODP-DRIFT-NATIVE-MIGRATION-001 (Native Drift Engine)

| Item | Value |
|---|---|
| PR URL | https://github.com/alfloop-dev/odayplus/pull/1219 |
| Title | `[ReviewBus] ODP-DRIFT-NATIVE-MIGRATION-001 實作不依賴 Evidently/NLTK 的完整原生漂移統計核心` |
| State | MERGED |
| PR Head SHA | `dc047bb22db0a98b54e731a99f398dce613e0257` |
| Merge Commit OID | `c929a24759b15c16ecae21c46d86b7ca1e702776` |
| Merged At | 2026-09-06T04:53:06Z |
| CI Checks | All SUCCESS |
| Ancestor of HEAD | ✅ YES (`git merge-base --is-ancestor` confirmed) |

### 7.2 PR #1222 — ODP-DRIFT-DEP-REMOVE-002 (Dependency Removal)

| Item | Value |
|---|---|
| PR URL | https://github.com/alfloop-dev/odayplus/pull/1222 |
| Title | `[ReviewBus] ODP-DRIFT-DEP-REMOVE-002 原子切換原生監控並移除 Evidently/NLTK 生產依賴` |
| State | MERGED |
| PR Head SHA | `6d438486c8645e476fcf86b5f675a5ca20592b9d` |
| Merge Commit OID | `66244b30c2615dcad8373e03fff096e298d21e98` |
| Merged At | 2026-09-06T05:56:17Z |
| CI Checks | All SUCCESS |
| Ancestor of HEAD | ✅ YES (`git merge-base --is-ancestor` confirmed) |

### 7.3 PR #1188 — ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001 (pip-audit Gate)

| Item | Value |
|---|---|
| PR URL | https://github.com/alfloop-dev/odayplus/pull/1188 |
| Title | `[ReviewBus] ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001 為 dependency audit 設定 fail-closed timeout 邊界` |
| State | MERGED |
| PR Head SHA | `a429e83e470fb0f89000d55e48ab37da79cf48ff` |
| Merge Commit OID | `bd4fb5aa11404519ff1d8ae97fa796eb6d40e12a` |
| Merged At | 2026-09-06T10:38:11Z |
| CI Checks | All SUCCESS |
| Ancestor of HEAD | ✅ YES (`git merge-base --is-ancestor` confirmed) |

### 7.4 Merge Ancestry Verification

All three upstream PRs are confirmed ancestors of the current base SHA
`c4bf87d81d55180d6d6769daf5358992b3bc6620` via `git merge-base --is-ancestor`.

## 8. Governance Attestation

- ❌ No waiver, suppression, or ignore rule added
- ❌ No `--no-deps` used to omit production dependencies
- ❌ No vulnerability moved to an unscanned scope
- ❌ No security threshold lowered
- ❌ No monitoring functionality disabled
- ❌ No fixture/mocked scan used to claim real vulnerability fixed
- ✅ All verification performed on real installed packages
- ✅ All tests executed against exact integration SHA
- ✅ Task-scoped SBOM generated from current lock files
- ✅ Root-level `docs/evidence/sbom.json` left unchanged (out of task scope)
- ✅ Banned package detection uses `importlib.metadata.distribution()` (not ImportError)
- ✅ SBOM test fails closed on missing or empty components
- ✅ All raw receipts saved as files with SHA256 hashes

## 9. Test Changes in This PR

This PR (ODP-DRIFT-SECURITY-VERIFY-003) adds/modifies only:
- `tests/security/test_nltk_dependency_removal.py` — negative regression tests
- `docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/verification_evidence.md` — this document
- `docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/sbom.json` — task-scoped SBOM
- `docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/receipts/` — raw execution receipts
- `docs/audits/code-boundary-inventory.csv` — boundary inventory entry for new test file

It does NOT modify production code, dependencies, lock files, or out-of-scope
evidence files.

## 10. Production Input Hashes

| Input | SHA256 |
|---|---|
| `pyproject.toml` | `130f024b80d55f439aa5467f4f469bb127aec955ef71444d2951a93d0f5d0391` |
| `uv.lock` | `ba5c393e49538e4da9e59de001cffc28c4cadfab8aebacf6dbf1503eb3a49a5d` |

## 11. Receipt File Index

All files reside in `docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/receipts/`.

| Receipt File | SHA256 | Content |
|---|---|---|
| `installed_inventory.json` | `efcd05d48227bd8d8a701c9dcc3e45aeef8c6b88ac323b46a3e63dc48de89de6` | 215 installed packages with name+version |
| `pip_audit_gate_stdout.txt` | `9f364760f972e0b2bf612fabe8b5d9c39905adce14a24a91f470799f8b214927` | Gate stdout (PASS, 215 deps) |
| `pip_audit_gate_stderr.txt` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | Gate stderr (empty) |
| `pip_audit_gate_exit_code.txt` | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | Exit code: 0 |
| `pip_audit_json_stdout.json` | `87b8b6a2ed1cf98ddb395969c074495e7ed2149897c07778f0052effe75cb8cf` | JSON audit output |
| `pip_audit_json_stderr.txt` | `15950a68a7ed99c59779717acefdceb3f69cfd31bde67d2c954f2a3cea4d7955` | "No known vulnerabilities found" |
| `pip_audit_json_exit_code.txt` | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | Exit code: 0 |
| `tool_versions.json` | `663a9d77e698f95bd5b2951696200279fc4d0da5c07ff24d52d1db26b40e1963` | Python 3.12.14, pip-audit 2.10.1 |
| `test_data_drift_receipt.txt` | `f908244005fd06d9193e09be43366f05c050146652e4a3a974389acb7b045f4a` | 3 passed, exit 0 |
| `test_prediction_drift_receipt.txt` | `4da15baada891183573fe8067fac2f3195b5d8dfc1f3f2d17c1d2ec09014575d` | 6 passed, exit 0 |
| `test_performance_drift_receipt.txt` | `8022d174947fa564407279f8568c6f25e0b339ca7eb0818e0d4731c18bcc95c8` | 9 passed, exit 0 |
| `test_nltk_removal_receipt.txt` | `1a9de07142fbac56766daee606f017053351295693286ebfdfb962a9b5fd4687` | 9 passed, exit 0 |
| `test_integration_oss_receipt.txt` | `edd91c218f80bc63e4b6a3a12005cc46ab5db6d2238f93c82b74a6a680f8b181` | 4 passed, exit 0 |
| `test_contract_adr_receipt.txt` | `58a7c8d003a3bb5c927f9e0e4257f0a2f8c386d06240ab34db71fa0fc8eae284` | 6 passed, exit 0 |
| `test_supply_chain_gate_receipt.txt` | `3c44e560518760f6cc5a458ca91a97131c04847a7501c9f18d696b9de5b06381` | 36 passed, 1 failed (pre-existing stale SBOM), exit 1 |
