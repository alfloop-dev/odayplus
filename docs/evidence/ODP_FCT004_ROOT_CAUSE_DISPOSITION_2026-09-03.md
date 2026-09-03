# ODP-FR-FCT-004: root_cause 契約處置與全樹追溯報告

- 日期：2026-09-03
- 任務：`ODP-FCT-ROOT-CAUSE-CONTRACT-001`
- 擁有者：Codex2
- 審查者：Claude2
- 狀態：`SUBMITTED_FOR_REVIEW`
- 基準：`task/ODP-FCT-ROOT-CAUSE-CONTRACT-001`
- 關聯文件：
  - [修正計畫](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md) § 4b
  - [待裁決事項](../plans/ODP_OPEN_DECISIONS_2026-09-03.md) § 8
  - [FR 查證報告](ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md) 成因一

---

## 1. 執行摘要

在 `ODP-FR-VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md` 查證中，`ODP-FR-FCT-004` 被標記為「成長階段／轉折機率／異常證據／根因候選：前三有；根因候選是一個沒有生產者的欄位」。

本任務完成以下工作：
1. **全樹 Writer / Reader 證明**：完成跨 Python domain、ForecastOps、API、Ingestion contracts、TypeScript schemas、Frontend domain types、dbt pipelines 及 PostgreSQL DDL 的 lineage 查證，證實 production backend 沒有自動化根因推導（automated root cause deduction）的 writer，也沒有依賴該推導結果的 backend reader。`RootCauseEvidenceCard` 是既有但未接後端資料的 presentation consumer，僅由 fixture 驅動，並已明示 `RESERVED (unproduced)`。
2. **拒絕製造假生產者**：堅守架構原則，不臨時撰寫裝飾性的根因推導 heuristic 來虛偽滿足需求。
3. **工程處置為 `RESERVED (unproduced)`，治理狀態為 `IMPLEMENTATION_READY`**（不是本任務代替人類治理角色建立正式 Waiver 或 Requirement Amendment）：
   - 標明擁有團隊為 `ForecastOps / Platform Ops`，目標時程為 `Wave 5+`。
   - 更新後端模型 `shared/domain/models.py` 與 canonical TS 介面 `packages/schemas/canonical/index.ts`，明確標記 `WorkOrder.root_cause` 為 `@reserved` / `RESERVED (unproduced)`。
   - 更新前端型別契約 `packages/domain-types/src/frontend-contracts.ts` 與元件設計文件 `docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md` §5.6，為 `RootCauseEvidenceCardContract` 與 `causeCandidate` 加上 `@reserved` 註釋與保留宣告，消解 API、TS 及 UI 契約暗示該能力已存在的誤導。
   - 新增 forward migration `000018_work_orders_root_cause_disposition.sql` 與 Alembic revision `0012_work_orders_root_cause_disposition.py`，於資料庫層級記錄 column comment 與保留語意，並具備完整 rollback 機制。
   - 於治理清單 `delivery_toolchain/governance/set_valued_requirements.json` 登錄 `ODP-FR-FCT-004`，標記 `ROOT_CAUSE_CANDIDATE` 為 `absent`，並以 `IMPLEMENTATION_READY`、`assigned_to` 與 `target_phase` 記錄可執行的工程處置。
   - 新增與更新契約／整合測試（`test_root_cause_contract_disposition.py`、`test_canonical_schema.py`、`test_migration_backfill.py`、`test_official_real_estate_postgresql.py`），在 disposable PostgreSQL 上真正執行 forward migration、檢查 comment、downgrade 後確認 comment 清除且 nullable column 保留。

---

## 2. 全樹 Producer / Consumer 查證結果

全樹掃描 `root_cause`、`WorkOrder`、`causeCandidate` 及 `FCT-004` 相關實體之結果如下：

