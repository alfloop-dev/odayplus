import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InterventionWorkspace } from "../InterventionWorkspace";
import { currentEvidenceGrade, evaluationTimeUnverifiedNow, mayClaimCausal, mayClaimEffect, observationWindowMatureNow, parseIntervention, parseOwnedRead, type Intervention } from "../interventionContract";
import native from "./fixtures/nativeWindowMoneyMemoryControls.json";

// These expected values use the actual native Window/decorator/resolver,
// Outcome.iromi and evaluate_effect expression ASTs, including real ZoneInfo
// wall-calendar/DST arithmetic. All mutations are declared finite memory
// projections; neither mocked DOM fetches nor this oracle are saved native
// producers, signed BFF, real authenticated Principal or browser evidence.
const row = (value: unknown) => value as Intervention;
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("native observation Window and measured amount coherence", () => {
  it.each(native.positiveRows)("preserves native consistent record and bounded current proof: $name", c => {
    const before = JSON.stringify(c.projection), value = row(c.projection), now = c.currentClockLowerBoundMs;
    expect(parseIntervention(value)).not.toBeNull();
    expect(observationWindowMatureNow(value, now)).toBe(c.expectedWindowMatureNow);
    expect(mayClaimEffect(value, now)).toBe(c.expectedEffectClaim); expect(mayClaimCausal(value, now)).toBe(c.expectedCausalClaim);
    expect(currentEvidenceGrade(value, now)).toBe(c.expectedEffectClaim ? value.effect!.evidence_level : null);
    expect(JSON.stringify(value)).toBe(before);
  });
  it.each(native.exactEightIndependentCounters)("refuses exact independent wire contradiction: $name", c => {
    const value = row(c.projection), now = c.currentClockLowerBoundMs;
    const actor = { actorSubjectId: value.review_context!.actor_subject_id, tenantId: value.review_context!.tenant_id, expiresAt: Math.floor(Date.now() / 1000) + 3600 };
    expect(parseIntervention(value)).toBeNull(); expect(parseOwnedRead(value, actor, value.intervention_id)).toBeNull();
    expect(mayClaimEffect(value, now)).toBe(false); expect(mayClaimCausal(value, now)).toBe(false); expect(currentEvidenceGrade(value, now)).toBeNull();
    if (c.name.includes("day endpoint") || c.name.includes("window")) expect(observationWindowMatureNow(value, now)).toBe(false);
  });
  it.each(native.additionalFractionalControls)("checks original full precision through day derivation: $name", c => {
    const value = row(c.projection); expect(parseIntervention(value)).toBeNull();
    expect(observationWindowMatureNow(value, c.currentClockLowerBoundMs)).toBe(false);
    expect(mayClaimEffect(value, c.currentClockLowerBoundMs)).toBe(false); expect(mayClaimCausal(value, c.currentClockLowerBoundMs)).toBe(false);
  });
  it("can prove a coherent mature window before any effect exists", () => {
    const c = native.positiveRows[0], value = structuredClone(row(c.projection)); value.effect = null; value.outcome = null; value.status = "OBSERVING";
    expect(parseIntervention(value)).not.toBeNull(); expect(observationWindowMatureNow(value, c.currentClockLowerBoundMs)).toBe(true);
    expect(mayClaimEffect(value, c.currentClockLowerBoundMs)).toBe(false); expect(currentEvidenceGrade(value, c.currentClockLowerBoundMs)).toBeNull();
  });
  it("keeps legal custom days without substituting the treatment default", () => {
    const value = row(native.positiveRows[0].projection); expect(value.kind).toBe("PRICE_CHANGE");
    expect(value.window_spec).toEqual({ outcome_window_days: 1, maturity_buffer_days: 1 }); expect(parseIntervention(value)).not.toBeNull();
  });
  it("retains actual native DST records without asserting a missing wire timezone identity", () => {
    const varying = native.positiveRows.filter(c => c.actualTimezoneKey && !c.expectedWindowMatureNow); expect(varying).toHaveLength(5);
    expect(varying.some(c => c.nativeElapsedOutcomeSeconds === 23 * 3600)).toBe(true);
    expect(varying.some(c => c.nativeElapsedOutcomeSeconds === 25 * 3600)).toBe(true);
    expect(varying.some(c => c.nativeElapsedOutcomeSeconds === 23.5 * 3600)).toBe(true);
    expect(varying.some(c => c.nativeElapsedMaturityBufferSeconds === 0)).toBe(true);
    for (const c of varying) {
      const value = parseIntervention(c.projection)!; expect(value.effect?.evidence_level).toBe("L3"); expect(c.wireTimezoneIdentityReturned).toBe(false);
      expect(evaluationTimeUnverifiedNow(value, c.currentClockLowerBoundMs)).toBe(true); expect(mayClaimCausal(value, c.currentClockLowerBoundMs)).toBe(false);
    }
  });
});

