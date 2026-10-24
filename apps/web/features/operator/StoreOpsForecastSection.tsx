"use client";

import { useEffect, useId, useRef, useState } from "react";
import { DataStatusBadge, Table, type TableColumnSpec } from "@oday-plus/ui";
import { Button, StatusBadge } from "./components";
import { operatorSecurityHeaders } from "./operatorSecurityHeaders";
import {
  readCurrentForecast, recordedQualityAtRun, visibleForecastCsv, type ForecastBand, type ForecastReadGuardFactory,
  type ForecastReadResult, type RecordedForecast,
} from "./storeOpsForecastClient";
import styles from "./designAligned.module.css";

type Props = {
  scopeKey: string; issueId: string; storeId: string; roleId: string;
  createAuthenticatedReadGuard?: ForecastReadGuardFactory;
};
const money = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 2 });
const amount = (value: number | null) => value === null ? "未提供" : money.format(value);
const percent = (value: number) => Number.isFinite(value * 100) ? `${amount(value * 100)}%` : `${value}（比例）`;

/** Four actual horizon observations; no daily interpolation or invented series. */
function ForecastBandChart({ forecast }: { forecast: RecordedForecast }) {
  // Reuse the original forecast chart dimensions and inherited shell tokens.
  const width = 540, height = 140, paddingX = 40, paddingY = 20;
  const values = forecast.bands.flatMap(row => [row.p10, row.p50, row.p90]);
  values.push(forecast.actualRevenue);
  if (forecast.siteScoreBaselineP50 !== null) values.push(forecast.siteScoreBaselineP50);
  // Normalize visual coordinates first so finite extreme values do not
  // overflow max-min. This changes no displayed or exported value.
  const scale = Math.max(...values.map(Math.abs)) || 1;
  const low = Math.min(...values.map(value => value / scale));
  const high = Math.max(...values.map(value => value / scale));
  const y = (value: number) => height - paddingY - ((value / scale - low) / (high - low || 1)) * (height - paddingY * 2);
  const x = (index: number) => paddingX + index * (width - paddingX * 2) / 3;
  return <>
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} role="img"
      aria-label="四個預測週期的 P10 至 P90 區間與 P50；完整數值見下方資料表">
      <line x1={paddingX} x2={width - paddingX} y1={height - paddingY} y2={height - paddingY} stroke="var(--operator-border)" />
      <line x1={paddingX} x2={width - paddingX} y1={y(forecast.actualRevenue)} y2={y(forecast.actualRevenue)} stroke="var(--tone-success)" />
      {forecast.siteScoreBaselineP50 !== null ? <line x1={paddingX} x2={width - paddingX}
        y1={y(forecast.siteScoreBaselineP50)} y2={y(forecast.siteScoreBaselineP50)}
        stroke="var(--tone-accent)" strokeDasharray="4,4" /> : null}
      {forecast.bands.map((band, index) => <g key={band.horizonWeeks}>
        <title>{`${band.horizonWeeks} 週：P10 ${band.p10}、P50 ${band.p50}、P90 ${band.p90}`}</title>
        <line x1={x(index)} x2={x(index)} y1={y(band.p10)} y2={y(band.p90)} stroke="var(--tone-info)" strokeWidth={3} />
        <line x1={x(index) - 5} x2={x(index) + 5} y1={y(band.p10)} y2={y(band.p10)} stroke="var(--tone-info)" />
        <line x1={x(index) - 5} x2={x(index) + 5} y1={y(band.p90)} y2={y(band.p90)} stroke="var(--tone-info)" />
        <circle cx={x(index)} cy={y(band.p50)} r={4} fill="var(--tone-info)" />
        <text x={x(index)} y={height - 4} textAnchor="middle" fill="currentColor" fontSize={10}>{band.horizonWeeks} 週</text>
      </g>)}
    </svg>
    <div className={styles.chartLegend}>
      <span className={styles.legendItem}><i style={{ background: "var(--tone-info)" }} />P10–P90 區間／P50 點</span>
      <span className={styles.legendItem}><i style={{ background: "var(--tone-success)" }} />實際營收記錄值</span>
      {forecast.siteScoreBaselineP50 !== null ? <span className={styles.legendItem}><i style={{ background: "var(--tone-accent)" }} />SiteScore P50 基準</span> : null}
    </div>
  </>;
}