| 層級 / 檔案 | 宣告形式 | Production Writer 存在性 | Production Reader 存在性 | 說明 |
|---|---|:---:|:---:|---|
| `shared/domain/models.py::WorkOrder` | `root_cause: str \| None = None` | ❌ 自動 writer 無 | ❌ backend reader 無 | domain shape 與手動／來源註記的相容邊界；業務模組無呼叫，已標記 RESERVED |
| `packages/schemas/canonical/index.ts::WorkOrder` | `root_cause: string \| null` | ❌ 自動 writer 無 | ❌ production data reader 無 | canonical type only；前端/API client 沒有讀寫接線，已標記 `@reserved` |
| `infra/db/migrations/000001_baseline_canonical_schema.sql` | `core.work_orders.root_cause TEXT` | ❌ 無 | ❌ 無 | baseline 只保留 nullable 欄位；無 DAO、SQL query 或 dbt 模型讀寫，migration 0012 只加 RESERVED comment |
| `packages/schemas/source_contracts/internal/maintenance_work_order_event.json` | optional string | ⚠️ 手動／外部工單可帶值 | ❌ 無自動化管線消費 | 允許手動維修工單輸入字串（如 `"worn_seal"`），不是模型推導結果；省略與帶值均維持相容 |
| `modules/forecastops/` (`ForecastOutput`, `Alert`) | 無 `root_cause` 欄位 | ❌ 無 | ❌ 無 | `ForecastOutput` 提供 `trajectory_class` 與 `turning_point_probability`；`Alert` 提供 `evidence_json` 異常證據，無根因候選 |
| `packages/domain-types/` + `packages/ui-domain/` (`RootCauseEvidenceCardContract`) | UI presentation contract + component render | ❌ 無後端產出 | ⚠️ 有 presentation reader，無 API data wiring | `components.tsx` 只讀 contract、fixture 提供示例；不代表 root-cause engine 存在。type、design 與 governance 均標記 `@reserved` / `unproduced`；ForecastOps API 只提供原始 `evidence_json` |

**查證結論**：全樹無任何自動化推導的 Writer，亦無任何相依此推導邏輯的 backend/business Reader。現存 UI presentation reader 只證明元件與 fixture 存在，沒有 production API 接線；這個差異被明確記錄，避免把「無自動化 producer」誤寫成「整棵樹沒有任何欄位 reader」。

---

## 3. 處置評估與實作就緒結論

針對 `FCT-004` 根因候選能力的工程處置，評估三個選項；本任務沒有宣告正式的人類裁決：

1. **選項 A：臨時實作一個 Heuristic 根因推導器（拒絕）**
   - 違反本次架構修復核心原則：「不為讓清單好看而硬接假生產者」。根因推導涉及完整多維度因果分析系統，硬塞假推導器會產生看似可信但毫無統計保證的錯因，誤導營運決策。
2. **選項 B：完全自 Schema 移除 `root_cause` 欄位（次佳，未採納）**
   - 雖然生產無推導器，但 `maintenance_work_order_event` 內部資料來源合約中包含手動工單之維修備註（如 `"worn_seal"`）。若物理移除，會破壞來源工單紀錄之相容性。
3. **選項 C：標記為 `RESERVED (unproduced)` 並登錄 `IMPLEMENTATION_READY`（採納的工程處置）**
   - 保留資料相容性，同時在所有型別、API 契約、前端展示合約、資料庫註解與治理清單中明示「目前版本無自動化生產者」。
   - 指定擁有者（ForecastOps / Platform Ops）與目標時程（Wave 5+）。
   - `ODP_OPEN_DECISIONS_2026-09-03.md` § 8 仍為 `OPEN`；本項沒有 `formal_decision_ref`、`decider` 或其他 Waiver／Amendment 欄位，不把工程排程冒充正式裁決。

---

## 4. 交付變更清單

1. **Domain Model (`shared/domain/models.py`)**
   - `WorkOrder` docstring 與 `root_cause` 屬性加上 `RESERVED (unproduced)` 註解與 Owner / Milestone 宣告。
