# ODP-OSS-POLICY-NOTICE-IMPLEMENTATION-001: 第一方 UNLICENSED 標示與 NOTICE／SBOM 工程

- **Task ID**: `ODP-OSS-POLICY-NOTICE-IMPLEMENTATION-001`
- **Owner / Reviewer**: Antigravity4 (接手自 Antigravity2 / Claude) / Codex
- **Base**: `dev` @ `3958385788bac5cf873c52a3be631481b22e118d`（已合併最新 `origin/dev` base advance）
- **Depends on**: `ODP-OSS-DECISION-PACK-001`（Stage A 盤點與 case matrix）
- **對應規畫**: `ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md` WP-11 階段一
- **本文件的效力**: 這是工程落地的證據紀錄。**它不是核准，也不代表任何 OSS 政策已生效。**

---

## 1. 這個 task 做了什麼、沒做什麼

D05（`FIRST-PARTY-UNLICENSED`）已由使用者選 B：自家 workspace 套件標示 `UNLICENSED`。
本 task 只做該決策的工程落地，範圍如下。

**做了**：

1. 八個第一方 workspace package manifest 標示 `license: "UNLICENSED"`。
2. 同步 `package-lock.json` 中對應的八筆 workspace metadata。
3. 修掉實測到的第一方辨識缺口（見 §4），使第一方標示在 NOTICE 與 SBOM 兩端一致。
4. 在 `docs/security/license_policy.json` 的該案件下記錄裁決與其界線（狀態仍為 `proposed`）。
5. 補上回歸測試，把「第三方不得因第一方規則被放行」釘成可驗證條件。

**明確沒做**（逐條對應驗收邊界）：

- 沒有讓任何 license policy 生效。`license_policy.json` 的 `status` 仍是 `proposed`，
  只有外部權威 receipt 能改變這件事。
- 沒有建立任何 exception、waiver、豁免或 allow-list 項目；`license_exemptions.json` 未被修改。
- D06、H01、H02 不在本 task 生效，也未被填入任何欄位。
- 沒有升級任何依賴、沒有重寫 lockfile（見 §3.2 的實測差異）。
- 沒有啟用任何外部資料來源、沒有讀取 secret、沒有連線 production、
  沒有修改 IAM／release lease／provider／quota，也沒有調降任何 required check。

---

## 2. 第一方 ownership 核對（交付前重新量過）

`case-matrix.json` 的 D05 列出八個套件。交付當下逐一核對 workspace 路徑與 manifest 內的
`name` 欄位，八個全部仍存在且名稱一致，沒有新增、改名或移除，因此標示落在真實 manifest 上，
沒有任何一項是用推測補上的。

| # | case-matrix 套件名 | workspace 路徑 | 現況 manifest `name` | 核對 | 本次標示 |
|---|---|---|---|---|---|
| 1 | `@oday-plus/ui` | `packages/ui` | `@oday-plus/ui` | 一致 | `UNLICENSED` |
| 2 | `@oday-plus/design-tokens` | `packages/design-tokens` | `@oday-plus/design-tokens` | 一致 | `UNLICENSED` |
| 3 | `@oday-plus/testkit` | `packages/testkit` | `@oday-plus/testkit` | 一致 | `UNLICENSED` |
| 4 | `@oday-plus/ui-domain` | `packages/ui-domain` | `@oday-plus/ui-domain` | 一致 | `UNLICENSED` |
| 5 | `@oday-plus/domain-types` | `packages/domain-types` | `@oday-plus/domain-types` | 一致 | `UNLICENSED` |
| 6 | `@oday-plus/schemas` | `packages/schemas` | `@oday-plus/schemas` | 一致 | `UNLICENSED` |
| 7 | `@oday-plus/web` | `apps/web` | `@oday-plus/web` | 一致 | `UNLICENSED` |
| 8 | `@oday-plus/openapi-client` | `packages/openapi-client` | `@oday-plus/openapi-client` | 一致 | `UNLICENSED` |

