import { describe, it, expect } from "vitest";
import {
  intakeDetailHref,
  intakeInboxHref,
  normalizeIntakeDetailSection,
  parseUrlState,
  serializeUrlState,
} from "../urlState";

describe("urlState serialization and parsing", () => {
  it("should parse from URLSearchParams", () => {
    const params = new URLSearchParams(
      "stage=NEEDS_REVIEW&outcome=POSSIBLE_MATCH&source=src1&hz=hz1&sort=submitted_at_desc&view=map&selected=IN-3011&dialog=detail&section=identity&field=address&decision=transfer&receipt=RCPT-1&compare=true"
    );
    const parsed = parseUrlState(params);

    expect(parsed.filters.stage).toBe("NEEDS_REVIEW");
    expect(parsed.filters.matchOutcome).toBe("POSSIBLE_MATCH");
    expect(parsed.filters.sourceId).toBe("src1");
    expect(parsed.filters.heatZoneId).toBe("hz1");
    expect(parsed.sort).toBe("submitted_at_desc");
    expect(parsed.view).toBe("map");
    expect(parsed.selectedId).toBe("IN-3011");
    expect(parsed.dialog).toBe("detail");
    expect(parsed.activeSection).toBe("identity");
    expect(parsed.fixFieldKey).toBe("address");
    expect(parsed.decisionKind).toBe("transfer");
    expect(parsed.receiptId).toBe("RCPT-1");
    expect(parsed.compareTask).toBe(true);
  });

  it("should parse from record", () => {
    const record = {
      stage: "READY",
      outcome: "EXACT_DUPLICATE",
      source: "src2",
      hz: "hz2",
      sort: "updated_at_desc",
      view: "list",
      selected: "IN-3012",
      dialog: "decide",
      section: "commercial",
      field: "rent",
      decision: "pause",
      receipt: "RCPT-2",
      compare: "false",
    };
    const parsed = parseUrlState(record);

    expect(parsed.filters.stage).toBe("READY");
    expect(parsed.filters.matchOutcome).toBe("EXACT_DUPLICATE");
    expect(parsed.filters.sourceId).toBe("src2");
    expect(parsed.filters.heatZoneId).toBe("hz2");
    expect(parsed.sort).toBe("updated_at_desc");
    expect(parsed.view).toBe("list");
    expect(parsed.selectedId).toBe("IN-3012");
    expect(parsed.dialog).toBe("decide");
    expect(parsed.activeSection).toBe("commercial");
    expect(parsed.fixFieldKey).toBe("rent");
    expect(parsed.decisionKind).toBe("pause");
    expect(parsed.receiptId).toBe("RCPT-2");
    expect(parsed.compareTask).toBe(false);
  });

  it("should parse from query string", () => {
    const parsed = parseUrlState("?stage=SUBMITTED&outcome=NEW&compare=true");
    expect(parsed.filters.stage).toBe("SUBMITTED");
    expect(parsed.filters.matchOutcome).toBe("NEW");
    expect(parsed.compareTask).toBe(true);
  });

  it("should serialize to URLSearchParams", () => {
    const state = {
      filters: {
        stage: "NEEDS_REVIEW" as const,
        matchOutcome: "POSSIBLE_MATCH" as const,
        sourceId: "src1",
        heatZoneId: "hz1",
      },
      sort: "submitted_at_desc" as const,
      view: "map" as const,
      selectedId: "IN-3011",
      dialog: "detail" as const,
      activeSection: "identity",
      fixFieldKey: "address",
      decisionKind: "transfer" as const,
      receiptId: "RCPT-1",
      compareTask: true,
    };
    const serialized = serializeUrlState(state);

    expect(serialized.get("stage")).toBe("NEEDS_REVIEW");
    expect(serialized.get("outcome")).toBe("POSSIBLE_MATCH");
    expect(serialized.get("source")).toBe("src1");
    expect(serialized.get("hz")).toBe("hz1");
    expect(serialized.get("sort")).toBe("submitted_at_desc");
    expect(serialized.get("view")).toBe("map");
    expect(serialized.get("selected")).toBe("IN-3011");
    expect(serialized.get("dialog")).toBe("detail");
    expect(serialized.get("section")).toBe("identity");
    expect(serialized.get("field")).toBe("address");
    expect(serialized.get("decision")).toBe("transfer");
    expect(serialized.get("receipt")).toBe("RCPT-1");
    expect(serialized.get("compare")).toBe("true");
  });

  it("should preserve unrelated query parameters", () => {
    const existing = new URLSearchParams("unrelated=value&other=123&stage=READY");
    const state = {
      selectedId: "IN-9999",
    };
    const serialized = serializeUrlState(state, existing);

    expect(serialized.get("unrelated")).toBe("value");
    expect(serialized.get("other")).toBe("123");
    expect(serialized.get("selected")).toBe("IN-9999");
    expect(serialized.get("stage")).toBeNull(); // Cleared because it is an intake parameter
  });

  it("should round-trip cleanly", () => {
    const state = {
      filters: {
        stage: "SUBMITTED" as const,
        heatZoneId: "hz3",
      },
      sort: "due_at_asc" as const,
      view: "list" as const,
      selectedId: "IN-8888",
      dialog: "fix" as const,
      activeSection: "property",
      compareTask: false,
    };

    const serialized = serializeUrlState(state);
    const parsed = parseUrlState(serialized);

    expect(parsed.filters.stage).toBe(state.filters.stage);
    expect(parsed.filters.heatZoneId).toBe(state.filters.heatZoneId);
    expect(parsed.sort).toBe(state.sort);
    expect(parsed.view).toBe(state.view);
    expect(parsed.selectedId).toBe(state.selectedId);
    expect(parsed.dialog).toBe(state.dialog);
    expect(parsed.activeSection).toBe(state.activeSection);
    expect(parsed.compareTask).toBe(state.compareTask);
  });

  it("should round-trip assignmentSla and pause cleanly", () => {
    const state = {
      selectedId: "IN-3003",
      dialog: "assignmentSla" as const,
      decisionKind: "pause" as const,
    };

    const serialized = serializeUrlState(state);
    const parsed = parseUrlState(serialized);

    expect(parsed.selectedId).toBe(state.selectedId);
    expect(parsed.dialog).toBe(state.dialog);
    expect(parsed.decisionKind).toBe(state.decisionKind);
  });

  it("builds the real durable detail route and preserves task/compare context", () => {
    const href = intakeDetailHref(
      "INTAKE/with spaces",
      "role=expansion-manager&selected=OLD&dialog=detail&section=identity&compare=true&compareTarget=L-88&task=TASK-4",
    );

    expect(href).toBe(
      "/w/expansion/listings/intake/INTAKE%2Fwith%20spaces?role=expansion-manager&section=identity&compare=true&compareTarget=L-88&task=TASK-4",
    );
  });

  it("returns to the Inbox without leaking detail-only state", () => {
    expect(
      intakeInboxHref(
        "role=expansion-manager&section=evidence&compareTarget=L-88&task=TASK-4&stage=READY",
      ),
    ).toBe("/w/expansion/listings?role=expansion-manager&stage=READY");
  });

  it("normalizes route sections without accepting arbitrary query values", () => {
    expect(normalizeIntakeDetailSection("identity")).toBe("identity");
    expect(normalizeIntakeDetailSection("not-a-section")).toBe("timeline");
    expect(normalizeIntakeDetailSection(null, "error")).toBe("error");
  });
});
