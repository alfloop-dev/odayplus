# 核心營運無模型上線 release profile（`core-ops`）

Task: ODP-CORE-OPS-RELEASE-PROFILE-001 · Owner: Claude2 · Reviewer: Codex2
延伸自：`docs/design/ODP_DEV_ADMIN_RELEASE_PROFILE.md`（`dev-admin`）

## 0. 決策來源（逐字記錄）

> 決策依據：2026-10-19 負責人蔡尚志於 Claude Code 對話中，對「staging/prod 要求模型已就緒、模型需真實歷史資料，要走哪條路？」選擇「兩條同時進行」，其選項內容為：先新增無模型上線 profile（類似 dev-admin 但允許進 staging/prod），上線登入/權限/租戶/稽核等核心功能，模型功能如實顯示未就緒；真實資料到位後升級成 full。

- 決策日期：2026-10-19。決策人：負責人蔡尚志。管道：Claude Code 對話。
- 本 task 只實作這個決策中的「無模型上線 profile」這一條路。另一條路（真實資料、模型訓練、升級為 `full`）仍由既有的模型、資料與業務 task 負責。
- **本決策不是 production GO。** 本 task 沒有放行任何 production 部署，也沒有觸發 deploy workflow。
- **本決策不是 gate-3（Model and Solver Gate）簽署。** 本 task 不修改 `RELEASE_GATE_REGISTRY.json` 的任何 gate 狀態。§8 只是一份未簽署的 DRAFT。
- **本決策不關閉任何模型、資料或業務驗收 task。** 一次通過的 `core-ops` live gate 不能當作模型就緒、資料就緒或完整產品驗收的證據。它的 receipt 明確寫著 `model_readiness_claimed=false` 與 `full_product_acceptance_claimed=false`。

## 1. 三個 profile

| Profile | 可封存的建置目標（`target_environment`） | 可 admission 的環境 | Gate 對模型的要求 | 額外證明 |
|---|---|---|---|---|
| `full`（預設，不寫入 manifest） | –（不綁環境） | dev、staging、production | 正式模型綁定、ForecastOps available、一個核准的 `production` alias 與 lineage | – |
| `dev-admin` | dev | dev | 缺模型必須如實回報並拒絕 | dev 專屬的 Web 密碼 session journey 與 `ODP_DEV_ADMIN_*` 輸入 |
| `core-ops` | dev、staging | dev；staging；production（只收 staging 封存的 manifest，見 §3） | 缺模型必須如實回報並拒絕（與 dev-admin 同語意） | 無；不需要 dev-admin 的帳密、bundle 或 journey |

`core-ops` 和 `dev-admin` 一樣，不放寬下列任何檢查：persistence（PostgreSQL）、provider posture、auth 與受保護的 Web 路由、worker enqueue/replay/drain、稽核與完整性、資料來源（data origin）、surrogate marker、租戶隔離。它只把「模型必須在 production 就緒」這組斷言，換成「缺模型必須如實拒絕」。如果 runtime 回報 `productionBindingsReady=true`，`core-ops` 仍然要通過 `full` 的完整模型斷言（測試：`test_core_ops_with_verified_models_is_held_to_the_full_model_assertions`）。

## 2. Gate 一般化：「profile 未宣稱模型就緒」

`delivery_toolchain/e2e/check_live_e2e_gate.py`：

- 新增 `MODEL_READINESS_CLAIMING_PROFILES = {"full"}` 與 `GateConfig.model_readiness_claimed`。
- `_models_refused_in_scope` 原本判斷 `config.dev_admin`，現在改成判斷 `not config.model_readiness_claimed`。走這條「缺模型如實拒絕」分支的情況包括：
  - `_check_model_limitation`（`runtime:model_limitation_reported`）
  - `_check_model_capabilities` 的 refused-in-scope 分支
  - `_check_model_registry_refused`（`models:registry_refused`，要求 503；registry 已綁定時才允許空的 200）
  - `runtime:release_profile` 的 `modelReadinessClaimed` 期望值
  - receipt 中的 `release_profile.model_readiness_claimed`