這張表由 `tests/security/test_oss_notice.py::test_the_eight_first_party_manifests_declare_unlicensed`
持續守住：任何一個 workspace 被改名，測試會直接指出 D05 的裁決不會自動延伸到新名字，
而不是默默把標示套到另一個套件上。

---

## 3. 變更逐項說明

### 3.1 八個 package manifest

在既有 `"private": true` 之後加入 `"license": "UNLICENSED"`，其餘欄位未動。

### 3.2 `package-lock.json`：只同步 workspace metadata

用 `npm install --package-lock-only --ignore-scripts --no-audit --no-fund` 產生，
由 npm 自己寫入格式，沒有手改 JSON。實測差異就是八行：

```
 package-lock.json | 8 ++++++++
 1 file changed, 8 insertions(+)
```

八行全部是那八筆 workspace 條目新增的 `"license": "UNLICENSED"`。沒有任何版本解析變動、
沒有依賴升級、沒有 lockfile 重寫。

| 檔案 | 變更前 sha256 | 變更後 sha256 |
|---|---|---|
| `package-lock.json` | `dbda408248444c617bf0b124e9d2ce73ce18be0ee6bf6d463f148ba3cd8b1c8a` | `3afe5f1b6b3b91e32c2d54e60ceb752b6d63a96df19fc827b324af2f54ae2f3e` |

（變更前的雜湊與 `ODP-OSS-DECISION-PACK-001` 的 `CMD-003` 收據所記錄的 npm lockfile 基準一致。）

### 3.3 `NOTICE-THIRD-PARTY.md`：產物零變更，工具未重建

既有 generator 本來就把第一方套件排除在第三方聲明之外，因此標示 `UNLICENSED` 後
NOTICE 產物**完全沒有變動**（相對 base 的 diff 為空），`--check` 前後都是 exit 0。
上游的 NOTICE 與 attribution 義務段落一字未動：LGPL 元件、Apache-2.0 的 NOTICE 保留、
`caniuse-lite` 的 CC-BY-4.0 標示都仍在，並由既有測試守住。

**結論：NOTICE generator 本身足夠，沒有重建工具。** 真正的缺口在 §4。

### 3.4 `docs/security/license_policy.json`：記錄裁決，不改變門禁行為

在 `FIRST-PARTY-UNLICENSED` 案件下把 `decision_needed` 換成 `decision` 物件，記錄裁決編號、
使用者選項、來源文件、落地方式、第一方身分如何證明，以及這個標示**不**授予什麼。

該案件的 `license` 欄位刻意維持 `UNKNOWN`：這個欄位會餵進門禁的 `review_required` license
集合，若改寫成 `UNLICENSED`，任何第三方只要宣告該字串就會被導向 review 而不是 fail closed。
`status` 仍為 `proposed`，allow／deny 清單、compound expression 規則、exemptions 均未變動。

### 3.5 測試

新增七項回歸測試，全部落在本 task 已宣告的兩個測試檔內：
1. `tests/security/test_oss_notice.py::test_the_eight_first_party_manifests_declare_unlicensed`：驗證八個第一方 manifest 的名稱與 `license: UNLICENSED` 標示。
2. `tests/security/test_oss_notice.py::test_the_lockfile_carries_the_same_marker_as_the_manifests`：驗證 `package-lock.json` 與 manifest 宣告一致。
3. `tests/security/test_oss_notice.py::test_a_third_party_may_not_inherit_the_first_party_exclusion`：驗證 `is_first_party` 邊界邏輯（精確匹配與 scope 匹配，非裸字串前綴）。
4. `tests/security/test_oss_notice.py::test_lookalike_third_party_is_collected_and_fails_the_policy`：驗證 NOTICE generator 對 lookalike 第三方套件的端到端行為。
5. `tests/security/test_oss_license_gate.py::test_sbom_catalogues_no_first_party_workspace_package`：驗證 SBOM 不收錄第一方套件與撞名公開 purl。
6. `tests/security/test_oss_license_gate.py::test_third_party_unlicensed_is_not_admitted_by_the_first_party_marker`：驗證第三方宣告 `UNLICENSED` 仍 fail closed。
7. `tests/security/test_oss_license_gate.py::test_sbom_workspace_third_party_dependencies_retained_in_root_synthetic`（commit `4c992108` 新增）：驗證第一方 workspace component 排除後，workspace 的第三方依賴仍能正確解析真實 purl 並保留於 application root 節點（合成 fixture 驗證）。

