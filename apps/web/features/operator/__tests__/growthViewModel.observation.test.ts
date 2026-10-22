import { describe, expect, it } from "vitest";
import { GROWTH_ITEMS, hasMatureGrowthObservation, judgeEffectiveness, type GrowthItem } from "../growthViewModel";

const item = (): GrowthItem => ({
  ...GROWTH_ITEMS[0],
  status: "OUTCOME_READY", observedLift: 3, targetLift: 2, evidenceLevel: "L3",
  observation: { status: "MATURE", startedAt: "2026-10-01T00:00:00Z",
    endsAt: "2026-10-15T00:00:00Z", days: 14, observedAt: "2026-10-15T00:00:00Z" },
});

describe("Growth uses recorded server observation windows", () => {
  it("does not treat legacy outcome-ready status as elapsed evidence", () => {
    const row = item(); delete row.observation;
    expect(judgeEffectiveness(row)).toBe("PENDING");
  });
  it("waits until the exact end on the source clock", () => {
    const row = item(); row.observation!.observedAt = "2026-10-14T23:59:59.999Z";
    expect(judgeEffectiveness(row)).toBe("PENDING");
    row.observation!.observedAt = "2026-10-15T00:00:00Z";
    expect(judgeEffectiveness(row)).toBe("EFFECTIVE");
  });
  it.each([null, 0, 366, 1.5])("refuses an invalid duration %s", (days) => {
    const row = item(); row.observation!.days = days;
    expect(hasMatureGrowthObservation(row)).toBe(false);
  });
  it("refuses a claimed mature flag with mismatched recorded bounds", () => {
    const row = item(); row.observation!.endsAt = "2026-10-02T00:00:00Z";
    expect(judgeEffectiveness(row)).toBe("PENDING");
  });
  it("preserves the lifecycle and causal evidence gates after elapsed time", () => {
    const row = item(); row.status = "OBSERVING";
    expect(judgeEffectiveness(row)).toBe("PENDING");
    row.status = "OUTCOME_READY"; row.evidenceLevel = "L1";
    expect(judgeEffectiveness(row)).toBe("INCONCLUSIVE");
  });
});
