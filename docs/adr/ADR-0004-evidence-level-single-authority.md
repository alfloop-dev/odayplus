---
adr_id: ADR-0004
title: "Evidence Level 的單一權威定義與 INSUFFICIENT_EVIDENCE 的歸屬"
version: 0.1.0
status: proposed
document_class: architecture-decision-record
project: ODay Plus
language: zh-TW
decision_date: null
updated_at: 2026-09-01
owner: "Architecture Owner"
approvers: "Validation Owner / Product Lead / Technology Lead"
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

兩份正式交付文件對同一個 Evidence Level 階梯給出不相容的定義：

| `ODP-SA-07` 第 6 節（第 2 批） | `ODP-ML-05` 第 5 節（第 6 批） |
|---|---|
| `L0_OBSERVED` | `L0` Anecdotal |
| `L1_ADJUSTED` | `L1` Before/After |
| `L2_MATCHED_CONTROL` | `L2` Matched Descriptive |
| `L3_DID_VALIDATED` | `L3` DiD Validated |
| `L4_EXPERIMENTAL` | `L4` Randomized／Staggered |
| `INSUFFICIENT_EVIDENCE` | `L5` Replicated／Policy Ready |

實作 `modules/adlift/domain/incrementality.py` 逐字採用了 ML-05 版本，其註解亦明寫依據為 `ODP-ML-05 §5`。也就是說，實作已經單方面替這個衝突做了選擇，而規則層仍在引用另一套。

後果不只是命名不一致。`INSUFFICIENT_EVIDENCE` 在 ML-05 的階梯中不存在，而 `ODP-BR-AD-004` 明文要求「Evidence 不足需輸出 `INSUFFICIENT_EVIDENCE`」，`ODP-AC-BR-005` 亦以它為驗收條件。目前這兩項**無法達成**。

依 `ODP-00-04`，兩份文件同為 `formal_deliverable` 且狀態均為 `draft-for-review`，衝突無法以批次先後或文件位階自動解決，故需 ADR 裁定。

本 ADR 的狀態為 `proposed`；下述 Decision 是待核准的裁決建議，不是已生效的規範。最終裁決權屬 Architecture Owner 與 Validation Owner，本任務不代替任一方核准，也不在核准前改寫來源文件或實作。

## Decision

**一、以 `ODP-ML-05` 第 5 節的六級階梯（L0–L5）為 Evidence Level 的單一權威定義。**

**二、`INSUFFICIENT_EVIDENCE` 不是階梯的一級，而是與階梯正交的獨立狀態。**

Evidence Level 承載兩種不同的判斷，現行的 SA-07 定義把它們壓在同一個列舉裡：

- **證據強度**（L0–L5）是一把有序尺規：L3 強於 L2，「可作為中等信心因果估計」強於「具參考性的增量估計」。
- **`INSUFFICIENT_EVIDENCE`** 不在這把尺上。依 SA-07 自己的定義，它表示「樣本、對照、重疊或資料品質不足 → 不得宣稱有效或無效」—— 這是**尺規不適用**，不是尺規的最低點。

兩者語意不同：`L0_ANECDOTAL` 是「觀察到了，但只有軼事等級的支持」；`INSUFFICIENT_EVIDENCE` 是「資料品質不足以支持任何方向的結論，包括無效」。把一個宣稱「有觀察」的等級用來表達「不能下任何結論」會誤導決策者。

實作上這個區分是硬性的。`modules/adlift/domain/incrementality.py` 的 `_EVIDENCE_ORDER` 是一個有序 tuple，`is_causal_evidence()` 以 `_EVIDENCE_ORDER.index()` 做大小比較：

```python
def is_causal_evidence(level: EvidenceLevel) -> bool:
    return _EVIDENCE_ORDER.index(level) >= _EVIDENCE_ORDER.index(CAUSAL_MIN_EVIDENCE)
```

若把 `INSUFFICIENT_EVIDENCE` 塞進這個 tuple，它就會取得一個序數並參與比較 —— 放在 L0 之前意味著「比軼事還弱」，這在語意上錯誤且會使任何「未定級」被當作一個真實的低等級處理。

**三、因此，證據判定的輸出為一組兩欄位，而非單一列舉值。**

| 欄位 | 型別 | 語意 |
|---|---|---|
| `evidence_assessable` | boolean | 是否具備定級的最低條件 |
| `evidence_level` | `L0`–`L5`，`evidence_assessable = false` 時為 null | 證據強度 |
| `insufficiency_reason_code` | `evidence_assessable = false` 時必填 | `SAMPLE_TOO_SMALL`／`NO_CONTROL`／`OVERLAPPING_TREATMENT`／`DATA_QUALITY_FAIL` |

