# ODP-STAGING-ADMISSION-4B2310C53-001：staging admission 換綁到 4b2310c53

本資料夾記錄 core-ops candidate `4b2310c53101da3d0f534ad5d035f8200646f59c` 進入 ephemeral staging 的 registry 綁定。這裡只有 evidence，還沒有 lease，也還沒有部署。

## 為什麼換 candidate

上一個綁定是 `60cf78695`（PR #1713），用掉了 lease `HUMANOPS-STAGING-LEASE-20261115O2`。第一次登記的 O 在簽發前就被擋下，原因是本機簽章帳號被換掉了。

這張 lease 觸發的 staging deploy 是 [43699516874](https://github.com/alfloop-dev/odayplus/actions/runs/43699516874)。它第一次成功建立 ephemeral release，但 verify 的 10 個階段有 7 個失敗，原因有三個：

- 用 gcloud impersonation 取 identity token，需要 module 沒有授予的角色。
- rollback probe 的 traffic tag 超過 Cloud Run 的長度上限。
- restore 用的使用者不擁有 release schema。

PR #1715 修正了這三個問題。dev 也另外合併了 PR #1714（forecastops startup alias）。

這一輪派工沒有凍結，由負責人決定。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `4b2310c53101da3d0f534ad5d035f8200646f59c`（dev tip，PR #1715 合併） |
| staging build | Runtime Release run [43725894635](https://github.com/alfloop-dev/odayplus/actions/runs/43725894635)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（`manifest_digest` `sha256:cead6a30…`）；components、SBOM、signature 一起換 |
| candidate 本身的 CI | push CI [43713144763](https://github.com/alfloop-dev/odayplus/actions/runs/43713144763) 與 merge group CI [43709407968](https://github.com/alfloop-dev/odayplus/actions/runs/43709407968) 都在 4b2310c53 實跑 7 個 product lane，全部 success |
| `60cf78695..4b2310c53` 的 PR | #1713（上一個 registry）、#1714、#1715 |
| dev 實際部署（**失敗，記為偏差**） | 見下節 |
| npm audit | staging build 的 production 相依 0 個 finding |
| target absence | staging build 讀回 5 個 `stg-odp-4b2310c53101-f57a451e-*` 資源都不存在 |

## dev 部署偏差

Runtime Release run [43717780340](https://github.com/alfloop-dev/odayplus/actions/runs/43717780340) 部署 4b2310c53 時**失敗**，dev 流量仍停在 60cf78695。

失敗與通過的項目：

- live E2E 77 項中過了 75 項。只有兩項失敗，原因都是 dev 專屬的模型資料：
  - `models:registry_refused`：另一條 seed-dev-data 工作依負責人的 dev 專屬核准（MRB-DEV-20261114-01／02、MRB-DEV-20261115-01／02），在 dev registry 把 forecast 版本 promote 成 shadow／full alias。
  - `models:no_surrogate_markers`：其中兩個版本的 `monitoring_config.requested_by` 被 gate 判定為 seed 標記。
- preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job 驗證、public-egress probe（預期 denied）全部通過。

staging 會建立自己的空 model registry，core-ops profile 也不使用模型，所以 dev 的模型狀態不會帶到 staging。

負責人在 HD-09p 接受這個偏差。條件是 77 項中只能有這兩項失敗、其他檢查全部通過，實際結果與這個條件完全吻合。紀錄檔為 `dev-deploy-deviation-4b2310c53.json`，hosted artifact 原檔在 `hosted-artifacts/run-43717780340/`。

## 人工決定

記錄檔為 `humanops-staging-admission-authorization-20261115.json`。負責人蔡尚志在 Claude Code 對話中做了兩次選擇，由 Claude 轉錄，不是外部身分系統的簽章：

- 2026-11-15T09:46:29Z：「修這三個，接著跑鏈 P」。
- 2026-11-15T14:43:03Z：「記偏差跑鏈 P，只寬這兩項模型檢查」。

| 步驟 | 內容 |
|---|---|
| A | dispatch 這個 candidate 的 core-ops staging build |
| HD-08p | gate-2 Data Gate 標成 `passed-with-deviation`，只適用於這個 candidate 進 staging，review_by 2026-11-27 |
| HD-09p | `release.stage=dev-verified`（含上述 dev 部署偏差）、`admission_target=staging`、`decision=go`，核准編號 `HUMANOPS-STAGING-ADMISSION-20261115P` |
| D | 以 Human/Ops 身分 approve 這個 registry PR |
| HD-10p | registry 合併後登記 lease `HUMANOPS-STAGING-LEASE-20261115P` |
| HD-12p | 只為這張 lease 觸發的 staging deploy run 核准 GitHub `staging` environment |

HD-08p 有三項偏差：

- source contract：不適用。core-ops 關閉所有外部來源，manifest 記錄 16 個來源全關、沒有任何憑證。
- point-in-time（model-ready data）：不適用。core-ops 不使用模型。
- freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。

HD-09p 只授權一次 staging release：一筆請求、一張 lease、一次 deploy，不包含 production。

每一步之前，dev tip 都必須仍是 4b2310c53，ancestry 也必須為空。E、F 兩步允許 tip 多出這個只含 evidence 的 registry 合併。

B、C 的生效條件在 build 後查核，全部成立：

- build 成功。
- manifest 的 candidate 為 4b2310c53，profile 為 core-ops。
- 16 個來源全關，且沒有憑證。
- npm audit 0 個 finding。
- 5 個 staging 資源都不存在。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄上述 CI，維持 `passed`。60cf78695 的 receipt 移到 `historical_receipts`。
- gate-1、gate-4：維持 `blocked`，等負責人決定 HD-07。
- gate-3、5、6：原本就是 `blocked`，只更新 `release_sha`。

## 驗證

- `check_release_gate_registry.py --expected-sha 4b2310c53…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=4b2310c53…, expected_manifest_digest=sha256:cead6a30…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`4b2310c53..head` 只改 `docs/evidence/`。
