// Regenerates styles/tokens.css from the TS token objects so the CSS-variable
// representation can never drift from the TS values (tokens doc §1.1).
//
// Run with Node >= 22 type-stripping:  npm run build:css
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { generateCss } from "../src/css.ts";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, "../styles/tokens.css");
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, generateCss(), "utf8");
console.log(`wrote ${out}`);
