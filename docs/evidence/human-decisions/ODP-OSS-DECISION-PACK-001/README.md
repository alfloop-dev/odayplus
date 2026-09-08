# ODP-OSS-DECISION-PACK-001: OSS 與逐資料來源審查包暨未簽署 Receipt 準備

- **Task ID**: `ODP-OSS-DECISION-PACK-001`
- **Phase**: Stage A — Human Decision Engineering Preparation
- **Inspected Head**: `9048161e058becff5a53593a773d3c42238213fb`
- **Inspected Date**: `2026-09-08T15:47:00Z`
- **Base References**:
  - `ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md` (WP-10, §2.1 D01–D05, §2.2 D06–D14, §4.1)
  - `OSS_LEGAL_POLICY_HUMAN_HANDOFF_2026-07-31.md` (`ODP-PLAN-OSS-LEGAL-POLICY-001`)
  - `external-source-update-policy.md` (§4.1 16 source groups)
- **Status**: Stage A engineering preparation deliverable complete. Stage B authoritative signing and enforcement pending H01/H02 human inputs.

---

## 1. 產物清單與索引 (Artifacts Index)

本目錄包含 Stage A 所需之完整證據、比對矩陣、決策卡與收據模板：

| 產物檔案 | 格式 | 說明 |
|---|---|---|
| [`case-matrix.json`](case-matrix.json) | JSON | D01–D14 決策個案與現行 lockfile、真實版本、PURL、第一方 ownership 及當前未抑制 audit 之完整比對矩陣 |
| [`source-decision-cards.json`](source-decision-cards.json) | JSON | 依據 `external-source-update-policy.md` §4.1 之 16 個外部資料來源群組逐筆決策卡（含授權、用途、地域、quota、建議週期等） |
| [`unsigned-receipt-template.json`](unsigned-receipt-template.json) | JSON | 符合法務交辦單與執行規畫要求之未簽署權威 Receipt 結構模板，明定具名簽署人與外部回讀系統規範 |
| [`missing-human-inputs.json`](missing-human-inputs.json) | JSON | H01（法務權威身分與回讀系統）與 H02（Dev 風險接受細節）缺口清單，以及 Stage B 入場條件 |
| [`command-receipts/`](command-receipts/evidence-collection-receipts.json) | 目錄 | 包含 [`command-receipts/evidence-collection-receipts.json`](command-receipts/evidence-collection-receipts.json) 記錄之實際執行命令、exit code、SHA 與輸出收據 |
| [`sbom.cdx.json`](sbom.cdx.json) | JSON | 由 `generate_sbom.py` 產出之 CycloneDX 1.5 格式完整軟體物料清單（775 個元件） |
| [`npm-audit-receipt.json`](npm-audit-receipt.json) | JSON | 由 `npm_audit_gate.py` 產出之生產環境 npm audit 脫敏收據（0 high / critical） |

---

## 2. D01–D14 OSS 決策矩陣摘要 (Case Matrix Summary)

詳細資料見 [`case-matrix.json`](case-matrix.json)。

### 2.1 個案決策 (D01–D05)

| 編號 | Case ID | 授權 / 套件 | 2026-08-08 盤點版本 | 當前 lockfile 版本 | 使用者選擇 | 落地與義務查核重點 |
|---|---|---|---|---|---|---|
| **D01** | `LGPL-SHARP-LIBVIPS` | LGPL-3.0-or-later<br>`@img/sharp-libvips-*`, `sharp` | 1.3.2 / 0.35.3 | 1.3.2 / 0.35.3 (一致) | A: 允許使用 | 維持功能；確認動態載入、未修改上游 binary、保留 NOTICE 與 upstream source offer。允許不等於免除義務。 |
| **D02** | `LGPL-PSYCOPG2` | LGPL with linking exception<br>`psycopg2-binary` | 2.9.12 | 2.9.12 (一致) | A: 接受上游例外 | Receipt 綁定 2.9.12 及上游 linking exception 條文；保留 NOTICE，不得修改原始碼。 |
| **D03** | `LGPL-PSYCOPG3` | LGPL-3.0-only<br>`psycopg`, `psycopg-binary`, `psycopg-pool` | 3.3.4 / 3.3.1 | 3.3.4 / 3.3.1 (一致) | A: 附條件允許 | 依動態連結、保留 NOTICE、提供 source offer、保留 relink 能力處理；**不沿用** psycopg2 linking exception。 |
| **D04** | `LGPL-MOOCORE` | LGPL-2.1-or-later<br>`moocore`, `pymoo` | 0.3.2 / 0.6.2 | 0.3.2 / 0.6.2 (一致) | A: 附條件允許 | 動態載入、使用未修改上游 binary、保留 NOTICE；核對發布形態義務。 |
| **D05** | `FIRST-PARTY-UNLICENSED` | UNKNOWN (未設定)<br>8 個 workspace packages | 8 packages | 8 packages (一致) | B: 標示 UNLICENSED | 僅限證明第一方身分套件（`@oday-plus/*`）；保留自家原始碼權利，**不得**群組放行第三方 UNKNOWN 套件。 |

