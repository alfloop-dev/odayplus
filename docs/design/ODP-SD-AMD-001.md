---
doc_id: ODP-SD-AMD-001
title: "平台與模組設計修正案 001"
version: 0.1.0
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

1. **接在既有結構上**。本案不新增平行機制。每項設計均指名其擴充的既有類別、資料表或服務，且不建立第二套做同一件事的路徑。
2. **政策與程式分離**。四燈門檻、熱區合併門檻、觀察期長度等均為政策值，一律移入 Decision Policy 物件，不得留在程式常數。
3. **回饋不覆寫**。所有回收與回饋機制（`FCT-008`、`HZ-004`、`AVM-005`）一律以獨立記錄承載，透過重算生效，不直接修改預測或決策欄位（`ODP-BR-GOV-001`）。

第 2 節為平台級機制，其餘模組設計依賴之，應優先實作。

---

## 2. 平台級：Decision Policy 機制

對應 `ODP-FR-FCT-005`，但適用範圍為全平台。`ODP-SA-07` 第 8 節已定義政策物件的欄位，本節定義其持久化、解析與綁定。

### 2.1 資料模型

新增 migration `000013_decision_policy.sql`：

```sql
CREATE TABLE governance.decision_policies (
    policy_id               TEXT        NOT NULL,
    policy_version          TEXT        NOT NULL,          -- semver
    policy_kind             TEXT        NOT NULL,          -- 'forecast_alert' | 'heatzone_merge' | ...
    tenant_id               TEXT        NOT NULL,
    effective_from          TIMESTAMPTZ NOT NULL,
    effective_to            TIMESTAMPTZ,                   -- NULL = 現行版本
    owner_role              TEXT        NOT NULL,
    approved_by             TEXT        NOT NULL,
    approved_at             TIMESTAMPTZ NOT NULL,
    input_contract          TEXT        NOT NULL,          -- 契約識別
    output_contract         TEXT        NOT NULL,
    change_reason           TEXT        NOT NULL,
    rollback_policy_version TEXT,                          -- 可回退的版本
    parameters              JSONB       NOT NULL,          -- 門檻與權重
    declared_inputs         TEXT[]      NOT NULL,          -- 實際使用的輸入清單
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (policy_id, policy_version)
);

CREATE UNIQUE INDEX idx_decision_policy_active
    ON governance.decision_policies (policy_id, tenant_id)
    WHERE effective_to IS NULL;
```

`change_reason` 與 `rollback_policy_version` 為 `ODP-SA-07` 第 8 節必填欄位，目前全域無實作，本表為其唯一承載處。

`declared_inputs` 對應 `ODP-SA-06-AMD-001` 第 3.4 節的輸入完整性要求：政策必須明示其使用哪些輸入，未列出者視為未納入。

### 2.2 版本解析

政策解析為**時點解析**而非取現行版本：以決策發生時刻查 `effective_from <= t < effective_to`。這使歷史決策可重現 —— 重跑一筆三個月前的警示，會取到當時生效的版本，而非今日版本。

政策升版採 close-and-insert：將舊版 `effective_to` 設為新版 `effective_from`，不修改舊版其餘欄位。舊版永久保留（`ODP-AC-BR-003`）。

### 2.3 與決策記錄的綁定

所有產生決策的資料表新增兩欄 `policy_id`、`policy_version`，兩者為 `NOT NULL`。這是硬性要求：**無法解析政策時應拒絕產生決策，而非以預設值產生**。

受影響資料表：`forecast_alerts`、`sitescore_recommendations`、`price_plans`、`netplan_scenarios`、`heatzone_scores`。

### 2.4 領域介面

```python
# shared/governance/decision_policy.py（新增）

@dataclass(frozen=True)
class DecisionPolicy:
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

---

## 3. ForecastOps

### 3.1 四燈改由政策產生（`ODP-FR-FCT-005`）

現行 `modules/forecastops/domain/forecasting.py:547` 的 `_alert_for()` 以字面值 `-0.35 / -0.20 / -0.10` 切分燈號。改為：

```python
def _alert_for(
    output: ForecastOutput,
    *,
    opened_at: datetime,
    policy: DecisionPolicy,          # 新增，必填
) -> Alert:
    level, reason_code, evidence = evaluate_alert_policy(output, policy)
    ...
