# ODP-PROD-RUNTIME-RELEASE-PATH-001：production 的 Runtime Release 部署路徑（證據）

- Task：建立 production 的 Runtime Release 部署路徑
- Owner / Reviewer：Claude2 / Codex
- 本輪狀態：**兩道 gate 已改為支援 Direct VPC egress，production 路徑在程式層面可執行；唯一剩下的前提是 `ODP_CLOUD_RUN_VPC_EGRESS` 這一個模式宣告變數（見 §5 AC-4 與 §8）。**
- 本輪未執行任何 production 部署、未對任何 GCP 或 GitHub 資源做寫入；所有實測皆為本機唯讀（`gh api` 讀取、stub 掉外部工具的 bash 執行、pytest）。

## 0. 改向摘要：本輪相對於前兩輪交付了什麼

前兩輪（head `d12e4614`、`ba5d5e80`）的 README 結論是「路徑結構正確、只缺 Human/Ops 佈建
`ODP_CLOUD_RUN_VPC_CONNECTOR` / `ODP_CLOUD_RUN_VPC_EGRESS`」。這個結論已於
2026-09-17T00:55:13Z 被 Human/Ops note 推翻，本輪照裁示改向；下列前版陳述在此**明確撤回**：

| 前版陳述 | 為何是錯的 |
|---|---|
| 「No workflow code change is required」「not a workflow code defect」 | `infra/terraform/cloud_run.tf:17-22, 183-188` 對 production 宣告的是 Direct VPC egress（`vpc_access.network_interfaces`），全樹沒有任何 `vpc_access_connector`。兩道 gate 硬性要求 connector 名，是 gate 與 IaC 脫節，屬程式缺陷。 |
| 「請 Human/Ops 佈建 `ODP_CLOUD_RUN_VPC_CONNECTOR`」 | 那是要求佈建一個 IaC 不產生、也不該存在的資源。若把網路名填進去，`deploy_cloud_run_waji.sh` 會送 `--vpc-connector=oday-prod-runtime` 給 Cloud Run API 而被拒（ODP-PROD-NETWORK-PARITY-PLAN-001 README §4.1 第 3 點）。 |
| 「production 目前有 42 個變數」 | 2026-09-17 以 `gh api`（`per_page=30` 分頁三頁）實測：`production` 47、`production-build` 9、`dev` 41、`dev-build` 12、`staging` 50。 |

本輪交付（three-dot diff `origin/dev...HEAD`）：

| 檔案 | 改動 |
|---|---|
| `delivery_toolchain/release/check_release_environment.py` | Gate 1：build scope 移除 connector；deploy scope 的網路綁定改為 connector 或 Direct VPC 二擇一（§2） |
| `product_ops/deployment/deploy_cloud_run_waji.sh` | Gate 2：sources-off guard 同步二擇一；`CLOUD_RUN_NETWORK_ARGS` 依模式組裝 `--vpc-connector` 或 `--network/--subnet`（§3） |
| `.github/workflows/deploy-dev.yml` | build 側移除 connector 接線；deploy job 與 deploy binding gate 接上 `ODP_PROD_VPC_NETWORK` / `ODP_PROD_VPC_SUBNETWORK`（§4） |
| `tests/release/test_release_environment_precheck.py` | Gate 1 雙軌單元測試 + 收據測試（§7） |
| `tests/ops/test_deploy_workflow_contract.py` | Gate 2 靜態 contract、workflow 接線、binding gate 精確集合（§7） |
| `tests/ops/test_cloud_run_live_deployment.py` | Gate 2 與 production URL guard 的 bash 實測（§7；AC-2 P2） |
| 本 README | 全文重寫為新事實 |

`tests/security/test_login_throttle_wiring.py` 對 script 的斷言（`WEB_SECRET_BINDINGS`、Web deploy 區塊的
`--add-cloudsql-instances`）不涉及本次改動的區段，實跑確認仍綠（§7），未修改。

