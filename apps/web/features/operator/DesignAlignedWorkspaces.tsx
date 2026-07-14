"use client";

import { CSSProperties, useState, useMemo, useEffect } from "react";
import { ISSUE_FIXTURES, EVIDENCE_FIXTURES, AUDIT_EVENT_FIXTURES, STORE_FIXTURES } from "./fixtures";
import styles from "./designAligned.module.css";
import type { AuditEvent, EvidenceItem, Issue, Severity, Store, StoreLightStatus, OperatorRoleId } from "./types";
import { STORE_OPS_REFRESH_EVENT, type StoreOpsWorkflowDialogType } from "./storeOpsWorkflowTypes";
import {
  filterStoreOpsIssues,
  getIssueEvidence,
  getAiRecommendation,
  getPrimaryActionLabel,
  getSecondaryActionLabels,
  getLocalAuditEvents,
  formatCompactDateTime,
  formatSla,
  getStatusLabel,
  getSeverityLabel,
  getSeverityTone,
  getStatusTone,
  getSourceLabel,
  getSourceTone,
} from "./storeOpsViewModel";

type DesignTodayWorkspaceProps = {
  onQueueSelect: (workspaceId: "store" | "growth" | "network" | "govern") => void;
  kpis?: any[];
  todayRows?: any[];
  decisions?: any[];
  riskStores?: any[];
  auditFeed?: any[];
};

type DesignStoreOpsWorkspaceProps = {
  onOpenWorkflow: (dialog: StoreOpsWorkflowDialogType, issue: Issue) => void;
  issues?: Issue[];
};

type StoreOpsLightDimension = keyof Store["lights"];

type StoreOpsLightFilter = {
  dimension: StoreOpsLightDimension;
  status: StoreLightStatus;
};

type StoreOpsLightSummary = {
  dimension: StoreOpsLightDimension;
  label: string;
  counts: Record<StoreLightStatus, number>;
  issueCounts: Record<StoreLightStatus, number>;
};

type StoreOpsApiState = {
  stores: Store[];
  issues: Issue[];
  evidence: EvidenceItem[];
  auditEvents: AuditEvent[];
  fourLightSummary: StoreOpsLightSummary[];
  count: number;
};

const lightStatusOrder: StoreLightStatus[] = ["red", "yellow"];

const lightStatusLabels: Record<StoreLightStatus, string> = {
  green: "Green",
  yellow: "Yellow",
  red: "Red",
};

const kpis = [
  { label: "高風險未指派", value: "1", note: "下一步：完成 Triage 與指派", tone: "danger" },
  { label: "已逾期 Issue", value: "1", note: "優先處理 SLA 逾期", tone: "danger" },
  { label: "即將逾期", value: "4", note: "今日期限內需完成", tone: "warn" },
  { label: "成效待判斷", value: "1", note: "請完成 Outcome Review", tone: "info" },
  { label: "待我核准", value: "4", note: "前往治理稽核・核准中心", tone: "info" },
  { label: "需升級門市", value: "1", note: "連續紅燈・店網重估", tone: "muted" },
];

const todayRows = [
  {
    id: "ISS-1021",
    title: "Kiosk 離線＋遠端重啟失敗",
    store: "皇羽自助洗衣 新莊店",
    signals: ["設備異常", "IoT", "支付"],
    state: "已指派",
    due: "已逾期 1h 24m",
    owner: "陳建宏",
    cta: "建立處置",
    tone: "danger",
  },
  {
    id: "ISS-1024",
    title: "付款機前卡住＋付款失敗＋Google 負評",
    store: "Oday 信義松仁店",
    signals: ["支付異常", "評價", "客服", "影像", "支付", "IoT"],
    state: "新進",
    due: "3h 12m",
    owner: "未指派",
    cta: "完成 Triage",
    tone: "danger",
  },
  {
    id: "ISS-1015",
    title: "地面髒亂 Camera 事件＋Google 一星評論",
    store: "Oday 大安和平店",
    signals: ["清潔品質", "影像", "評價", "清潔"],
    state: "已分類",
    due: "2h 40m",
    owner: "未指派",
    cta: "指派 Owner",
    tone: "warn",
  },
  {
    id: "ISS-1019",
    title: "烘不乾客訴增加＋乾衣機 cycle 異常",
    store: "Oday 板橋府中店",
    signals: ["設備異常", "客服", "IoT"],
    state: "處置中",
    due: "明日 18:00 前",
    owner: "陳建宏",
    cta: "提交現場回報",
    tone: "warn",
  },
  {
    id: "ISS-1008",
    title: "離峰閒置率高＋會員回訪下降",
    store: "洗多星 中壢中原店",
    signals: ["營收／需求", "預測", "支付"],
    state: "成效待判斷",
    due: "今日內判斷",
    owner: "黃仕杰",
    cta: "判斷成效",
    tone: "muted",
  },
  {
    id: "ISS-1017",
    title: "退款申請 NT$180 逾 24h 未處理",
    store: "Oday 板橋府中店",
    signals: ["支付／退款", "客服", "支付"],
    state: "已指派",
    due: "今日 16:00 前",
    owner: "張珮珊",
    cta: "建立處置",
    tone: "muted",
  },
];

