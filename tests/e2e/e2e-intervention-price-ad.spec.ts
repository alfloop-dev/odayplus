import { expect, test } from "@playwright/test";

const ROUTES = [
  "/interventions?selected=int-3001&drawer=case",
  "/interventions?selected=int-3002&drawer=case",
  "/pricing?selected=price-5101&drawer=plan",
  "/pricing?selected=price-5102&drawer=plan",
  "/adlift?selected=adlift-8801&drawer=report",
  "/adlift?selected=adlift-8802&drawer=report",
  "/adlift?selected=adlift-8803&drawer=report",
];

test("Intervention, PriceOps, and AdLift routes render inside the OpsBoard shell", async ({ page }) => {
  for (const route of ROUTES) {
    const res = await page.goto(route);
    expect(res?.status(), `GET ${route}`).toBeLessThan(400);
    await expect(page.getByTestId("app-shell")).toBeVisible();
    await expect(page.getByTestId("page-header")).toBeVisible();
  }
});

test("E2E-INT-001 intervention smoke shows timeline, conflict guard, stop action, and immature outcome", async ({ page }) => {
  await page.goto("/interventions?selected=int-3002&drawer=case");
  await expect(page.getByTestId("intervention-page")).toBeVisible();
  await expect(page.getByTestId("intervention-timeline")).toContainText("Triggered");
  await expect(page.getByTestId("intervention-timeline")).toContainText("Observation started");
  await expect(page.getByTestId("intervention-conflict-block")).toContainText("Conflict blocks approval execution");
  await expect(page.getByTestId("intervention-approval-panel")).toContainText("停止此干預");
  await expect(page.getByTestId("intervention-approval-panel")).toContainText("decision_id dec-int-3002-pending");

  await page.goto("/interventions?selected=int-3001&drawer=case");
  await expect(page.getByTestId("intervention-drawer")).toContainText("Outcome not mature");
  await expect(page.getByTestId("intervention-drawer")).toContainText("Evidence immature");
});

test("E2E-PRICE-001 PriceOps smoke blocks hard constraint approval and exposes rollback", async ({ page }) => {
  await page.goto("/pricing?selected=price-5102&drawer=plan");
  await expect(page.getByTestId("priceops-page")).toBeVisible();
  await expect(page.getByTestId("pricing-plan-comparison")).toContainText("Current price");
  await expect(page.getByTestId("pricing-plan-comparison")).toContainText("Candidate price");
  await expect(page.getByTestId("priceops-constraint")).toContainText("HARD_CONSTRAINT_FAILED");
  await expect(page.getByTestId("priceops-constraint")).toContainText("Hard constraint failures cannot be approved");
  await expect(page.getByRole("button", { name: "核准此調價方案" })).toBeDisabled();
  await expect(page.getByTestId("priceops-rollback")).toContainText("Rollback");
  await expect(page.getByTestId("priceops-approval-panel")).toContainText("decision_id dec-price-5102-blocked");
});

test("E2E-AD-001 AdLift smoke shows controls, evidence, pre-trend warnings, and contamination", async ({ page }) => {
  await page.goto("/adlift?selected=adlift-8801&drawer=report");
  await expect(page.getByTestId("adlift-report-card")).toContainText("Treatment stores");
  await expect(page.getByTestId("adlift-report-card")).toContainText("Control stores");
  await expect(page.getByTestId("adlift-report-card")).toContainText("iROMI");
  await expect(page.getByTestId("adlift-claim-guard")).toContainText("causal incrementality claim allowed");

  await page.goto("/adlift?selected=adlift-8802&drawer=report");
  await expect(page.getByTestId("adlift-claim-guard")).toContainText("No matched control");
  await expect(page.getByTestId("adlift-claim-guard")).toContainText("Contamination");
  await expect(page.getByTestId("adlift-decision-panel")).toContainText("decision_id dec-adlift-8802-review");

  await page.goto("/adlift?selected=adlift-8803&drawer=report");
  await expect(page.getByTestId("adlift-claim-guard")).toContainText("Pre-trend failed");
});
