# ODP-DEV-CANDIDATE-CODE-RECEIPT-001 — Candidate C Code Gate (Gate 0) 審查收據與缺口清單

- Task ID: `ODP-DEV-CANDIDATE-CODE-RECEIPT-001`
- Owner: `Antigravity2`
- Reviewer: `Codex`
- Candidate SHA (C): `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`
- Baseline Evidence SHA (E): `d084f51d4009b7b435416c8b83410a8b4fb4a267`
- Manifest Digest: `sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c`
- Target Gate: `gate-0` (Code Gate)
- Gate 判定結論: **維持 `blocked`**
- Release 准入決定: **維持 `no-go`**

---

## 1. 任務目標與邊界說明

本任務旨在為固定 release candidate C（`596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`）整理並執行 **Gate 0（Code Gate）** 的獨立審查用證據包。

### 嚴格遵循之操作規範：
1. **不變更 Release 准入決定**：交付本證據包不代表 Code Gate 通過、Human/Ops GO 簽署、法務批准或系統可執行部署；Gate 0 與 Release 准入維持 fail-closed `blocked` / `no-go`。
2. **隔離與精確來源綁定**：所有歷史收據均綁定 exact candidate C (`596b9c9a`) 或經等價比對之不可變來源；不重跑整套大型 CI suite 製造虛假歷史收據。
3. **重用既有真實收據**：既有已驗證的 build handoff、image digest 簽章與 6 份 raw artifacts（run `34179207603` 與 `34179791241`）予以引用與比對，不重 build image 或重新發布容器。
4. **有限範圍修改**：只建立並修改本任務 owned 目錄 `docs/evidence/runtime/ODP-DEV-CANDIDATE-CODE-RECEIPT-001/`，不修改 registry、manifest、validator、workflow、lockfile 或產品程式碼。
5. **誠實記錄失敗與缺口**：缺少之執行證據（如 format check、packages workspaces typecheck、CI product job skip 等）如實標記為 `unknown` 或 `gap`，並精確分派後續責任。

---

## 2. 交付產物索引 (Deliverables Index)

本證據目錄包含下列機器可讀與說明檔案（均使用相對路徑索引）：

| 檔案名稱 | 格式 | 說明 |
|---|---|---|
| [`README.md`](./README.md) | Markdown | 本說明與審查總覽文件，索引所有證據產物與驗證收據。 |
| [`review-receipt.json`](./review-receipt.json) | JSON | 機器可讀之 Code Gate 執行收據總表，綁定各指令的 argv、binding、exit_code、UTC 與輸出摘要。 |
| [`criteria-evidence-matrix.json`](./criteria-evidence-matrix.json) | JSON | Gate 0 五項 criteria 的逐項驗證狀態、證據參照、已驗證項目與缺口責任分派矩陣。 |
| [`source-index.json`](./source-index.json) | JSON | 來源文件、GitHub Actions runs、4 個 component images 與 6 份 raw artifacts 的 hash 索引。 |

---

## 3. Code Gate (Gate 0) 五項標準評估摘要

依據 `RELEASE_GATE_REGISTRY.json`，Gate 0 包含五項必要檢查（`required_checks`）：

### 3.1 Criterion 1: Lint and format checks pass on the release candidate commit
- **執行命令與結果**：
  - `make lint` (`uv run ruff check ...`)：**EXIT=0 (PASSED)**，Python 靜態 lint 全數通過。
  - CI Code Boundary Check (Run `34138969380` Job `101796385286`)：**PASSED**，log 明確記錄 `Code boundary checks passed for 1137 files`。
  - 離線 Scratch Boundary 觀察：回傳 exit 1（stale inventory）；此差異保留並標記為環境與工具輸入待核對（unknown pending reconciliation），不武斷改動 C 邊界清冊。
  - 前端 Lint (`npm run lint --workspace=@oday-plus/web`)：**EXIT=0 (PASSED)**，`✔ No ESLint warnings or errors`。
  - 程式碼排版（Format）：無專屬 format check 收據（`ruff check` 與 `next lint` 不涵蓋排版驗證），記錄為缺口。
- **結論**：**PARTIAL_BLOCKED**（邊界核對差異與排版收據缺口）。

