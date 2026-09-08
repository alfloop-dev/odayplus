# NET-002 租約最小契約欄位字典 (Field Dictionary)

- **任務識別碼**：`ODP-NET002-LEASE-CONTRACT-PREP-001`
- **關聯需求**：`ODP-FR-NET-002`（硬限制：租約 LEASE）
- **關聯決策**：決策 D18（NET-002 LEASE 實作／補齊租約契約）、工作包 WP-32A
- **基準代碼 SHA**：`9048161e058becff5a53593a773d3c42238213fb`
- **生成時間**：2026-09-08T16:11:05Z
- **文件狀態**：A 階段工程準備草案（Draft Specification）
- **關聯產物**：
  - [JSON 契約草案](lease-contract-draft.json)
  - [Solver 驗收矩陣](solver-acceptance-matrix.md)
  - [H05 資料請求單](human-input-request-H05.md)
  - [實作交接計畫](implementation-handoff.md)
  - [總索引 README](README.md)

---

## 1. 概述與設計原則

本欄位字典定義 NetPlan 求解器在考量「租約（LEASE）」硬限制時所需的最小資料模型。

### 1.1 核心原則

1. **缺席 (Missing) 與量測為零 (Measured Zero) 嚴格區分**：
   - `None / NULL` 代表資料缺失（Unmeasured），在啟用租約約束時必須觸發 **Fail-Closed** 拒絕或標記為不可行。
   - `0.0` 代表經實質合約或協議確認之零金額（例如合約自然到期、房東無條件解約），為合法計算輸入。
   - 嚴禁程式碼層級預設 `exit_cost: float = 0.0`。
2. **敏感資訊隔離與脫敏**：
   - 原始紙本合約、房東個人身分證字號/銀行帳戶等 PII 資訊**禁止進入本資料庫與程式庫**。
   - 合約識別碼（`lease_contract_id`）採去識別化或雜湊代碼。
3. **雙側時間與財務視窗（MOVE 動作）**：
   - 搬遷動作必須同時約束「舊店解約時間與違約代價」及「新店起租時間與簽約截止日」。

---

## 2. 既有門市租約合約實體 (`StoreLeaseContract`)

對應目前運營中門市之合約狀態，為 `KEEP`, `IMPROVE`, `MOVE`, `EXIT` 動作之基礎依據。

