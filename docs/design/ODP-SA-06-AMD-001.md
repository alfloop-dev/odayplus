---
doc_id: ODP-SA-06-AMD-001
title: "功能需求規格書修正案 001"
version: 0.2.0
status: draft-for-review
document_class: system-analysis-amendment
project: ODay Plus
language: zh-TW
updated_at: 2026-09-01
owner: "Product Lead"
approvers: "Domain Business Owners / Architecture Owner / QA Lead"
content_format: markdown
amends: ODP-SA-06_FUNCTIONAL_REQUIREMENTS_SPECIFICATION.md
change_class: C2
source_gap: ODP-GAP-FR-20260901
---

# 功能需求規格書修正案 001

## 1. 修正目的與範圍

本修正案處理 `ODP-GAP-FR-20260901` 所列 9 項落差中，**需要修改需求條文**的 5 項。其餘 4 項（`ODP-FR-FCT-006`、`ODP-FR-AVM-005`、`ODP-FR-AVM-008`、`ODP-FR-NET-007`）的需求敘述已足夠明確，落差僅在設計與實作層，改由 `ODP-SD-AMD-001` 處理。

修正原則：既有 FR ID 一律保留，不重用、不刪除。原條文過於簡略而無法驗收者，補齊 Trigger/Input 與 Output/Acceptance；語意不完整者，改寫並標註 supersedes。

變更類別依 `ODP-00-04` 判定為 **C2（Non-breaking design）**：不改變已核准的模組邊界與資料契約方向，但擴充驗收條件，故需 Product、Architecture 與 QA 三方審查，並同步更新測試與 RTM。

## 2. 本修正案的共同背景

`ODP-MOD-01` 至 `ODP-MOD-11` 各模組設計文件的功能表以「編號 + 功能描述 + Priority + FR ID」四欄呈現，其中功能描述多為 2 至 6 字的短語（例如「Top-K 熱區推薦」、「Feedback 修正」）。這些短語在 `ODP-SA-06` 主表中沒有對應的展開條文，導致該批 FR 缺少可驗收的輸入與輸出定義。

本修正案為其中 3 條補上展開條文（`FCT-008`、`HZ-006`、`PRICE-006`），並修訂 2 條語意不足者（`FCT-005`、`HZ-004`）。同一問題仍存在於其餘 30 條 MOD 延伸 FR，建議後續以獨立修正案統一處理。

---

## 3. 修訂條文

### 3.1 `ODP-FR-FCT-008` — Feedback 修正

**原條文**（`ODP-MOD-04` 功能表第 8 項）：

> | 8 | Feedback 修正 | MUST | ODP-FR-FCT-008 |

**修訂後條文**：

| FR ID | Requirement | Primary Module | Trigger/Input | Output/Acceptance | Priority | Trace |
|---|---|---|---|---|---|---|
| `ODP-FR-FCT-008` | 系統必須讓具權限的營運人員對已產生的預測或警示提交結構化回饋，回饋須註記類型、理由、影響期間與提交者，並在後續預測中依已核准的處理規則生效或明確標示未生效。 | ForecastOps | 操作者於警示或預測結果上提交回饋 | 回饋持久化且可稽核；後續預測輸出須可查詢其是否受回饋影響；回饋未生效時須有原因碼 | MUST | `ODP-HLR-FCT-*` |

**新增條文說明**：

回饋類型至少須涵蓋三類，其處理路徑不同，不得混為一談：

| 類型 | 語意 | 對模型的作用 |
|---|---|---|
| `CONTEXT_ANNOTATION` | 標註已知外部因素（裝修、周邊施工、鄰店歇業） | 不修改預測值；作為排除區間，使該期間不進入訓練集與 Precision 計算 |
| `OUTCOME_CORRECTION` | 修正系統取得的實績有誤 | 需 Data Owner 核准；核准後修正 canonical 資料並觸發重算 |
| `ALERT_DISPOSITION` | 判定警示為誤報或已處理 | 不修改預測值；作為 Precision 計算的標記，並關閉警示 |

