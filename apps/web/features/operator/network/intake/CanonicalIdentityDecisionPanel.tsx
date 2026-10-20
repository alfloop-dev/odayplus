"use client";

import { useEffect, useState, type ReactNode } from "react";
import type { CandidateDispositionRequest, CanonicalIdentityPlanningContext, CanonicalIdentityReceipt, CanonicalIdentitySummary, CanonicalIntakeSummary, ListingPlan, MatchCaseSummary, MergePlan, SplitPlan } from "@oday-plus/openapi-client";
import {
  CanonicalIntakeReadError, readCanonicalIdentityContext, readCanonicalIdentityDecision, submitCanonicalIdentity,
  type CanonicalIdentityCommand, type CanonicalIntakeReadContext, type CanonicalListingProposal,
} from "./canonicalIntakeReadClient";
import styles from "./intake.module.css";
import { useCanonicalIntakeCommand } from "./useCanonicalIntakeCommand";
import { CanonicalMatchCaseCreation } from "./CanonicalMatchCaseCreation";

type Load<T> = { status: "loading" } | { status: "ready"; value: T } | { status: "error"; error: unknown };
type Kind = CanonicalListingProposal["decision_type"];
type ListingInput = NonNullable<CanonicalIdentityPlanningContext["listing_input"]>;
const titles: Record<Kind, string> = { CREATE: "確認為新物件", REVISE: "確認更新既有物件", DUPLICATE: "提案為重複物件，交獨立覆核" };
const confirmable = (input: ListingInput) => input.fields.length > 0
  && input.fields.every((field) => !field.masked && field.status === "PROPOSED")
  && Date.parse(input.manual_entry_valid_until) > Date.now();

export function CanonicalIdentityDecisionPanel({ context, selected, intake, onBusyChange, busy }: {
  context: CanonicalIntakeReadContext; selected: MatchCaseSummary; intake: CanonicalIntakeSummary;
  onBusyChange: (busy: boolean) => void; busy: boolean;
}) {
  const [selection, setSelection] = useState<string | null>(null);
  const [caseChanged, setCaseChanged] = useState(false);
  const ids = selected.identity_decision_ids ?? [];
  const canPropose = !caseChanged && ids.length === 0 && ["PROPOSED", "PENDING_REVIEW"].includes(selected.status)
    && ["NEW", "REVISION", "POSSIBLE_MATCH"].includes(selected.outcome);
  const canProposeGraph = !caseChanged && ids.length === 0 && selected.status === "PENDING_REVIEW"
    && ["POSSIBLE_MATCH", "QUARANTINED"].includes(selected.outcome);
  const canContinue = ids.length > 0 && selected.status === "EXECUTED" && selected.outcome === "POSSIBLE_MATCH"
    && intake.intake_method === "URL" && intake.processing_state === "NEEDS_REVIEW";
  return <section aria-label="正式案件決策" className={styles.sectionBox}>
    {canPropose ? <button className={styles.secondaryButton} disabled={busy} type="button" onClick={() => setSelection("planning")}>檢視確認資料</button> : null}
    {canContinue ? <><p>圖譜決策已執行，收件仍待確認。伺服器將核對執行憑證及目前權限，再提供原始補錄欄位與最新候選。</p>
      <button className={styles.secondaryButton} disabled={busy} type="button" onClick={() => setSelection("planning")}>繼續確認原收件</button></> : null}
    {canProposeGraph ? <button className={styles.secondaryButton} disabled={busy} type="button" onClick={() => setSelection("graph")}>提出合併／拆分計畫</button> : null}
    {canProposeGraph ? <button className={styles.secondaryButton} disabled={busy} type="button" onClick={() => setSelection("append")}>追加人工覆核關聯</button> : null}
    {selected.outcome === "EXACT_DUPLICATE" ? <p>明確重複由既定服務處理；可重新讀取案件查看實際決策。</p> : null}
    {ids.length ? <ul aria-label="已記錄的正式決策">{ids.map((id) => <li key={id}>
      <button className={styles.secondaryButton} disabled={busy} type="button" onClick={() => setSelection(id)}>檢視決策 {id}</button>
    </li>)}</ul> : null}
    {selection === "append" ? <CanonicalMatchCaseCreation context={context} intake={intake} matchCase={selected}
      Failure={CanonicalIntakeFailure} onBusyChange={onBusyChange} onCaseChanged={setCaseChanged} />
      : selection === "planning" ? <PlanningForm context={context} selected={selected} onBusyChange={onBusyChange} />
      : selection === "graph" ? <GraphPlanningForm context={context} selected={selected} onBusyChange={onBusyChange} />
      : selection ? <RecordedDecision key={selection} context={context} selected={selected} decisionId={selection} onBusyChange={onBusyChange} /> : null}
  </section>;
}

