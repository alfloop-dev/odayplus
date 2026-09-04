# ODP-NET002-LEASE-DISPOSITION-001 — NET-002 租約 (LEASE) 硬限制處置與 Human-Authority Handback 報告

- **任務識別碼**：`ODP-NET002-LEASE-DISPOSITION-001`
- **文件路徑**：`docs/evidence/ODP_NET002_LEASE_DISPOSITION_2026-09-03.md`
- **日期**：2026-09-03
- **任務負責人**：Codex2（Helper Execution Lease: Antigravity3）
- **審查人**：Antigravity6
- **基準代碼**：`origin/dev` @ `1edb2f83`
- **關聯需求**：`ODP-FR-NET-002`（系統必須考量資本、租約、施工、設備、人力、覆蓋、稀釋與時序硬限制）
- **前置任務**：
  - `ODP-NET002-LEASE-DATA-READINESS-001`（查證 NET-002 per-option 租約檔期與解約金資料來源）
  - `ODP-REQ-DISPOSITION-GOVERNANCE-001`（建立 MUST requirement amendment／waiver 的可機讀 disposition gate）
  - `ODP-NETPLAN-DISCLOSURE-UI-E2E-001`（在 Operator UI 顯示 NetPlan 未建模限制並完成 approval E2E）
- **依據與來源**：
  - [修正計畫](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md) §第 0 批 & 第 6 批
  - [待裁決事項](../plans/ODP_OPEN_DECISIONS_2026-09-03.md) §第 12 項
  - [NetPlan 硬限制類別設計](../design/ODP_NETPLAN_CONSTRAINT_CLASSES_2026-09-01.md)
  - [結構性成因處理結果](ODP_STRUCTURAL_REMEDIATION_2026-09-01.md)
  - [NET-002 租約資料準備度查證報告](ODP_NET002_LEASE_DATA_READINESS_2026-09-03.md)
  - [需求處置與治理政策](../governance/ODP_REQUIREMENT_DISPOSITIONS.md)
  - [集合型需求治理清單](../../delivery_toolchain/governance/set_valued_requirements.json)

---

## 1. 執行摘要與處置核心判定 (Executive Summary)

本報告依據 `ODP-NET002-LEASE-DATA-READINESS-001` 之資料準備度查證事實，針對 `ODP-FR-NET-002` 八類硬限制中之「**租約 (LEASE)**」成員進行正式處置與生命週期狀態判定，並依據 Remediation Plan 規則建立需人類治理授權的 Handback Package。

### 1.1 核心處置結論：`BLOCKED_BY_EVIDENCE`

依據治理政策與防偽原則，本任務對 `ODP-FR-NET-002` 進行獨立處置判定：

```
+-------------------------------------------------------------------------------------------------------+
|                                    ODP-FR-NET-002 處置架構總覽                                       |
+----------------------+--------------------+-----------------------+-----------------------------------+
| 需求成員 (Member)    | 履約現況 (Status)  | 處置狀態 (Disposition) | 後續路徑 (Action Path)            |
+----------------------+--------------------+-----------------------+-----------------------------------+
| CAPITAL              | satisfied          | VERIFIED              | 生產求解器約束 (MIP & CP-SAT)     |
| CONSTRUCTION         | satisfied          | VERIFIED              | 生產求解器約束 (MIP & CP-SAT)     |
| EQUIPMENT            | satisfied          | VERIFIED              | 生產求解器約束 (MIP & CP-SAT)     |
| LABOUR               | satisfied          | VERIFIED              | 生產求解器約束 (MIP & CP-SAT)     |
| COVERAGE             | satisfied          | VERIFIED              | 生產求解器約束 (MIP & CP-SAT)     |
| DILUTION             | satisfied          | VERIFIED              | 實作商圈開店上限，pairwise 正式修訂|
| SEQUENCING           | absent             | DECIDED               | 正式 Waiver (無 per-period 產能)  |
| LEASE                | absent             | BLOCKED_BY_EVIDENCE   | 移交 HB-NET002-LEASE-001          |
+----------------------+--------------------+-----------------------+-----------------------------------+
```

