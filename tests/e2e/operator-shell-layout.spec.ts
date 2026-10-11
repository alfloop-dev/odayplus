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
      const auditActor = "17e9cb99-db46-4a07-8e61-6bf9b22cf5d2";
      const auditTime = "2026-10-11T15:53:07.811458+00:00";
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
        envelope.today.auditFeed = [{
          actor: auditActor, time: auditTime,
          detail: "view operator_console: allow", category: "security.authorization",
        }];
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
      const audit = page.getByTestId("operator-audit-feed").locator("article").first();
      await expect(audit.locator("time")).toHaveText("23:53");
      await expect(audit.locator("time")).toHaveAttribute("datetime", auditTime);
      await expect(audit.locator("time")).toHaveAttribute("title", auditTime);
      await expect(audit.locator("p")).toContainText(auditActor);
      const auditGeometry = await audit.evaluate((row) => ({
        rowWidth: row.getBoundingClientRect().width,
        textWidth: row.querySelector("p")!.getBoundingClientRect().width,
      }));
      expect(auditGeometry.textWidth, "audit text squeezed by the timestamp column")
        .toBeGreaterThan(auditGeometry.rowWidth * 0.65);
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

/**
 * Network 找區域 against Package 10 S05 (audit content-parity-20261009 and
 * ODP-UI-NETWORK-FIND-AREAS-PARITY-001). Design reference at 1440: one header
 * row, tabs 212px below the header, a 206 / 836 / 330 grid, a 426px map with
 * the tray directly under it, nine single-line lenses, and a zone detail with
 * a big score, a fact table, why/risk lists, a next-step box and 1 + 5 actions.
 */
