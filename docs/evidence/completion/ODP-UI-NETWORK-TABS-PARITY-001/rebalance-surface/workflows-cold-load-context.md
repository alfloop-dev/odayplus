# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: operator-network-rebalance.spec.ts >> ODP-OC-R4-008 Network Rebalance >> AVM + NetPlan workflow persists selected scenario and creates Govern approval without execution
- Location: tests/e2e/operator-network-rebalance.spec.ts:27:7

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByTestId('network-panel-rebalance')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByTestId('network-panel-rebalance')

```

```yaml
- banner:
  - text: O+
  - strong: Oday Plus
  - text: Operator Console E2E
  - navigation "Operator workspaces":
    - button "今日工作 Today"
    - button "門市營運 Store Ops"
    - button "營收成長 Growth"
    - button "展店與店網 Network"
    - button "治理稽核 Govern"
  - textbox "Global search":
    - /placeholder: 搜尋門市、案件
  - button "Open command palette": ⌘K
  - button "通知 0"
  - button "任務中心 0":
    - text: 任務中心
    - strong: "0"
  - button "待核准 0":
    - text: 待核准
    - strong: "0"
  - button "營運主管"
- strong: Local fixture mode
- text: 本機示範資料，僅供開發與測試使用。
- button "重設視角"
- main:
  - heading "展店與店網" [level=2]
  - paragraph: 找區域 → 物件收件 → 候選點 → SiteScore → 比較 → 審核；低效門市另走重配
  - strong: "0"
  - text: 今日新物件
  - strong: "4"
  - text: 進行中候選
  - strong: "2"
  - text: 待審 Review
  - strong: "1"
  - text: 重配候選
  - region "Network Golden Flow":
    - text: EXPANSION FLOW · 找點流程
    - strong: 目前步驟：店網管理
    - emphasis: 下一步：執行 SiteScore
    - button "找區域 Find Areas HZ-01 completed":
      - strong: 找區域
      - text: Find Areas HZ-01 completed
    - button "物件雷達 Listing Radar L-2024 completed":
      - strong: 物件雷達
      - text: Listing Radar L-2024 completed
    - button "候選點 Candidate CS-1001 current":
      - strong: 候選點
      - text: Candidate CS-1001 current
    - button "SiteScore SiteScore CS-1001 next":
      - strong: SiteScore
      - text: SiteScore CS-1001 next
    - button "比較 Compare CS-1001 next":
      - strong: 比較
      - text: Compare CS-1001 next
    - button "審核 Review RV-701 next":
      - strong: 審核
      - text: Review RV-701 next
    - text: 目前流程
    - button "找區域"
    - button "物件雷達"
    - button "候選點"
    - button "SiteScore"
    - button "比較"
    - button "審核"
  - tablist "Network tabs":
    - tab "找區域 Find Areas"
    - tab "物件雷達 Listing Radar"
    - tab "候選點 Candidates 4"
    - tab "SiteScore Score Lab"
    - tab "比較 Compare 2"
    - tab "審核 Review 2"
    - tab "低效重配 Rebalance 1" [selected]
    - tab "空間治理 Merge & Split"
  - status: 低效重配面板載入中…
