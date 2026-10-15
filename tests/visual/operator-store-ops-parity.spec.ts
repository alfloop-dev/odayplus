import AxeBuilder from "@axe-core/playwright";
import currentMemoryTriage from "../fixtures/operator-store-triage-visual-memory.json";
import { createHash } from "node:crypto";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { ISSUE_FIXTURES, STORE_FIXTURES, EVIDENCE_FIXTURES, AUDIT_EVENT_FIXTURES } from "../../apps/web/features/operator/fixtures";

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
  workflowRecords?: Record<string, unknown>[];
  outcomeReviews?: Record<string, unknown>[];
};

type MemoryWorkflowSample = {
  requestPayload: Record<string, unknown>;
  issueUpdatedAt: string;
  record: Record<string, unknown>;
  auditEvent: { id: string; occurredAt: string; metadata: Record<string, unknown> };
  evidenceItem?: Record<string, unknown>;
};

function canonicalFixtureJSON(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonicalFixtureJSON).join(",") + "]";
  if (value !== null && typeof value === "object") {
    const row = value as Record<string, unknown>;
    return "{" + Object.keys(row).sort().map((key) => JSON.stringify(key) + ":" + canonicalFixtureJSON(row[key])).join(",") + "}";
  }
  return JSON.stringify(value);
}

function bindMemoryWorkflowReceipt(
  sample: MemoryWorkflowSample,
  action: string,
  issue: StoreOpsIssue,
  sourceIssueUpdatedAt: string,
  payload: Record<string, unknown>,
  headers: Record<string, string>,
) {
  const expectedPayload = structuredClone(sample.requestPayload);
  if (action === "field-report") expectedPayload.observedAt = sourceIssueUpdatedAt;
  expect(payload).toMatchObject(expectedPayload);
  const record = structuredClone(sample.record);
  const auditEvent = structuredClone(sample.auditEvent);
  record.correlationId = headers["x-correlation-id"];
  record.sourceIssueUpdatedAt = sourceIssueUpdatedAt;
  record.recordedAt = issue.updatedAt;
  if (action === "field-report") (record.submission as Record<string, unknown>).observedAt = payload.observedAt;
  auditEvent.occurredAt = action === "camera-purpose" ? sample.auditEvent.occurredAt : issue.updatedAt;
  auditEvent.metadata.idempotencyKey = headers["idempotency-key"];
  auditEvent.metadata.reviewRecordHash = createHash("sha256").update(canonicalFixtureJSON(record)).digest("hex");
  const history = action === "outcome" ? "outcomeReviews" : "workflowRecords";
  issue[history] = [...(issue[history] ?? []), record];
  return { auditEvent };
}

// Actual local Memory producer capture; only CID/key/time transport fields are adapted below.
// This mocked visual API does not prove native SQL, identity or delivered effects.
const visualMemoryTriageSample = currentMemoryTriage.triage as unknown as MemoryWorkflowSample;

test.describe.configure({ timeout: 180_000 });

const dialogs = [
  { id: "triage", label: "Dialog Triage", title: "完成 Triage", width: 470 },
  { id: "assign", label: "Dialog Assign", title: "指派負責人", width: 470 },
  { id: "action", label: "Dialog Create Action", title: "建立處置", width: 520 },
  { id: "outcome", label: "Dialog Outcome Review", title: "成效判斷", width: 560 },
  { id: "escalate", label: "Dialog Escalate", title: "升級事件", width: 470 },
  { id: "cameraPurpose", label: "Dialog Camera Purpose", title: "調閱 Camera 片段", width: 450 },
  { id: "replyReview", label: "Dialog Reply Review", title: "回覆審查", width: 520 },
] as const;

async function openStore(page: Page, withPaymentTrend = true) {
  const state = {
    stores: STORE_FIXTURES,
    issues: structuredClone(ISSUE_FIXTURES) as StoreOpsIssue[],
    evidence: withPaymentTrend ? EVIDENCE_FIXTURES : EVIDENCE_FIXTURES.map(({ paymentTrend: _trend, ...item }) => item),
    auditEvents: [...AUDIT_EVENT_FIXTURES] as unknown[],
    count: ISSUE_FIXTURES.length,
  };
  await page.route("**/api/v1/operator/store-ops/**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ ...state, issue: state.issues.find(issue => route.request().url().includes("/issues/" + issue.id)) }),
  }));
  await page.goto("/operator?ws=store&entity=ISS-1024");
  await expect(page.locator('[aria-label="ISS-1024 detail"]')).toBeVisible();
  return state;
}

