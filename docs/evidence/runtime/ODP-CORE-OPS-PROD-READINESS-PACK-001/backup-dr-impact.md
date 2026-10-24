# core-ops production 備份與 DR 影響審查（DRAFT · 未簽署）

行號以 `origin/dev` @ `5d761526` 為準。本 task 沒有呼叫 GCP，所有雲端現況都取自 repo 內既有的讀回證據，並註明讀取日期。

**結論先講：**

- **IaC 層級**：Terraform 為 production Cloud SQL 設計了自動備份、PITR 與 regional HA。
- **但這三點都沒有在 production 生效或讀回：**
  1. repo 內**沒有任何** production 備份讀回、還原或 DR 演練的 receipt。
  2. 2026-09-16 讀回的 production 執行個體 `oday-prod-sql` 與 IaC 不符：`db-f1-micro`、`ZONAL`、`deletionProtectionEnabled=false`，而且當時沒有讀取備份與 PITR 設定。
  3. 所有曾經執行過的備份、還原「演練」，對象都是本機、SQLite 或「staging-surrogate」。
- **gate-6「backup/DR impact reviewed」**：本文件是審查草稿，狀態為**部分**。

## 1. core-ops 對備份與 DR 的影響

| 面向 | core-ops 的變化 | 依據 |
|---|---|---|
| 交易資料（PostgreSQL） | **開始有真實的 production 資料**：使用者與帳號、角色、session、稽核、營運主控台寫入、worker 佇列。這是第一次有需要保護的 production 資料，RPO／RTO 從這時起才有實際意義 | `docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:24`；`deploy-dev.yml:1271`（`ODP_PERSISTENCE: postgresql`） |
| 模型 artifact | 不適用。core-ops 不綁定任何模型，`backup-and-restore.md` 中「model artifacts」那一列這次不會產生資料 | design `:160-165`；`docs/runbooks/backup-and-restore.md:20`、`:59` |
| 外部資料、snapshot | 外部來源全部關閉（`deploy-dev.yml:1270`），所以不會從第三方匯入資料 | 同左 |
| 稽核證據 | 稽核寫入成為 production 義務。WORM bucket 只有在 prod 才鎖定 retention（`infra/terraform/storage.tf:82`；`infra/terraform/audit/variables.tf:38-42`） | 見 §4 |
| 首次 release 失敗時 | 回復動作會刪除 service 與 job，但**不會**回復已執行的 migration（見 `rollback-plan.md` §2.3）。新的 production 資料庫在首次 release 時就會被 migration 改動 | `deploy_cloud_run_waji.sh:822-877` |

## 2. IaC 設計（Terraform）

| 項目 | 設定 | 依據 |
|---|---|---|
| Cloud SQL 定義位置 | `modules/runtime_foundation/database.tf`，由 `infra/terraform/network.tf:1-38` 引用 | |
| 備份 | `enabled = true`；`point_in_time_recovery_enabled = true`；`location = var.region`，同區域，沒有跨區備份；保留份數 `var.cloud_sql_retained_backups` | `infra/terraform/modules/runtime_foundation/database.tf:15-26` |
| HA | `availability_type = local.is_prod ? "REGIONAL" : "ZONAL"` | 同檔 `:9` |
| deletion protection | prod 與 staging 開啟，dev 關閉 | `infra/terraform/network.tf:19-22` |
| prod 的 PITR 下限 | transaction log 至少保留 7 天 | `database.tf:60-62` |
| prod 的容量與備份下限 | disk ≥ 100 GB、保留 ≥ 30 份備份、tier 至少 4 vCPU | `infra/terraform/checks.tf:43-52` |
| 預設值 | 保留 30 份；log 保留 7 天；備份開始時間 18:00 UTC | `infra/terraform/variables.tf:250`、`:261`、`:272` |
| prod 範本 | `env/prod.tfvars.example` 只是範本，其中的 `REPLACE_WITH` 值會被 production check 拒絕 | `infra/terraform/env/prod.tfvars.example:1-2`、`:11-13` |

