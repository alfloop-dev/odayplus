# core-ops production 部署計畫（DRAFT · 未簽署）

本文件依照 `.github/workflows/deploy-dev.yml`（workflow 名稱 `Runtime Release`）與 `product_ops/deployment/deploy_cloud_run_waji.sh` 的**實際程式**描述 production rollout。凡是規劃文件與程式不一致之處，都在 §6 列出，並以程式為準。

行號以 `origin/dev` @ `5d761526` 為準。

## 0. 先決條件：現在還不能部署 production

production admission 需要同時滿足兩件事（`delivery_toolchain/release/check_runtime_admission.py:1-23`）：

1. **一份未消耗的 Supervisor lease。** lease 必須已簽署、未過期，並綁定 task、candidate SHA、manifest digest、環境與 action。
2. **registry 准許 production。** registry 要同時滿足：
   - `release.decision == "go"`（同檔 `:149-150`）。
   - `release.stage == "staging-verified"`，而且 `admission_target == "production"`（`:171-186`；stage 對照表在 `delivery_toolchain/e2e/check_release_gate_registry.py:64-71`）。
   - 每個 `admission_target == "production"` 的 gate 狀態都屬於 `passed`、`passed-with-deviation` 或 `not-applicable`（`check_release_gate_registry.py:55`）。
   - 除 `not-applicable` 外，每個這類 gate 都有一筆 `result: pass`、且綁定 candidate SHA 的 receipt（`check_runtime_admission.py:206-260`）。

目前綁定 production 的 gate 是 gate-3、gate-5、gate-6（`RELEASE_GATE_REGISTRY.json:298`、`:405`、`:454`）。三者都是 `blocked`，也都沒有 receipt。

registry 目前停在 dev candidate `6140d0ef…`，`stage: candidate-built`，`admission_target: dev`（`:15-22`）。lease issuer 也明確寫著：還沒有 release 到達 `staging-verified`，所以 production 會被拒絕（`.orchestrator/release_lease.py:23-26`）。

**結論：在完成 `human-decisions-checklist.md` 的對應決策並把 receipt 寫入 registry 之前，以下步驟到 Step 4 就會被 admission 拒絕。**

**另一個既有 blocker：即使 admission 通過，以 `origin/dev` 現行 workflow，production deploy job 會在流量已切到 100% 之後的步驟 17 失敗，步驟 19 的 staging closeout 會被 skip（§5 第 1 點）。** 修正屬於另一個 task。

## 1. core-ops 的晉升路徑

- core-ops 只能以 dev 或 staging 為建置目標（`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:58`）。
- production 只接受 **staging 封存**的同一份 manifest，原樣晉升（同檔 `:59`、`:71`）。
- workflow 的 `release_profile` 說明也寫明：build 只能針對 dev 或 staging，production 只 admit staging 封存的 core-ops manifest（`.github/workflows/deploy-dev.yml:150-155`）。
- 以 production 為建置目標，在 build 的 `release_phase` 與 `build_release_handoff` 就會被拒絕（design `:66`）。

因此 production 部署一定是：**staging build → staging deploy 與演練 → production deploy（重用 staging build 的 manifest 與四個 image digest）**。

## 2. 步驟

