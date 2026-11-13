# ODP-STAGING-ADMISSION-CCB9553BB-001：staging admission 換綁到 ccb9553bb

本資料夾記錄 core-ops candidate `ccb9553bb1511ec746e69a831a1a8f5ad483a91f` 進入 ephemeral staging 的 registry 綁定。只有 evidence，沒有 lease，也沒有部署。

## 為什麼換 candidate

上一個 candidate `730328798` 的 staging deploy（Runtime Release run [41387164145](https://github.com/alfloop-dev/odayplus/actions/runs/41387164145)）通過了 lease 驗證，卻在 `Execute ephemeral staging lifecycle create` 失敗：`FileNotFoundError: 'terraform'`。GitHub-hosted runner 沒有 terraform，而 `deploy-dev.yml` 從未安裝。失敗發生在任何資源建立之前；唯讀查詢 Cloud Run service／job、SA、secret、SQL database、Pub/Sub、bucket 都沒有殘留。修正是 PR #1637，它改的是 workflow，不屬於 evidence 路徑，所以必須換成包含修正的新 candidate。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `ccb9553bb1511ec746e69a831a1a8f5ad483a91f`（dev tip；含 #1637 terraform 安裝、#1638 lease 簽發器、#1639 帳號池冷卻改派修正，以及 #1633、#1636） |
| staging build | Runtime Release run [41426783637](https://github.com/alfloop-dev/odayplus/actions/runs/41426783637)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（檔案 sha256 `6e649d5e…`，`manifest_digest` `sha256:93ffa139…`）；components、SBOM、signature 一起換，沒有沿用 730328798 的任何成品 |
| exact CI | push CI [41424817212](https://github.com/alfloop-dev/odayplus/actions/runs/41424817212)，11 個 job 全部成功 |
| dev 實際部署 | Runtime Release run [41429631582](https://github.com/alfloop-dev/odayplus/actions/runs/41429631582)：preflight、migration 相容、Cloud Run smoke、live E2E、worker／migration／scheduler job 驗證、public-egress probe 都通過 |
| npm audit | staging build 與 dev 部署的 production 相依都是 0 個 finding |

## 人工決定（`humanops-staging-admission-authorization-20261102.json`）

負責人蔡尚志於 2026-11-02 在 Claude Code 對話中同意，由 Claude 轉錄，不是外部身分系統的簽章。取代只適用於 730328798 的 `HUMANOPS-STAGING-ADMISSION-20261102`。

- **HD-08b**：gate-2 Data Gate 標成 `passed-with-deviation`，只適用於這個 candidate 進入 staging，review_by 2026-11-16。
  - source contract：不適用，core-ops 關閉所有外部來源。
  - point-in-time（model-ready data）：不適用，core-ops 不使用模型。
  - freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。
- **HD-09b**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`。只授權一次 staging release（一筆請求、一張 lease、一次 deploy），GitHub `staging` environment 仍需另外核准，不包含 production。

## 其他 gate

- gate-0 Code Gate：以 exact CI 41424817212 重新綁 receipt，維持 `passed`；730328798 的 receipt 移到 `historical_receipts`。
- gate-1、gate-4：維持 `blocked`，等負責人決定 HD-07（契約審查、H01 處置）。
- gate-3／5／6：本來就是 `blocked`，只更新 `release_sha`。

staging admission 只檢查 gate-2；dev 自動部署走 2026-10-04 的 standing authorization，不讀這份 registry。

## 驗證

- `check_release_gate_registry.py --expected-sha ccb9553bb…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=ccb9553bb…, expected_manifest_digest=sha256:93ffa139…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`ccb9553bb..head` 只改 `docs/evidence/`。部署 dispatch 前，dev 上不可以合併其他非 evidence 變更，否則 workflow 的 candidate ancestry 檢查會拒絕。
