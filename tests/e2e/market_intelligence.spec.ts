import { expect, test } from "@playwright/test";

test.describe("Market Intelligence Features", () => {
  test("Market Explorer, Site Dossier, and Candidate Compare render without crashing", async ({ page }) => {
    // Market intelligence is an operator network panel, not a standalone route.
    await page.goto(
      "/operator?ws=network&panel=market-intelligence&site_id=site-1&site_ids=site-1,site-2",
    );

    // The page should have the headers
    await expect(page.locator("h1")).toHaveText("Market Intelligence");

    // Since the backend might not have mocked data for these endpoints,
    // the components might show "No cells found" or "No evidence" or throw errors.
    // We just verify they are mounted and not completely crashing the app.
    const marketExplorer = page.getByTestId("market-explorer").or(page.getByTestId("market-explorer-empty")).or(page.getByTestId("market-explorer-error"));
    await expect(marketExplorer).toBeVisible();

    const siteDossier = page.getByTestId("site-dossier").or(page.getByTestId("site-dossier-empty")).or(page.getByTestId("site-dossier-error"));
    await expect(siteDossier).toBeVisible();

    const candidateCompare = page.getByTestId("candidate-compare").or(page.getByTestId("candidate-compare-empty")).or(page.getByTestId("candidate-compare-error"));
    await expect(candidateCompare).toBeVisible();
  });
});
