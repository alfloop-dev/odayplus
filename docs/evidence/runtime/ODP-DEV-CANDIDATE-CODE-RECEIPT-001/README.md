# ODP-DEV-CANDIDATE-CODE-RECEIPT-001 — Candidate C Code Gate (Gate 0) 審查收據與缺口清單

- Task ID: `ODP-DEV-CANDIDATE-CODE-RECEIPT-001`
- Owner: `Antigravity2`
- Reviewer: `Codex2`
- Candidate SHA (C): `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`
- Baseline Evidence SHA (E): `d084f51d4009b7b435416c8b83410a8b4fb4a267`
- Manifest Digest: `sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c`
- Target Gate: `gate-0` (Code Gate)
- Gate 判定結論: **維持 `blocked`**
- Release 准入決定: **維持 `no-go`**

---

## 1. 任務目標與邊界說明

本任務旨在為固定 release candidate C（`596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`）整理並記錄 **Gate 0（Code Gate）** 的獨立審查用證據包。

### 嚴格遵循之操作規範：
1. **不變更 Release 准入決定**：交付本證據包不代表 Code Gate 通過、Human/Ops GO 簽署、法務批准或系統可執行部署；Gate 0 與 Release 准入維持 fail-closed `blocked` / `no-go`。
2. **隔離與精確來源綁定**：所有歷史收據均綁定 exact candidate C (`596b9c9a`) 或經等價比對之不可變來源；不重跑整套大型 CI suite 製造虛假歷史收據。
3. **重用既有真實收據**：既有已驗證的 build handoff、image digest 簽章與 6 份 raw artifacts（run `34179207603` 與 `34179791241`）以及 candidate C 自帶之 manifest 驗證結果予以引用與比對，不重 build image 或重新發布容器。
4. **有限範圍修改**：只建立並修改本任務 owned 目錄 `docs/evidence/runtime/ODP-DEV-CANDIDATE-CODE-RECEIPT-001/`，不修改 registry、manifest、validator、workflow、lockfile 或產品程式碼。
5. **誠實記錄失敗與缺口**：缺少之執行證據（如 format check、packages workspaces typecheck、CI product job skip、離線未留存 terminal log 之觀察等）如實標記為 `unknown`、`unverified` 或 `gap`，並精確分派後續責任。

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
  - CI Code Boundary Check (Run `34138969380` Job `101796385286` Step 8)：**PASSED**，log 明確記錄 `Code boundary checks passed for 1137 files` (step window 15:35:03Z–15:35:08Z)。
  - CI Orchestrator Lint (Job `101796385286` Step 12)：**PASSED**，`uv run ruff check .orchestrator delivery_toolchain scripts` 驗證通過。
  - 離線 Scratch Boundary 觀察：回傳 exit 1（stale inventory）；此差異保留並標記為環境與工具輸入待核對（unknown pending reconciliation），不武斷改動 C 邊界清冊。
  - 全專案 Python Lint (`uv run ruff check ...`) 與前端 Lint (`npm run lint --workspace=@oday-plus/web`)：在 CI 中因 scope 分類為 tooling 而被 skipped；離線 scratch 曾有通過觀察，但因未留存不可變 terminal receipt/log，依規範保留為未驗證觀察（`unverified`）。
  - 程式碼排版（Format）：無專屬 format check 收據（`ruff check` 與 `next lint` 不涵蓋排版驗證），記錄為缺口（`unknown`）。
- **結論**：**PARTIAL_BLOCKED**（邊界核對差異、排版收據缺口、全專案 lint 缺少 CI 收據）。

### 3.2 Criterion 2: Static/type checks pass where configured
- **執行命令與結果**：
  - 5 組治理靜態檢查腳本（CI Job `101796385286` 執行通過）：
    - `check_measurement_defaults.py`：**PASSED**，19 處 score defaults 均符合豁免規範。
    - `check_requirement_members.py`：**PASSED**，9 個集合需求、47 成員全部解析。
    - `generate_vocabularies.py --check`：**PASSED**，3 個詞彙無未核准分叉。
    - `check_orchestrator_config.py` & `check_config_wiring.py`：**PASSED**，設定檔與 190 個 keys 讀取接線驗證通過。
  - Web TypeScript 檢查 (`npm run typecheck --workspace=@oday-plus/web`)：CI skipped；離線 scratch 觀察為通過但無不可變 terminal receipt，標記為 `unverified`。
  - 套件型別檢查（Packages Typecheck）：`Makefile:90-97` 定義 `--workspaces`，`packages/design-tokens`、`domain-types`、`openapi-client`、`ui-domain` 與 `ui` 均有 typecheck 設定；web `tsc` 不等價於 packages（`ui-domain` 啟用 `noUncheckedIndexedAccess` 且含測試）。Packages typecheck 缺少歷史收據，誠實記錄為缺口（`unknown`）。