async function save(page: Page, info: TestInfo, name: string, fullPage = false) {
  const file = process.env.STORE_OPS_PARITY_EVIDENCE_DIR
    ? path.join(process.env.STORE_OPS_PARITY_EVIDENCE_DIR, `${name}.png`)
    : info.outputPath(`${name}.png`);
  await mkdir(path.dirname(file), { recursive: true });
  await page.screenshot({ path: file, fullPage, animations: "disabled" });
  await info.attach(name, { path: file, contentType: "image/png" });
}

async function measure(locator: Locator) {
  return locator.evaluate((element) => {
    const r = element.getBoundingClientRect();
    const s = getComputedStyle(element);
    return { x: r.x, y: r.y, width: r.width, height: r.height, scrollWidth: element.scrollWidth, radius: s.borderRadius };
  });
}

async function record(info: TestInfo, name: string, data: unknown) {
  const file = process.env.STORE_OPS_PARITY_EVIDENCE_DIR
    ? path.join(process.env.STORE_OPS_PARITY_EVIDENCE_DIR, `${name}.json`)
    : info.outputPath(`${name}.json`);
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(data, null, 2));
  await info.attach(name, { path: file, contentType: "application/json" });
}

test("Missing payment series stays unavailable; AI CTA opens action without a write", async ({ page }) => {
  let writes = 0;
  page.on("request", (request) => { if (request.method() === "POST" && request.url().includes("/store-ops/")) writes += 1; });
  await openStore(page, false);
  const metric = page.getByLabel("付款失敗率趨勢");
  await expect(metric).toContainText("來源未提供付款失敗率序列");
  await expect(metric.locator("i")).toHaveCount(0);
  await expect(metric.locator('[data-baseline="true"]')).toHaveCount(0);
  await page.getByRole("button", { name: "採用建議並建立處置" }).click();
  await expect(page.getByRole("dialog", { name: "建立處置", exact: true })).toBeVisible();
  expect(writes).toBe(0);
});

