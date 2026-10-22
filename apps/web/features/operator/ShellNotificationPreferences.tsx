"use client";

import { useEffect, useRef, useState } from "react";
import { Button, Form, Toast } from "@oday-plus/ui";
import { payloadContainsSeedData } from "./operatorDataMode";

type Preferences = { channels: Record<string, boolean>; severityFloor: string; digest: string };
type Read = { preferences: Preferences; isDefault: boolean; levels: string[]; canUpdate: boolean };
type Lease = { key: string; active: boolean; keys: Map<string, string> };
export type ShellNotificationPreferencesProps = {
  scopeKey: string;
  expectedSubjectId: string | null;
  headers: Record<string, string>;
  createAuthenticatedReadGuard: (current: () => boolean) => () => Promise<boolean>;
  allowFixtureData?: boolean;
};
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function parsePreferences(value: unknown): Preferences {
  if (!object(value) || !object(value.channels) || Object.keys(value.channels).length === 0
    || Object.entries(value.channels).some(([key, enabled]) => !key.trim() || typeof enabled !== "boolean")
    || typeof value.severityFloor !== "string" || !["info", "warning", "critical"].includes(value.severityFloor)
    || typeof value.digest !== "string" || !value.digest.trim()) throw new Error("通知偏好資料格式不正確。");
  return { channels: { ...value.channels } as Record<string, boolean>, severityFloor: value.severityFloor, digest: value.digest };
}
function fingerprint(value: Preferences): string {
  return JSON.stringify({ channels: Object.fromEntries(Object.entries(value.channels).sort(([a], [b]) => a.localeCompare(b))), severityFloor: value.severityFloor, digest: value.digest });
}
export function normalizeNotificationPreferences(value: unknown): Read {
  if (!object(value) || typeof value.isDefault !== "boolean" || !Array.isArray(value.severityLevels)
    || value.severityLevels.length === 0 || value.severityLevels.some((level) => !["info", "warning", "critical"].includes(level as string))
    || new Set(value.severityLevels).size !== value.severityLevels.length) throw new Error("通知偏好回應格式不正確。");
  const preferences = parsePreferences(value.preferences);
  if (!value.severityLevels.includes(preferences.severityFloor)) throw new Error("偏好嚴重度不在服務提供的範圍。");
  if (value.actions !== undefined && !object(value.actions)) throw new Error("偏好操作權限格式不正確。");
  const update = object(value.actions) ? value.actions.update : undefined;
  if (update !== undefined && (!object(update) || typeof update.allowed !== "boolean")) throw new Error("偏好操作權限格式不正確。");
  return { preferences, isDefault: value.isDefault, levels: [...value.severityLevels] as string[], canUpdate: object(update) && update.allowed === true };
}

