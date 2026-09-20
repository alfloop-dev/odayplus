# Foundation 第14次審查修復 — 2026-09-20

沿用 `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001` 與 PR #1046。
使用者授權前景 Codex 接手修復，Codex2 獨立審查；全部退回紀錄保留。

## Finding 與驗收

| Finding | 修正 | 行為驗收 |
| --- | --- | --- |
| 收到 INT/TERM 後仍繼續執行 | EXIT 統一處理；INT/TERM 轉送至 Terraform job group，等待 state flush，再以 130/143 退出 | plan/apply/migration × INT/TERM × shell/group 共12種取消情境；不再啟動後續命令、子程序結束、保留 state |
| migration 失敗時可能覆蓋較新 state | Phase 2 開始後保留 canonical state，不用 Phase 1 舊副本覆蓋 | migration 非零及 signal 時，canonical 新 marker 與 phase1 舊 marker 各自保留 |
| backend 片段錯用 staging fallback | 讀既有 `backend_config_hcl_example` output | dev/staging/prod prefix、相對且含空白 tfvars、output 失敗不進入 migration |
| 文字測試接受錯誤實作 | 用真正 subprocess 執行 Bash 與離線 Terraform/provider stub | partial apply 保留 exit17、migration 保留 exit19、output 保留 exit23；成功 migration 才清除 local state |
| PR 主文、測試集合及收據落後 | 將 bootstrap tests 納入 canonical verification，更新同一 PR 主文並讀回 | 新 source SHA／source hashes、實際命令、起訖時間、exit code 及 duration 的收據 |

新增的執行測試曾對修復前程式執行，抓出全部12種取消情境、dev/prod
prefix 及 output failure；原有字串測試無法攔住這些問題。
測試只使用隔離暫存目錄與假的 Terraform，不執行真實 Terraform/GCP。

## 證據版本

本輪 source、完整 Terraform/release manifest tests 與 registry 驗證收據
使用 `review14-*-20260920.json` 命名，記錄真正 tested commit 及 source
hashes。測試後的 evidence-only commit 不冒稱為已執行該次測試的 commit；
最終送審 head 另由 canonical task verification 和 GitHub CI 綁定。

既有 `foundation-egress-focused-20260912.json`、
`foundation-full-checks-20260912.json`、
`foundation-registry-after-20260912.json` 保留為歷史量測，本輪驗證由上述新
收據取代。它們不是新 bootstrap patch 的執行證明，也不改寫其中舊 timestamp。

## 既有範圍

Human/Ops 核准的 Route (c) 保持：root 6 個檔案永遠綁定，module candidate
再綁定 6 個 module 檔；digest/proof_source 同源，缺檔 fail closed。
既有17個 moved mappings、CMEK、retention、default-deny egress 與
`oday-plus/bootstrap` migration prefix 不變。

歷史 pre-module candidate 的 `integrity_errors=[]` 不代表 release 放行；
registry 的既有 blocking gates 仍維持 NO-GO。本輪沒有新增 live apply、
API/Web deployment 或 ALL_TRAFFIC readback。retention 到期清理由原有獨立
任務追蹤；不能拿離線測試或 PR 合併取代這些 release 驗收。
