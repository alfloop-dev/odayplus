# ODP-DEV-CANDIDATE-CODE-RECEIPT-001 — Candidate C Code Gate (Gate 0) 審查收據與缺口清單

- Task ID: `ODP-DEV-CANDIDATE-CODE-RECEIPT-001`
- Owner: `Antigravity`
- Reviewer: `Codex2`
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
1. **不變更 Release 准入決定**：交付本證據包不代表 Code Gate 通過、Human/Ops GO 簽署、法務批准或系統可執行部署。
2. **隔離 Scratch 執行**：所有產品碼檢查均在 HEAD 精確等於 candidate C 的獨立乾淨 scratch 目錄執行，嚴禁把 task 交付分支的 HEAD 誤當作 candidate C。
3. **重用既有真實收據**：既有已驗證的 build handoff、image digest 簽章與 6 份 raw artifacts（run `34179207603` 與 `34179791241`）予以引用與比對，不重 build image 或重新發布容器。
4. **有限範圍修改**：只建立並修改本任務 owned 目錄 `docs/evidence/runtime/ODP-DEV-CANDIDATE-CODE-RECEIPT-001/`，不修改 registry、manifest、validator、workflow、lockfile 或產品程式碼。
5. **誠實記錄失敗與缺口**：離線驗證若失敗（如 `make boundary-check` 退出代碼 1），誠實記錄原始 exit code 與錯誤輸出，不偽造 pass。

---

## 2. 交付產物索引 (Deliverables Index)

本證據目錄包含下列機器可讀與說明檔案：

