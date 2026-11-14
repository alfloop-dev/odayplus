# ODP-STAGING-ADMISSION-5948EBA26-001：staging admission 換綁到 5948eba26

本資料夾記錄 core-ops candidate `5948eba26c4c1e8d9bb1e617ddd46030ee451774` 進入 ephemeral staging 的 registry 綁定。這裡只有 evidence，還沒有 lease，也還沒有部署。

## 為什麼換 candidate

上一個綁定 `786db7baf`（PR #1702）已用掉 lease `HUMANOPS-STAGING-LEASE-20261114M`。它的 staging deploy [43566488597](https://github.com/alfloop-dev/odayplus/actions/runs/43566488597) 建立 staging web service 時失敗：Cloud Run 不接受 TCP liveness probe（`INVALID_ARGUMENT`）。ephemeral 資源已清除，PR #1708 拿掉了這個 probe。

同一段時間 dev 合併了 #1703、#1704、#1705、#1707。3e3c92ba0、41fb39a48、b633a5b3f 三次 dev 部署（[43596627522](https://github.com/alfloop-dev/odayplus/actions/runs/43596627522)、[43607733605](https://github.com/alfloop-dev/odayplus/actions/runs/43607733605)、[43615692416](https://github.com/alfloop-dev/odayplus/actions/runs/43615692416)）都卡在 live E2E 76/77，唯一失敗的是 `models:registry_refused`。原因是 dev registry 裡有 seed-dev-data 訓練登記的 2 個 forecast 版本：stage=dev、沒有 alias、沒有核准。

PR #1710 讓 dev target 只接受這種未 promote 的版本，staging 與 production 的規則不變。

這一輪派工沒有凍結，由負責人決定。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `5948eba26c4c1e8d9bb1e617ddd46030ee451774`（dev tip，PR #1710 合併） |
| staging build | Runtime Release run [43624182684](https://github.com/alfloop-dev/odayplus/actions/runs/43624182684)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（`manifest_digest` `sha256:6a32f75f…`）；components、SBOM、signature 一起換 |
| 最後一次實跑 7 個 product lane | `b633a5b3f`（#1707 合併）：push CI [43611776529](https://github.com/alfloop-dev/odayplus/actions/runs/43611776529) 與 merge group CI [43606885353](https://github.com/alfloop-dev/odayplus/actions/runs/43606885353)，7 個 lane 全部 success（`last-product-lanes-b633a5b3f.json`） |
| `b633a5b3f..5948eba26` | 只有 #1710：`delivery_toolchain/e2e/check_live_e2e_gate.py`、`tests/e2e/test_live_e2e_gate_dev_admin.py`，`classify_change_review_scope.py` 判為 `development_tooling` |
| candidate 本身的 CI | push CI [43620659160](https://github.com/alfloop-dev/odayplus/actions/runs/43620659160) 與 merge group CI [43619690890](https://github.com/alfloop-dev/odayplus/actions/runs/43619690890) 都是 success；7 個 product lane 依 change scope 跳過（不是在 5948eba26 實跑），實際執行的 job（change-scope、orchestrator、product）全部 success |
| `786db7baf..5948eba26` 的 PR | #1702（上一個 registry）、#1703、#1704、#1705、#1707、#1708、#1710 |
| dev 實際部署 | Runtime Release run [43621593504](https://github.com/alfloop-dev/odayplus/actions/runs/43621593504) 部署 5948eba26 本身，第一次就通過：live E2E 77 項全過，0 個 blocker（`models:registry_refused` 在 2 個未 promote 的 dev 版本下通過）；preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job、public-egress probe（預期 denied）都通過 |
| npm audit | staging build 的 production 相依 0 個 finding |
| target absence | staging build 讀回 5 個 `stg-odp-5948eba26c4c-32d697b8-*` 資源都不存在 |

## 人工決定

記錄檔為 `humanops-staging-admission-authorization-20261114.json`。負責人蔡尚志在 Claude Code 對話中先後選了兩個選項，由 Claude 轉錄，不是外部身分系統的簽章：

- 2026-11-14T16:23:02Z：「先解 registry 衝突，再跑鏈 N」。
- 2026-11-14T16:27:37Z：「窄幅放寬 dev gate」。

這兩個選項把 A–F 整條鏈套用到 #1710 合併後的 dev tip，核准編號用 N。另加三個條件，都已查核成立：

- 最後一個實跑 7 lane 全綠的 commit 到 candidate 之間，只能有 tooling scope 或 evidence 變更。
- candidate 的 CI 全綠。
- dev 部署 live E2E 77/77。

| 步驟 | 內容 |
|---|---|
| A | dispatch 這個 candidate 的 core-ops staging build |
| HD-08n | gate-2 Data Gate 標成 `passed-with-deviation`，只適用這個 candidate 進 staging，review_by 2026-11-27 |
| HD-09n | `release.stage=dev-verified`、`admission_target=staging`、`decision=go`，核准編號 `HUMANOPS-STAGING-ADMISSION-20261114N` |
| D | 以 Human/Ops 身分 approve 這個 registry PR |
| HD-10n | registry 合併後登記 lease `HUMANOPS-STAGING-LEASE-20261114N` |
| HD-12n | 只為這張 lease 觸發的 staging deploy run 核准 GitHub `staging` environment |

HD-08n 的三項偏差：

- source contract：不適用。core-ops 關閉所有外部來源，manifest 記錄 16 個來源全關、沒有任何憑證。
- point-in-time（model-ready data）：不適用。core-ops 不使用模型。
- freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。

HD-09n 只授權一次 staging release：一筆請求、一張 lease、一次 deploy，不包含 production。

每一步之前，dev tip 都必須仍是 5948eba26，ancestry 也必須為空；E、F 兩步則允許 tip 多出這個只含 evidence 的 registry 合併。任一條件不符就停下來重問。

B、C 的生效條件在 build 後查核，全部成立：

- build 成功。
- manifest 的 candidate 為 5948eba26、profile 為 core-ops。
- 16 個來源全關，且沒有憑證。
- npm audit 0 個 finding。
- 5 個 staging 資源都不存在。

綁定的 digest `sha256:6a32f75f…` 就是這次 build 的產物。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄上述 CI（lane 最後實跑在 b633a5b3f），維持 `passed`；786db7baf 的 receipt 移到 `historical_receipts`。
- gate-1、gate-4：維持 `blocked`，等負責人決定 HD-07。
- gate-3、5、6：原本就是 `blocked`，只更新 `release_sha`。

## 驗證

- `check_release_gate_registry.py --expected-sha 5948eba26…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=5948eba26…, expected_manifest_digest=sha256:6a32f75f…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`5948eba26..head` 只改 `docs/evidence/`。