| 欄位名稱 (`Field Name`) | 資料型態 (`Type`) | 必填／可空 (`Nullability`) | 單位／值域 (`Unit / Domain`) | 商業定義與語意說明 | 缺席 vs 量測為零處理規則 | 驗證規則與限制 | 敏感度等級 (`Data Class`) | 來源系統 (`Source System`) | 下游消費端對齊 (`Downstream Consumer`) |
|---|---|---|---|---|---|---|---|---|---|
| `store_id` | `VARCHAR(64)` | 必填 (NOT NULL) | `STR-[A-Z0-9_-]+` | 門市唯一識別碼，外鍵參照 `core.stores.store_id`。 | 不可為空；缺失則無法關聯門市。 | 正則表達式檢驗，必須存在於 `core.stores`。 | 內部公開 (INTERNAL) | ERP / Store Master | `modules/netplan/domain/planning.py::ExistingStoreInput.store_id` |
| `lease_contract_id` | `VARCHAR(128)` | 必填 (NOT NULL) | 字串識別碼 | 去識別化之合約編號或 CLM 系統主鍵。 | 不可為空；代表合約主檔存在。 | 長度 1-128 字元，禁止明文個人姓名。 | 內部機密 (CONFIDENTIAL) | CLM / ERP Contract Module | 稽核日誌與 ApprovalRecord 參照 |
| `lease_start_date` | `DATE` | 必填 (NOT NULL) | `YYYY-MM-DD` | 當前租期之正式生效起始日。 | 不可為空。 | 必須 `<=` `lease_expiry_date`。 | 內部公開 (INTERNAL) | CLM / ERP | `LeaseAdmissibilityChecker` 期間計算 |
| `lease_expiry_date` | `DATE` | 必填 (NOT NULL) | `YYYY-MM-DD` | 當前租約之契約到期日（屆期自然終止日）。 | 不可為空；若缺失則無法判定 KEEP/EXIT 合法性。 | 必須 `>=` `lease_start_date`。 | 內部受限 (RESTRICTED) | CLM / ERP | `LeaseAdmissibilityChecker` 自然終止與續約判定 |
| `monthly_rent` | `NUMERIC(12,2)` | 必填 (NOT NULL) | 新台幣 (TWD)，`>= 0.0` | 每月約定基礎租金。 | 不可為空；`0.0` 表示自有資產免租（需有相應標記）。 | 數值 `>= 0.0`。 | 內部機密 (CONFIDENTIAL) | ERP 財務模組 | 營運毛利試算與重疊租金試算 |
| `deposit_amount` | `NUMERIC(12,2)` | 必填 (NOT NULL) | 新台幣 (TWD)，`>= 0.0` | 押金總額（通常為 2-3 個月租金）。 | 不可為空；預設可為 `0.0`（若免押）。 | 數值 `>= 0.0`。 | 內部機密 (CONFIDENTIAL) | ERP 財務模組 | 提前解約沒收押金試算 |
| `break_clause_allowed` | `BOOLEAN` | 必填 (NOT NULL) | `true` / `false` | 是否具備提前解約條款（允許期前終止）。 | 若為 `false` 且未到期，EXIT / MOVE 判定為不可行或須經法務核准。 | 布林值。 | 內部受限 (RESTRICTED) | CLM | `LeaseAdmissibilityChecker` EXIT/MOVE 可行性檢驗 |
| `break_notice_months` | `INTEGER` | 必填 (NOT NULL) | 月數，`>= 0` | 提前解約法定/合約預告期（通常為 1-6 個月）。 | 若 `break_clause_allowed=true` 則必須 `>= 0`。 | 整數 `>= 0`。 | 內部受限 (RESTRICTED) | CLM | 規劃執行季度與預告期時序排程 |
| `early_termination_penalty` | `NUMERIC(12,2)` | 可為空 (Nullable) | 新台幣 (TWD)，`>= 0.0` | 提前解約應支付之具體違約罰金。 | **核心語意**：<br>1. `None`: 未知／未量測（Fail-Closed 阻擋求解）。<br>2. `0.0`: 經確認無罰金（合法計入預算）。 | 數值 `>= 0.0` 或 `NULL`。 | 內部機密 (CONFIDENTIAL) | CLM / 法務財務試算 | `ExistingStoreInput.exit_cost`、Solver 預算約束 |
| `restoration_cost_estimate` | `NUMERIC(12,2)` | 可為空 (Nullable) | 新台幣 (TWD)，`>= 0.0` | 原狀復原、拆除清運及交還工程預估費用。 | `None` 視為未量測；`0.0` 表示現況交還無復原義務。 | 數值 `>= 0.0` 或 `NULL`。 | 內部機密 (CONFIDENTIAL) | 工務部 / 營運部估價 | `ExistingStoreInput.exit_cost` 附加項目 |
| `renewal_option_flag` | `BOOLEAN` | 必填 (NOT NULL) | `true` / `false` | 契約是否享有優先續約權利。 | `false` 表示租約到期可能無法繼續營運。 | 布林值。 | 內部受限 (RESTRICTED) | CLM | KEEP 規劃跨越到期日之可行性依據 |
| `renewal_notice_deadline_days` | `INTEGER` | 可為空 (Nullable) | 日數，`>= 0` | 續約通知截止天數（到期前 N 天前須行使）。 | 若 `renewal_option_flag=true` 建議提供。 | 整數 `>= 0` 或 `NULL`。 | 內部受限 (RESTRICTED) | CLM | 營運待辦預警與排程 |
| `rent_escalation_cap_rate` | `NUMERIC(5,4)` | 可為空 (Nullable) | 比率，`0.0 - 1.0` | 續約時約定之租金最大調幅上限（如 0.03 代表 3%）。 | `None` 表示無上限或需重新議約。 | `0.0 <= x <= 1.0` 或 `NULL`。 | 內部機密 (CONFIDENTIAL) | CLM | 續約後 Baseline GM 修正 |
| `landlord_consent_status` | `VARCHAR(32)` | 必填 (NOT NULL) | `APPROVED`, `PENDING`, `REJECTED`, `UNKNOWN` | 房東就改裝、轉租或續約之正式意向狀態。 | `REJECTED` 阻擋 IMPROVE 或到期後 KEEP；`UNKNOWN` 依 fail-closed 告警。 | 列舉值限制。 | 內部受限 (RESTRICTED) | 租賃營運管理系統 | IMPROVE / KEEP 決策前置條件 |
| `alteration_permitted` | `BOOLEAN` | 必填 (NOT NULL) | `true` / `false` | 租約是否允許進行結構變更與大型改裝。 | `false` 則直接禁止 `IMPROVE` 動作。 | 布林值。 | 內部受限 (RESTRICTED) | CLM | `IMPROVE` 動作之 Admissibility 檢核 |
| `source_system` | `VARCHAR(64)` | 必填 (NOT NULL) | `ERP_SAP`, `CLM_DOCTRACK`, 等 | 來源系統代碼。 | 不可為空。 | 列舉值驗證。 | 內部公開 (INTERNAL) | 系統整合層 | Lineage 與資料治理稽核 |
| `source_snapshot_id` | `VARCHAR(128)` | 必填 (NOT NULL) | 快照 ID 字串 | 資料匯入時之版本快照識別碼。 | 不可為空。 | 必須具備可追溯之 Snapshot Reference。 | 內部公開 (INTERNAL) | 資料管線 | `ActionOption.source_snapshot_ids` |