const decisions = [
  { tag: "核准", time: "7/8 前", title: "SiteScore 審核：板橋府中候選點（WAIT 76）" },
  { tag: "核准", time: "7/4 18:00 前", title: "活動核准：60 天未回訪會員召回（LINE 推播）" },
  { tag: "核准", time: "今日 17:00 前", title: "退款批次核准：7 筆／NT$1,240" },
  { tag: "成效判斷", time: "今日內", title: "ISS-1008：離峰閒置率高＋會員回訪下降" },
];

const riskStores = [
  { name: "Oday 信義松仁店", note: "支付異常處理中（ISS-1024）", tone: "warn" },
  { name: "Oday 板橋府中店", note: "連續 8 週橙／紅燈・營收下滑", tone: "warn" },
  { name: "Oday 大安和平店", note: "租金壓力高・回本期延長", tone: "warn" },
  { name: "洗多星 中壢中原店", note: "連續 90 天紅燈・重配候選", tone: "danger" },
  { name: "皇羽自助洗衣 新莊店", note: "低回訪＋商圈變化・Kiosk 工單處理中", tone: "warn" },
];

const storeQueue = [
  {
    id: "ISS-1021",
    title: "Kiosk 離線＋遠端重啟失敗",
    store: "皇羽自助洗衣 新莊店",
    status: "已指派",
    due: "已逾期 1h 24m",
    tags: ["IoT", "支付"],
    owner: "陳建宏",
    tone: "danger",
    next: "建立處置",
  },
  {
    id: "ISS-1024",
    title: "付款機前卡住＋付款失敗＋Google 負評",
    store: "Oday 信義松仁店",
    status: "新進",
    due: "3h 12m",
    tags: ["評價", "客服", "影像", "支付", "IoT"],
    owner: "未指派",
    tone: "danger",
    next: "完成 Triage",
  },
  {
    id: "ISS-1015",
    title: "地面髒亂 Camera 事件＋Google 一星評論",
    store: "Oday 大安和平店",
    status: "警示",
    due: "2h 40m",
    tags: ["影像", "評價", "清潔"],
    owner: "未指派",
    tone: "warn",
    next: "指派 Owner",
  },
  {
    id: "ISS-1019",
    title: "烘不乾客訴增加＋乾衣機 cycle 異常",
    store: "Oday 板橋府中店",
    status: "警示",
    due: "明日 18:00 前",
    tags: ["客服", "IoT"],
    owner: "陳建宏",
    tone: "warn",
    next: "提交現場回報",
  },
  {
    id: "ISS-1008",
    title: "離峰閒置率高＋會員回訪下降",
    store: "洗多星 中壢中原店",
    status: "一般",
    due: "今日內判斷",
    tags: ["預測", "支付"],
    owner: "黃仕杰",
    tone: "muted",
    next: "判斷成效",
  },
];

