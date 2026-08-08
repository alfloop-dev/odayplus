/**
 * First Load JS budget enforcement for the Next.js app router build.
 *
 * ODP-ENG-FRONTEND-BUILD-001.
 *
 * `next build` prints a "First Load JS" column but exits 0 no matter how large
 * it gets, so a regression like statically importing the deck.gl/maplibre map
 * stack into a shared workspace is invisible to CI. This module recomputes the
 * same number from the build output and compares it against
 * `apps/web/bundle-budget.json`.
 *
 * The reproduction is deliberate rather than approximate: Next sizes a route as
 * the gzip (level 9) length of every unique `.js` chunk reachable from the route
 * entry plus its layout chain, reported in kB where 1 kB = 1000 bytes. Measured
 * against the 2026-08-08 build, this module lands within rounding of the printed
 * table (e.g. /operator 272.7 vs 272 kB, shared 103.3 vs 103 kB).
 *
 * The module exports pure helpers so the evaluation logic is unit tested without
 * needing a build; `check-bundle-budget.mjs` is the CLI wrapper.
 */

import { gzipSync } from "node:zlib";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/** Next prints kB as 1000 bytes, not 1024. */
const BYTES_PER_KB = 1000;

/**
 * Manifest keys that are not addressable routes. They still contribute chunks
 * to real routes via the layout chain, but they get no budget of their own.
 */
const NON_ROUTE_SUFFIXES = ["/layout", "/error", "/loading", "/not-found", "/global-error"];

export function isRouteEntry(manifestKey) {
  if (NON_ROUTE_SUFFIXES.some((suffix) => manifestKey.endsWith(suffix))) {
    // `/_not-found/page` is a real entry; `/not-found` is the boundary file.
    return manifestKey.endsWith("/page");
  }
  return manifestKey.endsWith("/page") || manifestKey.endsWith("/route");
}

/**
 * Layout entries a route inherits, outermost first. `/intake/[intakeId]/page`
 * inherits `/layout` and `/intake/layout`.
 */
export function layoutChainFor(manifestKey, availableKeys) {
  const available = new Set(availableKeys);
  const segments = manifestKey.split("/").slice(1, -1);
  const chain = [];
  let prefix = "";
  for (let index = 0; index <= segments.length; index += 1) {
    const candidate = `${prefix}/layout`;
    if (available.has(candidate)) {
      chain.push(candidate);
    }
    if (index < segments.length) {
      prefix = `${prefix}/${segments[index]}`;
    }
  }
  return chain;
}

/**
 * Unique `.js` files charged to a route's first load: its own entry plus every
 * inherited layout. CSS is excluded because Next's First Load JS column is.
 */
export function firstLoadFilesFor(manifestKey, pages) {
  const keys = [...layoutChainFor(manifestKey, Object.keys(pages)), manifestKey];
  const files = new Set();
  for (const key of keys) {
    for (const file of pages[key] ?? []) {
      if (file.endsWith(".js")) {
        files.add(file);
      }
    }
  }
  return [...files];
}

function gzipKb(distDir, files, readFile) {
  const bytes = files.reduce(
    (total, file) => total + gzipSync(readFile(join(distDir, file)), { level: 9 }).length,
    0,
  );
  return bytes / BYTES_PER_KB;
}

/**
 * Measure every addressable route in a finished `next build`.
 *
 * @returns {{routes: Array<{route: string, manifestKey: string, firstLoadKb: number}>, sharedKb: number}}
 */
export function measureBuild(distDir, { readFile = readFileSync } = {}) {
  const pages = JSON.parse(readFile(join(distDir, "app-build-manifest.json"), "utf8")).pages;
  const routePaths = JSON.parse(
    readFile(join(distDir, "app-path-routes-manifest.json"), "utf8"),
  );

  const routes = Object.keys(pages)
    .filter(isRouteEntry)
    .map((manifestKey) => ({
      route: routePaths[manifestKey] ?? manifestKey,
      manifestKey,
      firstLoadKb: gzipKb(distDir, firstLoadFilesFor(manifestKey, pages), readFile),
    }))
    .sort((left, right) => right.firstLoadKb - left.firstLoadKb);

  const sharedKb = pages["/layout"]
    ? gzipKb(distDir, firstLoadFilesFor("/layout", pages), readFile)
    : 0;

  return { routes, sharedKb };
}

/**
 * Compare a measurement against the budget file.
 *
 * A route with no explicit entry falls back to `defaultFirstLoadJsKb`, so a new
 * route cannot be added without either fitting the default or being declared.
 *
 * @returns {{ok: boolean, rows: Array<object>, violations: Array<object>}}
 */
export function evaluateBudget(measurement, budget) {
  const rows = measurement.routes.map((entry) => {
    const declared = Object.prototype.hasOwnProperty.call(budget.routes ?? {}, entry.route);
    const budgetKb = declared ? budget.routes[entry.route] : budget.defaultFirstLoadJsKb;
    return {
      ...entry,
      budgetKb,
      declared,
      overBy: entry.firstLoadKb - budgetKb,
    };
  });

  const violations = rows.filter((row) => row.overBy > 0);

  if (
    typeof budget.sharedFirstLoadJsKb === "number" &&
    measurement.sharedKb > budget.sharedFirstLoadJsKb
  ) {
    violations.push({
      route: "(shared by all)",
      manifestKey: "/layout",
      firstLoadKb: measurement.sharedKb,
      budgetKb: budget.sharedFirstLoadJsKb,
      declared: true,
      overBy: measurement.sharedKb - budget.sharedFirstLoadJsKb,
    });
  }

  return { ok: violations.length === 0, rows, violations };
}

export function formatReport(measurement, result) {
  const kb = (value) => `${value.toFixed(1)} kB`;
  const lines = [
    "First Load JS budget (gzip, kB = 1000 bytes)",
    "",
    `${"route".padEnd(30)}${"first load".padStart(12)}${"budget".padStart(12)}  status`,
  ];

  for (const row of result.rows) {
    const status = row.overBy > 0 ? `OVER by ${kb(row.overBy)}` : row.declared ? "ok" : "ok (default)";
    lines.push(
      row.route.padEnd(30) + kb(row.firstLoadKb).padStart(12) + kb(row.budgetKb).padStart(12) + `  ${status}`,
    );
  }

  lines.push("", `shared by all: ${kb(measurement.sharedKb)}`);

  if (!result.ok) {
    lines.push("", "Budget exceeded:");
    for (const violation of result.violations) {
      lines.push(
        `  ${violation.route}: ${kb(violation.firstLoadKb)} > ${kb(violation.budgetKb)} (+${kb(violation.overBy)})`,
      );
    }
    lines.push(
      "",
      "Split the newly added weight behind next/dynamic, or raise the budget in",
      "apps/web/bundle-budget.json with before/after evidence for why the route",
      "genuinely needs it.",
    );
  }

  return lines.join("\n");
}
