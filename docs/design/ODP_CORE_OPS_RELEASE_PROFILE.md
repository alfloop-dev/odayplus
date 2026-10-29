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
2. production 的 core-ops 必然是 staging 演練過的同一份 manifest：digest 相同，四個 component image digest 也相同。production lease 針對同一個 digest 簽發。另外，`staging_closeout` 的「staging digest == production digest」斷言仍然生效。production 部署前會把這四個 image 以相同 digest 複製到 production 自己的 registry，manifest 不改寫（§13）。
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
   - 憑證：這是受保護的讀取，與 §12 的 live gate 使用相同的分工。`Authorization` 帶 release smoke 身分的 token（audience 為 `urn:odp:staging-smoke:<release>`，API 只透過 `ODP_AUTH_SERVICE_AUDIENCES` 與 principal map 信任它，`model_owner` 角色具 `model` VIEW）。API audience 的 token 只放在 `X-Serverless-Authorization`，當 Cloud Run 傳輸憑證。若把 API audience token 當 application bearer，API 會以 `audience_mismatch` 拒絕，`full` 或 registry 已綁定的 core-ops 即使模型健康也會失敗。`/readiness`、`/platform/*` 是公開路徑，應用層不驗 bearer，維持只在 `Authorization` 帶 API audience token 作為 Cloud Run 傳輸憑證。
4. **完整 live gate（F4，已由 ODP-CORE-OPS-STAGING-LIVE-GATE-001 實作）。** staging verify 新增最後一個 stage `live_e2e_gate`，執行**完整的** `check_live_e2e_gate.py`（persistence、provider、auth、Web 受保護路由、模型、worker、audit、資料來源）。smoke 身分、worker driver 與 provider posture 的設定來源見 §12。上面的模型最低檢查保留在 `api_web_authenticated_smoke`，兩者不衝突。

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

**失敗時實際做什麼（兩個環境不同）：**

- production（以及 dev）：`deploy_cloud_run_waji.sh` 的 `initial-release-cleanup`，刪除 candidate、維持零流量。
- ephemeral staging：`deploy-dev.yml` 的「Hold ephemeral staging resources on failure」只執行 `staging_lifecycle.py hold`。它把 release-scoped namespace 標成 `retained_for_debugging` 並記下 TTL，**不刪 candidate，也不動流量**。namespace 之後由 `staging_lifecycle.py cleanup` 整個拆除（`scan-orphans` 會標出過期的資源）。這個 namespace 只屬於這個 release，沒有其他 release 的流量依賴它，所以沒有流量需要保護或還原。

### 5.3 兩個方向，選 A

- **B（以已 admitted 的前一個 manifest 當 rollback 目標）不採用。**
  - 空的 production 在失敗時實際做的是刪除 candidate。manifest 卻會寫著一個在 production 從未存在過的 rollback 目標，正是 `EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN` §8.5「不得宣稱正在還原流量——那會描述一個從未存在過的版本」禁止的狀況。
  - 若要讓 B 成真，production 就得在事故當下部署一個從未在 production 驗證過的版本，風險比刪除 candidate 更高。
  - 自動 dev 部署是 `dev-admin`，依 profile 規則本來就不能進 staging／production，所以 B 還需要另外產生一份 core-ops／full 的 dev 前版，才有東西可綁。
- **A（擴充 `initial_release_recovery`）採用。** 它如實記錄 `rollback_target_available=false`，並按環境寫明失敗時實際做的事（見 §5.4「recovery 語意依環境」）。B 的程式路徑（`rollback_release`）維持原狀，沒有被放寬。

### 5.4 規則（已實作）

