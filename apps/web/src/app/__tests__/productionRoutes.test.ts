import { existsSync, readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

function collectPageFiles(directory: string, prefix = ""): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      return collectPageFiles(resolve(directory, entry.name), relativePath);
    }
    return entry.name === "page.tsx" ? [relativePath] : [];
  });
}

const RETIRED_FEATURE_ROOTS = [
  "adlift",
  "audit",
  "avm",
  "expansion",
  "intervention",
  "learninghub",
  "map",
  "netplan",
  "operations",
  "priceops",
] as const;

const RETIRED_HREFS = [
  'href: "/"',
  'href: "/tasks"',
  'href: "/search"',
  'href: "/expansion"',
  'href: "/operations"',
  'href: "/interventions"',
  'href: "/pricing"',
  'href: "/adlift"',
  'href: "/avm"',
  'href: "/netplan"',
  'href: "/learning"',
  'href: "/audit"',
  'href: "/admin"',
] as const;

describe("canonical production routes", () => {
  it("keeps only the three canonical executable pages", () => {
    expect(collectPageFiles(resolve(process.cwd(), "src/app")).sort()).toEqual([
      "franchisee/page.tsx",
      "intake/[intakeId]/page.tsx",
      "operator/page.tsx",
    ]);
  });

  it("keeps the franchisee page on an authenticated per-request read", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/app/franchisee/page.tsx"),
      "utf8",
    );

    expect(source).toContain("getServerApiClient");
    expect(source).toContain('export const dynamic = "force-dynamic"');
  });

  it("redirects retired visual URLs to the Package 10 runtime", () => {
    const source = readFileSync(
      resolve(process.cwd(), "next.config.mjs"),
      "utf8",
    );

    expect(source).toContain('{ source: "/", destination: "/operator"');
    expect(source).toContain('destination: "/operator?ws=network"');
    expect(source).toContain('destination: "/operator?ws=growth"');
    expect(source).toContain('destination: "/operator?ws=govern"');
    expect(source).toContain(
      '{ source: "/w/:path*", destination: "/operator"',
    );
  });

  it("removes the old root frame and sends recovery links to /operator", () => {
    const layout = readFileSync(
      resolve(process.cwd(), "src/app/layout.tsx"),
      "utf8",
    );
    const routeError = readFileSync(
      resolve(process.cwd(), "src/app/error.tsx"),
      "utf8",
    );
    const notFound = readFileSync(
      resolve(process.cwd(), "src/app/not-found.tsx"),
      "utf8",
    );

    expect(layout).not.toContain(["Ops", "BoardFrame"].join(""));
    expect(routeError).toContain('href="/operator"');
    expect(notFound).toContain('href="/operator"');
    expect(notFound).not.toContain('href="/search"');
  });

  it("keeps canonical imports off retired feature roots and old loaders", () => {
    for (const root of RETIRED_FEATURE_ROOTS) {
      expect(
        existsSync(resolve(process.cwd(), "features", root)),
        `${root} should be retired`,
      ).toBe(false);
    }

    const operator = readFileSync(
      resolve(process.cwd(), "features/operator/OperatorConsole.tsx"),
      "utf8",
    );
    const network = readFileSync(
      resolve(
        process.cwd(),
        "features/operator/NetworkFindAreasWorkspace.tsx",
      ),
      "utf8",
    );

    expect(operator).toContain("./network/networkFindAreasLoader");
    expect(operator).not.toContain('from "./networkFindAreasLoader"');
    expect(network).toContain('from "./network/HeatZoneMap"');
    expect(network).toContain('from "./network/mapTypes"');
    expect(network).not.toContain("../map/HeatZoneMap");
    expect(network).not.toContain("../expansion/data");
  });

  it("removes the orphan shared route map and keeps command navigation canonical", () => {
    expect(
      existsSync(resolve(process.cwd(), "../../packages/ui/src/nav/routes.ts")),
    ).toBe(false);
    const source = readFileSync(
      resolve(process.cwd(), "features/operator/OperatorConsole.tsx"),
      "utf8",
    );

    for (const href of RETIRED_HREFS) {
      expect(source).not.toContain(href);
    }
    expect(source).toContain('href: "/operator"');
  });

  it("renders the durable intake route instead of redirecting away from it", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/app/intake/[intakeId]/page.tsx"),
      "utf8",
    );

    expect(source).toContain("<OperatorConsole");
    expect(source).toContain('export const dynamic = "force-dynamic"');
    expect(source).toContain('ws: "network"');
    expect(source).toContain('tab: "radar"');
    expect(source).toContain('dialog: "detail"');
    expect(source).toContain("selected: intakeId");
    expect(source).not.toContain("redirect(");
  });
});
