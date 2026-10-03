# ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001: 未完成 Live 驗收承接與舊 PR 處置對照報告

- **Task ID**: `ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001`
- **負責人 (Owner)**: `Claude`（2026-10-03 由 Antigravity3 改派）
- **評審人 (Reviewer)**: `Codex`
- **參照基礎**: `origin/dev` @ `734dcb652edf28b92cdc0bf5fd1e7b983ed46837`
- **執行依據**: `support/handoffs/remaining-work-corrections-20261003/EXECUTION.md`
- **機讀承接矩陣**: [`obligation_matrix.json`](obligation_matrix.json)
- **舊 PR 處置矩陣**: [`pr_disposition_matrix.json`](pr_disposition_matrix.json)
- **Owner inventory**: [`owner_inventory.json`](owner_inventory.json)（canonical `ai-status.json` 與 archive 唯讀讀回，含 sha256 provenance）

---

## 1. 核心原則與邊界切分

本任務落實「未完成 live 驗收承接」與「舊 open PR 處置對照」，嚴格遵循以下工程邊界：

1. **程式交付 (Code Delivered)、Live 驗收 (Live Validated) 與 人工批准 (Formally Signed Off) 嚴格切分**：
   - 程式碼已合併或離線測試綠燈（`is_engineering_done = True`），**不等於** live 運行環境驗收完成（`is_live_done = False`）。
   - 任何涉及 live 運行環境的 NFR 觀測窗、CDC 實體叢集驗證、Merge Queue 真實流量觀測，均維持 `BLOCKED_BY_EVIDENCE` 直至真實部署與收據產生。
2. **歷史已完成工程 (Historical Source Task) 與當前作用中執行通道 (Active Canonical Lane) 嚴格分離**：
   - 已 archived done 的任務（`owner_inventory.json` 的 `archived_tasks`，含 `ODP-MODEL-ARTIFACT-HISTORY-RECOVERY-001` 這類有界調查）只能當 `historical_source_task`。
   - `canonical_owner_task` 與 `prerequisite_tasks` 必須出現在 inventory 的 `active_tasks`（狀態 todo/in_progress/review/blocked），且該 lane 的 `lane_roles` 要涵蓋該項 stage；其他任意 ID 一律拒絕。
   - 找不到既有 lane 的項目（ADJUST、AVM Finance）記為 `OWNER_RECONCILIATION_REQUIRED`，列出提議 lane、需由誰做什麼與缺少的權責，不宣稱已承接。Wave 5+ 是 backlog 參照（`deferred_backlog_ref`），不偽裝成 task。
3. **階段固定、拒絕循環依賴 (Anti-Circularity)**：
   - 每項只屬一個 stage：`pre_production_admission`、`code_remediation`、`production_cutover`、`post_deploy_observation`、`dev_runtime_observation`、`deferred`；stage 決定 `pre_prod_blocking`／`post_prod_observation`／`requires_production_deployment` 三旗標，不一致即拒絕。
   - 准入項不得需要 production 部署；production 上的 24h／多日／整月窗只能是 `post_deploy_observation`。
   - 原 SHARED-008 拆為 staging 准入讀回與 production 0%-green cutover 讀回；RPO-004、CDC DDL 同理；PARTIAL 拆為業務範圍裁定、staging intake 演練、production 驗收三段，保留原 live 義務。
4. **單一發布閘門真相 (Single Canonical Gate Truth)**：
   - 本任務產出承接矩陣與處置對照，**不重寫、不取代** `docs/evidence/gates/RELEASE_GATE_REGISTRY.json` 與 `RELEASE_MANIFEST.json`。
   - `PRODUCT_RELEASE_GO_NO_GO.md` 中的 2026-06-29 PR #82 歷史評估已被明確標記為歷史脈絡，當前發布狀態回歸唯一 Canonical Gate。
5. **不擅自簽署人類權限**：
   - 缺真實資料或批准時，絕不用假數據（synthetic fixture）或假簽名冒充 Human/Ops。
   - `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001` 的驗收只涵蓋 BRAND_TRANSFER／FORMAT_CONVERSION／LEASE／PARTIAL／CDC／merge queue 六項，不擴張為 ADJUST、Finance 或通用 UAT/ModelRisk/Ops 授權。

---

## 2. 跨領域未完成驗收承接 (Obligation Carryforward)

