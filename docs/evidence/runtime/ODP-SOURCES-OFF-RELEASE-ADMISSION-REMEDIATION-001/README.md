# ODP-SOURCES-OFF-RELEASE-ADMISSION-REMEDIATION-001

修正 Runtime Release 把「所有外部來源保持關閉」誤判為「缺少 masked snapshot」的
admission 語意。

- 任務狀態：實作完成，等待正式 review submission
- Owner：Claude2 · Reviewer：Codex2（第二次 reopen 後由 Codex 移交）
- 量測 code head：`f7a1c7143764f2b1823373b113c4e8d5abc58b2a`
- base advance：merge commit `9f59424740fd606f47041a2df23d21c76ab12faa`，包含 `origin/dev@a5d85428597ff196973105ca68ca7e042dd804e1`

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
  "egress_evidence": {
    "kind": "runtime-release-egress-contract",
    "cloud_run_egress": "ALL_TRAFFIC",
    "firewall_egress": "default-deny",
    "workflow_vpc_binding": "verified",
    "deploy_entrypoint_vpc_binding": "verified",
    "runtime_probe_wiring": "verified",
    "runtime_probe": "public_egress_denied",
    "runtime_probe_receipt": ".odp_data/deployment/public-egress-probe.json",
    "resolved_cloud_run_egress": "ALL_TRAFFIC",
    "runtime_probe_receipt_content_digest": "sha256:<semantic receipt content>",
    "provider_credentials_runtime": "absent",
    "proof_source": [".github/workflows/deploy-dev.yml",
                     "product_ops/deployment/deploy_cloud_run_waji.sh",
                     "infra/terraform/cloud_run.tf",
                     "infra/terraform/network.tf",
                     "product_ops/deployment/staging_lifecycle.py",
                     "product_ops/deployment/cloud_run_job_entrypoint.py"],
    "contract_digest": "sha256:<computed over the checked-in contract files>"
  },
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
   有無接上 provider endpoint），並讀取同一 Runtime Release 的 VPC connector/
   egress wiring，並把 build environment 實際解析到的
   `ODP_CLOUD_RUN_VPC_EGRESS` 綁進證據；再封裝 Cloud Run `ALL_TRAFFIC`、Terraform
   default-deny firewall 與 promotion 前 public-egress deny probe、secret-free runtime
   receipt content digest 及 contract digest 證據。CLI、dispatch input 與 repository vars **都沒有**可以
   傳入 posture 或 binding digest 的管道。

## 2.1 posture 怎麼在不認得任何 provider 的前提下推導

第一版把 provider credential 與 endpoint 的變數名抄了一份到
`delivery_toolchain/release/`，好讓 build 階段知道要找什麼。那份抄寫本身違反
external-data boundary（`odayplus.legacy-external-data-disposition.v2`）：那份封閉
清單屬於 frozen registry，disposition record 只授權 registry、deployment wiring 與
測試套件持有它，`delivery_toolchain/release/` 不在其中。六個 architecture 測試因此
變紅。

現在改成**形狀判斷**，release 端不再記得任何 provider：

- **歸屬**：一個接線變數屬於哪個 source，由 source id 自己的字詞決定。
  `poi_snapshot` 的識別字是 `POI`，所以名稱帶 `POI` 的變數算在它頭上。
  `snapshot`／`event`／`raw`／`result`／`daily`／`store` 這些多個 source 共用的字
  會先被丟掉，否則 `competitor_store_snapshot` 會吃掉
  `store_opening_authority_snapshot` 的 attestation。
- **分類**：用通用的安全名詞字尾判斷是 credential（`API_KEY`／`TOKEN`／`SECRET`／
  `ATTESTATION`／`AUTH_STATUS`…）、endpoint（`URL`／`URI`／`ENDPOINT`／`HOST`）或
  posture flag（`STATUS`／`MODE`／`ENABLED`）。`AUTH_STATUS` 排在 `STATUS` 前面，
  因為它守的是 credential。
- **認不出來就當 credential**：不熟悉的秘密不該成為 sources-off release 被放行的
  理由。
