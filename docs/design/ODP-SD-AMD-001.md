---
doc_id: ODP-SD-AMD-001
title: "平台與模組設計修正案 001"
version: 0.5.0
status: draft-for-review
document_class: system-design-amendment
project: ODay Plus
language: zh-TW
updated_at: 2026-09-01
owner: "Architecture Owner"
approvers: "Technology Lead / Data Owner / QA Lead"
content_format: markdown
amends:
  - ODP-SD-05_DATABASE_AND_STORAGE_DESIGN.md
  - ODP-SD-06_API_DESIGN_SPECIFICATION.md
  - ODP-SD-08_WORKFLOW_JOB_AND_STATE_MACHINE_DESIGN.md
  - ODP-UX-03_SCREEN_AND_INTERACTION_SPECIFICATION.md
change_class: C2
source_gap: ODP-GAP-FR-20260901
source_amendment: ODP-SA-06-AMD-001
baseline_commit: origin/dev@29a10711
---

# 平台與模組設計修正案 001

## 1. 修正目的與範圍

本修正案為 `ODP-GAP-FR-20260901` 所列 9 項落差提供設計。設計原則有三：

1. **接在既有結構上**。本案不新增平行機制。每項設計均指名其擴充的既有 schema、資料表或服務，且不建立第二套做同一件事的路徑。
2. **政策與程式分離**。四燈門檻、熱區合併門檻、觀察期長度等均為政策值，一律移入 Decision Policy 物件，不得留在程式常數。
3. **回饋不覆寫**。所有回收與回饋機制（`FCT-008`、`HZ-004`、`AVM-005`）一律以獨立記錄承載，透過重算生效，不直接修改預測或決策欄位（`ODP-BR-GOV-001`）。

第 3 節為平台級機制，其餘模組設計依賴之，應優先實作。

### 1.1 v0.2.0 修訂說明

初版（v0.1.0）以 `governance`、`forecastops`、`heatzone`、`avm`、`priceops` 五個 schema 命名新資料表，並以 `forecast_alerts`、`heatzone_scores`、`sitescore_recommendations`、`price_plans`、`netplan_scenarios` 指稱既有資料表。**這些名稱在版本庫中都不存在**，因此初版一方面宣稱不建立平行結構，一方面實際上把每一張表都放在既有 canonical schema 之外——兩者互相矛盾。

v0.2.0 先建立第 2 節的 baseline 對照表，再讓所有設計綁定其上。名稱的更動不是措辭問題：綁錯 schema 的設計一旦實作，產生的就是本案原則第 1 條明文禁止的平行結構。

### 1.2 v0.3.0 修訂說明

針對審查反饋（Codex2 第 2 輪）補正四項設計落差：

1. **FCT-005 評估識別（Evaluation Identity）**：原設計 `alert_id` 僅由 `forecast_output_id` 衍生，導致同一預測輸出套用多個政策版本時 `operations.alerts` 主鍵碰撞。v0.3.0 明確定義 Evaluation Identity 為 `(forecast_output_id, decision_policy_version_id)`，`alert_id` 衍生納入政策版本，並在 `operations.alerts` 補上外鍵與唯一索引，使多版本判定得以共存持久化並可被獨立稽核。
2. **FCT-008 回饋結構化狀態與重算血統**：原 `applied_effect` 為 nullable free text，後續預測無法以 SQL 結構化查詢其回饋來源。v0.3.0 將其改為結構化 `applied_status`、`not_applied_reason_code`、`recalculation_forecast_output_id`、`recalculation_run_id` 與 `applied_at`，並建立索引，落實可雙向追溯的重算血統。
3. **PRICE-006 Bandit 候選介面與逐決策 Gate 稽核**：原設計僅定義 Gate 授權與 GET 端點。v0.3.0 補齊領域層 `BanditCandidate` 資料結構、`BanditPriceExplorer` 協定、`POST /api/v1/priceops/exploration-candidates` 候選產生端點，並在 `000017` 增加 `pricing.exploration_decisions` 記錄逐筆定價決策所關聯之 `gate_id` 與預算扣抵。
4. **000013 Migration 可執行 Seed 與 Retrofit 列**：第 11 節所述之 `four-light-policy-0.0.0-retrofit` 回填佔位列與 `four-light-policy-v1` 首版政策列，直接以可執行且具冪等性（`ON CONFLICT DO NOTHING`）的 `INSERT` 語句納入第 3.2 節 migration SQL。

### 1.3 v0.4.0 修訂說明

針對審查反饋（Codex2 第 4 輪）補正七項落差。其中前三項是 v0.3.0 引入的**內部不一致**，後四項是被宣稱但未被強制的**空頭約束**：

1. **政策識別碼命名規則未貫穿全文**：v0.3.0 的 seed SQL 改為逐租戶的 `four-light-policy-v1:{tenant_id}`，但第 4.1 與第 11 節仍寫無租戶後綴的 `four-light-policy-v1`／`four-light-policy-0.0.0-retrofit`。v0.4.0 在第 3.2 節新增 `policy_label` 欄位與 `chk_decision_policy_version_id_format`，把「`policy_version_id` = `policy_label` + `:` + `tenant_id`」由慣例升為資料庫強制的規則，並逐節校正 seed、回滾、解析與相容性文字（見第 3.2、3.3、3.5、4.1、11 節）。
2. **`operations.alerts.decision_policy_version_id` 無外鍵**：`FCT-005` 要求每筆警示保有真實政策綁定，但該欄位僅為 nullable `VARCHAR` 且無外鍵，可寫入任意字串。v0.4.0 為四張擴充表補上 `NOT VALID` 外鍵（第 3.4 節），並在第 11 節寫明 `NOT NULL` 前的回填語句與轉換條件。
3. **`chk_feedback_recalculation_provenance` 過寬**：原約束容許 `APPLIED_RECALCULATION` 只填 `recalculation_run_id`，與第 4.3 節「以 `recalculation_forecast_output_id` 查詢」的宣稱矛盾。v0.4.0 改為必填該欄位，並新增 `chk_feedback_kind_applied_status`（回饋類型與生效路徑必須相容）與 `chk_feedback_applied_at`（已生效必有生效時點）。
4. **`asset.deal_outcomes.deal_terms` 無完整性規則**：`FR-AVM-005` 要求回收交易條件，但該欄位可為 NULL 且無鍵位要求。v0.4.0 以 `chk_deal_outcome_terms_completeness` 強制 `CLOSED` 必須帶 `payment_method`／`handover_date`／`contingencies` 三鍵，未成交則不得挾帶（第 6.2 節）。
5. **熱區吸收欄位無約束**：`absorbed_demand`／`remaining_demand` 可為負、可只填一半、可與 `absorption_ratio` 互相矛盾，`HZ-004` 因而不可強制。v0.4.0 補 `chk_heatzone_absorption_non_negative`、強化 `chk_heatzone_absorption_complete` 為全有全無，並新增 `chk_heatzone_absorption_consistent`（第 5.1 節）。
6. **第 13.1 節案例數與腳本不符**：文中寫 39／39，腳本實際為 48 例。v0.4.0 改以腳本實際輸出為準（見第 13.1 節）。
7. **負向案例可能因錯誤原因被拒**：原腳本多個負向案例共用同一 `valuation_run_id`／`decision_id`／`geo_cell_id`，可能被主鍵或唯一索引先擋下，而非被它宣稱測試的 CHECK 擋下。v0.4.0 讓每個案例自帶隔離用相依列，並**逐案斷言拒絕訊息中出現預期的約束名稱**，使「被拒」與「被正確的約束拒」不再被混為一談。

### 1.4 v0.5.0 修訂說明

針對審查反饋（Codex2 第 5 輪）補正五項落差。五項的共同型態是**治理宣稱缺乏結構性支撐**：文件寫明的閘門，在資料庫層由寫入端自律，因此任何能寫該表的角色都能繞過。

1. **回饋核准與 `workflow.approvals` 無實際連結**：`chk_feedback_applied_requires_approval` 只讀本表自述的 `approval_status`，寫入端可自填 `APPROVED` 後推進到 `APPLIED_RECALCULATION`，與第 4.3 節「需 Data Owner 核准」的宣稱矛盾。v0.5.0 新增 `approval_id`／`approval_decision_id` 與生成欄位 `approval_source_status`，以三欄複合外鍵 `fk_feedback_workflow_approval` 綁定 `workflow.approvals` 中該決策的真實核准列（第 4.3 節）。
2. **熱區組成稽核不是 Append-Only**：文件宣告不作原地覆寫，DDL 卻對 UPDATE／DELETE 毫無限制，撤銷用的 UPDATE 可順手改寫決策人與理由。v0.5.0 以 `trg_heatzone_composition_append_only` 只放行「將 `reverted_at` 由 NULL 設為時點」一種改寫（第 5.2 節）。
3. **探索預算未與 Gate 累計器原子綁定**：`exploration_decisions.budget_consumed` 僅有 `>= 0`，Gate 的累加寫在文件的協定段落而非資料庫，直接 INSERT 即可繞過總預算。v0.5.0 以 `trg_exploration_decisions_accrue` 在同一次寫入內累加並驗證 Gate 有效性，另以 append-only trigger 禁止事後回收扣抵（第 7 節）。
4. **政策與既有表之間的租戶歸屬未解**：政策逐租戶建列，但既有四張表沒有 `tenant_id`，單欄外鍵只證明政策存在、不證明它屬於同一租戶。v0.5.0 為四張表補 `tenant_id`，政策綁定改為 `(decision_policy_version_id, tenant_id)` 複合外鍵，`operations.alerts` 另以 `fk_alerts_store_tenant` 使租戶不可自述；`expansion.heatzone_composition` 與 `pricing.exploration_gates` 一併改為複合外鍵（第 3.4、5.2、7 節）。
5. **HZ 吸收來源未持久化**：`HeatZoneV3Input` 有 `absorption_source` 與 `absorbing_store_count`，持久化卻只寫三個數字與一個時點，事後無從分辨排名下降是需求被吸收還是換了實績來源。v0.5.0 將兩欄一併持久化並納入全有全無的完整性約束（第 5.1 節）。

---

## 2. Baseline 儲存現況（設計前提）

本節列出設計所依賴的既有結構，供審查者逐項核對。來源為 `infra/db/migrations/000001_baseline_canonical_schema.sql` 至 `000012`。

**既有 schema**：`core`、`workflow`、`expansion`、`operations`、`pricing`、`marketing`、`asset`、`network`、`learning`、`audit`、`geo`（以上由 `000001` 建立），以及 `odp_runtime`、`external_data`、`data_plane`、`intake`、`identity`（由 `000008` 至 `000012` 建立）。

**本案涉及的既有資料表**：

| 既有資料表 | 主鍵 | 本案用途 | 初版誤稱為 |
|---|---|---|---|
| `workflow.decisions` | `decision_id UUID` | 政策綁定的既有承載處，已有 `policy_version_id VARCHAR(100) NOT NULL` | （未提及） |
| `workflow.approvals` | `approval_id UUID` | 回饋與推翻的核准路徑 | （未提及） |
| `operations.alerts` | `alert_id UUID` | 四燈警示，擴充政策與生命週期欄位 | `forecast_alerts` |
| `operations.forecast_outputs` | `forecast_output_id UUID` | 回饋的預測目標 | （未提及） |
| `expansion.heatzone_scores` | `heatzone_score_id UUID` | 熱區評分，擴充吸收欄位 | `heatzone_scores`（無 schema） |
| `expansion.site_score_runs` | `sitescore_run_id UUID` | 選址評分，擴充政策欄位 | `sitescore_recommendations` |
| `asset.valuation_runs` | `valuation_run_id UUID` | 成交結果的估值對象 | （未提及） |
| `network.network_plans` | `network_plan_id UUID` | 方案，擴充政策欄位 | `netplan_scenarios` |
| `learning.predictions` | `prediction_id UUID` | 回饋的預測目標 | （未提及） |
| `learning.model_versions` | `model_version_id VARCHAR(100)` | 版本登錄表的既有形制範本 | （未提及） |
| `geo.h3_cells` | `geo_cell_id UUID` | 熱區組成單元 | （未提及） |

**兩項需要明講的 baseline 事實**：

1. **`pricing` schema 存在但沒有任何資料表**。初版所稱的 `price_plans` 不存在，PriceOps 目前完全無持久化。因此第 7 節的 `pricing.exploration_gates` 會是該 schema 的第一張表——這仍屬「接在既有結構上」（schema 已由 `000001` 宣告），但必須據實說明，不能讓讀者以為它擴充了某張既有表。
2. **`000001` 建立的模組資料表大多沒有 `tenant_id`**。整份 `000001` 只有 `core.tenants`、`core.brands`、`core.stores` 三處出現該欄位，`operations.alerts` 與 `expansion.heatzone_scores` 都是經由 `store_id` / `geo_cell_id` 間接歸屬租戶。較晚的 `000009` 至 `000012` 則普遍直接帶 `tenant_id`。本案新表採後者（直接帶 `tenant_id` 並外鍵至 `core.tenants`），因為新表需要在無 store 關聯時（如平台級政策）仍可租戶隔離。v0.5.0 另為本案綁定政策的四張既有表（`operations.alerts`、`expansion.heatzone_scores`、`expansion.site_score_runs`、`network.network_plans`）補上 nullable `tenant_id`，理由見第 3.4 節：政策逐租戶建列後，沒有租戶欄位的表無法證明它綁的是自己租戶的政策。**這只解到「政策綁定所需」為止**——`000001` 其餘模組表的租戶模型仍不一致，該部分為既存問題，本案不修復也不假裝不存在，記於第 14 節。

---

## 3. 平台級：Decision Policy 機制

對應 `ODP-FR-FCT-005`，但適用範圍為全平台。`ODP-SA-07` 第 8 節已定義政策物件的欄位，本節定義其持久化、解析與綁定。

### 3.1 綁定既有結構的依據

`workflow.decisions` 已有一欄 `policy_version_id VARCHAR(100) NOT NULL`，且**沒有任何資料表在它後面**——它是一個指向不存在登錄表的必填外鍵語意欄位。同理，`modules/forecastops/domain/forecasting.py:38` 已定義 `FOUR_LIGHT_POLICY_VERSION = "four-light-policy-v1"` 並寫入每筆警示的 `evidence_json`。

因此政策登錄表**不是新機制**，而是補上既有欄位與既有常數所預設、但從未建立的那一張表。它屬於 `workflow` schema，因為決策治理的既有歸屬就在該處。形制比照 `learning.model_versions`（`VARCHAR(100)` 自然主鍵的版本登錄表），使兩類版本治理形狀一致。

### 3.2 資料模型

新增 migration `000013_decision_policy_registry.sql`：

