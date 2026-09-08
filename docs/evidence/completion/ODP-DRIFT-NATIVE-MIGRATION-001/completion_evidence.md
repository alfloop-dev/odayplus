---
evidence_id: ODP-DRIFT-NATIVE-MIGRATION-001-COMPLETION
title: "原生漂移統計核心 native_drift 交付與 Evidently 0.7.21 等價收據"
date: 2026-09-06
status: DELIVERED_CORE_ONLY
owner: Claude2
reviewer: Antigravity4
repository: alfloop-dev/odayplus
task: ODP-DRIFT-NATIVE-MIGRATION-001
base_ref: 62dfc845925ed1b90bf06c0cbb490a0b82ed9b3a
reference_engine: "evidently==0.7.21"
related_documents:
  - docs/evidence/ODP_NLTK_UNPATCHED_DEPENDENCY_DISPOSITION_2026-09-05.md
successor_task: ODP-DRIFT-DEP-REMOVE-002
---

# 原生漂移統計核心交付收據

## 1. 交付範圍與明確不宣稱事項

### 1.1 本 task 交付物

| 檔案 | 性質 | 說明 |
|---|---|---|
| `modules/learninghub/infrastructure/native_drift.py` | 新增產品程式碼 | 原生漂移統計引擎；尚未接入 production |
| `tests/models/test_native_drift.py` | 新增測試 | 129 個測試，含 65 個黃金等價案例 |
| `docs/evidence/completion/ODP-DRIFT-NATIVE-MIGRATION-001/capture_evidently_reference.py` | 收據產生器 | 執行 pinned 0.7.21 產生 golden |
| `docs/evidence/completion/ODP-DRIFT-NATIVE-MIGRATION-001/evidently_0_7_21_reference.json` | Golden baseline | 65 案例的輸入與 0.7.21 實際輸出 |

未修改任何既有檔案。`evidently_monitor.py`、`oss_capabilities.py`、`pyproject.toml`、`uv.lock`
皆維持原狀；`modules/learninghub/infrastructure/__init__.py` 也未變更（測試以 submodule
形式 `from modules.learninghub.infrastructure import native_drift` 匯入）。

### 1.2 本 task **不**宣稱的事項

- **不宣稱 NLTK 漏洞已從 production 移除**。`evidently 0.7.21` 與 `nltk 3.10.3` 仍在
  `pyproject.toml` / `uv.lock` / 執行環境與 SBOM 中。本 task 只證明「原生核心已具備等價能力」。
- **不宣稱 production 已切換**。`EvidentlyDriftMonitor.run` / `run_prediction` 仍呼叫
  `Report([DataDriftPreset(...)])`。切換、依賴移除、lock/SBOM 驗證與四維監控等價由
  `ODP-DRIFT-DEP-REMOVE-002` 原子承接。
- **不宣稱 baseline 等價已完成**。`ODP-NLTK-MONITORING-BASELINE-001` 的 worktree 與
  fixtures 未被讀取也未被修改。
- 未申請、未使用任何 waiver / suppression / ignore；未偽造修補版本；未使用 `--no-deps`；
  未把漏洞搬到未掃描 scope；未停用任何既有監控功能。

## 2. Reference 取得方式（不手刻）

黃金 baseline 全部由**執行** pinned 發行版產生，沒有任何一個期望值是人工填寫的。

1. **Source inspection**：直接讀取 `.venv/.../evidently/` 之 0.7.21 原始碼，確認
   `legacy/calculations/stattests/registry.py::_get_default_stattest` 的分流、各 stattest
   的比較方向與預設閾值、`core/datasets.py::infer_column_type` 的型別推論、
   `legacy/calculations/data_drift.py::get_one_column_drift` 的清理與 `get_dataset_drift`
   的 share 分母、以及 `core/metric_types.py::explicit_metric_id` 的 `metric_name` 文法。
2. **Runtime capture**：`capture_evidently_reference.py` 以 production 完全相同的進入點
   `Report([DataDriftPreset(**preset_kwargs)]).run(current, reference)` 跑 65 個合成固定
   案例，並把 `evaluation.json()` 原樣、以及 `EvidentlyDriftMonitor._result` /
   `_drifted_column_names` 推導出的 consumer 欄位一併寫入 golden。
3. **可重現**：harness 為決定性；重跑後除 `captured_at` 外逐位元相同（已實測）。

