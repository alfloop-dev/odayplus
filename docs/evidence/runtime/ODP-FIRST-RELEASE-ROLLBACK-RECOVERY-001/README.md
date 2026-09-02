# ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001

定義並實作首次 dev release 的 fail-closed recovery admission：修正 Runtime Release
build-once handoff 對「target 上還沒有任何已核准 release」時無條件要求上一核准
rollback release 的語意缺口。

- 任務狀態：實作完成，等待正式 review submission
- Owner：Claude2 · Reviewer：Codex
- 量測 code head：`579d6ceb2e88608381d7a2e0c0102aea8ed7e4b0`（本 branch 最後一個含
  code 變更的 commit；其後只有 evidence commit，僅新增本目錄）
- base：`origin/dev@38de35a7bac2d6c6f6d8d079ffaf16abf6163c29`，量測時
  `git rev-list --count HEAD..origin/dev` = 0，未落後、無需 base advance merge

## 1. 問題

`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN` §8.4 的整套回滾程序都假設 target 上
已經有一個可以切回去的已核准版本。release manifest schema v2 把這個假設寫成無
條件要求：

```python
REQUIRED_FIELDS_V2 = REQUIRED_FIELDS_V1 + ("rollback_release",)
```

`build_release_handoff.build_handoff()` 也照著它 fail closed：

```python
if resolved_rollback_release is None:
    errors.append("缺少 rollback release 參照；build 階段必須綁定上一核准 release 與 snapshot pointer。")
```

**首次部署到某個 target 時沒有上一核准 release 可綁**，所以：

- build 階段永遠寫不出 handoff → 沒有 `manifest_digest` → Supervisor 簽不出
  lease → admission 沒東西可驗 → 那個環境連第一次部署都做不到；
- 唯一「繞過去」的方法都比死結更糟：偽造一份 rollback manifest（用假證據換放行）、
  為了首次部署把要求對**所有** release 放寬、或另開一條略過 admission 的
  bootstrap workflow（用移除閘門的方式移除死結）。

既有測試 `test_missing_rollback_release_refuses_to_write_a_handoff` 就是這個死結
本身，它現在仍然通過——沒有讀回證據的 release 還是被擋住。

## 2. 修法

不放寬要求，而是換一種**等價強度、且是讀回而非宣告**的證據。manifest 新增
`initial_release_recovery`，是 `rollback_release` 的**唯一替代分支**，兩者互斥：

```json
{
  "kind": "initial-release-recovery",
  "target_environment": "dev",
  "recovery_method": "delete-candidate-zero-traffic",
  "recovery_actions": [
    "delete-candidate-cloud-run-services",
    "delete-candidate-cloud-run-jobs",
    "delete-candidate-scheduler-triggers",
    "hold-zero-traffic"
  ],
  "rollback_target_available": false,
  "prior_release_absent": true,
  "absence_readback": {
    "kind": "runtime-release-target-absence-readback",
    "target_environment": "dev",
    "project": "<GCP_PROJECT_ID>",
    "region": "<GCP_REGION>",
    "probe_command": "gcloud run services list --format=value(metadata.name); gcloud run jobs list --format=value(metadata.name)",
    "targets": [
      {"component": "api", "resource_kind": "cloud-run-service",
       "resource_name": "<ODP_CLOUD_RUN_API_SERVICE>",
       "exists": false, "serving_traffic": false}
    ]
  },
  "binding_digest": "sha256:<candidate + component digests + target env + recovery method>"
}
```

它之所以可被採信，是因為每一項都被綁住或被推導，沒有一項可以手填：

| 性質 | 實作 |
| --- | --- |
| 證據是讀回來的 | `probe_release_target_absence.py` 逐一問 `gcloud run services/jobs list`，五個部署 target 全部不存在才寫 receipt |
| 讀不到 ≠ 不存在 | `gcloud` 非零 exit code 一律 raise。「查不到」與「查不動」是兩件事，把後者讀成前者正好會在權限壞掉時放行假的 initial release |
| 不能被搬走 | `binding_digest` 綁 candidate SHA、component image digest、target environment、recovery method |
| 不能被延用 | rebuild 後 image digest 改變 → binding 失效 |
| 不能跨環境重放 | `validate_release_admission(manifest, environment=...)` 比對 `target_environment` |
| 只有 dev 適用 | `INITIAL_RELEASE_ELIGIBLE_ENVIRONMENTS = ("dev",)`。staging 每個 release 重建、production 是被 promote 進去的 |
| 啟用來源不適用 | `external_sources_expected_enabled` 非空時直接拒絕，維持 masked snapshot + rollback 要求 |
| schema v1 不適用 | v1 沒有可綁定的 initial-release admission |
| 無法夾帶未綁定欄位 | readback 與記錄本身都拒絕 binding digest 未涵蓋的多餘鍵 |
| 無 dispatch 通道 | workflow 只有一個 boolean input 選擇分支；readback 內容、posture、binding digest 都沒有 input 也沒有 `vars.*` fallback |
| deploy 前重驗 | admission 在**消費 lease 之前**無條件重讀 target；一般 release 的 manifest 直接 no-op |

### 2.1 recovery 是什麼，以及不是什麼

