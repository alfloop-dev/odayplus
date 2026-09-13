# Execution tasks：去重、依賴與派工計畫

日期：2026-09-13。根對話只調查／SA／SD／驗收；下列implementation由Supervisor派autoworker。

## 本次任務

| Task | 動作 | Owner／Reviewer | 先決任務 | 交付 |
|---|---|---|---|---|
| DPF-SOURCE-SA-SD-BASELINE-001 | 新增 | Antigravity2／Codex2 | 無 | 納入外部來源調查與SA／SD設計基線 |
| DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001 | 沿用既有ID並改派 | Antigravity／Codex2 | DPF-SOURCE-SA-SD-BASELINE-001 | 接手MOF／RIS局部修正並完成公開來源實測 |
| DPF-OFFICIAL-SOURCE-ENDPOINT-INTEGRATION-001 | 新增 | Antigravity3／Codex2 | DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001 | 完成CWA／MOI租賃／NLSC真實來源接入 |
| DPF-TRANSPORT-POI-RELEASE-INTEGRATION-001 | 新增 | Antigravity4／Codex2 | DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001 | 完成OSM／TDX與開放POI真實release接入 |
| DPF-LISTING-PRODUCTION-ASSET-INTEGRATION-001 | 新增 | Antigravity5／Codex2 | DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001 | 將刊登production asset接到真實來源channel |
| DPF-MOBILITY-PRODUCTION-ASSET-INTEGRATION-001 | 新增 | Antigravity6／Codex2 | DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001 | 將observed mobility接到真實聚合資料feed |
| DPF-ACQUISITION-RETENTION-BRIDGE-001 | 新增 | Antigravity7／Codex2 | DPF-PUBLIC-SOURCE-LIVE-INGESTION-REPAIR-001 | 銜接擷取證據與受治理raw retention |
| DPF-SITE-CONTEXT-REAL-COMPONENTS-001 | 新增 | Antigravity2／Codex2 | DPF-ACQUISITION-RETENTION-BRIDGE-001 | 將site market context接到真實component manifests |

機器可讀完整欄位見execution-tasks.json：每筆包含owned_paths、forbidden_paths、acceptance、verification、source_docs、priority。驗證指令是worker須完成的計畫，未執行的不得回報PASS；新測試檔由對應worker建立。

## 去重與既有任務銜接

- 本次新增7筆；既有MOF/RIS task沿用1筆，根對話不再持有實作。
- DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001／PR #63：沿用；等retention、各domain與site context實際資料到位後執行真實八域artifact。不得另建masked任務或在此加入provider fetch。
- XR-SOURCE-APPROVAL-ACTIVATION-001：沿用；逐來源啟用與排程，不與接入worker重疊寫policy/configmap。
- XR-EXT-OSS-FINAL-AUDIT-001／PR #1312：沿用；真正historical dual-run/runtime/egress evidence留在此task，不轉移成「文件通過」。
- HUMAN-OSS-LEGAL-APPROVAL-001：沿用既有D01–D14決定與必要具名receipt，不把技術工程依賴掛在此task完成之後。
- 已合併kernel、official adapters、transport/POI、listing/mobility及retention-bootstrap原任務不重開、不重寫；本次各task只補調查指出的真實接線／錯誤網址／證據缺口。

## 競態控制

1. 先納管並審查SA/SD baseline，再派MOF/RIS。
2. MOF/RIS擁有kernel與共用official檔；official後繼task依賴其完成，才能再改mof_moi.py、ris_nlsc.py與official tests。
3. Transport/POI、listing、mobility及retention在共同底座之後可並行，owned_paths互斥；不得各自改kernel、policy或release_snapshot。
4. site context在retention連接完成後消費已驗證component；它不擁有masked materializer。
5. baseline owned docs/architecture/external-source-ingestion/，其他task只讀；各自證據進自己的evidence_path。
6. Supervisor不得復用根對話未提交worktree做自動派工；worker讀封存manifest/patch，在自己的乾淨工作樹接手。

## 當前狀態與完成定義

本檔記錄完整規劃。canonical寫入／派工結果另見dispatch-receipt.json，避免把規劃當已開跑。`todo`是可排程，`in_progress`仍需actual run evidence。PR merged且owner完成closeout才可done。

保留依賴的原因是同檔案順序、可審查設計及consumer接口；不是等待使用者提供raw資料。真正live auth/source條件由worker逐項查證，先完成可獨立推進部分。

## 條件依賴需注意

既有OSS legal gate依賴XR final audit，activation又依賴legal gate；若final audit要求已啟用runtime，會形成條件循環。baseline worker須核對actual acceptance並提出拆成「既有決策與逐來源權威receipt準備」及「啟用後runtime驗收」的排程修正；不能靠虛構receipt、刪掉驗收或再問已決D01–D14解除。此調查不直接修改policy門檻。