### 3.2 Criterion 2: Static/type checks pass where configured
- **執行命令與結果**：
  - Web TypeScript 檢查 (`npm run typecheck --workspace=@oday-plus/web`)：**EXIT=0 (PASSED)**，`tsc --noEmit` 0 errors。
  - 5 組治理靜態檢查腳本（分項記錄）：
    - `check_measurement_defaults.py`：**EXIT=0 (PASSED)**，19 處 score defaults 均符合豁免規範。
    - `check_requirement_members.py`：**EXIT=0 (PASSED)**，9 個集合需求、47 成員全部解析。
    - `generate_vocabularies.py --check`：**EXIT=0 (PASSED)**，3 個詞彙無未核准分叉。
    - `check_orchestrator_config.py`：**EXIT=0 (PASSED)**，設定檔驗證通過。
    - `check_config_wiring.py`：**EXIT=0 (PASSED)**，190 個 keys 均有生產程式碼讀取。
  - 套件型別檢查（Packages Typecheck）：`Makefile:90-97` 定義 `--workspaces`，`packages/design-tokens`、`domain-types`、`openapi-client`、`ui-domain` 與 `ui` 均有 typecheck 設定；web `tsc` 不等價於 packages（`ui-domain` 啟用 `noUncheckedIndexedAccess` 且含測試）。Packages typecheck 缺少歷史收據，誠實記錄為缺口。
- **結論**：**PARTIAL**。

### 3.3 Criterion 3: Unit tests pass for changed backend and domain logic
- **執行命令與結果**：
  - 針對 candidate C 相對於 review base `04e1572f` 實際修改之後端/領域邏輯執行聚焦測試：
    - `tests/models/test_heatzone_merge_split.py`、`test_native_drift.py`、`test_evidently_monitor_baseline.py`、`tests/tooling/*`、`tests/release/*`、`scripts/test_ai_status.py`：**EXIT=0 (PASSED)**（791 passed, 17 warnings, 101 subtests passed in 331.87s）。
    - `tests/unit/persistence/test_sql_decision_policy_repository.py`、`tests/ops/test_heatzone_composition_migration.py`、`scripts/orchestrator/test_archive_history_recovery.py`：**EXIT=0 (PASSED)**（104 passed, 26 subtests passed in 9.89s）。
    - `tests/integration/test_heatzone_composition_api.py`：**EXIT=0 (PASSED)**（41 passed, 22 warnings in 222.30s）。
  - 合計執行通過 **936 項測試 + 127 項 subtests**。
- **未涵蓋/缺口**：
  - GitHub Actions CI 在 C 上的 `product`、`product-e2e-gate` 與 `performance-gate` jobs 均為 skipped（run `34138969380` 與 `34138605942`）；不能以其他 commit 之綠色 CI 代替 C 的完整 CI 收據。
  - Gate 1 Contract 比對（`tests/contract`）與 Gate 4 Security（`tests/security`、`pip-audit`）屬獨立 gate，不混入 Gate 0。
- **結論**：**PASSED_SCOPED**（領域單元測試通過，完整 CI 仍有缺口）。

### 3.4 Criterion 4: Component tests pass for changed frontend surfaces
- **執行命令與結果**：
  - 前端 Vitest 單元/元件測試 (`npm run test --workspace=@oday-plus/web`)：**EXIT=0 (PASSED)**（57 test files passed, 525 tests passed in 67.64s）。
  - 前端 Next.js 生產建置 (`npm run build --workspace=@oday-plus/web`)：**EXIT=0 (PASSED)**（編譯成功，4 static pages, 13 routes）。
  - Bundle 大小預算檢查 (`npm run bundle:budget --workspace=@oday-plus/web`)：**EXIT=0 (PASSED)**（所有 13 個路由均在預算內，如 `/operator` 299.8 kB <= 300.0 kB）。
- **結論**：**PASSED**。

### 3.5 Criterion 5: Build artifacts are immutable and traceable to the release candidate SHA
- **來源與 Run 職責區分**：
  - Run `34179207603`（Image Producer）：於 `2026-09-08T02:17:01Z–02:19:42Z` 建置、推播並以 Cosign 簽章 4 個 component images（Rekor log index: `api`=2754425715, `worker`=2754426118, `scheduler`=2754426482, `web`=2754427686）。因 step 20 `INITIAL_RELEASE_RECOVERY: false` 而結論為 `failure`。
  - Run `34179791241`（Artifact Publisher）：重用該 4 個 image digests，執行 `cosign verify` 全數通過，step 20 以 `INITIAL_RELEASE_RECOVERY: true` 成功產出並發布 6 份 raw artifacts（建立時間 `02:21:49Z`，完成時間 `02:26:33Z`）。引用自固定來源 `build-dispatch-evidence.md#L549-L905`。
  - 兩者的 workflow head SHA 均為 `8c570a56353abdcc8ba70fe0a3fdd9b963902391`（`dev` tip），由 step 3 強制 checkout `release_sha=596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`。
  - 4 個 component images：
    - `api`: `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-api@sha256:5e1a152e839cbfa7a2bf422b924928b56fad89f35e44243619a3f2fe98802cee`
    - `web`: `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-web@sha256:38c716462b569b7420fe95788a84f8a1b3778e2e7c938d535a1dc13288a78971`
    - `worker`: `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-worker@sha256:b2c0e4473ad529ad71f215b76fc125115d8ba0b4e845a87e532d10ebdb8ddba3`
    - `scheduler`: `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-scheduler@sha256:f3fd22c00478d730273494c23c512a87c807de645a8b759d0af4908d82cd4cc9`
    - `migration`: 共用 `worker` 映像檔。
  - `manifest_digest`: `sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c`（經 candidate C 之 `validate_manifest` 自我驗證通過）。
