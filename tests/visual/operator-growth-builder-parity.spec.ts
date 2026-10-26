import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

// Package 10 prescribes a 610px / 14px dialog. VDC-002/003 require the
// implemented five-step workflow to remain contained and keyboard operable.
// This fixture run supplies local evidence, not authenticated remote approval.
for (const width of [1440, 1024, 390]) {
  test(`Growth draft builder geometry and five-step keyboard flow at ${width}`, async ({ page }, info) => {
    // One case performs five axe scans and captures all five workflow states.
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => sessionStorage.setItem("oday.operator.role", "marketing-manager"));
    let creates = 0;
    page.on("request", request => {
      if (request.method() === "POST" && /\/growth\/actions(?:\?|$)/.test(request.url())) creates += 1;
    });
    await page.goto("/operator?ws=growth");
    const trigger = page.getByTestId("growth-entry-offpeak");
    await trigger.focus(); await trigger.click();
    const dialog = page.getByRole("dialog", { name: "建立 Growth Action 草稿" });
    await expect(dialog.getByLabel("活動名稱")).toBeFocused();
    const results = [];
    for (let step = 1; step <= 5; step += 1) {
      await expect(dialog).toHaveAttribute("data-step", String(step));
      const geometry = await dialog.evaluate(element => {
        const r = element.getBoundingClientRect();
        return { x:r.x, y:r.y, width:r.width, height:r.height, scrollWidth:element.scrollWidth,
          radius:getComputedStyle(element).borderRadius, documentWidth:document.documentElement.scrollWidth, viewport:innerWidth };
      });
      expect(geometry.width).toBe(Math.min(610, width-40));
      expect(geometry.radius).toBe("14px");
      expect(geometry.x).toBeGreaterThanOrEqual(20);
      expect(geometry.x+geometry.width).toBeLessThanOrEqual(width-20);
      expect(geometry.y).toBeGreaterThanOrEqual(20);
      expect(geometry.height).toBeLessThanOrEqual(860);
      expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.width);
      expect(geometry.documentWidth).toBeLessThanOrEqual(width);
      const axe = await new AxeBuilder({ page }).include('[data-testid="growth-draft-modal"]').analyze();
      expect(axe.violations).toEqual([]);
      results.push({ step, geometry, axeViolations:axe.violations });
      const last = step < 5 ? dialog.getByTestId("growth-builder-next") : dialog.getByTestId("growth-draft-submit-approval");
      await last.focus(); await page.keyboard.press("Tab");
      await expect(dialog.getByTestId("growth-draft-close")).toBeFocused();
      await page.keyboard.press("Shift+Tab"); await expect(last).toBeFocused();
      const evidence = process.env.STORE_OPS_PARITY_EVIDENCE_DIR ?? info.outputDir;
      await mkdir(evidence, { recursive:true });
      await page.screenshot({ path:path.join(evidence, `growth-builder-${width}-step-${step}.png`), animations:"disabled" });
      if (step < 5) await dialog.getByTestId("growth-builder-next").click();
    }
    // The supplemental CI server intentionally has no API. Exercise that
    // unavailable branch locally too, rather than only checking a live fixture.
    await page.route("**/api/v1/operator/growth/conflicts/check", route => route.fulfill({ status:503, body:"Synthetic unavailable conflict check" }));
    await dialog.getByTestId("growth-builder-prev").click();
    await dialog.getByTestId("growth-builder-prev").click();
    await dialog.getByTestId("growth-builder-next").click();
    await expect(dialog.getByTestId("growth-conflict-panel")).toContainText("尚未取得伺服器衝突結果");
    const unavailableAxe = await new AxeBuilder({ page }).include('[data-testid="growth-draft-modal"]').analyze();
    expect(unavailableAxe.violations).toEqual([]);
    const evidence = process.env.STORE_OPS_PARITY_EVIDENCE_DIR ?? info.outputDir;
    await writeFile(path.join(evidence, `growth-builder-${width}.json`), JSON.stringify({ results, creates, unavailableAxeViolations:unavailableAxe.violations, unavailableConflictBranchVerified:true, scope:"Local fixture UI; no release approval" }, null, 2));
    expect(creates).toBe(0);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await expect(page).not.toHaveURL(/builder=/);
  });
}


