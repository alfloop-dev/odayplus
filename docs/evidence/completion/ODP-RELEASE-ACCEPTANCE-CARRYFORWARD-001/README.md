# ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001: 未完成 Live 驗收承接與舊 PR 處置對照報告

- **Task ID**: `ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001`
- **負責人 (Owner)**: `Antigravity3`
- **評審人 (Reviewer)**: `Codex2`
- **參照基礎**: `origin/dev` @ `1b14b276447a7778f1dad1045ced1a5bd6abe01e`
- **執行依據**: `support/handoffs/remaining-work-corrections-20261003/EXECUTION.md`
- **機讀承接矩陣**: [`obligation_matrix.json`](obligation_matrix.json)
- **舊 PR 處置矩陣**: [`pr_disposition_matrix.json`](pr_disposition_matrix.json)

---

## 1. 核心原則與邊界切分

本任務落實「未完成 live 驗收承接」與「舊 open PR 處置對照」，嚴格遵循以下工程邊界：

1. **程式交付 (Code Delivered)、Live 驗收 (Live Validated) 與 人工批准 (Formally Signed Off) 嚴格切分**：
   - 程式碼已合併或離線測試綠燈（`is_engineering_done = True`），**不等於** live 運行環境驗收完成（`is_live_done = False`）。
   - 任何涉及 live 運行環境的 NFR 觀測窗、CDC 實體叢集驗證、Merge Queue 真實流量觀測，均維持 `BLOCKED_BY_EVIDENCE` 直至真實部署與收據產生。
2. **拒絕循環依賴 (Anti-Circularity)**：
   - 預發布 / 准入階段（Pre-production Admission）不得循環依賴上線後的長期觀測窗（如 NFR 7 營運日、1 個月可用率）。
   - 部署前必需項（如 SHARED-008 配置隔離、Staging 還原演練、UAT/ModelRisk 簽核）與部署後觀測項（24h 流量監控、7 日批次、月可用率）明確劃分。
3. **單一發布閘門真相 (Single Canonical Gate Truth)**：
   - 本任務產出承接矩陣與處置對照，**不重寫、不取代** `docs/evidence/gates/RELEASE_GATE_REGISTRY.json` 與 `RELEASE_MANIFEST.json`。
   - `PRODUCT_RELEASE_GO_NO_GO.md` 中的 2026-06-29 PR #82 歷史評估已被明確標記為歷史脈絡，當前發布狀態回歸唯一 Canonical Gate。
4. **不擅自簽署人類權限**：
   - 缺真實資料或批准時，絕不用假數據（synthetic fixture）或假簽名冒充 Human/Ops。

---

## 2. 跨領域未完成驗收承接 (Obligation Carryforward)

