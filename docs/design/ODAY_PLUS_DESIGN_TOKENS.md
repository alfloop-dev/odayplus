---
doc_id: ODP-R0-DESIGN-TOKENS
title: "ODay Plus Design Tokens"
version: 0.1.0
status: draft
document_class: design-system
project: ODay Plus
language: zh-TW
updated_at: 2026-06-26
owner: "Product Design / Frontend"
approvers: "Product Lead / Frontend Lead"
content_format: markdown
source_documents:
  - ODP-UX-02_DESIGN_SYSTEM.md
  - ODP-UX-04_MAP_AND_DATA_VISUALIZATION_SPECIFICATION.md
  - ODP-UX-05_FRONTEND_TECHNICAL_DESIGN.md
related_documents:
  - docs/design/ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md
  - docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md
---

# ODay Plus Design Tokens

## 1. Purpose & Rules

本文件是 ODay Plus 視覺系統的**唯一 token 值來源**。`ODP-UX-02` 定義了 token 命名，本文件補上**具體值**，使前端可直接建立 `packages/design-tokens`。

規則（normative）：

- 元件**不得硬編色碼、字級、間距、圓角、陰影、z-index、動畫時間**；一律引用 semantic token（見 `ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md §10.1`）。
- Token 分兩層：**primitive（原始值，不直接用於元件）** 與 **semantic（語意別名，元件只用這層）**。元件只引用 semantic 與 component-scope token。
- Theme（light/dark/high-contrast/presentation）是對 **semantic** 層的值覆寫；primitive 不隨 theme 改變。
- 所有值以本文件為準；新增/變更 token 需走 §13 governance。

### 1.1 命名與輸出格式

- 規格命名以 dot-notation（`color.status.green`）。
- CSS 變數：`--odp-color-status-green`。
- TS token 物件：`tokens.color.status.green`。
- 三種表示法一一對應，由 `packages/design-tokens` 的 build step 產生，不手抄。

---

## 2. Primitive Palette

primitive 色階為 50–900。**元件禁止直接使用 primitive**，僅供 semantic 層引用。值為建議基準，theme build 時可微調但語意不變。

### 2.1 Neutral（介面主體）

```text
neutral.0   #FFFFFF
neutral.50  #F8FAFC
neutral.100 #F1F5F9
neutral.200 #E2E8F0
neutral.300 #CBD5E1
neutral.400 #94A3B8
neutral.500 #64748B
neutral.600 #475569
neutral.700 #334155
neutral.800 #1E293B
neutral.900 #0F172A
```

### 2.2 語意色家族（primitive）

```text
green.100  #DCFCE7   green.500  #16A34A   green.700  #15803D
yellow.100 #FEF9C3   yellow.500 #CA8A04   yellow.700 #A16207
orange.100 #FFEDD5   orange.500 #EA580C   orange.700 #C2410C
red.100    #FEE2E2   red.500    #DC2626   red.700    #B91C1C
blue.100   #DBEAFE   blue.500   #2563EB   blue.700   #1D4ED8
purple.100 #EDE9FE   purple.500 #7C3AED   purple.700 #6D28D9
gray.100   #F1F5F9   gray.500   #64748B   gray.700   #334155
```

設計取向：中低飽和、偏冷的工程色盤，符合「calm / 沉穩」個性。語意色保留給狀態/風險/模型/地圖，不用於 chrome。

---

## 3. Semantic Color Tokens

元件**只用**這一層。下表給 `light` theme 值；其他 theme 在 §12 覆寫。

### 3.1 Background

```text
color.bg.canvas        neutral.50    應用最底層畫布
color.bg.surface       neutral.0     卡片 / 面板表面
color.bg.muted         neutral.100   次要區塊 / 表頭
color.bg.inset         neutral.100   輸入框 / well
color.bg.success-soft  green.100     成功軟底
color.bg.warning-soft  yellow.100    注意軟底
color.bg.danger-soft   red.100       危險軟底
color.bg.info-soft     blue.100      資訊軟底
color.bg.model-soft    purple.100    模型相關軟底
color.bg.overlay       rgba(15,23,42,0.48)  modal/backdrop
```

### 3.2 Text

```text
color.text.primary    neutral.900
color.text.secondary  neutral.600
color.text.muted      neutral.400
color.text.inverse    neutral.0
color.text.link       blue.700
color.text.success    green.700
color.text.warning    yellow.700
color.text.danger     red.700
color.text.info       blue.700
color.text.model      purple.700
```

### 3.3 Border

```text
color.border.default  neutral.200
color.border.strong   neutral.300
color.border.focus    blue.500     focus ring，2px outline
color.border.danger   red.500
color.border.warning  yellow.500
color.border.success  green.500
```

### 3.4 Status（綁 §6 狀態語言）

```text
color.status.green   green.500     正常 / 達標 / 低風險
color.status.yellow  yellow.500    輕微偏離 / 需注意
color.status.orange  orange.500    高風險 / 需處置
color.status.red     red.500       危急 / 阻擋 / 重大失敗
color.status.gray    gray.500      未啟用 / 無資料 / 草稿
color.status.blue    blue.500      資訊 / 進行中
color.status.purple  purple.500    模型 / AI 相關
```

