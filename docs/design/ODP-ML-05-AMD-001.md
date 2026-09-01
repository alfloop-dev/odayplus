---
doc_id: ODP-ML-05-AMD-001
title: "因果推論與實驗設計修正案 001：Evidence Level 的可評估性"
version: 0.1.0
status: draft-for-review
document_class: ml-design-amendment
project: ODay Plus
language: zh-TW
updated_at: 2026-09-01
owner: "Validation Owner"
approvers: "Validation Owner / Architecture Owner / Product Lead"
content_format: markdown
amends: ODP-ML-05_CAUSAL_INFERENCE_AND_EXPERIMENT_DESIGN.md
change_class: C2
source_decision: ADR-0004
---

# 因果推論與實驗設計修正案 001：Evidence Level 的可評估性

## 1. 修正目的

`ADR-0004`（accepted，2026-09-01）以本文件第 5 節為 Evidence Level 的單一權威。本修正案補上該節缺少的一塊：**證據不可評估時如何表達**。

變更類別為 **C2（Non-breaking design）**：L0–L5 階梯本身一字不動，本案只新增與其正交的狀態。既有的因果門檻、級名與語意皆不受影響。

## 2. 背景：第 5 節少了什麼

本文件第 5 節定義 L0–L5，涵蓋「證據有多強」。它沒有涵蓋「證據強度無法判定」這個情況。

`ODP-SA-07` 曾以自己的階梯處理它 —— 在同一個列舉裡放一個 `INSUFFICIENT_EVIDENCE` 成員。`ADR-0004` 裁定該作法不可行（理由見該 ADR 與 `ODP-SA-07-AMD-001` 第 3.3 節），並以本文件的階梯為準；因此表達「不可評估」的責任落到本節。

實作層面的證據：`assess_evidence()` 在無處置資料時原本回傳 `L0_ANECDOTAL`。那是錯的陳述 —— `L0` 宣稱有觀察且經評級，而實際情況是沒有東西可讀。活動既不能被稱為有效，也不能被稱為無效。

## 3. 第 5 節新增內容

於現行 L0–L5 表格之後、末句規範之前，新增下列內容：

> ### 5.1 可評估性
>
> Evidence Level 只在證據可被評級時適用。當定級的最低條件不成立時，**不得**以階梯的任一級表達，包括 `L0` 在內 —— `L0` 表示「已觀察但僅軼事等級支持」，與「無從判讀」是不同的陳述。
>
> 判定輸出為：
>
> | 欄位 | 語意 |
> |---|---|
> | `evidence_assessable` | 是否具備定級的最低條件 |
> | `evidence_level` | `L0`–`L5`；`evidence_assessable = false` 時為空 |
> | `insufficiency_reason_code` | `evidence_assessable = false` 時必填 |
>
> 對外呈現時，`evidence_assessable = false` 一律顯示為 `INSUFFICIENT_EVIDENCE`（`ODP-BR-AD-004`）。
>
> ### 5.2 Insufficiency 原因碼
>
> | 原因碼 | 語意 | 判定來源 |
> |---|---|---|
> | `NO_TREATMENT_DATA` | 觀察期內無任何處置門市資料 | `assess_evidence()` |
>
> 清單刻意只納入**具備判定路徑**者。下列情況曾被提議為原因碼，但不屬於此處：
>
> | 情況 | 實際歸屬 | 理由 |
> |---|---|---|
> | 無對照組 | `L1` Before/After | 前後比較是一個真實的（雖然弱的）讀數，不是讀不出來 |
> | 處置重疊／污染 | `L2` Matched Descriptive | 活動仍可量測；失敗的是因果宣稱，而非量測本身 |
> | 樣本過小 | 暫不納入 | 本文件與實作皆未定義最小樣本門檻。門檻確立後方可加入 |
> | 資料品質失敗 | 暫不納入 | 目前無資料品質訊號進入評估函式。訊號接通後方可加入 |
>
> 新增原因碼時**必須**同時提供其判定路徑。一個宣告了卻無人能產生的原因碼，與階梯上一個無人能產生的等級同樣有害：`causal_candidate` 即以此形式在 canonical schema 中存活數月而未被察覺（`ODP-EVIDENCE-LEVEL-ALIGNMENT-001`）。

## 4. 既有規範的延伸

本文件第 5 節末句原文為：

> 系統前端必須顯示 Evidence Level，Evidence 不足時不得以「造成」、「提升」等確定語氣描述。

該句維持不變，並明確其適用範圍：「Evidence 不足」包含兩種情形 —— `evidence_assessable = false`（不可評估），以及 `evidence_level` 低於 `CAUSAL_MIN_EVIDENCE`（可評估但未達因果門檻）。兩者皆不得使用確定語氣。

## 5. 已落地的實作

| 變更 | 位置 | Task |
|---|---|---|
| `EvidenceAssessment`、`EvidenceInsufficiencyReason` | `modules/adlift/domain/incrementality.py` | `ODP-EVIDENCE-ASSESSABILITY-001` |
| `assess_evidence()` 取代 `assign_evidence_level()` | 同上 | 同上 |
| `is_causal_evidence()` 於不可評估時短路，不進入序數比較 | 同上 | 同上 |
| `_EVIDENCE_ORDER` 維持純有序，未新增成員 | 同上 | 同上 |

`CAUSAL_MIN_EVIDENCE = L3` 未變更。本修正案不調整因果宣稱的門檻。

## 6. 未涵蓋事項

`ODP-SA-07` 第 6 節需同步改為引用本節而不再自行定義階梯，見 `ODP-SA-07-AMD-001`。兩份修正案須一併核准。
