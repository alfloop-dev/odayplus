# ODP-JOB-PARTIAL-DISPOSITION-001 — SHARED-001 PARTIAL 狀態轉移處置與 Human-Authority Handback 報告

- **任務識別碼**：`ODP-JOB-PARTIAL-DISPOSITION-001`
- **文件路徑**：`docs/evidence/ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md`
- **日期**：2026-09-03
- **任務負責人**：Antigravity6（Helper Execution Lease: Antigravity3）
- **審查人**：Claude2
- **基準代碼**：`origin/dev` @ `120b46d37d6bbbaccd145e84cce456e6610160b3`
- **關聯需求**：`ODP-FR-SHARED-001`（所有長時間任務都能查詢 QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/PARTIAL）
- **前置任務**：
  - `ODP-JOB-PARTIAL-PRODUCER-EVIDENCE-001`（查證哪些 production jobs 真的具有 PARTIAL outcome）
  - `ODP-REQ-DISPOSITION-GOVERNANCE-001`（建立 MUST requirement amendment／waiver 的可機讀 disposition gate）
- **依據與來源**：
  - [修正計畫](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md) §第 5b 批
  - [待裁決事項](../plans/ODP_OPEN_DECISIONS_2026-09-03.md) §第 10 項
  - [結構性成因處理結果](ODP_STRUCTURAL_REMEDIATION_2026-09-01.md)
  - [PARTIAL Production Producer 查證報告](ODP_JOB_PARTIAL_PRODUCER_EVIDENCE_2026-09-03.md)
  - [需求處置與治理政策](../governance/ODP_REQUIREMENT_DISPOSITIONS.md)
  - [集合型需求治理清單](../../delivery_toolchain/governance/set_valued_requirements.json)

---

## 1. 執行摘要與處置核心判定

本報告依據 `ODP-JOB-PARTIAL-PRODUCER-EVIDENCE-001` 之靜態與執行期生產者查證事實，針對 `ODP-FR-SHARED-001`（「所有長時間任務都能查詢 QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/PARTIAL」）中尚未有生產者寫入的成員 —— **`PARTIAL`（部分成功狀態）** 進行正式處置、生命週期狀態判定，並依據 Remediation Plan 與架構治理規範建立需人類授權之 Handback Package。

### 1.1 處置架構總覽

依據治理政策與防偽原則，本任務逐 member 判定獨立處置結果：

```
+------------------------------------------------------------------------------------------------------------------+
|                                        ODP-FR-SHARED-001 處置架構總覽                                            |
+----------------------+--------------------+-----------------------+----------------------------------------------+
| 需求成員 (Member)    | 履約現況 (Status)  | 處置狀態 (Disposition) | 後續路徑 (Action Path)                       |
+----------------------+--------------------+-----------------------+----------------------------------------------+
| QUEUED               | satisfied          | VERIFIED              | 詞彙與狀態機完整運作中 (shared/governance)    |
| RUNNING              | satisfied          | VERIFIED              | 詞彙與狀態機完整運作中 (shared/governance)    |
| SUCCEEDED            | satisfied          | VERIFIED              | 詞彙與狀態機完整運作中 (shared/governance)    |
| FAILED               | satisfied          | VERIFIED              | 詞彙與狀態機完整運作中 (shared/governance)    |
| CANCELLED            | satisfied          | VERIFIED              | 詞彙與狀態機完整運作中 (shared/governance)    |
| PARTIAL              | absent             | BLOCKED_BY_EVIDENCE   | 移交 HB-SHARED001-PARTIAL-001 人類授權包      |
+----------------------+--------------------+-----------------------+----------------------------------------------+
```

### 1.2 核心處置判定

