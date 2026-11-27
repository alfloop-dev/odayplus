# core-ops production 部署計畫（DRAFT · 未簽署 · 2026-11-27 版）

本文件依照 `.github/workflows/deploy-dev.yml`（workflow 名稱 `Runtime Release`）與 `product_ops/deployment/deploy_cloud_run_waji.sh` 的**實際程式**描述 production rollout。凡是規劃文件與程式不一致之處，都在 §6 列出，並以程式為準。

- 行號以 `origin/dev` @ `48c5271b5d882d824d432f3483ab4415030bb8e6` 為準。
- GCP 與 GitHub 的現況一律引用 `live-readback-20261127.md` 的項目編號（`L-…`）。本 task 沒有自行查證。
- 負責人決定 A–H 見 `live-readback-20261127.md` §2。

## 0. 先決條件：現在還不能部署 production

production admission 需要同時滿足兩件事（`delivery_toolchain/release/check_runtime_admission.py:1-23`）：

1. **一份未消耗的 Supervisor lease。** lease 必須已簽署、未過期，並綁定 task、candidate SHA、manifest digest、環境與 action。
2. **registry 准許 production。** registry 要同時滿足：
   - `release.decision == "go"`（同檔 `:167-168`）。
   - `release.stage == "staging-verified"`，而且 `admission_target == "production"`（`:184-205`；stage 對照表在 `delivery_toolchain/e2e/check_release_gate_registry.py:67`）。
   - 每個 `admission_target == "production"` 的 gate 狀態都屬於 `passed`、`passed-with-deviation` 或 `not-applicable`（`check_release_gate_registry.py:55`；`check_runtime_admission.py:251-253`）。
   - 除 `not-applicable` 外，每個這類 gate 都有一筆 `result: pass`、而且綁定 candidate SHA 的 receipt（`check_runtime_admission.py:224-278`）。

目前綁定 production 的 gate 是 gate-3、gate-5、gate-6（`docs/evidence/gates/RELEASE_GATE_REGISTRY.json:588`、`:707`、`:756`）。三者都是 `blocked`，`receipts` 都是空的（`:583`／`:609`、`:702`／`:737`、`:751`／`:793`）。

registry 目前綁定鏈 U 的 candidate `8e5020a29bad…`，`stage: dev-verified`，`admission_target: staging`（`RELEASE_GATE_REGISTRY.json:15-31`）。依 `L-5.2`，這個 candidate **不採用**：dev 之後合併了含程式碼的變更，ancestry 讓它無法上 production。新的 candidate 必須重新建置，registry 也要重新綁定（見 §1）。

lease issuer 的檔頭仍寫著「還沒有 release 到達 `staging-verified`，所以 production 會被拒絕」（`.orchestrator/release_lease.py:23-26`）。這是以 gate stage 擋下，不是特例；registry 推進到 `staging-verified` 之後就不再擋。

**結論：在完成 `human-decisions-checklist.md` 的對應決策、並把 receipt 寫入 registry 之前，以下流程到步驟 7（production admission）就會被拒絕。**

### 0.1 與 2026-10-24 版相比，已由程式解決的阻擋項