- 只屬於 dev-admin、沒有被一般化的部分：
  - `config:dev_admin_account` / `config:dev_admin_denied_role`
  - `_check_dev_admin_session`（Web 密碼 journey）
  - `ODP_DEV_ADMIN_CREDENTIAL_BUNDLE` 及其他 `ODP_DEV_ADMIN_*` 輸入

  測試證明 `core-ops` 在完全沒有這些輸入時，dev、staging、production 三個環境都能通過，而且 report 裡沒有任何 `session:` 或 `admin:` 檢查。
- 完整驗收：`evaluate_full_acceptance` 對非 `full` profile 一律回報 `NOT_ADMITTED`。`live_business_journeys.KNOWN_RELEASE_PROFILES` 納入 `core-ops`，所以 core-ops manifest 會被辨識為已知 profile，而不是 unknown，之後再判定為 `NOT_ADMITTED`。caller 的 `ODP_RELEASE_PROFILE=full` 無法升級它。

## 3. 不可變綁定與 staging→production 一致性

### 3.1 問題

`release_profile.target_environment` 封存在 manifest digest 裡。依照 dev-admin 的規則，admission 時要求 `environment == target_environment`。repo 的設計是 staging 與 production 共用同一份 manifest：

- `release_manifest.py` 檔頭寫明 manifest 是 dev、ephemeral staging、production 共用的 artifact identity。
- `deploy-dev.yml` 的 `staging_closeout` 斷言 staging tfvars 的 `manifest_digest` 等於 production admission 的 `MANIFEST_DIGEST`。

所以照搬 dev-admin 的規則，`core-ops` 永遠無法從 staging 推進 production。

### 3.2 綁定方式（已實作）

- `RELEASE_PROFILE_ENVIRONMENTS["core-ops"] = {"dev", "staging"}`：只能建置封存為 dev 或 staging。
- `RELEASE_PROFILE_PROMOTIONS[("core-ops", "staging")] = {"staging", "production"}`：封存為 staging 的 core-ops manifest，**原樣**可 admission 到 production。
- `release_profile_admissible_environments(name, target)` 是唯一的計算點：
  - `release_profile_errors` 在 admission 時用它判斷。
  - runtime、gate、Web、staging 各鏡像把它沒有 target 時的聯集（`{dev, staging, production}`）釘住。
- 不可能的路徑會在 lease 消耗前被拒絕，lease 保持 `issued`：
  - dev 封存的 manifest 進 staging 或 production。
  - staging 封存的 manifest 回到 dev。
  - 以 production 為建置目標。這在 build 的 `release_phase` 與 `build_release_handoff` 就會拒絕，錯誤訊息會指出「production 只收 staging 封存的 manifest」。

為什麼不比 `full` 弱：

1. `full` 的 manifest 不綁任何環境。`core-ops` 只在明確的晉升路徑上可 admission。
2. production 的 core-ops 必然是 staging 演練過的同一份 manifest：digest 相同，四個 component image digest 也相同。production lease 針對同一個 digest 簽發。另外，`staging_closeout` 的「staging digest == production digest」斷言仍然生效。
3. 正向測試 `test_core_ops_admission_matches_full_on_its_promotion_path` 在同一套 lease、registry、admission harness 下，證明 dev→dev、staging→staging、staging→production 都和 `full` 一樣被 admit，lease 被 consumed，receipt 記錄 `release_profile=core-ops`。
4. 反向測試 `test_core_ops_off_its_promotion_path_is_refused_with_the_lease_unconsumed` 證明不在路徑上的組合被拒絕，lease 未被消耗。

如果要把封存值竄改為 production、改名、拿掉 profile、改宣稱模型就緒，或加入多餘欄位，都會破壞 digest 或被 `validate_manifest` 拒絕，即使重新封存也一樣。

### 3.3 部署、workflow、runtime

- `deploy-dev.yml`：
  - build-phase 的 `release_profile` 選項為 `[full, dev-admin, core-ops]`。
  - admission 輸出的 `case` 是 `full|dev-admin|core-ops`。
  - deploy phase 仍然沒有自己的 profile 輸入。
  - `ODP_DEV_ADMIN_CREDENTIAL_BUNDLE` 只注入給 dev 且 admitted profile 為 dev-admin 的部署，core-ops 拿不到（有測試）。