#### D05 第一方套件清單 (8 個)
1. `@oday-plus/ui` (`packages/ui`)
2. `@oday-plus/design-tokens` (`packages/design-tokens`)
3. `@oday-plus/testkit` (`packages/testkit`)
4. `@oday-plus/ui-domain` (`packages/ui-domain`)
5. `@oday-plus/domain-types` (`packages/domain-types`)
6. `@oday-plus/schemas` (`packages/schemas`)
7. `@oday-plus/web` (`apps/web`)
8. `@oday-plus/openapi-client` (`packages/openapi-client`)

### 2.2 共通政策與稽核現況 (D06–D14)

- **D06 (Dev Toolchain Vulnerability)**:
  - 舊 2026-08-08 盤點記錄之 13 個 high 漏洞已在後續依賴升級中完全修復。
  - 當下未抑制 audit 實測：`pip-audit` 215 個 Python 依賴為 **0 漏洞**；`npm audit` 567 個套件（含 dev 及 prod）為 **0 漏洞**。
  - 依執行規畫 §2.2：「先取得當下未抑制的掃描結果；若已無 finding，直接記錄無需例外，不建立預防性空白例外。」因此本案無需建立 dev toolchain waiver 或 exception。
- **D07 (UNKNOWN / PROPRIETARY)**: 第一方依 D05 處理；第三方套件維持逐件審查，未審查前不得進入可發布產物。
- **D08 (Permissive Licenses)**: MIT (npm: 368, py: 99), ISC (npm: 32), Apache-2.0 (npm: 23, py: 68), BSD-2/3-Clause (npm: 18, py: 47) 等均允許使用，各自保留對應義務（如 Apache-2.0 NOTICE、caniuse-lite CC-BY-4.0 等）。
- **D09 (NOTICE Auto-generation)**: 每次發布由 `generate_oss_notice.py` 自動產生並綁定 SBOM。
- **D10 (Exception Approval Authority)**: 必須由具名且有授權之 Legal/Security/Risk 人員核准，AI 不得代簽。
- **D11 (Exception Scope & Duration Limits)**: 嚴禁永久或全域例外；每筆必須綁定套件/finding、環境、release 及有效期限。
- **D12 (Receipt Fail-Closed)**: 缺失、過期、不可回讀或 hash 不符時一律 fail closed。
- **D13 (Narrow Denylist)**: AGPL / SSPL / BSL 預設拒絕；GPL 逐件 review；LGPL 依個案條件處理。
- **D14 (Authoritative Receipt)**: 必須能由外部權威系統回讀；hash 僅證明完整性，不能單獨證明批准權。

---

## 3. §4.1 外部資料來源決策卡 (External Data Source Decision Cards)

詳細資料見 [`source-decision-cards.json`](source-decision-cards.json)。

本包對 `external-source-update-policy.md` §4.1 列出之 16 個來源群組完成逐筆卡片拆分。所有來源預設維持 `PENDING_HUMAN_DECISION` 且 `ENABLED=false`。未知欄位均明示 `unknown`，**禁止群組批准**；runbook 的 default cron 僅為排程建議，**不是**官方發布頻率。