export function useStoreOpsForecastRead({ scopeKey, issueId, storeId, roleId, createAuthenticatedReadGuard }: Props, enabled = true) {
  const contextKey = JSON.stringify([scopeKey, issueId, storeId, roleId]);
  const context = useRef(contextKey);
  const generation = useRef(0);
  if (context.current !== contextKey) { context.current = contextKey; ++generation.current; }
  const guardFactory = useRef(createAuthenticatedReadGuard);
  const sourceGuard = useRef<(() => Promise<boolean>) | undefined>(undefined);
  guardFactory.current = createAuthenticatedReadGuard;
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<{ contextKey: string; result: ForecastReadResult | null }>({ contextKey, result: null });
  const [exportError, setExportError] = useState(false);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    if (!enabled) {
      ++generation.current; sourceGuard.current = undefined;
      setState({ contextKey, result: null }); setExportError(false); setExporting(false);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    const epoch = ++generation.current;
    const current = () => !cancelled && !controller.signal.aborted
      && epoch === generation.current && context.current === contextKey;
    const guard = scopeKey.trim() ? guardFactory.current?.(current) : undefined;
    sourceGuard.current = guard;
    setState({ contextKey, result: null }); setExportError(false); setExporting(false);
    const invalidate = () => { cancelled = true; controller.abort(); };
    window.addEventListener("pagehide", invalidate);
    window.addEventListener("beforeunload", invalidate);
    void readCurrentForecast({ issueId, storeId, headers: operatorSecurityHeaders(roleId), guard, signal: controller.signal })
      .then(result => { if (current()) setState({ contextKey, result }); });
    return () => {
      invalidate(); window.removeEventListener("pagehide", invalidate);
      window.removeEventListener("beforeunload", invalidate);
    };
  }, [contextKey, scopeKey, issueId, storeId, roleId, retry, enabled]);

  const result = enabled && state.contextKey === contextKey ? state.result : null;
  return { contextKey, context, generation, sourceGuard, result, setRetry,
    exportError, setExportError, exporting, setExporting };
}

type ForecastRead = ReturnType<typeof useStoreOpsForecastRead>;

export function StoreOpsForecastQuality({ read }: { read: ForecastRead }) {
  const forecast = read.result?.state === "ready" ? read.result.forecast : null;
  const quality = forecast ? recordedQualityAtRun(forecast) : null;
  if (!quality || quality.state !== "recorded") return <p role="status" data-testid="forecast-quality-unknown">
    執行時品質限制資料未提供；不代表未觸發或目前資料新鮮。
  </p>;
  const saved = quality.data;
  return <div className={styles.evidenceDetail} data-testid="forecast-quality-at-run" role={saved.violated ? "alert" : "status"}>
    <StatusBadge tone={saved.violated ? "warning" : "neutral"}>
      {saved.violated ? "執行時資料品質限制已觸發" : "執行時未觸發此資料時效限制"}
    </StatusBadge>
    <p>{saved.violated
      ? saved.stalenessDays === null ? "該次來源時效未提供，已套用保存的品質限制。"
        : `該次來源距預測日 ${saved.stalenessDays} 天；政策上限 ${saved.maxStalenessDays} 天。`
      : `該次來源距預測日 ${saved.stalenessDays ?? "未提供"} 天；政策上限 ${saved.maxStalenessDays} 天。`}
      此為執行時紀錄，不表示目前資料時效或整體品質合格。</p>
    {saved.violated ? <DataStatusBadge status={saved.stalenessDays === null ? "MISSING" : "STALE"}
      snapshotTime={saved.featureSnapshotTime} sources={forecast?.sourceSnapshotIds ?? []}
      qualityChecks={[`執行時政策 ${saved.policy.versionId}`, saved.onViolation]}
      knownLimitations={["僅呈現保存的時效限制，不推定目前品質或其他品質檢核"]} /> : null}
    <dl>
      <dt>執行時特徵快照</dt><dd>{saved.featureSnapshotTime}</dd>
      <dt>告警評估時間</dt><dd>{saved.evaluatedAt}</dd>
      <dt>保存政策</dt><dd>{saved.policy.id}／{saved.policy.version}／{saved.policy.versionId}</dd>
      <dt>保存判定</dt><dd>{saved.onViolation}；{saved.thresholdAlertLevel} → {saved.actualAlertLevel}；{saved.alertReasonCode}</dd>
    </dl>
  </div>;
}

export function StoreOpsForecastSection(props: Props) {
  const read = useStoreOpsForecastRead(props);
  return <StoreOpsForecastView read={read} storeId={props.storeId} />;
}