## 1. 命名裁定與設計決定

### 1.1 Direct VPC 變數沿用既有的 `ODP_PROD_VPC_NETWORK` / `ODP_PROD_VPC_SUBNETWORK`

前任 owner 與 reviewer 都要求 owner 在 §4.2 建議的 `ODP_CLOUD_RUN_NETWORK` 與環境既有的
`ODP_PROD_VPC_NETWORK` 之間擇一，並在 gate、script、GitHub 變數三處一致。本輪選擇**既有名稱**：

- `gh api` 實測 `production` 已有 `ODP_PROD_VPC_NETWORK=oday-prod-runtime`、`ODP_PROD_VPC_SUBNETWORK=oday-prod-runtime`
  （皆於 2026-09-17T01:50Z 設定），值即 `infra/terraform/network.tf:1-10` 的 `${local.name_prefix}-runtime`。
  選它就**不需要任何新的變數佈建**，符合操作者「不要再等變數佈建」的裁示。
- 與 staging foundation 的 `ODP_STAGING_VPC_NETWORK` / `ODP_STAGING_VPC_SUBNETWORK` 同一命名慣例。
- 若改用 `ODP_CLOUD_RUN_NETWORK`，環境上會出現兩套同義變數並存，正是前任 owner 提醒要避免的。

三處一致的證據：`check_release_environment.py:139-144`（gate）、`deploy_cloud_run_waji.sh:69-92, 134-144`（script）、
`deploy-dev.yml:1026-1027, 1142-1143`（workflow → `vars.*` 同名）；
`tests/ops/test_deploy_workflow_contract.py:1294` 斷言 script 讀的正是 gate 宣告的那組名字，
`:1327` 斷言 workflow 以 `${{ vars.<同名> }}` 接線。

### 1.2 `ODP_CLOUD_RUN_VPC_EGRESS` 兩種模式都保留，build scope 也保留

§4.2 字面上建議 build scope「移除 VPC Connector / Egress 檢查」。本輪只移除 connector，**保留 egress**，原因是實測程式：

- `delivery_toolchain/release/build_release_handoff.py:191-200` 從 build job 的 `ODP_CLOUD_RUN_VPC_EGRESS` 取
  `resolved_cloud_run_egress`，缺值時為 `unresolved`，sources-off handoff 直接 fail closed
  （`tests/release/test_build_release_handoff.py::test_sources_off_build_refuses_unresolved_runtime_egress`）。
  若 build gate 不再檢查 egress，`production-build` 缺值的失敗只會從「gate 的中文收據」後移到「handoff 的 HandoffError」，訊息更難讀。
- Direct VPC 只改變「怎麼接進 VPC」，不改變 sources-off 對 egress 的要求：`cloud_run.tf` 兩個 service 都是
  `egress = "ALL_TRAFFIC"`，`--network/--subnet` 仍要搭配 `--vpc-egress=all-traffic`。把它硬編進 script 會讓
  build 端的 attestation 失去「記錄 GitHub 實際注入值」的依據，等於用推論取代觀測。

### 1.3 兩種模式互斥

connector 與 network 同時有值時 gate 1 與 gate 2 都拒絕（Cloud Run 的 `--vpc-connector` 與 `--network` 互斥），
半套（只有 network 或只有 subnetwork）也拒絕；兩者都在第一次 Cloud Run mutation 之前，訊息點名缺哪個變數。

### 1.4 `release_manifest.py` / `build_release_handoff.py` 的靜態 contract 檢查刻意不動