for (const width of [1440, 1024, 390]) {
  test(`Store workflow preserves pending and failed intent at ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await openStore(page);
    let finish!: () => void;
    const waitForRelease = new Promise<void>((resolve) => { finish = resolve; });
    const keys: string[] = [];
    await page.route("**/api/v1/operator/store-ops/issues/ISS-1024/triage", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      keys.push(route.request().headers()["idempotency-key"]);
      if (keys.length === 1) {
        await waitForRelease;
        await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "synthetic response failure" }) });
      } else {
        const issue = state.issues.find((issue) => issue.id === "ISS-1024")!;
        const sourceIssueUpdatedAt = issue.updatedAt;
        issue.status = currentMemoryTriage.triage.status;
        issue.updatedAt = visualMemoryTriageSample.issueUpdatedAt;
        const receipt = bindMemoryWorkflowReceipt(visualMemoryTriageSample, "triage", issue, sourceIssueUpdatedAt, route.request().postDataJSON(), route.request().headers());
        state.auditEvents.push(receipt.auditEvent);
        await route.fulfill({ contentType: "application/json", body: JSON.stringify({ ...state, issue, ...receipt, idempotentReplay: false }) });
      }
    });
    const trigger = page.getByLabel("Action rail").getByRole("button", { name: "完成 Triage", exact: true });
    await trigger.click();
    const panel = page.getByRole("dialog", { name: "完成 Triage", exact: true });
    await panel.getByLabel("根因分類").selectOption("payment");
    await panel.getByRole("button", { name: "完成 Triage", exact: true }).click();
    await expect(panel).toHaveAttribute("aria-busy", "true");
    await expect(panel.getByRole("status")).toBeFocused();
    await expect(panel.getByLabel("根因分類")).toBeDisabled();
    await expect(panel.getByRole("button", { name: "關閉對話框" })).toBeDisabled();
    await expect(panel.getByRole("button", { name: "取消" })).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(panel).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(panel.getByRole("status")).toBeFocused();
    const pending = await measure(panel);
    expect(pending.width).toBe(Math.min(470, width - 40));
    expect(pending.scrollWidth).toBeLessThanOrEqual(Math.ceil(pending.width));
    const pendingAxe = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
    expect(pendingAxe.violations).toEqual([]);
    await save(page, info, `store-triage-pending-${width}`);
    finish();
    await expect(panel.getByRole("alert")).toContainText("HTTP 503");
    await expect(panel.getByLabel("根因分類")).toHaveValue("payment");
    await expect(panel.getByRole("alert")).toBeFocused();
    await expect(panel.getByRole("button", { name: "完成 Triage", exact: true })).toBeEnabled();
    const failedAxe = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
    expect(failedAxe.violations).toEqual([]);
    await save(page, info, `store-triage-failed-${width}`);
    await panel.getByRole("button", { name: "完成 Triage", exact: true }).click();
    await expect(panel).toHaveCount(0);
    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(keys[0]);
    await record(info, `store-triage-confirmation-${width}`, {
      pending, pendingAccessibilityViolations: pendingAxe.violations,
      failedAccessibilityViolations: failedAxe.violations, requestCount: keys.length, sameRetryKey: keys[1] === keys[0],
    });
  });
}

for (const width of [1440, 1024, 390]) {
  test(`Store content geometry and reachable bottom cards at ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await openStore(page);
    const detail = page.getByLabel("ISS-1024 detail");
    const fusion = detail.locator("section").filter({ has: page.getByRole("heading", { name: "證據融合", exact: true }) }).first();
    const timeline = page.getByRole("region", { name: "事件與處置時間軸" });
    const source = page.getByRole("region", { name: "證據來源明細卡片" });
    const metric = page.getByLabel("付款失敗率趨勢");
    await expect(metric).toContainText("12.4%");
    await expect(metric.locator('[role="img"] i')).toHaveCount(14);
    await expect(fusion.getByRole("tablist")).toHaveCount(0);
    await expect(source.getByRole("tablist")).toBeVisible();
    await expect(timeline.locator("li")).not.toHaveCount(0);
    const rail = page.getByLabel("Action rail");
    await expect(rail.locator("section").first().getByRole("button")).toHaveCount(2);
    await expect(rail.getByRole("button", { name: "指派負責人" })).not.toBeVisible();
    const data = { detail: await measure(detail), fusion: await measure(fusion), timeline: await measure(timeline), source: await measure(source), metric: await measure(metric), document: await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth })) };
    expect(data.document.scrollWidth).toBeLessThanOrEqual(width);
    for (const card of [data.fusion, data.timeline, data.source, data.metric]) {
      expect(card.width).toBeLessThanOrEqual(data.detail.width);
      expect(card.scrollWidth).toBeLessThanOrEqual(Math.ceil(card.width));
    }
    expect(data.timeline.y).toBeGreaterThan(data.fusion.y + data.fusion.height);
    expect(data.source.y).toBeGreaterThan(data.timeline.y + data.timeline.height);
    await expect(page.getByRole("main")).toHaveCount(1);
    if (width === 1440) {
      expect((await measure(page.getByLabel("門市 Issue queue"))).x).toBe(20);
      expect(data.detail.width).toBeGreaterThan(730);
      expect(data.detail.width).toBeLessThan(745);
    }
    await source.getByRole("tab", { name: "ForecastOps", exact: true }).click();
    await expect(source.getByText(/28 天門市營運營收預測/)).toBeVisible();
    await source.getByRole("tab", { name: "Google 評價", exact: true }).click();
    const accessibility = await new AxeBuilder({ page }).include('[aria-label="付款失敗率趨勢"]').include('[aria-label="事件與處置時間軸"]').include('[aria-label="證據來源明細卡片"]').analyze();
    expect(accessibility.violations).toEqual([]);
    await record(info, `geometry-store-${width}`, { ...data, accessibilityViolations: accessibility.violations });
    if (width !== 1024) {
      await page.evaluate(() => scrollTo(0, 0));
      await save(page, info, `after-store-${width}`, true);
      if (process.env.STORE_OPS_PARITY_DESIGN === "1") {
        const design = await page.context().newPage();
        await design.setViewportSize({ width, height: 900 });
        await openDesign(design);
        await save(design, info, `design-store-${width}`, true);
        await design.close();
      }
    }
  });
}

