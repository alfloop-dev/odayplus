---
doc_id: ODP-SA-07-AMD-001
title: "業務規則與決策政策修正案 001：Evidence Level 定義歸屬"
version: 0.1.0
status: draft-for-review
document_class: system-analysis-amendment
project: ODay Plus
language: zh-TW
updated_at: 2026-09-01
owner: "Product Lead"
approvers: "Domain Business Owners / Architecture Owner / Data-Service Owner"
content_format: markdown
amends: ODP-SA-07_BUSINESS_RULES_AND_DECISION_POLICIES.md
change_class: C3
source_decision: ADR-0004
---

# 業務規則與決策政策修正案 001：Evidence Level 定義歸屬

## 1. 修正目的

`ADR-0004`（`docs/adr/ADR-0004-evidence-level-single-authority.md`，status: accepted，2026-09-01）裁定 Evidence Level 以 `ODP-ML-05` 第 5 節為單一權威。本修正案執行該裁決在 `ODP-SA-07` 這一側的後果：移除本文件自行定義的階梯，並修訂 `ODP-BR-AD-004` 的表述使其可被實作滿足。

變更類別依 `ODP-00-04` 判定為 **C3（Breaking）**：本案移除一份正式交付文件既有的列舉定義，並改變一條 Policy 級業務規則（`ODP-BR-AD-004`）的判定介面。依 C3 規則需業務 owner、架構 owner 與資料／服務 owner 三方核准。

## 2. 背景：為何本文件的定義要讓位

`ODP-SA-07` 第 6 節與 `ODP-ML-05` 第 5 節對同一個階梯給出不相容的定義，兩份同為 `formal_deliverable`，位階無法自動判定。裁決依據不是批次先後，而是**實作實況**：

盤點 `origin/dev` 的 130 個 `evidence_level` 使用點後，三套值並存且彼此無轉換。其中只有 ML-05 那套有產生者與消費者（`modules/adlift/domain/incrementality.py` 計算、AdLift API 輸出），並有 `CAUSAL_MIN_EVIDENCE = L3` 這個實際用來阻擋因果宣稱的門檻 —— 也就是 `ODP-BR-AD-001`（Hard Constraint）的執行點。本文件第 6 節那套沒有任何實作。

完整分析見 `ADR-0004` Context 一節。

## 3. 第 6 節修訂：Evidence Level

### 3.1 原內容

原第 6 節以表格自行定義六個等級：`L0_OBSERVED`、`L1_ADJUSTED`、`L2_MATCHED_CONTROL`、`L3_DID_VALIDATED`、`L4_EXPERIMENTAL`、`INSUFFICIENT_EVIDENCE`。

### 3.2 修訂後內容

> ## 6. 干預效果 Evidence Level
>
> Evidence Level 的權威定義為 `ODP-ML-05` 第 5 節，本文件不另行定義。該階梯為 `L0`–`L5` 六級有序尺度，因果宣稱的最低門檻為 `L3`（見 `ODP-BR-AD-001`）。
>
> **`INSUFFICIENT_EVIDENCE` 不是該階梯的一級。** 它與階梯正交：階梯衡量證據強度，`INSUFFICIENT_EVIDENCE` 表示尺規不適用 —— 資料不足以支持任何方向的結論，**包含「無效」在內**。
>
> 判定輸出為三個欄位：
>
> | 欄位 | 語意 |
> |---|---|
> | `evidence_assessable` | 是否具備定級的最低條件 |
> | `evidence_level` | `L0`–`L5`；`evidence_assessable = false` 時為空 |
> | `insufficiency_reason_code` | `evidence_assessable = false` 時必填 |
>
> 對外呈現時，`evidence_assessable = false` 一律顯示為 `INSUFFICIENT_EVIDENCE`。
>
> 原因碼的權威清單見 `ODP-ML-05` 第 5 節。清單刻意只納入具備判定路徑者：一個沒有產生路徑的原因碼，與階梯上一個沒有產生路徑的等級同樣有害。

### 3.3 為何不把 `INSUFFICIENT_EVIDENCE` 併入階梯

