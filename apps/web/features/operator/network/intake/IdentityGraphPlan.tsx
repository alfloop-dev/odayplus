"use client";

import styles from "./identity.module.css";
import type { IdentityGraphEdge, IdentityGraphNode, IdentityGraphPlan } from "./identityTypes";

function GraphNodeList({ nodes, testId }: { nodes: IdentityGraphNode[]; testId: string }) {
  return (
    <ul className={styles.nodeList} data-testid={testId}>
      {nodes.map((node) => (
        <li key={node.nodeId}>
          <code>{node.nodeType}</code> <strong>{node.label}</strong>
          {" · "}
          <code>{node.nodeId}</code>
          {" · "}
          {node.effective ? "effective" : "historical"}
          {node.version === null ? "" : ` · v${node.version}`}
        </li>
      ))}
    </ul>
  );
}

function GraphEdgeList({ edges, testId }: { edges: IdentityGraphEdge[]; testId: string }) {
  if (edges.length === 0) return <p className={styles.hint}>沒有 graph edge。</p>;
  return (
    <ul className={styles.nodeList} data-testid={testId}>
      {edges.map((edge) => (
        <li key={edge.edgeId}>
          <code>{edge.edgeId}</code>：{edge.fromNodeId} → {edge.toNodeId} ({edge.relation})
          {edge.supersedesEdgeId ? `，supersedes ${edge.supersedesEdgeId}` : ""}
        </li>
      ))}
    </ul>
  );
}

export function IdentityGraphPlanView({
  plan,
  className,
}: {
  plan: IdentityGraphPlan;
  className?: string;
}) {
  return (
    <section
      aria-labelledby="identity-graph-plan-title"
      className={`${styles.section} ${className ?? ""}`}
      data-testid="identity-graph-plan"
      data-operation={plan.operation}
    >
      <div className={styles.headingRow}>
        <h3 className={styles.title} id="identity-graph-plan-title">
          {plan.operation} 圖譜計畫
        </h3>
        <span className={styles.badge}>{plan.state}</span>
      </div>
      <p className={styles.subtitle}>
        Plan <code>{plan.planId}</code> · expected graph version {plan.expectedGraphVersion}
        {plan.originalDecisionId ? ` · original decision ${plan.originalDecisionId}` : ""}
      </p>

      <div className={styles.metaRow}>
        <span className={styles.meta}>
          提案者：{plan.proposer.displayName} ({plan.proposer.subjectId})
        </span>
        <span className={styles.meta}>
          指定審查者：
          {plan.requestedReviewer
            ? `${plan.requestedReviewer.displayName} (${plan.requestedReviewer.subjectId})`
            : "尚未指定"}
        </span>
      </div>

      <div className={styles.graphColumns}>
        <div data-testid="graph-before">
          <h4>變更前</h4>
          <GraphNodeList nodes={plan.before.nodes} testId="graph-before-nodes" />
          <GraphEdgeList edges={plan.before.edges} testId="graph-before-edges" />
        </div>
        <div data-testid="graph-after">
          <h4>變更後</h4>
          <GraphNodeList nodes={plan.after.nodes} testId="graph-after-nodes" />
          <GraphEdgeList edges={plan.after.edges} testId="graph-after-edges" />
        </div>
      </div>

      <div className={styles.graphColumns}>
        <div>
          <h4>Redirect 影響</h4>
          {plan.redirects.length > 0 ? (
            <ul className={styles.lineageList} data-testid="graph-redirects">
              {plan.redirects.map((redirect) => (
                <li key={`${redirect.disposition}-${redirect.fromPropertyId}-${redirect.toPropertyId}`}>
                  {redirect.disposition}: {redirect.fromPropertyId} → {redirect.toPropertyId}
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.hint}>不變更 Property redirect。</p>
          )}
        </div>
        <div>
          <h4>Candidate 影響</h4>
          {plan.candidateImpacts.length > 0 ? (
            <ul className={styles.lineageList} data-testid="graph-candidate-impacts">
              {plan.candidateImpacts.map((impact) => (
                <li key={impact.candidateSiteId}>
                  {impact.candidateSiteId}: {impact.disposition}
                  {impact.targetPropertyId ? ` → ${impact.targetPropertyId}` : ""}
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.hint}>沒有 Candidate reassignment。</p>
          )}
        </div>
      </div>

      <div>
        <h4>Lineage impact</h4>
        <ul className={styles.lineageList} data-testid="graph-lineage-impact">
          {plan.lineageImpact.map((impact) => (
            <li key={impact}>{impact}</li>
          ))}
        </ul>
      </div>

      <p className={styles.notice} data-testid="identity-risk-summary">
        {plan.riskSummary}
      </p>
    </section>
  );
}
