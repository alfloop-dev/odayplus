# ODP-PROD-DEPLOY-SAFETY-001：production 部署路徑安全修正

- base：`origin/dev`；branch：`task/ODP-PROD-DEPLOY-SAFETY-001`
- 依據：`task/ODP-CORE-OPS-PROD-READINESS-PACK-001` 的 `rollback-plan.md` §3、`deployment-plan.md` §2 步驟 17／19 與 §5、`backup-dr-impact.md` §6
- 本 task **沒有**觸發任何 workflow、沒有呼叫 gcloud 或任何 GCP API、沒有開 PR，也沒有修改 `docs/evidence/gates/RELEASE_GATE_REGISTRY.json`。以下所有「雲端行為」都只經過假 `gcloud` 的本機測試，**未在 production 實際執行過**。

§2／§3 的行號以 base advance 之前的 commit `2d9648a26` 為準。base advance（§5）之後，`.github/workflows/deploy-dev.yml` 中本 task 的每一處行號都**往後移 36 行**（例如 `Capture pre-switch production blue-green state` 從 `:1426` 移到 `:1462`，`staging_closeout` 的 `id: watch-receipt` 從 `:1935` 移到 `:1971`）；`bluegreen_release.py` 與測試檔的行號不變。

## 1. 三項缺陷的驗證結果

| # | 稽核發現 | 驗證結果 | 依據（`origin/dev` 原始程式） |
|---|---|---|---|
| 1 | blue-green `capture-state` 在切換**之後**執行，輸出只在 `RUNNER_TEMP`，沒有上傳 | **成立，而且比稽核寫的更嚴重**。另外發現：舊 step 把 `--project`／`--region` 放在 `capture-state` 子命令之後，但這兩個是頂層參數，argparse 會以 exit 2 拒絕（本機重現：`python3 product_ops/deployment/bluegreen_release.py capture-state ... --project p --region r` → `error: the following arguments are required: --project, --region`）。所以舊 step 在每次 production 部署切換完成後**必定失敗**，讓已經 commit 的 release 顯示為失敗，並使 `staging_closeout` 被跳過 | 舊 `deploy-dev.yml` 的 `Verify production blue-green deployment state`（在 `Deploy Cloud Run by immutable digest` 之後）；`bluegreen_release.py` 的 `_build_parser` |
| 1a | 狀態檔本身能否給 rollback 使用 | **另有兩個缺口**：(a) `release-state.json` 記錄的是絕對路徑（runner 上的 `RUNNER_TEMP`），下載到別處後 `rollback` 找不到 snapshot；(b) snapshot 是完整的 `gcloud run services describe` 輸出，含 env block 與 secret selector，workflow 與 `release_receipts.py` 都明訂這類 dump 不得離開 runner，所以不能直接上傳 | 舊 `bluegreen_release.py` `main()` 的 `capture-state`／`rollback` 分支；`deploy-dev.yml` validation upload 的註解；`delivery_toolchain/release/release_receipts.py:10-13` |
| 1b | 首次 production release 沒有 blue | 舊 `capture_traffic_snapshot` 直接 `describe`，service 不存在時會失敗。若只是把 step 移到切換前而不處理，首次 production release 會被擋下 | 舊 `bluegreen_release.py` `capture_traffic_snapshot` |
| 2 | `staging_closeout` 在首次 production release 後幾乎一定失敗 | **成立，但不限首次 release**。job 在 deploy 成功後立即開始（只受 `staging` environment 核准影響），它要求 `ODP_PRODUCTION_WATCH_CLOSEOUT_URI` 已有一份綁定本 SHA、`WATCH_PASSED`、`end_time - start_time >= 900s`、而且有 provider 簽章 readback 的 receipt（`shared/observability/watch_window.py` `verify_watch_window_receipt`）。這份 receipt 要觀察切換後至少 15 分鐘，並由 workflow 之外的 watch owner 產生；repo 內唯一呼叫 `record_deployment_watch_window_status` 的是 fail-closed 負向檢查（`validate_cloud_run_live_deployment.py`）。所以 job 開始時物件不存在，`gcloud storage cp` 失敗，整個 release run 變紅。除非核准人把 staging 核准壓到 watch 結束、而且有人在 workflow 外產生 receipt，否則每一次 release 都會這樣。**這仍是依程式推論，未實際觀察** | 舊 `deploy-dev.yml` `staging_closeout` 的 `Read and verify production watch receipt with staging identity` |
| 3 | production 部署前沒有 Cloud SQL 備份 | **成立**。production 路徑從 auth 直接進 `deploy_cloud_run_waji.sh`，腳本在切換前先跑 migration job（`deploy_cloud_run_waji.sh:822-877`）；任何 rollback 都不回復 schema。workflow 與腳本都沒有 `gcloud sql backups create`、export 或 PITR 讀回 | `deploy_cloud_run_waji.sh:813-877`；舊 `deploy-dev.yml` deploy job |

