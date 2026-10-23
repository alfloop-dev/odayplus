import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const headers = { "x-subject-id": "operator-expansion-manager", "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager", "x-tenant-id": "tenant-a" };
const api = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const responseEvidence = new Map<string, unknown[]>();
test.afterEach(async ({ page }, info) => {
  const observations = responseEvidence.get(info.title) ?? [];
  await info.attach("intake-network-observations", { body: JSON.stringify(observations, null, 2), contentType: "application/json" });
  if (info.status !== info.expectedStatus) {
    await info.attach("intake-navigation-failure", { body: JSON.stringify({ url: page.url(), observations }), contentType: "application/json" });
  }
});

async function restoredSection(page: Page) {
  const section = page.getByTestId("intake-match-evidence-section");
  await expect(section).toBeInViewport({ ratio: 1 });
  await expect.poll(() => section.evaluate((element) => {
    let ancestor = element.parentElement;
    while (ancestor) {
      if (ancestor.scrollHeight > ancestor.clientHeight + 1 && /auto|scroll/.test(getComputedStyle(ancestor).overflowY)) {
        const top = ancestor === document.scrollingElement ? 0 : ancestor.getBoundingClientRect().top;
        const offset = element.getBoundingClientRect().top - top + ancestor.scrollTop;
        const padding = parseFloat(getComputedStyle(ancestor).scrollPaddingTop) || 0;
        const expected = Math.max(0, Math.min(offset - padding, ancestor.scrollHeight - ancestor.clientHeight));
        return Math.abs(ancestor.scrollTop - expected);
      }
      ancestor = ancestor.parentElement;
    }
    const scroller = document.scrollingElement!;
    const padding = parseFloat(getComputedStyle(scroller).scrollPaddingTop) || 0;
    const expected = Math.max(0, Math.min(element.getBoundingClientRect().top + window.scrollY - padding, scroller.scrollHeight - scroller.clientHeight));
    return Math.abs(window.scrollY - expected);
  })).toBeLessThan(3);
}