```sql
CREATE TABLE IF NOT EXISTS workflow.decision_policies (
    policy_version_id       VARCHAR(100) PRIMARY KEY,          -- 例：'four-light-policy-v1:11111111-1111-1111-1111-111111111111'
    policy_label            VARCHAR(100) NOT NULL,             -- 跨租戶共用的版本標籤，例：'four-light-policy-v1'
    policy_id               VARCHAR(100) NOT NULL,             -- 例：'four-light-policy'
    policy_version          VARCHAR(50)  NOT NULL,             -- semver 或 retrofit 標記
    policy_kind             VARCHAR(100) NOT NULL,
    tenant_id               UUID         NOT NULL REFERENCES core.tenants(tenant_id),
    effective_from          TIMESTAMP WITH TIME ZONE NOT NULL,
    effective_to            TIMESTAMP WITH TIME ZONE,          -- NULL = 現行版本
    owner_role              VARCHAR(100) NOT NULL,
    approved_by             VARCHAR(255) NOT NULL,
    approved_at             TIMESTAMP WITH TIME ZONE NOT NULL,
    input_contract          VARCHAR(100) NOT NULL,
    output_contract         VARCHAR(100) NOT NULL,
    change_reason           TEXT         NOT NULL,
    rollback_policy_version VARCHAR(100),   -- 回退目標，見下方 fk_decision_policy_rollback_tenant
    parameters              JSONB        NOT NULL,
    declared_inputs         TEXT[]       NOT NULL,
    created_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_decision_policy_tenant_id_version UNIQUE (tenant_id, policy_id, policy_version),
    -- 命名規則由資料庫強制，而非靠慣例：policy_version_id = policy_label || ':' || tenant_id
    CONSTRAINT chk_decision_policy_version_id_format CHECK (
        policy_version_id = policy_label || ':' || tenant_id::text
    ),
    -- 標籤本身不得含分隔符，否則上式的拆解不唯一
    CONSTRAINT chk_decision_policy_label CHECK (
        policy_label <> '' AND position(':' in policy_label) = 0
    ),
    CONSTRAINT chk_decision_policy_kind CHECK (
        policy_kind IN ('forecast_alert', 'heatzone_merge', 'heatzone_absorption',
                        'sitescore_recommendation', 'price_exploration', 'netplan_action')
    ),
    CONSTRAINT chk_decision_policy_window CHECK (
        effective_to IS NULL OR effective_to > effective_from
    ),
    CONSTRAINT chk_decision_policy_reason CHECK (change_reason <> ''),
    CONSTRAINT chk_decision_policy_inputs CHECK (cardinality(declared_inputs) > 0),
    CONSTRAINT chk_decision_policy_params CHECK (jsonb_typeof(parameters) = 'object')
);

-- 每個 (policy_id, tenant_id) 至多一個現行版本。
CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_policy_active
    ON workflow.decision_policies (policy_id, tenant_id)
    WHERE effective_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_decision_policy_kind_window
    ON workflow.decision_policies (policy_kind, tenant_id, effective_from);

-- 供既有表以 (decision_policy_version_id, tenant_id) 複合外鍵綁定的參照目標（第 3.4 節）。
-- 主鍵已蘊含其唯一性，此處是為了讓「政策的租戶」成為可被外鍵引用的欄位對。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_decision_policy_version_tenant'
    ) THEN
        ALTER TABLE workflow.decision_policies
            ADD CONSTRAINT uq_decision_policy_version_tenant
            UNIQUE (policy_version_id, tenant_id);
    END IF;
    -- 回退目標必須是同一租戶的真實版本：跨租戶回退等於把另一租戶的門檻套到自己身上
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_decision_policy_rollback_tenant'
    ) THEN
        ALTER TABLE workflow.decision_policies
            ADD CONSTRAINT fk_decision_policy_rollback_tenant
            FOREIGN KEY (rollback_policy_version, tenant_id)
            REFERENCES workflow.decision_policies(policy_version_id, tenant_id);
    END IF;
END $$;

-- 初始政策列與回填佔位列（逐租戶建立，支援冪等重跑）
INSERT INTO workflow.decision_policies (
    policy_version_id, policy_label, policy_id, policy_version, policy_kind,
    tenant_id, effective_from, effective_to,
    owner_role, approved_by, approved_at,
    input_contract, output_contract, change_reason,
    rollback_policy_version, parameters, declared_inputs
)
SELECT
    'four-light-policy-0.0.0-retrofit:' || t.tenant_id::text,
    'four-light-policy-0.0.0-retrofit',
    'four-light-policy',
    '0.0.0-retrofit',
    'forecast_alert',
    t.tenant_id,
    '2020-01-01 00:00:00+00',
    '2026-09-01 00:00:00+00',
    'system',
    'system_bootstrap',
    '2026-09-01 00:00:00+00',
    'ForecastOutput',
    'Alert',
    '歷史警示回填佔位，記錄機制導入前判定',
    NULL,
    '{"thresholds": [{"level": "RED", "value": -0.35}, {"level": "ORANGE", "value": -0.20}, {"level": "YELLOW", "value": -0.10}]}'::jsonb,
    ARRAY['sitescore_gap_ratio']
FROM core.tenants t
ON CONFLICT (policy_version_id) DO NOTHING;

INSERT INTO workflow.decision_policies (
    policy_version_id, policy_label, policy_id, policy_version, policy_kind,
    tenant_id, effective_from, effective_to,
    owner_role, approved_by, approved_at,
    input_contract, output_contract, change_reason,
    rollback_policy_version, parameters, declared_inputs
)
SELECT
    'four-light-policy-v1:' || t.tenant_id::text,
    'four-light-policy-v1',
    'four-light-policy',
    '1.0.0',
    'forecast_alert',
    t.tenant_id,
    '2026-09-01 00:00:00+00',
    NULL,
    'ops',
    'architecture_owner',
    '2026-09-01 00:00:00+00',
    'ForecastOutput',
    'Alert',
    '機制導入，門檻沿用常數，納入資料品質守衛',
    'four-light-policy-0.0.0-retrofit:' || t.tenant_id::text,
    '{"thresholds": [{"level": "RED", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.35}, {"level": "ORANGE", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.20}, {"level": "YELLOW", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.10}], "data_quality_guard": {"max_staleness_days": 2, "on_violation": "SUPPRESS_HIGH_CONFIDENCE"}}'::jsonb,
    ARRAY['sitescore_gap_ratio', 'data_quality.staleness_days']
FROM core.tenants t
ON CONFLICT (policy_version_id) DO NOTHING;
```

`change_reason` 與 `rollback_policy_version` 為 `ODP-SA-07` 第 8 節必填欄位，目前產品根目錄無實作，本表為其唯一承載處。`rollback_policy_version` 以 `(rollback_policy_version, tenant_id)` 自我複合外鍵，確保可回退目標不只是真實存在的版本，而且是**同一租戶**的版本——跨租戶回退等於把別的租戶的門檻套到自己身上。該欄位可為 NULL（首版無回退目標），故此處採 `MATCH SIMPLE`：未指定回退目標時不檢查。

`tenant_id` 設為 `NOT NULL` 有一項直接後果：平台級政策也必須逐租戶建列，不能以單一 NULL 列涵蓋所有租戶。這是刻意的——`idx_decision_policy_active` 若允許 NULL，Postgres 的 NULL 相異語意會讓同一政策存在多個「現行版本」而不被擋下。以每租戶一列換取多租戶獨立性與唯一性由資料庫保證，確保後續新增租戶或跨租戶政策解析均能正確執行。

**政策識別碼命名規則（全文適用）**。逐租戶建列使識別碼分成兩層，兩者不可混用：

| 名稱 | 欄位 | 範圍 | 例 |
|---|---|---|---|
| 政策標籤 | `policy_label` | 跨租戶共用，人可讀，用於文件、程式常數與 UI 文案 | `four-light-policy-v1`、`four-light-policy-0.0.0-retrofit` |
| 政策版本識別碼 | `policy_version_id` | 逐租戶唯一，為本表主鍵與所有外鍵的參照目標 | `four-light-policy-v1:11111111-1111-1111-1111-111111111111` |

規則為 `policy_version_id = policy_label || ':' || tenant_id`，由 `chk_decision_policy_version_id_format` 在資料庫層強制，不是靠寫入端自律；`chk_decision_policy_label` 另禁止標籤內含 `:`，使該式的拆解唯一。兩者成立後，`(tenant_id, policy_label)` 的唯一性由主鍵直接蘊含，故不另設 UNIQUE 約束。

**這條規則存在的理由**：v0.3.0 只在 seed SQL 裡採用後綴格式，文件其餘各節仍寫無後綴的字串，導致同一份設計對「政策識別碼是什麼」給出兩種答案。把規則寫成約束後，任何一節若再寫出無後綴的識別碼，第 13.1 節的驗證會直接失敗，不必依賴人工比對。

### 3.3 版本解析

政策解析為**時點解析**而非取現行版本：以決策發生時刻查 `effective_from <= t AND (effective_to IS NULL OR t < effective_to)`。這使歷史決策可重現——重跑一筆三個月前的警示，會取到當時生效的版本，而非今日版本。

解析的輸入為 `(policy_kind, tenant_id, at)`，輸出為該租戶的那一列，其 `policy_version_id` 必然帶租戶後綴（第 3.2 節命名規則）。**解析端不得自行以 `policy_label` 拼出識別碼**：租戶下若無對應列，正確行為是 `PolicyResolutionError`（第 3.5 節）而非拼出一個查無此列的字串。第 3.4 節新增的外鍵使這種拼接在資料庫層也會被擋下。

政策升版採 close-and-insert：將舊版 `effective_to` 設為新版 `effective_from`，不修改舊版其餘欄位。舊版永久保留（`ODP-AC-BR-003`）。

### 3.4 與決策記錄的綁定

`workflow.decisions.policy_version_id` 補上其早已隱含的外鍵：

```sql
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_decisions_policy_version'
    ) THEN
        ALTER TABLE workflow.decisions
            ADD CONSTRAINT fk_decisions_policy_version
            FOREIGN KEY (policy_version_id)
            REFERENCES workflow.decision_policies(policy_version_id)
            NOT VALID;                 -- 既有列不阻擋；回填後再 VALIDATE
    END IF;
END $$;
```

其餘產生決策的既有資料表新增 `decision_policy_version_id`，且 `operations.alerts` 新增 `forecast_output_id` 以支援同一預測輸出在多版本政策評估時之評估識別（Evaluation Identity）：

```sql
ALTER TABLE operations.alerts
    ADD COLUMN IF NOT EXISTS forecast_output_id UUID REFERENCES operations.forecast_outputs(forecast_output_id),
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES core.tenants(tenant_id),
    ADD COLUMN IF NOT EXISTS decision_policy_version_id VARCHAR(100);
ALTER TABLE expansion.heatzone_scores
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES core.tenants(tenant_id),
    ADD COLUMN IF NOT EXISTS decision_policy_version_id VARCHAR(100);
ALTER TABLE expansion.site_score_runs
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES core.tenants(tenant_id),
    ADD COLUMN IF NOT EXISTS decision_policy_version_id VARCHAR(100);
ALTER TABLE network.network_plans
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES core.tenants(tenant_id),
    ADD COLUMN IF NOT EXISTS decision_policy_version_id VARCHAR(100);

-- 同一預測輸出在同一政策版本下至多一筆評估警示（評估識別）
CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_forecast_policy
    ON operations.alerts (forecast_output_id, decision_policy_version_id)
    WHERE forecast_output_id IS NOT NULL;

-- 四張擴充表的政策綁定一律外鍵至登錄表，且**連同租戶一起綁**：欄位可暫時為 NULL
-- （見第 11 節兩階段），但不得填入查無此列的字串，也不得綁到別的租戶的政策。
-- MATCH FULL 使「填了政策卻不宣告租戶」同樣被擋下；NOT VALID 使既有列不阻擋 migration。
DO $$
BEGIN
    -- 門市的 (store_id, tenant_id)：既有表的租戶歸屬要能被外鍵引用，必須先有此參照目標
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_stores_store_tenant'
    ) THEN
        ALTER TABLE core.stores
            ADD CONSTRAINT uq_stores_store_tenant UNIQUE (store_id, tenant_id);
    END IF;
    -- 警示自述的租戶必須就是其門市的租戶（此處為 MATCH SIMPLE：store_id 恆非 NULL，
    -- 而 tenant_id 在第一階段可為 NULL，MATCH FULL 會使既有列與過渡期寫入全數失敗）
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_alerts_store_tenant'
    ) THEN
        ALTER TABLE operations.alerts
            ADD CONSTRAINT fk_alerts_store_tenant
            FOREIGN KEY (store_id, tenant_id)
            REFERENCES core.stores(store_id, tenant_id) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_alerts_decision_policy'
    ) THEN
        ALTER TABLE operations.alerts
            ADD CONSTRAINT fk_alerts_decision_policy
            FOREIGN KEY (decision_policy_version_id, tenant_id)
            REFERENCES workflow.decision_policies(policy_version_id, tenant_id)
            MATCH FULL NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_heatzone_scores_decision_policy'
    ) THEN
        ALTER TABLE expansion.heatzone_scores
            ADD CONSTRAINT fk_heatzone_scores_decision_policy
            FOREIGN KEY (decision_policy_version_id, tenant_id)
            REFERENCES workflow.decision_policies(policy_version_id, tenant_id)
            MATCH FULL NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_site_score_runs_decision_policy'
    ) THEN
        ALTER TABLE expansion.site_score_runs
            ADD CONSTRAINT fk_site_score_runs_decision_policy
            FOREIGN KEY (decision_policy_version_id, tenant_id)
            REFERENCES workflow.decision_policies(policy_version_id, tenant_id)
            MATCH FULL NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_network_plans_decision_policy'
    ) THEN
        ALTER TABLE network.network_plans
            ADD CONSTRAINT fk_network_plans_decision_policy
            FOREIGN KEY (decision_policy_version_id, tenant_id)
            REFERENCES workflow.decision_policies(policy_version_id, tenant_id)
            MATCH FULL NOT VALID;
    END IF;
END $$;
```

**租戶歸屬：政策綁定為何必須是複合外鍵**。第 3.2 節讓 `policy_version_id` 逐租戶唯一，但既有的四張表（`000001` 建立，第 2 節事實 2）本身沒有 `tenant_id`，因此「這筆警示綁的政策是不是它自己租戶的政策」在資料庫層無從判斷——單欄外鍵只證明該政策列存在，不證明它屬於誰。租戶 A 的警示可以合法地綁上租戶 B 的政策列，而多租戶隔離正是第 3.2 節逐租戶建列的唯一理由。

領域層其實早已有這個概念：`Alert`（`modules/forecastops/domain/forecasting.py:277`）第二個欄位就是必填的 `tenant_id`，只有資料表沒有——租戶歸屬在寫入時被丟棄，因此本節補的不是新概念，而是把既有領域欄位落回儲存層。

本版因此把租戶歸屬補在同一層：四張表各新增 nullable `tenant_id`（外鍵至 `core.tenants`），政策綁定改為 `(decision_policy_version_id, tenant_id)` 複合外鍵，參照 `workflow.decision_policies (policy_version_id, tenant_id)`。`MATCH FULL` 使「綁了政策卻不宣告租戶」與「宣告的租戶與政策不符」都被擋下，而兩欄皆為 NULL 的既有列不受影響。`operations.alerts` 另以 `fk_alerts_store_tenant` 綁回 `core.stores (store_id, tenant_id)`，使租戶不能自述——它必須等於該警示所屬門市的租戶。三者合起來，租戶歸屬形成一條可驗證的鏈：門市 → 警示 → 政策。

`expansion.heatzone_scores`、`expansion.site_score_runs` 與 `network.network_plans` 沒有 `store_id` 這類單一租戶錨點（分別經 `geo_cell_id`、`candidate_site_id` 與整體規劃範圍間接歸屬），故本案只補到「租戶必須與政策一致」為止，其錨點回填屬第 11 節第二階段的資料工作。這是本案刻意的邊界，不是遺漏。

**為何四張表都要外鍵，而不是只在程式層檢查**。`decision_policy_version_id` 是 `VARCHAR(100)`；沒有外鍵時，任何字串都寫得進去——包含第 3.3 節明文禁止的「以 `policy_label` 拼出來的識別碼」。`ODP-FR-FCT-005` 要求每筆警示保有可查證的政策綁定，一個查無此列的字串滿足不了該要求，而且要到稽核當下才會被發現。`NOT VALID` 只豁免既有列，對此後的所有寫入即刻生效，故這是本階段就能取得的最強保證。