- **posture flag 是觀察到的事實**：來源自己的 status 變數若有接線而值不是
  `disabled`，就以它為準把該來源標成 enabled。「16 個來源全關」因此不再只是一句
  由 `ODP_EXTERNAL_PROVIDER_MODE` 推出來的宣告。

覆蓋範圍反而變大：原本的對照表只列了 6 個來源的變數，形狀判斷涵蓋全部 16 個。
名單本身移到 `tests/release/`（disposition record 明文允許持有 credential 名稱的
路徑），由測試證明形狀判斷重現 runtime registry 的每一個 credential 與 endpoint，
且沒有任何一個變數被歸屬到兩個來源。

這也是防漂移的機制：registry 若新增一個形狀判斷認不出來、或歸屬不到任何來源的
credential，這幾個測試就會紅，而不是讓 sources-off admission 悄悄少檢查一項。
形狀判斷本身仍有極限——名稱不帶來源字詞的 credential 不會被歸屬——所以把
「registry 是封閉清單」這件事釘在測試裡，比在 release 端多抄一份名單更能撐住。

## 3. Fail-closed 條件

| 情境 | 結果 |
|---|---|
| inventory 中任一來源 `status != "disabled"` | 拒絕 |
| 任一來源 `credentials_present` 為 true | 拒絕 |
| 任一來源 `public_egress != "denied"`／`egress_posture != "default-deny"` | 拒絕 |
| `provider_mode != "disabled"` | 拒絕 |
| workflow 缺少 Cloud Run VPC connector 或 egress binding | 拒絕 |
| deploy entrypoint 未把 connector/egress 傳給 Cloud Run | 拒絕 |
| deploy entrypoint 未在 promotion 前執行 public-egress deny probe | 拒絕 |
| probe 未確認候選 worker job 為 `ALL_TRAFFIC` | 拒絕 |
| public-egress probe receipt 未保留或 wiring 未綁定 | 拒絕 |
| resolved egress 不是 `ALL_TRAFFIC`，或 receipt content digest 不符 | 拒絕 |
| probe timeout、DNS/未知網路錯誤被當成 deny | 拒絕（只接受明確 network-policy errno） |
| Cloud Run IaC 不是 `ALL_TRAFFIC` 或 Terraform firewall contract 失效 | 拒絕 |
| egress evidence 缺欄位、credential runtime 非 absent、或 contract digest 不符 | 拒絕 |
| inventory 未涵蓋全部 16 個來源 | 拒絕 |
| verdict 欄位與自身 inventory 不一致（重算 digest 也一樣） | 拒絕 |
| binding digest 屬於別的 candidate SHA／image digests／source policy | 拒絕 |
| `external_sources_expected_enabled` 非空卻帶 attestation | 拒絕 |
| attestation 與 `data_snapshot` 同時存在 | 拒絕 |
| 上一核准 release 仍綁著 `data_snapshot`，本次改用 attestation | 拒絕（anti-downgrade） |
| deploy workflow 沒有宣告 `ODP_EXTERNAL_PROVIDER_MODE` | 拒絕（不猜） |
| 來源自己的 status 變數有接線但值不是 `disabled` | 拒絕（以觀察值為準，不採信宣告） |
| 歸屬到某來源的變數字尾認不出來 | 當成 credential 而拒絕 |

最後一列的 anti-downgrade 是這次的核心：一旦某個 release 是以 masked snapshot
被放行的，下一個 release 不能靠宣告自己 sources-off 就擺脫 snapshot 驗證。
rollback binding 仍記著前一個 release 被放行時綁定的 snapshot，
posture attestation 不能取代它。

## 4. 未變更的嚴格路徑

`external_sources_expected_enabled` 非空時，行為與修改前完全一致：

