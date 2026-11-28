# ODP-PRODUCTION-ADMISSION-9DE70F949-001：鏈 V `core-ops` Production GO 閘門准入封存（`9de70f949915`）

本目錄封存候選提交 `9de70f949915706d1bad1bc25c24b681eeb902e9`（`odp-9de70f949915`、`release_profile: core-ops`、`manifest_digest: sha256:964d00090d8516b30d6329dcc5e0550cbab0342f9d32567b651003aa12ef0ccd`）完成 Ephemeral Staging 11 階段預演（run `46024230572`，PR `#1774` 已合併並結案 `ODP-EPHEMERAL-STAGING-ROLLOUT-001`）、`HD-21` 首次發布 Web `allUsers` 撤銷／恢復演練、`HD-20` 正式環境 Cloud SQL DR 備份還原演練與 `HD-13` UAT 簽核後，由負責人蔡尚志（`Human/Ops`）於 `2026-11-28T03:43:06Z`（`HUMANOPS-PRODUCTION-GO-20261128A`，`step_index 8666`）具名核准推進 `RELEASE_GATE_REGISTRY.json` 至 `stage=staging-verified`、`admission_target=production`、`decision=go` 之完整證據包。

## 檔案清單

- `humanops-production-go-authorization-20261128.json`：負責人於演練與 UAT 完成後之明確具名授權紀錄（涵蓋 HD-13..HD-17、HD-23..HD-26）。
- `uat-signoff-core-ops-20261128.yaml`：`core-ops` UAT 正式簽核單（零 P0/P1 缺陷，明確記載排除模組與單租戶 `UAT-CO-11` 替代驗證方式）。
- `production-dr-drill/production-dr-drill-receipt.json`：`oday-prod-foundation-sql` 備份還原演練收據（on-demand 備份 `1795819475472`、billing 恢復後新自動備份 `1795802400000`、還原 RTO `4.93` 分鐘、建機加還原 `8.25` 分鐘、臨時執行個體已刪除並誠實揭露首次檢查 Job `--args` 逗號切分細節與來源 VPC 內唯讀基準）。
- `production-environment-iam-readback-20261128.json`：正式環境 AR、IAM、環境變數、SSL 憑證、WORM 鎖定 7 年與 Cloud Monitoring 6 條告警政策讀回紀錄。
- `release-notes-core-ops-odp-9de70f949915.md`：核准後之 `core-ops` 正式上線說明（HD-23）。
- `evidence-manifest-rtm-9de70f949915.json`：可由內容雜湊重算驗證之 RTM 與 Gate 證據清單（`sha256:61ae9694d655cbe057d5e4ec4308fa5685427b3cc0b456f4155ddff45db15c74`）。
- 同步併入 `docs/runbooks/core-ops.md`：正式核准之 `core-ops` 營運 Runbook。
