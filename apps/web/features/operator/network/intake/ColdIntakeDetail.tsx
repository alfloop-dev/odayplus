"use client";

import { useEffect, useState } from "react";
import { createOdpApiClient, INTAKE_STAGES, type AssistedIntake } from "@oday-plus/openapi-client";
import { hasCanonicalScoreSession, readId, readObject } from "../networkReadValidation";
import { payloadContainsSeedData } from "../../operatorDataMode";
import { IntakeProcessingDetail } from "./IntakeProcessingDetail";
import { intakeApi } from "./intakeClient";
import { isMatchOutcome, matchConfidence } from "./intakeTypes";

export type ColdIntakeDetailProps = { intakeId: string; onClose: () => void };

/** A resource read does not depend on the shell's persona catalog. Its actual
 * session and the resource API still authorize the exact record. Until the
 * normal shell recovers, this view supplies no command callbacks or grants. */
export function ColdIntakeDetail({ intakeId, onClose }: ColdIntakeDetailProps) {
  const [reload, setReload] = useState(0);
  const [read, setRead] = useState<{ id: string; attempt: number; record: AssistedIntake | null; error: string | null } | null>(null);
  const currentRead = read?.id === intakeId && read.attempt === reload ? read : null;
  useEffect(() => {
    let cancelled = false;
    const current = () => !cancelled;
    setRead(null);
    async function subject() {
      const response = await fetch("/auth/session", { cache: "no-store", headers: { accept: "application/json" } });
      const body: unknown = await response.json();
      if (!current() || response.status !== 200 || !hasCanonicalScoreSession(body)) {
        throw new Error("登入狀態尚未確認，請重新登入後讀取此收件。");
      }
      return body.subject;
    }
    async function load() {
      try {
        if (!readId(intakeId)) throw new Error("連結未指定唯一的收件紀錄。");
        const actor = await subject();
        const pin = async () => {
          if (await subject() !== actor || !current()) throw new Error("登入身分已變更，請重新讀取此收件。");
        };
        const client = createOdpApiClient({
          baseUrl: window.location.origin,
          // No unverified UI persona is supplied as a caller constraint.
          fetchImpl: async (input, init) => {
            await pin();
            const response = await fetch(input, init);
            await pin();
            const readJSON = response.json.bind(response);
            response.json = async () => {
              await pin();
              const body: unknown = await readJSON();
              await pin();
              return body;
            };
            return response;
          },
        });
        if (!client) throw new Error("收件讀取服務尚未接通。");
        const result = await intakeApi.get(client, encodeURIComponent(intakeId));
        if (!current()) return;
        if (!result.ok) throw new Error(`${result.error.summary}（${result.error.code}）`);
        if (!hasColdIntakeRecord(result.value, intakeId)) throw new Error("收件身分或資料內容尚未核驗，請重新讀取。");
        setRead({ id: intakeId, attempt: reload, record: result.value, error: null });
      } catch (error) {
        if (current()) setRead({ id: intakeId, attempt: reload, record: null,
          error: error instanceof Error ? error.message : "收件資料暫時無法取得。" });
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [intakeId, reload]);
  const refresh = () => { setRead(null); setReload((value) => value + 1); };
  if (!currentRead?.record) return (
    <section aria-live="polite" data-testid="cold-intake-read-state">
      <p>{currentRead?.error ?? "正在依目前登入權限讀取收件詳情…"}</p>
      {currentRead?.error ? <button type="button" onClick={refresh}>重新讀取收件</button> : null}
      <button type="button" onClick={onClose}>返回收件匣</button>
    </section>
  );
  return <>
    <p role="status">工作區資料尚未恢復；目前可唯讀檢視已授權的收件詳情。</p>
    <IntakeProcessingDetail record={currentRead.record} onClose={onClose} onRefresh={refresh}
      canCorrect={false} canDecide={false} canRetry={false} canReplay={false}
      canRequestPromotion={false} canReviewPromotion={false} canReplayScore={false} />
  </>;
}

function hasColdIntakeRecord(value: unknown, id: string): value is AssistedIntake {
  if (!readObject(value) || value.id !== id || payloadContainsSeedData(value)
    || !Number.isSafeInteger(value.version) || Number(value.version) < 1
    || !INTAKE_STAGES.includes(value.stage as AssistedIntake["stage"])
    || typeof value.policy !== "string"
    || !readObject(value.parsedFields) || !Array.isArray(value.auditEvents)) return false;
  const text = (v: unknown) => v === null || v === undefined || typeof v === "string";
  if (!["policyLabel", "policyReason", "originalUrl", "canonicalUrl", "parserVersion", "submitter", "owner", "heatZoneId", "sourceId", "snapshotId", "capturedAt", "correlationId",
    "submittedAt", "submitted_at", "assignmentId", "assignmentStatus", "slaInstanceId", "slaState", "dueAt", "slaDueAt",
    "assignedOwner", "assignedQueue", "target_owner_role"].every((key) => text(value[key]))) return false;
  // The existing detail also renders legacy assignment metadata and history.
  // Validate the supplied values before handing this successful read to React.
  if (!["assignmentHistory", "slaHistory"].every((key) => value[key] == null
    || Array.isArray(value[key]) && value[key].every((entry) => readObject(entry)
      && ["timestamp", "occurredAt", "action", "type", "note", "reason", "description"].every((field) => text(entry[field]))))) return false;
  const scalar = (v: unknown) => v === null || v === undefined || ["string", "boolean"].includes(typeof v)
    || typeof v === "number" && Number.isFinite(v);
  if (!Object.values(value.parsedFields).every((cell) => readObject(cell)
    && ["key", "label"].every((key) => typeof cell[key] === "string")
    && ["sourceValue", "normalizedValue", "correctedValue"].every((key) => scalar(cell[key]))
    && text(cell.correctionReason) && text(cell.classification))) return false;
  if (!value.auditEvents.every((event) => readObject(event)
    && ["id", "action", "actorName", "actorRoleId", "occurredAt", "targetId", "message", "correlationId"].every((key) => text(event[key])))) return false;
  if (value.failure != null) {
    const failure = value.failure;
    if (!readObject(failure) || typeof failure.code !== "string"
      || typeof (failure.message ?? failure.summary) !== "string"
      || typeof (failure.next_action ?? failure.nextAction) !== "string"
      || !["correlation_id", "correlationId", "occurred_at", "occurredAt"].every((key) => text(failure[key]))
      || failure.current_version != null && (!Number.isSafeInteger(failure.current_version) || Number(failure.current_version) < 0)
      || typeof failure.retryable !== "boolean") return false;
  }
  if (value.matchResult != null) {
    const match = value.matchResult;
    if (!readObject(match) || match.outcome != null && !isMatchOutcome(match.outcome)
      || !["outcomeLabel", "summary", "targetListingId"].every((key) => text(match[key]))
      || match.confidence != null && matchConfidence(match.confidence) === null
      || !["agreeingSignals", "contradictingSignals"].every((key) => match[key] == null
        || Array.isArray(match[key]) && match[key].every((signal) => readObject(signal)
          && ["key", "label", "detail"].every((field) => typeof signal[field] === "string")))) return false;
  }
  return true;
}
