# core-ops production 備份與 DR 影響審查（DRAFT · 未簽署 · 2026-11-27 版）

- 行號以 `origin/dev` @ `48c5271b5d882d824d432f3483ab4415030bb8e6` 為準。
- GCP 現況引用 `live-readback-20261127.md`（`L-…`）。本 task 沒有呼叫 GCP。
- 負責人決定 A–H 見同檔 §2。本文件主要依決定 E（DR 演練）與決定 H（Cloud SQL 新建、WORM 鎖 7 年）。

**結論先講：**

- **production 資料庫已換成照規格新建的 `oday-prod-foundation-sql`**：REGIONAL、沒有 public IPv4、CMEK（`L-2.3`）。舊的 `oday-prod-sql`（ZONAL）依決定 H 不動（`L-2.4`）。2026-10-24 版所說「production 執行個體與 IaC 不符」，對新執行個體已不成立；它的備份與 PITR 設定**尚未讀回**。
- **每次 production 部署前都會自動建一份 on-demand 備份**，並讀回確認 `SUCCESSFUL`（ODP-PROD-DEPLOY-SAFETY-001，`deploy-dev.yml:2086-2175`）。2026-10-24 版的「production 沒有 deploy 前備份」已由程式解決。
- **稽核 WORM bucket 已鎖定 7 年**（`L-2.5`）。
- **仍然沒有任何 production 還原或 DR 演練的 receipt。** 依決定 E，GO 前要先做一次 production-representative DR 演練，程序見 §6。
- gate-6「backup/DR impact reviewed」：本文件是審查稿，狀態仍為**部分**；演練完成並留收據後才可能改為已有證據。

## 1. core-ops 對備份與 DR 的影響

| 面向 | core-ops 的變化 | 依據 |
|---|---|---|
| 交易資料（PostgreSQL） | **開始有真實的 production 資料**：使用者與帳號、角色、session、稽核、營運主控台寫入、worker 佇列。RPO／RTO 從這時起才有實際意義 | `docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:22-24`；`deploy-dev.yml:1746`（`ODP_PERSISTENCE: postgresql`） |
| 模型 artifact | 不適用。core-ops 不綁定任何模型 | design `:240-245`；`docs/runbooks/backup-and-restore.md:20`、`:59` |
| 外部資料、snapshot | 外部來源全部關閉，不會從第三方匯入資料 | `deploy-dev.yml:1745` |
| 稽核證據 | 稽核寫入成為 production 義務；WORM bucket 已鎖定 | §4 |
| 首次 release 失敗時 | 回復動作刪除 service 與 job，但**不會**回復已執行的 migration。migration 前的 on-demand 備份是唯一的資料回復點 | `rollback-plan.md` §2.2；`deploy-dev.yml:2086-2175` |

## 2. IaC 設計（Terraform）

| 項目 | 設定 | 依據 |
|---|---|---|
| Cloud SQL 定義位置 | `modules/runtime_foundation/database.tf`，由 `infra/terraform/network.tf:1-38` 引用 | |
| production 執行個體名稱 | `oday-prod-foundation-sql`；不得沿用或 import `oday-prod-sql` | `infra/terraform/env/prod.tfvars.example:26-31`；`infra/terraform/checks.tf:70-76` |
| 備份 | `enabled = true`；`point_in_time_recovery_enabled = true`；`location = var.region`（同區域，沒有跨區備份）；保留份數 `var.cloud_sql_retained_backups` | `infra/terraform/modules/runtime_foundation/database.tf:15-26` |
| HA | prod 是 `REGIONAL` | 同檔 `:9` |
| 網路與加密 | `ipv4_enabled = false`；CMEK | 同檔 `:5`、`:29` |
| deletion protection | prod 與 staging 開啟 | `infra/terraform/network.tf:22` |
| prod 的 PITR 下限 | transaction log 至少保留 7 天 | `database.tf:60-62` |
| prod 的容量與備份下限 | disk ≥ 100 GB、保留 ≥ 30 份、tier 至少 4 vCPU；範本是 `db-custom-4-15360` | `infra/terraform/checks.tf:60-67`；`prod.tfvars.example:29-31` |
| 預設值 | 保留 30 份；log 保留 7 天；備份開始時間 18:00 UTC | `infra/terraform/variables.tf:282`、`:293`、`:304` |
| migration 前備份的權限 | production deployer 的自訂角色 `prodDeployerSqlBackup`：`cloudsql.backupRuns.create/get/list`、`cloudsql.instances.get` | `infra/terraform/iam.tf:236-262` |