**約束**：

1. 回饋**不得**直接寫入預測值或決策欄位（依 `ODP-BR-GOV-001`）。回饋是獨立記錄，其對模型的作用透過訓練集篩選或重算達成，不是覆寫。
2. `OUTCOME_CORRECTION` 屬 Learning Policy 層級，須依 `ODP-SA-07` 第 2 節由 Data／Model Owner 核准後生效。
3. 在本需求實作完成前，UI **不得**呈現任何宣稱回饋已被處理的文案。

---

### 3.2 `ODP-FR-HZ-006` — 相鄰熱區合併與拆分

**原條文**（`ODP-MOD-01` 功能表第 6 項）：

> | 6 | 相鄰熱區合併與拆分 | MUST | ODP-FR-HZ-006 |

**修訂後條文**：

| FR ID | Requirement | Primary Module | Trigger/Input | Output/Acceptance | Priority | Trace |
|---|---|---|---|---|---|---|
| `ODP-FR-HZ-006` | 系統必須能將需求連續的相鄰空間單元合併為單一熱區，並將內部異質性超過門檻的熱區拆分；合併與拆分結果須保留來源單元清單、判定依據與可逆的稽核軌跡。 | HeatZone Radar | 批次評分完成後，依鄰接關係與相似度門檻評估 | 合併熱區具備獨立識別碼與其組成單元清單；拆分後各子熱區可追溯至原熱區；任一合併或拆分均可回溯與撤銷 | MUST | `ODP-HLR-HZ-*` |

**新增條文說明**：

1. **鄰接定義**：以 H3 的 k-ring（k=1）為預設鄰接關係。跨行政區界的相鄰單元是否可合併，屬政策決定，須由 Decision Policy 控制而非硬編。
2. **合併條件**：相鄰、且需求特徵相似度高於門檻、且合併後不跨越已設定的不可合併邊界。門檻為政策值。
3. **拆分條件**：單一熱區內部的需求或租金離散度超過門檻，使其平均分數不具代表性。
4. **治理歸屬**：合併與拆分屬 Ranking Policy 層級（`ODP-SA-07` 第 2 節），可調整權重版本，但**不得無痕覆寫**。自動合併的結果須可由展店 Owner 人工推翻，推翻須留理由與責任人。
5. 合併熱區的識別碼**不得**重用任一組成單元的識別碼，避免下游將合併體誤認為原單元。

---

### 3.3 `ODP-FR-PRICE-006` — Bandit 框架受 Gate 控制啟用

**原條文**：

> | `ODP-FR-PRICE-006` | 系統必須Bandit 框架受 Gate 控制啟用。 | PriceOps | … | MUST | `ODP-HLR-PRICE-*` |

**修訂後條文**：

| FR ID | Requirement | Primary Module | Trigger/Input | Output/Acceptance | Priority | Trace |
|---|---|---|---|---|---|---|
| `ODP-FR-PRICE-006` | 系統必須將線上探索（Bandit）實作為受 Gate 控制的可選能力：未通過 Gate 時不得產生任何探索性價格；通過 Gate 後，探索範圍須受硬限制、探索預算與時間窗約束，且每次探索決策須記錄其所依據的 Gate 授權。 | PriceOps | Gate 授權存在且未過期時，由價格最佳化流程觸發 | 未授權時系統輸出確定性方案且明確標示未啟用探索；已授權時每個探索性價格可追溯至授權紀錄、探索預算餘額與適用硬限制 | MUST | `ODP-HLR-PRICE-*` |

**新增條文說明**：

1. **交付綁定**：Bandit 機制與其 Gate **必須作為同一交付單元**，不得分階段上線。若先實作探索能力而後補 Gate，`ODP-BR-PRICE-004`（Hard Constraint）在期間內即為破口。
2. **Gate 授權內容**：至少須包含適用範圍（租戶、品牌、品項、門市群）、探索預算上限、有效期限、核准者與回滾條件。
3. **硬限制不可放寬**：探索**不得**成為繞過 `ODP-BR-PRICE-001`（毛利底線）的路徑。探索空間是硬限制內的子集，不是其例外。
4. **Tier 歸屬**：依 `ODP-SA-08` 第 12 節，Bandit 屬 Tier 4，Feature Flag 關閉時不得影響核心定價流程。

