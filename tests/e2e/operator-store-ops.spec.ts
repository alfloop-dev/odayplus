import { expect, test, type Page } from "@playwright/test";

type StoreLightStatus = "green" | "yellow" | "red";
type StoreOpsIssue = {
  id: string;
  title: string;
  storeId: string;
  storeName: string;
  status: string;
  severity: string;
  source: string;
  ownerRoleId: string;
  ownerName: string;
  slaDueAt: string;
  createdAt: string;
  updatedAt: string;
  evidenceIds: string[];
  summary: string;
};

const stores = [
  {
    id: "ST-008",
    name: "台北信義 A11",
    district: "Xinyi",
    city: "Taipei",
    manager: "Mina Chen",
    lights: { demand: "green", operations: "red", staffing: "yellow", margin: "yellow" },
    riskScore: 88,
  },
  {
    id: "ST-014",
    name: "台北大安復興",
    district: "Da-an",
    city: "Taipei",
    manager: "Leo Huang",
    lights: { demand: "green", operations: "yellow", staffing: "green", margin: "green" },
    riskScore: 52,
  },
  {
    id: "ST-021",
    name: "新北板橋文化",
    district: "Banqiao",
    city: "New Taipei",
    manager: "An Lin",
    lights: { demand: "yellow", operations: "yellow", staffing: "red", margin: "red" },
    riskScore: 79,
  },
] as const;

const baseIssues: StoreOpsIssue[] = [
  {
    id: "ISS-1024",
    title: "晚間負評與清潔分數同步惡化",
    storeId: "ST-008",
    storeName: "台北信義 A11",
    status: "new",
    severity: "critical",
    source: "multiSignal",
    ownerRoleId: "opsLead",
    ownerName: "營運主管",
    slaDueAt: "2026-07-05T11:00:00.000Z",
    createdAt: "2026-07-05T06:24:00.000Z",
    updatedAt: "2026-07-05T06:24:00.000Z",
    evidenceIds: ["EV-1024-CAM", "EV-1024-FOUR"],
    summary: "Payment, review, camera, and ForecastOps signals point to a peak-hour incident.",
  },
  {
    id: "ISS-1021",
    title: "冷氣遠端重啟等待核准",
    storeId: "ST-014",
    storeName: "台北大安復興",
    status: "waitingapproval",
    severity: "high",
    source: "iot",
    ownerRoleId: "facilitiesLead",
    ownerName: "工務主任",
    slaDueAt: "2026-07-05T10:30:00.000Z",
    createdAt: "2026-07-05T04:50:00.000Z",
    updatedAt: "2026-07-05T07:30:00.000Z",
    evidenceIds: [],
    summary: "HVAC telemetry shows repeated compressor fault codes.",
  },
  {
    id: "ISS-1008",
    title: "補班日人力不足觀察中",
    storeId: "ST-021",
    storeName: "新北板橋文化",
    status: "observing",
    severity: "medium",
    source: "forecastOps",
    ownerRoleId: "supportLead",
    ownerName: "客服主管",
    slaDueAt: "2026-07-06T03:00:00.000Z",
    createdAt: "2026-07-04T02:10:00.000Z",
    updatedAt: "2026-07-05T01:15:00.000Z",
    evidenceIds: [],
    summary: "ForecastOps staffing light remains red after shift swap.",
  },
];

const baseEvidence = [
  {
    id: "EV-1024-CAM",
    issueId: "ISS-1024",
    kind: "camera",
    title: "Camera event placeholder",
    sourceLabel: "Camera Access",
    summary: "Video access is locked until an operator records a purpose for review.",
    polarity: "neutral",
    confidence: 0.7,
    occurredAt: "2026-07-04T20:05:00.000Z",
    lockedReason: "Purpose confirmation required before camera evidence can be opened.",
  },
  {
    id: "EV-1024-FOUR",
    issueId: "ISS-1024",
    kind: "forecastOps",
    title: "ForecastOps four-light snapshot",
    sourceLabel: "ForecastOps",
    summary: "Operations light is red; staffing and margin lights are yellow.",
    polarity: "supporting",
    confidence: 0.88,
    occurredAt: "2026-07-05T05:00:00.000Z",
  },
];

