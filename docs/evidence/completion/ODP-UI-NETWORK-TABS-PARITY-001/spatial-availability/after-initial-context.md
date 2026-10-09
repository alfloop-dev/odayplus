# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: operator-spatial-surface-parity.spec.ts >> Spatial list availability and retry at 390
- Location: tests/visual/operator-spatial-surface-parity.spec.ts:19:7

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByTestId('heatzone-merge-split-panel').getByTestId('proposal-read-error')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByTestId('heatzone-merge-split-panel').getByTestId('proposal-read-error')

```

```yaml
- banner:
  - text: O+
  - strong: Oday Plus
  - text: Operator Console E2E
  - navigation "Operator workspaces":
    - button "今日工作 Today"
    - button "門市營運 Store Ops" [disabled]
    - button "營收成長 Growth" [disabled]
    - button "展店與店網 Network"
    - button "治理稽核 Govern"
  - textbox "Global search":
    - /placeholder: 搜尋門市、案件
  - button "Open command palette": ⌘K
  - button "通知 2"
  - button "任務中心 3":
    - text: 任務中心
    - strong: "3"
  - button "待核准 1":
    - text: 待核准
    - strong: "1"
  - button "展店經理"
- strong: Local fixture mode
- text: 本機示範資料，僅供開發與測試使用。
- button "重設視角"
- main:
  - heading "展店與店網" [level=2]
  - paragraph: 找區域 → 物件收件 → 候選點 → SiteScore → 比較 → 審核；低效門市另走重配
  - strong: "1"
  - text: 今日新物件
  - strong: "4"
  - text: 進行中候選
  - strong: "3"
  - text: 待審 Review
  - strong: "1"
  - text: 重配候選
  - region "Network Golden Flow":
    - text: EXPANSION FLOW · 找點流程
    - strong: 目前步驟：店網管理
    - emphasis: 下一步：Convert L-2024 to create CS-1001.
    - button "找區域 Find Areas HZ-01 completed":
      - strong: 找區域
      - text: Find Areas HZ-01 completed
    - button "物件雷達 Listing Radar L-2024 current":
      - strong: 物件雷達
      - text: Listing Radar L-2024 current
    - button "候選點 Candidate L-2024 next":
      - strong: 候選點
      - text: Candidate L-2024 next
    - button "SiteScore SiteScore 缺資料 blocked" [disabled]:
      - strong: SiteScore
      - text: SiteScore 缺資料 blocked
    - button "比較 Compare 缺資料 blocked" [disabled]:
      - strong: 比較
      - text: Compare 缺資料 blocked
    - button "審核 Review 缺資料 blocked" [disabled]:
      - strong: 審核
      - text: Review 缺資料 blocked
    - text: 目前流程
    - button "找區域"
    - button "物件雷達"
    - button "候選點"
    - button "SiteScore" [disabled]
    - button "比較" [disabled]
    - button "審核" [disabled]
  - tablist "Network tabs":
    - tab "找區域 Find Areas"
    - tab "物件雷達 Listing Radar 1"
    - tab "候選點 Candidates 4"
    - tab "SiteScore Score Lab"
    - tab "比較 Compare 3"
    - tab "審核 Review 3"
    - tab "低效重配 Rebalance 1"
    - tab "空間治理 Merge & Split" [selected]
  - heading "熱區合併／拆分" [level=3]
  - paragraph: 依據 HZ-004 實績吸收證據、空間相關性及邊界異質性自動產生之熱區拓撲變更提案。
  - combobox "提案狀態篩選" [disabled]:
    - option "全部提案（尚未確認）" [selected]
    - option "待審批 (PROPOSED)"
    - option "已核准 (APPROVED)"
    - option "已拒絕 (REJECTED)"
  - status: 正在載入熱區合併／拆分提案數據…
