# ODP-AVM-QUALITY-NULLABLE-001

## 處置

`ValuationInput.quality_score` 現在代表一個「可有可無的量測值」。省略與顯式 `null`
在 API 解析、domain mapping、durable case 序列化與 API 回應中都維持 `None`。
normalize/value 邊界會以具名理由拒絕未量測的輸入：
`quality_score is required before AVM valuation; input quality is unmeasured`。
該路徑不會持久化任何估值報告或估值卡。

## 欄位傳遞鏈

| 層 | 負責符號／產物 | 缺值行為 |
|---|---|---|
| API request | `apps/api/app/routes/avm.py::AVMCasePayload.quality_score` | 型別 `float \| None`，預設 `None`；省略與 JSON `null` 都被保留 |
| Domain parser | `modules/avm/domain/valuation.py::ValuationInput.from_mapping` | `quality_score` 與舊別名 `data_quality_score` 只在存在時做上下界收斂，不再有滿分 fallback |
| Domain case | `modules/avm/domain/valuation.py::ValuationInput.to_dict` | 輸出 `quality_score: null`，讓缺值仍可被觀察到 |
| Service 邊界 | `modules/avm/application/valuation.py::AVMService.normalize` | 在改變 case 狀態或寫入 margin 之前就拒絕 |
| Domain 消費端 | `normalize_margin`、`value_store`、`build_model_valuation_report` | 在推導信心或報告之前拒絕缺值；對不透明的 legacy margin 做冪等降級 |
| Durable case | `shared/infrastructure/persistence/repositories.py::DurableAVMRepository` | pickle 持久化保留 `None` 不做強制轉換；只有 payload 早於 status 欄位的 case 會被寫成 `legacy_unknown` |
| 歷史報告 | `ValuationReport.with_legacy_quality_disposition`、`latest_report`、`report_history` | legacy 報告持久化為 `legacy_unknown_downgraded`，對外只暴露 `low` 信心，且不會帶著仍可執行的舊核准 |
| 歷史 data room | `DataRoom.with_legacy_quality_disposition`、`get_dataroom` | 保留歷史價格供稽核，但對外是具名的低信心降級；rebuild 與 export 會被拒絕 |
| Operator 估值卡 | `modules/opsboard/application/network_rebalance.py::NetworkRebalanceService._reproject_avm_card` | 依卡片自己指名的 `reportId` 重新推導品質宣稱；解析不到就 fail closed |
| Operator 畫面 | `apps/web/features/operator/network/RebalancePanel.tsx::describeAvmQuality` | 降級與無法驗證的卡片各有具名標題與說明，不再一律顯示為 service output |
| PostgreSQL | `infra/db/migrations/000024_avm_quality_score_nullable.sql` 與 Alembic `0018` | 移除 `NOT NULL` 與 `DEFAULT`；舊列保留原值並取得 `legacy_unknown` 狀態 |
| SQLite | `infra/db/migrations/000024_avm_quality_score_nullable_sqlite.sql` | 以可為 null 的欄位重建資料表；前後各 commit 一次 PRAGMA，讓 `foreign_keys=1` 在全新與重啟後的 engine 上都保留並拒絕孤兒子列 |
| API 契約 | `packages/openapi-client/openapi.json` | `quality_score` 為 `number \| null`，無 default，且非必填 |
| TypeScript | `packages/openapi-client/src/generated/types.ts`、`src/index.ts` | `quality_score?: number \| null` |
| Governance | `delivery_toolchain/governance/measurement_default_exemptions.json` | AVM 豁免在同一次變更中移除 |

## 舊資料處置

前向 migration 不會把歷史上 `quality_score = 1.00` 的列改成 `NULL`：舊 schema
本來就分不出「量測到滿分」與「根本沒填」。既有列保留原值，並標記
`quality_score_status = 'legacy_unknown'`。新的寫入端（例如
`LineageManifest.to_audit_snapshot_row()`）會顯式輸出 `measured` 或 `unmeasured`。
在 `DurableAVMRepository` 中，缺少顯式 status 標記的 legacy pickled case 會在讀取時
被遷移為 `quality_score_status = 'legacy_unknown'`，並把該標記寫回 case。