---

### 3.4 `ODP-FR-FCT-005` — 由 Decision Policy 產生四燈

**原條文**：

> | `ODP-FR-FCT-005` | 系統必須由 Decision Policy 產生四燈。 | ForecastOps | … | MUST | `ODP-HLR-FCT-*` |

**修訂後條文**：

| FR ID | Requirement | Primary Module | Trigger/Input | Output/Acceptance | Priority | Trace |
|---|---|---|---|---|---|---|
| `ODP-FR-FCT-005` | 系統必須由具版本的 Decision Policy 物件產生四燈：燈號門檻與輸入權重須為政策資料而非程式常數，政策變更須升版並保留舊版，且每個已產生的警示必須永久保存其判定時所使用的 `policy_id` 與 `policy_version`。 | ForecastOps | 預測結果產出後，以生效中的政策版本評估 | 警示記錄含政策識別與版本；同一預測結果以不同政策版本評估可得不同燈號且兩者皆可重現；政策門檻調整不需變更程式碼 | MUST | `ODP-HLR-FCT-*`、`ODP-BR-FCT-001` |

**新增條文說明**：

1. **輸入完整性**：`ODP-SA-07` 第 5 節要求四燈政策輸入至少包含預測殘差、Prediction Interval、SiteScore Gap、成本異常、設備可用率、客服主題、維修工單、外部因素、重疊干預與資料品質共十項。現行實作僅使用 SiteScore Gap 一項，**不符本需求**。政策物件須能宣告其實際使用的輸入子集，未使用的輸入須明示為未納入，不得隱含。

   十項輸入不要求一次到齊：政策物件的價值正在於輸入清單可隨版本擴充。本修正案要求的是**宣告的誠實性**——政策必須列出它實際讀取的輸入，讀者由該清單即可知道尚有八項未納入，而不需閱讀程式碼才能發現。

2. **與資料品質的關係**：依 `ODP-BR-FCT-003`，資料 Stale 時不得產生高信心警示。政策評估**必須**將資料品質作為輸入，而非在政策之外另行判斷。

   此項為**第一版政策即須滿足**的條件，不得延後至後續升版：若首版政策不宣告資料品質為輸入，則該政策自上線之日起即違反 `ODP-BR-FCT-003`，機制上線反而使違規取得了政策外觀。因此第一版政策的 `declared_inputs` 至少為 SiteScore Gap 與資料品質兩項，其餘八項標記為未納入。`ODP-SD-AMD-001` 第 4.1 節據此定義政策評估內容，第 11 節定義首版政策列的實際欄位值。
3. **回溯性**：政策升版**不得**改寫既有警示的燈號。歷史警示保留其原判定與原政策版本。

---

### 3.5 `ODP-FR-HZ-004` — 依新店實績更新需求吸收與剩餘需求

**原條文**：

> | `ODP-FR-HZ-004` | 系統必須依新店實績更新需求吸收與剩餘需求。 | HeatZone Radar | … | MUST | `ODP-HLR-HZ-*` |

**修訂後條文**：

| FR ID | Requirement | Primary Module | Trigger/Input | Output/Acceptance | Priority | Trace |
|---|---|---|---|---|---|---|
| `ODP-FR-HZ-004` | 系統必須在熱區內有新店開業並累積實績後，依實績計算該熱區已被吸收的需求量與剩餘未滿足需求，並以剩餘需求重新評分；吸收計算須使用實績而非預測，且須保留計算依據與時點。 | HeatZone Radar | 熱區內門市開業滿觀察期且實績可用時觸發重算 | 熱區輸出含已吸收需求、剩餘需求與其計算基準時點；已飽和熱區的排名須隨吸收量下降；吸收計算所用的實績來源可追溯 | MUST | `ODP-HLR-HZ-*`、`ODP-BR-HZ-004` |