- **結論**：**PARTIAL**。

### 3.3 Criterion 3: Unit tests pass for changed backend and domain logic
- **執行命令與結果**：
  - CI Orchestrator & Tooling Tests (Job `101796385286` Step 14)：**PASSED**，`uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling` 在 CI 乾淨環境中全數通過。
  - GitHub Actions CI 在 C 上的 `product`、`product-e2e-gate` 與 `performance-gate` jobs 均為 skipped（run `34138969380` 與 `34138605942`）；不能以其他 commit 之綠色 CI 代替 C 的完整 CI 收據。
  - 離線後端領域單元測試（`tests/models`、`tests/unit/persistence`、`tests/integration`）：雖有 936 項測試之離線觀察，但因未留存不可變 terminal receipt/log 與 isolate 證明，依規範不宣稱為 candidate C 之 verified pass，保留為 `unverified`。
  - Gate 1 Contract 比對（`tests/contract`）與 Gate 4 Security（`tests/security`、`pip-audit`）屬獨立 gate，不混入 Gate 0。
- **結論**：**BLOCKED**（完整產品 CI 單元測試在 candidate C 上 skipped，離線收據不足）。

### 3.4 Criterion 4: Component tests pass for changed frontend surfaces
- **執行命令與結果**：
  - 前端 Vitest 單元/元件測試 (`npm run test --workspace=@oday-plus/web`)、Next.js 生產建置與 bundle budget：在 candidate C 之 CI 中因 `product` job skipped 而未於 CI 執行；離線 scratch 觀察缺少不可變 terminal receipt/log，依規範保留為 `unverified`。
- **結論**：**UNVERIFIED / BLOCKED**。

### 3.5 Criterion 5: Build artifacts are immutable and traceable to the release candidate SHA
- **來源與 Run 職責區分**：
  - Run `34179207603`（Image Producer）：於 `2026-09-08T02:11:41Z–02:20:27Z` 執行建置，step 19（`02:15:38Z–02:20:20Z`）建置、推播並以 Cosign 簽章 4 個 component images，Rekor 簽章事件區間為 `02:17:01Z–02:19:42Z`（161s，Rekor log index: `api`=2754425715, `worker`=2754426118, `scheduler`=2754426482, `web`=2754427686）。因 step 20 `INITIAL_RELEASE_RECOVERY: false`（`started_at=completed_at=02:20:20Z`，build job window `02:12:00Z–02:20:25Z`）而結論為 `failure`。
  - Run `34179791241`（Artifact Publisher）：重用該 4 個 image digests，執行 `cosign verify` 全數通過，step 20 以 `INITIAL_RELEASE_RECOVERY: true` 成功產出並發布 6 份 raw artifacts（建立時間 `02:21:49Z`，完成時間 `02:26:33Z`）。引用自固定來源 `build-dispatch-evidence.md#L549-L905`。
  - 兩者的 workflow head SHA 均為 `8c570a56353abdcc8ba70fe0a3fdd9b963902391`（`dev` tip），由 step 3 強制 checkout `release_sha=596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`。
  - 4 個 component images：
    - `api`: `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-api@sha256:5e1a152e839cbfa7a2bf422b924928b56fad89f35e44243619a3f2fe98802cee`
    - `web`: `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-web@sha256:38c716462b569b7420fe95788a84f8a1b3778e2e7c938d535a1dc13288a78971`
    - `worker`: `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-worker@sha256:b2c0e4473ad529ad71f215b76fc125115d8ba0b4e845a87e532d10ebdb8ddba3`
    - `scheduler`: `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-scheduler@sha256:f3fd22c00478d730273494c23c512a87c807de645a8b759d0af4908d82cd4cc9`
    - `migration`: 共用 `worker` 映像檔。
  - `manifest_digest`: `sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c`（重用 build handoff §13.5 來源收據，經 candidate C 之 `release_manifest.validate_manifest` 自我驗證通過）。
- **結論**：**PASSED**。

---

## 4. 執行與不可變收據總表 (Execution & Immutable Receipts Table)

