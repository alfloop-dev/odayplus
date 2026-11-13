# ODP-STAGING-ADMISSION-429157600-001：staging admission 換綁到 429157600

本資料夾記錄 core-ops candidate `429157600b1bfbccb9fb89b7604a67f4691f2ddf` 進入 ephemeral staging 的 registry 綁定。這裡只有 evidence，還沒有 lease，也還沒有部署。

## 為什麼換 candidate

dev 上的綁定 `28881624b`（PR #1669）在 lease 前被 PR #1651 作廢，因為 #1651 改了 `deploy-dev.yml`、`product_ops/deployment` 與 release 工具。之後兩個 candidate 也在 admission 前作廢：

- `751439bac`：產品 PR #1671、#1672 合併而作廢。
- `ffdf894e4`：staging build 前，#1683 合併而作廢。

這個 candidate 包含：

- 最後一支 ACCEPT 拆分 #1673。
- 它帶進的 e2e flake 的修正 #1684。
- #1683。
- staging probe switch readback 修正 #1687。

這一輪派工沒有凍結，由負責人決定。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `429157600b1bfbccb9fb89b7604a67f4691f2ddf`（dev tip，PR #1687 合併） |
| staging build | Runtime Release run [43363668275](https://github.com/alfloop-dev/odayplus/actions/runs/43363668275)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（檔案 sha256 `428355f1…`，`manifest_digest` `sha256:bd1d5c8b…`）；components、SBOM、signature 一起換 |
| push CI | [43238427758](https://github.com/alfloop-dev/odayplus/actions/runs/43238427758)，在 429157600 本身實跑 7 個 product lane，全部 success |
| merge group CI | [43230659744](https://github.com/alfloop-dev/odayplus/actions/runs/43230659744)，同樣在 429157600 實跑 7 個 product lane，全部 success |
| dev 實際部署 | Runtime Release run [43247589659](https://github.com/alfloop-dev/odayplus/actions/runs/43247589659) 第一次就通過：live E2E 77 項全過（含 `worker:idempotent_replay`），0 個 blocker；preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job、public-egress probe 都通過 |
| npm audit | staging build 的 production 相依 0 個 finding |
| target absence | staging build 讀回 5 個 `stg-odp-429157600b1b-3e2860e8-*` 資源都不存在 |

## 人工決定

記錄檔為 `humanops-staging-admission-authorization-20261113.json`。負責人蔡尚志於 2026-11-13T05:12:32Z 在 Claude Code 對話中同意，由 Claude 轉錄，不是外部身分系統的簽章。

- **HD-08i**：gate-2 Data Gate 標成 `passed-with-deviation`，只適用於這個 candidate 進入 staging，review_by 2026-11-27。
  - source contract：不適用。core-ops 關閉所有外部來源，manifest 記錄 16 個來源全關、沒有任何憑證。
  - point-in-time（model-ready data）：不適用。core-ops 不使用模型。
  - freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。
- **HD-09i**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`。
  - 只授權一次 staging release：一筆請求、一張 lease、一次 deploy。
  - GitHub `staging` environment 仍需另外核准。
  - 不包含 production。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄上述 CI，維持 `passed`；28881624b 的 receipt 移到 `historical_receipts`。
- gate-1、gate-4：維持 `blocked`，等負責人決定 HD-07。
- gate-3、5、6：原本就是 `blocked`，只更新 `release_sha`。

## 驗證

- `check_release_gate_registry.py --expected-sha 429157600…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=429157600…, expected_manifest_digest=sha256:bd1d5c8b…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`429157600..head` 只改 `docs/evidence/`。