每個 status 另有 `*-soft`（軟底，= 對應 `bg.*-soft`）與 `*-on`（其上文字色，確保對比 ≥ 4.5:1）。

### 3.5 Model stage

```text
color.model.production  purple.700   穩定上線
color.model.candidate   purple.500   候選 / challenger
color.model.shadow      blue.500     shadow 評估中
color.model.canary      blue.700     canary 漸進放量
color.model.rollback    red.500      回滾
```

### 3.6 Risk scale（用於風險梯度，獨立於四燈）

```text
color.risk.low      green.500
color.risk.medium   yellow.500
color.risk.high     orange.500
color.risk.critical red.500
```

### 3.7 Map tokens

```text
color.map.heat.low     #FEF3C7   color.map.heat.medium  #FB923C   color.map.heat.high    #B91C1C
color.map.risk.low     #16A34A   color.map.risk.medium  #EA580C   color.map.risk.high    #DC2626
color.map.selected     blue.500           選取 cell 強邊框
color.map.stale-overlay rgba(100,116,139,0.35)  過期資料遮罩
color.map.cluster      neutral.700        cluster 標記底
```

地圖 sequential heat scale 至少 5 階（low→high），實作以 `color.map.heat.*` 為錨點插值；風險梯度同理。地圖另有獨立 basemap theme（light/dark/minimal/print_safe），預設 minimal。

---

## 4. Typography Tokens

### 4.1 Families

```text
font.family.sans  "Inter", "Noto Sans TC", system-ui, -apple-system, "Segoe UI", sans-serif
font.family.mono  "JetBrains Mono", "Roboto Mono", "Noto Sans Mono", ui-monospace, monospace
```

`sans` 含 Noto Sans TC 以正確呈現繁中。`mono` 用於 IDs、model version、code、event name、數值對齊。

### 4.2 Size scale（rem，基準 16px = 1rem）

```text
font.size.xs    0.75rem   (12px)
font.size.sm    0.875rem  (14px)
font.size.md    1rem      (16px)   ← body 預設
font.size.lg    1.125rem  (18px)
font.size.xl    1.25rem   (20px)
font.size.2xl   1.5rem    (24px)
font.size.3xl   1.875rem  (30px)
```

### 4.3 Weight

```text
font.weight.regular   400
font.weight.medium    500
font.weight.semibold  600
font.weight.bold      700
```

### 4.4 Line-height

```text
line-height.compact  1.25
line-height.normal   1.5
line-height.relaxed  1.7
```

### 4.5 Text roles（語意層級 → token 組合）

| Role | size / weight / line-height | 用途 |
|---|---|---|
| Display | 3xl / bold / compact | Executive summary、major KPI |
| H1 | 2xl / semibold / compact | Page title |
| H2 | xl / semibold / normal | Section title |
| H3 | lg / semibold / normal | Card title |
| Body | md / regular / normal | 一般說明 |
| Body-sm | sm / regular / normal | 表格內文、次要說明 |
| Caption | xs / regular / normal | metadata、timestamp |
| Mono | sm / regular / normal · mono family | IDs、model version、event name |

`presentation` density 對 Display/H1/H2 各放大一階（見 §9）。

---

## 5. Spacing Tokens

基準 4px scale：

```text
space.0   0px
space.1   4px
space.2   8px
space.3   12px
space.4   16px
space.6   24px
space.8   32px
space.12  48px
space.16  64px
```

用法：卡片內 padding 預設 `space.4`；section 間距 `space.8`；inline gap `space.2`；表格 cell 內距由 density 決定（§9）。所有 padding/margin/gap **只能**用這些值。

---

## 6. Radius Tokens

```text
radius.none  0px
radius.sm    4px    badge、chip、input
radius.md    8px    button、card、drawer
radius.lg    12px   modal、大型 panel
radius.xl    16px   特殊容器
radius.full  9999px pill badge、avatar、四燈圓點
```

---

## 7. Elevation / Shadow Tokens

```text
elevation.none      none
elevation.card      0 1px 2px rgba(15,23,42,0.06), 0 1px 3px rgba(15,23,42,0.10)
elevation.dropdown  0 4px 8px rgba(15,23,42,0.10), 0 2px 4px rgba(15,23,42,0.06)
elevation.drawer    -8px 0 24px rgba(15,23,42,0.12)
elevation.modal     0 20px 48px rgba(15,23,42,0.24)
elevation.toast     0 8px 24px rgba(15,23,42,0.18)
```

elevation 只表達「浮起/聚焦」，不裝飾。dark theme 改用較低不透明、較深的陰影或以 border 取代（見 §12）。

---

## 8. Z-index Tokens

```text
z.base             0
z.sticky           100    sticky header / table header
z.dropdown         1000
z.drawer           1100
z.modal            1300
z.toast            1400
z.command-palette  1500
```

層級嚴格遞增；command palette 永遠在最上。新 overlay 必須登記到此表，不得用任意數字。

