# 人工資料請求單：H05 門市租約最小授權匯出 (Human Input Request H05)

- **請求編號**：`H05`（參照 `ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md` §4）
- **關聯任務**：`ODP-NET002-LEASE-CONTRACT-PREP-001`
- **關聯需求**：`ODP-FR-NET-002`（硬限制：租約 LEASE）
- **決策依據**：使用者確認之決策 D18（NET-002 租約：實作／補齊租約契約）
- **工作包代碼**：`WP-32A`（契約草案與請求交付）→ `WP-32B`（入場條件）
- **基準代碼 SHA**：`9048161e058becff5a53593a773d3c42238213fb`
- **生成時間**：2026-09-08T16:11:05Z
- **文件狀態**：正式資料請求（Formal Data Request）
- **關聯產物**：
  - [JSON 契約草案](lease-contract-draft.json)
  - [欄位字典](field-dictionary.md)
  - [Solver 驗收矩陣](solver-acceptance-matrix.md)
  - [實作交接計畫](implementation-handoff.md)
  - [總索引 README](README.md)

---

## 1. 請求背景與目的 (Background & Objective)

依據 2026-09-08 使用者確認之政策決策 **D18**，系統確立不刪除需求、不建立豁免（No Waiver），採取**實作／補齊真實租約契約**方向。

目前系統現況（經 `ODP-NET002-LEASE-DATA-READINESS-001` 查證）：
1. 既有資料庫 `core.stores` 僅有門市開關日，全無租約到期日、無解約違約金、無續約條款。
2. 領域模型 `ExistingStoreInput.exit_cost` 存在危險之 `0.0` 預設值，導致求解器誤將關店視為零代價。
3. 候選新址缺乏具定期新鮮度之自動 Feed，亦缺乏簽約截止日與裝潢免租期。

**本請求單（H05）旨在向業務、財務與法務負責人提出「最小授權資料匯出」需求**，以支撐下一階段（WP-32B）之真實資料串接與求解器約束啟用。

---

## 2. 權責人與交付角色 (Designated Authorities)

| 治理角色 (`Role`) | 權責範圍 (`Responsibility`) | 指定負責人／單位 (`Designated Principal`) |
|---|---|---|
| **Store Operations Lead** (門市營運主管) | 提供既有門市租約起迄、續約意向、改裝許可與房東協調現況。 | 零售營運部 (Retail Store Ops) |
| **Real Estate Finance Lead** (展店財務主管) | 審定提前解約違約金試算公式、押金沒收金額、新址租賃預算與雙重租金上限。 | 財務規劃與不動產投資部 (RE Finance) |
| **Legal & Compliance** (法務與合規) | 審核合約脫敏規範、確保無個人資料洩漏、授權合約摘要資料進入運算管線。 | 法務與合規部 (Legal & Compliance) |
| **Platform Architecture Board** | 驗證資料匯入管線之安全性、快照版本控管與 Fail-Closed 防護。 | 平台架構委員會 (Architecture Board) |

---

## 3. 最小必要欄位清單 (Minimum Required Data Fields)

為落實資料最小化（Data Minimization）原則，僅請求以下 12 項關鍵欄位，不索取完整合約本文：

| 欄位名稱 (`Field Name`) | 必填性 (`Requirement`) | 格式／型態 (`Format`) | 範例值 (`Example`) | 商業用途與必要性說明 |
|---|---|---|---|---|
| `store_id` | **必填** (Mandatory) | 字串 (`STR-xxx`) | `STR-TPE-001` | 關聯現有門市主檔之唯一索引鍵。 |
| `lease_contract_id` | **必填** (Mandatory) | 字串（去識別化） | `CNT-2022-TPE001` | 合約主檔代碼，供稽核追溯，禁止個人姓名。 |
| `lease_start_date` | **必填** (Mandatory) | 日期 (`YYYY-MM-DD`) | `2022-01-01` | 租期起日，計算已履約期間。 |
| `lease_expiry_date` | **必填** (Mandatory) | 日期 (`YYYY-MM-DD`) | `2027-12-31` | 租期迄日，判斷 KEEP 跨期與 EXIT 自然屆期。 |
| `monthly_rent` | **必填** (Mandatory) | 數值 (TWD, `>= 0.0`) | `120000.0` | 每月租金，計算 OPEX 與 MOVE 重疊租金。 |
| `deposit_amount` | **必填** (Mandatory) | 數值 (TWD, `>= 0.0`) | `360000.0` | 押金總額，計算提前解約沒收損失。 |
| `break_clause_allowed` | **必填** (Mandatory) | 布林值 (`true`/`false`) | `true` | 是否允許提前終止合約。若 false 則禁止期前關店。 |
| `break_notice_months` | **必填** (Mandatory) | 整數 (月數, `>= 0`) | `3` | 提前解約預告期，對齊規劃執行季度時序。 |
| `early_termination_penalty` | **可空但建議提供** | 數值 (TWD, `>= 0.0`) 或 `null` | `240000.0` | 提前解約實質違約金。若未知請填 `null`（不得誤填 `0.0`）。 |
| `restoration_cost_estimate` | **可空** (Optional) | 數值 (TWD, `>= 0.0`) 或 `null` | `150000.0` | 拆除復原清運預估費。 |
| `renewal_option_flag` | **必填** (Mandatory) | 布林值 (`true`/`false`) | `true` | 是否有優先續約權。 |
| `alteration_permitted` | **必填** (Mandatory) | 布林值 (`true`/`false`) | `true` | 是否允許門市大型改裝（`IMPROVE` 前置條件）。 |

---

## 4. 敏感資料與安全保護規範 (Security & Confidentiality Rules)

> [!CAUTION]
> **嚴格禁止將真實未脫敏之原始租約合約上傳至 Git 儲存庫！**

1. **嚴禁機密洩漏**：
   - 房東個人姓名、身分證字號、電話、私有住址、銀行匯款帳號等個資（PII），必須在來源端全數過濾與移除。
   - 不得將合約全文掃描檔（PDF/JPG）上傳至程式碼儲存庫或公開 Bucket。
2. **傳輸與存儲途徑**：
   - 正式資料請經由公司內部加密資料庫 View（如 `internal_analytics.v_store_lease_summary`）或受 IAM 控管之專用 GCS Bucket 提供。
   - 進入工程環境之測試資料一律使用 [lease-contract-draft.json](lease-contract-draft.json) 所定義之合成 Mock Fixture。
3. **資料新鮮度與快照驗證**：
   - 每次匯出資料必須附帶 `snapshot_id` 與產出時間戳記，以利建立可追溯之 Lineage。

---

## 5. 交付與驗收簽署流程 (Handoff & Sign-off Checklist)

當權責人完成資料準備後，請依循以下檢核清單進行簽署交接：

- [ ] **欄位完整性檢核**：已涵蓋全台所有營業中門市之 `store_id`，且無遺漏門市。
- [ ] **語意正確性檢核**：確認無違約金者（如期滿自然關店）填入 `0.0`；合約未知／待查者填入 `null`（非 `0.0`）。
- [ ] **脫敏合規審查**：法務或資安負責人確認匯出資料中無任何個人個資與非必要機密。
- [ ] **格式驗證**：符合 [field-dictionary.md](field-dictionary.md) 所載之型態與驗證規則。
- [ ] **正式簽署**：由 `Store Operations Lead` 或 `Real Estate Finance Lead` 發布正式匯出通知。