**欄位命名的理由**：不用 `policy_version_id`，因為 `operations.forecast_outputs` 與 `Alert.evidence_json` 已存在語意不同的 `policy_version`（見第 4.1 節）。同名不同義的欄位會使日後的查詢與稽核無從分辨。`decision_policy_version_id` 明確指向本節的登錄表。

**`workflow.decisions` 為何仍是單欄外鍵**：該表本身沒有 `tenant_id`，其租戶歸屬須經 `entity_type`／`entity_id` 指向的實體推導，不是本案可在四張表之外一併解決的範圍。因此 `fk_decisions_policy_version` 維持單欄，它證明政策存在但不證明租戶一致；該缺口與 `000001` 其餘模組表的租戶模型一併記於第 14 節，不在此偽稱已解。

**PriceOps 例外**：PriceOps 無既有資料表可加欄位（第 2 節事實 1），其政策綁定改由第 7 節的 `pricing.exploration_gates.decision_policy_version_id` 承載，並經 `workflow.decisions` 記錄決策本身。

**硬性要求**：**無法解析政策時應拒絕產生決策，而非以預設值產生**。三層保證分工如下，缺一則該要求僅存在於文件：

| 層 | 機制 | 現在就生效？ | 擋得住什麼 |
|---|---|---|---|
| 應用層 | `resolve_policy()` fail-closed（第 3.5 節） | 是 | 解析失敗時繼續以預設門檻產生決策 |
| 資料庫參照完整性 | `fk_*_decision_policy`（`NOT VALID`） | 是 | 填入查無此列的政策識別碼 |
| 資料庫必填性 | `NOT NULL` | 否，見第 11 節第二階段 | 完全不填政策識別碼 |

第三層須待既有列回填完成才能開啟；在此之前欄位維持 nullable，回填語句與轉換條件列於第 11 節，不留待實作時自行決定。

### 3.5 領域介面

```python
# shared/governance/decision_policy.py（新增）

@dataclass(frozen=True)
class DecisionPolicy:
    policy_version_id: str               # 帶租戶後綴，外鍵參照用
    policy_label: str                    # 跨租戶標籤，文件與 evidence 用
    policy_id: str
    policy_version: str
    policy_kind: str
    parameters: Mapping[str, Any]
    declared_inputs: tuple[str, ...]
    effective_from: datetime
    effective_to: datetime | None


class PolicyResolutionError(RuntimeError):
    """政策無法解析。呼叫端必須 fail closed，不得以預設門檻繼續。"""


def resolve_policy(kind: str, tenant_id: str, *, at: datetime) -> DecisionPolicy: ...
```

模組放在 `shared/governance/` 而非任一模組下，因為五個 `policy_kind` 分屬五個模組，置於任一模組內都會造成跨模組相依。

---

## 4. ForecastOps

### 4.1 四燈改由政策產生（`ODP-FR-FCT-005`）

現行 `modules/forecastops/domain/forecasting.py:547` 的 `_alert_for()` 以字面值 `-0.35 / -0.20 / -0.10` 切分燈號。改為：

```python
def _alert_for(
    output: ForecastOutput,
    *,
    opened_at: datetime,
    policy: DecisionPolicy,          # 新增，必填
) -> Alert:
    level, reason_code, evidence = evaluate_alert_policy(output, policy)
    # 評估識別（Evaluation Identity）：同一預測輸出在不同政策版本下具獨立 alert_id
    evaluation_alert_id = _stable_id(
        "forecast-alert",
        output.forecast_output_id,
        policy.policy_version_id,
    )
    return Alert(
        alert_id=evaluation_alert_id,
        tenant_id=output.tenant_id,
        store_id=output.store_id,
        forecast_output_id=output.forecast_output_id,
        alert_level=level,
        alert_reason_code=reason_code,
        evidence_json={
            **evidence,
            "policy_version_id": policy.policy_version_id,
        },
        opened_at=opened_at,
        decision_policy_version_id=policy.policy_version_id,
    )
```

**評估識別（Evaluation Identity）的必要性**。在基線版本中，`alert_id` 僅由 `_stable_id("forecast-alert", output.forecast_output_id)` 生成。這導致當同一筆預測輸出套用兩個不同版本政策進行試算或回溯時，產生的 `alert_id` 完全相同，在 `operations.alerts` 寫入時會引發主鍵衝突或覆寫歷史，無法滿足 `ODP-AC-FR-008`（同一預測結果以不同政策版本評估可得不同燈號且兩者皆可重現與持久化共存）。修訂後，評估識別由 `(forecast_output_id, decision_policy_version_id)` 複合決定，`alert_id` 衍生納入政策版本，並在資料庫層建立唯一索引 `idx_alerts_forecast_policy`，確保多版本判定可獨立持久化與查詢。

**既有 `policy_version` 的處置**。`ForecastOutput.policy_version` 目前填入常數 `FOUR_LIGHT_POLICY_VERSION = "four-light-policy-v1"`（`modules/forecastops/domain/forecasting.py:38`、`:537`），並被複製進 `evidence_json["policy_version"]`。依第 3.2 節的命名規則，該常數的值正是**政策標籤**（`policy_label`），不是主鍵：

| 欄位 | 值 | 變動 |
|---|---|---|
| `ForecastOutput.policy_version` | `policy.policy_label` = `four-light-policy-v1` | 值不變，語意由「一個沒有對應列的字串」變為「登錄表中真實存在的標籤」 |
| `evidence_json["policy_version"]` | 同上 | 值不變 |
| `evidence_json["policy_version_id"]` | `policy.policy_version_id` = `four-light-policy-v1:{tenant_id}` | 新增鍵，逐租戶 |
| `operations.alerts.decision_policy_version_id` | 同上 | 新增欄位，外鍵至登錄表 |

因此 `tests/integration/test_forecastops_alerts.py:85` 的既有斷言（`evidence_json["policy_version"] == "four-light-policy-v1"`）仍然成立，`docs/design/ODAY_PLUS_OPERATIONS_ALERT_UI_SPEC.md` 對該字串的三處引用亦不需改寫——它們引用的本來就是標籤。

**不採取的做法，與理由**：把常數改為逐租戶的 `policy_version_id`，會使一個租戶無關的模組常數承載租戶資訊，並讓上述既有斷言與 UI 文案全部失效。標籤與主鍵分層後，跨租戶引用一律用標籤，資料庫參照一律用主鍵，兩者不再互相冒充。

`evaluate_alert_policy()` 依 `policy.parameters` 的門檻表與 `policy.declared_inputs` 求值。政策參數結構：

```json
{
  "thresholds": [
    {"level": "RED",    "input": "sitescore_gap_ratio", "op": "<=", "value": -0.35},
    {"level": "ORANGE", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.20},
    {"level": "YELLOW", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.10}
  ],
  "data_quality_guard": {"max_staleness_days": 2, "on_violation": "SUPPRESS_HIGH_CONFIDENCE"}
}
```

首版政策的 `declared_inputs` 為：

```
{"sitescore_gap_ratio", "data_quality.staleness_days"}
```

**兩項輸入，不是一項**。`ODP-SA-06-AMD-001` 第 3.4 節說明第 2 點要求資料品質自第一版政策起即為宣告輸入：若首版不含資料品質，該政策從上線之日起就違反 `ODP-BR-FCT-003`，等於讓違規取得了政策外觀。`ODP-SA-07` 第 5 節其餘八項輸入在首版標記為未納入，由後續升版逐步擴充。

**行為凍結的正確範圍**。門檻值逐字沿用現行三個常數，故四燈判定結果不變；但 `data_quality_guard` 是**新增行為**，會使 Stale 資料下的高信心警示被抑制。兩者必須分開驗證：門檻遷移以「同一批預測輸入產生同一組燈號」為驗收，資料品質守衛以 `ODP-BR-FCT-003` 的獨立情境為驗收。初版將兩者混稱為「行為不變」是不正確的。

### 4.2 Alert 生命週期擴充（`ODP-FR-FCT-006`）

現行 `Alert`（同檔 275 行）有 `opened_at`、`acknowledged_*`，但無法計算預警提前量。

**欄位順序**：`Alert` 自 `status: str = "open"` 起皆為有預設值的欄位。新增的必填欄位**必須置於 `opened_at` 之後、`status` 之前**；置於既有預設值欄位之後會使 dataclass 定義本身無效（`TypeError: non-default argument follows default argument`）。修訂後全貌：

```python
@dataclass(frozen=True)
class Alert:
    alert_id: str
    tenant_id: str
    store_id: str
    alert_level: AlertLevel
    alert_reason_code: str
    evidence_json: dict[str, Any]
    opened_at: datetime
    decision_policy_version_id: str                       # 新增，必填，須在預設值欄位之前
    forecast_output_id: str | None = None                 # 新增：關聯預測輸出，支援評估識別
    status: str = "open"
    closed_at: datetime | None = None
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    acknowledgement_note: str | None = None
    deterioration_confirmed_at: datetime | None = None    # 新增：實際惡化確認時點
    disposition: AlertDisposition | None = None           # 新增：結案分類
```

```python
class AlertDisposition(StrEnum):
    TRUE_POSITIVE = "TRUE_POSITIVE"      # 惡化確實發生
    FALSE_POSITIVE = "FALSE_POSITIVE"    # 未發生惡化
    KNOWN_CONTEXT = "KNOWN_CONTEXT"      # 有已知外部因素，不計入
    UNRESOLVED = "UNRESOLVED"            # 觀察期未滿
```

**相容性影響**：新增必填欄位改變了 `Alert` 的位置參數順序，所有建構點都須傳入 `decision_policy_version_id`。基準版本的建構點共三處，全部使用關鍵字引數，故不受位置變動影響，但仍須補該引數：

- `modules/forecastops/domain/forecasting.py:558`（`_alert_for()` 內）
- `tests/contract/test_canonical_schema.py:267`
- `tests/integration/test_operator_live_repository.py:38`

對應的資料表欄位（第 3.4 節已加 `decision_policy_version_id` 與 `forecast_output_id`）：

```sql
ALTER TABLE operations.alerts
    ADD COLUMN IF NOT EXISTS deterioration_confirmed_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS disposition VARCHAR(50);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_alerts_disposition'
    ) THEN
        ALTER TABLE operations.alerts
            ADD CONSTRAINT chk_alerts_disposition CHECK (
                disposition IS NULL OR disposition IN (
                    'TRUE_POSITIVE', 'FALSE_POSITIVE', 'KNOWN_CONTEXT', 'UNRESOLVED'
                )
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_alerts_deterioration_order'
    ) THEN
        ALTER TABLE operations.alerts
            ADD CONSTRAINT chk_alerts_deterioration_order CHECK (
                deterioration_confirmed_at IS NULL
                OR deterioration_confirmed_at >= opened_at
            );
    END IF;
END $$;
```

**提前天數** = `deterioration_confirmed_at - opened_at`，僅在 `disposition = 'TRUE_POSITIVE'` 時有效。上列 `chk_alerts_deterioration_order` 使其不可能為負。

**Precision** = `TRUE_POSITIVE / (TRUE_POSITIVE + FALSE_POSITIVE)`，分母排除 `KNOWN_CONTEXT` 與 `UNRESOLVED`。排除 `KNOWN_CONTEXT` 是必要的：因裝修而下滑的門市被判紅燈，模型並沒有錯。

`deterioration_confirmed_at` 由批次作業回填——以警示開啟後的實績確認是否跨越惡化門檻，該門檻同樣為政策值（`policy_kind = 'forecast_alert'` 的 `parameters.deterioration_threshold`）。

### 4.3 Feedback 機制（`ODP-FR-FCT-008`）

歸屬 `operations` schema：回饋的目標是 `operations.alerts` 與 `operations.forecast_outputs`，兩者都在該 schema 內。

新增 migration `000014_forecast_feedback.sql`：

