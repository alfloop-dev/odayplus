import { expect, test } from "@playwright/test";

test("authorized live workspace disclosure before approval", async ({ page }) => {
  const origin = process.env.ODP_LIVE_UI_BASE_URL ?? "";
  const path = process.env.ODP_LIVE_UI_PATH ?? "";
  const username = process.env.ODP_LIVE_UI_USERNAME ?? "";
  const password = process.env.ODP_LIVE_UI_PASSWORD ?? "";
  if (!origin || !path || !username || !password) throw new Error("Live UI admission missing");
  const base = new URL(origin);
  const target = new URL(path, base);
  if (target.origin !== base.origin || !path.startsWith("/operator?")) {
    throw new Error("Live UI target must be the admitted operator origin");
  }
  const assertions: { action: string; selector: string; text: string }[] = JSON.parse(
    process.env.ODP_LIVE_UI_ASSERTIONS ?? "[]",
  );
  if (!assertions.length) throw new Error("Live disclosure assertions missing");

  await page.goto(new URL("/login", base).href);
  await page.locator('input[name="username"]').fill(username);
  await page.locator('input[name="password"]').fill(password);
  await page.locator('button[type="submit"]').click();
  // Do not proceed on a refused/failed login. No role switch or admin fallback.
  await expect(page).not.toHaveURL(/\/login(?:\?|$)/);
  await page.goto(target.href);
  for (const assertion of assertions) {
    if (!assertion.selector) throw new Error("Disclosure selector missing");
    const element = page.getByTestId(assertion.selector);
    if (assertion.action === "click") await element.click();
    else if (assertion.action === "visible") await expect(element).toBeVisible();
    else if (assertion.action === "text" && assertion.text) {
      await expect(element).toBeVisible();
      await expect(element).toContainText(assertion.text);
    } else throw new Error("Invalid disclosure assertion");
  }
});
