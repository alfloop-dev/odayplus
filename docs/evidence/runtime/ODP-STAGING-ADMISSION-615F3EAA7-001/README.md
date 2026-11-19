# ODP-STAGING-ADMISSION-615F3EAA7-001：staging admission 換綁到 615f3eaa7

本資料夾記錄 core-ops candidate `615f3eaa7fb5d6fe1fad129d2e58485a408fd8d5` 進入 ephemeral staging 的 registry 綁定。這裡只有 evidence，還沒有 lease，也還沒有部署。

## 為什麼換 candidate

上一個綁定是 `a060115fb`（PR #1723，鏈 R）。它以 lease `HUMANOPS-STAGING-LEASE-20261118R` 部署過一次，lease 已用掉。

那次部署是 [44261935709](https://github.com/alfloop-dev/odayplus/actions/runs/44261935709)：

- staging verify 的 10 個階段第一次全部通過。
- 下一步「遠端 staging proof」失敗。checker 用 `gcloud auth print-identity-token` 取 token，workload identity federation 的身分不能用這個指令。
- 接著 hold 被拒，因為 lifecycle 狀態已是 `verified`。a060115fb 的 staging 資源因此既沒被保留，也沒被銷毀。

之後合併了兩個 PR：

- PR #1724：遠端 proof 改用 staging verify 已在用的 generateIdToken 路徑；hold 接受 `verified` 狀態；cleanup phase 接受這種情況。Antigravity 審查。
- PR #1725：operator 畫面隱藏 GREEN 的 forecast alert。這個 PR 不是鏈 S 開的，合併後成為 dev tip。

60cf78695 與 4b2310c53 兩組被保留的 staging release，已在 2026-11-18 用 cleanup phase 銷毀。

這一輪派工沒有凍結，由負責人決定。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `615f3eaa7fb5d6fe1fad129d2e58485a408fd8d5`（dev tip，PR #1725 合併，已含 #1723、#1724） |
| staging build | Runtime Release run [44426728206](https://github.com/alfloop-dev/odayplus/actions/runs/44426728206)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（`manifest_digest` `sha256:225bd242…`）；components、SBOM、signature 一起換 |
| candidate 本身的 CI | push CI [44343517064](https://github.com/alfloop-dev/odayplus/actions/runs/44343517064)：11/11 個 job 成功。merge group CI [44333329658](https://github.com/alfloop-dev/odayplus/actions/runs/44333329658)：10/11 成功，唯一沒跑的是只負責 dispatch 的 automatic dev release job，它在 merge_group 本來就會 skip。兩次都實跑 7 個 product lane，全部成功 |
| `a060115fb..615f3eaa7` 的 PR | #1723（上一個 registry）、#1724、#1725 |
| dev 實際部署 | [44422952022](https://github.com/alfloop-dev/odayplus/actions/runs/44422952022) **成功**，見下節 |
| npm audit | staging build 的 production 相依 0 個 finding |
| target absence | staging build 讀回 5 個 `stg-odp-615f3eaa7fb5-0f1fd612-*` 資源都不存在 |

逐 job 的紀錄在 `candidate-ci-615f3eaa7.json`。

## dev 部署結果

CI 自動送出的 dev 部署 [44355300563](https://github.com/alfloop-dev/odayplus/actions/runs/44355300563) 被拒：

- 它停在前置檢查「Verify standing dev policy against completed push CI」，訊息是「GitHub read/dispatch failed」。
- 沒有任何 build 或 deploy job 執行，dev 沒被動到。
- 前一個 tip 9191d0e17 的自動部署 [44337860835](https://github.com/alfloop-dev/odayplus/actions/runs/44337860835) 也是同樣被拒。
- 同一段前置檢查在本機用相同輸入唯讀重跑會通過。它要讀 GitHub 327 次，adapter 沒有重試，任何一次讀取失敗就整個被拒。這個弱點還沒修。

負責人決定對 615f3eaa7 另送一個新的 `phase=auto` run，沒有 rerun 失敗的 run。新的 run 是 44422952022，它成功：

- live E2E 83 項全部通過，沒有 blocker。
- preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job 驗證、public-egress probe 全部通過。
- dev 現在的線上版本是 `odp-615f3eaa7fb5`。

負責人原本允許 `models:registry_refused` 與 `models:no_surrogate_markers` 兩項失敗。這次沒有用到這項寬限，所以不需要記錄 dev 部署偏差。hosted artifact 原檔在 `hosted-artifacts/run-44422952022/`。

## 人工決定

紀錄檔是 `humanops-staging-admission-authorization-20261119.json`。負責人蔡尚志在 Claude Code 對話中做了兩次選擇，由 Claude 轉錄，不是外部身分系統的簽章：

- 2026-11-18T08:06:48Z：「修這兩個，接著跑鏈 S」。規則同鏈 R，跟 tip 期限 2026-11-22，最多換 3 次。
- 2026-11-18T23:41:54Z：「對 615f3eaa7 重送，最多 2 次」。

| 步驟 | 內容 |
|---|---|
| A | dispatch 這個 candidate 的 core-ops staging build |
| HD-08s | gate-2 Data Gate 標成 `passed-with-deviation`，只適用於這個 candidate 進 staging，review_by 2026-11-27 |
| HD-09s | `release.stage=dev-verified`、`admission_target=staging`、`decision=go`，核准編號 `HUMANOPS-STAGING-ADMISSION-20261119S` |
| D | 以 Human/Ops 身分 approve 這個 registry PR |
| HD-10s | registry 合併後登記 lease `HUMANOPS-STAGING-LEASE-20261119S` |
| HD-12s | 只為這張 lease 觸發的 staging deploy run 核准 GitHub `staging` environment |

HD-08s 有三項偏差：

- source contract：不適用。core-ops 關閉所有外部來源，manifest 記錄 16 個來源全關、沒有任何憑證。
- point-in-time（model-ready data）：不適用。core-ops 不使用模型。
- freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。

HD-09s 只授權一次 staging release：一筆請求、一張 lease、一次 deploy，不包含 production。

每一步之前，dev tip 都必須仍是 615f3eaa7，ancestry 也必須為空。E、F 兩步允許 tip 多出這個只含 evidence 的 registry 合併。這是鏈 S 的第 1 次換 tip（9191d0e17 → 615f3eaa7），上限 3 次。

B、C 的生效條件在 build 後查核，全部成立：

- build 成功。
- manifest 的 candidate 為 615f3eaa7，profile 為 core-ops。
- 16 個來源全關，且沒有憑證。
- npm audit 0 個 finding。
- 5 個 staging 資源都不存在。

## 還沒驗證的

- #1724 的新取 token 路徑沒在 staging 實跑過。
- 取到 token 之後，proof 的 `/platform/health` 與 `/platform/version` 檢查從沒在 staging 跑到過。
- #1724 對 workflow 的修改沒有實際跑過。
- a060115fb 的 staging 殘留資源在本 evidence 記錄時還沒清除。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄上述 CI，維持 `passed`。a060115fb 的 receipt 移到 `historical_receipts`。
- gate-1 Contract Gate：維持 `blocked`。先前的契約審查只涵蓋 `60cf78695..be095f9f4`，不沿用；等負責人決定 HD-07。
- gate-2 Data Gate：a060115fb 的 receipt 移到 `historical_receipts`，依 HD-08s 重新處置。
- gate-4 Security and Privacy Gate：維持 `blocked`，等負責人決定 HD-07。
- gate-3、5、6：原本就是 `blocked`，只更新 `release_sha`。

staging admission 不要求 gate-1 與 gate-4 通過；production admission 會要求。

## 驗證

- `check_release_gate_registry.py --expected-sha 615f3eaa7…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=615f3eaa7…, expected_manifest_digest=sha256:225bd242…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`615f3eaa7..head` 只改 `docs/evidence/`。
