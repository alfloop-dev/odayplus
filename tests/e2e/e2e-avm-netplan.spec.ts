import { expect, test } from "@playwright/test";

const ROUTES = [
  "/avm",
  "/w/dealroom/cases?selected=vc-5101&drawer=case",
  "/w/dealroom/cases/vc-5101",
  "/w/dealroom/cases/vc-5102",
  "/netplan",
  "/w/network/scenarios?selected=np-6201&drawer=scenario",
  "/w/network/scenarios/np-6201",
  "/w/network/scenarios/np-6203",
];

test("AVM and NetPlan routes render inside the OpsBoard shell", async ({ page }) => {
  for (const route of ROUTES) {
    const res = await page.goto(route);
    expect(res?.status(), `GET ${route}`).toBeLessThan(400);
    await expect(page.getByTestId("app-shell")).toBeVisible();
    await expect(page.getByTestId("page-header")).toBeVisible();
  }
});

test("DealRoomAVM case list scans status, masked reserve/asking, approval and DataRoom", async ({ page }) => {
  await page.goto("/w/dealroom/cases?selected=vc-5101&drawer=case");
  await expect(page.getByTestId("avm-cases-page")).toContainText("DATAROOM_READY");
  await expect(page.getByTestId("avm-cases-page")).toContainText("Reserve / Asking");
  await expect(page.getByTestId("avm-case-drawer")).toContainText("依權限遮罩");
  await expect(page.getByTestId("avm-case-drawer")).toContainText("開啟案件詳情");
});

test("DealRoomAVM case detail shows three-lens chart, finance approval, and DataRoom checklist", async ({ page }) => {
  await page.goto("/w/dealroom/cases/vc-5101");
  await expect(page.getByTestId("avm-case-detail-page")).toBeVisible();
  await expect(page.getByTestId("avm-normalized-margin")).toContainText("normalized_gm");
  const chart = page.getByTestId("valuation-range-chart");
  await expect(chart).toContainText("Three-Lens Valuation");
  await expect(chart).toContainText("blended");
  await expect(chart).toContainText("永不只顯示 P50");
  await expect(page.getByTestId("avm-approval-panel")).toContainText("never optimistic");
  await expect(page.getByText("decision_id dec-avm-5101")).toBeVisible();
  await expect(page.getByTestId("avm-dataroom")).toContainText("Valuation card");
  await expect(page.getByTestId("avm-dataroom")).toContainText("avm.dataroom_exported.v1");
});

test("DealRoomAVM blocks DataRoom before finance approval and allows approval at REVIEW_REQUIRED", async ({ page }) => {
  await page.goto("/w/dealroom/cases/vc-5102");
  await expect(page.getByTestId("avm-dataroom")).toContainText("不得建立 DataRoom 或匯出");
  await expect(page.getByTestId("avm-approval-panel").getByRole("button")).toBeEnabled();
});

test("NetPlan scenario list scans status, solver, objective, action counts, approval", async ({ page }) => {
  await page.goto("/w/network/scenarios?selected=np-6201&drawer=scenario");
  await expect(page.getByTestId("netplan-scenarios-page")).toContainText("OPEN 1");
  await expect(page.getByTestId("netplan-scenarios-page")).toContainText("optimal");
  await expect(page.getByTestId("netplan-scenario-drawer")).toContainText("開啟情境詳情");
});

test("NetPlan feasible detail shows plan card, binding constraints, alternatives, and outcome variance", async ({ page }) => {
  await page.goto("/w/network/scenarios/np-6201");
  const card = page.getByTestId("netplan-scenario-card");
  await expect(card).toContainText("action_counts");
  await expect(card).toContainText("OPEN 1");
  await expect(card).toContainText("Binding constraints");
  await expect(card).toContainText("min_expected_gross_margin");
  await expect(card).toContainText("Alternatives");
  await expect(card).toContainText("netplan-exhaustive-cpsat-compatible-v1");
  await expect(page.getByTestId("netplan-execution")).toContainText("Outcome");
  await expect(page.getByTestId("netplan-execution")).toContainText("Variance");
  await expect(page.getByTestId("netplan-approval-panel")).toContainText("never optimistic");
  await expect(page.getByText("approval_id apr-6201")).toBeVisible();
});

test("NetPlan infeasible detail shows diagnosis and never auto-relaxes constraints", async ({ page }) => {
  await page.goto("/w/network/scenarios/np-6203");
  const diag = page.getByTestId("netplan-infeasibility");
  await expect(diag).toContainText("Infeasibility Diagnosis");
  await expect(diag).toContainText("UI 不自動放寬任何限制");
  await expect(diag).toContainText("required_relaxation");
  await expect(diag).toContainText("business_impact");
  await expect(diag).toContainText("suggested_action");
  await expect(page.getByTestId("netplan-approval-panel")).toContainText("infeasible 不顯示核准動作");
});
