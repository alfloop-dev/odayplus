# ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001: 未完成 Live 驗收承接與舊 PR 處置對照報告

- **Task ID**: `ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001`
- **負責人 (Owner)**: `Antigravity3`
- **評審人 (Reviewer)**: `Codex2`
- **參照基礎**: `origin/dev` @ `9a5ef53de6955aef2b2c772b8e5f83c78222c675`
- **執行依據**: `support/handoffs/remaining-work-corrections-20261003/EXECUTION.md`
- **機讀承接矩陣**: [`obligation_matrix.json`](obligation_matrix.json)
- **舊 PR 處置矩陣**: [`pr_disposition_matrix.json`](pr_disposition_matrix.json)

---

## 1. 核心原則與邊界切分

本任務落實「未完成 live 驗收承接」與「舊 open PR 處置對照」，嚴格遵循以下工程邊界：

1. **程式交付 (Code Delivered)、Live 驗收 (Live Validated) 與 人工批准 (Formally Signed Off) 嚴格切分**：
   - 程式碼已合併或離線測試綠燈（`is_engineering_done = True`），**不等於** live 運行環境驗收完成（`is_live_done = False`）。
   - 任何涉及 live 運行環境的 NFR 觀測窗、CDC 實體叢集驗證、Merge Queue 真實流量觀測，均維持 `BLOCKED_BY_EVIDENCE` 直至真實部署與收據產生。
2. **歷史已完成工程 (Historical Source Task) 與當前作用中執行通道 (Active Canonical Lane) 嚴格分離**：
   - 原已 archived done 的工程任務（如 `ODP-NFR-RUNTIME-EVIDENCE-001`、`ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001`、`ODP-MERGE-QUEUE-H08-ACTIVATION-001`、`ODP-DURABLE-PARTIAL-IMPL-001`）僅作為歷史來源依據，不得重開已完成工程，亦不得作為 active live execution lane。
   - 所有待驗收項目均明確綁定至當前作用中之 canonical owner task（如 Staging 部署由 `ODP-EPHEMERAL-STAGING-ROLLOUT-001` 承接、Prod 上線後觀測由 `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` 承接、結構性整改收尾由 `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` 承接）。
3. **拒絕循環依賴 (Anti-Circularity)**：
   - 預發布 / 准入階段（Pre-production Admission）不得循環依賴上線後的長期觀測窗（如 NFR 7 營運日、1 個月可用率）。
   - 部署前必需項（如 Staging 壓測、WIF 配置隔離、Staging 還原演練、UAT/ModelRisk 簽核）與部署後觀測項（Prod 24h 流量監控、7 日批次、月可用率）明確劃分，禁止同一項目同時宣告 pre_prod_blocking 與 post_prod_observation。
4. **單一發布閘門真相 (Single Canonical Gate Truth)**：
   - 本任務產出承接矩陣與處置對照，**不重寫、不取代** `docs/evidence/gates/RELEASE_GATE_REGISTRY.json` 與 `RELEASE_MANIFEST.json`。
   - `PRODUCT_RELEASE_GO_NO_GO.md` 中的 2026-06-29 PR #82 歷史評估已被明確標記為歷史脈絡，當前發布狀態回歸唯一 Canonical Gate。
5. **不擅自簽署人類權限**：
   - 缺真實資料或批准時，絕不用假數據（synthetic fixture）或假簽名冒充 Human/Ops。
   - `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001` 僅涵蓋六項業務處置（H01-H06），不擴張為通用 UAT/ModelRisk/Ops 授權。

---

## 2. 跨領域未完成驗收承接 (Obligation Carryforward)

