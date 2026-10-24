import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { inspectReviewsReadProjection } from "../networkReviewProjection";
import type { NetworkReviewsSnapshot } from "../networkReviewTypes";

const root = existsSync(resolve(process.cwd(), "shared")) ? process.cwd() : resolve(process.cwd(), "../..");
// Exercise actual durable producers and normal read projection with explicitly
// offline identities. This supplies no production role grant or model result.
const producer = `
import json,tempfile
from pathlib import Path
from tests.unit.test_network_review_canonical_action_metadata import TENANT,components,decision_args,install_real_related_producers
from tests.unit.test_network_canonical_review_projection import report
results={}
with tempfile.TemporaryDirectory() as directory:
 for action in ('GO','WAIT','RETURN','REJECT'):
  engine,scores,decisions,audit,workflow,service=components(Path(directory)/(action+'.sqlite'),go_enabled=action=='GO')
  install_real_related_producers(engine,decisions,workflow,service)
  frozen=scores.save_report(report())
  decision=workflow.open_decision(frozen,created_by='offline-author',tenant_id=TENANT)
  service.decide_review(**decision_args(decision.decision_id,action))
  results[action]=service.snapshot()
  engine.close()
print(json.dumps(results))
`;
const actual = JSON.parse(execFileSync(process.env.ODP_TEST_PYTHON ?? resolve(root, ".venv/bin/python"), ["-c", producer], { cwd: root, encoding: "utf8" })) as Record<string, NetworkReviewsSnapshot>;

describe("canonical action evidence from actual durable producers", () => {
  it.each(["GO", "WAIT", "RETURN", "REJECT"])("admits %s using its stored action and own Approval identity", (action) => {
    const value = actual[action];
    expect(inspectReviewsReadProjection(value)).toBe("ready");
    expect(value.reviews[0].decision?.decision).toBe(action);
    expect(value.approvals![0].id).toBe(value.decisions![0].approval_id);
    expect(value.approvals![0].id).not.toBe(value.reviews[0].id);
  });

  it.each(["WAIT-as-GO", "conditions", "required-data", "Approval-alias", "Approval-status", "envelope-status", "actor", "history-action", "history-report", "history-tenant", "history-version"])("rejects inconsistent %s evidence", (failure) => {
    const value = structuredClone(actual[failure === "required-data" ? "RETURN" : "WAIT"]);
    if (failure === "WAIT-as-GO") value.reviews[0].decision!.decision = "GO";
    if (failure === "conditions") value.reviews[0].decision!.conditions = "invented changed conditions";
    if (failure === "required-data") value.decisions![0].required_data = [];
    if (failure === "Approval-alias") value.approvals![0].id = value.reviews[0].id;
    if (failure === "Approval-status") value.approvals![0].approvalStatus = "returned";
    if (failure === "envelope-status") value.approvals![0].status = "approved";
    if (failure === "actor") value.approvals![0].decidedBy = "forged actor";
    if (failure === "history-action") value.decisions![0].history!.at(-1)!.network_action = "GO";
    if (failure === "history-report") value.decisions![0].history!.at(-1)!.report_id = "other-report";
    if (failure === "history-tenant") value.decisions![0].history!.at(-1)!.tenant_id = "other-tenant";
    if (failure === "history-version") value.decisions![0].history!.at(-1)!.version = 1;
    expect(inspectReviewsReadProjection(value)).toBe("error");
  });
});
