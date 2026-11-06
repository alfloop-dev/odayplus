# ODP-STAGING-ADMISSION-C4F5771FF-001：staging admission 換綁到 c4f5771ff

本資料夾記錄 core-ops candidate `c4f5771ff3e047dd4c0983d52ed92ed5a65579ba` 進入 ephemeral staging 的 registry 綁定。只有 evidence，沒有 lease，也沒有部署。

## 為什麼換 candidate

上一個 candidate `8e5872544` 的 registry（PR #1646）合併後、lease 登記前，PR #1649 改了 CI 工作流程，不在 candidate ancestry 的白名單內，所以它作廢了。負責人 2026-11-06 決定暫停 Supervisor 派工（`ready_dispatcher.enabled=false`），讓這個 candidate 能走到 lease；deploy 觸發後恢復。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `c4f5771ff3e047dd4c0983d52ed92ed5a65579ba`（dev tip，PR #1653 合併） |
| staging build | Runtime Release run [42209759190](https://github.com/alfloop-dev/odayplus/actions/runs/42209759190)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（檔案 sha256 `df50043e…`，`manifest_digest` `sha256:b591ab37…`）；components、SBOM、signature 一起換 |
| push CI | [42164409991](https://github.com/alfloop-dev/odayplus/actions/runs/42164409991) 整體 success，只跑了 `orchestrator`、`change-scope`、`product` 聚合與 `Request automatic dev release`；7 個 product lane 被 change scope 跳過 |
| merge group CI | [42163390686](https://github.com/alfloop-dev/odayplus/actions/runs/42163390686) 同樣跳過 7 個 product lane |
| product lane 最後實跑 | PR CI [42147962456](https://github.com/alfloop-dev/odayplus/actions/runs/42147962456)，在 fa374a69d（8e5872544 加 `docs/evidence` commit）：`product-lint-unit`、`product-db`、`product-api-contract`、`product-e2e-gate`、`product-security`、`performance-gate`、`product-node` 全部 success |
| 與 8e5872544 的差異 | 只有 `.orchestrator/`、三個非部署用 CI 工作流程（`ci.yml`、`emgi-consumer-boundary.yml`、`tooling-scope-review-gate.yml`）、`scripts/test_ai_status.py`、`tests/tooling/`；沒有產品路徑，`deploy-dev.yml` 未變 |
| dev 實際部署 | Runtime Release run [42165191261](https://github.com/alfloop-dev/odayplus/actions/runs/42165191261) 第一次就通過：live E2E 77 項全過，preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job、public-egress probe 都通過 |
| npm audit | staging build 的 production 相依 0 個 finding |

這份記錄不宣稱 product lane 在 c4f5771ff 本身跑過。這支 registry PR 的 CI 會在 c4f5771ff 加本 commit 上再跑一次，核准前逐項確認。

## 人工決定（`humanops-staging-admission-authorization-20261106.json`）

負責人蔡尚志於 2026-11-06T11:34:40Z 在 Claude Code 對話中同意，由 Claude 轉錄，不是外部身分系統的簽章。

- **HD-08f**：gate-2 Data Gate 標成 `passed-with-deviation`，只適用於這個 candidate 進入 staging，review_by 2026-11-20。
  - source contract：不適用，core-ops 關閉所有外部來源（manifest 記錄 16 個來源全關）。
  - point-in-time（model-ready data）：不適用，core-ops 不使用模型。
  - freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。
- **HD-09f**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`。只授權一次 staging release（一筆請求、一張 lease、一次 deploy），GitHub `staging` environment 仍需另外核准，不包含 production。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄上述 CI，維持 `passed`；8e5872544 的 receipt 移到 `historical_receipts`。
- gate-1、gate-4：維持 `blocked`，等負責人決定 HD-07。
- gate-3／5／6：本來就是 `blocked`，只更新 `release_sha`。

## 驗證

- `check_release_gate_registry.py --expected-sha c4f5771ff…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=c4f5771ff…, expected_manifest_digest=sha256:b591ab37…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`c4f5771ff..head` 只改 `docs/evidence/`。