| 領域 | 項目 ID | Stage | 目標環境 | 承接 task（或缺口） | 前置 task | 權責 | 觸發條件 | 真實狀態 |
|---|---|---|---|---|---|---|---|---|
| NFR | `ODP-FR-SHARED-008-STAGING` | `pre_production_admission` | dev / staging | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | — | Human/Ops | Runtime Release deploy job conclusion 'success' on the staging environment for the exact candidate | `BLOCKED_BY_EVIDENCE` |
| NFR | `ODP-FR-SHARED-008-PROD-READBACK` | `production_cutover` | production | `ODP-PROD-BLUEGREEN-ROLLOUT-001` | — | Human/Ops | Human GO and valid Supervisor lease exist; production green deployed at 0% traffic with the same digests | `BLOCKED_BY_EVIDENCE` |
| NFR | `ODP-NFR-PERF-001-STAGING` | `pre_production_admission` | staging | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | — | Release / QA Lead | Ephemeral staging rollout completion with staging traffic injection | `BLOCKED_BY_EVIDENCE` |
| NFR | `ODP-NFR-PERF-001-PROD` | `post_deploy_observation` | production | `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` | — | Human/Ops | Production deployment running active traffic for at least 24 hours | `BLOCKED_BY_EVIDENCE` |
| NFR | `ODP-NFR-BATCH-002` | `post_deploy_observation` | production | `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` | — | Human/Ops | Live production deployment running scheduled daily batch jobs over 7 operating days | `BLOCKED_BY_EVIDENCE` |
| NFR | `ODP-NFR-AVAIL-003` | `post_deploy_observation` | production | `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` | — | Human/Ops | 1 full calendar month elapsed following live production deployment | `BLOCKED_BY_EVIDENCE` |
| NFR | `ODP-NFR-RPO-004-STAGING-DRILL` | `pre_production_admission` | staging | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | — | Human/Ops | Deployed staging database instance available for timed restoration drill | `BLOCKED_BY_EVIDENCE` |
| NFR | `ODP-NFR-RPO-004-PROD-BACKUP-READBACK` | `production_cutover` | production | `ODP-PROD-BLUEGREEN-ROLLOUT-001` | — | Human/Ops | Production green deployed at 0% traffic after Human GO | `BLOCKED_BY_EVIDENCE` |
| CDC Live | `ODP-CDC-LIVE-REPLICA-SET` | `pre_production_admission` | production | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | — | Human/DBA | Production MongoDB infrastructure inspection | `BLOCKED_BY_EVIDENCE` |
| CDC Live | `ODP-CDC-LIVE-LATENCY-STAGING` | `pre_production_admission` | staging | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | — | Data Platform Owner | Staging Scoped CDC ChangeStream sensor running against active database | `BLOCKED_BY_EVIDENCE` |
| CDC Live | `ODP-CDC-LIVE-LATENCY-PROD` | `post_deploy_observation` | production | `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` | — | Data Platform Owner | Production Scoped CDC ChangeStream running live stream | `BLOCKED_BY_EVIDENCE` |
| CDC Live | `ODP-CDC-LIVE-IAM-CREDENTIALS` | `pre_production_admission` | staging / production | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | — | Human/Ops | IAM / MongoDB role assignment | `BLOCKED_BY_EVIDENCE` |
| CDC Live | `ODP-CDC-LIVE-PG-DDL-STAGING` | `pre_production_admission` | dev / staging | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | — | Platform / Release Engineering | Staging rollout migration step for the exact candidate | `BLOCKED_BY_EVIDENCE` |
| CDC Live | `ODP-CDC-LIVE-PG-DDL-PROD-READBACK` | `production_cutover` | production | `ODP-PROD-BLUEGREEN-ROLLOUT-001` | — | Platform / Release Engineering | Production migration step of the blue-green rollout after Human GO | `BLOCKED_BY_EVIDENCE` |
| CDC Live | `ODP-CDC-LIVE-OPLOG-FAIL-CLOSED` | `pre_production_admission` | staging | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | — | Data Platform Owner | Staging fault injection with expired resume token | `BLOCKED_BY_EVIDENCE` |
| CDC Live | `ODP-CDC-MACHINE-EVENT-LIFECYCLE` | `code_remediation` | codebase / dev / staging / production | `ODP-CDC-MACHINE-EVENT-LIFECYCLE-001` | — | ODP-CDC-MACHINE-EVENT-LIFECYCLE-001 Owner | Execution of task ODP-CDC-MACHINE-EVENT-LIFECYCLE-001 | `IN_PROGRESS` |
| 業務營運 | `ODP-PARTIAL-H06-SCOPE-RATIFICATION` | `pre_production_admission` | production | `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001` | — | Human/Business Owner | Business stakeholder ratification under HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001 (SHARED-001 PARTIAL item) | `PENDING_HUMAN_AUTHORITY` |
| 業務營運 | `ODP-PARTIAL-H06-STAGING-INTAKE` | `pre_production_admission` | staging | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001` | Release / QA Lead | ODP-PARTIAL-H06-SCOPE-RATIFICATION recorded and staging deployed with the exact candidate | `BLOCKED_BY_EVIDENCE` |
| 業務營運 | `ODP-PARTIAL-H06-PROD-ACCEPTANCE` | `post_deploy_observation` | production | `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` | — | Human/Business Owner | Production cutover complete and live partial intake jobs executed in the watch window | `BLOCKED_BY_EVIDENCE` |
| 業務營運 | `ODP-ADJUST-OPERATIONAL-CONFIRMATION` | `pre_production_admission` | production | **缺 owner**：提議 `HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001`，待其 owner/reviewer 明確接受 | — | Operations Lead / Product Lead | Operations leadership review of intervention adjustment workflow | `OWNER_RECONCILIATION_REQUIRED` |
| 業務營運 | `ODP-AVM-FINANCE-CUTOVER` | `pre_production_admission` | production | **缺 owner**：提議 `ODP-PRODUCTION-MODEL-REGISTRY-001`、`HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001`，待其 owner/reviewer 明確接受 | — | Finance Owner (R-4 thresholds); Product Owner + ML Risk Owner (AVM promotion) | Finance owner review of depreciation contract and valuation benchmarks | `OWNER_RECONCILIATION_REQUIRED` |
| Merge Queue | `ODP-MERGE-QUEUE-BATCH-FORMATION` | `dev_runtime_observation` | dev | `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` | — | Platform Ops / Supervisor | 2+ eligible PRs queued concurrently under active dev development | `BLOCKED_BY_EVIDENCE` |
| Merge Queue | `ODP-MERGE-QUEUE-HOLD-TIMEOUT` | `dev_runtime_observation` | dev | `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` | — | Platform Ops / Supervisor | Single PR queued in dev merge queue | `BLOCKED_BY_EVIDENCE` |
| Merge Queue | `ODP-MERGE-QUEUE-ALLGREEN-REBUILD` | `dev_runtime_observation` | dev | `ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001` | — | Platform Ops / Supervisor | Test failure occurrence inside an active merge batch | `BLOCKED_BY_EVIDENCE` |
| 人工簽核 | `ODP-SIGN-OFF-UAT` | `pre_production_admission` | staging | `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | — | Human/QA & Product Owner | Live staging environment availability with UAT test data | `PENDING_HUMAN_AUTHORITY` |
| 人工簽核 | `ODP-SIGN-OFF-MODEL-RISK` | `pre_production_admission` | dev / staging | `ODP-PRODUCTION-MODEL-REGISTRY-001` | `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` | Product Owner + ML Risk Owner | Approved real history/model inputs and operational admission arrive for ODP-PRODUCTION-MODEL-REGISTRY-001 | `BLOCKED_BY_EVIDENCE` |
| 人工簽核 | `ODP-SIGN-OFF-OPS-GO-NO-GO` | `pre_production_admission` | production | `ODP-PROD-BLUEGREEN-ROLLOUT-001` | — | Human/Ops | All pre-production gates (Gates 0-6) satisfied on exact release candidate SHA | `PENDING_HUMAN_AUTHORITY` |
| 延後項 | `ODP-DEFERRED-ROOT-CAUSE-WAVE-5` | `deferred` | codebase | backlog 參照 `DEFERRED-BACKLOG-WAVE5`（非 task） | — | Platform / Quality Lead | Post-launch maintenance cycle prioritization | `DEFERRED_NON_BLOCKING` |
| 延後項 | `ODP-DEFERRED-GOOGLE-OAUTH` | `deferred` | codebase / dev / staging / production | `HUMAN-GCP-WEB-OAUTH-CLIENTS-001` | — | Product / Security Lead | Future optional feature prioritization | `DEFERRED_NON_BLOCKING` |

