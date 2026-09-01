---
adr_id: ADR-0004
title: "Evidence Level 的單一權威定義與 INSUFFICIENT_EVIDENCE 的歸屬"
version: 1.0.0
status: accepted
document_class: architecture-decision-record
project: ODay Plus
language: zh-TW
decision_date: 2026-09-01
updated_at: 2026-09-01
owner: "Architecture Owner"
approvers: "Human/Ops"
owners:
  - Architecture Owner
  - Validation Owner
content_format: markdown
source_documents:
  - ODP-00-04_DOCUMENT_VERSION_AND_ADR_GOVERNANCE.md
  - ODP-SA-07_BUSINESS_RULES_AND_DECISION_POLICIES.md
  - ODP-ML-05_CAUSAL_INFERENCE_AND_EXPERIMENT_DESIGN.md
related_requirements:
  - ODP-FR-AD-007
  - ODP-BR-AD-004
  - ODP-AC-BR-005
review_trigger: "Review when a sixth evidence tier is proposed, or when automated policy actions begin consuming evidence level."
---

# ADR-0004: Evidence Level 的單一權威定義與 INSUFFICIENT_EVIDENCE 的歸屬

## Context

### 原始問題陳述

兩份正式交付文件對同一個 Evidence Level 階梯給出不相容的定義：

| `ODP-SA-07` 第 6 節（第 2 批） | `ODP-ML-05` 第 5 節（第 6 批） |
|---|---|
| `L0_OBSERVED` | `L0` Anecdotal |
| `L1_ADJUSTED` | `L1` Before/After |
| `L2_MATCHED_CONTROL` | `L2` Matched Descriptive |
| `L3_DID_VALIDATED` | `L3` DiD Validated |
| `L4_EXPERIMENTAL` | `L4` Randomized／Staggered |
| `INSUFFICIENT_EVIDENCE` | `L5` Replicated／Policy Ready |

依 `ODP-00-04`，兩份同為 `formal_deliverable` 且狀態均為 `draft-for-review`，衝突無法以批次先後或文件位階自動解決。

### 裁決前補查所修正的前提

本 ADR 初版將問題界定為「兩套定義擇一」。裁決前對 `origin/dev@595e7501` 的 130 個 `evidence_level` 使用點（橫跨 20 個非測試檔案）逐一盤點後，該界定並不完整：**實際存在三套值，彼此之間沒有任何轉換**。

| 層 | 位置 | 值 | 產生者 | 消費者 |
|---|---|---|---|---|
| 實作 ① | `modules/adlift/domain/incrementality.py` | `L0_ANECDOTAL`…`L5_POLICY_READY`（`.value` = `"L0"`…`"L5"`） | AdLift 增量分析 | AdLift API（`adlift.py:271`） |
| 實作 ② | `shared/domain/models.py:419`、`infra/db/migrations/000001_baseline_canonical_schema.sql:490`、`packages/schemas/canonical/index.ts:358` | `low` / `medium` / `high` / `causal_candidate`，預設 `'medium'` | 無 | dbt `intervention_panel_view.sql`（讀取永不出現的值） |
| 實作 ③ | `apps/web/features/operator/growthViewModel.ts` | `high` / `medium` / `low`（`ConfidenceLevel`） | 前端 fixtures | 營收成長工作區 |

三項連帶事實：

1. **AdLift 算出的等級從未被持久化。** L0–L5 是 Python enum，資料庫欄位為 `VARCHAR(50)` 且語意為三檔粗粒度。型別能塞、語意對不上，且程式碼中無任何轉換。
2. **`causal_candidate` 是孤兒值：有讀取者，沒有產生者。** 初次盤點時判定它為死值，那個判定不正確 —— 實作 task 執行時發現 `pipelines/dbt/models/model_ready/intervention_panel_view.sql` 有一個分支消費它（`when outcomes.evidence_level = 'causal_candidate' then 0.9`）。但仍然沒有任何程式碼寫入該值，所以那個分支永遠不會被選中，dbt 在為一個不會出現的值保留一條路徑。其對應的概念實際由 AdLift 的 `CAUSAL_MIN_EVIDENCE = L3` 承擔。裁決結論不變（移除），但理由是「有讀無寫的孤兒」而非「完全無人引用」。
3. **`DEFAULT 'medium'` 是 fail-open，而且 dbt 有第二道。** 未評級的干預效果記錄會被存為「中等證據」而非「未評級」。同一個檢查也發現 `intervention_panel_view.sql` 的 confidence 映射以 `else 0.7` 收尾 —— 任何未知或空的等級都取得 0.7 信心，高於 `low` 而僅略低於 `medium`，並照常進入訓練資格判定。兩者皆為 `ODP-LISTING-PROMOTION-FAILOPEN-001` 所修補的同型缺陷：缺資料時填入看似正常的值。

