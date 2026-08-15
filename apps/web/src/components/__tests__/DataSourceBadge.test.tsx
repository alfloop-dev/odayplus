import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DataSourceBadge } from "../DataSourceBadge";

describe("DataSourceBadge", () => {
  it.each([
    ["empty", "API empty · no production data"],
    ["error", "API unavailable · no production data"],
    ["unconfigured", "API unconfigured · no production data"],
  ] as const)("does not claim fixture fallback for %s bindings", (state, label) => {
    render(
      <DataSourceBadge
        binding={{
          state,
          items: [],
          source: "unavailable",
          fetchedAt: "2026-07-24T00:00:00Z",
        }}
        testId={`source-${state}`}
      />,
    );

    expect(screen.getByText(label)).toBeTruthy();
    expect(screen.queryByText(/fixture/i)).toBeNull();
  });
});
