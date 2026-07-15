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
    await openRadarAsExpansionManager(page);
    await expect(page.getByTestId("listing-row-L-2029")).toContainText("EV-L-2029-RAW-591");

    // ODP-OC-R5-011: merge is a high-impact write committed through a real
    // confirmation surface — the operator writes their own reason and must
    // acknowledge the exact risk summary the dialog rendered.
    await page.getByTestId("merge-L-2029").click();
    await expect(page.getByTestId("listing-merge-dialog")).toBeVisible();
    await expect(page.getByTestId("listing-merge-risk-summary")).toContainText(
      "將把 L-2029 標記為 L-2025 的重複",
    );

    await page.getByTestId("listing-merge-reason").fill(OPERATOR_REASON);
    await page.getByTestId("listing-merge-risk-ack").click();
    await page.getByTestId("listing-merge-submit").click();

    await expect(page.getByTestId("listing-merge-dialog")).toBeHidden();
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

    // The reason the operator typed and the risk summary they acknowledged both
    // reach the audit event — not a default invented by the UI.
    const mergeAudit = body.auditEvents.find(
      (event: { action: string; targetId: string }) =>
        event.action === "listing.merge" && event.targetId === "L-2025",
    );
    expect(mergeAudit).toBeTruthy();
    expect(mergeAudit.metadata.reason).toBe(OPERATOR_REASON);
    expect(mergeAudit.metadata.riskSummary).toContain("將把 L-2029 標記為 L-2025 的重複");
    expect(mergeAudit.metadata.riskAcknowledged).toBe(true);
    expect(mergeAudit.correlationId).toBeTruthy();
    expect(source.mergeReason).toBe(OPERATOR_REASON);
    await api.dispose();
  });

  test("merge writes nothing when cancelled, unacknowledged, or missing a reason", async ({
    page,
  }) => {
    await openRadarAsExpansionManager(page);

    // 1. Cancel — the dialog closes and nothing is written.
    await page.getByTestId("merge-L-2029").click();
    await page.getByTestId("listing-merge-reason").fill(OPERATOR_REASON);
    await page.getByTestId("listing-merge-risk-ack").click();
    await page.getByTestId("listing-merge-cancel").click();
    await expect(page.getByTestId("listing-merge-dialog")).toBeHidden();
    await expect(page.getByTestId("listing-row-L-2029")).not.toContainText("merged into");

    // 2. Reason supplied but risk NOT acknowledged — submit is refused locally.
    await page.getByTestId("merge-L-2029").click();
    await page.getByTestId("listing-merge-reason").fill(OPERATOR_REASON);
    await page.getByTestId("listing-merge-submit").click();
    await expect(page.getByTestId("listing-merge-error")).toContainText("請先勾選確認");
    await expect(page.getByTestId("listing-merge-dialog")).toBeVisible();

    // 3. Acknowledged but no reason — still refused.
    await page.getByTestId("listing-merge-reason").fill("");
    await page.getByTestId("listing-merge-risk-ack").click();
    await page.getByTestId("listing-merge-submit").click();
    await expect(page.getByTestId("listing-merge-error")).toContainText("合併原因必填");
    await expect(page.getByTestId("listing-merge-dialog")).toBeVisible();

    await page.getByTestId("listing-merge-close").click();
    await expect(page.getByTestId("listing-merge-dialog")).toBeHidden();

    // The durable state never moved: no merge, no audit event, no reason.
    const api = await apiContext();
    const snapshot = await api.get("/api/v1/operator/network-listings");
    const body = await snapshot.json();
    const source = body.listings.find((item: { id: string }) => item.id === "L-2029");
    expect(source.mergedIntoId).toBeFalsy();
    expect(source.mergeReason).toBeFalsy();
    expect(source.status).not.toBe("duplicate");
    expect(
      body.auditEvents.filter((event: { action: string }) => event.action === "listing.merge"),
    ).toHaveLength(0);
    await api.dispose();
  });

  test("a role without listing:UPDATE is not offered the merge action", async ({ page }) => {
    // ops-lead maps to operations_manager, which holds no listing grant, so the
    // console must not offer a merge that the server would refuse.
    await page.addInitScript(() => {
      window.sessionStorage.setItem("oday.operator.role", "ops-lead");
    });
    await page.goto("/operator?ws=network");
    await page.getByTestId("network-tab-1").click();
    await expect(page.getByTestId("network-listing-table")).toContainText("L-2024", {
      timeout: 15_000,
    });
    await page.getByTestId("listing-filter-all").click();
    await expect(page.getByTestId("listing-row-L-2029")).toBeVisible();
    await expect(page.getByTestId("merge-L-2029")).toHaveCount(0);
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

/** The operator's own words — never a UI default. Asserted verbatim in audit. */
const OPERATOR_REASON = "同地址同租金，現場確認 L-2029 與 L-2025 為同一物件的重複刊登。";

/**
 * Open Listing Radar as 展店經理 — the only console role mapping to an API role
 * (expansion_user) that holds listing:UPDATE, which merge requires.
 */
async function openRadarAsExpansionManager(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    window.sessionStorage.setItem("oday.operator.role", "expansion-manager");
  });
  await page.goto("/operator?ws=network");
  await page.getByTestId("network-tab-1").click();
  await expect(page.getByTestId("network-listing-table")).toContainText("L-2024", {
    timeout: 15_000,
  });
  await page.getByTestId("listing-filter-all").click();
}

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