| 領域 | 項目 / 需求 ID | 目標環境 | 歷史來源依據 | 當前作用中承接任務 | 所需真實收據 | 觸發條件 | 當前真實狀態 |
|---|---|---|---|---|---|---|---|
| **NFR** | `ODP-FR-SHARED-008` (金鑰與身份隔離) | dev / staging / prod | `ODP-NFR-RUNTIME-EVIDENCE-001` | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (Human/Ops) | WIF 內 Secret Version SHA-256 Digest 比對 (零明文洩漏)；Cloud Run SA 讀回 | 各環境 Runtime Release 部署成功 | `BLOCKED_BY_EVIDENCE` (配置指紋已隔離，待 live 讀回) |
| **NFR** | `ODP-NFR-PERF-001-STAGING` (Staging API 延遲 P95 <= 3s) | staging | `ODP-NFR-RUNTIME-EVIDENCE-001` | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | 已部署 staging `oday-api` 壓測報告 (併發 10/20/50 零失敗) | Staging 部署並注入流量 | `BLOCKED_BY_EVIDENCE` (離線測試不代表 runtime 證據) |
| **NFR** | `ODP-NFR-PERF-001-PROD` (Prod 24h 流量延遲 P95 <= 3s) | prod | `ODP-NFR-RUNTIME-EVIDENCE-001` | `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` (Human/Ops) | Cloud Monitoring >= 24h Prod 流量 P95 遙測 | Prod 運行滿 24 小時 | `BLOCKED_BY_EVIDENCE` (上線後觀測項) |
| **NFR** | `ODP-NFR-BATCH-002` (每日批次截止) | prod | `ODP-NFR-RUNTIME-EVIDENCE-001` | `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` (Human/Ops) | 7 個連續營運日 Job 執行成功紀錄；**Human/Ops 書面定義營運日截止時刻** | Prod 運行 7 個營運日 | `BLOCKED_BY_EVIDENCE` (repo 內無截止時刻，需 Human 提供) |
| **NFR** | `ODP-NFR-AVAIL-003` (月可用率 >= 99.5%) | prod | `ODP-NFR-RUNTIME-EVIDENCE-001` | `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` (Human/Ops) | Cloud Monitoring OpsBoard 路由 Uptime Check 一個完整日曆月比例 >= 99.5% | Prod 部署後滿 1 個日曆月 | `BLOCKED_BY_EVIDENCE` (上線後觀測項) |
| **NFR** | `ODP-NFR-RPO-004` (RPO <= 60m, RTO <= 240m) | staging / prod | `ODP-NFR-RUNTIME-EVIDENCE-001` | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (Human/Ops) | Cloud SQL PITR 設定讀回；Staging 資料庫計時還原演練日誌 (ODP-AC-NFR-007) | Staging 資料庫實體建立 | `BLOCKED_BY_EVIDENCE` (本機 copy 演練非 live 證據) |
| **CDC Live** | `ODP-CDC-LIVE-REPLICA-SET` (ReplicaSet/oplog 24-48h) | prod | `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (DBA) | DBA 執行 `rs.status()` / `db.getReplicationInfo()` 書面讀回 | 生產 MongoDB 連線驗證 | `BLOCKED_BY_EVIDENCE` (口頭確認待轉書面 DBA 收據) |
| **CDC Live** | `ODP-CDC-LIVE-LATENCY-STAGING` (Staging 延遲驗證 < 10s, P95 < 5s) | staging | `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | Staging Scoped CDC 實體串流真實時鐘量測 `latency_seconds` 與 `sla_breaches()` 遙測日誌 | Staging CDC 啟用 | `BLOCKED_BY_EVIDENCE` (測試時鐘非生產延遲) |
| **CDC Live** | `ODP-CDC-LIVE-LATENCY-PROD` (Prod 持續遙測觀測) | prod | `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` | `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` | 生產環境 >= 24h ChangeStream 遙測驗證持續 P95 < 5s 且無 SLA breach | Prod CDC 運行 | `BLOCKED_BY_EVIDENCE` (上線後觀測項) |
| **CDC Live** | `ODP-CDC-LIVE-IAM-CREDENTIALS` (帳號與權限) | staging / prod | `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (Human/Ops) | IAM 與 DB 角色讀回：`odp_cdc_reader` 具備 `changeStream` 權限 | Ops / DBA 配置授權 | `BLOCKED_BY_EVIDENCE` (非互動 Worker 無權改 IAM) |
| **CDC Live** | `ODP-CDC-LIVE-PG-DDL-MIGRATION` (PostgreSQL 表結構) | dev / staging / prod | `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | 真實 PG 執行 `control_schema.sql` (checkpoints/staging_events) 驗證 | 部署執行 Migration | `BLOCKED_BY_EVIDENCE` (離線 SQL 待部署至實體叢集) |
| **CDC Live** | `ODP-CDC-LIVE-OPLOG-FAIL-CLOSED` (過期 Token 拒絕) | staging | `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | Staging 注入過期 token 觸發 fail-closed 並自動排入批次回復日誌 | Staging 故障注入演練 | `BLOCKED_BY_EVIDENCE` |
| **CDC Live** | `ODP-CDC-MACHINE-EVENT-LIFECYCLE` (生命週期欄位) | codebase / dev | `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` | `ODP-CDC-MACHINE-EVENT-LIFECYCLE-001` | `core.machine_status_events` 欄位 migration 與 soft-retirement 程式/測試 | 執行本輪修正任務 | `IN_PROGRESS` |
| **業務營運** | `ODP-PARTIAL-LIVE-H06` (H06 業務範圍與 Live 佇列) | prod | `ODP-DURABLE-PARTIAL-IMPL-001` | `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001` | 具名業務負責人核准之 Partial 功能營運範圍裁定；真實來源資料匯入、live 佇列政策套用與 production 驗收收據 (ODP-DURABLE-PARTIAL-IMPL-001 §5) | 業務負責人簽核 & Staging 取證 | `PENDING_HUMAN_AUTHORITY` |
| **業務營運** | `ODP-ADJUST-OPERATIONAL-CONFIRMATION` (調整確認) | prod | `ODP_REQUIREMENT_DISPOSITIONS.md` | `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001` | 營運主管確認門市實務（就地調整 vs 停舊開新）之正式書面決策（含具名決策人、日期、門市範圍與可追溯來源證據，ODP_REQUIREMENT_DISPOSITIONS.md:211-225） | 營運主管簽核 | `PENDING_HUMAN_AUTHORITY` |
| **業務營運** | `ODP-AVM-FINANCE-CUTOVER` (AVM 財務切換) | prod | `ODP-AVM-DEPRECIATION-INTEGRATION-001` | `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001` | 財務主管簽署 Contract R-4 三項回滾門檻（數值／結構／校準門檻，ODP_AVM001_DEPRECIATION_DISPOSITION_2026-09-04.md §4-5）及生產切換核准 | 財務主管簽核 | `PENDING_HUMAN_AUTHORITY` |
| **Merge Queue** | `ODP-MERGE-QUEUE-BATCH-FORMATION` (多 PR 批次) | dev | `ODP-MERGE-QUEUE-H08-ACTIVATION-001` | `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` | GitHub Actions / Merge Queue 審計日誌證明 >= 2 PR 同時排隊時形成批次 | 自然 PR 排隊流量 | `BLOCKED_BY_EVIDENCE` (Option B 已寫入，待流量觀測) |
| **Merge Queue** | `ODP-MERGE-QUEUE-HOLD-TIMEOUT` (單 PR 等待 <= 10m) | dev | `ODP-MERGE-QUEUE-H08-ACTIVATION-001` | `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` | Merge Queue 延遲紀錄證明單 PR hold 不超過 10 分鐘 | 單 PR 排隊情境 | `BLOCKED_BY_EVIDENCE` |
| **Merge Queue** | `ODP-MERGE-QUEUE-ALLGREEN-REBUILD` (失敗剔除重建) | dev | `ODP-MERGE-QUEUE-H08-ACTIVATION-001` | `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` | 審計日誌證明批次內失敗 PR 被剔除且其餘 PR 重建合併 | 批次測試失敗情境 | `BLOCKED_BY_EVIDENCE` |
| **人工簽核** | `ODP-SIGN-OFF-UAT` (跨角色 UAT 簽核) | staging / prod | `RELEASE_GATE_REGISTRY.json §gate-5` | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` (QA/Product) | 全角色 (Operator, Manager, Franchisee) 零 P0/P1 缺陷簽署件 | Staging UAT 完成 | `PENDING_HUMAN_AUTHORITY` |
| **人工簽核** | `ODP-SIGN-OFF-MODEL-RISK` (模型風險簽核) | staging / prod | `RELEASE_GATE_REGISTRY.json §gate-3` | `ODP-MODEL-ARTIFACT-HISTORY-RECOVERY-001` (ML Risk) | ML Risk Owner 簽署之 ForecastOps 模型版本保管、SiteScore/AVM 驗證基準與 production alias 綁定件 | 模型主管審查 | `PENDING_HUMAN_AUTHORITY` |
| **人工簽核** | `ODP-SIGN-OFF-OPS-GO-NO-GO` (最終 Go/No-Go) | prod | `RELEASE_GATE_REGISTRY.json §gate-6` | `ODP-PROD-BLUEGREEN-ROLLOUT-001` (Human/Ops) | `PRODUCT_RELEASE_GO_NO_GO.md` 與 `RELEASE_GATE_REGISTRY.json` 具名簽署（綁定 exact release SHA） | Gate 0-6 滿足 | `PENDING_HUMAN_AUTHORITY` |
| **延後項** | `ODP-DEFERRED-ROOT-CAUSE-WAVE-5` (Wave 5+ 根因) | codebase | `EXECUTION.md` | `DEFERRED-BACKLOG-WAVE5` | 納入上線後維護週期 Backlog 排程追蹤 | 上線後排程 | `DEFERRED_NON_BLOCKING` |
| **延後項** | `ODP-DEFERRED-GOOGLE-OAUTH` (Google OAuth) | codebase | `Decision D15` | `HUMAN-GCP-WEB-OAUTH-CLIENTS-001` | D15 決策合規 (維持帳密登入，OAuth 保持停用) | 未來需求評估 | `DEFERRED_NON_BLOCKING` |

