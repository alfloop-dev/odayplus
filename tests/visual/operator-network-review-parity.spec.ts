import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

// R7 520px decision surface, all four decision forms at three viewports.
// Synthetic local geometry/keyboard evidence never grants a live decision.
for (const width of [1440,1024,390]) {
  for (const action of ["go","wait","return","reject"]) {
    test(`Review ${action} dialog geometry and keyboard at ${width}`, async ({page},info) => {
      await page.setViewportSize({width,height:900});
      await page.addInitScript(() => sessionStorage.setItem("oday.operator.role","expansion-manager"));
      let writes=0;
      page.on("request",request => { if(request.method()==="POST" && /\/network-reviews\/[^/]+\/decide/.test(request.url()))writes+=1; });
      await page.goto("/operator?ws=network&tab=review");
      await expect(page.getByTestId("network-panel-review")).toBeVisible();
      // RV-701 exists in both the dedicated API queue and the archived fixture
      // queue. The supplemental CI server intentionally runs without an API.
      await page.getByTestId("review-card-RV-701").click();
      const trigger=page.getByTestId(`review-btn-${action}-RV-701`);
      await trigger.focus(); await trigger.click();
      const dialog=page.getByRole("dialog");
      const reason=dialog.getByLabel("決策原因（必填 — 寫入 Decision Log）");
      await expect(reason).toBeFocused();
      if(action==="wait")await expect(dialog.getByLabel("通過條件（必填 — 條件達成後可重評為 GO）")).toBeVisible();
      if(action==="return")await expect(dialog.getByLabel("需補資料（必填 — 以「、」分隔，會同步至 Candidate 缺資料清單）")).toBeVisible();
      const geometry=await dialog.evaluate(element => {
        const r=element.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,scrollWidth:element.scrollWidth,
          radius:getComputedStyle(element).borderRadius,documentWidth:document.documentElement.scrollWidth,viewport:innerWidth};
      });
      expect(geometry.width).toBe(Math.min(520,width-40));
      expect(geometry.radius).toBe("14px");
      expect(geometry.x).toBeGreaterThanOrEqual(20);
      expect(geometry.x+geometry.width).toBeLessThanOrEqual(width-20);
      expect(geometry.y).toBeGreaterThanOrEqual(20);
      expect(geometry.height).toBeLessThanOrEqual(860);
      expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.width);
      expect(geometry.documentWidth).toBeLessThanOrEqual(width);
      const axe=await new AxeBuilder({page}).include('[data-testid="review-decision-dialog"]').analyze();
      expect(axe.violations).toEqual([]);
      const submit=dialog.getByTestId("review-decision-submit");
      await submit.focus();await page.keyboard.press("Tab");
      await expect(dialog.getByTestId("review-decision-close")).toBeFocused();
      await page.keyboard.press("Shift+Tab");await expect(submit).toBeFocused();
      const evidence=process.env.STORE_OPS_PARITY_EVIDENCE_DIR??info.outputDir;
      await mkdir(evidence,{recursive:true});
      await page.screenshot({path:path.join(evidence,`network-review-${action}-${width}.png`),animations:"disabled"});
      await writeFile(path.join(evidence,`network-review-${action}-${width}.json`),JSON.stringify({geometry,axeViolations:axe.violations,writesBeforeConfirmation:writes,scope:"Local fixture; not remote visual approval"},null,2));
      await reason.fill("Synthetic local entered reason remains until explicit cancellation.");
      await page.keyboard.press("Escape");await expect(dialog).toBeVisible();
      await expect(dialog.getByRole("status")).toHaveText("決策確認視窗需以「取消」按鈕關閉（防誤觸 Esc）");
      await expect(reason).toHaveValue("Synthetic local entered reason remains until explicit cancellation.");
      expect(writes).toBe(0);
      await dialog.getByRole("button",{name:"取消",exact:true}).click();
      await expect(dialog).toHaveCount(0);await expect(trigger).toBeFocused();expect(writes).toBe(0);
    });
  }
}
