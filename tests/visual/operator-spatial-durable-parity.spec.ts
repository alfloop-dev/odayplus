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
test.describe.configure({ mode: "serial" });
for (const width of [1440, 390]) {
  for (const kind of ["approve", "reject"] as const) {
    test(`Spatial SQLite ${kind} preview decision reload at ${width}`, async ({ page }, info) => {
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
      expect(generation.proposals).toHaveLength(1);
      const proposal = generation.proposals[0];
      expect(proposal.composition_kind).toBe("MERGED");
      expect(proposal.member_cell_ids).toEqual(["cell-taipei-00", "cell-taipei-01"]);
      const id = proposal.proposal_id;
      const note = `本地 SQLite ${kind} ${width} 決策；測試生成歷史，不是正式上線核准。`;
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript(({ subject }) => {
        sessionStorage.setItem("oday.operator.role", "expansion-manager");
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
        const lineage = await page.request.get(`/api/v1/heatzones/zones/${proposal.zone_id}/lineage`, { headers });
        expect(lineage.status()).toBe(200);
        const body = await lineage.json();
        expect(body.is_active).toBe(true);
        expect(body.member_cell_ids).toEqual(proposal.member_cell_ids);
        await save("lineage", body);
        // Explicit test cleanup: next case may evaluate the same fixture cells.
        const rollback = await page.request.post(`/api/v1/heatzones/zones/${proposal.zone_id}/rollback`, { headers, data: { revert_reason: "Isolated browser test cleanup, not a production rollback" } });
        expect(rollback.status()).toBe(200);
        await save("cleanup-rollback", await rollback.json());
      } else {
        expect(persisted.proposal.rejection_reason).toBe(note);
        expect(persisted.compositions.filter((record: { is_active: boolean }) => record.is_active)).toHaveLength(0);
      }
    });
  }
}