export function DesignTodayWorkspace({
  onQueueSelect,
  kpis: propKpis,
  todayRows: propTodayRows,
  decisions: propDecisions,
  riskStores: propRiskStores,
  auditFeed: propAuditFeed,
}: DesignTodayWorkspaceProps) {
  const activeKpis = propKpis || kpis;
  const activeTodayRows = propTodayRows || todayRows;
  const activeDecisions = propDecisions || decisions;
  const activeRiskStores = propRiskStores || riskStores;

  return (
    <div className={styles.todayWorkspace} data-screen-label="Today 今日工作">
      <header className={styles.hero}>
        <div>
          <h1>早安，林承翰 — 營運主管</h1>
          <p>資料範圍：全品牌・12 門市・北北桃</p>
        </div>
        <div className={styles.heroMeta}>
          <span>2026/07/05 ・週日</span>
          <strong>Demo 視角：營運主管</strong>
        </div>
      </header>

      <section className={styles.kpiGrid} aria-label="Today KPI cards">
        {activeKpis.map((item) => (
          <article className={styles.kpiCard} data-tone={item.tone} key={item.label}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <p>{item.note || item.meta}</p>
          </article>
        ))}
      </section>

      <div className={styles.todayGrid}>
        <section className={styles.tablePanel} aria-label="今天最需要處理">
          <div className={styles.panelHeader}>
            <div>
              <h2>今天最需要處理</h2>
              <span>依嚴重度與 SLA 排序</span>
            </div>
            <span>{activeTodayRows.length} 項</span>
          </div>
          <div className={styles.issueTable}>
            {activeTodayRows.map((row) => (
              <button className={styles.todayRow} key={row.id} onClick={() => onQueueSelect("store")} type="button">
                <i data-tone={row.tone} />
                <span className={styles.rowMain}>
                  <small>{row.id}</small>
                  <strong>{row.title}</strong>
                  <span>{row.store || row.storeName}</span>
                  <span className={styles.tagLine}>
                    {(row.signals || row.tags || []).map((signal: string) => (
                      <b key={signal}>{signal}</b>
                    ))}
                  </span>
                </span>
                <span className={styles.rowState}>
                  <b>{row.state || row.status}</b>
                  <em>{row.due || row.time || ""}</em>
                </span>
                <span className={styles.rowOwner}>
                  <small>Owner</small>
                  <strong>{row.owner}</strong>
                </span>
                <span className={styles.rowCta}>{row.cta || row.next || "處理"} →</span>
              </button>
            ))}
          </div>
        </section>

        <aside className={styles.todayRail}>
          <section className={styles.railPanel}>
            <div className={styles.panelHeader}>
              <h2>需要你決策</h2>
            </div>
            <div className={styles.decisionStack}>
              {activeDecisions.map((item) => (
                <button className={styles.decisionItem} key={item.title} onClick={() => onQueueSelect("govern")} type="button">
                  <span>
                    <b>{item.tag || item.cta}</b>
                    {item.time || item.status}
                  </span>
                  <strong>{item.title}</strong>
                  <em>進行核准 →</em>
                </button>
              ))}
            </div>
          </section>

          <section className={styles.railPanel}>
            <div className={styles.panelHeader}>
              <h2>門市風險快照</h2>
              <span>12 門市・示意</span>
            </div>
            <div className={styles.riskMap} aria-label="門市風險地圖示意">
              {activeRiskStores.map((store, index) => (
                <i data-tone={store.tone} key={store.name || store.label} style={{ "--x": `${18 + index * 16}%`, "--y": `${34 + (index % 3) * 14}%` } as CSSProperties} />
              ))}
            </div>
            <div className={styles.riskList}>
              {activeRiskStores.map((store) => (
                <div key={store.name || store.label}>
                  <i data-tone={store.tone} />
                  <strong>{store.name || store.label}</strong>
                  <span>{store.note || store.signal}</span>
                </div>
              ))}
            </div>
          </section>

          <section className={styles.railPanel}>
            <div className={styles.panelHeader}>
              <h2>最近動態</h2>
              <span>AUDIT FEED</span>
            </div>
            <div className={styles.auditMini}>
              {propAuditFeed ? (
                propAuditFeed.map((item, idx) => (
                  <p key={idx}><time>{item.time}</time> {item.actor ? `${item.actor}: ` : ""}{item.detail || item.message}</p>
                ))
              ) : (
                <>
                  <p><time>09:12</time> 系統 ForecastOps 捕捉連續紅燈（90 天）</p>
                  <p><time>08:44</time> 支付異常自動併入 ISS-1024</p>
                  <p><time>08:20</time> 核准中心新增 SiteScore WAIT 76</p>
                </>
              )}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

export function DesignStoreOpsWorkspace({ onOpenWorkflow, issues: propIssues }: DesignStoreOpsWorkspaceProps) {
  // 1. Get current operator role from sessionStorage if available
  const [roleId, setRoleId] = useState<OperatorRoleId>("opsLead");
  const [apiState, setApiState] = useState<StoreOpsApiState | null>(null);
  const [storeOpsRefreshToken, setStoreOpsRefreshToken] = useState(0);
  const [isStoreOpsLoading, setIsStoreOpsLoading] = useState(false);
  useEffect(() => {
    if (typeof window !== "undefined") {
      const storedRole = window.sessionStorage.getItem("oday.operator.role") as OperatorRoleId;
      if (storedRole) {
        setRoleId(storedRole);
      }
    }
  }, []);

  // 2. Search & filter states
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [selectedSeverities, setSelectedSeverities] = useState<Severity[]>([]);
  const [selectedLightFilter, setSelectedLightFilter] = useState<StoreOpsLightFilter | null>(null);
  const [mineOnly, setMineOnly] = useState(false);
  const [selectedIssueId, setSelectedIssueId] = useState<string>("ISS-1024");

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const handleRefresh = () => setStoreOpsRefreshToken((token) => token + 1);
    window.addEventListener(STORE_OPS_REFRESH_EVENT, handleRefresh);
    return () => window.removeEventListener(STORE_OPS_REFRESH_EVENT, handleRefresh);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadStoreOpsIssues() {
      const params = new URLSearchParams();
      if (searchQuery.trim()) params.set("query", searchQuery.trim());
      selectedSources.forEach((source) => params.append("sources", source));
      selectedSeverities.forEach((severity) => params.append("severities", severity));
      if (mineOnly) params.set("mineOnly", "true");
      params.set("roleId", roleId);
      if (selectedLightFilter) {
        params.set("light", selectedLightFilter.dimension);
        params.set("lightStatus", selectedLightFilter.status);
      }

      setIsStoreOpsLoading(true);
      try {
        const response = await fetch(`/api/v1/operator/store-ops/issues?${params.toString()}`);
        if (!response.ok) return;
        const data = (await response.json()) as StoreOpsApiState;
        if (!cancelled) {
          setApiState(data);
        }
      } catch (error) {
        console.error("Error loading Store Ops issues:", error);
      } finally {
        if (!cancelled) {
          setIsStoreOpsLoading(false);
        }
      }
    }

    loadStoreOpsIssues();
    return () => {
      cancelled = true;
    };
  }, [searchQuery, selectedSources, selectedSeverities, mineOnly, roleId, selectedLightFilter, storeOpsRefreshToken]);

  const issueSource = apiState?.issues ?? propIssues ?? ISSUE_FIXTURES;
  const activeStores = apiState?.stores ?? STORE_FIXTURES;
  const activeEvidence = apiState?.evidence ?? EVIDENCE_FIXTURES;
  const activeAuditEvents = apiState?.auditEvents ?? AUDIT_EVENT_FIXTURES;
  const fourLightSummary = apiState?.fourLightSummary ?? [];

  // 3. Apply filters and sort (by severity + SLA)
  const filteredIssues = useMemo(() => {
    if (apiState) {
      return issueSource;
    }
    return filterStoreOpsIssues(
      issueSource,
      {
        search: searchQuery,
        statuses: [],
        sources: selectedSources as any[],
        severities: selectedSeverities,
        mineOnly,
      },
      roleId
    );
  }, [apiState, issueSource, searchQuery, selectedSources, selectedSeverities, mineOnly, roleId]);

  // 4. Resolve selected issue
  const issue = useMemo(() => {
    return filteredIssues.find((i) => i.id === selectedIssueId) ?? filteredIssues[0] ?? ISSUE_FIXTURES[0];
  }, [filteredIssues, selectedIssueId]);

  // 5. Generate 28-day revenue forecast band data based on selected store
  const forecastData = useMemo(() => {
    const baseVal = issue.storeId === "ST-014" ? 14000 : issue.storeId === "ST-021" ? 9000 : 20000;
    const points = [];
    for (let i = 0; i < 28; i++) {
      const dayOfWeek = i % 7;
      const weekendBoost = (dayOfWeek === 5 || dayOfWeek === 6) ? baseVal * 0.25 : 0;
      const wave = Math.sin(i / 3) * (baseVal * 0.08);
      const p50 = baseVal + weekendBoost + wave;
      const p90 = p50 * 1.15;
      const p10 = p50 * 0.85;

      let actual: number | undefined = undefined;
      if (i < 14) {
        actual = p50 + (Math.sin(i * 1.7) * (baseVal * 0.05));
        // Simulate revenue drop during issue peak (Day 11-13)
        if (i >= 11 && i <= 13) {
          actual = p10 * 0.82;
        }
      }
      points.push({ day: i - 14, p10, p50, p90, actual });
    }
    return points;
  }, [issue.storeId]);

  // SVG Chart layout mapping
  const chartWidth = 540;
  const chartHeight = 140;
  const paddingX = 40;
  const paddingY = 20;

  const chartParams = useMemo(() => {
    const p10Values = forecastData.map((p) => p.p10);
    const p90Values = forecastData.map((p) => p.p90);
    const actualValues = forecastData.map((p) => p.actual).filter((v): v is number => v !== undefined);

    const minY = Math.min(...p10Values, ...actualValues) * 0.9;
    const maxY = Math.max(...p90Values) * 1.1;

    const getX = (index: number) => paddingX + (index / 27) * (chartWidth - paddingX * 2);
    const getY = (value: number) => chartHeight - paddingY - ((value - minY) / (maxY - minY)) * (chartHeight - paddingY * 2);

    const bandPoints = forecastData.map((p, idx) => `${getX(idx)},${getY(p.p90)}`);
    const reverseBandPoints = [...forecastData].reverse().map((p, idx) => `${getX(27 - idx)},${getY(p.p10)}`);
    const bandPath = `M ${bandPoints.join(" L ")} L ${reverseBandPoints.join(" L ")} Z`;

    const p50Path = `M ${forecastData.map((p, idx) => `${getX(idx)},${getY(p.p50)}`).join(" L ")}`;

    const actualPoints = forecastData.filter((p) => p.actual !== undefined);
    const actualPath = `M ${actualPoints.map((p, idx) => `${getX(idx)},${getY(p.actual!)}`).join(" L ")}`;

    return { getX, getY, bandPath, p50Path, actualPath, minY, maxY };
  }, [forecastData]);

  // 6. Dynamic evidence list resolved from issue
  const issueEvidence = useMemo(() => {
    return getIssueEvidence(issue, activeEvidence);
  }, [issue, activeEvidence]);

  const supportingEvidence = useMemo(() => {
    return issueEvidence.filter((e) => e.polarity === "supporting");
  }, [issueEvidence]);

  const contraryEvidence = useMemo(() => {
    return issueEvidence.filter((e) => e.polarity === "contrary");
  }, [issueEvidence]);

  const evGoogleReview = issueEvidence.find((e) => e.kind === "googleReview");
  const evCsCase = issueEvidence.find((e) => e.kind === "csCase");
  const evCamera = issueEvidence.find((e) => e.kind === "camera");
  const evPayment = issueEvidence.find((e) => e.kind === "payment");
  const evIot = issueEvidence.find((e) => e.kind === "iot");
  const evForecastOps = issueEvidence.find((e) => e.kind === "forecastOps");

  // 7. Filtered audit timelines
  const localAuditEvents = useMemo(() => {
    return getLocalAuditEvents(issue, activeAuditEvents);
  }, [issue, activeAuditEvents]);

  // 8. Store lighting status helper
  const storeObj = useMemo(() => {
    return activeStores.find((s) => s.id === issue.storeId);
  }, [activeStores, issue.storeId]);

  // Interactive callbacks
  const toggleSource = (source: string) => {
    setSelectedSources((prev) =>
      prev.includes(source) ? prev.filter((s) => s !== source) : [...prev, source]
    );
  };

  const toggleSeverity = (severity: Severity) => {
    setSelectedSeverities((prev) =>
      prev.includes(severity) ? prev.filter((s) => s !== severity) : [...prev, severity]
    );
  };

  const toggleLightFilter = (dimension: StoreOpsLightDimension, status: StoreLightStatus) => {
    setSelectedLightFilter((current) =>
      current?.dimension === dimension && current.status === status ? null : { dimension, status }
    );
  };

  const clearFilters = () => {
    setSelectedSources([]);
    setSelectedSeverities([]);
    setSelectedLightFilter(null);
    setMineOnly(false);
    setSearchQuery("");
  };

  const getDialogTypeFromLabel = (label: string): StoreOpsWorkflowDialogType => {
    const normalized = label.toLowerCase();
    if (normalized.includes("triage")) return "triage";
    if (normalized.includes("assign") || normalized.includes("指派")) return "assign";
    if (normalized.includes("action") || normalized.includes("處置") || normalized.includes("工單")) return "action";
    if (normalized.includes("observation") || normalized.includes("field report") || normalized.includes("現場回報")) return "fieldReport";
    if (normalized.includes("outcome") || normalized.includes("成效") || normalized.includes("判斷")) return "outcome";
    if (normalized.includes("escalate") || normalized.includes("升級") || normalized.includes("approval")) return "escalate";
    if (normalized.includes("camera") || normalized.includes("影像")) return "cameraPurpose";
    if (normalized.includes("reply") || normalized.includes("回覆")) return "replyReview";
    if (normalized.includes("transfer") || normalized.includes("audit") || normalized.includes("packet") || normalized.includes("稽核")) return "transfer";
    return "triage";
  };

  const handleActionClick = (label: string) => {
    const dialogType = getDialogTypeFromLabel(label);
    if (issue) {
      onOpenWorkflow(dialogType, issue);
    }
  };

  const primaryActionLabel = getPrimaryActionLabel(issue);
  const secondaryActionLabels = getSecondaryActionLabels(issue);
  const totalIssueCount =
    fourLightSummary[0]
      ? lightStatusOrder.reduce(
          (total, status) => total + fourLightSummary[0].issueCounts[status],
          fourLightSummary[0].issueCounts.green,
        )
      : (propIssues ?? ISSUE_FIXTURES).length;

  return (
    <div className={styles.storeWorkspace} data-screen-label="Store Ops 門市營運">
      <header className={styles.storeHeader}>
        <h1>門市營運</h1>
        <p>問題 → 證據 → 指派 → 處置 → 觀察 → 成效，在同一個工作台完成</p>
      </header>
      <div className={styles.storeGrid}>
        <aside className={styles.storeQueue} aria-label="門市 Issue queue">
          <label className={styles.designSearch}>
            <input
              placeholder="搜尋標題／門市／編號"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </label>
          {fourLightSummary.length > 0 ? (
            <div className={styles.filterRows} aria-label="Store Ops four-light quick filters">
              {fourLightSummary.flatMap((item) =>
                lightStatusOrder.map((status) => {
                  const isActive = selectedLightFilter?.dimension === item.dimension && selectedLightFilter.status === status;
                  return (
                    <button
                      aria-pressed={isActive}
                      className={`${styles.filterButton} ${isActive ? styles.filterActive : ""}`}
                      key={`${item.dimension}-${status}`}
                      onClick={() => toggleLightFilter(item.dimension, status)}
                      type="button"
                    >
                      {item.label} {lightStatusLabels[status]} {item.issueCounts[status]}
                    </button>
                  );
                })
              )}
            </div>
          ) : null}
          <div className={styles.filterRows}>
            <button
              type="button"
              className={`${styles.filterButton} ${selectedSources.length === 0 && selectedSeverities.length === 0 && !selectedLightFilter && !mineOnly ? styles.filterActive : ""}`}
              onClick={clearFilters}
            >
              全部 {totalIssueCount}
            </button>
            {isStoreOpsLoading ? <span>API</span> : null}
            <button
              type="button"
              className={`${styles.filterButton} ${selectedSeverities.includes("critical") ? styles.filterActive : ""}`}
              onClick={() => toggleSeverity("critical")}
            >
              Critical
            </button>
            <button
              type="button"
              className={`${styles.filterButton} ${selectedSeverities.includes("high") ? styles.filterActive : ""}`}
              onClick={() => toggleSeverity("high")}
            >
              High
            </button>
            <button
              type="button"
              className={`${styles.filterButton} ${selectedSeverities.includes("medium") ? styles.filterActive : ""}`}
              onClick={() => toggleSeverity("medium")}
            >
              Medium
            </button>
          </div>
          <div className={styles.filterRows}>
            <button
              type="button"
              className={`${styles.filterButton} ${mineOnly ? styles.filterActive : ""}`}
              onClick={() => setMineOnly(!mineOnly)}
            >
              只看我的
            </button>
            <button
              type="button"
              className={`${styles.filterButton} ${selectedSources.includes("googleReview") ? styles.filterActive : ""}`}
              onClick={() => toggleSource("googleReview")}
            >
              評價
            </button>
            <button
              type="button"
              className={`${styles.filterButton} ${selectedSources.includes("csCase") ? styles.filterActive : ""}`}
              onClick={() => toggleSource("csCase")}
            >
              客服
            </button>
            <button
              type="button"
              className={`${styles.filterButton} ${selectedSources.includes("camera") ? styles.filterActive : ""}`}
              onClick={() => toggleSource("camera")}
            >
              影像
            </button>
            <button
              type="button"
              className={`${styles.filterButton} ${selectedSources.includes("iot") ? styles.filterActive : ""}`}
              onClick={() => toggleSource("iot")}
            >
              設備
            </button>
            <button
              type="button"
              className={`${styles.filterButton} ${selectedSources.includes("payment") ? styles.filterActive : ""}`}
              onClick={() => toggleSource("payment")}
            >
              支付
            </button>
            <button
              type="button"
              className={`${styles.filterButton} ${selectedSources.includes("forecastOps") ? styles.filterActive : ""}`}
              onClick={() => toggleSource("forecastOps")}
            >
              預測
            </button>
          </div>
          <div className={styles.storeQueueList}>
            {filteredIssues.map((row) => {
              const next = getPrimaryActionLabel(row);
              return (
                <div
                  className={styles.storeQueueItem}
                  data-active={row.id === selectedIssueId}
                  key={row.id}
                  onClick={() => setSelectedIssueId(row.id)}
                  style={{ cursor: "pointer" }}
                >
                  <span className={styles.storeTopline}>
                    <small>{row.id}</small>
                    <b data-tone={getStatusTone(row.status)}>{getStatusLabel(row.status)}</b>
                    <b data-tone={getSeverityTone(row.severity)} style={{ marginLeft: "4px" }}>
                      {getSeverityLabel(row.severity)}
                    </b>
                    <em>{formatSla(row.slaDueAt)}</em>
                  </span>
                  <strong>{row.title}</strong>
                  <span>{row.storeName}・{getSourceLabel(row.source)}</span>
                  <span className={styles.nextLine}>下一步：{next}</span>
                </div>
              );
            })}
            {filteredIssues.length === 0 && (
              <div style={{ padding: "20px", color: "#8794aa", textAlign: "center" }}>沒有匹配的 Issue</div>
            )}
          </div>
        </aside>

        <main className={styles.storeDetail} aria-label={`${issue.id} detail`}>
          <section className={styles.issueHero}>
            <div>
              <span className={styles.issueId}>{issue.id}</span>
              <b data-tone={getSeverityTone(issue.severity)}>{getSeverityLabel(issue.severity)}</b>
              <b data-tone={getStatusTone(issue.status)}>{getStatusLabel(issue.status)}</b>
            </div>
            <h2>{issue.title}</h2>
            <p>{issue.summary}</p>
            <ol className={styles.progress}>
              {["new", "triaged", "assigned", "inprogress", "executed", "observing", "outcomeready", "closed"].map((step, index) => {
                const isActive = issue.status === step || 
                  (step === "new" && issue.status !== "new") ||
                  (step === "triaged" && !["new"].includes(issue.status)) ||
                  (step === "assigned" && !["new", "triaged"].includes(issue.status)) ||
                  (step === "inprogress" && !["new", "triaged", "assigned"].includes(issue.status)) ||
                  (step === "executed" && !["new", "triaged", "assigned", "inprogress"].includes(issue.status)) ||
                  (step === "observing" && !["new", "triaged", "assigned", "inprogress", "executed"].includes(issue.status)) ||
                  (step === "outcomeready" && !["new", "triaged", "assigned", "inprogress", "executed", "observing"].includes(issue.status));
                return (
                  <li data-active={isActive} key={step}>
                    {getStatusLabel(step as any)}
                  </li>
                );
              })}
            </ol>
            <div className={styles.nextStep}>下一步：{getAiRecommendation(issue)}</div>
          </section>

          <section className={styles.storeStrip}>
            <span>
              <small>門市</small>
              <strong>{issue.storeName}</strong>
            </span>
            <span>
              <small>型態</small>
              <strong>自助洗衣</strong>
            </span>
            <span>
              <small>機台</small>
              <strong>14 台</strong>
            </span>
            <span>
              <small>今日營收</small>
              <strong>
                NT$
                {issue.storeId === "ST-008" ? "18,420" : issue.storeId === "ST-014" ? "14,200" : "9,500"}
              </strong>
            </span>
            <span>
              <small>FORECASTOPS 四燈</small>
              <strong>
                {storeObj
                  ? `需求(${storeObj.lights.demand})・設備(${storeObj.lights.operations})・清潔(${storeObj.lights.staffing})・利潤(${storeObj.lights.margin})`
                  : "需求・設備・清潔・利潤"}
              </strong>
            </span>
          </section>

          <section className={styles.evidenceFusion}>
            <div className={styles.sectionTitle}>
              <h3>證據融合</h3>
              <span>EVIDENCE FUSION</span>
              <strong>證據強度 {supportingEvidence.length >= 3 ? "強" : "中"}</strong>
            </div>
            <div className={styles.evidenceCards}>
              <article>
                <small>Google 評價</small>
                {evGoogleReview ? (
                  <>
                    <strong>★ {(evGoogleReview.confidence * 5).toFixed(1)} 未回覆</strong>
                    <p>{evGoogleReview.summary}</p>
                  </>
                ) : (
                  <>
                    <strong style={{ color: "#087a47" }}>4.8 ★ 正常</strong>
                    <p>近期無負面評價回報</p>
                  </>
                )}
              </article>
              <article>
                <small>客服案件</small>
                {evCsCase ? (
                  <>
                    <strong>Zendesk 異常</strong>
                    <p>{evCsCase.summary}</p>
                  </>
                ) : (
                  <>
                    <strong style={{ color: "#087a47" }}>0 件 正常</strong>
                    <p>客服管道運作正常</p>
                  </>
                )}
              </article>
              <article
                className={evCamera && evCamera.lockedReason ? styles.evidenceClickable : ""}
                onClick={() => evCamera && evCamera.lockedReason && onOpenWorkflow("cameraPurpose", issue)}
              >
                <small>Camera 影像</small>
                {evCamera ? (
                  evCamera.lockedReason ? (
                    <>
                      <strong style={{ color: "#d18700" }}>影像鎖定 • 需授權</strong>
                      <p style={{ textDecoration: "underline" }}>點擊填寫調閱目的</p>
                    </>
                  ) : (
                    <>
                      <strong style={{ color: "#087a47" }}>影像已解鎖</strong>
                      <p>{evCamera.summary}</p>
                    </>
                  )
                ) : (
                  <>
                    <strong style={{ color: "#087a47" }}>正常</strong>
                    <p>影像監控無异常偵測</p>
                  </>
                )}
              </article>
              <article>
                <small>支付</small>
                {evPayment ? (
                  <>
                    <strong>交易延遲警告</strong>
                    <p>{evPayment.summary}</p>
                  </>
                ) : (
                  <>
                    <strong style={{ color: "#087a47" }}>交易正常</strong>
                    <p>無異常交易或退款尖峰</p>
                  </>
                )}
              </article>
              <article>
                <small>IoT 設備</small>
                {evIot ? (
                  <>
                    <strong>Telemetry 警告</strong>
                    <p>{evIot.summary}</p>
                  </>
                ) : (
                  <>
                    <strong style={{ color: "#087a47" }}>設備在線</strong>
                    <p>硬體感測器狀態健康</p>
                  </>
                )}
              </article>
              <article>
                <small>ForecastOps</small>
                {evForecastOps ? (
                  <>
                    <strong>四燈評估警告</strong>
                    <p>{evForecastOps.summary}</p>
                  </>
                ) : (
                  <>
                    <strong style={{ color: "#087a47" }}>指標正常</strong>
                    <p>需求與營運四燈皆綠</p>
                  </>
                )}
              </article>
            </div>

            {/* 28-day Revenue Forecast and Anomaly Band Chart */}
            <div className={styles.forecastChartSection}>
              <h4>28 天門市營運營收預測與異常帶 (Forecast Band Chart)</h4>
              <svg
                width="100%"
                height={chartHeight}
                viewBox={`0 0 ${chartWidth} ${chartHeight}`}
                style={{ background: "#111a2e", borderRadius: "8px", padding: "8px" }}
              >
                <defs>
                  <linearGradient id="bandGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#3b82f6" stopOpacity="0.25" />
                    <stop offset="100%" stopColor="#3b82f6" stopOpacity="0.05" />
                  </linearGradient>
                </defs>

                {/* Grid Lines */}
                <line x1={paddingX} y1={chartParams.getY(chartParams.minY)} x2={chartWidth - paddingX} y2={chartParams.getY(chartParams.minY)} stroke="#2a3b5c" strokeWidth="1" />
                <line x1={paddingX} y1={chartParams.getY((chartParams.maxY + chartParams.minY) / 2)} x2={chartWidth - paddingX} y2={chartParams.getY((chartParams.maxY + chartParams.minY) / 2)} stroke="#2a3b5c" strokeDasharray="3,3" />
                <line x1={paddingX} y1={chartParams.getY(chartParams.maxY)} x2={chartWidth - paddingX} y2={chartParams.getY(chartParams.maxY)} stroke="#2a3b5c" strokeWidth="1" />

                {/* Prediction Band Area */}
                <path d={chartParams.bandPath} fill="url(#bandGrad)" />

                {/* P50 Median Forecast Line */}
                <path d={chartParams.p50Path} fill="none" stroke="#3b82f6" strokeWidth="2" strokeDasharray="4,4" />

                {/* Actual Revenue Line */}
                <path d={chartParams.actualPath} fill="none" stroke="#10b981" strokeWidth="2.5" />

                {/* Today vertical indicator */}
                <line x1={chartParams.getX(13)} y1={paddingY} x2={chartParams.getX(13)} y2={chartHeight - paddingY} stroke="#ef4444" strokeWidth="1.5" strokeDasharray="3,3" />
                <text x={chartParams.getX(13)} y={paddingY - 5} fill="#ef4444" fontSize="10" textAnchor="middle" fontWeight="bold">Today</text>

                {/* Pulse circle on Today Actual value if exists */}
                {forecastData[13].actual !== undefined && (
                  <>
                    <circle cx={chartParams.getX(13)} cy={chartParams.getY(forecastData[13].actual!)} r="5" fill="#ef4444" />
                    <circle cx={chartParams.getX(13)} cy={chartParams.getY(forecastData[13].actual!)} r="10" fill="none" stroke="#ef4444" strokeWidth="1.5" opacity="0.7">
                      <animate attributeName="r" values="5;12;5" dur="2s" repeatCount="indefinite" />
                    </circle>
                  </>
                )}

                {/* Y Axis text labels */}
                <text x={paddingX - 10} y={chartParams.getY(chartParams.maxY) + 4} fill="#8190a8" fontSize="9" textAnchor="end">{(chartParams.maxY / 1000).toFixed(0)}k</text>
                <text x={paddingX - 10} y={chartParams.getY((chartParams.maxY + chartParams.minY) / 2) + 4} fill="#8190a8" fontSize="9" textAnchor="end">{((chartParams.maxY + chartParams.minY) / 2000).toFixed(0)}k</text>
                <text x={paddingX - 10} y={chartParams.getY(chartParams.minY) + 4} fill="#8190a8" fontSize="9" textAnchor="end">{(chartParams.minY / 1000).toFixed(0)}k</text>

                {/* X Axis text labels */}
                <text x={chartParams.getX(0)} y={chartHeight - 4} fill="#8190a8" fontSize="9" textAnchor="middle">D-14</text>
                <text x={chartParams.getX(7)} y={chartHeight - 4} fill="#8190a8" fontSize="9" textAnchor="middle">D-7</text>
                <text x={chartParams.getX(13)} y={chartHeight - 4} fill="#ef4444" fontSize="9" textAnchor="middle" fontWeight="bold">Today</text>
                <text x={chartParams.getX(20)} y={chartHeight - 4} fill="#8190a8" fontSize="9" textAnchor="middle">D+7</text>
                <text x={chartParams.getX(27)} y={chartHeight - 4} fill="#8190a8" fontSize="9" textAnchor="middle">D+14</text>
              </svg>
              <div className={styles.chartLegend}>
                <span className={styles.legendItem}><i style={{ background: "rgba(59, 130, 246, 0.25)" }} /> P10-P90 預測帶</span>
                <span className={styles.legendItem}><i style={{ borderTop: "2px dashed #3b82f6" }} /> P50 預測中位數</span>
                <span className={styles.legendItem}><i style={{ borderTop: "2px solid #10b981" }} /> 實際營收 (實際跌破預測帶)</span>
              </div>
            </div>

            <div className={styles.evidenceLists}>
              <section>
                <h4>支持證據 {supportingEvidence.length}</h4>
                {supportingEvidence.map((e) => (
                  <p key={e.id}>{e.title}：{e.summary}</p>
                ))}
              </section>
              <section>
                <h4>反向證據 {contraryEvidence.length}</h4>
                {contraryEvidence.map((e) => (
                  <p key={e.id}>{e.title}：{e.summary}</p>
                ))}
                {contraryEvidence.length === 0 && <p style={{ color: "#8794aa", fontStyle: "italic" }}>目前無反向反駁證據</p>}
              </section>
            </div>

            <div className={styles.aiBox}>
              <b>AI 建議</b>
              {getAiRecommendation(issue)}
            </div>
          </section>
        </main>

        <aside className={styles.actionRail} aria-label="Action rail">
          <section>
            <h2>ACTION RAIL <span>下一步</span></h2>
            <dl>
              <div>
                <dt>狀態</dt>
                <dd>{getStatusLabel(issue.status)}</dd>
              </div>
              <div>
                <dt>Owner</dt>
                <dd>{issue.ownerName}</dd>
              </div>
              <div>
                <dt>SLA</dt>
                <dd>{formatSla(issue.slaDueAt)}</dd>
              </div>
              <div>
                <dt>期限</dt>
                <dd>今日 {formatCompactDateTime(issue.slaDueAt)} 前完成</dd>
              </div>
            </dl>
            <button
              className={styles.primaryAction}
              onClick={() => handleActionClick(primaryActionLabel)}
              type="button"
            >
              {primaryActionLabel}
            </button>
            {secondaryActionLabels.map((label) => (
              <button
                key={label}
                className={styles.secondaryAction}
                onClick={() => handleActionClick(label)}
                type="button"
              >
                {label}
              </button>
            ))}
          </section>

          <section>
            <h2>AUDIT TIMELINE <span>全部 →</span></h2>
            <div className={styles.auditMini}>
              {localAuditEvents.map((event) => (
                <p key={event.id}>
                  <time>{formatCompactDateTime(event.occurredAt)}</time>
                  <strong>{event.actorName}</strong> {event.message}
                </p>
              ))}
              {localAuditEvents.length === 0 && (
                <p style={{ color: "#8794aa", fontStyle: "italic" }}>目前尚無此事件的稽核日誌</p>
              )}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
