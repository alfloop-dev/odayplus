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
   - 憑證：這是受保護的讀取，與 §12 的 live gate 使用相同的分工。`Authorization` 帶 release smoke 身分的 token（audience 為 `urn:odp:staging-smoke:<release>`，API 只透過 `ODP_AUTH_SERVICE_AUDIENCES` 與 principal map 信任它，`model_owner` 角色具 `model` VIEW）。API audience 的 token 只放在 `X-Serverless-Authorization`，當 Cloud Run 傳輸憑證。若把 API audience token 當 application bearer，API 會以 `audience_mismatch` 拒絕，`full` 或 registry 已綁定的 core-ops 即使模型健康也會失敗。`/readiness`、`/platform/*` 是公開路徑，應用層不驗 bearer，維持只在 `Authorization` 帶 API audience token 作為 Cloud Run 傳輸憑證。
4. **完整 live gate（F4，已由 ODP-CORE-OPS-STAGING-LIVE-GATE-001 實作）。** staging verify 新增最後一個 stage `live_e2e_gate`，執行**完整的** `check_live_e2e_gate.py`（persistence、provider、auth、Web 受保護路由、模型、worker、audit、資料來源）。smoke 身分、worker driver 與 provider posture 的設定來源見 §12。上面的模型最低檢查保留在 `api_web_authenticated_smoke`，兩者不衝突。

## 5. Rollback 綁定盤點（首次 staging/production release）

- `INITIAL_RELEASE_ELIGIBLE_ENVIRONMENTS = ("dev",)`（`release_manifest.py`）。`initial_release_recovery` 只允許 dev。staging 與 production 的首次 release 必須以 `rollback_release` 綁定一份先前可 admission 的 manifest（workflow 的 `rollback_manifest` 或 `vars.ODP_PREVIOUS_RELEASE_MANIFEST_PATH`）。
- `validate_rollback_manifest` 驗證 rollback 目標時不帶 environment。實務上可以用更早 SHA、已在 dev admitted 的 manifest 當 rollback 目標，但帶 `initial_release_recovery(dev)` 的 manifest 永遠不能進 staging 或 production。
- core-ops 晉升 production 時，production 使用的是 staging 封存 manifest 中同一個 `rollback_release`。這和 `full` 的共用 manifest 設計相同，並沒有變弱。但 production 從未有過 approved release，這個 rollback 目標也就從未在 production 執行過。
- staging 演練中的 `rollback_rehearsal` 需要 ephemeral service 上已有非零流量，這是既有條件，沒有改變。
- `.orchestrator/release_lease.py` 指出 production 目前由 gate stage 拒絕：沒有任何 release 到達 `staging-verified`，也還沒有 production blue-green 路徑。

**結論：** 現況是 fail closed。首次 staging/production release 在沒有合法 rollback 綁定時會被 admission 拒絕，本 task 沒有放寬。缺的是能力，不是安全漏洞，所以本 task 沒有以「最小修正」的名義開放 staging/production 的 `initial_release_recovery`。列為後續項 F1。

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
| F1 `ODP-CORE-OPS-FIRST-RELEASE-ROLLBACK-BINDING` | 首次 staging/production release 的 rollback 綁定：決定是開放 `initial_release_recovery` 給 staging/production（含 absence readback 與 binding digest），還是以已 admitted 的前一個 dev manifest 作為 rollback 目標的明確政策 | Release owner + Human/Ops |
| F2 `ODP-CORE-OPS-PROD-TERRAFORM-MODEL-CONTRACT` | `terraform_data.production_contract` 的模型 precondition 與 core-ops production 並存的方式（不得填假值；可能需要 profile-aware 的契約，且仍 fail closed） | Infra owner + ML Risk Owner |
| F3 gate-3 | Model and Solver Gate 的 passed-with-deviation 由 Human/Ops 具名簽署（見 §8 草稿），或維持 blocked | Product Owner + ML Risk Owner |
| F4 `ODP-CORE-OPS-STAGING-LIVE-GATE` | 在 staging 跑完整的 `check_live_e2e_gate.py`。**程式已由 ODP-CORE-OPS-STAGING-LIVE-GATE-001 實作（見 §12）**；實際 live 執行仍需 §12.5 的 Human/Ops 前置條件 | Release owner |
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
