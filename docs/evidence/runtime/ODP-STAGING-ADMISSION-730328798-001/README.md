# ODP-STAGING-ADMISSION-730328798-001：staging admission 綁定

本資料夾記錄 core-ops candidate `730328798ce8456ae698bf5ade35d5e75f5ff662` 進入 ephemeral staging 的 registry 綁定。只有 evidence，沒有 lease，也沒有部署。

## 綁定內容

| 項目 | 值 |
|---|---|
| candidate | `730328798ce8456ae698bf5ade35d5e75f5ff662`（dev tip，PR #1632 合併） |
| staging build | Runtime Release run [41363014110](https://github.com/alfloop-dev/odayplus/actions/runs/41363014110)，`release_profile=core-ops`，`initial_release_recovery=true` |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，由 build artifact 原檔複製（檔案 sha256 `e25b6754…`，`manifest_digest` `sha256:2c9159d5…`）；components、SBOM、signature 一起換，沒有沿用 6140d0ef 的任何成品 |
| exact CI | push CI [41283177551](https://github.com/alfloop-dev/odayplus/actions/runs/41283177551) 全綠 |
| dev 實際部署 | Runtime Release run [41285267801](https://github.com/alfloop-dev/odayplus/actions/runs/41285267801)：Cloud Run smoke、live E2E、worker／migration／scheduler job 驗證、public-egress probe 都通過 |

## 人工決定（`humanops-staging-admission-authorization-20261102.json`）

負責人蔡尚志於 2026-11-01T23:47Z（台北時間 11-02）在 Claude Code 對話中同意，由 Claude 轉錄，不是外部身分系統的簽章。

- **HD-08**：gate-2 Data Gate 標成 `passed-with-deviation`，只適用於這個 candidate 進入 staging。
  - source contract：不適用，core-ops 關閉所有外部來源。
  - point-in-time（model-ready data）：不適用，core-ops 不使用模型。
  - freshness／lineage：`check_live_production_data.py` 從未對這個 candidate 執行過，延後到 staging verify 與 production gate，不宣稱通過。
- **HD-09**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`。只授權一次 staging release（一張 lease、一次 deploy），GitHub `staging` environment 仍需另外核准，不包含 production。

## 其他 gate

registry 驗證要求七個 gate 都綁同一個 candidate：

- gate-0 Code Gate：以 exact CI 41283177551 重新綁 receipt，維持 `passed`。
- gate-1 Contract Gate：原本的 not-applicable 理由只適用於只改工具的 6140d0ef。730328798 含有產品變更，需要重做契約審查（HD-07），改為 `blocked`。
- gate-4 Security and Privacy Gate：原本的 H01／npm 偏差只核准給 6140d0ef。新 candidate 需要 Human/Ops 重新處置（HD-07），改為 `blocked`。
- gate-3／5／6：本來就是 `blocked`，只更新 `release_sha`。

gate-1 與 gate-4 綁定 dev。dev 自動部署走 2026-10-04 的 standing authorization，不讀這份 registry，所以不受影響。staging admission 只檢查 gate-2。

## 驗證

- `check_release_gate_registry.py --expected-sha 730328798…`：`RELEASE STATE: GO`，沒有 integrity error。
- `registry_admission_errors(environment="staging", release_sha=730328798…, expected_manifest_digest=sha256:2c9159d5…)` 回傳空清單。
- 分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`730328798..head` 只改 `docs/evidence/`。部署 dispatch 前，dev 上不可以合併其他非 evidence 變更，否則 workflow 的 candidate ancestry 檢查會拒絕。