| 2026-10-24 版的阻擋項 | 現況 | 依據 |
|---|---|---|
| production 的 `capture-state` 參數順序錯誤，deploy job 會在流量切到 100% 之後 failure，`staging_closeout` 被 skip | **已修**（ODP-PROD-DEPLOY-SAFETY-001，PR #1594，2026-10-24 合併）。`--project`、`--region` 已放在子命令之前；這一步移到**任何 Cloud Run 變更之前**，切換前的狀態以 artifact 上傳。切換後已沒有 `capture-state` 步驟 | `deploy-dev.yml:2034-2083`；`product_ops/deployment/bluegreen_release.py:757-773` |
| production 沒有 deploy 前的備份步驟 | **已修**（同一個 task）。migration 前先建 on-demand Cloud SQL 備份並讀回確認 `SUCCESSFUL`，收據以 artifact 上傳 | `deploy-dev.yml:2086-2175` |
| production runtime 能否拉 staging build 的 image | **改為 promotion**（ODP-PROD-IMAGE-PROMOTION-001，PR #1596，2026-10-26 合併）。production 從自己的 Artifact Registry 部署：admission 之後、任何 Cloud Run 變更之前，四個 image 依 digest 連同簽章與 SBOM attestation 複製過去，再讀回驗證 | `deploy-dev.yml:1989-2032`、`:2181-2187`；`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:410-485` |
| `staging_closeout` 在 watch receipt 還不存在時會失敗 | **已修**（ODP-PROD-DEPLOY-SAFETY-001）。receipt 不存在（404）時改為 defer：保留 staging、job 成功並提示重跑 | `deploy-dev.yml:2674-2707` |
| PR #1565（首次 release 回滾語意）待合併 | **已合併**（2026-10-24，merge commit `9a346570a`） | `docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:118-204` |
| production Terraform root 的模型 precondition（F2） | **已由程式解決**：模型相關 precondition 只在 `release_profile == "full"` 時生效 | `infra/terraform/checks.tf:131-155`、`:355-385`；`infra/terraform/main.tf:52-53` |
| production 沒有告警 | **已有**。Terraform 有 `monitoring.tf`；live 上已有 6 條告警政策與 email 通道（`L-3`） | `infra/terraform/monitoring.tf:1-435` |

### 0.2 仍然存在的阻擋項

1. **watch receipt 無法以真實資料產生**（`L-6`）。修正 task `ODP-PROD-WATCH-RECEIPT-REAL-METRICS-261127` 為 **pending**。在它合併並重新建置 candidate 之前，`staging_closeout` 只會 defer，staging 不會被自動清理（§5 第 1 點）。
2. **`ODP_PRODUCTION_WATCH_CLOSEOUT_URI` 未設定**（`L-4.3`）。`staging_closeout` 在 `Prepare release-scoped staging closeout paths` 就要求它（`deploy-dev.yml:2647`），沒設定時 job 會失敗。
3. gate-3、gate-5、gate-6 的處置、receipt 與簽署（`gate-evidence-map.md`）。

## 1. candidate 與晉升路徑

### 1.1 晉升規則（程式）

- core-ops 只能以 dev 或 staging 為建置目標；production 只接受 **staging 封存**的同一份 manifest，原樣晉升（`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:20-22`、`:56-75`）。
- workflow 的 `release_profile` 說明也寫明這一點（`deploy-dev.yml:150-155`）。
- 首次 release 的建置目標只能是 dev 或 staging（`delivery_toolchain/release/release_manifest.py:357`）。staging 封存的首次 release 紀錄可以 admission 到 staging 與 production（design `:157`）。

因此 production 部署一定是：**staging build（`initial_release_recovery=true`）→ staging deploy 與演練 → production deploy（重用 staging build 的 manifest 與四個 image digest，再 promotion 到 production registry）**。

### 1.2 candidate 選定程序（負責人決定 A，2026-11-27）

1. 在**不凍結**的情況下先把本包與相關文件備妥。
2. 凍結 `ready_dispatcher`，以當時的 `origin/dev` tip 作為 candidate C。
3. staging 通過後**直接**接 production，中間不再換 candidate。
4. production 完成後解凍。

**為什麼要凍結：** production dispatch 的 ref E 必須是 C 的 evidence-only descendant（`deploy-dev.yml:1104-1127`；規則在 `check_release_gate_registry.py:731`；evidence-only 路徑定義在 `:691-728`）。凍結期間 dev 若又合併了 evidence 以外的變更，E 就不再合格，必須重建 candidate。鏈 U 正是因此不採用（`L-5.2`）。

## 2. 步驟

