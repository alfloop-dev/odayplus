# ODP-STAGING-ADMISSION-502898D3F-001：staging admission 換綁到 502898d3f

本資料夾記錄 core-ops candidate `502898d3fc636cb32fa03bcbd9290b22203c68e1` 進入 ephemeral staging 的 registry 綁定。這裡只有 evidence，還沒有 lease，也還沒有部署。

## 為什麼換 candidate

dev 上的綁定 `429157600`（PR #1690）在 lease 前作廢：PR #1688 改了七個 workflow 與 Supervisor 檔案，這些都不在 candidate ancestry 白名單內。下一個 candidate `3edcb471b` 在 admission 前也作廢了，原因是產品 PR #1692 合併。

這個 candidate 包含：

- #1692（NetPlan）。
- #1691（UAT plan）。
- 只改控制面的 #1685。

這一輪派工沒有凍結，由負責人決定。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `502898d3fc636cb32fa03bcbd9290b22203c68e1`（dev tip，PR #1685 合併） |
| staging build | Runtime Release run [43421551430](https://github.com/alfloop-dev/odayplus/actions/runs/43421551430)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（檔案 sha256 `998f15f9…`，`manifest_digest` `sha256:119f94a7…`）；components、SBOM、signature 一起換 |
| candidate 本身的 CI | push CI [43404517371](https://github.com/alfloop-dev/odayplus/actions/runs/43404517371) 與 merge group CI [43403210638](https://github.com/alfloop-dev/odayplus/actions/runs/43403210638) 為 success，但 change scope 跳過全部 7 個 product lane，因為 #1685 只改控制面 |
| product lane 最後實跑 | 5cb356cb3（PR #1692 合併）的 push CI [43400629117](https://github.com/alfloop-dev/odayplus/actions/runs/43400629117) 與 merge group CI [43394531969](https://github.com/alfloop-dev/odayplus/actions/runs/43394531969)，7 條全部 success。`5cb356cb3..502898d3f` 只改 `.orchestrator/` 與 `scripts/ai_status.py`，兩者都在 candidate ancestry 的 evidence 白名單內 |
| dev 實際部署 | Runtime Release run [43405429880](https://github.com/alfloop-dev/odayplus/actions/runs/43405429880) 部署 502898d3f 本身，第一次就通過：live E2E 77 項全過（含 `worker:idempotent_replay`），0 個 blocker；preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job、public-egress probe 都通過 |
| npm audit | staging build 的 production 相依 0 個 finding |
| target absence | staging build 讀回 5 個 staging 資源都不存在 |

## 人工決定

記錄檔為 `humanops-staging-admission-authorization-20261113.json`。負責人蔡尚志於 2026-11-13T12:00:23Z 在 Claude Code 對話中，以一題合併提問同意 A、B、C，由 Claude 轉錄，不是外部身分系統的簽章。

- **A**：dispatch 這個 candidate 的 core-ops staging build。
- **HD-08k**：gate-2 Data Gate 標成 `passed-with-deviation`，只適用於這個 candidate 進入 staging，review_by 2026-11-27。
  - source contract：不適用。core-ops 關閉所有外部來源，manifest 記錄 16 個來源全關、沒有任何憑證。
  - point-in-time（model-ready data）：不適用。core-ops 不使用模型。
  - freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。
- **HD-09k**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`，核准編號 `HUMANOPS-STAGING-ADMISSION-20261113K`。
  - 只授權一次 staging release：一筆請求、一張 lease、一次 deploy。
  - GitHub `staging` environment 仍需另外核准。
  - 不包含 production。

HD-08k 與 HD-09k 是在 build 執行前同意的，同意時附帶生效條件。以下條件在 build 後全部查核成立：

- build 成功。
- manifest 的 candidate 為 502898d3f、profile 為 core-ops。
- 16 個來源全關，且沒有憑證。
- npm audit 0 個 finding。
- 5 個 staging 資源都不存在。
- 每一步之前，dev tip 仍是 502898d3f。

綁定的 digest `sha256:119f94a7…` 就是這次 build 的產物。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄上述 CI，維持 `passed`；429157600 的 receipt 移到 `historical_receipts`。
- gate-1、gate-4：維持 `blocked`，等負責人決定 HD-07。
- gate-3、5、6：原本就是 `blocked`，只更新 `release_sha`。

## 驗證

- `check_release_gate_registry.py --expected-sha 502898d3f…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=502898d3f…, expected_manifest_digest=sha256:119f94a7…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`502898d3f..head` 只改 `docs/evidence/`。
