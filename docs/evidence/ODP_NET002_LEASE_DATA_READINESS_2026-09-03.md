# NET-002 per-option 租約檔期與解約金資料來源查證報告

- 日期：2026-09-03
- 任務：`ODP-NET002-LEASE-DATA-READINESS-001`
- 階段：ODP Remediation · W0 Evidence（第 0 批：資料源確認）
- 基準：`origin/dev` @ `6b893fd3`
- 需求依據：`ODP-FR-NET-002`（系統必須考量資本、租約、施工、設備、人力、覆蓋、稀釋與時序硬限制）
- 狀態：`BLOCKED_BY_EVIDENCE`
- 審查者：Codex2
- 負責人：Antigravity5
- 關聯文件：
  - [修正計畫](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md)（第 0 批、第 6 批）
  - [待裁決事項](../plans/ODP_OPEN_DECISIONS_2026-09-03.md)（第 12 項）
  - [NetPlan 硬限制類別設計](../design/ODP_NETPLAN_CONSTRAINT_CLASSES_2026-09-01.md)
  - [五個結構性成因處理結果](ODP_STRUCTURAL_REMEDIATION_2026-09-01.md)
  - [列舉型需求清單](../../delivery_toolchain/governance/set_valued_requirements.json)

---

## 一、執行摘要 (Executive Summary)

本查證針對 `ODP-FR-NET-002` 八類硬限制中之「**租約 (LEASE)**」限制，全面清查全樹關聯資料庫 Schema、dbt View、API / Connector、NetPlan Domain Model 與 Solver 求解路徑。

### 查證核心結論：`BLOCKED_BY_EVIDENCE`

1. **候選新址 (Candidate Sites / `OPEN`)**：
   - 僅 `expansion.listings` 具備自外部租屋爬蟲／XLSX 匯入之 `available_from: DATE`（起租日，選填）。
   - **完全缺乏**簽約檔期截止日 (`available_to` / `signing_deadline`)、租約期限約束 (`lease_term_years`)、免租裝潢期 (`fitout_grace_days`)、押金期數與房東簽約可行性評估。
   - 在 NetPlan 領域層中，`modules/netplan/domain/planning.py::CandidateSiteInput` 與 `solver/netplan/model.py::ActionOption` 均未引入任何租約檔期或簽約約束欄位。
2. **既有門市 (Existing Stores / `KEEP`, `IMPROVE`, `MOVE`, `EXIT`)**：
   - 關聯資料庫 `core.stores`（見 `infra/db/migrations/000001_baseline_canonical_schema.sql:75`、`000002_data_domain_canonical_entities.sql:75`）僅記錄 `opened_on`、`closed_on`、`store_status` 與 `ownership_type`。
   - **全系統完全沒有門市租約合約表 (No Lease Contract Entity)、無租約到期日 (`lease_expiry_date`)、無續約權條件、無月租金合約歷史**。
   - 解約金 (`termination_cost` / `exit_cost`) 在 `modules/netplan/domain/planning.py::ExistingStoreInput:95` 中為手動／場景輸入且**預設為 `exit_cost: float = 0.0`**。全樹無任何 ERP、合約管理系統 (CLM) 或租賃會計系統介面提供真實門市解約違約金。
3. **缺席 (Missing) vs 量測為零 (Measured Zero) 的核心風險**：
   - 現行程式將未量測之 `exit_cost` 預設為 `0.0`，導致 Solver 在評估 `EXIT`（關店）時誤將關店視為「零解約代價」，製造偏向關店的嚴重偏差計畫。
   - 同理，未評估租約到期日的 `KEEP` 選項被預設為永久可行，忽視租約屆期無法續約的實體營運中斷風險。
4. **處置決策 (Disposition)**：
   - 依據「先有量測才有限制」與「不得以裝飾性限制掩蓋資料缺口」之原則，**維持 `ConstraintClass.LEASE` 於 `unmodelled_constraint_classes`**（PR #1133 架構）。
   - 記錄正式 `BLOCKED_BY_EVIDENCE` 處置，設定重啟觸發條件 (Reopen Triggers)，並設計標準化之租約可行性檢查介面 (Admissibility Interface)，不虛構資料。

---

## 二、Per-Option 欄位 Lineage、Owner、Freshness 與 Production Producer 清查

