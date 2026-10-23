"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Button, Modal } from "@oday-plus/ui";
import { createAreaPlanningDraftApi, admitsCurrentAreaPlanningDraft, type AreaPlanningDraft,
  type AreaPlanningFields, type AreaPlanningResult } from "./areaPlanningDraftClient";
import styles from "./listingSearchDialog.module.css";

export type AreaPlanningDraftDialogProps = { zoneId: string; zoneLabel: string;
  candidates: { id: string; title: string }[]; headers: Record<string, string>;
  guard: () => Promise<boolean>; onClose: () => void };
type Intent = { operation: "create" | "update"; id?: string; previous?: AreaPlanningDraft;
  input: AreaPlanningFields & { expectedVersion?: number }; key: string };

export function AreaPlanningDraftDialog({ zoneId, zoneLabel, candidates, headers, guard, onClose }: AreaPlanningDraftDialogProps) {
  const api = useMemo(() => createAreaPlanningDraftApi(zoneId, headers, guard), [zoneId, headers, guard]);
  const context = useMemo(() => ({ api, zoneId }), [api, zoneId]);
  const latest = useRef(context); latest.current = context;
  const mounted = useRef(false), busy = useRef(false), sequence = useRef(0);
  const [pending, setPending] = useState(false), [reading, setReading] = useState(false);
  const [items, setItems] = useState<AreaPlanningDraft[]>([]);
  const [selected, setSelected] = useState<AreaPlanningDraft | null>(null);
  const [name, setName] = useState(""), [quarter, setQuarter] = useState(""), [budget, setBudget] = useState("");
  const [candidateIds, setCandidateIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null);
  const [unverified, setUnverified] = useState(false), [uncertain, setUncertain] = useState<Intent | null>(null);
  const current = (captured: typeof context) => mounted.current && latest.current === captured;
  function clear() {
    setSelected(null); setName(""); setQuarter(""); setBudget(""); setCandidateIds([]);
    setError(null); setNotice(null); setUnverified(false); setUncertain(null);
  }
  function edit(row: AreaPlanningDraft) {
    setSelected(row); setName(row.name); setQuarter(row.quarter ?? ""); setBudget(row.budget?.toString() ?? "");
    setCandidateIds(row.candidateIds); setError(null); setUnverified(false); setUncertain(null);
  }
  function retire() { clear(); setItems([]); onClose(); }
  function failure(result: Exclude<AreaPlanningResult<unknown>, { ok: true }>) {
    if ("superseded" in result || result.denied) { retire(); return; }
    setError(result.message);
  }
  function fields(): AreaPlanningFields | null {
    const cap = budget.trim() === "" ? null : Number(budget);
    if (!name.trim() || name.trim().length > 120 || quarter !== "" && !/^[1-9][0-9]{3}Q[1-4]$/.test(quarter)
      || cap !== null && (!Number.isFinite(cap) || cap < 0)
      || candidateIds.some(id => !candidates.some(candidate => candidate.id === id))) return null;
    return { name: name.trim(), quarter: quarter || null, budget: cap, candidateIds: [...candidateIds] };
  }
  async function reload() {
    if (busy.current || uncertain) return;
    const captured = context, run = ++sequence.current;
    setReading(true); setError(null);
    try {
      if (selected) {
        const result = await captured.api.get(selected.draftId);
        if (!current(captured) || run !== sequence.current) return;
        if (!result.ok) { failure(result); return; }
        if (!admitsCurrentAreaPlanningDraft(selected, result.value)) {
          setError("目前紀錄與已收到的版本不一致，請重新讀取。"); return;
        }
        edit(result.value); setNotice(`已讀取目前草稿 ${result.value.name} v${result.value.version}。`);
      }
      const result = await captured.api.list();
      if (!current(captured) || run !== sequence.current) return;
      if (result.ok) setItems(result.value.items); else failure(result);
    } finally { if (current(captured) && run === sequence.current) setReading(false); }
  }
  useEffect(() => {
    mounted.current = true; busy.current = false; setPending(false); setReading(false); setItems([]); clear();
    const captured = context, run = ++sequence.current;
    setReading(true);
    void captured.api.list().then(result => {
      if (!current(captured) || run !== sequence.current) return;
      setReading(false); if (result.ok) setItems(result.value.items); else failure(result);
    });
    return () => { mounted.current = false; ++sequence.current; };
  }, [context]);
  async function open(row: AreaPlanningDraft) {
    if (busy.current || uncertain || unverified) return;
    const captured = context, run = ++sequence.current; setReading(true); setError(null);
    const result = await captured.api.get(row.draftId);
    if (!current(captured) || run !== sequence.current) return;
    setReading(false);
    if (result.ok) {
      if (!admitsCurrentAreaPlanningDraft(row, result.value)) { setError("目前紀錄與列表版本不一致，請重新讀取。"); return; }
      edit(result.value); setNotice(null);
    } else failure(result);
  }
  async function submit(retry?: Intent) {
    const captured = context, value = fields();
    if (busy.current || reading || !current(captured) || !retry && (unverified || uncertain || !value || selected?.status === "MATERIALIZED")) return;
    const intent: Intent = retry ?? { operation: selected ? "update" : "create", id: selected?.draftId,
      previous: selected ?? undefined,
      input: { ...value!, ...(selected ? { expectedVersion: selected.version } : {}) }, key: crypto.randomUUID() };
    busy.current = true; setPending(true); setError(null); setNotice(null); ++sequence.current;
    // Until a receipt or definite rejection is received, keep this exact command.
    setUncertain(intent);
    try {
      const result = await captured.api.command(intent.operation, intent.input, intent.key, intent.id, intent.previous);
      if (!current(captured)) return;
      if (!result.ok) {
        if (!("superseded" in result) && result.rejected) setUncertain(null);
        failure(result); return;
      }
      const accepted = result.value.draft;
      setSelected(accepted); setUncertain(null); setUnverified(true);
      setNotice(`保存回應已收到；正在核對草稿 v${accepted.version}。`);
      const fresh = await captured.api.get(accepted.draftId);
      if (!current(captured)) return;
      if (!fresh.ok) { failure(fresh); return; }
      if (!admitsCurrentAreaPlanningDraft(accepted, fresh.value)) {
        setError("保存回應已收到；目前紀錄尚未核對，請重新讀取。"); return;
      }
      edit(fresh.value);
      setItems(previous => [...previous.filter(row => row.draftId !== fresh.value.draftId), fresh.value]);
      setNotice(`已核對區域規劃草稿 ${fresh.value.name} v${fresh.value.version}。`);
    } finally { if (current(captured)) { busy.current = false; setPending(false); } }
  }
  const disabled = pending || reading;
  const locked = disabled || unverified || !!uncertain || selected?.status === "MATERIALIZED";
  return <Modal open title="季度展店：區域規劃草稿" className={styles.dialog}
    onClose={onClose} closeDisabled={pending || !!uncertain}>
    <p>區域：{zoneLabel}（{zoneId}）。先保存草稿，再補齊季度、候選點與預算。</p>
    <p>季度和預算可暫留空白；此處保存的是規劃草稿，後續建立 NetPlan 並送審時另行確認。</p>
    {reading ? <p role="status">讀取區域規劃草稿中…</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {uncertain && !pending ? <div><p>上次保存結果尚未確認，請先重試；輸入已保留。</p>
      <Button disabled={disabled} onClick={() => { void submit(uncertain); }}>重試上次保存</Button></div> : null}
    <Button disabled={disabled || !!uncertain} onClick={() => { void reload(); }}>重新讀取草稿</Button>
    <ul aria-label="本區規劃草稿">{items.map(row => <li key={row.draftId}>
      <Button disabled={disabled || !!uncertain || unverified} onClick={() => { void open(row); }}>
        {row.name} · v{row.version}{row.status === "MATERIALIZED" ? " · 已建立 NetPlan" : ""}</Button>
    </li>)}</ul>
    {!reading && !error && items.length === 0 ? <p>本區尚無規劃草稿。</p> : null}
    <fieldset disabled={locked} className={styles.fields}><legend>{selected ? `草稿 ${selected.name}（v${selected.version}）` : "建立區域草稿"}</legend>
      <label>草稿名稱<input value={name} maxLength={120} onChange={event => setName(event.target.value)} /></label>
      <label>規劃季度<input value={quarter} placeholder="例如 2027Q1；可留空白" onChange={event => setQuarter(event.target.value)} /></label>
      <label>展店預算（元）<input type="number" min="0" step="any" value={budget} onChange={event => setBudget(event.target.value)} /></label>
      <div><p>候選點（可稍後補齊）</p>{candidates.map(candidate => <label key={candidate.id}>
        <input type="checkbox" checked={candidateIds.includes(candidate.id)} onChange={event => setCandidateIds(previous =>
          event.target.checked ? [...previous, candidate.id] : previous.filter(id => id !== candidate.id))} />{candidate.title}</label>)}
        {candidates.length === 0 ? <p>本區目前沒有可選候選點。</p> : null}
        {candidateIds.filter(id => !candidates.some(candidate => candidate.id === id)).map(id => <label key={id}>
          <input type="checkbox" checked onChange={() => setCandidateIds(previous => previous.filter(value => value !== id))} />候選點已不在目前列表：{id}（取消勾選以移除）</label>)}
      </div>
    </fieldset>
    <Button disabled={!!locked || !fields()} onClick={() => { void submit(); }}>{selected ? "保存草稿修改" : "保存區域草稿"}</Button>
    <Button disabled={disabled || unverified || !!uncertain} onClick={clear}>建立另一份草稿</Button>
    {selected?.status === "MATERIALIZED" ? <p>已建立 NetPlan：{selected.nativeScenarioId}。此草稿已固定，後續請至 NetPlan 檢視。</p> : null}
  </Modal>;
}