各項的原驗收出處、所需收據與觀測窗見 `obligation_matrix.json`。

---

## 3. 舊 Open PR 逐一對照與處置建議

| 儲存庫 | PR | Exact head | 規模 | 差異內容 | 已合併替代／現況 | 未落地程式 | 建議 |
|---|---|---|---|---|---|---|---|
| `odayplus` | **#1243** | `3a9fb628d83bb0ff39e36ef652d3e4164922e992` | 22 檔 +2429/-99 | 22 files: modifies docs/evidence/gates/README.md, RELEASE_GATE_REGISTRY.json and RELEASE_MANIFEST.json to bind candidate 596b9c9a1788d952811a2bf8d4bba8a4e4d76b12 with artifact-handoff Runtime Release run 34179791241 (images built by producer run 34179207603), and adds 19 evidence files under docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/ (including the historical-run-33942097235/ archive). | Evidence directory docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/ landed through PR #1246 (ODP-DEV-CANDIDATE-GATE-RECONCILIATION-003 root clean commit, merge 00c0347383806e8aa6679ed78215b4ac31b1da57) and PR #1292 (merge 3613faff582bd1c5a2c9b2ae5b5cd390d8be381f); RELEASE_GATE_REGISTRY/RELEASE_MANIFEST are now bound to candidate 6140d0ef633cbf94522c171d9927103ab200257f by PR #1392 (ODP-DEV-CANDIDATE-REBIND-6140D0EF-001, merge fdf0fb9fde1dfd8e5de7636bbc3b0f8ee16a4e63). | 無 | **可關閉 (Superseded)** |
| `odayplus` | **#1205** | `eaa7f8c51b81718a3582ce047fd86867c6db9eda` | 22 檔 +2450/-109 | Same 22 paths as #1243, also binding candidate 596b9c9a1788d952811a2bf8d4bba8a4e4d76b12 / run 34179791241 (producer run 34179207603). Against #1243 head the evidence differs only in wording of docs/evidence/gates/README.md, RELEASE_GATE_REGISTRY.json description, the evidence README.md and verification-transcript.txt; it is the earlier iteration on the non-CLEAN branch of the same task. | Evidence directory docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/ landed through PR #1246 (ODP-DEV-CANDIDATE-GATE-RECONCILIATION-003 root clean commit, merge 00c0347383806e8aa6679ed78215b4ac31b1da57) and PR #1292 (merge 3613faff582bd1c5a2c9b2ae5b5cd390d8be381f); RELEASE_GATE_REGISTRY/RELEASE_MANIFEST are now bound to candidate 6140d0ef633cbf94522c171d9927103ab200257f by PR #1392 (ODP-DEV-CANDIDATE-REBIND-6140D0EF-001, merge fdf0fb9fde1dfd8e5de7636bbc3b0f8ee16a4e63). | 無 | **可關閉 (Superseded)** |
| `odayplus` | **#1052** | `73a9ee4676b8ebd9526b9d2768cb8c88aa8cacbd` | 2 檔 +253/-3 | Modifies 2 files: delivery_toolchain/git/check_commit_trailers.py (adjusts task-ID-dependent subject length checks) and adds support/sidecars/ORCH-PROVIDER-QUOTA-SIGNAL-AUTHORITY-001-SIDECAR-BLOCKED-TASK-DIAGNOSTICS.md diagnosing worker_lifecycle failure policy. | PR #1045 and mainline supervisor failure policy in origin/dev, but trailer logic differences and sidecar doc are unmerged. | 有 | **保留／需比對 (Needs Comparison)** |
| `odayplus` | **#986** | `173f51c649ec52cf5073b306fddb38abb6fd2e31` | 2 檔 +50/-1 | Modifies worker_workspace.py and test_supervisor.py to carry git stderr into status_unreadable.detail and adds 43 lines of supervisor test coverage. | Partial: origin/dev independently added worktree_clean/nothing_to_preserve handlers but worker_workspace.py:3625 still drops git stderr. | 有 | **保留／需比對 (Needs Comparison)** |
| `odayplus` | **#970** | `e102821ccd5aa89e95b04d957e3b2fd863a8c083` | 65 檔 +1511/-10027 | 65 files changed (1,511 additions, 10,027 deletions) at head e102821c, as enumerated by `git diff --name-status -M c045f5f9...e102821c` in pr-970-disposition.md. | PR #991 (ODP-XR-CUTOVER-ACTIVATE-002, merge b32fd65f4e60e4814b6b96bf074c5dc34dec12d4) merged into origin/dev. | 無 | **可關閉 (Superseded)** |
| `odayplus` | **#607** | `a70b62ca13168927c1500c3e24a8603885681331` | 1 檔 +329/-0 | Added docs/evidence/DEV_PROGRESS_SPEC_GAP_AUDIT_2026-08-03.md (early August 2026 gap audit). | September 2026 remediation architecture: ODP_REMEDIATION_PLAN_2026-09-03.md, ODP_OPEN_DECISIONS_2026-09-03.md, EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md, and EXECUTION.md. | 無 | **可結案 (Historical Snapshot)** |
| `oday-data-platform` | **#1** | `0577773d0457d7b51e93d2e1913f70002227fdb4` | 6 檔 +305/-35 | 6 files: deploy/k8s/dev/configmap.yaml (+2), src/oday_data_platform/defs/assets.py (+10/-2), src/oday_data_platform/defs/raw_transactions.py (+57/-25), src/oday_data_platform/defs/schedules.py (added, +94), tests/test_assets.py (+73/-8), tests/test_schedules.py (added, +69): transaction daily-lookback job/schedule, env-configurable partition start (ODAY_TRANSACTIONS_AUTOMATION_LOOKBACK_DAYS), count-mismatch failure and automation run metadata. | Unmerged: DPF #63 and #77 are open masked-snapshot and bounded-capture work, not functional replacements. On dev 24c40ae7 schedules.py does not exist and raw_transactions.py keeps a fixed TRANSACTIONS_PARTITION_LOOKBACK_DAYS = 365 partition start. | 有 | **保留／需比對 (Needs Comparison)** |

