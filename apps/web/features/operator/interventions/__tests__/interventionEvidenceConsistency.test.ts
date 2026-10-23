import { describe, expect, it } from "vitest";
import { mayClaimCausal, mayClaimEffect, parseIntervention, type Intervention } from "../interventionContract";
import native from "./fixtures/nativeEvidenceResolverMemoryControls.json";
import actual from "./fixtures/nativeInterventionEffect.json";

// Expected grades were computed by the actual source-extracted Python native
// resolver. Projection alterations are finite memory controls, not new saved
// cases, native authentication, SQL, a real replication claim or browser proof.
describe("native evidence grades and observable consistency", () => {
  it.each(native.positiveRows)("keeps native resolver grade $nativeExpectedLevel for $name", ({ projection, nativeExpectedLevel, nativeResolverInputs }) => {
    const row = parseIntervention(projection);
    expect(row).not.toBeNull(); expect(row?.effect?.evidence_level).toBe(nativeExpectedLevel);
    expect(mayClaimEffect(row!)).toBe(Number(nativeExpectedLevel[1]) >= 1);
    expect(mayClaimCausal(row!)).toBe(Number(nativeExpectedLevel[1]) >= 3);
    expect(projection.effect.observation_mature).toBe(nativeResolverInputs.mature);
  });
  it.each(native.negativeRows)("refuses contradictory grade: $name", ({ projection }) => {
    expect(parseIntervention(projection)).toBeNull();
    expect(mayClaimEffect(projection as Intervention)).toBe(false);
    expect(mayClaimCausal(projection as Intervention)).toBe(false);
  });
  it("reproduces the exact independent L3-to-L4 nonrandomized counterexample without changing its actual source premise", () => {
    const actualL3 = structuredClone(actual.MATURE);
    expect(actualL3.outcome.randomized).toBe(false); expect(parseIntervention(actualL3)).not.toBeNull();
    actualL3.effect.evidence_level = "L4";
    expect(parseIntervention(actualL3)).toBeNull();
    expect(mayClaimCausal(actualL3 as Intervention)).toBe(false);
  });
  it("does not manufacture independent replication evidence for otherwise native-consistent L5", () => {
    const values = native.positiveRows.filter(row => row.nativeExpectedLevel === "L5"); expect(values.length).toBeGreaterThan(0);
    for (const value of values) {
      const row = parseIntervention(value.projection)!;
      expect(row.effect?.evidence_level).toBe("L5");
      expect("replicated" in row.effect!).toBe(false); expect("replicated" in row.outcome!).toBe(false);
    }
    expect(native.explicitL5Limitation).toContain("omits independent replicated input");
  });
});
