import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_INBOX_FILTERS, INBOX_FILTER_KEYS } from "../inboxUrlState";
import { useIntakeInboxQuery } from "../useIntakeInboxQuery";

afterEach(cleanup);
describe("intake URL navigation ownership", () => {
  it("keeps cleared and reset area filters explicit across history restoration", () => {
    window.history.replaceState(null, "", "/operator?ws=network&page=3&selected=IN-1&extra=a&extra=b");
    const { result } = renderHook(() => useIntakeInboxQuery());
    expect(result.current.filters.heatZoneId).toBeNull();
    act(() => result.current.updateFilters({ heatZoneId: "" }));
    const cleared = window.location.pathname + window.location.search;
    expect(new URLSearchParams(window.location.search).get("heatZoneId")).toBe("");
    expect(result.current.filters.page).toBe(1);
    act(() => {
      window.history.replaceState(null, "", "/operator?heatZoneId=HZ-02&page=4");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(result.current.filters.heatZoneId).toBe("HZ-02");
    act(() => {
      window.history.replaceState(null, "", cleared);
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(result.current.filters.heatZoneId).toBe("");
    act(() => result.current.updateFilters({ search: "台北", heatZoneId: "HZ-03", viewMode: "map" }));
    act(() => result.current.resetFilters());
    expect(result.current.filters).toMatchObject({ search: "", heatZoneId: "", viewMode: "map", selectedIntakeId: "IN-1" });
    const params = new URLSearchParams(window.location.search);
    expect(params.get("heatZoneId")).toBe("");
    expect(params.getAll("extra")).toEqual(["a", "b"]);
  });

  it("does not rewrite a direct link on mount and retains framework history state on updates", () => {
    window.history.replaceState({ __NA: true, tree: "framework-owned" }, "", "/operator?ws=network&extra=a&extra=b&page=3#other");
    const push = vi.spyOn(window.history, "pushState");
    const replace = vi.spyOn(window.history, "replaceState");
    const { result } = renderHook(() => useIntakeInboxQuery());
    expect(result.current.filters.page).toBe(3);
    expect(push).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
    act(() => { result.current.updateFilters({ search: "台北" }); result.current.updateFilters({ viewMode: "map" }); });
    expect(result.current.filters).toMatchObject({ search: "台北", viewMode: "map", page: 1 });
    expect(new URLSearchParams(window.location.search).getAll("extra")).toEqual(["a", "b"]);
    expect(window.location.hash).toBe("#other");
    expect(window.history.state).toEqual({ __NA: true, tree: "framework-owned" });
    push.mockRestore(); replace.mockRestore();
  });

  it.each(INBOX_FILTER_KEYS)("resets server pagination after changing %s while preserving the detail context", (key) => {
    const change = vi.fn();
    const { result } = renderHook(() => useIntakeInboxQuery({ filters: { ...DEFAULT_INBOX_FILTERS, page: 4,
      selectedIntakeId: "record-1" }, onChange: change }));
    act(() => result.current.updateFilters({ [key]: key === "savedView" ? "blocked" : "" }));
    expect(change).toHaveBeenCalledWith(expect.objectContaining({ page: 1, selectedIntakeId: "record-1" }));
  });

  it("restores popstate and hash navigation without creating another history entry", () => {
    window.history.replaceState(null, "", "/operator?search=first");
    const { result } = renderHook(() => useIntakeInboxQuery());
    const push = vi.spyOn(window.history, "pushState");
    act(() => { window.history.replaceState(null, "", "/operator?search=second&page=2#intake/IN-2"); window.dispatchEvent(new PopStateEvent("popstate")); });
    expect(result.current.filters).toMatchObject({ search: "second", page: 2, selectedIntakeId: "IN-2" });
    act(() => { window.location.hash = "#intake/IN-3"; window.dispatchEvent(new Event("hashchange")); });
    expect(result.current.filters.selectedIntakeId).toBe("IN-3");
    expect(push).not.toHaveBeenCalled(); push.mockRestore();
  });

  it("uses controlled Next navigation without a competing global-history writer", () => {
    window.history.replaceState(null, "", "/operator?search=unrelated");
    const change = vi.fn();
    const push = vi.spyOn(window.history, "pushState");
    const { result, rerender } = renderHook(({ search }) => useIntakeInboxQuery({ filters: { ...DEFAULT_INBOX_FILTERS, search }, onChange: change }), { initialProps: { search: "router" } });
    expect(result.current.filters.search).toBe("router");
    act(() => result.current.updateFilters({ search: "new" }));
    expect(change).toHaveBeenCalledWith(expect.objectContaining({ search: "new" }));
    expect(push).not.toHaveBeenCalled();
    rerender({ search: "back" });
    expect(result.current.filters.search).toBe("back"); push.mockRestore();
  });
});