```sql
-- 核准的參照目標：使回饋能以複合外鍵綁定「某決策的某一筆核准列」，
-- 而不是只在自己表內複製一份核准狀態欄位（見本節「核准為何要外鍵」）。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_approvals_decision_status'
    ) THEN
        ALTER TABLE workflow.approvals
            ADD CONSTRAINT uq_approvals_decision_status
            UNIQUE (approval_id, decision_id, approval_status);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS operations.forecast_feedback (
    feedback_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id               UUID NOT NULL REFERENCES core.tenants(tenant_id),
    store_id                UUID NOT NULL,
    feedback_kind           VARCHAR(50) NOT NULL,

    -- 回饋目標：三選一以上，依 kind 決定何者必填
    target_alert_id             UUID REFERENCES operations.alerts(alert_id),
    target_forecast_output_id   UUID REFERENCES operations.forecast_outputs(forecast_output_id),
    target_prediction_id        UUID REFERENCES learning.predictions(prediction_id),

    -- 修正內容：OUTCOME_CORRECTION 專用
    corrected_metric        VARCHAR(100),          -- 被修正的實績指標名
    observed_value          NUMERIC(16, 4),        -- 系統原本取得的值
    corrected_value         NUMERIC(16, 4),        -- 提交者主張的正確值
    correction_unit         VARCHAR(50),

    effective_from          DATE NOT NULL,         -- 影響期間
    effective_to            DATE NOT NULL,
    reason_code             VARCHAR(100) NOT NULL,
    note                    TEXT,
    submitted_by            VARCHAR(255) NOT NULL,
    submitted_at            TIMESTAMP WITH TIME ZONE NOT NULL,
    approval_status         VARCHAR(50) NOT NULL,
    approved_by             VARCHAR(255),
    approved_at             TIMESTAMP WITH TIME ZONE,

    -- 核准的持久綁定：指向 workflow 的實際核准列，而非本表自述的狀態
    approval_decision_id    UUID REFERENCES workflow.decisions(decision_id),
    approval_id             UUID,
    approval_source_status  VARCHAR(50) GENERATED ALWAYS AS (
        CASE approval_status WHEN 'APPROVED' THEN 'approved'
                             WHEN 'REJECTED' THEN 'rejected' END
    ) STORED,

    -- 結構化生效狀態與重算血統（取代自由文字 applied_effect）
    applied_status          VARCHAR(50) NOT NULL DEFAULT 'PENDING_APPLICATION',
    not_applied_reason_code VARCHAR(100),
    recalculation_forecast_output_id UUID REFERENCES operations.forecast_outputs(forecast_output_id),
    recalculation_run_id    UUID REFERENCES learning.prediction_runs(prediction_run_id),
    applied_at              TIMESTAMP WITH TIME ZONE,

    correlation_id          VARCHAR(255) NOT NULL,
    created_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- 回饋的租戶不可自述：必須就是其門市所屬租戶
    CONSTRAINT fk_feedback_store_tenant FOREIGN KEY (store_id, tenant_id)
        REFERENCES core.stores(store_id, tenant_id),
    -- 一筆核准決策至多支撐一筆回饋，核准不可被重複借用
    CONSTRAINT uq_feedback_approval_decision UNIQUE (approval_decision_id),
    -- 核准狀態必須對應 workflow.approvals 中該決策的真實核准列：
    -- 三欄同時比對，'APPROVED' 只能對到狀態確為 'approved' 的那一列
    CONSTRAINT fk_feedback_workflow_approval
        FOREIGN KEY (approval_id, approval_decision_id, approval_source_status)
        REFERENCES workflow.approvals (approval_id, decision_id, approval_status)
        MATCH FULL,
    -- 已核准或已駁回必須指名該核准列；尚未進入核准流程者不得挾帶
    CONSTRAINT chk_feedback_approval_link CHECK (
        (approval_status IN ('APPROVED', 'REJECTED')
            AND approval_id IS NOT NULL AND approval_decision_id IS NOT NULL)
     OR (approval_status IN ('PENDING', 'AUTO_ACCEPTED')
            AND approval_id IS NULL AND approval_decision_id IS NULL)
    ),
    CONSTRAINT chk_feedback_kind CHECK (
        feedback_kind IN ('CONTEXT_ANNOTATION', 'OUTCOME_CORRECTION', 'ALERT_DISPOSITION')
    ),
    CONSTRAINT chk_feedback_approval_status CHECK (
        approval_status IN ('AUTO_ACCEPTED', 'PENDING', 'APPROVED', 'REJECTED')
    ),
    CONSTRAINT chk_feedback_applied_status CHECK (
        applied_status IN (
            'PENDING_APPLICATION', 'APPLIED_TRAINING_EXCLUSION',
            'APPLIED_RECALCULATION', 'APPLIED_DISPOSITION', 'NOT_APPLIED'
        )
    ),
    CONSTRAINT chk_feedback_not_applied_reason CHECK (
        (applied_status =  'NOT_APPLIED' AND not_applied_reason_code IS NOT NULL AND not_applied_reason_code <> '')
     OR (applied_status <> 'NOT_APPLIED' AND not_applied_reason_code IS NULL)
    ),
    -- 重算血統：APPLIED_RECALCULATION 必須指名重算後的預測輸出（該欄位是第 4.3 節
    -- 宣稱可查詢的那一欄）；非重算狀態則不得挾帶任何重算欄位。
    CONSTRAINT chk_feedback_recalculation_provenance CHECK (
        (applied_status =  'APPLIED_RECALCULATION'
            AND recalculation_forecast_output_id IS NOT NULL)
     OR (applied_status <> 'APPLIED_RECALCULATION'
            AND recalculation_forecast_output_id IS NULL
            AND recalculation_run_id IS NULL)
    ),
    -- 回饋類型與生效路徑必須相容：每類回饋只能走第 4.3 節表列的那一條路徑
    CONSTRAINT chk_feedback_kind_applied_status CHECK (
        applied_status IN ('PENDING_APPLICATION', 'NOT_APPLIED')
     OR (feedback_kind = 'CONTEXT_ANNOTATION' AND applied_status = 'APPLIED_TRAINING_EXCLUSION')
     OR (feedback_kind = 'OUTCOME_CORRECTION' AND applied_status = 'APPLIED_RECALCULATION')
     OR (feedback_kind = 'ALERT_DISPOSITION'  AND applied_status = 'APPLIED_DISPOSITION')
    ),
    -- 已生效者必有生效時點；未生效者不得有
    CONSTRAINT chk_feedback_applied_at CHECK (
        (applied_status IN ('APPLIED_TRAINING_EXCLUSION', 'APPLIED_RECALCULATION',
                            'APPLIED_DISPOSITION')
            AND applied_at IS NOT NULL)
     OR (applied_status IN ('PENDING_APPLICATION', 'NOT_APPLIED') AND applied_at IS NULL)
    ),
    -- 任何回饋都必須指向至少一個目標，否則無從稽核其作用對象
    CONSTRAINT chk_feedback_has_target CHECK (
        target_alert_id IS NOT NULL
        OR target_forecast_output_id IS NOT NULL
        OR target_prediction_id IS NOT NULL
    ),
    -- ALERT_DISPOSITION 必須指向警示
    CONSTRAINT chk_feedback_disposition_target CHECK (
        feedback_kind <> 'ALERT_DISPOSITION' OR target_alert_id IS NOT NULL
    ),
    -- OUTCOME_CORRECTION 必須指向預測或預測輸出，且必須帶完整修正內容
    CONSTRAINT chk_feedback_correction_target CHECK (
        feedback_kind <> 'OUTCOME_CORRECTION'
        OR (
            (target_forecast_output_id IS NOT NULL OR target_prediction_id IS NOT NULL)
            AND corrected_metric IS NOT NULL
            AND observed_value  IS NOT NULL
            AND corrected_value IS NOT NULL
            AND correction_unit IS NOT NULL
        )
    ),
    -- 非 OUTCOME_CORRECTION 不得挾帶修正值
    CONSTRAINT chk_feedback_correction_exclusive CHECK (
        feedback_kind = 'OUTCOME_CORRECTION'
        OR (corrected_metric IS NULL AND observed_value IS NULL
            AND corrected_value IS NULL AND correction_unit IS NULL)
    ),
    -- OUTCOME_CORRECTION 不得自動接受（須 Data Owner 核准）
    CONSTRAINT chk_feedback_correction_needs_approval CHECK (
        feedback_kind <> 'OUTCOME_CORRECTION' OR approval_status <> 'AUTO_ACCEPTED'
    ),
    -- 已核准或已駁回者必須有核准人與時點
    CONSTRAINT chk_feedback_approver_present CHECK (
        approval_status NOT IN ('APPROVED', 'REJECTED')
        OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)
    ),
    -- 嚴格治理：回饋進入套用狀態前必須具備相符核准（OUTCOME_CORRECTION 必須為 APPROVED
    -- 且有審核人，其真實性由上方 fk_feedback_workflow_approval 保證；
    -- 其餘可為 APPROVED 或 AUTO_ACCEPTED）
    CONSTRAINT chk_feedback_applied_requires_approval CHECK (
        applied_status NOT IN ('APPLIED_RECALCULATION', 'APPLIED_TRAINING_EXCLUSION', 'APPLIED_DISPOSITION')
        OR (
            (feedback_kind = 'OUTCOME_CORRECTION' AND approval_status = 'APPROVED' AND approved_by IS NOT NULL AND approved_at IS NOT NULL)
            OR (feedback_kind <> 'OUTCOME_CORRECTION' AND approval_status IN ('APPROVED', 'AUTO_ACCEPTED'))
        )
    ),
    -- PENDING 狀態之套用進度僅能為 PENDING_APPLICATION 或 NOT_APPLIED
    CONSTRAINT chk_feedback_pending_applied_status CHECK (
        approval_status <> 'PENDING'
        OR applied_status IN ('PENDING_APPLICATION', 'NOT_APPLIED')
    ),
    -- REJECTED 狀態必須為 NOT_APPLIED
    CONSTRAINT chk_feedback_rejected_applied_status CHECK (
        approval_status <> 'REJECTED'
        OR applied_status = 'NOT_APPLIED'
    ),
    CONSTRAINT chk_feedback_period CHECK (effective_to >= effective_from)
);

CREATE INDEX IF NOT EXISTS idx_forecast_feedback_store
    ON operations.forecast_feedback (store_id, effective_from);
CREATE INDEX IF NOT EXISTS idx_forecast_feedback_alert
    ON operations.forecast_feedback (target_alert_id)
    WHERE target_alert_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_forecast_feedback_pending
    ON operations.forecast_feedback (tenant_id, submitted_at)
    WHERE approval_status = 'PENDING';
CREATE INDEX IF NOT EXISTS idx_forecast_feedback_recalc
    ON operations.forecast_feedback (recalculation_forecast_output_id)
    WHERE recalculation_forecast_output_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_forecast_feedback_applied_status
    ON operations.forecast_feedback (tenant_id, applied_status);
CREATE INDEX IF NOT EXISTS idx_forecast_feedback_approval
    ON operations.forecast_feedback (approval_id)
    WHERE approval_id IS NOT NULL;
```

**核准為何要外鍵，而不是三個自述欄位**。v0.4.0 的 `chk_feedback_applied_requires_approval` 只讀本表自己的 `approval_status`／`approved_by`／`approved_at`。這三欄由提交回饋的同一條寫入路徑填寫，因此「Data Owner 已核准」在資料庫層只是**寫入端的自我宣告**：任何能寫本表的角色都可以填上 `APPROVED` 與一個姓名字串，接著合法地把 `applied_status` 推進到 `APPLIED_RECALCULATION`，而 `workflow.approvals` 裡從來沒有這筆核准。本表的治理宣稱（第 4.3 節表列「需 Data Owner（`workflow.approvals`）」）因而不可稽核。

v0.5.0 把核准變成參照完整性問題：

1. `approval_decision_id` 指向承載此回饋核准的 `workflow.decisions` 列——該表的 `policy_version_id` 本身已外鍵至政策登錄表（第 3.4 節），故核准也落在同一套治理下。
2. `approval_source_status` 是**由 `approval_status` 生成**的欄位（`GENERATED ALWAYS ... STORED`），把本表的大寫狀態映射為 `workflow.approvals` 的小寫值，寫入端無法單獨指定它。
3. `fk_feedback_workflow_approval` 以 `(approval_id, approval_decision_id, approval_source_status)` 三欄複合外鍵，參照 `workflow.approvals (approval_id, decision_id, approval_status)`。於是 `APPROVED` 只能對上一筆**確實存在、確實屬於該決策、且狀態確實是 `approved`** 的核准列；`MATCH FULL` 使三欄必須同時為 NULL 或同時具值。
4. `chk_feedback_approval_link` 規定 `APPROVED`／`REJECTED` 必須指名核准列，`PENDING`／`AUTO_ACCEPTED` 則不得挾帶。
5. `uq_feedback_approval_decision` 使一筆核准決策至多支撐一筆回饋，避免一次核准被多筆修正共用。

一個附帶但重要的後果：核准一旦被回饋引用，`workflow.approvals` 該列的狀態就**不能再被改回** `returned` 或 `pending`——外鍵會擋下該 UPDATE。核准的撤銷因此必須走新決策，而不是原地改寫既有核准，這與本案第 3 節「不覆寫」原則一致。

**這道閘擋得住什麼、擋不住什麼，據實說明**：它擋下「回饋表自己宣稱被核准」，因為核准的存在與狀態不再由本表決定；它擋不住同時具備 `workflow` 寫入權限者自建決策與核准列。後者是權限問題，不是結構問題——但差別在於，核准現在只存在於 `workflow.approvals` 一處，權限控制與稽核因此有單一施力點，而不是每張表各自複製一份可自行填寫的狀態欄位。第 14 節第 3 點所記的「稽核表缺資料庫層寫入限制」即為該施力點尚未收斂的部分。

**目標欄位為何是三個而非一個**。`ODP-SA-06-AMD-001` 第 3.1 節的修訂條文為「對已產生的**預測或警示**提交結構化回饋」。初版只有 `target_alert_id`，因此對預測的回饋無處可放，該 FR 在設計層就不可能被滿足，也無從稽核。三個目標欄位分別對應警示（`operations.alerts`）、預測輸出（`operations.forecast_outputs`）與模型預測（`learning.predictions`），由 `chk_feedback_has_target` 保證至少填一個。

**修正內容為何要三欄**。`OUTCOME_CORRECTION` 的語意是「系統取得的實績有誤」。只記「有誤」而不記原值、正確值與指標名，核准者無從判斷是否該核准，事後也無從稽核核准是否正確。`observed_value` 保留系統原值，使修正可被還原比對。

**結構化重算血統（Provenance）**。為滿足「後續預測輸出須可查詢其是否受回饋影響」，設計引入 `recalculation_forecast_output_id` 與 `recalculation_run_id` 兩項明確外鍵，取代模糊的文字描述：
1. 當 `OUTCOME_CORRECTION` 經 Data Owner 核准並觸發批次重算後，管線將新產生的 `forecast_output_id` 回填至本表的 `recalculation_forecast_output_id`，並將 `applied_status` 更新為 `APPLIED_RECALCULATION`、`applied_at` 填入生效時點。
2. 查詢任一預測輸出是否受回饋影響，只需執行 `SELECT * FROM operations.forecast_feedback WHERE recalculation_forecast_output_id = :id`，即可完整重現該預測所吸收之營運回饋清單與修正前／後實績差異。
3. 若回饋因故未生效（如核准駁回或超出時間窗），`applied_status` 設為 `NOT_APPLIED`，並由 `chk_feedback_not_applied_reason` 強制填寫 `not_applied_reason_code`。

**上一版的錯誤，與這次的修法**。v0.3.0 的 `chk_feedback_recalculation_provenance` 寫成 `recalculation_forecast_output_id IS NOT NULL OR recalculation_run_id IS NOT NULL`，也就是只填 `recalculation_run_id` 也算合格。但第 2 點宣稱的查詢是以 `recalculation_forecast_output_id` 為條件——一筆只有 run id 的列，在那個查詢裡查不到，卻仍被標記為已重算。**該約束因此容許了它自己宣稱要杜絕的狀態**。v0.4.0 改為必填 `recalculation_forecast_output_id`；`recalculation_run_id` 維持選填，作為重算批次的附加線索，但不能單獨充當血統。反向也一併封死：非 `APPLIED_RECALCULATION` 的列不得挾帶任何重算欄位，避免出現「有重算輸出但狀態說沒生效」的矛盾列。

**類型與路徑的相容性**。第 4.3 節表列的三條生效路徑是一對一的：標註走排除、修正走重算、處置走 Alert `disposition`。在 v0.3.0，`feedback_kind` 與 `applied_status` 是兩個彼此無關的欄位，因此可以寫出 `CONTEXT_ANNOTATION` + `APPLIED_RECALCULATION` 這種在設計上不存在的組合。`chk_feedback_kind_applied_status` 把該表變成資料庫規則；`PENDING_APPLICATION` 與 `NOT_APPLIED` 兩個尚未落到任一路徑的狀態，三類回饋皆可使用。`chk_feedback_applied_at` 則使「已生效」必然帶有生效時點——沒有時點的生效在稽核上無法定位到任何一次重算或訓練批次。

**三類回饋的處理路徑**：

| 類型 | 目標 | 核准 | 生效方式 | 結構化狀態碼 |
|---|---|---|---|---|
| `CONTEXT_ANNOTATION` | 任一 | 自動接受 | 該期間標記為排除區間，不進入訓練集與 Precision 分母 | `APPLIED_TRAINING_EXCLUSION` |
| `OUTCOME_CORRECTION` | 預測輸出或預測 | 需 Data Owner（`workflow.approvals`） | 核准後修正 canonical 實績並觸發重算；未核准時不生效 | `APPLIED_RECALCULATION` |
| `ALERT_DISPOSITION` | 警示 | 自動接受 | 寫入對應 Alert 的 `disposition`，關閉警示 | `APPLIED_DISPOSITION` |

**不覆寫原則**：三者皆不修改預測值。`OUTCOME_CORRECTION` 修改的是實績（canonical 資料），修改後由重算產生新預測，符合 `ODP-BR-GOV-001`。`chk_feedback_correction_needs_approval` 使「未經核准即生效」在資料庫層即不可能。

**API**（3 個端點）：

```
POST   /api/v1/forecastops/feedback                  建立回饋
GET    /api/v1/forecastops/feedback?store_id=        查詢
POST   /api/v1/forecastops/feedback/{id}/approve     OUTCOME_CORRECTION 專用
```

需 `forecastops:write` 權限；`OUTCOME_CORRECTION` 的核准另需 `data:approve`。核准端點的職責不是把本表的 `approval_status` 改成 `APPROVED`，而是在同一交易內建立 `workflow.decisions` 與 `workflow.approvals` 兩列，再把回饋綁上該核准（`approval_decision_id`／`approval_id`）；狀態欄位只是那筆綁定的投影。若核准列未建立，上述外鍵會使該次更新失敗——這是刻意的，核准不能只存在於本表。

**UI 前置處理**：在本機制上線前，`packages/domain-types/src/frontend-contracts.ts:292` 與 `packages/ui-domain/src/components.tsx:136` 的字串 `"Feedback written to label registry"` 必須移除或改為未啟用狀態。該文案目前對操作者作出不實陳述。

---

## 5. HeatZone Radar

### 5.1 需求吸收閉環（`ODP-FR-HZ-004`）