NetPlan 規劃實體包含「既有門市 (`existing_store`)」與「候選新址 (`candidate_site`)」，對應五種 `NetworkAction`（`OPEN`, `KEEP`, `IMPROVE`, `MOVE`, `EXIT`）。以下為各動作選項在全鏈路中之租約相關欄位清查：

| 動作選項 (`ActionOption`) | 規劃實體類型 | 租約相關欄位需求 | 現有資料層實體與欄位 | 欄位 Lineage 與 Production Producer | 擁有者 (Owner) | 資料新鮮度 (Freshness) | 資料就緒狀態 |
|---|---|---|---|---|---|---|---|
| **`OPEN`** | `candidate_site` | 1. 起租可得日 (`available_from`)<br>2. 簽約檔期截止日 (`available_to` / `signing_deadline`)<br>3. 租期條件 (`lease_term_years`, 押金) | `expansion.listings.available_from` (DATE, Nullable)<br>`expansion.listings.rent_amount` (NUMERIC) | `modules/external_data/providers/` (591, 永慶爬蟲 / XLSX) → `modules/listing/application/pipeline.py` → `expansion.listings` | ExpansionOps / Data Platform | 批次爬蟲／匯入（每日至每週） | **部分存在但斷鏈**：`available_from` 與 `rent_amount` 存在於 Listing 層，但未傳入 `CandidateSiteInput`；簽約截止日與租期條件**完全不存在**。 |
| **`KEEP`** | `existing_store` | 1. 租約到期日 (`lease_expiry_date`)<br>2. 續約可行性 (`renewal_option_flag`, 房東意願)<br>3. 租金調幅 (`rent_escalation_rate`) | **無** (`core.stores` 僅有 `opened_on`, `closed_on`, `effective_to`) | 無生產資料源；`modules/netplan/domain/planning.py::build_scenario_options` 無租約欄位輸入 | Store Ops / Real Estate Finance | N/A（無資料表） | **完全不存在 (`MISSING`)**：系統無門市合約檔，無法驗證門市在規劃期內是否租約到期或可否續約。 |
| **`IMPROVE`** | `existing_store` | 1. 剩餘租期 (`remaining_lease_months`)<br>2. 裝修許可 (`alteration_permitted`)<br>3. 投資回收期對比租期 | **無** (`ExistingStoreInput` 僅有 `improve_cost`, `improve_risk`) | 無生產資料源；`modules/netplan/domain/planning.py::ExistingStoreInput` 手動填入 `improve_cost` | Store Ops / Engineering | N/A（無資料表） | **完全不存在 (`MISSING`)**：無法檢查改裝投資金額是否能在剩餘租期內完成攤提與回收。 |
| **`MOVE`** | `existing_store` | 1. 既有店提早解約金 (`termination_cost`)<br>2. 既有店復原費用 (`restoration_cost`)<br>3. 新址起租日與重疊檔期 | **無** (`ExistingStoreInput` 僅有 `move_cost`, `move_risk`) | 無生產資料源；`move_cost` 為單一粗估數值，未拆解新店押金與舊店解約金 | ExpansionOps / Finance | N/A（無資料表） | **完全不存在 (`MISSING`)**：無法計算搬遷時新舊店交接租金重疊與舊約終止違約金。 |
| **`EXIT`** | `existing_store` | 1. 提前解約違約金 (`early_termination_penalty`)<br>2. 押金沒收金額 (`deposit_forfeiture`)<br>3. 原狀復原與拆除清運費 (`restoration_cost`) | `ExistingStoreInput.exit_cost` (預設 `0.0`)<br>`network.network_plan_actions.capital_required` | `modules/netplan/domain/planning.py:95` 預設 `0.0`；無任何資料表或 ERP 合約介面支援 | Store Ops / Legal & Finance | N/A（常數預設值） | **完全不存在 (`MISSING`) 且預設為 0.0**：將未量測之解約成本視為 0.0，嚴重違反 Fail-Closed 原則。 |

---

## 三、缺席 (Missing) 與量測為零 (Measured Zero) 之語意界定與 Fail-Closed 防護

本查證依據 Pantheon 治理原則：「**缺席必須與量測結果始終可區分，不得把缺席表示為一個正常的值**」。

