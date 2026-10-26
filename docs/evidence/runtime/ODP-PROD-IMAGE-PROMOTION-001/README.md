# ODP-PROD-IMAGE-PROMOTION-001：production 部署前把 image 以相同 digest 複製到 production registry

Owner: Claude · Reviewer: Human/Ops · 設計：`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md` §13

## 決策

> 決策依據：2026-10-24 負責人蔡尚志在『production 跨專案拉 image』兩案中選 (b)：把 image 複製到 production 自己的 registry，不讓 production runtime 依賴 staging 專案（staging 專案 odayplus-runtime-20260825 曾因 BILLING_ISSUE 暫停）。

## 交付內容

| 檔案 | 改了什麼 |
|---|---|
| `delivery_toolchain/release/release_manifest.py` | `promoted_image_ref`（唯一推導點：只換 repository 根，image 名稱與 digest 不變）、`IMAGE_PROMOTION_ENVIRONMENTS = ("production",)`、`component_binding_errors(..., target_registry=)` |
| `delivery_toolchain/release/check_runtime_admission.py` | `--target-registry`；只有 production admission 可帶，其他環境在 lease 消耗前拒絕 |
| `delivery_toolchain/release/promote_release_images.py` | production promotion：核對 manifest digest 與 handoff → `cosign copy` → 讀回 digest、簽章與 attestation → `sign_images.sh verify` → receipt 與 output |
| `.github/workflows/deploy-dev.yml` | admission 只對 production 傳 `--target-registry=`；deploy job（只在 production）在 Cosign 安裝之後、blue-green 擷取、Cloud SQL 備份與 Cloud Run 部署之前下載 manifest 並執行 promotion，上傳 receipt；deploy step 的 image 改用 promotion output（dev 不變） |
| `product_ops/deployment/deploy_cloud_run_waji.sh` | production 只部署 `${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${GCP_AR_REPO}/` 底下的 image |
| `docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md` | §13（逐字決策、規則、fail closed、斷言核對、前置、測試），§3.2 與 §7（F6） |
| `tests/release/test_first_release_staging_production.py`、`tests/contract/test_runtime_release_workflow.py`、`tests/ops/test_deploy_workflow_contract.py` | 正向、負向、mutation 與 workflow 實跑測試 |

## 「staging digest == production digest」斷言

- `staging_closeout` 比的是 staging tfvars 的 `manifest_digest` 與 production 的 `MANIFEST_DIGEST`，不含 registry 路徑。manifest 沒有改寫，所以這個斷言照常成立，沒有修改。
- `deploy_cloud_run_waji.sh` 在 `origin/dev`（2af1e333d）沒有比對 staging 與 production image 的斷言；本 task 加上「production 只部署自己 registry 的 image」。
- 部署的 digest 等於 manifest 的 digest，由 admission（manifest image 或同名同 digest 的 promoted copy）與 promotion 讀回（逐一等於 manifest digest）保證，兩處都同時比 image 名稱與 digest。

## Human/Ops 前置（本 task 只寫，不執行）

以下值是 2026-10-25 以 `gh api repos/alfloop-dev/odayplus/environments/<env>/variables` 讀到的 GitHub environment 變數：

| 項目 | staging（manifest 來源） | production（promotion 目標） |
|---|---|---|
| `GCP_PROJECT_ID` | `odayplus-runtime-20260825` | `odayplus-prod-20260826` |
| `GCP_REGION` | `asia-east1` | `asia-east1` |
| `GCP_AR_REPO` | `oday-plus-dev` | `oday-plus` |
| `GCP_SERVICE_ACCOUNT` | `github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com` | `github-deployer@odayplus-prod-20260826.iam.gserviceaccount.com` |

promotion 由 production deploy job 的身分（production `GCP_SERVICE_ACCOUNT`）執行，需要以下三項。先用唯讀指令確認，缺的才補：