- alert
```

# Test source

```ts
  1   | import AxeBuilder from "@axe-core/playwright";
  2   | import { expect, test } from "@playwright/test";
  3   | import { mkdir, writeFile } from "node:fs/promises";
  4   | import path from "node:path";
  5   | import { pathToFileURL } from "node:url";
  6   | 
  7   | // Controlled read-model render coverage, NOT a durable-write or live-data receipt.
  8   | const proposal = {
  9   |   proposal_id: "11111111-2222-3333-4444-555555555555", zone_id: "MZ-0123456789abcdef",
  10  |   tenant_id: "tenant-a", composition_kind: "MERGED", member_cell_ids: ["cell-1", "cell-2"], member_count: 2,
  11  |   ndcg_gain: 0, cannibalization_variance_reduction: 0.24, correlation_rho: 0.88, disconnect_index: 0.12,
  12  |   confidence: 0.88, model_version: "heatzone-composition-v1", policy_version_id: "heatzone-merge-v1:tenant-a",
  13  |   status: "PROPOSED", reasons: ["adjacent_high_demand_correlation", "continuous_spatial_absorption"],
  14  |   warnings: ["Requires independent boundary review"], created_at: "2026-09-03T12:00:00Z",
  15  | };
  16  | const phase = process.env.NETWORK_PARITY_CAPTURE_PHASE ?? "after";
  17  | 
  18  | for (const width of [1440, 390]) {
  19  |   test(`Spatial list availability and retry at ${width}`, async ({ page }, info) => {
  20  |     test.setTimeout(120_000);
  21  |     await page.setViewportSize({ width, height: 900 });
  22  |     await page.addInitScript(() => {
  23  |       sessionStorage.setItem("oday.operator.role", "expansion-manager");
  24  |       sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
  25  |       sessionStorage.setItem("oday.operator.tenant", "tenant-a");
  26  |     });
  27  |     const directory = process.env.NETWORK_PARITY_EVIDENCE_DIR ?? info.outputDir;
  28  |     await mkdir(directory, { recursive: true });
  29  |     let release!: () => void;
  30  |     let response: { status: number; json: unknown } = { status: 500, json: { detail: "controlled read failure" } };
  31  |     let received = 0;
  32  |     await page.route("**/api/v1/heatzones/merge-split/proposals", async (route) => {
  33  |       received += 1;
  34  |       await new Promise<void>((resolve) => { release = resolve; });
  35  |       await route.fulfill(response);
  36  |     });
  37  |     await page.goto("/operator?ws=network&tab=composition");
  38  |     await page.getByTestId("network-tab-7").click();
  39  |     const panel = page.getByTestId("heatzone-merge-split-panel");
  40  |     await expect(panel).toBeVisible({ timeout: 30_000 });
  41  |     await expect.poll(() => received).toBeGreaterThan(0);
  42  |     async function capture(state: string) {
  43  |       await page.evaluate(() => scrollTo(0, 0));
  44  |       const boxes = await panel.evaluate((el) => {
  45  |         const r = el.getBoundingClientRect();
  46  |         return { x: r.x, width: r.width, scrollWidth: el.scrollWidth, documentWidth: document.documentElement.scrollWidth };
  47  |       });
  48  |       const axe = await new AxeBuilder({ page }).include('[data-testid="heatzone-merge-split-panel"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  49  |       await writeFile(path.join(directory, `${phase}-${state}-geometry-${width}.json`), JSON.stringify(boxes, null, 2));
  50  |       await writeFile(path.join(directory, `${phase}-${state}-axe-${width}.json`), JSON.stringify(axe, null, 2));
  51  |       await page.screenshot({ path: path.join(directory, `${phase}-${state}-${width}.png`), fullPage: true, animations: "disabled" });
  52  |       if (phase === "after") {
  53  |         expect(boxes.x).toBeGreaterThanOrEqual(0);
  54  |         expect(boxes.x + boxes.width).toBeLessThanOrEqual(width);
  55  |         expect(boxes.scrollWidth).toBeLessThanOrEqual(Math.ceil(boxes.width));
  56  |         expect(boxes.documentWidth).toBeLessThanOrEqual(width);
  57  |         expect(axe.violations).toEqual([]);
  58  |       }
  59  |     }
  60  |     if (phase === "after") {
  61  |       await expect(panel.getByTestId("loading-proposals")).toBeVisible();
  62  |       await expect(panel.getByTestId("empty-proposals")).toHaveCount(0);
  63  |       await expect(panel.getByTestId("proposal-status-filter")).toBeDisabled();
  64  |     }
  65  |     await capture("list-pending");
  66  |     const completed = page.waitForResponse("**/api/v1/heatzones/merge-split/proposals");
  67  |     release();
  68  |     await completed;
  69  |     if (phase === "after") await expect(panel.getByTestId("proposal-read-error")).toBeVisible();
  70  |     else await expect(panel.getByTestId("empty-proposals")).toBeVisible();
  71  |     await capture("list-failed-500");
  72  |     if (phase === "after") {
  73  |       await expect(panel.getByTestId("empty-proposals")).toHaveCount(0);
  74  |       await expect(panel.getByTestId("proposal-detail")).toHaveCount(0);
  75  |       const count = received;
  76  |       await panel.getByRole("button", { name: "重新載入提案" }).click();
  77  |       await expect.poll(() => received).toBeGreaterThan(count);
  78  |       await expect(panel.getByTestId("loading-proposals")).toBeVisible();
  79  |       response = { status: 200, json: { items: [proposal] } };
  80  |       release();
  81  |       await expect(panel.getByTestId("proposal-detail")).toContainText(proposal.zone_id);
  82  |       await capture("list-recovered");
  83  |     }
  84  |     // Controlled negative reads, not authorization grants or backend write proof.
  85  |     for (const [state, next] of [
  86  |       ["list-denied-403", { status: 403, json: { detail: "controlled scope denial" } }],
  87  |       ["list-malformed", { status: 200, json: { not_items: [] } }],
  88  |     ] as const) {
  89  |       response = next;
  90  |       const count = received;
  91  |       await page.reload();
  92  |       await page.getByTestId("network-tab-7").click();
  93  |       await expect.poll(() => received).toBeGreaterThan(count);
  94  |       const done = page.waitForResponse("**/api/v1/heatzones/merge-split/proposals");
  95  |       release();
  96  |       await done;
> 97  |       if (phase === "after") await expect(panel.getByTestId("proposal-read-error")).toBeVisible();
      |                                                                                     ^ Error: expect(locator).toBeVisible() failed
  98  |       else await expect(panel.getByTestId("empty-proposals")).toBeVisible();
  99  |       await capture(state);
  100 |     }
  101 |   });
  102 | }
  103 | 
  104 | for (const width of [1440, 1024, 390]) {
  105 |   test(`Spatial later-spec integration and empty state at ${width}`, async ({ page }, info) => {
  106 |     test.setTimeout(120_000);
  107 |     await page.setViewportSize({ width, height: 900 });
  108 |     await page.addInitScript(() => {
  109 |       sessionStorage.setItem("oday.operator.role", "expansion-manager");
  110 |       sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
  111 |       sessionStorage.setItem("oday.operator.tenant", "tenant-a");
  112 |     });
  113 |     const directory = process.env.NETWORK_PARITY_EVIDENCE_DIR ?? info.outputDir;
  114 |     await mkdir(directory, { recursive: true });
  115 |     async function save(name: string, value: unknown) { await writeFile(path.join(directory, name), JSON.stringify(value, null, 2)); }
  116 |     async function shot(target: typeof page, name: string) {
  117 |       await target.evaluate(() => scrollTo(0, 0));
  118 |       const clip = await target.evaluate(() => ({ x: 0, y: 0, width: innerWidth, height: document.documentElement.scrollHeight }));
  119 |       await target.screenshot({ path: path.join(directory, name), fullPage: true, clip, animations: "disabled" });
  120 |     }
  121 |     if (process.env.NETWORK_PARITY_DESIGN === "1" && width !== 1024) {
  122 |       const design = await page.context().newPage();
  123 |       await design.setViewportSize({ width, height: 900 });
  124 |       await design.route(/^https?:\/\//, (route) => route.abort());
  125 |       await design.goto(pathToFileURL(path.resolve("docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html")).href, { waitUntil: "domcontentloaded" });
  126 |       await design.getByRole("button", { name: /林.*營運主管/ }).click();
  127 |       await design.getByRole("button", { name: /展店經理.*林曉青/ }).click();
  128 |       await design.getByRole("button", { name: /展店與店網.*Network/ }).click();
  129 |       await expect(design.getByText(/已切換 Demo 角色/)).toBeHidden({ timeout: 15_000 });
  130 |       // Spatial/HZ-006 is a later-spec addition; never invent a reference screen.
  131 |       await expect(design.getByRole("button", { name: /空間治理|Merge & Split|Spatial/ })).toHaveCount(0);
  132 |       await shot(design, `design-network-no-spatial-${width}.png`);
  133 |       await save(`design-absence-${width}.json`, { spatialControls: 0, reference: "Package 10 Network landing, not a Spatial reference", width });
  134 |       await design.close();
  135 |     }
  136 |     let populated = false;
  137 |     await page.route("**/api/v1/heatzones/merge-split/proposals", (route) => route.fulfill({ json: { items: populated ? [proposal] : [] } }));
  138 |     await page.goto("/operator?ws=network");
  139 |     await page.getByTestId("network-tab-7").click();
  140 |     const panel = page.getByTestId("heatzone-merge-split-panel");
  141 |     await expect(panel.getByTestId("empty-proposals")).toBeVisible({ timeout: 30_000 });
  142 |     async function capture(state: string) {
  143 |       const boxes = await panel.evaluate((el) => {
  144 |         const measure = (node: Element) => {
  145 |           const r = node.getBoundingClientRect();
  146 |           return { x: r.x, y: r.y, width: r.width, height: r.height, scrollWidth: node.scrollWidth };
  147 |         };
  148 |         return { panel: measure(el), children: [...el.querySelectorAll('[data-testid="proposal-list"], [data-testid="proposal-detail"]')].map(measure), documentWidth: document.documentElement.scrollWidth };
  149 |       });
  150 |       await save(`${phase}-${state}-geometry-${width}.json`, boxes);
  151 |       await shot(page, `${phase}-${state}-${width}.png`);
  152 |       const axe = await new AxeBuilder({ page }).include('[data-testid="heatzone-merge-split-panel"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  153 |       await save(`${phase}-${state}-axe-${width}.json`, axe);
  154 |       if (phase !== "after") return;
  155 |       expect(boxes.documentWidth).toBeLessThanOrEqual(width);
  156 |       for (const box of [boxes.panel, ...boxes.children]) {
  157 |         expect(box.x).toBeGreaterThanOrEqual(0);
  158 |         expect(box.x + box.width).toBeLessThanOrEqual(width);
  159 |         expect(box.scrollWidth).toBeLessThanOrEqual(Math.ceil(box.width));
  160 |       }
  161 |       if (boxes.children.length && width === 390) expect(boxes.children[1].y).toBeGreaterThanOrEqual(boxes.children[0].y + boxes.children[0].height);
  162 |       expect(axe.violations).toEqual([]);
  163 |     }
  164 |     await capture("empty");
  165 |     populated = true;
  166 |     await page.reload();
  167 |     await page.getByTestId("network-tab-7").click();
  168 |     await expect(panel.getByTestId("proposal-detail")).toContainText(proposal.zone_id, { timeout: 30_000 });
  169 |     await capture("proposal");
  170 |     if (phase === "after") {
  171 |       const row = panel.getByTestId(`proposal-item-${proposal.proposal_id}`);
  172 |       await row.focus();
  173 |       await page.keyboard.press("Enter");
  174 |       await expect(row).toHaveAttribute("aria-pressed", "true");
  175 |       await expect(panel.getByTestId("proposal-detail")).toContainText(proposal.warnings[0]);
  176 |       await expect(panel.getByTestId("proposal-detail")).toContainText("+0.00%");
  177 |       await panel.getByTestId("proposal-status-filter").selectOption("REJECTED");
  178 |       await expect(panel.getByTestId("empty-proposals")).toBeVisible();
  179 |       await expect(panel.getByTestId("proposal-detail")).toHaveCount(0);
  180 |     }
  181 |   });
  182 | }
  183 | 
```