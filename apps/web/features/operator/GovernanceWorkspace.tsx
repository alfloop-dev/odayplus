"use client";

import { useMemo, useState } from "react";
import styles from "./governance.module.css";
import type {
  GovernanceApproval,
  GovernanceAuditCategory,
  GovernanceAuditRow,
  GovernanceDecisionAction,
  GovernanceDecisionPayload,
  GovernanceDecisionRow,
  GovernanceRole,
  GovernanceWorkspaceCallbacks,
} from "./governanceTypes";

type GovernanceTab = "approvals" | "decisions" | "audit";

export type GovernanceWorkspaceProps = {
  approvals?: GovernanceApproval[];
  decisions?: GovernanceDecisionRow[];
  auditRows?: GovernanceAuditRow[];
  role?: GovernanceRole;
  canDecide?: boolean;
  callbacks?: GovernanceWorkspaceCallbacks;
};

const fallbackApprovals: GovernanceApproval[] = [
  {
    id: "ap-store-1042",
    module: "Store Ops",
    title: "Close escalated service issue",
    requestor: "Store Ops Lead",
    submittedAt: "2026-07-05 08:12",
    status: "pending",
    priority: "high",
    owner: "營運主管",
    sla: "42m",
    entityRef: "ISS-1042",
    summary: "Manager requests closure after staff resolution and customer callback.",
    systemRecommendation: "Approve with customer follow-up audit retained.",
    risk: "Customer-facing escalation",
    roleNote: "營運主管 can decide after reviewing evidence package.",
    evidence: [
      { id: "ev-issue", label: "Issue timeline", type: "issue", state: "ready" },
      { id: "ev-call", label: "Customer callback", type: "note", state: "ready" },
      { id: "ev-photo", label: "Counter photo", type: "camera", state: "ready" },
    ],
  },
  {
    id: "ap-growth-2207",
    module: "Growth",
    title: "Schedule promo campaign",
    requestor: "Growth Manager",
    submittedAt: "2026-07-05 07:48",
    status: "pending",
    priority: "medium",
    owner: "行銷經理",
    sla: "2h 10m",
    entityRef: "CMP-2207",
    summary: "Campaign needs final governance approval before audience export.",
    systemRecommendation: "Return unless audience mask proof is attached.",
    risk: "Export and consent policy",
    roleNote: "Return requires a reason for downstream Growth revision.",
    evidence: [
      { id: "ev-draft", label: "Campaign draft", type: "growth", state: "ready" },
      { id: "ev-mask", label: "Masking proof", type: "export", state: "missing" },
    ],
  },
  {
    id: "ap-network-3319",
    module: "Network",
    title: "Approve SiteScore override",
    requestor: "Expansion Manager",
    submittedAt: "2026-07-05 06:35",
    status: "pending",
    priority: "critical",
    owner: "展店經理",
    sla: "18m",
    entityRef: "SITE-3319",
    summary: "Team requests WAIT to GO override for a high-traffic corner candidate.",
    systemRecommendation: "Reject override due to competitor density and lease risk.",
    risk: "Model override",
    roleNote: "展店經理 decision must include model and dataset snapshot context.",
    evidence: [
      { id: "ev-score", label: "SiteScore v4.8", type: "model", state: "ready" },
      { id: "ev-snapshot", label: "Dataset 2026-W27", type: "dataset", state: "ready" },
      { id: "ev-comp", label: "Competitor scan", type: "network", state: "ready" },
    ],
  },
  {
    id: "ap-govern-0903",
    module: "Govern",
    title: "Evidence package export",
    requestor: "PM／稽核",
    submittedAt: "2026-07-05 05:22",
    status: "pending",
    priority: "high",
    owner: "PM／稽核",
    sla: "1h 05m",
    entityRef: "EXP-0903",
    summary: "Auditor requests signed export for an external review packet.",
    systemRecommendation: "Approve with seven-day retention and masked actor fields.",
    risk: "Retention and signed URL policy",
    roleNote: "PM／稽核 can approve export after retention policy review.",
    evidence: [
      { id: "ev-policy", label: "Retention policy", type: "system", state: "ready" },
      { id: "ev-mask-2", label: "Actor masking", type: "export", state: "ready" },
      { id: "ev-audit", label: "Audit bundle", type: "audit", state: "ready" },
    ],
  },
];