```

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

初版政策的 `thresholds` 直接沿用現行三個常數值，`declared_inputs` 僅列 `sitescore_gap_ratio`。**這是刻意的**：先讓機制上線而不改變行為，門檻擴充為後續政策升版，兩件事分開驗證。

`data_quality_guard` 對應 `ODP-BR-FCT-003`（Stale 不得高信心），將資料品質納入政策評估而非政策外的另一道判斷。

### 3.2 Alert 生命週期擴充（`ODP-FR-FCT-006`）

現行 `Alert`（同檔 275 行）有 `opened_at`、`acknowledged_*`，但無法計算預警提前量。新增四欄：

```python
@dataclass(frozen=True)
class Alert:
    # ... 既有欄位不變 ...
    policy_id: str                                  # 新增，必填
    policy_version: str                             # 新增，必填
    deterioration_confirmed_at: datetime | None = None   # 實際惡化確認時點
    disposition: AlertDisposition | None = None          # 結案分類
```

```python
class AlertDisposition(StrEnum):
    TRUE_POSITIVE = "TRUE_POSITIVE"      # 惡化確實發生
    FALSE_POSITIVE = "FALSE_POSITIVE"    # 未發生惡化
    KNOWN_CONTEXT = "KNOWN_CONTEXT"      # 有已知外部因素，不計入
    UNRESOLVED = "UNRESOLVED"            # 觀察期未滿
```

**提前天數** = `deterioration_confirmed_at - opened_at`，僅在 `disposition == TRUE_POSITIVE` 時有效。

**Precision** = `TRUE_POSITIVE / (TRUE_POSITIVE + FALSE_POSITIVE)`，分母排除 `KNOWN_CONTEXT` 與 `UNRESOLVED`。排除 `KNOWN_CONTEXT` 是必要的：因裝修而下滑的門市被判紅燈，模型並沒有錯。

`deterioration_confirmed_at` 由批次作業回填 —— 以警示開啟後的實績確認是否跨越惡化門檻，該門檻同樣為政策值。

### 3.3 Feedback 機制（`ODP-FR-FCT-008`）

新增 migration `000014_forecast_feedback.sql`：

```sql
CREATE TABLE forecastops.feedback (
    feedback_id         TEXT        PRIMARY KEY,
    tenant_id           TEXT        NOT NULL,
    store_id            TEXT        NOT NULL,
    feedback_kind       TEXT        NOT NULL,   -- CONTEXT_ANNOTATION | OUTCOME_CORRECTION | ALERT_DISPOSITION
    target_alert_id     TEXT,                   -- ALERT_DISPOSITION 時必填
    effective_from      DATE        NOT NULL,   -- 影響期間
    effective_to        DATE        NOT NULL,
    reason_code         TEXT        NOT NULL,
    note                TEXT,
    submitted_by        TEXT        NOT NULL,
    submitted_at        TIMESTAMPTZ NOT NULL,
    approval_status     TEXT        NOT NULL,   -- AUTO_ACCEPTED | PENDING | APPROVED | REJECTED
    approved_by         TEXT,
    approved_at         TIMESTAMPTZ,
    applied_effect      TEXT,                   -- 實際生效方式；未生效時記原因碼
    correlation_id      TEXT        NOT NULL
);
```

**三類回饋的處理路徑**：

| 類型 | 核准 | 生效方式 |
|---|---|---|
| `CONTEXT_ANNOTATION` | 自動接受 | 該期間標記為排除區間，不進入訓練集與 Precision 分母 |
| `OUTCOME_CORRECTION` | 需 Data Owner | 核准後修正 canonical 實績並觸發重算；未核准時不生效 |
| `ALERT_DISPOSITION` | 自動接受 | 寫入對應 Alert 的 `disposition`，關閉警示 |

**不覆寫原則**：三者皆不修改預測值。`OUTCOME_CORRECTION` 修改的是實績（canonical 資料），修改後由重算產生新預測，符合 `ODP-BR-GOV-001`。

**API**：

```
POST   /api/v1/forecastops/feedback          建立回饋
GET    /api/v1/forecastops/feedback?store_id= 查詢
POST   /api/v1/forecastops/feedback/{id}/approve   OUTCOME_CORRECTION 專用
```

需 `forecastops:write` 權限；`OUTCOME_CORRECTION` 的核准另需 `data:approve`。

**UI 前置處理**：在本機制上線前，`packages/domain-types/src/frontend-contracts.ts:292` 與 `packages/ui-domain/src/components.tsx:136` 的字串 `"Feedback written to label registry"` 必須移除或改為未啟用狀態。該文案目前對操作者作出不實陳述。

---

## 4. HeatZone Radar

### 4.1 需求吸收閉環（`ODP-FR-HZ-004`）

`HeatZoneV3Input`（`modules/heatzone/v3/contract.py:54`）現有欄位均為單一網格的靜態屬性，無實績輸入。新增：

```python
@dataclass(frozen=True)
class HeatZoneV3Input:
    # ... 既有欄位不變 ...

    # 需求吸收（新增）
    absorbing_store_count: int = 0              # 該單元內已開業且滿觀察期的門市數
    absorbed_demand: float = 0.0                # 依實績計算的已吸收需求量
    absorption_basis_at: datetime | None = None # 吸收量的計算基準時點
    absorption_source: str = ""                 # 實績來源識別（可追溯）