function mountDetail(c: { projection: unknown; currentClockLowerBoundMs: number }) {
  const value = row(c.projection); vi.spyOn(Date, "now").mockReturnValue(c.currentClockLowerBoundMs);
  const actor = { actorSubjectId: value.review_context!.actor_subject_id, tenantId: value.review_context!.tenant_id, expiresAt: Math.floor(c.currentClockLowerBoundMs / 1000) + 3600 };
  window.history.replaceState(null, "", `/operator?ws=today&tab=interventions&entity=${value.intervention_id}`);
  const fetch = vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/operator-context" ? json(actor) : json(value));
  vi.stubGlobal("fetch", fetch); render(<InterventionWorkspace selectedId={value.intervention_id} onSelect={vi.fn()} />); return fetch;
}

describe("native Window/money guards in existing detail and queue", () => {
  it.each(native.exactEightIndependentCounters)("fails the declared unit read before showing contradictory current evidence: $name", async c => {
    const fetch = mountDetail(c); await waitFor(() => expect(screen.getByTestId("intervention-error")).toHaveTextContent("INTERVENTION_CANONICAL_READ_INVALID"));
    expect(screen.queryByRole("heading", { name: "8. Outcome & Effect" })).not.toBeInTheDocument();
    expect(screen.queryByText("123")).not.toBeInTheDocument(); expect(fetch.mock.calls.some(([url]) => String(url).includes("/approve"))).toBe(false);
  });
  it("shows the saved DST grade with unverified maturity and withholds current cause and money", async () => {
    const c = native.positiveRows.find(c => c.name === "ZoneInfo NewYork spring23hour day")!; mountDetail(c);
    await waitFor(() => expect(screen.getByRole("heading", { name: "8. Outcome & Effect" })).toBeInTheDocument());
    expect(screen.getByText(`L3 · 評估於 ${c.projection.effect.evaluated_at}`)).toBeInTheDocument();
    expect(screen.getByText("◷ 觀察窗成熟度尚未核驗，目前不宣稱成效。")).toBeInTheDocument();
    expect(screen.getByTestId("intervention-future-evaluation")).toBeInTheDocument(); expect(screen.getByTestId("intervention-effect-withheld")).toBeInTheDocument();
    expect(screen.getByText("僅描述，不可宣稱因果。")).toBeInTheDocument(); expect(screen.queryByText("123")).not.toBeInTheDocument();
  });
  it("keeps mature awaiting-evaluation windows in the due queue without creating effect evidence", async () => {
    const c = native.positiveRows[0], value = structuredClone(row(c.projection)); value.effect = null; value.outcome = null; value.status = "OBSERVING";
    vi.spyOn(Date, "now").mockReturnValue(c.currentClockLowerBoundMs);
    const actor = { actorSubjectId: value.review_context!.actor_subject_id, tenantId: value.review_context!.tenant_id, expiresAt: Math.floor(c.currentClockLowerBoundMs / 1000) + 3600 };
    window.history.replaceState(null, "", "/operator?ws=today&tab=interventions&window_due=due");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/operator-context" ? json(actor) : json({ items: [value], count: 1 })));
    render(<InterventionWorkspace onSelect={vi.fn()} />); await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    const evidenceCell = within(screen.getByRole("table")).getByRole("cell", { name: `${value.observation_window!.label_maturity_time} 尚無成熟成效` });
    expect(evidenceCell).toHaveTextContent(value.observation_window!.label_maturity_time);
    expect(evidenceCell).toHaveTextContent("尚無成熟成效");
    expect(screen.getByRole("combobox", { name: "觀察窗篩選" })).toHaveValue("due");
    fireEvent.click(screen.getByRole("button", { name: "快速查看" })); await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
  });
});
