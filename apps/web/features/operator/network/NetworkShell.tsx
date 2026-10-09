"use client";

import type { ReactNode } from "react";
import styles from "../networkFindAreas.module.css";
import { ExpansionStepper, type ExpansionStep } from "./ExpansionStepper";

export type NetworkTabDef =
  | string
  | {
      label: string;
      englishLabel?: string;
      badgeCount?: string | number;
    };

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
  tabs: readonly (string | NetworkTabDef)[];
}) {
  return (
    <>
      <ExpansionStepper activeTab={activeTab} onStepSelect={onTabChange} steps={steps} />
      <nav className={styles.tabs} aria-label="Network tabs" role="tablist">
        {tabs.map((tab, index) => {
          const label = typeof tab === "string" ? tab.split(" / ")[0] : tab.label;
          const englishLabel =
            typeof tab === "string" ? tab.split(" / ")[1] : tab.englishLabel;
          const badgeCount = typeof tab === "string" ? undefined : tab.badgeCount;
          return (
            <button
              aria-controls="network-active-panel"
              aria-current={index === activeTab ? "page" : undefined}
              aria-selected={index === activeTab}
              className={styles.tab}
              data-testid={`network-tab-${index}`}
              key={label}
              onClick={() => onTabChange(index)}
              role="tab"
              type="button"
            >
              <span>{label}</span>
              {englishLabel ? <small>{englishLabel}</small> : null}
              {badgeCount ? <span className={styles.tabBadge}>{badgeCount}</span> : null}
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
