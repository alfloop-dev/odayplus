# ODP-CORE-OPS-PROD-READINESS-PACK-001：core-ops production 上線準備包（2026-11-27 版）

> **狀態：DRAFT · 未簽署 · 不具效力。**
> 本資料夾整理文件與證據對照，供負責人（蔡尚志）在選定 candidate 之後，對 production release gates 做具名決定。
> 這裡沒有任何簽署、核准、收據、digest 或 run ID 是本 task 產生的；所有簽名欄都留白。
> 負責人在 2026-11-27 與 2026-11-20 做的是**方向決定**（`live-readback-20261127.md` §2 的 A–H），**不是** gate 簽署。

- 原 task：ODP-CORE-OPS-PROD-READINESS-PACK-001（2026-10-24 版，基線 `5d761526`）
- 本版 task：ODP-CORE-OPS-PROD-GO-PACK-REFRESH-261127 · 準備者：Claude（LLM agent）· Reviewer：Antigravity
- 程式基線：`origin/dev` @ `48c5271b5d882d824d432f3483ab4415030bb8e6`（2026-11-27 讀取；最新合併是 PR #1763）
- GCP 與 GitHub 的現況：只引用 `live-readback-20261127.md`。那是 Claude 在另一個 session 以唯讀身分讀回、再寫進 task brief 的內容；本 task 沒有呼叫 GCP 或 GitHub API。

## 1. 範圍

**涵蓋：**

- `core-ops` release profile 晉升到 production 所需的文件：部署計畫、回滾計畫、監控與值班、runbook 差異、備份與 DR、UAT、gate 證據對照、release notes 草稿、人工決策清單，以及 2026-11-27 的 live 讀回紀錄。
- `core-ops` 的定義見 `docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md`：不宣稱模型就緒，缺模型時如實拒絕；production 只接受 staging 封存的 manifest（`:16-24`、`:56-75`）。決策日期 2026-10-19，該決策**不是** production GO，也**不是** gate-3 簽署（`:6-14`）。

**本 task 沒有做：**

- 沒有改本資料夾以外的任何檔案（含 product code、workflow、`docs/evidence/gates/RELEASE_GATE_REGISTRY.json`）。
- 沒有觸發任何 workflow，沒有呼叫 GCP 或 GitHub 的寫入 API。

所有檔案只放在本資料夾（task 範圍如此規定）。production dispatch 的 ref 必須是 candidate 的 evidence-only descendant（`.github/workflows/deploy-dev.yml:1104-1127` 呼叫 `delivery_toolchain/e2e/check_release_gate_registry.py:731` 的 `check_candidate_ancestry`）。「evidence-only」的路徑由同檔 `is_evidence_path`（`:691-728`）定義：`docs/evidence/`、`docs/release/`、`docs/runbooks/`、`docs/testing/`、`docs/uat/`、`docs/audits/code-boundary-inventory.csv`、`.orchestrator/`、`scripts/orchestrator/` 與 ai-status 看板相關檔案（`:708-726`）。改到這些路徑以外（product code、infra、workflow、`delivery_toolchain/`、dependency lock 等）才會讓 candidate 失效。本包其他檔案說「evidence 以外的路徑」時，都是指這個定義。

## 2. 檔案索引

| 檔案 | 內容 | 對應 gate check |
|---|---|---|
| `live-readback-20261127.md` | **新增。** 2026-11-27 的 live 事實（billing、production 基礎設施、監控、GitHub environment、鏈 U、watch receipt），逐項註明讀取方式；負責人決定 A–H | 全部（作為現況來源） |
| `deployment-plan.md` | workflow 實際執行的 production rollout：staging build → registry → lease → staging deploy 與演練 → registry → lease → production admission → promotion → 切換前狀態 → migration 前備份 → deploy → 0% 讀回 → 切換 → live gate → watch → staging closeout | gate-6「release note and deployment plan completed」 |
| `rollback-plan.md` | 自動與手動回滾；首次 release commit 後的下線程序（決定 D）與 staging 演練步驟；後續 release 的 blue-green rollback | gate-6「rollback plan completed, reviewed, and proven」；gate-3「human approval and rollback path recorded」 |
| `monitoring-oncall-plan.md` | Terraform 與 live 的告警、watch window、Claude 上線後 24 小時讀回、值班表 | gate-6「monitoring watch window and on-call route assigned and live」 |
| `runbook-core-ops.md` | core-ops 的營運差異：模型被拒絕時的現象、release identity 的讀法與所需身分、決定 D 的值班步驟 | gate-6「runbook updated for changed operational behaviour」 |
| `backup-dr-impact.md` | 備份、還原、DR 現況；production DR 演練程序（決定 E） | gate-6「backup/DR impact reviewed」 |
| `uat-plan.md` | UAT 範圍、staging 環境與帳號建立方式（決定 B）、測試案例、空白簽核欄 | gate-5 UAT 兩項 |
| `gate-evidence-map.md` | gate-2、3、5、6 每個 required_check 的狀態 | 全部 |
| `release-notes-draft.md` | 面向使用者的上線說明草稿 | gate-6「release note」 |
| `human-decisions-checklist.md` | HD-01 到 HD-32，每項標示已決定／已由程式解決／仍待決或待簽；簽名欄留白 | 全部 |