| # | 步驟 | 實際執行者 | 人工動作 | 依據（file:line） |
|---|---|---|---|---|
| 1 | **Staging build**：`phase=build`、`environment=staging`、`release_profile=core-ops` | GitHub Actions 的 `build` job，綁定 `staging-build` environment，不需部署核准 | 有 workflow dispatch 權限的人觸發 dispatch，並填入 `release_sha`（40 字元）與 `task_id`。首次 staging release 的回滾綁定見 `rollback-plan.md` §2 | `deploy-dev.yml:53-160`、`:303-312`、`:35-42` |
| 1a | build 內的檢查：secret scan、SAST、production npm audit、SBOM、本機 E2E 備份／還原／回滾證明 | 同上 | 無 | `deploy-dev.yml:404-434` |
| 1b | 建置、推送、Cosign 簽章、SBOM attest 四個 image，並解析成 `@sha256` digest。四個 tag 全部已存在時重用，只有部分存在時拒絕 | 同上 | 無 | `deploy-dev.yml:496-608` |
| 1c | 寫出 build-once handoff 與 candidate manifest，封存 `release_profile` 與 rollback／initial-release 綁定，然後上傳 artifact `runtime-release-manifest-<sha>` | 同上 | 記錄 run ID、`manifest_digest` 與四個 image ref。這些是後續 lease 與 deploy 的輸入 | `deploy-dev.yml:611-715` |
| 2 | **Registry 推進到 staging**：`stage=dev-verified`、`admission_target=staging`、`decision=go`；gate-2 必須 cleared 並有 receipt | 以 evidence-only commit 修改 registry，由另一個 task 執行 | Human/Ops 對 gate-2 做處置（見 `human-decisions-checklist.md`），並簽 release decision | `check_runtime_admission.py:149-186`；`RELEASE_GATE_REGISTRY.json:254`（gate-2 綁 staging） |
| 3 | **Staging lease**：Supervisor 以 `.orchestrator/release_lease.py issue` 簽發 | Supervisor，唯一持有 Ed25519 私鑰者 | Human/Ops 提出或確認 lease 請求 | `.orchestrator/release_lease.py:1-21`；`check_runtime_admission.py:44-58` |
| 4 | **Staging deploy**：`phase=deploy`、`environment=staging`，帶入 `manifest_run_id`、`manifest_digest`、四個 image、`release_lease` | `admission` job 與 `deploy` job，都綁定 `staging` environment | GitHub `staging` environment 的 required reviewers 核准。依 `docs/deployment/GCP_DEPLOY_GUIDE.md:14` 是 `Alien-alfaloop`、`ajoe734`；目前設定**未驗證** | `deploy-dev.yml:730-1081`、`:1174-1180` |
| 4a | admission：讀回並驗證 event SHA 與 candidate 的 ancestry、image handoff，下載 manifest 並綁定 digest，檢查 environment 變數綁定，再讀一次 target（initial release），最後驗 lease 並以 CAS 消耗 | `admission` job | 無 | `deploy-dev.yml:757-1046` |
| 4b | 抽出 admitted profile，只接受 `full\|dev-admin\|core-ops` | 同上 | 確認 receipt 內是 `core-ops` | `deploy-dev.yml:1048-1067` |
| 4c | `staging_lifecycle.py create` 建立 release-scoped 資源；`verify` 跑 10 個 stage，最後一個是 `live_e2e_gate` | `deploy` job | 先確認 design §12.5 的 IAM 與網路前提（見 checklist） | `deploy-dev.yml:1491-1548`；`staging_lifecycle.py:2685-2698`；design `:231-238`、`:277-287` |
| 4d | 失敗時執行 `hold`，保留 24 小時供鑑識 | `deploy` job | 指定鑑識負責人；需要延長時記錄原因（上限 168 小時） | `deploy-dev.yml:1562-1591`；`staging_lifecycle.py:62` |
| 5 | **UAT 與 gate 證據收集**（staging 存活期間） | 人工 | 見 `uat-plan.md` 與 `gate-evidence-map.md` | `RELEASE_GATE_REGISTRY.json:439` |
| 6 | **Registry 推進到 production**：`stage=staging-verified`、`admission_target=production`、`decision=go`；gate-3、gate-5、gate-6 cleared 並有綁定 candidate SHA 的 receipt；寫入 `human_signoff` | 以 evidence-only commit（E）修改 registry，E 必須是 candidate C 的 evidence-only descendant | 負責人具名簽署 gate-3、gate-5、gate-6 與 release decision | `check_runtime_admission.py:149-260`；`deploy-dev.yml:744-756`；`RELEASE_GATE_REGISTRY.json:26-30`（human_signoff 欄位的既有格式） |
| 7 | **Production lease**：Supervisor 針對同一個 manifest digest 簽發 | Supervisor | Human/Ops 提出或確認 lease 請求 | design `:71`；`.orchestrator/release_lease.py:1-27` |
| 8 | **Production deploy dispatch**：`phase=deploy`、`environment=production`、`release_sha=C`；`manifest_run_id` 用**步驟 1 的 staging build run**；`manifest_digest` 與四個 image 都用步驟 1c 的值；附 `release_lease` | 從 ref E dispatch | 有 dispatch 權限的人觸發。GitHub `production` environment 的 required reviewers 核准。`admission` 與 `deploy` 兩個 job 都綁定 `production`；實際要核准幾次**未驗證** | `deploy-dev.yml:735-736`、`:1179-1180`；`GCP_DEPLOY_GUIDE.md:15` |
| 9 | **環境變數綁定與 preflight**：檢查 deploy scope 的變數，production 必須是 Direct VPC 加 egress；接著跑 live runtime preflight | `deploy` job | 先確認變數已設定（見 §4） | `deploy-dev.yml:1318-1382` |
| 10 | **部署腳本開始**。依序檢查：profile（core-ops 只能進 dev、staging、production）、production 必須提供 HTTPS 的 `ODP_PROD_DEPLOY_URL` 與 `ODP_PROD_API_URL`、production 必須以 digest 部署 | `deploy_cloud_run_waji.sh` | 無 | `deploy_cloud_run_waji.sh:84-117`、`:133-140`、`:320-321` |
| 11 | **記錄既有流量並啟動自動回復**：捕捉 API／Web traffic 與兩個 scheduler trigger，`ROLLBACK_ARMED=true` | 同上 | 無 | `deploy_cloud_run_waji.sh:813-820` |
| 12 | **Migration 與相容性 gate**：部署並執行 migration job，再檢查新 schema 與舊 revision 的相容性。全新 target 則改跑 bootstrap-compatibility | 同上 | migration 一旦執行就**不會**被回復，見 `rollback-plan.md` §4 | `deploy_cloud_run_waji.sh:822-877` |
| 13 | **0% green**：API 與 Web candidate 帶 tag 部署。既有 service 用 `--no-traffic`；首次 release 會建立新 service，Cloud Run 會讓第一個 revision 承接 100%，所以改以 private 建立（`--no-allow-unauthenticated`）、不開放公開存取 | 同上 | 無 | `deploy_cloud_run_waji.sh:879-906`、`:1086-1147` |
| 14 | **0% 讀回（candidate smoke）**：對 tagged candidate revision 跑 release-aware smoke，比對 `--expected-sha` | 同上 | 無 | `deploy_cloud_run_waji.sh:1171-1182` |
| 15 | **切換**：先把兩個 scheduler trigger 指向 candidate job，再把 API、Web 依序切到 100%。首次 release 時，Web 在這一步才開放 `allUsers` | 同上 | 無（沒有中途人工確認點） | `deploy_cloud_run_waji.sh:1184-1198`；`cloud_run_release_traffic.sh:259-268` |
| 16 | **Live E2E gate**：對 production custom domain 執行 `check_live_e2e_gate.py --release-profile core-ops`。core-ops 不帶 `--require-full-acceptance`。通過才設 `DEPLOYMENT_COMMITTED=true`；失敗時走 EXIT trap 回復 | 同上 | 無 | `deploy_cloud_run_waji.sh:1211-1305`、`:1286-1303` |
| 17 | **Blue-green 狀態讀取（現行程式會失敗）**：workflow 呼叫 `bluegreen_release.py capture-state`，但把 `--project`、`--region` 放在子命令**之後**。CLI 把這兩個參數定義成子命令**之前**的必要全域參數，所以 argparse 會以 exit 2 結束，不會寫出任何 state 檔，這一步失敗，`deploy` job 結果是 failure。這時部署腳本已經在前一個 step 設定 `DEPLOYMENT_COMMITTED=true` 並正常結束，production 已經 100% 承接新版，**不會**自動回復 | `deploy` job | 見 §5 第 1 點。即使參數順序修正，讀到的也是**切換後**的狀態，存在 `RUNNER_TEMP`，**不會**上傳成 artifact（見 `rollback-plan.md` §3） | `deploy-dev.yml:1438`、`:1604-1619`、`:1638-1671`；`bluegreen_release.py:985-989`、`:1056`；`deploy_cloud_run_waji.sh:1305` |
| 18 | **Watch window**：人工監看 | **人工**；repo 內沒有任何 watch 自動化 | watch owner 與 rollback owner 必須在線，見 `monitoring-oncall-plan.md` | `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:265-266`、`:288-296` |
| 19 | **Staging closeout**：只在 `needs.deploy.result == 'success'` 時執行，綁定 `staging` environment。執行時先讀 `vars.ODP_PRODUCTION_WATCH_CLOSEOUT_URI` 指向的 watch receipt 並驗證，通過才清理 staging。**以現行程式，步驟 17 會讓 deploy job failure，所以這個 job 會被 skip**，根本不會讀 watch receipt，也不會清理 staging | `staging_closeout` job | staging 需由人工處理（`human-decisions-checklist.md` HD-28）。watch receipt 的產生者也不存在，見 §5 | `deploy-dev.yml:1678-1813`、`:1681`；`shared/observability/watch_window.py:694-734` |