對外呈現時，`evidence_assessable = false` 一律顯示為 `INSUFFICIENT_EVIDENCE`，滿足 `ODP-BR-AD-004` 的字面要求，同時不污染階梯的有序語意。

## Rationale

**為何選 ML-05 而非 SA-07：**

1. **實作已對齊**。改動成本不對稱：採用 ML-05 只需補上 `INSUFFICIENT_EVIDENCE` 的正交表達；採用 SA-07 需重寫既有列舉、`_EVIDENCE_ORDER`、`CAUSAL_MIN_EVIDENCE` 與所有下游判斷。
2. **ML-05 的級名描述方法，SA-07 的級名描述處理**。`Matched Descriptive` 指明「有對照但 pre-trend 未通過」這個具體方法狀態；`L2_MATCHED_CONTROL` 只說有對照，未涵蓋 pre-trend 是否通過 —— 而後者正是 `ODP-BR-AD-001`（Hard Constraint）的判定依據。
3. **ML-05 有 L5，SA-07 沒有**。`Replicated／Policy Ready`（多次重複、跨區域穩定、通過安全門檻）是自動化政策規則的前置條件。`ODP-FR-PRICE-006` 的 Bandit 與其他自動化能力若要以證據強度為啟用門檻，需要這一級。SA-07 的階梯止於 L4，無法表達「可用於自動化」。
4. **ML-05 是該主題的專責文件**。因果推論與實驗設計是 ML-05 的正題，SA-07 的正題是業務規則；同一概念的定義應以專責文件為準。

**為何不折衷成新的第三套定義**：任何新編號都會使既有實作、既有文件與既有評估紀錄同時失效，且不解決任何實質問題。衝突的成本在於有兩套，不在於選了哪一套。

## Consequences

**需修訂的文件：**

| 文件 | 修訂內容 |
|---|---|
| `ODP-SA-07` §6 | 整節改為引用 `ODP-ML-05` §5，不再自行定義階梯；補充 `INSUFFICIENT_EVIDENCE` 作為正交狀態的說明 |
| `ODP-SA-07` §3（`ODP-BR-AD-004`） | 表述由「輸出 `INSUFFICIENT_EVIDENCE`」改為「`evidence_assessable = false` 時，對外呈現為 `INSUFFICIENT_EVIDENCE` 並記錄 `insufficiency_reason_code`」 |
| `ODP-ML-05` §5 | 補上 `INSUFFICIENT_EVIDENCE` 的正交定義與四個原因碼 |

**需變更的實作：**

1. `modules/adlift/domain/incrementality.py` — 新增 `evidence_assessable` 與 `insufficiency_reason_code`；`_EVIDENCE_ORDER` **不變**（保持純有序）。
2. `is_causal_evidence()` — 在 `evidence_assessable = false` 時直接回傳 `False`，不進入序數比較。
3. 前端 — `evidence_assessable = false` 時顯示 `INSUFFICIENT_EVIDENCE` 與原因碼；依 `ODP-ML-05` §5 末句，此狀態下不得以「造成」「提升」等確定語氣描述。

**可達成的驗收：** `ODP-AC-BR-005`（Evidence 不足時不得顯示因果確定結論）在本 ADR 落地後方可驗收。

**不變的部分：** `CAUSAL_MIN_EVIDENCE = L3` 維持不變。本 ADR 不調整因果宣稱的門檻，只釐清「無法定級」與「定級為最低」的差別。

## Alternatives Considered

**甲、採用 SA-07 定義，實作改回。** 否決：改動成本高於效益，且會失去 L5 與 `Matched Descriptive` 的方法精確性。

**乙、把 `INSUFFICIENT_EVIDENCE` 加為 ML-05 階梯的第七級（置於 L0 之前）。** 否決：它會取得序數並參與 `_EVIDENCE_ORDER` 比較，語意上等同宣告「比軼事更弱的證據」，而其實際語意是「不可評估」。這正是 SA-07 原設計的缺陷，複製它不會改善。

**丙、維持現狀，由實作自行決定。** 否決：現狀下 `ODP-BR-AD-004` 與 `ODP-AC-BR-005` 永久無法達成，且規則層與實作層的分歧會在每次審查時重複浮現。

## Open Questions

1. 四個 `insufficiency_reason_code` 是否足夠？特別是「重疊處置」（`OVERLAPPING_TREATMENT`）與 `ODP-BR-AD-002`（重疊促銷需標記或排除）的關係，需 Validation Owner 確認兩者是否為同一判定。
2. 既有已產生的評估紀錄如何回填？建議標記為 `evidence_assessable = null`（未經本 ADR 評估）而非追溯定級 —— 與 `ODP-SD-AMD-001` 第 9 節對政策回填的處理原則一致：歷史紀錄的歸屬不可偽造。
