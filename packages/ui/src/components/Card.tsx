import type { ReactNode } from "react";
import { Badge } from "./Badge.tsx";
import { DataStatusBadge } from "./StatusBadges.tsx";
import type { ActionSpec, BadgeSpec, DataQualityAware } from "./contracts.ts";
import { Button } from "./Button.tsx";

export type CardProps = DataQualityAware & {
  title: ReactNode;
  status?: BadgeSpec;
  actions?: ActionSpec[];
  children: ReactNode;
  elevation?: "card" | "none";
  type?:
    | "summary"
    | "decision"
    | "evidence"
    | "kpi"
    | "risk"
    | "model"
    | "data-quality"
    | "task"
    | "store"
    | "candidate-site"
    | "valuation"
    | "scenario";
  className?: string;
  "aria-label"?: string;
};

export function Card({
  title,
  status,
  actions = [],
  children,
  elevation = "card",
  type = "summary",
  dataQuality,
  className,
  ...rest
}: CardProps) {
  return (
    <section
      className={["odp-card", className].filter(Boolean).join(" ")}
      data-elevation={elevation}
      data-card-type={type}
      aria-label={rest["aria-label"]}
    >
      <header className="odp-card__header">
        <h2 className="odp-card__title">{title}</h2>
        <div className="odp-card__meta">
          {status ? <Badge {...status} /> : null}
          {dataQuality ? (
            <DataStatusBadge
              status={dataQuality.status}
              snapshotTime={dataQuality.snapshotTime}
              sources={dataQuality.sources}
              knownLimitations={dataQuality.warnings}
            />
          ) : null}
        </div>
      </header>
      <div className="odp-card__body">{children}</div>
      {actions.length > 0 ? (
        <footer className="odp-actions">
          {actions.map((action) => (
            <Button
              key={action.id}
              onClick={action.onSelect}
              loading={action.loading}
              disabled={action.permitted === false}
              disabledReason={action.disabledReason}
              variant={action.tone === "danger" ? "danger" : "secondary"}
            >
              {action.label}
            </Button>
          ))}
        </footer>
      ) : null}
    </section>
  );
}