| 規則 | 實作 |
|---|---|
| 可封存首次 release 的建置目標只有 dev 與 staging；production 永遠不是 | `INITIAL_RELEASE_ELIGIBLE_ENVIRONMENTS = ("dev", "staging")`；`probe_release_target_absence.py --output` 拒絕 production |
| staging 封存的紀錄可 admission 到 staging 與 production；dev 封存的只能進 dev | `INITIAL_RELEASE_ADMISSIBLE_ENVIRONMENTS`、`initial_release_admissible_environments()`。由封存的 `target_environment` 推導，不是 manifest 欄位，所以 binding digest 已經涵蓋 |
| staging 的讀回對象是這個 release 會建立的 release-scoped 名稱 | `staging_release_target_names(release_id)` 與 `staging_lifecycle.get_ephemeral_resource_names` 一致（anti-drift 測試）；staging probe 只收 `--staging-release-id`，拒收 `--target` |
| 每次 admission 都要有**該環境**當下的讀回，綁 manifest digest 且未過期 | probe `--manifest` 寫出帶 `environment`、`observed_at`、`manifest_digest` 的 receipt；`check_runtime_admission.py --initial-release-receipt` 以 `initial_release_verification_errors` 驗證（10 分鐘內）。admission 環境等於封存目標時，讀回必須與封存的 readback 一致；production 則單獨驗證五個 target 全部不存在、Job 名稱為 SHA-suffixed candidate 名稱 |
| 一個 lineage 只能用一次 | `initial_release_lineage_errors`：讀 **production 的** Supervisor lease store，只要 production 曾為**其他** manifest 消耗過 lease 就拒絕，該 release 必須綁定 `rollback_release`。同一份 manifest 的重試不算；staging 的多次演練嘗試不算 |
| lineage 的來源是 production store | 每個環境的 lease store 是分開的（`vars.ODP_RELEASE_LEASE_STATE_URI`：staging=`gs://odayplus-runtime-20260825-release-leases/leases`，production=`gs://odayplus-prod-20260826-release-leases/leases`），staging 的 store 永遠不會有 production 的紀錄。production admission 讀自己的 store，給它不同的 `--lineage-state-uri` 會被拒絕。staging admission 必須以 `--lineage-state-uri`（workflow 取 staging environment 的 `vars.ODP_PRODUCTION_RELEASE_LEASE_STATE_URI`）跨環境讀 production store；沒有給、給的是 staging 自己的 store、打不開、讀不到，或 store 裡有任何非 production 的 lease（表示它不是 production store），都拒絕。workflow 的 `Validate shared lease store configuration` 對 staging 的這個 variable 套用與主 lease store 相同的 `^gs://[^/]+/.+` 形狀檢查：有設定但不是 `gs://bucket/prefix`（本機目錄、`.`、只有 bucket、空 prefix），在 WIF 驗證與 lease 消耗前就讓 job 失敗 |
| 紀錄要先分類才能略過 | `_lineage_record_problem`：record 與 lease 都必須是物件、`state` 屬於 `issued`／`consumed`／`revoked`（與 `release_lease.LEASE_STATES` 有 anti-drift 測試）、record 與 lease 的 `lease_id` 一致、`target_environment` 屬於 dev／staging／production，production lease 還必須有 sha256 `manifest_digest`。任一不成立就拒絕，不當成無關紀錄略過；一筆壞的紀錄不會被其他正常紀錄稀釋 |
| 毀損的紀錄不是空歷史 | 本機目錄 lease store（`LeaseStateStore`）讀到存在但內容不是 JSON object 的 `lease-<32hex>.json`（`null`、陣列、字串、數字、布林）時拋 `LeaseStateError`，與 GCS backend 的 `_read` 一致；`list_records` 不再把 `null` 當成不存在而略過，admission 因「讀不到 production lease history」在 lease 消耗前拒絕。CAS transition 遇到同樣的紀錄也拒絕，不改寫檔案。內容不是合法 UTF-8 的紀錄同樣拋 `LeaseStateError`（`get` 與 `_transition` 都把 `UnicodeDecodeError` 包進去），verifier 照常寫出 admission receipt，不會以 traceback 結束 |
| recovery 語意依環境 | `INITIAL_RELEASE_ENVIRONMENT_RECOVERY`。dev／production：`delete-candidate-zero-traffic`（刪 service、job、scheduler trigger，維持零流量）。staging：`forensic-hold-release-scoped-namespace`（`hold-release-scoped-namespace-for-forensics`、`cleanup-release-scoped-namespace-after-ttl`），不承諾刪除也不承諾零流量。staging 封存的紀錄頂層寫 staging 自己的 hold，另以 `promotion_recovery` 寫明晉升到 production 時的刪除 candidate；兩者都在 binding digest 內。dev 紀錄沒有 `promotion_recovery`，binding digest 的計算方式與修正前相同。admission receipt 的 `recovery_method`／`recovery_actions` 必須等於 manifest 對**該 admission 環境**寫明的那一份 |
| 只允許外部來源關閉的 release | 沿用既有規則：`external_sources_expected_enabled` 非空時 build 拒絕、`validate_manifest` 也拒絕 |
| dev 不變 | dev 仍是 build 讀回加 admission 重讀；lineage 檢查不套用到 dev，自動 dev 路徑（`automatic_dev.py`）也沒有修改 |
| 權威 verifier 綁定環境 | `check_runtime_admission.py` 改呼叫 `validate_release_admission(manifest, environment=...)` |

### 5.5 Fail closed：全部在 lease 消耗前拒絕，lease 保持 `issued`

- admission 讀回缺失或無法讀取
- `observed_at` 缺失、沒有時區、在未來，或超過 10 分鐘
- 讀回的 `environment` 或 `absence_readback.target_environment` 與 admission 環境不符
- `manifest_digest`、`candidate_sha`、`release_id`、`binding_digest` 與 admitted manifest 不符；`recovery_method`、`recovery_actions` 不是 manifest 對這個 admission 環境寫明的那一份（例如 production receipt 寫 staging 的 hold）
- 任一 target 已存在或正在服務流量、target 清單不完整或重複、名稱不是這個 release 會部署的名稱
- `gcloud` 讀不到（權限、網路）時 probe 直接失敗，不寫 receipt
- production 已為其他 manifest admitted 過 release（staging admission 也一樣，因為它讀的是 production store）
- staging admission 沒有 production lineage store，或給的是 staging 自己的 store、打不開、讀不到、含非 production lease
- lineage 紀錄無法分類：`state` 未知或缺漏、lease 是空物件、`target_environment` 是 null 或未知值、`lease_id` 不一致、production lease 沒有合法 `manifest_digest`
- lineage store 裡有存在但不是 JSON object 的紀錄（例如內容為 `null`），或內容不是合法 UTF-8 的紀錄（admission receipt 照常寫出）
- staging environment 的 `ODP_PRODUCTION_RELEASE_LEASE_STATE_URI` 有設定但不是 `gs://bucket/prefix`（workflow 在 admission step 之前拒絕）
- dev 封存的紀錄進 staging／production、staging 封存的紀錄進 dev
- 竄改後重新封存（改 `target_environment`、改 readback 環境、宣稱有 rollback 目標、改 `release_id`、讓 staging 宣稱刪除 candidate／零流量、拿掉或改寫 `promotion_recovery`、在 dev 紀錄加上 `promotion_recovery`）仍被拒絕

