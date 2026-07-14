"use client";

import styles from "../networkFindAreas.module.css";

export type ExpansionStepState = "completed" | "current" | "next" | "blocked";

export type ExpansionStep = {
  id: string;
  label: string;
  state: ExpansionStepState;
  tabIndex: number;
  entityId?: string | null;
  summary: string;
};

export function ExpansionStepper({
  activeTab,
  onStepSelect,
  steps,
}: {
  activeTab: number;
  onStepSelect: (tabIndex: number) => void;
  steps: ExpansionStep[];
}) {
  if (!steps.length) {
    return null;
  }

  return (
    <section className={styles.expansionStepper} aria-label="Network Golden Flow" data-testid="network-expansion-stepper">
      {steps.map((step, index) => {
        const isBlocked = step.state === "blocked";
        return (
          <button
            aria-current={activeTab === step.tabIndex ? "step" : undefined}
            className={styles.expansionStep}
            data-state={step.state}
            data-testid={`network-step-${step.id}`}
            disabled={isBlocked}
            key={step.id}
            onClick={() => onStepSelect(step.tabIndex)}
            title={step.summary}
            type="button"
          >
            <span className={styles.expansionStepIndex}>{index + 1}</span>
            <span className={styles.expansionStepBody}>
              <strong>{step.label}</strong>
              <small>{step.entityId ?? step.summary}</small>
            </span>
            <span className={styles.expansionStepState}>{step.state}</span>
          </button>
        );
      })}
    </section>
  );
}
