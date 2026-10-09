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

// Package 10 S05 flowVals: what the operator does at each step, shown as
// 「下一步：…」. The API step summaries stay available as button titles.
const stepActions: Record<string, string> = {
  find: "選擇目標區域",
  radar: "查看本區物件並轉為候選點",
  candidate: "補齊候選點資料",
  sitescore: "執行 SiteScore",
  compare: "加入比較",
  review: "送審並完成審核決策",
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
  const activeLabel = activeStep ? stepLabels[activeStep.id] ?? { zh: activeStep.label, en: activeStep.label } : null;
  const blockedNext = nextActionableStep(steps)?.state === "blocked" ? nextActionableStep(steps) : undefined;

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
          {activeLabel ? `目前步驟：${activeLabel.zh} ${activeLabel.en}` : "低效重配 — 不在找點流程內"}
        </strong>
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
      {/*
        Later steps are routinely "blocked" until a candidate exists; that is
        already shown by their 缺資料 tags. The notice is for a blocked step
        the operator has to act on now, as in Package 10 (flowBlockShow).
      */}
      {blockedNext ? (
        <div className={styles.flowBlockNotice} role="status">
          <span aria-hidden="true">!</span>
          {blockedNext.summary}
        </div>
      ) : null}
    </section>
  );
}

/** The step after the current one (or the first "next" step). */
function nextActionableStep(steps: ExpansionStep[]) {
  const currentIndex = steps.findIndex((step) => step.state === "current");
  return currentIndex >= 0 ? steps[currentIndex + 1] : steps.find((step) => step.state === "next");
}

function nextActionLabel(steps: ExpansionStep[]) {
  // Package 10 names the first unfinished step: while the radar is current the
  // operator's job is still 「查看本區物件並轉為候選點」.
  const todo = steps.find((step) => step.state === "current") ?? nextActionableStep(steps);
  if (!todo) {
    return steps.length && steps.every((step) => step.state === "completed") ? "流程完成" : "流程資料同步中";
  }
  const action = stepActions[todo.id] ?? todo.summary;
  return todo.state === "blocked" ? `下一步受阻：${action}` : `下一步：${action}`;
}

function stateLabel(state: ExpansionStepState) {
  if (state === "completed") return "完成";
  if (state === "current") return "目前";
  if (state === "next") return "下一步";
  return "缺資料";
}