1. **租約限制 (LEASE)**：獨立判定為 **`BLOCKED_BY_EVIDENCE`**。
   - **判定依據**：
     - **既有門市 (`core.stores`)**：關聯資料庫僅記錄門市基本狀態與起迄日，**全系統完全沒有門市租約合約表 (`core.store_leases`)、無租約到期日 (`lease_expiry_date`)、無續約權條件、無月租金合約歷史**；解約金 (`exit_cost`) 在模型中僅為手動場景輸入，無任何 ERP / CLM / 租賃會計系統提供真實門市解約罰金。
     - **候選新址 (`expansion.listings`)**：雖然 Schema 具備 `available_from: DATE` 與 `rent_amount: NUMERIC`，但外部租屋來源（591/樂屋為 `ASSISTED_ENTRY_ONLY`、好房為 `AUTH_REQUIRED`、永慶為 `POLICY_UNKNOWN`）受安全與策略閘門限制僅能人工單筆進件，合作夥伴 Feed (`listing.partner_feed`) 未簽約配置，**全系統完全缺乏具備定期新鮮度 (Freshness) 保證之自動化 Feed 生產者**；且完全缺乏簽約檔期截止日 (`available_to` / `signing_deadline`)、租期約束與免租裝潢期。
     - **搬遷雙側限制 (`MOVE`)**：搬遷需要同時比對舊店提早解約違約金與新店起租檔期重疊視窗（雙重租金與交接期），目前兩側資料均缺失。
   - **實作拒絕**：未達 `IMPLEMENTATION_READY`。嚴禁在缺少資料的情況下撰寫裝飾性限制，或將 `None` 當作 `0.0`（將未量測之解約金視為「免費關店」，製造偏向關店的嚴重偏差計畫）。
   - **誠實揭露與約束同步**：維持 `ConstraintClass.LEASE` 於 `unmodelled_constraint_classes`（MIP 函式庫求解與 CP-SAT 生產求解路徑完全一致）。在 Operator UI、PlanGanttChart、以及持久化 ApprovalRecord / receipt 中完整同步揭露，依政策阻擋或要求具名 acknowledgement。
   - **移交處置**：建立結構化 Human-Authority Handback 單（`HB-NET002-LEASE-001`），提報至 `Human/Ops`、`Architecture Board`、`Store Operations Lead` 與 `Real Estate Finance Lead`，指定下次檢視日期為 **`2026-10-01`**。

### 1.2 嚴格遵守治理防線（No AI Self-Signed Waivers）

- **AI 禁止自簽豁免**：AI 代理人嚴格遵守 `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md` 規範，絕不將未滿足成員逕自標記為 `DECIDED`，亦不假造人類簽署人。
- **維持客觀技術現況**：在 `set_valued_requirements.json` 中維持 `status: "absent"` 與 `disposition.state: "BLOCKED_BY_EVIDENCE"`，完整記錄 `evidence_needed`、`evidence_owner`、`next_review_date`、`rationale`、`reopen_trigger` 與 `formal_handback_ref`。

---

## 2. 人類授權移交單 (Human-Authority Handback Package)

```yaml
handback_id: HB-NET002-LEASE-001
requirement_id: ODP-FR-NET-002
member: LEASE
current_disposition_state: BLOCKED_BY_EVIDENCE
evidence_request_ref: docs/evidence/ODP_NET002_LEASE_DATA_READINESS_2026-09-03.md#六決策記錄與重啟觸發條件-disposition--reopen-triggers
designated_authority:
  - Store Operations Lead
  - Real Estate Expansion & Finance Lead (Site Economics)
  - Platform Architecture Board
  - Human/Ops
assigned_risk_owner: Network Planning Product Owner & Retail Operations Finance Lead
next_review_date: 2026-10-01
reopen_triggers:
  - trigger_1: "企業建立或導入門市租約合約主檔 (如 core.store_leases 或 CLM 系統)，提供每家門市之 lease_expiry_date、解約違約金公式與續約狀態。"
  - trigger_2: "建立具備生產新鮮度保證之候選新址 Feed 生產者（如完成 listing.partner_feed 簽約配置），並擴展資料模型納入簽約截止日 (signing_deadline) 與免租裝潢期。"
  - trigger_3: "財務與法務部門建立門市提前解約違約金與 MOVE 雙側檔期重疊試算服務 (Lease Admissibility Evaluator) 並通過生產驗證。"

decision_pathways:
  pathway_a_implementation:
    description: "門市租約主檔與候選新址 Feed 生產者就緒後實作租約可行性評估"
    prerequisites:
      - "建立門市租約合約資料表 (core.store_leases)，含 lease_expiry_date、early_termination_penalty 等欄位"
      - "完成 listing.partner_feed 商業簽約與自動化攝取管線配置，擴充 available_to 與 signing_deadline"
      - "於 modules/netplan 實作 LeaseAdmissibilityChecker 評估介面與雙側 MOVE 檢驗"
      - "更新 NetPlanConstraints.modelled_classes 納入 ConstraintClass.LEASE 並完成反事實測試"
    target_state: "IMPLEMENTATION_READY"

  pathway_b_formal_amendment_or_waiver:
    description: "由人類授權人簽署正式需求修訂 (Amendment) 或具期限豁免 (Waiver)"
    prerequisites:
      - "人類治理角色（Human/Ops / Architecture Board / Store Operations Lead）簽署"
      - "提供 7 大法定欄位：formal_decision_ref, decider, decision_date, scope, risk_owner, expiry, reopen_trigger"
      - "註冊於 docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md"
    target_state: "DECIDED"
```