---

## 3. 舊 Open PR 逐一對照與處置建議

| 儲存庫 | PR 編號 | Head SHA | 比較基準 (Base) | 分支名稱 | 標題 | 狀態 | 差異內容與未合併程式碼現況 | 處置建議 |
|---|---|---|---|---|---|---|---|---|
| `odayplus` | **#1243** | `3a9fb628...` | `origin/dev` | `task/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002-CLEAN` | ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002: clean evidence from C | OPEN | 重建 evidence 樹嘗試綁定 candidate C (596b9c9a) 與 build run 33942097235。已被後續候選重綁 sequence (PR #1387, #1390, #1392) 取代，當前 dev 已綁定至 6140d0ef。無遺留代碼。 | **建議關閉 (Superseded)** |
| `odayplus` | **#1205** | `eaa7f8c5...` | `origin/dev` | `task/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002` | [ReviewBus] ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002 以最新 build receipt 重整 dev candidate gate | OPEN | 舊版 candidate 3b5de7e7 綁定嘗試（8 檔文件/證據）。已被 PR #1243 及後續 PR 取代。無遺留代碼。 | **建議關閉 (Superseded)** |
| `odayplus` | **#1052** | `73a9ee46...` | `origin/dev` | `task/ORCH-PROVIDER-QUOTA-SIGNAL-AUTHORITY-001-SIDECAR-BLOCKED-TASK-DIAGNOSTICS` | ORCH-PROVIDER-QUOTA-SIGNAL-AUTHORITY-001-SIDECAR-BLOCKED-TASK-DIAGNOSTICS: 建立阻塞診斷交接包 | OPEN | 修改 2 檔：`check_commit_trailers.py`（task-ID 相關之主旨長度判斷邏輯）與新增診斷文件 `support/sidecars/...-DIAGNOSTICS.md`。主線 dev 雖已修復 failure policy，但 trailer 邏輯差異未合併。 | **需進一步比對/保留 (Needs Comparison)** |
| `odayplus` | **#986** | `173f51c6...` | `origin/dev` | `fix/quarantine-clean-is-not-unreadable` | orchestrator: a clean worktree is not an unreadable one | OPEN | 修改 `worker_workspace.py` 與 `test_supervisor.py`（+43 行測試），將 git 的 stderr 傳入 `status_unreadable.detail`。主線 `origin/dev` 雖已具備 clean worktree 判定，但 `worker_workspace.py:3624-3625` 仍丟棄 git stderr。有未合併有效改動。 | **需進一步比對/保留 (Needs Comparison)** |
| `odayplus` | **#970** | `4c813d1c...` | `origin/dev` | `task/XR-CUTOVER-001` | [ReviewBus] XR-CUTOVER-001 Execute dual-run, reconciliation, cutover and rollback of legacy odayplus external ingestion | OPEN | **已完成 65 檔完整稽核**：`docs/evidence/completion/ODP-XR-CUTOVER-ACTIVATE-002/pr-970-disposition.md` 逐檔對照，拒絕毀滅性刪除以保有緊急回滾能力；改由 PR #991 (`ODP-XR-CUTOVER-ACTIVATE-002`，commit `b32fd65f`) 實作單一控制面 `PLATFORM_PRIMARY` + 預設阻斷。所有有效功能均已在 dev 落地，無遺留未合併代碼。 | **建議關閉 (Superseded with 65-file audit)** |
| `odayplus` | **#607** | `a70b62ca...` | `origin/dev` | `agent/dev-progress-spec-gap-audit-20260803` | docs: audit latest dev progress against specification | OPEN | 新增 8 月 3 日早期盤點文件 `docs/evidence/DEV_PROGRESS_SPEC_GAP_AUDIT_2026-08-03.md`。已被 9 月整改計畫 (`ODP_REMEDIATION_PLAN_2026-09-03.md`)、待決策事項及 `EXECUTION.md` 完整取代。 | **建議結案/歷史存檔 (Historical Snapshot)** |
| `oday-data-platform` | **#1** | `0577773d...` | `origin/dev (24c40ae7)` | `feat/transaction-automation` | [Feat] add transactions daily lookback automation | OPEN | 修改 `assets.py`, `schedules.py`, `test_assets.py`，新增 transaction daily-lookback scheduling、可配置 partition start、count-mismatch failure 與 automation metadata。這些功能在 DPF dev 中仍不存在，且未被 unmerged 的 PR #63/#77 取代。有未合併功能代碼。 | **需進一步比對/保留 (Needs Comparison)** |

