import { defineConfig } from "@playwright/test";
import base from "./operator-network-tabs-parity.config";

// Separate isolated SQLite backend. Never reuse a server with unknown state.
if (!process.env.NETWORK_SPATIAL_DURABLE_DIR) {
  throw new Error("NETWORK_SPATIAL_DURABLE_DIR must name a fresh scratch directory");
}
export default defineConfig({
  ...base,
  testMatch: /operator-spatial-durable-parity\.spec\.ts/,
  webServer: Array.isArray(base.webServer) ? base.webServer.map((server, index) => ({
    ...server,
    reuseExistingServer: false,
    ...(index === 0 ? {
      command: `.venv/bin/python -m uvicorn tests.visual.spatial_durable_backend:create_test_app --factory --host 127.0.0.1 --port ${process.env.ODP_API_PORT ?? 8099}`,
    } : {}),
  })) : base.webServer,
});
