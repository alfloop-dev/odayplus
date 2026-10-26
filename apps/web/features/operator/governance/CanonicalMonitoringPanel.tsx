"use client";

import { useEffect, useRef, useState } from "react";
import styles from "../governance.module.css";
import { readCanonicalMonitoring,
  type CanonicalMonitoringGuardFactory, type CanonicalMonitoringRead } from "./canonicalMonitoringClient";

export function CanonicalMonitoringPanel({ releaseId, roleId, scopeKey, createAuthenticatedReadGuard }: {
  releaseId: string; roleId: string; scopeKey?: string;
  createAuthenticatedReadGuard?: CanonicalMonitoringGuardFactory;
}) {
  const owner = useRef({ releaseId, roleId, scopeKey, mounted: true, sequence: 0 });
  if (owner.current.releaseId !== releaseId || owner.current.roleId !== roleId || owner.current.scopeKey !== scopeKey) {
    owner.current = { releaseId, roleId, scopeKey, mounted: true, sequence: owner.current.sequence + 1 };
  }
  const context = owner.current;
  const factory = useRef(createAuthenticatedReadGuard);
  factory.current = createAuthenticatedReadGuard;
  const [result, setResult] = useState<{ context: object; value: CanonicalMonitoringRead } | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    context.mounted = true;
    return () => { context.mounted = false; ++context.sequence; };
  }, [context]);
  useEffect(() => {
    const sequence = ++context.sequence;
    const abort = new AbortController();
    const current = () => context === owner.current && context.mounted && sequence === context.sequence && !abort.signal.aborted;
    const guard = factory.current?.(current);
    setResult(null);
    void readCanonicalMonitoring(releaseId, roleId, guard, abort.signal).then(value => {
      if (current()) {
        setResult({ context, value: value.state === "superseded"
          ? { state: "unavailable", reason: "目前登入或讀取範圍已變更，請重新核對。" } : value });
      }
    });
    return () => abort.abort();
  }, [context, releaseId, roleId, retry]);

  const value = result?.context === context ? result.value : null;

  return <div data-testid="canonical-release-monitoring">
    <p className={styles.summary}>預測分布漂移依已保存的發布評估呈現。成熟期預測誤差尚未提供；本次結果不代表完整監控期間已完成。回滾仍由原發布流程核准。</p>
    {!value ? <p role="status">正在讀取發布監測狀態…</p> : value.state === "error" ? <p role="alert">{value.message}</p>
      : value.state === "unavailable" ? <p role="status">{value.reason}</p> : value.state === "ready" ? <>
        {value.unavailableCount > 0 ? <p role="status">部分保存評估的目前來源或權限無法重新驗證。</p> : null}
        {value.items.map(item => <section key={item.evaluationId}>
          <dl className={styles.detailMeta}>
            <div><dt>發布模型</dt><dd>{item.modelName}:{item.modelVersion}</dd></div>
            <div><dt>預測期間</dt><dd>{item.horizonDays / 7} 週</dd></div>
            <div><dt>監控期間</dt><dd>{item.executedAt} → {item.windowEnd}（{item.monitoringWindow}）</dd></div>
            <div><dt>實際參考資料涵蓋</dt><dd>{item.referenceCoverage.join(" → ")}</dd></div>
            <div><dt>實際比較資料涵蓋</dt><dd>{item.currentCoverage.join(" → ")}</dd></div>
            <div><dt>預測分布漂移</dt><dd>{item.driftShare} / {item.driftDetected ? "偵測到分布漂移；建議人工檢視回滾" : "本次未偵測到分布漂移；持續觀測"}</dd></div>
            <div><dt>評估時間</dt><dd>{item.assessedAt}</dd></div>
          </dl>
          <details>
            <summary>稽核來源</summary>
            <dl className={styles.detailMeta}>
              <div><dt>發布 / 門市</dt><dd>{item.releaseId}（revision {item.releaseRevision}）/ {item.storeId}</dd></div>
              <div><dt>決策政策</dt><dd>{item.policyVersionId}</dd></div>
              <div><dt>參考預測來源</dt><dd>{item.referenceIds.join("、")}</dd></div>
              <div><dt>比較預測來源</dt><dd>{item.currentIds.join("、")}</dd></div>
              <div><dt>來源摘要 / 稽核紀錄</dt><dd>{item.sourceDigest} / {item.auditEventId}</dd></div>
            </dl>
          </details>
        </section>)}
      </> : null}
    <button type="button" className={styles.secondaryButton} onClick={() => setRetry(value => value + 1)}>重新讀取監測狀態</button>
  </div>;
}
