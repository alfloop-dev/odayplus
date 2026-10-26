# OSS 授權：四項待決案例（可簽清單）

- **對象**: 有權決定 OSS 授權使用的負責人
- **日期**: 2026-09-18
- **來源政策**: `docs/security/license_policy.json`（`ODP-OSS-License-Gate-Policy-v1`，status 仍為 `proposed`）
- **擋住什麼**: gate-4（Security and Privacy Gate）的 licence gate，連帶 dev 部署准入

---

## 這份清單是什麼

平台的 OSS 授權閘目前判定失敗，但**不是因為有違規**。實測結果是：

```
violations      : 0 項
review_required : 8 個元件（歸屬四個案例）
status          : FAIL   ← 因為有待審項目，不是因為有違規
```

閘的輸出訊息是 `Policy evaluation FAILED: 0 violations found`，讀起來自相矛盾——它只印違規數，沒印真正的原因。實際卡住的是下面四個案例，每一個都需要一次授權決定。

政策檔對這一節的註解寫得很清楚：**「These are the decisions this policy cannot make for Legal. Each is a real, present case -- not a hypothetical.」**

四個案例全是 **LGPL 家族**的動態連結情境。共通問題是：我們以未修改的形式使用上游編譯好的函式庫，是否符合貴公司對 LGPL 的政策。

---

## 案例一：LGPL-SHARP-LIBVIPS

| 項目 | 內容 |
|---|---|
| 授權 | LGPL-3.0-or-later |
| 元件 | `@img/sharp-libvips-linux-x64` 1.3.3、`@img/sharp-libvips-linuxmusl-x64` 1.3.3 |
| 依賴路徑 | `@oday-plus/web` → `next` → `sharp` → `@img/sharp-libvips-*` |
| 用途 | Next.js 的圖片最佳化功能，會進入 production 映像 |

**性質**：典型的 LGPL 動態連結案例。`sharp` 以預先編譯好的共享函式庫形式載入 `libvips`，我們不修改它。

**請決定**：
- [ ] 直接允許
- [ ] 附條件允許（條件：＿＿＿＿，例如限動態連結、保留 NOTICE、提供原始碼取得管道）
- [ ] 逐版本審查
- [ ] 禁止（需替換 Next.js 圖片最佳化方案）

---

## 案例二：LGPL-PSYCOPG2

| 項目 | 內容 |
|---|---|
| 授權 | LGPL-3 **加上連結例外條款** |
| 元件 | `psycopg2-binary` 2.9.12 |
| 依賴路徑 | `odayplus` → `dlt[postgres]` → `psycopg2-binary` |
| 用途 | dlt 資料載入工具使用的 PostgreSQL 驅動，production |

**性質**：上游宣告 LGPL-3 外加允許連結的例外條款。政策檔特別註明「例外條款的文字才是讓這件事可行的關鍵，應該實際讀過而不是假設」。

**請決定**：
- [ ] 確認上游例外條款可接受
- [ ] 要求改用其他授權的驅動
- [ ] 其他：＿＿＿＿

---

## 案例三：LGPL-PSYCOPG3 ← 四案中最需要判斷的一項

| 項目 | 內容 |
|---|---|
| 授權 | **LGPL-3.0-only（無連結例外）** |
| 元件 | `psycopg` 3.3.4、`psycopg-binary` 3.3.4、`psycopg-pool` 3.3.1 |
| 依賴路徑 | `odayplus` → `psycopg[binary,pool]`（`pyproject.toml:26`，**直接依賴**） |
| 用途 | production 主要的 PostgreSQL 驅動與連線池 |

**性質**：與案例二不同——psycopg 3.x **沒有**連結例外條款。它是 production Python runtime 動態載入的 LGPL-3.0 函式庫，需評估動態連結／C 擴充的使用方式是否符合貴公司對 LGPL-3.0 的政策（含使用者重新連結的權利）。

**請決定**：
- [ ] 附條件允許（條件：未修改上游二進位、動態連結、NOTICE 揭露、提供原始碼取得管道）
- [ ] 改用其他授權的驅動（例如 `asyncpg`，Apache-2.0）
- [ ] 禁止

