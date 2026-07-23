import { expect, request as playwrightRequest, test, type Page } from "@playwright/test";

import {
  acquireOperatorBackendLock,
  releaseOperatorBackendLock,
} from "./_operatorBackendLock";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const MANAGER_SUBJECT = "qa-intake-shell-manager";
const MANAGER_HEADERS = {
  "x-operator-role": "expansion-manager",
  "x-roles": "expansion_user,site_reviewer",
  "x-subject-id": MANAGER_SUBJECT,
  "x-tenant-id": "tenant-a",
};

test.describe.configure({ mode: "serial", timeout: 120_000 });
test.use({ extraHTTPHeaders: {} });

test.beforeAll(async () => {
  await acquireOperatorBackendLock();
});

test.afterAll(() => {
  releaseOperatorBackendLock();
});

test.beforeEach(async () => {
  const api = await playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: MANAGER_HEADERS,
  });
  const reset = await api.post("/api/v1/operator/network-listings/reset");
  expect(reset.status()).toBe(200);
  await api.dispose();
});

async function createReadyIntake(suffix: string): Promise<string> {
  const api = await playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: {
      ...MANAGER_HEADERS,
      "Idempotency-Key": `idem-shell-${suffix}`,
      "X-Correlation-Id": `corr-shell-${suffix}`,
    },
  });
  const response = await api.post("/api/v1/operator/network-listings/intake/submit", {
    data: {
      heatZoneId: "HZ-01",
      url: "https://www.synthetic.example/detail-77120345.html",
    },
  });
  expect(response.status(), await response.text()).toBe(200);
  const record = (await response.json()) as { id: string; stage: string };
  expect(record.stage).toBe("READY");
  await api.dispose();
  return record.id;
}

function durableHref(intakeId: string, section = "timeline") {
  const query = new URLSearchParams({
    compareTarget: "LISTING-SHELL-TARGET",
    role: "expansion-manager",
    section,
    subject: MANAGER_SUBJECT,
    task: "TASK-SHELL-001",
  });
  return `/w/expansion/listings/intake/${intakeId}?${query.toString()}`;
}

async function expectDurableContext(page: Page, intakeId: string, section: string) {
  await expect(page).toHaveURL(
    new RegExp(`/w/expansion/listings/intake/${intakeId}\\?.*section=${section}`),
  );
  const current = new URL(page.url());
  expect(current.searchParams.get("compareTarget")).toBe("LISTING-SHELL-TARGET");
  expect(current.searchParams.get("task")).toBe("TASK-SHELL-001");
  expect(current.searchParams.get("selected")).toBeNull();
  expect(current.searchParams.get("dialog")).toBeNull();
}

test("direct-open, reload, external source, back and forward preserve the durable detail state", async ({
  page,
}) => {
  const intakeId = await createReadyIntake("direct");
  await page.goto(durableHref(intakeId));

  await expect(page.getByTestId("intake-processing-page")).toBeVisible();
  await expect(page.getByTestId("intake-detail-id")).toHaveText(intakeId);
  await expect(page.getByTestId("intake-stage-timeline")).toBeVisible();
  await expectDurableContext(page, intakeId, "timeline");

  const sourceLink = page.getByTestId("intake-open-source-link");
  await expect(sourceLink).toHaveAttribute("target", "_blank");
  const sourcePagePromise = page.context().waitForEvent("page");
  await sourceLink.click();
  const sourcePage = await sourcePagePromise;
  await expectDurableContext(page, intakeId, "timeline");
  await sourcePage.close();

  await page.reload();
  await expect(page.getByTestId("intake-processing-page")).toBeVisible();
  await expect(page.getByTestId("intake-stage-timeline")).toBeVisible();
  await expectDurableContext(page, intakeId, "timeline");

  await page.getByTestId("tab-evidence").click();
  await expect(page.getByTestId("intake-evidence-panel")).toBeVisible();
  await expectDurableContext(page, intakeId, "evidence");

  await page.getByTestId("tab-identity").click();
  await expect(page.getByTestId("identity-decision-panel")).toBeVisible();
  await expectDurableContext(page, intakeId, "identity");
  expect(new URL(page.url()).searchParams.get("compare")).toBe("true");
  await page.getByTestId("tab-compare-btn").click();
  await expect(page.getByTestId("listing-compare-table")).toBeVisible();

  await page.goBack();
  await expect(page.getByTestId("intake-evidence-panel")).toBeVisible();
  await expectDurableContext(page, intakeId, "evidence");
  await page.goForward();
  await expect(page.getByTestId("identity-decision-panel")).toBeVisible();
  await expectDurableContext(page, intakeId, "identity");

  await page.getByTestId("tab-assignment").click();
  await expect(page.getByTestId("assignment-sla-summary")).toBeVisible();
  await expectDurableContext(page, intakeId, "assignment");

  await page.getByTestId("tab-receipts").click();
  await expect(page.getByTestId("intake-durable-receipt-panel")).toBeVisible();
  await expectDurableContext(page, intakeId, "receipts");

  await expect(page.getByTestId("tab-promotion")).toBeVisible();
  await page.getByTestId("tab-promotion").click();
  await expect(page.getByTestId("promotion-review-panel")).toBeVisible();
  await expectDurableContext(page, intakeId, "promotion");
});

test("the Inbox drawer is preview-only and its full-page action reaches the durable route", async ({
  page,
}) => {
  const intakeId = await createReadyIntake("preview");
  await page.goto("/w/expansion/listings?role=expansion-manager");

  await expect(page.getByTestId("intake-inbox-view")).toBeVisible();
  await page.getByTestId(`intake-inbox-row-${intakeId}`).click();
  await expect(page.getByTestId("intake-detail-preview")).toBeVisible();
  await expect(page.getByTestId("intake-detail-dialog")).toHaveCount(0);
  await expect(page.getByTestId("identity-decision-panel")).toHaveCount(0);
  await expect(page.getByTestId("promotion-review-panel")).toHaveCount(0);

  await page.getByTestId("intake-preview-open-full-page").click();
  await expect(page.getByTestId("intake-processing-page")).toBeVisible();
  await expect(page.getByTestId("intake-detail-id")).toHaveText(intakeId);
  await expect(page).toHaveURL(
    new RegExp(`/w/expansion/listings/intake/${intakeId}(?:\\?|$)`),
  );
});

test("the durable route owns missing-record and permission-denied states", async ({ page }) => {
  await page.goto(durableHref("IN-DOES-NOT-EXIST"));
  await expect(page.getByTestId("intake-route-state-missing")).toBeVisible();
  await expect(page).toHaveURL(/\/w\/expansion\/listings\/intake\/IN-DOES-NOT-EXIST/);

  const intakeId = await createReadyIntake("denied");
  await page.goto(
    `/w/expansion/listings/intake/${intakeId}?role=ops-lead&subject=qa-denied`,
  );
  await expect(page.getByTestId("intake-route-state-denied")).toBeVisible();
  await expect(page).toHaveURL(
    new RegExp(`/w/expansion/listings/intake/${intakeId}(?:\\?|$)`),
  );
});
