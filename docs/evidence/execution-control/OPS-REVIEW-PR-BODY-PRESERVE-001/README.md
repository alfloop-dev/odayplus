# OPS-REVIEW-PR-BODY-PRESERVE-001 驗收與證據紀錄

## 任務摘要
- **任務 ID**: OPS-REVIEW-PR-BODY-PRESERVE-001
- **標題**: 保留人工整理的繁中 PR body，避免 Review Bus 狀態同步覆寫
- **負責人**: Antigravity6 (Canonical Owner)
- **評審人**: Codex2
- **目標**: 修正 Review Bus 每次狀態同步時覆寫既有 PR body 的行為，保留人工整理的中文說明與自訂內容，只在 `<!-- pantheon-bus -->` 與 `<!-- /pantheon-bus -->` 標記範圍內更新受治理的 review bus 狀態 metadata，並將 PR 審查範本轉為繁體中文。

## 變更範圍

1. **Review Bus 狀態同步與 Body 組合邏輯** (`.orchestrator/github_bus.py`):
   - 使用既有 `COMMENT_MARKER` (`<!-- pantheon-bus -->`) 與新增之 `BUS_END_MARKER` (`<!-- /pantheon-bus -->`) 定界治理區塊（刪除多餘且未使用的 `BUS_START_MARKER`）。
   - 實作 `reconcile_pr_body(existing_body, bus_template_body)` 函式：
     - 若既有 PR body 為空，使用完整 Review Bus 範本。
     - 若既有 PR body 含有 `<!-- pantheon-bus -->` 與 `<!-- /pantheon-bus -->` 區塊，僅就地更新該區塊，完整保留區塊前後之自訂說明與筆記。
     - 若既有 PR body 含有舊版未閉合之標記，僅在匹配到兩個精確已知終止句（`KNOWN_LEGACY_TERMINATORS`：英文舊範本與中文舊範本結尾）時進行閉合遷移並保留自訂尾部內容。
     - 若未匹配到已知舊終止句（未識別之未閉合 block），嚴格採用 **fail closed** 機制：原 body 原樣保留（正規化尾端換行），完全避免無聲覆寫或遺失人工內容，且重複執行具備嚴格冪等性。
     - 若既有 PR body 無任何標記（例如由 `task_finalize.sh` 或開發者手動建立之 PR 說明），保留既有內容並在下方以分隔線附加治理 metadata 區塊。
     - 多次執行保持完全冪等（Idempotent）。
   - 更新 `find_existing_pr` 查詢欄位加入 `body`，使 Review Bus 在對照與更新時具備 PR 內容可見度。
   - 更新 `upsert_review_pr`：
     - 同步時先透過 `reconcile_pr_body` 合併現有 PR 內容與最新治理 metadata。
     - 計算 hash 時以合併後 body 為基準，若無變動則立即判定冪等並跳過 API 呼叫。
     - 在 PR 存在時僅透過 REST PATCH 更新合併後內容與標籤，不建立第二個 PR writer。

2. **繁體中文 PR 審查範本** (`.orchestrator/templates/github_review_pr.md`):
   - 將所有區段標題轉換為繁體中文（`## 任務資訊`、`## 審查範圍`、`## 分支資訊`、`## 下一步`、`## 行動審查指南`）。
   - 包含起始 `{{marker}}`（`<!-- pantheon-bus -->`）以及結尾 `<!-- /pantheon-bus -->` 標記。

3. **單元與回歸測試套件** (`.orchestrator/test_github_bus.py`):
   - 新增 `ReconcilePrBodyTests` 測試類別：
     - `test_reconcile_empty_or_none_uses_template`
     - `test_preserves_custom_human_body_without_markers`
     - `test_reconcile_is_strictly_idempotent`
     - `test_updates_bus_block_while_preserving_prefix_content`
     - `test_preserves_suffix_content_below_bus_block`
     - `test_preserves_both_prefix_and_suffix_content`
     - `test_handles_legacy_bus_template_without_end_marker`
     - `test_handles_chinese_legacy_bus_template_without_end_marker`
     - `test_unrecognized_unclosed_block_fails_closed_and_preserves_body`
     - `test_unrecognized_unclosed_block_with_prefix_fails_closed`
     - `test_chinese_review_template_rendering`
   - 新增 `UpsertReviewPrPreserveBodyTests` 測試類別：
     - `test_upsert_review_pr_preserves_human_body_on_reconcile` 驗證端到端 reconcile 保留既有 PR body 且重複執行具備冪等性。

## 驗證證據

### 1. GitHub Bus 測試套件（102 測試全數通過）
```bash
$ uv run --no-project --python 3.12 --with pytest --with jsonschema --with pyyaml pytest .orchestrator/test_github_bus.py -q
................................................................. [ 63%]
.....................................                                 [100%]
102 passed in 1.30s
```

```bash
$ PYTHONPATH=.orchestrator python3 -m unittest test_github_bus
......................................................................................................
----------------------------------------------------------------------
Ran 102 tests in 1.118s

OK
```

### 2. 設定 Schema 與 Wiring 檢查
```bash
$ python3 delivery_toolchain/governance/check_orchestrator_config.py --config /home/lupin/odayplus/.orchestrator/config.json
Validated 3 config documents and their merged runtime views.

$ python3 delivery_toolchain/governance/check_config_wiring.py
All 166 config keys are read by production code.
```

### 3. 程式碼邊界檢查
```bash
$ python3 delivery_toolchain/governance/check_code_boundaries.py
Code boundary checks passed for 982 files.
- archived: 14
- development_delivery_tooling: 66
- development_platform_system: 63
- evidence_artifact: 22
- product_operations_tooling: 29
- product_system: 485
- verification: 303
```

### 4. Git Diff 乾淨度檢查
```bash
$ git diff --check origin/dev
(clean exit 0)
```

## 驗收條件對照表
- [x] **既有 PR body 有非模板內容時不得被 Review Bus 覆寫**: 透過 `reconcile_pr_body` 保留前綴、後綴或整段無標記之自訂內容，僅覆寫受標記之 Review Bus 區塊。未識別之未閉合 block 嚴格 fail closed 保留原內容。
- [x] **新 PR 的 review template 使用中文**: `.orchestrator/templates/github_review_pr.md` 完整採用繁體中文欄位與說明。
- [x] **同一 PR 重複 reconcile 必須 idempotent**: 測試證明連續多輪 reconcile 輸出字節完全一致，且 `upsert_review_pr` 雜湊命中時不發送重複 API PATCH。
- [x] **仍保留 pantheon-bus marker 與 command parsing**: 起始標記 `<!-- pantheon-bus -->` 依然存在且相容評論與 PR 過濾解析。
- [x] **不得建立第二個 PR writer 或 finalize path**: PR 建立依然由 `task_finalize.sh` 獨佔，Review Bus 僅更新既有 PR 或記錄 `missing_pr`。
- [x] **加入現有 PR body preservation 與中文 template regression**: 於 `test_github_bus.py` 新增 12 個針對性回歸測試。
