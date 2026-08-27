# Evidence: OPS-AGY-HARD-TIMEOUT-2H-001

## Task Summary
- **Task ID**: OPS-AGY-HARD-TIMEOUT-2H-001
- **Title**: 恢復 Agy 兩小時 hard timeout 單一設定
- **Owner**: Antigravity2
- **Reviewer**: Codex2
- **Objective**: Restore the single `agy` absolute wall-clock timeout to 2 hours (`2h`) across all Antigravity providers (`antigravity` and `antigravity2`), eliminating 12h/168h drift across tracked authoritative templates, adapter default constants, and live supervisor configuration.

## Scope of Changes

1. **Adapter Default Constant** (`.orchestrator/adapters/antigravity.py`):
   - Changed `DEFAULT_HARD_PRINT_TIMEOUT` from `"168h"` to `"2h"`.
   - Updated documentation comment to clarify that the single absolute wall-clock timeout is 2 hours.

2. **Tracked Authoritative Template** (`.orchestrator/config.example.json`):
   - Updated `providers.antigravity.antigravity.hard_print_timeout` from `"168h"` to `"2h"`.
   - Updated `providers.antigravity2.antigravity.hard_print_timeout` from `"168h"` to `"2h"`.

3. **Live Coordination Config** (`/home/lupin/odayplus/.orchestrator/config.json`):
   - Updated `providers.antigravity.antigravity.hard_print_timeout` from `"12h"` to `"2h"`.
   - Updated `providers.antigravity2.antigravity.hard_print_timeout` from `"12h"` to `"2h"`.

4. **Adapter & Rotation Unit Tests** (`.orchestrator/test_model_rotation.py`):
   - Updated `test_adapter_persists_dispatched_pool_in_worker_metadata` to assert `--print-timeout 2h`.
   - Added assertions to `test_adapter_hard_print_timeout_wins_and_legacy_key_remains_compatible` to verify fallback to default `"2h"`.
   - Added `test_adapter_default_hard_print_timeout_is_2h` verifying that unconfigured providers default to `["--print-timeout", "2h"]`.

## Verification Evidence

### 1. Model Rotation & Adapter Test Suite
```bash
$ uv run --no-project --python 3.12 --with pytest --with jsonschema --with pyyaml pytest .orchestrator/test_model_rotation.py -q
......................................                                   [100%]
38 passed in 0.92s
```

### 2. Config Schema and Wiring Validation
```bash
$ python3 delivery_toolchain/governance/check_orchestrator_config.py --config /home/lupin/odayplus/.orchestrator/config.json
Validated 3 config documents and their merged runtime views.

$ python3 delivery_toolchain/governance/check_config_wiring.py
All 166 config keys are read by production code.
```

### 3. Code Boundaries Check
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

### 4. Git Diff Cleanliness
```bash
$ git diff --check origin/dev
(clean exit 0)
```

### 5. Supervisor Runtime & Post-Update Worker Argv Verification
- **Supervisor Process (Live)**:
  ```text
  lupin 3979693 python3 -u .orchestrator/supervisor.py --verbose
  ```
- **Persisted Worker Runtime Receipt**:
  - File: `/home/lupin/odayplus/.orchestrator/worker-runtime/status/antigravity-20260827T025914Z-485e7dcb.json`
  - Run ID: `antigravity-20260827T025914Z-485e7dcb` (Started At: `2026-08-27T02:59:14Z`, PID: 3995866)
  - Recorded Worker Command:
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
- **Active Process Command Verification**:
  ```text
  lupin 3995866 /usr/bin/python3 .../worker_runner.py --run-id antigravity-20260827T025914Z-485e7dcb ... -- .../bin/agy --model gemini-3.7-flash-high --print-timeout 2h --dangerously-skip-permissions ...
  ```

## Acceptance Criteria Checklist
- [x] Tracked authoritative config (`config.example.json`) has `hard_print_timeout: "2h"` for both Agy providers.
- [x] Live config (`/home/lupin/odayplus/.orchestrator/config.json`) has `hard_print_timeout: "2h"` for both Agy providers.
- [x] Adapter (`antigravity.py`) and rotation tests (`test_model_rotation.py`) verify `--print-timeout 2h`.
- [x] No second timeout or inactivity mechanism added (clean single `hard_print_timeout`).
- [x] Atomic update and non-disruption of executing workers preserved.
- [x] Post-update supervisor runtime reloaded live config and worker argv confirmed with `--print-timeout 2h` in persisted receipt `/home/lupin/odayplus/.orchestrator/worker-runtime/status/antigravity-20260827T025914Z-485e7dcb.json`.
