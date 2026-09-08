---
evidence_id: ODP-DRIFT-SECURITY-VERIFY-003
title: "NLTK Dependency Removal & Native Monitoring Security Evidence"
date: 2026-09-08
status: VERIFIED
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

This document records the final security verification evidence for the complete
removal of `evidently`, `nltk`, `defusedxml`, and `regex` from the production
dependency chain, and the successful operation of four-dimension native
monitoring, on exact integration SHA `c4bf87d81d55180d6d6769daf5358992b3bc6620`.

**Verdict**: All acceptance criteria are met. The production dependency chain
no longer includes any vulnerable or removed packages, the pip-audit gate
passes with zero findings, the SBOM is clean, and all four monitoring
dimensions function correctly with the native engine.

## 2. Exact Integration SHA & Verification Scope

| Item | Value |
|---|---|
| **Verification Base SHA** | `c4bf87d81d55180d6d6769daf5358992b3bc6620` |
| **Branch** | `task/ODP-DRIFT-SECURITY-VERIFY-003` (from `origin/dev`) |
| **Verification Timestamp** | 2026-09-08T01:03:41Z |
| **Python Version** | CPython 3.12.14 |
| **pip-audit Version** | 2.10.1 |
| **Advisory DB Service** | PyPI (default) |

## 3. Dependency Removal Verification

### 3.1 pyproject.toml (SHA256: `130f024b80d55f439aa5467f4f469bb127aec955ef71444d2951a93d0f5d0391`)

`evidently` is NOT declared in the `[project.dependencies]` or
`[project.optional-dependencies]` sections. Verified by direct text search.

### 3.2 uv.lock (SHA256: `ba5c393e49538e4da9e59de001cffc28c4cadfab8aebacf6dbf1503eb3a49a5d`)

| Banned Package | Status |
|---|---|
| `evidently` | ❌ NOT PRESENT |
| `nltk` | ❌ NOT PRESENT |
| `defusedxml` | ❌ NOT PRESENT |
| `regex` | ❌ NOT PRESENT |

Lock consistency check: `uv lock --check` passed (resolved 221 packages in 4ms).

### 3.3 Installed Packages

| Check | Result |
|---|---|
| Total installed packages | 215 |
| `evidently` importable? | NO (ImportError) |
| `nltk` importable? | NO (ImportError) |
| `defusedxml` importable? | NO (ImportError) |
| `regex` importable? | NO (ImportError) |

### 3.4 Production Import Path

Source code scan of `modules/`, `models/`, and `apps/` confirmed:
- **Zero** `import evidently` or `from evidently` in production code
- **Zero** `import nltk` or `from nltk` in production code
- `evidently_monitor.py` imports from `native_drift` engine only
- `native_drift.py` has zero imports of `evidently` or `nltk`

### 3.5 SBOM Verification (CycloneDX 1.5)

| Item | Value |
|---|---|
| SBOM Format | CycloneDX 1.5 |
| Components Cataloged | 775 |
| Content Digest | `sha256:938f53ec845d7e10be18eee7f2289516bec661ec29cc0b5ed2c4aeb00e03b70c` |
| Git SHA in SBOM | `c4bf87d81d55180d6d6769daf5358992b3bc6620` |
| Banned packages in SBOM | NONE |

## 4. pip-audit Gate Verification

### 4.1 Direct pip-audit Execution

```
$ uv run --with pip-audit pip-audit --local
No known vulnerabilities found
Exit code: 0
```

### 4.2 pip_audit_gate.py (PR #1188) Execution

```
$ uv run python3 delivery_toolchain/security/pip_audit_gate.py
PASS: no Python package vulnerabilities found (215 dependencies audited).
Exit code: 0
```

| Detail | Value |
|---|---|
| Packages audited | 215 |
| Packages skipped | 0 |
| Findings | 0 |
| Gate verdict | PASS |
| Socket timeout | 15.0s |
| Process timeout | 300.0s |
| Retry attempts | 3 |

## 5. Four-Dimension Monitoring Verification

All monitoring tests executed against exact SHA
`c4bf87d81d55180d6d6769daf5358992b3bc6620` using the native drift engine
(`engine = "native_drift"`):

### 5.1 Data Drift Monitoring

```
$ uv run pytest tests/models/test_evidently_monitor.py -v
tests/models/test_evidently_monitor.py::test_evidently_monitor_persists_real_report_payload PASSED
tests/models/test_evidently_monitor.py::test_evidently_monitor_detects_shifted_features PASSED
tests/models/test_evidently_monitor.py::test_native_monitoring_has_no_evidently_or_nltk_dependency PASSED
3 passed in 17.73s
```

### 5.2 Feature Drift Monitoring (via Data Drift)

Feature-level drift detection is verified within the data drift test suite.
`test_evidently_monitor_detects_shifted_features` confirms per-column drift
detection with `drifted_columns == 1` and correct `drifted_column_names`.

### 5.3 Prediction Drift Monitoring

```
$ uv run pytest modules/learninghub/tests/test_prediction_drift.py -v
modules/learninghub/tests/test_prediction_drift.py::... 6 passed in 57.72s
```

All 6 tests passed covering: cohort validation, policy threshold, model version,
prediction column selection, and drift share computation.

### 5.4 Performance Drift Monitoring

