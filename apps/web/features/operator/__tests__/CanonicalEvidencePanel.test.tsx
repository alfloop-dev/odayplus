import "@testing-library/jest-dom/vitest";
import { webcrypto } from "node:crypto";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CanonicalEvidencePanel } from "../governance/CanonicalEvidencePanel";
import {
  downloadCanonicalEvidence, fetchCanonicalEvidenceSources, previewCanonicalEvidence,
} from "../governance/canonicalEvidenceApi";

import { catalog, chainPreviewValue, digest, previewValue, reply, selection, source } from "./fixtures/canonicalEvidenceResponses";

// HTTP doubles exercise the real client/component. These are not policy
// records, production measurements, a PDF renderer test or human UAT.
function transport(options: { exportFailure?: boolean } = {}) {
  let created = false;
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/sources")) return reply(catalog());
    if (url.endsWith("/preview")) return reply(previewValue());
    if (url.endsWith("/export")) {
      if (options.exportFailure) return reply({}, 503);
      created = true;
      return reply({ export_id: "engineering-export", artifact_sha256: digest, delivery_status: "pending", download_ready: false, external_acknowledged: false }, 201);
    }
    if (url.endsWith("/exports")) return reply({ exports: created ? [{ export_id: "engineering-export", decision_id: source.decision_id, reason: selection.reason,
      generated_at: source.decided_at, delivery_status: "pending", external_acknowledged: false, download_ready: false, legal_hold: false, purged_at: null }] : [] });
    throw new Error(`Unexpected engineering request ${url} ${init?.method ?? "GET"}`);
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
async function chooseAndPreview() {
  await screen.findByRole("option", { name: /candidate-engineering/ });
  fireEvent.change(screen.getByLabelText("決策來源（SiteScore）"), { target: { value: source.decision_id } });
  fireEvent.change(screen.getByLabelText("匯出理由"), { target: { value: selection.reason } });
  fireEvent.click(screen.getByRole("button", { name: "預覽實際欄位與遮罩" }));
  await screen.findByTestId("canonical-evidence-preview");
}
beforeEach(() => {
  vi.stubEnv("ODP_REQUIRE_LIVE_DATA", "true");
  vi.stubGlobal("crypto", webcrypto);
  window.sessionStorage.setItem("oday.operator.subject", "engineering-account-a");
  window.sessionStorage.setItem("oday.operator.tenant", "engineering-tenant-a");
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); window.sessionStorage.clear(); });

