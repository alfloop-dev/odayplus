---
evidence_id: ODP-NLTK-MONITORING-BASELINE-001
title: "Pinned Evidently 0.7.21 監控可執行基準與相容性回歸"
date: 2026-09-06
status: IMPLEMENTED
owner: Claude
reviewer: Antigravity4
repository: alfloop-dev/odayplus
task: ODP-NLTK-MONITORING-BASELINE-001
base_ref: 62dfc845925ed1b90bf06c0cbb490a0b82ed9b3a
related_document: docs/evidence/ODP_NLTK_UNPATCHED_DEPENDENCY_DISPOSITION_2026-09-05.md
related_advisories:
  - GHSA-8mgp-746c-j5xp
  - OSV/PYSEC-2026-3740
---

# Pinned Evidently 0.7.21 監控可執行基準

## 1. 範圍與明確不宣稱事項

本 task 只做一件事：對**目前線上使用的** `evidently==0.7.21` 監控路徑，建立一份
從真實 runtime 讀回、可重跑、可比對的黃金基準，供後續替代引擎逐 case 對齊。

交付物：

| 路徑 | 內容 |
|---|---|
| `tests/models/fixtures/evidently_0_7_21/` | 44 個 case 的輸入定義、產生器、`manifest.json`、raw evaluation 與 normalized golden |
| `tests/models/test_evidently_monitor_baseline.py` | 對照 golden 的可重跑回歸套件 |
| `docs/evidence/completion/ODP-NLTK-MONITORING-BASELINE-001/` | 本文件與實測輸出 |

**本文件明確不宣稱下列任何一項**（逐條對應 task acceptance 的邊界）：

1. **不宣稱 NLTK 漏洞已修復或已從 production 移除。** `evidently 0.7.21` 仍在
   `pyproject.toml` 與 `uv.lock` 中，`nltk 3.10.3` 仍隨之安裝。本 task 未更動
   `pyproject.toml`、`uv.lock`、`NOTICE-THIRD-PARTY.md`、任何 production 程式、
   任何 CI 或安全閾值，也未產生或覆寫任何 SBOM。
2. **不構成 waiver、GO 或架構決策。** 使用者要求修復漏洞這件事，不等於已核准
   disposition §5.1 的原生引擎方案。本 task 沒有選定任何替代架構。
3. **不宣稱與任何替代引擎等價。** 本套件比對的是「同一顆引擎的前後兩次執行」。
   §6.3 要求的替代引擎容差必須另行按演算法推導並由 reviewer 核准。
4. **不宣稱本文件解除 #1188 的 fail-closed 判定，也不宣稱 PR #1188 可合併。**
   本 task 未執行任何 scanner，未讀任何 advisory DB。
5. **未測到的一律標記為未測，不當作不存在。** 第 7 節逐項列出限制與 `UNKNOWN`。

## 2. Runtime 與 provenance receipts

`manifest.json` 由產生器寫入，全部欄位皆為程式讀回，非手寫：

```json
{
  "source_sha": "62dfc845925ed1b90bf06c0cbb490a0b82ed9b3a",
  "source_branch": "task/ODP-NLTK-MONITORING-BASELINE-001",
  "source_worktree_clean": false,
  "generated_at": "2026-09-06T02:41:59Z",
  "environment": {
    "python_version": "3.12.14",
    "python_implementation": "CPython",
    "python_compiler": "Clang 22.1.3 ",
    "platform": "Linux-6.17.0-1022-gcp-x86_64-with-glibc2.39",
    "machine": "x86_64",
    "processor_architecture": "64bit"
  },
  "packages": {
    "evidently": "0.7.21",
    "nltk": "3.10.3",
    "pandas": "2.3.3",
    "numpy": "2.5.1",
    "scipy": "1.18.0",
    "scikit-learn": "1.9.0"
  },
  "rng_seed": 20260906,
  "rng_decimals": 6,
  "case_count": 44
}
```

兩點必須照實說明，不作美化：

- `source_sha` 是產生當下的 repository HEAD，也就是 task brief 的 source-doc-cache
  ref `62dfc845…`。fixtures 本身在那一刻尚未被追蹤，因此
  `source_worktree_clean` 為 `false`。這是產生器與被產生物之間必然的先後關係，
  不是髒工作區污染了結果：`source_sha` 指的是「基準是針對哪一版程式產生的」。