`release_manifest.py:2028-2033` 與 `build_release_handoff.py:178-183` 對 script 內容比對的是 connector token
（`"--vpc-connector=${ODP_CLOUD_RUN_VPC_CONNECTOR}"`、`"--vpc-egress=${ODP_CLOUD_RUN_VPC_EGRESS}"`）。
這些 token 在新 script 的 connector 分支裡仍逐字存在，所以檢查照常通過（實測見 §6）。
沒有把 Direct VPC token 加成硬性要求，是因為這組檢查會以 `git show <candidate>:<file>` 對**歷史 candidate**
（例如 committed manifest 的 `596b9c9a`）重新評估，加 AND 條件會讓所有既有 candidate 回溯性失敗。
Direct VPC 接線由 `tests/ops/test_deploy_workflow_contract.py:1294` 對當前樹斷言。

## 2. Gate 1：`delivery_toolchain/release/check_release_environment.py`

| 位置 | 之前（origin/dev） | 之後（HEAD） |
|---|---|---|
| `REQUIRED_VARIABLES["build"]`（:75-96） | 含 `ODP_CLOUD_RUN_VPC_CONNECTOR`、`ODP_CLOUD_RUN_VPC_EGRESS` | 移除 connector；保留 egress（理由見 §1.2，註解寫在程式裡） |
| `REQUIRED_VARIABLES["deploy"]`（:97-111） | 含 connector、egress | 移除 connector；保留 egress |
| `VPC_BINDING_MODES`（:139-144，新增） | — | `connector: (ODP_CLOUD_RUN_VPC_CONNECTOR,)`；`direct_vpc: (ODP_PROD_VPC_NETWORK, ODP_PROD_VPC_SUBNETWORK)` |
| `VPC_BINDING_SCOPES`（:146） | — | `("deploy",)`：build 不部署、admission 只驗 lease、staging 網路由 Terraform output 決定 |
| `declared_variables()`（:164） | — | required + 二擇一的全部名字；workflow gate step 的 `env:` 必須與之精確相等 |
| `vpc_binding_errors()`（:204） | — | 半套 → 點名缺的那一半；兩套 → 「互斥」；零套 → 列出兩個選項；`binding_errors` 於 :283 併入 |
| 收據 `vpc_binding`（:378） | — | `{"mode": "connector"|"direct_vpc"|null, "modes": {…present/absent…}}`，不記任何值 |
| `main()`（:408） | 只讀 required | 讀 `declared_variables(scope)` |

## 3. Gate 2：`product_ops/deployment/deploy_cloud_run_waji.sh`

| 行 | 內容 | production（Direct VPC）走向 |
|---|---|---|
| 43-52（未改） | `ODP_DEPLOY_ENV=production` 時 `:?` 要求 `ODP_PROD_DEPLOY_URL` / `ODP_PROD_API_URL` 並驗 HTTPS | 缺值或非 HTTPS → exit 1 |
| 69-72 | connector 與 `ODP_PROD_VPC_*` 同時有值 → 「mutually exclusive」exit 1 | production 無 connector，通過 |
| 73-80 | network / subnetwork 只有一半 → 點名缺的那個 exit 1 | 兩者皆有，通過 |
| 81-85 | 決定 `CLOUD_RUN_VPC_MODE`：`connector` 或 `direct-vpc` | `direct-vpc` |
| 86-93 | 有模式沒 egress、或有 egress 沒模式 → exit 1 | 需 `ODP_CLOUD_RUN_VPC_EGRESS` |
| 94-104（未改） | egress 值只收 `all|all-traffic|private-ranges-only` | `all-traffic` |
| 113-131 | sources-off guard：**任一模式**都可滿足網路半邊（:114-116）；`MANIFEST_DIGEST` 與 ALL_TRAFFIC 要求不變（:118-130） | 通過 |
| 133-145 | `case "${CLOUD_RUN_VPC_MODE}"`：connector → `--vpc-connector` + `--vpc-egress`；direct-vpc → `--network` + `--subnet` + `--vpc-egress`；並印出模式 | `--network=oday-prod-runtime --subnet=oday-prod-runtime --vpc-egress=all-traffic` |
| 457 | 第一個 `gcloud run` 呼叫 | 上述所有 guard 都在它之前 |

