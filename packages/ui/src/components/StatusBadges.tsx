import { Badge } from "./Badge.tsx";
import type { ReactNode } from "react";
import type { DataStatus, ModelStatus, RiskLevel, StatusTone } from "./contracts.ts";
import { dataStatusTone } from "@oday-plus/domain-types";

export type DataStatusBadgeProps = {
  status: DataStatus;
  snapshotTime: string;
  ingestedAt?: string;
  sources: readonly string[];
  qualityChecks?: readonly string[];
  knownLimitations?: readonly string[];
  className?: string;
};

export type ModelVersionBadgeProps = {
  modelId: string;
  version: string;
  stage: ModelStatus;
  releaseTime?: string;
  className?: string;
};

export type AlertChipProps = {
  tone: StatusTone;
  label: string;
  icon?: ReactNode;
  pattern?: string;
  onClick?: () => void;
  severity?: RiskLevel;
  className?: string;
};

const modelTone: Record<ModelStatus, StatusTone> = {
  EXPERIMENTAL: "purple",
  CANDIDATE: "purple",
  CHALLENGER: "purple",
  CHAMPION: "purple",
  SHADOW: "blue",
  CANARY: "blue",
  PRODUCTION: "purple",
  DEPRECATED: "gray",
  ROLLED_BACK: "red",
  BLOCKED: "red",
};

export function DataStatusBadge({
  status,
  snapshotTime,
  ingestedAt,
  sources,
  qualityChecks = [],
  knownLimitations = [],
  className,
}: DataStatusBadgeProps) {
  return (
    <details className={["odp-status-details", className].filter(Boolean).join(" ")}>
      <summary>
        <Badge label={status} tone={dataStatusTone[status]} marker="●" />
      </summary>
      <dl>
        <dt>Snapshot time</dt>
        <dd>{snapshotTime}</dd>
        {ingestedAt ? (
          <>
            <dt>Ingested at</dt>
            <dd>{ingestedAt}</dd>
          </>
        ) : null}
        <dt>Sources</dt>
        <dd>{sources.join(", ") || "未提供"}</dd>
        {qualityChecks.length > 0 ? (
          <>
            <dt>Quality checks</dt>
            <dd>{qualityChecks.join(", ")}</dd>
          </>
        ) : null}
        {knownLimitations.length > 0 ? (
          <>
            <dt>Limitations</dt>
            <dd>{knownLimitations.join(", ")}</dd>
          </>
        ) : null}
      </dl>
    </details>
  );
}

export function ModelVersionBadge({
  modelId,
  version,
  stage,
  releaseTime,
  className,
}: ModelVersionBadgeProps) {
  const title = [`${modelId} ${version}`, stage, releaseTime].filter(Boolean).join(" · ");
  return (
    <span className={["odp-model-badge", className].filter(Boolean).join(" ")} title={title}>
      <Badge label={stage} tone={modelTone[stage]} marker="◇" />
      <code>{version}</code>
    </span>
  );
}

export function AlertChip({
  tone,
  label,
  icon,
  pattern,
  onClick,
  severity,
  className,
}: AlertChipProps) {
  const content = (
    <>
      {icon ? <span aria-hidden="true">{icon}</span> : null}
      {pattern ? <span aria-hidden="true">{pattern}</span> : null}
      <span>{label}</span>
      {severity ? <span className="odp-sr-only">Severity: {severity}</span> : null}
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        className={["odp-alert-chip", className].filter(Boolean).join(" ")}
        data-tone={tone}
        onClick={onClick}
      >
        {content}
      </button>
    );
  }

  return (
    <span className={["odp-alert-chip", className].filter(Boolean).join(" ")} data-tone={tone}>
      {content}
    </span>
  );
}
