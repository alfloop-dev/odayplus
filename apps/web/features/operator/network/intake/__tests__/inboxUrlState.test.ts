import { describe, expect, it } from "vitest";
import { DEFAULT_INBOX_FILTERS, parseInboxUrlState, serializeInboxUrlState, toIntakeInboxQuery } from "../inboxUrlState";

describe("shareable intake inbox navigation", () => {
  it("restores every API filter, view, pagination and selection from a direct link", () => {
    const query = new URLSearchParams("search=台北&intakeMethod=ASSISTED_ENTRY&intakeStage=READY&matchOutcome=NEW&slaState=BREACHED&heatZoneId=HZ-TPE&savedView=ready&viewMode=map&page=3&pageSize=25&sortBy=sourceId&sortOrder=asc&selected=IN-123");
    expect(parseInboxUrlState(query)).toEqual({ ...DEFAULT_INBOX_FILTERS, search: "台北", intakeMethod: "ASSISTED_ENTRY", intakeStage: "READY", matchOutcome: "NEW", slaState: "BREACHED", heatZoneId: "HZ-TPE", savedView: "ready", viewMode: "map", page: 3, pageSize: 25, sortBy: "sourceId", sortOrder: "asc", selectedIntakeId: "IN-123" });
  });

  it("restores legacy sort fields with their actual timestamp semantics", () => {
    expect(parseInboxUrlState(new URLSearchParams("stage=READY&outcome=NEW&hz=HZ-1&view=map&sort=status_asc"))).toMatchObject({ intakeStage: "READY", matchOutcome: "NEW", heatZoneId: "HZ-1", viewMode: "map", sortBy: "stage", sortOrder: "asc" });
    expect(parseInboxUrlState(new URLSearchParams("sort=due_at_asc"))).toMatchObject({ sortBy: "dueAt", sortOrder: "asc" });
    for (const sort of ["submitted_at_desc", "submitted_desc"]) expect(parseInboxUrlState(new URLSearchParams({ sort }))).toMatchObject({ sortBy: "submittedAt", sortOrder: "desc" });
  });

  it("round-trips exact identities, tri-state flags and timezone-bearing ranges without changing source context", () => {
    const dimensions = { sourceId: "source-id", submittedBy: "subject-1", ownerId: "subject-2", assignmentStatus: "CLAIMED",
      assignedAreaId: "area-7", observedFrom: "2026-10-01T08:00:00+08:00", observedTo: "2026-10-02T00:00:00Z",
      updatedFrom: "2026-10-03T00:00:00Z", updatedTo: "2026-10-04T00:00:00Z", masked: "false", restricted: "true", retryable: "false" };
    const params = new URLSearchParams({ ...dimensions, source: "legacy-shell-source", sortBy: "lastObservedAt", page: "3" });
    const state = parseInboxUrlState(params);
    expect(state).toMatchObject({ ...dimensions, sortBy: "lastObservedAt", page: 3 });
    const saved = serializeInboxUrlState(state, params);
    expect(saved.get("source")).toBe("legacy-shell-source");
    expect(parseInboxUrlState(saved)).toEqual(state);
    expect(toIntakeInboxQuery(state, "fallback-area")).toMatchObject({ ...dimensions, masked: false, restricted: true,
      retryable: false, selectedHeatZoneId: "fallback-area" });
    expect(toIntakeInboxQuery(DEFAULT_INBOX_FILTERS)).toMatchObject({ masked: undefined, restricted: undefined, retryable: undefined });
  });

  it("does not coerce arbitrary or conflicting boolean filters into true", () => {
    const state = parseInboxUrlState(new URLSearchParams("masked=0&restricted=true&restricted=false&retryable=FALSE"));
    expect(state).toMatchObject({ masked: "", restricted: "", retryable: "" });
  });

  it("preserves unrelated repeated values and the surrounding detail context", () => {
    const existing = new URLSearchParams("ws=network&tab=radar&source=src-1&extra=a&extra=b&selected=IN-1&dialog=detail&section=identity&stage=READY&sort=due_at_asc");
    const state = { ...parseInboxUrlState(existing), search: "台北", viewMode: "map" as const };
    const saved = serializeInboxUrlState(state, existing);
    expect(saved.getAll("extra")).toEqual(["a", "b"]);
    for (const key of ["ws", "tab", "source", "selected", "dialog", "section"]) expect(saved.get(key)).toBe(existing.get(key));
    expect(saved.has("stage")).toBe(false);
    expect(saved.has("sort")).toBe(false);
    expect(parseInboxUrlState(saved)).toEqual(state);
    expect(serializeInboxUrlState(state, saved).toString()).toBe(saved.toString());
  });

  it.each(["0", "-1", "1.5", "NaN", "9007199254740992"])("rejects unsafe page %s", (page) => {
    expect(parseInboxUrlState(new URLSearchParams({ page, pageSize: "101" }))).toMatchObject({ page: 1, pageSize: 10 });
  });

  it("does not choose the first of conflicting record identifiers or fall back to a hash", () => {
    expect(parseInboxUrlState(new URLSearchParams("selected=IN-1&selected=IN-2"), "#intake/IN-3").selectedIntakeId).toBeNull();
    expect(parseInboxUrlState(new URLSearchParams("selected="), "#intake/IN-3").selectedIntakeId).toBeNull();
    expect(parseInboxUrlState(new URLSearchParams("selected=IN-1&selected=IN-1")).selectedIntakeId).toBe("IN-1");
  });

  it("accepts a legacy encoded hash and refuses malformed or multi-segment links", () => {
    expect(parseInboxUrlState(new URLSearchParams(), "#intake/IN%20A").selectedIntakeId).toBe("IN A");
    for (const hash of ["#intake/%ZZ", "#intake/IN-1/IN-2", "#another"]) expect(parseInboxUrlState(new URLSearchParams(), hash)).toEqual(DEFAULT_INBOX_FILTERS);
  });
});