**IaC 能否套用到 production：** 2026-10-24 版的 F2（模型 precondition 擋住 production root stack）已由程式解決，模型相關 precondition 只在 `release_profile == "full"` 時生效（`checks.tf:131-155`）。live 上 `oday-prod-foundation-sql`、VPC、KMS keyring 都已存在（`L-2.1`–`L-2.3`），看起來 root stack 已套用到 production，但 Terraform apply 的紀錄**沒有讀回**。

## 3. 雲端現況

| 環境 | 內容 | 來源 |
|---|---|---|
| production `oday-prod-foundation-sql` | REGIONAL、沒有 public IPv4、CMEK 使用 keyring `oday-prod-runtime` | `L-2.3` |
| 同上：備份與 PITR | **未讀回**。IaC 設計見 §2 | — |
| production 舊 `oday-prod-sql` | ZONAL，依決定 H 不動 | `L-2.4` |
| billing 中斷 | 2026-11-26 07:18Z 起停用，2026-11-27 05:3xZ 改綁後恢復；Cloud SQL 06:04Z 全部 `RUNNABLE` | `L-1` |
| `GCP_CLOUD_SQL_INSTANCE`（production environment） | migration 前備份讀這個變數決定對哪個執行個體備份（`deploy-dev.yml:2095-2104`）。它是否指向 `oday-prod-foundation-sql`，**未讀回** | — |

**billing 中斷的影響（需要讀回）：** billing 停用期間，Cloud SQL 的自動備份與 transaction log 可能沒有正常產生。DR 演練前要先讀回 `oday-prod-foundation-sql` 的備份清單，確認恢復後已有新的自動備份（§6 步驟 0）。

## 4. 稽核證據保存

| 項目 | 現況 | 依據 |
|---|---|---|
| WORM bucket | `oday-prod-audit-worm-odayplus-prod-20260826`，retention `220924800` 秒（7 年），**已鎖定** | `L-2.5` |
| IaC | versioning、CMEK；只在 prod 鎖定；prod 下限 `220752000` 秒 | `infra/terraform/audit/main.tf:16-36`；`infra/terraform/storage.tf:80-82`；`checks.tf:170` |
| 應用層持久化接線 | PostgreSQL 模式下，`_postgres_bundle` 以同一個 WORM sink 建立 `DurableAuditLog` 與 `DurableEvidenceBundleStore`；`build_persistence` 在 PostgreSQL 模式走這條路；API 啟動時取用 bundle 的 `audit_log` 與 `evidence_store`。production 缺 sink 或 SA 時 fail closed | `shared/infrastructure/persistence/factory.py:654`、`:768-778`、`:830`；`apps/api/oday_api/main.py:263`、`:269`；`shared/audit/worm.py:450-480` |
| workflow 變數 | deploy job 讀 `ODP_AUDIT_WORM_SINK_URI` 與 writer、recovery SA | `deploy-dev.yml:1691-1693` |
| 尚未驗證 | production 的 sink 變數是否指向上面的 bucket；資料實際寫入的效果；排程 retention sweep（repo 內只有手動 `POST /evidence/retention/purge`） | `apps/api/app/routes/audit.py:505` |

## 5. RPO／RTO 目標與演練

| 項目 | 內容 | 依據 |
|---|---|---|
| 核准的目標 | RPO ≤ 60 分鐘、RTO ≤ 240 分鐘 | `docs/design/ODP-PGAP-RELIABILITY-001_SLO_AND_DR_TARGETS_RATIFICATION.md:45-46` |
| 同一份文件的限制 | production-representative DR drill 是 production-go 前必須完成的後續項 | 同檔 `:56-58` |
| 負責人決定 | **GO 前先做 DR 演練**（決定 E），不採「具名接受未演練風險」 | `live-readback-20261127.md` §2 E |
| runbook | Cloud SQL 的 RPO 1 小時、RTO 4 小時；還原程序；每季演練 | `docs/runbooks/backup-and-restore.md:53-56`、`:65-78`、`:113-118` |
| staging 演練 stage | `backup_restore_drill` 是 `gcloud sql export` 後再 `import` 回同一個 release DB。不是從自動備份或 PITR 還原，也不量 RPO／RTO | `product_ops/deployment/staging_lifecycle.py:2879`、`:4414-4450` |
| 曾執行的演練 | 本機 `shutil.copy`；`staging-surrogate`；build 時的 SQLite E2E。都不是 production-representative | `docs/evidence/completion/ODP-INTAKE-RELEASE-001/restore.json:1-10`；`docs/evidence/DEPLOYMENT_HEALTH_BACKUP_ROLLBACK_EVIDENCE.md:5-6`、`:58` |
| NFR 判定 | `ODP-NFR-RPO-004` 是 `BLOCKED_BY_EVIDENCE`，`runtime_receipts: []` | `docs/evidence/runtime/ODP-NFR-RUNTIME-EVIDENCE-001/disposition.json:165-183` |
| 待辦義務 | `ODP-NFR-RPO-004-STAGING-DRILL`（pre-prod blocking）；`ODP-NFR-RPO-004-PROD-BACKUP-READBACK` | `docs/evidence/completion/ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001/obligation_matrix.json:199-258` |

