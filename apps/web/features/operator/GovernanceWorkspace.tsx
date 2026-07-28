"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./governance.module.css";
import type {
  GovernanceApproval,
  GovernanceAuditCategory,
  GovernanceAuditRow,
  GovernanceDecisionAction,
  GovernanceDecisionPayload,
  GovernanceDecisionRow,
  GovernanceEvidence,
  GovernanceRole,
  GovernanceWorkspaceCallbacks,
} from "./governanceTypes";
import {
  exportEvidencePackage,
  fetchGovernanceSnapshot,
  submitGovernanceDecision,
  type GovernanceSnapshot,
  type GovernanceStatusBoard,
  type GovernanceStatusRow,
} from "./governance/governanceLoader";
import { OperatorDataUnavailableGate } from "./OperatorDataUnavailableGate";
import {
  isSeedDataSource,
  operatorFixturesAllowed,
  payloadContainsSeedData,
  toUnavailableOperatorStatus,
  type OperatorDataAvailability,
} from "./operatorDataMode";

type GovernanceTab = "approvals" | "decisions" | "audit" | "evidencePackage" | "statusBoard";

/**
 * Rendered wherever a governance field is absent or unusable. The row keeps its
 * place in the record, and the gap is shown as a gap: the workspace never fills
 * a missing module, requestor, time, risk or status with a plausible value.
 */
const FIELD_UNAVAILABLE = "未提供";

