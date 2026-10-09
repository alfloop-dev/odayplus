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

  const activeStep = steps.find((step) => activeTab === step.tabIndex);

  return (
    <section
      className={styles.expansionStepper}
      aria-label="Network Golden Flow"
      data-screen-label="Network Expansion Flow Stepper"
      data-testid="network-expansion-stepper"
    >
      <div className={styles.flowHeader}>
        <span>EXPANSION FLOW · 找點流程</span>
        <strong title={activeStep?.summary}>
          目前步驟：{activeStep ? stepLabels[activeStep.id]?.zh ?? activeStep.label : "店網管理"}
        </strong>
        <em role={activeStep?.state === "blocked" ? "status" : undefined}>
          {activeStep?.state === "blocked"
            ? `此流程受阻：${flowSummary(activeStep.summary)}`
            : nextActionLabel(steps, activeTab)}
        </em>
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
              title={flowSummary(step.summary)}
              type="button"
            >
              <span className={styles.expansionStepIndex} aria-hidden="true">
                {step.state === "completed" ? "✓" : step.state === "blocked" ? "!" : index + 1}
              </span>
              <span className={styles.expansionStepBody}>
                <strong>{label.zh}</strong>
                <small>{label.en}</small>
              </span>
              <span className={styles.expansionStepState}>
                {step.state === "blocked" ? "缺資料" : step.entityId ?? stateLabel(step.state)}
              </span>
              <span className={styles.srOnly}>{step.state}</span>
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

// The listings journey is scoped to its own entity, not the scoring tab's
// aggregate candidate count. Preserve its authoritative states/disabled steps.
function flowSummary(summary: string) {
  const labels: Record<string, string> = {
    "Blocked until candidate exists.": "須先建立此流程候選點",
    "Blocked by missing candidate.": "此流程尚缺候選點",
    "Requires a production model binding.": "須綁定正式模型",
    "Requires live scored candidates.": "須有正式候選點評分",
    "No live review packet is available.": "此流程尚無正式審核資料",
  };
  return labels[summary] ?? summary;
}

function nextActionLabel(steps: ExpansionStep[], activeTab: number) {
  const activeIndex = steps.findIndex((step) => step.tabIndex === activeTab);
  const currentIndex = activeIndex >= 0 ? activeIndex : steps.findIndex((step) => step.state === "current");
  const next = currentIndex >= 0 ? steps[currentIndex + 1] : steps.find((step) => step.state === "next");
  if (next && next.state !== "blocked") {
    return `下一步：${flowSummary(next.summary)}`;
  }
  if (next?.state === "blocked") {
    return `此流程下一步受阻：${flowSummary(next.summary)}`;
  }
  return "流程資料同步中";
}

function stateLabel(state: ExpansionStepState) {
  if (state === "completed") return "完成";
  if (state === "current") return "目前";
  if (state === "next") return "下一步";
  return "缺資料";
}