| 來源 ID | 供應者 / 名稱 | 資料集 / Endpoint | 授權版本 | 官方發布頻率 | 建議更新週期 | 預設 Cron |
|---|---|---|---|---|---|---|
| `CWA` | 中央氣象署 | 即時氣象觀測、災害警示、預報 | 政府資料開放授權條款 1.0 | 觀測分鐘級 / 警報事件驅動 | 每 10 分鐘 | `*/10 * * * *` |
| `TDX` | 運輸資料流通服務平台 | 停車場即時、路況、公車動態 | unknown (需依 endpoint 審查) | 分鐘級營運狀態 | 每 10 分鐘 | `*/10 * * * *` |
| `MARKET_EVENTS` | 市場事件 / 公告來源 | 公共公告、候選事件 | unknown (需逐來源審查) | 事件驅動 | 每小時 15 分 | `15 * * * *` |
| `MOF` | 財政部 | 營業登記主檔異動 | unknown (政府開放資料條款) | 每日異動 | 每日 02:30 | `30 2 * * *` |
| `OSM` | OpenStreetMap Foundation | 道路與步行路網 extract | ODbL 1.0 | 持續編輯 / 週期 extract | 每週日 03:00 | `0 3 * * 0` |
| `MOI_RENTAL` | 內政部實價登錄 | 租賃實價批次成交資料 | unknown (政府開放資料條款) | 每月批次 (約 1-10 日) | 每月 8 日 04:00 | `0 4 8 * *` |
| `RIS` | 內政部戶政司 | 人口與戶數統計 | unknown (政府開放資料條款) | 每月發布 | 每月 10 日 03:00 | `0 3 10 * *` |
| `NLSC` | 國土測繪中心 | 行政與地籍界線 | unknown (政府開放資料條款) | 低頻 / 版本化發布 | 每月 15 日 04:00 | `0 4 15 * *` |
| `OVERTURE` | Overture Maps Foundation | 批次 POI (Places) release | CDLA-Permissive-2.0 | 約每月 release | 每月 2 日 05:00 | `0 5 2 * *` |
| `FOURSQUARE` | Foursquare | 商業 POI API (Places API) | unknown (商業 API 契約條款) | 即時 API | 每週一 05:00 | `0 5 * * 1` |
| `BRAND_STORE_LOCATOR` | 各品牌門市定位器 / feeds | 品牌官網門市清單 / feed | unknown (需逐品牌/網站審查) | 各品牌不定 | 每週一 05:30 | `30 5 * * 1` |
| `TGOS` | 內政部 TGOS | 地址地理編碼 API | unknown (TGOS 服務條款) | 按需 API | 按需 (無 cron) | N/A |
| `GOOGLE_PLACES` | Google Maps Platform | Places API (付費驗證) | unknown (Google Maps ToS) | 即時 API | 按需 (無 cron) | N/A |
| `LISTINGS` | 核准房源 feeds | 房源委託與刊登 feed | unknown (需逐供應商契約審查) | 事件驅動 | 事件驅動 (無 cron) | N/A |
| `MOBILITY` | 移動大數據供應商 | 聚合移動人流批次 | unknown (需確認供應商與個資範圍) | 事件驅動 | 事件驅動 (無 cron) | N/A |
| `SURVEY` | 第一方現場調查 | 現場調查提交與審核記錄 | 第一方自建 (Internal) | 人員提交事件驅動 | 事件驅動 (無 cron) | N/A |

---

## 4. 人工缺口與 Stage B 入場條件 (Missing Human Inputs & Stage B Entry)

詳細資料見 [`missing-human-inputs.json`](missing-human-inputs.json) 及 [`unsigned-receipt-template.json`](unsigned-receipt-template.json)。

### 4.1 缺口分類 (H01 & H02)

1. **H01 (OSS 正式身分與權威系統)**：
   - 缺漏：具名 approver (`display_name`)、身分系統 principal ID (`principal_id`)、授權角色 (`role`)、外部權威系統名稱 (`source_system`)、外部 ticket/decision ID (`approval_reference`)。
   - 影響：阻擋正式 receipt 簽署與 policy enforcement 生效；**不阻擋** Stage A 工程準備。
2. **H02 (Dev Toolchain 風險接受細節)**：
   - 現況：最新未抑制 audit（npm audit 與 pip-audit）均為 0 漏洞，原 13 high 已完全解決。目前無 active finding，暫無需填寫風險接受欄位。