- `environment.platform` 含 kernel 版本，屬 provenance；回歸套件只斷言套件版本
  完全相符與 Python major.minor 相符，不斷言 kernel 字串。

產生與自我比對指令（皆為本機、無對外連線）：

```bash
uv run --frozen --python 3.12 python tests/models/fixtures/evidently_0_7_21/generate_baseline.py
uv run --frozen --python 3.12 python tests/models/fixtures/evidently_0_7_21/generate_baseline.py --check
```

`--check` 逐 byte 比對每個 case 檔，並比對 `manifest.json` 除四個 provenance
欄位（`generated_at`、`source_sha`、`source_branch`、`source_worktree_clean`）
以外的全部內容。

Python toolchain 必須釘 3.12：本 repo 的 `uv.lock` 含 `pgserver==0.1.4`，只有
`cp312` wheel，預設 CPython 3.14 會直接無法建立環境。

## 3. 演算法分流：實測結果對照 disposition §5.1

下表每一列的 `method` / `threshold` / 統計量都是從 `evaluation.json()` 讀回的，
不是從 §5.1 的表格抄的。`N` 為 reference 欄位**清理後**樣本數，`unique` 為
reference+current 清理後合併相異值數（定義見
`evidently/legacy/calculations/data_drift.py:138-149` 與
`stattests/registry.py:137-159`）。

| case | N | unique | 實測 method | threshold | 實測統計量 | drift_share | drift_detected |
|---|---|---|---|---|---|---|---|
| `numeric_merged_unique_2_ztest` | 200 | 2 | `Z-test p_value` | 0.05 | 1.0 | 0.0 | False |
| `numeric_merged_unique_3_chisquare` | 200 | 3 | `chi-square p_value` | 0.05 | 1.0 | 0.0 | False |
| `numeric_merged_unique_5_chisquare` | 200 | 5 | `chi-square p_value` | 0.05 | 1.0 | 0.0 | False |
| `numeric_merged_unique_6_ks` | 200 | 6 | `K-S p_value` | 0.05 | 1.0 | 0.0 | False |
| `numeric_reference_n_1000_ks` | 1000 | 1000 | `K-S p_value` | 0.05 | 1.0 | 0.0 | False |
| `numeric_reference_n_1001_wasserstein` | 1001 | 1001 | `Wasserstein distance (normed)` | 0.1 | 0.0 | 0.0 | False |
| `numeric_reference_n_1001_jensenshannon` | 1001 | 4 | `Jensen-Shannon distance` | 0.1 | 0.0 | 0.0 | False |
| `categorical_merged_unique_2_ztest` | 200 | 2 | `Z-test p_value` | 0.05 | 1.0 | 0.0 | False |
| `categorical_merged_unique_3_chisquare` | 200 | 3 | `chi-square p_value` | 0.05 | 1.0 | 0.0 | False |
| `categorical_reference_n_1001_jensenshannon` | 1001 | 3 | `Jensen-Shannon distance` | 0.1 | 0.0 | 0.0 | False |
| `text_percentile_stable` | 200 | 200 | `Percentile text content drift` | 0.95 | 0.19721636016480754 | 0.0 | False |
| `text_percentile_drifted` | 200 | 200 | `Percentile text content drift` | 0.95 | 1.0 | 1.0 | True |
| `text_absolute_drifted` | 1001 | 1001 | `Absolute text content drift` | 0.55 | 1.0 | 1.0 | True |

分流表 11 個分支（含 Text 兩個分支、z-test、Jensen–Shannon）全部由真實執行覆蓋，
與 §5.1 的 source inspection 一致。原本被列為 `UNKNOWN` 的 threshold 解析，
現在有實測值：p-value 類檢定一律 `0.05`，距離類檢定（Wasserstein、Jensen–Shannon）
一律 `0.1`，text percentile `0.95`、text absolute `0.55`。

### 3.1 邊界：分流看的是清理後的 N，不是原始列數

| case | 原始列數 | 清理後 N | 實測 method |
|---|---|---|---|
| `numeric_nan_cleaned_to_1000_ks` | 1001 | 1000 | `K-S p_value` |
| `numeric_nan_cleaned_to_1001_wasserstein` | 1002 | 1001 | `Wasserstein distance (normed)` |
| `numeric_infinity_cleaned_to_1001_wasserstein` | 1002 | 1001 | `Wasserstein distance (normed)` |