## 6. production-representative DR 演練程序（決定 E）

**目的：** 以真實的 `oday-prod-foundation-sql` 證明「備份可以還原到另一個執行個體」，並量測 RTO 與 RPO，對照 RPO ≤ 60 分、RTO ≤ 240 分。

**前提與限制：**

- **建立臨時執行個體之前，以及刪除之前，Claude 都會再向負責人確認**（決定 E）。下面每個標「確認點」的步驟，沒有負責人當次的明確同意就不執行。
- 執行身分要能建立／刪除 Cloud SQL 執行個體並執行還原。唯讀的 `deborah.lu` 不行；實際使用的身分**仍待決**。
- 臨時執行個體會產生費用；`odayplus-prod-20260826` 的 billing 剛恢復（`L-1`）。
- 演練**只讀**正式執行個體（建一份 on-demand 備份），不寫入正式資料。
- 本程序**沒有**實際執行過。下面的 gcloud 命令依 gcloud 文件的一般形式撰寫，執行前要以 `--help` 再確認一次參數。

### 6.0 讀回基準（唯讀）

```bash
PROJECT=odayplus-prod-20260826
SRC=oday-prod-foundation-sql

gcloud sql instances describe "$SRC" --project="$PROJECT" --format=json > dr-src-describe.json
# 記下：databaseVersion、settings.tier、region、settings.availabilityType、
#       settings.ipConfiguration.privateNetwork、diskEncryptionConfiguration.kmsKeyName、
#       settings.backupConfiguration（enabled、pointInTimeRecoveryEnabled、
#       transactionLogRetentionDays、backupRetentionSettings）
gcloud sql backups list --instance="$SRC" --project="$PROJECT" --format=json > dr-src-backups-before.json
# 確認 billing 恢復後（L-1.2 之後）已有 SUCCESSFUL 的自動備份
```

這一步同時滿足 `ODP-NFR-RPO-004-PROD-BACKUP-READBACK` 的讀回要求（`obligation_matrix.json:199-258`）。

### 6.1 建立演練備份

```bash
DESC="odp-dr-drill-$(date -u +%Y%m%dT%H%MZ)"
T0=$(date -u +%FT%TZ)                       # 量測起點之一：開始備份
gcloud sql backups create --instance="$SRC" --project="$PROJECT" --description="$DESC"
gcloud sql backups list --instance="$SRC" --project="$PROJECT" --format=json > dr-src-backups-after.json
# 取出 description == $DESC 的那一筆：id、status（必須 SUCCESSFUL）、type（ON_DEMAND）、
# windowStartTime、endTime。判斷方式與 workflow 的 migration 前備份相同（deploy-dev.yml:2120-2163）。
```

### 6.2 建立臨時執行個體（**確認點**）

```bash
TMP=oday-prod-dr-drill-$(date -u +%Y%m%d)
gcloud sql instances create "$TMP" --project="$PROJECT" \
  --region=<與 SRC 相同> --database-version=<與 SRC 相同> --tier=<與 SRC 相同> \
  --network=<SRC 的 privateNetwork> --no-assign-ip \
  --disk-encryption-key=<SRC 的 kmsKeyName> \
  --availability-type=ZONAL --no-deletion-protection
```

- 參數取自 6.0 的 `dr-src-describe.json`。臨時執行個體用 ZONAL、不開 deletion protection，方便事後刪除；這不影響還原驗證。
- CMEK 金鑰的使用權限：Cloud SQL service agent 對該金鑰必須有 encrypter/decrypter。正式執行個體已在使用同一把金鑰，預期可用，但**未讀回**。

### 6.3 還原並計時

```bash
T1=$(date -u +%FT%TZ)                       # RTO 起點：開始還原
gcloud sql backups restore <BACKUP_ID> --project="$PROJECT" \
  --restore-instance="$TMP" --backup-instance="$SRC"
gcloud sql operations list --instance="$TMP" --project="$PROJECT" --limit=5 --format=json > dr-tmp-operations.json
# 取 RESTORE_VOLUME 那一筆的 startTime、endTime、status（DONE、沒有 error）
gcloud sql instances describe "$TMP" --project="$PROJECT" --format='value(state)'   # 必須 RUNNABLE
```

### 6.4 驗證資料並量測