- alert
```

# Test source

```ts
  1   | import { expect, request as playwrightRequest, test } from "@playwright/test";
  2   | 
  3   | const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
  4   | const NETWORK_HEADERS = {
  5   |   "x-subject-id": "operator-expansion-manager",
  6   |   "x-roles": "expansion_user",
  7   |   "x-operator-role": "expansion-staff",
  8   |   "x-tenant-id": "tenant-a",
  9   | };
  10  | const OPS_HEADERS = {
  11  |   "x-subject-id": "operator-ops-lead",
  12  |   "x-roles": "operations_manager",
  13  |   "x-operator-role": "ops-lead",
  14  |   "x-tenant-id": "tenant-a",
  15  | };
  16  | 
  17  | test.describe.configure({ mode: "serial" });
  18  | 
  19  | test.describe("ODP-OC-R4-008 Network Rebalance", () => {
  20  |   test.beforeEach(async () => {
  21  |     const api = await apiContext(NETWORK_HEADERS);
  22  |     const reset = await api.post("/api/v1/operator/network-rebalance/reset");
  23  |     expect(reset.status()).toBe(200);
  24  |     await api.dispose();
  25  |   });
  26  | 
  27  |   test("AVM + NetPlan workflow persists selected scenario and creates Govern approval without execution", async ({
  28  |     page,
  29  |   }) => {
  30  |     await page.goto("/operator?ws=network");
  31  |     await expect(
  32  |       page.getByTestId("network-find-areas-workspace"),
  33  |     ).toBeVisible();
  34  | 
  35  |     await page.getByTestId("network-tab-6").click();
> 36  |     await expect(page.getByTestId("network-panel-rebalance")).toBeVisible();
      |                                                               ^ Error: expect(locator).toBeVisible() failed
  37  |     await expect(page.getByTestId("rebalance-card-RB-801")).toContainText(
  38  |       "新北板橋文化",
  39  |     );
  40  |     await expect(page.getByTestId("rebalance-primary-action")).toContainText(
  41  |       "建立 AVM 估值請求",
  42  |       { timeout: 15_000 },
  43  |     );
  44  |     await expect(page.getByTestId("rebalance-boundary-RB-801")).toHaveAttribute(
  45  |       "data-relocation-executed",
  46  |       "false",
  47  |     );
  48  |     await expect(page.getByTestId("rebalance-boundary-RB-801")).toContainText("尚未執行搬遷");
  49  | 
  50  |     await page.getByTestId("rebalance-primary-action").click();
  51  |     await expect(page.getByTestId("rebalance-primary-action")).toContainText(
  52  |       "完成 AVM job",
  53  |     );
  54  | 
  55  |     await page.getByTestId("rebalance-primary-action").click();
  56  |     await expect(page.getByTestId("rebalance-avm-RB-801")).toBeVisible();
  57  |     await expect(page.getByTestId("rebalance-avm-RB-801")).toContainText(
  58  |       "service output",
  59  |     );
  60  |     await expect(page.getByTestId("rebalance-avm-RB-801")).toContainText(
  61  |       "avm-rebalance-income-market-v1.0.0",
  62  |     );
  63  |     await expect(page.getByTestId("rebalance-avm-RB-801")).toContainText(
  64  |       "AVM-SNAP-20260714-0600",
  65  |     );
  66  |     await expect(page.getByTestId("rebalance-primary-action")).toContainText(
  67  |       "建立 NetPlan Review",
  68  |     );
  69  | 
  70  |     await page.getByTestId("rebalance-primary-action").click();
  71  |     await expect(page.getByTestId("rebalance-netplan-RB-801")).toBeVisible();
  72  |     await expect(page.getByTestId("rebalance-scenario-keep")).toContainText(
  73  |       "Keep / Improve",
  74  |     );
  75  |     await expect(page.getByTestId("rebalance-scenario-move")).toContainText(
  76  |       "Move (移轉新址)",
  77  |     );
  78  |     await expect(page.getByTestId("rebalance-scenario-move")).toContainText(
  79  |       "系統建議",
  80  |     );
  81  |     await expect(page.getByTestId("rebalance-scenario-exit")).toContainText(
  82  |       "Exit (關店止損)",
  83  |     );
  84  |     await expect(page.getByTestId("rebalance-scenario-move")).toContainText(
  85  |       "NP-SNAP-20260714-0615",
  86  |     );
  87  | 
  88  |     await page.getByTestId("rebalance-scenario-move").click();
  89  |     await expect(page.getByTestId("rebalance-selection-RB-801")).toContainText(
  90  |       "Selected: Move (移轉新址)",
  91  |     );
  92  |     await expect(page.getByTestId("rebalance-selection-RB-801")).toContainText(
  93  |       "Owner Expansion Manager",
  94  |     );
  95  |     await expect(page.getByTestId("rebalance-selection-RB-801")).toContainText(
  96  |       "EV-SEL-",
  97  |     );
  98  |     // The fixture scenarios were never solved against a construction, equipment,
  99  |     // labour, coverage or dilution cap, and this surface has no NetPlan
  100 |     // disclosure policy registered, so every unmodelled class is treated as
  101 |     // blocking and the journey stops here (ODP-FR-NET-002). Until
  102 |     // ODP-NETPLAN-DISCLOSURE-UI-E2E-001 this reached Govern: the console
  103 |     // displayed the disclosure and then submitted anyway.
  104 |     await expect(page.getByTestId("rebalance-blocked-alert")).toContainText(
  105 |       "CONSTRUCTION",
  106 |     );
  107 |     // No acknowledgement form is offered for a plan the server would refuse.
  108 |     await expect(
  109 |       page.getByTestId("rebalance-acknowledgement-section"),
  110 |     ).toHaveCount(0);
  111 |     await expect(page.getByTestId("rebalance-primary-action")).toContainText(
  112 |       "送審（無法送審）",
  113 |     );
  114 |     await expect(page.getByTestId("rebalance-primary-action")).toBeDisabled();
  115 |     await expect(page.getByTestId("rebalance-boundary-RB-801")).toHaveAttribute(
  116 |       "data-relocation-executed",
  117 |       "false",
  118 |     );
  119 |     await expect(page.getByTestId("rebalance-boundary-RB-801")).toContainText("尚未執行搬遷");
  120 | 
  121 |     await page.reload();
  122 |     await expect(
  123 |       page.getByTestId("network-find-areas-workspace"),
  124 |     ).toBeVisible();
  125 |     await page.getByTestId("network-tab-6").click();
  126 |     await expect(page.getByTestId("rebalance-primary-action")).toContainText(
  127 |       "送審（無法送審）",
  128 |       { timeout: 15_000 },
  129 |     );
  130 |     await expect(page.getByTestId("rebalance-selection-RB-801")).toContainText(
  131 |       "Owner Expansion Manager",
  132 |     );
  133 |     await expect(page.getByTestId("rebalance-selection-RB-801")).toContainText(
  134 |       "EV-SEL-",
  135 |     );
  136 | 
```