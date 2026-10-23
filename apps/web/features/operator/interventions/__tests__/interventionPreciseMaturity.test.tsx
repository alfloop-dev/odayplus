import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InterventionWorkspace } from "../InterventionWorkspace";
import { compareNativeInstants, currentEvidenceGrade, evaluationTimeUnverifiedNow, mayClaimCausal, mayClaimEffect, observationWindowMatureNow, parseIntervention, type Intervention } from "../interventionContract";
import native from "./fixtures/nativePreciseObservationMemoryControls.json";

// Native Window/resolver/aware-datetime results are source-extracted Python
// memory controls. The first two records retain the exact independent premise.
// Component fetches below are declared unit projections, not signed BFF,
// persisted producers, production authentication, SQL or browser evidence.
const record = (name: string) => native.rows.find(row => row.name === name)!;
const asRow = (value: unknown) => value as Intervention;
const boundary = record("Legitimate mature record at boundary with current coarse clock");
const verified = record("Mature record verified at next clock millisecond");
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("native full precision observation authority", () => {
  it.each(native.rows)("matches the native as-of/current distinction: $name", row => {
    const projection = asRow(row.projection), now = row.currentClockLowerBoundMs;
    expect(Boolean(parseIntervention(projection))).toBe(row.expectedParseAccepted);
    expect(mayClaimEffect(projection, now)).toBe(row.expectedEffectClaim);
    expect(mayClaimCausal(projection, now)).toBe(row.expectedCausalClaim);
    expect(observationWindowMatureNow(projection, now)).toBe(row.expectedWindowMatureNow);
    expect(currentEvidenceGrade(projection, now)).toBe(row.expectedEffectClaim ? projection.effect!.evidence_level : null);
  });
  it.each(native.comparisons)("matches actual aware native datetime ordering: $name", row => {
    expect(compareNativeInstants(row.a, row.b)).toBe(row.expected);
  });
  it.each(native.invalidISOInstants)("refuses invalid native instant %s through parser and raw predicates", instant => {
    const projection = structuredClone(verified.projection); projection.effect.evaluated_at = instant;
    expect(compareNativeInstants(instant, projection.observation_window.label_maturity_time)).toBeNull();
    expect(parseIntervention(projection)).toBeNull();
    expect(mayClaimEffect(asRow(projection), verified.currentClockLowerBoundMs)).toBe(false);
    expect(mayClaimCausal(asRow(projection), verified.currentClockLowerBoundMs)).toBe(false);
    expect(currentEvidenceGrade(asRow(projection), verified.currentClockLowerBoundMs)).toBeNull();
  });
  it("preserves a valid future native as-of record without manufacturing a current claim", () => {
    const before = JSON.stringify(boundary.projection), row = parseIntervention(boundary.projection)!;
    expect(row.effect?.evidence_level).toBe("L3"); expect(row.effect?.observation_mature).toBe(true);
    expect(evaluationTimeUnverifiedNow(row, boundary.currentClockLowerBoundMs)).toBe(true);
    expect(mayClaimEffect(row, boundary.currentClockLowerBoundMs)).toBe(false);
    expect(JSON.stringify(row)).toBe(before);
    expect(mayClaimEffect(asRow(verified.projection), verified.currentClockLowerBoundMs)).toBe(true);
  });
  it("cannot turn an earlier server read into current evidence by advancing only the browser clock", () => {
    const control = record("Future as-of record preserved against stale earlier read observation"), row = parseIntervention(control.projection)!;
    expect(observationWindowMatureNow(row, control.currentClockLowerBoundMs)).toBe(true);
    expect(evaluationTimeUnverifiedNow(row, control.currentClockLowerBoundMs)).toBe(true);
    expect(mayClaimCausal(row, control.currentClockLowerBoundMs)).toBe(false);
    expect(row.effect?.evidence_level).toBe("L3");
  });
  it.each([Number.NaN, Number.POSITIVE_INFINITY, verified.currentClockLowerBoundMs + .5])("refuses an unverifiable clock %s", now => {
    const row = asRow(verified.projection);
    expect(observationWindowMatureNow(row, now)).toBe(false); expect(mayClaimEffect(row, now)).toBe(false); expect(mayClaimCausal(row, now)).toBe(false);
  });
  it("does not let raw predicates bypass evidence consistency or a malformed read observation", () => {
    const row = structuredClone(verified.projection); row.effect.evidence_level = "L4";
    expect(mayClaimEffect(asRow(row), verified.currentClockLowerBoundMs)).toBe(false);
    const bad = structuredClone(verified.projection); bad.read_at = "2026-02-30T00:00:00Z";
    expect(mayClaimEffect(asRow(bad), verified.currentClockLowerBoundMs)).toBe(false);
    expect(currentEvidenceGrade(asRow(bad), verified.currentClockLowerBoundMs)).toBeNull();
  });
});