「缺少顯式 status 標記」指的是**儲存的 payload 早於這個欄位**，而不是呼叫端沒有傳。
`ValuationInput.is_pre_status_payload` 檢查 `quality_score_status` 是否不在 instance
dict 中，而這只可能發生在反序列化前一版寫入的紀錄時：`__init__` 一定會寫入每個欄位。
因此一個帶有量測 `quality_score`、只是沒傳 status 的全新輸入會被判為 `measured`，
在 in-memory 與 durable 兩條路徑上都保留原本的信心與價格；
`DurableAVMRepository._migrate_legacy_case` 只改寫 pre-status payload。
`test_fresh_input_with_omitted_status_is_measured_not_legacy` 涵蓋 domain、in-memory
與 durable 三個入口，而 legacy 測試改以「從 payload 移除欄位」來模擬舊紀錄，
不再用傳 `None` 假裝。

進入估值時，狀態為 `legacy_unknown` 的 case 會取得具名的
`legacy_quality_unknown_discount` 與保守的 `low` 信心——即使先前已持久化了一個高信心
margin 也一樣；service 會在進入公式或已核准的 production executor 之前先存下降級後的
margin，並在回傳前標記報告。這讓「既有 margin 快速路徑」無法繞過 legacy 處置。
歷史報告與 data room 同樣在讀取時遷移：價格仍可供稽核，但報告與估值卡會被標記為
`legacy_unknown_downgraded`，所有對外暴露的信心都是 `low`，任何舊的財務核准只會保留在
`legacy_finance_approval` 之下。新的財務核准、data room rebuild 與 export 都必須以
量測到的品質重新計算。

## Operator durable 估值卡（第 8 輪 reopen 的 P1）

Operator 的 rebalance 估值卡是一份持久化在 rebalance state 裡的 **projection**，不是
報告本身。因此重啟後回來的是「估值完成當下寫進去的那個 confidence」。這正是缺陷：
一張在品質處置存在之前產生的卡片，重啟後仍然宣稱高信心，即使它的 case 已經分不出
量測滿分與缺值。

修正沿用既有的 canonical projection（`_refresh_canonical_stores`），不另建 quality gate：

- 卡片依**自己指名的 `reportId`** 到 `report_history(case_id)` 取回那一份報告，
  而不是拿 case 的 `latest_report`。後者描述的是另一次估值，不能代替這張卡片發言。
- 取回的報告已經套用過 legacy 處置（`report_history` 負責），因此
  `confidence`、`quality_score_status`、`quality_disposition` 直接由它決定：
  measured 卡片維持原信心，不透明的卡片才降級。
- **價格不動**。P10/P50/P90 是操作者當時看到的歷史紀錄，仍留在卡片上。
- 解析不到那份報告時 **fail closed**：`confidence` 與 `quality_score_status` 清為
  `null`，`quality_disposition` 標為 `unverifiable_report_reference`。不會有任何
  其他報告被拿來頂替。

卡片產出當下（`complete_avm`）也會一併蓋上 `qualityScoreStatus` /
`qualityDisposition`，讓即時回應與之後讀取重新推導的結果一致。
`_view_store` 以 `avmQualityScoreStatus`、`avmQualityDisposition` 對外暴露，
`RebalanceStore` 回應模型與 OpenAPI／generated client 同步擴充。

畫面端（`RebalancePanel.tsx`）不再把每張卡片都寫成「AVM 估值（service output）」：

| disposition | 標題 | 信心欄 |
|---|---|---|
| （無） | AVM 估值（service output） | 報告的 confidence |
| `legacy_unknown_downgraded` | AVM 估值（歷史卡片 · 品質未量測） | `low`，並附具名說明 |
| `unverifiable_report_reference` | AVM 估值（來源報告無法驗證） | 不宣稱 |

`不宣稱` 是刻意選的字面：`—` 會被讀成「沒有提供」，而這裡要說的是「不對它作任何宣稱」。

## 搜尋邊界

task 範圍的搜尋涵蓋 `modules/avm`、`apps/api/app/routes/avm.py`、
`infra/db/migrations`、`packages/openapi-client`、`shared/infrastructure/persistence`、
`shared/infrastructure/persistence/model_ready.py`、
`modules/opsboard/application/network_rebalance.py`、
`apps/web/features/operator/network/RebalancePanel.tsx` 與 tests。
AVM 品質路徑上已無 `quality_score`／`data_quality_score` 退回 `1.0` 的 fallback；
既有 margin 的 production-entry 路徑對已持久化的 legacy 資料也有覆蓋。
搜尋樹中剩下的 `1.0` 屬於不相關的 bounded 預設值或歷史 migration。
governance checker 已執行，確認 AVM dataclass 豁免不再生效。

## Base advance

### 第二次（2026-09-05，本輪）