| Receipt ID | 檢查分類 | 種類與來源 | 執行指令 / 參照 | Exit Code | 執行時間 (UTC) | 結果與摘要 |
|---|---|---|---|---|---|---|
| `RCPT-CODE-CI-001` | CI Boundary Check | 不可變 CI 收據 | `uv run python check_code_boundaries.py` (Run 34138969380 Job 101796385286) | **null** (success) | 2026-09-07T15:35:08Z | **PASSED**：CI 乾淨環境驗證 1137 個檔案全數通過 (step duration 5s)。 |
| `RCPT-CODE-OBS-001` | Diagnostic Probe | 離線診斷觀察 | `uv run python check_code_boundaries.py` (scratch probe) | **null** | — | **DISCREPANCY_UNVERIFIED**：Scratch 回報 stale inventory，原因待環境核對。 |
| `RCPT-CODE-CI-GOV-001` | Gov Measurement | 不可變 CI 收據 | `uv run python check_measurement_defaults.py` (Job 101796385286 Step 9) | **null** (success) | 2026-09-07T15:35:10Z | **PASSED**：CI 執行通過，19 項 defaults 驗證。 |
| `RCPT-CODE-CI-GOV-002` | Gov Requirements | 不可變 CI 收據 | `uv run python check_requirement_members.py` (Job 101796385286 Step 10) | **null** (success) | 2026-09-07T15:35:11Z | **PASSED**：CI 執行通過，47 項 requirement members 驗證。 |
| `RCPT-CODE-CI-GOV-003` | Gov Vocabularies | 不可變 CI 收據 | `uv run python generate_vocabularies.py --check` (Job 101796385286 Step 11) | **null** (success) | 2026-09-07T15:35:12Z | **PASSED**：CI 執行通過，3 項 vocabularies 驗證。 |
| `RCPT-CODE-CI-GOV-004` | Gov Config & Wiring | 不可變 CI 收據 | `uv run python check_orchestrator_config.py & check_config_wiring.py` (Job 101796385286 Step 13) | **null** (success) | 2026-09-07T15:35:14Z | **PASSED**：CI 執行通過，設定檔與 190 項 wiring 驗證。 |
| `RCPT-CODE-CI-TOOL-001` | Tooling Tests | 不可變 CI 收據 | `uv run pytest -m "not requires_live_env" ... tests/tooling` (Job 101796385286 Step 14) | **null** (success) | 2026-09-07T15:38:10Z | **PASSED**：CI 執行通過，tooling 與 orchestrator 測試通過。 |
| `RCPT-CODE-LINT-001` | Python Lint (Full) | 離線未驗證觀察 | `uv run ruff check ...` | **null** | — | **UNVERIFIED**：全專案 ruff lint 於 CI 中未執行，離線 scratch 觀察未留存原始 terminal receipt。 |
| `RCPT-CODE-FMT-001` | Code Format | 缺口清單 | `ruff format --check / prettier --check` | **null** | — | **UNKNOWN**：無歷史排版收據。 |
| `RCPT-CODE-LINT-002` | Frontend Lint | 離線未驗證觀察 | `npm run lint --workspace=@oday-plus/web` | **null** | — | **UNVERIFIED**：CI skipped；離線 scratch 觀察未留存原始 terminal receipt。 |
| `RCPT-CODE-TSC-001` | TypeScript Web | 離線未驗證觀察 | `npm run typecheck --workspace=@oday-plus/web` | **null** | — | **UNVERIFIED**：CI skipped；離線 scratch 觀察未留存原始 terminal receipt。 |
| `RCPT-CODE-TSC-002` | TypeScript Packages | 缺口清單 | `npm run typecheck --workspaces` (packages/*) | **null** | — | **UNKNOWN**：套件 workspaces 缺少歷史收據。 |
| `RCPT-CODE-CI-002` | CI Product Test | 不可變 CI 收據 | GitHub Actions CI `product` / `product-e2e` jobs (skipped; gh api .../jobs/101796473509) | **null** | — | **SKIPPED**：CI product 測試在 candidate C 上被 skipped (job 101796473509, 101796473393, 101796473835; started_at=completed_at=2026-09-07T15:34:27Z, steps=[])。 |
| `RCPT-CODE-UNIT-001` | Domain Unit Tests | 離線未驗證觀察 | `uv run pytest tests/models/...` | **null** | — | **UNVERIFIED**：CI skipped；離線 scratch 觀察未留存原始 terminal receipt。 |
| `RCPT-CODE-UNIT-002` | Persistence Tests | 離線未驗證觀察 | `uv run pytest tests/unit/persistence/...` | **null** | — | **UNVERIFIED**：CI skipped；離線 scratch 觀察未留存原始 terminal receipt。 |
| `RCPT-CODE-UNIT-003` | HeatZone API | 離線未驗證觀察 | `uv run pytest tests/integration/test_heatzone_composition_api.py` | **null** | — | **UNVERIFIED**：CI skipped；離線 scratch 觀察未留存原始 terminal receipt。 |
| `RCPT-CODE-COMP-001` | Component Tests | 離線未驗證觀察 | `npm run test --workspace=@oday-plus/web` | **null** | — | **UNVERIFIED**：CI skipped；離線 scratch 觀察未留存原始 terminal receipt。 |
| `RCPT-CODE-BLD-001` | Frontend Build | 離線未驗證觀察 | `npm run build --workspace=@oday-plus/web` | **null** | — | **UNVERIFIED**：CI skipped；離線 scratch 觀察未留存原始 terminal receipt。 |
| `RCPT-CODE-BLD-002` | Bundle Budget | 離線未驗證觀察 | `npm run bundle:budget --workspace=@oday-plus/web` | **null** | — | **UNVERIFIED**：CI skipped；離線 scratch 觀察未留存原始 terminal receipt。 |
| `RCPT-CODE-BLD-TRACE-001` | Image Producer | 不可變 CI 收據 | Run `34179207603` (4 images built & signed) | **null** (failure) | 2026-09-08T02:11:41Z | **PRODUCER_FAIL**：四映像檔簽章推播完成 (Rekor span 02:17:01Z–02:19:42Z, 161s)，step 20 失敗 (02:20:20Z)。 |
| `RCPT-CODE-BLD-TRACE-002` | Artifact Publisher | 重用不可變收據 | Run `34179791241` (6 artifacts published) | **null** (success) | 2026-09-08T02:21:49Z | **PASSED**：6 份 artifact raw bytes 完全比對吻合。 |
| `RCPT-CODE-MANIFEST-001` | Manifest Validation | 重用不可變收據 | `release_manifest` validator (Handoff §13.5) | **null** (success) | — | **PASSED**：重用 build handoff 不可變收據，C 自帶驗證器核驗通過。 |

---

## 5. 追查 ODP-PLAN-ENGINEERING-HARDENING-001 結果

在 `RELEASE_GATE_REGISTRY.json` 中，`ODP-PLAN-ENGINEERING-HARDENING-001` 被記錄為 Gate 0 與 Gate 1 的 blocker。經本任務追查：
1. **歷史來源**：該任務原為工程品質硬化計畫（包含 OpenAPI response typing、13 項 dev-tool 漏洞之 Human/Ops risk decision 綁定、前端 bundle/CSS 整理等）。存在歷史 sidecar 記錄 [`support/sidecars/ODP-PLAN-ENGINEERING-HARDENING-001/ODP-PLAN-ENGINEERING-HARDENING-001-SIDECAR-ACCEPTANCE.md`](https://github.com/alfloop-dev/odayplus/blob/42e3b207cd125ef6a2fbc3f3aa092a88853587f2/support/sidecars/ODP-PLAN-ENGINEERING-HARDENING-001/ODP-PLAN-ENGINEERING-HARDENING-001-SIDECAR-ACCEPTANCE.md)，其記錄之父分支 HEAD 為 `d24fd0c4`，狀態為 `blocked`（等待 Human/Ops 針對 13 項 dev-tool 高風險漏洞做正式裁決）。
2. **現行 Supervisor 狀態**：在當前 canonical state (`ai-status.json`) 中，**並無此任務的 active canonical task**（回傳 Unknown task）。
3. **判定與處置**：依驗收規範標記為 **`歷史待核對`（historical pending reconciliation）**；本任務不得重建同名任務、不得宣稱其已完成，亦不自行執行整套 hardening。後續由 Release Gate Authority / Supervisor 於 release gate reconciliation 階段處理。

---

## 6. 未做事項與禁止行為確認 (Prohibitions & Limits)

本任務恪守以下約束：
- **未清除任何 Gate**：Gate 0 乃至 Gate 0–6 全數維持 `status: "blocked"`。
- **未改動 Release 決策**：`release.decision` 維持 `no-go`。
- **未簽發 Lease、未執行 Deploy、未啟用第三方資料來源**。
- **未修改 candidate C 或 candidate manifest/registry**。
- **未讀取 Secret 亦未偽造 Human/Ops 具名核准**。
