import { Badge } from "@oday-plus/ui";
import type { StatusTone } from "@oday-plus/domain-types";
import type { ApiBinding, BindingState } from "../lib/api/binding.ts";

const STATE_TONE: Record<BindingState, StatusTone> = {
  ready: "green",
  empty: "blue",
  error: "red",
  unconfigured: "gray",
};

const STATE_LABEL: Record<BindingState, string> = {
  ready: "API live",
  empty: "API empty · fixture fallback",
  error: "API error · fixture fallback",
  unconfigured: "Fixture (no API base URL)",
};

/**
 * Visible, test-addressable indicator of where the surrounding region's data
 * came from. `data-source` is `api` only when the backend actually served the
 * rendered rows; `data-state` exposes the finer loading/empty/error/stale
 * vocabulary required by the page contract.
 */
export function DataSourceBadge({
  binding,
  testId,
}: {
  binding: ApiBinding<unknown>;
  testId: string;
}) {
  return (
    <span data-testid={testId} data-source={binding.source} data-state={binding.state}>
      <Badge
        label={STATE_LABEL[binding.state]}
        tone={STATE_TONE[binding.state]}
        marker={binding.source === "api" ? "◆" : "◫"}
      />
    </span>
  );
}