原本 :82 的 `: "${ODP_CLOUD_RUN_VPC_CONNECTOR:?…}"` 已移除；`"${CLOUD_RUN_NETWORK_ARGS[@]}"` 仍套用於全部五個
Cloud Run target（`tests/ops/test_deploy_workflow_contract.py:1238` 未改仍綠）。

## 4. Workflow：`.github/workflows/deploy-dev.yml`

| 位置（HEAD 行號） | 改動 | 理由 |
|---|---|---|
| build job `env:`（:291-296） | 移除 `ODP_CLOUD_RUN_VPC_CONNECTOR`，保留 `ODP_CLOUD_RUN_VPC_EGRESS` 並加註解 | build 不開網路；egress 由 handoff 記錄 |
| build binding gate step `env:`（:319-329） | 移除 connector | 必須與 `declared_variables("build")` 精確相等 |
| handoff step `env:`（:568-569 附近） | 移除 connector | `build_release_handoff.py` 只讀 egress |
| deploy job `env:`（:1020-1028） | 新增 `ODP_PROD_VPC_NETWORK` / `ODP_PROD_VPC_SUBNETWORK`（`vars.*`），加註解說明兩種模式 | script 才讀得到 |
| deploy binding gate step `env:`（:1130-1143） | 同時暴露 egress、connector、`ODP_PROD_VPC_*` | 必須與 `declared_variables("deploy")` 精確相等（14 個） |

未改：environment choice（:61）、concurrency（:150-151）、deploy job `environment.name`（:995-996）、
`ODP_DEPLOY_ENV: ${{ inputs.environment }}`（:1071）、`ODP_EXTERNAL_PROVIDER_MODE: disabled`（:1080）、
`ODP_PROD_DEPLOY_URL` / `ODP_PROD_API_URL` 接線（:1088-1089）。

## 5. Acceptance 逐條

### AC-1：environment choice 含 production、concurrency group 獨立 — 滿足（本輪未改）

`deploy-dev.yml:61` `options: [dev, staging, production]`；`:150` `group: runtime-release-${{ inputs.environment }}-${{ inputs.phase }}`
→ production 的 group 是 `runtime-release-production-build` / `-deploy`，與 dev、staging 互不相同。
Contract test：`tests/ops/test_deploy_workflow_contract.py:1137 test_environment_inputs_support_dev_staging_production`。

### AC-2：production 解析到 `vars.ODP_PROD_DEPLOY_URL`，且有測試證明不再 fallback — 滿足

**揭露**：AC 指名的「第 94 行 url 二元三元式」已由 `c35ffe05`（ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001）
連同 `url:` 鍵一起移除；現在的 URL 解析在 deploy script 內：

- `deploy_cloud_run_waji.sh:1028-1034`：`if ODP_DEPLOY_ENV = production → ODP_PROD_API_URL / ODP_PROD_DEPLOY_URL；else → service_snapshot_url`。
  staging 不會走到這支 script（workflow :1210 `!= 'staging'`）。
- `:43-52`：production 缺 URL 或非 HTTPS 時在任何 `gcloud run` 之前 exit 1。
- workflow `:1071` 把 `inputs.environment` 原樣給 `ODP_DEPLOY_ENV`，`:1088-1089` 從綁定的 `production` environment 取 `vars.ODP_PROD_*`；
  script 全文不出現 `ODP_DEV_DEPLOY_URL` / `ODP_STAGING_DEPLOY_URL` / `ODP_STAGING_API_URL`，沒有可退回的 dev URL。

**測試證據（回應 round-2 P2，全部為本輪新增、實跑通過，數字見 §7）**：