const fallbackDecisions: GovernanceDecisionRow[] = [
  {
    id: "dec-8841",
    module: "Store Ops",
    item: "ISS-0994 resolution close",
    systemRecommendation: "Approve",
    finalDecision: "Approved",
    reason: "Evidence package matched closure policy.",
    actor: "營運主管",
    decidedAt: "2026-07-05 04:51",
    model: "ops-risk-v2.2",
    datasetSnapshot: "ops-2026-W27",
    approvalId: "ap-store-0994",
  },
  {
    id: "dec-8840",
    module: "Growth",
    item: "CMP-2198 audience export",
    systemRecommendation: "Return",
    finalDecision: "Returned",
    reason: "Audience masking proof was incomplete.",
    actor: "PM／稽核",
    decidedAt: "2026-07-04 19:18",
    model: "campaign-guard-v1.9",
    datasetSnapshot: "growth-2026-W27",
    approvalId: "ap-growth-2198",
  },
  {
    id: "dec-8839",
    module: "Network",
    item: "SITE-3308 WAIT override",
    systemRecommendation: "Reject",
    finalDecision: "Rejected",
    reason: "Lease sensitivity exceeded override threshold.",
    actor: "展店經理",
    decidedAt: "2026-07-04 17:44",
    model: "sitescore-v4.8",
    datasetSnapshot: "network-2026-W27",
    approvalId: "ap-network-3308",
  },
];

const fallbackAuditRows: GovernanceAuditRow[] = [
  {
    id: "aud-7101",
    category: "approval",
    timestamp: "2026-07-05 08:12",
    actor: "Store Ops Lead",
    action: "Approval requested",
    module: "Store Ops",
    entityRef: "ISS-1042",
    summary: "Issue closure approval entered queue.",
    correlationId: "corr-iss-1042",
  },
  {
    id: "aud-7100",
    category: "camera",
    timestamp: "2026-07-05 08:08",
    actor: "Camera service",
    action: "Evidence attached",
    module: "Store Ops",
    entityRef: "ISS-1042",
    summary: "Counter photo linked to closure packet.",
    correlationId: "corr-iss-1042",
  },
  {
    id: "aud-7099",
    category: "growth",
    timestamp: "2026-07-05 07:48",
    actor: "Growth Manager",
    action: "Campaign submitted",
    module: "Growth",
    entityRef: "CMP-2207",
    summary: "Promo campaign submitted for governance review.",
    correlationId: "corr-cmp-2207",
  },
  {
    id: "aud-7098",
    category: "network",
    timestamp: "2026-07-05 06:35",
    actor: "Expansion Manager",
    action: "Override requested",
    module: "Network",
    entityRef: "SITE-3319",
    summary: "SiteScore WAIT to GO override requested.",
    correlationId: "corr-site-3319",
  },
  {
    id: "aud-7097",
    category: "export",
    timestamp: "2026-07-05 05:22",
    actor: "PM／稽核",
    action: "Export approval requested",
    module: "Govern",
    entityRef: "EXP-0903",
    summary: "Evidence Package export queued for approval.",
    correlationId: "corr-exp-0903",
  },
  {
    id: "aud-7096",
    category: "system",
    timestamp: "2026-07-05 05:10",
    actor: "Policy engine",
    action: "Retention rule evaluated",
    module: "Govern",
    entityRef: "EXP-0903",
    summary: "Seven-day signed URL retention selected.",
    correlationId: "corr-exp-0903",
  },
  {
    id: "aud-7095",
    category: "issue",
    timestamp: "2026-07-04 21:03",
    actor: "營運主管",
    action: "Escalation observed",
    module: "Store Ops",
    entityRef: "ISS-1011",
    summary: "Customer impact marked as contained.",
    correlationId: "corr-iss-1011",
  },
];

