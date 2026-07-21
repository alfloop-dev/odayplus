import { test, expect } from "@playwright/test";
import { createOdpApiClient } from "@oday-plus/openapi-client";
import { loadApiResource } from "../../apps/web/features/shell/resource.ts";
import { resourceState } from "../../apps/web/features/shell/ShellStates.tsx";
import { resolveProductMode, isProductionMode } from "../../apps/web/features/shell/mode.ts";

/**
 * Shell resource binding + product mode — ODP-PGAP-SHELL-001 (acceptance §7, §8).
 *
 * These run in Node with no browser. The shell's pages fetch from *server*
 * components, so a browser-level `page.route` cannot intercept them — mocking
 * there would prove nothing. Instead the real classifier is driven through the
 * real API client with an injected `fetchImpl`, which is the code path that
 * actually decides which state surface a page renders.
 *
 * The rendered surfaces themselves are covered against a live backend in
 * shell-product.spec.ts (a genuine 403 and a genuine 404).
 *
 * Run:
 *   npx playwright test tests/e2e --grep ODP-PGAP-SHELL-001
 */

function clientReturning(status: number, body: unknown = {}) {
  return createOdpApiClient({
    baseUrl: "http://api.test",
    fetchImpl: (async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json", "x-correlation-id": "corr-test" },
      })) as unknown as typeof fetch,
  });
}

test("ODP-PGAP-SHELL-001 binding classifies a healthy response as ready", async () => {
  const resource = await loadApiResource({
    client: clientReturning(200, { status: { openTasks: 0 } }),
    fetcher: (client) => client.getShellHome(),
  });

  expect(resource.state).toBe("ready");
  expect(resource.source).toBe("api");
  expect(resource.data).not.toBeNull();
  expect(resourceState(resource)).toBeNull();
});

test("ODP-PGAP-SHELL-001 an empty-but-healthy shell is ready, not a fixture fallback", async () => {
  // The list binding treats a cold store as `empty` → fixture. For the shell a
  // zero-task response is a real, healthy state and must render as "no tasks"
  // rather than as sample data.
  const resource = await loadApiResource({
    client: clientReturning(200, { items: [], count: 0, total: 0 }),
    fetcher: (client) => client.getShellTasks(),
  });

  expect(resource.state).toBe("ready");
  expect(resource.source).toBe("api");
});

test("ODP-PGAP-SHELL-001 binding classifies 403 as forbidden and keeps the server's reason", async () => {
  const resource = await loadApiResource({
    client: clientReturning(403, { detail: "目前角色無法檢視平台管理；需要營運主管權限。" }),
    fetcher: (client) => client.getShellAdmin(),
  });

  expect(resource.state).toBe("forbidden");
  expect(resource.status).toBe(403);
  // The refusal copy is the server's, rendered verbatim — never invented.
  expect(resource.detail).toContain("營運主管");
  expect(resourceState(resource)).toBe("forbidden");
  expect(resource.data).toBeNull();
});

test("ODP-PGAP-SHELL-001 binding classifies 401 as forbidden", async () => {
  const resource = await loadApiResource({
    client: clientReturning(401, { detail: "unauthenticated" }),
    fetcher: (client) => client.getShellHome(),
  });

  expect(resource.state).toBe("unauthorized");
  expect(resourceState(resource)).toBe("forbidden");
});

test("ODP-PGAP-SHELL-001 binding classifies 500 as an error, and 503 as maintenance", async () => {
  const outage = await loadApiResource({
    client: clientReturning(500, { detail: "boom" }),
    fetcher: (client) => client.getShellHome(),
  });
  expect(outage.state).toBe("error");
  expect(resourceState(outage)).toBe("error");
  expect(outage.correlationId).toBe("corr-test");
  expect(outage.data).toBeNull();

  // A maintenance window is a wait, an outage is a report — the shell must not
  // tell an operator to file a ticket for planned downtime.
  const maintenance = await loadApiResource({
    client: clientReturning(503, { detail: "maintenance window" }),
    fetcher: (client) => client.getShellHome(),
  });
  expect(maintenance.state).toBe("error");
  expect(maintenance.status).toBe(503);
  expect(resourceState(maintenance)).toBe("maintenance");
});

test("ODP-PGAP-SHELL-001 binding classifies a transport failure as an error", async () => {
  const client = createOdpApiClient({
    baseUrl: "http://api.test",
    fetchImpl: (async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch,
  });
  const resource = await loadApiResource({ client, fetcher: (c) => c.getShellHome() });

  expect(resource.state).toBe("error");
  expect(resource.error).toContain("ECONNREFUSED");
  expect(resource.data).toBeNull();
});

test("ODP-PGAP-SHELL-001 an unconfigured API never degrades to fixture data", async () => {
  const resource = await loadApiResource({
    client: null,
    fetcher: async () => ({}) as never,
  });

  expect(resource.state).toBe("unconfigured");
  // `none`, not `fixture`: the shell has no sample-data path to fall back to.
  expect(resource.source).toBe("none");
  expect(resource.data).toBeNull();
  expect(resourceState(resource)).toBe("unconfigured");
});

test("ODP-PGAP-SHELL-001 product mode is explicit and defaults production fail-closed", async () => {
  expect(resolveProductMode({ ODP_PRODUCT_MODE: "production" })).toBe("production");
  expect(resolveProductMode({ NEXT_PUBLIC_ODP_PRODUCT_MODE: "poc" })).toBe("poc");
  // An explicit value wins over NODE_ENV.
  expect(resolveProductMode({ ODP_PRODUCT_MODE: "poc", NODE_ENV: "production" })).toBe("poc");
  // A production build defaults to production mode: a wrong guess here costs a
  // visible "unavailable" state, never fake data shown as real.
  expect(resolveProductMode({ NODE_ENV: "production" })).toBe("production");
  expect(resolveProductMode({ NODE_ENV: "development" })).toBe("poc");
  // An unrecognised value is not honoured.
  expect(resolveProductMode({ ODP_PRODUCT_MODE: "nonsense", NODE_ENV: "production" })).toBe(
    "production",
  );
  expect(isProductionMode({ NODE_ENV: "production" })).toBe(true);
  expect(isProductionMode({ NODE_ENV: "test" })).toBe(false);
});
