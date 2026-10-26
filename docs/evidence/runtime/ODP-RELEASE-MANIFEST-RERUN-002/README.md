# ODP-RELEASE-MANIFEST-RERUN-002：最新 dev 的 release artifact 重建與 fail-closed 收據

## 結論

最新 `origin/dev` 為 `a027fa1c3935360e6fc4b3bd073cd91cbee07548`。本次針對這個
candidate 重新建立 release manifest，結果是 **BLOCKED**：這個 candidate 沒有任何
container image、沒有綁定的 release workflow run、沒有 SBOM、沒有 Cosign 簽章，
而且 GitHub `dev` environment 缺少 release lease 設定，hosted 的 Runtime Release
根本無法通過 admission。

因此 `docs/evidence/gates/RELEASE_MANIFEST.json` 以 `release_status: "blocked"`
重建，`components` 是空的，`sbom_refs` 與 `signature_refs` 維持空陣列，
`release-receipts-index.json` 的 `total_receipts` 為 `0`，**不產生任何部署成功收據**。

## 為什麼必須重跑，而不是沿用上一份 manifest

上一份 manifest（`odp-20260826-001`，candidate `ace4265b…`）確實驗到六個真實
image digest，但它已經不能代表現在的 dev：

```
release.candidate_sha 'ace4265b…' is an ancestor of expected SHA 'a027fa1c…',
but intervening commits touch non-evidence paths: delivery_toolchain/release/
check_runtime_admission.py, delivery_toolchain/release/release_manifest.py,
delivery_toolchain/security/sign_images.sh, docs/audits/code-boundary-inventory.csv,
tests/e2e/test_release_gate_registry.py, tests/ops/test_dev_rollout.py,
tests/release/test_release_manifest.py, tests/release/test_release_manifest_cli.py,
tests/release/test_sign_images.py
```

（`check_release_gate_registry.py --expected-sha a027fa1c…`，`EXIT=1`，
transcript §1 L46。）

registry 的 evidence-only ancestor 規則允許 manifest 落後 dev，前提是中間只動
evidence 路徑。這次中間動到 `delivery_toolchain/` 與 `tests/`，所以舊 candidate 的
image digest **不得**沿用到新 candidate 上。上一份證據目錄原封不動保留。

## Candidate manifest