test("four-light chip drives Store Ops API query and visible queue", async ({ page }) => {
  const api = await mockStoreOpsApi(page);

  await page.goto("/operator");
  await page.getByRole("button", { name: /門市營運|Store Ops/ }).click();
  await expect(page.locator('[data-screen-label="Store Ops 門市營運"]')).toBeVisible();

  const operationsRed = page.getByRole("button", { name: /Operations Red 1/ });
  await expect(operationsRed).toBeVisible();
  await operationsRed.click();

  await expect.poll(() => api.readRequests.some((url) => url.searchParams.get("light") === "operations" && url.searchParams.get("lightStatus") === "red")).toBe(true);
  await expect(page.locator('[aria-label="門市 Issue queue"]')).toContainText("ISS-1024");
  await expect(page.locator('[aria-label="門市 Issue queue"]')).not.toContainText("ISS-1008");
});

test("ISS-1024 lifecycle writes through Store Ops API and reloads updated state", async ({ page }) => {
  const api = await mockStoreOpsApi(page);

  await page.goto("/operator");
  await page.getByRole("button", { name: /門市營運|Store Ops/ }).click();

  await page.getByRole("button", { exact: true, name: "完成 Triage" }).click();
  await page.getByRole("button", { exact: true, name: "Submit Triage" }).click();
  await waitForWrite(api, "/api/v1/operator/store-ops/issues/ISS-1024/triage");
  await expect(page.getByRole("button", { exact: true, name: "指派 Owner" })).toBeVisible();

  await page.getByRole("button", { exact: true, name: "指派 Owner" }).click();
  await page.getByRole("button", { exact: true, name: "Assign Owner" }).click();
  await waitForWrite(api, "/api/v1/operator/store-ops/issues/ISS-1024/assign");
  await expect(page.getByRole("button", { exact: true, name: "建立 Field Action" })).toBeVisible();

  await page.getByRole("button", { exact: true, name: "建立 Field Action" }).click();
  await page.getByRole("button", { exact: true, name: "Create Action" }).click();
  await waitForWrite(api, "/api/v1/operator/store-ops/issues/ISS-1024/actions");
  await expect(page.getByRole("button", { exact: true, name: "提交 Field Report" })).toBeVisible();

  await page.getByRole("button", { exact: true, name: "提交 Field Report" }).click();
  await page.getByLabel("Report summary").fill("Counter lane cleaned and payment queue cleared.");
  await page.getByRole("button", { exact: true, name: "Submit Report" }).click();
  await waitForWrite(api, "/api/v1/operator/store-ops/issues/ISS-1024/field-report");
  await expect(page.getByRole("button", { exact: true, name: "檢視 Outcome" })).toBeVisible();

  await page.getByRole("button", { exact: true, name: "檢視 Outcome" }).click();
  await page.getByLabel("Impact summary").fill("Negative review cluster stopped after field action.");
  await page.getByLabel("Evidence summary").fill("Payment queue and CS case trend returned to baseline.");
  await page.getByRole("button", { exact: true, name: "Submit Outcome" }).click();
  await waitForWrite(api, "/api/v1/operator/store-ops/issues/ISS-1024/outcome");

  await expect(page.locator('[aria-label="ISS-1024 detail"]')).toContainText("Closed");
  expect(api.writeRequests.map((url) => url.pathname)).toEqual(
    expect.arrayContaining([
      "/api/v1/operator/store-ops/issues/ISS-1024/triage",
      "/api/v1/operator/store-ops/issues/ISS-1024/assign",
      "/api/v1/operator/store-ops/issues/ISS-1024/actions",
      "/api/v1/operator/store-ops/issues/ISS-1024/field-report",
      "/api/v1/operator/store-ops/issues/ISS-1024/outcome",
    ]),
  );
  expect(api.writeHeaders.every((headers) => headers["idempotency-key"] && headers["x-correlation-id"])).toBe(true);
});

