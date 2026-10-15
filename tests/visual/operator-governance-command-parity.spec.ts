import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import snapshot from "./fixtures/governance-command-snapshot.json";

for (const width of [1440, 1024, 390]) {
  test(`Governance uncertain decision and export preserve intent at ${width}`, async ({ page }, info) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => sessionStorage.setItem("oday.operator.role", "ops-lead"));
    const data = structuredClone(snapshot);
    const decisionKeys: string[] = [];
    const exportKeys: string[] = [];
    let finish!: () => void;
    const heldExport = new Promise<void>(resolve => { finish = resolve; });
    await page.route("**/api/v1/operator/governance/**", async route => {
      const request = route.request();
      if (request.method() !== "POST") return route.fulfill({ contentType: "application/json", body: JSON.stringify(data) });
      const key = request.headers()["idempotency-key"];
      if (new URL(request.url()).pathname.endsWith("/decisions")) {
        decisionKeys.push(key);
        if (decisionKeys.length === 1) return route.fulfill({ status: 503, body: "Synthetic uncertain decision" });
        if (decisionKeys.length === 2) return route.fulfill({ contentType: "application/json", body: JSON.stringify({ approvalId: "other-approval", action: "approve", status: "approved", finalDecision: "Approved" }) });
        const body = request.postDataJSON();
        const approval = data.approvals.find(row => row.id === body.approvalId)!;
        approval.status = "approved";
        return route.fulfill({ contentType: "application/json", body: JSON.stringify({ approvalId: approval.id, action: body.action, status: "approved", finalDecision: "Approved" }) });
      }
      exportKeys.push(key);
      if (exportKeys.length === 1) await heldExport;
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "CANONICAL_EVIDENCE_EXPORT_REQUIRED" }) });
    });
    await page.goto("/operator?ws=govern&entity=ap-store-1042");
    const detail = page.getByRole("article", { name: "Approval detail" });
    await detail.getByRole("button", { name: "核准", exact: true }).click();
    await expect(detail).toContainText("尚未確認決策結果");
    await expect(detail.getByRole("button", { name: "核准", exact: true })).toBeEnabled();
    await detail.getByRole("button", { name: "核准", exact: true }).click();
    await expect(detail).toContainText("尚未確認決策結果");
    await expect(detail.getByRole("button", { name: "核准", exact: true })).toBeEnabled();
    await detail.getByRole("button", { name: "核准", exact: true }).click();
    await expect.poll(() => decisionKeys.length).toBe(3);
    expect(new Set(decisionKeys).size).toBe(1);
    await expect(detail).toContainText("Approved submitted for ap-store-1042");
    await page.goto("/operator?ws=govern&tab=evidencePackage&entity=ap-store-1042");
    await page.getByTestId("governance-tab-evidencePackage").click();
    const exportPanel = page.getByRole("region", { name: "Evidence Package export" });
    await exportPanel.getByTestId("governance-export-button").click();
    await expect(exportPanel.getByLabel("起始日")).toBeDisabled();
    await expect(exportPanel.getByLabel("格式")).toBeDisabled();
    await expect(exportPanel.getByTestId("governance-export-button")).toBeDisabled();
    const pending = await new AxeBuilder({ page }).include('[aria-label="Evidence Package export"]').analyze();
    expect(pending.violations).toEqual([]);
    const evidence = process.env.STORE_OPS_PARITY_EVIDENCE_DIR ?? info.outputDir;
    await mkdir(evidence, { recursive: true });
    await exportPanel.getByTestId("governance-export-button").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(evidence, `governance-export-pending-${width}.png`), animations: "disabled" });
    finish();
    await expect(exportPanel.getByTestId("governance-export-button")).toBeEnabled();
    await expect(page.getByTestId("evidence-package-result")).toHaveCount(0);
    await exportPanel.getByTestId("governance-export-button").click();
    await expect.poll(() => exportKeys.length).toBe(2);
    await expect(exportPanel.getByTestId("governance-export-button")).toBeEnabled();
    expect(exportKeys[0]).toBe(exportKeys[1]);
    await expect(page.getByTestId("evidence-package-result")).toHaveCount(0);
    const failed = await new AxeBuilder({ page }).include('[aria-label="Evidence Package export"]').analyze();
    expect(failed.violations).toEqual([]);
    const geometry = await exportPanel.evaluate(element => ({
      width: element.getBoundingClientRect().width, scrollWidth: element.scrollWidth,
      documentWidth: document.documentElement.scrollWidth, viewport: innerWidth,
    }));
    expect(geometry.documentWidth).toBeLessThanOrEqual(width);
    expect(geometry.scrollWidth).toBeLessThanOrEqual(Math.ceil(geometry.width));
    await page.screenshot({ path: path.join(evidence, `governance-export-unconfirmed-${width}.png`), animations: "disabled" });
    await writeFile(path.join(evidence, `governance-command-${width}.json`), JSON.stringify({
      geometry, decisionAttempts: decisionKeys.length, decisionRetryKeyStable: true,
      exportAttempts: exportKeys.length, exportRetryKeyStable: true, noLocalSuccessAfterApiFailure: true,
      pendingAxeViolations: pending.violations, failedAxeViolations: failed.violations,
      scope: "Synthetic UI fixture; no real-account approval or formal artifact admission",
    }, null, 2));
  });
}
