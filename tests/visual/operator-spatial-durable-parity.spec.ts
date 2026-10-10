import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

// No intercepted HTTP: generated integration history -> real mounted engine ->
// SQLite decisions/audit -> browser reload and a separate-process repository.
// NOT live maturity, PostgreSQL, deployed auth or independent visual approval.
const endpoint = "/api/v1/heatzones/merge-split";
const phase = process.env.NETWORK_PARITY_CAPTURE_PHASE ?? "after";
const split = process.env.NETWORK_SPATIAL_COMPOSITION === "split";
const selectedCase = process.env.NETWORK_SPATIAL_CASE;
if (split && !/^(approve|reject)-(1440|390)$/.test(selectedCase ?? "")) {
  throw new Error("Split runs require NETWORK_SPATIAL_CASE=approve|reject-1440|390 and a fresh scratch DB per case");
}
test.describe.configure({ mode: "serial" });
for (const width of [1440, 390]) {
  for (const kind of ["approve", "reject"] as const) {
    if (split && selectedCase !== `${kind}-${width}`) continue;
    test(`Spatial SQLite ${split ? "split " : ""}${kind} preview decision reload at ${width}`, async ({ page }, info) => {
      test.setTimeout(120_000);
      const subject = `spatial-durable-${kind}-${width}`;
      const headers = { "x-subject-id": subject, "x-roles": "expansion_user,site_reviewer", "x-tenant-id": "tenant-a" };
      const directory = process.env.NETWORK_PARITY_EVIDENCE_DIR ?? info.outputDir;
      await mkdir(directory, { recursive: true });
      async function save(name: string, value: unknown) {
        await writeFile(path.join(directory, `${kind}-${width}-${name}.json`), JSON.stringify(value, null, 2));
      }
      const evaluated = await page.request.post(`${endpoint}/evaluate`, { headers, data: {} });
      expect(evaluated.status()).toBe(200);
      const generation = await evaluated.json();
      await save("generation", generation);
      expect(generation.abstained).toBe(false);
      const proposals = generation.proposals.filter((p: { composition_kind: string }) => p.composition_kind === (split ? "SPLIT_CHILD" : "MERGED"));
      expect(proposals).toHaveLength(1);
      const generatedProposal = proposals[0];
      const generatedRead = await page.request.get(`${endpoint}/proposals/${generatedProposal.proposal_id}`, { headers });
      expect(generatedRead.status()).toBe(200);
      const proposal = await generatedRead.json();
      await save("generated-readback", proposal);
      expect(proposal.composition_kind).toBe(generatedProposal.composition_kind);
      expect(proposal.member_cell_ids).toEqual(generatedProposal.member_cell_ids);
      expect(proposal.child_partitions).toEqual(generatedProposal.child_partitions);
      expect(proposal.member_cell_ids).toEqual(split ? ["cell-kaohsiung-00", "cell-kaohsiung-01"] : ["cell-taipei-00", "cell-taipei-01"]);
      if (split) {
        expect(proposal.child_partitions).toEqual([["cell-kaohsiung-00"], ["cell-kaohsiung-01"]]);
        expect(proposal.child_zone_ids).toHaveLength(2);
        expect(new Set(proposal.child_zone_ids).size).toBe(2);
        expect(proposal.parent_zone_id).toBe(proposal.zone_id);
        expect(proposal.split_density_ratio).toBeGreaterThan(1);
      }
      const id = proposal.proposal_id;
      const note = `本地 SQLite ${kind} ${width} 決策；測試生成歷史，不是正式上線核准。`;
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript(({ subject }) => {
        sessionStorage.setItem("oday.operator.role", new URL(location.href).searchParams.get("spatial-test-role") === "pm-audit" ? "pm-audit" : "expansion-manager");
        sessionStorage.setItem("oday.operator.subject", subject);
        sessionStorage.setItem("oday.operator.tenant", "tenant-a");
      }, { subject });
      await page.goto("/operator?ws=network&tab=composition");
      await page.getByTestId("network-tab-7").click();
      const panel = page.getByTestId("heatzone-merge-split-panel");
      await panel.getByTestId(`proposal-item-${id}`).click();
      await expect(panel.getByTestId("proposal-detail")).toContainText(proposal.zone_id);
      const previewResponse = page.waitForResponse((r) => r.url().endsWith(`${endpoint}/proposals/${id}/preview`));
      await panel.getByTestId("btn-preview-proposal").click();
      const previewHTTP = await previewResponse;
      expect(previewHTTP.status()).toBe(200);
      const preview = await previewHTTP.json();
      await save("preview", preview);
      expect(preview.proposal.proposal_id).toBe(id);
      expect(preview.proposed_member_cells).toEqual(proposal.member_cell_ids);
      expect(preview.expected_ndcg_gain).toBe(proposal.ndcg_gain);
      await expect(panel.getByTestId("preview-box")).toBeVisible();
      async function capture(state: string, modal = false) {
        const target = modal ? page.getByTestId(`${kind}-modal`) : panel;
        const geometry = await target.evaluate((el) => {
          const box = (node: Element) => { const r = node.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, scrollWidth: node.scrollWidth }; };
          return { box: box(el.querySelector('[role="dialog"]') ?? el), controls: [...el.querySelectorAll("button, select, textarea")].map(box), documentWidth: document.documentElement.scrollWidth };
        });
        const axe = await new AxeBuilder({ page }).include(modal ? `[data-testid="${kind}-modal"]` : '[data-testid="heatzone-merge-split-panel"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
        await save(`${state}-geometry`, geometry);
        await save(`${state}-axe`, { violations: axe.violations, incomplete: axe.incomplete, passes: axe.passes.map((rule) => rule.id) });
        await page.screenshot({ path: path.join(directory, `${kind}-${width}-${state}.png`), fullPage: true, animations: "disabled" });
        expect(geometry.documentWidth).toBeLessThanOrEqual(width);
        for (const box of [geometry.box, ...geometry.controls]) {
          expect(box.x).toBeGreaterThanOrEqual(0);
          expect(box.x + box.width).toBeLessThanOrEqual(width);
          expect(box.scrollWidth).toBeLessThanOrEqual(Math.ceil(box.width));
        }
        if (modal) {
          expect(geometry.box.y).toBeGreaterThanOrEqual(0);
          expect(geometry.box.y + geometry.box.height).toBeLessThanOrEqual(900);
        }
        // Baseline records violations; after repair must enforce all scoped AA rules.
        if (phase === "after") expect(axe.violations).toEqual([]);
      }
      if (split) {
        // The actual PM/auditor persona has no heatzone VIEW or OVERRIDE.
        // Assert its genuine denied read state, not an invented read-only role.
        const deniedListResponse = page.waitForResponse((r) => r.url().endsWith(`${endpoint}/proposals`) && r.status() === 403);
        await page.goto("/operator?ws=network&tab=composition&spatial-test-role=pm-audit");
        await page.getByTestId("network-tab-7").click();
        const deniedListHTTP = await deniedListResponse;
        await expect(panel.getByRole("alert")).toContainText("HTTP 403");
        await expect(panel.getByTestId("proposal-detail")).toHaveCount(0);
        await expect(panel.getByTestId("btn-open-approve")).toHaveCount(0);
        await expect(panel.getByTestId("btn-open-reject")).toHaveCount(0);
        await capture("auditor-permission");
        const deniedReadback = await page.request.get(`${endpoint}/proposals`, { headers: { ...headers, "x-roles": "auditor" } });
        expect(deniedReadback.status()).toBe(403);
        await save("permission-read", { browserStatus: deniedListHTTP.status(), independentStatus: deniedReadback.status(), response: await deniedReadback.json() });
        const before = JSON.parse(execFileSync(path.resolve(".venv/bin/python"), ["-m", "tests.visual.spatial_durable_backend", "--inspect", id], { encoding: "utf8" }));
        const denials = [];
        for (const action of ["approve", "reject"]) {
          const denied = await page.request.post(`${endpoint}/proposals/${id}/${action}`, {
            headers: { ...headers, "x-roles": "auditor" },
            data: action === "approve" ? { notes: note } : { reason: note },
          });
          expect(denied.status()).toBe(403);
          denials.push({ action, status: denied.status(), response: await denied.json() });
        }
        const after = JSON.parse(execFileSync(path.resolve(".venv/bin/python"), ["-m", "tests.visual.spatial_durable_backend", "--inspect", id], { encoding: "utf8" }));
        expect(after).toEqual(before);
        expect(after.events).toEqual([]);
        await save("permission-denials", { denials, before, after });
        await page.goto("/operator?ws=network&tab=composition");
        await page.getByTestId("network-tab-7").click();
        await panel.getByTestId(`proposal-item-${id}`).click();
        await expect(panel.getByTestId("split-children")).toBeVisible();
        for (const childId of proposal.child_zone_ids) await expect(panel.getByTestId("split-children")).toContainText(childId);
        await panel.getByTestId("btn-preview-proposal").click();
        await expect(panel.getByTestId("preview-box")).toBeVisible();
      }
      await capture("preview");
      await panel.getByTestId(`btn-open-${kind}`).click();
      const modal = page.getByTestId(`${kind}-modal`);
      await modal.locator("textarea").fill(note);
      await capture("modal", true);
      const decisionResponse = page.waitForResponse((r) => r.url().endsWith(`${endpoint}/proposals/${id}/${kind}`) && r.request().method() === "POST");
      await modal.getByTestId(`btn-confirm-${kind}`).click();
      const decisionHTTP = await decisionResponse;
      expect(decisionHTTP.status()).toBe(200);
      // The browser client checks response.ok without consuming the POST body.
      // Read persisted state independently; do not wait on a discarded CDP body.
      await expect(modal).toBeHidden();
      await expect(panel.getByTestId("feedback-message")).toContainText("最新提案狀態已讀回確認");
      const readback = await page.request.get(`${endpoint}/proposals/${id}`, { headers });
      expect(readback.status()).toBe(200);
      const decidedProposal = await readback.json();
      await save("decision", { status: decisionHTTP.status(), request: decisionHTTP.request().postDataJSON(), readback: decidedProposal });
      expect(decidedProposal.status).toBe(kind === "approve" ? "APPROVED" : "REJECTED");
      expect(decidedProposal.approved_by).toBe(subject);
      await page.reload();
      await page.getByTestId("network-tab-7").click();
      await panel.getByTestId(`proposal-item-${id}`).click();
      await expect(panel.getByTestId("proposal-detail")).toContainText(proposal.zone_id);
      await expect(panel.getByTestId("btn-open-approve")).toHaveCount(0);
      await expect(panel.getByTestId("btn-open-reject")).toHaveCount(0);
      await capture("reloaded-terminal");
      const detail = await page.request.get(`${endpoint}/proposals/${id}`, { headers });
      expect(detail.status()).toBe(200);
      expect(await detail.json()).toEqual(decidedProposal);
      const repeated = await page.request.post(`${endpoint}/proposals/${id}/${kind}`, { headers, data: kind === "approve" ? { notes: note } : { reason: note } });
      expect(repeated.status()).toBe(422);
      await save("repeat-refused", { status: repeated.status(), response: await repeated.json() });
      // Child process opens a new SQLite connection without seeding or loader seam.
      const persisted = JSON.parse(execFileSync(path.resolve(".venv/bin/python"), ["-m", "tests.visual.spatial_durable_backend", "--inspect", id], { encoding: "utf8", maxBuffer: 2_000_000 }));
      await save("fresh-process", persisted);
      expect(persisted.proposal).toEqual(decidedProposal);
      expect(persisted.audit_chain).toEqual({ ok: true, issues: [] });
      expect(persisted.events).toHaveLength(1);
      expect(persisted.events[0].actor).toBe(subject);
      expect(persisted.events[0].integrity.worm_sink_id).toBe(`file://${path.resolve(process.env.NETWORK_SPATIAL_DURABLE_DIR!, "audit-worm")}`);
      expect(persisted.events[0].metadata[kind === "approve" ? "notes" : "reason"]).toBe(note);
      if (kind === "approve") {
        const created = persisted.compositions.filter((record: { decided_by: string }) => record.decided_by === subject);
        expect(created).toHaveLength(2);
        expect(created.map((record: { member_cell_id: string }) => record.member_cell_id).sort()).toEqual([...proposal.member_cell_ids].sort());
        for (const record of created) {
          expect(record.reverted_at).toBeNull();
          expect(record.decision_policy_version_id).toBe(proposal.policy_version_id);
          expect(record.model_version).toBe(proposal.model_version);
        }
        const lineageIds: string[] = split ? proposal.child_zone_ids : [proposal.zone_id];
        const lineages = [];
        for (const [index, zoneId] of lineageIds.entries()) {
          const lineage = await page.request.get(`/api/v1/heatzones/zones/${zoneId}/lineage`, { headers });
          expect(lineage.status()).toBe(200);
          const body = await lineage.json();
          expect(body.is_active).toBe(true);
          expect(body.member_cell_ids).toEqual(split ? proposal.child_partitions[index] : proposal.member_cell_ids);
          lineages.push(body);
        }
        await save("lineage", split ? lineages : lineages[0]);
        if (split) {
          expect(persisted.parent_compositions).toHaveLength(2);
          expect(persisted.parent_compositions.every((record: { is_active: boolean }) => !record.is_active)).toBe(true);
          for (const record of created) {
            expect(record.parent_zone_id).toBe(proposal.zone_id);
            expect(record.composition_kind).toBe("SPLIT_CHILD");
          }
          // A single fresh DB per split case; no fixture parent restoration or
          // post-decision cleanup that could obscure the terminal topology.
        } else {
          const rollback = await page.request.post(`/api/v1/heatzones/zones/${proposal.zone_id}/rollback`, { headers, data: { revert_reason: "Isolated browser test cleanup, not a production rollback" } });
          expect(rollback.status()).toBe(200);
          await save("cleanup-rollback", await rollback.json());
        }
      } else {
        expect(persisted.proposal.rejection_reason).toBe(note);
        expect(persisted.compositions.filter((record: { is_active: boolean }) => record.is_active)).toHaveLength(0);
        if (split) {
          expect(persisted.parent_compositions).toHaveLength(2);
          expect(persisted.parent_compositions.every((record: { is_active: boolean }) => record.is_active)).toBe(true);
        }
      }
    });
  }
}