> 若選擇更換驅動，影響範圍是 production 資料庫存取層，不是小改動，建議先評估再決定。

---

## 案例四：LGPL-MOOCORE

| 項目 | 內容 |
|---|---|
| 授權 | LGPL-2.1-or-later |
| 元件 | `moocore` 0.3.2 |
| 依賴路徑 | `odayplus` → `pymoo`（`pyproject.toml:44`）→ `moocore` |
| 用途 | pymoo 多目標最佳化函式庫的編譯 C/C++ 核心，production |

**性質**：由 pymoo 以編譯擴充模組形式動態載入。

**請決定**：
- [ ] 附條件允許（條件：動態連結／共享函式庫載入、未修改上游、保留 NOTICE）
- [ ] 更換多目標最佳化依賴
- [ ] 禁止

---

## 不需要您決定的部分

政策檔原本列了第五個案例 `FIRST-PARTY-UNLICENSED`（8 個自有 workspace 套件無授權欄位）。
該案**已由決策 D05 處理完畢**，八個 manifest 皆已標記 `UNLICENSED` 作為第一方識別標記，
2026-09-18 實測確認。此處僅供參考，不需要再次決定。

同時要說明：`UNLICENSED` 是第一方身分標記，不是允許的授權，也不授予任何人對我們原始碼的權利；
第三方元件若宣告 `UNLICENSED` 仍會被歸類為未知並 fail-closed。

---

## 簽署

- **決定人（具名）**: **蔡尚志**
- **職稱／授權角色**: 負責人
- **裁示日期**: 2026-09-18

### 四案決定：全部「附條件允許」

| 案例 | 元件 | 決定 |
|---|---|---|
| LGPL-SHARP-LIBVIPS | `@img/sharp-libvips-linux-x64`、`@img/sharp-libvips-linuxmusl-x64` | 附條件允許 |
| LGPL-PSYCOPG2 | `psycopg2-binary` | 附條件允許（確認上游連結例外條款可接受） |
| LGPL-PSYCOPG3 | `psycopg`、`psycopg-binary`、`psycopg-pool` | 附條件允許 |
| LGPL-MOOCORE | `moocore` | 附條件允許 |

### 四案共通條件

1. 僅以**未修改的上游二進位**形式使用，不對 LGPL 函式庫本身做任何修改
2. 僅以**動態連結／共享函式庫載入**方式使用，不靜態連結
3. 保留 **NOTICE 揭露**（`NOTICE-THIRD-PARTY.md`，由 `generate_oss_notice.py` 維護）
4. 提供**原始碼取得管道**

> **對案例三（psycopg 3.x）的特別註記**：該套件為 LGPL-3.0-only，**不含連結例外條款**，
> 與 psycopg2 不同。因此上述第 4 項「提供原始碼取得管道」對它是實質義務而非形式，
> 且 LGPL-3.0 對使用者重新連結（relinking）的要求需在散布形態改變時重新檢視。
> 本決定成立於「以未修改二進位動態連結於 production Python runtime」這個前提；
> 若日後改為靜態連結、修改上游、或將函式庫隨產品散布給第三方，本決定不再適用，須重新裁示。

### 條件失效情形

上述任一條件不成立時，本決定即失效，須重新評估：
- 對任一 LGPL 函式庫進行修改
- 改為靜態連結
- NOTICE 揭露遺失或未同步
- 散布形態改變（例如產品交付形式從服務改為可安裝套件）

---

> **具名來源**：由本機操作者於 2026-09-18 互動工作階段中提供決定內容與具名，
> Claude 代為謄錄，**非簽核人本人在此檔案上的數位簽署**。若後續閘門要求可驗證的
> 簽署憑據（principal ID、簽章或身分系統憑證），須由簽核人本人補齊，本檔不足以替代。

四案決定後，`docs/security/license_policy.json` 的 `status` 可由 `proposed` 轉為生效，
licence gate 即可通過。

> 另註：`docs/security/license_exemptions.json` 目前未簽署。若上述四案皆為「允許」或「附條件允許」，
> 則不需要建立任何例外條款——例外只在需要偏離政策時才使用。