- `deploy_cloud_run_waji.sh`：`core-ops` 只能部署到 dev、staging、production，而且不讀任何 dev-admin 輸入。其他環境在任何 `gcloud` 或 `docker` 呼叫前就拒絕。
- `runtime_mode.release_profile`：`core-ops` 可服務 dev、staging、production，`modelReadinessClaimed=false`。在其他 deployment 會變成 `valid=false`，readiness 回 503。
- `automatic_dev`：自動 dev 部署維持 `dev-admin`，本 task 沒有修改。

## 4. Staging 路徑修正（fail closed）

修正前的問題：

- staging 不走 `deploy_cloud_run_waji.sh`。它走 `staging_lifecycle.py create/verify` 加上 `infra/terraform/modules/ephemeral_staging`。
- 這個 Terraform 模組不設定 `ODP_RELEASE_PROFILE`，所以無模型的 staging runtime 會預設為 `full`，並謊報 `modelReadinessClaimed=true`。
- verify 只讀 `/platform/health`、`/platform/version`、`/operator`，從不讀 `/readiness`。

修正後：

1. **Profile 來源。** deploy job 的 `ODP_RELEASE_PROFILE` 原本就只來自 admission 的輸出，也就是剛以 lease-bound digest 驗證過的 admitted manifest。workflow 把它以 `--release-profile` 傳給 `staging_lifecycle.py create` 與 `verify`。兩者都是**必填**，`choices` 限定為 `full|core-ops`，缺少或填錯在 argparse 就失敗。
2. **Runtime env。**
   - `StagingConfig.release_profile` 會寫進 tfvars。`validate_staging_config` 拒絕 `dev-admin`、unknown 與空值。
   - 模組新增 `variable "release_profile"`：沒有預設值，validation 為 `^(full|core-ops)$`。
   - API、Web、migration、worker、scheduler 五個容器都加上 `ODP_RELEASE_PROFILE`（數量與 `ODP_DEPLOY_ENV` 一致，有測試）。
   - 少了這個變數時，tfvars 契約檢查會報錯。
3. **Verify。** `verify_ephemeral_staging(..., release_profile=...)` 在 live 模式下會拒絕非 staging profile。live executor 的 `api_web_authenticated_smoke` stage 新增 `/readiness` 讀回，由 `staging_release_profile_errors` 判定：
   - `status=ok`、`deploymentMode=staging`。
   - `runtime:release_profile`：名稱等於 admitted profile、`valid=true`、只有 `full` 宣稱模型就緒。
   - readiness 503 會在讀取時直接失敗。
   - 接著讀 `GET /api/v1/learninghub/models`（HTTP 錯誤狀態照實回傳、不拋出，因為 503 本身就是要驗證的證據；連線失敗回 status 0，任何檢查都不接受）。模型斷言由 `staging_model_gate_checks` 執行，它**直接載入 canonical `check_live_e2e_gate.py` 的函式**，不另寫一份規則，兩者不會漂移。分支也由 gate 自己的 `_models_refused_in_scope` 決定：
     - **未宣稱就緒**（profile 不宣稱模型就緒，且 runtime 沒有回報 `productionBindingsReady=true`）：`runtime:model_limitation_reported`（`productionBindingsReady=false`、`autoSeeded=false`、mode 不是 `mlflow-production`、有 error、帶有 `PRODUCTION_MODEL_BINDINGS_UNVERIFIED`）、每個 `runtime:model_capability:*`（governed-disabled 證據完整；其餘 available 與 reasonCode 必須如實）、`models:registry_refused`（registry 實際回 503；runtime 回報 `learninghubRegistryBound=true` 時才接受 200 且空清單，沿用 gate 既有例外）與 `models:no_surrogate_markers`。
     - **宣稱就緒**（`full`，或 runtime 回報 `productionBindingsReady=true` 的 core-ops）：完整的 `runtime:model_bindings`、`runtime:model_capability:*`、`models:registry`，以及每個 active service 恰好一個 `production` alias 加 lineage 與 artifact store；governed-disabled service 不得有 alias。
     - 兩個分支都另外掃描 readiness 的 `details.models` 是否有 surrogate marker（`runtime:model_no_surrogate_markers`）。
     - 缺 capabilities、缺 registry 證據或 registry 讀不到都直接失敗，不做任何推定。
   - 任何不符都會讓 stage 失敗，並沿用既有的 `hold` 流程。stage 回執記錄通過的模型檢查名稱（`model_checks`）。
   - 前提：registry 已綁定時，`/learninghub/models` 會檢查 `model` VIEW 權限，staging 演練身分若沒有這個權限會以 401/403 失敗（dependency 標為 `auth`），仍是 fail closed。registry 未綁定時，Learning Hub 的 runtime binding guard 在權限檢查前就回 503。