測試中的 `node_modules` 與 lockfile 是**合成 fixture**，不是安裝樹，
只證明程式邏輯，不證明任何真實套件的授權狀態。

測試總數：39 passed（base 為 32 項，初版實作新增 6 項至 38 項，後續補上 workspace 依賴回歸測試至 39 項）。

---

## 4. 實測到的缺口（不是預防性修改）

### 4.1 SBOM 第一方判定、workspace 依賴掛接與巢狀 node_modules purl 解析修正

在 `generate_sbom.py` 實測中發現兩項與 CycloneDX SBOM 產生相關的缺口並予以修復：

1. **第一方 workspace 元件排除與 workspace 第三方依賴掛接至 root**：
   - `generate_sbom.py` 原以 lockfile 的**路徑**切開推導 npm 元件名稱（`packages/ui` 變成 `ui`），導致名稱不符第一方判定而被誤列為第三方元件，產生撞名公開 registry 之 purl（例如 `pkg:npm/ui@0.1.0` 等 8 個套件，授權全標 `UNKNOWN`）。
   - 修法：workspace 成員改由 lockfile 條目自身的 `name` 欄位取得完整名稱（`@oday-plus/*`），精確辨識第一方身分並自第三方 components 排除。
   - 同時，在排除第一方 component 的同時，收集 workspace 宣告的直接第三方依賴（12 條依賴：`@deck.gl/core`、`@deck.gl/layers`、`@deck.gl/react`、`@deck.gl/widgets`、`@types/pg`、`argon2`、`h3-js`、`maplibre-gl`、`next`、`pg`、`react`、`react-dom`），解析其於 `package-lock.json` 中的真實 purl 並接入 application root 節點（`pkg:generic/alfloop-dev/odayplus@{git_sha}`），確保 root dependsOn 完整記錄直接使用關係。

2. **巢狀 `node_modules/` 路徑推導邏輯修正與 14 筆第三方 purl 更正**：
   - `generate_sbom.py:302-305` 原使用 `pkg_path.startswith("node_modules/")` 搭配 `pkg_path.replace("node_modules/", "")`。在存在巢狀依賴路徑時（例如 `node_modules/@maplibre/vt-pbf/node_modules/pbf` 或 `node_modules/@types/node/node_modules/node`），`replace("node_modules/", "")` 會移除所有路徑片段中的 `node_modules/`，產生如 `pkg:npm/@maplibre/vt-pbf/pbf@5.1.2` 或 `pkg:npm/node@20.14.10` 等非標準或損毀的 purl。
   - 修法：改為 `if "node_modules/" in pkg_path: pkg_name = pkg_path.split("node_modules/")[-1]` 並對無 scope 套件處理 slash，精確截取最內層真實套件名稱。這項修正修正了 14 筆巢狀第三方套件的 purl 名稱及其在 dependency graph 的 reference/dependsOn（例如 `@maplibre/vt-pbf/pbf@5.1.2` -> `pbf@5.1.2`、`node@20.14.10` -> `@types/node@20.14.10`、`@typescript-eslint/typescript-estree/minimatch@10.2.5` -> `minimatch@10.2.5`、`@typescript-eslint/visitor-keys/eslint-visitor-keys@5.0.1` -> `eslint-visitor-keys@5.0.1` 等）。

重產後的實測差異：

| 項目 | 修正前 | 修正後 |
|---|---|---|
| SBOM 元件總數 | 775 | 767 |
| 被移除的第一方元件 | `design-tokens`, `domain-types`, `openapi-client`, `schemas`, `testkit`, `ui`, `ui-domain`, `web`（皆為 `0.1.0`／`UNKNOWN`） | — |
| 新增的元件 | — | 無（總套件數因排除 8 個第一方由 775 降至 767） |
| 巢狀第三方 purl 修正 | 14 筆路徑損毀 purl | 14 筆修正為標準 purl 及對應 graph edge |
| root 節點 direct dependsOn | 39（僅 Python） | 51（39 Python + 12 npm workspace 依賴） |
| dependency graph 懸空引用 | 0 | 0 |

