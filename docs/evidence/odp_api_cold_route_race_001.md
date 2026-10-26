# ODP-API-COLD-ROUTE-RACE-001 Evidence

修復 API 冷啟動並行路由間歇 404，解除 PR #1229 的 performance CI 阻塞。

- Base: `dev` @ `c4bf87d81d55180d6d6769daf5358992b3bc6620`
- Task branch: `task/ODP-API-COLD-ROUTE-RACE-001`
- FastAPI: `0.138.1`（`uv.lock` 釘住）
- 環境: `uv run --frozen --python 3.12`（cp314 缺 `pgserver` wheel，必須釘 3.12）

## 1. 根因

`fastapi/routing.py` 的 `_IncludedRouter.effective_candidates()` 就地填充共享
的 memo：

```python
self._effective_candidates = []                       # (1) 立刻對所有執行緒可見
for route in candidates:
    ...
    self._effective_candidates.append(route_context)  # (2) 每圈重新解析屬性
self._effective_candidates_version = routes_version   # (3) 蓋版本號
return self._effective_candidates                     # (4) 回傳當下的內容
```

冷啟動時兩個請求同時走到同一個 branch：

1. A 通過版本檢查，把屬性綁成 `L1`，append 前段路由（含 `POST /jobs`）。
2. B 也通過同一個版本檢查（A 還沒到 (3)），把屬性綁成新的 `L2`。
3. A 剩下的 `self._effective_candidates.append(...)` 因為每圈重新解析屬性，
   全部落進 `L2`。
4. A 在 (3) 蓋上版本號、在 (4) 回傳 `L2` —— 少了 A 先前放進 `L1` 的前段路由。

A 於是對一份「`/jobs` 被刪掉」的路由表做比對而回 404；而且截斷的表已經帶著
當前版本號，之後只要路由集合不變就會被快取住，持續回 404。
`effective_low_priority_routes()` 有完全相同的形狀。

PR #1229 的 performance 測試正是這個形狀：建好全新 app 後立刻打並行波次。

## 2. Before：在目前 dev 上重現

前置診斷來自 ODP-CODEX-ULTRA-DRIFT-REPAIR-001（Claude2）的 `/tmp/repro_widen.py`，
只在 `_build_effective_context` 注入 0.2ms 延遲來加寬重建視窗，不改任何 production 行為。
在本 task 自有 worktree（base `c4bf87d8`）重跑確認：

```
PYTHONPATH=. uv run --frozen --python 3.12 python repro_widen.py widen 8
RESULT MODE=widen ROUNDS=8 CODES={202: 75, 404: 5}
```

因果反證（同一支腳本的 `widen_locked` 模式序列化該函式）在原診斷為 80/80 202。

## 3. Fix

`shared/api/route_table_safety.py` 的
`ensure_atomic_route_table_publication()` 只改**發佈時機**：重建改為累積到區域
list，最後一次指派回實例。讀者只會看到舊表或完整新表，不可能看到填到一半的表。
`create_app()` 在掛載任何 router 之前呼叫一次（idempotent）。

明確不做的事：

- **不加鎖**、不序列化路由查詢。缺陷只存在於「發佈」瞬間，為它讓每個請求排隊
  是不成比例的。
- **不在 `create_app` 全量預熱**。實測預熱本 app 全部 68 個 branch 需
  3.35–4.38 s，而請求實際只走到少數 branch（兩個請求後只有 4/38 個 branch 被
  建起，共 ~166 ms）。測試套件每個測試都建 app，全量預熱會被整碗放大。
- 不加 retry、不加暖身請求、不動任何路由 / 授權 / tenant 隔離 / response schema
  / 效能門檻 / audit suppression / waiver。重建出的表與單執行緒下 FastAPI 自己
  建的完全一致（相同內容、相同順序、相同版本號語意）。

### 使用框架 private API 的相容性與 fail-closed

`_IncludedRouter` 與它的 memo 欄位是 FastAPI 私有介面，因此安裝時檢查形狀：

- `_IncludedRouter` 不存在 → 該版本沒有這個 lazy per-branch memo，沒有東西要
  改，安裝函式安靜返回。