首次 release 沒有更早的版本，所以「回滾」會指向一個從未存在過的版本。記錄裡因此
明寫 `rollback_target_available: false` 與
`recovery_method: "delete-candidate-zero-traffic"`，動作是：刪除本次建立的候選
service／job／scheduler trigger，維持零流量。

runtime 端本來就做對了——`restore_service_traffic()` 在 pre-deploy snapshot 為
absent 時是刪除 bootstrap candidate，不是還原流量。壞的是**它怎麼說**：失敗路徑
無論如何都印 `Deployment failed; restoring the recorded API/Web traffic split.`，
而那正是 operator 讀 log 判斷「舊版本回來了沒」的那一刻。新增
`release_recovery_mode()` 依實際捕捉到的 snapshot 決定訊息，沒有可回滾的既有版本
時明講沒有。

### 2.2 這條分支會自己關上

第二個 release 時 target 上已經有第一個 release，讀回就不是空的，這條分支拒絕，
只剩既有的 `rollback_release` 綁定。而第一個 release 本身是一份完整的已核准
manifest，可以直接被第二個 release 當成 rollback target
（`validate_rollback_manifest()` 回傳空清單）。所以這是 bootstrap，不是常設豁免。

## 3. 改了哪些檔案

| 檔案 | 變更 |
| --- | --- |
| `delivery_toolchain/release/release_manifest.py` | 新增 `initial_release_recovery` 的常數、建構、驗證；`validate_manifest` 與 `validate_release_admission` 把它接成 `rollback_release` 的唯一替代分支；`validate_release_admission` 新增 `environment=` 參數 |
| `delivery_toolchain/release/probe_release_target_absence.py` | 新增。build 模式產生 target 讀回 receipt；admission 模式在 lease 消費前重驗 |
| `delivery_toolchain/release/build_release_handoff.py` | 新增 `--initial-release-readback` 與 `--target-environment`；與 rollback manifest 互斥；只在讀回為空、target 合格、無啟用來源時採用 |
| `.github/workflows/deploy-dev.yml` | 新增 `initial_release_recovery` boolean input；build 讀回並上傳 receipt；admission 無條件重讀並上傳 receipt |
| `product_ops/deployment/deploy_cloud_run_waji.sh` | 失敗路徑依實際 snapshot 說明 recovery |
| `product_ops/deployment/cloud_run_release_traffic.sh` | 新增 `release_recovery_mode()` |
| `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` | 新增 §8.5 首次 release 的 recovery |
| `docs/audits/code-boundary-inventory.csv` | 兩個新檔案的分類 |

沒有新增第二條 workflow、第二個 admission job，或第二次 lease 檢查——
`test_the_first_release_branch_adds_no_second_admission_path` 就是在守這件事。

## 4. 驗證

完整逐命令結果見 `verification-receipt.json`。摘要：

| 項目 | 結果 |
| --- | --- |
| `pytest tests/release/ tests/ops/test_deploy_workflow_contract.py tests/ops/test_cloud_run_live_deployment.py tests/ops/test_runtime_config_code_closeout.py` | exit 0，830 tests |
| `pytest -m "not requires_live_env" delivery_toolchain scripts tests/tooling` | exit 0 |
| `pytest tests/ops`（完整 820 tests） | exit 0 |
| `pytest tests/architecture/test_external_data_boundary.py` | exit 0 |
| `ruff check .orchestrator delivery_toolchain scripts` | exit 0 |
| `ruff check tests modules apps shared models solver pipelines infra` | exit 0 |
| `check_code_boundaries.py` | exit 0，1047 files |
| `secret_scan.py` | exit 0 |
| `bash -n`（兩個 deploy shell） | exit 0 |

### 4.1 mutation 檢查

綠燈本身不代表測到了缺陷路徑，所以對三個最關鍵的不變量各做一次反向驗證：

| 移除的保護 | 抓到的測試 |
| --- | --- |
| `initial_release_recovery_errors` 不再重算 `binding_digest` | `test_a_first_release_admission_lifted_onto_another_candidate_fails_closed` |
| admission 的重讀步驟改成 `if: ${{ inputs.initial_release_recovery }}` | `test_admission_re_reads_the_target_before_the_lease_is_consumed` |
| probe 把 `gcloud` 非零 exit code 當成「不存在」 | `test_a_gcloud_that_cannot_look_is_not_a_target_that_is_empty` |

三次都由新測試失敗擋下，改動已還原。

## 5. 沒有做的事

- **沒有在真實 GCP target 上跑過讀回。** 本機沒有也不應該有 dev 環境的憑證，
  probe 的覆蓋是用 fake `gcloud`（成功但空輸出＝不存在、非零 exit＝無法斷定、
  多筆輸出＝不唯一）。真正的首次部署仍需一次 dispatch 才能證明端到端。
- **沒有動 `check_release_environment.py` 的 build scope 必要變數。**
  `ODP_CLOUD_RUN_MIGRATION_JOB` 只有這條分支需要，改成全域必要會讓沒綁該變數的
  環境連一般 build 都跑不了。現在是沒綁就只擋這一步，訊息明講哪個 target 沒名字。
- **沒有把 `sources_off_attestation` 與這條分支耦合。** 首次 release 仍要自己
  滿足 sources-off posture 或 masked snapshot 的既有要求，兩者是獨立的資料面／
  回復面證據。