| 測試 | 檔案:行 | 證明什麼 |
|---|---|---|
| `test_a_production_deploy_refuses_to_start_without_its_production_origin[ODP_PROD_DEPLOY_URL / ODP_PROD_API_URL]` | `tests/ops/test_cloud_run_live_deployment.py:366` | 以 bash 實跑 script、stub 掉 python3/uv/gcloud/docker：缺任一 production URL → exit 1，且 stub 從未被呼叫（沒有 `PREFLIGHT_REACHED`），即在任何工具與 Cloud Run mutation 之前擋下 |
| `test_a_production_deploy_refuses_a_non_https_production_origin[3 種]` | `:388` | `http://`、無 scheme、含空白 → exit 1「must be HTTPS custom domains」 |
| `test_a_production_deploy_with_https_origins_and_direct_vpc_enters_preflight` | `:402` | HTTPS URL + Direct VPC → 通過全部 guard 進入 preflight（exit 97 = stub） |
| `test_a_dev_deploy_does_not_require_the_production_origins` | `:416` | guard 只綁 production，不是全域要求 |
| `test_live_e2e_origins_come_from_the_production_variables_with_no_dev_fallback` | `:424` | 靜態：production 分支逐字存在且唯一；`:?` 與 HTTPS guard 的位置早於第一個 `gcloud run`；script 無任何其他環境的 URL 變數 |
| `test_deploy_job_passes_the_production_live_e2e_origins_from_the_bound_environment` | `tests/ops/test_deploy_workflow_contract.py:1346` | workflow：deploy job 綁 `inputs.environment`、`ODP_DEPLOY_ENV` 就是 input、`ODP_PROD_*` 來自 `vars.*`、`env:` 無 dev/staging URL |

### AC-3：逐一檢視 `inputs.environment == staging` 的條件 — 滿足（HEAD 行號）

| 行 | Step | 條件 | production | 理由 |
|---|---|---|---|---|
| 1128 | 確認 deploy 階段已綁定 environment 且變數齊備 | `!= 'staging'` | **執行** | `--scope deploy` 對 `production` 檢查 11 個必要變數 + 二擇一網路綁定；production 解析到 `direct_vpc`（§5 AC-4 收據） |
| 1163 | 確認 staging foundation 已綁定 environment 且變數齊備 | `== 'staging'` | 跳過 | staging Terraform foundation（KMS、state bucket…）專用 |
| 1187 | Run the live runtime preflight | `!= 'staging'` | **執行** | production 與 dev 同一支 preflight |
| 1210 | Deploy Cloud Run by immutable digest | `!= 'staging'` | **執行** | 進入 `deploy_cloud_run_waji.sh`，`ODP_DEPLOY_ENV=production` → Direct VPC 參數 + production URL 分支 |
| 1214 | Prepare release-scoped staging handoff paths | `== 'staging'` | 跳過 | ephemeral staging Terraform 路徑 |
| 1237 | Execute ephemeral staging lifecycle create | `== 'staging'` | 跳過 | production 不走 `staging_lifecycle.py` |
| 1263 | Persist staging recovery bundle… | `success() && == 'staging'` | 跳過 | staging 專用 |
| 1273 | Execute ephemeral staging rehearsal verification | `== 'staging'` | 跳過 | staging 專用 |
| 1294 | Verify release-scoped staging authority endpoint | `== 'staging'` | 跳過 | 讀 staging Terraform output |
| 1306 | Hold ephemeral staging resources on failure | `failure() && == 'staging'` | 跳過 | production 失敗回滾由 script 的 EXIT trap 處理 |
| 1325 | Persist staging hold state… | `always() && == 'staging'` | 跳過 | staging 專用 |
| 1337 | Upload staging lifecycle receipts | `always() && == 'staging'` | 跳過 | 只有 staging lifecycle 會產生 |
| 1348 | Verify production blue-green deployment state | `== 'production'` | **執行** | 只有 production 有 blue-green 流量管理 |
| 1407 | `staging_closeout` job | `always() && == 'production' && needs.deploy.result == 'success'` | **執行** | production 部署成功後清理 staging |