測試：`tests/release/test_first_release_staging_production.py`。mutation 測試分別：把讀回驗證換成 no-op；把 lineage 驗證換成 no-op；讓 staging 回到「讀自己的 store」（重現審查指出的繞過：production 已 admitted A，B 仍能進 staging 並消耗 lease）；把紀錄分類換回只檢查形狀的舊版（`state="unknown"`、未知環境會被略過）；把 dev 的 admission 路徑擴大到 production（此時仍被缺少 `promotion_recovery` 擋下，兩道都拿掉才會通過）；以及讓目錄 store 的 `get` 回到不檢查型別的舊版（production store 內一筆 `null` 紀錄被略過，staging 與 production 的首次 release 都會被 admitted 並消耗 lease）；讓 `get` 回到不包 `UnicodeDecodeError` 的舊版（verifier 以例外結束、沒有 admission receipt）。workflow 的形狀檢查以 bash 實際執行該 step 的 script 驗證。每個都證明對應的拒絕確實是由該 guard 造成的。

### 5.6 與政策文件的對照

- `ROLLBACK_AND_POSITION_SEMANTICS.md`、`KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`：在 `origin/dev` 與整個 git 歷史中都不存在（`git log --all -- '*ROLLBACK_AND_POSITION_SEMANTICS.md' '*KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md'` 沒有結果）。它們只出現在 `ai-status.json` 的 `canonical_document_layers` 清單中，是 Pantheon 平台的文件分層樣板。本 task **無法逐條對照**，所以不宣稱一致，也沒有發現衝突；請 Human/Ops 確認這兩份政策是否存在於其他位置。
- `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`（status: proposed，非 canonical）：
  - §8.5 原本寫「只適用於 dev。staging 每個 release 重建、production 是被 promote 進去的，兩者都不會在沒有已核准前版的情況下走到首次部署」。對首次 production release 來說，這個前提不成立。本 task 依上面的規則更新 §8.5。
  - §8.2「任一步驟失敗，就刪除或停用 green，production blue 維持 100%」：首次 release 沒有 blue，刪除 green、維持 0% 與它一致。
  - §8.4 的回滾順序假設 blue 存在，不適用於首次 release，由 §8.5 處理。
  - §7.2「rollback rehearsal：service/image/snapshot pointer 回到前一版本」：既有實作演練的是 candidate 自己 revision 的流量切換（見 §5.2），和規劃文字有落差。這不是本 task 造成的，本 task 也沒有修改演練實作；列為觀察項。

### 5.7 前提與未涵蓋

