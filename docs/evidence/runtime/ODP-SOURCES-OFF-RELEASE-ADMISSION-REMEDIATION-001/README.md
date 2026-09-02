# ODP-SOURCES-OFF-RELEASE-ADMISSION-REMEDIATION-001

修正 Runtime Release 把「所有外部來源保持關閉」誤判為「缺少 masked snapshot」的
admission 語意。

- 任務狀態：實作完成，等待 review
- Owner：Claude2 · Reviewer：Codex2
- 量測 head：`b1b3c6420278de69ae08a463c70123234d031b81`

## 1. 問題

`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN` §3／§9／§14 規定本產品的常態姿態是
**16 個資料來源全部 disabled、零 provider credentials、public egress default-deny**，
逐來源啟用是獨立的法律與營運核准流程。

但 release manifest schema v2 把 `data_snapshot` 列為無條件必要欄位，
`validate_release_admission()` 也無條件要求它。結果是：

- sources-off release 根本沒有可綁定的 masked snapshot（沒有攝入第三方資料就沒有
  東西可遮蔽），卻被當成「缺少 artifact」而 fail closed；
- 唯一能放行的辦法變成填 placeholder snapshot 或手填 digest —— 那等於用假證據
  換取放行，比擋住更糟。

## 2. 修法

不放寬檢查，而是**換一種等價強度的資料面證據**。manifest 新增
`sources_off_attestation`，只在 `external_sources_expected_enabled` 為空時出現：

```json
{
  "provider_mode": "disabled",
  "egress_posture": "default-deny",
  "total_sources_audited": 16,
  "all_sources_disabled": true,
  "zero_credentials_present": true,
  "sources_inventory": [
    {"source_id": "...", "status": "disabled",
     "credentials_present": false, "public_egress": "denied"}
  ],
  "binding_digest": "sha256:..."
}
```

三件事讓它不是一句宣告：

1. **完整清點**：必須逐一列出 `EXTERNAL_SOURCE_INVENTORY` 的 16 個來源，缺一個、
   多一個、重複一個都會被拒；不能用單一布林值或短清單交差。
2. **綁定**：`binding_digest` 涵蓋 candidate SHA、component image digests 與
   `source_policy_digest`。attestation 因此無法從別的 release 搬過來、無法在
   rebuild 後沿用、也無法跨 source policy 變更存活。
3. **推導而非填寫**：build 階段從該 release SHA 上的 deploy workflow 讀出實際部署
   的 provider 設定（`ODP_EXTERNAL_PROVIDER_MODE`、有無接上 provider credential、
   有無接上 provider endpoint）再封裝。CLI、dispatch input 與 repository vars
   **都沒有**可以傳入 posture 或 binding digest 的管道。

## 3. Fail-closed 條件

| 情境 | 結果 |
|---|---|
| inventory 中任一來源 `status != "disabled"` | 拒絕 |
| 任一來源 `credentials_present` 為 true | 拒絕 |
| 任一來源 `public_egress != "denied"`／`egress_posture != "default-deny"` | 拒絕 |
| `provider_mode != "disabled"` | 拒絕 |
| inventory 未涵蓋全部 16 個來源 | 拒絕 |
| verdict 欄位與自身 inventory 不一致（重算 digest 也一樣） | 拒絕 |
| binding digest 屬於別的 candidate SHA／image digests／source policy | 拒絕 |
| `external_sources_expected_enabled` 非空卻帶 attestation | 拒絕 |
| attestation 與 `data_snapshot` 同時存在 | 拒絕 |
| 上一核准 release 仍綁著 `data_snapshot`，本次改用 attestation | 拒絕（anti-downgrade） |
| deploy workflow 沒有宣告 `ODP_EXTERNAL_PROVIDER_MODE` | 拒絕（不猜） |

最後一列的 anti-downgrade 是這次的核心：一旦某個 release 是以 masked snapshot
被放行的，下一個 release 不能靠宣告自己 sources-off 就擺脫 snapshot 驗證。
rollback binding 仍記著前一個 release 被放行時綁定的 snapshot，
posture attestation 不能取代它。

## 4. 未變更的嚴格路徑

`external_sources_expected_enabled` 非空時，行為與修改前完全一致：

- 必須綁定本次核准的 masked snapshot（`masked=true`、`content_sha256`、
  `data_contract_digest` 與 manifest 的 `data_contract_digest` 一致）；
- rollback manifest 的 object generation、content SHA 與 contract digest 驗證不變；
- 這條路徑上不接受 `sources_off_attestation`。

sources-off release 若已有 masked snapshot，也維持既有 snapshot 路徑，不會改寫成
attestation。

## 5. Single-path 保證

- 沒有新增 workflow：仍是 `.github/workflows/deploy-dev.yml` 一個 Runtime Release。
- 沒有新增 release manifest registry：`EXTERNAL_SOURCE_INVENTORY` 是既有
  `release_manifest.py` schema 模組內的常數，並由測試對
  `ODP-DEV-ROLLOUT-001` 的 provider-off 稽核報告與 runtime provider registry
  雙向比對，避免與現有清單漂移。
- 沒有新增 deployment entrypoint：只改既有 build handoff 與 manifest verifier。
- workflow 只多一個 `external_sources_enabled` dispatch input，用途是宣告
  「本次預期**啟用**哪些來源」（因而讓 masked snapshot 成為必要）；
  sources-off 本身沒有輸入管道。

## 6. 變更檔案

| 檔案 | 變更 |
|---|---|
| `delivery_toolchain/release/release_manifest.py` | attestation schema、binding digest、fail-closed 驗證、rollback binding、anti-downgrade |
| `delivery_toolchain/release/build_release_handoff.py` | 從 deploy workflow 推導 posture、build 端 anti-downgrade、嚴格路徑保留 |
| `.github/workflows/deploy-dev.yml` | `external_sources_enabled` input 與 `--external-source` 接線 |
| `tests/release/test_release_manifest.py` | 20 個 focused 正／負向測試 |
| `tests/release/test_build_release_handoff.py` | 12 個 focused 正／負向測試 |
| `tests/ops/test_deploy_workflow_contract.py` | 斷言 posture 沒有 dispatch 管道 |

## 7. 驗證

見 [`verification-receipt.json`](verification-receipt.json)。收據不含任何 secret
值：測試 fixture 只出現 GitHub Actions 的 `secrets.*` **參照字面**，
`delivery_toolchain/security/secret_scan.py` 於同一 head 通過。