沒有任何條件是 `staging ? X : Y` 形式的預設值；每個 `!= 'staging'` 都明確代表「dev 與 production」，
production 專屬邏輯由 `ODP_DEPLOY_ENV` 在 script 內分支。

### AC-4：deploy job 綁定 production environment、`vars.*` 正確解析 — 滿足（附已綁定證據與剩餘前提）

- 綁定：`deploy-dev.yml:995-996` `environment: name: ${{ inputs.environment }}`；
  `tests/ops/test_deploy_workflow_contract.py:1655 test_each_phase_binds_to_its_own_authority_environment`、
  `:1630 test_every_job_that_reads_environment_variables_binds_an_environment`。
- gate step（:1127-1150）暴露的 `env:` 與 `declared_variables("deploy")` 精確相等（14 個），
  由 `:1674 test_the_binding_gate_exposes_exactly_the_variables_its_scope_requires` 把關；gate 在 WIF auth 之前（`:1712`）。
- **對現況實測（本機以 `check_release_environment.py` CLI、值一律 `x`、只驗有無）**：
  - production 現有變數形狀（有 `ODP_PROD_VPC_NETWORK` / `ODP_PROD_VPC_SUBNETWORK`、無 connector、無 egress）→ `--scope deploy` **拒絕，且只點名一個變數**：
    `deploy 階段在 GitHub environment production 取不到必要變數：ODP_CLOUD_RUN_VPC_EGRESS`。網路綁定已解析為 `direct_vpc`，不再要求 connector。
  - production-build 現有形狀（9 個）→ `--scope build` 拒絕，同樣只點名 `ODP_CLOUD_RUN_VPC_EGRESS`（connector 已不在 build scope）。
  - 若 production 再加上 `ODP_CLOUD_RUN_VPC_EGRESS=all-traffic` → `--scope deploy` **通過**，收據：

    ```json
    {"admitted": true, "missing_variables": [],
     "vpc_binding": {"mode": "direct_vpc",
                     "modes": {"connector": {"ODP_CLOUD_RUN_VPC_CONNECTOR": false},
                               "direct_vpc": {"ODP_PROD_VPC_NETWORK": true, "ODP_PROD_VPC_SUBNETWORK": true}}},
     "resolved_non_secret_values": {"ODP_CLOUD_RUN_VPC_EGRESS": "all-traffic"},
     "summary_zh_tw": "deploy 階段已綁定 GitHub environment `production`，11 個必要環境變數全部解析成功，Cloud Run VPC 網路綁定模式為 direct_vpc。"}
    ```

- **剩餘前提（非本任務可為、也不是 blocker）**：`production` 與 `production-build` 各需 `ODP_CLOUD_RUN_VPC_EGRESS=all-traffic`。
  這是 egress **模式宣告**（鏡射 `cloud_run.tf` 的 `egress = "ALL_TRAFFIC"`；dev / dev-build 的值即 `all-traffic`），
  不是任何雲端資源，也**不是** connector；只有 Human/Ops 能寫 GitHub environment 變數。它是
  ODP-PROD-BLUEGREEN-ROLLOUT-001 首次 dispatch 的前提，不影響本任務的交付物（路徑本身）。
  `production` 專案裡 `oday-prod-runtime` VPC 尚未 `terraform apply`（前任 owner note 2026-09-17T01:54:59Z），同屬 rollout 前提。

### AC-5：未執行 production 部署或觸發 production 資源異動 — 滿足

沒有任何 `workflow_dispatch`；沒有 `gcloud` / `terraform` 寫入；`gh api` 只做 GET。
bash 實測全部以 stub 取代 `python3 / uv / gcloud / docker`（stub 一被呼叫就 exit 97）。

### AC-6：未以 billing 或 PROD-OPS-05 為由放寬 — 滿足

未引用任一者放寬任何 AC；剩餘前提以事實記錄，未豁免。

## 6. Digest 凍結：實測結果而非假設