function GraphPlanningForm({ context, selected, onBusyChange }: {
  context: CanonicalIntakeReadContext; selected: MatchCaseSummary; onBusyChange: (busy: boolean) => void;
}) {
  const [data, setData] = useState<Load<CanonicalIdentityPlanningContext>>({ status: "loading" });
  const [kind, setKind] = useState<"MERGE" | "SPLIT" | "">("");
  const [sources, setSources] = useState<string[]>([]);
  const [target, setTarget] = useState("");
  const [splitSource, setSplitSource] = useState("");
  const [targets, setTargets] = useState<string[]>([]);
  const [assignments, setAssignments] = useState<Record<string, string>>({});
  const [dispositions, setDispositions] = useState<Record<string, { disposition: CandidateDispositionRequest["disposition"]; target: string }>>({});
  const [locked, setLocked] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void readCanonicalIdentityContext(context, selected, controller.signal, false).then(
      (value) => { if (!controller.signal.aborted) setData({ status: "ready", value }); },
      (error) => { if (!controller.signal.aborted) setData({ status: "error", error }); });
    return () => controller.abort();
  }, [context, selected]);
  if (data.status === "loading") return <p role="status">正在讀取案件的主體與來源關聯…</p>;
  if (data.status === "error") return <CanonicalIntakeFailure error={data.error} />;
  const observed = data.value;
  const active = observed.properties.filter((row) => row.status === "ACTIVE");
  const sourceIds = kind === "MERGE" ? sources : splitSource ? [splitSource] : [];
  const targetIds = kind === "MERGE" ? target ? [target] : [] : targets;
  const affected = new Set([...sourceIds, ...targetIds]);
  const edges = observed.source_identity_edges.filter((row) => sourceIds.includes(row.property_id));
  const linkedListings = new Set(observed.source_identity_edges.filter((row) => affected.has(row.property_id)).map((row) => row.listing_id));
  const listings = observed.listings.filter((row) => row.property_id !== null && affected.has(row.property_id) || linkedListings.has(row.listing_id));
  const candidates = observed.candidate_sites.filter((row) => sourceIds.includes(row.property_id));
  const completeDispositions = candidates.every((row) => dispositions[row.candidate_site_id]
    && ["KEEP_HISTORICAL", "REQUIRE_REVIEW", "REASSIGN"].includes(dispositions[row.candidate_site_id].disposition)
    && (dispositions[row.candidate_site_id].disposition !== "REASSIGN" || targetIds.includes(dispositions[row.candidate_site_id].target)));
  const base = {
    expected_property_versions: Object.fromEntries(observed.properties.filter((row) => affected.has(row.property_id)).map((row) => [row.property_id, row.version])),
    expected_listing_versions: Object.fromEntries(listings.map((row) => [row.listing_id, row.version])),
    expected_edge_versions: Object.fromEntries(edges.map((row) => [row.edge_id, row.edge_version])),
    candidate_reassignment_plan: candidates.filter((row) => dispositions[row.candidate_site_id]).map((row) => ({
      candidate_site_id: row.candidate_site_id, expected_version: row.version, disposition: dispositions[row.candidate_site_id].disposition,
      target_property_id: dispositions[row.candidate_site_id].disposition === "REASSIGN" ? dispositions[row.candidate_site_id].target : null,
    })),
  };
  let plan: MergePlan | SplitPlan | null = null;
  if (completeDispositions && sourceIds.every((id) => active.some((row) => row.property_id === id))
    && targetIds.every((id) => active.some((row) => row.property_id === id))) {
    if (kind === "MERGE" && sources.length > 0 && target && !sources.includes(target)) {
      plan = { ...base, source_property_ids: sources, target_property_id: target };
    } else if (kind === "SPLIT" && splitSource && targets.length >= 2 && !targets.includes(splitSource)
      && edges.length >= targets.length && edges.every((row) => targets.includes(assignments[row.edge_id]))
      && targets.every((id) => edges.some((row) => assignments[row.edge_id] === id))) {
      plan = { ...base, source_property_id: splitSource, partitions: targets.map((id) => ({ target_property_id: id,
        source_identity_edge_ids: edges.filter((row) => assignments[row.edge_id] === id).map((row) => row.edge_id) })) };
    }
  }
  function prepare(reason: string): CanonicalIdentityCommand {
    if (!plan) throw new Error("EXPLICIT_IDENTITY_GRAPH_PLAN_REQUIRED");
    const body = kind === "MERGE" && "source_property_ids" in plan ? { decision_type: "MERGE" as const, plan, reason, risk_acknowledged: true as const }
      : kind === "SPLIT" && "partitions" in plan ? { decision_type: "SPLIT" as const, plan, reason, risk_acknowledged: true as const } : null;
    if (!body) throw new Error("EXPLICIT_IDENTITY_GRAPH_PLAN_REQUIRED");
    return { path: `/api/v1/match-cases/${selected.match_case_id}/identity-decisions`, key: `canonical-ui:${crypto.randomUUID()}`,
      expectedVersion: observed.match_case_version, matchCaseId: selected.match_case_id, body };
  }
  const toggle = (ids: string[], id: string) => ids.includes(id) ? ids.filter((value) => value !== id) : [...ids, id];
  return <>
    <p>案件版本 {observed.match_case_version} · 收件版本 {observed.intake_version}。僅能選取此案件實際觀察到的主體與版本。</p>
    <label className={styles.fieldLabel}>身分計畫類型<select className={styles.select} value={kind} disabled={locked}
      onChange={(event) => { setKind(event.target.value as typeof kind); setSources([]); setTarget(""); setSplitSource(""); setTargets([]); setAssignments({}); setDispositions({}); }}>
      <option value="">請明確選取</option><option value="MERGE">合併主體</option><option value="SPLIT">拆分主體</option>
    </select></label>
    {kind === "MERGE" ? <>
      <label className={styles.fieldLabel}>合併目標主體<select className={styles.select} disabled={locked} value={target}
        onChange={(event) => { setTarget(event.target.value); setSources((rows) => rows.filter((id) => id !== event.target.value)); }}>
        <option value="">請選取目標</option>{active.map((row) => <option key={row.property_id} value={row.property_id}>{row.property_id} · 版本 {row.version}</option>)}
      </select></label>
      <fieldset disabled={locked}><legend>合併來源主體</legend>{active.filter((row) => row.property_id !== target).map((row) =>
        <label className={styles.checkboxRow} key={row.property_id}><input type="checkbox" checked={sources.includes(row.property_id)}
          onChange={() => setSources((ids) => toggle(ids, row.property_id))} />來源 {row.property_id} · 版本 {row.version}</label>)}</fieldset>
    </> : null}
    {kind === "SPLIT" ? <>
      <label className={styles.fieldLabel}>拆分來源主體<select className={styles.select} disabled={locked} value={splitSource}
        onChange={(event) => { setSplitSource(event.target.value); setTargets([]); setAssignments({}); setDispositions({}); }}>
        <option value="">請選取來源</option>{active.map((row) => <option key={row.property_id} value={row.property_id}>{row.property_id} · 版本 {row.version}</option>)}
      </select></label>
      <fieldset disabled={locked}><legend>拆分目標主體（至少兩個）</legend>{active.filter((row) => row.property_id !== splitSource).map((row) =>
        <label className={styles.checkboxRow} key={row.property_id}><input type="checkbox" checked={targets.includes(row.property_id)}
          onChange={() => setTargets((ids) => toggle(ids, row.property_id))} />目標 {row.property_id} · 版本 {row.version}</label>)}</fieldset>
      {edges.map((edge) => <label className={styles.fieldLabel} key={edge.edge_id}>來源關聯 {edge.edge_id} · 物件 {edge.listing_id ?? "未連結物件"} · 版本 {edge.edge_version}
        <select className={styles.select} aria-label={`關聯 ${edge.edge_id} 的拆分目標`} disabled={locked} value={assignments[edge.edge_id] ?? ""}
          onChange={(event) => setAssignments((rows) => ({ ...rows, [edge.edge_id]: event.target.value }))}>
          <option value="">請明確分配</option>{targets.map((id) => <option key={id} value={id}>{id}</option>)}
        </select></label>)}
    </> : null}
    {candidates.map((row) => <div key={row.candidate_site_id}>
      <p>候選點 {row.candidate_site_id} · {row.status} · {row.target_format_code} · 版本 {row.version}</p>
      <label className={styles.fieldLabel}>候選點 {row.candidate_site_id} 的處置<select className={styles.select} disabled={locked}
        value={dispositions[row.candidate_site_id]?.disposition ?? ""} onChange={(event) => setDispositions((items) => ({ ...items,
          [row.candidate_site_id]: { disposition: event.target.value as CandidateDispositionRequest["disposition"], target: "" } }))}>
        <option value="">請明確選取</option><option value="KEEP_HISTORICAL">保留歷史歸屬</option><option value="REQUIRE_REVIEW">標記待覆核</option><option value="REASSIGN">重新指派至所選目標</option>
      </select></label>
      {dispositions[row.candidate_site_id]?.disposition === "REASSIGN" ? <label className={styles.fieldLabel}>候選點 {row.candidate_site_id} 的目標<select className={styles.select}
        disabled={locked} value={dispositions[row.candidate_site_id].target} onChange={(event) => setDispositions((items) => ({ ...items,
          [row.candidate_site_id]: { ...items[row.candidate_site_id], target: event.target.value } }))}>
        <option value="">請明確選取目標</option>{targetIds.map((id) => <option key={id} value={id}>{id}</option>)}
      </select></label> : null}
    </div>)}
    {observed.candidate_sites.filter((row) => targetIds.includes(row.property_id)).map((row) => <p key={row.candidate_site_id}>
      目標現有候選點 {row.candidate_site_id} · {row.status} · {row.target_format_code} · 版本 {row.version}
    </p>)}
    {kind && !plan ? <p>請完成來源、目標、每條來源關聯的分配，以及所有受影響候選點的明確處置。</p> : null}
    {plan ? <ObservedPlan plan={plan} /> : null}
    <CommandForm context={context} prepare={prepare} onBusyChange={onBusyChange} onLock={() => setLocked(true)} canSubmit={plan !== null}
      selectionKey={JSON.stringify({ kind, sources, target, splitSource, targets, assignments, dispositions })} minReason={20} acknowledgementRequired
      risk="合併／拆分會改變主體、來源關聯與候選點歸屬。此步只記錄提案，須由另一位授權人員覆核後交既有執行器處理；它不套用補錄欄位，也不代表收件已完成。"
      label={kind === "SPLIT" ? "提交拆分提案" : "提交合併提案"} />
  </>;
}