## 3. 每一步由誰觸發、誰簽署

| 動作 | 誰（依 repo 文件；姓名留白） | 依據 |
|---|---|---|
| dispatch staging／production build 與 deploy | 有 workflow dispatch 權限者。repo 未指定具名人員 | `deploy-dev.yml:53-55` |
| 核准 GitHub `staging`／`production` environment | required reviewers。repo 記載為 `Alien-alfaloop`、`ajoe734`，目前設定**未驗證** | `GCP_DEPLOY_GUIDE.md:11-28` |
| 簽發 lease | Supervisor，唯一持有私鑰者 | `.orchestrator/release_lease.py:1-21` |
| gate-3 passed-with-deviation | Product Owner + ML Risk Owner | design `:147` |
| gate-5 | gate owner Codex2，reviewer Human/Ops | `RELEASE_GATE_REGISTRY.json:398-399` |
| gate-6 | gate owner Claude，reviewer Human/Ops | `RELEASE_GATE_REGISTRY.json:447-448` |
| release decision `go` 與 `human_signoff` | Human/Ops（registry 的 `decision_owner`） | `RELEASE_GATE_REGISTRY.json:23-30` |
| production GO、正式回滾決定、incident response | 人類負責人 | `EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:426-429` |

> 具名核准只能由負責人本人做出，不可代簽。本文件沒有預填任何姓名。

