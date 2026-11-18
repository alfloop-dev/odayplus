# ODP-STAGING-ADMISSION-A060115FB-001：staging admission 換綁到 a060115fb

本資料夾記錄 core-ops candidate `a060115fb3a348fdcad7e5f40a66a7e5ea4a35fc` 進入 ephemeral staging 的 registry 綁定。這裡只有 evidence，還沒有 lease，也還沒有部署。

## 為什麼換 candidate

上一個綁定是 `be095f9f4`（PR #1720）。它是 dev admission，用來做一次簽章手動 dev 部署，lease 是 `HUMANOPS-DEV-LEASE-20261117B`。

那次部署是 [44028045679](https://github.com/alfloop-dev/odayplus/actions/runs/44028045679)，在 smoke 之前就失敗：

- 新的 API revision 啟動時載入已 promote 的 LightGBM 模型。
- `python:3.12-slim` 沒有 `libgomp.so.1`，容器直接結束。
- dev 流量留在 60cf78695。這次 run 留下了讀回收據，自動 dev 部署因此不再被拒。

之後合併了兩個 PR：

- PR #1721：在 api、worker、scheduler 三個 image 安裝 `libgomp1`，並加測試。Antigravity 審查。
- PR #1722：Runtime Release 新增只限 staging 的 `cleanup` phase，用來銷毀驗證失敗後被保留的 ephemeral staging release。Antigravity2 審查。

更早的 staging candidate 是 `4b2310c53`（PR #1716）。它的 staging verify 在 run 43742101252 失敗，release 目前仍被保留。

這一輪派工沒有凍結，由負責人決定。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `a060115fb3a348fdcad7e5f40a66a7e5ea4a35fc`（dev tip，PR #1722 合併，已含 #1721） |
| staging build | Runtime Release run [44234731301](https://github.com/alfloop-dev/odayplus/actions/runs/44234731301)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（`manifest_digest` `sha256:a502624b…`）；components、SBOM、signature 一起換 |
| candidate 本身的 CI | push CI [44215881371](https://github.com/alfloop-dev/odayplus/actions/runs/44215881371)：11/11 個 job 成功。merge group CI [44207501940](https://github.com/alfloop-dev/odayplus/actions/runs/44207501940)：10/11 成功，唯一沒跑的是只負責 dispatch 的 automatic dev release job，它在 merge_group 本來就會 skip。兩次都實跑 7 個 product lane，全部成功 |
| `be095f9f4..a060115fb` 的 PR | #1720（上一個 registry）、#1721、#1722 |
| dev 實際部署 | 自動部署 [44221981411](https://github.com/alfloop-dev/odayplus/actions/runs/44221981411) **成功**，見下節 |
| npm audit | staging build 的 production 相依 0 個 finding |
| target absence | staging build 讀回 5 個 `stg-odp-a060115fb3a3-b9c34064-*` 資源都不存在 |

逐 job 的紀錄在 `candidate-ci-a060115fb.json`。

## dev 部署結果

Runtime Release run 44221981411 是 a060115fb 的自動 dev 部署。它沒有被拒，而且成功：

- live E2E 83 項全部通過，沒有 blocker。先前 candidate 失敗的兩項模型檢查這次也通過。
- preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job 驗證、public-egress probe 全部通過。
- dev 現在的線上版本是 `odp-a060115fb3a3`。

負責人原本允許 `models:registry_refused` 與 `models:no_surrogate_markers` 兩項失敗。這次沒有用到這項寬限，所以不需要記錄 dev 部署偏差。hosted artifact 原檔在 `hosted-artifacts/run-44221981411/`。

## 人工決定

紀錄檔是 `humanops-staging-admission-authorization-20261118.json`。負責人蔡尚志在 Claude Code 對話中做了兩次選擇，由 Claude 轉錄，不是外部身分系統的簽章：

- 2026-11-17T06:19:10Z：「修 image，接著跑鏈 R」。
- 2026-11-18T00:12:18Z：「#1721 你審，#1722 等額度」。這個選項同時把「自動跟 tip」的期限延到 2026-11-22。選項中由負責人審 #1721 的部分沒有用到，因為 Antigravity 已在 2026-11-18T00:06:13Z 核准。

| 步驟 | 內容 |
|---|---|
| A | dispatch 這個 candidate 的 core-ops staging build |
| HD-08r | gate-2 Data Gate 標成 `passed-with-deviation`，只適用於這個 candidate 進 staging，review_by 2026-11-27 |
| HD-09r | `release.stage=dev-verified`、`admission_target=staging`、`decision=go`，核准編號 `HUMANOPS-STAGING-ADMISSION-20261118R` |
| D | 以 Human/Ops 身分 approve 這個 registry PR |
| HD-10r | registry 合併後登記 lease `HUMANOPS-STAGING-LEASE-20261118R` |
| HD-12r | 只為這張 lease 觸發的 staging deploy run 核准 GitHub `staging` environment |

HD-08r 有三項偏差：

- source contract：不適用。core-ops 關閉所有外部來源，manifest 記錄 16 個來源全關、沒有任何憑證。
- point-in-time（model-ready data）：不適用。core-ops 不使用模型。
- freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。

HD-09r 只授權一次 staging release：一筆請求、一張 lease、一次 deploy，不包含 production。

每一步之前，dev tip 都必須仍是 a060115fb，ancestry 也必須為空。E、F 兩步允許 tip 多出這個只含 evidence 的 registry 合併。這是鏈 R 的第一個 candidate，還沒有換過 tip。

B、C 的生效條件在 build 後查核，全部成立：

- build 成功。
- manifest 的 candidate 為 a060115fb，profile 為 core-ops。
- 16 個來源全關，且沒有憑證。
- npm audit 0 個 finding。
- 5 個 staging 資源都不存在。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄上述 CI，維持 `passed`。be095f9f4 的 receipt 移到 `historical_receipts`。
- gate-1 Contract Gate：改回 `blocked`。be095f9f4 的契約審查只涵蓋 `60cf78695..be095f9f4`，而且只為那一次 dev 部署，不沿用到這個 candidate。receipt 移到 `historical_receipts`，等負責人決定 HD-07。
- gate-4 Security and Privacy Gate：改回 `blocked`。be095f9f4 的 H01 與 dev npm 偏差只適用於那個 dev candidate，不沿用。receipt 移到 `historical_receipts`，等負責人決定 HD-07。
- gate-3、5、6：原本就是 `blocked`，只更新 `release_sha`。

staging admission 不要求 gate-1 與 gate-4 通過；production admission 會要求。

## 驗證

- `check_release_gate_registry.py --expected-sha a060115fb…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=a060115fb…, expected_manifest_digest=sha256:a502624b…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`a060115fb..head` 只改 `docs/evidence/`。
