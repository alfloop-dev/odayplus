import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InterventionWorkspace } from "../InterventionWorkspace";
import { currentEvidenceGrade, evaluationTimeUnverifiedNow, mayClaimCausal, mayClaimEffect, observationWindowMatureNow, parseIntervention, parseOwnedRead, type Intervention } from "../interventionContract";
import native from "./fixtures/nativeRecordedEvaluationTimezoneMemoryControls.json";

// Original native Window/resolver/_recommend/effect-copy ASTs compute these
// records with same ZoneInfo, ZoneInfo.no_cache and fixed-offset datetime
// objects. ISO wire does not retain that identity or fold. These controls and
// mocked DOM fetches are finite memory projections, not persisted producers,
// signed BFF, actual authenticated Principal or browser/runtime acceptance.
const asRow = (value: unknown) => value as Intervention;
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("native recorded evaluation and current timezone proof", () => {
  it.each([...native.exactOriginalTwoRecordedEdges, ...native.nativeComparisonMatrix])("retains legal native recorded inputs and their bounded current proof: $name", c => {
    const value = asRow(c.projection), original = JSON.stringify(value), actor = { actorSubjectId: value.review_context!.actor_subject_id, tenantId: value.review_context!.tenant_id, expiresAt: Math.floor(Date.now() / 1000) + 3600 };
    expect(parseIntervention(value)).not.toBeNull(); expect(parseOwnedRead(value, actor, value.intervention_id)).not.toBeNull();
    expect(value.effect?.observation_mature).toBe(c.nativeAwareMature); expect(value.effect?.evidence_level).toBe(c.nativeGrade);
    expect(value.effect?.recommendation).toBe(c.nativeRecommendation);
    expect(observationWindowMatureNow(value, c.clockMs)).toBe(c.expectedWindowMatureNow);
    expect(mayClaimEffect(value, c.clockMs)).toBe(c.expectedEffectClaim); expect(mayClaimCausal(value, c.clockMs)).toBe(c.expectedCausalClaim);
    expect(currentEvidenceGrade(value, c.clockMs)).toBe(c.expectedEffectClaim ? c.nativeGrade : null);
    if (!c.comparisonTruthAgreement) expect(evaluationTimeUnverifiedNow(value, c.clockMs)).toBe(true);
    expect(JSON.stringify(value)).toBe(original);
  });
  it.each(native.negativeUnsupportedFlags)("refuses a flag rejected by both supported native comparisons: $name", c => {
    const value = asRow(c.projection); expect(parseIntervention(value)).toBeNull();
    expect(mayClaimEffect(value, c.clockMs)).toBe(false); expect(mayClaimCausal(value, c.clockMs)).toBe(false); expect(currentEvidenceGrade(value, c.clockMs)).toBeNull();
  });
  it("proves identical ISO inputs cannot identify the original same-object/no_cache/fixed-offset comparison", () => {
    for (const pair of native.identicalISOInputsDifferentNativeMaturityProof) {
      expect(pair.inputsIdentical).toBe(true); expect(pair.rows).toHaveLength(3);
      expect(pair.rows[0].nativeSameTimezoneObject).toBe(true); expect(pair.rows[1].nativeSameTimezoneObject).toBe(false);
      expect(pair.rows[0].nativeAwareMature).not.toBe(pair.rows[1].nativeAwareMature);
      expect(pair.rows[1].nativeAwareMature).toBe(pair.rows[2].nativeAwareMature);
    }
  });
  it("does not invent timezone identity, fold or replication fields in retained native records", () => {
    for (const c of native.nativeComparisonMatrix) {
      for (const field of [c.projection, c.projection.observation_window, c.projection.effect, c.projection.outcome]) {
        expect("timezoneKey" in field).toBe(false); expect("fold" in field).toBe(false); expect("replicated" in field).toBe(false);
      }
    }
  });
  it("keeps window-only due proof separate from ambiguous evaluation or an absent effect", () => {
    const c = native.exactOriginalTwoRecordedEdges[0], value = structuredClone(asRow(c.projection));
    expect(observationWindowMatureNow(value, c.clockMs)).toBe(true); expect(evaluationTimeUnverifiedNow(value, c.clockMs)).toBe(true);
    value.effect = null; expect(observationWindowMatureNow(value, c.clockMs)).toBe(true); expect(mayClaimEffect(value, c.clockMs)).toBe(false);
  });
});

function mount(c: { projection: unknown; clockMs: number }) {
  const value = asRow(c.projection); vi.spyOn(Date, "now").mockReturnValue(c.clockMs);
  const actor = { actorSubjectId: value.review_context!.actor_subject_id, tenantId: value.review_context!.tenant_id, expiresAt: Math.floor(c.clockMs / 1000) + 3600 };
  window.history.replaceState(null, "", `/operator?ws=today&tab=interventions&entity=${value.intervention_id}`);
  const fetch = vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/operator-context" ? json(actor) : json(value));
  vi.stubGlobal("fetch", fetch); render(<InterventionWorkspace selectedId={value.intervention_id} onSelect={vi.fn()} />); return fetch;
}

describe("native saved timezone records in unchanged nine-section detail", () => {
  it.each(native.exactOriginalTwoRecordedEdges)("shows the retained native as-of record while withholding unsupported current claims: $name", async c => {
    const fetch = mount(c); await waitFor(() => expect(screen.getByRole("heading", { name: "8. Outcome & Effect" })).toBeInTheDocument());
    expect(screen.getByText(`${c.nativeGrade} · 評估於 ${c.projection.effect.evaluated_at}`)).toBeInTheDocument();
    expect(screen.getByText("✓ 已達紀錄的成熟時間；仍須核對最新成效評估。")).toBeInTheDocument();
    expect(screen.getByTestId("intervention-future-evaluation")).toBeInTheDocument(); expect(screen.getByTestId("intervention-effect-withheld")).toBeInTheDocument();
    expect(screen.getByText("尚未通過目前成熟度與評估核對")).toBeInTheDocument(); expect(screen.getByText("僅描述，不可宣稱因果。")).toBeInTheDocument();
    expect(screen.queryByText("123")).not.toBeInTheDocument(); expect(fetch.mock.calls.some(([url]) => String(url).includes("/approve"))).toBe(false);
  });
  it("keeps verified current evidence when different offsets agree under both native maturity comparisons", async () => {
    const c = native.nativeComparisonMatrix.find(c => c.name === "Different fixed offsets both comparison truths mature")!; mount(c);
    await waitFor(() => expect(screen.getByText("123")).toBeInTheDocument());
    expect(screen.getByText("L3", { selector: "dd" })).toBeInTheDocument(); expect(screen.getByText("✓ 原生證據符合因果宣稱閘門")).toBeInTheDocument();
    expect(screen.queryByTestId("intervention-future-evaluation")).not.toBeInTheDocument(); expect(screen.queryByTestId("intervention-effect-withheld")).not.toBeInTheDocument();
  });
});
