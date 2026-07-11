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

type GovernanceTab = "approvals" | "decisions" | "audit" | "evidencePackage" | "statusBoard";

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
    actor: "營營主管",
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
  { id: "evidencePackage", label: "Evidence Package 匯出" },
  { id: "statusBoard", label: "系統狀態盤" },
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
  const [localApprovals, setLocalApprovals] = useState<GovernanceApproval[]>(approvals ?? fallbackApprovals);
  const [localDecisions, setLocalDecisions] = useState<GovernanceDecisionRow[]>(decisions ?? fallbackDecisions);
  const [localAuditRows, setLocalAuditRows] = useState<GovernanceAuditRow[]>(auditRows ?? fallbackAuditRows);

  const [activeTab, setActiveTab] = useState<GovernanceTab>("approvals");
  const [selectedApprovalId, setSelectedApprovalId] = useState(localApprovals[0]?.id ?? "");
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState("");
  const [auditCategory, setAuditCategory] = useState<GovernanceAuditCategory | "all">("all");
  const [lastAction, setLastAction] = useState("");
  const [localToast, setLocalToast] = useState<string | null>(null);

  // Evidence Package Form States
  const [evFrom, setEvFrom] = useState("2026-06-01");
  const [evTo, setEvTo] = useState("2026-07-03");
  const [evModS, setEvModS] = useState(true);
  const [evModG, setEvModG] = useState(true);
  const [evModN, setEvModN] = useState(true);
  const [evModV, setEvModV] = useState(true);
  const [incAudit, setIncAudit] = useState(true);
  const [incDec, setIncDec] = useState(true);
  const [incOut, setIncOut] = useState(true);
  const [incSla, setIncSla] = useState(true);
  const [fmt, setFmt] = useState("PDF");

  const [evdRunning, setEvdRunning] = useState(false);
  const [evdResult, setEvdResult] = useState<{ file: string; size: string; t: string; range: string } | null>(null);
  const [evdHist, setEvdHist] = useState<Array<{ id: string; range: string; mod: string; fmt: string; t: string; by: string }>>([
    { id: "EVD-2026-0701-01", range: "2026-06-01 – 2026-06-30", mod: "Store Ops＋Growth＋Network", fmt: "PDF", t: "2026-07-01 10:15", by: "周明德" },
    { id: "EVD-2026-0615-02", range: "2026-05-01 – 2026-05-31", mod: "Store Ops＋Network", fmt: "CSV", t: "2026-06-15 14:22", by: "周明德" }
  ]);

  const pendingCount = localApprovals.filter((approval) => approval.status === "pending").length;
  const selectedApproval =
    localApprovals.find((approval) => approval.id === selectedApprovalId) ?? localApprovals[0];

  const auditCategories = useMemo(() => {
    const categorySet = new Set<GovernanceAuditCategory>(baseAuditCategories);
    localAuditRows.forEach((row) => categorySet.add(row.category));
    return Array.from(categorySet);
  }, [localAuditRows]);

  const filteredAuditRows =
    auditCategory === "all"
      ? localAuditRows
      : localAuditRows.filter((row) => row.category === auditCategory);

  const triggerToast = (msg: string) => {
    setLocalToast(msg);
    setTimeout(() => setLocalToast(null), 3200);
  };

  const handleExport = () => {
    if (evdRunning) return;
    setEvdRunning(true);
    triggerToast("Evidence Package 產生中 (mock)...");
    setTimeout(() => {
      const selectedMods = [
        evModS ? "Store Ops" : null,
        evModG ? "Growth" : null,
        evModN ? "Network" : null,
        evModV ? "Govern" : null,
      ].filter(Boolean).join("＋") || "全模組";

      const fileExtension = fmt === "CSV" ? "zip" : "pdf";
      const randomSuffix = Math.floor(10 + Math.random() * 90);
      const file = `EVD-2026-0705-${randomSuffix}.${fileExtension}`;
      const nowStr = new Date().toISOString().replace("T", " ").substring(0, 16);
      const result = {
        file,
        size: "4.2 MB",
        t: nowStr.split(" ")[1],
        range: `${evFrom} – ${evTo}`,
      };

      setEvdResult(result);
      setEvdRunning(false);

      const histItem = {
        id: file.replace(/\.(pdf|zip)$/, ""),
        range: result.range,
        mod: selectedMods,
        fmt: fmt,
        t: `今日 ${result.t}`,
        by: role,
      };
      setEvdHist((prev) => [histItem, ...prev]);

      // Write to audit trail
      const newAuditId = `aud-${Math.floor(7000 + Math.random() * 999)}`;
      const newAuditRow: GovernanceAuditRow = {
        id: newAuditId,
        category: "export",
        timestamp: nowStr,
        actor: `${role} (Antigravity6)`,
        action: "Export Evidence Package",
        module: "Govern",
        entityRef: file.replace(/\.(pdf|zip)$/, ""),
        summary: `匯出 Evidence Package: 範圍 ${result.range} · 模組 ${selectedMods} · 格式 ${fmt} · 含 Audit／Decision／Outcome`,
        correlationId: `corr-exp-${randomSuffix}`,
      };
      setLocalAuditRows((prev) => [newAuditRow, ...prev]);

      triggerToast("Evidence Package 已產生 (mock) — 可於下方下載");
    }, 1200);
  };

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
    if ((action === "return" || action === "reject") && trimmedReason.length < 10) {
      setReasonError("退回或駁回理由需至少 10 個字");
      return;
    }

    const finalDecisionLabel = action === "approve" ? "Approved" : action === "return" ? "Returned" : "Rejected";
    const decisionReason = trimmedReason || "符合風險與預算規範";

    // Update approvals state
    setLocalApprovals((prev) =>
      prev.map((app) =>
        app.id === selectedApproval.id
          ? {
              ...app,
              status: action === "approve" ? "approved" : action === "return" ? "returned" : "rejected",
              reason: decisionReason,
            }
          : app
      )
    );

    // Add to Decision Log
    const newDecisionId = `dec-${Math.floor(1000 + Math.random() * 9000)}`;
    const newDecisionRow: GovernanceDecisionRow = {
      id: newDecisionId,
      module: selectedApproval.module,
      item: `${selectedApproval.entityRef || selectedApproval.id} ${selectedApproval.title}`,
      systemRecommendation: selectedApproval.systemRecommendation || "—",
      finalDecision: finalDecisionLabel,
      reason: decisionReason,
      actor: role,
      decidedAt: new Date().toISOString().replace("T", " ").substring(0, 16),
      model: selectedApproval.module === "Network" ? "sitescore-v4.8" : selectedApproval.module === "Growth" ? "PriceOps-v0.9" : "—",
      datasetSnapshot: selectedApproval.module === "Network" ? "network-2026-W27" : selectedApproval.module === "Growth" ? "growth-2026-W27" : "—",
      approvalId: selectedApproval.id,
    };
    setLocalDecisions((prev) => [newDecisionRow, ...prev]);

    // Add to Audit Trail
    const newAuditId = `aud-${Math.floor(7000 + Math.random() * 999)}`;
    const newAuditRow: GovernanceAuditRow = {
      id: newAuditId,
      category: "approval",
      timestamp: new Date().toISOString().replace("T", " ").substring(0, 16),
      actor: `${role} (Antigravity6)`,
      action: action === "approve" ? "決策核准" : action === "return" ? "決策退回" : "決策駁回",
      module: selectedApproval.module,
      entityRef: selectedApproval.entityRef,
      summary: `核准中心審查決策：${selectedApproval.title}，狀態變更為 ${finalDecisionLabel}。`,
      reason: decisionReason,
      correlationId: `corr-${selectedApproval.id}`,
    };
    setLocalAuditRows((prev) => [newAuditRow, ...prev]);

    const payload: GovernanceDecisionPayload = {
      approvalId: selectedApproval.id,
      action,
      reason: decisionReason,
      role,
      approval: {
        ...selectedApproval,
        status: action === "approve" ? "approved" : action === "return" ? "returned" : "rejected",
      },
    };

    if (action === "approve") {
      callbacks?.onApprove?.(payload);
      setLastAction(`Approve submitted for ${selectedApproval.id}`);
      triggerToast(`已核准決策 ${selectedApproval.id} — 已寫入 Decision Log 與 Audit`);
    } else if (action === "return") {
      callbacks?.onReturn?.(payload);
      setLastAction(`Return submitted for ${selectedApproval.id}`);
      triggerToast(`已退回決策 ${selectedApproval.id} — 理由已記錄`);
    } else {
      callbacks?.onReject?.(payload);
      setLastAction(`Reject submitted for ${selectedApproval.id}`);
      triggerToast(`已駁回決策 ${selectedApproval.id} — 理由已記錄`);
    }

    setReason("");
    setReasonError("");
  }

  // System Status Board Fixtures
  const dqRows = [
    { src: "Google Reviews Connector", st: "正常", isGood: true, note: "15 分鐘前同步 · 覆蓋 12/12 門市" },
    { src: "Camera Events", st: "延遲", isGood: false, note: "事件延遲 12 分鐘 · 影響即時性" },
    { src: "POS／支付交易", st: "正常", isGood: true, note: "即時串流 · 缺漏 0.2%" },
    { src: "591 物件源", st: "正常", isGood: true, note: "每日 06:00 匯入 · 昨日新增 14 筆" },
    { src: "IoT 心跳", st: "注意", isGood: false, note: "1 台設備 >3h 未回報（ISS-1021）" }
  ];

  const modelRows = [
    { name: "SiteScore", ver: "v2.3", st: "上線", isGood: true, note: "選址評分 · 每週再訓練 · 用於 Network" },
    { name: "CS Intent", ver: "v1.8", st: "上線", isGood: true, note: "客服意圖分類 · 準確率 91%" },
    { name: "PriceOps", ver: "v0.9", st: "Shadow", isGood: false, note: "動態定價 · 影子模式驗證中" },
    { name: "Camera Event", ver: "v1.2", st: "上線", isGood: true, note: "場域事件偵測 · 不含人臉" }
  ];

  const connRows = [
    { name: "Google Business Profile", st: "已連接", isGood: true, note: "評價／回覆 API" },
    { name: "LINE 官方帳號", st: "已連接", isGood: true, note: "客服＋推播" },
    { name: "591 租屋網", st: "已連接", isGood: true, note: "每日物件匯入" },
    { name: "TapPay 金流閘道", st: "已連接", isGood: true, note: "交易／退款 webhook" }
  ];

  const runbookRows = [
    { name: "災備演練 (Disaster Recovery)", st: "正常", isGood: true, note: "Completed 2026-07-01 · 復原時間 18m" },
    { name: "資料備份與還原 (Backup & Restore)", st: "正常", isGood: true, note: "每日 03:00 自動備份 · 驗證成功" },
    { name: "事件管理與升級 (Incident Management)", st: "運作中", isGood: true, note: "SLA 升級規則已啟用 · 監控端點正常" },
    { name: "系統觀測性 (Observability)", st: "正常", isGood: true, note: "Prometheus/Grafana 指標正常 · 心跳正常" }
  ];

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
              <span>{localApprovals.length} rows</span>
            </div>
            <div className={styles.queueList}>
              {localApprovals.map((approval) => (
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
                  {selectedApproval.status === "pending" ? (
                    <>
                      <label htmlFor="governance-reason">Reason</label>
                      <textarea
                        id="governance-reason"
                        onChange={(event) => {
                          setReason(event.target.value);
                          if (event.target.value.trim().length >= 10) {
                            setReasonError("");
                          }
                        }}
                        placeholder="Optional for approve; required for return/reject (min 10 chars)"
                        rows={4}
                        value={reason}
                      />
                      <div className={styles.reasonRow}>
                        <span>Return/reject: reason required (at least 10 chars)</span>
                        {reasonError ? <strong className={styles.errorText}>{reasonError}</strong> : null}
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
                    </>
                  ) : (
                    <div className={styles.decidedNotice}>
                      <strong>已完成決策 ({selectedApproval.status})</strong>
                      <p>決策理由：{selectedApproval.reason || "符合風險與預算規範"}</p>
                    </div>
                  )}
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
            <span>{localDecisions.length} rows</span>
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
                {localDecisions.map((decision) => (
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

      {activeTab === "evidencePackage" ? (
        <section className={styles.evidencePackageGrid} aria-label="Evidence Package export">
          <div className={styles.exportPanel}>
            <div className={styles.exportPanelTitle}>產生 Evidence Package</div>
            <div className={styles.exportPanelSubtitle}>
              補助查核與內部稽核用 — 匯出範圍內的決策、稽核與成效紀錄。
            </div>

            <div className={styles.formGrid2Col}>
              <div>
                <label className={styles.formLabel}>起始日</label>
                <input
                  className={styles.formInputDate}
                  onChange={(e) => setEvFrom(e.target.value)}
                  type="date"
                  value={evFrom}
                />
              </div>
              <div>
                <label className={styles.formLabel}>結束日</label>
                <input
                  className={styles.formInputDate}
                  onChange={(e) => setEvTo(e.target.value)}
                  type="date"
                  value={evTo}
                />
              </div>
            </div>

            <div>
              <label className={styles.formLabel}>模組範圍</label>
              <div className={styles.checkboxGroup}>
                <label className={styles.checkboxLabel}>
                  <input
                    checked={evModS}
                    className={styles.checkboxInput}
                    onChange={(e) => setEvModS(e.target.checked)}
                    type="checkbox"
                  />
                  Store Ops
                </label>
                <label className={styles.checkboxLabel}>
                  <input
                    checked={evModG}
                    className={styles.checkboxInput}
                    onChange={(e) => setEvModG(e.target.checked)}
                    type="checkbox"
                  />
                  Growth
                </label>
                <label className={styles.checkboxLabel}>
                  <input
                    checked={evModN}
                    className={styles.checkboxInput}
                    onChange={(e) => setEvModN(e.target.checked)}
                    type="checkbox"
                  />
                  Network
                </label>
                <label className={styles.checkboxLabel}>
                  <input
                    checked={evModV}
                    className={styles.checkboxInput}
                    onChange={(e) => setEvModV(e.target.checked)}
                    type="checkbox"
                  />
                  Govern
                </label>
              </div>
            </div>

            <div>
              <label className={styles.formLabel}>內容</label>
              <div className={styles.checkboxGroup}>
                <label className={styles.checkboxLabel}>
                  <input
                    checked={incAudit}
                    className={styles.checkboxInput}
                    onChange={(e) => setIncAudit(e.target.checked)}
                    type="checkbox"
                  />
                  Audit Trail
                </label>
                <label className={styles.checkboxLabel}>
                  <input
                    checked={incDec}
                    className={styles.checkboxInput}
                    onChange={(e) => setIncDec(e.target.checked)}
                    type="checkbox"
                  />
                  Decision Log
                </label>
                <label className={styles.checkboxLabel}>
                  <input
                    checked={incOut}
                    className={styles.checkboxInput}
                    onChange={(e) => setIncOut(e.target.checked)}
                    type="checkbox"
                  />
                  Outcome 對比
                </label>
                <label className={styles.checkboxLabel}>
                  <input
                    checked={incSla}
                    className={styles.checkboxInput}
                    onChange={(e) => setIncSla(e.target.checked)}
                    type="checkbox"
                  />
                  SLA 報表
                </label>
              </div>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <label className={styles.formLabel}>格式</label>
              <select
                className={styles.formSelect}
                onChange={(e) => setFmt(e.target.value)}
                style={{ width: "130px" }}
                value={fmt}
              >
                <option value="PDF">PDF</option>
                <option value="CSV">CSV (zip)</option>
              </select>
            </div>

            <button
              className={styles.exportButton}
              disabled={evdRunning}
              onClick={handleExport}
              type="button"
            >
              {evdRunning ? "產生中… (mock)" : "產生 Evidence Package"}
            </button>

            {evdResult ? (
              <div className={styles.exportResult} data-testid="evidence-package-result">
                <div className={styles.exportResultHeader}>
                  <span className={styles.exportFileName}>{evdResult.file}</span>
                  <span className={styles.exportFileSize}>{evdResult.size}</span>
                  <span className={styles.exportFileTime}>{evdResult.t}</span>
                </div>
                <div className={styles.exportResultMeta}>
                  範圍 {evdResult.range} · 匯出行為已寫入 Audit Trail
                </div>
                <button
                  className={styles.downloadButton}
                  onClick={() => triggerToast(`已下載證據包：${evdResult.file}`)}
                  type="button"
                >
                  下載 (mock)
                </button>
              </div>
            ) : null}
          </div>

          <div className={styles.exportPanel} style={{ minHeight: "360px" }}>
            <div className={styles.exportPanelTitle}>匯出紀錄</div>
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              {evdHist.map((eh) => (
                <div className={styles.historyItem} key={eh.id}>
                  <span className={styles.historyFileId}>{eh.id}</span>
                  <span className={styles.historyMeta}>{eh.range}</span>
                  <span className={styles.historyMeta}>{eh.mod}</span>
                  <span className={styles.historyBadge}>{eh.fmt}</span>
                  <span className={styles.historyTimeBy}>
                    {eh.t} · {eh.by}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </section>
      ) : null}

      {activeTab === "statusBoard" ? (
        <section className={styles.statusBoardGrid} aria-label="System status board">
          <div className={styles.statusCard}>
            <div className={styles.statusCardTitle}>Data Quality 監控</div>
            <div style={{ display: "flex", flexDirection: "column" }}>
              {dqRows.map((dq) => (
                <div className={styles.statusRow} key={dq.src}>
                  <span className={styles.statusNameWide}>{dq.src}</span>
                  <span
                    className={`${styles.statusBadge} ${
                      dq.isGood ? styles.badgeGood : styles.badgeWarn
                    }`}
                  >
                    {dq.st}
                  </span>
                  <span className={styles.statusNote}>{dq.note}</span>
                </div>
              ))}
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <div className={styles.statusCard}>
              <div className={styles.statusCardTitle}>Model Registry & Connector</div>
              <div style={{ display: "flex", flexDirection: "column" }}>
                <div style={{ fontWeight: 700, fontSize: "12px", color: "#475569", marginBottom: "4px" }}>Model Registry</div>
                {modelRows.map((mr) => (
                  <div className={styles.statusRow} key={mr.name}>
                    <span className={styles.statusName}>{mr.name}</span>
                    <span className={styles.statusVersion}>{mr.ver}</span>
                    <span
                      className={`${styles.statusBadge} ${
                        mr.isGood ? styles.badgeGood : styles.badgeWarn
                      }`}
                    >
                      {mr.st}
                    </span>
                    <span className={styles.statusNote}>{mr.note}</span>
                  </div>
                ))}

                <div style={{ fontWeight: 700, fontSize: "12px", color: "#475569", marginTop: "14px", marginBottom: "4px" }}>Connector／API</div>
                {connRows.map((cr) => (
                  <div className={styles.statusRow} key={cr.name}>
                    <span className={styles.statusNameWide}>{cr.name}</span>
                    <span
                      className={`${styles.statusBadge} ${
                        cr.isGood ? styles.badgeGood : styles.badgeWarn
                      }`}
                    >
                      {cr.st}
                    </span>
                    <span className={styles.statusNote}>{cr.note}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className={styles.statusCard}>
              <div className={styles.statusCardTitle}>Runbook 狀態</div>
              <div style={{ display: "flex", flexDirection: "column" }}>
                {runbookRows.map((rb) => (
                  <div className={styles.statusRow} key={rb.name}>
                    <span className={styles.statusNameWide}>{rb.name}</span>
                    <span
                      className={`${styles.statusBadge} ${
                        rb.isGood ? styles.badgeGood : styles.badgeWarn
                      }`}
                    >
                      {rb.st}
                    </span>
                    <span className={styles.statusNote}>{rb.note}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>
      ) : null}

      {localToast ? <div className={styles.toast}>{localToast}</div> : null}
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