因此本 ADR 需裁決的不是一件事，是三件。

## Decision

### D1 — 對外的證據階梯以 `ODP-ML-05` 第 5 節的 L0–L5 為單一權威

`ODP-SA-07` 第 6 節改為引用 `ODP-ML-05` 第 5 節，不再自行定義階梯。

### D2 — 持久化層與 D1 對齊，並移除其 fail-open 預設

1. `evidence_level` 欄位改存 L0–L5，允許為空（表示未評級）。
2. 移除 `DEFAULT 'medium'`。
3. 移除孤兒值 `causal_candidate`，並將 `intervention_panel_view.sql` 的 confidence 映射改用 L0–L5；其 `else 0.7` 改為 `0.0`，未評級記錄同時排除於訓練資格之外（`exclusion_reason = 'evidence_unrated'`）。
4. 既有記錄回填為「未評級」（NULL），**不追溯定級**。那些記錄是在無評級機制下產生的，追溯定級等同偽造其來源。此原則與 `ODP-SD-AMD-001` 第 9 節對政策版本回填的處理一致。

### D3 — `INSUFFICIENT_EVIDENCE` 與階梯正交，不是階梯的一級

證據判定的輸出為一組欄位而非單一列舉值：

| 欄位 | 型別 | 語意 |
|---|---|---|
| `evidence_assessable` | boolean | 是否具備定級的最低條件 |
| `evidence_level` | `L0`–`L5`；`evidence_assessable = false` 時為 null | 證據強度 |
| `insufficiency_reason_code` | `evidence_assessable = false` 時必填 | 見下方 |

原因碼初版為 `SAMPLE_TOO_SMALL`、`NO_CONTROL`、`OVERLAPPING_TREATMENT`、`DATA_QUALITY_FAIL`。

對外呈現時，`evidence_assessable = false` 一律顯示為 `INSUFFICIENT_EVIDENCE`，滿足 `ODP-BR-AD-004` 的字面要求。

## Rationale

**D1 選 ML-05，理由不是「實作已經如此」。** 該論證是本 ADR 初版所用，且是最弱的一種 —— 它把既成事實當作依據。真正的理由是：**三套之中只有一套在運作**。AdLift 的 L0–L5 有產生者、有消費者、有排序語意，並有 `CAUSAL_MIN_EVIDENCE = L3` 這個實際用來阻擋因果宣稱的門檻（`ODP-BR-AD-001` Hard Constraint 的執行點）。SA-07 那套沒有任何實作；持久化那套沒有任何寫入者 —— 它有一個 dbt 讀取分支，但讀的是永遠不會被寫進去的值。統一到唯一運作中的定義，是把兩個空殼對齊到一個實體，而非在對等選項間擇一。

次要理由有二。其一，ML-05 的級名描述**方法**而非處理結果：`Matched Descriptive` 指明「有對照但 pre-trend 未通過」這個具體狀態，而該狀態正是 `ODP-BR-AD-001` 的判定依據；`L2_MATCHED_CONTROL` 只說有對照，未涵蓋此區分。其二，ML-05 有 L5「Replicated／Policy Ready」，是自動化政策規則的啟用前提 —— `ODP-FR-PRICE-006` 的 Bandit 若以證據強度為 Gate 條件，需要這一級；SA-07 止於 L4，無法表達「可用於自動化」。

**D2 選對齊而非維持雙層加轉換。** 「分析階梯」與「呈現粒度」分離只有在兩層各自都有真實需求時才划算。粗粒度那層目前沒有任何消費者，為其維護一個轉換函式是為假想需求付出治理成本 —— 轉換規則本身會成為需要版本控制的政策。若日後前端確實需要三檔呈現，那屬於呈現層決定，可在 UI 完成，不需要在持久化層固化。

**D3 選正交而非併入階梯，理由是程式層面的硬約束。** `_EVIDENCE_ORDER` 是有序 tuple，`is_causal_evidence()` 以 `_EVIDENCE_ORDER.index()` 比較大小：

```python
def is_causal_evidence(level: EvidenceLevel) -> bool:
    return _EVIDENCE_ORDER.index(level) >= _EVIDENCE_ORDER.index(CAUSAL_MIN_EVIDENCE)
```