**IaC 能否實際套用到 production：**

- production root stack 的 plan／apply 會被 `terraform_data.production_contract` 的模型 precondition 擋下，因為沒有真實的模型 artifact（design `:128-134`，後續項 F2）。
- Runtime Release 不執行 root stack（design `:135`）。

所以 Terraform 中的備份與 HA 設計，**不保證**存在於 production 實際的執行個體上。

## 3. 雲端現況（repo 內最後一次讀回）

| 環境 | 讀回內容 | 日期 | 依據 |
|---|---|---|---|
| production `oday-prod-sql` | `tier: db-f1-micro`、`availabilityType: ZONAL`、`deletionProtectionEnabled: false`、有 public IP。**沒有**讀取 backup 與 PITR 設定 | 2026-09-16 | `docs/evidence/runtime/ODP-PROD-NETWORK-PARITY-PLAN-001/README.md:220-237` |
| production 備份計畫 | 只提出 Phase 0 的 `gcloud sql backups create` 與 `export` 計畫，**沒有執行**。該 task 是零雲端變更 | 2026-09-16 | 同上 README `:22-24`、`:471-472` |
| staging foundation `oday-staging-foundation-sql` | ZONAL、`db-custom-2-7680`、PITR 開啟、log 保留 7 天、保留 30 份、deletion protection 開啟 | 2026-08-30 | `docs/evidence/runtime/ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001/live-foundation-readback-receipt.json:119-133` |

production 的現況是否已經改變，本包**未驗證**。

## 4. 稽核證據保存

| 項目 | 現況 | 依據 |
|---|---|---|
| WORM bucket | 開啟 versioning 與 CMEK；`retention_period = var.retention_period_seconds`；`is_locked = var.lock_retention_policy` | `infra/terraform/audit/main.tf:16-36` |
| 鎖定 | 只在 prod 鎖定（`lock_retention_policy = local.is_prod`） | `infra/terraform/storage.tf:82` |
| 保存期 | 預設約 7 年；prod 下限 `220752000` 秒 | `audit/variables.tf:30`；`checks.tf:138-143` |
| 應用層保存證明 | 只有整合測試（`docs/evidence/AUDIT_EVIDENCE_RETENTION_PROOF.md:60-90`） | 同左 |
| 應用層持久化接線 | **已存在**。PostgreSQL 模式下 `_postgres_bundle` 以同一個 WORM sink 建立 `DurableAuditLog`、`DurableEvidenceBundleStore` 與 canonical intake audit delivery；`build_persistence` 在 PostgreSQL 模式走這條路；API 啟動時取用這個 bundle 的 `audit_log` 與 `evidence_store`。production 模式下沒有 GCS sink URI，或缺 writer／recovery service account 時，會 fail closed。`AUDIT_EVIDENCE_RETENTION_PROOF.md:111-113` 仍把這項列為後續項，已過時 | `shared/infrastructure/persistence/factory.py:609-632`、`:709-725`；`apps/api/oday_api/main.py:180`、`:254-260`；`shared/audit/worm.py:450-480` |
| 尚未驗證 | production 實際的 sink 設定、WORM bucket retention 讀回、資料實際保存的效果；排程 retention sweep（repo 內只找到手動 API `POST /evidence/retention/purge`，沒有排程呼叫它）；部署環境的 decision log receipt | `AUDIT_EVIDENCE_RETENTION_PROOF.md:117-118`；`apps/api/app/routes/audit.py:505-515` |
| production WORM bucket 讀回 | repo 內**找不到** | |
| workflow 變數 | deploy job 讀取 `ODP_AUDIT_WORM_SINK_URI` 與相關 SA | `deploy-dev.yml:1216-1218` |

## 5. RPO／RTO 目標與演練

