import { expect, test, type Page } from "@playwright/test";

/**
 * Real-CSS geometry guard for the shared Operator header (audit
 * ODP-UI-AUDIT-20261008, F01-F06 / F10). The header must not change with the
 * mounted workspace or data state, must keep the Package 10 sizes, and must
 * not widen the document at any breakpoint edge.
 */
const WORKSPACES = ["today", "store", "growth", "network", "govern"] as const;
const WIDTHS = [390, 1024, 1240, 1241, 1279, 1280, 1440, 1920] as const;

type HeaderGeometry = {
  badgeFontSize: string;
  badgeHeight: number;
  buttonFontFamily: string;
  consoleFontFamily: string;
  docScrollWidth: number;
  headerHeight: number;
  innerBoxSizing: string;
  innerWidth: number;
  innerX: number;
  offscreenControls: string[];
  viewport: number;
};

async function measureHeader(page: Page): Promise<HeaderGeometry> {
  return page.evaluate(() => {
    const header = document.querySelector<HTMLElement>('[data-screen-label="Top Navigation"]');
    const inner = document.querySelector<HTMLElement>('[data-testid="operator-topbar-inner"]');
    const badge = document.querySelector<HTMLElement>('[data-testid="operator-environment-badge"]');
    const consoleRoot = document.querySelector<HTMLElement>('[data-testid="operator-console"]');
    if (!header || !inner || !badge || !consoleRoot) throw new Error("operator header not rendered");
    const viewport = document.documentElement.clientWidth;
    const offscreenControls = Array.from(header.querySelectorAll<HTMLElement>("button, a, input"))
      .filter((element) => element.offsetParent !== null && !element.closest("nav"))
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.left < -0.5 || rect.right > viewport + 0.5;
      })
      .map((element) => element.textContent?.trim() || element.getAttribute("aria-label") || element.tagName);
    const round = (value: number) => Math.round(value * 100) / 100;
    return {
      badgeFontSize: getComputedStyle(badge).fontSize,
      badgeHeight: round(badge.getBoundingClientRect().height),
      buttonFontFamily: getComputedStyle(header.querySelector("button") as HTMLElement).fontFamily,
      consoleFontFamily: getComputedStyle(consoleRoot).fontFamily,
      docScrollWidth: document.documentElement.scrollWidth,
      headerHeight: round(header.getBoundingClientRect().height),
      innerBoxSizing: getComputedStyle(inner).boxSizing,
      innerWidth: round(inner.getBoundingClientRect().width),
      innerX: round(inner.getBoundingClientRect().x),
      offscreenControls,
      viewport,
    };
  });
}

async function openWorkspace(page: Page, workspace: string) {
  await page.goto(`/operator?ws=${workspace}`);
  await expect(page.getByTestId("operator-topbar-inner")).toBeVisible();
  await expect(page.locator('[data-testid="operator-console"] > main')).not.toBeEmpty();
}

// Each width loads five workspaces; a cold dev-server compile needs headroom.
test.describe.configure({ timeout: 120_000 });

