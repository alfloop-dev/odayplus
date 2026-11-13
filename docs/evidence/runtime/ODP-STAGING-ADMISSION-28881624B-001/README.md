# ODP-STAGING-ADMISSION-28881624B-001：staging admission 換綁到 28881624b

本資料夾記錄 core-ops candidate `28881624b834fd711e98b29726497107485b0961` 進入 ephemeral staging 的 registry 綁定。只有 evidence，沒有 lease，也沒有部署。

## 為什麼換 candidate

dev 上最後合併的綁定是 `8e5872544`，lease 登記前被 PR #1649（改 CI 工作流程）作廢。下一個 candidate `c4f5771ff` 也在 lease 前被 #1658 到 #1665 的 CI、toolchain 與產品修改作廢，它的 registry PR #1655 沒有合併就關閉，所以這次直接從 8e5872544 的記錄換綁。candidate ancestry 只放行控制面路徑（`.orchestrator/` 與 ai-status 腳本）。這一輪派工沒有凍結，由負責人決定。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `28881624b834fd711e98b29726497107485b0961`（dev tip，PR #1666 合併） |
| staging build | Runtime Release run [42423504694](https://github.com/alfloop-dev/odayplus/actions/runs/42423504694)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（檔案 sha256 `2a447a3a…`，`manifest_digest` `sha256:40d94651…`）；components、SBOM、signature 一起換 |
| push CI | [42372983710](https://github.com/alfloop-dev/odayplus/actions/runs/42372983710) 整體 success，只跑了 `orchestrator`、`change-scope`、`product` 聚合與 `Request automatic dev release`；7 個 product lane 被 change scope 跳過 |
| merge group CI | [42372195272](https://github.com/alfloop-dev/odayplus/actions/runs/42372195272) 同樣跳過 7 個 product lane |
| product lane 最後實跑 | dev 上的 5eb7cba6c（PR #1661 合併）：push CI [42356697819](https://github.com/alfloop-dev/odayplus/actions/runs/42356697819) 與 merge group CI [42354651502](https://github.com/alfloop-dev/odayplus/actions/runs/42354651502)，`product-lint-unit`、`product-db`、`product-api-contract`、`product-e2e-gate`、`product-security`、`performance-gate`、`product-node` 全部 success |
| 與 5eb7cba6c 的差異 | 6 個路徑，分類器判定全部屬於 development tooling：`.orchestrator/supervisor.py`、`.orchestrator/test_supervisor.py`、`.orchestrator/worker_failure_policy.py`、`delivery_toolchain/e2e/product_e2e_receipt.py`、`tests/e2e/test_acceptance_coverage.py`、`tests/tooling/test_ci_product_parallel.py`；沒有產品路徑 |
| 本機補充（非 CI） | 收據程式與它的測試屬於 `product-lint-unit`，在 CI 跳過 lane 後沒有跑過。在 916d1457f（這兩個檔案內容相同）的 blob:none sparse clone 本機執行 `test_acceptance_coverage.py` 與 `test_release_manifest.py`：156 通過、1 失敗；失敗的 `test_no_deleted_specs_referenced_and_inventory_consistent` 是本機沒有安裝 `@playwright/test` |
| dev 實際部署 | Runtime Release run [42373586318](https://github.com/alfloop-dev/odayplus/actions/runs/42373586318) 第一次就通過：live E2E 77 項全過（含 `worker:idempotent_replay`），preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job、public-egress probe 都通過 |
| npm audit | staging build 的 production 相依 0 個 finding |

這份記錄不宣稱 product lane 在 28881624b 本身跑過。這支 registry PR 只改 `docs/evidence/`，它的 CI 會在 28881624b 加本 commit 上實跑 7 個 lane，核准前逐項確認。

## 人工決定（`humanops-staging-admission-authorization-20261107.json`）

負責人蔡尚志於 2026-11-07T23:24:05Z 在 Claude Code 對話中同意，由 Claude 轉錄，不是外部身分系統的簽章。

- **HD-08g**：gate-2 Data Gate 標成 `passed-with-deviation`，只適用於這個 candidate 進入 staging，review_by 2026-11-20。
  - source contract：不適用，core-ops 關閉所有外部來源（manifest 記錄 16 個來源全關、沒有憑證）。
  - point-in-time（model-ready data）：不適用，core-ops 不使用模型。
  - freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。
- **HD-09g**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`。只授權一次 staging release（一筆請求、一張 lease、一次 deploy），GitHub `staging` environment 仍需另外核准，不包含 production。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄上述 CI，維持 `passed`；8e5872544 的 receipt 移到 `historical_receipts`。
- gate-1、gate-4：維持 `blocked`，等負責人決定 HD-07。
- gate-3／5／6：本來就是 `blocked`，只更新 `release_sha`。

## 驗證

- `check_release_gate_registry.py --expected-sha 28881624b…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=28881624b…, expected_manifest_digest=sha256:40d94651…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`28881624b..head` 只改 `docs/evidence/`。