---

## 3. 全鏈路資料源與生產者清查事實 (Traceability Evidence)

依據 `ODP_NET002_LEASE_DATA_READINESS_2026-09-03.md` 與程式庫清查，各規劃動作選項在系統全鏈路中之真實狀態如下：

| 動作選項 (`ActionOption`) | 規劃實體類型 | 租約相關欄位需求 | 現有資料層實體與欄位 | 欄位 Lineage 與 Production Producer 真實狀態 | 擁有者 (Owner) | 資料新鮮度 (Freshness) | 資料就緒狀態 |
|---|---|---|---|---|---|---|---|
| **`OPEN`** | `candidate_site` | 1. 起租可得日 (`available_from`)<br>2. 簽約檔期截止日 (`available_to` / `signing_deadline`)<br>3. 租期條件 (`lease_term_years`, 押金, 免租裝潢期) | `expansion.listings.available_from` (DATE, Nullable)<br>`expansion.listings.rent_amount` (NUMERIC) | **Schema/DTO 能力**：`assisted_intake` 與 `xlsx_import` 有欄位定義。<br>**生產者查證事實**：<br>1. 外部租屋網站（591/樂屋為 `ASSISTED_ENTRY_ONLY`、好房為 `AUTH_REQUIRED`、永慶為 `POLICY_UNKNOWN`）：僅人工單筆進件，無排程爬蟲。<br>2. `listing.partner_feed`：在 `provider_registry.py` 未簽約配置。<br>3. XLSX：`xlsx_import.py` 無寫入 `expansion.listings` 證據。 | ExpansionOps / External Data Platform | **無自動新鮮度保證**（僅人工單筆進件，非定期自動更新） | **結構有定義但無自動化生產管線與新鮮度保證，且領域層完全斷鏈**：簽約截止日與租期條件**完全不存在**。 |
| **`KEEP`** | `existing_store` | 1. 租約到期日 (`lease_expiry_date`)<br>2. 續約可行性 (`renewal_option_flag`, 房東意願)<br>3. 租金調幅 (`rent_escalation_rate`) | **無** (`core.stores` 僅有 `opened_on`, `closed_on`, `effective_to`) | 無生產資料源；`modules/netplan/domain/planning.py::build_scenario_options` 無租約欄位輸入 | Store Ops / Real Estate Finance | N/A（無資料表） | **完全不存在 (`MISSING`)**：系統無門市合約檔，無法驗證門市在規劃期內是否租約到期或可否續約。 |
| **`IMPROVE`** | `existing_store` | 1. 剩餘租期 (`remaining_lease_months`)<br>2. 裝修許可 (`alteration_permitted`)<br>3. 投資回收期對比租期 | **無** (`ExistingStoreInput` 僅有 `improve_cost`, `improve_risk`) | 無生產資料源；`modules/netplan/domain/planning.py::ExistingStoreInput` 手動填入 `improve_cost` | Store Ops / Engineering | N/A（無資料表） | **完全不存在 (`MISSING`)**：無法檢查改裝投資金額是否能在剩餘租期內完成攤提與回收。 |
| **`MOVE`** | `existing_store` → `candidate_site` (雙側實體) | 1. 既有店提早解約金 (`termination_cost`)<br>2. 既有店復原費用 (`restoration_cost`)<br>3. 新址起租日與重疊檔期視窗 (`overlap_window_days`) | **無** (`ExistingStoreInput` 僅有單一粗估 `move_cost`, `move_risk`) | 無生產資料源；全樹無舊店解約違約金與新店起租時間視窗雙側比對邏輯 | ExpansionOps / Store Ops / Finance | N/A（無資料表） | **完全不存在 (`MISSING`)**：無法計算搬遷時新舊店交接租金重疊與舊約終止違約金。 |
| **`EXIT`** | `existing_store` | 1. 提前解約違約金 (`early_termination_penalty`)<br>2. 押金沒收金額 (`deposit_forfeiture`)<br>3. 原狀復原與拆除清運費 (`restoration_cost`) | `ExistingStoreInput.exit_cost` (預設 `0.0`)<br>`network.network_plan_actions.capital_required` | `modules/netplan/domain/planning.py:97` 預設 `0.0`；無任何資料表或 ERP 合約介面支援 | Store Ops / Legal & Finance | N/A（常數預設值） | **完全不存在 (`MISSING`) 且預設為 0.0**：將未量測之解約成本視為 0.0，嚴重違反 Fail-Closed 原則。 |

---

## 4. 缺席與量測為零之語意防護與求解器行為

依據「缺席必須與量測結果始終可區分」原則：

1. **`Measured Zero`（量測為零）**：
   - 門市合約已自然期滿、或已取得房東無條件解約協議，經實質審查確認不需支付解約罰金與復原費用。
   - 接受為合法輸入（`exit_cost = 0.0`），計入資本預算。