臨時執行個體只有 private IP。資料核對要在能連到該 VPC 的地方執行（例如 Cloud SQL Studio，或 VPC 內的一次性 job）；用哪一種**待負責人確認**。

| 量測 | 怎麼量 | 對照 |
|---|---|---|
| **RTO** | `T2 − T1`。`T2` 是還原完成、執行個體 `RUNNABLE`、而且下面的核心查詢都成功的時間 | ≤ 240 分鐘 |
| **RPO（備份還原）** | 在臨時執行個體上查一張只會新增的表（例如 `audit.audit_events`）的最新時間戳，對照備份的 `windowStartTime`。兩者差距代表這份備份涵蓋到哪個時間點。實際欄位名稱依 production schema 讀回確認 | 記錄實測值 |
| **RPO（可達成上限）** | 只靠每日自動備份時，最壞情況的資料遺失接近一天，**無法**滿足 ≤ 60 分。要滿足 ≤ 60 分必須依賴 PITR：6.0 讀回的 `pointInTimeRecoveryEnabled=true` 與 transaction log 保留天數是必要條件。若要實測，另以 `gcloud sql instances clone "$SRC" <TMP2> --point-in-time=<T>` 做一次 PITR clone，確認時間點 T 之前的資料存在；這會再建一個臨時執行個體，**是否執行需負責人另外確認** | ≤ 60 分鐘 |
| 核心查詢 | 至少：各 schema 的資料表數；`identity.accounts`、`audit.audit_events` 的筆數；最新一筆稽核的時間戳 | 與 6.1 時正式執行個體的同一組查詢比對（正式端的查詢也只讀） |

### 6.5 收據

收據寫成 JSON，放在新的 evidence 目錄（由執行演練的 task 建立）。欄位：

| 欄位 | 內容 |
|---|---|
| `kind` | `production-dr-drill` |
| `source_instance` | `oday-prod-foundation-sql`；6.0 讀回的 tier、version、availabilityType、backupConfiguration |
| `backup` | id、type、status、description、windowStartTime、endTime |
| `temporary_instance` | 名稱、建立時間、建立者身分 |
| `restore_operation` | operation id、startTime、endTime、status |
| `rto_minutes` | `T2 − T1` |
| `rpo_observed_minutes` | 6.4 的實測值與量法 |
| `rpo_capability` | PITR 是否開啟、log 保留天數；是否做了 PITR clone 與結果 |
| `verification_queries` | 查詢內容與兩端結果（只放筆數與時間戳，不放資料內容） |
| `owner_confirmations` | 建立與刪除前負責人確認的時間與管道 |
| `cleanup` | 刪除時間、刪除後讀回結果 |
| `result` | `pass`（RTO 與 RPO 都在目標內）或 `fail`，附原因 |

### 6.6 清理（**確認點**）

```bash
gcloud sql instances delete "$TMP" --project="$PROJECT"
gcloud sql instances list --project="$PROJECT" --filter="name=$TMP" --format='value(name)'   # 必須為空
```

- 6.1 的 on-demand 備份不會自動過期。要保留（作為演練證據）還是刪除（`gcloud sql backups delete <BACKUP_ID> --instance="$SRC"`），**待負責人決定**。
- 若做了 PITR clone，同樣刪除並讀回。

## 7. production 上線前需要的動作

1. **讀回 `oday-prod-foundation-sql` 的 `backupConfiguration` 與 PITR**（§6.0）。
2. **確認 production 的 `GCP_CLOUD_SQL_INSTANCE` 指向 `oday-prod-foundation-sql`**，以及 deployer 已有 `prodDeployerSqlBackup`（§2、§3）。否則 workflow 的 migration 前備份會失敗，部署停在 migration 之前。
3. **執行 §6 的 DR 演練並留收據**（決定 E）。
4. **staging 的定時還原演練**（`ODP-NFR-RPO-004-STAGING-DRILL`）：staging 的 `backup_restore_drill` 是 export／import，不能代替。§6 若在 production 完成，是否仍要在 staging 另做，**待負責人決定**。
5. **確認 production 的 `ODP_AUDIT_WORM_SINK_URI` 指向 `oday-prod-audit-worm-odayplus-prod-20260826`**（§4）。

## 8. 簽核（留白）

| 項目 | 簽署人 | 角色 | 日期 | 決定 |
|---|---|---|---|---|
| 備份與 DR 影響已審閱 | | SRE Owner／Human/Ops | | □ 核准 □ 附條件 □ 退回 |
| DR 演練結果（§6，決定 E） | | 負責人 | | □ 通過 □ 未通過　收據：＿＿＿＿ |
| 演練備份保留或刪除（§6.6） | | 負責人 | | □ 保留 □ 刪除 |
