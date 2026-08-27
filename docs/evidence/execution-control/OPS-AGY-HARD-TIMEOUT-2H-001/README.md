# OPS-AGY-HARD-TIMEOUT-2H-001 驗收與證據紀錄

## 任務摘要
- **任務 ID**: OPS-AGY-HARD-TIMEOUT-2H-001
- **標題**: 恢復 Agy 兩小時 hard timeout 單一設定
- **負責人**: Antigravity2
- **評審人**: Codex2
- **目標**: 將所有 Antigravity 提供者（`antigravity` 與 `antigravity2`）的唯一 `agy` absolute wall-clock timeout 恢復為 2 小時（`2h`），徹底消除在追蹤權威範本、adapter 預設常數以及 live supervisor 設定中的 12h/168h 漂移。

## 變更範圍

1. **Adapter 預設常數** (`.orchestrator/adapters/antigravity.py`):
   - 將 `DEFAULT_HARD_PRINT_TIMEOUT` 從 `"168h"` 修改為 `"2h"`。
   - 更新註解說明，確認唯一的 absolute wall-clock timeout 為 2 小時。

2. **追蹤權威範本** (`.orchestrator/config.example.json`):
   - 將 `providers.antigravity.antigravity.hard_print_timeout` 從 `"168h"` 修改為 `"2h"`。
   - 將 `providers.antigravity2.antigravity.hard_print_timeout` 從 `"168h"` 修改為 `"2h"`。

3. **Live 協調設定** (`/home/lupin/odayplus/.orchestrator/config.json`):
   - 將 `providers.antigravity.antigravity.hard_print_timeout` 從 `"12h"` 修改為 `"2h"`。
   - 將 `providers.antigravity2.antigravity.hard_print_timeout` 從 `"12h"` 修改為 `"2h"`。

4. **Adapter 與 Rotation 單元測試** (`.orchestrator/test_model_rotation.py`):
   - 更新 `test_adapter_persists_dispatched_pool_in_worker_metadata`，斷言 `--print-timeout 2h`。
   - 在 `test_adapter_hard_print_timeout_wins_and_legacy_key_remains_compatible` 新增斷言，驗證回退至預設 `"2h"`。
   - 新增 `test_adapter_default_hard_print_timeout_is_2h`，驗證未設定的提供者預設帶入 `["--print-timeout", "2h"]`。

## 驗證證據

### 1. Model Rotation 與 Adapter 測試套件
```bash
$ uv run --no-project --python 3.12 --with pytest --with jsonschema --with pyyaml pytest .orchestrator/test_model_rotation.py -q
......................................                                   [100%]
38 passed in 0.92s
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

### 5. Supervisor Runtime 與更新後 Worker Argv 驗證
- **Supervisor 執行程序（Live）**:
  ```text
  lupin 3979693 python3 -u .orchestrator/supervisor.py --verbose
  ```
- **持久化 Worker Runtime 收據**:
  - 檔案: `/home/lupin/odayplus/.orchestrator/worker-runtime/status/antigravity-20260827T025914Z-485e7dcb.json`
  - 執行 ID: `antigravity-20260827T025914Z-485e7dcb`（啟動時間: `2026-08-27T02:59:14Z`，PID: 3995866）
  - 記錄之 Worker 命令:
    ```json
    [
      "/home/lupin/oday-plus-supervisor-runtime-fe698e88d916/.orchestrator/bin/agy",
      "--model",
      "gemini-3.7-flash-high",
      "--print-timeout",
      "2h",
      "--dangerously-skip-permissions",
      "--add-dir",
      "/tmp/pantheon-worker-worktrees/pantheon/ops-agy-hard-timeout-2h-001",
      "--prompt",
      "..."
    ]
    ```
- **活躍程序命令列驗證**:
  ```text
  lupin 3995866 /usr/bin/python3 .../worker_runner.py --run-id antigravity-20260827T025914Z-485e7dcb ... -- .../bin/agy --model gemini-3.7-flash-high --print-timeout 2h --dangerously-skip-permissions ...
  ```

## 驗收條件檢查清單
- [x] 追蹤權威設定 (`config.example.json`) 中兩個 Agy 提供者皆為 `hard_print_timeout: "2h"`。
- [x] Live 設定 (`/home/lupin/odayplus/.orchestrator/config.json`) 中兩個 Agy 提供者皆為 `hard_print_timeout: "2h"`。
- [x] Adapter (`antigravity.py`) 與 rotation 測試 (`test_model_rotation.py`) 驗證 `--print-timeout 2h`。
- [x] 不新增第二個 timeout 或 inactivity 機制（維持乾淨單一之 `hard_print_timeout`）。
- [x] 維持原子更新且不中斷既有執行中 worker。
- [x] 更新後 Supervisor runtime 已重載 live 設定，並在持久化收據 `/home/lupin/odayplus/.orchestrator/worker-runtime/status/antigravity-20260827T025914Z-485e7dcb.json` 中確認 worker argv 帶有 `--print-timeout 2h`。
