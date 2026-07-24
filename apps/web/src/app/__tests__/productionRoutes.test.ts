import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const API_BOUND_ROUTES = [
  "page.tsx",
  "admin/page.tsx",
  "audit/page.tsx",
  "avm/page.tsx",
  "expansion/page.tsx",
  "franchisee/page.tsx",
  "interventions/page.tsx",
  "learning/page.tsx",
  "netplan/page.tsx",
  "notifications/page.tsx",
  "operations/page.tsx",
  "pricing/page.tsx",
  "search/page.tsx",
  "settings/page.tsx",
  "tasks/page.tsx",
  "w/ai/models/page.tsx",
  "w/ai/releases/page.tsx",
  "w/ai/releases/[releaseId]/page.tsx",
  "w/audit/decisions/page.tsx",
  "w/audit/decisions/[decisionId]/page.tsx",
  "w/audit/evidence/page.tsx",
  "w/expansion/page.tsx",
  "w/expansion/candidates/page.tsx",
  "w/expansion/heatzone/page.tsx",
  "w/expansion/listings/page.tsx",
  "w/expansion/sitescore/page.tsx",
  "w/expansion/sitescore/[reportId]/page.tsx",
  "w/network/scenarios/page.tsx",
  "w/network/scenarios/[scenarioId]/page.tsx",
  "w/operations/alerts/page.tsx",
  "w/operations/forecast/page.tsx",
] as const;

describe("production API route rendering", () => {
  it.each(API_BOUND_ROUTES)(
    "forces a per-request authenticated read for %s",
    (route) => {
      const source = readFileSync(
        resolve(process.cwd(), "src/app", route),
        "utf8",
      );

      expect(source).toContain("getServerApiClient");
      expect(source).toContain('export const dynamic = "force-dynamic"');
    },
  );
});