- production admission 的身分必須能 list 自己的 lease store（GCS `storage.objects.list`）。staging admission 的身分還必須能**讀 production 的 lease bucket**，而且 staging environment 要設定 `ODP_PRODUCTION_RELEASE_LEASE_STATE_URI`。這兩項目前都不存在：本 task 不改 IAM、不新增 GitHub variable。在 Human/Ops 補上之前，staging 的首次 release admission 一律拒絕（fail closed），lease 不會被消耗。後續項 F1-b。
- `ODP_PRODUCTION_RELEASE_LEASE_STATE_URI` 由 staging environment 的管理者設定，和其他 environment variable 同一信任等級。workflow 只檢查形狀（`gs://bucket/prefix`），不核對它是不是 production 那一個 bucket：形狀正確但指到一個空的 bucket／prefix 時，verifier 無法和「production 從未發過 lease」區分；指到 staging 自己的 store，或任何含非 production lease 的 store，會被拒絕。production admission 本身讀的是自己的 store，所以 production 的 lineage 判斷不受這個 variable 影響。
- lineage 檢查與 lease 消耗不是原子操作，兩者之間 production 可以多出一筆 consumed 紀錄；`issued` 的 production lease 不算 admitted（既有語意，`issued-only` 測試）。前提：**同一時間只會有一個 production admission 在讀 lineage 並消耗 lease**。保證來源是 `deploy-dev.yml` 的 workflow 層 `concurrency`（`runtime-release-production-deploy`，`cancel-in-progress: false`）：兩個 production deploy run 不會交錯，後到的 run 在前一個結束（lease 已 consumed）後才讀 lineage，因此被拒絕；`deploy-dev.yml` 也是唯一消耗 production lease 的路徑。若有人在 workflow 之外手動對 production store 執行 verifier，這個前提就改由 Supervisor 與 release owner 保證：同時只簽發一張 initial-release production lease，要改簽另一份 manifest 前先 revoke 前一張。不採用「其他 manifest 的 `issued` production lease 也拒絕」：它會改變已通過的語意，而且任何一張未 revoke 的舊 lease 都會擋住首次 release。staging admission 用的是另一個 concurrency group，可能在 production 消耗另一份 manifest 的 lease 時通過；那份 manifest 進 production 時仍會被 production 自己的 lineage 拒絕，production 的性質不受影響。
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
| F1-b | staging environment 設定 `ODP_PRODUCTION_RELEASE_LEASE_STATE_URI`，並授權 staging admission 身分唯讀 production lease bucket（`storage.objects.list`／`get`）。未完成前 staging 首次 release 一律拒絕（§5.7） | Human/Ops（IAM、GitHub environment） |
| F2 `ODP-CORE-OPS-PROD-TERRAFORM-MODEL-CONTRACT` | `terraform_data.production_contract` 的模型 precondition 與 core-ops production 並存的方式（不得填假值；可能需要 profile-aware 的契約，且仍 fail closed） | Infra owner + ML Risk Owner |
| F3 gate-3 | Model and Solver Gate 的 passed-with-deviation 由 Human/Ops 具名簽署（見 §8 草稿），或維持 blocked | Product Owner + ML Risk Owner |
| F4 `ODP-CORE-OPS-STAGING-LIVE-GATE` | 在 staging 跑完整的 `check_live_e2e_gate.py`。**程式已由 ODP-CORE-OPS-STAGING-LIVE-GATE-001 實作（見 §12）**；實際 live 執行仍需 §12.5 的 Human/Ops 前置條件 | Release owner |
| F5 | production 的 `staging-verified` registry stage 與 blue-green 路徑（既有阻擋，不屬於本 task） | Release owner |
| F6 | production image promotion 的 IAM 與 repository 前置：production deployer 對 staging AR repository 的 reader、對 production AR repository 的 writer、production repository 存在（§13.5；確切指令見 `docs/evidence/runtime/ODP-PROD-IMAGE-PROMOTION-001/README.md`）。未完成前 production 部署在 promotion 步驟失敗，Cloud Run 不被變更 | Human/Ops（IAM、Artifact Registry） |

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
- `tests/release/test_first_release_staging_production.py`：首次 staging／production release 的封存、probe、admission 讀回（缺失、過期、未來時間、環境不符、digest 不符、target 已存在）、lineage（staging 與 production 分開的 store、無法分類的紀錄）、依環境的 recovery 語意與 workflow 失敗路徑 anti-drift、lease 未消耗、竄改後重新封存，以及 mutation 測試。
- `tests/e2e/test_live_e2e_gate_dev_admin.py`（core-ops 區段）：三個環境的正向路徑、不需要 dev-admin 輸入、環境外拒絕、`full` 仍然拒絕、runtime profile 不符、偽造模型狀態、registry 不拒絕、已驗證模型時改用 `full` 斷言、PostgreSQL 失敗、`NOT_ADMITTED`、真實 API composition。
- `tests/e2e/test_live_business_journeys.py`：core-ops manifest 是已知 profile，但 `NOT_ADMITTED`。
- `tests/ops/test_ephemeral_staging_lifecycle.py` 等 staging 測試：profile 寫入 tfvars 與模組、verify 必填、`/readiness` 讀回 fail closed；模型檢查的兩個分支（缺模型時 registry 仍回 200 非空清單、capability 狀態不合法、宣稱就緒卻 `autoSeeded=true`、mode 錯誤、缺 capabilities、缺 production alias 或 lineage 都會失敗），以及 live executor 實際讀 registry 的整合測試。
- `apps/web` 的 vitest：`operatorReleaseStatus` 與 `OperatorAdminAccess`。

本 task 沒有部署、沒有觸發 deploy workflow、沒有修改 `RELEASE_GATE_REGISTRY`、沒有修改 IAM。live staging/production 執行是 Human/Ops 的後續步驟，本 task 沒有宣稱已完成。

## 12. Staging 完整 live E2E gate（F4）

Task: ODP-CORE-OPS-STAGING-LIVE-GATE-001 · Owner: Claude2 · Reviewer: Codex2

### 12.1 執行位置與判定

- `staging_lifecycle.py verify` 的 live executor 新增第 10 個 stage `live_e2e_gate`，排在 `external_providers_disabled_readback` 之後，也就是在 rollback 演練把流量還原之後，對還原後的 release 執行。
- 這個 stage 直接載入 canonical `check_live_e2e_gate.py`，呼叫它的 `evaluate_gate`，不另寫規則。`GateConfig.release_profile` 是 admitted manifest 的 profile（workflow 的 `--release-profile`，只來自 admission 輸出）：
  - `core-ops`：缺模型必須如實拒絕（`runtime:model_limitation_reported`、`models:registry_refused` 等）。
  - `full`：完整模型斷言（`runtime:model_bindings`、`models:*:lineage`）。
  - 其他 profile（含 `dev-admin`、空值）在 gate 執行前就拒絕。
- gate 回報任何 blocker，stage 就回 `success=false`。verify receipt 因此失敗，CLI exit 1，lifecycle 狀態寫成 `verification_failed`。workflow 既有的 `if: failure()` hold 步驟照常保留資源，流程沒有改。
- receipt 的 stage proof 只放 `live_e2e_gate` 摘要：`ok`、profile 宣稱、每個 check 的名稱與結果、`blocking_dependencies`、`full_acceptance_status`。不放 URL、request detail、token。失敗時 `errors` 帶 blocker 名稱與經 gate redactor 處理過的 detail，並再用本次三個 token 做一次 redaction。gate 內部例外只回報例外型別。
- staging 不提供 business journey receipt，所以 `full_acceptance_status` 不會是 `PASSED`。staging 演練證明的是 runtime gate，不是完整產品驗收。

### 12.2 Staging smoke 帳號（身分來源）