## 2. 修改內容

### 2.1 缺陷 1：切換前保存 blue 狀態並上傳

`.github/workflows/deploy-dev.yml`

- `:1416-1454` 新增 `Capture pre-switch production blue-green state`（只限 production，id `prod-bluegreen-state`），位置在 `Deploy Cloud Run by immutable digest`（`:1557`）之前，也就是在部署腳本的 migration 與切換之前：
  - 參數順序改正（`--project`／`--region` 在子命令之前）。
  - 輸出寫到 `${RELEASE_RECEIPT_DIR}/production-bluegreen-state`，不再用 `RUNNER_TEMP`。
  - 用既有的 `cloud_run_traffic.py exists`／`restore-arg` 檢查：API 與 Web 必須同時存在或同時不存在；存在時流量必須能還原（active revision 合計 100%）。任何一項不成立就 fail，deploy 不會開始。
  - 確認 `release-state.json` 的 `release_id` 綁定本 SHA，snapshot 路徑是相對路徑。
  - 輸出 `previous_release_present=true|false`。
- `:1456-1463` 新增 `Publish pre-switch production blue-green state`：artifact 名稱 `production-bluegreen-prerelease-state-<release_sha>-<run_attempt>`，`retention-days: 90`（與既有 `deployed-release-manifest-dev` 相同），`if-no-files-found: error`。上傳在切換**之前**完成，即使 runner 在部署中途消失，狀態也已保存。
- 刪除切換後的舊 `Verify production blue-green deployment state` step。

沒有新增儲存位置：repo 內 release run 的既有保存方式就是 workflow artifact（`release-phase-receipt-*`、`deployed-release-manifest-dev` 等），這裡沿用。

`product_ops/deployment/bluegreen_release.py`

- `:308-320` `_traffic_snapshot_contract()`：載入 `cloud_run_traffic.py`，沿用它的 `absent_snapshot`／`is_present`，不在這裡重複定義「service 不存在」的格式。
- `:323-441` `capture_traffic_snapshot`：
  - 先用與部署腳本 `capture_service_traffic`（`cloud_run_release_traffic.sh:9-34`）相同的 `services list --filter=metadata.name=` 判斷 service 是否存在。列表失敗、結果不明確時 fail；不存在時寫入 absent marker（首次 release）。
  - 存在時只保留 `status.url` 與 `status.traffic`，丟掉 env block 與 secret selector；沒有 `status.traffic` 時 fail。
- `:493-` `restore_traffic_from_snapshot`：snapshot 是 absent marker 時明確拒絕，說明這是首次 release、沒有前一版可以切回，不會呼叫 gcloud。
- `:1178`、`:1189`、`:1208` `capture-state` 改寫相對於 state 檔的路徑（`api-traffic.json` 等）。
- `:1243-1252` `rollback` 以 `--state-file` 所在目錄解析 snapshot 路徑（舊的絕對路徑仍可用）；state 沒有 API／Web snapshot 時 exit 1。

**rollback 如何取得這份 artifact（人工程序，未演練）：**

```bash
gh run download <production deploy run id> \
  -n production-bluegreen-prerelease-state-<release_sha>-<run_attempt> \
  -D ./prod-bluegreen-state
python3 product_ops/deployment/bluegreen_release.py \
  --project <prod project> --region <region> --dry-run \
  rollback --api-service <api> --web-service <web> \
  --state-file ./prod-bluegreen-state/release-state.json
```

- 確認 dry-run 的輸出後，再拿掉 `--dry-run` 執行。
- 一次 run 有多個 attempt 時，使用**實際完成切換的那個 attempt** 的 artifact。每個 attempt 都會在自己切換之前重新 capture。
- 首次 production release 的 artifact 會記錄兩個 service 都不存在，`rollback` 會拒絕執行。首次 release commit 之後要怎麼下線，仍是 `rollback-plan.md` §2.4 待人工決定的事項。

