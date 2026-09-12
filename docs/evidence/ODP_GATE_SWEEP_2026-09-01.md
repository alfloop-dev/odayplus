# 閘的清查：13 道閘，哪些會失敗、哪些跑得到

- 日期：2026-09-01
- 基準：`origin/dev` @ `62c6c2de`
- 起因：[FR 查證報告](ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md) 的成因四——「閘存在、內容正確、但結構上不會被執行」
- 方法：對每一道宣稱會擋住某件事的東西，問兩個問題——**什麼輸入會讓它失敗**、**那個輸入跑在實際會執行的路徑上嗎**
- 狀態：清查為唯讀；其後的修正在分支 `task/ODP-GATE-EXECUTION-PATH-001`，尚未合併

---

## 主要量測

> **數字更正。** 本文件初稿寫「190 條」，那是把只標了一兩條的檔案整檔的測試數加總，錯的。
> 權威數字取自 `pytest -m requires_live_env --collect-only`：**101 條**。以下為精算後的版本。

**89 條測試從不在 CI 執行。**

| | 條數 |
|---|---:|
| 帶 `requires_live_env` 的測試總數 | 101 |
| — 其中 `make security` 會跑到（`tests/security/`） | 7 |
| — 其中有專用步驟（`test_official_real_estate_postgresql.py`） | 5 |
| **從不執行** | **89** |
| — 其中真的需要 live 多 repo 環境（`.orchestrator/`，marker 用得正確） | 10 |
| **從不執行、但其實只需要一個 PostgreSQL** | **79** |

那 79 條跑得起來：`pgserver` 是專案依賴，自帶 PostgreSQL 16，不需 root、不需 service container。
`make security` 一直以來就是這樣跑資料庫測試的。

把它們在本機跑起來的結果：

```
2 failed（修正前）
1 failed（補 uuid-ossp stub 後）
0 failed（把需要 PostGIS 的那條獨立標記後）
```

兩條紅，處理如下：

1. `tests/contract/test_assisted_listing_intake_schema.py::test_schema_validator_script_passes`
   —— 路徑常數指向不存在的檔案，**真缺陷**（詳見閘 #11）。修好後**第一次真的執行，而且通過**——
   schema 本身合規，FORCE RLS、fail-closed tenant policy、tenant-qualified FK 都在。
   （我先前預測「修好會立刻是紅的」，那個預測錯了。）
2. `tests/integration/test_assisted_listing_postgresql_runtime.py::test_full_stack_composes_after_canonical_migration_and_is_idempotent`
   —— 先是 `extension "uuid-ossp" is not available`，補 stub 後變成 `extension "postgis" is not available`。
   **PostGIS 不能 stub**：它提供真的幾何型別與空間述詞，假造一個會接受值卻不算東西的 `geometry`，
   正是這份清查在獵的那個形狀。這條改標 `requires_postgis`，等有 PostGIS service container 再收。

**開啟這 79 條的代價**：一個路徑常數修正、一個 conftest stub、一個 CI 步驟。
收穫是 schema 契約、migration 冪等性、Decision Policy registry schema 的測試從此會執行。

---

## 分類一：已證明會失敗，且跑在會執行的路徑上

這五道是健康的，而且它們是其餘八道的範本。

### 1. `check_code_boundaries`

8 條測試，含真正的違規案例：`test_product_cannot_import_development_tooling`、`test_removal_bundle_rejects_foreign_code_and_missed_scope`、`test_invalid_python_is_reported`。跑在 orchestrator job（不受 scope skip 影響）。

### 2. `classify_change_review_scope`

7 條測試，其中 `test_empty_change_is_not_automatically_approved` 正是 fail-closed 的直接證明。跑在 orchestrator job。

### 3. 供應鏈六閘

`tests/security/test_supply_chain_security_gate.py` 有六條 `*_rejected_negative`，每一條植入違規再斷言 `returncode != 0`：

```
test_stale_lockfiles_rejected_negative
test_generated_client_drift_rejected_negative
test_vulnerable_fixtures_rejected_negative
test_unsigned_images_rejected_negative
test_invalid_provenance_rejected_negative
test_leaked_test_secrets_rejected_negative
```

**這是全 repo 最完整的閘證明範本。** 其餘要補證明的閘應該照抄這個形狀，不需要另外發明。

### 4. `sign_images.sh`

歷史上的假閘（cosign 缺席時一律印 PASSED）已修好：