1. **無適用生產者（Absence of Production Job Producers）**：
   - 經 `ODP_JOB_PARTIAL_PRODUCER_EVIDENCE_2026-09-03.md` 全樹查證，代碼庫中現行 default worker registry 僅註冊三個 job handler：`forecast`、`external-fetch`、`assisted-listing-intake`。三者皆為單一實體或單一資料窗口執行單元，正常返回由外層框架統一記錄 `SUCCEEDED`，異常則進入重試或 `FAILED`，皆無可聚合之成員清單或 `JobStatus.PARTIAL` 狀態轉移寫入點。
   - 所有 module worker entry points（AVM、SiteScore、ForecastOps、HeatZone、AdLift、NetPlan、PriceOps、Geocode、Intervention observation、Market survey expiry、LearningHub release/drift）均無任何 `JobStatus.PARTIAL` 寫入。

2. **堅決拒絕假實作與語意冒充（Strict Prohibition of Fake Implementation）**：
   - **禁止拿 Queue Delivery State 冒充 PARTIAL**：基礎設施隊列之 `RETRYING`（可重試錯誤）與 `DEAD_LETTER`（重試耗盡死信）屬於 delivery mechanics（`JobDeliveryState`），絕非業務成果（`JobStatus`）。
   - **禁止拿同步 API Command Receipt 冒充 JobStatus**：`POST /api/v1/intake-batches` 之 HTTP 207 多狀態逐列收據（`BatchIntakeReceipt`）與 XLSX 局部提交（`XlsxCommitReceipt`）為同步 API 指令收據，非 Durable Queue Job。
   - **禁止拿資料層隔離筆數冒充 JobStatus**：External Ingestion 之 `accepted_count` 與 `quarantined_count` 屬於資料品質層標記，其 scheduler run 狀態仍為 `SUCCEEDED` / `FAILED`。
   - **禁止拿模組告警（Warnings）冒充 PARTIAL**：HeatZone 或 SiteScore 之 warnings 係屬 domain notices，非工作項失敗。

3. **嚴格遵守治理防線（AI 禁止自簽豁免）**：
   - AI 代理人嚴格遵守 `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md` §3.2，絕不自作主張將 `PARTIAL` 逕自宣告為 `DECIDED` 或假造人類簽署。
   - 在 `set_valued_requirements.json` 中維持 `status: "absent"` 與 `disposition.state: "BLOCKED_BY_EVIDENCE"`，完整指明 `evidence_needed`、`evidence_owner`、`next_review_date`（`2026-10-01`）與 Handback 參照。

---

## 2. 生產者查證事實與排除架構

### 2.1 生產任務庫盤點與排除事實

| 候選任務 (Candidate) | 入口與註冊點 | 實際 Aggregate 行為 | PARTIAL 判定 |
|---|---|---|---|
| `forecast` | `apps/api/oday_api/main.py:977` | 驗證單一門市時序，正常固定回傳 `SUCCEEDED`；無成員明細 | **不適用**：無 member aggregate 與 `PARTIAL` 寫入 |
| `external-fetch` | `apps/scheduler/oday_scheduler/main.py:181` | 單一 provider/window 抓取，排程層回報 `SUCCEEDED` 或 `FAILED`；失敗走 circuit breaker | **不適用**：無 job 級成員聚合狀態轉移 |
| `assisted-listing-intake` | `modules/opsboard/application/network_listings.py:1210` | 單一房源 URL 爬蟲，依序執行 stage，超限拋 `NonRetryableJobError`；外層寫 `SUCCEEDED`/`FAILED` | **不適用**：單一實體任務，非批次聚合 |
| Generic / Unknown | `apps/api/oday_api/main.py:1018` | 未註冊 job_type 拋 `UnknownJobTypeError` 後 dead-letter | **不適用**：無對應 handler |

### 2.2 業務部分成功但非隊列任務之邊界確認

1. **批次房源寫入 (`POST /api/v1/intake-batches`)**：
   - 具有真實業務部分成功語意（Valid rows 回傳 200/`ACCEPTED`，Invalid rows 回傳 `REJECTED`，混合時 API 回傳 HTTP 207 Multi-Status 及 `(accepted_count, rejected_count)`）。
   - **邊界確認**：此為同步 API 冪等指令收據（`BatchIntakeReceipt`），未經隊列調度，無 `job_id` 與 `JobStatus` 欄位。若未來需轉為非同步長任務，需由產品與架構簽署昇格方案。