function PlanningForm({ context, selected, onBusyChange }: {
  context: CanonicalIntakeReadContext; selected: MatchCaseSummary; onBusyChange: (busy: boolean) => void;
}) {
  const [data, setData] = useState<Load<CanonicalIdentityPlanningContext>>({ status: "loading" });
  const [kind, setKind] = useState<Kind | "">("");
  const [listingId, setListingId] = useState("");
  const [locked, setLocked] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void readCanonicalIdentityContext(context, selected, controller.signal).then(
      (value) => { if (!controller.signal.aborted) setData({ status: "ready", value }); },
      (error) => { if (!controller.signal.aborted) setData({ status: "error", error }); });
    return () => controller.abort();
  }, [context, selected]);
  if (data.status === "loading") return <p role="status">正在讀取確認資料…</p>;
  if (data.status === "error") return <CanonicalIntakeFailure error={data.error} />;
  const observed = data.value;
  const input = observed.listing_input!;
  const choices: Kind[] = selected.outcome === "NEW" ? ["CREATE"] : selected.outcome === "REVISION" ? ["REVISE"] : ["CREATE", "REVISE", "DUPLICATE"];
  const eligible = confirmable(input);
  const anchor = observed.anchors.find((row) => row.listing_id === listingId);
  const properties = kind === "CREATE" ? observed.properties : observed.properties.filter((row) => row.property_id === anchor?.effective_property_id);
  const listings = kind === "CREATE" ? observed.listings : observed.listings.filter((row) => row.listing_id === listingId);
  const edges = kind === "CREATE" ? observed.source_identity_edges : observed.source_identity_edges.filter((row) => row.listing_id === listingId);
  const selectedResources = kind === "CREATE" || Boolean(anchor) && properties.length === 1 && listings.length === 1;
  // The displayed and submitted plan is the same projection of the actual
  // read. Current authority and graph completeness remain the owner's checks.
  const plan: ListingPlan = {
      expected_intake_version: observed.intake_version,
      expected_property_versions: Object.fromEntries(properties.map((row) => [row.property_id, row.version])),
      expected_listing_versions: Object.fromEntries(listings.map((row) => [row.listing_id, row.version])),
      expected_edge_versions: Object.fromEntries(edges.map((row) => [row.edge_id, row.edge_version])),
      candidate_reassignment_plan: [], input_binding: {
        parser_job_id: input.parser_job_id, parser_run_id: input.parser_run_id, parser_run_version: input.parser_run_version,
        source_snapshot_id: input.source_snapshot_id, original_input_sha256: input.original_input_sha256,
        normalized_payload_sha256: input.normalized_payload_sha256,
        expected_correction_versions: Object.fromEntries(input.fields.map((row) => [row.correction_id, row.version])),
      },
  };
  function prepare(reason: string): CanonicalIdentityCommand {
    if (!kind || !eligible || !selectedResources) throw new Error("CONFIRMATION_CONTEXT_REQUIRED");
    return { path: `/api/v1/match-cases/${selected.match_case_id}/identity-decisions`, key: `canonical-ui:${crypto.randomUUID()}`,
      expectedVersion: observed.match_case_version, matchCaseId: selected.match_case_id,
      body: { decision_type: kind, reason, risk_acknowledged: true, plan } };
  }
  const risk = kind === "CREATE" ? "將依下列補錄欄位建立新物件；現有候選將保留。"
    : kind === "REVISE" ? `將依下列補錄欄位更新所選物件 ${listingId || "（尚未選取）"}。`
      : "重複提案須由另一位授權人員覆核；核准後連結至所選既有物件，不套用補錄欄位。";
  return <>
    <p>收件版本 {observed.intake_version} · 案件版本 {observed.match_case_version}</p>
    <p>補錄核准有效至 {new Date(input.manual_entry_valid_until).toLocaleString("zh-TW")}</p>
    <InputFields input={input} />
    {!eligible ? <p role="alert">欄位被遮罩、已變更或補錄核准已過期，無法確認；請重新讀取案件。</p> : null}
    <label className={styles.fieldLabel}>確認方式<select className={styles.select} disabled={locked} value={kind}
      onChange={(event) => { setKind(event.target.value as Kind | ""); setListingId(""); }}>
      <option value="">請明確選取</option>{choices.map((value) => <option key={value} value={value}>{titles[value]}</option>)}
    </select></label>
    {kind && kind !== "CREATE" ? <label className={styles.fieldLabel}>所選既有物件<select className={styles.select}
      disabled={locked} value={listingId} onChange={(event) => setListingId(event.target.value)}>
      <option value="">請明確選取候選物件</option>{observed.anchors.filter((row) => row.listing_id !== null).map((row) =>
        <option key={row.match_candidate_id} value={row.listing_id!}>{row.listing_id} · 主體 {row.effective_property_id} · 版本 {row.listing_version}</option>)}
    </select></label> : null}
    {kind && selectedResources ? <ObservedPlan plan={plan} /> : null}
    <CommandForm context={context} prepare={prepare} onLock={() => setLocked(true)} onBusyChange={onBusyChange}
      selectionKey={`${kind}:${listingId}`} canSubmit={eligible && Boolean(kind) && selectedResources}
      minReason={kind === "DUPLICATE" ? 20 : 3} acknowledgementRequired risk={risk} label={kind ? titles[kind] : "確認所選決策"} />
  </>;
}