4. **尚未涵蓋。** staging 尚未跑**完整的** `check_live_e2e_gate.py`（persistence、provider、worker、audit、租戶隔離、business journeys 等其他區段）。它需要 staging 目前沒有的 smoke 憑證、worker driver 與資料來源設定。後續項見 §7 的 F4。上面的模型最低檢查不在 F4 的延期範圍內，本 task 已實作。

## 5. 首次 staging/production release 的 rollback 綁定（F1，已實作）

Task: ODP-CORE-OPS-FIRST-RELEASE-ROLLBACK-001 · Owner: Claude2 · Reviewer: Codex2

### 5.1 修正前

- `INITIAL_RELEASE_ELIGIBLE_ENVIRONMENTS = ("dev",)`。staging 與 production 的首次 release 只能以 `rollback_release` 綁定一份先前可 admission 的 manifest。
- `validate_rollback_manifest` 驗 rollback 目標時不帶 environment，所以實務上只能拿一份在 dev admitted 過的 manifest 來當 staging／production 的「rollback 目標」。
- `check_runtime_admission.py` 呼叫 `validate_release_admission(manifest)` 時沒有帶 environment，`initial_release_recovery` 與目標環境的綁定只靠 workflow 裡另一個 probe step 檢查，不在權威 verifier 內。

### 5.2 盤點：各環境的 rollback 實際回到哪裡

| 環境 | 實際 recovery 機制 | 讀不讀 manifest 的 `rollback_release` | 首次 release 時回到哪裡 |
|---|---|---|---|
| staging（ephemeral） | `staging_lifecycle.py` 的 `rollback_rehearsal`：先讀回 release-scoped API／Web service 目前已釘在 revision 上的非零流量配置，部署同一個 image 的 probe revision（`--no-traffic`、tag `rollback-probe`），切 100%、讀 health，再還原原配置並讀回比對 | 不讀 | 回到 **candidate 自己已釘選的 revision**。全新環境沒有前一版，所以這個演練驗證的是「流量指標切換與還原」的機制，不是「回到前一個版本」 |
| production | `deploy_cloud_run_waji.sh` 在 deploy 前捕捉 API／Web traffic snapshot。失敗時 `release_recovery_mode` 依 snapshot 判斷：service 原本不存在就是 `initial-release-cleanup`，刪除 candidate job、維持零流量，log 寫明「沒有前一版可回滾」；否則還原 snapshot 的流量配置 | 不讀 | production 從未有 ODay Plus 工作負載，snapshot 顯示 service 不存在，所以是**刪除 candidate、維持 0% 流量**，等同 blue-green 的「移除 green、不切流量」。沒有舊狀態可保留，也不需要保留 |

結論：manifest 的 `rollback_release` 是一份宣告。runtime 回復永遠以 target 當下的狀態為準，兩個環境都不會去部署 `rollback_release` 指向的版本。

### 5.3 兩個方向，選 A

- **B（以已 admitted 的前一個 manifest 當 rollback 目標）不採用。**
  - 空的 production 在失敗時實際做的是刪除 candidate。manifest 卻會寫著一個在 production 從未存在過的 rollback 目標，正是 `EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN` §8.5「不得宣稱正在還原流量——那會描述一個從未存在過的版本」禁止的狀況。
  - 若要讓 B 成真，production 就得在事故當下部署一個從未在 production 驗證過的版本，風險比刪除 candidate 更高。
  - 自動 dev 部署是 `dev-admin`，依 profile 規則本來就不能進 staging／production，所以 B 還需要另外產生一份 core-ops／full 的 dev 前版，才有東西可綁。
