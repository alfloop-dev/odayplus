# ODP-STAGING-ADMISSION-0B69B62D3-001：staging admission 換綁到 0b69b62d3

本資料夾記錄 core-ops candidate `0b69b62d32e0ad71f1a70b20eb546f4b5c6725b1` 進入 ephemeral staging 的 registry 綁定。這裡只有 evidence，還沒有 lease，也還沒有部署。

## 為什麼換 candidate

dev 上的綁定 `429157600`（PR #1690）在 lease 前作廢：PR #1688 改了 workflow 與 Supervisor 檔案。之後兩個 candidate 都在 registry 合併前作廢：

- `3edcb471b`：產品 PR #1692 合併而作廢。
- `502898d3f`：PR #1695 合併而作廢（它的 registry PR #1697 沒有合併）。#1695 只改 `.github/workflows/ci.yml`，但 ancestry 白名單不收 workflow 檔。

這一輪派工沒有凍結，由負責人決定。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `0b69b62d32e0ad71f1a70b20eb546f4b5c6725b1`（dev tip，PR #1695 合併） |
| staging build | Runtime Release run [43523926766](https://github.com/alfloop-dev/odayplus/actions/runs/43523926766)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（檔案 sha256 `4d6c8f35…`，`manifest_digest` `sha256:aac410ab…`）；components、SBOM、signature 一起換 |
| candidate 本身的 CI | push CI [43484884486](https://github.com/alfloop-dev/odayplus/actions/runs/43484884486) 與 merge group CI [43483269225](https://github.com/alfloop-dev/odayplus/actions/runs/43483269225) 為 success，但 change scope 跳過全部 7 個 product lane |
| product lane 最後實跑 | 5cb356cb3（PR #1692 合併）的 push CI [43400629117](https://github.com/alfloop-dev/odayplus/actions/runs/43400629117) 與 merge group CI [43394531969](https://github.com/alfloop-dev/odayplus/actions/runs/43394531969)，7 條全部 success |
| 中間差異 | `5cb356cb3..0b69b62d3` 只改 `.orchestrator/`、`scripts/ai_status.py`（#1685）與 `.github/workflows/ci.yml`（#1695）。前兩者在 ancestry 的 evidence 白名單內；`ci.yml` 不由 Runtime Release workflow 執行 |
| dev 實際部署 | Runtime Release run [43486461634](https://github.com/alfloop-dev/odayplus/actions/runs/43486461634) 部署 0b69b62d3 本身，第一次就通過：live E2E 77 項全過（含 `worker:idempotent_replay`），0 個 blocker；preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job、public-egress probe 都通過 |
| npm audit | staging build 的 production 相依 0 個 finding |
| target absence | staging build 讀回 5 個 staging 資源都不存在 |

## 人工決定

記錄檔為 `humanops-staging-admission-authorization-20261113.json`。負責人蔡尚志於 2026-11-13T23:01:28Z 在 Claude Code 對話中，以一題整條鏈的提問同意 A–F，由 Claude 轉錄，不是外部身分系統的簽章。

| 步驟 | 內容 |
|---|---|
| A | dispatch 這個 candidate 的 core-ops staging build |
| HD-08l | gate-2 Data Gate 標成 `passed-with-deviation`，只適用這個 candidate 進 staging，review_by 2026-11-27 |
| HD-09l | `release.stage=dev-verified`、`admission_target=staging`、`decision=go`，核准編號 `HUMANOPS-STAGING-ADMISSION-20261113L` |
| D | 以 Human/Ops 身分 approve 這個 registry PR |
| HD-10l | registry 合併後登記 lease `HUMANOPS-STAGING-LEASE-20261113L` |
| HD-12l | 只為這張 lease 觸發的 staging deploy run 核准 GitHub `staging` environment |

HD-08l 的三項偏差：

- source contract：不適用。core-ops 關閉所有外部來源，manifest 記錄 16 個來源全關、沒有任何憑證。
- point-in-time（model-ready data）：不適用。core-ops 不使用模型。
- freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。

HD-09l 只授權一次 staging release：一筆請求、一張 lease、一次 deploy，不包含 production。

每一步之前，dev tip 都必須仍是 0b69b62d3，ancestry 也必須為空；E、F 兩步則允許 tip 多出這個只含 evidence 的 registry 合併。任一條件不符就停下來重問。

B、C 的生效條件在 build 後查核，全部成立：

- build 成功。
- manifest 的 candidate 為 0b69b62d3、profile 為 core-ops。
- 16 個來源全關，且沒有憑證。
- npm audit 0 個 finding。
- 5 個 staging 資源都不存在。

綁定的 digest `sha256:aac410ab…` 就是這次 build 的產物。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄上述 CI，維持 `passed`；429157600 的 receipt 移到 `historical_receipts`。
- gate-1、gate-4：維持 `blocked`，等負責人決定 HD-07。
- gate-3、5、6：原本就是 `blocked`，只更新 `release_sha`。

## 驗證

- `check_release_gate_registry.py --expected-sha 0b69b62d3…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=0b69b62d3…, expected_manifest_digest=sha256:aac410ab…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`0b69b62d3..head` 只改 `docs/evidence/`。