function RecordedDecision({ context, selected, decisionId, onBusyChange }: {
  context: CanonicalIntakeReadContext; selected: MatchCaseSummary; decisionId: string; onBusyChange: (busy: boolean) => void;
}) {
  const [data, setData] = useState<Load<CanonicalIdentitySummary>>({ status: "loading" });
  const [action, setAction] = useState<"APPROVE" | "REJECT" | "">("");
  const [locked, setLocked] = useState(false);
  const [sourceRead, setSourceRead] = useState<Load<CanonicalIdentityPlanningContext> | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void readCanonicalIdentityDecision(context, selected, decisionId, controller.signal).then(
      (value) => { if (!controller.signal.aborted) setData({ status: "ready", value }); },
      (error) => { if (!controller.signal.aborted) setData({ status: "error", error }); });
    return () => controller.abort();
  }, [context, selected, decisionId]);
  useEffect(() => {
    if (action !== "APPROVE" || data.status !== "ready" || data.value.decision_type !== "DUPLICATE") return;
    const controller = new AbortController(); setSourceRead({ status: "loading" });
    void readCanonicalIdentityContext(context, selected, controller.signal).then(
      (value) => { if (!controller.signal.aborted) setSourceRead({ status: "ready", value }); },
      (error) => { if (!controller.signal.aborted) setSourceRead({ status: "error", error }); });
    return () => controller.abort();
  }, [context, selected, action, data]);
  if (data.status === "loading") return <p role="status">正在讀取已記錄的決策…</p>;
  if (data.status === "error") return <CanonicalIntakeFailure error={data.error} />;
  const decision = data.value;
  const input = sourceRead?.status === "ready" ? sourceRead.value.listing_input : null;
  const binding = "input_binding" in decision.plan ? decision.plan.input_binding : null;
  const currentInput = Boolean(input && binding && confirmable(input) && sourceRead?.status === "ready"
    && sourceRead.value.match_case_version === decision.match_case_version
    && ["parser_job_id", "parser_run_id", "parser_run_version", "source_snapshot_id", "original_input_sha256", "normalized_payload_sha256"].every(
      (key) => input[key as keyof typeof input] === binding[key as keyof typeof binding])
    && input.fields.length === Object.keys(binding.expected_correction_versions).length
    && input.fields.every((field) => binding.expected_correction_versions[field.correction_id] === field.version));
  function prepare(reason: string, acknowledged: boolean): CanonicalIdentityCommand {
    if (!action) throw new Error("EXPLICIT_REVIEW_REQUIRED");
    return { path: `/api/v1/canonical-identity-decisions/${decisionId}/actions/review`, key: `canonical-ui:${crypto.randomUUID()}`,
      expectedVersion: decision.version, decisionId, matchCaseId: decision.match_case_id,
      body: { decision: action, expected_case_version: decision.match_case_version, reason, risk_acknowledged: acknowledged } };
  }
  return <>
    <p>決策 {decision.decision_id} · {decision.decision_type} · {decision.status} · 版本 {decision.version}</p>
    <p>提案者 {decision.proposer_subject_id ?? decision.proposer_service_principal} · 覆核者 {decision.reviewer_subject_id ?? "尚未覆核"}</p>
    <p>工作 {decision.job_id ?? "尚未派送"} · {decision.job_status ?? "無工作狀態"}</p>
    <ObservedPlan plan={decision.plan} />
    {decision.status === "PENDING_REVIEW" && !decision.proposer_service_principal ? <>
      <p>須由另一位授權人員覆核。拒絕不會套用欄位或變更物件。</p>
      <label className={styles.fieldLabel}>覆核結果<select className={styles.select} disabled={locked} value={action}
        onChange={(event) => setAction(event.target.value as typeof action)}>
        <option value="">請明確選取</option><option value="APPROVE">核准並交執行器處理</option><option value="REJECT">拒絕提案</option>
      </select></label>
      {action === "APPROVE" && decision.decision_type === "DUPLICATE" ? <>
        {sourceRead?.status === "loading" ? <p role="status">正在讀取覆核所需的補錄內容…</p>
          : sourceRead?.status === "error" ? <CanonicalIntakeFailure error={sourceRead.error} />
            : input ? <InputFields input={input} /> : null}
        {sourceRead?.status === "ready" && !currentInput ? <p role="alert">補錄內容被遮罩、已變更或已過期，無法核准；仍可選擇拒絕提案。</p> : null}
      </> : null}
      <CommandForm context={context} prepare={prepare} onLock={() => setLocked(true)} onBusyChange={onBusyChange} selectionKey={action}
        canSubmit={Boolean(action) && (action !== "APPROVE" || decision.decision_type !== "DUPLICATE" || currentInput)} minReason={3} acknowledgementRequired={action === "APPROVE"}
        risk={action === "APPROVE" ? "核准後將依已記錄的計畫派送執行工作；系統會再次核對目前的權限、來源與版本。" : "拒絕將留下覆核紀錄；此提案不會執行。"}
        label={action === "REJECT" ? "確認拒絕" : "確認核准"} />
    </> : <p>請重新讀取案件以取得最新的決策與工作狀態。</p>}
  </>;
}

