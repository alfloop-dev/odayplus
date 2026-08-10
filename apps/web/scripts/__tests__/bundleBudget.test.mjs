import { describe, expect, it } from "vitest";

import {
  evaluateBudget,
  firstLoadFilesFor,
  formatReport,
  isRouteEntry,
  layoutChainFor,
  measureBuild,
} from "../bundleBudget.mjs";

const PAGES = {
  "/layout": ["static/chunks/shared.js", "static/css/shared.css"],
  "/intake/layout": ["static/chunks/shared.js", "static/chunks/intake-layout.js"],
  "/intake/[intakeId]/page": ["static/chunks/shared.js", "static/chunks/intake-page.js"],
  "/operator/page": ["static/chunks/shared.js", "static/chunks/operator.js"],
  "/api/v1/route": ["static/chunks/shared.js"],
  "/not-found": ["static/chunks/shared.js"],
  "/global-error": ["static/chunks/shared.js"],
  "/error": ["static/chunks/shared.js"],
  "/loading": ["static/chunks/shared.js"],
};

const ROUTE_PATHS = {
  "/intake/[intakeId]/page": "/intake/[intakeId]",
  "/operator/page": "/operator",
  "/api/v1/route": "/api/v1",
};

/**
 * Deterministic stand-in for a finished `next build`: every chunk is a distinct
 * incompressible payload whose byte count is controlled by the test.
 */
function fakeBuild(sizes) {
  const manifests = {
    "dist/app-build-manifest.json": JSON.stringify({ pages: PAGES }),
    "dist/app-path-routes-manifest.json": JSON.stringify(ROUTE_PATHS),
  };
  return (filePath, encoding) => {
    const key = filePath.replaceAll("\\", "/");
    if (encoding === "utf8") {
      return manifests[key];
    }
    const chunk = key.slice("dist/".length);
    const size = sizes[chunk];
    if (size === undefined) {
      throw new Error(`unexpected chunk read: ${chunk}`);
    }
    return Buffer.alloc(size, 0);
  };
}

describe("isRouteEntry", () => {
  it("keeps addressable pages and route handlers", () => {
    expect(isRouteEntry("/operator/page")).toBe(true);
    expect(isRouteEntry("/api/v1/route")).toBe(true);
    expect(isRouteEntry("/_not-found/page")).toBe(true);
  });

  it("drops layouts and boundary files that carry no budget of their own", () => {
    for (const key of ["/layout", "/intake/layout", "/error", "/loading", "/not-found", "/global-error"]) {
      expect(isRouteEntry(key), key).toBe(false);
    }
  });
});

describe("layoutChainFor", () => {
  it("collects inherited layouts outermost first", () => {
    expect(layoutChainFor("/intake/[intakeId]/page", Object.keys(PAGES))).toEqual([
      "/layout",
      "/intake/layout",
    ]);
  });

  it("skips segments that declare no layout", () => {
    expect(layoutChainFor("/operator/page", Object.keys(PAGES))).toEqual(["/layout"]);
  });
});

describe("firstLoadFilesFor", () => {
  it("unions the route and its layout chain without double counting shared chunks", () => {
    expect(firstLoadFilesFor("/intake/[intakeId]/page", PAGES).sort()).toEqual([
      "static/chunks/intake-layout.js",
      "static/chunks/intake-page.js",
      "static/chunks/shared.js",
    ]);
  });

  it("excludes CSS, matching Next's First Load JS column", () => {
    expect(firstLoadFilesFor("/layout", PAGES)).toEqual(["static/chunks/shared.js"]);
  });
});

describe("measureBuild", () => {
  const measurement = measureBuild("dist", {
    readFile: fakeBuild({
      "static/chunks/shared.js": 400_000,
      "static/chunks/operator.js": 900_000,
      "static/chunks/intake-page.js": 100_000,
      "static/chunks/intake-layout.js": 100_000,
    }),
  });

  it("reports one row per addressable route, heaviest first", () => {
    expect(measurement.routes.map((row) => row.route)).toEqual([
      "/operator",
      "/intake/[intakeId]",
      "/api/v1",
    ]);
  });

  it("charges the shared layout chunks to every route", () => {
    expect(measurement.sharedKb).toBeGreaterThan(0);
    const apiRoute = measurement.routes.find((row) => row.route === "/api/v1");
    expect(apiRoute.firstLoadKb).toBeCloseTo(measurement.sharedKb, 5);
  });
});

describe("evaluateBudget", () => {
  const measurement = {
    sharedKb: 103.3,
    routes: [
      { route: "/operator", manifestKey: "/operator/page", firstLoadKb: 272.7 },
      { route: "/franchisee", manifestKey: "/franchisee/page", firstLoadKb: 116.5 },
      { route: "/login", manifestKey: "/login/route", firstLoadKb: 103.5 },
    ],
  };
  const budget = {
    defaultFirstLoadJsKb: 130,
    sharedFirstLoadJsKb: 115,
    routes: { "/operator": 300, "/franchisee": 130 },
  };

  it("passes when every route fits", () => {
    const result = evaluateBudget(measurement, budget);
    expect(result.ok).toBe(true);
    expect(result.violations).toEqual([]);
  });

  it("applies the default budget to routes with no explicit entry", () => {
    const login = evaluateBudget(measurement, budget).rows.find((row) => row.route === "/login");
    expect(login.declared).toBe(false);
    expect(login.budgetKb).toBe(130);
  });

  it("fails when a declared route regresses past its budget", () => {
    // The pre-split /operator first load: statically importing the deck.gl and
    // maplibre stack put this route at 816 kB.
    const regressed = {
      ...measurement,
      routes: [{ route: "/operator", manifestKey: "/operator/page", firstLoadKb: 816 }],
    };
    const result = evaluateBudget(regressed, budget);
    expect(result.ok).toBe(false);
    expect(result.violations.map((row) => row.route)).toEqual(["/operator"]);
    expect(result.violations[0].overBy).toBeCloseTo(516, 5);
  });

  it("fails when an undeclared route lands over the default budget", () => {
    const result = evaluateBudget(
      { sharedKb: 103.3, routes: [{ route: "/new", manifestKey: "/new/page", firstLoadKb: 400 }] },
      budget,
    );
    expect(result.ok).toBe(false);
    expect(result.violations[0].budgetKb).toBe(130);
  });

  it("fails when the shared chunk grows past its own budget", () => {
    const result = evaluateBudget({ ...measurement, sharedKb: 200 }, budget);
    expect(result.ok).toBe(false);
    expect(result.violations.map((row) => row.route)).toEqual(["(shared by all)"]);
  });
});

describe("formatReport", () => {
  it("explains how to resolve a violation instead of only printing numbers", () => {
    const measurement = {
      sharedKb: 103.3,
      routes: [{ route: "/operator", manifestKey: "/operator/page", firstLoadKb: 816 }],
    };
    const budget = { defaultFirstLoadJsKb: 130, routes: { "/operator": 300 } };
    const report = formatReport(measurement, evaluateBudget(measurement, budget));
    expect(report).toContain("/operator");
    expect(report).toContain("OVER by");
    expect(report).toContain("next/dynamic");
  });
});