1. production AR repository 存在

   ```bash
   gcloud artifacts repositories describe oday-plus \
     --project=odayplus-prod-20260826 --location=asia-east1 \
     --format='value(name,format,dockerConfig.immutableTags)'
   # 不存在時：
   gcloud artifacts repositories create oday-plus \
     --project=odayplus-prod-20260826 --location=asia-east1 \
     --repository-format=docker
   ```

2. production deployer 對 production repository 的 writer（`production-build` 用同一個身分推到同一個 repository，可能已具備；以 policy 確認）

   ```bash
   gcloud artifacts repositories get-iam-policy oday-plus \
     --project=odayplus-prod-20260826 --location=asia-east1 --format=json
   gcloud projects get-iam-policy odayplus-prod-20260826 --format=json \
     --flatten='bindings[].members' \
     --filter='bindings.members:serviceAccount:github-deployer@odayplus-prod-20260826.iam.gserviceaccount.com'
   # 缺少時：
   gcloud artifacts repositories add-iam-policy-binding oday-plus \
     --project=odayplus-prod-20260826 --location=asia-east1 \
     --member='serviceAccount:github-deployer@odayplus-prod-20260826.iam.gserviceaccount.com' \
     --role='roles/artifactregistry.writer'
   ```

3. production deployer 對 staging repository 的 reader（跨專案，目前 repo 內沒有任何這類授權）

   ```bash
   gcloud artifacts repositories get-iam-policy oday-plus-dev \
     --project=odayplus-runtime-20260825 --location=asia-east1 --format=json
   # 缺少時：
   gcloud artifacts repositories add-iam-policy-binding oday-plus-dev \
     --project=odayplus-runtime-20260825 --location=asia-east1 \
     --member='serviceAccount:github-deployer@odayplus-prod-20260826.iam.gserviceaccount.com' \
     --role='roles/artifactregistry.reader'
   ```

注意：

- 複製只在 promotion 當下讀 staging；之後 production runtime 只讀自己的 repository。但 promotion 那一刻 staging 專案必須是 ACTIVE（billing 有效），否則 promotion 失敗、Cloud Run 不被變更。
- production Cloud Run 從同專案的 repository 拉 image，一般由 Cloud Run service agent 的預設權限涵蓋；若 production 專案曾移除預設授權，需另行確認。
- 若 `dockerConfig.immutableTags` 為 true：第一次複製不受影響；重跑時 cosign 對相同內容會略過、不重寫 tag。本 task 沒有以實機驗證 immutable tag 下的行為。
- 這三項未完成前，production 部署在 promotion 步驟失敗（讀不到 staging image 或寫不進 production），Cloud Run 不被變更。admission 已在前一個 job 消耗 lease；這與其他 deploy job 失敗相同，重跑需要新的 lease。

## 驗證

離線測試（cosign／gcloud 都是 spy），不是 live 證據：

- `uv run --frozen pytest -q tests/release/test_release_profile.py tests/release/test_first_release_staging_production.py tests/contract/test_runtime_release_workflow.py tests/ops/test_deploy_workflow_contract.py`
- `uv run --frozen ruff check delivery_toolchain product_ops tests/release tests/ops tests/contract`

結果與 receipt 見 PR 說明與 task verification receipt。

## 尚未整合、未驗證、未上線

- 沒有部署、沒有觸發 workflow、沒有修改 `RELEASE_GATE_REGISTRY`、IAM 或 secret。
- promotion 從未在真實 registry 上執行。`cosign copy` 的行為依 cosign v2.5.2（`sigstore/cosign-installer@v3` 的預設版本）原始碼：不加 `--force` 時，目的地內容相同就略過、不同就拒絕覆寫；簽章與 attestation 沿用 `sha256-<hex>.sig`／`.att` tag。本機沒有 cosign，這部分只以 spy 測試。
- 上面三項 Human/Ops 前置尚未確認或設定。