- 必須綁定本次核准的 masked snapshot（`masked=true`、`object_generation`、`content_sha256`、
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
| `delivery_toolchain/release/release_manifest.py` | attestation schema、VPC/firewall contract evidence、binding digest、fail-closed 驗證、rollback binding、anti-downgrade；provider 名單改為形狀判斷（§2.1） |
| `delivery_toolchain/release/build_release_handoff.py` | 從 deploy workflow 推導 posture 與 egress wiring、build 端 anti-downgrade、嚴格路徑保留；改為列舉接線變數再判形狀，並讀取 status flag |
| `delivery_toolchain/release/release_receipts.py` | 將 probe receipt 納入既有 literal artifact allowlist |
| `delivery_toolchain/release/check_release_environment.py` | deploy environment 必須解析 VPC connector 與 egress，避免空值退回 public path |
| `product_ops/deployment/deploy_cloud_run_waji.sh` | sources-off 強制 connector、`ALL_TRAFFIC`，並在 promotion 前執行 public-egress probe、寫入 secret-free receipt；probe 相關的 python 一律走 `run_locked_python` |
| `.github/workflows/deploy-dev.yml` | `external_sources_enabled` input、`--external-source` 接線與 probe receipt allowlist |
| `tests/release/test_release_manifest.py` | 20 個 focused 正／負向測試；registry 對照改為證明形狀判斷與 runtime registry 等價且歸屬唯一 |
| `tests/release/test_build_release_handoff.py` | 12 個 focused 正／負向測試 |
| `tests/ops/test_deploy_workflow_contract.py` | 斷言 posture 沒有 dispatch 管道 |
| `tests/ops/test_cloud_run_live_deployment.py` | resolver 改釘「一份實作」而非 caller 數；補 sources-off deploy 的 VPC/manifest fixture；把 `python3 -c` 一併關上 |
| `tests/ops/test_cloud_run_job_entrypoint.py` | probe 只接受明確 network-policy errno 的 focused 測試 |
| `product_ops/deployment/cloud_run_job_entrypoint.py` | `public-egress-probe` 子命令與 secret-free runtime receipt |
| `tests/release/test_release_environment_precheck.py` | deploy environment 必須解析 VPC connector/egress |
| `tests/release/test_release_manifest_cli.py`、`tests/release/test_runtime_admission.py` | 既有 fixture 補上 `object_generation` |
| `docs/evidence/runtime/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001/verify_build_handoff_wiring.py` | 前一個 task 的 evidence 驗證腳本：`object_generation` 成為必要欄位後，fixture 必須跟著補，否則該腳本會失敗 |

## 7. 驗證

見 [`verification-receipt.json`](verification-receipt.json)。全部在 code head
`f7a1c714` 量測，收據不含任何 secret 值。

第二次 reviewer reopen 指出的 9 個 product CI failure 已逐一量測為綠：

| 量測 | exit | 秒 |
|---|---|---|
| task 指定 focused selection（255 tests） | 0 | 12.27 |
| `tests/architecture/test_external_data_boundary.py`（原本紅 6 個） | 0 | 58.45 |
| `tests/ops/test_cloud_run_live_deployment.py`（原本紅 3 個） | 0 | 51.09 |
| runtime probe／receipt focused tests | 0 | 5.16 |
| CI orchestrator job 測試範圍 | 0 | 29.41 |
| `check_code_boundaries.py` | 0 | 6.60 |
| ruff（orchestrator 與 product 兩個 job 的範圍） | 0 | 0.11／0.09 |
| `bash -n` deploy entrypoint | 0 | 0.01 |

**本機沒有跑完的**：完整 product suite（`pytest ... tests modules apps shared
models -n auto`）在這台機器上 25 分鐘只跑到 8%，估計要數小時，因此中止；依
verification evidence policy 記為 `interrupted`，不是 pass。完整 product gate 以
PR CI 的 exact head 結果為準。

**本機已知的無關紅燈**：`tests/security` 有 9 個 failure，全部是 SBOM／OSS
notice／license attestation 與本機已安裝套件樹的比對。本 branch 對 `origin/dev`
的 18 個變更檔案不含 `sbom.json`、`package-lock.json`、`uv.lock` 或任何 license
artifact，所以在 `origin/dev` 上以相同環境會得到相同結果；CI 會先跑 `npm ci` 與
`uv sync`，本機沒有。

probe 行為未變更：只接受明確 network-policy errno，並由 Cloud Logging readback
驗證實際 runtime receipt 的 candidate、manifest、resolved egress 與 semantic content
digest 後才保留 evidence。