```

輸出 `HeatZoneV3ScoreResult` 新增 `remaining_demand` 與 `absorption_ratio`，並使 `unmet_demand_score` 改以 `remaining_demand` 為基礎，而非原始需求。

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

### 4.2 熱區合併與拆分（`ODP-FR-HZ-006`）

新增 migration `000015_heatzone_composition.sql`：

```sql
CREATE TABLE heatzone.zone_composition (
    zone_id             TEXT        NOT NULL,   -- 合併後熱區識別碼
    tenant_id           TEXT        NOT NULL,
    member_cell_id      TEXT        NOT NULL,   -- 組成單元
    composition_kind    TEXT        NOT NULL,   -- MERGED | SPLIT_CHILD | ATOMIC
    parent_zone_id      TEXT,                   -- SPLIT_CHILD 時指向原熱區
    decided_by          TEXT        NOT NULL,   -- 'system' 或操作者
    decided_at          TIMESTAMPTZ NOT NULL,
    policy_id           TEXT        NOT NULL,
    policy_version      TEXT        NOT NULL,
    override_reason     TEXT,                   -- 人工推翻時必填
    reverted_at         TIMESTAMPTZ,            -- 撤銷時點；NULL = 生效中
    PRIMARY KEY (zone_id, member_cell_id)
);
```

**識別碼規則**：合併熱區的 `zone_id` **不得**重用任一組成單元的 `cell_id`，避免下游將合併體誤認為原單元。格式為 `MZ-{hash}`。

**鄰接判定**：以 H3 k-ring（k=1）為預設。跨行政區界是否可合併由 `policy.parameters["allow_cross_admin_boundary"]` 控制，不硬編。

**治理**：屬 Ranking Policy 層級（`ODP-SA-07` 第 2 節）。自動合併可由展店 Owner 推翻，推翻須填 `override_reason` 與 `decided_by`。所有合併與拆分可撤銷（設 `reverted_at`），不實體刪除。

**API**：

```
GET    /api/v1/heatzone/zones/{zone_id}/composition   查組成
POST   /api/v1/heatzone/zones/{zone_id}/override      人工推翻（需理由）
```

---

## 5. DealRoom AVM — 成交結果回收（`ODP-FR-AVM-005`／`008`）

既有 `LiquidityTrainingRecord`（`modules/avm/domain/liquidity.py:9`）已承載 `duration_days`（成交期間）與 `sold`（成交與否），是正確的擴充位置。四項要素中缺三項。

新增 migration `000016_avm_deal_outcome.sql`：

```sql
CREATE TABLE avm.deal_outcomes (
    deal_outcome_id     TEXT        PRIMARY KEY,
    tenant_id           TEXT        NOT NULL,
    valuation_id        TEXT        NOT NULL,   -- 對應的估值
    listing_id          TEXT        NOT NULL,
    outcome_kind        TEXT        NOT NULL,   -- CLOSED | WITHDRAWN | EXPIRED
    -- 成交（outcome_kind = CLOSED 時必填）
    settlement_price    NUMERIC(18,2),
    settlement_date     DATE,
    duration_days       INTEGER     NOT NULL,   -- 上架至結案天數
    -- 未成交（outcome_kind <> CLOSED 時必填）
    no_deal_reason_code TEXT,                   -- PRICE_GAP | CONDITION | FINANCING | WITHDRAWN_BY_OWNER | OTHER
    no_deal_note        TEXT,
    -- 交易條件
    deal_terms          JSONB,                  -- 付款方式、交屋期、附帶條件
    -- 溯源
    recorded_by         TEXT        NOT NULL,
    recorded_at         TIMESTAMPTZ NOT NULL,
    source_authority    TEXT        NOT NULL,   -- 資料來源權威
    correlation_id      TEXT        NOT NULL
);
```

**與估值的綁定**：`valuation_id` 為必填。成交結果必須能對應到當時的估值輸出（Fair／Reserve／Asking 三價），否則無法計算偏差。

**校準用途**：新增 `modules/avm/application/calibration.py`：

```python
def compute_valuation_error(outcome: DealOutcome, valuation: Valuation) -> ValuationError:
    """計算估值偏差。CLOSED 才計算價格偏差；未成交只計入 Coverage 統計。"""
