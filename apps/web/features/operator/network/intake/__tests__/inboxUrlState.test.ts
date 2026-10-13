import { describe, expect, it } from "vitest";
import { DEFAULT_INBOX_FILTERS, parseInboxUrlState, serializeInboxUrlState } from "../inboxUrlState";

describe("shareable intake inbox navigation", () => {
  it("restores every API filter, view, pagination and selection from a direct link", () => {
    const query = new URLSearchParams("search=台北&intakeMethod=ASSISTED_ENTRY&intakeStage=READY&matchOutcome=NEW&slaState=BREACHED&heatZoneId=HZ-TPE&savedView=ready&viewMode=map&page=3&pageSize=25&sortBy=sourceId&sortOrder=asc&selected=IN-123");
    expect(parseInboxUrlState(query)).toEqual({ search: "台北", intakeMethod: "ASSISTED_ENTRY", intakeStage: "READY", matchOutcome: "NEW", slaState: "BREACHED", heatZoneId: "HZ-TPE", savedView: "ready", viewMode: "map", page: 3, pageSize: 25, sortBy: "sourceId", sortOrder: "asc", selectedIntakeId: "IN-123" });
  });

  it("restores supported legacy sort fields and defaults unsupported timestamp sorts", () => {
    expect(parseInboxUrlState(new URLSearchParams("stage=READY&outcome=NEW&hz=HZ-1&view=map&sort=status_asc"))).toMatchObject({ intakeStage: "READY", matchOutcome: "NEW", heatZoneId: "HZ-1", viewMode: "map", sortBy: "stage", sortOrder: "asc" });
    for (const sort of ["due_at_asc", "submitted_at_desc"]) expect(parseInboxUrlState(new URLSearchParams({ sort }))).toMatchObject({ sortBy: "updatedAt", sortOrder: "desc" });
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
