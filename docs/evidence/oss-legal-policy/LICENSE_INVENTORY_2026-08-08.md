# OSS 授權盤點與決策依據（2026-08-08）

供 `ODP-PLAN-OSS-LEGAL-POLICY-001` 的具名 Legal／Security／Risk owner 審閱。

本文件與 `docs/security/license_policy.json`、`docs/security/license_exemptions.json`
皆為**提案**，在權威決策收據回來之前不具效力。

---

## 1. 盤點方法

| 生態系 | 來源 | 數量 |
|---|---|---|
| npm | `node_modules/**/package.json` 的 `license` 欄位，遞迴含巢狀 | 464 |
| Python | `uv.lock` 宣告的套件名，與已安裝 distribution metadata 取交集 | 240 宣告 |

Python 側**刻意用 `uv.lock` 過濾**。直接列舉系統 Python 會混入 Ubuntu 自帶套件
（`apparmor`、`cloud-init`、`ufw`、`ubuntu-pro-client`、`sos`、`python-apt` 等，
多為 GPL），那些不是本專案的依賴，列進來會嚴重誤導風險評估。

---

## 2. 授權分布（npm，464 套件）

| 數量 | 授權 |
|---|---|
| 368 | MIT |
| 32 | ISC |
| 23 | Apache-2.0 |
| 11 | BSD-2-Clause |
| 8 | UNKNOWN（**全部是自家 workspace 套件**） |
| 7 | BSD-3-Clause |
| 5 | MPL-2.0 |
| 2 | LGPL-3.0-or-later |
| 1 | Apache-2.0 AND LGPL-3.0-or-later AND MIT |
| 1 | 0BSD |
| 1 | BlueOak-1.0.0 |
| 1 | CC0-1.0 |
| 1 | CC-BY-4.0 |
| 1 | Python-2.0 |
| 1 | (MIT OR CC0-1.0) |
| 1 | (MIT OR Apache-2.0) |

**兩棵樹都沒有 GPL、AGPL、SSPL、BSL。** deny list 目前是預防性的，不是在處理既有問題。

---

## 3. 需要決策的三個案例

以下每一個都是**真實存在的案例**，不是假設。

### 3.1 `LGPL-SHARP-LIBVIPS` — LGPL-3.0-or-later，在 production

```
@oday-plus/web
  └─ next@15.5.21
      └─ sharp@0.35.3
          ├─ @img/sharp-libvips-linux-x64@1.3.2       LGPL-3.0-or-later
          └─ @img/sharp-libvips-linuxmusl-x64@1.3.2   LGPL-3.0-or-later
```

**Next.js 的圖片最佳化把 libvips 帶進 production image。** 這是典型的 LGPL
動態連結情境：sharp 載入預先編譯好的 libvips 共享函式庫，未加修改。

要決定：直接允許／附條件允許（連結方式、NOTICE、source offer）／逐 release
review／禁止。

**若決定禁止**：必須停用或替換 Next.js 圖片最佳化。這不是拿掉一個套件就好的改動。

（另有 `@img/sharp-wasm32@0.35.3`，授權為 `Apache-2.0 AND LGPL-3.0-or-later AND MIT`，
目前狀態是 `extraneous`，不在 production 依賴樹內。）

### 3.2 `LGPL-PSYCOPG2` — LGPL with exceptions，在 production

`psycopg2-binary@2.9.12`，PostgreSQL 驅動程式。上游宣告 LGPL-3 加上允許連結的
例外條款。**該例外條款的實際文字是這件事可行與否的關鍵，應該讀過而非假定。**

要決定：確認上游例外條款可接受，或要求改用其他授權的驅動程式。

### 3.3 `FIRST-PARTY-UNLICENSED` — 8 個自家套件沒有授權欄位

```
@oday-plus/ui              @oday-plus/design-tokens
@oday-plus/testkit         @oday-plus/ui-domain
@oday-plus/domain-types    @oday-plus/schemas
@oday-plus/web             @oday-plus/openapi-client
```