```

估值區間的 Coverage 檢查（`ODP-SA-08` 第 7 節 Calibration）以此為輸入：統計實際成交價落在 P10–P90 區間內的比率，理想值為 80%。

**與現有 liquidity 模型的關係**：`LiquidityTrainingRecord` 的 `duration_days` 與 `sold` 改由本表推導，不另行維護第二份來源。

**API**：

```
POST   /api/v1/avm/deal-outcomes              登錄成交結果
GET    /api/v1/avm/valuations/{id}/calibration 查該估值的偏差
```

需 `avm:write`；`settlement_price` 屬敏感財務資料，讀取需 `finance:view` 並依 `ODP-BR-OPS-002` 記錄匯出。

---

## 6. PriceOps — Bandit 與其 Gate（`ODP-FR-PRICE-006`）

**交付綁定**：本節的 Gate 與探索機制必須同批上線。先探索後補 Gate 會使 `ODP-BR-PRICE-004`（Hard Constraint）在期間內成為破口。

新增 migration `000017_price_exploration_gate.sql`：

```sql
CREATE TABLE priceops.exploration_gates (
    gate_id             TEXT        PRIMARY KEY,
    tenant_id           TEXT        NOT NULL,
    scope_brand_id      TEXT,
    scope_store_group   TEXT,
    scope_sku_group     TEXT,
    budget_limit        NUMERIC(18,2) NOT NULL,   -- 探索預算上限
    budget_consumed     NUMERIC(18,2) NOT NULL DEFAULT 0,
    effective_from      TIMESTAMPTZ NOT NULL,
    effective_to        TIMESTAMPTZ NOT NULL,     -- 必填，不可無限期
    approved_by         TEXT        NOT NULL,
    rollback_condition  TEXT        NOT NULL,
    revoked_at          TIMESTAMPTZ,
    policy_id           TEXT        NOT NULL,
    policy_version      TEXT        NOT NULL
);
```

**Gate 判定**（新增 `modules/priceops/application/exploration.py`）：

```python
class ExplorationNotAuthorizedError(RuntimeError):
    """Gate 未授權。呼叫端不得產生探索性價格。"""

def authorize_exploration(scope: PriceScope, *, at: datetime) -> ExplorationGrant:
    """解析授權。無有效 Gate、已過期、已撤銷或預算用罄時一律 raise。

    fail closed：任何無法確認授權的情況都視為未授權。
    """