比對基準：odayplus `origin/dev` `734dcb652edf`；data-platform dev `24c40ae7`（DPF#1 的 PR base 是 `main` `e079199b`）。本任務未關閉、合併或編輯任何 PR；建議僅供 PR 作者／owner 決定。

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
   - 對 inventory 內每個 archived task、任意未知 ID、backlog 參照、錯 lane owner 與非 active 狀態一律拒絕。
   - 拒絕無 `ownership_gap` 的缺 owner 項、提議 lane 不存在或缺 required_action 的缺口。
   - 拒絕空列表或包含空白字串 `[""]` 的 `required_receipts`。
   - 拒絕將尚未具備 live 證據的項目虛構標記為 `is_live_done = True`。
   - 拒絕循環依賴：stage 與三旗標不一致即拒絕；例如把 `ODP-CDC-LIVE-LATENCY-PROD` 改成 pre_prod_blocking=true/post=false（保留 post-deploy stage、owner 與 production 24h 窗）、改 stage、再改 owner 三種變形都會被擋；拆分前的 SHARED-008 形狀（准入卻需 production 部署）亦被擋。
3. **階段區分**：SHARED-008／RPO-004／CDC DDL 拆成 staging 准入與 production cutover；PERF、CDC latency、PARTIAL 的 production 部分屬 post-deploy；ModelRisk 綁 `ODP-PRODUCTION-MODEL-REGISTRY-001`（前置 `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001`），權責 Product Owner + ML Risk Owner（gate-3）。
4. **具體原驗收標準**：
   - PARTIAL 包含真實來源資料匯入、live 佇列政策套用與 production 驗收（ODP-DURABLE-PARTIAL-IMPL-001 README §5.3）。
   - ADJUST 包含門市營運實務（就地調整 vs 停舊開新）具名決策人、日期、範圍與來源依據（ODP_REQUIREMENT_DISPOSITIONS.md:211-225）。
   - AVM 包含 Contract R-4 數值／結構／校準三項回滾門檻（ODP_AVM001_DEPRECIATION_DISPOSITION_2026-09-04.md §4-5）。
5. **PRODUCT_RELEASE_GO_NO_GO 歷史脈絡與唯一發布閘門連結驗證**。
6. **舊 PR 處置對照**：精確記錄 exact PR head、檔數／行數、comparison base、diff 及未合併代碼現況，保護作用中交付。