`+inf` / `-inf` 先被換成 NaN 再 drop，因此它們會改變分流用的 `N`，卻不會進入任何
統計量。任何替代引擎若用原始 `len(rows)` 判斷 1000 邊界，這三個 case 會直接抓到。

### 3.2 常數欄位與新類別

- `numeric_constant_stable_ztest`：零變異數欄位走 z-test（`unique <= 2`），
  p-value 1.0，不判漂移。
- `numeric_constant_shifted_ztest`：常數由 7.0 變 9.0，z-test p-value 0.0，判漂移。
- `categorical_new_label_chisquare`：current 出現 reference 沒有的類別，
  chi-square p-value 0.0，判漂移。類別標籤不做任何 normalization。

## 4. drift_share threshold 與欄位層旗標是兩件事

`mixed_share_meets_threshold` 與 `mixed_share_below_threshold` 用**完全相同的輸入**，
只改 `drift_share_threshold`：

| case | threshold | drift_share | drifted_columns | drifted_column_names | drift_detected |
|---|---|---|---|---|---|
| `mixed_share_meets_threshold` | 0.5 | 0.5 | 1 | `["demand"]` | **True** |
| `mixed_share_below_threshold` | 0.6 | 0.5 | 1 | `["demand"]` | **False** |

`drift_detected` 是 dataset 層判定（`drift_share >= threshold`）；
`drifted_column_names` 是欄位層清單，不受 share threshold 影響。替代引擎若把兩者
綁在一起，這一對 case 會抓到。

## 5. Prediction drift 覆蓋

`run_prediction` 的 8 個成功 case 與 8 個拒絕 case 全部有 golden。重點實測：

- **output types**：numeric、categorical、混合兩種、以及省略 `output_types` 時
  由 wrapper 自行推論（`{"prediction": "numeric", "risk_band": "categorical"}`）。
- **cohort 隔離**：每列 fixture 都帶 `entity_id`，但它不在 `prediction_columns`
  內，實測確認它不會出現在 report 的任何 `ValueDrift` 中。
- **policy threshold 解析**：
  - `prediction_policy_threshold_only`：governed 0.8、實測 share 0.5 → `drift_detected=False`，
    但 `drifted_column_names=["prediction"]`。
  - `prediction_requested_threshold_tightens_policy`：同樣輸入 + caller 指定 0.4
    （比 policy 嚴格）→ 接受，`drift_detected=True`。
  - `failure_prediction_requested_threshold_weakens_policy`：caller 指定 0.9
    （比 policy 寬鬆）→ `ValueError: prediction drift threshold cannot weaken the
    DecisionPolicy threshold`。**這是安全性質的拒絕，golden 逐字保存，不做任何寬鬆化。**

## 6. 四維監控能力：真實 caller、覆蓋與限制

| 維度 | 真實 owner / entry point | 本 baseline 覆蓋 | 限制 |
|---|---|---|---|
| 1. Data Drift | `EvidentlyDriftMonitor.run` | **完整**：25 個 data-drift case（含 3 個拒絕 case），涵蓋全部 11 個分流分支 | 只涵蓋單欄與雙欄 frame；未測寬表（數十欄）下的 share 精度與效能 |
| 2. Feature Drift | `_drifted_column_names` / `_drift_metric_detected` | **完整**：每個成功 case 都比對 `drifted_column_names`，並交叉檢查其長度等於引擎自報的 `DriftedColumnsCount` | 第一方是**重新實作**判定規則而非讀引擎旗標；文字分支的規則不一致風險見 §7.1 |
| 3. Prediction Drift | `EvidentlyDriftMonitor.run_prediction` / `run_prediction_drift` | **部分**：`run_prediction` 完整；`run_prediction_drift` 的相容別名參數（`cohort`、`prediction_output_columns`、`decision_policy`）**未覆蓋** | 見 §7.2 |
| 4. Performance Drift | `models/shared_ml/validation.py`、`modules/learninghub/application/monitor.py`、`release.py`、`modules/learninghub/domain/monitoring.py` | **本 baseline 不涵蓋**。這四個 owner 是第一方實作，不呼叫 Evidently；其回歸由既有 `modules/learninghub/tests/test_performance_drift_and_baseline_comparison.py`（10 個測試）承擔，本次 focused verification 一併執行 | 本 task 未新增效能維度的 golden；替代引擎若真的做了，仍必須自行證明此維度零迴歸，不能引用本文件 |