test.describe("Operator shared header layout", () => {
  for (const width of [390, 1024, 1440]) {
    test(`Today long ingestion identifiers and human titles remain readable at ${width}px`, async ({ page }, testInfo) => {
      // Sanitized identifiers from the predecessor authenticated Today readback.
      // Offline response substitution exercises the actual React/CSS, not live
      // acceptance, business data mutation or a DOM/prototype layout surrogate.
      const timestamps = [
        "20261005234153", "20261006111822", "20261007061834", "20261007115623",
        "20261008040013", "20261008190312", "20261009010154",
      ];
      const ids = timestamps.map(
        (time) => `external-fetch:listing.partner_feed:blocked:${time}:e34f2117-de4b-478c-82fd-13c4ef428d42`,
      );
      const title = "External ingestion requires review";
      await page.route(/\/api\/v1\/operator\/(bootstrap|today)(\?.*)?$/, async (route) => {
        const response = await route.fetch();
        expect(response.status()).toBe(200);
        const envelope = await response.json();
        expect(envelope.today.queue.length).toBeGreaterThan(0);
        envelope.today.queue = ids.map((id) => ({
          ...envelope.today.queue[0], id, title,
          description: "External provider fetch is disabled for this deployment.",
          meta: "listing.partner_feed", status: "failed", owner: "Data Operations",
        }));
        await route.fulfill({ response, json: envelope });
      });
      await page.setViewportSize({ width, height: 900 });
      await openWorkspace(page, "today");
      const rows = page.getByTestId("operator-today-queue").getByRole("button");
      await expect(rows).toHaveCount(ids.length, { timeout: 45_000 });
      for (const [index, id] of ids.entries()) {
        await expect(rows.nth(index).locator("small").first()).toHaveText(id);
        await expect(rows.nth(index).locator("strong").first()).toHaveText(title);
        await expect(rows.nth(index)).toHaveAccessibleName(new RegExp(`${id}.*${title}`));
      }
      const geometry = await rows.evaluateAll((buttons) => buttons.map((button) => {
        const row = button.getBoundingClientRect();
        const viewport = document.documentElement.clientWidth;
        const labels = Array.from(button.querySelectorAll("small, strong")).slice(0, 2);
        return labels.map((label) => {
          const box = label.getBoundingClientRect();
          const range = document.createRange();
          range.selectNodeContents(label);
          // Check painted text, not only document/header width: console overflow
          // can conceal offscreen labels while those coarse checks still pass.
          const textRects = Array.from(range.getClientRects());
          return {
            text: label.textContent,
            readable: box.width > 0 && box.height > 0 && textRects.length > 0 &&
              textRects.every((rect) => rect.left >= Math.max(0, row.left, box.left) - 0.5 &&
                rect.right <= Math.min(viewport, row.right, box.right) + 0.5 &&
                rect.top >= box.top - 0.5 && rect.bottom <= box.bottom + 0.5),
          };
        });
      }));
      expect(geometry.flat().filter((label) => !label.readable), "clipped identifier/title text").toEqual([]);
      expect((await measureHeader(page)).docScrollWidth).toBeLessThanOrEqual(width);
      await testInfo.attach(`today-long-identifiers-${width}`, {
        body: await page.screenshot({ fullPage: true }), contentType: "image/png",
      });
    });
  }

  for (const width of WIDTHS) {
    test(`header geometry is identical across workspaces at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      const measured: Record<string, HeaderGeometry> = {};

      for (const workspace of WORKSPACES) {
        await openWorkspace(page, workspace);
        const geometry = await measureHeader(page);
        measured[workspace] = geometry;

        expect(geometry.docScrollWidth, `${workspace} document width`).toBeLessThanOrEqual(width);
        expect(geometry.offscreenControls, `${workspace} unreachable header controls`).toEqual([]);
        expect(geometry.consoleFontFamily).toMatch(/^"Noto Sans TC"/);
        expect(geometry.buttonFontFamily).toBe(geometry.consoleFontFamily);
        expect(geometry.innerBoxSizing).toBe("border-box");
        expect(geometry.badgeFontSize).toBe("9.5px");
        expect(geometry.badgeHeight).toBeLessThanOrEqual(22);
      }

      const reference = measured.today;
      for (const workspace of WORKSPACES) {
        expect(measured[workspace].headerHeight, `${workspace} header height`).toBe(reference.headerHeight);
        expect(measured[workspace].innerWidth, `${workspace} header row width`).toBe(reference.innerWidth);
        expect(measured[workspace].badgeHeight, `${workspace} badge height`).toBe(reference.badgeHeight);
      }

      if (width > 1240) {
        // One 52px row plus the 1px bottom border (Package 10 S02:33-34): every
        // desktop width keeps brand, nav and actions on a single row.
        expect(reference.headerHeight).toBe(53);
      }
      if (width >= 1920) {
        expect(reference.innerWidth).toBe(1720);
        expect(reference.innerX).toBe((width - 1720) / 2);
      }
    });
  }

  test("header popovers and account actions stay inside a 390px viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openWorkspace(page, "today");

    const insideViewport = async (selector: string) =>
      page.locator(selector).first().evaluate((element) => {
        const rect = element.getBoundingClientRect();
        return rect.left >= -0.5 && rect.right <= document.documentElement.clientWidth + 0.5;
      });

    await page.getByRole("button", { name: /通知/ }).click();
    expect(await insideViewport('[data-screen-label="Notifications"]')).toBe(true);
    await page.keyboard.press("Escape");

    await page.getByTestId("operator-task-center-button").click();
    expect(await insideViewport('[data-testid="operator-task-center"]')).toBe(true);
    await page.keyboard.press("Escape");

    await page.getByRole("button", { name: /營運主管/, expanded: false }).click();
    expect(await insideViewport('[data-screen-label="Role Switch Menu"]')).toBe(true);
    const adminLink = page.getByTestId("operator-admin-link");
    const logout = page.getByRole("button", { name: "登出" });
    await adminLink.scrollIntoViewIfNeeded();
    await expect(adminLink).toBeInViewport();
    expect(await insideViewport('[data-testid="operator-admin-link"]')).toBe(true);
    await logout.scrollIntoViewIfNeeded();
    await expect(logout).toBeInViewport();
    await expect(logout).toBeEnabled();
  });

  test("account menu is not clipped in the desktop intake-detail context", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/operator?ws=network&tab=radar&selected=layout-probe&dialog=detail");
    await expect(page.getByTestId("operator-console")).toHaveAttribute("data-intake-detail-open", "true");

    await page.getByRole("button", { name: /營運主管/, expanded: false }).click();
    const adminLink = page.getByTestId("operator-admin-link");
    const logout = page.getByRole("button", { name: "登出" });
    await expect(adminLink).toBeInViewport();
    await expect(logout).toBeInViewport();

    const hitTest = await page.evaluate(() => {
      const header = document.querySelector<HTMLElement>('[data-screen-label="Top Navigation"]');
      const hits = (element: Element | null) => {
        if (!element) return false;
        const rect = element.getBoundingClientRect();
        const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        return top === element || element.contains(top);
      };
      const logoutButton = Array.from(document.querySelectorAll("button")).find(
        (button) => button.textContent?.trim() === "登出",
      );
      return {
        admin: hits(document.querySelector('[data-testid="operator-admin-link"]')),
        logout: hits(logoutButton ?? null),
        headerOverflowY: header ? getComputedStyle(header).overflowY : "missing",
      };
    });
    // Each account action is the topmost element at its own centre, and the
    // header did not turn into a scroll container that clips its popovers.
    expect(hitTest).toEqual({ admin: true, logout: true, headerOverflowY: "visible" });
  });

  test("header does not change when workspace content is replaced by a data gate", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page, "today");
    const withContent = await measureHeader(page);

    // Same controlled experiment as the audit: remove the Today content node.
    await page.evaluate(() => {
      const main = document.querySelector('[data-testid="operator-console"] > main');
      if (main) main.innerHTML = '<section data-testid="operator-data-unavailable" data-status="error"></section>';
    });
    const withoutContent = await measureHeader(page);

    expect(withoutContent).toEqual(withContent);
  });
});