2. **Canonical TypeScript Interface (`packages/schemas/canonical/index.ts`)**
   - `WorkOrder` 介面加上 JSDoc `@reserved` 標籤與不支援宣告。
3. **Frontend Domain Types & Component Contracts (`packages/domain-types/`, `docs/design/`)**
   - `packages/domain-types/src/frontend-contracts.ts`：為 `RootCauseEvidenceCardContract` 加上 `@reserved` JSDoc，標示 `causeCandidate` 無後端生產者及 Owner/Milestone。
   - `docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md` §5.6：為 `RootCauseEvidenceCard` 標註契約保留宣告（ODP-FR-FCT-004）。
4. **Database Migration (`infra/db/migrations/`)**
   - `000018_work_orders_root_cause_disposition.sql`：執行 `COMMENT ON COLUMN core.work_orders.root_cause IS 'RESERVED: No automated producer exists in current release (ODP-FR-FCT-004). Owner: ForecastOps/Platform; Target: Wave 5+';`
   - `versions/0012_work_orders_root_cause_disposition.py`：Alembic 遷移腳本，支援 `upgrade` 與 `downgrade` 回滾。
5. **Governance Registry (`delivery_toolchain/governance/set_valued_requirements.json`)**
   - 登錄 `ODP-FR-FCT-004`，4 個 members（3 個 satisfied、`ROOT_CAUSE_CANDIDATE` 記為 `absent`，disposition 為 `IMPLEMENTATION_READY`，並具名 owner／target phase）。
6. **Contract & Regression Tests (`tests/contract/`, `tests/ops/`, `tests/integration/`)**
   - 新增 `tests/contract/test_root_cause_contract_disposition.py`（7 項驗證通過）。
   - 更新 `tests/contract/test_canonical_schema.py`。
   - 更新 `tests/ops/test_migration_backfill.py`。
   - 更新 `tests/integration/test_official_real_estate_postgresql.py`：baseline fixture 提供 0012 所需的 `core.work_orders`，並在 disposable PostgreSQL 上真正執行 forward migration、檢查 comment、downgrade 後確認 comment 清除且 nullable column 保留。

---

## 5. 可重現的驗證收據

所有驗證均於 Python 3.12 執行環境測試通過：

### 5.1 契約與處置測試
```bash
uv run --python 3.12 pytest tests/contract/test_root_cause_contract_disposition.py -v
```
- 結果：`7 passed`（Python contract compatibility、reserved markers、no fake ForecastOps producer、governance `IMPLEMENTATION_READY` disposition）

### 5.2 資料庫遷移計畫與回滾規劃測試
```bash
uv run --python 3.12 pytest tests/ops/test_migration_backfill.py -v
```
- 結果：`15 passed`（migration plan reachability 與版本鏈）

### 5.3 PostgreSQL migration forward / rollback
```bash
uv run --python 3.12 pytest -q \
  tests/integration/test_official_real_estate_postgresql.py::test_root_cause_reserved_migration_round_trips_on_postgresql
```
- 結果：`1 passed`（`0012` upgrade 寫入 RESERVED comment；downgrade 至 `0011` 清除 comment、保留 `core.work_orders.root_cause` 欄位）

### 5.4 治理清單成員驗證測試
```bash
uv run --python 3.12 pytest delivery_toolchain/governance/test_check_requirement_members.py -v
```
- 結果：`41 passed in 0.70s`

### 5.5 官方 PostgreSQL Alembic head smoke
```bash
uv run --python 3.12 pytest -q \
  tests/integration/test_official_real_estate_postgresql.py::test_alembic_head_installs_official_schema_and_both_view_branches
```
- 結果：`1 passed`（官方 PostgreSQL fixture 的 baseline stamp 提供 0012 所需 `core.work_orders`，Alembic head 可達）

### 5.6 程式碼邊界防護檢查
```bash
uv run --python 3.12 delivery_toolchain/governance/check_code_boundaries.py
```
- 結果：`Code boundary checks passed for 1069 files.`