const tabs: Array<{ id: GovernanceTab; label: string }> = [
  { id: "approvals", label: "核准中心" },
  { id: "decisions", label: "Decision Log" },
  { id: "audit", label: "Audit Trail" },
];

const baseAuditCategories: GovernanceAuditCategory[] = [
  "issue",
  "camera",
  "approval",
  "growth",
  "network",
  "export",
  "system",
];

export function GovernanceWorkspace({
  approvals,
  decisions,
  auditRows,
  role = "營運主管",
  canDecide = true,
  callbacks,
}: GovernanceWorkspaceProps) {
  const effectiveApprovals = approvals ?? fallbackApprovals;
  const effectiveDecisions = decisions ?? fallbackDecisions;
  const effectiveAuditRows = auditRows ?? fallbackAuditRows;
  const [activeTab, setActiveTab] = useState<GovernanceTab>("approvals");
  const [selectedApprovalId, setSelectedApprovalId] = useState(effectiveApprovals[0]?.id ?? "");
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState("");
  const [auditCategory, setAuditCategory] = useState<GovernanceAuditCategory | "all">("all");
  const [lastAction, setLastAction] = useState("");

  const pendingCount = effectiveApprovals.filter((approval) => approval.status === "pending").length;
  const selectedApproval =
    effectiveApprovals.find((approval) => approval.id === selectedApprovalId) ?? effectiveApprovals[0];
  const auditCategories = useMemo(() => {
    const categorySet = new Set<GovernanceAuditCategory>(baseAuditCategories);
    effectiveAuditRows.forEach((row) => categorySet.add(row.category));
    return Array.from(categorySet);
  }, [effectiveAuditRows]);
  const filteredAuditRows =
    auditCategory === "all"
      ? effectiveAuditRows
      : effectiveAuditRows.filter((row) => row.category === auditCategory);

  function selectApproval(approval: GovernanceApproval) {
    setSelectedApprovalId(approval.id);
    setReason("");
    setReasonError("");
    setLastAction("");
    callbacks?.onSelectApproval?.(approval);
  }

  function submitDecision(action: GovernanceDecisionAction) {
    if (!selectedApproval) {
      return;
    }

    const trimmedReason = reason.trim();
    if ((action === "return" || action === "reject") && !trimmedReason) {
      setReasonError("Reason required");
      return;
    }

    const payload: GovernanceDecisionPayload = {
      approvalId: selectedApproval.id,
      action,
      reason: trimmedReason || undefined,
      role,
      approval: selectedApproval,
    };

    if (action === "approve") {
      callbacks?.onApprove?.(payload);
      setLastAction(`Approve submitted for ${selectedApproval.id}`);
    } else if (action === "return") {
      callbacks?.onReturn?.(payload);
      setLastAction(`Return submitted for ${selectedApproval.id}`);
    } else {
      callbacks?.onReject?.(payload);
      setLastAction(`Reject submitted for ${selectedApproval.id}`);
    }

    setReasonError("");
  }

  return (
    <section className={styles.workspace} data-testid="governance-workspace" data-screen-label="Govern 治理稽核">
      <header className={styles.header}>
        <div>
          <p className={styles.kicker}>Governance</p>
          <h2>Govern Approval Console</h2>
        </div>
        <div className={styles.headerStats} aria-label="Governance state">
          <span>{pendingCount} pending</span>
          <span>{role}</span>
          <span>{canDecide ? "Can decide" : "View only"}</span>
        </div>
      </header>

      <nav className={styles.tabs} aria-label="Governance tabs">
        {tabs.map((tab) => (
          <button
            aria-current={activeTab === tab.id ? "page" : undefined}
            className={styles.tab}
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {activeTab === "approvals" ? (
        <section className={styles.approvalGrid} aria-label="Approval center">
          <div className={styles.queuePanel}>
            <div className={styles.panelHeader}>
              <h3>Approval Queue</h3>
              <span>{effectiveApprovals.length} rows</span>
            </div>
            <div className={styles.queueList}>
              {effectiveApprovals.map((approval) => (
                <button
                  aria-current={selectedApproval?.id === approval.id ? "true" : undefined}
                  className={styles.queueItem}
                  key={approval.id}
                  onClick={() => selectApproval(approval)}
                  type="button"
                >
                  <span className={styles.queueTopline}>
                    <span className={styles.module}>{approval.module}</span>
                    <span className={styles.sla}>{approval.sla ?? "No SLA"}</span>
                  </span>
                  <strong>{approval.title}</strong>
                  <span className={styles.queueMeta}>
                    {approval.entityRef ?? approval.id} · {approval.requestor}
                  </span>
                  <span className={styles.queueFooter}>
                    <span className={statusClass(approval.priority ?? "medium")}>
                      {approval.priority ?? "medium"}
                    </span>
                    <span className={statusClass(approval.status)}>{approval.status}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>

          <article className={styles.detailPanel} aria-label="Approval detail">
            {selectedApproval ? (
              <>
                <div className={styles.detailHeader}>
                  <div>
                    <span className={styles.module}>{selectedApproval.module}</span>
                    <h3>{selectedApproval.title}</h3>
                  </div>
                  <span className={statusClass(selectedApproval.status)}>{selectedApproval.status}</span>
                </div>

                <dl className={styles.detailMeta}>
                  <div>
                    <dt>Approval</dt>
                    <dd>{selectedApproval.id}</dd>
                  </div>
                  <div>
                    <dt>Entity</dt>
                    <dd>{selectedApproval.entityRef ?? "None"}</dd>
                  </div>
                  <div>
                    <dt>Submitted</dt>
                    <dd>{selectedApproval.submittedAt}</dd>
                  </div>
                  <div>
                    <dt>Owner</dt>
                    <dd>{selectedApproval.owner ?? "Unassigned"}</dd>
                  </div>
                </dl>

                <div className={styles.summaryGrid}>
                  <section>
                    <h4>Request</h4>
                    <p>{selectedApproval.summary ?? "No request summary supplied."}</p>
                  </section>
                  <section>
                    <h4>System Recommendation</h4>
                    <p>{selectedApproval.systemRecommendation ?? "No recommendation supplied."}</p>
                  </section>
                  <section>
                    <h4>Risk</h4>
                    <p>{selectedApproval.risk ?? "No risk note supplied."}</p>
                  </section>
                  <section>
                    <h4>Role Note</h4>
                    <p>{selectedApproval.roleNote ?? `${role} decision context pending.`}</p>
                  </section>
                </div>

                <section className={styles.evidenceBlock} aria-label="Evidence">
                  <h4>Evidence</h4>
                  <div className={styles.evidenceChips}>
                    {(selectedApproval.evidence ?? []).map((evidence) =>
                      evidence.href ? (
                        <a className={styles.evidenceChip} href={evidence.href} key={evidence.id}>
                          <span>{evidence.label}</span>
                          <small>{evidence.state ?? evidence.type ?? "ready"}</small>
                        </a>
                      ) : (
                        <span className={styles.evidenceChip} key={evidence.id}>
                          <span>{evidence.label}</span>
                          <small>{evidence.state ?? evidence.type ?? "ready"}</small>
                        </span>
                      ),
                    )}
                    {selectedApproval.evidence?.length ? null : (
                      <span className={styles.emptyChip}>No evidence</span>
                    )}
                  </div>
                </section>

                <section className={styles.decisionBox} aria-label="Decision reason">
                  <label htmlFor="governance-reason">Reason</label>
                  <textarea
                    id="governance-reason"
                    onChange={(event) => {
                      setReason(event.target.value);
                      if (event.target.value.trim()) {
                        setReasonError("");
                      }
                    }}
                    placeholder="Optional for approve; required for return/reject"
                    rows={4}
                    value={reason}
                  />
                  <div className={styles.reasonRow}>
                    <span>Return/reject: reason required</span>
                    {reasonError ? <strong>{reasonError}</strong> : null}
                  </div>
                  <div className={styles.actions}>
                    <button disabled={!canDecide} onClick={() => submitDecision("approve")} type="button">
                      Approve
                    </button>
                    <button disabled={!canDecide} onClick={() => submitDecision("return")} type="button">
                      Return
                    </button>
                    <button disabled={!canDecide} onClick={() => submitDecision("reject")} type="button">
                      Reject
                    </button>
                  </div>
                  {lastAction ? <p className={styles.lastAction}>{lastAction}</p> : null}
                </section>
              </>
            ) : (
              <div className={styles.emptyState}>No approvals</div>
            )}
          </article>
        </section>
      ) : null}

      {activeTab === "decisions" ? (
        <section className={styles.tablePanel} aria-label="Decision Log">
          <div className={styles.panelHeader}>
            <h3>Decision Log</h3>
            <span>{effectiveDecisions.length} rows</span>
          </div>
          <div className={styles.tableWrap}>
            <table className={styles.dataTable}>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Module</th>
                  <th>Item</th>
                  <th>System Rec</th>
                  <th>Final</th>
                  <th>Reason</th>
                  <th>Actor</th>
                  <th>Model</th>
                  <th>Dataset</th>
                </tr>
              </thead>
              <tbody>
                {effectiveDecisions.map((decision) => (
                  <tr key={decision.id}>
                    <td>{decision.decidedAt}</td>
                    <td>{decision.module}</td>
                    <td>{decision.item}</td>
                    <td>{decision.systemRecommendation}</td>
                    <td>
                      <span className={statusClass(decision.finalDecision)}>{decision.finalDecision}</span>
                    </td>
                    <td>{decision.reason}</td>
                    <td>{decision.actor}</td>
                    <td>{decision.model ?? "n/a"}</td>
                    <td>{decision.datasetSnapshot ?? "n/a"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {activeTab === "audit" ? (
        <section className={styles.tablePanel} aria-label="Audit Trail">
          <div className={styles.panelHeader}>
            <h3>Audit Trail</h3>
            <span>{filteredAuditRows.length} rows</span>
          </div>
          <div className={styles.filters} aria-label="Audit category filters">
            <button
              aria-current={auditCategory === "all" ? "true" : undefined}
              onClick={() => setAuditCategory("all")}
              type="button"
            >
              all
            </button>
            {auditCategories.map((category) => (
              <button
                aria-current={auditCategory === category ? "true" : undefined}
                key={category}
                onClick={() => setAuditCategory(category)}
                type="button"
              >
                {category}
              </button>
            ))}
          </div>
          <div className={styles.tableWrap}>
            <table className={styles.dataTable}>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Category</th>
                  <th>Module</th>
                  <th>Action</th>
                  <th>Entity</th>
                  <th>Actor</th>
                  <th>Summary</th>
                  <th>Correlation</th>
                </tr>
              </thead>
              <tbody>
                {filteredAuditRows.map((row) => (
                  <tr key={row.id}>
                    <td>{row.timestamp}</td>
                    <td>
                      <span className={statusClass(row.category)}>{row.category}</span>
                    </td>
                    <td>{row.module ?? "n/a"}</td>
                    <td>{row.action}</td>
                    <td>{row.entityRef ?? "n/a"}</td>
                    <td>{row.actor}</td>
                    <td>{row.summary ?? row.reason ?? "n/a"}</td>
                    <td>{row.correlationId ?? "n/a"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </section>
  );
}

function statusClass(value: string) {
  const normalized = value.toLowerCase().replace(/[^a-z0-9]+/g, "");
  if (normalized.includes("reject") || normalized === "critical" || normalized === "missing") {
    return `${styles.badge} ${styles.badgeDanger}`;
  }
  if (normalized.includes("return") || normalized === "high" || normalized === "stale") {
    return `${styles.badge} ${styles.badgeWarn}`;
  }
  if (normalized.includes("approve") || normalized === "ready") {
    return `${styles.badge} ${styles.badgeGood}`;
  }
  return styles.badge;
}
