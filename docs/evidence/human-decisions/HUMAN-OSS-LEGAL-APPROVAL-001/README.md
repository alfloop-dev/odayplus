# HUMAN-OSS-LEGAL-APPROVAL-001 — 外部資料來源核准紀錄

- **核准者**：蔡尚志（負責人），GitHub `ajoe734`（id 169176954）
- **權威紀錄**：[alfloop-dev/odayplus#1418](https://github.com/alfloop-dev/odayplus/issues/1418)，由核准者本人發布
- **收據**：`source-data-approval-receipt.json`，sha256 `2119dc6f74335d3a1884e0a0dfda6db61171df0542c46371c1719c26204d83c7`，與 issue 內 JSON 逐字相同（見 `readback.json`）
- **有效期**：2026-10-06 核准，2027-10-06 複審，2028-10-06 到期
- **範圍**：dev 有界擷取與 EMGI raw 保存，以及 production 排程自動更新；16 個來源逐一列出處置與條件

## 檔案

| 檔案 | 內容 |
|---|---|
| `source-data-approval-receipt.json` | 核准收據原文，`ODAY_SOURCE_*_APPROVAL_RECEIPT_SHA256` 綁的就是它的 sha256 |
| `readback.json` | issue 作者、時間與 sha256 讀回結果；13 個分類 digest |
| `classifications/*.json` | 13 個 runner source lane 的 `emgi.raw-field-classification.v1` 正式版，全部經 runner 驗證器判定 `BOUND` |
| `classification-review.md` | 核准者審閱的逐欄分類表（照草稿接受） |

## 尚未涵蓋與限制

- 分類目前**沒有遮蔽強制效果**：runner 只檢查欄位是否列齊，RESTRICTED 欄位仍會原樣保存。依收據條件另開工作補上遮蔽或拒存。
- `market_events`、`brand_locator` 尚無 adapter；`tgos`、`google_places_verifier`、`survey` 沒有 runner lane。核准已記錄，但對應程式完成前不得啟用。
- `google_places_verifier` 僅核准即時驗證、不保存原始回應；目前 Dagster asset 會保存 raw response，啟用前必須改程式。
- 這份紀錄本身不會啟用任何來源。啟用需另外設定 `ODAY_SOURCE_*_ENABLED` 與 `ODAY_SOURCE_*_APPROVAL_RECEIPT_SHA256`。

## OSS 授權政策與 LGPL 四案

- **權威紀錄**：[alfloop-dev/odayplus#1421](https://github.com/alfloop-dev/odayplus/issues/1421)，由核准者本人發布
- **收據**：`oss-license-approval-receipt.json`，sha256 `ce5fe213070e4f45cfc85953ac26d4a70e82a886a36d35d45125328064635210`，與 issue 內 JSON 逐字相同（見 `oss-license-readback.json`）
- **決定**：D01 sharp/libvips 1.3.3、D03 psycopg 3.3.4／psycopg-binary 3.3.4／psycopg-pool 3.3.1、D04 moocore 0.3.2 附條件允許；D02 psycopg2-binary 2.9.12 接受上游連結例外；D05–D14 照 ODP-OSS-DECISION-PACK-001 既有方向核准
- **限制**：豁免只適用上述精確版本，升版即需重新核准；本紀錄不登記豁免、不改 licence gate，由 ODP-OSS-LICENSE-EXEMPTION-REGISTER-001 依此收據登記