## 4. production 環境前提（最後一次 repo 證據；現況**未驗證**）

| 前提 | 現況證據 | 狀態 |
|---|---|---|
| `production` 與 `production-build` 都要設定 `ODP_CLOUD_RUN_VPC_EGRESS=all-traffic` | 2026-09-17 讀取時兩者都缺（`docs/evidence/runtime/ODP-PROD-RUNTIME-RELEASE-PATH-001/README.md:183-185`、`:197-201`） | 未驗證是否已補 |
| production VPC `oday-prod-runtime` 已經 `terraform apply` | 同上 README `:201`、`:271` 記載「尚未 apply」 | 未驗證 |
| `ODP_PROD_DEPLOY_URL`、`ODP_PROD_API_URL` 是 HTTPS custom domain | 腳本強制要求（`deploy_cloud_run_waji.sh:133-140`）。repo 內沒有 domain 綁定設定的證據 | 未驗證 |
| smoke 身分 `ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT` 與 principal map secret 對應到 `["operations_manager","model_owner","data_owner"]` | `deploy-dev.yml:1225-1230` | 未驗證 |
| `ODP_FORECAST_ENGINE`、`ODP_FORECAST_MODEL` 必須是允許的組合；MLflow 相關變數只需「存在」 | `deploy_cloud_run_waji.sh:242-252`；design `:135-139` | 未驗證 |
| lease public key 與 production lease store URI | `deploy-dev.yml:887-921`；PR #1565 design §5.4 記載 production store 的 URI（待合併） | 未驗證 |
| production runtime 可拉取 staging build 推送的 image | repo 內沒有證據 | **未驗證，可能是阻擋項** |
| production Cloud SQL 的等級、HA、deletion protection、備份 | 2026-09-16 讀回結果是 `db-f1-micro`、`ZONAL`、`deletionProtectionEnabled=false`（`ODP-PROD-NETWORK-PARITY-PLAN-001/README.md:226-237`）。詳見 `backup-dr-impact.md` | 與 IaC 不符 |
| production Terraform root 的模型 precondition（F2） | 沒有真實模型 artifact 時 plan／apply 一定失敗（design `:128-134`）。Runtime Release 不執行 root stack（`:135`） | 需人工決定 |
| production backup checkpoint 完成 | 規劃要求（`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:265`）。workflow 沒有這一步 | 缺；需人工執行 |

## 5. 已知的程式與流程缺口（本 task 只記錄，不修改）

程式修正屬於另一個 task。本 task 只動本資料夾，不改 workflow 或程式。

