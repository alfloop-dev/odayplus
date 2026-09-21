# ODP-GCP-STAGING-EXECUTION-PREFLIGHT-002 實測收據

2026-09-07 當下執行的唯讀 GCP / staging 部署前置實測。**不沿用 9 月 5/6 日的狀態當最新結果**，
所有結論均綁定本次真實命令的 exit code 與 UTC 時間。

## 檔案

| 檔案 | 內容 |
|---|---|
| `preflight-result.json` | machine-readable 實測結果（identity、readback、必要變數矩陣、缺口、advisories） |
| `remediation-table.md` | 按舊 task ID 對應的修復表 |
| `github-variables-audit.json` | 六個 GitHub environment 的變數盤點（resource identity 記值；其餘僅記存在） |
| `command-log.jsonl` | 每一條命令的 argv、UTC 起訖、耗時、exit code、stderr 摘要 |
| `raw/*.err`, `raw/*.out` | 命令原始輸出 |

## 綁定

- commit：`596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`（origin/dev，執行當下的 tip）
- 執行者：Claude（helper execution lease；canonical owner `Antigravity4`、reviewer `Codex`）

## 一句話結論

**GCP live readback 這一路完全沒有成立**：ACTIVE 帳號憑證需重新驗證，12 次唯讀查詢在送出 API
請求之前就全部中止（exit 1，`Reauthentication failed. cannot prompt during non-interactive
execution.`），其中 **0 次** 回傳 `PERMISSION_DENIED`、**0 次** 回傳 `NOT_FOUND`。因此本收據
**不對任何 GCP 資源的存在性或 IAM 作出判定**。

不依賴該登入的那一路（GitHub environment 變數 + 已合併 IaC 核對）則完整完成，並推翻了先前
「staging 僅缺 5 項 vars」的結論：實測 staging 缺 10 項，dev 缺 2 項，production 缺 4 項。

## 邊界

- 未建立、修改或刪除任何 GCP 或 GitHub 資源；未變更 IAM；未發起互動登入；未替換 credential。
- 未輸出 access/refresh token、secret value 或任何 Terraform state 內容。
- 未接管 `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001` 或 workflow owner 的 owned paths；
  本任務只寫入自己的 evidence 目錄。
- 本任務完成僅代表實測完成，**不替代任何部署驗收，不解除任何 release gate，不變更任何
  task 的 blocked 狀態**。
- `raw/` 中的 GitHub 變數原始 dump 已移除，改以 `github-variables-audit.json` 呈現；
  原命令記於 `command-log.jsonl`，reviewer 可逐條重跑複核。