## 7. 限制與仍為 UNKNOWN 的項目

### 7.1 Text 分支：可達，且第一方判定規則與引擎判定規則不是同一條規則

**可達性（實測，非推論）**：`EvidentlyDriftMonitor.run` 不傳 `DataDefinition`，
欄位型別由 Evidently 自行推論。實測顯示，高基數自由文字欄位會被推論為
`ColumnType.Text`，兩個 text stat-test 都會被選中。也就是說，只要 caller 傳入自由
文字欄位，**不需要任何 opt-in 就會走到 Text 分支**。disposition §6.2 Task 1 提到
「若 candidate 不支援 Text，須先證明生產路徑不會傳入 Text 欄位」——本實測顯示這個
證明無法由 wrapper 的 API 形狀取得，因為 wrapper 接受任意 `Sequence[Mapping]`。

**低基數字串不會走 Text 分支**：同樣是字串欄位，若只有少數幾種重複句子，會被推論為
`Categorical` 而走 chi-square / z-test / Jensen–Shannon。fixture 中的
`categorical_*` case 正是這一類。

**規則不一致風險（`UNKNOWN`，未證實亦未排除）**：`_drift_metric_detected` 對名稱不含
`p_value` 的 method 一律套用「距離規則」`value >= threshold`。但 text 分支
`metric_name` 裡的 `threshold`（0.95 / 0.55）**不是距離門檻**：
`perc_text_content_drift` 以 `p_value = 1 - threshold` 呼叫
`calculate_text_drift_score`，實際判定是 `roc_auc > bootstrap 百分位`
（`evidently/legacy/utils/data_drift_utils.py:122-166`）。兩條規則不同，理論上存在
「引擎判漂移但 `drifted_column_names` 漏列」的區間。

本 task 為此做了實測搜尋而**沒有找到**分歧樣本：共測 7 種文字組態（完全相同、整列
替換 1/4、1/6、1/8、1/10 的詞彙、逐詞替換 1/2、1/3、1/5、以及完全不相交的詞彙），
domain classifier 的 ROC AUC 不是落在 0.19–0.34 就是落在 0.98–1.0，兩條規則在所有
實測點都給出相同結論。**這是「未找到」，不是「不存在」**；本文件不宣稱兩條規則等價。
回歸套件以 `test_first_party_column_list_agrees_with_the_engine_drift_count` 對每個
case 斷言兩者一致，未來若有 fixture 讓它們分歧，測試會直接紅。

**text stat-test 不觸碰 NLTK**：`calculate_text_drift_score` 用的是 sklearn 的
`TfidfVectorizer(stop_words="english")` + `SGDClassifier`，兩者皆 `random_state=42`
且 bootstrap `seed=42`，因此可重現。它不載入任何 NLTK corpus，也不下載任何模型。

### 7.2 未覆蓋項目（逐項列出，不當作不存在）

1. `run_prediction_drift` 的相容別名參數（`cohort`、`prediction_output_columns`、
   `decision_policy`）未建立 golden。它只是轉呼叫 `run_prediction`，但轉呼叫本身
   未被本 baseline 測到。
2. `prediction_drift_threshold_from_policy` 的多層 fallback 解析（
   `prediction_drift_by_model`、`metric_thresholds_by_model`、`metric_thresholds`…）
   只覆蓋了 `prediction_drift.drift_share_threshold` 一條路徑。
3. 效能維度（第 4 維）無新 golden，見 §6。
4. 寬表、缺欄、混合 dtype（同一欄同時有 str 與 float）未測。
5. 20MB 級資料量與資源上限未測；那屬另行的資源 gate，不是演算法等價證明。
6. NLTK 受影響 API（`TransitionParser.train/parse`、`AveragedPerceptron.save/load`、
   `PerceptronTagger.save_to_json`、`save_maxent_params`）是否被 Evidently 內部
   **呼叫**（而非只是被匯入）未測，見 §8。