```bash
require_cosign() {
  if ! command -v cosign >/dev/null 2>&1; then
    echo "Error: cosign is required ...; refusing to simulate success." >&2
```

註解明寫「Missing cosign must fail before any success text is」。另有 `tests/release/test_sign_images.py`。

### 5. assisted listing RLS 與資料落地

`tests/security/test_assisted_listing_intake_rls.py`（6 條）與 `test_assisted_listing_snapshot_residency.py`（4 條）雖然標了 `requires_live_env`，但 `make security` 執行 `pytest tests/security` **不帶 marker 過濾**，而 `pgserver` 是依賴，所以它們在 CI 帶著真的 PostgreSQL 跑起來。

> 修正先前的說法：我一度認為 RLS 測試從不執行。錯的。`make security` 這條路徑救了它們。

---

## 分類二：閘存在，但沒有「它會失敗」的證明

### 6. `check_orchestrator_config`

```
grep -rn "check_orchestrator_config" --include=*.py .   →  0 命中
```

它跑在 orchestrator job 的「Check config wiring」步驟，但全樹沒有任何 Python 檔引用它，也就是**零測試**。沒有東西證明它在 config 壞掉時會失敗。

### 7. ABAC 的 `scope.brand` 與 `scope.module`

`tests/security/test_rbac_abac.py` 涵蓋的 deny 路徑：

```
tenant_isolation · scope.region · scope.store · data_classification
franchisee_isolation · high_risk.feature_flag · high_risk.separation_of_duties
```

缺 `scope.brand` 與 `scope.module`。六軸中兩軸沒有拒絕證明。

### 8. `MetricThreshold`（效能退化）

```
models/shared_ml/validation.py:28  MetricThreshold.evaluate(self, value: float)
```

只吃觀測值，沒有基線參數。這不是「缺一條測試」，是**「效能退化」這件事在系統裡沒有任何門檻能表達**——`baseline_metrics` 全樹 20 處，無一處比較。詳見 FR 報告 `LH-005`。

---

## 分類三：閘有證明，但證明無效

### 9. ABAC `tenant_isolation` —— removed-by-decision（2026-09-02）

原 finding 的測試存在而且是綠的：

```python
# tests/security/test_rbac_abac.py:79
def test_tenant_isolation_blocks_other_tenant() -> None:
    p = principal(Role.OPERATIONS_MANAGER, tenant_id="tenant-a")
    res = ResourceDescriptor(type="forecastops", tenant_id="tenant-b")
    ...
    assert decision.policy_id == "tenant_isolation"
```

它**手工建構**了一個 tenant 與 principal 不同的資源，證明函式會拒絕。

但生產路徑不是這樣建的：

```python
# apps/api/oday_api/security/dependencies.py:551, 576, 862
ResourceDescriptor(type=resource_type, tenant_id=principal.tenant_id, ...)
```

資源的 tenant **取自 principal 自己**。`tenant-b` 在真實路徑上永遠不會出現，該子句結構上不可能 deny。

另外它與五個兄弟軸的 None 處理相反：

```python
shared/auth/abac.py:96   tenant_isolation
    if resource_tenant is None: return None      ← 棄權
shared/auth/identity.py:85  _axis_allows（brand/region/store/area/heat_zone 共用）
    if value is None: return False               ← 拒絕
```

**裁定：removed-by-decision。** 這道 ABAC 子句在 API 路徑上結構上不可能 deny；本 PR 移除 `tenant_isolation` 函式、default policy chain entry，以及只餵入生產路徑不會產生的 cross-tenant 資源的手工測試。這不是破口修補，也不改變資料層的真正隔離。

資料層查證（本次 PR）：

- Operator Live 的 stores / transactions read chain 在
  `modules/opsboard/application/operator_live_repository.py` 傳入已驗證的
  `scope.tenant_id`；`DurableStoreRepository.list_stores` 與
  `DurableTransactionRepository.list_transactions` 都先呼叫
  `shared/infrastructure/persistence/repositories.py:_require_tenant_scope`。
- assisted-listing 的 tenant-bearing SQL 讀寫都透過
  `SourceSnapshotService._execute(..., tenant_id=...)`，在 SQL 前設定
  `app.tenant_id`；`004_tenant_rls_lineage.sql` 對該契約的 tenant tables
  同時啟用、強制 RLS 與 fail-closed policy。