function InputFields({ input }: { input: ListingInput }) {
  return <table className={styles.compareGrid} aria-label="本次確認的補錄欄位"><thead><tr><th>欄位</th><th>解析值</th><th>標準值</th><th>人工值</th><th>版本／狀態</th></tr></thead>
    <tbody>{input.fields.map((field) => <tr key={field.correction_id}>
      <th scope="row">{field.field_path} · {field.classification}</th>
      {[field.parsed, field.normalized, field.corrected].map((value, index) => <td className={styles.summaryValue} key={index}>{field.masked ? "無權檢視" : value === null ? "未提供" : String(value)}</td>)}
      <td>{field.version} · {field.status}</td>
    </tr>)}</tbody></table>;
}

function ObservedPlan({ plan }: { plan: CanonicalIdentitySummary["plan"] }) {
  return <div aria-label="已記錄的影響範圍">
    {"target_property_id" in plan ? <p>合併至主體 {plan.target_property_id}；來源 {plan.source_property_ids.join("、")}</p> : null}
    {"partitions" in plan ? <ul>{plan.partitions.map((part) => <li key={part.target_property_id}>拆分至主體 {part.target_property_id} · 來源關聯 {part.source_identity_edge_ids.join("、")}</li>)}</ul> : null}
    {([ ["主體", plan.expected_property_versions], ["物件", plan.expected_listing_versions], ["來源關聯", plan.expected_edge_versions] ] as const).map(([label, entries]) =>
      <div key={label}><strong>{label}與觀察版本</strong><ul>{Object.entries(entries).map(([id, version]) => <li key={id}>{id} · 版本 {version}</li>)}</ul></div>)}
    {plan.candidate_reassignment_plan.length ? <ul aria-label="候選點處置">{plan.candidate_reassignment_plan.map((row) =>
      <li key={row.candidate_site_id}>{row.candidate_site_id} · {row.disposition} · 版本 {row.expected_version} · 目標 {row.target_property_id ?? "維持原紀錄"}</li>)}</ul> : null}
  </div>;
}