for (const width of [1440, 390]) {
  test(`Seven Store dialogs preserve Package 10 geometry and keyboard return at ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await openStore(page);
    const rail = page.getByLabel("Action rail");
    const measurements: Record<string, unknown> = {};
    const design = process.env.STORE_OPS_PARITY_DESIGN === "1" ? await page.context().newPage() : null;
    if (design) {
      await design.setViewportSize({ width, height: 900 });
      await openDesign(design);
    }
    for (const dialog of dialogs) {
      await page.getByRole("button", { name: /晚間負評與清潔分數同步惡化/ }).click();
      if (dialog.id === "outcome") await page.getByRole("button", { name: /補班日人力不足觀察中/ }).click();
      if (dialog.id === "escalate") await page.getByRole("button", { name: /冷氣遠端重啟等待核准/ }).click();
      const buttons: Record<string, string> = { triage: "完成 Triage", assign: "指派負責人", action: "建立處置", outcome: "判斷成效", escalate: "升級處理", cameraPurpose: "填寫影像調閱目的", replyReview: "回覆審查" };
      const trigger = rail.getByRole("button", { name: buttons[dialog.id], exact: true }).first();
      if (!await trigger.isVisible()) await rail.locator("summary").click();
      await trigger.focus();
      await trigger.click();
      const panel = page.getByRole("dialog", { name: dialog.title, exact: true });
      await expect(panel).toBeVisible();
      const actual = await measure(panel);
      expect(actual.width).toBe(Math.min(dialog.width, width - 40));
      expect(actual.radius).toBe("14px");
      expect(actual.x).toBeGreaterThanOrEqual(20);
      expect(actual.x + actual.width).toBeLessThanOrEqual(width - 20);
      expect(actual.y).toBeGreaterThanOrEqual(20);
      expect(actual.height).toBeLessThanOrEqual(860);
      expect(actual.scrollWidth).toBeLessThanOrEqual(actual.width);
      if (dialog.id === "triage") {
        await expect(panel.getByLabel("根因分類")).toBeFocused();
        await expect(panel.getByLabel("嚴重度")).not.toBeVisible();
        await expect(panel.getByRole("combobox").filter({ visible: true })).toHaveCount(2);
        expect(actual.height).toBeLessThan(470);
        await panel.locator("summary").click();
        await expect(panel.getByLabel("嚴重度")).toBeVisible();
        await panel.locator("summary").click();
      }
      const accessibility = await new AxeBuilder({ page }).include(`[data-workflow="${dialog.id}"]`).analyze();
      expect(accessibility.violations).toEqual([]);
      await save(page, info, `after-${dialog.id}-${width}`);
      measurements[dialog.id] = { actual, accessibilityViolations: accessibility.violations };
      const cancel = panel.getByRole("button", { name: "取消", exact: true });
      const submit = panel.getByRole("button", { name: /完成 Triage|確認指派|建立處置|送出成效判斷|確認升級|記錄目的並檢視|送出回覆審查/, exact: true }).last();
      await submit.focus();
      await page.keyboard.press("Tab");
      await expect(panel.getByRole("button", { name: "關閉對話框" })).toBeFocused();
      await page.keyboard.press("Shift+Tab");
      await expect(submit).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(panel).toHaveCount(0);
      await expect(trigger).toBeFocused();
      expect(await cancel.count()).toBe(0);
      if (design) {
        await openDesignDialog(design, dialog.id);
        const reference = design.locator(`[data-screen-label="${dialog.label}"] > div`).first();
        await expect(reference).toBeVisible();
        const geometry = await measure(reference);
        expect(actual.width).toBeCloseTo(geometry.width, 0);
        measurements[dialog.id] = { actual, design: geometry, accessibilityViolations: accessibility.violations };
        await save(design, info, `design-${dialog.id}-${width}`);
        await design.getByRole("button", { name: "關閉對話框", exact: true }).click();
      }
    }
    await design?.close();
    await record(info, `geometry-dialogs-${width}`, measurements);
  });
}

async function openDesign(page: Page) {
  // The archive is self-contained. Do not wait on optional Google Font CDN requests.
  await page.route(/^https?:\/\//, (route) => route.abort());
  await page.goto(pathToFileURL(path.resolve("docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html")).href, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /門市營運.*Store Ops/ }).click();
  await page.addStyleTag({ content: "*, *::before, *::after { animation: none !important; transition: none !important; }" });
}
async function openDesignDialog(page: Page, id: string) {
  // Reference-only DOM events: the archived 390px prototype clips its tabs
  // (known VDC-002 defect). Actual application interactions above use real clicks.
  await page.getByText("ISS-1024", { exact: true }).first().dispatchEvent("click");
  await page.getByRole("button", { name: "Google 評價", exact: true }).dispatchEvent("click");
  if (id === "assign") await page.getByText("ISS-1015", { exact: true }).first().dispatchEvent("click");
  if (id === "outcome") await page.getByText("ISS-1008", { exact: true }).first().dispatchEvent("click");
  if (id === "cameraPurpose") {
    await page.getByRole("button", { name: "Camera", exact: true }).dispatchEvent("click");
    await page.getByRole("button", { name: "輸入調閱目的以檢視", exact: true }).dispatchEvent("click");
    return;
  }
  const buttons: Record<string, string> = { triage: "完成 Triage", assign: "指派 Owner", action: "採用建議並建立處置", outcome: "判斷成效", escalate: "升級（Growth／Network／Govern）", replyReview: "回覆評價" };
  await page.getByRole("button", { name: buttons[id], exact: true }).dispatchEvent("click");
}