for (const width of [1440, 1024, 390]) {
  test(`Intake URL direct reload back forward share and section at ${width}px`, async ({ page, request, browser }, info) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 1000 });
    await page.setExtraHTTPHeaders(headers);
    const observations: unknown[] = [];
    responseEvidence.set(info.title, observations);
    page.on("pageerror", (error) => observations.push({ pageError: error.message }));
    page.on("response", async (response) => {
      if (response.url().includes("network-listings/intake")) {
        observations.push({ url: response.url(), status: response.status(), body: await response.json().catch(() => null) });
      }
    });
    await page.addInitScript(() => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
    });
    expect((await request.post(`${api}/api/v1/operator/network-listings/reset`, { headers })).status()).toBe(200);
    const submitted = await request.post(`${api}/api/v1/operator/network-listings/intake/submit`, { headers: { ...headers, "Idempotency-Key": `intake-url-${width}` }, data: { url: "https://www.synthetic.example/detail-99310418.html", heatZoneId: "HZ-01" } });
    expect(submitted.ok()).toBe(true);
    const record = await submitted.json();
    const ids = [record.id];
    for (const [index, url] of ["https://www.synthetic.example/detail-77120345.html", "https://www.synthetic.example/detail-40028801.html"].entries()) {
      const response = await request.post(`${api}/api/v1/operator/network-listings/intake/submit`, {
        headers: { ...headers, "Idempotency-Key": `intake-url-${width}-${index}` }, data: { url, heatZoneId: "HZ-01" },
      });
      expect(response.ok()).toBe(true); ids.push((await response.json()).id);
    }
    expect(record.submittedBy).toBe(headers["x-subject-id"]);
    const authorizedPage = await request.get(`${api}/api/v1/operator/network-listings/intake`, { headers });
    expect(authorizedPage.ok()).toBe(true);
    const visibleRecord = (await authorizedPage.json()).items.find((item: { id: string }) => item.id === record.id);
    expect(visibleRecord).toBeDefined();
    expect(typeof visibleRecord.masked).toBe("boolean");
    const maskedFilter = String(visibleRecord.masked);
    const query = `ws=network&tab=radar&savedView=needsReview&intakeStage=NEEDS_REVIEW&sortBy=id&sortOrder=asc&pageSize=25&extra=a&extra=b&source=shell-context&sourceId=${encodeURIComponent(record.sourceId)}&submittedBy=${encodeURIComponent(record.submittedBy)}&masked=${maskedFilter}`;
    await page.goto(`/operator?${query}`);
    const row = page.getByTestId(`intake-inbox-row-${record.id}`);
    await expect(row).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("intake-filter-stage")).toHaveValue("NEEDS_REVIEW");
    await expect(page.getByTestId("intake-page-size-select")).toHaveValue("25");
    await expect(page.getByTestId("intake-filter-source")).toHaveValue(record.sourceId);
    await expect(page.getByTestId("intake-filter-submitted-by")).toHaveValue(record.submittedBy);
    await expect(page.getByTestId("intake-filter-masked")).toHaveValue(maskedFilter);
    await page.reload();
    await expect(row).toBeVisible();
    await expect(page.getByTestId("intake-filter-masked")).toHaveValue(maskedFilter);
    await page.getByTestId("intake-filter-masked").selectOption("");
    await page.getByTestId("intake-tab-all").click();
    await page.getByTestId("intake-filter-stage").selectOption("");
    const rows = page.locator('[data-testid^="intake-inbox-row-"]');
    await expect(rows).toHaveCount(3);
    await expect.poll(() => rows.evaluateAll((elements) => elements.map((element) => element.getAttribute("data-testid")!.replace("intake-inbox-row-", "")))).toEqual([...ids].sort());
    await page.getByTestId("intake-table").getByRole("button", { name: /收件 ID/ }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get("sortOrder") ?? "desc").toBe("desc");
    await page.reload();
    await expect.poll(() => rows.evaluateAll((elements) => elements.map((element) => element.getAttribute("data-testid")!.replace("intake-inbox-row-", "")))).toEqual([...ids].sort().reverse());
    await page.getByTestId("intake-view-mode-map").click();
    await expect(page).toHaveURL(/viewMode=map/);
    await page.reload();
    await expect(page.getByTestId("intake-map-view-panel")).toBeVisible();
    await page.goBack();
    await expect(page.getByTestId("intake-table")).toBeVisible();
    await page.goForward();
    await expect(page.getByTestId("intake-map-view-panel")).toBeVisible();
    await page.getByTestId("intake-view-mode-list").click();
    await page.getByTestId(`intake-row-open-${record.id}`).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("intake-detail-dialog")).toBeVisible();
    await page.getByTestId("intake-match-evidence-section").click();
    await expect(page).toHaveURL(/section=identity/);
    const share = page.url();
    expect(new URL(share).searchParams.getAll("extra")).toEqual(["a", "b"]);
    expect(new URL(share).searchParams.get("source")).toBe("shell-context");
    expect(new URL(share).searchParams.get("sourceId")).toBe(record.sourceId);
    expect(new URL(share).searchParams.has("masked")).toBe(false);
    await page.reload();
    await expect(page.getByTestId("intake-detail-dialog")).toBeVisible();
    await restoredSection(page);
    const context = await browser.newContext({ viewport: { width, height: 1000 }, extraHTTPHeaders: headers });
    await context.addInitScript(() => { sessionStorage.setItem("oday.operator.role", "expansion-manager"); sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager"); });
    const shared = await context.newPage();
    await shared.goto(share);
    await expect(shared.getByTestId("intake-detail-dialog")).toContainText(record.id);
    await restoredSection(shared);
    await context.close();
    await page.goBack();
    await expect(page.getByTestId("intake-inbox-view")).toBeVisible();
    await page.goForward();
    await expect(page.getByTestId("intake-detail-dialog")).toContainText(record.id);
    const stepper = page.getByTestId("timeline-stepper");
    await stepper.focus();
    await expect(stepper).toBeFocused();
    if (width === 390) {
      await page.keyboard.press("ArrowRight");
      await expect.poll(() => stepper.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
    }
    const axe = await new AxeBuilder({ page }).include('[data-testid="intake-detail-dialog"]').analyze();
    expect(axe.violations).toEqual([]);
    const geometry = await page.evaluate(() => ({ viewport: innerWidth, documentWidth: document.documentElement.scrollWidth }));
    expect(geometry.documentWidth).toBeLessThanOrEqual(width);
    const evidence = process.env.INTAKE_URL_EVIDENCE_DIR ?? info.outputDir;
    await mkdir(evidence, { recursive: true });
    await page.screenshot({ path: path.join(evidence, `intake-url-detail-${width}.png`), fullPage: true, animations: "disabled" });
    await page.getByTestId("intake-return-button").click();
    await expect(row).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("intake-filter-stage")).toHaveValue("");
    const inboxAxe = await new AxeBuilder({ page }).include('[data-testid="intake-inbox-view"]').analyze();
    expect(inboxAxe.violations).toEqual([]);
    const inboxGeometry = await page.evaluate(() => ({ viewport: innerWidth, documentWidth: document.documentElement.scrollWidth,
      filters: [...document.querySelectorAll<HTMLElement>('[data-testid^="intake-filter-"]')].map((element) => {
        const rect = element.getBoundingClientRect(); return { id: element.dataset.testid, left: rect.left, right: rect.right };
      }) }));
    expect(inboxGeometry.documentWidth).toBeLessThanOrEqual(width);
    const paginationTops = await page.getByTestId("intake-pagination").locator("select,button").evaluateAll(
      (elements) => elements.map((element) => element.getBoundingClientRect().top),
    );
    expect(Math.max(...paginationTops) - Math.min(...paginationTops)).toBeLessThan(3);
    for (const id of ["intake-prev-page", "intake-next-page"]) {
      const lineCount = await page.getByTestId(id).evaluate((button) => {
        const range = document.createRange(); range.selectNodeContents(button);
        return new Set([...range.getClientRects()].map((rect) => rect.top)).size;
      });
      expect(lineCount, `${id} must keep its label on one line`).toBe(1);
    }
    for (const filter of inboxGeometry.filters) { expect(filter.left).toBeGreaterThanOrEqual(0); expect(filter.right).toBeLessThanOrEqual(width); }
    await page.screenshot({ path: path.join(evidence, `intake-url-inbox-${width}.png`), fullPage: true, animations: "disabled" });
    await page.goto(`/intake/${record.id}?${query}&selected=OTHER&dialog=detail&section=audit`);
    await expect(page.getByTestId("intake-detail-dialog")).toContainText(record.id);
    await page.goto(`/operator?${query}#intake/${encodeURIComponent(record.id)}`);
    await expect(page.getByTestId("intake-detail-dialog")).toContainText(record.id);
    await expect(page).toHaveURL(/dialog=detail/);
    expect(new URL(page.url()).hash).toBe("");
    await page.goto(`/operator?${query}&selected=A&selected=B&dialog=detail`);
    await expect(page.getByTestId("intake-direct-detail-error")).toContainText("連結未指定唯一");
    await expect(page.getByTestId("intake-detail-dialog")).toHaveCount(0);
    await writeFile(path.join(evidence, `intake-url-${width}.json`), JSON.stringify({ scope: "LOOPBACK_SYNTHETIC_NAVIGATION_NOT_REAL_ROLE_OR_RELEASE_APPROVAL", width, recordId: record.id, sortedRecordIds: ids, share, geometry, inboxGeometry, inboxAxeViolations: inboxAxe.violations, axeViolations: axe.violations, observations, checks: ["direct", "reload", "back", "forward", "fresh-context-share", "actual-three-record-sort", "source-submitter-masked-filter-restoration", "detail-section", "repeated-unrelated-params", "path-priority", "legacy-hash", "ambiguous-id-refusal"] }, null, 2));
  });
}