`HeatZoneV3Input`（`modules/heatzone/v3/contract.py:54`）現有欄位均為單一網格的靜態屬性，無實績輸入。新增：

```python
@dataclass(frozen=True)
class HeatZoneV3Input:
    # ... 既有欄位不變；下列新增欄位均有預設值，故置於既有欄位之後為合法 ...

    absorbing_store_count: int = 0               # 該單元內已開業且滿觀察期的門市數
    absorbed_demand: float = 0.0                 # 依實績計算的已吸收需求量
    absorption_basis_at: datetime | None = None  # 吸收量的計算基準時點
    absorption_source: str = ""                  # 實績來源識別（可追溯）
```

輸出 `HeatZoneV3ScoreResult` 新增 `remaining_demand` 與 `absorption_ratio`，並使 `unmet_demand_score` 改以 `remaining_demand` 為基礎，而非原始需求。

持久化擴充既有的 `expansion.heatzone_scores`（第 3.4 節已加 `decision_policy_version_id`）：

```sql
ALTER TABLE expansion.heatzone_scores
    ADD COLUMN IF NOT EXISTS absorbed_demand      NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS remaining_demand     NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS absorption_ratio     NUMERIC(5, 4),
    ADD COLUMN IF NOT EXISTS absorption_basis_at  TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS absorption_source    VARCHAR(255),
    ADD COLUMN IF NOT EXISTS absorbing_store_count INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_heatzone_absorption_ratio'
    ) THEN
        ALTER TABLE expansion.heatzone_scores
            ADD CONSTRAINT chk_heatzone_absorption_ratio CHECK (
                absorption_ratio IS NULL
                OR (absorption_ratio >= 0 AND absorption_ratio <= 1)
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_heatzone_absorption_complete'
    ) THEN
        -- 吸收六欄全有或全無：只有一半的吸收結果無法稽核，也無法計算剩餘需求；
        -- 缺來源識別與門市數則無從回推該次計算吃了哪些實績
        ALTER TABLE expansion.heatzone_scores
            ADD CONSTRAINT chk_heatzone_absorption_complete CHECK (
                (absorbed_demand     IS NULL
                 AND remaining_demand      IS NULL
                 AND absorption_ratio      IS NULL
                 AND absorption_basis_at   IS NULL
                 AND absorption_source     IS NULL
                 AND absorbing_store_count IS NULL)
             OR (absorbed_demand     IS NOT NULL
                 AND remaining_demand      IS NOT NULL
                 AND absorption_ratio      IS NOT NULL
                 AND absorption_basis_at   IS NOT NULL
                 AND absorption_source     IS NOT NULL
                 AND absorbing_store_count IS NOT NULL)
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_heatzone_absorption_non_negative'
    ) THEN
        -- 需求量不可為負：負的吸收量或負的剩餘需求沒有業務語意
        ALTER TABLE expansion.heatzone_scores
            ADD CONSTRAINT chk_heatzone_absorption_non_negative CHECK (
                (absorbed_demand  IS NULL OR absorbed_demand  >= 0)
            AND (remaining_demand IS NULL OR remaining_demand >= 0)
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_heatzone_absorption_source'
    ) THEN
        -- 來源識別必須是可追溯的字串，且吸收門市數不可為負
        ALTER TABLE expansion.heatzone_scores
            ADD CONSTRAINT chk_heatzone_absorption_source CHECK (
                (absorption_source IS NULL OR absorption_source <> '')
            AND (absorbing_store_count IS NULL OR absorbing_store_count >= 0)
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_heatzone_absorption_consistent'
    ) THEN
        -- 三個數字必須互相吻合：ratio = absorbed / (absorbed + remaining)
        ALTER TABLE expansion.heatzone_scores
            ADD CONSTRAINT chk_heatzone_absorption_consistent CHECK (
                absorbed_demand IS NULL
             OR (absorbed_demand + remaining_demand = 0 AND absorption_ratio = 0)
             OR (absorbed_demand + remaining_demand > 0
                 AND abs(absorption_ratio
                         - round(absorbed_demand / (absorbed_demand + remaining_demand), 4))
                     <= 0.0001)
            );
    END IF;
END $$;
```

**這四條約束為何是 `HZ-004` 的驗收前提**。`ODP-AC-FR-009` 要求熱區排名在需求被吸收後下降，其可驗證性完全建立在「吸收量、剩餘需求、吸收比例是同一次計算的三個面向，且該次計算的實績基礎可被指名」之上。v0.3.0 只約束了 `absorption_ratio` 的值域與「有吸收量就要有基準時點」，於是下列四種列都能入庫，而每一種都使該驗收無從執行：

| 可入庫的矛盾列 | 為何使 `HZ-004` 不可驗收 | 擋下它的約束 |
|---|---|---|
| `absorbed_demand = 10`、`remaining_demand = NULL` | 沒有剩餘需求就無法判斷排名是否應該下降 | `chk_heatzone_absorption_complete` |
| `absorbed_demand = -5` | 負吸收量會使排名不降反升 | `chk_heatzone_absorption_non_negative` |
| `absorbed = 10`、`remaining = 90`、`ratio = 0.9` | 比例與量互相矛盾，兩個下游讀哪一個就得到相反結論 | `chk_heatzone_absorption_consistent` |
| 有吸收量但 `absorption_source` 為 NULL | 無從回推該數字吃了哪批實績，重算與爭議時無法對帳 | `chk_heatzone_absorption_complete` |

**來源識別為何必須落到資料表**。`HeatZoneV3Input` 自 v0.2.0 起就有 `absorption_source`（實績來源識別）與 `absorbing_store_count` 兩個輸入欄位，但持久化只寫了三個數字與一個時點——輸入端宣稱可追溯，儲存端卻把追溯線索丟掉。`ODP-AC-FR-009` 的驗收要判斷「排名下降是因為需求真的被吸收」，而不是因為換了一批實績來源或觀察期門市集合改變；沒有這兩欄，兩者在事後無法分辨。v0.5.0 因此將兩欄一併持久化，並納入全有全無的完整性約束：吸收結果要嘛完整（含來源），要嘛不存在。`absorption_source` 記錄該次計算所依據的實績批次識別（例如 `revenue_daily@2026-08-31`），與 `absorption_basis_at` 一起構成可重算的基準。

一致性檢查以 `round(..., 4)` 對齊 `absorption_ratio` 的 `NUMERIC(5, 4)` 精度，並留 `0.0001` 的容差吸收寫入端的捨入差異；`absorbed + remaining = 0`（該單元完全無需求）另列為合法情形，此時比例定義為 `0` 而非除以零。第 5.1 節的 `compute_absorbed_demand()` 是這三個數字的唯一產生處，因此一致性在應用層與資料庫層都只有一個來源。

**吸收計算**（新增 `modules/heatzone/v3/absorption.py`）：

```python
def compute_absorbed_demand(
    observations: Sequence[StoreRevenueObservation],
    *,
    policy: DecisionPolicy,
) -> AbsorptionResult:
    """以實績計算已吸收需求。

    只接受已實現營收，不接受 SiteScore 預測值 —— 以預測計算吸收會
    造成自我實現循環：預測高的熱區被判吸收多因而降排名，而該預測
    從未被驗證。

    未滿觀察期的門市一律排除，避免以 Ramp 期實績低估吸收量。
    觀察期長度取自 policy.parameters["min_observation_days"]。
    """
```

**觸發**：熱區內門市開業滿觀察期、且新實績到達時，由排程批次重算該熱區。非即時。

**狀態轉移**：`PARTIALLY_ABSORBED` 與 `SATURATED` 改由 `absorption_ratio` 與政策門檻驅動，經 `ODP-BR-HZ-001` 的核准狀態機轉移，不得直接賦值。

**一致性約束**：`remaining_demand` 與 `ODP-FR-SITE-003` 的稀釋指標必須使用同一組實績基礎。實作上兩者共用 `compute_absorbed_demand()` 的輸出，不各自計算。

### 5.2 熱區合併與拆分（`ODP-FR-HZ-006`）

歸屬 `expansion` schema：`expansion.heatzone_scores` 在該處，組成單元參照 `geo.h3_cells`。

新增 migration `000015_heatzone_composition.sql`：

```sql
CREATE TABLE IF NOT EXISTS expansion.heatzone_composition (
    composition_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    zone_id             VARCHAR(100) NOT NULL,   -- 合併後熱區識別碼，格式 'MZ-{hash}'
    tenant_id           UUID NOT NULL REFERENCES core.tenants(tenant_id),
    member_cell_id      UUID NOT NULL REFERENCES geo.h3_cells(geo_cell_id),
    composition_kind    VARCHAR(50) NOT NULL,
    parent_zone_id      VARCHAR(100),            -- SPLIT_CHILD 時指向原熱區
    decided_by          VARCHAR(255) NOT NULL,   -- 'system' 或操作者
    decided_at          TIMESTAMP WITH TIME ZONE NOT NULL,
    decision_policy_version_id VARCHAR(100) NOT NULL,
    override_reason     TEXT,                    -- 人工推翻時必填
    reverted_at         TIMESTAMP WITH TIME ZONE,-- 撤銷時點；NULL = 生效中
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_composition_kind CHECK (
        composition_kind IN ('MERGED', 'SPLIT_CHILD', 'ATOMIC')
    ),
    -- SPLIT_CHILD 必須有母熱區；其餘不得有
    CONSTRAINT chk_composition_parent CHECK (
        (composition_kind =  'SPLIT_CHILD' AND parent_zone_id IS NOT NULL)
     OR (composition_kind <> 'SPLIT_CHILD' AND parent_zone_id IS NULL)
    ),
    -- 人工決定必須留理由；系統自動決定不得挾帶推翻理由
    CONSTRAINT chk_composition_override_reason CHECK (
        (decided_by =  'system' AND override_reason IS NULL)
     OR (decided_by <> 'system' AND override_reason IS NOT NULL AND override_reason <> '')
    ),
    CONSTRAINT chk_composition_revert_order CHECK (
        reverted_at IS NULL OR reverted_at >= decided_at
    ),
    -- 合併熱區識別碼不得重用組成單元識別碼
    CONSTRAINT chk_composition_zone_id_format CHECK (zone_id ~ '^MZ-[0-9a-f]{16}$'),
    -- 政策綁定連同租戶一起參照：不得綁到別的租戶的政策（第 3.4 節同一規則）
    CONSTRAINT fk_heatzone_composition_decision_policy
        FOREIGN KEY (decision_policy_version_id, tenant_id)
        REFERENCES workflow.decision_policies(policy_version_id, tenant_id)
);

-- 一個網格在同一時點只能屬於一個生效中的熱區
CREATE UNIQUE INDEX IF NOT EXISTS idx_heatzone_composition_active_member
    ON expansion.heatzone_composition (tenant_id, member_cell_id)
    WHERE reverted_at IS NULL;

-- 支援依熱區追溯完整可逆變更與人工推翻歷程之稽核索引
CREATE INDEX IF NOT EXISTS idx_heatzone_composition_audit
    ON expansion.heatzone_composition (tenant_id, zone_id, decided_at);

-- Append-Only 由資料庫強制：唯一允許的 UPDATE 是把生效中的列標為已撤銷，
-- 其餘欄位一律不可改寫，DELETE 一律不可。
CREATE OR REPLACE FUNCTION expansion.heatzone_composition_append_only()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'heatzone_composition_append_only: DELETE is not permitted (composition_id=%)',
            OLD.composition_id USING ERRCODE = '23514';
    END IF;
    IF OLD.reverted_at IS NOT NULL THEN
        RAISE EXCEPTION
            'heatzone_composition_append_only: composition_id=% is already reverted',
            OLD.composition_id USING ERRCODE = '23514';
    END IF;
    IF NEW.reverted_at IS NULL THEN
        RAISE EXCEPTION
            'heatzone_composition_append_only: the only permitted UPDATE is setting reverted_at'
            USING ERRCODE = '23514';
    END IF;
    IF ROW(NEW.composition_id, NEW.zone_id, NEW.tenant_id, NEW.member_cell_id,
           NEW.composition_kind, NEW.parent_zone_id, NEW.decided_by, NEW.decided_at,
           NEW.decision_policy_version_id, NEW.override_reason, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.composition_id, OLD.zone_id, OLD.tenant_id, OLD.member_cell_id,
           OLD.composition_kind, OLD.parent_zone_id, OLD.decided_by, OLD.decided_at,
           OLD.decision_policy_version_id, OLD.override_reason, OLD.created_at)
    THEN
        RAISE EXCEPTION
            'heatzone_composition_append_only: only reverted_at may change (composition_id=%)',
            OLD.composition_id USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $fn$;

DROP TRIGGER IF EXISTS trg_heatzone_composition_append_only
    ON expansion.heatzone_composition;
CREATE TRIGGER trg_heatzone_composition_append_only
    BEFORE UPDATE OR DELETE ON expansion.heatzone_composition
    FOR EACH ROW EXECUTE FUNCTION expansion.heatzone_composition_append_only();
```

**識別碼規則**：合併熱區的 `zone_id` **不得**重用任一組成單元的 `geo_cell_id`，避免下游將合併體誤認為原單元。`chk_composition_zone_id_format` 強制 `MZ-` 前綴，而 `geo_cell_id` 為 UUID，兩者格式互斥，故重用在資料庫層即不可能。

**鄰接判定**：以 H3 k-ring（k=1）為預設。跨行政區界是否可合併由 `policy.parameters["allow_cross_admin_boundary"]` 控制，不硬編。`geo.h3_cells` 已有 `admin_city` 與 `admin_district` 兩欄可供判定。

**治理與可逆稽核**：屬 Ranking Policy 層級（`ODP-SA-07` 第 2 節）。為滿足 `ODP-AC-FR-011` 的可逆稽核軌跡要求，本表採 **Append-Only 版本紀錄模型**（以 `composition_id` 為獨立主鍵），任何自動合併、拆分、人工推翻或撤銷均寫入新列，原生效中記錄則標註 `reverted_at`，不作 destructive 原地覆寫。自動合併可由展店 Owner 推翻，推翻須填 `override_reason` 與 `decided_by`，由 `chk_composition_override_reason` 保證。所有歷史版本均可依 `(tenant_id, zone_id, decided_at)` 完整重現與還原。

**Append-Only 為何需要 trigger 才成立**。v0.4.0 只在此段文字宣告「不作 destructive 原地覆寫」，而 DDL 完全沒有 UPDATE／DELETE 的限制——本節與第 13 節示範的撤銷流程本身就是一次 `UPDATE ... SET reverted_at`，同一條 UPDATE 也可以順手改掉 `decided_by`、`override_reason` 或 `decision_policy_version_id`，甚至直接 `DELETE` 掉整段人工推翻歷程，資料庫不會有任何反應。稽核軌跡若可被無痕改寫，它就不是稽核軌跡。

`trg_heatzone_composition_append_only` 把宣告變成規則，只放行一種改寫：把 `reverted_at` 由 NULL 設為時點，其餘十一個欄位逐一比對必須不變；已撤銷的列不可再改（撤銷本身也只發生一次）；`DELETE` 一律拒絕。因此「撤銷」與「竄改」在資料庫層分得開，而 `ODP-AC-FR-011` 要求的可逆軌跡不再依賴寫入端自律。至於本表的 `reverted_at` 與第 10 節的 migration 回滾仍是兩件事，見該節說明。

**API**（2 個端點）：

```
GET    /api/v1/heatzone/zones/{zone_id}/composition   查組成
POST   /api/v1/heatzone/zones/{zone_id}/override      人工推翻（需理由）
```

---

## 6. DealRoom AVM — 成交結果回收（`ODP-FR-AVM-005`／`008`）