### 1. 概念界定

| 狀態 | 程式與資料庫表達 | 商業與實體世界語意 | Solver / 決策系統應有行為 |
|---|---|---|---|
| **量測為零 (`Measured Zero`)** | `exit_cost = 0.0`<br>`termination_penalty = 0.0` | 該門市合約已到期自然終止、或已取得房東無條件解約協議，**經查證確實不需支付任何解約罰金與復原費用**。 | 接受為有效成本輸入；以 0.0 成本計入資本預算。 |
| **資料缺席 (`Missing / Unmeasured`)** | `exit_cost = None`<br>`lease_expiry_date = None` | 該門市未串接租約合約資料，**不知道何時約滿，亦不知道解約要賠多少錢**。 | **Fail-Closed 攔阻**：若宣告需考慮租約約束，拒絕求解或將該 Option 標記為不可行；不得當作 0.0 成本。 |

### 2. 現行程式碼的危險預設模式

在 `modules/netplan/domain/planning.py` 中：

```python
# modules/netplan/domain/planning.py:88-102
@dataclass(frozen=True)
class ExistingStoreInput:
    store_id: str
    baseline_gross_margin: float
    improve_gross_margin_uplift: float = 0.0
    improve_cost: float = 0.0
    move_gross_margin_uplift: float = 0.0
    move_cost: float = 0.0
    exit_cost: float = 0.0          # ⚠️ 預設為 0.0：把未量測的解約金當作「免費關店」
    keep_risk: float = 0.1
    improve_risk: float = 0.25
    move_risk: float = 0.35
    exit_risk: float = 0.2
    current_capacity: int = 1
    source_snapshot_ids: tuple[str, ...] = ()
```

當 `build_scenario_options()` 產生 `NetworkAction.EXIT` 選項時：

```python
# modules/netplan/domain/planning.py:449-456
ActionOption(
    entity_id=store.store_id,
    action=NetworkAction.EXIT,
    expected_gross_margin=0.0,
    budget_cost=store.exit_cost,    # ⚠️ 若未提供，直接帶入 0.0
    risk_score=store.exit_risk,
    capacity_delta=-store.current_capacity,
    source_snapshot_ids=store.source_snapshot_ids,
)
```

### 3. 危害分析

若求解器在未具備租約資料時啟用租約或預算限制：
- 一家毛利微負但尚有 3 年合約（違約金高達數百萬元）的門市，會被 Solver 視為「關店成本為 0.0 元」，並立即建議 `EXIT`。
- 企業決策者採用此方案後，進入實體執行時才會發現需承受鉅額違約金與押金沒收，形成重大財務與法律意外。
- **結論：在無真實合約資料前，絕不能把 `exit_cost` 預設為 0.0，亦不能宣稱已考量 LEASE 限制。**

---

## 四、真實 Contract / Schema 證據與 BLOCKED_BY_EVIDENCE 證明

本報告對程式庫進行了全樹查證，以下列出核心檔案之真實狀態證據：

### 1. 關聯資料庫遷移檔案 (Database Schema)

- **`infra/db/migrations/000001_baseline_canonical_schema.sql`**：
  - 行 75–95 (`core.stores`)：僅有 `store_id`, `tenant_id`, `brand_id`, `source_store_id`, `store_name`, `store_status`, `ownership_type`, `store_format_code`, `opened_on`, `closed_on`, `address_id`, `region_code`, `service_start_time`, `service_end_time`, `effective_from`, `effective_to`, `is_current`。**無任何租約相關欄位**。
  - 行 262–283 (`expansion.listings`)：包含 `available_from DATE`、`rent_amount NUMERIC(12,2)`，但無租期年限、無簽約截止日、無解約違約條款。
  - 行 537–549 (`network.network_plan_actions`)：僅記錄 `action_type`, `quarter`, `expected_gm_delta`, `capital_required`, `risk_level`。**無租約約束標記欄位**。
- **`infra/db/migrations/000002_data_domain_canonical_entities.sql`**：
  - 行 75–95 (`core.stores`) 與行 207–229 (`expansion.listings`) 同上，全樹無 `core.store_leases` 或 `core.lease_contracts` 資料表。

### 2. dbt 數據轉換層 (dbt Model-Ready Views)

