# 執行任務去重、派工對照與既有 PR/Task 銜接報告

任務：`DPF-SOURCE-SA-SD-BASELINE-001`
版本：2026-09-13

## 新規劃任務與既有任務對照

### 保留沿用的既有任務

| 既有 Task/PR | 現行狀態 | 銜接方式 | 不得重建或繞過的原因 |
|---|---|---|---|
| `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` / PR #63 | 等待 retained 八域 artifact | 沿用；上游 domain 資料到位後執行真實八域 masked release | 已有獨立 materializer，不在此加 provider fetch |
| `XR-EXT-OSS-FINAL-AUDIT-001` / PR #1312 | blocked（A1–A3 partially met） | 沿用；historical dual-run/runtime/egress 證據在此 task 完成 | 不把 runtime 驗收轉成「文件通過」 |
| `XR-SOURCE-APPROVAL-ACTIVATION-001` | todo（依賴 Legal gate） | 沿用；逐來源啟用/排程；不與接入 worker 重疊寫 policy/configmap | 已有獨立 owned_paths |
| `HUMAN-OSS-LEGAL-APPROVAL-001` | todo（依賴 XR audit） | 沿用 D01–D14 已決定；具名 receipt 準備由此 task 處理 | 技術工程不掛在此 task 完成之後 |

### 新規劃 8 筆任務

| # | Task ID | 類型 | Owner | Reviewer | depends_on |
|---|---|---|---|---|---|
| 1 | `DPF-SOURCE-SA-SD-BASELINE-001` | 新增 | Antigravity2 | Codex2 | 無 |
| 2 | `DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001` | 沿用改派 | Antigravity | Codex2 | [1] |
| 3 | `DPF-OFFICIAL-SOURCE-ENDPOINT-INTEGRATION-001` | 新增 | Antigravity3 | Codex2 | [2] |
| 4 | `DPF-TRANSPORT-POI-RELEASE-INTEGRATION-001` | 新增 | Antigravity4 | Codex2 | [2] |
| 5 | `DPF-LISTING-PRODUCTION-ASSET-INTEGRATION-001` | 新增 | Antigravity5 | Codex2 | [2] |
| 6 | `DPF-MOBILITY-PRODUCTION-ASSET-INTEGRATION-001` | 新增 | Antigravity6 | Codex2 | [2] |
| 7 | `DPF-ACQUISITION-RETENTION-BRIDGE-001` | 新增 | Antigravity7 | Codex2 | [2] |
| 8 | `DPF-SITE-CONTEXT-REAL-COMPONENTS-001` | 新增 | Antigravity2 | Codex2 | [7] |

### 路徑所有權互斥與共用檔序列化確認

| owned_paths 衝突檢查 | 結果 | 序列化與邊界說明 |
|---|---|---|
| Task 2 與 Task 3 共用 4 個 official 檔案 (`mof_moi.py`, `ris_nlsc.py`, `test_official_live_acquisition.py`, `official_source_fixtures.py`) | ✓ 透過 depends_on 嚴格序列化 | Task 3 明確依賴 Task 2 完成並合併後，才可接續修改官方來源檔 |
| Task 3–6 並行路徑互斥 | ✓ 各自 owned_paths 完全不重疊 | 各自擁有獨立 source adapter / test 檔案 |
| Task 7 擁有 `raw_snapshot_retention.py` | ✓ 其他 task 均不碰此檔 | 專注於 acquisition 與 retention 的連接 |
| Task 8 擁有 `products/site_context/` | ✓ 其他 task 不碰此目錄 | 依賴 Task 7 完成後消費真實 component manifests |
| 所有 task 禁止碰 `configmap.yaml`/`external/policy/` | ✓ 邊界隔離明確 | 屬於 XR activation 獨立任務 |
| 所有 task 禁止碰 `release_snapshot.py` | ✓ 邊界隔離明確 | 屬於 PR #63 / masked release 獨立任務 |

### 派工變更對照（Canonical Alignment）

| 項目 | 歷史規劃快照（PR #1331） | 目前 Canonical 狀態（`ai-status.json`） | 說明與對照規範 |
|---|---|---|---|
| `mutates_canonical` | 標記為 `false`（規劃暫存） | **全數為 `true`**（8 筆任務均正式納管） | 8 筆任務皆為正式 canonical 任務，不可拿舊快照 `false` 覆蓋 |
| 派工鎖定 (`non_dispatchable`) | 標記為 `true`（人工 hold） | **全數為 `false`**（人工 hold 已解除） | 使用者授權到位後解除 hold，Supervisor 可自動依依賴派工 |
| Baseline 設計 Repository | `oday-data-platform`（原規劃） | **`alfloop-dev/odayplus`** | 使用者已授權發布至 `odayplus`，設計文件統一納管於此 |
| 產品程式碼 Repository | `oday-data-platform` | **`alfloop-dev/oday-data-platform`** | 產品實作與修復維持在 data-platform repo |

### 已完成/已合併的既有任務

以下原任務已完成，本次規劃不重開、不重寫，各 task 只補調查指出的真實缺口：

- 原 kernel/adapter 建立任務
- 原 domain asset 建立任務
- 原 retention bootstrap 任務
- 原 transport/POI 整合任務

## PR #63 與 PR #1312 的位置

### PR #63 (DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001)
- **位置**：`oday-data-platform` repo
- **HEAD**: `56c9c2bb…`
- **功能**：masked release materializer，在 sources-off/default-deny 環境讀取 retained raw，產出八域 masked release artifact
- **銜接**：Task 7 (BRIDGE-001) 建立 acquisition → retention 連接；八域齊備後 PR #63 執行真實 artifact 產出
- **新任務禁止修改 PR #63 範圍內的檔案**

### PR #1312 (XR-EXT-OSS-FINAL-AUDIT-001)
- **位置**：`odayplus` repo
- **HEAD**: `f73345e73301…`
- **功能**：OSS final audit；需 live runtime evidence
- **銜接**：缺的 live evidence（DB snapshot readback、pod digest、VPC egress log）映射到 `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` 與 `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`
- **4 項 legal gates 映射到 `HUMAN-OSS-LEGAL-APPROVAL-001`**

## 依賴圖

```
DPF-SOURCE-SA-SD-BASELINE-001  (本 task: odayplus)
        │
        ▼
DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001  (oday-data-platform)
        │
        ├──▶ DPF-OFFICIAL-SOURCE-ENDPOINT-INTEGRATION-001
        ├──▶ DPF-TRANSPORT-POI-RELEASE-INTEGRATION-001
        ├──▶ DPF-LISTING-PRODUCTION-ASSET-INTEGRATION-001
        ├──▶ DPF-MOBILITY-PRODUCTION-ASSET-INTEGRATION-001
        └──▶ DPF-ACQUISITION-RETENTION-BRIDGE-001
                    │
                    ▼
             DPF-SITE-CONTEXT-REAL-COMPONENTS-001
                    │
                    ▼
         DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001 / PR #63
         （八域齊備後產出 release artifact）
                    │
                    ▼
         XR-EXT-OSS-FINAL-AUDIT-001 / PR #1312
         （runtime evidence → legal gates）
                    │
                    ▼
         HUMAN-OSS-LEGAL-APPROVAL-001
                    │
                    ▼
         XR-SOURCE-APPROVAL-ACTIVATION-001
         （逐來源啟用）
```
