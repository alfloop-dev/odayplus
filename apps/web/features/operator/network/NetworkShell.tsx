"use client";

import type { ReactNode } from "react";
import styles from "../networkFindAreas.module.css";
import { ExpansionStepper, type ExpansionStep } from "./ExpansionStepper";

export function NetworkShell({
  activeTab,
  children,
  onTabChange,
  steps,
  tabCounts,
  tabs,
}: {
  activeTab: number;
  children: ReactNode;
  onTabChange: (tabIndex: number) => void;
  steps: ExpansionStep[];
  /** Package 10 count badge per tab; zero or missing shows no badge. */
  tabCounts?: ReadonlyArray<number | null | undefined>;
  tabs: readonly string[];
}) {
  return (
    <>
      <ExpansionStepper activeTab={activeTab} onStepSelect={onTabChange} steps={steps} />
      <nav className={styles.tabs} aria-label="Network tabs" role="tablist">
        {tabs.map((tab, index) => {
          const [label, englishLabel] = tab.split(" / ");
          const count = tabCounts?.[index] ?? 0;
          return (
            <button
              aria-controls="network-active-panel"
              aria-current={index === activeTab ? "page" : undefined}
              aria-selected={index === activeTab}
              className={styles.tab}
              data-testid={`network-tab-${index}`}
              key={tab}
              onClick={() => onTabChange(index)}
              role="tab"
              type="button"
            >
              <span>{label}</span>
              {englishLabel ? <small>{englishLabel}</small> : null}
              {count > 0 ? (
                <span className={styles.tabCount} data-testid={`network-tab-count-${index}`}>
                  {count}
                </span>
              ) : null}
            </button>
          );
        })}
      </nav>
      <div className={styles.networkPanelHost} id="network-active-panel">
        {children}
      </div>
    </>
  );
}
