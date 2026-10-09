import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Static guard for the shared Operator chrome (audit ODP-UI-AUDIT-20261008,
 * F01-F06). happy-dom does not apply CSS modules, so these rules pin the
 * stylesheet invariants that the browser geometry spec
 * (tests/e2e/operator-shell-layout.spec.ts) measures for real.
 */
const css = readFileSync(join(__dirname, "..", "operator.module.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

type Rule = { selector: string; body: string };

function rules(source: string): Rule[] {
  const result: Rule[] = [];
  const pattern = /([^{}]+)\{([^{}]*)\}/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(source))) {
    result.push({ selector: match[1].trim(), body: match[2] });
  }
  return result;
}

function selectorList(rule: Rule): string[] {
  return rule.selector.split(",").map((part) => part.trim());
}

const allRules = rules(css);

function rulesFor(selector: string): Rule[] {
  return allRules.filter((rule) => selectorList(rule).includes(selector));
}

describe("operator.module.css shared chrome contract", () => {
  it("never styles shared chrome conditionally on mounted workspace content", () => {
    expect(css).not.toMatch(/:has\(/);
  });

  it("defines the base .chip exactly once so the environment badge cannot be overridden", () => {
    const chipBases = allRules.filter((rule) => selectorList(rule).includes(".chip"));
    expect(chipBases).toHaveLength(1);
  });

  it("establishes the design font chain and border-box model on the console root", () => {
    const [root] = rulesFor(".console");
    expect(root.body).toMatch(/font-family:\s*var\(--operator-font-family\)/);
    expect(root.body).toMatch(/--operator-font-family:\s*var\(--font-noto-sans-tc, "Noto Sans TC"\)/);
    expect(root.body).toMatch(/--operator-font-mono:\s*var\(--font-ibm-plex-mono, "IBM Plex Mono"\)/);
    expect(root.body).toMatch(/font-size:\s*13px/);
    expect(root.body).toMatch(/line-height:\s*1\.55/);

    const universal = allRules.find((rule) => selectorList(rule).includes(".console *"));
    expect(universal?.body).toMatch(/box-sizing:\s*border-box/);
    expect(css).toMatch(/\.console :where\(button, input, select, textarea\)\s*\{\s*font-family:\s*inherit/);
  });

  it("imposes no page-level desktop minimum width on the shell", () => {
    for (const selector of [".console", ".topbar", ".topbarInner", ".shell"]) {
      for (const rule of rulesFor(selector)) {
        const minWidth = /min-width:\s*(\d+)px/.exec(rule.body);
        expect(minWidth, `${selector} min-width`).toBeNull();
      }
    }
  });

  it("centres the header content row at the design max width", () => {
    const [inner] = rulesFor(".topbarInner");
    expect(inner.body).toMatch(/max-width:\s*1720px/);
    expect(inner.body).toMatch(/margin:\s*0 auto/);
    expect(inner.body).toMatch(/min-height:\s*52px/);
    expect(inner.body).toMatch(/padding:\s*6px 20px/);
  });
});