這是原定義的核心缺陷，且在實作層是硬約束。`_EVIDENCE_ORDER` 是有序 tuple，`is_causal_evidence()` 以 `index()` 比較大小。任何置入該 tuple 的成員都會取得序數並參與比較 —— 放在 `L0` 之前意味著「比軼事更弱的證據」，而其真實語意是「這把尺不適用」。

兩者是不同的陳述：`L0` 宣稱有觀察但僅軼事等級支持；`INSUFFICIENT_EVIDENCE` 宣稱讀不出結果。以有序尺度上的一點表達「不可評估」，會使下游把未定級當作一個真實的低等級處理。

## 4. 第 3 節修訂：`ODP-BR-AD-004`

### 4.1 原條文

> | `ODP-BR-AD-004` | AdLift | Evidence 不足需輸出 `INSUFFICIENT_EVIDENCE`。 | Policy | API／Workflow／Decision Service／UI | Audit Log + Policy Version |

該表述在原定義下無法實作：`INSUFFICIENT_EVIDENCE` 不存在於 `ODP-ML-05` 的階梯，而實作採用的是後者。

### 4.2 修訂後條文

> | `ODP-BR-AD-004` | AdLift | Evidence 不足時 `evidence_assessable` 必須為 false，必須記錄 `insufficiency_reason_code`，且對外必須呈現為 `INSUFFICIENT_EVIDENCE`。 | Policy | API／Workflow／Decision Service／UI | Audit Log + Policy Version |

字面要求（對外顯示 `INSUFFICIENT_EVIDENCE`）不變，改變的是它由哪個機制承擔。

## 5. 驗收條件影響

| 驗收 ID | 原狀態 | 修訂後 |
|---|---|---|
| `ODP-AC-BR-005` | 無法達成 —— 判定所需的值不存在於實作採用的階梯 | 可驗收：`evidence_assessable = false` 時 UI 不得出現因果確定語氣，且原因碼可稽核 |

## 6. 對應實作

本修正案描述的機制已寫成程式碼，非提案：

| 變更 | Task | 交付 PR | 併入狀態（2026-09-01 快照） |
|---|---|---|---|
| `EvidenceAssessment`、`EvidenceInsufficiencyReason`、`assess_evidence()`、`is_causal_evidence()` 短路 | `ODP-EVIDENCE-ASSESSABILITY-001` | #1095 | 未併入 |
| 持久化層對齊 L0–L5、移除 `DEFAULT 'medium'` 與孤兒值 `causal_candidate` | `ODP-EVIDENCE-LEVEL-ALIGNMENT-001` | #1094 | **已併入 `dev`**（merge commit `4a14d52d`） |
| 本修正案的裁決依據 `ADR-0004` | `ODP-EVIDENCE-LEVEL-ADR-001` | #1090 | 未併入 |

PR #1094 已在 `dev` 上：`infra/db/migrations/000013_evidence_level_alignment.sql` 移除了 `evidence_level` 的 `DEFAULT 'medium'` 與 `NOT NULL`，`packages/schemas/canonical` 已無 `causal_candidate`。第 3.2 節要求 `evidence_assessable = false` 時 `evidence_level` 為空，其持久化層前提（欄位可為 NULL、且無 fail-open 預設值）因此在 `dev` 上已成立。

PR #1095 與 PR #1090 尚未併入，因此 `dev` 上還沒有 `assess_evidence()`，也還沒有 `ADR-0004` 本身。這兩項請以 `task/ODP-EVIDENCE-ASSESSABILITY-001` 與 `task/ODP-EVIDENCE-LEVEL-ADR-001` 查核，不要以 `dev` 查核。本修正案不應早於 `ADR-0004` 併入。

上表是快照，會隨合併而過期。查核時以 `gh pr view <PR>` 的當下結果為準，不以本文件為準。

文件在此**跟隨**實作而非領先。這是刻意的：本次衝突的成因，正是規格層先行定義而實作各自為政，最終產生三套並存的值。

## 7. 未涵蓋事項

`ODP-ML-05` 第 5 節需同步補上正交狀態與原因碼清單，見 `ODP-ML-05-AMD-001`。兩份修正案須一併核准，單獨採納任一份會使兩份文件再度分歧。
