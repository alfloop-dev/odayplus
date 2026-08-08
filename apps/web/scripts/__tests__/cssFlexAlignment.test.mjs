import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const SKIPPED_DIRECTORIES = new Set(["node_modules", ".next", ".turbo"]);

function collectStylesheets(directory) {
  const found = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (!SKIPPED_DIRECTORIES.has(entry.name)) {
        found.push(...collectStylesheets(join(directory, entry.name)));
      }
    } else if (entry.name.endsWith(".css")) {
      found.push(join(directory, entry.name));
    }
  }
  return found;
}

/**
 * `align-items: start | end` (and friends) are box-alignment keywords. Flexbox
 * only accepts them via the newer spec level, so autoprefixer emits
 * "start value has mixed support, consider using flex-start instead" and
 * `next build` finishes with "Compiled with warnings". In a grid container the
 * same keywords are correct and stay untouched, which is why this guard is
 * scoped to declaration blocks that also set `display: flex`.
 *
 * ODP-ENG-FRONTEND-BUILD-001.
 */
const ALIGNMENT_PROPERTIES =
  /(align-items|align-self|align-content|justify-content|justify-items|justify-self)\s*:\s*(start|end)\s*(;|$)/g;

const FLEX_DISPLAY = /display\s*:\s*(inline-)?flex/;

function findFlexAlignmentKeywords(cssPath) {
  const source = readFileSync(cssPath, "utf8");
  const offences = [];
  // CSS modules here are flat: every declaration block is a `{ ... }` run with
  // no nested braces, so a non-greedy brace scan is enough to attribute a
  // declaration to its own rule.
  for (const block of source.matchAll(/\{([^{}]*)\}/g)) {
    const body = block[1];
    if (!FLEX_DISPLAY.test(body)) {
      continue;
    }
    for (const declaration of body.matchAll(ALIGNMENT_PROPERTIES)) {
      const line = source.slice(0, block.index + 1 + declaration.index).split("\n").length;
      offences.push(`${relative(appRoot, cssPath)}:${line}  ${declaration[0].trim()}`);
    }
  }
  return offences;
}

describe("CSS build hygiene", () => {
  const stylesheets = collectStylesheets(appRoot);

  it("finds the stylesheets it claims to guard", () => {
    expect(stylesheets.length).toBeGreaterThan(5);
  });

  it("uses flex-start / flex-end inside flex containers so next build stays warning-free", () => {
    const offences = stylesheets.flatMap(findFlexAlignmentKeywords);
    expect(offences).toEqual([]);
  });
});