### 2.2 缺陷 2：`staging_closeout` 沒有 watch receipt 時延後，不再讓 release 失敗

`.github/workflows/deploy-dev.yml`

- `:1810-1811` job 新增 output `watch_verified`。
- `:1934-1953` `Read and verify production watch receipt with staging identity`（id `watch-receipt`）：
  - 先執行 `gcloud storage objects describe`。
  - 回應是 404／not found：發出 `::warning`、寫入 step summary、輸出 `verified=false`，exit 0。staging **保留，不刪除**。
  - 其他讀取錯誤（例如 403）：照舊 fail。
  - 物件存在：照舊 `cp` 後執行 `verify_watch_window_receipt`，任何問題都 fail，通過才輸出 `verified=true`。
- `:1955-1956` cleanup step 加上 `if: steps.watch-receipt.outputs.verified == 'true'`，沒有通過驗證的 receipt 就不會刪除 staging。
- `:1976` closeout receipt artifact 名稱加上 `-${{ github.run_attempt }}`，因為延後之後要**單獨重跑這個 job**。
- **digest 綁定沒有放寬。** `Read staging recovery bundle with staging identity`（`:1908`）不變，每個 attempt（包括延後的那次）都在 watch 檢查之前執行，並 assert staging tfvars 的 `manifest_digest == MANIFEST_DIGEST`（production 的 `needs.deploy.outputs.manifest_digest`）以及 `candidate_sha`。

**延後之後的程序：** watch owner 把 receipt 寫到 `ODP_PRODUCTION_WATCH_CLOSEOUT_URI` 之後，在同一個 run 中對 `Verify production watch and clean up ephemeral staging` 執行「Re-run job」（需要再次核准 `staging` environment）。GitHub 只保留 run 30 天內可重跑；staging lifecycle 的 TTL 規則見 `staging_lifecycle.py:61-62`。

**已知限制：** 判斷「不存在」的依據是 stderr 中的 `404`／`not found`。bucket 本身不存在也會回 404，因此會被當成「尚未寫入」而延後，不會刪除任何東西，但會一直延後下去。所以 `ODP_PRODUCTION_WATCH_CLOSEOUT_URI` 是否設定正確，需要人工確認（§4）。

### 2.3 缺陷 3：production migration 前先做 Cloud SQL 備份

`.github/workflows/deploy-dev.yml`

- `:1470-1546` 新增 `Take pre-migration production Cloud SQL backup`（只限 production，id `prod-sql-backup`），位置在 blue-green 狀態上傳之後、`Deploy Cloud Run by immutable digest`（migration 在這一步裡執行）之前：
  - 從 workflow 已有的 `GCP_CLOUD_SQL_INSTANCE`（`project:region:instance`）取出 instance 名稱。project 或 region 與 `GCP_PROJECT`／`GCP_REGION` 不符、格式不是三段、或沒有設定時，在呼叫 gcloud 之前就 fail。
  - `gcloud sql backups create --instance=<instance> --project=<project> --description=odp-predeploy-<sha12>-run-<run_id>-<attempt>`（同步等待完成）。
  - `gcloud sql backups list` 讀回，必須剛好有一筆 description 相同、`status == SUCCESSFUL`、`type == ON_DEMAND` 的備份，否則 fail，deploy 不會開始。
  - 寫入 `${RELEASE_RECEIPT_DIR}/production-predeploy-cloud-sql-backup.json`，內容包括 backup id、instance、release SHA、manifest digest、run id／attempt、時間；輸出 `backup_id`；寫入 step summary。
- `:1548-1555` `Publish production pre-migration Cloud SQL backup record`：artifact `production-predeploy-cloud-sql-backup-<release_sha>-<run_attempt>`，`retention-days: 90`。
- `:1183` deploy job output `production_backup_id`。
- dev 與 staging **不加**。dev auto-deploy 的行為沒有任何改變；staging 本來就有 release-scoped 資料庫與 `backup_restore_drill`。

## 3. 測試

`tests/ops/test_deploy_workflow_contract.py`（`:1489-1900`）。測試會用假 `gcloud`／`uv` 實際執行 workflow 中的 step shell，不是只比對字串：

