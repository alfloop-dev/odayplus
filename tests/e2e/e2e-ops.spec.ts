import { expect, test } from "@playwright/test";

const OPS_ROUTES = [
  "/operations",
  "/w/operations/forecast?selected=store-002",
  "/w/operations/alerts?selected=alert-orange-2001",
  "/w/operations/forecast/store-001",
];

test("Operations workspace routes render inside the OpsBoard shell", async ({ page }) => {
  for (const route of OPS_ROUTES) {
    const res = await page.goto(route);
    expect(res?.status(), `GET ${route}`).toBeLessThan(400);
    await expect(page.getByTestId("app-shell")).toBeVisible();
    await expect(page.getByTestId("page-header")).toBeVisible();
    await expect(page.getByTestId("operations-data-status")).toBeVisible();
  }
});

test("Forecast overview exposes four-light, forecast bands, freshness, model, and drawer", async ({ page }) => {
  await page.goto("/w/operations/forecast?selected=store-002");

  await expect(page.getByTestId("ops-forecast-page")).toBeVisible();
  await expect(page.getByRole("table").first()).toContainText("P10-P90");
  await expect(page.getByTestId("four-light-red")).toContainText("RED");
  await expect(page.getByTestId("four-light-orange").first()).toContainText("gap -26%");
  await expect(page.getByText("four-light-policy-v1").first()).toBeVisible();
  await expect(page.getByTestId("forecast-row-drawer")).toContainText("新北板橋店");
  await expect(page.getByTestId("forecast-row-drawer")).toContainText("開啟單店詳情");
});

test("Alert center separates acknowledge and RED/ORANGE handoff rules", async ({ page }) => {
  await page.goto("/w/operations/alerts?selected=alert-yellow-3001");

  await expect(page.getByTestId("ops-alerts-page")).toContainText("actual $103,200 vs forecast_p50");
  await expect(page.getByTestId("ops-alerts-page")).toContainText("manual_review");
  await expect(page.getByTestId("ops-alerts-page")).toContainText("eligible");
  await expect(page.getByTestId("alert-drawer")).toContainText("alert-only");
  await expect(page.getByTestId("alert-drawer")).toContainText("建立資料查核任務");
  await expect(page.getByTestId("alert-drawer")).toContainText("not optimistic");
  await expect(page.getByText("correlation_id")).toBeVisible();
});

test("Store detail shows fixed sections for forecast, root cause, recommendation, handoff, and audit", async ({ page }) => {
  await page.goto("/w/operations/forecast/store-001");

  await expect(page.getByTestId("store-summary")).toContainText("Gap vs baseline");
  await expect(page.getByTestId("forecast-band-chart")).toContainText("w4");
  await expect(page.getByTestId("forecast-band-chart")).toContainText("P10");
  await expect(page.getByTestId("root-cause-evidence-card")).toContainText("Partial machine telemetry");
  await expect(page.getByTestId("root-cause-evidence-card")).toContainText("recommended_actions");
  await expect(page.getByTestId("recommendation-panel")).toContainText("由系統依");
  await expect(page.getByTestId("handoff-panel")).toContainText("handoff-9001");
  await expect(page.getByTestId("handoff-panel")).toContainText("manual_review");
  await expect(page.getByTestId("audit-metadata")).toContainText("prediction_run_id");
  await expect(page.getByTestId("audit-metadata")).toContainText("corr-forecast-red-1001");
});