| 欄位 | 值 |
|---|---|
| Release ID | `odp-20260826-002` |
| Candidate SHA | `a027fa1c3935360e6fc4b3bd073cd91cbee07548` |
| Manifest | `docs/evidence/gates/RELEASE_MANIFEST.json` |
| Manifest digest | `sha256:18582f5600585d6c492690fa1ed013f2d9b0fefd395a132f36325ecabe1eb1e2` |
| Registry | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev` |
| Status | `blocked` |
| Components | 0 |
| Supersedes | `odp-20260826-001` (`ace4265b…`) |

## 五個 P0 blocker

每一項都對應 `release-candidate-transcript.txt` 中一段逐字輸出（指令、合併後的
stdout/stderr、exit code），不是散文結論。harness 直接執行指令並在任何 pipeline
之前讀 `$?`，所以沒有 exit code 被 pipe 吃掉。

| Blocker | 觀察 | Transcript |
|---|---|---|
| `…-IMAGES-002` | 11 個 repository 名稱的 `release-a027fa1c…` tag 全部 `not found`、`EXIT=1` | §2 L117 |
| `…-WORKFLOW-002` | `gh run list --workflow deploy-dev.yml --commit a027fa1c…` 回 `[]`、`EXIT=0` | §3 L199 |
| `…-COSIGN-002` | `command -v cosign` → `EXIT=1`；`.sig` 探測 `not found` | §4 L225、§5 L256 |
| `…-SBOM-002` | `command -v syft` → `EXIT=1`；SBOM 探測 `not found` | §4 L231、§5 L249 |
| `…-LEASE-CONFIG-002` | `dev` environment 沒有 `ODP_RELEASE_LEASE_STATE_URI` 與 `ODP_RELEASE_LEASE_PUBLIC_KEY` | §6 L275 |

### 為什麼 image 探測涵蓋 11 個名稱

`dev` 與 `staging` 共用同一個 Artifact Registry repo（`oday-plus-dev`），但服務
名稱不同。為了避免把「名稱猜錯」誤判成「image 不存在」，探測同時涵蓋 dev 名稱
（`oday-api`、`oday-web`、`oday-worker`、`oday-scheduler`、`oday-migration`）與
staging 名稱（`oday-staging-*`、`oday-data-platform`）。11 個全部 `not found`。

### 根因是 LEASE-CONFIG-002

其餘四個 blocker 都是它的下游。`.github/workflows/deploy-dev.yml`（在 candidate
SHA 上名為 `Runtime Release`，且**只有** `workflow_dispatch` trigger）的 admission
job 有這一段（workflow L122）：

```bash
if [[ ! "${RELEASE_LEASE_STATE_URI}" =~ ^gs://[^/]+/.+ ]]; then
  echo "Error: durable lease admission requires a shared gs://bucket/prefix state URI." >&2
  exit 1
fi
```

`gh variable list --env dev --json name` 列出 38 個變數，其中沒有
`ODP_RELEASE_LEASE_STATE_URI`，也沒有 `ODP_RELEASE_LEASE_PUBLIC_KEY`；repository
scope 回 `[]`，沒有 fallback。`staging` 與 `production` 兩個 environment 都有這兩個
變數。所以 dev release 在拿到 lease 之前就會被擋下，hosted run 不可能產出 image、
SBOM 或簽章。細節在 `hosted-release-admission-audit.json`。

**本 worker 沒有在本機 build/push image 來補這個缺口。** 那樣產出的 artifact 沒有
hosted OIDC provenance、沒有 lease 授權，正是這套 release 模型要消除的 artifact
漂移。正確的修法是設定 dev environment，不是本機建置。

只採集變數**名稱**，本目錄不記錄任何變數值。

## Manifest schema：讓「沒有 image 的 candidate」可以被誠實記錄

`components` 原本必須非空。對一個從未 build 過的 candidate，這條規則只有兩種出路：
編造，或引用**別的** candidate 的 digest。上一份 manifest 之所以能停在舊 SHA，正是
這個壓力的結果。

因此 `delivery_toolchain/release/release_manifest.py` 改為：

- `validate_manifest`：`components` 只有在 `release_status == "blocked"` 時才可以是
  空的；`ready` 或未宣告 status 的 manifest 一律要求非空。
- `validate_release_admission`：**不論** status 宣告什麼，空的 `components` 一律拒絕
  admission。

所以放寬只作用在「可審閱的 blocked 紀錄」，不會流進 admission。相關測試在
`tests/release/test_release_manifest.py`：
`test_blocked_manifest_may_record_that_no_candidate_image_exists`、
`test_empty_components_are_rejected_unless_the_manifest_is_blocked`、
`test_empty_components_are_never_admissible`。

## Gate registry 只是機械 rebind

`docs/evidence/gates/RELEASE_GATE_REGISTRY.json` 的 `release.candidate_sha`、
`release.manifest_digest` 與七個 gate 的 `release_sha` 改綁到新 candidate。

- 沒有任何 gate 被 clear，`gates_cleared = 0`。
- 沒有新增任何 receipt，`receipts_added = 0`。
- `decision` 維持 `no-go`，`decision_owner` / `decision_date` 是 2026-07-30 的
  Human/Ops 決定，未被改寫。
- 新增 `registry.candidate_rebind`，明寫這是機械 rebind、`re_attestation_required`
  為 `true`。

candidate 前進只會**重新打開** gate，不會關閉任何一道。

## Verifier commands

```bash
# 1. Release admission（預設模式）。本 candidate 為 blocked，必然 exit 1，
#    輸出不會出現任何成功字樣。
python3 delivery_toolchain/release/release_manifest.py \
  --manifest docs/evidence/gates/RELEASE_MANIFEST.json \
  --expected-sha a027fa1c3935360e6fc4b3bd073cd91cbee07548
# 預期：exit 1，開頭 "BLOCKED: ..."，列出 4 項拒絕理由與 5 個 P0 blocker。

# 2. 只做 schema 與 digest 自洽檢查、不判斷是否可部署。
python3 delivery_toolchain/release/release_manifest.py \
  --manifest docs/evidence/gates/RELEASE_MANIFEST.json \
  --expected-sha a027fa1c3935360e6fc4b3bd073cd91cbee07548 --structure-only
# 預期：exit 0，開頭 "STRUCTURE-OK:"，並明示未評估 release admission。

# 3. Gate registry 與 manifest 是否綁到同一個 candidate。
python3 delivery_toolchain/e2e/check_release_gate_registry.py \
  --expected-sha a027fa1c3935360e6fc4b3bd073cd91cbee07548
# 預期：exit 0，結尾 "RELEASE STATE: NO-GO" + "Release gate registry checks passed."

# 4. 回歸測試。
uv run --python 3.12 python -m pytest \
  tests/release/ tests/ops/test_dev_rollout.py -q

# 5. 重跑 registry 探測（需 registry pull 權限）。預期 not found、exit 1。
docker buildx imagetools inspect \
  asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-staging-api:release-a027fa1c3935360e6fc4b3bd073cd91cbee07548
```

Transcript 完整性：

| 檔案 | SHA-256 |
|---|---|
| `release-candidate-transcript.txt` | `sha256:3265240b81bbe0c69ba3cf5399062a28156497922eaaba6bede114ae56420d2a` |

## 已知限制

- 本稽核用 `docker buildx imagetools`（OCI distribution API）而非
  `gcloud artifacts docker images describe`；後者在本 worker 的 sandbox 會在
  process 啟動前被拒絕，產不出 stdout/stderr 或 exit code。兩者查的是同一台
  Artifact Registry 主機。
- 本稽核只能證明「設定中的 Artifact Registry 裡沒有」這些 artifact，不能證明
  其他 registry 或 transparency log 裡也沒有。要關掉這個限制需要 cosign 與
  hosted signing identity，那正是 `…-COSIGN-002` 記錄為缺少的東西。
- 本地環境未安裝 node modules，`check_product_release_gate.py` 的 Playwright
  步驟在本機必然失敗（`Cannot find module '@playwright/test'`）。這與本次改動
  無關，CI 會安裝 node 相依。

## 解除條件

必須先在 GitHub `dev` environment 設好 `ODP_RELEASE_LEASE_STATE_URI` 與
`ODP_RELEASE_LEASE_PUBLIC_KEY`（並確認對應的 GCS lease bucket 存在），再由
Supervisor 簽出 lease，從這個 exact candidate SHA dispatch 唯一的 Runtime Release
workflow。hosted run 會安裝 cosign、發布 candidate-bound SBOM 與簽章，並留下
workflow provenance；之後才可重跑本稽核並把 manifest 移出 `blocked`。

不得手寫、不得沿用本次 blocker 收據宣稱 release 成功。