- `test_production_pre_switch_state_is_captured_and_published_before_the_cutover`：capture → 上傳 → deploy 的順序；切換後沒有 `bluegreen_release.py`；參數順序；artifact 名稱、路徑、retention、`if-no-files-found: error`；沒有 `continue-on-error`／`always()`。
- `test_pre_switch_capture_step_writes_a_redacted_relocatable_rollback_input`：產出相對路徑，不含 env 值，只有讀取型 gcloud 呼叫。
- `test_pre_switch_capture_step_admits_a_first_release_with_no_blue`：首次 release 通過，`previous_release_present=false`。
- `test_pre_switch_capture_step_fails_closed`（負向，3 個案例）：只有一半 service 存在、流量無法還原、查詢錯誤。
- `test_production_backup_precedes_migrations_and_is_published`、`test_backup_is_production_only_and_dev_auto_deploy_is_unchanged`。
- `test_backup_step_records_the_confirmed_backup_id`。
- `test_backup_step_fails_closed_without_a_confirmed_backup`（負向，6 個案例）：create 失敗、備份 FAILED、備份 RUNNING、instance 屬於其他 project、只有 instance 名稱、未設定。綁定錯誤時不會呼叫 gcloud。
- `test_staging_closeout_defers_cleanup_until_a_watch_receipt_exists`：cleanup 的條件；bundle 與 digest 檢查在 watch 檢查之前而且沒有條件；artifact 名稱含 attempt。
- `test_staging_closeout_absent_watch_receipt_defers_without_deleting`、`test_staging_closeout_storage_errors_still_fail_closed`（403）、`test_staging_closeout_present_but_invalid_watch_receipt_fails`（SHA 不符）。

`tests/ops/test_bluegreen_release.py`：

- `test_capture_traffic_snapshot`：改寫，涵蓋列表失敗、結果不明確、describe 失敗、JSON 無效、缺 `status.traffic`；成功時確認 snapshot 不含 env／secret，而且 `cloud_run_traffic.py restore-arg` 可以使用。
- `test_capture_traffic_snapshot_records_a_first_release_as_absent`。
- `test_restore_refuses_an_absent_snapshot_instead_of_claiming_a_rollback`。
- `test_cli_capture_state`：補 assert 相對路徑。
- `test_cli_rollback_resolves_snapshots_next_to_the_downloaded_state_file`。
- `test_cli_rollback_refuses_a_state_without_traffic_snapshots`。

### 執行的指令與結果（Python 3.12，`UV_PYTHON=3.12`）

| 指令 | 結果 |
|---|---|
| `uv run --frozen pytest -q tests/contract/test_runtime_release_workflow.py tests/ops/test_deploy_workflow_contract.py` | exit 0；173 passed |
| `uv run --frozen pytest -q tests/ops/test_bluegreen_release.py tests/ops/test_workflow_expression_contexts.py tests/ops/test_first_release_no_traffic.py tests/ops/test_first_release_recovery.py tests/ops/test_release_receipts.py tests/ops/test_runtime_config_code_closeout.py tests/ops/test_cloud_run_live_deployment.py` | exit 0；560 passed |
| `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy-dev.yml'))"` | exit 0 |
| 對三個新增／修改的 step `run:` 區塊執行 `bash -n` | 三個都 exit 0 |
| `bash -n product_ops/deployment/deploy_cloud_run_waji.sh product_ops/deployment/cloud_run_release_traffic.sh`（本 task 沒有修改 shell 腳本，只做回歸確認） | exit 0 |
| `uv run --frozen ruff check product_ops/deployment/bluegreen_release.py tests/ops/test_bluegreen_release.py tests/ops/test_deploy_workflow_contract.py` | exit 0 |
| `git diff --check origin/dev...HEAD` | 無輸出（clean） |

沒有執行 `actionlint`：環境中沒有這個工具。

## 4. 需要人工處理的前提（本 task 沒有授權，也沒有執行）

