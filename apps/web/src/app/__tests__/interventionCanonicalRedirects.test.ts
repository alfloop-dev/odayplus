import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";
import * as ts from "typescript";
import nextConfig from "../../../next.config.mjs";

const require = createRequire(import.meta.url);
type Route = { source: string; destination: string; internal?: boolean; permanent?: boolean };
type Built = Route & { match: (pathname: string) => Record<string, string> | false };
type Redirect = { finished: boolean; statusCode: number; parsedUrl: { pathname: string; search: string } };
function installedNode(file: string, select: (node: ts.Node) => boolean): string {
  const filename = require.resolve(file);
  const source = readFileSync(filename, "utf8");
  const tree = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  let selected: ts.Node | undefined;
  function visit(node: ts.Node) { if (select(node)) selected = node; ts.forEachChild(node, visit); }
  visit(tree);
  if (!selected) throw new Error(`Installed Next route producer was not found: ${file}`);
  return selected.getText(tree);
}

// Execute the exact installed filesystem producer and terminal redirect block.
// Imports are the real installed Next functions; no server, HTTP or app page
// is mounted, and neither original route assertion nor its waits are changed.
const builderSource = installedNode("next/dist/server/lib/router-utils/filesystem", (node) =>
  ts.isVariableDeclaration(node) && node.name.getText() === "buildCustomRoute");
const builder = runInNewContext(`(${builderSource.slice(builderSource.indexOf("=") + 1)})`, {
  _pathmatch: require("next/dist/shared/lib/router/utils/path-match"),
  _redirectstatus: require("next/dist/lib/redirect-status"),
}) as (type: string, route: Route, basePath: string, caseSensitive: boolean) => Built;
const redirectBlock = installedNode("next/dist/server/lib/router-utils/resolve-routes", (node) =>
  ts.isIfStatement(node) && node.expression.getText().includes("'statusCode' in route")
    && node.expression.getText().includes("route.destination"));
const render = runInNewContext(`(function(route, params, parsedUrl, req) { ${redirectBlock} })`, {
  _preparedestination: require("next/dist/shared/lib/router/utils/prepare-destination"),
  _serverrouteutils: require("next/dist/server/server-route-utils"),
  _utils2: require("next/dist/shared/lib/utils"),
  _redirectstatus: require("next/dist/lib/redirect-status"),
}) as (route: Built, params: Record<string, string>, parsedUrl: { query: Record<string, string> }, req: object) => Redirect;

describe("legacy Intervention URLs use the canonical owner through installed Next redirects", () => {
  it("selects the narrow queue/detail before the preserved blanket and encodes entity on the real destination path", async () => {
    const { default: normalize } = require("next/dist/lib/load-custom-routes");
    const { defaultConfig } = require("next/dist/server/config-shared");
    const config = { ...defaultConfig, ...nextConfig };
    const normalized = await normalize(config);
    const routes: Built[] = normalized.redirects.map((route: Route) => builder("redirect", route, config.basePath, config.experimental.caseSensitiveRoutes));
    function redirect(pathname: string, query: Record<string, string> = {}) {
      for (const route of routes) {
        const params = route.match(pathname);
        if (params) return { source: route.source, result: render(route, params, { query }, {}) };
      }
      return null;
    }
    const queue = redirect("/w/operations/interventions", { ws: "govern", tab: "other" })!;
    expect(queue.source).toBe("/w/operations/interventions");
    expect(queue.result.finished).toBe(true);
    expect(queue.result.statusCode).toBe(308);
    const queueURL = new URL(`${queue.result.parsedUrl.pathname}?${queue.result.parsedUrl.search}`, "https://canonical.invalid");
    expect(queueURL.pathname).toBe("/operator");
    expect(Object.fromEntries(queueURL.searchParams)).toEqual({ ws: "today", tab: "interventions" });
    for (const entity of ["693ec85b-7491-4169-a7af-5800c8b657a1", "entity 空白/組合?#&=%"]) {
      const detail = redirect(`/w/operations/interventions/${encodeURIComponent(entity)}`)!;
      expect(detail.source).toBe("/w/operations/interventions/:interventionId");
      expect(detail.result.finished).toBe(true);
      const url = new URL(`${detail.result.parsedUrl.pathname}?${detail.result.parsedUrl.search}`, "https://canonical.invalid");
      expect(url.pathname).toBe("/operator");
      expect(Object.fromEntries(url.searchParams)).toEqual({ ws: "today", tab: "interventions", entity });
      expect(url.search).toContain(`entity=${encodeURIComponent(entity)}`);
    }
    // Original AVM A5 list/detail pages remain executable; neither can be
    // swallowed by the retired-/w blanket after Intervention canonicalization.
    for (const pathname of ["/w/dealroom/cases", "/w/dealroom/cases/693ec85b-7491-4169-a7af-5800c8b657a1"]) {
      expect(redirect(pathname)).toBeNull();
    }
    for (const pathname of ["/w/operations/other", "/w/operations/interventions/deeper/path",
      "/w/dealroom/cases-extra", "/w/dealroom/cases/case-id/deeper"]) {
      const other = redirect(pathname)!;
      expect(other.source).toBe("/w/:path((?!dealroom/cases(?:/[^/]+)?/?$).*)");
      expect(other.result.parsedUrl.pathname).toBe("/operator");
      expect(other.result.parsedUrl.search).toBe("");
    }
    expect(redirect("/operator")).toBeNull();
    expect(redirect("/intake/canonical-owned-record")).toBeNull();
  });
});