本輪 base 為 `origin/dev` `eed8d51bb8a1`，以 merge commit 併入，第一父為上一個
approved head `9cf97feb`；沒有 rebase、沒有 reset、沒有 force push。

ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001 先落地並占用了 `0017` / `000023` 兩個
migration 槽位，因此本 task（較晚到者）再次讓號：

| 原本 | 現在 |
|---|---|
| `infra/db/migrations/000023_avm_quality_score_nullable.sql` | `000024_avm_quality_score_nullable.sql` |
| `infra/db/migrations/000023_avm_quality_score_nullable_sqlite.sql` | `000024_avm_quality_score_nullable_sqlite.sql` |
| `infra/db/migrations/versions/0017_avm_quality_score_nullable.py` | `versions/0018_avm_quality_score_nullable.py` |
| Alembic `revision="0017"`, `down_revision="0016"` | `revision="0018"`, `down_revision="0017"` |

鏈結因此是 `0016（manual corrections）-> 0017（heatzone composition）-> 0018（本 task）`，
而不是兩個 revision 共用 `0017`。

在合成後的樹（而非合併前的 head）上做的跨層檢查：

- 兩個衝突檔案都**兩邊都留**，不是二選一。`tests/ops/test_migration_backfill.py`
  的預期 revision 清單補到 `"0018"`，兩個 reachability 測試各自保留：heatzone 對
  `0017`，本 task 對 `0018`。
- `docs/audits/code-boundary-inventory.csv` 同時收錄
  `0017_heatzone_composition.py` 與 `0018_avm_quality_score_nullable.py`。
  重產只在 merge index 完全解決之後才做——在還有未合併路徑時重產會產生重複列，
  因為 `git ls-files` 會為每個 conflict stage 各印一次，而 checker 對那份被污染的
  輸出仍然會通過。`cut -d, -f1 ... | sort | uniq -d` 為空。
- `shared/infrastructure/persistence/engine.py` 的 SQLite bootstrap 清單跟著改為
  `000024_avm_quality_score_nullable_sqlite.sql`，仍排在最後。
- `packages/openapi-client/openapi.json` 與 `src/generated/types.ts` 由合成後的 app
  重新產生，本輪新增 `avmQualityScoreStatus` / `avmQualityDisposition` 兩個
  `string | null` 欄位；`quality_score` 的 nullability 不變、未被任一分支收窄。

### 第一次（2026-09-05，較早的 head）

PR #1149 在 ODP-INT-MANUAL-CORRECTION-AUDIT-001 落地後被 merge queue 以
`CONFLICTING` 退出。當時的 `dev`（`6c4a8be8`）以 merge commit 併入，approved head
`974c8904` 保留為第一父。那次同樣是槽位相撞後讓號：

| 原本 | 當時改為 |
|---|---|
| `infra/db/migrations/000021_avm_quality_score_nullable.sql` | `000023_avm_quality_score_nullable.sql` |
| `infra/db/migrations/000021_avm_quality_score_nullable_sqlite.sql` | `000023_avm_quality_score_nullable_sqlite.sql` |
| `infra/db/migrations/versions/0016_avm_quality_score_nullable.py` | `versions/0017_avm_quality_score_nullable.py` |

那一輪也有一個 git 沒有標示的手動修正：兩個分支都在預期 revision 清單尾端加了字面
完全相同的 `"0016",`，merge 把它們併成一行而不是報衝突。

## 驗證

以下命令都在 task worktree、於當時的 head 上執行。專案 virtualenv 是 CPython 3.12；
repository 預設的 CPython 3.14 環境裝不了釘住的 `pgserver==0.1.4` wheel，因此所有命令
都經由 `uv run --frozen --python 3.12` 執行。

### 本輪 head（base advance 至 dev `eed8d51bb8a1` + Operator 卡片投影）

所有命令都在合成後的樹上重跑，不是從合併前的 head 沿用。

**Python（focused，`uv run --frozen --python 3.12`）**

```
pytest tests/integration/test_operator_canonical_wiring.py \
  tests/contract/test_operator_network_rebalance_api.py \
  tests/integration/test_avm_valuation.py \
  tests/integration/test_avm_deal_outcome.py \
  tests/integration/test_model_ready_materialization.py \
  tests/ops/test_migration_backfill.py \
  tests/ops/test_avm_quality_nullable_migration.py \
  tests/ops/test_heatzone_composition_migration.py \
  tests/contract/test_openapi_artifact_and_client.py \
  tests/contract/test_netplan_disclosure_transport.py \
  modules/avm/tests/ \
  delivery_toolchain/governance/test_check_measurement_defaults.py
```