- `_IncludedRouter` 存在但本模組要改寫的任一屬性不見了 → 內部結構已經改動，
  本模組無法再保證原子發佈，丟出 `RouteTableSafetyError`。因為它在
  `create_app()` 開頭呼叫，服務會直接拒絕啟動，而不是安靜退回會掉路由的舊行為。

## 4. 回歸測試

`tests/reliability/test_cold_start_route_table.py`，三個案例，**強制交錯**而非
碰運氣：把第一條執行緒停在剛解析完 `/jobs` 的位置，放第二條進來開始自己的重建，
再讓第一條收尾發佈；證據在放行第二條之前就先取樣（否則第二條會把證據補回去）。

- `test_cold_start_route_table_is_published_whole[alias]` → `POST /jobs`
- `test_cold_start_route_table_is_published_whole[versioned]` → `POST /api/v1/jobs`
- `test_concurrent_cold_start_wave_keeps_every_route` → 2 輪 × 12 執行緒 barrier
  同步波次，兩條路徑各半，加寬視窗只在 `create_app()` **之後**注入。

框架若不再用 lazy per-branch memo，這些測試會 skip —— 與修復本身 no-op 的條件一致。

## 5. 收據

修復前（只把 `apps/api/oday_api/main.py` 的接線還原，讓新模組不被 import）：

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest \
  tests/reliability/test_cold_start_route_table.py -p no:randomly --no-header -rA
exit=1 — 3 failed, 2 warnings in 46.42s
  [alias]     assert _serves_jobs(first_table) -> False
  [versioned] assert _serves_jobs(first_table) -> False
  wave        6 of 24 cold-start requests failed to route (404 Not Found)
```

修復後（HEAD）：

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest \
  tests/reliability/test_cold_start_route_table.py -p no:randomly --no-header -rA
exit=0 — 3 passed, 1 warning in 33.30s
```

既有 performance gate（未更動門檻、concurrency、volume）：

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest \
  tests/performance/test_load_and_soak.py -m performance -p no:randomly --no-header -rA
exit=0 — 1 passed, 1 warning in 19.68s
load_soak_performance_report.json:
  success_count=150  failure_count=0  errors=[]
  latency_p50=0.9995s  p95=1.3148s  p99=1.4394s
  budget_p95_seconds_target=3.0  passed=true
```

Boundary inventory（新增 `.py` 必然改到，`task_finalize` 的必過閘）：

```
uv run --frozen --python 3.12 python \
  delivery_toolchain/governance/check_code_boundaries.py --write-inventory
uv run --frozen --python 3.12 python \
  delivery_toolchain/governance/check_code_boundaries.py
exit=0 — Code boundary checks passed for 1139 files.
```

Focused 迴歸（路由 / 版本別名 / OpenAPI 契約 / 授權邊界 / 健康檢查 / 並行復原）：
見 §6。

## 6. Focused regression 收據

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest \
  tests/contract/test_api_versioning.py \
  tests/contract/test_platform_api.py \
  tests/contract/test_openapi_artifact_and_client.py \
  tests/contract/test_api_trust_contract.py \
  tests/integration/test_auth_boundary_authz.py \
  tests/reliability/test_health_endpoints.py \
  tests/reliability/test_concurrency_recovery.py \
  tests/reliability/test_cold_start_route_table.py \
  -p no:randomly --no-header
exit=0 — 118 passed, 1 warning in 188.65s (0:03:08)
```

涵蓋：`/api/v1` 與 unversioned alias 的雙掛載契約、OpenAPI artifact 與產生的
client（路由順序不變、無 artifact drift）、API trust contract、授權邊界與
tenant 隔離、健康檢查、既有並行復原測試，以及本 task 新增的冷啟動回歸。

未重跑不相關的完整套件。

## 7. 交回

合併後把 merge SHA 交回 ODP-CODEX-ULTRA-DRIFT-REPAIR-001 供整合並重跑其 required CI。
本修復**不**代表該 config task 的 Astra/ultra 驗收自動完成，也**不**授權任何部署。