2. **XLSX 匯入提交 (`xlsx_import.py`)**：
   - 預覽與提交區分 valid rows 與 row errors，提交時僅寫入 valid rows，回傳 `accepted_count` 與 `rejected_count`。
   - **邊界確認**：此為同步 API Command，非 Durable Worker Job，無 queue claim 或 worker retry 流程。
3. **外部資料攝取 (`ingestion_store.py` / `scheduled_fetch.py`)**：
   - Ingestion 紀錄區分 `accepted_count` 與 `quarantined_count`。
   - **邊界確認**：Scheduler 的 `ExternalFetchRun` 僅產生 `SUCCEEDED` 或 `FAILED`，失敗走排程重試。未提供「僅重試 quarantined 記錄」之 job receipt。

### 2.3 業務結果（Outcome）與交付機制（Delivery State）之型別分離

本專案在 `shared/governance/vocabularies.py` 與 `apps/api/app/routes/listings.py` 中已實現型別與欄位的徹底分離：
- **`JobStatus`（業務完成狀態）**：`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `PARTIAL`。
- **`JobDeliveryState`（隊列傳遞狀態）**：`RETRYING`, `DEAD_LETTER`。

```
+------------------------------------------------------------------------------------+
|                               型別與概念邊界分離                                   |
+------------------------------------------------------------------------------------+
| 業務結果 (JobStatus)             | 基礎設施交付狀態 (JobDeliveryState)              |
+----------------------------------+-------------------------------------------------+
| QUEUED: 任務已接受但尚未開始     | (None): 正常執行或終態                           |
| RUNNING: 任務正在運算中          | RETRYING: 遭遇短暫異常，正在等待隊列指數退避重試 |
| SUCCEEDED: 全部工作項目皆成功完成| DEAD_LETTER: 重試次數耗盡或毒藥訊息，已移入死信  |
| FAILED: 全部工作項目皆失敗       |                                                 |
| CANCELLED: 人工或系統主動取消    |                                                 |
| PARTIAL: 部分項目成功、部分失敗  |                                                 |
+----------------------------------+-------------------------------------------------+
```

---

## 3. 人類授權移交單（Human-Authority Handback Package）

```yaml
handback_id: HB-SHARED001-PARTIAL-001
requirement_id: ODP-FR-SHARED-001
member: PARTIAL
current_disposition_state: BLOCKED_BY_EVIDENCE
evidence_ref: docs/evidence/ODP_JOB_PARTIAL_PRODUCER_EVIDENCE_2026-09-03.md
designated_authority:
  - Platform Infrastructure Lead
  - Platform Architecture Board
  - Product Lead / Workflow Governance
  - Human/Ops
assigned_risk_owner: Platform Infrastructure Lead
next_review_date: 2026-10-01
reopen_trigger: >-
  (1) Production worker registry adds a multi-item batch job handler with reachable JobStatus.PARTIAL
  transition, OR (2) synchronous command operations (batch intake / external ingestion) are formally scheduled
  to become durable jobs with itemized receipts and member-level retry contracts, OR (3) live production
  queue/worker audit receipts demonstrate deployed jobs reporting PARTIAL.

decision_pathways:
  pathway_a_implementation:
    description: "產品確立批次長任務（如非同步批次房源匯入或多來源資料聚合）需回報 PARTIAL，並按設計契約實作"
    prerequisites:
      - "產品與架構團隊核准批次長任務需求與 Schema 規格"
      - "worker registry 註冊具備成員聚合語意之 Batch Job Handler"
      - "實作 ItemizedJobReceipt（含 succeeded_items 與 failed_items 明細）"
      - "實作隊列冪等重試機制（Retry 僅重試 failed items，跳過已 succeeded items）"
      - "API /platform/jobs/{job_id} 與 /api/v1/jobs/{job_id}/receipt 支援查詢 PARTIAL 與成員明細"
      - "通過反事實驗收測試套件"
    target_state: "IMPLEMENTATION_READY"

  pathway_b_formal_amendment_or_waiver:
    description: "由人類授權人簽署正式需求修訂 (Amendment) 或具期限豁免 (Waiver)"
    prerequisites:
      - "由具名人類治理角色（Platform Infrastructure Lead / Architecture Board / Human/Ops）簽署"
      - "提供 6 大法定欄位：formal_decision_ref, decider, scope, risk_owner, expiry, reopen_trigger"
      - "登記於 docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md"
    target_state: "DECIDED"
