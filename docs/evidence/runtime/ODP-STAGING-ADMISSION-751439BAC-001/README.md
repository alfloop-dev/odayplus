# ODP-STAGING-ADMISSION-751439BAC-001：staging admission 換綁到 751439bac

本資料夾記錄 core-ops candidate `751439bacac1ac7396bf0300c39eaaae87f21bd7` 進入 ephemeral staging 的 registry 綁定。只有 evidence，沒有 lease，也沒有部署。

## 為什麼換 candidate

dev 上的綁定 `28881624b`（PR #1669）在 lease 前被 PR #1651 作廢：#1651 改了 `deploy-dev.yml`、`product_ops/deployment` 與 release 工具，不在 candidate ancestry 的白名單內。#1651 也讓 dev 自動部署在 scheduler 查詢時失敗（Deploy Dev 42440886093），PR #1670 修正後才有這個 candidate。這一輪派工沒有凍結，由負責人決定。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `751439bacac1ac7396bf0300c39eaaae87f21bd7`（dev tip，PR #1670 合併） |
| staging build | Runtime Release run [42757249880](https://github.com/alfloop-dev/odayplus/actions/runs/42757249880)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（檔案 sha256 `42023888…`，`manifest_digest` `sha256:955b2eba…`）；components、SBOM、signature 一起換 |
| push CI | [42496350199](https://github.com/alfloop-dev/odayplus/actions/runs/42496350199)，在 751439bac 本身實跑 7 個 product lane，全部 success |
| merge group CI | [42492640644](https://github.com/alfloop-dev/odayplus/actions/runs/42492640644)，同樣在 751439bac 實跑 7 個 product lane，全部 success |
| dev 實際部署 | Runtime Release run [42500035692](https://github.com/alfloop-dev/odayplus/actions/runs/42500035692) 第一次就通過：live E2E 77 項全過（含 `worker:idempotent_replay`），preflight、migration 相容、Cloud Run smoke、worker／migration／scheduler job、public-egress probe 都通過 |
| npm audit | staging build 的 production 相依 0 個 finding |

## 人工決定（`humanops-staging-admission-authorization-20261110.json`）

負責人蔡尚志於 2026-11-10T11:54:21Z 在 Claude Code 對話中同意，由 Claude 轉錄，不是外部身分系統的簽章。

- **HD-08h**：gate-2 Data Gate 標成 `passed-with-deviation`，只適用於這個 candidate 進入 staging，review_by 2026-11-24。
  - source contract：不適用，core-ops 關閉所有外部來源（manifest 記錄 16 個來源全關、沒有憑證）。
  - point-in-time（model-ready data）：不適用，core-ops 不使用模型。
  - freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。
- **HD-09h**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`。只授權一次 staging release（一筆請求、一張 lease、一次 deploy），GitHub `staging` environment 仍需另外核准，不包含 production。

## 其他 gate

- gate-0 Code Gate：receipt 如實記錄上述 CI，維持 `passed`；28881624b 的 receipt 移到 `historical_receipts`。
- gate-1、gate-4：維持 `blocked`，等負責人決定 HD-07。
- gate-3／5／6：本來就是 `blocked`，只更新 `release_sha`。

## 驗證

- `check_release_gate_registry.py --expected-sha 751439bac…` 沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=751439bac…, expected_manifest_digest=sha256:955b2eba…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`751439bac..head` 只改 `docs/evidence/`。
