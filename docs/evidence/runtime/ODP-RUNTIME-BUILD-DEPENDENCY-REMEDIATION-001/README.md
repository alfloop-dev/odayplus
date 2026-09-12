---
task: ODP-RUNTIME-BUILD-DEPENDENCY-REMEDIATION-001
title: Runtime Release Production Dependency Security Remediation Evidence
owner: Antigravity6
reviewer: Codex2
status: completed
recorded_at: 2026-09-12T15:11:24Z
---

# Runtime Release Production Dependency Security Remediation Evidence

## 1. 任務背景與目標

在 2026-09-12 執行的 Runtime Release 構建中，生產環境 npm audit 檢測出 1 high 與 2 critical 安全漏洞（包含 MapLibre DOM sanitize XSS、Next.js RCE、sharp libheif 漏洞，以及連帶之 PostCSS 依賴漏洞）。
本任務（`ODP-RUNTIME-BUILD-DEPENDENCY-REMEDIATION-001`）針對上述 production 漏洞進行獨立修復，確保 manifest 與 lockfile 一致、型別與 API 相容、通過各項測試與構建，並保留真實 audit 與驗證收據。

## 2. 漏洞與修復版本對照表

| 套件 (Package) | 漏洞編號 (Advisory) | 嚴重程度 | 原版本 | 修復後版本 | 處置方式與說明 |
|---|---|---|---|---|---|
| `maplibre-gl` | [GHSA-jrc7-96c5-q579](https://github.com/advisories/GHSA-jrc7-96c5-q579) | Critical (CVSS 10) | `^5.24.0` | `^6.4.1` (6.9.0 resolved) | 升級至 MapLibre v6 修補版本；更新 `HeatZoneMap.tsx` 模組 import（命名空間 `import * as maplibregl`）及錯誤事件型別相容性。 |
| `next` | [GHSA-p293-qw3h-jr36](https://github.com/advisories/GHSA-p293-qw3h-jr36)<br>[GHSA-2xp9-vwfh-vxw4](https://github.com/advisories/GHSA-2xp9-vwfh-vxw4) | Critical (CVSS 9.0) | `15.5.21` | `15.5.25` | 升級至 patched 15.5.x (`15.5.25`)，並同步升級 `eslint-config-next` 至 `15.5.25`。 |
| `sharp` | [GHSA-rgj7-g3m4-5g8c](https://github.com/advisories/GHSA-rgj7-g3m4-5g8c) | High | `0.35.3` | `0.35.4` | 更新根目錄 `package.json` overrides 中的 `sharp` 鎖定為 `0.35.4`。 |
| `postcss` | [GHSA-6g55-p6wh-862q](https://github.com/advisories/GHSA-6g55-p6wh-862q)<br>[GHSA-r28c-9q8g-f849](https://github.com/advisories/GHSA-r28c-9q8g-f849)<br>[GHSA-fxqj-rqcc-2cmp](https://github.com/advisories/GHSA-fxqj-rqcc-2cmp) | High | `8.4.31` / `8.5.10` | `8.5.28` | 更新根目錄 `package.json` overrides 與 `package-lock.json` 中的 `postcss` 鎖定至修補版本 `8.5.28`。 |

## 3. 驗收標準（Acceptance Criteria）達成說明

- **A1 鎖定 npm install 及 production audit high/critical=0 並保留真實收據**:
  - 執行 `python3 delivery_toolchain/security/npm_audit_gate.py` 結果：`PASS: no production vulnerabilities at or above 'high' (0 finding(s) below the threshold).` (Exit code: 0)。
  - `npm audit --omit=dev --json` 結果：`total: 0`, `critical: 0`, `high: 0`, `moderate: 0`, `low: 0`, `info: 0`。
  - 收據已保存於 `docs/evidence/runtime/ODP-RUNTIME-BUILD-DEPENDENCY-REMEDIATION-001/receipts/`。
- **A2 Next 與 sharp 升至修補版本且 manifest 和 lockfile 一致**:
  - `apps/web/package.json`：`next: 15.5.25`, `eslint-config-next: 15.5.25`。
  - `package.json`：`overrides.sharp: 0.35.4`, `overrides.postcss: 8.5.28`。
  - `package-lock.json` 已依 npm resolver 與 overrides 完整同步鎖定。
- **A3 MapLibre 升至 6.4.1 或較新修補版本且 web typecheck 及 map/unit tests 成功**:
  - `apps/web/package.json` 升級為 `maplibre-gl: ^6.4.1`。
  - `apps/web/features/operator/network/HeatZoneMap.tsx` 已完成 v6 模組導入與事件型別相容性調整。
  - `npm run typecheck` 通過 (Exit code: 0)。
  - `npm run test` (Vitest 58 test files, 528 tests) 全數通過 (Exit code: 0)。
- **A4 production web build 與必要 CI 成功且獨立審查**:
  - `npm run lint` 通過 (Exit code: 0)。
  - `npm run build` (Next.js production build) 成功編譯且靜態/動態頁面優化完成 (Exit code: 0)。
  - `npm run bundle:budget` 所有路由均在 budget 上限內 (Exit code: 0)。
- **A5 不更動 audit 門檻或 waiver 及 Runtime Release workflow 或 GCP 及 canonical manifest**:
  - 未修改 `delivery_toolchain/security/npm_audit_gate.py` 判定邏輯或門檻。
  - 未添加任何 waiver 或 ignore 機制。
  - 未修改 Release workflow、GCP 設定或 canonical manifest。

## 4. 驗證指令與執行收據

1. **Audit Security Gate**:
   ```bash
   python3 delivery_toolchain/security/npm_audit_gate.py --receipt docs/evidence/runtime/ODP-RUNTIME-BUILD-DEPENDENCY-REMEDIATION-001/receipts/remediation-npm-audit-receipt.json
   # Output: PASS: no production vulnerabilities at or above 'high' (0 finding(s) below the threshold).
   # Exit code: 0
   ```

2. **TypeScript Typecheck**:
   ```bash
   npm run typecheck
   # Output: tsc --noEmit across all workspaces: 0 errors
   # Exit code: 0
   ```

3. **Workspace Unit / Integration Tests**:
   ```bash
   npm run test
   # Output: 58 test files passed (58), 528 tests passed (528)
   # Exit code: 0
   ```

4. **Linting**:
   ```bash
   npm run lint
   # Output: No ESLint warnings or errors
   # Exit code: 0
   ```

5. **Production Build & Bundle Budget**:
   ```bash
   npm run build
   # Output: Compiled successfully
   npm run bundle:budget
   # Output: All routes ok
   # Exit code: 0
   ```

6. **SBOM & OSS Notice Consistency and Security Test Suite**:
   ```bash
   uv run python delivery_toolchain/security/generate_sbom.py --check
   # Output: SBOM at docs/evidence/sbom.json is valid and up to date.
   # Exit code: 0

   uv run python delivery_toolchain/security/generate_oss_notice.py --check
   # Output: NOTICE-THIRD-PARTY.md matches the installed dependency trees.
   # Exit code: 0

   uv run pytest tests/security
   # Output: 353 passed, 5 warnings in 505.02s
   # Exit code: 0
   ```