**新增條文說明**：

1. **實績而非預測**：吸收量**必須**以已實現的營收或客流計算。使用 SiteScore 預測值計算吸收會造成自我實現的循環 —— 預測高的熱區被判定吸收多，因而降低排名，而該預測從未被驗證。
2. **觀察期**：新店開業後須滿足最低觀察期才可用於吸收計算，避免以爬坡期（Ramp）實績低估吸收量。觀察期為政策值，與 `ODP-FR-SITE-002` 的 M1/M3/M6/M12 里程碑對齊。
3. **與稀釋的一致性**：本需求的剩餘需求輸出，須與 `ODP-FR-SITE-003` 的稀釋指標使用同一組實績基礎。兩者不得各自計算而給出矛盾結論。
4. **狀態轉移**：`PARTIALLY_ABSORBED` 與 `SATURATED` 的狀態轉移須由吸收計算驅動，並依 `ODP-BR-HZ-001` 只能經核准狀態機轉移。

---

## 4. 新增驗收條件

本修正案為 `ODP-SA-06` 第 5 節新增下列驗收條件：

| 驗收 ID | 內容 | 對應 FR |
|---|---|---|
| `ODP-AC-FR-008` | 對同一預測結果套用兩個不同版本的四燈政策，可產生不同燈號，且兩次判定各自保存其 `policy_id` 與 `policy_version`。 | `FCT-005` |
| `ODP-AC-FR-009` | 提交一筆 `CONTEXT_ANNOTATION` 回饋後，該期間不再進入後續訓練集與 Precision 計算，且回饋本身可稽核。 | `FCT-008` |
| `ODP-AC-FR-010` | 熱區內新店累積實績後重新評分，該熱區的剩餘需求下降且排名隨之下降，計算所用實績可追溯。 | `HZ-004` |
| `ODP-AC-FR-011` | 兩個需求連續的相鄰單元合併後，其組成單元清單可查，且合併可被人工推翻並留下理由與責任人。 | `HZ-006` |
| `ODP-AC-FR-012` | Gate 未授權時，價格最佳化不產生任何探索性價格，且輸出明確標示探索未啟用。 | `PRICE-006` |

## 5. 對既有文件的影響

| 文件 | 影響 |
|---|---|
| `ODP-SA-07` | 第 5 節四燈政策輸入清單成為 `FCT-005` 的驗收依據，需確認十項輸入的必要性分級（必備／可選） |
| `ODP-MOD-01` | 功能表第 4、6 項的短語描述由本修正案的展開條文取代 |
| `ODP-MOD-04` | 功能表第 5、8 項同上 |
| `ODP-MOD-06` | 功能表 Bandit 相關項同上 |
| `ODP-00-05` | 需新增 5 條 `ODP-AC-FR-*` 的追蹤列 |
| `ODP-QA-03` | 需為新增驗收條件補 E2E 情境 |

## 6. 未處理事項

下列問題在對照過程中被發現，但超出本修正案範圍，需獨立處理：

1. **`ODP-SA-07` 第 6 節與 `ODP-ML-05` 第 5 節對 Evidence Level 給出兩套不相容定義**，實作已採用 ML-05 版本。`INSUFFICIENT_EVIDENCE` 在 ML-05 中不存在，致使 `ODP-BR-AD-004` 與 `ODP-AC-BR-005` 無法達成。此為 C3 級衝突，需 ADR 裁定何者為準。
2. **其餘 30 條 MOD 延伸 FR 同樣缺少可驗收的展開條文**，建議以統一修正案處理，避免逐條發現、逐條補寫。
3. **`ODP-SA-07` 第 8 節的 `rollback_policy_version` 與 `change_reason` 兩個必填欄位在產品根目錄零命中**（計數與範圍定義見 `ODP-GAP-FR-20260901` 第 2 節與 4.4），影響範圍不限於四燈，屬平台級落差。
