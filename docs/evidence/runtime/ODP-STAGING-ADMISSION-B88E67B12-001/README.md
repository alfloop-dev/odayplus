# ODP-STAGING-ADMISSION-B88E67B12-001：staging admission 換綁到 b88e67b12

本資料夾記錄 core-ops candidate `b88e67b128d8f11f9653dd3acf518fe87cb1832c` 進入 ephemeral staging 的 registry 綁定。只有 evidence，沒有 lease，也沒有部署。

## 為什麼換 candidate

上一個 candidate `ccb9553bb` 的 staging deploy（Runtime Release run 41464086945 到 41564634734）一路修到 staging 建立步驟：deployer IAM 由負責人同意後以 gcloud 補授；最後一次 API 容器因資料庫尚未 migration 而無法啟動，因為 migration 原本在 verify 才執行。唯讀盤點又發現 verify 的 10 個 stage 中有 9 個必敗。PR #1641 一次修正 create 順序與這些 stage；它不屬於 evidence 路徑，所以換成包含修正的新 candidate。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `b88e67b128d8f11f9653dd3acf518fe87cb1832c`（dev tip，PR #1641 合併） |
| staging build | Runtime Release run [41582327336](https://github.com/alfloop-dev/odayplus/actions/runs/41582327336)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（檔案 sha256 `d7a831d5…`，`manifest_digest` `sha256:f194e6ef…`）；components、SBOM、signature 一起換 |
| exact CI | push CI [41582285623](https://github.com/alfloop-dev/odayplus/actions/runs/41582285623)，11 個 job 全部成功 |
| dev 實際部署 | Runtime Release run [41586245245](https://github.com/alfloop-dev/odayplus/actions/runs/41586245245) 第 3 次嘗試成功：live E2E 77 項全過，preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job、public-egress probe 都通過 |
| npm audit | staging build 的 production 相依 0 個 finding |

dev 部署第 1、2 次嘗試（06:14Z、07:37Z）都因 `admin:scoped_business_read` 經 Web BFF 回 504 而自動回滾：那段時間新 revision 的每個 API 請求都慢了約 15 倍（管理者 bootstrap 11.49 秒，超過 BFF 10 秒上限）。程式、Cloud Run 設定、環境變數、映像檔基底 layer、資料量、DB CPU、VPC connector 都已比對排除；第 3 次（10:26Z）延遲恢復正常（中位數 0.077 秒、bootstrap 0.83 秒）。確切成因未查出，記錄為暫時性環境問題。`hosted-artifacts/run-41586245245` 是第 3 次的報告。

## 人工決定（`humanops-staging-admission-authorization-20261103.json`）

負責人蔡尚志於 2026-11-03 在 Claude Code 對話中同意，由 Claude 轉錄，不是外部身分系統的簽章。取代只適用於 ccb9553bb 的 `HUMANOPS-STAGING-ADMISSION-20261102B`。

- **HD-08c**：gate-2 Data Gate 標成 `passed-with-deviation`，只適用於這個 candidate 進入 staging，review_by 2026-11-17。
  - source contract：不適用，core-ops 關閉所有外部來源。
  - point-in-time（model-ready data）：不適用，core-ops 不使用模型。
  - freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。
- **HD-09c**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`。只授權一次 staging release（一筆請求、一張 lease、一次 deploy），GitHub `staging` environment 仍需另外核准，不包含 production。

## 其他 gate

- gate-0 Code Gate：以 exact CI 41582285623 重新綁 receipt，維持 `passed`；ccb9553bb 的 receipt 移到 `historical_receipts`。
- gate-1、gate-4：維持 `blocked`，等負責人決定 HD-07。
- gate-3／5／6：本來就是 `blocked`，只更新 `release_sha`。

## 驗證

- `check_release_gate_registry.py --expected-sha b88e67b12…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=b88e67b12…, expected_manifest_digest=sha256:f194e6ef…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`b88e67b12..head` 只改 `docs/evidence/`。