- `SOURCES_OFF_EGRESS_CONTRACT_FILES`（`release_manifest.py:244-251`，六個檔案）的 digest 由內容計算：
  `origin/dev 803f3aa9` = `sha256:e8411854dc1629f54bd17287fa1132662cbaef0dde6f0cc64b2ca1c93ddaa09c`；
  本輪 anchor `6d35892d` 起 = `sha256:a57c203aac4733a9a1b0925c6c5d733554f88b3a07dfe6760b0b908eb5ab8582`
  （之後的 commit 只動 tests 與本 README，不在清單內，digest 不再變）。
- 這個 repo **沒有**任何寫死的 HEAD digest 常數需要「重新凍結」：`tests/release/test_release_manifest.py:1417` 的
  `expected_digest = sha256:a9ab95a0…` 綁的是歷史 candidate `596b9c9a`，以 `git show` 讀該 commit 的檔案，不受工作樹影響；
  `docs/evidence/gates/RELEASE_MANIFEST.json` 也是同一個 candidate 的 manifest，validator 對它自己指名的樹評估
  （`release_manifest.py:1758-1791, 1961-1999` 的 `candidate_sha` 路徑）。
- 在 HEAD 上實測：`_sources_off_egress_contract_errors(candidate_sha=HEAD) == []`；
  `derive_sources_off_posture(candidate_sha=HEAD)` 的 `workflow_vpc_binding / deploy_entrypoint_vpc_binding / runtime_probe_wiring` 皆 `verified`。
- 「改了 script 會不會讓 release 測試轉紅」不靠推論，直接以 `tests/release/test_release_manifest.py`、
  `test_release_manifest_cli.py`、`test_build_release_handoff.py` 全檔實跑回答（§7）。

## 7. 驗證紀錄（2026-09-17，本 worktree，`uv run --frozen --python 3.12`，Python 3.12.14，pytest 9.1.1）

### 7.1 pytest（背景執行、以 JUnit XML 與 exit code 判定，不靠 grep passed）

| 批次 | 檔案 | 結果 |
|---|---|---|
| 焦點（直接涉及本次改動） | `tests/release/test_release_environment_precheck.py`（48）、`tests/ops/test_deploy_workflow_contract.py`（88）、`tests/ops/test_cloud_run_live_deployment.py`（409）、`tests/security/test_login_throttle_wiring.py`（9）、`tests/release/test_build_release_handoff.py`（99）、`tests/release/test_release_manifest.py`（87）、`tests/release/test_release_manifest_cli.py`（9）、`tests/ops/test_cloud_run_job_entrypoint.py`（18）、`tests/release/test_probe_release_target_absence.py`（19） | **786 passed, 0 failed, 0 skipped**，exit 0（308 s） |
| 其餘會讀 `deploy-dev.yml` / deploy script / gate 的檔案 | `tests/contract/test_api_trust_contract.py`、`tests/e2e/test_release_gate_registry.py`、`tests/e2e/test_remote_staging_proof_checker.py`、`tests/integration/test_external_fetch_enqueue_tenant_binding.py`、`tests/ops/test_conditional_oidc_deployment.py`、`tests/ops/test_ephemeral_staging_lifecycle.py`、`tests/ops/test_release_receipts.py`、`tests/ops/test_runtime_config_code_closeout.py`、`tests/ops/test_workflow_expression_contexts.py`、`tests/security/test_supply_chain_security_gate.py`、`tests/tooling/test_dependency_audit_boundary.py` | 467 tests：**464 passed, 3 failed, 22 skipped**，exit 1；3 筆失敗皆為本機環境假紅（下） |

3 筆失敗的成因（皆與本次改動無關，已逐筆查證）：

