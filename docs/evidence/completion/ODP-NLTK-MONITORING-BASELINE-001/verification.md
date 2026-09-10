# ODP-NLTK-MONITORING-BASELINE-001 — Focused verification

- Owner: Claude
- Reviewer: Antigravity4
- Branch: `task/ODP-NLTK-MONITORING-BASELINE-001`
- Base: `62dfc845925ed1b90bf06c0cbb490a0b82ed9b3a`
- Toolchain: `uv run --frozen --python 3.12` （必須釘 3.12：`uv.lock` 的
  `pgserver==0.1.4` 只有 `cp312` wheel，預設 CPython 3.14 連環境都建不起來）
- Environment: CPython 3.12.14, Linux-6.17.0-1022-gcp-x86_64-with-glibc2.39, x86_64
- Packages under test: `evidently 0.7.21`, `nltk 3.10.3`, `pandas 2.3.3`,
  `numpy 2.5.1`, `scipy 1.18.0`, `scikit-learn 1.9.0`

安全 gate 未被觸碰：本次驗證未執行、未修改、未繞過任何 scanner 或 supply-chain
gate，也未產生或覆寫任何 SBOM。

## 1. 新 baseline 套件

```text
$ uv run --frozen --python 3.12 pytest tests/models/test_evidently_monitor_baseline.py -p no:randomly
171 passed, 41 warnings in 333.66s (0:05:33)
exit 0
```

## 2. Focused verification：新套件 + task brief 指定的既有 selectors

```text
$ uv run --frozen --python 3.12 pytest \
    tests/models/test_evidently_monitor_baseline.py \
    tests/models/test_evidently_monitor.py \
    modules/learninghub/tests/test_prediction_drift.py \
    modules/learninghub/tests/test_performance_drift_and_baseline_comparison.py \
    -p no:randomly --durations=15
188 passed, 41 warnings in 397.38s (0:06:37)
exit 0
```

最慢的 15 項（節錄，說明耗時歸屬）：

```text
20.52s call  modules/learninghub/tests/test_prediction_drift.py::test_prediction_drift_service_persists_receipt_and_alert
20.39s call  tests/models/test_evidently_monitor_baseline.py::...[text_percentile_stable]
17.72s call  tests/models/test_evidently_monitor_baseline.py::test_a_plain_numeric_drift_run_loads_nltk_into_the_process
16.79s call  tests/models/test_evidently_monitor_baseline.py::...[text_percentile_drifted]
16.32s call  tests/models/test_evidently_monitor_baseline.py::...[numeric_ks_stable]
12.60s call  modules/learninghub/tests/test_prediction_drift.py::test_prediction_drift_shifted_output_alerts
10.58s call  modules/learninghub/tests/test_prediction_drift.py::test_prediction_drift_same_distribution_is_healthy
 9.88s call  tests/models/test_evidently_monitor.py::test_evidently_monitor_detects_shifted_features
 8.52s call  tests/models/test_evidently_monitor.py::test_evidently_monitor_persists_real_report_payload
```

`numeric_ks_stable` 是 200 列單一 float 欄位的 K-S，純計算只需 0.2 秒量級，卻要
16.32s；既有的 `test_evidently_monitor_persists_real_report_payload` 也要 8.52s。
成因是每次呼叫都會跑一次隔離能力探測子行程，量測與說明見
`baseline.md` §8.1。**這是既有行為，本 task 未修改，也未把它算成新套件的成本。**

## 3. 基準可重現性（逐 byte）

```text
$ uv run --frozen --python 3.12 python tests/models/fixtures/evidently_0_7_21/generate_baseline.py --check
baseline is reproducible: 78 file(s) match (manifest provenance keys
['generated_at', 'source_branch', 'source_sha', 'source_worktree_clean'] are excluded by design)
exit 0
```

78 個檔案（44 個 golden + 33 個 raw evaluation + manifest）在重新執行後逐 byte 相同。
被排除的四個 key 是 provenance，每次執行必然改變，其成因已在輸出中具名列出。

## 4. Lint 與治理閘

```text
$ uv run --frozen --python 3.12 ruff check tests/models/test_evidently_monitor_baseline.py tests/models/fixtures/evidently_0_7_21/
All checks passed!

$ uv run --frozen --python 3.12 python delivery_toolchain/governance/check_code_boundaries.py
Code boundary checks passed for 1129 files.
exit 0

$ uv run --frozen --python 3.12 python delivery_toolchain/governance/check_requirement_members.py
Requirement member checks passed: 9 set-valued requirements, 47 members
(36 satisfied, 11 absent and noted).
exit 0
```

`docs/audits/code-boundary-inventory.csv` 新增 3 列，全部是本 task 新增的 `.py`，
分類為 `verification`；已確認無重複列（`cut -d, -f1 | sort | uniq -d` 為空）。

## 5. 未執行的項目（明列，不當作已驗）

- **未跑全套 product suite**。本次只跑 task brief 指定的 focused selectors 加上新套件。
- **未執行任何安全 scanner**（`pip_audit_gate.py`、SBOM 產生、簽章驗證），依 task
  邊界刻意不執行。因此本文件不含任何漏洞狀態結論。
- **未執行 NLTK 受影響 API 的 call-level trace**。第 8 節只量到模組匯入可達性。
- **未執行 evidently 的 text descriptor / guardrail 路徑**，因為它們會呼叫
  `nltk.download(...)`；task 邊界禁止下載 NLP 模型。這是保留的具體阻礙，不是降級。