| 領域 | 項目 / 需求 ID | 目標環境 | 承接階層 / 任務 | 所需真實收據 | 觸發條件 | 當前真實狀態 |
|---|---|---|---|---|---|---|
| **NFR** | `ODP-FR-SHARED-008` (金鑰與身份隔離) | dev / staging / prod | `ODP-NFR-RUNTIME-EVIDENCE-001` (Human/Ops) | WIF 內 Secret Version SHA-256 Digest 比對 (零明文洩漏)；Cloud Run SA 讀回 | 各環境 Runtime Release 部署成功 | `BLOCKED_BY_EVIDENCE` (配置指紋已隔離，待 live 讀回) |
| **NFR** | `ODP-NFR-PERF-001` (API 延遲 P95 <= 3s) | staging / prod | `ODP-NFR-RUNTIME-EVIDENCE-001` (Human/Ops) | 已部署 `oday-api` 壓測報告 (併發 10/20/50 零失敗)；Cloud Monitoring >= 24h P95 | Staging / Prod 部署並注入流量 | `BLOCKED_BY_EVIDENCE` (離線測試不代表 runtime 證據) |
| **NFR** | `ODP-NFR-BATCH-002` (每日批次截止) | prod | `ODP-NFR-RUNTIME-EVIDENCE-001` (Human/Ops) | 7 個連續營運日 Job 執行成功紀錄；**Human/Ops 書面定義營運日截止時刻** | Prod 運行 7 個營運日 | `BLOCKED_BY_EVIDENCE` (repo 內無截止時刻，需 Human 提供) |
| **NFR** | `ODP-NFR-AVAIL-003` (月可用率 >= 99.5%) | prod | `ODP-NFR-RUNTIME-EVIDENCE-001` (Human/Ops) | Cloud Monitoring OpsBoard 路由 Uptime Check 一個完整日曆月比例 >= 99.5% | Prod 部署後滿 1 個日曆月 | `BLOCKED_BY_EVIDENCE` (上線後觀測項) |
| **NFR** | `ODP-NFR-RPO-004` (RPO <= 60m, RTO <= 240m) | staging / prod | `ODP-NFR-RUNTIME-EVIDENCE-001` (Human/Ops) | Cloud SQL PITR 設定讀回；Staging 資料庫計時還原演練日誌 (ODP-AC-NFR-007) | Staging 資料庫實體建立 | `BLOCKED_BY_EVIDENCE` (本機 copy 演練非 live 證據) |
| **CDC Live** | `ODP-CDC-LIVE-REPLICA-SET` (ReplicaSet/oplog 24-48h) | prod | `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` | DBA 執行 `rs.status()` / `db.getReplicationInfo()` 書面讀回 | 生產 MongoDB 連線驗證 | `BLOCKED_BY_EVIDENCE` (口頭確認待轉書面 DBA 收據) |
| **CDC Live** | `ODP-CDC-LIVE-LATENCY-SLA` (延遲 < 10s, P95 < 5s) | staging / prod | `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` | 實體串流真實時鐘量測 `latency_seconds` 與 `sla_breaches()` 遙測日誌 | Live Scoped CDC 啟用 | `BLOCKED_BY_EVIDENCE` (測試時鐘非生產延遲) |
| **CDC Live** | `ODP-CDC-LIVE-IAM-CREDENTIALS` (帳號與權限) | staging / prod | `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` | IAM 與 DB 角色讀回：`odp_cdc_reader` 具備 `changeStream` 權限 | Ops / DBA 配置授權 | `BLOCKED_BY_EVIDENCE` (非互動 Worker 無權改 IAM) |
| **CDC Live** | `ODP-CDC-LIVE-PG-DDL-MIGRATION` (PostgreSQL 表結構) | dev / staging / prod | `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` | 真實 PG 執行 `control_schema.sql` (checkpoints/staging_events) 驗證 | 部署執行 Migration | `BLOCKED_BY_EVIDENCE` (離線 SQL 待部署至實體叢集) |
| **CDC Live** | `ODP-CDC-LIVE-OPLOG-FAIL-CLOSED` (過期 Token 拒絕) | staging | `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` | Staging 注入過期 token 觸發 fail-closed 並自動排入批次回復日誌 | Staging 故障注入演練 | `BLOCKED_BY_EVIDENCE` |
| **CDC Live** | `ODP-CDC-MACHINE-EVENT-LIFECYCLE` (生命週期欄位) | codebase / dev | `ODP-CDC-MACHINE-EVENT-LIFECYCLE-001` | `core.machine_status_events` 欄位 migration 與 soft-retirement 程式/測試 | 執行本輪修正任務 | `IN_PROGRESS` |
| **業務營運** | `ODP-PARTIAL-LIVE-H06` (H06 業務範圍) | prod | `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001` | 具名業務負責人核准之 Partial 功能營運範圍裁定 | 業務負責人簽核 | `PENDING_HUMAN_AUTHORITY` |
| **業務營運** | `ODP-ADJUST-OPERATIONAL-CONFIRMATION` (調整確認) | prod | `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001` | 營運主管確認之價格與配額調整規則備忘錄 | 營運主管簽核 | `PENDING_HUMAN_AUTHORITY` |
| **業務營運** | `ODP-AVM-FINANCE-CUTOVER` (AVM 財務切換) | prod | `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001` | 財務主管核准之估價模型與財務計算切換簽署件 | 財務主管簽核 | `PENDING_HUMAN_AUTHORITY` |
| **Merge Queue** | `ODP-MERGE-QUEUE-BATCH-FORMATION` (多 PR 批次) | dev | `ODP-MERGE-QUEUE-H08-ACTIVATION-001` | GitHub Actions / Merge Queue 審計日誌證明 >= 2 PR 同時排隊時形成批次 | 自然 PR 排隊流量 | `BLOCKED_BY_EVIDENCE` (Option B 已寫入，待流量觀測) |
| **Merge Queue** | `ODP-MERGE-QUEUE-HOLD-TIMEOUT` (單 PR 等待 <= 10m) | dev | `ODP-MERGE-QUEUE-H08-ACTIVATION-001` | Merge Queue 延遲紀錄證明單 PR hold 不超過 10 分鐘 | 單 PR 排隊情境 | `BLOCKED_BY_EVIDENCE` |
| **Merge Queue** | `ODP-MERGE-QUEUE-ALLGREEN-REBUILD` (失敗剔除重建) | dev | `ODP-MERGE-QUEUE-H08-ACTIVATION-001` | 審計日誌證明批次內失敗 PR 被剔除且其餘 PR 重建合併 | 批次測試失敗情境 | `BLOCKED_BY_EVIDENCE` |
| **人工簽核** | `ODP-SIGN-OFF-UAT` (跨角色 UAT 簽核) | staging / prod | `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001` | 全角色 (Operator, Manager, Franchisee) 零 P0/P1 缺陷簽署件 | Staging UAT 完成 | `PENDING_HUMAN_AUTHORITY` |
| **人工簽核** | `ODP-SIGN-OFF-MODEL-RISK` (模型風險簽核) | staging / prod | `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001` | ML Risk Owner 簽署之 ForecastOps 模型卡、驗證基準與 production alias | 模型主管審查 | `PENDING_HUMAN_AUTHORITY` |
| **人工簽核** | `ODP-SIGN-OFF-OPS-GO-NO-GO` (最終 Go/No-Go) | prod | `ODP-PV-008` (Human/Ops) | `PRODUCT_RELEASE_GO_NO_GO.md` 與 `RELEASE_GATE_REGISTRY.json` 具名簽署 | Gate 0-6 滿足 | `PENDING_HUMAN_AUTHORITY` |
| **延後項** | `ODP-DEFERRED-ROOT-CAUSE-WAVE-5` (Wave 5+ 根因) | codebase | `ODP-ROOT-CAUSE-WAVE5-001` | 納入上線後維護週期 Backlog | 上線後排程 | `DEFERRED_NON_BLOCKING` |
| **延後項** | `ODP-DEFERRED-GOOGLE-OAUTH` (Google OAuth) | codebase | `ODP-AUTH-GOOGLE-OAUTH-001` | D15 決策合規 (維持帳密登入，OAuth 保持停用) | 未來需求評估 | `DEFERRED_NON_BLOCKING` |

