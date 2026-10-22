"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Button, Modal } from "@oday-plus/ui";
import { createListingSearchApi, sameListingQuery, type ListingQuery, type ListingSearchMatches,
  type PersonalListingSearch } from "./listingSearchClient";
import styles from "./listingSearchDialog.module.css";

export type ListingSearchDialogProps = { zoneId: string; zoneLabel: string; headers: Record<string, string>;
  guard: () => Promise<boolean>; onClose: () => void; onOpenIntake: (id: string) => void };

export function ListingSearchDialog({ zoneId, zoneLabel, headers, guard, onClose, onOpenIntake }: ListingSearchDialogProps) {
  const api = useMemo(() => createListingSearchApi(zoneId, headers, guard), [zoneId, headers, guard]);
  const context = useMemo(() => ({ api, zoneId }), [api, zoneId]);
  const latest = useRef(context); latest.current = context;
  const mounted = useRef(false);
  const sequence = useRef(0);
  const busy = useRef(false);
  const [pending, setPending] = useState(false);
  const [reading, setReading] = useState(false);
  const [items, setItems] = useState<PersonalListingSearch[]>([]);
  const [selected, setSelected] = useState<PersonalListingSearch | null>(null);
  const [matches, setMatches] = useState<ListingSearchMatches | null>(null);
  const [name, setName] = useState("");
  const [min, setMin] = useState("");
  const [max, setMax] = useState("");
  const [rent, setRent] = useState("");
  const [floor, setFloor] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [deletePreview, setDeletePreview] = useState(false);
  const [unverified, setUnverified] = useState(false);
  const retry = useRef<{ fingerprint: string; key: string } | null>(null);
  const current = (captured: typeof context) => mounted.current && latest.current === captured;

  function newIntent() {
    setSelected(null); setMatches(null); setName(""); setMin(""); setMax(""); setRent(""); setFloor(false);
    setDeletePreview(false); setNotice(null); setError(null); retry.current = null;
    setUnverified(false);
  }
  function edit(row: PersonalListingSearch) {
    setSelected(row); setName(row.name); setMin(row.query.minAreaPing?.toString() ?? "");
    setMax(row.query.maxAreaPing?.toString() ?? ""); setRent(row.query.maxRentPerMonth?.toString() ?? "");
    setFloor(row.query.groundFloorOnly); setMatches(null); setDeletePreview(false); setNotice(null); setError(null); retry.current = null;
    setUnverified(false);
  }
  function query(): ListingQuery | null {
    const number = (s: string) => s.trim() === "" ? null : Number(s);
    const low = number(min), high = number(max), cap = number(rent);
    if ([low, high, cap].some(x => x !== null && (!Number.isFinite(x) || x < 0))
      || low !== null && high !== null && low > high) return null;
    return { kind: "listing-search.v1", heatZoneId: zoneId, minAreaPing: low, maxAreaPing: high,
      maxRentPerMonth: cap, groundFloorOnly: floor };
  }
  async function reload() {
    if (busy.current) return;
    const captured = context, run = ++sequence.current;
    setReading(true); setError(null); setMatches(null);
    const result = await captured.api.list();
    if (!current(captured) || run !== sequence.current) return;
    setReading(false);
    if (result.ok) {
      setItems(result.value.items);
      if (unverified && selected) {
        const actual = result.value.items.find(row => row.saved_view_id === selected.saved_view_id);
        if (selected.deleted_at && !actual) {
          newIntent(); setNotice("已核對：個人搜尋條件已刪除。原有進件與稽核紀錄保留。");
        } else if (actual && actual.version === selected.version && actual.name === selected.name
          && sameListingQuery(actual.query, selected.query)) {
          setSelected(actual); setUnverified(false); retry.current = null;
          setNotice(`已核對個人搜尋條件 ${actual.name} v${actual.version}。`);
        }
      }
    }
    else if ("superseded" in result) { newIntent(); setItems([]); onClose(); }
    else {
      setError(result.message);
      if (result.denied) { newIntent(); setItems([]); setError(result.message); }
    }
  }
  useEffect(() => {
    mounted.current = true; busy.current = false; setPending(false); setItems([]); newIntent(); void reload();
    return () => { mounted.current = false; ++sequence.current; };
  }, [context]);

  async function open(row: PersonalListingSearch) {
    if (busy.current) return;
    const captured = context, run = ++sequence.current;
    setReading(true); setMatches(null); setError(null);
    const result = await captured.api.results(row.saved_view_id);
    if (!current(captured) || run !== sequence.current) return;
    setReading(false);
    if (result.ok) { edit(result.value.search); setMatches(result.value); }
    else if ("superseded" in result) { newIntent(); setItems([]); onClose(); }
    else {
      setError(result.message);
      if (result.denied) { newIntent(); setItems([]); setError(result.message); }
    }
  }
  async function submit(operation: "create" | "update" | "delete") {
    const captured = context, criteria = query();
    if (busy.current || reading || unverified || !current(captured) || operation !== "create" && !selected
      || operation !== "delete" && (!name.trim() || !criteria)) return;
    const input = operation === "delete" ? { expectedVersion: selected!.version }
      : { name: name.trim(), query: criteria!, ...(selected ? { expectedVersion: selected.version } : {}) };
    const id = operation === "create" ? undefined : selected!.saved_view_id;
    const fingerprint = JSON.stringify({ operation, id, input });
    if (retry.current?.fingerprint !== fingerprint) retry.current = { fingerprint, key: crypto.randomUUID() };
    busy.current = true; setPending(true); setError(null); setNotice(null); ++sequence.current; setMatches(null);
    try {
      const result = await captured.api.command(operation, input, retry.current.key, id);
      if (!current(captured)) return;
      if (!result.ok) {
        if ("superseded" in result) { newIntent(); setItems([]); onClose(); }
        else {
          setError(result.message);
          if (result.denied) { newIntent(); setItems([]); setError(result.message); }
        }
        return;
      }
      const saved = result.value.search;
      // Preserve the accepted identity even when its following read fails.
      // Re-reading a command result never creates another search.
      setSelected(saved); setDeletePreview(false); setUnverified(true);
      setNotice(`${operation === "delete" ? "刪除" : "保存"}回應已收到：${saved.saved_view_id} v${saved.version}；核對最新紀錄中。`);
      if (operation === "delete") {
        const fresh = await captured.api.list();
        if (!current(captured)) return;
        if (!fresh.ok && ("superseded" in fresh || fresh.denied)) {
          newIntent(); setItems([]);
          if ("superseded" in fresh) onClose(); else setError(fresh.message);
          return;
        }
        if (fresh.ok && !fresh.value.items.some(row => row.saved_view_id === saved.saved_view_id)) {
          setItems(fresh.value.items); newIntent();
          setNotice("已核對：個人搜尋條件已刪除。原有進件與稽核紀錄保留。");
        } else setError("刪除回應已收到；列表尚未核對，請保留命令識別重試或重新讀取。");
        return;
      }
      const fresh = await captured.api.get(saved.saved_view_id);
      if (!current(captured)) return;
      if (!fresh.ok && "superseded" in fresh) { newIntent(); setItems([]); onClose(); return; }
      if (fresh.ok && fresh.value.version === saved.version && fresh.value.name === saved.name
        && sameListingQuery(fresh.value.query, saved.query)) {
        setSelected(fresh.value); setUnverified(false); retry.current = null;
        setItems(previous => [...previous.filter(row => row.saved_view_id !== saved.saved_view_id), fresh.value]);
        setNotice(`已核對個人搜尋條件 ${saved.name} v${saved.version}。人工進件與已授權 feed 收件後會自動比對；可重新讀取結果。`);
      } else if (!fresh.ok && !("superseded" in fresh) && fresh.denied) {
        newIntent(); setItems([]); setError(fresh.message);
      } else setError("保存回應已收到；最新版本尚未核對，請保留輸入並重新讀取。");
    } finally { if (current(captured)) { busy.current = false; setPending(false); } }
  }

  const disabled = pending || reading;
  const deleted = !!selected?.deleted_at;
  return <Modal open onClose={onClose} closeDisabled={pending} className={styles.dialog} title="個人物件搜尋條件">
    <p>區域：{zoneLabel}（{zoneId}）。只供本人使用；人工進件及已授權 feed 收件後自動比對。</p>
    <p>空白代表不限；資料尚未取得或被遮罩時，不會當作符合該項條件。命中結果不會自動建立候選點。</p>
    {reading ? <p role="status">讀取個人條件與可見結果中…</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {notice ? <p role="status">{notice}</p> : null}
    <Button disabled={disabled} onClick={() => { void reload(); }}>重新讀取已保存條件</Button>
    <ul aria-label="已保存的個人搜尋條件">{items.map(row => <li key={row.saved_view_id}>
      <Button disabled={disabled} onClick={() => { void open(row); }}>{row.name} · v{row.version}</Button>
    </li>)}</ul>
    {!reading && !error && items.length === 0 ? <p>本區尚無個人搜尋條件。</p> : null}
    <fieldset disabled={disabled || deleted || unverified} className={styles.fields}>
      <legend>{selected ? `修改 ${selected.name}（v${selected.version}）` : "建立個人條件"}</legend>
      <label>條件名稱<input value={name} maxLength={120} onChange={e => setName(e.target.value)} /></label>
      <label>最小坪數<input type="number" min="0" step="any" value={min} onChange={e => setMin(e.target.value)} /></label>
      <label>最大坪數<input type="number" min="0" step="any" value={max} onChange={e => setMax(e.target.value)} /></label>
      <label>每月租金上限（元）<input type="number" min="0" step="any" value={rent} onChange={e => setRent(e.target.value)} /></label>
      <label><input type="checkbox" checked={floor} onChange={e => setFloor(e.target.checked)} />僅限一樓</label>
    </fieldset>
    <Button disabled={disabled || deleted || unverified || !name.trim() || !query()} onClick={() => { void submit(selected ? "update" : "create"); }}>
      {selected ? "保存修改" : "保存個人條件"}</Button>
    <Button disabled={disabled || unverified} onClick={newIntent}>建立另一個條件</Button>
    {selected && !deleted ? <>
      <Button disabled={disabled} onClick={() => { void open(selected); }}>重新讀取此條件與結果</Button>
      <Button disabled={disabled || unverified} onClick={() => setDeletePreview(true)}>刪除個人條件</Button>
      {deletePreview ? <div><p>確定刪除「{selected.name}」？</p>
        <Button disabled={disabled} onClick={() => { void submit("delete"); }}>確認刪除</Button>
        <Button disabled={disabled} onClick={() => setDeletePreview(false)}>取消刪除</Button></div> : null}
    </> : null}
    {matches && selected && matches.search.saved_view_id === selected.saved_view_id && matches.search.version === selected.version ?
      <section aria-label="目前可見的符合進件"><h3>目前可見的符合進件（{matches.total}）</h3>
        <p>背景比對持續處理新進件；每次閱讀會重新核對當下權限及進件版本。</p>
        <ul>{matches.items.map(row => <li key={row.intakeId}><Button disabled={disabled}
          onClick={() => onOpenIntake(row.intakeId)}>查看進件 {row.intakeId} · v{row.intakeVersion}</Button></li>)}</ul>
      </section> : null}
  </Modal>;
}
