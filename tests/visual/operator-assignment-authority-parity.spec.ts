import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { acquireOperatorBackendLock, releaseOperatorBackendLock } from "../e2e/_operatorBackendLock";

// Genuine local fixture API boundary evidence, NOT a Transfer/Pause success
// fixture or complete modal parity acceptance. Do not inject resource IDs.
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.beforeAll(acquireOperatorBackendLock);
test.afterAll(releaseOperatorBackendLock);
const headers = { "x-subject-id": "operator-expansion-manager", "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager", "x-tenant-id": "tenant-a" };

for (const width of [1440, 390]) {
  test(`actual Intake without resource authority rejects Transfer/Pause deep links at ${width}`, async ({ page, request }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
    });
    const api = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
    expect((await request.post(`${api}/api/v1/operator/network-listings/reset`, { headers })).status()).toBe(200);
    expect((await request.get("/api/v1/operator/network-listings/intake", { headers })).status()).toBe(200);
    const writes: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && /\/(assignments|sla-instances)\//.test(req.url())) writes.push(req.url());
    });
    await page.goto("/operator?ws=network");
    await expect(page.getByRole("button", { name: "展店經理", exact: true })).toBeVisible();
    await page.getByTestId("network-tab-1").click();
    await expect(page.getByTestId("intake-inbox-empty")).toBeVisible();
    await page.getByTestId("intake-add-button").click();
    await page.getByTestId("intake-url-input").fill("https://www.synthetic.example/detail-77120345.html");
    await page.getByTestId("intake-submit-button").click();
    await expect(page.getByTestId("intake-detail-stage")).toHaveText("可決策");
    const id = (await page.getByTestId("intake-detail-id").textContent())!.trim();
    const detail = await request.get(`/api/v1/operator/network-listings/intake/${encodeURIComponent(id)}`, { headers });
    expect(detail.status()).toBe(200);
    const record = await detail.json();
    expect(record.id).toBe(id);
    for (const field of ["assignmentId", "assignmentVersion", "slaInstanceId", "slaVersion"]) {
      expect(record[field] ?? null).toBeNull();
    }
    const directory = process.env.NETWORK_PARITY_EVIDENCE_DIR ?? info.outputPath("authority");
    await mkdir(directory, { recursive: true });
    await writeFile(path.join(directory, `actual-intake-${width}.json`), JSON.stringify({ evidenceMode: "local-synthetic-source-real-api", record }, null, 2));
    for (const decision of ["transfer", "pause"]) {
      const url = new URL(page.url());
      url.searchParams.set("dialog", "assignmentSla");
      url.searchParams.set("decision", decision);
      url.searchParams.set("selected", id);
      await page.goto(url.href);
      await expect(page.getByTestId("assignment-action-unavailable")).toBeVisible();
      await expect(page.getByTestId("sla-action-unavailable")).toBeVisible();
      await expect(page.getByTestId("transfer-intake-dialog")).toHaveCount(0);
      await expect(page.getByTestId("pause-sla-dialog")).toHaveCount(0);
      for (const action of ["claim", "transfer", "pause", "resume"]) {
        await expect(page.getByTestId(`asg-btn-${action}`)).toHaveCount(0);
      }
      const geometry = await page.getByTestId("assignment-sla-summary").evaluate((element) => {
        const box = element.getBoundingClientRect();
        return { width: innerWidth, documentScrollWidth: document.documentElement.scrollWidth, summary: { x: box.x, width: box.width, scrollWidth: element.scrollWidth } };
      });
      expect(geometry.documentScrollWidth).toBeLessThanOrEqual(width);
      expect(geometry.summary.x).toBeGreaterThanOrEqual(0);
      expect(geometry.summary.x + geometry.summary.width).toBeLessThanOrEqual(width);
      expect(geometry.summary.scrollWidth).toBeLessThanOrEqual(Math.ceil(geometry.summary.width));
      await writeFile(path.join(directory, `unavailable-${decision}-${width}-geometry.json`), JSON.stringify(geometry, null, 2));
      await page.screenshot({ path: path.join(directory, `unavailable-${decision}-${width}.png`), animations: "disabled" });
    }
    expect(writes).toEqual([]);
    await writeFile(path.join(directory, `refused-writes-${width}.json`), JSON.stringify({ writes, decisions: ["transfer", "pause"] }, null, 2));
  });
}