Golden header 已釘住環境：`evidently 0.7.21`、`scipy 1.18.0`、`numpy 2.5.1`、
`pandas 2.3.3`、`scikit-learn 1.9.0`、`Python 3.12.14`。

## 3. 實作的演算法與分流（全部依 0.7.21 receipt，非推定）

### 3.1 欄位型別推論（`infer_column_type`，對 current frame）

| dtype | 規則 | 結果 |
|---|---|---|
| `float*` | — | `num` |
| `int*` | `nunique <= 10` | `cat`，否則 `num` |
| `string`/`str`/`object`(首尾皆 str) | `nunique > count * 0.5` | `text`，否則 `cat` |
| `object`(首尾皆 list/tuple) | — | `list` |
| `object`(其他) / 全空 | — | `unknown` |
| `bool` / `category` | — | `cat` |
| `datetime*` | — | `datetime` |

`datetime` / `unknown` / `list` 欄位不進入漂移評估，也**不進入 share 分母**（已由
`real_datetime_column`、`unknown_object_column` 兩案例實證）。

### 3.2 統計方法分流（依**清理後**的 reference 列數）

| 型別 | reference 列數 ≤ 1000 | reference 列數 > 1000 |
|---|---|---|
| `num` | combined unique ≤ 2 → **z**；3–5 → **chi-square**；> 5 → **KS** | ≤ 5 → **Jensen-Shannon**；> 5 → **normalized Wasserstein** |
| `cat` | ≤ 2 → **z**；> 2 → **chi-square** | **Jensen-Shannon** |
| `text` | **perc_text_content_drift**（bootstrap） | **abs_text_content_drift** |

`combined unique` = `pd.concat([reference, current]).nunique()`。列數邊界取**清理後**長度：
1001 列中含 1 個 NaN 的 reference 會落回 KS 而非 Wasserstein（案例
`num_float_n1001_nan_cleans_to_1000`）。

### 3.3 閾值預設與比較方向（逐項保留，含嚴格／非嚴格差異）

| 方法 | 預設閾值 | 判定方向 |
|---|---|---|
| `ks` | 0.05 | `p_value <= threshold`（**非嚴格**） |
| `chisquare` | 0.05 | `p_value < threshold` |
| `z` | 0.05 | `p_value < threshold` |
| `wasserstein` | 0.1 | `distance >= threshold` |
| `jensenshannon` | 0.1 | `distance >= threshold` |
| `abs_text_content_drift` | 0.55 | `roc_auc > threshold`（**嚴格**） |
| `perc_text_content_drift` | 0.95 | `roc_auc > random-classifier percentile`（閾值不是比較界） |

閾值解析順序：per-column → 型別（cat/num/text）→ 全域 → 方法預設。
邊界方向已由 `test_kolmogorov_smirnov_drifts_when_the_p_value_equals_the_threshold`、
`test_p_value_tests_other_than_ks_are_strict_at_the_threshold`、
`test_distance_tests_drift_when_the_distance_equals_the_threshold`、
`test_absolute_text_content_drift_is_strict_at_the_threshold` 以「分數恰等於閾值」實測。

### 3.4 兩段式評估與 share 分母

released engine 實際跑兩段，且兩段的缺失值處理不同，本實作照樣保留：

- **value-drift 段**（每欄一個 `ValueDrift`）：**一律**先 `replace([-inf, inf], nan).dropna()`。
- **counted 段**（`DriftedColumnsCount` 的 count 與 share）：**只有該欄真的含 NaN 時**才清理。
  含 `inf` 但不含 `NaN` 的欄位會帶著 `inf` 進入統計檢定。

因此兩段可以合法地對同一欄位得到不同結論。案例
`inf_reference_shifted_divergence` 就是實證：`drifted_columns == 0`（counted 段的
Wasserstein 因 `std=inf` 得到 `nan`）但 `drifted_column_names == ("value",)`（value-drift 段
清理後 KS 的 `p == 0`）。此行為在 released engine 上完全相同。

share 分母 = counted 段的欄位數；`num` 群組還會再依 **reference frame 的 dtype** 過濾
（reference 為 object、current 為 float 的欄位會整個掉出分母）。

### 3.5 Text 處理不使用 NLTK

