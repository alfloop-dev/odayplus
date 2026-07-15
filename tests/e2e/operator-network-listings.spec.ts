import { expect, request as playwrightRequest, test } from "@playwright/test";
import {
  acquireOperatorBackendLock,
  releaseOperatorBackendLock,
} from "./_operatorBackendLock";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const NETWORK_HEADERS = {
  "x-subject-id": "operator-expansion-manager",
  "x-roles": "expansion_user",
  "x-operator-role": "expansion-manager",
  "x-tenant-id": "tenant-a",
};

test.describe.configure({ mode: "serial" });

// This file's reset wipes the shared operator backend, which other spec files
// also reset; hold the lock for its whole run. See ./_operatorBackendLock.ts.
test.beforeAll(async () => {
  await acquireOperatorBackendLock();
});

test.afterAll(() => {
  releaseOperatorBackendLock();
});

test.describe("ODP-OC-R4-005 Network Listing Radar", () => {
  test.beforeEach(async () => {
    const api = await apiContext();
    const reset = await api.post("/api/v1/operator/network-listings/reset");
    expect(reset.status()).toBe(200);
    await api.dispose();
  });

  test("HZ-01 to L-2024 to CS-1001 completes through UI and API", async ({ page }) => {
    await page.goto("/operator?ws=network");
    await expect(page.getByTestId("network-find-areas-workspace")).toBeVisible();
    await expect(page.getByTestId("network-expansion-stepper")).toBeVisible();
    await expect(page.getByTestId("network-step-find")).toContainText("completed");
    await expect(page.getByTestId("network-step-radar")).toContainText("current");
    await expect(page.getByTestId("network-step-sitescore")).toContainText("blocked");

    await page.getByTestId("network-tab-1").click();
    await expect(page.getByTestId("listing-zone-filter-chip")).toContainText("HZ-01");
    await expect(page.getByTestId("network-listing-table")).toContainText("L-2024", { timeout: 15_000 });
    await expect(page.getByTestId("listing-row-L-2024")).toContainText("Clean");

    await page.getByTestId("convert-L-2024").click();
    await expect(page.getByTestId("network-panel-candidates")).toBeVisible();
    await expect(page.getByTestId("network-candidate-table")).toContainText("CS-1001");
    await expect(page.getByTestId("network-candidate-table")).toContainText("SiteScore v2.3");
    await expect(page.getByTestId("network-step-candidate")).toContainText("current");
    await expect(page.getByTestId("network-step-sitescore")).toContainText("next");

    const api = await apiContext();
    const snapshot = await api.get("/api/v1/operator/network-listings");
    expect(snapshot.status()).toBe(200);
    const body = await snapshot.json();
    const cs1001 = body.candidates.filter((candidate: { id: string }) => candidate.id === "CS-1001");
    expect(cs1001).toHaveLength(1);
    const listing = body.listings.find((item: { id: string }) => item.id === "L-2024");
    expect(listing).toMatchObject({ status: "candidate", candidateId: "CS-1001" });
    await api.dispose();
  });

  test("L-2029 merge retains evidence and L-2030 archives with reason", async ({ page }) => {
    await page.goto("/operator?ws=network");
    await page.getByTestId("network-tab-1").click();
    await expect(page.getByTestId("network-listing-table")).toContainText("L-2024", { timeout: 15_000 });

    await page.getByTestId("listing-filter-all").click();
    await expect(page.getByTestId("listing-row-L-2029")).toContainText("EV-L-2029-RAW-591");

    // ODP-OC-R5-011: merge is a high-impact write and now discloses its risk
    // before committing. Playwright auto-dismisses dialogs, which would cancel
    // the merge, so accept it explicitly and assert the operator was told what
    // the merge does.
    const riskTexts: string[] = [];
    page.on("dialog", (dialog) => {
      riskTexts.push(dialog.message());
      void dialog.accept();
    });
    await page.getByTestId("merge-L-2029").click();
    await expect
      .poll(() => riskTexts.join(" "))
      .toContain("marks L-2029 a duplicate of L-2025");
    await expect(page.getByTestId("listing-row-L-2029")).toContainText("merged into L-2025");
    await expect(page.getByTestId("listing-row-L-2025")).toContainText("EV-L-2029-RAW-591");

    await page.getByTestId("archive-L-2030").click();
    await expect(page.getByTestId("listing-row-L-2030")).toContainText("封存");
    await expect(page.getByTestId("listing-row-L-2030")).toContainText("Hard-rule archive");

    const api = await apiContext();
    const snapshot = await api.get("/api/v1/operator/network-listings");
    const body = await snapshot.json();
    const source = body.listings.find((item: { id: string }) => item.id === "L-2029");
    const target = body.listings.find((item: { id: string }) => item.id === "L-2025");
    const archived = body.listings.find((item: { id: string }) => item.id === "L-2030");
    expect(source.sourceEvidence).toContain("EV-L-2029-RAW-591");
    expect(source.mergedIntoId).toBe("L-2025");
    expect(target.sourceEvidence).toContain("EV-L-2029-RAW-591");
    expect(archived.status).toBe("archived");
    expect(archived.archivedReason).toContain("Hard-rule archive");
    await api.dispose();
  });

  test("real HeatZone map stays nonblank and synchronized to selected zone and lens", async ({ page }) => {
    await page.goto("/operator?ws=network");
    await expect(page.getByTestId("network-panel-find-areas")).toBeVisible();
    await expect(page.getByTestId("heat-zone-map")).toHaveAttribute("data-selected-zone", "HZ-01", { timeout: 15_000 });
    await expect.poll(async () => page.locator(".maplibregl-canvas").count()).toBeGreaterThan(0);
    await expect.poll(async () => canvasHasVisiblePixels(page, ".maplibregl-canvas")).toBe(true);

    await page.getByRole("button", { name: /Fit Brand Fit/ }).click();
    await page.getByRole("button", { name: /HZ-02 ·/ }).click();
    await expect(page.getByTestId("heat-zone-map")).toHaveAttribute("data-selected-zone", "HZ-02");
    await page.getByTestId("network-tab-1").click();
    await expect(page.getByTestId("listing-zone-filter-chip")).toContainText("HZ-02");
  });
});

async function apiContext() {
  return playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: NETWORK_HEADERS,
  });
}

async function canvasHasVisiblePixels(page: import("@playwright/test").Page, selector: string) {
  return page.locator(selector).first().evaluate((canvas) => {
    const source = canvas as HTMLCanvasElement;
    const context = source.getContext("webgl2") ?? source.getContext("webgl");
    if (!context || source.width === 0 || source.height === 0) return false;

    const width = Math.min(source.width, 120);
    const height = Math.min(source.height, 80);
    const image = new Uint8Array(width * height * 4);
    context.readPixels(0, 0, width, height, context.RGBA, context.UNSIGNED_BYTE, image);
    for (let index = 0; index < image.length; index += 4) {
      const alpha = image[index + 3];
      const red = image[index];
      const green = image[index + 1];
      const blue = image[index + 2];
      if (alpha > 0 && (red !== 255 || green !== 255 || blue !== 255)) {
        return true;
      }
    }
    return false;
  });
}