— `190 passed, 8 xfailed, 9 warnings in 183.59s`，exit 0。清單中的
`test_heatzone_composition_migration.py` 與 `test_operator_network_rebalance_api.py`
是為了證明本輪 base advance 與投影修正沒有弄壞併進來的 base，不是本 task 的交付物。

註：`pyproject.toml` 的 `addopts = "-q"` 加上命令列再給一次 `-q` 會變成 `-qq`，
pytest 會**完全不印**總結行。上面的數字來自不帶額外 `-q` 的那一次執行。

**新增測試的反向對照（確認測到的是缺陷路徑，而不是空過）**

暫時把 `_refresh_canonical_stores` 中的 `self._reproject_avm_card(row)` 換成
`pass` 後重跑
`tests/integration/test_operator_canonical_wiring.py::test_durable_rebalance_card_cannot_outlive_the_quality_claim_it_was_written_with`
— exit 1，停在 `assert row["avmQualityScoreStatus"] == "legacy_unknown"`；
而在此之前的 measured 卡片斷言全部通過。也就是說這個測試抓的正是「重啟後舊卡片
仍宣稱高信心」這條路徑，而不是連帶把 measured 卡片一起判紅。修正後檔案已還原。

**Web（`npm --workspace=apps/web`）**

- `npm run test --workspace=apps/web -- --run features/operator/network/__tests__/RebalanceAvmQualityDisposition.test.tsx features/operator/network/__tests__/RebalanceDisclosurePartition.test.tsx`
  — `Test Files 2 passed (2)`、`Tests 19 passed (19)`，exit 0。
  新檔 `RebalanceAvmQualityDisposition.test.tsx` 涵蓋 measured／legacy 降級／
  無法驗證三種卡片的標題、信心欄與 `data-quality-disposition`。
- `npm run typecheck --workspace=apps/web`（`tsc --noEmit`）— exit 0。
- `npm run lint --workspace=apps/web` — `✔ No ESLint warnings or errors`，exit 0。

**Gates**

- `uv run --frozen --python 3.12 python delivery_toolchain/openapi/check_drift.py`
  — `API contract gate: PASS`；artifact 與 generated client 都是新的，
  `0 additive, 1 approved breaking, 0 unapproved breaking`（本 task 已核准的
  `quality_score` nullability）。
- `uv run --frozen --python 3.12 python delivery_toolchain/governance/check_code_boundaries.py`
  — `Code boundary checks passed for 1128 files`；
  `cut -d, -f1 docs/audits/code-boundary-inventory.csv | sort | uniq -d` 為空。
- `uv run --frozen --python 3.12 python delivery_toolchain/governance/check_measurement_defaults.py`
  — passed：15 known（dataclass 6、mapper 4、sql 5），15 exempted with an owner；
  next expiry 2026-10-31。
- `uv run --frozen --python 3.12 ruff check <相對 origin/dev 變動的 22 個 .py>`
  — `All checks passed!`。
- `git diff --check origin/dev...HEAD` 與 `git diff --check` — 皆為 exit 0。
- Alembic：單一 head `0018`，鏈結 `0016 -> 0017 -> 0018`。


### 歷史收據（先前 head，未於本輪重跑）

以下是先前 head 上留下的收據，保留供對照，**不代表本輪的量測**；本輪重跑的範圍
以上一節為準。

- 合成至 dev `6c4a8be8` 的 head：`uv run --frozen pytest -q`（12 個檔案，含 base 自身的
  manual-correction contract 與 persistence suite）— 208 passed, 0 failed。
- 同一 head：`uv run --frozen python delivery_toolchain/openapi/check_drift.py` — PASS，
  `0 additive, 1 approved breaking, 0 unapproved breaking`。
- 同一 head：`check_code_boundaries.py` — 1112 files 通過。
- fresh-vs-legacy 判別的 head：`uv run --frozen pytest -x -q tests/integration/test_avm_valuation.py`
  — 13 passed，含 `test_fresh_input_with_omitted_status_is_measured_not_legacy`。
- 同一 head：`uv run --frozen pytest -q`（7 個檔案）— 72 passed，含
  `test_sqlite_engine_enforces_foreign_keys_and_rejects_orphan_child_inserts_on_fresh_and_restart`。
- 同一 head：`uv run --frozen pytest -q tests/contract/test_openapi_artifact_and_client.py` — 23 passed。
- OpenAPI breaking-change 核准的 head：`make api-contract` 與
  `uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling` — passed。
