# ODP-STAGING-ADMISSION-49EF699A6-001：staging admission 換綁到 49ef699a6

本資料夾記錄 core-ops candidate `49ef699a6c086f117463be5b73d0efc66d8ebb31` 進入 ephemeral staging 的 registry 綁定。這裡只有 evidence，還沒有 lease，也還沒有部署。

## 為什麼換 candidate

dev 上合併中的綁定仍是 `a060115fb`（PR #1723，鏈 R）。它以 lease `HUMANOPS-STAGING-LEASE-20261118R` 部署過一次，lease 已用掉。那次部署 [44261935709](https://github.com/alfloop-dev/odayplus/actions/runs/44261935709) 通過 staging verify 的 10 個階段後，在遠端 staging proof 取不到 identity token 而失敗，之後 hold 也被拒。PR #1724 修好這兩點。a060115fb 的 staging 殘留已在 2026-11-18 用 cleanup phase（[44438676956](https://github.com/alfloop-dev/odayplus/actions/runs/44438676956)）銷毀。

鏈 S 之後換了 3 次 tip，已到上限：

1. `9191d0e17`（#1724）→ `615f3eaa7`（#1725，operator 畫面隱藏 GREEN forecast alert）。615f3eaa7 的 admission PR #1726 沒有合併，由本 PR 取代：
   - 它的 orchestrator CI job 失敗。原因是 `ubuntu-latest` 已改指 ubuntu-26.04，Bubblewrap 在那裡因 AppArmor 無法建立 uid map，所有受保護執行的測試都失敗。
   - 它用的核准編號 `HUMANOPS-STAGING-ADMISSION-20261119S` 沿用到本 PR；lease `HUMANOPS-STAGING-LEASE-20261119S` 從未登記。
2. → `a3fecbb32`（#1728，orchestrator job 釘在 ubuntu-24.04）。它的 dev 自動部署 [44448038532](https://github.com/alfloop-dev/odayplus/actions/runs/44448038532) 失敗：
   - 新 runner 上的 `docker build` 會附加 provenance attestation，推上去的 digest 變成 OCI index。
   - Cloud Run execution 記錄的是平台 manifest digest，所以 job 驗證的 immutable image binding 不相符而拒絕。
3. → `49ef699a6`（#1729，release image 用 `--provenance=false` 建置，並加 workflow 契約測試）。這是本次 candidate。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `49ef699a6c086f117463be5b73d0efc66d8ebb31`（dev tip，PR #1729 合併，已含 #1723、#1724、#1725、#1728） |
| staging build | Runtime Release run [44492664259](https://github.com/alfloop-dev/odayplus/actions/runs/44492664259)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（`manifest_digest` `sha256:89b85e82…`）；components、SBOM、signature 一起換 |
| candidate 本身的 CI | push CI [44481534423](https://github.com/alfloop-dev/odayplus/actions/runs/44481534423)：11/11 個 job 成功。merge group CI [44473688356](https://github.com/alfloop-dev/odayplus/actions/runs/44473688356)：10/11 成功，唯一沒跑的是只負責 dispatch 的 automatic dev release job，它在 merge_group 本來就會 skip。兩次都實跑 7 個 product lane，全部成功 |
| `a060115fb..49ef699a6` 的 PR | #1723（上一個 registry）、#1724、#1725、#1728、#1729 |
| dev 實際部署 | [44488560222](https://github.com/alfloop-dev/odayplus/actions/runs/44488560222) **成功**，見下節 |
| npm audit | staging build 的 production 相依 0 個 finding |
| target absence | staging build 讀回 5 個 `stg-odp-49ef699a6c08-a4eeaa0e-*` 資源都不存在 |

逐 job 的紀錄在 `candidate-ci-49ef699a6.json`。

負責人曾同意：如果這個 tip 的 7 個 product lane 因變更範圍被 skip，只要 product code 與 615f3eaa7 相同，可改由 615f3eaa7 的 CI 代證。實際上 7 個 lane 都在 49ef699a6 本身跑過且全部成功，所以沒有使用這項代證，也不記錄偏差。

## dev 部署結果

CI 自動送出的 dev 部署 44488560222 第一次就成功：

- live E2E 83 項全部通過，沒有 blocker。
- preflight、migration 相容、Cloud Run smoke、public-egress probe 全部通過。
- worker／migration／scheduler 三個 job 驗證全部通過，包括 a3fecbb32 失敗的 immutable image binding。
- dev 現在的線上版本是 `odp-49ef699a6c08`。

負責人原本允許前置檢查被拒時另送新 run，也允許 `models:registry_refused` 與 `models:no_surrogate_markers` 兩項失敗。兩項寬限都沒有用到，所以不需要記錄 dev 部署偏差。hosted artifact 原檔在 `hosted-artifacts/run-44488560222/`。

## 人工決定

紀錄檔是 `humanops-staging-admission-authorization-20261119.json`。負責人蔡尚志在 Claude Code 對話中做了以下選擇，由 Claude 轉錄，不是外部身分系統的簽章：

- 2026-11-18T08:06:48Z：「修這兩個，接著跑鏈 S」。規則同鏈 R，跟 tip 期限 2026-11-22，最多換 3 次。
- 2026-11-18T23:41:54Z：「對 615f3eaa7 重送，最多 2 次」。
- 2026-11-19T02:10:23Z：「先把 orchestrator 釘在 24.04」。合併後在新 tip 重做鏈 S，取代 #1726。
- 2026-11-19T05:25:48Z：「加 --provenance=false，接受 lane 由 615f3eaa7 代證」。合併後的 tip 是第 3 次換 tip，也是最後一次；dev 部署仍要成功（或只那兩項模型檢查失敗），其餘 A–F 不變。

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

每一步之前，dev tip 都必須仍是 49ef699a6，ancestry 也必須為空。E、F 兩步允許 tip 多出這個只含 evidence 的 registry 合併。

B、C 的生效條件在 build 後查核，全部成立：

- build 成功。
- manifest 的 candidate 為 49ef699a6，profile 為 core-ops。
- 16 個來源全關，且沒有憑證。
- npm audit 0 個 finding。
- 5 個 staging 資源都不存在。

## 還沒驗證的

- #1724 的新取 token 路徑沒在 staging 實跑過。
- 取到 token 之後，proof 的 `/platform/health` 與 `/platform/version` 檢查從沒在 staging 跑到過。
- #1724 對 workflow 的修改沒有實際跑過。
- #1729 只在 dev 驗證過；staging 部署會再走一次同一個 binding 檢查。
- orchestrator job 在 ubuntu-26.04 上的 sandbox 問題只是被避開（釘 24.04），還沒修。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄上述 CI，維持 `passed`。a060115fb 的 receipt 移到 `historical_receipts`。
- gate-1 Contract Gate：維持 `blocked`。先前的契約審查只涵蓋 `60cf78695..be095f9f4`，不沿用；等負責人決定 HD-07。
- gate-2 Data Gate：a060115fb 的 receipt 移到 `historical_receipts`，依 HD-08s 重新處置。
- gate-4 Security and Privacy Gate：維持 `blocked`，等負責人決定 HD-07。
- gate-3、5、6：原本就是 `blocked`，只更新 `release_sha`。

staging admission 不要求 gate-1 與 gate-4 通過；production admission 會要求。

## 驗證

- `check_release_gate_registry.py --expected-sha 49ef699a6…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=49ef699a6…, expected_manifest_digest=sha256:89b85e82…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`49ef699a6..head` 只改 `docs/evidence/`。