---

## 3. 候選新址租賃條件實體 (`CandidateSiteLeaseTerms`)

對應展店候選點位（`OPEN`）或搬遷目的地（`MOVE` 接收端）之租賃條件。

| 欄位名稱 (`Field Name`) | 資料型態 (`Type`) | 必填／可空 (`Nullability`) | 單位／值域 (`Unit / Domain`) | 商業定義與語意說明 | 缺席 vs 量測為零處理規則 | 驗證規則與限制 | 敏感度等級 (`Data Class`) | 來源系統 (`Source System`) | 下游消費端對齊 (`Downstream Consumer`) |
|---|---|---|---|---|---|---|---|---|---|
| `candidate_site_id` | `VARCHAR(64)` | 必填 (NOT NULL) | `CND-[A-Z0-9_-]+` | 候選新址唯一識別碼，外鍵參照 `expansion` 領域模型。 | 不可為空。 | 正則表達式檢驗。 | 內部公開 (INTERNAL) | Expansion Domain | `CandidateSiteInput.candidate_site_id` |
| `listing_id` | `VARCHAR(128)` | 必填 (NOT NULL) | 物件代碼字串 | 參照關聯資料庫 `expansion.listings.listing_id`。 | 不可為空。 | 外鍵或來源識別參照。 | 內部公開 (INTERNAL) | `expansion.listings` | 資料表關聯與物件比對 |
| `available_from` | `DATE` | 必填 (NOT NULL) | `YYYY-MM-DD` | 起租可得日（物件可交付裝潢/營業之最早日期）。 | 不可為空；若缺失無法計算展店時間視窗。 | 必須小於等於 `available_to`。 | 內部受限 (RESTRICTED) | Listing Feed / Assisted Intake | `OPEN` / `MOVE` 執行季度對齊 |
| `available_to` | `DATE` | 可為空 (Nullable) | `YYYY-MM-DD` | 招租截止日（房東保留物件之最晚有效日）。 | `None` 表示常態招租；有日期時若晚於規劃執行期則過期失效。 | 必須 `>= available_from` 或 `NULL`。 | 內部受限 (RESTRICTED) | Listing Feed / Assisted Intake | 檔期過期過濾 (Time-window filter) |
| `signing_deadline` | `DATE` | 可為空 (Nullable) | `YYYY-MM-DD` | 簽約檔期截止日（必須完成正式簽約之最後期限）。 | `None` 視為未設定簽約時限；若已過期則不可採用。 | 必須 `<= available_from` 或 `NULL`。 | 內部受限 (RESTRICTED) | 開發業務紀錄 | 簽約急迫性與不可行判定 |
| `target_lease_term_years` | `INTEGER` | 必填 (NOT NULL) | 年數，`>= 1` | 房東要求或計畫設定之起租合約年限（如 3 年、5 年）。 | 不可為空；預設至少 1 年。 | 整數 `>= 1`。 | 內部受限 (RESTRICTED) | 租賃條件設定 | Capex 攤提年限與總租金承諾試算 |
| `expected_monthly_rent` | `NUMERIC(12,2)` | 必填 (NOT NULL) | 新台幣 (TWD)，`>= 0.0` | 預估每月租金。 | 不可為空。 | 數值 `>= 0.0`。 | 內部機密 (CONFIDENTIAL) | Listing Data | `CandidateSiteInput.open_cost` / 預估損益 |
| `security_deposit` | `NUMERIC(12,2)` | 必填 (NOT NULL) | 新台幣 (TWD)，`>= 0.0` | 預估押金需求（通常為 2-3 個月租金）。 | 不可為空。 | 數值 `>= 0.0`。 | 內部機密 (CONFIDENTIAL) | Listing Data | `ActionOption.budget_cost`（期初資本支出） |
| `fitout_grace_days` | `INTEGER` | 必填 (NOT NULL) | 日數，`>= 0` | 免租裝潢期天數（房東同意免計租金之施工期）。 | 缺失視為 `0` 天；可抵扣重疊租金或施工期成本。 | 整數 `>= 0`。 | 內部受限 (RESTRICTED) | 商業條件協議 | `MOVE` 重疊租金抵減與 `OPEN` 施工期計算 |
| `zoning_commercial_permitted` | `BOOLEAN` | 必填 (NOT NULL) | `true` / `false` | 都市計畫土地使用分區是否合法允許零售商業經營。 | `false` 直接拒絕 `OPEN` / `MOVE`（硬限制違規）。 | 布林值。 | 內部公開 (INTERNAL) | 地政／法規資料庫 | 前置 Admissibility 檢核 |
| `source_feed_id` | `VARCHAR(64)` | 必填 (NOT NULL) | `PARTNER_FEED_EXPANSION`, 等 | 資料進件來源代碼。 | 不可為空。 | 列舉值驗證。 | 內部公開 (INTERNAL) | 供應商註冊表 | Ingestion 溯源 |
| `source_snapshot_id` | `VARCHAR(128)` | 必填 (NOT NULL) | 快照 ID 字串 | 候選點位資料快照版本號。 | 不可為空。 | 必須具備可追溯 Snapshot ID。 | 內部公開 (INTERNAL) | 資料管線 | `CandidateSiteInput.source_snapshot_ids` |