| 項目 | 內容 | 依據 |
|---|---|---|
| 核准的目標 | RPO ≤ 60 分鐘、RTO ≤ 240 分鐘 | `docs/design/ODP-PGAP-RELIABILITY-001_SLO_AND_DR_TARGETS_RATIFICATION.md:45-46` |
| 同一份文件的限制 | 「production-representative DR drill」是任何 production-go 決策前必須完成的後續項，該文件**沒有宣稱**已完成 | 同檔 `:56-58` |
| runbook | Cloud SQL 的 RPO 1 小時、RTO 4 小時；還原程序；每季演練 | `docs/runbooks/backup-and-restore.md:53-56`、`:65-78`、`:113-118` |
| DR 演練頻率 | 完整 DR 每 6 個月；還原演練每季 | `docs/runbooks/disaster-recovery-drill.md:12-14` |
| staging 演練 stage | `backup_restore_drill` 是 `gcloud sql export` 後再 `import` 回同一個 release DB 的邏輯匯出入。它不是從自動備份或 PITR 還原，也不量測 RPO／RTO。只有 staging 會執行 | `product_ops/deployment/staging_lifecycle.py:2691`、`:3345-3383`；`deploy-dev.yml:1528-1529` |
| 曾執行的演練 | 本機 `shutil.copy`（ratification 文件明言這不算證明）；`staging-surrogate`，Cloud SQL PITR 與 regional failover 列在 `not_executed_targets`；build 時的 SQLite E2E | `docs/evidence/completion/ODP-INTAKE-RELEASE-001/restore.json:1-10`；`docs/evidence/DEPLOYMENT_HEALTH_BACKUP_ROLLBACK_EVIDENCE.md:5-6`、`:58` |
| NFR 判定 | `ODP-NFR-RPO-004` 是 `BLOCKED_BY_EVIDENCE`，`runtime_receipts: []` | `docs/evidence/runtime/ODP-NFR-RUNTIME-EVIDENCE-001/disposition.json:165-183` |
| 待辦義務 | `ODP-NFR-RPO-004-STAGING-DRILL`：staging 的定時還原演練，**pre-prod blocking**。`ODP-NFR-RPO-004-PROD-BACKUP-READBACK`：production 的 backup／PITR 讀回 | `docs/evidence/completion/ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001/obligation_matrix.json:199-258` |

## 6. production 上線前需要的動作（需人工執行或決定）

1. **讀回 production Cloud SQL 的 `backupConfiguration` 與 PITR**，指令依 `disposition.json:174-176`。讀回結果寫成 receipt。這對應 `ODP-NFR-RPO-004-PROD-BACKUP-READBACK`。
2. **決定 production Cloud SQL 是否先升級到 IaC 基準**：tier、REGIONAL、deletion protection、關閉 public IP。這是 Infra owner 與 Human/Ops 的決定，在 F2 未解決前，不能靠 root stack apply 完成。
3. **在 production deploy 前做一次 backup checkpoint**（`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:265`），並記錄備份 ID。workflow 沒有這一步，需要人工執行。
4. **在 staging 做一次真正的定時還原演練**：從 PITR 或備份 clone 到某個時間點，量測 RPO 與 RTO，並跑核心查詢。這對應 `ODP-NFR-RPO-004-STAGING-DRILL`，是 pre-prod blocking。
5. **讀回 production 稽核 WORM bucket 的 retention policy，確認 `isLocked`。**
6. **確認首次 release 失敗後 migration 已套用的處理方式**，見 `rollback-plan.md` §2.3。

## 7. 簽核（留白）

| 項目 | 簽署人 | 角色 | 日期 | 決定 |
|---|---|---|---|---|
| 備份與 DR 影響已審閱 | | SRE Owner／Human/Ops | | □ 核准 □ 附條件 □ 退回 |
| production backup checkpoint ID | | Human/Ops | | 備份 ID：＿＿＿＿ |
| 接受「production DR 演練未完成」的風險（若選擇接受） | | 負責人 | | □ 接受 □ 不接受 |