### 4.2 三類結果分類 (Classification Summary)

- **掃描 Finding (Scan Findings)**: 0 active findings (npm audit 0, pip-audit 0)。
- **工具錯誤 (Tool Errors)**: 0 errors。SBOM generator (`generate_sbom.py`)、npm audit gate (`npm_audit_gate.py`) 及 pip-audit gate (`pip_audit_gate.py`) 均順利執行並產出收據。
- **已修復 (Already Fixed)**: 2026-08-08 盤點提及之 13 個 dev toolchain high 漏洞已在後續版本升級中完全修復。

### 4.3 Stage B 入場條件 (Entry Conditions for Stage B)

在以下條件齊備前，**禁止**將政策標示為已生效、禁止啟用任何外部資料來源，亦禁止宣稱 production gate 通過：
1. 由具名 Legal/Security/Risk 權責主管於外部權威系統建立並簽發正式核准 Receipt。
2. 提供外部可回讀之 reference 與簽章/雜湊。
3. 針對需要啟用的資料來源（如 CWA、OSM 等），逐筆完成授權條款審查並填入對應 receipt SHA-256。

---

## 5. 執行收據與命令記錄 (Command Receipts)

詳細命令收據見 [`command-receipts/evidence-collection-receipts.json`](command-receipts/evidence-collection-receipts.json)。

| Receipt ID | 執行命令 | 目的 | Exit Code | 耗時 | 結果摘要 |
|---|---|---|---|---|---|
| `CMD-001` | `uv run python delivery_toolchain/security/generate_sbom.py --help` | 確認 SBOM 工具支援 `--output` 參數 | `0` | 3s | 支援 `--output` 與 `--check` |
| `CMD-002` | `uv run python delivery_toolchain/security/generate_oss_notice.py --help` | 確認 NOTICE 工具支援 `--check` 與 `--reconcile` | `0` | 3s | 支援三種模式，確認需完整安裝樹 |
| `CMD-003` | `sha256sum package-lock.json uv.lock` | 記錄 lockfile 基準雜湊 | `0` | 1s | npm: `dbda4082...`, py: `ba5c393e...` |
| `CMD-004` | `grep` lockfile 比對 D01–D05 版本 | 比對 2026-08-08 盤點版本 | `0` | 3s | D01–D05 套件版本完全一致 |
| `CMD-005` | `uv run python delivery_toolchain/security/generate_sbom.py --output docs/evidence/human-decisions/ODP-OSS-DECISION-PACK-001/sbom.cdx.json` | 產出 CycloneDX 1.5 SBOM 證據 | `0` | 2s | 775 個元件目錄建立，Digest: `d76c9428...` |
| `CMD-006` | `uv run python delivery_toolchain/security/pip_audit_gate.py` | 未抑制 Python 依賴漏洞掃描 | `0` | 30s | PASS: 215 個依賴 0 漏洞 |
| `CMD-007` | `uv run python delivery_toolchain/security/npm_audit_gate.py --receipt docs/evidence/human-decisions/ODP-OSS-DECISION-PACK-001/npm-audit-receipt.json` | 生產環境 npm 依賴門禁稽核 | `0` | 3s | PASS: 0 high/critical 漏洞 |
| `CMD-008` | `npm audit --json` | 全環境（dev + prod）npm 漏洞掃描 | `0` | 1s | 0 vulnerabilities (567 deps) |

---

## 6. 合規與邊界宣告 (Governance & Boundary Disclosures)

1. **不建立 Waiver 或 Bypass**: 本交付物未建立任何 waiver 檔案、未加入 `--ignore-vuln` 參數，亦未設置任何環境變數旁路。
2. **不偽造身分或簽名**: 未使用 `Human/Ops` 或 AI 名稱充當正式簽署人；`unsigned-receipt-template.json` 保持未簽署狀態。
3. **不變更既有門禁或外部設定**: NOTICE、license policy、lockfiles、CI gates、雲端 provider、配額與 production 設定均維持不變。
4. **Stage A 獨立交付**: 本證據包完成 WP-10 之 Stage A 工程盤點與準備交付，下一階段（WP-11 及 Stage B）須待 H01 人工輸入到位後始得啟動。
