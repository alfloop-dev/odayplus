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

## 3. 測試與驗證證據 (Verification Evidence)

### 3.1 單元測試回歸 (`apps/web/features/operator/__tests__/StoreOpsPackage10Parity.test.tsx`)
新增了 3 項確定性單元測試：
1. `aborts in-flight fetch and suppresses console.error and state update on unmount`：確認元件卸載時主動 abort 請求，且不觸發 `console.error` 與 state 寫入。
2. `logs console.error and exposes error gate on real API failure when mounted`：確認在真實網路異常（非取消）時，正確輸出 `console.error` 並顯示錯誤頁面閘道。
3. `handles HTTP 500 responses with error state and console.error when mounted`：確認在 HTTP 500 錯誤時，正確記錄錯誤並顯示 API 狀態碼。

**執行結果**：
```bash
npm --workspace=@oday-plus/web test -- StoreOpsPackage10Parity
# Test Files  1 passed (1)
# Tests       6 passed (6)
# Duration    4.32s
```

全域 Web 測試套件：
```bash
npm --workspace=@oday-plus/web test
# Test Files  54 passed (54)
# Tests       487 passed (487)
```

### 3.2 靜態代碼與類型檢查
```bash
npm --workspace=@oday-plus/web run typecheck
# tsc --noEmit: Passed with 0 errors

npm --workspace=@oday-plus/web run lint
# next lint: Passed (No ESLint warnings or errors)
```

### 3.3 端到端 Playwright 測試情境 (`tests/e2e/e2e-operator-console.spec.ts`)
新增 `ODP-STORE-OPS-FETCH-CANCELLATION-001 unmounting or navigating away from Store Ops aborts in-flight issues fetch without console errors` 測試案例，透過設定延遲 route 模擬 Store Ops 請求進行中時快速導向 Govern 頁面，驗證瀏覽器主控台不會產生任何錯誤訊息。

---

## 4. 交付清單 (Artifacts Delivered)

- `apps/web/features/operator/DesignAlignedWorkspaces.tsx`：支援 `AbortController` 請求中斷與安全 catch 邏輯。
- `apps/web/features/operator/__tests__/StoreOpsPackage10Parity.test.tsx`：確定性單元回歸測試案例。
- `tests/e2e/e2e-operator-console.spec.ts`：Playwright 導向取消與 console error 驗證案例。
- `docs/evidence/ci/ODP-STORE-OPS-FETCH-CANCELLATION-001/README.md`：本中文證據報告。