- 本次查證沒有發現上述 API/data paths 會在未經 `_require_tenant_scope` 或
  RLS-protected session 的情況下進入 tenant-scoped read；上述 enforcement
  及 assisted-listing RLS 均未修改。

**這道閘給出的教訓，比它本身重要：**

> 一道閘的負向測試，必須用**生產環境建構輸入的方式**去建構輸入。
> 手工組裝輸入的測試，恰好繞過了那個讓閘失效的接線。

原 finding 比「每道閘都要有負向測試」更精確的教訓是：負向測試必須以生產建構方式餵入輸入；這個測試雖然曾經存在，卻沒有證明生產閘會觸發。

---

## 分類四：閘不在會執行的路徑上

### 10. 89 條 `requires_live_env` 測試不在任何 CI 路徑上

全部 7 個 workflow 裡只有四個 pytest 呼叫：

```
ci.yml:106  -m "not requires_live_env"                  .orchestrator delivery_toolchain scripts tests/tooling
ci.yml:179  -m "not requires_live_env and not performance"  tests modules apps shared models -n auto
ci.yml:182  tests/integration/test_official_real_estate_postgresql.py    ← 唯一的專用步驟
ci.yml:248  -m performance tests/performance
```

marker 的註冊說明本身就寫著「**and is excluded from CI**」——所以這不是有人忘了設定，
是它被設計成逃生門，然後 101 條測試堆積在後面。

問題在於這個 marker **混了兩件不同的事**：

- 真的需要 live 多 repo／CLI／本機路徑環境（`.orchestrator/test_coordination_file_watcher.py`
  的註解明講「cross-repo coordination against sidecar checkouts ... that do not exist in a
  clean CI runner」——marker 用得完全正確）
- 只是需要一個 PostgreSQL（跑得起來：`pgserver` 是專案依賴，`make security` 一直這樣跑）

逐檔（數字取自 `pytest --collect-only`，非檔案總測試數）：

| 檔案 | 帶 marker | CI 可達 |
|---|---:|---|
| `tests/ops/test_assisted_listing_intake_migration.py` | 21 | 排除 |
| `tests/contract/test_decision_policy_registry_schema.py` | 19 | 排除 |
| `tests/contract/test_assisted_listing_intake_schema.py` | 16 | 排除 |
| `tests/integration/test_place_geography_backfill.py` | 13 | 排除 |
| `.orchestrator/test_coordination_file_watcher.py` | 9 | 排除（正當——需要 sidecar checkouts）|
| `tests/integration/test_assisted_listing_postgresql_runtime.py` | 5 | 排除 |
| `tests/integration/test_model_ready_geo_views.py` | 2 | 排除 |
| `.orchestrator/test_provider_permissions.py` | 1 | 排除（正當）|
| `tests/integration/test_store_opening_backfill.py` | 1 | 排除 |
| `tests/integration/test_operator_live_provenance_health.py` | 1 | 排除 |
| `tests/integration/test_assisted_listing_snapshots.py` | 1 | 排除 |
| `tests/security/test_assisted_listing_intake_rls.py` | 6 | **跑**（`make security`）|
| `tests/security/test_assisted_listing_snapshot_residency.py` | 1 | **跑**（`make security`）|
| `tests/integration/test_official_real_estate_postgresql.py` | 5 | **跑**（專用步驟）|

排除合計 **89 條**，其中 **79 條只需要一個 PostgreSQL**。

### 11. assisted listing schema validator —— 自我掩蓋的缺陷

```
tests/contract/test_assisted_listing_intake_schema.py:29
    VALIDATOR_SQL = REPO_ROOT / "scripts" / "validate_assisted_listing_intake_schema.sql"
    → 該路徑無檔案

實際位置：delivery_toolchain/governance/validate_assisted_listing_intake_schema.sql
    commit 549ce261「refactor: isolate standalone delivery tools」搬移了檔案，常數沒改。
    同檔 line 322 的註解已經寫成新路徑，程式碼沒有。
```

那支 SQL 檢查每張帶 tenant 的表是否有 FORCE RLS 與 fail-closed tenant policy、每個 tenant-scoped FK 是否有對應的複合外鍵。

**它從該次重構以來從未真正執行過**，而它沒被發現的原因，正是它在上面那 89 條裡——會告訴你它壞了的東西，就是壞掉的那個東西。

### 12. `assisted-intake-design-validation.yml` —— path filter 沒蓋到它要驗的東西