1. **既有 blocker：production 的 `capture-state` 參數順序錯誤，deploy job 在 commit 之後必定 failure。**
   - workflow 的寫法是 `bluegreen_release.py capture-state --release-id … --api-service … --web-service … --project … --region … --output-dir …`（`deploy-dev.yml:1609-1615`）。
   - CLI 把 `--dry-run`、`--project`、`--region` 定義在主 parser，`--project`、`--region` 是 `required=True`，子命令在其後（`bluegreen_release.py:985-989`），`main` 以 `parser.parse_args` 解析（`:1056`）。放在子命令之後的 `--project`、`--region` 不會被主 parser 收到。
   - 本 task 以 `origin/dev` 的 `_build_parser()` 離線解析上述參數順序，結果是 `the following arguments are required: --project, --region`、`SystemExit 2`。改成 `--project … --region … capture-state …` 就能解析。這只是 argparse 層的離線檢查，沒有呼叫 GCP。
   - 實際失敗路徑：
     - 部署腳本（`deploy-dev.yml:1438` 的 step）已經切換 100% 流量、通過 live E2E gate，並設定 `DEPLOYMENT_COMMITTED=true`（`deploy_cloud_run_waji.sh:1305`），正常結束。
     - 下一個 production step 的 `capture-state` 以 exit 2 失敗，沒有寫出 `api-traffic.json`、`web-traffic.json`、`release-state.json`，後面的 `test -s` 與斷言都不會執行。
     - `deploy` job 結果是 failure。`Upload deployment validation reports` 是 `if: always()`，仍會上傳 validation 報告（`:1638-1671`）。
     - `staging_closeout` 的條件是 `needs.deploy.result == 'success'`（`:1681`），所以被 **skip**。不會讀 watch receipt，也不會清理 staging。
   - 影響：production 實際已經在新版上運作，但 workflow run 是紅的。部署腳本的 EXIT trap 已經結束，不會因為這一步失敗而回復。staging 不會被這次 run 清理。
   - 處置：production GO 前要由另一個 task 修正 workflow 並重新產生 candidate，或者具名接受這個失敗路徑與人工收尾程序（`human-decisions-checklist.md` HD-21 第 3 點、HD-28）。注意：修改 workflow 不是 evidence-only 變更，會讓已建置的 candidate 失效（`deploy-dev.yml:744-756`）。
2. **watch receipt 沒有任何產生者。**
   - `staging_closeout` 執行時，要求 `ODP_PRODUCTION_WATCH_CLOSEOUT_URI` 已有綁定本 SHA、狀態 `WATCH_PASSED`、至少 15 分鐘的 receipt（`deploy-dev.yml:1793-1798`；`watch_window.py:710-734`）。
   - repo 內唯一呼叫 `record_deployment_watch_window_status` 的地方，是 fail-closed 的負向測試（`product_ops/deployment/validate_cloud_run_live_deployment.py:617-634`）。
   - 推論：以現行程式，`staging_closeout` 會先因第 1 點被 skip。第 1 點修正之後，`staging_closeout` 會在 deploy 成功後立即執行，這時幾乎一定會因為找不到 receipt 而失敗，staging 仍不會被清理。這個 job 綁定 staging 身分、不碰 production。之後如何補跑屬於人工決定，**未驗證**。
3. **watch 期間沒有自動回滾。** 部署腳本一旦設定 `DEPLOYMENT_COMMITTED=true`，就不再自動回復（`deploy_cloud_run_waji.sh:368-376`、`:1305`）。PROD-OPS-05 的門檻只是人工判斷依據（`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:296`）。
4. **切換時沒有暫停排程。** scheduler trigger 在切換**前**就指向 candidate job，沒有「暫停、one-shot、再恢復」的程序（`deploy_cloud_run_waji.sh:1184-1191`）。規劃 §8.3 寫的是先 one-shot 再恢復（`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:283-288`）。
5. **production 沒有 deploy 前的備份步驟。** workflow 的 production 路徑沒有 SQL 備份、export 或 PITR 讀回。

## 6. 規劃文件與程式不一致（以程式為準）

| 文件說法 | 程式實際 |
|---|---|
| `GCP_DEPLOY_GUIDE.md:226`：production 用 `bluegreen_release.py` 執行 blue→green 100% 切換 | 切換是 `deploy_cloud_run_waji.sh` 呼叫 `cloud_run_release_traffic.sh` 的 `promote_service_traffic`（`:1193`、`:1198`）。`bluegreen_release.py` 在 workflow 中只用到 `capture-state`（`deploy-dev.yml:1609`），而且以現行參數順序會失敗（§5 第 1 點） |
| `ENVIRONMENTS.md:38` 寫 staging 演練是「9-stage」 | 現在是 10 個 stage，多了 `live_e2e_gate`（`staging_lifecycle.py:2685-2698`） |
| 規劃 §8.3 第 4、5 步：worker／scheduler 先 one-shot 驗證，再恢復 trigger | scheduler trigger 在流量切換前直接 upsert 到 candidate job（`deploy_cloud_run_waji.sh:1184-1191`） |
| 規劃 §8.2：先部署 data platform green | Runtime Release 的 production 路徑不部署 data platform。data platform 屬於另一個 repo 與流程（`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:450`），本包**未驗證**其狀態 |