## 3. 與 2026-10-24 版相比的更正（以 `origin/dev` @ `48c5271b5` 核對）

| 2026-10-24 版的說法 | 現況 | 依據 |
|---|---|---|
| production 的 `capture-state` 參數順序錯誤，deploy job 會在切換後 failure；切換前狀態沒有保存 | **已修**（ODP-PROD-DEPLOY-SAFETY-001，PR #1594）。全域參數已放在子命令之前；這一步移到任何 Cloud Run 變更之前，切換前狀態以 artifact `production-bluegreen-prerelease-state-*` 上傳，保存 90 天 | `deploy-dev.yml:2034-2083`；`product_ops/deployment/bluegreen_release.py:757-773` |
| production 沒有 deploy 前的 Cloud SQL 備份 | **已修**（同一個 task）。migration 前建 on-demand 備份、讀回確認 `SUCCESSFUL`，收據上傳為 artifact | `deploy-dev.yml:2086-2175`；`infra/terraform/iam.tf:236-262` |
| production 能否從 staging 的 Artifact Registry 拉 image 未驗證 | **改為 promotion**（ODP-PROD-IMAGE-PROMOTION-001，PR #1596）。四個 image 依 digest 複製到 production 自己的 registry 並驗簽；部署腳本拒絕其他 registry | `deploy-dev.yml:1989-2032`、`:2181-2187`；`product_ops/deployment/deploy_cloud_run_waji.sh:432-440`；design `:410-485` |
| PR #1565 待合併 | **已合併**（2026-10-24，`9a346570a`） | `delivery_toolchain/release/release_manifest.py:357`；design `:118-204` |
| Terraform 沒有任何 Cloud Monitoring 資源；production 沒有告警 | **`monitoring.tf` 已存在**（ODP-PROD-MONITORING-ALERTING-001，PR #1593）；live 已有 email 通道、6 條告警、Web uptime（`L-3`） | `infra/terraform/monitoring.tf:1-435`；`infra/terraform/main.tf:87`；`infra/terraform/checks.tf:175-182` |
| staging 沒有密碼帳號路徑，UAT 不能在 staging 跑 | **已有**（PR #1737，2026-11-22）。每個 staging release 有自己的 bootstrap `platform_admin`（`staging-admin`）與兩個密碼 secret；live gate 以密碼登入證明 | `product_ops/deployment/staging_lifecycle.py:194-204`；`infra/terraform/modules/ephemeral_staging/main.tf:668-745`；`outputs.tf:101-110` |
| staging verify 有 10 個 stage | **11 個**，新增 `web_custom_domain`：把 release 接到 `oday-staging-web-lb`，讀回 DNS、受管憑證與 custom domain 的 HTTPS | `staging_lifecycle.py:2868-2886`、`:3971`、`:183-192` |
| `staging_closeout` 找不到 watch receipt 會失敗 | **已改為 defer**：保留 staging、job 成功並提示重跑 | `deploy-dev.yml:2674-2707` |
| 切換時沒有暫停排程 | trigger 以 PAUSED 指向 candidate，live gate 通過後才恢復 | `deploy_cloud_run_waji.sh:1397-1408`、`:1520-1532`；`product_ops/deployment/cloud_scheduler_trigger.py:371` |
| F2：production Terraform 的模型 precondition 擋住 root stack | **已解決**：只對 `full` profile 生效 | `checks.tf:131-155`；`main.tf:52-53` |
| production Cloud SQL 是 `db-f1-micro`、ZONAL、沒有 deletion protection | 那是舊的 `oday-prod-sql`，依決定 H 不動。新的 `oday-prod-foundation-sql` 是 REGIONAL、無 public IPv4、CMEK（`L-2.3`） | `infra/terraform/env/prod.tfvars.example:26-31`；`checks.tf:70-76` |

## 4. 哪些已證明、哪些沒有

### 4.1 程式或 live 讀回層級已具備

