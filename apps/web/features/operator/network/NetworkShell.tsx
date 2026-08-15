"use client";

import type { ReactNode } from "react";
import styles from "../networkFindAreas.module.css";
import { ExpansionStepper, type ExpansionStep } from "./ExpansionStepper";

export function NetworkShell({
  activeTab,
  children,
  onTabChange,
  steps,
  tabs,
}: {
  activeTab: number;
  children: ReactNode;
  onTabChange: (tabIndex: number) => void;
  steps: ExpansionStep[];
  tabs: readonly string[];
}) {
  return (
    <>
      <ExpansionStepper activeTab={activeTab} onStepSelect={onTabChange} steps={steps} />
      <nav className={styles.tabs} aria-label="Network tabs" role="tablist">
        {tabs.map((tab, index) => {
          const [label, englishLabel] = tab.split(" / ");
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