export function ShellNotificationPreferences(props: ShellNotificationPreferencesProps) {
  const latest = useRef(props); latest.current = props;
  const leaseRef = useRef<Lease>({ key: props.scopeKey, active: true, keys: new Map() });
  if (leaseRef.current.key !== props.scopeKey) {
    leaseRef.current.active = false; leaseRef.current = { key: props.scopeKey, active: true, keys: new Map() };
  }
  const sequence = useRef(0);
  const busyRef = useRef<Lease | null>(null);
  const [stateScope, setStateScope] = useState(props.scopeKey);
  const [data, setData] = useState<Read | null>(null);
  const [draft, setDraft] = useState<Preferences | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  function begin() {
    const lease = leaseRef.current; const read = ++sequence.current;
    const current = () => lease.active && leaseRef.current === lease && sequence.current === read;
    return { lease, current, pin: latest.current.createAuthenticatedReadGuard(current),
      headers: { ...latest.current.headers }, subject: latest.current.expectedSubjectId,
      allowFixture: latest.current.allowFixtureData === true };
  }
  type Operation = ReturnType<typeof begin>;
  async function readPreferences(operation: Operation): Promise<Read | null> {
    const { pin, current, headers } = operation;
    if (!await pin() || !current()) return null;
    const response = await fetch("/api/v1/operator/shell/notifications/preferences", { headers, credentials: "include", cache: "no-store" });
    if (!await pin() || !current()) return null;
    if (!response.ok) throw new Error(`通知偏好讀取失敗（HTTP ${response.status}）。`);
    const value: unknown = await response.json();
    if (!await pin() || !current()) return null;
    if (!operation.allowFixture && payloadContainsSeedData(value)) throw new Error("通知偏好來源不可使用示範資料。");
    return normalizeNotificationPreferences(value);
  }
  async function load(preserveInput = false) {
    if (busyRef.current === leaseRef.current) return;
    const operation = begin(); setLoading(true); setError(""); setMessage("");
    try {
      const next = await readPreferences(operation);
      if (!next || !operation.current()) return;
      setData(next);
      if (!preserveInput) setDraft(next.preferences);
    } catch (failure) {
      if (await operation.pin() && operation.current()) setError(failure instanceof Error ? failure.message : "通知偏好無法讀取。");
    } finally { if (operation.current()) setLoading(false); }
  }
  useEffect(() => {
    setStateScope(props.scopeKey); setData(null); setDraft(null); setBusy(false); setError(""); setMessage("");
    busyRef.current = null; void load();
    return () => { ++sequence.current; };
  }, [props.scopeKey]);

  async function save() {
    const lease = leaseRef.current;
    if (stateScope !== props.scopeKey || busyRef.current === lease || !data?.canUpdate || !draft || loading) return;
    const desired = parsePreferences(draft); const serialized = fingerprint(desired);
    if (serialized === fingerprint(data.preferences)) return;
    const operation = begin(); const { current, pin, headers } = operation;
    busyRef.current = lease; setBusy(true); setError(""); setMessage("");
    try {
      if (!await pin() || !current()) return;
      const key = lease.keys.get(serialized) ?? `notification-preferences-${crypto.randomUUID()}`;
      lease.keys.set(serialized, key);
      const response = await fetch("/api/v1/operator/shell/notifications/preferences", {
        method: "PUT", credentials: "include", headers: { ...headers, "Content-Type": "application/json", "Idempotency-Key": key },
        body: JSON.stringify(desired),
      });
      if (!await pin() || !current()) return;
      if (!response.ok) throw new Error(`通知偏好儲存失敗（HTTP ${response.status}）。`);
      const value: unknown = await response.json();
      if (!await pin() || !current()) return;
      if (!operation.allowFixture && payloadContainsSeedData(value)) throw new Error("通知偏好儲存回應不可使用示範資料。");
      if (!object(value) || fingerprint(parsePreferences(value.preferences)) !== serialized || !object(value.auditEvent)
        || typeof value.auditEvent.auditEventId !== "string" || !value.auditEvent.auditEventId.trim()
        || value.auditEvent.category !== "shell.notification" || value.auditEvent.action !== "update_preferences"
        || value.auditEvent.targetType !== "notification_preferences"
        || (!operation.allowFixture && (!operation.subject || value.auditEvent.actorSubjectId !== operation.subject))) throw new Error("通知偏好儲存回應或稽核身分無法核對。");
      const next = await readPreferences(operation);
      if (!next || !current()) return;
      setData(next);
      if (fingerprint(next.preferences) !== serialized) throw new Error("偏好已送出，但目前後端值不同；輸入已保留，請重新核對。");
      setDraft(next.preferences); lease.keys.delete(serialized);
      setMessage("通知偏好已保存並向後端重新核對。");
    } catch (failure) {
      if (await pin() && current()) setError(failure instanceof Error ? failure.message : "通知偏好儲存失敗。");
    } finally {
      if (current()) { busyRef.current = null; setBusy(false); }
    }
  }
  const own = stateScope === props.scopeKey;
  const confirmed = own ? data : null; const input = own ? draft : null;
  const dirty = Boolean(confirmed && input && fingerprint(confirmed.preferences) !== fingerprint(input));
  return <section aria-labelledby="notification-preferences-title" data-testid="personal-notification-preferences">
    <h2 id="notification-preferences-title">個人通知偏好</h2>
    <p>設定屬於目前登入帳號；角色切換不會借用其他人的偏好。偏好設定不代表通道已實際發送。</p>
    {!own || loading ? <p role="status">正在讀取個人通知偏好…</p> : null}
    {props.allowFixtureData ? <p>POC：示範模式，不作正式個人設定證明。</p> : null}
    {!confirmed && error ? <p role="alert">{error}</p> : null}
    {!confirmed && !loading && own ? <Button onClick={() => { void load(); }}>重新讀取通知偏好</Button> : null}
    {confirmed && input ? <>
      {confirmed.isDefault ? <p>尚未保存個人偏好，以下為服務提供的預設值。</p> : <p>目前顯示已保存的個人偏好。</p>}
      {!confirmed.canUpdate ? <p>目前帳號可檢視；未取得通知偏好更新權限。</p> : null}
      <Form onSubmit={(event) => { event.preventDefault(); void save(); }}
        fieldErrors={error ? [{ field: "通知偏好", message: error }] : []}
        submitAction={{ id: "save-personal-notification-preferences", label: "儲存通知偏好", loading: busy,
          permitted: confirmed.canUpdate && dirty && !loading, disabledReason: !confirmed.canUpdate ? "未取得更新權限" : !dirty ? "沒有尚未儲存的變更" : undefined }}>
        <fieldset disabled={busy || loading || !confirmed.canUpdate}>
          <legend>通知通道</legend>
          {Object.entries(input.channels).map(([channel, enabled]) => <label key={channel}>
            <input type="checkbox" checked={enabled} onChange={(event) => setDraft((previous) => previous ? {
              ...previous, channels: { ...previous.channels, [channel]: event.target.checked },
            } : previous)} />{channel}
          </label>)}
          <label>最低嚴重度 <select value={input.severityFloor} onChange={(event) => setDraft((previous) => previous ? { ...previous, severityFloor: event.target.value } : previous)}>
            {confirmed.levels.map((level) => <option key={level} value={level}>{level}</option>)}
          </select></label>
        </fieldset>
        <p>摘要模式（目前服務值）：{input.digest}</p>
        <Button disabled={busy || loading || !dirty} onClick={() => { setDraft(confirmed.preferences); setError(""); setMessage(""); }}>還原為後端值</Button>
        <Button disabled={busy || loading} onClick={() => { void load(true); }}>重新核對後端值</Button>
      </Form>
      {dirty ? <p>有尚未儲存的變更。</p> : null}
    </> : null}
    {own && message ? <Toast tone="blue" message={message} /> : null}
  </section>;
}