`perc_/abs_text_content_drift` 的實作是 domain classifier：
`TfidfVectorizer(sublinear_tf=True, max_df=0.5, stop_words="english")` +
`SGDClassifier(alpha=1e-4, max_iter=50, penalty="l1", loss="modified_huber", random_state=42)`，
以 `train_test_split(test_size=0.5, random_state=42)` 的 held-out ROC AUC 為漂移分數。
停用詞來自 **scikit-learn 內建清單**；不涉及任何 NLTK corpus、tokenizer 或下載。
（0.7.21 的 text 分支本身就沒有走 NLTK；NLTK 是 evidently 的 `requires_dist` 依賴。）

## 4. Text 分支在 production 的可達性實證

acceptance 要求：若宣稱 Text 不可達，必須有真實 caller / type-inference 實證。
**實測結論：Text 分支在目前 production 是可達的，因此已完整實作，未以「未 import」推定不可達。**

證據鏈：

1. `EvidentlyDriftMonitor.run` / `run_prediction` 都是把 `pd.DataFrame` 直接交給
   `Report(...).run(current, reference)`，**沒有傳入任何 column mapping 或型別宣告**。
   型別完全由 evidently 對 current frame 自動推論。
2. `run_prediction` 的 `output_types` 只用於第一方驗證（`_validate_prediction_rows`），
   **不會傳給 evidently**。因此宣告為 `categorical` 的字串輸出欄位，若基數高，仍會被
   推論成 `text`。
3. 生產實際欄位名就是 `prediction`（見 `modules/learninghub/tests/test_prediction_drift.py::_rows`）。
   案例 `prediction_column_high_cardinality_text` 以該欄位名、100 列高基數字串輸出跑
   pinned 0.7.21，實得：

   ```text
   ValueDrift(column=prediction,method=Percentile text content drift,threshold=0.95) = 0.12004801920768308
   ```

   亦即單一 `prediction` 欄位就足以觸發 text 分支。
4. `EvidentlyDriftMonitor.run` 接受任意 rows，`entity_id` 之類的高基數字串欄位同樣會落入
   text 分支（案例 `text_high_cardinality_n100`、`mixed_num_cat_text`）。
5. 型別邊界已釘住：`nunique == count * 0.5` → `cat`（`str_unique_exactly_half`）；
   `nunique == count * 0.5 + 1` → `text`（`str_unique_just_over_half`）。

## 5. 等價驗收結果

`tests/models/test_native_drift.py::test_native_engine_matches_reference_report`
對 **65/65** 案例逐項比對，全部通過：

- `metrics` 陣列長度與順序
- 每筆 `metric_name` 字串（含參數順序：caller 指定的參數依宣告順序在前，
  auto-resolve 的 method / threshold 依解析順序附加在後）
- 每筆 `config`（僅 `type` 前綴依 provenance 要求不同，見 §6）
- 每筆 `value`（p-value / distance）：**完全相等**，非近似。因兩邊呼叫的是同一份
  scipy/sklearn，不需要容差；測試以 `==` 斷言，僅 NaN 以 `isnan` 對齊
- `drifted_columns`、`drift_share`、`drift_detected`（`share >= drift_share_threshold`）
- `drifted_column_names`（2 項例外見 §6.3）
- 錯誤路徑：`nan_all_reference` / `nan_all_current` 之 `ValueError` 訊息逐字相同

案例矩陣涵蓋（依 disposition §6.3 的 golden 標準）：

| 面向 | 案例 |
|---|---|
| 樣本量 | 100 / 200 / 1000 / 1001 列；清理後跨越 1000 邊界 |
| combined unique 邊界 | 2 / 3 / 5 / 6；int 基數 10 / 11；text 比例 0.5 / 0.5+ |
| 分流 | z / chi-square / KS / Jensen-Shannon / Wasserstein / 兩種 text |
| 缺失值 | NaN、±inf、僅 inf 無 NaN、全 NaN（reference 與 current 各一） |
| 常數欄位 | 相同常數、不同常數、常數 reference 的 Wasserstein 正規化下限 |
| 類別增減 | 新增未見類別（期望次數為 0）、reference 類別在 current 消失 |
| empty / mismatch | 空欄位、跨 frame dtype 不一致、缺欄位 |
| 閾值邊界 | drift share 恰等於門檻 / 低於門檻 / 門檻 1.0；顯式 threshold / num_threshold / per_column_threshold / method |
| production 形狀 | `prediction` 單欄 numeric / probability / 2-unique / 字串標籤 / 高基數 text；`target`+`prediction` 併存 |
| 序列化 | `metric_name` 參數順序、dict 型參數不入名、datetime/unknown 欄位被略過 |