| # | 步驟 | 實際執行者 | 人工動作 | 依據（file:line） |
|---|---|---|---|---|
| 1 | **Staging build**：`phase=build`、`environment=staging`、`release_profile=core-ops`、`initial_release_recovery=true` | GitHub Actions 的 `build` job，綁定 `staging-build` environment | 有 dispatch 權限的人觸發，填入 `release_sha`（C，40 字元）與 `task_id` | `deploy-dev.yml:54-160`、`:426-436` |
| 1a | build 內的檢查：secret scan、SAST、production npm audit、SBOM、本機 E2E 備份／還原／回滾證明 | 同上 | 無 | `deploy-dev.yml:623-656` |
| 1b | 讀回 staging target，確認沒有既有的已核准 release（首次 release） | 同上 | 無 | `deploy-dev.yml:693-720` |
| 1c | 建置、推送、Cosign 簽章、SBOM attest 四個 image，解析成 `@sha256` digest | 同上 | 無 | `deploy-dev.yml:724-834` |
| 1d | 寫出 build-once handoff 與 candidate manifest，上傳 artifact `runtime-release-manifest-<sha>` | 同上 | 記錄 run ID、`manifest_digest` 與四個 image ref。這些是之後 lease、deploy 與 promotion 的輸入 | `deploy-dev.yml:835-953` |
| 2 | **Registry 推進到 staging**：`stage=dev-verified`、`admission_target=staging`、`decision=go`；gate-2 必須 cleared 並有綁定 C 的 receipt | 由另一個 task 以 evidence-only commit 修改 registry | 負責人對 gate-2 做處置（HD-08），並簽 staging 的 release decision（HD-09） | `check_runtime_admission.py:167-205`；`RELEASE_GATE_REGISTRY.json:402`（gate-2 綁 staging） |
| 3 | **Staging lease**：Supervisor 以 `.orchestrator/release_lease.py issue` 簽發 | Supervisor（唯一持有 Ed25519 私鑰者） | 負責人提出或確認 lease 請求（HD-10） | `.orchestrator/release_lease.py:1-21` |
| 4 | **Staging deploy**：`phase=deploy`、`environment=staging`，帶入 `manifest_run_id`、`manifest_digest`、四個 image、`release_lease` | `admission` 與 `deploy` job，都綁定 `staging` environment | GitHub `staging` environment 的 required reviewers 核准。依 `L-4.5` 是 `Alien-alfaloop`、`ajoe734`（HD-12） | `deploy-dev.yml:954-962`、`:1644-1650` |
| 4a | admission：驗證 dispatch SHA 與 candidate 的 ancestry、image handoff，下載並綁定 manifest，檢查 environment 變數，重讀 target（首次 release），驗 lease 並以 CAS 消耗 | `admission` job | 無 | `deploy-dev.yml:1104-1442` |
| 4b | 首次 release 的 lineage：staging admission 會跨環境讀 production 的 lease store（`ODP_PRODUCTION_RELEASE_LEASE_STATE_URI`），只要 production 曾為別的 manifest 消耗過 lease 就拒絕 | 同上 | staging 讀 production lease bucket 的 IAM 需先就緒（HD-04）。變數本身已設定（`L-4.2`） | design `:154-164`、`:196-201` |
| 4c | `staging_lifecycle.py create` 建立 release-scoped 資源；`verify` 跑 **11** 個 stage，最後一個是 `live_e2e_gate`；接著驗 release-scoped staging authority endpoint | `deploy` job | 無 | `deploy-dev.yml:2272-2351`；`staging_lifecycle.py:2868-2886` |
| 4d | 失敗時 `hold`，保留 24 小時供鑑識 | `deploy` job | 指定鑑識負責人；需要延長時記錄原因（上限 168 小時）。之後以 `phase=cleanup` 清理 | `deploy-dev.yml:2352-2370`、`:2752-3399`；`staging_lifecycle.py:68-69` |
| 5 | **UAT 與 gate 證據收集**（staging 存活期間；TTL 預設 24 小時） | 負責人（UAT）；Claude（建帳號、讀回） | 見 `uat-plan.md` 與 `gate-evidence-map.md` | `staging_lifecycle.py:68`；負責人決定 B |
| 6 | **Registry 推進到 production**：`stage=staging-verified`、`admission_target=production`、`decision=go`；gate-3、gate-5、gate-6 cleared，並有綁定 C 的 receipt；寫入 `human_signoff` | 由另一個 task 以 evidence-only commit（E）修改 registry；E 必須是 C 的 evidence-only descendant | 負責人具名簽署 gate-3、gate-5、gate-6 與 release decision（HD-15、HD-16、HD-17、HD-24） | `check_runtime_admission.py:160-278`；`RELEASE_GATE_REGISTRY.json:26-30`（`human_signoff` 的既有格式） |
| 7 | **Production lease**：Supervisor 針對同一個 manifest digest 簽發 | Supervisor | 負責人提出或確認 lease 請求（HD-25） | design `:70-75`；`.orchestrator/release_lease.py:1-27` |
| 8 | **Production deploy dispatch**：`phase=deploy`、`environment=production`、`release_sha=C`；`manifest_run_id` 用**步驟 1 的 staging build run**；`manifest_digest` 與四個 image 用步驟 1d 的值；附 `release_lease` | 從 ref E dispatch | GitHub `production` environment 的 required reviewers 核准（`L-4.5`）。`admission` 與 `deploy` 兩個 job 都綁定 `production`（HD-26） | `deploy-dev.yml:959-960`、`:1649-1650` |
| 8a | production admission 額外帶 `--target-registry`（production 的 AR），只接受 manifest image 或它的 promoted copy | `admission` job | 無 | `deploy-dev.yml:1395-1419`；design `:432-445` |
| 9 | **環境變數綁定與 live preflight**：production 必須是 Direct VPC 加 all-traffic egress | `deploy` job | `L-4.1` 已設 `ODP_CLOUD_RUN_VPC_EGRESS=all-traffic` | `deploy-dev.yml:1892-1960`；`deploy_cloud_run_waji.sh:265-272` |
| 10 | **Image promotion**：下載 build run 的 manifest，把四個 image 依 digest 複製到 production registry，讀回並驗簽，上傳 `production-image-promotion-receipt-<sha>-<attempt>` | `deploy` job | AR 的 IAM 前置（design `:469-477`）**未讀回** | `deploy-dev.yml:1989-2032` |
| 11 | **切換前狀態**：`bluegreen_release.py --project … --region … capture-state …`；API 與 Web 必須同時存在或同時不存在；首次 release 兩者記為 absent。上傳 `production-bluegreen-prerelease-state-<sha>-<attempt>` | `deploy` job | 無。這份 artifact 是之後手動 rollback 的 `--state-file` 來源（`rollback-plan.md` §3） | `deploy-dev.yml:2034-2083` |
| 12 | **migration 前備份**：`gcloud sql backups create`，再 `list` 讀回，只接受這次建立、`SUCCESSFUL`、`ON_DEMAND` 的那一筆；上傳 `production-predeploy-cloud-sql-backup-<sha>-<attempt>` | `deploy` job | 記錄 backup ID。deployer 需要 `prodDeployerSqlBackup` 自訂角色（`infra/terraform/iam.tf:236-262`），live 是否已授予**未讀回** | `deploy-dev.yml:2086-2175` |
| 13 | **部署腳本開始**：profile 檢查（core-ops 只能進 dev、staging、production）；production 必須有 HTTPS 的 `ODP_PROD_DEPLOY_URL`、`ODP_PROD_API_URL`；min instances ≥ 2；production 只接受自己 registry 的 `@sha256` image | `deploy_cloud_run_waji.sh` | `L-4.1` 已設這三個變數 | `deploy_cloud_run_waji.sh:44-68`、`:115-130`、`:147-172`、`:420-440`；`deploy-dev.yml:2177-2207` |
| 14 | **記錄既有流量並啟動自動回復**：捕捉 API／Web traffic 與兩個 scheduler trigger，`ROLLBACK_ARMED=true` | 同上 | 無 | `deploy_cloud_run_waji.sh:1041-1053` |
| 15 | **Migration 與相容性 gate**：執行 migration job，再以舊 revision 驗新 schema；全新 target 改跑 bootstrap-compatibility | 同上 | migration 一旦執行就**不會**被回復（`rollback-plan.md` §4）；步驟 12 的備份是唯一的資料回復點 | `deploy_cloud_run_waji.sh:1055-1118` |
| 16 | **0% green**：API、Web candidate 帶 tag 部署。既有 service 用 `--no-traffic`；首次 release 以 private 建立（`--no-allow-unauthenticated`）。production 的 ingress 是 `internal-and-cloud-load-balancing` | 同上 | 無 | `deploy_cloud_run_waji.sh:1120-1166`、`:1292-1340`、`:271` |
| 17 | **0% 讀回（candidate smoke）**：對 tagged candidate 跑 release-aware smoke，比對 `--expected-sha` | 同上 | 無 | `deploy_cloud_run_waji.sh:1379-1395` |
| 18 | **切換**：兩個 scheduler trigger 先指向 candidate job，但以 **PAUSED** 狀態寫入；再把 API、Web 依序切到 100%。首次 release 時，Web 在這一步才開放 `allUsers`；接著套用 service 層的 min instances | 同上 | 無（沒有中途人工確認點） | `deploy_cloud_run_waji.sh:1397-1425`；`product_ops/deployment/cloud_scheduler_trigger.py:371` |
| 19 | **Live E2E gate**：對 production custom domain 執行 `check_live_e2e_gate.py --release-profile core-ops`，core-ops 不帶 `--require-full-acceptance`。通過後才恢復 trigger（新 trigger 預設保持 PAUSED），並設 `DEPLOYMENT_COMMITTED=true`；失敗時由 EXIT trap 回復 | 同上 | 無 | `deploy_cloud_run_waji.sh:1427-1533` |
| 20 | **Watch window**：30 分鐘，人工監看 | **負責人**（on-call，決定 C）；Claude 在上線後 24 小時內每小時讀回 | 見 `monitoring-oncall-plan.md` | `docs/evidence/runtime/ODP-PROD-OPS-WATCH-PARAMS-001/README.md:22-25` |
| 21 | **Staging closeout**：只在 `needs.deploy.result == 'success'` 時執行，綁定 `staging` environment。先讀 staging recovery bundle，確認 staging 的 manifest digest 等於 production；再讀 watch receipt：不存在（404）就 defer，存在就驗證，通過才清理 staging | `staging_closeout` job | 以現況會 defer（§5 第 1 點）。watch receipt 寫好後**重跑這個 job**（HD-28） | `deploy-dev.yml:2455-2750` |