這 8 個**不是第三方風險**，是我們自己的 workspace 套件。但掃描器無法區分
「自家未標註」與「第三方授權不明」——兩者都是 UNKNOWN。

要決定：在自家 workspace 套件上宣告授權（或明確標示 `UNLICENSED`），
讓 gate 能區分這兩種情況。

---

## 4. 附帶義務的授權（提案為允許）

| 授權 | 套件 | scope | 義務 | 目前狀態 |
|---|---|---|---|---|
| MPL-2.0 | `lightningcss`（+2 平台變體，經 next） | prod | 檔案層 copyleft：修改到 MPL 檔案才需公開該檔案原始碼 | **未修改任何 MPL 檔案**，僅使用未修改的上游產物 |
| MPL-2.0 | `axe-core`、`@axe-core/playwright` | dev | 同上 | 同上 |
| MPL-2.0 | `certifi@2023.11.17` | prod (python) | 同上 | 同上 |
| CC-BY-4.0 | `caniuse-lite@1.0.30001806`（經 browserslist） | prod | **需要標示出處**，這是資料授權不是程式碼授權 | **目前發布物中沒有任何 NOTICE 標示** |
| Apache-2.0 | 23 個套件 | 混合 | 保留 NOTICE、修改需聲明變更 | 未查核 NOTICE 是否隨發布物提供 |

`caniuse-lite` 的標示義務與 Apache NOTICE 的保留義務**目前都沒有落實**。
這兩件不需要法務決策也該補，但補的方式（NOTICE 檔位置、格式）屬於本次決策範圍。

---

## 5. 一個必須先講清楚的前提

**目前沒有任何 license gate 的實作。**

- `scripts/security/` 只有 `generate_sbom.py`、`sast_scan.py`、`secret_scan.py`、`sign_images.sh`
- 已提交的 SBOM 有 778 個元件，**授權欄位全部是空的**

所以即使政策今天核准，也**還沒有東西在執行它**。要讓政策生效，必須先：

1. `generate_sbom.py` 填入 CycloneDX 的 `licenses` 欄位
2. 寫一支 gate 腳本，比對 SBOM 與本政策，遇 deny／unknown 時 fail closed
3. 把該 gate 加入 required status checks

**核准政策本身不會產生 gate。** 這點寫在 `license_policy.json` 的 `enforcement`
區塊，避免核准後誤以為已受保護。

---

## 6. Dev toolchain 風險（交辦單 §C）

已於 2026-08-08 以 `REMEDIATE` 處置，見 PR #728。

交辦單記載的「13 個 high」是 2026-08-05 的數字；2026-08-08 實測為 2 個
（`brace-expansion`、`js-yaml`，皆 CVSS 7.5，皆僅經由 ESLint 進入，
`npm audit --omit=dev` 本來就是 0）。`npm audit fix` 不加 `--force` 即可歸零，
三個 patch 版號，無任何 major 變動——因此不構成 reviewer 禁止的
forced-major override，也不是 AI 自簽的 waiver。

**公告資料庫會自行變動，任務紀錄裡的數字會在沒人碰程式碼的情況下過期。**
決策必須針對當下的數字做。

---

## 7. 收據要求

不重複交辦單內容，見
`docs/evidence/OSS_LEGAL_POLICY_HUMAN_HANDOFF_2026-07-31.md`（PR #532）第 2、3 節。

要點：收據必須來自外部權威系統並可回讀，綁定具名 principal、
`policy_file_sha256`、SBOM 與 audit report 的雜湊、明確的 scope 與 release 範圍、
以及 UTC 的 issued/expires/review 時間。

**repo 內自填的 JSON 不構成核准。** 本次提交的兩個 JSON 檔 `status` 皆為
`proposed`，且不得由 AI agent 或 repository author 改為 `approved`。