---

## 9. Density Tokens

density 是一組「尺寸覆寫」，作用於行高、cell padding、控制元件高度與部分字級。**不改語意色、不改資訊層級。**

| Token | comfortable | compact | presentation |
|---|---|---|---|
| `density.row-height` | 44px | 36px | 56px |
| `density.cell-padding-y` | `space.3` (12px) | `space.2` (8px) | `space.4` (16px) |
| `density.cell-padding-x` | `space.4` (16px) | `space.3` (12px) | `space.4` (16px) |
| `density.control-height` | 40px | 32px | 48px |
| `density.card-padding` | `space.4` (16px) | `space.3` (12px) | `space.6` (24px) |
| `density.font-scale` | 1.0 | 1.0 | 1.125 |
| `density.heading-bump` | 0 | 0 | +1 step (Display/H1/H2) |

預設：多數工作頁 `comfortable`；列表/收件匣/audit log `compact`；Executive 與簡報模式 `presentation`。density 由使用者偏好 + 頁面預設決定，存於 local UI state。

---

## 10. Breakpoint & Layout Tokens

### 10.1 Breakpoints

```text
breakpoint.sm   640px    mobile
breakpoint.md   768px    tablet
breakpoint.lg   1024px   desktop（完整功能保證起點）
breakpoint.xl   1280px   large desktop
breakpoint.2xl  1536px   command center / wall screen（預設 presentation 密度）
```

### 10.2 Layout

```text
layout.sidebar-width          264px
layout.sidebar-collapsed       64px
layout.header-height           56px
layout.drawer-width           420px
layout.drawer-width-wide      560px
layout.readable-max           768px   純閱讀型報告區塊最大寬
layout.content-gutter        space.6  content 左右留白
layout.filter-bar-height       52px
```

工作頁 content 不設硬上限（充分利用寬螢幕做表格/地圖）；報告區塊套 `layout.readable-max`。

---

## 11. Motion Tokens

```text
motion.duration.instant   80ms    hover / press 回饋
motion.duration.fast     160ms    drawer / dropdown 開合
motion.duration.normal   240ms    page transition / fade
motion.duration.slow     360ms    map layer cross-fade
motion.easing.standard   cubic-bezier(0.2, 0, 0, 1)
motion.easing.emphasized cubic-bezier(0.2, 0, 0, 1)
motion.easing.exit       cubic-bezier(0.4, 0, 1, 1)
```

規則：critical alert **不使用閃爍**；loading 用 skeleton / stage indicator；尊重 `prefers-reduced-motion`（reduced 時 duration → instant、停用 cross-fade）。

---

## 12. Theme Overrides

theme 覆寫 **semantic** 層。下列只列關鍵差異；未列者沿用 light。

### 12.1 dark

```text
color.bg.canvas        neutral.900
color.bg.surface       neutral.800
color.bg.muted         neutral.700
color.text.primary     neutral.50
color.text.secondary   neutral.300
color.text.muted       neutral.400
color.border.default   neutral.700
color.border.strong    neutral.600
color.bg.overlay       rgba(0,0,0,0.64)
status/risk/model      使用 *.500 但搭較深 soft 底（*.700 的 20% alpha）
elevation.*            降低 alpha，必要時以 border.strong 取代陰影
```

### 12.2 high-contrast

```text
color.text.primary     #000000 (light) / #FFFFFF (dark base)
color.border.default   neutral.500（加粗為 2px）
color.border.focus     2px solid + 2px offset，對比 ≥ 3:1
status colours         改用 *.700 以拉高對比；軟底改用 *.100 + 1px *.500 邊框
所有互動元件            最小對比 ≥ 7:1（WCAG AAA 目標）
```

### 12.3 presentation

沿用 light 色，套 §9 `presentation` density（放大字級與間距、heading bump），用於會議投影/wall screen。

---

## 13. Token Governance

- 生命週期：`PROPOSED → EXPERIMENTAL → APPROVED → DEPRECATED → REMOVED`。
- 新 token 先登記於本文件（含值、語意、適用 theme）才可在產品碼使用。
- **Breaking change**（改既有 token 的語意值、刪 token、改命名）需建 ADR 並列出受影響頁面/元件（見 `ODP-UX-02 §14.3`）。
- 缺 token 時：先在本文件新增 semantic token，再實作；**不得在元件就地硬編**。
- `packages/design-tokens` 為唯一輸出來源，CSS 變數 / TS 物件由本文件 build 而來，不手抄、不分叉。

---

## 14. 驗收條件

- color / typography / spacing / radius / elevation / z-index / density / breakpoint / layout / motion 皆有**具體值**，非僅命名。
- status / risk / confidence / model / map token 與 `ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md §6` 的狀態語言一致。
- primitive 與 semantic 兩層分離，元件只用 semantic。
- light / dark / high-contrast / presentation 四 theme 的覆寫規則明確，對比度達 WCAG AA（high-contrast 目標 AAA）。
- 可由 `packages/design-tokens` 直接產生 CSS 變數與 TS token 物件，並支援視覺回歸測試。
