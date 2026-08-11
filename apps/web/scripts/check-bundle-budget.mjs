#!/usr/bin/env node
/**
 * CLI wrapper for the First Load JS budget (ODP-ENG-FRONTEND-BUILD-001).
 *
 *   node scripts/check-bundle-budget.mjs [--dist .next] [--budget bundle-budget.json] [--json]
 *
 * Exits 1 when any route exceeds its budget, so `make node-check` and CI fail on
 * a bundle regression instead of printing a bigger number and moving on.
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { evaluateBudget, formatReport, measureBuild } from "./bundleBudget.mjs";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function parseArgs(argv) {
  const options = { dist: ".next", budget: "bundle-budget.json", json: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--json") {
      options.json = true;
    } else if (arg === "--dist" || arg === "--budget") {
      index += 1;
      if (index >= argv.length) {
        throw new Error(`${arg} requires a value`);
      }
      options[arg.slice(2)] = argv[index];
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return options;
}

const options = parseArgs(process.argv.slice(2));
const distDir = resolve(appRoot, options.dist);
const budgetPath = resolve(appRoot, options.budget);

if (!existsSync(resolve(distDir, "app-build-manifest.json"))) {
  console.error(
    `Bundle budget: no build output at ${distDir}.\n` +
      "Run `npm run build --workspace=@oday-plus/web` first.",
  );
  process.exit(1);
}

const budget = JSON.parse(readFileSync(budgetPath, "utf8"));
const measurement = measureBuild(distDir);
const result = evaluateBudget(measurement, budget);

if (options.json) {
  console.log(
    JSON.stringify(
      {
        ok: result.ok,
        sharedFirstLoadKb: Number(measurement.sharedKb.toFixed(1)),
        routes: result.rows.map((row) => ({
          route: row.route,
          firstLoadKb: Number(row.firstLoadKb.toFixed(1)),
          budgetKb: row.budgetKb,
          declared: row.declared,
        })),
        violations: result.violations.map((row) => row.route),
      },
      null,
      2,
    ),
  );
} else {
  console.log(formatReport(measurement, result));
}

process.exit(result.ok ? 0 : 1);