- **身分**：這個 release 自己的 runtime service account（`staging_runtime_service_account`）。它就是 verify 既有規定的 operator identity（`--operator-identity` 必須等於這個 output）。Terraform 隨 release 建立它，release destroy 時一起刪除。
- **API 如何信任它**：模組只在 API 容器加上四個非機密 env：
  - `ODP_AUTH_SERVICE_ISSUER=https://accounts.google.com`
  - `ODP_AUTH_SERVICE_JWKS_URI=https://www.googleapis.com/oauth2/v3/certs`
  - `ODP_AUTH_SERVICE_AUDIENCES=urn:odp:staging-smoke:<release_label>`
  - `ODP_AUTH_PRINCIPAL_MAP`：只有一筆，key 是 `google_service_account.staging_runtime.unique_id`（ID token 的數字 `sub`），值是 `roles=["operations_manager","model_owner","data_owner"]` 與 `tenant_id=<release tenant>`。

  issuer/JWKS 與 root stack 的 `service_auth_*` 預設值相同。
- **Token**：gate stage 執行時，以 `gcloud auth print-identity-token --impersonate-service-account=<runtime SA> --include-email` 現場簽發三個短效 ID token：
  - smoke bearer（audience 是上面的 urn）
  - API transport（audience 是 API URI）
  - Web transport（audience 是 Web URI）

  三個 token 只存在記憶體，不寫入檔案、receipt 或 log。
- **沒有新增**：
  - 沒有密碼。
  - 沒有新 secret（模組的 secret 仍只有 database URL、cursor key、web session 三個）。
  - 沒有新 IAM binding（模組的 `*_iam_member` 清單不變，有測試）。
  - 沒有新 Terraform variable。
  - 沒有讀取任何 dev 輸入：`ODP_DEV_ADMIN_*`、`ODP_DEV_ADMIN_CREDENTIAL_BUNDLE`、`ODP_OPERATOR_SMOKE_*`、`ODP_AUTH_PRINCIPAL_MAP_SECRET`、`ODP_IDENTITY_BOOTSTRAP_*`。staging 的 `GateConfig` 完全由 Terraform outputs 與 admitted manifest 組成，不讀 process env（有測試：即使 env 帶有 dev 值也不會進入 config）。
- **為什麼不用 identity bootstrap 的密碼帳號**：bootstrap 只建立 `platform_admin`，而且 `must_change=true`。core-ops/full gate 需要的是帶有 operator、model、integration、audit 讀取權的 bearer principal（與 dev smoke 相同的三個角色），密碼帳號還得經過 Web 登入與換密碼流程，也需要新的 signing key secret。用 release 自己的 service identity 可以不新增任何 secret，而且帳號與環境同生同滅。
- **銷毀**：principal map 只引用這個 release 的 runtime SA，該 SA 在 `plan_staging_resources` 與 release ownership inventory 中。cleanup 的 release-scoped `terraform destroy` 會同時刪除 SA 與帶著 principal map 的 API service。release 銷毀後，這個身分已不存在，map 也已不存在。

### 12.3 Worker driver（設定來源）

- `CloudRunWorkerDriver`，job 是 Terraform output `staging_worker_job_name`，region/project 是 verify 的 `--region`/`--project-id`，gcloud 執行檔是 executor 的 `gcloud_bin`。
- 以 `--args=product_ops/deployment/cloud_run_job_entrypoint.py,worker,--max-jobs,4` 執行。gate 在 provider disabled 時只 enqueue 一個 probe job，再加上 replay。
- 執行前先讀回 worker job 的 image 與 VPC egress，確認等於 Terraform output 並為 `ALL_TRAFFIC`。

### 12.4 Provider posture（設定來源）

- 模組把五個容器的 `ODP_EXTERNAL_PROVIDER_MODE` 都寫死為 `disabled`，沒有 variable 可以改。
- gate 以 `external_provider_mode=disabled`、`required_provider_ids=()` 執行。它要求 runtime 回報 disabled，worker probe 對 `listing.partner_feed` 產生 `provider_mode_disabled` 的 BLOCKED receipt（`worker:terminal_disabled`），並且不得有任何 live ingestion。
- 外部來源維持 disabled。本 task 沒有任何啟用路徑。

### 12.5 Human/Ops 前置條件（本 task 沒有新增，也沒有設定）

以下是**既有** verify stage 本來就依賴、這次 live gate 也依賴的條件。本 task 沒有修改 IAM、secret、網路或 workflow 變數；live staging 執行前需要由 Human/Ops 確認：

1. **ID token 簽發權**：模組只授予 deployer `roles/iam.serviceAccountUser`（`actAs`），它不含 `iam.serviceAccounts.getOpenIdToken`。verify 既有的 `_live_identity_token` 與本 gate 都需要 deployer 對 release runtime SA 有 `roles/iam.serviceAccountOpenIdTokenCreator`（或在專案層級已有同等權限）。
2. **Cloud Run invoker**：模組只授予 web SA 與 worker SA `run.invoker`。verify 以 runtime SA 的 token 讀 API 與 Web，需要 runtime SA 對兩個 service 有 `roles/run.invoker`（或在專案層級已有同等權限）。
3. **API 可達性**：staging API 的 ingress 是 `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`。執行 verify 的 runner 必須在可到達它的網路路徑上（既有 `/platform/health` 讀取也是如此）。
4. **JWKS 連線**：API 的 egress 是 `ALL_TRAFFIC`，經過 default-deny 的 VPC。驗證 smoke token 需要從 API 連到 `www.googleapis.com`（Private Google Access 與對應的 firewall/DNS）。GCS、Pub/Sub 本來就需要同樣的路徑。
5. **既有 stage 的已知問題（不屬於本 task）**：`worker_idempotency` 以 `--max-jobs 1 --require-job` 執行 worker job。scheduler trigger 是 paused，演練中沒有其他步驟先 enqueue，所以佇列為空時這個 stage 會以 `required_job_not_claimed` 失敗。gate 的 worker 檢查（enqueue、replay、drain、terminal、audit）是自足的，不依賴這個 stage。

