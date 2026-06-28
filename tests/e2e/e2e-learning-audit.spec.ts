import { expect, test } from "@playwright/test";

const ROUTES = [
  "/learning",
  "/w/ai/models?selected=price-elasticity",
  "/w/ai/models/sitescore-propensity",
  "/w/ai/models/sitescore-propensity/2.4.0",
  "/w/ai/releases",
  "/w/ai/releases/rel-lh-240-canary",
  "/audit",
  "/w/audit/decisions?selected=decision-netplan-404",
  "/w/audit/decisions/decision-lh-240",
  "/w/audit/evidence",
  "/admin/audit",
];

test("Learning Hub and Audit routes render inside the OpsBoard shell", async ({ page }) => {
  for (const route of ROUTES) {
    const res = await page.goto(route);
    expect(res?.status(), `GET ${route}`).toBeLessThan(400);
    await expect(page.getByTestId("app-shell")).toBeVisible();
    await expect(page.getByTestId("page-header")).toBeVisible();
  }
});

test("Learning Hub model registry exposes release governance gates", async ({ page }) => {
  await page.goto("/w/ai/models?selected=price-elasticity");

  await expect(page.getByTestId("model-registry-table")).toContainText("sitescore-propensity");
  await expect(page.getByTestId("model-registry-table")).toContainText("blocked");
  await expect(page.getByTestId("model-drawer")).toContainText("ModelReleaseCard");
  await expect(page.getByTestId("release-gate-checklist")).toContainText("Validation passed");
  await expect(page.getByTestId("release-gate-checklist")).toContainText("Release blocked");
  await expect(page.getByText("No optimistic stage changes")).toBeVisible();
});

test("Learning Hub model detail separates model card, validation, release, rollback, and audit", async ({ page }) => {
  await page.goto("/w/ai/models/sitescore-propensity/2.4.0");

  await expect(page.getByTestId("model-summary")).toContainText("Rollback target");
  await expect(page.getByTestId("model-card-section")).toContainText("privacy/security");
  await expect(page.getByTestId("validation-panel")).toContainText("precision_at_50");
  await expect(page.getByTestId("release-controller")).toContainText("Affected modules");
  await expect(page.getByTestId("rollback-console")).toContainText("rollback reason");
  await expect(page.getByTestId("learning-audit-metadata")).toContainText("correlation_id");
});

test("Audit decision detail uses the fixed timeline, Decision Card, metadata, and export flow", async ({ page }) => {
  await page.goto("/w/audit/decisions/decision-netplan-404");

  await expect(page.getByTestId("audit-summary")).toContainText("override");
  await expect(page.getByTestId("decision-card")).toContainText("System Recommendation");
  await expect(page.getByTestId("decision-card")).toContainText("Human Decision Status");
  await expect(page.getByTestId("override-comparison")).toContainText("override_reason");
  await expect(page.getByTestId("decision-audit-timeline")).toContainText("Prediction generated");
  await expect(page.getByTestId("decision-audit-timeline")).toContainText("Feedback written to label registry");
  await expect(page.getByTestId("audit-metadata-panel")).toContainText("feature_snapshot_time");
  await expect(page.getByTestId("evidence-export-panel")).toContainText("RESTRICTED");
  await expect(page.getByTestId("evidence-export-panel")).toContainText("no optimistic export state");
});

test("Audit evidence matrix shows subsidy evidence completeness and batch export constraints", async ({ page }) => {
  await page.goto("/w/audit/evidence");

  await expect(page.getByTestId("subsidy-evidence-matrix")).toContainText("Urban Growth Subsidy");
  await expect(page.getByTestId("subsidy-evidence-matrix")).toContainText("待補");
  await expect(page.getByTestId("subsidy-evidence-matrix")).toContainText("corr-avm-118");
  await expect(page.getByTestId("batch-export-panel")).toContainText("no silent truncation");
  await expect(page.getByTestId("batch-export-panel")).toContainText("masked fields");
});