- **A（擴充 `initial_release_recovery`）採用。** 它如實記錄 `rollback_target_available=false` 與 `recovery_method=delete-candidate-zero-traffic`，和 runtime 實際做的事一致。B 的程式路徑（`rollback_release`）維持原狀，沒有被放寬。

### 5.4 規則（已實作）

| 規則 | 實作 |
|---|---|
| 可封存首次 release 的建置目標只有 dev 與 staging；production 永遠不是 | `INITIAL_RELEASE_ELIGIBLE_ENVIRONMENTS = ("dev", "staging")`；`probe_release_target_absence.py --output` 拒絕 production |
| staging 封存的紀錄可 admission 到 staging 與 production；dev 封存的只能進 dev | `INITIAL_RELEASE_ADMISSIBLE_ENVIRONMENTS`、`initial_release_admissible_environments()`。由封存的 `target_environment` 推導，不是 manifest 欄位，所以 binding digest 已經涵蓋 |
| staging 的讀回對象是這個 release 會建立的 release-scoped 名稱 | `staging_release_target_names(release_id)` 與 `staging_lifecycle.get_ephemeral_resource_names` 一致（anti-drift 測試）；staging probe 只收 `--staging-release-id`，拒收 `--target` |
| 每次 admission 都要有**該環境**當下的讀回，綁 manifest digest 且未過期 | probe `--manifest` 寫出帶 `environment`、`observed_at`、`manifest_digest` 的 receipt；`check_runtime_admission.py --initial-release-receipt` 以 `initial_release_verification_errors` 驗證（10 分鐘內）。admission 環境等於封存目標時，讀回必須與封存的 readback 一致；production 則單獨驗證五個 target 全部不存在、Job 名稱為 SHA-suffixed candidate 名稱 |
| 一個 lineage 只能用一次 | `initial_release_lineage_errors`：staging／production admission 時讀 Supervisor lease store，只要 production 曾為**其他** manifest 消耗過 lease 就拒絕，該 release 必須綁定 `rollback_release`。同一份 manifest 的重試不算；staging 的多次演練嘗試不算；store 讀不到或紀錄無法分類一律拒絕 |
| 只允許外部來源關閉的 release | 沿用既有規則：`external_sources_expected_enabled` 非空時 build 拒絕、`validate_manifest` 也拒絕 |
| dev 不變 | dev 仍是 build 讀回加 admission 重讀；lineage 檢查不套用到 dev，自動 dev 路徑（`automatic_dev.py`）也沒有修改 |
| 權威 verifier 綁定環境 | `check_runtime_admission.py` 改呼叫 `validate_release_admission(manifest, environment=...)` |

### 5.5 Fail closed：全部在 lease 消耗前拒絕，lease 保持 `issued`

- admission 讀回缺失或無法讀取
- `observed_at` 缺失、沒有時區、在未來，或超過 10 分鐘
- 讀回的 `environment` 或 `absence_readback.target_environment` 與 admission 環境不符
- `manifest_digest`、`candidate_sha`、`release_id`、`binding_digest`、`recovery_method`、`recovery_actions` 與 admitted manifest 不符
- 任一 target 已存在或正在服務流量、target 清單不完整或重複、名稱不是這個 release 會部署的名稱
- `gcloud` 讀不到（權限、網路）時 probe 直接失敗，不寫 receipt
- production 已為其他 manifest admitted 過 release
- dev 封存的紀錄進 staging／production、staging 封存的紀錄進 dev
- 竄改後重新封存（改 `target_environment`、改 readback 環境、宣稱有 rollback 目標、改 `release_id`）仍被拒絕

測試：`tests/release/test_first_release_staging_production.py`。其中三個 mutation 測試把讀回驗證、lineage 驗證分別換成 no-op，或把 dev 的 admission 路徑擴大到 production，證明對應的拒絕確實是由該 guard 造成的。

### 5.6 與政策文件的對照