### 作用中正式交付 PR (Active In-flight PRs — 嚴禁重複開立或誤關閉)
- `alfloop-dev/odayplus` **#1381** (`task/ODP-DEV-LIVE-DEPLOY-EXECUTION-001`)
- `alfloop-dev/odayplus` **#1312** (`task/XR-EXT-OSS-FINAL-AUDIT-001-RECOVERY-20260911`)
- `alfloop-dev/odayplus` **#1014** (`task/ODP-EPHEMERAL-STAGING-ROLLOUT-001`)
- `alfloop-dev/oday-data-platform` **#77** (`task/DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001`)
- `alfloop-dev/oday-data-platform` **#63** (`task/DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001`)

---

## 4. 驗證依據與測試

本交付包含專屬 focused test suite：
```bash
uv run --frozen --python 3.12 pytest -q docs/evidence/completion/ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001/test_carryforward.py
```
測試涵蓋：
1. **結構綱要與欄位完整性驗證**：嚴格驗證所有 obligation 的 `obligation_id`、`title`、`category`、`stage`、`phase`、`canonical_owner_task`、`historical_source_task`、`owner_or_authority`、`original_acceptance_ref`、`trigger_condition`、`target_environments`、`observation_window`、`observation_window_type`、`required_receipts`、`real_status`、`is_engineering_done`、`is_live_done`、`pre_prod_blocking`、`post_prod_observation`。
2. **防偽與負向測試 (Negative Test Cases)**：
   - 拒絕缺失或空白的 `canonical_owner_task`、`stage`、`phase`、`trigger_condition`、`original_acceptance_ref`。
   - 拒絕將已 archived 的任務（如 `ODP-NFR-RUNTIME-EVIDENCE-001`）作為 `canonical_owner_task`。
   - 拒絕空列表或包含空白字串 `[""]` 的 `required_receipts`。
   - 拒絕將尚未具備 live 證據的項目虛構標記為 `is_live_done = True`。
   - 拒絕循環依賴：禁止同一項目同時具備 `pre_prod_blocking = True` 與 `post_prod_observation = True`。
3. **NFR 與 CDC 階層區分**：PERF 與 CDC latency 分別拆分為 Staging 部署前驗證（pre-prod blocking）與 Production 上線後觀測（post-prod observation）。
4. **具體原驗收標準**：
   - PARTIAL 包含真實來源資料匯入、live 佇列政策套用與 production 驗收（ODP-DURABLE-PARTIAL-IMPL-001 §5）。
   - ADJUST 包含門市營運實務（就地調整 vs 停舊開新）具名決策人、日期、範圍與來源依據（ODP_REQUIREMENT_DISPOSITIONS.md:211-225）。
   - AVM 包含 Contract R-4 數值／結構／校準三項回滾門檻（ODP_AVM001_DEPRECIATION_DISPOSITION_2026-09-04.md §4-5）。
5. **PRODUCT_RELEASE_GO_NO_GO 歷史脈絡與唯一發布閘門連結驗證**。
6. **舊 PR 處置對照**：精確記錄 exact PR head、comparison base、diff 及未合併代碼現況，保護作用中交付。