test("camera evidence remains locked until permitted purpose is submitted", async ({ page }) => {
  const api = await mockStoreOpsApi(page);

  await page.goto("/operator");
  await page.getByRole("button", { name: /門市營運|Store Ops/ }).click();
  await expect(page.getByText("影像鎖定 • 需授權")).toBeVisible();

  await page.getByText("點擊填寫調閱目的").click();
  const dialog = page.getByRole("dialog", { name: "Camera Purpose" });
  await dialog.getByRole("textbox", { name: /Purpose Required/ }).fill("payment incident quality audit");
  await dialog.getByRole("checkbox", { name: /Acknowledge privacy and audit warning/ }).check();
  await dialog.getByRole("button", { exact: true, name: "Record Purpose" }).click();
  await waitForWrite(api, "/api/v1/operator/store-ops/issues/ISS-1024/camera-purpose");

  await expect(page.getByText("影像已解鎖")).toBeVisible();
  expect(api.writeRequests.some((url) => url.pathname === "/api/v1/operator/store-ops/issues/ISS-1024/camera-purpose")).toBe(true);
});

async function mockStoreOpsApi(page: Page) {
  const issues = baseIssues.map((issue) => ({ ...issue, evidenceIds: [...issue.evidenceIds] }));
  const evidence = baseEvidence.map((item) => ({ ...item }));
  const auditEvents: unknown[] = [];
  const readRequests: URL[] = [];
  const writeRequests: URL[] = [];
  const writeHeaders: Record<string, string>[] = [];

  await page.route("**/api/v1/operator/store-ops/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (request.method() === "GET" && url.pathname.endsWith("/issues")) {
      readRequests.push(url);
      const filtered = filterIssues(issues, url);
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          stores,
          issues: filtered,
          evidence,
          auditEvents,
          fourLightSummary: summarizeLights(issues),
          count: filtered.length,
          filters: Object.fromEntries(url.searchParams.entries()),
        }),
      });
      return;
    }

    if (request.method() === "POST") {
      writeRequests.push(url);
      writeHeaders.push(request.headers());
      const issue = issues.find((item) => url.pathname.includes(`/issues/${item.id}/`));
      if (issue) {
        if (url.pathname.endsWith("/triage")) issue.status = "triaged";
        else if (url.pathname.endsWith("/assign")) issue.status = "assigned";
        else if (url.pathname.endsWith("/actions")) issue.status = "inprogress";
        else if (url.pathname.endsWith("/field-report")) issue.status = "observing";
        else if (url.pathname.endsWith("/outcome")) issue.status = "closed";
        else if (url.pathname.endsWith("/camera-purpose")) {
          const camera = evidence.find((item) => item.id === "EV-1024-CAM");
          if (camera) {
            delete (camera as { lockedReason?: string }).lockedReason;
            camera.summary = "Camera evidence unlocked for a recorded, audit-scoped purpose.";
          }
        }
        issue.updatedAt = new Date().toISOString();
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          stores,
          issues,
          evidence,
          auditEvents,
          fourLightSummary: summarizeLights(issues),
          count: issues.length,
          issue,
          idempotentReplay: false,
        }),
      });
      return;
    }

    await route.fallback();
  });

  return { readRequests, writeRequests, writeHeaders };
}

async function waitForWrite(api: Awaited<ReturnType<typeof mockStoreOpsApi>>, pathname: string) {
  await expect.poll(() => api.writeRequests.some((url) => url.pathname === pathname)).toBe(true);
}

function filterIssues(issues: StoreOpsIssue[], url: URL) {
  const light = url.searchParams.get("light");
  const lightStatus = url.searchParams.get("lightStatus");
  if (!light || !lightStatus) return issues;

  return issues.filter((issue) => {
    const store = stores.find((item) => item.id === issue.storeId);
    return store?.lights[light as keyof typeof store.lights] === lightStatus;
  });
}

function summarizeLights(issues: StoreOpsIssue[]) {
  return (["demand", "operations", "staffing", "margin"] as const).map((dimension) => {
    const counts: Record<StoreLightStatus, number> = { green: 0, yellow: 0, red: 0 };
    const issueCounts: Record<StoreLightStatus, number> = { green: 0, yellow: 0, red: 0 };
    for (const store of stores) {
      const status = store.lights[dimension] as StoreLightStatus;
      counts[status] += 1;
      issueCounts[status] += issues.filter((issue) => issue.storeId === store.id).length;
    }
    return {
      dimension,
      label: dimension[0].toUpperCase() + dimension.slice(1),
      counts,
      issueCounts,
    };
  });
}