- **結論**：**PASSED**。

---

## 4. 執行與不可變收據總表 (Execution & Immutable Receipts Table)

| Receipt ID | 檢查分類 | 種類與來源 | 執行指令 / 參照 | Exit Code | 執行時間 (UTC) | 結果與摘要 |
|---|---|---|---|---|---|---|
| `RCPT-CODE-CI-001` | CI Boundary Check | 不可變 CI 收據 | `python check_code_boundaries.py` (Run 34138969380 Job 101796385286) | **0** | 2026-09-07T15:35:08Z | **PASSED**：CI 乾淨環境驗證 1137 個檔案全數通過。 |
| `RCPT-CODE-OBS-001` | Diagnostic Probe | 離線診斷觀察 | `uv run python check_code_boundaries.py` (scratch probe) | **1** | 2026-09-10T23:38:55Z | **UNKNOWN**：Scratch 回報 stale inventory，原因待環境核對。 |
| `RCPT-CODE-LINT-001` | Python Lint | 離線隔離執行 | `uv run ruff check ...` | **0** | 2026-09-10T23:38:57Z | **PASSED**：Python 目錄 ruff 檢查通過。 |
| `RCPT-CODE-FMT-001` | Code Format | 缺口清單 | `ruff format --check / prettier --check` | **null** | — | **UNKNOWN**：無歷史排版收據。 |
| `RCPT-CODE-LINT-002` | Frontend Lint | 離線隔離執行 | `npm run lint --workspace=@oday-plus/web` | **0** | 2026-09-10T23:40:51Z | **PASSED**：ESLint 0 錯誤 0 警告。 |
| `RCPT-CODE-TSC-001` | TypeScript Web | 離線隔離執行 | `npm run typecheck --workspace=@oday-plus/web` | **0** | 2026-09-10T23:40:06Z | **PASSED**：Web `tsc --noEmit` 0 錯誤。 |
| `RCPT-CODE-TSC-002` | TypeScript Packages | 缺口清單 | `npm run typecheck --workspaces` (packages/*) | **null** | — | **UNKNOWN**：套件 workspaces 缺少歷史收據。 |
| `RCPT-CODE-GOV-001` | Gov Measurement | 離線隔離執行 | `uv run python check_measurement_defaults.py` | **0** | 2026-09-10T23:39:00Z | **PASSED**：19 項 defaults 驗證通過。 |
| `RCPT-CODE-GOV-002` | Gov Requirements | 離線隔離執行 | `uv run python check_requirement_members.py` | **0** | 2026-09-10T23:39:03Z | **PASSED**：47 項 requirement members 驗證通過。 |
| `RCPT-CODE-GOV-003` | Gov Vocabularies | 離線隔離執行 | `uv run python generate_vocabularies.py --check` | **0** | 2026-09-10T23:39:05Z | **PASSED**：3 項 vocabularies 驗證通過。 |
| `RCPT-CODE-GOV-004` | Gov Orch Config | 離線隔離執行 | `uv run python check_orchestrator_config.py` | **0** | 2026-09-10T23:39:07Z | **PASSED**：Orchestrator 設定驗證通過。 |
| `RCPT-CODE-GOV-005` | Gov Wiring | 離線隔離執行 | `uv run python check_config_wiring.py` | **0** | 2026-09-10T23:39:10Z | **PASSED**：190 項 config keys 讀取接線驗證通過。 |
| `RCPT-CODE-CI-002` | CI Product Test | 不可變 CI 收據 | GitHub Actions CI `product` / `product-e2e` jobs | **null** | 2026-09-07T15:35:10Z | **SKIPPED**：CI product 測試在 candidate C 上被 skipped。 |
| `RCPT-CODE-UNIT-001` | Domain Unit Tests | 離線隔離執行 | `uv run pytest tests/models/... tests/tooling/...` | **0** | 2026-09-10T23:45:07Z | **PASSED**：791 通過、101 subtests 通過。 |
| `RCPT-CODE-UNIT-002` | Persistence Tests | 離線隔離執行 | `uv run pytest tests/unit/persistence/...` | **0** | 2026-09-10T23:51:23Z | **PASSED**：104 通過、26 subtests 通過。 |
| `RCPT-CODE-UNIT-003` | HeatZone API | 離線隔離執行 | `uv run pytest tests/integration/test_heatzone_composition_api.py` | **0** | 2026-09-10T23:51:45Z | **PASSED**：41 通過。 |
| `RCPT-CODE-COMP-001` | Component Tests | 離線隔離執行 | `npm run test --workspace=@oday-plus/web` | **0** | 2026-09-10T23:41:01Z | **PASSED**：Vitest 57 檔案 525 測試通過。 |
| `RCPT-CODE-BLD-001` | Frontend Build | 離線隔離執行 | `npm run build --workspace=@oday-plus/web` | **0** | 2026-09-10T23:42:11Z | **PASSED**：Next.js 生產編譯成功。 |
| `RCPT-CODE-BLD-002` | Bundle Budget | 離線隔離執行 | `npm run bundle:budget --workspace=@oday-plus/web` | **0** | 2026-09-10T23:44:16Z | **PASSED**：13 路由預算均符合。 |
| `RCPT-CODE-BLD-TRACE-001` | Image Producer | 不可變 CI 收據 | Run `34179207603` (4 images built & signed) | **null** | 2026-09-08T02:17:01Z | **PRODUCER_FAIL**：四映像檔簽章推播完成，step 20 失敗。 |
| `RCPT-CODE-BLD-TRACE-002` | Artifact Publisher | 重用不可變收據 | Run `34179791241` (6 artifacts published) | **null** | 2026-09-08T02:21:49Z | **PASSED**：6 份 artifact raw bytes 完全比對吻合。 |
| `RCPT-CODE-MANIFEST-001` | Manifest Verify | 離線隔離執行 | `python delivery_toolchain/release/validate_manifest.py` | **0** | 2026-09-10T23:52:00Z | **PASSED**：Release manifest 自我驗證通過。 |

---

## 5. 追查 ODP-PLAN-ENGINEERING-HARDENING-001 結果

在 `RELEASE_GATE_REGISTRY.json` 中，`ODP-PLAN-ENGINEERING-HARDENING-001` 被記錄為 Gate 0 與 Gate 1 的 blocker。經本任務追查：
1. **歷史來源**：該任務原為工程品質硬化計畫（包含 OpenAPI response typing、13 項 dev-tool 漏洞之 Human/Ops risk decision 綁定、前端 bundle/CSS 整理等）。存在歷史 sidecar 記錄 [`support/sidecars/ODP-PLAN-ENGINEERING-HARDENING-001/ODP-PLAN-ENGINEERING-HARDENING-001-SIDECAR-ACCEPTANCE.md`](support/sidecars/ODP-PLAN-ENGINEERING-HARDENING-001/ODP-PLAN-ENGINEERING-HARDENING-001-SIDECAR-ACCEPTANCE.md)，其父分支為 `task/ODP-PLAN-ENGINEERING-HARDENING-001`（HEAD `d24fd0c4`），狀態為 `blocked`（等待 Human/Ops 針對 13 項 dev-tool 高風險漏洞做正式裁決）。
2. **現行 Supervisor 狀態**：在當前 canonical state (`ai-status.json`) 中，**並無此任務的 active canonical task**（回傳 Unknown task）。
3. **判定與處置**：依驗收規範標記為 **`歷史待核對`（historical pending reconciliation）**；本任務不得重建同名任務、不得宣稱其已完成，亦不自行執行整套 hardening。

---

## 6. 未做事項與禁止行為確認 (Prohibitions & Limits)

本任務恪守以下約束：
- **未清除任何 Gate**：Gate 0 乃至 Gate 0–6 全數維持 `status: "blocked"`。
- **未改動 Release 決策**：`release.decision` 維持 `no-go`。
- **未簽發 Lease、未執行 Deploy、未啟用第三方資料來源**。
- **未修改 candidate C 或 candidate manifest/registry**。
- **未讀取 Secret 亦未偽造 Human/Ops 具名核准**。