這些條件不成立時，gate 會如實失敗（例如 `auth:operator_bootstrap` 401 的 dependency 是 `auth`），stage 失敗並進入 hold，不會被當成通過。

#### 12.5.1 IaC 修正（ODP-STAGING-REHEARSAL-NETWORK-IDENTITY-001）

Task: ODP-STAGING-REHEARSAL-NETWORK-IDENTITY-001 · Owner: Claude2 · Reviewer: Codex2。只改 Terraform 原始碼，沒有 apply、部署、觸發 workflow，也沒有改 live IAM、secret 或 GitHub 變數。

- **第 1 項（已改）**：`modules/ephemeral_staging` 在 `deployer_sa_user_*` 旁新增 `deployer_id_token_runtime`，授予 deployer 對這個 release 的 runtime SA `roles/iam.serviceAccountOpenIdTokenCreator`。授權範圍是 SA 本身，不是專案層級。
- **第 2 項（已改）**：在 `staging_web_invokes_api` 旁新增 `staging_runtime_invokes_api`、`staging_runtime_invokes_web`，授予 runtime SA 對 staging API 與 staging Web 的 `roles/run.invoker`。兩者都是 service 層級，模組仍沒有 `allUsers`／`allAuthenticatedUsers`。三筆授權都跟 release 一起建立、一起 destroy。§12.2 的「沒有新 IAM binding」只描述 F4 task 當時的狀態；`tests/ops/test_ephemeral_staging_lifecycle.py` 的 IAM 清單已改成包含這三筆。
- **第 4 項（已改）**：`modules/runtime_foundation/network.tf` 為該模組的 runtime VPC 建立兩個私有 zone，`<name_prefix>-runtime-googleapis`（`googleapis.com.`）與 `<name_prefix>-runtime-run`（`run.app.`）。結構沿用 `dev_connector_egress`：一筆 VIP A 記錄，加上一筆 wildcard CNAME 指向它。zone 只綁 `google_compute_network.runtime`，名稱依環境區分，不會與 default 網路的 `oday-dev-connector-*` 撞名。root 的 `required_apis` 加入 `dns.googleapis.com`。
  - **VIP 選擇：兩個 zone 都用 `private.googleapis.com`（199.36.153.8/30），不用 restricted。** 依 Google 官方文件（Configure Private Google Access 的 domain 表、VPC-SC restricted VIP services 清單）：
    - restricted VIP 只服務支援 VPC Service Controls 的 API，其餘一律阻擋。
    - API 驗 service token 用的 JWKS 主機 `www.googleapis.com` 不在 restricted 清單上。`modules/opsboard/auth/jwks.py` 以 `follow_redirects=False` 直接 GET 這個主機。
    - `*.run.app` 只列在 private VIP 下。
    - Secret Manager、Cloud Storage、Pub/Sub、Cloud SQL Admin、IAM Credentials 在 restricted 清單上，也都被 private 的 `*.googleapis.com` 涵蓋。
    - Google 要求不要混用兩組 VIP，所以 googleapis 也一起用 private，與 dev connector 的 restricted 不同。
    - 既有 firewall `allow_restricted_google_apis` 已放行 199.36.153.8/30 tcp:443，firewall 不變。
    - 殘餘風險：private VIP 不提供 VPC-SC 的資料外洩限制。repo 內沒有任何 VPC-SC perimeter（`access_context_manager`）定義。live 專案是否有 perimeter，本 task 沒有讀回。
- **第 3 項（未改，待負責人決定）**：本 task 原定把 staging API 的 ingress 由 `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` 改為 `INGRESS_TRAFFIC_ALL`，存取仍只靠 IAM、不加 `allUsers`，目的是對齊 production 由 `deploy_cloud_run_waji.sh` 建立的 API 形態。這一行修改在 auto worker 中被判定為降低安全性而擋下，目前沒有包含在本次變更中。在負責人核准並套用之前，第 3 項仍然成立，GitHub-hosted runner 的 verify 讀不到 staging API。
  - 後續事項，不在本 task 修改：root `infra/terraform/cloud_run.tf` 仍宣告 internal LB ingress。若日後 apply root stack，runner 端的 smoke／live E2E 也會讀不到 API。

### 12.6 測試

`tests/ops/test_ephemeral_staging_lifecycle.py::StagingLiveE2EGateTests`：
- core-ops 在 staging 以缺模型如實拒絕通過完整 gate，且所有區段都有實際執行。
- `full` 套用完整模型斷言，對缺模型 runtime 失敗。
- persistence、auth、Web 登入導向、worker drain、registry 不拒絕，每一種失敗都會讓 stage 失敗。
- 不在 staging profile 清單中的 profile，不會進入 gate。
- live verify 失敗會寫入 `verification_failed`，可被 hold。
- 即使 API 回應內容夾帶三個 token，receipt 與 errors 中也不會出現 token。
- 例外只回報型別。
- `GateConfig` 不讀 dev 的 env。
- worker driver 的 argv。
- API/Web client 綁定 release origin 與 transport token。
- Terraform 的 audience、角色、principal map 與 Python 釘選值一致。
- 沒有新增 secret、IAM 或 dev 輸入。
- smoke 身分是 release 擁有、會隨 release 刪除的資源。

