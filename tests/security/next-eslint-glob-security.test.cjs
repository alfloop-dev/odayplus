const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");
const { test } = require("node:test");
const { Linter } = require("eslint");
const plugin = require("@next/eslint-plugin-next");
const { getRootDirs } = require("@next/eslint-plugin-next/dist/utils/get-root-dirs");

const packageRoot = path.dirname(require.resolve("@next/eslint-plugin-next/package.json"));

test("the installed private fork retains its declared source and copyright notices", () => {
  const pkg = require("@next/eslint-plugin-next/package.json");
  assert.equal(pkg.version, "15.5.27-oday.1");
  assert.equal(pkg.private, true);
  assert.equal(Object.keys(plugin.rules).length, 21);
  const provenance = JSON.parse(fs.readFileSync(path.join(packageRoot, "UPSTREAM.json"), "utf8"));
  for (const [name, expected] of Object.entries(provenance.source_sha256)) {
    const actual = crypto.createHash("sha256").update(fs.readFileSync(path.join(packageRoot, name))).digest("hex");
    assert.equal(actual, expected, name);
  }
  for (const component of provenance.components) {
    assert.match(fs.readFileSync(path.join(packageRoot, component.license_path), "utf8"), /Copyright/i);
  }
  for (const name of ["compile.js", "expand.js"]) {
    assert.equal(fs.existsSync(path.join(packageRoot, "dist/utils/dir-glob/brace-range-parser", name)), false);
  }
});

test("root discovery retains brace, glob, duplicate, exclusion and path semantics", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "oday-next-glob-"));
  try {
    for (const name of ["alpha", "beta", "site1", "site2"]) fs.mkdirSync(path.join(root, "roots", name), { recursive: true });
    fs.writeFileSync(path.join(root, "roots", "ordinary-file"), "not a directory");
    const discover = (rootDir) => getRootDirs({ cwd: root, settings: { next: { rootDir } } });
    const expected = (names) => names.map((name) => path.join(root, "roots", name)).sort();
    assert.deepEqual(discover(path.join(root, "roots/{alpha,beta}")).sort(), expected(["alpha", "beta"]));
    assert.deepEqual(discover(path.join(root, "roots/site{1..2}")).sort(), expected(["site1", "site2"]));
    assert.deepEqual(discover(path.join(root, "roots/@(alpha|beta)")).sort(), expected(["alpha", "beta"]));
    assert.deepEqual(discover([path.join(root, "roots/*"), `!${path.join(root, "roots/beta")}`]).sort(), expected(["alpha", "beta", "site1", "site2"]));
    assert.deepEqual(discover([path.join(root, "roots/alpha"), path.join(root, "roots/alpha")]), expected(["alpha", "alpha"]));
    assert.deepEqual(discover(path.join(root, "roots/alpha").replace(/\//g, "\\")), expected(["alpha"]));
    assert.throws(() => discover(path.join(root, "roots/site{0..1000}")), /range limit/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("the original internal-link lint rule remains enforced for multiple discovered roots", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "oday-next-rule-"));
  try {
    const pages = path.join(root, "roots/alpha/pages");
    fs.mkdirSync(pages, { recursive: true });
    fs.writeFileSync(path.join(pages, "index.js"), "export default function Page() {}");
    const linter = new Linter({ cwd: root });
    linter.defineRule("next-internal-link", plugin.rules["no-html-link-for-pages"]);
    const config = {
      parserOptions: { ecmaVersion: 2021, sourceType: "module", ecmaFeatures: { jsx: true } },
      rules: { "next-internal-link": 2 },
      settings: { next: { rootDir: path.join(root, "roots/{alpha,beta}") } },
    };
    const run = (code) => linter.verify(code, config, { filename: path.join(pages, "index.js") });
    assert.equal(run('export default () => <a href="/">Home</a>').length, 1);
    assert.equal(run('import Link from "next/link"; export default () => <Link href="/">Home</Link>').length, 0);
    assert.equal(run('export default () => <a href="https://example.invalid">External</a>').length, 0);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("deep brace input stays bounded without the vulnerable recursive walker", () => {
  const child = `const helper = require(process.argv[1]);
    try {
      const depth = 3500;
      const roots = helper.getRootDirs({cwd: process.cwd(), settings: {next: {
        rootDir: '{'.repeat(depth) + 'a,b' + '}'.repeat(depth)
      }}});
      process.stdout.write(JSON.stringify({kind: 'value', count: roots.length}));
    } catch (error) {
      process.stdout.write(JSON.stringify({kind: 'error', name: error.name,
        code: error.code, message: error.message.slice(0, 100)}));
    }`;
  const result = spawnSync(process.execPath, ["--max-old-space-size=96", "-e", child,
    require.resolve("@next/eslint-plugin-next/dist/utils/get-root-dirs")], {
    encoding: "utf8", timeout: 8000, maxBuffer: 4096,
  });
  assert.equal(result.error, undefined);
  assert.equal(result.signal, null);
  assert.equal(result.status, 0);
  const observed = JSON.parse(result.stdout);
  assert.doesNotMatch(observed.message || "", /call stack|heap out of memory/i);
  assert.ok(observed.kind === "value" || observed.code === "ENAMETOOLONG");
});
