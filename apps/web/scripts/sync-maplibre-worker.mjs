#!/usr/bin/env node
/**
 * Synchronize MapLibre GL JS v6 worker scripts to public assets and standalone build.
 *
 * MapLibre v6 ships its WebWorker separately as ES modules (maplibre-gl-worker.mjs
 * and maplibre-gl-shared.mjs). This script ensures they are emitted to apps/web/public/
 * for browser delivery and packaged into Next.js standalone outputs.
 */

import { copyFileSync, cpSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(__dirname, "..");
const publicDir = resolve(webRoot, "public");

const candidateDistDirs = [
  resolve(webRoot, "node_modules", "maplibre-gl", "dist"),
  resolve(webRoot, "..", "..", "node_modules", "maplibre-gl", "dist"),
];

const distDir = candidateDistDirs.find((dir) => existsSync(dir));

if (!distDir) {
  console.warn("[sync-maplibre-worker] Warning: maplibre-gl/dist not found in node_modules.");
} else {
  if (!existsSync(publicDir)) {
    mkdirSync(publicDir, { recursive: true });
  }

  const workerFiles = [
    "maplibre-gl-worker.mjs",
    "maplibre-gl-worker.mjs.map",
    "maplibre-gl-shared.mjs",
    "maplibre-gl-shared.mjs.map",
    "maplibre-gl-worker-dev.mjs",
    "maplibre-gl-worker-dev.mjs.map",
    "maplibre-gl-shared-dev.mjs",
    "maplibre-gl-shared-dev.mjs.map",
  ];

  for (const file of workerFiles) {
    const src = resolve(distDir, file);
    const dest = resolve(publicDir, file);
    if (existsSync(src)) {
      copyFileSync(src, dest);
    }
  }
}

// Standalone packaging synchronization if post-build
const isPostBuild = process.argv.includes("--post") || process.argv.includes("--standalone");
if (isPostBuild) {
  const standaloneWebDir = resolve(webRoot, ".next", "standalone", "apps", "web");
  const standaloneRootDir = resolve(webRoot, ".next", "standalone");

  if (existsSync(standaloneWebDir)) {
    if (existsSync(publicDir)) {
      cpSync(publicDir, resolve(standaloneWebDir, "public"), { recursive: true });
    }
    const staticDir = resolve(webRoot, ".next", "static");
    if (existsSync(staticDir)) {
      cpSync(staticDir, resolve(standaloneWebDir, ".next", "static"), { recursive: true });
    }
  }

  if (existsSync(standaloneRootDir) && existsSync(publicDir)) {
    cpSync(publicDir, resolve(standaloneRootDir, "public"), { recursive: true });
  }
}