describe("ordinary canonical evidence in the original Govern export area", () => {
  it.each([source.decision_id, "foreign-or-no-longer-authorized-decision"])("resolves requested %s only through the current server source catalog", async (id) => {
    transport();
    render(<CanonicalEvidencePanel roleId="pm-audit" canExport requestedDecisionId={id} />);
    await screen.findByRole("option", { name: /candidate-engineering/ });
    expect(screen.getByLabelText("決策來源（SiteScore）")).toHaveValue(id === source.decision_id ? id : "");
    expect(screen.getByTestId("governance-export-button")).toBeDisabled();
  });

  it("shows versioned partial prediction and human records without replacing missing confidence/execution/outcome", async () => {
    const ordinary = transport();
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => String(input).endsWith("/preview")
      ? Promise.resolve(reply(chainPreviewValue())) : ordinary(input, init)));
    render(<CanonicalEvidencePanel roleId="pm-audit" canExport />);
    await chooseAndPreview();
    expect(screen.getByText("已記錄決策鏈（PARTIAL）")).toBeInTheDocument();
    expect(screen.getByText("成效紀錄未取得")).toBeInTheDocument();
    expect(screen.getByText("未取得量測")).toBeInTheDocument();
    expect(screen.getByText("候選實現紀錄未取得")).toBeInTheDocument();
    expect(screen.getByTestId("canonical-evidence-preview").querySelector("pre")).toBeNull();
    expect(screen.queryByText(/NO_CANONICAL_DECISION_BOUND_SITESCORE_OUTCOME_OWNER/)).not.toBeInTheDocument();
    expect(screen.queryByText("unmasked-actor")).not.toBeInTheDocument();
  });

  it("does not fetch or fabricate sources without server-verified export authority", () => {
    const fetcher = transport();
    render(<CanonicalEvidencePanel roleId="pm-audit" canExport={false} />);
    expect(fetcher).not.toHaveBeenCalled();
    expect(screen.getByTestId("governance-export-button")).toBeDisabled();
    expect(screen.getByText(/尚未取得伺服器驗證/)).toBeInTheDocument();
  });

  it("requires real source/reason and masked preview, then records pending without a fake download", async () => {
    const fetcher = transport();
    render(<CanonicalEvidencePanel roleId="pm-audit" canExport />);
    expect(screen.getByTestId("governance-export-button")).toBeDisabled();
    await chooseAndPreview();
    expect(screen.getByText("決策人（已遮罩）")).toBeInTheDocument();
    expect(screen.queryByText("engineering-account-a")).not.toBeInTheDocument();
    expect(screen.getByText(/Outcome／SLA 匯出尚未支援/)).toBeInTheDocument();
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/export"))).toHaveLength(0);
    fireEvent.click(screen.getByTestId("governance-export-button"));
    await screen.findByText(/已建立 engineering-export/);
    const [, init] = fetcher.mock.calls.find(([url]) => String(url).endsWith("/export"))!;
    expect(JSON.parse(String(init?.body))).toEqual({ ...selection, expected_document_sha256: digest });
    expect(new Headers(init?.headers).get("Idempotency-Key")).toMatch(/^ik-evidence-/);
    expect(new Headers(init?.headers).has("X-Roles")).toBe(false);
    expect(screen.getByRole("button", { name: "下載 PDF" })).toBeDisabled();
  });

  it("keeps a confirmed export ID when history fails and rereads status without a second create", async () => {
    const ordinary = transport();
    let established = false;
    let historyUnavailable = true;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/export")) {
        const response = await ordinary(input, init);
        established = response.status === 201;
        return response;
      }
      if (url.endsWith("/exports") && established && historyUnavailable) return reply({}, 503);
      return ordinary(input, init);
    });
    vi.stubGlobal("fetch", fetcher);
    render(<CanonicalEvidencePanel roleId="pm-audit" canExport />);
    await chooseAndPreview();
    fireEvent.click(screen.getByTestId("governance-export-button"));
    expect(await screen.findByTestId("canonical-evidence-created")).toHaveTextContent("engineering-export");
    expect(await screen.findByRole("alert")).toHaveTextContent("已建立；投遞狀態重讀失敗");
    expect(screen.getByTestId("canonical-evidence-preview")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "重新讀取正式來源與投遞狀態" })).toBeEnabled());
    expect(screen.getByTestId("governance-export-button")).toBeDisabled();
    fireEvent.click(screen.getByTestId("governance-export-button"));
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/export"))).toHaveLength(1);
    historyUnavailable = false;
    fireEvent.click(screen.getByRole("button", { name: "重新讀取正式來源與投遞狀態" }));
    await screen.findByRole("button", { name: "下載 PDF" });
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByTestId("canonical-evidence-created")).toHaveTextContent("engineering-export");
    expect(screen.getByTestId("canonical-evidence-preview")).toBeInTheDocument();
    expect(screen.getByTestId("governance-export-button")).toBeDisabled();
    expect(screen.getByRole("button", { name: "下載 PDF" })).toBeDisabled();
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/export"))).toHaveLength(1);
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/preview"))).toHaveLength(1);
  });

  it("keeps an unknown create retry key after a read-only status refresh", async () => {
    const fetcher = transport({ exportFailure: true });
    render(<CanonicalEvidencePanel roleId="pm-audit" canExport />);
    await chooseAndPreview();
    fireEvent.click(screen.getByTestId("governance-export-button"));
    await screen.findByRole("alert");
    await waitFor(() => expect(screen.getByRole("button", { name: "重新讀取正式來源與投遞狀態" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "重新讀取正式來源與投遞狀態" }));
    await waitFor(() => expect(screen.getByTestId("governance-export-button")).toBeEnabled());
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/export"))).toHaveLength(1);
    fireEvent.click(screen.getByTestId("governance-export-button"));
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/export"))).toHaveLength(2));
    const writes = fetcher.mock.calls.filter(([url]) => String(url).endsWith("/export"));
    expect(new Headers(writes[0][1]?.headers).get("Idempotency-Key")).toBe(new Headers(writes[1][1]?.headers).get("Idempotency-Key"));
    expect(writes[0][1]?.body).toBe(writes[1][1]?.body);
    await screen.findByRole("alert");
  });

  it("invalidates the preview after selection details change and retains one retry key for uncertain create", async () => {
    const fetcher = transport({ exportFailure: true });
    render(<CanonicalEvidencePanel roleId="pm-audit" canExport />);
    await chooseAndPreview();
    fireEvent.change(screen.getByLabelText("匯出理由"), { target: { value: "Another actual reason" } });
    expect(screen.queryByTestId("canonical-evidence-preview")).not.toBeInTheDocument();
    expect(screen.getByTestId("governance-export-button")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("匯出理由"), { target: { value: selection.reason } });
    fireEvent.click(screen.getByTestId("governance-export-button"));
    await screen.findByRole("alert");
    await waitFor(() => expect(screen.getByTestId("governance-export-button")).toBeEnabled());
    fireEvent.click(screen.getByTestId("governance-export-button"));
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/export"))).toHaveLength(2));
    const keys = fetcher.mock.calls.filter(([url]) => String(url).endsWith("/export")).map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"));
    expect(keys[0]).toBe(keys[1]);
  });

  it.each(["preview", "create"] as const)("ignores a late %s response after the principal context changes, including messages", async (kind) => {
    let complete!: (value: Response) => void;
    const ordinary = transport();
    const fetcher = vi.fn((input: RequestInfo | URL, init?: RequestInit) => String(input).endsWith(kind === "create" ? "/export" : "/preview")
      ? new Promise<Response>((resolve) => { complete = resolve; }) : ordinary(input, init));
    vi.stubGlobal("fetch", fetcher);
    const view = render(<CanonicalEvidencePanel roleId="pm-audit" canExport />);
    if (kind === "create") { await chooseAndPreview(); fireEvent.click(screen.getByTestId("governance-export-button")); }
    else {
      await screen.findByRole("option", { name: /candidate-engineering/ });
      fireEvent.change(screen.getByLabelText("決策來源（SiteScore）"), { target: { value: source.decision_id } });
      fireEvent.change(screen.getByLabelText("匯出理由"), { target: { value: selection.reason } });
      fireEvent.click(screen.getByRole("button", { name: "預覽實際欄位與遮罩" }));
    }
    window.sessionStorage.setItem("oday.operator.subject", "engineering-account-b");
    view.rerender(<CanonicalEvidencePanel roleId="site-reviewer" canExport={false} />);
    await act(async () => { complete(reply(kind === "create" ? { export_id: "engineering-old-export", artifact_sha256: digest, delivery_status: "pending", download_ready: false, external_acknowledged: false } : previewValue(), kind === "create" ? 201 : 200)); });
    await waitFor(() => expect(screen.queryByTestId("canonical-evidence-preview")).not.toBeInTheDocument());
    expect(screen.queryByText(/engineering-old-export/)).not.toBeInTheDocument();
    expect(screen.getByTestId("governance-export-button")).toBeDisabled();
  });
});