- `ROLLBACK_AND_POSITION_SEMANTICS.md`、`KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`：在 `origin/dev` 與整個 git 歷史中都不存在（`git log --all -- '*ROLLBACK_AND_POSITION_SEMANTICS.md' '*KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md'` 沒有結果）。它們只出現在 `ai-status.json` 的 `canonical_document_layers` 清單中，是 Pantheon 平台的文件分層樣板。本 task **無法逐條對照**，所以不宣稱一致，也沒有發現衝突；請 Human/Ops 確認這兩份政策是否存在於其他位置。
- `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`（status: proposed，非 canonical）：
  - §8.5 原本寫「只適用於 dev。staging 每個 release 重建、production 是被 promote 進去的，兩者都不會在沒有已核准前版的情況下走到首次部署」。對首次 production release 來說，這個前提不成立。本 task 依上面的規則更新 §8.5。
  - §8.2「任一步驟失敗，就刪除或停用 green，production blue 維持 100%」：首次 release 沒有 blue，刪除 green、維持 0% 與它一致。
  - §8.4 的回滾順序假設 blue 存在，不適用於首次 release，由 §8.5 處理。
  - §7.2「rollback rehearsal：service/image/snapshot pointer 回到前一版本」：既有實作演練的是 candidate 自己 revision 的流量切換（見 §5.2），和規劃文字有落差。這不是本 task 造成的，本 task 也沒有修改演練實作；列為觀察項。

### 5.7 前提與未涵蓋

- staging／production admission 的身分必須能 list lease store（GCS `storage.objects.list`）。若不能，lineage 檢查會拒絕（fail closed）。本 task 不改 IAM，也沒有 live 驗證這個權限。
- 首次 production release 若在 lease 消耗後失敗，且下一個候選是不同的 manifest，lineage 檢查會拒絕它，而它也沒有可綁的 rollback 目標。這是刻意的 fail closed，需要 Human/Ops 決策（後續項 F1-a）。
- admission receipt 由同一個 admission job 的 probe 產生，verifier 本身不執行 `gcloud`。能修改 workflow 的人可以偽造這份檔案，和 workflow 內其他 receipt 屬於同一信任等級。主要控制仍是 Supervisor lease 與 GitHub `production` environment 的人工核准。
- 沒有部署、沒有觸發 deploy workflow、沒有修改 `RELEASE_GATE_REGISTRY`、IAM 或 secret。首次 staging／production 的 live 執行仍是 Human/Ops 的後續步驟。

## 6. `infra/terraform/checks.tf` 對 core-ops production 的影響

- `check "production_model_runtime_contract"` 只會產生 warning。真正擋住的是 `checks.tf` 中 `terraform_data.production_contract` 的阻斷式 `precondition`。當 `var.environment == "prod"` 時，它要求：
  - HTTPS 的 `mlflow_tracking_uri`
  - 完整的 `required_model_config_env_names`：`ODP_AVM_LIQUIDITY_{APPROVED_AT,APPROVED_BY,ARTIFACT_SHA256,ARTIFACT_URI,DATASET_SNAPSHOT_ID,MODEL_VERSION}`、`ODP_AVM_MODEL_NAME`、`ODP_FORECAST_ENGINE`、`ODP_FORECAST_MODEL`
  - 真實的 artifact SHA-256 與 URI
- 沒有訓練好的模型，就沒有真實的 artifact 與核准中繼資料，production root stack 的 plan/apply 一定失敗。這和 release profile 無關，`infra/` 中沒有任何 profile 判斷。**本 task 不修改它，也不得以假值填補。** 後續項 F2。
- Runtime Release 的 production 部署不執行這個 root stack，而是用 `deploy_cloud_run_waji.sh` 以 gcloud 部署。該腳本與 `validate_cloud_run_live_deployment.py` 要求以下設定存在：
  - `ODP_FORECAST_ENGINE`、`ODP_FORECAST_MODEL`
  - `MLFLOW_TRACKING_URI`、`ODP_MLFLOW_CLOUD_RUN_AUDIENCE`

  這些是「設定存在」，不是「模型已核准」。dev 在 dev-admin 下已經以「有 MLflow 端點、沒有核准模型」的狀態如實回報 `PRODUCTION_MODEL_BINDINGS_UNVERIFIED`。production 需要由 Human/Ops 提供同等的 production 環境變數，本 task 不設定。