- **`pipelines/dbt/models/model_ready/network_plan_view.sql`**：
  - 行 34：`plans.constraint_summary_json -> lease as lease_constraint`
  - 行 45：`plans.constraint_summary_json as hard_constraint_flags`
  - 查證顯示：`network_plan_view` 僅從 `plans.constraint_summary_json` 讀取 JSON 摘要字串，其底層並沒有任何來自門市合約或新址租期的實體欄位聚合。

### 3. Solver 與優化模型層 (NetPlan Solver)

- **`solver/netplan/model.py`**：
  - 行 42–52 (`ConstraintClass`)：定義了 `LEASE = "LEASE"`。
  - 行 55–86 (`ActionOption`)：包含 `construction_days`, `equipment_units`, `labour_headcount`, `coverage_delta`, `dilution_zone_id`, `period_key`，但**完全無 `lease_available_from`, `lease_available_to`, `termination_penalty` 欄位**。
  - 行 151–171 (`NetPlanConstraints.modelled_classes`)：
    ```python
    # solver/netplan/model.py:155-159
    # Lease and sequencing never appear -- the model has no lease admissibility
    # check and no time dimension, so a plan from this solver has never been
    # tested against either, and saying so is the point of this method.
    ```
  - `modelled_classes()` 明確將 `LEASE` 排除，並由 `unmodelled_classes()` 在每次求解結果中向外宣告 `ConstraintClass.LEASE` 為未建模類別。

### 4. 治理與需求檢驗層 (Governance Manifest)

- **`delivery_toolchain/governance/set_valued_requirements.json`**：
  - 行 41–45 (`ODP-FR-NET-002` -> `LEASE`)：
    ```json
    {
      "name": "LEASE",
      "status": "absent",
      "note": "Needs a per-option admissibility check (can this lease be signed or broken in the plan window, and at what penalty). No such data source is wired to the solver. Reported through ConstraintClass.LEASE in unmodelled_constraint_classes on every solve."
    }
    ```

---

## 五、後續可行性檢查介面設計 (Future Feasibility Interface)

若未來企業引入合約管理系統 (CLM) 或租賃資料庫，不得直接將欄位硬塞入 Solver 內部做假計算，應透過明確定義之「**租約可行性評估介面 (Lease Admissibility Evaluator)**」進行前置過濾或成本修正。

以下為後續實作之標準介面定義（不虛構資料，僅定義契約形狀）：

```python
"""Proposed interface for NetPlan Lease Admissibility Evaluation (ODP-FR-NET-002)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol, Sequence


class LeaseAdmissibilityStatus(StrEnum):
    FEASIBLE = "FEASIBLE"                     # 檔期與條件完全吻合，可正常納入規劃
    INFEASIBLE_WINDOW = "INFEASIBLE_WINDOW"   # 簽約檔期與規劃執行季度衝突（太早或太晚）
    INFEASIBLE_LANDLORD = "INFEASIBLE_LANDLORD" # 房東已發不續約通知或拒絕改裝
    PENALTY_REQUIRED = "PENALTY_REQUIRED"     # 可解約但須支付具體量測之違約金
    UNMEASURED = "UNMEASURED"                 # 缺合約資料（Fail-Closed 拒絕）


@dataclass(frozen=True)
class StoreLeaseContract:
    store_id: str
    lease_start_date: date
    lease_expiry_date: date
    monthly_rent: float
    deposit_amount: float
    break_clause_allowed: bool
    break_notice_months: int
    early_termination_penalty: float | None    # None 代表未知（不得當作 0.0）
    restoration_cost_estimate: float | None   # 原狀復原費用
    renewal_option_flag: bool
    landlord_consent_status: str              # APPROVED / PENDING / REJECTED / UNKNOWN
    source_contract_id: str
    source_snapshot_id: str


@dataclass(frozen=True)
class CandidateSiteLeaseTerms:
    candidate_site_id: str
    available_from: date
    signing_deadline: date | None             # 簽約截止日
    target_lease_term_years: int
    expected_monthly_rent: float
    security_deposit: float
    fitout_grace_days: int                    # 免租裝潢期
    zoning_commercial_permitted: bool         # 使用分區允許商業
    source_snapshot_id: str


@dataclass(frozen=True)
class LeaseAdmissibilityResult:
    entity_id: str
    action: str
    status: LeaseAdmissibilityStatus
    adjusted_budget_cost: float | None        # 計入量測解約金／押金後之成本
    reason: str
    evidence_snapshot_id: str


class LeaseAdmissibilityChecker(Protocol):
    """Protocol for checking lease feasibility before NetPlan optimization."""

    def evaluate_existing_store(
        self,
        store: StoreLeaseContract | None,
        action: str,
        planning_period_start: date,
        planning_period_end: date,
    ) -> LeaseAdmissibilityResult:
        """Evaluate feasibility of KEEP/IMPROVE/MOVE/EXIT on an existing store."""
        ...

    def evaluate_candidate_site(
        self,
        site: CandidateSiteLeaseTerms | None,
        planning_period_start: date,
        planning_period_end: date,
    ) -> LeaseAdmissibilityResult:
        """Evaluate feasibility of OPEN on a candidate site."""
        ...
```