| 測試 | 成因 |
|---|---|
| `tests/e2e/test_release_gate_registry.py::test_product_gate_accepts_expected_sha`、`::test_dev_merge_gate_accepts_valid_registry_and_require_go_checks_packet` | product release gate 需要 Playwright：`Cannot find module '@playwright/test'`，worktree 沒有 `node_modules`。 |
| `tests/security/test_supply_chain_security_gate.py::test_sbom_and_provenance_present_and_valid` | 本機無 `node_modules` 時 `generate_sbom()` 產出的 335 個 npm component 缺 `supplier` 欄位，與 committed `docs/evidence/sbom.json` 不符（實測 component 0 唯一差異鍵為 `supplier`）。本次 diff 未觸及 `package-lock.json`、`uv.lock`、`docs/evidence/sbom.json`。 |

release 套件（`test_release_manifest.py` 87、`test_release_manifest_cli.py` 9、`test_build_release_handoff.py` 99）全綠，
即直接回答 §6：改動兩個 contract 檔案並未讓任何 release 測試轉紅，committed manifest 對其自身 candidate 仍驗證通過。

### 7.2 本次新增／改寫的測試（34 筆，含 parametrize 展開，全數通過）

- `tests/release/test_release_environment_precheck.py`（:213-322、:430-505）：build scope 不再要求網路綁定但仍要求 egress；
  deploy scope 宣告兩種模式但無條件要求皆無；Direct VPC production 部署核准；connector 仍核准；半套／兩套／零套拒絕並點名變數；
  Direct VPC 仍需 egress；收據記錄 `vpc_binding.mode` 且不含網路名；被拒收據 `mode: null`；build 收據無 `vpc_binding`。
- `tests/ops/test_deploy_workflow_contract.py`：`:1257` 新 guard 訊息與順序（guard 與組裝都在第一個 `gcloud run` 之前，舊的 `:?` connector 要求已不存在）；
  `:1294` 兩個分支組裝互斥的 flag、變數名與 gate 一致、無硬編網路名；`:1327` deploy job 接兩種模式、build job 不接任何綁定只接 egress；
  `:1346` production live E2E origin 接線；`:1674` binding gate `env:` 與 `declared_variables(scope)` 精確相等。
- `tests/ops/test_cloud_run_live_deployment.py`（:236-437）：以 bash 實跑 script 的 12 筆行為測試（gate 2 七筆 + production URL guard 五筆），見 §5 AC-2。

### 7.3 其他檢查

| 檢查 | 結果 |
|---|---|
| `uv run --frozen --python 3.12 --with ruff ruff check` 四個改動的 .py | All checks passed |
| `delivery_toolchain/governance/check_code_boundaries.py` | exit 0 |
| `bash -n product_ops/deployment/deploy_cloud_run_waji.sh`；`yaml.safe_load(deploy-dev.yml)` | OK |
| `check_release_environment.py` CLI 對 production / production-build 現況形狀 | 見 §5 AC-4（拒絕時只點名 `ODP_CLOUD_RUN_VPC_EGRESS`；補上後核准並回報 `direct_vpc`） |
| script 以 stub 工具實跑 13 種環境組合（§3 每一列） | Direct VPC / connector 進入 preflight；半套、兩套、零綁定、sources-off 無綁定、private-ranges-only、production 缺 URL／非 HTTPS 全部 exit 1 且未呼叫任何工具 |

未執行：CI（由 PR 觸發）；任何 production dispatch（AC-5）。

## 8. 後續與 ODP-PROD-BLUEGREEN-ROLLOUT-001 的前提

1. Human/Ops：在 `production` 與 `production-build` 設 `ODP_CLOUD_RUN_VPC_EGRESS=all-traffic`（模式宣告；**請勿**設 `ODP_CLOUD_RUN_VPC_CONNECTOR`）。
2. 依 ODP-PROD-NETWORK-PARITY-PLAN-001 README §5 對 production 執行 Terraform apply，讓 `oday-prod-runtime` network/subnetwork 真的存在。
3. WIF、lease issuer 等既有前提不變（不在本任務量測範圍）。