### 7.3 已知的引擎未處理失敗

`failure_run_all_values_missing`：單一欄位全為缺失值時，清理後兩個 series 皆為空，
pinned Evidently 0.7.21 從 stat-test 內部拋出 `ZeroDivisionError: division by zero`。
這**不是**第一方驗證錯誤，而是引擎的未處理失敗路徑。golden 逐字保存例外型別與訊息，
沒有把它包裝成友善錯誤，也沒有把它從 fixture 移除。

## 8. Runtime footprint：一次純數值 drift 執行會把 NLTK 載進 production process

disposition §3.3 明確要求可達性必須由 pinned runtime 的 probe 決定，不得以
「第一方沒有 `import nltk`」推論。本 task 在全新直譯器中做了這個 probe
（`test_a_plain_numeric_drift_run_loads_nltk_into_the_process`）：

- 只 import `modules.learninghub.infrastructure` 時，`sys.modules` 中的 `nltk*` 模組數為 **0**。
- 執行一次最普通的呼叫（50 列單一 float 欄位、沒有任何文字欄位）之後，
  `sys.modules` 中的 `nltk*` 模組數為 **245**。
- 其中包含 advisory 所指受影響 API 所在的三個模組：
  `nltk.parse.transitionparser`、`nltk.tag.perceptron`、`nltk.classify.maxent`，
  以及 `nltk.data`、`nltk.downloader`。

**這句話的邊界要說清楚**：這是**模組匯入可達性**的量測，證明「未直接 import」不能
用來主張不可達；它**不**證明上述任一 API 被呼叫，**不**證明可被利用，也**不**是
任何修復或風險判定。要判定實際呼叫，需要另行由獲授權的 task 做 call-level trace。

附帶的靜態範圍事實（`grep`，非執行）：evidently 0.7.21 內部 `import nltk` 的檔案為
`legacy/features/words_feature.py`、`sentiment_feature.py`、
`OOV_words_percentage_feature.py`、`trigger_words_presence_feature.py`、
`descriptors/text_match.py`、`guardrails/guards/word_presence.py`。這些是 text
descriptor / guardrail，且都會呼叫 `nltk.download("wordnet"/"vader_lexicon"/"words")`。
本 task 的 acceptance 禁止下載 NLP 模型，因此**刻意不執行**這些路徑；這是明確保留的
阻礙，不是把功能砍掉，也不是偽造基準。`DataDriftPreset` 路徑不經過它們。

## 9. Normalization 政策

只有一個欄位被 normalize：`EvidentlyDriftResult.snapshot_id`，且僅限 caller 傳入
`snapshot_id=None`、由 wrapper 自行填 `f"evidently-{uuid4()}"` 的情況。normalizer
會先用 uuid4 形狀的 regex 驗證，通過才替換成 `<auto-generated-uuid4>`；若 caller
明明有傳 id 卻收到自動產生的值（或反之），normalizer 直接 raise。

**絕不 normalize**：統計量、p-value、距離、method 名稱、threshold、
`drift_detected` / `drifted_columns` / `drift_share` / `drifted_column_names`、
類別標籤、欄位名稱、metric id 與順序、以及所有拒絕的例外型別與訊息。

這條政策同時寫進 `manifest.json["normalization"]`，並有三個測試守住它：
政策未被放寬、caller 提供的 id 不被替換、以及**被竄改的統計量必定讓比對失敗**
（`test_normalizer_never_smooths_a_statistic_away` 把 p-value 從 1.0 改成 0.9999，
斷言比對會紅）。

Fixture 完整性另由 `manifest.json` 中每個檔案的 SHA-256 守住：手改 golden 會被
`test_fixture_files_match_the_hashes_recorded_in_the_manifest` 抓到。

## 10. 容差

回歸比對對**結構欄位**（method、threshold、欄位名、旗標、count、share、metric id、
順序）採完全相等；只有非布林數值採 `rel_tol=1e-9, abs_tol=1e-12`。

這個容差存在的唯一理由是：同一顆引擎在不同 BLAS / CPU 下的浮點運算並非逐位保證。
**它不是、也不得被引用為替代引擎的等價容差**——§6.3 要求那個容差必須按實際演算法
推導並由 reviewer 核准。