本 task 沒有部署、沒有觸發 deploy workflow、沒有修改 `RELEASE_GATE_REGISTRY`、IAM 或 secret。

## 13. production 部署前的 image promotion（ODP-PROD-IMAGE-PROMOTION-001）

Task: ODP-PROD-IMAGE-PROMOTION-001 · Owner: Claude · Reviewer: Human/Ops

### 13.0 決策來源（逐字記錄）

> 決策依據：2026-10-24 負責人蔡尚志在『production 跨專案拉 image』兩案中選 (b)：把 image 複製到 production 自己的 registry，不讓 production runtime 依賴 staging 專案（staging 專案 odayplus-runtime-20260825 曾因 BILLING_ISSUE 暫停）。

- 決策日期：2026-10-24。決策人：負責人蔡尚志。
- 另一案 (a)（production 跨專案從 staging registry 拉 image）不採用：production runtime 會依賴 staging 專案的存續與 billing。

### 13.1 問題（以 `origin/dev` 2af1e333d 核對）

- build 綁 `<env>-build` environment，image 推到 `${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${GCP_AR_REPO}`（`deploy-dev.yml` 505–618，`REPO_PATH` 在 510；handoff 在 620）。staging-build 的值是 `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev`。
- core-ops 只能以 staging 封存的 manifest 原樣晉升 production（`release_manifest.py` 509 的 `RELEASE_PROFILE_PROMOTIONS`）。
- `component_binding_errors`（`release_manifest.py` 2319）要求 handoff image 與 manifest 記錄的字串完全相同。
- `deploy_cloud_run_waji.sh` 以 `--image` 原樣部署（824、891、923、941、1102）。
- `infra/terraform/checks.tf` 要求 prod 的 API／Web image 以 `${region}-docker.pkg.dev/${var.project_id}/` 開頭。
- repo 內沒有任何跨專案 Artifact Registry 讀取授權。

結果：首次 production release 只能部署 staging 專案的 image，與上面的決策衝突，而且 production runtime 會依賴 staging 專案。

### 13.2 規則（已實作）

| 規則 | 實作 |
|---|---|
| 推導 promoted reference 的唯一位置 | `release_manifest.promoted_image_ref(image, registry)`：只把 repository 根（`<region>-docker.pkg.dev/<project>/<repository>`）換成目標 registry，image 名稱與 `sha256` digest 原樣保留。image 不是 Artifact Registry 的 `@sha256` reference，或 registry 形狀不對（例如變數沒解析出的 `-docker.pkg.dev//`），就回傳 `None`。image 名稱不改：staging 建置的 `oday-staging-api` 在 production registry 仍叫 `oday-staging-api` |
| admission 綁定 | `component_binding_errors(manifest, images, target_registry=...)`：handoff image 必須是 manifest 記錄的 reference，或它的 `promoted_image_ref`。digest 不同、image 名稱不同（含多一層路徑）、registry 是第三者、tag 而不是 digest，一律拒絕，lease 不消耗。manifest 本身與 lease 綁定的 digest 不變 |
| 只有 production 可以帶目標 registry | `IMAGE_PROMOTION_ENVIRONMENTS = ("production",)`。`check_runtime_admission.py --target-registry` 在其他環境會被拒絕（lease 不消耗）；workflow 的 admission step 只在 `production` 傳入，值由 production environment 的 `GCP_REGION`／`GCP_PROJECT_ID`／`GCP_AR_REPO` 組成，以 `--target-registry=VALUE` 傳遞，讓未解析的值也能被 verifier 讀到並拒絕 |
| promotion 步驟的位置 | `deploy-dev.yml` deploy job（只在 `production`）：WIF 驗證與 Cosign 安裝之後，先下載 build run 的 manifest artifact，再執行 `Promote the admitted images into the production registry`。它在 pre-switch blue-green 狀態擷取、Cloud SQL 備份與 `Deploy Cloud Run by immutable digest` 之前，沒有 `continue-on-error` |
| promotion 內容 | `delivery_toolchain/release/promote_release_images.py`：manifest 必須等於 admission 消耗 lease 的 `manifest_digest` 並記錄這次 release SHA；admission 收到的 handoff 以同一條 `component_binding_errors` 重新核對；四個可部署 component（`migration` 與 worker 共用 image，隨 worker 一起複製）各自以 `cosign copy <manifest image> <production repo>:release-<sha>`（不加 `--force`：內容相同就略過、不同就拒絕覆寫）複製 image、Cosign 簽章與 SBOM attestation。來源永遠是 manifest 記錄的 reference，不是 handoff |
| 讀回與驗簽 | 對 tag 與 `@sha256` reference 用 build 相同的 `gcloud artifacts docker images describe --format='value(image_summary.digest)'` 讀回，digest 必須逐一等於 manifest；`sha256-<hex>.sig`／`.att` 讀回的 digest 必須是 manifest `signature_refs`／`sbom_refs` 記錄的那一份（換成 production registry）；最後用既有的 `sign_images.sh verify` 在 production registry 上驗簽。沒有新增第二套簽章或 registry 機制 |
| 不移動既有 tag | production 已有 `release-<sha>` tag 但指向別的 digest 時拒絕，不複製。指向同一 digest（重跑）時 cosign 略過相同內容，照常讀回與驗簽 |
| deploy 只拿 promoted reference | deploy step 的 `API_IMAGE` 等改為 `steps.promote-images.outputs.* \|\| needs.build.outputs.* \|\| inputs.*`；promotion 只在成功時寫 output，dev 的值與 job 層 env 完全相同 |
| deploy 腳本的最後一道 | `deploy_cloud_run_waji.sh` 在 deploy-by-digest 時，若 `ODP_DEPLOY_ENV` 是 `prod`／`production`，每個 image 都必須以 `${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${GCP_AR_REPO}/` 開頭，否則在任何 `gcloud`／`docker` 呼叫前拒絕。這與 `checks.tf` 的 production 規則一致。dev 不受影響 |
| 證據 | promotion receipt（`production-image-promotion.json`，kind `release-image-promotion`）記錄每個 component 的來源、promoted reference、digest、簽章與 attestation reference，失敗時記錄原因；以 artifact `production-image-promotion-receipt-<sha>-<attempt>` 上傳 |

