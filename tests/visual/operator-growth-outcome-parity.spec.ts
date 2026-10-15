import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

// R7 / Package 10: Dialog Growth Outcome is a 540px confirmation dialog.
// VDC-002/003 override the prototype's mobile overflow and keyboard defects.
// These are local fixture geometry/axe checks, not remote visual approval.
for (const width of [1440, 1024, 390]) {
  test(`Growth outcome confirmation follows Package 10 geometry and keyboard flow at ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => sessionStorage.setItem("oday.operator.role", "marketing-manager"));
    let writes = 0;
    page.on("request", request => {
      if (request.method() === "POST" && /\/growth\/actions\/[^/]+\/outcome/.test(request.url())) writes += 1;
    });
    await page.goto("/operator?ws=growth&item=growth-7001");
    const trigger = page.getByTestId("growth-outcome-open");
    await expect(page.getByRole("dialog", { name: "活動成效判斷" })).toHaveCount(0);
    await trigger.focus();
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "活動成效判斷" });
    const reason = dialog.getByLabel("判斷理由（寫入 Decision Log）");
    await expect(reason).toBeFocused();
    await expect(dialog.getByTestId("growth-close-button")).toBeDisabled();
    expect(writes).toBe(0);
    const geometry = await dialog.evaluate(element => {
      const r = element.getBoundingClientRect();
      return { x:r.x, y:r.y, width:r.width, height:r.height, scrollWidth:element.scrollWidth,
        radius:getComputedStyle(element).borderRadius, documentWidth:document.documentElement.scrollWidth, viewport:innerWidth };
    });
    expect(geometry.width).toBe(Math.min(540, width-40));
    expect(geometry.radius).toBe("14px");
    expect(geometry.x).toBeGreaterThanOrEqual(20);
    expect(geometry.x+geometry.width).toBeLessThanOrEqual(width-20);
    expect(geometry.y).toBeGreaterThanOrEqual(20);
    expect(geometry.height).toBeLessThanOrEqual(860);
    expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.width);
    expect(geometry.documentWidth).toBeLessThanOrEqual(width);
    const axe = await new AxeBuilder({ page }).include('[data-testid="growth-outcome-modal"]').analyze();
    expect(axe.violations).toEqual([]);
    const evidence = process.env.STORE_OPS_PARITY_EVIDENCE_DIR ?? info.outputDir;
    await mkdir(evidence,{ recursive:true });
    const screenshot = path.join(evidence,`growth-outcome-${width}.png`);
    await page.screenshot({ path:screenshot, animations:"disabled" });
    await writeFile(path.join(evidence,`growth-outcome-${width}.json`),JSON.stringify({ geometry, axeViolations:axe.violations, writesBeforeConfirmation:writes, scope:"Local fixture UI; no production or remote approval" },null,2));
    await info.attach(`growth-outcome-${width}`,{ path:screenshot, contentType:"image/png" });
    await reason.fill("合成驗證：核對觀測增量與證據後確認判斷。");
    const confirm = dialog.getByTestId("growth-close-button");
    await confirm.focus();
    await page.keyboard.press("Tab");
    await expect(dialog.getByRole("button",{ name:"關閉活動成效判斷" })).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(confirm).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
    expect(writes).toBe(0);
  });
}
