import { defineConfig } from "@playwright/test";
import base from "./operator-intake-url.config";

export default defineConfig({
  ...base,
  testMatch: /(operator-intake-url-parity|operator-score-provenance)\.spec\.ts/,
});