## 7. 具名後續項（本 task 未解決，不假裝已解決）

| ID（建議） | 內容 | 權責 |
|---|---|---|
| F1 `ODP-CORE-OPS-FIRST-RELEASE-ROLLBACK-BINDING` | **已由 ODP-CORE-OPS-FIRST-RELEASE-ROLLBACK-001 實作（見 §5）**：擴充 `initial_release_recovery` 到 staging（建置目標）與 production（晉升目標），admission 讀回綁 digest 且限時，production lineage 只能用一次 | Release owner |
| F1-a | 首次 production release 在 lease 消耗後失敗、下一個候選是不同 manifest 時，lineage 檢查會拒絕它且沒有可綁的 rollback 目標；如何解除需要具名決策（§5.7） | Release owner + Human/Ops |
| F2 `ODP-CORE-OPS-PROD-TERRAFORM-MODEL-CONTRACT` | `terraform_data.production_contract` 的模型 precondition 與 core-ops production 並存的方式（不得填假值；可能需要 profile-aware 的契約，且仍 fail closed） | Infra owner + ML Risk Owner |
| F3 gate-3 | Model and Solver Gate 的 passed-with-deviation 由 Human/Ops 具名簽署（見 §8 草稿），或維持 blocked | Product Owner + ML Risk Owner |
| F4 `ODP-CORE-OPS-STAGING-LIVE-GATE` | 在 staging 跑完整的 `check_live_e2e_gate.py`（需要 staging smoke 憑證、worker driver，並確認 provider posture） | Release owner |
| F5 | production 的 `staging-verified` registry stage 與 blue-green 路徑（既有阻擋，不屬於本 task） | Release owner |

## 8. gate-3 passed-with-deviation 草稿（DRAFT — 未簽署）

> **DRAFT · 未簽署 · 不具效力。** 本草稿由 ODP-CORE-OPS-RELEASE-PROFILE-001 準備，供 Human/Ops 具名審閱。本 task 沒有修改 `docs/evidence/gates/RELEASE_GATE_REGISTRY.json`，gate-3 仍為 `blocked`。在具名簽署並由獨立 task 寫入 registry 之前，不得引用本草稿作為 gate-3 通過的證據。

```yaml
gate: gate-3 (Model and Solver Gate)
proposed_status: passed-with-deviation   # DRAFT，未簽署
release_profile: core-ops
deviation:
  scope: >
    僅限以 core-ops profile admission 的 release（manifest.release_profile.name=core-ops，
    model_readiness=not_claimed）。production 中所有模型相依功能（ForecastOps、AVM、
    HeatZone、SiteScore、Learning Hub registry）維持如實拒絕：runtime 回報
    productionBindingsReady=false 與 PRODUCTION_MODEL_BINDINGS_UNVERIFIED，Learning Hub
    回 503，不提供替代預測、不建立任何模型版本、alias、lineage 或核准。
  not_covered:
    - 任何 full profile release
    - 模型訓練、回補、晉升與模型卡
    - 求解器（solver）功能
    - 完整產品／業務 journey 驗收
  required_checks_deviated:
    - dataset snapshot is reproducible and hash-identified
    - baseline comparison and segment metrics reviewed
    - calibration or coverage checked where applicable
    - model card or decision card completed
    - solver hard constraints, feasibility, infeasibility diagnostics, and runtime report captured
  compensating_controls:
    - live E2E gate 的 runtime:model_limitation_reported、runtime:model_capability:*、models:registry_refused
    - staging verify 的 runtime:release_profile，以及重用 live gate 的模型檢查（limitation、capabilities、registry refusal、surrogate；宣稱就緒時改用完整 bindings 與 alias/lineage 斷言）
    - Web 營運通知如實顯示「模型功能尚未就緒」
risk_owner: ""            # 風險承擔人（空白，待 Human/Ops 具名填寫）
approved_by: ""           # 空白
approved_at: ""           # 空白
review_by: ""             # 複審期限（空白，待簽署人決定；建議不超過 90 天）
reopen_trigger: >
  真實模型登錄完成時（任一模型取得核准的 production alias 與 lineage，或 runtime 回報
  productionBindingsReady=true），本 deviation 立即失效，gate-3 必須以真實模型證據重新評估，
  並以 full profile 重新上線。
```