除了排除 8 個第一方元件、掛接 12 個 workspace 第三方依賴至 root、修正 14 筆巢狀第三方 purl 名稱與 edge，以及隨 commit 變更的 root git-sha 之外，SBOM 無其他差異。

### 4.2 第一方判定用裸字串前綴，會吞掉第三方

兩個 generator 原本都用 `FIRST_PARTY_PREFIXES = ("@oday-plus/", "oday-plus")` 做
`startswith` 判定。實測既有常數的行為：

```
'oday-plus-lookalike'  → startswith(("@oday-plus/", "oday-plus")) = True
```

也就是說，registry 上任何叫 `oday-plus-*` 的**第三方**套件都會被當成自家套件，
從 NOTICE 與 policy 評估中一起消失。目前的安裝樹沒有這種套件，但第一方標示現在是
承重判定，這個洞必須先補起來。

修法：改為「`@oday-plus/` scope」或「等於 root 名稱 `oday-plus`」，不再用裸前綴。
`is_first_party()` 在兩個 generator 中各自定義，沒有引入跨檔 import
（這兩個腳本在 workflow 中是以 `python3 <path>` 直跑的）。

---

## 5. 本 task **未涵蓋**的第一方 manifest（明確揭露）

D05 列舉的是八個 npm workspace 套件。以下第一方 manifest 不在該列舉內，本 task **未修改**，
需要另一次明確裁決才動：

| manifest | 現況 | 為何未動 |
|---|---|---|
| 根目錄 `package.json`（`oday-plus`） | 無 `license` 欄位 | 不在 D05 列舉的八個之內。它已被兩個 generator 以 root 名稱排除，不會被當成第三方。 |
| `pyproject.toml`（`odayplus`, pypi） | `license = { text = "MIT" }` | 第一方 python 套件宣告的是 MIT，與 D05 的 `UNLICENSED` 方向不同。D05 未涵蓋 python 端，改動它是法務語意變更，不是本 task 的工程落地。 |

附帶事實：`odayplus` 在 SBOM 中仍以 `UNKNOWN` 列出（`uv.lock` 的 root 條目沒有 license 欄位），
這是既有狀態，本 task 未處理，也未因此宣稱它已被審查。

---

## 6. 邊界說明：超出 task `owned_paths` 的檔案

以下三個檔案不在 task 的 `owned_paths` 清單內，但屬於 WP-11 §範圍宣告的
「`docs/security/license_policy.json`、既有 license／NOTICE 工具與測試」，
且其中兩項是被 in-scope 變更**強制**帶出來的：

| 檔案 | 為何被納入 |
|---|---|
| `delivery_toolchain/security/generate_sbom.py` | §4 的實測缺口。不修就會把八個撞名公開 purl 標成 `UNLICENSED`。 |
| `docs/evidence/sbom.json` | 同步 lockfile 必然改變 SBOM 產物；不重產則 `generate_sbom.py --check` 必紅。 |
| `docs/security/license_policy.json` | WP-11 階段一明列的「更新政策提案」；只記錄裁決，不改門禁行為。 |

沒有修改其他 task 的工作樹，也沒有動任何共用 gate 的判定門檻。

---

## 7. 驗證收據

環境準備（兩者皆為本地開發安裝，非外部來源啟用）：

| 命令 | Exit | 說明 |
|---|---|---|
| `npm ci` | `0` | NOTICE／SBOM 的授權欄位讀自安裝樹，缺 `node_modules` 會判成 partial install |
| `uv sync --frozen --python 3.12` | `0` | 釘 3.12：`pgserver` 沒有 cp314 wheel |

### 7.1 歷史提交量測紀錄（Historical Receipts）

保留任務推進過程中的歷史量測與原始收據：