1. **production deploy SA 的 Cloud SQL 備份權限。** production deploy job 以 `production` environment 的 `vars.GCP_SERVICE_ACCOUNT`（WIF）身分執行，需要在 production project 上擁有：
   - `cloudsql.backupRuns.create`、`cloudsql.backupRuns.get`、`cloudsql.backupRuns.list`
   - `cloudsql.instances.get`（gcloud 等待 operation 完成時需要）

   最小做法是建立含上述 4 個 permission 的 custom role。若使用預定義角色，則是 `roles/cloudsql.editor`；它的權限比需要的大，例如可以修改 instance 設定。**沒有授權。**
2. **確認 production Cloud SQL 能建立 on-demand backup。** 2026-09-16 讀回的 `oday-prod-sql` 沒有讀取 backup 設定（`backup-dr-impact.md` §3）。backup 正在進行時（例如自動備份時段），`backups create` 可能會失敗，這時本 step 會 fail closed，需要人工重跑。
3. **還原程序與演練。** 本修改只保證「migration 前有一份已確認成功的備份，而且 ID 有記錄」。要從這份備份還原（`gcloud sql backups restore` 會覆蓋 instance 或還原到新的 instance），仍需要依 `docs/runbooks/backup-and-restore.md` 由人工決定與演練。
4. **`ODP_PRODUCTION_WATCH_CLOSEOUT_URI`。** 必須在 `staging` environment 設定，而且要指向實際存在的 bucket（bucket 不存在會一直延後，見 §2.2）。staging closeout 身分需要該物件的 `storage.objects.get`。watch receipt 的**產生者**仍然不存在於 repo 中，需要 watch owner 依 `record_deployment_watch_window_status` 的要求，在 workflow 之外產生。
5. **延後之後重跑 closeout。** 由有 `staging` environment 核准權的人，在 watch receipt 寫入後、30 天內，重跑該 job。
6. **blue-green rollback 程序。** §2.1 的下載與 rollback 指令**未演練**。建議先在 staging 以 `--dry-run` 走一次。首次 production release commit 之後的下線方式，仍待 `rollback-plan.md` §2.4 的決策。
7. 本修改沒有任何一部分在 GitHub Actions 或 GCP 上實際執行過。第一次 production dispatch 就是第一次實際執行。

## 5. Base advance 到 `origin/dev`（2026-10-24）

- 以 merge（不是 rebase）把 `origin/dev` `4f89378ec`（PR #1591）併入本 branch，merge commit `4561da4fa`（第一親 `2d9648a26`、第二親 `4f89378ec`）。
- 合併前 `git merge-tree --write-tree` 預覽為乾淨合併、沒有衝突；合併後 `HEAD^{tree}` 與預覽 tree `0be1330c2` 逐位元相同，`origin/dev` 是 HEAD 的祖先。
- dev 這段期間對本 task 檔案的唯一修改是 `.github/workflows/deploy-dev.yml`（PR #1565，`ODP-CORE-OPS-FIRST-RELEASE-ROLLBACK-001`）：`initial_release_recovery` 的說明文字、build 與 deploy job 的 `probe_release_target_absence.py` 在 staging 改用 `--staging-release-id`／不帶 `--target`，以及 admission 在 staging 首次 release 時多讀 production lease store。這些都在本 task 的 step 之前（合併後的 `:146`、`:481-499`、`:967-985`、`:1041-1081`），與三項修正沒有重疊的行，也沒有改變本 task 依賴的行為：
  - 修正 (1)：以 `initial_release_recovery` 進行的 production 首次 release，dev 的修改沒有改變 production 的 absence probe（仍用長期 service 名稱，只有 staging 改用 release-scoped 名稱）；本 task 的 pre-switch capture 在那種情況下記錄兩個 service 都不存在，兩者一致。
  - 修正 (2)：`staging_closeout` job 本身沒有被 dev 修改；staging digest == production digest 的檢查不變。
  - 修正 (3)：dev 沒有在 production deploy job 加入任何 Cloud SQL 相關步驟，不需要取捨。
- 因此沒有任何「與 dev 衝突、以 dev 為準」的取捨。dev 同時新增的 `tests/release/test_first_release_staging_production.py` 與 `docs/evidence/runtime/ODP-CORE-OPS-PROD-READINESS-PACK-001/` 未被本 task 修改。
- 宣告的 verification 由 `task_verification.py run` 在最終 head 執行一次，receipt 在 supervisor evidence store；exit code 與耗時以送審後的 task note 公布，本文件不內嵌（內嵌會讓 head 前進、receipt 失效）。