## 9. 鏡像與 anti-drift

`tests/release/test_release_profile.py::test_every_profile_mirror_matches_the_manifest_vocabulary` 和 `test_profile_vocabularies_agree_across_manifest_runtime_and_gate` 把以下鏡像全部綁到 `release_manifest.RELEASE_PROFILES` 與 `release_profile_admissible_environments`：

- `check_live_e2e_gate.RELEASE_PROFILE_DEPLOYMENTS` 與 `MODEL_READINESS_CLAIMING_PROFILES`
- `runtime_mode._RELEASE_PROFILE_DEPLOYMENTS`
- `live_business_journeys.KNOWN_RELEASE_PROFILES`
- `deploy-dev.yml` 的 `release_profile.options` 與 admission `case`
- `deploy_cloud_run_waji.sh` 的 profile `case` 標籤
- `apps/web` 的 `OperatorReleaseProfile` TS union、各 profile 的通知文案分支、`RELEASE_PROFILE_DEPLOYMENTS`
- `staging_lifecycle.STAGING_RELEASE_PROFILES` 與 Terraform 的 `release_profile` validation

已用突變測試確認：從 TS union 或 shell `case` 移除 `core-ops`，測試就會失敗。

## 10. Web 營運通知

`OperatorReleaseNotice` 對 `core-ops` 顯示：「core-ops：核心營運功能（登入、權限、租戶隔離、稽核）已上線；本發布範圍不宣稱模型功能就緒，模型功能以下方實際狀態為準，不代表完整產品或模型驗收。」

- 實際缺模型時，下一行顯示「正式模型綁定尚未就緒；缺少模型的功能不可用，模型相依操作仍會拒絕，不提供替代預測。」，並列出不可用的模型功能。
- `operatorReleaseStatus` 在 dev、staging、production 都接受 `core-ops`，所以不會顯示為 unknown。
- 若 runtime 回報的 profile 名稱、`valid` 或 `modelReadinessClaimed` 與部署不符，仍然顯示 unknown，不會猜測。

## 11. 驗證

離線回歸測試只是測試輸入，不是 live 證據：

- `tests/release/test_release_profile.py`：建置、封存、晉升路徑、admission parity、lease 未消耗、竄改、deploy `case`、phase check、anti-drift。
- `tests/release/test_first_release_staging_production.py`：首次 staging／production release 的封存、probe、admission 讀回（缺失、過期、未來時間、環境不符、digest 不符、target 已存在）、lineage、lease 未消耗、竄改後重新封存，以及三個 mutation 測試。
- `tests/e2e/test_live_e2e_gate_dev_admin.py`（core-ops 區段）：三個環境的正向路徑、不需要 dev-admin 輸入、環境外拒絕、`full` 仍然拒絕、runtime profile 不符、偽造模型狀態、registry 不拒絕、已驗證模型時改用 `full` 斷言、PostgreSQL 失敗、`NOT_ADMITTED`、真實 API composition。
- `tests/e2e/test_live_business_journeys.py`：core-ops manifest 是已知 profile，但 `NOT_ADMITTED`。
- `tests/ops/test_ephemeral_staging_lifecycle.py` 等 staging 測試：profile 寫入 tfvars 與模組、verify 必填、`/readiness` 讀回 fail closed；模型檢查的兩個分支（缺模型時 registry 仍回 200 非空清單、capability 狀態不合法、宣稱就緒卻 `autoSeeded=true`、mode 錯誤、缺 capabilities、缺 production alias 或 lineage 都會失敗），以及 live executor 實際讀 registry 的整合測試。
- `apps/web` 的 vitest：`operatorReleaseStatus` 與 `OperatorAdminAccess`。

本 task 沒有部署、沒有觸發 deploy workflow、沒有修改 `RELEASE_GATE_REGISTRY`、沒有修改 IAM。live staging/production 執行是 Human/Ops 的後續步驟，本 task 沒有宣稱已完成。
