#!/usr/bin/env node
/** Fail closed when ODay Plus grows a new EMGI producer surface. */

import { execFileSync } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import { extname, basename, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const DEFAULT_POLICY = resolve(ROOT, "delivery_toolchain/governance/emgi-consumer-boundary.json");

export function parseNameStatus(text) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const fields = line.split("\t");
      return { status: fields[0], path: fields.at(-1) };
    });
}

function startsWithAny(path, prefixes) {
  return prefixes.some((prefix) => path.startsWith(prefix));
}

function isAdded(status) {
  return /^[ACR]/.test(status);
}

function isConsumerCode(path, policy) {
  return startsWithAny(path, policy.consumer_code_prefixes ?? [])
    && new Set(policy.consumer_code_extensions ?? []).has(extname(path));
}

export function boundaryViolations(changes, policy, root = ROOT) {
  const violations = [];
  const forbiddenPrefixes = policy.forbidden_added_prefixes ?? [];
  const forbiddenPaths = new Set(policy.forbidden_added_paths ?? []);
  const forbiddenTokens = (policy.forbidden_added_name_tokens_under_external_data ?? [])
    .map((value) => String(value).toLowerCase());
  const directPatterns = policy.forbidden_direct_reference_patterns ?? [];

  for (const change of changes) {
    const path = change.path;
    const added = isAdded(change.status);
    if (added && (startsWithAny(path, forbiddenPrefixes) || forbiddenPaths.has(path))) {
      violations.push(`${path}: new EMGI producer code belongs to alfloop-dev/oday-data-platform`);
    }

    if (added && path.startsWith("modules/external_data/")) {
      const filename = basename(path).toLowerCase();
      if (forbiddenTokens.some((token) => filename.includes(token))) {
        violations.push(`${path}: new external-data producer-shaped file is forbidden in odayplus`);
      }
    }

    if (!isConsumerCode(path, policy)) continue;
    const target = resolve(root, path);
    if (!existsSync(target)) continue;
    const text = readFileSync(target, "utf8");
    for (const pattern of directPatterns) {
      if (text.includes(pattern)) {
        violations.push(`${path}: product consumer references external provider surface \`${pattern}\` directly`);
      }
    }
  }
  return [...new Set(violations)].sort();
}

function parseArgs(argv) {
  const result = { policy: DEFAULT_POLICY };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--base-sha") result.baseSha = argv[++index];
    else if (arg === "--head-sha") result.headSha = argv[++index];
    else if (arg === "--policy") result.policy = resolve(argv[++index]);
    else throw new Error(`unknown argument: ${arg}`);
  }
  if (!result.baseSha || !result.headSha) throw new Error("--base-sha and --head-sha are required");
  return result;
}

export function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const policy = JSON.parse(readFileSync(args.policy, "utf8"));
  const diff = execFileSync(
    "git",
    ["diff", "--name-status", "-M", args.baseSha, args.headSha],
    { cwd: ROOT, encoding: "utf8" },
  );
  const changes = parseNameStatus(diff);
  const violations = boundaryViolations(changes, policy, ROOT);
  console.log(JSON.stringify({
    authoritative_platform: policy.authoritative_platform,
    changed_paths: changes.map((change) => change.path),
    violations,
  }, null, 2));
  return violations.length === 0 ? 0 : 1;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 2;
  }
}