## 6. 三項刻意分歧（未默默 normalize）

三項全部在測試中逐項釘住，任何一項改變都會讓測試變紅。

### 6.1 Provenance：`config.type` 前綴與 report envelope

原生報表的每筆 `config.type` 是 `native_drift:metric_v2:...` 而非
`evidently:metric_v2:...`，並在 report 頂層附加 `engine: "native_drift"` 與
`engine_version`。`metric_name` / `value` / `metrics` 順序完全不變，既有 consumer 的
key-based 讀取不受影響。`id` 為原生決定性 sha256 摘要（非 evidently 的 fingerprint）。
這是 acceptance 要求的「如實標原生實作、不假稱仍由 Evidently 計算」。
測試：`test_report_declares_the_native_engine_and_never_claims_evidently`。

### 6.2 `ZeroDivisionError` → 可診斷的 `NativeDriftError`

當欄位型別推論後 counted 段沒有任何欄位時（例：某欄在 current 是 float、在 reference 是
object 字串），0.7.21 會在 `share = drifted / len(drift_metrics)` 拋出
`ZeroDivisionError: division by zero`。原生引擎改拋
`NativeDriftError("no drift-eligible columns remain after column typing; ...")`。

`NativeDriftError` 是 `ValueError` 子類，與 monitor 既有的輸入錯誤同一族，caller 若
`except ValueError` 行為不變；只有 `except ZeroDivisionError` 才會受影響（現行程式碼中沒有）。
**這是一個既有 latent 缺陷**：`EvidentlyDriftMonitor` 只驗證兩邊欄位名集合相同，不驗證
dtype，因此此路徑在 production 可達。已提請 cutover task 評估是否要在 monitor 層擋下。
測試：`test_dtype_mismatch_raises_a_diagnosable_error_instead_of_dividing_by_zero`。

### 6.3 `drifted_column_names`：2/65 案例與 released parser 不同

`EvidentlyDriftMonitor._drifted_column_names` 不是讀引擎的判定，而是**用字串剖析
`metric_name`、再從 method 標籤猜比較方向**。該 heuristic 在兩種情況會答錯：

| 案例 | released parser | 原生引擎 | count 支持誰 |
|---|---|---|---|
| `text_high_cardinality_n100_shifted` | `[]` | `["note"]` | count = 1 → **原生** |
| `explicit_method_ks_above_1000` | `["demand"]` | `[]` | count = 0 → **原生** |

- 前者：text 漂移實際是跟 bootstrap 隨機分類器百分位比，不是跟報表上的 0.95 比；
  parser 讀成 `0.941 >= 0.95` 為 False。**此路徑 production 可達**（見 §4）。
- 後者：caller 指定 method 時 `metric_name` 會回填原始名 `ks`，parser 因字串不含
  `p_value` 而套用距離規則。production 目前不會指定 method，故不可達。

兩者原生答案都與 `DriftedColumnsCount` 一致，亦即原生修正的是 parser 的缺陷、不是改動引擎。
測試 `test_known_name_divergences_agree_with_the_drifted_column_count` 會同時斷言
「原生答案與 count 一致」與「把既有 parser 套在原生 report 上會逐字重現舊答案」，
確保 cutover 時這個差異是被看見而不是被吞掉的。

### 6.4 未複製的部分（無可觀察差異）

render / scatter / distribution / correlation widget payload 未複製。理由：
`Report.json()` 與 `.dict()` 的輸出只有 `{"metrics": [...], "tests": []}`，
不含這些欄位（已由 65 個 golden 逐案實證），production consumer
（`release.py` 的 `drift_report_json`）也只讀 `metrics`。這是刻意的非目標，不是遺漏。

## 7. 測試是否真的擋得住錯誤（mutation 驗證）

為避免「綠測試沒碰到缺陷路徑」，對 `native_drift.py` 施加 8 個單點突變，逐個重跑
`tests/models/test_native_drift.py`。**8/8 全部被測到（KILLED）**：

