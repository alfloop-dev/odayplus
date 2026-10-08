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
