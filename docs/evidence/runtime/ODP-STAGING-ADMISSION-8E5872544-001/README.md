# ODP-STAGING-ADMISSION-8E5872544-001：staging admission 換綁到 8e5872544

本資料夾記錄 core-ops candidate `8e5872544bff88d421a1923c7957ca6b12429ce2` 進入 ephemeral staging 的 registry 綁定。只有 evidence，沒有 lease，也沒有部署。

## 為什麼換 candidate

上一個 candidate `ccb9553bb` 的 staging deploy 在 staging 建立步驟失敗，PR #1641 修正 create 順序與 verify 的 9 個必敗 stage。它的 candidate `b88e67b12` 在 lease 之前被只改 supervisor 控制面的 #1642、#1643 依舊規則作廢；PR #1645 把控制面加入 ancestry 白名單。兩者都不屬於 evidence 路徑，所以換成包含它們的新 candidate。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `8e5872544bff88d421a1923c7957ca6b12429ce2`（dev tip，PR #1645 合併） |
| staging build | Runtime Release run [41753392676](https://github.com/alfloop-dev/odayplus/actions/runs/41753392676)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（檔案 sha256 `ed88280a…`，`manifest_digest` `sha256:22c22bdc…`）；components、SBOM、signature 一起換 |
| exact push CI | [41753251393](https://github.com/alfloop-dev/odayplus/actions/runs/41753251393) 整體 success，但 11 個 job 只跑了 4 個（`orchestrator`、`change-scope`、`product` 聚合、`Request automatic dev release`）；7 個 product lane 被 change scope 跳過 |
| product lane | PR CI [42068563588](https://github.com/alfloop-dev/odayplus/actions/runs/42068563588) 跑在 e68b3688e（8e5872544 加一個只改 `docs/evidence` 的 commit）：`product-lint-unit`、`product-db`、`product-api-contract`、`product-e2e-gate`、`product-security`、`performance-gate`、`product-node` 全部 success |
| dev 實際部署 | Runtime Release run [41754011897](https://github.com/alfloop-dev/odayplus/actions/runs/41754011897) 一次就通過：live E2E 77 項全過，preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job、public-egress probe 都通過 |
| npm audit | staging build 的 production 相依 0 個 finding |

## 人工決定（`humanops-staging-admission-authorization-20261106.json`）

負責人蔡尚志於 2026-11-06T01:08:56Z 在 Claude Code 對話中同意，由 Claude 轉錄，不是外部身分系統的簽章。取代只適用於 ccb9553bb 的 `HUMANOPS-STAGING-ADMISSION-20261102B`，也取代建立在錯誤敘述上的 `HUMANOPS-STAGING-ADMISSION-20261104D`（見下方更正）。

- **HD-08e**：gate-2 Data Gate 標成 `passed-with-deviation`，只適用於這個 candidate 進入 staging，review_by 2026-11-19。
  - source contract：不適用，core-ops 關閉所有外部來源。
  - point-in-time（model-ready data）：不適用，core-ops 不使用模型。
  - freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。
- **HD-09e**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`。只授權一次 staging release（一筆請求、一張 lease、一次 deploy），GitHub `staging` environment 仍需另外核准，不包含 production。

## 更正

2026-11-04 問負責人 HD-08、HD-09 時，題目寫「CI 41753251393 的 11 個 job 全部成功」，這是錯的：7 個 product lane 被跳過。文字是從上一個 candidate 套來的，當時只檢查了 job 數量和整體結論。錯誤由 supervisor 派出的 worker 唯讀檢查發現，並以 GitHub API 確認。依該同意未做任何核准、lease 或部署；負責人看過更正後的事實，於 2026-11-06 重新同意。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄 exact push CI 41753251393 與實際跑 product lane 的 PR CI 42068563588，維持 `passed`；ccb9553bb 的 receipt 移到 `historical_receipts`。
- gate-1、gate-4：維持 `blocked`，等負責人決定 HD-07。
- gate-3／5／6：本來就是 `blocked`，只更新 `release_sha`。

## 驗證

- `check_release_gate_registry.py --expected-sha 8e5872544…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=8e5872544…, expected_manifest_digest=sha256:22c22bdc…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`8e5872544..head` 只改 `docs/evidence/`。
