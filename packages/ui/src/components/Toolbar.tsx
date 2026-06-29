import type { ReactNode } from "react";
import { Badge } from "./Badge.tsx";
import { Button } from "./Button.tsx";
import type { ActionSpec, Density, FilterSpec, SavedViewSpec } from "./contracts.ts";

export type ToolbarProps = {
  filters?: FilterSpec[];
  savedViews?: SavedViewSpec[];
  actions?: ActionSpec[];
  selectedCount?: number;
  onExport?: () => void;
  exportInProgress?: boolean;
  density?: Density;
  onDensityChange?: (density: Density) => void;
  children?: ReactNode;
  className?: string;
};

function renderFilter(filter: FilterSpec) {
  return (
    <label className="odp-filter" key={filter.id}>
      <span>{filter.label}</span>
      {filter.options ? (
        <select
          className="odp-select"
          value={filter.value ?? ""}
          disabled={filter.disabled}
          onChange={(event) => filter.onChange?.(event.currentTarget.value)}
        >
          <option value="">{filter.placeholder ?? "全部"}</option>
          {filter.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      ) : (
        <input
          className="odp-input"
          value={filter.value ?? ""}
          placeholder={filter.placeholder}
          disabled={filter.disabled}
          onChange={(event) => filter.onChange?.(event.currentTarget.value)}
        />
      )}
    </label>
  );
}

function renderAction(action: ActionSpec) {
  return (
    <Button
      key={action.id}
      variant={action.tone === "danger" ? "danger" : action.tone === "warning" ? "warning" : "secondary"}
      icon={action.icon}
      loading={action.loading}
      disabled={action.permitted === false}
      disabledReason={action.disabledReason}
      onClick={action.onSelect}
    >
      {action.label}
    </Button>
  );
}

export function Toolbar({
  filters = [],
  savedViews = [],
  actions = [],
  selectedCount = 0,
  onExport,
  exportInProgress = false,
  density = "comfortable",
  onDensityChange,
  children,
  className,
}: ToolbarProps) {
  const activeFilterCount = filters.filter((filter) => filter.active || Boolean(filter.value)).length;
  return (
    <section className={["odp-toolbar", className].filter(Boolean).join(" ")} aria-label="Filter and action toolbar">
      <div className="odp-toolbar__group">
        {savedViews.length > 0 ? (
          <label className="odp-filter">
            <span>Saved view</span>
            <select
              className="odp-select"
              value={savedViews.find((view) => view.active)?.id ?? ""}
              onChange={(event) => savedViews.find((view) => view.id === event.currentTarget.value)?.onSelect?.()}
            >
              <option value="">預設檢視</option>
              {savedViews.map((view) => (
                <option key={view.id} value={view.id}>
                  {view.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {filters.map(renderFilter)}
        {activeFilterCount > 0 ? <Badge label={`${activeFilterCount} filters`} tone="blue" /> : null}
      </div>

      {selectedCount > 0 ? (
        <div className="odp-toolbar__selection" role="region" aria-live="polite">
          已選取 {selectedCount} 筆
        </div>
      ) : null}

      <div className="odp-toolbar__spacer" />
      {children}
      {onDensityChange ? (
        <label className="odp-filter">
          <span>Density</span>
          <select
            className="odp-select"
            value={density}
            onChange={(event) => onDensityChange(event.currentTarget.value as Density)}
          >
            <option value="comfortable">comfortable</option>
            <option value="compact">compact</option>
            <option value="presentation">presentation</option>
          </select>
        </label>
      ) : null}
      {onExport ? (
        <Button variant="secondary" loading={exportInProgress} onClick={onExport}>
          匯出
        </Button>
      ) : null}
      {actions.map(renderAction)}
    </section>
  );
}

export const FilterBar = Toolbar;
