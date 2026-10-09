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

  test("workspace content sits on the shared page container without a second chrome", async ({ page }) => {
    for (const width of [1440, 1024, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const [workspace, titleSelector] of [
        ["store", '[data-screen-label="Store Ops 門市營運"] :is(h1, h2)'],
        ["growth", '[data-screen-label="Growth 營收成長"] [class*="headerTitle"]'],
      ] as const) {
        await openWorkspace(page, workspace);
        // Measure the row holding the workspace title, not the workspace box: a
        // second padding layer keeps the box in place but moves its content.
        const title = page.locator(titleSelector).first();
        await expect(title).toBeVisible();
        const { rowX, shellContentX, rowWidth, shellContentWidth } = await title.evaluate((element) => {
          const shell = document.querySelector('[data-testid="operator-console"] > main') as HTMLElement;
          const style = getComputedStyle(shell);
          const shellBox = shell.getBoundingClientRect();
          const row = element.parentElement!.getBoundingClientRect();
          const left = parseFloat(style.paddingLeft);
          return {
            rowX: row.x,
            rowWidth: row.width,
            shellContentX: shellBox.x + left,
            shellContentWidth: shellBox.width - left - parseFloat(style.paddingRight),
          };
        });
        // The shell already applies the Package 10 container padding at every
        // breakpoint; workspaces must not pad again.
        expect(Math.round(rowX), `${workspace} title row x at ${width}px`).toBe(Math.round(shellContentX));
        expect(Math.round(rowWidth), `${workspace} title row width at ${width}px`).toBeGreaterThanOrEqual(
          Math.round(shellContentWidth) - 4,
        );
      }
    }

    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page, "govern");
    await expect(page.getByTestId("governance-workspace")).toBeVisible();
    await expect(page.getByRole("heading", { name: "治理稽核" })).toHaveCount(1);
    await expect(page.getByRole("heading", { level: 1, name: "治理稽核" })).toBeVisible();
    await expect(page.locator("main")).toHaveCount(1);
  });

  test("Store Ops detail fits a 390px viewport except for its scrollable strips", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openWorkspace(page, "store");
    const detail = page.locator('[data-screen-label="Store Ops 門市營運"] section[aria-label$=" detail"]');
    await expect(detail).toBeVisible();
    await expect(page.locator("main")).toHaveCount(1);
    const clipped = await detail.evaluate((root) => {
      const limit = root.getBoundingClientRect().right + 1;
      const insideScroller = (element: Element) => {
        for (let node = element.parentElement; node && node !== root; node = node.parentElement) {
          if (["auto", "scroll"].includes(getComputedStyle(node).overflowX)) return true;
        }
        return false;
      };
      return Array.from(root.querySelectorAll("*"))
        .filter((element) => element.getBoundingClientRect().width > 0)
        .filter((element) => element.getBoundingClientRect().right > limit && !insideScroller(element))
        .map((element) => element.tagName + "." + String(element.className).slice(0, 40));
    });
    expect(clipped).toEqual([]);
  });

  test("Listing inbox filters stay on one compact row", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.addInitScript(() => window.sessionStorage.setItem("oday.operator.role", "expansion-manager"));
    await page.goto("/operator?ws=network&tab=radar");
    const search = page.getByTestId("intake-search-input");
    await expect(search).toBeVisible({ timeout: 30_000 });
    const boxes = await Promise.all(
      ["intake-search-input", "intake-filter-method", "intake-filter-stage", "intake-filter-outcome"].map(
        async (id) => page.getByTestId(id).boundingBox(),
      ),
    );
    const [searchBox, ...selects] = boxes.map((box) => box!);
    expect(searchBox.height).toBeLessThan(48);
    for (const box of selects) {
      expect(box.width).toBeLessThanOrEqual(230);
      expect(Math.abs(box.y - selects[0].y)).toBeLessThan(2);
    }
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