| 突變 | 結果 |
|---|---|
| `SMALL_SAMPLE_ROW_LIMIT` 1000 → 1001 | KILLED |
| KS 方向 `<=` → `<` | KILLED |
| `INTEGER_CARDINALITY_LIMIT` 10 → 5 | KILLED |
| Wasserstein 正規化下限 `max(std, 0.001)` → `std` | KILLED |
| chi-square 移除 `k_norm` 重新縮放 | KILLED |
| counted 段改為一律清理 | KILLED |
| `TEXT_CARDINALITY_RATIO` 0.5 → 0.9 | KILLED |
| 距離方向 `>=` → `>` | KILLED |

突變腳本為一次性驗證，未納入交付物；上表為其輸出。

## 8. 重現指令與收據

所有指令都在 repo root、以 pinned lockfile 與 Python 3.12 執行。

```bash
# 1) 重新產生 golden（需要 evidently 仍安裝；cutover 後不再可跑）
PYTHONPATH=. uv run --frozen --python 3.12 \
  python docs/evidence/completion/ODP-DRIFT-NATIVE-MIGRATION-001/capture_evidently_reference.py \
  docs/evidence/completion/ODP-DRIFT-NATIVE-MIGRATION-001/evidently_0_7_21_reference.json
# -> wrote ... (65 cases)；除 captured_at 外與已提交檔案逐位元相同

# 2) 原生等價與行為測試
uv run --frozen --python 3.12 pytest tests/models/test_native_drift.py --no-header
# -> 129 passed

# 3) 既有相鄰套件無回歸
uv run --frozen --python 3.12 pytest tests/models modules/learninghub/tests \
  tests/contract/test_deferred_oss_adr.py --no-header
# -> 425 passed

# 4) Lint
uv run --frozen --python 3.12 ruff check \
  modules/learninghub/infrastructure/native_drift.py \
  tests/models/test_native_drift.py \
  docs/evidence/completion/ODP-DRIFT-NATIVE-MIGRATION-001/
# -> All checks passed!
```

註：`uv run` 必須指定 `--python 3.12`；cp314 下 `pgserver 0.1.4` 無 wheel 會使環境建立失敗。

## 9. 依賴面

`native_drift.py` 只 import `numpy` / `pandas` / `scipy` / `scikit-learn`。
`numpy>=2.0` 與 `scikit-learn>=1.5` 已是 `pyproject.toml` 的直接相依；`scipy` 與 `pandas`
由既有相依帶入且既有 production 程式碼（含 `evidently_monitor.py`）已在使用。
**本 task 未新增任何依賴，`pyproject.toml` 與 `uv.lock` 未變更。**

不含 Evidently / NLTK 的結構性驗證有兩層，皆為測試：

1. `test_native_module_does_not_import_evidently_or_nltk`：AST 解析模組，斷言 import
   目標不含 `evidently` / `nltk`，且不存在 `import_module` / `__import__` 呼叫
   （即無 runtime fallback）。
2. `test_importing_the_native_module_loads_neither_engine`：在**乾淨子行程**中 import
   本模組，斷言 `sys.modules` 內既無 `evidently` 也無 `nltk`。

## 10. 交接給 ODP-DRIFT-DEP-REMOVE-002

`NativeDriftEngine.run(current, reference)` 的參數順序與回傳物件的 `.dict()` / `.json()`
刻意對齊 `Report([DataDriftPreset(...)]).run(current, reference)`，因此
`EvidentlyDriftMonitor._result(evaluation=...)` 可以不改地接收原生 evaluation。

cutover 需要處理、但**不在本 task 範圍**的項目：

1. 把 `run` / `run_prediction` 的 `Report([DataDriftPreset(...)])` 換成 `NativeDriftEngine`；
   `drift_share` 對應 `drift_share_threshold`。
2. `EvidentlyDriftResult.engine` 預設值 `"evidently"` 需改為原生標示，否則收據會說謊。
3. 決定 `drifted_column_names` 要改用 `evaluation.drifted_column_names`（建議，正確）
   或續用 `_drifted_column_names(payload)`（會保留 §6.3 的兩個錯誤）。
4. `OssCapability.MODEL_MONITORING: ("evidently",)` 的能力映射與
   `require_oss_capability` 呼叫。
5. `pyproject.toml` / `uv.lock` / `NOTICE-THIRD-PARTY.md` / SBOM 的 evidently+nltk 移除與
   重新驗證，並更新 `tests/contract/test_deferred_oss_adr.py` 對 `evidently_monitor.py`
   的斷言。
6. §6.2 的 dtype 不一致路徑是否要在 monitor 層先擋。