將 `INSUFFICIENT_EVIDENCE` 置入該 tuple 會使其取得序數並參與比較。放在 L0 之前意味著「比軼事更弱的證據」，而其真實語意是「這把尺不適用」。兩者不同：`L0_ANECDOTAL` 宣稱有觀察但僅軼事等級支持；`INSUFFICIENT_EVIDENCE` 表示資料不足以支持任何方向的結論，**包括「無效」在內**。以有序尺度上的一點表達「不可評估」，會使下游將未定級當作一個真實的低等級處理。

## Consequences

### 文件

| 文件 | 修訂 |
|---|---|
| `ODP-SA-07` §6 | 整節改為引用 `ODP-ML-05` §5；補充 `INSUFFICIENT_EVIDENCE` 作為正交狀態 |
| `ODP-SA-07` §3（`ODP-BR-AD-004`） | 表述改為「`evidence_assessable = false` 時對外呈現為 `INSUFFICIENT_EVIDENCE` 並記錄原因碼」 |
| `ODP-ML-05` §5 | 補上正交狀態與四個原因碼 |

### 實作

| # | 變更 | 檔案 |
|---|---|---|
| 1 | 新增 `evidence_assessable` 與 `insufficiency_reason_code` | `modules/adlift/domain/incrementality.py` |
| 2 | `is_causal_evidence()` 於 `evidence_assessable = false` 時直接回傳 `False`，不進入序數比較 | 同上 |
| 3 | `_EVIDENCE_ORDER` **維持不變**（保持純有序） | 同上 |
| 4 | `evidence_level` 改存 L0–L5、可為空、移除 `DEFAULT 'medium'`、移除 `causal_candidate` | `infra/db/migrations/`（新 migration）、`shared/domain/models.py:419`、`packages/schemas/canonical/index.ts:358` |
| 5 | 既有記錄回填為 NULL | 同上 migration |
| 6 | `evidence_assessable = false` 時顯示 `INSUFFICIENT_EVIDENCE` 與原因碼；依 `ODP-ML-05` §5 末句，此狀態下不得以「造成」「提升」等確定語氣描述 | 前端 |

### 可達成的驗收

`ODP-AC-BR-005`（Evidence 不足時不得顯示因果確定結論）在上述實作落地後方可驗收。

### 不變的部分

`CAUSAL_MIN_EVIDENCE = L3` 維持不變。本 ADR 不調整因果宣稱的門檻，僅釐清「無法定級」與「定級為最低」的差別。

## Alternatives Considered

**D1 乙 — 採用 SA-07 定義，實作改回。** 未採納：需重寫 `EvidenceLevel`、`_EVIDENCE_ORDER`、`CAUSAL_MIN_EVIDENCE` 與 AdLift 對外值，且失去 L5，未來實作自動化啟用門檻時仍須補回。

**D1 丙 — 另立第三套定義。** 未採納：會使既有實作、既有文件與既有評估紀錄同時失效，且不解決任何實質問題。衝突的成本在於有多套，不在於選了哪一套。

**D2 乙 — 保留雙層並定義明確轉換。** 未採納：見 Rationale。

**D2 丙 — 維持現狀。** 未採納：該欄位今日無人寫入，故無立即損害，但兩道 fail-open 都仍在 —— 欄位的 `DEFAULT 'medium'` 與 dbt 的 `else 0.7`。一旦開始寫入，未評級記錄會同時取得中等證據的儲存值與 0.7 的信心分數，並照常進入訓練資格。

**D3 乙 — 作為階梯最低一級。** 未採納：見 Rationale 的排序語意論證。

## Open Questions

裁決已定，下列問題屬實作細節，不阻擋本 ADR 生效，但需在對應實作 task 中解決：

1. **既有 `evidence_level` 記錄的數量與分佈。** 回填為 NULL 前需實際查詢。本 ADR 撰寫時三個環境均無工作負載運行，未執行任何線上查詢。
2. **`causal_candidate` 的原始意圖。** 判定為孤兒值（dbt 有讀取分支，無任何寫入者）而移除。若有紀錄顯示它對應某個未實作的業務概念，應補記於此 —— 移除已隨 `ODP-EVIDENCE-LEVEL-ALIGNMENT-001` 執行，回復需另開 task。
3. **四個 insufficiency 原因碼是否足夠。** 其中 `OVERLAPPING_TREATMENT` 與 `ODP-BR-AD-002`（重疊促銷需標記或排除）可能為同一判定的兩個名稱，需 Validation Owner 確認是否合併。
