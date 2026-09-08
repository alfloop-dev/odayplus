---
evidence_id: ODP-DRIFT-SECURITY-VERIFY-003
title: "NLTK Dependency Removal & Native Monitoring Security Evidence"
date: 2026-09-08
status: PENDING_INDEPENDENT_REVIEW
owner: Claude2
previous_owner: Antigravity4
reviewer: Codex
review_round: 3
repository: alfloop-dev/odayplus
task: ODP-DRIFT-SECURITY-VERIFY-003
verified_ref: c4bf87d81d55180d6d6769daf5358992b3bc6620
base_ref: c4bf87d81d55180d6d6769daf5358992b3bc6620
upstream_pr_chain:
  - PR_1218: ODP-NLTK-MONITORING-BASELINE-001
  - PR_1217: ODP-SBOM-CANDIDATE-OUTPUT-ISOLATION-001
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
in §14. No receipt is hand-transcribed — every entry is a file saved from
actual stdout/stderr redirection at execution time.

### 1.1 SHA Relationship

- **Base SHA** (`origin/dev` tip at branch creation): `c4bf87d81d55180d6d6769daf5358992b3bc6620`
- **Task branch HEAD**: see PR head at submission time
- The base SHA already contains all upstream PR merges (#1217, #1218, #1219, #1222, #1188).
  This task adds only verification tests, evidence documents, and raw receipts;
  it does not change production code, dependencies, or lock files.

### 1.2 What this round adds

Ownership moved to Claude2 after the second reviewer reopen. That reopen
(2026-09-08T01:35:38Z) named two gaps and one process failure:

| Reviewer finding | Answered in |
|---|---|
| production-entry evidence missing | §11 — every production monitoring entry executed and recorded, four dimensions covered |
| baseline-equivalence evidence missing | §12 — all 44 recorded Evidently goldens replayed against the native path at exact equality, twice |
| remote head not atomically updated by `task_finalize` | this round is submitted through `delivery_toolchain/git/task_finalize.sh`, so the PR head and the recorded review submission move together |

Nothing from the previous rounds was removed. All 15 raw receipts from round 2
are byte-identical, the supply-chain root SBOM failure in §5.2 and §6.8 is
preserved unedited, and the document status stays
`PENDING_INDEPENDENT_REVIEW`. No waiver, suppression or ignore rule was added,
and the new evidence is measurement rather than a rerun of the same unit tests:
§11 records which production code paths executed, §12 replays an immutable
recorded baseline, and §7 reads merge proof from the GitHub API.

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

## 7. Remediation Chain — GitHub MERGED Proof

The acceptance asks for the native core, the baseline, the SBOM output repair,
the atomic cutover and PR #1188 to be recorded one by one with the real PR URL,
the approved exact head, the required CI and the merge SHA. Neither a task
reaching `done`, nor a document, nor a historical PASS is used in place of the
GitHub proof below.

All rows are read from raw API output saved under `receipts/github/`:
`pr_<n>.json` is unmodified `gh pr view --json` output, and
`statuses_<n>_<head>.json` is unmodified `GET /repos/alfloop-dev/odayplus/commits/<head>/statuses`.
Captured 2026-09-08T01:45Z.

### 7.1 PR #1218 — ODP-NLTK-MONITORING-BASELINE-001 (monitoring baseline)

| Item | Value |
|---|---|
| PR URL | https://github.com/alfloop-dev/odayplus/pull/1218 |
| Title | `[ReviewBus] ODP-NLTK-MONITORING-BASELINE-001 建立 NLTK 漏洞修復的完整監控實測基準` |
| State | **MERGED** |
| Approved exact head | `3ef557769d670c73ccc0c36b7ba5cc90b6635200` |
| Approval receipt | `task-review-gate` = `success`, "Approved by assigned reviewer Antigravity4", 2026-09-06T03:49:55Z |
| Required CI at that head | 7 checks, all `SUCCESS`: `boundary`, `change-scope`, `classify`, `orchestrator`, `performance-gate`, `product`, `product-e2e-gate` |
| Merge commit | `d0bbb76392e9d8a366354876d1f9302146eb1483` |
| Merged at / by | 2026-09-06T04:16:42Z / `ajoe734` |
| Ancestor of this task's base | ✅ `git merge-base --is-ancestor` |

Delivered the Evidently 0.7.21 golden set replayed in §12.

### 7.2 PR #1217 — ODP-SBOM-CANDIDATE-OUTPUT-ISOLATION-001 (SBOM output repair)

| Item | Value |
|---|---|
| PR URL | https://github.com/alfloop-dev/odayplus/pull/1217 |
| Title | `[ReviewBus] ODP-SBOM-CANDIDATE-OUTPUT-ISOLATION-001 修正既有 SBOM CLI 的歷史證據覆寫副作用` |
| State | **MERGED** |
| Approved exact head | `05164662abb41f75fdb6cf40ae25ea9df40371a1` |
| Approval receipt | `task-review-gate` = `success`, "Approved by assigned reviewer Claude2", 2026-09-06T03:10:25Z |
| Required CI at that head | 7 checks, all `SUCCESS` (same set as §7.1) |
| Merge commit | `14821fd8c2f716dcf01f358f9d7408a9c1b9c528` |
| Merged at / by | 2026-09-06T03:35:16Z / `ajoe734` |
| Ancestor of this task's base | ✅ `git merge-base --is-ancestor` |

### 7.3 PR #1219 — ODP-DRIFT-NATIVE-MIGRATION-001 (native drift core)

| Item | Value |
|---|---|
| PR URL | https://github.com/alfloop-dev/odayplus/pull/1219 |
| Title | `[ReviewBus] ODP-DRIFT-NATIVE-MIGRATION-001 實作不依賴 Evidently／NLTK 的完整原生漂移統計核心` |
| State | **MERGED** |
| Approved exact head | `dc047bb22db0a98b54e731a99f398dce613e0257` |
| Approval receipt | `task-review-gate` = `success`, "Approved by assigned reviewer Antigravity4", 2026-09-06T04:25:49Z |
| Required CI at that head | 7 checks, all `SUCCESS` (same set as §7.1) |
| Merge commit | `c929a24759b15c16ecae21c46d86b7ca1e702776` |
| Merged at / by | 2026-09-06T04:53:06Z / `ajoe734` |
| Ancestor of this task's base | ✅ `git merge-base --is-ancestor` |

The status history at this head also records an earlier `failure`
("Review rejected or reopened") before the approval; the raw receipt is kept
unedited so the reopen is visible rather than smoothed away.

### 7.4 PR #1222 — ODP-DRIFT-DEP-REMOVE-002 (atomic cutover)

| Item | Value |
|---|---|
| PR URL | https://github.com/alfloop-dev/odayplus/pull/1222 |
| Title | `[ReviewBus] ODP-DRIFT-DEP-REMOVE-002 原子切換原生監控並移除 Evidently／NLTK 生產依賴` |
| State | **MERGED** |
| Approved exact head | `6d438486c8645e476fcf86b5f675a5ca20592b9d` |
| Approval receipt | `task-review-gate` = `success`, "Approved by assigned reviewer Antigravity4", 2026-09-06T05:31:36Z |
| Required CI at that head | 7 checks, all `SUCCESS` (same set as §7.1) |
| Merge commit | `66244b30c2615dcad8373e03fff096e298d21e98` |
| Merged at / by | 2026-09-06T05:56:17Z / `ajoe734` |
| Ancestor of this task's base | ✅ `git merge-base --is-ancestor` |

### 7.5 PR #1188 — ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001 (pip-audit gate)

| Item | Value |
|---|---|
| PR URL | https://github.com/alfloop-dev/odayplus/pull/1188 |
| Title | `[ReviewBus] ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001 為 dependency audit 設定 fail-closed timeout 邊界` |
| State | **MERGED** |
| Approved exact head | `a429e83e470fb0f89000d55e48ab37da79cf48ff` |
| Approval receipt | `task-review-gate` = `success`, "Approved by assigned reviewer Codex2", 2026-09-06T10:12:25Z |
| Required CI at that head | 7 checks, all `SUCCESS` (same set as §7.1) |
| Merge commit | `bd4fb5aa11404519ff1d8ae97fa796eb6d40e12a` |
| Merged at / by | 2026-09-06T10:38:11Z / `ajoe734` |
| Ancestor of this task's base | ✅ `git merge-base --is-ancestor` |

This is the sole `pip_audit_gate` executed in §4.1.

### 7.6 Merge ancestry

All five merge commits are ancestors of this task's base SHA
`c4bf87d81d55180d6d6769daf5358992b3bc6620`, confirmed with
`git merge-base --is-ancestor`.

### 7.7 This evidence PR is not yet merged

This document's own PR (#1241) is **OPEN**, not approved and not merged. The
remediation chain is therefore **not** claimed as verified-complete: that claim
becomes available only after independent review approves this PR and required CI
merges it. Nothing here deploys anything or grants a deployment GO.

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
- ✅ Production-entry coverage is measured, not asserted: §11 counts recorded
  calls into the real entry points and reports an unreached entry as `calls: 0`
- ✅ The Evidently reference stack was not installed into the audited candidate
  scope; §12.2 records the exclusion and a regression test now enforces it
- ✅ Baseline goldens were replayed, not regenerated, and their recorded hashes
  were re-verified in the same run
- ✅ Merge proof in §7 comes from unmodified GitHub API output, including the
  earlier `failure` status left visible at PR #1219's head
- ❌ No receipt from a previous round was deleted or rewritten; the round-3
  regression run is filed under its own `*_round3_*` names

## 9. Test Changes in This PR

This PR (ODP-DRIFT-SECURITY-VERIFY-003) adds/modifies only:
- `tests/security/test_nltk_dependency_removal.py` — negative regression tests
- `docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/verification_evidence.md` — this document
- `docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/sbom.json` — task-scoped SBOM
- `docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/receipts/` — raw execution receipts
- `docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/tools/production_entry_probe.py` — the evidence generator for §11
- `docs/audits/code-boundary-inventory.csv` — boundary inventory entry for the new files

Round 3 added three negative regressions to the owned test file, all passing
(§14, `test_nltk_removal_round3_receipt.txt`, 12 passed):

| Test | What its failure would mean |
|---|---|
| `test_production_monitoring_entry_points_survive_the_removal` | a production monitoring entry, or one of its cohort/threshold/policy arguments, was removed instead of migrated |
| `test_prediction_and_data_drift_entries_route_to_the_native_engine` | the retained public API stopped dispatching to the first-party engine, or a metric fingerprint was re-attributed to Evidently |
| `test_baseline_reference_stack_stays_out_of_the_audited_scope` | the pinned Evidently reference stack was installed into the scope `pip_audit_gate.py` audits |

It does NOT modify production code, dependencies, lock files, or out-of-scope
evidence files. The `tools/` script is evidence generation only: it is not
imported by production code and installs nothing.

## 10. Production Input Hashes

| Input | SHA256 |
|---|---|
| `pyproject.toml` | `130f024b80d55f439aa5467f4f469bb127aec955ef71444d2951a93d0f5d0391` |
| `uv.lock` | `ba5c393e49538e4da9e59de001cffc28c4cadfab8aebacf6dbf1503eb3a49a5d` |

## 11. Production-Entry Execution Evidence (four dimensions)

Reviewer finding (reopen #2, 2026-09-08T01:35:38Z): production-entry evidence
was missing. A test module name does not establish which code path ran, so this
round measures execution at the entry points instead of asserting it.

### 11.1 Method

`tools/production_entry_probe.py` (committed alongside this document) wraps every
production monitoring entry point with a pass-through recorder, runs a selected
set of node ids in-process, and writes down which entries were actually reached,
with what governed arguments, and with what outcome. The recorders change no
behaviour: arguments and return values pass through untouched and exceptions are
re-raised after being noted. An entry that is never called is reported with
`calls: 0`, so the receipt cannot claim coverage the run did not produce.

The probe installs nothing. It imports only the standard library plus the
already-installed `pytest`, so the audited candidate scope of §3.3 and §4.1 is
the same scope that produced this receipt.

| Item | Value |
|---|---|
| Command | `MLFLOW_DISABLE_AGENT_HINT=1 uv run --frozen --python 3.12 python docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/tools/production_entry_probe.py --out receipts/production_entry_matrix.json --log receipts/production_entry_pytest_log.txt` |
| Receipt (structured) | `receipts/production_entry_matrix.json` |
| Receipt (pytest output) | `receipts/production_entry_pytest_log.txt` |
| Receipt (exit code) | `receipts/production_entry_exit_code.txt` |
| Receipt (probe stderr) | `receipts/production_entry_probe_stderr.txt` (empty, 0 bytes) |
| pytest result | 45 passed, 5 warnings in 66.34s |
| pytest exit code | 0 |
| `all_dimensions_covered` | `true` |
| Run SHA | `5cd7b74dd44e3991cc9cd3a5b4441a25a02df1fe` |

The receipt pins itself to content, not only to a commit: it records the SHA-256
of all 14 production and test input files whose content decides the outcome
(`input_file_sha256`), so a later evidence-only commit cannot silently move what
the measurement refers to. It also records `worktree_dirty_paths` at run time,
which contains only this task's own evidence paths and its owned test file.

### 11.2 Production entries reached

| Production symbol | Calls | Outcomes | Governed arguments observed at the entry |
|---|---|---|---|
| `EvidentlyDriftMonitor.run` | 3 | 3 returned | `drift_share_threshold` |
| `EvidentlyDriftMonitor.run_prediction` | 2 | 1 returned, 1 raised `ValueError` | `cohort_key`, `model_version`, `output_types`, `policy`, `prediction_columns` |
| `LearningHubService.monitor_prediction_drift` | 2 | 1 returned, 1 raised `LearningHubError` | `cohort_key`, `model_version`, `output_types`, `policy`, `prediction_columns` |
| `LearningHubService.evaluate_monitoring` | 4 | 4 returned | `policy`, `signal_type`, `thresholds` |
| `LearningHubService.ingest_outcome_monitoring` | 1 | 1 returned | `thresholds` |
| `LearningHubService.monitor_release` | 3 | 2 returned, 1 raised `LearningHubError` | `guardrails` |

Every entry was reached. No entry is listed here on the strength of a name; each
row is a count of recorded calls from `production_entry_matrix.json.entries`.

### 11.3 Dimension coverage

| Dimension | Production entries executed | Refusal outcomes observed |
|---|---|---|
| Data drift | `EvidentlyDriftMonitor.run` | — |
| Feature drift | `EvidentlyDriftMonitor.run` (per-column verdict) | — |
| Prediction drift | `LearningHubService.monitor_prediction_drift`, `EvidentlyDriftMonitor.run_prediction` | `LearningHubError`, `ValueError` |
| Performance drift | `LearningHubService.evaluate_monitoring`, `LearningHubService.ingest_outcome_monitoring`, `LearningHubService.monitor_release` | `LearningHubError` |

The acceptance asks that the error, threshold and cohort checks survive:

- **Cohort** — `cohort_key` is recorded at both prediction-drift entries, and
  `test_prediction_drift_rejects_mixed_cohort_and_version_rows` produced the
  recorded `ValueError` refusal.
- **Threshold** — `thresholds` at `evaluate_monitoring`, `guardrails` at
  `monitor_release`, `drift_share_threshold` at `run`, and `policy` at the
  prediction entries, all observed as passed arguments rather than defaults.
- **Error** — refusals were recorded at three of the six entries, including
  `test_release_monitor_rejects_unknown_release` and
  `test_prediction_drift_service_rejects_non_production_model_version`. A green
  run here therefore covers the refusal paths, not only the happy paths.

### 11.4 Selected node ids

The selection spans the in-process service entry, the durable-database lifecycle
path and the HTTP route, so the evidence is not limited to unit-level calls:

- Data drift: `tests/models/test_evidently_monitor.py::test_evidently_monitor_persists_real_report_payload`, `tests/integration/test_oss_ai_execution_flow.py::test_governed_training_registry_and_monitoring_flow_uses_real_oss`
- Feature drift: `tests/models/test_evidently_monitor.py::test_evidently_monitor_detects_shifted_features`, `tests/models/test_evidently_monitor_baseline.py::test_first_party_column_list_agrees_with_the_engine_drift_count`
- Prediction drift: `modules/learninghub/tests/test_prediction_drift.py::{test_prediction_drift_service_persists_receipt_and_alert, test_prediction_drift_service_rejects_non_production_model_version, test_prediction_drift_rejects_mixed_cohort_and_version_rows}`
- Performance drift: `modules/learninghub/tests/test_performance_drift_and_baseline_comparison.py::{test_evaluate_monitoring_triggers_retraining_on_performance_drift, test_decision_policy_governs_performance_drift_thresholds}`, `tests/integration/test_learninghub_release.py::{test_release_monitor_api_forwards_explicit_baseline_metrics, test_release_monitor_breach_recommends_rollback_and_leaves_alias_unchanged, test_release_monitor_rejects_unknown_release}`, `tests/integration/test_production_model_lifecycle.py::test_monitoring_comparison_restart_safety_and_governed_rollback`

`test_release_monitor_api_forwards_explicit_baseline_metrics` drives
`POST /learninghub/releases/{release_id}/monitor` through the FastAPI route, and
`test_monitoring_comparison_restart_safety_and_governed_rollback` runs against a
durable database and a process restart.

## 12. Baseline Equivalence Evidence

Reviewer finding (reopen #2): baseline-equivalence evidence was missing. The
repository already carries an immutable Evidently 0.7.21 golden set from
`ODP-NLTK-MONITORING-BASELINE-001` (PR #1218, §7.1); no prior round in this task
had executed it. It is executed here.

`tests/models/test_evidently_monitor_baseline.py` replays every recorded golden
against the production native path and compares statistics, p-values, distances,
method names, thresholds, verdicts, columns and governed metadata with
`FLOAT_REL_TOL = FLOAT_ABS_TOL = 0.0` — exact equality, no relaxed tolerance.
Only the engine name, the metric type prefix and an auto-generated
`snapshot_id` are expected to differ, and each of those is an explicit, asserted
substitution rather than a skipped field.

| Item | Run 1 | Run 2 (machine-readable) |
|---|---|---|
| Command | `uv run --frozen --python 3.12 pytest tests/models/test_evidently_monitor_baseline.py -v --tb=short --no-header` | same plus `-rA --junitxml=receipts/baseline_equivalence_junit.xml` |
| Tests | 171 passed | 171 passed |
| Failures / errors / skipped | 0 / 0 / 0 | 0 / 0 / 0 |
| Exit code | 0 | 0 |
| Wall clock | 153.09s | 189.15s |
| Receipt | `receipts/test_baseline_equivalence_receipt.txt` | `receipts/baseline_equivalence_junit.xml`, `receipts/baseline_equivalence_junit_stdout.txt` |
| Exit-code receipt | `receipts/test_baseline_equivalence_exit_code.txt` | `receipts/baseline_equivalence_junit_exit_code.txt` |

### 12.1 What the 171 tests are

| Test | Count | What it replays |
|---|---|---|
| `test_recorded_inputs_still_describe_the_live_fixtures` | 44 | every recorded case's input hashes, dtypes, cleaned `N` and merged-unique counts |
| `test_case_reproduces_the_recorded_baseline` | 33 | the completed cases: full `EvidentlyDriftResult`, `to_dict()` payload and per-column method/threshold/statistic index |
| `test_recorded_stat_test_matches_the_routing_table` | 33 | the stat-test the engine chose per case against the recorded routing rule |
| `test_first_party_column_list_agrees_with_the_engine_drift_count` | 33 | the wrapper's drifted-column list against the engine's own count |
| `test_failure_case_reproduces_the_recorded_failure` | 11 | the refusal cases: exception type and message |
| single-instance policy and guard tests | 17 | manifest pinning, hash re-verification, normalization narrowness, threshold resolution, text stat tests, no-NLTK-in-process |

The manifest declares `case_count: 44`, and
`test_manifest_covers_exactly_the_declared_cases` asserts the replayed set is
exactly that set — 33 completed plus 11 refusals — so no case can be quietly
dropped from the comparison.

The suite carries its own anti-fabrication checks, all of which passed in the run
above:

- `test_fixture_files_match_the_hashes_recorded_in_the_manifest` — every golden
  and raw report still hashes to what `manifest.json` recorded, so the goldens
  were not edited to match the native engine.
- `test_recorded_inputs_still_describe_the_live_fixtures` — the recorded input
  row hashes, dtypes, cleaned `N` and merged-unique counts still describe the
  inputs the test replays.
- `test_normalization_policy_is_recorded_and_narrow` — the normalization policy
  cannot be widened to smooth a divergence away.
- `test_manifest_pins_reference_and_candidate_numerical_stack` — the manifest
  pins `evidently == 0.7.21` as the reference, and asserts that neither
  `evidently` nor `nltk` is installed in the candidate environment.
- `test_production_drift_run_does_not_load_nltk_into_the_process` — a production
  drift run loads no `nltk` module.

### 12.2 Reference regeneration is an isolated tool

`tests/models/fixtures/evidently_0_7_21/generate_baseline.py` is the only way to
regenerate the reference, and it was **not** run for this evidence. Doing so
would require installing `evidently 0.7.21`, which still carries the unpatched
`nltk`. The candidate environment audited in §3.3 and §4.1 therefore stays free
of the reference stack, and `installed_scope_excludes` in
`receipts/production_entry_matrix.json` records `evidently: true, nltk: true`
measured in the same process that ran the production entries.

This is now also a negative regression rather than only a claim:
`test_baseline_reference_stack_stays_out_of_the_audited_scope` in
`tests/security/test_nltk_dependency_removal.py` fails if the reference stack is
ever installed into the audited candidate scope — the one way a green audit and
a green equivalence run could stop being compatible claims.

## 13. Native Core Evidence

| Item | Value |
|---|---|
| Command | `uv run --frozen --python 3.12 pytest tests/models/test_native_drift.py --tb=short --no-header --junitxml=receipts/native_core_junit.xml` |
| Tests | 129 passed |
| Failures / errors / skipped | 0 / 0 / 0 |
| Exit code | 0 |
| Wall clock | 38.87s |
| Receipt | `receipts/test_native_core_receipt.txt`, `receipts/native_core_junit.xml` |
| Exit-code receipt | `receipts/test_native_core_exit_code.txt` |

This is the first-party statistical core introduced by PR #1219 (§7.3), the code
that replaced the Evidently computation the goldens in §12 were recorded from.

## 14. Receipt File Index

All files reside in `docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/receipts/`.
Hashes below were computed from the files as committed (40 receipts).
The 15 round-2 receipts are unchanged; round-3 additions are listed alongside them.

| Receipt File | SHA256 | Content |
|---|---|---|
| `baseline_equivalence_junit.xml` | `7a52f4c1eccda489e5d9136ca358f3c9b066ba74ea4723c72e8935461659ed16` | §12 run 2 — JUnit: 171 tests, 0 failures, 0 errors, 0 skipped |
| `baseline_equivalence_junit_exit_code.txt` | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | §12 run 2 exit code: 0 |
| `baseline_equivalence_junit_stdout.txt` | `26a289c4114172c5de2199984f9b15f3080abba445e59f795fcfc93d5f6efbda` | §12 run 2 stdout (-rA per-test PASSED lines) |
| `github/pr_1188.json` | `ef7215a2b4b98da163107cb943790fed62247990bc0afe2035073f60af886fa7` | §7.5 raw `gh pr view --json` |
| `github/pr_1217.json` | `a08c2cfd37a700e5e6b636332a306cfc93826c77fd829f2cfeed523bd0a94da8` | §7.2 raw `gh pr view --json` |
| `github/pr_1218.json` | `31d06d37a866cd0678d43cb947fe809cdf37dfc99584a853b436ecec89a1f762` | §7.1 raw `gh pr view --json` |
| `github/pr_1219.json` | `0da4c38cd4cb3e814b341a833f46f2ded5f3693bc2063f700b162348097a8d4b` | §7.3 raw `gh pr view --json` |
| `github/pr_1222.json` | `fc5fce3e9fd310b861233610bf67b728c72d50c47f0716d874616ccf649de238` | §7.4 raw `gh pr view --json` |
| `github/statuses_1188_a429e83e470fb0f89000d55e48ab37da79cf48ff.json` | `26bf42fcdff3914def861917a9ef541250e11d4a5a06cb30453f2b54dfccf838` | §7 raw commit statuses at PR #1188's approved head |
| `github/statuses_1217_05164662abb41f75fdb6cf40ae25ea9df40371a1.json` | `e6f6c2fb665acb85a101f53cab121c0f5305203a257f4eaa8527c136e84130b8` | §7 raw commit statuses at PR #1217's approved head |
| `github/statuses_1218_3ef557769d670c73ccc0c36b7ba5cc90b6635200.json` | `3fd89ec97ddda15bf6c8042a6d7ca32d2c1cb395baf6d071e919826924dd5ca9` | §7 raw commit statuses at PR #1218's approved head |
| `github/statuses_1219_dc047bb22db0a98b54e731a99f398dce613e0257.json` | `52adf4bfc329d925db2f02c3eaf14ee78b8ddff0df691c2bc7e5cc87dd525cfa` | §7 raw commit statuses at PR #1219's approved head |
| `github/statuses_1222_6d438486c8645e476fcf86b5f675a5ca20592b9d.json` | `358961b6d90230a4d97f813593cc0c87f9b687d6883517b9aac32b1b46a6636d` | §7 raw commit statuses at PR #1222's approved head |
| `installed_inventory.json` | `efcd05d48227bd8d8a701c9dcc3e45aeef8c6b88ac323b46a3e63dc48de89de6` | 215 installed packages with name+version |
| `native_core_junit.xml` | `b9e0ee3b5debfb98d4f2c97cef02103c7cd02ac169f2d771ffa30caa28efc89a` | §13 JUnit: 129 tests, 0 failures, 0 errors, 0 skipped |
| `nltk_removal_round3_junit.xml` | `d8897b666062ab523302ec5990a81b2bf3457b429cace0266f91ffb922285972` | §9 JUnit: 12 tests, 0 failures |
| `pip_audit_gate_exit_code.txt` | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | Exit code: 0 |
| `pip_audit_gate_stderr.txt` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | Gate stderr (empty) |
| `pip_audit_gate_stdout.txt` | `9f364760f972e0b2bf612fabe8b5d9c39905adce14a24a91f470799f8b214927` | Gate stdout (PASS, 215 deps) |
| `pip_audit_json_exit_code.txt` | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | Exit code: 0 |
| `pip_audit_json_stderr.txt` | `15950a68a7ed99c59779717acefdceb3f69cfd31bde67d2c954f2a3cea4d7955` | "No known vulnerabilities found" |
| `pip_audit_json_stdout.json` | `87b8b6a2ed1cf98ddb395969c074495e7ed2149897c07778f0052effe75cb8cf` | JSON audit output |
| `production_entry_exit_code.txt` | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | §11 exit code: 0 |
| `production_entry_matrix.json` | `c3cbe4eaf4a4ac8d63005c80858c1b5e48eaddf708b02595a853f862fd1b3852` | §11 — recorded calls per production entry, dimension coverage, input hashes |
| `production_entry_probe_stderr.txt` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | §11 probe stderr (empty) |
| `production_entry_pytest_log.txt` | `c4ae5b63f5b7bba3314af2527c527fe5c97922e4adbec37b2a408b27f7a09062` | §11 pytest output — 45 passed |
| `test_baseline_equivalence_exit_code.txt` | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | §12 run 1 exit code: 0 |
| `test_baseline_equivalence_receipt.txt` | `ae77af72555cf5cb1397d26307bd427857ea05ec552532e6b720b78ca9e6ac2b` | §12 run 1 — 171 passed, exit 0 |
| `test_contract_adr_receipt.txt` | `58a7c8d003a3bb5c927f9e0e4257f0a2f8c386d06240ab34db71fa0fc8eae284` | 6 passed, exit 0 |
| `test_data_drift_receipt.txt` | `f908244005fd06d9193e09be43366f05c050146652e4a3a974389acb7b045f4a` | 3 passed, exit 0 |
| `test_integration_oss_receipt.txt` | `edd91c218f80bc63e4b6a3a12005cc46ab5db6d2238f93c82b74a6a680f8b181` | 4 passed, exit 0 |
| `test_native_core_exit_code.txt` | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | §13 exit code: 0 |
| `test_native_core_receipt.txt` | `47ab4c99a4f512550dfb875d325d8b10d60ed98dd09ace469f19a53724e78d95` | §13 — 129 passed, exit 0 |
| `test_nltk_removal_receipt.txt` | `1a9de07142fbac56766daee606f017053351295693286ebfdfb962a9b5fd4687` | 9 passed, exit 0 (round 2, preserved unedited) |
| `test_nltk_removal_round3_exit_code.txt` | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | §9 exit code: 0 |
| `test_nltk_removal_round3_receipt.txt` | `6c9db123f46408a10ff73ca816b7cf10fa4926c5bab487fe63fb0fdc1d237787` | §9 — 12 passed, exit 0 (9 round-2 tests + 3 new regressions) |
| `test_performance_drift_receipt.txt` | `8022d174947fa564407279f8568c6f25e0b339ca7eb0818e0d4731c18bcc95c8` | 9 passed, exit 0 |
| `test_prediction_drift_receipt.txt` | `4da15baada891183573fe8067fac2f3195b5d8dfc1f3f2d17c1d2ec09014575d` | 6 passed, exit 0 |
| `test_supply_chain_gate_receipt.txt` | `3c44e560518760f6cc5a458ca91a97131c04847a7501c9f18d696b9de5b06381` | 36 passed, 1 failed (pre-existing stale root SBOM), exit 1 |
| `tool_versions.json` | `663a9d77e698f95bd5b2951696200279fc4d0da5c07ff24d52d1db26b40e1963` | Python 3.12.14, pip-audit 2.10.1 |