歸屬 `asset` schema：估值對象 `asset.valuation_runs` 在該處。

### 6.1 欄位命名的硬性約束

`models/model_ready/contracts.py:181-182` 的 AVM `ModelSpec` 已宣告：

```python
label_name="realized_transaction_price",
label_column="realized_transaction_price",
temporal_column="realized_transaction_at",
relation="model_ready.valuation_view",
```

而 `pipelines/dbt/models/model_ready/valuation_view.sql` 不產出這兩欄（`realized` 在該檔零命中）。同時 `modules/dealroom/domain/confidential_access.py:94-97` 已將 `realized_transaction_price`、`raw_transaction_price`、`transaction_price` 列為機密遮罩鍵。

因此成交價欄位**必須沿用 `realized_transaction_price` 與 `realized_transaction_at`**。初版使用的 `settlement_price` / `settlement_date` 會造成兩項具體損害：標籤契約繼續指向不存在的欄位（模型仍無法訓練），且新名稱不在機密遮罩清單內，成交價會繞過既有的 `ConfidentialLeakError` 防護外洩。這正是本案原則第 1 條所要避免的平行結構。

### 6.2 資料模型

新增 migration `000016_avm_deal_outcome.sql`：

```sql
CREATE TABLE IF NOT EXISTS asset.deal_outcomes (
    deal_outcome_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID NOT NULL REFERENCES core.tenants(tenant_id),
    valuation_run_id    UUID NOT NULL REFERENCES asset.valuation_runs(valuation_run_id),
    listing_id          UUID REFERENCES expansion.listings(listing_id),
    outcome_kind        VARCHAR(50) NOT NULL,

    -- 成交（outcome_kind = 'CLOSED' 時必填，其餘必須為 NULL）
    realized_transaction_price  NUMERIC(18, 2),
    realized_transaction_at     TIMESTAMP WITH TIME ZONE,

    -- 未成交（outcome_kind <> 'CLOSED' 時必填，CLOSED 時必須為 NULL）
    no_deal_reason_code VARCHAR(100),
    no_deal_note        TEXT,

    duration_days       INTEGER NOT NULL,        -- 上架至結案天數，各 outcome_kind 皆適用
    deal_terms          JSONB,                   -- 交易條件；CLOSED 必填且須含三個鍵（見下）

    recorded_by         VARCHAR(255) NOT NULL,
    recorded_at         TIMESTAMP WITH TIME ZONE NOT NULL,
    source_authority    VARCHAR(100) NOT NULL,   -- 資料來源權威
    correlation_id      VARCHAR(255) NOT NULL,
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_deal_outcome_kind CHECK (
        outcome_kind IN ('CLOSED', 'WITHDRAWN', 'EXPIRED')
    ),
    -- 成交與未成交兩組欄位互斥且各自完整
    CONSTRAINT chk_deal_outcome_closed_fields CHECK (
        (outcome_kind =  'CLOSED'
            AND realized_transaction_price IS NOT NULL
            AND realized_transaction_at    IS NOT NULL
            AND no_deal_reason_code        IS NULL
            AND no_deal_note               IS NULL)
     OR (outcome_kind <> 'CLOSED'
            AND realized_transaction_price IS NULL
            AND realized_transaction_at    IS NULL
            AND no_deal_reason_code        IS NOT NULL)
    ),
    CONSTRAINT chk_deal_outcome_reason_code CHECK (
        no_deal_reason_code IS NULL OR no_deal_reason_code IN (
            'PRICE_GAP', 'CONDITION', 'FINANCING', 'WITHDRAWN_BY_OWNER', 'OTHER'
        )
    ),
    -- OTHER 必須附說明，否則「未成交原因」等於沒有回收
    CONSTRAINT chk_deal_outcome_other_note CHECK (
        no_deal_reason_code <> 'OTHER'
        OR (no_deal_note IS NOT NULL AND no_deal_note <> '')
    ),
    CONSTRAINT chk_deal_outcome_price_positive CHECK (
        realized_transaction_price IS NULL OR realized_transaction_price > 0
    ),
    CONSTRAINT chk_deal_outcome_duration CHECK (duration_days >= 0),
    -- 成交必須回收完整交易條件（ODP-FR-AVM-005）；未成交不得挾帶交易條件
    CONSTRAINT chk_deal_outcome_terms_completeness CHECK (
        (outcome_kind =  'CLOSED'
            AND deal_terms IS NOT NULL
            AND jsonb_typeof(deal_terms) = 'object'
            AND deal_terms ? 'payment_method'
            AND deal_terms ? 'handover_date'
            AND deal_terms ? 'contingencies'
            AND coalesce(deal_terms->>'payment_method', '') <> ''
            AND coalesce(deal_terms->>'handover_date', '')  <> ''
            AND jsonb_typeof(deal_terms->'contingencies') = 'array')
     OR (outcome_kind <> 'CLOSED' AND deal_terms IS NULL)
    )
);

-- 一次估值至多一筆有效成交結果
CREATE UNIQUE INDEX IF NOT EXISTS idx_deal_outcome_valuation
    ON asset.deal_outcomes (valuation_run_id);
CREATE INDEX IF NOT EXISTS idx_deal_outcome_realized
    ON asset.deal_outcomes (tenant_id, realized_transaction_at)
    WHERE outcome_kind = 'CLOSED';
```

`chk_deal_outcome_closed_fields` 是本表的核心約束：初版僅以 SQL 註解寫明「CLOSED 時必填」，註解不會阻擋任何寫入，一筆沒有成交價的 CLOSED 記錄仍可入庫，而校準計算會在讀取時才發現資料不完整。此處改為資料庫層強制。

**`deal_terms` 為何也要納入同一條規則**。`ODP-FR-AVM-005` 要求回收的不只是成交價，還包括交易條件；v0.3.0 把 `deal_terms` 留為可空且無任何鍵位要求，等於讓該 FR 的一半停在註解階段——這正是上一段批評 `chk_deal_outcome_closed_fields` 缺席時的同一個問題，只是換了一欄。`chk_deal_outcome_terms_completeness` 因此要求 `CLOSED` 必須帶三個鍵：

| 鍵 | 型別 | 為何是必要的 |
|---|---|---|
| `payment_method` | 非空字串 | 分期、貸款成數與一次付清的成交價不可直接比較，缺此欄則估值偏差計算會把不同條件的成交價混為一談 |
| `handover_date` | 非空字串（ISO 日期） | 交屋期是價格的一部分；`realized_transaction_at` 記的是成交時點，兩者不同 |
| `contingencies` | JSON 陣列（可為空陣列） | 附帶條件會實質改變成交價。允許空陣列而不允許缺鍵，是為了讓「確認沒有附帶條件」與「沒有人去問」在資料上可區分 |

未成交（`WITHDRAWN`／`EXPIRED`）則不得挾帶 `deal_terms`，與 `chk_deal_outcome_closed_fields` 對成交欄位的處理一致：沒有成交就沒有成交條件，若有議約過程要保留，其歸屬是 `no_deal_note` 而不是本欄。此處刻意不驗證三個鍵的內容值域（例如付款方式的列舉），因為條件用語隨市場而異，過早收斂會使真實成交無法登錄；本約束保證的是「該問的三件事都問了」。

### 6.3 與估值及標籤管線的綁定

**與估值的綁定**：`valuation_run_id` 為必填外鍵至 `asset.valuation_runs`。成交結果必須能對應到當時的估值輸出（`fair_price_p50`／`reserve_price`／`asking_price` 三價），否則無法計算偏差。

**標籤管線接線**（本項為 `ODP-FR-AVM-008` 可驗收的關鍵）：`pipelines/dbt/models/model_ready/valuation_view.sql` 須 `left join asset.deal_outcomes`，並新增兩個輸出欄位：

```sql
    deal_outcomes.realized_transaction_price,
    deal_outcomes.realized_transaction_at,
```

接線完成後，`ModelSpec` 宣告的 `label_column` 與 `temporal_column` 才第一次指向真實存在的欄位。`source_snapshot_ids` 陣列亦須加入 `'asset.deal_outcomes'`。**驗收方式**：對 `model_ready.valuation_view` 取欄位清單，`realized_transaction_price` 與 `realized_transaction_at` 必須存在——這是一個可直接執行的檢查，不需等模型訓練。

**校準用途**：新增 `modules/avm/application/calibration.py`：

```python
def compute_valuation_error(outcome: DealOutcome, valuation: ValuationRun) -> ValuationError:
    """計算估值偏差。CLOSED 才計算價格偏差；未成交只計入 Coverage 統計。"""
```

估值區間的 Coverage 檢查（`ODP-SA-08` 第 7 節 Calibration）以此為輸入：統計實際成交價落在 P10–P90 區間內的比率，理想值為 80%。`ModelSpec` 已設 `min_p80_coverage=0.70`，該門檻在成交價回收前無法被評估。

**與現有 liquidity 模型的關係**：`LiquidityTrainingRecord`（`modules/avm/domain/liquidity.py:9`）的 `duration_days` 與 `sold` 改由本表推導（`sold = (outcome_kind = 'CLOSED')`），不另行維護第二份來源。

**API**（2 個端點）：

```
POST   /api/v1/avm/deal-outcomes                   登錄成交結果
GET    /api/v1/avm/valuations/{id}/calibration     查該估值的偏差
```

需 `avm:write`；`realized_transaction_price` 屬敏感財務資料，讀取需 `finance:view`，回應須經 `modules/dealroom/domain/confidential_access.py` 的既有遮罩路徑，並依 `ODP-BR-OPS-002` 記錄匯出。

---

## 7. PriceOps — Bandit 與其 Gate（`ODP-FR-PRICE-006`）

**交付綁定**：本節的 Gate 與探索機制必須同批上線。先探索後補 Gate 會使 `ODP-BR-PRICE-004`（Hard Constraint）在期間內成為破口。

**歸屬說明**：置於 `pricing` schema。該 schema 由 `000001` 建立但至今無任何資料表（第 2 節事實 1），故本表是它的第一張表。此處沒有既有表可以擴充——這是 baseline 的實際狀態，不是設計上的選擇。

新增 migration `000017_price_exploration_gate.sql`：

```sql
CREATE TABLE IF NOT EXISTS pricing.exploration_gates (
    gate_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID NOT NULL REFERENCES core.tenants(tenant_id),
    scope_brand_id      UUID REFERENCES core.brands(brand_id),
    scope_store_group   VARCHAR(100),
    scope_sku_group     VARCHAR(100),
    budget_limit        NUMERIC(18, 2) NOT NULL,
    budget_consumed     NUMERIC(18, 2) NOT NULL DEFAULT 0,
    effective_from      TIMESTAMP WITH TIME ZONE NOT NULL,
    effective_to        TIMESTAMP WITH TIME ZONE NOT NULL,   -- 必填，不可無限期
    approved_by         VARCHAR(255) NOT NULL,
    rollback_condition  TEXT NOT NULL,
    revoked_at          TIMESTAMP WITH TIME ZONE,
    decision_policy_version_id VARCHAR(100) NOT NULL,
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_exploration_gates_gate_tenant UNIQUE (gate_id, tenant_id),
    -- 授權的政策必須屬於該 Gate 的租戶（第 3.4 節同一規則）
    CONSTRAINT fk_exploration_gates_decision_policy
        FOREIGN KEY (decision_policy_version_id, tenant_id)
        REFERENCES workflow.decision_policies(policy_version_id, tenant_id),
    CONSTRAINT chk_gate_window CHECK (effective_to > effective_from),
    CONSTRAINT chk_gate_budget_limit CHECK (budget_limit > 0),
    -- 已消耗不得為負，也不得超出上限：預算用罄在資料庫層即成立
    CONSTRAINT chk_gate_budget_consumed CHECK (
        budget_consumed >= 0 AND budget_consumed <= budget_limit
    ),
    CONSTRAINT chk_gate_rollback_condition CHECK (rollback_condition <> ''),
    CONSTRAINT chk_gate_revoke_order CHECK (
        revoked_at IS NULL OR revoked_at >= effective_from
    )
);

CREATE INDEX IF NOT EXISTS idx_exploration_gate_active
    ON pricing.exploration_gates (tenant_id, effective_from, effective_to)
    WHERE revoked_at IS NULL;

-- 每次探索定價決策所關聯之 Gate 紀錄與預算扣抵（逐決策稽核）
CREATE TABLE IF NOT EXISTS pricing.exploration_decisions (
    decision_id         UUID PRIMARY KEY REFERENCES workflow.decisions(decision_id),
    gate_id             UUID NOT NULL,
    tenant_id           UUID NOT NULL,
    sku_id              VARCHAR(100) NOT NULL,
    store_id            UUID REFERENCES core.stores(store_id),
    baseline_price      NUMERIC(18, 2) NOT NULL,
    explored_price      NUMERIC(18, 2) NOT NULL,
    budget_consumed     NUMERIC(18, 2) NOT NULL,
    algorithm           VARCHAR(50) NOT NULL,
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_exploration_decisions_gate_tenant
        FOREIGN KEY (gate_id, tenant_id)
        REFERENCES pricing.exploration_gates(gate_id, tenant_id),
    CONSTRAINT fk_exploration_decisions_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES core.tenants(tenant_id),
    CONSTRAINT chk_exploration_decision_prices CHECK (
        baseline_price > 0 AND explored_price > 0
    ),
    CONSTRAINT chk_exploration_decision_budget CHECK (budget_consumed >= 0)
);

CREATE INDEX IF NOT EXISTS idx_exploration_decisions_gate
    ON pricing.exploration_decisions (gate_id, created_at);

-- 預算扣抵由資料庫執行，而非由呼叫端記得執行：每寫入一筆探索決策，
-- 同一交易內即累加至該 Gate；Gate 必須在該時點有效且未撤銷。
CREATE OR REPLACE FUNCTION pricing.exploration_decisions_accrue_budget()
RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    accrued pricing.exploration_gates%ROWTYPE;
BEGIN
    UPDATE pricing.exploration_gates
       SET budget_consumed = budget_consumed + NEW.budget_consumed
     WHERE gate_id   = NEW.gate_id
       AND tenant_id = NEW.tenant_id
       AND revoked_at IS NULL
       AND NEW.created_at >= effective_from
       AND NEW.created_at <  effective_to
    RETURNING * INTO accrued;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'exploration_decisions_accrue_budget: gate % is not active for tenant % at %',
            NEW.gate_id, NEW.tenant_id, NEW.created_at USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END $fn$;

DROP TRIGGER IF EXISTS trg_exploration_decisions_accrue
    ON pricing.exploration_decisions;
CREATE TRIGGER trg_exploration_decisions_accrue
    AFTER INSERT ON pricing.exploration_decisions
    FOR EACH ROW EXECUTE FUNCTION pricing.exploration_decisions_accrue_budget();

-- 已扣抵的決策不可回頭改寫或刪除，否則累計器與逐筆紀錄會失去對應
CREATE OR REPLACE FUNCTION pricing.exploration_decisions_append_only()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION
        'exploration_decisions_append_only: % is not permitted on pricing.exploration_decisions',
        TG_OP USING ERRCODE = '23514';
END $fn$;

DROP TRIGGER IF EXISTS trg_exploration_decisions_append_only
    ON pricing.exploration_decisions;
CREATE TRIGGER trg_exploration_decisions_append_only
    BEFORE UPDATE OR DELETE ON pricing.exploration_decisions
    FOR EACH ROW EXECUTE FUNCTION pricing.exploration_decisions_append_only();
```