---

## 六、決策記錄與重啟觸發條件 (Disposition & Reopen Triggers)

依據 [待裁決事項](../plans/ODP_OPEN_DECISIONS_2026-09-03.md) 與 [修正計畫](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md) 規範，本項查證產出正式 disposition 記錄：

```yaml
disposition_record:
  task_id: "ODP-NET002-LEASE-DATA-READINESS-001"
  requirement_id: "ODP-FR-NET-002"
  member: "LEASE"
  current_status: "BLOCKED_BY_EVIDENCE"
  previous_status: "absent (manifest index only)"
  decider: "Architecture & Data Governance Board (Codex / Antigravity5)"
  decision_date: "2026-09-03"
  scope: "NetPlan Solver (pywraplp & CP-SAT), NetPlan Domain, Operator UI & OpsBoard"
  rationale: |
    查證確認生產系統無門市合約檔 (core.stores 無 lease 到期日、解約金、續約權)，
    候選新址僅有外部爬蟲之 available_from，無簽約截止日與租期條件。
    若在無資料情況下強行實作限制，只能依賴常數或將 None 當作 0.0，
    將製造裝飾性限制並導致誤將關店視為零成本之重大決策風險。
    因此維持 ConstraintClass.LEASE 於 unmodelled_constraint_classes，
    以誠實宣告替代虛構限制。
  risk_owner: "Network Planning Product Owner & Retail Operations Finance Lead"
  review_date: "2026-12-01"
  expiry_date: "2027-03-01"
  reopen_triggers:
    - trigger_1: "企業建立或導入門市租約合約主檔 (如 core.store_leases 或 CLM 系統)，提供每家門市之 lease_expiry_date、解約違約金公式與續約狀態。"
    - trigger_2: "擴展 expansion.listings 與 candidate_sites 資料模型，納入經過驗證之簽約檔期視窗 (available_from 至 signing_deadline) 與免租期。"
    - trigger_3: "財務與法務部門建立門市提前解約違約金試算服務 (Termination Cost Calculator) 並通過生產驗證。"
```

---

## 七、驗證收據與測試狀態

在文件基準與本地環境上驗證，相關治理與優化器測試均保持綠燈：

```bash
# 驗證 NetPlan 硬限制、生產限制與需求治理檢查
.venv/bin/python -m pytest -q \
  tests/integration/test_netplan_hard_constraints.py \
  tests/integration/test_netplan_production_constraints.py \
  delivery_toolchain/governance/test_check_requirement_members.py \
  delivery_toolchain/governance/test_check_measurement_defaults.py \
  delivery_toolchain/governance/test_generate_vocabularies.py
```

- 執行結果：`66 passed`
- 單元測試套件：`572 passed in tests/unit/`
- Delivery Toolchain 套件：`253 passed in delivery_toolchain/`
- 驗證確認：
  1. `ConstraintClass.LEASE` 在給予完整可用資源上限時，與 `SEQUENCING` 一同精確出現在 `unmodelled_constraint_classes`。
  2. `check_requirement_members` 測試確認 `ODP-FR-NET-002` 之 8 個成員中，`LEASE` 誠實記錄為 `absent` 且具備說明。
  3. 全樹無因本查證報告新增任何不實常數或虛構假資料。
