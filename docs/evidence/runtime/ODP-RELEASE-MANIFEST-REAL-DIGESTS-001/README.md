# ODP-RELEASE-MANIFEST-REAL-DIGESTS-001：真實 digest 稽核與 fail-closed 收據

## 結論

本次稽核確認最新 `origin/dev` 為
`ace4265b5190c00c72846b637fc04850bacec77e`。Artifact Registry 中已有以此
SHA 命名 tag 的六個 component image；每個 image 的 registry digest、tag
digest 與 OCI `org.opencontainers.image.revision` 都能現場解析並互相符合。

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

| Component | Immutable image digest | OCI revision label | Registry / tag |
|---|---|---|---|
| API | `sha256:002d43d0b6ea180946df2f1bfc4ff15a9eb8fc2abfbd49b89055a696a6986ce7` | candidate SHA | PASS / PASS |
| Web | `sha256:e4595a35aa68537e0e560ac45263ebfaadc19939942a86635eeb6a5bacfb0a79` | candidate SHA | PASS / PASS |
| Data platform | `sha256:b9aa10b5334406aca653c68e128de8c85414cec27b49c0052f4dd0f293da3ea7` | candidate SHA | PASS / PASS |
| Migration | `sha256:73edc2bf000668b1bbd8e7b6cd3f270f6a517ddddbec7758d15d4e8bc9422434` | candidate SHA | PASS / PASS |
| Worker | `sha256:73edc2bf000668b1bbd8e7b6cd3f270f6a517ddddbec7758d15d4e8bc9422434` | candidate SHA | PASS / PASS |
| Scheduler | `sha256:5922f88105ef9d9c5a1a1228e64b18dda5e8e3cdb42603bf39822b53a8c5e586` | candidate SHA | PASS / PASS |

OCI index 也包含 BuildKit provenance attestation manifest；這只能證明
registry 目前有 provenance 物件，不能取代本次 release workflow 的 hosted
OIDC/Cosign 驗證。詳細 platform 與 attestation digest 在
`image-build-and-digest-audit.json`。

## Fail-closed blocker

1. GitHub Actions `Deploy Dev` 沒有 candidate SHA 的 run；先前使用的
   `runtime-release.yml/run-20260826-001` 不是存在的 workflow run，已不再引用。
2. `cosign` 在稽核環境未安裝；預期的 candidate signature artifact lookup
   回傳 `Image not found`。`sign_images.sh` 現在遇到缺少 cosign 會直接失敗，
   不會模擬成功。
3. 預期的 candidate SBOM OCI artifact 不存在；可取得的歷史 SBOM 檔案實際
   SHA-256 為 `3dd49805c8b46b3c4ec0b85ce59eeb246ab9bfd43b0be33f0dd6c662a516d4a2`，
   與其宣告的內容 digest 不一致，且不是 candidate-bound SBOM。
4. 因上述條件未同時滿足，`sbom_refs`、`signature_refs` 保持空陣列，
   `release-receipts-index.json` 的 `total_receipts` 為 `0`；任何 deployment
   success receipt 都禁止寫入。

## Verifier commands

```bash
python3 delivery_toolchain/release/release_manifest.py \
  --manifest docs/evidence/gates/RELEASE_MANIFEST.json \
  --expected-sha ace4265b5190c00c72846b637fc04850bacec77e
python3 -m pytest tests/release/test_release_manifest.py tests/release/test_sign_images.py
```

要解除 blocker，必須從 exact candidate SHA 執行唯一 Runtime Release workflow，
由 hosted OIDC 取得 Cosign signing identity，發布 candidate-bound SBOM 與簽章，
再以 verifier 取得真實 run URL、artifact references 與驗證輸出後重新建立
manifest。不得手寫或沿用本次 blocker 收據宣稱 release 成功。