describe("real ordinary evidence client fail-closed transport contracts", () => {
  it.each(["extra_field", "unmasked_actor", "wrong_type", "seed"])("rejects %s preview without disclosing an opaque/raw projection", async (tamper) => {
    const value = previewValue();
    if (tamper === "extra_field") Object.assign(value.document.sections.decision_log[0], { raw_report: { notAllowed: true } });
    else if (tamper === "unmasked_actor") value.document.sections.decision_log[0].decision_actor = "unmasked-actor";
    else if (tamper === "wrong_type") Object.assign(value.document.sections.decision_log[0], { source_snapshot_ids: "not-an-array" });
    else Object.assign(value, { source: "seed" });
    vi.stubGlobal("fetch", vi.fn(async () => reply(value)));
    await expect(previewCanonicalEvidence(selection, "pm-audit")).rejects.toThrow(/尚未完整驗證/);
  });

  it.each(["report_id", "report_version", "report_sha256", "approval_id", "masked_actor"])("refuses a same-digest recorded chain with mismatched %s", async (change) => {
    // Deliberately inconsistent HTTP response, not a canonical owner write.
    const value = JSON.parse(JSON.stringify(chainPreviewValue()));
    const chain = value.document.recorded_chain;
    if (change === "report_id") chain.prediction.report_id = "different-report";
    else if (change === "report_version") chain.prediction.report_version += 1;
    else if (change === "report_sha256") chain.prediction.report_sha256 = "b".repeat(64);
    else if (change === "approval_id") chain.human_decision.approval_id = "different-signature";
    else chain.human_decision.actor = "***";
    vi.stubGlobal("fetch", vi.fn(async () => reply(value)));
    await expect(previewCanonicalEvidence(selection, "pm-audit")).rejects.toThrow(/尚未完整驗證/);
  });

  it("shows unavailable transport and missing-source metadata as failures instead of fixtures", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ sources: [source] })));
    await expect(fetchCanonicalEvidenceSources("pm-audit")).rejects.toThrow(/尚未完整驗證/);
  });

  it.each(["valid", "hash", "media", "magic"])("verifies actual downloaded %s response bytes before creating a file", async (change) => {
    const bytes = new TextEncoder().encode(change === "magic" ? "NOT A PDF" : "%PDF-engineering-HTTP-double");
    const hash = Buffer.from(await webcrypto.subtle.digest("SHA-256", bytes)).toString("hex");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(bytes, { headers: {
      "Content-Type": change === "media" ? "application/json" : "application/pdf", "X-Artifact-SHA256": change === "hash" ? "b".repeat(64) : hash } })));
    const operation = downloadCanonicalEvidence("engineering/export?foreign", "pdf", "pm-audit");
    if (change === "valid") {
      const value = await operation;
      expect(value.blob.type).toBe("application/pdf");
      expect(value.filename).toBe("audit-evidence.pdf");
    } else await expect(operation).rejects.toThrow(/尚未完整驗證/);
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain("engineering%2Fexport%3Fforeign/download");
  });
});