**多租戶隔離與 Gate 累計預算扣抵**：
1. **租戶強綁定**：`pricing.exploration_decisions` 透過 `(gate_id, tenant_id)` 複合外鍵直接參照 `pricing.exploration_gates(gate_id, tenant_id)`，由資料庫核心層確保決策租戶與授權 Gate 租戶嚴格一致，防止跨租戶借用 Gate 探索。
2. **總預算累計扣抵與防超支**：單筆決策之 `budget_consumed` 僅記錄該次探索消耗，Gate 之累計消耗則由 `pricing.exploration_gates.budget_consumed` 追蹤。兩者由 `trg_exploration_decisions_accrue` 在**同一次 INSERT 內**綁定：寫入一筆探索決策必然累加至其 Gate，且該 Gate 必須在決策時點有效（未撤銷、落在授權期間內），否則整筆寫入被拒。累加後 `chk_gate_budget_consumed (budget_consumed <= budget_limit)` 立即生效，因此第 N 筆使累計超出上限的決策會被資料庫回滾，多筆並行寫入亦由該列的行鎖序列化，不會出現兩筆同時通過的競態。
3. **扣抵不可回收**：`trg_exploration_decisions_append_only` 禁止對已寫入的探索決策做 UPDATE 或 DELETE。否則刪掉一筆決策就能讓累計器與逐筆紀錄不再對應——預算看似歸還，實際已花掉的探索卻已發生。

**v0.4.0 的漏洞，與這次的修法**。前一版的第 2 點是一段**寫給呼叫端看的協定**：文中要求「交易中必須原子執行 `UPDATE ... budget_consumed + :decision_budget`」，但資料庫沒有任何機制要求它真的被執行。`pricing.exploration_decisions` 的唯一預算約束是 `budget_consumed >= 0`，因此直接 `INSERT` 一批決策而不動 Gate，總預算就完全繞過——`ODP-BR-PRICE-004` 的 Hard Constraint 只存在於文件。把累加移進 trigger 之後，「逐筆決策」與「累計預算」不再是兩份需要同步的資料，而是同一次寫入的兩個面向；呼叫端也不再需要（也不得）自行執行那段 UPDATE，重複扣抵的風險一併消失。

**Gate 判定與 Bandit 介面**（新增 `modules/priceops/application/exploration.py` 與 `solver/pricing/bandit.py`）：

```python
class ExplorationNotAuthorizedError(RuntimeError):
    """Gate 未授權。呼叫端不得產生探索性價格。"""


def authorize_exploration(scope: PriceScope, *, at: datetime) -> ExplorationGrant:
    """解析授權。無有效 Gate、已過期、已撤銷或預算用罄時一律 raise。

    fail closed：任何無法確認授權的情況都視為未授權。
    """


@dataclass(frozen=True)
class BanditCandidate:
    candidate_id: str
    gate_id: str                          # 綁定授權 Gate
    sku_id: str
    store_id: str
    baseline_price: Decimal
    explored_price: Decimal
    delta_ratio: float
    algorithm: str                        # 例：'THOMPSON_SAMPLING', 'EPSILON_GREEDY', 'UCB1'
    expected_reward: float
    uncertainty: float
    estimated_exploration_cost: Decimal
    hard_constraints_satisfied: bool


class BanditPriceExplorer(Protocol):
    def generate_candidates(
        self,
        scope: PriceScope,
        grant: ExplorationGrant,
        hard_constraints: Sequence[PriceConstraint],
    ) -> Sequence[BanditCandidate]:
        """依 Gate 授權額度與硬限制產生探索性價格候選。"""
        ...
```

接在既有 `PriceOpsService`（`modules/priceops/application/pricing.py:110`）上，沿用其 `ApprovalBlockedError` 與 `MissingRollbackPlanError` 的既有模式。

**逐決策 Gate 記錄與稽核**：當價格最佳化流程啟用 Bandit 探索並採納候選價格時，系統必須將產生的決策寫入 `workflow.decisions`，並在 `pricing.exploration_decisions` 記錄該決策所依據的 `gate_id`、探索前後價格與實際扣抵之探索預算，確保所有探索決策具備完整審查軌跡與可回溯性。應用層不再自行更新 Gate 的 `budget_consumed`，該欄位由上述 trigger 獨佔維護。

**硬限制不可放寬**：探索空間是 `solver/pricing/constraints.py` 既有硬限制的**子集**。實作上探索候選價格必須先通過同一組約束檢查才能被提出——探索不是繞過 `ODP-BR-PRICE-001`（毛利底線）的路徑。

**未授權時的行為**：輸出確定性方案，並在回應中明確標示 `exploration_enabled: false`，而非靜默省略。此為 `ODP-AC-FR-012` 的驗收依據。

**API**（2 個端點）：

```
GET    /api/v1/priceops/exploration-gates?scope=      查有效授權、有效期與預算餘額
POST   /api/v1/priceops/exploration-candidates        依 Gate 授權產生探索價格候選
```

`GET` 端點供驗收者獨立查得當時的授權狀態（`ODP-AC-FR-012`）；`POST` 端點供價格最佳化流程在通過 Gate 授權後產生受約束的探索候選方案。Gate 的建立與撤銷不走 HTTP，而是經 `workflow.approvals` 的既有核准路徑，因為它需要的是核准而非 API 寫入。

**Tier 歸屬**：依 `ODP-SA-08` 第 12 節屬 Tier 4，Feature Flag 關閉時不得影響核心定價流程。

---

## 8. NetPlan — 季度甘特圖（`ODP-FR-NET-007`）

**後端無須新增資料表**。`solver/netplan/optimizer.py` 已產出季度行動清單（其無解診斷訊息 `"solver cannot produce a complete quarter action list"` 可證），`network.network_plan_actions` 已持久化每個行動的 `action_type` 與 `quarter`，`NetPlanScenario`（`modules/netplan/domain/planning.py:152`）承載方案內容。甘特圖所需的三項資料（實體、行動類型、季度）皆已存在。

本項為純呈現層落差。新增前端元件 `apps/web/features/operator/PlanGanttChart.tsx`，由既有的 `NetworkFindAreasWorkspace.tsx` 掛載（`apps/web/features/operator/` 目前為扁平檔案結構，無 `network/` 子目錄，故不另建目錄）：

| 呈現要素 | 資料來源 | 說明 |
|---|---|---|
| 橫軸 | `network.network_plans.planning_period_start/end` | 以季度為刻度 |
| 每列 | 一個規劃實體 | `network.network_plan_actions.store_id` 或 `candidate_site_id` |
| 條 | 行動期間 | 依 `action_type`（open／keep／improve／move／exit）著色 |
| 相依線 | 時序硬限制 | `ODP-FR-NET-002` 要求的時序限制，甘特圖是其自然表達 |
| 衝突標記 | Binding Constraints | `constraint_summary_json` 中的資源或時序衝突以警示色標示 |

**與 baseline 的一項落差**：`network.network_plan_actions.action_type`（`000001` 第 542 行）為 `VARCHAR(50) NOT NULL DEFAULT 'keep'`，其允許值 `open/keep/improve/move/exit` **只寫在行末註解，並無 CHECK 約束**，因此資料庫實際上接受任何字串。而 `ODP-BR-NET-002` 所述流程另提及 `TRANSFER`。本案不擴充該列舉、也不補該約束（兩者皆超出 9 項落差範圍），甘特圖依註解所列五類著色並對未知值採預設樣式，`TRANSFER` 與缺少的 CHECK 一併記於第 14 節。

**無障礙與降級**：甘特圖須提供等價的表格檢視（既有清單），不得成為唯一取得該資訊的途徑。依 `ODP-SA-08` 第 11 節，大圖層可延遲載入。

**核准脈絡**：`ODP-BR-NET-002` 要求 MOVE/EXIT 需管理層核准，甘特圖為核准畫面的一部分，須同時呈現方案的 `decision_policy_version_id`（第 3.4 節）。

---

## 9. 資料表變更彙總

新增資料表（6 張）：

| Migration | 表 | Schema 歸屬理由 | 對應 FR |
|---|---|---|---|
| `000013` | `workflow.decision_policies` | 決策治理既有歸屬；補上 `workflow.decisions.policy_version_id` 缺少的登錄表 | `FCT-005` 及全平台 |
| `000014` | `operations.forecast_feedback` | 回饋目標 `operations.alerts`／`forecast_outputs` 皆在此 | `FCT-008` |
| `000015` | `expansion.heatzone_composition` | `expansion.heatzone_scores` 在此 | `HZ-006` |
| `000016` | `asset.deal_outcomes` | `asset.valuation_runs` 在此 | `AVM-005`／`008` |
| `000017` | `pricing.exploration_gates` | `pricing` schema 既有但無表；PriceOps 無既有表可擴充 | `PRICE-006` |
| `000017` | `pricing.exploration_decisions` | 每次探索定價決策關聯之 Gate 授權與預算扣抵紀錄 | `PRICE-006` |

既有表新增欄位（4 張）：

| 既有表 | 新增欄位 | 對應 FR |
|---|---|---|
| `operations.alerts` | `forecast_output_id`、`tenant_id`、`decision_policy_version_id`、`deterioration_confirmed_at`、`disposition` | `FCT-005`、`FCT-006` |
| `expansion.heatzone_scores` | `tenant_id`、`decision_policy_version_id`、`absorbed_demand`、`remaining_demand`、`absorption_ratio`、`absorption_basis_at`、`absorption_source`、`absorbing_store_count` | `HZ-004` |
| `expansion.site_score_runs` | `tenant_id`、`decision_policy_version_id` | 第 3.4 節 |
| `network.network_plans` | `tenant_id`、`decision_policy_version_id` | 第 3.4 節 |

本案新增於既有表與政策登錄表的約束、唯一索引與外鍵：

| 對象 | 新增 | 作用 |
|---|---|---|
| `workflow.decisions` | `fk_decisions_policy_version`（`NOT VALID`） | 補上既有欄位所隱含、但從未建立的外鍵 |
| `workflow.decision_policies` | `uq_decision_policy_version_tenant` | 讓「政策的租戶」成為可被複合外鍵引用的欄位對 |
| `workflow.approvals` | `uq_approvals_decision_status` | 讓「某決策的某筆核准及其狀態」成為可被複合外鍵引用的欄位對（第 4.3 節） |
| `core.stores` | `uq_stores_store_tenant` | 讓「門市的租戶」成為可被複合外鍵引用的欄位對 |
| `operations.alerts` | `fk_alerts_decision_policy`（複合、`MATCH FULL`、`NOT VALID`） | 政策綁定必須指向真實政策列，且該政策必須屬於同一租戶 |
| `operations.alerts` | `fk_alerts_store_tenant`（`NOT VALID`） | 警示自述的租戶必須就是其門市的租戶 |
| `expansion.heatzone_scores` | `fk_heatzone_scores_decision_policy`（複合、`MATCH FULL`、`NOT VALID`） | 同上（政策部分） |
| `expansion.site_score_runs` | `fk_site_score_runs_decision_policy`（複合、`MATCH FULL`、`NOT VALID`） | 同上 |
| `network.network_plans` | `fk_network_plans_decision_policy`（複合、`MATCH FULL`、`NOT VALID`） | 同上 |
| `operations.alerts` | 唯一索引 `idx_alerts_forecast_policy` | `(forecast_output_id, decision_policy_version_id)` 的評估識別唯一性 |
| `expansion.heatzone_scores` | `chk_heatzone_absorption_complete`（強化為六欄）、`chk_heatzone_absorption_non_negative`、`chk_heatzone_absorption_consistent`、`chk_heatzone_absorption_source` | `HZ-004` 吸收結果的可驗收性與可追溯性（第 5.1 節） |

新增 trigger（2 個，皆為本案宣稱之治理規則的執行機制）：

| 對象 | Trigger | 作用 |
|---|---|---|
| `expansion.heatzone_composition` | `trg_heatzone_composition_append_only` | 唯一允許的改寫是把 `reverted_at` 由 NULL 設為時點；`DELETE` 一律拒絕（第 5.2 節） |
| `pricing.exploration_decisions` | `trg_exploration_decisions_accrue`、`trg_exploration_decisions_append_only` | 逐筆決策與 Gate 累計預算在同一次寫入內綁定，且事後不可回收扣抵（第 7 節） |

既有 dbt 模型變更（1 個）：`pipelines/dbt/models/model_ready/valuation_view.sql` 新增 `realized_transaction_price`、`realized_transaction_at` 兩個輸出欄位（第 6.3 節）。

## 10. Migration 的可重跑與回滾

`ODP-SA-08` 第 9 節要求所有 migration 須可重跑或可回滾。初版僅陳述此要求而未提供做法，本節補上，並對齊版本庫既有做法。

**編號與 Alembic 對應**。既有 SQL migration 編號至 `000012`，故本案取 `000013` 至 `000017`。每個 SQL 檔須有對應的 Alembic revision（`infra/db/migrations/versions/`，既有至 `0005`，本案為 `0006` 至 `0010`），沿用既有形制：`upgrade()` 讀取同名 SQL 檔並 `op.execute`。

**可重跑（idempotency）**。本案所有 DDL 皆採既有慣例，重跑不報錯：

| 語句類型 | 採用寫法 | 依據 |
|---|---|---|
| 建表 | `CREATE TABLE IF NOT EXISTS` | `000009`、`000011` |
| 加欄位 | `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` | `000012` |
| 建索引 | `CREATE INDEX IF NOT EXISTS` | `000012` |
| 加約束 | `DO $$ ... IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = ...) ...` | 本案新增；PostgreSQL 不支援 `ADD CONSTRAINT IF NOT EXISTS` |
| 建 trigger | `CREATE OR REPLACE FUNCTION` + `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER` | 本案新增；PostgreSQL 不支援 `CREATE TRIGGER IF NOT EXISTS`，改以先 DROP 再建達成重跑一致 |

最後兩列是本案唯一需要新寫法的部分。`ALTER TABLE ... ADD CONSTRAINT` 沒有 `IF NOT EXISTS` 形式，重跑會以 `duplicate_object` 失敗，因此必須以 `pg_constraint` 查詢包裹。第 3.4、4.2、5.1 節的約束均已依此撰寫；第 5.2 與第 7 節的 trigger 依倒數第一列撰寫，兩者的可重跑性均由第 13.1 節的 B 項實際重跑驗證。

**回滾（rollback）**。版本庫的既有回滾原則為 **expand-only**：`infra/db/migrations/versions/0005_identity_session_server_secrets.py` 的 `downgrade()` 是 `op.execute(sa.text("SELECT 1"))`，並在註解中說明「rollback 藉停用程式路徑達成，不 drop 欄位或資料」。`assisted_listing_intake/downgrade.sql` 亦明言結構性 drop 僅適用於 greenfield／staging，生產回滾改為關閉旗標並將資料轉為唯讀。

本案沿用該原則，不自創第二套回滾語意：

| 情境 | 回滾動作 | 資料處置 |
|---|---|---|
| 政策機制（`000013`） | 停用 `resolve_policy()` 呼叫點，回到既有常數門檻 | 保留登錄表與已寫入的 `decision_policy_version_id` |
| 回饋（`000014`） | 關閉三個 feedback 端點 | 保留已提交回饋；未生效者 `applied_status` 記原因碼 |
| 熱區組成（`000015`） | 停用合併批次；讀取端退回單網格評分 | 保留組成列，不設 `reverted_at`（撤銷是業務動作，不是回滾） |
| 成交回收（`000016`） | 關閉登錄端點；`valuation_view` 移除兩個輸出欄位 | 保留已回收成交結果 |
| 探索 Gate（`000017`） | Tier 4 Feature Flag 關閉 | 保留 Gate 與決策列；`revoked_at` 由業務決定，非回滾動作 |

