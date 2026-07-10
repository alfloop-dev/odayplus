import type { TabSpec } from "./contracts.ts";

export type TabsProps = {
  tabs: readonly TabSpec[];
  active: string;
  onChange: (tabId: string) => void;
  className?: string;
};

export function Tabs({ tabs, active, onChange, className }: TabsProps) {
  const visibleTabs = tabs.filter((tab) => tab.permitted !== false);
  const activeTab = visibleTabs.find((tab) => tab.id === active) ?? visibleTabs[0];

  return (
    <div className={["odp-tabs", className].filter(Boolean).join(" ")}>
      <div className="odp-tabs__list" role="tablist" aria-label="Content tabs">
        {visibleTabs.map((tab) => (
          <button
            key={tab.id}
            id={`odp-tab-${tab.id}`}
            className="odp-tabs__tab"
            type="button"
            role="tab"
            aria-selected={activeTab?.id === tab.id}
            aria-controls={`odp-panel-${tab.id}`}
            disabled={Boolean(tab.disabledReason)}
            title={tab.disabledReason}
            onClick={() => onChange(tab.id)}
          >
            {tab.label}
            {tab.badge !== undefined ? <span className="odp-tabs__badge">{tab.badge}</span> : null}
          </button>
        ))}
      </div>
      {activeTab ? (
        <section
          id={`odp-panel-${activeTab.id}`}
          className="odp-tabs__panel"
          role="tabpanel"
          aria-labelledby={`odp-tab-${activeTab.id}`}
        >
          {activeTab.panel}
        </section>
      ) : null}
    </div>
  );
}