這個 workflow 做對了一件事：它有一個明確的步驟叫 **「Verify cross-contract gate fails closed」**。

但它是 path-filtered 的，而觸發清單長這樣：

```yaml
paths:
  - 'docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_*'
  - 'docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_*'
  - 'docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_*'
  - 'docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_*'
  - 'docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_*'
  - 'delivery_toolchain/governance/validate_assisted_listing_intake_*'
  - 'delivery_toolchain/openapi/build_validate_assisted_listing_intake.py'
  - '.github/workflows/assisted-intake-design-validation.yml'
```

**沒有 `infra/db/migrations/assisted_listing_intake/`。** 也就是改動實際的 DDL——包括 `004_tenant_rls_lineage.sql`，那支專門處理租戶 RLS 與血緣的 migration——**不會觸發設計驗證**。

閘被證明會 fail closed，然後不會被它存在的理由所觸發。這是成因四的第三種形狀：不是 marker 排除、不是路徑斷掉，是 **path filter 沒蓋到目標**。

### 13. tooling scope 跳過整個 product job

```yaml
product:
  needs: change-scope
  if: ${{ needs.change-scope.outputs.scope != 'development_tooling' }}
```

`make security`（含上述六條供應鏈負向證明）跑在 product job 裡。因此一個 tooling-scoped PR 會連同那六條證明一起跳過。

這是已知的取捨，列在這裡是為了完整——`orchestrator` job 有補跑 `tests/tooling`，註解也說明了理由。

---

## 三種失效形狀

清查前我以為成因四只有一種形狀。實際上有三種，而且第三種最難察覺：

| 形狀 | 實例 | 為什麼難察覺 |
|---|---|---|
| **不在執行路徑上** | 89 條 marker 排除、path filter 沒蓋到 DDL | 沒有紅燈，也沒有綠燈——什麼都沒有 |
| **沒有失敗證明** | `check_orchestrator_config`、`scope.brand`/`scope.module` | 綠燈只證明「沒違規」，不證明「會擋」 |
| **證明本身無效** | `tenant_isolation` | **綠燈，而且是負向測試的綠燈**——看起來已經證明過了 |

第三種是唯一一種會主動誤導人的：一份寫著 `test_tenant_isolation_blocks_other_tenant` 且通過的測試，讓任何人都不會再去問這道閘在生產路徑上會不會觸發。

---

## 建議（依成本排序）

### 已做（分支 `task/ODP-GATE-EXECUTION-PATH-001`）

1. **修 `VALIDATOR_SQL` 路徑常數。** 那支租戶隔離驗證器第一次真的執行，**通過**。
2. **補三條負向案例**，證明它會失敗而且是為了該有的理由：`NO FORCE ROW LEVEL SECURITY`
   → `RLS_POLICY_INCOMPLETE`、`DROP POLICY tenant_isolation` → `RLS_POLICY_INCOMPLETE`、
   `DROP CONSTRAINT fk_intake_resolved_listing_tenant` → 錯誤訊息必須指名該約束。
   一道剛修好的閘若只看到綠燈就收工，就是在成因四的修正裡重犯成因四。
3. **`conftest.py` 補 `uuid-ossp` stub**，只別名 `uuid_generate_v4()`（與核心 `gen_random_uuid()`
   語意完全相同），**刻意不定義 v1／v3／v5**——隨機產生器代替不了時間或名稱衍生的 UUID，
   缺函式的失敗比錯值的成功大聲。
4. **加 CI 步驟** `Test database contracts, migrations and schema gates`，
   跑 `-m "requires_live_env and not requires_postgis"`。本機模擬：**85 條、全綠**。
5. **`requires_postgis` marker**：把殘餘的排除從「隱性 89 條」縮成「顯性 1 條具名測試」。

### 待做

6. **`infra/db/migrations/assisted_listing_intake/` 加入 design-validation 的 paths。**
7. **`check_orchestrator_config` 補負向測試**，照 `test_supply_chain_security_gate.py` 的形狀。
8. **`scope.brand` / `scope.module` 補 deny 測試。**
9. **`tenant_isolation`：** **removed-by-decision（2026-09-02）。**
   這道結構上不可能觸發的 ABAC 子句、default chain entry 與誤導性的
   手工 deny test 已移除；資料層 `_require_tenant_scope` 與 assisted-listing
   RLS 維持不變。

第 9 項已完成裁定與移除，其餘待做項目不受此決定影響。