**兩項刻意的區分**。其一，`reverted_at` 與 `revoked_at` 是業務撤銷，不是 migration 回滾；把兩者混為一談會使「回滾一次部署」意外撤銷營運決策。其二，`downgrade()` 為 no-op 的代價是 schema 無法真正倒退，這是既有原則的已知取捨，本案承接而非重新論證。

## 11. 遷移與相容性

**政策欄位的 NOT NULL 導入**分兩階段，避免既有資料阻擋 migration：

1. 第一階段（`000013`，本案）：`decision_policy_version_id` 新增為 nullable；`fk_decisions_policy_version` 與第 3.4 節的四個 `fk_*_decision_policy` 均建為 `NOT VALID`；同時以 `000013` migration SQL 直接寫入初版政策列與回填佔位列（見第 3.2 節）。此階段結束後，欄位可為 NULL，但**不可為查無此列的字串**。
2. 第二階段（後續 migration，不在本案編號內）：回填既有記錄，`VALIDATE CONSTRAINT`，再轉為 `NOT NULL`。

**第二階段的預定語句**。下列語句是第一階段的驗收出口，寫在此處以免留待實作時各自發明。它們屬後續 migration，不在本案 DDL 內，故未納入第 13.1 節的執行驗證（見第 13.3 節）：

```text
-- (a) 回填：租戶歸屬與政策綁定同一句補齊，歷史列一律指向該租戶的 retrofit 佔位列
UPDATE operations.alerts a
   SET tenant_id = s.tenant_id,
       decision_policy_version_id =
       'four-light-policy-0.0.0-retrofit:' || s.tenant_id::text
  FROM core.stores s
 WHERE a.store_id = s.store_id
   AND a.decision_policy_version_id IS NULL;
-- expansion.heatzone_scores / expansion.site_score_runs / network.network_plans
-- 依各自的租戶歸屬路徑（第 2 節事實 2）比照辦理：三者沒有 store_id 這類單一錨點，
-- 其 tenant_id 需分別經 geo_cell_id、candidate_site_id 與規劃範圍推導，屬資料工作。

-- (b) 驗證既有列，再轉為必填（兩個欄位、三條約束一起收斂）
ALTER TABLE operations.alerts VALIDATE CONSTRAINT fk_alerts_store_tenant;
ALTER TABLE operations.alerts VALIDATE CONSTRAINT fk_alerts_decision_policy;
ALTER TABLE operations.alerts ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE operations.alerts ALTER COLUMN decision_policy_version_id SET NOT NULL;
```

**進入第二階段的條件**（三者皆成立才可執行，否則 `SET NOT NULL` 會在生產環境失敗）：

1. 四張表的 `decision_policy_version_id IS NULL` 與 `tenant_id IS NULL` 計數皆為 0；
2. 產生決策的所有寫入路徑都已改走第 3.5 節的 `resolve_policy()`，不再有不帶政策的新列；
3. `VALIDATE CONSTRAINT` 在四張表的政策外鍵、以及 `operations.alerts` 的門市租戶外鍵上皆通過。

**回填語意須誠實**：既有警示是在無政策機制下產生的，回填時不得指向首版政策列，否則等於偽稱那些警示由該政策判定。回填一律指向該租戶專設的 retrofit 列（`policy_label = 'four-light-policy-0.0.0-retrofit'`，`policy_version_id = 'four-light-policy-0.0.0-retrofit:{tenant_id}'`，`policy_version = '0.0.0-retrofit'`），`change_reason` 記明其為回填佔位。歷史決策的政策歸屬不可偽造。

**首版政策列**（`policy_kind = 'forecast_alert'`，逐租戶各一列）：

| 欄位 | 值 | 說明 |
|---|---|---|
| `policy_version_id` | `four-light-policy-v1:{tenant_id}` | 依第 3.2 節命名規則，由 `chk_decision_policy_version_id_format` 強制 |
| `policy_label` | `four-light-policy-v1` | 逐字沿用既有常數 `FOUR_LIGHT_POLICY_VERSION`（第 4.1 節） |
| `policy_id` | `four-light-policy` | |
| `policy_version` | `1.0.0` | |
| `parameters.thresholds` | `-0.35`／`-0.20`／`-0.10` | 逐字對應現行程式常數 |
| `declared_inputs` | `{sitescore_gap_ratio, data_quality.staleness_days}` | 見第 4.1 節 |
| `change_reason` | 機制導入，門檻沿用常數，納入資料品質守衛 | 與第 3.2 節 seed 逐字相同 |
| `rollback_policy_version` | `four-light-policy-0.0.0-retrofit:{tenant_id}` | 首版回退至同租戶的佔位列 |

`{tenant_id}` 於 `000013` 由 `SELECT ... FROM core.tenants` 展開，故新增租戶時只需重跑該 migration（`ON CONFLICT DO NOTHING` 使既有租戶不受影響）。

**驗證的分工**：門檻遷移以「同一批預測輸入產生同一組燈號」驗收；`data_quality_guard` 為新增行為，以 `ODP-BR-FCT-003` 的獨立情境驗收。機制上線、資料品質守衛、門檻調整是三次獨立變更，不得合併驗證。

## 12. 對既有文件的影響

| 文件 | 影響 |
|---|---|
| `ODP-SD-05` | 新增 6 張表、4 張表擴充欄位；未新增任何 schema（全部落在 `000001` 既有的 `workflow`／`operations`／`expansion`／`asset`／`pricing` 內） |
| `ODP-SD-06` | 新增 9 個端點：ForecastOps feedback 3（第 4.3 節）、HeatZone 2（第 5.2 節）、AVM 2（第 6.3 節）、PriceOps 2（第 7 節） |
| `ODP-SD-08` | Alert 生命週期新增 `disposition` 狀態與評估識別；熱區合併／拆分為新狀態機 |
| `ODP-SD-11` | Precision 與提前天數為新增可觀測指標 |
| `ODP-UX-03` | 新增甘特圖畫面；移除 Feedback 既有不實文案 |
| `ODP-QA-03` | 需為 `ODP-AC-FR-008` 至 `012` 補 E2E 情境 |
| `ODP-ML-*` | AVM 標籤契約首次可解析（第 6.3 節）；`min_p80_coverage=0.70` 首次可評估 |

端點總數為 9，逐一列舉如下，供與上表核對：

| # | 端點 | 節次 | 說明 |
|---|---|---|---|
| 1 | `POST /api/v1/forecastops/feedback` | 4.3 | 建立回饋 |
| 2 | `GET /api/v1/forecastops/feedback` | 4.3 | 查詢回饋 |
| 3 | `POST /api/v1/forecastops/feedback/{id}/approve` | 4.3 | `OUTCOME_CORRECTION` 專用核准 |
| 4 | `GET /api/v1/heatzone/zones/{zone_id}/composition` | 5.2 | 查熱區組成單元清單 |
| 5 | `POST /api/v1/heatzone/zones/{zone_id}/override` | 5.2 | 人工推翻自動合併／拆分 |
| 6 | `POST /api/v1/avm/deal-outcomes` | 6.3 | 登錄成交／未成交結果 |
| 7 | `GET /api/v1/avm/valuations/{id}/calibration` | 6.3 | 查該估值的偏差與校準資訊 |
| 8 | `GET /api/v1/priceops/exploration-gates` | 7 | 查有效 Gate 授權與預算餘額 |
| 9 | `POST /api/v1/priceops/exploration-candidates` | 7 | 依 Gate 授權與硬限制產生探索價格候選 |


## 13. 設計驗證

初版未經任何執行驗證，第 3.2 節的 dataclass 因而帶有一個定義即失敗的欄位順序錯誤。本版對兩類可機械驗證的宣稱實際執行檢查，結果如下。

### 13.1 SQL：套用、可重跑與約束行為

驗證腳本：`docs/evidence/ODP-SD-AMD-001_ddl_check.py`。它從本文件抽出所有 ```` ```sql ```` 區塊（第 6.3 節的 dbt select 片段除外，該片段非獨立語句），對真實 PostgreSQL 執行：

```
uv run --no-project --python 3.12 --with pgserver \
    python docs/evidence/ODP-SD-AMD-001_ddl_check.py
```

（釘 3.12 是因為 `pgserver` 目前無 cp314 wheel。）執行結果：

| 檢查 | 結果 |
|---|---|
| A. 9 個 DDL 區塊套用於 baseline 相依樁 | 9／9 通過 |
| B. 每個區塊重跑一次（第 10 節宣稱的可重跑，含兩個 trigger 區塊） | 9／9 通過 |
| C. 新增約束、外鍵與 trigger 是否真的擋下它宣稱要擋的資料 | 102／102 符合設計 |

C 項逐條涵蓋本案每一條新增 CHECK、外鍵、唯一索引與 trigger。其中與本版（v0.5.0）新增規則直接對應者為：宣稱 `APPROVED` 但 `workflow.approvals` 沒有對應核准列被拒；指向一筆尚未核准（`pending`）的核准列被拒；`AUTO_ACCEPTED` 卻挾帶核准連結被拒；同一核准決策被第二筆回饋重用被拒；**已被回饋引用的核准列改回 `returned` 被外鍵拒**；回饋或警示自述的租戶與其門市租戶不符被拒；綁定他租戶政策、或綁了政策卻不宣告租戶被拒；政策的回退目標指向他租戶版本被拒；熱區組成列被刪除、被二次撤銷、或在撤銷的同一句改寫其他欄位被 trigger 拒；探索決策寫入未累加即超出 Gate 上限被拒（含累計器實際被移動的正向斷言）；對已撤銷或已過期 Gate 的探索決策被拒；已扣抵的探索決策被改寫或刪除被拒；吸收結果缺來源識別或門市數、來源為空字串、門市數為負被拒。

v0.4.0 已涵蓋且本版保留者包括：`CLOSED` 缺成交價、成交時點或 `deal_terms` 三鍵被拒；未成交挾帶成交價或成交條件被拒；同一 `valuation_run_id` 登錄第二筆結果被拒；`APPLIED_RECALCULATION` 只填 `recalculation_run_id` 被拒；回饋類型與生效路徑不相容被拒；已生效卻無 `applied_at` 被拒；吸收欄位只填一半、為負或與比例矛盾被拒；`policy_version_id` 未帶租戶後綴被拒。

**C 項的兩項方法要求**（v0.4.0 補上，之前兩項皆不成立）：

1. **案例隔離**。每個負向案例自帶其相依列（`asset.valuation_runs`、`geo.h3_cells`、`workflow.decisions`），不與其他案例共用。共用時，一列可能是被前一個案例留下的主鍵或唯一索引擋下，而非被該案例宣稱測試的約束擋下——案例照樣「通過」，卻什麼也沒證明。唯二刻意共用的是專門測試唯一索引的兩對案例。
2. **拒絕原因具名**。每個負向案例宣告它預期的約束、索引或 trigger 名稱，只有當 PostgreSQL 的錯誤訊息確實出現該名稱時才計為符合設計（trigger 的 `RAISE EXCEPTION` 訊息因此一律以其函式名開頭）。少數列必然同時違反兩條耦合的吸收約束（比例大於 1 必然也與其輸入不一致），這類案例宣告兩個名稱並接受其一；這仍排除了「因不相關的理由被拒」。腳本另在載入時檢查每個負向案例都有宣告名稱，否則直接中止。

腳本以非零結束碼表示任一項不符，故其結果可被外部重跑核對，不需信任本節的敘述。

**此驗證的範圍限制，據實說明**：pgserver 內建的 PostgreSQL 未附 `uuid-ossp` 與 `postgis` 兩個擴充，因此 `000001_baseline_canonical_schema.sql` 無法在該環境逐字套用——這也正是版本庫既有資料庫測試標記 `requires_live_env` 的原因。腳本改以一份與 `000001` 在主鍵與型別上相容的相依樁（涵蓋本案引用到的 15 張表，v0.5.0 新增 `workflow.approvals`，並使 `core.stores` 帶上其 `tenant_id`），並將 `uuid_generate_v4()` 接到內建的 `gen_random_uuid()`。**因此上述結果證明的是本修正案 DDL 自身的正確性，不是它與完整 baseline 的整合。** 後者需要具備 PostGIS 的 PostgreSQL 16 環境，屬部署前驗證，未在此完成。

### 13.2 Python：Alert dataclass 欄位順序

第 4.2 節的欄位順序已實際執行確認。初版第 3.2 節的寫法（必填欄位置於 `status: str = "open"` 等預設值欄位之後）在類別定義當下即拋出：

```
TypeError: non-default argument 'policy_id' follows default argument
```

本版將 `decision_policy_version_id` 置於 `opened_at` 之後、`status` 之前，可正常定義與實例化，且既有欄位的相對順序不變。

### 13.3 未驗證的部分

第 4.1、5.1、6.3、7 節的 Python 介面與 dbt 變更均為設計敘述，尚無實作可執行，故未驗證。第 6.3 節提出的驗收方式（對 `model_ready.valuation_view` 取欄位清單確認 `realized_transaction_price` 存在）在該 view 變更落地後即可執行，本案未變更該 view，故此刻仍為未通過狀態——這是預期的，本案是設計而非實作。

第 11 節「第二階段的預定語句」（回填、`VALIDATE CONSTRAINT`、`SET NOT NULL`）屬後續 migration，不在本案 DDL 內，故以 ```` ```text ```` 標記而不被驗證腳本抽取，亦未執行。在空表上執行它們會全數通過，但那不構成任何證據——該階段的風險完全在既有資料的回填涵蓋率，而本環境沒有既有資料。它的驗證條件列於第 11 節，屬部署前驗證。

## 14. 未涵蓋事項

1. **本案未處理 `ODP-SA-07` 第 6 節與 `ODP-ML-05` 第 5 節的 Evidence Level 定義衝突**。該衝突使 `ODP-BR-AD-004` 與 `ODP-AC-BR-005` 無法達成，屬 C3 級，需 ADR 裁定後才能設計。
2. **`ODP-BR-LST-001` 的 fail-open 缺陷**（`modules/listing/application/promotion.py:272-303`，缺資料時給予滿分信心與預設熱區）不在本案 9 項範圍內，但其嚴重度高於本案多數項目，建議以獨立缺陷單優先處理。
3. **稽核表缺資料庫層寫入限制**（有雜湊鏈可偵測竄改，無 `REVOKE UPDATE/DELETE`，RLS 僅存在於 assisted-listing-intake 子系統），影響 `ODP-BR-OPS-004`，屬平台級安全設計，需獨立評估。
4. **兩代資料表的租戶模型仍未統一**（第 2 節事實 2）：`000001` 的模組表無 `tenant_id`，`000009` 之後的表有。v0.5.0 已為本案綁定政策的四張表補上 `tenant_id` 與租戶一致性外鍵（第 3.4 節），因為沒有它，逐租戶政策的隔離在資料庫層無法成立；但這只涵蓋本案觸及的四張表，`000001` 其餘模組表（如 `operations.interventions`、`network.network_plan_actions`）仍無租戶欄位。**全面統一租戶模型、以及第 11 節第二階段中三張無 store 錨點的表如何回填租戶，屬平台級資料設計**，需獨立評估，不宜夾帶在本案內完成。
5. **`network.network_plan_actions.action_type` 無 CHECK 約束，且註解列舉缺 `TRANSFER`**（第 8 節）。該欄位目前接受任意字串，允許值僅存在於行末註解中。屬 NetPlan 領域範圍，不在本案 9 項落差內；但同型問題（以註解代替約束）正是本案第 6.2 節在 `asset.deal_outcomes` 上刻意避免的，建議一併納入後續資料完整性盤點。
6. 本案所有設計**未經執行期驗證**——三個環境目前無應用工作負載運行。設計落地後須以實際執行證明其行為。
