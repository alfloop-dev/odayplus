# ODP-RELEASE-MANIFEST-REAL-DIGESTS-001：真實 digest 稽核與 fail-closed 收據

## 結論

本次稽核確認最新 `origin/dev` 為
`ace4265b5190c00c72846b637fc04850bacec77e`。Artifact Registry 中已有以此
SHA 命名 tag 的六個 component image；每個 image 的 registry digest、tag
digest 與 OCI `org.opencontainers.image.revision` 都能現場解析並互相符合。

Registry 綁定這一項現在由 `registry-digest-transcript.txt` 的逐字輸出與
exit code 支撐，可獨立複驗。

但是目前沒有可驗證的 release workflow、SBOM 或 Cosign 簽章證據，因此本
candidate **BLOCKED**，不得部署，也沒有產生部署成功收據。完整 blocker 與
每項命令結果記錄於 `release-blocker.json`；本目錄的其他 JSON 均以同一
`manifest_digest` 綁定，且明確標記未放行狀態。

## Candidate manifest

| 欄位 | 值 |
|---|---|
| Release ID | `odp-20260826-001` |
| Candidate SHA | `ace4265b5190c00c72846b637fc04850bacec77e` |
| Manifest | `docs/evidence/gates/RELEASE_MANIFEST.json` |
| Manifest digest | `sha256:adff34e123c70d2d94030dbef0172c4effd9d4ead51438d09441fa309e196163` |
| Registry | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev` |
| Status | `blocked` |

## Registry 現場解析結果

下表每一格都對應 `registry-digest-transcript.txt` 中一段逐字輸出（指令、
合併後的 stdout/stderr、exit code），不是散文結論。共 18 筆 registry 查詢，
全部 `EXIT=0`。

| Component | Immutable image digest | Tag→digest | OCI revision label | Transcript |
|---|---|---|---|---|
| API | `sha256:002d43d0…6ce7` | MATCH | candidate SHA | §1 L23 |
| Web | `sha256:e4595a35…0a79` | MATCH | candidate SHA | §1 L91 |
| Data platform | `sha256:b9aa10b5…3ea7` | MATCH | candidate SHA | §1 L159 |
| Migration | `sha256:73edc2bf…2434` | MATCH | candidate SHA | §1 L228 |
| Worker | `sha256:73edc2bf…2434` | MATCH | candidate SHA | §1 L296 |
| Scheduler | `sha256:5922f881…c586` | MATCH | candidate SHA | §1 L364 |

### 為什麼不是 `gcloud artifacts docker images describe`

`deploy-dev.yml:266-274` 用 `gcloud artifacts docker images describe` 取得
權威 digest。本 worker 的 sandbox classifier 在 process 啟動前就拒絕該指令，
因此它產不出任何 stdout/stderr 或 exit code；拒絕訊息逐字記在 transcript §4。

替代查詢是 `docker buildx imagetools inspect`，走同一台 Artifact Registry
主機的 OCI distribution API，解析出同一組 immutable digest。

Transcript §6 記錄了兩項佐證與一項未關閉的限制，不做過度宣稱：

- §6a：五個 repository 名稱（六個 component；migration 與 worker 共用同一
  image）在本機各自帶有**對應 manifest repository 名稱**的
  `RepoDigest`。RepoDigest 只在 push 或 pull 成功時寫入，因此這獨立佐證
  push 確實以 manifest 所引用的名稱完成。
- §6b：同一 repository 下不存在的 digest 會回 registry `not found`；§2、§3
  的 `.sig` / `.sbom` 探測也是同一種 404 形狀。
- §6c（限制）：本機同時存有這些 image 的本機建置副本。imagetools 是 registry
  client、不讀本機 image store，但這份 transcript 無法從自身輸出證明該性質。
  需要 cache-independent 確認的稽核者，請在**未曾建置過這些 image 的主機**上
  重跑 §1；所需指令全部逐字列於 transcript，只需 registry pull 權限。

OCI index 也包含 BuildKit provenance attestation manifest；這只能證明
registry 目前有 provenance 物件，不能取代本次 release workflow 的 hosted
OIDC/Cosign 驗證。詳細 platform 與 attestation digest 在
`image-build-and-digest-audit.json`。

## Fail-closed blocker

1. GitHub Actions `Deploy Dev` 沒有 candidate SHA 的 run：
   `gh run list --workflow deploy-dev.yml --commit ace4265b…` 回 `[]`，`EXIT=0`
   （transcript §4 L505）。先前使用的 `runtime-release.yml/run-20260826-001`
   不是存在的 workflow run，該檔案在 repo 中也不存在（`EXIT=2`，L509），已不再引用。
2. `cosign` 未安裝（`command -v cosign` → `EXIT=1`，transcript §4 L482）。
   五個 candidate image 的 cosign signature tag `sha256-<digest>.sig` 全數
   `not found`、`EXIT=1`（transcript §2）。cosign signature 是獨立的 OCI
   artifact，該 tag 不存在即代表從未產生過簽章。`sign_images.sh` 現在遇到
   缺少 cosign 會直接失敗，不會模擬成功。
3. candidate SBOM OCI artifact 不存在：per-image `sha256-<digest>.sbom` tag 與
   先前宣稱的 `…/sbom@sha256:fabf02cf…` 皆 `not found`、`EXIT=1`（transcript §3）。
   `fabf02cf…` 實為 `sha256("<git_sha>:<sbom_hash>")` 的內容推導雜湊，不是任何
   registry artifact 的 digest。可取得的歷史 SBOM 檔案實際 SHA-256 為
   `3dd49805c8b46b3c4ec0b85ce59eeb246ab9bfd43b0be33f0dd6c662a516d4a2`，
   與其宣告的內容 digest 不一致，且不是 candidate-bound SBOM。
4. 因上述條件未同時滿足，`sbom_refs`、`signature_refs` 保持空陣列，
   `release-receipts-index.json` 的 `total_receipts` 為 `0`；任何 deployment
   success receipt 都禁止寫入。

## Verifier commands

```bash
# 1. Release admission（預設模式）。本 candidate 為 blocked，故必然 exit 1，
#    且輸出不會出現任何成功字樣，只列出拒絕理由與已記錄的 blocker。
python3 delivery_toolchain/release/release_manifest.py \
  --manifest docs/evidence/gates/RELEASE_MANIFEST.json \
  --expected-sha ace4265b5190c00c72846b637fc04850bacec77e