function mountDetail(initial = boundary) {
  let value = structuredClone(initial.projection);
  const now = vi.spyOn(Date, "now").mockReturnValue(initial.currentClockLowerBoundMs);
  const actor = { actorSubjectId: value.review_context.actor_subject_id, tenantId: value.review_context.tenant_id, expiresAt: Math.floor(verified.currentClockLowerBoundMs / 1000) + 3600 };
  window.history.replaceState(null, "", `/operator?ws=today&tab=interventions&entity=${value.intervention_id}`);
  const fetch = vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/operator-context" ? json(actor) : json(value));
  vi.stubGlobal("fetch", fetch);
  render(<InterventionWorkspace selectedId={value.intervention_id} onSelect={vi.fn()} />);
  return { fetch, update: () => { value = structuredClone(verified.projection); now.mockReturnValue(verified.currentClockLowerBoundMs); } };
}

describe("visible native maturity and current evidence", () => {
  it("withholds early maturity, current grade, causal assertion and money while retaining the labelled saved as-of record", async () => {
    const run = mountDetail();
    await waitFor(() => expect(screen.getByRole("heading", { name: "8. Outcome & Effect" })).toBeInTheDocument());
    expect(screen.getByText("◷ 觀察窗成熟度尚未核驗，目前不宣稱成效。")).toBeInTheDocument();
    expect(screen.getByTestId("intervention-future-evaluation")).toBeInTheDocument();
    expect(screen.getByText("尚未通過目前成熟度與評估核對")).toBeInTheDocument();
    expect(screen.getByText(`L3 · 評估於 ${boundary.projection.effect.evaluated_at}`)).toBeInTheDocument();
    expect(screen.getByTestId("intervention-effect-withheld")).toBeInTheDocument();
    expect(screen.getByText("僅描述，不可宣稱因果。")).toBeInTheDocument();
    expect(screen.queryByText("123")).not.toBeInTheDocument();
    expect(run.fetch.mock.calls.some(([url]) => String(url).includes("/approve"))).toBe(false);
  });
  it("exposes current grade and numbers only after both the current clock and fresh observed record reach the boundary", async () => {
    const run = mountDetail(); await waitFor(() => expect(screen.getByTestId("intervention-effect-withheld")).toBeInTheDocument());
    run.update(); fireEvent.click(screen.getByRole("button", { name: "重新載入" }));
    await waitFor(() => expect(screen.getByText("123")).toBeInTheDocument());
    expect(screen.getByText("✓ 已達紀錄的成熟時間；仍須核對最新成效評估。")).toBeInTheDocument();
    expect(screen.getByText("✓ 原生證據符合因果宣稱閘門")).toBeInTheDocument();
    expect(screen.queryByTestId("intervention-effect-withheld")).not.toBeInTheDocument();
    expect(screen.queryByTestId("intervention-future-evaluation")).not.toBeInTheDocument();
    expect(screen.getByText("L3", { selector: "dd" })).toBeInTheDocument();
  });
  it("uses the conservative full precision boundary for the visible queue filter and grade", async () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(boundary.currentClockLowerBoundMs);
    const value = structuredClone(boundary.projection), actor = { actorSubjectId: value.review_context.actor_subject_id, tenantId: value.review_context.tenant_id, expiresAt: Math.floor(verified.currentClockLowerBoundMs / 1000) + 3600 };
    window.history.replaceState(null, "", "/operator?ws=today&tab=interventions&window_due=due");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/operator-context" ? json(actor) : json({ items: [value], count: 1 })));
    render(<InterventionWorkspace onSelect={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("目前範圍尚無干預紀錄。")).toBeInTheDocument());
    now.mockReturnValue(verified.currentClockLowerBoundMs); value.read_at = verified.projection.read_at;
    fireEvent.click(screen.getByRole("button", { name: "重新載入" }));
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    const evidenceCell = within(screen.getByRole("table")).getByRole("cell", { name: `${value.observation_window.label_maturity_time} L3` });
    expect(evidenceCell).toHaveTextContent(value.observation_window.label_maturity_time);
    expect(evidenceCell).toHaveTextContent("L3");
  });
});
