import { describe, expect, it } from "vitest";
import { nativeStoreLabels } from "../storeOpsNativeSummary";

const facts = () => ({
  storeId: "native-store",
  observedAt: "2026-10-14T12:00:00+00:00",
  storeFormat: {
    status: "recorded", value: "REGISTERED_CODE", source: "core.stores.store_format_code",
    recordId: "native-store", effectiveFrom: "2026-01-01T00:00:00Z",
    effectiveTo: "2027-01-01T00:00:00Z",
  },
  machines: {
    status: "recorded", value: 2, source: "core.machines",
    basis: "current_nonretired_registered_records", recordIds: ["machine-a", "machine-b"],
    physicalInventoryComplete: null,
  },
});

describe("native Store Ops facts", () => {
  it("binds native code and registered count to the exact selected store", () => {
    expect(nativeStoreLabels(facts(), "native-store")).toMatchObject({
      format: "REGISTERED_CODE", machines: "2 台（登錄）",
    });
    expect(nativeStoreLabels(facts(), "other-store")).toMatchObject({
      format: "不可用", machines: "不可用",
    });
  });

  it.each([undefined, null, {}, { ...facts(), observedAt: "2026-10-14T12:00:00" }])(
    "keeps missing or unbound observations unavailable", (value) => {
      expect(nativeStoreLabels(value, "native-store")).toMatchObject({
        format: "不可用", machines: "不可用",
      });
    },
  );

  it.each([
    { value: 0, recordIds: [] },
    { value: 2, recordIds: ["same", "same"] },
    { value: 3, recordIds: ["one", "two"] },
    { value: 2, physicalInventoryComplete: true },
    { value: 2, status: "unavailable" },
    { value: 2, source: "fixture" },
  ])("refuses invented completeness, inconsistent counts and unknown status", (changes) => {
    const input = facts();
    expect(nativeStoreLabels({ ...input, machines: { ...input.machines, ...changes } }, "native-store").machines)
      .toBe("不可用");
  });

  it("refuses a stale or foreign native store-format record", () => {
    const input = facts();
    expect(nativeStoreLabels({ ...input, storeFormat: { ...input.storeFormat, recordId: "foreign" } }, "native-store").format)
      .toBe("不可用");
    expect(nativeStoreLabels({ ...input, storeFormat: { ...input.storeFormat, effectiveTo: input.observedAt } }, "native-store").format)
      .toBe("不可用");
  });

  it("retains a native observation one hundred microseconds before the exclusive end", () => {
    const input = facts();
    input.observedAt = "2026-10-14T12:00:00.000800+00:00";
    input.storeFormat.effectiveTo = "2026-10-14T12:00:00.000900+00:00";
    expect(nativeStoreLabels(input, "native-store").format).toBe("REGISTERED_CODE");
  });

  it("rejects a recorded claim one hundred microseconds before the inclusive start", () => {
    const input = facts();
    input.observedAt = "2026-10-14T12:00:00.000800+00:00";
    input.storeFormat.effectiveFrom = "2026-10-14T12:00:00.000900+00:00";
    expect(nativeStoreLabels(input, "native-store").format).toBe("不可用");
  });

  it("rejects February 30 instead of normalizing it into March", () => {
    const input = facts();
    input.observedAt = "2026-02-30T12:00:00+00:00";
    input.storeFormat.effectiveFrom = "2026-02-01T00:00:00+00:00";
    input.storeFormat.effectiveTo = "2026-04-01T00:00:00+00:00";
    expect(nativeStoreLabels(input, "native-store")).toMatchObject({
      format: "不可用", machines: "不可用",
    });
  });

  it("compares explicit offsets without inventing a named timezone", () => {
    const input = facts();
    input.observedAt = "2026-10-14T12:00:00.000800Z";
    input.storeFormat.effectiveFrom = "2026-10-14T20:00:00.000800+08:00";
    input.storeFormat.effectiveTo = "2026-10-14T07:00:00.000900-05:00";
    expect(nativeStoreLabels(input, "native-store").format).toBe("REGISTERED_CODE");
    input.storeFormat.effectiveTo = "2026-10-14T07:00:00.000800-05:00";
    expect(nativeStoreLabels(input, "native-store").format).toBe("不可用");
  });

  it.each([
    "2026-10-14T12:00:00", "2026-10-14T12:00:00-00:00",
    "2026-10-14T12:00:00[Asia/Taipei]", "2026-10-14T12:00:00+08:00[Asia/Taipei]",
    "2026-10-14T12:00:00.1234567Z", "2026-10-14T12:00:00+00:00:00.000001",
    "2026-10-14T12:00:00+24:00", "2026-10-14T12:00:00+00:60",
    "2026-10-14T24:00:00Z", "2026-10-14T12:00:60Z",
    "2026-04-31T12:00:00Z", "0000-10-14T12:00:00Z",
  ])("keeps malformed, ambiguous or excess-precision native instants unavailable: %s", (observedAt) => {
    expect(nativeStoreLabels({ ...facts(), observedAt }, "native-store")).toMatchObject({
      format: "不可用", machines: "不可用",
    });
  });
});