---

## 4. 租約可行性評估結果實體 (`LeaseAdmissibilityResult`)

定義 `LeaseAdmissibilityChecker` 在前置評估或求解過程中的輸出規格。

| 欄位名稱 (`Field Name`) | 資料型態 (`Type`) | 必填／可空 (`Nullability`) | 單位／值域 (`Unit / Domain`) | 商業定義與說明 |
|---|---|---|---|---|
| `entity_id` | `VARCHAR(128)` | 必填 (NOT NULL) | 字串 | 評估對象識別碼（門市 ID、候選點 ID，或 MOVE 時之 `STR-xxx -> CND-yyy` 複合 ID）。 |
| `action` | `VARCHAR(32)` | 必填 (NOT NULL) | `OPEN`, `KEEP`, `IMPROVE`, `MOVE`, `EXIT` | 評估之網路規劃動作。 |
| `status` | `VARCHAR(32)` | 必填 (NOT NULL) | `FEASIBLE`, `INFEASIBLE_WINDOW`, `INFEASIBLE_LANDLORD`, `PENALTY_REQUIRED`, `UNMEASURED` | 可行性狀態分類。 |
| `fail_closed_triggered` | `BOOLEAN` | 必填 (NOT NULL) | `true` / `false` | 是否因資料缺席或違規觸發 Fail-Closed 拒絕。 |
| `adjusted_budget_cost` | `NUMERIC(12,2)` | 可為空 (Nullable) | 新台幣 (TWD) | 計入違約金、押金沒收、復原費、雙重租金後之修正預算成本。若 `status=UNMEASURED` 則為 `NULL`。 |
| `overlap_window_days` | `INTEGER` | 可為空 (Nullable) | 日數 | 搬遷 (`MOVE`) 動作時計算之新舊門市重疊交接天數。 |
| `reason` | `TEXT` | 必填 (NOT NULL) | 字串 | 結構化診斷訊息，提供決策者與審批者審查依據。 |
| `evidence_snapshot_id` | `VARCHAR(128)` | 必填 (NOT NULL) | 字串 | 綁定輸入合約資料之快照雜湊。 |

---

## 5. 資料敏感性與權限控管規範 (Data Governance & Privacy)

1. **禁止存儲敏感合約本文**：
   - Git 儲存庫嚴禁提交任何未經脫敏的真實租賃合約 PDF、掃描件、房東個人證件或銀行資料。
   - 所有測試 Fixture 與 Contract Draft 僅使用合成資料（如 `STR-TPE-001`）與遮蔽合約 ID（`CNT-2022-TPE001-RED`）。
2. **存取權限矩陣**：
   - `Store Operations Lead` / `Real Estate Finance Lead`：具備讀寫權限。
   - `NetPlan Optimization Engine`（演算法/求解器服務）：具備只讀授權快照（Read-Only Authorized View）存取權。
   - `Operator UI`：僅展示摘要數值（如 `exit_cost`、合約到期季度）與未建模告警，不揭露合約私密條款。