---

## 3. 舊 Open PR 逐一對照與處置建議

| 儲存庫 | PR 編號 | 分支名稱 | 標題 | 狀態 | 差異規模與內容 | 與 dev 替代關聯 / 處置證據 | 處置建議 |
|---|---|---|---|---|---|---|---|
| `odayplus` | **#1243** | `task/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002-CLEAN` | ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002: clean evidence from C | OPEN | 僅 docs/evidence (9 月 8 日嘗試將 C04/33942097235 重綁入 Gate Registry) | 已被後續候選重綁任務 `ODP-DEV-CANDIDATE-REBIND-B175-001` (PR #1387, #1390) 與 `ODP-DEV-CANDIDATE-REBIND-6140D0EF-001` (PR #1392) 完整取代。當前 dev 已綁定至候選 `6140d0ef`。無遺留程式。 | **建議關閉 (Superseded)** |
| `odayplus` | **#1205** | `task/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002` | [ReviewBus] ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002 以最新 build receipt 重整 dev candidate gate | OPEN | 舊版 candidate 3b5de7e7 綁定嘗試 | #1243 之早期版本，同樣已被後續 PR #1387, #1390, #1392 取代。 | **建議關閉 (Superseded)** |
| `odayplus` | **#1052** | `task/ORCH-PROVIDER-QUOTA-SIGNAL-AUTHORITY-001-SIDECAR-BLOCKED-TASK-DIAGNOSTICS` | ORCH-PROVIDER-QUOTA-SIGNAL-AUTHORITY-001-SIDECAR-BLOCKED-TASK-DIAGNOSTICS: 建立阻塞診斷交接包 | OPEN | 新增 `docs/evidence/execution-control/.../SIDECAR_BLOCKED_TASK_DIAGNOSTICS.md` (5 檔診斷) | 診斷 review churn 期間 `is_success` 短路問題。主線 dev 已包含 PR #1045 之主要重構與 failure policy 修復。此 PR 為診斷紀錄。 | **建議結案/保留歷史 (Historical Diagnostic)** |
| `odayplus` | **#986** | `fix/quarantine-clean-is-not-unreadable` | orchestrator: a clean worktree is not an unreadable one | OPEN | 修改 `worker_workspace.py` 與 `test_supervisor.py` | 解決乾淨工作區被誤判為 `status_unreadable` 的問題。主線 `origin/dev` 已在 `_quarantine_and_preserve_dirty_worktree` (L3628) 獨立實作 `worktree_clean` / `nothing_to_preserve` 處置。分支為非正規 `fix/*`。 | **建議關閉 (Superseded by mainline)** |
| `odayplus` | **#970** | `task/XR-CUTOVER-001` | [ReviewBus] XR-CUTOVER-001 Execute dual-run, reconciliation, cutover and rollback of legacy odayplus external ingestion | OPEN | 65 檔 (+1,511, -10,027) 嘗試整包刪除/改名 legacy 擷取模組 | **非垃圾，已完成 65 檔完整稽核**：`docs/evidence/completion/ODP-XR-CUTOVER-ACTIVATE-002/pr-970-disposition.md` 逐檔對照，拒絕毀滅性刪除以保有緊急回滾與測試能力；改由 PR #991 (`ODP-XR-CUTOVER-ACTIVATE-002`，commit `b32fd65f`) 實作單一控制面 `PLATFORM_PRIMARY` + 預設阻斷。所有有效變更均已在 dev 落地，無遺留未合併代碼。 | **建議關閉 (Superseded with 65-file audit)** |
| `odayplus` | **#607** | `agent/dev-progress-spec-gap-audit-20260803` | docs: audit latest dev progress against specification | OPEN | 新增 8 月 3 日早期盤點文件 `DEV_PROGRESS_SPEC_GAP_AUDIT_20260803.md` | 8 月初期盤點已被 9 月 3 日整改計畫 (`ODP_REMEDIATION_PLAN_2026-09-03.md`)、待決策事項 (`ODP_OPEN_DECISIONS_2026-09-03.md`) 及 `EXECUTION.md` 完整取代。 | **建議結案/歷史存檔 (Historical Snapshot)** |
| `oday-data-platform` | **#1** | `feat/transaction-automation` | [Feat] add transactions daily lookback automation | OPEN | 修改 `assets.py`, `schedules.py`, `test_assets.py` (7 月 6 日早期交易分割排程) | 7 月早期分支，已被現代 Dagster 資料管線架構與現行作用中交付 `DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001` (PR #77) / `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` (PR #63) 完全取代。 | **建議關閉 (Superseded by Dagster pipeline)** |

### 作用中正式交付 PR (Active In-flight PRs — 嚴禁重複開立或誤關閉)
- `alfloop-dev/odayplus` **#1381** (`task/ODP-DEV-LIVE-DEPLOYMENT-REPAIR-001`)
- `alfloop-dev/odayplus` **#1312** (`task/XR-EXT-OSS-FINAL-AUDIT-001`)
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
1. 承接矩陣結構與綱要合法性驗證。
2. 五項 NFR 之正確時間窗定義（24h、7 營運日、1 個月、1 次演練）、零假數字、BATCH 截止時刻必須為 Human/Ops 之防偽驗證。
3. CDC live 六項缺口、業務確認三項、Merge Queue 三項、跨角色簽核三項之綁定驗證。
4. 負向測試：防止 archived 工程 done 被推成 live done、防止缺少收據時宣稱 live 通過、防止部署前准入循環依賴部署後觀測窗。
5. `PRODUCT_RELEASE_GO_NO_GO.md` 歷史脈絡與唯一發布閘門連結驗證。
6. 舊 PR 處置對照（含 #970 65 檔處置與 DPF #1）與作用中交付防重複驗證。