```
$ uv run pytest modules/learninghub/tests/test_performance_drift_and_baseline_comparison.py -v
modules/learninghub/tests/test_performance_drift_and_baseline_comparison.py::... 9 passed in 25.10s
```

All 9 tests passed covering: metric threshold evaluation, segment thresholds,
guardrail breaches, degradation rates, and retraining requests.

### 5.5 Integration Tests

```
$ uv run pytest tests/integration/test_oss_ai_execution_flow.py -v
tests/integration/test_oss_ai_execution_flow.py::... 4 passed in 86.73s
```

### 5.6 Contract Tests

```
$ uv run pytest tests/contract/test_deferred_oss_adr.py -v
tests/contract/test_deferred_oss_adr.py::... 6 passed in 0.28s
```

### 5.7 Negative Regression Tests (new)

```
$ uv run pytest tests/security/test_nltk_dependency_removal.py -v
tests/security/test_nltk_dependency_removal.py::test_banned_packages_not_importable PASSED
tests/security/test_nltk_dependency_removal.py::test_pyproject_does_not_declare_evidently PASSED
tests/security/test_nltk_dependency_removal.py::test_uv_lock_does_not_contain_banned_packages PASSED
tests/security/test_nltk_dependency_removal.py::test_no_evidently_imports_in_production_code PASSED
tests/security/test_nltk_dependency_removal.py::test_native_drift_engine_is_used PASSED
tests/security/test_nltk_dependency_removal.py::test_native_drift_engine_does_not_depend_on_banned_packages PASSED
tests/security/test_nltk_dependency_removal.py::test_sbom_does_not_contain_banned_packages PASSED
tests/security/test_nltk_dependency_removal.py::test_lock_consistency PASSED
8 passed in 0.33s
```

## 6. Upstream PR Chain Verification

### 6.1 PR #1219 — ODP-DRIFT-NATIVE-MIGRATION-001 (Native Drift Engine)

| Item | Value |
|---|---|
| PR URL | https://github.com/alfloop-dev/odayplus/pull/1219 |
| PR Head SHA | (resolved via merge queue) |
| Merge SHA | `c929a24759b15c16ecae21c46d86b7ca1e702776` |
| Merge Subject | `Merge pull request #1219 from alfloop-dev/task/ODP-DRIFT-NATIVE-MIGRATION-001` |
| Merged At | 2026-09-06T04:26:31Z |
| Ancestor of HEAD | ✅ YES |
| Content | Native scipy/statsmodels drift engine implementation |

### 6.2 PR #1222 — ODP-DRIFT-DEP-REMOVE-002 (Dependency Removal)

| Item | Value |
|---|---|
| PR URL | https://github.com/alfloop-dev/odayplus/pull/1222 |
| PR Head SHA | `6d438486c8645e476fcf86b5f675a5ca20592b9d` |
| Merge SHA | `66244b30c2615dcad8373e03fff096e298d21e98` |
| Merge Subject | `Merge pull request #1222 from alfloop-dev/task/ODP-DRIFT-DEP-REMOVE-002` |
| Merged At | 2026-09-06T05:56:17Z |
| Reviewer | Antigravity4 (approved 2026-09-06T05:31:36Z) |
| Ancestor of HEAD | ✅ YES |
| Content | Atomic removal of evidently/nltk from pyproject.toml and uv.lock |

### 6.3 PR #1188 — ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001 (pip-audit Gate)

| Item | Value |
|---|---|
| PR URL | https://github.com/alfloop-dev/odayplus/pull/1188 |
| PR Head SHA | `a429e83e470fb0f89000d55e48ab37da79cf48ff` |
| Merge SHA | `bd4fb5aa11404519ff1d8ae97fa796eb6d40e12a` |
| Merge Subject | `Merge pull request #1188 from alfloop-dev/task/ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001` |
| Merged At | 2026-09-06T10:12:34Z |
| Ancestor of HEAD | ✅ YES |
| Content | Fail-closed pip-audit gate with zero suppression/waivers |

### 6.4 Merge Ancestry Verification

All three upstream PRs are confirmed ancestors of the current exact integration
SHA `c4bf87d81d55180d6d6769daf5358992b3bc6620` via `git merge-base --is-ancestor`.

## 7. Governance Attestation

- ❌ No waiver, suppression, or ignore rule added
- ❌ No `--no-deps` used to omit production dependencies
- ❌ No vulnerability moved to an unscanned scope
- ❌ No security threshold lowered
- ❌ No monitoring functionality disabled
- ❌ No fixture/mocked scan used to claim real vulnerability fixed
- ✅ All verification performed on real installed packages
- ✅ All tests executed against exact integration SHA
- ✅ SBOM generated from current lock files

## 8. Test Execution Summary

| Test Suite | Tests | Status | Duration |
|---|---|---|---|
| Data/Feature drift monitoring | 3 | ✅ PASSED | 17.73s |
| Prediction drift monitoring | 6 | ✅ PASSED | 57.72s |
| Performance drift monitoring | 9 | ✅ PASSED | 25.10s |
| Integration (OSS AI flow) | 4 | ✅ PASSED | 86.73s |
| Contract (deferred ADR) | 6 | ✅ PASSED | 0.28s |
| Negative regression (new) | 8 | ✅ PASSED | 0.33s |
| **Total** | **36** | **✅ ALL PASSED** | **~188s** |
