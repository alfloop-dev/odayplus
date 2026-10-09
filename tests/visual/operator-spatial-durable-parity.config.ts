import { defineConfig } from "@playwright/test";
import base from "./operator-network-tabs-parity.config";

// Separate isolated SQLite backend. Never reuse a server with unknown state.
if (!process.env.NETWORK_SPATIAL_DURABLE_DIR) {
  throw new Error("NETWORK_SPATIAL_DURABLE_DIR must name a fresh scratch directory");
}
export default defineConfig({
  ...base,
  testMatch: /operator-spatial-durable-parity\.spec\.ts/,
  // Let actual browser persona headers reach the server; the broad E2E context
  // headers would otherwise overwrite its subject and permissions.
  use: { ...base.use, extraHTTPHeaders: {} },
  webServer: Array.isArray(base.webServer) ? base.webServer.map((server, index) => ({
    ...server,
    reuseExistingServer: false,
    ...(index === 0 ? {
      env: {
        ...server.env,
        ODP_PERSISTENCE: "memory",
        ODP_DEPLOY_ENV: "e2e",
        ODP_DATA_BINDING_MODE: "fixture",
        ODP_E2E_MODE: "true",
        ODP_AUDIT_WORM_SINK_URI: "",
        ODP_AUDIT_WORM_LOCAL_PATH: `${process.env.NETWORK_SPATIAL_DURABLE_DIR}/audit-worm`,
      },
      command: `.venv/bin/python -m uvicorn tests.visual.spatial_durable_backend:create_test_app --factory --host 127.0.0.1 --port ${process.env.ODP_API_PORT ?? 8099}`,
    } : {}),
  })) : base.webServer,
});