function CommandForm({ context, prepare, canSubmit, minReason, acknowledgementRequired, risk, label, selectionKey, onLock, onBusyChange }: {
  context: CanonicalIntakeReadContext; prepare: (reason: string, acknowledged: boolean) => CanonicalIdentityCommand;
  canSubmit: boolean; minReason: number; acknowledgementRequired: boolean; risk: ReactNode; label: string;
  selectionKey: string; onLock: () => void; onBusyChange: (busy: boolean) => void;
}) {
  const [reason, setReason] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const command = useCanonicalIntakeCommand<CanonicalIdentityCommand, CanonicalIdentityReceipt>(context,
    (packet, signal) => submitCanonicalIdentity(context, packet, signal), onBusyChange);
  const { busy, error, receipt, hasPending } = command;
  useEffect(() => { setAcknowledged(false); }, [selectionKey]);
  function submit() {
    if (!hasPending && (!canSubmit || reason.trim().length < minReason || reason.trim().length > 4000 || acknowledgementRequired && !acknowledged)) return;
    void command.submit(() => prepare(reason.trim(), acknowledged), onLock);
  }
  if (receipt) return <div role="status" className={styles.noteBox}>
    <p>已收到正式回執：決策 {receipt.decision_id} · {receipt.status}</p>
    <p>案件版本 {receipt.match_case_version} · 決策版本 {receipt.version}</p>
    <p>工作 {receipt.job_id ?? (receipt.status === "REJECTED" ? "提案已拒絕，不會派送工作" : "尚未派送，等待獨立覆核")} · {receipt.job_status ?? "尚無工作狀態"}</p>
    <p>稽核紀錄 {receipt.audit_event_id} · 查詢編號 {receipt.correlation_id}</p>
    <p>請重新讀取案件以查看最新結果；工作派送回執尚不代表物件已完成變更。</p>
  </div>;
  return <div className={styles.sectionBox}>
    <p>{risk}</p>
    <label className={styles.fieldLabel}>決策／覆核原因（至少 {minReason} 字）<textarea className={styles.textarea}
      disabled={busy || hasPending} maxLength={4000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
    <label className={styles.checkboxRow}><input type="checkbox" checked={acknowledged} disabled={busy || hasPending}
      onChange={(event) => setAcknowledged(event.target.checked)} />我已閱讀上述影響與觀察版本，確認此決策</label>
    {error ? <><CanonicalIntakeFailure error={error} /><p>結果尚未確認；重試會沿用同一請求。若需修改內容，請先重新讀取案件確認伺服器狀態。</p></> : null}
    <button className={styles.secondaryButton} type="button" onClick={() => void submit()}
      disabled={busy || !hasPending && (!canSubmit || reason.trim().length < minReason || acknowledgementRequired && !acknowledged)}>
      {busy ? "正在提交…" : hasPending ? "重試同一請求" : label}
    </button>
  </div>;
}

export function CanonicalIntakeFailure({ error }: { error: unknown }) {
  let message = "無法完成請求，請重新讀取案件後再試。";
  if (error instanceof CanonicalIntakeReadError) {
    if (error.status === 401) message = "登入狀態已失效，請重新登入。";
    else if (error.status === 403) message = "目前身分沒有執行此操作的權限，或不符合獨立覆核要求。";
    else if (error.status === 404) message = "無法取得所選收件、案件或決策，請重新讀取。";
    else if (error.status === 409) message = "資料版本或執行條件已更新，請重新讀取案件。";
    else if (error.code === "CANONICAL_RESPONSE_INVALID") message = "回傳資料無法辨識，請重新讀取案件確認結果。";
  }
  return <div role="alert" className={styles.errorPanel}><p>{message}</p>{error instanceof CanonicalIntakeReadError && error.correlationId ? <small>查詢編號 {error.correlationId}</small> : null}</div>;
}
