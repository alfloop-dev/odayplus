# ODP-STAGING-ADMISSION-60CF78695-001：staging admission 換綁到 60cf78695

本資料夾記錄 core-ops candidate `60cf786952c78e498372fa1ffb58d2c40860708c` 進入 ephemeral staging 的 registry 綁定。這裡只有 evidence，還沒有 lease，也還沒有部署。

## 為什麼換 candidate

上一個合併的綁定 `786db7baf`（PR #1702）已用掉 lease `HUMANOPS-STAGING-LEASE-20261114M`。它的 staging deploy [43566488597](https://github.com/alfloop-dev/odayplus/actions/runs/43566488597) 在建立 staging web service 時失敗：Cloud Run 不接受 TCP liveness probe（`INVALID_ARGUMENT`）。ephemeral 資源已清除，PR #1708 拿掉了這個 probe。

之後 dev 合併了 #1703、#1704、#1705、#1707。3e3c92ba0、41fb39a48、b633a5b3f 三次 dev 部署（[43596627522](https://github.com/alfloop-dev/odayplus/actions/runs/43596627522)、[43607733605](https://github.com/alfloop-dev/odayplus/actions/runs/43607733605)、[43615692416](https://github.com/alfloop-dev/odayplus/actions/runs/43615692416)）都在 live E2E 停在 76/77，唯一失敗的是 `models:registry_refused`。原因是 dev registry 裡有 seed-dev-data 訓練登記的 2 個 forecast 版本：stage=dev、沒有 alias、沒有核准。PR #1710 讓 dev target 只接受這種未 promote 的版本，staging 與 production 的規則不變。

下一個 candidate `5948eba26`（registry PR #1712）在 Human/Ops 核准前作廢：web 產品 PR #1711 合併進 dev。#1712 已關閉，沒有合併。

這一輪派工沒有凍結，由負責人決定。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `60cf786952c78e498372fa1ffb58d2c40860708c`（dev tip，PR #1711 合併） |
| staging build | Runtime Release run [43659963695](https://github.com/alfloop-dev/odayplus/actions/runs/43659963695)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（`manifest_digest` `sha256:62450c57…`）；components、SBOM、signature 一起換 |
| candidate 本身的 CI | push CI [43643491051](https://github.com/alfloop-dev/odayplus/actions/runs/43643491051) 與 merge group CI [43639788576](https://github.com/alfloop-dev/odayplus/actions/runs/43639788576) 都在 60cf78695 實跑 7 個 product lane，全部 success |
| `786db7baf..60cf78695` 的 PR | #1702（上一個 registry）、#1703、#1704、#1705、#1707、#1708、#1710、#1711 |
| dev 實際部署 | Runtime Release run [43648043409](https://github.com/alfloop-dev/odayplus/actions/runs/43648043409) 部署 60cf78695 本身，第一次就通過：live E2E 77 項全過，0 個 blocker（`models:registry_refused` 在 2 個未 promote 的 dev 版本下通過）；preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job、public-egress probe（預期 denied）都通過 |
| npm audit | staging build 的 production 相依 0 個 finding |
| target absence | staging build 讀回 5 個 `stg-odp-60cf786952c7-d1182418-*` 資源都不存在 |

## 人工決定

記錄檔為 `humanops-staging-admission-authorization-20261115.json`。負責人蔡尚志於 2026-11-15T00:41:20Z 在 Claude Code 對話中選擇「換到 60cf78695 跑鏈 O，可自動跟 tip」。這是 Claude 轉錄的對話回覆，不是外部身分系統的簽章。

這個選項的內容：

- 把 A–F 整條鏈套用到 60cf78695，核准編號用 O，其餘條件不變。
- 關閉 #1712，並 supersede 它的 task。
- 在 D 或 E 之前，如果 dev 又被產品 PR 推進，就換到新 tip 重跑，編號依序用 P、Q。換的條件是新 tip 自己的 CI 實跑 7 個 lane 全綠，而且 dev 部署 77/77。到 2026-11-17 為止最多換 3 次，超過就停下來重問。

| 步驟 | 內容 |
|---|---|
| A | dispatch 這個 candidate 的 core-ops staging build |
| HD-08o | gate-2 Data Gate 標成 `passed-with-deviation`，只適用於這個 candidate 進 staging，review_by 2026-11-27 |
| HD-09o | `release.stage=dev-verified`、`admission_target=staging`、`decision=go`，核准編號 `HUMANOPS-STAGING-ADMISSION-20261115O` |
| D | 以 Human/Ops 身分 approve 這個 registry PR |
| HD-10o | registry 合併後登記 lease `HUMANOPS-STAGING-LEASE-20261115O` |
| HD-12o | 只為這張 lease 觸發的 staging deploy run 核准 GitHub `staging` environment |

HD-08o 有三項偏差：

- source contract：不適用。core-ops 關閉所有外部來源，manifest 記錄 16 個來源全關、沒有任何憑證。
- point-in-time（model-ready data）：不適用。core-ops 不使用模型。
- freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。

HD-09o 只授權一次 staging release：一筆請求、一張 lease、一次 deploy，不包含 production。

每一步之前，dev tip 都必須仍是 60cf78695，ancestry 也必須為空。E、F 兩步允許 tip 多出這個只含 evidence 的 registry 合併。任一條件不符，就照上面的自動換 tip 規則處理，或停下來重問。

B、C 的生效條件在 build 後查核，全部成立：

- build 成功。
- manifest 的 candidate 為 60cf78695，profile 為 core-ops。
- 16 個來源全關，且沒有憑證。
- npm audit 0 個 finding。
- 5 個 staging 資源都不存在。

綁定的 digest `sha256:62450c57…` 就是這次 build 的產物。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄上述 CI，維持 `passed`。786db7baf 的 receipt 移到 `historical_receipts`。
- gate-1、gate-4：維持 `blocked`，等負責人決定 HD-07。
- gate-3、5、6：原本就是 `blocked`，只更新 `release_sha`。

## 驗證

- `check_release_gate_registry.py --expected-sha 60cf78695…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=60cf78695…, expected_manifest_digest=sha256:62450c57…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`60cf78695..head` 只改 `docs/evidence/`。
