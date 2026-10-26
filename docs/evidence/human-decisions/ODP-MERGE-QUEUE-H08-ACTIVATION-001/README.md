# ODP-MERGE-QUEUE-H08-ACTIVATION-001：H08 批次參數裁決與 dev merge queue 實際套用

- **Task ID**: `ODP-MERGE-QUEUE-H08-ACTIVATION-001`
- **對應結構修復項**: `docs/plans/ODP_OPEN_DECISIONS_2026-09-03.md` 第 19 項（merge queue 批次）
- **前置決策**: D21（2026-09-08，選項 B：保留為正式實作需求），見
  [`ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md`](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md)
- **本次涵蓋**: H08（批次參數選擇）與 WP-35C 的 live ruleset 套用及讀回
- **基準**: `origin/dev` `32330be95f1…`（PR #1389 合併後）

## 1. H08 裁決

| 欄位 | 內容 |
|---|---|
| 裁決 | 採用 [`configuration-options.md`](../ODP-MERGE-QUEUE-BATCH-DESIGN-001/configuration-options.md) 的 **Option B：Conservative Batch with Bounded Wait** |
| 參數 | `min_entries_to_merge = 2`、`min_entries_to_merge_wait_minutes = 10`；其餘（`MERGE`、`ALLGREEN`、`max_entries_to_merge = 5`、`max_entries_to_build = 5`、`check_response_timeout_minutes = 60`）維持不變 |
| 簽核人 (Decider) | **蔡尚志** |
| 授權角色 | 負責人 |
| 裁示日期 | 2026-10-02（UTC） |
| 範圍 | 僅 `alfloop-dev/odayplus` 的 `dev` 分支 merge queue ruleset（`dev-merge-queue`，id `20508144`）；`main` 與 classic branch protection 不在範圍內 |
| 風險承擔人 | 蔡尚志（負責人） |
| 複審日期 | 2026-11-02 |
| 重新裁決觸發條件 | 見 §4 |
| 同時授權 | WP-35C live 套用（將上述參數寫入 GitHub ruleset） |

> [!NOTE]
> 具名來源：由本機操作者於 2026-10-02 互動工作階段中選定 Option B、提供簽核人與角色，
> 並授權立即套用；Claude 代為謄錄，非簽核人本人在此檔案上的數位簽署。
> 若後續閘門要求可驗證的簽署憑據，需由簽核人本人補齊，本檔不足以替代。
> 複審日期為謄錄時設定的 30 天預設值，簽核人可另行調整。

這份裁決補上 `ODP-MERGE-QUEUE-BATCH-IMPLEMENTATION-001` 證據自承的缺口：該 task 的
`policy.json` 參數是「Codex 依當時使用者指示採用的可調工程預設值，未取得逐項 H08 簽核
或 live 套用核准」。本次簽核的值與 repo 已存在的 `policy.json` 相同，因此 **不需要修改
任何程式或政策檔**。

## 2. 套用前的實際狀態（WP-35B 與 live 脫節）

2026-10-02 套用前讀回 GitHub ruleset（[`live-ruleset-before.json`](live-ruleset-before.json)）：

| 參數 | repo `policy.json` | live（套用前） |
|---|---|---|
| `min_entries_to_merge` | 2 | **1** |
| `min_entries_to_merge_wait_minutes` | 10 | **5** |

也就是說，自 PR #1284（2026-09-09）合併以來，GitHub 實際生效的一直是 Option A。
`policy.json` 的改動從未被套用。

## 3. 套用與讀回

- **方法**：以套用前讀回的 ruleset 為底，只改上述兩個參數，`PUT repos/alfloop-dev/odayplus/rulesets/20508144`。
  不使用 `apply_branch_protection.py` 的完整套用，避免連帶重寫 classic branch protection。
  實際送出的 body 見 [`ruleset-put-payload.json`](ruleset-put-payload.json)；與套用前讀回的 `rules` 差異只有這兩個值。
- **GitHub 回傳 `updated_at`**：`2026-10-02T00:35:36.445Z`
- **套用後讀回**（[`live-ruleset-after.json`](live-ruleset-after.json)）：

```json
{"name":"dev-merge-queue","enforcement":"active","refs":["refs/heads/dev"],
 "merge_queue":{"merge_method":"MERGE","grouping_strategy":"ALLGREEN",
  "min_entries_to_merge":2,"min_entries_to_merge_wait_minutes":10,
  "max_entries_to_merge":5,"max_entries_to_build":5,"check_response_timeout_minutes":60}}
```

- **一致性**：從 `origin/dev` 完整樹的複本執行
  `python3 delivery_toolchain/github/apply_branch_protection.py --verify-only`，exit 0，
  `dev` 與 `main` 的 protection 皆為 ACTIVE。

| 檔案 | sha256 |
|---|---|
| `live-ruleset-before.json` | `7e5a729bdb84b6641f161b87aef385ccaa7153dbf9fd1d1508a270413dd375b6` |
| `live-ruleset-after.json` | `74cba7fb51180bebaf1c5ad82cad7fb035c7fcb92053da17a8ab68e73fb5304d` |
| `ruleset-put-payload.json` | `a1b673b9cf76442409dda967e9707eeb13c15fe8ec1371ea9c12fc755516de2b` |

（雜湊為 `jq -S .` 正規化後的檔案內容。）

## 4. 尚未證明的部分與重新裁決觸發條件

本 task **只證明參數已寫入並讀回**，不證明 GitHub 的實際批次行為。下列 WP-35C 項目
需要在套用後的真實 PR 流量上觀測，不在本 task 內宣稱完成：

1. 兩個以上合格 PR 同時排隊時實際形成批次。
2. 單一 PR 的實際等待時間（GitHub accumulation timer 的起點與實際 hold 長度）。
3. ALLGREEN 下失敗 PR 被剔除、其餘重建。

出現下列任一情況時應重新裁決或回退：

- 觀測到單一 PR 在 queue 中因 accumulation 而 hold 超過 10 分鐘；
- 批次造成 required check 逾時（60 分鐘）或剔除率明顯上升；
- fleet 的合併延遲成為 release 路徑上的瓶頸。

## 5. 回退

以 [`live-ruleset-before.json`](live-ruleset-before.json) 的兩個值重新 `PUT` 同一 ruleset 即回到 Option A；
或依 [`docs/runbooks/dev-merge-queue.md`](../../../runbooks/dev-merge-queue.md) 的 Rollback 段落整體移除 merge queue。