2. **`Missing / Unmeasured`（資料缺席）**：
   - 系統無門市合約或新址檔期資料（`exit_cost = None`, `lease_expiry_date = None`）。
   - **Fail-Closed 原則**：不得將未量測之缺席當作 `0.0` 關店代價或當作可行。
3. **求解器一致性與揭露約束**：
   - MIP 求解器 (`solver/netplan/optimizer.py`) 與 CP-SAT 生產求解器 (`modules/netplan/application/production.py`) 對於相同輸入保持一致的約束類別劃分。
   - 由於目前無租約資料餵入，兩者均將 `ConstraintClass.LEASE` 置於 `unmodelled_constraint_classes` 中向外陳述。
   - 經由 `shared/governance/netplan_disclosure.py`、`modules/opsboard` 與 `apps/web/features/operator/network` 完整傳遞至 Operator UI 與審批流程，杜絕未受約束之計畫被誤判為全約束計畫。

---

## 5. 後續實作租約可行性評估介面規範 (Future Implementation Spec)

當 Human/Ops 或權威業務負責人回應 Handback Package 並解除 `BLOCKED_BY_EVIDENCE` 後，未來承接實作之任務應依循以下標準介面定義：

```python
"""Standard Protocol for NetPlan Lease Admissibility Evaluation (ODP-FR-NET-002)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol


class LeaseAdmissibilityStatus(StrEnum):
    FEASIBLE = "FEASIBLE"                     # 檔期與條件完全吻合，可正常納入規劃
    INFEASIBLE_WINDOW = "INFEASIBLE_WINDOW"   # 簽約檔期與規劃執行季度衝突（太早、太晚或重疊超標）
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
        """Evaluate feasibility of KEEP/IMPROVE/EXIT on an existing store.
        
        Fail-Closed rule: If store is None or required penalty/expiry is None,
        returns UNMEASURED.
        """
        ...

    def evaluate_move(
        self,
        source_store: StoreLeaseContract | None,
        destination_site: CandidateSiteLeaseTerms | None,
        planning_period_start: date,
        planning_period_end: date,
        max_overlap_days: int = 60,
    ) -> LeaseAdmissibilityResult:
        """Evaluate feasibility of MOVE from an existing store to a candidate site.
        
        Dual-side feasibility requirements:
        1. Source store must allow break clause with measured termination penalty.
        2. Destination site must have available_from and signing_deadline within plan period.
        3. Gap/overlap between source exit and destination availability must not exceed max_overlap_days.
        4. If either side is unmeasured (None), returns UNMEASURED (Fail-Closed).
        """
        ...

    def evaluate_candidate_site(
        self,
        site: CandidateSiteLeaseTerms | None,
        planning_period_start: date,
        planning_period_end: date,
    ) -> LeaseAdmissibilityResult:
        """Evaluate feasibility of OPEN on a candidate site.
        
        Fail-Closed rule: If site is None or signing_deadline is expired/missing,
        returns UNMEASURED or INFEASIBLE_WINDOW.
        """
        ...
```

---

## 6. 治理清單與規範對齊狀態

1. **`delivery_toolchain/governance/set_valued_requirements.json`**：
   - `ODP-FR-NET-002` 成員 `LEASE` 保持 `absent` 狀態，其 `disposition` 區塊宣告 `state: "BLOCKED_BY_EVIDENCE"`，並附帶完整法定追蹤欄位與本報告之 handback 參照（`HB-NET002-LEASE-001`）。
2. **`docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md`**：
   - §4.1 登錄正式處置記錄、證據請求單號、Handback Package 編號、風險負責人與下次檢視時點。
3. **自動化驗證**：
   - `delivery_toolchain/governance/check_requirement_members.py` 檢查維持全綠燈通過。

---

## 7. 可重現的驗證收據 (Reproducibility Receipts)

以下驗證指令於工作區執行全數通過：

```bash
# 1. 驗證集合型需求治理清單與處置檢查器全數通過
uv run --python 3.12 python delivery_toolchain/governance/check_requirement_members.py --show-dispositions --show-gaps

# 2. 執行治理檢查器單元與整合測試套件
uv run --python 3.12 pytest delivery_toolchain/governance/test_check_requirement_members.py

# 3. 執行 NetPlan 求解器硬限制、生產限制與揭露審批整合測試
uv run --python 3.12 pytest -q \
  tests/integration/test_netplan_hard_constraints.py \
  tests/integration/test_netplan_production_constraints.py \
  tests/integration/test_netplan_constraint_disclosure_approval.py \
  tests/integration/test_netplan_disclosure_ui_e2e.py

# 4. 執行 NET-002 專屬租約處置整合測試
uv run --python 3.12 pytest -q tests/integration/test_net002_lease_disposition.py
```