## 3. 每一步由誰觸發、誰簽署

| 動作 | 誰 | 依據 |
|---|---|---|
| dispatch staging／production build 與 deploy | 有 workflow dispatch 權限者。repo 沒有指定具名人員 | `deploy-dev.yml:54` |
| 核准 GitHub `staging`／`production` environment | required reviewers：`Alien-alfaloop`、`ajoe734`（`L-4.5`） | `docs/deployment/GCP_DEPLOY_GUIDE.md:14-15` |
| 簽發 lease | Supervisor，唯一持有私鑰者 | `.orchestrator/release_lease.py:1-21` |
| gate-3 passed-with-deviation | Product Owner + ML Risk Owner。負責人已在 2026-11-20 決定方向（決定 G），**簽署**仍在 candidate 選定之後 | design `:226`、`:231-269` |
| gate-5 | gate owner Codex2，reviewer Human/Ops | `RELEASE_GATE_REGISTRY.json:700-701` |
| gate-6 | gate owner Claude，reviewer Human/Ops | `RELEASE_GATE_REGISTRY.json:749-750` |
| release decision `go` 與 `human_signoff` | Human/Ops（registry 的 `decision_owner`） | `RELEASE_GATE_REGISTRY.json:22-31` |
| production GO、正式回滾決定、incident response | 人類負責人 | `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:451` |
| watch window 監看與回滾判定 | 負責人（on-call，決定 C） | `live-readback-20261127.md` §2 C |