for (const width of [1440, 1024, 390]) {
  test(`Growth draft retries uncertain create and submit without duplicating intent at ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => sessionStorage.setItem("oday.operator.role", "marketing-manager"));
    const createKeys: string[] = [];
    const submitKeys: string[] = [];
    let release!: () => void;
    const responseHeld = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/api/v1/operator/growth/conflicts/check", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({ checks: [], blocked: false, reasons: [] }) }));
    await page.route("**/api/v1/operator/growth/actions", async route => {
      if (route.request().method() !== "POST") return route.fallback();
      createKeys.push(route.request().headers()["idempotency-key"]);
      if (createKeys.length === 1) return route.abort("failed");
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "synthetic-recovery-draft", status: "DRAFT", kind: "offpeak", correlation_id: "synthetic-create" }) });
    });
    await page.route("**/api/v1/operator/growth/actions/synthetic-recovery-draft/submit", async route => {
      submitKeys.push(route.request().headers()["idempotency-key"]);
      if (submitKeys.length === 1) return route.fulfill({ status: 503, body: "Synthetic lost submission acknowledgement" });
      await responseHeld;
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "synthetic-recovery-draft", status: "PENDING_APPROVAL", approval: { id: "synthetic-recovery-approval", ref: "synthetic-recovery-draft", module: "Growth", status: "pending" } }) });
    });
    await page.goto("/operator?ws=growth&builder=offpeak");
    const dialog = page.getByRole("dialog", { name: "建立 Growth Action 草稿" });
    await dialog.getByLabel("活動名稱").fill("Synthetic recovery campaign");
    for (let i = 0; i < 4; i += 1) await dialog.getByTestId("growth-builder-next").click();
    await dialog.getByTestId("growth-draft-submit-approval").click();
    await expect(dialog.getByRole("alert")).toContainText("尚未確認草稿建立結果");
    await dialog.getByTestId("growth-draft-submit-approval").click();
    await expect(dialog.getByRole("alert")).toContainText("synthetic-recovery-draft 已建立");
    await expect(dialog.getByTestId("growth-builder-prev")).toBeDisabled();
    await expect(dialog.getByTestId("growth-draft-submit")).toHaveText("保留草稿");
    const partialAxe = await new AxeBuilder({ page }).include('[data-testid="growth-draft-modal"]').analyze();
    expect(partialAxe.violations).toEqual([]);
    const evidence = process.env.STORE_OPS_PARITY_EVIDENCE_DIR ?? info.outputDir;
    await mkdir(evidence, { recursive: true });
    await page.screenshot({ path: path.join(evidence, `growth-draft-partial-${width}.png`), animations: "disabled" });
    await dialog.getByTestId("growth-draft-submit-approval").click();
    await expect(dialog.getByTestId("growth-builder-submit-status")).toBeFocused();
    await expect(dialog.getByTestId("growth-draft-close")).toBeDisabled();
    await expect(dialog.getByTestId("growth-draft-submit")).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeVisible();
    const pendingAxe = await new AxeBuilder({ page }).include('[data-testid="growth-draft-modal"]').analyze();
    expect(pendingAxe.violations).toEqual([]);
    await page.screenshot({ path: path.join(evidence, `growth-draft-retry-pending-${width}.png`), animations: "disabled" });
    release();
    await expect(dialog).toHaveCount(0);
    expect(createKeys).toHaveLength(2);
    expect(submitKeys).toHaveLength(2);
    expect(createKeys[0]).toBe(createKeys[1]);
    expect(submitKeys[0]).toBe(submitKeys[1]);
    expect(createKeys[0]).not.toBe(submitKeys[0]);
    await writeFile(path.join(evidence, `growth-draft-recovery-${width}.json`), JSON.stringify({
      width, createAttempts: createKeys.length, submitAttempts: submitKeys.length, createRetryKeyStable: true,
      submitRetryKeyStable: true, separatePhaseKeys: true, partialAxeViolations: partialAxe.violations,
      pendingAxeViolations: pendingAxe.violations, scope: "Isolated synthetic browser requests; not deployed UAT",
    }, null, 2));
  });
}
