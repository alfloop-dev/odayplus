# ODP-STAGING-ADMISSION-786DB7BAF-001：staging admission 換綁到 786db7baf

本資料夾記錄 core-ops candidate `786db7bafee061382e8bc60ab60bf139a232176f` 進入 ephemeral staging 的 registry 綁定。這裡只有 evidence，還沒有 lease，也還沒有部署。

## 為什麼換 candidate

dev 上的綁定 `429157600`（PR #1690）在 lease 前作廢：PR #1688 改了 workflow 與 Supervisor 檔案。之後三個 candidate 都在 registry 合併前作廢：

- `3edcb471b`：產品 PR #1692 合併而作廢。
- `502898d3f`：PR #1695 合併而作廢（它的 registry PR #1697 沒有合併）。#1695 只改 `.github/workflows/ci.yml`，但 ancestry 白名單不收 workflow 檔。
- `0b69b62d3`：它的 registry PR #1698 的 product-security 變紅。pip-audit 回報上游新公告 `opentelemetry-api 1.45.1`（CVE-2026-126360，PR CI [43526150351](https://github.com/alfloop-dev/odayplus/actions/runs/43526150351)），PR #1701 在 dev 上修正。

這一輪派工沒有凍結，由負責人決定。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `786db7bafee061382e8bc60ab60bf139a232176f`（dev tip，PR #1701 合併） |
| staging build | Runtime Release run [43554456263](https://github.com/alfloop-dev/odayplus/actions/runs/43554456263)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（`manifest_digest` `sha256:9a3a7b9e…`）；components、SBOM、signature 一起換 |
| candidate 本身的 CI | push CI [43547110665](https://github.com/alfloop-dev/odayplus/actions/runs/43547110665) 與 merge group CI [43542301696](https://github.com/alfloop-dev/odayplus/actions/runs/43542301696) 都在 786db7baf 實跑 7 個 product lane，全部 success |
| 與上一個 candidate 的差異 | `0b69b62d3..786db7baf` 只改 `uv.lock`、`NOTICE-THIRD-PARTY.md`、`docs/evidence/sbom.json`（#1701：opentelemetry-api／sdk 1.45.1→1.45.2，semantic-conventions 0.66b1→0.66b2） |
| dev 實際部署 | Runtime Release run [43552117814](https://github.com/alfloop-dev/odayplus/actions/runs/43552117814) 部署 786db7baf 本身，第一次就通過：live E2E 77 項全過（含 `worker:idempotent_replay`），0 個 blocker；preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job、public-egress probe（預期 denied）都通過 |
| npm audit | staging build 的 production 相依 0 個 finding |
| target absence | staging build 讀回 5 個 `stg-odp-786db7bafee0-5774aed7-*` 資源都不存在 |

## 人工決定

記錄檔為 `humanops-staging-admission-authorization-20261114.json`。負責人蔡尚志於 2026-11-14T00:41:10Z 在 Claude Code 對話中選擇「修 CVE，接著跑整條鍵」，由 Claude 轉錄，不是外部身分系統的簽章。

這個選項把 0b69b62d3 的 A–F 整條鏈，套用到 CVE 修正合併後的 dev tip，核准編號用 M。另加兩個條件，都已查核成立：

- `0b69b62d3` 到新 candidate 之間，只能改這三個檔加 evidence。
- 新 candidate 的 CI 要實跑 7 個 product lane 且全綠，dev 部署的 live E2E 要 77/77。

| 步驟 | 內容 |
|---|---|
| A | dispatch 這個 candidate 的 core-ops staging build |
| HD-08m | gate-2 Data Gate 標成 `passed-with-deviation`，只適用這個 candidate 進 staging，review_by 2026-11-27 |
| HD-09m | `release.stage=dev-verified`、`admission_target=staging`、`decision=go`，核准編號 `HUMANOPS-STAGING-ADMISSION-20261114M` |
| D | 以 Human/Ops 身分 approve 這個 registry PR |
| HD-10m | registry 合併後登記 lease `HUMANOPS-STAGING-LEASE-20261114M` |
| HD-12m | 只為這張 lease 觸發的 staging deploy run 核准 GitHub `staging` environment |

HD-08m 的三項偏差：

- source contract：不適用。core-ops 關閉所有外部來源，manifest 記錄 16 個來源全關、沒有任何憑證。
- point-in-time（model-ready data）：不適用。core-ops 不使用模型。
- freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。

HD-09m 只授權一次 staging release：一筆請求、一張 lease、一次 deploy，不包含 production。

每一步之前，dev tip 都必須仍是 786db7baf，ancestry 也必須為空；E、F 兩步則允許 tip 多出這個只含 evidence 的 registry 合併。任一條件不符就停下來重問。

B、C 的生效條件在 build 後查核，全部成立：

- build 成功。
- manifest 的 candidate 為 786db7baf、profile 為 core-ops。
- 16 個來源全關，且沒有憑證。
- npm audit 0 個 finding。
- 5 個 staging 資源都不存在。

綁定的 digest `sha256:9a3a7b9e…` 就是這次 build 的產物。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄上述 CI，維持 `passed`；429157600 的 receipt 移到 `historical_receipts`。
- gate-1、gate-4：維持 `blocked`，等負責人決定 HD-07。
- gate-3、5、6：原本就是 `blocked`，只更新 `release_sha`。

## 驗證

- `check_release_gate_registry.py --expected-sha 786db7baf…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=786db7baf…, expected_manifest_digest=sha256:9a3a7b9e…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`786db7baf..head` 只改 `docs/evidence/`。