> 具名核准只能由負責人本人做出，不可代簽。本文件沒有預填任何姓名或簽署。

## 4. production 環境前提

| 前提 | 現況 | 狀態 |
|---|---|---|
| `ODP_CLOUD_RUN_VPC_EGRESS=all-traffic` | `production` 已設定（`L-4.1`）。`production-build` 是否需要、是否已設，`L-4` **沒有讀回** | production：已設 |
| production VPC `oday-prod-runtime` | 已存在，2026-11-25 建立（`L-2.1`） | 已存在 |
| `ODP_PROD_DEPLOY_URL`、`ODP_PROD_API_URL` 是 HTTPS custom domain | 已設定為 `console.oday-plus.com.tw`、`api.oday-plus.com.tw`（`L-4.1`）；LB 與受管憑證 `ACTIVE`（`L-2.6`、`L-2.7`）。腳本的要求見 `deploy_cloud_run_waji.sh:147-160` | 已設 |
| min instances | `ODP_CLOUD_RUN_MIN_INSTANCES=2`（`L-4.1`）；腳本要求 production ≥ 2（`deploy_cloud_run_waji.sh:61-68`） | 已設 |
| API／Web service 名稱 | `oday-prod-api`、`oday-prod-web`（`L-4.1`）；由部署流程建立（決定 H；`L-2.8`） | 已設 |
| smoke 身分 `ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT` 與 principal map 對應到 `["operations_manager","model_owner","data_owner"]` | 變數已設（`L-4.1`）。principal map secret 的內容**未讀回** | 部分 |
| `ODP_AUTH_AUDIENCES` | 已設（`L-4.1`） | 已設 |
| `ODP_INTAKE_CURSOR_SIGNING_KEY_SECRET` 必須是 `secret-name:數字版本` | 腳本要求（`deploy_cloud_run_waji.sh:161-164`）。`L-4` **沒有讀回** | 未讀回 |
| `ODP_FORECAST_ENGINE`、`ODP_FORECAST_MODEL` 與 MLflow 變數 | 只要求「存在」（design `:205-216`）。production 已有 `oday-prod-mlflow`（`L-2.8`）。變數**未讀回** | 未讀回 |
| lease public key 與 production lease store URI | 已設（`L-4.1`） | 已設 |
| `GCP_CLOUD_SQL_INSTANCE` 指向 `oday-prod-foundation-sql` | migration 前備份要求格式 `<project>:<region>:<instance>`（`deploy-dev.yml:2095-2104`）。`L-4` **沒有讀回**這個變數 | 未讀回 |
| production 的 AR repository `oday-plus` 與 promotion 的 IAM | design `:469-477` 列出三項前置。**未讀回** | 未讀回 |
| production Cloud SQL | 新的 `oday-prod-foundation-sql`：REGIONAL、無 public IPv4、CMEK（`L-2.3`）。舊 `oday-prod-sql` 不動（`L-2.4`、決定 H） | 已存在 |
| migration 前的 backup checkpoint | workflow 已內建（步驟 12） | 已由程式解決 |
| production DR 演練 | 決定 E：GO 前先做，見 `backup-dr-impact.md` §6 | 待執行 |
| `ODP_PRODUCTION_WATCH_CLOSEOUT_URI` | **未設**（`L-4.3`） | 缺 |
| `ODP_PROD_API_INVOKER_GROUP` | 未設（`L-4.4`）。選填，但影響值班人員能否直接讀 API（`runbook-core-ops.md` §2.3） | 選填；未設 |