### 13.3 Fail closed

以下任一情況，promotion 以非零結束、不寫 output，job 停在 Cloud Run 變更之前：

- 環境不是 production；目標 registry 形狀不對（含變數未解析）
- manifest digest 或 candidate SHA 與 admission 不符
- handoff 的 digest 或 image 名稱與 manifest 不符；handoff 的 component 集合與 manifest 可部署的 component 不同；共用 image 的 component 記錄了另一個 image
- `cosign copy` 失敗；tag 或 digest 讀回不等於 manifest digest
- production 的簽章或 attestation 讀不到，或 digest 不是 manifest 記錄的那一份
- `sign_images.sh verify` 失敗
- production 的 release tag 已指向別的 digest

admission 端的拒絕（handoff 不是 manifest image 也不是它的 promoted copy、目標 registry 形狀不對、非 production 帶目標 registry）都在 lease 消耗前發生，lease 保持 `issued`。

### 13.4 「staging digest == production digest」斷言的核對

task 要求把 `deploy_cloud_run_waji.sh` 與 `staging_closeout` 的這個斷言改為比 digest、不比 registry 路徑。以 `origin/dev` 核對的結果：

- `staging_closeout` 的 `Read staging recovery bundle with staging identity`（`deploy-dev.yml` 1955）比的是 staging tfvars 的 `manifest_digest` 與 production admission 的 `MANIFEST_DIGEST`。這是 manifest digest，裡面沒有 registry 路徑；manifest 又封存了四個 component image digest。promotion 不改寫 manifest，所以這個斷言照常成立，沒有修改，也沒有放寬。
- `deploy_cloud_run_waji.sh` 沒有比對 staging 與 production image 的斷言。它只檢查 reference 是 `@sha256` 形式；本 task 在這裡加上「production 只部署自己 registry 的 image」（§13.2）。
- 「production 部署的 digest 等於 staging 演練的 digest」由兩處保證：admission 只接受 manifest image 或同 digest 的 promoted copy；promotion 讀回的 digest 必須逐一等於 manifest。兩處都比 digest 與 image 名稱，沒有任何地方只比名稱。

### 13.5 Human/Ops 前置（本 task 只記錄，不執行）

確切指令與檢查方式在 `docs/evidence/runtime/ODP-PROD-IMAGE-PROMOTION-001/README.md`：

- production deployer（`github-deployer@odayplus-prod-20260826.iam.gserviceaccount.com`）對 staging AR repository `oday-plus-dev`（`odayplus-runtime-20260825`，`asia-east1`）的 `roles/artifactregistry.reader`
- 同一個身分對 production AR repository `oday-plus`（`odayplus-prod-20260826`，`asia-east1`）的 `roles/artifactregistry.writer`
- production AR repository `oday-plus` 必須存在

未完成前，production 部署會在 promotion 步驟失敗（讀不到 staging image 或寫不進 production），Cloud Run 不被變更。lease 在 admission 已消耗；這與其他 deploy job 失敗相同，重跑需要新的 lease。

### 13.6 測試

- `tests/release/test_first_release_staging_production.py`：`promoted_image_ref` 的形狀；production 接受 manifest image 或 promoted copy 並消耗 lease；十種錯誤 handoff 或 registry 被拒絕且 lease 未消耗；staging 帶目標 registry 被拒絕；staging 照舊只收 manifest image；mutation：只比 digest 時錯名與第三者 registry 會被 admit、拿掉環境限制時 staging 會 admit promoted image。
- `tests/contract/test_runtime_release_workflow.py`：以 cosign／gcloud spy 實跑 workflow 的 promotion step：成功時從 manifest 來源複製、在 production 驗簽、output 是 promoted reference；重跑同一 tag 可通過；十三種失敗都不寫 output；共用 image 的 component 不會漏複製；步驟順序在 admission 之後、任何 production 變更之前；deploy step 對 production 用 promoted reference、對 dev 與原本相同；admission 只對 production 傳目標 registry。
- `tests/ops/test_deploy_workflow_contract.py`：deploy 腳本在 production 拒絕 staging registry 與相似前綴的 repository，dev 不變。

本 task 沒有部署、沒有觸發 workflow、沒有修改 `RELEASE_GATE_REGISTRY`、IAM 或 secret。live 的 promotion 尚未執行過；第一次 production release 時才會實際跑。
