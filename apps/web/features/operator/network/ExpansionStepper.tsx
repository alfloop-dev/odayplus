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

const stepLabels: Record<string, { zh: string; en: string }> = {
  candidate: { zh: "候選點", en: "Candidate" },
  compare: { zh: "比較", en: "Compare" },
  find: { zh: "找區域", en: "Find Areas" },
  radar: { zh: "物件雷達", en: "Listing Radar" },
  review: { zh: "審核", en: "Review" },
  sitescore: { zh: "SiteScore", en: "SiteScore" },
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
      <div className={styles.flowHeader}>
        <span>EXPANSION FLOW · 找點流程</span>
        <strong>{steps.find((step) => activeTab === step.tabIndex)?.summary ?? steps[0]?.summary}</strong>
        <em>{nextActionLabel(steps)}</em>
      </div>
      <div className={styles.expansionStepGrid}>
        {steps.map((step, index) => {
          const isBlocked = step.state === "blocked";
          const label = stepLabels[step.id] ?? { zh: step.label, en: step.label };
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
                <strong>{label.zh}</strong>
                <small>{label.en}</small>
              </span>
              <span className={styles.expansionStepEntity}>{step.entityId ?? "缺資料"}</span>
              <span className={styles.expansionStepState}>{step.state}</span>
            </button>
          );
        })}
      </div>
      <div className={styles.flowChain} aria-label="Current Network flow">
        <span>目前流程</span>
        {steps.map((step, index) => (
          <button
            aria-current={activeTab === step.tabIndex ? "step" : undefined}
            disabled={step.state === "blocked"}
            key={step.id}
            onClick={() => onStepSelect(step.tabIndex)}
            type="button"
          >
            {stepLabels[step.id]?.zh ?? step.label}
            {index < steps.length - 1 ? <i aria-hidden="true">→</i> : null}
          </button>
        ))}
      </div>
    </section>
  );
}

function nextActionLabel(steps: ExpansionStep[]) {
  const current = steps.find((step) => step.state === "current");
  if (current) {
    return `下一步：${current.summary}`;
  }
  const next = steps.find((step) => step.state === "next");
  return next ? `下一步：${next.summary}` : "流程資料同步中";
}