| 檔案名稱 | 格式 | 說明 |
|---|---|---|
| [`README.md`](file:///tmp/pantheon-worker-worktrees/pantheon/odp-dev-candidate-code-receipt-001/docs/evidence/runtime/ODP-DEV-CANDIDATE-CODE-RECEIPT-001/README.md) | Markdown | 本說明與審查總覽文件，索引所有證據產物與驗證收據。 |
| [`review-receipt.json`](file:///tmp/pantheon-worker-worktrees/pantheon/odp-dev-candidate-code-receipt-001/docs/evidence/runtime/ODP-DEV-CANDIDATE-CODE-RECEIPT-001/review-receipt.json) | JSON | 機器可讀之 Code Gate 執行收據總表，綁定各指令的 argv、cwd、exit_code、UTC 與輸出摘要。 |
| [`criteria-evidence-matrix.json`](file:///tmp/pantheon-worker-worktrees/pantheon/odp-dev-candidate-code-receipt-001/docs/evidence/runtime/ODP-DEV-CANDIDATE-CODE-RECEIPT-001/criteria-evidence-matrix.json) | JSON | Gate 0 五項 criteria 的逐項驗證狀態、證據參照、已驗證項目與缺口責任分派矩陣。 |
| [`source-index.json`](file:///tmp/pantheon-worker-worktrees/pantheon/odp-dev-candidate-code-receipt-001/docs/evidence/runtime/ODP-DEV-CANDIDATE-CODE-RECEIPT-001/source-index.json) | JSON | 來源文件、GitHub Actions runs、4 個 component images 與 6 份 raw artifacts 的 hash 索引。 |

---

## 3. Code Gate (Gate 0) 五項標準評估摘要

依據 `RELEASE_GATE_REGISTRY.json`，Gate 0 包含五項必要檢查（`required_checks`）：

### 3.1 Criterion 1: Lint and format checks pass on the release candidate commit
- **執行命令與結果**：
  - `make lint` (`uv run ruff check ...`)：**EXIT=0 (PASSED)**，Python 靜態 lint 全數通過。
  - `make boundary-check` (`uv run python delivery_toolchain/governance/check_code_boundaries.py`)：**EXIT=1 (FAILED)**。
    - 錯誤輸出：`Code boundary checks failed: - boundary inventory is stale: run delivery_toolchain/governance/check_code_boundaries.py --write-inventory`。
  - 前端 Lint (`npm run lint --workspace=@oday-plus/web`)：**EXIT=0 (PASSED)**，`✔ No ESLint warnings or errors`。
- **結論**：**BLOCKED**（因 `check_code_boundaries.py` 在 candidate C 上 inventory 過期失敗）。

### 3.2 Criterion 2: Static/type checks pass where configured
- **執行命令與結果**：
  - Web TypeScript 檢查 (`npm run typecheck --workspace=@oday-plus/web`)：**EXIT=0 (PASSED)**，`tsc --noEmit` 0 errors。
  - 分數預設值檢查 (`check_measurement_defaults.py`)：**EXIT=0 (PASSED)**，19 處 score defaults 均符合豁免規範。
  - 需求成員檢查 (`check_requirement_members.py`)：**EXIT=0 (PASSED)**，9 個集合需求、47 成員全部解析。
  - 受治理詞彙檢查 (`generate_vocabularies.py --check`)：**EXIT=0 (PASSED)**，3 個詞彙無未核准分叉。
  - 設定與接線檢查 (`check_orchestrator_config.py` & `check_config_wiring.py`)：**EXIT=0 (PASSED)**，190 個 keys 均有生產程式碼讀取。
- **結論**：**PASSED**。

### 3.3 Criterion 3: Unit tests pass for changed backend and domain logic
- **執行命令與結果**：
  - 針對 candidate C 相對於 review base `04e1572f` 實際修改之後端/領域邏輯執行聚焦測試：
    - `tests/models/test_heatzone_merge_split.py`、`test_native_drift.py`、`test_evidently_monitor_baseline.py`、`tests/tooling/*`、`tests/release/*`、`scripts/test_ai_status.py`：**EXIT=0 (PASSED)**（791 passed, 17 warnings, 101 subtests passed in 331.87s）。
    - `tests/unit/persistence/test_sql_decision_policy_repository.py`、`tests/ops/test_heatzone_composition_migration.py`、`scripts/orchestrator/test_archive_history_recovery.py`：**EXIT=0 (PASSED)**（104 passed, 26 subtests passed in 9.89s）。
    - `tests/integration/test_heatzone_composition_api.py`：**EXIT=0 (PASSED)**（41 passed, 22 warnings in 222.30s）。
  - 合計執行通過 **936 項測試 + 127 項 subtests**。
- **未涵蓋/缺口**：
  - GitHub Actions CI 在 C 上的 `product`、`product-e2e` 與 `performance` jobs 均為 skipped；不能以其他 commit 之綠色 CI 代替 C 的完整 CI 收據。
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
  - Run `34179791241`（Artifact Publisher）：重用該 4 個 image digests，執行 `cosign verify` 全數通過，step 20 以 `INITIAL_RELEASE_RECOVERY: true` 成功產出並發布 6 份 raw artifacts。
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

## 4. 執行收據總表 (Execution Receipts Table)

以下為在隔離 candidate C scratch 環境（`/tmp/.../c-scratch`）實際執行的收據彙整：

| Receipt ID | 檢查分類 | 執行指令 (argv) | Exit Code | 執行時間 (UTC) | 耗時 | 結果與摘要 |
|---|---|---|---|---|---|---|
| `RCPT-CODE-001` | Python Lint | `uv run ruff check ...` | **0** | 2026-09-10T23:38:57Z | 1.2s | **PASSED**：全 Python 目錄 ruff 檢查通過。 |
| `RCPT-CODE-002` | Boundary Check | `uv run python check_code_boundaries.py` | **1** | 2026-09-10T23:38:55Z | 0.5s | **FAILED**：邊界清冊過期（stale boundary inventory）。 |
| `RCPT-CODE-003` | Frontend Lint | `npm run lint --workspace=@oday-plus/web` | **0** | 2026-09-10T23:40:51Z | 7.8s | **PASSED**：ESLint 0 錯誤 0 警告。 |
| `RCPT-CODE-004` | TypeScript | `npm run typecheck --workspace=@oday-plus/web` | **0** | 2026-09-10T23:40:06Z | 43.0s | **PASSED**：`tsc --noEmit` 0 錯誤。 |
| `RCPT-CODE-005` | Governance Static | 5 組治理靜態檢查腳本包 | **0** | 2026-09-10T23:39:00Z | 15.0s | **PASSED**：measurement defaults, members, vocabularies, config 均通過。 |
| `RCPT-CODE-006` | Frontend Component | `npm run test --workspace=@oday-plus/web` | **0** | 2026-09-10T23:41:01Z | 67.6s | **PASSED**：Vitest 57 檔案 525 測試通過。 |
| `RCPT-CODE-007` | Frontend Build | `npm run build && bundle:budget` | **0** | 2026-09-10T23:42:11Z | 170.0s | **PASSED**：Next.js 生產建置與 13 路由預算均符合。 |
| `RCPT-CODE-008` | Backend Models/Tooling | `uv run pytest tests/models/... tests/tooling/...` | **0** | 2026-09-10T23:45:07Z | 331.9s | **PASSED**：791 通過、101 subtests 通過。 |
| `RCPT-CODE-009` | Backend Persistence | `uv run pytest tests/unit/persistence/...` | **0** | 2026-09-10T23:51:23Z | 9.9s | **PASSED**：104 通過、26 subtests 通過。 |
| `RCPT-CODE-010` | HeatZone API | `uv run pytest tests/integration/test_heatzone_composition_api.py` | **0** | 2026-09-10T23:51:45Z | 222.3s | **PASSED**：41 通過。 |
| `RCPT-CODE-011` | Build Traceability | 引用 run 34179791241 與 34179207603 | **0** | 2026-09-08T02:26:33Z | 284.0s | **PASSED**：4 映像檔簽章驗證通過，6 份 artifact raw bytes 吻合。 |

---

## 5. 追查 ODP-PLAN-ENGINEERING-HARDENING-001 結果

在 `RELEASE_GATE_REGISTRY.json` 中，`ODP-PLAN-ENGINEERING-HARDENING-001` 被記錄為 Gate 0 與 Gate 1 的 blocker。經本任務追查：
1. **歷史來源**：該任務原為工程品質硬化計畫（包含 OpenAPI response typing、13 項 dev-tool 漏洞之 Human/Ops risk decision 綁定、前端 bundle/CSS 整理等）。存在歷史 sidecar 記錄 [`support/sidecars/ODP-PLAN-ENGINEERING-HARDENING-001/ODP-PLAN-ENGINEERING-HARDENING-001-SIDECAR-ACCEPTANCE.md`](file:///tmp/pantheon-worker-worktrees/pantheon/odp-dev-candidate-code-receipt-001/support/sidecars/ODP-PLAN-ENGINEERING-HARDENING-001/ODP-PLAN-ENGINEERING-HARDENING-001-SIDECAR-ACCEPTANCE.md)，其父分支為 `task/ODP-PLAN-ENGINEERING-HARDENING-001`（HEAD `d24fd0c4`），狀態為 `blocked`（等待 Human/Ops 針對 13 項 dev-tool 高風險漏洞做正式裁決）。
2. **現行 Supervisor 狀態**：在當前 canonical state (`ai-status.json`) 中，**並無此任務的 active canonical task**。
3. **判定與處置**：依驗收規範標記為 **`歷史待核對`（historical pending reconciliation）**；本任務不得重建同名任務、不得宣稱其已完成，亦不自行執行整套 hardening。

---

## 6. 未做事項與禁止行為確認 (Prohibitions & Limits)

本任務恪守以下約束：
- **未清除任何 Gate**：Gate 0 乃至 Gate 0–6 全數維持 `status: "blocked"`。
- **未改動 Release 決策**：`release.decision` 維持 `no-go`。
- **未簽發 Lease、未執行 Deploy、未啟用第三方資料來源**。
- **未修改 candidate C 或 candidate manifest/registry**。
- **未讀取 Secret 亦未偽造 Human/Ops 具名核准**。
