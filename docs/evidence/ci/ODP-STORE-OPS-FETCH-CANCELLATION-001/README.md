# Store Ops Fetch 取消請求 E2E Console Error 修補報告

- **Task ID**: `ODP-STORE-OPS-FETCH-CANCELLATION-001`
- **Owner**: `Antigravity`
- **Reviewer**: `Codex`
- **Phase**: `CI Gate Remediation`
- **狀態**: `Review Ready`

---

## 1. 問題背景與根因分析 (Root Cause Analysis)

在 Operator Console 的 Store Ops workspace (`DesignStoreOpsWorkspace`) 中，每當元件載入或篩選條件變更時，會透過 `useEffect` 發送 `/api/v1/operator/store-ops/issues` 的 HTTP 讀取請求。

### 根因：
1. **未傳遞 AbortSignal**：原先的 `fetch` 呼叫未綁定 `AbortController`，在頁面快速導向（例如由 `/operator` 切換至 `/operator?ws=govern`）或元件卸載（unmount）時，無法主動中斷進行中的網路請求。
2. **無條件記錄 console.error**：原程式碼在 `catch (error)` 區塊中無條件呼叫 `console.error("Error loading Store Ops issues:", error)`，未區分是否為元件已卸載、已被 AbortController 取消、或是瀏覽器導向所觸發的 `AbortError`。
3. **E2E 測試偶發失敗**：在 Playwright E2E 測試（例如 `ODP-OC-PREVIEW-001`）中，測試腳本會監聽 `page.on("console")`，任何 `error` 層級的記錄都會被收集至 `browserErrors` 中，最終導致 `expect(browserErrors).toEqual([])` 斷言偶發失敗。

---

## 2. 修正方案與變更範圍 (Remediation & Boundary)

### 2.1 核心修補 (`apps/web/features/operator/DesignAlignedWorkspaces.tsx`)
- 在 `DesignStoreOpsWorkspace` 的資料讀取 `useEffect` 內實例化 `AbortController`，並將 `controller.signal` 傳入 `fetch` 請求。
- 在 `useEffect` 的 cleanup 函式中同步執行 `cancelled = true` 與 `controller.abort()`。
- 在 `catch (error)` 區塊中增加取消判定：
  ```typescript
  const isAbort =
    cancelled ||
    controller.signal.aborted ||
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError");
  if (isAbort) {
    return;
  }
  ```
- **保留真實失敗可觀測性**：未被取消的真實網路失敗（如連線中斷）或 HTTP 錯誤（如 500 Internal Server Error），依然會完整執行 `console.error` 並將錯誤狀態反映在 `OperatorDataUnavailableGate` 與診斷訊息中。

### 2.2 守備邊界確認
- **未修改 API proxy deployment workflow**：未變動任何 API 或部署組態。
- **未另建非必要 retry 機制**：維持既有資料流架構。
- **變更範圍精確限於 owned paths**。

---

## 3. 本機 Focused 測試與 PR CI 驗證 (Verification)

依任務規則僅執行與 Store Ops 取消修復直接相關之 focused selection，全域回歸測試交由 PR exact-head CI 執行：

### 3.1 本機聚焦單元測試 (`apps/web/features/operator/__tests__/StoreOpsPackage10Parity.test.tsx`)
執行指令：
```bash
npm --workspace=@oday-plus/web test -- StoreOpsPackage10Parity
```
**執行結果**：
- `renders the full-store four-light summary and dense three-part workspace`：Passed
- `applies and clears Package 10 quick filters against the Issue queue`：Passed
- `filters lifecycle groups and exposes evidence detail tabs`：Passed
- `aborts in-flight fetch and suppresses console.error and state update on unmount`：Passed（驗證元件卸載時 AbortController 中斷請求且無 console.error）
- `logs console.error and exposes error gate on real API failure when mounted`：Passed（驗證未取消的真實網路異常仍有 console.error 與錯誤閘道）
- `handles HTTP 500 responses with error state and console.error when mounted`：Passed（驗證未取消的 HTTP 500 回應保留錯誤診斷）

總計：1 個測試檔案、6 個測試全部通過。

### 3.2 Playwright 測試整合與測試庫存維持
為維持 canonical Playwright inventory（嚴格要求 108 tests / 17 files），將精確的 Store Ops fetch 取消情境直接整合至既有測試案例 `tests/e2e/e2e-operator-console.spec.ts` 之 `ODP-OC-PREVIEW-001 design-preview-only smoke mounts iframe prototype and Store Ops dialog`：
- 在測試前段註冊延遲的 `/api/v1/operator/store-ops/issues*` 請求路由。
- 進入 Store Ops 後立即快速導向至 `/operator?ws=govern`。
- 驗證在 in-flight 請求被 unmount 中斷時，瀏覽器 console 仍維持無任何 `error` 記錄（`expect(browserErrors).toEqual([])`）。
- 測試總數嚴格維持 108 tests / 17 files，符合 canonical registry 要求。

### 3.3 PR CI 驗證綁定
全域驗證與 E2E 完整套件綁定至 PR 之 exact head commit，由 GitHub Actions CI 進行稽核與驗證。

---

## 4. 交付清單 (Artifacts Delivered)

- `apps/web/features/operator/DesignAlignedWorkspaces.tsx`：支援 `AbortController` 請求中斷與安全 catch 邏輯。
- `apps/web/features/operator/__tests__/StoreOpsPackage10Parity.test.tsx`：確定性單元回歸測試案例。
- `tests/e2e/e2e-operator-console.spec.ts`：Playwright 導向取消與 console error 驗證案例。
- `docs/evidence/ci/ODP-STORE-OPS-FETCH-CANCELLATION-001/README.md`：本中文證據報告。