1. **Commit `98a1ed1ba5c2770990eba3fc0526498045afa9e0`**（初版實作，38 passed）：
   - `git diff --check`：exit `0`，耗時 `<1s`
   - `uv run pytest tests/security/test_oss_notice.py tests/security/test_oss_license_gate.py -q`：exit `0`，耗時 `52s`（38 passed：base 32 + 本次新增 6）
   - `uv run python delivery_toolchain/security/generate_oss_notice.py --check`：exit `0`，耗時 `1s`

2. **Commit `0dd3e6e2c23a82751a842d6b33bd30f53e268e43`**（第一次 dev base advance merge，PR #1279 第一輪審查）：
   - `git diff --check`：exit `0`，耗時 `0.017s`，收據 ID `d42f5c992a979656`
   - `uv run pytest tests/security/test_oss_notice.py tests/security/test_oss_license_gate.py -q`：exit `0`，耗時 `34.334s`（38 passed），收據 ID `034f71c3833b11eb`
   - `uv run python delivery_toolchain/security/generate_oss_notice.py --check`：exit `0`，耗時 `1.257s`，收據 ID `6adc57ccadfc191d`

3. **Commit `4c992108962437a3f8a0265b95690737d5900777`**（SBOM workspace 邊與 14 筆巢狀 purl 修正，新增第 39 個測試，PR #1279 第二輪審查）：
   - `git diff --check`：exit `0`，耗時 `0.017s`，收據 ID `87a2f956d87abb6a`
   - `uv run pytest tests/security/test_oss_notice.py tests/security/test_oss_license_gate.py -q`：exit `0`，耗時 `29.889s`（39 passed：base 32 + 新增 7），收據 ID `84b5dc73e65bb23a`
   - `uv run python delivery_toolchain/security/generate_oss_notice.py --check`：exit `0`，耗時 `1.182s`，收據 ID `1860bc84f7ce26f0`

4. **Commit `62d16c01f17d5d679caefbec1eb1c12539539acd`**（第三輪審查 head）：
   - `git diff --check`：exit `0`，耗時 `0.017s`，收據 ID `beaf103037fcfb77`
   - `uv run pytest tests/security/test_oss_notice.py tests/security/test_oss_license_gate.py -q`：exit `0`，耗時 `28.761s`（39 passed），收據 ID `bc282f990392c3bb`
   - `uv run python delivery_toolchain/security/generate_oss_notice.py --check`：exit `0`，耗時 `1.042s`，收據 ID `11815823784751ac`

### 7.2 本輪送審 Head 實測收據

本輪完成第三輪 `origin/dev` base advance merge（`3958385788ba`）並修正本證據文件（更正 12 條直接依賴名稱與 5 個 pytest node ID）後，由 `delivery_toolchain/git/task_verification.py run` 針對送審 commit 執行並產出權威收據：

- `git diff --check`：exit `0`
- `uv run pytest tests/security/test_oss_notice.py tests/security/test_oss_license_gate.py -q`：exit `0`（39 passed）
- `uv run python delivery_toolchain/security/generate_oss_notice.py --check`：exit `0`
- 額外量測 `uv run python delivery_toolchain/security/generate_sbom.py --check`：exit `0`

綁定 head 的權威收據由 `delivery_toolchain/git/task_verification.py` 記錄於 `.orchestrator/evidence/` 並由 `task_finalize.sh` 於送審前核驗。

---

## 8. 不宣稱事項

1. 本文件與其中的測試結果**不代表**任何 OSS 元件、授權或外部資料來源已獲批准。
   `ODP-OSS-DECISION-PACK-001` 的 Stage B 入場條件（H01 具名權責簽署與外部可回讀 receipt）
   仍未滿足。
2. 測試通過是離線程式行為的證明，**不代表**真實資料已提供，也**不代表**任何 live 能力已驗證。
3. `UNLICENSED` 是套件授權標示，不是部署開關，也不是供應鏈驗證結果；
   它保留自家原始碼權利，不對第三方授予任何權利。
4. 第三方的 `UNKNOWN` 或 `UNLICENSED` 仍然 fail closed，未因第一方規則被放行。