export function StoreOpsForecastView({ read, storeId, showQuality = true }: { read: ForecastRead; storeId: string; showQuality?: boolean }) {
  const title = useId();
  const { contextKey, context, generation, sourceGuard, result, setRetry,
    exportError, setExportError, exporting, setExporting } = read;
  const forecast = result?.state === "ready" ? result.forecast : null;
  const columns: TableColumnSpec<ForecastBand>[] = [
    { id: "horizon", header: "預測週期", render: row => `${row.horizonWeeks} 週` },
    { id: "p10", header: "P10", render: row => amount(row.p10), align: "end" },
    { id: "p50", header: "P50", render: row => amount(row.p50), align: "end" },
    { id: "p90", header: "P90", render: row => amount(row.p90), align: "end" },
  ];
  const exportVisible = async () => {
    if (!forecast || exporting || context.current !== contextKey) return;
    const epoch = generation.current;
    const current = () => epoch === generation.current && context.current === contextKey;
    const guard = sourceGuard.current;
    setExportError(false);
    setExporting(true);
    let url: string | null = null;
    try {
      if (!guard || !await guard() || !current()) return;
      const blob = new Blob([visibleForecastCsv(forecast, storeId)], { type: "text/csv;charset=utf-8" });
      url = URL.createObjectURL(blob);
      const link = document.createElement("a"); link.href = url;
      link.download = `forecast-${storeId.replace(/[^a-zA-Z0-9_.-]/g, "_")}-v${forecast.forecastVersion}.csv`;
      link.click();
    } catch { if (current()) setExportError(true); }
    finally {
      if (url !== null) URL.revokeObjectURL(url);
      if (current()) setExporting(false);
    }
  };

  return <div className={styles.forecastChartSection} role="tabpanel" id="store-evidence-panel"
    aria-labelledby="store-evidence-tab-forecastOps" tabIndex={0} data-testid={forecast ? "store-forecast-recorded" : "store-forecast-unavailable"}>
    <h4 id={title}>門市營收預測 · 4／8／12／24 週</h4>
    {showQuality ? <StoreOpsForecastQuality read={read} /> : null}
    {!result || result.state === "superseded" ? <p role="status">正在核對目前事件的預測來源。</p>
      : !forecast ? <>
        <p role="status">{result.state === "error" ? result.message : "此事件尚無可核對的預測資料；原事件工作流程仍可使用。"}</p>
        <Button size="sm" onClick={() => setRetry(value => value + 1)}>重新讀取預測</Button>
      </> : <>
        <p>實際營收：{amount(forecast.actualRevenue)}；SiteScore P50 基準：{amount(forecast.siteScoreBaselineP50)}（TWD）。</p>
        <p>軌跡：{forecast.trajectoryClass}；相對基準差距：{percent(forecast.siteScoreGapRatio)}；轉折機率：{percent(forecast.turningPointProbability)}。</p>
        <p role="status">資料品質：{forecast.dataQualityScore ?? "未提供"}；來源距預測日：{forecast.dataStalenessDays === null ? "未提供" : `${forecast.dataStalenessDays} 天`}。</p>
        <ForecastBandChart forecast={forecast} />
        <Table columns={columns} data={forecast.bands} density="compact" getRowKey={row => String(row.horizonWeeks)}
          summary="四個原始預測週期的 P10／P50／P90（TWD），非每日營收序列" />
        <Button size="sm" disabled={exporting} onClick={() => void exportVisible()}>匯出目前顯示的預測 CSV</Button>
        {exportError ? <p role="alert">匯出未完成，請重試。</p> : null}
        <details>
          <summary>版本與來源</summary>
          <dl>
            <dt>預測執行</dt><dd>{forecast.predictionRunId}</dd>
            <dt>輸出／版本</dt><dd>{forecast.forecastOutputId}／{forecast.forecastVersion}</dd>
            <dt>預測起點</dt><dd>{forecast.predictionOriginTime}</dd><dt>評分時間</dt><dd>{forecast.scoredAt}</dd>
            <dt>模型</dt><dd>{forecast.modelBinding.model_name}／{forecast.modelBinding.model_version}／{forecast.modelBinding.model_id}</dd>
            <dt>執行引擎／登錄版本</dt><dd>{forecast.modelBinding.model_engine}／{forecast.modelBinding.registry_version ?? "未提供"}</dd>
            <dt>執行時模型階段</dt><dd>{forecast.modelBinding.model_stage}</dd>
            <dt>模型檔 SHA256</dt><dd>{forecast.modelBinding.artifact_sha256}</dd>
            <dt>資料集／特徵 schema／標籤版本</dt><dd>{forecast.modelBinding.dataset_snapshot_id}／{forecast.modelBinding.feature_schema_version}／{forecast.modelBinding.label_version}</dd>
            <dt>特徵版本</dt><dd>{forecast.featureVersion}</dd>
            <dt>告警政策</dt><dd>{forecast.policy.id}／{forecast.policy.version}／{forecast.policy.versionId}</dd>
            <dt>來源快照</dt><dd>{forecast.sourceSnapshotIds.join("、")}</dd>
          </dl>
        </details>
      </>}
  </div>;
}