test.describe("Network Find Areas Package 10 layout", () => {
  async function openFindAreas(page: Page, width: number) {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => window.sessionStorage.setItem("oday.operator.role", "expansion-manager"));
    await page.goto("/operator?ws=network");
    await expect(page.getByTestId("find-areas-zone-detail")).toBeVisible({ timeout: 30_000 });
  }

  test("1440px geometry matches the design grid", async ({ page }) => {
    await openFindAreas(page, 1440);
    const g = await page.evaluate(() => {
      const box = (selector: string) => {
        const element = document.querySelector<HTMLElement>(selector);
        if (!element) throw new Error(`missing ${selector}`);
        const rect = element.getBoundingClientRect();
        return { x: rect.x, y: rect.y + window.scrollY, w: rect.width, h: rect.height, bottom: rect.bottom + window.scrollY };
      };
      const header = box('[data-testid="network-header"]');
      const headerParts = Array.from(
        document.querySelectorAll<HTMLElement>('[data-testid="network-header"] > h2, [data-testid="network-header"] > p, [data-testid="network-header-stats"] > li'),
      ).map((element) => {
        const rect = element.getBoundingClientRect();
        return rect.y + rect.height / 2;
      });
      const panel = document.querySelector<HTMLElement>('[data-testid="network-panel-find-areas"]')!;
      const lensButtons = Array.from(document.querySelectorAll<HTMLElement>('[data-testid="find-areas-lens-list"] button'));
      const actions = Array.from(document.querySelectorAll<HTMLElement>('[data-testid="find-areas-zone-actions"] button'));
      const tray = box('[data-testid="find-areas-tray"]');
      return {
        actions: actions.map((button) => ({ text: button.textContent, bg: getComputedStyle(button).backgroundColor })),
        bigScoreFont: getComputedStyle(document.querySelector('[data-testid="find-areas-zone-score"]')!).fontSize,
        columns: Array.from(panel.children).map((child) => Math.round(child.getBoundingClientRect().width)),
        docScrollWidth: document.documentElement.scrollWidth,
        factRows: document.querySelectorAll('[data-testid="find-areas-zone-facts"] > div').length,
        header,
        headerCenters: headerParts,
        headerText: document.querySelector('[data-testid="network-header"]')!.textContent,
        lensHeights: lensButtons.map((button) => Math.round(button.getBoundingClientRect().height)),
        lensTexts: lensButtons.map((button) => button.textContent),
        legend: document.querySelector('[aria-labelledby="find-areas-legend-title"]')!.textContent,
        map: box('[data-testid="find-areas-map-frame"]'),
        panelX: panel.getBoundingClientRect().x,
        tabBadge: document.querySelector('[data-testid^="network-tab-count-"]')?.textContent ?? null,
        tabs: box('[aria-label="Network tabs"]'),
        trayHeadingY: box("#find-areas-tray-title").y,
        trayY: tray.y,
      };
    });

    expect(g.docScrollWidth).toBeLessThanOrEqual(1440);
    // One header row: title, summary and four chips share a centre line.
    expect(g.header.h).toBeLessThanOrEqual(44);
    expect(g.headerCenters).toHaveLength(6);
    for (const center of g.headerCenters) expect(Math.abs(center - g.headerCenters[0])).toBeLessThanOrEqual(4);
    expect(g.headerText).not.toMatch(/HeatZones|listings|avg confidence|NETWORK/);
    // Tabs sit right under header + stepper (design: 212px below the header top).
    expect(g.tabs.y - g.header.y).toBeLessThanOrEqual(215);
    expect(g.tabBadge).toMatch(/^[1-9]\d*$/);
    // 206 / 836 / 330 with 14px gaps from the 20px content edge.
    expect(Math.round(g.panelX)).toBe(20);
    expect(g.columns).toEqual([206, 836, 330]);
    // 426px map, tray heading right under it — no dead band.
    expect(Math.round(g.map.h)).toBe(426);
    expect(g.trayHeadingY - g.map.bottom).toBeLessThanOrEqual(12);
    expect(g.trayY).toBeGreaterThan(g.map.bottom);
    // Nine single-line Chinese lenses.
    expect(g.lensTexts).toEqual([
      "需求熱度", "Oday G2 適配", "競店壓力", "自家稀釋", "租金可行性", "住宅／學區／商圈", "交通／人流", "未滿足需求", "資料信心",
    ]);
    for (const height of g.lensHeights) expect(height).toBeLessThanOrEqual(34);
    expect(g.legend).toContain("HeatZone ≥ 80");
    expect(g.legend).not.toMatch(/High lens fit|Watch tradeoff|Risk pressure/);
    // Zone detail: 22px score, seven facts, one filled primary and five outlined actions.
    expect(g.bigScoreFont).toBe("22px");
    expect(g.factRows).toBe(7);
    expect(g.actions).toHaveLength(6);
    expect(g.actions[0].bg).toBe("rgb(46, 58, 151)");
    for (const action of g.actions.slice(1)) expect(action.bg).not.toBe("rgb(46, 58, 151)");
  });

  test("map draws the offline basemap under a lens caption without developer strings", async ({ page }) => {
    await openFindAreas(page, 1440);
    const status = page.getByTestId("heat-zone-map-status");
    await expect(status).toHaveText(/^HeatZone Lens：需求熱度 · /, { timeout: 30_000 });
    await expect(status).not.toHaveText(/snap-|network-ops-local|MapLibre|layers/);
    await expect
      .poll(
        () =>
          page.evaluate(() => {
            const map = window.__odpMaplibreMap;
            return map?.isStyleLoaded() ? map.getStyle().layers.map((layer: { id: string }) => layer.id) : [];
          }),
        { timeout: 30_000 },
      )
      .toEqual(expect.arrayContaining(["odp-schematic-river", "odp-schematic-road", "odp-local-heatzone-fill"]));

    await page.getByRole("button", { name: "資料信心" }).click();
    await expect(status).toHaveText(/^HeatZone Lens：資料信心 · /);
    await expect(page.getByText("依「資料信心」排序")).toBeVisible();
  });

  test("390px stacks lens, map and zone detail inside the viewport", async ({ page }) => {
    await openFindAreas(page, 390);
    const g = await page.evaluate(() => {
      const rect = (selector: string) => document.querySelector<HTMLElement>(selector)!.getBoundingClientRect();
      const panel = document.querySelector<HTMLElement>('[data-testid="network-panel-find-areas"]')!;
      const limit = document.documentElement.clientWidth + 0.5;
      const insideScroller = (element: Element) => {
        for (let node = element.parentElement; node && node !== panel; node = node.parentElement) {
          if (["auto", "scroll"].includes(getComputedStyle(node).overflowX)) return true;
        }
        return false;
      };
      return {
        detailTop: rect('[data-testid="find-areas-zone-detail"]').top,
        docScrollWidth: document.documentElement.scrollWidth,
        lensBottom: rect('[data-testid="find-areas-lens-list"]').bottom,
        lensScrolls: getComputedStyle(document.querySelector('[data-testid="find-areas-lens-list"]')!).overflowX,
        mapBottom: rect('[data-testid="find-areas-map-frame"]').bottom,
        mapTop: rect('[data-testid="find-areas-map-frame"]').top,
        offscreen: Array.from(panel.querySelectorAll("*"))
          .filter((element) => element.getBoundingClientRect().width > 0)
          .filter((element) => element.getBoundingClientRect().right > limit && !insideScroller(element))
          .map((element) => element.tagName + "." + String(element.className).slice(0, 40)),
        trayTop: rect('[data-testid="find-areas-tray"]').top,
      };
    });
    expect(g.docScrollWidth).toBeLessThanOrEqual(390);
    expect(g.offscreen).toEqual([]);
    expect(g.lensScrolls).toBe("auto");
    expect(g.mapTop).toBeGreaterThan(g.lensBottom);
    expect(g.detailTop).toBeGreaterThan(g.mapBottom);
    expect(g.trayTop).toBeGreaterThan(g.detailTop);
  });

  test("從網址新增物件 opens the intake dialog pre-set to the selected zone", async ({ page }) => {
    await openFindAreas(page, 1440);
    await page.getByRole("button", { name: "＋ 從網址新增物件（帶入本區）" }).click();
    await expect(page.getByTestId("intake-url-input")).toBeVisible({ timeout: 30_000 });
    await expect(page).toHaveURL(/tab=radar/);
    await expect(page).toHaveURL(/dialog=add/);
    await expect(page.getByTestId("intake-area-select")).toHaveValue("HZ-01");
  });
});