```

---

## 4. 未來實作啟用時的設計契約與反事實驗收標準（Pathway A Spec）

當 Human/Ops 或權威負責人批准進入實作（Pathway A）時，未來承接實作之任務必須嚴格遵循以下設計契約與驗收規範：

### 4.1 狀態轉移與業務結果模型契約（State Transition Contract）

1. **僅限具備成員清單之批次任務可轉移為 `PARTIAL`**：
   - 單一原子任務（Single-item job）僅能為 `SUCCEEDED` 或 `FAILED`。
   - 批次任務在全部項目處理完成後，依據彙整統計轉移：
     - 若 $\text{succeeded\_count} == \text{total\_count}$ 且 $\text{failed\_count} == 0 \implies \text{JobStatus.SUCCEEDED}$。
     - 若 $\text{succeeded\_count} == 0$ 且 $\text{failed\_count} == \text{total\_count} \implies \text{JobStatus.FAILED}$。
     - 若 $\text{succeeded\_count} > 0$ 且 $\text{failed\_count} > 0 \implies \text{JobStatus.PARTIAL}$。
     - 若中途被主動中斷 $\implies \text{JobStatus.CANCELLED}$。
2. **與交付狀態之共存約束**：
   - 當任務達到終態 `PARTIAL` 時，隊列交付狀態 `delivery_state` 必須為 `None`（表示基礎設施傳遞已結束，業務結果為部分成功）。
   - 當隊列正在進行指數退避重試時，`delivery_state` 為 `RETRYING`，`status` 應為 `QUEUED` 或 `RUNNING`，絕不得在此階段將 `status` 設為 `PARTIAL`。

### 4.2 明細收據與成員識別架構契約（Itemized Receipt Schema Contract）

`PARTIAL` 狀態之任務收據必須提供結構化成員明細，供下游查詢與精確補救：

```json
{
  "job_id": "job-batch-8849a2f1",
  "job_type": "batch-listing-intake",
  "status": "PARTIAL",
  "delivery_state": null,
  "summary": {
    "total_count": 10,
    "succeeded_count": 7,
    "failed_count": 3
  },
  "items": [
    {
      "item_id": "row-001",
      "item_status": "SUCCEEDED",
      "result_ref": "intake-991823",
      "error": null
    },
    {
      "item_id": "row-002",
      "item_status": "FAILED",
      "result_ref": null,
      "error": {
        "code": "MISSING_MANDATORY_ADDRESS",
        "message": "Street address is missing or unparseable",
        "retryable": false
      }
    }
  ],
  "created_at": "2026-09-03T18:00:00Z",
  "completed_at": "2026-09-03T18:00:05Z"
}
```

### 4.3 重試契約（不重做成功項）(Member-Level Retry Contract)

1. **差異化重試（Scoped Replay）**：
   - 針對狀態為 `PARTIAL` 之任務發起重試（如 `POST /platform/jobs/{job_id}/retry`）時，系統必須支援 `retry_scope="FAILED_ONLY"`（預設）。
   - 執行引擎必須自持久層讀取上次執行之 `items` 收據：
     - 對於 `item_status == "SUCCEEDED"` 之項目，直接跳過或重用既有成果，**嚴禁重複調用下游寫入或扣款**。
     - 僅對 `item_status == "FAILED"` 且 `retryable == true` 之項目重新執行運算。
2. **重試結果收斂（Convergence）**：
   - 重試成功之項目原地更新其 `item_status` 為 `SUCCEEDED`。
   - 當所有原本失敗之項目皆重試成功後，整體 `JobStatus` 自動由 `PARTIAL` 收斂轉移為 `SUCCEEDED`。

### 4.4 API 查詢與客戶端合約（API Query & Client Contract）

1. **後端端點**：
   - `GET /platform/jobs/{job_id}` 與 `GET /api/v1/jobs/{job_id}/receipt` 必須在 `status` 欄位精確序列化 `"PARTIAL"`，並將 `delivery_state` 序列化為 `null`（若無傳遞中錯誤）。
2. **OpenAPI 與前端生成契約**：
   - `packages/schemas/canonical/vocabularies.json` 與 `packages/openapi-client/openapi.json` 保持 `JobStatus` 包含 `"PARTIAL"`，`JobDeliveryState` 包含 `"RETRYING" | "DEAD_LETTER"`。
   - 前端 Operator UI 針對 `PARTIAL` 顯示「部分成功（需檢視明細）」，而非警告圖示或滿分綠燈。

### 4.5 反事實驗收測試（Counterfactual Acceptance Criteria）

未來實作 PR 必須具備以下三項反事實驗證測試：

- **測試 1（狀態轉移精確性驗證）**：
  - 輸入 10 筆項目（8 筆成功，2 筆校驗失敗），驗證任務終態為 `JobStatus.PARTIAL`，且斷言不得為 `SUCCEEDED` 或 `FAILED`。
- **測試 2（冪等重試不重複執行驗證）**：
  - 對上述 `PARTIAL` 任務發起重試，以 Mock/Spy 驗證 8 筆成功項目之處理函式被調用次數為 0，僅 2 筆失敗項目被調用 1 次。
- **測試 3（交付狀態與業務結果分離驗證）**：
  - 當重試過程中遭遇網路 Timeout 時，驗證 `JobDeliveryState` 為 `RETRYING`，但業務聚合狀態不被錯誤修改為 `PARTIAL`；直到重試預算耗盡或全部完成前，兩者維持正交獨立。

---

## 5. 治理清單與規範對齊狀態

1. **`delivery_toolchain/governance/set_valued_requirements.json`**：
   - `ODP-FR-SHARED-001` 成員 `PARTIAL` 保持 `status: "absent"`，`disposition.state: "BLOCKED_BY_EVIDENCE"`。
   - 配置法定參照欄位：`formal_handback_ref`、`evidence_needed`、`evidence_owner`（`Platform Infrastructure Lead`）、`next_review_date`（`2026-10-01`）、`reopen_trigger` 與 `rationale`。
2. **`docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md`**：
   - §4.5 登錄正式處置記錄、Handback Package ID `HB-SHARED001-PARTIAL-001`、風險負責人與下次檢視時點。
3. **自動化驗證**：
   - `delivery_toolchain/governance/check_requirement_members.py` 與 `tests/governance/test_job_partial_disposition.py` 檢查全數通過。

---

## 6. 可重現驗證收據（Reproducibility Receipts）

以下驗證指令於本分支工作目錄執行全數通過：

```bash
# 1. 驗證集合型需求治理清單與處置檢查器全數通過
python3 delivery_toolchain/governance/check_requirement_members.py

# 2. 執行治理檢查器單元與整合測試套件 (41 passed)
UV_PYTHON=/usr/bin/python3.12 uv run --frozen pytest delivery_toolchain/governance/test_check_requirement_members.py

# 3. 執行 SHARED-001 PARTIAL 專屬治理與合約驗證測試套件
UV_PYTHON=/usr/bin/python3.12 uv run --frozen pytest tests/governance/test_job_partial_disposition.py

# 4. 查證 JobStatus 與 JobDeliveryState 之型別分離
python3 -c "from shared.governance.vocabularies import JobStatus, JobDeliveryState; print('JobStatus:', [s.value for s in JobStatus]); print('JobDeliveryState:', [d.value for d in JobDeliveryState])"

# 5. 查證代碼庫中無任何寫入 JobStatus.PARTIAL 的偽生產者
rg -n --glob '*.py' --glob '*.ts' --glob '*.tsx' 'JobStatus\s*\.\s*PARTIAL' apps modules shared packages
```
