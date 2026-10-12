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