export type GovernanceWorkspaceProps = {
  approvals?: GovernanceApproval[];
  decisions?: GovernanceDecisionRow[];
  auditRows?: GovernanceAuditRow[];
  role?: GovernanceRole;
  /** Operator role id used for the X-Operator-Role snapshot header. */
  roleId?: string;
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
  { id: "evidencePackage", label: "Evidence Package" },
  { id: "statusBoard", label: "系統狀態" },
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

export function inspectGovernanceSnapshot(
  snapshot: GovernanceSnapshot | null,
): Extract<OperatorDataAvailability, "ready" | "seed" | "empty"> {
  if (!snapshot) return "empty";
  if (payloadContainsSeedData(snapshot) || isSeedDataSource(snapshot.source)) {
    return "seed";
  }

  const statusBoard = snapshot.statusBoard;
  const statusGroups = statusBoard
    ? [
        statusBoard.dataQuality,
        statusBoard.models,
        statusBoard.connectors,
        statusBoard.sla,
        statusBoard.users,
      ]
    : [];
  const hasRequiredShape =
    typeof snapshot.source === "string" &&
    snapshot.source.trim().length > 0 &&
    Array.isArray(snapshot.approvals) &&
    Array.isArray(snapshot.decisions) &&
    Array.isArray(snapshot.auditRows) &&
    Array.isArray(snapshot.evidencePackages) &&
    statusGroups.length === 5 &&
    statusGroups.every(Array.isArray);
  const hasRows = [
    snapshot.approvals,
    snapshot.decisions,
    snapshot.auditRows,
    snapshot.evidencePackages,
    ...statusGroups,
  ].some((rows) => rows.length > 0);

  return hasRequiredShape && hasRows ? "ready" : "empty";
}

export function GovernanceWorkspace({
  approvals: approvalsProp,
  decisions: decisionsProp,
  auditRows: auditRowsProp,
  role = "營運主管",
  roleId,
  canDecide = true,
  callbacks,
}: GovernanceWorkspaceProps) {
  const fixturesAllowed = operatorFixturesAllowed();

  // Callers hand these rows over from the Operator shell envelope, which is a
  // different producer with a different row shape. Normalize at the boundary so
  // a foreign or malformed record can never reach the render path.
  const approvals = useMemo(
    () => (approvalsProp ? normalizeGovernanceApprovals(approvalsProp) : undefined),
    [approvalsProp],
  );
  const decisions = useMemo(
    () => (decisionsProp ? normalizeGovernanceDecisionRows(decisionsProp) : undefined),
    [decisionsProp],
  );
  const auditRows = useMemo(
    () => (auditRowsProp ? normalizeGovernanceAuditRows(auditRowsProp) : undefined),
    [auditRowsProp],
  );

  // Whether each external row feed has ever supplied rows. Used to tell "this
  // caller has no governance side channel" apart from "this caller's side
  // channel went away", which must clear the rows it had supplied.
  const externalApprovalsSeen = useRef(Boolean(approvals));
  const externalDecisionsSeen = useRef(Boolean(decisions));
  const externalAuditRowsSeen = useRef(Boolean(auditRows));

  const [localApprovals, setLocalApprovals] = useState<GovernanceApproval[]>(
    approvals ?? (fixturesAllowed ? fallbackApprovals : []),
  );
  const [localDecisions, setLocalDecisions] = useState<GovernanceDecisionRow[]>(
    decisions ?? (fixturesAllowed ? fallbackDecisions : []),
  );
  const [localAuditRows, setLocalAuditRows] = useState<GovernanceAuditRow[]>(
    auditRows ?? (fixturesAllowed ? fallbackAuditRows : []),
  );

  // When the Govern API answers, it becomes the source of truth: approvals,
  // decisions, audit trail, status board and evidence history all come from
  // /api/v1/operator/governance/snapshot. Local/POC may render fixtures while
  // production waits for a verified live response.
  const [apiActive, setApiActive] = useState(false);
  const [apiStatusBoard, setApiStatusBoard] = useState<GovernanceStatusBoard | null>(null);
  const [apiLoadState, setApiLoadState] = useState<OperatorDataAvailability>(
    fixturesAllowed ? "fixture" : "loading",
  );
  const [apiLoadError, setApiLoadError] = useState<string | null>(null);

  const [activeTab, setActiveTab] = useState<GovernanceTab>("approvals");
  const [selectedApprovalId, setSelectedApprovalId] = useState(localApprovals[0]?.id ?? "");
  const [selectedEvidenceId, setSelectedEvidenceId] = useState(
    localApprovals[0]?.evidence?.[0]?.id ?? "",
  );
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState("");
  const [decisionRunning, setDecisionRunning] = useState(false);
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
  const [evdHist, setEvdHist] = useState<Array<{ id: string; range: string; mod: string; fmt: string; t: string; by: string }>>(
    fixturesAllowed
      ? [
          { id: "EVD-2026-0701-01", range: "2026-06-01 – 2026-06-30", mod: "Store Ops＋Growth＋Network", fmt: "PDF", t: "2026-07-01 10:15", by: "周明德" },
          { id: "EVD-2026-0615-02", range: "2026-05-01 – 2026-05-31", mod: "Store Ops＋Network", fmt: "CSV", t: "2026-06-15 14:22", by: "周明德" },
        ]
      : [],
  );

  const refreshSnapshot = useCallback(async () => {
    if (!fixturesAllowed) setApiLoadState("loading");
    setApiLoadError(null);
    const snapshot = await fetchGovernanceSnapshot(roleId);
    if (!snapshot) {
      setApiLoadState(fixturesAllowed ? "fixture" : "error");
      setApiLoadError("Governance snapshot API 無法完成讀取。");
      return false;
    }
    const inspection = inspectGovernanceSnapshot(snapshot);
    if (!fixturesAllowed && inspection !== "ready") {
      setLocalApprovals([]);
      setLocalDecisions([]);
      setLocalAuditRows([]);
      setApiStatusBoard(null);
      setEvdHist([]);
      setApiActive(false);
      setApiLoadState(inspection);
      setApiLoadError(
        inspection === "seed"
          ? "Governance snapshot API 回傳 seed、fixture 或 mock 資料。"
          : "Governance snapshot API 回傳空白或不完整資料。",
      );
      return false;
    }
    const statusRows = snapshot.statusBoard
      ? Object.values(snapshot.statusBoard).flatMap((rows) => rows ?? [])
      : [];
    const hasData =
      snapshot.approvals.length > 0 ||
      snapshot.decisions.length > 0 ||
      snapshot.auditRows.length > 0 ||
      snapshot.evidencePackages.length > 0 ||
      statusRows.length > 0;
    setLocalApprovals(snapshot.approvals);
    setLocalDecisions(snapshot.decisions);
    setLocalAuditRows(snapshot.auditRows);
    if (snapshot.statusBoard) setApiStatusBoard(snapshot.statusBoard);
    if (snapshot.evidencePackages?.length) {
      setEvdHist(
        snapshot.evidencePackages.map((pkg) => ({
          id: pkg.id,
          range: pkg.range,
          mod: pkg.mod,
          fmt: pkg.fmt,
          t: pkg.t,
          by: pkg.by,
        })),
      );
    }
    setSelectedApprovalId((current) =>
      snapshot.approvals.some((approval) => approval.id === current)
        ? current
        : snapshot.approvals[0]?.id ?? "",
    );
    setApiActive(true);
    if (inspection === "seed") {
      setApiLoadState(fixturesAllowed ? "fixture" : "seed");
    } else {
      setApiLoadState(hasData ? "ready" : fixturesAllowed ? "fixture" : "empty");
    }
    return true;
  }, [fixturesAllowed, roleId]);

  useEffect(() => {
    // Bind on mount / role change; only local/POC keeps fixtures while pending.
    void refreshSnapshot();
  }, [refreshSnapshot]);

  useEffect(() => {
    if (apiActive) return;
    // A caller that had been supplying governance rows and now supplies none has
    // withdrawn its side channel: the previous rows are stale and must not
    // survive the withdrawal. A caller that never supplied any is left alone.
    if (!approvals && !externalApprovalsSeen.current) return;
    externalApprovalsSeen.current = externalApprovalsSeen.current || Boolean(approvals);
    const nextApprovals = approvals ?? [];
    setLocalApprovals(nextApprovals);
    setSelectedApprovalId((current) =>
      nextApprovals.some((approval) => approval.id === current)
        ? current
        : nextApprovals[0]?.id ?? "",
    );
  }, [approvals, apiActive]);

  useEffect(() => {
    if (apiActive) return;
    if (!decisions && !externalDecisionsSeen.current) return;
    externalDecisionsSeen.current = externalDecisionsSeen.current || Boolean(decisions);
    setLocalDecisions(decisions ?? []);
  }, [decisions, apiActive]);

  useEffect(() => {
    if (apiActive) return;
    if (!auditRows && !externalAuditRowsSeen.current) return;
    externalAuditRowsSeen.current = externalAuditRowsSeen.current || Boolean(auditRows);
    setLocalAuditRows(auditRows ?? []);
  }, [auditRows, apiActive]);

  const pendingCount = localApprovals.filter((approval) => approval.status === "pending").length;
  const sortedApprovals = useMemo(
    () =>
      [...localApprovals].sort((left, right) => {
        if (left.status === right.status) return 0;
        if (left.status === "pending") return -1;
        if (right.status === "pending") return 1;
        return 0;
      }),
    [localApprovals],
  );
  const selectedApproval =
    localApprovals.find((approval) => approval.id === selectedApprovalId) ?? localApprovals[0];
  const selectedEvidence =
    selectedApproval?.evidence?.find((evidence) => evidence.id === selectedEvidenceId) ??
    selectedApproval?.evidence?.[0];

  useEffect(() => {
    if (!selectedApproval) {
      setSelectedEvidenceId("");
      return;
    }
    setSelectedEvidenceId((current) =>
      selectedApproval.evidence?.some((evidence) => evidence.id === current)
        ? current
        : selectedApproval.evidence?.[0]?.id ?? "",
    );
  }, [selectedApproval]);

  const auditCategories = useMemo(() => {
    const categorySet = new Set<GovernanceAuditCategory>(baseAuditCategories);
    // Rows without a category keep the canonical filter list unchanged; they
    // stay reachable through 全部 instead of adding a nameless filter chip.
    localAuditRows.forEach((row) => {
      if (row.category) categorySet.add(row.category);
    });
    return Array.from(categorySet);
  }, [localAuditRows]);

  if (!fixturesAllowed && apiLoadState !== "ready") {
    return (
      <OperatorDataUnavailableGate
        detail={apiLoadError}
        onRetry={() => void refreshSnapshot()}
        status={toUnavailableOperatorStatus(apiLoadState)}
      />
    );
  }

  const filteredAuditRows =
    auditCategory === "all"
      ? localAuditRows
      : localAuditRows.filter((row) => row.category === auditCategory);

  const triggerToast = (msg: string) => {
    setLocalToast(msg);
    setTimeout(() => setLocalToast(null), 3200);
  };

  const selectedExportModules = () =>
    [
      evModS ? "Store Ops" : null,
      evModG ? "Growth" : null,
      evModN ? "Network" : null,
      evModV ? "Govern" : null,
    ].filter((value): value is string => Boolean(value));

  const selectedExportContents = () =>
    [
      incAudit ? "Audit Trail" : null,
      incDec ? "Decision Log" : null,
      incOut ? "Outcome 對比" : null,
      incSla ? "SLA 報表" : null,
    ].filter((value): value is string => Boolean(value));

  const handleExport = async () => {
    if (evdRunning) return;
    setEvdRunning(true);
    triggerToast("Evidence Package 產生中…");

    const modules = selectedExportModules();
    const contents = selectedExportContents();

    if (apiActive) {
      const record = await exportEvidencePackage({
        dateFrom: evFrom,
        dateTo: evTo,
        modules,
        contents,
        format: fmt,
        role,
        actorName: role,
        roleId,
      });
      if (record) {
        setEvdResult({
          file: record.file,
          size: record.size,
          t: record.generatedAt.split(" ")[1] ?? record.generatedAt,
          range: record.range,
        });
        setEvdRunning(false);
        // Refresh so the server-recorded audit event + history row appear.
        await refreshSnapshot();
        triggerToast(
          `Evidence Package 已產生 — 保留策略 ${record.retentionPolicy} · 關聯 ${record.correlationId}`,
        );
        return;
      }
      if (!fixturesAllowed) {
        setEvdRunning(false);
        triggerToast("Evidence Package 未建立，API 暫時無法使用。");
        return;
      }
    } else if (!fixturesAllowed) {
      setEvdRunning(false);
      triggerToast("Evidence Package 未建立，Governance API 尚未就緒。");
      return;
    }

    const selectedMods = modules.join("＋") || "全模組";
    const fileExtension = fmt === "CSV" ? "zip" : "pdf";
    const suffix = String(evdHist.length + 3).padStart(2, "0");
    const file = `EVD-2026-0705-${suffix}.${fileExtension}`;
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
      fmt,
      t: `今日 ${result.t}`,
      by: role,
    };
    setEvdHist((prev) => [histItem, ...prev]);

    const newAuditRow: GovernanceAuditRow = {
      id: `aud-${7200 + localAuditRows.length}`,
      category: "export",
      timestamp: nowStr,
      actor: role,
      action: "Export Evidence Package",
      module: "Govern",
      entityRef: file.replace(/\.(pdf|zip)$/, ""),
      summary: `匯出 Evidence Package（離線）: 範圍 ${result.range} · 模組 ${selectedMods} · 格式 ${fmt} · 內容 ${contents.join("／") || "Audit／Decision"}`,
      correlationId: `corr-exp-${suffix}`,
    };
    setLocalAuditRows((prev) => [newAuditRow, ...prev]);
    triggerToast("Evidence Package 已產生（離線）— 可於下方下載");
  };

  function selectApproval(approval: GovernanceApproval) {
    setSelectedApprovalId(approval.id);
    setSelectedEvidenceId(approval.evidence?.[0]?.id ?? "");
    setReason("");
    setReasonError("");
    setLastAction("");
    callbacks?.onSelectApproval?.(approval);
  }

  async function submitDecision(action: GovernanceDecisionAction) {
    if (!selectedApproval || decisionRunning) {
      return;
    }

    const trimmedReason = reason.trim();
    if ((action === "return" || action === "reject") && trimmedReason.length < 10) {
      setReasonError("退回或駁回理由需至少 10 個字");
      return;
    }

    // API path: the server re-enforces the return/reject reason policy and
    // persists the decision + audit event so it survives reload.
    if (apiActive) {
      setDecisionRunning(true);
      const outcome = await submitGovernanceDecision({
        approvalId: selectedApproval.id,
        action,
        reason: trimmedReason,
        role,
        actorName: role,
        roleId,
      });
      if (outcome.ok) {
        await refreshSnapshot();
        setDecisionRunning(false);
        setReason("");
        setReasonError("");
        setLastAction(`${outcome.finalDecision} submitted for ${selectedApproval.id}`);
        const decisionPayload: GovernanceDecisionPayload = {
          approvalId: selectedApproval.id,
          action,
          reason: trimmedReason,
          role,
        };
        if (action === "approve") callbacks?.onApprove?.(decisionPayload);
        else if (action === "return") callbacks?.onReturn?.(decisionPayload);
        else callbacks?.onReject?.(decisionPayload);
        triggerToast(
          `已${action === "approve" ? "核准" : action === "return" ? "退回" : "駁回"}決策 ${selectedApproval.id} — 已寫入 Decision Log 與 Audit`,
        );
        return;
      }
      if (outcome.policyError) {
        setDecisionRunning(false);
        setReasonError(outcome.detail);
        return;
      }
      if (!fixturesAllowed) {
        setDecisionRunning(false);
        setReasonError(outcome.detail);
        return;
      }
      setDecisionRunning(false);
    } else if (!fixturesAllowed) {
      setReasonError("決策未送出，Governance API 尚未就緒。");
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

  // System Status Board — API-bound when the snapshot answers, else fixtures.
  const mapStatusRows = (
    rows: GovernanceStatusRow[] | undefined,
    fallback: Array<{ src?: string; name?: string; ver?: string; st: string; isGood: boolean; note: string }>,
  ) =>
    rows && rows.length
      ? rows.map((row) => ({
          src: row.source ?? row.name ?? "",
          name: row.name ?? row.source ?? "",
          ver: row.version ?? "",
          st: row.status,
          isGood: row.good,
          note: row.note,
        }))
      : fixturesAllowed
        ? fallback
        : [];

  const dqRows = mapStatusRows(apiStatusBoard?.dataQuality, [
    { src: "Google Reviews Connector", st: "正常", isGood: true, note: "15 分鐘前同步 · 覆蓋 12/12 門市" },
    { src: "Camera Events", st: "延遲", isGood: false, note: "事件延遲 12 分鐘 · 影響即時性" },
    { src: "POS／支付交易", st: "正常", isGood: true, note: "即時串流 · 缺漏 0.2%" },
    { src: "591 物件源", st: "正常", isGood: true, note: "每日 06:00 匯入 · 昨日新增 14 筆" },
    { src: "IoT 心跳", st: "注意", isGood: false, note: "1 台設備 >3h 未回報（ISS-1021）" },
  ]);

  const modelRows = mapStatusRows(apiStatusBoard?.models, [
    { name: "SiteScore", ver: "v2.3", st: "上線", isGood: true, note: "選址評分 · 每週再訓練 · 用於 Network" },
    { name: "CS Intent", ver: "v1.8", st: "上線", isGood: true, note: "客服意圖分類 · 準確率 91%" },
    { name: "PriceOps", ver: "v0.9", st: "Shadow", isGood: false, note: "動態定價 · 影子模式驗證中" },
    { name: "Camera Event", ver: "v1.2", st: "上線", isGood: true, note: "場域事件偵測 · 不含人臉" },
  ]);

  const connRows = mapStatusRows(apiStatusBoard?.connectors, [
    { name: "Google Business Profile", st: "已連接", isGood: true, note: "評價／回覆 API" },
    { name: "LINE 官方帳號", st: "已連接", isGood: true, note: "客服＋推播" },
    { name: "591 租屋網", st: "已連接", isGood: true, note: "每日物件匯入" },
    { name: "TapPay 金流閘道", st: "已連接", isGood: true, note: "交易／退款 webhook" },
  ]);

  const slaRows = mapStatusRows(apiStatusBoard?.sla, [
    { name: "核准處理 SLA", st: "達成", isGood: true, note: "P95 42m · 目標 <2h" },
    { name: "事件升級 SLA", st: "達成", isGood: true, note: "橘/紅燈 30m 內升級" },
    { name: "證據匯出 SLA", st: "注意", isGood: false, note: "1 筆匯出等待簽章 >1h" },
  ]);

  const usersRows = mapStatusRows(apiStatusBoard?.users, [
    { name: "營運主管", st: "啟用", isGood: true, note: "全域監控、跨域指派與核准" },
    { name: "PM／稽核", st: "啟用", isGood: true, note: "模型、決策追蹤與稽核線索" },
    { name: "行銷經理", st: "啟用", isGood: true, note: "活動、分群、定價建議" },
    { name: "展店經理", st: "啟用", isGood: true, note: "HeatZone、候選點與 SiteScore" },
  ]);

  const runbookRows = mapStatusRows(apiStatusBoard?.runbooks, [
    { name: "災備演練 (Disaster Recovery)", st: "正常", isGood: true, note: "Completed 2026-07-01 · 復原時間 18m" },
    { name: "資料備份與還原 (Backup & Restore)", st: "正常", isGood: true, note: "每日 03:00 自動備份 · 驗證成功" },
    { name: "事件管理與升級 (Incident Management)", st: "運作中", isGood: true, note: "SLA 升級規則已啟用 · 監控端點正常" },
    { name: "系統觀測性 (Observability)", st: "正常", isGood: true, note: "Prometheus/Grafana 指標正常 · 心跳正常" },
  ]);

  return (
    <section className={styles.workspace} data-testid="governance-workspace" data-screen-label="Govern 治理稽核">
      <header className={styles.header}>
        <div>
          <h2>治理稽核</h2>
          <p>核准、決策、稽核與證據 — 所有處置的可追溯層</p>
        </div>
        <div className={styles.headerStats} aria-label="Governance state">
          <span>{pendingCount} 件待核准</span>
          <span>{role}</span>
          <span>{canDecide ? "可決策" : "僅可查看"}</span>
        </div>
      </header>

      <nav className={styles.tabs} aria-label="Governance tabs">
        {tabs.map((tab) => (
          <button
            aria-current={activeTab === tab.id ? "page" : undefined}
            className={styles.tab}
            data-testid={`governance-tab-${tab.id}`}
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            type="button"
          >
            {tab.label}
            {tab.id === "approvals" && pendingCount > 0 ? (
              <span className={styles.tabCount}>{pendingCount}</span>
            ) : null}
          </button>
        ))}
      </nav>

      {activeTab === "approvals" ? (
        <section className={styles.approvalGrid} aria-label="Approval center">
          <div className={styles.queuePanel} aria-label="核准佇列">
            <div className={styles.queueHeader}>
              <h3>核准佇列</h3>
              <span>{pendingCount} 待處理 · {localApprovals.length} 全部</span>
            </div>
            <div className={styles.queueList}>
              {sortedApprovals.map((approval) => (
                <button
                  aria-current={selectedApproval?.id === approval.id ? "true" : undefined}
                  className={styles.queueItem}
                  key={approval.id}
                  onClick={() => selectApproval(approval)}
                  type="button"
                >
                  <span className={styles.queueTopline}>
                    <span className={styles.approvalId}>{approval.id}</span>
                    <span className={moduleClass(approval.module)}>{approval.module}</span>
                    <span className={priorityClass(approval.priority)}>
                      風險 {approval.priority ? priorityLabel(approval.priority) : FIELD_UNAVAILABLE}
                    </span>
                    <span className={statusClass(approval.status)}>
                      {approvalStatusLabel(approval.status)}
                    </span>
                  </span>
                  <strong>{approval.title}</strong>
                  <span className={styles.queueFooter}>
                    <span>{approval.requestor ?? FIELD_UNAVAILABLE}</span>
                    <span className={styles.sla}>{approval.sla ?? "未設定 SLA"}</span>
                  </span>
                </button>
              ))}
              {sortedApprovals.length === 0 ? (
                <div className={styles.queueEmpty}>目前沒有核准請求</div>
              ) : null}
            </div>
          </div>

          <article className={styles.detailPanel} aria-label="Approval detail">
            {selectedApproval ? (
              <>
                <div className={styles.detailHeader}>
                  <div className={styles.detailIdentity}>
                    <span className={styles.approvalId}>{selectedApproval.id}</span>
                    <span className={moduleClass(selectedApproval.module)}>
                      {selectedApproval.module}
                    </span>
                    <span className={priorityClass(selectedApproval.priority)}>
                      風險{" "}
                      {selectedApproval.priority
                        ? priorityLabel(selectedApproval.priority)
                        : FIELD_UNAVAILABLE}
                    </span>
                  </div>
                  <span className={statusClass(selectedApproval.status)}>
                    {approvalStatusLabel(selectedApproval.status)}
                  </span>
                </div>
                <h3 className={styles.detailTitle}>{selectedApproval.title}</h3>

                <dl className={styles.detailMeta}>
                  <div>
                    <dt>申請人</dt>
                    <dd>{selectedApproval.requestor ?? FIELD_UNAVAILABLE}</dd>
                  </div>
                  <div>
                    <dt>核准角色</dt>
                    <dd>{selectedApproval.owner ?? role}</dd>
                  </div>
                  <div>
                    <dt>SLA</dt>
                    <dd className={styles.sla}>{selectedApproval.sla ?? "未設定"}</dd>
                  </div>
                </dl>

                <div className={styles.summaryGrid}>
                  <section>
                    <h4>申請摘要</h4>
                    <p>{selectedApproval.summary ?? "未提供申請摘要。"}</p>
                  </section>
                  <section>
                    <h4>系統建議</h4>
                    <p>{selectedApproval.systemRecommendation ?? "未提供系統建議。"}</p>
                  </section>
                  <section>
                    <h4>風險說明</h4>
                    <p>{selectedApproval.risk ?? "未提供風險說明。"}</p>
                  </section>
                  <section>
                    <h4>決策邊界</h4>
                    <p>{selectedApproval.roleNote ?? `${role} 必須先完成證據審查。`}</p>
                  </section>
                </div>

                <section className={styles.evidenceBlock} aria-label="隨附證據">
                  <div className={styles.sectionHeading}>
                    <h4>隨附證據</h4>
                    <span>{selectedApproval.evidence?.length ?? 0} 項</span>
                  </div>
                  <div className={styles.evidenceChips}>
                    {(selectedApproval.evidence ?? []).map((evidence) => (
                        <button
                          aria-pressed={selectedEvidence?.id === evidence.id}
                          className={styles.evidenceChip}
                          key={evidence.id}
                          onClick={() => setSelectedEvidenceId(evidence.id)}
                          type="button"
                        >
                          <span>{evidence.label}</span>
                          <small>{evidence.state ?? evidence.type ?? "ready"}</small>
                        </button>
                      ))}
                    {selectedApproval.evidence?.length ? null : (
                      <span className={styles.emptyChip}>沒有可驗證證據</span>
                    )}
                  </div>
                  {selectedEvidence ? (
                    <EvidenceDetail evidence={selectedEvidence} approval={selectedApproval} />
                  ) : null}
                  <div className={styles.referenceRow}>
                    <span>關聯</span>
                    <strong>{selectedApproval.entityRef ?? selectedApproval.id}</strong>
                    <span>送出 {selectedApproval.submittedAt ?? FIELD_UNAVAILABLE}</span>
                  </div>
                </section>

                <section className={styles.decisionBox} aria-label="Decision reason">
                  {selectedApproval.status === "pending" ? (
                    <>
                      <label htmlFor="governance-reason">
                        決策理由
                        <span>核准選填；退回／駁回必填，寫入 Decision Log</span>
                      </label>
                      <textarea
                        aria-describedby={reasonError ? "governance-reason-error" : "governance-reason-help"}
                        aria-invalid={Boolean(reasonError)}
                        id="governance-reason"
                        onChange={(event) => {
                          setReason(event.target.value);
                          if (event.target.value.trim().length >= 10) {
                            setReasonError("");
                          }
                        }}
                        placeholder="例：受眾與預算合規，成效量測明確…"
                        rows={2}
                        value={reason}
                      />
                      <div className={styles.reasonRow}>
                        <span id="governance-reason-help">退回或駁回理由至少 10 個字</span>
                        {reasonError ? (
                          <strong className={styles.errorText} id="governance-reason-error" role="alert">
                            {reasonError}
                          </strong>
                        ) : null}
                      </div>
                      {canDecide ? (
                        <div className={styles.actions} aria-busy={decisionRunning}>
                          <button disabled={decisionRunning} onClick={() => submitDecision("approve")} type="button">
                            {decisionRunning ? "送出中…" : "核准"}
                          </button>
                          <button disabled={decisionRunning} onClick={() => submitDecision("return")} type="button">
                            退回修改
                          </button>
                          <button disabled={decisionRunning} onClick={() => submitDecision("reject")} type="button">
                            駁回
                          </button>
                        </div>
                      ) : (
                        <div className={styles.readOnlyNotice}>
                          目前角色僅可查看 — 核准需營運主管或 PM／稽核
                        </div>
                      )}
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
              <div className={styles.emptyState}>目前沒有核准請求</div>
            )}
          </article>
        </section>
      ) : null}

      {activeTab === "decisions" ? (
        <section className={styles.logSection} aria-label="Decision Log">
          <div className={styles.viewHeader}>
            <div>
              <h3>Decision Log</h3>
              <p>系統建議、最終決策與採用證據的不可分割紀錄</p>
            </div>
            <span>{localDecisions.length} 筆</span>
          </div>
          <div className={styles.decisionList}>
            {localDecisions.map((decision) => (
              <article className={styles.decisionCard} key={decision.id}>
                <header>
                  <span className={styles.approvalId}>{decision.id}</span>
                  <span className={moduleClass(decision.module)}>{decision.module}</span>
                  <strong>{decision.item}</strong>
                  <time>{decision.decidedAt ?? FIELD_UNAVAILABLE}</time>
                </header>
                <div className={styles.decisionCompare}>
                  <section>
                    <span>系統建議</span>
                    <p>{decision.systemRecommendation ?? FIELD_UNAVAILABLE}</p>
                  </section>
                  <section>
                    <span>最終決策</span>
                    <p>{decision.finalDecision ?? FIELD_UNAVAILABLE}</p>
                  </section>
                </div>
                <p className={styles.decisionReason}>
                  理由：{decision.reason ?? FIELD_UNAVAILABLE}
                </p>
                <footer>
                  <span>決策人：<strong>{decision.actor ?? FIELD_UNAVAILABLE}</strong></span>
                  <span>模型：<code>{decision.model ?? "n/a"}</code></span>
                  <span>資料快照：<code>{decision.datasetSnapshot ?? "n/a"}</code></span>
                  <span>關聯核准：<code>{decision.approvalId ?? "n/a"}</code></span>
                </footer>
              </article>
            ))}
            {localDecisions.length === 0 ? (
              <div className={styles.emptyState}>目前沒有決策紀錄</div>
            ) : null}
          </div>
        </section>
      ) : null}

      {activeTab === "audit" ? (
        <section className={styles.logSection} aria-label="Audit Trail">
          <div className={styles.viewHeader}>
            <div>
              <h3>Audit Trail</h3>
              <p>依時間追蹤 actor、事件、實體與 correlation ID</p>
            </div>
            <span>{filteredAuditRows.length} 筆</span>
          </div>
          <div className={styles.filters} aria-label="Audit category filters">
            <button
              aria-current={auditCategory === "all" ? "true" : undefined}
              onClick={() => setAuditCategory("all")}
              type="button"
            >
              全部
            </button>
            {auditCategories.map((category) => (
              <button
                aria-current={auditCategory === category ? "true" : undefined}
                key={category}
                onClick={() => setAuditCategory(category)}
                type="button"
              >
                {auditCategoryLabel(category)}
              </button>
            ))}
          </div>
          <div className={styles.auditFeed}>
            {filteredAuditRows.map((row) => (
              <article className={styles.auditRow} key={row.id}>
                <time>{row.timestamp ?? FIELD_UNAVAILABLE}</time>
                <div className={styles.auditActor}>
                  <strong>{row.actor ?? FIELD_UNAVAILABLE}</strong>
                  <span>
                    {row.module ??
                      (row.category ? auditCategoryLabel(row.category) : FIELD_UNAVAILABLE)}
                  </span>
                </div>
                <div className={styles.auditEvent}>
                  <strong>{row.action}</strong>
                  {row.category === "camera" ? (
                    <span className={styles.sensitiveBadge}>隱私敏感</span>
                  ) : null}
                  <p>{row.summary ?? row.reason ?? "未提供事件摘要"}</p>
                  <code>{row.correlationId ?? "correlation unavailable"}</code>
                </div>
                <span className={styles.auditEntity}>{row.entityRef ?? "n/a"}</span>
              </article>
            ))}
            {filteredAuditRows.length === 0 ? (
              <div className={styles.emptyState}>此分類目前沒有稽核事件</div>
            ) : null}
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
              data-testid="governance-export-button"
              disabled={evdRunning}
              onClick={handleExport}
              type="button"
            >
              {evdRunning ? "產生中…" : "產生 Evidence Package"}
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
                {fixturesAllowed ? (
                  <button
                    className={styles.downloadButton}
                    onClick={() => triggerToast(`已下載證據包：${evdResult.file}`)}
                    type="button"
                  >
                    下載 (local fixture)
                  </button>
                ) : (
                  <span className={styles.errorText}>
                    尚未提供可下載 artifact，請由 Evidence Package API 回傳正式下載位置。
                  </span>
                )}
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
        <section className={styles.statusSection} aria-label="System status board">
          <div className={styles.viewHeader}>
            <div>
              <h3>系統狀態</h3>
              <p>Data Quality、模型、Connector、SLA、角色權限與 Runbook</p>
            </div>
            <span>{dqRows.length + modelRows.length + connRows.length + slaRows.length} 個監控項目</span>
          </div>
          <div className={styles.statusBoardGrid}>
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

            <div className={styles.statusCard} data-testid="governance-sla-card">
              <div className={styles.statusCardTitle}>SLA 監控</div>
              <div style={{ display: "flex", flexDirection: "column" }}>
                {slaRows.map((sr) => (
                  <div className={styles.statusRow} key={sr.name || sr.src}>
                    <span className={styles.statusNameWide}>{sr.name || sr.src}</span>
                    <span
                      className={`${styles.statusBadge} ${
                        sr.isGood ? styles.badgeGood : styles.badgeWarn
                      }`}
                    >
                      {sr.st}
                    </span>
                    <span className={styles.statusNote}>{sr.note}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className={styles.statusCard} data-testid="governance-users-card">
              <div className={styles.statusCardTitle}>Users 角色與權限</div>
              <div style={{ display: "flex", flexDirection: "column" }}>
                {usersRows.map((ur) => (
                  <div className={styles.statusRow} key={ur.name || ur.src}>
                    <span className={styles.statusNameWide}>{ur.name || ur.src}</span>
                    <span
                      className={`${styles.statusBadge} ${
                        ur.isGood ? styles.badgeGood : styles.badgeWarn
                      }`}
                    >
                      {ur.st}
                    </span>
                    <span className={styles.statusNote}>{ur.note}</span>
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
          </div>
        </section>
      ) : null}

      {localToast ? <div className={styles.toast}>{localToast}</div> : null}
    </section>
  );
}

function EvidenceDetail({
  approval,
  evidence,
}: {
  approval: GovernanceApproval;
  evidence: GovernanceEvidence;
}) {
  return (
    <div className={styles.evidenceDetail} data-testid="governance-selected-evidence">
      <div>
        <span>已選證據</span>
        <strong>{evidence.label}</strong>
      </div>
      <dl>
        <div>
          <dt>Evidence ID</dt>
          <dd>{evidence.id}</dd>
        </div>
        <div>
          <dt>類型</dt>
          <dd>{evidence.type ?? "unspecified"}</dd>
        </div>
        <div>
          <dt>狀態</dt>
          <dd>{evidence.state ?? "ready"}</dd>
        </div>
        <div>
          <dt>綁定實體</dt>
          <dd>{approval.entityRef ?? approval.id}</dd>
        </div>
      </dl>
      {evidence.href ? (
        <a href={evidence.href} rel="noreferrer" target="_blank">
          開啟證據來源
        </a>
      ) : (
        <p>此證據僅能在治理紀錄中檢視，未提供外部連結。</p>
      )}
    </div>
  );
}

/**
 * Badge helpers run against externally sourced rows, so they must stay total:
 * a missing or non-string field renders the explicit unavailable marker and a
 * neutral badge tone instead of throwing or standing in for a real value.
 */
function badgeText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function approvalStatusLabel(value: unknown) {
  const raw = badgeText(value);
  const labels: Record<string, string> = {
    pending: "待核准",
    approved: "已核准",
    returned: "退回修改",
    rejected: "已駁回",
    escalated: "已升級",
  };
  return labels[raw.toLowerCase()] ?? (raw || FIELD_UNAVAILABLE);
}

function priorityLabel(value: unknown) {
  const raw = badgeText(value);
  const labels: Record<string, string> = {
    low: "低",
    medium: "中",
    high: "高",
    critical: "極高",
  };
  return labels[raw.toLowerCase()] ?? (raw || FIELD_UNAVAILABLE);
}

function priorityClass(value: unknown) {
  const normalized = badgeText(value).toLowerCase();
  if (normalized === "critical" || normalized === "high") {
    return `${styles.badge} ${styles.badgeDanger}`;
  }
  if (normalized === "medium") {
    return `${styles.badge} ${styles.badgeWarn}`;
  }
  return styles.badge;
}

function moduleClass(value: unknown) {
  const normalized = badgeText(value).toLowerCase();
  const tone =
    normalized === "growth"
      ? styles.moduleGrowth
      : normalized === "network"
        ? styles.moduleNetwork
        : normalized === "store ops"
          ? styles.moduleStore
          : styles.moduleGovern;
  return `${styles.moduleBadge} ${tone}`;
}

function auditCategoryLabel(value: unknown) {
  const raw = badgeText(value);
  const labels: Record<string, string> = {
    issue: "Issue",
    camera: "影像調閱",
    approval: "核准",
    growth: "Growth",
    network: "Network",
    export: "匯出",
    system: "系統",
  };
  return labels[raw.toLowerCase()] ?? (raw || FIELD_UNAVAILABLE);
}

function statusClass(value: unknown) {
  const normalized = badgeText(value).toLowerCase().replace(/[^a-z0-9]+/g, "");
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