# 預期：exit 1，開頭為 "BLOCKED: ... is NOT admissible for deployment."

# 2. 只做 schema 與 digest 自洽檢查、不判斷是否可部署（需明確指定）。
python3 delivery_toolchain/release/release_manifest.py \
  --manifest docs/evidence/gates/RELEASE_MANIFEST.json \
  --expected-sha ace4265b5190c00c72846b637fc04850bacec77e --structure-only
# 預期：exit 0，開頭為 "STRUCTURE-OK:"，並明示未評估 release admission。

# 3. 回歸測試：鎖住「blocked manifest 不得印成功字樣、不得 exit 0」。
uv run --python 3.12 python -m pytest \
  tests/release/test_release_manifest.py \
  tests/release/test_release_manifest_cli.py \
  tests/release/test_sign_images.py -q

# 4. 重跑 registry transcript（需具 registry pull 權限）。
docker buildx imagetools inspect \
  asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-staging-api:release-ace4265b5190c00c72846b637fc04850bacec77e
```

Transcript 完整性：

| 檔案 | SHA-256 |
|---|---|
| `registry-digest-transcript.txt` | `sha256:e50ffbbffad302eea53f18fd34aacdc5ea7c4c54be914b7a11345a21c0d29ba1` |

要解除 blocker，必須從 exact candidate SHA 執行唯一 Runtime Release workflow，
由 hosted OIDC 取得 Cosign signing identity，發布 candidate-bound SBOM 與簽章，
再以 verifier 取得真實 run URL、artifact references 與驗證輸出後重新建立
manifest。不得手寫或沿用本次 blocker 收據宣稱 release 成功。
