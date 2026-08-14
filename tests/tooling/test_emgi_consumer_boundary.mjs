import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { boundaryViolations } from "../../delivery_toolchain/governance/validate_emgi_consumer_boundary.mjs";

const policy = {
  authoritative_platform: { repository: "alfloop-dev/oday-data-platform" },
  forbidden_added_prefixes: ["modules/external_data/providers/"],
  forbidden_added_paths: ["modules/external_data/application/ingestion_service.py"],
  forbidden_added_name_tokens_under_external_data: ["provider", "connector"],
  consumer_code_prefixes: ["modules/sitescore/"],
  consumer_code_extensions: [".py"],
  forbidden_direct_reference_patterns: ["modules.external_data.providers"],
};

function withTempDir(callback) {
  const root = mkdtempSync(join(tmpdir(), "emgi-boundary-"));
  try { callback(root); } finally { rmSync(root, { recursive: true, force: true }); }
}

test("new provider file is rejected", () => withTempDir((root) => {
  const changes = [{ status: "A", path: "modules/external_data/providers/new_source.py" }];
  assert.ok(boundaryViolations(changes, policy, root).some((item) => item.includes("data-platform")));
}));

test("existing legacy provider may be modified for migration", () => withTempDir((root) => {
  const changes = [{ status: "M", path: "modules/external_data/providers/live.py" }];
  assert.deepEqual(boundaryViolations(changes, policy, root), []);
}));

test("direct provider import from SiteScore is rejected", () => withTempDir((root) => {
  const target = join(root, "modules/sitescore/consumer.py");
  mkdirSync(join(root, "modules/sitescore"), { recursive: true });
  writeFileSync(target, "from modules.external_data.providers import live\n", "utf8");
  const changes = [{ status: "M", path: "modules/sitescore/consumer.py" }];
  assert.ok(boundaryViolations(changes, policy, root).some((item) => item.includes("references external provider")));
}));

test("delivery tooling change is allowed", () => withTempDir((root) => {
  const changes = [{ status: "A", path: "delivery_toolchain/governance/new_guard.mjs" }];
  assert.deepEqual(boundaryViolations(changes, policy, root), []);
}));