## 5. 已知的程式與流程缺口（本 task 只記錄，不修改）

1. **watch receipt 無法以真實資料產生（pending）。**
   - `staging_closeout` 驗 receipt 時呼叫 `verify_watch_window_receipt`（`deploy-dev.yml:2706`）。它要求 provider 簽章與 `MONITORING_PROVIDER_SECRET`（`shared/observability/watch_window.py:357-400`、`:825-844`），這個簽章不存在（`L-6.1`）。
   - repo 內沒有任何非測試程式呼叫 `record_deployment_watch_window_status`（只有 `tests/reliability/test_runtime_observability.py`）。
   - 影響：receipt 不存在時，`staging_closeout` 會 defer（`deploy-dev.yml:2693-2701`），staging 保留，job 是綠的。若有人上傳一份 receipt，它會被驗證而且一定失敗，job 變紅。
   - 修正：`ODP-PROD-WATCH-RECEIPT-REAL-METRICS-261127`（**pending**，`L-6.2`）。它若改到 evidence 以外的路徑，就要在凍結前合併，否則會讓 candidate 失效。這是**決定 A 的時序前提**，見 HD-05。
2. **`ODP_PRODUCTION_WATCH_CLOSEOUT_URI` 未設**（`L-4.3`）。`staging_closeout` 綁定 staging environment，在 `Prepare release-scoped staging closeout paths` 就要求這個變數（`deploy-dev.yml:2647`）。沒有設定時 job 會失敗，staging 不會清理。
3. **watch 期間沒有自動回滾。** `DEPLOYMENT_COMMITTED=true` 之後，EXIT trap 不再回復（`deploy_cloud_run_waji.sh:498-507`、`:1533`）。PROD-OPS-05 的門檻只是人工判斷依據（`ODP-PROD-OPS-WATCH-PARAMS-001/README.md:31-36`）。
4. **首次 release commit 後的下線**：依決定 D 採選項 (a)，程序見 `rollback-plan.md` §2.4。**不要**對首次 release 執行 `bluegreen_release.py rollback`：切換前狀態把兩個 service 記為 absent，rollback 會呼叫 `restore_service_traffic`，結果是**刪除** API 與 Web service（`bluegreen_release.py:718-744`、`:469-475`；`product_ops/deployment/cloud_run_release_traffic.sh:320-360`）。那是選項 (b)，不是決定 D。
5. **前滾 release 會在切換之前就重新開放 Web。** Web service 已存在時，部署腳本以 `--allow-unauthenticated` 部署 candidate（`deploy_cloud_run_waji.sh:1302-1306`、`:1323`）。撤銷 `allUsers` 之後的下一個 release，會在 Web candidate 部署這一步就把 `allUsers` 加回來，這時流量仍在舊（有問題的）revision 上，直到步驟 18 切換。若這個前滾 release 在 commit 前失敗，EXIT trap 只回復流量，不回復 IAM，Web 會維持公開、跑在舊 revision 上。處置見 `rollback-plan.md` §2.4.4。

## 6. 規劃文件與程式不一致（以程式為準）

| 文件說法 | 程式實際 |
|---|---|
| `docs/deployment/ENVIRONMENTS.md:38` 寫 staging 演練是「9-stage」 | 現在是 11 個 stage，多了 `web_custom_domain` 與 `live_e2e_gate`（`staging_lifecycle.py:2868-2886`） |
| `.orchestrator/release_lease.py:23-26`：「There is no production blue-green path yet」 | production 路徑已存在（本文件 §2）。lease issuer 仍以 `staging-verified` stage 擋 production，這是正確的 gate 行為，只是註解過時 |
| `EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` §8.2：先部署 data platform green | Runtime Release 的 production 路徑不部署 data platform。data platform 屬於另一個 repo 與流程，本包**未驗證**其狀態 |

2026-10-24 版列的另外兩項不一致已經消失：`GCP_DEPLOY_GUIDE.md:226` 現在寫明切換由 `deploy_cloud_run_waji.sh` 經 `cloud_run_release_traffic.sh` 執行；規劃 §8.3 的「先 PAUSED、live gate 後再恢復」與程式一致（`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:279-298`；`deploy_cloud_run_waji.sh:1397-1408`、`:1520-1532`）。
