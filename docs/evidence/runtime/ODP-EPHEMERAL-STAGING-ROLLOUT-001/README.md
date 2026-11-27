# ODP-EPHEMERAL-STAGING-ROLLOUT-001：Ephemeral Staging 11 階段預演與收據封存（`odp-9de70f949915`）

本目錄封存候選版本 `9de70f949915706d1bad1bc25c24b681eeb902e9`（`release_id: odp-9de70f949915`、`release_profile: core-ops`、`manifest_digest: sha256:964d00090d8516b30d6329dcc5e0550cbab0342f9d32567b651003aa12ef0ccd`）於 GCP 隔離專案 `odayplus-runtime-20260825` 實際執行完成之 Ephemeral Staging 全套預演收據。

## 驗收條件逐項對照

1. **建立隔離 staging 並部署同一 digests**
   - Build handoff：`Runtime Release` run `45995201655`（封存於 `docs/evidence/gates/RELEASE_MANIFEST.json`）。
   - Admission 與單次 Supervisor 租約消耗：`hosted-artifacts/run-46024230572/release-admission-receipt-staging/release-admission-receipt.json`（`lease_id: lease-56ee417e9b6e02dae2b11bbf0e9f185f`、`admitted: true`）。
   - 隔離命名空間建立：`hosted-artifacts/run-46024230572/runtime-release-staging-lifecycle-receipts/staging-lifecycle-create.json`（`stg-odp-9de70f949915-5c64b623-api`、`-web`、Cloud Run Jobs、Cloud Scheduler、隔離 DB schema 與專屬 SA）。

2. **migration / E2E / worker / scheduler / backup / restore / rollback rehearsal 全部通過**
   - 11 階段完整預演收據：`hosted-artifacts/run-46024230572/runtime-release-staging-lifecycle-receipts/staging-rehearsal-receipt.json`（`stages_count: 11`、`success: true`、`errors: []`、`remote_state_verified: true`）。
   - 遠端驗證證明：`hosted-artifacts/run-46024230572/runtime-release-staging-validation/remote-staging-proof/staging-46024230572.json`（`ok: true`）。
   - 首次發布上線後撤銷／恢復 `allUsers` 下線演練（決定 D / HD-21）：`hd21-first-release-takedown-rehearsal/hd21-staging-takedown-rehearsal-receipt.json`（撤銷後 `https://console-staging.oday-plus.com.tw/login` 回 `403`、API 維持 `200`，恢復後回 `200` 且 IAM 與基準完全一致）。

3. **staging Web 自訂網域只使用 `console-staging.oday-plus.com.tw`，並驗證 DNS、受管憑證與帳密登入（Google OAuth 依 D15 記 `not-applicable`）**
   - Web 自訂網域僅綁定 `https://console-staging.oday-plus.com.tw`（經 `oday-staging-web-lb` 與 `ACTIVE` Google 受管憑證）。
   - 依架構決策 **D15**，Google OAuth 關閉（`auth_mode=local`），故 OAuth callback 檢查記為 `not-applicable`；本機帳密登入與首次登入強制換密碼已於 `live_e2e_gate` 及 `staging-admin` 登入實測通過，並簽發 4 組 `core-ops` 角色邀請（`hd21-first-release-takedown-rehearsal/uat-invitations-redacted-receipt.json`，不含任何私密 token）。

4. **第三方來源保持關閉**
   - `staging-rehearsal-receipt.json` 記錄 `external_sources_expected_enabled: []`、`public_egress: "default_deny"`，外部資料來源維持關閉。

5. **失敗時保留至 TTL，成功時等待 prod closeout 清理**
   - 前一輪已逾期並被取代之 `odp-8e5020a29bad` 已透過 `phase=cleanup`（run `46020319575`）完整銷毀 23/23 項雲端資源：`hosted-artifacts/run-46020319575/runtime-release-staging-retained-cleanup-receipts/staging-lifecycle-cleanup.json`。
   - 本輪通過驗證之 `odp-9de70f949915` 保留供 UAT 與正式環境 `staging_closeout` 對帳清理。

6. **產生 `staging-verified` receipts**
   - 彙總索引：`staging-verified-summary.json`。

7. **承接 `ODP-STAGING-RECOVERY-STORAGE-ACCEPTANCE-001` readiness 與實際 recovery bundle `location / generation / hash`**
   - 前置儲存就緒依據：`docs/evidence/runtime/ODP-STAGING-RECOVERY-STORAGE-ACCEPTANCE-001/recovery-storage-readiness.json`。
   - 實際寫入並讀回驗證之 GCS recovery bundles（見 `hosted-artifacts/run-46024230572/runtime-release-staging-lifecycle-receipts/staging-recovery-final.json`）：
     - Initial lifecycle：`gs://oday-staging-recovery-odayplus-runtime-20260825/oday-plus/ephemeral-staging/odp-9de70f949915/bundle/odp-9de70f949915-5c64b623.lifecycle.json`（`generation: 1795793421076706`、`sha256: bfd38f3e89909d915f9952e9fb27bc8b999325f6d6f1b07919908eb0928f1d0b`）
     - Final hold/verified lifecycle：`gs://oday-staging-recovery-odayplus-runtime-20260825/oday-plus/ephemeral-staging/odp-9de70f949915/bundle/hold-46024230572-1-odp-9de70f949915-5c64b623.lifecycle.json`（`generation: 1795794185986489`、`sha256: 0df87f04c4075ed4331d9616fe9dd0a8426e9dde58053b9f20d3e17228e5beff`）