- **profile 機制**：core-ops 的晉升路徑、admission parity 與竄改拒絕都有離線測試（design `:293-305`）。
- **production 部署路徑（程式層）**：promotion、切換前狀態、migration 前備份、0% candidate、smoke、切換、live E2E gate、EXIT trap 回復（`deployment-plan.md` §2）。
- **staging 演練程式**：11 個 stage，最後是完整 live E2E gate（`staging_lifecycle.py:2868-2886`）。鏈 U 的 `8e5020a29` 曾在 staging 部署成功（`L-5.1`），但不採用（`L-5.2`）。
- **production 基礎設施**：VPC、KMS、Cloud SQL、WORM bucket（鎖定 7 年）、LB 與受管憑證都已存在（`L-2`）。
- **production 監控**：email 通道、6 條告警、Web uptime（`L-3`）。
- **GitHub environment**：production 主要變數已設定；required reviewers 是 `Alien-alfaloop`、`ajoe734`（`L-4.1`、`L-4.5`）。

### 4.2 沒有證明（沒有任何綁定 candidate 的 receipt）

- **core-ops 從未部署到 production。** production 只有 `oday-prod-mlflow`（`L-2.8`）。
- **registry 綁的是不採用的 candidate。** gate-3、gate-5、gate-6 都是 `blocked`、`receipts` 為空（`RELEASE_GATE_REGISTRY.json:583`／`:609`、`:702`／`:737`、`:751`／`:793`）；candidate C 尚未選定。
- **沒有** C 的 staging live gate receipt、效能量測、UAT 簽核、production 備份與 PITR 讀回、DR 演練。
- **回滾從未演練**：決定 D 的撤銷程序、後續 release 的 `bluegreen_release.py rollback` 都沒有 receipt。

## 5. 仍待決／待簽（摘要）

完整清單見 `human-decisions-checklist.md`。

**會擋 production 的程式或設定：**

1. **watch receipt 無法以真實資料產生**（`L-6`）。修正 task `ODP-PROD-WATCH-RECEIPT-REAL-METRICS-261127`：**pending**。若改到 evidence 以外的路徑，必須在凍結、選定 C 之前合併（HD-05）。
2. **`ODP_PRODUCTION_WATCH_CLOSEOUT_URI` 未設**（`L-4.3`）。`staging_closeout` 需要它（`deploy-dev.yml:2647`）。

**仍待決：**

- candidate C 的 SHA（決定 A 的程序，凍結時選定）（HD-05）。
- F1-a lineage 解鎖政策（HD-03）；F1-b 的 IAM（HD-04）。
- gate-0／1／4、gate-2 對 C 的處置（HD-07、HD-08）。
- production 未讀回的前提：AR promotion IAM、`GCP_CLOUD_SQL_INSTANCE`、備份與 PITR、`prodDeployerSqlBackup`、`production-build` 變數（HD-18、HD-19）。
- UAT：PriceOps／AdLift／Growth／Franchisee 是否納入；UAT-CO-11 在 staging 無法執行的處置（HD-13）。
- `ODP_PROD_API_INVOKER_GROUP`、應用層告警、Claude 讀回的身分與紀錄（HD-22）。
- DR 演練的執行身分與細節（HD-20）；各項人工操作的執行身分（HD-32）。
- staging closeout 在 receipt 修正前的清理方式（HD-28）。

**待簽（選定 C 之後）：**

- gate-3（方向已由決定 G 決定）、gate-5、gate-6 的簽署（HD-15、HD-16、HD-17）。
- release decision：staging（HD-09）、production GO（HD-24）。
- lease：staging（HD-10）、production（HD-25）。
- GitHub environment 核准：staging（HD-12）、production（HD-26）。
- 本包作為審閱基礎（HD-01）、release notes（HD-23）、回滾程序與演練結果（HD-21）、DR 演練結果（HD-20）、UAT 結果（HD-13、HD-16）。

## 6. 行號核對方式

- 本包所有 `file:line` 以 `origin/dev` @ `48c5271b5` 為準。
- 2026-10-24 版引用的每個檔案，都以 `git diff --numstat 5d761526 48c5271b5 -- <file>` 判斷是否變動：
  - **沒有變動的檔案**（例如 `shared/observability/watch_window.py`、`shared/auth/rbac.py`、`docs/runbooks/*`、`infra/monitoring/*`、`obligation_matrix.json`），原行號仍有效，沿用。
  - **有變動的檔案**（例如 `.github/workflows/deploy-dev.yml`、`deploy_cloud_run_waji.sh`、`staging_lifecycle.py`、`bluegreen_release.py`、`check_runtime_admission.py`、`RELEASE_GATE_REGISTRY.json`、`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md`、`apps/api/oday_api/main.py`、`infra/terraform/{checks,iam,main,variables}.tf`、部分 E2E spec），本版重新讀過並更新行號。
- 這只是文件層級的核對；本 task 沒有執行任何測試或部署。