```

接在既有 `PriceOpsService`（`modules/priceops/application/pricing.py:110`）上，沿用其 `ApprovalBlockedError` 與 `MissingRollbackPlanError` 的既有模式。

**硬限制不可放寬**：探索空間是 `solver/pricing/constraints.py` 既有硬限制的**子集**。實作上探索候選價格必須先通過同一組約束檢查才能被提出 —— 探索不是繞過 `ODP-BR-PRICE-001`（毛利底線）的路徑。

**未授權時的行為**：輸出確定性方案，並在回應中明確標示 `exploration_enabled: false`，而非靜默省略。

**Tier 歸屬**：依 `ODP-SA-08` 第 12 節屬 Tier 4，Feature Flag 關閉時不得影響核心定價流程。

---

## 7. NetPlan — 季度甘特圖（`ODP-FR-NET-007`）

**後端無須變更**。`solver/netplan/optimizer.py` 已產出季度行動清單（其無解診斷訊息 `"solver cannot produce a complete quarter action list"` 可證），`NetPlanScenario`（`modules/netplan/domain/planning.py:152`）承載方案內容。

本項為純呈現層落差。新增前端元件 `apps/web/features/operator/network/PlanGanttChart.tsx`：

| 呈現要素 | 資料來源 | 說明 |
|---|---|---|
| 橫軸 | 方案期程 | 以季度為刻度 |
| 每列 | 一個規劃實體（門市或候選點） | 依 `ExistingStoreInput` / `CandidateSiteInput` |
| 條 | 行動期間 | 依 OPEN／KEEP／IMPROVE／MOVE／EXIT／TRANSFER 著色 |
| 相依線 | 時序硬限制 | `ODP-FR-NET-002` 要求的時序限制，甘特圖是其自然表達 |
| 衝突標記 | Binding Constraints | 資源或時序衝突處以警示色標示 |

**無障礙與降級**：甘特圖須提供等價的表格檢視（既有清單），不得成為唯一取得該資訊的途徑。依 `ODP-SA-08` 第 11 節，大圖層可延遲載入。

**核准脈絡**：`ODP-BR-NET-002` 要求 MOVE/EXIT 需管理層核准，甘特圖為核准畫面的一部分，須同時呈現方案的 `policy_id` 與 `policy_version`（第 2.3 節）。

---

## 8. 資料表變更彙總

| Migration | 表 | 用途 | 對應 FR |
|---|---|---|---|
| `000013` | `governance.decision_policies` | 政策物件（平台級） | `FCT-005` 及全平台 |
| `000014` | `forecastops.feedback` | 三類回饋 | `FCT-008` |
| `000015` | `heatzone.zone_composition` | 合併與拆分組成 | `HZ-006` |
| `000016` | `avm.deal_outcomes` | 成交結果回收 | `AVM-005`／`008` |
| `000017` | `priceops.exploration_gates` | 探索授權 | `PRICE-006` |

既有表新增欄位：

| 表 | 新增欄位 | 對應 FR |
|---|---|---|
| `forecast_alerts` | `policy_id`、`policy_version`、`deterioration_confirmed_at`、`disposition` | `FCT-005`、`FCT-006` |
| `heatzone_scores` | `remaining_demand`、`absorption_ratio`、`policy_id`、`policy_version` | `HZ-004` |
| `sitescore_recommendations`、`price_plans`、`netplan_scenarios` | `policy_id`、`policy_version` | 第 2.3 節 |

依 `ODP-SA-08` 第 9 節，所有 migration 須可重跑或可回滾。

---

## 9. 遷移與相容性

**政策欄位的 NOT NULL 導入**分兩階段，避免既有資料阻擋 migration：

1. 第一階段：新增為 nullable，同時建立各 `policy_kind` 的初版政策（參數沿用現行程式常數，行為不變）。
2. 第二階段：回填既有記錄為初版政策的 `policy_id`／`policy_version`，然後轉為 `NOT NULL`。

**回填語意須誠實**：既有警示是在無政策機制下產生的，回填時 `policy_version` 應標記為 `0.0.0-retrofit` 而非佯稱其為初版政策的產物。歷史決策的政策歸屬不可偽造。

**行為凍結**：初版政策的參數必須逐字對應現行程式常數（四燈為 `-0.35`／`-0.20`／`-0.10`）。機制上線與門檻調整是兩次獨立變更，分別驗證。

---

## 10. 對既有文件的影響

| 文件 | 影響 |
|---|---|
| `ODP-SD-05` | 新增 5 張表、3 張表擴充欄位；`governance` schema 為新增 |
| `ODP-SD-06` | 新增 8 個端點（feedback 3、heatzone 2、avm 2、priceops 1） |
| `ODP-SD-08` | Alert 生命週期新增 `disposition` 狀態；熱區合併／拆分為新狀態機 |
| `ODP-SD-11` | Precision 與提前天數為新增可觀測指標 |
| `ODP-UX-03` | 新增甘特圖畫面；移除 Feedback 既有不實文案 |
| `ODP-QA-03` | 需為 `ODP-AC-FR-008` 至 `012` 補 E2E 情境 |

---

## 11. 未涵蓋事項

1. **本案未處理 `ODP-SA-07` 第 6 節與 `ODP-ML-05` 第 5 節的 Evidence Level 定義衝突**。該衝突使 `ODP-BR-AD-004` 與 `ODP-AC-BR-005` 無法達成，屬 C3 級，需 ADR 裁定後才能設計。
2. **`ODP-BR-LST-001` 的 fail-open 缺陷**（`modules/listing/application/promotion.py:272-303`，缺資料時給予滿分信心與預設熱區）不在本案 9 項範圍內，但其嚴重度高於本案多數項目，建議以獨立缺陷單優先處理。
3. **稽核表缺資料庫層寫入限制**（有雜湊鏈可偵測竄改，無 `REVOKE UPDATE/DELETE`，RLS 僅存在於 assisted-listing-intake 子系統），影響 `ODP-BR-OPS-004`，屬平台級安全設計，需獨立評估。
4. 本案所有設計**未經執行期驗證** —— 三個環境目前無應用工作負載運行。設計落地後須以實際執行證明其行為。
